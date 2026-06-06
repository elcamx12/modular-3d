"""평가 탭 패널 — 한 케이스의 종합 보고서.

[설계 근거]
- 평가탭_구축_계획서.md §1·§2 (Phase J·M·P).
- 단일 스크롤 페이지: A. 건물 개요 / B. 부재 구성 / C. 물량·자재비 / D. 운송 /
  E. 공기 / F. 종합 비용. 우측 상단에 케이스 저장 버튼 1개.

[Phase P 수정]
- 1종/2종/3종 정책 토글 *제거* — 물량 탭의 current_policy 한 곳에서만 결정.
  평가 탭은 그 값을 그대로 표시.
- 공기 영역의 축소 막대그래프 → *공정표 표* 로 변경 (정보 가치 향상).
- 자재비·강재 본수·운송 평균 적재·운송비 본구현 (실제 attr 매핑 후).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt, QRect, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded

# 종합탭 import 시점에 한국어 폰트 등록 (안전망 — 메인이 먼저 호출했어도 idempotent).
ensure_fonts_loaded()


# ── 자동 리스케일 QLabel ─────────────────────────────────
# 평면도가 깨지던 원인:
#   기존 set_layout_pixmap 이 self._layout_card.width()-24 를 기준으로 1 회만
#   스케일링 → 첫 호출엔 카드 폭이 미정(0), 창 리사이즈 시 재스케일 안 됨,
#   target_h=360 고정으로 라벨이 그보다 작으면 비율이 일그러져 보임.
# 해결: 원본 픽스맵을 들고 resizeEvent 마다 라벨 크기에 맞게 KeepAspectRatio
# 로 다시 그림. 한 번만 setRawPixmap 호출하면 이후 자동.
class _AutoScalePixmapLabel(QLabel):
    """원본 픽스맵을 보관, 라벨 크기에 맞춰 KeepAspectRatio 로 매번 리스케일.

    [2026-05-31] 잘림 방지 — Expanding 사이즈폴리시 + setScaledContents(False).
    Qt 가 라벨을 컨테이너 가득 키우고, paint 직전 우리가 직접 비율 유지 스케일.
    """
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._raw_pix: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setScaledContents(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setRawPixmap(self, pix: Optional[QPixmap]) -> None:
        if pix is None or pix.isNull():
            self._raw_pix = None
            super().setPixmap(QPixmap())
            return
        self._raw_pix = pix
        self._rescale()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._rescale()

    def _rescale(self) -> None:
        if self._raw_pix is None:
            return
        sz = self.size()
        if sz.width() < 10 or sz.height() < 10:
            return
        # KeepAspectRatio 는 (w,h) 안에 *완전히* 들어가게 축소 → 절대 잘림 없음.
        scaled = self._raw_pix.scaled(
            sz.width(), sz.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        super().setPixmap(scaled)


# ── 종합탭 전용 미니 간트차트 위젯 ───────────────────────────
# 공정표 탭의 큰 간트차트를 그대로 가져오는 대신, 종합탭에는
# *공종 막대 한 줄 = 한 공종* 의 단순 가로 막대형으로 요약 표시.
class _GanttMini(QWidget):
    """공기 데이터(dict list: name/start/end/total/color)를 받아 단순 간트로 렌더.

    [2026-05-31 v2] 좁은 컬럼 대응:
      - Qt.TextSingleLine 플래그 — 글자가 세로로 쪼개지지 않게 강제.
      - 폰트 크기 자동 — 컨테이너 폭·행 높이에 따라 6~10pt 사이 스케일.
      - LEFT_PAD 도 컨테이너 폭의 25% 로 자동.
    """

    _RIGHT_PAD = 6      # 우측 여백만 — 일수 라벨은 제거됨
    _TOP_PAD = 18       # 상단 눈금
    _BOT_PAD = 4
    _MIN_ROW_H = 9

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tasks: List[Dict[str, Any]] = []
        self._total_days: int = 0
        self._start_date: Optional[str] = None  # ISO yyyy-mm-dd
        self.setMinimumHeight(140)
        self.setStyleSheet(
            "background: white; border: 1px solid #DDE4ED; border-radius: 6px;"
        )

    def set_tasks(self, tasks: List[Dict[str, Any]], total_days: int,
                  start_date: Optional[str] = None) -> None:
        self._tasks = list(tasks or [])
        self._total_days = max(1, int(total_days or 0))
        self._start_date = start_date
        self.update()

    @staticmethod
    def _month_ticks(start_date: Optional[str], total_days: int):
        """[(day_offset, year, month), ...] — 착공월 1일부터 total_days 안에 들어오는
        매월 1일의 day offset 목록.
        """
        import datetime
        if not start_date or total_days <= 0:
            return []
        try:
            sd = datetime.date.fromisoformat(str(start_date)[:10])
        except Exception:
            return []
        cur = datetime.date(sd.year, sd.month, 1)
        ticks = []
        # 안전 가드 — 최대 60개월
        for _ in range(60):
            delta = (cur - sd).days
            if delta > total_days:
                break
            if delta >= 0:
                ticks.append((delta, cur.year, cur.month))
            # 다음 달 1일
            if cur.month == 12:
                cur = datetime.date(cur.year + 1, 1, 1)
            else:
                cur = datetime.date(cur.year, cur.month + 1, 1)
        return ticks

    def paintEvent(self, _e):  # noqa: N802
        # [2026-05-31 v3] 글자 깨짐 방지 — QFontMetrics.elidedText 로 사전에
        # "…" 줄임 처리해서 단일 라인 보장. TextSingleLine 플래그가 환경에 따라
        # 안 듣는 경우가 있어 elidedText 가 더 안전.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width(); h = self.height()
        # LEFT_PAD — 컨테이너 폭의 32% (좁은 컬럼에서 라벨 영역 충분히 확보)
        left_pad = max(70, min(150, int(w * 0.32)))
        chart_x0 = left_pad
        chart_x1 = w - self._RIGHT_PAD
        chart_w = max(1, chart_x1 - chart_x0)
        n = max(1, len(self._tasks))
        avail_h = max(self._MIN_ROW_H, h - self._TOP_PAD - self._BOT_PAD)
        row_h = max(self._MIN_ROW_H, avail_h // n)

        # 폰트 — 행 높이의 0.55배 정도 픽셀로 설정 (point 가 아닌 pixel 로 명시).
        px = max(7, min(12, int(row_h * 0.62)))
        font_lbl = QFont(self.font()); font_lbl.setPixelSize(px)
        font_tick = QFont(self.font()); font_tick.setPixelSize(max(8, px - 1))
        fm_lbl = QFontMetrics(font_lbl)
        fm_tick = QFontMetrics(font_tick)

        # 상단 날짜 눈금 — [2026-05-31] D+N 대신 *월 라벨* (1월, 2월…).
        # 착공일(start_date) 기준 매월 1일 위치에 눈금 + "M월" 라벨.
        # 라벨이 겹치지 않을 만큼만 표시.
        p.setPen(QPen(QColor("#888"), 1))
        p.setFont(font_tick)
        month_ticks = self._month_ticks(self._start_date, self._total_days)
        if month_ticks:
            # 한 라벨 폭 약 26px — 그 이상 간격에서만 라벨 그림.
            min_label_gap_px = 26
            last_drawn_x = -1000
            for (day_off, yr, mo) in month_ticks:
                x = chart_x0 + int(chart_w * day_off / self._total_days)
                p.setPen(QPen(QColor("#aaa"), 1))
                p.drawLine(x, self._TOP_PAD - 2, x, h - self._BOT_PAD)
                if x - last_drawn_x >= min_label_gap_px:
                    p.setPen(QPen(QColor("#444"), 1))
                    label = f"{mo}월"
                    p.drawText(x - 10, self._TOP_PAD - 4, label)
                    last_drawn_x = x
        else:
            # fallback — 착공일 없으면 D+N 3 등분.
            steps = 3
            for i in range(steps + 1):
                x = chart_x0 + int(chart_w * i / steps)
                day = int(self._total_days * i / steps)
                p.drawLine(x, self._TOP_PAD - 2, x, h - self._BOT_PAD)
                label = fm_tick.elidedText(f"D+{day}", Qt.ElideRight, 32)
                p.drawText(x - 16, self._TOP_PAD - 4, label)

        # 공종별 막대
        # [2026-06-01] 사용자 요청 — 윗 공종 아래 바와 아래 공종 윗 바를 같은
        # 레벨로. bar_thick = row_h (가득 채움), bar_pad = 0 → 막대 사이 간격 없음.
        y = self._TOP_PAD
        bar_thick = row_h
        bar_pad = 0

        label_w = left_pad - 6

        for t in self._tasks:
            name = str(t.get("name", ""))
            start = int(t.get("start", 0))
            total = max(1, int(t.get("total", 0)))
            # [2026-06-01] 공정표 탭의 CP 판정 결과 type('critical/normal/orange')
            # 를 색으로 매핑. type 이 없으면 옛 'color' 필드 fallback.
            type_key = str(t.get("type") or t.get("color") or "normal")
            if type_key == "normal":
                type_key = "blue"
            color = self._color_for(type_key)

            # 공종명 — elidedText 로 한 줄 강제, 폭 넘으면 … 처리.
            p.setPen(QPen(QColor("#222"), 1))
            p.setFont(font_lbl)
            disp_name = fm_lbl.elidedText(name, Qt.ElideRight, label_w)
            text_y = y + (row_h + fm_lbl.ascent() - fm_lbl.descent()) // 2
            p.drawText(3, text_y, disp_name)

            # 막대
            bx = chart_x0 + int(chart_w * start / self._total_days)
            bw = max(2, int(chart_w * total / self._total_days))
            p.fillRect(bx, y + bar_pad, bw, bar_thick, color)
            p.setPen(QPen(color.darker(130), 1))
            p.drawRect(bx, y + bar_pad, bw, bar_thick)
            # [2026-05-31] 우측 일수 라벨 제거 — 사용자 요청.

            y += row_h

    @staticmethod
    def _color_for(key: str) -> QColor:
        table = {
            "critical": QColor("#C00000"),
            "orange":   QColor("#E08A00"),
            "blue":     QColor("#1F4E79"),
            "green":    QColor("#2E8B57"),
            "purple":   QColor("#7A4FB5"),
            "gray":     QColor("#7A8390"),
        }
        return table.get(key, QColor("#1F4E79"))


# ── 스타일 (비교탭과 통일된 토큰) ──────────────────────────
_CARD_BG = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEADLINE_BG = "#F5F8FF"
_HEADLINE_BORDER = "#1F4E79"
_SECTION_TITLE_COLOR = "#1F4E79"   # 다크 블루 = 비교탭과 동일
_PAGE_BG = "#EDF2F7"
_EMPTY_COLOR = "#a0a8b5"
_BODY_FG = "#1F2A37"
_SUB_FG  = "#5B6573"


def _won(n: float) -> str:
    return f"{int(round(n)):,}원"


def _section_title(text: str) -> QLabel:
    """카드 위 섹션 제목 — Paperlogy Bold."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
        f" font-size: 19px; font-weight: 800; color: {_SECTION_TITLE_COLOR};"
        " padding: 0 0 5px 2px; background: transparent;"
    )
    lbl.setMaximumHeight(32)
    return lbl


