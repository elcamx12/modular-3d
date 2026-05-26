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
    CantileverBeam,
    CantileverSlab,
    MidBeam,
    MidColumn,
    InteriorWall,
)


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

    return {
        "floors_above_ground": int(floors_above),
        "basement_floors": basement,
        "modules_total": int(len(modules)),
        "footprint_m2": round(footprint_m2, 1),
        "total_floor_area_m2": round(total_floor_area_m2, 1),
    }


# ── B. 부재 구성 ──────────────────────────────────────────
def _build_members(comps: List[Component]) -> Dict[str, Any]:
    module_buckets: Dict[Tuple[int, int], int] = {}
    for c in comps:
        if not isinstance(c, (Module, Vertical3Module)):
            continue
        xmin, ymin, _z0, xmax, ymax, _z1 = _aabb(c)
        a, b = sorted([round((xmax - xmin) / 1000.0, 2), round((ymax - ymin) / 1000.0, 2)])
        key = (int(a * 1000), int(b * 1000))
        module_buckets[key] = module_buckets.get(key, 0) + 1
    modules_by_type: List[Dict[str, Any]] = []
    name_iter = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for key, n in sorted(module_buckets.items(), key=lambda kv: (-kv[1], kv[0])):
        a_mm, b_mm = key
        modules_by_type.append({
            "name": next(name_iter, "X") + "타입",
            "width_m": a_mm / 1000.0,
            "length_m": b_mm / 1000.0,
            "count": int(n),
            "area_m2": round(a_mm * b_mm / 1.0e6, 2),
        })

    panels = {
        "floor_panel": sum(1 for c in comps if isinstance(c, FloorPanel)),
        "struct_wall": sum(1 for c in comps if isinstance(c, StructWall)),
        "interior_wall": sum(1 for c in comps if isinstance(c, InteriorWall)),
        "core_slab": sum(1 for c in comps if isinstance(c, CoreSlab)),
    }
    attached = {
        "cantilever_beam": sum(1 for c in comps if isinstance(c, CantileverBeam)),
        "cantilever_slab": sum(1 for c in comps if isinstance(c, CantileverSlab)),
        "mid_beam": sum(1 for c in comps if isinstance(c, MidBeam)),
        "mid_column": sum(1 for c in comps if isinstance(c, MidColumn)),
    }
    return {"modules_by_type": modules_by_type, "panels": panels, "attached": attached}


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
    return {
        "headline": headline,
        "members": members,
        "materials": materials,
        "transport": transport,
        "schedule": schedule,
        "cost": cost,
        "status": status,
    }


__all__ = ["build_evaluation_data"]
