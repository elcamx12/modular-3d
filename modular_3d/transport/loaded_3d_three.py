"""운송 적재 3D 도식 — Three.js + OrbitControls + CSS2DRenderer.

[설계 근거]
- 사용자 요청: modular_designer.html 의 Three.js 스타일과 동일한 디자인.
- 어두운 배경 (#0d1117) + Fog + 그림자 (PCFSoftShadowMap) + 다중 광원.
- OrbitControls 로 마우스 자유 회전·이동·줌. 댐핑 적용.
- 회차 강조는 `runJavaScript("highlightTrip(N)")` 로 부분 갱신 — 페이지 reload
  없이 강조만 변경 → 카메라 시점 자동 보존.
- 라벨은 CSS2DRenderer 로 HTML div 직접 — 폰트·색상 자유.

[좌표계]
- Python 운송 좌표: x=길이, y=폭, z=높이 (모두 mm 단위)
- Three.js 좌표: x=오른쪽, y=위, z=화면쪽 (단위 m)
- 매핑: Python(x, y, z, mm) → Three.js(x, z, y, m) — y↔z 교환, mm→m 환산

[데이터 흐름]
- Python: trips → serialize_pack_for_three() → JSON dict
- HTML: 그 JSON 을 인라인 임베드 + Three.js JS 가 scene 구축
- 회차 강조: window.highlightTrip(N) JS 함수 호출 (page reload X)
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from .models import Module, Panel, SpacingParams, Truck, WallSegment
from .packer import Trip
from .packer_safety import _item_dims_for_posture
from .packer_types import Placement, PlacementSlot, Posture


# ── 회차 간 간격 (m) — 사용자 결정 ───────────────────────────────
# 다른 그룹은 *진행 방향 (X)* 으로 일렬 (사이드뷰에서 가로로)
# 같은 그룹은 *폭 방향 (Y, Python -Y = Three.js -Z = 사이드뷰 화면 깊이)*
#   으로 겹쳐 누적 — 사용자 표현: "사이드뷰에서 뒤쪽 = 트럭 오른쪽"
INTER_TRUCK_GAP_M: float = 4.0   # 다른 그룹 사이 (X 방향)
INTRA_GROUP_GAP_M: float = 1.5   # 같은 그룹 사이 (Y 방향 — 좁게, 시각 정체성)


def _trip_signature(trip) -> tuple:
    """trip 의 *동일 결과* 판정용 시그니처.

    같은 트럭 종류·크기 + 같은 화물 구성(종류·치수·자세·자리) 의 회차들은
    *같은 그룹* 으로 묶여 진행 방향(+x) 으로 일렬 겹침.
    """
    truck = trip.truck
    sig_parts = [
        truck.truck_type,
        int(round(truck.max_length)),
        int(round(truck.max_width)),
        int(round(truck.max_height)),
    ]
    items_sig = []
    if trip.placements:
        for p in trip.placements:
            it = p.item
            L, W, h, _ = _item_dims_for_posture(it, p.posture)
            posture_str = (
                p.posture.value if hasattr(p.posture, "value") else str(p.posture)
            )
            slot_str = (
                p.slot.value if hasattr(p.slot, "value") else str(p.slot)
            )
            items_sig.append((
                _classify_item(it),
                int(round(L)), int(round(W)), int(round(h)),
                posture_str, slot_str,
            ))
    else:
        # 역호환 — placements 비어있으면 items 만으로 추정
        for it in trip.items:
            items_sig.append((
                _classify_item(it),
                int(round(getattr(it, "length", 0.0))),
                int(round(getattr(it, "width", 0.0))),
                int(round(getattr(it, "height",
                                    getattr(it, "thickness", 0.0)))),
                "lying", "floor",
            ))
    items_sig.sort()
    return (tuple(sig_parts), tuple(items_sig))


def _classify_item(item) -> str:
    """화물 종류 식별 — 색상 매핑용."""
    if isinstance(item, Module):
        return "module"
    if isinstance(item, Panel):
        if item.kind == "wall":
            return "wall"
        if item.wall_segments or item.kind == "lshape":
            return "dep_floor"
        return "floor"
    return "unknown"


def _serialize_wall_segment(
    seg: WallSegment, panel: Panel,
    floor_x0_m: float, floor_y0_m: float, floor_top_z_m: float,
) -> Dict:
    """wall_segment 위치 (m 단위) 산출 — floor 좌하단 기준 + 적재면 위.

    좌표 약속 (Phase A 정규화 후의 종속 floor 로컬):
    - x = panel.length 방향, y = panel.width 방향
    - side 0 = 하변 (y=0, x 방향 변)  / side 1 = 우변 (x=panel.length)
    - side 2 = 상변 (y=panel.width)   / side 3 = 좌변 (x=0)
    """
    panel_length_m = panel.length / 1000.0
    panel_width_m = panel.width / 1000.0
    seg_length_m = seg.length_mm / 1000.0
    seg_thickness_m = seg.thickness_mm / 1000.0
    start_offset_m = seg.start_offset_mm / 1000.0
    seg_height_m = seg.height_mm / 1000.0

    if seg.side == 0:  # 하변
        sx = floor_x0_m + start_offset_m
        sy = floor_y0_m
        sdx = seg_length_m
        sdy = seg_thickness_m
    elif seg.side == 1:  # 우변
        sx = floor_x0_m + panel_length_m - seg_thickness_m
        sy = floor_y0_m + start_offset_m
        sdx = seg_thickness_m
        sdy = seg_length_m
    elif seg.side == 2:  # 상변
        sx = floor_x0_m + start_offset_m
        sy = floor_y0_m + panel_width_m - seg_thickness_m
        sdx = seg_length_m
        sdy = seg_thickness_m
    else:  # side 3 — 좌변
        sx = floor_x0_m
        sy = floor_y0_m + start_offset_m
        sdx = seg_thickness_m
        sdy = seg_length_m

    return {
        "side": seg.side,
        "x_m": sx,
        "y_m": sy,
        "z_m": floor_top_z_m,
        "dx_m": sdx,
        "dy_m": sdy,
        "dz_m": seg_height_m,
    }


def _nominal_dims_for_visual(item, posture: Posture) -> Tuple[float, float, float]:
    """*시각화 박스* 의 nominal 차원 (mm) — 부재 외곽이 아닌 부모 본체 치수.

    안전 검사 _item_dims_for_posture 가 부재 포함 외곽을 반환하므로 시각화는
    여기서 별도 산출. 부재는 attached_parts 로 별도 mesh 그림.
    """
    if isinstance(item, Module):
        return item.length, item.width, item.height
    # Panel
    if posture == Posture.STANDING:
        return item.length, item.thickness, item.width
    # LYING: 박스 본체는 두께만 (벽·캔틸은 별도)
    return item.length, item.width, item.thickness


def _serialize_attached_part(
    part, floor_x0_m: float, floor_y0_m: float, z_bottom_m: float,
) -> Dict:
    """AttachedPart → JSON. 부모 화물의 좌하단(월드) 기준 위치 (m)."""
    return {
        "kind": part.kind,
        "source_kind": part.source_kind,
        "subkind": part.subkind,
        "x_m": floor_x0_m + part.x_mm / 1000.0,
        "y_m": floor_y0_m + part.y_mm / 1000.0,
        "z_m": z_bottom_m + part.z_mm / 1000.0,
        "length_m": part.length_mm / 1000.0,
        "width_m": part.width_mm / 1000.0,
        "height_m": part.height_mm / 1000.0,
        "section_name": part.section_name,
        # H형강 운송 렌더 — 보면 I자 압출 + 웨브 방향(운송 좌표계).
        "section_type": getattr(part, "section_type", "shs"),
        "web_dir": [getattr(part, "web_dx", 0.0),
                    getattr(part, "web_dy", 0.0),
                    getattr(part, "web_dz", 1.0)],
    }


def _serialize_body_parts(
    item, x0_m: float, y0_m: float, z0_m: float,
) -> List[Dict]:
    """item.body_parts (운송 좌표계 BodyPart) → 화물 좌하단 기준 m 단위 dict 리스트.

    body_parts 는 어댑터에서 *배치 모델의 실측 좌표* 를 운송 nominal 좌표계로
    변환해 채운 것. 시각화는 추정 없이 이걸 그대로 사용.
    """
    out: List[Dict] = []
    for bp in tuple(getattr(item, "body_parts", ()) or ()):
        out.append({
            "kind": bp.kind,
            "subkind": bp.subkind,
            "x_m": x0_m + bp.x_mm / 1000.0,
            "y_m": y0_m + bp.y_mm / 1000.0,
            "z_m": z0_m + bp.z_mm / 1000.0,
            "length_m": bp.length_mm / 1000.0,
            "width_m": bp.width_mm / 1000.0,
            "height_m": bp.height_mm / 1000.0,
            # H형강 운송 렌더 — 보면 I자 압출 + 웨브 방향(운송 좌표계).
            "section_type": getattr(bp, "section_type", "shs"),
            "web_dir": [getattr(bp, "web_dx", 0.0),
                        getattr(bp, "web_dy", 0.0),
                        getattr(bp, "web_dz", 1.0)],
        })
    return out





def _serialize_placement(p: Placement, truck: Truck) -> Dict:
    """Placement → JSON dict (m 단위). 트럭 좌측 끝 = x=0 기준.

    [Phase 3 — 사실적 부재 단위 렌더링]
    nominal 차원으로 박스 본체 좌표 산출. 모듈은 기둥·보·슬래브로 분해,
    attached_parts(캔틸·중간보·중간기둥) 는 별도 직렬화 → JS 가 부재별 mesh
    생성.
    """
    item = p.item
    # 시각화는 nominal 차원으로 (안전 검사용 외곽 차원 X)
    L_mm, W_mm, h_mm = _nominal_dims_for_visual(item, p.posture)
    # placement.truck_xyz 는 적재함 *중심* 원점. 외곽 차원으로 자리 잡았으므로
    # 본 박스의 중심 = truck 좌측 끝 + (외곽 차원 L_outer/2 - L_nominal/2) +
    # placement.truck_xyz[0]. 단순화 — Phase 3 은 외곽-nominal 차이 무시
    # (대부분 캔틸 돌출은 부모 한쪽에 치우치므로 중심 오차 ≤ 부재 길이).
    center_x_m = (truck.max_length / 2 + p.truck_xyz[0]) / 1000.0
    center_y_m = (truck.max_width / 2 + p.truck_xyz[1]) / 1000.0
    z_bottom_m = (truck.vehicle_height_offset + p.truck_xyz[2]) / 1000.0
    L_m = L_mm / 1000.0
    W_m = W_mm / 1000.0
    h_m = h_mm / 1000.0
    # 본체 좌하단 — 부재 좌표 산출 기준점
    x0_m = center_x_m - L_m / 2
    y0_m = center_y_m - W_m / 2

    out = {
        "name": getattr(item, "name", "?"),
        "kind": _classify_item(item),
        "posture": p.posture.value if hasattr(p.posture, "value") else str(p.posture),
        "slot": p.slot.value if hasattr(p.slot, "value") else str(p.slot),
        "center_x_m": center_x_m,
        "center_y_m": center_y_m,
        "z_bottom_m": z_bottom_m,
        "length_m": L_m,
        "width_m": W_m,
        "height_m": h_m,
        "weight_kg": float(getattr(item, "weight", 0.0)),
        "wall_segments": [],
        "attached_parts": [],
        "body_parts": [],
    }
    # 부모 자체 부재 — 어댑터가 배치 모델에서 실측한 좌표 그대로
    out["body_parts"] = _serialize_body_parts(item, x0_m, y0_m, z_bottom_m)
    # 종속 floor 의 wall_segments — LYING 자세만
    if (
        isinstance(item, Panel) and item.wall_segments
        and p.posture == Posture.LYING
    ):
        floor_top_z_m = z_bottom_m + (item.thickness / 1000.0)
        out["wall_segments"] = [
            _serialize_wall_segment(
                seg, item, x0_m, y0_m, floor_top_z_m,
            )
            for seg in item.wall_segments
        ]
    # attached_parts — 캔틸·중간보·중간기둥
    parts = tuple(getattr(item, "attached_parts", ()) or ())
    out["attached_parts"] = [
        _serialize_attached_part(prt, x0_m, y0_m, z_bottom_m) for prt in parts
    ]
    return out


def _serialize_legacy_item(
    item, truck: Truck, cursor_x_mm: float, sp: SpacingParams,
) -> Dict:
    """역호환 — Trip.placements 비어있을 때 items 만으로 위치 추정."""
    if isinstance(item, Module):
        L_mm = item.length
        W_mm = item.width
        h_mm = item.height
    elif isinstance(item, Panel):
        L_mm = item.length
        W_mm = item.width
        # 종속 floor 도 시각화 박스는 *바닥 두께만* — 벽은 wall_segments 별도 그림
        h_mm = item.thickness
    else:
        L_mm = W_mm = h_mm = 0.0

    center_x_m = (cursor_x_mm + L_mm / 2) / 1000.0
    center_y_m = (truck.max_width / 2) / 1000.0
    z_bottom_m = truck.vehicle_height_offset / 1000.0

    L_m = L_mm / 1000.0
    W_m = W_mm / 1000.0
    h_m = h_mm / 1000.0
    x0_m = center_x_m - L_m / 2
    y0_m = center_y_m - W_m / 2
    out = {
        "name": getattr(item, "name", "?"),
        "kind": _classify_item(item),
        "posture": "lying",
        "slot": "floor",
        "center_x_m": center_x_m,
        "center_y_m": center_y_m,
        "z_bottom_m": z_bottom_m,
        "length_m": L_m,
        "width_m": W_m,
        "height_m": h_m,
        "weight_kg": float(getattr(item, "weight", 0.0)),
        "wall_segments": [],
        "attached_parts": [],
        "body_parts": [],
    }
    out["body_parts"] = _serialize_body_parts(item, x0_m, y0_m, z_bottom_m)
    parts = tuple(getattr(item, "attached_parts", ()) or ())
    out["attached_parts"] = [
        _serialize_attached_part(prt, x0_m, y0_m, z_bottom_m) for prt in parts
    ]
    return out


# ── 그룹별 색상 팔레트 (동일 결과 그룹 표시용) ───────────────────
_GROUP_COLORS = [
    "#58a6ff", "#3fb950", "#d29922", "#bc8cff", "#ff7b72",
    "#79c0ff", "#56d364", "#e3b341", "#a371f7", "#ffa657",
]


def serialize_pack_for_three(
    trips: List[Trip],
    sp: Optional[SpacingParams] = None,
    highlight_trip_no: Optional[int] = None,
    trip_costs: Optional[Dict[int, float]] = None,
) -> Dict:
    """PackResult.trips → Three.js scene 구축용 JSON dict.

    [배치 규칙 — 2026-05-26 사용자 결정]
    - *같은 트럭 + 같은 화물 구성* 인 회차들은 *같은 그룹* → 진행 방향(+x) 으로 일렬
    - 다른 그룹끼리는 *폭 방향(+z = Three.js Z = Python Y)* 으로 분리
    - 그룹 간 간격 = INTER_TRUCK_GAP_M (4 m), 그룹 내 간격 = INTRA_GROUP_GAP_M (2 m)
    """
    if sp is None:
        sp = SpacingParams()
    trip_costs = trip_costs or {}

    # 1차 패스 — 시그니처 + 그룹 ID 할당
    group_id_for_trip: Dict[int, int] = {}
    sig_to_group: Dict[tuple, int] = {}
    for trip in trips:
        sig = _trip_signature(trip)
        if sig not in sig_to_group:
            sig_to_group[sig] = len(sig_to_group)
        group_id_for_trip[trip.trip_no] = sig_to_group[sig]

    n_groups = len(sig_to_group)

    # 2차 패스 — 다른 그룹은 *진행 방향 (X)* 분리, 같은 그룹은 *폭 방향 (-Y)* 누적.
    group_x_offset_mm: Dict[int, float] = {}
    group_count_so_far: Dict[int, int] = {}
    next_x_mm = 0.0

    data = {
        "trips": [],
        "inter_truck_gap_m": INTER_TRUCK_GAP_M,
        "intra_group_gap_m": INTRA_GROUP_GAP_M,
        "highlight_trip_no": highlight_trip_no,
        "n_groups": n_groups,
        "group_colors": _GROUP_COLORS,
    }

    for trip in trips:
        gid = group_id_for_trip[trip.trip_no]
        truck = trip.truck
        truck_length_m = truck.max_length / 1000.0
        veh_h_m = truck.vehicle_height_offset / 1000.0

        # 새 그룹 — X 방향으로 새 자리
        if gid not in group_x_offset_mm:
            group_x_offset_mm[gid] = next_x_mm
            group_count_so_far[gid] = 0
            next_x_mm += truck.max_length + INTER_TRUCK_GAP_M * 1000.0
        # 같은 그룹 내 N 번째 회차 — *폭 방향 (-Y)* 으로 누적
        # Python -Y = Three.js -Z = 사이드뷰 카메라(기본 +Z 쪽) 의 *화면 깊이*
        # 사용자 표현: "사이드뷰에서 뒤쪽 = 트럭 오른쪽"
        n_in_group = group_count_so_far[gid]
        x_off_mm = group_x_offset_mm[gid]
        y_off_mm = -n_in_group * (
            truck.max_width + INTRA_GROUP_GAP_M * 1000.0
        )
        group_count_so_far[gid] += 1

        # placements 우선
        placements_json: List[Dict] = []
        if trip.placements:
            for p in trip.placements:
                placements_json.append(_serialize_placement(p, truck))
        else:
            edge_mm = sp.truck_edge_clearance_mm
            gap_mm = sp.panel_gap_mm
            cursor_mm = edge_mm
            for it in trip.items:
                placements_json.append(_serialize_legacy_item(
                    it, truck, cursor_mm, sp,
                ))
                cursor_mm += getattr(it, "length", 0.0) + gap_mm

        trip_data = {
            "trip_no": trip.trip_no,
            "truck_name": truck.name,
            "truck_type": truck.truck_type,
            "x_offset_m": x_off_mm / 1000.0,
            "y_offset_m": y_off_mm / 1000.0,
            "group_id": gid,
            "group_size_in_group": n_in_group + 1,  # 1-based 표시용 — "1/3 동일"
            "truck_length_m": truck_length_m,
            "truck_width_m": truck.max_width / 1000.0,
            "truck_height_m": truck.max_height / 1000.0,
            "vehicle_height_offset_m": veh_h_m,
            "cargo_weight_kg": float(trip.cargo_weight),
            "utilization": float(trip.utilization),
            "cost_krw": trip_costs.get(trip.trip_no),
            "placements": placements_json,
        }
        data["trips"].append(trip_data)

    # 각 그룹의 총 회차 수 추가 (라벨에 "1/3 동일" 표시용)
    for trip_data in data["trips"]:
        gid = trip_data["group_id"]
        trip_data["group_total"] = group_count_so_far[gid]
    return data


# ════════════════════════════════════════════════════════════════════
# HTML 템플릿 — Three.js + OrbitControls + CSS2DRenderer
# ════════════════════════════════════════════════════════════════════
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>운송 적재 3D 도식</title>
<style>
  html, body { margin: 0; padding: 0; overflow: hidden; background: #0d1117;
               width: 100%; height: 100%; font-family: 'Segoe UI', sans-serif; }
  #container { width: 100vw; height: 100vh; position: relative; }
  #renderer-canvas { display: block; width: 100%; height: 100%; }
  .label { color: #e6edf3; pointer-events: none; }
  .label-trip {
    background: rgba(13, 17, 23, 0.88);
    padding: 6px 10px;
    border-radius: 6px;
    border: 1px solid #30363d;
    color: #e6edf3;
    text-align: center;
    min-width: 100px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    font-size: 12px;
  }
  .label-trip.highlight {
    border-color: #f85149;
    box-shadow: 0 0 12px rgba(248,81,73,0.6);
  }
  .label-trip.dimmed {
    opacity: 0.6;
    transition: opacity 0.25s ease-out;
  }
  .label-trip { transition: opacity 0.25s ease-out; }
  .label-cargo.dimmed { opacity: 0.5; transition: opacity 0.25s ease-out; }
  .label-cargo { transition: opacity 0.25s ease-out; }
  .label-cargo {
    color: #c9d1d9;
    font-size: 10px;
    text-shadow: 0 1px 3px rgba(0,0,0,0.95);
  }
  #toolbar {
    position: absolute; top: 12px; left: 12px; z-index: 10;
    display: flex; gap: 6px;
  }
  #toolbar button {
    padding: 7px 14px;
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
    font-weight: 500;
  }
  #toolbar button:hover { background: #30363d; border-color: #6e7681; }
  #toolbar button.active { background: #1f6feb; border-color: #1f6feb; color: white; }
  #info {
    position: absolute; bottom: 12px; right: 12px; z-index: 10;
    background: rgba(13,17,23,0.85); color: #8b949e; padding: 8px 12px;
    border-radius: 6px; border: 1px solid #30363d; font-size: 11px;
    line-height: 1.5;
  }
  #empty-msg {
    position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
    color: #8b949e; font-size: 16px; text-align: center;
    background: rgba(13,17,23,0.7); padding: 24px 36px; border-radius: 12px;
    border: 1px solid #30363d;
  }
</style>
</head>
<body>
<div id="container">
  <div id="toolbar">
    <button onclick="setCameraPreset('iso')" id="btn-iso" class="active">🔄 Iso</button>
    <button onclick="setCameraPreset('top')" id="btn-top">⬇ Top</button>
    <button onclick="setCameraPreset('side')" id="btn-side">➡ Side</button>
    <button onclick="setCameraPreset('front')" id="btn-front">⬆ Front</button>
    <button onclick="fitAllTrucks()" id="btn-fit">⊡ Fit</button>
  </div>
  <div id="info">왼클릭 = 회전 · 우클릭 = 이동 · 휠 = 줌</div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/renderers/CSS2DRenderer.js"></script>
<!-- Qt WebChannel — Python ↔ JS 양방향 통신 -->
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
const PACK_DATA = __PACK_DATA__;

// 색상 팔레트
const COLORS = {
  module:    0x4a90e2,
  floor:     0x66bb6a,
  dep_floor: 0xff9800,
  wall:      0xab47bc,
  wall_seg:  0x8d6e63,
  // Phase 3 — 사실적 부재 색
  steel:     0x808080,  // 기둥·보 (강재)
  concrete:  0xc0c0c0,  // 슬래브 (콘크리트)
  cant_beam: 0xff6e40,  // 캔틸레버 보 (강조)
  cant_slab: 0xffab40,  // 캔틸레버 슬래브 (강조)
  midbeam:   0x6e7681,  // 중간보 (약간 어두운 회색)
  midcolumn: 0x6e7681,  // 중간기둥 (동일)
  cab: { lowbed: 0x1976d2, extendable: 0x388e3c, aframe: 0xe64a19 },
};

let scene, camera, renderer, controls, labelRenderer;
let truckGroups = {};         // trip_no → THREE.Group
let highlightOutlines = {};   // trip_no → LineSegments (강조 외곽선)
let allCargoMeshes = [];      // raycaster 후보
let groupLabelDivs = {};      // group_id → HTMLDivElement (그룹별 *1 개* 대표 라벨)
let groupTripsCache = {};     // group_id → [trip 객체 배열] (라벨 갱신용 캐시)

const PRESETS = {
  iso:   { pos: [1.5, 1.0, 1.5], up: [0, 1, 0] },
  top:   { pos: [0.0, 2.4, 0.01], up: [0, 0, -1] },
  side:  { pos: [0.0, 0.3, 2.4], up: [0, 1, 0] },
  front: { pos: [-2.4, 0.3, 0.0], up: [0, 1, 0] },
};

// ─────────────────────────────────────────────────────────────
// Qt WebChannel bridge — Python ↔ JS. JS 가 트럭 클릭 시 bridge.on_truck_clicked
// 호출 → Python 이 회차표·경제성표 행 선택 + 3D 강조 트리거.
// ─────────────────────────────────────────────────────────────
let qtBridge = null;
function _initWebChannel() {
  if (typeof QWebChannel === 'undefined' || typeof qt === 'undefined' || !qt.webChannelTransport) {
    // 브리지 없음 (브라우저에서 단독 열기 등) — JS 만으로 동작
    return;
  }
  new QWebChannel(qt.webChannelTransport, function(channel) {
    qtBridge = channel.objects.bridge || null;
  });
}

// 마우스 클릭 vs 드래그 구분 (OrbitControls 와 충돌 방지)
const _raycaster = new THREE.Raycaster();
const _mouse = new THREE.Vector2();
let _mouseDownPos = { x: 0, y: 0 };
let _isDragging = false;

function _onMouseDown(e) {
  _isDragging = false;
  _mouseDownPos.x = e.clientX;
  _mouseDownPos.y = e.clientY;
}
function _onMouseMove(e) {
  if (e.buttons === 0) return;
  const dx = e.clientX - _mouseDownPos.x;
  const dy = e.clientY - _mouseDownPos.y;
  if (dx * dx + dy * dy > 25) _isDragging = true;  // 5px 이상 = 드래그
}
function _onMouseClick(e) {
  if (_isDragging) return;  // 드래그 후 click 무시
  if (e.button !== 0) return;  // 좌클릭만
  const rect = renderer.domElement.getBoundingClientRect();
  _mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  _mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  _raycaster.setFromCamera(_mouse, camera);

  // 모든 trip 의 *클릭 가능* mesh 수집 (트럭 본체 + 화물)
  const targets = [];
  for (const tno in truckGroups) {
    truckGroups[tno].traverse(obj => {
      if (obj.isMesh && obj.userData.role) targets.push(obj);
    });
  }
  const hits = _raycaster.intersectObjects(targets, false);
  if (hits.length === 0) return;
  // 부모 group 의 userData.trip_no
  let obj = hits[0].object;
  while (obj && obj.userData.trip_no === undefined) obj = obj.parent;
  if (!obj || obj.userData.trip_no === undefined) return;
  const tno = obj.userData.trip_no;
  // Python 으로 신호 전달 — 없으면 자체 강조만
  if (qtBridge && qtBridge.on_truck_clicked) {
    qtBridge.on_truck_clicked(tno);
  } else {
    highlightTrip(tno);
  }
}

function init() {
  const container = document.getElementById('container');
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1117);
  scene.fog = new THREE.FogExp2(0x0d1117, 0.012);

  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 800);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.id = 'renderer-canvas';
  container.appendChild(renderer.domElement);

  labelRenderer = new THREE.CSS2DRenderer();
  labelRenderer.domElement.style.cssText =
    'position:absolute;top:0;left:0;pointer-events:none;width:100%;height:100%;';
  container.appendChild(labelRenderer.domElement);

  // 광원 — modular_designer.html 동일 스타일
  scene.add(new THREE.AmbientLight(0xffffff, 0.42));
  const hemi = new THREE.HemisphereLight(0x8899aa, 0x223344, 0.55);
  scene.add(hemi);
  const dirMain = new THREE.DirectionalLight(0xffffff, 0.95);
  dirMain.position.set(30, 50, 25);
  dirMain.castShadow = true;
  dirMain.shadow.mapSize.set(2048, 2048);
  const sd = 80;
  dirMain.shadow.camera.left = -sd; dirMain.shadow.camera.right = sd;
  dirMain.shadow.camera.top = sd; dirMain.shadow.camera.bottom = -sd;
  dirMain.shadow.camera.near = 0.5; dirMain.shadow.camera.far = 200;
  scene.add(dirMain);
  const dirFill = new THREE.DirectionalLight(0x8899cc, 0.32);
  dirFill.position.set(-20, 15, -15);
  scene.add(dirFill);

  // 바닥 plane (그림자 받는 면 — 그리드는 사용자 요청대로 없음)
  const groundSize = 300;
  const gnd = new THREE.Mesh(
    new THREE.PlaneGeometry(groundSize, groundSize),
    new THREE.MeshLambertMaterial({ color: 0x111318 })
  );
  gnd.rotation.x = -Math.PI / 2;
  gnd.receiveShadow = true;
  scene.add(gnd);

  // OrbitControls — vispy 비슷한 조작 (왼클릭 회전, 우클릭 이동, 휠 줌)
  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 1.5;
  controls.maxDistance = 300;
  controls.mouseButtons = {
    LEFT:  THREE.MOUSE.ROTATE,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.PAN,
  };

  // Scene 구축
  buildScene(PACK_DATA);

  if (PACK_DATA && PACK_DATA.highlight_trip_no !== null) {
    highlightTrip(PACK_DATA.highlight_trip_no);
  }

  // 자동 카메라 fit
  fitAllTrucks();
  setCameraPreset('iso');

  window.addEventListener('resize', onResize);
  onResize();
  // 마우스 클릭 핸들러 + WebChannel
  renderer.domElement.addEventListener('mousedown', _onMouseDown);
  renderer.domElement.addEventListener('mousemove', _onMouseMove);
  renderer.domElement.addEventListener('click', _onMouseClick);
  _initWebChannel();
  animate();
}

function onResize() {
  const container = document.getElementById('container');
  const w = container.clientWidth, h = container.clientHeight;
  if (w < 2 || h < 2) return;
  renderer.setSize(w, h, false);
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
}

// ─────────────────────────────────────────────────────────────
// Scene 빌드
// ─────────────────────────────────────────────────────────────
function buildScene(data) {
  if (!data || !data.trips || data.trips.length === 0) {
    const emptyDiv = document.createElement('div');
    emptyDiv.id = 'empty-msg';
    emptyDiv.innerHTML = '🚚<br><br><b>회차 없음</b><br>' +
                         '<span style="color:#6e7681;font-size:13px;">' +
                         '운송 계산을 먼저 실행해주세요</span>';
    document.getElementById('container').appendChild(emptyDiv);
    return;
  }
  for (const trip of data.trips) {
    const grp = buildTruckGroup(trip);
    scene.add(grp);
    truckGroups[trip.trip_no] = grp;
  }
  // 그룹별 *1 개* 라벨 추가
  _addGroupLabels(data);
}

// Python 좌표 (x=길이, y=폭, z=높이) → Three.js 좌표 (x, y=z_py, z=y_py)
// 즉 *수직* = Three.js Y, *폭* = Three.js Z, *길이* = Three.js X.
function pyToThree(x, y, z) {
  return { x: x, y: z, z: y };
}

function buildTruckGroup(trip) {
  const g = new THREE.Group();
  g.userData.trip_no = trip.trip_no;
  g.userData.cargoLabels = [];
  // [그룹화] Y 오프셋 (폭 방향 = Three.js Z) 적용
  if (trip.y_offset_m) {
    g.position.z = trip.y_offset_m;
  }

  const xo = trip.x_offset_m;
  const tw = trip.truck_width_m;
  const tl = trip.truck_length_m;
  const veh_h = trip.vehicle_height_offset_m;
  const inner_h = trip.truck_height_m - veh_h;

  // ① 적재함 외곽선 (LineSegments)
  const bedGeo = new THREE.BoxGeometry(tl, inner_h, tw);
  const bedEdges = new THREE.EdgesGeometry(bedGeo);
  const bedLine = new THREE.LineSegments(
    bedEdges,
    new THREE.LineBasicMaterial({ color: 0x9ca3af })
  );
  bedLine.position.set(xo + tl/2, veh_h + inner_h/2, tw/2);
  g.add(bedLine);

  // ② 적재함 바닥 판 (얇은 회색)
  const floorTh = 0.08;
  const bedFloor = new THREE.Mesh(
    new THREE.BoxGeometry(tl, floorTh, tw),
    new THREE.MeshStandardMaterial({ color: 0x4a5568, roughness: 0.85 })
  );
  bedFloor.position.set(xo + tl/2, veh_h - floorTh/2, tw/2);
  bedFloor.receiveShadow = true;
  bedFloor.castShadow = true;
  bedFloor.userData.baseOpacity = 1.0;
  bedFloor.userData.role = 'truck';
  g.add(bedFloor);

  // ③ 캐빈 — 바퀴 위에서 시작
  const cabColor = COLORS.cab[trip.truck_type] || 0x616161;
  const cabLen = 2.4, cabH = 2.2;
  const cabX0 = xo - cabLen - 0.2;
  const cab = new THREE.Mesh(
    new THREE.BoxGeometry(cabLen, cabH, tw),
    new THREE.MeshStandardMaterial({ color: cabColor, roughness: 0.65, metalness: 0.1 })
  );
  cab.position.set(cabX0 + cabLen/2, veh_h + cabH/2, tw/2);
  cab.castShadow = true;
  cab.receiveShadow = true;
  cab.userData.baseOpacity = 1.0;
  cab.userData.role = 'truck';
  g.add(cab);
  // 캐빈 윈드실드 (앞면 어둡게)
  const wsGeo = new THREE.BoxGeometry(0.05, cabH * 0.55, tw * 0.85);
  const ws = new THREE.Mesh(
    wsGeo,
    new THREE.MeshStandardMaterial({ color: 0x0a0e14, roughness: 0.2, metalness: 0.4 })
  );
  ws.position.set(cabX0 + 0.02, veh_h + cabH * 0.6, tw / 2);
  g.add(ws);

  // ④ 바퀴 4 쌍 — 캐빈 앞바퀴 + 적재함 하단 3 쌍
  const wheelR = Math.min(veh_h * 0.5, 0.45);
  const wheelTh = 0.32;
  const wheelXs = [
    cabX0 + cabLen * 0.3,
    xo + 0.5,
    xo + tl / 2 - wheelTh / 2,
    xo + tl - 0.5 - 0.6,
  ];
  const wheelGeo = new THREE.CylinderGeometry(wheelR, wheelR, wheelTh, 24);
  const wheelMat = new THREE.MeshStandardMaterial({ color: 0x1a1a1a, roughness: 0.95 });
  // CylinderGeometry 는 기본 y축 정렬 → x축 회전 시 z축 정렬됨 → 폭 방향 일치
  for (const wx of wheelXs) {
    for (const zPos of [-0.05, tw + 0.05]) {
      const w = new THREE.Mesh(wheelGeo, wheelMat.clone());
      w.rotation.x = Math.PI / 2;
      w.position.set(wx + wheelTh / 2, wheelR, zPos);
      w.castShadow = true;
      w.userData.baseOpacity = 1.0;
      w.userData.role = 'truck';
      g.add(w);
    }
  }

  // ⑤ A-frame 트럭 — 양옆 프레임
  if (trip.truck_type === 'aframe') {
    const frameH = inner_h + 1.5;
    const frameTh = 0.2;
    for (const side of ['left', 'right']) {
      const zPos = side === 'left' ? frameTh / 2 : tw - frameTh / 2;
      const frame = new THREE.Mesh(
        new THREE.BoxGeometry(tl * 0.4, frameH, frameTh),
        new THREE.MeshStandardMaterial({
          color: 0xe64a19, roughness: 0.7, transparent: true, opacity: 0.55,
        })
      );
      frame.position.set(xo + tl * 0.5, veh_h + frameH / 2, zPos);
      frame.castShadow = true;
      g.add(frame);
    }
  }

  // ⑥ 화물 — placements
  for (const p of trip.placements || []) {
    buildPlacement(g, trip, p);
  }

  // 회차 라벨은 *그룹별 1 개* 로 buildScene 에서 별도 배치 — 여기선 안 넣음.
  return g;
}

// ─────────────────────────────────────────────────────────────
// 그룹 라벨 — 같은 그룹은 *1 개* 라벨로 통합. 위치는 그룹의 *깊이 방향 끝*
// 뒤(=다음 트럭이 있을 자리). 클릭 시 그 회차 정보로 동적 갱신.
// ─────────────────────────────────────────────────────────────
function renderGroupLabelHtml(tripsInGroup, focusedTrip) {
  // focusedTrip 가 주어지면 그 회차 정보, 아니면 그룹 요약
  if (focusedTrip) {
    const t = focusedTrip;
    const costStr = (t.cost_krw !== null && t.cost_krw !== undefined)
      ? `<br>₩${Math.round(t.cost_krw).toLocaleString()}` : '';
    const sizeBadge = tripsInGroup.length > 1
      ? `<span style="background:#1f6feb33;color:#58a6ff;` +
        `padding:1px 5px;border-radius:3px;font-size:10px;margin-left:6px;">` +
        `${tripsInGroup.length}대 동일</span>` : '';
    return (
      `<div style="font-weight:600;font-size:13px;">` +
      `#${t.trip_no} · ${t.truck_name}${sizeBadge}</div>` +
      `<div style="color:#8b949e;margin-top:2px;font-size:11px;">` +
      `${Math.round(t.cargo_weight_kg)} kg · ${t.utilization.toFixed(0)}%${costStr}</div>`
    );
  }
  // 디폴트 — 그룹 요약
  if (tripsInGroup.length === 1) {
    const t = tripsInGroup[0];
    const costStr = (t.cost_krw !== null && t.cost_krw !== undefined)
      ? `<br>₩${Math.round(t.cost_krw).toLocaleString()}` : '';
    return (
      `<div style="font-weight:600;font-size:13px;">#${t.trip_no} · ${t.truck_name}</div>` +
      `<div style="color:#8b949e;margin-top:2px;font-size:11px;">` +
      `${Math.round(t.cargo_weight_kg)} kg · ${t.utilization.toFixed(0)}%${costStr}</div>`
    );
  }
  // 여러 회차 동일 — 회차 범위 + 그룹 요약
  const tripNos = tripsInGroup.map(t => t.trip_no).sort((a, b) => a - b);
  const t0 = tripsInGroup[0];
  const lastNo = tripNos[tripNos.length - 1];
  const isContiguous = (tripNos.length === lastNo - tripNos[0] + 1);
  const rangeStr = isContiguous
    ? `#${tripNos[0]}~${lastNo}`
    : `#${tripNos.join(',')}`;
  const totalKg = tripsInGroup.reduce((s, t) => s + t.cargo_weight_kg, 0);
  const avgUtil = tripsInGroup.reduce((s, t) => s + t.utilization, 0) / tripsInGroup.length;
  const totalCost = tripsInGroup.reduce(
    (s, t) => s + (t.cost_krw !== null && t.cost_krw !== undefined ? t.cost_krw : 0), 0);
  const costStr = totalCost > 0
    ? `<br>총 ₩${Math.round(totalCost).toLocaleString()}` : '';
  return (
    `<div style="font-weight:600;font-size:13px;">${rangeStr} · ${t0.truck_name} ` +
    `<span style="background:#1f6feb33;color:#58a6ff;padding:1px 5px;` +
    `border-radius:3px;font-size:10px;">${tripsInGroup.length}대 동일</span></div>` +
    `<div style="color:#8b949e;margin-top:2px;font-size:11px;">` +
    `평균 ${Math.round(totalKg/tripsInGroup.length)} kg · ${avgUtil.toFixed(0)}%${costStr}</div>`
  );
}

function _addGroupLabels(data) {
  // 그룹별 trips 캐시 + 라벨 위치 계산
  groupTripsCache = {};
  for (const trip of data.trips) {
    if (!(trip.group_id in groupTripsCache)) groupTripsCache[trip.group_id] = [];
    groupTripsCache[trip.group_id].push(trip);
  }
  for (const gidStr in groupTripsCache) {
    const gid = Number(gidStr);
    const tripsInGroup = groupTripsCache[gid];
    const first = tripsInGroup[0];
    const lblDiv = document.createElement('div');
    lblDiv.className = 'label label-trip';
    lblDiv.dataset.groupId = String(gid);
    lblDiv.innerHTML = renderGroupLabelHtml(tripsInGroup, null);
    const lblObj = new THREE.CSS2DObject(lblDiv);
    // 위치:
    //  - X = 그룹 첫 회차의 트럭 X 중앙
    //  - Y(높이) = 적재함 중앙 (트럭 위 X)
    //  - Z = *그룹 깊이 끝 + 한 트럭 폭만큼 더 뒤* (다음 트럭이 있을 자리)
    const minZ = Math.min(...tripsInGroup.map(t => t.y_offset_m || 0));
    const labelX = first.x_offset_m + first.truck_length_m / 2;
    const labelY = first.vehicle_height_offset_m +
                   (first.truck_height_m - first.vehicle_height_offset_m) / 2;
    // 사용자 결정: 그룹 끝 + *두 칸* 뒤 (다음 다음 트럭 자리 정도)
    const labelZ = minZ - 2 * (first.truck_width_m + 1.5);
    lblObj.position.set(labelX, labelY, labelZ);
    scene.add(lblObj);
    groupLabelDivs[gid] = lblDiv;
  }
}

// H형강 보 — 박스 대신 I자 단면을 길이방향으로 압출. (2026-05-28)
// 좌표·치수 규약은 _addPyBox 와 동일(Python x=길이축성분, y=폭, z=높이 → three
// X=길이, Y=높이, Z=폭). 단면은 200×200 고정(웨브 t1=8, 플랜지 t2=12mm).
// webDirPy: *운송(Python) 좌표계* 웨브(강축) 단위벡터 [dx,dy,dz]. 건물 단면을
// 그대로 회전시킨 값이라, 이 방향으로 웨브를 세워 강축/약축을 정확히 표현한다.
function _addBeamHsection(g, trip, x_m, y_m, z_m, L_m, W_m, H_m, webDirPy, color, opts) {
  opts = opts || {};
  const T1 = 0.008, T2 = 0.012;   // 웨브/플랜지 두께(m) — H200×200 카탈로그
  // 박스 중심 (three 좌표)
  const cx = trip.x_offset_m + x_m + L_m / 2;
  const cy = z_m + H_m / 2;
  const cz = y_m + W_m / 2;
  // three 축별 박스 치수: [X=길이방향, Y=높이, Z=폭]
  const dims = [L_m, H_m, W_m];
  // 길이축 = 최장축
  let lenAxis = 0;
  if (dims[1] > dims[lenAxis]) lenAxis = 1;
  if (dims[2] > dims[lenAxis]) lenAxis = 2;
  // 웨브축(three) — webDirPy(Python x,y,z) → three(x, z, y)
  const wt = [webDirPy[0], webDirPy[2], webDirPy[1]];
  let webAxis = 0;
  for (let i = 1; i < 3; i++) if (Math.abs(wt[i]) > Math.abs(wt[webAxis])) webAxis = i;
  if (webAxis === lenAxis) {
    // 안전장치 — 직교가 깨지면 박스로 폴백.
    _addPyBox(g, trip, x_m, y_m, z_m, L_m, W_m, H_m, color, opts);
    return;
  }
  const flangeAxis = 3 - lenAxis - webAxis;  // 나머지 축
  const length = dims[lenAxis];
  const Hsec = dims[webAxis];     // 단면 높이(웨브 방향)
  const Bsec = dims[flangeAxis];  // 플랜지 폭

  // I자 2D 단면 (로컬 X=플랜지폭, Y=웨브높이) — mesh_builder.build_h_section 과 동일.
  const hb = Bsec / 2, hh = Hsec / 2, tw = T1 / 2, hw = hh - T2;
  const shape = new THREE.Shape();
  const pts = [
    [-hb, -hh], [hb, -hh], [hb, -hw], [tw, -hw],
    [tw, hw], [hb, hw], [hb, hh], [-hb, hh],
    [-hb, hw], [-tw, hw], [-tw, -hw], [-hb, -hw],
  ];
  shape.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) shape.lineTo(pts[i][0], pts[i][1]);
  shape.closePath();
  const geom = new THREE.ExtrudeGeometry(shape, { depth: length, bevelEnabled: false });
  geom.translate(0, 0, -length / 2);  // 길이 중심 정렬(로컬 Z)

  // 로컬축(X=플랜지, Y=웨브, Z=길이) → three 월드축 정렬 회전행렬.
  // [중요] 축 단위벡터를 그대로 makeBasis 하면 길이축이 X 인 경우 행렬식이
  // -1(반사)이 되어 setRotationFromMatrix 가 90° 틀어진 회전으로 근사한다.
  // 플랜지 방향을 (웨브 × 길이) 외적으로 구해 항상 오른손 좌표(det=+1)를 보장.
  const e = (i) => {
    const v = new THREE.Vector3(0, 0, 0);
    v.setComponent(i, 1);
    return v;
  };
  const lenVec = e(lenAxis);
  const webVec = e(webAxis);
  if (wt[webAxis] < 0) webVec.multiplyScalar(-1);
  const flangeVec = new THREE.Vector3().crossVectors(webVec, lenVec).normalize();
  const basis = new THREE.Matrix4().makeBasis(flangeVec, webVec, lenVec);

  const mat = new THREE.MeshStandardMaterial({
    color: color,
    roughness: opts.roughness !== undefined ? opts.roughness : 0.7,
    metalness: opts.metalness !== undefined ? opts.metalness : 0.0,
    transparent: true,
    opacity: opts.opacity !== undefined ? opts.opacity : 0.95,
  });
  const mesh = new THREE.Mesh(geom, mat);
  mesh.setRotationFromMatrix(basis);
  mesh.position.set(cx, cy, cz);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData.baseOpacity = mat.opacity;
  mesh.userData.role = opts.role || 'cargo';
  if (opts.label) mesh.userData.label = opts.label;
  g.add(mesh);
  if (opts.role === 'cargo' || !opts.role) allCargoMeshes.push(mesh);
  if (opts.withEdges !== false) {
    const edges = new THREE.EdgesGeometry(mesh.geometry);
    const edgeColor = opts.edgeColor !== undefined ? opts.edgeColor : 0x222222;
    const line = new THREE.LineSegments(
      edges, new THREE.LineBasicMaterial({ color: edgeColor })
    );
    line.setRotationFromMatrix(basis);
    line.position.copy(mesh.position);
    g.add(line);
  }
  return mesh;
}

// 보 부재 1개를 그린다 — H형강이면 I자, 아니면 박스. (분기 진입점)
function _addPartMesh(g, trip, part, color, opts) {
  if (part.kind === 'beam' && part.section_type === 'h' && part.web_dir) {
    _addBeamHsection(g, trip, part.x_m, part.y_m, part.z_m,
                     part.length_m, part.width_m, part.height_m,
                     part.web_dir, color, opts);
  } else {
    _addPyBox(g, trip, part.x_m, part.y_m, part.z_m,
              part.length_m, part.width_m, part.height_m, color, opts);
  }
}

// Python(x=길이,y=폭,z=높이,m) 좌표의 박스 → Three.js mesh (Y=높이, Z=폭).
// trip.x_offset_m 은 *그룹 X 오프셋* (회차마다 다름) — 본 함수는 *trip 로컬* 좌표
// (즉 트럭 좌측 끝 기준) 의 좌하단을 받아 trip.x_offset_m 만 더해 배치.
function _addPyBox(g, trip, x_m, y_m, z_m, L_m, W_m, H_m, color, opts) {
  opts = opts || {};
  const cx = trip.x_offset_m + x_m + L_m / 2;
  const cy = z_m + H_m / 2;
  const cz = y_m + W_m / 2;
  const mat = new THREE.MeshStandardMaterial({
    color: color,
    roughness: opts.roughness !== undefined ? opts.roughness : 0.7,
    metalness: opts.metalness !== undefined ? opts.metalness : 0.0,
    transparent: true,
    opacity: opts.opacity !== undefined ? opts.opacity : 0.95,
  });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(L_m, H_m, W_m), mat);
  mesh.position.set(cx, cy, cz);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData.baseOpacity = mat.opacity;
  mesh.userData.role = opts.role || 'cargo';
  if (opts.label) mesh.userData.label = opts.label;
  g.add(mesh);
  if (opts.role === 'cargo' || !opts.role) allCargoMeshes.push(mesh);
  if (opts.withEdges !== false) {
    const edges = new THREE.EdgesGeometry(mesh.geometry);
    const edgeColor = opts.edgeColor !== undefined ? opts.edgeColor : 0x222222;
    const line = new THREE.LineSegments(
      edges, new THREE.LineBasicMaterial({ color: edgeColor })
    );
    line.position.copy(mesh.position);
    g.add(line);
  }
  return mesh;
}

// 부재의 (kind, subkind, source_kind, 부모 화물 종류) 로부터 색상·라벨·재질 결정.
// source_kind='own' 이면 부모 자체 부재, 그 외면 캔틸레버/중간 부재.
function _stylePart(part, parent, source) {
  const sk = source || 'own';
  const k = part.kind;
  const sub = part.subkind || '';
  let color, label, opts = { roughness: 0.5, metalness: 0.6, opacity: 0.95 };

  // 라벨 — 부재 종류 + subkind
  let kindKo = k;
  if (k === 'column') kindKo = '기둥';
  else if (k === 'beam') {
    if (sub === 'bottom') kindKo = '하부보';
    else if (sub === 'top') kindKo = '상부보';
    else if (sub === 'perim') kindKo = '둘레보';
    else if (sub === 'runner') kindKo = '런너';
    else kindKo = '보';
  } else if (k === 'slab') kindKo = '슬래브';
  else if (k === 'wall_fill') kindKo = '채움';

  // 색상 — source_kind 별
  if (sk === 'cantilever_beam') {
    color = COLORS.cant_beam;
    label = `${parent.name} 캔틸보`;
  } else if (sk === 'cantilever_slab') {
    color = (k === 'slab') ? COLORS.cant_slab : COLORS.cant_beam;
    label = `${parent.name} 캔틸${kindKo}`;
    if (k === 'slab') { opts.roughness = 0.85; opts.opacity = 0.92; }
  } else if (sk === 'midbeam') {
    color = COLORS.midbeam;
    label = `${parent.name} 중간보`;
  } else if (sk === 'midcolumn') {
    color = COLORS.midcolumn;
    label = `${parent.name} 중간기둥`;
  } else {
    // own — 부모 자체 부재
    if (k === 'column' || k === 'beam') {
      color = COLORS.steel;
    } else if (k === 'slab') {
      color = (parent.kind === 'module') ? COLORS.concrete : COLORS.floor;
      opts.roughness = 0.85; opts.opacity = 0.9;
    } else if (k === 'wall_fill') {
      color = (parent.kind === 'wall') ? COLORS.wall : 0xa0a0a0;
      opts.roughness = 0.8; opts.opacity = 0.82;
    } else if (k === 'core_wall') {
      color = 0x888888; opts.roughness = 0.8; opts.opacity = 0.92;
    } else {
      color = 0x999999;
    }
    label = `${parent.name} ${kindKo}`;
  }
  opts.label = label;
  return { color, opts };
}


function buildPlacement(g, trip, p) {
  const cx_t = p.center_x_m + trip.x_offset_m;
  const cy_t = p.z_bottom_m + p.height_m / 2;
  const cz_t = p.center_y_m;
  // 본체 좌하단 (trip 로컬, m)
  const x0 = p.center_x_m - p.length_m / 2;
  const y0 = p.center_y_m - p.width_m / 2;
  const z0 = p.z_bottom_m;

  // ── 부재별 mesh — body_parts (어댑터 실측 좌표, source='own') ─
  const bodyParts = p.body_parts || [];
  if (bodyParts.length > 0) {
    for (const bp of bodyParts) {
      const styled = _stylePart(bp, p, 'own');
      _addPartMesh(g, trip, bp, styled.color, styled.opts);
    }
  } else {
    // Fallback — body_parts 없는 화물 단일 박스
    const baseColor = COLORS[p.kind] || 0x999999;
    _addPyBox(g, trip, x0, y0, z0,
              p.length_m, p.width_m, p.height_m,
              baseColor, { label: `${p.name} (${p.kind})`, roughness: 0.7, opacity: 0.92 });
  }

  // 화물 라벨 (모든 placement 공통 — 박스 위)
  const cargoLbl = document.createElement('div');
  cargoLbl.className = 'label label-cargo';
  cargoLbl.textContent = p.name;
  const cargoLblObj = new THREE.CSS2DObject(cargoLbl);
  cargoLblObj.position.set(cx_t, cy_t + p.height_m / 2 + 0.15, cz_t);
  g.add(cargoLblObj);
  if (g.userData.cargoLabels) g.userData.cargoLabels.push(cargoLbl);

  // ── attached_parts — 종속 컴포넌트의 *모든 부재* (보·기둥·슬래브 별도) ─
  for (const ap of (p.attached_parts || [])) {
    const styled = _stylePart(ap, p, ap.source_kind || 'own');
    _addPartMesh(g, trip, ap, styled.color, styled.opts);
  }

  // wall_segments — 종속 floor 의 솟은 벽
  for (const ws of p.wall_segments) {
    const wsx = trip.x_offset_m + ws.x_m + ws.dx_m / 2;
    const wsy = ws.z_m + ws.dz_m / 2;  // Y = 높이
    const wsz = ws.y_m + ws.dy_m / 2;  // Z = 폭
    const wsMesh = new THREE.Mesh(
      new THREE.BoxGeometry(ws.dx_m, ws.dz_m, ws.dy_m),
      new THREE.MeshStandardMaterial({
        color: COLORS.wall_seg, roughness: 0.85,
      })
    );
    wsMesh.position.set(wsx, wsy, wsz);
    wsMesh.castShadow = true;
    wsMesh.receiveShadow = true;
    wsMesh.userData.label = `wall side=${ws.side}`;
    wsMesh.userData.baseOpacity = 0.92;
    wsMesh.userData.role = 'cargo';
    g.add(wsMesh);

    const wsEdges = new THREE.EdgesGeometry(wsMesh.geometry);
    const wsLine = new THREE.LineSegments(
      wsEdges, new THREE.LineBasicMaterial({ color: 0x3a2818 })
    );
    wsLine.position.copy(wsMesh.position);
    g.add(wsLine);
  }
}

// ─────────────────────────────────────────────────────────────
// 회차 강조 — *근본 시각 해결*: 선택 외 회차 *반투명*, 선택은 *불투명* + outline.
// 모든 회차 동시 표시 + 강조 변경만으로 어디가 선택인지 명확. 카메라 안 움직임.
// ─────────────────────────────────────────────────────────────
// 사용자 결정 — 강조 시 나머지 회차가 너무 어두웠음. 0.18→0.55, 0.30→0.65 로 완화.
const DIMMED_OPACITY_CARGO = 0.55;
const DIMMED_OPACITY_TRUCK = 0.65;

function _setGroupDimmed(g, dimmed) {
  // group 내 모든 Mesh + LineSegments 의 opacity 조정 + 화물 라벨 dim.
  // (회차 라벨은 그룹 단위로 별도 관리 — groupLabelDivs)
  g.traverse(obj => {
    if (obj.isMesh && obj.material) {
      const base = obj.userData.baseOpacity;
      if (base === undefined) return;
      obj.material.transparent = true;
      obj.material.opacity = dimmed ? (
        obj.userData.role === 'cargo' ? DIMMED_OPACITY_CARGO : DIMMED_OPACITY_TRUCK
      ) : base;
    } else if (obj.isLineSegments && obj.material) {
      obj.material.transparent = true;
      obj.material.opacity = dimmed ? 0.5 : 1.0;
    }
  });
  // 화물 라벨
  for (const cl of g.userData.cargoLabels || []) {
    if (dimmed) cl.classList.add('dimmed');
    else cl.classList.remove('dimmed');
  }
}

function highlightTrip(tripNo) {
  // 기존 outline 제거
  for (const tno in highlightOutlines) {
    const hl = highlightOutlines[tno];
    if (hl.parent) hl.parent.remove(hl);
  }
  highlightOutlines = {};

  if (tripNo === null || tripNo === undefined) {
    // 강조 해제 — 모든 trips 정상 + 모든 그룹 라벨 디폴트 복원
    for (const tno in truckGroups) _setGroupDimmed(truckGroups[tno], false);
    for (const gidStr in groupLabelDivs) {
      const lbl = groupLabelDivs[gidStr];
      lbl.classList.remove('highlight');
      lbl.classList.remove('dimmed');
      const tripsInGroup = groupTripsCache[gidStr] || [];
      lbl.innerHTML = renderGroupLabelHtml(tripsInGroup, null);
    }
    return;
  }

  // 클릭한 trip
  const clickedTrip = PACK_DATA.trips.find(t => t.trip_no === tripNo);
  if (!clickedTrip) return;
  const clickedGid = clickedTrip.group_id;

  // ① 트럭 강조 — 클릭한 trip 의 트럭 그룹만 정상 opacity, 그 외는 반투명
  for (const tno in truckGroups) {
    const isSelected = (Number(tno) === tripNo);
    _setGroupDimmed(truckGroups[tno], !isSelected);
  }

  // ② 라벨 — *클릭한 trip 의 그룹 라벨만* highlight + 그 회차 정보로 갱신
  //   다른 그룹의 라벨은 dimmed
  for (const gidStr in groupLabelDivs) {
    const gid = Number(gidStr);
    const lbl = groupLabelDivs[gidStr];
    if (gid === clickedGid) {
      lbl.classList.add('highlight');
      lbl.classList.remove('dimmed');
      // 클릭한 회차의 상세 정보로 내용 갱신
      lbl.innerHTML = renderGroupLabelHtml(
        groupTripsCache[gidStr] || [], clickedTrip,
      );
    } else {
      lbl.classList.remove('highlight');
      lbl.classList.add('dimmed');
      // 다른 그룹은 디폴트 (그룹 요약) 로 복원
      const tripsInGroup = groupTripsCache[gidStr] || [];
      lbl.innerHTML = renderGroupLabelHtml(tripsInGroup, null);
    }
  }

  // ③ 선택 trip — 빨간 outline
  const g = truckGroups[tripNo];
  if (!g) return;
  const yo = clickedTrip.y_offset_m || 0;
  const tw = clickedTrip.truck_width_m;
  const tl = clickedTrip.truck_length_m;
  const xo = clickedTrip.x_offset_m;
  const veh_h = clickedTrip.vehicle_height_offset_m;
  const inner_h = clickedTrip.truck_height_m - veh_h;
  const hlGeo = new THREE.BoxGeometry(tl + 0.1, inner_h + 0.1, tw + 0.1);
  const hlEdges = new THREE.EdgesGeometry(hlGeo);
  const hlLine = new THREE.LineSegments(
    hlEdges,
    new THREE.LineBasicMaterial({ color: 0xf85149, transparent: false })
  );
  // truckGroup 의 position.z (y_offset_m) 가 자식에 적용되므로 outline 도 그룹 안에 넣어야
  hlLine.position.set(xo + tl / 2, veh_h + inner_h / 2, tw / 2);
  g.add(hlLine);
  highlightOutlines[tripNo] = hlLine;
}

window.highlightTrip = highlightTrip;  // Qt runJavaScript 에서 호출

// ─────────────────────────────────────────────────────────────
// 회차 포커스 — *화면 밖일 때만* 카메라 평행이동.
// Frustum 검사로 trip 트럭의 bounding box 가 카메라 시야에 들어가면 안 움직임.
// 시야 밖이면 X·Z 만 평행이동 (거리·각도·Y 보존). 350 ms easeOutCubic.
// ─────────────────────────────────────────────────────────────
let _focusAnimId = null;
const _frustum = new THREE.Frustum();
const _projMat = new THREE.Matrix4();
const _tripBox = new THREE.Box3();

function _isTripInView(t) {
  const yo = t.y_offset_m || 0;
  // *살짝 여유* — bounding 의 70% 만 보여도 "안에 있다" 로 판정 (가장자리 살짝 잘려도 OK)
  const margin = 0.7;
  const halfL = t.truck_length_m * margin / 2;
  const halfW = t.truck_width_m * margin / 2;
  const cx = t.x_offset_m + t.truck_length_m / 2;
  const cz = yo + t.truck_width_m / 2;
  _tripBox.min.set(cx - halfL, 0, cz - halfW);
  _tripBox.max.set(cx + halfL, t.truck_height_m, cz + halfW);
  camera.updateMatrixWorld();
  _projMat.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  _frustum.setFromProjectionMatrix(_projMat);
  return _frustum.intersectsBox(_tripBox);
}

function focusTrip(tripNo) {
  const t = PACK_DATA.trips.find(tr => tr.trip_no === tripNo);
  if (!t) return;
  // 사용자 결정: *화면 안* 이면 카메라 안 움직임. 강조만.
  if (_isTripInView(t)) return;

  const yOff = t.y_offset_m || 0;
  const targetX = t.x_offset_m + t.truck_length_m / 2;
  const targetZ = yOff + t.truck_width_m / 2;
  const dx = targetX - controls.target.x;
  const dz = targetZ - controls.target.z;
  if (Math.abs(dx) < 1e-3 && Math.abs(dz) < 1e-3) return;

  if (_focusAnimId !== null) cancelAnimationFrame(_focusAnimId);

  const startCamX = camera.position.x;
  const startCamZ = camera.position.z;
  const startTargetX = controls.target.x;
  const startTargetZ = controls.target.z;
  const startTime = performance.now();
  const duration = 350;

  function step(now) {
    const t01 = Math.min(1, (now - startTime) / duration);
    const e = 1 - Math.pow(1 - t01, 3);
    camera.position.x = startCamX + dx * e;
    camera.position.z = startCamZ + dz * e;
    controls.target.x = startTargetX + dx * e;
    controls.target.z = startTargetZ + dz * e;
    controls.update();
    if (t01 < 1) _focusAnimId = requestAnimationFrame(step);
    else _focusAnimId = null;
  }
  _focusAnimId = requestAnimationFrame(step);
}

window.focusTrip = focusTrip;

// ─────────────────────────────────────────────────────────────
// 카메라 프리셋 + 자동 fit
// ─────────────────────────────────────────────────────────────
let _sceneBox = null;
function fitAllTrucks() {
  // 모든 truckGroups 의 bounding box 합산
  const box = new THREE.Box3();
  for (const tno in truckGroups) {
    box.expandByObject(truckGroups[tno]);
  }
  if (box.isEmpty()) return;
  _sceneBox = box;
  setCameraPreset('iso');
}

function setCameraPreset(name) {
  // 모든 버튼의 active 상태 갱신
  for (const id of ['btn-iso', 'btn-top', 'btn-side', 'btn-front']) {
    const b = document.getElementById(id);
    if (b) b.classList.remove('active');
  }
  const btn = document.getElementById('btn-' + name);
  if (btn) btn.classList.add('active');

  const preset = PRESETS[name];
  if (!preset || !_sceneBox) return;

  const center = new THREE.Vector3();
  _sceneBox.getCenter(center);
  const size = new THREE.Vector3();
  _sceneBox.getSize(size);
  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = camera.fov * Math.PI / 180;
  const dist = maxDim / (2 * Math.tan(fov / 2)) * 1.4;

  const eye = new THREE.Vector3(
    center.x + preset.pos[0] * dist,
    center.y + preset.pos[1] * dist,
    center.z + preset.pos[2] * dist,
  );
  camera.position.copy(eye);
  camera.up.set(preset.up[0], preset.up[1], preset.up[2]);
  controls.target.copy(center);
  camera.lookAt(center);
  controls.update();
}

window.fitAllTrucks = fitAllTrucks;
window.setCameraPreset = setCameraPreset;

// 시작
init();
</script>
</body>
</html>
"""


def build_loaded_3d_three_html(
    trips: List[Trip],
    sp: Optional[SpacingParams] = None,
    highlight_trip_no: Optional[int] = None,
    trip_costs: Optional[Dict[int, float]] = None,
) -> str:
    """Three.js HTML 문서 생성 — PackResult.trips 데이터를 인라인 임베드."""
    data = serialize_pack_for_three(trips, sp, highlight_trip_no, trip_costs)
    pack_json = json.dumps(data, ensure_ascii=False)
    return _HTML_TEMPLATE.replace("__PACK_DATA__", pack_json)


__all__ = [
    "INTER_TRUCK_GAP_M",
    "serialize_pack_for_three",
    "build_loaded_3d_three_html",
]
