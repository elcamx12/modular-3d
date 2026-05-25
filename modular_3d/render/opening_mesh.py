"""개구부(사각 구멍) 관통 박스 메시.

[정책 2026-05-24 디자인 탭 2분리 — 3단계 개구부]
- 축정렬 박스(슬래브/벽체 채움)에 축정렬 사각 구멍을 실제로 뚫은 메시 생성.
- 구멍은 '내부'(박스 모서리에 닿지 않는 창 형태)로 가정한다(문 미지원 — 사용자 결정).
  → 외곽 4측면은 그대로 두고, 큰 두 면을 격자 분할(구멍 제외) + 구멍 내벽만 추가.
- 슬래브/벽 모두 월드 축정렬 박스이므로(부재 회전 0/90/180/270) 같은 로직 적용.
- 색만 입히고 음영은 없음(shading=None) → 삼각형 와인딩은 외관에 무관.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np


def openings_to_world_rects(face_corners, openings, plane_axes
                            ) -> List[Tuple[float, float, float, float]]:
    """면 로컬 개구부(u,v,w,h) → 월드 평면 사각형 (a0,a1,b0,b1) 목록.

    face_corners: (4,3) 월드, 순서 [원점, +u, +u+v, +v].
    plane_axes: (pa, pb) — 관통축을 제외한 두 월드 축 인덱스.
    """
    fc = np.asarray(face_corners, dtype=np.float64)
    c0 = fc[0]
    fu = fc[1] - fc[0]
    fv = fc[3] - fc[0]
    ulen = float(np.linalg.norm(fu)) or 1.0
    vlen = float(np.linalg.norm(fv)) or 1.0
    pa, pb = plane_axes
    rects = []
    for op in openings:
        u = float(op.get('u', 0.0)); v = float(op.get('v', 0.0))
        w = float(op.get('w', 0.0)); h = float(op.get('h', 0.0))
        if w <= 0 or h <= 0:
            continue
        aa, bb = [], []
        for (uu, vv) in ((u, v), (u + w, v), (u + w, v + h), (u, v + h)):
            wp = c0 + fu * (uu / ulen) + fv * (vv / vlen)
            aa.append(wp[pa]); bb.append(wp[pb])
        rects.append((min(aa), max(aa), min(bb), max(bb)))
    return rects


def build_box_with_holes(mn, mx, holes, through_axis, color):
    """축정렬 박스(mn,mx)에 관통축 방향 사각 구멍들을 뚫은 메시.

    holes: [(a0,a1,b0,b1), ...] — 관통축 제외 두 평면축(world) 사각형.
    through_axis: 0/1/2 — 구멍이 관통하는 축.
    반환: (vertices(N,3) f32, faces(M,3) u32, face_colors(M,4) f32).
    구멍이 유효하지 않으면 호출자가 일반 박스를 쓰도록 None 반환.
    """
    mn = np.asarray(mn, dtype=np.float64)
    mx = np.asarray(mx, dtype=np.float64)
    axes = [0, 1, 2]
    axes.remove(through_axis)
    pa, pb = axes
    a0, a1 = float(mn[pa]), float(mx[pa])
    b0, b1 = float(mn[pb]), float(mx[pb])
    lo, hi = float(mn[through_axis]), float(mx[through_axis])

    # 구멍을 박스 범위로 클램프(가장자리에 닿는 것 허용 — 문/전체 제거 지원).
    tol = 1.0
    H = []
    for (ha0, ha1, hb0, hb1) in holes:
        ca0 = max(a0, min(ha0, ha1))
        ca1 = min(a1, max(ha0, ha1))
        cb0 = max(b0, min(hb0, hb1))
        cb1 = min(b1, max(hb0, hb1))
        if ca1 - ca0 > tol and cb1 - cb0 > tol:
            H.append((ca0, ca1, cb0, cb1))
    if not H:
        return None  # 유효 구멍 없음 → 일반 박스 사용

    verts: List[list] = []
    faces: List[tuple] = []

    def _v(a, b, t):
        p = [0.0, 0.0, 0.0]
        p[pa] = a; p[pb] = b; p[through_axis] = t
        verts.append(p)
        return len(verts) - 1

    def _quad(a, b, c, d):
        faces.append((a, b, c)); faces.append((a, c, d))

    def _in_hole(ca, cb):
        for (x0, x1, y0, y1) in H:
            if x0 < ca < x1 and y0 < cb < y1:
                return True
        return False

    # 격자 좌표(외곽 + 모든 구멍 경계)
    As = sorted(set([a0, a1] + [h[0] for h in H] + [h[1] for h in H]))
    Bs = sorted(set([b0, b1] + [h[2] for h in H] + [h[3] for h in H]))

    # 큰 두 면(관통=lo, hi) — 구멍 제외 격자 분할
    for t in (lo, hi):
        for i in range(len(As) - 1):
            for j in range(len(Bs) - 1):
                ca = 0.5 * (As[i] + As[i + 1])
                cb = 0.5 * (Bs[j] + Bs[j + 1])
                if _in_hole(ca, cb):
                    continue
                p0 = _v(As[i], Bs[j], t)
                p1 = _v(As[i + 1], Bs[j], t)
                p2 = _v(As[i + 1], Bs[j + 1], t)
                p3 = _v(As[i], Bs[j + 1], t)
                _quad(p0, p1, p2, p3)

    # 외곽 4측면 — 가장자리에 닿은 구멍 구간은 비운다(그 면이 뚫림).
    # a=a0 / a=a1 측면: b 격자 분할, 구간 중점이 그 모서리에 닿은 구멍에 들면 skip.
    def _edge_covered(seg_lo, seg_hi, side):
        """side 모서리에 닿은 구멍이 [seg_lo,seg_hi] 중점을 덮는지."""
        mid = 0.5 * (seg_lo + seg_hi)
        for (x0, x1, y0, y1) in H:
            if side == 'a0' and abs(x0 - a0) < tol and y0 < mid < y1:
                return True
            if side == 'a1' and abs(x1 - a1) < tol and y0 < mid < y1:
                return True
            if side == 'b0' and abs(y0 - b0) < tol and x0 < mid < x1:
                return True
            if side == 'b1' and abs(y1 - b1) < tol and x0 < mid < x1:
                return True
        return False

    for a, side in ((a0, 'a0'), (a1, 'a1')):
        for j in range(len(Bs) - 1):
            if _edge_covered(Bs[j], Bs[j + 1], side):
                continue
            _quad(_v(a, Bs[j], lo), _v(a, Bs[j + 1], lo),
                  _v(a, Bs[j + 1], hi), _v(a, Bs[j], hi))
    for b, side in ((b0, 'b0'), (b1, 'b1')):
        for i in range(len(As) - 1):
            if _edge_covered(As[i], As[i + 1], side):
                continue
            _quad(_v(As[i], b, lo), _v(As[i + 1], b, lo),
                  _v(As[i + 1], b, hi), _v(As[i], b, hi))

    # 구멍 내벽 — 박스 경계와 겹치는 변은 생략(그쪽은 외곽이 이미 비워짐).
    for (x0, x1, y0, y1) in H:
        if abs(x0 - a0) >= tol:
            _quad(_v(x0, y0, lo), _v(x0, y1, lo), _v(x0, y1, hi), _v(x0, y0, hi))
        if abs(x1 - a1) >= tol:
            _quad(_v(x1, y0, lo), _v(x1, y1, lo), _v(x1, y1, hi), _v(x1, y0, hi))
        if abs(y0 - b0) >= tol:
            _quad(_v(x0, y0, lo), _v(x1, y0, lo), _v(x1, y0, hi), _v(x0, y0, hi))
        if abs(y1 - b1) >= tol:
            _quad(_v(x0, y1, lo), _v(x1, y1, lo), _v(x1, y1, hi), _v(x0, y1, hi))

    if not faces:
        # 면 전체가 구멍 → 빈 메시(그 면 사라짐). 빈 배열 반환.
        return (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32),
                np.zeros((0, 4), np.float32))

    V = np.array(verts, dtype=np.float32)
    F = np.array(faces, dtype=np.uint32)
    C = np.tile(np.asarray(color, dtype=np.float32), (len(F), 1))
    return V, F, C


def _face_corners_for_opening(comp, face=None):
    """부재의 개구부 기준 면 코너(4,3) + 종류 + 두께 반환. 없으면 (None,'',0).

    face: 'slab' | 'wall' | 'wall_0'..'wall_3' | None(기본 추정).
    """
    from modular_3d.model import ComponentType as _CT
    if face:
        if face == 'slab':
            slab = getattr(comp, 'slab', None)
            if slab is not None:
                return (np.asarray(slab.corners, dtype=np.float64), 'slab',
                        float(getattr(slab, 'thickness', 150.0)))
        elif face == 'wall':
            wf = getattr(comp, 'wall_fill', None)
            if wf is not None:
                return (np.asarray(wf.corners, dtype=np.float64), 'wall',
                        float(getattr(wf, 'thickness', 100.0)))
        elif face.startswith('wall_'):
            try:
                i = int(face.split('_')[1])
            except (IndexError, ValueError):
                return None, '', 0.0
            wfs = getattr(comp, 'wall_fills', None) or []
            if 0 <= i < len(wfs):
                return (np.asarray(wfs[i].corners, dtype=np.float64), 'wall',
                        float(getattr(wfs[i], 'thickness', 100.0)))
        return None, '', 0.0
    # face 미지정 — 역호환 기본 추정
    if comp.comp_type == _CT.STRUCT_WALL and getattr(comp, 'wall_fill', None):
        wf = comp.wall_fill
        return (np.asarray(wf.corners, dtype=np.float64), 'wall',
                float(getattr(wf, 'thickness', 100.0)))
    slab = getattr(comp, 'slab', None)
    if slab is not None:
        return (np.asarray(slab.corners, dtype=np.float64), 'slab',
                float(getattr(slab, 'thickness', 150.0)))
    return None, '', 0.0


def _point_seg_dist_xy(px, py, a, b):
    """점(px,py)과 선분 a-b(둘 다 (x,y,..)) 의 xy 거리."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def resolve_opening_face(comp, wx, wy):
    """클릭 위치에서 개구부 대상 면 결정. 'slab'|'wall'|'wall_N' 또는 None.

    모듈: 4벽 중 가장 가까운 벽이 가장자리 밴드 안이면 그 벽, 아니면 바닥 슬래브.
    구조벽: 'wall'. 그 외 슬래브 보유 부재: 'slab'.
    """
    from modular_3d.model import ComponentType as _CT
    if (comp.comp_type in (_CT.STRUCT_WALL, _CT.INTERIOR_WALL)
            and getattr(comp, 'wall_fill', None)):
        return 'wall'
    wfs = getattr(comp, 'wall_fills', None)
    if wfs:
        best_i, best_d = -1, float('inf')
        for i, wf in enumerate(wfs):
            c = np.asarray(wf.corners, dtype=np.float64)
            d = _point_seg_dist_xy(wx, wy, c[0], c[1])
            if d < best_d:
                best_d, best_i = d, i
        w = float(comp.dimensions.get('width', 3000.0))
        dd = float(comp.dimensions.get('depth', 6000.0))
        band = min(700.0, 0.3 * min(w, dd))
        if best_i >= 0 and best_d <= band:
            return f'wall_{best_i}'
        if getattr(comp, 'slab', None) is not None:
            return 'slab'
        return None
    if getattr(comp, 'slab', None) is not None:
        return 'slab'
    return None


