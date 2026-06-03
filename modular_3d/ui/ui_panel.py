"""
Qt UI — 치수 입력 패널, 상태바 관리.
"""
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QLineEdit, QStatusBar, QCheckBox, QComboBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QEvent
from PyQt5.QtGui import QIntValidator, QValidator

from modular_3d.model import AppState, ComponentType


class DimensionInputPanel(QWidget):
    """하단 치수 입력 패널. Tab으로 필드 이동, Enter 확정, Esc 취소."""

    confirmed = pyqtSignal(dict)    # {'width': float, 'depth': float, 'height': float}
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields = {}
        self._field_order = []
        self._move_mode = False
        self._move_axis = 'X'
        self._setup_ui()
        self.setVisible(False)

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self._type_label = QLabel('[모듈]')
        self._type_label.setStyleSheet('font-weight: bold;')
        layout.addWidget(self._type_label)

        validator = QIntValidator(1, 99999)

        # [2026-05-11] 기본 placeholder 값. configure_for_type 에서 부재별로 덮어씀.
        for name, label_text, default in [
            ('width', 'X:', '3400'),
            ('depth', 'Y:', '6820'),
            ('height', 'Z:', '3400'),
        ]:
            lbl = QLabel(label_text)
            edit = QLineEdit(default)
            edit.setValidator(validator)
            edit.setFixedWidth(80)
            edit.setAlignment(Qt.AlignRight)
            edit.installEventFilter(self)  # Enter/Esc/Tab 가로채기
            layout.addWidget(lbl)
            layout.addWidget(edit)
            layout.addWidget(QLabel('mm'))
            self._fields[name] = edit
            self._field_order.append(name)

        # 벽패널 전용 "바닥패널과 병합" 체크박스 (X1·X2·g4)
        # 기본 숨김. configure_for_type에서 벽패널일 때만 노출.
        self._merge_check = QCheckBox('바닥패널과 병합')
        self._merge_check.setChecked(True)  # 기본 ON
        self._merge_check.setVisible(False)
        layout.addWidget(self._merge_check)

        # 보 단면 타입(각형강관/H형강) — 보가 있는 직접배치 부재일 때만 노출.
        self._section_caption = QLabel('보:')
        self._section_combo = QComboBox()
        self._section_combo.addItem('각형강관', 'shs')
        self._section_combo.addItem('H형강', 'h')
        layout.addWidget(self._section_caption)
        layout.addWidget(self._section_combo)
        self._section_caption.setVisible(False)
        self._section_combo.setVisible(False)

        # 모드 라벨 (배치/정렬/IDLE 등 — c4)
        self._mode_label = QLabel('')
        self._mode_label.setStyleSheet('color: #88c; padding-left: 8px;')
        layout.addWidget(self._mode_label)

        layout.addStretch()
        layout.addWidget(QLabel('Enter: 확정  |  Esc: 취소'))

    def set_mode_text(self, text: str):
        """하단 dim 패널 좌측에 현재 모드 표시 (c4)."""
        self._mode_label.setText(text)

    def configure_for_type(self, comp_type: ComponentType):
        """부재 타입별 필드 표시/숨김."""
        # 보 단면 타입 콤보 — 직접 선택 부재(모듈·바닥패널·수직3층모듈·벽패널)만.
        # 종속(캔틸레버·중간보)은 부모를 따라가므로 숨김. 매 호출 기본값 각형강관.
        _show_section = comp_type in (
            ComponentType.MODULE, ComponentType.FLOOR_PANEL,
            ComponentType.VERTICAL_MODULE, ComponentType.STRUCT_WALL,
        )
        self._section_caption.setVisible(_show_section)
        self._section_combo.setVisible(_show_section)
        if _show_section:
            self._section_combo.setCurrentIndex(0)   # 기본 각형강관
        # 기본: 전부 활성
        for f in self._fields.values():
            f.setEnabled(True)

        # 병합 체크박스: 벽패널일 때만 노출
        self._merge_check.setVisible(comp_type == ComponentType.STRUCT_WALL)

        labels = {
            ComponentType.MODULE: '[모듈]',
            ComponentType.FLOOR_PANEL: '[바닥패널]',
            ComponentType.STRUCT_WALL: '[벽패널]',
            ComponentType.INTERIOR_WALL: '[내벽]',
            ComponentType.CANTILEVER_BEAM: '[캔틸레버보]',
            ComponentType.CANTILEVER_SLAB: '[캔틸레버슬래브]',
            ComponentType.MID_BEAM: '[중간보]',
            ComponentType.MID_COLUMN: '[중간기둥]',
            ComponentType.VERTICAL_MODULE: '[수직 3층 모듈]',
            ComponentType.CORE: '[RC 코어]',
        }
        self._type_label.setText(labels.get(comp_type, '[부재]'))

        if comp_type == ComponentType.MODULE:
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('3400')
        elif comp_type == ComponentType.FLOOR_PANEL:
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('200')
        elif comp_type == ComponentType.STRUCT_WALL:
            # width=벽길이, depth=200(고정), height=3400(고정, MODULE_HEIGHT_MM)
            self._fields['depth'].setEnabled(False)
            self._fields['depth'].setText('200')
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('3400')
        elif comp_type == ComponentType.INTERIOR_WALL:
            # 내벽: width=벽길이(입력), depth=100(두께 고정), height=자동(부모 검출).
            # 높이는 부모를 클릭한 시점에 컨트롤러가 부모 높이로 채운다 — 모듈이면
            # 모듈 높이, 바닥패널·캔틸레버슬래브면 같은 그룹 모듈 높이(없으면 표준 한 층).
            self._fields['depth'].setEnabled(False)
            self._fields['depth'].setText('100')
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('자동')
        elif comp_type == ComponentType.CANTILEVER_BEAM:
            # width=길이만 입력
            self._fields['depth'].setEnabled(False)
            self._fields['depth'].setText('200')
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('200')
        elif comp_type == ComponentType.CANTILEVER_SLAB:
            # [2026-05-11 Phase 2] 길이(돌출)만 입력. 폭(depth) 은 부모 외곽 변
            # 길이로 자동 결정(≤3400). 두께(height) 200 고정.
            self._fields['depth'].setEnabled(False)
            self._fields['depth'].setText('자동')
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('200')
        elif comp_type == ComponentType.MID_BEAM:
            # [2026-05-11 Phase 2] 길이도 자동 결정 (인접 기둥 또는 부모 외곽 보까지).
            # 사용자는 단면(depth=200) 만 확인. width 도 비활성화.
            self._fields['width'].setEnabled(False)
            self._fields['width'].setText('자동')
            self._fields['depth'].setEnabled(False)
            self._fields['depth'].setText('200')
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('200')
        elif comp_type == ComponentType.MID_COLUMN:
            # 높이만 입력
            self._fields['width'].setEnabled(False)
            self._fields['width'].setText('200')
            self._fields['depth'].setEnabled(False)
            self._fields['depth'].setText('200')
        elif comp_type == ComponentType.CORE:
            # RC 코어벽: width=벽 길이 (300~20000), depth=벽 두께 (200~500),
            # height=3400 (단층 모듈과 동일, 고정).
            self._fields['width'].setText('3400')
            self._fields['depth'].setText('300')
            self._fields['height'].setEnabled(False)
            self._fields['height'].setText('3400')
        elif comp_type == ComponentType.VERTICAL_MODULE:
            # 수직 3층 모듈: 가로/세로 3400 고정, 높이 10240(=3·3400+2·20) 고정.
            # [함정] 모든 필드를 disabled 로 두면 포커스가 없어 Enter 가 안 먹힘 →
            # 활성 상태로 두고 기본값만 채운 뒤, 실제 강제 덮어쓰기는 컨트롤러
            # _on_dimension_confirmed 에서 수행한다.
            self._fields['width'].setText('3400')
            self._fields['depth'].setText('3400')
            self._fields['height'].setText('10240')

    def set_defaults(self, dims: dict):
        """기본값 설정.

        [정책 2026-05-12] 비활성(disabled) 필드는 '자동' 라벨 보존을 위해
        덮어쓰지 않음. CANTILEVER_SLAB.depth, MID_BEAM.width 등이 해당.
        """
        for k, v in dims.items():
            if k in self._fields:
                edit = self._fields[k]
                if not edit.isEnabled():
                    continue  # '자동' 라벨 유지
                edit.setText(str(int(v)))

    def get_values(self) -> dict:
        """현재 입력값 반환. 벽패널일 때 'merge' 키 포함 (X1).

        [정책 2026-05-12] 비활성/자동/빈 값은 0.0 폴백 — 호출 측 (model.core
        의 generate_sub_components) 이 dimensions['width'] 직접 접근하므로
        키 누락 시 KeyError. 0 으로 만들어진 퇴화 부재는 후속 dep_snap 호출
        에서 부모 변 길이로 자동 채워짐.
        """
        result = {}
        for name, edit in self._fields.items():
            txt = edit.text().strip()
            # [2026-05-13 수정] 비활성 필드라도 '자동' 라벨이 아닌
            # 숫자 텍스트(예: MODULE.height='3400' 고정값) 면 그 값을 그대로
            # 사용해야 함. 이전엔 비활성=무조건 0.0 폴백으로 처리해서
            # 모듈 높이가 0 으로 넘어가 기둥 길이 0(=비표시) + 천장보가
            # 한 층 아래 위치로 어긋나는 문제가 있었음.
            if txt == '자동' or txt == '':
                result[name] = 0.0  # dep_snap 이 채워줌
                continue
            try:
                result[name] = float(txt)
            except ValueError:
                result[name] = 0.0
        # 벽패널 전용 merge 플래그 (체크박스 visible 여부로 판단)
        if self._merge_check.isVisible():
            result['merge'] = bool(self._merge_check.isChecked())
        # 보 단면 타입 (콤보 visible 일 때만)
        if self._section_combo.isVisible():
            result['section_type'] = self._section_combo.currentData()
        return result

    def activate(self, comp_type: ComponentType, defaults: dict):
        """패널 표시 + 첫 필드에 포커스."""
        self._move_mode = False
        self.configure_for_type(comp_type)
        self.set_defaults(defaults)
        self.setVisible(True)
        # 첫 활성 필드에 포커스
        for name in self._field_order:
            if self._fields[name].isEnabled():
                self._fields[name].setFocus()
                self._fields[name].selectAll()
                break

    def activate_move(self, axis_name: str):
        """이동 거리 입력 모드. axis_name = 'X' or 'Y'."""
        self._move_mode = True
        self._move_axis = axis_name
        self._type_label.setText(f'[이동 {axis_name}축]')
        # 모든 필드 숨기고 width만 사용
        for name in self._field_order:
            self._fields[name].setEnabled(name == 'width')
            if name != 'width':
                self._fields[name].setText('0')
        self._fields['width'].setValidator(QIntValidator(-99999, 99999))
        self._fields['width'].setText('0')
        self._fields['width'].setFocus()
        self._fields['width'].selectAll()
        self.setVisible(True)

    def get_move_distance(self) -> float:
        """이동/확장 거리 반환."""
        try:
            return float(self._fields['width'].text())
        except ValueError:
            return 0.0

    def deactivate(self):
        """패널 숨기기."""
        self.setVisible(False)
        # validator 복원 (이동 모드에서 음수 허용했던 것)
        self._fields['width'].setValidator(QIntValidator(1, 99999))
        self._move_mode = False

    def eventFilter(self, obj, event):
        """QLineEdit의 Enter/Esc/Tab을 가로채서 패널 동작으로 처리."""
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.confirmed.emit(self.get_values())
                return True
            elif key == Qt.Key_Escape:
                self.cancelled.emit()
                return True
            elif key == Qt.Key_Tab:
                self._focus_next()
                return True
        return super().eventFilter(obj, event)

    def _focus_next(self):
        """다음 활성 필드로 포커스 이동."""
        current = None
        for name in self._field_order:
            if self._fields[name].hasFocus():
                current = name
                break
        if current is None:
            return

        idx = self._field_order.index(current)
        for i in range(1, len(self._field_order) + 1):
            next_name = self._field_order[(idx + i) % len(self._field_order)]
            if self._fields[next_name].isEnabled():
                self._fields[next_name].setFocus()
                self._fields[next_name].selectAll()
                break


