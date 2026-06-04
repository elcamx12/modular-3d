"""비교 탭 — 3 케이스 가로 비교 표 + 우측 차트 사이드바.

[2026-06-01 재설계]
- 행 = 지표(연면적·자재·공기 등), 열 = 케이스 A/B/C.
  카테고리(면적/부재 종류 수/물량/비용/공기) 가 같은 그룹의 첫 행에만
  큰 라벨로 표시되고 그 옆에 항목 라벨이 붙는다.
- 상단에 A/B/C 케이스 박스 — 빈 상태엔 "파일 가져오기" 버튼, 채워진 상태엔
  파일명 + 제거(✕) 버튼.
- 우측 사이드바에 가로 막대 차트 3 개: 공기 / 비용 / 평면효율.

[정책]
- .case.json 단일 파일 포맷. 평면도 PNG 는 본 화면에 표시 X (간략 비교용).
"""
from __future__ import annotations

import base64
import os
import re
from typing import Any, Dict, List, Optional


# [2026-06-05] 부재 타입 라벨에서 뒤쪽 인스턴스 번호(-방번호-순번)를 떼어
#   *기준 타입 글자* 만 남긴다. 예: '모듈A-1-2' → '모듈A',
#   '수직 3층 모듈A-2-3' → '수직 3층 모듈A', '벽패널A-1-1' → '벽패널A'.
#   [함정] 타입명 자체엔 '-' 가 없고(공백만 있음) 뒤 번호만 '-숫자' 형태이므로
#   끝에서 반복되는 '-숫자' 묶음만 제거한다.
_TYPE_SUFFIX_RE = re.compile(r"(?:-\d+)+$")


def _base_type_label(label: Optional[str]) -> Optional[str]:
    """인스턴스 번호 접미사를 제거한 기준 타입 라벨."""
    if not label:
        return None
    return _TYPE_SUFFIX_RE.sub("", str(label)).strip()


def _count_base_types(labels) -> int:
    """라벨들을 기준 타입 글자 기준으로 묶어 distinct 종류 수를 센다."""
    seen = set()
    for lb in labels:
        base = _base_type_label(lb)
        if base:
            seen.add(base)
    return len(seen)

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from modular_3d.evaluation.case_io import load_case
from modular_3d.ui.evaluation_panel import _AutoScalePixmapLabel
from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded


# [2026-06-01] 폰트 등록 — 임포트 시점에 1회. 본문=Freesentation, 헤드=Paperlogy.
ensure_fonts_loaded()


# ── 색/치수 토큰 ───────────────────────────────────────────
_PAGE_BG     = "#EDF2F7"
_CARD_BG     = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEAD_FG     = "#1F4E79"
_BODY_FG     = "#1F2A37"
_SUB_FG      = "#5B6573"
_ROW_BORDER  = "#E8EDF4"
_TOTAL_FG    = "#C00000"
_EMPTY_COLOR = "#a0a8b5"

_CASE_COLORS = ["#7BB3F0", "#3A78D6", "#BBD7F8"]  # A/B/C 막대 색

_SLOT_COUNT = 3


# ── 포매터 ─────────────────────────────────────────────────
def _won(n: Any) -> str:
    try:
        v = float(n)
        if v >= 1e8:
            return f"{v/1e8:.2f}억원"
        if v >= 1e4:
            return f"{int(round(v)):,}원"
        return f"{int(round(v)):,}원"
    except Exception:
        return "—"


def _fmt_int(n: Any, unit: str = "") -> str:
    try:
        return f"{int(round(float(n))):,}{(' ' + unit) if unit else ''}"
    except Exception:
        return "—"


def _fmt_float(n: Any, decimals: int = 1, unit: str = "") -> str:
    try:
        return f"{float(n):,.{decimals}f}{(' ' + unit) if unit else ''}"
    except Exception:
        return "—"


