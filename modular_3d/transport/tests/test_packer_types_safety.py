"""Phase 2 단위 테스트 — packer_types + packer_safety.

[검증 범위]
- 자세·자리 enum 정의
- Placement / TruckState / CheckResult 데이터클래스
- TruckState 의 파생 속성 (remaining_*, used_inner_height)
- _item_dims_for_posture 의 자세별 차원 변환
- can_place 의 4 중 검사 (길이·폭·높이·무게 각 위반 케이스)
- Trip.placements 필드 역호환 (기존 188 테스트가 영향 받지 않음)
"""
from __future__ import annotations

import pytest

from modular_3d.transport.models import (
    Module, Panel, Section, SiteLimit, SpacingParams, Truck, WallSegment,
)
from modular_3d.transport.packer_types import (
    CheckResult, Placement, PlacementSlot, Posture, TruckState,
)
from modular_3d.transport.packer_safety import _item_dims_for_posture, can_place
from modular_3d.transport.packer import Trip


SHS = Section(name="SHS200x8", section_type="SHS",
              width=200, height=200, thickness=8, weight_per_m=47.9)

TR_LOWBED = Truck(
    name="저상25t", truck_type="lowbed",
    max_length=12000, max_width=3000, max_height=4500, max_weight=25000,
    vehicle_height_offset=700, curb_weight_kg=14000, active=True,
)
TR_AFRAME = Truck(
    name="A프레임", truck_type="aframe",
    max_length=12000, max_width=3000, max_height=4500, max_weight=20000,
    vehicle_height_offset=700, curb_weight_kg=12000, active=True,
)
SITE = SiteLimit(max_gvw_kg=None, max_width_mm=3500, max_height_mm=4500)
SP = SpacingParams()


# ── enum 정의 ────────────────────────────────────────────────────
def test_posture_enum_values():
    assert Posture.LYING.value == "lying"
    assert Posture.STANDING.value == "standing"


def test_placement_slot_enum_values():
    assert PlacementSlot.FLOOR.value == "floor"
    assert PlacementSlot.STACK.value == "stack"
    assert PlacementSlot.DEP_INNER.value == "dep_inner"


# ── Placement / CheckResult 기본 동작 ─────────────────────────────
def test_placement_freezes_state():
    m = Module(name="M1", length=10000, width=3000, height=3000,
               column_section=SHS, beam_section=SHS, extra_weight_kg=5000)
    p = Placement(item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(0.0, 0.0, 0.0))
    # frozen 이라 수정 불가
    with pytest.raises(Exception):
        p.slot = PlacementSlot.STACK  # type: ignore


def test_check_result_bool():
    assert CheckResult(ok=True) is not None
    assert bool(CheckResult(ok=True))
    assert not bool(CheckResult(ok=False, reasons=("X",)))


# ── TruckState 파생 속성 ────────────────────────────────────────
def test_truck_state_remaining_cargo_limit():
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=25000,
                    used_cargo_weight=10000)
    assert ts.remaining_cargo_limit == 15000


def test_truck_state_remaining_cargo_limit_negative_clamped():
    """used_cargo_weight 가 effective_cargo_limit 를 넘으면 0 으로 clamp."""
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=10000,
                    used_cargo_weight=15000)
    assert ts.remaining_cargo_limit == 0.0


def test_truck_state_used_inner_height_empty():
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=25000)
    assert ts.used_inner_height(100.0) == 0.0


def test_truck_state_used_inner_height_two_layers():
    """2 단 = 두께 합 + (단 수 − 1) × gap"""
    ts = TruckState(
        truck=TR_LOWBED, effective_cargo_limit=25000,
        layer_max_thickness=[200.0, 150.0],  # 2 단
    )
    # 200 + 150 + 1 × 100 = 450
    assert ts.used_inner_height(100.0) == 450.0


def test_truck_state_n_layers():
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=25000,
                    layer_max_thickness=[200, 150, 100])
    assert ts.n_layers == 3


def test_truck_state_remaining_floor_length():
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=25000,
                    used_floor_length=4000)
    # usable = 11600 (12000 − 2×200)
    assert ts.remaining_floor_length(11600) == 7600


# ── _item_dims_for_posture ──────────────────────────────────────
def test_dims_module_always_lying():
    m = Module(name="M", length=10000, width=3000, height=3000,
               column_section=SHS, beam_section=SHS, extra_weight_kg=5000)
    L, W, H, wt = _item_dims_for_posture(m, Posture.LYING)
    assert L == 10000 and W == 3000 and H == 3000


def test_dims_simple_floor_lying():
    p = Panel(name="F1", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS)
    L, W, H, wt = _item_dims_for_posture(p, Posture.LYING)
    assert L == 6000 and W == 2800 and H == 150


def test_dims_wall_panel_standing():
    """세움 자세: 점유 (length, thickness, width) — width 가 높이방향으로."""
    p = Panel(name="W1", kind="wall", width=3000, length=6000, thickness=200,
              wall_height=3000, beam_section=SHS, column_section=SHS)
    L, W, H, wt = _item_dims_for_posture(p, Posture.STANDING)
    assert L == 6000 and W == 200 and H == 3000


def test_dims_dependent_panel_lshape_lying():
    """종속 패널 (L자) 점유 두께 = thickness + max(seg.height)."""
    seg = WallSegment(side=0, start_offset_mm=0, length_mm=6000,
                      height_mm=3000, thickness_mm=200,
                      column_section=SHS, beam_section=SHS)
    p = Panel(name="L1", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(seg,))
    L, W, H, wt = _item_dims_for_posture(p, Posture.LYING)
    # 150 + 3000 = 3150
    assert L == 6000 and W == 2800 and H == 3150


