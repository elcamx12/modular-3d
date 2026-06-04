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

[A-2 캔틸 합체 — Phase 1 재설계 2026-05-27]
캔틸레버 보·슬래브는 부모 화물(모듈 또는 종속 floor)에 *기하학적으로* 종속된 상태로
같은 회차에 함께 운송된다. cantilever_packing_mode 옵션(embedded/separate) 폐지.
- 자중: 부모 모듈/패널의 extra_weight 에 가산 (기존 그대로)
- 형상: 부모의 attached_parts 에 AttachedPart 로 첨부 — 안전 검사·시각화가 이를 반영

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
    InteriorWall,
    BeamData, ColumnData, SlabData, WallPanelData,
    _rotate_point, _rotate_local_to_world,
    TYPE_NAMES,
)
from ..카탈로그.geometry import (
    SLAB_THICKNESS_MM, FLOOR_HEIGHT, SECTION_W_MM, SECTION_H_MM,
)
from ..카탈로그.materials import CONCRETE_DENSITY_KG_M3
from ..카탈로그.steel_sections import SHSSection
from .models import (
    Section as TSection, Module as TModule, Panel as TPanel,
    WallSegment, AttachedPart, BodyPart, SectionType,
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
    # 캔틸 처리 — Phase 1 (2026-05-27) 이후 캔틸레버는 *항상* 부모에 기하학적 종속.
    # cantilever_packing_mode 옵션 폐지. include_cantilever 만 유지(자중 합산 토글).
    include_cantilever: bool = True

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
    # 독립 벽패널: 운송은 눕혀 적재(True), 물량은 세워서 표시(False).
    treat_wall_as_lying: bool = True

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
def to_transport_section(sec) -> TSection:
    """디자인 단면 → 운송 Section. 각형강관(SHSSection)·H형강(HSection) 모두 처리.

    [2026-05-26 일반화] 종전엔 각형강관(w/h/t)만 가정해, 디자인에서 보를
    H형강으로 설계하면 운송 변환이 깨졌다. H형강(H/B/t1/t2)도 받도록 분기한다.
    무게는 둘 다 weight_per_m_kg(단위중량)로 계산되므로 정확하다.
    - 각형강관: width=w, height=h, thickness=t, flange=0, type="SHS"
    - H형강:   width=B(플랜지폭), height=H(전체높이), thickness=t1(웨브),
               flange_thickness=t2(플랜지), type="H"
    """
    if hasattr(sec, "t1"):  # H형강 — HSection(H,B,t1,t2,...)
        return TSection(
            name=sec.name,
            section_type="H",
            width=float(sec.B),
            height=float(sec.H),
            thickness=float(sec.t1),
            weight_per_m=float(sec.weight_per_m_kg),
            flange_thickness=float(sec.t2),
        )
    # 각형강관 — SHSSection(w,h,t)
    return TSection(
        name=sec.name,
        section_type="SHS",
        width=float(sec.w),
        height=float(sec.h),
        thickness=float(sec.t),
        weight_per_m=float(sec.weight_per_m_kg),
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


# ── 라벨 (comp_meta 재구성) — _utils/format.py 의 공용 함수 사용 ────────
from modular_3d._utils.format import alpha_label as _alpha_label


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
    """부모 cid 에 매달린 캔틸 부재(보 + 슬래브) 자중.

    Phase 1 (2026-05-27): cantilever_packing_mode 폐지. 캔틸은 항상 부모에
    기하 종속이므로 자중은 항상 부모 extra_weight 에 합산한다.
    include_cantilever=False 면 합산 생략(자중 무시 토글).
    """
    if not options.include_cantilever:
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


# ── Phase 3-fix — 부재 위치 실측 헬퍼 ─────────────────────
# [매핑 규칙] 운송 nominal 좌표계
#   운송 X (length 축) = 배치 nominal Y (depth 축)
#   운송 Y (width 축)  = 배치 nominal X (width 축)
#   운송 Z (height 축) = 배치 nominal Z
# (어댑터에서 TModule(width=dimensions['width'], length=dimensions['depth']) 정의
#  와 일관됨. 부재 좌표도 동일하게 X-Y 교환해 nominal 으로 변환.)
def _section_name_for_comp(cid: int, model, design_result) -> str:
    """부재 cid 의 *대표* 단면명 (시각화 라벨용). 없으면 빈 문자열."""
    for mid in model.comp_to_members.get(cid, []):
        sec = lookup_section_for_member(mid, design_result)
        if sec is not None:
            return getattr(sec, "name", "")
    return ""


def _world_to_parent_nominal(
    world_pt: np.ndarray, parent_comp: Component,
) -> Tuple[float, float, float]:
    """월드 좌표 → 부모 컴포넌트의 *회전·앵커 풀린* nominal 좌표 (mm).

    [수식 — core.py 의 to_world 역변환]
      to_world(lx, ly, lz) = rotate(lx - ax, ly - ay, rot) + pos
    역:
      lx - ax = unrotate(world - pos).x
      lx = unrotate(world - pos).x + ax

    여기서 nominal 좌표 (lx, ly) ∈ [0, W] × [0, D] 는 부모의 *원래 차원* 좌표계.
    회전·앵커가 어떻게 바뀌든 항상 같은 좌표를 반환 — 사용자가 r/v/m/c 로
    회전·반전해도 부재 위치가 어긋나지 않는 핵심.
    """
    W = float(parent_comp.dimensions.get("width", 0.0))
    D = float(parent_comp.dimensions.get("depth", 0.0))
    ax, ay = parent_comp._anchor_offset(W, D)
    rel = np.asarray(world_pt, dtype=np.float64) - parent_comp.position
    inv_rot = (-int(parent_comp.rotation)) % 360
    lx_off, ly_off = _rotate_point(float(rel[0]), float(rel[1]), inv_rot)
    return (lx_off + ax, ly_off + ay, float(rel[2]))


def _nominal_aabb_of_points(
    world_points, parent_comp: Component,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """월드 좌표 점들을 모두 parent nominal 로 변환 + AABB."""
    nps = [_world_to_parent_nominal(p, parent_comp) for p in world_points]
    xs = [p[0] for p in nps]; ys = [p[1] for p in nps]; zs = [p[2] for p in nps]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _anchored_extent_1d(center_pos, comp_center, actual, nominal):
    """단면축 1개의 [min,max] — 외곽면을 (중심선 + 부호·공칭/2) 에 고정하고
    안쪽으로 actual 만큼 성장. 부호 = 모듈 중심 반대쪽(외곽) 방향. 부재가 모듈
    중앙(부호 0)이면 중심 대칭. 기둥·보 공용(3D 렌더 outer_anchor 와 동일 규칙).

    단면이 공칭(nominal)보다 커져도 외곽면(footprint)이 불변이라, 둘레 부재
    단면이 커져도 모듈 외곽 폭이 늘지 않는다(→ 운송 폭 과대계산 방지).
    """
    d = center_pos - comp_center
    sign = 1.0 if d > 1.0 else (-1.0 if d < -1.0 else 0.0)
    if sign == 0.0:
        return center_pos - actual / 2.0, center_pos + actual / 2.0
    outer = center_pos + sign * (nominal / 2.0)   # footprint 외곽면(불변)
    inner = outer - sign * actual                 # 안쪽으로 성장
    return min(outer, inner), max(outer, inner)


def _module_col_section_w(comp: Component) -> float:
    """모듈/벽 기둥의 대표 단면폭(mm) — 보 길이축을 기둥 안쪽 면에 맞출 때 사용.
    기둥이 없으면(바닥패널 등) 0 → 보 길이 조정 안 함."""
    cols = getattr(comp, "columns", None) or []
    if not cols:
        return 0.0
    return max(float(getattr(c, "section_w", 0.0)) for c in cols)


def _nominal_aabb_beam(
    b: BeamData, parent_comp: Component,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """보의 nominal AABB — 시작/끝 + 단면 두께 반영.

    [2026-06-03] 두 가지 외곽 보정(시각화 목적, 물량·해석 수치는 불변):
    1. 수평 단면폭(sw)은 외곽 고정·안쪽 성장(기둥과 동일). 둘레보 단면이 커져도
       모듈 footprint 폭이 불변 → 운송 폭 과대계산·누락 방지.
    2. 길이축 양 끝이 모서리 기둥에 닿으면 *기둥 안쪽 면* 까지 줄이거나 늘린다.
       기둥이 두꺼워지면 안쪽 면이 안으로 들어오므로 보가 짧아지고(겹침 방지),
       얇아지면 길어진다(틈 방지). 길이축이 모듈 경계에서 먼 내부 보는 그대로.
    높이(z)는 그대로 둔다.
    """
    s_n = _world_to_parent_nominal(b.start, parent_comp)
    e_n = _world_to_parent_nominal(b.end, parent_comp)
    sw, sh = float(b.section_w), float(b.section_h)
    W = float(parent_comp.dimensions.get("width", 0.0))
    D = float(parent_comp.dimensions.get("depth", 0.0))
    col_sw = _module_col_section_w(parent_comp)
    dx = abs(e_n[0] - s_n[0]); dy = abs(e_n[1] - s_n[1])
    if dx >= dy:
        # X 방향 보 — 길이=x, 수평 단면폭 sw=y(외곽 고정), 높이 sh=z(그대로)
        x0, x1 = min(s_n[0], e_n[0]), max(s_n[0], e_n[0])
        if col_sw > 0.0:
            if x0 <= SECTION_W_MM:       # near 끝 = 경계(0) 쪽 기둥
                x0 = col_sw              # 기둥 안쪽 면
            if x1 >= W - SECTION_W_MM:   # far 끝 = 경계(W) 쪽 기둥
                x1 = W - col_sw
        x_min, x_max = x0, x1
        y_min, y_max = _anchored_extent_1d(s_n[1], D / 2.0, sw, SECTION_W_MM)
        z_min, z_max = s_n[2] - sh / 2, s_n[2] + sh / 2
    else:
        # Y 방향 보 — 길이=y, 수평 단면폭 sw=x(외곽 고정), 높이 sh=z(그대로)
        y0, y1 = min(s_n[1], e_n[1]), max(s_n[1], e_n[1])
        if col_sw > 0.0:
            if y0 <= SECTION_W_MM:
                y0 = col_sw
            if y1 >= D - SECTION_W_MM:
                y1 = D - col_sw
        x_min, x_max = _anchored_extent_1d(s_n[0], W / 2.0, sw, SECTION_W_MM)
        y_min, y_max = y0, y1
        z_min, z_max = s_n[2] - sh / 2, s_n[2] + sh / 2
    return (x_min, y_min, z_min), (x_max, y_max, z_max)


def _nominal_aabb_column(
    c: ColumnData, parent_comp: Component,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """기둥의 nominal AABB — 외곽 고정·안쪽 성장 (3D 렌더링과 동일 규칙).

    [2026-06-03 버그수정] 과거엔 `중심선 ± 실제단면/2` 로 양쪽(바깥 포함)으로
    키워, 설계가 큰 단면(예: SHS 350)을 배정하면 기둥 외곽이 모듈 footprint
    (= 중심선 ± 공칭 SECTION_W_MM/2) 를 넘어섰다. 그 결과 폭 3400 모듈이 운송에서
    3550 으로 잡혀 현장 폭 한도 초과로 *조용히 누락*됐다.

    3D 렌더링(mesh_builder._add_columns, outer_anchor)은 모듈 중심 반대쪽(외곽)
    면을 공칭 위치에 고정하고 *안쪽으로* 성장시켜, 단면이 커져도 외곽(footprint)이
    불변이다. 운송도 동일 규칙을 적용해 footprint 를 보존한다.
    """
    b_n = _world_to_parent_nominal(c.base, parent_comp)
    t_n = _world_to_parent_nominal(c.top, parent_comp)
    sw, sh = float(c.section_w), float(c.section_h)
    z_min, z_max = min(b_n[2], t_n[2]), max(b_n[2], t_n[2])

    # 모듈 footprint 중심 (nominal [0,W]×[0,D])
    W = float(parent_comp.dimensions.get("width", 0.0))
    D = float(parent_comp.dimensions.get("depth", 0.0))
    cx, cy = W / 2.0, D / 2.0

    x_min, x_max = _anchored_extent_1d(b_n[0], cx, sw, SECTION_W_MM)
    y_min, y_max = _anchored_extent_1d(b_n[1], cy, sh, SECTION_H_MM)
    return (x_min, y_min, z_min), (x_max, y_max, z_max)


def _nominal_aabb_slab(
    s: SlabData, parent_comp: Component,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """슬래브의 nominal AABB — 4 코너 (z = 하면) + thickness (z 방향)."""
    (xmin, ymin, zmin), (xmax, ymax, _zmax) = _nominal_aabb_of_points(
        s.corners, parent_comp,
    )
    return (xmin, ymin, zmin), (xmax, ymax, zmin + float(s.thickness))


def _nominal_aabb_wallpanel(
    w: WallPanelData, parent_comp: Component,
) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """벽 채움의 nominal AABB — 평면 4 코너 + thickness 방향 확장.

    평면 판별: 4 코너의 nominal x 또는 y 중 *분산이 작은 축* 이 두께 방향.
    회전 0/90/180/270 만 다루므로 항상 axis-aligned 평면.
    """
    nps = [_world_to_parent_nominal(p, parent_comp) for p in w.corners]
    xs = [p[0] for p in nps]; ys = [p[1] for p in nps]; zs = [p[2] for p in nps]
    t = float(w.thickness)
    dx = max(xs) - min(xs); dy = max(ys) - min(ys)
    if dx < 1.0:
        # X 평면 (좌·우 벽) — 두께 = X 방향
        cx = (max(xs) + min(xs)) / 2
        return ((cx - t / 2, min(ys), min(zs)),
                (cx + t / 2, max(ys), max(zs)))
    if dy < 1.0:
        # Y 평면 (전·후 벽) — 두께 = Y 방향
        cy = (max(ys) + min(ys)) / 2
        return ((min(xs), cy - t / 2, min(zs)),
                (max(xs), cy + t / 2, max(zs)))
    # 둘 다 분산 큼 — 평면이 아님 (이론상 없음)
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


# ── 개구부 '실제 구멍' 표현 — 면을 개구부 제외 직사각형 조각으로 분할 ──────
# 운송/물량 3D 는 부재를 AABB 박스로 그린다(three.js BoxGeometry, CSG 없음).
# 통짜 박스 1개 대신, 개구부를 뺀 잔여 직사각형 여러 조각을 각각 박스로 만들면
# 개구부 자리는 박스가 비어 '구멍'처럼 보인다. 회전 0/90/180/270 의 축정렬
# 평면만 다루므로 조각 AABB 가 정확하다.

def _seg_len(a, b) -> float:
    return ((float(b[0]) - float(a[0])) ** 2
            + (float(b[1]) - float(a[1])) ** 2
            + (float(b[2]) - float(a[2])) ** 2) ** 0.5


def _uv_world_pt(c0, cu, cv, fu, fv):
    """면 코너(c0=원점, cu=+u끝, cv=+v끝)에서 보간 비율 (fu,fv) 의 월드 점."""
    return tuple(
        float(c0[k]) + (float(cu[k]) - float(c0[k])) * fu
                     + (float(cv[k]) - float(c0[k])) * fv
        for k in range(3)
    )


def _rects_minus_openings(U: float, V: float, ops):
    """면 직사각형 [0,U]×[0,V] 에서 개구부들을 뺀 잔여 직사각형 조각 목록.

    개구부 경계로 격자 컷을 만들고, 셀 중심이 어느 개구부에도 안 들면 채택한다.
    개구부 없으면 통짜 1개. ops = [(u,v,w,h), ...] (면 로컬 mm).
    """
    if not ops:
        return [(0.0, 0.0, U, V)]

    def _clip(val, hi):
        return max(0.0, min(hi, val))

    us = sorted({0.0, U} | {_clip(o[0], U) for o in ops}
                | {_clip(o[0] + o[2], U) for o in ops})
    vs = sorted({0.0, V} | {_clip(o[1], V) for o in ops}
                | {_clip(o[1] + o[3], V) for o in ops})
    rects = []
    for i in range(len(us) - 1):
        u0, u1 = us[i], us[i + 1]
        if u1 - u0 < 1.0:
            continue
        for j in range(len(vs) - 1):
            v0, v1 = vs[j], vs[j + 1]
            if v1 - v0 < 1.0:
                continue
            uc, vc = (u0 + u1) / 2.0, (v0 + v1) / 2.0
            inside = any(o[0] <= uc <= o[0] + o[2] and o[1] <= vc <= o[1] + o[3]
                         for o in ops)
            if not inside:
                rects.append((u0, v0, u1, v1))
    return rects


def _wall_rect_nominal_aabb(pts4, thickness, parent_comp):
    """벽 평면 조각(4 월드 코너) + 두께 → nominal AABB (축정렬 평면 가정)."""
    nps = [_world_to_parent_nominal(p, parent_comp) for p in pts4]
    xs = [p[0] for p in nps]; ys = [p[1] for p in nps]; zs = [p[2] for p in nps]
    t = float(thickness)
    dx = max(xs) - min(xs); dy = max(ys) - min(ys)
    if dx < 1.0:
        cx = (max(xs) + min(xs)) / 2.0
        return ((cx - t / 2, min(ys), min(zs)), (cx + t / 2, max(ys), max(zs)))
    if dy < 1.0:
        cy = (max(ys) + min(ys)) / 2.0
        return ((min(xs), cy - t / 2, min(zs)), (max(xs), cy + t / 2, max(zs)))
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def _slab_rect_nominal_aabb(pts4, thickness, parent_comp):
    """슬래브 평면 조각(4 월드 코너, z=하면) + 두께(z) → nominal AABB."""
    nps = [_world_to_parent_nominal(p, parent_comp) for p in pts4]
    xs = [p[0] for p in nps]; ys = [p[1] for p in nps]; zs = [p[2] for p in nps]
    zmin = min(zs)
    return ((min(xs), min(ys), zmin), (max(xs), max(ys), zmin + float(thickness)))


def _ops_for_face(comp, face):
    """comp.openings 중 해당 face 의 개구부만 (레거시 face 미지정은 face 로 간주)."""
    return [o for o in (getattr(comp, "openings", None) or [])
            if o.get("face", face) == face]


def _opening_split_wall_parts(wf, parent_comp, ops, kind="wall_fill"):
    """벽 채움을 개구부 제외 조각 BodyPart 들로. 개구부 없으면 통짜 1개."""
    c = list(wf.corners)
    if len(c) < 4:
        return []
    c0, cu, cv = c[0], c[1], c[3]
    U = _seg_len(c0, cu); V = _seg_len(c0, cv)
    if U < 1e-3 or V < 1e-3:
        return []
    op_uvwh = [(float(o.get("u", 0.0)), float(o.get("v", 0.0)),
                float(o.get("w", 0.0)), float(o.get("h", 0.0))) for o in ops]
    out = []
    for (u0, v0, u1, v1) in _rects_minus_openings(U, V, op_uvwh):
        pts = [_uv_world_pt(c0, cu, cv, u0 / U, v0 / V),
               _uv_world_pt(c0, cu, cv, u1 / U, v0 / V),
               _uv_world_pt(c0, cu, cv, u1 / U, v1 / V),
               _uv_world_pt(c0, cu, cv, u0 / U, v1 / V)]
        bp = _make_body_part(kind, _wall_rect_nominal_aabb(pts, wf.thickness, parent_comp))
        if bp is not None:
            out.append(bp)
    return out


def _opening_split_slab_parts(s, parent_comp, ops):
    """슬래브를 개구부 제외 조각 BodyPart 들로. 개구부 없으면 통짜 1개."""
    c = list(s.corners)
    if len(c) < 4:
        return []
    c0, cu, cv = c[0], c[1], c[3]
    U = _seg_len(c0, cu); V = _seg_len(c0, cv)
    if U < 1e-3 or V < 1e-3:
        return []
    op_uvwh = [(float(o.get("u", 0.0)), float(o.get("v", 0.0)),
                float(o.get("w", 0.0)), float(o.get("h", 0.0))) for o in ops]
    out = []
    for (u0, v0, u1, v1) in _rects_minus_openings(U, V, op_uvwh):
        pts = [_uv_world_pt(c0, cu, cv, u0 / U, v0 / V),
               _uv_world_pt(c0, cu, cv, u1 / U, v0 / V),
               _uv_world_pt(c0, cu, cv, u1 / U, v1 / V),
               _uv_world_pt(c0, cu, cv, u0 / U, v1 / V)]
        bp = _make_body_part("slab", _slab_rect_nominal_aabb(pts, s.thickness, parent_comp))
        if bp is not None:
            out.append(bp)
    return out


def _nominal_to_transport_box(
    nominal_aabb: Tuple[Tuple[float, float, float], Tuple[float, float, float]],
) -> Tuple[float, float, float, float, float, float]:
    """*nominal AABB → 운송 좌표계 박스* (x, y, z, L, W, H).

    매핑: 운송 x = nominal y, 운송 y = nominal x, 운송 z = nominal z.
    (운송 length 축 = 배치 depth, 운송 width 축 = 배치 width.)
    """
    (nx_min, ny_min, nz_min), (nx_max, ny_max, nz_max) = nominal_aabb
    return (
        ny_min, nx_min, nz_min,
        ny_max - ny_min, nx_max - nx_min, nz_max - nz_min,
    )


def _make_body_part(
    kind: str, nominal_aabb, subkind: str = "",
    section_type: str = "shs",
) -> Optional[BodyPart]:
    """nominal AABB → BodyPart (운송 좌표계 변환 후 생성). 영부피면 None.

    section_type: 보(kind="beam")의 단면 형상('shs'|'h'). 정상 자세의 보는
    웨브가 연직이라 web_dir=(0,0,1) 로 둔다(눕히기 remap 에서 축 순열 적용).
    """
    x, y, z, L, W, H = _nominal_to_transport_box(nominal_aabb)
    if L <= 1e-3 or W <= 1e-3 or H <= 1e-3:
        return None
    return BodyPart(
        kind=kind, subkind=subkind,
        x_mm=float(x), y_mm=float(y), z_mm=float(z),
        length_mm=float(L), width_mm=float(W), height_mm=float(H),
        section_type=section_type,
        web_dx=0.0, web_dy=0.0, web_dz=1.0,
    )


def _make_attached_part(
    kind: str, source_kind: str, nominal_aabb, subkind: str = "",
    section_name: str = "", section_type: str = "shs",
) -> Optional[AttachedPart]:
    """nominal AABB → AttachedPart (운송 좌표계 변환 후 생성). 영부피면 None."""
    x, y, z, L, W, H = _nominal_to_transport_box(nominal_aabb)
    if L <= 1e-3 or W <= 1e-3 or H <= 1e-3:
        return None
    return AttachedPart(
        kind=kind, source_kind=source_kind, subkind=subkind,
        x_mm=float(x), y_mm=float(y), z_mm=float(z),
        length_mm=float(L), width_mm=float(W), height_mm=float(H),
        section_name=section_name,
        section_type=section_type,
        web_dx=0.0, web_dy=0.0, web_dz=1.0,
    )


def _collect_module_body_parts(
    comp: SceneModule,
) -> Tuple[BodyPart, ...]:
    """단층 모듈 — 기둥 4 + 하부보 4 + 상부보 4 + 슬래브 1 + 비내력벽 4."""
    parts: List[BodyPart] = []
    for c in comp.columns:
        bp = _make_body_part("column", _nominal_aabb_column(c, comp))
        if bp is not None: parts.append(bp)
    for b in comp.bottom_beams:
        bp = _make_body_part("beam", _nominal_aabb_beam(b, comp), subkind="bottom",
                             section_type=getattr(b, "section_type", "shs"))
        if bp is not None: parts.append(bp)
    for b in comp.top_beams:
        bp = _make_body_part("beam", _nominal_aabb_beam(b, comp), subkind="top",
                             section_type=getattr(b, "section_type", "shs"))
        if bp is not None: parts.append(bp)
    if comp.slab is not None:
        parts.extend(_opening_split_slab_parts(
            comp.slab, comp, _ops_for_face(comp, 'slab')))
    for i, w in enumerate(comp.wall_fills):
        parts.extend(_opening_split_wall_parts(
            w, comp, _ops_for_face(comp, f'wall_{i}')))
    return tuple(parts)


def _collect_interior_wall_parts(
    parent_comp: Component, parent_cid: int, scene: Scene,
) -> Tuple[BodyPart, ...]:
    """모듈에 종속된 내벽(InteriorWall) 채움판을 *시각화 전용* BodyPart 로.

    물량/운송 3D 에 내벽을 보이게만 한다. 운송 패킹·겹침 검사는 화물 외곽
    박스로만 수행하므로 내벽 개별 겹침은 보지 않으며, 내벽은 모듈 footprint
    내부(하중 0 부재)라 외곽 차원·무게에 영향이 없다.
    """
    parts: List[BodyPart] = []
    for wcid, wcomp in scene.components.items():
        if getattr(wcomp, "parent_id", 0) != parent_cid:
            continue
        if not isinstance(wcomp, InteriorWall):
            continue
        wf = getattr(wcomp, "wall_fill", None)
        if wf is None:
            continue
        # 내벽 채움도 개구부('wall' face)를 빼고 조각 박스로 — 외피 벽과 동일 렌더.
        parts.extend(_opening_split_wall_parts(
            wf, parent_comp, _ops_for_face(wcomp, 'wall')))
    return tuple(parts)


def _collect_floor_body_parts(
    comp: FloorPanel,
) -> Tuple[BodyPart, ...]:
    """바닥패널 — 둘레 보 4 + 슬래브 1."""
    parts: List[BodyPart] = []
    for b in comp.edge_beams:
        bp = _make_body_part("beam", _nominal_aabb_beam(b, comp), subkind="perim",
                             section_type=getattr(b, "section_type", "shs"))
        if bp is not None: parts.append(bp)
    if comp.slab is not None:
        parts.extend(_opening_split_slab_parts(
            comp.slab, comp, _ops_for_face(comp, 'slab')))
    return tuple(parts)


def _collect_wall_body_parts(
    comp: StructWall,
) -> Tuple[BodyPart, ...]:
    """벽패널 — 양쪽 기둥 2 + 상·하 런너 2 + 채움 1."""
    parts: List[BodyPart] = []
    for c in comp.columns:
        bp = _make_body_part("column", _nominal_aabb_column(c, comp))
        if bp is not None: parts.append(bp)
    if comp.bottom_runner is not None:
        bp = _make_body_part("beam", _nominal_aabb_beam(comp.bottom_runner, comp), subkind="runner",
                             section_type=getattr(comp.bottom_runner, "section_type", "shs"))
        if bp is not None: parts.append(bp)
    if comp.top_runner is not None:
        bp = _make_body_part("beam", _nominal_aabb_beam(comp.top_runner, comp), subkind="runner",
                             section_type=getattr(comp.top_runner, "section_type", "shs"))
        if bp is not None: parts.append(bp)
    if comp.wall_fill is not None:
        parts.extend(_opening_split_wall_parts(
            comp.wall_fill, comp, _ops_for_face(comp, 'wall')))
    return tuple(parts)


def _collect_v3_body_parts(
    comp: Vertical3Module,
) -> Tuple[BodyPart, ...]:
    """수직3모듈 — 4 통기둥 + 층별 슬래브 받침보 4×3 + 옥상 보 4 + 슬래브 3.

    [주의] V3 lying 의 경우 운송 nominal 매핑이 추가 회전됨 (어댑터 측에서
    별도 처리). 여기서는 원래 배치 좌표 nominal AABB 반환 — 운송 매핑은
    convert_vertical3_lying 가 추가 회전 후 BodyPart 생성에 _v3_lying_remap 사용.
    """
    parts: List[BodyPart] = []
    for c in comp.columns:
        bp = _make_body_part("column", _nominal_aabb_column(c, comp))
        if bp is not None: parts.append(bp)
    for fb in comp.bottom_beams:
        for b in fb:
            bp = _make_body_part("beam", _nominal_aabb_beam(b, comp), subkind="bottom",
                                 section_type=getattr(b, "section_type", "shs"))
            if bp is not None: parts.append(bp)
    for b in comp.top_beams:
        bp = _make_body_part("beam", _nominal_aabb_beam(b, comp), subkind="top",
                             section_type=getattr(b, "section_type", "shs"))
        if bp is not None: parts.append(bp)
    for i, s in enumerate(comp.slabs):
        parts.extend(_opening_split_slab_parts(
            s, comp, _ops_for_face(comp, f'slab_{i}')))
    # 비내력벽 12면 — 단층 모듈과 동일하게 운송/물량 3D에 표시(운송 겹침 판정 대상은 아님).
    for i, w in enumerate(getattr(comp, "wall_fills", None) or []):
        parts.extend(_opening_split_wall_parts(
            w, comp, _ops_for_face(comp, f'wall_{i}')))
    return tuple(parts)



# ── 종속 부재 → 부재 단위 AttachedPart 다중 생성 ─────────────
# 핵심: CantileverSlab 의 3 변 보 + 슬래브 = 4 부재를 *각각* 별도 AttachedPart
# 로 첨부. 종전 한 덩어리 AABB 묶음에서 보 정보가 사라지던 문제 해결.
def _collect_child_parts(
    child: Component, parent_comp: Component, child_cid: int,
    model, design_result,
) -> List[AttachedPart]:
    out: List[AttachedPart] = []
    sec_name = _section_name_for_comp(child_cid, model, design_result)
    if isinstance(child, MidBeam) and child.beam is not None:
        ap = _make_attached_part(
            "beam", "midbeam", _nominal_aabb_beam(child.beam, parent_comp),
            section_name=sec_name,
            section_type=getattr(child.beam, "section_type", "shs"),
        )
        if ap is not None: out.append(ap)
    elif isinstance(child, MidColumn) and child.column is not None:
        ap = _make_attached_part(
            "column", "midcolumn", _nominal_aabb_column(child.column, parent_comp),
            section_name=sec_name,
        )
        if ap is not None: out.append(ap)
    elif isinstance(child, CantileverBeam) and child.beam is not None:
        ap = _make_attached_part(
            "beam", "cantilever_beam",
            _nominal_aabb_beam(child.beam, parent_comp),
            section_name=sec_name,
            section_type=getattr(child.beam, "section_type", "shs"),
        )
        if ap is not None: out.append(ap)
    elif isinstance(child, CantileverSlab):
        for b in child.beams:
            ap = _make_attached_part(
                "beam", "cantilever_slab",
                _nominal_aabb_beam(b, parent_comp),
                subkind="perim", section_name=sec_name,
                section_type=getattr(b, "section_type", "shs"),
            )
            if ap is not None: out.append(ap)
        if child.slab is not None:
            ap = _make_attached_part(
                "slab", "cantilever_slab",
                _nominal_aabb_slab(child.slab, parent_comp),
                section_name=sec_name,
            )
            if ap is not None: out.append(ap)
    return out



def _remap_part_v3_lying(bp: BodyPart) -> BodyPart:
    """V3 lying 추가 회전 — 운송 좌표 재매핑.

    _nominal_to_transport_box 가 (운송 x = nominal y, 운송 y = nominal x) 매핑.
    V3 lying 은 운송 x = nominal z, 운송 y = nominal x, 운송 z = nominal y 라야
    하므로, 이미 만들어진 BodyPart 의 (x_t, y_t, z_t, L_t, W_t, H_t) →
    재매핑: (z_t, y_t, x_t, H_t, W_t, L_t) 로 변환.

    [도출]
    normal: x_t = ny, y_t = nx, z_t = nz, L_t = ny_size, W_t = nx_size, H_t = nz_size
    v3:     x_t = nz, y_t = nx, z_t = ny, L_t = nz_size, W_t = nx_size, H_t = ny_size
    -> v3.x_t = normal.z_t, v3.y_t = normal.y_t, v3.z_t = normal.x_t를 잘못
    -- 다시: v3.x_t = nz = normal.H? 음 normal.H_t = nz_size 이고 v3.x_t = nz_min.
    nz_min 는 normal 박스의 z_min = normal.z_t. 그래서 v3.x_t = normal.z_t.
    v3.y_t = nx_min = normal.y_t. v3.z_t = ny_min = normal.x_t.
    v3.L_t = nz_size = normal.H_t. v3.W_t = nx_size = normal.W_t. v3.H_t = ny_size = normal.L_t.
    """
    return BodyPart(
        kind=bp.kind, subkind=bp.subkind,
        x_mm=bp.z_mm, y_mm=bp.y_mm, z_mm=bp.x_mm,
        length_mm=bp.height_mm, width_mm=bp.width_mm, height_mm=bp.length_mm,
        section_type=bp.section_type,
        # 좌표 순열(x←z, y←y, z←x)과 동일하게 웨브 방향도 순열.
        web_dx=bp.web_dz, web_dy=bp.web_dy, web_dz=bp.web_dx,
    )


def _remap_attached_v3_lying(ap: AttachedPart) -> AttachedPart:
    return AttachedPart(
        kind=ap.kind, source_kind=ap.source_kind, subkind=ap.subkind,
        x_mm=ap.z_mm, y_mm=ap.y_mm, z_mm=ap.x_mm,
        length_mm=ap.height_mm, width_mm=ap.width_mm, height_mm=ap.length_mm,
        section_name=ap.section_name,
        section_type=ap.section_type,
        web_dx=ap.web_dz, web_dy=ap.web_dy, web_dz=ap.web_dx,
    )


def _remap_part_wall_lying(bp: BodyPart) -> BodyPart:
    """독립 wall 패널의 *눕히기* 매핑 재적용.

    normal 매핑(운송 X = nominal Y, Y = nominal X) 으로 만든 BodyPart 를
    wall-lying 매핑(운송 X = nominal X[벽 길이], Y = nominal Z[높이],
    Z = nominal Y[두께]) 으로 재매핑.

    [도출]
    normal: x_t = ny_min, y_t = nx_min, z_t = nz_min
            L_t = ny_size, W_t = nx_size, H_t = nz_size
    wall:   x_t = nx_min = normal.y_t
            y_t = nz_min = normal.z_t
            z_t = ny_min = normal.x_t
            L_t = nx_size = normal.W_t
            W_t = nz_size = normal.H_t
            H_t = ny_size = normal.L_t
    """
    return BodyPart(
        kind=bp.kind, subkind=bp.subkind,
        x_mm=bp.y_mm, y_mm=bp.z_mm, z_mm=bp.x_mm,
        length_mm=bp.width_mm, width_mm=bp.height_mm, height_mm=bp.length_mm,
        section_type=bp.section_type,
        # 좌표 순열(x←y, y←z, z←x)과 동일하게 웨브 방향도 순열.
        web_dx=bp.web_dy, web_dy=bp.web_dz, web_dz=bp.web_dx,
    )


def _collect_attached_parts(
    parent_comp: Component, scene: Scene, model, design_result,
    options: TransportOptions,
    *, include_mid: bool, include_cantilever: bool,
) -> Tuple[AttachedPart, ...]:
    """부모에 매달린 종속 부재(중간보·중간기둥·캔틸 보·캔틸 슬래브) 수집.

    부모 회전·앵커가 어떻게 변해도 nominal 좌표로 변환해서 첨부 → 종속
    위치 어긋남 없음.
    """
    parts: List[AttachedPart] = []
    parent_cid = parent_comp.id
    for cid, comp in scene.components.items():
        if getattr(comp, "parent_id", 0) != parent_cid:
            continue
        is_mid = isinstance(comp, (MidBeam, MidColumn))
        is_cant = isinstance(comp, (CantileverBeam, CantileverSlab))
        if not (is_mid or is_cant):
            continue
        if is_mid and not include_mid:
            continue
        if is_cant and not include_cantilever:
            continue
        # 자식 컴포넌트의 *모든 부재* 를 풀어 AttachedPart 다중 생성
        parts.extend(_collect_child_parts(comp, parent_comp, cid, model, design_result))
    return tuple(parts)


# ── 3축 정규화 — 모든 부재 좌표 최소점이 0 이 되도록 평행이동 ───
# [근거]
# 배치 모델은 기둥 base z = -half_s(=-100mm), x/y 도 캔틸 돌출 시 음수 가능.
# 운송 시 컴포넌트 좌하단이 *정확히 (0,0,0)* 이어야 packer 의 _compute_xyz 가
# 정확한 트럭 좌표를 산출. 그렇지 않으면 캔틸이 트럭 적재함 밖으로 튀어나감.
def _z_normalize_parts(
    body: Tuple[BodyPart, ...], attached: Tuple[AttachedPart, ...],
) -> Tuple[Tuple[BodyPart, ...], Tuple[AttachedPart, ...]]:
    """모든 부재의 x/y/z 최저점이 0 이 되도록 *3 축* 평행이동.

    [중요] 함수명이 _z_ 지만 실제로는 *3 축 모두* 정규화. 기존 호출처와의
    호환을 위해 이름은 유지. v3-lying / 캔틸 돌출로 인한 x/y 음수도 처리.
    """
    all_x: List[float] = []
    all_y: List[float] = []
    all_z: List[float] = []
    for bp in body:
        all_x.append(bp.x_mm); all_y.append(bp.y_mm); all_z.append(bp.z_mm)
    for ap in attached:
        all_x.append(ap.x_mm); all_y.append(ap.y_mm); all_z.append(ap.z_mm)
    if not all_x:
        return body, attached
    x_min = min(all_x); y_min = min(all_y); z_min = min(all_z)
    sx = -x_min if x_min < -1e-3 else 0.0
    sy = -y_min if y_min < -1e-3 else 0.0
    sz = -z_min if z_min < -1e-3 else 0.0
    if sx == 0.0 and sy == 0.0 and sz == 0.0:
        return body, attached
    new_body = tuple(
        BodyPart(kind=bp.kind, subkind=bp.subkind,
                 x_mm=bp.x_mm + sx, y_mm=bp.y_mm + sy, z_mm=bp.z_mm + sz,
                 length_mm=bp.length_mm, width_mm=bp.width_mm,
                 height_mm=bp.height_mm,
                 # H형강 단면·웨브 방향 보존 (평행이동은 형상·방향 불변).
                 section_type=bp.section_type,
                 web_dx=bp.web_dx, web_dy=bp.web_dy, web_dz=bp.web_dz)
        for bp in body
    )
    new_attached = tuple(
        AttachedPart(kind=ap.kind, source_kind=ap.source_kind, subkind=ap.subkind,
                     x_mm=ap.x_mm + sx, y_mm=ap.y_mm + sy, z_mm=ap.z_mm + sz,
                     length_mm=ap.length_mm, width_mm=ap.width_mm,
                     height_mm=ap.height_mm,
                     section_name=ap.section_name,
                     section_type=ap.section_type,
                     web_dx=ap.web_dx, web_dy=ap.web_dy, web_dz=ap.web_dz)
        for ap in attached
    )
    return new_body, new_attached


def _outer_xy_dims(
    body: Tuple[BodyPart, ...], attached: Tuple[AttachedPart, ...],
) -> Tuple[float, float]:
    """모든 부재의 외곽 길이/폭 (x 방향 최대, y 방향 최대).

    *_z_normalize_parts 호출 후* 에 사용 — 모든 부재 x/y 최소가 0 이므로
    외곽 차원 = 최대값 그대로.

    [용도] 컴포넌트 nominal length/width 를 *부모 nominal* 대신 *부모+종속
    외곽* 으로 확장. _compute_xyz 가 외곽 차원 기준으로 자리 잡음.
    """
    max_x = 0.0; max_y = 0.0
    for bp in body:
        max_x = max(max_x, bp.x_mm + bp.length_mm)
        max_y = max(max_y, bp.y_mm + bp.width_mm)
    for ap in attached:
        max_x = max(max_x, ap.x_mm + ap.length_mm)
        max_y = max(max_y, ap.y_mm + ap.width_mm)
    return max_x, max_y


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
    # [2026-06-04] 단면이 혼재해도 *부재마다 제 단면×길이로* 강재 무게를 정확히
    #   합산한다(과거: 가장 무거운 단면 1종으로 통일 → 과대 + 경고). 대표 단면
    #   (col_pick/beam_pick)은 시각화·표시용으로만 남긴다.
    # 부재 길이 조회 가능 여부 — 분석 모델은 get_member_length 제공. 길이를 못
    #   구하면(테스트 stub 등) 정확 합산 대신 대표 단면 근사식으로 폴백한다.
    _has_len = hasattr(model, "get_member_length")
    col_secs: List[SHSSection] = []
    beam_secs: List[SHSSection] = []
    col_steel_kg = 0.0
    beam_steel_kg = 0.0
    for mid in model.comp_to_members.get(cid, []):
        m = model.members.get(mid)
        if m is None:
            continue
        kind = getattr(m, "kind", None) or getattr(m, "member_type", None)
        sec = lookup_section_for_member(mid, design_result)
        if sec is None:
            continue
        # 부재 실제 길이(mm→m) × 단면 단위중량(kg/m) = 그 부재의 강재 무게.
        if _has_len:
            len_m = float(model.get_member_length(mid)) / 1000.0
            steel_kg = to_transport_section(sec).weight_per_m * len_m
        else:
            steel_kg = 0.0
        if kind == "column":
            col_secs.append(sec)
            col_steel_kg += steel_kg
        elif kind == "beam":
            beam_secs.append(sec)
            beam_steel_kg += steel_kg
    col_pick = _pick_heaviest(col_secs)
    beam_pick = _pick_heaviest(beam_secs)
    if col_pick is None or beam_pick is None:
        diag.warnings.append(f"{label}: 기둥/보 단면 룩업 실패 — 운송 대상 제외")
        return None
    # 길이를 구할 수 있을 때만 정확 프레임 무게 채택, 아니면 None(근사식 폴백).
    frame_weight_kg = (col_steel_kg + beam_steel_kg) if _has_len else None

    # extra_weight
    area_m2 = (w / 1000.0) * (d / 1000.0)
    extra = _slab_self_weight_kg(area_m2, options)
    if face_classes:
        extra += _sum_4_face_nonbearing_weight(comp, face_classes, options)
    extra += _sum_mid_member_weight_in_module(cid, scene, model, design_result)
    extra += _sum_cantilever_weight_attached_to(cid, scene, model, design_result, options)

    # Phase 3-fix + 캔틸 돌출 보정 — 실측 부재 + 3축 정규화 + 외곽 차원 확장
    body = _collect_module_body_parts(comp)
    # 모듈에 종속된 내벽(칸막이) 을 시각화 전용으로 body 에 추가(운송 패킹 무영향).
    body = body + _collect_interior_wall_parts(comp, cid, scene)
    attached = _collect_attached_parts(
        comp, scene, model, design_result, options,
        include_mid=True, include_cantilever=options.include_cantilever,
    )
    body, attached = _z_normalize_parts(body, attached)
    # 컴포넌트 nominal length/width 를 *부모+종속 외곽* 으로 확장
    # (캔틸이 부모 밖으로 돌출하는 경우 외곽이 부모 nominal 보다 큼)
    outer_L, outer_W = _outer_xy_dims(body, attached)
    final_length = max(d, outer_L)
    final_width = max(w, outer_W)

    return TModule(
        name=label,
        width=final_width, length=final_length, height=h,
        column_section=to_transport_section(col_pick),
        beam_section=to_transport_section(beam_pick),
        extra_weight_kg=extra,
        attached_parts=attached,
        body_parts=body,
        frame_weight_kg=frame_weight_kg,
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

    # [2026-06-04] 일반 모듈과 동일 — 부재별 단면×길이로 정확 강재 무게 합산
    #   (3 층치 기둥·보 모두). 대표 단면은 시각화·표시용으로만 유지.
    _has_len = hasattr(model, "get_member_length")
    col_secs: List[SHSSection] = []
    beam_secs: List[SHSSection] = []
    col_steel_kg = 0.0
    beam_steel_kg = 0.0
    for mid in model.comp_to_members.get(cid, []):
        m = model.members.get(mid)
        if m is None:
            continue
        kind = getattr(m, "kind", None) or getattr(m, "member_type", None)
        sec = lookup_section_for_member(mid, design_result)
        if sec is None:
            continue
        if _has_len:
            len_m = float(model.get_member_length(mid)) / 1000.0
            steel_kg = to_transport_section(sec).weight_per_m * len_m
        else:
            steel_kg = 0.0
        if kind == "column":
            col_secs.append(sec)
            col_steel_kg += steel_kg
        elif kind == "beam":
            beam_secs.append(sec)
            beam_steel_kg += steel_kg
    col_pick = _pick_heaviest(col_secs)
    beam_pick = _pick_heaviest(beam_secs)
    if col_pick is None or beam_pick is None:
        diag.warnings.append(f"{label}: 수직3모듈 단면 룩업 실패 — 운송 대상 제외")
        return None
    frame_weight_kg = (col_steel_kg + beam_steel_kg) if _has_len else None

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

    # Phase 3-fix — 실측 부재 수집 + v3-lying 매핑 추가 회전
    # 수직모듈에 종속된 내벽(칸막이)도 시각화 전용으로 포함(눕히기 remap 까지 함께 받음).
    body_raw = _collect_v3_body_parts(comp) + _collect_interior_wall_parts(comp, cid, scene)
    attached = _collect_attached_parts(
        comp, scene, model, design_result, options,
        include_mid=True, include_cantilever=options.include_cantilever,
    )

    if options.treat_v3_module_as_lying:
        body = tuple(_remap_part_v3_lying(bp) for bp in body_raw if bp is not None)
        attached = tuple(_remap_attached_v3_lying(ap) for ap in attached if ap is not None)
        body, attached = _z_normalize_parts(body, attached)
        outer_L, outer_W = _outer_xy_dims(body, attached)
        return TModule(
            name=label + "(눕힘)",
            width=max(w, outer_W), length=max(h, outer_L), height=d,
            column_section=to_transport_section(col_pick),
            beam_section=to_transport_section(beam_pick),
            extra_weight_kg=extra,
            attached_parts=attached,
            body_parts=body,
            frame_weight_kg=frame_weight_kg,
        )
    else:
        body, attached = _z_normalize_parts(body_raw, attached)
        outer_L, outer_W = _outer_xy_dims(body, attached)
        return TModule(
            name=label,
            width=max(w, outer_W), length=max(d, outer_L), height=h,
            column_section=to_transport_section(col_pick),
            beam_section=to_transport_section(beam_pick),
            extra_weight_kg=extra,
            attached_parts=attached,
            body_parts=body,
            frame_weight_kg=frame_weight_kg,
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
    fp: FloorPanel, cid: int, scene: Scene, model, design_result,
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
    # Phase 3-fix + 캔틸 보정 — 실측 부재 + 3축 정규화 + 외곽 차원 확장
    body = _collect_floor_body_parts(fp)
    attached = _collect_attached_parts(
        fp, scene, model, design_result, options,
        include_mid=False, include_cantilever=options.include_cantilever,
    )
    body, attached = _z_normalize_parts(body, attached)
    outer_L, outer_W = _outer_xy_dims(body, attached)
    extra += _sum_cantilever_weight_attached_to(cid, scene, model, design_result, options)
    return TPanel(
        name=label, kind="floor",
        width=max(w, outer_W), length=max(d, outer_L),
        thickness=options.floor_slab_thickness_mm,
        beam_section=to_transport_section(beam_pick),
        extra_weight_kg=extra,
        attached_parts=attached,
        body_parts=body,
        floor_index=getattr(fp, "floor_index", -1),
        group_id=getattr(fp, "group_id", -1),
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
    # Phase 3-fix — 실측 부재 + 종속 캔틸 첨부
    body = _collect_floor_body_parts(fp)
    attached = _collect_attached_parts(
        fp, scene, model, design_result, options,
        include_mid=False, include_cantilever=options.include_cantilever,
    )
    body, attached = _z_normalize_parts(body, attached)
    outer_L, outer_W = _outer_xy_dims(body, attached)
    extra += _sum_cantilever_weight_attached_to(cid, scene, model, design_result, options)

    return TPanel(
        name=label, kind="floor",
        width=max(w, outer_W), length=max(d, outer_L),
        thickness=options.floor_slab_thickness_mm,
        beam_section=to_transport_section(beam_pick),
        extra_weight_kg=extra,
        wall_segments=tuple(segments),
        attached_parts=attached,
        body_parts=body,
        floor_index=getattr(fp, "floor_index", -1),
        group_id=getattr(fp, "group_id", -1),
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

    body_raw = _collect_wall_body_parts(wall)
    if options.treat_wall_as_lying:
        # 운송: 눕혀 적재 — width=h(층고), length=w(벽 길이), thickness=d.
        # body_parts 는 normal 매핑으로 만든 뒤 wall-lying 회전 재매핑.
        body = tuple(_remap_part_wall_lying(bp) for bp in body_raw)
        body, _ = _z_normalize_parts(body, ())
        return TPanel(
            name=label, kind="wall",
            width=h, length=w, thickness=d,
            beam_section=to_transport_section(beam_pick),
            column_section=to_transport_section(col_pick),
            wall_height=h,
            extra_weight_kg=extra,
            body_parts=body,
            floor_index=getattr(wall, "floor_index", -1),
            group_id=getattr(wall, "group_id", -1),
        )
    # 물량: 세워서 표시 — normal 매핑(높이축이 연직 Z) 그대로.
    # [함정] 외곽 박스 차원을 (w,d) 로 하드코딩하면 벽 방향(전후벽/좌우벽)에 따라
    # body 의 운송 x·y 가 바뀌어 박스와 90도 어긋난다. body 실측 외곽
    # (_outer_xy_dims)으로 length·width 를 잡아 벽 방향과 무관하게 정합시킨다.
    body, _ = _z_normalize_parts(body_raw, ())
    outer_L, outer_W = _outer_xy_dims(body, ())
    return TPanel(
        name=label, kind="wall",
        width=outer_W, length=outer_L, thickness=h,
        beam_section=to_transport_section(beam_pick),
        column_section=to_transport_section(col_pick),
        wall_height=h,
        extra_weight_kg=extra,
        body_parts=body,
        floor_index=getattr(wall, "floor_index", -1),
        group_id=getattr(wall, "group_id", -1),
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

        # 내벽(비내력 칸막이) — 렌더링 전용. 운송 점검 대상이 아니므로 제외.
        # (분기에서 빠지면 '알 수 없는 타입' 경고가 떠 운송 확인 팝업을 채운다.)
        if isinstance(comp, InteriorWall):
            ti.excluded.append(ExcludedItem(cid=cid, type_name="내벽",
                                            reason="비내력 칸막이(운송 대상 아님)"))
            continue

        # Mid* — 부모 모듈 extra_weight 에 흡수됨 (위에서 합산)
        if isinstance(comp, (MidBeam, MidColumn)):
            ti.excluded.append(ExcludedItem(cid=cid, type_name="모듈 내부 보강재",
                                            reason="부모 모듈에 흡수"))
            continue

        # 캔틸레버 — Phase 1 이후 항상 부모 화물에 기하학적 종속.
        # 별도 회차로 빠지지 않으며, 자중·형상은 부모 변환 시 흡수됨.
        if isinstance(comp, (CantileverBeam, CantileverSlab)):
            ti.excluded.append(ExcludedItem(cid=cid, type_name="캔틸레버",
                                            reason="부모 화물에 기하 종속(같은 회차 운송)"))
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
                tp = convert_floor_panel_pure(comp, cid, scene, model, design_result,
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

    # [Phase A — 2026-05-26] 좌표 자동 정렬 — 입력 length 가 width 보다 짧으면 swap.
    # 모듈 / 순수 floor / 종속 floor 적용. 단순 wall (kind="wall") 은 *벽 길이/높이*
    # 물리 의미 보존 위해 제외.
    ti.modules = [_normalize_dims_for_transport(m) for m in ti.modules]
    ti.panels = [_normalize_dims_for_transport(p) for p in ti.panels]

    ti.diagnostics = diag

    # [2026-06-03 진단] 모듈이 운송에서 통째로 빠지는 버그 추적용.
    #   씬의 모듈/수직3모듈 컴포넌트 중 코어·내부보강재가 아닌 "운송 대상" 개수와
    #   실제 추출된 ti.modules 개수를 비교해, 모자라면(=convert_module 이 단면
    #   룩업 실패로 None 반환) 콘솔에 사유를 경고로 남긴다. 정상이면 아무 출력 없음.
    #   원인 1순위: 운송에 넘긴 model 의 부재 id 가 design_result(다른 빌드 기준)와
    #   어긋나 lookup_section_for_member 가 전부 실패 → 모듈 0 회차.
    try:
        from modular_3d._utils.debug import log_warn
        excluded_cids = {e.cid for e in ti.excluded}
        mod_targets = [c for cid, c in scene.components.items()
                       if isinstance(c, (SceneModule, Vertical3Module))
                       and cid not in excluded_cids]
        dropped = len(mod_targets) - len(ti.modules)
        if dropped > 0:
            n_mtg = len(getattr(design_result, 'member_to_group', {}) or {})
            sec_fail = sum(1 for w in diag.warnings if '단면 룩업 실패' in w)
            log_warn(
                f"[운송] 모듈 {dropped}/{len(mod_targets)}개가 운송에서 누락됨 "
                f"(단면룩업실패 경고 {sec_fail}건, design_result.member_to_group "
                f"{n_mtg}개). model 과 design_result 의 부재 id 불일치 가능 — "
                f"운송에 넘긴 모델이 설계결과와 같은 빌드인지 확인 필요.",
                cat='transport')
    except Exception:
        pass

    return ti


# ── Phase A — 좌표 자동 정렬 헬퍼 ─────────────────────────────────
def _normalize_dims_for_transport(
    item: Union[TModule, TPanel],
) -> Union[TModule, TPanel]:
    """모듈/패널의 length 가 width 보다 작은 경우 swap (긴 쪽 = length 정규화).

    [정책 — 운송 3D 도식 개편 계획서 § 1 Q1~Q4]
    - 모듈 / 순수 floor / 종속 floor: swap 적용
    - 단순 wall (kind="wall"): swap 제외 (length=벽 길이, width=벽 높이 물리 의미 보존)
    - 종속 floor 의 wall_segments.side 인덱스: swap 발생 시 +1 (mod 4) 회전 매핑.
      세그먼트 자체 치수 (length_mm, start_offset_mm, height_mm, thickness_mm) 는
      절대값이므로 그대로 유지.
    - 이미 정규화된 경우 (length ≥ width) 변화 없음 (idempotent).

    [side 회전 매핑 유도]
    원본 좌표 (x=length 방향, y=width 방향) 가 swap 후 (x'=새 length=old width 방향,
    y'=새 width=old length 방향) 로 반시계 90° 회전:
      0 (하변, y=0)        → 1 (우변, x=새 length)
      1 (우변, x=length)   → 2 (상변, y=새 width)
      2 (상변, y=width)    → 3 (좌변, x=0)
      3 (좌변, x=0)        → 0 (하변, y=0)
    side_new = (side_old + 1) % 4.
    """
    # 단순 wall 은 제외 — width=벽 높이 의미 보존
    if isinstance(item, TPanel) and item.kind == "wall":
        return item

    if item.length + 1e-6 >= item.width:
        return item  # 이미 정규화됨

    if isinstance(item, TModule):
        return TModule(
            name=item.name,
            width=item.length,   # 새 width = 원래 length (짧은 쪽)
            length=item.width,   # 새 length = 원래 width (긴 쪽)
            height=item.height,
            column_section=item.column_section,
            beam_section=item.beam_section,
            extra_weight_kg=item.extra_weight_kg,
        )

    # TPanel — 종속 floor 의 wall_segments side 회전 매핑 포함
    new_segments = tuple(
        WallSegment(
            side=(seg.side + 1) % 4,
            start_offset_mm=seg.start_offset_mm,
            length_mm=seg.length_mm,
            height_mm=seg.height_mm,
            thickness_mm=seg.thickness_mm,
            column_section=seg.column_section,
            beam_section=seg.beam_section,
        )
        for seg in item.wall_segments
    )
    return TPanel(
        name=item.name,
        kind=item.kind,
        width=item.length,
        length=item.width,
        thickness=item.thickness,
        beam_section=item.beam_section,
        column_section=item.column_section,
        wall_height=item.wall_height,
        extra_weight_kg=item.extra_weight_kg,
        wall_segments=new_segments,
        # [함정] swap 재생성 시 층 식별자 누락하면 -1 로 리셋돼 묶음 복제가 깨진다.
        floor_index=item.floor_index,
        group_id=item.group_id,
    )


__all__ = [
    "TransportOptions", "TransportInput", "TransportError",
    "ExcludedItem", "Diagnostics",
    "to_transport_section", "lookup_section_for_member",
    "build_comp_meta", "build_transport_input",
    "convert_module", "convert_vertical3_lying",
    "convert_floor_panel_pure", "convert_floor_panel_dependent",
    "convert_independent_wall_panel",
    "_normalize_dims_for_transport",  # Phase A — 테스트용 export
]
