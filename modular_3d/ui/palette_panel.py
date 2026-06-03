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
        ensure_fonts_loaded()  # Paperlogy/Freesentation 등록 보장
        # 패널 전체를 종합탭 톤 카드 배경으로.
        self.setStyleSheet(
            f"PalettePanel {{ background: {_PAGE_BG}; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(7)

        # 헤더 라벨 — Paperlogy, 종합탭 헤드라인 톤.
        title = QLabel('부재 팔레트')
        title.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_HEAD_FG}; font-size: 17px; font-weight: 800;"
            " background: transparent; padding: 2px 0 4px 2px;"
        )
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {_CARD_BORDER}; background: {_CARD_BORDER};")
        sep.setFixedHeight(1)
        lay.addWidget(sep)

        # 부재 버튼 공통 스타일 — 둥근 카드형, hover 시 강조.
        _btn_css = (
            "QPushButton {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" text-align: left; padding: 9px 11px; font-size: 16px;"
            f" font-weight: 700; color: {_BODY_FG}; background: {_CARD_BG};"
            f" border: 1px solid {_CARD_BORDER}; border-radius: 8px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_SOFT};"
            f" border-color: {_ACCENT}; color: {_HEAD_FG}; }}"
            f"QPushButton:disabled {{ color: #AEB6C2; background: #F3F5F8;"
            f" border-color: #E6EAF0; }}"
        )
        for key, ctype, label, desc, enabled in PALETTE_ITEMS:
            btn = QPushButton(f'[{key}] {label}')
            btn.setStyleSheet(_btn_css)
            btn.setEnabled(enabled)
            btn.clicked.connect(lambda _checked=False, t=ctype: self._on_select(t))
            self._buttons.append(btn)
            lay.addWidget(btn)

            # 부재 치수 설명 — 값 정보이므로 Freesentation.
            d = QLabel(f'  {desc}')
            d.setStyleSheet(
                f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                f" color: {_SUB_FG}; font-size: 13px; background: transparent;"
            )
            d.setWordWrap(True)
            lay.addWidget(d)

        # (코어 슬래브는 코어벽 동작 완료 시 자동 재생성 — 수동 버튼 제거.)

        # ── 상세 설계 모드 (2026-05-30 단일 모드 통합) ──────
        # ON 이면 부재 팔레트(+코어)는 비활성, 실 지정/벽/개구부 버튼이 활성된다.
        sep_detail = QFrame()
        sep_detail.setFrameShape(QFrame.HLine)
        sep_detail.setStyleSheet(f"color: {_CARD_BORDER}; background: {_CARD_BORDER};")
        sep_detail.setFixedHeight(1)
        lay.addWidget(sep_detail)

        # 상세 설계 토글 — 종합탭 액센트(진파랑) 채움, checked 시 강조.
        self._detail_mode_btn = QPushButton('상세 설계')
        self._detail_mode_btn.setCheckable(True)
        self._detail_mode_btn.setStyleSheet(
            "QPushButton {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" padding: 9px 11px; font-size: 15px; font-weight: 700;"
            f" color: {_HEAD_FG}; background: {_CARD_BG};"
            f" border: 1px solid {_ACCENT}; border-radius: 8px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_SOFT}; }}"
            f"QPushButton:checked {{ background: {_ACCENT}; color: white;"
            f" border-color: {_ACCENT}; font-weight: 800; }}")
        if self._on_detail_mode_toggle is not None:
            self._detail_mode_btn.toggled.connect(self._on_detail_mode_toggled)
        else:
            self._detail_mode_btn.setEnabled(False)
        lay.addWidget(self._detail_mode_btn)

        # 실/벽/개구부 버튼 — 기능 구분용 색상은 유지하되 둥근 카드형으로 통일.
        def _action_btn_css(bg: str, bg_hov: str, fg: str, border: str) -> str:
            return (
                "QPushButton {"
                f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                f" padding: 9px 11px; font-size: 15px; font-weight: 700;"
                f" color: {fg}; background: {bg};"
                f" border: 1px solid {border}; border-radius: 8px; }}"
                f"QPushButton:hover {{ background: {bg_hov}; }}"
                f"QPushButton:disabled {{ color: #AEB6C2; background: #F3F5F8;"
                f" border-color: #E6EAF0; }}")

        # 실 지정 — 상세 설계 ON 일 때만 활성.
        self._room_btn = QPushButton('실 지정')
        self._room_btn.setStyleSheet(
            _action_btn_css('#E6F4EA', '#D4ECDC', '#1E6B3A', '#BFE0CB'))
        if self._on_room_draw is not None:
            self._room_btn.clicked.connect(lambda: self._on_room_draw())
        self._room_btn.setEnabled(False)
        lay.addWidget(self._room_btn)

        # 벽 (내벽 배치) — 상세 설계 ON 일 때만 활성.
        self._wall_btn = QPushButton('벽')
        self._wall_btn.setStyleSheet(
            _action_btn_css('#FCEBD8', '#F7DCC0', '#9A5A1E', '#EECCAC'))
        if self._on_wall_place is not None:
            self._wall_btn.clicked.connect(lambda: self._on_wall_place())
        self._wall_btn.setEnabled(False)
        lay.addWidget(self._wall_btn)

        # 개구부 — 벽 바로 아래. 상세 설계 ON 일 때만 활성.
        self._opening_add_btn = QPushButton('개구부')
        self._opening_add_btn.setStyleSheet(
            _action_btn_css('#FCF0E0', '#F7E6CC', '#9A6A1E', '#EED9BC'))
        if self._on_opening_add is not None:
            self._opening_add_btn.clicked.connect(lambda: self._on_opening_add())
        self._opening_add_btn.setEnabled(False)
        lay.addWidget(self._opening_add_btn)

        # 조작 안내 — 안내문이므로 Paperlogy.
        detail_hint = QLabel(
            '  상세 설계 ON → 실 지정/벽/개구부 사용\n'
            '  실 지정: 좌클릭=점, Enter=완료, 우클릭/Back=취소점 · 실 클릭=선택, Del=삭제\n'
            '  벽: 부모 클릭 → 길이 입력 → R 방향/V 앵커/클릭 확정\n'
            '  개구부: "개구부" 후 벽/슬래브 클릭 · 개구부 클릭=선택, Del=삭제')
        detail_hint.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_SUB_FG}; font-size: 11px; background: transparent;"
            " padding-top: 4px;")
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
