"""운임 계산 — 거리(km) 기반 두 방식.

[운임 모델 — 2026-05-24 개편]
거리(km) 기반 두 방식만 쓴다. (시간단가·주행시간 방식은 제거)
- ① 요금표(freight_table): 트럭 톤수 × 왕복거리 계단식 내장 요금표
  (전국특송24시콜) 조회 → 회차당 요금. 단가 입력이 필요 없는 기본 방식.
- ② 트레일러별 km단가(per_km): 트럭 종류별(저상/광폭/A-frame) 원/km 단가 ×
  왕복거리 → 회차당 비용. 단가는 프로젝트 설정에서 종류별로 입력.

[운임 단위]
- `TripCost`: 회차 1 건 비용 + 메타(방식·거리·적용단가 라벨)
- `EconomicsResult`: 회차별 리스트 + 총합 + 평균 + 방식별 분리

[단순화]
- 톨게이트·연료비·기사 식대 등 부가 비용 미반영. 필요 시 추후 확장.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .models import Truck
from .packer import Trip, PackResult


# ── [C1] 화물운송요금표 (전국특송24시콜, 편도 기준, 단위: 만원) ──────
# 출처: https://korea24call.com:447/new/rate/charge01.html (원본 app.py 이식)
# 톤수 열 × 왕복거리 행 계단식. 운임 방식='freight_table' 일 때 사용.
FREIGHT_RATE_TABLE = {
    5:   {"11톤": 11, "18톤": 16, "25톤": 18, "추레라": 20},
    10:  {"11톤": 12, "18톤": 17, "25톤": 19, "추레라": 21},
    20:  {"11톤": 13, "18톤": 18, "25톤": 20, "추레라": 22},
    30:  {"11톤": 14, "18톤": 19, "25톤": 21, "추레라": 23},
    50:  {"11톤": 15, "18톤": 20, "25톤": 22, "추레라": 24},
    70:  {"11톤": 17, "18톤": 21, "25톤": 23, "추레라": 25},
    90:  {"11톤": 19, "18톤": 22, "25톤": 25, "추레라": 27},
    110: {"11톤": 20, "18톤": 24, "25톤": 27, "추레라": 29},
    130: {"11톤": 21, "18톤": 25, "25톤": 28, "추레라": 30},
    150: {"11톤": 23, "18톤": 27, "25톤": 30, "추레라": 33},
    170: {"11톤": 24, "18톤": 28, "25톤": 31, "추레라": 35},
    200: {"11톤": 24, "18톤": 28, "25톤": 31, "추레라": 35},
    250: {"11톤": 33, "18톤": 39, "25톤": 42, "추레라": 45},
    300: {"11톤": 37, "18톤": 45, "25톤": 48, "추레라": 50},
    350: {"11톤": 38, "18톤": 50, "25톤": 53, "추레라": 56},
    400: {"11톤": 42, "18톤": 55, "25톤": 58, "추레라": 65},
    450: {"11톤": 44, "18톤": 58, "25톤": 62, "추레라": 69},
    500: {"11톤": 46, "18톤": 60, "25톤": 64, "추레라": 71},
    550: {"11톤": 48, "18톤": 63, "25톤": 67, "추레라": 75},
    600: {"11톤": 50, "18톤": 65, "25톤": 70, "추레라": 79},
}


def freight_col(truck: Truck) -> str:
    """톤수 기준 요금표 열 자동 매핑 (트럭 max_weight kg)."""
    w = truck.max_weight
    if w <= 11_000:
        return "11톤"
    if w <= 18_000:
        return "18톤"
    if w <= 25_000:
        return "25톤"
    return "추레라"


def lookup_freight_rate(round_trip_km: float, col: str) -> int:
    """왕복 거리 기준 요금(원). 계단식 — 해당 거리 이상인 첫 구간 요금."""
    dists = sorted(FREIGHT_RATE_TABLE)
    if round_trip_km <= dists[0]:
        return FREIGHT_RATE_TABLE[dists[0]][col] * 10_000
    if round_trip_km > dists[-1]:
        return FREIGHT_RATE_TABLE[dists[-1]][col] * 10_000
    for d in dists:
        if round_trip_km <= d:
            return FREIGHT_RATE_TABLE[d][col] * 10_000
    return FREIGHT_RATE_TABLE[dists[-1]][col] * 10_000


@dataclass(frozen=True)
class EconomicsOptions:
    """운임 계산 옵션. 운송탭/프로젝트 설정 위젯과 매핑.

    [운임 방식]
    - cost_mode='freight_table' (기본): 요금표 자동 조회. km단가 미사용.
    - cost_mode='per_km': 트럭 종류별 km단가 × 왕복거리.
    """
    distance_km: float = 30.0             # 편도 거리 (운임은 항상 왕복=편도×2)
    cost_mode: str = "freight_table"      # 'freight_table' | 'per_km'
    # 트럭 종류별 km단가 (per_km 방식에서 사용)
    lowbed_per_km_krw: float = 3500.0     # 저상/초저상 트레일러
    extendable_per_km_krw: float = 5000.0 # 광폭(확장형) 트레일러
    aframe_per_km_krw: float = 5000.0     # A-frame 트레일러


@dataclass(frozen=True)
class TripCost:
    """회차 1 건의 운임 정보."""
    trip_no: int
    truck_name: str
    truck_type: str
    cost_krw: float
    pricing_mode: str       # 'freight_table' | 'per_km'
    distance_km: float      # 회차당 주행거리(왕복 반영)
    rate_label: str         # 표시용 — per_km: '₩x/km', freight_table: '요금표'

    @property
    def cost_label(self) -> str:
        return f"₩{int(round(self.cost_krw)):,}"


@dataclass
class EconomicsResult:
    """패킹 결과 전체에 대한 운임 합산."""
    options: EconomicsOptions
    trips: List[TripCost] = field(default_factory=list)

    @property
    def total_cost_krw(self) -> float:
        return sum(t.cost_krw for t in self.trips)

    @property
    def freight_total_krw(self) -> float:
        return sum(t.cost_krw for t in self.trips
                   if t.pricing_mode == "freight_table")

    @property
    def per_km_total_krw(self) -> float:
        return sum(t.cost_krw for t in self.trips if t.pricing_mode == "per_km")

    @property
    def average_per_trip_krw(self) -> float:
        if not self.trips:
            return 0.0
        return self.total_cost_krw / len(self.trips)


# ── 트럭 종류별 km단가 선택 ───────────────────────────────
def _per_km_rate(options: EconomicsOptions, truck_type: str) -> float:
    """트럭 종류 → km단가. 저상/초저상은 lowbed 단가, 광폭은 extendable,
    A-frame 은 aframe 단가. 알 수 없는 종류는 lowbed 단가로 처리."""
    if truck_type == "extendable":
        return options.extendable_per_km_krw
    if truck_type == "aframe":
        return options.aframe_per_km_krw
    return options.lowbed_per_km_krw


# ── 회차 1 건 운임 ───────────────────────────────────────
def compute_trip_cost(
    trip: Trip, options: EconomicsOptions,
) -> TripCost:
    """회차 1 건의 운임 산정 (거리 기반 두 방식)."""
    tr = trip.truck
    # 운임은 항상 왕복거리 기준(트럭 회송 포함). GitHub 원본과 동일.
    distance_total_km = options.distance_km * 2.0

    # ① 요금표 방식 — 톤수 × 왕복거리 계단식 (전국특송24시콜)
    if options.cost_mode == "freight_table":
        cost = float(lookup_freight_rate(distance_total_km, freight_col(tr)))
        return TripCost(
            trip_no=trip.trip_no, truck_name=tr.name,
            truck_type=tr.truck_type, cost_krw=cost,
            pricing_mode="freight_table",
            distance_km=distance_total_km, rate_label="요금표",
        )

    # ② 트레일러별 km단가 방식 — 종류별 단가 × 왕복거리
    per_km = _per_km_rate(options, tr.truck_type)
    cost = per_km * distance_total_km
    return TripCost(
        trip_no=trip.trip_no, truck_name=tr.name,
        truck_type=tr.truck_type, cost_krw=cost,
        pricing_mode="per_km",
        distance_km=distance_total_km,
        rate_label=f"₩{int(round(per_km)):,}/km",
    )


# ── PackResult 전체 ──────────────────────────────────────
def compute_economics(
    pack_result: PackResult,
    options: Optional[EconomicsOptions] = None,
) -> EconomicsResult:
    """패킹 결과 전체에 대한 운임 산정 (회차별 + 총합)."""
    if options is None:
        options = EconomicsOptions()
    out = EconomicsResult(options=options)
    for trip in pack_result.trips:
        out.trips.append(compute_trip_cost(trip, options))
    return out


__all__ = [
    "EconomicsOptions", "TripCost", "EconomicsResult",
    "compute_trip_cost", "compute_economics",
    "FREIGHT_RATE_TABLE", "freight_col", "lookup_freight_rate",
]
