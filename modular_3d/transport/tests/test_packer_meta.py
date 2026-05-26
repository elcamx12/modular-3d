"""Phase 5 단위 테스트 — packer_meta (다중 시드 + Lower Bound).

[검증 범위]
- `trip_cost` / `compute_cost` 모드별 비용 계산
- `lower_bound_cost` 모드별 + 모듈 합산(4.5m) 반영
- `pack_all_seeds` 54 시드 모두 평가 + 비용 최저 채택
- 무작위 30 회 고정 시드 재현성
- Lower Bound 도달 시 조기 종료 동작
- 결과 메타 dict 구조 (all_costs / best_label / lower_bound / early_exit)
"""
from __future__ import annotations

import math

import pytest

from modular_3d.transport.models import (
    Module, Panel, Section, SiteLimit, SpacingParams, Truck,
)
from modular_3d.transport.packer import PackResult, Trip
from modular_3d.transport.packer_core import (
    EcoOptions, default_eco_options,
)
from modular_3d.transport.packer_meta import (
    DEFAULT_RANDOM_SEED_BASE,
    N_RANDOM_SEEDS,
    compute_cost,
    lower_bound_cost,
    pack_all_seeds,
    trip_cost,
)
from modular_3d.transport.tests.transport_v2_fixtures import (
    default_site, default_trucks, generate_fixture,
)


# ── 공용 픽스처 헬퍼 ─────────────────────────────────────────────
_SHS = Section(
    name="SHS200x8", section_type="SHS",
    width=200, height=200, thickness=8, weight_per_m=47.9,
)


def _module(name, length=6000.0, extra=5000.0) -> Module:
    return Module(name=name, length=length, width=3000.0, height=2700.0,
                  column_section=_SHS, beam_section=_SHS,
                  extra_weight_kg=extra)


def _floor(name, length=6000.0, width=2800.0, thickness=150.0, extra=2500.0) -> Panel:
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=thickness, beam_section=_SHS,
                 extra_weight_kg=extra)


def _lowbed() -> Truck:
    return Truck(name="저상25t", truck_type="lowbed",
                 max_length=12000, max_width=3000, max_height=4500,
                 max_weight=25000, vehicle_height_offset=700,
                 curb_weight_kg=14000, active=True)


def _aframe() -> Truck:
    return Truck(name="A프레임20t", truck_type="aframe",
                 max_length=12000, max_width=3000, max_height=4500,
                 max_weight=20000, vehicle_height_offset=700,
                 curb_weight_kg=12000, active=True)


def _sp() -> SpacingParams:
    return SpacingParams()


def _site() -> SiteLimit:
    return SiteLimit(max_gvw_kg=None, max_width_mm=3500, max_height_mm=4500)


# ════════════════════════════════════════════════════════════════════
# §1 trip_cost — 모드별
# ════════════════════════════════════════════════════════════════════
def test_trip_cost_fixed_per_trip():
    eco = default_eco_options()
    trip = Trip(trip_no=1, truck=_lowbed(), items=[])
    assert trip_cost(trip, "fixed_per_trip", eco) == eco.fixed_per_trip_rate


def test_trip_cost_freight_table_lowbed():
    eco = default_eco_options()
    trip = Trip(trip_no=1, truck=_lowbed(), items=[])
    assert trip_cost(trip, "freight_table", eco) == eco.lowbed_fixed_krw


def test_trip_cost_freight_table_aframe():
    eco = default_eco_options()
    trip = Trip(trip_no=1, truck=_aframe(), items=[])
    assert trip_cost(trip, "freight_table", eco) == eco.aframe_fixed_krw


def test_trip_cost_per_km():
    eco = default_eco_options()
    trip = Trip(trip_no=1, truck=_lowbed(), items=[])
    expected = eco.lowbed_km_rate * eco.round_trip_km
    assert trip_cost(trip, "per_km", eco) == expected


def test_trip_cost_unknown_mode_raises():
    eco = default_eco_options()
    trip = Trip(trip_no=1, truck=_lowbed(), items=[])
    with pytest.raises(ValueError):
        trip_cost(trip, "nonexistent_mode", eco)


