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
    QApplication, QDialog, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QSlider, QLabel, QCheckBox, QTabWidget, QSplitter, QPushButton,
    QFileDialog, QMessageBox, QSpinBox, QInputDialog,
)
from PyQt5.QtCore import Qt, QEvent, QObject, pyqtSlot, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWebChannel import QWebChannel


# ─────────────────────────────────────────────────────────────
# Transport 3D ↔ Python 통신 브리지 (QWebChannel)
# ─────────────────────────────────────────────────────────────
class _TransportBridge(QObject):
    """Three.js JS 가 트럭 클릭 시 on_truck_clicked(trip_no) 호출.

    PyQt 신호로 변환 후 MainWindow 가 받아 회차표·경제성표 행 선택 + 3D 강조 트리거.
    """
    truck_clicked_in_3d = pyqtSignal(int)

    @pyqtSlot(int)
    def on_truck_clicked(self, trip_no: int) -> None:
        self.truck_clicked_in_3d.emit(int(trip_no))

from modular_3d.model import Scene, ComponentType, TYPE_NAMES
from modular_3d.render.viewer import Viewer3D
# UI 마이그레이션 M2 (2026-05-28) — Strangler 어댑터로 교체. 같은 인터페이스라
# 기존 호출 코드는 한 줄도 안 건드림. 자세한 결정은 UI_마이그레이션/05_M2_설계.md.
from modular_3d.render.viewer_strangler import ViewerStrangler
from modular_3d.ui.ui_panel import DimensionInputPanel, StatusBarManager
from modular_3d.ui.analysis_panel import AnalysisPanel
from modular_3d.ui.alignment.alignment_view import AlignmentDockPanel
# UI 마이그레이션 M3-b — three.js 2D 평면 뷰 어댑터.
from modular_3d.ui.alignment.alignment_dock_three import AlignmentDockPanelThree
from modular_3d.ui.controls import Controller
from modular_3d.ui.palette_panel import PalettePanel
from modular_3d.ui.design_props_panel import DesignPropertiesPanel
from modular_3d.ui.room_props_panel import RoomPropertiesPanel
from modular_3d.ui.joint_edit_panel import JointEditPanel
from modular_3d.ui.project_settings import ProjectSettings, ProjectSettingsDialog
from modular_3d.ui.define_tab import DefineTab
from modular_3d.model.definition_library import DefinitionLibrary
from modular_3d._utils.debug import dprint, log_error


