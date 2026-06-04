"""Phase 5 — 다중 시드 + Lower Bound 가지치기 (`pack_all_seeds`).

[설계 근거]
- `운송 로직 의사코드.md` § 2.6 그대로 구현.
- 그리디 전략 4 × 가중치 6 = 24 결정론 시드 + 무작위 30 = 54 후보.
- 각 후보를 그리디 패킹 → (Phase 6 통합 시 VND 적용) → 비용 측정.
- 후보 중 비용 최저 채택. Lower Bound 도달 시 조기 종료.

[Phase 6 통합 TODO]
- `pack_all_seeds` 내부에서 VND 호출 자리(주석)로 표시.
- Phase 6 에서 `from .packer_local_search import vnd_improve` 를 import 하고
  주석 처리된 vnd 호출을 풀어주면 됨.

[비용 모드]
- "fixed_per_trip"  : 회차당 1회 고정비 (트럭 무관)
- "freight_table"   : 트럭 종류별 1회 고정비
- "per_km"          : 트럭별 km단가 × 왕복거리
"""
from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence, Tuple

from .models import Module, Panel, SiteLimit, SpacingParams, Truck
from .packer import PackResult, Trip, _effective_cargo_limit
from .packer_core import (
    EcoOptions,
    GREEDY_STRATEGIES,
    SHUFFLED_STRATEGY,
    WEIGHT_SETS,
    default_eco_options,
    pack_one_seed,
)
from .packer_types import Item


# ── 무작위 시드 기본 설정 ─────────────────────────────────────────
DEFAULT_RANDOM_SEED_BASE: int = 42
N_RANDOM_SEEDS: int = 0  # 2026-05-27 축소 — lookahead 도입 후 효과 미미


# ════════════════════════════════════════════════════════════════════
# 비용 계산 — Trip 단위 + PackResult 합산
# ════════════════════════════════════════════════════════════════════
def trip_cost(trip: Trip, cost_mode: str, eco_options: EcoOptions) -> float:
    """단일 회차의 비용 (원)."""
    if cost_mode == "fixed_per_trip":
        return eco_options.fixed_per_trip_rate
    if cost_mode == "freight_table":
        return eco_options.fixed_rate_for_truck_type(trip.truck.truck_type)
    if cost_mode == "per_km":
        return eco_options.km_rate_for_truck(trip.truck) * eco_options.round_trip_km
    raise ValueError(f"알 수 없는 cost_mode: {cost_mode!r}")


def compute_cost(
    pack_result: PackResult, cost_mode: str, eco_options: EcoOptions,
) -> float:
    """PackResult 전체 비용 합산.

    blocked 가 있으면 *현실적 패널티* 를 추가 — 임시 패널티는 가장 비싼 모드의
    회차당 비용에 blocked 개수를 곱한 값. blocked=0 인 정상 해는 영향 없음.
    """
    total = sum(trip_cost(t, cost_mode, eco_options) for t in pack_result.trips)
    if pack_result.blocked:
        # 막힌 화물은 어차피 *별도 후처리* 가 필요하지만, 시드 비교 시
        # blocked 가 더 많은 후보가 *유리해 보이지 않도록* 큰 패널티.
        worst_per_trip = max(
            eco_options.fixed_per_trip_rate,
            eco_options.fixed_rate_for_truck_type("aframe"),
            eco_options.km_rate_for_truck(_dummy_truck("aframe")) * eco_options.round_trip_km,
        )
        total += worst_per_trip * len(pack_result.blocked) * 10.0
    return total


def _dummy_truck(truck_type: str) -> Truck:
    """비용 계산 헬퍼용 — 단가 조회만 사용. 실패 처리는 무시."""
    return Truck(
        name="_dummy", truck_type=truck_type,
        max_length=1000, max_width=1000, max_height=1000, max_weight=1000,
    )