def opening_xy_poly(comp, op):
    """개구부 1건(op dict)을 2D 평면(xy) 사각형 [(x,y)x4] 로 변환. 없으면 None.

    - 슬래브: u,v 모두 xy → 실제 평면 사각형.
    - 벽: u=길이(xy), v=높이(z) → 평면에서는 벽 두께만큼 두꺼운 선분(사각형).
    """
    fc, kind, thick = _face_corners_for_opening(comp, op.get('face'))
    if fc is None:
        return None
    c0 = fc[0]
    fu = fc[1] - fc[0]
    fv = fc[3] - fc[0]
    ulen = float(np.linalg.norm(fu)) or 1.0
    vlen = float(np.linalg.norm(fv)) or 1.0
    u = float(op.get('u', 0.0)); v = float(op.get('v', 0.0))
    w = float(op.get('w', 0.0)); h = float(op.get('h', 0.0))
    if w <= 0 or h <= 0:
        return None
    if kind == 'slab':
        pts = []
        for (uu, vv) in ((u, v), (u + w, v), (u + w, v + h), (u, v + h)):
            wp = c0 + fu * (uu / ulen) + fv * (vv / vlen)
            pts.append((float(wp[0]), float(wp[1])))
        return pts
    # wall — 길이 u..u+w 구간을 벽 두께만큼 두껍게 (thick = 면 두께)
    fu_xy = np.array([fu[0], fu[1]], dtype=np.float64)
    n = float(np.linalg.norm(fu_xy)) or 1.0
    dir_xy = fu_xy / n
    perp = np.array([-dir_xy[1], dir_xy[0]]) * (thick / 2.0)
    base = np.array([c0[0], c0[1]])
    p_start = base + fu_xy * (u / ulen)
    p_end = base + fu_xy * ((u + w) / ulen)
    return [
        (float(p_start[0] + perp[0]), float(p_start[1] + perp[1])),
        (float(p_end[0] + perp[0]), float(p_end[1] + perp[1])),
        (float(p_end[0] - perp[0]), float(p_end[1] - perp[1])),
        (float(p_start[0] - perp[0]), float(p_start[1] - perp[1])),
    ]


