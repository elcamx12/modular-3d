"""우리 씬 → 운송 입력 변환 단일 진입점 (분석 ④).

진입점: `build_transport_input(scene, model, design_result, policy, options)
→ TransportInput`.

[책임]
1. 부재 분류 분기: Module / Vertical3Module / FloorPanel(+merged) / StructWall /
   Cantilever / Core / Mid*.
2. 단면 룩업: AnalysisModel.comp_to_members[cid] → DesignResult.member_to_group
   → groups[gname].section (SHSSection) → transport.Section 변환.
3. extra_weight 계산: 슬래브 자중(150·2400) + 비내력벽 가중평균(분석 ⑥) +
   MidBeam/MidColumn 강재 자중 + (옵션) 캔틸 합체분.
4. 부재 라벨: comp_meta (analysis_panel 과 같은 알고리즘) — "a모듈-#3" 형식.
5. source_index: 운송 item.name → 우리 cid 역인덱스.

[가드 단락 준수]
- 슬래브 두께 150 통일 (geometry.SLAB_THICKNESS_MM).
- 비내력벽 양면 합산 X — 면별 한 면 면적 합산만 (운송프로그램 모듈 분기 정책).
- 부재 라벨은 구조해석탭 comp_meta 그대로.
- B-11 모듈 weight 식은 Phase 1 에서 정정됨 → 어댑터는 정답 프레임 무게를
  운송 측 자동 합산에 위임 (extra_weight 에 프레임 가산 안 함).

[A-2 캔틸 합체]
options.cantilever_packing_mode='embedded' (기본): 부모 모듈의 extra_weight 에
캔틸 자중 가산. 운송 packer 에는 별도 Panel 안 보냄.
options.cantilever_packing_mode='separate': 별도 Panel(kind='floor') 로 보냄.

[A-1 다면 종속]
FloorPanel.merged_wall_ids 의 개수에 따라 wall_segments 1/2/3/4 개 채워서
Panel(kind='floor') 로 보낸다. 운송 측 packer 가 _pack_dependent_panels 로 처리.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from ..model.core import (
    Component, ComponentType, Scene,
    Module as SceneModule, FloorPanel, StructWall, Vertical3Module,
    Core, CoreSlab, CantileverBeam, CantileverSlab, MidBeam, MidColumn,
    TYPE_NAMES,
)
from ..카탈로그.geometry import (
    SLAB_THICKNESS_MM, FLOOR_HEIGHT,
)
from ..카탈로그.materials import CONCRETE_DENSITY_KG_M3
from ..카탈로그.steel_sections import SHSSection
from .models import (
    Section as TSection, Module as TModule, Panel as TPanel,
    WallSegment, SectionType,
)
from .wall_classifier import (
    ClassifierOptions, FaceClass, FACE_NAMES,
    classify_module, classify_vertical3_module, classify_independent_wall,
    face_unit_weight,
)


# ── 옵션 ──────────────────────────────────────────────────
@dataclass
class TransportOptions:
    """운송탭 UI 옵션. 분석 ⑥ 결정 반영."""
    # 캔틸 처리
    include_cantilever: bool = True
    cantilever_packing_mode: str = "embedded"   # 'embedded' | 'separate'

    # 비내력벽 자동판별
    wall_classifier_enabled: bool = True
    interior_wall_unit_weight: float = 30.0     # kg/m²
    exterior_wall_unit_weight: float = 55.0     # kg/m²
    wall_segment_size_mm: float = 100.0
    include_floor_panels_as_obstacle: bool = True

    # 슬래브
    floor_slab_thickness_mm: float = SLAB_THICKNESS_MM
    concrete_density_kg_m3: float = CONCRETE_DENSITY_KG_M3
    slab_rebar_ratio: float = 0.012             # 자중 합산용 (현재 미사용)

    # 수직 3 모듈
    treat_v3_module_as_lying: bool = True

    # Mid*
    mid_member_inclusion: str = "extra_weight"  # 'extra_weight' | 'ignore'


# ── 출력 ──────────────────────────────────────────────────
@dataclass
class ExcludedItem:
    cid: int
    type_name: str
    reason: str


@dataclass
class Diagnostics:
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)


@dataclass
class TransportInput:
    modules: List[TModule] = field(default_factory=list)
    panels: List[TPanel] = field(default_factory=list)
    excluded: List[ExcludedItem] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    # 운송 item.name → 우리 cid 목록 (다중 인스턴스 가능).
    source_index: Dict[str, List[int]] = field(default_factory=dict)


class TransportError(Exception):
    """어댑터 변환 실패 (선행 조건 미충족 등)."""


# ── 단면 변환 ──────────────────────────────────────────────
def to_transport_section(shs: SHSSection) -> TSection:
    """우리 SHSSection → 운송 Section."""
    return TSection(
        name=shs.name,
        section_type="SHS",
        width=float(shs.w),
        height=float(shs.h),
        thickness=float(shs.t),
        weight_per_m=float(shs.weight_per_m_kg),
        flange_thickness=0.0,
    )


# ── 단면 룩업 ──────────────────────────────────────────────
def lookup_section_for_member(
    mid: int, design_result, fallback: Optional[SHSSection] = None,
) -> Optional[SHSSection]:
    """부재 mid → 채택 SHSSection (그룹 경유)."""
    gname = design_result.member_to_group.get(mid)
    if gname is None:
        return fallback
    gd = design_result.groups.get(gname)
    if gd is None or gd.section is None:
        return fallback
    return gd.section


def lookup_sections_for_component(
    cid: int, model, design_result,
    member_filter=None,
) -> List[Tuple[int, SHSSection]]:
    """컴포넌트의 모든 부재 mid → (mid, SHSSection) 리스트.

    member_filter: 함수 (mid → bool). None 이면 모두 사용.
    """
    out: List[Tuple[int, SHSSection]] = []
    for mid in model.comp_to_members.get(cid, []):
        if member_filter is not None and not member_filter(mid):
            continue
        s = lookup_section_for_member(mid, design_result)
        if s is not None:
            out.append((mid, s))
    return out


def _pick_heaviest(sections: List[SHSSection]) -> Optional[SHSSection]:
    """단면 일관성 깨질 때 가장 무거운 단면 채택 (보수적)."""
    if not sections:
        return None
    return max(sections, key=lambda s: s.weight_per_m_kg)


# ── 라벨 (comp_meta 재구성) ────────────────────────────────
def _alpha_label(i: int) -> str:
    """0=a, 1=b, ... 25=z, 26=aa, ..."""
    s = ""
    n = i + 1
    while n > 0:
        n -= 1
        s = chr(ord("a") + n % 26) + s
        n //= 26
    return s


def build_comp_meta(scene: Scene, model) -> Dict[int, Tuple[str, int, str]]:
    """analysis_panel._fill_member_tree 와 동일 알고리즘으로 라벨 재구성.

    Returns: {cid: (xy_label, floor_num, type_name)}
    """
    by_type: Dict[str, list] = defaultdict(list)
    for cid, comp in scene.components.items():
        if cid not in getattr(model, "comp_to_members", {}):
            continue
        if not model.comp_to_members[cid]:
            continue
        tname = TYPE_NAMES.get(comp.comp_type, str(comp.comp_type))
        by_type[tname].append((cid, comp))

    comp_meta: Dict[int, Tuple[str, int, str]] = {}
    for tname, items in by_type.items():
        xy_set = sorted({(int(round(float(c.position[0]))),
                          int(round(float(c.position[1]))))
                         for _, c in items})
        xy_to_label = {xy: _alpha_label(i) for i, xy in enumerate(xy_set)}
        z_set = sorted({int(round(float(c.position[2]))) for _, c in items})
        z_to_floor = {z: i + 1 for i, z in enumerate(z_set)}
        for cid, c in items:
            xy = (int(round(float(c.position[0]))),
                  int(round(float(c.position[1]))))
            z = int(round(float(c.position[2])))
            comp_meta[cid] = (xy_to_label[xy], z_to_floor[z], tname)
    return comp_meta


def _format_label(meta: Tuple[str, int, str], cid: int) -> str:
    """'a모듈-#3' 식 라벨. xy_label + tname + floor 정보."""
    xy_label, floor_num, tname = meta
    return f"{xy_label}{tname}-{floor_num}F#{cid}"


# ── 부재 그룹화 헬퍼 (모듈 4 기둥 / 8 보 등) ───────────────
def _classify_module_member(mid: int, model) -> str:
    """부재 mid 가 기둥/보/캔틸 등 어느 카테고리인지 — group_categories 와 동일.

    단순화: AnalysisModel.members[mid].member_type (string) 또는 카테고리
    추정. 본 어댑터는 design_result.member_to_group 의 그룹명을 사용해 우회.
    """
    m = model.members.get(mid)
    if m is None:
        return "?"
    return getattr(m, "kind", getattr(m, "member_type", "?"))


# ── 비내력벽 자중 합산 (4 면) ──────────────────────────────
def _sum_4_face_nonbearing_weight(
    comp: SceneModule, face_classes: Dict[str, FaceClass], options: TransportOptions,
) -> float:
    """4 면 비내력벽 자중 합산 — 면별 한 면 면적 합산 (A-3, 양면 X).

    면적: 전면/후면 = width × height, 좌면/우면 = depth × height.
    단위중량: face_unit_weight 가중평균 (분석 ⑥).
    """
    w_m = comp.dimensions["width"] / 1000.0
    d_m = comp.dimensions["depth"] / 1000.0
    h_m = comp.dimensions["height"] / 1000.0
    face_area = {
        "전면": w_m * h_m, "후면": w_m * h_m,
        "우면": d_m * h_m, "좌면": d_m * h_m,
    }
    total = 0.0
    for face, area in face_area.items():
        fc = face_classes.get(face)
        if fc is None:
            unit = options.interior_wall_unit_weight  # 분류 결과 없으면 내부 가정
        else:
            unit = face_unit_weight(
                fc, options.interior_wall_unit_weight,
                options.exterior_wall_unit_weight,
            )
        total += unit * area
    return total


def _sum_v3_face_nonbearing_weight(
    v3: Vertical3Module, face_classes: Dict[Tuple[int, str], FaceClass],
    options: TransportOptions,
) -> float:
    """수직 3 모듈 12 면 합산 (사용자 결정 ⑥-4)."""
    w_m = v3.dimensions["width"] / 1000.0
    d_m = v3.dimensions["depth"] / 1000.0
    # 한 층 높이 = 3400 (모듈 본체) → m
    floor_h_m = 3400.0 / 1000.0
    face_area = {
        "전면": w_m * floor_h_m, "후면": w_m * floor_h_m,
        "우면": d_m * floor_h_m, "좌면": d_m * floor_h_m,
    }
    total = 0.0
    for f_idx in range(3):
        for face, area in face_area.items():
            fc = face_classes.get((f_idx, face))
            if fc is None:
                unit = options.interior_wall_unit_weight
            else:
                unit = face_unit_weight(
                    fc, options.interior_wall_unit_weight,
                    options.exterior_wall_unit_weight,
                )
            total += unit * area
    return total


# ── 슬래브·MidBeam·캔틸 자중 헬퍼 ──────────────────────────
def _slab_self_weight_kg(area_m2: float, options: TransportOptions) -> float:
    """슬래브 자중 (kg) = 면적(m²) × 두께(m) × 콘크리트 밀도(kg/m³)."""
    thickness_m = options.floor_slab_thickness_mm / 1000.0
    return area_m2 * thickness_m * options.concrete_density_kg_m3


def _sum_mid_member_weight_in_module(
    module_cid: int, scene: Scene, model, design_result,
) -> float:
    """모듈 내부 MidBeam/MidColumn 강재 자중 (parent_id 기준).

    Mid* 부재는 별도 컴포넌트로 씬에 존재하지만 운송 회차는 부모 모듈에
    흡수된다. parent_id 가 모듈 cid 면 부모 자중에 가산.
    """
    total = 0.0
    for cid, comp in scene.components.items():
        if not isinstance(comp, (MidBeam, MidColumn)):
            continue
        if getattr(comp, "parent_id", 0) != module_cid:
            continue
        # mid* 의 모든 mid 의 단면 × 부재 길이 합산
        for mid in model.comp_to_members.get(cid, []):
            sec = lookup_section_for_member(mid, design_result)
            if sec is None:
                continue
            m = model.members.get(mid)
            if m is None:
                continue
            n1 = model.nodes.get(m.n1)
            n2 = model.nodes.get(m.n2)
            if n1 is None or n2 is None:
                continue
            L_mm = float(np.linalg.norm(n2.coord - n1.coord))
            total += (L_mm / 1000.0) * sec.weight_per_m_kg
    return total


def _sum_cantilever_weight_attached_to(
    parent_cid: int, scene: Scene, model, design_result, options: TransportOptions,
) -> float:
    """부모 cid 에 매달린 캔틸 부재(보 + 슬래브) 자중."""
    if options.cantilever_packing_mode != "embedded":
        return 0.0
    total = 0.0
    for cid, comp in scene.components.items():
        if not isinstance(comp, (CantileverBeam, CantileverSlab)):
            continue
        if getattr(comp, "parent_id", 0) != parent_cid:
            continue
        # 강재 보 합산
        for mid in model.comp_to_members.get(cid, []):
            sec = lookup_section_for_member(mid, design_result)
            if sec is None:
                continue
            m = model.members.get(mid)
            if m is None:
                continue
            n1 = model.nodes.get(m.n1)
            n2 = model.nodes.get(m.n2)
            if n1 is None or n2 is None:
                continue
            L_mm = float(np.linalg.norm(n2.coord - n1.coord))
            total += (L_mm / 1000.0) * sec.weight_per_m_kg
        # 슬래브 자중 (CantileverSlab)
        if isinstance(comp, CantileverSlab):
            w_m = comp.dimensions["width"] / 1000.0
            d_m = comp.dimensions["depth"] / 1000.0
            total += _slab_self_weight_kg(w_m * d_m, options)
    return total


# ── 변환 — Module ─────────────────────────────────────────
def convert_module(
    comp: SceneModule, cid: int, scene: Scene, model, design_result,
    options: TransportOptions, diag: Diagnostics, label: str,
    face_classes: Optional[Dict[str, FaceClass]] = None,
) -> Optional[TModule]:
    """씬 Module → 운송 Module."""
    w = float(comp.dimensions["width"])
    d = float(comp.dimensions["depth"])
    h = float(comp.dimensions["height"])

    # 4 기둥 / 8 보 mid 의 단면 룩업 — analysis_model.members[mid].kind 가 'column'/'beam'
    col_secs: List[SHSSection] = []
    beam_secs: List[SHSSection] = []
    for mid in model.comp_to_members.get(cid, []):
        m = model.members.get(mid)
        if m is None:
            continue
        kind = getattr(m, "kind", None) or getattr(m, "member_type", None)
        sec = lookup_section_for_member(mid, design_result)
        if sec is None:
            continue
        if kind == "column":
            col_secs.append(sec)
        elif kind == "beam":
            beam_secs.append(sec)
    # 일관성 검사
    col_set = {s.name for s in col_secs}
    beam_set = {s.name for s in beam_secs}
    if len(col_set) > 1:
        diag.warnings.append(f"{label}: 모듈 기둥 단면 {len(col_set)}종 혼재 — 가장 무거운 단면 채택")
    if len(beam_set) > 1:
        diag.warnings.append(f"{label}: 모듈 보 단면 {len(beam_set)}종 혼재 — 가장 무거운 단면 채택")
    col_pick = _pick_heaviest(col_secs)
    beam_pick = _pick_heaviest(beam_secs)
    if col_pick is None or beam_pick is None:
        diag.warnings.append(f"{label}: 기둥/보 단면 룩업 실패 — 운송 대상 제외")
        return None

    # extra_weight
    area_m2 = (w / 1000.0) * (d / 1000.0)
    extra = _slab_self_weight_kg(area_m2, options)
    if face_classes:
        extra += _sum_4_face_nonbearing_weight(comp, face_classes, options)
    extra += _sum_mid_member_weight_in_module(cid, scene, model, design_result)
    extra += _sum_cantilever_weight_attached_to(cid, scene, model, design_result, options)

    return TModule(
        name=label,
        width=w, length=d, height=h,
        column_section=to_transport_section(col_pick),
        beam_section=to_transport_section(beam_pick),
        extra_weight_kg=extra,
    )


# ── 변환 — Vertical3Module (눕히기) ───────────────────────
def convert_vertical3_lying(
    comp: Vertical3Module, cid: int, scene: Scene, model, design_result,
    options: TransportOptions, diag: Diagnostics, label: str,
) -> Optional[TModule]:
    """수직 3 모듈을 옆으로 눕혀 운송 (사용자 정정).

    실제 치수 3,400 × 3,400 × 12,000(이내) → 회전 매핑:
    - 운송 length = 원래 height (≤12,000) → 광로 19,000 ✓
    - 운송 width  = 원래 width  (3,400) → 광로 3,500 ✓
    - 운송 height = 원래 depth  (3,400) → 광로 4,500 ✓
    """
    if not options.treat_v3_module_as_lying:
        diag.info.append(f"{label}: treat_v3_module_as_lying=False 라 세워서 운송 시도 (도로 한도 초과 가능)")

    w = float(comp.dimensions["width"])
    d = float(comp.dimensions["depth"])
    h = float(comp.dimensions["height"])

    col_secs: List[SHSSection] = []
    beam_secs: List[SHSSection] = []
    for mid in model.comp_to_members.get(cid, []):
        m = model.members.get(mid)
        if m is None:
            continue
        kind = getattr(m, "kind", None) or getattr(m, "member_type", None)
        sec = lookup_section_for_member(mid, design_result)
        if sec is None:
            continue
        if kind == "column":
            col_secs.append(sec)
        elif kind == "beam":
            beam_secs.append(sec)
    col_pick = _pick_heaviest(col_secs)
    beam_pick = _pick_heaviest(beam_secs)
    if col_pick is None or beam_pick is None:
        diag.warnings.append(f"{label}: 수직3모듈 단면 룩업 실패 — 운송 대상 제외")
        return None

    # 12 면 비내력벽
    face_classes = classify_vertical3_module(
        comp, scene, ClassifierOptions(
            segment_size_mm=options.wall_segment_size_mm,
            include_floor_panels=options.include_floor_panels_as_obstacle,
            enabled=options.wall_classifier_enabled,
        ),
    )
    # 슬래브 3 장 자중
    area_m2 = (w / 1000.0) * (d / 1000.0)
    extra = 3 * _slab_self_weight_kg(area_m2, options)
    extra += _sum_v3_face_nonbearing_weight(comp, face_classes, options)
    extra += _sum_mid_member_weight_in_module(cid, scene, model, design_result)
    extra += _sum_cantilever_weight_attached_to(cid, scene, model, design_result, options)

    if options.treat_v3_module_as_lying:
        # 회전 매핑: length=height, width=width, height=depth
        return TModule(
            name=label + "(눕힘)",
            width=w, length=h, height=d,
            column_section=to_transport_section(col_pick),
            beam_section=to_transport_section(beam_pick),
            extra_weight_kg=extra,
        )
    else:
        return TModule(
            name=label,
            width=w, length=d, height=h,
            column_section=to_transport_section(col_pick),
            beam_section=to_transport_section(beam_pick),
            extra_weight_kg=extra,
        )


# ── 변환 — 종속/순수 FloorPanel ────────────────────────────
def _wall_to_segment(
    wall: StructWall, fp: FloorPanel, design_result, diag: Diagnostics,
) -> Optional[WallSegment]:
    """StructWall → WallSegment (FloorPanel 변과 매칭)."""
    # 면 매칭 — 단순화: wall.position 과 fp.position 의 상대 위치로 side 판별.
    # 정확한 매칭은 anchor_edge_id (StructWall 의 부모 모서리) 가 있으면 사용.
    side_from_anchor = getattr(wall, "anchor_edge_id", -1)
    side = side_from_anchor if side_from_anchor in (0, 1, 2, 3) else 0

    # 벽 단면
    col_secs: List[SHSSection] = []
    beam_secs: List[SHSSection] = []
    for mid in []:  # model.comp_to_members 는 외부에서 닫음 — 호출부에서 단면 주입
        pass
    # 호출부에서 단면을 미리 채워서 wall.column_section/beam_section 으로 임시 주입
    col_sec: Optional[SHSSection] = getattr(wall, "_tx_col_sec", None)
    beam_sec: Optional[SHSSection] = getattr(wall, "_tx_beam_sec", None)
    if col_sec is None or beam_sec is None:
        diag.warnings.append(f"wall #{wall.id}: 단면 미주입 — wall_segment 생성 실패")
        return None

    return WallSegment(
        side=side, start_offset_mm=0.0,
        length_mm=float(wall.dimensions["width"]),    # 벽 길이
        height_mm=float(wall.dimensions["height"]),   # 벽 높이
        thickness_mm=float(wall.dimensions["depth"]), # 벽 두께
        column_section=to_transport_section(col_sec),
        beam_section=to_transport_section(beam_sec),
    )


def _inject_wall_sections(wall: StructWall, model, design_result) -> None:
    """StructWall 에 단면을 임시 속성으로 주입 (wall_segment 변환용)."""
    col_secs: List[SHSSection] = []
    beam_secs: List[SHSSection] = []
    for mid in model.comp_to_members.get(wall.id, []):
        m = model.members.get(mid)
        if m is None:
            continue
        kind = getattr(m, "kind", None) or getattr(m, "member_type", None)
        sec = lookup_section_for_member(mid, design_result)
        if sec is None:
            continue
        if kind == "column":
            col_secs.append(sec)
        elif kind == "beam":
            beam_secs.append(sec)
    setattr(wall, "_tx_col_sec", _pick_heaviest(col_secs))
    setattr(wall, "_tx_beam_sec", _pick_heaviest(beam_secs))


def convert_floor_panel_pure(
    fp: FloorPanel, cid: int, model, design_result,
    options: TransportOptions, diag: Diagnostics, label: str,
) -> Optional[TPanel]:
    """순수 floor (merged_wall_ids 빈 경우) → Panel(kind='floor', wall_segments=())."""
    w = float(fp.dimensions["width"])
    d = float(fp.dimensions["depth"])
    # 둘레 보 단면 — beam_section 만 의미 있음
    beam_secs: List[SHSSection] = []
    for mid in model.comp_to_members.get(cid, []):
        m = model.members.get(mid)
        if m is None:
            continue
        sec = lookup_section_for_member(mid, design_result)
        if sec is not None:
            beam_secs.append(sec)
    beam_pick = _pick_heaviest(beam_secs)
    if beam_pick is None:
        diag.warnings.append(f"{label}: floor 패널 단면 룩업 실패 — 제외")
        return None
    area_m2 = (w / 1000.0) * (d / 1000.0)
    extra = _slab_self_weight_kg(area_m2, options)
    return TPanel(
        name=label, kind="floor",
        width=w, length=d, thickness=options.floor_slab_thickness_mm,
        beam_section=to_transport_section(beam_pick),
        extra_weight_kg=extra,
    )


def convert_floor_panel_dependent(
    fp: FloorPanel, cid: int, walls: List[StructWall], scene: Scene,
    model, design_result, options: TransportOptions, diag: Diagnostics, label: str,
) -> Optional[TPanel]:
    """종속 floor (L자/ㄷ자/3면/4면) — wall_segments 채워서 Panel(kind='floor').

    [Phase 6 점검 패치 — 결정 1] 종속 벽의 비내력벽 단위중량은 독립 벽과
    동일하게 양면 자동판별 가중평균으로 산출. 기존 "외부 100% 고정" 보수적
    추정 폐기. 분류 결과 없는 벽은 외부 가정(fallback).
    """
    w = float(fp.dimensions["width"])
    d = float(fp.dimensions["depth"])

    beam_secs: List[SHSSection] = []
    for mid in model.comp_to_members.get(cid, []):
        sec = lookup_section_for_member(mid, design_result)
        if sec is not None:
            beam_secs.append(sec)
    beam_pick = _pick_heaviest(beam_secs)
    if beam_pick is None:
        diag.warnings.append(f"{label}: dependent 패널 보 단면 룩업 실패 — 제외")
        return None

    # 각 wall → wall_segment + 같은 wall 의 양면 자동판별
    segments: List[WallSegment] = []
    classifier_opts = ClassifierOptions(
        segment_size_mm=options.wall_segment_size_mm,
        include_floor_panels=options.include_floor_panels_as_obstacle,
        enabled=options.wall_classifier_enabled,
    )
    nb_extra = 0.0  # 비내력벽 자중 합
    for wall in walls:
        _inject_wall_sections(wall, model, design_result)
        seg = _wall_to_segment(wall, fp, design_result, diag)
        if seg is not None:
            segments.append(seg)
        # 양면 자동판별 — 독립 벽과 동일 알고리즘
        try:
            face_cls = classify_independent_wall(wall, scene, classifier_opts)
        except Exception as e:
            diag.warnings.append(
                f"{label}: 종속 벽 #{wall.id} 자동판별 실패 — 외부 가정. {type(e).__name__}: {e}"
            )
            face_cls = {}
        front_unit = face_unit_weight(
            face_cls.get("전면", FaceClass(exterior_ratio=1.0)),
            options.interior_wall_unit_weight, options.exterior_wall_unit_weight,
        )
        back_unit = face_unit_weight(
            face_cls.get("후면", FaceClass(exterior_ratio=1.0)),
            options.interior_wall_unit_weight, options.exterior_wall_unit_weight,
        )
        avg_unit = 0.5 * (front_unit + back_unit)
        wall_w_m = float(wall.dimensions["width"]) / 1000.0
        wall_h_m = float(wall.dimensions["height"]) / 1000.0
        nb_extra += avg_unit * wall_w_m * wall_h_m

    extra = _slab_self_weight_kg((w / 1000.0) * (d / 1000.0), options) + nb_extra

    return TPanel(
        name=label, kind="floor",
        width=w, length=d, thickness=options.floor_slab_thickness_mm,
        beam_section=to_transport_section(beam_pick),
        extra_weight_kg=extra,
        wall_segments=tuple(segments),
    )


def convert_independent_wall_panel(
    wall: StructWall, cid: int, scene: Scene, model, design_result,
    options: TransportOptions, diag: Diagnostics, label: str,
) -> Optional[TPanel]:
    """독립 wall → Panel(kind='wall')."""
    w = float(wall.dimensions["width"])    # 벽 길이
    d = float(wall.dimensions["depth"])    # 벽 두께
    h = float(wall.dimensions["height"])   # 벽 높이

    col_secs: List[SHSSection] = []
    beam_secs: List[SHSSection] = []
    for mid in model.comp_to_members.get(cid, []):
        m = model.members.get(mid)
        if m is None:
            continue
        kind = getattr(m, "kind", None) or getattr(m, "member_type", None)
        sec = lookup_section_for_member(mid, design_result)
        if sec is None:
            continue
        if kind == "column":
            col_secs.append(sec)
        elif kind == "beam":
            beam_secs.append(sec)
    col_pick = _pick_heaviest(col_secs)
    beam_pick = _pick_heaviest(beam_secs)
    if col_pick is None or beam_pick is None:
        diag.warnings.append(f"{label}: 독립 wall 단면 룩업 실패 — 제외")
        return None

    # 양면 분류 → 평균 단위중량 × 한 면 면적 (A-3)
    face_cls = classify_independent_wall(
        wall, scene, ClassifierOptions(
            segment_size_mm=options.wall_segment_size_mm,
            include_floor_panels=options.include_floor_panels_as_obstacle,
            enabled=options.wall_classifier_enabled,
        ),
    )
    front_unit = face_unit_weight(
        face_cls.get("전면", FaceClass(exterior_ratio=1.0)),
        options.interior_wall_unit_weight, options.exterior_wall_unit_weight,
    )
    back_unit = face_unit_weight(
        face_cls.get("후면", FaceClass(exterior_ratio=1.0)),
        options.interior_wall_unit_weight, options.exterior_wall_unit_weight,
    )
    avg_unit = 0.5 * (front_unit + back_unit)
    extra = avg_unit * (w / 1000.0) * (h / 1000.0)

    # 운송 측 Panel(kind='wall') 매핑: width=벽 길이(=length 방향), length=벽 길이?
    # 분석 ① C: width=층고, length=벽 길이. 여기서는 단순화로 width=w, length=w (보 식 단순화)
    # 더 정확히 분석 ① 결정대로 width=h(층고), length=w(벽 길이) 매핑.
    return TPanel(
        name=label, kind="wall",
        width=h, length=w, thickness=d,
        beam_section=to_transport_section(beam_pick),
        column_section=to_transport_section(col_pick),
        wall_height=h,
        extra_weight_kg=extra,
    )


# ── 진입점 ────────────────────────────────────────────────
def build_transport_input(
    scene: Scene, model, design_result, policy: str,
    options: Optional[TransportOptions] = None,
) -> TransportInput:
    """우리 씬 → 운송 입력 단일 진입점."""
    if scene is None:
        raise TransportError("scene 이 None — 씬을 먼저 생성하세요.")
    if model is None or not getattr(model, "comp_to_members", None):
        raise TransportError("AnalysisModel 미준비 — 구조해석을 먼저 실행하세요.")
    if design_result is None:
        raise TransportError("DesignResult 미준비 — 단면 산정을 먼저 실행하세요.")
    if not getattr(design_result, "groups", None):
        raise TransportError("DesignResult.groups 가 비어있음.")
    if options is None:
        options = TransportOptions()

    diag = Diagnostics()
    ti = TransportInput()
    comp_meta = build_comp_meta(scene, model)

    # 비내력벽 분류는 모듈별로 호출 (캐싱은 분석 ⑧ 캐시 정책에서 통합)
    classifier_opts = ClassifierOptions(
        segment_size_mm=options.wall_segment_size_mm,
        include_floor_panels=options.include_floor_panels_as_obstacle,
        enabled=options.wall_classifier_enabled,
    )

    processed_walls: set = set()

    # 1차 패스 — FloorPanel 의 merged_wall_ids 를 미리 처리하기 위해 walls 식별
    for cid, comp in list(scene.components.items()):
        if not isinstance(comp, StructWall):
            continue
        if comp.merged_fp_id is not None:
            processed_walls.add(cid)

    for cid, comp in scene.components.items():
        meta = comp_meta.get(cid)
        if meta is None:
            # 라벨 불가 (모델에 안 잡힌 컴포넌트) — 컴포넌트 종류로 폴백
            tname = TYPE_NAMES.get(comp.comp_type, "?")
            label = f"#{cid}{tname}"
        else:
            label = _format_label(meta, cid)

        # 코어 계열 — 제외
        if isinstance(comp, (Core, CoreSlab)):
            ti.excluded.append(ExcludedItem(cid=cid, type_name="RC 코어/슬래브",
                                            reason="현장 타설(운송 대상 아님)"))
            continue

        # Mid* — 부모 모듈 extra_weight 에 흡수됨 (위에서 합산)
        if isinstance(comp, (MidBeam, MidColumn)):
            ti.excluded.append(ExcludedItem(cid=cid, type_name="모듈 내부 보강재",
                                            reason="부모 모듈에 흡수"))
            continue

        # 캔틸 — embedded 면 부모에 흡수, separate 면 별도 panel
        if isinstance(comp, (CantileverBeam, CantileverSlab)):
            if options.cantilever_packing_mode == "embedded":
                ti.excluded.append(ExcludedItem(cid=cid, type_name="캔틸레버",
                                                reason="embedded — 부모 모듈에 흡수"))
            else:
                # separate 모드 — Phase 3 1차 구현에서는 단순 floor panel 로 보냄
                if isinstance(comp, CantileverSlab):
                    w = float(comp.dimensions["width"])
                    d = float(comp.dimensions["depth"])
                    beam_secs = []
                    for mid in model.comp_to_members.get(cid, []):
                        sec = lookup_section_for_member(mid, design_result)
                        if sec is not None:
                            beam_secs.append(sec)
                    beam_pick = _pick_heaviest(beam_secs)
                    if beam_pick is not None:
                        extra = _slab_self_weight_kg((w / 1000.0) * (d / 1000.0), options)
                        ti.panels.append(TPanel(
                            name=label + "(캔틸)", kind="floor",
                            width=w, length=d, thickness=options.floor_slab_thickness_mm,
                            beam_section=to_transport_section(beam_pick),
                            extra_weight_kg=extra,
                        ))
                        ti.source_index.setdefault(label + "(캔틸)", []).append(cid)
            continue

        if isinstance(comp, Vertical3Module):
            tm = convert_vertical3_lying(comp, cid, scene, model, design_result,
                                          options, diag, label)
            if tm is not None:
                ti.modules.append(tm)
                ti.source_index.setdefault(tm.name, []).append(cid)
            continue

        if isinstance(comp, SceneModule):
            face_cls = classify_module(comp, scene, classifier_opts)
            tm = convert_module(comp, cid, scene, model, design_result,
                                 options, diag, label, face_classes=face_cls)
            if tm is not None:
                ti.modules.append(tm)
                ti.source_index.setdefault(tm.name, []).append(cid)
            continue

        if isinstance(comp, FloorPanel):
            if comp.merged_wall_ids:
                walls = [scene.components.get(wid) for wid in comp.merged_wall_ids]
                walls = [w for w in walls if isinstance(w, StructWall)]
                tp = convert_floor_panel_dependent(
                    comp, cid, walls, scene, model, design_result, options, diag, label)
            else:
                tp = convert_floor_panel_pure(comp, cid, model, design_result,
                                               options, diag, label)
            if tp is not None:
                ti.panels.append(tp)
                ti.source_index.setdefault(tp.name, []).append(cid)
            continue

        if isinstance(comp, StructWall):
            if cid in processed_walls:
                continue
            tp = convert_independent_wall_panel(
                comp, cid, scene, model, design_result, options, diag, label)
            if tp is not None:
                ti.panels.append(tp)
                ti.source_index.setdefault(tp.name, []).append(cid)
            continue

        # 미지의 타입 — 경고
        diag.warnings.append(f"{label}: 알 수 없는 컴포넌트 타입 {type(comp).__name__}")

    ti.diagnostics = diag
    return ti


__all__ = [
    "TransportOptions", "TransportInput", "TransportError",
    "ExcludedItem", "Diagnostics",
    "to_transport_section", "lookup_section_for_member",
    "build_comp_meta", "build_transport_input",
    "convert_module", "convert_vertical3_lying",
    "convert_floor_panel_pure", "convert_floor_panel_dependent",
    "convert_independent_wall_panel",
]
