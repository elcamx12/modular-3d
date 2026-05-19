"""공간 매칭 헬퍼 — fuzzy 매칭 4곳의 공통 알고리즘 추출.

[배경 — 단계 5 (2026-05-08)]
topology / ops_builder 의 fuzzy 매칭 4곳:
  1. topology._resolve_floor_panel_supports : FP 코너 ↔ 모듈 기둥/보 / 캔틸 끝
  2. topology._resolve_wall_panel_connections : 벽 column ↔ 다른 벽 / 모듈 보 / 모듈 기둥
  3. ops_builder._snap_dependent_cantilever_to_module : 캔틸 anchor → 부모 노드 정렬
  4. ops_builder._inject_cantilever_external_supports : 캔틸 외측 끝 → 외부 모듈

각각 매칭 후 액션·우선순위·임계값이 달라 완전 통합 불가. 본 모듈은 매칭
알고리즘 (점↔점 nearest, 점↔선분 projection) 만 추출.

[사용 패턴]
  best = nearest_node(point, candidates, tol_xy, tol_z)
  best_proj = nearest_segment_projection(point, segments, tol_xy, tol_z)
"""
from __future__ import annotations

from typing import List, Tuple, Optional, Any
import numpy as np


def nearest_node(point: np.ndarray,
                 candidates: List[Tuple[Any, np.ndarray]],
                 tol_xy: float,
                 tol_z: float) -> Optional[Tuple[Any, float, float]]:
    """점 ↔ 점 가장 가까운 후보 찾기.

    Args:
        point: 기준점 (3,) world coord
        candidates: [(payload, coord), ...] — payload 는 호출자가 식별에 쓸 임의 객체
        tol_xy: xy 평면 거리 한계
        tol_z: z 거리 한계

    Returns:
        (best_payload, best_dist_xy, best_dist_z) 또는 None.
    """
    pxy = point[:2]
    pz = float(point[2])
    best = None
    best_xy = tol_xy
    best_z = tol_z
    for payload, c in candidates:
        cxy = c[:2]
        cz = float(c[2])
        dz = abs(pz - cz)
        if dz > tol_z:
            continue
        dxy = float(np.linalg.norm(pxy - cxy))
        # xy 우선, 동률(±1mm)이면 z 더 가까운 것
        if (dxy < best_xy - 1.0
                or (dxy < best_xy + 1.0 and dz < best_z)):
            best_xy = dxy
            best_z = dz
            best = (payload, dxy, dz)
    return best


def project_point_to_segment(
    point_xy: np.ndarray,
    seg_c1_xy: np.ndarray,
    seg_c2_xy: np.ndarray,
) -> Optional[Tuple[np.ndarray, float, float]]:
    """점을 (xy 평면) 선분에 사영.

    Returns:
        (proj_xy, distance, t)  — t 는 c1→c2 의 정규화 파라미터 [0, 1]
        선분 길이 0 또는 사영 점이 선분 밖이면 None.
    """
    bvec = seg_c2_xy - seg_c1_xy
    blen = float(np.linalg.norm(bvec))
    if blen < 1e-6:
        return None
    bdir = bvec / blen
    t = float(np.dot(point_xy - seg_c1_xy, bdir))
    if t < -1.0 or t > blen + 1.0:
        return None
    t_clamped = max(0.0, min(t, blen))
    proj = seg_c1_xy + bdir * t_clamped
    dist = float(np.linalg.norm(point_xy - proj))
    return proj, dist, t_clamped / blen


def nearest_segment_projection(
    point: np.ndarray,
    segments: List[Tuple[Any, np.ndarray, np.ndarray, float]],
    tol_xy: float,
    tol_z: float,
) -> Optional[Tuple[Any, float, float, np.ndarray]]:
    """점 ↔ 여러 선분 중 가장 가까운 것 찾기.

    Args:
        point: 기준점 (3,)
        segments: [(payload, c1, c2, segment_z), ...]
            c1/c2 는 (3,) 또는 (2,). segment_z 는 선분 z 평균.
        tol_xy / tol_z: 임계값

    Returns:
        (best_payload, best_dist_xy, best_dist_z, proj_xy) 또는 None.
    """
    pxy = point[:2]
    pz = float(point[2])
    best = None
    best_xy = tol_xy
    best_z = tol_z
    for payload, c1, c2, seg_z in segments:
        dz = abs(pz - seg_z)
        if dz > tol_z:
            continue
        c1xy = c1[:2] if c1.ndim == 1 else c1
        c2xy = c2[:2] if c2.ndim == 1 else c2
        proj = project_point_to_segment(pxy, c1xy, c2xy)
        if proj is None:
            continue
        proj_xy, dist, _ = proj
        if (dist < best_xy
                or (dist < best_xy + 1.0 and dz < best_z)):
            best_xy = dist
            best_z = dz
            best = (payload, dist, dz, proj_xy)
    return best
