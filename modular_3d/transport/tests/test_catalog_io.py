"""Phase 1 단위 테스트 — catalog_io.py.

[검증 포인트]
- 패키지 내장 trucks/roads 로드 + 항목 수 (5종/3종).
- A-frame 트럭이 active=False 인지 확인.
- active_only=True 필터가 4종만 반환.
- 프로젝트 오버라이드 save → load 라운드트립 (tmp_path 사용).
- 프로젝트 항목이 같은 name 의 내장 항목을 덮어쓰는지.
- validate_truck/validate_road 가 잘못된 dict 에 에러 메시지 반환.
"""
from __future__ import annotations

import json

import pytest

from modular_3d.transport import catalog_io as cio
from modular_3d.transport.models import Truck, RoadClass


# ─────────────────────────── 내장 로드 ───────────────────────────
def test_load_builtin_trucks_count():
    trucks = cio.load_builtin_trucks()
    assert len(trucks) == 5
    names = {t.name for t in trucks}
    assert any("저상" in n for n in names)
    assert any("광폭" in n for n in names)
    assert any("A-frame" in n for n in names)


def test_aframe_is_inactive_in_builtin():
    trucks = cio.load_builtin_trucks()
    a = [t for t in trucks if t.truck_type == "aframe"]
    assert len(a) == 1
    assert a[0].active is False


def test_load_all_trucks_active_filter():
    all_t = cio.load_all_trucks(project_root=None, active_only=False)
    active_t = cio.load_all_trucks(project_root=None, active_only=True)
    assert len(all_t) == 5
    assert len(active_t) == 4
    assert all(t.active for t in active_t)


def test_load_builtin_roads_count():
    roads = cio.load_builtin_roads()
    assert len(roads) == 3
    names = {r.name for r in roads}
    assert any("광로" in n for n in names)
    assert any("일반도로" in n for n in names)
    assert any("이면도로" in n for n in names)


# ─────────────────────────── 프로젝트 오버라이드 ───────────────────────────
def test_load_project_trucks_empty_when_no_dir(tmp_path):
    assert cio.load_project_trucks(tmp_path) == []


def test_save_then_load_project_truck_roundtrip(tmp_path):
    custom = Truck(
        name="사용자 정의 30톤", truck_type="lowbed",
        max_length=14000, max_width=3000, max_height=4500, max_weight=30000,
        curb_weight_kg=15000, trailer_length_mm=13000, active=True,
        note="사용자 추가",
    )
    saved_path = cio.save_project_truck(custom, project_root=tmp_path)
    assert saved_path.exists()
    loaded = cio.load_project_trucks(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].name == "사용자 정의 30톤"
    assert loaded[0].max_weight == 30000
    assert loaded[0].curb_weight_kg == 15000


def test_project_overrides_builtin(tmp_path):
    """프로젝트가 동일 name 의 내장 트럭을 덮어쓰는지."""
    # 내장 중 첫번째와 같은 name 으로 다른 max_weight 저장
    builtin = cio.load_builtin_trucks()
    target = builtin[0]
    override = Truck(
        name=target.name, truck_type=target.truck_type,
        max_length=target.max_length, max_width=target.max_width,
        max_height=target.max_height,
        max_weight=99999,  # 의도적 다른 값
        curb_weight_kg=target.curb_weight_kg,
        trailer_length_mm=target.trailer_length_mm,
        active=target.active,
    )
    cio.save_project_truck(override, project_root=tmp_path)
    merged = cio.load_all_trucks(project_root=tmp_path)
    # 같은 name 은 한 번만 등장
    same_name = [t for t in merged if t.name == target.name]
    assert len(same_name) == 1
    assert same_name[0].max_weight == 99999  # 오버라이드 값


def test_save_project_truck_replaces_existing(tmp_path):
    """같은 name 두번 저장 시 갱신만 되는지."""
    t1 = Truck(name="X", truck_type="lowbed",
               max_length=10000, max_width=3000, max_height=4500, max_weight=20000)
    t2 = Truck(name="X", truck_type="lowbed",
               max_length=12000, max_width=3000, max_height=4500, max_weight=22000)
    cio.save_project_truck(t1, project_root=tmp_path)
    cio.save_project_truck(t2, project_root=tmp_path)
    loaded = cio.load_project_trucks(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].max_length == 12000


def test_save_then_load_project_road_roundtrip(tmp_path):
    r = RoadClass(name="사용자 도로", max_length=20000, max_width=3500,
                  max_height=4500, max_weight=45000)
    cio.save_project_road(r, project_root=tmp_path)
    loaded = cio.load_project_roads(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].name == "사용자 도로"


# ─────────────────────────── validate ───────────────────────────
def test_validate_truck_ok():
    data = dict(name="ok", truck_type="lowbed",
                max_length=10000, max_width=3000, max_height=4500, max_weight=20000)
    assert cio.validate_truck(data) == []


def test_validate_truck_bad_type():
    data = dict(name="bad", truck_type="ufo",
                max_length=10000, max_width=3000, max_height=4500, max_weight=20000)
    errs = cio.validate_truck(data)
    assert errs and "truck_type" in errs[0]


def test_validate_road_bad_dim():
    data = dict(name="r", max_length=-1, max_width=3000, max_height=4500, max_weight=40000)
    errs = cio.validate_road(data)
    assert errs


# ─────────────────────────── JSON 파일 깨짐 에러 메시지 ───────────────────────────
def test_truck_json_with_invalid_item_reports_index(tmp_path):
    p = tmp_path / "transport_config"
    p.mkdir()
    bad = [
        {"name": "good", "truck_type": "lowbed",
         "max_length": 10000, "max_width": 3000, "max_height": 4500, "max_weight": 20000},
        {"name": "bad", "truck_type": "ZZZ",  # 잘못된 타입
         "max_length": 10000, "max_width": 3000, "max_height": 4500, "max_weight": 20000},
    ]
    (p / "trucks.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="#1"):
        cio.load_project_trucks(tmp_path)
