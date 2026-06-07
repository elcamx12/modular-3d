"""
부재 → 3D 메쉬 변환.
각형강관 중공 단면, 슬래브 솔리드 박스 등.
"""
import numpy as np
from typing import Tuple

# 색상 상수
COLOR_STEEL = np.array([0.50, 0.50, 0.50, 1.0], dtype=np.float32)   # #808080
COLOR_STEEL_INNER = np.array([0.375, 0.375, 0.375, 1.0], dtype=np.float32)  # #606060
COLOR_CONCRETE = np.array([0.75, 0.75, 0.75, 1.0], dtype=np.float32)  # #C0C0C0

# [2026-05-30 B2] modular_designer.html 의 부재 색상(사용자 지정 6가지)을 그대로 가져옴.
COLOR_COLUMN        = np.array([0.800, 0.200, 0.200, 1.0], dtype=np.float32)  # 0xCC3333 기둥
COLOR_COLUMN_IN     = np.array([0.600, 0.150, 0.150, 1.0], dtype=np.float32)  # 기둥 내부면(중공)
COLOR_FLOOR_BEAM    = np.array([0.133, 0.400, 0.800, 1.0], dtype=np.float32)  # 0x2266CC 바닥보
COLOR_FLOOR_BEAM_IN = np.array([0.100, 0.300, 0.600, 1.0], dtype=np.float32)  # 바닥보 내부면
COLOR_CEIL_BEAM     = np.array([0.333, 0.600, 0.867, 1.0], dtype=np.float32)  # 0x5599DD 천장보
COLOR_CEIL_BEAM_IN  = np.array([0.250, 0.450, 0.650, 1.0], dtype=np.float32)  # 천장보 내부면
COLOR_SLAB          = np.array([0.824, 0.824, 0.745, 1.0], dtype=np.float32)  # 0xD2D2BE 슬래브
COLOR_WALL_NL       = np.array([0.467, 0.600, 0.667, 1.0], dtype=np.float32)  # 0x7799AA 비내력 벽체
COLOR_WALL_NL_PARTITION = np.array([0.467, 0.600, 0.667, 0.35], dtype=np.float32)  # 반투명
COLOR_WINDOW        = np.array([0.533, 0.800, 0.933, 1.0], dtype=np.float32)  # 0x88CCEE 창호

MeshData = Tuple[np.ndarray, np.ndarray, np.ndarray]  # (vertices, faces, face_colors)


def _local_frame(start: np.ndarray, end: np.ndarray):
    """보/기둥 방향 벡터로 로컬 좌표계 (forward, right, up) 계산."""
    forward = end - start
    length = np.linalg.norm(forward)
    if length < 1e-6:
        return np.array([1, 0, 0.]), np.array([0, 1, 0.]), np.array([0, 0, 1.]), 0.0
    forward = forward / length

    # up 후보: Z축이 기본, forward가 Z와 평행하면 Y축 사용
    if abs(forward[2]) > 0.99:
        ref_up = np.array([0.0, 1.0, 0.0])
    else:
        ref_up = np.array([0.0, 0.0, 1.0])

    right = np.cross(forward, ref_up)
    right = right / np.linalg.norm(right)
    up = np.cross(right, forward)
    up = up / np.linalg.norm(up)
    return forward, right, up, length


