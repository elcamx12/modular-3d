"""Phase 2 단위 테스트 — limits.py.

[검증 포인트]
- 기본 모드(strict_*=False) 가 원본 정책과 동일 (단순화 유지).
- strict_weight=True → GVW(curb+화물) 가 도로 한도 초과 시 reasons 발생.
- strict_length=True → 화물 + trailer_length 가 도로 한도 초과 시 reasons 발생.
- 패널 화물 높이: wall_segments 있으면 thickness + max(seg.height) 적용 (B-14).
- notes 가 단순화 정책 사용 시 표시됨.
"""
from __future__ import annotations

import pytest

from modular_3d.transport.limits import can_carry
from modular_3d.transport.models import (
    Section, Module, Panel, Truck, RoadClass, WallSegment,
)


# ── 픽스처 ────────────────────────────────────────────────
SHS = Section(name="SHS200x8", section_type="SHS",
              width=200, height=200, thickness=8, weight_per_m=47.9)

TR_24 = Truck(
    name="저상24t", truck_type="lowbed",
    max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
    vehicle_height_offset=700,
    curb_weight_kg=14000, trailer_length_mm=13000, active=True,
)
RD_GO = RoadClass(name="광로", max_length=19000, max_width=3500,
                  max_height=4500, max_weight=40000)
RD_NORMAL = RoadClass(name="일반도로", max_length=16500, max_width=3300,
                      max_height=4200, max_weight=25000)
RD_NARROW = RoadClass(name="이면도로", max_length=12000, max_width=3200,
                      max_height=4000, max_weight=15000)


# ── 기본 모드 (단순화 유지) ────────────────────────────────
def test_module_basic_pass():
    m = Module(name="m", width=3000, length=6000, height=3400,
               column_section=SHS, beam_section=SHS, extra_weight_kg=5000)
    r = can_carry(m, TR_24, RD_GO)
    assert r.ok
    assert r.wide_check is False


def test_module_basic_weight_only_compares_cargo():
    """기본 모드 — 화물만 비교. 차체 14t + 화물 23t = 37t 인데 화물만 23t<24t 으로 통과."""
    heavy = Module(name="m", width=3000, length=6000, height=3400,
                   column_section=SHS, beam_section=SHS, extra_weight_kg=23000 - _frame_kg())
    r = can_carry(heavy, TR_24, RD_NARROW)  # 이면도로 max_weight=15t
    # 화물 23t > 트럭 24t 는 통과지만 도로 15t 초과 → 화물만 비교해도 fail
    assert not r.ok
    # notes 에 단순화 메모 있어야
    assert any("화물 중량만" in n for n in r.notes)


def _frame_kg() -> float:
    """SHS 단면으로 6×3×3.4 모듈 프레임 무게."""
    h = 3.4
    w_l = 3.0 + 6.0
    return (4 * h + 4 * w_l) * 47.9


# ── [D1] 기본 모드 한도 위반 — 원본 test_can_carry 8케이스 시나리오 이전 ──
# 원본 테스트는 구버전 Module(weight= 직접 인자) API 라 stale → 우리 단면 기반
# API 로 동일 시나리오(길이/폭/높이/중량/도로/패널/광폭)를 직접 검증.
def _mod(**kw):
    base = dict(name="m", width=3000, length=6000, height=3000,
                column_section=SHS, beam_section=SHS, extra_weight_kg=3000)
    base.update(kw)
    return Module(**base)


def test_basic_length_violation():
    r = can_carry(_mod(length=20000), TR_24, RD_GO)
    assert not r.ok
    assert any("길이" in s for s in r.reasons)


def test_basic_width_violation_sets_wide_check():
    r = can_carry(_mod(width=3900), TR_24, RD_GO)
    assert not r.ok
    assert any("폭" in s for s in r.reasons)
    assert r.wide_check  # 3.0m 초과 → 광폭 검토 플래그 ON


def test_basic_height_violation():
    # 화물 4000 + 차체 700 = 4700 > 4500
    r = can_carry(_mod(height=4000), TR_24, RD_GO)
    assert not r.ok
    assert any("높이" in s for s in r.reasons)


def test_basic_weight_violation():
    # extra_weight 를 키워 화물이 트럭 24t 초과
    r = can_carry(_mod(extra_weight_kg=30000), TR_24, RD_GO)
    assert not r.ok
    assert any("중량" in s for s in r.reasons)


