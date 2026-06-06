# -*- coding: utf-8 -*-
"""부재 거울 좌우/상하 대칭(반사) — 배치설계 뒤집기(Shift+X/Y) 전용.

[개념] 부재의 구조 박스(기둥·보·슬래브)는 대칭이라 거울 반사해도 외형이 같다.
실제로 바뀌는 비대칭 요소는 (1) 위치·회전·앵커(반사로 box 가 반대편을 향함),
(2) 개구부(창·문)의 면·위치다. 영구 'mirror' 필드를 두지 않고 기존 필드(position
/rotation/anchor/openings)에 반사를 '베이크'한다 → 해석·물량·운송 등 downstream 이
새 상태를 몰라도 됨.

[반사 수식] 세로축 X=c 기준 반사 M_x: (x,y)→(2c−x, y).
  M_x·R(θ) = R(−θ)·M_x  →  rotation' = (360−θ)%360.
  로컬 x 뒤집힘(lx→w−lx)  →  anchor 0↔1, 3↔2 (x), 0↔3, 1↔2 (y).
  이 셋(position·rotation·anchor)만 바꾸면 box 외형은 정확히 반사된다(대칭이므로).

[개구부] 반사 전 각 개구부의 면 위 월드 4점을 떠 두고, 부재 반사·재생성 후 그 4점을
반사한 좌표가 새 어느 면 위에 놓이는지 투영해 면로컬 (u,v) 를 재계산한다. 회전과
무관하게 정확(월드 기하 기반). 두 번 뒤집으면 원상복구되는 것이 핵심 불변식.
"""
from __future__ import annotations

import copy
import numpy as np

from modular_3d.render.opening_mesh import _face_corners_for_opening


_ANCHOR_XFLIP = {0: 1, 1: 0, 2: 3, 3: 2}
_ANCHOR_YFLIP = {0: 3, 1: 2, 2: 1, 3: 0}

# 부재가 가질 수 있는 개구부 면 후보(투영 대상 탐색용).
_FACE_CANDIDATES = (['slab', 'wall']
                    + [f'wall_{i}' for i in range(4)]
                    + [f'slab_{i}' for i in range(3)])

_PLANE_TOL = 1.0     # 면 평면 위 판정 허용오차(mm)
_BOUND_TOL = 1.0     # 면 범위 안 판정 허용오차(mm)


def _reflect_pt(p, coord: float, axis: str):
    """월드 점 p(3,) 를 세로축(x=coord) 또는 가로축(y=coord) 기준 반사."""
    q = np.array(p, dtype=np.float64)
    if axis == 'x':
        q[0] = 2.0 * coord - q[0]
    else:
        q[1] = 2.0 * coord - q[1]
    return q


def _opening_world_corners(comp, op):
    """개구부 1건의 면 위 월드 4점(3,) 리스트 반환. 면 못 찾으면 None."""
    fc, kind, _t = _face_corners_for_opening(comp, op.get('face'))
    if fc is None:
        return None
    c0 = fc[0]
    fu = fc[1] - fc[0]
    fv = fc[3] - fc[0]
    ulen = float(np.linalg.norm(fu)) or 1.0
    vlen = float(np.linalg.norm(fv)) or 1.0
    u = float(op.get('u', 0.0)); v = float(op.get('v', 0.0))
    w = float(op.get('w', 0.0)); h = float(op.get('h', 0.0))
    pts = []
    for (uu, vv) in ((u, v), (u + w, v), (u + w, v + h), (u, v + h)):
        pts.append(c0 + fu * (uu / ulen) + fv * (vv / vlen))
    return pts


def _project_corners_to_face(comp, face, world_pts):
    """반사된 월드 4점이 face 평면 위·범위 안이면 (u,v,w,h) 반환, 아니면 None."""
    fc, kind, _t = _face_corners_for_opening(comp, face)
    if fc is None:
        return None
    c0 = fc[0]
    fu = fc[1] - fc[0]
    fv = fc[3] - fc[0]
    ulen = float(np.linalg.norm(fu)) or 1.0
    vlen = float(np.linalg.norm(fv)) or 1.0
    fu_hat = fu / ulen
    fv_hat = fv / vlen
    n = np.cross(fu, fv)
    nn = float(np.linalg.norm(n)) or 1.0
    n_hat = n / nn
    us, vs = [], []
    for P in world_pts:
        rel = np.array(P, dtype=np.float64) - c0
        # 면 평면 위인지(법선 성분 ≈ 0)
        if abs(float(np.dot(rel, n_hat))) > _PLANE_TOL:
            return None
        uu = float(np.dot(rel, fu_hat))
        vv = float(np.dot(rel, fv_hat))
        if not (-_BOUND_TOL <= uu <= ulen + _BOUND_TOL
                and -_BOUND_TOL <= vv <= vlen + _BOUND_TOL):
            return None
        us.append(uu); vs.append(vv)
    u0 = max(0.0, min(us)); v0 = max(0.0, min(vs))
    w0 = max(us) - min(us); h0 = max(vs) - min(vs)
    return u0, v0, w0, h0


def mirror_component_inplace(comp, coord: float, axis: str) -> None:
    """comp 를 세로축(axis='x', x=coord) 또는 가로축(axis='y', y=coord) 기준 거울 반사.

    position·rotation·anchor 를 반사하고 개구부 면/위치를 월드 투영으로 재계산한다.
    호출 후 caller 가 generate_sub_components + 메시 재생성을 한다(여기서도 면 재계산을
    위해 generate_sub_components 를 1회 호출하지만, caller 가 다시 호출해도 무방).
    """
    # 1) 반사 전 개구부 월드 4점 스냅(현재 면 기준).
    old_ops = list(getattr(comp, 'openings', None) or [])
    snaps = []  # (reflected_world_pts, orig_face)
    for op in old_ops:
        wp = _opening_world_corners(comp, op)
        if wp is None:
            snaps.append((None, op))
            continue
        refl = [_reflect_pt(P, coord, axis) for P in wp]
        snaps.append((refl, op))

    # 2) position·rotation·anchor 반사.
    pos = np.array(comp.position, dtype=np.float64)
    if axis == 'x':
        pos[0] = 2.0 * coord - pos[0]
        comp.anchor = _ANCHOR_XFLIP.get(int(comp.anchor), int(comp.anchor))
    else:
        pos[1] = 2.0 * coord - pos[1]
        comp.anchor = _ANCHOR_YFLIP.get(int(comp.anchor), int(comp.anchor))
    comp.position = pos
    comp.rotation = (360 - int(comp.rotation)) % 360

    # 3) 새 위치/회전으로 면 재생성 후 개구부 재투영.
    comp.generate_sub_components()
    if not old_ops:
        return
    new_ops = []
    for refl, op in snaps:
        if refl is None:
            new_ops.append(copy.deepcopy(op))   # 면 못 떴으면 원본 유지(안전)
            continue
        placed = None
        # 같은 종류 면 우선(slab→slab, wall→wall_*) 탐색.
        for face in _FACE_CANDIDATES:
            res = _project_corners_to_face(comp, face, refl)
            if res is not None:
                u0, v0, w0, h0 = res
                nop = dict(op)
                nop['face'] = face
                nop['u'] = u0; nop['v'] = v0; nop['w'] = w0; nop['h'] = h0
                placed = nop
                break
        new_ops.append(placed if placed is not None else copy.deepcopy(op))
    comp.openings = new_ops


__all__ = ['mirror_component_inplace']
