"""물량탭 우측 비용 패널 — 물량탭 개편 Phase 3.

[구성]
- 상단 트리: [타입] > [역할] > [부재(단면)] 계층. 펼치면 물량·금액. 층 단계 없음
  (§1-8). 응력비 색 없음(§3-C, 사용자 확정) — 비용 집중.
  상위 노드 값은 숨기고(빈칸), 최종 총합은 트리 하단 라벨에 별도 표기.
- 하단: 강재 본수표 + 자재비표 — *프로젝트 전체* QuantityReport 기준(§3-C).
  기존 analysis_panel 의 채우기 로직과 동치이되, 본 패널은 독립(회귀 위험 차단).

[데이터 출처]
- 트리: analysis.quantity_by_component.QuantityByComponent (타입별 분해).
- 하단 표: analysis.quantity_takeoff.QuantityReport (전체) + MaterialCost.
  ※ 평가탭이 쓰는 _quantity_reports 채움 경로와 무관(읽기만, 별도 사본 X).

[강조 연동 — Phase 4 에서 배선]
- 트리 타입 노드 선택 → type_selected(trip_idx) emit → 3D 타입 강조.
  본 패널은 시그널만 정의, 실제 3D 연결은 main_3d 가 담당.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from modular_3d._utils.format import won as _won
from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded


# ── 종합탭 톤 디자인 토큰 ─────────────────────────────────
_PAGE_BG     = "#EDF2F7"
_CARD_BG     = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEAD_FG     = "#1F4E79"
_BODY_FG     = "#1F2A37"

# 패널 전체 스타일시트 — 구조해석 탭과 동일 톤(인접 탭 일관성).
# - QLabel(캡션·묻는 글) → Paperlogy
# - QTreeWidget/QTableWidget(값·표) → Freesentation, 흰 카드 톤
# - QHeaderView::section(표 머리) → Paperlogy (구조해석과 동일)
_QUANTITY_QSS = (
    f"QuantityPanel {{ background: {_PAGE_BG}; }}"
    "QLabel {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; font-weight: 700; color: {_HEAD_FG};"
    " background: transparent; }"
    "QTreeWidget, QTableWidget {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" color: {_BODY_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 8px; }"
    "QHeaderView::section {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 14px; font-weight: 700; color: {_HEAD_FG};"
    f" background: {_PAGE_BG}; border: none;"
    f" border-bottom: 1px solid {_CARD_BORDER}; padding: 6px 8px;"
    " }"
)


class QuantityPanel(QWidget):
    """물량탭 우측 패널 — 타입별 비용 트리 + 전체 본수표·자재비표."""

    # 트리에서 타입 선택 → 그 타입 인덱스(0-based) emit (3D 강조용, Phase 4 배선).
    type_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._types_cache: List = []          # TypeQuantity 리스트
        self._build_ui()

    # ── UI 구성 ──────────────────────────────────────────────
    def _build_ui(self):
        # [2026-06-02 디자인 통일] 종합/구조해석 탭 톤 적용.
        ensure_fonts_loaded()
        self.setStyleSheet(_QUANTITY_QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # 패널 헤더 라벨(Paperlogy) — 다른 탭과 동일한 제목 톤.
        _hdr = QLabel('물량 산출')
        _hdr.setStyleSheet(
            f"font-family:'{F_HEAD}','Malgun Gothic',sans-serif;"
            f" font-size:20px; font-weight:800; color:{_HEAD_FG};"
            " background:transparent; padding:2px;")
        lay.addWidget(_hdr)

        # 1) 타입별 비용 트리
        lay.addWidget(QLabel("타입별 물량·비용 (행 클릭 → 3D 강조):"))
        self._tree = QTreeWidget()
        # [2026-06-02] 정렬을 직접 그리는 전용 헤더(구조해석과 공유) — 스타일시트가
        #   적용된 기본 헤더는 setTextAlignment 를 무시해 헤더가 늘 왼쪽으로 그려진다.
        #   물량 열 헤더 "물량" 을 가운데 정렬해 셀 값 중심과 같은 세로선에 맞춘다.
        from modular_3d.ui.analysis_panel import _AlignedHeader
        self._tree.setHeader(_AlignedHeader(self._tree, font_px=14))
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["항목", "물량", "금액(원)"])
        self._tree.setIndentation(16)
        tf = QFont("Consolas")
        tf.setPointSize(11)   # 구조해석 부재 트리와 동일 크기(가독성)
        self._tree.setFont(tf)
        hdr = self._tree.header()
        # [2026-06-01] 텍스트 안 짤리게 — 모든 컬럼 내용 폭에 맞춰 확장 + 가로 스크롤 허용.
        for c in range(3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(False)
        # 헤더 정렬: 항목=왼쪽, 물량=가운데(셀 값 중심과 정렬), 금액=오른쪽(통화).
        if isinstance(hdr, _AlignedHeader):
            hdr.set_column_alignment(0, Qt.AlignLeft)
            hdr.set_column_alignment(1, Qt.AlignHCenter)
            hdr.set_column_alignment(2, Qt.AlignHCenter)
        self._tree.setTextElideMode(Qt.ElideNone)
        # [2026-06-01] 가로 스크롤 대신 패널이 내용 폭에 맞춰 넓어지게(_fit_width).
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.currentItemChanged.connect(self._on_tree_current_changed)
        lay.addWidget(self._tree, stretch=1)
        # (총비용은 자재비표 제목 라벨이 대체 — 아래 4번)

        # 3) 강재 본수표 (프로젝트 전체)
        lay.addWidget(QLabel("강재 본수표 (프로젝트 전체):"))
        self._steel_table = QTableWidget()
        self._steel_table.setColumnCount(6)
        self._steel_table.setHorizontalHeaderLabels(
            ["단면", "길이(mm)", "본수", "총 길이(m)", "총 중량(ton)", "금액(원)"])
        sh = self._steel_table.horizontalHeader()
        sh.setSectionResizeMode(QHeaderView.ResizeToContents)
        sh.setStretchLastSection(False)
        self._steel_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._steel_table.setMaximumHeight(220)
        self._steel_table.setTextElideMode(Qt.ElideNone)
        self._steel_table.setWordWrap(False)
        self._steel_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self._steel_table)

        # 4) 자재비표 (프로젝트 전체).
        #   [2026-06-01 사용자 요청] 트리 위에 있던 "총 자재비: 금액" 라벨을 제거하고,
        #   그 의미를 이 자재비표 제목이 대체한다. 금액은 표 합계 행에 있으므로 제목엔
        #   넣지 않는다(중복 제거).
        self._matcost_caption = QLabel("총 자재비 (재료비, 프로젝트 전체):")
        mc_f = self._matcost_caption.font(); mc_f.setBold(True)
        self._matcost_caption.setFont(mc_f)
        lay.addWidget(self._matcost_caption)
        self._matcost_table = QTableWidget()
        self._matcost_table.setColumnCount(4)
        self._matcost_table.setHorizontalHeaderLabels(
            ["항목", "물량", "단가", "금액(원)"])
        mh = self._matcost_table.horizontalHeader()
        mh.setSectionResizeMode(QHeaderView.ResizeToContents)
        mh.setStretchLastSection(True)
        self._matcost_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._matcost_table.setRowCount(4)
        self._matcost_table.setMaximumHeight(150)
        self._matcost_table.setTextElideMode(Qt.ElideNone)
        self._matcost_table.setWordWrap(False)
        self._matcost_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self._matcost_table)

    # ── 데이터 주입 ──────────────────────────────────────────
    def populate(self, qbc, report=None, matcost=None):
        """타입별 분해(QuantityByComponent) + 전체 보고서(QuantityReport)로 갱신.

        qbc: QuantityByComponent (트리·총합). None 이면 비움.
        report: QuantityReport (하단 본수표). None 이면 본수표 비움.
        matcost: MaterialCost (하단 자재비표). None 이면 자재비표 비움.
        """
        self._fill_tree(qbc)
        self._fill_steel_table(report)
        self._fill_matcost_table(matcost)
        self._fit_width()

    def _fit_width(self):
        """트리·표 내용 폭 합에 맞춰 패널 minimumWidth 확장(가로 스크롤 대신 창 넓힘).

        [2026-06-01 사용자 요청] 가로 스크롤이 아니라 창(패널)이 내용에 맞춰 넓어져야 함.
        analysis_panel._fit_panel_to_quantity 와 동일 원리 — 컬럼 폭 합 + 여백·세로바 폭.
        """
        from PyQt5.QtGui import QFontMetrics
        max_w = 380
        # 트리 — 컬럼 폭 합 + 인덴트·여백.
        tw = sum(self._tree.columnWidth(c) for c in range(self._tree.columnCount()))
        tw += 2 * self._tree.frameWidth() + 36
        vb = self._tree.verticalScrollBar()
        if vb is not None:
            tw += vb.sizeHint().width()
        max_w = max(max_w, tw)
        # 표 2개 — 헤더/셀 중 큰 폭으로 컬럼 맞춘 뒤 합산.
        for t in (self._steel_table, self._matcost_table):
            t.resizeColumnsToContents()
            hdr = t.horizontalHeader()
            fm = QFontMetrics(hdr.font())
            for c in range(t.columnCount()):
                hi = t.horizontalHeaderItem(c)
                ht = hi.text() if hi is not None else ''
                hw = fm.horizontalAdvance(ht) + 24
                if t.columnWidth(c) < hw:
                    t.setColumnWidth(c, hw)
            w = sum(t.columnWidth(c) for c in range(t.columnCount()))
            w += 2 * t.frameWidth() + 24
            vb2 = t.verticalScrollBar()
            if vb2 is not None:
                w += vb2.sizeHint().width()
            max_w = max(max_w, w)
        self.setMinimumWidth(int(max_w + 12))
        self.updateGeometry()

    def clear(self):
        self._tree.clear()
        self._types_cache = []
        self._steel_table.setRowCount(0)
        for r in range(self._matcost_table.rowCount()):
            for c in range(self._matcost_table.columnCount()):
                self._matcost_table.setItem(r, c, QTableWidgetItem(""))

    # ── 트리 채우기 ──────────────────────────────────────────
    def _fill_tree(self, qbc):
        self._tree.clear()
        self._types_cache = []
        if qbc is None or not getattr(qbc, "types", None):
            return
        self._types_cache = list(qbc.types)

        for ti_idx, tq in enumerate(qbc.types):
            # [타입] 노드 — "모듈A-1 (×3)". 값 컬럼은 숨김(빈칸, §3-C).
            type_text = f"{tq.type_label}  (×{tq.instance_count})"
            type_item = QTreeWidgetItem([type_text, "", ""])
            # UserRole 에 타입 인덱스 저장 — 선택 시 3D 강조(trip_no = idx+1).
            type_item.setData(0, Qt.UserRole, ti_idx)
            f = type_item.font(0)
            f.setBold(True)
            type_item.setFont(0, f)

            # [역할] — 같은 역할에 단면이 *1종뿐이면* 역할 중간 노드를 생략하고
            #   "기둥 SHS 250x250x8" 처럼 한 줄로 바로 표시(불필요한 펼침 제거,
            #   2026-06-01 사용자 요청). 같은 역할에 단면 2종 이상일 때만 역할 노드로 묶음.
            # 먼저 역할별 줄 수 집계(steel_lines 는 role 정렬돼 같은 역할 연속).
            lines_by_role = {}
            for line in tq.rep.steel_lines:
                lines_by_role.setdefault(line.role_ko, []).append(line)

            for role_ko, lines in lines_by_role.items():
                qty_of = lambda ln: f"{ln.count}본 · {ln.total_weight_ton:.3f}t"
                if len(lines) == 1:
                    # 단면 1종 — "역할 단면명" 한 줄로 직접.
                    ln = lines[0]
                    leaf = QTreeWidgetItem(
                        [f"{role_ko} {ln.section_name}", qty_of(ln), _won(ln.cost)])
                    leaf.setData(0, Qt.UserRole, None)
                    leaf.setTextAlignment(1, Qt.AlignHCenter | Qt.AlignVCenter)
                    leaf.setTextAlignment(2, Qt.AlignHCenter | Qt.AlignVCenter)
                    type_item.addChild(leaf)
                else:
                    # 단면 2종 이상 — 역할 노드 아래 단면별 leaf.
                    role_item = QTreeWidgetItem([role_ko, "", ""])
                    role_item.setData(0, Qt.UserRole, None)
                    type_item.addChild(role_item)
                    for ln in lines:
                        leaf = QTreeWidgetItem(
                            [ln.section_name, qty_of(ln), _won(ln.cost)])
                        leaf.setData(0, Qt.UserRole, None)
                        leaf.setTextAlignment(1, Qt.AlignHCenter | Qt.AlignVCenter)
                        leaf.setTextAlignment(2, Qt.AlignHCenter | Qt.AlignVCenter)
                        role_item.addChild(leaf)

            # 데크·콘크리트 줄 (있으면)
            if tq.rep.deck_area_m2 > 0:
                deck = QTreeWidgetItem(["데크슬래브", f"{tq.rep.deck_area_m2:.2f}㎡",
                                        _won(tq.rep.deck_cost)])
                deck.setTextAlignment(1, Qt.AlignHCenter | Qt.AlignVCenter)
                deck.setTextAlignment(2, Qt.AlignHCenter | Qt.AlignVCenter)
                type_item.addChild(deck)
            if tq.rep.concrete_m3 > 0:
                conc = QTreeWidgetItem(["콘크리트", f"{tq.rep.concrete_m3:.3f}㎥",
                                        _won(tq.rep.concrete_cost)])
                conc.setTextAlignment(1, Qt.AlignHCenter | Qt.AlignVCenter)
                conc.setTextAlignment(2, Qt.AlignHCenter | Qt.AlignVCenter)
                type_item.addChild(conc)

            self._tree.addTopLevelItem(type_item)

        self._tree.expandToDepth(0)  # 타입만 펼치고 역할은 접어 둠
        # [2026-06-02] 물량 열과 금액 열 사이 간격 확보 — 물량 열(1)을 자연폭 + 여백
        #   으로 고정한다. 넓어진 열 안에서도 헤더·값은 계속 가운데 정렬이라 중심은
        #   유지되고, 두 열 사이만 벌어진다.
        _COL_GAP = 28
        self._tree.header().setSectionResizeMode(1, QHeaderView.Interactive)
        self._tree.setColumnWidth(1, self._tree.sizeHintForColumn(1) + _COL_GAP)

    # ── 하단 본수표 (전체) ───────────────────────────────────
    def _fill_steel_table(self, report):
        if report is None or not getattr(report, "steel_items", None):
            self._steel_table.setRowCount(0)
            return
        from modular_3d.카탈로그.material_prices import get_unit_price
        steel_unit = get_unit_price('강재_각형강관_SHS')
        items = report.steel_items
        self._steel_table.setRowCount(len(items))
        for r, it in enumerate(items):
            sec_item = QTableWidgetItem(it.section_name)
            if it.is_total:
                f = sec_item.font(); f.setBold(True); sec_item.setFont(f)
            self._steel_table.setItem(r, 0, sec_item)
            len_text = f"{it.length_mm:.0f}" if not it.is_total else ""
            self._steel_table.setItem(r, 1, QTableWidgetItem(len_text))
            self._steel_table.setItem(r, 2, QTableWidgetItem(f"{it.count}"))
            self._steel_table.setItem(
                r, 3, QTableWidgetItem(f"{it.total_length_m:.2f}"))
            self._steel_table.setItem(
                r, 4, QTableWidgetItem(f"{it.total_weight_ton:.4f}"))
            if steel_unit is not None:
                amt = f"{it.total_weight_ton * steel_unit:,.0f}"
            else:
                amt = "—"
            self._steel_table.setItem(r, 5, QTableWidgetItem(amt))
            if it.is_total:
                for c in range(6):
                    cell = self._steel_table.item(r, c)
                    if cell is not None:
                        f = cell.font(); f.setBold(True); cell.setFont(f)

    # ── 하단 자재비표 (전체) ─────────────────────────────────
    def _fill_matcost_table(self, mc):
        if mc is None:
            for r in range(self._matcost_table.rowCount()):
                for c in range(self._matcost_table.columnCount()):
                    self._matcost_table.setItem(r, c, QTableWidgetItem(""))
            return
        # 단가 미입력 감지 — 미입력 항목은 금액이 0 으로 들어가 합계가 과소된다.
        _missing = [n for n, u in (
            ("강재", mc.steel_unit), ("데크슬래브", mc.deck_unit),
            ("콘크리트", mc.concrete_unit)) if not u]
        rows = [
            ("강재(각형강관)", f"{mc.steel_ton:.4f} ton",
             (f"{mc.steel_unit:,.0f} 원/ton" if mc.steel_unit else "미입력"),
             _won(mc.steel_cost)),
            ("데크슬래브", f"{mc.deck_area_m2:.2f} ㎡",
             (f"{mc.deck_unit:,.0f} 원/㎡" if mc.deck_unit else "미입력"),
             _won(mc.deck_cost)),
            ("콘크리트(레미콘)", f"{mc.concrete_m3:.3f} ㎥",
             (f"{mc.concrete_unit:,.0f} 원/㎥" if mc.concrete_unit else "미입력"),
             _won(mc.concrete_cost)),
            ("합계", "", "", _won(mc.total_cost)),
        ]
        for r, (name, qty, unit, amt) in enumerate(rows):
            for c, txt in enumerate((name, qty, unit, amt)):
                cell = QTableWidgetItem(txt)
                if r == 3:
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                self._matcost_table.setItem(r, c, cell)
        # 단가 미입력이 있으면 합계 행에 경고 표시(주황+툴팁) — 합계 과소를 알림.
        if _missing:
            from PyQt5.QtGui import QColor
            warn = QTableWidgetItem(
                f"합계  ⚠ 단가 미입력({', '.join(_missing)}) → 0원 처리, 과소")
            f = warn.font(); f.setBold(True); warn.setFont(f)
            warn.setForeground(QColor("#c0392b"))
            warn.setToolTip(
                "단가가 '미입력'인 항목은 금액이 0 으로 계산되어 합계가 실제보다 "
                "작습니다.\n카탈로그의 재료 단가표에 단가를 입력하면 합계에 반영됩니다.")
            self._matcost_table.setItem(3, 0, warn)

    # ── 트리 선택 → 시그널 ───────────────────────────────────
    def _on_tree_current_changed(self, current, _previous):
        """타입 노드 선택 시 type_selected(타입 인덱스) emit. 자식은 부모 타입으로."""
        if current is None:
            return
        item = current
        # 부재/역할 노드면 부모를 거슬러 타입 노드를 찾는다.
        idx = item.data(0, Qt.UserRole)
        while idx is None and item.parent() is not None:
            item = item.parent()
            idx = item.data(0, Qt.UserRole)
        if isinstance(idx, int):
            self.type_selected.emit(idx)


__all__ = ["QuantityPanel"]
