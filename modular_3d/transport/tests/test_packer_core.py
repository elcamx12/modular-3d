"""Phase 4 단위 테스트 — packer_core (Best-Fit 자유 배치 단일 패킹).

[검증 범위]
- 자세 차원: 단순 wall 의 LYING / STANDING 둘 다 평가
- 적층 기하 제약: 위 단 길이/폭 ≤ 아래 단 (위반 시 -inf)
- 모듈 합산: 두 짧은 모듈(≤4.5m) 페어링 / 긴 모듈은 거부
- 자리 평가식: 길이 분모 = 잔여 적재 공간 (gap 포함 누적)
- 자원 누적: used_floor_length, used_cargo_weight, layer_max_thickness
- 시드 재현성: 같은 입력 두 번 → 같은 결과
- 셔플 입력 안전: random.shuffle 후에도 blocked=0
- mini-시뮬레이션 비파괴성: items 불변
- TruckState → Trip 변환 (역호환 약속)
"""
from __future__ import annotations

import random

import pytest

from modular_3d.transport.models import (
    Module, Panel, Section, SiteLimit, SpacingParams, Truck, WallSegment,
)
from modular_3d.transport.packer_core import (
    EcoOptions,
    GREEDY_STRATEGIES,
    SHUFFLED_STRATEGY,
    WEIGHT_SETS,
    _allowed_slots_for,
    _commit_placement,
    _compute_xyz,
    _dependent_first_sorter,
    _find_dep_inner_parent_idx,
    _find_stack_bottom_idx,
    _is_dependent_floor,
    _is_simple_wall,
    _merge_small_modules,
    _postures_for,
    _select_best_new_truck,
    _simulate_packing_weight,
    _truck_posture_compatible,
    _truck_state_to_trip,
    cost_efficiency_truck_score,
    default_eco_options,
    default_truck_score,
    evaluate_slot,
    pack_one_seed,
)
from modular_3d.transport.packer_types import (
    Placement, PlacementSlot, Posture, TruckState,
)
from modular_3d.transport.tests.transport_v2_fixtures import (
    default_site, default_trucks, generate_fixture,
)


# ── 공용 픽스처 헬퍼 ─────────────────────────────────────────────
_SHS = Section(
    name="SHS200x8", section_type="SHS",
    width=200, height=200, thickness=8, weight_per_m=47.9,
)


def _make_module(name: str, length=6000.0, width=3000.0, height=2700.0,
                 extra=5000.0) -> Module:
    return Module(name=name, length=length, width=width, height=height,
                  column_section=_SHS, beam_section=_SHS,
                  extra_weight_kg=extra)


def _make_floor(name: str, length=6000.0, width=2800.0, thickness=150.0,
                extra=2500.0) -> Panel:
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=thickness, beam_section=_SHS,
                 extra_weight_kg=extra)


def _make_wall(name: str, length=6000.0, height=3000.0, thickness=150.0,
               extra=600.0) -> Panel:
    """단순 wall: length=벽 길이, width=벽 높이, thickness=두께."""
    return Panel(name=name, kind="wall", width=height, length=length,
                 thickness=thickness, wall_height=height,
                 beam_section=_SHS, column_section=_SHS,
                 extra_weight_kg=extra)


def _make_lshape(name: str, length=6000.0, width=2800.0, thickness=150.0,
                 seg_side=0, seg_height=3000.0, seg_thickness=200.0,
                 extra=2500.0) -> Panel:
    seg_len = length if seg_side in (0, 2) else width
    seg = WallSegment(side=seg_side, start_offset_mm=0.0, length_mm=seg_len,
                      height_mm=seg_height, thickness_mm=seg_thickness,
                      column_section=_SHS, beam_section=_SHS)
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=thickness, beam_section=_SHS,
                 wall_segments=(seg,), extra_weight_kg=extra)


def _sp() -> SpacingParams:
    return SpacingParams()


def _site() -> SiteLimit:
    return SiteLimit(max_gvw_kg=None, max_width_mm=3500, max_height_mm=4500)


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


