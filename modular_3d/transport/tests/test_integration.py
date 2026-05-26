"""Phase 9 통합(E2E) 테스트 — 씬 → 어댑터 → 패커 → 운임 / 캐시.

[검증 시나리오]
1. 풀 파이프라인: 씬(모듈 2개) → build_transport_input → pack_items →
   compute_economics. 회차 수·총중량·운임 일관성 검증.
2. 캐시 E2E: TransportCache.get_or_compute_pack 2회 호출 시 입력 동일하면
   동일 PackResult 객체 재사용(캐시 hit), 현장 제한 변경 시 재계산(miss).
(시나리오 3 수동 시뮬 E2E — 수동 시뮬레이션 기능 제거로 함께 삭제됨.)

[픽스처 재사용]
test_adapter 의 stub AnalysisModel/DesignResult + 씬 모듈 헬퍼를 그대로 import
해 중복 제거.
"""
from __future__ import annotations

from modular_3d.model.core import Scene
from modular_3d.transport.adapter import TransportOptions, build_transport_input
from modular_3d.transport.cache import TransportCache
from modular_3d.transport.economics import EconomicsOptions, compute_economics
from modular_3d.transport.models import SiteLimit, SpacingParams, Truck
from modular_3d.transport.packer import pack_items

# test_adapter 의 stub 헬퍼 재사용 (DRY)
from modular_3d.transport.tests.test_adapter import (
    SHS_NAME, SHS_SMALL,
    _StubAM, _StubDR, _attach_module_to_model, _make_scene_module,
)


# ── 공용 카탈로그 ──────────────────────────────────────────
def _trucks():
    return [
        Truck(name="저상25t", truck_type="lowbed",
              max_length=13000, max_width=3000, max_height=4000,
              max_weight=25000, curb_weight_kg=15000),
        Truck(name="광폭확장", truck_type="extendable",
              max_length=16000, max_width=3400, max_height=4200,
              max_weight=30000, curb_weight_kg=18000),
    ]


def _site():
    # 현장 제한: GVW 40t, 폭 3000, 높이 4000 (옛 '1급 도로' 한도를 현장 제한으로).
    return SiteLimit(max_gvw_kg=40000, max_width_mm=3000, max_height_mm=4000)


def _build_two_module_scene():
    scene = Scene()
    am, dr = _StubAM(), _StubDR()
    # 모듈 높이 3000 → 외측높이 3700 ≤ 도로/트럭 한도 4000 (운송 가능 보장)
    cid1, c1 = _make_scene_module(scene, x=0, y=0, h=3000)
    cid2, c2 = _make_scene_module(scene, x=4000, y=0, h=3000)
    _attach_module_to_model(am, dr, c1, cid1, SHS_NAME, SHS_SMALL)
    _attach_module_to_model(am, dr, c2, cid2, SHS_NAME, SHS_SMALL)
    return scene, am, dr


# ── 시나리오 1: 풀 파이프라인 ──────────────────────────────
def test_full_pipeline_scene_to_economics():
    scene, am, dr = _build_two_module_scene()
    ti = build_transport_input(scene, am, dr, "3종", TransportOptions())
    assert len(ti.modules) == 2

    pack = pack_items(ti.modules, ti.panels, _trucks(), _site(), SpacingParams())
    # 모듈 1 트럭 = 1 모듈 정책 → 회차 2
    assert pack.total_trips == 2
    assert pack.module_trips == 2
    # 각 회차 화물 무게 > 0, 트럭 한도 이내
    for trip in pack.trips:
        assert trip.cargo_weight > 0
        assert trip.cargo_weight <= trip.truck.max_weight

    eco = compute_economics(pack, EconomicsOptions(distance_km=40))
    # 총 운임 = 회차별 합
    assert abs(eco.total_cost_krw - sum(t.cost_krw for t in eco.trips)) < 1e-6
    # 회차 수 일치
    assert len(eco.trips) == pack.total_trips


# ── 시나리오 2: 캐시 hit / miss ───────────────────────────
def test_cache_hit_and_invalidate():
    scene, am, dr = _build_two_module_scene()
    cache = TransportCache()
    opts = TransportOptions()
    trucks, road, sp = _trucks(), _site(), SpacingParams()
    design = dr

    pack1 = cache.get_or_compute_pack(
        scene, am, design, "3종", opts, trucks, road, sp)
    pack2 = cache.get_or_compute_pack(
        scene, am, design, "3종", opts, trucks, road, sp)
    # 동일 입력 → 같은 객체 (캐시 hit)
    assert pack1 is pack2

    # 현장 제한 변경 → pack_fp 변경 → 재계산 (다른 객체)
    site2 = SiteLimit(max_gvw_kg=20000, max_width_mm=2500, max_height_mm=4000)
    pack3 = cache.get_or_compute_pack(
        scene, am, design, "3종", opts, trucks, site2, sp)
    assert pack3 is not pack1


def test_cache_invalidate_from_levels():
    cache = TransportCache()
    cache.analysis_result = "x"
    cache.design_results["3종"] = "d"
    cache.transport_input = "ti"
    cache.pack_result = "p"
    cache.economics_result = "e"
    # [8] 만 무효 → 운임만 사라짐
    cache.invalidate_from(8)
    assert cache.economics_result is None
    assert cache.pack_result == "p"
    # [6] 부터 무효 → 어댑터·패킹 사라짐, 단면 유지
    cache.invalidate_from(6)
    assert cache.transport_input is None
    assert cache.pack_result is None
    assert cache.design_results == {"3종": "d"}


# (시나리오 3 수동 시뮬 E2E — 수동 시뮬레이션 기능 제거(2026-05-26)로 함께 제거)