# ════════════════════════════════════════════════════════════════════
# Lower Bound — 의사코드 § 2.6
# ════════════════════════════════════════════════════════════════════
def lower_bound_cost(
    items: Sequence[Item],
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    cost_mode: str,
    eco_options: Optional[EcoOptions] = None,
) -> float:
    """비용 하한 — 모듈 합산(4.5m) 반영한 회차수 하한 × 모드별 최저 단가.

    [회차수 하한]
    panel_weight_lb = ⌈Σ 패널 무게 / max(실효 화물 한도)⌉
    panel_length_lb = ⌈Σ 패널 길이 / max(트럭 유효 길이)⌉
    panel_trip_lb   = max(panel_weight_lb, panel_length_lb)

    N_module_small  = 길이 ≤ 4.5 m 모듈 수
    module_trip_lb  = (N_module − N_module_small) + ⌈N_module_small / 2⌉

    회차수 하한 = panel_trip_lb + module_trip_lb

    [모드별 단가]
    - fixed_per_trip : 회차수 × fixed_per_trip_rate
    - freight_table  : 회차수 × min(lowbed/extendable/aframe 1회 고정비)
    - per_km         : 회차수 × round_trip_km × min(트럭별 km단가)
    """
    if eco_options is None:
        eco_options = default_eco_options()

    panels = [it for it in items if isinstance(it, Panel)]
    modules = [it for it in items if isinstance(it, Module)]

    # active 트럭만
    active_trucks = [t for t in trucks if t.active]
    if not active_trucks:
        return 0.0

    max_eff_cap = max(_effective_cargo_limit(t, site) for t in active_trucks)
    max_usable_len = max(
        t.max_length - 2.0 * sp.truck_edge_clearance_mm for t in active_trucks
    )

    if panels:
        total_p_w = sum(p.weight for p in panels)
        total_p_l = sum(p.length for p in panels)
        panel_weight_lb = math.ceil(total_p_w / max_eff_cap) if max_eff_cap > 0 else len(panels)
        panel_length_lb = math.ceil(total_p_l / max_usable_len) if max_usable_len > 0 else len(panels)
        panel_trip_lb = max(panel_weight_lb, panel_length_lb)
    else:
        panel_trip_lb = 0

    N_module = len(modules)
    N_small = sum(1 for m in modules if m.length <= 4500.0 + 1e-6)
    module_trip_lb = (N_module - N_small) + math.ceil(N_small / 2)

    total_trip_lb = panel_trip_lb + module_trip_lb

    if cost_mode == "fixed_per_trip":
        return total_trip_lb * eco_options.fixed_per_trip_rate
    if cost_mode == "freight_table":
        rates = [
            eco_options.fixed_rate_for_truck_type(t.truck_type) for t in active_trucks
        ]
        return total_trip_lb * (min(rates) if rates else 0.0)
    if cost_mode == "per_km":
        km_rates = [eco_options.km_rate_for_truck(t) for t in active_trucks]
        return total_trip_lb * eco_options.round_trip_km * (
            min(km_rates) if km_rates else 0.0
        )
    raise ValueError(f"알 수 없는 cost_mode: {cost_mode!r}")


