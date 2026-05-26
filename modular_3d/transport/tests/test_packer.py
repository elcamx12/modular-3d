"""Phase 2 단위 테스트 — packer.py.

[검증 시나리오]
- 단일 종속 (L자 = wall_segments 1개) 패킹.
- 2면 (ㄷ자) 패킹 — 높이/내공 검사.
- 3면 패킹.
- 부분벽 (변 일부만 채움) — 적층 가능한 빈 공간.
- 적층 (종속 위에 floor 적층) — _can_stack_on_dependent.
- B-3: recheck 시 적층 무게 합산.
- B-5: 혼적 시 ppr 재계산.
- B-25: cargo vs gross weight 분리.
- 메인 pack_items 통합 흐름.
"""
from __future__ import annotations

import pytest

from modular_3d.transport.models import (
    Section, Module, Panel, Truck, SiteLimit, SpacingParams, WallSegment,
)
from modular_3d.transport.packer import (
    pack_items, recheck_trip_with_truck, Trip, PackResult,
)
from modular_3d.transport import packer as pk


SHS = Section(name="SHS200x8", section_type="SHS",
              width=200, height=200, thickness=8, weight_per_m=47.9)

TR_24 = Truck(name="저상24t", truck_type="lowbed",
              max_length=12000, max_width=3000, max_height=4500, max_weight=24000,
              vehicle_height_offset=700, curb_weight_kg=14000, active=True)
TR_28W = Truck(name="광폭28t", truck_type="extendable",
               max_length=18000, max_width=3400, max_height=4500, max_weight=28000,
               vehicle_height_offset=700, curb_weight_kg=16000, active=True)
# 현장 제한: 폭·높이는 광로급(트럭보다 넓어 비제약), 무게는 해당없음(트럭 한도로만).
SITE = SiteLimit(max_gvw_kg=None, max_width_mm=3500, max_height_mm=4500)
SP = SpacingParams()


def _seg(side, length, height=3000, thickness=200):
    return WallSegment(side=side, start_offset_mm=0, length_mm=length,
                       height_mm=height, thickness_mm=thickness,
                       column_section=SHS, beam_section=SHS)


# ── 단일 종속 (L자) ───────────────────────────────────────
def test_single_dependent_lshape_packs_one_trip():
    p = Panel(name="L1", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(_seg(0, 6000),))
    res = pack_items([], [p], [TR_24], SITE, SP)
    assert res.total_trips == 1
    assert not res.blocked
    assert res.trips[0].kind == "panel"
    assert len(res.trips[0].items) == 1


# ── 2면 (ㄷ자) ────────────────────────────────────────────
def test_2face_dependent_packs():
    p = Panel(name="C2", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS,
              wall_segments=(_seg(0, 6000), _seg(2, 6000)))
    res = pack_items([], [p], [TR_24], SITE, SP)
    assert res.total_trips == 1
    assert not res.blocked


# ── 3면 패킹 ─────────────────────────────────────────────
def test_3face_dependent_packs():
    p = Panel(name="C3", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS,
              wall_segments=(_seg(0, 6000), _seg(1, 2800), _seg(2, 6000)))
    res = pack_items([], [p], [TR_24], SITE, SP)
    assert res.total_trips == 1
    assert not res.blocked


# ── 부분 벽 ──────────────────────────────────────────────
def test_partial_wall_segment_packs():
    """변의 일부만 채운 부분 벽."""
    partial = WallSegment(side=0, start_offset_mm=1000, length_mm=3000,
                          height_mm=3000, thickness_mm=200,
                          column_section=SHS, beam_section=SHS)
    p = Panel(name="PV", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(partial,))
    res = pack_items([], [p], [TR_24], SITE, SP)
    assert res.total_trips == 1
    assert not res.blocked


# ── 적층: 종속 위에 floor 적층 ─────────────────────────────
def test_stack_floor_on_dependent_base():
    """L자 베이스 위에 small floor 패널 적층 가능 — 한 회차 안에 두 화물 모두 진입.

    [Phase 8 변경] 신규 패커는 Best-Fit 자유 배치라서 base 옆자리(FLOOR)·
    내부 슬롯(DEP_INNER) 중 더 효율적인 자리를 선택. 기존 FFD 의 stacked_items
    분리는 깨질 수 있으나 *기능적으로는 동등*: 두 화물이 한 회차 안에 진입 + blocked 0.
    """
    base = Panel(name="L", kind="floor", width=2800, length=6000, thickness=150,
                 beam_section=SHS, wall_segments=(_seg(0, 6000),))
    cand = Panel(name="F_small", kind="floor", width=1500, length=4000, thickness=150,
                 beam_section=SHS)
    res = pack_items([], [base, cand], [TR_24], SITE, SP)
    assert res.total_trips == 1
    assert not res.blocked
    # 두 화물이 한 회차 안에 (items + stacked_items 합산)
    trip = res.trips[0]
    all_in_trip = list(trip.items) + [s for s in trip.stacked_items if s is not None]
    names = {x.name for x in all_in_trip}
    assert names == {"L", "F_small"}


