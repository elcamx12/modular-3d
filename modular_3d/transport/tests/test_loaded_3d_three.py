"""Three.js 운송 3D 도식 단위 테스트.

[검증 범위]
- 직렬화: trips → JSON (m 단위 변환, side 매핑, placements 구조)
- HTML 생성: 템플릿 내 인라인 JSON 임베드, Three.js CDN 스크립트 태그
- 빈 trips / 강조 trip_no / 비용 정보 전달
"""
from __future__ import annotations

import json

import pytest

from modular_3d.transport.loaded_3d_three import (
    INTER_TRUCK_GAP_M,
    build_loaded_3d_three_html,
    serialize_pack_for_three,
)
from modular_3d.transport.models import (
    Module, Panel, Section, SpacingParams, Truck, WallSegment,
)
from modular_3d.transport.packer import Trip
from modular_3d.transport.packer_core import (
    GREEDY_STRATEGIES, WEIGHT_SETS, default_eco_options, pack_one_seed,
)
from modular_3d.transport.packer_types import (
    Placement, PlacementSlot, Posture,
)
from modular_3d.transport.tests.transport_v2_fixtures import (
    default_site, default_trucks, generate_fixture,
)


_SHS = Section(name="SHS200x8", section_type="SHS",
               width=200, height=200, thickness=8, weight_per_m=47.9)


def _lowbed() -> Truck:
    return Truck(name="저상25t", truck_type="lowbed",
                 max_length=12000, max_width=3000, max_height=4500,
                 max_weight=25000, vehicle_height_offset=700,
                 curb_weight_kg=14000, active=True)


def _module() -> Module:
    return Module(name="M1", length=6000, width=3000, height=2700,
                  column_section=_SHS, beam_section=_SHS,
                  extra_weight_kg=5000)


# ════════════════════════════════════════════════════════════════════
# §1 직렬화 — 단위 변환 (mm → m)
# ════════════════════════════════════════════════════════════════════
def test_serialize_empty_trips():
    data = serialize_pack_for_three([])
    assert data["trips"] == []
    assert data["inter_truck_gap_m"] == 4.0
    assert data["intra_group_gap_m"] == 1.5
    assert data["n_groups"] == 0


def test_serialize_module_trip_unit_conversion():
    """mm → m 단위 환산 확인."""
    m = _module()
    p = Placement(item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(0.0, 0.0, 0.0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[p])
    data = serialize_pack_for_three([trip])
    assert len(data["trips"]) == 1
    td = data["trips"][0]
    assert td["trip_no"] == 1
    assert td["truck_length_m"] == 12.0
    assert td["truck_width_m"] == 3.0
    assert td["vehicle_height_offset_m"] == 0.7
    # placement
    assert len(td["placements"]) == 1
    pj = td["placements"][0]
    assert pj["kind"] == "module"
    assert pj["length_m"] == 6.0
    assert pj["width_m"] == 3.0
    assert pj["height_m"] == 2.7


def test_serialize_same_group_trips_stack_in_y_direction():
    """*같은 트럭 + 같은 화물 (이름 무관)* 회차 2 개 → 같은 그룹 →
    *Y 방향(폭)* 으로 누적 (사이드뷰 화면 깊이 = 트럭의 오른쪽).

    같은 시그니처라 두 번째 회차는 X 위치 동일 + Y 오프셋만 음수 방향으로.
    """
    m1 = _module()
    m2 = Module(name="M_other", length=6000, width=3000, height=2700,
                column_section=_SHS, beam_section=_SHS, extra_weight_kg=5000)
    truck = _lowbed()
    p1 = Placement(item=m1, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                   truck_xyz=(0, 0, 0), parent_idx=None)
    p2 = Placement(item=m2, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                   truck_xyz=(0, 0, 0), parent_idx=None)
    t1 = Trip(trip_no=1, truck=truck, items=[m1], placements=[p1])
    t2 = Trip(trip_no=2, truck=truck, items=[m2], placements=[p2])
    data = serialize_pack_for_three([t1, t2])
    # 같은 그룹
    assert data["n_groups"] == 1
    # 같은 X 오프셋
    assert data["trips"][0]["x_offset_m"] == data["trips"][1]["x_offset_m"]
    # 두 번째는 Y 음수 방향 (-truck_width - intra_gap)
    expected_dy = -(3.0 + 1.5)  # truck width + INTRA_GROUP_GAP_M, 음수
    assert data["trips"][1]["y_offset_m"] == pytest.approx(expected_dy)


def test_serialize_different_groups_separate_in_x_direction():
    """다른 트럭 종류 → 다른 그룹 → *X 방향(진행 방향)* 으로 분리."""
    m1 = _module()
    m2 = _module()
    lowbed = _lowbed()
    extendable = Truck(name="광폭28t", truck_type="extendable",
                       max_length=18000, max_width=3400, max_height=4500,
                       max_weight=28000, vehicle_height_offset=700,
                       curb_weight_kg=16000, active=True)
    p1 = Placement(item=m1, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                   truck_xyz=(0, 0, 0), parent_idx=None)
    p2 = Placement(item=m2, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                   truck_xyz=(0, 0, 0), parent_idx=None)
    t1 = Trip(trip_no=1, truck=lowbed, items=[m1], placements=[p1])
    t2 = Trip(trip_no=2, truck=extendable, items=[m2], placements=[p2])
    data = serialize_pack_for_three([t1, t2])
    # 다른 그룹
    assert data["n_groups"] == 2
    # X 오프셋 다름
    assert data["trips"][0]["x_offset_m"] != data["trips"][1]["x_offset_m"]
    # 둘 다 Y=0 (각자 그룹의 첫 회차)
    assert data["trips"][0]["y_offset_m"] == 0.0
    assert data["trips"][1]["y_offset_m"] == 0.0


# ════════════════════════════════════════════════════════════════════
# §2 종속 floor 의 wall_segments
# ════════════════════════════════════════════════════════════════════
def test_serialize_dependent_floor_wall_segments():
    """L 자 → wall_segments 가 직렬화 결과에 포함, 위치 정확."""
    seg = WallSegment(
        side=0, start_offset_mm=0.0, length_mm=8000,
        height_mm=3000.0, thickness_mm=200.0,
        column_section=_SHS, beam_section=_SHS,
    )
    L = Panel(name="L1", kind="floor", width=3000, length=8000,
              thickness=150, beam_section=_SHS,
              wall_segments=(seg,), extra_weight_kg=2500)
    p = Placement(item=L, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[L], placements=[p])
    data = serialize_pack_for_three([trip])
    pj = data["trips"][0]["placements"][0]
    assert pj["kind"] == "dep_floor"
    assert len(pj["wall_segments"]) == 1
    wsj = pj["wall_segments"][0]
    assert wsj["side"] == 0
    assert wsj["dx_m"] == 8.0   # 하변 길이 = panel.length
    assert wsj["dy_m"] == 0.2   # 두께
    assert wsj["dz_m"] == 3.0   # 벽 높이


def test_serialize_highlight_trip_no_and_costs():
    """강조 trip_no + 비용 dict 가 그대로 전달."""
    m = _module()
    p = Placement(item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[p])
    data = serialize_pack_for_three(
        [trip], highlight_trip_no=1, trip_costs={1: 600_000.0}
    )
    assert data["highlight_trip_no"] == 1
    assert data["trips"][0]["cost_krw"] == 600_000.0


def test_serialize_legacy_trip_without_placements():
    """placements 비어있는 Trip 도 items 로 직렬화."""
    m = _module()
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[])
    data = serialize_pack_for_three([trip])
    assert len(data["trips"][0]["placements"]) == 1
    assert data["trips"][0]["placements"][0]["kind"] == "module"