def opening_xy_polygons(comp):
    """부재의 각 개구부를 2D 평면 사각형으로 변환. [(index, pts, kind), ...]."""
    openings = getattr(comp, 'openings', None)
    if not openings:
        return []
    out = []
    for idx, op in enumerate(openings):
        fc, kind, _t = _face_corners_for_opening(comp, op.get('face'))
        if fc is None:
            continue
        pts = opening_xy_poly(comp, op)
        if pts is not None:
            out.append((idx, pts, kind))
    return out


def opening_world_box(comp, op):
    """개구부 1건의 3D 볼륨(관통 박스) AABB (mn, mx) 반환. 없으면 None.

    개구부 부피 = 평면 사각형 × 관통 두께(슬래브 두께 / 벽 두께).
    """
    fc, kind, thickness = _face_corners_for_opening(comp, op.get('face'))
    if fc is None:
        return None
    if kind == 'slab':
        through = 2
        lo = float(fc[:, 2].min())
        hi = lo + thickness
        plane = (0, 1)
    else:
        mn0 = fc.min(axis=0); mx0 = fc.max(axis=0)
        through = 2
        for ax in range(3):
            if mx0[ax] - mn0[ax] < 1.0:
                through = ax
        lo = float(fc[:, through].min()) - thickness / 2.0
        hi = float(fc[:, through].max()) + thickness / 2.0
        plane = tuple(a for a in (0, 1, 2) if a != through)
    rects = openings_to_world_rects(fc, [op], plane)
    if not rects:
        return None
    a0, a1, b0, b1 = rects[0]
    pa, pb = plane
    mn = [0.0, 0.0, 0.0]; mx = [0.0, 0.0, 0.0]
    mn[pa] = a0; mx[pa] = a1
    mn[pb] = b0; mx[pb] = b1
    mn[through] = lo; mx[through] = hi
    return np.array(mn, dtype=np.float64), np.array(mx, dtype=np.float64)


