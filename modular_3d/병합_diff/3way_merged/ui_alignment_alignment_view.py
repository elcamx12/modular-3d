"""
정렬 검증 뷰 (F5) — 2D 탑뷰 평면도 기반.

흐름:
  IDLE → [부재 클릭] → SELECTED
    · 선택 부재의 4 모서리 파랑 하이라이트
    · 정렬 오차(≤100mm, 정확히 20은 제외)가 있는 다른 부재 빨강 + 거리 표시

  SELECTED → [X / Y 키] → DIRECTION
    · 해당 방향의 선택 부재 모서리 2개가 노란색으로 표시

  DIRECTION → [노란 모서리 클릭] → REFERENCE
    · 기준 모서리 확정 (녹색), 대상 부재를 마우스로 호버

  REFERENCE + hover → [1 / 2 / 3 키]
    · 1: -220mm  /  2: 정확 붙임  /  3: +220mm 스냅 → 즉시 이동 → 자동 IDLE 복귀

  1 / 2 키 (IDLE 상태에서만): 하부/상부 레이어 전환
  Esc: 현재 단계 한 칸 뒤로
"""
import numpy as np
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
    QWidget, QSpinBox, QPushButton,
)
from PyQt5.QtCore import Qt, pyqtSignal, QPointF
from PyQt5.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QWheelEvent, QPolygonF,
)

from modular_3d.model import (
    Action, Module, FloorPanel, StructWall,
    CantileverBeam, CantileverSlab, MidBeam, MidColumn, ComponentType,
)

# 상수·헬퍼는 alignment_helpers 단일 진실 원천 (단계 2 통합).
# TYPE_NAMES 는 model.core 에서.
from modular_3d.model import TYPE_NAMES  # noqa: E402
from modular_3d.ui.alignment.alignment_helpers import (
    LAYER_BOTTOM, LAYER_TOP,
    TYPE_COLORS,
    STATE_IDLE, STATE_SELECTED, STATE_DIRECTION, STATE_REFERENCE,
    STATE_DEPENDENCY_PICK,
    ALIGN_TOL, GAP_20, SNAP_20, SNAP_220, EPS, EDGE_PICK_PX,
    xy_bbox as _xy_bbox,
    slab_xy as _slab_xy,
    column_xy as _column_xy,
    beam_xy as _beam_xy,
    allowed_parent_types as _allowed_parent_types,
    nearest_edge_id as _nearest_edge_id,
    detect_mid_beam_level as _detect_mid_beam_level,
    iter_component_rects as _iter_component_rects,
    component_layers as _component_layers,
    visible_ids as _visible_ids,
)
from modular_3d.ui.alignment.alignment_paint import AlignmentCanvasPaintMixin
from modular_3d.ui.alignment.alignment_pick import AlignmentCanvasPickMixin


# ── UI 마이그레이션 M5 — JS KeyboardEvent.key → Qt.Key_* 매핑 ──
# three.js 2D 가 보낸 키 문자열(소문자)을 vispy 키 처리에 쓰는 Qt enum 으로.
_JS_KEY_TO_QT = {
    '1': Qt.Key_1, '2': Qt.Key_2, '3': Qt.Key_3, '4': Qt.Key_4,
    '5': Qt.Key_5, '6': Qt.Key_6, '7': Qt.Key_7, '8': Qt.Key_8,
    '9': Qt.Key_9, '0': Qt.Key_0,
    'x': Qt.Key_X, 'y': Qt.Key_Y, 'r': Qt.Key_R, 'v': Qt.Key_V,
    'm': Qt.Key_M, 'c': Qt.Key_C, 'z': Qt.Key_Z,
    'escape': Qt.Key_Escape, 'enter': Qt.Key_Return,
    'delete': Qt.Key_Delete, 'backspace': Qt.Key_Backspace,
    'f5': Qt.Key_F5, 'f6': Qt.Key_F6,
}


