"""Phase 7 — 무게중심 최종 시각 보정 (`balance_trips`).

[설계 근거]
- `운송 로직 의사코드.md` § 2.8 + 계획서 § 8 그대로 구현.
- 모든 회차 완성 후 회차별 1 회씩 적용.
- 같은 단·자리 내에서만 x·y 슬라이드 (단 간 교환 금지 — 계획서 § 8.2).
- 10 mm 격자 (사용자 결정).
- 종속 패널 무게중심은 *정확 식* (계획서 § 8.4) — 동음이의 변수 분리.

[비용 영향 0 보장]
- placements 의 *truck_xyz 만 변경* — items / 트럭 / 회차 수 모두 보존.
- compute_cost 결과는 보정 전·후 완전 동일.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .models import Module, Panel, SpacingParams
from .packer import Trip
from .packer_safety import _item_dims_for_posture
from .packer_types import Placement, PlacementSlot, Posture


GRID_STEP_MM: float = 10.0
MAX_BALANCE_ITER: int = 300


# ════════════════════════════════════════════════════════════════════
# 종속 패널 정확 무게중심 — 계획서 § 8.4
# ════════════════════════════════════════════════════════════════════
def _floor_frame_weight(panel: Panel) -> float:
    """둘레 보 4 변의 자중 (kg)."""
    beam_total_m = (2.0 * (panel.width + panel.length)) / 1000.0
    return beam_total_m * panel.beam_section.weight_per_m


def _seg_beam_weight(seg) -> float:
    return (seg.length_mm / 1000.0) * seg.beam_section.weight_per_m


def _seg_column_weight(seg) -> float:
    return (seg.height_mm / 1000.0) * seg.column_section.weight_per_m


def _panel_local_cog(panel: Panel) -> Tuple[float, float]:
    """패널 *좌하단 원점* 기준 무게중심 (x, y) (mm).

    [좌표 약속 — models.WallSegment 의 _side_length 정의와 일관]
    - x: 패널 길이 방향 (0 ~ panel.length)
    - y: 패널 폭 방향   (0 ~ panel.width)
    - 변 인덱스:
        0 = 하변 (y=0,            x 방향 변, 변길이 = panel.length)
        1 = 우변 (x=panel.length, y 방향 변, 변길이 = panel.width)
        2 = 상변 (y=panel.width,  x 방향 변, 변길이 = panel.length)
        3 = 좌변 (x=0,            y 방향 변, 변길이 = panel.width)
      이는 LYING 자세 시 *패널 length = 트럭 길이 방향* 약속과도 일관.

    [순수 floor / 모듈] → 중심 (length/2, width/2).
    [종속 floor] → 둘레 보(=floor) + 각 세그먼트 보·기둥 가중 평균.
    """
    if not panel.wall_segments:
        return (panel.length / 2.0, panel.width / 2.0)

    # 바닥 무게 = 둘레 보 자중 + extra_weight_kg
    w_floor = _floor_frame_weight(panel) + panel.extra_weight_kg
    cog_fx = panel.length / 2.0
    cog_fy = panel.width / 2.0
    sx = w_floor * cog_fx
    sy = w_floor * cog_fy
    total = w_floor

    for seg in panel.wall_segments:
        # 한 세그먼트 = 상단 보 + 양쪽 기둥 2 개
        w_s = _seg_beam_weight(seg) + 2.0 * _seg_column_weight(seg)
        if seg.side == 0:  # 하변 (y=0) — x 방향 변
            xs = seg.start_offset_mm + seg.length_mm / 2.0
            ys = seg.thickness_mm / 2.0
        elif seg.side == 1:  # 우변 (x=panel.length) — y 방향 변
            xs = panel.length - seg.thickness_mm / 2.0
            ys = seg.start_offset_mm + seg.length_mm / 2.0
        elif seg.side == 2:  # 상변 (y=panel.width) — x 방향 변
            xs = seg.start_offset_mm + seg.length_mm / 2.0
            ys = panel.width - seg.thickness_mm / 2.0
        else:  # 좌변 (x=0) — y 방향 변
            xs = seg.thickness_mm / 2.0
            ys = seg.start_offset_mm + seg.length_mm / 2.0
        sx += w_s * xs
        sy += w_s * ys
        total += w_s

    if total <= 0:
        return (panel.length / 2.0, panel.width / 2.0)
    return (sx / total, sy / total)


# ════════════════════════════════════════════════════════════════════
# 회차 무게중심 — 패널 로컬 COG offset 반영
# ════════════════════════════════════════════════════════════════════
def _placement_cog_xy(placement: Placement) -> Tuple[float, float]:
    """한 placement 의 *트럭 좌표계* 무게중심 (x, y).

    placement.truck_xyz 는 패널 *중심* 기준 위치. 패널 로컬 COG offset 을 더해
    실제 무게중심 위치 산출. STANDING 자세는 폭 방향 의미가 다르나 시각 보정
    목적상 단순화 — placement 중심 위치를 그대로 사용.
    """
    item = placement.item
    tx, ty = placement.truck_xyz[0], placement.truck_xyz[1]
    if isinstance(item, Module):
        return tx, ty
    # Panel — 로컬 COG 의 *중심 offset* 을 트럭 좌표에 더함
    if placement.posture == Posture.STANDING:
        # 세움 자세: 시각 보정에선 placement 중심을 그대로 사용
        return tx, ty
    local_x, local_y = _panel_local_cog(item)
    # 로컬 좌표 약속: x = length 방향, y = width 방향
    offset_x = local_x - item.length / 2.0
    offset_y = local_y - item.width / 2.0
    return tx + offset_x, ty + offset_y


def _compute_trip_cog(placements: List[Placement]) -> Tuple[float, float]:
    """회차 무게중심 (xC, yC) — 트럭 적재함 중심 원점."""
    sx = sy = 0.0
    total = 0.0
    for p in placements:
        w = getattr(p.item, "weight", 0.0)
        cx, cy = _placement_cog_xy(p)
        sx += w * cx
        sy += w * cy
        total += w
    if total <= 0:
        return 0.0, 0.0
    return sx / total, sy / total


# ════════════════════════════════════════════════════════════════════
# 슬라이드 안전 검사 — 트럭 내부 + 옆 패널 겹침 금지
# ════════════════════════════════════════════════════════════════════
def _within_truck_bounds(
    truck, placement: Placement, new_x: float, new_y: float, sp: SpacingParams,
) -> bool:
    """new_x, new_y 위치에 패널이 트럭 적재함 안에 들어가는가."""
    L, W, _, _ = _item_dims_for_posture(placement.item, placement.posture)
    usable = truck.max_length - 2.0 * sp.truck_edge_clearance_mm
    eff_w = truck.max_width + 2.0 * sp.side_overhang_mm
    if new_x - L / 2.0 < -usable / 2.0 - 1e-6:
        return False
    if new_x + L / 2.0 > usable / 2.0 + 1e-6:
        return False
    if new_y - W / 2.0 < -eff_w / 2.0 - 1e-6:
        return False
    if new_y + W / 2.0 > eff_w / 2.0 + 1e-6:
        return False
    return True


def _no_overlap_with_others(
    idx: int, placements: List[Placement],
    new_x: float, new_y: float, sp: SpacingParams,
) -> bool:
    """idx 의 placement 를 (new_x, new_y) 로 옮길 때 같은 단의 다른 placement
    와 겹치지 않는가.

    Δz 가 다른 placement (다른 단) 은 검사 안 함 — 적층 자리는 부모 위에 고정.
    """
    target = placements[idx]
    L_t, W_t, _, _ = _item_dims_for_posture(target.item, target.posture)
    for j, p in enumerate(placements):
        if j == idx:
            continue
        if abs(p.truck_xyz[2] - target.truck_xyz[2]) > 1e-6:
            continue  # 다른 단
        L_o, W_o, _, _ = _item_dims_for_posture(p.item, p.posture)
        dx = abs(new_x - p.truck_xyz[0])
        dy = abs(new_y - p.truck_xyz[1])
        min_dx = (L_t + L_o) / 2.0 + sp.panel_gap_mm
        min_dy = (W_t + W_o) / 2.0 + sp.panel_gap_mm
        # 두 축 모두 최소 간격 미달 → 겹침
        if dx < min_dx - 1e-6 and dy < min_dy - 1e-6:
            return False
    return True


# ════════════════════════════════════════════════════════════════════
# 한 회차 보정 — 그래디언트 디센트 방식 슬라이드
# ════════════════════════════════════════════════════════════════════
def _balance_one_trip(trip: Trip, sp: SpacingParams) -> None:
    """같은 단·자리 내에서 10 mm 격자 슬라이드 — 회차 COG 거리 최소화.

    in-place 수정 — trip.placements 만 갱신. 비용 영향 0.
    """
    placements = list(trip.placements)
    if len(placements) <= 1:
        return  # 화물 1 개 이하면 슬라이드 의미 없음

    improved = True
    it = 0
    cur_cog = _compute_trip_cog(placements)
    cur_dist = cur_cog[0] ** 2 + cur_cog[1] ** 2

    while improved and it < MAX_BALANCE_ITER:
        improved = False
        for i in range(len(placements)):
            p = placements[i]
            # 자식 placement (parent_idx != None) 인 자리는 부모 위/내부 — 슬라이드 X
            if p.parent_idx is not None:
                continue
            # 4 방향 1 격자
            for dx, dy in ((-GRID_STEP_MM, 0.0), (GRID_STEP_MM, 0.0),
                           (0.0, -GRID_STEP_MM), (0.0, GRID_STEP_MM)):
                new_x = p.truck_xyz[0] + dx
                new_y = p.truck_xyz[1] + dy
                if not _within_truck_bounds(trip.truck, p, new_x, new_y, sp):
                    continue
                if not _no_overlap_with_others(i, placements, new_x, new_y, sp):
                    continue
                trial = list(placements)
                trial[i] = Placement(
                    item=p.item, slot=p.slot, posture=p.posture,
                    truck_xyz=(new_x, new_y, p.truck_xyz[2]),
                    parent_idx=p.parent_idx,
                )
                new_cog = _compute_trip_cog(trial)
                new_dist = new_cog[0] ** 2 + new_cog[1] ** 2
                if new_dist < cur_dist - 1e-6:
                    placements = trial
                    cur_dist = new_dist
                    improved = True
                    break
            if improved:
                break
        it += 1

    trip.placements = placements


# ════════════════════════════════════════════════════════════════════
# 메인 진입점
# ════════════════════════════════════════════════════════════════════
def balance_trips(
    trips: List[Trip], sp: Optional[SpacingParams] = None,
) -> List[Trip]:
    """모든 회차 완성 후 회차별 1 회 무게중심 보정.

    Args:
        trips: 회차 리스트 (Trip.placements 가 채워져 있어야 함)
        sp: 간격 파라미터 (None → 기본값)

    Returns:
        같은 trips 리스트 (in-place 수정).
    """
    if sp is None:
        sp = SpacingParams()
    for trip in trips:
        _balance_one_trip(trip, sp)
    return trips


__all__ = [
    "GRID_STEP_MM",
    "MAX_BALANCE_ITER",
    "balance_trips",
    "_panel_local_cog",
    "_compute_trip_cog",
    "_balance_one_trip",
]
