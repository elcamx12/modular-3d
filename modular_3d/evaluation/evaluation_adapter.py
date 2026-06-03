"""평가 데이터 수집 어댑터.

[설계 근거]
- 평가탭_구축_계획서.md §1, Phase K + Phase P 수정.
- 입력:
    - scene components (배치 데이터)
    - ProjectSettings
    - quantity_reports: dict[policy → QuantityReport] (analysis_panel._quantity_reports)
    - current_policy: 사용자가 물량 탭에서 선택한 정책 (analysis_panel._current_policy)
    - transport_pack: PackResult (회차·평균 적재)
    - transport_eco: EconomicsResult (총 운송비)
    - schedule_payload: 공정표 calc() 의 QWebChannel 푸시 결과
- 출력: 평가 화면이 그대로 표시하는 dict.

[정책]
- 자재비는 quantity_takeoff.compute_material_cost 로 매번 산출 (analysis_panel 과 동일 로직).
- 1종/2종/3종 정책 선택은 *물량 탭의 _current_policy* 한 곳에서만 결정.
  평가 탭은 그 값을 그대로 가져와 표시.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from ..model.core import (
    Component,
    ComponentType,
    Module,
    Vertical3Module,
    FloorPanel,
    StructWall,
    CoreSlab,
    Core,
    CantileverBeam,
    CantileverSlab,
    MidBeam,
    MidColumn,
    InteriorWall,
)
try:
    # 모듈 비내력 외벽 두께 — 카탈로그 상수가 있으면 사용, 없으면 100mm 가정.
    from ..카탈로그.geometry import NONBEARING_WALL_THICKNESS_MM as _NB_WALL_T
except Exception:
    _NB_WALL_T = 100.0


# ── 좌표 보조 ─────────────────────────────────────────────
def _aabb(comp: Component) -> Tuple[float, float, float, float, float, float]:
    try:
        verts = comp.get_world_corners()
        if verts is None or len(verts) == 0:
            raise ValueError("no corners")
        return (
            float(verts[:, 0].min()), float(verts[:, 1].min()), float(verts[:, 2].min()),
            float(verts[:, 0].max()), float(verts[:, 1].max()), float(verts[:, 2].max()),
        )
    except Exception:
        x = float(comp.position[0]); y = float(comp.position[1]); z = float(comp.position[2])
        w = float(comp.dimensions.get("width", 0.0))
        d = float(comp.dimensions.get("depth", 0.0))
        h = float(comp.dimensions.get("height", 0.0))
        return x, y, z, x + w, y + d, z + h


# ── A. 건물 개요 ──────────────────────────────────────────
_MODULE_FLOOR_HEIGHT_MM = 3000.0


def _module_floor_span(comp: Component) -> Tuple[int, int]:
    f0 = int(getattr(comp, "floor_index", 0))
    if isinstance(comp, Vertical3Module):
        h = float(comp.dimensions.get("height", 0.0))
        k = max(1, int(round(h / _MODULE_FLOOR_HEIGHT_MM)))
        return f0, f0 + k - 1
    return f0, f0


def _build_headline(comps: List[Component], project_settings: Optional[Any]) -> Dict[str, Any]:
    modules = [c for c in comps if isinstance(c, (Module, Vertical3Module))]
    basement = 0
    if project_settings is not None:
        basement = int(getattr(project_settings, "basement_floors", 0) or 0)
    if not modules:
        return {
            "floors_above_ground": 0,
            "basement_floors": basement,
            "modules_total": 0,
            "footprint_m2": 0.0,
            "total_floor_area_m2": 0.0,
        }
    spans = [_module_floor_span(c) for c in modules]
    floors_above = max(s[1] for s in spans) + 1
    floor0_present = [c for c, s in zip(modules, spans) if s[0] <= 0 <= s[1]]

    footprint_mm2 = 0.0
    for c in floor0_present:
        xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(c)
        footprint_mm2 += (xmax - xmin) * (ymax - ymin)
    for c in comps:
        if isinstance(c, FloorPanel) and int(getattr(c, "floor_index", 0)) == 0:
            xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(c)
            footprint_mm2 += (xmax - xmin) * (ymax - ymin)
    footprint_m2 = footprint_mm2 / 1.0e6

    total_mm2_per_floor: Dict[int, float] = {}
    for c in modules:
        s0, s1 = _module_floor_span(c)
        xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(c)
        area = (xmax - xmin) * (ymax - ymin)
        for f in range(s0, s1 + 1):
            total_mm2_per_floor[f] = total_mm2_per_floor.get(f, 0.0) + area
    for c in comps:
        if isinstance(c, FloorPanel):
            f = int(getattr(c, "floor_index", 0))
            xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(c)
            total_mm2_per_floor[f] = total_mm2_per_floor.get(f, 0.0) + (xmax - xmin) * (ymax - ymin)
    total_floor_area_m2 = sum(total_mm2_per_floor.values()) / 1.0e6

    eff = _compute_floor1_effective_area(comps)

    return {
        "floors_above_ground": int(floors_above),
        "basement_floors": basement,
        "modules_total": int(len(modules)),
        "footprint_m2": round(footprint_m2, 1),
        "total_floor_area_m2": round(total_floor_area_m2, 1),
        "effective_area_floor1": eff,
    }


# ── A-2. 1F 유효면적 비율 ──────────────────────────────────
def _compute_floor1_effective_area(comps: List[Component]) -> Dict[str, float]:
    """1F 기준 (사용 가능한 면적) / (한 층 전체 면적) 비율.

    [정의 — 사용자 합의 2026-05-31]
    - 전체 면적(gross): 1F 모듈 외곽 + 코어 슬래브 + 캔틸레버 슬래브 AABB 합.
      → 1층에서 사람이 밟고 다닐 수 있는 모든 바닥의 총합.
    - 빼는 면적: 위에서 봤을 때 기둥·벽이 차지하는 평면 면적.
      - 모듈 코너 기둥 4 개 × (section_w × section_h)
      - 모듈 외벽 4면 (둘레 × 비내력 벽 두께) — 모서리 중복은 4·t² 빼서 보정
      - 1F 의 StructWall / InteriorWall / Core(코어벽): width × depth (위에서 본 면적)
      - 1F 의 MidColumn: section_w × section_h
    - 사용 가능 면적(net) = gross − 위 합. ratio = net / gross.

    중간보·캔틸레버보 등 "보" 부재는 위에서 본 점유 면적이 작고 본질적으로
    천장에 매달리는 부재라 빼지 않음(거주자 입장에서 머리 위 통과).
    """
    floor = 0  # 1F 만

    gross_mm2 = 0.0

    # 1F 를 지나는 모듈 모으기
    floor1_modules: List[Component] = []
    for c in comps:
        if isinstance(c, (Module, Vertical3Module)):
            s0, s1 = _module_floor_span(c)
            if s0 <= floor <= s1:
                floor1_modules.append(c)
                xmin, ymin, _, xmax, ymax, _ = _aabb(c)
                gross_mm2 += (xmax - xmin) * (ymax - ymin)

    # 1F 코어 슬래브 + 캔틸레버 슬래브 (1F 위에 놓이는 바닥)
    for c in comps:
        if not isinstance(c, (CoreSlab, CantileverSlab)):
            continue
        f = int(getattr(c, "floor_index", 0))
        if f != floor:
            continue
        xmin, ymin, _, xmax, ymax, _ = _aabb(c)
        gross_mm2 += (xmax - xmin) * (ymax - ymin)

    if gross_mm2 <= 0.0:
        return {"gross_m2": 0.0, "net_m2": 0.0, "ratio_pct": 0.0}

    sub_mm2 = 0.0

    # 모듈: 코너 기둥 4개 + 외벽 4면 둘레
    for m in floor1_modules:
        w = float(m.dimensions.get("width", 0.0))
        d = float(m.dimensions.get("depth", 0.0))
        cols = getattr(m, "columns", []) or []
        for col in cols:
            sw = float(getattr(col, "section_w", 200.0) or 200.0)
            sh = float(getattr(col, "section_h", 200.0) or 200.0)
            sub_mm2 += sw * sh
        # 4 면 외벽 (둘레 × 두께) − 모서리 4 개 중복 t²
        t = float(_NB_WALL_T)
        perimeter = 2.0 * (w + d)
        sub_mm2 += perimeter * t - 4.0 * t * t

    # 1F 의 벽패널 / 내벽 / 코어벽 / 중간기둥
    for c in comps:
        f = int(getattr(c, "floor_index", 0))
        if f != floor:
            continue
        if isinstance(c, StructWall):
            w = float(c.dimensions.get("width", 0.0))
            d = float(c.dimensions.get("depth", 200.0))
            sub_mm2 += w * d
        elif isinstance(c, InteriorWall):
            w = float(c.dimensions.get("width", 0.0))
            d = float(c.dimensions.get("depth", _NB_WALL_T))
            sub_mm2 += w * d
        elif isinstance(c, Core):
            w = float(c.dimensions.get("width", 0.0))
            d = float(c.dimensions.get("depth", 300.0))
            sub_mm2 += w * d
        elif isinstance(c, MidColumn):
            col = getattr(c, "column", None)
            if col is not None:
                sw = float(getattr(col, "section_w", 200.0) or 200.0)
                sh = float(getattr(col, "section_h", 200.0) or 200.0)
                sub_mm2 += sw * sh

    net_mm2 = max(0.0, gross_mm2 - sub_mm2)
    return {
        "gross_m2": round(gross_mm2 / 1.0e6, 2),
        "net_m2":   round(net_mm2 / 1.0e6, 2),
        "ratio_pct": round(net_mm2 / gross_mm2 * 100.0, 1),
    }


# ── B-1. 단면 설계 탭의 타입 목록을 그대로 사용 ────────────
def _build_members_from_section_types(
    section_types: List[Dict[str, Any]],
    scene_components_by_id: Dict[int, Any],
    comps: List[Component],
) -> Dict[str, Any]:
    """단면 설계 탭이 제공한 types 리스트를 부재 구성으로 그대로 변환.

    [입력] types 항목 예:
        {label, rep_comp_id, count, summary, ok, max_ratio, floor_counts}
    [출력] 기존 _build_members 와 같은 키 구조 — 종합탭/비교탭이 그대로 표시.
    """
    modules_by_type: List[Dict[str, Any]] = []
    panels_by_type:  List[Dict[str, Any]] = []

    for t in section_types or []:
        label = str(t.get("label", "?"))
        cnt   = int(t.get("count", 0) or 0)
        rep_id = t.get("rep_comp_id", -1)
        rep = scene_components_by_id.get(int(rep_id)) if rep_id is not None else None
        if rep is None:
            # 대표 컴포넌트 못 찾으면 라벨만 띄움.
            modules_by_type.append({
                "name": label, "width_m": 0.0, "length_m": 0.0,
                "count": cnt, "area_m2": 0.0,
                "sections": str(t.get("summary", "") or "—"),
            })
            continue
        xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(rep)
        a, b = sorted([round((xmax - xmin) / 1000.0, 2),
                       round((ymax - ymin) / 1000.0, 2)])
        if isinstance(rep, (Module, Vertical3Module)):
            secs = _collect_module_sections(rep)
            split = _collect_module_sections_split(rep)
            modules_by_type.append({
                "name": label,
                "width_m": a,
                "length_m": b,
                "count": cnt,
                "area_m2": round(a * b, 2),
                "sections": ", ".join(secs) if secs else
                            (str(t.get("summary", "") or "—")),
                **split,
            })
        elif isinstance(rep, (FloorPanel, StructWall, InteriorWall)):
            secs = _collect_panel_sections(rep)
            cls_label = ("바닥패널" if isinstance(rep, FloorPanel)
                         else "벽패널" if isinstance(rep, StructWall)
                         else "내벽")
            panels_by_type.append({
                "class_label": label or cls_label,
                "width_m": a,
                "depth_m": b,
                "count": cnt,
                "sections": ", ".join(secs) if secs else
                            (str(t.get("summary", "") or "—")),
            })
        # CoreSlab 등은 패널 목록에서 제외.

    # 카운트(요약 카드)는 컴포넌트에서 직접 산출 — types 가 누락한 부분 대비.
    panels = {
        "floor_panel":   sum(1 for c in comps if isinstance(c, FloorPanel)),
        "struct_wall":   sum(1 for c in comps if isinstance(c, StructWall)),
        "interior_wall": sum(1 for c in comps if isinstance(c, InteriorWall)),
    }
    attached = {
        "cantilever_beam": sum(1 for c in comps if isinstance(c, CantileverBeam)),
        "cantilever_slab": sum(1 for c in comps if isinstance(c, CantileverSlab)),
        "mid_beam":        sum(1 for c in comps if isinstance(c, MidBeam)),
        "mid_column":      sum(1 for c in comps if isinstance(c, MidColumn)),
    }
    return {
        "modules_by_type": modules_by_type,
        "panels": panels,
        "panels_by_type": panels_by_type,
        "attached": attached,
    }


# ── B. 부재 구성 (단면 설계 결과가 없을 때 폴백) ───────────
def _build_members(comps: List[Component]) -> Dict[str, Any]:
    """모듈·패널을 *외곽 치수 + 강재 시그니처* 두 단계로 그룹화.

    [2026-05-31 옵션 B]
    - 1차 키: 외곽 (a_mm, b_mm). 외곽이 같은 모듈끼리 같은 알파벳(A, B, …) 부여.
    - 2차 키: 강재 단면 시그니처 tuple (기둥·보 라벨 모음).
      같은 외곽 안에서 강재가 다르면 'A-1타입', 'A-2타입' 변형 번호 부여.
    - 변형이 1 개뿐이면 그냥 'A타입' (변형 번호 없음).
    """
    # 1) 모듈을 (외곽, 강재 시그니처) 키로 카운트. 분리 단면도 캐싱.
    module_buckets: Dict[Tuple[int, int, Tuple[str, ...]], int] = {}
    split_cache: Dict[Tuple[int, int, Tuple[str, ...]], Dict[str, str]] = {}
    for c in comps:
        if not isinstance(c, (Module, Vertical3Module)):
            continue
        xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(c)
        a, b = sorted([round((xmax - xmin) / 1000.0, 2), round((ymax - ymin) / 1000.0, 2)])
        a_mm = int(a * 1000); b_mm = int(b * 1000)
        secs = tuple(_collect_module_sections(c))
        key = (a_mm, b_mm, secs)
        module_buckets[key] = module_buckets.get(key, 0) + 1
        if key not in split_cache:
            split_cache[key] = _collect_module_sections_split(c)

    # 2) 외곽별로 변형 그룹핑 → 알파벳·변형 번호 부여.
    outer_to_variants: Dict[Tuple[int, int],
                             List[Tuple[Tuple[str, ...], int]]] = {}
    for (a_mm, b_mm, secs), n in module_buckets.items():
        outer_to_variants.setdefault((a_mm, b_mm), []).append((secs, n))
    # 외곽 그룹 정렬: 총 개수 많은 외곽 먼저.
    outer_sorted = sorted(
        outer_to_variants.items(),
        key=lambda kv: (-sum(n for _, n in kv[1]), kv[0]),
    )
    modules_by_type: List[Dict[str, Any]] = []
    name_iter = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for (a_mm, b_mm), variants in outer_sorted:
        alpha = next(name_iter, "X")
        # 변형 내부 정렬: 개수 많은 변형 먼저.
        variants.sort(key=lambda v: (-v[1], v[0]))
        single = (len(variants) == 1)
        for vi, (secs, n) in enumerate(variants, 1):
            type_name = f"{alpha}타입" if single else f"{alpha}-{vi}타입"
            split = split_cache.get((a_mm, b_mm, secs), {
                "columns_section": "—",
                "top_beams_section": "—",
                "bottom_beams_section": "—",
            })
            modules_by_type.append({
                "name": type_name,
                "width_m": a_mm / 1000.0,
                "length_m": b_mm / 1000.0,
                "count": int(n),
                "area_m2": round(a_mm * b_mm / 1.0e6, 2),
                "sections": ", ".join(secs) if secs else "—",
                **split,
            })

    # [2026-06-01] 코어슬래브는 패널이 아님 — 카운트·타입 목록 모두 제외.
    panels = {
        "floor_panel": sum(1 for c in comps if isinstance(c, FloorPanel)),
        "struct_wall": sum(1 for c in comps if isinstance(c, StructWall)),
        "interior_wall": sum(1 for c in comps if isinstance(c, InteriorWall)),
    }
    _PANEL_CLASS_KO = {
        FloorPanel: "바닥패널",
        StructWall: "벽패널",
        InteriorWall: "내벽",
    }
    panel_buckets: Dict[Tuple[str, int, int, Tuple[str, ...]], int] = {}
    for c in comps:
        cls_label = None
        for cls, label in _PANEL_CLASS_KO.items():
            if isinstance(c, cls):
                cls_label = label
                break
        if cls_label is None:
            continue
        try:
            w = float(c.dimensions.get("width", 0.0))
            d = float(c.dimensions.get("depth", 0.0))
            h = float(c.dimensions.get("height", 0.0))
        except Exception:
            w = d = h = 0.0
        sides = sorted([w, d, h], reverse=True)
        a_mm = int(round(sides[0] / 100.0) * 100)
        b_mm = int(round(sides[1] / 100.0) * 100)
        secs = tuple(_collect_panel_sections(c))
        key = (cls_label, a_mm, b_mm, secs)
        panel_buckets[key] = panel_buckets.get(key, 0) + 1

    # (class, 외곽) 별로 변형 묶기
    pnl_outer_to_variants: Dict[Tuple[str, int, int],
                                 List[Tuple[Tuple[str, ...], int]]] = {}
    for (label, a_mm, b_mm, secs), n in panel_buckets.items():
        pnl_outer_to_variants.setdefault((label, a_mm, b_mm), []).append((secs, n))
    pnl_outer_sorted = sorted(
        pnl_outer_to_variants.items(),
        key=lambda kv: (kv[0][0], -sum(n for _, n in kv[1]), kv[0][1:]),
    )
    panels_by_type: List[Dict[str, Any]] = []
    for (label, a_mm, b_mm), variants in pnl_outer_sorted:
        variants.sort(key=lambda v: (-v[1], v[0]))
        single = (len(variants) == 1)
        for vi, (secs, n) in enumerate(variants, 1):
            class_name = label if single else f"{label}-{vi}"
            panels_by_type.append({
                "class_label": class_name,
                "width_m": round(a_mm / 1000.0, 2),
                "depth_m": round(b_mm / 1000.0, 2),
                "count": int(n),
                "sections": ", ".join(secs) if secs else "—",
            })

    attached = {
        "cantilever_beam": sum(1 for c in comps if isinstance(c, CantileverBeam)),
        "cantilever_slab": sum(1 for c in comps if isinstance(c, CantileverSlab)),
        "mid_beam": sum(1 for c in comps if isinstance(c, MidBeam)),
        "mid_column": sum(1 for c in comps if isinstance(c, MidColumn)),
    }
    # 그룹화 자체에 sections 가 들어가 있으므로 _enrich_with_sections 호출 불필요.

    return {
        "modules_by_type": modules_by_type,
        "panels": panels,
        "panels_by_type": panels_by_type,
        "attached": attached,
    }


def _section_label(obj: Any) -> Optional[str]:
    """기둥/보/슬래브 sub-component → 'SHS 200×200×8' 같은 라벨.

    [2026-05-31] BeamData/ColumnData 는 section_type('shs'|'h') + section_w/h/t 로
    구성. 라벨을 그 둘을 합쳐 생성한다 (예: 'SHS 200×200×8', 'H 200×100×8').
    SlabData 는 thickness 만 → 'T150'.
    """
    # 1) 사람이 미리 박아둔 라벨이 있으면 그대로 사용
    for attr in ("section_label", "section_name"):
        v = getattr(obj, attr, None)
        if v:
            return str(v)
    # 2) BeamData / ColumnData — 형상 + 치수 조합
    sw = getattr(obj, "section_w", None)
    sh = getattr(obj, "section_h", None)
    st = getattr(obj, "section_t", None)
    if sw is not None and sh is not None:
        stype = getattr(obj, "section_type", None)
        head = "H" if (stype and str(stype).lower() == "h") else "SHS"
        if st is not None:
            return f"{head} {int(sw)}×{int(sh)}×{int(st)}"
        return f"{head} {int(sw)}×{int(sh)}"
    # 3) SlabData — 두께만
    thick = getattr(obj, "thickness", None)
    if thick is not None:
        return f"T{int(thick)}"
    # 4) dict 형식 fallback
    if isinstance(obj, dict):
        for k in ("section_label", "section_name"):
            v = obj.get(k)
            if v:
                return str(v)
        if "section_w" in obj and "section_h" in obj:
            sw_ = obj["section_w"]; sh_ = obj["section_h"]; st_ = obj.get("section_t")
            stype = obj.get("section_type")
            head = "H" if (stype and str(stype).lower() == "h") else "SHS"
            return (f"{head} {int(sw_)}×{int(sh_)}×{int(st_)}"
                    if st_ is not None
                    else f"{head} {int(sw_)}×{int(sh_)}")
        if "thickness" in obj:
            return f"T{int(obj['thickness'])}"
    # 5) 마지막 fallback — section_type 만이라도
    v = getattr(obj, "section_type", None)
    if v:
        return str(v).upper()
    return None


def _collect_module_sections(c: Component) -> List[str]:
    """한 모듈에서 사용된 기둥·보 단면 라벨 수집 (중복 제거)."""
    found: List[str] = []
    for attr in ("columns", "top_beams", "bottom_beams",
                 "edge_beams", "beams"):
        seq = getattr(c, attr, None)
        if not seq:
            continue
        try:
            for item in seq:
                lbl = _section_label(item)
                if lbl and lbl not in found:
                    found.append(lbl)
        except TypeError:
            pass
    return found


def _collect_module_sections_split(c: Component) -> Dict[str, str]:
    """모듈에서 *기둥 / 천장보 / 바닥보* 단면 라벨을 컬럼별로 분리.

    [2026-06-01] 종합탭 부재 컬럼이 한 셀에 모두 합쳐져 잘림이 잦아, 별도
    컬럼으로 분리. 모듈은 top_beams=천장보, bottom_beams=바닥보 컨벤션.
    """
    def grab(*attrs) -> str:
        labels: List[str] = []
        for attr in attrs:
            seq = getattr(c, attr, None) or []
            try:
                for item in seq:
                    lbl = _section_label(item)
                    if lbl and lbl not in labels:
                        labels.append(lbl)
            except TypeError:
                pass
        return ", ".join(labels) if labels else "—"
    return {
        "columns_section": grab("columns"),
        "top_beams_section": grab("top_beams"),
        "bottom_beams_section": grab("bottom_beams", "edge_beams", "beams"),
    }


def _collect_panel_sections(c: Component) -> List[str]:
    """패널(바닥패널/벽패널/코어슬래브)의 단면 라벨 수집."""
    found: List[str] = []
    # 가장자리 보 / 보
    for attr in ("edge_beams", "beams", "studs", "posts"):
        seq = getattr(c, attr, None)
        if not seq:
            continue
        try:
            for item in seq:
                lbl = _section_label(item)
                if lbl and lbl not in found:
                    found.append(lbl)
        except TypeError:
            pass
    # 슬래브 — SlabData 자체에 thickness 있음, _section_label 이 'T150' 로 만든다.
    slab = getattr(c, "slab", None)
    if slab is not None:
        lbl = _section_label(slab)
        if lbl and lbl not in found:
            found.append(lbl)
    return found


def _enrich_with_sections(modules_by_type: List[Dict[str, Any]],
                           panels_by_type: List[Dict[str, Any]],
                           comps: List[Component]) -> None:
    """타입별 사용 단면을 'sections' 키에 문자열로 부착."""
    # 모듈 — 같은 (폭, 길이) 버킷에 들어간 첫 모듈에서 단면 추출.
    mod_section_cache: Dict[Tuple[int, int], List[str]] = {}
    for c in comps:
        if not isinstance(c, (Module, Vertical3Module)):
            continue
        xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(c)
        a, b = sorted([round((xmax - xmin) / 1000.0, 2), round((ymax - ymin) / 1000.0, 2)])
        key = (int(a * 1000), int(b * 1000))
        if key in mod_section_cache:
            continue
        secs = _collect_module_sections(c)
        if secs:
            mod_section_cache[key] = secs
    for mt in modules_by_type:
        key = (int(round(mt["width_m"] * 1000)), int(round(mt["length_m"] * 1000)))
        secs = mod_section_cache.get(key, [])
        mt["sections"] = ", ".join(secs) if secs else "—"

    # 패널 — (class, 폭mm, 깊이mm) 버킷별 첫 인스턴스에서 단면 추출.
    pnl_section_cache: Dict[Tuple[str, int, int], List[str]] = {}
    cls_ko_map = {
        FloorPanel: "바닥패널", StructWall: "벽패널",
        InteriorWall: "내벽", CoreSlab: "코어슬래브",
    }
    for c in comps:
        cls_label = None
        for cls, lbl in cls_ko_map.items():
            if isinstance(c, cls):
                cls_label = lbl
                break
        if cls_label is None:
            continue
        try:
            w = float(c.dimensions.get("width", 0.0))
            d = float(c.dimensions.get("depth", 0.0))
            h = float(c.dimensions.get("height", 0.0))
        except Exception:
            w = d = h = 0.0
        sides = sorted([w, d, h], reverse=True)
        a_mm = int(round(sides[0] / 100.0) * 100)
        b_mm = int(round(sides[1] / 100.0) * 100)
        key = (cls_label, a_mm, b_mm)
        if key in pnl_section_cache:
            continue
        secs = _collect_panel_sections(c)
        if secs:
            pnl_section_cache[key] = secs
    for p in panels_by_type:
        key = (p["class_label"],
               int(round(p["width_m"] * 1000)),
               int(round(p["depth_m"] * 1000)))
        secs = pnl_section_cache.get(key, [])
        p["sections"] = ", ".join(secs) if secs else "—"


# ── C. 물량·자재비 ────────────────────────────────────────
def _compute_material_cost_safe(report) -> Any:
    """analysis_panel 과 동일 로직으로 자재비 산출. 실패 시 None."""
    try:
        from modular_3d.카탈로그.material_prices import get_unit_price
        from modular_3d.analysis.quantity_takeoff import compute_material_cost
        steel_unit = get_unit_price('강재_각형강관_SHS')
        steel_h_unit = get_unit_price('강재_H형강')
        deck_unit = get_unit_price('데크슬래브')
        concrete_unit = get_unit_price('콘크리트_레미콘')
        return compute_material_cost(
            report, steel_unit, deck_unit, concrete_unit,
            steel_h_unit_per_ton=steel_h_unit,
        )
    except Exception:
        return None


def _build_materials(
    quantity_reports: Optional[Dict[str, Any]],
    current_policy: str = "3종",
) -> Dict[str, Any]:
    """C. 물량·자재비 — current_policy 기준만 표시(물량 탭과 일치)."""
    if not quantity_reports:
        return {"available": False, "current_policy": current_policy}
    policy = current_policy if current_policy in quantity_reports else next(iter(quantity_reports))
    rep = quantity_reports[policy]
    if rep is None:
        return {"available": False, "current_policy": policy}

    # 본수표 — 합계행(is_total=True) 분리
    steel_rows: List[Dict[str, Any]] = []
    steel_total_row: Optional[Dict[str, Any]] = None
    for it in getattr(rep, "steel_items", []) or []:
        row = {
            "section": getattr(it, "section_name", "—"),
            "length_mm": float(getattr(it, "length_mm", 0.0) or 0.0),
            "count": int(getattr(it, "count", 0) or 0),
            "total_length_m": float(getattr(it, "total_length_m", 0.0) or 0.0),
            "unit_weight_kg_m": float(getattr(it, "unit_weight_kg_m", 0.0) or 0.0),
            "total_weight_ton": float(getattr(it, "total_weight_ton", 0.0) or 0.0),
            "section_type": getattr(it, "section_type", "shs"),
        }
        if getattr(it, "is_total", False):
            steel_total_row = row
        else:
            steel_rows.append(row)

    # 그룹별 채택 단면
    sections_by_group = getattr(rep, "sections_by_group", {}) or {}
    groups_rows: List[Dict[str, Any]] = []
    for gname, sec in sections_by_group.items():
        groups_rows.append({
            "group": str(gname),
            "section": getattr(sec, "name", "—"),
        })

    # 슬래브
    slab = getattr(rep, "slab", None)
    slab_info: Dict[str, Any] = {}
    if slab is not None:
        slab_info = {
            "total_area_m2": float(getattr(slab, "total_area_m2", 0.0) or 0.0),
            "total_volume_m3": float(getattr(slab, "total_volume_m3", 0.0) or 0.0),
            "thickness_mm": float(getattr(slab, "thickness_mm", 0.0) or 0.0),
            "rebar_weight_ton": float(getattr(slab, "rebar_weight_ton", 0.0) or 0.0),
        }

    # 자재비
    mc = _compute_material_cost_safe(rep)
    cost_info: Dict[str, Any] = {}
    total_material_cost = 0.0
    if mc is not None:
        cost_info = {
            "steel_ton": float(getattr(mc, "steel_ton", 0.0) or 0.0),
            "steel_cost": float(getattr(mc, "steel_cost", 0.0) or 0.0),
            "deck_area_m2": float(getattr(mc, "deck_area_m2", 0.0) or 0.0),
            "deck_cost": float(getattr(mc, "deck_cost", 0.0) or 0.0),
            "concrete_m3": float(getattr(mc, "concrete_m3", 0.0) or 0.0),
            "concrete_cost": float(getattr(mc, "concrete_cost", 0.0) or 0.0),
            "total_cost": float(getattr(mc, "total_cost", 0.0) or 0.0),
            "has_missing_price": bool(getattr(mc, "has_missing_price", False)),
        }
        total_material_cost = cost_info["total_cost"]

    return {
        "available": True,
        "current_policy": policy,
        "groups": groups_rows,
        "steel_rows": steel_rows,
        "steel_total": steel_total_row,
        "slab": slab_info,
        "cost": cost_info,
        "total_material_cost_krw": total_material_cost,
    }


# ── D. 운송 요약 ──────────────────────────────────────────
def _build_transport(
    transport_pack: Optional[Any],
    transport_eco: Optional[Any],
    project_settings: Optional[Any],
) -> Dict[str, Any]:
    if transport_pack is None:
        return {"available": False}
    trips = list(getattr(transport_pack, "trips", []) or [])
    total_trips = len(trips)
    # 평균 적재 — Trip.cargo_weight (kg) 평균
    if total_trips > 0:
        weights = [float(getattr(t, "cargo_weight", 0.0) or 0.0) for t in trips]
        avg_load_kg = sum(weights) / total_trips
    else:
        avg_load_kg = 0.0
    # 적재율 — pack.avg_utilization (%)
    avg_util = float(getattr(transport_pack, "avg_utilization", 0.0) or 0.0)
    # 운송 거리 = ProjectSettings.distance_km × 회차 × 2 (왕복)
    distance_one_way = 0.0
    if project_settings is not None:
        distance_one_way = float(getattr(project_settings, "distance_km", 0.0) or 0.0)
    distance_total = distance_one_way * total_trips * 2.0
    # 운송비 — EconomicsResult.total_cost_krw 프로퍼티
    total_cost = 0.0
    if transport_eco is not None:
        try:
            total_cost = float(getattr(transport_eco, "total_cost_krw", 0.0) or 0.0)
        except Exception:
            total_cost = 0.0
    return {
        "available": True,
        "trips_total": int(total_trips),
        "module_trips": int(getattr(transport_pack, "module_trips", 0) or 0),
        "panel_trips": int(getattr(transport_pack, "panel_trips", 0) or 0),
        "avg_load_kg": float(avg_load_kg),
        "avg_utilization_pct": avg_util,
        "distance_km_total": float(distance_total),
        "total_cost_krw": float(total_cost),
    }


# ── E. 공기 요약 ──────────────────────────────────────────
def _build_schedule(schedule_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not schedule_payload:
        return {"available": False}
    p = schedule_payload
    return {
        "available": True,
        "total_days": int(p.get("total_days", 0) or 0),
        "core_days": int(p.get("core_days", 0) or 0),
        "module_install_days": int(p.get("module_install_days", 0) or 0),
        "tasks": list(p.get("tasks", []) or []),
        "labor_sum_krw": float(p.get("labor_sum_krw", 0.0) or 0.0),
        "equip_sum_krw": float(p.get("equip_sum_krw", 0.0) or 0.0),
    }


# ── F. 종합 비용 ──────────────────────────────────────────
def _build_cost(materials: Dict[str, Any], transport: Dict[str, Any],
                schedule: Dict[str, Any]) -> Dict[str, Any]:
    material_cost = float((materials or {}).get("total_material_cost_krw", 0.0) or 0.0)
    transport_cost = float((transport or {}).get("total_cost_krw", 0.0) or 0.0)
    labor_cost = float((schedule or {}).get("labor_sum_krw", 0.0) or 0.0)
    equip_cost = float((schedule or {}).get("equip_sum_krw", 0.0) or 0.0)
    total = material_cost + transport_cost + labor_cost + equip_cost
    return {
        "material_krw": round(material_cost),
        "transport_krw": round(transport_cost),
        "labor_krw": round(labor_cost),
        "equip_krw": round(equip_cost),
        "total_krw": round(total),
        "policy_used_for_material": (materials or {}).get("current_policy"),
    }


# ── 메인 진입점 ───────────────────────────────────────────
def build_evaluation_data(
    components: Optional[Iterable[Component]] = None,
    project_settings: Optional[Any] = None,
    quantity_reports: Optional[Dict[str, Any]] = None,
    current_policy: str = "3종",
    transport_pack: Optional[Any] = None,
    transport_eco: Optional[Any] = None,
    schedule_payload: Optional[Dict[str, Any]] = None,
    section_types: Optional[List[Dict[str, Any]]] = None,
    scene_components_by_id: Optional[Dict[int, Any]] = None,
) -> Dict[str, Any]:
    """평가 화면이 받을 dict.

    Returns:
        {
          "headline": {...},
          "members": {...},
          "materials": {"available": bool, "current_policy", "steel_rows", "cost", ...},
          "transport": {"available": bool, ...},
          "schedule": {"available": bool, ...},
          "cost": {...},
          "status": {"materials_ok", "transport_ok", "schedule_ok"}
        }
    """
    comps = list(components or [])
    headline = _build_headline(comps, project_settings)
    # [2026-06-01] 단면 설계 탭의 타입 목록이 있으면 그걸 정답으로 사용,
    # 없으면 배치된 컴포넌트 자동 분류 (기존 동작).
    if section_types:
        members = _build_members_from_section_types(
            section_types, scene_components_by_id or {}, comps)
    else:
        members = _build_members(comps)
    materials = _build_materials(quantity_reports, current_policy=current_policy)
    transport = _build_transport(transport_pack, transport_eco, project_settings)
    schedule = _build_schedule(schedule_payload)
    cost = _build_cost(materials, transport, schedule)
    status = {
        "materials_ok": bool(materials.get("available")),
        "transport_ok": bool(transport.get("available")),
        "schedule_ok": bool(schedule.get("available")),
    }
    # [2026-05-31] 케이스 식별 헤더 — 지역·착공일·평가작성일.
    # project_settings 의 region_city / start_date 그대로 노출.
    # eval_date 는 평가 시점에 시스템 날짜로 채움(없으면 빈 문자열).
    case: Dict[str, Any] = {}
    if project_settings is not None:
        case["region"] = str(getattr(project_settings, "region_city", "") or "")
        case["start_date"] = str(getattr(project_settings, "start_date", "") or "")
        # 프로젝트명 필드가 추후 추가될 수 있으니 우선 안전 접근.
        name = getattr(project_settings, "project_name", None)
        if name:
            case["name"] = str(name)
    try:
        from datetime import date
        case["eval_date"] = date.today().isoformat()
    except Exception:
        pass
    return {
        "case": case,
        "headline": headline,
        "members": members,
        "materials": materials,
        "transport": transport,
        "schedule": schedule,
        "cost": cost,
        "status": status,
    }


__all__ = ["build_evaluation_data"]
