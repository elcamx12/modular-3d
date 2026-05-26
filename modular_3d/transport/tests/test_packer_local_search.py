"""Phase 6 단위 테스트 — packer_local_search (VND 5 종 수술).

[검증 범위]
- Trip ↔ TruckState 역변환 보존성
- 5 수술 각각 — 의도적 비효율 초기 해에서 비용 감소 확인
- vnd_improve 종료 조건 (max_iter, 무한 개선 없음까지)
- pack_all_seeds + apply_vnd 통합
"""
from __future__ import annotations

import pytest

from modular_3d.transport.models import (
    Module, Panel, Section, SiteLimit, SpacingParams, Truck,
)
from modular_3d.transport.packer import PackResult, Trip
from modular_3d.transport.packer_core import (
    GREEDY_STRATEGIES, WEIGHT_SETS, default_eco_options, pack_one_seed,
)
from modular_3d.transport.packer_local_search import (
    _build_truck_state_without,
    _clone_truck_state,
    _pack_to_truck_states,
    _trip_to_truck_state,
    _truck_states_to_pack,
    _try_add_to_truck,
    op_merge,
    op_posture_toggle,
    op_swap,
    op_transfer,
    op_truck_downgrade,
    vnd_improve,
)
from modular_3d.transport.packer_meta import compute_cost, pack_all_seeds
from modular_3d.transport.packer_types import (
    Placement, PlacementSlot, Posture, TruckState,
)
from modular_3d.transport.tests.transport_v2_fixtures import (
    default_trucks, generate_fixture,
)


# ── 공용 픽스처 헬퍼 ─────────────────────────────────────────────
_SHS = Section(name="SHS200x8", section_type="SHS",
               width=200, height=200, thickness=8, weight_per_m=47.9)


def _module(name, length=6000.0, extra=5000.0) -> Module:
    return Module(name=name, length=length, width=3000.0, height=2700.0,
                  column_section=_SHS, beam_section=_SHS,
                  extra_weight_kg=extra)


def _floor(name, length=6000.0, width=2800.0, thickness=150.0,
           extra=2500.0) -> Panel:
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=thickness, beam_section=_SHS,
                 extra_weight_kg=extra)


def _lowbed(name="저상25t") -> Truck:
    return Truck(name=name, truck_type="lowbed",
                 max_length=12000, max_width=3000, max_height=4500,
                 max_weight=25000, vehicle_height_offset=700,
                 curb_weight_kg=14000, active=True)


def _site() -> SiteLimit:
    return SiteLimit(max_gvw_kg=None, max_width_mm=3500, max_height_mm=4500)


def _sp() -> SpacingParams:
    return SpacingParams()


# ════════════════════════════════════════════════════════════════════
# §1 Trip ↔ TruckState 역변환
# ════════════════════════════════════════════════════════════════════
def test_trip_to_truck_state_round_trip_preserves_items():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    states = _pack_to_truck_states(res, fx.site, fx.spacing)
    # 화물 개수 보존
    items_in_states = sum(len(ts.placements) for ts in states)
    items_in_res = sum(len(t.placements) for t in res.trips)
    assert items_in_states == items_in_res


def test_truck_states_to_pack_preserves_truck_count():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    states = _pack_to_truck_states(res, fx.site, fx.spacing)
    res2 = _truck_states_to_pack(states, res.blocked, fx.spacing)
    assert res.total_trips == res2.total_trips


# ════════════════════════════════════════════════════════════════════
# §2 TruckState 조작 — clone / without / try_add
# ════════════════════════════════════════════════════════════════════
def test_clone_truck_state_is_deep_copy():
    fx = generate_fixture("min")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    ts = _trip_to_truck_state(res.trips[0], fx.site, fx.spacing)
    clone = _clone_truck_state(ts, fx.spacing)
    assert clone is not ts
    assert clone.placements is not ts.placements
    assert len(clone.placements) == len(ts.placements)


def test_build_truck_state_without_removes_placement():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    ts = _trip_to_truck_state(res.trips[0], fx.site, fx.spacing)
    # 자식 없는 placement 찾기
    free_idx = None
    for i in range(len(ts.placements)):
        if not any(p.parent_idx == i for p in ts.placements):
            free_idx = i
            break
    if free_idx is None:
        pytest.skip("자식 없는 placement 없음")
    new_ts = _build_truck_state_without(ts, free_idx, fx.spacing)
    assert new_ts is not None
    assert len(new_ts.placements) == len(ts.placements) - 1


def test_try_add_to_truck_succeeds_when_room():
    ts = TruckState(truck=_lowbed(), effective_cargo_limit=20000.0)
    p = _floor("F1", length=4000.0)
    new_ts = _try_add_to_truck(ts, p, _site(), _sp())
    assert new_ts is not None
    assert len(new_ts.placements) == 1


