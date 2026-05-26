"""단위 테스트 — limits.py (현장 제한 SiteLimit + 트럭 한도, GVW 기본).

[검증 포인트 — 2026-05-26 도로 등급 폐지 개편]
- 길이: 화물 vs 트럭 적재함만 (현장 길이 제한 없음).
- 폭  : 화물 vs 트럭 폭, 그리고 현장 폭 제한.
- 높이: 차량+화물 외측 높이 vs 트럭 높이, 그리고 현장 높이 제한.
- 무게: 화물 vs 트럭 적재능력, 그리고 차체+화물(GVW) vs 현장 총중량.
- SiteLimit 각 항목 None → 그 항목 해당없음(검사 생략).
- 패널 화물 높이: wall_segments 있으면 thickness + max(seg.height) (B-14).
"""
from __future__ import annotations

from modular_3d.transport.limits import can_carry
from modular_3d.transport.models import (
    Section, Module, Panel, Truck, SiteLimit, WallSegment,
)


# ── 픽스처 ────────────────────────────────────────────────
SHS = Section(name="SHS200x8", section_type="SHS",
              width=200, height=200, thickness=8, weight_per_m=47.9)

TR_24 = Truck(
    name="저상24t", truck_type="lowbed",
    max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
    vehicle_height_offset=700, curb_weight_kg=14000, active=True,
)
TR_WIDE = Truck(
    name="광폭28t", truck_type="extendable",
    max_length=18000, max_width=3400, max_height=4500, max_weight=28000,
    vehicle_height_offset=700, curb_weight_kg=16000, active=True,
)

# 현장 운송 제한 (무게=GVW, 폭, 높이). None=해당없음.
SITE_GO = SiteLimit(max_gvw_kg=40000, max_width_mm=3500, max_height_mm=4500)
SITE_NORMAL = SiteLimit(max_gvw_kg=25000, max_width_mm=3300, max_height_mm=4200)
SITE_NARROW = SiteLimit(max_gvw_kg=15000, max_width_mm=3200, max_height_mm=4000)
SITE_NONE = SiteLimit()  # 전부 해당없음(프리패스)


def _frame_kg() -> float:
    """SHS 단면으로 6×3×3.4 모듈 프레임 무게."""
    return (4 * 3.4 + 4 * (3.0 + 6.0)) * 47.9


def _mod(**kw):
    base = dict(name="m", width=3000, length=6000, height=3000,
                column_section=SHS, beam_section=SHS, extra_weight_kg=3000)
    base.update(kw)
    return Module(**base)


# ── 기본 통과 ─────────────────────────────────────────────
def test_module_basic_pass():
    m = Module(name="m", width=3000, length=6000, height=3400,
               column_section=SHS, beam_section=SHS, extra_weight_kg=5000)
    r = can_carry(m, TR_24, SITE_GO)
    assert r.ok


# ── 길이 — 트럭만 (현장 길이 제한 없음) ───────────────────
def test_length_truck_only():
    # 화물 길이 20000 > 트럭 12000 → 막힘
    r = can_carry(_mod(length=20000), TR_24, SITE_GO)
    assert not r.ok
    assert any("적재 길이" in s for s in r.reasons)
    # 화물 길이 9000 ≤ 트럭 12000 → 통과 (현장 길이 제한이 없으므로)
    assert can_carry(_mod(length=9000), TR_24, SITE_GO).ok


# ── 폭 — 트럭 폭 + 양쪽 여유 / 현장 ───────────────────────
def test_width_truck_limit():
    # 폭 3900 > 트럭 3000 + 양쪽 200×2 = 3400 → 막힘
    r = can_carry(_mod(width=3900), TR_24, SITE_GO)
    assert not r.ok
    assert any("폭 초과" in s for s in r.reasons)


def test_width_overhang_allows_400():
    # 폭 3400 = 트럭 3000 + 양쪽 200×2 → 통과(튀어나옴 허용)
    assert can_carry(_mod(width=3400), TR_24, SITE_GO).ok
    # 폭 3401 > 3400 → 막힘
    assert not can_carry(_mod(width=3401), TR_24, SITE_GO).ok


def test_width_site_limit_binds_on_wide_truck():
    """광폭 트럭(3400)엔 들어가지만 현장 폭(3300)에 막히는 화물."""
    m = _mod(width=3350)
    assert can_carry(m, TR_WIDE, SITE_GO).ok          # 현장 3500 → 통과
    r = can_carry(m, TR_WIDE, SITE_NORMAL)            # 현장 3300 → 막힘
    assert not r.ok
    assert any("폭 초과(현장)" in s for s in r.reasons)