def _card(min_height: int = 60) -> QFrame:
    """공통 카드 — 흰 배경 + 옅은 회색 1.5px 보더 + 26px 큰 라운드."""
    f = QFrame()
    f.setObjectName("evalCard")
    f.setStyleSheet(
        f"QFrame#evalCard {{ background: {_CARD_BG};"
        f" border: 1.5px solid #C9D2DD; border-radius: 26px; }}"
    )
    f.setMinimumHeight(min_height)
    return f


def _inner_card(min_height: int = 0) -> QFrame:
    """카드 내부 결과 박스 — 직사각형 X, 모서리 라운드 14px."""
    f = QFrame()
    f.setObjectName("innerCard")
    f.setStyleSheet(
        f"QFrame#innerCard {{ background: #FAFBFD;"
        f" border: 1px solid {_CARD_BORDER}; border-radius: 14px; }}"
    )
    if min_height > 0:
        f.setMinimumHeight(min_height)
    return f


def _scroll_card(min_height: int = 60) -> tuple:
    """카드 + 내부 QScrollArea + 컨테이너 layout. Returns: (card_frame, inner_vboxlayout).

    [2026-06-01] AlignTop 제거 — 콘텐츠 안 addStretch 가 정상 동작해 항목 분배
    가능 (예: 부재구성 카드에서 모듈 표 ↔ 패널 표 사이 vertical stretch).
    """
    card = _card(min_height)
    outer = QVBoxLayout(card)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    scroll = QScrollArea(card)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }"
        "QScrollBar:vertical { width: 8px; background: transparent; }"
        "QScrollBar::handle:vertical { background: #c5cfdb; border-radius: 4px; }"
    )
    outer.addWidget(scroll)
    inner_widget = QWidget()
    inner_widget.setStyleSheet("background: transparent;")
    inner_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    inner_lay = QVBoxLayout(inner_widget)
    inner_lay.setContentsMargins(12, 10, 12, 10)
    inner_lay.setSpacing(6)
    scroll.setWidget(inner_widget)
    return card, inner_lay


