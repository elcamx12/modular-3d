"""Phase 4 단위 테스트 — visualizer.

[검증 — 시각 회귀는 수동, 본 테스트는 figure 생성 가능 여부만]
- 모듈/순수 floor/벽 패널/종속 패널 4 시나리오 각각 draw_top_view/draw_rear_view 호출 후 Figure 반환.
- A-1 일반화: wall_segments 1/2/3 면 모두 figure 생성 + 도형 수 증가 확인.
- B-16 화물 초과: 한도 초과 시 overload 색·텍스트 포함.
- 빈 회차도 figure 반환.
"""
from __future__ import annotations

import pytest
import plotly.graph_objects as go

from modular_3d.transport.models import (
    Section, Module, Panel, Truck, SpacingParams, WallSegment,
)
from modular_3d.transport.packer import Trip
from modular_3d.transport.visualizer import draw_top_view, draw_rear_view


SHS = Section(name="SHS200x8", section_type="SHS",
              width=200, height=200, thickness=8, weight_per_m=47.9)
TR = Truck(name="저상24t", truck_type="lowbed",
           max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
           vehicle_height_offset=700, curb_weight_kg=14000, active=True)
SP = SpacingParams()


def _module_trip():
    m = Module(name="m1", width=3000, length=6000, height=3400,
               column_section=SHS, beam_section=SHS, extra_weight_kg=5000)
    return Trip(trip_no=1, truck=TR, items=[m],
                panels_per_row=1, n_layers=1,
                used_length_mm=6000, usable_length_mm=11600)


def _floor_trip():
    p = Panel(name="F1", kind="floor", width=2800, length=4000, thickness=150,
              beam_section=SHS, extra_weight_kg=1600)
    return Trip(trip_no=2, truck=TR, items=[p],
                panels_per_row=1, n_layers=1,
                used_length_mm=4000, usable_length_mm=11600)


def _wall_trip():
    p = Panel(name="W1", kind="wall", width=3000, length=6000, thickness=200,
              beam_section=SHS, column_section=SHS, wall_height=3000)
    return Trip(trip_no=3, truck=TR, items=[p],
                panels_per_row=1, n_layers=1,
                used_length_mm=6000, usable_length_mm=11600)


def _dependent_trip(n_segs=1):
    sides = (0, 2, 1, 3)[:n_segs]
    segs = tuple(
        WallSegment(side=s, start_offset_mm=0,
                    length_mm=(6000.0 if s in (0, 2) else 2800.0),
                    height_mm=3000, thickness_mm=200,
                    column_section=SHS, beam_section=SHS)
        for s in sides
    )
    p = Panel(name=f"D{n_segs}면", kind="floor",
              width=2800, length=6000, thickness=150,
              beam_section=SHS, wall_segments=segs, extra_weight_kg=2400)
    return Trip(trip_no=4, truck=TR, items=[p], stacked_items=[None],
                panels_per_row=1, n_layers=1,
                used_length_mm=6000, usable_length_mm=11600)


# ── 기본 figure 반환 ────────────────────────────────────
def test_top_view_module():
    fig = draw_top_view(_module_trip(), TR, SP)
    assert isinstance(fig, go.Figure)
    assert len(fig.layout.shapes) >= 1


def test_rear_view_module():
    fig = draw_rear_view(_module_trip(), TR, SP)
    assert isinstance(fig, go.Figure)
    # 차체 + 적재함 + 모듈 = 최소 3 shape
    assert len(fig.layout.shapes) >= 3


def test_top_view_floor():
    fig = draw_top_view(_floor_trip(), TR, SP)
    assert isinstance(fig, go.Figure)


def test_rear_view_floor():
    fig = draw_rear_view(_floor_trip(), TR, SP)
    assert isinstance(fig, go.Figure)


def test_top_view_wall():
    fig = draw_top_view(_wall_trip(), TR, SP)
    assert isinstance(fig, go.Figure)


def test_rear_view_wall():
    fig = draw_rear_view(_wall_trip(), TR, SP)
    assert isinstance(fig, go.Figure)


# ── A-1 wall_segments 1/2/3 면 ────────────────────────────
@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_top_view_dependent_n_faces(n):
    fig = draw_top_view(_dependent_trip(n), TR, SP)
    assert isinstance(fig, go.Figure)
    # 종속 패널 1매 + wall_segments n개 = 박스 수가 n과 함께 늘어남
    n_rects = sum(1 for s in fig.layout.shapes if s.type == "rect")
    # 트럭 외곽(1) + edge zone(2) + 바닥판(1) + wall_segs(n) 최소
    assert n_rects >= 4 + n - 1


def test_rear_view_dependent_with_stack():
    seg = WallSegment(side=0, start_offset_mm=0, length_mm=6000,
                      height_mm=3000, thickness_mm=200,
                      column_section=SHS, beam_section=SHS)
    p = Panel(name="L", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(seg,))
    stk = Panel(name="S", kind="floor", width=1500, length=4000, thickness=150,
                beam_section=SHS)
    trip = Trip(trip_no=5, truck=TR, items=[p], stacked_items=[stk],
                panels_per_row=1, n_layers=2,
                used_length_mm=6000, usable_length_mm=11600)
    fig = draw_rear_view(trip, TR, SP)
    assert isinstance(fig, go.Figure)
    # 적층 박스 포함 → shape ≥ 5 (차체+적재함+바닥+벽+적층)
    assert len(fig.layout.shapes) >= 5


# ── B-16 overload 경고 ──────────────────────────────────
def test_overload_marks_outline_red():
    """화물 무게가 트럭 한도 초과 시 경고 텍스트 + 외곽 빨강."""
    p = Panel(name="Heavy", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, extra_weight_kg=30000)
    trip = Trip(trip_no=99, truck=TR, items=[p], panels_per_row=1, n_layers=1,
                used_length_mm=6000, usable_length_mm=11600)
    fig = draw_top_view(trip, TR, SP)
    # 트럭 외곽 색 빨강
    outline = fig.layout.shapes[0]
    assert outline.line.color.lower() in ("#cc0000", "cc0000")
    # overload annotation 텍스트
    texts = [a.text for a in fig.layout.annotations if a.text]
    assert any("중량" in t and "kg" in t for t in texts)


# ── 빈 회차 ─────────────────────────────────────────────
def test_empty_trip_returns_figure():
    trip = Trip(trip_no=0, truck=TR, items=[],
                panels_per_row=0, n_layers=0,
                used_length_mm=0, usable_length_mm=11600)
    fig_t = draw_top_view(trip, TR, SP)
    fig_r = draw_rear_view(trip, TR, SP)
    assert isinstance(fig_t, go.Figure)
    assert isinstance(fig_r, go.Figure)


# ── truck / sp 인자 생략 시 trip.truck / 기본값 사용 ──────
def test_omitted_truck_and_sp():
    fig = draw_top_view(_module_trip())
    assert isinstance(fig, go.Figure)
