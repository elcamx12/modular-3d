"""Phase 4-B 단위 테스트 — CollisionGrid + 박스 헬퍼.

[검증 포인트]
- 그리드 셀 인덱스 계산이 음수에서도 floor 작동
- insert / query_near / remove 정확성
- margin 확장 시 100mm 갭 검사 정상
- boxes_gap_ok 의 *축 분리* 정책 정확
"""
from __future__ import annotations

import pytest

from ..collision import (
    CELL_MM, Box, CollisionGrid,
    boxes_gap_ok, boxes_min_distance, boxes_overlap,
    _floor_div, _cell_range_for_box,
)


# ── 셀 인덱스 계산 ────────────────────────────────────────────
def test_floor_div_positive():
    assert _floor_div(0, 100) == 0
    assert _floor_div(50, 100) == 0
    assert _floor_div(100, 100) == 1
    assert _floor_div(150, 100) == 1


def test_floor_div_negative():
    """음수에서 표준 // 처럼 floor 동작."""
    assert _floor_div(-1, 100) == -1
    assert _floor_div(-50, 100) == -1
    assert _floor_div(-100, 100) == -1
    assert _floor_div(-101, 100) == -2


def test_cell_range_basic():
    """0~100 박스는 셀 (0,) 한 칸만 점유 (max 면이 셀 경계)."""
    box: Box = (0, 0, 0, 100, 100, 100)
    rx, ry, rz = _cell_range_for_box(box)
    assert list(rx) == [0]
    assert list(ry) == [0]
    assert list(rz) == [0]


def test_cell_range_spanning():
    """0~250 박스는 셀 0, 1, 2 = 3 칸 점유."""
    box: Box = (0, 0, 0, 250, 100, 100)
    rx, _, _ = _cell_range_for_box(box)
    assert list(rx) == [0, 1, 2]


# ── 박스 겹침 / 거리 ─────────────────────────────────────────
def test_boxes_overlap_yes():
    b1: Box = (0, 0, 0, 100, 100, 100)
    b2: Box = (50, 50, 50, 150, 150, 150)
    assert boxes_overlap(b1, b2) is True


def test_boxes_overlap_touch():
    """경계 접촉은 겹침 X."""
    b1: Box = (0, 0, 0, 100, 100, 100)
    b2: Box = (100, 0, 0, 200, 100, 100)
    assert boxes_overlap(b1, b2) is False


def test_boxes_min_distance_apart():
    b1: Box = (0, 0, 0, 100, 100, 100)
    b2: Box = (200, 0, 0, 300, 100, 100)
    assert boxes_min_distance(b1, b2) == pytest.approx(100.0)


def test_boxes_min_distance_diagonal():
    """대각 분리 — 유클리드 거리."""
    b1: Box = (0, 0, 0, 100, 100, 100)
    b2: Box = (300, 400, 0, 400, 500, 100)
    # dx=200, dy=300, dz=0 → sqrt(40000+90000) = sqrt(130000) ≈ 360.555
    assert boxes_min_distance(b1, b2) == pytest.approx(360.5551, abs=0.01)


def test_boxes_gap_ok_axis_separation():
    """축 분리 정책 — 한 축이라도 100 이상이면 OK."""
    b1: Box = (0, 0, 0, 100, 100, 100)
    # x 방향만 200 분리 — 축 분리 OK
    b2: Box = (300, 0, 0, 400, 100, 100)
    assert boxes_gap_ok(b1, b2, 100) is True


def test_boxes_gap_ok_insufficient():
    b1: Box = (0, 0, 0, 100, 100, 100)
    # x 방향 50 분리 — 축 분리 100 미만 → 모든 축 NG
    b2: Box = (150, 0, 0, 250, 100, 100)
    assert boxes_gap_ok(b1, b2, 100) is False


# ── CollisionGrid ────────────────────────────────────────────
def test_grid_insert_and_query_basic():
    g = CollisionGrid()
    b: Box = (0, 0, 0, 100, 100, 100)
    g.insert([b], owner_id="A")
    # 같은 박스 위치 조회 → A 검출
    found = g.query_near((50, 50, 50, 70, 70, 70))
    assert "A" in found


def test_grid_query_with_margin():
    """margin=100 으로 갭 검사."""
    g = CollisionGrid()
    g.insert([(0, 0, 0, 100, 100, 100)], owner_id="A")
    # B 박스는 150~250 위치. margin=0 이면 A 와 안 겹치는 셀.
    # margin=100 이면 B 의 확장 박스가 50~350 으로 A 와 겹침.
    near_no_margin = g.query_near((150, 0, 0, 250, 100, 100), margin_mm=0)
    near_with_margin = g.query_near((150, 0, 0, 250, 100, 100), margin_mm=100)
    # 150 ~ 250 박스는 셀 1, 2 점유. A 는 셀 0 점유. margin=0 이면 A 셀과 안 닿음.
    assert "A" not in near_no_margin
    # margin=100 이면 B 의 셀 0 (50-100 부분) 도 포함 → A 검출
    assert "A" in near_with_margin


def test_grid_remove():
    g = CollisionGrid()
    g.insert([(0, 0, 0, 100, 100, 100)], owner_id="A")
    g.insert([(200, 0, 0, 300, 100, 100)], owner_id="B")
    assert "A" in g.all_owners()
    g.remove("A")
    assert "A" not in g.all_owners()
    assert "B" in g.all_owners()
    # A 위치 조회 → A 없음
    found = g.query_near((0, 0, 0, 50, 50, 50))
    assert "A" not in found


def test_grid_multi_boxes_per_owner():
    """한 owner 가 여러 박스 (예: 컴포넌트의 부재 여러 개)."""
    g = CollisionGrid()
    b1: Box = (0, 0, 0, 100, 100, 100)
    b2: Box = (500, 0, 0, 600, 100, 100)  # 멀리 떨어진 두 번째 박스
    g.insert([b1, b2], owner_id="A")
    # 첫 박스 위치
    assert "A" in g.query_near((50, 50, 50, 70, 70, 70))
    # 두 번째 박스 위치 (먼 곳)
    assert "A" in g.query_near((520, 50, 50, 580, 70, 70))
    # 중간 빈 공간
    assert "A" not in g.query_near((250, 50, 50, 350, 70, 70))


def test_grid_boxes_of():
    g = CollisionGrid()
    b1: Box = (0, 0, 0, 100, 100, 100)
    b2: Box = (200, 0, 0, 300, 100, 100)
    g.insert([b1, b2], owner_id="A")
    out = g.boxes_of("A")
    assert b1 in out
    assert b2 in out
    assert len(out) == 2


def test_grid_negative_coords():
    """음수 좌표 (캔틸이 부모 -x 방향 돌출 등) 도 정상."""
    g = CollisionGrid()
    g.insert([(-200, 0, 0, -50, 100, 100)], owner_id="cant")
    found = g.query_near((-150, 0, 0, -100, 50, 50))
    assert "cant" in found
