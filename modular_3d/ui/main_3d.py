"""
진입점 — QMainWindow + QTabWidget(7 탭) + Vispy + Controller 연결.
실행: venv/Scripts/python.exe -m modular_3d

[2026-05-11 탭 구조 도입]
- 메인 윈도우 상단에 4 탭: [디자인] [구조해석] [물량] [시나리오]
- F5/F6 단축키 폐기 — 탭 클릭으로 동작 분기
- F5 도크·F6 도크 등 QDockWidget 제거. 알맹이(AnalysisPanel·AlignmentCanvas)만
  탭 페이지에 박힘
- 디자인 탭 = 좌 팔레트(180px) + 중앙(3D | 2D 가로분할) + 우 속성(240px) + 하단 치수입력
- 구조해석 탭 = 중앙(3D + 변형슬라이더) + 우 AnalysisPanel(서브탭: 요약·내부력)
- 물량 탭 = 중앙(3D + 변형슬라이더) + 우 AnalysisPanel(서브탭: 물량산출)
- 시나리오 탭 = 저장·불러오기 버튼
- 공유 위젯(3D viewer · 변형 슬라이더 · AlignmentDockPanel · AnalysisPanel)은
  탭 전환 시 setParent + addWidget 으로 reparent
"""
import os
import sys

# 직접 실행 시 패키지 상위 디렉토리를 sys.path 에 추가
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_pkg_dir)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

# Qt 플랫폼 플러그인 경로 설정 (venv 환경)
_qt_plugin_path = os.path.join(_pkg_dir, '..', 'venv',
                               'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
if os.path.isdir(_qt_plugin_path):
    os.environ['QT_PLUGIN_PATH'] = os.path.abspath(_qt_plugin_path)

import vispy
vispy.use('pyqt5')  # canvas 생성 전 필수

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QSlider, QLabel, QCheckBox, QTabWidget, QSplitter, QPushButton,
    QFileDialog, QMessageBox, QSpinBox,
)
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QFont, QFontDatabase

from modular_3d.model import Scene, ComponentType
from modular_3d.render.viewer import Viewer3D
from modular_3d.ui.ui_panel import DimensionInputPanel, StatusBarManager
from modular_3d.ui.analysis_panel import AnalysisPanel
from modular_3d.ui.alignment_view import AlignmentDockPanel
from modular_3d.ui.controls import Controller
from modular_3d.ui.palette_panel import PalettePanel
from modular_3d.ui.design_props_panel import DesignPropertiesPanel
from modular_3d.ui.joint_edit_panel import JointEditPanel
from modular_3d._utils.debug import dprint


