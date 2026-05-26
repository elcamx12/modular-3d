"""Phase 6 — VND (Variable Neighborhood Descent) 5 종 수술.

[설계 근거]
- `운송 로직 의사코드.md` § 2.7 그대로 구현.
- 5 수술: Transfer / Swap / Posture Toggle / Merge / Truck Downgrade
- 작은 수술 → 큰 수술 순. 큰 수술 개선 시 작은 수술로 복귀.
- 비용 감소 발견 즉시 채택 (best-improvement 아니라 first-improvement).

[자세 토글 보강 — K]
- 자세 바뀌면 점유 차원 바뀜 → truck_xyz z + layer_max_thickness 자동 재계산
- 본 코드는 `_build_truck_state_without` + `_try_add_to_truck` 의 합성으로 자동 처리
- _commit_placement 가 _item_dims_for_posture 로 자세별 차원을 받아 단별 두께 갱신

[모듈 합산 보강 — J]
- Transfer/Swap/Merge 가 모듈에 적용될 때 결과 회차 모듈 수 ≤ 2 + 둘 다 길이 ≤ 4500 mm 검사
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .models import Module, SiteLimit, SpacingParams, Truck
from .packer import PackResult, Trip, _effective_cargo_limit
from .packer_core import (
    EcoOptions,
    _allowed_slots_for,
    _commit_placement,
    _compute_xyz,
    _is_simple_wall,
    _postures_for,
    _truck_posture_compatible,
    _truck_state_to_trip,
    default_eco_options,
    evaluate_slot,
)
from .packer_meta import compute_cost, trip_cost
from .packer_types import Placement, PlacementSlot, Posture, TruckState


# ════════════════════════════════════════════════════════════════════
# Trip ↔ TruckState 변환 헬퍼
# ════════════════════════════════════════════════════════════════════
def _trip_to_truck_state(
    trip: Trip, site: SiteLimit, sp: SpacingParams,
) -> TruckState:
    """Trip.placements 로부터 TruckState 재구성.

    placement 의 xyz 는 *원본 값을 그대로* 보존 — VND 비용 비교 단계에서만
    쓰이고 최종 시각 좌표는 Phase 7 무게중심 보정에서 다시 잡힘.
    """
    ts = TruckState(
        truck=trip.truck,
        effective_cargo_limit=_effective_cargo_limit(trip.truck, site),
    )
    for p in trip.placements:
        meta = {"xyz": p.truck_xyz, "parent_idx": p.parent_idx}
        _commit_placement(ts, p.item, p.slot, p.posture, meta, sp)
    return ts


def _pack_to_truck_states(
    pack: PackResult, site: SiteLimit, sp: SpacingParams,
) -> List[TruckState]:
    return [_trip_to_truck_state(t, site, sp) for t in pack.trips]


def _truck_states_to_pack(
    truck_states: Sequence[TruckState],
    blocked: list,
    sp: SpacingParams,
) -> PackResult:
    trips = [
        _truck_state_to_trip(ts, trip_no=i + 1, sp=sp)
        for i, ts in enumerate(truck_states)
    ]
    return PackResult(trips=trips, blocked=blocked)


# ════════════════════════════════════════════════════════════════════
# TruckState 조작 — 한 placement 제거 / 한 화물 추가
# ════════════════════════════════════════════════════════════════════
def _clone_truck_state(ts: TruckState, sp: SpacingParams) -> TruckState:
    """TruckState 깊은 복사 — placements 의 누적 자원도 다시 누적."""
    new_ts = TruckState(
        truck=ts.truck, effective_cargo_limit=ts.effective_cargo_limit,
    )
    for p in ts.placements:
        meta = {"xyz": p.truck_xyz, "parent_idx": p.parent_idx}
        _commit_placement(new_ts, p.item, p.slot, p.posture, meta, sp)
    return new_ts


def _build_truck_state_without(
    ts: TruckState, p_idx: int, sp: SpacingParams,
) -> Optional[TruckState]:
    """ts 에서 p_idx 의 placement 를 제거한 새 TruckState.

    자식 placement (parent_idx == p_idx) 가 있으면 None 반환 (불가).
    제거 후 parent_idx 가 p_idx 보다 큰 자식의 인덱스는 -1 보정.
    """
    has_children = any(p.parent_idx == p_idx for p in ts.placements)
    if has_children:
        return None

    new_ts = TruckState(
        truck=ts.truck, effective_cargo_limit=ts.effective_cargo_limit,
    )
    for i, p in enumerate(ts.placements):
        if i == p_idx:
            continue
        new_parent = p.parent_idx
        if new_parent is not None and new_parent > p_idx:
            new_parent -= 1
        meta = {"xyz": p.truck_xyz, "parent_idx": new_parent}
        _commit_placement(new_ts, p.item, p.slot, p.posture, meta, sp)
    return new_ts


def _try_add_to_truck(
    ts: TruckState, item, site: SiteLimit, sp: SpacingParams,
    weights: Tuple[float, float, float] = (1.0, 1.0, 1.0),
    forced_posture: Optional[Posture] = None,
) -> Optional[TruckState]:
    """item 을 ts 에 Best-Fit 으로 추가 시도. 성공 시 *새* TruckState 반환.

    forced_posture 가 주어지면 그 자세로만 시도 (Posture Toggle 수술용).
    """
    postures = [forced_posture] if forced_posture is not None else _postures_for(item)

    best_score = float("-inf")
    best_choice: Optional[Tuple[PlacementSlot, Posture, dict]] = None
    for posture in postures:
        if posture is None or not _truck_posture_compatible(ts.truck, posture, item):
            continue
        for slot in _allowed_slots_for(item, posture):
            score, meta = evaluate_slot(item, posture, slot, ts, weights, site, sp)
            if meta is not None and score > best_score:
                best_score = score
                best_choice = (slot, posture, meta)

    if best_choice is None:
        return None

    new_ts = _clone_truck_state(ts, sp)
    slot, posture, meta = best_choice
    _commit_placement(new_ts, item, slot, posture, meta, sp)
    return new_ts


# ════════════════════════════════════════════════════════════════════
# 5 수술 — 각 함수: (pack, trucks, site, sp, cost_mode, eco_options)
#                  → (improved: bool, pack: PackResult)
# ════════════════════════════════════════════════════════════════════
def _check_module_merge_constraint(
    target_state: TruckState, new_item, _site: SiteLimit, _sp: SpacingParams,
) -> bool:
    """target_state 에 new_item 을 추가했을 때 모듈 합산 규칙을 만족하는가.

    - 두 모듈 회차에 모듈 추가 금지 (3 개 불가)
    - 한 모듈 회차에 모듈 추가 시 둘 다 길이 ≤ 4500
    """
    if not isinstance(new_item, Module):
        return True
    existing_modules = [
        p.item for p in target_state.placements if isinstance(p.item, Module)
    ]
    if len(existing_modules) >= 2:
        return False
    for m in existing_modules:
        if m.length > 4500.0 + 1e-6 or new_item.length > 4500.0 + 1e-6:
            return False
    return True


def op_transfer(
    pack: PackResult, trucks: Sequence[Truck], site: SiteLimit, sp: SpacingParams,
    cost_mode: str, eco_options: EcoOptions,
) -> Tuple[bool, PackResult]:
    """회차 A의 화물 한 장을 B로 이동. 비용 감소 발견 시 즉시 채택."""
    truck_states = _pack_to_truck_states(pack, site, sp)
    current_cost = compute_cost(pack, cost_mode, eco_options)

    for a_idx in range(len(truck_states)):
        ts_a = truck_states[a_idx]
        for pa_idx in range(len(ts_a.placements)):
            placement = ts_a.placements[pa_idx]
            # 자식 있는 부모는 skip
            if any(p.parent_idx == pa_idx for p in ts_a.placements):
                continue
            item = placement.item

            for b_idx in range(len(truck_states)):
                if b_idx == a_idx:
                    continue
                ts_b = truck_states[b_idx]

                # 모듈 합산 검사
                if not _check_module_merge_constraint(ts_b, item, site, sp):
                    continue

                new_a = _build_truck_state_without(ts_a, pa_idx, sp)
                if new_a is None:
                    continue
                new_b = _try_add_to_truck(ts_b, item, site, sp)
                if new_b is None:
                    continue

                new_states = list(truck_states)
                if new_a.placements:
                    new_states[a_idx] = new_a
                    new_states[b_idx] = new_b
                else:
                    # a 회차 비어버림 → 삭제 (회차 1 절감)
                    new_states.pop(a_idx)
                    actual_b = b_idx - 1 if b_idx > a_idx else b_idx
                    new_states[actual_b] = new_b

                new_pack = _truck_states_to_pack(new_states, pack.blocked, sp)
                new_cost = compute_cost(new_pack, cost_mode, eco_options)
                if new_cost < current_cost - 1e-6:
                    return True, new_pack

    return False, pack


def op_swap(
    pack: PackResult, trucks: Sequence[Truck], site: SiteLimit, sp: SpacingParams,
    cost_mode: str, eco_options: EcoOptions,
) -> Tuple[bool, PackResult]:
    """회차 A의 한 장 ↔ B의 한 장 교환. 비용 감소 시 채택."""
    truck_states = _pack_to_truck_states(pack, site, sp)
    current_cost = compute_cost(pack, cost_mode, eco_options)

    for a_idx in range(len(truck_states)):
        for b_idx in range(a_idx + 1, len(truck_states)):
            ts_a = truck_states[a_idx]
            ts_b = truck_states[b_idx]
            for pa_idx in range(len(ts_a.placements)):
                if any(p.parent_idx == pa_idx for p in ts_a.placements):
                    continue
                for pb_idx in range(len(ts_b.placements)):
                    if any(p.parent_idx == pb_idx for p in ts_b.placements):
                        continue
                    item_a = ts_a.placements[pa_idx].item
                    item_b = ts_b.placements[pb_idx].item

                    # 모듈 합산 검사 — 교환 후 각 회차의 모듈 개수
                    if isinstance(item_b, Module) or isinstance(item_a, Module):
                        # 단순화: 모듈이 끼는 swap 은 module merge constraint
                        # 양쪽 모두 통과해야
                        pass  # 아래 _check_module_merge_constraint 가 알아서

                    new_a = _build_truck_state_without(ts_a, pa_idx, sp)
                    new_b = _build_truck_state_without(ts_b, pb_idx, sp)
                    if new_a is None or new_b is None:
                        continue

                    if not _check_module_merge_constraint(new_a, item_b, site, sp):
                        continue
                    if not _check_module_merge_constraint(new_b, item_a, site, sp):
                        continue

                    new_a_added = _try_add_to_truck(new_a, item_b, site, sp)
                    new_b_added = _try_add_to_truck(new_b, item_a, site, sp)
                    if new_a_added is None or new_b_added is None:
                        continue

                    new_states = list(truck_states)
                    new_states[a_idx] = new_a_added
                    new_states[b_idx] = new_b_added
                    new_pack = _truck_states_to_pack(new_states, pack.blocked, sp)
                    new_cost = compute_cost(new_pack, cost_mode, eco_options)
                    if new_cost < current_cost - 1e-6:
                        return True, new_pack

    return False, pack


def op_posture_toggle(
    pack: PackResult, trucks: Sequence[Truck], site: SiteLimit, sp: SpacingParams,
    cost_mode: str, eco_options: EcoOptions,
) -> Tuple[bool, PackResult]:
    """단순 wall 패널 한 장의 자세 토글 + 재배치. 비용 감소 시 채택.

    자세 토글은 *같은 트럭 안에서만* — 트럭 종류 변경(A-frame 등)이 필요한
    토글은 op_transfer 가 함께 처리.
    """
    truck_states = _pack_to_truck_states(pack, site, sp)
    current_cost = compute_cost(pack, cost_mode, eco_options)

    for a_idx, ts_a in enumerate(truck_states):
        for p_idx in range(len(ts_a.placements)):
            placement = ts_a.placements[p_idx]
            item = placement.item
            if not _is_simple_wall(item):
                continue
            if any(p.parent_idx == p_idx for p in ts_a.placements):
                continue

            new_posture = (
                Posture.STANDING if placement.posture == Posture.LYING
                else Posture.LYING
            )
            if not _truck_posture_compatible(ts_a.truck, new_posture, item):
                continue

            new_ts = _build_truck_state_without(ts_a, p_idx, sp)
            if new_ts is None:
                continue
            new_ts2 = _try_add_to_truck(
                new_ts, item, site, sp, forced_posture=new_posture,
            )
            if new_ts2 is None:
                continue

            new_states = list(truck_states)
            new_states[a_idx] = new_ts2
            new_pack = _truck_states_to_pack(new_states, pack.blocked, sp)
            new_cost = compute_cost(new_pack, cost_mode, eco_options)
            if new_cost < current_cost - 1e-6:
                return True, new_pack

    return False, pack


def op_merge(
    pack: PackResult, trucks: Sequence[Truck], site: SiteLimit, sp: SpacingParams,
    cost_mode: str, eco_options: EcoOptions,
) -> Tuple[bool, PackResult]:
    """두 회차 A·B의 모든 화물을 한 트럭에 다시 패킹. 성공 시 회차 1 절감."""
    truck_states = _pack_to_truck_states(pack, site, sp)
    current_cost = compute_cost(pack, cost_mode, eco_options)

    for a_idx in range(len(truck_states)):
        for b_idx in range(a_idx + 1, len(truck_states)):
            ts_a = truck_states[a_idx]
            ts_b = truck_states[b_idx]
            all_items = (
                [p.item for p in ts_a.placements]
                + [p.item for p in ts_b.placements]
            )
            # 모듈 합산 검사
            modules = [it for it in all_items if isinstance(it, Module)]
            if len(modules) > 2:
                continue
            if (
                len(modules) == 2
                and not all(m.length <= 4500.0 + 1e-6 for m in modules)
            ):
                continue

            # 시도 트럭 — A 또는 B 트럭, 그리고 trucks 중 사용 안 한 더 큰 active 트럭도 후보
            candidate_trucks = [ts_a.truck, ts_b.truck]
            for tr in trucks:
                if not tr.active:
                    continue
                if tr.name not in [c.name for c in candidate_trucks]:
                    candidate_trucks.append(tr)

            for target_truck in candidate_trucks:
                ts_test = TruckState(
                    truck=target_truck,
                    effective_cargo_limit=_effective_cargo_limit(target_truck, site),
                )
                # 무게 내림차순으로 재배치 시도 (간이 그리디)
                items_sorted = sorted(
                    all_items,
                    key=lambda x: -getattr(x, "weight", 0.0),
                )
                success = True
                for it in items_sorted:
                    new_ts = _try_add_to_truck(ts_test, it, site, sp)
                    if new_ts is None:
                        success = False
                        break
                    ts_test = new_ts
                if not success:
                    continue

                # 성공 — 회차 1 절감
                new_states = [
                    ts for i, ts in enumerate(truck_states)
                    if i not in (a_idx, b_idx)
                ]
                new_states.append(ts_test)
                new_pack = _truck_states_to_pack(new_states, pack.blocked, sp)
                new_cost = compute_cost(new_pack, cost_mode, eco_options)
                if new_cost < current_cost - 1e-6:
                    return True, new_pack

    return False, pack


def op_truck_downgrade(
    pack: PackResult, trucks: Sequence[Truck], site: SiteLimit, sp: SpacingParams,
    cost_mode: str, eco_options: EcoOptions,
) -> Tuple[bool, PackResult]:
    """회차의 트럭을 더 싼 트럭으로 교체 시도. 성공 시 비용 감소."""
    truck_states = _pack_to_truck_states(pack, site, sp)
    current_cost = compute_cost(pack, cost_mode, eco_options)

    for a_idx, ts_a in enumerate(truck_states):
        for cand_truck in trucks:
            if not cand_truck.active:
                continue
            if cand_truck.name == ts_a.truck.name:
                continue
            # 비용 비교 — 단가 더 싼 트럭만
            cur_trip = Trip(trip_no=0, truck=ts_a.truck)
            cand_trip = Trip(trip_no=0, truck=cand_truck)
            cost_cur = trip_cost(cur_trip, cost_mode, eco_options)
            cost_cand = trip_cost(cand_trip, cost_mode, eco_options)
            if cost_cand >= cost_cur - 1e-6:
                continue

            # 후보 트럭에 모든 화물 재배치
            ts_test = TruckState(
                truck=cand_truck,
                effective_cargo_limit=_effective_cargo_limit(cand_truck, site),
            )
            items_sorted = sorted(
                [p.item for p in ts_a.placements],
                key=lambda x: -getattr(x, "weight", 0.0),
            )
            success = True
            for it in items_sorted:
                new_ts = _try_add_to_truck(ts_test, it, site, sp)
                if new_ts is None:
                    success = False
                    break
                ts_test = new_ts
            if not success:
                continue

            new_states = list(truck_states)
            new_states[a_idx] = ts_test
            new_pack = _truck_states_to_pack(new_states, pack.blocked, sp)
            new_cost = compute_cost(new_pack, cost_mode, eco_options)
            if new_cost < current_cost - 1e-6:
                return True, new_pack

    return False, pack


# ════════════════════════════════════════════════════════════════════
# VND 메인 루프 — 의사코드 § 2.7
# ════════════════════════════════════════════════════════════════════
def vnd_improve(
    pack: PackResult,
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    cost_mode: str = "fixed_per_trip",
    eco_options: Optional[EcoOptions] = None,
    max_iter: Optional[int] = None,
) -> PackResult:
    """VND — 작은 수술 → 큰 수술 순. 큰 수술 개선 시 작은 수술로 복귀.

    Args:
        pack: 초기 해
        trucks: 사용 가능 트럭 목록 (active=False 자동 제외)
        site, sp: 현장·간격 파라미터
        cost_mode: 비용 모드
        eco_options: 비용 단가 — None 이면 default_eco_options()
        max_iter: 최대 수술 시도 횟수 (None = 더 이상 개선 없음까지)

    Returns:
        VND 가 더 이상 개선 못하는 해.
    """
    if eco_options is None:
        eco_options = default_eco_options()

    operations = [
        op_transfer,
        op_swap,
        op_posture_toggle,
        op_merge,
        op_truck_downgrade,
    ]
    iter_count = 0
    op_idx = 0
    while op_idx < len(operations):
        if max_iter is not None and iter_count >= max_iter:
            break
        improved, pack = operations[op_idx](
            pack, trucks, site, sp, cost_mode, eco_options,
        )
        iter_count += 1
        if improved and op_idx > 0:
            op_idx = 0  # 작은 수술로 복귀
        elif not improved:
            op_idx += 1
        # improved 이고 op_idx == 0 → 같은 수술 다시 시도 (op_idx 유지)

    return pack


__all__ = [
    "vnd_improve",
    "op_transfer",
    "op_swap",
    "op_posture_toggle",
    "op_merge",
    "op_truck_downgrade",
    "_trip_to_truck_state",
    "_pack_to_truck_states",
    "_truck_states_to_pack",
    "_clone_truck_state",
    "_build_truck_state_without",
    "_try_add_to_truck",
]
