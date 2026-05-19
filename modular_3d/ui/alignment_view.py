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
from modular_3d.ui.alignment_helpers import (
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
from modular_3d.ui.alignment_paint import AlignmentCanvasPaintMixin
from modular_3d.ui.alignment_pick import AlignmentCanvasPickMixin


# ── 캔버스 ─────────────────────────────────────────────────
class AlignmentCanvas(QLabel, AlignmentCanvasPaintMixin, AlignmentCanvasPickMixin):
    """2D 평면뷰 캔버스."""

    move_requested = pyqtSignal(int, int, float)  # (comp_id, axis, delta)
    # [2026-05-11] 선택 부재 변경 통지 — 우측 디자인 속성 패널 갱신용
    selection_changed = pyqtSignal(int)            # comp_id (선택 해제 = -1)

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

    def set_placement_callback(self, on_type_key=None,
                               on_canvas_click=None, on_escape=None,
                               on_rotate=None, on_anchor=None,
                               on_canvas_move=None, on_delete=None,
                               on_select=None,
                               on_dependency_pick=None,
                               on_move_key=None,
                               on_undo=None,
                               on_copy=None):
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

        if event.button() != Qt.LeftButton:
            return

        mx, my = event.pos().x(), event.pos().y()

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

        # F5 PREVIEW: 마우스 따라 좌측 3D 고스트 + 2D 캔버스 고스트 갱신
        if self._f5_in_preview and self._f5_on_canvas_move is not None:
            mx, my = event.pos().x(), event.pos().y()
            raw_wx, raw_wy = self._screen_to_world(mx, my)
            wx, wy, snap_p = self._f5_world_snap(raw_wx, raw_wy)
            self._f5_mouse_world = (wx, wy)
            # 스냅 마커 표시용 저장(paintEvent에서 그림)
            self._f5_snap_point = snap_p
            self._f5_on_canvas_move(wx, wy)
            self.update()
            return

        if self._state == STATE_REFERENCE:
            mx, my = event.pos().x(), event.pos().y()
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
        key = event.key()

        # 0) F5/F6 키는 항상 컨트롤러로 전달 (도크에 포커스가 있어도 토글 작동).
        # 이벤트 소비(accept)로 부모 위젯/메인 윈도우로 전파되어 이중 토글되는 것 방지.
        if key in (Qt.Key_F5, Qt.Key_F6):
            ctrl = self._controller
            ctrl.on_qt_key_press(event.text(), key)
            event.accept()
            return

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

        super().keyPressEvent(event)

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
        top_row.addWidget(QLabel('층수:'))
        # 층수는 3 의 배수만 허용 (수직 3층 모듈이 한 번에 3개 층을 차지하므로
        # 단층 모듈과 섞어 쓸 때 칸마다 빠짐없이 채워지려면 N 이 3 의 배수여야 함).
        self._floors_spin = QSpinBox()
        self._floors_spin.setRange(3, 24)
        self._floors_spin.setSingleStep(3)
        self._floors_spin.setValue(3)
        self._floors_spin.setFixedWidth(60)
        self._floors_spin.valueChanged.connect(self._on_floors_changed)
        top_row.addWidget(self._floors_spin)
        top_row.addWidget(QLabel('층  (3의 배수)'))
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
        self._canvas.update()