# ════════════════════════════════════════════════════════════════════
# §3 HTML 생성
# ════════════════════════════════════════════════════════════════════
def test_build_html_contains_threejs_cdn():
    """HTML 안에 Three.js CDN + OrbitControls + CSS2DRenderer 스크립트 태그."""
    m = _module()
    p = Placement(item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[p])
    html = build_loaded_3d_three_html([trip])
    assert "three.min.js" in html
    assert "OrbitControls.js" in html
    assert "CSS2DRenderer.js" in html
    # 어두운 배경 + Fog
    assert "0x0d1117" in html
    # 카메라 프리셋 + 강조 + 포커스 JS 함수
    assert "setCameraPreset" in html
    assert "highlightTrip" in html
    assert "focusTrip" in html


def test_build_html_inlines_pack_data_json():
    """HTML 안에 PACK_DATA JSON 이 인라인 임베드."""
    m = _module()
    p = Placement(item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[p])
    html = build_loaded_3d_three_html([trip])
    # trip_no=1 가 JSON 안에
    assert '"trip_no": 1' in html or '"trip_no":1' in html
    # 트럭 이름이 JSON 안에
    assert "저상25t" in html


def test_build_html_empty_trips_still_returns_html():
    """빈 trips 도 정상 HTML 반환 (안내 메시지 표시)."""
    html = build_loaded_3d_three_html([])
    assert "<html>" in html.lower()
    # PACK_DATA 의 trips 가 빈 배열
    assert '"trips":' in html.replace(" ", "")


def test_build_html_with_real_pack_result():
    """실제 픽스처 패킹 결과로 HTML 생성 — 예외 없이."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    pack = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                        "fixed_per_trip", default_eco_options())
    html = build_loaded_3d_three_html(pack.trips, fx.spacing,
                                       trip_costs={1: 600_000})
    assert len(html) > 1000
    assert "PACK_DATA" in html


# ════════════════════════════════════════════════════════════════════
# §4 JSON 유효성
# ════════════════════════════════════════════════════════════════════
def test_serialized_data_is_valid_json():
    """직렬화 결과가 표준 JSON 으로 dumps 가능."""
    m = _module()
    p = Placement(item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[p])
    data = serialize_pack_for_three([trip])
    s = json.dumps(data, ensure_ascii=False)
    parsed = json.loads(s)
    assert parsed["trips"][0]["trip_no"] == 1