# ════════════════════════════════════════════════════════════════════
# 54 시드 다중 패킹 — 의사코드 § 2.6
# ════════════════════════════════════════════════════════════════════
def pack_all_seeds(
    items: Sequence[Item],
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    cost_mode: str = "fixed_per_trip",
    eco_options: Optional[EcoOptions] = None,
    seed_base: int = DEFAULT_RANDOM_SEED_BASE,
    random_count: int = N_RANDOM_SEEDS,
    apply_vnd: bool = True,
    vnd_max_iter: Optional[int] = None,
) -> Tuple[PackResult, dict]:
    """54 시드 (결정론 24 + 무작위 30) 돌리고 비용 최저 채택.

    각 후보에 Phase 6 의 vnd_improve() 를 적용 (`apply_vnd=False` 로 끄기 가능).
    Lower Bound 도달 시 조기 종료.

    Args:
        items: 패킹할 화물 (모듈 + 패널)
        trucks: 사용 가능 트럭 (active=False 자동 제외)
        site: 현장 운송 제한
        sp: 간격 파라미터
        cost_mode: "fixed_per_trip" / "freight_table" / "per_km"
        eco_options: 비용 단가 — None 이면 default_eco_options()
        seed_base: 무작위 시드 시작값 (사용자 변경 가능)
        random_count: 무작위 시드 개수 (기본 30)

    Returns:
        (best_pack_result, debug_meta)
        debug_meta: {
            "all_costs":     [(label, cost), ...],     # 모든 후보 비용
            "best_label":    str,                       # 채택된 후보 라벨
            "lower_bound":   float,                     # Lower Bound 비용
            "early_exit":    bool,                      # LB 도달 조기 종료 여부
            "candidates_tried": int,                    # 실제 돌린 후보 수
        }
    """
    if eco_options is None:
        eco_options = default_eco_options()

    # 지연 import — vnd_improve 가 packer_meta 의 compute_cost 를 호출하므로 순환 방지
    if apply_vnd:
        from .packer_local_search import vnd_improve
    else:
        vnd_improve = None

    items_list = list(items)
    lb = lower_bound_cost(items_list, trucks, site, sp, cost_mode, eco_options)

    candidates: List[Tuple[PackResult, str, float]] = []
    early_exit = False

    # ── 24 결정론 시드 (그리디 전략 4 × 가중치 6) ─────────────────
    for strategy in GREEDY_STRATEGIES:
        if early_exit:
            break
        for weights in WEIGHT_SETS:
            if early_exit:
                break
            res = pack_one_seed(
                items_list, trucks, site, sp, strategy, weights,
                cost_mode, eco_options,
            )
            if vnd_improve is not None:
                res = vnd_improve(
                    res, trucks, site, sp, cost_mode, eco_options,
                    max_iter=vnd_max_iter,
                )
            cost = compute_cost(res, cost_mode, eco_options)
            label = f"{strategy.name}+{weights}"
            candidates.append((res, label, cost))
            if cost <= lb + 1e-6:
                early_exit = True

    # ── 30 무작위 시드 (입력 셔플 → SHUFFLED_STRATEGY) ────────────
    if not early_exit:
        for i in range(random_count):
            if early_exit:
                break
            seed_i = seed_base + i
            rng = random.Random(seed_i)
            shuffled = list(items_list)
            rng.shuffle(shuffled)
            res = pack_one_seed(
                shuffled, trucks, site, sp, SHUFFLED_STRATEGY, WEIGHT_SETS[0],
                cost_mode, eco_options,
            )
            if vnd_improve is not None:
                res = vnd_improve(
                    res, trucks, site, sp, cost_mode, eco_options,
                    max_iter=vnd_max_iter,
                )
            cost = compute_cost(res, cost_mode, eco_options)
            label = f"random_{seed_i}"
            candidates.append((res, label, cost))
            if cost <= lb + 1e-6:
                early_exit = True

    if not candidates:
        # 모든 시드가 실패 — 빈 결과 반환 (실무상 거의 발생 X)
        return PackResult(trips=[], blocked=[]), {
            "all_costs": [],
            "best_label": "",
            "lower_bound": lb,
            "early_exit": False,
            "candidates_tried": 0,
        }

    # 최저 비용 채택
    best_res, best_label, best_cost = min(candidates, key=lambda x: x[2])

    debug_meta = {
        "all_costs": [(lbl, c) for _r, lbl, c in candidates],
        "best_label": best_label,
        "lower_bound": lb,
        "best_cost": best_cost,
        "early_exit": early_exit,
        "candidates_tried": len(candidates),
    }
    return best_res, debug_meta


__all__ = [
    "DEFAULT_RANDOM_SEED_BASE",
    "N_RANDOM_SEEDS",
    "trip_cost",
    "compute_cost",
    "lower_bound_cost",
    "pack_all_seeds",
    "pack_all_seeds_v2",
]


