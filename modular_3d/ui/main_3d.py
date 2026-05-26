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
from modular_3d.ui.ui_panel import DimensionInputPanel, StatusBarManager
from modular_3d.ui.analysis_panel import AnalysisPanel
from modular_3d.ui.alignment_view import AlignmentDockPanel
from modular_3d.ui.controls import Controller
from modular_3d.ui.palette_panel import PalettePanel
from modular_3d.ui.design_props_panel import DesignPropertiesPanel
from modular_3d.ui.room_props_panel import RoomPropertiesPanel
from modular_3d.ui.joint_edit_panel import JointEditPanel
from modular_3d.ui.project_settings import ProjectSettings, ProjectSettingsDialog
from modular_3d.ui.define_tab import DefineTab
from modular_3d.model.definition_library import DefinitionLibrary
from modular_3d._utils.debug import dprint


# 메인 탭 인덱스 상수
# [정책 2026-05-24 디자인 2분리] 맨 앞에 '모듈 정의' 탭 신설. 이하 모두 +1 shift.
# 외부 코드(컨트롤러 등) 가 이 상수를 참조하므로 정수 리터럴로 비교하지 말고
# 반드시 본 상수를 사용.
TAB_DEFINE = 0      # 모듈 정의 탭 (신규)
TAB_DESIGN = 1      # 배치 설계 탭 (기존 디자인 탭)
TAB_JOINT_EDIT = 2
TAB_ANALYSIS = 3
TAB_QUANTITY = 4
TAB_TRANSPORT = 5
# [2026-05-27 공정표 이식] 옛 이름 TAB_JOINT_AIR → TAB_SCHEDULE 로 변경.
# 외부 코드에서 참조하는 경우를 위해 옛 이름은 별칭으로 유지.
TAB_SCHEDULE = 6
TAB_JOINT_AIR = TAB_SCHEDULE  # deprecated alias
# [2026-05-27 평가 탭 이식] 옛 이름 TAB_FINAL → TAB_EVALUATION 으로 변경.
TAB_EVALUATION = 7
TAB_FINAL = TAB_EVALUATION  # deprecated alias


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('모듈러 부재 스터디')
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
        # [2026-05-25 접합부 변경] 편집 모드/변경/복귀 시그널 배선.
        self._joint_edit_mode = False
        self._joint_add_mode = False
        self._joint_add_first = None
        self._joint_add_cands = []      # 첫 점 선택 후 접합 가능 후보점들
        self._joint_add_snap = None     # 현재 마우스가 스냅한 후보(x,y,z,comp)
        self._joint_selected = None
        self._joint_om = None
        self._joint_am = None
        self._joint_panel.edit_mode_toggled.connect(self._on_joint_edit_mode)
        self._joint_panel.joint_change_requested.connect(self._on_joint_change)
        self._joint_panel.joint_revert_requested.connect(self._on_joint_revert)
        self._joint_panel.add_mode_toggled.connect(self._on_joint_add_mode)
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
        # 코어 슬래브 버튼 콜백 — 컨트롤러(F5Mixin)의 메서드 직접 주입.
        _regen_cb = getattr(self._controller, 'regenerate_all_core_slabs', None)
        self._palette = PalettePanel(
            on_select=self._on_palette_select,
            on_regen_core_slabs=_regen_cb,
            on_room_draw=self._on_room_draw,
            on_room_mode_toggle=self._on_room_mode_toggle,
            on_opening_mode_toggle=self._on_opening_mode_toggle,
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
        # Phase 6: 운송 탭은 placeholder 대신 운송 전용 center+right 페이지.
        self._tab_transport = self._build_transport_tab_layout()
        # [2026-05-27 공정표 이식 Phase A] placeholder → 팀원 HTML 임베드.
        # 공정표_이식_계획서.md 참조. 변수명을 _tab_schedule 로 통일하고,
        # 외부 호환을 위해 _tab_joint_air 는 별칭으로 유지.
        from .schedule_panel import SchedulePanel
        self._tab_schedule = SchedulePanel()
        # [2026-05-27 평가 탭 Phase L] 공정표 calc() 결과 캐시.
        # SchedulePanel 의 ScheduleBridge 시그널을 받아 self._schedule_payload 에 저장.
        # 평가 탭이 진입 시 이 캐시를 어댑터에 넘긴다.
        self._schedule_payload: dict = {}
        self._tab_schedule.bridge().schedule_payload_pushed.connect(
            self._on_schedule_payload_pushed
        )
        self._tab_joint_air = self._tab_schedule  # deprecated alias
        # [2026-05-27 평가 탭 이식 Phase J] placeholder → EvaluationPanel.
        # 평가탭_구축_계획서.md 참조. 변수명 _tab_evaluation 통일, 별칭 유지.
        from .evaluation_panel import EvaluationPanel
        self._tab_evaluation = EvaluationPanel()
        self._tab_evaluation.save_case_requested.connect(self._on_evaluation_save_case)
        self._tab_final = self._tab_evaluation  # deprecated alias
        self._tabs.addTab(self._define_tab, '모듈 정의')
        self._tabs.addTab(self._tab_design, '배치 설계')
        self._tabs.addTab(self._tab_joint_edit, '접합부 설계')
        self._tabs.addTab(self._tab_analysis, '구조해석')
        self._tabs.addTab(self._tab_quantity, '물량')
        self._tabs.addTab(self._tab_transport, '운송')
        self._tabs.addTab(self._tab_schedule, '공정표')
        self._tabs.addTab(self._tab_evaluation, '평가')
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

        # [Phase 7] 파일 메뉴 — 운송 카탈로그 관리 진입점.
        # AnalysisPanel 의 _transport_tab 이 다이얼로그를 띄움. 트랜스포트 탭에
        # 들어가지 않아도 메뉴에서 카탈로그를 열 수 있도록 노출.
        self._build_menu_bar()

        # [Phase 7] 운송탭에 프로젝트 루트 전달 — trucks.json 저장 위치 결정.
        # _parent (my_project) 디렉토리 아래 transport_config/ 가 생성됨.
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

    def _open_project_settings(self) -> None:
        """프로젝트 설정 모달 — 공통 설정 입력. 확인 시 세션 메모리에 보관.

        [정책 2026-05-24] 1단계는 보관까지만. 운송 탭 등 소비처와의 연결은
        다음 단계이므로, 확인을 눌러도 해석/물량/운송 재계산은 트리거하지
        않는다.
        """
        dlg = ProjectSettingsDialog(
            self._project_settings,
            on_open_catalog=self._open_transport_catalog,
            parent=self,
        )
        if dlg.exec_() == QDialog.Accepted:
            # [2026-05-26] 확인 즉시 운송탭 읽기전용 표시(현장제한 등) 갱신.
            # 종전엔 탭을 빠져나갔다 다시 들어와야 반영됐음.
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None and hasattr(tt, 'apply_project_settings'):
                tt.apply_project_settings(self._project_settings)

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
            evaluation_data = build_evaluation_data(
                components=comps,
                project_settings=self._project_settings,
                design_results=getattr(self._controller, '_design_results', None),
                transport_result=getattr(self, '_last_transport_pack', None),
                schedule_payload=getattr(self, '_schedule_payload', None) or None,
            )
            save_case(
                path=path,
                scene_state=scene_state,
                project_settings=self._project_settings,
                evaluation_data=evaluation_data,
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

        # 우측 속성 패널 — 부재 속성 + 실 속성(선택 시 전환)
        right = QWidget()
        right.setFixedWidth(320)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(self._design_props)
        rv.addWidget(self._room_props)
        self._room_props.setVisible(False)
        h.addWidget(right)
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

    def _build_transport_tab_layout(self) -> QWidget:
        """운송 탭 — *3 단 분할* (좌 입력 / 중 3D 도식 / 우 결과).

        [2026-05-27 사용자 결정]
        - 좌측: TransportTab 의 입력 패널 (카탈로그 / 옵션 / 실행 버튼)
        - 중앙: 운송 3D 적재 도식 (WebEngineView)
        - 우측: TransportTab 의 결과 패널 (회차표 / 적재율 / 경제성 / 진단)
        - 좌측은 *접기 토글 버튼* 으로 collapse 가능
        """
        from PyQt5.QtWebEngineWidgets import QWebEngineView
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        from PyQt5.QtWidgets import QSplitter, QPushButton
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 상단 툴바 — 좌측 패널 접기 토글
        toolbar = QWidget()
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(4, 2, 4, 2)
        tb_lay.setSpacing(4)
        self._transport_left_toggle_btn = QPushButton("◀ 입력 패널 접기")
        self._transport_left_toggle_btn.setCheckable(True)
        self._transport_left_toggle_btn.setMaximumWidth(150)
        self._transport_left_toggle_btn.clicked.connect(
            self._on_transport_left_toggle
        )
        tb_lay.addWidget(self._transport_left_toggle_btn)
        tb_lay.addStretch(1)
        root.addWidget(toolbar)

        # 메인 3 단 splitter
        self._transport_splitter = QSplitter(Qt.Horizontal)
        # 좌 영역
        self._transport_left_pane = QWidget()
        self._transport_left_pane.setMinimumWidth(280)
        self._transport_left_lay = QVBoxLayout(self._transport_left_pane)
        self._transport_left_lay.setContentsMargins(0, 0, 0, 0)
        # 중 영역
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self._transport_center_pane = QWidget()
        self._transport_center_lay = QVBoxLayout(self._transport_center_pane)
        self._transport_center_lay.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self._transport_center_pane, stretch=1)

        # 우 영역
        self._transport_right_pane = QWidget()
        self._transport_right_pane.setMinimumWidth(400)
        self._transport_right_lay = QVBoxLayout(self._transport_right_pane)
        self._transport_right_lay.setContentsMargins(0, 0, 0, 0)

        self._transport_splitter.addWidget(self._transport_left_pane)
        self._transport_splitter.addWidget(center)
        self._transport_splitter.addWidget(self._transport_right_pane)
        self._transport_splitter.setStretchFactor(0, 0)
        self._transport_splitter.setStretchFactor(1, 2)
        self._transport_splitter.setStretchFactor(2, 1)
        self._transport_splitter.setSizes([320, 900, 500])
        # 사용자 사이즈 저장용 (접기 후 펼침 시 복원)
        self._transport_left_saved_size = 320
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

    def _on_transport_left_toggle(self, checked: bool) -> None:
        """[2026-05-27] 운송 탭 좌측 입력 패널 접기 / 펼치기 토글."""
        if not hasattr(self, '_transport_splitter'):
            return
        sizes = self._transport_splitter.sizes()
        if checked:
            # 접기 — 좌측 0, 그 폭을 중·우에 분배
            if sizes[0] > 0:
                self._transport_left_saved_size = sizes[0]
            extra = sizes[0]
            self._transport_splitter.setSizes(
                [0, sizes[1] + int(extra * 0.6), sizes[2] + int(extra * 0.4)]
            )
            self._transport_left_toggle_btn.setText("▶ 입력 패널 펼치기")
        else:
            # 펼치기 — 저장된 사이즈 복원
            saved = getattr(self, '_transport_left_saved_size', 320)
            mid_take = saved * 6 // 10
            right_take = saved - mid_take
            self._transport_splitter.setSizes(
                [saved, max(200, sizes[1] - mid_take), max(200, sizes[2] - right_take)]
            )
            self._transport_left_toggle_btn.setText("◀ 입력 패널 접기")

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
            print(f"[운송 3D 강조 실패] {type(e).__name__}: {e}", flush=True)

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
            print(f"[3D→회차표 동기화 실패] {type(e).__name__}: {e}", flush=True)

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
            print(f"[운송 3D 렌더 실패] {tb}", flush=True)
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

    def _write_transport_3d_temp_html(self, html: str) -> str:
        """ASCII 경로 임시 파일에 HTML 저장 — file:// URL 로 로드 가능.

        한글 경로 회피 위해 시스템 temp / 가상드라이브 / ProgramData 폴백.
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
                path = os.path.join(d, "transport_3d_loaded.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
                if path.isascii():
                    return path
            except Exception:
                continue
        return ""

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
            self._joint_selected = None
            self._reset_add_progress()
        if (prev_idx == TAB_JOINT_EDIT
                and idx in (TAB_ANALYSIS, TAB_QUANTITY)):
            v = self._viewer
            if hasattr(v, 'is_ops_view_active') and v.is_ops_view_active():
                v.hide_ops_view()
                dprint('ANALYSIS', '[ANALYSIS] 접합부 조정 → 구조해석/물량 — ops 뷰 무효화 후 재빌드 예정')

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
            # 불러오기 직후 첫 진입이면 저장본/새 계산 선택(프리뷰 전에).
            self._maybe_prompt_joint_choice()
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
        elif idx == TAB_TRANSPORT:
            # [2026-05-27] 운송 탭 — *좌 입력 / 중 3D / 우 결과* 3 단 분할.
            # AnalysisPanel 통째 부착 안 함. TransportTab 의 좌·우 영역만 직접
            # reparent 해서 좌·우 layout 에 부착. AnalysisPanel 의 메서드는
            # _transport_tab 접근자로 호출.
            self._transport_center_lay.addWidget(self._transport_3d_web)
            if hasattr(self, "_deformed_widget"):
                self._deformed_widget.hide()
            tt = getattr(self._analysis_panel, '_transport_tab', None)
            if tt is not None:
                if hasattr(tt, '_left_pane_scroll'):
                    self._transport_left_lay.addWidget(tt._left_pane_scroll)
                if hasattr(tt, '_right_pane_scroll'):
                    self._transport_right_lay.addWidget(tt._right_pane_scroll)
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
            # [Phase C] 진입 즉시 자동주입 — 현재 씬 상태를 어댑터로 직렬화 후
            # SchedulePanel.apply_scene_data() 호출. 페이지 미로드 상태면
            # SchedulePanel 가 큐에 쌓아뒀다가 loadFinished 시 적용한다.
            try:
                from modular_3d.schedule.schedule_adapter import build_scene_data
                comps = list(self._scene.components.values()) if hasattr(self._scene, 'components') else []
                data = build_scene_data(comps, project_settings=self._project_settings)
                self._tab_schedule.apply_scene_data(data)
            except Exception as e:
                dprint('SCHEDULE', f'[SCHEDULE] 자동주입 실패: {e}')
        elif idx == TAB_EVALUATION:
            # [2026-05-27 평가 탭 Phase J·M] EvaluationPanel — 공유 위젯 X.
            # 진입 즉시 evaluation_adapter 가 scene·ProjectSettings·물량·운송·공정표
            # 결과를 모아 dict 산출 → 패널의 apply_data 로 주입.
            if hasattr(self._dim_panel, 'deactivate'):
                self._dim_panel.deactivate()
            self._dim_panel.setVisible(False)
            if hasattr(self, "_deformed_widget"):
                self._deformed_widget.hide()
            try:
                from modular_3d.evaluation.evaluation_adapter import build_evaluation_data
                comps = list(self._scene.components.values()) if hasattr(self._scene, 'components') else []
                # 물량 — analysis_panel._quantity_reports (정책별 QuantityReport).
                # 정책 — analysis_panel._current_policy (사용자가 물량 탭에서 선택).
                quantity_reports = getattr(self._analysis_panel, '_quantity_reports', None) or None
                current_policy = getattr(self._analysis_panel, '_current_policy', '3종')
                # 운송 — pack 은 self._last_transport_pack, economics 는 transport_panel._last_eco.
                transport_pack = getattr(self, '_last_transport_pack', None)
                transport_tab = getattr(self._analysis_panel, '_transport_tab', None)
                transport_eco = getattr(transport_tab, '_last_eco', None) if transport_tab is not None else None
                data = build_evaluation_data(
                    components=comps,
                    project_settings=self._project_settings,
                    quantity_reports=quantity_reports,
                    current_policy=current_policy,
                    transport_pack=transport_pack,
                    transport_eco=transport_eco,
                    schedule_payload=getattr(self, '_schedule_payload', None) or None,
                )
                self._tab_evaluation.apply_data(data)
            except Exception as e:
                dprint('EVALUATION', f'[EVALUATION] 데이터 수집 실패: {e}')

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

    def _run_joint_edit_preview(self, force: bool = False):
        """접합부 조정 탭 진입 시 spec 만 빌드해 와이어프레임 표시.

        [정책 2026-05-13 접합부조정탭]
        구조해석 탭의 `_run_structural_analysis` 와 달리 **solve_all_cases 를
        부르지 않는다**. build_analysis_model + build_ops_model 까지만 — spec
        알갱이가 채워지면 viewer.show_ops_view 가 _draw_from_spec 로 와이어프
        레임을 그린다.

        [함정] 이미 ops 뷰가 켜져 있으면(예: 구조해석 탭에서 본 탭으로 이동) 또
        토글되어 꺼지는 일이 없도록 활성 상태일 땐 build 자체를 생략한다. 단,
        force=True 면(접합 변경 직후 등) 활성 상태여도 강제 재빌드한다 —
        오버라이드 반영 결과를 즉시 와이어프레임에 반영하기 위함.
        """
        if not self._scene.components:
            dprint('JOINT-EDIT', '[JOINT-EDIT] Scene 이 비어 있음 — 프리뷰 생략')
            return
        v = self._viewer
        # [2026-05-25 D4 수정] ops 뷰가 이미 활성이어도 _joint_om 이 아직 없으면
        # (구조해석 탭 경유로 접합 탭 첫 진입 등) 빌드해 접합 픽킹용 모델을 확보한다.
        # 그렇지 않으면 픽킹이 None 모델을 참조해 무반응.
        if (not force and self._joint_om is not None
                and hasattr(v, 'is_ops_view_active') and v.is_ops_view_active()):
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
            # 접합 픽킹을 위해 마지막 빌드 결과 보관.
            self._joint_om = om_view
            self._joint_am = am
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
                self, '접합부 설계 프리뷰 실패',
                f'와이어프레임 빌드 중 오류:\n\n{type(e).__name__}: {e}',
            )

    # ── 접합부 변경 — 편집 모드/픽킹/적용 (2026-05-25) ──────

    def _on_joint_edit_mode(self, on: bool):
        """접합 편집 모드 토글. 끄면 선택·강조 정리."""
        self._joint_edit_mode = bool(on)
        self._joint_selected = None
        self._joint_panel.clear_selection()
        if hasattr(self._viewer, 'clear_ops_joint_highlight'):
            self._viewer.clear_ops_joint_highlight()

    def _on_joint_pick(self, pos):
        """3D 좌클릭 → 가장 가까운 컴포넌트 간 접합 선택·강조."""
        om = self._joint_om
        am = self._joint_am
        if om is None:
            return
        info = self._viewer.pick_ops_joint_at(pos, om, am)
        if info is None:
            self._joint_selected = None
            self._joint_panel.clear_selection()
            self._viewer.clear_ops_joint_highlight()
            return
        self._joint_selected = info
        self._joint_panel.show_selected_joint(info)
        self._highlight_selected_joint(info)

    def _highlight_selected_joint(self, info):
        """선택 접합 강조 — 직각접합이면 위-N1-아래 체인, 아니면 한 결합."""
        om = self._joint_om
        if info.get('right_angle') and info.get('n1_tag') is not None:
            chain = self._right_angle_chain(om, info['n1_tag'])
            self._viewer.highlight_ops_joint_chain(om, chain)
        else:
            self._viewer.highlight_ops_joint(om, info['master'], info['slave'])

    def _right_angle_chain(self, om, n1_tag):
        """중간 노드 n1_tag 를 공유하는 직각접합 결합들의 노드 체인 [위, N1, 아래].
        rule_id 무관 — 사용자추가(R10/R11) + 자동(R03) 모두 N1 공유로 수집."""
        nodes = set()
        for ed in om.spec.equal_dofs:
            if n1_tag in (ed.master, ed.slave):
                nodes.add(ed.master)
                nodes.add(ed.slave)
        ends = [t for t in nodes if t != n1_tag and t in om.node_tags]
        ends.sort(key=lambda t: om.node_tags[t][2])
        if len(ends) >= 2:
            return [ends[-1], n1_tag, ends[0]]   # 위(z큰) - N1 - 아래
        return [n1_tag] + ends

    def _find_n1_tag_by_xy(self, om, n1_xy):
        """평면 위치로 중간 노드(N1, panel_z_route) tag 찾기(다층 중 첫 매칭)."""
        if not n1_xy:
            return None
        from modular_3d.model.joint_override import MATCH_TOL_MM as _T
        for t, c in om.node_tags.items():
            if abs(c[0] - n1_xy[0]) <= _T and abs(c[1] - n1_xy[1]) <= _T:
                nr = om.spec.node(t) if om.spec is not None else None
                if nr is not None and getattr(nr, 'role', '') == 'panel_z_route':
                    return t
        return None

    def _find_add_override_by_n1(self, n1_xy):
        """N1 평면 위치로 직각 add 오버라이드 역추적. N1.xy = 위(z 큰) 점 xy."""
        if not n1_xy:
            return None
        from modular_3d.model.joint_override import MATCH_TOL_MM as _T
        for o in self._scene.joint_overrides:
            if getattr(o, 'kind', '') != 'add' or not getattr(o, 'right_angle', False):
                continue
            if float(o.z_a) >= float(o.z_b):
                ux, uy = o.a_xy
            else:
                ux, uy = o.b_xy
            if abs(ux - n1_xy[0]) <= _T and abs(uy - n1_xy[1]) <= _T:
                return o
        return None

    def _comp_group(self, comp_id: int) -> int:
        """컴포넌트 id → group_id (보조 식별). 없으면 0."""
        comp = self._scene.components.get(comp_id)
        return int(getattr(comp, 'group_id', 0) or 0) if comp is not None else 0

    def _on_joint_change(self, kind: str):
        """선택 접합에 변경(remove/pin/rigid) 적용 → 같은 xy 모든 층 → 재빌드.

        - 자동 접합: remove/rigid/pin 오버라이드(게이트)로 처리.
        - 사용자 추가 접합(USER_ADD): 그 add 오버라이드를 직접 삭제(remove)하거나
          add_dofs 를 핀/강접으로 바꾼다(추가 접합은 게이트를 안 거치므로).
        """
        info = self._joint_selected
        if info is None:
            return
        from modular_3d.model.joint_override import (
            JointOverride, PIN_DOFS, RIGID_DOFS, same_joint)
        rid0 = info.get('rule_id', '')
        is_user_add = (rid0 == 'USER_ADD'
                       or rid0.startswith('R10') or rid0.startswith('R11'))
        if is_user_add and info.get('right_angle'):
            # 직각접합 — 중간 노드로 add 오버라이드 1개를 역추적해 통째로 처리.
            ov = self._find_add_override_by_n1(info.get('n1_xy'))
            if ov is not None:
                if kind == 'remove':
                    if ov in self._scene.joint_overrides:
                        self._scene.joint_overrides.remove(ov)
                else:
                    ov.add_dofs = RIGID_DOFS if kind == 'rigid' else PIN_DOFS
        elif is_user_add:
            ax, bx = info['a_xy'], info['b_xy']
            if kind == 'remove':
                self._scene.joint_overrides = [
                    o for o in self._scene.joint_overrides
                    if not (getattr(o, 'kind', '') == 'add'
                            and same_joint(o, ax, bx))
                ]
            else:
                dofs = RIGID_DOFS if kind == 'rigid' else PIN_DOFS
                for o in self._scene.joint_overrides:
                    if getattr(o, 'kind', '') == 'add' and same_joint(o, ax, bx):
                        o.add_dofs = dofs
                        break
        elif info.get('right_angle') and info.get('n1_tag') is not None:
            # 자동 직각접합(R03 등) — N1 공유 체인 두 결합(위↔N1 수직, N1↔아래
            # 수평)을 통째로 게이트 처리. 한 결합만 바꾸면 ㄴ자가 반쪽만 변경됨.
            single = self._joint_panel.is_edit_single_layer()
            om = self._joint_om
            rid = str(rid0)
            chain = self._right_angle_chain(om, info['n1_tag'])
            for i in range(len(chain) - 1):
                u, w = chain[i], chain[i + 1]
                cu = om.node_tags.get(u)
                cw = om.node_tags.get(w)
                if cu is None or cw is None:
                    continue
                self._scene.set_joint_override(JointOverride(
                    kind=kind,
                    a_xy=(float(cu[0]), float(cu[1])),
                    b_xy=(float(cw[0]), float(cw[1])),
                    z_a=float(cu[2]), z_b=float(cw[2]),
                    rule_id=rid, single_layer=single,
                ))
        else:
            # rule_id 저장 — 같은 평면 위치에 겹친 다른 종류 수직 접합과 구분해
            # 이 종류에만 변경이 적용되도록. single_layer 면 이 층만 적용.
            single = self._joint_panel.is_edit_single_layer()
            ov = JointOverride(
                kind=kind, a_xy=info['a_xy'], b_xy=info['b_xy'],
                z_a=float(info.get('a_z', 0.0)),
                z_b=float(info.get('b_z', 0.0)),
                a_group=self._comp_group(info['a_comp']),
                b_group=self._comp_group(info['b_comp']),
                rule_id=str(info.get('rule_id', '')),
                single_layer=single,
            )
            self._scene.set_joint_override(ov)
        self._run_joint_edit_preview(force=True)
        # 변경 결과를 패널/강조에 반영.
        if kind == 'remove':
            self._joint_selected = None
            self._joint_panel.clear_selection()
            self._viewer.clear_ops_joint_highlight()
            self._warn_if_unstable()   # 제거로 불안정해졌는지 즉시 경고
        else:
            info['is_rigid'] = (kind == 'rigid')
            self._joint_panel.show_selected_joint(info)
            if info.get('right_angle'):
                # 재빌드로 N1 tag 가 바뀌므로 평면 위치로 다시 찾아 체인 강조.
                n1 = self._find_n1_tag_by_xy(self._joint_om, info.get('n1_xy'))
                if n1 is not None:
                    info['n1_tag'] = n1
                    self._viewer.highlight_ops_joint_chain(
                        self._joint_om,
                        self._right_angle_chain(self._joint_om, n1))
                else:
                    self._viewer.clear_ops_joint_highlight()
            else:
                self._viewer.highlight_ops_joint(
                    self._joint_om, info['master'], info['slave'])

    def _warn_if_unstable(self):
        """현재 접합 프리뷰 모델이 mechanism(불안정) 위험이면 경고(차단 없음).

        사용자 사양: 제거는 허용하되 경고만. self_check 가 고립 노드/특이강성을
        감지하면 다이얼로그 + 좌측 빨강 강조로 알린다."""
        om = self._joint_om
        if om is None:
            return
        try:
            from modular_3d.analysis.ops_builder import self_check
            res = self_check(om)
        except Exception:
            return
        if not getattr(res, 'is_critical', False):
            if hasattr(self._viewer, 'hide_unstable_warning'):
                self._viewer.hide_unstable_warning()
            return
        issues = getattr(res, 'issues', []) or []
        QMessageBox.warning(
            self, '구조 불안정 경고',
            '접합 제거로 구조가 불안정(mechanism)해질 수 있습니다.\n'
            '제거는 적용되었으나, 구조해석 탭에서 상세를 확인하세요.\n\n'
            + '\n'.join('· ' + str(s) for s in issues[:5]))
        nid = getattr(res, 'problem_node_ids', None)
        if nid and hasattr(self._viewer, 'show_unstable_warning'):
            self._viewer.show_unstable_warning(om, nid)

    def _on_joint_revert(self):
        """모든 접합 변경 초기화 → 자동 규칙 상태로(확인 후)."""
        if not self._scene.joint_overrides:
            return
        ret = QMessageBox.question(
            self, '모든 접합 변경 초기화',
            '이 디자인의 모든 접합 변경(제거·핀·강접·추가)을 지우고\n'
            '자동 규칙 상태로 되돌립니다. 계속할까요?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self._scene.clear_joint_overrides()
        self._run_joint_edit_preview(force=True)
        self._joint_selected = None
        self._joint_panel.clear_selection()
        self._viewer.clear_ops_joint_highlight()

    def _design_signature(self):
        """현재 디자인(부재 구성)의 시그니처 — 부재 id·위치·치수·회전 기반.
        접합 탭 진입 사이에 디자인이 바뀌었는지 비교하는 데 쓴다."""
        parts = []
        for cid, c in sorted(self._scene.components.items()):
            pos = tuple(round(float(v), 1) for v in c.position)
            dims = tuple(sorted((k, round(float(v), 1))
                                for k, v in c.dimensions.items()))
            parts.append((cid, pos, dims, int(getattr(c, 'rotation', 0))))
        return hash(tuple(parts))

    def _maybe_prompt_joint_choice(self):
        """접합 탭 진입 시 접합 오버라이드 처리 정책(3 케이스).

        (1) 불러오기 직후(+디자인 미변경): '저장본 사용 / 새 자동계산' 선택.
        (2) 직전 접합 탭 진입 이후 디자인이 바뀜: 무조건 리셋(자동 규칙).
        (3) 디자인 미변경 + 접합 변경 후 왕복: '유지 / 초기화' 선택.
        프리뷰(_run_joint_edit_preview) 전에 호출된다.
        """
        scene = self._scene
        cur_sig = self._design_signature()
        last_sig = getattr(self, '_joint_last_design_sig', None)
        pending = getattr(scene, '_joint_overrides_pending_choice', False)

        if pending and scene.joint_overrides:
            # 케이스 1 — 불러온 저장본.
            box = QMessageBox(self)
            box.setWindowTitle('접합 설정 불러오기')
            box.setText('저장된 접합 변경 사항이 있습니다.\n'
                        '저장된 접합을 사용할까요, 아니면 새로 자동 계산할까요?')
            box.addButton('저장된 접합 사용', QMessageBox.AcceptRole)
            new_btn = box.addButton('새로 자동 계산', QMessageBox.DestructiveRole)
            box.exec_()
            if box.clickedButton() is new_btn:
                scene.clear_joint_overrides()
            scene._joint_overrides_pending_choice = False
        elif (scene.joint_overrides and last_sig is not None
              and cur_sig != last_sig):
            # 케이스 2 — 디자인이 바뀌면 접합 변경이 위치와 안 맞을 수 있어 무조건 리셋.
            scene.clear_joint_overrides()
            QMessageBox.information(
                self, '접합 초기화',
                '디자인이 변경되어 기존 접합 변경을 초기화하고\n'
                '자동 규칙으로 다시 계산합니다.')
        elif (scene.joint_overrides and last_sig is not None
              and cur_sig == last_sig):
            # 케이스 3 — 디자인 그대로 + 접합 변경 후 재진입.
            ret = QMessageBox.question(
                self, '접합 설정',
                '저장된 접합 변경을 그대로 유지할까요?\n'
                "'아니오'를 누르면 초기화하고 자동 규칙으로 계산합니다.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret == QMessageBox.No:
                scene.clear_joint_overrides()
        self._joint_last_design_sig = cur_sig

    # ── 접합 추가 (2026-05-25 2단계) ───────────────────────

    def _on_joint_add_mode(self, on: bool):
        """접합 추가 모드 토글. 끄면 진행 중 첫 점·후보·고스트 정리."""
        self._joint_add_mode = bool(on)
        if hasattr(self._viewer, 'clear_ops_joint_highlight'):
            self._viewer.clear_ops_joint_highlight()
        self._reset_add_progress()

    def _reset_add_progress(self):
        """접합 추가 진행 상태(첫 점·후보·고스트·선) 모두 정리."""
        self._joint_add_first = None
        self._joint_add_cands = []
        self._joint_add_snap = None
        v = self._viewer
        for m in ('clear_joint_ghost', 'clear_joint_candidates',
                  'clear_joint_line_ghost'):
            if hasattr(v, m):
                getattr(v, m)()

    def _validate_add(self, a: dict, b: dict, right_angle: bool = False):
        """신규 접합 두 점 검증(모서리 위 점). 반환 (가능여부, 안내문).
        right_angle=True 면 직각(ㄴ자) — 평면·높이 둘 다 다른 두 점을 허용."""
        import math
        ca = int(a.get('comp', 0) or 0)
        cb = int(b.get('comp', 0) or 0)
        if ca != 0 and cb != 0 and ca == cb:
            return False, '같은 부재의 두 점입니다. 서로 다른 부재의 점을 골라야 합니다.'
        ax, ay, az = a['point']
        bx, by, bz = b['point']
        dist = math.dist((ax, ay, az), (bx, by, bz))
        if dist > 400.0:
            return False, f'두 점이 너무 멉니다 ({dist:.0f}mm > 400mm).'
        tol = 50.0
        dx, dy, dz = abs(ax - bx), abs(ay - by), abs(az - bz)
        if right_angle:
            if not (dz > tol and (dx > tol or dy > tol)):
                return False, ('직각(ㄴ자) 접합은 평면과 높이가 모두 다른 '
                               '두 점이어야 합니다.')
        else:
            if (dx > tol) + (dy > tol) + (dz > tol) != 1:
                return False, ('두 점은 수평 또는 수직(한 축만 차이)으로만 '
                               '연결할 수 있습니다. (직각 결합은 옵션을 켜세요.)')
        return True, ''

    def _update_joint_ghost(self, pos):
        """접합 추가 모드 — 마우스 위치 미리보기.
        첫 점 전: 모서리 점 고스트. 첫 점 후: 후보에 스냅(하늘색→초록) + 선 고스트."""
        om = self._joint_om
        am = self._joint_am
        if om is None:
            return
        if self._joint_add_first is None:
            nd = self._viewer.pick_ops_edge_point_at(pos, om, am)
            if nd is None:
                self._viewer.clear_joint_ghost()
            else:
                self._viewer.show_joint_ghost(nd['point'], nd['snapped'])
            return
        # 첫 점 이후 — 후보 우선 스냅.
        p_first = self._joint_add_first['point']
        hit = self._viewer.pick_nearest_candidate(pos, self._joint_add_cands)
        if hit is not None:
            q, _idx = hit
            self._joint_add_snap = q
            self._viewer.show_joint_ghost((q[0], q[1], q[2]), True)
            self._viewer.show_joint_line_ghost(p_first, (q[0], q[1], q[2]))
            return
        self._joint_add_snap = None
        nd = self._viewer.pick_ops_edge_point_at(pos, om, am)
        if nd is None:
            self._viewer.clear_joint_ghost()
            self._viewer.clear_joint_line_ghost()
        else:
            self._viewer.show_joint_ghost(nd['point'], nd['snapped'])
            self._viewer.show_joint_line_ghost(p_first, nd['point'])

    def _on_joint_add_pick(self, pos):
        """접합 추가 모드 좌클릭 — 모서리 위 점 두 개로 신규 접합 생성.
        둘째 점은 스냅한 후보가 있으면 그 후보를, 없으면 모서리 점을 쓴다."""
        om = self._joint_om
        am = self._joint_am
        if om is None:
            return
        if self._joint_add_first is None:
            nd = self._viewer.pick_ops_edge_point_at(pos, om, am)
            if nd is None:
                return
            self._joint_add_first = nd
            self._viewer.show_joint_ghost(nd['point'], nd['snapped'])
            from modular_3d.analysis.joint_rules import candidate_joint_points
            cands = candidate_joint_points(
                om, nd['point'], int(nd.get('comp', 0) or 0),
                right_angle=self._joint_panel.is_add_right_angle())
            self._joint_add_cands = cands
            self._joint_add_snap = None
            self._viewer.show_joint_candidates(cands)
            self._joint_panel.set_add_hint('점2를 클릭하세요(하늘색 점에 스냅).')
            return
        a = self._joint_add_first
        snap = self._joint_add_snap
        if snap is not None:
            # 후보점(다른 부재 보 위 선점) — 노드 추가형. 색이 바뀐(스냅된)
            # 그대로 실제 접합도 그 자리에 노드를 만들어 결합한다.
            b = {'point': (snap[0], snap[1], snap[2]),
                 'comp': int(snap[3]) if len(snap) > 3 else 0,
                 'snapped': False}
        else:
            nd = self._viewer.pick_ops_edge_point_at(pos, om, am)
            if nd is None:
                return
            b = nd
        # 끝점 종류 — 선 위 점(주황·미스냅)이면 그 자리에 보 분할로 노드 생성,
        # 꼭지점(초록·스냅)이면 기존 노드 스냅. 사용자가 본 색과 실제 접합 일치.
        a_on_edge = not bool(a.get('snapped', False))
        b_on_edge = not bool(b.get('snapped', False))
        right = self._joint_panel.is_add_right_angle()
        single = self._joint_panel.is_add_single_layer()
        ok, msg = self._validate_add(a, b, right_angle=right)
        if not ok:
            QMessageBox.information(self, '접합 추가 불가', msg)
            self._reset_add_progress()
            self._joint_panel.set_add_hint('점1을 다시 클릭하세요.')
            return
        from modular_3d.model.joint_override import (
            JointOverride, PIN_DOFS, RIGID_DOFS)
        rigid = self._joint_panel.is_add_rigid()
        ax, ay, az = a['point']
        bx, by, bz = b['point']
        ov = JointOverride(
            kind='add', a_xy=(ax, ay), b_xy=(bx, by),
            z_a=az, z_b=bz,
            a_group=self._comp_group(int(a.get('comp', 0) or 0)),
            b_group=self._comp_group(int(b.get('comp', 0) or 0)),
            add_dofs=(RIGID_DOFS if rigid else PIN_DOFS),
            single_layer=single, right_angle=right,
            a_on_edge=a_on_edge, b_on_edge=b_on_edge,
        )
        self._scene.set_joint_override(ov)
        self._run_joint_edit_preview(force=True)
        self._reset_add_progress()
        self._joint_panel.set_add_hint('추가됨. 다음 점1을 클릭하세요.')

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

    def _on_room_mode_toggle(self, checked: bool):
        """팔레트 '실 모드' 토글 → 배치 탭 2D 캔버스 편집 모드 전환(2단계)."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'set_edit_mode'):
            canvas.set_edit_mode('room' if checked else 'component')
        if not checked:
            self._show_room_props(None)

    def _on_room_draw(self):
        """팔레트 '실 그리기' → 배치 탭 2D 캔버스 실 그리기 모드 진입(2단계)."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'start_room_draw'):
            canvas.start_room_draw()

    def _on_opening_mode_toggle(self, checked: bool):
        """팔레트 '개구부 모드' 토글 → 배치 탭 캔버스 편집 모드 전환(3단계)."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'set_edit_mode'):
            canvas.set_edit_mode('opening' if checked else 'component')

    def _on_opening_add(self):
        """팔레트 '개구부 추가' → 다음 부재 클릭으로 개구부 배치(3단계)."""
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'start_opening_add'):
            canvas.start_opening_add()

    def _on_wall_place(self):
        """팔레트 '벽 배치(내벽)' → 부재 배치 머신으로 내벽 종속 배치 시작.

        부모 클릭(DEPENDENCY_PICK) → 길이 입력 → 고스트(R/V/클릭) 흐름은
        캔틸레버 등 기존 종속 배치와 동일. 머신은 component 편집 모드에서만
        라우팅되므로 실·벽/개구부 토글을 먼저 해제한다.
        """
        from modular_3d.model import ComponentType
        canvas = getattr(self._f5_panel, 'canvas', None)
        if canvas is None:
            return
        self._palette.clear_special_modes()
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
                # [2026-05-25] 접합부 조정 탭: 좌클릭 → 추가 모드면 노드 픽킹,
                # 편집 모드면 접합 픽킹.
                if (btn == Qt.LeftButton
                        and self._tabs.currentIndex() == TAB_JOINT_EDIT):
                    if self._joint_add_mode:
                        self._on_joint_add_pick(pos)
                        return True
                    if self._joint_edit_mode:
                        self._on_joint_pick(pos)
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
                if (self._joint_add_mode
                        and self._tabs.currentIndex() == TAB_JOINT_EDIT):
                    self._update_joint_ghost(pos)
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
    app = QApplication.instance() or QApplication(sys.argv)
    _apply_app_font(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