def build_hollow_box_section(
    start: np.ndarray,
    end: np.ndarray,
    w: float = 200.0,
    h: float = 200.0,
    t: float = 8.0,
    color_outer: np.ndarray = COLOR_STEEL,
    color_inner: np.ndarray = COLOR_STEEL_INNER,
    anchor_right: int = 0,
    anchor_up: int = 0,
    nom_w: float = None,
    nom_h: float = None,
) -> MeshData:
    """
    중공 각형강관 메쉬 생성.
    16 꼭지점, 32 삼각형 (외부 8 + 내부 8 + 캡 16).

    [2026-05-31 외곽 고정·안쪽 성장] anchor_right/anchor_up ∈ {-1,0,+1}.
      0  : 부재 축 중심(기존 동작, 기본값 → 하위호환).
      ±1 : 그 단면축(+/-) 쪽 외곽면을 nom(공칭) 위치에 고정하고 안쪽으로 성장.
           offset = anchor · (nom - w)/2 만큼 단면 중심을 이동 → 외곽면 불변.
    nom_w/nom_h 미지정 시 앵커 무효(0 과 동일).
    """
    _, right, up, _ = _local_frame(start, end)

    hw = w / 2.0   # 100
    hh = h / 2.0   # 100
    iw = hw - t    # 92
    ih = hh - t    # 92

    # 외곽 고정 오프셋 — 단면 중심을 안쪽으로 이동(외곽면을 nom 위치에 유지).
    off_r = anchor_right * (nom_w - w) / 2.0 if (anchor_right and nom_w) else 0.0
    off_u = anchor_up * (nom_h - h) / 2.0 if (anchor_up and nom_h) else 0.0
    if off_r or off_u:
        shift = (off_r * right + off_u * up)
        start = start + shift
        end = end + shift

    # 8 꼭지점 × 2끝 = 16
    offsets_outer = [
        -hw * right - hh * up,  # 0: 외부 좌하
        +hw * right - hh * up,  # 1: 외부 우하
        +hw * right + hh * up,  # 2: 외부 우상
        -hw * right + hh * up,  # 3: 외부 좌상
    ]
    offsets_inner = [
        -iw * right - ih * up,  # 4: 내부 좌하
        +iw * right - ih * up,  # 5: 내부 우하
        +iw * right + ih * up,  # 6: 내부 우상
        -iw * right + ih * up,  # 7: 내부 좌상
    ]

    verts = np.zeros((16, 3), dtype=np.float32)
    for i, off in enumerate(offsets_outer):
        verts[i] = start + off          # 시작단 외부 0-3
    for i, off in enumerate(offsets_inner):
        verts[4 + i] = start + off      # 시작단 내부 4-7
    for i, off in enumerate(offsets_outer):
        verts[8 + i] = end + off        # 끝단 외부 8-11
    for i, off in enumerate(offsets_inner):
        verts[12 + i] = end + off       # 끝단 내부 12-15

    faces = np.array([
        # 외부 4면 (8 삼각형)
        [0, 8, 9], [0, 9, 1],     # 하
        [1, 9, 10], [1, 10, 2],   # 우
        [2, 10, 11], [2, 11, 3],  # 상
        [3, 11, 8], [3, 8, 0],    # 좌

        # 내부 4면 (8 삼각형, 반전 와인딩)
        [4, 5, 13], [4, 13, 12],  # 하
        [5, 6, 14], [5, 14, 13],  # 우
        [6, 7, 15], [6, 15, 14],  # 상
        [7, 4, 12], [7, 12, 15],  # 좌

        # 시작단 캡 (8 삼각형)
        [0, 1, 5], [0, 5, 4],
        [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6],
        [3, 0, 4], [3, 4, 7],

        # 끝단 캡 (8 삼각형, 반전)
        [8, 12, 13], [8, 13, 9],
        [9, 13, 14], [9, 14, 10],
        [10, 14, 15], [10, 15, 11],
        [11, 15, 12], [11, 12, 8],
    ], dtype=np.uint32)

    # 면 색상: 외부+캡 = color_outer, 내부 = color_inner
    n_faces = len(faces)
    colors = np.zeros((n_faces, 4), dtype=np.float32)
    colors[:8] = color_outer      # 외부
    colors[8:16] = color_inner    # 내부
    colors[16:24] = color_outer   # 시작 캡
    colors[24:32] = color_outer   # 끝 캡

    return verts, faces, colors


def build_h_section(
    start: np.ndarray,
    end: np.ndarray,
    H: float = 200.0,
    B: float = 200.0,
    t1: float = 8.0,
    t2: float = None,
    color: np.ndarray = COLOR_STEEL,
) -> MeshData:
    """H형강(I자 단면) 압출 메시. 강축 휨 — 웨브가 수직(up), 플랜지 수평(right).

    H: 형강 높이(up), B: 플랜지 폭(right), t1: 웨브 두께, t2: 플랜지 두께.
    t2 미지정 시 표시용으로 1.5·t1 근사(공칭 H200×200×8×12 와 일치). 실제 단면
    치수는 구조해석 자동설계가 결정 — 본 메시는 형상 표현용.
    단면 외곽 12점을 start→end 로 압출(측면 12 quad + 양끝 캡 I자 3사각형).
    """
    if t2 is None:
        t2 = 1.5 * t1
    _, right, up, _ = _local_frame(start, end)
    hb = B / 2.0
    hh = H / 2.0
    hw = hh - t2     # 웨브 상/하단 y
    tw = t1 / 2.0    # 웨브 반폭

    # I자 외곽 12점 (right=x, up=y). 반시계.
    pts2d = [
        (-hb, -hh), (hb, -hh), (hb, -hw), (tw, -hw),
        (tw, hw), (hb, hw), (hb, hh), (-hb, hh),
        (-hb, hw), (-tw, hw), (-tw, -hw), (-hb, -hw),
    ]
    n = len(pts2d)
    verts = np.zeros((2 * n, 3), dtype=np.float32)
    for i, (x, y) in enumerate(pts2d):
        off = x * right + y * up
        verts[i] = start + off
        verts[n + i] = end + off

    faces = []
    # 측면 12 quad
    for i in range(n):
        a, b = i, (i + 1) % n
        c, d = n + (i + 1) % n, n + i
        faces.append([a, b, c]); faces.append([a, c, d])
    # 양끝 캡 — I자를 3 사각형(하플랜지·상플랜지·웨브)으로 분해.
    cap_quads = [
        (0, 1, 2, 11),   # 하플랜지
        (8, 5, 6, 7),    # 상플랜지
        (10, 3, 4, 9),   # 웨브
    ]
    for (a, b, c, d) in cap_quads:
        faces.append([a, b, c]); faces.append([a, c, d])            # start 캡
        faces.append([n + a, n + b, n + c]); faces.append([n + a, n + c, n + d])  # end 캡

    F = np.array(faces, dtype=np.uint32)
    C = np.tile(color, (len(F), 1))
    return verts, F, C


