"""
스냅 시스템 — 배치된 부재 꼭지점에 스냅 + 20mm 갭 적용.
"""
import numpy as np
from typing import Optional, Tuple, Dict, List
from modular_3d.model import Component, Scene


class SnapManager:
    """부재 꼭지점 스냅 관리. 부재 추가/삭제 시 인덱스 갱신."""

    def __init__(self, snap_screen_radius: float = 15.0):
        self._snap_radius = snap_screen_radius  # 화면 픽셀 단위
        # comp_id → (N, 3) 월드 꼭지점 배열
        self._vertex_cache: Dict[int, np.ndarray] = {}

    # ── 인덱스 관리 ──────────────────────────────────────────

    def add_component(self, comp_id: int, comp: Component):
        """부재의 스냅 포인트를 캐시에 추가 (꼭지점 + 보 중심/1/4)."""
        snap_pts = comp.get_snap_points()
        if len(snap_pts) == 0:
            # 폴백: 바닥 꼭지점
            corners = comp.get_world_corners()
            snap_pts = corners[:4]
        self._vertex_cache[comp_id] = snap_pts

    def remove_component(self, comp_id: int):
        """부재 꼭지점을 캐시에서 제거."""
        self._vertex_cache.pop(comp_id, None)

    def clear(self):
        """전체 캐시 초기화."""
        self._vertex_cache.clear()

    # ── 스냅 탐색 ────────────────────────────────────────────

    def find_nearest(
        self,
        mouse_qt: Tuple[float, float],
        scene_transform,
        exclude_comp_id: Optional[int] = None,
        exclude_ids: Optional[set] = None,
    ) -> Optional[Tuple[np.ndarray, int, np.ndarray]]:
        """
        vispy 씬 변환으로 각 스냅 포인트를 화면에 투영하고,
        마우스와의 2D 픽셀 거리가 snap_radius 이내인 가장 가까운 점 반환.

        Returns: (snap_world_pos, comp_id, comp_all_verts) 또는 None
        """
        if not self._vertex_cache:
            return None

        radius_sq = self._snap_radius ** 2
        mx, my = mouse_qt

        best_dist_sq = float('inf')
        best_idx = -1
        all_verts = []
        all_comp_ids = []

        for cid, verts in self._vertex_cache.items():
            if cid == exclude_comp_id:
                continue
            if exclude_ids is not None and cid in exclude_ids:
                continue
            for v in verts:
                all_verts.append(v)
                all_comp_ids.append(cid)
                # vispy 씬 변환: 3D → canvas pixels (동차좌표)
                mapped = scene_transform.map(v)
                w_clip = mapped[3]
                if abs(w_clip) < 1e-6:
                    continue
                sx = mapped[0] / w_clip
                sy = mapped[1] / w_clip  # vispy canvas = Qt 좌표계
                dx = sx - mx
                dy = sy - my
                dist_sq = dx * dx + dy * dy
                if dist_sq < radius_sq and dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_idx = len(all_verts) - 1

        if best_idx < 0:
            return None

        hit_comp_id = all_comp_ids[best_idx]
        comp_verts = self._vertex_cache[hit_comp_id]
        return np.array(all_verts[best_idx]).copy(), hit_comp_id, comp_verts.copy()

    # ── 갭 적용 ──────────────────────────────────────────────

    @staticmethod
    def apply_gap(
        placing_pos: np.ndarray,
        placing_dims: Dict[str, float],
        placing_rotation: int,
        placing_anchor: int,
        snap_comp: Component,
        gap: float = None,
    ) -> np.ndarray:
        """
        스냅 위치에 배치할 때 갭 자동 적용 (기본 MODULE_JOINT_GAP_MM).
        placing의 AABB와 snap_comp의 AABB가 겹치는 축으로 밀어냄.

        Returns: 갭이 적용된 새 위치
        """
        from modular_3d.model import _rotate_point
        if gap is None:
            from modular_3d.카탈로그.geometry import MODULE_JOINT_GAP_MM
            gap = float(MODULE_JOINT_GAP_MM)

        # 배치할 부재의 AABB 계산 (임시)
        w = placing_dims.get('width', 0)
        d = placing_dims.get('depth', 0)
        h = placing_dims.get('height', 0)

        # 앵커 오프셋
        anchor_offsets = {0: (0, 0), 1: (w, 0), 2: (w, d), 3: (0, d)}
        ax, ay = anchor_offsets.get(placing_anchor, (0, 0))

        # 로컬 꼭지점 → 월드
        local_corners = [(0 - ax, 0 - ay), (w - ax, 0 - ay),
                         (w - ax, d - ay), (0 - ax, d - ay)]
        world_xs = []
        world_ys = []
        for lx, ly in local_corners:
            rx, ry = _rotate_point(lx, ly, placing_rotation)
            world_xs.append(placing_pos[0] + rx)
            world_ys.append(placing_pos[1] + ry)

        p_min_x, p_max_x = min(world_xs), max(world_xs)
        p_min_y, p_max_y = min(world_ys), max(world_ys)

        # 스냅 대상 부재의 AABB
        s_min, s_max = snap_comp.get_bounding_box()

        # 겹침 판정 및 밀어내기
        result = placing_pos.copy()

        # X축 겹침
        x_overlap = min(p_max_x, s_max[0]) - max(p_min_x, s_min[0])
        # Y축 겹침
        y_overlap = min(p_max_y, s_max[1]) - max(p_min_y, s_min[1])

        if x_overlap > -1 and y_overlap > -1:
            # 두 축 모두 겹치거나 거의 맞닿음 → 더 작은 겹침 축으로 밀어냄
            p_center_x = (p_min_x + p_max_x) / 2.0
            s_center_x = (s_min[0] + s_max[0]) / 2.0
            p_center_y = (p_min_y + p_max_y) / 2.0
            s_center_y = (s_min[1] + s_max[1]) / 2.0

            if abs(x_overlap) <= abs(y_overlap):
                # X축으로 밀어내기
                direction = 1.0 if p_center_x >= s_center_x else -1.0
                result[0] += direction * (x_overlap + gap)
            else:
                # Y축으로 밀어내기
                direction = 1.0 if p_center_y >= s_center_y else -1.0
                result[1] += direction * (y_overlap + gap)

        return result
