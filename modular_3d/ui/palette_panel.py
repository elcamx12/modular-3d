"""좌측 부재 팔레트 패널 — 1~9 단축키 버튼화.

설계:
- 키 1~9 와 1:1 매핑된 버튼 목록.
- 버튼 클릭 시 등록된 콜백(`on_select(comp_type)`) 호출 — Controller 의
  F5 캔버스 입구(`_f5_on_type_key`) 와 동일한 진입점을 사용해
  키 입력과 완전 동일한 흐름으로 통합한다.
- 디자인 탭에서만 보이며, 폭은 main_3d 에서 setFixedWidth 로 180 고정.

[부재 타입과 매핑]
  1 → MODULE
  2 → FLOOR_PANEL
  3 → STRUCT_WALL
  4 → CANTILEVER_BEAM
  5 → CANTILEVER_SLAB
  6 → MID_BEAM
  7 → MID_COLUMN
  8 → VERTICAL_MODULE
  9 → CORE  (2026-05-11 활성화 — RC 코어벽, 한 장 단위. 자동으로 N+1 장 + 슬래브 생성)
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QFrame,
)

from modular_3d.model import ComponentType


# 팔레트 항목 — (key, comp_type or None, 라벨, 설명, 활성여부)
PALETTE_ITEMS = [
    ('1', ComponentType.MODULE,           '모듈',           '3400×6820×3400', True),
    ('2', ComponentType.FLOOR_PANEL,      '바닥패널',       '3400×5000×200',  True),
    ('3', ComponentType.STRUCT_WALL,      '구조벽',         '벽길이×200×3400', True),
    ('4', ComponentType.CANTILEVER_BEAM,  '캔틸레버보',     '돌출길이만 입력', True),
    ('5', ComponentType.CANTILEVER_SLAB,  '캔틸레버슬래브', '돌출×폭',         True),
    ('6', ComponentType.MID_BEAM,         '중간보',         '보 길이만 입력',   True),
    ('7', ComponentType.MID_COLUMN,       '중간기둥',       '높이만 입력',      True),
    ('8', ComponentType.VERTICAL_MODULE,  '수직 3층 모듈',  '3400×3400×10240', True),
    ('9', ComponentType.CORE,             'RC 코어',        '벽길이×두께×3400 (RC 코어 슬래브 재생성 버튼이 모든 층 슬래브를 자동 생성)', True),
]


class PalettePanel(QWidget):
    """좌측 부재 팔레트 — 디자인 탭 전용."""

    def __init__(self,
                 on_select: Callable[[Optional[ComponentType]], None],
                 on_regen_core_slabs: Optional[Callable[[], None]] = None,
                 on_room_draw: Optional[Callable[[], None]] = None,
                 on_room_mode_toggle: Optional[Callable[[bool], None]] = None,
                 on_opening_mode_toggle: Optional[Callable[[bool], None]] = None,
                 on_opening_add: Optional[Callable[[], None]] = None,
                 on_wall_place: Optional[Callable[[], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_select = on_select
        # 코어 슬래브 수동 생성/재생성 콜백 (main_3d 에서 컨트롤러 메서드 주입).
        self._on_regen_core_slabs = on_regen_core_slabs
        # 실 그리기 진입 콜백 (2단계). 주입되면 '실 그리기' 버튼 활성.
        self._on_room_draw = on_room_draw
        # 내벽 배치 진입 콜백 (2026-05-24). 주입되면 '벽 배치' 버튼 활성.
        self._on_wall_place = on_wall_place
        # 실 모드 토글 콜백 (2단계). True=실 모드, False=부재 모드.
        self._on_room_mode_toggle = on_room_mode_toggle
        # 개구부 모드 토글 + 추가 콜백 (3단계).
        self._on_opening_mode_toggle = on_opening_mode_toggle
        self._on_opening_add = on_opening_add
        self._buttons: list[QPushButton] = []
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        title = QLabel('부재 팔레트')
        title.setStyleSheet('font-weight: bold; font-size: 12px;')
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep)

        for key, ctype, label, desc, enabled in PALETTE_ITEMS:
            btn = QPushButton(f'[{key}] {label}')
            btn.setStyleSheet('text-align: left; padding: 4px 6px;')
            btn.setEnabled(enabled)
            # 클릭 → 콜백. 비활성 항목(코어)은 시각화만, 콜백은 ComponentType=None 으로
            # 호출되어 Controller 가 무시하도록 한다.
            btn.clicked.connect(lambda _checked=False, t=ctype: self._on_select(t))
            self._buttons.append(btn)
            lay.addWidget(btn)

            d = QLabel(f'  {desc}')
            d.setStyleSheet('color: #666; font-size: 10px;')
            d.setWordWrap(True)
            lay.addWidget(d)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep2)

        self._clear_btn = QPushButton('선택 해제 (Esc)')
        self._clear_btn.clicked.connect(lambda: self._on_select(None))
        lay.addWidget(self._clear_btn)

        # ── 코어 슬래브 수동 생성/재생성 버튼 ─────────────────
        # [정책 2026-05-12] 코어 슬래브는 더 이상 자동 생성되지 않는다.
        # 이 버튼을 눌렀을 때에만 씬의 모든 코어 그룹에 대해 슬래브를
        # 만들거나 다시 그린다.
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep3)

        self._core_slab_btn = QPushButton('RC 코어 슬래브 재생성')
        self._core_slab_btn.setStyleSheet(
            'background-color: #d8e6ff; padding: 4px 6px;')
        if self._on_regen_core_slabs is not None:
            self._core_slab_btn.clicked.connect(
                lambda: self._on_regen_core_slabs())
        else:
            self._core_slab_btn.setEnabled(False)
        lay.addWidget(self._core_slab_btn)

        hint = QLabel('  코어벽 추가/이동/층변경 후 직접 누르세요')
        hint.setStyleSheet('color: #666; font-size: 10px;')
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # ── 실(공간) 모드 (2026-05-24 2단계) ───────────────
        sep4 = QFrame()
        sep4.setFrameShape(QFrame.HLine)
        sep4.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep4)

        # 실·벽 배치 모드 토글 — ON 이면 부재 버튼 비활성, 실 그리기/선택 활성.
        self._room_mode_btn = QPushButton('실·벽 배치 모드')
        self._room_mode_btn.setCheckable(True)
        self._room_mode_btn.setStyleSheet(
            'QPushButton{padding:4px 6px;}'
            'QPushButton:checked{background-color:#9be79b; font-weight:bold;}')
        if self._on_room_mode_toggle is not None:
            self._room_mode_btn.toggled.connect(self._on_room_mode_toggled)
        else:
            self._room_mode_btn.setEnabled(False)
        lay.addWidget(self._room_mode_btn)

        self._room_btn = QPushButton('실 그리기')
        self._room_btn.setStyleSheet(
            'background-color: #e6ffe6; padding: 4px 6px;')
        if self._on_room_draw is not None:
            self._room_btn.clicked.connect(lambda: self._on_room_draw())
        self._room_btn.setEnabled(False)   # 실·벽 배치 모드 ON 일 때만 활성
        lay.addWidget(self._room_btn)

        # 내벽 배치 — 부모(모듈·바닥패널·캔틸레버슬래브) 클릭 → 길이 입력 →
        # 고스트(R 방향/V 앵커/클릭 확정). 부재 배치 머신을 재사용하므로 클릭 시
        # 컴포넌트 편집 모드로 전환된다(핸들러가 처리). 항상 활성.
        self._wall_btn = QPushButton('벽 배치 (내벽)')
        self._wall_btn.setStyleSheet(
            'background-color: #ffe6cc; padding: 4px 6px;')
        if self._on_wall_place is not None:
            self._wall_btn.clicked.connect(lambda: self._on_wall_place())
        else:
            self._wall_btn.setEnabled(False)
        lay.addWidget(self._wall_btn)

        room_hint = QLabel('  실 그리기: 좌클릭=점, Enter=완료, 우클릭/Back=취소점 · '
                           '실 클릭=선택, Del=삭제\n'
                           '  벽 배치: 부모 클릭 → 길이 입력 → R 방향/V 앵커/클릭 확정')
        room_hint.setStyleSheet('color: #666; font-size: 10px;')
        room_hint.setWordWrap(True)
        lay.addWidget(room_hint)

        # ── 개구부 모드 (2026-05-24 3단계) ─────────────────
        sep5 = QFrame()
        sep5.setFrameShape(QFrame.HLine)
        sep5.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep5)

        self._opening_mode_btn = QPushButton('개구부 모드')
        self._opening_mode_btn.setCheckable(True)
        self._opening_mode_btn.setStyleSheet(
            'QPushButton{padding:4px 6px;}'
            'QPushButton:checked{background-color:#ffd29b; font-weight:bold;}')
        if self._on_opening_mode_toggle is not None:
            self._opening_mode_btn.toggled.connect(self._on_opening_mode_toggled)
        else:
            self._opening_mode_btn.setEnabled(False)
        lay.addWidget(self._opening_mode_btn)

        self._opening_add_btn = QPushButton('개구부 추가')
        self._opening_add_btn.setStyleSheet(
            'background-color: #fff0e0; padding: 4px 6px;')
        if self._on_opening_add is not None:
            self._opening_add_btn.clicked.connect(lambda: self._on_opening_add())
        self._opening_add_btn.setEnabled(False)   # 개구부 모드에서만
        lay.addWidget(self._opening_add_btn)

        op_hint = QLabel('  개구부 모드 ON → "개구부 추가" 후 벽/슬래브 클릭 · '
                         '개구부 클릭=선택, Del=삭제')
        op_hint.setStyleSheet('color: #666; font-size: 10px;')
        op_hint.setWordWrap(True)
        lay.addWidget(op_hint)

        lay.addStretch(1)

    # ── 모드 토글 처리 ─────────────────────────────────────
    def _set_component_buttons_enabled(self, on: bool):
        for b in self._buttons:
            b.setEnabled(on)
        if hasattr(self, '_clear_btn'):
            self._clear_btn.setEnabled(on)
        if hasattr(self, '_core_slab_btn'):
            self._core_slab_btn.setEnabled(
                on and self._on_regen_core_slabs is not None)

    def _on_room_mode_toggled(self, checked: bool):
        """실 모드 ON/OFF — 개구부 모드와 상호배타, 부재 버튼 비활성/활성."""
        if checked and self._opening_mode_btn.isChecked():
            self._opening_mode_btn.setChecked(False)   # 상호배타
        any_special = checked or self._opening_mode_btn.isChecked()
        self._set_component_buttons_enabled(not any_special)
        self._room_btn.setEnabled(checked and self._on_room_draw is not None)
        if self._on_room_mode_toggle is not None:
            self._on_room_mode_toggle(checked)

    def _on_opening_mode_toggled(self, checked: bool):
        """개구부 모드 ON/OFF — 실 모드와 상호배타, 부재 버튼 비활성/활성."""
        if checked and self._room_mode_btn.isChecked():
            self._room_mode_btn.setChecked(False)      # 상호배타
        any_special = checked or self._room_mode_btn.isChecked()
        self._set_component_buttons_enabled(not any_special)
        self._opening_add_btn.setEnabled(
            checked and self._on_opening_add is not None)
        if self._on_opening_mode_toggle is not None:
            self._on_opening_mode_toggle(checked)

    def set_room_mode(self, on: bool):
        """외부에서 실 모드 토글 상태 동기화(시그널 발생)."""
        if self._room_mode_btn.isChecked() != on:
            self._room_mode_btn.setChecked(on)

    def clear_special_modes(self):
        """실·벽/개구부 토글을 모두 OFF — 컴포넌트 모드로 복귀(시그널 발생).

        '벽 배치' 처럼 부재 배치 머신을 쓰는 동작 직전에 호출.
        """
        if self._opening_mode_btn.isChecked():
            self._opening_mode_btn.setChecked(False)
        if self._room_mode_btn.isChecked():
            self._room_mode_btn.setChecked(False)
