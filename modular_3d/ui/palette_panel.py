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
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_select = on_select
        # 코어 슬래브 수동 생성/재생성 콜백 (main_3d 에서 컨트롤러 메서드 주입).
        self._on_regen_core_slabs = on_regen_core_slabs
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

        clear_btn = QPushButton('선택 해제 (Esc)')
        clear_btn.clicked.connect(lambda: self._on_select(None))
        lay.addWidget(clear_btn)

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

        lay.addStretch(1)
