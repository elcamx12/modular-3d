"""공정표 어댑터 — 우리 모델(scene + ProjectSettings) → 팀원 HTML 입력 직렬화.

[설계 근거]
- `공정표_이식_계획서.md` §3 Phase B.
- 출력: dict {summary: ..., components: [...]} — 팀원 HTML 의 importScene 이 그대로 받음.
- 좌표 정책: position 을 항상 **min-corner**(앵커=0, 회전=0) 로 정규화해 export.
  팀원 box() 가 0도 분기만 타도록 만들어 회전·앵커 다양성 흡수.
- summary 우선 — readModuleJoints / 좌표 폴백 경로를 거치지 않게 충분히 채운다.

[모듈-모듈 접합 카운트]
- 수직접합: floor_index>0 인 *일반 모듈* 만 × 4 (꼭지점 기둥). 수직3층모듈 자체엔
  층간 수직접합 없음(한 덩어리).
- 수평접합: 층별로 그 층에 존재하는 모듈(수직모듈은 관통 층 모두 포함)의
  인접쌍 수 합산 — C3 정정 반영.

[모듈 최대 중량]
- 운송 어댑터의 산출값을 받을 수 있으면 그것을 사용, 아니면 면적 기반 어림으로
  보수적 추정. HTML 의 moduleTypes 입력으로 사용자가 보정 가능.

[비의존성]
- ops_builder · OpenSees · transport 어댑터에 의존하지 않는다(가벼움 우선).
- model/core.py 의 ComponentType / Component 만 읽음.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from ..model.core import (
    Component,
    ComponentType,
    Module,
    FloorPanel,
    StructWall,
    Vertical3Module,
    CoreSlab,
)


# ── 좌표 정규화 ─────────────────────────────────────────────
def _aabb_xy_extent(comp: Component) -> Tuple[float, float, float, float, float, float]:
    """월드 AABB (xmin, ymin, zmin, xmax, ymax, zmax) — 회전·앵커 모두 반영."""
    try:
        verts = comp.get_world_corners()
        if verts is None or len(verts) == 0:
            raise ValueError("no corners")
        xmin, ymin, zmin = float(verts[:, 0].min()), float(verts[:, 1].min()), float(verts[:, 2].min())
        xmax, ymax, zmax = float(verts[:, 0].max()), float(verts[:, 1].max()), float(verts[:, 2].max())
        return xmin, ymin, zmin, xmax, ymax, zmax
    except Exception:
        # 폴백: position + dimensions 그대로 (rotation 무시)
        x = float(comp.position[0]); y = float(comp.position[1]); z = float(comp.position[2])
        w = float(comp.dimensions.get("width", 0.0))
        d = float(comp.dimensions.get("depth", 0.0))
        h = float(comp.dimensions.get("height", 0.0))
        return x, y, z, x + w, y + d, z + h


def _to_jsonable(v: Any) -> Any:
    """numpy / 비표준 자료형을 JSON 안전형으로 변환."""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, np.ndarray):
        return [_to_jsonable(x) for x in v.tolist()]
    if isinstance(v, dict):
        return {str(k): _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return v


def _normalize_joint_records(records: Iterable[Any]) -> List[Dict[str, Any]]:
    """joint_records 의 numpy 값들을 모두 native 형으로 변환."""
    out: List[Dict[str, Any]] = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        out.append({str(k): _to_jsonable(v) for k, v in r.items()})
    return out


def _component_to_export_dict(comp: Component) -> Dict[str, Any]:
    """컴포넌트를 팀원 HTML 이 기대하는 component dict 로 직렬화.

    - position 은 AABB.min (rotation=0, anchor=0 으로 export).
    - dimensions.width/depth/height 는 월드 AABB 변(회전이 이미 반영된 길이).
    - joint_records 는 numpy 자료형을 native 로 정규화해 그대로 전달.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = _aabb_xy_extent(comp)
    dim = dict(comp.dimensions or {})
    dim["width"] = float(xmax - xmin)
    dim["depth"] = float(ymax - ymin)
    dim["height"] = float(zmax - zmin)
    dim = {str(k): _to_jsonable(v) for k, v in dim.items()}
    out: Dict[str, Any] = {
        "id": int(comp.id),
        "comp_type": comp.comp_type.value if isinstance(comp.comp_type, ComponentType) else str(comp.comp_type),
        "position": [float(xmin), float(ymin), float(zmin)],
        "rotation": 0,
        "anchor": 0,
        "dimensions": dim,
        "floor_index": int(getattr(comp, "floor_index", 0)),
        "group_id": int(getattr(comp, "group_id", 0)),
        "sub_index": int(getattr(comp, "sub_index", 0)),
        "joint_records": _normalize_joint_records(getattr(comp, "joint_records", []) or []),
    }
    return out


# ── 모듈/수직모듈 관통 층 산출 ─────────────────────────────
_MODULE_FLOOR_HEIGHT_MM = 3000.0   # 표준 1층 높이 (관통 층수 추정용)