class StatusBarManager:
    """상태바 관리."""

    def __init__(self, status_bar: QStatusBar):
        self._bar = status_bar

        self._mode_label = QLabel('[IDLE]')
        self._mode_label.setStyleSheet('font-weight: bold; padding: 0 10px;')
        self._coord_label = QLabel('X: 0  Y: 0')
        self._coord_label.setStyleSheet('padding: 0 10px;')
        self._count_label = QLabel('부재: 0개')
        self._count_label.setStyleSheet('padding: 0 10px;')

        self._bar.addPermanentWidget(self._mode_label)
        self._bar.addPermanentWidget(self._coord_label)
        self._bar.addPermanentWidget(self._count_label)

    def update_mode(self, state: AppState):
        labels = {
            AppState.IDLE: '[IDLE]',
            AppState.PLACEMENT_PREVIEW: '[모듈 배치]',
            AppState.DIMENSION_INPUT: '[치수 입력]',
            AppState.SELECTED: '[선택됨]',
            AppState.MOVING: '[이동 중]',
        }
        self._mode_label.setText(labels.get(state, str(state)))

    def update_coords(self, x: float, y: float):
        self._coord_label.setText(f'X: {x:.0f}  Y: {y:.0f}')

    def update_count(self, n: int):
        self._count_label.setText(f'부재: {n}개')
