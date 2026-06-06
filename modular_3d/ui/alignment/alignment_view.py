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
    'm': Qt.Key_M, 'c': Qt.Key_C, 'z': Qt.Key_Z, 'i': Qt.Key_I,
    't': Qt.Key_T,   # 거리 측정 도구 토글
    'p': Qt.Key_P,   # 코어 슬래브 그리기

    'escape': Qt.Key_Escape, 'enter': Qt.Key_Return,
    'delete': Qt.Key_Delete, 'backspace': Qt.Key_Backspace,
    'f5': Qt.Key_F5, 'f6': Qt.Key_F6,
    ' ': Qt.Key_Space, '-': Qt.Key_Minus,   # 수치 이동 입력(스페이스 진입/적용, 음수)
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
    # 다중선택 묶음 통째 정렬 — (선택 id 목록, axis, delta)
    move_multi_requested = pyqtSignal(object, int, float)
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
        # 다중선택(Ctrl+클릭): 직접 고른 본체 부재 id 목록(순서 보존, [0]=기준 seed).
        # _selected_id 는 항상 이 목록의 seed(첫 원소) 또는 -1 과 동기화한다(단일선택
        # 코드 경로가 _selected_id 만 읽어도 동작하도록). 길이 2 이상이면 다중 경로.
        self._selected_ids: list = []
        # X/Y 정렬을 다중(묶음 통째)으로 진입했는지 표시 — _apply_snap 분기용.
        self._multi_align = False
        # 수치 거리 이동 모드(스페이스바 진입): 축 선택(x/y) + 부호 숫자 입력 후
        # 스페이스/엔터로 그 거리만큼 평행이동. _num_axis: None|0(x)|1(y).
        self._num_active = False
        self._num_axis = None
        self._num_buf = ''
        # 거리 측정 도구(T 키): 선(부재 외곽 모서리) 두 개를 클릭해 사이 거리 표시.
        # _measure_pts: [(axis, coord, wx, wy), ...] (0~2개). axis 0=세로선(x), 1=가로선(y).
        self._measure_active = False
        self._measure_pts = []
        self._measure_result = None   # {'p1':[x,y],'p2':[x,y],'dist':float} or None
        # 코어 슬래브 그리기(P 키): 실 그리기처럼 폴리곤 점을 찍어 코어 슬래브 생성.
        self._core_slab_draw_active = False
        self._core_slab_points = []          # [(wx, wy), ...]
        self._core_slab_snap_point = None
        # 내벽 수동 종속(i 키): 부모 클릭을 기다리는 내벽 id (-1=비활성).
        self._wall_attach_id = -1
        # 코어벽 종속(i 키): 슬래브 클릭을 기다리는 코어벽 id 목록(빈=비활성).
        self._core_attach_ids = []
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
        # 실 다중선택(Ctrl+클릭): 고른 실 id 목록(순서 보존, [0]=기준 seed).
        # _selected_room_id 는 항상 이 목록의 seed 또는 -1 과 동기화.
        self._selected_room_ids = []
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
        # 다중선택(Ctrl+클릭) 집합 — 비면 단일 selected_id 로 대체.
        sel_ids = (list(self._selected_ids) if getattr(self, '_selected_ids', None)
                   else ([selected_id] if selected_id > 0 else []))
        selected_set = set(int(c) for c in sel_ids)
        # 정렬 오차는 단일선택일 때만 의미.
        mis_dict = (self._misaligned_set(selected_id)
                    if len(sel_ids) == 1 and selected_id > 0 else {})

        # 같은 그룹 + 합체 파트너 — 다중이면 고른 모든 부재 기준 합집합.
        sel_gids = set()
        for sid in sel_ids:
            c0 = scene.get(int(sid))
            g0 = int(getattr(c0, 'group_id', 0) or 0) if c0 is not None else 0
            if g0 > 0:
                sel_gids.add(g0)
        same_group_ids = set()
        if sel_gids:
            for cid_o, c in scene.items():
                if (int(cid_o) not in selected_set
                        and int(getattr(c, 'group_id', 0) or 0) in sel_gids):
                    same_group_ids.add(int(cid_o))
        merged_partner_ids = set()
        for sid in sel_ids:
            sel = scene.get(int(sid))
            if sel is None:
                continue
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
            if int(cid) in selected_set or int(cid) in merged_partner_ids:
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

            # [C4·F1] 라벨은 1개 층만(floor_index==0). 독립 부재는 '모듈A-1',
            # 종속(캔틸 등)은 '모듈A-2 캔틸레버보1', 코어벽 'RC코어벽1',
            # 코어 슬래브 'RC코어 슬래브'(같은 텍스트라 겹쳐도 1개로 보임).
            _raw_label = (type_labels.get(int(cid), f'{tname}#{int(cid)}')
                          if int(getattr(comp, 'floor_index', 0) or 0) == 0
                          else '')
            # 종속 부재 라벨('부모라벨 종속명')은 2D 표시에서 두 줄로 — 부모↔종속을
            # 가르는 첫 공백만 줄바꿈한다. 코어 슬래브 'RC코어 슬래브'처럼 단일
            # 명칭 안의 공백은 분리하면 안 되므로 코어류는 제외한다. 흡수(합체)
            # 벽패널도 '패널라벨 벽패널1' 형식이라 함께 두 줄 처리한다.
            _ctv = ct.value if ct is not None else ''
            _is_dep_label = (int(getattr(comp, 'sub_index', 0) or 0) > 0
                             and _ctv not in ('core', 'core_slab'))
            _is_merged_wall = (_ctv == 'struct_wall'
                               and getattr(comp, 'merged_fp_id', None))
            if (_is_dep_label or _is_merged_wall) and ' ' in _raw_label:
                _raw_label = _raw_label.replace(' ', '\n', 1)
            # 폴리곤 코어 슬래브 — 2D 에 실제 폴리곤 외곽(월드 xy) 전달(사각형 대신).
            comp_poly = None
            if (_ctv == 'core_slab' and comp.dimensions.get('polygon')
                    and getattr(comp, 'slab', None) is not None):
                try:
                    comp_poly = [[float(c[0]), float(c[1])]
                                 for c in comp.slab.corners]
                except Exception:
                    comp_poly = None
            components.append({
                'id': int(cid),
                'comp_type': ct.value if ct is not None else '',
                'group_id': int(getattr(comp, 'group_id', 0) or 0),
                'floor_index': int(getattr(comp, 'floor_index', 0) or 0),
                'sub_index': int(getattr(comp, 'sub_index', 0) or 0),
                'rects': rects,
                'bbox': [float(bx0), float(by0), float(bx1), float(by1)],
                'polygon': comp_poly,
                'label': _raw_label,
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
                'selected': bool(rid == self._selected_room_id
                                 or int(rid) in self._selected_room_ids),
            })

        # ── M3-c-2: 미리보기·고스트·스냅 (Python 에서 계산) ──
        # F5 PREVIEW 고스트 — 실제 부재와 같은 형식(역할 사각형 + 개구부 + bbox)으로
        # 정밀 표시. 공유 빌더(controller)가 2D·3D 동일한 변환 부재를 만들어, 두 화면
        # 고스트가 항상 정합한다. [함정] JS `_drawPreviews` 가 f5_ghost.comps 를 읽으므로
        # 구조를 바꾸면 양쪽을 같이 고쳐야 한다.
        f5_ghost = None
        if self._f5_in_preview:
            mx, my = (float(self._f5_mouse_world[0]),
                      float(self._f5_mouse_world[1]))
            gcomps = None
            try:
                gcomps = self._controller._f5_build_ghost_components(mx, my)
            except Exception:
                gcomps = None
            if gcomps:
                from modular_3d.render.opening_mesh import opening_xy_polygons
                comps_payload = []
                for gc in gcomps:
                    grects = []
                    try:
                        for rect, role in _iter_component_rects(gc, self._layer):
                            x0, y0, x1, y1 = rect
                            grects.append({'role': role,
                                           'x0': float(x0), 'y0': float(y0),
                                           'x1': float(x1), 'y1': float(y1)})
                    except Exception:
                        pass
                    try:
                        bx0, by0, bx1, by1 = _xy_bbox(gc)
                        bbox = [float(bx0), float(by0), float(bx1), float(by1)]
                    except Exception:
                        bbox = None
                    gops = []
                    try:
                        for (idx, pts, kind) in opening_xy_polygons(gc):
                            gops.append([[float(px), float(py)]
                                         for (px, py) in pts])
                    except Exception:
                        pass
                    comps_payload.append({'rects': grects, 'bbox': bbox,
                                          'openings': gops})
                f5_ghost = {'comps': comps_payload, 'mouse': [mx, my]}

        # 실 이동/복사 미리보기 폴리곤 (계산된 결과)
        room_preview_poly = None
        room_preview_multi = None
        if self._room_preview is not None and self._room_preview.get('multi'):
            try:
                polys = self._room_preview_polygons_multi()
            except Exception:
                polys = {}
            room_preview_multi = [
                [[float(x), float(y)] for (x, y) in poly]
                for poly in polys.values() if poly and len(poly) >= 2
            ]
        elif self._room_preview is not None:
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

        # ── 부재 겹침 경고 — 같은 종류·같은 층에서 평면(xy)이 실제 침범하는 쌍의
        #    교집합 영역. 위아래 적층(같은 xy·다른 층)은 정상이라 floor_index 로
        #    그룹을 나눠 제외한다. 20mm 갭·맞닿음·수치오차는 _OVERLAP_MARGIN 으로
        #    무시하고 '실제로 파고든' 침범만 빨갛게 표시(거친 bbox 기준).
        # 같은 group_id(한 모듈/한 부재의 내부 격자 — 중간보·중간기둥 등)는
        # 정상적으로 bbox 가 겹치므로 제외. 서로 다른 그룹(또는 독립 부재 group 0)
        # 간 침범만 '의도치 않은 중복 배치'로 본다.
        from collections import defaultdict as _dd
        _OVERLAP_MARGIN = 1.0   # mm
        _ov_groups = _dd(list)
        for _c in components:
            _ov_groups[(_c['comp_type'], _c['floor_index'])].append(
                (int(_c['group_id']), _c['bbox']))
        overlaps = []
        for _items in _ov_groups.values():
            _n = len(_items)
            for _i in range(_n):
                _ga, _a = _items[_i]
                for _j in range(_i + 1, _n):
                    _gb, _b = _items[_j]
                    if _ga == _gb and _ga != 0:
                        continue   # 같은 모듈/그룹 내부 — 정상
                    _ox0 = max(_a[0], _b[0]); _oy0 = max(_a[1], _b[1])
                    _ox1 = min(_a[2], _b[2]); _oy1 = min(_a[3], _b[3])
                    if (_ox1 - _ox0 > _OVERLAP_MARGIN
                            and _oy1 - _oy0 > _OVERLAP_MARGIN):
                        overlaps.append([_ox0, _oy0, _ox1, _oy1])

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
            'overlaps': overlaps,
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
            'room_preview': (None if (self._room_preview is not None
                                      and self._room_preview.get('multi'))
                             else self._room_preview),
            'room_preview_multi': room_preview_multi,
            'core_slab_draw': {
                'active': bool(self._core_slab_draw_active),
                'points': [[float(x), float(y)] for (x, y) in self._core_slab_points],
            },
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
            # 거리 측정 결과(선 두 개 사이) — {p1,p2,dist} 또는 None
            'measure': (self._measure_result if self._measure_active else None),
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

    # ── DEPENDENCY_PICK 외부 진입 (벽패널 ↔ FP 합체 대상 선택용) ──
    # [2026-05-11] 좌측 팔레트 버튼용 진입점.
    # 키 1~8 입력 시 keyPressEvent 안의 분기와 100% 동일한 동작을 수행.
    # 캔틸레버보(4) · 캔틸레버슬래브(5) · 중간보(6) · 중간기둥(7) 은 DEPENDENCY_PICK
    # 상태로 진입하여 paint 가 후보 부모 부재를 하이라이트하도록 한다.
    def handle_palette_select(self, comp_type: ComponentType):
        if self._f5_in_preview or self._state != STATE_IDLE:
            # 진행 중인 배치/이동 흐름 있음 — 기존 흐름 유지를 위해 무시
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
            return
        # 일반 부재 — 컨트롤러의 type_key 콜백 직접 호출 (사이즈 입력)
        if self._f5_on_type_key is not None:
            self._f5_on_type_key(comp_type)

    def enter_dependency_pick(self, dep_type):
        """controls.py 가 dim 확정 후 호출 — DEPENDENCY_PICK 상태로 진입.

        벽패널의 경우 dim 패널의 '바닥패널과 병합' 체크박스가 켜진 상태로
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
        if self._selected_room_id != -1 or self._selected_room_ids:
            self._selected_room_id = -1
            self._selected_room_ids = []
            self.room_selection_changed.emit(-1)
        self._cancel_opening_preview()
        self._selected_opening = None
        if self._f5_in_preview and self._f5_on_escape is not None:
            self._f5_on_escape()
        self.reset_state()   # 부재 선택 해제(STATE_IDLE)
        self._edit_mode = mode
        self.setFocus()
        self.update()

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
        if pv.get('multi'):
            # 다중: 2D payload 가 모든 폴리곤을 그림(3D 고스트는 생략).
            self.update()
            return
        poly = self._room_preview_polygon(wx, wy)
        if poly and self._f5_on_room_ghost is not None:
            self._f5_on_room_ghost(pv['room_type'], poly)
        self.update()

    def _room_commit_at(self, wx, wy):
        """클릭 — 실 이동/복사 확정."""
        pv = self._room_preview
        if pv is None:
            return
        if pv.get('multi'):
            polys = self._room_preview_polygons_multi(wx, wy)
            mode = pv['mode']
            self._cancel_room_preview()
            items = [(rid, poly) for rid, poly in polys.items()]
            if items and hasattr(self._controller, '_f5_on_room_commit_multi'):
                self._controller._f5_on_room_commit_multi(items, mode)
            return
        poly = self._room_preview_polygon(wx, wy)
        src = pv['src']; mode = pv['mode']
        self._cancel_room_preview()
        if poly and self._f5_on_room_commit is not None:
            self._f5_on_room_commit(src, poly, mode)

    def begin_room_preview_multi(self, mode):
        """다중 실 이동/복사 미리보기 — 기준 실(첫 클릭) 중심 공통 변환."""
        ids = list(self._selected_room_ids)
        if len(ids) < 2:
            return
        rooms = self._controller._scene.rooms
        seed = rooms.get(ids[0])
        if seed is None or len(getattr(seed, 'polygon', [])) < 3:
            return
        scx = sum(p[0] for p in seed.polygon) / len(seed.polygon)
        scy = sum(p[1] for p in seed.polygon) / len(seed.polygon)
        bases = {}
        rtypes = {}
        for rid in ids:
            r = rooms.get(rid)
            if r is None or len(getattr(r, 'polygon', [])) < 3:
                continue
            bases[rid] = [(float(x), float(y)) for (x, y) in r.polygon]
            rtypes[rid] = r.room_type
        if len(bases) < 2:
            return
        self._room_preview = {
            'mode': mode, 'multi': True, 'seed_id': ids[0],
            'seed_center': (scx, scy),
            'base': bases[ids[0]],     # V 앵커는 seed 폴리곤 꼭짓점 기준
            'bases': bases, 'room_types': rtypes,
            'anchor': 0, 'rot': 0, '_wx': None, '_wy': None,
        }
        self.setFocus()
        self.update()

    def _room_preview_polygons_multi(self, wx=None, wy=None):
        """다중 미리보기 — {room_id: 변환된 폴리곤}. seed 중심 회전 + seed 앵커→마우스."""
        pv = self._room_preview
        if pv is None or not pv.get('multi'):
            return {}
        import math
        scx, scy = pv['seed_center']
        rad = math.radians(pv['rot'])
        cs, sn = math.cos(rad), math.sin(rad)
        if wx is None:
            wx, wy = pv.get('_wx'), pv.get('_wy')
        seed_base = pv['base']
        ai = pv['anchor'] % len(seed_base)
        sx0, sy0 = seed_base[ai]
        rax = scx + (sx0 - scx) * cs - (sy0 - scy) * sn
        ray = scy + (sx0 - scx) * sn + (sy0 - scy) * cs
        ddx, ddy = (0.0, 0.0) if wx is None else (wx - rax, wy - ray)
        out = {}
        for rid, base in pv['bases'].items():
            poly = []
            for (x, y) in base:
                rx = scx + (x - scx) * cs - (y - scy) * sn
                ry = scy + (x - scx) * sn + (y - scy) * cs
                poly.append((rx + ddx, ry + ddy))
            out[rid] = poly
        return out

    def _rotate_rooms_multi(self):
        """다중 실 제자리 묶음 회전 — 기준 실(첫 클릭) 중심 90° 회전 후 확정."""
        ids = list(self._selected_room_ids)
        if len(ids) < 2:
            return
        rooms = self._controller._scene.rooms
        seed = rooms.get(ids[0])
        if seed is None or len(getattr(seed, 'polygon', [])) < 3:
            return
        scx = sum(p[0] for p in seed.polygon) / len(seed.polygon)
        scy = sum(p[1] for p in seed.polygon) / len(seed.polygon)
        items = []
        for rid in ids:
            r = rooms.get(rid)
            if r is None or len(getattr(r, 'polygon', [])) < 3:
                continue
            rot = [(scx - (y - scy), scy + (x - scx)) for (x, y) in r.polygon]
            items.append((rid, rot))
        if items and hasattr(self._controller, '_f5_on_room_commit_multi'):
            self._controller._f5_on_room_commit_multi(items, 'move')

    def _flip_selected_room(self, axis):
        """단일 실 제자리 거울 뒤집기 — 자기 중심 축 기준 폴리곤 반사(전 층 동기)."""
        rid = self._selected_room_id
        room = self._controller._scene.rooms.get(rid)
        if room is None or len(getattr(room, 'polygon', [])) < 3:
            return
        cx = sum(p[0] for p in room.polygon) / len(room.polygon)
        cy = sum(p[1] for p in room.polygon) / len(room.polygon)
        if axis == 'x':
            fl = [(2 * cx - x, y) for (x, y) in room.polygon]
        else:
            fl = [(x, 2 * cy - y) for (x, y) in room.polygon]
        if self._f5_on_room_commit is not None:
            self._f5_on_room_commit(rid, fl, 'move')

    def _flip_rooms_multi(self, axis):
        """다중 실 거울 뒤집기 — 기준 실(첫 클릭) 중심 축 기준 전체 반사(자리바꿈+각 반사)."""
        ids = list(self._selected_room_ids)
        if len(ids) < 2:
            return
        rooms = self._controller._scene.rooms
        seed = rooms.get(ids[0])
        if seed is None or len(getattr(seed, 'polygon', [])) < 3:
            return
        scx = sum(p[0] for p in seed.polygon) / len(seed.polygon)
        scy = sum(p[1] for p in seed.polygon) / len(seed.polygon)
        items = []
        for rid in ids:
            r = rooms.get(rid)
            if r is None or len(getattr(r, 'polygon', [])) < 3:
                continue
            if axis == 'x':
                fl = [(2 * scx - x, y) for (x, y) in r.polygon]
            else:
                fl = [(x, 2 * scy - y) for (x, y) in r.polygon]
            items.append((rid, fl))
        if items and hasattr(self._controller, '_f5_on_room_commit_multi'):
            self._controller._f5_on_room_commit_multi(items, 'move')

    def _delete_rooms_multi(self):
        """다중 실 삭제."""
        ids = list(self._selected_room_ids)
        if ids and hasattr(self._controller, '_f5_on_room_delete_multi'):
            self._controller._f5_on_room_delete_multi(ids)
        self._selected_room_id = -1
        self._selected_room_ids = []
        self.update()
        self.room_selection_changed.emit(-1)

    # ── 코어 슬래브 그리기 (P 키, 실 그리기 흐름 재사용) ─────────
    def start_core_slab_draw(self):
        """P 키 — 코어 슬래브 폴리곤 그리기 시작(부재 모드). 완료 시 자동 종료."""
        self._core_slab_draw_active = True
        self._core_slab_points = []
        self._core_slab_snap_point = None
        self._selected_id = -1
        self._selected_ids = []
        self.setFocus()
        if hasattr(self._controller, '_dim_panel') and self._controller._dim_panel:
            self._controller._dim_panel.set_mode_text(
                '[코어 슬래브 그리기: 점 클릭 / Enter 완료 / 우클릭=취소 / Esc 종료]')
        self.update()

    def _finish_core_slab_draw(self):
        """Enter — 점 3개 이상이면 코어 슬래브 생성 후 모드 자동 종료."""
        pts = list(self._core_slab_points)
        self._core_slab_draw_active = False
        self._core_slab_points = []
        self._core_slab_snap_point = None
        self.update()
        if len(pts) >= 3 and hasattr(self._controller, '_f5_on_core_slab_complete'):
            self._controller._f5_on_core_slab_complete(pts)

    def _cancel_core_slab_draw(self):
        """Esc — 코어 슬래브 그리기 취소."""
        self._core_slab_draw_active = False
        self._core_slab_points = []
        self._core_slab_snap_point = None
        if hasattr(self._controller, '_dim_panel') and self._controller._dim_panel:
            self._controller._dim_panel.set_mode_text('[IDLE]')
        self.update()

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
        if self._selected_room_id != -1 or self._selected_room_ids:
            self._selected_room_id = -1
            self._selected_room_ids = []
            self.room_selection_changed.emit(-1)
        self._room_draw_active = True
        self._room_points = []
        self._room_snap_point = None
        self.setFocus()
        self.update()

    def clear_room_selection(self):
        """실 선택 해제(패널 삭제 등 외부 트리거용) — 시그널 발생 없음."""
        if self._selected_room_id != -1 or self._selected_room_ids:
            self._selected_room_id = -1
            self._selected_room_ids = []
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
            pass

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
        self._selected_ids = []
        self._multi_align = False
        self._num_active = False
        self._num_axis = None
        self._num_buf = ''
        self._measure_active = False
        self._measure_pts = []
        self._measure_result = None
        self._core_slab_draw_active = False
        self._core_slab_points = []
        self._wall_attach_id = -1
        self._core_attach_ids = []
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
        # Ctrl 동시누름이면 다중선택 토글 신호로 전달.
        ctrl_held = bool(event.modifiers() & Qt.ControlModifier)
        self._process_press_at(event.pos().x(), event.pos().y(),
                               event.button(), ctrl=ctrl_held)

    # ── UI 마이그레이션 M4-b — three.js 2D 클릭 진입점 ──
    def handle_world_click(self, wx: float, wy: float, button: int,
                           ctrl: bool = False) -> None:
        """three.js 2D 의 클릭 — world 좌표를 받아 기존 선택 분기를 그대로 재사용.

        three.js 는 자체 카메라 world 좌표를 보냄. 이를 vispy `_world_to_screen`
        으로 환산하면 (왕복 항등) 기존 _process_press_at 이 `_screen_to_world`
        로 정확히 복원해 동일 동작. button: 0=left, 2=right (JS 규약).
        ctrl=True 면 다중선택 토글(Ctrl+클릭).
        """
        qt_btn = Qt.RightButton if button == 2 else Qt.LeftButton
        sx, sy = self._world_to_screen(wx, wy)
        self._process_press_at(int(sx), int(sy), qt_btn, ctrl=bool(ctrl))

    def _process_press_at(self, mx, my, button, ctrl=False):
        """좌/우클릭 처리 — vispy mousePressEvent 와 three.js handle_world_click
        이 공유. mx, my 는 *vispy screen 좌표*, button 은 Qt.LeftButton 등.
        ctrl=True 면 본체 부재 다중선택 토글(Ctrl+클릭)."""
        # ── 내벽 수동 종속(i 키): 부모 클릭 대기 ──
        # 좌클릭한 모듈/패널을 부모로 삼아 종속. 우클릭/빈 곳/취소면 모드 해제.
        if getattr(self, '_wall_attach_id', -1) > 0:
            if button == Qt.LeftButton:
                hit = self._hit_test_component(mx, my)
                ctrl = self._controller
                if (hit and int(hit) > 0
                        and hasattr(ctrl, '_f5_attach_wall_to_parent')):
                    ctrl._f5_attach_wall_to_parent(self._wall_attach_id, int(hit))
            self._wall_attach_id = -1
            self.reset_state()
            return
        # ── 코어벽 종속(i 키): 좌클릭한 코어 슬래브에 선택 코어벽들을 종속 ──
        if self._core_attach_ids:
            if button == Qt.LeftButton:
                hit = self._hit_test_component(mx, my)
                ctrl = self._controller
                if hit and int(hit) > 0 and hasattr(ctrl, '_f5_attach_core_to_slab'):
                    ctrl._f5_attach_core_to_slab(
                        list(self._core_attach_ids), int(hit))
            self._core_attach_ids = []
            self.reset_state()
            return
        # ── 거리 측정 모드: 좌클릭으로 선(모서리) 2개를 찍어 거리 표시 ──
        if self._measure_active:
            if button == Qt.LeftButton:
                self._measure_click(mx, my)
            return
        # ── 코어 슬래브 그리기: 좌클릭=점 추가(스냅), 우클릭=직전 점 취소 ──
        if self._core_slab_draw_active:
            if button == Qt.RightButton:
                if self._core_slab_points:
                    self._core_slab_points.pop()
                    self.update()
                return
            if button == Qt.LeftButton:
                raw_wx, raw_wy = self._screen_to_world(mx, my)
                sx, sy, _ = self._room_snap(raw_wx, raw_wy)
                self._core_slab_points.append((sx, sy))
                self.update()
            return
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
            # 그리기 중 아님 → 좌클릭으로 실 선택/해제 (Ctrl=다중 토글)
            if button == Qt.LeftButton:
                wx, wy = self._screen_to_world(mx, my)
                rid = self._hit_test_room(wx, wy)
                if ctrl:
                    if rid > 0:
                        if rid in self._selected_room_ids:
                            self._selected_room_ids.remove(rid)
                        else:
                            if (not self._selected_room_ids
                                    and self._selected_room_id > 0):
                                self._selected_room_ids = [int(self._selected_room_id)]
                            self._selected_room_ids.append(int(rid))
                        self._selected_room_id = (self._selected_room_ids[0]
                                                  if self._selected_room_ids else -1)
                        self.setFocus()
                        self.update()
                        self.room_selection_changed.emit(int(self._selected_room_id))
                    return
                self._selected_room_id = rid
                self._selected_room_ids = [int(rid)] if rid > 0 else []
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

        # ── Ctrl+클릭: 본체 부재 다중선택 토글 ──────────────────
        if ctrl:
            if hit > 0:
                comp = self._controller._scene.components.get(hit)
                # 본체(sub_index==0)만 다중선택 대상 — 종속 보/캔틸레버·개구부 제외.
                if comp is not None and int(getattr(comp, 'sub_index', 0) or 0) == 0:
                    if hit in self._selected_ids:
                        self._selected_ids.remove(hit)        # 이미 있으면 해제
                    else:
                        # 기존 단일선택이 목록에 없으면 seed 로 먼저 승계 후 추가.
                        if (not self._selected_ids and self._selected_id > 0
                                and self._selected_id != hit):
                            self._selected_ids = [int(self._selected_id)]
                        self._selected_ids.append(int(hit))
                    # seed(_selected_id) 동기화 — 목록 비면 해제.
                    self._selected_id = (self._selected_ids[0]
                                         if self._selected_ids else -1)
                    self._state = (STATE_SELECTED if self._selected_ids
                                   else STATE_IDLE)
                    self._direction = None
                    self._reference_edge = None
                    self._multi_align = False
                    self._hover_id = -1
                    self.setFocus()
                    self.update()
                    self.selection_changed.emit(int(self._selected_id))
            # Ctrl+빈클릭: 선택 유지(변화 없음)
            return

        if hit > 0:
            # 일반 클릭 → 단일선택으로 초기화
            self._selected_id = hit
            self._selected_ids = [int(hit)]
            self._multi_align = False
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
        # 수정키(Shift)는 뒤집기(Shift+X/Y) 판별에 쓰인다.
        self._process_key(event.key(), event.text(), int(event.modifiers()))
        event.accept()  # 2D 캔버스 키는 항상 소비 (QLabel 기본 동작 불필요)

    # ── UI 마이그레이션 M5-a — three.js 2D 키 입력 진입점 ──
    def handle_key(self, js_key: str, text: str, shift: bool = False) -> None:
        """three.js 2D 키 입력 — JS KeyboardEvent.key 문자열을 Qt.Key_* 로
        매핑해 기존 키 처리 분기를 그대로 재사용. shift=True 면 뒤집기(Shift+X/Y)
        판별용 ShiftModifier 비트를 함께 넘긴다."""
        qt_key = _JS_KEY_TO_QT.get((js_key or '').lower())
        if qt_key is not None:
            mods = Qt.ShiftModifier if shift else Qt.NoModifier
            self._process_key(qt_key, text, int(mods))

    def _process_key(self, key, text, modifiers=0):
        """키 처리 — vispy keyPressEvent 와 three.js handle_key 공유.
        key 는 Qt.Key_*, text 는 입력 문자, modifiers 는 Qt 수정키 비트(Shift 등)."""
        # 0) F5/F6 키는 항상 컨트롤러로 전달 (도크에 포커스가 있어도 토글 작동).
        if key in (Qt.Key_F5, Qt.Key_F6):
            self._controller.on_qt_key_press(text, key)
            return

        # 0a1) 수치 거리 이동 모드(스페이스바 진입) — 활성 시 키를 독점 처리.
        #   X/Y=축, 0~9·'-'=숫자 입력, Backspace=한 자 지움, Space/Enter=적용, Esc=취소.
        if self._num_active:
            self._handle_num_key(key, text)
            return

        # 0a1b) T 키 — 거리 측정 도구 토글(선 두 개 클릭 → 사이 거리). 부재 모드에서만.
        if (key == Qt.Key_T and self._edit_mode == 'component'
                and not self._f5_in_preview and self._import_preview is None):
            if self._measure_active:
                self._measure_active = False
                self._measure_pts = []
                self._measure_result = None
                self._num_set_panel('[IDLE]')
            else:
                self._measure_active = True
                self._measure_pts = []
                self._measure_result = None
                self._selected_id = -1
                self._selected_ids = []
                self._state = STATE_IDLE
                self._num_set_panel(
                    '[거리 측정: 첫 번째 선(부재 모서리) 클릭 — T 또는 Esc 로 종료]')
            self.update()
            return

        # 0a1c) 코어 슬래브 그리기 모드(P 키 진입) — 점 찍는 중엔 키 독점.
        if self._core_slab_draw_active:
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._finish_core_slab_draw()
                return
            if key == Qt.Key_Escape:
                self._cancel_core_slab_draw()
                return
            if key in (Qt.Key_Backspace, Qt.Key_Delete):
                if self._core_slab_points:
                    self._core_slab_points.pop()
                    self.update()
                return
            return  # 그리는 중 다른 키 무시
        # P 키 — 코어 슬래브 그리기 시작(부재 모드, 미리보기/측정/그리기 아닐 때).
        if (key == Qt.Key_P and self._edit_mode == 'component'
                and not self._f5_in_preview and not self._measure_active
                and self._import_preview is None):
            self.start_core_slab_draw()
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
            # 다중선택(2개+) 실 — M/C/R/Delete/뒤집기 묶음(기준 실 중심 공통 변환).
            if len(self._selected_room_ids) >= 2:
                if (modifiers & Qt.ShiftModifier) and key in (Qt.Key_X, Qt.Key_Y):
                    self._flip_rooms_multi('x' if key == Qt.Key_X else 'y')
                    return
                if key == Qt.Key_M:
                    self.begin_room_preview_multi('move')
                    return
                if key == Qt.Key_C:
                    self.begin_room_preview_multi('copy')
                    return
                if key == Qt.Key_R:
                    self._rotate_rooms_multi()
                    return
                if key in (Qt.Key_Delete, Qt.Key_Backspace):
                    self._delete_rooms_multi()
                    return
            # 선택 실 조작 — M 이동 / C 복사 / R 회전 / Shift+X·Y 뒤집기 / Del / Z / Esc
            if self._selected_room_id > 0:
                if (modifiers & Qt.ShiftModifier) and key in (Qt.Key_X, Qt.Key_Y):
                    self._flip_selected_room('x' if key == Qt.Key_X else 'y')
                    return
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
                    self._selected_room_ids = []
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

        # 2b-multi) 다중선택(2개+) + Delete → 합집합 통째 삭제(단일 undo).
        if (self._state == STATE_SELECTED and len(self._selected_ids) >= 2
                and key in (Qt.Key_Delete, Qt.Key_Backspace)):
            ctrl = self._controller
            if hasattr(ctrl, '_f5_on_delete_multi'):
                ctrl._f5_on_delete_multi(list(self._selected_ids))
                self.reset_state()
                return

        # 2b) F5 SELECTED + Delete → 삭제. 이동과 동일 대상으로 '자기 줄' 만 지운다:
        #     - 종속 부재(sub>0, 비코어): 같은 group_id+sub_index(모든 층).
        #     - 코어벽(core): 같은 group_id+xy(모든 층) 한 줄 — 다른 코어벽·슬래브는
        #       남는다.
        #     본체·코어슬래브는 기존처럼 그룹 통째 삭제.
        if (self._state == STATE_SELECTED and self._selected_id > 0
                and key in (Qt.Key_Delete, Qt.Key_Backspace)
                and self._f5_on_delete is not None):
            scene = self._controller._scene.components
            comp = scene.get(self._selected_id)
            if comp is not None:
                ctrl = self._controller
                sub = int(getattr(comp, 'sub_index', 0) or 0)
                ctv = getattr(getattr(comp, 'comp_type', None), 'value', '')
                line_only = (sub > 0 and ctv not in ('core', 'core_slab')) or (ctv == 'core')
                if line_only and hasattr(ctrl, '_f5_delete_member_line'):
                    ctrl._f5_delete_member_line(self._selected_id)
                    self.reset_state()
                    return
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
            else:
                # 일반 부재: 기존 흐름
                self._f5_on_type_key(ct)
            return

        # DEPENDENCY_PICK 상태 + Esc → 취소
        if self._state == STATE_DEPENDENCY_PICK and key == Qt.Key_Escape:
            self._f5_pending_dep_type = None
            self._state = STATE_IDLE
            self.update()
            return

        # 6) Esc 처리 — 정책 (b) 2026-05-12: 어떤 상태든 IDLE 로 완전 취소.
        # 옛 정책 (단계별 후퇴: REFERENCE→DIRECTION→SELECTED) 폐기.
        if key == Qt.Key_Escape:
            self.reset_state()
            return

        # 4) ALIGN_SELECTED → 방향 선택 / M 키 = 자유 이동(배치와 동일 흐름)
        if self._state == STATE_SELECTED and self._selected_id > 0:
            # 스페이스바 → 수치 거리 이동 모드 진입(이후 x/y 축 + 부호 숫자 입력).
            if key == Qt.Key_Space:
                self._num_active = True
                self._num_axis = None
                self._num_buf = ''
                self._num_update_text()
                self.update()
                return
            # 다중선택(2개+) 여부 — X/Y 정렬·M 이동·C 복사를 묶음 통째로 처리.
            _multi = len(self._selected_ids) >= 2
            # Shift+X(좌우)·Shift+Y(상하) — 거울 뒤집기. 개별·다중 모두. 정렬(X/Y)
            # 보다 먼저 가로채야 한다(같은 키라 순서 중요).
            if (modifiers & Qt.ShiftModifier) and key in (Qt.Key_X, Qt.Key_Y):
                axis = 'x' if key == Qt.Key_X else 'y'
                flip_ids = (list(self._selected_ids) if self._selected_ids
                            else [int(self._selected_id)])
                if hasattr(self._controller, '_f5_on_flip_key'):
                    self._controller._f5_on_flip_key(
                        int(self._selected_id), flip_ids, axis)
                return
            if key == Qt.Key_X:
                self._direction = 'X'
                self._multi_align = _multi   # 정렬 확정 시 묶음 통째 이동 분기용
                self._state = STATE_DIRECTION
                self.update()
                return
            if key == Qt.Key_Y:
                self._direction = 'Y'
                self._multi_align = _multi
                self._state = STATE_DIRECTION
                self.update()
                return
            # M 키: 자유 이동 모드 — 배치와 동일하게 마우스 따라 반투명 고스트 +
            # R/V 작동. 치수는 기존 부재 그대로 사용. 다중이면 합집합을 seed
            # (처음 클릭 부재) 기준으로 통째 이동(회전·앵커도 seed 기준).
            if key == Qt.Key_M:
                if _multi and hasattr(self._controller, '_f5_on_move_key_multi'):
                    self._controller._f5_on_move_key_multi(
                        int(self._selected_id), list(self._selected_ids))
                elif self._f5_on_move_key is not None:
                    self._f5_on_move_key(self._selected_id)
                return
            # C 키: 복사 — M 과 동일 흐름이되 원본 부재는 그대로 두고 새 부재를
            # 같은 위치에 만들어 마우스 따라 이동 후 클릭으로 위치 확정.
            if key == Qt.Key_C:
                if _multi and hasattr(self._controller, '_f5_on_copy_multi'):
                    self._controller._f5_on_copy_multi(
                        int(self._selected_id), list(self._selected_ids))
                elif self._f5_on_copy is not None:
                    self._f5_on_copy(self._selected_id)
                return
            # I 키:
            #  · 코어벽(단일/다중) 선택 → 선택한 벽들로 코어 슬래브 재정의
            #    (해당 벽들이 속한 그룹의 기존 코어 슬래브 제거 + 새 슬래브 1개 생성).
            #  · 내벽 선택 → 다음 클릭 모듈/패널에 수동 종속(기존).
            if key == Qt.Key_I:
                comp = self._controller._scene.components.get(self._selected_id)
                ctv = getattr(getattr(comp, 'comp_type', None), 'value', '')
                if ctv == 'core':
                    # 코어벽(단일/다중) 선택 + I → 다음 클릭한 코어 슬래브에 종속.
                    self._core_attach_ids = (list(self._selected_ids)
                                             if self._selected_ids
                                             else [int(self._selected_id)])
                    self._hover_id = -1
                    if (hasattr(self._controller, '_dim_panel')
                            and self._controller._dim_panel):
                        self._controller._dim_panel.set_mode_text(
                            '[코어벽 종속: 종속시킬 코어 슬래브를 클릭하세요 / Esc 취소]')
                    self.update()
                    return
                if comp is not None and ctv in ('interior_wall',
                                                'mid_column', 'mid_beam'):
                    # 내벽·중간기둥·중간보 — 다음 클릭한 부모(모듈 등)에 수동 종속.
                    self._wall_attach_id = int(self._selected_id)
                    self._hover_id = -1
                    if (hasattr(self._controller, '_dim_panel')
                            and self._controller._dim_panel):
                        msg = ('[중간부재 종속: 종속시킬 모듈을 클릭하세요 / Esc 취소]'
                               if ctv in ('mid_column', 'mid_beam')
                               else '[내벽 종속: 부모(모듈/패널)를 클릭하세요 / Esc 취소]')
                        self._controller._dim_panel.set_mode_text(msg)
                    self.update()
                return

        # (옛 중복 분기 제거 2026-05-12) REFERENCE+hover 1~5 키 처리는 위쪽
        # 분기에서 이미 잡혔으므로 본 자리 도달 불가.
        # 처리 안 된 키 — 2D 캔버스는 기본 동작 불필요하므로 무시.
        return

    # ── 수치 거리 이동 (스페이스바 → x/y → 부호 숫자 → Space/Enter) ──
    def _num_set_panel(self, msg: str):
        dp = getattr(self._controller, '_dim_panel', None)
        if dp is not None and hasattr(dp, 'set_mode_text'):
            dp.set_mode_text(msg)

    def _num_update_text(self):
        axis_txt = {0: 'X', 1: 'Y'}.get(self._num_axis, '축?(X/Y)')
        buf = self._num_buf if self._num_buf else '0'
        self._num_set_panel(
            f'[수치 이동: {axis_txt} {buf}mm — X/Y 축, 숫자/-, '
            f'Space·Enter 적용, Esc 취소]')

    def _handle_num_key(self, key, text):
        """수치 이동 모드 키 처리. _num_active 일 때 _process_key 가 위임."""
        if key == Qt.Key_Escape:
            self._num_active = False
            self._num_axis = None
            self._num_buf = ''
            self._num_set_panel('[IDLE]')
            self.update()
            return
        if key == Qt.Key_X:
            self._num_axis = 0
            self._num_update_text()
            return
        if key == Qt.Key_Y:
            self._num_axis = 1
            self._num_update_text()
            return
        if key == Qt.Key_Minus:
            # 부호 토글 — 위치 무관하게 앞 '-' 를 켜고 끈다.
            if self._num_buf.startswith('-'):
                self._num_buf = self._num_buf[1:]
            else:
                self._num_buf = '-' + self._num_buf
            self._num_update_text()
            return
        if key == Qt.Key_Backspace:
            if self._num_buf:
                self._num_buf = self._num_buf[:-1]
            self._num_update_text()
            return
        if Qt.Key_0 <= key <= Qt.Key_9:
            self._num_buf += chr(key)   # Qt.Key_0..9 == ord('0'..'9')
            self._num_update_text()
            return
        if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._num_apply()
            return
        # 그 외 키는 모드 중 무시.
        return

    def _num_apply(self):
        """입력한 부호 거리만큼 선택 부재(들)를 축 방향으로 평행이동."""
        # 축·숫자 미입력이면 적용 안 함(모드 유지).
        if self._num_axis is None or self._num_buf in ('', '-', '+'):
            return
        try:
            delta = float(self._num_buf)
        except ValueError:
            return
        axis = int(self._num_axis)
        ids = (list(self._selected_ids) if self._selected_ids
               else ([int(self._selected_id)] if self._selected_id > 0 else []))
        # 모드 종료(선택은 유지 — 반복 입력 가능).
        self._num_active = False
        self._num_axis = None
        self._num_buf = ''
        if not ids:
            self._num_set_panel('[IDLE]')
            return
        self._emit_move(ids, axis, float(delta))
        self._num_set_panel(f'[수치 이동 적용: {"XY"[axis]} {delta:+.0f}mm]')
        self.update()

    def _emit_move(self, ids, axis, delta):
        """선택 1개=move_requested, 2개+=move_multi_requested 로 평행이동 요청."""
        if len(ids) >= 2:
            self.move_multi_requested.emit(list(ids), int(axis), float(delta))
        else:
            self.move_requested.emit(int(ids[0]), int(axis), float(delta))

    # ── 거리 측정 도구 (T 키) ────────────────────────────────
    def _pick_any_edge(self, mx, my):
        """클릭 위치에서 가장 가까운 부재 외곽 모서리(선) 반환.

        반환 (axis, coord, wx, wy): axis 0=세로선(x=coord), 1=가로선(y=coord).
        wx,wy=클릭 월드좌표(연결선 중점용). 가까운 모서리 없으면 None.
        모서리 선분 범위(±여유) 안의 클릭만 후보로 본다.
        """
        wx, wy = self._screen_to_world(mx, my)
        scene = self._controller._scene.components
        tol_px = EDGE_PICK_PX * 2.5
        margin = 80.0   # 선분 끝 약간 바깥 클릭도 허용(mm)
        best = None     # (d_px, axis, coord)
        for cid in self._current_visible_ids():
            comp = scene.get(cid)
            if comp is None:
                continue
            try:
                x0, y0, x1, y1 = _xy_bbox(comp)
            except Exception:
                continue
            # (axis, coord, span_lo, span_hi)
            for axis, coord, lo, hi in ((0, x0, y0, y1), (0, x1, y0, y1),
                                        (1, y0, x0, x1), (1, y1, x0, x1)):
                along = wy if axis == 0 else wx
                if along < lo - margin or along > hi + margin:
                    continue
                d_world = abs(wx - coord) if axis == 0 else abs(wy - coord)
                d_px = d_world * self._zoom
                if best is None or d_px < best[0]:
                    best = (d_px, axis, coord)
        if best is not None and best[0] <= tol_px:
            return (best[1], best[2], wx, wy)
        return None

    def _measure_click(self, mx, my):
        """측정 모드 클릭 — 선 1개 찍고, 2개째에서 사이 거리 계산·표시."""
        edge = self._pick_any_edge(mx, my)
        if edge is None:
            self._num_set_panel('[거리 측정: 부재 모서리 가까이 클릭하세요]')
            return
        if len(self._measure_pts) != 1:
            # 0개 또는 2개(측정 끝남) → 새 측정 시작.
            self._measure_pts = [edge]
            self._measure_result = None
            self._num_set_panel('[거리 측정: 두 번째 선 클릭]')
            self.update()
            return
        e1, e2 = self._measure_pts[0], edge
        if e1[0] != e2[0]:
            # 방향이 다른 두 선(세로 vs 가로)은 평행 거리 정의가 안 됨 — 다시 받기.
            self._num_set_panel('[거리 측정: 같은 방향 선 2개(둘 다 세로/가로) — 두 번째 선 다시 클릭]')
            self.update()
            return
        dist = abs(e1[1] - e2[1])
        if e1[0] == 0:           # 세로선 → 가로 연결선(중점 y)
            ym = (e1[3] + e2[3]) / 2.0
            p1, p2 = [e1[1], ym], [e2[1], ym]
        else:                    # 가로선 → 세로 연결선(중점 x)
            xm = (e1[2] + e2[2]) / 2.0
            p1, p2 = [xm, e1[1]], [xm, e2[1]]
        self._measure_pts = [e1, e2]
        self._measure_result = {'p1': p1, 'p2': p2, 'dist': float(dist)}
        self._num_set_panel(
            f'[거리: {dist:.0f} mm — 새 측정=선 클릭, 종료=T·Esc]')
        self.update()

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

        if self._multi_align and len(self._selected_ids) >= 2:
            # 묶음 통째 정렬 — 합집합 전체를 같은 delta 로 평행이동(상대 배치 유지).
            self.move_multi_requested.emit(
                list(self._selected_ids), axis, float(delta))
        else:
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
        self._canvas.move_multi_requested.connect(self._apply_move_multi)

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
        else:
            pass
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
        sub = getattr(comp, 'sub_index', 0)
        delta_vec = np.zeros(3)
        delta_vec[axis] = delta

        # 함정: 키 이동(X/Y 정렬)과 마우스 자유 이동의 대상 집합이 달라, 종속
        # 부재나 코어벽을 키로 옮기면 group_id 전체가 끌려오는 버그가 있었다.
        # 마우스 이동과 동일하게 _resolve_move_targets 로 '자기 줄' 만 움직인다:
        #  - 종속 부재(sub>0, 비코어): 같은 group_id + 같은 sub_index(모든 층).
        #  - 코어벽(CORE): 같은 group_id + 같은 xy(모든 층) 한 줄 — 다른 코어벽·
        #    슬래브는 제자리.
        # 본체·코어슬래브·레거시는 기존 경로(translate_group, 부수효과 보존).
        from modular_3d.model import ComponentType as _CT
        _ct = comp.comp_type
        _is_core = _ct in (_CT.CORE, _CT.CORE_SLAB)
        _line_only = (sub > 0 and not _is_core) or (_ct == _CT.CORE)
        if gid > 0 and _line_only and hasattr(ctrl, '_resolve_move_targets'):
            for cid in ctrl._resolve_move_targets(comp_id):
                c = ctrl._scene.components.get(cid)
                if c is None:
                    continue
                c.position = c.position + delta_vec
                c.generate_sub_components()
                ctrl._viewer.remove_component_visual(cid)
                v, f, cl = build_component_mesh(c)
                ctrl._viewer.add_component_visual(cid, v, f, cl)
                ctrl._snap.remove_component(cid)
                ctrl._snap.add_component(cid, c)
        elif gid > 0:
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
        else:
            # 그룹 미부여(레거시 부재) — 단일 부재만 이동
            comp.position = comp.position + delta_vec
            comp.generate_sub_components()
            ctrl._viewer.remove_component_visual(comp_id)
            v, f, cl = build_component_mesh(comp)
            ctrl._viewer.add_component_visual(comp_id, v, f, cl)
            ctrl._snap.remove_component(comp_id)
            ctrl._snap.add_component(comp_id, comp)

        ctrl._status.update_count(ctrl._scene.component_count)
        # X/Y 정렬·스냅으로 코어벽이 이동했으면 슬래브 자동 재생성(코어 변경 시에만).
        if hasattr(ctrl, '_auto_regen_core_slabs'):
            ctrl._auto_regen_core_slabs()
        self._canvas.update()

    def _apply_move_multi(self, ids, axis: int, delta: float):
        """다중선택 묶음 통째 정렬 — 선택 부재들의 합집합을 (axis,delta)만큼 평행이동.

        각 선택 부재를 `_resolve_move_targets` 로 '자기 줄/그룹'까지 확장한 합집합을
        cid 중복 없이 모은 뒤, 같은 delta 로 평행이동(상대 배치 유지, 회전 없음).
        단일 정렬(_apply_move)과 동일하게 undo 는 남기지 않는다(정렬 정책).
        """
        ctrl = self._controller
        if not hasattr(ctrl, '_resolve_move_targets'):
            return
        from modular_3d.render.mesh_builder import build_component_mesh

        # 합집합 — 각 선택 부재의 이동 대상(전 층/종속/코어 줄)을 모아 dedup.
        target_ids: list = []
        seen: set = set()
        for sid in ids:
            for cid in ctrl._resolve_move_targets(int(sid)):
                if cid not in seen:
                    seen.add(cid)
                    target_ids.append(cid)
        if not target_ids:
            return

        delta_vec = np.zeros(3)
        delta_vec[axis] = float(delta)
        for cid in target_ids:
            c = ctrl._scene.components.get(cid)
            if c is None:
                continue
            c.position = c.position + delta_vec
            c.generate_sub_components()
            ctrl._viewer.remove_component_visual(cid)
            v, f, cl = build_component_mesh(c)
            ctrl._viewer.add_component_visual(cid, v, f, cl)
            ctrl._snap.remove_component(cid)
            ctrl._snap.add_component(cid, c)

        ctrl._status.update_count(ctrl._scene.component_count)
        if hasattr(ctrl, '_auto_regen_core_slabs'):
            ctrl._auto_regen_core_slabs()
        self._canvas.update()