# ── can_place — 4 검사 위반 케이스 ────────────────────────────────
def _empty_ts(truck=TR_LOWBED, effective=25000):
    return TruckState(truck=truck, effective_cargo_limit=effective)


def test_can_place_ok_simple_floor_lying_on_empty_truck():
    p = Panel(name="F", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS)
    res = can_place(p, Posture.LYING, PlacementSlot.FLOOR, _empty_ts(), SITE, SP)
    assert res.ok, res.reasons


def test_can_place_length_violation():
    """패널 길이가 트럭 유효 길이 초과."""
    p = Panel(name="F", kind="floor", width=2800, length=15000, thickness=150,
              beam_section=SHS)
    res = can_place(p, Posture.LYING, PlacementSlot.FLOOR, _empty_ts(), SITE, SP)
    assert not res.ok
    assert any("길이 초과" in r for r in res.reasons)


def test_can_place_width_violation_truck():
    """패널 폭이 트럭 폭 + 여유 초과 → 트럭 측 위반 메시지."""
    p = Panel(name="F", kind="floor", width=5000, length=6000, thickness=150,
              beam_section=SHS)
    res = can_place(p, Posture.LYING, PlacementSlot.FLOOR, _empty_ts(), SITE, SP)
    assert not res.ok
    assert any("폭 초과 (트럭)" in r for r in res.reasons)


def test_can_place_width_violation_site():
    """현장 폭 제한이 트럭 + 여유보다 더 빡빡한 경우."""
    site_tight = SiteLimit(max_gvw_kg=None, max_width_mm=2900, max_height_mm=None)
    # 트럭 폭 + 양쪽 200 = 3400 통과지만 현장 2900 위반
    p = Panel(name="F", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS)
    res = can_place(p, Posture.LYING, PlacementSlot.FLOOR, _empty_ts(), site_tight, SP)
    assert not res.ok
    assert any("폭 초과 (현장)" in r for r in res.reasons)


def test_can_place_height_violation_truck():
    """벽 점유 높이가 트럭 내공 초과 (LYING)."""
    seg = WallSegment(side=0, start_offset_mm=0, length_mm=6000,
                      height_mm=5000, thickness_mm=200,  # 벽 5m → 너무 높음
                      column_section=SHS, beam_section=SHS)
    p = Panel(name="L", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(seg,))
    res = can_place(p, Posture.LYING, PlacementSlot.FLOOR, _empty_ts(), SITE, SP)
    assert not res.ok
    assert any("내공높이 초과" in r for r in res.reasons)


def test_can_place_weight_violation_truck():
    """누적 화물이 트럭 적재능력 초과."""
    p = Panel(name="F", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, extra_weight_kg=20000)
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=25000,
                    used_cargo_weight=10000)
    res = can_place(p, Posture.LYING, PlacementSlot.FLOOR, ts, SITE, SP)
    assert not res.ok
    assert any("중량 초과" in r or "총중량 초과" in r for r in res.reasons)


def test_can_place_weight_violation_site_gvw():
    """현장 GVW 가 트럭 적재능력보다 빡빡한 경우 — 누적 GVW 메시지."""
    site_gvw = SiteLimit(max_gvw_kg=30000, max_width_mm=None, max_height_mm=None)
    # 차체 14000 + 화물 10000(누적) + 7000(새) = 31000 > 30000
    p = Panel(name="F", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, extra_weight_kg=7000)
    # effective_cargo_limit = min(25000, 30000−14000) = 16000
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=16000,
                    used_cargo_weight=10000)
    res = can_place(p, Posture.LYING, PlacementSlot.FLOOR, ts, site_gvw, SP)
    assert not res.ok
    assert any("총중량 초과" in r or "중량 초과" in r for r in res.reasons)


def test_can_place_stack_uses_layer_height():
    """STACK 자리는 단 위에 쌓아 단별 잔여 높이 검사."""
    p = Panel(name="F", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS)
    # 이미 두께 200 단 3 개 쌓여있음 → 200 + 200 + 200 + 2 × 100(gap) = 800mm
    # 잔여 = 3800 − 800 − 100(새 단 gap) = 2900 → 150 OK
    ts = TruckState(truck=TR_LOWBED, effective_cargo_limit=25000,
                    layer_max_thickness=[200.0, 200.0, 200.0])
    res = can_place(p, Posture.LYING, PlacementSlot.STACK, ts, SITE, SP)
    assert res.ok, res.reasons


def test_can_place_stack_overflow_height():
    """STACK 적층 시 내공 높이 초과."""
    p = Panel(name="L", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS,
              wall_segments=(WallSegment(side=0, start_offset_mm=0, length_mm=6000,
                                          height_mm=4000, thickness_mm=200,
                                          column_section=SHS, beam_section=SHS),))
    # 점유 높이 150+4000=4150 → 어느 자리든 내공 3800 초과
    res = can_place(p, Posture.LYING, PlacementSlot.STACK, _empty_ts(), SITE, SP)
    assert not res.ok
    assert any("내공높이 초과" in r for r in res.reasons)


# ── Trip 역호환 ────────────────────────────────────────────────
def test_trip_placements_default_empty():
    """기존 코드가 placements 안 채워도 동작 — 기본값 빈 리스트."""
    t = Trip(trip_no=1, truck=TR_LOWBED)
    assert t.placements == []
    assert t.has_mixed_posture is False
    assert t.standing_count == 0


def test_trip_with_placements_field():
    """새 패커가 placements 채워도 OK."""
    p = Panel(name="F", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS)
    plc = Placement(item=p, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                    truck_xyz=(0, 0, 0))
    t = Trip(trip_no=1, truck=TR_LOWBED, placements=[plc])
    assert len(t.placements) == 1
    assert t.placements[0].item is p
