"""사영 매칭 공용 유틸리티 (2026-05-13 전역 사영 정책).

[정책]
- 모든 부재 페어의 접합 자리는 사영(직각). 평면상 대각 페어 거부.
- 한 후보 노드의 좌표를 대상 부재의 면/선에 직각 사영해 사영점 좌표 계산.
- 그 사영점에 새 노드 등록 후 결합.

[제공 함수]
- project_to_line(point, line_p1, line_p2)  → ProjectionResult
- project_to_plane(point, plane_origin, plane_normal)  → ProjectionResult
- project_to_quad(point, quad_corners)  → QuadProjection
- is_orthogonal_pair(coord_a, coord_b, tol_tangent)  → bool

[정책 임계 — 본 모듈에 상수로 보관]
- ORTHOGONAL_TOL_MM = 5.0       (Q3 5mm)
- PROJECTION_LIMIT_MM = 350.0   (Q4 350mm)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np


# ── 정책 임계 ────────────────────────────────────────────────

# 평면(x, y) 위 두 노드의 (dx, dy) 중 작은 쪽이 이 값 이내일 때만 직각 인정.
# Q3 결정: 5mm — 매우 엄격(격자 정확).
ORTHOGONAL_TOL_MM: float = 5.0

# 후보 노드에서 대상 부재 면/선까지의 사영 거리 한도. Q4 결정: 350mm.
PROJECTION_LIMIT_MM: float = 350.0


# ── 결과 자료 구조 ────────────────────────────────────────────


@dataclass
class ProjectionResult:
    """직선·평면 사영 결과."""
    point: np.ndarray              # 사영점 좌표 (3,)
    distance: float                # 사영 거리 (mm)
    parameter: float               # 직선의 경우 매개변수 t (0~1 사이 = 직선 안)
    inside: bool                   # 사영점이 부재 범위 안


@dataclass
class QuadProjection:
    """4 노드 quad(셸) 사영 결과.

    [폐기 — 2026-05-13]
    wide-column 전환으로 셸 부재 자체가 없어져 본 자료 구조 사용처 없음.
    파일 자체는 보존(향후 셸 재도입 가능성 대비). 호출 흐름 없음.
    """
    point: np.ndarray              # 사영점 좌표 (3,)
    distance: float                # 사영 거리 (mm) — 평면 법선 방향
    u: float                       # bilinear 좌표 (0~1)
    v: float                       # bilinear 좌표 (0~1)
    inside: bool                   # u, v 모두 [0, 1] 안


# ── 사영 함수 ────────────────────────────────────────────────


def project_to_line(point: np.ndarray, line_p1: np.ndarray,
                    line_p2: np.ndarray) -> ProjectionResult:
    """점을 두 끝점으로 정의된 직선에 직각 사영.

    Returns:
        ProjectionResult.parameter 는 [0, 1] 정규화 (0=p1, 1=p2).
        inside=True 면 사영점이 직선 양 끝 사이.
    """
    p = np.asarray(point, dtype=np.float64)
    a = np.asarray(line_p1, dtype=np.float64)
    b = np.asarray(line_p2, dtype=np.float64)
    ab = b - a
    L2 = float(np.dot(ab, ab))
    if L2 < 1e-12:
        return ProjectionResult(
            point=a.copy(), distance=float(np.linalg.norm(p - a)),
            parameter=0.0, inside=False,
        )
    t = float(np.dot(p - a, ab) / L2)
    proj = a + t * ab
    dist = float(np.linalg.norm(p - proj))
    inside = (0.0 - 1e-6 <= t <= 1.0 + 1e-6)
    return ProjectionResult(point=proj, distance=dist, parameter=t,
                            inside=inside)


def project_to_plane(point: np.ndarray, plane_origin: np.ndarray,
                     plane_normal: np.ndarray) -> ProjectionResult:
    """점을 평면에 직각 사영. 평면은 한 점 + 법선 벡터로 정의.

    Returns:
        ProjectionResult.parameter 는 사영 거리 부호(법선 방향 +).
        inside=True (평면은 무한, 항상 안 — 추가 범위 검사는 호출자가).
    """
    p = np.asarray(point, dtype=np.float64)
    o = np.asarray(plane_origin, dtype=np.float64)
    n = np.asarray(plane_normal, dtype=np.float64)
    nn = float(np.linalg.norm(n))
    if nn < 1e-12:
        return ProjectionResult(point=o.copy(), distance=0.0,
                                parameter=0.0, inside=False)
    nu = n / nn
    signed = float(np.dot(p - o, nu))
    proj = p - signed * nu
    return ProjectionResult(point=proj, distance=abs(signed),
                            parameter=signed, inside=True)


def project_to_quad(point: np.ndarray,
                    quad_corners: list) -> QuadProjection:
    """점을 4 노드 quad 평면에 사영 + bilinear 좌표 (u, v) 계산.

    quad_corners: [c0, c1, c2, c3] — 반시계(또는 시계) 순. 각 (3,) ndarray.

    근사: 평면을 (c0, c1, c3) 세 점으로 정의(c2 무시). quad 가 거의 평면일 때
    정확. 작은 비평면은 무시.

    Returns:
        u, v ∈ [0, 1] 이면 inside=True. 그 외 False.
        u 는 c0→c1 방향, v 는 c0→c3 방향.
    """
    p = np.asarray(point, dtype=np.float64)
    c0 = np.asarray(quad_corners[0], dtype=np.float64)
    c1 = np.asarray(quad_corners[1], dtype=np.float64)
    c3 = np.asarray(quad_corners[3], dtype=np.float64)
    e_u = c1 - c0
    e_v = c3 - c0
    # 평면 법선
    n = np.cross(e_u, e_v)
    nn = float(np.linalg.norm(n))
    if nn < 1e-9:
        return QuadProjection(point=c0.copy(), distance=0.0,
                              u=0.0, v=0.0, inside=False)
    nu = n / nn
    # 평면 사영
    signed = float(np.dot(p - c0, nu))
    proj = p - signed * nu
    # (u, v) 계산 — e_u, e_v 가 직교가 아닐 수 있어 일반 좌표.
    # [e_u, e_v] · [u, v]^T = (proj - c0)  (3D 식 3개 중 2개 만족하는 u, v)
    # 최소제곱: A = [e_u, e_v] (3×2), b = proj - c0 (3,)
    A = np.stack([e_u, e_v], axis=1)
    b = proj - c0
    try:
        uv, _resid, _rank, _sv = np.linalg.lstsq(A, b, rcond=None)
        u = float(uv[0])
        v = float(uv[1])
    except Exception:
        u, v = 0.0, 0.0
    inside = (-1e-6 <= u <= 1.0 + 1e-6) and (-1e-6 <= v <= 1.0 + 1e-6)
    return QuadProjection(point=proj, distance=abs(signed),
                          u=u, v=v, inside=inside)


# ── 직각 페어 검사 ───────────────────────────────────────────


def is_orthogonal_pair(coord_a: np.ndarray, coord_b: np.ndarray,
                       tol_tangent: float = ORTHOGONAL_TOL_MM) -> bool:
    """두 노드의 평면 페어가 직각 사영(한 축 정렬) 인지 검사.

    (dx, dy) 중 작은 쪽이 tol_tangent 이내면 직각. 그 외 평면상 대각.

    Args:
        tol_tangent: 직각 인정 접선 한도 (mm).
    """
    a = np.asarray(coord_a, dtype=np.float64)
    b = np.asarray(coord_b, dtype=np.float64)
    dx = abs(float(a[0] - b[0]))
    dy = abs(float(a[1] - b[1]))
    return min(dx, dy) <= float(tol_tangent)


# ── 단순 자체 검증 (모듈 import 시 자동 실행 X) ────────────


def _self_test():
    """단순 검증 — 모듈 단독 호출 시 실행."""
    # 1. 직선 사영
    r = project_to_line(np.array([5.0, 1.0, 0.0]),
                        np.array([0.0, 0.0, 0.0]),
                        np.array([10.0, 0.0, 0.0]))
    assert abs(r.point[0] - 5.0) < 1e-6
    assert abs(r.distance - 1.0) < 1e-6
    assert abs(r.parameter - 0.5) < 1e-6
    assert r.inside

    # 2. 평면 사영
    r = project_to_plane(np.array([1.0, 2.0, 5.0]),
                         np.array([0.0, 0.0, 0.0]),
                         np.array([0.0, 0.0, 1.0]))
    assert abs(r.point[2] - 0.0) < 1e-6
    assert abs(r.distance - 5.0) < 1e-6

    # 3. quad 사영 (단위 사각형 z=0 평면)
    corners = [np.array([0.0, 0.0, 0.0]),
               np.array([10.0, 0.0, 0.0]),
               np.array([10.0, 10.0, 0.0]),
               np.array([0.0, 10.0, 0.0])]
    r = project_to_quad(np.array([3.0, 4.0, 7.0]), corners)
    assert abs(r.u - 0.3) < 1e-6
    assert abs(r.v - 0.4) < 1e-6
    assert abs(r.distance - 7.0) < 1e-6
    assert r.inside

    # 4. 직각 페어
    assert is_orthogonal_pair(np.array([0.0, 0.0, 0.0]),
                              np.array([100.0, 3.0, 0.0]))  # dy=3 < 5
    assert not is_orthogonal_pair(np.array([0.0, 0.0, 0.0]),
                                  np.array([100.0, 10.0, 0.0]))  # dy=10 > 5



if __name__ == '__main__':
    _self_test()