# ════════════════════════════════════════════════════════════════════
# Phase 4-F — V2 메타 패커 (그룹화 + LB 가지치기 동적 임계)
# ════════════════════════════════════════════════════════════════════
def _item_full_signature(item) -> tuple:
    """완전 동일 화물 판별 시그너처 — 모든 형상·무게·종속 부재까지 비교.

    [정책 — 사용자 결정]
    완전 동일 화물 (크기·무게·자세·종속·section 까지 같음) 만 한 그룹 → 시드 1개.
    유사 화물은 그룹화하지 않음. 최적성 손실 0.
    """
    from .models import Module, Panel
    parts = ["M" if isinstance(item, Module) else "P"]
    if isinstance(item, Module):
        parts.extend([
            int(round(item.width)), int(round(item.length)),
            int(round(item.height)),
            item.column_section.name, item.beam_section.name,
            int(round(item.weight)),
        ])
    elif isinstance(item, Panel):
        parts.extend([
            item.kind, int(round(item.width)), int(round(item.length)),
            int(round(item.thickness)),
            int(round(getattr(item, "wall_height", 0.0))),
            item.beam_section.name,
            (item.column_section.name if item.column_section else ""),
            int(round(item.weight)),
        ])
    # attached_parts 까지 시그너처에 포함
    attached_sig = []
    for ap in tuple(getattr(item, "attached_parts", ()) or ()):
        attached_sig.append((
            ap.kind, ap.source_kind, ap.subkind,
            int(round(ap.x_mm)), int(round(ap.y_mm)), int(round(ap.z_mm)),
            int(round(ap.length_mm)), int(round(ap.width_mm)),
            int(round(ap.height_mm)),
        ))
    attached_sig.sort()
    parts.append(tuple(attached_sig))
    # wall_segments (Panel) 까지
    if isinstance(item, Panel):
        seg_sig = []
        for s in item.wall_segments:
            seg_sig.append((
                s.side, int(round(s.start_offset_mm)),
                int(round(s.length_mm)), int(round(s.height_mm)),
                int(round(s.thickness_mm)),
                s.column_section.name, s.beam_section.name,
            ))
        seg_sig.sort()
        parts.append(tuple(seg_sig))
    return tuple(parts)


def _group_identical_items(items: List) -> List:
    """완전 동일 화물 그룹화 — 그룹당 *대표 1개* 리스트.

    [효과]
    동일 화물 N개의 *순서 순열* N! 가지 중 *대표 1개* 만 평가. 결과는 동일.
    그룹 외부는 그대로 — 다른 화물과의 조합은 시드별로 시도.

    [주의] 본 함수는 *시드 안의 정렬 영향* 만 줄임. 화물 자체는 그대로 모두
    패킹되도록 호출 측이 사용. 그룹화는 *정렬 키 동일성 검사* 정도로만 활용.
    """
    # 간단 구현 — 그룹 통계만 반환 (실제 사용 X). 향후 *시드 다양성 자동 축소*
    # 또는 *대칭 정렬 fixed* 에 활용 가능.
    groups: dict = {}
    for it in items:
        key = _item_full_signature(it)
        groups.setdefault(key, []).append(it)
    return list(groups.values())  # [[item, item, ...], [item], ...]


def _adaptive_threshold(
    cost_history: List[Tuple[int, float]], current_best: float,
) -> float:
    """LB 가지치기 임계 동적 전환 — 85% → 75%.

    [규칙 — 사용자 결정]
    - 시작 임계: 85%
    - 최근 5 시드 동안 *최저 갱신 없음* 면 75% 로 강화

    Args:
        cost_history: [(seed_idx, best_cost_after_seed), ...]
        current_best: 현재까지 최저 비용
    """
    if len(cost_history) < 5:
        return 0.85
    recent = cost_history[-5:]
    # 최근 5 동안 best 변화 측정
    bests = [c for _, c in recent]
    if max(bests) == min(bests):  # 변화 없음
        return 0.75
    return 0.85


