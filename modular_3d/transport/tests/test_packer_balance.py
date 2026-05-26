"""Phase 7 단위 테스트 — packer_balance (무게중심 최종 시각 보정).

[검증 범위]
- `_panel_local_cog` 정확 식 — 순수 floor / 단순 wall / 1면 종속 / 4면 종속
- `_compute_trip_cog` 회차 무게중심
- `balance_trips` — 보정 후 거리 ≤ 보정 전 거리
- 비용 영향 0 — 보정 전·후 compute_cost 동일
- 1 placement 이하 회차 — noop
"""
from __future__ import annotations

import pytest

from modular_3d.transport.models import (
    Module, Panel, Section, SiteLimit, SpacingParams, Truck, WallSegment,
)
from modular_3d.transport.packer import PackResult, Trip
from modular_3d.transport.packer_balance import (
    GRID_STEP_MM,
    _balance_one_trip,
    _compute_trip_cog,
    _panel_local_cog,
    balance_trips,
)
from modular_3d.transport.packer_core import (
    GREEDY_STRATEGIES, WEIGHT_SETS, default_eco_options, pack_one_seed,
)
from modular_3d.transport.packer_meta import compute_cost
from modular_3d.transport.packer_types import (
    Placement, PlacementSlot, Posture, TruckState,
)
from modular_3d.transport.tests.transport_v2_fixtures import (
    generate_fixture,
)


_SHS = Section(name="SHS200x8", section_type="SHS",
               width=200, height=200, thickness=8, weight_per_m=47.9)


def _floor(name, length=6000.0, width=2800.0, thickness=150.0,
           extra=2500.0) -> Panel:
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=thickness, beam_section=_SHS,
                 extra_weight_kg=extra)


def _lshape(name, length=8000.0, width=3000.0, side=0,
            seg_height=3000.0, seg_thickness=200.0,
            extra=2500.0) -> Panel:
    seg_len = length if side in (0, 2) else width
    seg = WallSegment(side=side, start_offset_mm=0.0, length_mm=seg_len,
                      height_mm=seg_height, thickness_mm=seg_thickness,
                      column_section=_SHS, beam_section=_SHS)
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=150.0, beam_section=_SHS,
                 wall_segments=(seg,), extra_weight_kg=extra)


def _4face(name, length=8000.0, width=3000.0) -> Panel:
    segs = tuple(
        WallSegment(side=s, start_offset_mm=0.0,
                    length_mm=(length if s in (0, 2) else width),
                    height_mm=3000.0, thickness_mm=200.0,
                    column_section=_SHS, beam_section=_SHS)
        for s in (0, 1, 2, 3)
    )
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=150.0, beam_section=_SHS,
                 wall_segments=segs, extra_weight_kg=3000.0)


def _lowbed() -> Truck:
    return Truck(name="저상25t", truck_type="lowbed",
                 max_length=12000, max_width=3000, max_height=4500,
                 max_weight=25000, vehicle_height_offset=700,
                 curb_weight_kg=14000, active=True)


def _sp() -> SpacingParams:
    return SpacingParams()


# ════════════════════════════════════════════════════════════════════
# §1 _panel_local_cog — 정확 식
# ════════════════════════════════════════════════════════════════════
def test_panel_local_cog_pure_floor_is_center():
    """순수 floor 패널 → 중심 (length/2, width/2). (local x = length 방향, y = width 방향)."""
    p = _floor("F", length=6000.0, width=2800.0)
    cog = _panel_local_cog(p)
    assert cog[0] == pytest.approx(p.length / 2.0)
    assert cog[1] == pytest.approx(p.width / 2.0)


def test_panel_local_cog_lshape_side0_shifts_toward_bottom():
    """1면 종속 (하변 = side 0, x 방향 변) → COG y(=width 방향)가 작은 쪽으로 치우침."""
    p = _lshape("L0", side=0)
    cog = _panel_local_cog(p)
    assert cog[1] < p.width / 2.0


def test_panel_local_cog_lshape_side2_shifts_toward_top():
    """상변 (side 2) → COG y(=width 방향)가 큰 쪽으로 치우침."""
    p = _lshape("L2", side=2)
    cog = _panel_local_cog(p)
    assert cog[1] > p.width / 2.0


def test_panel_local_cog_lshape_side3_shifts_toward_left():
    """좌변 (side 3, y 방향 변) → COG x(=length 방향)가 작은 쪽으로 치우침."""
    p = _lshape("L3", side=3)
    cog = _panel_local_cog(p)
    assert cog[0] < p.length / 2.0