# ── 실 폴리곤 점 포함 판정 (ray casting) ─────────────────────
def _point_in_polygon(px, py, poly):
    """점(px,py)이 단순 다각형 poly 내부인지 — ray casting."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and \
                (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ── 캔버스 ─────────────────────────────────────────────────
class AlignmentCanvas(QLabel, AlignmentCanvasPaintMixin, AlignmentCanvasPickMixin):
    """2D 평면뷰 캔버스."""

    move_requested = pyqtSignal(int, int, float)  # (comp_id, axis, delta)
    # [2026-05-11] 선택 부재 변경 통지 — 우측 디자인 속성 패널 갱신용
    selection_changed = pyqtSignal(int)            # comp_id (선택 해제 = -1)
    # [2026-05-24] 선택 실 변경 통지 — 우측 실 속성 패널 갱신용
    room_selection_changed = pyqtSignal(int)       # room_id (선택 해제 = -1)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        # [2026-05-11] 메인 윈도우 가로폭이 작아도 3D 가 압축되지 않도록
        # 2D 캔버스 최소 크기 완화 (400×300 → 80×60). 사용자는 QSplitter 핸들로
        # 비율 자유 조정.
        self.setMinimumSize(80, 60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._zoom = 0.08
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._auto_fit_done = False

        self._layer = LAYER_BOTTOM
        self._state = STATE_IDLE
        self._selected_id = -1
        self._direction = None          # 'X' or 'Y'
        self._reference_edge = None     # (axis, coord) axis: 0=V(x)선, 1=H(y)선
        self._hover_id = -1
        self._hover_world = (0.0, 0.0)  # 현재 마우스 월드 좌표 (대상 모서리 선택용)

        self._panning = False
        self._pan_start = None
        self._pan_start_cx = 0.0
        self._pan_start_cy = 0.0

        # ── F5 배치 모드 (단계 3b) ───────────────────────
        # _f5_in_preview: True면 마우스 클릭 시 부재 생성. Controller 가 토글.
        self._f5_in_preview = False
        # 클릭 시 마우스 월드 좌표
        self._f5_mouse_world = (0.0, 0.0)
        # Controller 콜백 (set_placement_callback 으로 주입)
        self._f5_on_type_key = None
        self._f5_on_canvas_click = None
        self._f5_on_escape = None
        self._f5_on_rotate = None
        self._f5_on_anchor = None
        self._f5_on_canvas_move = None
        self._f5_on_delete = None
        self._f5_on_select = None
        self._f5_on_dependency_pick = None  # 단계 4
        self._f5_on_move_key = None         # 자유 이동(M)
        self._f5_on_undo = None             # Z (직전 사용자 액션 되돌리기)
        self._f5_on_copy = None             # C (선택 그룹 복사 — M 과 같은 흐름)

        # ── 단계 4: 종속 부재 부모 선택 ─────────────────
        # _f5_pending_dep_type: 4/5/6/7 키 입력 → 종속 부재 타입 저장
        # DEPENDENCY_PICK 상태에서 클릭 → 콜백으로 부모/모서리/level 전달
        self._f5_pending_dep_type = None
        # 추가 단계: 2D 스냅 마커 위치 (paintEvent에서 그림)
        self._f5_snap_point = None

        # ── 2단계: 실(Room) 그리기/편집 모드 ────────────
        # _edit_mode: 'component'(부재 배치/선택) | 'room'(실 그리기/선택).
        #   명시적 토글 버튼으로만 전환. room 모드에선 부재 선택·배치 비활성.
        self._edit_mode = 'component'
        # _room_draw_active: True 면 좌클릭=점 추가, Enter=완료, Esc=취소,
        #   우클릭/Backspace=마지막 점 취소. 폴리곤 점은 월드 xy(mm).
        self._room_draw_active = False
        self._room_points = []              # [(wx, wy), ...]
        self._room_cursor_world = (0.0, 0.0)  # 현재 마우스(스냅 후) — 고무줄선
        self._room_snap_point = None        # 꼭짓점 스냅 마커 표시용
        self._selected_room_id = -1         # 선택된 실 id (-1 = 없음)
        self._f5_on_room_complete = None    # 콜백(points) — 완료 시 호출
        self._f5_on_room_delete = None      # 콜백(room_id) — Del 삭제 시 호출
        # 실 이동/복사 미리보기 (부재식 조작) — 개구부 _op_preview 와 같은 패턴.
        #   {mode('move'|'copy'), src(room_id), base(원본 점들), room_type,
        #    anchor(꼭짓점 idx), rot(0/90/180/270), _wx,_wy}
        self._room_preview = None
        self._f5_on_room_commit = None      # 콜백(src_id, polygon, mode)
        self._f5_on_room_ghost = None       # 콜백(room_type, polygon) — 3D 고스트
        self._f5_on_room_ghost_clear = None  # 콜백()

        # ── 3단계: 개구부 모드 (고스트 미리보기 배치) ──────
        # _op_preview: 개구부 고스트 미리보기 상태(dict) 또는 None.
        #   {w,h,sill,rot(0|90),anchor(0..3),mode('add'|'move'|'copy'),
        #    src(comp_id,index)|None, target(comp_id|-1), u,v, kind}
        # 부재 배치처럼 고스트가 마우스를 따라다니고 R/V/클릭으로 확정한다.
        self._op_preview = None
        self._selected_opening = None       # (comp_id, index) 또는 None
        self._f5_on_opening_add_start = None  # 콜백() — 크기 입력+미리보기 시작
        self._f5_on_opening_commit = None     # 콜백(target_id, op, mode, src)
        self._f5_on_opening_ghost = None      # 콜백(comp_id, op) — 3D 고스트 갱신
        self._f5_on_opening_ghost_clear = None  # 콜백()
        self._f5_on_opening_delete = None     # 콜백(comp_id, index)

        # ── 4단계: 정의 가져오기 그룹 미리보기 ───────────
        # _import_preview: {pivots(list xy), footprints(list of poly), room_polys,
        #   rot(0/90/180/270), anchor(pivot idx), _wx,_wy}
        self._import_preview = None
        self._f5_on_import_ghost = None       # 콜백(target, rot_deg, pivot)
        self._f5_on_import_commit = None      # 콜백(target, rot_deg, pivot)
        self._f5_on_import_cancel = None      # 콜백()

        # UI 마이그레이션 M3-b — three.js 동기화 후크. 외부에서
        # AlignmentDockPanelThree.attach_to(self) 가 sync 메서드를 등록.
        # None 이면 paintEvent 끝에서 호출 안 함 (회귀 0).
        self._three_sync_callback = None

    # ── UI 마이그레이션 M8-a — update() 시 three.js 동기화 ─────
    def update(self, *args, **kwargs):
        """super().update() (vispy QPainter 재그리기 예약) 후 three.js sync.

        sync 트리거를 paintEvent 가 아닌 update() 로 둔 이유: vispy 2D 표시를
        제거(M8-b)해도 update() 는 호출되므로 three.js 갱신이 끊기지 않는다.
        후크 미설정이면 no-op. AlignmentDockPanelThree.attach_to 가 등록.
        """
        super().update(*args, **kwargs)
        sync_cb = getattr(self, '_three_sync_callback', None)
        if sync_cb is not None:
            try:
                sync_cb(self._collect_paint_state())
            except Exception as e:
                from modular_3d._utils.debug import log_error
                log_error(f'AlignmentCanvas.update three.js sync 실패: {e}', cat='alignment', exc=True)

    # ── UI 마이그레이션 M3-b — three.js 측 paint 상태 dump ─────
    def _collect_paint_state(self) -> dict:
        """paintEvent 가 그리는 데이터 전체를 JSON 직렬화 가능 dict 로 반환.

        three.js 측 AlignmentCanvasThree 가 그대로 받아 *전체 redraw* 한다.
        M3-b 에서는 viewport + 부재 사각형(역할별) + 실 폴리곤만 채움.
        나머지 (선택 하이라이트·정렬 오차·F5 PREVIEW 고스트 등) 는 M3-c.
        """
        ctrl = self._controller
        scene = ctrl._scene.components

        # viewport — three.js OrthographicCamera 동기화용
        viewport = {
            'zoom': float(self._zoom),
            'pan_x': float(self._pan_x),
            'pan_y': float(self._pan_y),
            'width': int(self.width()),
            'height': int(self.height()),
        }

        # ── M3-c-1: 하이라이트 집합 미리 계산 ──
        selected_id = int(self._selected_id)
        # 정렬 오차 (선택 부재 기준)
        mis_dict = (self._misaligned_set(selected_id)
                    if selected_id > 0 else {})

        # 같은 그룹 + 합체 파트너
        same_group_ids = set()
        merged_partner_ids = set()
        if selected_id > 0:
            sel = scene.get(selected_id)
            sel_gid = int(getattr(sel, 'group_id', 0) or 0) if sel else 0
            if sel and sel_gid > 0:
                for cid_o, c in scene.items():
                    if (cid_o != selected_id
                            and int(getattr(c, 'group_id', 0) or 0) == sel_gid):
                        same_group_ids.add(int(cid_o))
            if sel is not None:
                # 합체 파트너 — StructWall ↔ FloorPanel
                if isinstance(sel, StructWall) and sel.merged_fp_id is not None:
                    merged_partner_ids.add(int(sel.merged_fp_id))
                elif isinstance(sel, FloorPanel) and sel.merged_wall_ids:
                    for fp_w in sel.merged_wall_ids:
                        merged_partner_ids.add(int(fp_w))

        # DEPENDENCY_PICK 후보 부재 (종속 부재 부모 클릭 단계)
        dep_candidate_ids = set()
        if (self._state == STATE_DEPENDENCY_PICK
                and self._f5_pending_dep_type is not None):
            allowed = _allowed_parent_types(self._f5_pending_dep_type)
            for cid_o in self._current_visible_ids():
                c = scene.get(cid_o)
                if c is None:
                    continue
                if (c.comp_type in allowed
                        and int(getattr(c, 'floor_index', 0) or 0) == 0
                        and int(getattr(c, 'sub_index', 0) or 0) == 0):
                    dep_candidate_ids.add(int(cid_o))

        # 부재 사각형 (역할별 4 layer — slab/wall/beam/column)
        # [F1] 독립 부재 '모듈A-1' 라벨 — 1 회 계산해 라벨에 사용.
        from modular_3d.model.type_naming import classify_component_types
        # 주의: 위 'scene' 은 components dict 다 — 분류 함수에는 Scene 객체를 넘긴다.
        try:
            type_labels = classify_component_types(ctrl._scene)
        except Exception as _e:
            from modular_3d._utils.debug import log_error
            log_error(f'F1 분류 실패: {_e}', cat='alignment', exc=True)
            type_labels = {}
        vis_ids = self._current_visible_ids()
        components = []
        for cid in vis_ids:
            comp = scene.get(cid)
            if comp is None:
                continue
            rects = []
            for rect, role in _iter_component_rects(comp, self._layer):
                x0, y0, x1, y1 = rect
                rects.append({
                    'role': role,
                    'x0': float(x0), 'y0': float(y0),
                    'x1': float(x1), 'y1': float(y1),
                })
            # 본체 bbox + 라벨 — VERTICAL_MODULE 처럼 _iter_component_rects 가
            # 아무것도 yield 안 하는 부재도 *외곽 + 라벨* 만은 그려야 가시.
            bx0, by0, bx1, by1 = _xy_bbox(comp)
            ct = getattr(comp, 'comp_type', None)
            tname = TYPE_NAMES.get(ct, '부재') if ct is not None else '부재'

            # M3-c-1: 하이라이트 카테고리 — vispy 의 _pick_base_color 와 동일 우선순위
            if cid == selected_id or int(cid) in merged_partner_ids:
                hl = 'selected'
            elif int(cid) in mis_dict:
                hl = 'misaligned'
            elif int(cid) in dep_candidate_ids:
                hl = 'dep_candidate'
            elif int(cid) in same_group_ids:
                hl = 'same_group'
            else:
                hl = None
            # 정렬 오차 거리 (선택 부재 기준)
            mis_label = None
            if int(cid) in mis_dict:
                dx, dy = mis_dict[int(cid)]
                parts = []
                if dx is not None:
                    parts.append(f'Δx={dx:.0f}')
                if dy is not None:
                    parts.append(f'Δy={dy:.0f}')
                mis_label = ' / '.join(parts) if parts else None

            components.append({
                'id': int(cid),
                'comp_type': ct.value if ct is not None else '',
                'group_id': int(getattr(comp, 'group_id', 0) or 0),
                'floor_index': int(getattr(comp, 'floor_index', 0) or 0),
                'sub_index': int(getattr(comp, 'sub_index', 0) or 0),
                'rects': rects,
                'bbox': [float(bx0), float(by0), float(bx1), float(by1)],
                # [C4·F1] 라벨은 1개 층만(floor_index==0). 독립 부재는 '모듈A-1',
                # 종속(캔틸 등)은 '모듈A-2 캔틸레버보1', 코어벽 'RC코어벽1',
                # 코어 슬래브 'RC코어 슬래브'(같은 텍스트라 겹쳐도 1개로 보임).
                'label': (type_labels.get(int(cid), f'{tname}#{int(cid)}')
                          if int(getattr(comp, 'floor_index', 0) or 0) == 0
                          else ''),
                'highlight': hl,
                'mis_label': mis_label,
            })

        # 실 폴리곤 — ROOM_TYPE_BY_KEY 색·용도명 포함 (vispy _draw_rooms 와 일치)
        from modular_3d.카탈로그.room_types import ROOM_TYPE_BY_KEY
        rooms = []
        for rid, room in getattr(ctrl._scene, 'rooms', {}).items():
            # [2026-05-28 다층] 2D 평면뷰는 1층(floor_index=0)만 그린다 — 다층 실은
            # 같은 xy 라 겹쳐 그려지면 클릭·표시가 모호. 1층만 편집 대표로 노출.
            if int(getattr(room, 'floor_index', 0) or 0) != 0:
                continue
            poly = getattr(room, 'polygon', None)
            if not poly or len(poly) < 3:
                continue
            rt = ROOM_TYPE_BY_KEY.get(getattr(room, 'room_type', ''))
            col = rt.color if rt is not None else (160, 160, 160)
            name = rt.name if rt is not None else '실'
            try:
                cx_r, cy_r = room.centroid()
            except Exception:
                cx_r = sum(p[0] for p in poly) / len(poly)
                cy_r = sum(p[1] for p in poly) / len(poly)
            rooms.append({
                'id': int(rid),
                'room_type': str(getattr(room, 'room_type', '')),
                'polygon': [[float(p[0]), float(p[1])] for p in poly],
                'color': [int(col[0]), int(col[1]), int(col[2])],
                'name': str(name),
                'centroid': [float(cx_r), float(cy_r)],
                'selected': bool(rid == self._selected_room_id),
            })

        # ── M3-c-2: 미리보기·고스트·스냅 (Python 에서 폴리곤 계산) ──
        # F5 PREVIEW 고스트 — 부재 4 꼭짓점 + 마우스 앵커
        f5_ghost = None
        if self._f5_in_preview:
            try:
                corners = self._f5_compute_ghost_corners()
            except Exception:
                corners = None
            if corners:
                f5_ghost = {
                    'corners': [[float(c[0]), float(c[1])] for c in corners],
                    'mouse': [float(self._f5_mouse_world[0]),
                              float(self._f5_mouse_world[1])],
                }

        # 실 이동/복사 미리보기 폴리곤 (계산된 결과)
        room_preview_poly = None
        if self._room_preview is not None:
            try:
                poly = self._room_preview_polygon()
            except Exception:
                poly = None
            if poly and len(poly) >= 2:
                ai = self._room_preview['anchor'] % len(poly)
                room_preview_poly = {
                    'polygon': [[float(x), float(y)] for (x, y) in poly],
                    'anchor': [float(poly[ai][0]), float(poly[ai][1])],
                }

        # 개구부 미리보기 폴리곤
        op_preview_poly = None
        if (self._op_preview is not None
                and self._op_preview.get('target', -1) > 0):
            try:
                from modular_3d.render.opening_mesh import opening_xy_poly
                pv = self._op_preview
                comp_t = ctrl._scene.components.get(pv['target'])
                if comp_t is not None:
                    ew = pv['h'] if pv['rot'] == 90 else pv['w']
                    eh = pv['w'] if pv['rot'] == 90 else pv['h']
                    op = {'face': pv.get('face', 'slab'),
                          'u': pv['u'], 'v': pv['v'], 'w': ew, 'h': eh}
                    pts = opening_xy_poly(comp_t, op)
                    if pts:
                        op_preview_poly = [[float(x), float(y)]
                                           for (x, y) in pts]
            except Exception:
                pass

        # 정의 가져오기 그룹 고스트 (변환된 footprints + room_polys)
        import_preview_polys = None
        if (self._import_preview is not None
                and self._import_preview.get('_wx') is not None):
            try:
                from modular_3d.model.definition_place import transform_point
                pv = self._import_preview
                pivot = self._import_pivot()
                rot = pv['rot']
                target = (pv['_wx'], pv['_wy'])

                def _tf(poly):
                    return [[float(a), float(b)] for (a, b) in (
                        transform_point(x, y, pivot, rot, target)
                        for (x, y) in poly)]

                import_preview_polys = {
                    'footprints': [_tf(fp) for fp in pv['footprints']
                                   if len(fp) >= 3],
                    'room_polys': [_tf(rp) for rp in pv['room_polys']
                                   if len(rp) >= 3],
                    'mouse': [float(pv['_wx']), float(pv['_wy'])],
                }
            except Exception:
                pass

        # 기존 개구부 (모든 1층 부재의 개구부 폴리곤)
        openings = []
        try:
            from modular_3d.render.opening_mesh import opening_xy_polygons
            sel_op = self._selected_opening
            for cid_o, comp_o in scene.items():
                if int(getattr(comp_o, 'floor_index', 0) or 0) != 0:
                    continue
                for (idx, pts, kind) in opening_xy_polygons(comp_o):
                    is_sel = (sel_op is not None
                              and sel_op[0] == cid_o and sel_op[1] == idx)
                    openings.append({
                        'pts': [[float(x), float(y)] for (x, y) in pts],
                        'selected': bool(is_sel),
                    })
        except Exception:
            pass

        # ── M3-c-1: 선택 부재 bbox · 호버 대상 모서리 · 도움말 텍스트 ──
        selected_bbox = None
        if selected_id > 0:
            sel = scene.get(selected_id)
            if sel is not None:
                sbx0, sby0, sbx1, sby1 = _xy_bbox(sel)
                selected_bbox = [float(sbx0), float(sby0),
                                 float(sbx1), float(sby1)]
        # 호버 + REFERENCE 시: 대상 부재 bbox + 같은 방향 모서리 좌표
        target_edge_info = None
        hover_bbox = None
        if (self._state == STATE_REFERENCE and self._hover_id > 0
                and self._hover_id != selected_id
                and self._reference_edge is not None):
            target_comp = scene.get(self._hover_id)
            if target_comp is not None:
                hbx0, hby0, hbx1, hby1 = _xy_bbox(target_comp)
                hover_bbox = [float(hbx0), float(hby0),
                              float(hbx1), float(hby1)]
                axis, ref_coord = self._reference_edge
                try:
                    tgt = self._pick_target_edge(target_comp, axis)
                    target_edge_info = {
                        'axis': int(axis),
                        'coord': float(tgt),
                        'ref_coord': float(ref_coord),
                    }
                except Exception:
                    pass

        # 도움말 텍스트 — vispy paintEvent line 263-292 와 동일 로직
        layer_txt = '하부 레벨' if self._layer == LAYER_BOTTOM else '상부 레벨'
        state_help_map = {
            STATE_IDLE: '부재 클릭 / 1~7 배치',
            STATE_SELECTED: 'X·Y 키로 이동 방향 선택 / Del 삭제',
            STATE_DIRECTION: '노란 모서리 클릭 → 기준 확정',
            STATE_REFERENCE: '대상 부재에 호버 후 1·2·3·4·5 스냅 이동',
            STATE_DEPENDENCY_PICK: '노란 강조 부재 클릭 → 부모 확정',
        }
        state_txt = state_help_map.get(self._state, '')
        if self._f5_in_preview:
            state_txt = '캔버스 클릭으로 위치 확정 (R: 회전, V: 앵커)'
        if self._edit_mode == 'room':
            if self._room_draw_active:
                state_txt = ('실 그리기: 좌클릭=점, Enter=완료, '
                             '우클릭/Back=취소점, Esc=취소')
            elif self._room_preview is not None:
                state_txt = ('실 배치: 클릭=확정 · V=기준꼭짓점 · R=90°회전 · Esc=취소')
            else:
                state_txt = ('실: 클릭=선택 · M=이동 · C=복사 · R=회전 · Del=삭제 · '
                             'Z=되돌리기 / "실 그리기"로 새로')
        if self._edit_mode == 'opening':
            if self._op_preview is not None:
                state_txt = ('개구부 배치: 대상 면 위 클릭 확정 · '
                             'R=가로세로 · V=기준점 · Esc=취소')
            else:
                state_txt = ('개구부: 클릭=선택 · Del=삭제 · M=이동 · C=복사 / '
                             '"개구부 추가" 버튼으로 새로')
        if self._import_preview is not None:
            state_txt = '정의 가져오기: 클릭=배치 · R=회전 · V=기준점 · Esc=취소'
        help_text = f'[{layer_txt}]  {state_txt}'

        # 스냅 안내 (REFERENCE + hover 시 1/2/3/4/5 키 스냅)
        snap_hint = None
        if target_edge_info is not None:
            tgt = target_edge_info['coord']
            ref = target_edge_info['ref_coord']
            delta_exact = tgt - ref
            from modular_3d.ui.alignment.alignment_helpers import (
                SNAP_220 as _S220, SNAP_20 as _S20,
            )
            snap_hint = (
                f'[1: -220({delta_exact - _S220:+.0f})  '
                f'2: 정확({delta_exact:+.0f})  '
                f'3: +220({delta_exact + _S220:+.0f})  '
                f'4: -20({delta_exact - _S20:+.0f})  '
                f'5: +20({delta_exact + _S20:+.0f})]'
            )

        return {
            'viewport': viewport,
            'layer': int(self._layer),
            'state': str(self._state),
            'edit_mode': str(self._edit_mode),
            'selected_id': selected_id,
            'hover_id': int(self._hover_id),
            'selected_room_id': int(self._selected_room_id),
            'selected_bbox': selected_bbox,
            'hover_bbox': hover_bbox,
            'target_edge_info': target_edge_info,
            'components': components,
            'rooms': rooms,
            'direction': self._direction,
            'reference_edge': (list(self._reference_edge)
                               if self._reference_edge is not None else None),
            'f5_in_preview': bool(self._f5_in_preview),
            'f5_snap_point': self._f5_snap_point,
            'room_draw': {
                'active': bool(self._room_draw_active),
                'points': list(self._room_points),
                'cursor': list(self._room_cursor_world),
                'snap_point': self._room_snap_point,
            },
            'room_preview': self._room_preview,
            'op_preview': self._op_preview,
            'import_preview': self._import_preview,
            'help_text': help_text,
            'snap_hint': snap_hint,
            # M3-c-2 — 미리보기·고스트 (Python 에서 계산된 폴리곤)
            'f5_ghost': f5_ghost,
            'room_preview_poly': room_preview_poly,
            'op_preview_poly': op_preview_poly,
            'import_preview_polys': import_preview_polys,
            'openings': openings,
        }

    def set_placement_callback(self, on_type_key=None,
                               on_canvas_click=None, on_escape=None,
                               on_rotate=None, on_anchor=None,
                               on_canvas_move=None, on_delete=None,
                               on_select=None,
                               on_dependency_pick=None,
                               on_move_key=None,
                               on_undo=None,
                               on_copy=None,
                               on_room_complete=None,
                               on_room_delete=None,
                               on_room_commit=None,
                               on_room_ghost=None,
                               on_room_ghost_clear=None,
                               on_opening_add_start=None,
                               on_opening_commit=None,
                               on_opening_ghost=None,
                               on_opening_ghost_clear=None,
                               on_opening_delete=None,
                               on_import_ghost=None,
                               on_import_commit=None,
                               on_import_cancel=None):
        """Controller 가 F5 배치/조작 콜백을 주입.
        - on_type_key(comp_type)         : 1~7 키 입력 시
        - on_canvas_click(world_x, y)    : PREVIEW 상태에서 클릭 시
        - on_escape()                    : Esc
        - on_rotate()                    : R 키
        - on_anchor()                    : V 키
        - on_canvas_move(world_x, y)     : PREVIEW 중 마우스 이동
        - on_delete(group_id)            : SELECTED 상태에서 Delete
        - on_select(component_id)        : F5 IDLE에서 부재 클릭 시
        """
        self._f5_on_type_key = on_type_key
        self._f5_on_canvas_click = on_canvas_click
        self._f5_on_escape = on_escape
        self._f5_on_rotate = on_rotate
        self._f5_on_anchor = on_anchor
        self._f5_on_canvas_move = on_canvas_move
        self._f5_on_delete = on_delete
        self._f5_on_select = on_select
        self._f5_on_dependency_pick = on_dependency_pick
        self._f5_on_move_key = on_move_key
        self._f5_on_undo = on_undo
        self._f5_on_copy = on_copy
        self._f5_on_room_complete = on_room_complete
        self._f5_on_room_delete = on_room_delete
        self._f5_on_room_commit = on_room_commit
        self._f5_on_room_ghost = on_room_ghost
        self._f5_on_room_ghost_clear = on_room_ghost_clear
        self._f5_on_opening_add_start = on_opening_add_start
        self._f5_on_opening_commit = on_opening_commit
        self._f5_on_opening_ghost = on_opening_ghost
        self._f5_on_opening_ghost_clear = on_opening_ghost_clear
        self._f5_on_opening_delete = on_opening_delete
        self._f5_on_import_ghost = on_import_ghost
        self._f5_on_import_commit = on_import_commit
        self._f5_on_import_cancel = on_import_cancel

    # ── DEPENDENCY_PICK 외부 진입 (구조벽 ↔ FP 합체 대상 선택용) ──
    # [2026-05-11] 좌측 팔레트 버튼용 진입점.
    # 키 1~8 입력 시 keyPressEvent 안의 분기와 100% 동일한 동작을 수행.
    # 캔틸레버보(4) · 캔틸레버슬래브(5) · 중간보(6) · 중간기둥(7) 은 DEPENDENCY_PICK
    # 상태로 진입하여 paint 가 후보 부모 부재를 하이라이트하도록 한다.
    def handle_palette_select(self, comp_type: ComponentType):
        if self._f5_in_preview or self._state != STATE_IDLE:
            # 진행 중인 배치/이동 흐름 있음 — 기존 흐름 유지를 위해 무시
            print(f'[PALETTE] 배치/이동 진행 중 — 무시 ({comp_type})')
            return
        if comp_type in (
            ComponentType.CANTILEVER_BEAM,
            ComponentType.CANTILEVER_SLAB,
            ComponentType.MID_BEAM,
            ComponentType.MID_COLUMN,
            ComponentType.INTERIOR_WALL,
        ):
            # 종속 부재 — 부모 클릭 단계 진입 (paint 가 후보 하이라이트)
            self._f5_pending_dep_type = comp_type
            self._state = STATE_DEPENDENCY_PICK
            self.setFocus()
            self.update()
            print(f'[PALETTE → F5] DEPENDENCY_PICK 진입 ({comp_type.value}) — 부모 부재 클릭')
            return
        # 일반 부재 — 컨트롤러의 type_key 콜백 직접 호출 (사이즈 입력)
        if self._f5_on_type_key is not None:
            self._f5_on_type_key(comp_type)

    def enter_dependency_pick(self, dep_type):
        """controls.py 가 dim 확정 후 호출 — DEPENDENCY_PICK 상태로 진입.

        구조벽의 경우 dim 패널의 '바닥패널과 병합' 체크박스가 켜진 상태로
        Enter 가 눌렸을 때 사용자가 합체할 FP 를 명시적으로 클릭하도록 안내.
        """
        self._f5_pending_dep_type = dep_type
        self._state = STATE_DEPENDENCY_PICK
        self.update()

    # ── 2·3단계: 편집 모드 (부재 / 실 / 개구부) ─────────
    def set_edit_mode(self, mode: str):
        """'component' | 'room' | 'opening' 편집 모드 전환(토글 버튼이 호출).

        전환 시 모든 진행 상태(배치 미리보기·실 그리기/선택·개구부 무장/선택)를
        정리한다.
        """
        if mode not in ('component', 'room', 'opening') or mode == self._edit_mode:
            return
        # 진행 중 상태 일괄 정리
        self._cancel_room_draw()
        self._cancel_room_preview()
        if self._selected_room_id != -1:
            self._selected_room_id = -1
            self.room_selection_changed.emit(-1)
        self._cancel_opening_preview()
        self._selected_opening = None
        if self._f5_in_preview and self._f5_on_escape is not None:
            self._f5_on_escape()
        self.reset_state()   # 부재 선택 해제(STATE_IDLE)
        self._edit_mode = mode
        self.setFocus()
        self.update()
        print(f'[MODE] 편집 모드 → {mode}')

    @property
    def edit_mode(self) -> str:
        return self._edit_mode

    # ── 3단계: 개구부 추가/선택 (고스트 미리보기) ───────
    def start_opening_add(self):
        """'개구부 추가' 버튼 → 크기 입력 후 고스트 미리보기 시작(컨트롤러 위임)."""
        if self._edit_mode != 'opening':
            self.set_edit_mode('opening')
        self._selected_opening = None
        if self._f5_on_opening_add_start is not None:
            self._f5_on_opening_add_start()

    def begin_opening_preview(self, ew, eh, sill, mode, src=None, face=None):
        """컨트롤러가 크기 확정 후 호출 — 고스트 미리보기 상태 설정.

        face: move 시 원본 개구부 면 고정. add/copy 는 None(클릭 위치로 면 판정).
        """
        self._op_preview = {
            'w': float(ew), 'h': float(eh), 'sill': float(sill),
            'rot': 0, 'anchor': 0, 'mode': mode, 'src': src,
            'fixed_face': face if mode == 'move' else None,
            'face': face or 'slab',
            'target': -1, 'u': 0.0, 'v': 0.0, 'kind': '',
        }
        self.setFocus()
        self.update()
        print(f'[OPENING] 미리보기 시작 ({mode}) — 마우스 이동/클릭, R/V, Esc')

    def _op_effective_wh(self):
        """rot(0/90) 반영 유효 폭·높이."""
        pv = self._op_preview
        if pv is None:
            return 0.0, 0.0
        if pv['rot'] == 90:
            return pv['h'], pv['w']
        return pv['w'], pv['h']

    def _op_update_at(self, wx, wy):
        """마우스 위치에서 대상 부재 + 면 로컬(u,v) 갱신 + 3D 고스트."""
        from modular_3d.render.opening_mesh import opening_facelocal_from_click
        pv = self._op_preview
        if pv is None:
            return
        pv['_wx'] = float(wx); pv['_wy'] = float(wy)
        ew, eh = self._op_effective_wh()
        # move 는 대상·면 고정(src comp/face), add/copy 는 마우스 아래 부재+면 자동판정
        fixed_face = pv.get('fixed_face')
        if pv['mode'] == 'move' and pv['src'] is not None:
            target = pv['src'][0]
        else:
            mx, my = self._world_to_screen(wx, wy)
            target = self._hit_test_component(int(mx), int(my))
        pv['target'] = target if target and target > 0 else -1
        comp = self._controller._scene.components.get(pv['target'])
        if comp is None:
            self.update()
            return
        res = opening_facelocal_from_click(comp, wx, wy, ew, eh, pv['sill'],
                                           pv['anchor'], face=fixed_face)
        if res is None:
            pv['target'] = -1
            self.update()
            return
        pv['u'], pv['v'], pv['kind'], pv['face'] = res
        # 3D 고스트
        if self._f5_on_opening_ghost is not None:
            self._f5_on_opening_ghost(
                pv['target'], {'face': pv['face'], 'u': pv['u'], 'v': pv['v'],
                               'w': ew, 'h': eh})
        self.update()

    def _op_commit_at(self, wx, wy):
        """클릭 — 현재 미리보기 개구부 확정(추가/이동/복사)."""
        pv = self._op_preview
        if pv is None:
            return
        self._op_update_at(wx, wy)
        if pv['target'] <= 0:
            return  # 대상 부재 위가 아님 — 확정 안 함
        ew, eh = self._op_effective_wh()
        op = {'face': pv.get('face', 'slab'), 'u': pv['u'], 'v': pv['v'],
              'w': ew, 'h': eh}
        if self._f5_on_opening_commit is not None:
            self._f5_on_opening_commit(pv['target'], op, pv['mode'], pv['src'])
        self._cancel_opening_preview()

    def _cancel_opening_preview(self):
        """미리보기 종료 + 3D 고스트 제거."""
        if self._op_preview is not None:
            self._op_preview = None
            if self._f5_on_opening_ghost_clear is not None:
                self._f5_on_opening_ghost_clear()
            self.update()

    # ── 4단계: 정의 가져오기 그룹 미리보기 ──────────────
    def begin_import_preview(self, pivots, footprints, room_polys):
        """컨트롤러가 정의 복원 후 호출 — 그룹 고스트 미리보기 시작."""
        # 진행 중 다른 흐름 정리
        self.set_edit_mode('component')
        self._import_preview = {
            'pivots': list(pivots), 'footprints': list(footprints),
            'room_polys': list(room_polys), 'rot': 0, 'anchor': 0,
            '_wx': None, '_wy': None,
        }
        self.setFocus()
        self.update()
        print('[IMPORT] 그룹 미리보기 — 마우스 이동, R 회전, V 기준점, 클릭 배치, Esc')

    def _import_pivot(self):
        pv = self._import_preview
        pivs = pv['pivots'] if pv else []
        return pivs[pv['anchor'] % len(pivs)] if pivs else (0.0, 0.0)

    def _import_update_at(self, wx, wy):
        pv = self._import_preview
        if pv is None:
            return
        pv['_wx'] = float(wx); pv['_wy'] = float(wy)
        if self._f5_on_import_ghost is not None:
            self._f5_on_import_ghost((float(wx), float(wy)), pv['rot'],
                                     self._import_pivot())
        self.update()

    def _import_commit_at(self, wx, wy):
        pv = self._import_preview
        if pv is None:
            return
        pivot = self._import_pivot(); rot = pv['rot']
        self._import_preview = None
        if self._f5_on_import_commit is not None:
            self._f5_on_import_commit((float(wx), float(wy)), rot, pivot)
        self.update()

    def _cancel_import_preview(self):
        if self._import_preview is not None:
            self._import_preview = None
            if self._f5_on_import_cancel is not None:
                self._f5_on_import_cancel()
            self.update()

    # ── 실(Room) 이동/복사 미리보기 (부재식 조작) ────────
    def begin_room_preview(self, mode):
        """선택된 실에 대해 이동/복사 미리보기 시작(M/C 키)."""
        rid = self._selected_room_id
        if rid is None or rid < 0:
            return
        room = self._controller._scene.rooms.get(rid)
        if room is None or len(getattr(room, 'polygon', [])) < 3:
            return
        self._room_preview = {
            'mode': mode, 'src': rid,
            'base': [(float(x), float(y)) for (x, y) in room.polygon],
            'room_type': room.room_type,
            'anchor': 0, 'rot': 0, '_wx': None, '_wy': None,
        }
        self.setFocus()
        self.update()
        print(f'[ROOM] 미리보기 시작 ({mode}) — V=기준꼭짓점, R=90°회전, 클릭=확정, Esc')

    def _room_preview_polygon(self, wx=None, wy=None):
        """현재 미리보기 상태의 폴리곤(월드) 계산.

        base 를 중심 기준 rot 회전 → anchor 꼭짓점이 마우스(wx,wy)에 오도록 평행이동.
        """
        pv = self._room_preview
        if pv is None:
            return None
        import math
        base = pv['base']
        # 중심
        cx = sum(p[0] for p in base) / len(base)
        cy = sum(p[1] for p in base) / len(base)
        rad = math.radians(pv['rot'])
        cs, sn = math.cos(rad), math.sin(rad)
        rotated = []
        for (x, y) in base:
            dx, dy = x - cx, y - cy
            rotated.append((cx + dx * cs - dy * sn, cy + dx * sn + dy * cs))
        if wx is None:
            wx, wy = pv.get('_wx'), pv.get('_wy')
        if wx is None:
            return rotated
        ai = pv['anchor'] % len(rotated)
        ax, ay = rotated[ai]
        ddx, ddy = wx - ax, wy - ay
        return [(x + ddx, y + ddy) for (x, y) in rotated]

    def _room_update_at(self, wx, wy):
        """마우스 따라 실 미리보기 폴리곤 + 3D 고스트 갱신."""
        pv = self._room_preview
        if pv is None:
            return
        pv['_wx'] = float(wx); pv['_wy'] = float(wy)
        poly = self._room_preview_polygon(wx, wy)
        if poly and self._f5_on_room_ghost is not None:
            self._f5_on_room_ghost(pv['room_type'], poly)
        self.update()

    def _room_commit_at(self, wx, wy):
        """클릭 — 실 이동/복사 확정."""
        pv = self._room_preview
        if pv is None:
            return
        poly = self._room_preview_polygon(wx, wy)
        src = pv['src']; mode = pv['mode']
        self._cancel_room_preview()
        if poly and self._f5_on_room_commit is not None:
            self._f5_on_room_commit(src, poly, mode)

    def _cancel_room_preview(self):
        if self._room_preview is not None:
            self._room_preview = None
            if self._f5_on_room_ghost_clear is not None:
                self._f5_on_room_ghost_clear()
            self.update()

    def _rotate_selected_room(self):
        """선택된 실을 중심 기준 90° 제자리 회전(되돌리기 기록은 commit 측)."""
        import math
        rid = self._selected_room_id
        room = self._controller._scene.rooms.get(rid)
        if room is None or len(getattr(room, 'polygon', [])) < 3:
            return
        base = [(float(x), float(y)) for (x, y) in room.polygon]
        cx = sum(p[0] for p in base) / len(base)
        cy = sum(p[1] for p in base) / len(base)
        # +90°
        rotated = [(cx - (y - cy), cy + (x - cx)) for (x, y) in base]
        if self._f5_on_room_commit is not None:
            self._f5_on_room_commit(rid, rotated, 'move')

    def _hit_test_opening(self, wx, wy):
        """월드 점을 품는 개구부 (comp_id, index) 반환. 없으면 None."""
        from modular_3d.render.opening_mesh import opening_xy_polygons
        scene = self._controller._scene.components
        for cid, comp in scene.items():
            if getattr(comp, 'floor_index', 0) != 0:
                continue
            for (idx, pts, _kind) in opening_xy_polygons(comp):
                if _point_in_polygon(wx, wy, pts):
                    return (cid, idx)
        return None

    # ── 2단계: 실(Room) 그리기 ─────────────────────────
    def start_room_draw(self):
        """좌측 팔레트 '실 그리기' 버튼 → 실 모드로 전환 + 새 폴리곤 시작."""
        if self._edit_mode != 'room':
            self.set_edit_mode('room')
        # 기존 선택 해제
        if self._selected_room_id != -1:
            self._selected_room_id = -1
            self.room_selection_changed.emit(-1)
        self._room_draw_active = True
        self._room_points = []
        self._room_snap_point = None
        self.setFocus()
        self.update()
        print('[ROOM] 실 그리기 — 좌클릭=점, Enter=완료, 우클릭/Back=취소점, Esc=취소')

    def clear_room_selection(self):
        """실 선택 해제(패널 삭제 등 외부 트리거용) — 시그널 발생 없음."""
        if self._selected_room_id != -1:
            self._selected_room_id = -1
            self.update()

    def _hit_test_room(self, wx, wy):
        """월드 점(wx,wy)을 품는 실 id 반환(겹치면 나중 생성 우선). 없으면 -1."""
        scene = self._controller._scene
        rooms = getattr(scene, 'rooms', {})
        best = -1
        for rid, room in rooms.items():
            # [2026-05-28 다층] 1층(floor_index=0)만 선택 대상 — 2D 표시와 일치.
            # 그룹 동기(이동/복사/삭제)는 컨트롤러가 group_id 로 전체 층 확장.
            if int(getattr(room, 'floor_index', 0) or 0) != 0:
                continue
            poly = getattr(room, 'polygon', None)
            if poly and len(poly) >= 3 and _point_in_polygon(wx, wy, poly):
                if rid > best:
                    best = rid
        return best

    def _cancel_room_draw(self):
        """실 그리기 취소 — 점 버리고 모드 종료."""
        self._room_draw_active = False
        self._room_points = []
        self._room_snap_point = None
        self.update()

    def _finish_room_draw(self):
        """실 그리기 완료 — 점 3개 이상이면 콜백으로 폴리곤 전달."""
        pts = list(self._room_points)
        self._room_draw_active = False
        self._room_points = []
        self._room_snap_point = None
        self.update()
        if len(pts) >= 3 and self._f5_on_room_complete is not None:
            self._f5_on_room_complete(pts)
        else:
            print(f'[ROOM] 점 {len(pts)}개 — 3개 미만이라 실 생성 취소')

    def _room_snap(self, wx, wy):
        """실 점 스냅: ① 부재 꼭짓점 → ② 부재 모서리(축별)/직교 → ③ 격자(100mm).

        반환: (sx, sy, vertex_point_or_None)
        vertex_point: 꼭짓점 스냅 시 (x,y), 아니면 None(마커 표시용).
        """
        from modular_3d.카탈로그.tolerances import HOVER_SNAP_RADIUS_MM
        thr = float(HOVER_SNAP_RADIUS_MM)
        scene = self._controller._scene.components
        # 후보: 1층 본체 부재의 bbox 꼭짓점 + 모서리 x/y 좌표
        edge_xs, edge_ys = [], []
        best_v = None
        best_d = float('inf')
        for cid, comp in scene.items():
            if (getattr(comp, 'floor_index', 0) != 0
                    or getattr(comp, 'sub_index', 0) != 0):
                continue
            x0, y0, x1, y1 = _xy_bbox(comp)
            edge_xs.extend([x0, x1])
            edge_ys.extend([y0, y1])
            for cx, cy in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
                d = ((wx - cx) ** 2 + (wy - cy) ** 2) ** 0.5
                if d < best_d and d <= thr:
                    best_d = d
                    best_v = (cx, cy)
        # ① 꼭짓점 스냅 — xy 동시
        if best_v is not None:
            return float(best_v[0]), float(best_v[1]), best_v
        prev = self._room_points[-1] if self._room_points else None

        def _snap_axis(v, edges, prev_v):
            # ② 모서리 좌표 스냅
            be, bd = None, thr
            for e in edges:
                d = abs(v - e)
                if d <= bd:
                    bd, be = d, e
            if be is not None:
                return float(be)
            # ② 직교 스냅(이전 점과 같은 선)
            if prev_v is not None and abs(v - prev_v) <= thr:
                return float(prev_v)
            # ③ 격자 폴백
            return round(v / 100.0) * 100.0

        sx = _snap_axis(wx, edge_xs, prev[0] if prev else None)
        sy = _snap_axis(wy, edge_ys, prev[1] if prev else None)
        return sx, sy, None

    # ── 상태 ───────────────────────────────────────────
    def reset_state(self):
        """선택/방향/기준을 모두 초기화."""
        self._state = STATE_IDLE
        self._selected_id = -1
        self._direction = None
        self._reference_edge = None
        self._hover_id = -1
        self.update()
        # 선택 해제 통지
        self.selection_changed.emit(-1)

    def set_layer(self, layer):
        self._layer = layer
        self.reset_state()

    # ── 좌표 변환 ───────────────────────────────────────
    def _world_to_screen(self, wx, wy):
        w, h = self.width(), self.height()
        sx = w / 2.0 + (wx - self._pan_x) * self._zoom
        sy = h / 2.0 - (wy - self._pan_y) * self._zoom
        return sx, sy

    def _screen_to_world(self, sx, sy):
        w, h = self.width(), self.height()
        wx = self._pan_x + (sx - w / 2.0) / self._zoom
        wy = self._pan_y - (sy - h / 2.0) / self._zoom
        return wx, wy

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._auto_fit_done:
            self._auto_fit()
            self._auto_fit_done = True
            self.update()

    def _auto_fit(self):
        ids = self._current_visible_ids()
        if not ids:
            return
        scene = self._controller._scene.components
        xs, ys = [], []
        for cid in ids:
            x0, y0, x1, y1 = _xy_bbox(scene[cid])
            xs.extend([x0, x1])
            ys.extend([y0, y1])
        margin = 500.0
        x_min, x_max = min(xs) - margin, max(xs) + margin
        y_min, y_max = min(ys) - margin, max(ys) + margin
        w = self.width() or 900
        h = self.height() or 700
        zx = w / (x_max - x_min) if x_max > x_min else 0.1
        zy = h / (y_max - y_min) if y_max > y_min else 0.1
        self._zoom = min(zx, zy) * 0.9
        self._pan_x = (x_min + x_max) / 2.0
        self._pan_y = (y_min + y_max) / 2.0

    def _current_visible_ids(self):
        ctrl = self._controller
        return _visible_ids(
            ctrl._scene.components, ctrl._floor_pairs,
            ctrl._child_pairs, self._layer, ctrl._child_parent)

    # ── 히트 테스트 ───────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self._pan_start_cx = self._pan_x
            self._pan_start_cy = self._pan_y
            return
        # 좌/우클릭 처리는 _process_press_at 로 위임 (three.js 입력과 공유).
        self._process_press_at(event.pos().x(), event.pos().y(),
                               event.button())

    # ── UI 마이그레이션 M4-b — three.js 2D 클릭 진입점 ──
    def handle_world_click(self, wx: float, wy: float, button: int) -> None:
        """three.js 2D 의 클릭 — world 좌표를 받아 기존 선택 분기를 그대로 재사용.

        three.js 는 자체 카메라 world 좌표를 보냄. 이를 vispy `_world_to_screen`
        으로 환산하면 (왕복 항등) 기존 _process_press_at 이 `_screen_to_world`
        로 정확히 복원해 동일 동작. button: 0=left, 2=right (JS 규약).
        """
        qt_btn = Qt.RightButton if button == 2 else Qt.LeftButton
        sx, sy = self._world_to_screen(wx, wy)
        self._process_press_at(int(sx), int(sy), qt_btn)

    def _process_press_at(self, mx, my, button):
        """좌/우클릭 처리 — vispy mousePressEvent 와 three.js handle_world_click
        이 공유. mx, my 는 *vispy screen 좌표*, button 은 Qt.LeftButton 등."""
        # ── 정의 가져오기 미리보기: 좌클릭 확정 ──
        if self._import_preview is not None:
            if button == Qt.LeftButton:
                wx, wy = self._screen_to_world(mx, my)
                self._import_commit_at(wx, wy)
            return

        # ── 실 모드: 미리보기 확정 / 그리기(점 추가/취소) / 실 선택 ──
        if self._edit_mode == 'room':
            if self._room_preview is not None:
                if button == Qt.LeftButton:
                    wx, wy = self._screen_to_world(mx, my)
                    self._room_commit_at(wx, wy)
                return
            if self._room_draw_active:
                if button == Qt.RightButton:
                    if self._room_points:
                        self._room_points.pop()
                        self.update()
                    return
                if button == Qt.LeftButton:
                    raw_wx, raw_wy = self._screen_to_world(mx, my)
                    sx, sy, _ = self._room_snap(raw_wx, raw_wy)
                    self._room_points.append((sx, sy))
                    self.update()
                return
            # 그리기 중 아님 → 좌클릭으로 실 선택/해제
            if button == Qt.LeftButton:
                wx, wy = self._screen_to_world(mx, my)
                rid = self._hit_test_room(wx, wy)
                self._selected_room_id = rid
                self.setFocus()
                self.update()
                self.room_selection_changed.emit(int(rid))
            return

        # ── 개구부 모드: 미리보기 확정(클릭) 또는 기존 개구부 선택 ──
        if self._edit_mode == 'opening':
            if button != Qt.LeftButton:
                return
            wx, wy = self._screen_to_world(mx, my)
            if self._op_preview is not None:
                self._op_commit_at(wx, wy)
                return
            # 기존 개구부 선택/해제
            self._selected_opening = self._hit_test_opening(wx, wy)
            self.setFocus()
            self.update()
            return

        if button != Qt.LeftButton:
            return

        # ── F5 배치 PREVIEW 상태: 클릭으로 위치 확정 (단계 3b) ──
        if self._f5_in_preview and self._f5_on_canvas_click is not None:
            raw_wx, raw_wy = self._screen_to_world(mx, my)
            # 부재 꼭지점 스냅 + 100mm 그리드 폴백 (c3 ㄱ)
            wx, wy, _ = self._f5_world_snap(raw_wx, raw_wy)
            self._f5_on_canvas_click(wx, wy)
            return

        # ── DEPENDENCY_PICK: 부모 부재 클릭 (단계 4) ──
        if (self._state == STATE_DEPENDENCY_PICK
                and self._f5_on_dependency_pick is not None):
            hit = self._hit_test_component(mx, my)
            if hit > 0:
                scene = self._controller._scene.components
                parent = scene.get(hit)
                if parent is None:
                    return
                # 종속 후보 검증
                dep_type = self._f5_pending_dep_type
                allowed = _allowed_parent_types(dep_type)
                if parent.comp_type not in allowed:
                    print(f'[F5 DEP] {parent.comp_type.value}는 '
                          f'{dep_type.value}의 종속 대상이 아님')
                    return
                # anchor_edge_id: 클릭 위치에서 가장 가까운 부모 모서리(0~3)
                wx, wy = self._screen_to_world(mx, my)
                edge_id = _nearest_edge_id(parent, wx, wy)
                # mid_beam: 클릭 위치 상·하반에 따라 'top'/'bottom'
                level = None
                if dep_type == ComponentType.MID_BEAM:
                    level = _detect_mid_beam_level(parent, wy)
                # 콜백 호출
                self._f5_on_dependency_pick(
                    dep_type, hit, edge_id, level)
                # 상태 정리 — DIMENSION_INPUT 단계로 controller 가 진입
                self._state = STATE_IDLE
                self._f5_pending_dep_type = None
                self.update()
            return

        if self._state == STATE_DIRECTION:
            # 방향 모서리 클릭 → 기준 확정
            picked = self._pick_direction_edge(mx, my)
            if picked is not None:
                self._reference_edge = picked
                self._state = STATE_REFERENCE
                self.update()
            return

        # IDLE / SELECTED / REFERENCE 에서 다른 부재 클릭 → 선택 변경
        hit = self._hit_test_component(mx, my)
        if hit > 0:
            self._selected_id = hit
            self._state = STATE_SELECTED
            self._direction = None
            self._reference_edge = None
            self._hover_id = -1
            self.setFocus()
            self.update()
            # 우측 디자인 속성 패널 갱신
            self.selection_changed.emit(int(hit))
        else:
            # 빈 영역 클릭 → 선택 해제
            self.reset_state()

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            dx = event.pos().x() - self._pan_start.x()
            dy = event.pos().y() - self._pan_start.y()
            self._pan_x = self._pan_start_cx - dx / self._zoom
            self._pan_y = self._pan_start_cy + dy / self._zoom
            self.update()
            return
        self._process_move_at(event.pos().x(), event.pos().y())

    # ── UI 마이그레이션 M4-c — three.js 2D 호버 진입점 ──
    def handle_world_move(self, wx: float, wy: float) -> None:
        """three.js 2D 호버 — world 좌표를 vispy screen 으로 환산해 동일 처리."""
        sx, sy = self._world_to_screen(wx, wy)
        self._process_move_at(int(sx), int(sy))

    def _process_move_at(self, mx, my):
        """마우스 이동 처리 — vispy mouseMoveEvent 와 three.js handle_world_move
        공유. mx, my 는 vispy screen 좌표."""
        # 정의 가져오기 미리보기: 마우스 따라 그룹 고스트 갱신
        if self._import_preview is not None:
            wx, wy = self._screen_to_world(mx, my)
            self._import_update_at(wx, wy)
            return

        # 개구부 미리보기: 마우스 따라 고스트 갱신
        if self._edit_mode == 'opening' and self._op_preview is not None:
            wx, wy = self._screen_to_world(mx, my)
            self._op_update_at(wx, wy)
            return

        # 실 이동/복사 미리보기: 마우스 따라 폴리곤 고스트 갱신
        if self._edit_mode == 'room' and self._room_preview is not None:
            wx, wy = self._screen_to_world(mx, my)
            self._room_update_at(wx, wy)
            return

        # 실 그리기 모드: 마우스(스냅 후) 위치 갱신 → 고무줄선/마커
        if self._room_draw_active:
            raw_wx, raw_wy = self._screen_to_world(mx, my)
            sx, sy, snap_v = self._room_snap(raw_wx, raw_wy)
            self._room_cursor_world = (sx, sy)
            self._room_snap_point = snap_v
            self.update()
            return

        # F5 PREVIEW: 마우스 따라 좌측 3D 고스트 + 2D 캔버스 고스트 갱신
        if self._f5_in_preview and self._f5_on_canvas_move is not None:
            raw_wx, raw_wy = self._screen_to_world(mx, my)
            wx, wy, snap_p = self._f5_world_snap(raw_wx, raw_wy)
            self._f5_mouse_world = (wx, wy)
            # 스냅 마커 표시용 저장(paintEvent에서 그림)
            self._f5_snap_point = snap_p
            self._f5_on_canvas_move(wx, wy)
            self.update()
            return

        if self._state == STATE_REFERENCE:
            wx, wy = self._screen_to_world(mx, my)
            self._hover_world = (wx, wy)
            hit = self._hit_test_component(mx, my)
            if hit != self._hover_id:
                self._hover_id = hit
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = False
            self._pan_start = None

    def wheelEvent(self, event: QWheelEvent):
        mx, my = event.pos().x(), event.pos().y()
        wx, wy = self._screen_to_world(mx, my)
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self._zoom *= factor
        w, h = self.width(), self.height()
        self._pan_x = wx - (mx - w / 2.0) / self._zoom
        self._pan_y = wy + (my - h / 2.0) / self._zoom
        self.update()

    # ── 키 이벤트 ─────────────────────────────────────
    # F5 키 매핑 우선순위 (단계 3b):
    #  1) ALIGN_REFERENCE + hover: 1~5 정렬 스냅 (제일 우선)
    #  2) F5 PREVIEW 상태: Esc → 콜백, 그 외 키는 무시(단계 3c에서 R/V 추가)
    #  3) F5 IDLE: 1~7 → 배치 시작 콜백
    #  4) ALIGN_SELECTED: X/Y 방향 선택
    #  5) STATE_DIRECTION: 그대로
    #  6) Esc: 상태 한 칸 뒤로
    _F5_KEY_TO_TYPE_INDEX = {
        Qt.Key_1: 0, Qt.Key_2: 1, Qt.Key_3: 2, Qt.Key_4: 3,
        Qt.Key_5: 4, Qt.Key_6: 5, Qt.Key_7: 6, Qt.Key_8: 7,
        Qt.Key_9: 8,  # 2026-05-12 RC 코어 활성화
    }

    def keyPressEvent(self, event):
        # 좌/우/키 처리는 _process_key 로 위임 (three.js 입력과 공유).
        self._process_key(event.key(), event.text())
        event.accept()  # 2D 캔버스 키는 항상 소비 (QLabel 기본 동작 불필요)

    # ── UI 마이그레이션 M5-a — three.js 2D 키 입력 진입점 ──
    def handle_key(self, js_key: str, text: str) -> None:
        """three.js 2D 키 입력 — JS KeyboardEvent.key 문자열을 Qt.Key_* 로
        매핑해 기존 키 처리 분기를 그대로 재사용."""
        qt_key = _JS_KEY_TO_QT.get((js_key or '').lower())
        if qt_key is not None:
            self._process_key(qt_key, text)

    def _process_key(self, key, text):
        """키 처리 — vispy keyPressEvent 와 three.js handle_key 공유.
        key 는 Qt.Key_*, text 는 입력 문자."""
        # 0) F5/F6 키는 항상 컨트롤러로 전달 (도크에 포커스가 있어도 토글 작동).
        if key in (Qt.Key_F5, Qt.Key_F6):
            self._controller.on_qt_key_press(text, key)
            return

        # 0a2) 정의 가져오기 미리보기 — R 회전 / V 기준점 / Esc 취소. 다른 키 차단.
        if self._import_preview is not None:
            pv = self._import_preview
            if key == Qt.Key_R:
                pv['rot'] = (pv['rot'] + 90) % 360
                if pv['_wx'] is not None:
                    self._import_update_at(pv['_wx'], pv['_wy'])
                return
            if key == Qt.Key_V:
                n = max(1, len(pv['pivots']))
                pv['anchor'] = (pv['anchor'] + 1) % n
                if pv['_wx'] is not None:
                    self._import_update_at(pv['_wx'], pv['_wy'])
                return
            if key == Qt.Key_Escape:
                self._cancel_import_preview()
                return
            return

        # 0b) 실 모드 — 그리기 / 이동·복사 미리보기 / 선택 실 조작. 부재 키 차단.
        if self._edit_mode == 'room':
            if self._room_draw_active:
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    self._finish_room_draw()
                    return
                if key == Qt.Key_Escape:
                    self._cancel_room_draw()
                    print('[ROOM] 실 그리기 취소')
                    return
                if key in (Qt.Key_Backspace, Qt.Key_Delete):
                    if self._room_points:
                        self._room_points.pop()
                        self.update()
                    return
                return  # 그리기 중엔 다른 키 무시
            # 이동/복사 미리보기 중 — V=기준꼭짓점, R=90°회전, Esc=취소
            if self._room_preview is not None:
                pv = self._room_preview
                if key == Qt.Key_V:
                    pv['anchor'] = (pv['anchor'] + 1) % max(1, len(pv['base']))
                    if pv['_wx'] is not None:
                        self._room_update_at(pv['_wx'], pv['_wy'])
                    return
                if key == Qt.Key_R:
                    pv['rot'] = (pv['rot'] + 90) % 360
                    if pv['_wx'] is not None:
                        self._room_update_at(pv['_wx'], pv['_wy'])
                    return
                if key == Qt.Key_Escape:
                    self._cancel_room_preview()
                    return
                return  # 미리보기 중 다른 키 무시
            # 선택 실 조작 — M 이동 / C 복사 / R 제자리 90°회전 / Del / Z / Esc
            if self._selected_room_id > 0:
                if key == Qt.Key_M:
                    self.begin_room_preview('move')
                    return
                if key == Qt.Key_C:
                    self.begin_room_preview('copy')
                    return
                if key == Qt.Key_R:
                    self._rotate_selected_room()
                    return
                if key in (Qt.Key_Delete, Qt.Key_Backspace):
                    rid = self._selected_room_id
                    if self._f5_on_room_delete is not None:
                        self._f5_on_room_delete(rid)
                    self._selected_room_id = -1
                    self.update()
                    self.room_selection_changed.emit(-1)
                    return
            if key == Qt.Key_Z and self._f5_on_undo is not None:
                self._f5_on_undo()
                return
            if key == Qt.Key_Escape:
                if self._selected_room_id != -1:
                    self._selected_room_id = -1
                    self.room_selection_changed.emit(-1)
                    self.update()
                return
            return  # 실 모드에선 부재 키 무시

        # 0c) 개구부 모드 — 미리보기 중 R/V/Esc, 아니면 Del/M/C/Esc. 부재 키 차단.
        if self._edit_mode == 'opening':
            pv = self._op_preview
            if pv is not None:
                if key == Qt.Key_R:
                    pv['rot'] = 90 if pv['rot'] == 0 else 0
                    if '_wx' in pv:
                        self._op_update_at(pv['_wx'], pv['_wy'])
                    return
                if key == Qt.Key_V:
                    pv['anchor'] = (pv['anchor'] + 1) % 4
                    if '_wx' in pv:
                        self._op_update_at(pv['_wx'], pv['_wy'])
                    return
                if key == Qt.Key_Escape:
                    self._cancel_opening_preview()
                    return
                return  # 미리보기 중 다른 키 무시
            # 미리보기 아님 — 선택 개구부 삭제/이동/복사
            if self._selected_opening is not None:
                cid, idx = self._selected_opening
                if key in (Qt.Key_Delete, Qt.Key_Backspace):
                    if self._f5_on_opening_delete is not None:
                        self._f5_on_opening_delete(int(cid), int(idx))
                    self._selected_opening = None
                    self.update()
                    return
                if key in (Qt.Key_M, Qt.Key_C):
                    comp = self._controller._scene.components.get(cid)
                    if comp is not None and 0 <= idx < len(comp.openings):
                        op = comp.openings[idx]
                        mode = 'move' if key == Qt.Key_M else 'copy'
                        self.begin_opening_preview(
                            float(op['w']), float(op['h']), float(op.get('v', 0.0)),
                            mode, src=(cid, idx), face=op.get('face'))
                    return
            if key == Qt.Key_Z and self._f5_on_undo is not None:
                self._f5_on_undo()
                return
            if key == Qt.Key_Escape:
                self._selected_opening = None
                self.update()
                return
            return  # 개구부 모드에선 부재 키 무시

        # 1) ALIGN_REFERENCE 우선 — 정렬 스냅(p1)
        if (self._state == STATE_REFERENCE and self._hover_id > 0
                and self._hover_id != self._selected_id):
            if key in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4, Qt.Key_5):
                self._apply_snap(key)
                return

        # 2a) F5 PREVIEW: R/V (회전·앵커) — 단계 3c
        if self._f5_in_preview:
            if key == Qt.Key_R and self._f5_on_rotate is not None:
                self._f5_on_rotate()
                return
            if key == Qt.Key_V and self._f5_on_anchor is not None:
                self._f5_on_anchor()
                return

        # 2) F5 PREVIEW 상태에서 Esc → 배치 취소
        if self._f5_in_preview and key == Qt.Key_Escape:
            if self._f5_on_escape is not None:
                self._f5_on_escape()
            return

        # 2c) Z 키 (Undo) — 어떤 상태에서든 직전 사용자 액션 한 번 되돌리기.
        #     M 키 발동 조건과 유사하게 키 입력만으로 즉시 실행.
        if key == Qt.Key_Z and self._f5_on_undo is not None:
            self._f5_on_undo()
            return

        # 2b) F5 SELECTED + Delete → 그룹 통째 삭제
        if (self._state == STATE_SELECTED and self._selected_id > 0
                and key in (Qt.Key_Delete, Qt.Key_Backspace)
                and self._f5_on_delete is not None):
            scene = self._controller._scene.components
            comp = scene.get(self._selected_id)
            if comp is not None:
                gid = getattr(comp, 'group_id', 0)
                if gid > 0:
                    self._f5_on_delete(gid)
                    self.reset_state()
                    return

        # 3) F5 IDLE에서 1~7 → 부재 타입 선택
        # 일반(1·2·3): 즉시 type_key 콜백 → DIMENSION_INPUT
        # 종속(4·5·6·7): DEPENDENCY_PICK 상태로 진입 → 부모 선택 후 type_key
        if (self._state == STATE_IDLE
                and not self._f5_in_preview
                and self._f5_on_type_key is not None
                and key in self._F5_KEY_TO_TYPE_INDEX):
            idx = self._F5_KEY_TO_TYPE_INDEX[key]
            types = [
                ComponentType.MODULE,
                ComponentType.FLOOR_PANEL,
                ComponentType.STRUCT_WALL,
                ComponentType.CANTILEVER_BEAM,
                ComponentType.CANTILEVER_SLAB,
                ComponentType.MID_BEAM,
                ComponentType.MID_COLUMN,
                ComponentType.VERTICAL_MODULE,
                ComponentType.CORE,
            ]
            ct = types[idx]
            if ct in (
                ComponentType.CANTILEVER_BEAM,
                ComponentType.CANTILEVER_SLAB,
                ComponentType.MID_BEAM,
                ComponentType.MID_COLUMN,
            ):
                # 종속 부재: DEPENDENCY_PICK 진입
                self._f5_pending_dep_type = ct
                self._state = STATE_DEPENDENCY_PICK
                self.update()
                print(f'[F5] DEPENDENCY_PICK 진입 ({ct.value}) — 부모 부재 클릭')
            else:
                # 일반 부재: 기존 흐름
                self._f5_on_type_key(ct)
            return

        # DEPENDENCY_PICK 상태 + Esc → 취소
        if self._state == STATE_DEPENDENCY_PICK and key == Qt.Key_Escape:
            self._f5_pending_dep_type = None
            self._state = STATE_IDLE
            self.update()
            print('[F5] DEPENDENCY_PICK 취소')
            return

        # 6) Esc 처리 — 정책 (b) 2026-05-12: 어떤 상태든 IDLE 로 완전 취소.
        # 옛 정책 (단계별 후퇴: REFERENCE→DIRECTION→SELECTED) 폐기.
        if key == Qt.Key_Escape:
            self.reset_state()
            return

        # 4) ALIGN_SELECTED → 방향 선택 / M 키 = 자유 이동(배치와 동일 흐름)
        if self._state == STATE_SELECTED and self._selected_id > 0:
            if key == Qt.Key_X:
                self._direction = 'X'
                self._state = STATE_DIRECTION
                self.update()
                return
            if key == Qt.Key_Y:
                self._direction = 'Y'
                self._state = STATE_DIRECTION
                self.update()
                return
            # M 키: 자유 이동 모드 — 배치와 동일하게 마우스 따라 반투명 고스트 +
            # R/V 작동. 치수는 기존 부재 그대로 사용.
            if key == Qt.Key_M:
                if self._f5_on_move_key is not None:
                    self._f5_on_move_key(self._selected_id)
                return
            # C 키: 복사 — M 과 동일 흐름이되 원본 부재는 그대로 두고 새 부재를
            # 같은 위치에 만들어 마우스 따라 이동 후 클릭으로 위치 확정.
            if key == Qt.Key_C:
                if self._f5_on_copy is not None:
                    self._f5_on_copy(self._selected_id)
                return

        # (옛 중복 분기 제거 2026-05-12) REFERENCE+hover 1~5 키 처리는 위쪽
        # 분기에서 이미 잡혔으므로 본 자리 도달 불가.
        # 처리 안 된 키 — 2D 캔버스는 기본 동작 불필요하므로 무시.
        return

    def _apply_snap(self, key):
        """1/2/3/4/5 키에 해당하는 스냅으로 이동 요청.
        1: -220, 2: 정확, 3: +220, 4: -20, 5: +20 (대상 모서리 기준)."""
        scene = self._controller._scene.components
        target = scene.get(self._hover_id)
        if target is None or self._reference_edge is None:
            return
        axis, ref_coord = self._reference_edge
        tgt = self._pick_target_edge(target, axis)

        delta_exact = tgt - ref_coord
        if key == Qt.Key_1:
            delta = delta_exact - SNAP_220
        elif key == Qt.Key_2:
            delta = delta_exact
        elif key == Qt.Key_3:
            delta = delta_exact + SNAP_220
        elif key == Qt.Key_4:
            delta = delta_exact - SNAP_20
        else:  # Key_5
            delta = delta_exact + SNAP_20

        if abs(delta) < EPS:
            # 이동량 0 → 의미 없음, 상태만 리셋
            self.reset_state()
            return

        self.move_requested.emit(self._selected_id, axis, delta)
        # 이동 후 자동 ESC
        self.reset_state()


# 단계 6: AlignmentDialog 클래스 제거 — F5 도킹 모드(AlignmentDockPanel)로 일원화.


# ── F5 도킹 패널 ─────────────────────────────────────────
# 단계 2: AlignmentDialog(QDialog 모달) → AlignmentDockPanel(QWidget 도킹).
# 우측 도킹 영역에 표시되며, 좌측 3D 뷰는 그대로 두고 동시 표시한다.
# 상단에 층수 입력(SpinBox)을 두어 다층 그룹 생성에 사용한다(g1·g2·Z1·p3).

class AlignmentDockPanel(QWidget):
    """F5 모드 — 2D 탑뷰 배치 + 정렬 도킹 패널.

    레이아웃:
      [상단] 층수 입력 (1~25) + 도움말
      [중앙] AlignmentCanvas (2D 탑뷰)
    """

    # 층수 변경 시그널 — main_3d/Controller가 받아 다층 그룹 재생성
    floors_changed = pyqtSignal(int)
    # 저장/불러오기 시그널 — Controller가 받아 처리
    save_requested = pyqtSignal()
    load_requested = pyqtSignal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller

        # [2026-05-11 v3] 패널 전체 최소 폭 제거 — QSplitter 가 자유 축소 가능하게.
        # 기본 sizePolicy 가 Preferred 라 자식의 sizeHint 가 누적되어 splitter 가 막힘.
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 상단: 층수 입력 + 저장/불러오기 + 도움말 ──────────
        top_row = QHBoxLayout()
        top_row.setContentsMargins(8, 4, 8, 4)
        self._floors_label = QLabel('층수:')
        top_row.addWidget(self._floors_label)
        # 층수는 3 의 배수만 허용 (수직 3층 모듈이 한 번에 3개 층을 차지하므로
        # 단층 모듈과 섞어 쓸 때 칸마다 빠짐없이 채워지려면 N 이 3 의 배수여야 함).
        self._floors_spin = QSpinBox()
        self._floors_spin.setRange(3, 24)
        self._floors_spin.setSingleStep(3)
        self._floors_spin.setValue(3)
        self._floors_spin.setFixedWidth(60)
        self._floors_spin.valueChanged.connect(self._on_floors_changed)
        top_row.addWidget(self._floors_spin)
        self._floors_unit_label = QLabel('층  (3의 배수)')
        top_row.addWidget(self._floors_unit_label)
        top_row.addStretch()

        self._save_btn = QPushButton('저장')
        self._save_btn.setFixedWidth(60)
        self._save_btn.clicked.connect(self.save_requested.emit)
        top_row.addWidget(self._save_btn)
        self._load_btn = QPushButton('불러오기')
        self._load_btn.setFixedWidth(80)
        self._load_btn.clicked.connect(self.load_requested.emit)
        top_row.addWidget(self._load_btn)

        top_widget = QWidget()
        top_widget.setLayout(top_row)
        top_widget.setMaximumHeight(34)
        # [2026-05-11 v3] 패널 가로 축소 시 버튼이 잘리는 것을 허용 (clip OK)
        top_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top_widget.setMinimumWidth(0)
        layout.addWidget(top_widget)

        # 도움말 — [2026-05-11 v3] wordWrap + Ignored 가로 정책으로 splitter 자유 축소 허용
        self._help = QLabel(
            '  배치: 1~7, 8(수직3층모듈), 9(RC 코어)  |  회전 R · 앵커 V · 이동 M · 복사 C · 삭제 Del  |  '
            '정렬: 부재 클릭→X/Y→모서리→스냅 1(−220)/2(정확)/3(+220)/4(−20)/5(+20)mm  |  Esc: 뒤로'
        )
        self._help.setStyleSheet(
            'background: #333; color: #ccc; padding: 4px; font-size: 11px;')
        self._help.setWordWrap(True)
        self._help.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._help.setMinimumWidth(0)
        layout.addWidget(self._help)

        # ── 중앙: 2D 탑뷰 캔버스 ───────────────────────
        self._canvas = AlignmentCanvas(controller, self)
        layout.addWidget(self._canvas)
        self._canvas.move_requested.connect(self._apply_move)

    @property
    def canvas(self):
        return self._canvas

    @property
    def floors(self) -> int:
        return int(self._floors_spin.value())

    def set_floors(self, n: int, emit: bool = False):
        """외부에서 층수 변경. emit=True 면 floors_changed 시그널 발화.
        3 의 배수가 아니면 가장 가까운 3 의 배수로 자동 보정.
        """
        snapped = self._snap_to_three(int(n))
        self._floors_spin.blockSignals(True)
        self._floors_spin.setValue(snapped)
        self._floors_spin.blockSignals(False)
        if emit:
            self.floors_changed.emit(snapped)

    def set_floors_control_visible(self, visible: bool):
        """층수 입력 컨트롤(라벨+스핀박스) 표시/숨김.

        정의 탭처럼 단일층 고정 작업공간에서는 층수 입력이 의미 없으므로 숨긴다.
        """
        for w in (self._floors_label, self._floors_spin, self._floors_unit_label):
            w.setVisible(visible)

    def hide_canvas(self):
        """UI 마이그레이션 M8-b — vispy QPainter 2D 캔버스·도움말만 숨김.

        상단 컨트롤(층수 SpinBox·저장·불러오기)은 유지. 캔버스 자리에는 three.js
        AlignmentDockPanelThree 가 표시된다. AlignmentCanvas 인스턴스는 입력·모델
        로직·sync 핸들러로 계속 살아있다 (위젯 표시만 제거)."""
        self._canvas.setVisible(False)
        self._help.setVisible(False)

    @staticmethod
    def _snap_to_three(n: int) -> int:
        """가장 가까운 3 의 배수로 스냅 (동률은 내림). 최소 3."""
        if n < 3:
            return 3
        lower = (n // 3) * 3
        upper = lower + 3
        # 거리 같으면 내림(예: 4·5 의 경우 4→3, 5→6 자동 결정).
        return lower if (n - lower) <= (upper - n) else upper

    def _on_floors_changed(self, n: int):
        # Z2: undo 불가, 단순 emit만 (Controller가 전체 재생성 처리).
        # QSpinBox 의 setSingleStep(3) 으로 화살표 이동은 항상 3 단위지만,
        # 키보드로 임의 값을 입력하면 비-3 배수가 들어올 수 있어 안전망 스냅.
        snapped = self._snap_to_three(int(n))
        if snapped != int(n):
            self._floors_spin.blockSignals(True)
            self._floors_spin.setValue(snapped)
            self._floors_spin.blockSignals(False)
            print(f'[F5] 층수 {n} → 가장 가까운 3 배수 {snapped} 로 보정')
        else:
            print(f'[F5] 층수 변경 → {snapped}')
        self.floors_changed.emit(snapped)

    def request_focus(self):
        """캔버스에 포커스 — 키 라우팅 필요 시 사용."""
        self._canvas.setFocus()

    # ── 정렬 이동 적용 (단계 5: 다층 그룹 시스템으로 전환) ────
    def _apply_move(self, comp_id: int, axis: int, delta: float):
        """선택 부재가 속한 다층 그룹 전체를 (axis, delta)만큼 이동(g5).

        페어/children/sibling 등 레거시 자료구조 의존 제거 — group_id 기반.
        """
        ctrl = self._controller
        comp = ctrl._scene.components.get(comp_id)
        if comp is None:
            return

        from modular_3d.render.mesh_builder import build_component_mesh
        from modular_3d.model.multi_floor import translate_group

        gid = getattr(comp, 'group_id', 0)
        delta_vec = np.zeros(3)
        delta_vec[axis] = delta

        if gid > 0:
            # 그룹 통째 이동
            affected = translate_group(ctrl._scene, gid, delta_vec)
            # 코어 그룹 이동 시 코어 슬래브가 regenerate 되어 옛 ID 가
            # affected 안에 있어도 scene 에서 pop 됐을 수 있다 — viewer/snap
            # 에서도 청소.
            for cid in affected:
                c = ctrl._scene.components.get(cid)
                if c is None:
                    ctrl._viewer.remove_component_visual(cid)
                    ctrl._snap.remove_component(cid)
                    continue
                ctrl._viewer.remove_component_visual(cid)
                v, f, cl = build_component_mesh(c)
                ctrl._viewer.add_component_visual(cid, v, f, cl)
                ctrl._snap.remove_component(cid)
                ctrl._snap.add_component(cid, c)
            # 같은 group_id 의 부재 중 affected 누락분(새로 생성된 코어 슬래브)
            # 도 viewer/snap 에 추가. 옛 ID 가 viewer 에 남아 있을 수 있으므로
            # remove → add 로 안전 갱신.
            affected_set = set(affected)
            for cid_extra, comp_extra in ctrl._scene.components.items():
                if getattr(comp_extra, 'group_id', 0) != gid:
                    continue
                if cid_extra in affected_set:
                    continue
                ctrl._viewer.remove_component_visual(cid_extra)
                v, f, cl = build_component_mesh(comp_extra)
                ctrl._viewer.add_component_visual(cid_extra, v, f, cl)
                ctrl._snap.remove_component(cid_extra)
                ctrl._snap.add_component(cid_extra, comp_extra)
            print(f'[ALIGN] group={gid} ({len(affected)}개) '
                  f'{"XY"[axis]}축 {delta:+.0f}mm')
        else:
            # 그룹 미부여(레거시 부재) — 단일 부재만 이동
            comp.position = comp.position + delta_vec
            comp.generate_sub_components()
            ctrl._viewer.remove_component_visual(comp_id)
            v, f, cl = build_component_mesh(comp)
            ctrl._viewer.add_component_visual(comp_id, v, f, cl)
            ctrl._snap.remove_component(comp_id)
            ctrl._snap.add_component(comp_id, comp)
            print(f'[ALIGN] #{comp_id} (단일) {"XY"[axis]}축 {delta:+.0f}mm')

        ctrl._status.update_count(ctrl._scene.component_count)
        # X/Y 정렬·스냅으로 코어벽이 이동했으면 슬래브 자동 재생성(코어 변경 시에만).
        if hasattr(ctrl, '_auto_regen_core_slabs'):
            ctrl._auto_regen_core_slabs()
        self._canvas.update()
