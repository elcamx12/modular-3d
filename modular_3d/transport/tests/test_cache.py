"""Phase 5 단위 테스트 — cache.py.

[검증 시나리오 (분석 ⑧ 10번 항목)]
1. 첫 호출 → 전부 miss → 8단계 모두 계산
2. 같은 입력 재호출 → 전부 hit → pack_result 객체 동일
3. 단위중량 변경 → [6] 어댑터부터 재계산
4. invalidate_from(3) → design + seg + classify + ti + pack 모두 무효
5. invalidate_from(7) → pack 만 무효, ti 유지
6. cache_status_flags 정확
7. fingerprint 일치성
8. 같은 정책 design_result 캐시 1개만 보관 (사용자 결정 ⑧-4)
"""
from __future__ import annotations

import numpy as np
import pytest

from modular_3d.model.core import Scene, instantiate, ComponentType
from modular_3d.카탈로그.steel_sections import find_section_by_name
from modular_3d.transport.adapter import TransportOptions
from modular_3d.transport.cache import (
    TransportCache, compute_scene_fp, compute_classify_fp,
    compute_trucks_fp, compute_pack_fp,
)
from modular_3d.transport.catalog_io import load_all_trucks, load_all_roads
from modular_3d.transport.economics import EconomicsOptions
from modular_3d.transport.models import SpacingParams
from modular_3d.transport.wall_classifier import ClassifierOptions
from modular_3d.transport.tests.test_adapter import (
    _StubAM, _StubDR, _attach_module_to_model,
)


# ── 공통 픽스처 ─────────────────────────────────────────
def _make_scene_and_design():
    scene = Scene()
    m = instantiate(ComponentType.MODULE, np.array([0.0, 0.0, 0.0]),
                    {"width": 3000.0, "depth": 6000.0, "height": 3400.0}, 0, 0)
    cid = scene.add_component(m)
    am, dr = _StubAM(), _StubDR()
    _attach_module_to_model(am, dr, m, cid, "SHS 200x200x8", "SHS 150x150x6")
    return scene, am, dr


def _make_inputs():
    trucks = load_all_trucks(active_only=True)
    road = next(r for r in load_all_roads() if "광로" in r.name)
    return trucks, road, SpacingParams()


# ── 1. 첫 호출 — 전부 miss ──────────────────────────────
def test_first_call_computes_all():
    scene, am, dr = _make_scene_and_design()
    trucks, road, sp = _make_inputs()
    cache = TransportCache()
    flags = cache.cache_status_flags()
    assert all(v == "✗" for v in flags.values())  # 시작 시 모두 stale

    pack = cache.get_or_compute_pack(
        scene, am, dr, "3종", TransportOptions(), trucks, road, sp,
    )
    assert pack is not None
    assert cache.pack_result is pack
    assert cache.transport_input is not None
    flags = cache.cache_status_flags()
    assert flags[6] == "✓"
    assert flags[7] == "✓"


# ── 2. 같은 입력 재호출 — hit ────────────────────────────
def test_second_call_same_input_returns_cached():
    scene, am, dr = _make_scene_and_design()
    trucks, road, sp = _make_inputs()
    cache = TransportCache()
    options = TransportOptions()
    first = cache.get_or_compute_pack(scene, am, dr, "3종", options, trucks, road, sp)
    second = cache.get_or_compute_pack(scene, am, dr, "3종", options, trucks, road, sp)
    assert first is second  # 같은 객체 — 진짜 캐시 hit


# ── 3. 단위중량만 변경 → [6] 부터 재계산 ──────────────────
def test_unit_weight_change_invalidates_from_step_6():
    scene, am, dr = _make_scene_and_design()
    trucks, road, sp = _make_inputs()
    cache = TransportCache()
    opts1 = TransportOptions(interior_wall_unit_weight=30.0)
    opts2 = TransportOptions(interior_wall_unit_weight=40.0)
    pack1 = cache.get_or_compute_pack(scene, am, dr, "3종", opts1, trucks, road, sp)
    pack2 = cache.get_or_compute_pack(scene, am, dr, "3종", opts2, trucks, road, sp)
    # ti_fp 가 달라야 (option hash 다름) → pack_result 다른 객체
    assert pack1 is not pack2
    # classify_fp 는 그대로 유지 (단위중량은 classify 입력에 없음)
    # ti 가 새로 계산되어 transport_input 변경됨
    assert cache.transport_input is not None