# 메인 탭 인덱스 상수
# [정책 2026-05-24 디자인 2분리] 맨 앞에 '모듈 정의' 탭 신설. 이하 모두 +1 shift.
# 외부 코드(컨트롤러 등) 가 이 상수를 참조하므로 정수 리터럴로 비교하지 말고
# 반드시 본 상수를 사용.
# [2026-06-01 랜딩 페이지 분리] 랜딩은 탭이 아니라 별도 StackedWidget 페이지.
# 따라서 탭 인덱스는 원래대로 (TAB_DEFINE=0).
TAB_DEFINE = 0
TAB_DESIGN = 1
TAB_JOINT_EDIT = 2
TAB_ANALYSIS = 3
TAB_SECTION = 4
TAB_QUANTITY = 5
TAB_TRANSPORT = 6
TAB_SCHEDULE = 7
TAB_JOINT_AIR = TAB_SCHEDULE  # deprecated alias
TAB_EVALUATION = 8
TAB_FINAL = TAB_EVALUATION    # deprecated alias
TAB_COMPARE = 9


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # [2026-06-01] 한국어 폰트(Freesentation 본문 / Paperlogy 헤드라인) 등록. (팀원)
        try:
            from .fonts import ensure_fonts_loaded
            ensure_fonts_loaded()
        except Exception:
            pass
        self.setWindowTitle('모듈러 설계 프로그램')
        self.resize(1600, 950)

        # ── 모델 ─────────────────────────────────────────
        self._scene = Scene()

        # ── 프로젝트 공통 설정 (세션 메모리) ──────────────
        # [2026-05-24] 사업 전체에 공통으로 쓰이는 입력(운송 거리·운임·비내력벽
        # 단위중량·현장 지역·착공일)을 한곳에 보관. 1단계는 보관까지만 — 운송
        # 탭 등 소비처와의 실제 연결은 다음 단계.
        self._project_settings = ProjectSettings()

        # ── 정의 라이브러리 (모듈 정의 탭 ↔ 배치 탭 공유, 파일 영구 저장) ──
        # [2026-05-24] 정의 탭에서 저장한 컴포넌트 정의를 배치 탭에서도 불러
        # 쓰기 위해 메인이 소유하고 두 탭이 공유한다. path 를 주면 추가/삭제 시
        # json 파일에 자동 저장하고 시작 시 불러온다(프로그램 재실행에도 유지).
        _deflib_path = os.path.join(_parent, 'definition_library',
                                    'definitions.json')
        self._definition_library = DefinitionLibrary(path=_deflib_path)

        # ── 3D 뷰어 (탭 간 공유 위젯) ─────────────────────
        # UI 마이그레이션 M2 — ViewerStrangler 가 Viewer3D + ViewerThree 를 모두
        # 보유하며 모든 변경 메서드를 broadcast. 기존 호출 코드는 인터페이스
        # 동일이라 그대로 동작. _canvas_widget 은 여전히 vispy 위젯 (이벤트필터
        # 호환). three.js 위젯은 _three_widget 으로 따로 마운트.
        self._viewer = ViewerStrangler()
        self._canvas_widget = self._viewer.vispy.get_native_widget()
        self._canvas_widget.setFocusPolicy(Qt.StrongFocus)
        self._three_widget = self._viewer.three.get_native_widget()

        # ── 변형 형상 컨트롤 (해석·물량 탭에서만 표시) ──
        self._deformed_widget = self._build_deformed_widget()

        # ── 하단 치수입력 패널 (디자인 탭에서만 표시) ──
        self._dim_panel = DimensionInputPanel()

        # ── 상태바 ──────────────────────────────────────
        self._status_mgr = StatusBarManager(self.statusBar())

        # ── 우측 패널들 ─────────────────────────────────
        # 디자인 탭 우측: 속성 패널 (활성/선택 라벨 + AI 최적배치 입력)
        self._design_props = DesignPropertiesPanel(self._scene)
        # 실(Room) 속성 패널 (2단계) — 실 선택 시 부재 패널과 전환 표시.
        self._room_props = RoomPropertiesPanel()
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
        # [2026-05-27 3-B Phase 3] 접합부 핸들러 18개 + 상태 변수 9개를
        # JointEditController 로 분리. self.window 로 main 참조.
        from modular_3d.ui.main_3d_joint import JointEditController
        self._joint_ctrl = JointEditController(self)
        # [2026-05-25 접합부 변경] 편집 모드/변경/복귀 시그널 배선 — controller 로.
        self._joint_panel.edit_mode_toggled.connect(self._joint_ctrl._on_joint_edit_mode)
        self._joint_panel.joint_change_requested.connect(self._joint_ctrl._on_joint_change)
        self._joint_panel.joint_revert_requested.connect(self._joint_ctrl._on_joint_revert)
        self._joint_panel.add_mode_toggled.connect(self._joint_ctrl._on_joint_add_mode)
        # 접합부 설계 탭 '저장' — 배치 설계 탭 저장과 동일(접합 변경 포함).
        self._joint_panel.save_requested.connect(self._on_scene_save)
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
        # UI 마이그레이션 M3-b — three.js 2D 뷰 (vispy 옆 세로 분할 마운트).
        # AlignmentCanvas.paintEvent 끝 후크에 자기 sync 등록.
        self._f5_panel_three = AlignmentDockPanelThree()
        self._f5_panel_three.attach_to(self._f5_panel.canvas)
        # Controller 에 패널 참조 주입 — 키 콜백/저장/불러오기 wiring
        if hasattr(self._controller, 'set_f5_dock'):
            self._controller.set_f5_dock(None, self._f5_panel)
        # [2026-05-11] 2D 캔버스 선택 변경 → 우측 디자인 속성 패널 자동 갱신
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'selection_changed'):
            canvas.selection_changed.connect(self._design_props.refresh_selected)
        # 실 선택 변경 → 실 속성 패널 + 패널 전환 (2단계)
        if canvas is not None and hasattr(canvas, 'room_selection_changed'):
            canvas.room_selection_changed.connect(self._on_room_selected)
        self._room_props.room_edited.connect(self._on_room_edited)
        self._room_props.room_delete_requested.connect(self._on_room_delete)
        # 보 단면 타입(각형강관/H형강) 변경 → 컨트롤러
        if hasattr(self._controller, '_set_beam_section_type'):
            self._design_props.beam_section_changed.connect(
                self._controller._set_beam_section_type)

        # (2026-05-24) AI 최적배치 영역 제거에 따라 속성 패널 ↔ F5 층수 양방향
        # 연동도 제거. 층수는 F5 패널 상단 SpinBox 가 단일 컨트롤이며, 그
        # floors_changed 는 set_f5_dock 에서 controller._on_floors_changed 로
        # 직접 연결되어 3D 재생성이 정상 동작한다.

        # ── 좌측 팔레트 (디자인 탭 전용) ─────────────────
        self._palette = PalettePanel(
            on_select=self._on_palette_select,
            on_room_draw=self._on_room_draw,
            on_detail_mode_toggle=self._on_detail_mode_toggle,
            on_opening_add=self._on_opening_add,
            on_wall_place=self._on_wall_place,
        )
        # (2026-05-13) 폭 확장 — 한글 라벨 잘림 방지.
        self._palette.setFixedWidth(220)
        self._design_props.setFixedWidth(320)

        # ── 모듈 정의 탭 (2026-05-24 디자인 2분리) ──────────
        # 배치 설계 탭과 완전 분리된 독립 작업공간(자체 Scene·Viewer·Controller).
        # 상태바 매니저 + 정의 라이브러리를 공유. 정책은 디자인탭_분리_계획서.md.
        self._define_tab = DefineTab(self._status_mgr, self._definition_library)

        # [2026-05-24] 배치 컨트롤러에 타입선택 인터셉트 주입 — 1~9 버튼/키 선택
        # 시 저장된 정의가 있으면 '새로 만들기/가져오기' 프롬프트를 띄운다.
        # (정의 탭 컨트롤러에는 주입하지 않아 프롬프트가 안 뜬다.)
        self._controller._type_select_intercept = self._on_design_type_select
        # 가져오기 배치(_import_definition_start)가 컨트롤러에서 정의를 복원할 수
        # 있도록 라이브러리 주입(메인↔컨트롤러 공유 인스턴스).
        self._controller._definition_library = self._definition_library

        # ── 메인 탭 위젯 + 시그널 연결 ──────────────────
        self._setup_tabs()

        # 초기 탭 진입 — 배치 설계 탭(기존 디자인)으로 시작. 정의 탭은 index 0
        # 이지만 시작 화면은 익숙한 배치 설계로 둔다. setCurrentIndex 가
        # currentChanged 를 발화해 공유 위젯 마운트(_on_tab_changed)를 트리거.
        self._on_tab_changed(TAB_DESIGN)
        self._tabs.setCurrentIndex(TAB_DESIGN)

        # Qt 이벤트 필터 — vispy 이벤트 대신 Qt에서 직접 가로챔
        self._canvas_widget.installEventFilter(self)
        self._canvas_widget.setMouseTracking(True)

        # 캔버스에 포커스
        self._canvas_widget.setFocus()

    # ── 메인 탭 위젯 구성 (1864→1700 다이어트, 2026-05-27) ────
    def _setup_tabs(self) -> None:
        """메인 탭 위젯 생성·시그널 연결·메뉴바·운송 wiring 일괄.

        호출 시점 함정: __init__ 의 위젯·controller 생성이 모두 끝난 *후* 에
        호출해야 한다. self._analysis_panel, self._define_tab, self._build_*_tab
        의 의존 attr 가 모두 준비되어 있어야 함.
        """
        self._tabs = QTabWidget()
        self._tab_design = self._build_design_tab()
        # [2026-05-13 접합부조정탭] 디자인 ↔ 구조해석 사이에 접합부 조정 탭 신설.
        self._tab_joint_edit = self._build_joint_edit_tab()
        self._tab_analysis = self._build_analysis_tab()
        # [2026-05-31 단면 설계 탭] 구조해석과 물량 사이.
        self._tab_section = self._build_section_design_tab()
        self._tab_quantity = self._build_quantity_tab()
        # Phase 6: 운송 탭은 placeholder 대신 운송 전용 center+right 페이지.
        self._tab_transport = self._build_transport_tab_layout()
        # [2026-05-27 공정표 이식 Phase A] 팀원 HTML 임베드. _tab_joint_air 별칭 유지.
        from .schedule_panel import SchedulePanel
        self._tab_schedule = SchedulePanel()
        # [2026-05-27 평가 탭 Phase L] 공정표 calc() 결과 캐시.
        self._schedule_payload: dict = {}
        self._tab_schedule.bridge().schedule_payload_pushed.connect(
            self._on_schedule_payload_pushed
        )
        self._tab_joint_air = self._tab_schedule  # deprecated alias
        # [2026-05-27 평가 탭 이식 Phase J] EvaluationPanel. _tab_final 별칭 유지.
        from .evaluation_panel import EvaluationPanel
        self._tab_evaluation = EvaluationPanel()
        self._tab_evaluation.save_case_requested.connect(self._on_evaluation_save_case)
        # 정책(1/2/3종) 변경을 평가 탭에도 push — 활성일 때만 즉시 재계산.
        if hasattr(self._analysis_panel, 'policy_changed'):
            self._analysis_panel.policy_changed.connect(
                self._on_policy_changed_for_evaluation)
        self._tab_final = self._tab_evaluation  # deprecated alias
        # [2026-05-31] 비교 탭 — 저장된 .case.json 들을 가로 4 개까지 나열.
        from .compare_panel import ComparePanel
        self._tab_compare = ComparePanel()
        self._tabs.addTab(self._define_tab, '모듈 정의')
        self._tabs.addTab(self._tab_design, '배치 설계')
        self._tabs.addTab(self._tab_joint_edit, '접합부 설계')
        self._tabs.addTab(self._tab_analysis, '구조해석')
        self._tabs.addTab(self._tab_section, '단면 설계')
        self._tabs.addTab(self._tab_quantity, '물량')
        self._tabs.addTab(self._tab_transport, '운송')
        self._tabs.addTab(self._tab_schedule, '공정표')
        self._tabs.addTab(self._tab_evaluation, '종합')
        self._tabs.addTab(self._tab_compare, '비교')
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # [2026-06-01] 랜딩 페이지 — 탭 UI 가 아닌 단독 화면.
        # QStackedWidget 으로 page 0 = 랜딩, page 1 = 메인 탭 UI.
        # 프로그램 시작 시 page 0 (랜딩) 만 보이고 탭바·메뉴는 안 보임.
        # [2026-06-01] 랜딩 카드에 ProjectSettings 폼이 인라인으로 들어가므로
        # 현재 settings 인스턴스를 그대로 넘김 → 시작 클릭 시 폼이 자체적으로
        # settings 에 적용한 뒤 시그널 발화.
        from PyQt5.QtWidgets import QStackedWidget
        from .home_panel import HomePanel
        self._tab_home = HomePanel(self._project_settings)
        self._tab_home.start_new_project_requested.connect(self._on_home_start_new)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._tab_home)   # index 0
        self._stack.addWidget(self._tabs)        # index 1
        self.setCentralWidget(self._stack)
        self._stack.setCurrentIndex(0)
        # 메뉴바도 랜딩에서는 숨겨 깨끗한 첫 화면. 시작 후 표시.
        try:
            self.menuBar().setVisible(False)
        except Exception:
            pass
        # [Phase 7] 파일 메뉴 — 운송 카탈로그 진입점.
        self._build_menu_bar()
        # [Phase 7] 운송탭에 프로젝트 루트 전달 + 신호 wiring (운송 탭 객체가
        # 늦게 만들어지는 케이스 대비 try/except 보호).
        try:
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None and hasattr(tt, 'set_project_root'):
                tt.set_project_root(_parent)
            # [Phase C — 2026-05-26] 운송 계산 완료 → center pane 3D 도식 갱신.
            if tt is not None and hasattr(tt, 'transport_pack_updated'):
                tt.transport_pack_updated.connect(self._on_transport_pack_updated)
            # [Phase E — 2026-05-26] 회차표 행 클릭 → 3D 도식에서 해당 회차 강조.
            if tt is not None and hasattr(tt, 'transport_trip_clicked'):
                tt.transport_trip_clicked.connect(self._on_transport_trip_clicked)
        except Exception:
            pass

    # ── 상단 메뉴바 — 프로젝트 설정 ──────────────────────
    def _build_menu_bar(self) -> None:
        """상단 메뉴 줄 — 사업 공통 설정 진입점(프로젝트 설정) 1 개.

        [2026-05-24 프로젝트 설정] 기존 '운송(T)' 메뉴(트럭 카탈로그 진입점)를
        '프로젝트 설정'으로 교체. 트럭 카탈로그 관리는 프로젝트 설정 창 내부의
        버튼으로 이동했다. 메뉴 줄에는 클릭 한 번으로 창이 열리는 단일 항목만
        둔다.
        """
        from PyQt5.QtWidgets import QAction
        mb = self.menuBar()
        act_settings = QAction("프로젝트 설정", self)
        act_settings.setShortcut("Ctrl+Shift+P")
        act_settings.triggered.connect(self._open_project_settings)
        mb.addAction(act_settings)

    def _open_project_settings(self) -> bool:
        """프로젝트 설정 모달 — 공통 설정 입력. 확인 시 세션 메모리에 보관.

        Returns:
            True 면 사용자가 확인(Accepted) 했고 변경 반영 완료, False 면 취소.

        [정책 2026-05-24] 1단계는 보관까지만. 운송 탭 등 소비처와의 연결은
        다음 단계이므로, 확인을 눌러도 해석/물량/운송 재계산은 트리거하지
        않는다.
        """
        dlg = ProjectSettingsDialog(
            self._project_settings,
            on_open_catalog=self._open_transport_catalog,
            parent=self,
        )
        if dlg.exec_() != QDialog.Accepted:
            return False
        if True:
            # [2026-05-26] 확인 즉시 운송탭 읽기전용 표시(현장제한 등) 갱신.
            # 종전엔 탭을 빠져나갔다 다시 들어와야 반영됐음.
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None and hasattr(tt, 'apply_project_settings'):
                tt.apply_project_settings(self._project_settings)
            # [2026-05-31] 공정표 탭도 즉시 갱신 — 착공일·지역 변경이 바차트
            # 날짜 라벨과 비작업일 보정에 바로 반영되도록.
            try:
                if hasattr(self._tab_schedule, 'apply_project_settings'):
                    self._tab_schedule.apply_project_settings(self._project_settings)
            except Exception as e:
                dprint('SCHEDULE', f'[SCHEDULE] 프로젝트 설정 푸시 실패: {e}')
        return True

    def _open_transport_catalog(self) -> None:
        """파일 메뉴 진입점 — AnalysisPanel 의 _transport_tab 에 위임."""
        tt = getattr(self._analysis_panel, '_transport_tab', None)
        if tt is not None and hasattr(tt, 'open_catalog_dialog'):
            tt.open_catalog_dialog()

    # ── 변형 형상 컨트롤 위젯 ─────────────────────────────
    # ── 평가 탭 — 공정표 결과 푸시 수신 (Phase L) ─────────────
    def _on_schedule_payload_pushed(self, payload: dict) -> None:
        """공정표 HTML 의 calc() 가 보낸 결과를 캐시. 평가 탭이 진입 시 어댑터로 전달."""
        if isinstance(payload, dict):
            self._schedule_payload = payload

    # ── 평가 탭 — 케이스 저장 핸들러 (Phase N) ─────────────────
    def _on_evaluation_save_case(self) -> None:
        """저장 버튼 → QFileDialog → .case.json 완전 스냅샷 저장.

        구조: scene_state + ProjectSettings + 평가 화면 표시 데이터.
        배치 설계 탭과 같은 흐름이지만 확장자 .case.json 으로 구분.
        """
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        from modular_3d.io.scene_io import scene_to_state_dict
        from modular_3d.evaluation.case_io import save_case
        from modular_3d.evaluation.evaluation_adapter import build_evaluation_data

        path, _ = QFileDialog.getSaveFileName(
            self, "평가 결과 저장", "case.case.json", "평가 케이스 (*.case.json)"
        )
        if not path:
            return
        try:
            comps = list(self._scene.components.values()) if hasattr(self._scene, 'components') else []
            n_floors = self._f5_panel.floors if getattr(self, '_f5_panel', None) else 1
            scene_state = scene_to_state_dict(self._scene, n_floors)
            # [2026-05-31] 어댑터 시그니처에 맞춰 인자 정정.
            # quantity_reports / current_policy / transport_pack / transport_eco /
            # schedule_payload — 탭 진입 핸들러와 동일한 키.
            quantity_reports = getattr(self._analysis_panel, '_quantity_reports', None) or None
            current_policy = getattr(self._analysis_panel, '_current_policy', '3종')
            transport_pack = getattr(self, '_last_transport_pack', None)
            transport_tab = getattr(self._analysis_panel, '_transport_tab', None)
            transport_eco = getattr(transport_tab, '_last_eco', None) if transport_tab is not None else None
            section_types = self._extract_section_types()
            scene_components_by_id = dict(self._scene.components) if hasattr(self._scene, 'components') else {}
            evaluation_data = build_evaluation_data(
                components=comps,
                project_settings=self._project_settings,
                quantity_reports=quantity_reports,
                current_policy=current_policy,
                transport_pack=transport_pack,
                transport_eco=transport_eco,
                schedule_payload=getattr(self, '_schedule_payload', None) or None,
                section_types=section_types,
                scene_components_by_id=scene_components_by_id,
            )
            # [2026-05-31 v2] 비교탭 표시용 — 전체 배치 fit-all 캡처.
            # (main_3d 에 typing.Optional 미import → 지역 annotation 생략)
            layout_png_bytes = None
            try:
                from PyQt5.QtCore import QBuffer, QIODevice
                pm = self._capture_layout_full_pixmap()
                if pm is not None and not pm.isNull():
                    buf = QBuffer()
                    buf.open(QIODevice.WriteOnly)
                    pm.save(buf, "PNG")
                    layout_png_bytes = bytes(buf.data())
                    buf.close()
            except Exception as e:
                dprint('EVALUATION', f'[EVALUATION] 평면도 캡처 실패(저장): {e}')
            save_case(
                path=path,
                scene_state=scene_state,
                project_settings=self._project_settings,
                evaluation_data=evaluation_data,
                layout_png_bytes=layout_png_bytes,
            )
            QMessageBox.information(
                self, "평가 결과 저장",
                f"케이스 파일을 저장했습니다.\n{path}",
            )
        except Exception as e:
            dprint('EVALUATION', f'[EVALUATION] 저장 실패: {e}')
            QMessageBox.critical(self, "저장 실패", f"케이스 저장 중 오류:\n{e}")

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
        # UI 마이그레이션 M2 — Strangler 세로 분할: 상단 vispy(탭 전환마다 reparent)
        # / 하단 three.js(영구 부착). 다른 탭 진입 시 vispy 만 빠져나가고 three.js 는
        # 디자인 탭에 그대로 남는다(보이지 않을 뿐). 비율 1:1 시작.
        self._design_left_split = QSplitter(Qt.Vertical)
        self._design_left_top = QWidget()    # vispy 자리 (탭 전환 시 reparent 대상)
        self._design_left_top_lay = QVBoxLayout(self._design_left_top)
        self._design_left_top_lay.setContentsMargins(0, 0, 0, 0)
        self._design_left_split.addWidget(self._design_left_top)
        self._design_left_split.addWidget(self._three_widget)
        self._design_left_split.setStretchFactor(0, 0)
        self._design_left_split.setStretchFactor(1, 1)
        # M8-b — vispy 3D 표시 제거: 상단(vispy 자리) 0, 하단 three.js 전체.
        self._design_left_split.setSizes([0, 800])
        self._design_left_split.setChildrenCollapsible(True)
        self._design_left_lay.addWidget(self._design_left_split, stretch=1)
        # [2026-05-30 B3] 3D 뷰 바로 아래 z축 단면(수평 평면 절단) 슬라이더.
        self._design_left_lay.addWidget(self._build_section_slider())
        # [2026-06-02] 벽 채움면 / 실 표기 표시 토글 (체크박스 2개). (내 작업)
        self._design_left_lay.addWidget(self._build_wall_room_toggles())
        # [2026-06-01] Y축 단면 슬라이더 — Z축과 같은 패턴, normal (0,-1,0) (팀원)
        self._design_left_lay.addWidget(self._build_section_y_slider())
        self._design_right_pane = QWidget()
        # M3-b 마운트는 빌더 끝에서 _design_right_lay 안에 세로 분할 추가
        # (코드는 아래에서 _design_right_lay 정의 후).
        self._design_right_lay = QVBoxLayout(self._design_right_pane)
        self._design_right_lay.setContentsMargins(0, 0, 0, 0)
        # UI 마이그레이션 M3-b — 우측 2D 도 세로 분할: 상단 vispy AlignmentDockPanel
        # / 하단 three.js AlignmentDockPanelThree. 좌측 3D 와 같은 Strangler 패턴.
        self._design_right_split = QSplitter(Qt.Vertical)
        self._design_right_top = QWidget()    # f5_panel 자리 (탭 전환 시 reparent)
        self._design_right_top_lay = QVBoxLayout(self._design_right_top)
        self._design_right_top_lay.setContentsMargins(0, 0, 0, 0)
        self._design_right_split.addWidget(self._design_right_top)
        self._design_right_split.addWidget(self._f5_panel_three)
        self._design_right_split.setStretchFactor(0, 0)
        self._design_right_split.setStretchFactor(1, 1)
        # M8-b — vispy 2D 캔버스 제거: 상단(_f5_panel)은 *상단 컨트롤만* 얇게
        # 남기고(hide_canvas), 하단 three.js 가 평면뷰 전체.
        self._design_right_split.setSizes([40, 800])
        self._design_right_split.setChildrenCollapsible(True)
        self._design_right_lay.addWidget(self._design_right_split, stretch=1)
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

        # 우측 속성 패널 — 부재 속성 + 실 속성(선택 시 전환)
        right = QWidget()
        right.setFixedWidth(320)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(self._design_props)
        rv.addWidget(self._room_props)
        self._room_props.setVisible(False)
        # [2026-06-02] 단면 정보 버튼 제거 — 비교탭에서 case 의 scene 으로
        # 단면을 직접 그리므로 배치설계 탭의 검증용 버튼 불필요.
        rv.addStretch(1)
        h.addWidget(right)
        return page

    def _build_section_slider(self) -> QWidget:
        """[B3] 3D 화면 z축 단면(수평 평면 절단) 컨트롤 — 체크박스 + 슬라이더.

        체크 ON 이면 슬라이더 높이(z, mm) 위쪽을 잘라 평면을 본다(three.js 클리핑).
        """
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(6, 2, 6, 2)
        self._section_check = QCheckBox('단면')
        self._section_check.setToolTip(
            '체크 시 슬라이더 높이에서 위쪽을 잘라 평면을 봅니다.')
        lay.addWidget(self._section_check)
        self._section_slider = QSlider(Qt.Horizontal)
        # 범위는 단면을 켤 때 -100 ~ 건물 최고점으로 동적 갱신(_update_section_range).
        self._section_slider.setRange(-100, 10000)
        self._section_slider.setValue(10000)
        self._section_slider.setSingleStep(100)
        lay.addWidget(self._section_slider, stretch=1)
        self._section_label = QLabel('z = 10000 mm')
        self._section_label.setFixedWidth(120)
        lay.addWidget(self._section_label)
        self._section_check.toggled.connect(self._on_section_toggled)
        self._section_slider.valueChanged.connect(self._on_section_changed)
        box.setMaximumHeight(34)
        return box

    def _build_wall_room_toggles(self) -> QWidget:
        """[2026-06-02] 배치설계 표시 토글 — 벽 채움면 + 실 표기를 한 체크박스로.

        '벽·실' 하나로 묶어 동시에 끄고 켠다. 벽=구조벽 채움·내벽(칸막이)·모듈
        외피 벽 채움(기둥·런너 프레임·슬래브·코어벽은 유지), 실=실 표기 반투명
        색면. 기본 ON.
        """
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(6, 2, 6, 2)
        self._wall_room_check = QCheckBox('벽·실 표시')
        self._wall_room_check.setChecked(True)
        self._wall_room_check.setToolTip(
            '벽 채움면(구조벽 채움·내벽·모듈 외피 벽)과 실 표기를 함께 끄거나 '
            '켭니다. 프레임·슬래브·코어벽은 항상 표시됩니다.')
        self._wall_room_check.toggled.connect(self._on_wall_room_toggled)
        lay.addWidget(self._wall_room_check)
        lay.addStretch(1)
        box.setMaximumHeight(34)
        return box

    def _on_wall_room_toggled(self, checked: bool) -> None:
        if hasattr(self._controller, 'set_wall_fill_visible'):
            self._controller.set_wall_fill_visible(checked)
        if hasattr(self._controller, 'set_rooms_visible'):
            self._controller.set_rooms_visible(checked)

    def _update_section_range(self):
        """단면 슬라이더 범위를 -100 ~ 건물 최고점(코어 옥탑 슬래브 끝)으로 갱신.

        장면의 모든 부재 메쉬 z 최댓값을 건물 최고점으로 본다(코어 옥탑 슬래브 포함).
        """
        from modular_3d.render.mesh_builder import build_component_mesh
        z_max = 0.0
        for comp in self._scene.components.values():
            try:
                v, _f, _c = build_component_mesh(comp)
            except Exception:
                continue
            if v is not None and len(v):
                z_max = max(z_max, float(v[:, 2].max()))
        z_min = -100
        z_max = max(int(round(z_max)), z_min + 100)
        self._section_slider.blockSignals(True)
        self._section_slider.setRange(z_min, z_max)
        self._section_slider.setValue(z_max)   # 켜는 순간엔 전체가 보이는 위치
        self._section_slider.blockSignals(False)

    def _on_section_toggled(self, checked: bool):
        """단면 체크 토글 — 켜질 때 슬라이더 범위를 현재 건물 높이에 맞춘다."""
        if checked:
            self._update_section_range()
        self._on_section_changed()

    def _on_section_changed(self, *_):
        """단면 체크/슬라이더 변경 → three.js 클리핑 평면 갱신."""
        z = float(self._section_slider.value())
        enabled = self._section_check.isChecked()
        self._section_label.setText(f'z = {int(z)} mm')
        three = getattr(self._viewer, 'three', None)
        if three is not None and hasattr(three, 'set_section_z'):
            three.set_section_z(z, enabled)

    # ── [2026-06-01] Y축 단면 (Z축 슬라이더와 동일 패턴) ─────────────
    def _build_section_y_slider(self) -> QWidget:
        """Y축 단면(수직 평면 절단) 컨트롤 — 체크박스 + 슬라이더 + 라벨.

        체크 ON 이면 슬라이더 y(mm) 한쪽을 잘라 평면을 본다(three.js 클리핑).
        normal (0,-1,0) → y < constant 영역만 보임.
        """
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(6, 2, 6, 2)
        self._section_y_check = QCheckBox('단면 Y')
        self._section_y_check.setToolTip(
            '체크 시 슬라이더 y 값 한쪽을 잘라 평면을 봅니다.')
        lay.addWidget(self._section_y_check)
        self._section_y_slider = QSlider(Qt.Horizontal)
        self._section_y_slider.setRange(-10000, 10000)
        # [2026-06-01] 반대 방향 시작 — 최솟값에서 전체 보임
        self._section_y_slider.setValue(-10000)
        self._section_y_slider.setSingleStep(100)
        lay.addWidget(self._section_y_slider, stretch=1)
        self._section_y_label = QLabel('y = -10000 mm')
        self._section_y_label.setFixedWidth(120)
        lay.addWidget(self._section_y_label)
        self._section_y_check.toggled.connect(self._on_section_y_toggled)
        self._section_y_slider.valueChanged.connect(self._on_section_y_changed)
        box.setMaximumHeight(34)
        return box

    def _update_section_y_range(self):
        """Y 슬라이더 범위를 건물 y 범위로 갱신."""
        from modular_3d.render.mesh_builder import build_component_mesh
        y_min = 0.0
        y_max = 0.0
        first = True
        for comp in self._scene.components.values():
            try:
                v, _f, _c = build_component_mesh(comp)
            except Exception:
                continue
            if v is not None and len(v):
                cy_min = float(v[:, 1].min())
                cy_max = float(v[:, 1].max())
                if first:
                    y_min, y_max = cy_min, cy_max
                    first = False
                else:
                    y_min = min(y_min, cy_min)
                    y_max = max(y_max, cy_max)
        # 여유 100 mm
        y_min = int(round(y_min)) - 100
        y_max = int(round(y_max)) + 100
        if y_max <= y_min:
            y_max = y_min + 1000
        self._section_y_slider.blockSignals(True)
        self._section_y_slider.setRange(y_min, y_max)
        # [2026-06-01] 반대 방향 — 슬라이더 최솟값에서 전체 보임, 올리면 작은 y 쪽부터 잘림
        self._section_y_slider.setValue(y_min)
        self._section_y_slider.blockSignals(False)

    def _on_section_y_toggled(self, checked: bool):
        if checked:
            self._update_section_y_range()
        self._on_section_y_changed()

    def _on_section_y_changed(self, *_):
        y = float(self._section_y_slider.value())
        enabled = self._section_y_check.isChecked()
        self._section_y_label.setText(f'y = {int(y)} mm')
        three = getattr(self._viewer, 'three', None)
        if three is not None and hasattr(three, 'set_section_y'):
            three.set_section_y(y, enabled)

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

    def _build_section_design_tab(self) -> QWidget:
        """단면 설계 탭: 중앙(공유 3D 와이어프레임) + 우 옵션 패널(P2=빈 골격).

        [2026-05-31 P2] 좌측은 구조해석과 같은 공유 vispy 캔버스를 reparent 해서
        쓴다(진입 시 _on_tab_changed 에서 마운트). 우측은 P3 에서 옵션 위젯이,
        P4 에서 타입 목록 + 컴포넌트 3D 가 채워질 자리. 지금은 안내 라벨만.
        """
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self._section_center_pane = QWidget()
        self._section_center_lay = QVBoxLayout(self._section_center_pane)
        self._section_center_lay.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self._section_center_pane, stretch=1)
        h.addWidget(center, stretch=1)

        # 우측 — 옵션 패널(P3). P4 에서 타입목록 + 컴포넌트 3D 가 패널 하단에 붙는다.
        self._section_right_pane = QWidget()
        self._section_right_pane.setMinimumWidth(380)
        self._section_right_pane.setSizePolicy(_QSP.Minimum, _QSP.Expanding)
        self._section_right_lay = QVBoxLayout(self._section_right_pane)
        self._section_right_lay.setContentsMargins(0, 0, 0, 0)
        from .section_design_panel import SectionDesignPanel
        self._section_panel = SectionDesignPanel()
        # '적용' → 현재 옵션으로 재수렴 + 색 갱신.
        self._section_panel.apply_requested.connect(self._apply_section_design)
        # 타입 목록 선택 → 그 타입 컴포넌트 단일 3D 갱신(P4b-1).
        self._section_panel.type_selected.connect(self._on_section_type_list_selected)
        # [9-7] 단면 변경 — 콤보 바꾸면 즉시 대상에 반영·재수렴·전파.
        self._section_panel.section_changed.connect(self._on_section_change)
        self._section_locks = {}            # {comp_id: {색클래스: 단면}}
        self._comp_type_label = {}          # comp_id → 타입 라벨(최근 수렴 기준)
        self._sel_section_type_label = None  # 현재 선택 타입 라벨
        self._section_selected_comp_id = None  # [7B-2] 좌측 3D 로 선택한 컴포넌트
        self._section_sel_mode = 'type'      # [7B-3] 'type'(목록) | 'single'(3D 클릭)
        self._section_right_lay.addWidget(self._section_panel)
        h.addWidget(self._section_right_pane)
        return page

    def _build_quantity_tab(self) -> QWidget:
        """물량 탭: 중앙(운송식 3D 뷰어) + 우 QuantityPanel(타입별 비용).

        [2026-05-31 물량탭 개편 Phase 4] 좌측 와이어프레임(_canvas_widget) 폐지.
        중앙 = QWebEngineView 물량 3D(트럭 없이 타입 평면 배치), 우 = QuantityPanel.
        """
        from PyQt5.QtWebEngineWidgets import QWebEngineView
        from PyQt5.QtWebChannel import QWebChannel
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        from modular_3d.ui.quantity_panel import QuantityPanel

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

        # ── 물량 3D WebEngineView (운송과 동일 패턴, hide_truck) ──
        self._quantity_3d_web = QWebEngineView()
        self._quantity_3d_web.setMinimumHeight(400)
        # WebChannel + Bridge — 3D 타입(=trip) 클릭 시 bridge.on_truck_clicked(idx).
        # 운송 _TransportBridge 재사용(같은 시그니처).
        self._quantity_bridge = _TransportBridge()
        self._quantity_channel = QWebChannel()
        self._quantity_channel.registerObject("bridge", self._quantity_bridge)
        self._quantity_3d_web.page().setWebChannel(self._quantity_channel)
        self._quantity_bridge.truck_clicked_in_3d.connect(
            self._on_quantity_type_clicked_3d)
        self._quantity_3d_web.setHtml(self._quantity_empty_html(
            "물량 산출 3D", "단면 설계를 먼저 실행해 주세요"))
        self._quantity_center_lay.addWidget(self._quantity_3d_web)

        # 우측 QuantityPanel — 타입별 비용 트리 + 전체 본수표·자재비표.
        self._quantity_right_pane = QWidget()
        self._quantity_right_pane.setMinimumWidth(380)
        self._quantity_right_pane.setSizePolicy(_QSP.Minimum, _QSP.Expanding)
        self._quantity_right_lay = QVBoxLayout(self._quantity_right_pane)
        self._quantity_right_lay.setContentsMargins(0, 0, 0, 0)
        self._quantity_panel = QuantityPanel()
        self._quantity_panel.type_selected.connect(
            self._on_quantity_type_selected_tree)
        self._quantity_right_lay.addWidget(self._quantity_panel)
        h.addWidget(self._quantity_right_pane)
        return page

    @staticmethod
    def _quantity_empty_html(title: str, msg: str) -> str:
        """물량 3D 빈/안내 화면 HTML (어두운 테마, 운송 빈 화면과 동일 톤)."""
        return (
            "<html><body style='margin:0;padding:0;background:#0d1117;'>"
            "<div style='display:flex;align-items:center;justify-content:center;"
            "height:100vh;font-family:Segoe UI,sans-serif;color:#8b949e;'>"
            "<div style='text-align:center;'>"
            "<div style='font-size:52px;margin-bottom:18px;'>📦</div>"
            f"<div style='color:#e6edf3;font-size:18px;font-weight:600;'>{title}</div>"
            f"<div style='margin-top:12px;color:#6e7681;font-size:14px;'>{msg}</div>"
            "</div></div></body></html>"
        )

    def _build_transport_tab_layout(self) -> QWidget:
        """운송 탭 — *2 단 분할* (중 3D 도식 / 우 결과).

        [개편 Phase 4]
        - 좌측 입력 패널 제거 — 옵션·참고자료는 TransportTab 우측 결과 패널로 통합됨.
        - 중앙: 운송 3D 적재 도식(WebEngineView) + 우상단 [▷ 운송 계산 실행] 오버레이.
          실행 버튼은 TransportTab 소유(_run_btn) 이며 탭 진입 시 center pane 으로 reparent.
        - 우측: TransportTab 의 결과 패널(옵션 / 회차표 / 적재율 / 경제성).
        """
        from PyQt5.QtWebEngineWidgets import QWebEngineView
        from PyQt5.QtWidgets import QSplitter
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 메인 2 단 splitter (중앙 3D + 우 결과)
        self._transport_splitter = QSplitter(Qt.Horizontal)
        # 중 영역
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self._transport_center_pane = QWidget()
        self._transport_center_lay = QVBoxLayout(self._transport_center_pane)
        self._transport_center_lay.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self._transport_center_pane, stretch=1)
        # center pane resize → 실행 버튼 오버레이 우상단 추종(eventFilter 분기).
        self._transport_center_pane.installEventFilter(self)

        # 우 영역
        self._transport_right_pane = QWidget()
        self._transport_right_pane.setMinimumWidth(400)
        self._transport_right_lay = QVBoxLayout(self._transport_right_pane)
        self._transport_right_lay.setContentsMargins(0, 0, 0, 0)

        self._transport_splitter.addWidget(center)
        self._transport_splitter.addWidget(self._transport_right_pane)
        self._transport_splitter.setStretchFactor(0, 2)
        self._transport_splitter.setStretchFactor(1, 1)
        self._transport_splitter.setSizes([900, 500])
        root.addWidget(self._transport_splitter, stretch=1)

        # ── 운송 3D 도식 WebEngineView (center pane 안에 들어갈 위젯) ──
        self._transport_3d_web = QWebEngineView()
        self._transport_3d_web.setMinimumHeight(400)
        # [양방향 동기화 — 2026-05-27] WebChannel + Bridge 등록.
        # Three.js JS 에서 트럭 클릭 시 bridge.on_truck_clicked(trip_no) 호출 가능.
        self._transport_bridge = _TransportBridge()
        self._transport_channel = QWebChannel()
        self._transport_channel.registerObject("bridge", self._transport_bridge)
        self._transport_3d_web.page().setWebChannel(self._transport_channel)
        self._transport_bridge.truck_clicked_in_3d.connect(
            self._on_3d_truck_clicked
        )
        # 초기 안내 HTML — Three.js 스타일에 맞춘 어두운 테마
        empty_html = (
            "<html><body style='margin:0;padding:0;background:#0d1117;'>"
            "<div style='display:flex;align-items:center;justify-content:center;"
            "height:100vh;font-family:Segoe UI,sans-serif;"
            "color:#8b949e;font-size:16px;'>"
            "<div style='text-align:center;'>"
            "<div style='font-size:56px;margin-bottom:20px;'>🚚</div>"
            "<div style='color:#e6edf3;font-size:18px;font-weight:600;'>"
            "운송 적재 3D 도식</div>"
            "<div style='margin-top:14px;color:#6e7681;font-size:14px;'>"
            "우측에서 <span style='color:#58a6ff;font-weight:500;'>"
            "[▷ 운송 계산 실행]</span> 버튼을 눌러주세요"
            "</div></div></div></body></html>"
        )
        self._transport_3d_web.setHtml(empty_html)

        return page

    def _position_transport_run_btn(self) -> None:
        """[Phase 4] 운송 실행 버튼(_run_btn)을 중앙 3D pane 우상단에 오버레이 배치.

        center pane resize 시 eventFilter 가 이 메서드를 호출해 우상단을 추종한다.
        버튼은 TransportTab 소유이며 탭 진입 시 center pane 으로 reparent 된 상태.
        """
        cp = getattr(self, '_transport_center_pane', None)
        tt = getattr(self._analysis_panel, '_transport_tab', None)
        btn = getattr(tt, '_run_btn', None) if tt is not None else None
        if cp is None or btn is None:
            return
        btn.adjustSize()
        margin = 12
        x = max(0, cp.width() - btn.width() - margin)
        btn.move(x, margin)
        btn.raise_()

    def _on_transport_pack_updated(self, pack, sp) -> None:
        """[Phase C] 운송 패널 계산 완료 → center pane 3D 도식 갱신.

        TransportTab 의 transport_pack_updated 신호에 연결. PackResult + SpacingParams
        를 받아 draw_loaded_3d_view 로 Plotly Figure 생성 후 임시 HTML 파일에
        저장하고 WebEngineView 에 file:// URL 로 로드.

        [Phase E] 최근 pack/sp 를 캐시해두면 회차 클릭 시 강조 재렌더 시 사용.
        회차별 비용 (trip_costs) 도 운송 패널의 마지막 economics 결과에서 추출.
        """
        # Phase E — 강조 재렌더용 캐시
        self._last_transport_pack = pack
        self._last_transport_sp = sp
        self._render_transport_3d(pack, sp, highlight_trip_no=None)

    def _on_transport_trip_clicked(self, trip_no: int) -> None:
        """[Phase E + Three.js 마이그레이션] 회차표 행 클릭 → JS 함수 호출로
        ① 빨간 outline 강조 + ② 카메라 평행이동 (X·Z 만, 거리·각도·Y 그대로).
        둘 다 페이지 reload 없이 — JS API 만 호출.
        """
        try:
            page = self._transport_3d_web.page()
            page.runJavaScript(
                f"window.highlightTrip({int(trip_no)}); "
                f"window.focusTrip({int(trip_no)});"
            )
        except Exception as e:
            log_error(f"운송 3D 강조 실패: {type(e).__name__}: {e}", cat='main_3d', exc=True)

    def _on_3d_truck_clicked(self, trip_no: int) -> None:
        """[양방향 동기화 — 2026-05-27] 3D 트럭 클릭 → 회차표·경제성표 행 선택.

        TransportTab.select_trip 이 통합 진입점 — 회차표·경제성표 둘 다 선택 + 3D
        강조 신호 emit. 무한 루프 없음 (selectRow 가 cellClicked 발화 안 함).
        """
        try:
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None and hasattr(tt, 'select_trip'):
                tt.select_trip(int(trip_no))
        except Exception as e:
            log_error(f"3D→회차표 동기화 실패: {type(e).__name__}: {e}", cat='main_3d', exc=True)

    def _render_transport_3d(self, pack, sp, highlight_trip_no=None) -> None:
        """[Three.js 마이그레이션 — 2026-05-26] 운송 3D 도식 렌더.

        - modular_designer.html 동일 스타일 (어두운 배경 + 그림자 + OrbitControls)
        - 회차 클릭 시 페이지 reload 없이 JS 함수 호출로 부분 갱신 → 카메라 보존
        - 마우스: 왼클릭=회전, 우클릭=이동, 휠=줌

        Phase C 의 _on_transport_pack_updated 와 Phase E 강조 재렌더(=초기 강조)가
        같은 함수를 호출. 단 *클릭 강조* 는 별도 _on_transport_trip_clicked 가
        JS runJavaScript 로 부분 갱신 → 이 함수는 *새 pack 결과 도착 시* 한 번 호출.
        """
        try:
            from modular_3d.transport.loaded_3d_three import (
                build_loaded_3d_three_html,
            )
            from PyQt5.QtCore import QUrl

            if pack is None or not pack.trips:
                empty_html = (
                    "<div style='display:flex;align-items:center;justify-content:center;"
                    "height:100vh;font-family:sans-serif;background:#0d1117;"
                    "color:#8b949e;font-size:16px;'>"
                    "<div style='text-align:center;'>"
                    "<div style='font-size:48px;margin-bottom:20px;'>🚚</div>"
                    "<div><b>회차 없음</b></div>"
                    "<div style='margin-top:10px;color:#6e7681;font-size:14px;'>"
                    "운송 가능 화물이 없거나 패킹 결과가 비어있습니다"
                    "</div></div></div>"
                )
                self._transport_3d_web.setHtml(empty_html)
                return

            # 회차별 비용 추출
            trip_costs = {}
            try:
                tt = getattr(self._analysis_panel, '_transport_tab', None)
                last_eco = getattr(tt, '_last_eco', None) if tt else None
                if last_eco is not None:
                    for tc in last_eco.trips:
                        trip_costs[tc.trip_no] = tc.cost_krw
            except Exception:
                trip_costs = {}

            html = build_loaded_3d_three_html(
                pack.trips, sp,
                highlight_trip_no=highlight_trip_no,
                trip_costs=trip_costs,
            )
            path = self._write_transport_3d_temp_html(html)
            if path:
                self._transport_3d_web.load(QUrl.fromLocalFile(path))
            else:
                self._transport_3d_web.setHtml(html)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log_error(f"운송 3D 렌더 실패: {tb}", cat='main_3d')
            err_html = (
                "<div style='padding:20px;font-family:sans-serif;background:#0d1117;"
                "color:#f85149;'>"
                "<b>운송 3D 도식 렌더링 실패</b><br>"
                f"{type(e).__name__}: {e}<br>"
                "<small style='color:#8b949e;'>콘솔에서 traceback 확인</small></div>"
            )
            try:
                self._transport_3d_web.setHtml(err_html)
            except Exception:
                pass

    # ── 물량탭 3D + 우측 패널 렌더 (Phase 4) ─────────────────
    def _render_quantity_view(self) -> None:
        """단면설계 단일출처 → 타입별 물량 분해 → 물량 3D + QuantityPanel 갱신.

        [데이터 흐름 — 컨트롤러 캐시/단일출처 재사용, 독립 재변환 최소화]
          self._section_result(ConvergeResult)
            → derive_section_types(types) + converge_result_to_design_result(dr) + result.am
            → build_quantity_by_component(qbc) / build_quantity_report(report)+MaterialCost(mc)
            → build_transport_input(treat_v3_module_as_lying=False) → item(부재 형상)
            → 타입별 items 묶기 → build_quantity_3d_html → WebEngine 로드
            → QuantityPanel.populate(qbc, report, mc)
        [단면설계 None/빈 방어] → 안내 화면 + 우측 비움(사용자 확정 B).
        """
        from PyQt5.QtCore import QUrl
        result = getattr(self, '_section_result', None)
        if result is None or getattr(result, 'am', None) is None:
            self._quantity_3d_web.setHtml(self._quantity_empty_html(
                "물량 산출 3D", "단면 설계를 먼저 실행해 주세요"))
            if hasattr(self, '_quantity_panel'):
                self._quantity_panel.clear()
            return
        try:
            from modular_3d.analysis.section_converge import (
                derive_section_types, converge_result_to_design_result)
            from modular_3d.analysis.quantity_by_component import (
                build_quantity_by_component, UnitPrices)
            from modular_3d.analysis.quantity_takeoff import (
                build_quantity_report, compute_material_cost)
            from modular_3d.transport.adapter import (
                build_transport_input, TransportOptions)
            from modular_3d.transport.loaded_3d_three import build_quantity_3d_html

            # [단일출처 재사용] 컨트롤러의 _quantity_from_section_design 이 이미
            # converge_result_to_design_result 로 (정책키 dr, am) 을 만든다. 그것을
            # 우선 재사용(독립 재변환 금지, §2-6). 없으면 직접 변환 폴백.
            dr = None
            am = None
            ctrl = getattr(self, '_controller', None)
            if ctrl is not None and hasattr(ctrl, '_quantity_from_section_design'):
                sd = ctrl._quantity_from_section_design()
                if sd is not None:
                    design_results, am = sd
                    # 세 정책 동일 dr — 아무 정책이나 단일 출처.
                    dr = next(iter(design_results.values()))
            if dr is None:
                dr = converge_result_to_design_result(result)
                am = result.am
            if not dr.groups:
                self._quantity_3d_web.setHtml(self._quantity_empty_html(
                    "물량 산출 3D", "단면 설계 결과가 비어 있습니다"))
                if hasattr(self, '_quantity_panel'):
                    self._quantity_panel.clear()
                return
            types, _comp_label = derive_section_types(result, self._scene)
            up = UnitPrices.load()
            qbc = build_quantity_by_component(self._scene, am, dr, types, up)
            # [2026-06-01] 하단 전체 본수표·자재비표는 *코어 강재 제외* dr 로 산출 →
            # 타입별 분해(코어 제외)와 합계 일치. 코어는 RC 라 강재 물량서 제외(사용자 확정).
            from modular_3d.analysis.quantity_by_component import (
                design_result_without_core)
            dr_no_core = design_result_without_core(self._scene, am, dr)
            report = build_quantity_report(self._scene, am, dr_no_core)
            mc = compute_material_cost(report, up.steel_shs, up.deck, up.concrete,
                                       steel_h_unit_per_ton=up.steel_h)

            # 우측 패널 갱신.
            if hasattr(self, '_quantity_panel'):
                self._quantity_panel.populate(qbc, report, mc)
                # 패널 내용폭으로 우측 pane 을 *고정* — minimumWidth 만으로는 이미
                # 넓어진 폭이 안 줄어든다(단면 탭과 동일 정책, L6).
                if hasattr(self, '_quantity_right_pane'):
                    _qw = self._quantity_panel.minimumWidth()
                    self._quantity_right_pane.setMinimumWidth(0)
                    self._quantity_right_pane.setFixedWidth(_qw)

            # 3D — 어댑터로 부재 형상 item 생성(v3 세움). cid→item 은 source_index.
            ti = build_transport_input(
                self._scene, am, dr, '단면설계',
                TransportOptions(treat_v3_module_as_lying=False))
            name_to_item = {}
            for it in list(ti.modules) + list(ti.panels):
                name_to_item[it.name] = it
            cid_to_item = {}
            for name, cids in ti.source_index.items():
                it = name_to_item.get(name)
                if it is None:
                    continue
                for c in cids:
                    cid_to_item[c] = it

            items_by_type = []
            missing = 0
            for tq in qbc.types:
                items = []
                for cid in tq.member_cids:
                    it = cid_to_item.get(cid)
                    if it is None:
                        missing += 1
                        continue
                    items.append(it)
                items_by_type.append({
                    'label': tq.type_label,
                    'items': items,
                    'section_lines': tq.section_lines,
                })
            if missing:
                # 타입↔item 불일치 진단(§4-보강): 단면 룩업 실패 등으로 item 누락.
                print(f"[물량 3D] item 없는 cid {missing}개 스킵 "
                      f"(단면 미배정/어댑터 제외 가능)", flush=True)

            html = build_quantity_3d_html(items_by_type)
            path = self._write_quantity_3d_temp_html(html)
            if path:
                self._quantity_3d_web.load(QUrl.fromLocalFile(path))
        except Exception as e:
            log_error(f"물량 3D 렌더 실패: {type(e).__name__}: {e}", cat='main_3d', exc=True)
            self._quantity_3d_web.setHtml(self._quantity_empty_html(
                "물량 산출 3D", f"렌더 오류: {type(e).__name__}"))

    def _on_quantity_type_selected_tree(self, type_idx: int) -> None:
        """우측 트리에서 타입 선택 → 3D 강조 + 화면 밖이면 카메라 이동(focusTrip).

        운송탭과 동일 패턴 — highlightTrip(강조) + focusTrip(시야 밖일 때만 평행이동).
        """
        try:
            page = self._quantity_3d_web.page()
            # trip_no = 타입 idx + 1 (serialize_quantity_for_three 규약).
            tno = int(type_idx) + 1
            page.runJavaScript(
                f"window.highlightTrip({tno}); window.focusTrip({tno});")
        except Exception as e:
            log_error(f"물량 트리→3D 강조 실패: {type(e).__name__}: {e}", cat='main_3d', exc=True)

    def _on_quantity_type_clicked_3d(self, trip_no: int) -> None:
        """3D 에서 타입(=trip) 클릭 → 우측 트리 해당 타입 행 선택."""
        try:
            idx = int(trip_no) - 1  # trip_no = 타입 idx + 1
            panel = getattr(self, '_quantity_panel', None)
            tree = getattr(panel, '_tree', None) if panel else None
            if tree is not None and 0 <= idx < tree.topLevelItemCount():
                tree.setCurrentItem(tree.topLevelItem(idx))
            # 3D 자체 강조도 갱신.
            self._quantity_3d_web.page().runJavaScript(
                f"window.highlightTrip({int(trip_no)});")
        except Exception as e:
            log_error(f"물량 3D→트리 동기화 실패: {type(e).__name__}: {e}", cat='main_3d', exc=True)

    def _write_quantity_3d_temp_html(self, html: str) -> str:
        """물량 3D HTML 임시 저장 — 운송과 같은 폴백, 별도 파일명."""
        return self._write_transport_3d_temp_html(
            html, filename="quantity_3d_loaded.html")

    def _write_transport_3d_temp_html(self, html: str,
                                       filename: str = "transport_3d_loaded.html") -> str:
        """ASCII 경로 임시 파일에 HTML 저장 — file:// URL 로 로드 가능.

        한글 경로 회피 위해 시스템 temp / 가상드라이브 / ProgramData 폴백.
        filename 으로 운송/물량 3D 를 다른 파일에 쓴다(상호 덮어쓰기 방지).
        """
        import tempfile
        candidates = []
        sys_tmp = tempfile.gettempdir()
        if sys_tmp.isascii():
            candidates.append(sys_tmp)
        for letter in "QRSTUVWXYZ":
            if os.path.exists(letter + ":\\"):
                candidates.append(letter + ":\\Temp")
                break
        candidates.append("C:\\ProgramData\\modular3d_temp")
        for d in candidates:
            try:
                os.makedirs(d, exist_ok=True)
                path = os.path.join(d, filename)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
                if path.isascii():
                    return path
            except Exception:
                continue
        return ""

    # ── 탭 전환 시 공유 위젯 reparent ────────────────────

    def _on_tab_changed(self, idx: int):
        # 모든 페이지의 placeholder layout 비우기 — 이전 reparent 잔재 제거
        # (Qt 는 widget 을 다른 layout 에 addWidget 하면 자동 reparent 하므로
        # 명시적 removeWidget 없이 진행 가능. 단, hide 처리만)

        # [2026-05-27] AnalysisPanel 상단 컨트롤 + 탭바 기본 표시. 운송 탭 분기에서만
        # 다시 hide 한다. 다른 탭(구조해석·물량·접합부 등) 으로 이동 시 자동 복원.
        if hasattr(self._analysis_panel, 'set_visualization_controls_visible'):
            self._analysis_panel.set_visualization_controls_visible(True)

        # [2026-05-13 접합부조정탭 Phase 4] 직전 탭 추적.
        # 접합부 조정 탭에서 구조해석/물량 탭으로 이동할 때는 본 탭에서 토글/
        # 수정한 결합 변경 사항이 다음 빌드에 반영돼야 하므로 ops 뷰를 강제
        # 무효화 — _run_analysis_if_needed 가 OFF 상태에서 새 빌드 + solve 를
        # 부르게 한다.
        prev_idx = getattr(self, '_prev_tab_idx', None)
        self._prev_tab_idx = idx
        # [2026-05-25] 접합 탭을 떠나면 접합 선택 강조 제거(다른 탭에 잔상 방지).
        if prev_idx == TAB_JOINT_EDIT and idx != TAB_JOINT_EDIT:
            if hasattr(self._viewer, 'clear_ops_joint_highlight'):
                self._viewer.clear_ops_joint_highlight()
            self._joint_ctrl.clear_selection()
        # [7B-4b] 단면 설계 탭을 떠나면 빨강 하이라이트(공유 pinned 시각) 해제.
        if prev_idx == TAB_SECTION and idx != TAB_SECTION:
            try:
                self._viewer.update_pinned_members([])
            except Exception:
                pass
        if (prev_idx == TAB_JOINT_EDIT
                and idx in (TAB_ANALYSIS, TAB_SECTION, TAB_QUANTITY)):
            v = self._viewer
            if hasattr(v, 'is_ops_view_active') and v.is_ops_view_active():
                v.hide_ops_view()
                dprint('ANALYSIS', '[ANALYSIS] 접합부 조정 → 구조해석/물량 — ops 뷰 무효화 후 재빌드 예정')

        # [9C-1 임시 진단] 배치·단면 설계 탭 진입 시 타입 라벨/시그니처 콘솔 출력.
        #   동일 모듈 병합 실패·c3 중복 원인(시그니처 차이) 파악용 — 확정 후 제거.
        if idx in (TAB_DESIGN, TAB_SECTION):
            try:
                from modular_3d.model.type_naming import dump_type_signatures
                dump_type_signatures(self._scene)
            except Exception:
                pass

        # [P5c stale 무효화] 배치/정의 탭(모델 편집 진입) → 저장된 단면 설계 수렴
        # 결과 폐기. 모델이 바뀌면 옛 결과가 물량/트리에 쓰이지 않게(다음 단면 설계
        # 탭 진입 시 자동 재수렴). 안전한 단일 지점.
        if idx in (TAB_DESIGN, TAB_DEFINE):
            if hasattr(self._controller, 'set_section_design_result'):
                self._controller.set_section_design_result(None)

        if idx == TAB_DEFINE:
            # 모듈 정의 탭 — 자체 작업공간(별도 인스턴스)이라 공유 위젯 reparent
            # 불필요. 공유 dim/변형 위젯만 숨기고 정의 캔버스에 포커스.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            self._deformed_widget.hide()
            self._define_tab.on_enter()
            return

        if idx == TAB_DESIGN:
            # 2D F5 → 디자인 우(상단 컨트롤바), 치수 → 디자인 하단.
            # UI 마이그레이션 M8-b — 디자인 탭은 three.js 단독 표시.
            #  · 좌 3D: vispy 캔버스(_canvas_widget) *마운트 안 함* → three.js 만.
            #    (vispy 3D 는 접합부/해석/물량 탭에서 계속 공유 사용.)
            #  · 우 2D: _f5_panel 은 *상단 컨트롤(층수·저장·불러오기)만* 얇게 남기고
            #    캔버스는 hide_canvas() 로 숨김 → 평면뷰는 three.js 가 전담.
            self._design_right_top_lay.addWidget(self._f5_panel)
            self._f5_panel.hide_canvas()
            self._design_dim_lay.addWidget(self._dim_panel)
            self._dim_panel.setVisible(True)
            self._deformed_widget.hide()
            # [2026-05-11 v5] 구조해석 → 디자인 복귀 시 ops 와이어프레임/변형형상 등을 끄고
            # 일반 메쉬 뷰로 복원. 기존 F6 → F5 토글 시의 정리 코드와 동일.
            self._restore_design_view()
            # [2026-05-28] 실 색면 표시 ON(배치설계는 실 편집 탭).
            if hasattr(self._viewer, 'set_rooms_visible'):
                self._viewer.set_rooms_visible(True)
        elif idx == TAB_JOINT_EDIT:
            # [2026-05-13 접합부조정탭] canvas + 우측 패널 reparent + 프리뷰.
            # solve 미실행 — spec/ops 빌드만 하고 _draw_from_spec 으로 표시.
            self._joint_center_lay.addWidget(self._canvas_widget)
            self._joint_right_lay.addWidget(self._joint_panel)
            self._deformed_widget.hide()
            # [2026-05-28] 접합부 탭은 와이어프레임만 — 실 색면 숨김(공중부양 방지).
            if hasattr(self._viewer, 'set_rooms_visible'):
                self._viewer.set_rooms_visible(False)
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            # 불러오기 직후 첫 진입이면 저장본/새 계산 선택(프리뷰 전에).
            self._joint_ctrl._maybe_prompt_joint_choice()
            self._joint_ctrl._run_joint_edit_preview()
        elif idx == TAB_ANALYSIS:
            self._analysis_center_lay.addWidget(self._canvas_widget)
            self._analysis_center_lay.addWidget(self._deformed_widget)
            self._deformed_widget.show()
            self._analysis_right_lay.addWidget(self._analysis_panel)
            # [2026-05-28] 구조해석 탭은 와이어프레임만 — 실 색면 숨김.
            if hasattr(self._viewer, 'set_rooms_visible'):
                self._viewer.set_rooms_visible(False)
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
        elif idx == TAB_SECTION:
            # [2026-05-31 P2] 단면 설계 탭 — 좌 공유 와이어프레임 마운트 +
            # 진입 시 '모두 통일' 자동 수렴 → 좌측 5단계 응력비 색.
            self._section_center_lay.addWidget(self._canvas_widget)
            self._deformed_widget.hide()
            if hasattr(self._viewer, 'set_rooms_visible'):
                self._viewer.set_rooms_visible(False)
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            self._run_section_design_if_needed()
        elif idx == TAB_QUANTITY:
            # [2026-05-31 물량탭 개편 Phase 4] 와이어프레임·AnalysisPanel 부착 폐지.
            # 중앙 = 물량 3D(QWebEngineView, 이미 _build_quantity_tab 에서 부착됨),
            # 우 = QuantityPanel(이미 부착됨). 공유 위젯(치수)만 정리.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            if hasattr(self, "_deformed_widget"):
                self._deformed_widget.hide()
            # 평가탭이 쓰는 _quantity_reports 채움 경로는 유지해야 하므로, 구조해석/
            # 케이스 선택은 그대로 호출(AnalysisPanel 은 화면 부착 안 돼도 내부 상태 갱신).
            self._run_analysis_if_needed()
            if hasattr(self._analysis_panel, 'select_envelope_case'):
                self._analysis_panel.select_envelope_case()
            # 물량 3D + 우측 패널 렌더 (단면설계 결과 단일출처 재사용).
            self._render_quantity_view()
        elif idx == TAB_TRANSPORT:
            # [개편 Phase 4] 운송 탭 — *중 3D / 우 결과* 2 단 분할(좌측 입력 패널 폐지).
            # AnalysisPanel 통째 부착 안 함. TransportTab 의 우측 결과 영역만 reparent.
            # 실행 버튼(_run_btn)은 중앙 3D 뷰 우상단 오버레이로 부착한다.
            self._transport_center_lay.addWidget(self._transport_3d_web)
            if hasattr(self, "_deformed_widget"):
                self._deformed_widget.hide()
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None:
                if hasattr(tt, '_right_pane_scroll'):
                    self._transport_right_lay.addWidget(tt._right_pane_scroll)
                # 실행 버튼을 중앙 3D pane 자식으로 reparent 후 우상단 오버레이 배치.
                if hasattr(tt, '_run_btn'):
                    tt._run_btn.setParent(self._transport_center_pane)
                    tt._run_btn.show()
                    tt._run_btn.raise_()
                    self._position_transport_run_btn()
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            # 운송 탭에선 AnalysisPanel 자체가 부착 안 됐지만 set_visible_subtabs /
            # set_visualization_controls_visible 메서드는 *AnalysisPanel 객체* 에
            # 호출해 내부 상태만 갱신. 부착 안 됐어도 sub-탭 visibility 는 다음
            # 탭 진입 시 영향.
            if hasattr(self._analysis_panel, 'set_visible_subtabs'):
                self._analysis_panel.set_visible_subtabs(
                    show_summary=False, show_member=False,
                    show_quantity=False, show_transport=True,
                )
            # [2026-05-27] 운송 탭에선 *AnalysisPanel 통째* 가 부착 안 되니 이미
            # 시각 컨트롤이 화면에 없지만 — 메서드 호출은 *다른 탭 복귀 시 복원
            # 동작* 의 한쪽 짝이라 그대로 유지.
            if hasattr(self._analysis_panel, 'set_visualization_controls_visible'):
                self._analysis_panel.set_visualization_controls_visible(False)
            # [2026-05-24 프로젝트 설정 2단계] 운송 탭 진입 시 공통 설정값을
            # 운송 옵션 위젯에 주입 + 읽기전용(회색)으로 갱신. 운송 계산은
            # 위젯 값을 읽으므로, 계산 실행 전에 먼저 동기화한다.
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None and hasattr(tt, 'apply_project_settings'):
                tt.apply_project_settings(self._project_settings)
            # 해석 + 단면 산정 자동 보장 (물량탭과 동일 패턴)
            self._run_analysis_if_needed()
            if hasattr(self._analysis_panel, 'select_envelope_case'):
                self._analysis_panel.select_envelope_case()
            # design_results 가 controller 에 있으면 운송탭에 주입
            design_results = getattr(self._controller, '_design_results', None)
            if design_results and hasattr(self._analysis_panel, 'populate_transport'):
                policy = getattr(self._analysis_panel, '_current_policy', '3종')
                self._analysis_panel.populate_transport(design_results, policy)
        elif idx == TAB_SCHEDULE:
            # [2026-05-27 공정표 이식 Phase A·C] SchedulePanel 자체가 자기 컨텐츠를
            # 가진 위젯이므로 공유 위젯 reparent 없음. 다만 다른 공유 위젯
            # (치수 패널·변형형상)은 다른 탭과 동일하게 정리한다.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            if hasattr(self, "_deformed_widget"):
                self._deformed_widget.hide()
            # [Phase C] 진입 즉시 자동주입 — 어댑터 호출 책임은 SchedulePanel 자체.
            # 페이지 미로드 상태면 SchedulePanel 가 큐에 쌓아뒀다가 loadFinished 시 적용.
            try:
                comps = (self._scene.components.values()
                         if hasattr(self._scene, 'components') else [])
                # [2026-05-31] scene 전달 — 접합부 설계 결과 기반 카운트 정확화(§3-7).
                self._tab_schedule.on_enter(comps, self._project_settings, self._scene)
            except Exception as e:
                # 사용자 시야 — dprint 만으로는 빈 화면 원인 모름. 상태바 + 로그.
                dprint('SCHEDULE', f'[SCHEDULE] 자동주입 실패: {e}')
                self.statusBar().showMessage(
                    f'공정표 자동주입 실패 — {type(e).__name__}: {e}', 8000)
        elif idx == TAB_EVALUATION:
            # [2026-05-27 평가 탭 Phase J·M] EvaluationPanel — 공유 위젯 X.
            # 진입 즉시 evaluation_adapter 가 scene·ProjectSettings·물량·운송·공정표
            # 결과를 모아 dict 산출 → 패널의 apply_data 로 주입.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            if hasattr(self, "_deformed_widget"):
                self._deformed_widget.hide()
            self._refresh_evaluation_panel()
        elif idx == TAB_COMPARE:
            # [2026-05-31] 비교 탭 — 자기 데이터(.case.json 로드)만 사용.
            # 공유 위젯은 다른 탭과 동일하게 정리. 자동주입 없음.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            if hasattr(self, "_deformed_widget"):
                self._deformed_widget.hide()

    # ── 평가 탭 데이터 갱신 헬퍼 (탭 진입·정책 변경 양쪽에서 사용) ──
    def _refresh_evaluation_panel(self):
        """평가 어댑터 호출 + apply_data. _on_tab_changed(EVALUATION) 과
        analysis_panel.policy_changed 양쪽이 같은 경로로 평가 화면을 갱신."""
        try:
            from modular_3d.evaluation.evaluation_adapter import build_evaluation_data
            comps = list(self._scene.components.values()) if hasattr(self._scene, 'components') else []
            quantity_reports = getattr(self._analysis_panel, '_quantity_reports', None) or None
            current_policy = getattr(self._analysis_panel, '_current_policy', '3종')
            transport_pack = getattr(self, '_last_transport_pack', None)
            transport_tab = getattr(self._analysis_panel, '_transport_tab', None)
            transport_eco = getattr(transport_tab, '_last_eco', None) if transport_tab is not None else None
            section_types = self._extract_section_types()
            scene_components_by_id = dict(self._scene.components) if hasattr(self._scene, 'components') else {}
            data = build_evaluation_data(
                components=comps,
                project_settings=self._project_settings,
                quantity_reports=quantity_reports,
                current_policy=current_policy,
                transport_pack=transport_pack,
                transport_eco=transport_eco,
                schedule_payload=getattr(self, '_schedule_payload', None) or None,
                section_types=section_types,
                scene_components_by_id=scene_components_by_id,
            )
            self._tab_evaluation.apply_data(data)
            # [2026-05-31 v2] 종합탭 — 전체 배치 캡처. 단순 grab() 은 사용자의
            # 현재 zoom/pan 상태를 그대로 잡아 일부만 보이므로, fit-all 뷰로
            # 강제 전환 후 캡처(캔버스 상태는 캡처 후 원복).
            try:
                pm = self._capture_layout_full_pixmap()
                if pm is not None and not pm.isNull():
                    self._tab_evaluation.set_layout_pixmap(pm)
            except Exception as e:
                dprint('EVALUATION', f'[EVALUATION] 평면도 캡처 실패: {e}')
        except Exception as e:
            # 빈 화면 방지 — 사용자에게 원인 노출.
            dprint('EVALUATION', f'[EVALUATION] 데이터 수집 실패: {e}')
            try:
                self.statusBar().showMessage(
                    f'평가 데이터 수집 실패 — {type(e).__name__}: {e}', 8000)
            except Exception:
                pass

    # ── 랜딩 페이지 핸들러 ───────────────────────────────
    def _on_home_start_new(self) -> None:
        """랜딩 '새 프로젝트 시작' → 메인 탭 UI 로 전환.

        [2026-06-01] HomePanel 이 폼 값을 이미 self._project_settings 에
        적용한 *뒤* 이 시그널을 발화한다. 따라서 별도 다이얼로그를 띄울
        필요 없이 곧바로 모듈 정의 탭으로 진입.
        설정 변경 즉시 반영 — 운송탭·공정표 등 소비처 갱신.
        """
        try:
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None and hasattr(tt, 'apply_project_settings'):
                tt.apply_project_settings(self._project_settings)
            if hasattr(self._tab_schedule, 'apply_project_settings'):
                self._tab_schedule.apply_project_settings(self._project_settings)
        except Exception as e:
            dprint('HOME', f'[HOME] 설정 푸시 실패: {e}')
        try:
            self._tabs.setCurrentIndex(TAB_DEFINE)
            self._stack.setCurrentIndex(1)
            self.menuBar().setVisible(True)
        except Exception:
            pass

    # ── 단면 설계 탭의 타입 목록 추출 ─────────────────────
    def _extract_section_types(self):
        """단면 설계 탭의 SectionDesignPanel 이 보관한 _types 리스트 반환.

        [2026-06-01] 종합탭 부재 구성을 단면 설계 탭의 타입 목록과 일치시키기
        위해 사용. 단면 설계를 아직 안 돌렸으면 None 반환 → 어댑터가 기존
        자동 분류로 폴백.
        """
        panel = getattr(self, '_section_panel', None)
        if panel is None:
            return None
        types = getattr(panel, '_types', None)
        if not types:
            return None
        try:
            return list(types)
        except Exception:
            return None

    # ── three.js 2D 뷰에서 캡처 (M8-b 친구 UI) ─────────────
    def _capture_three_layout_pixmap(self):
        """three.js 2D 뷰의 canvas.toDataURL() → PNG 픽스맵.

        [2026-06-01] 친구가 배치설계 화면을 three.js 2D 뷰로 마이그레이션했음.
        사용자가 실제로 보는 것은 이 뷰. QEventLoop 로 비동기 JS 호출을
        동기처럼 처리.
        """
        from PyQt5.QtGui import QPixmap as _QPixmap
        from PyQt5.QtCore import QEventLoop, QTimer
        panel_three = getattr(self, '_f5_panel_three', None)
        if panel_three is None:
            return None
        canvas3 = getattr(panel_three, 'canvas', None)
        view = getattr(canvas3, '_view', None) if canvas3 is not None else None
        if view is None:
            return None
        # three.js 동기화 한 번 더 강제 — pending state 있으면 즉시 flush.
        try:
            if hasattr(panel_three, '_flush'):
                panel_three._flush()
        except Exception:
            pass
        # JS — three.js renderer 의 canvas 를 PNG dataURL 로.
        # [2026-06-01]
        # 1) WebGLRenderer 가 preserveDrawingBuffer:false 라 직전 frame GL buffer
        #    가 비워짐 → toDataURL 직전에 renderer.render() 한 번 더.
        # 2) 사용자의 현재 zoom/pan 그대로 캡처되면 콘텐츠가 잘림.
        #    OrthographicCamera 를 일시적으로 scene bounding box 에 맞춰
        #    fit-all 로 조정 → 캡처 → 카메라 원복.
        # [2026-06-01] 추가로 renderer 의 픽셀 사이즈를 일시 1600x1200 으로
        # 키워 디테일 확보 — 캡처 직후 setSize 로 원복.
        js = (
            "(function(){ try {"
            "  if (typeof renderer === 'undefined' || !renderer.domElement) return '';"
            "  if (typeof scene === 'undefined' || typeof camera === 'undefined') return '';"
            "  var dpr = renderer.getPixelRatio();"
            "  var pw  = renderer.domElement.width;"
            "  var ph  = renderer.domElement.height;"
            "  var cw  = renderer.domElement.clientWidth  || pw;"
            "  var ch  = renderer.domElement.clientHeight || ph;"
            "  var prev = {"
            "    px: camera.position.x, py: camera.position.y, pz: camera.position.z,"
            "    left: camera.left, right: camera.right,"
            "    top: camera.top, bottom: camera.bottom, zoom: camera.zoom"
            "  };"
            "  try {"
            "    var TW = 1600, TH = 1200;"
            "    try { renderer.setSize(TW, TH, false); } catch(_e){}"
            "    var box = new THREE.Box3().setFromObject(scene);"
            "    if (isFinite(box.min.x) && isFinite(box.max.x)) {"
            "      var size = new THREE.Vector3(); box.getSize(size);"
            "      var ctr  = new THREE.Vector3(); box.getCenter(ctr);"
            "      var ar = TW / Math.max(1, TH);"
            "      var pad = 1.08;"
            "      var halfW = Math.max(size.x, size.y * ar) * 0.5 * pad;"
            "      var halfH = halfW / ar;"
            "      camera.position.x = ctr.x;"
            "      camera.position.y = ctr.y;"
            "      camera.left = -halfW; camera.right = halfW;"
            "      camera.top  =  halfH; camera.bottom = -halfH;"
            "      camera.zoom = 1.0;"
            "      camera.updateProjectionMatrix();"
            "    }"
            "    renderer.render(scene, camera);"
            "    var url = renderer.domElement.toDataURL('image/png');"
            "    return url;"
            "  } finally {"
            "    camera.position.x = prev.px;"
            "    camera.position.y = prev.py;"
            "    camera.position.z = prev.pz;"
            "    camera.left = prev.left; camera.right = prev.right;"
            "    camera.top  = prev.top;  camera.bottom = prev.bottom;"
            "    camera.zoom = prev.zoom;"
            "    camera.updateProjectionMatrix();"
            "    try { renderer.setSize(cw, ch, false); } catch(_e){}"
            "    try { renderer.setPixelRatio(dpr); } catch(_e){}"
            "    try { renderer.render(scene, camera); } catch(_e){}"
            "  }"
            "} catch(e) { return ''; } })()"
        )
        result = {"data": "", "done": False}
        loop = QEventLoop()
        def _cb(data_url):
            result["data"] = data_url or ""
            result["done"] = True
            loop.quit()
        view.page().runJavaScript(js, _cb)
        # 안전 타임아웃 — 1.5초 안에 콜백 안 오면 포기.
        QTimer.singleShot(1500, loop.quit)
        loop.exec_()
        data_url = result["data"]
        if not data_url or not data_url.startswith("data:image/png;base64,"):
            return None
        try:
            import base64
            raw = base64.b64decode(data_url.split(",", 1)[1])
            pm = _QPixmap()
            pm.loadFromData(raw, "PNG")
            if pm.isNull():
                return None
            return pm
        except Exception:
            return None

    # ── 배치 캔버스 전체 캡처 ────────────────────────────
    def _capture_layout_full_pixmap(self):
        """배치설계 2D 캔버스의 전체 배치를 항상 fit-all 뷰로 캡처.

        [2026-05-31] 사용자의 현재 zoom/pan 과 무관하게 모든 부재가 한눈에
        들어오는 평면도를 만든다. 캔버스 상태(zoom/pan/size)는 캡처 후 원복.

        [2026-06-01 친구 UI 마이그레이션 대응]
        AlignmentDockPanel.hide_canvas() 가 호출되어 vispy QPainter 캔버스가
        숨겨졌어도(three.js 2D 뷰로 대체) AlignmentCanvas 인스턴스의 모델/입력
        로직은 살아있다. 캡처 직전 일시적으로 가시화 + resize + 이벤트 펌프 +
        _auto_fit 호출 → render → 다시 숨김. 사용자 화면엔 깜빡임 없음
        (paint 한 번이 frame 안에 들어가도록 즉시 원복).
        """
        from PyQt5.QtGui import QPixmap as _QPixmap
        from PyQt5.QtCore import Qt as _Qt, QCoreApplication as _QCA
        # [2026-06-01] 1순위 — three.js 2D 뷰 (사용자가 실제로 보는 화면).
        # three.js scene.background 가 이미 흰색이라 검정→흰색 치환 후처리
        # 불필요. (오히려 텍스트 라벨 색(#222222) 까지 흰색으로 바뀌어 글자가
        # 사라지는 부작용이 있었음.)
        try:
            pm3 = self._capture_three_layout_pixmap()
            if pm3 is not None and not pm3.isNull():
                return pm3
        except Exception:
            pass
        # 2순위 — vispy 캔버스 (마이그레이션 전 / 폴백).
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is None:
            return None
        # 백업
        old_visible = bool(canvas.isVisible())
        old_zoom    = getattr(canvas, '_zoom',  None)
        old_pan_x   = getattr(canvas, '_pan_x', None)
        old_pan_y   = getattr(canvas, '_pan_y', None)
        old_size    = canvas.size()
        old_min     = canvas.minimumSize()
        old_max     = canvas.maximumSize()
        old_af_done = getattr(canvas, '_auto_fit_done', None)
        try:
            # 1) layout 이 작은 크기로 되돌리지 못하게 setFixedSize 로 강제.
            #    hidden 위젯이라도 layout 사이즈는 정상 적용된다.
            if not old_visible:
                canvas.setVisible(True)
            target_w, target_h = 900, 700
            canvas.setFixedSize(target_w, target_h)
            try:
                _QCA.processEvents()
            except Exception:
                pass
            # 2) _auto_fit_done 플래그가 True 면 resizeEvent 에서 _auto_fit 가
            #    호출 안 됨. 우리가 명시 호출하므로 플래그 상태와 무관.
            if hasattr(canvas, '_auto_fit'):
                try:
                    canvas._auto_fit()
                except Exception:
                    pass
            # 3) 즉시 paintEvent — repaint() 는 동기, render 전에 paint state
            #    가 갱신되도록 한 번 그리고 처리.
            try:
                canvas.repaint()
                _QCA.processEvents()
            except Exception:
                pass
            # 4) 캡처
            pm = _QPixmap(canvas.size())
            pm.fill(_Qt.white)
            canvas.render(pm)
            # 5) 검정 배경 흔적 제거.
            try:
                from .compare_panel import _CaseSlot
                pm = _CaseSlot._whiten_dark_pixels(pm, threshold=45)
            except Exception:
                pass
            return pm
        finally:
            # 원복 — setFixedSize 풀고 원래 min/max 복원.
            canvas.setMinimumSize(old_min)
            canvas.setMaximumSize(old_max)
            canvas.resize(old_size)
            if old_zoom  is not None: canvas._zoom  = old_zoom
            if old_pan_x is not None: canvas._pan_x = old_pan_x
            if old_pan_y is not None: canvas._pan_y = old_pan_y
            if old_af_done is not None:
                try:
                    canvas._auto_fit_done = old_af_done
                except Exception:
                    pass
            if not old_visible:
                canvas.setVisible(False)
            try:
                canvas.update()
            except Exception:
                pass

    def _on_policy_changed_for_evaluation(self, policy: str):
        """정책 변경 시 평가 탭이 활성이면 즉시 재계산. 비활성이면 다음
        탭 진입 때 lazy pull 로 갱신되므로 무시."""
        if self._tabs.currentIndex() == TAB_EVALUATION:
            self._refresh_evaluation_panel()

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

    # ── 접합부 변경 — 편집 모드/픽킹/적용 (2026-05-25) ──────

    # ── 접합 추가 (2026-05-25 2단계) ───────────────────────

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

    def _run_section_design_if_needed(self):
        """단면 설계 탭 진입 — 와이어프레임 확보 후 '모두 통일' 자동 수렴 → 응력비 색.

        [2026-05-31 P2] 절차:
          1) _run_analysis_if_needed 로 ops 와이어프레임 뷰 + om 확보(컨트롤러
             _last_ops_analysis).
          2) converge_sections(모두 통일)로 단면을 수렴시켜 부재별 응력비 산출.
          3) 그 om 에 5단계 응력비 색(show_member_ratio_bands, 빨강=NG) 적용.
        수렴은 코어 포함 씬이 필요하고 무거우므로(최대 ~60 해석) 실패는 경고만.
        결과는 self._section_result 에 보관(P3·P4 에서 옵션/3D 가 소비).
        """
        if not self._scene.components:
            return
        # 1) 와이어프레임 + om 확보 (기존 구조해석 경로 재사용)
        self._run_analysis_if_needed()
        # 2) 현재 패널 옵션(기본=모두 통일)으로 수렴 + 색.
        opts = self._section_panel.current_options()
        self._converge_and_color(opts)

    def _apply_section_design(self):
        """단면 설계 패널 '적용' — 현재 옵션으로 재수렴 + 색 갱신.

        진입 시점과 달리 ops 와이어프레임은 이미 떠 있으므로 분석 재실행은
        is_ops_view_active 가드로 자동 생략된다.
        """
        if not self._scene.components:
            return
        self._run_analysis_if_needed()
        self._converge_and_color(self._section_panel.current_options())

    def _drift_summary_text(self, ops_results) -> str:
        """[L4-1] 수렴 해석 결과 → '변위비 지진 1/N(OK) · 풍 1/N(NG)' 한 줄.

        각 케이스의 가진 방향(Ex/Wx→X, Ey/Wy→Y) 최악 변위비를 본다.
        횡력 변위 데이터가 없으면 빈 문자열(상태 라벨에 안 붙임).
        """
        if not ops_results:
            return ''
        try:
            from modular_3d.analysis.drift_check import compute_all_drifts
            drifts = compute_all_drifts(ops_results)
        except Exception:
            return ''
        if not drifts:
            return ''

        def _worst(cases):
            wr = 0.0
            ok = True
            for key, is_x in cases:
                for sd in drifts.get(key, []):
                    rr = sd.drift_x if is_x else sd.drift_y
                    okk = sd.ok_x if is_x else sd.ok_y
                    if rr > wr:
                        wr = rr
                    if not okk:
                        ok = False
            return wr, ok

        parts = []
        sr, sok = _worst([('Ex', True), ('Ey', False)])
        if sr > 1e-9:
            parts.append(f"지진 1/{1.0 / sr:.0f}({'OK' if sok else 'NG'})")
        wr, wok = _worst([('Wx', True), ('Wy', False)])
        if wr > 1e-9:
            parts.append(f"풍 1/{1.0 / wr:.0f}({'OK' if wok else 'NG'})")
        return ('변위비 ' + ' · '.join(parts)) if parts else ''

    def _converge_and_color(self, options):
        """주어진 옵션으로 converge_sections 실행 → 좌측 5단계 응력비 색 적용.

        결과는 self._section_result 에 보관(P4 의 타입 목록·컴포넌트 3D 가 소비).
        수렴은 무겁고(최대 ~60 해석) 코어 포함 씬이 필요 → 실패는 경고만.
        """
        try:
            from modular_3d.analysis.section_converge import (
                converge_sections, expand_locks_to_members)
            from modular_3d.analysis.topology import build_analysis_model
            # 수동 잠금(comp_id별 클래스→단면)을 부재 단위로 전개해 고정.
            # 토폴로지 공유 제공자 경유 — 실패 시 기존 빌드 폴백(동작 보존).
            try:
                from modular_3d.analysis.model_provider import get_analysis_model
                am = get_analysis_model(self._scene)
            except Exception:
                am = build_analysis_model(self._scene)
            locks = getattr(self, '_section_locks', None) or {}
            locked_sections = expand_locks_to_members(am, locks)
            self._section_result = converge_sections(
                self._scene, options, prebuilt_am=am,
                locked_sections=locked_sections)
            # [P5b] 물량 탭이 단일 출처로 쓰도록 컨트롤러에 전달.
            if hasattr(self._controller, 'set_section_design_result'):
                self._controller.set_section_design_result(self._section_result)
            # [L1-1 WYSIWYG] 수렴 단면을 배치 컴포넌트 렌더 형상에 반영 — 3D 솔리드가
            # 설계 굵기를 따라가게(렌더 전용, 해석·물량 불변). 실패해도 설계 결과는 유효.
            try:
                from modular_3d.render.section_apply import (
                    apply_design_sections_to_scene)
                apply_design_sections_to_scene(self._scene, am)
            except Exception as e:
                dprint('ANALYSIS',
                       f'[SECTION] 렌더 단면 반영 실패: {type(e).__name__}: {e}')
        except Exception as e:
            dprint('ANALYSIS', f'[SECTION] 수렴 실패: {type(e).__name__}: {e}')
            QMessageBox.warning(
                self, '단면 설계 수렴 실패',
                f'단면 수렴 중 오류:\n\n{type(e).__name__}: {e}',
            )
            return
        # 결과 요약을 패널 상태 라벨에 표시.
        try:
            r = self._section_result
            n_ng = len(getattr(r, 'ng_member_ids', []) or [])
            # 반복 횟수는 사용자에게 혼란만 줘서 표시에서 제외. 수렴 여부 + NG 수만.
            conv = '수렴 완료' if getattr(r, 'converged', False) else '안전측 종료'
            status = f'{conv} · NG {n_ng}개'
            # [L4-1] 수렴 단면 기준 층간변위비 한 줄 요약(지진/풍 최악 비·판정).
            drift_txt = self._drift_summary_text(getattr(r, 'ops_results', None))
            if drift_txt:
                status += ' · ' + drift_txt
            self._section_panel.set_status(status)
        except Exception:
            pass
        # 타입 목록(-1-1 로컬 파생) 채우기.
        try:
            from modular_3d.analysis.section_converge import derive_section_types
            types, _comp_label = derive_section_types(self._section_result, self._scene)
            self._comp_type_label = _comp_label or {}
            self._section_panel.populate_types(types)
        except Exception as e:
            dprint('ANALYSIS', f'[SECTION] 타입 목록 생성 실패: {type(e).__name__}: {e}')
        # 응력비 색 — 분석 om 에 수렴 응력비를 5단계 색으로(빨강=NG).
        try:
            v = self._viewer
            last_ops = getattr(self._controller, '_last_ops_analysis', None)
            ratios = getattr(self._section_result, 'member_ratios', None) or {}
            if (last_ops and ratios
                    and hasattr(v, 'is_ops_view_active') and v.is_ops_view_active()):
                v.show_member_ratio_bands(last_ops[0], ratios)
        except Exception as e:
            dprint('ANALYSIS', f'[SECTION] 응력비 색 적용 실패: {type(e).__name__}: {e}')

    def _on_section_type_selected(self, label: str, comp_id: int):
        """단면 설계 타입 목록 선택 → 그 대표 컴포넌트 색 메쉬를 패널 3D 에 표시.

        [P4b-1] scene 컴포넌트를 build_component_mesh 로 그대로 렌더(원점 중심 이동).
        [P4b-2 잔여] 수렴 단면 치수 반영·종속 부재 포함·외곽 고정 안쪽 성장.
        """
        self._sel_section_type_label = label or None
        if comp_id is None or comp_id < 0:
            return
        comp = self._scene.components.get(comp_id)
        if comp is None:
            return
        try:
            import numpy as _np
            from modular_3d.analysis.section_converge import find_dependent_comp_ids
            # [7C-2] 컴포넌트(+종속)를 색 클래스별 메시로 — 각 메시에 단면명 부여(hover).
            #   클래스별 분리라야 부재 위 hover 시 그 강재명을 띄울 수 있다(통짜 메시 X).
            raw = []   # (verts, faces, colors, name)
            for cid in [comp_id] + list(find_dependent_comp_ids(self._scene, comp_id)):
                c = self._scene.components.get(cid)
                if c is None:
                    continue
                names_c = self._section_dims_names_by_class(cid)   # {cc: 단면명}
                cmeshes = self._build_component_class_meshes_sized(
                    c, self._section_dims_by_class(cid))
                for cc, (v, f, col) in cmeshes.items():
                    v = _np.asarray(v, dtype=float)
                    if v.size == 0:
                        continue
                    raw.append((v, f, col, names_c.get(cc, '')))
            if not raw:
                self._section_panel.set_component_members([])
                return
            stack = _np.vstack([r[0] for r in raw])
            vmin = stack.min(axis=0)
            vmax = stack.max(axis=0)
            center = (vmin + vmax) * 0.5     # 전체 공통 원점 중심 이동
            size = vmax - vmin               # 외곽 치수(폭/깊이/높이, mm)
            members = [{
                'vertices': (v - center), 'faces': f, 'face_colors': col, 'name': nm,
            } for (v, f, col, nm) in raw]
            self._section_panel.set_component_members(members)
            # [7C-1] 캐드식 치수선(폭/깊이/높이).
            self._section_panel.set_component_dims(
                float(size[0]), float(size[1]), float(size[2]))
        except Exception as e:
            dprint('ANALYSIS',
                   f'[SECTION] 컴포넌트 3D 빌드 실패: {type(e).__name__}: {e}')

    def _section_highlight_members(self, comp_ids):
        """[7B-4b] 주어진 컴포넌트들의 부재를 좌측 ops 와이어프레임에서 빨강 강조.

        공유 pinned 라인 시각(update_pinned_members) 재사용 — 단면 설계 탭 떠날 때
        _on_tab_changed 에서 해제. comp_id → 부재 mid 는 분석 am.comp_to_members.
        """
        last_ops = getattr(self._controller, '_last_ops_analysis', None)
        if not last_ops:
            return
        om, am = last_ops[0], last_ops[1]
        import numpy as _np
        red = (1.0, 0.15, 0.15, 1.0)
        segs = []
        for cid in (comp_ids or []):
            for mid in am.comp_to_members.get(cid, []):
                m = am.members.get(mid)
                if m is None:
                    continue
                c1 = om.node_tags.get(m.n1)
                c2 = om.node_tags.get(m.n2)
                if c1 is None or c2 is None:
                    continue
                segs.append(((_np.asarray(c1, float), _np.asarray(c2, float)), red))
        try:
            self._viewer.update_pinned_members(segs, 10.0)   # [9-3] 더 굵게
        except Exception as e:
            dprint('ANALYSIS', f'[SECTION] 하이라이트 실패: {type(e).__name__}: {e}')

    def _on_section_type_list_selected(self, label: str, rep_comp_id: int):
        """[7B-3] 타입 목록 *사용자 클릭* → 타입 전체 모드.

        잠금/변경이 그 타입 전체에 적용(분기 없음). 컴포넌트 3D 는 대표 comp 로 표시,
        선택 라벨·잠금 콤보 미리채움. (3D 클릭이 select_type_row 로 동기 이동할 때는
        blockSignals 라 이 핸들러가 안 불려 single 모드가 유지된다.)
        """
        self._section_show_component(rep_comp_id, label, single=False)

    def _on_section_3d_pick(self, pos):
        """[7B-2] 단면 설계 탭 좌측 3D 클릭 → 컴포넌트 선택 연동.

        ops 부재를 픽킹 → 그 부재의 소유 컴포넌트 역추적(종속이면 parent_id 로 부모) →
        타입 목록 동기(표시), 그 컴포넌트로 3D 갱신, 선택 라벨 표시, 잠금 콤보 현재 단면
        미리채움. (선택 1개만 잠금/하이라이트는 7B-3·7B-4.)
        """
        last_ops = getattr(self._controller, '_last_ops_analysis', None)
        if not last_ops:
            return
        om, am = last_ops[0], last_ops[1]
        try:
            mid = self._viewer.pick_ops_member_at(pos, om, am)
        except Exception:
            mid = None
        if mid is None:
            return
        m = am.members.get(mid)
        if m is None:
            return
        cids = getattr(m, 'source_comp_ids', None) or []
        comp_id = cids[0] if cids else None
        if comp_id is None:
            return
        # 종속 부재(캔틸 등)면 소유 본체(parent_id)로 역추적.
        comp = self._scene.components.get(comp_id)
        if comp is not None:
            pid = int(getattr(comp, 'parent_id', 0) or 0)
            if pid:
                comp_id = pid
        label = (self._comp_type_label or {}).get(comp_id)
        if label is None:
            return  # 코어 등 타입 목록 비대상 → 무시
        # [7B-3] 3D 클릭 = 이 컴포넌트 1개만(잠금 시 -1-2 분기).
        self._section_show_component(comp_id, label, single=True)

    def _section_dims_names_by_class(self, comp_id: int) -> dict:
        """그 컴포넌트의 색 클래스별 현재 배정 단면 *이름* {cc: name}."""
        out = {}
        res = getattr(self, '_section_result', None)
        if res is None:
            return out
        try:
            from modular_3d.analysis.section_converge import sections_by_color_class
            for cc, sec in sections_by_color_class(res, comp_id).items():
                nm = getattr(sec, 'name', None)
                if nm:
                    out[cc] = nm
        except Exception:
            pass
        return out

    def _section_comp_ids_for_label(self, label: str):
        """그 타입 라벨에 속한 모든 컴포넌트 id(최근 수렴 기준)."""
        return [cid for cid, l in (self._comp_type_label or {}).items()
                if l == label]

    def _section_lock_targets(self):
        """[7B-3] 선택 출처별 잠금 대상 comp_id 목록.

        - single(3D 클릭): 그 컴포넌트 1개만 → 재수렴 시 -1-2 로 분기.
        - type(목록 클릭): 그 타입 라벨의 모든 컴포넌트 → 다 같이 변경(분기 없음).
        """
        mode = getattr(self, '_section_sel_mode', 'type')
        if mode == 'single' and self._section_selected_comp_id is not None:
            return [self._section_selected_comp_id]
        label = getattr(self, '_sel_section_type_label', None)
        if not label:
            return []
        return self._section_comp_ids_for_label(label)

    def _section_show_component(self, comp_id, label, single):
        """[9-7 통합] 선택 컴포넌트로 패널 전체 상태 동기 — 타입목록·3D·상세·대상라벨·
        콤보 미리채움·하이라이트를 한 번에. single=True(3D 클릭, 그 comp 1개) /
        False(타입 목록, 타입 전체)."""
        self._section_sel_mode = 'single' if single else 'type'
        self._section_selected_comp_id = comp_id if single else None
        self._sel_section_type_label = label or None
        self._section_panel.select_type_row(label)            # 표시 동기(신호 차단)
        self._on_section_type_selected(label or '', comp_id)  # 3D 메시 + 치수선
        self._section_panel.show_type_detail(label or '')     # [9-5] 상세 텍스트
        tgt = '이 컴포넌트만' if single else '타입 전체'
        self._section_panel.set_lock_target_label(
            f'대상: {label} ({tgt})' if label else '대상: (선택 없음)')
        self._section_panel.prefill_lock_combos(
            self._section_dims_names_by_class(comp_id))
        ids = [comp_id] if single else self._section_comp_ids_for_label(label)
        self._section_highlight_members(ids)

    def _on_section_change(self):
        """[9-7] 단면 변경 콤보 변경 → 대상에 즉시 반영·재수렴·전파.

        대상 = 선택 출처별(single=그 comp→-1-2 분기 / type=타입 전체). 콤보 상태 그대로
        잠금 교체(자동=해제). 재수렴 후 편집한 그 comp(새 타입)로 다시 선택·렌더(시야 유지).
        """
        targets = self._section_lock_targets()
        if not targets:
            return
        single = (getattr(self, '_section_sel_mode', 'type') == 'single')
        focus = (self._section_selected_comp_id if single else targets[0])
        choices = self._section_panel.current_lock_choices()  # {색클래스: 단면명}(비-자동)
        from modular_3d.카탈로그.steel_sections import SHS_CATALOG
        name2sec = {s.name: s for s in SHS_CATALOG}
        class_map = {cc: name2sec[n] for cc, n in choices.items() if n in name2sec}
        for cid in targets:
            if class_map:
                self._section_locks[cid] = dict(class_map)   # 콤보 상태로 교체
            else:
                self._section_locks.pop(cid, None)           # 모두 자동 → 잠금 해제
        self._apply_section_design()   # 재수렴(populate 가 row0 자동선택)
        # [9-6] 편집한 그 컴포넌트(새 타입)로 다시 선택·렌더 — 시야 유지(메시만 교체).
        if focus is not None:
            new_label = (self._comp_type_label or {}).get(focus)
            if new_label:
                self._section_show_component(focus, new_label, single=single)

    def _section_dims_by_class(self, comp_id: int) -> dict:
        """수렴 결과에서 그 컴포넌트의 색 클래스별 단면 치수 (w,h,t).

        SHS 단면만(w/h/t 보유) 반영. H형강은 치수 의미가 달라 생략(공칭 유지).
        """
        out: dict = {}
        res = getattr(self, '_section_result', None)
        if res is None:
            return out
        try:
            from modular_3d.analysis.section_converge import sections_by_color_class
            for cc, sec in sections_by_color_class(res, comp_id).items():
                w = getattr(sec, 'w', None)
                h = getattr(sec, 'h', None)
                t = getattr(sec, 't', None)
                if w and h and t:
                    out[cc] = (float(w), float(h), float(t))
        except Exception:
            pass
        return out

    def _section_apply_override(self, comp, dims: dict):
        """클래스별 단면 치수를 컴포넌트 부재에 *임시 적용*하고 복원용 saved 반환.

        scene 객체를 영구 변형하지 않도록, 호출부가 finally 에서 _section_restore_override.
        """
        saved = []
        if not dims:
            return saved

        # [수직3층모듈 호환] bottom_beams 가 리스트의 리스트일 수 있어 평탄화.
        def _flat(seq):
            out = []
            for it in (seq or []):
                if isinstance(it, (list, tuple)):
                    out.extend(it)
                else:
                    out.append(it)
            return out

        groups = {
            'column': _flat(getattr(comp, 'columns', [])),
            'ceil':   _flat(getattr(comp, 'top_beams', [])),
            'floor':  _flat(getattr(comp, 'bottom_beams', []))
                      + _flat(getattr(comp, 'edge_beams', [])),
        }
        tr = getattr(comp, 'top_runner', None)
        if tr is not None:
            groups['ceil'].append(tr)
        br = getattr(comp, 'bottom_runner', None)
        if br is not None:
            groups['floor'].append(br)
        cbeam = getattr(comp, 'beam', None)
        if cbeam is not None and hasattr(cbeam, 'section_w'):
            groups['ceil'].append(cbeam)
        groups['floor'] += list(getattr(comp, 'beams', []) or [])
        for cc, elems in groups.items():
            d = dims.get(cc)
            if not d:
                continue
            for e in elems:
                if hasattr(e, 'section_w'):
                    saved.append((e, e.section_w, e.section_h, e.section_t))
                    e.section_w, e.section_h, e.section_t = d
        return saved

    @staticmethod
    def _section_restore_override(saved):
        for e, w, h, t in saved:
            e.section_w, e.section_h, e.section_t = w, h, t

    def _build_component_class_meshes_sized(self, comp, dims: dict) -> dict:
        """[7C-2] 수렴 단면 치수를 임시 적용한 뒤 색 클래스별 메시 {cc: MeshData} 빌드.

        모듈/바닥패널/벽패널은 외곽 고정·안쪽 성장(outer_anchor=True). 종속(캔틸)은 False.
        """
        from modular_3d.render.mesh_builder import build_component_class_meshes
        from modular_3d.model import ComponentType as _CT
        anchor = getattr(comp, 'comp_type', None) in (
            _CT.MODULE, _CT.FLOOR_PANEL, _CT.STRUCT_WALL)
        saved = self._section_apply_override(comp, dims)
        try:
            return build_component_class_meshes(comp, outer_anchor=anchor)
        finally:
            self._section_restore_override(saved)

    # ── 좌측 팔레트 콜백 ────────────────────────────────

    def _on_detail_mode_toggle(self, checked: bool):
        """팔레트 '상세 설계' 토글 → 배치 탭 2D 캔버스 모드 정리.

        상세 설계 모드는 실 지정/벽/개구부의 관문 — 세부 모드는 각 버튼이
        전환하므로 토글 자체는 component(선택/편집) 기준으로 진행 상태만 정리한다.
        """
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'set_edit_mode'):
            canvas.set_edit_mode('component')
        if not checked:
            self._show_room_props(None)

    def _on_room_draw(self):
        """팔레트 '실 지정' → 실 편집 모드 진입 + 실 그리기 시작."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is None:
            return
        if hasattr(canvas, 'set_edit_mode'):
            canvas.set_edit_mode('room')
        if hasattr(canvas, 'start_room_draw'):
            canvas.start_room_draw()

    def _on_opening_add(self):
        """팔레트 '개구부' → 개구부 모드 진입 후 벽/슬래브 클릭으로 배치.

        start_opening_add 가 내부에서 opening 편집 모드로 전환한다.
        """
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'start_opening_add'):
            canvas.start_opening_add()

    def _on_wall_place(self):
        """팔레트 '벽' → 부재 배치 머신으로 내벽 종속 배치 시작.

        부모 클릭(DEPENDENCY_PICK) → 길이 입력 → 고스트(R/V/클릭) 흐름은
        캔틸레버 등 기존 종속 배치와 동일. 머신은 component 편집 모드에서만
        라우팅되므로 캔버스를 component 로 전환한다(상세 설계 토글은 유지).
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
            self._design_props.setVisible(False)
            self._room_props.setVisible(True)
            self._room_props.set_room(room)
        else:
            self._room_props.clear()
            self._room_props.setVisible(False)
            self._design_props.setVisible(True)

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
        # 캔버스에 위임 — 종속/일반 분기는 캔버스가 결정.
        # [2026-05-24] '새로/가져오기' 프롬프트는 캔버스가 거치는 컨트롤러
        # _f5_on_type_key 인터셉트에서 처리(버튼·1~9키 공용, 이중 방지).
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'handle_palette_select'):
            canvas.handle_palette_select(comp_type)
        self._design_props.refresh_active(comp_type)

    def _on_design_type_select(self, comp_type) -> bool:
        """배치 탭 타입 선택 인터셉트(버튼·1~9키 공용).

        저장된 정의가 해당 타입에 있으면 '새로 만들기 / 가져오기' 프롬프트를
        띄운다. 반환 True → 인터셉트(기존 새로 만들기 흐름 생략), False → 정상
        진행. (현재 가져오기 실제 배치는 다음 단계 — 안내만.)
        """
        lib = self._definition_library
        type_val = getattr(comp_type, 'value', None)
        names = [n for n in lib.list_names()
                 if lib.get(n) is not None and lib.get(n).root_type == type_val]
        if not names:
            return False   # 저장된 정의 없음 → 바로 새로 만들기
        label = TYPE_NAMES.get(comp_type, str(comp_type))
        items = ['(새로 만들기)'] + names
        choice, ok = QInputDialog.getItem(
            self, f'{label} 배치',
            '새로 만들거나 저장된 정의를 가져옵니다:', items, 0, False)
        if not ok:
            return True    # 취소 — 아무것도 안 함
        if choice == '(새로 만들기)':
            return False   # 정상 새로 만들기 흐름
        # 가져오기 — 그룹 고스트 미리보기 시작(마우스 따라, R/V, 클릭 배치).
        if hasattr(self._controller, '_import_definition_start'):
            self._controller._import_definition_start(choice)
        return True   # 인터셉트(기존 새로 만들기 흐름 생략)

    # ── 시나리오 탭 핸들러 ──────────────────────────────

    def _on_scene_save(self):
        """시나리오 탭 '저장' 버튼 → F5Mixin._f5_save_scene 재사용."""
        if hasattr(self._controller, '_f5_save_scene'):
            self._controller._f5_save_scene()

    # ── 헬퍼 ─────────────────────────────────────────

    def _is_design_tab(self) -> bool:
        return self._tabs.currentIndex() == TAB_DESIGN

    # ── Qt 이벤트 필터 (캔버스 — vispy 위젯) ──────────────

    def eventFilter(self, obj, event):
        # [Phase 4] 운송 중앙 3D pane resize → 실행 버튼 오버레이 우상단 추종.
        if (getattr(self, '_transport_center_pane', None) is obj
                and event.type() == QEvent.Resize):
            self._position_transport_run_btn()
            return False
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
                # [2026-05-25] 접합부 조정 탭: 좌클릭 → 추가 모드면 노드 픽킹,
                # 편집 모드면 접합 픽킹.
                if (btn == Qt.LeftButton
                        and self._tabs.currentIndex() == TAB_JOINT_EDIT):
                    if self._joint_ctrl.is_add_mode():
                        self._joint_ctrl._on_joint_add_pick(pos)
                        return True
                    if self._joint_ctrl.is_edit_mode():
                        self._joint_ctrl._on_joint_pick(pos)
                        return True
                # [7B-2] 단면 설계 탭 좌클릭 → 컴포넌트 선택(부재 픽킹 후 소유 컴포넌트
                # 역추적) → 패널/타입목록/컴포넌트3D 연동. ops 부재 핀(정보창) 대신 사용.
                if (btn == Qt.LeftButton
                        and self._tabs.currentIndex() == TAB_SECTION):
                    self._on_section_3d_pick(pos)
                    return True
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
                # [2026-05-25] 접합 추가 모드: 마우스 위치 모서리 점 고스트 미리보기.
                if (self._joint_ctrl.is_add_mode()
                        and self._tabs.currentIndex() == TAB_JOINT_EDIT):
                    self._joint_ctrl._update_joint_ghost(pos)
                    return False
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
    # 모든 콘솔 출력을 로그 파일(logs/console.log)에도 복제 — 더블클릭 실행 시
    # 콘솔을 못 봐도 오류가 파일에 남는다. 실패해도 앱 구동엔 영향 없음.
    try:
        from modular_3d._utils.debug import install_stdout_tee
        install_stdout_tee()
    except Exception:
        pass
    app = QApplication.instance() or QApplication(sys.argv)
    _apply_app_font(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
