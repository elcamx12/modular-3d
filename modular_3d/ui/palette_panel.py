"""좌측 부재 팔레트 패널 — 1~9 단축키 버튼화.

설계:
- 키 1~9 와 1:1 매핑된 버튼 목록.
- 버튼 클릭 시 등록된 콜백(`on_select(comp_type)`) 호출 — Controller 의
  F5 캔버스 입구(`_f5_on_type_key`) 와 동일한 진입점을 사용해
  키 입력과 완전 동일한 흐름으로 통합한다.
- 디자인 탭에서만 보이며, 폭은 main_3d 에서 setFixedWidth 로 고정.

[부재 타입과 매핑]
  1 → MODULE
  2 → FLOOR_PANEL
  3 → STRUCT_WALL  (표시명 '벽 패널')
  4 → CANTILEVER_BEAM
  5 → CANTILEVER_SLAB
  6 → MID_BEAM
  7 → MID_COLUMN
  8 → VERTICAL_MODULE  (표시명 '수직 모듈(3층)')
  9 → CORE  (RC 코어벽, 한 장 단위. 슬래브는 코어 슬래브 재생성으로 생성)

[상세 설계 모드 — 2026-05-30 단일 모드 통합]
- 기존 '실·벽 배치 모드' + '개구부 모드' 두 상호배타 토글을 '상세 설계' 토글
  하나로 통합. ON 이면 부재 팔레트 비활성 + 실 지정/벽/개구부 버튼 활성.
- 각 버튼이 캔버스 편집 모드를 알아서 전환한다(실 지정→room, 개구부→opening,
  벽→component 배치 머신). '선택 해제(Esc)' 버튼은 키보드 Esc 로 충분해 제거.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QFrame,
)

from modular_3d.model import ComponentType


# 팔레트 항목 — (key, comp_type, 라벨, 설명, 활성여부)
PALETTE_ITEMS = [
    ('1', ComponentType.MODULE,           '모듈',           '3400×6820×3400', True),
    ('2', ComponentType.FLOOR_PANEL,      '바닥패널',       '3400×5000×200',  True),
    ('3', ComponentType.STRUCT_WALL,      '벽 패널',        '벽길이×200×3400', True),
    ('4', ComponentType.CANTILEVER_BEAM,  '캔틸레버보',     '돌출길이만 입력', True),
    ('5', ComponentType.CANTILEVER_SLAB,  '캔틸레버슬래브', '돌출×폭',         True),
    ('6', ComponentType.MID_BEAM,         '중간보',         '보 길이만 입력',   True),
    ('7', ComponentType.MID_COLUMN,       '중간기둥',       '높이만 입력',      True),
    ('8', ComponentType.VERTICAL_MODULE,  '수직 모듈(3층)', '3400×3400×10240', True),
    ('9', ComponentType.CORE,             'RC 코어',        '벽길이×두께×3400 (슬래브는 코어벽 동작 후 자동 생성)', True),
]


class PalettePanel(QWidget):
    """좌측 부재 팔레트 — 디자인 탭 전용."""

    def __init__(self,
                 on_select: Callable[[Optional[ComponentType]], None],
                 on_room_draw: Optional[Callable[[], None]] = None,
                 on_detail_mode_toggle: Optional[Callable[[bool], None]] = None,
                 on_opening_add: Optional[Callable[[], None]] = None,
                 on_wall_place: Optional[Callable[[], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_select = on_select
        # 실 지정 진입 콜백. 주입되면 '실 지정' 버튼 활성(상세 설계 ON 시).
        self._on_room_draw = on_room_draw
        # 내벽 배치 진입 콜백. 주입되면 '벽' 버튼 활성(상세 설계 ON 시).
        self._on_wall_place = on_wall_place
        # 상세 설계 모드 토글 콜백. True=상세 설계, False=부재 배치.
        self._on_detail_mode_toggle = on_detail_mode_toggle
        # 개구부 추가 콜백.
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
            btn.clicked.connect(lambda _checked=False, t=ctype: self._on_select(t))
            self._buttons.append(btn)
            lay.addWidget(btn)

            d = QLabel(f'  {desc}')
            d.setStyleSheet('color: #666; font-size: 10px;')
            d.setWordWrap(True)
            lay.addWidget(d)

        # (코어 슬래브는 코어벽 동작 완료 시 자동 재생성 — 수동 버튼 제거.)

        # ── 상세 설계 모드 (2026-05-30 단일 모드 통합) ──────
        # ON 이면 부재 팔레트(+코어)는 비활성, 실 지정/벽/개구부 버튼이 활성된다.
        sep_detail = QFrame()
        sep_detail.setFrameShape(QFrame.HLine)
        sep_detail.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep_detail)

        self._detail_mode_btn = QPushButton('상세 설계')
        self._detail_mode_btn.setCheckable(True)
        self._detail_mode_btn.setStyleSheet(
            'QPushButton{padding:4px 6px;}'
            'QPushButton:checked{background-color:#9be79b; font-weight:bold;}')
        if self._on_detail_mode_toggle is not None:
            self._detail_mode_btn.toggled.connect(self._on_detail_mode_toggled)
        else:
            self._detail_mode_btn.setEnabled(False)
        lay.addWidget(self._detail_mode_btn)

        # 실 지정 — 상세 설계 ON 일 때만 활성.
        self._room_btn = QPushButton('실 지정')
        self._room_btn.setStyleSheet(
            'background-color: #e6ffe6; padding: 4px 6px;')
        if self._on_room_draw is not None:
            self._room_btn.clicked.connect(lambda: self._on_room_draw())
        self._room_btn.setEnabled(False)
        lay.addWidget(self._room_btn)

        # 벽 (내벽 배치) — 상세 설계 ON 일 때만 활성.
        self._wall_btn = QPushButton('벽')
        self._wall_btn.setStyleSheet(
            'background-color: #ffe6cc; padding: 4px 6px;')
        if self._on_wall_place is not None:
            self._wall_btn.clicked.connect(lambda: self._on_wall_place())
        self._wall_btn.setEnabled(False)
        lay.addWidget(self._wall_btn)

        # 개구부 — 벽 바로 아래. 상세 설계 ON 일 때만 활성.
        self._opening_add_btn = QPushButton('개구부')
        self._opening_add_btn.setStyleSheet(
            'background-color: #fff0e0; padding: 4px 6px;')
        if self._on_opening_add is not None:
            self._opening_add_btn.clicked.connect(lambda: self._on_opening_add())
        self._opening_add_btn.setEnabled(False)
        lay.addWidget(self._opening_add_btn)

        detail_hint = QLabel(
            '  상세 설계 ON → 실 지정/벽/개구부 사용\n'
            '  실 지정: 좌클릭=점, Enter=완료, 우클릭/Back=취소점 · 실 클릭=선택, Del=삭제\n'
            '  벽: 부모 클릭 → 길이 입력 → R 방향/V 앵커/클릭 확정\n'
            '  개구부: "개구부" 후 벽/슬래브 클릭 · 개구부 클릭=선택, Del=삭제')
        detail_hint.setStyleSheet('color: #666; font-size: 10px;')
        detail_hint.setWordWrap(True)
        lay.addWidget(detail_hint)

        lay.addStretch(1)

    # ── 모드 토글 처리 ─────────────────────────────────────
    def _set_component_buttons_enabled(self, on: bool):
        """부재 팔레트 버튼 활성/비활성 일괄 토글."""
        for b in self._buttons:
            b.setEnabled(on)

    def _on_detail_mode_toggled(self, checked: bool):
        """상세 설계 ON/OFF — 부재 버튼 비활성/활성, 실 지정/벽/개구부 활성/비활성."""
        self._set_component_buttons_enabled(not checked)
        self._room_btn.setEnabled(checked and self._on_room_draw is not None)
        self._wall_btn.setEnabled(checked and self._on_wall_place is not None)
        self._opening_add_btn.setEnabled(
            checked and self._on_opening_add is not None)
        if self._on_detail_mode_toggle is not None:
            self._on_detail_mode_toggle(checked)

    def set_detail_mode(self, on: bool):
        """외부에서 상세 설계 토글 상태 동기화(시그널 발생)."""
        if self._detail_mode_btn.isChecked() != on:
            self._detail_mode_btn.setChecked(on)

    def clear_special_modes(self):
        """상세 설계 토글을 OFF — 부재 배치 모드로 복귀(시그널 발생)."""
        if self._detail_mode_btn.isChecked():
            self._detail_mode_btn.setChecked(False)