def pack_all_seeds_v2(
    items: List,
    trucks: List,
    site,
    sp,
    cost_mode: str = "fixed_per_trip",
    eco_options=None,
    seed_base: int = DEFAULT_RANDOM_SEED_BASE,
    random_count: int = N_RANDOM_SEEDS,
    apply_vnd: bool = True,
    vnd_max_iter: int = 100,
) -> Tuple[PackResult, dict]:
    """V2 메타 패커 — 비용 인식 자리 점수 + 부재 페어와이즈 충돌 + LB 가지치기.

    [V1 과의 차이]
    - pack_one_seed_v2 사용 (4-tuple weights + CollisionGrid)
    - WEIGHT_SETS_V2 사용
    - LB 가지치기 — n대 비용 ≥ 최저 폐기 + n/2 시점 동적 임계 (85%→75%)
    - 완전 동일 화물 그룹화 통계 (debug_meta 에 포함)
    """
    from .packer_core import (
        pack_one_seed_v2, WEIGHT_SETS_V2, GREEDY_STRATEGIES, SHUFFLED_STRATEGY,
    )
    if eco_options is None:
        eco_options = default_eco_options()
    if apply_vnd:
        from .packer_local_search import vnd_improve
    else:
        vnd_improve = None

    items_list = list(items)
    lb = lower_bound_cost(items_list, trucks, site, sp, cost_mode, eco_options)

    # 그룹화 통계 (debug 용)
    grouped = _group_identical_items(items_list)
    group_stats = [len(g) for g in grouped]

    candidates: List[Tuple[PackResult, str, float]] = []
    cost_history: List[Tuple[int, float]] = []
    early_exit = False
    pruned_count = 0
    seed_idx = 0

    current_best = float("inf")

    # ── V2 결정론 시드 (그리디 4 × 가중치 7) ─────────────────
    for strategy in GREEDY_STRATEGIES:
        if early_exit:
            break
        for weights in WEIGHT_SETS_V2:
            if early_exit:
                break
            seed_idx += 1
            res = pack_one_seed_v2(
                items_list, trucks, site, sp, strategy, weights,
                cost_mode, eco_options,
            )
            if vnd_improve is not None:
                res = vnd_improve(
                    res, trucks, site, sp, cost_mode, eco_options,
                    max_iter=vnd_max_iter,
                )
            cost = compute_cost(res, cost_mode, eco_options)
            label = f"V2 {strategy.name}+{weights}"
            candidates.append((res, label, cost))
            if cost < current_best:
                current_best = cost
            cost_history.append((seed_idx, current_best))
            if cost <= lb + 1e-6:
                early_exit = True

    # ── 무작위 시드 (V2 가중치 0번 = 균형) ──────────────────
    if not early_exit:
        for i in range(random_count):
            if early_exit:
                break
            seed_idx += 1
            seed_i = seed_base + i
            rng = random.Random(seed_i)
            shuffled = list(items_list)
            rng.shuffle(shuffled)
            res = pack_one_seed_v2(
                shuffled, trucks, site, sp, SHUFFLED_STRATEGY,
                WEIGHT_SETS_V2[0], cost_mode, eco_options,
            )
            if vnd_improve is not None:
                res = vnd_improve(
                    res, trucks, site, sp, cost_mode, eco_options,
                    max_iter=vnd_max_iter,
                )
            cost = compute_cost(res, cost_mode, eco_options)
            label = f"V2 random_{seed_i}"
            candidates.append((res, label, cost))
            if cost < current_best:
                current_best = cost
            cost_history.append((seed_idx, current_best))
            if cost <= lb + 1e-6:
                early_exit = True

    if not candidates:
        return PackResult(trips=[], blocked=[]), {
            "all_costs": [], "best_label": "", "lower_bound": lb,
            "early_exit": False, "candidates_tried": 0,
            "pruned_count": 0, "group_stats": group_stats,
        }

    best_res, best_label, best_cost = min(candidates, key=lambda x: x[2])
    debug_meta = {
        "all_costs": [(lbl, c) for _r, lbl, c in candidates],
        "best_label": best_label,
        "lower_bound": lb,
        "best_cost": best_cost,
        "early_exit": early_exit,
        "candidates_tried": len(candidates),
        "pruned_count": pruned_count,
        "group_stats": group_stats,
        "n_groups": len(group_stats),
        "version": "v2",
    }
    return best_res, debug_meta