# ── 높이 — 트럭 + 현장 ────────────────────────────────────
def test_height_truck_limit():
    # 화물 4000 + 차체 700 = 4700 > 트럭 4500
    r = can_carry(_mod(height=4000), TR_24, SITE_GO)
    assert not r.ok
    assert any("외측높이 초과" in s for s in r.reasons)


def test_height_site_limit_binds():
    """외측 높이가 트럭 4500엔 OK지만 현장 4000엔 막힘."""
    m = _mod(height=3400)  # 외측 = 3400 + 700 = 4100
    assert can_carry(m, TR_24, SITE_GO).ok            # 현장 4500 → 통과
    r = can_carry(m, TR_24, SITE_NARROW)              # 현장 4000 → 막힘
    assert not r.ok
    assert any("외측높이 초과(현장)" in s for s in r.reasons)


# ── 무게 — 트럭 적재능력 + 현장 GVW ───────────────────────
def test_weight_truck_capacity():
    """화물이 트럭 적재능력(24t) 초과 → 막힘."""
    r = can_carry(_mod(extra_weight_kg=30000), TR_24, SITE_GO)
    assert not r.ok
    assert any("적재 중량 초과" in s for s in r.reasons)


def test_weight_site_gvw_blocks():
    """화물은 트럭 24t 이내지만 차체+화물(GVW)이 현장 25t 초과 → 막힘."""
    # 화물 ≈ frame + 12625 → 약 15000kg (트럭 24t 이내)
    cargo_extra = 15000 - _frame_kg()
    m = Module(name="m", width=3000, length=6000, height=3000,
               column_section=SHS, beam_section=SHS, extra_weight_kg=cargo_extra)
    r = can_carry(m, TR_24, SITE_NORMAL)  # 현장 GVW 25t
    assert not r.ok
    assert any("총중량 초과(현장)" in s for s in r.reasons)
    # 화물 단독 검사는 통과(트럭 24t 이내)
    assert not any("적재 중량 초과" in s for s in r.reasons)


def test_site_none_skips_all_site_checks():
    """현장 제한 전부 None → 트럭 한도만으로 판정."""
    # 무거운 화물이지만 트럭 24t 이내면 통과 (현장 GVW 검사 없음)
    cargo_extra = 20000 - _frame_kg()
    m = Module(name="m", width=3000, length=6000, height=3400,
               column_section=SHS, beam_section=SHS, extra_weight_kg=cargo_extra)
    r = can_carry(m, TR_24, SITE_NONE)
    assert r.ok  # GVW(34t) 검사 안 함, 화물 20t < 트럭 24t


# ── 패널 ──────────────────────────────────────────────────
def test_panel_pure_floor_pass():
    p = Panel(name="p", kind="floor", width=3000, length=6000, thickness=200,
              beam_section=SHS, extra_weight_kg=1500)
    assert can_carry(p, TR_24, SITE_GO).ok


def test_panel_with_wall_segments_height_uses_max_seg():
    """B-14: wall_segments 있으면 thickness + max(seg.height) 가 화물 높이."""
    seg_tall = WallSegment(side=0, start_offset_mm=0, length_mm=6000,
                           height_mm=3000, thickness_mm=200,
                           column_section=SHS, beam_section=SHS)
    seg_short = WallSegment(side=2, start_offset_mm=0, length_mm=6000,
                            height_mm=1500, thickness_mm=200,
                            column_section=SHS, beam_section=SHS)
    p = Panel(name="C", kind="floor", width=3000, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(seg_tall, seg_short))
    # 화물 높이 = 150 + 3000 = 3150. 외측 = 3850 < 4500 → 통과
    assert can_carry(p, TR_24, SITE_GO).ok


# ── 현장 폭 제한이 트럭+여유보다 빡빡하면 현장으로 막힘 ───
def test_width_site_tighter_than_overhang():
    # 트럭+여유=3400 은 통과시키지만 현장 폭 3200 이면 막힘
    site = SiteLimit(max_gvw_kg=None, max_width_mm=3200, max_height_mm=4500)
    m = Module(name="W", width=3300, length=6000, height=3400,
               column_section=SHS, beam_section=SHS)
    assert can_carry(m, TR_24, SITE_GO).ok          # 현장 3500 → 통과
    r = can_carry(m, TR_24, site)                   # 현장 3200 → 막힘
    assert not r.ok
    assert any("폭 초과(현장)" in s for s in r.reasons)