def _module_floor_span(comp: Component) -> Tuple[int, int]:
    """이 모듈이 점유하는 [시작층, 끝층] (inclusive).

    - 일반 모듈: (f, f)
    - 수직3층모듈: height 로 관통 층수 추정.
    """
    f0 = int(getattr(comp, "floor_index", 0))
    if isinstance(comp, Vertical3Module):
        h = float(comp.dimensions.get("height", 0.0))
        k = max(1, int(round(h / _MODULE_FLOOR_HEIGHT_MM)))
        return f0, f0 + k - 1
    return f0, f0


# ── 인접 모듈쌍 카운트 ─────────────────────────────────────
_ADJ_TOL_MM = 400.0      # 인접 허용 간격(팀원 코드 T)
_OVERLAP_REJECT_MM = 50  # 그 이상 겹치면 비정상으로 제외


def _adjacent_pairs(boxes: List[Tuple[float, float, float, float]]) -> int:
    """팀원 countHorizJointsFloor0 와 동일 산식: 인접쌍 카운트."""
    n = 0
    L = len(boxes)
    for i in range(L):
        a = boxes[i]
        for k in range(i + 1, L):
            b = boxes[k]
            ox = min(a[2], b[2]) - max(a[0], b[0])  # x 겹침
            oy = min(a[3], b[3]) - max(a[1], b[1])  # y 겹침
            if ox > _OVERLAP_REJECT_MM and oy > _OVERLAP_REJECT_MM:
                continue   # 비정상 겹침 — 제외
            t_x = (abs(a[2] - b[0]) <= _ADJ_TOL_MM or abs(b[2] - a[0]) <= _ADJ_TOL_MM) and oy > -_ADJ_TOL_MM
            t_y = (abs(a[3] - b[1]) <= _ADJ_TOL_MM or abs(b[3] - a[1]) <= _ADJ_TOL_MM) and ox > -_ADJ_TOL_MM
            if t_x or t_y:
                n += 1
    return n


# ── summary 빌드 ───────────────────────────────────────────
def _is_module_kind(comp: Component) -> bool:
    return isinstance(comp, (Module, Vertical3Module))


def _build_summary(
    comps: List[Component],
    module_weight_t_max: Optional[float],
    basement_floors: int,
) -> Dict[str, Any]:
    modules = [c for c in comps if _is_module_kind(c)]
    if not modules:
        return {
            "floors_above_ground": 0,
            "modules_total": 0,
            "modules_per_floor": 0,
            "footprint_area_m2": 0.0,
            "core_area_per_floor_m2": 0.0,
            "vertical_joints_total": 0,
            "horizontal_joints_total": 0,
            "module_max_weight_t": float(module_weight_t_max or 0.0),
            "basement_floors": int(basement_floors),
        }

    # 층 범위
    spans = [_module_floor_span(c) for c in modules]
    floors_above_ground = max(s[1] for s in spans) + 1

    # modules_per_floor — floor 0 에서 그 층을 점유하는 모듈 수 (수직모듈 포함)
    floor0_present = [
        c for c, s in zip(modules, spans) if s[0] <= 0 <= s[1]
    ]
    modules_per_floor = len(floor0_present)
    modules_total = len(modules)

    # footprint — floor 0 모듈 + floor 0 바닥패널의 AABB footprint 합(m²)
    footprint_mm2 = 0.0
    for c in floor0_present:
        xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
        footprint_mm2 += (xmax - xmin) * (ymax - ymin)
    for c in comps:
        if isinstance(c, FloorPanel) and int(getattr(c, "floor_index", 0)) == 0:
            xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
            footprint_mm2 += (xmax - xmin) * (ymax - ymin)
    footprint_area_m2 = footprint_mm2 / 1.0e6

    # 코어 한 층 면적 — CoreSlab 한 개의 width×depth (m²). 여러 개면 합산.
    core_area_m2 = 0.0
    seen_core_floors: set = set()
    for c in comps:
        if isinstance(c, CoreSlab):
            f = int(getattr(c, "floor_index", 0))
            if f in seen_core_floors:
                continue
            seen_core_floors.add(f)
            xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
            core_area_m2 += (xmax - xmin) * (ymax - ymin) / 1.0e6
            # 한 층 면적이면 충분하므로 첫 발견 층의 값만 사용
            break

    # 수직접합: floor_index>0 인 *일반 모듈* × 4 (수직3층모듈 제외)
    vertical_joints_total = sum(
        4 for c in comps if isinstance(c, Module) and int(getattr(c, "floor_index", 0)) > 0
    )

    # 수평접합: 층별 인접 모듈쌍 합산 (수직모듈은 관통 층 모두에 포함)
    horizontal_joints_total = 0
    for f in range(floors_above_ground):
        boxes_f: List[Tuple[float, float, float, float]] = []
        for c, s in zip(modules, spans):
            if s[0] <= f <= s[1]:
                xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
                boxes_f.append((xmin, ymin, xmax, ymax))
        horizontal_joints_total += _adjacent_pairs(boxes_f)

    return {
        "floors_above_ground": int(floors_above_ground),
        "modules_total": int(modules_total),
        "modules_per_floor": int(modules_per_floor),
        "footprint_area_m2": round(footprint_area_m2, 1),
        "core_area_per_floor_m2": round(core_area_m2, 1),
        "vertical_joints_total": int(vertical_joints_total),
        "horizontal_joints_total": int(horizontal_joints_total),
        "module_max_weight_t": float(module_weight_t_max or 0.0),
        "basement_floors": int(basement_floors),
    }