def test_basic_road_class_length_limit():
    # 이면도로(max_length 12000) — 9m 모듈 OK, 12.1m 모듈 NG
    assert can_carry(_mod(length=9000), TR_24, RD_NARROW).ok
    assert not can_carry(_mod(length=12100), TR_24, RD_NARROW).ok


def test_basic_panel_pass():
    p = Panel(name="p", kind="floor", width=3000, length=6000, thickness=200,
              beam_section=SHS, extra_weight_kg=1500)
    assert can_carry(p, TR_24, RD_GO).ok


def test_module_strict_weight_gvw_check():
    """strict_weight=True → GVW 정밀 비교."""
    m = Module(name="m", width=3000, length=6000, height=3400,
               column_section=SHS, beam_section=SHS, extra_weight_kg=10000)
    # GVW = 14000 + (frame + 10000)
    cargo = _frame_kg() + 10000
    gvw = 14000 + cargo
    # 광로 max_weight=40t. 만약 gvw < 40t 통과, > 40t fail
    r = can_carry(m, TR_24, RD_GO, strict_weight=True)
    if gvw <= 40000:
        assert r.ok
    else:
        assert any("GVW" in s for s in r.reasons)


def test_strict_length_trailer_length_summed():
    """strict_length=True 면 trailer_length 가 화물에 더해진다."""
    m = Module(name="m", width=3000, length=10000, height=3400,
               column_section=SHS, beam_section=SHS)
    # 화물 10000 + 트레일러 13000 = 23000 > 광로 19000 → fail
    r = can_carry(m, TR_24, RD_GO, strict_length=True)
    assert not r.ok
    assert any("전장 초과" in s for s in r.reasons)


def test_strict_length_basic_passes():
    """기본 모드면 화물 길이만 비교 → 통과."""
    m = Module(name="m", width=3000, length=10000, height=3400,
               column_section=SHS, beam_section=SHS)
    r = can_carry(m, TR_24, RD_GO)
    assert r.ok
    assert any("트레일러 길이 미포함" in n for n in r.notes)


# ── Panel 화물 높이 (wall_segments) ─────────────────────────
def test_panel_with_wall_segments_height_uses_max_seg():
    """B-14 정밀도: wall_segments 가 있으면 thickness + max(seg.height) 가 높이."""
    seg_tall = WallSegment(side=0, start_offset_mm=0, length_mm=6000,
                           height_mm=3000, thickness_mm=200,
                           column_section=SHS, beam_section=SHS)
    seg_short = WallSegment(side=2, start_offset_mm=0, length_mm=6000,
                            height_mm=1500, thickness_mm=200,
                            column_section=SHS, beam_section=SHS)
    p = Panel(name="C", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(seg_tall, seg_short))
    # 화물 높이 = 150 + 3000 = 3150. 외측 = 3150 + 700 = 3850 < 4500 → 광로 통과
    r = can_carry(p, TR_24, RD_GO)
    assert r.ok
    # 이면도로(max_height=4000) 도 통과
    r2 = can_carry(p, TR_24, RD_NARROW)
    assert r2.ok


def test_panel_pure_floor_height_uses_thickness():
    """순수 floor (wall_segments 없음) — 높이 = thickness 만."""
    p = Panel(name="F", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS)
    r = can_carry(p, TR_24, RD_GO)
    assert r.ok


# ── 광폭 모듈 ─────────────────────────────────────────────
def test_wide_module_flag():
    m = Module(name="W", width=3400, length=6000, height=3400,
               column_section=SHS, beam_section=SHS)
    r = can_carry(m, TR_24, RD_GO)
    # 폭 3400 > 트럭 3000 → fail. wide_check 여전히 True.
    assert not r.ok
    assert r.wide_check is True


# ── strict_weight 가 없으면 GVW 검사 안함 ─────────────────
def test_no_strict_weight_skips_gvw():
    m = Module(name="m", width=3000, length=6000, height=3400,
               column_section=SHS, beam_section=SHS, extra_weight_kg=20000)
    # 화물 약 20k+frame, 트럭 24t 한도 안쪽이면 통과
    r = can_carry(m, TR_24, RD_GO)  # 광로 40t — 화물만 비교 시 통과
    cargo = _frame_kg() + 20000
    if cargo <= 24000:
        assert r.ok
