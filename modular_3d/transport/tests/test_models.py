"""Phase 1 단위 테스트 — models.py.

[검증 포인트]
- B-11 정정: Module.weight 가 4(w+l) 정답 식과 일치.
- B-15: 잘못된 truck_type/section_type/panel_kind 가 ValueError.
- A-1: Panel(kind="floor", wall_segments=N) 0/1/2/3/4 면 모두 생성 가능.
- A-1 일반화 weight: 다면 종속 floor 의 weight 가 합산 식과 일치.
- Truck 신규 필드(curb_weight_kg, trailer_length_mm, active) 기본값/검증.
- WallSegment 검증: side 범위, 변 길이 초과 시 ValueError.
"""
from __future__ import annotations

import pytest

from modular_3d.transport.models import (
    Section, WallSegment, Module, Panel, Truck, RoadClass, SpacingParams,
)


# ── 공용 픽스처 단면 ──────────────────────────────────────
SHS_200 = Section(
    name="SHS 200x200x8",
    section_type="SHS",
    width=200.0, height=200.0, thickness=8.0,
    weight_per_m=47.9,  # 단순 가정치
)
SHS_150 = Section(
    name="SHS 150x150x6",
    section_type="SHS",
    width=150.0, height=150.0, thickness=6.0,
    weight_per_m=27.0,
)


# ─────────────────────────── Section ───────────────────────────
def test_section_invalid_type_raises():
    with pytest.raises(ValueError, match="section_type"):
        Section(name="x", section_type="ZZZ", width=100, height=100, thickness=5, weight_per_m=10)


def test_section_negative_width_raises():
    with pytest.raises(ValueError):
        Section(name="x", section_type="SHS", width=-1, height=100, thickness=5, weight_per_m=10)


# ─────────────────────────── Module (B-11) ───────────────────────────
def test_module_weight_b11_correct_formula():
    """Module.weight 가 4(w+l) 정답 식과 일치하는지 검증 (B-11)."""
    w, l, h = 3000.0, 6000.0, 3400.0
    m = Module(
        name="m1", width=w, length=l, height=h,
        column_section=SHS_200, beam_section=SHS_150,
        extra_weight_kg=0.0,
    )
    col_m = (4 * h) / 1000.0
    beam_m = (4 * (w + l)) / 1000.0  # 정답: 8보 총 길이 = 4(w+l)
    expected = col_m * SHS_200.weight_per_m + beam_m * SHS_150.weight_per_m
    assert m.weight == pytest.approx(expected, rel=1e-9)


def test_module_weight_not_double_of_original_buggy():
    """원본의 2배 오버 식 결과와 정답이 다르다는 것을 명시 검증."""
    w, l, h = 3000.0, 6000.0, 3400.0
    m = Module(name="m", width=w, length=l, height=h,
               column_section=SHS_200, beam_section=SHS_150)
    col_m = (4 * h) / 1000.0
    bugged_beam_m = (4 * (w + l) * 2) / 1000.0   # 원본 식
    bugged = col_m * SHS_200.weight_per_m + bugged_beam_m * SHS_150.weight_per_m
    assert m.weight != pytest.approx(bugged)
    # 차이는 beam 한 세트 분량
    delta = bugged - m.weight
    assert delta == pytest.approx((4 * (w + l) / 1000.0) * SHS_150.weight_per_m)


def test_module_extra_weight_negative_raises():
    with pytest.raises(ValueError):
        Module(name="m", width=1000, length=1000, height=1000,
               column_section=SHS_200, beam_section=SHS_150,
               extra_weight_kg=-1)


def test_module_is_wide():
    m_narrow = Module(name="n", width=3000, length=6000, height=3400,
                      column_section=SHS_200, beam_section=SHS_150)
    m_wide = Module(name="w", width=3001, length=6000, height=3400,
                    column_section=SHS_200, beam_section=SHS_150)
    assert m_narrow.is_wide() is False
    assert m_wide.is_wide() is True


# ─────────────────────────── Panel (A-1) ───────────────────────────
def test_panel_pure_floor_weight():
    """순수 floor (wall_segments 없음) — 둘레 보 4개 기준."""
    p = Panel(name="f", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS_150)
    expected = (2 * (3000 + 6000) / 1000.0) * SHS_150.weight_per_m
    assert p.weight == pytest.approx(expected)


def test_panel_floor_with_one_wall_segment_lshape():
    """floor + wall_segment 1개 = L자 일반화. weight 가 둘레보 + 세그먼트 합산."""
    seg = WallSegment(
        side=0, start_offset_mm=0, length_mm=6000.0, height_mm=3000.0,
        thickness_mm=200.0,
        column_section=SHS_200, beam_section=SHS_150,
    )
    p = Panel(name="L", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS_150, wall_segments=(seg,))
    expected_floor = (2 * (3000 + 6000) / 1000.0) * SHS_150.weight_per_m
    expected_seg = (6000 / 1000.0) * SHS_150.weight_per_m + (2 * 3000 / 1000.0) * SHS_200.weight_per_m
    assert p.weight == pytest.approx(expected_floor + expected_seg)