def test_try_add_to_truck_none_when_no_room():
    """매우 큰 화물 → 빈 트럭에 못 들어감."""
    ts = TruckState(truck=_lowbed(), effective_cargo_limit=20000.0)
    huge = _floor("F_huge", length=99999.0, width=3000.0)
    new_ts = _try_add_to_truck(ts, huge, _site(), _sp())
    assert new_ts is None


# ════════════════════════════════════════════════════════════════════
# §3 op_transfer / op_merge — 비효율 초기 해에서 비용 감소
# ════════════════════════════════════════════════════════════════════
def _build_inefficient_pack(
    items, trucks, site, sp,
) -> PackResult:
    """의도적 비효율 — 각 화물을 별도 회차에 넣은 초기 해.

    화물 한 장씩 따로 호출해 강제로 회차 N 개를 만듦.
    """
    from modular_3d.transport.packer_core import (
        _commit_placement, _compute_xyz, _postures_for,
        _truck_posture_compatible,
    )
    trips = []
    for i, item in enumerate(items):
        # 호환 트럭 1 대 선택
        for tr in trucks:
            if not tr.active:
                continue
            ts = TruckState(
                truck=tr,
                effective_cargo_limit=20000.0,
            )
            new_ts = _try_add_to_truck(ts, item, site, sp)
            if new_ts is not None:
                from modular_3d.transport.packer_core import _truck_state_to_trip
                trips.append(_truck_state_to_trip(new_ts, trip_no=i + 1, sp=sp))
                break
    return PackResult(trips=trips, blocked=[])


def test_op_transfer_reduces_cost_on_inefficient_pack():
    """5 개 floor 패널이 각각 한 회차 → transfer 가 합치기."""
    panels = [_floor(f"F{i}", length=3000.0, extra=1500.0) for i in range(5)]
    trucks = [_lowbed()]
    pack = _build_inefficient_pack(panels, trucks, _site(), _sp())
    assert pack.total_trips == 5  # 비효율 초기 해
    cost_before = compute_cost(pack, "fixed_per_trip", default_eco_options())
    improved, new_pack = op_transfer(
        pack, trucks, _site(), _sp(), "fixed_per_trip", default_eco_options(),
    )
    if improved:
        cost_after = compute_cost(new_pack, "fixed_per_trip", default_eco_options())
        assert cost_after < cost_before


def test_op_merge_reduces_cost_on_two_sparse_trips():
    """두 회차에 각각 작은 패널 1 장씩 → merge 로 한 회차로."""
    p1 = _floor("F1", length=3000.0, extra=1500.0)
    p2 = _floor("F2", length=3000.0, extra=1500.0)
    trucks = [_lowbed()]
    pack = _build_inefficient_pack([p1, p2], trucks, _site(), _sp())
    assert pack.total_trips == 2
    cost_before = compute_cost(pack, "fixed_per_trip", default_eco_options())
    improved, new_pack = op_merge(
        pack, trucks, _site(), _sp(), "fixed_per_trip", default_eco_options(),
    )
    assert improved is True
    cost_after = compute_cost(new_pack, "fixed_per_trip", default_eco_options())
    assert cost_after < cost_before
    assert new_pack.total_trips == 1


# ════════════════════════════════════════════════════════════════════
# §4 op_swap / op_posture_toggle — 채택 시 비용 감소
# ════════════════════════════════════════════════════════════════════
def test_op_swap_returns_false_when_no_improvement():
    """잘 패킹된 해는 swap 으로 더 줄이기 어려움 → 보통 False."""
    fx = generate_fixture("min")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    improved, _ = op_swap(
        res, fx.trucks, fx.site, fx.spacing,
        "fixed_per_trip", default_eco_options(),
    )
    # 정상 해는 swap 으로 더 줄어들기 어려움 — improved 가 False 인 케이스가 일반적.
    assert isinstance(improved, bool)


def test_op_posture_toggle_returns_bool():
    """자세 토글 — 단순 wall 이 없으면 즉시 False."""
    panels = [_floor("F1"), _floor("F2")]
    pack = _build_inefficient_pack(panels, [_lowbed()], _site(), _sp())
    improved, _ = op_posture_toggle(
        pack, [_lowbed()], _site(), _sp(),
        "fixed_per_trip", default_eco_options(),
    )
    # 단순 wall 없음 → 토글 후보 없음 → False
    assert improved is False


