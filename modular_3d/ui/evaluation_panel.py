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

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


# ── 스타일 ────────────────────────────────────────────────
_CARD_BG = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEADLINE_BG = "#F5F8FF"
_HEADLINE_BORDER = "#1F4E79"
_SECTION_TITLE_COLOR = "#1F4E79"
_PAGE_BG = "#EDF2F7"
_EMPTY_COLOR = "#a0a8b5"


def _won(n: float) -> str:
    return f"{int(round(n)):,}원"


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"font-size: 13px; font-weight: 700; color: {_SECTION_TITLE_COLOR};"
        " padding: 4px 0 6px 2px;"
    )
    return lbl


def _card(min_height: int = 60) -> QFrame:
    f = QFrame()
    f.setStyleSheet(
        f"QFrame {{ background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
        " border-radius: 8px; }}"
    )
    f.setMinimumHeight(min_height)
    return f


def _empty_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet(f"color: {_EMPTY_COLOR}; font-size: 12px; padding: 18px;")
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

        # 상단 툴바
        toolbar = QWidget()
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(16, 10, 16, 6)
        tb_lay.setSpacing(8)
        tb_title = QLabel("평가 — 한 케이스 종합 보고서")
        tb_title.setStyleSheet("font-weight: 700; font-size: 14px; color: #1F4E79;")
        tb_lay.addWidget(tb_title)
        tb_lay.addStretch(1)
        self._save_btn = QPushButton("💾 평가 결과 저장")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(
            "QPushButton { padding: 6px 14px; border: 1px solid #1F4E79;"
            " border-radius: 6px; background: #fff; color: #1F4E79;"
            " font-weight: 700; font-size: 12px; }"
            "QPushButton:hover { background: #1F4E79; color: #fff; }"
        )
        self._save_btn.clicked.connect(self.save_case_requested.emit)
        tb_lay.addWidget(self._save_btn)
        root.addWidget(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        root.addWidget(scroll, stretch=1)

        body = QWidget()
        body.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(16, 6, 16, 16)
        body_lay.setSpacing(12)
        scroll.setWidget(body)

        # A. 건물 개요
        body_lay.addWidget(_section_title("A. 건물 개요"))
        self._head_strip = QFrame()
        self._head_strip.setStyleSheet(
            f"QFrame {{ background: {_HEADLINE_BG};"
            f" border-left: 4px solid {_HEADLINE_BORDER}; border-radius: 8px;"
            " padding: 12px 14px; }}"
        )
        self._head_lay = QHBoxLayout(self._head_strip)
        self._head_lay.setContentsMargins(0, 0, 0, 0)
        self._head_lay.setSpacing(20)
        self._head_cells: List[Dict[str, QLabel]] = []
        for label in ("총 층수", "지하층", "총 모듈 수", "1층 footprint", "연면적"):
            cell, val_lbl = self._mk_head_cell(label, "—")
            self._head_lay.addWidget(cell)
            self._head_cells.append({"label": label, "value": val_lbl})
        body_lay.addWidget(self._head_strip)

        # 중간 행: B(좌) / D+E(우)
        mid_row = QHBoxLayout()
        mid_row.setSpacing(12)

        b_col = QVBoxLayout()
        b_col.setSpacing(4)
        b_col.addWidget(_section_title("B. 부재 구성"))
        self._b_card = _card(min_height=180)
        self._b_inner = QVBoxLayout(self._b_card)
        self._b_inner.setContentsMargins(8, 8, 8, 8)
        self._b_inner.setSpacing(6)
        b_col.addWidget(self._b_card, stretch=1)
        mid_row.addLayout(b_col, stretch=1)

        de_col = QVBoxLayout()
        de_col.setSpacing(8)
        de_col.addWidget(_section_title("D. 운송 요약"))
        self._d_card = _card(min_height=80)
        self._d_inner = QHBoxLayout(self._d_card)
        self._d_inner.setContentsMargins(10, 10, 10, 10)
        self._d_inner.setSpacing(14)
        de_col.addWidget(self._d_card)
        de_col.addWidget(_section_title("E. 공기 요약"))
        self._e_card = _card(min_height=180)
        self._e_inner = QVBoxLayout(self._e_card)
        self._e_inner.setContentsMargins(8, 8, 8, 8)
        self._e_inner.setSpacing(6)
        de_col.addWidget(self._e_card, stretch=1)
        mid_row.addLayout(de_col, stretch=1)
        body_lay.addLayout(mid_row)

        # C. 물량·자재비 — 정책 토글 제거, 라벨로 현재 정책 표시
        c_title_row = QHBoxLayout()
        c_title_row.addWidget(_section_title("C. 강재·콘크리트 물량 + 자재비"))
        c_title_row.addStretch(1)
        self._c_policy_lbl = QLabel("정책: —")
        self._c_policy_lbl.setStyleSheet(
            "font-size: 11px; color: #555; padding: 4px 8px;"
            " background: #EEF4FF; border-radius: 4px;"
        )
        c_title_row.addWidget(self._c_policy_lbl)
        c_hint = QLabel("(물량 탭에서 변경)")
        c_hint.setStyleSheet("font-size: 10px; color: #888; padding-left: 4px;")
        c_title_row.addWidget(c_hint)
        body_lay.addLayout(c_title_row)
        self._c_card = _card(min_height=220)
        self._c_inner = QVBoxLayout(self._c_card)
        self._c_inner.setContentsMargins(8, 8, 8, 8)
        self._c_inner.setSpacing(6)
        body_lay.addWidget(self._c_card)

        # F. 종합 비용
        body_lay.addWidget(_section_title("F. 종합 비용"))
        self._cost_strip = QFrame()
        self._cost_strip.setStyleSheet(
            f"QFrame {{ background: {_HEADLINE_BG};"
            f" border-left: 4px solid {_HEADLINE_BORDER}; border-radius: 8px;"
            " padding: 12px 14px; }}"
        )
        self._cost_lay = QHBoxLayout(self._cost_strip)
        self._cost_lay.setContentsMargins(0, 0, 0, 0)
        self._cost_lay.setSpacing(20)
        self._cost_cells: Dict[str, QLabel] = {}
        for label, key, emphasize in (
            ("자재비", "material", False),
            ("운송비", "transport", False),
            ("노무비", "labor", False),
            ("경비",   "equip",     False),
            ("공사비 (합계)", "total", True),
        ):
            cell, val_lbl = self._mk_cost_cell(label, "—", emphasize)
            self._cost_lay.addWidget(cell)
            self._cost_cells[key] = val_lbl
        body_lay.addWidget(self._cost_strip)

        body_lay.addStretch(1)

        self._last_data: Optional[Dict[str, Any]] = None

    # ── 외부 진입점 ──────────────────────────────────────
    def apply_data(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        self._last_data = data
        self._render_headline(data.get("headline", {}))
        self._render_members(data.get("members", {}))
        self._render_materials(data.get("materials", {}))
        self._render_transport(data.get("transport", {}))
        self._render_schedule(data.get("schedule", {}))
        self._render_cost(data.get("cost", {}))

    # ── A. 헤드라인 ──────────────────────────────────────
    def _render_headline(self, h: Dict[str, Any]) -> None:
        vals = [
            (h.get("floors_above_ground", 0), "층"),
            (h.get("basement_floors", 0), "층"),
            (h.get("modules_total", 0), "개"),
            (h.get("footprint_m2", 0.0), "㎡"),
            (h.get("total_floor_area_m2", 0.0), "㎡"),
        ]
        for cell, (val, unit) in zip(self._head_cells, vals):
            if isinstance(val, float):
                cell["value"].setText(f"{val:,.1f} {unit}")
            else:
                cell["value"].setText(f"{val:,} {unit}")

    # ── B. 부재 구성 ─────────────────────────────────────
    def _render_members(self, m: Dict[str, Any]) -> None:
        self._clear_layout(self._b_inner)
        mods = m.get("modules_by_type", []) or []
        if mods:
            tbl = self._table(
                ["타입", "폭 m", "길이 m", "면적 ㎡", "개수"],
                [[mt["name"], f"{mt['width_m']:.1f}", f"{mt['length_m']:.1f}",
                  f"{mt['area_m2']:.2f}", f"{mt['count']}"] for mt in mods],
            )
            self._b_inner.addWidget(tbl)
        else:
            self._b_inner.addWidget(_empty_label("배치된 모듈이 없습니다."))
        panels = m.get("panels", {}) or {}
        attached = m.get("attached", {}) or {}
        small = QLabel(
            f"바닥패널 {panels.get('floor_panel', 0)} · "
            f"구조벽 {panels.get('struct_wall', 0)} · "
            f"내벽 {panels.get('interior_wall', 0)} · "
            f"코어슬래브 {panels.get('core_slab', 0)}"
            "  ⎮  "
            f"캔틸레버보 {attached.get('cantilever_beam', 0)} · "
            f"캔틸레버슬래브 {attached.get('cantilever_slab', 0)} · "
            f"중간보 {attached.get('mid_beam', 0)} · "
            f"중간기둥 {attached.get('mid_column', 0)}"
        )
        small.setStyleSheet("color: #555; font-size: 11px; padding-top: 6px;")
        small.setWordWrap(True)
        self._b_inner.addWidget(small)

    # ── C. 물량·자재비 ───────────────────────────────────
    def _render_materials(self, mat: Dict[str, Any]) -> None:
        self._clear_layout(self._c_inner)
        if not mat or not mat.get("available"):
            self._c_policy_lbl.setText("정책: —")
            self._c_inner.addWidget(_empty_label(
                "물량 탭에서 단면 산정·자재비를 먼저 실행하세요."
            ))
            return
        policy = mat.get("current_policy", "—")
        self._c_policy_lbl.setText(f"정책: {policy}")

        # 그룹별 채택 단면 — 1줄 요약 라벨
        groups = mat.get("groups") or []
        if groups:
            gtxt = " · ".join(f"{g['group']}:{g['section']}" for g in groups)
            gl = QLabel(f"채택 단면 — {gtxt}")
            gl.setStyleSheet("color: #1F4E79; font-size: 11px; font-weight: 600;")
            gl.setWordWrap(True)
            self._c_inner.addWidget(gl)

        # 강재 본수표 (합계행 별도 추가)
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
            )
            self._c_inner.addWidget(tbl)
        else:
            self._c_inner.addWidget(_empty_label("강재 본수 데이터가 비어 있습니다."))

        # 슬래브 + 자재비 한 줄 요약
        slab = mat.get("slab") or {}
        cost = mat.get("cost") or {}
        slab_lbl = QLabel(
            f"슬래브 — 면적 {slab.get('total_area_m2', 0):,.1f} ㎡ · "
            f"부피 {slab.get('total_volume_m3', 0):,.2f} ㎥ · "
            f"두께 {slab.get('thickness_mm', 0):,.0f} mm"
        )
        slab_lbl.setStyleSheet("color: #555; font-size: 11px; padding-top: 4px;")
        self._c_inner.addWidget(slab_lbl)

        if cost:
            cost_lbl = QLabel(
                f"자재비 — 강재 {_won(cost.get('steel_cost', 0))} · "
                f"데크 {_won(cost.get('deck_cost', 0))} · "
                f"콘크리트 {_won(cost.get('concrete_cost', 0))}  ⎮  "
                f"<b>합계 {_won(cost.get('total_cost', 0))}</b>"
            )
            cost_lbl.setStyleSheet("color: #1F4E79; font-size: 12px; font-weight: 600;"
                                   " padding-top: 4px;")
            cost_lbl.setTextFormat(Qt.RichText)
            self._c_inner.addWidget(cost_lbl)
            if cost.get("has_missing_price"):
                miss = QLabel("⚠️ 일부 단가가 누락되어 자재비가 부정확할 수 있습니다.")
                miss.setStyleSheet("color: #C00000; font-size: 10px;")
                self._c_inner.addWidget(miss)

    # ── D. 운송 ──────────────────────────────────────────
    def _render_transport(self, t: Dict[str, Any]) -> None:
        self._clear_layout(self._d_inner)
        if not t or not t.get("available"):
            self._d_inner.addWidget(_empty_label(
                "운송 탭에서 [계산 실행] 을 먼저 누르세요."
            ))
            return
        avg_t = t.get("avg_load_kg", 0.0) / 1000.0
        pairs = [
            ("총 회차", f"{t.get('trips_total', 0):,} 회"),
            ("평균 적재", f"{avg_t:.1f} t  ({t.get('avg_utilization_pct', 0):.1f}%)"),
            ("운송 거리", f"{t.get('distance_km_total', 0.0):,.0f} km"),
            ("총 운송비", _won(t.get("total_cost_krw", 0.0))),
        ]
        for label, val in pairs:
            cell, _ = self._mk_head_cell(label, val, value_size=14)
            self._d_inner.addWidget(cell)

    # ── E. 공기 (표 형식) ────────────────────────────────
    def _render_schedule(self, s: Dict[str, Any]) -> None:
        self._clear_layout(self._e_inner)
        if not s or not s.get("available"):
            self._e_inner.addWidget(_empty_label(
                "공정표 탭에 한 번 진입해 자동 계산을 마치세요."
            ))
            return
        # 3 카드 한 줄
        row = QHBoxLayout()
        row.setSpacing(14)
        for label, val in (
            ("총 공기", f"{s.get('total_days', 0):,} 일"),
            ("코어 기간", f"{s.get('core_days', 0):,} 일"),
            ("모듈 설치", f"{s.get('module_install_days', 0):,} 일"),
        ):
            cell, _ = self._mk_head_cell(label, val, value_size=14)
            row.addWidget(cell)
        wrap = QWidget(); wrap.setLayout(row)
        self._e_inner.addWidget(wrap)
        # 공정 표 (공종명·기간·착수·완료)
        tasks = list(s.get("tasks", []) or [])
        if tasks:
            rows = [
                [str(t.get("name", "")),
                 f"{int(t.get('total', 0)):,} 일",
                 f"D+{int(t.get('start', 0))}",
                 f"D+{int(t.get('end', 0))}"]
                for t in sorted(tasks, key=lambda x: x.get("start", 0))
            ]
            tbl = self._table(
                ["공종", "기간", "착수", "완료"], rows,
            )
            self._e_inner.addWidget(tbl)

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
        self._cost_cells["total"].setText(_won(c.get("total_krw", 0)))

    # ── 보조 UI 빌더 ─────────────────────────────────────
    def _table(self, headers: List[str], rows: List[List[str]]) -> QTableWidget:
        tbl = QTableWidget(len(rows), len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setShowGrid(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.NoSelection)
        tbl.setStyleSheet(
            "QTableWidget { font-size: 11px; gridline-color: #DDE4ED; }"
            "QHeaderView::section { background: #1F4E79; color: white;"
            " padding: 4px; border: none; font-size: 11px; }"
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
        tbl.resizeColumnsToContents()
        h = tbl.horizontalHeader().height() + sum(
            tbl.rowHeight(r) for r in range(tbl.rowCount())
        ) + 2
        tbl.setMinimumHeight(min(h, 240))
        tbl.setMaximumHeight(280)
        return tbl

    def _mk_head_cell(self, label: str, value_text: str,
                       value_size: int = 22) -> tuple:
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        l1 = QLabel(label)
        l1.setStyleSheet("color: #666; font-size: 11px;")
        l2 = QLabel(value_text)
        l2.setStyleSheet(f"color: #1F4E79; font-size: {value_size}px; font-weight: 700;")
        v.addWidget(l1)
        v.addWidget(l2)
        return w, l2

    def _mk_cost_cell(self, label: str, value_text: str, emphasize: bool) -> tuple:
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        l1 = QLabel(label)
        l1.setStyleSheet("color: #666; font-size: 11px;")
        color = "#C00000" if emphasize else "#1F4E79"
        font_size = 22 if emphasize else 18
        l2 = QLabel(value_text)
        l2.setStyleSheet(f"color: {color}; font-size: {font_size}px; font-weight: 700;")
        v.addWidget(l1)
        v.addWidget(l2)
        return w, l2

    def _clear_layout(self, lay) -> None:
        while lay.count() > 0:
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
