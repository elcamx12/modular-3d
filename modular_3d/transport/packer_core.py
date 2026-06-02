"""Phase 4 — Best-Fit 자유 배치 단일 패킹 (`pack_one_seed`).

[설계 근거]
- `운송 로직 의사코드.md` § 2.3 ~ § 2.5, § 2.11 그대로 구현.
- 그리디 전략 4 종 + 가중치 6 조합 + 트럭 선정 정확 시뮬레이션 + 모듈 6m 합산.

[자리·자세·기하 제약 요약]
- LYING 자세: Module / 종속 floor / 단순 floor / 단순 wall — 트럭 lowbed·extendable
- STANDING 자세: 단순 wall + A-frame 트럭 전용 — A-frame 은 STANDING 만 허용
- FLOOR: 모든 자세 가능 (단, Module 은 FLOOR 만, 적층 금지)
- STACK: LYING 만 + 기하 제약 (위 단 길이 ≤ 아래 단 길이 AND 폭 ≤ 폭)
- DEP_INNER: LYING + 종속 floor 의 빈 슬롯 (한 부모 위 1 매)

[수치 일관성 — packer_safety 와 맞춤]
- `truck_state.used_floor_length` 는 *gap 포함 누적값*.
  · packer_safety.can_place 의 길이 검사 식과 일관:
    needed = used_floor_length + (n>0 ? gap : 0) + length
  · `_commit_placement` 도 `used_floor_length += gap + length` 로 누적.
- 자리 평가 식 (evaluate_slot) 도 동일 식 사용:
    remaining_len = usable − used_floor_length − new_gap

[Phase 8 통합 예정]
- pack_items 진입점에서 본 모듈의 pack_one_seed → packer_meta.pack_all_seeds 호출
- VND 와 무게중심 보정도 같은 흐름.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from .models import (
    MODULE_PAIR_MAX_LEN_MM, Module, Panel, SiteLimit, SpacingParams, Truck,
)
from .packer import (
    PackResult,
    Trip,
    _dependent_free_inner_dims,
    _diagnose_blocked,
    _effective_cargo_limit,
)
from .packer_safety import _item_dims_for_posture, can_place
from .packer_types import (
    Item,
    Placement,
    PlacementSlot,
    Posture,
    TruckState,
)


# ════════════════════════════════════════════════════════════════════
# 비용 옵션 — 2026-05-27 단일 진실원 통합
# ════════════════════════════════════════════════════════════════════
# 이전엔 본 모듈의 EcoOptions 와 economics.py 의 EconomicsOptions 가 *별개로*
# 살아 단가 불일치 버그 유발. 통합 — EconomicsOptions 가 단일 진실원이고
# 본 모듈은 호환성 위해 alias 로 import 만 한다.
from .economics import EconomicsOptions as EcoOptions


def default_eco_options() -> "EcoOptions":
    """기본 비용 옵션 — EconomicsOptions() 인스턴스."""
    return EcoOptions()


# ────────────────────────────────────────────────────────────────────
# 자세 / 자리 / 트럭 호환성 헬퍼
# ────────────────────────────────────────────────────────────────────
def _is_simple_wall(item: Item) -> bool:
    """단순 wall 패널(kind=wall, wall_segments 미사용)인가."""
    return (
        isinstance(item, Panel)
        and item.kind == "wall"
        and not item.wall_segments
    )


def _is_dependent_floor(item: Item) -> bool:
    """종속 floor (wall_segments 가 있거나 호환용 kind=lshape)인가."""
    if not isinstance(item, Panel):
        return False
    return bool(item.wall_segments) or item.kind == "lshape"


def _postures_for(item: Item) -> List[Posture]:
    """이 화물에 시도할 자세 후보.

    - 단순 wall 패널 → [LYING, STANDING] 둘 다 평가
    - 그 외(Module / 종속 floor / 단순 floor) → [LYING] 만
    """
    if _is_simple_wall(item):
        return [Posture.LYING, Posture.STANDING]
    return [Posture.LYING]


def _truck_posture_compatible(truck: Truck, posture: Posture, item: Item) -> bool:
    """트럭 — 자세 호환성.

    - A-frame 트럭: 단순 wall STANDING 만 받음
    - lowbed / extendable: LYING 만 (STANDING 은 A-frame 전용)
    """
    if truck.truck_type == "aframe":
        return posture == Posture.STANDING and _is_simple_wall(item)
    # lowbed / extendable
    return posture == Posture.LYING


def _allowed_slots_for(item: Item, posture: Posture) -> List[PlacementSlot]:
    """자세·화물 종류별 허용 자리.

    - STANDING → [FLOOR] (적층 금지 — 세운 패널 위에 못 올림)
    - LYING + Module → [FLOOR] (모듈 적층 금지 — 계획서 § 5.7 H)
    - LYING + Panel → [FLOOR, STACK, DEP_INNER]
    """
    if posture == Posture.STANDING:
        return [PlacementSlot.FLOOR]
    if isinstance(item, Module):
        return [PlacementSlot.FLOOR]
    return [PlacementSlot.FLOOR, PlacementSlot.STACK, PlacementSlot.DEP_INNER]


def _volume_of(item: Item) -> float:
    """볼륨 정렬용 (mm³)."""
    if isinstance(item, Module):
        return item.length * item.width * item.height
    # Panel — wall_segments 의 max 높이는 제외 (단일 점유 두께만)
    return item.length * item.width * item.thickness


# ────────────────────────────────────────────────────────────────────
# 적층·종속 슬롯 부모 찾기
# ────────────────────────────────────────────────────────────────────
def _find_stack_bottom_idx(truck_state: TruckState) -> Optional[int]:
    """STACK 자리: 새 단 아래에 깔릴 placement 인덱스.

    정책: 현재 가장 위 단(z 좌표 최대)의 LYING + FLOOR/STACK 첫 placement.
    적층 기하 제약(위 ≤ 아래)은 호출자(evaluate_slot)가 별도 검사.

    [규칙 — 2026-05-27 사용자 결정]
    *모듈 위에는 아무것도 못 올림* (모듈 높이 ≈ 3.4m, 운송 한계 근접). 따라서
    Module placement 는 STACK 부모 후보에서 제외. 결과적으로 *바닥패널은
    바닥패널 위에만* 적층 가능 — 상식적 운송과 일치.
    """
    best_idx: Optional[int] = None
    best_z = -float("inf")
    for i, p in enumerate(truck_state.placements):
        if p.posture != Posture.LYING:
            continue
        if p.slot not in (PlacementSlot.FLOOR, PlacementSlot.STACK):
            continue
        # 모듈 위 적층 금지 (사용자 결정)
        if isinstance(p.item, Module):
            continue
        z = p.truck_xyz[2]
        if z > best_z:
            best_z = z
            best_idx = i
    return best_idx


def _find_dep_inner_parent_idx(
    truck_state: TruckState, item: Item, sp: SpacingParams
) -> Optional[int]:
    """DEP_INNER 자리: 이 item 을 종속 패널의 빈 슬롯에 넣을 수 있는 부모 인덱스.

    조건:
    - 부모 = LYING FLOOR 종속 floor (wall_segments 있음)
    - 그 부모 위에 이미 다른 DEP_INNER 가 없음
    - item 의 (LYING) 폭·길이가 free_inner dims 안에 들어감
    """
    if not isinstance(item, Panel):
        return None
    item_len, item_w, _, _ = _item_dims_for_posture(item, Posture.LYING)
    for i, p in enumerate(truck_state.placements):
        if not isinstance(p.item, Panel) or not p.item.wall_segments:
            continue
        if p.slot != PlacementSlot.FLOOR or p.posture != Posture.LYING:
            continue
        # 이미 다른 DEP_INNER 가 이 부모 위에 있는지
        already = any(
            q.slot == PlacementSlot.DEP_INNER and q.parent_idx == i
            for q in truck_state.placements
        )
        if already:
            continue
        free_w, free_l = _dependent_free_inner_dims(p.item, sp)
        if item_w <= free_w + 1e-6 and item_len <= free_l + 1e-6:
            return i
    return None


# ────────────────────────────────────────────────────────────────────
# xyz 좌표 계산 — 트럭 적재함 중심 원점
# ────────────────────────────────────────────────────────────────────
def _compute_xyz(
    truck_state: TruckState,
    slot: PlacementSlot,
    posture: Posture,
    item: Item,
    sp: SpacingParams,
    parent_idx: Optional[int],
) -> Tuple[float, float, float]:
    """트럭 적재함 중심 원점 좌표 (mm)."""
    tr = truck_state.truck
    usable = tr.max_length - 2.0 * sp.truck_edge_clearance_mm
    L_eff, _W_eff, _h_occ, _ = _item_dims_for_posture(item, posture)

    if slot == PlacementSlot.FLOOR:
        # 트럭 좌측 끝 + 현 누적 길이(gap 포함) + (새 gap) + 패널 절반 (중앙 정렬)
        x_left_edge = -usable / 2.0
        new_gap = sp.panel_gap_mm if truck_state.placements else 0.0
        x = x_left_edge + truck_state.used_floor_length + new_gap + L_eff / 2.0
        y = 0.0  # 폭 방향 중앙 (Phase 7 무게중심 보정 단계에서 미세 슬라이드)
        z = 0.0
        return (x, y, z)

    if slot == PlacementSlot.STACK:
        if parent_idx is None:
            parent_idx = _find_stack_bottom_idx(truck_state)
        assert parent_idx is not None, "STACK 자리에 부모가 없음"
        parent = truck_state.placements[parent_idx]
        x = parent.truck_xyz[0]
        y = parent.truck_xyz[1]
        # 새 단 z = *부모* 단 상단 + gap (트럭 전체 layer 합산 아님 — 모듈 옆에
        # 적층했을 때 모듈 천장 높이로 잘못 올라가는 버그 방지)
        # [수정 2026-05-27]
        _bL, _bW, b_h_occ, _ = _item_dims_for_posture(parent.item, parent.posture)
        z = parent.truck_xyz[2] + b_h_occ + sp.panel_gap_mm
        return (x, y, z)

    # DEP_INNER
    assert parent_idx is not None, "DEP_INNER 자리에 부모가 없음"
    parent = truck_state.placements[parent_idx]
    x = parent.truck_xyz[0]
    y = parent.truck_xyz[1]
    # 부모 floor 두께 + lshape_stack_gap 위에 종속 슬롯
    parent_th = parent.item.thickness if isinstance(parent.item, Panel) else 0.0
    z = parent_th + sp.lshape_stack_gap_mm
    return (x, y, z)


# ────────────────────────────────────────────────────────────────────
# 자리 평가 (Best-Fit) — 의사코드 § 2.4
# ────────────────────────────────────────────────────────────────────
def evaluate_slot(
    item: Item,
    posture: Posture,
    slot: PlacementSlot,
    truck_state: TruckState,
    weights: Tuple[float, float, float],
    site: SiteLimit,
    sp: SpacingParams,
) -> Tuple[float, Optional[dict]]:
    """자리 평가 — 미통과면 (-inf, None). 통과면 (점수, meta dict) 반환.

    [사용자 결정 반영]
    - 길이 분모: 잔여 적재 공간 (유효 길이 − 깔린 패널 길이 합 − gap 합 − 새 gap).
      packer_safety 식과 일관 — used_floor_length 가 이미 gap 누적값.
    - 높이 점수: 모든 자리에서 계산 (FLOOR/DEP_INNER 도 두께 손해 반영).

    Returns:
        (score, meta) — meta = {"slot", "posture", "xyz", "parent_idx"}
    """
    # ① 안전 4중 검사
    if not can_place(item, posture, slot, truck_state, site, sp).ok:
        return float("-inf"), None

    # ①-b 모듈 합산 규칙 (계획서 § 5.7 Q20) + 모듈↔패널 혼적 금지
    # 정책 (2026-05-27 사용자 결정):
    # - 모듈 + 패널 혼적 금지: 한 트럭에 모듈이 있으면 패널 못 넣고, 패널이
    #   있으면 모듈 못 넣음. 현실 운송 관행.
    # - 모듈끼리 합산 예외: 6m 이하 두 모듈만 한 트럭에 옆자리 합산 허용.
    existing_modules = [
        p for p in truck_state.placements if isinstance(p.item, Module)
    ]
    existing_panels = [
        p for p in truck_state.placements if isinstance(p.item, Panel)
    ]
    if isinstance(item, Module):
        # 새 모듈 — 기존 패널 있으면 혼적 금지
        if existing_panels:
            return float("-inf"), None
        # 이미 모듈 2 개면 무조건 거부 (3 개 이상 금지)
        if len(existing_modules) >= 2:
            return float("-inf"), None
        # 1 개 있고 새 모듈 추가 — 둘 다 6000mm 이하여야
        if existing_modules:
            if item.length > MODULE_PAIR_MAX_LEN_MM + 1e-6:
                return float("-inf"), None
            if any(p.item.length > MODULE_PAIR_MAX_LEN_MM + 1e-6 for p in existing_modules):
                return float("-inf"), None
    elif isinstance(item, Panel):
        # 새 패널 — 기존 모듈 있으면 혼적 금지
        if existing_modules:
            return float("-inf"), None

    # ② 자리별 추가 기하 제약
    parent_idx: Optional[int] = None
    if slot == PlacementSlot.STACK:
        parent_idx = _find_stack_bottom_idx(truck_state)
        if parent_idx is None:
            return float("-inf"), None
        bottom = truck_state.placements[parent_idx]
        if bottom.posture != Posture.LYING:
            return float("-inf"), None
        # 모듈 위 적층 금지
        if isinstance(bottom.item, Module):
            return float("-inf"), None
        # L자(종속 floor) 패널 위 단순 STACK 금지 — 벽 segment 위로 올라가
        # ㄷ자가 되는 물리적으로 불가능한 배치 방지. 종속 floor 위 적층은
        # *DEP_INNER 슬롯* (벽 안쪽 free area) 만 허용.
        if isinstance(bottom.item, Panel) and (
            bottom.item.wall_segments or bottom.item.kind == "lshape"
        ):
            return float("-inf"), None
        # 위 단 길이 ≤ 아래 단 길이 AND 폭 ≤ 폭 (큰 면적이 아래)
        b_len, b_w, _, _ = _item_dims_for_posture(bottom.item, bottom.posture)
        c_len, c_w, _, _ = _item_dims_for_posture(item, posture)
        if c_len > b_len + 1e-6 or c_w > b_w + 1e-6:
            return float("-inf"), None
    elif slot == PlacementSlot.DEP_INNER:
        parent_idx = _find_dep_inner_parent_idx(truck_state, item, sp)
        if parent_idx is None:
            return float("-inf"), None

    # ③ 점수 계산
    alpha, beta, gamma = weights
    length, _width, h_occ, w_kg = _item_dims_for_posture(item, posture)
    tr = truck_state.truck

    usable = tr.max_length - 2.0 * sp.truck_edge_clearance_mm

    # 길이 활용도 — packer_safety 와 일관 (used_floor_length 가 gap 누적)
    if slot == PlacementSlot.FLOOR:
        new_gap = sp.panel_gap_mm if truck_state.placements else 0.0
        remaining_len = max(1.0, usable - truck_state.used_floor_length - new_gap)
        delta_len = length / remaining_len
    else:
        # STACK / DEP_INNER 는 바닥 길이 점유 안 함 — 유효 길이 분모
        delta_len = length / max(1.0, usable)

    # 무게 활용도
    remaining_wt = max(1.0, truck_state.remaining_cargo_limit)
    delta_wt = w_kg / remaining_wt

    # 높이 활용도 — 모든 자리 (사용자 결정 — 라)
    inner_h = max(1.0, tr.max_height - tr.vehicle_height_offset)
    delta_ht = h_occ / inner_h

    score = alpha * delta_len + beta * delta_wt + gamma * delta_ht

    # ④ xyz 좌표 계산
    xyz = _compute_xyz(truck_state, slot, posture, item, sp, parent_idx)
    return score, {
        "slot": slot,
        "posture": posture,
        "xyz": xyz,
        "parent_idx": parent_idx,
    }


# ════════════════════════════════════════════════════════════════════
# Phase 4-D — 비용 인식 자리 점수 evaluate_slot_v2
# ════════════════════════════════════════════════════════════════════
# [점수 식]
#   score = α·길이활용 + β·무게활용 + γ·높이활용 + δ·비용절감보너스
#
# [δ·비용절감보너스 — 사용자 결정 반영]
#   적층(STACK)·종속슬롯(DEP_INNER) 활용은 *새 트럭 회피* 효과가 있으므로
#   FLOOR 보다 점수 우대.
#   - 더 큰 패널 위에 작은 패널 적층 → 같은 트럭에 더 많이 → 회차 감소 → 비용 ↓
#   - 종속 floor 안 슬롯 활용 → 빈 공간 재활용 → 트럭 효율 ↑
#
# [δ 값 결정 휴리스틱]
#   FLOOR: δ_bonus = 0 (기본)
#   STACK: δ_bonus = stack_bonus_factor (양수 — 0.5 ~ 2.0)
#          단 *FLOOR 가 못 들어가는 자리* 면 더 강한 보너스
#   DEP_INNER: δ_bonus = stack_bonus_factor * 1.5 (빈 슬롯 적극 활용)
#
# [기존 evaluate_slot 영향 X]
#   본 함수는 옆에 새로 추가. Phase 4-E (pack_one_seed_v2) 에서만 호출.

_DEFAULT_STACK_BONUS = 1.0
_DEFAULT_DEP_BONUS = 1.5


def evaluate_slot_v2(
    item: Item,
    posture: Posture,
    slot: PlacementSlot,
    truck_state: TruckState,
    weights: Tuple[float, float, float, float],
    site: SiteLimit,
    sp: SpacingParams,
    *,
    can_place_fn=None,
    collision_grid=None,
) -> Tuple[float, Optional[dict]]:
    """V2 자리 평가 — 4-tuple weights (α, β, γ, δ).

    [차이점]
    - weights 가 4-tuple. δ 가 비용절감 보너스 가중치.
    - collision_grid 주어지면 *xyz 결정 후* can_place_v2 호출 → 트럭 한도
      (폭 ±200, 앞뒤 100, 내공) + 부재 페어와이즈 충돌 모두 검사.
    - 외곽 차원 합산 폐기. 캔틸이 트럭 폭 밖 돌출 등을 부재 박스 외곽 AABB
      로 정확히 차단.
    - STACK / DEP_INNER 에 δ·bonus 항 추가.

    [반환] evaluate_slot 과 동일 — (score, meta) 또는 (-inf, None).
    """
    # ① V1 기본 안전 검사 (무게/내공 등 빠른 거부)
    if can_place_fn is None:
        ok = can_place(item, posture, slot, truck_state, site, sp).ok
    else:
        ok = can_place_fn(item, posture, slot, truck_state, site, sp).ok
    if not ok:
        return float("-inf"), None

    # ①-b 모듈 합산 규칙 + 모듈↔패널 혼적 금지 (사용자 결정 2026-05-27)
    existing_modules = [
        p for p in truck_state.placements if isinstance(p.item, Module)
    ]
    existing_panels = [
        p for p in truck_state.placements if isinstance(p.item, Panel)
    ]
    if isinstance(item, Module):
        if existing_panels:
            return float("-inf"), None
        if len(existing_modules) >= 2:
            return float("-inf"), None
        if existing_modules:
            if item.length > MODULE_PAIR_MAX_LEN_MM + 1e-6:
                return float("-inf"), None
            if any(p.item.length > MODULE_PAIR_MAX_LEN_MM + 1e-6 for p in existing_modules):
                return float("-inf"), None
    elif isinstance(item, Panel):
        if existing_modules:
            return float("-inf"), None

    # ② 자리별 추가 기하 제약 (기존과 동일 + Module 위 적층 이중 안전망)
    parent_idx: Optional[int] = None
    if slot == PlacementSlot.STACK:
        parent_idx = _find_stack_bottom_idx(truck_state)
        if parent_idx is None:
            return float("-inf"), None
        bottom = truck_state.placements[parent_idx]
        if bottom.posture != Posture.LYING:
            return float("-inf"), None
        # 모듈 위 적층 금지 (이중 안전망)
        if isinstance(bottom.item, Module):
            return float("-inf"), None
        # L자(종속 floor) 위 단순 STACK 금지 — 벽 위로 올라가는 ㄷ자 형태 방지.
        # 종속 floor 위 적층은 DEP_INNER 슬롯 (벽 안쪽 free area) 만 허용.
        if isinstance(bottom.item, Panel) and (
            bottom.item.wall_segments or bottom.item.kind == "lshape"
        ):
            return float("-inf"), None
        # 적층 기하 — 사용자 결정: 큰 면적이 아래
        b_len, b_w, _, _ = _item_dims_for_posture(bottom.item, bottom.posture)
        c_len, c_w, _, _ = _item_dims_for_posture(item, posture)
        # 위 ≤ 아래 검사 (위가 더 작아야 함)
        if c_len > b_len + 1e-6 or c_w > b_w + 1e-6:
            return float("-inf"), None
    elif slot == PlacementSlot.DEP_INNER:
        parent_idx = _find_dep_inner_parent_idx(truck_state, item, sp)
        if parent_idx is None:
            return float("-inf"), None

    # ③ 점수 계산 (α·길이 + β·무게 + γ·높이 + δ·비용보너스)
    alpha, beta, gamma, delta = weights
    length, _width, h_occ, w_kg = _item_dims_for_posture(item, posture)
    tr = truck_state.truck
    usable = tr.max_length - 2.0 * sp.truck_edge_clearance_mm

    if slot == PlacementSlot.FLOOR:
        new_gap = sp.panel_gap_mm if truck_state.placements else 0.0
        remaining_len = max(1.0, usable - truck_state.used_floor_length - new_gap)
        delta_len = length / remaining_len
    else:
        delta_len = length / max(1.0, usable)

    remaining_wt = max(1.0, truck_state.remaining_cargo_limit)
    delta_wt = w_kg / remaining_wt

    inner_h = max(1.0, tr.max_height - tr.vehicle_height_offset)
    delta_ht = h_occ / inner_h

    # 비용절감 보너스
    if slot == PlacementSlot.STACK:
        # FLOOR 가 *어차피 못 들어가는 경우* 더 큰 보너스
        new_gap = sp.panel_gap_mm if truck_state.placements else 0.0
        floor_remaining = usable - truck_state.used_floor_length - new_gap
        if floor_remaining < length - 1e-3:
            # FLOOR 안 들어감 → STACK == 새 트럭 1대 회피
            delta_cost = _DEFAULT_STACK_BONUS * 2.0
        else:
            # FLOOR 도 가능. STACK 도 약간 우대 (적층 친화 시드 다양성)
            delta_cost = _DEFAULT_STACK_BONUS
    elif slot == PlacementSlot.DEP_INNER:
        delta_cost = _DEFAULT_DEP_BONUS
    else:
        delta_cost = 0.0

    score = (alpha * delta_len + beta * delta_wt + gamma * delta_ht
             + delta * delta_cost)

    # ④ xyz 좌표 계산 (기존과 동일)
    xyz = _compute_xyz(truck_state, slot, posture, item, sp, parent_idx)

    # ⑤ V2 트럭 한도 + 부재 페어와이즈 충돌 검사 (xyz 결정 후)
    # 외곽 차원 합산 폐기 → 캔틸 돌출이 트럭 한도 밖으로 나가는 경우 여기서 거부.
    from .packer_safety import can_place_v2
    v2_chk = can_place_v2(
        item, posture, xyz, truck_state, site, sp,
        collision_grid=collision_grid,
    )
    if not v2_chk.ok:
        return float("-inf"), None

    return score, {
        "slot": slot,
        "posture": posture,
        "xyz": xyz,
        "parent_idx": parent_idx,
    }


# V2 용 가중치 세트 — 4-tuple (α, β, γ, δ)
# 2026-05-27 축소: 중복 효과 제거 (lookahead 도입으로 가중치 다양성 영향 작아짐).
# 4 종 — 균형 / 무게 / 적층(높이) / 비용 보너스.
WEIGHT_SETS_V2: List[Tuple[float, float, float, float]] = [
    (1.0, 1.0, 1.0, 1.0),    # 균형 (안전망)
    (1.0, 2.0, 1.0, 1.0),    # 무게 강조 (무거운 화물 우선 안착)
    (0.5, 0.5, 2.0, 1.0),    # 적층 강조 (높이 단독)
    (1.0, 1.0, 1.0, 2.5),    # 비용 보너스 강조 (적층/혼합 매우 우대)
]


# ────────────────────────────────────────────────────────────────────
# Placement commit — TruckState 자원 누적
# ────────────────────────────────────────────────────────────────────
def _commit_placement(
    truck_state: TruckState,
    item: Item,
    slot: PlacementSlot,
    posture: Posture,
    meta: dict,
    sp: SpacingParams,
) -> None:
    """TruckState 에 Placement 추가 + 누적 자원 갱신.

    used_floor_length 는 gap 포함 누적값 (packer_safety 와 일관).
    """
    n_before = len(truck_state.placements)
    length, _width, h_occ, weight = _item_dims_for_posture(item, posture)

    truck_state.placements.append(Placement(
        item=item,
        slot=slot,
        posture=posture,
        truck_xyz=meta["xyz"],
        parent_idx=meta.get("parent_idx"),
    ))
    truck_state.used_cargo_weight += weight

    if slot == PlacementSlot.FLOOR:
        gap = sp.panel_gap_mm if n_before > 0 else 0.0
        truck_state.used_floor_length += gap + length
        # LYING FLOOR 만 첫 단 max 두께 갱신 (STANDING 은 layer 개념 X)
        if posture == Posture.LYING:
            if not truck_state.layer_max_thickness:
                truck_state.layer_max_thickness.append(h_occ)
            else:
                truck_state.layer_max_thickness[0] = max(
                    truck_state.layer_max_thickness[0], h_occ
                )
    elif slot == PlacementSlot.STACK:
        # 새 단 추가
        truck_state.layer_max_thickness.append(h_occ)
    # DEP_INNER: 부모 단 위 슬롯 — 단별 갱신 불요 (부모가 단을 차지)


# ────────────────────────────────────────────────────────────────────
# 트럭 선정 — mini-시뮬레이션 (의사코드 § 2.11)
# ────────────────────────────────────────────────────────────────────
def _simulate_packing_weight(
    truck: Truck, items: Sequence[Item], site: SiteLimit, sp: SpacingParams,
) -> float:
    """후보 트럭 1 대에 items 를 무게 내림차순 simple-FFD 로 적재해 실리는 무게 합.

    *비파괴* — items 변경 없음. 적층은 무시(첫 단만 — 간이 추정).
    A-frame 트럭은 단순 wall STANDING 만 받음.
    """
    sorted_items = sorted(items, key=lambda x: -x.weight)

    eff_limit = _effective_cargo_limit(truck, site)
    if eff_limit <= 0:
        return 0.0
    usable_len = truck.max_length - 2.0 * sp.truck_edge_clearance_mm
    eff_w = truck.max_width + 2.0 * sp.side_overhang_mm
    inner_h = truck.max_height - truck.vehicle_height_offset

    used_len = 0.0
    used_wt = 0.0
    placed_count = 0

    for item in sorted_items:
        # 자세 결정 — 트럭 호환성에 따라
        if truck.truck_type == "aframe":
            if not _is_simple_wall(item):
                continue
            posture = Posture.STANDING
        else:
            # lowbed / extendable: 모든 화물 LYING (단순 wall 도 LYING 으로 간이 추정)
            posture = Posture.LYING

        length, width, h_occ, w_kg = _item_dims_for_posture(item, posture)

        gap = sp.panel_gap_mm if placed_count > 0 else 0.0
        if used_len + gap + length > usable_len + 1e-6:
            continue
        if width > eff_w + 1e-6:
            continue
        if h_occ > inner_h + 1e-6:
            continue
        if used_wt + w_kg > eff_limit + 1e-6:
            continue

        used_len += gap + length
        used_wt += w_kg
        placed_count += 1

    return used_wt


def default_truck_score(
    truck: Truck,
    remaining_items: Sequence[Item],
    site: SiteLimit,
    sp: SpacingParams,
    cost_mode: str,
    eco_options: EcoOptions,
) -> float:
    """*그 트럭 1회 비용*. 낮을수록 우선.

    [근거 — 2026-05-27 휴리스틱 폐기]
    종전엔 _simulate_packing_weight 같은 *희망적 추정* 으로 점수를 만들었으나
    *그 회차에 실제 실릴 양* 과 동떨어져 큰 트럭이 부당 우대됐다. 이제
    *그 트럭 자체의 1회 비용* 만 본다. 가장 싼 트럭이 항상 첫 우선.
    *다양성* (큰 트럭이 더 효율적인 케이스 등) 은 트럭별 우선 시드로 확보.
    """
    if cost_mode == "fixed_per_trip":
        return eco_options.fixed_per_trip_rate
    if cost_mode == "freight_table":
        return eco_options.fixed_rate_for_truck_type(truck.truck_type)
    if cost_mode == "per_km":
        return eco_options.km_rate_for_truck(truck) * eco_options.round_trip_km
    return 1.0


def make_truck_pref_score(preferred_name: str):
    """*특정 트럭 우선* 점수 함수 팩토리.

    [시드 다양성 — 2026-05-27]
    각 트럭별로 *그 트럭이 모든 회차에서 우선 선택* 되는 시드를 만들기 위해
    사용. preferred_name 인 트럭은 score=0, 나머지는 *그 트럭의 1회 비용* 으로
    fallback (호환 안 되는 등 preferred 가 못 들어갈 때).
    """
    def _score(truck, remaining_items, site, sp, cost_mode, eco_options):
        if truck.name == preferred_name:
            return 0.0
        # fallback — 단순 1회 비용
        return default_truck_score(truck, remaining_items, site, sp, cost_mode, eco_options) + 1.0
    return _score


def cost_efficiency_truck_score(
    truck: Truck,
    remaining_items: Sequence[Item],
    site: SiteLimit,
    sp: SpacingParams,
    cost_mode: str,
    eco_options: EcoOptions,
) -> float:
    """트럭 자체 효율 — 1회 고정비 / 트럭 실효 적재능력 (원/kg).

    remaining_items 는 무시 (트럭 자체 능력만 본다 — 정렬 전략의 변종).
    """
    if cost_mode == "fixed_per_trip":
        fixed = eco_options.fixed_per_trip_rate
    elif cost_mode == "freight_table":
        fixed = eco_options.fixed_rate_for_truck_type(truck.truck_type)
    elif cost_mode == "per_km":
        fixed = eco_options.km_rate_for_truck(truck) * eco_options.round_trip_km
    else:
        fixed = 1.0
    eff = _effective_cargo_limit(truck, site)
    if eff <= 0:
        return float("inf")
    return fixed / eff


# ────────────────────────────────────────────────────────────────────
# 그리디 전략 4 종 + 가중치 6 조합 — 의사코드 § 2.3
# ────────────────────────────────────────────────────────────────────
TruckScoreFn = Callable[
    [Truck, Sequence[Item], SiteLimit, SpacingParams, str, EcoOptions],
    float,
]


@dataclass(frozen=True)
class GreedyStrategy:
    """그리디 전략 — 정렬 + 트럭선정 정책의 결합."""
    name: str
    sort_items_fn: Callable[[Sequence[Item]], List[Item]]
    truck_score_fn: TruckScoreFn


def _dependent_first_sorter(items: Sequence[Item]) -> List[Item]:
    """종속 패널 묶음을 정렬 키 앞에, 그 안에서 무게 내림차순.

    같은 분류 안에서는 무게 내림차순으로 동일 (그리디 우선순위 일치).
    """
    def key(it: Item) -> Tuple[int, float]:
        is_dep = _is_dependent_floor(it)
        return (0 if is_dep else 1, -getattr(it, "weight", 0.0))
    return sorted(items, key=key)


# 2026-05-27 축소: cost_efficiency 시드 제거 (weight_desc 와 정렬 동일,
# truck_score_fn 도 lookahead 도입 후 의미 X). 3 정렬 × 4 가중치 = 12 시드.
GREEDY_STRATEGIES: List[GreedyStrategy] = [
    GreedyStrategy(
        name="weight_desc",
        sort_items_fn=lambda items: sorted(items, key=lambda x: -x.weight),
        truck_score_fn=default_truck_score,
    ),
    GreedyStrategy(
        name="volume_desc",
        sort_items_fn=lambda items: sorted(items, key=lambda x: -_volume_of(x)),
        truck_score_fn=default_truck_score,
    ),
    GreedyStrategy(
        name="dependent_first",
        sort_items_fn=_dependent_first_sorter,
        truck_score_fn=default_truck_score,
    ),
]


WEIGHT_SETS: List[Tuple[float, float, float]] = [
    (1.0, 1.0, 1.0),  # 균형
    (2.0, 1.0, 1.0),  # 길이 강조
    (1.0, 2.0, 1.0),  # 무게 강조
    (1.0, 1.0, 2.0),  # 높이 강조
    (1.5, 1.5, 0.5),  # 길이·무게 동시 강조
    (0.5, 0.5, 2.0),  # 높이 단독 강조
]


# 무작위 시드 전용 — 입력 순서 그대로 (셔플 무효화 방지)
SHUFFLED_STRATEGY = GreedyStrategy(
    name="shuffled",
    sort_items_fn=lambda items: list(items),  # identity — 셔플 순서 유지
    truck_score_fn=default_truck_score,
)


# ────────────────────────────────────────────────────────────────────
# 새 트럭 선정 — 빈 트럭 한 대 만들어 점수 비교
# ────────────────────────────────────────────────────────────────────
_DEBUG_TRUCK_SELECTION: bool = False   # 트럭 선정 진단 로그 (필요 시 True)


# ── lookahead 재귀 방지용 thread-local ─────────────────────
import threading as _threading
_lookahead_state = _threading.local()


def _is_in_lookahead() -> bool:
    return getattr(_lookahead_state, "active", False)


# ── 모듈 트럭 선택 캐시 (메모이제이션) ──────────────────────
# 동일 사양 모듈은 호환 트럭 + 단가 정책이 같으면 항상 같은 트럭이 선택됨
# → 결과 캐시해서 16개 모듈 결정을 16번 계산하지 않고 1번만.
_module_truck_cache: Dict[Tuple, str] = {}


def _module_cache_key(item: "Module", trucks_sig: tuple, cost_mode: str) -> Tuple:
    """모듈 사양 + 트럭 세트 시그너처 + 비용 모드 = 캐시 키."""
    return (
        int(round(item.length)),
        int(round(item.width)),
        int(round(item.height)),
        int(round(item.weight)),
        trucks_sig,
        cost_mode,
    )


def clear_module_truck_cache() -> None:
    """패킹 시작 시 호출 — 시드 간 캐시 잔존 방지."""
    _module_truck_cache.clear()


def _filter_compat_trucks(
    item: Item, all_trucks: Sequence[Truck],
    site: SiteLimit, sp: SpacingParams,
) -> Tuple[List[Truck], List[Tuple[Truck, str]]]:
    """호환 트럭 후보 + 거부 사유 분리."""
    candidates: List[Truck] = []
    rejected: List[Tuple[Truck, str]] = []
    for tr in all_trucks:
        if not tr.active:
            rejected.append((tr, "active=False"))
            continue
        ts = TruckState(
            truck=tr, effective_cargo_limit=_effective_cargo_limit(tr, site),
        )
        ok = False
        first_reason = ""
        for posture in _postures_for(item):
            if not _truck_posture_compatible(tr, posture, item):
                if not first_reason:
                    first_reason = f"자세 {posture.value} 비호환"
                continue
            chk = can_place(item, posture, PlacementSlot.FLOOR, ts, site, sp)
            if chk.ok:
                ok = True
                break
            elif not first_reason and chk.reasons:
                first_reason = chk.reasons[0]
        if ok:
            candidates.append(tr)
        else:
            rejected.append((tr, first_reason or "can_place 실패"))
    return candidates, rejected


def _select_best_new_truck(
    item: Item,
    all_trucks: Sequence[Truck],
    remaining: Sequence[Item],
    strategy: GreedyStrategy,
    site: SiteLimit,
    sp: SpacingParams,
    cost_mode: str,
    eco_options: EcoOptions,
) -> Optional[Truck]:
    """새 트럭 선정 — *진짜 lookahead* 로 결과 기반 결정.

    [핵심 — 2026-05-27 휴리스틱 폐기 후 lookahead 도입]
    각 호환 트럭 후보 cand 에 대해:
      1. cand 를 *현 화물 (item) 의 새 트럭* 으로 강제 선택
      2. 나머지 화물은 *단순 가장 싼 트럭* 휴리스틱으로 그리디 시뮬 끝까지
      3. *진짜 총 비용* (compute_cost) 측정
    가장 낮은 총 비용을 만든 cand 채택.

    [재귀 방지]
    시뮬 내부에서 또 _select_best_new_truck 이 호출되면 _is_in_lookahead()=True
    → *단순 가장 싼 트럭* 즉 lookahead 깊이 1 만.

    [효과]
    *각 회차마다 *서로 다른* 트럭이 최적인 경우* (예: 회차1=광폭 가득,
     회차2=저상 1개) 자동 발견. 트럭 1종 우선 시드의 한계 극복.

    호환 트럭 = 자세 ∈ _postures_for(item) 중 하나로 빈 트럭 FLOOR 진입 가능.
    """
    candidates, rejected = _filter_compat_trucks(item, all_trucks, site, sp)

    if _DEBUG_TRUCK_SELECTION:
        try:
            import sys
            item_name = getattr(item, "name", "?")
            sys.stderr.write(
                f"[truck_select] item={item_name} 후보 {len(candidates)}대 / "
                f"거부 {len(rejected)}대 / lookahead={'OFF' if _is_in_lookahead() else 'ON'}\n"
            )
            for tr, reason in rejected:
                sys.stderr.write(f"  ✗ {tr.name}: {reason}\n")
        except Exception:
            pass

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # ── 모듈 빠른 경로 — 단독 회차 + 캐시 메모이제이션 ────────
    # 정책 (사용자 결정 2026-05-27): 모듈+패널 혼적 금지 → 모듈 회차 단독.
    # 동일 사양 모듈은 항상 같은 트럭 → 캐시. 16개 모듈도 캐시 미스 1번만 계산.
    if isinstance(item, Module):
        trucks_sig = tuple(
            (tr.name, tr.active, tr.truck_type) for tr in all_trucks
        )
        key = _module_cache_key(item, trucks_sig, cost_mode)
        cached_name = _module_truck_cache.get(key)
        if cached_name is not None:
            for tr in candidates:
                if tr.name == cached_name:
                    return tr
            # 캐시 트럭이 *현 후보에 없으면* (드물게 active 변경 등) 재계산
        scored = [
            (default_truck_score(tr, [item], site, sp, cost_mode, eco_options), tr)
            for tr in candidates
        ]
        scored.sort(key=lambda x: x[0])
        chosen = scored[0][1]
        _module_truck_cache[key] = chosen.name
        return chosen

    # 재귀 (시뮬 안) — *첫 결정만* pref_once cand 강제 + 이후 가장 싼 트럭
    if _is_in_lookahead():
        pref = getattr(_lookahead_state, "pref_once", None)
        if pref is not None:
            # 한 번만 사용 후 리셋 — 이후 결정은 fallback
            _lookahead_state.pref_once = None
            if pref in candidates:
                return pref
        # pref 없거나 호환 X → 단순 가장 싼 트럭
        scored = [
            (default_truck_score(tr, remaining, site, sp, cost_mode, eco_options), tr)
            for tr in candidates
        ]
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    # 외부 — 진짜 lookahead
    _lookahead_state.active = True
    best_truck: Optional[Truck] = None
    best_cost = float("inf")
    cand_results: List[Tuple[Truck, float]] = []
    try:
        from .packer_meta import compute_cost
        # *순서 그대로* 유지 — item 이 시뮬의 첫 화물이어야 pref_once 가 정확히
        # 그 화물의 결정에 cand 강제 가능. strategy.sort_items_fn 으로 재정렬
        # 하면 첫 화물이 바뀌어 cand 가 다른 화물에 강제됨 (cand 호환 X → fallback).
        rem_items = [item] + list(remaining[1:])
        # 시뮬용 strategy — sort 폐기, truck_score_fn 만 그대로
        sim_strategy = GreedyStrategy(
            name=f"_la_sim_{strategy.name}",
            sort_items_fn=lambda items: list(items),  # identity — 순서 그대로
            truck_score_fn=strategy.truck_score_fn,
        )
        for cand in candidates:
            try:
                # *시뮬 첫 결정만* cand 강제. 이후 결정은 default(가장 싼 트럭).
                # 혼합 결과 (광폭 1 + 저상 N) 자연 발견.
                _lookahead_state.pref_once = cand
                sim_pack = _quick_simulate_pack(
                    rem_items, list(all_trucks), site, sp,
                    sim_strategy, cost_mode, eco_options,
                )
                sim_cost = compute_cost(sim_pack, cost_mode, eco_options)
            except Exception as e:
                if _DEBUG_TRUCK_SELECTION:
                    try:
                        import sys
                        sys.stderr.write(f"  ⚠ {cand.name} 시뮬 예외: {e}\n")
                    except Exception:
                        pass
                continue
            finally:
                _lookahead_state.pref_once = None
            cand_results.append((cand, sim_cost))
            if sim_cost < best_cost:
                best_cost = sim_cost
                best_truck = cand
    finally:
        _lookahead_state.active = False
        _lookahead_state.pref_once = None

    if _DEBUG_TRUCK_SELECTION:
        try:
            import sys
            for cand, sc in sorted(cand_results, key=lambda x: x[1]):
                marker = "★" if cand is best_truck else "▷"
                sys.stderr.write(
                    f"  {marker} {cand.name} 시뮬 총비용={sc:.0f}원\n"
                )
        except Exception:
            pass

    # lookahead 실패 시 fallback — 가장 싼 트럭
    if best_truck is None:
        scored = [
            (default_truck_score(tr, remaining, site, sp, cost_mode, eco_options), tr)
            for tr in candidates
        ]
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    return best_truck


def _quick_simulate_pack(
    items: Sequence[Item], trucks: List[Truck], site: SiteLimit,
    sp: SpacingParams, strategy: GreedyStrategy,
    cost_mode: str, eco_options,
) -> "PackResult":
    """lookahead 용 *빠른* 그리디 패킹 (V1 단순). 내부 select 는 재귀 fallback."""
    # 지연 import — pack_one_seed 가 본 모듈에 정의되어 있어 직접 호출 가능
    return pack_one_seed(
        items, trucks, site, sp, strategy,
        WEIGHT_SETS[0],  # 기본 균형 가중치 (3-tuple)
        cost_mode, eco_options,
    )


# ────────────────────────────────────────────────────────────────────
# 모듈 합산 후처리 — 6m 두 모듈 페어링 (계획서 § 5.7 Q20)
# ────────────────────────────────────────────────────────────────────
def _merge_small_modules(
    truck_states: List[TruckState], site: SiteLimit, sp: SpacingParams,
) -> List[TruckState]:
    """모듈만 든 회차 중 *모듈 1 개 + 길이 ≤ 6000mm* 인 회차들을 2 개씩 합쳐 회차 절감.

    합칠 트럭 후보 = 두 회차의 트럭 중 둘 다 들어가는 것 (또는 그 중 하나 — 보수 정책).
    안전 4중 검사 + 모듈 합산 검사 모두 통과해야 채택.
    """
    small_states: List[Tuple[int, TruckState, Module]] = []
    other_states: List[TruckState] = []

    for i, ts in enumerate(truck_states):
        if (
            len(ts.placements) == 1
            and isinstance(ts.placements[0].item, Module)
            and ts.placements[0].item.length <= MODULE_PAIR_MAX_LEN_MM + 1e-6
        ):
            small_states.append((i, ts, ts.placements[0].item))
        else:
            other_states.append(ts)

    merged: List[TruckState] = list(other_states)
    used = [False] * len(small_states)

    for a in range(len(small_states)):
        if used[a]:
            continue
        _ia, ts_a, m_a = small_states[a]
        paired = False
        for b in range(a + 1, len(small_states)):
            if used[b]:
                continue
            _ib, ts_b, m_b = small_states[b]
            # 두 모듈 모두 한 트럭(ts_a 또는 ts_b)에 들어가는지 시도
            for target_truck in [ts_a.truck, ts_b.truck]:
                ts_test = TruckState(
                    truck=target_truck,
                    effective_cargo_limit=_effective_cargo_limit(target_truck, site),
                )
                meta_a = evaluate_slot(
                    m_a, Posture.LYING, PlacementSlot.FLOOR, ts_test,
                    (1.0, 1.0, 1.0), site, sp,
                )
                if meta_a[0] == float("-inf"):
                    continue
                _commit_placement(ts_test, m_a, PlacementSlot.FLOOR, Posture.LYING,
                                  meta_a[1], sp)
                meta_b = evaluate_slot(
                    m_b, Posture.LYING, PlacementSlot.FLOOR, ts_test,
                    (1.0, 1.0, 1.0), site, sp,
                )
                if meta_b[0] == float("-inf"):
                    continue
                _commit_placement(ts_test, m_b, PlacementSlot.FLOOR, Posture.LYING,
                                  meta_b[1], sp)
                merged.append(ts_test)
                used[a] = used[b] = True
                paired = True
                break
            if paired:
                break
        if not paired:
            merged.append(ts_a)
            used[a] = True

    return merged


# ────────────────────────────────────────────────────────────────────
# TruckState → 기존 Trip 으로 변환 (역호환)
# ────────────────────────────────────────────────────────────────────
def _truck_state_to_trip(
    ts: TruckState, trip_no: int, sp: SpacingParams,
) -> Trip:
    """TruckState 의 placements 를 기존 Trip 데이터 구조로 변환.

    역호환 약속:
    - items: FLOOR + STACK 의 모든 item (모듈·단순·종속 모두 — DEP_INNER 만 제외).
             *STACK 화물 도 items 에 풀어넣음* — 단순 floor·wall 의 다단 적층은
             기존 FFD 처럼 panels_per_row × n_layers 정보로 표현.
    - stacked_items: FLOOR placement 와 1:1 매칭 — *그 위 DEP_INNER 자식*만.
             STACK 자식은 items 에 풀어넣었으므로 여기엔 안 들어감.
             stacked_items.length == 회차 안 FLOOR 화물 수.
    - panels_per_row: LYING FLOOR 화물 개수 (계획서 § 12.1)
    - n_layers: layer_max_thickness 길이 (STACK 단 포함)
    - used_length_mm: ts.used_floor_length (gap 포함 누적값 — 의도된 일관성)
    """
    tr = ts.truck
    usable = tr.max_length - 2.0 * sp.truck_edge_clearance_mm

    floor_placements = [
        (i, p) for i, p in enumerate(ts.placements) if p.slot == PlacementSlot.FLOOR
    ]
    stack_placements = [
        p for p in ts.placements if p.slot == PlacementSlot.STACK
    ]
    dep_placements = [
        p for p in ts.placements if p.slot == PlacementSlot.DEP_INNER
    ]

    items: List[Item] = []
    stacked_items: List = []
    # FLOOR 들을 먼저 items 에 + 매칭 DEP_INNER 자식을 stacked_items 에
    for floor_idx, p in floor_placements:
        items.append(p.item)
        dep_child = next(
            (q.item for q in dep_placements if q.parent_idx == floor_idx), None,
        )
        stacked_items.append(dep_child)
    # STACK 화물도 items 에 풀어넣음 (다단 적층 시 3 매째 이상 누락 방지)
    for q in stack_placements:
        items.append(q.item)

    seen_lying = any(p.posture == Posture.LYING for p in ts.placements)
    seen_standing = any(p.posture == Posture.STANDING for p in ts.placements)
    standing_count = sum(1 for p in ts.placements if p.posture == Posture.STANDING)
    has_mixed_posture = seen_lying and seen_standing

    panels_per_row = max(
        1, sum(1 for _, p in floor_placements if p.posture == Posture.LYING)
    )
    n_layers = max(1, len(ts.layer_max_thickness))

    return Trip(
        trip_no=trip_no,
        truck=tr,
        items=items,
        stacked_items=stacked_items,
        panels_per_row=panels_per_row,
        n_layers=n_layers,
        used_length_mm=ts.used_floor_length,
        usable_length_mm=usable,
        placements=list(ts.placements),
        has_mixed_posture=has_mixed_posture,
        standing_count=standing_count,
    )


# ────────────────────────────────────────────────────────────────────
# 단일 패킹 진입점 — 의사코드 § 2.5
# ────────────────────────────────────────────────────────────────────
def pack_one_seed(
    items: Sequence[Item],
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    strategy: GreedyStrategy,
    weights: Tuple[float, float, float],
    cost_mode: str = "fixed_per_trip",
    eco_options: Optional[EcoOptions] = None,
) -> PackResult:
    """한 그리디 전략 + 한 가중치 조합으로 1 회 패킹 → 1 후보 해 반환.

    Best-Fit 자유 배치 — 모든 트럭 × 자세 × 자리 후보 중 점수 최대 채택.
    어디에도 못 들어가면 새 트럭 추가 (mini-시뮬레이션으로 선정).

    Args:
        items: 패킹할 모듈·패널 리스트 (정렬 X — strategy 가 정렬)
        trucks: 사용 가능 트럭 목록 (active=False 는 자동 제외)
        site: 현장 운송 제한
        sp: 간격 파라미터
        strategy: GREEDY_STRATEGIES 중 하나 또는 SHUFFLED_STRATEGY
        weights: (α, β, γ) — WEIGHT_SETS 중 하나
        cost_mode: "fixed_per_trip" / "freight_table" / "per_km"
        eco_options: 비용 단가 (None → default_eco_options())

    Returns:
        PackResult — trips + blocked (이유는 _diagnose_blocked)
    """
    if eco_options is None:
        eco_options = default_eco_options()

    sorted_items = list(strategy.sort_items_fn(items))
    truck_states: List[TruckState] = []
    blocked: list = []

    for idx, item in enumerate(sorted_items):
        postures = _postures_for(item)

        best_score = float("-inf")
        best_choice: Optional[Tuple[TruckState, PlacementSlot, Posture, dict]] = None

        # ① 모든 기존 트럭 × 자세 × 자리 후보 탐색 (Best Fit)
        for ts in truck_states:
            for posture in postures:
                if not _truck_posture_compatible(ts.truck, posture, item):
                    continue
                for slot in _allowed_slots_for(item, posture):
                    score, meta = evaluate_slot(
                        item, posture, slot, ts, weights, site, sp,
                    )
                    if meta is not None and score > best_score:
                        best_score = score
                        best_choice = (ts, slot, posture, meta)

        if best_choice is not None:
            ts, slot, posture, meta = best_choice
            _commit_placement(ts, item, slot, posture, meta, sp)
            continue

        # ② 기존 트럭 어디에도 못 들어감 → 새 트럭 선정
        remaining = sorted_items[idx:]
        new_truck = _select_best_new_truck(
            item, trucks, remaining, strategy, site, sp, cost_mode, eco_options,
        )
        if new_truck is None:
            blocked.append((item, _diagnose_blocked(item, list(trucks), site)))
            continue

        ts_new = TruckState(
            truck=new_truck,
            effective_cargo_limit=_effective_cargo_limit(new_truck, site),
        )
        truck_states.append(ts_new)

        # 새 트럭 위에 이 item 의 모든 자세 × FLOOR 자리 다시 평가
        best_new_score = float("-inf")
        best_new: Optional[Tuple[Posture, dict]] = None
        for posture in postures:
            if not _truck_posture_compatible(new_truck, posture, item):
                continue
            score, meta = evaluate_slot(
                item, posture, PlacementSlot.FLOOR, ts_new, weights, site, sp,
            )
            if meta is not None and score > best_new_score:
                best_new_score = score
                best_new = (posture, meta)
        if best_new is None:
            blocked.append((item, "새 트럭 추가했으나 자세 매칭 실패"))
            continue
        _commit_placement(
            ts_new, item, PlacementSlot.FLOOR, best_new[0], best_new[1], sp,
        )

    # ③ 모듈 합산 6m 후처리
    truck_states = _merge_small_modules(truck_states, site, sp)

    # ④ PackResult 조립
    trips = [
        _truck_state_to_trip(ts, trip_no=i + 1, sp=sp)
        for i, ts in enumerate(truck_states)
    ]
    return PackResult(trips=trips, blocked=blocked)


# ════════════════════════════════════════════════════════════════════
# Phase 4-E — V2 그리디 패커 (비용 인식 + 그리드 충돌 검사)
# ════════════════════════════════════════════════════════════════════
def _sync_grid_for_trucks(
    truck_states: List[TruckState], sp: SpacingParams,
):
    """각 트럭 상태에 대한 CollisionGrid 동기화 (commit 직후 호출).

    placement 의 truck_xyz_center → 컴포넌트 좌하단 → boxes_of_component →
    CollisionGrid 에 등록. owner_id = (trip_idx, placement_idx).
    """
    from .collision import CollisionGrid
    from .packer_safety import (
        boxes_of_component, _nominal_dims_for_posture,
        _trip_xyz_from_truck_center,
    )
    grids: List["CollisionGrid"] = []
    for ts in truck_states:
        g = CollisionGrid()
        for p_idx, p in enumerate(ts.placements):
            L_nom, W_nom, _ = _nominal_dims_for_posture(p.item, p.posture)
            x0, y0, z0 = _trip_xyz_from_truck_center(
                p.truck_xyz, L_nom, W_nom, ts.truck,
            )
            boxes = boxes_of_component(p.item, p.posture, (x0, y0, z0))
            g.insert(boxes, owner_id=p_idx)
        grids.append(g)
    return grids


def _verify_v2_collision(
    item: Item, posture: Posture, meta: dict,
    ts: TruckState, grid, sp: SpacingParams,
) -> bool:
    """commit 직전 V2 페어와이즈 충돌 검사.

    meta["xyz"] 의 좌표로 컴포넌트를 놓았을 때 grid 안 기존 부재와 갭 100mm 이상
    유지되는가 확인. True 면 commit 가능.
    """
    from .collision import boxes_gap_ok
    from .packer_safety import (
        boxes_of_component, _nominal_dims_for_posture,
        _trip_xyz_from_truck_center, GAP_MM,
    )
    L_nom, W_nom, _ = _nominal_dims_for_posture(item, posture)
    xyz_center = meta["xyz"]
    x0, y0, z0 = _trip_xyz_from_truck_center(xyz_center, L_nom, W_nom, ts.truck)
    new_boxes = boxes_of_component(item, posture, (x0, y0, z0))
    if not new_boxes:
        return True
    # 외곽 + GAP_MM margin 으로 근접 owner 조회
    xs0 = [b[0] for b in new_boxes]; ys0 = [b[1] for b in new_boxes]; zs0 = [b[2] for b in new_boxes]
    xs1 = [b[3] for b in new_boxes]; ys1 = [b[4] for b in new_boxes]; zs1 = [b[5] for b in new_boxes]
    outer = (min(xs0), min(ys0), min(zs0), max(xs1), max(ys1), max(zs1))
    near_owners = grid.query_near(outer, margin_mm=GAP_MM)
    for oid in near_owners:
        other_boxes = grid.boxes_of(oid)
        for nb in new_boxes:
            for ob in other_boxes:
                if not boxes_gap_ok(nb, ob, GAP_MM):
                    return False
    return True


def pack_one_seed_v2(
    items: Sequence[Item],
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    strategy: GreedyStrategy,
    weights: Tuple[float, float, float, float],
    cost_mode: str = "fixed_per_trip",
    eco_options: Optional[EcoOptions] = None,
) -> PackResult:
    """V2 그리디 패커 — 비용 인식 자리 점수 + 부재 페어와이즈 충돌 검사.

    [V1 과의 차이]
    - weights 4-tuple (α, β, γ, δ) — δ 가 비용 보너스
    - evaluate_slot_v2 사용
    - 각 트럭별 CollisionGrid 유지. commit 직전 페어와이즈 충돌 검사
    - 충돌 발견 시 다음 후보 자리 시도 (자리 후보 상위 3개 보관)

    Args/Returns: pack_one_seed 와 동일 형식.
    """
    from .collision import CollisionGrid

    if eco_options is None:
        eco_options = default_eco_options()

    sorted_items = list(strategy.sort_items_fn(items))
    truck_states: List[TruckState] = []
    grids: List[CollisionGrid] = []
    blocked: list = []

    BEAM_K = 3  # 자리 후보 상위 K 개 보관 (충돌 시 fallback)

    for idx, item in enumerate(sorted_items):
        postures = _postures_for(item)

        # ① 모든 기존 트럭 × 자세 × 자리 후보 평가 → 상위 K 보관
        # evaluate_slot_v2 에 해당 트럭의 collision_grid 전달 → 캔틸 등 부재가
        # 트럭 한도 밖 돌출 / 다른 컴포넌트와 충돌 시 자리 평가 단계에서 거부.
        candidates: List[Tuple[float, int, PlacementSlot, Posture, dict]] = []
        for ts_idx, ts in enumerate(truck_states):
            grid_for_ts = grids[ts_idx]
            for posture in postures:
                if not _truck_posture_compatible(ts.truck, posture, item):
                    continue
                for slot in _allowed_slots_for(item, posture):
                    score, meta = evaluate_slot_v2(
                        item, posture, slot, ts, weights, site, sp,
                        collision_grid=grid_for_ts,
                    )
                    if meta is not None:
                        candidates.append((score, ts_idx, slot, posture, meta))
        # 점수 내림차순 정렬, 상위 K 만
        candidates.sort(key=lambda x: x[0], reverse=True)
        candidates = candidates[:BEAM_K]

        # ② 후보를 순서대로 시도 — V2 충돌 검사 통과 시 채택
        committed = False
        for _score, ts_idx, slot, posture, meta in candidates:
            ts = truck_states[ts_idx]
            grid = grids[ts_idx]
            if not _verify_v2_collision(item, posture, meta, ts, grid, sp):
                continue  # 충돌 — 다음 후보
            # commit + grid 갱신
            _commit_placement(ts, item, slot, posture, meta, sp)
            # 새 placement 의 박스를 grid 에 등록
            from .packer_safety import (
                boxes_of_component, _nominal_dims_for_posture,
                _trip_xyz_from_truck_center,
            )
            L_nom, W_nom, _ = _nominal_dims_for_posture(item, posture)
            xyz_center = meta["xyz"]
            x0, y0, z0 = _trip_xyz_from_truck_center(xyz_center, L_nom, W_nom, ts.truck)
            new_boxes = boxes_of_component(item, posture, (x0, y0, z0))
            new_p_idx = len(ts.placements) - 1
            grid.insert(new_boxes, owner_id=new_p_idx)
            committed = True
            break

        if committed:
            continue

        # ③ 기존 트럭 어디에도 못 들어감 → 새 트럭 선정
        remaining = sorted_items[idx:]
        new_truck = _select_best_new_truck(
            item, trucks, remaining, strategy, site, sp, cost_mode, eco_options,
        )
        if new_truck is None:
            blocked.append((item, _diagnose_blocked(item, list(trucks), site)))
            continue

        ts_new = TruckState(
            truck=new_truck,
            effective_cargo_limit=_effective_cargo_limit(new_truck, site),
        )
        truck_states.append(ts_new)
        grids.append(CollisionGrid())

        # 새 트럭 위에 이 item 의 모든 자세 × FLOOR 자리 다시 평가
        # 새 그리드(빈) 전달 — 트럭 한도 검사가 evaluate_slot_v2 에서 적용됨.
        new_grid = grids[-1]
        best_new_score = float("-inf")
        best_new: Optional[Tuple[Posture, dict]] = None
        v2_fail_reasons: List[str] = []  # 진단용 — can_place_v2 위반 사유 누적
        for posture in postures:
            if not _truck_posture_compatible(new_truck, posture, item):
                v2_fail_reasons.append(f"자세 {posture.value} 호환 X (트럭 {new_truck.truck_type})")
                continue
            score, meta = evaluate_slot_v2(
                item, posture, PlacementSlot.FLOOR, ts_new, weights, site, sp,
                collision_grid=new_grid,
            )
            if meta is not None and score > best_new_score:
                best_new_score = score
                best_new = (posture, meta)
            elif meta is None:
                # 거부 사유 추출 — can_place_v2 직접 호출해 reason 확보
                from .packer_safety import can_place_v2 as _cpv2
                # xyz 계산이 evaluate 안에서 실패할 수 있으므로 임시 FLOOR 자리 좌표 계산
                try:
                    xyz_try = _compute_xyz(ts_new, PlacementSlot.FLOOR, posture, item, sp, None)
                    chk = _cpv2(item, posture, xyz_try, ts_new, site, sp,
                                collision_grid=new_grid)
                    for r in chk.reasons:
                        v2_fail_reasons.append(f"[{posture.value}] {r}")
                except Exception as e:
                    v2_fail_reasons.append(f"[{posture.value}] 검사 예외: {e}")
        if best_new is None:
            reason_text = (
                "새 트럭 추가했으나 자세 매칭 실패"
                + (" — " + " / ".join(v2_fail_reasons[:3]) if v2_fail_reasons else "")
            )
            blocked.append((item, reason_text))
            continue
        _commit_placement(
            ts_new, item, PlacementSlot.FLOOR, best_new[0], best_new[1], sp,
        )
        # 새 트럭의 첫 placement 박스 등록
        from .packer_safety import (
            boxes_of_component, _nominal_dims_for_posture,
            _trip_xyz_from_truck_center,
        )
        L_nom, W_nom, _ = _nominal_dims_for_posture(item, best_new[0])
        x0, y0, z0 = _trip_xyz_from_truck_center(
            best_new[1]["xyz"], L_nom, W_nom, ts_new.truck,
        )
        new_boxes = boxes_of_component(item, best_new[0], (x0, y0, z0))
        grids[-1].insert(new_boxes, owner_id=0)

    # ④ 모듈 합산 6m 후처리 (V1 그대로 — grid 영향 없음)
    truck_states = _merge_small_modules(truck_states, site, sp)

    # ⑤ PackResult 조립
    trips = [
        _truck_state_to_trip(ts, trip_no=i + 1, sp=sp)
        for i, ts in enumerate(truck_states)
    ]
    return PackResult(trips=trips, blocked=blocked)


__all__ = [
    "EcoOptions",
    "default_eco_options",
    "GreedyStrategy",
    "GREEDY_STRATEGIES",
    "WEIGHT_SETS",
    "WEIGHT_SETS_V2",
    "SHUFFLED_STRATEGY",
    "default_truck_score",
    "cost_efficiency_truck_score",
    "evaluate_slot",
    "evaluate_slot_v2",
    "pack_one_seed",
    "pack_one_seed_v2",
    # Phase 5/6/7/8 에서 재사용할 내부 헬퍼들 — _underscore 유지하되 export
    "_postures_for",
    "_truck_posture_compatible",
    "_allowed_slots_for",
    "_is_simple_wall",
    "_is_dependent_floor",
    "_volume_of",
    "_find_stack_bottom_idx",
    "_find_dep_inner_parent_idx",
    "_compute_xyz",
    "_commit_placement",
    "_simulate_packing_weight",
    "_select_best_new_truck",
    "_merge_small_modules",
    "_truck_state_to_trip",
]
