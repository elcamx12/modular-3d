"""수동 시뮬레이션 — 단일 회차 가능성 판정 (분석 ⑦, Phase 8).

[목적]
사용자가 직접 고른 화물 리스트 + 트럭 1대 + 도로 1종 + 간격을 받아
**단일 회차** 가 성립하는지만 판정한다. 자동 packing 흐름과 독립.

[원본과의 관계]
운송프로그램 원본 `simulate_manual_trip` 은 우리 패키지에 이식되지 않았으나,
동일 의미를 갖는 함수를 본 모듈에서 패커 API(can_carry, Trip) 를 재사용해
새로 구현한다. B-시리즈 정정 정책(중량 합산·트럭 타입 제한 등) 을 그대로 따른다.

[정책]
- 모듈과 패널을 한 회차에 섞을 수 없음 (원본 정책).
- 서로 다른 종류 패널을 한 회차에 섞을 수 없음 (원본 정책).
- 트럭 종류는 lowbed / extendable 만 허용 (aframe 제외 — B-4).
- 적재 모델: 단일 행(single row) 길이 방향 평적 가정. 적층은 다루지 않음
  (수동 시뮬레이션은 "탐색·가설" 목적이라 단순 모델로 충분).
- 중량: 화물 합계 vs 트럭 max_weight. strict_weight 옵션 시 GVW·도로 한도까지.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

from .models import Module, Panel, RoadClass, SpacingParams, Truck
from .packer import Trip, recheck_trip_with_truck


Item = Union[Module, Panel]


# ── 입출력 데이터모델 ─────────────────────────────────────
@dataclass
class ManualSimInput:
    items: List[Item]
    truck: Truck
    road: RoadClass
    spacing: SpacingParams
    strict_weight: bool = False
    strict_length: bool = False


@dataclass
class ManualSimResult:
    ok: bool
    reason: str
    trip: Optional[Trip]
    cargo_weight: float = 0.0
    weight_utilization: float = 0.0
    length_utilization: float = 0.0


# ── 패널 종류 키 (혼적 검사용) ─────────────────────────────
def _panel_kind_key(p: Panel) -> str:
    """패널을 혼적 가능 단위로 묶는 키.

    순수 floor / 종속 floor(벽 세그먼트 보유) / wall / lshape 를 구분한다.
    어댑터가 종속 패널을 kind='floor' + wall_segments 로 보내므로 wall_segments
    유무로 순수/종속을 가른다.
    """
    if p.kind == "floor":
        return "dependent_floor" if p.wall_segments else "pure_floor"
    return p.kind  # 'wall' | 'lshape'


# ── 핵심 — 단일 회차 판정 ─────────────────────────────────
def simulate_manual_trip(
    items: List[Item],
    truck: Truck,
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    strict_weight: bool = False,
    strict_length: bool = False,
) -> tuple[bool, str, Optional[Trip]]:
    """화물 리스트가 트럭 1대에 단일 회차로 실리는지 판정.

    반환: (ok, reason, trip). ok=True 면 trip 은 렌더 가능한 Trip 객체.

    [B1 정정] 혼적 검사 후 packer.recheck_trip_with_truck 에 위임한다(원본
    simulate_manual_trip 과 동일 패턴). 그래야 패널 다열×다단 적재 용량과
    종속 패널 적층 조건이 정확히 반영된다(이전 단일행 단순화는 패널 용량을
    과소평가했음).
    """
    if not items:
        return False, "화물이 비어 있습니다.", None

    # 1) 모듈/패널 혼적 금지
    is_module_first = isinstance(items[0], Module)
    for it in items:
        if isinstance(it, Module) != is_module_first:
            return False, "한 회차에 모듈과 패널을 섞을 수 없습니다.", None

    # 2) 패널이면 같은 종류만 (순수/종속 floor·wall·lshape 구분)
    if not is_module_first:
        keys = {_panel_kind_key(p) for p in items}  # type: ignore[arg-type]
        if len(keys) > 1:
            return False, (
                "한 회차에 서로 다른 종류의 패널을 섞을 수 없습니다.\n"
                f"  • 발견된 종류: {', '.join(sorted(keys))}"
            ), None

    # 3) 트럭 종류·도로/트럭 한도·총중량·다열다단 용량 → recheck 에 위임
    fake_trip = Trip(trip_no=0, truck=truck, items=list(items))
    return recheck_trip_with_truck(
        fake_trip, truck, road, spacing,
        strict_weight=strict_weight, strict_length=strict_length,
    )


# ── 래퍼 — UI 친화적 결과 ─────────────────────────────────
def run_manual_sim(inp: ManualSimInput) -> ManualSimResult:
    """ManualSimInput → ManualSimResult. UI 가 직접 호출."""
    ok, reason, trip = simulate_manual_trip(
        inp.items, inp.truck, inp.road, inp.spacing,
        strict_weight=inp.strict_weight, strict_length=inp.strict_length,
    )
    if not ok or trip is None:
        return ManualSimResult(ok=False, reason=reason, trip=None)
    return ManualSimResult(
        ok=True, reason="OK", trip=trip,
        cargo_weight=trip.cargo_weight,
        weight_utilization=trip.weight_utilization,
        length_utilization=trip.length_utilization,
    )


__all__ = [
    "ManualSimInput", "ManualSimResult",
    "simulate_manual_trip", "run_manual_sim",
]