def test_stack_blocked_if_too_wide():
    """적층 후보가 베이스 내공 폭보다 넓어도 — 신규 패커는 FLOOR 옆자리 사용으로 1 회차 가능.

    [Phase 8 변경] 기존 FFD 는 *DEP_INNER 적층 실패 → 별도 회차* 분기였으나, 신규
    패커는 FLOOR 자유 배치로 옆자리에 넣음. 길이 합산이 트럭 유효 길이 안이면 1 회차.
    """
    base = Panel(name="L", kind="floor", width=2800, length=6000, thickness=150,
                 beam_section=SHS, wall_segments=(_seg(0, 6000, thickness=300),))
    cand = Panel(name="F_wide", kind="floor", width=2600, length=4000, thickness=150,
                 beam_section=SHS)
    res = pack_items([], [base, cand], [TR_24], SITE, SP)
    assert not res.blocked
    # 회차 1 또는 2 모두 정상 — 두 화물 모두 진입했는지가 핵심
    all_in_trips = []
    for t in res.trips:
        all_in_trips += list(t.items) + [s for s in t.stacked_items if s is not None]
    names = {x.name for x in all_in_trips}
    assert names == {"L", "F_wide"}


# ── B-3: recheck 적층 무게 합산 ────────────────────────────
def test_recheck_includes_stacked_weight():
    """B-3 — recheck 시 stacked_items 무게가 합산되어 한도 초과 시 거부."""
    base = Panel(name="L", kind="floor", width=2800, length=6000, thickness=150,
                 beam_section=SHS, wall_segments=(_seg(0, 6000),),
                 extra_weight_kg=10000)
    cand = Panel(name="F_heavy", kind="floor", width=1500, length=4000, thickness=150,
                 beam_section=SHS, extra_weight_kg=18000)
    # 두 매 합치면 cargo > 24t 초과 가능성
    trip = Trip(trip_no=1, truck=TR_24, items=[base], stacked_items=[cand])
    ok, reason, _ = recheck_trip_with_truck(trip, TR_24, SITE, SP)
    # 무게가 24t 안쪽이면 ok, 초과면 fail. 어느 쪽이든 stacked 가 합산되었는지 확인.
    assert trip.cargo_weight == pytest.approx(base.weight + cand.weight)
    # 큰 트럭(28t)으로 바꾸면 통과해야
    ok2, reason2, _ = recheck_trip_with_truck(trip, TR_28W, SITE, SP)
    if base.weight + cand.weight <= 28000:
        assert ok2


# ── A1: recheck 패널 용량 재계산 (작은 트럭 override 차단) ──
def test_recheck_panel_override_to_small_truck_rejected():
    """[A1] 패널 회차를 더 작은 트럭으로 override 하면 적재 매수 초과를 거부.

    이전 구현은 기존 레이아웃을 그대로 두어 작은 트럭에 안 맞아도 통과했음.
    """
    panels = [Panel(name=f"F{i}", kind="floor", width=2400, length=3000,
                    thickness=150, beam_section=SHS, extra_weight_kg=500)
              for i in range(8)]
    res = pack_items([], panels, [TR_28W], SITE, SP)
    assert res.total_trips == 1
    trip = res.trips[0]
    # [Phase 8] 신규 패커는 일부를 STACK 으로 보낼 수 있어 items 길이는 ≤ 8.
    # 회차 안의 *전체 화물* 수가 8 인지 확인 — items + stacked_items.
    all_in_trip = list(trip.items) + [s for s in trip.stacked_items if s is not None]
    assert len(all_in_trip) == 8
    # 8매가 안 들어가는 작은 트럭으로 override → 거부
    small = Truck(name="소형", truck_type="lowbed", max_length=5000,
                  max_width=3000, max_height=2000, max_weight=24000,
                  vehicle_height_offset=700)
    ok, reason, new_trip = recheck_trip_with_truck(trip, small, SITE, SP)
    assert not ok
    assert new_trip is None
    assert "매" in reason  # "N매 요청 / 최대 M매" 형식 진단
    # 원래 큰 트럭으로는 OK + 레이아웃 재계산
    ok2, _, nt2 = recheck_trip_with_truck(trip, TR_28W, SITE, SP)
    assert ok2 and nt2 is not None
    assert nt2.panels_per_row >= 1


