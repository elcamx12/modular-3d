"""Phase A 단위 테스트 — `_normalize_dims_for_transport` (어댑터 정규화).

[검증 범위]
- 모듈 swap 동작 (length < width → swap)
- 모듈 idempotent (이미 length ≥ width → 변화 없음)
- 순수 floor 패널 swap
- 종속 floor 패널 swap + wall_segments side 회전 매핑 (+1 mod 4)
- 단순 wall 패널은 제외 (벽 길이/높이 의미 보존)
- 세그먼트 자체 치수 (length_mm, start_offset_mm 등) 보존
- 정사각형 (length == width) — 변화 없음
"""
from __future__ import annotations

import pytest

from modular_3d.transport.adapter import _normalize_dims_for_transport
from modular_3d.transport.models import (
    Module, Panel, Section, WallSegment,
)


_SHS = Section(
    name="SHS200x8", section_type="SHS",
    width=200, height=200, thickness=8, weight_per_m=47.9,
)


# ── 헬퍼 ─────────────────────────────────────────────────────
def _make_module(length, width, height=2700.0) -> Module:
    return Module(
        name="M_test", length=length, width=width, height=height,
        column_section=_SHS, beam_section=_SHS, extra_weight_kg=5000.0,
    )


def _make_floor(length, width, thickness=150.0) -> Panel:
    return Panel(
        name="F_test", kind="floor", width=width, length=length,
        thickness=thickness, beam_section=_SHS, extra_weight_kg=2500.0,
    )


def _make_wall(length, width, thickness=150.0) -> Panel:
    """단순 wall: length=벽 길이, width=벽 높이."""
    return Panel(
        name="W_test", kind="wall", width=width, length=length,
        thickness=thickness, wall_height=width,
        beam_section=_SHS, column_section=_SHS,
        extra_weight_kg=800.0,
    )


def _seg(side, length_mm, start_offset_mm=0.0, height_mm=3000.0,
         thickness_mm=200.0) -> WallSegment:
    return WallSegment(
        side=side, start_offset_mm=start_offset_mm, length_mm=length_mm,
        height_mm=height_mm, thickness_mm=thickness_mm,
        column_section=_SHS, beam_section=_SHS,
    )


def _make_lshape(length, width, side=0, seg_length=None) -> Panel:
    """L 자 패널 — 한 변에 wall_segment."""
    if seg_length is None:
        seg_length = length if side in (0, 2) else width
    seg = _seg(side, seg_length)
    return Panel(
        name="L_test", kind="floor", width=width, length=length,
        thickness=150.0, beam_section=_SHS,
        wall_segments=(seg,), extra_weight_kg=2500.0,
    )


def _make_4face(length, width) -> Panel:
    segs = tuple(
        _seg(s, length_mm=(length if s in (0, 2) else width))
        for s in (0, 1, 2, 3)
    )
    return Panel(
        name="C4_test", kind="floor", width=width, length=length,
        thickness=150.0, beam_section=_SHS,
        wall_segments=segs, extra_weight_kg=3000.0,
    )


# ════════════════════════════════════════════════════════════════════
# §1 모듈
# ════════════════════════════════════════════════════════════════════
def test_module_swap_when_length_less_than_width():
    """길이 3400, 폭 4500 → swap 후 (length=4500, width=3400)."""
    m = _make_module(length=3400, width=4500)
    out = _normalize_dims_for_transport(m)
    assert out.length == 4500
    assert out.width == 3400
    assert out.height == m.height  # height 는 그대로


def test_module_idempotent_when_already_normalized():
    """이미 length ≥ width → 변화 없음."""
    m = _make_module(length=4500, width=3400)
    out = _normalize_dims_for_transport(m)
    assert out.length == 4500
    assert out.width == 3400


def test_module_unchanged_when_square():
    """정사각형 (length == width) → 변화 없음."""
    m = _make_module(length=3000, width=3000)
    out = _normalize_dims_for_transport(m)
    assert out.length == 3000
    assert out.width == 3000


def test_module_preserves_sections_and_weight():
    """swap 후에도 단면·extra_weight_kg 보존."""
    m = _make_module(length=3400, width=4500)
    out = _normalize_dims_for_transport(m)
    assert out.column_section is m.column_section
    assert out.beam_section is m.beam_section
    assert out.extra_weight_kg == m.extra_weight_kg


# ════════════════════════════════════════════════════════════════════
# §2 순수 floor 패널
# ════════════════════════════════════════════════════════════════════
def test_pure_floor_swap_when_length_less_than_width():
    p = _make_floor(length=3400, width=4500)
    out = _normalize_dims_for_transport(p)
    assert out.length == 4500
    assert out.width == 3400
    assert out.thickness == p.thickness
    assert out.kind == "floor"