# ── 케이스 헤더 박스 (A/B/C 상단) ─────────────────────────
class _CaseBox(QFrame):
    """한 케이스 슬롯의 상단 박스 — 빈 상태(파일 가져오기 버튼) / 채워진 상태(파일명 + ✕)."""

    state_changed = pyqtSignal()

    def __init__(self, slot_label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._slot_label = slot_label
        self._case: Optional[Dict[str, Any]] = None
        self._file_label: str = ""
        # [2026-06-01] 채워진 상태에서 어떤 뷰를 보일지 — "plan" 또는 "section".
        self._active_view: str = "plan"

        self.setObjectName("caseBox")
        # [2026-06-01 v5] 카드 세로 360 → 520 — 비교탭 세로의 약 절반.
        # [2026-06-05] 520 → 480 — 비용표에 '간접비·이윤·부가세' 행(약 40px)을
        #   추가하면서 세로가 넘쳐 스크롤이 생겼다. 미리보기 박스를 한 행 높이만큼
        #   줄여 스크롤 없이 한 화면에 들어오게 한다.
        self.setStyleSheet("QFrame#caseBox { background: transparent;"
                            " border: none; }")
        self.setMinimumWidth(200)
        self.setFixedHeight(480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self._render()

    # ── 외부 API ─────────────────────────────────────────
    def load_file(self, path: str) -> None:
        try:
            data = load_case(path)
        except Exception as e:
            QMessageBox.critical(self, "파일 불러오기 실패",
                                  f"{path}\n\n{e}")
            return
        self._case = data
        self._file_label = os.path.basename(path)
        self._render()
        self.state_changed.emit()

    def clear_case(self) -> None:
        self._case = None
        self._file_label = ""
        self._render()
        self.state_changed.emit()

    def case_evaluation(self) -> Optional[Dict[str, Any]]:
        if not self._case:
            return None
        return (self._case.get("results") or {}).get("evaluation") or {}

    # ── 내부 렌더 ────────────────────────────────────────
    def _render(self) -> None:
        while self._root.count() > 0:
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None); w.deleteLater()
        self._root.setContentsMargins(0, 0, 0, 0)
        if self._case is None:
            self._render_empty()
        else:
            self._render_filled()

    def _render_empty(self) -> None:
        """빈 상태 — 컨테이너 전체에 단일 큰 박스, 안에 파일 가져오기 버튼."""
        big_box = QFrame()
        big_box.setObjectName("emptyBigBox")
        big_box.setStyleSheet(
            "QFrame#emptyBigBox {"
            f" background: {_CARD_BG}; border: 2px solid {_HEAD_FG};"
            " border-radius: 22px; }"
        )
        big_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(big_box)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(8)
        v.addStretch(1)
        btn = QPushButton("파일 가져오기")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(54)
        btn.setStyleSheet(
            "QPushButton {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" background: white; color: {_SUB_FG};"
            f" border: 1.5px solid #C5CFDB; border-radius: 10px;"
            " font-size: 17px; font-weight: 600; padding: 14px 26px; }"
            f"QPushButton:hover {{ background: {_HEAD_FG}; color: white;"
            f"  border-color: {_HEAD_FG}; }}"
        )
        btn.clicked.connect(self._on_open_file)
        v.addWidget(btn, alignment=Qt.AlignCenter)
        v.addStretch(1)
        self._root.addWidget(big_box)

    def _render_filled(self) -> None:
        """채워진 상태 — 단일 큰 박스. 우상단에 [2D 평면도] [거실 단면] 토글
        버튼 + ✕ 제거. 본문에 선택된 뷰의 이미지/placeholder.
        """
        big_box = QFrame()
        big_box.setObjectName("filledBigBox")
        big_box.setStyleSheet(
            "QFrame#filledBigBox {"
            f" background: {_CARD_BG}; border: 2px solid {_HEAD_FG};"
            " border-radius: 22px; }"
        )
        v = QVBoxLayout(big_box)
        v.setContentsMargins(16, 14, 16, 18)
        v.setSpacing(10)

        # ── 상단: 토글 버튼 두 개 + ✕ ────────────────────
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        btn_plan = self._mk_toggle_btn("2D 평면도", active=(self._active_view == "plan"))
        btn_plan.clicked.connect(lambda: self._set_view("plan"))
        btn_sec = self._mk_toggle_btn("거실 단면", active=(self._active_view == "section"))
        # [2026-06-02] 거실 단면 토글 → view 전환만. 카드 안에 단면 직접 그림.
        btn_sec.clicked.connect(lambda: self._set_view("section"))
        top.addWidget(btn_plan)
        top.addWidget(btn_sec)
        top.addStretch(1)
        btn_x = QPushButton("✕")
        btn_x.setFixedSize(28, 28)
        btn_x.setCursor(Qt.PointingHandCursor)
        btn_x.setStyleSheet(
            "QPushButton { border: none; background: transparent;"
            f" color: {_SUB_FG}; font-size: 15px; font-weight: 800;"
            " border-radius: 14px; }"
            "QPushButton:hover { color: white; background: #C00000; }"
        )
        btn_x.clicked.connect(self.clear_case)
        top.addWidget(btn_x)
        v.addLayout(top)

        # ── 본문: 선택된 뷰 이미지 ────────────────────────
        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet(
            f"background: white; border-radius: 12px; border: 1px solid #EEF2F7;"
        )
        if self._active_view == "plan":
            pix = _decode_layout_pixmap(self._case)
            if pix is not None and not pix.isNull():
                # 박스 안에 KeepAspectRatio 로 채움 — _AutoScaleImageLabel 대체.
                # 크기 변동을 위해 별도 위젯 사용.
                img = _AutoScalePixmapLabel()
                img.setRawPixmap(pix)
                img.setStyleSheet(
                    "background: white; border-radius: 12px;"
                    " border: 1px solid #EEF2F7;"
                )
            else:
                img.setText("저장된 평면도 없음")
                img.setStyleSheet(
                    f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                    f" color: {_EMPTY_COLOR}; font-size: 14px;"
                    " background: white; border-radius: 12px;"
                    " border: 1px solid #EEF2F7;"
                )
        else:
            # [2026-06-02] 카드 안 거실 단면 — case scene 복원 → render_section_pixmap
            section_pix = self._build_section_pixmap()
            if section_pix is not None and not section_pix.isNull():
                img = _AutoScalePixmapLabel()
                img.setRawPixmap(section_pix)
                img.setStyleSheet(
                    "background: white; border-radius: 12px;"
                    " border: 1px solid #EEF2F7;"
                )
            else:
                img.setText("거실 단면\n(거실 미지정 또는 scene 정보 없음)")
                img.setStyleSheet(
                    f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                    f" color: {_EMPTY_COLOR}; font-size: 14px;"
                    " background: white; border-radius: 12px;"
                    " border: 1px dashed #C9D2DD;"
                )
        v.addWidget(img, stretch=1)
        self._root.addWidget(big_box)

    def _mk_toggle_btn(self, text: str, active: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(30)
        if active:
            btn.setStyleSheet(
                "QPushButton {"
                f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                f" background: {_HEAD_FG}; color: white; border: none;"
                " border-radius: 14px; font-size: 13px; font-weight: 700;"
                " padding: 5px 14px; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton {"
                f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                f" background: white; color: {_HEAD_FG};"
                f" border: 1.5px solid {_HEAD_FG}; border-radius: 14px;"
                " font-size: 13px; font-weight: 700; padding: 5px 14px; }"
                f"QPushButton:hover {{ background: #EEF2F7; }}"
            )
        return btn

    def _set_view(self, v: str) -> None:
        if v not in ("plan", "section"):
            return
        if v == self._active_view:
            return
        self._active_view = v
        self._render()

    def _build_section_pixmap(self):
        """[2026-06-02] case 의 scene_state 를 Scene 으로 복원해 거실 단면을
        QPixmap 으로 반환. 카드 안 img QLabel 에 직접 임베드용. 실패 시 None.
        """
        try:
            scene_state = (self._case or {}).get('scene') or {}
            if not scene_state.get('components'):
                return None
            from modular_3d.io.scene_io import state_dict_to_scene
            from modular_3d.ui.section_viewer import render_section_pixmap
            scene, _n = state_dict_to_scene(scene_state)
            # 단면 비율에 맞춘 픽스맵 — _AutoScalePixmapLabel 가 카드에 fit 시
            # 비율 유지하며 가득 채움. max_size 2000 으로 충분한 해상도.
            return render_section_pixmap(scene, max_size=2000)
        except Exception as e:
            import traceback
            print("[compare_panel section] 오류:\n" + traceback.format_exc(),
                  flush=True)
            return None


    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "비교할 .case.json 선택", "", "케이스 파일 (*.case.json)"
        )
        if path:
            self.load_file(path)


# ── 비교 표 한 행 ──────────────────────────────────────────
class _RowBuilder:
    """카테고리 / 항목 / A,B,C 값을 한 행씩 표에 추가."""
    def __init__(self, grid: QGridLayout, start_row: int = 1) -> None:
        self.grid = grid
        self.row = start_row

    def add_section_header(self, name: str, item_count: int) -> None:
        lbl = QLabel(name)
        lbl.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 22px; font-weight: 800;"
            " background: transparent; padding: 10px 18px 10px 0;"
        )
        lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.grid.addWidget(lbl, self.row, 0, item_count, 1)

    def add_item_row(self, item_label: str, a_val: str, b_val: str, c_val: str,
                     total: bool = False) -> None:
        item = QLabel(item_label)
        item.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 17px; background: transparent;"
            f" padding: 11px 16px 11px 0; border-bottom: 1px dashed {_ROW_BORDER};"
        )
        item.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.grid.addWidget(item, self.row, 1)
        for col, val in enumerate((a_val, b_val, c_val), start=2):
            v = QLabel(val)
            color = _TOTAL_FG if total else _BODY_FG
            family = F_HEAD if total else F_BODY
            weight = "800" if total else "500"
            v.setStyleSheet(
                f"font-family: '{family}', 'Malgun Gothic', sans-serif;"
                f" color: {color}; font-size: 17px; font-weight: {weight};"
                " background: transparent; padding: 11px 16px;"
                f" border-bottom: 1px dashed {_ROW_BORDER};"
            )
            v.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            self.grid.addWidget(v, self.row, col)
        self.row += 1


