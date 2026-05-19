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
) -> MeshData:
    """
    중공 각형강관 메쉬 생성.
    16 꼭지점, 32 삼각형 (외부 8 + 내부 8 + 캡 16).
    """
    _, right, up, _ = _local_frame(start, end)

    hw = w / 2.0   # 100
    hh = h / 2.0   # 100
    iw = hw - t    # 92
    ih = hh - t    # 92

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


def _build_slab_mesh(slab) -> MeshData:
    """SlabData → 솔리드 박스 메쉬."""
    corners = slab.corners
    mn = corners.min(axis=0)
    mx = corners.max(axis=0)
    mx[2] = mn[2] + slab.thickness
    return build_solid_box(mn, mx, COLOR_CONCRETE)


def _build_wall_fill_mesh(wall_fill, partition=False) -> MeshData:
    """WallPanelData → 솔리드 판 메쉬."""
    corners = wall_fill.corners  # (4, 3)
    mn = corners.min(axis=0).copy()
    mx = corners.max(axis=0).copy()
    # 두께 방향: Y가 가장 좁은 축일 때 Y로 확장
    for axis in range(3):
        if mx[axis] - mn[axis] < 1.0:
            mn[axis] -= wall_fill.thickness / 2.0
            mx[axis] += wall_fill.thickness / 2.0
    color = COLOR_WALL_PARTITION if partition else COLOR_WALL
    return build_solid_box(mn, mx, color)


def _add_columns(meshes, columns):
    for col in columns:
        meshes.append(build_hollow_box_section(
            col.base, col.top, col.section_w, col.section_h, col.section_t))


def _add_beams(meshes, beams):
    for beam in beams:
        meshes.append(build_hollow_box_section(
            beam.start, beam.end, beam.section_w, beam.section_h, beam.section_t))


def build_module_mesh(module) -> MeshData:
    meshes = []
    _add_columns(meshes, module.columns)
    _add_beams(meshes, module.bottom_beams)
    _add_beams(meshes, module.top_beams)
    if module.slab is not None:
        meshes.append(_build_slab_mesh(module.slab))
    return _merge_meshes(meshes)


def build_floor_panel_mesh(panel) -> MeshData:
    meshes = []
    _add_beams(meshes, panel.edge_beams)
    if panel.slab is not None:
        meshes.append(_build_slab_mesh(panel.slab))
    return _merge_meshes(meshes)


def build_struct_wall_mesh(wall) -> MeshData:
    meshes = []
    _add_columns(meshes, wall.columns)
    if wall.bottom_runner:
        _add_beams(meshes, [wall.bottom_runner])
    if wall.top_runner:
        _add_beams(meshes, [wall.top_runner])
    if wall.wall_fill:
        meshes.append(_build_wall_fill_mesh(wall.wall_fill))
    return _merge_meshes(meshes)


def build_cantilever_beam_mesh(cb) -> MeshData:
    meshes = []
    if cb.beam:
        _add_beams(meshes, [cb.beam])
    return _merge_meshes(meshes)


def build_cantilever_slab_mesh(cs) -> MeshData:
    meshes = []
    _add_beams(meshes, cs.beams)
    if cs.slab is not None:
        meshes.append(_build_slab_mesh(cs.slab))
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
    """수직 3층 모듈: 4 통기둥 + 층 슬래브 받침보 12 + 옥상 프레임 보 4 + 슬래브 3."""
    meshes = []
    _add_columns(meshes, vm.columns)
    for fb in vm.bottom_beams:        # 3 stories × 4 beams
        _add_beams(meshes, fb)
    _add_beams(meshes, vm.top_beams)  # 4 옥상 프레임
    for slab in vm.slabs:
        meshes.append(_build_slab_mesh(slab))
    return _merge_meshes(meshes)


# 부재 타입 → 빌더 함수 dispatch 테이블 (모듈 로드 시 1회 구성).
# isinstance 체인 대신 ComponentType enum 으로 직접 조회 → O(1) + 추가 부재 시
# 새 키만 박으면 됨 (스파게티화 방지).
from modular_3d.model import ComponentType as _CT
_MESH_BUILDERS = {
    _CT.MODULE:           build_module_mesh,
    _CT.FLOOR_PANEL:      build_floor_panel_mesh,
    _CT.STRUCT_WALL:      build_struct_wall_mesh,
    _CT.CANTILEVER_BEAM:  build_cantilever_beam_mesh,
    _CT.CANTILEVER_SLAB:  build_cantilever_slab_mesh,
    _CT.MID_BEAM:         build_mid_beam_mesh,
    _CT.MID_COLUMN:       build_mid_column_mesh,
    _CT.VERTICAL_MODULE:  build_vertical3_module_mesh,
    _CT.CORE:             build_core_mesh,
    _CT.CORE_SLAB:        build_core_slab_mesh,
}


def build_component_mesh(comp) -> MeshData:
    """부재 타입에 따라 적절한 메쉬 빌더 호출. 미지의 타입은 module 폴백."""
    builder = _MESH_BUILDERS.get(comp.comp_type, build_module_mesh)
    return builder(comp)


def build_ghost_component_mesh(comp) -> MeshData:
    """부재 메쉬와 동일하지만 alpha=0.3."""
    verts, faces, colors = build_component_mesh(comp)
    ghost_colors = colors.copy()
    ghost_colors[:, 3] = 0.3
    return verts, faces, ghost_colors