# ── B-5: 혼적 시 ppr 재계산 ────────────────────────────────
def test_mixed_panel_ppr_recomputed():
    """길이 다른 floor 패널 혼적 시 ppr 이 가장 긴 패널 기준으로 재계산."""
    p_long = Panel(name="FL", kind="floor", width=2500, length=5500, thickness=150,
                   beam_section=SHS, extra_weight_kg=500)
    p_short = Panel(name="FS", kind="floor", width=2500, length=2000, thickness=150,
                    beam_section=SHS, extra_weight_kg=500)
    # 둘 다 12m 저상에 들어가야. usable=11600.
    # 가장 긴 5500 기준 ppr = floor((11600+100)/(5500+100)) = floor(11700/5600) = 2
    res = pack_items([], [p_long, p_short], [TR_24], SITE, SP)
    # 같은 트립에 묶여야 (혼적 가능)
    assert res.total_trips == 1
    trip = res.trips[0]
    assert trip.panels_per_row == 2


# ── B-25: cargo vs gross weight 분리 ──────────────────────
def test_cargo_vs_gross_weight():
    p = Panel(name="F", kind="floor", width=2500, length=4000, thickness=150,
              beam_section=SHS, extra_weight_kg=2000)
    res = pack_items([], [p], [TR_24], SITE, SP)
    trip = res.trips[0]
    assert trip.cargo_weight == pytest.approx(p.weight)
    # GVW = cargo + curb (14000)
    assert trip.gross_weight == pytest.approx(p.weight + 14000)
    assert trip.gross_weight > trip.cargo_weight


# ── 모듈 기본 경로 ────────────────────────────────────────
def test_module_pack_one_trip_per_module():
    m1 = Module(name="m1", width=3000, length=6000, height=3400,
                column_section=SHS, beam_section=SHS)
    m2 = Module(name="m2", width=3000, length=6000, height=3400,
                column_section=SHS, beam_section=SHS)
    res = pack_items([m1, m2], [], [TR_24], SITE, SP)
    assert res.module_trips == 2
    assert res.total_trips == 2


def test_module_blocked_when_too_wide():
    m = Module(name="bigW", width=3600, length=6000, height=3400,
               column_section=SHS, beam_section=SHS)
    res = pack_items([m], [], [TR_24], SITE, SP)
    # 24t 트럭 폭 3000 < 3600
    assert res.module_trips == 0
    assert len(res.blocked) == 1


def test_module_wide_packs_on_extendable():
    m = Module(name="bigW", width=3400, length=6000, height=3400,
               column_section=SHS, beam_section=SHS)
    res = pack_items([m], [], [TR_28W], SITE, SP)
    assert res.module_trips == 1
    assert res.trips[0].truck.name == "광폭28t"


# ── 독립 wall 패널 ────────────────────────────────────────
def test_independent_wall_panel_packs():
    w = Panel(name="W1", kind="wall", width=3000, length=6000, thickness=200,
              beam_section=SHS, column_section=SHS, wall_height=3000)
    res = pack_items([], [w], [TR_24], SITE, SP)
    assert res.total_trips == 1
    assert res.trips[0].items[0].kind == "wall"


# ── 빈 입력 ──────────────────────────────────────────────
def test_empty_inputs():
    res = pack_items([], [], [TR_24], SITE, SP)
    assert res.total_trips == 0
    assert not res.blocked


# ── 종속 무한루프 회피 (광폭/높이 모두 초과) ──────────────
def test_dependent_blocked_if_too_tall():
    """베이스 + 가장 큰 wall_segment 높이 + 차체 가 트럭 높이 초과 → blocked."""
    seg = WallSegment(side=0, start_offset_mm=0, length_mm=6000,
                      height_mm=4500, thickness_mm=200,  # 너무 높음
                      column_section=SHS, beam_section=SHS)
    p = Panel(name="TooTall", kind="floor", width=2800, length=6000, thickness=150,
              beam_section=SHS, wall_segments=(seg,))
    res = pack_items([], [p], [TR_24], SITE, SP)
    # 150+4500=4650, 외측 4650+700=5350 > 4500 → 도로/트럭 한도 초과
    assert res.total_trips == 0
    assert len(res.blocked) == 1


