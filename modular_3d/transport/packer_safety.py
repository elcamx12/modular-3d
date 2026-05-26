"""Phase 2 — 새 패커의 안전 검사 일원화.

[설계 근거]
- `운송 로직 의사코드.md` § 2.2 참조.
- 한 함수 `can_place()` 가 길이·폭·높이·무게 4중 검사를 모두 수행.
- 모든 자리 평가 + VND 수술이 이 함수를 호출 → 검사 누락 *구조적 불가능*.
- (A) 패치의 `_effective_cargo_limit` 헬퍼를 누적 GVW 검사에 그대로 사용.

[Phase 4+ 호출 지점]
- packer_core.evaluate_slot — 자리 후보 평가
- packer_local_search.op_* — VND 수술 (Transfer/Swap/Merge/Posture Toggle 등)
- packer.recheck_trip_with_truck — 트럭 교체 검사
"""
from __future__ import annotations

from typing import List, Tuple

from .models import Module, Panel, SiteLimit, SpacingParams
from .packer_types import CheckResult, Item, PlacementSlot, Posture, TruckState


# ════════════════════════════════════════════════════════════════════
# Phase 4-A — 컴포넌트 박스 헬퍼 (충돌 검사 V2 용)
# ════════════════════════════════════════════════════════════════════
# 컴포넌트 = 운송 단위 (모듈 1개 + 종속 부재 / 패널 1개 + 종속 부재).
# 충돌 검사 V2 는 *부재 단위* 박스 페어와이즈 비교 — 외곽 AABB 합산 폐기.
# 본 헬퍼는 컴포넌트의 모든 부재 박스를 *트럭 좌표계 박스 리스트* 로 반환한다.
#
# [좌표계 약속]
#   - 컴포넌트 nominal 좌표: x=length, y=width, z=height (mm)
#   - 컴포넌트 좌하단(0, 0, 0) = trip_xyz_mm 으로 트럭 적재함 좌표 변환
#   - 트럭 적재함 좌측 끝 = (0, 0, 0), 폭 좌측 = y=0, 바닥 = z=0
#   - 반환 박스 = (x_min, y_min, z_min, x_max, y_max, z_max) tuple
#
# [데이터 흐름]
#   item.body_parts / item.attached_parts (어댑터에서 nominal 좌표로 채움)
#     → 본 함수가 trip_xyz_mm 더해 트럭 좌표 박스로 변환
#     → 충돌 검사 / 그리드 인덱스 입력으로 사용
#
# [기존 코드 영향 없음]
#   _expand_with_attachments, can_place 등 기존 함수는 그대로 둠. 본 함수는
#   *옆에 새로* 추가 — Phase 4-C 의 can_place_v2 에서 호출 예정.


def _nominal_dims_for_posture(item: Item, posture: Posture) -> Tuple[float, float, float]:
    """컴포넌트 자체의 nominal 차원 (부재 포함 외곽 X — 단순 본체 박스).

    - Module: (length, width, height) — 항상 LYING
    - Panel LYING: (length, width, thickness)
    - Panel STANDING: (length, thickness, width)
    """
    if isinstance(item, Module):
        return item.length, item.width, item.height
    if posture == Posture.STANDING:
        return item.length, item.thickness, item.width
    return item.length, item.width, item.thickness