# ── 모듈 타입 자동 추출 (multi-type 직접 주입용, B2 우회) ──
def _module_types_from_components(
    comps: Iterable[Component], default_weight_t: float = 10.0,
) -> List[Dict[str, Any]]:
    """모듈 폭(m)·길이(m) 같은 타입을 묶어 카운트.

    팀원 importScene 은 단일 항목으로 압축하지만, 본 어댑터는 multi-type 으로
    직접 JS 전역(moduleTypes)에 세팅해 B2 를 우회한다.
    """
    buckets: Dict[Tuple[int, int], int] = {}
    weight_by_key: Dict[Tuple[int, int], float] = {}
    for c in comps:
        if not _is_module_kind(c):
            continue
        xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
        w_m = round((xmax - xmin) / 1000.0, 2)
        l_m = round((ymax - ymin) / 1000.0, 2)
        # depth 가 길이일지 width 가 길이일지 컴포넌트에 따라 다르지만, AABB 라
        # 회전 후 결과 그대로 카운트해도 입력으로는 충분. 항상 (작은 변, 큰 변)
        # 정규화 — A타입과 B타입이 회전만 달라도 동일 버킷에 들어가도록.
        a, b = sorted([w_m, l_m])
        key = (int(a * 1000), int(b * 1000))
        buckets[key] = buckets.get(key, 0) + 1
        wt = float(getattr(c, "weight_t_estimate", 0.0) or 0.0)
        if wt > weight_by_key.get(key, 0.0):
            weight_by_key[key] = wt

    types: List[Dict[str, Any]] = []
    name_iter = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for key, n in sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0])):
        a_mm, b_mm = key
        types.append({
            "name": next(name_iter, "X") + "타입",
            "w": a_mm / 1000.0,
            "l": b_mm / 1000.0,
            "n": int(n),
            "wt": float(weight_by_key.get(key) or default_weight_t),
        })
    if not types:
        types = [{"name": "기본타입", "w": 3.4, "l": 6.0, "n": 0, "wt": default_weight_t}]
    return types


# ── ProjectSettings 매핑 보조 ──────────────────────────────
def _region_key_for_html(region_city: Optional[str]) -> Optional[str]:
    """ProjectSettings.region_city("천안 충남") → HTML REGION_GADONG 키("천안") 추출.

    ProjectSettings 는 "도시 시도" 또는 단순 "도시" 형식을 모두 허용한다.
    HTML 키는 첫 단어(공백 앞)만 사용 — 매핑 실패 시 None 반환.
    """
    if not region_city:
        return None
    first = str(region_city).strip().split()[0] if str(region_city).strip() else ""
    return first or None


# ── 메인 진입점 ────────────────────────────────────────────
def build_scene_data(
    components: Iterable[Component],
    project_settings: Optional[Any] = None,
    module_weight_t_max: Optional[float] = None,
    module_default_weight_t: float = 10.0,
) -> Dict[str, Any]:
    """씬 + 프로젝트 설정 → 팀원 HTML 이 받을 dict.

    Returns:
        {
          "summary": {...},
          "components": [...],
          "module_types": [...],   # B2 우회용 (JS 전역에 직접 주입)
          "project": {             # Phase H — ProjectSettings 자동주입용
            "region_key": "천안" | None,
            "start_date": "2026-01-01" | None,
          }
        }
    """
    comps = list(components)

    # 지하 층수 — ProjectSettings 에 필드가 없으면 0.
    basement_floors = 0
    region_city = None
    start_date = None
    if project_settings is not None:
        basement_floors = int(getattr(project_settings, "basement_floors", 0) or 0)
        region_city = getattr(project_settings, "region_city", None)
        start_date = getattr(project_settings, "start_date", None)

    summary = _build_summary(
        comps, module_weight_t_max=module_weight_t_max, basement_floors=basement_floors,
    )
    components_out = [_component_to_export_dict(c) for c in comps]
    module_types = _module_types_from_components(
        comps, default_weight_t=module_default_weight_t,
    )
    return {
        "summary": summary,
        "components": components_out,
        "module_types": module_types,
        "project": {
            "region_key": _region_key_for_html(region_city),
            "start_date": str(start_date) if start_date else None,
        },
    }


__all__ = [
    "build_scene_data",
]