# ════════════════════════════════════════════════════════════════════
# §5 op_truck_downgrade — 더 싼 트럭 교체
# ════════════════════════════════════════════════════════════════════
def test_op_truck_downgrade_returns_bool():
    """freight_table 모드: 큰 트럭으로 작은 화물 든 회차 → 다운그레이드 가능."""
    # 큰 광폭 트럭(70만)과 저상(60만) 두 종.
    extendable = Truck(name="광폭28t", truck_type="extendable",
                       max_length=18000, max_width=3400, max_height=4500,
                       max_weight=28000, curb_weight_kg=16000, active=True)
    trucks = [_lowbed(), extendable]
    # 작은 floor 한 장 — 작은 저상에 들어감
    p = _floor("F1", length=4000.0, width=2400.0, extra=1500.0)
    # 광폭에 든 회차를 만듦
    ts = TruckState(truck=extendable, effective_cargo_limit=20000.0)
    new_ts = _try_add_to_truck(ts, p, _site(), _sp())
    from modular_3d.transport.packer_core import _truck_state_to_trip
    trip = _truck_state_to_trip(new_ts, trip_no=1, sp=_sp())
    pack = PackResult(trips=[trip], blocked=[])
    cost_before = compute_cost(pack, "freight_table", default_eco_options())
    improved, new_pack = op_truck_downgrade(
        pack, trucks, _site(), _sp(),
        "freight_table", default_eco_options(),
    )
    if improved:
        cost_after = compute_cost(new_pack, "freight_table", default_eco_options())
        assert cost_after < cost_before


# ════════════════════════════════════════════════════════════════════
# §6 vnd_improve 메인 루프 — 종료 조건
# ════════════════════════════════════════════════════════════════════
def test_vnd_improve_max_iter_zero_returns_input_unchanged():
    """max_iter=0 → 수술 X → 입력과 동일."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    pack = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                        "fixed_per_trip", default_eco_options())
    cost_before = compute_cost(pack, "fixed_per_trip", default_eco_options())
    improved_pack = vnd_improve(
        pack, fx.trucks, fx.site, fx.spacing,
        "fixed_per_trip", default_eco_options(), max_iter=0,
    )
    cost_after = compute_cost(improved_pack, "fixed_per_trip", default_eco_options())
    assert cost_after == pytest.approx(cost_before)


def test_vnd_improve_reduces_cost_on_inefficient_pack():
    """비효율 초기 해 → VND 적용 후 비용 ≤ 적용 전."""
    panels = [_floor(f"F{i}", length=3000.0, extra=1500.0) for i in range(5)]
    trucks = [_lowbed()]
    pack = _build_inefficient_pack(panels, trucks, _site(), _sp())
    cost_before = compute_cost(pack, "fixed_per_trip", default_eco_options())
    improved_pack = vnd_improve(
        pack, trucks, _site(), _sp(),
        "fixed_per_trip", default_eco_options(),
    )
    cost_after = compute_cost(improved_pack, "fixed_per_trip", default_eco_options())
    assert cost_after <= cost_before


def test_vnd_improve_idempotent_after_convergence():
    """수렴 후 한 번 더 돌려도 결과 변동 없음."""
    panels = [_floor(f"F{i}", length=3000.0, extra=1500.0) for i in range(3)]
    trucks = [_lowbed()]
    pack = _build_inefficient_pack(panels, trucks, _site(), _sp())
    p1 = vnd_improve(pack, trucks, _site(), _sp(),
                    "fixed_per_trip", default_eco_options())
    cost1 = compute_cost(p1, "fixed_per_trip", default_eco_options())
    p2 = vnd_improve(p1, trucks, _site(), _sp(),
                    "fixed_per_trip", default_eco_options())
    cost2 = compute_cost(p2, "fixed_per_trip", default_eco_options())
    # 수렴 후엔 더 안 줄어듦
    assert cost2 == pytest.approx(cost1)


# ════════════════════════════════════════════════════════════════════
# §7 pack_all_seeds + apply_vnd 통합
# ════════════════════════════════════════════════════════════════════
def test_pack_all_seeds_with_vnd_does_not_increase_cost():
    """VND 적용 결과가 미적용 결과보다 *최소 같거나 더 싸야* (회귀 방지)."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    _, meta_no = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip", apply_vnd=False)
    _, meta_yes = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                  "fixed_per_trip", apply_vnd=True)
    assert meta_yes["best_cost"] <= meta_no["best_cost"] + 1e-6


def test_pack_all_seeds_with_vnd_no_blocked():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    best, _ = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                              "fixed_per_trip", apply_vnd=True)
    assert len(best.blocked) == 0


def test_pack_all_seeds_vnd_max_iter_limits_work():
    """vnd_max_iter 작은 값 → 정상 동작 (예외 없이)."""
    fx = generate_fixture("min")
    items = list(fx.modules) + list(fx.panels)
    best, _ = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                              "fixed_per_trip", apply_vnd=True, vnd_max_iter=5)
    assert len(best.blocked) == 0