def opening_facelocal_from_click(comp, wx, wy, ew, eh, sill, anchor, face=None):
    """클릭 월드(wx,wy)+크기(ew,eh)+앵커 → (u0, v0, kind, face). 대상 아니면 None.

    face 미지정이면 클릭 위치로 면을 자동 판정(모듈 4벽/슬래브).
    anchor: 0=좌하,1=우하,2=우상,3=좌상. 벽이면 세로 v=sill 고정.
    개구부가 면 가장자리에 닿는 것 허용([0, len-크기] 클램프) — '그 면 통째 제거' 가능.
    """
    if face is None:
        face = resolve_opening_face(comp, wx, wy)
    if face is None:
        return None
    fc, kind, thick = _face_corners_for_opening(comp, face)
    if fc is None:
        return None
    c0 = fc[0]
    fu = fc[1] - fc[0]; fv = fc[3] - fc[0]
    ulen = float(np.linalg.norm(fu)) or 1.0
    vlen = float(np.linalg.norm(fv)) or 1.0
    fu_hat = fu / ulen
    rel = np.array([wx - c0[0], wy - c0[1], 0.0])
    u_c = float(rel[0] * fu_hat[0] + rel[1] * fu_hat[1])
    ax = 0.0 if anchor in (0, 3) else ew
    u0 = u_c - ax
    if kind == 'slab':
        fv_hat = fv / vlen
        v_c = float(rel[0] * fv_hat[0] + rel[1] * fv_hat[1])
        ay = 0.0 if anchor in (0, 1) else eh
        v0 = v_c - ay
    else:
        v0 = float(sill)
    u0 = max(0.0, min(u0, max(0.0, ulen - ew)))
    v0 = max(0.0, min(v0, max(0.0, vlen - eh)))
    return u0, v0, kind, face


__all__ = ["build_box_with_holes", "openings_to_world_rects",
           "opening_xy_polygons", "opening_xy_poly", "opening_world_box",
           "opening_facelocal_from_click", "resolve_opening_face"]