def build_solid_box(
    min_corner: np.ndarray,
    max_corner: np.ndarray,
    color: np.ndarray = COLOR_CONCRETE,
) -> MeshData:
    """6면 솔리드 박스 (슬래브, 벽체 등)."""
    x0, y0, z0 = min_corner
    x1, y1, z1 = max_corner

    verts = np.array([
        [x0, y0, z0],  # 0
        [x1, y0, z0],  # 1
        [x1, y1, z0],  # 2
        [x0, y1, z0],  # 3
        [x0, y0, z1],  # 4
        [x1, y0, z1],  # 5
        [x1, y1, z1],  # 6
        [x0, y1, z1],  # 7
    ], dtype=np.float32)

    faces = np.array([
        # 하면 (z=z0)
        [0, 2, 1], [0, 3, 2],
        # 상면 (z=z1)
        [4, 5, 6], [4, 6, 7],
        # 전면 (y=y0)
        [0, 1, 5], [0, 5, 4],
        # 후면 (y=y1)
        [2, 3, 7], [2, 7, 6],
        # 좌면 (x=x0)
        [0, 4, 7], [0, 7, 3],
        # 우면 (x=x1)
        [1, 2, 6], [1, 6, 5],
    ], dtype=np.uint32)

    colors = np.tile(color, (12, 1))
    return verts, faces, colors


def _merge_meshes(mesh_list: list) -> MeshData:
    """여러 메쉬를 하나로 합치기."""
    if not mesh_list:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32), np.zeros((0, 4), np.float32)

    all_verts = []
    all_faces = []
    all_colors = []
    vert_offset = 0

    for verts, faces, colors in mesh_list:
        all_verts.append(verts)
        all_faces.append(faces + vert_offset)
        all_colors.append(colors)
        vert_offset += len(verts)

    return (
        np.concatenate(all_verts, axis=0),
        np.concatenate(all_faces, axis=0),
        np.concatenate(all_colors, axis=0),
    )


COLOR_WALL = np.array([0.815, 0.815, 0.815, 1.0], dtype=np.float32)  # #D0D0D0
COLOR_WALL_PARTITION = np.array([0.815, 0.815, 0.815, 0.35], dtype=np.float32)  # 반투명


