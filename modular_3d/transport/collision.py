"""Phase 4-B — 100mm 셀 3D 그리드 인덱스 (충돌 후보 가속 조회).

[목적]
컴포넌트 단위 페어와이즈 충돌 검사를 O(N²M²) → O(NM) 수준으로 가속.
새 컴포넌트의 박스가 들어갈 자리 주변 셀만 조회해 *근접* 한 기존 컴포넌트를
빠르게 찾고, 그 일부만 정밀 페어와이즈 검사.

[자료 구조]
  cells: Dict[(ix, iy, iz), Set[owner_id]]
    - (ix, iy, iz) = (x_mm // CELL_MM, y_mm // CELL_MM, z_mm // CELL_MM)
    - owner_id = placement 인덱스 또는 임의 식별자 (호출자가 정함)
  owner_to_boxes: Dict[owner_id, List[box]]
    - owner_id → 그 owner 가 차지한 박스 리스트 (정밀 검사용 저장)

[API]
  - insert(boxes, owner_id) : 박스 집합을 그리드에 등록
  - remove(owner_id)        : owner 의 모든 박스 제거
  - query_near(box, margin) : box (+margin 확장) 와 겹치는 셀의 owner_id set
  - boxes_of(owner_id)      : 등록된 박스 리스트 조회 (페어와이즈 비교용)

[좌표·단위]
  모든 입력 박스는 (x_min, y_min, z_min, x_max, y_max, z_max) mm tuple.
  음수 좌표 허용 (트럭 적재함 음의 x 방향 등 — 캔틸 돌출).
  단 floor 함수가 음수에서 올바르게 동작해야 함 → 표준 Python int 나눗셈은
  음수에서 floor 로 동작 (예: -50 // 100 = -1). OK.

[기존 코드 영향 없음]
  이 모듈은 어디서도 호출되지 않음 (Phase 4-C 에서 can_place_v2 가 처음 사용).
"""
from __future__ import annotations

from typing import Dict, Hashable, Iterable, List, Set, Tuple


# 그리드 셀 크기 — 사용자 결정 (100mm 단위)
CELL_MM: int = 100


# 박스 타입 별칭
Box = Tuple[float, float, float, float, float, float]
# (x_min, y_min, z_min, x_max, y_max, z_max)


def _floor_div(v: float, cell: int) -> int:
    """음수에서도 안전한 floor 나눗셈."""
    return int(v) // cell if v >= 0 else -((-int(v) + cell - 1) // cell)


def _cell_range_for_box(box: Box) -> Tuple[range, range, range]:
    """박스가 점유하는 셀 인덱스 범위 (ix, iy, iz)."""
    x0, y0, z0, x1, y1, z1 = box
    ix0 = _floor_div(x0, CELL_MM)
    iy0 = _floor_div(y0, CELL_MM)
    iz0 = _floor_div(z0, CELL_MM)
    # 박스 max 면이 셀 경계에 닿으면 *다음 셀* 안 들어감 → -1 보정
    ix1 = _floor_div(x1 - 1e-6, CELL_MM)
    iy1 = _floor_div(y1 - 1e-6, CELL_MM)
    iz1 = _floor_div(z1 - 1e-6, CELL_MM)
    return range(ix0, ix1 + 1), range(iy0, iy1 + 1), range(iz0, iz1 + 1)


