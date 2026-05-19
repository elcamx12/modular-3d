"""controls.py 의 순수 기하 헬퍼 분리.

self 를 쓰지 않는 함수들을 모아 단위 테스트와 재사용을 쉽게 한다.
controls.py 는 본 모듈을 import 해서 그대로 호출.

[설계 원칙]
- 본 모듈 함수는 모두 순수 함수 — Controller 상태에 의존하지 않는다.
- vispy / Qt import 금지 (numpy + model 만 의존).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from modular_3d.model import (
    Component, ComponentType, Module, FloorPanel, StructWall,
    CantileverBeam, CantileverSlab, MidBeam, MidColumn, Vertical3Module,
)


# ── Ray 교차 / 캐스팅 ──────────────────────────────────────────

def ray_aabb_intersect(origin: np.ndarray, direction: np.ndarray,
                       bbox_min: np.ndarray, bbox_max: np.ndarray) -> Optional[float]:
    """Ray-AABB 교차 판정. 교차 시 t 값 반환, 아니면 None."""
    t_min = -np.inf
    t_max = np.inf
    for i in range(3):
        if abs(direction[i]) < 1e-12:
            if origin[i] < bbox_min[i] or origin[i] > bbox_max[i]:
                return None
        else:
            t1 = (bbox_min[i] - origin[i]) / direction[i]
            t2 = (bbox_max[i] - origin[i]) / direction[i]
            if t1 > t2:
                t1, t2 = t2, t1
            t_min = max(t_min, t1)
            t_max = min(t_max, t2)
            if t_min > t_max:
                return None
    if t_max < 0:
        return None
    return max(t_min, 0.0)


def raycast_z0(origin: np.ndarray, direction: np.ndarray,
               z_target: float = 0.0) -> Optional[np.ndarray]:
    """Ray 와 평면 z=z_target 의 교점 반환. 평행/뒤쪽이면 None."""
    if abs(direction[2]) < 1e-9:
        return None
    t = (z_target - origin[2]) / direction[2]
    if t < 0:
        return None
    return origin + t * direction


# ── Component 인스턴스화 ───────────────────────────────────────

def create_component(comp_type: ComponentType, position: np.ndarray,
                     dims: dict, rotation: int = 0, anchor: int = 0) -> Component:
    """Component 인스턴스 생성 — model.core.instantiate 위임 (단계 2 통합)."""
    from modular_3d.model.core import instantiate
    return instantiate(comp_type, position, dims, rotation, anchor)


# ── 중간보 / 중간기둥 자동 배치 ────────────────────────────────

def calc_mid_beam_placement(snap_pos, snap_comp):
    """스냅 지점에서 중간보 자동 배치.

    스냅된 보와 직교 방향으로 반대편 평행 보까지 연결. 중간보의 중심이
    스냅 지점을 지난다.

    Returns: (position, rotation, dims) 또는 None.
    """
    beams = snap_comp._get_all_beams()
    if not beams:
        return None

    # 스냅 지점에 가장 가까운 보 찾기 (XY + Z 일치 우선)
    best_beam = None
    best_dist = float('inf')
    snap_z = snap_pos[2]
    for beam in beams:
        z_penalty = abs(beam.start[2] - snap_z)
        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
            p = beam.start + t * (beam.end - beam.start)
            d = np.linalg.norm(p[:2] - snap_pos[:2]) + z_penalty
            if d < best_dist:
                best_dist = d
                best_beam = beam

    if best_beam is None:
        return None

    # 보 방향 판별 (X 방향 vs Y 방향)
    delta = best_beam.end - best_beam.start
    is_x_beam = abs(delta[0]) > abs(delta[1])
    perp_axis = 1 if is_x_beam else 0

    beam_perp = (best_beam.start[perp_axis] + best_beam.end[perp_axis]) / 2.0
    beam_z = best_beam.start[2]

    parallel_perps = []
    for beam in beams:
        d = beam.end - beam.start
        b_is_x = abs(d[0]) > abs(d[1])
        b_z = beam.start[2]
        if b_is_x == is_x_beam and abs(b_z - beam_z) < 50:
            b_perp = (beam.start[perp_axis] + beam.end[perp_axis]) / 2.0
            if abs(b_perp - beam_perp) > 50:
                parallel_perps.append(b_perp)

    if not parallel_perps:
        return None

    target_perp = min(parallel_perps, key=lambda p: abs(p - beam_perp))
    span = abs(target_perp - beam_perp)

    if is_x_beam:
        # 스냅된 보: X 방향 → 중간보: Y 방향 (rotation=90)
        mid_rotation = 90
        center_x = snap_pos[0]
        center_y = (beam_perp + target_perp) / 2.0
        # MidBeam rot=90, anchor=0: beam center = (px-100, py+w/2)
        px = center_x + 100
        py = center_y - span / 2.0
    else:
        # 스냅된 보: Y 방향 → 중간보: X 방향 (rotation=0)
        mid_rotation = 0
        center_x = (beam_perp + target_perp) / 2.0
        center_y = snap_pos[1]
        # MidBeam rot=0, anchor=0: beam center = (px+w/2, py+100)
        px = center_x - span / 2.0
        py = center_y - 100

    position = np.array([px, py, beam_z], dtype=np.float64)
    dims = {'width': span, 'depth': 200, 'height': 200}
    return position, mid_rotation, dims


def calc_mid_column_placement(snap_pos, snap_comp):
    """스냅 지점에 중간기둥 자동 배치.

    기둥 중심이 스냅 지점을 지난다. 높이는 대상 부재 높이.

    Returns: (position, rotation, dims) 또는 None.
    """
    bbox_min, bbox_max = snap_comp.get_bounding_box()
    comp_height = bbox_max[2] - bbox_min[2]
    if comp_height < 100:
        comp_height = 3400.0  # 폴백

    # MidColumn rot=0, anchor=0: column center = (px+100, py+100)
    px = snap_pos[0] - 100
    py = snap_pos[1] - 100

    position = np.array([px, py, bbox_min[2]], dtype=np.float64)
    dims = {'width': 200, 'depth': 200, 'height': comp_height}
    return position, 0, dims