# ── 우측 사이드바 가로 막대 차트 ──────────────────────────
class _SideBarChart(QFrame):
    """카테고리 헤더 + 3 행 (A/B/C 라벨 / 값 / 가로 막대 / 비율)."""

    def __init__(self, title: str, formatter, parent: Optional[QWidget] = None,
                 absolute_pct: bool = False) -> None:
        """
        absolute_pct=True 면 % 라벨이 *값 자체* (예: 평면효율 81.8%) 로 표시되고
        막대 길이도 100 기준으로 계산. False (기본) 면 최댓값 대비 비율.
        """
        super().__init__(parent)
        self._title = title
        self._fmt = formatter
        self._absolute_pct = absolute_pct
        self._values: List[Optional[float]] = [None, None, None]
        self.setObjectName("sidebarChart")
        self.setStyleSheet(
            f"QFrame#sidebarChart {{ background: {_CARD_BG};"
            f" border: 1.5px solid #C9D2DD; border-radius: 14px; }}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(230)
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(26, 20, 26, 20)
        self._v.setSpacing(12)
        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 22px; font-weight: 800;"
            " background: transparent;"
        )
        self._v.addWidget(self._title_lbl)
        # 본문 — 3 행 (A/B/C). 막대는 paintEvent 로 그림.
        self._bars_host = QWidget()
        self._bars_host.setMinimumHeight(110)
        self._bars_host.installEventFilter(self)
        self._v.addWidget(self._bars_host, stretch=1)

    def set_values(self, values: List[Optional[float]]) -> None:
        v = list(values) + [None] * 3
        self._values = v[:3]
        self._bars_host.update()

    # ── 막대 그리기 — bars_host paintEvent 가로채기 ────
    def eventFilter(self, obj, ev):
        from PyQt5.QtCore import QEvent
        if obj is self._bars_host and ev.type() == QEvent.Paint:
            self._paint_bars()
            return True
        return False

    def _paint_bars(self) -> None:
        host = self._bars_host
        p = QPainter()
        if not p.begin(host):
            return
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            W = host.width(); H = host.height()
            if W <= 6 or H <= 6:
                return
            # 정규화 — absolute_pct=True 면 100 고정(0~100% 절대), 아니면 최댓값.
            nums = [v for v in self._values if isinstance(v, (int, float)) and v > 0]
            if self._absolute_pct:
                v_max = 100.0
            else:
                v_max = max(nums) if nums else 1.0
                if v_max <= 0:
                    v_max = 1.0

            row_h = H / 3.0
            # [폰트 키움] 막대 라벨/값/% 폰트 +2pt.
            f_lbl = QFont(F_HEAD); f_lbl.setBold(True); f_lbl.setPointSize(13)
            f_val = QFont(F_BODY); f_val.setPointSize(11)
            f_pct = QFont(F_HEAD); f_pct.setBold(True); f_pct.setPointSize(14)

            # [2026-06-01 v3] 폭 더 키움:
            # - pct_w 78 → 96 → "100%" 잘림 없음
            # - label_w 36 → 44 (A/B/C 라벨)
            # - bar_pad 14 → 22 → 라벨/트랙 시각적 분리
            label_w = 44.0
            pct_w   = 96.0
            bar_pad = 22.0
            bar_x0  = label_w + bar_pad
            bar_x1  = W - pct_w - bar_pad
            bar_w   = max(40.0, bar_x1 - bar_x0)

            best_idx = max(range(3),
                           key=lambda k: (self._values[k] or 0))

            for i, (case, val) in enumerate(zip(("A", "B", "C"), self._values)):
                y_center = row_h * (i + 0.5)
                if isinstance(val, (int, float)) and val > 0:
                    try:
                        val_text = self._fmt(val)
                    except Exception:
                        val_text = f"{val}"
                else:
                    val_text = ""
                # A/B/C 좌측 라벨 — 막대 쪽으로 살짝 우측 이동 (그래프/막대 위치 유지)
                label_shift = 16.0
                p.setFont(f_lbl)
                p.setPen(QPen(QColor(_BODY_FG)))
                p.drawText(
                    QRectF(label_shift, float(y_center - row_h/2), label_w, row_h),
                    int(Qt.AlignLeft | Qt.AlignVCenter), case,
                )
                # 막대 트랙 + 채움
                track_h = 10.0
                track_y = y_center - track_h/2 + 10
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor("#EEF2F7")))
                p.drawRoundedRect(QRectF(bar_x0, track_y, bar_w, track_h), 5.0, 5.0)
                if isinstance(val, (int, float)) and val > 0:
                    ratio = min(1.0, float(val) / float(v_max))
                    fill_w = bar_w * ratio
                    p.setBrush(QBrush(QColor(_CASE_COLORS[i])))
                    p.drawRoundedRect(QRectF(bar_x0, track_y, fill_w, track_h), 5.0, 5.0)
                # 절대값 라벨 (막대 위)
                if val_text:
                    p.setFont(f_val)
                    p.setPen(QPen(QColor(_SUB_FG)))
                    p.drawText(
                        QRectF(bar_x0, track_y - 22, bar_w, 18),
                        int(Qt.AlignLeft | Qt.AlignVCenter), val_text,
                    )
                # 우측 % 표시 — absolute_pct 면 값 자체, 아니면 최댓값 대비.
                if isinstance(val, (int, float)) and val > 0:
                    if self._absolute_pct:
                        pct = float(val)        # 값 자체가 이미 0~100 % 단위
                    else:
                        pct = (val / v_max) * 100.0
                    pct_text = f"{pct:.0f}%"
                    p.setFont(f_pct)
                    color = _CASE_COLORS[i] if i == best_idx else _SUB_FG
                    p.setPen(QPen(QColor(color)))
                    p.drawText(
                        QRectF(bar_x1 + 4, track_y - 8, pct_w - 4, 24),
                        int(Qt.AlignRight | Qt.AlignVCenter), pct_text,
                    )
        finally:
            p.end()