class CollisionGrid:
    """100mm 셀 3D 그리드 인덱스.

    [성능]
    insert: O(부재 수 × 셀 수)
    query_near: O(쿼리 박스 셀 수)
    remove: O(owner 박스 수 × 셀 수)
    """

    def __init__(self, cell_mm: int = CELL_MM) -> None:
        self.cell_mm: int = int(cell_mm)
        self._cells: Dict[Tuple[int, int, int], Set[Hashable]] = {}
        self._owner_to_boxes: Dict[Hashable, List[Box]] = {}

    # ── 등록 / 제거 ───────────────────────────────────────────
    def insert(self, boxes: Iterable[Box], owner_id: Hashable) -> None:
        """boxes 를 owner_id 명의로 그리드에 등록.

        같은 owner_id 가 이미 있으면 *추가* 등록 (이전 박스 보존).
        교체하려면 remove(owner_id) 먼저 호출.
        """
        box_list = list(boxes)
        if not box_list:
            return
        self._owner_to_boxes.setdefault(owner_id, []).extend(box_list)
        for b in box_list:
            rx, ry, rz = _cell_range_for_box(b)
            for ix in rx:
                for iy in ry:
                    for iz in rz:
                        key = (ix, iy, iz)
                        s = self._cells.get(key)
                        if s is None:
                            s = set()
                            self._cells[key] = s
                        s.add(owner_id)

    def remove(self, owner_id: Hashable) -> None:
        """owner_id 의 모든 박스 제거. 셀에서 owner_id 도 빼고, 빈 셀은 폐기."""
        boxes = self._owner_to_boxes.pop(owner_id, None)
        if not boxes:
            return
        for b in boxes:
            rx, ry, rz = _cell_range_for_box(b)
            for ix in rx:
                for iy in ry:
                    for iz in rz:
                        key = (ix, iy, iz)
                        s = self._cells.get(key)
                        if s is not None:
                            s.discard(owner_id)
                            if not s:
                                self._cells.pop(key, None)

    # ── 조회 ──────────────────────────────────────────────────
    def query_near(self, box: Box, margin_mm: float = 0.0) -> Set[Hashable]:
        """box (+ margin 확장) 와 셀이 겹치는 모든 owner_id set.

        margin 으로 갭 100mm 도 함께 검사하려면 margin_mm=100 전달.
        """
        x0, y0, z0, x1, y1, z1 = box
        expanded: Box = (
            x0 - margin_mm, y0 - margin_mm, z0 - margin_mm,
            x1 + margin_mm, y1 + margin_mm, z1 + margin_mm,
        )
        rx, ry, rz = _cell_range_for_box(expanded)
        owners: Set[Hashable] = set()
        for ix in rx:
            for iy in ry:
                for iz in rz:
                    s = self._cells.get((ix, iy, iz))
                    if s:
                        owners.update(s)
        return owners

    def boxes_of(self, owner_id: Hashable) -> List[Box]:
        """등록된 박스 리스트 (없으면 빈 리스트)."""
        return list(self._owner_to_boxes.get(owner_id, ()))

    def all_owners(self) -> Set[Hashable]:
        return set(self._owner_to_boxes.keys())

    def clear(self) -> None:
        self._cells.clear()
        self._owner_to_boxes.clear()


# ── 박스 거리 / 충돌 검사 (그리드와 독립적 헬퍼) ────────────────
def boxes_overlap(b1: Box, b2: Box) -> bool:
    """두 박스가 겹치는가 (경계 접촉은 안 겹침으로 봄 = strict)."""
    if b1[3] <= b2[0] or b2[3] <= b1[0]: return False
    if b1[4] <= b2[1] or b2[4] <= b1[1]: return False
    if b1[5] <= b2[2] or b2[5] <= b1[2]: return False
    return True


def boxes_min_distance(b1: Box, b2: Box) -> float:
    """두 박스 사이 최소 3D 거리 (mm). 겹치면 0.

    [수식] 각 축별 분리 거리 dx, dy, dz 의 유클리드 노름.
    dx = max(0, max(b1.x_min, b2.x_min) - min(b1.x_max, b2.x_max)). 단순화:
    dx = max(0, b2.x_min - b1.x_max, b1.x_min - b2.x_max).
    """
    dx = max(0.0, b2[0] - b1[3], b1[0] - b2[3])
    dy = max(0.0, b2[1] - b1[4], b1[1] - b2[4])
    dz = max(0.0, b2[2] - b1[5], b1[2] - b2[5])
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return 0.0
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def boxes_gap_ok(b1: Box, b2: Box, min_gap_mm: float) -> bool:
    """두 박스가 *모든 3D 방향* 으로 min_gap_mm 이상 떨어졌는가.

    [정책] 갭 = *어느 한 축 이상* 으로 min_gap_mm 분리되면 OK (3D 안전 거리).
    즉 dx ≥ min_gap OR dy ≥ min_gap OR dz ≥ min_gap.
    이 조건은 boxes_min_distance ≥ min_gap 보다 약함 — 모서리 빗각 거리 대신
    *축 분리* 만 봄. 운송 실무에선 화물 정렬 → 축 분리 검사가 자연.
    """
    dx = max(0.0, b2[0] - b1[3], b1[0] - b2[3])
    dy = max(0.0, b2[1] - b1[4], b1[1] - b2[4])
    dz = max(0.0, b2[2] - b1[5], b1[2] - b2[5])
    return dx >= min_gap_mm or dy >= min_gap_mm or dz >= min_gap_mm


__all__ = [
    "Box", "CELL_MM",
    "CollisionGrid",
    "boxes_overlap", "boxes_min_distance", "boxes_gap_ok",
]