# ── 4. invalidate_from 다중 단계 무효 ────────────────────
def test_invalidate_from_step_3_clears_design_and_below():
    cache = TransportCache()
    # 임의 채움
    cache.scene_fp = "S"
    cache.analysis_result = "ops"
    cache.analysis_fp = "A"
    cache.design_results["3종"] = "design"
    cache.design_fp["3종"] = "DFP"
    cache.seg_results[("3종", 1)] = "seg"
    cache.seg_fp[("3종", 1)] = "SFP"
    cache.classify_result = "cls"
    cache.classify_fp = "CFP"
    cache.transport_input = "ti"
    cache.ti_fp = "TFP"
    cache.pack_result = "pr"
    cache.pack_fp = "PFP"
    cache.invalidate_from(3)
    # [3] 부터 무효
    assert not cache.design_results
    assert not cache.seg_results
    assert cache.classify_result is None
    assert cache.transport_input is None
    assert cache.pack_result is None
    # [2] 는 유지 (step 3 부터)
    assert cache.analysis_result == "ops"


def test_invalidate_from_step_7_only_clears_pack():
    cache = TransportCache()
    cache.transport_input = "ti"
    cache.ti_fp = "TFP"
    cache.pack_result = "pr"
    cache.pack_fp = "PFP"
    cache.economics_result = "eco"
    cache.invalidate_from(7)
    assert cache.transport_input == "ti"   # [6] 은 유지
    assert cache.pack_result is None       # [7] 부터 무효
    assert cache.economics_result is None  # [8] 도 무효


# ── 5. cache_status_flags ─────────────────────────────
def test_cache_status_flags_initial_all_stale():
    flags = TransportCache().cache_status_flags()
    assert flags == {2: "✗", 3: "✗", 4: "✗", 5: "✗", 6: "✗", 7: "✗", 8: "✗"}


def test_cache_status_flags_after_pack():
    scene, am, dr = _make_scene_and_design()
    trucks, road, sp = _make_inputs()
    cache = TransportCache()
    cache.get_or_compute_pack(scene, am, dr, "3종", TransportOptions(),
                               trucks, road, sp)
    flags = cache.cache_status_flags()
    assert flags[6] == "✓"
    assert flags[7] == "✓"


# ── 6. fingerprint 결정성 ──────────────────────────────
def test_scene_fp_deterministic():
    scene, _, _ = _make_scene_and_design()
    fp1 = compute_scene_fp(scene)
    fp2 = compute_scene_fp(scene)
    assert fp1 == fp2


def test_scene_fp_changes_on_mutation():
    scene, _, _ = _make_scene_and_design()
    fp1 = compute_scene_fp(scene)
    # 부재 1 추가
    m = instantiate(ComponentType.MODULE, np.array([7000.0, 0.0, 0.0]),
                    {"width": 3000.0, "depth": 6000.0, "height": 3400.0}, 0, 0)
    scene.add_component(m)
    fp2 = compute_scene_fp(scene)
    assert fp1 != fp2


def test_classify_fp_changes_on_option_change():
    scene, _, _ = _make_scene_and_design()
    fp1 = compute_classify_fp(compute_scene_fp(scene),
                                ClassifierOptions(segment_size_mm=100.0))
    fp2 = compute_classify_fp(compute_scene_fp(scene),
                                ClassifierOptions(segment_size_mm=200.0))
    assert fp1 != fp2


def test_trucks_fp_changes_on_truck_edit():
    trucks = load_all_trucks(active_only=True)
    fp1 = compute_trucks_fp(trucks)
    # 인위적으로 한 종 max_weight 변경 후 fingerprint
    from dataclasses import replace
    modified = trucks[:]
    modified[0] = replace(modified[0], max_weight=trucks[0].max_weight + 1000)
    fp2 = compute_trucks_fp(modified)
    assert fp1 != fp2


# ── 7. set_analysis_result ─────────────────────────────
def test_set_analysis_result_invalidates_below():
    cache = TransportCache()
    # 임의 하위 캐시 채우고 set_analysis_result 호출
    cache.design_results["3종"] = "design"
    cache.pack_result = "pr"
    cache.set_analysis_result("scene_fp_v1", ops_results="ops")
    assert cache.analysis_result == "ops"
    assert not cache.design_results
    assert cache.pack_result is None


# ── 8. economics 라운드트립 ─────────────────────────────
def test_economics_after_pack():
    scene, am, dr = _make_scene_and_design()
    trucks, road, sp = _make_inputs()
    cache = TransportCache()
    cache.get_or_compute_pack(scene, am, dr, "3종", TransportOptions(),
                               trucks, road, sp)
    eco = cache.get_or_compute_economics(EconomicsOptions())
    assert eco is not None
    assert eco.total_cost_krw >= 0


def test_economics_requires_pack_first():
    cache = TransportCache()
    with pytest.raises(RuntimeError, match="pack_result"):
        cache.get_or_compute_economics(EconomicsOptions())


# ── 9. 정책별 design 캐시 1개만 (사용자 결정 ⑧-4) ───────
def test_current_policy_tracking():
    scene, am, dr = _make_scene_and_design()
    trucks, road, sp = _make_inputs()
    cache = TransportCache()
    cache.get_or_compute_pack(scene, am, dr, "3종", TransportOptions(),
                               trucks, road, sp)
    assert cache.current_policy == "3종"
    assert "3종" in cache.design_results
