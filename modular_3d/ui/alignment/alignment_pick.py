"""AlignmentCanvas 의 hit-test / edge picking Mixin.

[설계]
- AlignmentCanvas 는 본 Mixin 을 inherit. self.* 상태 (controller, _layer 등) 를 그대로 사용.
- 본 모듈은 캔버스 좌표 → 부재 ID / 모서리 ID 로의 변환만 담당.
"""
from __future__ import annotations


from modular_3d.ui.alignment.alignment_helpers import (
    ALIGN_TOL, GAP_20, EPS, EDGE_PICK_PX,
    xy_bbox as _xy_bbox,
)


class AlignmentCanvasPickMixin:
    """클릭 좌표 ↔ 부재·모서리 매칭."""

    def _hit_test_component(self, mx, my):
        """XY-bbox 안에 마우스가 있으면 반환 (가장 작은 면적 우선)."""
        wx, wy = self._screen_to_world(mx, my)
        scene = self._controller._scene.components
        best_id = -1
        best_area = float('inf')
        for cid in self._current_visible_ids():
            comp = scene[cid]
            x0, y0, x1, y1 = _xy_bbox(comp)
            if x0 <= wx <= x1 and y0 <= wy <= y1:
                area = (x1 - x0) * (y1 - y0)
                if area < best_area:
                    best_area = area
                    best_id = cid
        return best_id

    def _pick_direction_edge(self, mx, my):
        """DIRECTION 상태에서 노란 모서리 클릭 → (axis, coord) 반환 or None."""
        if self._selected_id < 0:
            return None
        scene = self._controller._scene.components
        comp = scene.get(self._selected_id)
        if comp is None:
            return None
        x0, y0, x1, y1 = _xy_bbox(comp)
        if self._direction == 'X':
            # 세로선 2개 (x=x0, x=x1)
            for coord in (x0, x1):
                sx, _ = self._world_to_screen(coord, 0)
                if abs(mx - sx) <= EDGE_PICK_PX:
                    return (0, coord)
        elif self._direction == 'Y':
            # 가로선 2개 (y=y0, y=y1)
            for coord in (y0, y1):
                _, sy = self._world_to_screen(0, coord)
                if abs(my - sy) <= EDGE_PICK_PX:
                    return (1, coord)
        return None

    def _pick_target_edge(self, target_comp, axis):
        """대상 부재의 두 모서리 중 마우스 커서에 가까운 쪽 좌표 반환."""
        x0, y0, x1, y1 = _xy_bbox(target_comp)
        wx, wy = self._hover_world
        if axis == 0:
            return x0 if abs(wx - x0) <= abs(wx - x1) else x1
        else:
            return y0 if abs(wy - y0) <= abs(wy - y1) else y1

    # ── 정렬 오차 검출 ────────────────────────────────
    def _misaligned_set(self, comp_id):
        """선택 부재 대비 오차가 있는 부재 → {oid: (dx_or_None, dy_or_None)}."""
        scene = self._controller._scene.components
        if comp_id not in scene:
            return {}
        sx0, sy0, sx1, sy1 = _xy_bbox(scene[comp_id])
        my_vs = [sx0, sx1]
        my_hs = [sy0, sy1]

        result = {}
        for oid in self._current_visible_ids():
            if oid == comp_id:
                continue
            ox0, oy0, ox1, oy1 = _xy_bbox(scene[oid])

            min_dx = None
            for a in my_vs:
                for b in (ox0, ox1):
                    d = abs(a - b)
                    if d <= ALIGN_TOL and d > EPS and not (abs(d - GAP_20) < EPS):
                        if min_dx is None or d < min_dx:
                            min_dx = d
            min_dy = None
            for a in my_hs:
                for b in (oy0, oy1):
                    d = abs(a - b)
                    if d <= ALIGN_TOL and d > EPS and not (abs(d - GAP_20) < EPS):
                        if min_dy is None or d < min_dy:
                            min_dy = d
            if min_dx is not None or min_dy is not None:
                result[oid] = (min_dx, min_dy)
        return result

    # ── 그리기 보조 ────────────────────────────────────
