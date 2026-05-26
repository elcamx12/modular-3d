"""현장 제한 + 트럭 + 적재 아이템 운송 가능성 검사기.

[검사 정책 — 2026-05-26 도로 등급 폐지 개편]
명명된 도로 등급(광로/일반/이면) 대신 사용자가 직접 넣는 SiteLimit(현장
운송 제한: 총중량 GVW · 폭 · 높이)로 검사한다. 각 항목은 트럭 자체 한도와
함께(둘 중 더 빡빡한 쪽) 본다.
- 길이: 화물 길이 ≤ 트럭 적재함 길이 (현장 길이 제한 없음).
- 폭  : 화물 폭 ≤ 트럭 폭, 그리고 현장 폭 제한(있으면)도.
- 높이: 차량 적재면 + 화물 외측 높이 ≤ 트럭 높이, 그리고 현장 높이 제한(있으면)도.
- 무게: 화물 ≤ 트럭 적재능력(Truck.max_weight), 그리고 현장 GVW 제한(있으면)이
  차체(curb) + 화물 총중량 이상이어야 한다.
SiteLimit 각 항목이 None 이면 그 항목은 "해당없음"으로 검사하지 않는다.

[A-1 일반화] Panel.wall_segments 가 있으면 가장 높은 세그먼트 높이를 화물
높이로 사용. (원본 lshape 의 wall_height 흡수.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .models import Module, Panel, Truck, SiteLimit


Item = Union[Module, Panel]


@dataclass(frozen=True)
class CarryResult:
    """can_carry 결과. ok=False 여도 reasons 에 모든 위반 사유 누적."""

    ok: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


def _item_dims(item: Item) -> tuple[float, float, float, float]:
    """(length, width, height, weight) 통일 추출.

    Panel 의 화물 높이 (적층/평적 시 1매 점유 높이):
    - wall_segments 가 있으면 = thickness + max(seg.height_mm) — B-14 정밀화
    - 원본 lshape kind = thickness + wall_height
    - 그 외 = thickness
    """
    if isinstance(item, Module):
        return item.length, item.width, item.height, item.weight
    # Panel
    if item.wall_segments:
        max_seg_h = max(s.height_mm for s in item.wall_segments)
        return item.length, item.width, item.thickness + max_seg_h, item.weight
    if item.kind == "lshape" and item.wall_height > 0:
        return item.length, item.width, item.thickness + item.wall_height, item.weight
    return item.length, item.width, item.thickness, item.weight


def can_carry(item: Item, truck: Truck, site: SiteLimit,
              side_overhang_mm: float = 200.0) -> CarryResult:
    """단일 아이템 1개를 단일 트럭으로 현장 제한 아래 운송 가능한가?

    트럭 자체 한도와 현장 제한(SiteLimit)을 동시에 본다. 현장 제한 각 항목이
    None 이면 그 항목은 검사하지 않는다(해당없음).
    - 길이: 화물 길이 ≤ 트럭 적재함 길이 (현장 길이 제한 없음).
    - 폭  : 화물 폭 ≤ 트럭 폭 + 양쪽 여유(side_overhang_mm), 그리고 현장 폭 제한.
    - 높이: 차량 적재면 + 화물 외측 높이 ≤ 트럭 높이, 그리고 현장 높이 제한.
    - 무게: 화물 ≤ 트럭 적재능력, 그리고 차체+화물(GVW) ≤ 현장 총중량 제한.
    """
    length, width, height, weight = _item_dims(item)
    reasons: list[str] = []

    # 1) 길이 — 트럭 적재함만 (현장 길이 제한 없음)
    if length > truck.max_length:
        reasons.append(
            f"적재 길이 초과: {length:.0f}mm > 트럭 {truck.max_length:.0f}mm"
        )

    # 2) 폭 — 트럭 폭 + 양쪽 여유 / 현장
    eff_truck_width = truck.max_width + 2.0 * side_overhang_mm
    if width > eff_truck_width:
        reasons.append(
            f"폭 초과: {width:.0f}mm > 트럭폭+여유 {eff_truck_width:.0f}mm "
            f"(트럭 {truck.max_width:.0f} + 양쪽 {side_overhang_mm:.0f})"
        )
    if site.max_width_mm is not None and width > site.max_width_mm:
        reasons.append(
            f"폭 초과(현장): {width:.0f}mm > 현장 한도 {site.max_width_mm:.0f}mm"
        )

    # 3) 높이 — 차량 적재면 + 화물 외측 높이
    outer_height = height + truck.vehicle_height_offset
    if outer_height > truck.max_height:
        reasons.append(
            f"외측높이 초과: {outer_height:.0f}mm "
            f"(차량 {truck.vehicle_height_offset:.0f} + 화물 {height:.0f}) "
            f"> 트럭 {truck.max_height:.0f}mm"
        )
    if site.max_height_mm is not None and outer_height > site.max_height_mm:
        reasons.append(
            f"외측높이 초과(현장): {outer_height:.0f}mm "
            f"(차량 {truck.vehicle_height_offset:.0f} + 화물 {height:.0f}) "
            f"> 현장 한도 {site.max_height_mm:.0f}mm"
        )

    # 4) 무게 — 트럭 적재능력(화물) + 현장 총중량(차체+화물)
    if weight > truck.max_weight:
        reasons.append(
            f"적재 중량 초과: {weight:.0f}kg > 트럭 적재한도 {truck.max_weight:.0f}kg"
        )
    if site.max_gvw_kg is not None:
        gvw = truck.curb_weight_kg + weight
        if gvw > site.max_gvw_kg:
            reasons.append(
                f"총중량 초과(현장): 차체 {truck.curb_weight_kg:.0f} + 화물 {weight:.0f} = "
                f"{gvw:.0f}kg > 현장 한도 {site.max_gvw_kg:.0f}kg"
            )

    return CarryResult(ok=not reasons, reasons=tuple(reasons))


__all__ = ["Item", "CarryResult", "can_carry"]
