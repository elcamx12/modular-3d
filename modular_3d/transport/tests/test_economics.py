"""단위 테스트 — economics.py (거리 km 기반 두 방식).

[검증 시나리오 — 2026-05-24 개편]
- 요금표 방식(기본): 톤수×왕복거리 계단식 조회
- 트레일러별 km단가 방식: 종류별(저상/광폭/aframe) 단가 × 왕복거리
- 회차별 + 총합 / 방식별 분리 / 빈 PackResult
- rate_label·cost_label 포맷
"""
from __future__ import annotations

import pytest

from modular_3d.transport.models import Section, Module, Panel, Truck, SpacingParams
from modular_3d.transport.packer import Trip, PackResult
from modular_3d.transport.economics import (
    EconomicsOptions, compute_trip_cost, compute_economics,
)


SHS = Section(name="SHS200x8", section_type="SHS",
              width=200, height=200, thickness=8, weight_per_m=47.9)

TR_LOWBED = Truck(name="저상24t", truck_type="lowbed",
                  max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
                  vehicle_height_offset=700,
                  curb_weight_kg=14000, active=True)
TR_EXTEND = Truck(name="광폭28t", truck_type="extendable",
                  max_length=18000, max_width=3400, max_height=4500, max_weight=28000,
                  vehicle_height_offset=700,
                  curb_weight_kg=16000, active=True)


def _trip(truck, trip_no=1):
    m = Module(name="m", width=3000, length=6000, height=3400,
               column_section=SHS, beam_section=SHS, extra_weight_kg=5000)
    return Trip(trip_no=trip_no, truck=truck, items=[m],
                panels_per_row=1, n_layers=1,
                used_length_mm=6000, usable_length_mm=11600)


# ── 요금표 방식 (기본) ────────────────────────────────────
def test_freight_table_default_mode():
    from modular_3d.transport.economics import freight_col, lookup_freight_rate
    # 기본 cost_mode='freight_table'. 24t 저상 → '25톤' 열. 편도 30 → 왕복 60km.
    opts = EconomicsOptions(distance_km=30.0)
    cost = compute_trip_cost(_trip(TR_LOWBED), opts)
    assert cost.pricing_mode == "freight_table"
    assert cost.rate_label == "요금표"
    assert cost.distance_km == pytest.approx(60.0)
    expected = lookup_freight_rate(60.0, freight_col(TR_LOWBED))
    assert cost.cost_krw == pytest.approx(expected)


# ── 트레일러별 km단가 방식 ───────────────────────────────
def test_per_km_lowbed():
    opts = EconomicsOptions(distance_km=30.0,
                            cost_mode="per_km", lowbed_per_km_krw=3500.0)
    cost = compute_trip_cost(_trip(TR_LOWBED), opts)
    # 항상 왕복 60 km × 3500 = 210000
    assert cost.pricing_mode == "per_km"
    assert cost.cost_krw == pytest.approx(60.0 * 3500.0)
    assert cost.rate_label == "₩3,500/km"


def test_per_km_extendable():
    opts = EconomicsOptions(distance_km=30.0,
                            cost_mode="per_km", extendable_per_km_krw=8000.0)
    cost = compute_trip_cost(_trip(TR_EXTEND), opts)
    # 항상 왕복 60 km × 8000 = 480000
    assert cost.pricing_mode == "per_km"
    assert cost.cost_krw == pytest.approx(60.0 * 8000.0)


def test_per_km_aframe():
    tr = Truck(name="aframe24t", truck_type="aframe",
               max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
               vehicle_height_offset=700,
               curb_weight_kg=14000, active=True)
    opts = EconomicsOptions(distance_km=50.0,
                            cost_mode="per_km", aframe_per_km_krw=9000.0)
    cost = compute_trip_cost(_trip(tr), opts)
    # 항상 왕복 → 편도 50 × 2 = 100 km, 100 × 9000 = 900000
    assert cost.pricing_mode == "per_km"
    assert cost.distance_km == pytest.approx(100.0)
    assert cost.cost_krw == pytest.approx(100.0 * 9000.0)