# ════════════════════════════════════════════════════════════════════
# §2 compute_cost — PackResult 합산
# ════════════════════════════════════════════════════════════════════
def test_compute_cost_zero_trips_is_zero():
    eco = default_eco_options()
    res = PackResult(trips=[], blocked=[])
    assert compute_cost(res, "fixed_per_trip", eco) == 0.0


def test_compute_cost_sums_all_trips():
    eco = default_eco_options()
    trips = [
        Trip(trip_no=1, truck=_lowbed(), items=[]),
        Trip(trip_no=2, truck=_aframe(), items=[]),
    ]
    res = PackResult(trips=trips, blocked=[])
    cost_ft = compute_cost(res, "freight_table", eco)
    expected = eco.lowbed_fixed_krw + eco.aframe_fixed_krw
    assert cost_ft == expected


def test_compute_cost_blocked_adds_penalty():
    """blocked 가 있으면 패널티 추가 — 시드 비교 시 정상 해보다 불리해짐."""
    eco = default_eco_options()
    res_clean = PackResult(
        trips=[Trip(trip_no=1, truck=_lowbed(), items=[])], blocked=[],
    )
    res_blocked = PackResult(
        trips=[Trip(trip_no=1, truck=_lowbed(), items=[])],
        blocked=[(_floor("F"), "막힘")],
    )
    c_clean = compute_cost(res_clean, "fixed_per_trip", eco)
    c_blocked = compute_cost(res_blocked, "fixed_per_trip", eco)
    assert c_blocked > c_clean


# ════════════════════════════════════════════════════════════════════
# §3 lower_bound_cost — 모드별
# ════════════════════════════════════════════════════════════════════
def test_lower_bound_empty_returns_zero():
    eco = default_eco_options()
    lb = lower_bound_cost([], [_lowbed()], _site(), _sp(), "fixed_per_trip", eco)
    assert lb == 0.0


def test_lower_bound_one_module_one_trip_minimum():
    """모듈 1 개 → 최소 1 회차 → 최소 1 회 단가."""
    eco = default_eco_options()
    m = _module("M1", length=9000.0)
    lb = lower_bound_cost([m], [_lowbed()], _site(), _sp(),
                          "fixed_per_trip", eco)
    assert lb == eco.fixed_per_trip_rate


def test_lower_bound_two_short_modules_pair_into_one_trip():
    """4.5m 이하 모듈 2 개 → ⌈2/2⌉ = 1 회차 하한."""
    eco = default_eco_options()
    m1 = _module("M_short_1", length=4500.0)
    m2 = _module("M_short_2", length=4500.0)
    lb = lower_bound_cost([m1, m2], [_lowbed()], _site(), _sp(),
                          "fixed_per_trip", eco)
    # 두 짧은 모듈 → 1 회차 하한
    assert lb == eco.fixed_per_trip_rate


def test_lower_bound_three_short_modules_two_trips():
    """4.5m 이하 모듈 3 개 → ⌈3/2⌉ = 2 회차 하한."""
    eco = default_eco_options()
    ms = [_module(f"M_short_{i}", length=4500.0) for i in range(3)]
    lb = lower_bound_cost(ms, [_lowbed()], _site(), _sp(),
                          "fixed_per_trip", eco)
    assert lb == 2 * eco.fixed_per_trip_rate


def test_lower_bound_long_modules_no_merge():
    """긴 모듈 2 개 → 합산 X → 2 회차 하한."""
    eco = default_eco_options()
    ms = [_module(f"M_long_{i}", length=9000.0) for i in range(2)]
    lb = lower_bound_cost(ms, [_lowbed()], _site(), _sp(),
                          "fixed_per_trip", eco)
    assert lb == 2 * eco.fixed_per_trip_rate