# ── 비교 탭 메인 ──────────────────────────────────────────
class ComparePanel(QWidget):
    """비교 탭 루트."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 상단 툴바
        tb = QWidget()
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(20, 12, 20, 8)
        tb_lay.setSpacing(8)
        title = QLabel("비교")
        title.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" font-weight: 800; font-size: 20px; color: {_BODY_FG};"
            " background: transparent;"
        )
        tb_lay.addWidget(title)
        tb_lay.addStretch(1)
        self._btn_clear_all = QPushButton("전체 비우기")
        self._btn_clear_all.setCursor(Qt.PointingHandCursor)
        self._btn_clear_all.setStyleSheet(
            "QPushButton { padding: 6px 12px; border: 1px solid #C00000;"
            " border-radius: 6px; background: #fff; color: #C00000;"
            " font-weight: 700; font-size: 12px; }"
            "QPushButton:hover { background: #C00000; color: #fff; }"
        )
        self._btn_clear_all.clicked.connect(self._on_clear_all)
        tb_lay.addWidget(self._btn_clear_all)
        root.addWidget(tb)

        # 본문 가로 분할 — 좌(비교표) / 우(차트 사이드바)
        body = QWidget()
        body.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(20, 4, 20, 20)
        body_lay.setSpacing(16)
        root.addWidget(body, stretch=1)

        # [비율 재측정] 메인 1160 : 사이드바 410 ≈ 2.83 : 1.
        # stretch 정수로는 17 : 6 ≈ 2.83. 단순화해 11 : 4 (2.75) 사용.
        body_lay.addWidget(self._build_main_table(), stretch=11)
        body_lay.addWidget(self._build_sidebar(), stretch=4)

        # 초기 차트 갱신
        self._refresh_charts()

    # ── 좌측 비교표 ──────────────────────────────────────
    def _build_main_table(self) -> QWidget:
        """이미지 비율 재측정 결과 반영:
        - 라벨 영역(카테고리+항목) : A/B/C 컬럼 영역 ≈ 3 : 7
        - A/B/C 박스 가로 200 × 세로 260 (≈ 1:1.3) → minimumHeight 260
        - 박스 사이 간격 80px (박스 폭의 ~40%)
        - 박스 모서리 라운드 24, 보더 2px (다크 블루)
        """
        host = QFrame()
        host.setObjectName("tableHost")
        host.setStyleSheet(
            f"QFrame#tableHost {{ background: {_CARD_BG};"
            f" border: 1px solid {_CARD_BORDER}; border-radius: 10px; }}"
        )
        outer = QVBoxLayout(host)
        outer.setContentsMargins(24, 20, 24, 24)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        grid = QGridLayout(inner)
        grid.setContentsMargins(0, 0, 0, 0)
        # [2026-06-01 v4] 박스가 expanding 으로 메인 폭에 잘 분배되도록 spacing 축소.
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(0)

        labels = ("A", "B", "C")
        self._slots: List[_CaseBox] = []
        # A/B/C 라벨 (박스 위) — Paperlogy 굵은 큰 글자
        for col, lbl_text in enumerate(labels, start=2):
            head_lbl = QLabel(lbl_text)
            head_lbl.setAlignment(Qt.AlignCenter)
            head_lbl.setStyleSheet(
                f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
                f" color: {_BODY_FG}; font-size: 28px; font-weight: 800;"
                " background: transparent; padding: 6px 0 14px 0;"
            )
            grid.addWidget(head_lbl, 0, col)
        # 박스 — 슬롯 컨테이너가 expanding. 셋이 컬럼 폭에 균등 분배되어
        # 항상 화면에 들어옴.
        for col, slot_label in enumerate(labels, start=2):
            slot = _CaseBox(slot_label)
            slot.state_changed.connect(self._refresh_charts)
            self._slots.append(slot)
            grid.addWidget(slot, 1, col)

        # 좌측 라벨 영역 폭
        grid.addItem(_spacer(100), 1, 0)
        grid.addItem(_spacer(200), 1, 1)

        # 헤더 행과 데이터 행 사이 간격
        spacer_row = QLabel("")
        spacer_row.setFixedHeight(22)
        spacer_row.setStyleSheet("background: transparent;")
        grid.addWidget(spacer_row, 2, 0, 1, 5)

        # 데이터 행
        self._rb = _RowBuilder(grid, start_row=3)
        self._build_data_rows()

        # [2026-06-01 v4] 라벨 컬럼은 spacer 최소폭으로만 결정(stretch=0). 케이스
        # 컬럼들이 메인 폭 전체를 균등 분배 → 박스 셋이 한 화면에 시원하게.
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(4, 1)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return host

    def _build_data_rows(self) -> None:
        """현재 슬롯들의 evaluation 데이터로 비교 행 전체 채움. 슬롯 변경 시 재호출."""
        # 그리드 비우기 (data row 만) — 매번 재구성하지 않고 라벨만 갱신하는 게 더
        # 효율적이나, 단순화를 위해 placeholder 행 1 회만 그린다(초기). 슬롯
        # 변경시엔 _refresh_data_rows() 가 값만 갱신.
        # 행 생성은 한 번만, 이후엔 setText.
        if getattr(self, '_value_labels', None) is not None:
            return  # 이미 빌드됨
        self._value_labels: Dict[str, List[QLabel]] = {}
        # 카테고리: (이름, [항목 키 리스트])
        # 항목 키는 _value_labels 의 키.
        sections = [
            ("면적", [
                ("연면적", "area_total"),
                ("1층 footprint", "area_footprint"),
                ("유효면적/전체면적", "eff_ratio"),
            ]),
            ("부재 종류 수", [
                ("모듈 타입", "n_module_types"),
                ("패널 타입", "n_panel_types"),
            ]),
            ("물량", [
                ("강재 총중량", "steel_ton"),
                ("슬래브 부피", "slab_m3"),
            ]),
            ("비용", [
                ("자재", "cost_material"),
                ("운송", "cost_transport"),
                ("노무", "cost_labor"),
                ("경비", "cost_equip"),
                # [2026-06-05] 종합탭과 동일하게 간접비·이윤·부가세 행을 둔다.
                #   이게 빠지면 자재+운송+노무+경비(직접비)와 합계(할증 후)가
                #   안 맞아 표가 모순돼 보인다(누적 약 1.475배 차이).
                ("간접비·이윤·부가세", "cost_indirect"),
                ("합계", "cost_total"),
            ]),
            ("공기", [
                ("총 공기", "schedule_days"),
            ]),
        ]
        for sec_name, items in sections:
            self._rb.add_section_header(sec_name, len(items))
            for item_label, key in items:
                is_total = (key == "cost_total")
                # 빈 값으로 초기 행 추가.
                start_row = self._rb.row
                self._rb.add_item_row(item_label, "—", "—", "—", total=is_total)
                # 추가된 3 개 값 라벨(QGridLayout 의 row=start_row, col=2..4) 추적.
                value_lbls = []
                for col in (2, 3, 4):
                    item = self._rb.grid.itemAtPosition(start_row, col)
                    if item is not None and item.widget() is not None:
                        value_lbls.append(item.widget())
                self._value_labels[key] = value_lbls

    def _refresh_data_rows(self) -> None:
        """슬롯 상태 변화 시 표 값만 갱신."""
        if not hasattr(self, '_value_labels'):
            return
        evs: List[Optional[Dict[str, Any]]] = [s.case_evaluation() for s in self._slots]

        def vals(extractor) -> List[str]:
            out = []
            for ev in evs:
                if not ev:
                    out.append("—")
                else:
                    try:
                        v = extractor(ev)
                        out.append(v if v is not None else "—")
                    except Exception:
                        out.append("—")
            return out

        # 각 항목 추출.
        def headline(k):
            return lambda ev: ev.get("headline", {}).get(k)
        def members_modtypes(ev):
            mods = ev.get("members", {}).get("modules_by_type", []) or []
            return _count_base_types(m.get("name") for m in mods)
        def members_paneltypes(ev):
            pnls = ev.get("members", {}).get("panels_by_type", []) or []
            return _count_base_types(p.get("class_label") for p in pnls)
        def steel_t(ev):
            return ((ev.get("materials") or {}).get("steel_total") or {}).get("total_weight_ton")
        def slab_v(ev):
            return ((ev.get("materials") or {}).get("slab") or {}).get("total_volume_m3")
        def cost(k):
            return lambda ev: (ev.get("cost") or {}).get(k)
        def cost_indirect(ev):
            # [2026-06-05] 간접비·이윤·부가세 = 총공사비 − 직접공사비.
            #   종합탭(evaluation_panel)과 같은 정의 — 직접비 항목들과 합계 사이를
            #   메워 표가 맞아떨어지게 한다.
            c = ev.get("cost") or {}
            t = c.get("total_krw")
            d = c.get("direct_krw")
            if t is None or d is None:
                return None
            return float(t) - float(d)
        def sched_days(ev):
            s = ev.get("schedule") or {}
            return s.get("total_days") if s.get("available") else None
        def eff_ratio_str(ev):
            eff = (ev.get("headline") or {}).get("effective_area_floor1") or {}
            if not eff.get("gross_m2"):
                return None
            return f"{eff.get('ratio_pct', 0):.1f}%"

        updates = [
            ("area_total",      vals(lambda ev: _fmt_float(headline('total_floor_area_m2')(ev), 1, '㎡'))),
            ("area_footprint",  vals(lambda ev: _fmt_float(headline('footprint_m2')(ev), 1, '㎡'))),
            ("eff_ratio",       vals(eff_ratio_str)),
            ("n_module_types",  vals(lambda ev: _fmt_int(members_modtypes(ev), '종'))),
            ("n_panel_types",   vals(lambda ev: _fmt_int(members_paneltypes(ev), '종'))),
            ("steel_ton",       vals(lambda ev: _fmt_float(steel_t(ev), 3, 't'))),
            ("slab_m3",         vals(lambda ev: _fmt_float(slab_v(ev), 2, '㎥'))),
            ("cost_material",   vals(lambda ev: _won(cost('material_krw')(ev)))),
            ("cost_transport",  vals(lambda ev: _won(cost('transport_krw')(ev)))),
            ("cost_labor",      vals(lambda ev: _won(cost('labor_krw')(ev)))),
            ("cost_equip",      vals(lambda ev: _won(cost('equip_krw')(ev)))),
            ("cost_indirect",   vals(lambda ev: _won(cost_indirect(ev)))),
            ("cost_total",      vals(lambda ev: _won(cost('total_krw')(ev)))),
            ("schedule_days",   vals(lambda ev: _fmt_int(sched_days(ev), '일'))),
        ]
        for key, value_strs in updates:
            lbls = self._value_labels.get(key, [])
            for lbl, s in zip(lbls, value_strs):
                lbl.setText(s)

    # ── 우측 사이드바 차트 ───────────────────────────────
    def _build_sidebar(self) -> QWidget:
        """이미지 측정: 카드 3개가 세로를 균등 분배. addStretch 없음."""
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        self._chart_days = _SideBarChart(
            "공기", lambda v: f"{int(round(v)):,}일")
        self._chart_cost = _SideBarChart(
            "비용", lambda v: f"{int(round(v/1e6)):,}M원" if v >= 1e6 else f"{int(round(v)):,}원")
        # [2026-06-01] 평면효율은 값 자체가 0~100% 단위 — absolute_pct=True 로
        # 막대 길이도 100 기준, 우측 % 표시도 케이스의 실제 평면효율 값.
        self._chart_eff  = _SideBarChart(
            "유효면적/전체면적", lambda v: f"{v:.1f}%", absolute_pct=True)
        # 균등 stretch — 세로 빈 공간 없이 카드 3 개가 사이드바 채움.
        v.addWidget(self._chart_days, stretch=1)
        v.addWidget(self._chart_cost, stretch=1)
        v.addWidget(self._chart_eff,  stretch=1)
        return host

    # ── 슬롯 → 차트 + 표 갱신 ───────────────────────────
    def _refresh_charts(self) -> None:
        # 데이터 행 빌드 (1 회) + 값 갱신.
        if not hasattr(self, '_value_labels'):
            # 표가 아직 빌드 안 됐을 수 있음 — main_table 빌드 시점에 호출됨.
            pass
        self._refresh_data_rows()

        days: List[Optional[float]] = []
        costs: List[Optional[float]] = []
        effs: List[Optional[float]] = []
        for s in self._slots:
            ev = s.case_evaluation()
            if not ev:
                days.append(None); costs.append(None); effs.append(None); continue
            d = (ev.get("schedule") or {})
            total_days = d.get("total_days") if d.get("available") else None
            total_cost = (ev.get("cost") or {}).get("total_krw")
            eff = ((ev.get("headline") or {}).get("effective_area_floor1") or {}).get("ratio_pct")
            days.append(float(total_days) if isinstance(total_days, (int, float)) and total_days > 0 else None)
            costs.append(float(total_cost) if isinstance(total_cost, (int, float)) and total_cost > 0 else None)
            effs.append(float(eff) if isinstance(eff, (int, float)) and eff > 0 else None)
        self._chart_days.set_values(days)
        self._chart_cost.set_values(costs)
        self._chart_eff.set_values(effs)

    def _on_clear_all(self) -> None:
        for s in self._slots:
            s.clear_case()


# ── 헬퍼 ───────────────────────────────────────────────────
def _spacer(min_w: int):
    from PyQt5.QtWidgets import QSpacerItem
    return QSpacerItem(min_w, 1, QSizePolicy.Minimum, QSizePolicy.Minimum)


def _decode_layout_pixmap(case: Optional[Dict[str, Any]]) -> Optional[QPixmap]:
    """case JSON 의 images.layout_png_b64 → QPixmap (없으면 None)."""
    if not case:
        return None
    try:
        b64 = (case.get("images") or {}).get("layout_png_b64")
        if not b64:
            return None
        raw = base64.b64decode(b64)
        pix = QPixmap()
        pix.loadFromData(raw, "PNG")
        if pix.isNull():
            return None
        return pix
    except Exception:
        return None
