"""alignment_view 의 순수 기하·레이어 헬퍼 분리.

부재 → XY 사각형 투영, 레이어 가시성 판정, 종속 부모 가능 여부 등
self 없이 model 만 의존하는 함수들을 모았다.

[설계 원칙]
- vispy 의존 금지, PyQt 의존 금지 — 단위 테스트 가능.
- alignment_view.py 는 본 모듈을 import 후 그대로 호출.
"""
from __future__ import annotations

from modular_3d.model import (
    Module, FloorPanel, StructWall, InteriorWall,
    CantileverBeam, CantileverSlab, MidBeam, MidColumn, ComponentType,
    Core, CoreSlab,
)

# ── 공유 상수 (alignment_view + alignment_paint + alignment_pick 모두 사용) ──
# [버그 수정 2026-05-08] Mixin 분리(alignment_paint/pick) 시 이 상수들이 거기서
# import 안 되어 NameError 발생. 본 helpers 모듈로 이동 + 모두 여기서 import.

# 레이어
LAYER_BOTTOM = 0
LAYER_TOP = 1

# 타입별 색상
from modular_3d.model import ComponentType as _CT
TYPE_COLORS = {
    _CT.MODULE:           (80, 80, 200),
    _CT.FLOOR_PANEL:      (80, 180, 80),
    _CT.STRUCT_WALL:      (200, 80, 80),
    _CT.INTERIOR_WALL:    (230, 140, 60),
    _CT.CANTILEVER_BEAM:  (180, 80, 180),
    _CT.CANTILEVER_SLAB:  (180, 120, 180),
    _CT.MID_BEAM:         (80, 180, 180),
    _CT.MID_COLUMN:       (180, 180, 80),
    # RC 코어 — 콘크리트 회색계열. 벽은 약간 진하게, 슬래브는 더 밝게.
    _CT.CORE:             (170, 170, 170),
    _CT.CORE_SLAB:        (200, 200, 200),
}

# 상태 (AlignmentCanvas 내부 FSM)
STATE_IDLE = 'idle'
STATE_SELECTED = 'selected'
STATE_DIRECTION = 'direction'
STATE_REFERENCE = 'reference'
STATE_DEPENDENCY_PICK = 'dependency_pick'   # 단계 4: 종속 부재 부모 선택

# 정렬·스냅 임계 — 갭·기둥 폭은 카탈로그에서 가져옴.
from modular_3d.카탈로그.geometry import (
    MODULE_JOINT_GAP_MM as _GAP_MM,
    SECTION_W_MM as _SECTION_W,
)
ALIGN_TOL = 100.0          # 정렬 오차 임계
GAP_20 = float(_GAP_MM)    # 정확히 20mm 갭 제외 (카탈로그)
SNAP_20 = float(_GAP_MM)   # 갭 20mm (카탈로그)
SNAP_220 = float(_SECTION_W + _GAP_MM)  # 기둥(200) + 갭(20) = 220mm
EPS = 0.5                   # 부동소수 여유
EDGE_PICK_PX = 8           # 모서리 클릭 허용 픽셀


# ── 부재 → XY 사각형 투영 ────────────────────────────────────
# [2026-05-18] xy_bbox 는 ui 비의존 순수 좌표 유틸이라 _utils/geometry 로 이동.
# render ↔ ui 양방향 import 차단용. 호환을 위해 본 모듈에서도 re-export.
from modular_3d._utils.geometry import xy_bbox  # noqa: F401


def slab_xy(slab):
    """SlabData → XY 사각형."""
    c = slab.corners
    return (float(c[:, 0].min()), float(c[:, 1].min()),
            float(c[:, 0].max()), float(c[:, 1].max()))


def column_xy(col):
    """ColumnData → 200×200 XY 사각형 (기둥 중심 기준)."""
    cx = float((col.base[0] + col.top[0]) / 2.0)
    cy = float((col.base[1] + col.top[1]) / 2.0)
    half = col.section_w / 2.0
    return (cx - half, cy - half, cx + half, cy + half)


