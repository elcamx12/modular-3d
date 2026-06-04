"""실(Room) 폴리곤 → 3D 반투명 색면 메시 빌더.

[정책 2026-05-24 디자인 탭 2분리 — 2단계 실 배치]
- 실 폴리곤(월드 xy mm, 단순 다각형)을 슬래브 위 평평한 색면 메시로 변환.
- 단순 다각형(구멍 없음)을 ear-clipping 으로 삼각분할 — 오목(L자) 평면도 정확히
  내부만 채운다(vispy 제약 삼각분할은 오목부 바깥까지 채워 부적합).
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np


# 실 색면이 놓이는 z 높이(mm). 모듈/바닥패널 바닥 슬래브 상면 = z=100
# (보 중심 z=0, 단면폭 200 → 상면 half_s=100; model/core.py:340). 그 위로 5mm 만
# 띄워 z-fighting 만 피한다. 다층은 _render_room_3d 에서 floor_index×FLOOR_HEIGHT 를
# 가산하므로 모든 층에서 동일하게 슬래브 위 5mm 가 유지된다.
ROOM_PLANE_Z_MM = 105.0


def _signed_area(pts: Sequence[Tuple[float, float]]) -> float:
    """폴리곤 부호 면적(>0 = CCW)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return 0.5 * a


def _point_in_triangle(px, py, ax, ay, bx, by, cx, cy) -> bool:
    """점(px,py)이 삼각형 abc 내부(경계 포함)인지 — 부호 일관성 검사."""
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def triangulate_simple_polygon(
    polygon: Sequence[Tuple[float, float]]
) -> List[Tuple[int, int, int]]:
    """단순 다각형(구멍 없음)을 ear-clipping 으로 삼각분할.

    반환: 입력 점 인덱스로 된 삼각형 [(i, j, k), ...]. 점 3개 미만이면 빈 목록.
    """
    n = len(polygon)
    if n < 3:
        return []
    # CCW 로 정규화 — 인덱스 목록을 CCW 순서로.
    idx = list(range(n))
    if _signed_area(polygon) < 0:
        idx.reverse()

    pts = polygon
    tris: List[Tuple[int, int, int]] = []
    guard = 0
    max_guard = 2 * n * n  # 무한루프 방지 안전판
    while len(idx) > 3 and guard < max_guard:
        guard += 1
        ear_found = False
        m = len(idx)
        for i in range(m):
            i0 = idx[(i - 1) % m]
            i1 = idx[i]
            i2 = idx[(i + 1) % m]
            ax, ay = pts[i0]
            bx, by = pts[i1]
            cx, cy = pts[i2]
            # 볼록(convex) 꼭짓점인지 (CCW 기준 외적 > 0)
            cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if cross <= 0:
                continue
            # 삼각형 abc 안에 다른 꼭짓점이 없어야 ear
            contains = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                px, py = pts[j]
                if _point_in_triangle(px, py, ax, ay, bx, by, cx, cy):
                    contains = True
                    break
            if contains:
                continue
            tris.append((i0, i1, i2))
            del idx[i]
            ear_found = True
            break
        if not ear_found:
            break  # 비정상 폴리곤 — 가능한 만큼만 분할
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def build_room_mesh(
    polygon: Sequence[Tuple[float, float]],
    color_rgb: Tuple[int, int, int],
    z: float = ROOM_PLANE_Z_MM,
    alpha: float = 0.35,
):
    """실 폴리곤 → (vertices Nx3, faces Mx3, face_colors Mx4).

    polygon: 월드 xy mm 점 목록(단순 다각형). 점 3개 미만이면 None.
    """
    n = len(polygon)
    if n < 3:
        return None
    tris = triangulate_simple_polygon(polygon)
    if not tris:
        return None
    vertices = np.array([[float(x), float(y), float(z)] for (x, y) in polygon],
                        dtype=np.float32)
    faces = np.array(tris, dtype=np.uint32)
    r, g, b = color_rgb
    rgba = np.array([r / 255.0, g / 255.0, b / 255.0, float(alpha)],
                    dtype=np.float32)
    face_colors = np.tile(rgba, (len(faces), 1))
    return vertices, faces, face_colors


__all__ = ["build_room_mesh", "triangulate_simple_polygon", "ROOM_PLANE_Z_MM"]