def boxes_of_component(
    item: Item, posture: Posture,
    trip_xyz_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> List[Tuple[float, float, float, float, float, float]]:
    """컴포넌트의 모든 부재 박스 리스트 (트럭 좌표 mm).

    Args:
        item: Module 또는 Panel
        posture: LYING / STANDING (Panel 만 STANDING 가능, Module 은 LYING 만)
        trip_xyz_mm: 컴포넌트 좌하단의 트럭 적재함 좌표 (x, y, z) mm.
                     기본값 (0,0,0) 면 nominal 좌표 그대로 반환.

    Returns:
        리스트 of (x_min, y_min, z_min, x_max, y_max, z_max) — 부재 1개당 1 박스.
        body_parts(부모 자체) + attached_parts(종속) 모두 포함.

    [STANDING 처리]
    STANDING 자세는 Panel 만 (벽 패널 세워 운송). body_parts/attached_parts 는
    LYING 기준 좌표라 STANDING 시 *축 교환* 필요 — nominal (x=length, y=width,
    z=thickness) → STANDING (x=length, y=thickness, z=width). 즉 y↔z 교환.
    """
    tx, ty, tz = trip_xyz_mm
    out: List[Tuple[float, float, float, float, float, float]] = []

    is_standing = posture == Posture.STANDING

    def _add(x: float, y: float, z: float, L: float, W: float, H: float) -> None:
        """LYING 기준 박스 → trip 좌표 박스. STANDING 시 y↔z 교환."""
        if is_standing:
            # nominal (x, y, z, L, W, H) → STANDING (x, z, y, L, H, W)
            x0 = tx + x; y0 = ty + z; z0 = tz + y
            x1 = x0 + L; y1 = y0 + H; z1 = z0 + W
        else:
            x0 = tx + x; y0 = ty + y; z0 = tz + z
            x1 = x0 + L; y1 = y0 + W; z1 = z0 + H
        if x1 > x0 and y1 > y0 and z1 > z0:
            out.append((x0, y0, z0, x1, y1, z1))

    # body_parts — 부모 자체 부재
    for p in tuple(getattr(item, "body_parts", ()) or ()):
        _add(p.x_mm, p.y_mm, p.z_mm, p.length_mm, p.width_mm, p.height_mm)
    # attached_parts — 종속 부재 (캔틸·중간)
    for p in tuple(getattr(item, "attached_parts", ()) or ()):
        _add(p.x_mm, p.y_mm, p.z_mm, p.length_mm, p.width_mm, p.height_mm)

    # Fallback — body_parts·attached_parts 비어있는 화물(어댑터가 못 채운 경우 등)
    # 컴포넌트 nominal 박스 1개로 대체.
    if not out:
        L_nom, W_nom, H_nom = _nominal_dims_for_posture(item, posture)
        # nominal 차원은 *posture 반영된 운송 차원* 이라 그대로 더하면 됨
        x0 = tx; y0 = ty; z0 = tz
        out.append((x0, y0, z0, x0 + L_nom, y0 + W_nom, z0 + H_nom))

    return out


def component_outer_aabb(
    item: Item, posture: Posture,
    trip_xyz_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Tuple[float, float, float, float, float, float]:
    """컴포넌트의 *모든 부재* 외곽 AABB. 그리드 인덱스 후보 조회용.

    [용도] 새 컴포넌트 배치 후보 자리에 *근접* 한 기존 컴포넌트만 충돌 검사
    하려고 광역 검색 박스 산출. 정밀 검사는 boxes_of_component 페어와이즈.
    """
    boxes = boxes_of_component(item, posture, trip_xyz_mm)
    if not boxes:
        # 이론상 못 옴 — boxes_of_component 가 fallback 으로 1 개 보장
        tx, ty, tz = trip_xyz_mm
        return (tx, ty, tz, tx + 1.0, ty + 1.0, tz + 1.0)
    xs0 = [b[0] for b in boxes]; ys0 = [b[1] for b in boxes]; zs0 = [b[2] for b in boxes]
    xs1 = [b[3] for b in boxes]; ys1 = [b[4] for b in boxes]; zs1 = [b[5] for b in boxes]
    return (min(xs0), min(ys0), min(zs0), max(xs1), max(ys1), max(zs1))


# ── 부재 포함 외곽 차원 합산 (Phase 2 추가) ─────────────────────
def _expand_with_attachments(
    L: float, W: float, H: float, item: Item, posture: Posture,
) -> Tuple[float, float, float]:
    """부착 부재(attached_parts) 의 박스를 부모 화물 박스와 합쳐 외곽 차원 산출.

    [정책 — 2026-05-27 수정]
    - 길이·폭: 부모 nominal 그대로. 운송 시 캔틸 등 돌출 부재는 *부모 위
      또는 적층 자리* 로 재배치 가능하다고 가정 → 폭/길이를 부풀리지 않는다.
      (부풀리면 캔틸 달린 floor 가 트럭 폭 한도 초과로 즉시 blocked 되어
       정상 운송이 안 되는 문제가 발생.)
    - 높이: 부재가 부모 위로 솟는 경우만 외곽 높이 합산. 부재가 부모 위로
      얼마나 더 올라가는지를 적층 한도·내공 검사에 반영.

    [STANDING 호환] STANDING 화물은 attached_parts 가 비어있으니 그대로 패스.
    """
    parts = tuple(getattr(item, "attached_parts", ()) or ())
    if not parts:
        return L, W, H
    z_max = H
    for p in parts:
        z_max = max(z_max, p.z_mm + p.height_mm)
    return (L, W, z_max)


# ── 자세별 점유 차원 변환 ─────────────────────────────────────────
def _item_dims_for_posture(
    item: Item, posture: Posture
) -> Tuple[float, float, float, float]:
    """화물의 점유 차원 → (length, width, height_occupied, weight).

    의사코드 § 2.2 의 식 그대로 + Phase 2 (2026-05-27) 확장:
    - Module: 항상 LYING. (length, width, height, weight) → 부재 포함 외곽
    - Panel LYING: (length, width, 점유두께, weight)
        · wall_segments 가 있으면 점유두께 = thickness + max(seg.height_mm)
        · kind="lshape" 호환은 thickness + wall_height
        · 그 외 = thickness
        · 캔틸 등 attached_parts 가 있으면 외곽 차원 포함
    - Panel STANDING: (length, thickness, width, weight)
        · width 가 높이방향으로 솟음 (A-frame 트럭)
        · 단순 wall 만 가능 (호출 측이 자세 후보 결정에서 이미 거름)

    [Phase 2 — 부재 포함 외곽] 부착 부재(중간보·중간기둥·캔틸레버) 가 있으면
    부모 박스 + 모든 부재 박스의 합집합 AABB 를 반환. 트럭 폭·길이·높이
    한도 검사와 적층 기하 제약(위 ≤ 아래) 이 자동으로 캔틸 돌출을 반영한다.
    """
    if isinstance(item, Module):
        L, W, H = _expand_with_attachments(
            item.length, item.width, item.height, item, posture,
        )
        return L, W, H, item.weight

    # Panel
    if posture == Posture.STANDING:
        # 세움 — width 가 높이방향. STANDING 화물엔 attached_parts 없음 가정.
        return item.length, item.thickness, item.width, item.weight

    # LYING — 점유 두께 계산
    if item.wall_segments:
        max_seg_h = max(s.height_mm for s in item.wall_segments)
        height_occ = item.thickness + max_seg_h
    elif item.kind == "lshape" and item.wall_height > 0:
        height_occ = item.thickness + item.wall_height
    else:
        height_occ = item.thickness

    L, W, H = _expand_with_attachments(
        item.length, item.width, height_occ, item, posture,
    )
    return L, W, H, item.weight


# ── 핵심 안전 4중 검사 ────────────────────────────────────────────
def can_place(
    item: Item,
    posture: Posture,
    slot: PlacementSlot,
    truck_state: TruckState,
    site: SiteLimit,
    sp: SpacingParams,
) -> CheckResult:
    """이 트럭의 현재 상태에 *이 화물을 이 자세로 이 자리에* 놓을 수 있는가?

    검사 4 항목 (모두 통과해야 OK):
    - ① 길이: 누적 점유 + 새 길이 + gap ≤ 트럭 유효 길이 (FLOOR 만 누적, STACK/DEP_INNER 는 별도 처리)
    - ② 폭:   화물 폭(자세 반영) ≤ 트럭 폭 + 양쪽 여유, 그리고 현장 폭 제한도
    - ③ 높이: STACK 이면 새 단 두께가 잔여 내공에 들어가는지, FLOOR/DEP_INNER 는 단일 단 높이 OK
    - ④ 무게: 누적 + 새 무게 ≤ 실효 화물 한도 (현장 GVW 누적 반영)

    Args:
        item: Module 또는 Panel
        posture: LYING 또는 STANDING
        slot: FLOOR / STACK / DEP_INNER
        truck_state: 현재 트럭의 잔여 자원
        site: 현장 운송 제한 (GVW · 폭 · 높이)
        sp: 간격 파라미터 (panel_gap_mm, truck_edge_clearance_mm, side_overhang_mm)

    Returns:
        CheckResult — ok 이고 reasons 비어있으면 통과.
    """
    length, width, height_occupied, weight = _item_dims_for_posture(item, posture)
    tr = truck_state.truck

    reasons: list[str] = []

    # ① 길이 검사
    usable_length = tr.max_length - 2.0 * sp.truck_edge_clearance_mm
    if slot == PlacementSlot.FLOOR:
        # 바닥 자리는 누적 길이에 추가
        gap = sp.panel_gap_mm if truck_state.placements else 0.0
        needed = truck_state.used_floor_length + gap + length
        if needed > usable_length + 1e-6:
            reasons.append(
                f"길이 초과: 누적 {truck_state.used_floor_length:.0f}mm + 새 화물 "
                f"{length:.0f}mm + 간격 {gap:.0f}mm = {needed:.0f}mm > 유효 길이 {usable_length:.0f}mm"
            )
    else:
        # STACK / DEP_INNER 는 단일 화물 길이만 검사 (위·아래 자리는 부모 위치 위)
        if length > usable_length + 1e-6:
            reasons.append(
                f"길이 초과: 화물 {length:.0f}mm > 유효 길이 {usable_length:.0f}mm"
            )

    # ② 폭 검사 (트럭 + 현장)
    eff_truck_width = tr.max_width + 2.0 * sp.side_overhang_mm
    if width > eff_truck_width + 1e-6:
        reasons.append(
            f"폭 초과 (트럭): {width:.0f}mm > 트럭폭+여유 {eff_truck_width:.0f}mm "
            f"(트럭 {tr.max_width:.0f} + 양쪽 {sp.side_overhang_mm:.0f})"
        )
    if site.max_width_mm is not None and width > site.max_width_mm + 1e-6:
        reasons.append(
            f"폭 초과 (현장): {width:.0f}mm > 현장 한도 {site.max_width_mm:.0f}mm"
        )

    # ③ 높이 검사 (트럭 내공 + 현장 외측)
    inner_h = tr.max_height - tr.vehicle_height_offset
    if slot == PlacementSlot.STACK:
        new_layer_top = truck_state.used_inner_height(sp.panel_gap_mm) + sp.panel_gap_mm
    else:
        # FLOOR / DEP_INNER 는 단일 단 높이 (DEP_INNER 는 모체 위 슬롯이지만
        # 모체 점유 높이가 이미 모체 Placement 시 검사됨)
        new_layer_top = 0.0
    cargo_top = new_layer_top + height_occupied
    if cargo_top > inner_h + 1e-6:
        reasons.append(
            f"내공높이 초과: 단 상단 {new_layer_top:.0f} + 화물 {height_occupied:.0f} = "
            f"{cargo_top:.0f}mm > 내공 {inner_h:.0f}mm "
            f"(트럭 {tr.max_height:.0f} − 차체 {tr.vehicle_height_offset:.0f})"
        )
    if site.max_height_mm is not None:
        outer = cargo_top + tr.vehicle_height_offset
        if outer > site.max_height_mm + 1e-6:
            reasons.append(
                f"외측높이 초과 (현장): {outer:.0f}mm "
                f"(차량 {tr.vehicle_height_offset:.0f} + 화물 상단 {cargo_top:.0f}) "
                f"> 현장 한도 {site.max_height_mm:.0f}mm"
            )

    # ④ 무게 검사 — 실효 화물 한도 (트럭 적재능력 ∩ 현장 GVW 누적)
    needed_w = truck_state.used_cargo_weight + weight
    if needed_w > truck_state.effective_cargo_limit + 1e-6:
        # 어느 한도에 걸렸는지 메시지 분기
        if site.max_gvw_kg is not None:
            gvw_room = site.max_gvw_kg - (tr.curb_weight_kg or 0.0)
            if gvw_room <= 0:
                reasons.append(
                    f"중량 초과: 차체 {tr.curb_weight_kg:.0f}kg 만으로도 현장 GVW "
                    f"{site.max_gvw_kg:.0f}kg 초과 — 이 트럭 사용 불가"
                )
            elif gvw_room < tr.max_weight:
                reasons.append(
                    f"총중량 초과 (현장 GVW): 차체 {tr.curb_weight_kg:.0f} + "
                    f"누적 {truck_state.used_cargo_weight:.0f} + 새 {weight:.0f} = "
                    f"차체+화물 {tr.curb_weight_kg + needed_w:.0f}kg > 현장 {site.max_gvw_kg:.0f}kg"
                )
            else:
                reasons.append(
                    f"적재 중량 초과: 누적 {truck_state.used_cargo_weight:.0f} + 새 "
                    f"{weight:.0f} = {needed_w:.0f}kg > 트럭 적재한도 {tr.max_weight:.0f}kg"
                )
        else:
            reasons.append(
                f"적재 중량 초과: 누적 {truck_state.used_cargo_weight:.0f} + 새 "
                f"{weight:.0f} = {needed_w:.0f}kg > 트럭 적재한도 {tr.max_weight:.0f}kg"
            )

    return CheckResult(ok=not reasons, reasons=tuple(reasons))


__all__ = [
    "_item_dims_for_posture",
    "_nominal_dims_for_posture",
    "boxes_of_component",
    "component_outer_aabb",
    "can_place",
    "can_place_v2",
    "GAP_MM",
]


# ════════════════════════════════════════════════════════════════════
# Phase 4-C — V2 안전 검사 (부재 페어와이즈 + 그리드 인덱스)
# ════════════════════════════════════════════════════════════════════
# [정책 — 2026-05-27 사용자 결정]
# - 다른 컴포넌트끼리 *축 분리 거리 ≥ 100mm* (한 축이라도 분리되면 OK)
# - 같은 컴포넌트 내부 부재끼리는 갭 0 (붙어있어야 결박)
# - 차량 앞뒤 100mm 여유 (truck_edge_clearance)
# - 폭 양쪽 ±200mm 돌출 허용 (side_overhang)
# - 모듈은 *통째로 닫힌 박스* — 캔틸이 모듈 관통 불가 (페어와이즈로 자동 처리)
# - 그리드 인덱스로 근접 컴포넌트만 후보 조회 → O(NM) 가속

GAP_MM: float = 100.0  # 컴포넌트 간 최소 갭


def _trip_xyz_from_truck_center(
    truck_xyz_center_mm: Tuple[float, float, float],
    L_eff: float, W_eff: float, truck,
) -> Tuple[float, float, float]:
    """packer 가 쓰는 *적재함 중심 원점* 좌표 → 컴포넌트 *좌하단* mm 변환.

    [좌표계]
    - 적재함 좌측 끝 = (0, 0, 0)
    - x 축 = 길이 방향, y 축 = 폭 방향, z 축 = 바닥부터 높이
    - 컴포넌트 좌하단 = truck 좌측 끝 기준 (x, y, z) mm

    truck_xyz_center_mm 은 *적재함 중심* 기준 *컴포넌트 중심* 좌표. 변환:
      좌하단 x = 적재함 길이/2 + center_x - 컴포넌트 L/2
      좌하단 y = 적재함 폭/2 + center_y - 컴포넌트 W/2
      좌하단 z = 적재함 바닥 + center_z
    """
    cx, cy, cz = truck_xyz_center_mm
    x0 = truck.max_length / 2.0 + cx - L_eff / 2.0
    y0 = truck.max_width / 2.0 + cy - W_eff / 2.0
    z0 = cz  # z 는 적재함 바닥(=0) 기준 화물 *하단*
    return (x0, y0, z0)


def can_place_v2(
    item: Item,
    posture: Posture,
    truck_xyz_center_mm: Tuple[float, float, float],
    truck_state: TruckState,
    site: SiteLimit,
    sp: SpacingParams,
    collision_grid=None,  # CollisionGrid 옵션 — None 이면 단순 페어와이즈
    placements_boxes=None,  # Optional[List[Tuple[owner_id, List[Box]]]] — 그리드 없을 때 fallback
) -> CheckResult:
    """V2 안전 검사 — 부재 페어와이즈 + 갭 100 + 트럭 한도 + 폭 ±200 돌출.

    [V1 과의 차이]
    - V1: _expand_with_attachments 의 *외곽 차원* 으로 한도 검사
    - V2: *부재 박스 집합* 으로 페어와이즈 충돌 + 컴포넌트 외곽 AABB 로 한도 검사

    [반환]
    CheckResult.ok 가 True 면 *그 자리에 컴포넌트 배치 가능*. False 면 reasons 에
    위반 사유 목록.

    Args:
        item, posture, truck_state, site, sp: 기존 can_place 와 동일
        truck_xyz_center_mm: 컴포넌트 중심을 둘 위치 (적재함 중심 원점)
        collision_grid: CollisionGrid 인스턴스 (선택). 주어지면 그리드로 근접
                        owner 만 추출해 페어와이즈 검사. None 이면 placements_boxes
                        를 통째로 검사.
        placements_boxes: 그리드 없을 때 fallback — 기존 placement 의 박스 리스트.
                          [(owner_id, [box, ...]), ...] 형태.
    """
    from .collision import boxes_gap_ok  # 지연 import (순환 방지)

    reasons: List[str] = []
    L_nom, W_nom, H_nom = _nominal_dims_for_posture(item, posture)

    # 좌하단 좌표
    x0, y0, z0 = _trip_xyz_from_truck_center(
        truck_xyz_center_mm, L_nom, W_nom, truck_state.truck,
    )

    # 새 컴포넌트의 부재 박스
    new_boxes = boxes_of_component(item, posture, (x0, y0, z0))
    if not new_boxes:
        return CheckResult(ok=False, reasons=("부재 박스 비어있음",))

    # 외곽 AABB
    xs0 = [b[0] for b in new_boxes]; ys0 = [b[1] for b in new_boxes]; zs0 = [b[2] for b in new_boxes]
    xs1 = [b[3] for b in new_boxes]; ys1 = [b[4] for b in new_boxes]; zs1 = [b[5] for b in new_boxes]
    outer_x0, outer_y0, outer_z0 = min(xs0), min(ys0), min(zs0)
    outer_x1, outer_y1, outer_z1 = max(xs1), max(ys1), max(zs1)

    tr = truck_state.truck
    edge = sp.truck_edge_clearance_mm
    side_over = sp.side_overhang_mm
    inner_h = tr.max_height - tr.vehicle_height_offset

    # ① 트럭 길이 한도 (앞뒤 100mm 결박 여유)
    if outer_x0 < edge - 1e-3:
        reasons.append(
            f"트럭 길이 앞 한도 위반: x_min {outer_x0:.0f} < {edge:.0f}mm 여유")
    if outer_x1 > tr.max_length - edge + 1e-3:
        reasons.append(
            f"트럭 길이 뒤 한도 위반: x_max {outer_x1:.0f} > "
            f"{tr.max_length - edge:.0f}mm (적재함 - 여유)")

    # ② 트럭 폭 한도 (양쪽 ±200mm 돌출 허용)
    if outer_y0 < -side_over - 1e-3:
        reasons.append(
            f"폭 좌측 한도 위반: y_min {outer_y0:.0f} < -{side_over:.0f}mm 돌출")
    if outer_y1 > tr.max_width + side_over + 1e-3:
        reasons.append(
            f"폭 우측 한도 위반: y_max {outer_y1:.0f} > "
            f"{tr.max_width + side_over:.0f}mm 한도")

    # ③ 트럭 내공 높이
    if outer_z1 > inner_h + 1e-3:
        reasons.append(
            f"내공 높이 초과: z_max {outer_z1:.0f} > 내공 {inner_h:.0f}mm")

    # ④ 현장 한도 — 폭·외측 높이
    box_w = outer_y1 - outer_y0
    if site.max_width_mm is not None and box_w > site.max_width_mm + 1e-3:
        reasons.append(
            f"현장 폭 한도: {box_w:.0f} > {site.max_width_mm:.0f}mm")
    if site.max_height_mm is not None:
        outer_total = outer_z1 + tr.vehicle_height_offset
        if outer_total > site.max_height_mm + 1e-3:
            reasons.append(
                f"현장 외측 높이 한도: {outer_total:.0f} > {site.max_height_mm:.0f}mm")

    # ⑤ 무게 한도 — 누적 + 새 화물
    new_weight = float(getattr(item, "weight", 0.0))
    if (truck_state.used_cargo_weight + new_weight
            > truck_state.effective_cargo_limit + 1e-3):
        reasons.append(
            f"무게 한도 초과: 누적 {truck_state.used_cargo_weight:.0f} + 새 "
            f"{new_weight:.0f} > 한도 {truck_state.effective_cargo_limit:.0f}kg")

    # ⑥ 부재 페어와이즈 충돌 검사 (다른 컴포넌트와 갭 100mm)
    candidates = None
    if collision_grid is not None:
        # 새 박스 외곽 + 100mm margin 으로 후보 owner 조회
        outer_box: Tuple[float, float, float, float, float, float] = (
            outer_x0, outer_y0, outer_z0, outer_x1, outer_y1, outer_z1,
        )
        owner_ids = collision_grid.query_near(outer_box, margin_mm=GAP_MM)
        candidates = [
            (oid, collision_grid.boxes_of(oid)) for oid in owner_ids
        ]
    elif placements_boxes is not None:
        candidates = placements_boxes
    else:
        candidates = []

    for owner_id, other_boxes in candidates:
        for nb in new_boxes:
            for ob in other_boxes:
                if not boxes_gap_ok(nb, ob, GAP_MM):
                    reasons.append(
                        f"컴포넌트 #{owner_id} 와 갭 부족 (< {int(GAP_MM)}mm)")
                    # 한 컴포넌트 충돌 발견 → 다음 owner 로 (사유는 1번만 표시)
                    break
            else:
                continue
            break  # owner_id 충돌 발견 → 다음 owner

    return CheckResult(ok=not reasons, reasons=tuple(reasons))