def test_panel_floor_with_three_segments_3face():
    """3면 종속 floor. 4면 종속도 가능하지만 3면 검증."""
    segs = tuple(
        WallSegment(side=s, start_offset_mm=0,
                    length_mm=(6000.0 if s in (0, 2) else 3000.0),
                    height_mm=3000.0, thickness_mm=200.0,
                    column_section=SHS_200, beam_section=SHS_150)
        for s in (0, 1, 2)
    )
    p = Panel(name="C3", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS_150, wall_segments=segs)
    expected_floor = (2 * (3000 + 6000) / 1000.0) * SHS_150.weight_per_m
    expected_segs = 0.0
    for s in segs:
        expected_segs += (s.length_mm / 1000.0) * SHS_150.weight_per_m
        expected_segs += (2 * s.height_mm / 1000.0) * SHS_200.weight_per_m
    assert p.weight == pytest.approx(expected_floor + expected_segs)


def test_panel_wall_independent():
    p = Panel(name="w", kind="wall", width=3000, length=6000, thickness=200,
              beam_section=SHS_150, column_section=SHS_200, wall_height=3000)
    # 원본 wall 식: beam 2L + col 2W
    expected = ((2 * 6000) / 1000.0) * SHS_150.weight_per_m + ((2 * 3000) / 1000.0) * SHS_200.weight_per_m
    assert p.weight == pytest.approx(expected)


def test_panel_wall_with_segments_raises():
    seg = WallSegment(side=0, start_offset_mm=0, length_mm=1000, height_mm=1000,
                      thickness_mm=200, column_section=SHS_200, beam_section=SHS_150)
    with pytest.raises(ValueError, match="kind=wall"):
        Panel(name="w", kind="wall", width=3000, length=6000, thickness=200,
              beam_section=SHS_150, column_section=SHS_200, wall_segments=(seg,))


def test_panel_invalid_kind_raises():
    with pytest.raises(ValueError, match="kind"):
        Panel(name="x", kind="zzz", width=1000, length=1000, thickness=100,
              beam_section=SHS_150)


def test_panel_wall_without_column_section_raises():
    with pytest.raises(ValueError, match="column_section"):
        Panel(name="w", kind="wall", width=3000, length=6000, thickness=200,
              beam_section=SHS_150)


def test_panel_segment_exceeds_side_length_raises():
    seg = WallSegment(side=0, start_offset_mm=4000, length_mm=3000, height_mm=3000,
                      thickness_mm=200, column_section=SHS_200, beam_section=SHS_150)
    # side=0 변 길이 = length(=6000). 4000+3000=7000 > 6000 → 에러
    with pytest.raises(ValueError, match="초과"):
        Panel(name="f", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS_150, wall_segments=(seg,))


# ─────────────────────────── WallSegment ───────────────────────────
def test_wall_segment_invalid_side_raises():
    with pytest.raises(ValueError, match="side"):
        WallSegment(side=4, start_offset_mm=0, length_mm=1000, height_mm=1000,
                    thickness_mm=200, column_section=SHS_200, beam_section=SHS_150)


def test_wall_segment_negative_offset_raises():
    with pytest.raises(ValueError):
        WallSegment(side=0, start_offset_mm=-1, length_mm=1000, height_mm=1000,
                    thickness_mm=200, column_section=SHS_200, beam_section=SHS_150)


# ─────────────────────────── Truck (B-15·B-1·B-12·B-4) ───────────────────────────
def test_truck_basic_construction_with_new_fields():
    t = Truck(name="t", truck_type="lowbed",
              max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
              curb_weight_kg=14000, trailer_length_mm=13000, active=True)
    assert t.curb_weight_kg == 14000
    assert t.trailer_length_mm == 13000
    assert t.active is True


def test_truck_invalid_type_raises():
    with pytest.raises(ValueError, match="truck_type"):
        Truck(name="t", truck_type="rocket",
              max_length=12000, max_width=3000, max_height=4500, max_weight=24000)


def test_truck_negative_curb_weight_raises():
    with pytest.raises(ValueError, match="curb_weight_kg"):
        Truck(name="t", truck_type="lowbed",
              max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
              curb_weight_kg=-1)


def test_truck_default_active_true():
    t = Truck(name="t", truck_type="lowbed",
              max_length=12000, max_width=3000, max_height=4500, max_weight=24000)
    assert t.active is True
    assert t.curb_weight_kg == 0.0
    assert t.trailer_length_mm == 0.0


# ─────────────────────────── RoadClass / SpacingParams ───────────────────────────
def test_road_invalid_dimension_raises():
    with pytest.raises(ValueError):
        RoadClass(name="r", max_length=-1, max_width=3000, max_height=4500, max_weight=40000)


def test_spacing_defaults():
    s = SpacingParams()
    assert s.panel_gap_mm == 100.0
    assert s.truck_edge_clearance_mm == 200.0
    assert s.lshape_stack_gap_mm == 100.0


def test_spacing_negative_raises():
    with pytest.raises(ValueError):
        SpacingParams(panel_gap_mm=-1)