# ════════════════════════════════════════════════════════════════════
# §1 자세 / 자리 / 트럭 호환성 헬퍼
# ════════════════════════════════════════════════════════════════════
def test_postures_for_module_lying_only():
    m = _make_module("M1")
    assert _postures_for(m) == [Posture.LYING]


def test_postures_for_simple_wall_both():
    w = _make_wall("W1")
    assert _postures_for(w) == [Posture.LYING, Posture.STANDING]


def test_postures_for_dependent_floor_lying_only():
    p = _make_lshape("L1")
    assert _postures_for(p) == [Posture.LYING]


def test_postures_for_simple_floor_lying_only():
    f = _make_floor("F1")
    assert _postures_for(f) == [Posture.LYING]


def test_is_simple_wall_distinguishes_kinds():
    assert _is_simple_wall(_make_wall("W"))
    assert not _is_simple_wall(_make_floor("F"))
    assert not _is_simple_wall(_make_lshape("L"))
    assert not _is_simple_wall(_make_module("M"))


def test_is_dependent_floor():
    assert _is_dependent_floor(_make_lshape("L"))
    assert not _is_dependent_floor(_make_floor("F"))
    assert not _is_dependent_floor(_make_wall("W"))
    assert not _is_dependent_floor(_make_module("M"))


def test_truck_posture_compat_lowbed_lying_only():
    lb = _lowbed()
    assert _truck_posture_compatible(lb, Posture.LYING, _make_wall("W"))
    assert not _truck_posture_compatible(lb, Posture.STANDING, _make_wall("W"))


def test_truck_posture_compat_aframe_standing_simple_wall_only():
    af = _aframe()
    assert _truck_posture_compatible(af, Posture.STANDING, _make_wall("W"))
    assert not _truck_posture_compatible(af, Posture.STANDING, _make_floor("F"))
    assert not _truck_posture_compatible(af, Posture.LYING, _make_wall("W"))
    assert not _truck_posture_compatible(af, Posture.STANDING, _make_lshape("L"))


def test_allowed_slots_module_floor_only():
    assert _allowed_slots_for(_make_module("M"), Posture.LYING) == [PlacementSlot.FLOOR]


def test_allowed_slots_standing_floor_only():
    assert _allowed_slots_for(_make_wall("W"), Posture.STANDING) == [PlacementSlot.FLOOR]


def test_allowed_slots_panel_lying_all_three():
    slots = _allowed_slots_for(_make_floor("F"), Posture.LYING)
    assert set(slots) == {PlacementSlot.FLOOR, PlacementSlot.STACK, PlacementSlot.DEP_INNER}


# ════════════════════════════════════════════════════════════════════
# §2 evaluate_slot — Best-Fit 자리 점수
# ════════════════════════════════════════════════════════════════════
def test_evaluate_slot_returns_meta_dict_on_pass():
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    p = _make_floor("F1", length=5000.0)

    score, meta = evaluate_slot(p, Posture.LYING, PlacementSlot.FLOOR, ts,
                                (1.0, 1.0, 1.0), _site(), sp)
    assert score > 0
    assert meta is not None
    assert meta["slot"] == PlacementSlot.FLOOR
    assert meta["posture"] == Posture.LYING
    assert "xyz" in meta
    assert "parent_idx" in meta


def test_evaluate_slot_returns_minus_inf_on_safety_violation():
    """초중량 패널 → 무게 검사 위반 → -inf."""
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=1000.0)  # 매우 작은 한도
    # 패널이 한도를 훨씬 넘는 무게
    p = _make_floor("F_heavy", extra=100000.0)
    score, meta = evaluate_slot(p, Posture.LYING, PlacementSlot.FLOOR, ts,
                                (1.0, 1.0, 1.0), _site(), sp)
    assert score == float("-inf")
    assert meta is None