# ── PackResult 전체 — 회차별 + 총합 ──────────────────────
def test_compute_economics_aggregate():
    trips = [_trip(TR_LOWBED, 1), _trip(TR_LOWBED, 2), _trip(TR_EXTEND, 3)]
    result = compute_economics(
        PackResult(trips=trips, blocked=[]),
        EconomicsOptions(distance_km=30.0, cost_mode="per_km",
                         lowbed_per_km_krw=3500.0, extendable_per_km_krw=8000.0),
    )
    assert len(result.trips) == 3
    assert result.total_cost_krw == pytest.approx(
        sum(c.cost_krw for c in result.trips)
    )
    # per_km 방식이므로 per_km_total == total, freight_total == 0
    assert result.per_km_total_krw == pytest.approx(result.total_cost_krw)
    assert result.freight_total_krw == pytest.approx(0.0)
    assert result.average_per_trip_krw == pytest.approx(result.total_cost_krw / 3)


def test_compute_economics_empty():
    result = compute_economics(PackResult(trips=[], blocked=[]))
    assert result.total_cost_krw == 0.0
    assert result.average_per_trip_krw == 0.0


# ── cost_label 포맷 ─────────────────────────────────────
def test_cost_label_format():
    opts = EconomicsOptions(distance_km=30.0, cost_mode="per_km",
                            lowbed_per_km_krw=3500.0)
    cost = compute_trip_cost(_trip(TR_LOWBED), opts)
    label = cost.cost_label
    assert label.startswith("₩")
    assert "," in label  # 천단위 구분


# ── 요금표 룩업 단위 ────────────────────────────────────
def test_freight_table_step_function():
    from modular_3d.transport.economics import lookup_freight_rate
    # 계단식 — 60km 는 70km 행, 정확히 70km 도 70km 행
    assert lookup_freight_rate(60, "25톤") == 23 * 10_000
    assert lookup_freight_rate(70, "25톤") == 23 * 10_000
    # 최소·최대 경계
    assert lookup_freight_rate(1, "11톤") == 11 * 10_000
    assert lookup_freight_rate(9999, "추레라") == 79 * 10_000


def test_freight_col_mapping():
    from modular_3d.transport.economics import freight_col
    def _tk(w):
        return Truck(name="t", truck_type="lowbed", max_length=12000,
                     max_width=3000, max_height=4500, max_weight=w)
    assert freight_col(_tk(11000)) == "11톤"
    assert freight_col(_tk(18000)) == "18톤"
    assert freight_col(_tk(25000)) == "25톤"
    assert freight_col(_tk(30000)) == "추레라"


# ── C단계: 트레일러별 1회 고정비 (거리 무관) ───────────────
def test_fixed_per_trip_distance_independent():
    o1 = EconomicsOptions(distance_km=30, cost_mode="fixed_per_trip",
                          lowbed_fixed_krw=600000)
    o2 = EconomicsOptions(distance_km=300, cost_mode="fixed_per_trip",
                          lowbed_fixed_krw=600000)
    c1 = compute_trip_cost(_trip(TR_LOWBED), o1)
    c2 = compute_trip_cost(_trip(TR_LOWBED), o2)
    assert c1.cost_krw == 600000          # 저상 고정비
    assert c2.cost_krw == 600000          # 거리 달라도 동일
    assert c1.pricing_mode == "fixed_per_trip"
    assert c1.rate_label == "1회 고정"


def test_fixed_per_trip_by_truck_type():
    opts = EconomicsOptions(cost_mode="fixed_per_trip",
                            lowbed_fixed_krw=600000,
                            extendable_fixed_krw=700000,
                            aframe_fixed_krw=800000)
    assert compute_trip_cost(_trip(TR_LOWBED), opts).cost_krw == 600000
    assert compute_trip_cost(_trip(TR_EXTEND), opts).cost_krw == 700000
