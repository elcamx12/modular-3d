"""평가 어댑터·케이스 저장 회귀 테스트 (Phase O + P)."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import numpy as np
import pytest

from modular_3d.model.core import (
    ComponentType, Module, Vertical3Module, FloorPanel, CoreSlab,
    CantileverBeam, MidBeam,
)
from modular_3d.evaluation.evaluation_adapter import build_evaluation_data
from modular_3d.evaluation.case_io import save_case, load_case


def _mk_module(cid, x, y, floor=0, w=3400, d=6000):
    return Module(
        id=cid, comp_type=ComponentType.MODULE,
        position=np.array([float(x), float(y), float(floor) * 3000.0]),
        rotation=0, anchor=0,
        dimensions={"width": float(w), "depth": float(d), "height": 3000.0},
        floor_index=floor,
    )


def _mk_fake_quantity_report(steel_total_ton: float = 12.5,
                              total_cost_in_compute: float = 0.0):
    """compute_material_cost 가 받을 수 있는 가짜 QuantityReport.

    steel_items 의 합계행 + 슬래브 + sections_by_group 까지 채움.
    """
    SteelItem = SimpleNamespace
    items = [
        SteelItem(
            section_name="SHS200x8", length_mm=3200, count=12,
            total_length_m=38.4, unit_weight_kg_m=47.9,
            total_weight_ton=1.84, section_type="shs", is_total=False,
        ),
        SteelItem(
            section_name="합계", length_mm=0, count=12,
            total_length_m=38.4, unit_weight_kg_m=0,
            total_weight_ton=steel_total_ton, section_type="shs", is_total=True,
        ),
    ]
    slab = SimpleNamespace(
        total_area_m2=120.5, total_volume_m3=18.07, thickness_mm=150.0,
        rebar_weight_ton=0.5, rebar_ratio=0.012, n_slabs=2,
    )
    rep = SimpleNamespace(
        policy="3종",
        steel_items=items,
        slab=slab,
        sections_by_group={"all": SimpleNamespace(name="SHS200x8")},
        member_ratios={},
        member_critical={},
        ng_groups=[],
        core=None,
    )
    return rep


# ── 1. 빈 입력 ───────────────────────────────────────────
def test_empty_inputs_all_status_false():
    out = build_evaluation_data()
    assert out["status"]["materials_ok"] is False
    assert out["status"]["transport_ok"] is False
    assert out["status"]["schedule_ok"] is False
    assert out["cost"]["total_krw"] == 0


# ── 2. 헤드라인 — 2층×2모듈 ─────────────────────────────
def test_headline_basic_counts():
    comps = [
        _mk_module(1, 0, 0, 0),
        _mk_module(2, 3400, 0, 0),
        _mk_module(3, 0, 0, 1),
        _mk_module(4, 3400, 0, 1),
    ]
    out = build_evaluation_data(components=comps)
    h = out["headline"]
    assert h["floors_above_ground"] == 2
    assert h["modules_total"] == 4
    assert h["footprint_m2"] == pytest.approx(40.8, abs=0.1)
    assert h["total_floor_area_m2"] == pytest.approx(81.6, abs=0.1)


# ── 3. 수직3층모듈 ───────────────────────────────────────
def test_total_floor_area_vertical_module():
    v3 = Vertical3Module(
        id=10, comp_type=ComponentType.VERTICAL_MODULE,
        position=np.array([0.0, 0.0, 0.0]), rotation=0, anchor=0,
        dimensions={"width": 3400.0, "depth": 3400.0, "height": 10000.0},
        floor_index=0,
    )
    out = build_evaluation_data(components=[v3])
    assert out["headline"]["total_floor_area_m2"] == pytest.approx(34.7, abs=0.2)


# ── 4. 부재 구성 ─────────────────────────────────────────
def test_members_breakdown():
    comps = [
        _mk_module(1, 0, 0, 0),
        FloorPanel(
            id=2, comp_type=ComponentType.FLOOR_PANEL,
            position=np.array([5000.0, 0.0, 0.0]), rotation=0, anchor=0,
            dimensions={"width": 2500.0, "depth": 5000.0, "thickness": 150.0},
            floor_index=0,
        ),
        CoreSlab(
            id=3, comp_type=ComponentType.CORE_SLAB,
            position=np.array([0.0, 0.0, 3000.0]), rotation=0, anchor=0,
            dimensions={"width": 4000.0, "depth": 4500.0, "thickness": 180.0},
            floor_index=0,
        ),
        CantileverBeam(
            id=4, comp_type=ComponentType.CANTILEVER_BEAM,
            position=np.array([0.0, 6000.0, 0.0]), rotation=0, anchor=0,
            dimensions={"width": 1500.0, "depth": 200.0, "height": 200.0},
            floor_index=0,
        ),
        MidBeam(
            id=5, comp_type=ComponentType.MID_BEAM,
            position=np.array([0.0, 3000.0, 0.0]), rotation=0, anchor=0,
            dimensions={"width": 3400.0, "depth": 200.0, "height": 200.0},
            floor_index=0,
        ),
    ]
    out = build_evaluation_data(components=comps)
    m = out["members"]
    assert m["panels"]["floor_panel"] == 1
    assert m["panels"]["core_slab"] == 1
    assert m["attached"]["cantilever_beam"] == 1
    assert m["attached"]["mid_beam"] == 1


# ── 5. 운송 — Trip.cargo_weight 평균 + 거리 계산 + eco 비용 ─
def test_transport_avg_load_and_distance_and_cost():
    trip_a = SimpleNamespace(cargo_weight=12000.0)
    trip_b = SimpleNamespace(cargo_weight=14000.0)
    trip_c = SimpleNamespace(cargo_weight=10000.0)
    pack = SimpleNamespace(
        trips=[trip_a, trip_b, trip_c],
        module_trips=2, panel_trips=1,
        avg_utilization=58.5,
    )
    eco = SimpleNamespace(total_cost_krw=2_500_000.0)
    ps = SimpleNamespace(distance_km=30.0)
    out = build_evaluation_data(
        components=[_mk_module(1, 0, 0, 0)],
        project_settings=ps,
        transport_pack=pack, transport_eco=eco,
    )
    t = out["transport"]
    assert t["available"] is True
    assert t["trips_total"] == 3
    assert t["avg_load_kg"] == pytest.approx(12000.0)
    # 운송 거리 = 30 × 3 × 2 = 180
    assert t["distance_km_total"] == pytest.approx(180.0)
    assert t["total_cost_krw"] == 2_500_000.0


# ── 6. 자재비 — QuantityReport + compute_material_cost ───
def test_materials_uses_compute_material_cost():
    rep = _mk_fake_quantity_report()
    quantity_reports = {"3종": rep}
    out = build_evaluation_data(
        components=[_mk_module(1, 0, 0, 0)],
        quantity_reports=quantity_reports,
        current_policy="3종",
    )
    mat = out["materials"]
    assert mat["available"] is True
    assert mat["current_policy"] == "3종"
    # 강재 본수표 (합계행 분리)
    assert len(mat["steel_rows"]) == 1
    assert mat["steel_total"]["total_weight_ton"] == pytest.approx(12.5)
    # 슬래브 정보 채워졌는지
    assert mat["slab"]["total_area_m2"] == pytest.approx(120.5)
    # 비용 dict 가 채워지면 status materials_ok=True
    assert out["status"]["materials_ok"] is True


# ── 7. 정책 폴백 — current_policy 가 보고서 dict 에 없으면 첫 번째 ─
def test_materials_policy_fallback():
    rep = _mk_fake_quantity_report()
    out = build_evaluation_data(
        components=[_mk_module(1, 0, 0, 0)],
        quantity_reports={"1종": rep},
        current_policy="3종",   # 없는 정책 — 1종으로 폴백
    )
    assert out["materials"]["current_policy"] == "1종"


# ── 8. 비용 합계 — 자재+운송+노무+경비 ────────────────────
def test_cost_total_sum():
    rep = _mk_fake_quantity_report()
    pack = SimpleNamespace(trips=[SimpleNamespace(cargo_weight=10000.0)],
                            module_trips=1, panel_trips=0, avg_utilization=50.0)
    eco = SimpleNamespace(total_cost_krw=3_000_000)
    schedule = {
        "total_days": 220, "core_days": 100, "module_install_days": 40,
        "tasks": [], "labor_sum_krw": 50_000_000, "equip_sum_krw": 12_000_000,
    }
    ps = SimpleNamespace(distance_km=20.0)
    out = build_evaluation_data(
        components=[_mk_module(1, 0, 0, 0)],
        project_settings=ps,
        quantity_reports={"3종": rep},
        current_policy="3종",
        transport_pack=pack, transport_eco=eco,
        schedule_payload=schedule,
    )
    c = out["cost"]
    # 자재비는 가짜 단가라 compute_material_cost 안에서 산출됨 — 0 이상.
    assert c["material_krw"] >= 0
    assert c["transport_krw"] == 3_000_000
    assert c["labor_krw"] == 50_000_000
    assert c["equip_krw"] == 12_000_000
    # [2026-06-04] 직접공사비 소계 = 4항목 합.
    assert c["direct_krw"] == (
        c["material_krw"] + c["transport_krw"] + c["labor_krw"] + c["equip_krw"]
    )
    # 총공사비 = 직접비에 간접비·관리비·이윤·부가세 적층(≈1.475배).
    direct = c["direct_krw"]
    expected_total = ((direct * 1.15) * 1.06 * 1.10) * 1.10
    # 반올림 오차 허용(자재비가 정수가 아닐 수 있음).
    assert abs(c["total_krw"] - expected_total) <= 2
    # 적층분(간접+관리+이윤+부가세)은 양수.
    assert c["total_krw"] > c["direct_krw"]


# ── 9. 부분 결과 — 운송만 None ────────────────────────────
def test_partial_results_transport_missing():
    out = build_evaluation_data(
        components=[_mk_module(1, 0, 0, 0)],
        schedule_payload={"total_days": 100, "labor_sum_krw": 1_000_000,
                          "equip_sum_krw": 200_000},
    )
    assert out["status"]["transport_ok"] is False
    assert out["status"]["schedule_ok"] is True
    assert out["cost"]["transport_krw"] == 0
    assert out["cost"]["labor_krw"] == 1_000_000


# ── 10. 케이스 파일 round-trip ────────────────────────────
def test_case_save_load_roundtrip():
    from modular_3d.ui.project_settings import ProjectSettings
    ps = ProjectSettings()
    evaluation_data = build_evaluation_data(components=[_mk_module(1, 0, 0, 0)])
    with tempfile.NamedTemporaryFile(suffix=".case.json", delete=False) as f:
        tmp = f.name
    try:
        save_case(
            path=tmp,
            scene_state={"components": [], "n_floors": 3},
            project_settings=ps,
            evaluation_data=evaluation_data,
            name="test_case",
        )
        loaded = load_case(tmp)
        assert loaded["kind"] == "modular_case_v1"
        assert loaded["name"] == "test_case"
        assert loaded["scene"]["n_floors"] == 3
        assert loaded["results"]["evaluation"]["headline"]["modules_total"] == 1
        assert loaded["project_settings"]["region_city"] == ps.region_city
    finally:
        os.unlink(tmp)


# ── 11. 케이스 파일 kind 불일치 ───────────────────────────
def test_case_load_wrong_kind():
    import json as _json
    with tempfile.NamedTemporaryFile(
        suffix=".case.json", delete=False, mode="w", encoding="utf-8",
    ) as f:
        _json.dump({"kind": "wrong_kind"}, f)
        tmp = f.name
    try:
        with pytest.raises(ValueError):
            load_case(tmp)
    finally:
        os.unlink(tmp)
