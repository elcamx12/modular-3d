"""모듈·패널 → 운송 회차 산정 (FFD 빈 패킹).

운송프로그램 원본(`src/packer.py`, Song-Jung-Hun/-3-) 이식 + Phase 2 확장.

[원본과의 차이 — Phase 2 변경점]
- **A-1 일반화**: `_pack_lshape_panels` → `_pack_dependent_panels`. 운송프로그램
  원본의 `kind="lshape"` (벽 1면 종속) 분기를 폐기하고, `Panel.wall_segments`
  리스트의 길이/내용에 따라 0/1/2/3/4 면 종속 + 부분 벽 모두 동일 함수에서
  처리. 어댑터(Phase 3) 가 floor + wall_segments 형태로 만들어 전달.
- **B-2 정정 (벽두께 ≠ 바닥두께)**: 원본 `_can_stack_on_lshape` 는 lshape 의
  `panel.thickness` 를 벽체 두께로 오인했으나, 우리는 각 wall_segment 의
  `thickness_mm` 를 직접 사용해 적층 내공 폭 계산.
- **B-3 정정 (recheck 적층 무게 합산)**: 원본 `recheck_trip_with_truck` 의
  모듈/플로어/벽 분기에서 stacked_items 무게를 합산하지 않음 → 우리는
  `_trip_cargo_weight(trip)` 헬퍼로 base + stacked 모두 합산.
- **B-5 정정 (ppr 재계산)**: 원본은 빈 첫 패널의 길이로 ppr 을 고정. 사양이
  다른 패널이 혼적되면 잘못된 ppr 이 남음. 우리는 bin 내 패널을 합집합으로
  재계산: `ppr = floor((usable + gap) / (max(len) + gap))`.
- **B-13 옵션화 (적층 길이 베이스 기준 토글)**: 원본 `_can_stack_on_lshape` 의
  길이 검사는 트럭 유효 길이까지 허용 (코드 주석에 "L자 개별 길이에 제한 없음"
  명시된 의도된 단순화). 우리는 `strict_stack_length=True` 일 때만 베이스
  길이 이내로 엄격 검사. 기본은 원본 정책 유지.
- **B-14 정밀도 (적층 L자 벽 두께)**: A-1 일반화로 자동 해결 — wall_segments
  각각의 thickness/height 가 정확히 반영됨.
- **B-25 정정 (cargo vs total 분리)**: Trip.cargo_weight = 화물 합산 (적층
  포함), `Trip.gross_weight = cargo + truck.curb_weight_kg` 추가. 원본은
  total_weight 가 cargo_weight 와 같았는데 의미 충돌이라 분리.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Union

from .limits import can_carry
from .models import (
    Module, Panel, RoadClass, SpacingParams, Truck, WallSegment,
)


Item = Union[Module, Panel]


# ── Trip / PackResult ─────────────────────────────────────────────
@dataclass
class Trip:
    trip_no: int
    truck: Truck
    items: List[Item] = field(default_factory=list)
    wide_check: bool = False
    blocked_reason: Optional[str] = None
    panels_per_row: int = 1
    n_layers: int = 1
    used_length_mm: float = 0.0
    usable_length_mm: float = 0.0
    # 종속 패널 회차: stacked_items[i] = items[i] 위에 올린 Panel 또는 None
    stacked_items: list = field(default_factory=list)

    @property
    def cargo_weight(self) -> float:
        """화물 무게 합산 (base + stacked). B-25 정정 — 적층 무게 누락 없음."""
        base_w = sum(getattr(i, "weight", 0.0) for i in self.items)
        stacked_w = sum(
            getattr(s, "weight", 0.0) for s in self.stacked_items if s is not None
        )
        return base_w + stacked_w

    @property
    def gross_weight(self) -> float:
        """차체 + 화물 (GVW). B-25 신규 — 도로 한도 비교용."""
        return self.cargo_weight + (self.truck.curb_weight_kg or 0.0)

    @property
    def total_weight(self) -> float:
        """[deprecated] 원본 호환 — cargo_weight 와 동일. 신규 코드는 gross_weight 사용."""
        return self.cargo_weight

    @property
    def weight_utilization(self) -> float:
        if self.truck.max_weight <= 0:
            return 0.0
        return self.cargo_weight / self.truck.max_weight * 100.0

    @property
    def length_utilization(self) -> float:
        if self.usable_length_mm <= 0:
            return 0.0
        return self.used_length_mm / self.usable_length_mm * 100.0

    @property
    def utilization(self) -> float:
        return max(self.weight_utilization, self.length_utilization)

    @property
    def kind(self) -> str:
        if not self.items:
            return "empty"
        if isinstance(self.items[0], Module):
            return "module"
        # 다면 종속 (wall_segments 가 있는 floor) 도 panel 로 일괄 분류.
        return "panel"


@dataclass
class PackResult:
    trips: List[Trip]
    blocked: List[tuple] = field(default_factory=list)

    @property
    def total_trips(self) -> int:
        return len(self.trips)

    @property
    def module_trips(self) -> int:
        return sum(1 for t in self.trips if t.kind == "module")

    @property
    def panel_trips(self) -> int:
        return sum(1 for t in self.trips if t.kind == "panel")

    @property
    def avg_utilization(self) -> float:
        if not self.trips:
            return 0.0
        return sum(t.utilization for t in self.trips) / len(self.trips)


# ── 트럭 호환성 (active 필터는 호출 측에서 처리) ─────────────────
def _module_compatible_trucks(trucks: List[Truck]) -> List[Truck]:
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _floor_panel_compatible_trucks(trucks: List[Truck]) -> List[Truck]:
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _wall_panel_compatible_trucks(trucks: List[Truck]) -> List[Truck]:
    # B-4: aframe 제외 (원본 정책 유지). active=false 처리도 호출 측이.
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _dependent_compatible_trucks(trucks: List[Truck]) -> List[Truck]:
    """A-1: lshape/ㄷ자/3면/4면/부분벽 종속 floor 모두 동일 트럭."""
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


# ── A-1 종속 floor 패널 헬퍼 ──────────────────────────────────────
def _dependent_max_thickness(p: Panel) -> float:
    """종속 패널 1매 점유 두께 = floor 바닥(thickness) + 가장 높은 wall segment.

    B-14 정밀도: wall_segments 의 각 thickness/height 가 정확히 반영됨.
    """
    if not p.wall_segments:
        return p.thickness
    return p.thickness + max(s.height_mm for s in p.wall_segments)


def _dependent_free_inner_dims(p: Panel, sp: SpacingParams) -> tuple[float, float]:
    """wall_segments 가 점유한 변(과 두께)을 뺀 적층 가능 내공 (width, length).

    side 0/2 (하변·상변) 의 벽은 length 방향 양끝 공간 점유 → width 감소.
    side 1/3 (우변·좌변) 의 벽은 length 방향 양끝 점유 → length 감소.
    (변 인덱스 정의: 0=하변(y=0, 길이=length), 1=우변(x=W, 길이=width),
     2=상변(y=L, 길이=length), 3=좌변(x=0, 길이=width))

    같은 side 에 여러 세그가 있으면 그 중 최대 두께만 사용 (보수적).
    """
    max_th_by_side = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    for s in p.wall_segments:
        if s.thickness_mm > max_th_by_side[s.side]:
            max_th_by_side[s.side] = s.thickness_mm
    # 0/2 변 벽 → width 차감
    width_taken = max_th_by_side[0] + max_th_by_side[2]
    # 1/3 변 벽 → length 차감
    length_taken = max_th_by_side[1] + max_th_by_side[3]
    # gap (사용자 정의 lshape_stack_gap_mm — 벽 안쪽 여유)
    gap_w = sp.lshape_stack_gap_mm if width_taken > 0 else 0.0
    gap_l = sp.lshape_stack_gap_mm if length_taken > 0 else 0.0
    free_w = p.width - width_taken - gap_w
    free_l = p.length - length_taken - gap_l
    return free_w, free_l


def _can_stack_on_dependent(
    cand: Panel,
    base: Panel,
    truck: Truck,
    sp: SpacingParams,
    strict_stack_length: bool = False,
) -> bool:
    """cand 패널을 base(종속 floor) 위에 적층 가능한가?

    조건:
      ① 폭/길이: cand 가 base 의 wall_segments 가 점유한 영역을 피해 안쪽
         빈 공간에 들어가야 함. strict_stack_length=False (B-13 단순화 정책
         유지) 면 길이 검사는 트럭 유효 길이까지 허용.
      ② 높이: base 단독 점유 높이와 (base 바닥 + gap + cand 점유 높이) 중
         큰 값이 트럭 내공 높이를 넘지 않아야 함.
    """
    free_w, free_l = _dependent_free_inner_dims(base, sp)

    # ① 폭
    if free_w <= 0 or cand.width > free_w:
        return False

    # ② 길이
    if strict_stack_length:
        if free_l <= 0 or cand.length > free_l:
            return False
    else:
        usable = truck.max_length - 2 * sp.truck_edge_clearance_mm
        if cand.length > usable:
            return False

    # ③ 높이
    inner_h = truck.max_height - truck.vehicle_height_offset
    base_solo_h = _dependent_max_thickness(base)
    cand_h = _dependent_max_thickness(cand)
    stk_h = base.thickness + sp.panel_gap_mm + cand_h
    cargo_h = max(base_solo_h, stk_h)
    return cargo_h <= inner_h


# ── 트럭 단일사양 최대 적재 헬퍼 (recheck 진단용) ─────────────────
def _max_modules_per_truck(module: Module, truck: Truck, spacing: SpacingParams) -> int:
    if module.width > truck.max_width:
        return 0
    if module.height + truck.vehicle_height_offset > truck.max_height:
        return 0
    usable = truck.max_length - 2 * spacing.truck_edge_clearance_mm
    if module.length > usable:
        return 0
    n_len = max(int((usable + spacing.panel_gap_mm) // (module.length + spacing.panel_gap_mm)), 1)
    n_wt = math.floor(truck.max_weight / module.weight) if module.weight > 0 else n_len
    return min(n_len, n_wt)


def _max_floor_panels_per_truck(
    panel: Panel, truck: Truck, sp: SpacingParams
) -> tuple[int, int, int]:
    if panel.width > truck.max_width:
        return 0, 0, 0
    usable_len = truck.max_length - 2 * sp.truck_edge_clearance_mm
    if panel.length > usable_len:
        return 0, 0, 0
    ppr = max(int((usable_len + sp.panel_gap_mm) // (panel.length + sp.panel_gap_mm)), 1)
    inner_h = truck.max_height - truck.vehicle_height_offset
    if panel.thickness > inner_h:
        return 0, ppr, 0
    nl = max(int((inner_h + sp.panel_gap_mm) // (panel.thickness + sp.panel_gap_mm)), 1)
    n_vol = ppr * nl
    if panel.weight <= 0:
        return n_vol, ppr, nl
    n_wt = math.floor(truck.max_weight / panel.weight)
    return min(n_vol, n_wt), ppr, nl


def _max_dependent_per_truck(panel: Panel, truck: Truck, sp: SpacingParams) -> int:
    """단일사양 종속 floor 1트럭 기저 배치 최대 매수 (적층 미포함)."""
    if panel.width > truck.max_width:
        return 0
    if _dependent_max_thickness(panel) + truck.vehicle_height_offset > truck.max_height:
        return 0
    usable = truck.max_length - 2 * sp.truck_edge_clearance_mm
    if panel.length > usable:
        return 0
    n_len = max(int((usable + sp.panel_gap_mm) // (panel.length + sp.panel_gap_mm)), 1)
    if panel.weight <= 0:
        return n_len
    n_wt = math.floor(truck.max_weight / panel.weight)
    return min(n_len, n_wt)


# ── 트럭 선택 ─────────────────────────────────────────────────────
def _closest_fit_truck(ok_trucks: List[Truck], ref_length: float, ref_weight: float) -> Truck:
    """정규화 여유 합이 최소인 트럭 (큰 트럭 낭비 방지)."""
    def score(tr: Truck) -> float:
        len_excess = (tr.max_length - ref_length) / max(ref_length, 1.0)
        wt_excess = (tr.max_weight - ref_weight) / max(ref_weight, 1.0)
        return len_excess + wt_excess
    return min(ok_trucks, key=score)


# ── 모듈 패킹 (1 트럭 = 1 모듈) ───────────────────────────────────
def _pack_modules(
    modules: List[Module],
    trucks: List[Truck],
    road: RoadClass,
    spacing: SpacingParams,
    start_trip_no: int = 1,
    strict_weight: bool = False,
    strict_length: bool = False,
) -> tuple[List[Trip], list]:
    if not modules:
        return [], []
    compat = _module_compatible_trucks(trucks)
    if not compat:
        return [], [(m, "모듈 운송 가능 트럭 없음 (lowbed/extendable 필요)") for m in modules]

    blocked: list = []
    trips: List[Trip] = []
    next_no = start_trip_no

    for m in modules:
        ok_trucks = [
            tr for tr in compat
            if can_carry(m, tr, road, strict_weight=strict_weight,
                         strict_length=strict_length).ok
            and m.width <= tr.max_width
            and m.height + tr.vehicle_height_offset <= tr.max_height
            and m.length <= tr.max_length - 2 * spacing.truck_edge_clearance_mm
        ]
        if not ok_trucks:
            blocked.append((m, "모듈 규격이 모든 트럭/도로 한도 초과"))
            continue
        best = _closest_fit_truck(ok_trucks, m.length, m.weight)
        usable = best.max_length - 2 * spacing.truck_edge_clearance_mm
        trips.append(Trip(
            trip_no=next_no, truck=best, items=[m],
            wide_check=m.is_wide(),
            panels_per_row=1, n_layers=1,
            used_length_mm=m.length, usable_length_mm=usable,
        ))
        next_no += 1
    return trips, blocked


# ── 패널 공용 FFD (floor / wall) ──────────────────────────────────
def _recompute_ppr(items: list, sp: SpacingParams, usable_len: float) -> int:
    """B-5 정정: 혼적 시 sample 첫 패널이 아닌 가장 긴 패널 기준으로 ppr 재계산."""
    if not items:
        return 1
    max_len = max(p.length for p in items)
    return max(int((usable_len + sp.panel_gap_mm) // (max_len + sp.panel_gap_mm)), 1)


def _pack_horizontal_stacked(
    panels: List[Panel],
    trucks: List[Truck],
    road: RoadClass,
    sp: SpacingParams,
    start_trip_no: int,
    label: str,
    compat_fn,
    strict_weight: bool = False,
    strict_length: bool = False,
) -> tuple[List[Trip], list]:
    """플로어/벽 패널 공용 FFD — 무게 내림차순, 눕혀서 적층, 혼적 허용."""
    if not panels:
        return [], []
    compat = compat_fn(trucks)
    if not compat:
        return [], [(p, f"{label} 운송 가능 트럭 없음") for p in panels]

    blocked: list = []
    valid: list = []
    for p in panels:
        ok = [tr for tr in compat
              if can_carry(p, tr, road, strict_weight=strict_weight,
                           strict_length=strict_length).ok
              and p.width <= tr.max_width]
        if ok:
            valid.append((p, ok))
        else:
            blocked.append((p, "운송 가능 트럭 없음"))
    if not valid:
        return [], blocked

    valid.sort(key=lambda x: x[0].weight, reverse=True)

    bins: list = []
    next_no = start_trip_no
    for p, ok_trucks in valid:
        placed = False
        for b in bins:
            if b["truck"] not in ok_trucks:
                continue
            tr = b["truck"]
            usable_len = tr.max_length - 2 * sp.truck_edge_clearance_mm
            if p.length > usable_len:
                continue
            # B-5: 새로 추가할 p 까지 포함한 사양으로 ppr 재계산
            future_items = b["items"] + [p]
            ppr = _recompute_ppr(future_items, sp, usable_len)
            new_n = len(future_items)
            new_layers = math.ceil(new_n / ppr)
            inner_h = tr.max_height - tr.vehicle_height_offset
            max_thick = max(pi.thickness for pi in future_items)
            stack_h = new_layers * max_thick + max(new_layers - 1, 0) * sp.panel_gap_mm
            if stack_h > inner_h:
                continue
            if b["total_cargo"] + p.weight > tr.max_weight:
                continue
            b["items"].append(p)
            b["total_cargo"] += p.weight
            b["n_layers"] = new_layers
            b["panels_per_row"] = ppr
            placed = True
            break
        if not placed:
            ok_for_new = [tr for tr in ok_trucks
                          if p.length <= tr.max_length - 2 * sp.truck_edge_clearance_mm]
            if not ok_for_new:
                blocked.append((p, f"{label} 길이가 트럭 유효 길이 초과"))
                continue
            best = _closest_fit_truck(ok_for_new, p.length, p.weight)
            _, ppr, _ = _max_floor_panels_per_truck(p, best, sp)
            ppr = max(ppr, 1)
            bins.append({
                "truck": best, "items": [p], "total_cargo": p.weight,
                "panels_per_row": ppr, "n_layers": 1,
            })

    trips: List[Trip] = []
    for b in bins:
        usable = b["truck"].max_length - 2 * sp.truck_edge_clearance_mm
        ppr = b["panels_per_row"]
        max_len = max(pi.length for pi in b["items"])
        used_l = ppr * max_len + max(0, ppr - 1) * sp.panel_gap_mm
        trips.append(Trip(
            trip_no=next_no, truck=b["truck"], items=b["items"],
            panels_per_row=ppr, n_layers=b["n_layers"],
            used_length_mm=used_l, usable_length_mm=usable,
        ))
        next_no += 1
    return trips, blocked


def _pack_floor_panels(panels, trucks, road, sp, start_trip_no,
                       strict_weight=False, strict_length=False):
    return _pack_horizontal_stacked(
        panels, trucks, road, sp, start_trip_no,
        "플로어 패널", _floor_panel_compatible_trucks,
        strict_weight=strict_weight, strict_length=strict_length,
    )


def _pack_wall_panels(panels, trucks, road, sp, start_trip_no,
                      strict_weight=False, strict_length=False):
    return _pack_horizontal_stacked(
        panels, trucks, road, sp, start_trip_no,
        "벽체 패널", _wall_panel_compatible_trucks,
        strict_weight=strict_weight, strict_length=strict_length,
    )


# ── A-1 종속 floor 패널 (L자/ㄷ자/3면/4면/부분벽 통합) ─────────────
def _pack_dependent_panels(
    panels: List[Panel],
    trucks: List[Truck],
    road: RoadClass,
    sp: SpacingParams,
    start_trip_no: int,
    stacking_candidates: Optional[List[Panel]] = None,
    strict_stack_length: bool = False,
    strict_weight: bool = False,
    strict_length: bool = False,
) -> tuple[List[Trip], list, List[Panel]]:
    """A-1 일반화: wall_segments 가 있는 모든 floor 패널 + 원본 lshape 통합.

    Returns: (trips, blocked, remaining_candidates)
    """
    sc = list(stacking_candidates) if stacking_candidates else []
    if not panels:
        return [], [], sc

    compat = _dependent_compatible_trucks(trucks)
    if not compat:
        return ([],
                [(p, "종속 패널 운송 가능 트럭 없음 (lowbed/extendable 필요)") for p in panels],
                sc)

    blocked: list = []
    valid: list = []
    for p in panels:
        ok = [tr for tr in compat
              if can_carry(p, tr, road, strict_weight=strict_weight,
                           strict_length=strict_length).ok
              and p.width <= tr.max_width]
        if ok:
            valid.append((p, ok))
        else:
            blocked.append((p, "운송 가능 트럭 없음"))
    if not valid:
        return [], blocked, sc

    valid.sort(key=lambda x: x[0].length, reverse=True)

    bins: list = []
    for p, ok_trucks in valid:
        placed = False

        # ① 기존 bin 의 빈 슬롯 위에 적층 (종속 위에 종속)
        if p.wall_segments or p.kind == "lshape":
            for b in bins:
                if b["truck"] not in ok_trucks:
                    continue
                for i, (base, slot) in enumerate(zip(b["base_items"], b["stacked_items"])):
                    if slot is not None:
                        continue
                    if not _can_stack_on_dependent(p, base, b["truck"], sp,
                                                    strict_stack_length=strict_stack_length):
                        continue
                    if b["total_weight"] + p.weight > b["truck"].max_weight:
                        continue
                    b["stacked_items"][i] = p
                    b["total_weight"] += p.weight
                    placed = True
                    break
                if placed:
                    break

        # ② 기존 bin 트럭 바닥에 나란히 추가
        if not placed:
            for b in bins:
                if b["truck"] not in ok_trucks:
                    continue
                tr = b["truck"]
                usable = tr.max_length - 2 * sp.truck_edge_clearance_mm
                gap = sp.panel_gap_mm if b["base_items"] else 0.0
                if b["used_length"] + gap + p.length > usable:
                    continue
                if p.width > tr.max_width:
                    continue
                if _dependent_max_thickness(p) + tr.vehicle_height_offset > tr.max_height:
                    continue
                if b["total_weight"] + p.weight > tr.max_weight:
                    continue
                b["base_items"].append(p)
                b["stacked_items"].append(None)
                b["used_length"] += gap + p.length
                b["total_weight"] += p.weight
                placed = True
                break

        # ③ 새 bin
        if not placed:
            ok_for_new = [
                tr for tr in ok_trucks
                if (p.length <= tr.max_length - 2 * sp.truck_edge_clearance_mm
                    and p.width <= tr.max_width
                    and _dependent_max_thickness(p) + tr.vehicle_height_offset <= tr.max_height)
            ]
            if not ok_for_new:
                blocked.append((p, "종속 패널 길이/폭/높이가 트럭 한도 초과"))
                continue
            best = _closest_fit_truck(ok_for_new, p.length, p.weight)
            bins.append({
                "truck": best,
                "base_items": [p], "stacked_items": [None],
                "used_length": p.length, "total_weight": p.weight,
            })

    # 적층 후보 패널 (플로어/벽) 을 빈 슬롯에 배치
    remaining_candidates: List[Panel] = []
    if sc:
        cands_sorted = sorted(sc, key=lambda x: x.weight, reverse=True)
        for cand in cands_sorted:
            placed = False
            for b in bins:
                for i, (base, slot) in enumerate(zip(b["base_items"], b["stacked_items"])):
                    if slot is not None:
                        continue
                    if not _can_stack_on_dependent(cand, base, b["truck"], sp,
                                                    strict_stack_length=strict_stack_length):
                        continue
                    if b["total_weight"] + cand.weight > b["truck"].max_weight:
                        continue
                    b["stacked_items"][i] = cand
                    b["total_weight"] += cand.weight
                    placed = True
                    break
                if placed:
                    break
            if not placed:
                remaining_candidates.append(cand)

    trips: List[Trip] = []
    next_no = start_trip_no
    for b in bins:
        usable = b["truck"].max_length - 2 * sp.truck_edge_clearance_mm
        ppr = len(b["base_items"])
        has_stacked = any(s is not None for s in b["stacked_items"])
        trips.append(Trip(
            trip_no=next_no, truck=b["truck"], items=b["base_items"],
            panels_per_row=ppr, n_layers=2 if has_stacked else 1,
            used_length_mm=b["used_length"], usable_length_mm=usable,
            stacked_items=b["stacked_items"],
        ))
        next_no += 1
    return trips, blocked, remaining_candidates


# ── recheck (트럭 교체) ───────────────────────────────────────────
def _trip_cargo_weight_with_extra(trip: Trip, extra: float = 0.0) -> float:
    """B-3 정정: stacked_items 무게를 포함한 화물 합."""
    return trip.cargo_weight + extra


def _panel_overcount_reason(
    n: int, max_n: int, ppr: int, nl: int,
    sample: Panel, new_truck: Truck, spacing: SpacingParams,
) -> str:
    """패널 적재 불가 원인을 사람이 읽기 쉬운 문자열로 진단 (원본 이식 — D2).

    ppr·nl 은 _max_floor_panels_per_truck 반환값을 그대로 전달.
    """
    inner_h = new_truck.max_height - new_truck.vehicle_height_offset
    usable_len = new_truck.max_length - 2 * spacing.truck_edge_clearance_mm

    # 원인 ① 패널 폭 > 트럭 폭
    if sample.width > new_truck.max_width:
        return (
            f"❌ 패널 폭이 트럭 폭보다 넓습니다\n"
            f"  • 패널 폭 {sample.width:.0f}mm > 트럭 최대 폭 {new_truck.max_width:.0f}mm"
        )
    # 원인 ② 패널 길이 > 트럭 유효 길이
    if ppr == 0 or sample.length > usable_len:
        return (
            f"❌ 트럭 적재 공간이 너무 짧습니다\n"
            f"  • 패널 길이 {sample.length:.0f}mm > 유효 적재 길이 {usable_len:.0f}mm\n"
            f"  • (트럭 {new_truck.max_length:.0f}mm − 양끝 여유 {spacing.truck_edge_clearance_mm:.0f}mm × 2)"
        )
    # 원인 ③ 패널 두께 > 내측 높이
    if nl == 0 or sample.thickness > inner_h:
        return (
            f"❌ 패널 두께가 내측 높이를 초과합니다\n"
            f"  • 패널 두께 {sample.thickness:.0f}mm > 내측 높이 {inner_h:.0f}mm\n"
            f"  • (트럭 {new_truck.max_height:.0f}mm − 차체 {new_truck.vehicle_height_offset:.0f}mm)"
        )
    # 원인 ④ 높이(적층 단수) vs 중량 병목
    stack_h = nl * sample.thickness + max(nl - 1, 0) * spacing.panel_gap_mm
    max_by_wt = math.floor(new_truck.max_weight / sample.weight) if sample.weight > 0 else n
    if max_n <= max_by_wt:
        return (
            f"❌ 적층 높이 초과 ({n}매 요청 / 최대 {max_n}매)\n"
            f"  • 내측 높이 {inner_h:.0f}mm = 트럭 {new_truck.max_height:.0f}mm − 차체 {new_truck.vehicle_height_offset:.0f}mm\n"
            f"  • 최대 {nl}단 × {ppr}열 = {nl * ppr}매 (높이 {stack_h:.0f}mm)"
        )
    total_req = n * sample.weight
    return (
        f"❌ 중량 초과 ({n}매 요청 / 최대 {max_n}매)\n"
        f"  • {n}매 × {sample.weight:.0f}kg/매 = {total_req:.0f}kg\n"
        f"  • 트럭 적재한도 {new_truck.max_weight:.0f}kg → 최대 {max_by_wt}매"
    )


def recheck_trip_with_truck(
    trip: Trip, new_truck: Truck, road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    strict_weight: bool = False, strict_length: bool = False,
) -> tuple[bool, str, Optional[Trip]]:
    """주어진 trip 의 화물을 new_truck 에 그대로 실을 수 있나 검사."""
    if not trip.items:
        return True, "(빈 회차)", trip

    sample = trip.items[0]
    if trip.kind == "module":
        if new_truck.truck_type not in ("lowbed", "extendable"):
            return False, (
                f"❌ 모듈은 lowbed / extendable 트럭에만 적재 가능\n"
                f"  • 선택한 트럭 종류: {new_truck.truck_type}"
            ), None
    else:
        if new_truck.truck_type not in ("lowbed", "extendable"):
            return False, (
                f"❌ 패널은 lowbed / extendable 트럭에만 적재 가능\n"
                f"  • 선택한 트럭 종류: {new_truck.truck_type}"
            ), None

    # 각 아이템 4 조건 검사
    for item in list(trip.items) + [s for s in trip.stacked_items if s is not None]:
        r = can_carry(item, new_truck, road,
                      strict_weight=strict_weight, strict_length=strict_length)
        if not r.ok:
            return False, "❌ 도로/트럭 한도 초과\n  • " + "\n  • ".join(r.reasons), None

    # B-3: 적층 무게 포함한 총 화물 합산
    total_cargo = trip.cargo_weight
    if total_cargo > new_truck.max_weight:
        return False, (
            f"❌ 중량 초과(적층 포함)\n"
            f"  • 화물 합계 {total_cargo:.0f}kg > 트럭 적재한도 {new_truck.max_weight:.0f}kg"
        ), None

    usable = new_truck.max_length - 2 * spacing.truck_edge_clearance_mm
    n = len(trip.items)

    if trip.kind == "module":
        # [A1] 새 트럭 길이 재검사 — 단일행 모듈 길이 합 ≤ 유효 길이.
        # (1 트럭 = 1 모듈이 기본이지만 일반화해 다중 모듈도 안전 검사.)
        total_len = (sum(getattr(m, "length", 0.0) for m in trip.items)
                     + max(0, n - 1) * spacing.panel_gap_mm)
        if total_len > usable + 1e-6:
            return False, (
                f"❌ 길이 초과\n"
                f"  • 모듈 길이 합계 {total_len:.0f}mm > 유효 적재 길이 {usable:.0f}mm\n"
                f"  • (트럭 {new_truck.max_length:.0f}mm − 양끝 여유 "
                f"{spacing.truck_edge_clearance_mm:.0f}mm×2)"
            ), None
        new_trip = Trip(
            trip_no=trip.trip_no, truck=new_truck, items=list(trip.items),
            wide_check=any(isinstance(i, Module) and i.is_wide() for i in trip.items),
            panels_per_row=n, n_layers=1,
            used_length_mm=total_len, usable_length_mm=usable,
        )
        return True, "OK", new_trip

    # [A1] panel / dependent — 새 트럭에 대해 적재 매수(다열×다단)를 재계산해
    # 초과를 거부 + 사용 길이를 다시 계산한다 (원본 동등 — 작은 트럭 override 시
    # 패널이 실제로 안 맞는데 통과하던 결함 수정).
    is_dependent = bool(getattr(sample, "wall_segments", None))
    if is_dependent:
        max_n = _max_dependent_per_truck(sample, new_truck, spacing)
        if n > max_n:
            return False, (
                f"❌ 적재 불가 ({n}매 요청 / 최대 {max_n}매)\n"
                f"  • 새 트럭 '{new_truck.name}' 종속 패널 용량 초과"
            ), None
        # 적층 패널도 새 트럭에서 적층 조건 만족하는지 검사
        for i, stk in enumerate(trip.stacked_items):
            if stk is not None and i < n:
                if not _can_stack_on_dependent(stk, trip.items[i], new_truck, spacing):
                    return False, (
                        f"❌ 적층 패널 '{getattr(stk, 'name', '?')}' 이 새 트럭에서 "
                        f"적층 조건을 만족하지 않습니다."
                    ), None
        used_l = n * sample.length + max(0, n - 1) * spacing.panel_gap_mm
        new_trip = Trip(
            trip_no=trip.trip_no, truck=new_truck, items=list(trip.items),
            panels_per_row=n, n_layers=trip.n_layers,
            used_length_mm=used_l, usable_length_mm=usable,
            stacked_items=list(trip.stacked_items),
        )
        return True, "OK", new_trip

    # 순수 floor / 독립 wall — 다열×다단 재계산
    max_n, ppr, nl = _max_floor_panels_per_truck(sample, new_truck, spacing)
    if n > max_n:
        return False, _panel_overcount_reason(n, max_n, ppr, nl, sample, new_truck, spacing), None
    used_layers = math.ceil(n / ppr) if ppr > 0 else 1
    used_l = ppr * sample.length + max(0, ppr - 1) * spacing.panel_gap_mm
    new_trip = Trip(
        trip_no=trip.trip_no, truck=new_truck, items=list(trip.items),
        panels_per_row=ppr, n_layers=used_layers,
        used_length_mm=used_l, usable_length_mm=usable,
    )
    return True, "OK", new_trip


# ── 메인 진입점 ───────────────────────────────────────────────────
def pack_items(
    modules: List[Module],
    panels: List[Panel],
    trucks: List[Truck],
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    strict_weight: bool = False,
    strict_length: bool = False,
    strict_stack_length: bool = False,
) -> PackResult:
    """모듈·패널 → 회차 산정.

    1) 모듈 (1 트럭 = 1 모듈)
    2) 종속 floor (wall_segments 있음) + 원본 lshape — 우선 배치 + 적층 시도
    3) 남은 floor (순수)
    4) 남은 wall (독립 벽)

    [엄격 모드 옵션]
    - strict_weight/length: limits.can_carry 의 GVW·전장 정밀화.
    - strict_stack_length: 적층 길이 검사를 베이스 길이로 엄격 (B-13).
    """
    trips: List[Trip] = []
    blocked: list = []
    next_no = 1

    # 분류
    dependent_panels = [p for p in panels
                        if (p.kind == "floor" and p.wall_segments)
                        or p.kind == "lshape"]
    floor_panels = [p for p in panels if p.kind == "floor" and not p.wall_segments]
    wall_panels = [p for p in panels if p.kind == "wall"]

    # 1) 모듈
    mod_trips, mod_blocked = _pack_modules(
        modules, trucks, road, spacing, start_trip_no=next_no,
        strict_weight=strict_weight, strict_length=strict_length,
    )
    trips.extend(mod_trips)
    blocked.extend(mod_blocked)
    next_no += len(mod_trips)

    # 2) 종속 패널 + 적층 후보 (floor + wall)
    stacking_candidates = floor_panels + wall_panels
    dep_trips, dep_blocked, remaining = _pack_dependent_panels(
        dependent_panels, trucks, road, spacing, next_no,
        stacking_candidates=stacking_candidates if stacking_candidates else None,
        strict_stack_length=strict_stack_length,
        strict_weight=strict_weight, strict_length=strict_length,
    )
    trips.extend(dep_trips)
    blocked.extend(dep_blocked)
    next_no += len(dep_trips)

    rem_floor = [p for p in remaining if p.kind == "floor"]
    rem_wall = [p for p in remaining if p.kind == "wall"]

    # 3) 순수 floor
    fl_trips, fl_blocked = _pack_floor_panels(
        rem_floor, trucks, road, spacing, next_no,
        strict_weight=strict_weight, strict_length=strict_length,
    )
    trips.extend(fl_trips)
    blocked.extend(fl_blocked)
    next_no += len(fl_trips)

    # 4) 독립 wall
    wl_trips, wl_blocked = _pack_wall_panels(
        rem_wall, trucks, road, spacing, next_no,
        strict_weight=strict_weight, strict_length=strict_length,
    )
    trips.extend(wl_trips)
    blocked.extend(wl_blocked)

    return PackResult(trips=trips, blocked=blocked)


__all__ = [
    "Trip", "PackResult", "pack_items", "recheck_trip_with_truck",
]
