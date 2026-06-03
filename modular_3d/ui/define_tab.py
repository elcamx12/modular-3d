"""모듈 정의 탭 — 컴포넌트(모듈/패널/수직모듈 + 종속부재)를 만들어 라이브러리에 저장.

[정책 2026-05-24 디자인 탭 2분리 — 1단계 골격]
- 배치 설계 탭과 완전히 분리된 별도 작업공간. 자체 Scene·Viewer3D·Controller·
  2D 캔버스·팔레트·치수입력·속성 패널을 내부에 인스턴스로 둔다(기존 클래스
  그대로 재사용 — 동작을 배치 탭과 최대한 동일하게).
- 우측에 정의 라이브러리 패널(목록 + 저장/불러오기/새로/삭제). 저장은 현재
  작업공간(Scene)을 이름 붙여 라이브러리에 보관(세션 메모리 골격).
- 실·개구부·벽·배치 탭 통합은 다음 단계(디자인탭_분리_계획서.md 참조).

[이벤트] 자체 3D 캔버스에 이벤트 필터를 직접 설치해 키 입력을 자체 2D 캔버스로
라우팅한다(배치 탭의 main_3d.eventFilter 와 동일 로직).
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel, QGroupBox,
    QListWidget, QLineEdit, QPushButton, QMessageBox, QCheckBox,
)

from modular_3d.model import Scene
from modular_3d.render.viewer import Viewer3D
# UI 마이그레이션 M2 — Strangler 어댑터. 정의 탭도 배치 탭과 같은 패턴.
from modular_3d.render.viewer_strangler import ViewerStrangler
from modular_3d.ui.ui_panel import DimensionInputPanel
from modular_3d.ui.alignment.alignment_view import AlignmentDockPanel
# UI 마이그레이션 M3-b — three.js 2D 평면 뷰 어댑터.
from modular_3d.ui.alignment.alignment_dock_three import AlignmentDockPanelThree
from modular_3d.ui.controls import Controller
from modular_3d.ui.palette_panel import PalettePanel
from modular_3d.ui.design_props_panel import DesignPropertiesPanel
from modular_3d.ui.room_props_panel import RoomPropertiesPanel
from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded


# ── 종합탭 톤 디자인 토큰 ─────────────────────────────────
_PAGE_BG     = "#EDF2F7"
_CARD_BG     = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEAD_FG     = "#1F4E79"
_BODY_FG     = "#1F2A37"
_SUB_FG      = "#5B6573"
_ACCENT      = "#1F4E79"
_ACCENT_HOV  = "#163A5E"
_ACCENT_SOFT = "#F5F8FF"

# 라이브러리 패널 전용 스타일시트(이 박스에만 적용 — 공유 팔레트/속성 패널과 분리).
# - GroupBox 타이틀 / QLabel(묻는 글) → Paperlogy
# - QListWidget·QLineEdit(값·입력) → Freesentation, 흰 카드 톤
# - QPushButton → Freesentation 둥근 카드형
_LIB_QSS = (
    "QGroupBox#libBox {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 16px; font-weight: 800; color: {_HEAD_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 10px; margin-top: 14px;"
    " padding: 14px 12px 12px 12px; }"
    "QGroupBox#libBox::title { subcontrol-origin: margin; left: 12px;"
    f" padding: 0 6px; background: {_CARD_BG};"
    " }"
    "QGroupBox#libBox QLabel {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 14px; color: {_BODY_FG}; background: transparent;"
    " }"
    "QGroupBox#libBox QLineEdit, QGroupBox#libBox QListWidget {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 14px; color: {_BODY_FG};"
    f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
    " background: white; padding: 4px 6px; }"
    # 목록 항목(item)에도 명시 — 뷰에 준 폰트가 항목 델리게이트에 전파되지
    # 않는 Qt 동작 보완.
    "QGroupBox#libBox QListWidget::item {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 14px; color: {_BODY_FG}; padding: 2px 2px; }}"
    f"QGroupBox#libBox QListWidget::item:selected {{"
    f" background: {_ACCENT_SOFT}; color: {_HEAD_FG}; }}"
    "QGroupBox#libBox QPushButton {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 14px; font-weight: 700; color: {_BODY_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 8px; padding: 7px 10px; }"
    f"QGroupBox#libBox QPushButton:hover {{ background: {_ACCENT_SOFT};"
    f" border-color: {_ACCENT}; color: {_HEAD_FG}; }}"
)


class DefineTab(QWidget):
    """모듈 정의 탭 — 독립 작업공간 + 정의 라이브러리."""

    def __init__(self, status_mgr, library, parent=None):
        super().__init__(parent)
        # 공유: 상태바 매니저 + 정의 라이브러리를 메인과 공유(배치 탭에서도 접근).
        # 나머지(Scene·Viewer·Controller 등)는 전부 자체.
        self._status_mgr = status_mgr
        self._library = library

        # ── 자체 모델·뷰·컨트롤러 ─────────────────────────
        # UI 마이그레이션 M2 — 정의 탭도 독립 ViewerStrangler. 배치 탭과 격리.
        self._scene = Scene()
        self._viewer = ViewerStrangler()
        self._canvas_widget = self._viewer.vispy.get_native_widget()
        self._canvas_widget.setFocusPolicy(Qt.StrongFocus)
        self._three_widget = self._viewer.three.get_native_widget()
        self._dim_panel = DimensionInputPanel()
        self._props = DesignPropertiesPanel(self._scene)
        self._room_props = RoomPropertiesPanel()
        self._controller = Controller(
            self._viewer, self._dim_panel, self._status_mgr, self._scene,
            analysis_panel=None, analysis_dock=None,
        )
        # [정의 탭 단일층] 정의는 '건물 층 복제 없는 1개 인스턴스' 다. 단일층
        # 모드를 켜면 배치 시 1층 1세트만 남고(수직3층모듈은 3층 높이 1개),
        # 층수 입력 변경에 따른 다층 복제도 비활성된다.
        self._controller.single_floor_mode = True
        # 2D 캔버스 패널
        self._f5_panel = AlignmentDockPanel(self._controller)
        # UI 마이그레이션 M3-b — three.js 2D 평면 뷰 (vispy 옆 세로 분할).
        # AlignmentCanvas.paintEvent 끝 후크에 자기 sync 등록.
        self._f5_panel_three = AlignmentDockPanelThree()
        self._f5_panel_three.attach_to(self._f5_panel.canvas)
        if hasattr(self._controller, 'set_f5_dock'):
            self._controller.set_f5_dock(None, self._f5_panel)
        # 단일층 작업공간이므로 층수 입력 컨트롤을 숨긴다.
        if hasattr(self._f5_panel, 'set_floors_control_visible'):
            self._f5_panel.set_floors_control_visible(False)
        # 캔버스 선택 변경 → 속성 패널 갱신
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'selection_changed'):
            canvas.selection_changed.connect(self._props.refresh_selected)
        # 실 선택 변경 → 실 속성 패널 갱신 + 패널 전환
        if canvas is not None and hasattr(canvas, 'room_selection_changed'):
            canvas.room_selection_changed.connect(self._on_room_selected)
        # 실 편집/삭제 → 컨트롤러
        self._room_props.room_edited.connect(self._on_room_edited)
        self._room_props.room_delete_requested.connect(self._on_room_delete)
        # 보 단면 타입(각형강관/H형강) 변경 → 컨트롤러
        if hasattr(self._controller, '_set_beam_section_type'):
            self._props.beam_section_changed.connect(
                self._controller._set_beam_section_type)
        # (2026-05-24) AI 최적배치 제거 — 속성 패널 ↔ F5 층수 연동 불필요.
        # 층수는 F5 패널 상단 SpinBox 가 단일 컨트롤(controller 직접 연결).

        # 좌측 팔레트
        self._palette = PalettePanel(
            on_select=self._on_palette_select,
            on_room_draw=self._on_room_draw,
            on_detail_mode_toggle=self._on_detail_mode_toggle,
            on_opening_add=self._on_opening_add,
            on_wall_place=self._on_wall_place)
        self._palette.setFixedWidth(220)
        self._props.setFixedWidth(320)

        # 정의 라이브러리는 생성자에서 주입(메인과 공유) — 위에서 self._library 설정.

        # ── 레이아웃 ──────────────────────────────────────
        self._build_ui()
        # 시작 시 영구 저장된 정의 목록 표시(파일에서 불러온 항목 포함).
        self._refresh_lib_list()

        # 이벤트 필터 — 자체 3D 캔버스
        self._canvas_widget.installEventFilter(self)
        self._canvas_widget.setMouseTracking(True)

    # ── UI ────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # [2026-06-02 디자인 통일] 종합탭 톤 — 페이지 배경 + 패널 간격·여백 확대.
        ensure_fonts_loaded()
        # 배치 설계 탭과 동일하게 QWidget 캐스케이드로 페이지 전체를 옅은 하늘색으로.
        # [중요] QWidget 을 상속한 클래스(DefineTab)는 WA_StyledBackground 를 켜야
        # 스타일시트의 background 가 자기 표면에 칠해진다. 이게 없으면 좌/상 여백·
        # 속성 패널 주변 등 노출된 DefineTab 표면이 윈도우 기본 회색으로 비친다.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget { background: #EDF2F7; }"
            "QWidget#defineCardPane { background: #FFFFFF;"
            " border: 1px solid #DDE4ED; border-radius: 10px; }"
            + _LIB_QSS)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 12, 12, 12)
        h.setSpacing(12)

        # 좌: 팔레트
        h.addWidget(self._palette)

        # 중앙: 3D 카드 | 2D 카드 — 배치 설계 탭과 동일하게 두 개의 흰색 카드로 분리.
        # (이전엔 하나의 카드 안에 3D·2D를 함께 넣었으나, 배치 설계 탭처럼
        #  좌 3D·우 2D를 각각 독립 카드로 감싼다. 내부 split·위젯·시그널 동일.)
        _card_qss = (
            "QWidget#defineCardPane { background: #FFFFFF;"
            " border: 1px solid #DDE4ED; border-radius: 10px; }"
        )
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(8)
        split = QSplitter(Qt.Horizontal)
        # UI 마이그레이션 M8-b — 정의 탭도 three.js 단독 표시.
        #  · 좌 3D 카드: vispy 자리(_canvas_widget) 0 크기, three.js 전체.
        left_pane = QWidget()
        left_pane.setObjectName("defineCardPane")
        left_pane.setStyleSheet(_card_qss)
        left_lay = QVBoxLayout(left_pane)
        left_lay.setContentsMargins(8, 8, 8, 8)
        left_lay.setSpacing(6)
        self._left_split = QSplitter(Qt.Vertical)
        self._left_split.addWidget(self._canvas_widget)
        self._left_split.addWidget(self._three_widget)
        self._left_split.setStretchFactor(0, 0)
        self._left_split.setStretchFactor(1, 1)
        self._left_split.setSizes([0, 800])
        self._left_split.setChildrenCollapsible(True)
        left_lay.addWidget(self._left_split, stretch=1)
        # [2026-06-03] 벽·실 표시 토글 — 배치설계 탭과 동일하게 3D 뷰 아래에 배치.
        # 체크박스 하나로 벽 채움면(벽패널·내벽·모듈 외피 벽)과 실 색면을 함께
        # 끄고 켠다. 프레임·슬래브·코어벽은 항상 표시. 기본 ON.
        self._wall_room_check = QCheckBox('벽·실 표시')
        self._wall_room_check.setChecked(True)
        self._wall_room_check.setToolTip(
            '벽 채움면(벽패널 채움·내벽·모듈 외피 벽)과 실 표기를 함께 끄거나 '
            '켭니다. 프레임·슬래브·코어벽은 항상 표시됩니다.')
        self._wall_room_check.toggled.connect(self._on_wall_room_toggled)
        left_lay.addWidget(self._wall_room_check)
        split.addWidget(left_pane)
        #  · 우 2D 카드: _f5_panel 은 상단 컨트롤만 얇게(hide_canvas), three.js 가 평면뷰 전담.
        right_pane = QWidget()
        right_pane.setObjectName("defineCardPane")
        right_pane.setStyleSheet(_card_qss)
        right_lay = QVBoxLayout(right_pane)
        right_lay.setContentsMargins(8, 8, 8, 8)
        right_lay.setSpacing(6)
        self._right_split = QSplitter(Qt.Vertical)
        self._right_split.addWidget(self._f5_panel)
        self._right_split.addWidget(self._f5_panel_three)
        self._right_split.setStretchFactor(0, 0)
        self._right_split.setStretchFactor(1, 1)
        # [2026-06-02] 모듈 정의 탭은 층수 컨트롤이 숨겨져 상단 띠가 거의 비어
        # 하늘색 빈 칸처럼 보였다 → _f5_panel 인스턴스 높이를 컨트롤 한 줄(저장/
        # 불러오기)만큼으로 제한해 띠를 얇게, 버튼은 맨 위에 오게 한다.
        # (공유 클래스 AlignmentDockPanel 은 그대로 — 이 인스턴스에만 적용되어
        #  배치 설계 탭에는 영향 없음.)
        self._f5_panel.setMaximumHeight(34)
        self._right_split.setSizes([34, 1000])
        self._right_split.setChildrenCollapsible(True)
        if hasattr(self._f5_panel, 'hide_canvas'):
            self._f5_panel.hide_canvas()
        right_lay.addWidget(self._right_split, stretch=1)
        split.addWidget(right_pane)
        split.setStretchFactor(0, 7)
        split.setStretchFactor(1, 3)
        split.setSizes([700, 300])
        split.setChildrenCollapsible(False)
        cv.addWidget(split, stretch=1)
        cv.addWidget(self._dim_panel)
        h.addWidget(center, stretch=1)

        # 우: 속성(부재/실 전환) + 라이브러리
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(self._props)
        rv.addWidget(self._room_props)
        self._room_props.setVisible(False)   # 실 선택 시에만 표시
        rv.addWidget(self._build_library_panel(), stretch=1)
        h.addWidget(right)

    def _build_library_panel(self) -> QWidget:
        box = QGroupBox("컴포넌트 라이브러리")
        box.setObjectName("libBox")   # _LIB_QSS 스코프 대상
        v = QVBoxLayout(box)
        self._lib_list = QListWidget()
        # QSS 폰트가 항목에 전파 안 되는 경우 대비 — 위젯 폰트 직접 지정(Freesentation).
        _lib_font = QFont(F_BODY, 11)
        self._lib_list.setFont(_lib_font)
        self._lib_name_font = _lib_font   # 이름 입력칸과 공유
        v.addWidget(self._lib_list, stretch=1)
        # 이름 입력
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("이름:"))
        self._lib_name = QLineEdit()
        self._lib_name.setFont(_lib_font)
        self._lib_name.setPlaceholderText("예: 표준모듈A")
        name_row.addWidget(self._lib_name, stretch=1)
        v.addLayout(name_row)
        # 버튼
        btn_row = QHBoxLayout()
        b_save = QPushButton("저장")
        b_load = QPushButton("불러오기")
        b_new = QPushButton("새로")
        b_del = QPushButton("삭제")
        b_save.clicked.connect(self._on_lib_save)
        b_load.clicked.connect(self._on_lib_load)
        b_new.clicked.connect(self._on_lib_new)
        b_del.clicked.connect(self._on_lib_delete)
        for b in (b_save, b_load, b_new, b_del):
            btn_row.addWidget(b)
        v.addLayout(btn_row)
        # 건물 파일(scene.json)을 작업공간으로 — 배치/접합부까지 저장한 건물을
        # 정의 탭에서 열어 필요한 모듈만 남기고 다시 정의로 저장하기 위함.
        b_file = QPushButton("건물 파일에서 불러오기")
        b_file.setToolTip(
            "배치 설계·접합부 설계 탭에서 저장한 건물 파일을 작업공간으로 엽니다. "
            "필요 없는 부재를 지우고 남긴 한 부재를 정의로 저장할 수 있습니다.")
        b_file.clicked.connect(self._on_lib_load_from_file)
        v.addWidget(b_file)
        hint = QLabel("현재 작업공간을 이름 붙여 저장 → 목록에서 선택해 불러오기")
        hint.setStyleSheet(
            f"font-family:'{F_HEAD}','Malgun Gothic',sans-serif;"
            f" font-size:12px; color:{_SUB_FG}; background:transparent;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return box

    # ── 상세 설계 모드/실 지정/개구부 (2026-05-30 단일 모드 통합) ──
    def _on_detail_mode_toggle(self, checked: bool):
        """'상세 설계' 토글 → 캔버스 모드 정리(세부 모드는 각 버튼이 전환)."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'set_edit_mode'):
            canvas.set_edit_mode('component')
        if not checked:
            self._show_room_props(None)

    def _on_room_draw(self):
        """'실 지정' → 실 편집 모드 진입 + 실 그리기 시작."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is None:
            return
        if hasattr(canvas, 'set_edit_mode'):
            canvas.set_edit_mode('room')
        if hasattr(canvas, 'start_room_draw'):
            canvas.start_room_draw()

    def _on_wall_room_toggled(self, checked: bool) -> None:
        """'벽·실 표시' 체크박스 → 벽 채움면 + 실 색면 동시 ON/OFF."""
        if hasattr(self._controller, 'set_wall_fill_visible'):
            self._controller.set_wall_fill_visible(checked)
        if hasattr(self._controller, 'set_rooms_visible'):
            self._controller.set_rooms_visible(checked)

    def _on_opening_add(self):
        """'개구부' → 개구부 모드 진입 후 벽/슬래브 클릭으로 배치."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'start_opening_add'):
            canvas.start_opening_add()

    # ── 내벽 배치 (2026-05-24) ────────────────────────────
    def _on_wall_place(self):
        """'벽' → 부재 배치 머신으로 내벽 종속 배치 시작.

        정의 탭은 단일층 모드 — 배치 후 floor_index>0 은 자동 제거되어
        1층 1세트만 정의에 남는다. 머신은 component 모드에서만 라우팅되므로
        캔버스를 component 로 전환한다(상세 설계 토글은 유지).
        """
        from modular_3d.model import ComponentType
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is None:
            return
        if hasattr(canvas, 'set_edit_mode'):
            canvas.set_edit_mode('component')
        if hasattr(canvas, 'handle_palette_select'):
            canvas.handle_palette_select(ComponentType.INTERIOR_WALL)

    def _on_room_selected(self, room_id: int):
        room = self._scene.rooms.get(room_id) if room_id > 0 else None
        self._show_room_props(room)

    def _show_room_props(self, room):
        """실 선택 시 실 패널 표시(부재 패널 숨김), 해제 시 반대."""
        if room is not None:
            self._props.setVisible(False)
            self._room_props.setVisible(True)
            self._room_props.set_room(room)
        else:
            self._room_props.clear()
            self._room_props.setVisible(False)
            self._props.setVisible(True)

    def _on_room_edited(self, room_id: int, room_type: str,
                        live_load: float = -1.0, sdl: float = -1.0):
        if hasattr(self._controller, '_update_room'):
            self._controller._update_room(room_id, room_type, live_load, sdl)

    def _on_room_delete(self, room_id: int):
        if hasattr(self._controller, '_delete_room'):
            self._controller._delete_room(room_id)
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'clear_room_selection'):
            canvas.clear_room_selection()
        self._show_room_props(None)

    # ── 팔레트 선택 (배치 탭과 동일 경로) ────────────────
    def _on_palette_select(self, comp_type):
        if comp_type is None:
            self._props.refresh_active(None)
            return
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'handle_palette_select'):
            canvas.handle_palette_select(comp_type)
        self._props.refresh_active(comp_type)

    # ── 라이브러리 동작 ───────────────────────────────────
    def _refresh_lib_list(self) -> None:
        self._lib_list.clear()
        for name in self._library.list_names():
            d = self._library.get(name)
            label = name
            if d is not None and d.type_summary:
                label = f"{name}  ({d.type_summary})"
            self._lib_list.addItem(label)
        # itemData 대신 표시 라벨에서 이름 복원이 어려우니 별도 매핑 보관
        self._lib_row_names = self._library.list_names()

    def _selected_lib_name(self) -> str | None:
        row = self._lib_list.currentRow()
        names = getattr(self, '_lib_row_names', [])
        if 0 <= row < len(names):
            return names[row]
        return None

    def _on_lib_save(self) -> None:
        name = self._lib_name.text().strip() or self._selected_lib_name() or ""
        if not name:
            QMessageBox.information(self, "정의 저장", "정의 이름을 입력하세요.")
            return
        if not self._scene.components:
            QMessageBox.information(self, "정의 저장", "작업공간이 비어 있습니다.")
            return
        try:
            # [정의 탭 단일층] 정의는 항상 1층(1세트)으로 저장. 작업공간에는
            # 이미 floor_index==0 부재만 있으므로 n_floors=1 로 직렬화한다.
            self._library.add_from_scene(name, self._scene, n_floors=1)
        except Exception as e:
            QMessageBox.warning(self, "정의 저장 실패", str(e))
            return
        self._refresh_lib_list()
        self._dim_panel.set_mode_text(f"[정의 저장: {name}]")

    def _on_lib_load(self) -> None:
        name = self._selected_lib_name()
        if not name:
            QMessageBox.information(self, "불러오기", "목록에서 정의를 선택하세요.")
            return
        d = self._library.get(name)
        if d is None:
            return
        try:
            new_scene, n_floors = d.to_scene()
        except Exception as e:
            QMessageBox.warning(self, "불러오기 실패", str(e))
            return
        self._load_scene_into_workspace(new_scene, n_floors)
        self._lib_name.setText(name)
        self._dim_panel.set_mode_text(f"[정의 불러옴: {name}]")

    def _on_lib_load_from_file(self) -> None:
        """건물 파일(scene.json)을 작업공간으로 불러온다 — 배치/접합부 설계 탭의
        저장과 같은 양식. 접합 변경(joint_overrides)도 함께 복사된다(정의로 다시
        저장할 때는 단일 부재라 접합 정보는 자연히 빠진다)."""
        from PyQt5.QtWidgets import QFileDialog
        from modular_3d.io.scene_io import load_scene
        path, _ = QFileDialog.getOpenFileName(
            self, "건물 파일 불러오기", "", "JSON (*.json)")
        if not path:
            return
        try:
            new_scene, n_floors = load_scene(path)
        except Exception as e:
            QMessageBox.warning(self, "불러오기 실패", str(e))
            return
        self._load_scene_into_workspace(new_scene, n_floors)
        self._lib_name.clear()
        self._dim_panel.set_mode_text(f"[건물 파일 불러옴: {n_floors}층]")

    def _on_lib_new(self) -> None:
        self._clear_workspace()
        self._lib_name.clear()
        self._dim_panel.set_mode_text("[새 정의]")

    def _on_lib_delete(self) -> None:
        name = self._selected_lib_name()
        if not name:
            return
        if self._library.remove(name):
            self._refresh_lib_list()

    # ── 작업공간 비우기/채우기 (배치 탭 불러오기와 동일 패턴) ──
    def _clear_workspace(self) -> None:
        ctrl = self._controller
        # 3D 실 색면 먼저 제거(rooms 비우기 전에).
        if hasattr(ctrl, '_clear_rooms_3d'):
            ctrl._clear_rooms_3d()
        for cid in list(ctrl._scene.components.keys()):
            ctrl._viewer.remove_component_visual(cid)
            ctrl._snap.remove_component(cid)
        ctrl._scene.components.clear()
        ctrl._scene.undo_stack.clear()
        ctrl._scene.rooms.clear()
        if self._f5_panel is not None and hasattr(self._f5_panel, 'canvas'):
            self._f5_panel.canvas.update()
        self._status_mgr.update_count(0)

    def _load_scene_into_workspace(self, new_scene: Scene, n_floors: int) -> None:
        from modular_3d.render.mesh_builder import build_component_mesh
        ctrl = self._controller
        # 비우기 — 부재 + 실(3D 색면 포함)
        if hasattr(ctrl, '_clear_rooms_3d'):
            ctrl._clear_rooms_3d()
        for cid in list(ctrl._scene.components.keys()):
            ctrl._viewer.remove_component_visual(cid)
            ctrl._snap.remove_component(cid)
        ctrl._scene.components.clear()
        ctrl._scene.undo_stack.clear()
        ctrl._scene.rooms.clear()
        # 재등록 (id 재발급은 add_component 가 처리)
        for _old_id, comp in new_scene.components.items():
            new_id = ctrl._scene.add_component(comp)
            v, f, c = build_component_mesh(comp)
            ctrl._viewer.add_component_visual(new_id, v, f, c)
            ctrl._snap.add_component(new_id, comp)
        ctrl._scene.undo_stack.clear()
        # 실 재등록 + 3D 렌더
        for room in getattr(new_scene, 'rooms', {}).values():
            ctrl._scene.add_room(room)
        if hasattr(ctrl, '_render_all_rooms_3d'):
            ctrl._render_all_rooms_3d()
        # 접합 오버라이드 복사 + 불러오기 질문 플래그(접합 탭 진입 시 선택).
        ctrl._scene.joint_overrides = list(
            getattr(new_scene, 'joint_overrides', []) or [])
        ctrl._scene._joint_overrides_pending_choice = bool(
            ctrl._scene.joint_overrides)
        if self._f5_panel is not None:
            self._f5_panel.set_floors(n_floors)
            if hasattr(self._f5_panel, 'canvas'):
                self._f5_panel.canvas.update()
        self._status_mgr.update_count(ctrl._scene.component_count)

    # ── 탭 진입 시 포커스 ─────────────────────────────────
    def on_enter(self) -> None:
        """main_3d 가 정의 탭 진입 시 호출 — 캔버스에 포커스."""
        self._canvas_widget.setFocus()

    # ── 이벤트 필터 (자체 3D 캔버스) — 배치 탭과 동일 로직 ──
    def eventFilter(self, obj, event):
        if obj is not self._canvas_widget:
            return super().eventFilter(obj, event)
        etype = event.type()
        try:
            if etype == QEvent.KeyPress:
                qt_key = event.key()
                if qt_key in (Qt.Key_F5, Qt.Key_F6):
                    return True
                # 키 입력은 2D 캔버스로 라우팅
                self._f5_panel.canvas.keyPressEvent(event)
                return True
            elif etype == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    return True   # 3D 좌클릭(선택/배치) 비활성
                pos = (event.x(), event.y())
                self._controller.on_qt_mouse_press(event.button(), pos)
                return False
            elif etype == QEvent.MouseButtonRelease:
                if event.button() == Qt.MiddleButton:
                    self._controller.refresh_after_camera()
                return False
            elif etype == QEvent.MouseMove:
                return False
        except Exception as e:
            print(f'[DEFINE EVENT ERROR] {etype}: {e}')
        return False


__all__ = ["DefineTab"]