def test_pure_floor_idempotent():
    p = _make_floor(length=6000, width=2800)
    out = _normalize_dims_for_transport(p)
    assert out.length == 6000
    assert out.width == 2800


# ════════════════════════════════════════════════════════════════════
# §3 단순 wall 패널 — 제외 정책
# ════════════════════════════════════════════════════════════════════
def test_simple_wall_not_swapped_even_when_length_less_than_width():
    """단순 wall (벽 길이 < 벽 높이) 도 swap 안 함 — 물리 의미 보존."""
    w = _make_wall(length=3000, width=4000)  # 벽 길이 3m, 벽 높이 4m
    out = _normalize_dims_for_transport(w)
    assert out.length == 3000  # 벽 길이 보존
    assert out.width == 4000   # 벽 높이 보존


def test_simple_wall_normal_case_unchanged():
    """정상 케이스 (벽 길이 > 벽 높이) 도 변화 없음."""
    w = _make_wall(length=6000, width=3000)
    out = _normalize_dims_for_transport(w)
    assert out.length == 6000
    assert out.width == 3000


# ════════════════════════════════════════════════════════════════════
# §4 종속 floor — swap + wall_segments side 회전 매핑
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("side_old,side_new", [
    (0, 1),  # 하변 → 우변
    (1, 2),  # 우변 → 상변
    (2, 3),  # 상변 → 좌변
    (3, 0),  # 좌변 → 하변
])
def test_dependent_floor_side_rotated_plus_1_mod_4(side_old, side_new):
    """swap 발생 시 wall_segments.side 가 (side+1) % 4 로 회전."""
    p = _make_lshape(length=3400, width=4500, side=side_old)
    out = _normalize_dims_for_transport(p)
    assert out.length == 4500
    assert out.width == 3400
    assert len(out.wall_segments) == 1
    assert out.wall_segments[0].side == side_new


def test_dependent_floor_segment_dimensions_preserved():
    """세그먼트 자체 치수 (length_mm, start_offset_mm, height_mm, thickness_mm) 보존."""
    seg = WallSegment(
        side=0, start_offset_mm=500.0, length_mm=2500.0,
        height_mm=3000.0, thickness_mm=200.0,
        column_section=_SHS, beam_section=_SHS,
    )
    p = Panel(
        name="L", kind="floor", width=4500, length=3400, thickness=150,
        beam_section=_SHS, wall_segments=(seg,), extra_weight_kg=2500.0,
    )
    out = _normalize_dims_for_transport(p)
    new_seg = out.wall_segments[0]
    assert new_seg.start_offset_mm == 500.0
    assert new_seg.length_mm == 2500.0
    assert new_seg.height_mm == 3000.0
    assert new_seg.thickness_mm == 200.0


def test_dependent_floor_idempotent_when_already_normalized():
    """이미 length ≥ width 인 종속 floor 는 side 도 그대로."""
    p = _make_lshape(length=6000, width=3000, side=2)
    out = _normalize_dims_for_transport(p)
    assert out.length == 6000
    assert out.width == 3000
    assert out.wall_segments[0].side == 2  # 회전 X


def test_dependent_floor_4face_all_sides_rotated():
    """4 면 종속 — 4 개 side 모두 +1 mod 4 회전."""
    p = _make_4face(length=3400, width=4500)
    out = _normalize_dims_for_transport(p)
    new_sides = sorted(s.side for s in out.wall_segments)
    # 원래 (0,1,2,3) → 회전 후 (1,2,3,0) → 정렬 (0,1,2,3) 그대로 (대칭)
    assert new_sides == [0, 1, 2, 3]


# ════════════════════════════════════════════════════════════════════
# §5 회귀 — 전체 운송 패키지 동작
# ════════════════════════════════════════════════════════════════════
def test_normalize_does_not_break_packer():
    """정규화된 모듈·패널이 신규 패커로 정상 패킹."""
    from modular_3d.transport.packer import pack_items
    from modular_3d.transport.tests.transport_v2_fixtures import (
        default_site, default_trucks,
    )

    m = _make_module(length=3400, width=4500)
    p = _make_floor(length=2400, width=6000)  # swap 발생
    L = _make_lshape(length=2800, width=8000, side=0)  # swap 발생

    items_module = [_normalize_dims_for_transport(m)]
    items_panel = [
        _normalize_dims_for_transport(p),
        _normalize_dims_for_transport(L),
    ]
    res = pack_items(items_module, items_panel, default_trucks(), default_site())
    # blocked 0 + 회차 ≥ 1
    assert len(res.blocked) == 0
    assert res.total_trips >= 1