# 메인 탭 인덱스 상수
# [정책 2026-05-13 접합부조정탭] 디자인 ↔ 구조해석 사이에 접합부 조정 탭 삽입.
# 이하 모든 탭 인덱스 +1 shift. 외부 코드(컨트롤러 등) 가 이 상수를 참조하므로
# 정수 리터럴로 비교하지 말고 반드시 본 상수를 사용.
TAB_DESIGN = 0
TAB_JOINT_EDIT = 1
TAB_ANALYSIS = 2
TAB_QUANTITY = 3
TAB_TRANSPORT = 4
TAB_JOINT_AIR = 5
TAB_FINAL = 6


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('모듈러 부재 스터디')
        self.resize(1600, 950)

        # ── 모델 ─────────────────────────────────────────
        self._scene = Scene()

        # ── 3D 뷰어 (탭 간 공유 위젯) ─────────────────────
        self._viewer = Viewer3D()
        self._canvas_widget = self._viewer.get_native_widget()
        self._canvas_widget.setFocusPolicy(Qt.StrongFocus)

        # ── 변형 형상 컨트롤 (해석·물량 탭에서만 표시) ──
        self._deformed_widget = self._build_deformed_widget()

        # ── 하단 치수입력 패널 (디자인 탭에서만 표시) ──
        self._dim_panel = DimensionInputPanel()

        # ── 상태바 ──────────────────────────────────────
        self._status_mgr = StatusBarManager(self.statusBar())

        # ── 우측 패널들 ─────────────────────────────────
        # 디자인 탭 우측: 속성 패널 (활성/선택 라벨 + AI 최적배치 입력)
        self._design_props = DesignPropertiesPanel(self._scene)
        # 접합부 조정 탭 우측: 결합 토글·합성거동·다이어프램 시각화·범례
        self._joint_panel = JointEditPanel()
        # [2026-05-13 접합부조정탭] 다이어프램 토글 → viewer 직접 wiring.
        # 구조해석 탭(AnalysisPanel) 에서 이동된 시그널.
        self._joint_panel.diaphragm_toggle.connect(
            self._viewer.set_show_diaphragms)
        # [2026-05-17] 규칙 ID 별 시각화 토글 → viewer.set_rule_visibility.
        # 실제 접합은 유지, 와이어프레임 결합선만 켜고/끔.
        self._joint_panel.rule_visibility_changed.connect(
            self._viewer.set_rule_visibility)
        # 구조해석/물량 탭 우측: AnalysisPanel (서브탭 [요약][내부력][물량산출])
        self._analysis_panel = AnalysisPanel()

        # ── Controller 생성 (analysis_dock 인자는 None — 도크 없음) ──
        self._controller = Controller(
            self._viewer, self._dim_panel, self._status_mgr, self._scene,
            self._analysis_panel, None,
        )
        # 변형 형상 컨트롤 위젯을 컨트롤러에 등록 (Phase 4-B)
        if hasattr(self._controller, 'attach_deformed_controls'):
            self._controller.attach_deformed_controls(
                self._deformed_check, self._deformed_slider, self._deformed_label,
            )

        # ── 디자인 탭 우측 가운데 2D F5 캔버스 (탭 간 공유) ──
        # F5 도크는 만들지 않고, 패널 알맹이만 디자인 탭에 직접 박는다.
        self._f5_panel = AlignmentDockPanel(self._controller)
        # Controller 에 패널 참조 주입 — 키 콜백/저장/불러오기 wiring
        if hasattr(self._controller, 'set_f5_dock'):
            self._controller.set_f5_dock(None, self._f5_panel)
        # [2026-05-11] 2D 캔버스 선택 변경 → 우측 디자인 속성 패널 자동 갱신
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'selection_changed'):
            canvas.selection_changed.connect(self._design_props.refresh_selected)

        # (2026-05-13) F5 패널 ↔ AI 최적배치 영역 층수 양방향 연동.
        # F5 → AI: F5 의 floors_changed 신호 → AI SpinBox 갱신(외부 갱신).
        # AI → F5: AI 의 floors_changed 신호 → F5 의 set_floors(emit=True).
        #          emit=True 라야 F5 의 floors_changed 시그널이 다시 발화해
        #          controller 의 3D 모델 재생성 트리거가 호출됨.
        #          무한 루프는 AI 측 _ai_floors_internal_update 플래그로 차단
        #          (외부 갱신 중에는 _on_ai_floors_changed 가 시그널 재발화 X).
        if hasattr(self._f5_panel, 'floors_changed'):
            self._f5_panel.floors_changed.connect(
                self._design_props.set_floors_from_external)
        if hasattr(self._design_props, 'floors_changed'):
            self._design_props.floors_changed.connect(
                lambda n: self._f5_panel.set_floors(int(n), emit=True))
        # 초기값 동기화 — F5 패널의 현재 층수로 AI SpinBox 맞춤.
        try:
            init_floors = int(self._f5_panel.floors)
            self._design_props.set_floors_from_external(init_floors)
        except Exception:
            pass

        # ── 좌측 팔레트 (디자인 탭 전용) ─────────────────
        # 코어 슬래브 버튼 콜백 — 컨트롤러(F5Mixin)의 메서드 직접 주입.
        _regen_cb = getattr(self._controller, 'regenerate_all_core_slabs', None)
        self._palette = PalettePanel(
            on_select=self._on_palette_select,
            on_regen_core_slabs=_regen_cb,
        )
        # (2026-05-13) 폭 확장 — 한글 라벨 잘림 방지.
        self._palette.setFixedWidth(220)
        self._design_props.setFixedWidth(320)

        # ── 메인 탭 위젯 ────────────────────────────────
        self._tabs = QTabWidget()
        self._tab_design = self._build_design_tab()
        # [2026-05-13 접합부조정탭] 디자인 ↔ 구조해석 사이에 접합부 조정 탭 신설.
        # 와이어프레임은 _draw_from_spec 코드 그대로 공유(중복 코드 금지). solve
        # 미실행. 자세한 정책은 접합부조정탭_계획서.md.
        self._tab_joint_edit = self._build_joint_edit_tab()
        self._tab_analysis = self._build_analysis_tab()
        self._tab_quantity = self._build_quantity_tab()
        # (2026-05-13 UI 개편) 시나리오 탭 제거 — 저장/불러오기 기능은 디자인 탭으로.
        # 새 탭: 운송 / 공기 및 접합부 / 최종평가. 내부 내용은 비워둠.
        self._tab_transport = self._build_placeholder_tab('운송')
        self._tab_joint_air = self._build_placeholder_tab('공기 및 접합부')
        self._tab_final = self._build_placeholder_tab('최종평가')
        self._tabs.addTab(self._tab_design, '디자인')
        self._tabs.addTab(self._tab_joint_edit, '접합부 조정')
        self._tabs.addTab(self._tab_analysis, '구조해석')
        self._tabs.addTab(self._tab_quantity, '물량')
        self._tabs.addTab(self._tab_transport, '운송')
        self._tabs.addTab(self._tab_joint_air, '공기 및 접합부')
        self._tabs.addTab(self._tab_final, '최종평가')
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

        # 초기 탭 진입 — 디자인 탭 마운트
        self._on_tab_changed(TAB_DESIGN)

        # Qt 이벤트 필터 — vispy 이벤트 대신 Qt에서 직접 가로챔
        self._canvas_widget.installEventFilter(self)
        self._canvas_widget.setMouseTracking(True)

        # 캔버스에 포커스
        self._canvas_widget.setFocus()

    # ── 변형 형상 컨트롤 위젯 ─────────────────────────────
    def _build_deformed_widget(self) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        self._deformed_check = QCheckBox('변형 형상')
        self._deformed_check.setChecked(False)
        row.addWidget(self._deformed_check)
        row.addWidget(QLabel('배율:'))
        self._deformed_slider = QSlider(Qt.Horizontal)
        self._deformed_slider.setMinimum(1)
        self._deformed_slider.setMaximum(5000)
        self._deformed_slider.setValue(100)
        self._deformed_label = QLabel('×100')
        row.addWidget(self._deformed_slider, stretch=1)
        row.addWidget(self._deformed_label)
        w = QWidget()
        w.setLayout(row)
        w.setMaximumHeight(34)
        return w

    # ── 탭 페이지 빌드 (공유 위젯은 _on_tab_changed 에서 reparent) ──

    def _build_design_tab(self) -> QWidget:
        """디자인 탭: 좌 팔레트 + 중앙(3D | 2D) + 우 속성 + 하단 치수."""
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)

        # 좌측 팔레트
        h.addWidget(self._palette)

        # 중앙: QVBoxLayout — 위는 3D | 2D 가로 분할(QSplitter), 아래는 치수 입력
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        self._design_center_split = QSplitter(Qt.Horizontal)
        # 좌(3D) / 우(2D) placeholder QWidget — 탭 전환 시 reparent 대상
        self._design_left_pane = QWidget()
        self._design_left_lay = QVBoxLayout(self._design_left_pane)
        self._design_left_lay.setContentsMargins(0, 0, 0, 0)
        self._design_right_pane = QWidget()
        self._design_right_lay = QVBoxLayout(self._design_right_pane)
        self._design_right_lay.setContentsMargins(0, 0, 0, 0)
        self._design_center_split.addWidget(self._design_left_pane)
        self._design_center_split.addWidget(self._design_right_pane)
        # [2026-05-11 v2] 3D : 2D = 7 : 3 비율로 초기화. 사용자가 핸들로 자유 조정.
        # 단순 stretchFactor 만 두면 자식 위젯의 sizeHint 가 우선되어 2D 가 과대평가됨 →
        # setSizes 로 절대 비율도 명시.
        self._design_center_split.setStretchFactor(0, 7)
        self._design_center_split.setStretchFactor(1, 3)
        self._design_center_split.setSizes([700, 300])
        self._design_center_split.setChildrenCollapsible(False)
        cv.addWidget(self._design_center_split, stretch=1)

        # 하단 치수입력 패널 placeholder
        self._design_dim_holder = QWidget()
        self._design_dim_lay = QVBoxLayout(self._design_dim_holder)
        self._design_dim_lay.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self._design_dim_holder)

        h.addWidget(center, stretch=1)

        # 우측 속성 패널
        h.addWidget(self._design_props)
        return page

    def _build_joint_edit_tab(self) -> QWidget:
        """접합부 조정 탭: 중앙(3D 와이어프레임) + 우 접합부 UI placeholder.

        [정책 2026-05-13]
        - viewer 위젯(canvas_widget) 은 디자인·구조해석 탭과 공유. _on_tab_changed
          에서 reparent.
        - solve 없이 build_ops_model 만 호출하여 spec 을 받고 _draw_from_spec 로
          와이어프레임 표시. 5 케이스 솔버는 본 탭에서 절대 부르지 않음.
        - 우측 패널은 일단 placeholder. Phase 3 에서 디자인 탭의 더미 접합부 UI
          를 이쪽으로 이동.
        """
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)

        # 중앙: 3D 와이어프레임 placeholder
        self._joint_center_pane = QWidget()
        self._joint_center_lay = QVBoxLayout(self._joint_center_pane)
        self._joint_center_lay.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self._joint_center_pane, stretch=1)

        # 우측: 접합부 UI placeholder — _on_tab_changed 진입 시 reparent.
        self._joint_right_pane = QWidget()
        self._joint_right_pane.setFixedWidth(320)
        self._joint_right_lay = QVBoxLayout(self._joint_right_pane)
        self._joint_right_lay.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self._joint_right_pane)
        return page

    def _build_analysis_tab(self) -> QWidget:
        """구조해석 탭: 중앙(3D + 변형슬라이더) + 우 AnalysisPanel(요약/내부력)."""
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self._analysis_center_pane = QWidget()
        self._analysis_center_lay = QVBoxLayout(self._analysis_center_pane)
        self._analysis_center_lay.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self._analysis_center_pane, stretch=1)
        h.addWidget(center, stretch=1)

        # 우측: AnalysisPanel placeholder
        # (2026-05-19) 고정폭 → 최소폭으로 변경. AnalysisPanel._fit_panel_to_tree
        # 가 트리/표 내용에 맞춰 minimumWidth 를 동적으로 늘리면 부모 pane 도
        # 같이 늘어나 글씨 짤림 없이 우측 패널이 자동 확장된다.
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        self._analysis_right_pane = QWidget()
        self._analysis_right_pane.setMinimumWidth(380)
        self._analysis_right_pane.setSizePolicy(_QSP.Minimum, _QSP.Expanding)
        self._analysis_right_lay = QVBoxLayout(self._analysis_right_pane)
        self._analysis_right_lay.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self._analysis_right_pane)
        return page

    def _build_quantity_tab(self) -> QWidget:
        """물량 탭: 중앙(3D + 변형슬라이더) + 우 AnalysisPanel(물량산출)."""
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self._quantity_center_pane = QWidget()
        self._quantity_center_lay = QVBoxLayout(self._quantity_center_pane)
        self._quantity_center_lay.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self._quantity_center_pane, stretch=1)
        h.addWidget(center, stretch=1)

        # (2026-05-19) 물량 탭도 동일 정책 — 트리/표 내용에 따라 자동 확장.
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        self._quantity_right_pane = QWidget()
        self._quantity_right_pane.setMinimumWidth(380)
        self._quantity_right_pane.setSizePolicy(_QSP.Minimum, _QSP.Expanding)
        self._quantity_right_lay = QVBoxLayout(self._quantity_right_pane)
        self._quantity_right_lay.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self._quantity_right_pane)
        return page

    def _build_placeholder_tab(self, title_text: str) -> QWidget:
        """빈 탭 — 운송 / 공기 및 접합부 / 최종평가 용. 내부 내용은 추후 추가.

        [2026-05-13 UI 개편] 시나리오 탭(저장/불러오기) 제거 후 신설된 3 탭의
        공통 placeholder. 향후 각 탭의 실제 위젯을 채워 넣는다.
        """
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)
        title = QLabel(f'{title_text}')
        title.setStyleSheet('font-weight: bold; font-size: 14px;')
        v.addWidget(title)
        info = QLabel('(추후 구현 예정)')
        info.setStyleSheet('color: #888;')
        v.addWidget(info)
        v.addStretch(1)
        return page

    # ── 탭 전환 시 공유 위젯 reparent ────────────────────

    def _on_tab_changed(self, idx: int):
        # 모든 페이지의 placeholder layout 비우기 — 이전 reparent 잔재 제거
        # (Qt 는 widget 을 다른 layout 에 addWidget 하면 자동 reparent 하므로
        # 명시적 removeWidget 없이 진행 가능. 단, hide 처리만)

        # [2026-05-13 접합부조정탭 Phase 4] 직전 탭 추적.
        # 접합부 조정 탭에서 구조해석/물량 탭으로 이동할 때는 본 탭에서 토글/
        # 수정한 결합 변경 사항이 다음 빌드에 반영돼야 하므로 ops 뷰를 강제
        # 무효화 — _run_analysis_if_needed 가 OFF 상태에서 새 빌드 + solve 를
        # 부르게 한다.
        prev_idx = getattr(self, '_prev_tab_idx', None)
        self._prev_tab_idx = idx
        if (prev_idx == TAB_JOINT_EDIT
                and idx in (TAB_ANALYSIS, TAB_QUANTITY)):
            v = self._viewer
            if hasattr(v, 'is_ops_view_active') and v.is_ops_view_active():
                v.hide_ops_view()
                dprint('ANALYSIS', '[ANALYSIS] 접합부 조정 → 구조해석/물량 — ops 뷰 무효화 후 재빌드 예정')

        if idx == TAB_DESIGN:
            # 3D → 디자인 좌, 2D F5 → 디자인 우, 치수 → 디자인 하단
            self._design_left_lay.addWidget(self._canvas_widget)
            self._design_right_lay.addWidget(self._f5_panel)
            self._design_dim_lay.addWidget(self._dim_panel)
            self._dim_panel.setVisible(True)
            self._deformed_widget.hide()
            # [2026-05-11 v5] 구조해석 → 디자인 복귀 시 ops 와이어프레임/변형형상 등을 끄고
            # 일반 메쉬 뷰로 복원. 기존 F6 → F5 토글 시의 정리 코드와 동일.
            self._restore_design_view()
        elif idx == TAB_JOINT_EDIT:
            # [2026-05-13 접합부조정탭] canvas + 우측 패널 reparent + 프리뷰.
            # solve 미실행 — spec/ops 빌드만 하고 _draw_from_spec 으로 표시.
            self._joint_center_lay.addWidget(self._canvas_widget)
            self._joint_right_lay.addWidget(self._joint_panel)
            self._deformed_widget.hide()
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            self._run_joint_edit_preview()
        elif idx == TAB_ANALYSIS:
            self._analysis_center_lay.addWidget(self._canvas_widget)
            self._analysis_center_lay.addWidget(self._deformed_widget)
            self._deformed_widget.show()
            self._analysis_right_lay.addWidget(self._analysis_panel)
            # (2026-05-12) dim_panel 비활성 + state 도 IDLE 로 — 탭 전환 후
            # 디자인 탭 복귀 시 "사용자 키 입력해도 무동작" 상태 방지.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            # 서브탭: [요약][부재별 내부력] 만 보이게
            if hasattr(self._analysis_panel, 'set_visible_subtabs'):
                self._analysis_panel.set_visible_subtabs(
                    show_summary=True, show_member=True, show_quantity=False,
                )
            # [2026-05-11 v4] 탭 진입 시 자동 해석 실행 (기존 F6 동작)
            self._run_analysis_if_needed()
            # (2026-05-19 작업 5) 구조해석 탭 복귀 시 케이스를 D+L 로 되돌림.
            if hasattr(self._analysis_panel, 'select_dl_case'):
                self._analysis_panel.select_dl_case()
        elif idx == TAB_QUANTITY:
            self._quantity_center_lay.addWidget(self._canvas_widget)
            self._quantity_center_lay.addWidget(self._deformed_widget)
            self._deformed_widget.show()
            self._quantity_right_lay.addWidget(self._analysis_panel)
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            # 서브탭: [물량산출] 만 보이게
            if hasattr(self._analysis_panel, 'set_visible_subtabs'):
                self._analysis_panel.set_visible_subtabs(
                    show_summary=False, show_member=False, show_quantity=True,
                )
            # 물량 탭도 해석 결과가 필요 → 동일하게 자동 실행
            self._run_analysis_if_needed()
            # (2026-05-19 작업 5) 물량 탭은 진입 시 자동으로 '지배조합' 선택.
            # 5 케이스 envelope 단면력 기준 응력비가 기본 화면이 된다.
            if hasattr(self._analysis_panel, 'select_envelope_case'):
                self._analysis_panel.select_envelope_case()
        elif idx in (TAB_TRANSPORT, TAB_JOINT_AIR, TAB_FINAL):
            # 운송 / 공기 및 접합부 / 최종평가 — 빈 탭. 공유 위젯 X.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)

    # ── 디자인 탭 복귀 시 3D 뷰 정리 ────────────────────

    def _restore_design_view(self):
        """구조해석/물량 탭 → 디자인 탭으로 돌아왔을 때 일반 메쉬 뷰 복원.

        [전략] 색상/GL state 만 강제 복원하는 방식은 vispy 의 깊은 GL 상태가
        남아 두 번째 복귀에서 메쉬가 보이지 않는 케이스가 있다. 가장 견고하게
        부재 메쉬 자체를 모두 제거 후 mesh_builder 로 새로 빌드한다.
        """
        v = self._viewer
        try:
            if hasattr(v, 'is_ops_view_active') and v.is_ops_view_active():
                v.hide_ops_view()
            if hasattr(v, 'hide_unstable_warning'):
                v.hide_unstable_warning()
            if hasattr(v, 'hide_deformed_shape'):
                v.hide_deformed_shape()
            if hasattr(v, 'hide_selection_box'):
                v.hide_selection_box()
            if hasattr(v, 'clear_member_highlight'):
                try:
                    v.clear_member_highlight()
                except Exception:
                    pass
            if hasattr(v, 'hide_snap_marker'):
                v.hide_snap_marker()
            if hasattr(v, 'set_ghost_enabled'):
                v.set_ghost_enabled(False)
            if hasattr(v, 'clear_ghost'):
                v.clear_ghost()

            # 부재 메쉬 완전 재빌드
            self._rebuild_all_component_visuals()

            if hasattr(v, 'canvas'):
                v.canvas.update()
        except Exception as e:
            dprint('VIEW', f'[VIEW] 디자인 복귀 정리 중 경고: {type(e).__name__}: {e}')

    def _rebuild_all_component_visuals(self):
        """Scene 의 모든 부재 메쉬를 viewer 에서 완전히 제거 후 재빌드.

        ops 뷰 토글로 인한 GL state 잔재를 깨끗이 비운다. 부재 모델 자체는
        그대로이므로 SnapManager 등의 다른 상태는 건드리지 않는다.
        """
        try:
            from modular_3d.render.mesh_builder import build_component_mesh
        except Exception as e:
            dprint('VIEW', f'[VIEW] mesh_builder import 실패: {e}')
            return
        v = self._viewer
        # 기존 메쉬 모두 제거
        for cid in list(getattr(v, '_component_visuals', {}).keys()):
            try:
                v.remove_component_visual(cid)
            except Exception:
                pass
        # 새로 빌드
        for cid, comp in self._scene.components.items():
            try:
                verts, faces, colors = build_component_mesh(comp)
                v.add_component_visual(cid, verts, faces, colors)
            except Exception as e:
                dprint('VIEW', f'[VIEW] id={cid} 재빌드 실패: {type(e).__name__}: {e}')

    # ── 접합부 조정 탭 — 와이어프레임 프리뷰 (solve 미실행) ──

    def _run_joint_edit_preview(self):
        """접합부 조정 탭 진입 시 spec 만 빌드해 와이어프레임 표시.

        [정책 2026-05-13 접합부조정탭]
        구조해석 탭의 `_run_structural_analysis` 와 달리 **solve_all_cases 를
        부르지 않는다**. build_analysis_model + build_ops_model 까지만 — spec
        알갱이가 채워지면 viewer.show_ops_view 가 _draw_from_spec 로 와이어프
        레임을 그린다.

        [함정] 이미 ops 뷰가 켜져 있으면(예: 구조해석 탭에서 본 탭으로 이동) 또
        토글되어 꺼지는 일이 없도록 활성 상태일 땐 build 자체를 생략한다. 단,
        본 탭에서 머무는 동안 결합 토글이 바뀌면 그 시점에 강제 재빌드 트리거
        (Phase 5 의 토글 핸들러) 가 필요하다 — 현재는 토글이 더미라 발생 X.
        """
        if not self._scene.components:
            dprint('JOINT-EDIT', '[JOINT-EDIT] Scene 이 비어 있음 — 프리뷰 생략')
            return
        v = self._viewer
        if hasattr(v, 'is_ops_view_active') and v.is_ops_view_active():
            dprint('JOINT-EDIT', '[JOINT-EDIT] ops 뷰 이미 활성 — 프리뷰 재실행 생략')
            return
        try:
            from modular_3d.analysis.topology import build_analysis_model
            from modular_3d.analysis.ops_builder import build_ops_model
        except ImportError as e:
            dprint('JOINT-EDIT', f'[JOINT-EDIT] 해석 모듈 import 실패: {e}')
            return
        try:
            am = build_analysis_model(self._scene)
            om_view = build_ops_model(am, scene=self._scene)
            dprint('JOINT-EDIT', '[JOINT-EDIT] spec 빌드 완료 — solve 생략. 와이어프레임 표시.')
            v.show_ops_view(om_view)
            # [Phase 5] 범례 동적 갱신 — spec 의 모든 결합 레코드에서 rule_id 수집.
            try:
                spec = getattr(om_view, 'spec', None)
                if spec is not None:
                    rule_ids = set()
                    for e in spec.iter_equal_dofs():
                        rule_ids.add(getattr(e, 'rule_id', 'legacy_auto'))
                    for r in spec.iter_rigid_links():
                        rule_ids.add(getattr(r, 'rule_id', 'legacy_auto'))
                    for d in spec.iter_diaphragms():
                        rule_ids.add(getattr(d, 'rule_id', 'legacy_auto'))
                    self._joint_panel.refresh_legend(rule_ids)
            except Exception as e2:
                dprint('JOINT-EDIT', f'[JOINT-EDIT] 범례 갱신 경고: {type(e2).__name__}: {e2}')
        except Exception as e:
            dprint('JOINT-EDIT', f'[JOINT-EDIT] 프리뷰 중 오류: {type(e).__name__}: {e}')
            QMessageBox.warning(
                self, '접합부 조정 프리뷰 실패',
                f'와이어프레임 빌드 중 오류:\n\n{type(e).__name__}: {e}',
            )

    # ── 구조해석 자동 실행 ─────────────────────────────

    def _run_analysis_if_needed(self):
        """구조해석/물량 탭 진입 시 OpenSeesPy 해석을 실행.

        - Scene 이 비어 있으면 안내 후 패스
        - ops 뷰가 이미 ON 이면 (해석 ↔ 물량 탭 이동 등) 재실행하지 않음
          — `_run_structural_analysis` 가 토글 동작이라 다시 호출하면 OFF 되어
            와이어프레임이 사라지는 문제를 회피한다.
        - OFF 상태에서만 호출하여 새로 빌드 + ON.
        """
        if not self._scene.components:
            dprint('ANALYSIS', '[ANALYSIS] Scene 이 비어 있음 — 해석 생략')
            return
        if not hasattr(self._controller, '_run_structural_analysis'):
            return
        # 이미 ON 이면 그대로 둠 (탭 간 이동 시 시각화 유지)
        v = self._viewer
        if hasattr(v, 'is_ops_view_active') and v.is_ops_view_active():
            dprint('ANALYSIS', '[ANALYSIS] ops 뷰 이미 활성 — 재실행 생략 (탭 이동)')
            return
        try:
            self._controller._run_structural_analysis()
        except Exception as e:
            dprint('ANALYSIS', f'[ANALYSIS] 해석 중 오류: {type(e).__name__}: {e}')
            QMessageBox.warning(
                self, '구조해석 실패',
                f'해석 중 오류가 발생했습니다:\n\n{type(e).__name__}: {e}',
            )

    # ── 좌측 팔레트 콜백 ────────────────────────────────

    def _on_palette_select(self, comp_type):
        """팔레트 버튼 클릭 → 2D 캔버스의 키 1~8 입력과 100% 동일 경로.

        [중요] 종속 부재(4·5·6·7) 의 DEPENDENCY_PICK 진입 + 가능 부모 하이라이트는
        AlignmentCanvas 측 분기에 들어 있다. 따라서 Controller._f5_on_type_key 를
        직접 호출하지 않고, 캔버스의 `handle_palette_select` 를 거쳐야 한다.
        """
        if comp_type is None:
            # '선택 해제' 버튼
            self._design_props.refresh_active(None)
            dprint('PALETTE', '[PALETTE] 선택 해제')
            return
        # 캔버스에 위임 — 종속/일반 분기는 캔버스가 결정
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'handle_palette_select'):
            canvas.handle_palette_select(comp_type)
        self._design_props.refresh_active(comp_type)

    # ── 시나리오 탭 핸들러 ──────────────────────────────

    def _on_scenes_floors_changed(self, n: int):
        """시나리오 탭의 층수 SpinBox 변경 → F5 패널과 동기화 + 시그널 발화."""
        if hasattr(self, '_f5_panel') and self._f5_panel is not None:
            self._f5_panel.set_floors(int(n), emit=True)

    def _on_scene_save(self):
        """시나리오 탭 '저장' 버튼 → F5Mixin._f5_save_scene 재사용."""
        if hasattr(self._controller, '_f5_save_scene'):
            self._controller._f5_save_scene()

    def _on_scene_load(self):
        """시나리오 탭 '불러오기' 버튼 → F5Mixin._f5_load_scene 재사용 후
        디자인 탭으로 자동 전환."""
        if hasattr(self._controller, '_f5_load_scene'):
            self._controller._f5_load_scene()
        self._tabs.setCurrentIndex(TAB_DESIGN)

    # ── 헬퍼 ─────────────────────────────────────────

    def _is_design_tab(self) -> bool:
        return self._tabs.currentIndex() == TAB_DESIGN

    # ── Qt 이벤트 필터 (캔버스 — vispy 위젯) ──────────────

    def eventFilter(self, obj, event):
        if obj is not self._canvas_widget:
            return super().eventFilter(obj, event)

        etype = event.type()
        try:
            if etype == QEvent.KeyPress:
                text = event.text()
                qt_key = event.key()
                # F5/F6 단축키 폐기 — 무시
                if qt_key in (Qt.Key_F5, Qt.Key_F6):
                    return True
                # 디자인 탭이면 키 입력은 2D F5 캔버스로 라우팅
                if self._is_design_tab():
                    self._f5_panel.canvas.keyPressEvent(event)
                    return True
                # 그 외 탭: 컨트롤러로 전달 (z 등만 동작)
                self._controller.on_qt_key_press(text, qt_key)
                return False

            elif etype == QEvent.MouseButtonPress:
                btn = event.button()
                # 디자인 탭: 3D 좌클릭(선택/배치) 비활성. 카메라 조작(가운데/오른쪽)은 통과
                if self._is_design_tab() and btn == Qt.LeftButton:
                    return True
                pos = (event.x(), event.y())
                self._controller.on_qt_mouse_press(btn, pos)
                return False

            elif etype == QEvent.MouseButtonRelease:
                if event.button() == Qt.MiddleButton:
                    self._controller.refresh_after_camera()
                return False

            elif etype == QEvent.MouseMove:
                # 디자인 탭: 3D 마우스 무브 무시 (고스트 위치 갱신 막음)
                if self._is_design_tab():
                    return False
                pos = (event.x(), event.y())
                self._controller.on_qt_mouse_move(pos)
                return False

        except Exception as e:
            print(f'[EVENT ERROR] {etype}: {e}')

        return False

    def keyPressEvent(self, event):
        """메인 윈도우 키 이벤트.

        - F5/F6 단축키 폐기 — 무시
        - 디자인 탭 + 그 외 키: F5 캔버스로 전달 (배치 입력)
        """
        key = event.key()
        if key in (Qt.Key_F5, Qt.Key_F6):
            event.accept()
            return
        if self._is_design_tab():
            self._f5_panel.canvas.keyPressEvent(event)
            return
        super().keyPressEvent(event)


def _apply_app_font(app: QApplication) -> None:
    """전역 폰트를 Pretendard 로 설정.

    [정책 2026-05-13] 발표용 디자인 시스템(Junghoon.md)이 Pretendard 로 통일된
    것에 맞춰 본 프로그램 UI 도 Pretendard 를 단일 보이스로 사용. 시스템에
    설치돼 있지 않으면 맑은 고딕 → Qt 기본순으로 안전 폴백.
    """
    installed = set(QFontDatabase().families())
    for candidate in ('Pretendard', 'Pretendard Variable',
                      'Malgun Gothic', '맑은 고딕'):
        if candidate in installed:
            f = QFont(candidate, 9)
            app.setFont(f)
            return


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    _apply_app_font(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