# ── 임의 폴리곤 슬래브(귀자르기 삼각분할) ─────────────────────
def _poly_signed_area(pts) -> float:
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def _tri_cross(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _pt_in_tri(p, a, b, c) -> bool:
    d1 = _tri_cross(a, b, p)
    d2 = _tri_cross(b, c, p)
    d3 = _tri_cross(c, a, p)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def _earclip_triangulate(pts):
    """단순 폴리곤(오목 허용) 귀자르기 삼각분할 → 삼각형 인덱스 [(i,j,k),...]."""
    n = len(pts)
    if n < 3:
        return []
    idx = list(range(n))
    if _poly_signed_area(pts) < 0:   # CCW 로 통일
        idx.reverse()
    tris = []
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        m = len(idx)
        ear = False
        for ii in range(m):
            i0, i1, i2 = idx[(ii - 1) % m], idx[ii], idx[(ii + 1) % m]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if _tri_cross(a, b, c) <= 0:   # 오목(reflex) 꼭짓점 — 귀 아님
                continue
            ok = True
            for jj in idx:
                if jj in (i0, i1, i2):
                    continue
                if _pt_in_tri(pts[jj], a, b, c):
                    ok = False
                    break
            if not ok:
                continue
            tris.append((i0, i1, i2))
            del idx[ii]
            ear = True
            break
        if not ear:   # 퇴화 — 폴백(부채꼴)
            break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    elif len(idx) > 3:   # 폴백: 남은 것 부채꼴
        for ii in range(1, len(idx) - 1):
            tris.append((idx[0], idx[ii], idx[ii + 1]))
    return tris


def _build_slab_polygon_mesh(corners, thickness, color) -> MeshData:
    """임의 N각형 슬래브 프리즘 — 윗/아랫면(삼각분할) + 옆면(두께). 개구부 미지원."""
    arr = np.asarray(corners, dtype=np.float64)
    xy = [(float(c[0]), float(c[1])) for c in arr]
    n = len(xy)
    z0 = float(arr[:, 2].min())
    z1 = z0 + float(thickness)
    tris = _earclip_triangulate(xy)
    verts = [[x, y, z0] for (x, y) in xy] + [[x, y, z1] for (x, y) in xy]
    faces = []
    for (i, j, k) in tris:
        faces.append([i, k, j])           # 아랫면(법선 -z)
        faces.append([n + i, n + j, n + k])  # 윗면(법선 +z)
    for i in range(n):                     # 옆면 두께
        j = (i + 1) % n
        faces.append([i, j, n + j])
        faces.append([i, n + j, n + i])
    V = np.array(verts, dtype=np.float32)
    F = np.array(faces, dtype=np.uint32)
    C = np.tile(np.asarray(color, dtype=np.float32), (len(F), 1))
    return V, F, C


def _build_slab_mesh(slab, openings=None, color=COLOR_SLAB) -> MeshData:
    """SlabData → 솔리드 박스 메쉬. openings 있으면 z 관통 사각 구멍.

    슬래브는 xy 평면 + z 두께 박스 → 관통축 = z(2), 평면축 = (x,y).
    [폴리곤] corners 가 4개 초과면 임의 N각형 프리즘(개구부 미지원).
    """
    corners = slab.corners
    if len(corners) > 4:
        return _build_slab_polygon_mesh(corners, slab.thickness, color)
    mn = corners.min(axis=0).copy()
    mx = corners.max(axis=0).copy()
    mx[2] = mn[2] + slab.thickness
    if openings:
        from modular_3d.render.opening_mesh import (
            build_box_with_holes, openings_to_world_rects)
        rects = openings_to_world_rects(corners, openings, (0, 1))
        holed = build_box_with_holes(mn, mx, rects, 2, color)
        if holed is not None:
            return holed
    return build_solid_box(mn, mx, color)


def _build_wall_fill_mesh(wall_fill, partition=False, openings=None) -> MeshData:
    """WallPanelData → 솔리드 판 메쉬. openings 있으면 두께 관통 사각 구멍."""
    corners = wall_fill.corners  # (4, 3)
    mn = corners.min(axis=0).copy()
    mx = corners.max(axis=0).copy()
    # 두께 방향: 가장 좁은 축을 thickness 로 확장 (= 개구부 관통축)
    through_axis = 2
    for axis in range(3):
        if mx[axis] - mn[axis] < 1.0:
            mn[axis] -= wall_fill.thickness / 2.0
            mx[axis] += wall_fill.thickness / 2.0
            through_axis = axis
    color = COLOR_WALL_NL_PARTITION if partition else COLOR_WALL_NL
    if openings:
        from modular_3d.render.opening_mesh import (
            build_box_with_holes, openings_to_world_rects)
        plane_axes = tuple(a for a in (0, 1, 2) if a != through_axis)
        rects = openings_to_world_rects(corners, openings, plane_axes)
        holed = build_box_with_holes(mn, mx, rects, through_axis, color)
        if holed is not None:
            return holed
    return build_solid_box(mn, mx, color)


# 공칭 단면(외곽 고정 기준) — SECTION_W_MM 와 동일. 모듈 footprint = 기둥
# 중심선 ± 공칭 절반으로 정의되므로, 설계 단면이 달라도 이 외곽면을 유지한다.
_NOMINAL_OUTER_MM = 200.0


def _add_columns(meshes, columns, color=COLOR_COLUMN, color_in=COLOR_COLUMN_IN,
                 outer_anchor=False, center_xy=None):
    """기둥 메쉬. outer_anchor=True 면 모듈 중심 반대쪽(외곽) 면을 공칭 위치에
    고정하고 안쪽으로 성장(단면 설계 탭 컴포넌트 3D 전용)."""
    for col in columns:
        ar = au = 0
        if outer_anchor and center_xy is not None:
            _, right, up, _ = _local_frame(col.base, col.top)
            d = np.asarray(col.base, float)[:2] - np.asarray(center_xy, float)
            dr = float(d[0] * right[0] + d[1] * right[1])   # 외곽 방향 · right
            du = float(d[0] * up[0] + d[1] * up[1])          # 외곽 방향 · up
            tol = 1.0
            ar = (1 if dr > tol else (-1 if dr < -tol else 0))
            au = (1 if du > tol else (-1 if du < -tol else 0))
        meshes.append(build_hollow_box_section(
            col.base, col.top, col.section_w, col.section_h, col.section_t,
            color_outer=color, color_inner=color_in,
            anchor_right=ar, anchor_up=au,
            nom_w=_NOMINAL_OUTER_MM, nom_h=_NOMINAL_OUTER_MM))


def _add_beams(meshes, beams, color=COLOR_FLOOR_BEAM, color_in=COLOR_FLOOR_BEAM_IN,
               outer_anchor=False, center_xy=None, vert_anchor=0, col_sw=0.0):
    """보 메쉬. outer_anchor=True 면 외곽 고정·안쪽 성장(단면 설계 탭 전용):
      - 수평(right): 모듈/패널 중심 반대쪽(가장자리 바깥) 면 고정.
      - 수직(up): vert_anchor=+1(천장보)=위 면 / -1(바닥보)=아래 면 고정.
    H형강 보(build_h_section)는 앵커 미적용(축 중심 유지).

    col_sw>0 & outer_anchor 면 보 양끝을 *기둥 안쪽 면* 까지 줄인다(시각화 전용).
    기둥은 외곽 고정이라 두꺼워지면 안쪽 면이 안으로 들어오므로, 보 끝을 그만큼
    당겨야 겹침/틈이 없다. 양끝을 각각 (col_sw - 공칭/2) 만큼 길이축 안쪽으로
    이동(중심선 경간·물량·해석 수치는 불변)."""
    shrink = (col_sw - _NOMINAL_OUTER_MM / 2.0) if (outer_anchor and col_sw > 0.0) else 0.0
    for beam in beams:
        start = np.asarray(beam.start, float)
        end = np.asarray(beam.end, float)
        if shrink != 0.0:
            along = end - start
            L = float(np.linalg.norm(along))
            if L > 1e-6:
                u = along / L
                s = max(min(shrink, L / 2.0 - 1.0), -L)   # 보가 사라지지 않게
                start = start + u * s
                end = end - u * s
        if getattr(beam, 'section_type', 'shs') == 'h':
            # H형강 — H=section_h(높이), B=section_w(폭), t1=section_t(웨브).
            meshes.append(build_h_section(
                start, end, beam.section_h, beam.section_w, beam.section_t,
                color=color))
        else:
            ar = au = 0
            if outer_anchor:
                _, right, up, _ = _local_frame(start, end)
                if vert_anchor:
                    au = vert_anchor * (1 if up[2] >= 0 else -1)
                if center_xy is not None:
                    mid = (start[:2] + end[:2]) * 0.5
                    d = mid - np.asarray(center_xy, float)
                    dr = float(d[0] * right[0] + d[1] * right[1])
                    ar = (1 if dr > 1.0 else (-1 if dr < -1.0 else 0))
            meshes.append(build_hollow_box_section(
                start, end, beam.section_w, beam.section_h, beam.section_t,
                color_outer=color, color_inner=color_in,
                anchor_right=ar, anchor_up=au,
                nom_w=_NOMINAL_OUTER_MM, nom_h=_NOMINAL_OUTER_MM))


def _openings_for_face(comp, face: str, default_face: str):
    """comp.openings 중 face 에 해당하는 것만(레거시는 default_face 로 간주)."""
    ops = getattr(comp, 'openings', None) or []
    return [o for o in ops if o.get('face', default_face) == face]


def build_module_mesh(module, outer_anchor=False) -> MeshData:
    """모듈 메쉬. outer_anchor=True(단면 설계 탭 전용) 면 기둥을 외곽 고정·안쪽 성장.

    기본 False → 기존 모든 호출부(와이어프레임/메쉬뷰) 동작 불변(하위호환).
    """
    meshes = []
    center_xy = None
    if outer_anchor and getattr(module, 'columns', None):
        bases = np.array([np.asarray(c.base, float)[:2] for c in module.columns])
        if len(bases):
            center_xy = bases.mean(axis=0)
    _add_columns(meshes, module.columns,
                 outer_anchor=outer_anchor, center_xy=center_xy)
    _add_beams(meshes, module.bottom_beams,
               outer_anchor=outer_anchor, center_xy=center_xy, vert_anchor=-1)
    _add_beams(meshes, module.top_beams,
               color=COLOR_CEIL_BEAM, color_in=COLOR_CEIL_BEAM_IN,
               outer_anchor=outer_anchor, center_xy=center_xy, vert_anchor=1)
    if module.slab is not None:
        meshes.append(_build_slab_mesh(
            module.slab, _openings_for_face(module, 'slab', 'slab')))
    # 비내력 벽 4면 (각 면별 개구부) — 반투명(partition). 토글 OFF 면 생략.
    if _SHOW_WALL_FILL:
        for i, wf in enumerate(getattr(module, 'wall_fills', None) or []):
            meshes.append(_build_wall_fill_mesh(
                wf, partition=True,
                openings=_openings_for_face(module, f'wall_{i}', f'wall_{i}')))
    return _merge_meshes(meshes)


def build_component_class_meshes(comp, outer_anchor=False) -> dict:
    """[7C-2] 컴포넌트의 강재 부재를 색 클래스별 메시로 분리 → {cc: MeshData}.

    cc ∈ {'column','ceil','floor'}. 슬래브/벽/창호(비강재)는 제외. hover 시 클래스별
    단면명 표시용(컴포넌트 내 같은 클래스는 단면 동일). 모듈/바닥패널/벽패널/캔틸 공통
    속성(columns/top_beams/bottom_beams/edge_beams/runners/beam/beams)으로 수집하고
    _add_columns/_add_beams 의 외곽 고정·안쪽 성장(outer_anchor)을 그대로 따른다.
    """
    # [수직3층모듈 호환] bottom_beams 가 *리스트의 리스트*(층×보)일 수 있어 평탄화 필요.
    def _flat(seq):
        out = []
        for it in (seq or []):
            if isinstance(it, (list, tuple)):
                out.extend(it)
            else:
                out.append(it)
        return out

    cols = _flat(getattr(comp, 'columns', []))
    ceil_beams = _flat(getattr(comp, 'top_beams', []))
    floor_beams = _flat(getattr(comp, 'bottom_beams', [])) + _flat(getattr(comp, 'edge_beams', []))
    tr = getattr(comp, 'top_runner', None)
    if tr is not None:
        ceil_beams.append(tr)
    br = getattr(comp, 'bottom_runner', None)
    if br is not None:
        floor_beams.append(br)
    cbeam = getattr(comp, 'beam', None)
    if cbeam is not None and hasattr(cbeam, 'section_w'):
        ceil_beams.append(cbeam)            # 캔틸레버보 = 천장보 색
    floor_beams += list(getattr(comp, 'beams', []) or [])   # 캔틸 슬래브 보 = 바닥보 색

    center_xy = None
    col_sw = 0.0
    if outer_anchor and cols:
        bases = np.array([np.asarray(c.base, float)[:2] for c in cols])
        if len(bases):
            center_xy = bases.mean(axis=0)
        # 보 길이를 기둥 안쪽 면에 맞추기 위한 대표 기둥 단면폭.
        col_sw = max((float(getattr(c, 'section_w', 0.0)) for c in cols), default=0.0)

    out: dict = {}
    if cols:
        cm = []
        _add_columns(cm, cols, outer_anchor=outer_anchor, center_xy=center_xy)
        out['column'] = _merge_meshes(cm)
    if ceil_beams:
        em = []
        _add_beams(em, ceil_beams, color=COLOR_CEIL_BEAM, color_in=COLOR_CEIL_BEAM_IN,
                   outer_anchor=outer_anchor, center_xy=center_xy, vert_anchor=1,
                   col_sw=col_sw)
        out['ceil'] = _merge_meshes(em)
    if floor_beams:
        fm = []
        _add_beams(fm, floor_beams,
                   outer_anchor=outer_anchor, center_xy=center_xy, vert_anchor=-1,
                   col_sw=col_sw)
        out['floor'] = _merge_meshes(fm)
    # [9-2] 비강재(슬래브·비내력벽) — 정의 탭처럼 형상 표시용. hover 대상 아님(name='').
    others = []
    slab = getattr(comp, 'slab', None)
    if slab is not None:
        others.append(_build_slab_mesh(slab, _openings_for_face(comp, 'slab', 'slab')))
    for sl in (getattr(comp, 'slabs', None) or []):   # 수직3층모듈 등 복수 슬래브
        others.append(_build_slab_mesh(sl))
    for i, wf in enumerate(getattr(comp, 'wall_fills', None) or []):
        others.append(_build_wall_fill_mesh(
            wf, partition=True,
            openings=_openings_for_face(comp, f'wall_{i}', f'wall_{i}')))
    wf1 = getattr(comp, 'wall_fill', None)
    if wf1 is not None:
        others.append(_build_wall_fill_mesh(
            wf1, partition=True, openings=_openings_for_face(comp, 'wall', 'wall')))
    if others:
        out['other'] = _merge_meshes(others)
    return out


def _beams_center_xy(beams):
    """보 묶음의 중점 xy 평균(외곽 방향 판정용)."""
    pts = []
    for b in beams or []:
        s = np.asarray(b.start, float)[:2]
        e = np.asarray(b.end, float)[:2]
        pts.append((s + e) * 0.5)
    if not pts:
        return None
    return np.mean(np.array(pts), axis=0)


def build_floor_panel_mesh(panel, outer_anchor=False) -> MeshData:
    """바닥패널 메쉬. outer_anchor=True 면 테두리보를 외곽 고정·안쪽 성장(바닥=아래)."""
    meshes = []
    center_xy = _beams_center_xy(panel.edge_beams) if outer_anchor else None
    _add_beams(meshes, panel.edge_beams,
               outer_anchor=outer_anchor, center_xy=center_xy, vert_anchor=-1)
    if panel.slab is not None:
        meshes.append(_build_slab_mesh(
            panel.slab, _openings_for_face(panel, 'slab', 'slab')))
    return _merge_meshes(meshes)


# ── [2026-06-02] 배치설계 '벽 채움면' 표시 토글 ──────────────
# 벽패널 채움·내벽·모듈 외피 벽 채움은 모두 _build_wall_fill_mesh 로 그려진다.
# 아래 전역 스위치를 끄면 메인 부재 빌드(모듈/벽패널/내벽)에서 채움면을 빼서
# 프레임·슬래브·코어만 남긴다. (hover 용 build_component_class_meshes 는 제외 —
# 배치설계 전용 토글이라 다른 탭의 형상 표시에는 영향을 주지 않는다.)
_SHOW_WALL_FILL = True


def set_wall_fill_visible(show: bool) -> None:
    """배치설계 벽 채움면 표시 토글. 변경 후 부재 재빌드는 호출측 책임."""
    global _SHOW_WALL_FILL
    _SHOW_WALL_FILL = bool(show)
    # 형상(벽 표시 유무)이 바뀌므로 형상 캐시를 무효화.
    clear_mesh_cache()


def build_struct_wall_mesh(wall, outer_anchor=False) -> MeshData:
    """벽패널 메쉬. outer_anchor=True 면 기둥·러너를 외곽 고정·안쪽 성장."""
    meshes = []
    center_xy = None
    if outer_anchor and getattr(wall, 'columns', None):
        bases = np.array([np.asarray(c.base, float)[:2] for c in wall.columns])
        if len(bases):
            center_xy = bases.mean(axis=0)
    _add_columns(meshes, wall.columns,
                 outer_anchor=outer_anchor, center_xy=center_xy)
    if wall.bottom_runner:
        _add_beams(meshes, [wall.bottom_runner],
                   outer_anchor=outer_anchor, center_xy=center_xy, vert_anchor=-1)
    if wall.top_runner:
        _add_beams(meshes, [wall.top_runner],
                   color=COLOR_CEIL_BEAM, color_in=COLOR_CEIL_BEAM_IN,
                   outer_anchor=outer_anchor, center_xy=center_xy, vert_anchor=1)
    # 채움면은 토글 OFF 면 생략 → 기둥·런너(프레임)만 남는다.
    if wall.wall_fill and _SHOW_WALL_FILL:
        meshes.append(_build_wall_fill_mesh(
            wall.wall_fill, partition=True,
            openings=_openings_for_face(wall, 'wall', 'wall')))
    return _merge_meshes(meshes)


def build_interior_wall_mesh(wall) -> MeshData:
    """내벽 — 채움 판 1장만 (반투명, 개구부 face='wall'). 토글 OFF 면 빈 메시."""
    meshes = []
    if getattr(wall, 'wall_fill', None) is not None and _SHOW_WALL_FILL:
        meshes.append(_build_wall_fill_mesh(
            wall.wall_fill, partition=True,
            openings=_openings_for_face(wall, 'wall', 'wall')))
    return _merge_meshes(meshes)


def build_cantilever_beam_mesh(cb) -> MeshData:
    meshes = []
    if cb.beam:
        # 캔틸레버보는 천장보에 연결되므로 천장보 색으로 표시.
        _add_beams(meshes, [cb.beam],
                   color=COLOR_CEIL_BEAM, color_in=COLOR_CEIL_BEAM_IN)
    return _merge_meshes(meshes)


def build_cantilever_slab_mesh(cs) -> MeshData:
    meshes = []
    _add_beams(meshes, cs.beams)
    if cs.slab is not None:
        meshes.append(_build_slab_mesh(
            cs.slab, _openings_for_face(cs, 'slab', 'slab')))
    return _merge_meshes(meshes)


def build_mid_beam_mesh(mb) -> MeshData:
    meshes = []
    if mb.beam:
        _add_beams(meshes, [mb.beam])
    return _merge_meshes(meshes)


def build_mid_column_mesh(mc) -> MeshData:
    meshes = []
    if mc.column:
        _add_columns(meshes, [mc.column])
    return _merge_meshes(meshes)


# 반투명 콘크리트 — RC 코어벽용. 슬래브는 불투명(COLOR_CONCRETE) 그대로.
COLOR_CORE_WALL = np.array([0.75, 0.75, 0.75, 0.7], dtype=np.float32)


def build_core_mesh(core) -> MeshData:
    """RC 코어벽 — 단순 솔리드 박스 (회전·앵커 반영된 월드 8 코너의 AABB).

    [회전 처리]
    Component.get_world_corners 가 회전·앵커를 이미 적용한 월드 좌표를 반환하므로
    그 8 코너의 min/max 로 AABB 를 그리면 회전 90/270 일 때도 자동으로 width/depth
    가 swap 된 형태로 나온다. (다른 부재의 WallPanelData 처리와 동일 패턴.)

    [색]
    COLOR_CORE_WALL (콘크리트 회색 + alpha 0.7) — 슬래브와 같은 RGB,
    벽이라 약간 반투명으로 분리.
    """
    corners = core.get_world_corners()  # (8, 3)
    mn = corners.min(axis=0)
    mx = corners.max(axis=0)
    return build_solid_box(mn, mx, COLOR_CORE_WALL)


def build_core_slab_mesh(cs) -> MeshData:
    """RC 코어 슬래브 — 보 없는 순수 RC 판. SlabData 1 개만."""
    meshes = []
    if cs.slab is not None:
        meshes.append(_build_slab_mesh(cs.slab))
    return _merge_meshes(meshes)


def build_vertical3_module_mesh(vm) -> MeshData:
    """수직 3층 모듈: 4 통기둥 + 층 슬래브 받침보 12 + 옥상 프레임 보 4 + 슬래브 3 + 비내력벽 12."""
    meshes = []
    _add_columns(meshes, vm.columns)
    for fb in vm.bottom_beams:        # 3 stories × 4 beams
        _add_beams(meshes, fb)
    _add_beams(meshes, vm.top_beams,  # 4 옥상 프레임 = 천장보
               color=COLOR_CEIL_BEAM, color_in=COLOR_CEIL_BEAM_IN)
    for i, slab in enumerate(vm.slabs):
        meshes.append(_build_slab_mesh(
            slab, _openings_for_face(vm, f'slab_{i}', f'slab_{i}')))
    # 비내력 벽 12면 (층 4면 × 3) — 일반 모듈과 동일하게 면별 개구부 적용·토글 가드.
    if _SHOW_WALL_FILL:
        for i, wf in enumerate(getattr(vm, 'wall_fills', None) or []):
            meshes.append(_build_wall_fill_mesh(
                wf, partition=True,
                openings=_openings_for_face(vm, f'wall_{i}', f'wall_{i}')))
    return _merge_meshes(meshes)


# 부재 타입 → 빌더 함수 dispatch 테이블 (모듈 로드 시 1회 구성).
# isinstance 체인 대신 ComponentType enum 으로 직접 조회 → O(1) + 추가 부재 시
# 새 키만 박으면 됨 (스파게티화 방지).
from modular_3d.model import ComponentType as _CT
_MESH_BUILDERS = {
    _CT.MODULE:           build_module_mesh,
    _CT.FLOOR_PANEL:      build_floor_panel_mesh,
    _CT.STRUCT_WALL:      build_struct_wall_mesh,
    _CT.INTERIOR_WALL:    build_interior_wall_mesh,
    _CT.CANTILEVER_BEAM:  build_cantilever_beam_mesh,
    _CT.CANTILEVER_SLAB:  build_cantilever_slab_mesh,
    _CT.MID_BEAM:         build_mid_beam_mesh,
    _CT.MID_COLUMN:       build_mid_column_mesh,
    _CT.VERTICAL_MODULE:  build_vertical3_module_mesh,
    _CT.CORE:             build_core_mesh,
    _CT.CORE_SLAB:        build_core_slab_mesh,
}


# ── 형상 캐시 (3D 복제 최적화 1단계) ───────────────────────
# [함정] 같은 형상의 부재는 메시 정점 = (부재 position) + (형상 오프셋) 이 성립한다
# (A/B/C 전수검증으로 확인: 같은 형상지문이면 z뿐 아니라 xy 평행이동만 다름).
# 그래서 '위치를 뺀 로컬 형상'을 캐시해 두고, 다음 같은 형상 부재는 그 부재
# position 만 더해 돌려준다. 형상에 영향을 주는 모든 속성을 키에 넣어야 한다 —
# 하나라도 빠지면 다른 형상이 같은 키로 잘못 공유된다. 특히 _SHOW_WALL_FILL
# (벽체 표시 전역 토글)은 메시 구성을 바꾸므로 키에 포함하고, 토글 변경 시에는
# set_wall_fill_visible 에서 캐시를 비운다.
_MESH_CACHE: dict = {}


def clear_mesh_cache() -> None:
    """형상 캐시 비우기 — 전역 표시 옵션 변경 등으로 형상이 달라질 때 호출."""
    _MESH_CACHE.clear()


def _shape_key(comp):
    """형상에 영향을 주는 속성만으로 캐시 키 구성(위치는 제외)."""
    dims = dict(getattr(comp, 'dimensions', {}))
    ops = getattr(comp, 'openings', None) or []
    return (
        comp.comp_type.value,
        int(getattr(comp, 'rotation', 0)),
        int(getattr(comp, 'anchor', 0)),
        str(getattr(comp, 'beam_section_type', 'shs')),
        bool(getattr(comp, 'merge_with_panel', False)),
        repr(sorted((k, str(v)) for k, v in dims.items())),
        repr(ops),
        bool(_SHOW_WALL_FILL),
    )


def build_component_mesh(comp) -> MeshData:
    """부재 타입에 따라 적절한 메쉬 빌더 호출. 미지의 타입은 module 폴백.

    [3D 복제 최적화] 같은 형상은 한 번만 빌드하고, 이후엔 캐시된 로컬 형상에
    부재 position 을 더해 재사용한다. 첫 빌드 결과는 원본 그대로 반환(비트 동일),
    이후 재사용분은 부동소수 한계(좌표 크기 대비 ~0.005mm) 내에서 동일하다.
    """
    key = _shape_key(comp)
    pos = np.asarray(comp.position, dtype=np.float64)
    cached = _MESH_CACHE.get(key)
    if cached is not None:
        local_v, faces, colors = cached
        verts = (local_v + pos).astype(np.float32)
        return verts, faces, colors
    builder = _MESH_BUILDERS.get(comp.comp_type, build_module_mesh)
    verts, faces, colors = builder(comp)
    # 위치를 뺀 로컬 형상 저장 — 다음 같은 형상 부재가 재사용.
    local_v = verts.astype(np.float64) - pos
    _MESH_CACHE[key] = (local_v, faces, colors)
    return verts, faces, colors


def build_ghost_component_mesh(comp) -> MeshData:
    """부재 메쉬와 동일하지만 alpha=0.3."""
    verts, faces, colors = build_component_mesh(comp)
    ghost_colors = colors.copy()
    ghost_colors[:, 3] = 0.3
    return verts, faces, ghost_colors
