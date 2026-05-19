"""우측 디자인 속성 패널 — 활성/선택 부재 정보 + AI 최적배치 입력 폼.

[현재 구현]
- 활성 부재 라벨 (팔레트에서 선택한 부재)
- 선택 부재 정보 라벨 (씬에서 클릭 선택한 부재)
- AI 최적배치 실행 입력(코어 수·세대 수·베이·층수 등)

회전·앵커 콤보박스는 만들지 않는다 (사용자 결정 — R/V 키 기존 동작 유지).

[이력 2026-05-13] 접합부 변경 모드 / 합성거동 옵션은
`ui/joint_edit_panel.py` 의 JointEditPanel 로 이동하여, 새 '접합부 조정' 탭의
우측 패널에 마운트됨.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QDoubleSpinBox, QPushButton, QSpinBox,
)

from modular_3d.model import ComponentType, Scene


# 부재 타입별 한글 이름 (single source of truth)
from modular_3d.model import TYPE_NAMES


class DesignPropertiesPanel(QWidget):
    """디자인 탭 우측 패널.

    [시그널]
    floors_changed(int): AI 최적배치 영역의 층수 SpinBox 가 변경됐을 때 발화.
                        외부(F5 패널 등) 와 양방향 연동에 사용.
    """

    # (2026-05-13) 층수 외부 연동용 시그널.
    floors_changed = pyqtSignal(int)

    def __init__(self, scene: Scene, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._scene = scene
        self._setup_ui()
        self.refresh_active(None)
        self.refresh_selected(-1)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        title = QLabel('속성 패널')
        title.setStyleSheet('font-weight: bold; font-size: 12px;')
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)

        # ── 활성/선택 부재 정보 ─────────────────────────
        self._active_label = QLabel('활성 부재: (없음)')
        self._active_label.setStyleSheet('color: #06c; font-size: 11px;')
        self._active_label.setWordWrap(True)
        self._active_label.setMinimumWidth(0)
        lay.addWidget(self._active_label)

        self._selected_label = QLabel('선택 부재: (없음)')
        self._selected_label.setStyleSheet('font-size: 11px;')
        self._selected_label.setWordWrap(True)
        self._selected_label.setMinimumWidth(0)
        lay.addWidget(self._selected_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        lay.addWidget(sep2)

        # ── 해석 좌표 안내 ─────────────────────────────
        # 모듈 외관 z = 0 ~ 3400 mm 이지만 해석 모델의 보 중심선은
        # z = 0 ~ 3200 (=h - SECTION_W=200) 위치. 사용자가 코어 슬래브 z 와
        # 보 격자 끝 z 차이를 헷갈리지 않도록 한 줄로 안내.
        _z_note = QLabel('해석 좌표: 보 중심선 z = h − 200 mm '
                         '(예: 모듈 천장보 = z 3200, 외관 = z 3400)')
        _z_note.setStyleSheet('color: #666; font-size: 10px;')
        _z_note.setWordWrap(True)
        lay.addWidget(_z_note)

        # (2026-05-13) 코어 CAD 입력 섹션 제거 — 코어벽 만드는 기능(팔레트) +
        # 코어 슬래브 생성 버튼이 별도 제공되므로 본 자리 불필요.

        # [2026-05-13 접합부조정탭 Phase 3] 접합부 변경 토글 + 합성거동 옵션은
        # 새 '접합부 조정' 탭의 JointEditPanel 로 이동. 본 패널에선 제거.

        # ── AI 최적배치 실행 (2026-05-13) ──────────────────
        sep4 = QFrame()
        sep4.setFrameShape(QFrame.HLine)
        lay.addWidget(sep4)

        ai_title = QLabel('AI 최적배치 실행')
        ai_title.setStyleSheet('font-weight: bold; font-size: 11px;')
        lay.addWidget(ai_title)

        # 입력 4 종
        def _mk_int_row(label_text: str, value: int, vmin: int, vmax: int,
                        step: int = 1) -> 'tuple[QHBoxLayout, QSpinBox]':
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            sb = QSpinBox()
            sb.setRange(vmin, vmax)
            sb.setSingleStep(step)
            sb.setValue(value)
            sb.setFixedWidth(80)
            row.addWidget(sb)
            row.addStretch(1)
            return row, sb

        # 코어 수 (1~2, 기본 1)
        row0, self._ai_core_count = _mk_int_row(
            '코어 수:', 1, 1, 2,
        )
        lay.addLayout(row0)

        # 층 당 세대 수
        row1, self._ai_units_per_floor = _mk_int_row(
            '층 당 세대 수:', 4, 1, 20,
        )
        lay.addLayout(row1)

        # 베이 (베이 수)
        row2, self._ai_bays = _mk_int_row(
            '베이:', 2, 1, 10,
        )
        lay.addLayout(row2)

        # 층 수 — F5 패널과 양방향 연동.
        row3, self._ai_floors = _mk_int_row(
            '층 수 (3의 배수):', 3, 3, 24, step=3,
        )
        lay.addLayout(row3)
        self._ai_floors.valueChanged.connect(self._on_ai_floors_changed)
        self._ai_floors_internal_update = False  # 외부 갱신 시 신호 재발화 방지

        # 세대 면적 (m²)
        row4 = QHBoxLayout()
        row4.addWidget(QLabel('세대 면적 (m²):'))
        self._ai_unit_area = QDoubleSpinBox()
        self._ai_unit_area.setRange(20.0, 200.0)
        self._ai_unit_area.setSingleStep(1.0)
        self._ai_unit_area.setValue(60.0)
        self._ai_unit_area.setSuffix(' m²')
        self._ai_unit_area.setFixedWidth(95)
        row4.addWidget(self._ai_unit_area)
        row4.addStretch(1)
        lay.addLayout(row4)

        # AI 최적배치 탐색 버튼 (비활성 상태)
        self._ai_search_btn = QPushButton('AI 최적배치 탐색 (추후 구현)')
        self._ai_search_btn.setFixedHeight(34)
        self._ai_search_btn.setEnabled(False)
        self._ai_search_btn.setToolTip(
            '기존 배치를 고정하고 빈 자리에 AI 로 최적 배치를 시도합니다. '
            '현재 비활성 상태 — 추후 구현 예정.'
        )
        lay.addWidget(self._ai_search_btn)

        lay.addStretch(1)

    # ── AI 최적배치 영역 외부 API ─────────────────────────

    def _on_ai_floors_changed(self, n: int):
        """AI 층수 SpinBox 사용자 입력 → 외부에 시그널 발화."""
        if self._ai_floors_internal_update:
            return
        self.floors_changed.emit(int(n))

    def set_floors_from_external(self, n: int) -> None:
        """외부(F5 패널 등)에서 층수 변경 시 호출 — 재귀 발화 방지하며 동기화."""
        if int(self._ai_floors.value()) == int(n):
            return
        self._ai_floors_internal_update = True
        try:
            self._ai_floors.setValue(int(n))
        finally:
            self._ai_floors_internal_update = False

    # ── 외부 API ─────────────────────────────────────

    def refresh_active(self, comp_type: Optional[ComponentType]):
        """활성 부재 변경 시 호출 — 라벨 갱신."""
        if comp_type is None:
            self._active_label.setText('활성 부재: (없음)')
        else:
            name = TYPE_NAMES.get(comp_type, str(comp_type))
            self._active_label.setText(f'활성 부재: {name}')

    def refresh_selected(self, comp_id: int):
        """선택 부재 변경 시 호출 — 라벨 갱신."""
        if comp_id <= 0 or comp_id not in self._scene.components:
            self._selected_label.setText('선택 부재: (없음)')
            return
        comp = self._scene.components[comp_id]
        name = TYPE_NAMES.get(comp.comp_type, str(comp.comp_type))
        x, y, z = (float(comp.position[0]),
                   float(comp.position[1]),
                   float(comp.position[2]))
        self._selected_label.setText(
            f'선택 부재: id={comp_id}\n  타입={name}\n'
            f'  위치=({x:.0f}, {y:.0f}, {z:.0f})'
        )