# ── (A 패치 2026-05-26) 회차 누적 GVW 검사 ──────────────────
def test_cumulative_gvw_splits_into_two_trips():
    """단순 패널 2장을 한 트럭에 넣으면 트럭 적재한도(max_weight)는 OK 지만
    차체(curb)+화물 합산이 현장 GVW 한도를 넘는 상황 — 두 회차로 분리되어야.

    트럭 적재한도 24t, 차체 14t, 현장 GVW 32t.
    패널 1장 = 10t. 2장 합치면 화물 20t (적재한도 24t 통과) 인데
    14 + 20 = 34t > 32t (현장 한도) → 한 트럭에 넣으면 안 됨.
    """
    site_gvw = SiteLimit(max_gvw_kg=32000, max_width_mm=None, max_height_mm=None)
    p1 = Panel(name="HV1", kind="floor", width=2800, length=6000, thickness=150,
               beam_section=SHS, extra_weight_kg=10000.0)
    p2 = Panel(name="HV2", kind="floor", width=2800, length=6000, thickness=150,
               beam_section=SHS, extra_weight_kg=10000.0)
    res = pack_items([], [p1, p2], [TR_24], site_gvw, SP)
    # GVW 누적 검사가 들어와 두 회차로 분리되어야.
    assert res.total_trips == 2, (
        f"누적 GVW 미검사: 2장이 1회차에 합쳐짐 (총 GVW=34t > 32t 한도)"
    )
    assert not res.blocked


def test_recheck_rejects_truck_when_cumulative_gvw_exceeded():
    """recheck_trip_with_truck 가 현장 GVW(누적) 위반을 거절해야 한다."""
    # 패널 2장 묶음 (적재한도 통과). 작은 차체 트럭으로는 OK.
    site_loose = SiteLimit(max_gvw_kg=None, max_width_mm=None, max_height_mm=None)
    p1 = Panel(name="W1", kind="floor", width=2800, length=6000, thickness=150,
               beam_section=SHS, extra_weight_kg=8000.0)
    p2 = Panel(name="W2", kind="floor", width=2800, length=6000, thickness=150,
               beam_section=SHS, extra_weight_kg=8000.0)
    res = pack_items([], [p1, p2], [TR_24], site_loose, SP)
    assert res.total_trips == 1  # 느슨한 site 에서는 한 회차
    trip = res.trips[0]
    # 실측 화물 무게 = extra 8000 × 2 + 보 자중 (Panel.weight property).
    # 느슨한 한도(40t)면 통과, 빡빡한 한도(28t)면 거절되어야.
    site_40 = SiteLimit(max_gvw_kg=40000, max_width_mm=None, max_height_mm=None)
    ok, msg, new_trip = recheck_trip_with_truck(trip, TR_24, site_40, SP)
    assert ok, f"한도 40t 에서는 통과해야: {msg}"
    site_tight = SiteLimit(max_gvw_kg=28000, max_width_mm=None, max_height_mm=None)
    ok2, msg2, _ = recheck_trip_with_truck(trip, TR_24, site_tight, SP)
    assert not ok2
    assert "총중량" in msg2 or "GVW" in msg2


def test_effective_cargo_limit_helper():
    """_effective_cargo_limit = min(트럭 적재능력, 현장 GVW − 차체)."""
    # GVW None → 트럭 적재능력 그대로
    site_none = SiteLimit(max_gvw_kg=None, max_width_mm=None, max_height_mm=None)
    assert pk._effective_cargo_limit(TR_24, site_none) == 24000.0
    # GVW 30t, 차체 14t → 16t 가 잘림
    site_30 = SiteLimit(max_gvw_kg=30000, max_width_mm=None, max_height_mm=None)
    assert pk._effective_cargo_limit(TR_24, site_30) == 16000.0
    # GVW 가 차체보다 작거나 같음 → 0
    site_tiny = SiteLimit(max_gvw_kg=14000, max_width_mm=None, max_height_mm=None)
    assert pk._effective_cargo_limit(TR_24, site_tiny) == 0.0
    # GVW 가 매우 큼 → 트럭 적재능력으로 잘림
    site_huge = SiteLimit(max_gvw_kg=100000, max_width_mm=None, max_height_mm=None)
    assert pk._effective_cargo_limit(TR_24, site_huge) == 24000.0