def beam_xy(beam):
    """BeamData → 축 정렬 XY 사각형 (단면 폭만큼 수직 확장)."""
    sx, sy = float(beam.start[0]), float(beam.start[1])
    ex, ey = float(beam.end[0]), float(beam.end[1])
    half = beam.section_w / 2.0
    dx = abs(ex - sx)
    dy = abs(ey - sy)
    if dx >= dy:
        # X 방향 보 → Y 로 두께 확장
        yc = (sy + ey) / 2.0
        return (min(sx, ex), yc - half, max(sx, ex), yc + half)
    else:
        # Y 방향 보 → X 로 두께 확장
        xc = (sx + ex) / 2.0
        return (xc - half, min(sy, ey), xc + half, max(sy, ey))


# ── 종속 부재 부모 / 모서리 ─────────────────────────────────

def allowed_parent_types(dep_type):
    """종속 부재 타입에 따라 부모로 가능한 ComponentType 집합."""
    if dep_type == ComponentType.CANTILEVER_SLAB:
        return {ComponentType.MODULE, ComponentType.FLOOR_PANEL}
    if dep_type in (ComponentType.CANTILEVER_BEAM,
                    ComponentType.MID_BEAM,
                    ComponentType.MID_COLUMN):
        return {ComponentType.MODULE}
    # 내벽: 모듈·바닥패널·캔틸레버슬래브 위에 배치 가능.
    if dep_type == ComponentType.INTERIOR_WALL:
        return {ComponentType.MODULE, ComponentType.FLOOR_PANEL,
                ComponentType.CANTILEVER_SLAB}
    # 구조벽은 진정한 종속이 아니라 '바닥패널과 합체' 대상 선택용 — DEPENDENCY_PICK
    # 상태를 재사용해 사용자가 합체할 FP 를 명시적으로 클릭하게 한다.
    if dep_type == ComponentType.STRUCT_WALL:
        return {ComponentType.FLOOR_PANEL}
    return set()


def nearest_edge_id(parent_comp, wx, wy):
    """클릭한 월드 좌표에서 가장 가까운 부모 모서리 ID(0~3) 반환.

    모서리 정의: 0=하단(y_min), 1=우측(x_max), 2=상단(y_max), 3=좌측(x_min).
    """
    x0, y0, x1, y1 = xy_bbox(parent_comp)
    dist = [
        abs(wy - y0),  # 0: 하단
        abs(wx - x1),  # 1: 우측
        abs(wy - y1),  # 2: 상단
        abs(wx - x0),  # 3: 좌측
    ]
    return int(min(range(4), key=lambda i: dist[i]))


def detect_mid_beam_level(parent_comp, wy):
    """모듈 내 클릭 y 위치에 따라 'bottom' / 'top' 결정 (DD1)."""
    x0, y0, x1, y1 = xy_bbox(parent_comp)
    yc = (y0 + y1) / 2.0
    return 'top' if wy > yc else 'bottom'


# ── 부재 내부 구성요소 → 사각형 + 역할 (레이어 필터링 포함) ───