def test_lower_bound_freight_table_uses_min_rate():
    """모드 2 — 트럭 종류별 최저 단가 적용."""
    eco = default_eco_options()
    m = _module("M1", length=9000.0)
    trucks = default_trucks()  # lowbed 60만 / extendable 70만 / aframe 80만
    lb = lower_bound_cost([m], trucks, _site(), _sp(), "freight_table", eco)
    assert lb == min(eco.lowbed_fixed_krw, eco.extendable_fixed_krw,
                     eco.aframe_fixed_krw)


def test_lower_bound_per_km_uses_min_rate():
    """모드 3 — 트럭 종류별 최저 km단가 × 왕복거리."""
    eco = default_eco_options()
    m = _module("M1", length=9000.0)
    trucks = default_trucks()
    lb = lower_bound_cost([m], trucks, _site(), _sp(), "per_km", eco)
    expected = (min(eco.lowbed_km_rate, eco.extendable_km_rate,
                    eco.aframe_km_rate) * eco.round_trip_km)
    assert lb == expected


def test_lower_bound_unknown_mode_raises():
    eco = default_eco_options()
    with pytest.raises(ValueError):
        lower_bound_cost([_module("M")], [_lowbed()], _site(), _sp(),
                          "nonexistent_mode", eco)


def test_lower_bound_inactive_trucks_excluded():
    """active=False 트럭은 하한 계산에서 제외 — 모두 inactive → 0."""
    eco = default_eco_options()
    inactive = Truck(name="X", truck_type="lowbed",
                     max_length=12000, max_width=3000, max_height=4500,
                     max_weight=25000, active=False)
    lb = lower_bound_cost([_module("M")], [inactive], _site(), _sp(),
                          "fixed_per_trip", eco)
    assert lb == 0.0


# ════════════════════════════════════════════════════════════════════
# §4 pack_all_seeds — 54 시드 + 비용 최저 채택
# ════════════════════════════════════════════════════════════════════
def test_pack_all_seeds_returns_best_and_meta():
    """min 픽스처 — 최저 비용 해 채택 + 메타 dict 구조."""
    fx = generate_fixture("min")
    items = list(fx.modules) + list(fx.panels)
    best, meta = pack_all_seeds(
        items, fx.trucks, fx.site, fx.spacing, "fixed_per_trip",
    )
    assert isinstance(best, PackResult)
    assert len(best.blocked) == 0
    assert best.total_trips >= 1
    # 메타 필수 키
    assert "all_costs" in meta
    assert "best_label" in meta
    assert "lower_bound" in meta
    assert "early_exit" in meta
    assert "candidates_tried" in meta
    # 후보 1 개 이상
    assert meta["candidates_tried"] >= 1


def test_pack_all_seeds_small_no_blocked():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    best, meta = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip")
    assert len(best.blocked) == 0


def test_pack_all_seeds_runs_24_deterministic_when_no_early_exit():
    """early_exit 안 되면 24 결정론 + 30 무작위 = 54 후보."""
    # Lower Bound 보다 비싼 케이스를 만들어 early_exit 회피
    # — 패널 무게가 LB 추정보다 비효율적이라 비용이 LB 위에 있게.
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    best, meta = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip", random_count=30)
    if not meta["early_exit"]:
        assert meta["candidates_tried"] == 24 + 30
    else:
        # early_exit 발동 — 후보 수는 그보다 적음
        assert meta["candidates_tried"] >= 1


def test_pack_all_seeds_best_label_in_all_costs():
    """채택된 best_label 이 all_costs 안에 존재."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    best, meta = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip")
    labels = {lbl for lbl, _ in meta["all_costs"]}
    assert meta["best_label"] in labels


def test_pack_all_seeds_best_cost_is_minimum():
    """채택된 best_cost 는 all_costs 의 최저값."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    best, meta = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip")
    min_cost = min(c for _, c in meta["all_costs"])
    assert meta["best_cost"] == pytest.approx(min_cost)


