"""비교 탭 패널 — 3 슬롯 고정. 각 슬롯이 빈 상태(중앙 파일 불러오기 버튼) /
채워진 상태(평면도 + 단면도 + 데이터 표) 두 모드를 토글.

[설계 근거]
- 종합탭 정비 + 비교탭 신설 계획서 (memoized-herding-sunset.md).
- 사용자 스케치(2026-05-31): A·B·C 3 슬롯 가로 배치. 각 슬롯 위쪽에 평면도+단면도,
  아래쪽에 면적·컴포넌트 종류수·모듈/패널 구성·접합부수·물량·비용·공기.
- [2026-05-31 재정비] "공정 탭과 비슷한 느낌" 으로 — 흰 카드 + 깔끔한 2열 표.
  기존 progress-bar 처럼 보이던 회색 막대는 `QFrame { ... }` 글로벌 셀렉터가
  자식 QFrame 에 줄줄이 적용된 부작용 → setObjectName 으로 스코프 제한.

[정책]
- 단일 .case.json 으로 저장/로드. 평면도는 case["images"]["layout_png_b64"]
  에서 디코드해 표시.
- 단면도는 추후 — 일단 자리만 잡고 "단면 (추후)" 안내.
- 빈 데이터(패널 0개, 접합부 0개)는 "—" 행 한 줄로 유지.
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from modular_3d.evaluation.case_io import load_case
from modular_3d.ui.evaluation_panel import _AutoScalePixmapLabel


# ── 색/치수 토큰 ───────────────────────────────────────────
_PAGE_BG = "#EDF2F7"
_CARD_BG = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEADLINE_BG = "#1F4E79"
_SECTION_BG = "#F1F5FA"
_SECTION_FG = "#1F4E79"
_ROW_BG_ALT = "#FAFBFD"
_LABEL_FG = "#5B6573"
_VALUE_FG = "#1F2A37"
_TOTAL_FG = "#C00000"
_EMPTY_COLOR = "#a0a8b5"

_SLOT_COUNT = 3
_SLOT_MIN_WIDTH = 300


# ── 포매터 ─────────────────────────────────────────────────
def _won(n: Any) -> str:
    try:
        return f"{int(round(float(n))):,}원"
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


# ── 한 섹션(헤더 + 2열 표) 빌더 ─────────────────────────────
class _SectionTable(QFrame):
    """섹션 제목 막대 + 2열(라벨/값) 그리드.

    공정탭의 INPUT/경제성 카드와 비슷한 느낌. 카드 내부 자체에 border 를 갖지
    않고 부모 카드의 흰 배경 위에 그대로 얹힘. 줄무늬 배경으로 행 구분.
    """

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionTable")
        self.setStyleSheet(
            "QFrame#sectionTable { background: transparent; border: none; }"
        )

        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(0)

        head = QLabel(title)
        head.setObjectName("sectionHead")
        head.setStyleSheet(
            "QLabel#sectionHead {"
            f" background: {_SECTION_BG}; color: {_SECTION_FG};"
            " font-size: 11px; font-weight: 800;"
            " padding: 5px 8px; border-top: 1px solid #DDE4ED;"
            " border-bottom: 1px solid #DDE4ED; }"
        )
        self._v.addWidget(head)

        self._grid_host = QWidget()
        self._grid_host.setObjectName("sectionGrid")
        self._grid_host.setStyleSheet(
            "QWidget#sectionGrid { background: white; }"
        )
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(0)
        self._grid.setVerticalSpacing(0)
        self._grid.setColumnStretch(0, 0)
        self._grid.setColumnStretch(1, 1)
        self._v.addWidget(self._grid_host)

        self._row_idx = 0

    def add_row(self, label: str, value: str, *, total: bool = False) -> None:
        bg = _CARD_BG if (self._row_idx % 2 == 0) else _ROW_BG_ALT
        # 라벨 셀
        l1 = QLabel(label)
        l1.setObjectName("rowLabel")
        l1.setStyleSheet(
            "QLabel#rowLabel {"
            f" background: {bg}; color: {_LABEL_FG};"
            " font-size: 11px; padding: 5px 8px;"
            " border-bottom: 1px solid #EEF2F7; }"
        )
        l1.setMinimumWidth(96)
        # 값 셀
        weight = "800" if total else "600"
        color = _TOTAL_FG if total else _VALUE_FG
        l2 = QLabel(value)
        l2.setObjectName("rowValue")
        l2.setStyleSheet(
            "QLabel#rowValue {"
            f" background: {bg}; color: {color};"
            f" font-size: 11px; font-weight: {weight}; padding: 5px 8px;"
            " border-bottom: 1px solid #EEF2F7; }"
        )
        l2.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._grid.addWidget(l1, self._row_idx, 0)
        self._grid.addWidget(l2, self._row_idx, 1)
        self._row_idx += 1

    def add_empty_row(self) -> None:
        self.add_row("—", "0개")


class _CaseSlot(QFrame):
    """비교 탭의 한 슬롯. 빈 상태 ↔ 채워진 상태 토글."""

    # [2026-05-31] 슬롯 상태 변화(파일 로드/비우기) 시 발화 — 하단 차트가 받음.
    state_changed = pyqtSignal()

    def __init__(self, slot_label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._slot_label = slot_label
        self._case: Optional[Dict[str, Any]] = None
        self._file_label: str = ""

        self.setObjectName("caseSlot")
        self.setMinimumWidth(_SLOT_MIN_WIDTH)
        # [중요] 셀렉터를 #caseSlot 로 고정 — 자식 QFrame 에 스타일이 줄줄이
        # 상속돼서 progress-bar 처럼 보이던 버그 차단.
        self.setStyleSheet(
            "QFrame#caseSlot {"
            f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
            " border-radius: 8px; }"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ── 상단 헤더 ─────────────────────────────────────
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        head = QWidget()
        head.setObjectName("slotHead")
        head.setStyleSheet(
            "QWidget#slotHead {"
            f" background: {_HEADLINE_BG};"
            " border-top-left-radius: 8px; border-top-right-radius: 8px; }"
        )
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(12, 6, 6, 6)
        head_lay.setSpacing(6)
        self._slot_lbl = QLabel(slot_label)
        self._slot_lbl.setStyleSheet(
            "color: white; font-size: 14px; font-weight: 800; background: transparent;"
        )
        head_lay.addWidget(self._slot_lbl)
        self._file_lbl = QLabel("")
        self._file_lbl.setStyleSheet(
            "color: #cfe0f7; font-size: 11px; background: transparent;"
        )
        head_lay.addWidget(self._file_lbl, stretch=1)
        self._btn_clear = QPushButton("✕")
        self._btn_clear.setFixedSize(22, 22)
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        self._btn_clear.setStyleSheet(
            "QPushButton { border: none; background: transparent;"
            " color: #cfe0f7; font-size: 13px; font-weight: 700; }"
            "QPushButton:hover { color: white; background: #C00000;"
            " border-radius: 11px; }"
        )
        self._btn_clear.clicked.connect(self.clear_case)
        self._btn_clear.hide()
        head_lay.addWidget(self._btn_clear)
        self._root.addWidget(head)

        # ── 본문 컨테이너 ──────────────────────────────────
        self._body = QWidget()
        self._body.setObjectName("slotBody")
        self._body.setStyleSheet(
            f"QWidget#slotBody {{ background: {_CARD_BG};"
            " border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }}"
        )
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(0)
        self._root.addWidget(self._body, stretch=1)

        self._render_empty()

    # ── 외부 API ─────────────────────────────────────────
    def load_file(self, path: str) -> None:
        try:
            data = load_case(path)
        except Exception as e:
            QMessageBox.critical(
                self, "파일 불러오기 실패",
                f"파일을 읽지 못했습니다:\n{path}\n\n오류: {e}"
            )
            return
        self._case = data
        self._file_label = os.path.basename(path)
        self._render_filled()
        self.state_changed.emit()

    def clear_case(self) -> None:
        self._case = None
        self._file_label = ""
        self._render_empty()
        self.state_changed.emit()

    # 차트가 케이스 데이터(ev) 를 읽을 수 있게.
    def case_evaluation(self) -> Optional[Dict[str, Any]]:
        if not self._case:
            return None
        return (self._case.get("results") or {}).get("evaluation") or {}

    def file_label(self) -> str:
        return self._file_label or ""

    # ── 빈 상태 ───────────────────────────────────────────
    def _render_empty(self) -> None:
        self._clear_body()
        self._file_lbl.setText("")
        self._btn_clear.hide()

        empty_host = QWidget()
        empty_host.setObjectName("emptyHost")
        empty_host.setStyleSheet(
            f"QWidget#emptyHost {{ background: {_CARD_BG}; }}"
        )
        eh_lay = QVBoxLayout(empty_host)
        eh_lay.setContentsMargins(20, 20, 20, 20)
        eh_lay.setSpacing(6)
        eh_lay.addStretch(1)

        btn = QPushButton("파일 불러오기")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(56)
        btn.setStyleSheet(
            "QPushButton { padding: 14px 22px; border: 2px dashed #1F4E79;"
            " border-radius: 10px; background: #F5F8FF; color: #1F4E79;"
            " font-weight: 800; font-size: 14px; }"
            "QPushButton:hover { background: #1F4E79; color: white;"
            " border-style: solid; }"
        )
        btn.clicked.connect(self._on_open_file)
        eh_lay.addWidget(btn, alignment=Qt.AlignCenter)

        hint = QLabel(".case.json 파일을 선택하세요")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            f"color: {_EMPTY_COLOR}; font-size: 11px; padding-top: 6px;"
            " background: transparent;"
        )
        eh_lay.addWidget(hint)

        eh_lay.addStretch(1)
        self._body_lay.addWidget(empty_host, stretch=1)

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "비교할 .case.json 선택", "", "케이스 파일 (*.case.json)"
        )
        if path:
            self.load_file(path)

    # ── 채워진 상태 ────────────────────────────────────────
    def _render_filled(self) -> None:
        self._clear_body()
        case = self._case or {}
        ev = (case.get("results") or {}).get("evaluation") or {}
        self._file_lbl.setText(self._file_label)
        self._btn_clear.show()

        # 스크롤 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: white; }"
        )
        inner = QWidget()
        inner.setObjectName("slotInner")
        inner.setStyleSheet(
            f"QWidget#slotInner {{ background: {_CARD_BG}; }}"
        )
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(10, 10, 10, 10)
        inner_lay.setSpacing(8)

        # ── 평면도 + 단면도 (양옆) ────────────────────────
        views_row = QHBoxLayout()
        views_row.setSpacing(6)

        # 평면도 — 흰 카드
        plan_box = QFrame()
        plan_box.setObjectName("planBox")
        plan_box.setStyleSheet(
            "QFrame#planBox { background: #FAFBFD;"
            f" border: 1px solid {_CARD_BORDER}; border-radius: 6px; }}"
        )
        plan_box.setMinimumHeight(180)
        pb = QVBoxLayout(plan_box)
        pb.setContentsMargins(6, 6, 6, 6)
        pb.setSpacing(2)
        pcap = QLabel("평면도")
        pcap.setStyleSheet(
            "background: transparent; color: #1F4E79; font-size: 10px;"
            " font-weight: 700;"
        )
        pb.addWidget(pcap)
        plan_img = _AutoScalePixmapLabel()
        plan_img.setStyleSheet("background: white;")
        plan_img.setAlignment(Qt.AlignCenter)
        pix = self._decode_layout_pixmap(case)
        if pix is not None and not pix.isNull():
            plan_img.setRawPixmap(pix)
        else:
            plan_img.setText("저장된\n평면도 없음")
            plan_img.setStyleSheet(
                f"color: {_EMPTY_COLOR}; font-size: 11px; background: white;"
            )
        pb.addWidget(plan_img, stretch=1)
        views_row.addWidget(plan_box, stretch=1)

        # 단면도 (자리만 — 추후 구현)
        sec_box = QFrame()
        sec_box.setObjectName("sectionBox")
        sec_box.setStyleSheet(
            "QFrame#sectionBox { background: #FAFBFD;"
            " border: 1px dashed #B8C4D6; border-radius: 6px; }"
        )
        sec_box.setMinimumHeight(180)
        sb = QVBoxLayout(sec_box)
        sb.setContentsMargins(6, 6, 6, 6)
        sb.setSpacing(2)
        scap = QLabel("단면도")
        scap.setStyleSheet(
            "background: transparent; color: #1F4E79; font-size: 10px;"
            " font-weight: 700;"
        )
        sb.addWidget(scap)
        sec_lbl = QLabel("단면\n(추후 구현)")
        sec_lbl.setAlignment(Qt.AlignCenter)
        sec_lbl.setStyleSheet(
            f"color: {_EMPTY_COLOR}; font-size: 12px; background: transparent;"
        )
        sb.addWidget(sec_lbl, stretch=1)
        views_row.addWidget(sec_box, stretch=1)

        inner_lay.addLayout(views_row, stretch=2)

        # ── 데이터 섹션들 ─────────────────────────────────
        headline = ev.get("headline") or {}
        members = ev.get("members") or {}
        attached = members.get("attached") or {}
        panels = members.get("panels") or {}
        panels_by_type = members.get("panels_by_type") or []
        modules_by_type = members.get("modules_by_type") or []
        materials = ev.get("materials") or {}
        transport = ev.get("transport") or {}
        schedule = ev.get("schedule") or {}
        cost = ev.get("cost") or {}

        # 데이터 표 호스트 (전체를 흰 바탕 + 외곽 라운드)
        tables_host = QFrame()
        tables_host.setObjectName("tablesHost")
        tables_host.setStyleSheet(
            "QFrame#tablesHost {"
            f" background: {_CARD_BG};"
            f" border: 1px solid {_CARD_BORDER}; border-radius: 6px; }}"
        )
        th_lay = QVBoxLayout(tables_host)
        th_lay.setContentsMargins(0, 0, 0, 0)
        th_lay.setSpacing(0)

        # 면적 — 평면효율(Plan Efficiency) 한 줄로 압축.
        s = _SectionTable("면적")
        s.add_row("연면적", _fmt_float(headline.get("total_floor_area_m2"), 1, "㎡"))
        s.add_row("1층 footprint", _fmt_float(headline.get("footprint_m2"), 1, "㎡"))
        eff = headline.get("effective_area_floor1") or {}
        if eff.get("gross_m2"):
            s.add_row("평면효율", f"{eff.get('ratio_pct', 0):.1f}%")
        th_lay.addWidget(s)

        # 컴포넌트 종류 수 — 코어슬래브는 패널이 아니므로 패널 타입 카운트에서 제외.
        # [2026-05-31] 사용자 요청 — 접합부재 종류 행은 제거.
        panels_by_type_no_core = [
            p for p in panels_by_type
            if (p.get("class_key") or p.get("class") or "") != "core_slab"
            and "코어슬래브" not in str(p.get("class_label", ""))
        ]
        n_mod_types = len(modules_by_type)
        n_panel_types = len(panels_by_type_no_core)
        s = _SectionTable("컴포넌트 종류 수")
        s.add_row("모듈 타입", _fmt_int(n_mod_types, "종"))
        s.add_row("패널 타입", _fmt_int(n_panel_types, "종"))
        th_lay.addWidget(s)

        # [2026-05-31] 모듈 구성/패널 구성/접합부 수량 섹션 제거.
        # 비교 의사결정에는 면적·비용·공기·평면효율 핵심 지표로 충분.
        # 구성 상세는 종합탭에서 케이스별로 확인.

        # 물량 — 정책 행은 제거 (사용자 요청).
        steel_total = (materials.get("steel_total") or {})
        slab = (materials.get("slab") or {})
        s = _SectionTable("물량")
        s.add_row("강재 총중량", _fmt_float(steel_total.get("total_weight_ton"), 3, "t"))
        s.add_row("슬래브 부피", _fmt_float(slab.get("total_volume_m3"), 2, "㎥"))
        th_lay.addWidget(s)

        # 비용
        s = _SectionTable("비용")
        s.add_row("자재", _won(cost.get("material_krw", 0)))
        s.add_row("운송", _won(cost.get("transport_krw", 0)))
        s.add_row("노무", _won(cost.get("labor_krw", 0)))
        s.add_row("경비", _won(cost.get("equip_krw", 0)))
        s.add_row("합계", _won(cost.get("total_krw", 0)), total=True)
        th_lay.addWidget(s)

        # 공기 — 총 공기만 표시 (코어/모듈 설치는 제거).
        s = _SectionTable("공기")
        if schedule.get("available"):
            s.add_row("총 공기", _fmt_int(schedule.get("total_days"), "일"))
        else:
            s.add_row("—", "데이터 없음")
        th_lay.addWidget(s)

        inner_lay.addWidget(tables_host, stretch=0)
        inner_lay.addStretch(1)

        scroll.setWidget(inner)
        self._body_lay.addWidget(scroll, stretch=1)

    # ── 헬퍼 ─────────────────────────────────────────────
    def _clear_body(self) -> None:
        while self._body_lay.count() > 0:
            item = self._body_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    @staticmethod
    def _decode_layout_pixmap(case: Dict[str, Any]) -> Optional[QPixmap]:
        try:
            b64 = (case.get("images") or {}).get("layout_png_b64")
            if not b64:
                return None
            raw = base64.b64decode(b64)
            pix = QPixmap()
            pix.loadFromData(raw, "PNG")
            # [2026-05-31] 옛 케이스 파일은 검은 배경 PNG. 검정 픽셀을 흰색으로
            # 치환해서 박스 안에 자연스럽게 들어가게.
            return _CaseSlot._whiten_dark_pixels(pix, threshold=45)
        except Exception:
            return None

    @staticmethod
    def _whiten_dark_pixels(pix: QPixmap, threshold: int = 40) -> QPixmap:
        """RGB 채널 최댓값이 threshold 이하인(짙은 검정) 픽셀을 흰색으로 치환.

        캔버스 paintEvent 가 깐 검은 배경 부분만 흰색이 됨. 모듈(파랑·초록),
        코어(회색), 텍스트(흰색) 등 콘텐츠는 RGB 값이 충분히 커서 영향 없음.
        numpy 로 일괄 처리 — 큰 픽스맵도 빠름.
        """
        try:
            import numpy as np
            from PyQt5.QtGui import QImage
            img = pix.toImage().convertToFormat(QImage.Format_RGBA8888)
            w = img.width(); h = img.height()
            if w < 4 or h < 4:
                return pix
            ptr = img.bits()
            ptr.setsize(img.byteCount())
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 4).copy()
            rgb_max = arr[:, :, :3].max(axis=2)
            mask = rgb_max < int(threshold)
            arr[mask, 0] = 255
            arr[mask, 1] = 255
            arr[mask, 2] = 255
            arr[mask, 3] = 255
            out = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888).copy()
            return QPixmap.fromImage(out)
        except Exception:
            return pix

    @staticmethod
    def _trim_dark_borders(pix: QPixmap, threshold: int = 40,
                            min_content_frac: float = 0.01) -> QPixmap:
        """검은(짙은) 가장자리 행·열을 잘라낸 픽스맵 반환.

        - threshold: RGB 채널 최댓값이 이 값 이하면 '검은 픽셀' 판정.
        - min_content_frac: 한 행/열에 "밝은 픽셀" 비율이 이만큼 이상이어야
          '콘텐츠 있는 행' 으로 판정. 노이즈 한 점이 가장자리 트리밍을 막는
          것을 방지.
        bbox 가 너무 작으면 원본 그대로 반환.
        """
        try:
            img = pix.toImage()
            w = img.width(); h = img.height()
            if w < 20 or h < 20:
                return pix

            sx = max(1, w // 200)
            sy = max(1, h // 200)
            samples_per_row = max(1, w // sx)
            samples_per_col = max(1, h // sy)
            min_row_hits = max(2, int(samples_per_row * min_content_frac))
            min_col_hits = max(2, int(samples_per_col * min_content_frac))

            def row_has_content(y: int) -> bool:
                hits = 0
                for x in range(0, w, sx):
                    c = img.pixelColor(x, y)
                    if max(c.red(), c.green(), c.blue()) > threshold:
                        hits += 1
                        if hits >= min_row_hits:
                            return True
                return False

            def col_has_content(x: int) -> bool:
                hits = 0
                for y in range(0, h, sy):
                    c = img.pixelColor(x, y)
                    if max(c.red(), c.green(), c.blue()) > threshold:
                        hits += 1
                        if hits >= min_col_hits:
                            return True
                return False

            top = 0
            found = False
            for y in range(h):
                if row_has_content(y):
                    top = y; found = True; break
            if not found:
                return pix

            bot = h - 1
            for y in range(h - 1, -1, -1):
                if row_has_content(y):
                    bot = y; break

            left = 0
            for x in range(w):
                if col_has_content(x):
                    left = x; break

            right = w - 1
            for x in range(w - 1, -1, -1):
                if col_has_content(x):
                    right = x; break

            # 안전 마진 작게.
            pad = max(2, min(w, h) // 100)
            left = max(0, left - pad)
            top = max(0, top - pad)
            right = min(w - 1, right + pad)
            bot = min(h - 1, bot + pad)

            cw = right - left + 1
            ch = bot - top + 1
            if cw < 20 or ch < 20 or (cw == w and ch == h):
                return pix
            return pix.copy(left, top, cw, ch)
        except Exception:
            return pix


# ── 케이스 비교 막대 차트 ─────────────────────────────────
# [2026-05-31] 사용자 요청 — 비교탭 하단에 케이스1/2/3 의 비용·공기·평면효율을
# 막대 차트로 한눈에 비교. QPainter 로 직접 그려 외부 의존성 없음.

# 막대 색 — 케이스 A/B/C 일관 색.
_BAR_COLORS = ["#4F86C6", "#E08C3D", "#5BAA6F"]
_BAR_AXIS = "#7A8597"
_BAR_GRID = "#E3E8F0"
_BAR_TEXT = "#1F2A37"
_BAR_LABEL = "#5B6573"
_BAR_TITLE = "#1F4E79"


class _BarChart(QWidget):
    """제목 + 케이스별 막대 + x축 라벨 + 값 라벨.

    setData([v0, v1, v2], formatter=..., higher_is_better=...) 로 갱신.
    None 인 값은 비활성(연한 회색 점선) 막대로 그림.
    """

    def __init__(self, title: str, formatter, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title = title
        self._fmt = formatter
        self._values: List[Optional[float]] = [None, None, None]
        self._x_labels: List[str] = ["케이스1", "케이스2", "케이스3"]
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # [버그픽스 2026-05-31] stylesheet + paintEvent 동시 사용 시 PyQt5 paint
        # engine 충돌로 비교탭 진입 즉시 abort. stylesheet 제거 → paintEvent 에서
        # 배경/테두리까지 직접 그림.

    def set_values(self, values: List[Optional[float]]) -> None:
        v = list(values) + [None] * 3
        self._values = v[:3]
        self.update()

    # ── paint ──
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter()
        if not p.begin(self):
            return
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            W = int(self.width()); H = int(self.height())
            if W <= 4 or H <= 4:
                return

            # ── 카드 외곽 + 라운드 ─────────────────────────
            outer = QRectF(0.5, 0.5, float(W - 1), float(H - 1))
            p.setPen(QPen(QColor(_CARD_BORDER), 1))
            p.setBrush(QBrush(QColor(_CARD_BG)))
            p.drawRoundedRect(outer, 6.0, 6.0)

            # ── 헤더 막대 (연파란 배경) — 물량탭 카드 느낌 ──
            header_h = 26.0
            header_rect = QRectF(1.0, 1.0, float(W - 2), header_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(_SECTION_BG)))
            # 위쪽만 라운드 — clip 으로 카드 라운드 안에 맞춤
            p.save()
            p.setClipRect(QRectF(1.0, 1.0, float(W - 2), header_h))
            p.drawRoundedRect(outer, 6.0, 6.0)
            p.restore()
            # 헤더 하단 구분선
            p.setPen(QPen(QColor(_CARD_BORDER), 1))
            p.drawLine(1, int(1 + header_h), W - 1, int(1 + header_h))

            # 제목 텍스트
            f_title = QFont()
            f_title.setBold(True)
            f_title.setPointSize(10)
            p.setFont(f_title)
            p.setPen(QPen(QColor(_SECTION_FG)))
            p.drawText(
                QRectF(12.0, 1.0, float(W - 24), header_h),
                int(Qt.AlignLeft | Qt.AlignVCenter), str(self._title),
            )

            # ── 차트 본문 영역 ─────────────────────────────
            pad_l, pad_r, pad_t, pad_b = 38.0, 16.0, header_h + 18.0, 30.0
            cx0 = pad_l
            cy0 = pad_t
            cw = max(10.0, W - pad_l - pad_r)
            ch = max(10.0, H - pad_t - pad_b)
            baseline_y = cy0 + ch

            # 정규화
            nums = [v for v in self._values if isinstance(v, (int, float)) and v > 0]
            v_max = max(nums) if nums else 1.0
            if v_max <= 0:
                v_max = 1.0
            top_v = v_max * 1.20  # 위 20% 여유

            # ── 가로 그리드 라인 + y축 라벨 ────────────────
            f_axis = QFont(); f_axis.setPointSize(7)
            p.setFont(f_axis)
            n_grid = 4
            for gi in range(n_grid + 1):
                ratio = gi / float(n_grid)
                gy = baseline_y - ch * ratio
                # 그리드 라인
                if gi == 0:
                    pen = QPen(QColor(_BAR_AXIS), 1)
                else:
                    pen = QPen(QColor(_BAR_GRID), 1, Qt.DashLine)
                p.setPen(pen)
                p.drawLine(int(cx0), int(gy), int(cx0 + cw), int(gy))
                # y 라벨
                gv = top_v * ratio
                try:
                    glabel = self._fmt(gv) if gv > 0 else "0"
                except Exception:
                    glabel = f"{int(round(gv))}"
                p.setPen(QPen(QColor(_BAR_LABEL)))
                p.drawText(
                    QRectF(0.0, float(gy - 8), float(cx0 - 4), 16.0),
                    int(Qt.AlignRight | Qt.AlignVCenter), str(glabel),
                )

            # ── 막대 ──────────────────────────────────────
            n = 3
            gap = cw * 0.10
            bar_w = (cw - gap * (n + 1)) / n
            if bar_w < 6.0:
                bar_w = 6.0

            f_val = QFont(); f_val.setPointSize(9); f_val.setBold(True)
            f_lbl = QFont(); f_lbl.setPointSize(8)

            for i in range(n):
                x = cx0 + gap + i * (bar_w + gap)
                v = self._values[i]
                if v is None or not isinstance(v, (int, float)) or v <= 0:
                    p.setPen(QPen(QColor("#C8D2E0"), 1, Qt.DashLine))
                    p.setBrush(Qt.NoBrush)
                    empty_h = ch * 0.12
                    rect = QRectF(float(x), float(baseline_y - empty_h),
                                  float(bar_w), float(empty_h))
                    p.drawRoundedRect(rect, 3.0, 3.0)
                    p.setFont(f_val)
                    p.setPen(QPen(QColor(_EMPTY_COLOR)))
                    p.drawText(
                        QRectF(float(x - 4), float(baseline_y - empty_h - 18),
                               float(bar_w + 8), 16.0),
                        int(Qt.AlignCenter), "—",
                    )
                else:
                    h = (float(v) / float(top_v)) * ch
                    rect = QRectF(float(x), float(baseline_y - h),
                                  float(bar_w), float(h))
                    p.setPen(Qt.NoPen)
                    p.setBrush(QBrush(QColor(_BAR_COLORS[i])))
                    p.drawRoundedRect(rect, 3.0, 3.0)
                    p.setFont(f_val)
                    p.setPen(QPen(QColor(_BAR_TEXT)))
                    try:
                        lbl = self._fmt(v)
                    except Exception:
                        lbl = f"{v}"
                    p.drawText(
                        QRectF(float(x - 20), float(baseline_y - h - 20),
                               float(bar_w + 40), 16.0),
                        int(Qt.AlignCenter), str(lbl),
                    )

                # x축 라벨 — 케이스1/2/3
                p.setFont(f_lbl)
                p.setPen(QPen(QColor(_BAR_LABEL)))
                p.drawText(
                    QRectF(float(x - 20), float(baseline_y + 6),
                           float(bar_w + 40), 18.0),
                    int(Qt.AlignCenter), str(self._x_labels[i]),
                )
        finally:
            p.end()


class ComparePanel(QWidget):
    """비교 탭 루트 — 3 슬롯 + 하단 비교 차트 3 개."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 상단 툴바 ─────────────────────────────────────
        tb = QWidget()
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(16, 10, 16, 6)
        tb_lay.setSpacing(8)
        title = QLabel("비교 — 케이스 3 개 가로 비교")
        title.setStyleSheet(
            "font-weight: 700; font-size: 14px; color: #1F4E79;"
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

        # ── 본문: 상단(3 슬롯) + 하단(3 차트) ───────────────
        body = QWidget()
        body.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(16, 6, 16, 16)
        body_v.setSpacing(12)
        root.addWidget(body, stretch=1)

        # 슬롯 행 (상단)
        slots_row = QHBoxLayout()
        slots_row.setSpacing(12)
        labels = ["A", "B", "C"]
        self._slots: List[_CaseSlot] = []
        for i in range(_SLOT_COUNT):
            slot = _CaseSlot(labels[i])
            slot.state_changed.connect(self._refresh_charts)
            self._slots.append(slot)
            slots_row.addWidget(slot, stretch=1)
        body_v.addLayout(slots_row, stretch=5)

        # 차트 행 (하단) — 비용 / 공기 / 평면효율
        self._chart_cost = _BarChart(
            "비용 (원)",
            lambda v: f"{int(round(v / 1e6)):,}M원" if v >= 1e6 else f"{int(round(v)):,}원",
        )
        self._chart_days = _BarChart(
            "공기 (일)",
            lambda v: f"{int(round(v)):,}일",
        )
        self._chart_eff = _BarChart(
            "평면효율 (%)",
            lambda v: f"{v:.1f}%",
        )
        charts_row = QHBoxLayout()
        charts_row.setSpacing(12)
        for ch in (self._chart_cost, self._chart_days, self._chart_eff):
            charts_row.addWidget(ch, stretch=1)
        body_v.addLayout(charts_row, stretch=3)

        self._refresh_charts()

    def _on_clear_all(self) -> None:
        for s in self._slots:
            s.clear_case()

    # ── 슬롯 상태 → 차트 갱신 ────────────────────────────
    def _refresh_charts(self) -> None:
        costs: List[Optional[float]] = []
        days: List[Optional[float]] = []
        effs: List[Optional[float]] = []
        for s in self._slots:
            ev = s.case_evaluation()
            if not ev:
                costs.append(None); days.append(None); effs.append(None)
                continue
            cost = (ev.get("cost") or {}).get("total_krw")
            sch = ev.get("schedule") or {}
            total_days = sch.get("total_days") if sch.get("available") else None
            eff = ((ev.get("headline") or {}).get("effective_area_floor1") or {}).get("ratio_pct")
            costs.append(float(cost) if isinstance(cost, (int, float)) and cost > 0 else None)
            days.append(float(total_days) if isinstance(total_days, (int, float)) and total_days > 0 else None)
            effs.append(float(eff) if isinstance(eff, (int, float)) and eff > 0 else None)
        self._chart_cost.set_values(costs)
        self._chart_days.set_values(days)
        self._chart_eff.set_values(effs)
