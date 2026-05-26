"""schedule_adapter 회귀 테스트 (Phase F).

[설계 근거]
- 공정표_이식_계획서.md §3 Phase F.
- 우리 어댑터가 만든 scene 데이터가 팀원 HTML 의 calc() 워닝 분기를 발화시키지
  않는 정상 입력이 되는지 / summary 핵심 카운트가 기대치와 맞는지 검증.
- HTML JS 자체는 실행하지 않는다(브라우저 없이). 대신 calc() 의 워닝 조건을
  파이썬으로 모사: 세대층 ≥ 1 / 층당세대 ≥ 1 / 세대당모듈 ≥ 1 만 만족하면 워닝 없음.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from modular_3d.model.core import (
    ComponentType,
    Module,
    Vertical3Module,
    FloorPanel,
    CoreSlab,
)
from modular_3d.schedule.schedule_adapter import build_scene_data


def _mk_module(cid: int, x: float, y: float, floor: int, w: float = 3400, d: float = 6000) -> Module:
    return Module(
        id=cid, comp_type=ComponentType.MODULE,
        position=np.array([float(x), float(y), float(floor) * 3000.0]),
        rotation=0, anchor=0,
        dimensions={"width": float(w), "depth": float(d), "height": 3000.0},
        floor_index=floor,
    )


# ── 1. 빈 씬 — 모든 카운트 0 ─────────────────────────────
def test_empty_scene_returns_zero_counts():
    out = build_scene_data([])
    s = out["summary"]
    assert s["modules_total"] == 0
    assert s["floors_above_ground"] == 0
    assert s["modules_per_floor"] == 0
    assert s["vertical_joints_total"] == 0
    assert s["horizontal_joints_total"] == 0
    assert s["footprint_area_m2"] == 0.0
    # 빈 씬에서도 JSON 직렬화 가능해야 함 (Phase C 의 page().runJavaScript 안전)
    json.dumps(out, ensure_ascii=False)


# ── 2. 2층 × 2모듈 — 수직 8 / 수평 2 ────────────────────
def test_two_floor_two_module_per_floor_counts():
    comps = [
        _mk_module(1, 0, 0, 0),
        _mk_module(2, 3400, 0, 0),
        _mk_module(3, 0, 0, 1),
        _mk_module(4, 3400, 0, 1),
    ]
    out = build_scene_data(comps)
    s = out["summary"]
    assert s["floors_above_ground"] == 2
    assert s["modules_total"] == 4
    assert s["modules_per_floor"] == 2
    assert s["vertical_joints_total"] == 8   # floor=1 모듈 2개 × 4 꼭지점
    assert s["horizontal_joints_total"] == 2  # 층당 1쌍 × 2층
    # floor 0 모듈 2개 × 3.4 × 6.0 = 40.8 m²
    assert s["footprint_area_m2"] == pytest.approx(40.8, abs=0.1)
    # calc() 워닝 분기 모사 — 정상 입력이어야 함
    assert s["floors_above_ground"] >= 1
    assert s["modules_per_floor"] >= 1


# ── 3. 수직3층모듈 단독 — 자체 층간 수직접합 없음 ────────
def test_vertical_module_no_self_vertical_joints():
    v3 = Vertical3Module(
        id=10, comp_type=ComponentType.VERTICAL_MODULE,
        position=np.array([0.0, 0.0, 0.0]),
        rotation=0, anchor=0,
        dimensions={"width": 3400.0, "depth": 3400.0, "height": 10000.0},
        floor_index=0,
    )
    out = build_scene_data([v3])
    s = out["summary"]
    # 관통 층수 = round(10000/3000) ≈ 3
    assert s["floors_above_ground"] == 3
    assert s["modules_total"] == 1
    # 수직3 자체 층간 접합 없음 — 옆에 일반 모듈이 없으니 수평도 없음
    assert s["vertical_joints_total"] == 0
    assert s["horizontal_joints_total"] == 0


# ── 4. 수직3층모듈 + 인접 일반모듈 — 층별 수평접합 발생 ─
def test_vertical_module_horizontal_joints_per_floor_with_neighbor():
    # 수직3은 1~3층 관통. 일반모듈 3개를 각 층에 옆에 둠 → 매 층 1쌍 인접 = 3
    v3 = Vertical3Module(
        id=10, comp_type=ComponentType.VERTICAL_MODULE,
        position=np.array([0.0, 0.0, 0.0]),
        rotation=0, anchor=0,
        dimensions={"width": 3400.0, "depth": 3400.0, "height": 10000.0},
        floor_index=0,
    )
    neighbors = [
        _mk_module(1, 3400, 0, 0, w=3400, d=3400),
        _mk_module(2, 3400, 0, 1, w=3400, d=3400),
        _mk_module(3, 3400, 0, 2, w=3400, d=3400),
    ]
    out = build_scene_data([v3, *neighbors])
    s = out["summary"]
    assert s["floors_above_ground"] == 3
    # 각 층에서 v3 + 그 층 일반모듈 1개 = 1쌍 인접 × 3층 = 3
    assert s["horizontal_joints_total"] == 3


# ── 5. JSON 안전 — numpy / 회전 / 앵커가 섞여도 직렬화 OK ─
def test_json_serializable_with_numpy_and_rotation():
    m = _mk_module(7, 0, 0, 0)
    m.rotation = 90
    m.anchor = 1
    m.joint_records = [
        {"corner_idx": 0,
         "target_comp_id": np.int64(99),
         "target_xy": np.array([100.0, 200.0]),
         "target_z": np.float64(0.0)},
    ]
    fp = FloorPanel(
        id=20, comp_type=ComponentType.FLOOR_PANEL,
        position=np.array([0.0, 0.0, 0.0]), rotation=0, anchor=0,
        dimensions={"width": 3400.0, "depth": 6000.0, "thickness": 150.0},
        floor_index=0,
    )
    out = build_scene_data([m, fp])
    text = json.dumps(out, ensure_ascii=False)
    assert "target_comp_id" in text
    assert "joint_records" in text


# ── 6. 코어 슬래브 — core_area_per_floor_m2 자동 산출 ─────
def test_core_slab_area_extracted():
    core = CoreSlab(
        id=30, comp_type=ComponentType.CORE_SLAB,
        position=np.array([0.0, 0.0, 3000.0]),
        rotation=0, anchor=0,
        dimensions={"width": 4000.0, "depth": 4500.0, "thickness": 180.0},
        floor_index=0,
    )
    out = build_scene_data([_mk_module(1, 0, 0, 0), core])
    s = out["summary"]
    # 4.0 × 4.5 = 18.0 m²
    assert s["core_area_per_floor_m2"] == pytest.approx(18.0, abs=0.1)


# ── 7. multi-type 모듈이 module_types 에 분리되어 들어감 ─
def test_module_types_multi_type_extraction():
    comps = [
        _mk_module(1, 0, 0, 0, w=3400, d=6000),
        _mk_module(2, 3400, 0, 0, w=3400, d=6000),
        _mk_module(3, 0, 6000, 0, w=3400, d=5000),
    ]
    out = build_scene_data(comps)
    types = out["module_types"]
    # 두 종류여야 함 (3.4×6.0 = 2개, 3.4×5.0 = 1개)
    assert len(types) == 2
    counts = sorted(t["n"] for t in types)
    assert counts == [1, 2]