def _empty_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet(
        f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
        f" color: {_EMPTY_COLOR}; font-size: 15px; padding: 20px;"
        " background: transparent;"
    )
    lbl.setWordWrap(True)
    return lbl


class EvaluationPanel(QWidget):
    save_case_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 상단 툴바 — 제목 제거, 우측 저장 버튼만.
        toolbar = QWidget()
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(12, 6, 12, 4)
        tb_lay.setSpacing(8)
        tb_lay.addStretch(1)
        self._save_btn = QPushButton("종합 결과 저장")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(
            "QPushButton {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            " padding: 9px 20px; border: 1px solid #1F4E79;"
            " border-radius: 6px; background: #fff; color: #1F4E79;"
            " font-weight: 700; font-size: 15px; }"
            "QPushButton:hover { background: #1F4E79; color: #fff; }"
        )
        self._save_btn.clicked.connect(self.save_case_requested.emit)
        tb_lay.addWidget(self._save_btn)
        root.addWidget(toolbar)

        # [2026-05-31] 한 화면에 모두 들어가야 함 — 외부 QScrollArea 제거.
        # 본문을 직접 root 에 부착. 각 카드에 max-height 를 두어 총 높이 ~ 700px.
        body = QWidget()
        body.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(22, 14, 22, 22)
        body_lay.setSpacing(22)
        root.addWidget(body, stretch=1)

        # ────────────────────────────────────────────────────────
        # [2026-06-01 사용자 와이어프레임 반영 — 3 컬럼 레이아웃]
        #   좌 컬럼: 건물 개요(상) + 부재 구성(하, 큼)
        #   중 컬럼: 2D 평면도(상, 큼) + 강재·콘크리트 물량+자재비(하)
        #   우 컬럼: 공정바(상, 큼·우상단 총공기) + 운송(중) + 종합비용(하)
        # ────────────────────────────────────────────────────────
        body_lay.setSpacing(16)
        body_row = QHBoxLayout()
        body_row.setSpacing(22)
        body_lay.addLayout(body_row, stretch=1)

        # 본문 body_lay 에 body_row 하나만 — 본문 전체가 3 컬럼.

        # ── 좌측 컬럼 ─────────────────────────────────────
        left_col = QVBoxLayout()
        left_col.setSpacing(0)
        body_row.addLayout(left_col, stretch=28)

        # 1) 건물 개요 — 좌 2열 그리드
        self._head_strip = _card(min_height=200)
        head_grid = QHBoxLayout(self._head_strip)
        head_grid.setContentsMargins(20, 18, 20, 18)
        head_grid.setSpacing(16)
        nums_col = QVBoxLayout()
        nums_col.setSpacing(10)
        self._head_cells: List[Dict[str, QLabel]] = []
        for label in ("총 층수", "모듈 개수", "패널 개수", "연면적"):
            cell, val_lbl = self._mk_head_cell(label, "—")
            nums_col.addWidget(cell)
            self._head_cells.append({"label": label, "value": val_lbl})
        case_col = QVBoxLayout()
        case_col.setSpacing(10)
        self._case_region_lbl = self._mk_case_line("지역")
        self._case_start_lbl  = self._mk_case_line("착공")
        self._case_eval_lbl   = self._mk_case_line("작성 일자")
        case_col.addWidget(self._case_region_lbl["host"])
        case_col.addWidget(self._case_start_lbl["host"])
        case_col.addWidget(self._case_eval_lbl["host"])
        head_grid.addLayout(nums_col, stretch=1)
        head_grid.addLayout(case_col, stretch=1)
        left_col.addWidget(self._build_titled_section("건물 개요", self._head_strip),
                           stretch=2)

        # 2) 부재 구성
        self._b_card, self._b_inner = _scroll_card(min_height=420)
        left_col.addWidget(self._build_titled_section("부재 구성", self._b_card),
                           stretch=5)

        # ── 중앙 컬럼: 2D 평면도(상) + 공정바(하) ──────────
        mid_col = QVBoxLayout()
        mid_col.setSpacing(0)
        body_row.addLayout(mid_col, stretch=38)

        # 1) 2D 평면도 — 중앙 상단, 크게 (stretch=5)
        self._layout_card = _card(min_height=0)
        layout_inner = QVBoxLayout(self._layout_card)
        layout_inner.setContentsMargins(8, 8, 8, 8)
        self._layout_img = _AutoScalePixmapLabel()
        self._layout_img.setStyleSheet("background: #fff;")
        self._layout_img.setText("평면설계 탭에서 모듈을 배치하세요.")
        layout_inner.addWidget(self._layout_img)
        mid_col.addWidget(self._build_titled_section("2D 평면도", self._layout_card),
                          stretch=5)

        # 2) 공정바 — 2D 평면도 아래 (작게), 우상단 "총 공기" 배지 (stretch=2)
        self._e_card = _card(min_height=0)
        self._e_inner = QVBoxLayout(self._e_card)
        self._e_inner.setContentsMargins(8, 8, 8, 8)
        self._e_inner.setSpacing(4)
        self._sched_total_lbl = QLabel("총 공기: —")
        self._sched_total_lbl.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" font-size: 15px; font-weight: 800; color: {_HEADLINE_BORDER};"
            " padding: 5px 14px; background: #EEF4FF; border-radius: 12px;"
        )
        mid_col.addWidget(self._build_titled_section(
            "공정바", self._e_card, right_widget=self._sched_total_lbl),
            stretch=2)

        # ── 우측 컬럼: 강재 자재비(상, 큼) + 운송 + 종합비용 ─
        right_col = QVBoxLayout()
        right_col.setSpacing(0)
        body_row.addLayout(right_col, stretch=34)

        # 1) 강재·콘크리트 물량 + 자재비 — 우측 상단, 큰 카드 (stretch=5)
        self._c_card, self._c_inner = _scroll_card(min_height=0)
        self._c_policy_lbl = QLabel("정책: —")
        self._c_policy_lbl.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" font-size: 13px; color: {_SUB_FG}; padding: 5px 12px;"
            " background: #EEF4FF; border-radius: 12px;"
        )
        right_col.addWidget(self._build_titled_section(
            "강재·콘크리트 물량 + 자재비", self._c_card,
            right_widget=self._c_policy_lbl), stretch=5)

        # 2) 운송 — stretch=1
        self._d_card, self._d_inner = _scroll_card(min_height=0)
        right_col.addWidget(self._build_titled_section("운송", self._d_card),
                            stretch=1)

        # 3) 종합비용
        self._cost_strip = QFrame()
        self._cost_strip.setObjectName("costStrip")
        self._cost_strip.setStyleSheet(
            f"QFrame#costStrip {{ background: {_CARD_BG};"
            f" border: 1.5px solid #C9D2DD; border-radius: 26px; }}"
        )
        self._cost_lay = QHBoxLayout(self._cost_strip)
        self._cost_lay.setContentsMargins(20, 18, 20, 18)
        self._cost_lay.setSpacing(18)
        self._cost_cells: Dict[str, QLabel] = {}
        for label, key, emphasize in (
            ("자재비", "material", False),
            ("운송비", "transport", False),
            ("노무비", "labor", False),
            ("경비",   "equip",     False),
            ("간접비·이윤·부가세", "indirect", False),
            ("공사비 (총합)", "total", True),
        ):
            cell, val_lbl = self._mk_cost_cell(label, "—", emphasize)
            self._cost_lay.addWidget(cell)
            self._cost_cells[key] = val_lbl
        self._cost_strip.setMinimumHeight(0)
        right_col.addWidget(self._build_titled_section("종합비용", self._cost_strip),
                            stretch=1)

        self._last_data: Optional[Dict[str, Any]] = None

    # ── 라벨 + 카드 묶음 widget ───────────────────────────
    def _build_titled_section(self, title: str, card: QWidget,
                              right_widget: Optional[QWidget] = None) -> QWidget:
        """카드 위에 섹션 제목 + 옵션 우측 배지. 컬럼에 stretch 로 추가하면
        라벨+카드+하단 spacing 이 같은 비율로 분배 → 옆 컬럼 카드와 상·하단 정렬.

        [정렬 핵심]
        - 묶음 자체에 하단 spacing 20 흡수 (`contentsMargins bottom=20`).
        - 컬럼은 `setSpacing(0)` — 묶음 사이 spacing 은 묶음 자체 마진으로.
        - 라벨 영역 fixedHeight 40 — 배지 유무와 무관하게 동일.
        """
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        # [정렬 보장] Ignored: 자식 sizeHint/minHeight 무시 → 묶음 height 가
        # 오직 stretch 로 결정. 컬럼별 묶음 minHeight 합 차이로 인한 어긋남 차단.
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        v = QVBoxLayout(host)
        # 모든 묶음 하단에 20px 마진 — 컬럼 setSpacing(0) 과 짝지어 정렬 보장.
        v.setContentsMargins(0, 0, 0, 20)
        v.setSpacing(8)
        title_host = QWidget()
        title_host.setStyleSheet("background: transparent;")
        title_host.setFixedHeight(40)
        title_row = QHBoxLayout(title_host)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(_section_title(title))
        if right_widget is not None:
            title_row.addStretch(1)
            title_row.addWidget(right_widget)
        v.addWidget(title_host)
        v.addWidget(card, stretch=1)
        return host

    # ── 케이스 라인 한 줄(라벨 + 값) ─────────────────────
    def _mk_case_line(self, label: str) -> Dict[str, Any]:
        """건물 개요 케이스 항목 — 라벨 Paperlogy Bold, 값 Freesentation Regular."""
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        l1 = QLabel(label)
        l1.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 16px; font-weight: 800;"
            " background: transparent;"
        )
        l2 = QLabel("—")
        l2.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 14px; font-weight: 400;"
            " background: transparent;"
        )
        v.addWidget(l1)
        v.addWidget(l2)
        return {"host": host, "value": l2}

    # ── 외부 진입점 ──────────────────────────────────────
    def apply_data(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        self._last_data = data
        self._render_case_header(data.get("case", {}))
        self._render_headline(data.get("headline", {}))
        self._render_members(data.get("members", {}))
        self._render_materials(data.get("materials", {}))
        self._render_transport(data.get("transport", {}))
        self._render_schedule(data.get("schedule", {}))
        self._render_cost(data.get("cost", {}))

    # ── 배치 평면도 이미지 주입 (외부) ───────────────────
    # main_3d 가 종합탭 진입 시 self._f5_panel.canvas.grab() 으로 캡처해 호출.
    # [2026-05-31] _AutoScalePixmapLabel 사용 — raw pixmap 만 넘기면 라벨이
    # 자기 크기 변화에 맞춰 KeepAspectRatio 로 자동 리스케일.
    def set_layout_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        if pixmap is None or pixmap.isNull():
            self._layout_img.setText("평면설계 탭에서 모듈을 배치하세요.")
            self._layout_img.setRawPixmap(None)
            return
        self._layout_img.setRawPixmap(pixmap)

    # ── 케이스 식별 (지역·착공·작성) ───────────────────
    # [2026-05-31] "평가" → "작성" 으로 명명 변경. 셀 3개 세로 배치.
    def _render_case_header(self, c: Dict[str, Any]) -> None:
        region = "—"
        start = "—"
        eval_d = "—"
        if c:
            region = str(c.get("region") or c.get("region_city") or "—")
            start = str(c.get("start_date") or "—")
            eval_d = str(c.get("eval_date") or "—")
        # [2026-06-01] 새 레이아웃 — 라벨/값 dict 구조.
        self._case_region_lbl["value"].setText(region)
        self._case_start_lbl["value"].setText(start)
        self._case_eval_lbl["value"].setText(eval_d)

    # ── A. 헤드라인 ──────────────────────────────────────
    def _render_headline(self, h: Dict[str, Any]) -> None:
        # [2026-06-01 새 와이어프레임] 4 개 셀: 총 층수 / 모듈 개수 / 패널 개수 /
        # 연면적. (지하층·footprint 은 종합비교에 안 들어가서 제외.)
        # 패널 개수는 members.panels 합계.
        panels = (self._last_data or {}).get("members", {}).get("panels", {}) or {}
        n_panels = sum(int(v or 0) for v in panels.values())
        vals = [
            (h.get("floors_above_ground", 0), "층"),
            (h.get("modules_total", 0), "개"),
            (n_panels, "개"),
            (h.get("total_floor_area_m2", 0.0), "㎡"),
        ]
        for cell, (val, unit) in zip(self._head_cells, vals):
            if isinstance(val, float):
                cell["value"].setText(f"{val:,.1f} {unit}")
            else:
                cell["value"].setText(f"{val:,} {unit}")

    # ── B. 부재 구성 ─────────────────────────────────────
    def _render_members(self, m: Dict[str, Any]) -> None:
        # [2026-05-31] 모듈·패널 각각 *크기별 타입 표* 로 보여줌.
        # 접합부재(캔틸레버 등)는 정보 가치가 낮아 한 줄 텍스트로 유지.
        self._clear_layout(self._b_inner)

        # ── 모듈 타입 표 ──────────────────────────────────
        # [2026-05-31] '부재' 컬럼 추가 — 깊이와 개수 사이.
        mods = m.get("modules_by_type", []) or []
        mod_title = QLabel("모듈")
        mod_title.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            " color: #1F4E79; font-size: 15px; font-weight: 800;"
            " padding: 4px 0 4px 2px; background: transparent;"
        )
        mod_title.setMaximumHeight(26)
        self._b_inner.addWidget(mod_title)
        if mods:
            # [2026-06-01] 부재 컬럼을 기둥/천장보/바닥보 3 개로 분리 표시.
            tbl = self._table(
                ["타입", "폭 m", "길이 m", "면적 ㎡",
                 "기둥", "천장보", "바닥보", "개수"],
                [[mt["name"],
                  f"{mt['width_m']:.1f}",
                  f"{mt['length_m']:.1f}",
                  f"{mt['area_m2']:.2f}",
                  str(mt.get("columns_section", mt.get("sections", "—"))),
                  str(mt.get("top_beams_section", "—")),
                  str(mt.get("bottom_beams_section", "—")),
                  f"{mt['count']}"] for mt in mods],
                stretch_col=4,
            )
            # [2026-06-01] max-height 풀어서 카드 안 빈 공간 흡수.
            tbl.setMaximumHeight(16777215)
            self._b_inner.addWidget(tbl, stretch=1)
        else:
            self._b_inner.addWidget(_empty_label("배치된 모듈이 없습니다."))

        # [2026-06-01 v3] 모듈/패널 표 각각 stretch=1 로 카드 안 빈 공간을 양쪽이
        # 흡수 — 모듈 절반/패널 절반. 별도 stretch widget 불필요.

        # ── 패널 타입 표 ──────────────────────────────────
        pnl_title = QLabel("패널")
        pnl_title.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            " color: #1F4E79; font-size: 15px; font-weight: 800;"
            " padding: 8px 0 4px 2px; background: transparent;"
        )
        pnl_title.setMaximumHeight(28)
        self._b_inner.addWidget(pnl_title)
        ptypes = m.get("panels_by_type", []) or []
        if ptypes:
            ptbl = self._table(
                ["종류", "폭 m", "깊이 m", "부재", "개수"],
                [[p["class_label"], f"{p['width_m']:.1f}", f"{p['depth_m']:.1f}",
                  str(p.get("sections", "—")),
                  f"{p['count']}"] for p in ptypes],
                stretch_col=3,
            )
            ptbl.setMaximumHeight(16777215)
            self._b_inner.addWidget(ptbl, stretch=1)
        else:
            panels = m.get("panels", {}) or {}
            if any(panels.values()):
                text = QLabel(
                    f"바닥패널 {panels.get('floor_panel', 0)} · "
                    f"벽패널 {panels.get('struct_wall', 0)} · "
                    f"내벽 {panels.get('interior_wall', 0)} · "
                    f"코어슬래브 {panels.get('core_slab', 0)}"
                )
                text.setStyleSheet("color: #555; font-size: 11px;")
                self._b_inner.addWidget(text)
            else:
                self._b_inner.addWidget(_empty_label("패널 없음"))

        # ── 접합부재 한 줄 ────────────────────────────────
        attached = m.get("attached", {}) or {}
        if any(attached.values()):
            small = QLabel(
                f"접합부재  —  캔틸레버보 {attached.get('cantilever_beam', 0)} · "
                f"캔틸레버슬래브 {attached.get('cantilever_slab', 0)} · "
                f"중간보 {attached.get('mid_beam', 0)} · "
                f"중간기둥 {attached.get('mid_column', 0)}"
            )
            small.setStyleSheet("color: #555; font-size: 11px; padding-top: 6px;")
            small.setWordWrap(True)
            self._b_inner.addWidget(small)

    # ── C. 물량·자재비 ───────────────────────────────────
    def _render_materials(self, mat: Dict[str, Any]) -> None:
        """[2026-06-01] 순서 재배치:
        ① 채택 단면 — 카드 맨 위 한 줄
        ② 강재 본수표 — 가운데 큰 영역, stretch=1 로 카드 안 빈 공간 차지
        ③ 슬래브 / 자재비 — 카드 맨 아래 두 줄
        """
        self._clear_layout(self._c_inner)
        if not mat or not mat.get("available"):
            self._c_policy_lbl.setText("정책: —")
            self._c_inner.addWidget(_empty_label(
                "물량 탭에서 단면 산정·자재비를 먼저 실행하세요."
            ))
            return
        policy = mat.get("current_policy", "—")
        self._c_policy_lbl.setText(f"정책: {policy}")

        # ① 채택 단면 — 맨 위
        groups = mat.get("groups") or []
        if groups:
            gtxt = " · ".join(f"{g['group']}:{g['section']}" for g in groups)
            gl = QLabel(f"채택 단면 — {gtxt}")
            gl.setStyleSheet(
                f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
                f" color: {_HEADLINE_BORDER}; font-size: 13px; font-weight: 700;"
                " padding: 2px 0 4px 0; background: transparent;"
            )
            gl.setWordWrap(True)
            self._c_inner.addWidget(gl)

        # ② 강재 본수표 — 가운데, stretch=1 로 빈 공간 흡수
        rows = mat.get("steel_rows") or []
        if rows:
            tbl_rows = [
                [r["section"], f"{r['length_mm']:,.0f}", f"{r['count']:,}",
                 f"{r['total_length_m']:,.2f}", f"{r['total_weight_ton']:.3f}"]
                for r in rows
            ]
            total_row = mat.get("steel_total")
            if total_row:
                tbl_rows.append([
                    "합계", f"{total_row['length_mm']:,.0f}",
                    f"{total_row['count']:,}",
                    f"{total_row['total_length_m']:,.2f}",
                    f"{total_row['total_weight_ton']:.3f}",
                ])
            tbl = self._table(
                ["단면", "길이(mm)", "본수", "총길이(m)", "총중량(t)"],
                tbl_rows,
                stretch_col=0,
            )
            # 표 max-height 풀어서 카드 안 빈 공간 흡수.
            tbl.setMaximumHeight(16777215)
            self._c_inner.addWidget(tbl, stretch=1)
        else:
            self._c_inner.addWidget(_empty_label("강재 본수 데이터가 비어 있습니다."),
                                     stretch=1)

        # ③ 슬래브 / 자재비 — 맨 아래
        slab = mat.get("slab") or {}
        cost = mat.get("cost") or {}
        slab_lbl = QLabel(
            f"슬래브 — 면적 {slab.get('total_area_m2', 0):,.1f} ㎡ · "
            f"부피 {slab.get('total_volume_m3', 0):,.2f} ㎥ · "
            f"두께 {slab.get('thickness_mm', 0):,.0f} mm"
        )
        slab_lbl.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {_SUB_FG}; font-size: 12px; padding-top: 6px;"
            " background: transparent;"
        )
        self._c_inner.addWidget(slab_lbl)

        if cost:
            cost_lbl = QLabel(
                f"자재비 — 강재 {_won(cost.get('steel_cost', 0))} · "
                f"데크 {_won(cost.get('deck_cost', 0))} · "
                f"콘크리트 {_won(cost.get('concrete_cost', 0))}  ⎮  "
                f"<b>합계 {_won(cost.get('total_cost', 0))}</b>"
            )
            cost_lbl.setStyleSheet(
                f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
                f" color: {_HEADLINE_BORDER}; font-size: 13px; font-weight: 700;"
                " padding-top: 4px; background: transparent;"
            )
            cost_lbl.setTextFormat(Qt.RichText)
            self._c_inner.addWidget(cost_lbl)
            if cost.get("has_missing_price"):
                miss = QLabel("⚠️ 일부 단가가 누락되어 자재비가 부정확할 수 있습니다.")
                miss.setStyleSheet(
                    f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                    " color: #C00000; font-size: 11px;"
                )
                self._c_inner.addWidget(miss)

    # ── 운송 ─────────────────────────────────────────────
    def _render_transport(self, t: Dict[str, Any]) -> None:
        """[2026-06-01] 4 KPI 셀을 가로 한 줄로 균등 배치 (총 회차/평균 적재/운송 거리/총 운송비).
        각 셀은 라벨 위·값 아래. 셀 간 간격 동일."""
        self._clear_layout(self._d_inner)
        if not t or not t.get("available"):
            self._d_inner.addWidget(_empty_label(
                "운송 탭에서 [계산 실행] 을 먼저 누르세요."
            ))
            return
        avg_t = t.get("avg_load_kg", 0.0) / 1000.0
        pairs = [
            ("총 회차", f"{t.get('trips_total', 0):,} 회"),
            ("평균 적재", f"{avg_t:.1f} t ({t.get('avg_utilization_pct', 0):.1f}%)"),
            ("운송 거리", f"{t.get('distance_km_total', 0.0):,.0f} km"),
            ("총 운송비", _won(t.get("total_cost_krw", 0.0))),
        ]
        row = QHBoxLayout()
        row.setSpacing(18)
        row.setContentsMargins(4, 6, 4, 6)
        for label, val in pairs:
            cell, _ = self._mk_head_cell(label, val, value_size=18)
            row.addWidget(cell, stretch=1)
        wrap = QWidget(); wrap.setLayout(row)
        # [2026-06-01] 카드 중앙 정렬 — 위·아래 양쪽에 stretch.
        self._d_inner.addStretch(1)
        self._d_inner.addWidget(wrap)
        self._d_inner.addStretch(1)

    # ── 공정바 (간트, 좁은 우측 컬럼에 한눈에 들어옴) ────
    # [2026-05-31 v2] 간트가 위, 총 일수 요약이 아래.
    def _render_schedule(self, s: Dict[str, Any]) -> None:
        self._clear_layout(self._e_inner)
        # [2026-06-01] 총공기 요약은 카드 바깥 우상단 배지로 옮김.
        if not s or not s.get("available"):
            self._sched_total_lbl.setText("총 공기: —")
            self._e_inner.addWidget(_empty_label(
                "공정표 탭에 한 번 진입해 자동 계산을 마치세요."
            ))
            return
        total_days = int(s.get("total_days", 0))
        self._sched_total_lbl.setText(f"총 공기: {total_days:,}일")
        tasks = list(s.get("tasks", []) or [])
        if tasks:
            # [2026-06-01] 공정표 탭 순서 그대로 유지 — sorted 제거.
            start_date = None
            if self._last_data:
                start_date = (self._last_data.get("case") or {}).get("start_date")
            gantt = _GanttMini()
            gantt.set_tasks(tasks, total_days, start_date=start_date)
            self._e_inner.addWidget(gantt, stretch=1)

    # ── F. 종합 비용 ─────────────────────────────────────
    def _render_cost(self, c: Dict[str, Any]) -> None:
        if not c:
            for lbl in self._cost_cells.values():
                lbl.setText("—")
            return
        self._cost_cells["material"].setText(_won(c.get("material_krw", 0)))
        self._cost_cells["transport"].setText(_won(c.get("transport_krw", 0)))
        self._cost_cells["labor"].setText(_won(c.get("labor_krw", 0)))
        self._cost_cells["equip"].setText(_won(c.get("equip_krw", 0)))
        # 간접비·이윤·부가세 = 총공사비 − 직접공사비 (요율 적층분 합).
        _indirect = float(c.get("total_krw", 0) or 0) - float(c.get("direct_krw", 0) or 0)
        self._cost_cells["indirect"].setText(_won(_indirect))
        self._cost_cells["total"].setText(_won(c.get("total_krw", 0)))

    # ── 보조 UI 빌더 ─────────────────────────────────────
    def _table(self, headers: List[str], rows: List[List[str]],
               stretch_col: Optional[int] = None) -> QTableWidget:
        # [2026-05-31] stretch_col — 지정된 컬럼만 stretch, 나머지 ResizeToContents.
        # [2026-06-01] 부재 컬럼처럼 긴 텍스트가 들어오는 컬럼은 wordwrap +
        # 행 높이 자동 조정 → "SHS 200×2..." 식 잘림 방지. 표가 카드 폭을 넘는
        # 경우엔 가로 스크롤바 활성화.
        from PyQt5.QtWidgets import QHeaderView, QAbstractScrollArea
        tbl = QTableWidget(len(rows), len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.verticalHeader().setVisible(False)
        tbl.setWordWrap(True)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        tbl.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        header = tbl.horizontalHeader()
        header.setStretchLastSection(False)
        for i in range(len(headers)):
            if i == stretch_col:
                header.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        # stretch 가 지정된 컬럼(보통 '부재') 에 최소 폭 보장 — 짧게 줄이는 것 방지.
        if stretch_col is not None and 0 <= stretch_col < len(headers):
            tbl.setColumnWidth(stretch_col, 180)
        tbl.setShowGrid(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.NoSelection)
        tbl.setStyleSheet(
            "QTableWidget {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            " font-size: 14px; font-weight: 400;"
            f" gridline-color: #E8EDF4; border: 1px solid {_CARD_BORDER};"
            " border-radius: 12px; background: white; }"
            "QTableWidget::item { padding: 4px 6px; }"
            "QHeaderView::section {"
            f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" background: #F1F5FA; color: {_BODY_FG};"
            " padding: 8px 6px; border: none;"
            f" border-bottom: 1px solid {_CARD_BORDER};"
            " font-size: 14px; font-weight: 800; }"
        )
        for r, row in enumerate(rows):
            is_total = (len(row) > 0 and row[0] == "합계")
            for c, val in enumerate(row):
                item = QTableWidgetItem(str(val))
                if c != 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if is_total:
                    f = item.font(); f.setBold(True); item.setFont(f)
                tbl.setItem(r, c, item)
        # wordwrap 로 늘어난 행 높이 적용.
        try:
            tbl.resizeRowsToContents()
        except Exception:
            pass
        h = tbl.horizontalHeader().height() + sum(
            tbl.rowHeight(r) for r in range(tbl.rowCount())
        ) + 2
        tbl.setMinimumHeight(min(h, 260))
        tbl.setMaximumHeight(300)
        return tbl

    def _mk_head_cell(self, label: str, value_text: str,
                       value_size: int = 14) -> tuple:
        """건물 개요/운송 셀 — 라벨 굵게 위, 값 일반 두께 아래.

        [2026-06-01] spacing 14 + 위·아래 addStretch — 셀 안 콘텐츠가 셀의
        세로 가운데 정렬. 종합비용 셀과 시각 동일.
        """
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        v.addStretch(1)
        l1 = QLabel(label)
        l1.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 16px; font-weight: 800;"
            " background: transparent;"
        )
        l2 = QLabel(value_text)
        l2.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: {value_size}px;"
            " font-weight: 400; background: transparent;"
        )
        v.addWidget(l1)
        v.addWidget(l2)
        v.addStretch(1)
        return w, l2

    def _mk_cost_cell(self, label: str, value_text: str, emphasize: bool) -> tuple:
        """종합비용 셀 — 운송 셀과 동일한 폰트·간격·정렬."""
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        v.addStretch(1)
        l1 = QLabel(label)
        l1.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 16px; font-weight: 800;"
            " background: transparent;"
        )
        color = "#C00000" if emphasize else _BODY_FG
        weight  = "700" if emphasize else "400"
        l2 = QLabel(value_text)
        l2.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {color}; font-size: 18px;"
            f" font-weight: {weight}; background: transparent;"
        )
        v.addWidget(l1)
        v.addWidget(l2)
        v.addStretch(1)
        return w, l2

    def _clear_layout(self, lay) -> None:
        while lay.count() > 0:
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