def test_evaluate_slot_stack_blocks_when_upper_larger_than_lower():
    """STACK 기하: 위 단 길이가 아래 단보다 크면 -inf."""
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    # 아래: 작은 패널, 위에 시도: 큰 패널
    small = _make_floor("F_small", length=4000.0, width=2400.0)
    big = _make_floor("F_big", length=8000.0, width=2800.0)
    # small 을 FLOOR 에 commit
    _, meta = evaluate_slot(small, Posture.LYING, PlacementSlot.FLOOR, ts,
                            (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, small, PlacementSlot.FLOOR, Posture.LYING, meta, sp)
    # big 을 STACK 시도 → 위 단(big) > 아래 단(small) → -inf
    score, meta_stack = evaluate_slot(big, Posture.LYING, PlacementSlot.STACK, ts,
                                       (1.0, 1.0, 1.0), _site(), sp)
    assert score == float("-inf")
    assert meta_stack is None


def test_evaluate_slot_stack_passes_when_upper_smaller_or_equal():
    """STACK 기하: 위 단이 아래보다 작거나 같으면 통과."""
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    big = _make_floor("F_big", length=8000.0, width=2800.0, thickness=150.0,
                     extra=3000.0)
    small = _make_floor("F_small", length=4000.0, width=2400.0, thickness=150.0,
                       extra=1500.0)
    _, meta = evaluate_slot(big, Posture.LYING, PlacementSlot.FLOOR, ts,
                            (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, big, PlacementSlot.FLOOR, Posture.LYING, meta, sp)
    score, meta_stack = evaluate_slot(small, Posture.LYING, PlacementSlot.STACK, ts,
                                       (1.0, 1.0, 1.0), _site(), sp)
    assert score > 0
    assert meta_stack is not None
    assert meta_stack["slot"] == PlacementSlot.STACK
    assert meta_stack["parent_idx"] == 0  # big 의 인덱스


def test_evaluate_slot_dep_inner_finds_parent():
    """DEP_INNER 자리: 종속 floor 부모 발견 + 빈 슬롯에 작은 패널 진입."""
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    # L자 종속 패널 (한 변에 wall) — 폭·길이 큰 편
    parent = _make_lshape("L1", length=8000.0, width=3000.0,
                          seg_side=0, seg_thickness=200.0)
    # 안쪽 슬롯에 들어갈 작은 패널
    child = _make_floor("F_inner", length=5000.0, width=2400.0, thickness=100.0,
                       extra=1200.0)
    _, meta_p = evaluate_slot(parent, Posture.LYING, PlacementSlot.FLOOR, ts,
                              (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, parent, PlacementSlot.FLOOR, Posture.LYING, meta_p, sp)
    score, meta_c = evaluate_slot(child, Posture.LYING, PlacementSlot.DEP_INNER, ts,
                                   (1.0, 1.0, 1.0), _site(), sp)
    assert score > 0
    assert meta_c is not None
    assert meta_c["slot"] == PlacementSlot.DEP_INNER
    assert meta_c["parent_idx"] == 0


def test_evaluate_slot_posture_dimension_lying_vs_standing():
    """단순 wall 의 LYING / STANDING 점수가 *서로 다른 차원*으로 평가됨."""
    tr_lb = _lowbed()
    tr_af = _aframe()
    sp = _sp()
    w = _make_wall("W1", length=6000.0, height=3000.0, thickness=150.0)

    ts_lb = TruckState(truck=tr_lb, effective_cargo_limit=20000.0)
    score_lying, _ = evaluate_slot(w, Posture.LYING, PlacementSlot.FLOOR, ts_lb,
                                    (1.0, 1.0, 1.0), _site(), sp)
    ts_af = TruckState(truck=tr_af, effective_cargo_limit=15000.0)
    score_standing, _ = evaluate_slot(w, Posture.STANDING, PlacementSlot.FLOOR, ts_af,
                                       (1.0, 1.0, 1.0), _site(), sp)
    # 둘 다 진입 가능 (양수 점수)
    assert score_lying > 0
    assert score_standing > 0


# ════════════════════════════════════════════════════════════════════
# §3 _commit_placement — 자원 누적
# ════════════════════════════════════════════════════════════════════
def test_commit_placement_updates_used_floor_length_and_weight():
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    p1 = _make_floor("F1", length=5000.0, extra=2000.0)
    _, meta1 = evaluate_slot(p1, Posture.LYING, PlacementSlot.FLOOR, ts,
                             (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, p1, PlacementSlot.FLOOR, Posture.LYING, meta1, sp)
    # used_floor_length 는 *순수 길이* (n_before=0 이라 gap=0)
    assert ts.used_floor_length == pytest.approx(p1.length)
    assert ts.used_cargo_weight == pytest.approx(p1.weight)
    # 첫 단 layer_max_thickness 갱신
    assert len(ts.layer_max_thickness) == 1


def test_commit_placement_accumulates_gap_after_first():
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    p1 = _make_floor("F1", length=4000.0, extra=1500.0)
    p2 = _make_floor("F2", length=3000.0, extra=1200.0)

    _, meta1 = evaluate_slot(p1, Posture.LYING, PlacementSlot.FLOOR, ts,
                             (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, p1, PlacementSlot.FLOOR, Posture.LYING, meta1, sp)
    _, meta2 = evaluate_slot(p2, Posture.LYING, PlacementSlot.FLOOR, ts,
                             (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, p2, PlacementSlot.FLOOR, Posture.LYING, meta2, sp)
    # 두 번째 commit 후 used_floor_length = L1 + gap + L2
    expected = p1.length + sp.panel_gap_mm + p2.length
    assert ts.used_floor_length == pytest.approx(expected)


def test_commit_placement_stack_adds_layer():
    """STACK commit 후 layer_max_thickness 새 단 추가."""
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    big = _make_floor("F_big", length=8000.0, width=2800.0, thickness=200.0,
                     extra=3500.0)
    small = _make_floor("F_small", length=4000.0, width=2400.0, thickness=150.0,
                       extra=1500.0)
    _, meta_b = evaluate_slot(big, Posture.LYING, PlacementSlot.FLOOR, ts,
                              (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, big, PlacementSlot.FLOOR, Posture.LYING, meta_b, sp)
    assert len(ts.layer_max_thickness) == 1
    _, meta_s = evaluate_slot(small, Posture.LYING, PlacementSlot.STACK, ts,
                              (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, small, PlacementSlot.STACK, Posture.LYING, meta_s, sp)
    assert len(ts.layer_max_thickness) == 2
    assert ts.n_layers == 2


# ════════════════════════════════════════════════════════════════════
# §4 _find_stack_bottom_idx / _find_dep_inner_parent_idx
# ════════════════════════════════════════════════════════════════════
def test_find_stack_bottom_idx_returns_none_when_empty():
    tr = _lowbed()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    assert _find_stack_bottom_idx(ts) is None


def test_find_stack_bottom_idx_returns_floor_placement_first():
    """LYING FLOOR 가 깔려있으면 그 인덱스 반환."""
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    p = _make_floor("F", length=5000.0)
    _, meta = evaluate_slot(p, Posture.LYING, PlacementSlot.FLOOR, ts,
                            (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, p, PlacementSlot.FLOOR, Posture.LYING, meta, sp)
    assert _find_stack_bottom_idx(ts) == 0


def test_find_dep_inner_parent_idx_returns_none_when_no_dependent():
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    # 일반 floor 만 깔림 — 종속 부모 없음
    f = _make_floor("F", length=5000.0)
    _, meta = evaluate_slot(f, Posture.LYING, PlacementSlot.FLOOR, ts,
                            (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, f, PlacementSlot.FLOOR, Posture.LYING, meta, sp)
    child = _make_floor("C", length=3000.0, width=2000.0)
    assert _find_dep_inner_parent_idx(ts, child, sp) is None


def test_find_dep_inner_parent_idx_finds_parent():
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    parent = _make_lshape("L", length=8000.0, width=3000.0, seg_side=0)
    _, meta = evaluate_slot(parent, Posture.LYING, PlacementSlot.FLOOR, ts,
                            (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, parent, PlacementSlot.FLOOR, Posture.LYING, meta, sp)
    child = _make_floor("C", length=5000.0, width=2400.0)
    assert _find_dep_inner_parent_idx(ts, child, sp) == 0


# ════════════════════════════════════════════════════════════════════
# §5 _compute_xyz — 좌표 계산
# ════════════════════════════════════════════════════════════════════
def test_compute_xyz_first_floor_is_left_edge_plus_half_length():
    """첫 FLOOR 화물은 트럭 좌측 끝 + 절반 위치."""
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    p = _make_floor("F", length=6000.0)
    xyz = _compute_xyz(ts, PlacementSlot.FLOOR, Posture.LYING, p, sp, None)
    usable = tr.max_length - 2 * sp.truck_edge_clearance_mm
    expected_x = -usable / 2 + p.length / 2
    assert xyz[0] == pytest.approx(expected_x)
    assert xyz[1] == 0.0
    assert xyz[2] == 0.0


# ════════════════════════════════════════════════════════════════════
# §6 _simulate_packing_weight — 비파괴 mini-FFD
# ════════════════════════════════════════════════════════════════════
def test_simulate_packing_weight_does_not_mutate_items():
    tr = _lowbed()
    items = [_make_floor(f"F{i}", length=4000.0, extra=1500.0) for i in range(5)]
    items_before = list(items)
    w = _simulate_packing_weight(tr, items, _site(), _sp())
    assert w > 0
    # 원본 리스트 ID 보존 + 길이 동일
    assert items == items_before


def test_simulate_packing_weight_zero_when_aframe_only_floor():
    """A-frame 트럭 + floor 만 → 아무것도 못 실음 → 0."""
    af = _aframe()
    items = [_make_floor("F1"), _make_floor("F2")]
    w = _simulate_packing_weight(af, items, _site(), _sp())
    assert w == 0.0


def test_simulate_packing_weight_aframe_only_simple_wall():
    """A-frame 트럭 + 단순 wall → 적재 가능 (STANDING)."""
    af = _aframe()
    items = [_make_wall("W1"), _make_wall("W2")]
    w = _simulate_packing_weight(af, items, _site(), _sp())
    assert w > 0


# ════════════════════════════════════════════════════════════════════
# §7 default_truck_score / cost_efficiency_truck_score
# ════════════════════════════════════════════════════════════════════
def test_default_truck_score_finite_for_capable_truck():
    tr = _lowbed()
    items = [_make_floor(f"F{i}", length=4000.0) for i in range(3)]
    score = default_truck_score(tr, items, _site(), _sp(),
                                "fixed_per_trip", default_eco_options())
    assert 0 < score < float("inf")


def test_default_truck_score_inf_when_zero_loadable():
    """A-frame 트럭에 floor 만 → 못 실음 → inf."""
    af = _aframe()
    items = [_make_floor("F1")]
    score = default_truck_score(af, items, _site(), _sp(),
                                "fixed_per_trip", default_eco_options())
    assert score == float("inf")


def test_cost_efficiency_score_is_fixed_over_capacity():
    tr = _lowbed()
    eco = default_eco_options()
    score = cost_efficiency_truck_score(tr, [], _site(), _sp(),
                                         "fixed_per_trip", eco)
    expected = eco.fixed_per_trip_rate / tr.max_weight
    assert score == pytest.approx(expected)


def test_default_truck_score_per_km_uses_round_trip():
    """모드 3 (per_km) 의 1회 비용 = km_rate × round_trip_km."""
    tr = _lowbed()
    eco = default_eco_options()
    items = [_make_floor(f"F{i}", length=4000.0, extra=2000.0) for i in range(3)]
    score = default_truck_score(tr, items, _site(), _sp(), "per_km", eco)
    assert 0 < score < float("inf")


# ════════════════════════════════════════════════════════════════════
# §8 pack_one_seed — 단일 패킹 진입점
# ════════════════════════════════════════════════════════════════════
def test_pack_one_seed_min_fixture_no_blocked():
    """min 픽스처 — 1 모듈 + 1 패널 모두 진입."""
    fx = generate_fixture("min")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(
        items, fx.trucks, fx.site, fx.spacing,
        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
        "fixed_per_trip", default_eco_options(),
    )
    assert len(res.blocked) == 0
    assert res.total_trips >= 1


def test_pack_one_seed_small_fixture_no_blocked():
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(
        items, fx.trucks, fx.site, fx.spacing,
        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
        "fixed_per_trip", default_eco_options(),
    )
    assert len(res.blocked) == 0


def test_pack_one_seed_medium_fixture_no_blocked():
    fx = generate_fixture("medium")
    items = list(fx.modules) + list(fx.panels)
    res = pack_one_seed(
        items, fx.trucks, fx.site, fx.spacing,
        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
        "fixed_per_trip", default_eco_options(),
    )
    assert len(res.blocked) == 0


def test_pack_one_seed_reproducible():
    """같은 입력 두 번 → 같은 회차 수 + 같은 화물 분배."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    r1 = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    r2 = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    assert r1.total_trips == r2.total_trips
    for t1, t2 in zip(r1.trips, r2.trips):
        assert [i.name for i in t1.items] == [i.name for i in t2.items]


def test_pack_one_seed_shuffled_no_blocked():
    """random.shuffle 후에도 SHUFFLED_STRATEGY 로 패킹 → blocked=0."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    rng = random.Random(42)
    rng.shuffle(items)
    res = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                       SHUFFLED_STRATEGY, WEIGHT_SETS[0],
                       "fixed_per_trip", default_eco_options())
    assert len(res.blocked) == 0


def test_pack_one_seed_all_strategies_no_blocked():
    """4 그리디 전략 × 6 가중치 — 모든 조합에서 small 픽스처 blocked=0."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    for strategy in GREEDY_STRATEGIES:
        for weights in WEIGHT_SETS:
            res = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                               strategy, weights,
                               "fixed_per_trip", default_eco_options())
            assert len(res.blocked) == 0, (
                f"{strategy.name}+{weights} 에서 blocked {len(res.blocked)} 발생"
            )


# ════════════════════════════════════════════════════════════════════
# §9 모듈 합산 4.5m 페어링 — _merge_small_modules
# ════════════════════════════════════════════════════════════════════
def test_merge_small_modules_pairs_two_short_modules():
    """두 짧은 모듈(≤4.5m)이 한 회차에 묶임."""
    m1 = _make_module("M_short_1", length=4500.0, extra=4000.0)
    m2 = _make_module("M_short_2", length=4500.0, extra=4000.0)
    trucks = [_lowbed()]
    res = pack_one_seed(
        [m1, m2], trucks, _site(), _sp(),
        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
        "fixed_per_trip", default_eco_options(),
    )
    # 두 모듈 모두 진입 + 회차 1 (합산 성공)
    assert len(res.blocked) == 0
    assert res.total_trips == 1
    assert len(res.trips[0].items) == 2


def test_merge_small_modules_skips_long_modules():
    """5m 초과 모듈은 합산 X — 각각 별도 회차."""
    m1 = _make_module("M_long_1", length=9000.0, extra=8000.0)
    m2 = _make_module("M_long_2", length=9000.0, extra=8000.0)
    trucks = [_lowbed()]
    res = pack_one_seed(
        [m1, m2], trucks, _site(), _sp(),
        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
        "fixed_per_trip", default_eco_options(),
    )
    assert len(res.blocked) == 0
    assert res.total_trips == 2


def test_merge_small_modules_one_short_one_long_no_merge():
    """한 모듈은 짧고(≤4.5m), 다른 모듈은 긴 경우 — 합산 X."""
    m1 = _make_module("M_short", length=4500.0, extra=4000.0)
    m2 = _make_module("M_long", length=9000.0, extra=8000.0)
    trucks = [_lowbed()]
    res = pack_one_seed(
        [m1, m2], trucks, _site(), _sp(),
        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
        "fixed_per_trip", default_eco_options(),
    )
    assert len(res.blocked) == 0
    assert res.total_trips == 2


# ════════════════════════════════════════════════════════════════════
# §10 _truck_state_to_trip — 역호환 변환
# ════════════════════════════════════════════════════════════════════
def test_truck_state_to_trip_preserves_placements():
    tr = _lowbed()
    sp = _sp()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    p = _make_floor("F", length=5000.0)
    _, meta = evaluate_slot(p, Posture.LYING, PlacementSlot.FLOOR, ts,
                            (1.0, 1.0, 1.0), _site(), sp)
    _commit_placement(ts, p, PlacementSlot.FLOOR, Posture.LYING, meta, sp)
    trip = _truck_state_to_trip(ts, trip_no=1, sp=sp)
    assert trip.trip_no == 1
    assert trip.truck is tr
    assert len(trip.placements) == 1
    assert len(trip.items) == 1


def test_truck_state_to_trip_marks_mixed_posture():
    """LYING + STANDING 둘 다 든 trip 은 has_mixed_posture=True."""
    # 직접 ts 조립 — 단순 wall LYING + STANDING (현실에선 같은 트럭에서 불가하지만 데이터 변환 테스트)
    tr = _lowbed()
    ts = TruckState(truck=tr, effective_cargo_limit=20000.0)
    w1 = _make_wall("W_lying")
    ts.placements.append(Placement(
        item=w1, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
        truck_xyz=(0.0, 0.0, 0.0), parent_idx=None,
    ))
    ts.placements.append(Placement(
        item=w1, slot=PlacementSlot.FLOOR, posture=Posture.STANDING,
        truck_xyz=(1000.0, 0.0, 0.0), parent_idx=None,
    ))
    trip = _truck_state_to_trip(ts, trip_no=1, sp=_sp())
    assert trip.has_mixed_posture is True
    assert trip.standing_count == 1


# ════════════════════════════════════════════════════════════════════
# §11 _select_best_new_truck
# ════════════════════════════════════════════════════════════════════
def test_select_best_new_truck_returns_compatible():
    """단순 wall + lowbed/extendable 트럭만 → LYING 호환 트럭 반환."""
    w = _make_wall("W")
    trucks = default_trucks()
    chosen = _select_best_new_truck(
        w, trucks, [w], GREEDY_STRATEGIES[0], _site(), _sp(),
        "fixed_per_trip", default_eco_options(),
    )
    assert chosen is not None
    # lowbed/extendable/aframe 모두 호환 가능 — 점수 최저가 채택됨


def test_select_best_new_truck_none_when_too_big():
    """트럭보다 큰 화물 → None."""
    too_big = _make_floor("Big", length=99999.0, width=3000.0)
    trucks = default_trucks()
    chosen = _select_best_new_truck(
        too_big, trucks, [too_big], GREEDY_STRATEGIES[0], _site(), _sp(),
        "fixed_per_trip", default_eco_options(),
    )
    assert chosen is None


# ════════════════════════════════════════════════════════════════════
# §12 _dependent_first_sorter
# ════════════════════════════════════════════════════════════════════
def test_dependent_first_sorter_puts_dependent_panels_first():
    f = _make_floor("F")
    L = _make_lshape("L")
    w = _make_wall("W")
    m = _make_module("M")
    sorted_items = _dependent_first_sorter([f, m, L, w])
    # L 가 가장 앞 (종속 first), 나머지는 무게 내림차순
    assert sorted_items[0] is L


# ════════════════════════════════════════════════════════════════════
# §13 회귀 — 기존 packer.py 가 여전히 동작
# ════════════════════════════════════════════════════════════════════
def test_existing_pack_items_still_works():
    """Phase 4 코드 도입이 기존 packer.pack_items 의 회귀를 깨지 않음."""
    from modular_3d.transport.packer import pack_items
    fx = generate_fixture("small")
    res = pack_items(fx.modules, fx.panels, fx.trucks, fx.site, fx.spacing)
    assert res.total_trips >= 1
    assert len(res.blocked) == 0