# ════════════════════════════════════════════════════════════════════
# §5 무작위 30 회 — 고정 시드 재현성
# ════════════════════════════════════════════════════════════════════
def test_pack_all_seeds_fixed_seed_reproducible():
    """같은 seed_base 두 번 → 같은 결과."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    best_a, meta_a = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                    "fixed_per_trip", seed_base=42)
    best_b, meta_b = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                    "fixed_per_trip", seed_base=42)
    assert meta_a["best_label"] == meta_b["best_label"]
    assert meta_a["best_cost"] == pytest.approx(meta_b["best_cost"])
    assert best_a.total_trips == best_b.total_trips


def test_pack_all_seeds_different_seed_can_differ():
    """다른 seed_base — 무작위 시드 결과가 *다를 수 있어야* (셔플 효과 확인).

    같을 수도 있음 (24 결정론 시드 중 하나가 최저면 무작위 영향 X).
    그래서 *all_costs 안의 무작위 후보 비용 시퀀스가 달라야* 한다고 검증.
    """
    fx = generate_fixture("medium")
    items = list(fx.modules) + list(fx.panels)
    _, meta_a = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip", seed_base=42)
    _, meta_b = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip", seed_base=999)
    rand_costs_a = [c for lbl, c in meta_a["all_costs"] if lbl.startswith("random_")]
    rand_costs_b = [c for lbl, c in meta_b["all_costs"] if lbl.startswith("random_")]
    # 적어도 하나는 달라야 (early_exit 안 됐다면)
    if not meta_a["early_exit"] and not meta_b["early_exit"]:
        assert rand_costs_a != rand_costs_b


# ════════════════════════════════════════════════════════════════════
# §6 Lower Bound 조기 종료
# ════════════════════════════════════════════════════════════════════
def test_pack_all_seeds_early_exit_triggers_when_lb_reached():
    """모듈 1 개 만 — 1 회차 = LB. 첫 시드부터 LB 도달 → 조기 종료."""
    m = _module("M_alone", length=9000.0)
    eco = default_eco_options()
    best, meta = pack_all_seeds(
        [m], [_lowbed()], _site(), _sp(),
        "fixed_per_trip", eco_options=eco,
    )
    assert meta["early_exit"] is True
    # 1 회차 = LB 와 같음
    assert meta["best_cost"] == pytest.approx(meta["lower_bound"])


def test_pack_all_seeds_no_early_exit_returns_full_candidates():
    """비효율 케이스 — early_exit 미발동 시 24+30=54 후보 모두 평가."""
    fx = generate_fixture("medium")
    items = list(fx.modules) + list(fx.panels)
    best, meta = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip", random_count=30)
    if not meta["early_exit"]:
        # 24 결정론 + 30 무작위 = 54
        assert meta["candidates_tried"] == 54


# ════════════════════════════════════════════════════════════════════
# §7 cost_mode 변경 — 동일 입력 다른 모드는 다른 비용
# ════════════════════════════════════════════════════════════════════
def test_pack_all_seeds_three_modes_all_succeed():
    """3 가지 비용 모드 모두 정상 동작 (blocked=0)."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    for mode in ["fixed_per_trip", "freight_table", "per_km"]:
        best, _meta = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing, mode)
        assert len(best.blocked) == 0, f"{mode} 에서 blocked 발생"


def test_pack_all_seeds_unknown_mode_raises():
    fx = generate_fixture("min")
    items = list(fx.modules) + list(fx.panels)
    with pytest.raises(ValueError):
        pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                       cost_mode="bogus_mode")


# ════════════════════════════════════════════════════════════════════
# §8 random_count 파라미터 — 무작위 시드 개수 변경
# ════════════════════════════════════════════════════════════════════
def test_pack_all_seeds_random_count_zero_skips_random():
    """random_count=0 → 24 결정론 시드만."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    best, meta = pack_all_seeds(items, fx.trucks, fx.site, fx.spacing,
                                "fixed_per_trip", random_count=0)
    rand_labels = [lbl for lbl, _ in meta["all_costs"] if lbl.startswith("random_")]
    assert rand_labels == []


def test_default_random_seed_base_is_42():
    """기본 seed_base = 42 (사용자 결정 — 고정 시드)."""
    assert DEFAULT_RANDOM_SEED_BASE == 42


def test_default_random_seed_count_is_30():
    assert N_RANDOM_SEEDS == 30
