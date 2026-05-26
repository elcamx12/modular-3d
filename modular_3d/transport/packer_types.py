"""Phase 2 — 새 패커의 데이터 구조 정의.

[설계 근거]
- `운송 로직 의사코드.md` § 2.1 참조.
- 자세(Posture), 자리 종류(PlacementSlot), 단일 화물 배치(Placement),
  트럭 잔여 자원(TruckState), 안전 검사 결과(CheckResult).
- Trip 자체에는 손대지 않고, Phase 8 통합 단계에서 `placements` 필드만
  models.py 의 Trip 데이터클래스에 추가한다 (역호환 — 기본값 빈 리스트).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union

from .models import Module, Panel, Truck


# ── 자세 ──────────────────────────────────────────────────────────
class Posture(str, Enum):
    """화물 적재 자세."""
    LYING = "lying"       # 눕힘 — 두께가 높이방향
    STANDING = "standing"  # 세움 — 폭이 높이방향 (단순 wall + A-frame 트럭 전용)


# ── 자리 종류 ─────────────────────────────────────────────────────
class PlacementSlot(str, Enum):
    """트럭 안에서 화물이 놓이는 자리 종류."""
    FLOOR = "floor"          # 트럭 바닥 옆자리
    STACK = "stack"          # 위층 적층 (LYING 만)
    DEP_INNER = "dep_inner"  # 종속 패널 내부 슬롯 (LYING 만)


# ── 단일 화물 배치 정보 ──────────────────────────────────────────
Item = Union[Module, Panel]


@dataclass(frozen=True)
class Placement:
    """한 화물의 트럭 내 배치 메타데이터.

    트럭 좌표계 원점 = 트럭 적재함 *중심* (mm).
    parent_idx 는 STACK / DEP_INNER 인 경우 모체 Placement 의 Trip.placements
    내 인덱스 (FLOOR 면 None).
    """
    item: Item
    slot: PlacementSlot
    posture: Posture
    truck_xyz: tuple[float, float, float]
    parent_idx: Optional[int] = None


# ── 트럭 잔여 자원 추적 ──────────────────────────────────────────
@dataclass
class TruckState:
    """패킹 도중 한 트럭(=한 회차)의 잔여 자원 추적.

    의사코드 § 2.1 의 식 그대로 구현. effective_cargo_limit 는
    `_effective_cargo_limit(truck, site)` 로 1 회 계산 후 캐시.
    """
    truck: Truck
    effective_cargo_limit: float
    used_floor_length: float = 0.0     # mm
    used_cargo_weight: float = 0.0     # kg
    # 단별 max 두께 (mm). len(layer_max_thickness) == 단 수.
    layer_max_thickness: list[float] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)

    @property
    def remaining_cargo_limit(self) -> float:
        """잔여 화물 적재 가능 무게 (kg). 0 미만이면 0 으로 보정."""
        return max(0.0, self.effective_cargo_limit - self.used_cargo_weight)

    def remaining_floor_length(self, usable_length: float) -> float:
        """잔여 트럭 바닥 길이 (mm). 의사코드 § 2.1 식."""
        return max(0.0, usable_length - self.used_floor_length)

    def used_inner_height(self, panel_gap_mm: float) -> float:
        """현재까지 쌓인 단의 총 점유 높이 (mm).

        식: Σ(단별 max 두께) + (단 수 − 1) × panel_gap_mm
        단 수 0 이면 0.
        """
        n = len(self.layer_max_thickness)
        if n == 0:
            return 0.0
        return sum(self.layer_max_thickness) + max(n - 1, 0) * panel_gap_mm

    @property
    def n_layers(self) -> int:
        """현재 단 수 (시각화·UI 용)."""
        return len(self.layer_max_thickness)


# ── 안전 검사 결과 ────────────────────────────────────────────────
@dataclass(frozen=True)
class CheckResult:
    """안전 검사 통과 여부 + 위반 사유들."""
    ok: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


__all__ = [
    "Posture",
    "PlacementSlot",
    "Placement",
    "TruckState",
    "CheckResult",
    "Item",
]