def iter_component_rects(comp, layer):
    """부재의 내부 구성 요소를 (rect, role) 튜플로 순회.

    role: 'slab' | 'wall' | 'beam' | 'column'
    layer 필터: 보/런너/슬래브는 해당 레이어에서만, 기둥/벽은 양쪽 모두.
    """
    if isinstance(comp, Module):
        if layer == LAYER_BOTTOM and comp.slab is not None:
            yield slab_xy(comp.slab), 'slab'
        for col in comp.columns:
            yield column_xy(col), 'column'
        beams = comp.bottom_beams if layer == LAYER_BOTTOM else comp.top_beams
        for beam in beams:
            yield beam_xy(beam), 'beam'

    elif isinstance(comp, FloorPanel):
        if layer == LAYER_BOTTOM:
            if comp.slab is not None:
                yield slab_xy(comp.slab), 'slab'
            for beam in comp.edge_beams:
                yield beam_xy(beam), 'beam'

    elif isinstance(comp, StructWall):
        yield xy_bbox(comp), 'wall'
        for col in comp.columns:
            yield column_xy(col), 'column'
        if layer == LAYER_BOTTOM and comp.bottom_runner is not None:
            yield beam_xy(comp.bottom_runner), 'beam'
        if layer == LAYER_TOP and comp.top_runner is not None:
            yield beam_xy(comp.top_runner), 'beam'

    elif isinstance(comp, InteriorWall):
        # 내벽 — 평면에서 두께×길이 직사각형 (기둥·보 없음). 양 레이어.
        yield xy_bbox(comp), 'wall'

    elif isinstance(comp, CantileverBeam):
        if layer == LAYER_BOTTOM and comp.beam is not None:
            yield beam_xy(comp.beam), 'beam'

    elif isinstance(comp, CantileverSlab):
        if layer == LAYER_BOTTOM:
            if comp.slab is not None:
                yield slab_xy(comp.slab), 'slab'
            for beam in comp.beams:
                yield beam_xy(beam), 'beam'

    elif isinstance(comp, MidBeam):
        if comp.beam is not None:
            yield beam_xy(comp.beam), 'beam'

    elif isinstance(comp, MidColumn):
        if comp.column is not None:
            yield column_xy(comp.column), 'column'

    elif isinstance(comp, Core):
        # RC 코어벽 — 평면에서는 두께×길이 직사각형 (구조벽과 동일한 'wall' 역할).
        # 양 레이어(상/하)에 그림.
        yield xy_bbox(comp), 'wall'

    elif isinstance(comp, CoreSlab):
        # RC 코어 슬래브 — 하부 레이어에만 그림 (다른 슬래브들과 동일 패턴).
        if layer == LAYER_BOTTOM and comp.slab is not None:
            yield slab_xy(comp.slab), 'slab'


def component_layers(comp, scene, child_parent):
    """부재가 속한 레이어 집합."""
    if isinstance(comp, Module):
        return {LAYER_BOTTOM, LAYER_TOP}
    if isinstance(comp, FloorPanel):
        return {LAYER_BOTTOM}
    if isinstance(comp, StructWall):
        return {LAYER_BOTTOM, LAYER_TOP}
    if isinstance(comp, InteriorWall):
        return {LAYER_BOTTOM, LAYER_TOP}
    if isinstance(comp, CantileverBeam):
        return {LAYER_BOTTOM}
    if isinstance(comp, CantileverSlab):
        return {LAYER_BOTTOM}
    if isinstance(comp, MidColumn):
        return {LAYER_BOTTOM, LAYER_TOP}
    if isinstance(comp, MidBeam):
        parent_id = child_parent.get(comp.id)
        parent = scene.get(parent_id) if parent_id is not None else None
        if not isinstance(parent, Module):
            return {LAYER_BOTTOM}
        h = parent.dimensions.get('height', 3400.0)
        bot_z = float(parent.position[2])
        top_z = float(parent.position[2] + h - 200.0)
        beam_z = float(comp.position[2])
        if abs(beam_z - bot_z) < 1.0:
            return {LAYER_BOTTOM}
        if abs(beam_z - top_z) < 1.0:
            return {LAYER_TOP}
        return {LAYER_BOTTOM}
    if isinstance(comp, Core):
        # 코어벽은 구조벽처럼 양 레이어 모두 보임 (단면이 천장·바닥 둘 다 횡단).
        return {LAYER_BOTTOM, LAYER_TOP}
    if isinstance(comp, CoreSlab):
        # 코어 슬래브는 천장 슬래브 — 하부 레이어에 그림 (다른 슬래브들과 동일).
        return {LAYER_BOTTOM}
    return {LAYER_BOTTOM}


def visible_ids(scene, module_pairs, child_pairs, layer, child_parent):
    """현재 레이어에 표시되는 부재 ID 리스트 (페어/sibling 중복 제외)."""
    skip = set()
    for a, b in module_pairs.items():
        if a in scene and b in scene:
            skip.add(max(a, b))
    for a, b in child_pairs.items():
        if a in scene and b in scene:
            skip.add(max(a, b))
    out = []
    for cid, comp in scene.items():
        if cid in skip:
            continue
        if layer in component_layers(comp, scene, child_parent):
            out.append(cid)
    return out