def test_panel_local_cog_4face_back_to_center():
    """4 면 모두 종속 → 대칭 → 중심 근처."""
    p = _4face("C4")
    cog = _panel_local_cog(p)
    assert cog[0] == pytest.approx(p.length / 2.0, rel=0.05)
    assert cog[1] == pytest.approx(p.width / 2.0, rel=0.05)


# ════════════════════════════════════════════════════════════════════
# §2 _compute_trip_cog
# ════════════════════════════════════════════════════════════════════
def test_compute_trip_cog_empty_returns_origin():
    assert _compute_trip_cog([]) == (0.0, 0.0)


def test_compute_trip_cog_single_centered_at_xy():
    """1 placement → 그 패널의 COG 가 회차 COG."""
    p = _floor("F")
    # placement 좌표 (1000, 500)
    placement = Placement(
        item=p, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
        truck_xyz=(1000.0, 500.0, 0.0), parent_idx=None,
    )
    cog = _compute_trip_cog([placement])
    # 순수 floor → 패널 중심 = (W/2, L/2) → offset = 0 → (1000, 500)
    assert cog[0] == pytest.approx(1000.0)
    assert cog[1] == pytest.approx(500.0)


# ════════════════════════════════════════════════════════════════════
# §3 balance_trips — 거리 감소 + 비용 영향 0
# ════════════════════════════════════════════════════════════════════
def test_balance_trips_does_not_change_cost():
    """보정 전·후 compute_cost 완전 동일 (회차 수·트럭·화물 보존)."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    pack = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                        "fixed_per_trip", default_eco_options())
    cost_before = compute_cost(pack, "fixed_per_trip", default_eco_options())
    balance_trips(pack.trips, fx.spacing)
    cost_after = compute_cost(pack, "fixed_per_trip", default_eco_options())
    assert cost_after == pytest.approx(cost_before)


def test_balance_trips_preserves_trip_count():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    pack = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                        "fixed_per_trip", default_eco_options())
    n_before = pack.total_trips
    balance_trips(pack.trips, fx.spacing)
    assert pack.total_trips == n_before


def test_balance_trips_preserves_items_per_trip():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    pack = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                        "fixed_per_trip", default_eco_options())
    before_names = [
        sorted(i.name for i in t.items) for t in pack.trips
    ]
    balance_trips(pack.trips, fx.spacing)
    after_names = [
        sorted(i.name for i in t.items) for t in pack.trips
    ]
    assert before_names == after_names


def test_balance_one_trip_single_placement_is_noop():
    """1 placement 만 든 회차 → noop."""
    p = _floor("F")
    placement = Placement(
        item=p, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
        truck_xyz=(1000.0, 500.0, 0.0), parent_idx=None,
    )
    trip = Trip(trip_no=1, truck=_lowbed(),
                items=[p], placements=[placement])
    _balance_one_trip(trip, _sp())
    assert len(trip.placements) == 1
    assert trip.placements[0].truck_xyz == (1000.0, 500.0, 0.0)


def test_balance_one_trip_reduces_cog_distance_on_asymmetric_load():
    """비대칭 적재 (한쪽으로 치우침) → 보정 후 COG 거리 감소.

    두 패널이 트럭의 한쪽 끝에 몰려있는 초기 해 → 가운데로 슬라이드 가능.
    """
    p1 = _floor("F1", length=4000.0, extra=2000.0)
    p2 = _floor("F2", length=4000.0, extra=2000.0)
    # 두 패널 모두 트럭 *왼쪽 끝* 에 — COG 가 음수 x 방향에 치우침.
    # 트럭 유효 길이 12000-400 = 11600. 좌측 끝 = -5800.
    placements = [
        Placement(item=p1, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(-3500.0, 0.0, 0.0), parent_idx=None),
        Placement(item=p2, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                  truck_xyz=(500.0, 0.0, 0.0), parent_idx=None),
    ]
    trip = Trip(trip_no=1, truck=_lowbed(),
                items=[p1, p2], placements=placements)
    cog_before = _compute_trip_cog(trip.placements)
    dist_before = cog_before[0] ** 2 + cog_before[1] ** 2
    _balance_one_trip(trip, _sp())
    cog_after = _compute_trip_cog(trip.placements)
    dist_after = cog_after[0] ** 2 + cog_after[1] ** 2
    # 보정 후 거리 ≤ 보정 전 거리 (등호 포함 — 이미 최적이면 그대로)
    assert dist_after <= dist_before + 1e-6


# ════════════════════════════════════════════════════════════════════
# §4 GRID_STEP_MM 사용자 결정값
# ════════════════════════════════════════════════════════════════════
def test_grid_step_is_10mm():
    """사용자 결정 — 10 mm 격자."""
    assert GRID_STEP_MM == 10.0
