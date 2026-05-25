"""도로 + 트럭 + 적재 아이템 4 조건 검사기.

운송프로그램 원본(`src/limits.py`, Song-Jung-Hun/-3-) 이식 + B-1·B-12 정밀화.

[원본과의 차이 — Phase 2 변경점]
- **B-1 정밀화 (옵션화)**: 원본은 "중량은 차량 자체 무게는 별도이므로 화물 ≤
  truck.max_weight 만 본다" 는 명시적 단순화. 우리는 그 단순화를 유지하되
  `strict_weight=True` 옵션을 켜면 GVW(=truck.curb_weight_kg + 화물) 가
  `min(truck.max_weight + curb_weight, road.max_weight)` 한도를 넘는지 검사.
  - 도로 한도 = GVW 기준 (총중량). 트럭 한도 = 화물(=적재량) 기준.
- **B-12 정밀화 (옵션화)**: 원본은 화물 길이만 도로 한도와 비교. 우리는
  `strict_length=True` 옵션을 켜면 화물 length + trailer_length_mm 합산을
  도로 한도와 비교.
- **B-6 단순화 유지 (옵션화)**: 원본은 화물 폭/높이만 검사 (트럭 자체 폭/
  높이 무시). 우리는 이 단순화를 유지하되 외측 높이는 원본대로 vehicle_
  height_offset 가산. `strict_width` 등 추가 옵션은 두지 않음 (충분히 정확).
- **A-1 일반화**: Panel.kind=lshape 분기 대신 Panel.wall_segments 가 있으면
  가장 높은 세그먼트 높이를 화물 높이로 사용 (B-14 정밀도 향상).

[버그 아님 — 원본의 의도된 단순화]
원본 정책 그대로 동작하는 것이 기본값(strict_*=False). 사용자가 GVW 정밀화를
원하면 옵션을 켠다 (가드 단락 — B-1·B-6·B-12 단순화 정책 재단정 금지).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .models import Module, Panel, Truck, RoadClass


Item = Union[Module, Panel]


@dataclass(frozen=True)
class CarryResult:
    """can_carry 결과. ok=False여도 reasons 누적, wide_check 는 광폭 검토 플래그.

    [원본과의 차이]
    - notes: 단순화 정책으로 인해 검사 생략된 항목을 사람에게 알려주는 메모.
    """

    ok: bool
    reasons: tuple[str, ...] = ()
    wide_check: bool = False
    notes: tuple[str, ...] = ()

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


def can_carry(
    item: Item,
    truck: Truck,
    road: RoadClass,
    strict_weight: bool = False,
    strict_length: bool = False,
) -> CarryResult:
    """단일 아이템 1개를 단일 트럭으로 단일 도로에서 운송 가능한가?

    [엄격 모드 옵션]
    - strict_weight: GVW(차체+화물) 정밀화. 도로 한도 = GVW, 트럭 한도 = 적재량.
    - strict_length: 트레일러 자체 길이 가산. 도로 한도 = 화물 + trailer_length.

    [기본 모드 — 원본 단순화 정책 유지]
    - 화물 단독으로 도로/트럭 한도 비교.
    """
    length, width, height, weight = _item_dims(item)
    reasons: list[str] = []
    notes: list[str] = []

    # 1) 길이
    if strict_length and truck.trailer_length_mm > 0:
        total_len = length + truck.trailer_length_mm
        # 트럭 자체 길이는 의미 없음(트럭 max_length 는 적재가능 길이) → 도로만 비교
        if total_len > road.max_length:
            reasons.append(
                f"전장 초과(엄격): 화물 {length:.0f} + 트레일러 {truck.trailer_length_mm:.0f} = "
                f"{total_len:.0f}mm > 도로 {road.max_length:.0f}mm"
            )
        if length > truck.max_length:
            reasons.append(
                f"적재 길이 초과: {length:.0f}mm > 트럭 {truck.max_length:.0f}mm"
            )
    else:
        length_lim = min(truck.max_length, road.max_length)
        if length > length_lim:
            reasons.append(
                f"길이 초과: {length:.0f}mm > {length_lim:.0f}mm "
                f"(트럭 {truck.max_length:.0f} / 도로 {road.max_length:.0f})"
            )
        if not strict_length:
            notes.append("화물 길이만 비교 (트레일러 길이 미포함 — 단순화)")

    # 2) 폭
    width_lim = min(truck.max_width, road.max_width)
    if width > width_lim:
        reasons.append(
            f"폭 초과: {width:.0f}mm > {width_lim:.0f}mm "
            f"(트럭 {truck.max_width:.0f} / 도로 {road.max_width:.0f})"
        )

    # 3) 높이 (차량 + 화물 외측 높이)
    outer_height = height + truck.vehicle_height_offset
    height_lim = min(truck.max_height, road.max_height)
    if outer_height > height_lim:
        reasons.append(
            f"외측높이 초과: {outer_height:.0f}mm "
            f"(차량 {truck.vehicle_height_offset:.0f} + 화물 {height:.0f}) "
            f"> {height_lim:.0f}mm"
        )

    # 4) 중량
    if strict_weight and truck.curb_weight_kg > 0:
        # 트럭 적재 한도: 화물 무게 vs truck.max_weight
        if weight > truck.max_weight:
            reasons.append(
                f"적재 중량 초과: {weight:.0f}kg > 트럭 {truck.max_weight:.0f}kg"
            )
        # 도로 한도: GVW (차체+화물) vs road.max_weight
        gvw = truck.curb_weight_kg + weight
        if gvw > road.max_weight:
            reasons.append(
                f"GVW 초과(엄격): 차체 {truck.curb_weight_kg:.0f} + 화물 {weight:.0f} = "
                f"{gvw:.0f}kg > 도로 {road.max_weight:.0f}kg"
            )
    else:
        weight_lim = min(truck.max_weight, road.max_weight)
        if weight > weight_lim:
            reasons.append(
                f"중량 초과: {weight:.0f}kg > {weight_lim:.0f}kg "
                f"(트럭 {truck.max_weight:.0f} / 도로 {road.max_weight:.0f})"
            )
        if not strict_weight:
            notes.append("화물 중량만 비교 (트럭 자체 중량 미포함 — 단순화)")

    wide_check = isinstance(item, Module) and item.is_wide()
    return CarryResult(
        ok=not reasons,
        reasons=tuple(reasons),
        wide_check=wide_check,
        notes=tuple(notes),
    )


__all__ = ["Item", "CarryResult", "can_carry"]
