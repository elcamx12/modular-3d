"""임시 화물 정의 모달 (풀스택, Phase 8 분석 ⑦ 결정 ⑦-1).

운송프로그램 원본 `app.py:_module_form` 패턴을 PyQt 로 이식. 사용자가 씬에
없는 가설 모듈/패널을 직접 정의해 수동 시뮬레이션 화물로 투입한다.

[지원 범위 — 1 단계]
- 모듈: 이름·치수(W/L/H)·기둥단면·보단면·extra_weight·수량
- 패널:
  - 순수 floor: 치수·두께·보단면·extra_weight
  - 독립 wall: + 기둥단면·벽높이
  - 종속 L자(floor + 벽 1면): + 1면 세그먼트(변/길이/높이/두께/기둥/보단면)
  - ㄷ자·3면·4면은 1 단계 제외 (분석 ⑦-7 결정)

[단면]
우리 SHS_CATALOG 전체를 콤보로 노출. 선택 단면은 to_transport_section 으로
운송 Section 변환. (임시 화물은 어댑터를 거치지 않으므로 SHS 외 형강은
미지원 — 분석 ⑦-7. 향후 직접 Section 입력 확장 가능.)

[반환]
exec_() == Accepted 후 built_items() → List[Module|Panel] (수량만큼 복제).
"""
from __future__ import annotations

from typing import List, Optional, Union

from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from modular_3d.카탈로그.steel_sections import SHS_CATALOG
from modular_3d.transport.adapter import to_transport_section
from modular_3d.transport.models import Module, Panel, WallSegment


Item = Union[Module, Panel]


def _make_section_combo() -> QComboBox:
    """SHS_CATALOG 전체를 담은 단면 선택 콤보. itemData = SHSSection."""
    cb = QComboBox()
    for s in SHS_CATALOG:
        cb.addItem(s.name, s)
    # 중간 단면을 기본값으로 (너무 작거나 큰 것 회피)
    if cb.count() > 0:
        cb.setCurrentIndex(min(cb.count() - 1, max(0, cb.count() // 2)))
    return cb


def _section_from_combo(cb: QComboBox):
    """콤보 선택 SHSSection → 운송 Section 변환."""
    shs = cb.currentData()
    return to_transport_section(shs)


class TempCargoDialog(QDialog):
    """임시 화물 정의 모달 — 모듈/패널 탭."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("임시 화물 정의")
        self.setMinimumWidth(460)
        self._built: List[Item] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_module_tab(), "모듈")
        self._tabs.addTab(self._build_panel_tab(), "패널")
        root.addWidget(self._tabs)

        self._err = QLabel("")
        self._err.setStyleSheet("color: #c0392b;")
        self._err.setWordWrap(True)
        root.addWidget(self._err)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("화물 추가")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── 모듈 탭 ────────────────────────────────────────────
    def _build_module_tab(self) -> QWidget:
        page = QWidget()
        lay = QFormLayout(page)
        self._m_name = QLineEdit("임시모듈")
        lay.addRow("이름:", self._m_name)
        self._m_width = self._spin(3000, 100, 100000)
        self._m_length = self._spin(6000, 100, 100000)
        self._m_height = self._spin(3000, 100, 100000)
        lay.addRow("폭(W):", self._m_width)
        lay.addRow("길이(L):", self._m_length)
        lay.addRow("높이(H):", self._m_height)
        self._m_col = _make_section_combo()
        self._m_beam = _make_section_combo()
        lay.addRow("기둥 단면:", self._m_col)
        lay.addRow("보 단면:", self._m_beam)
        self._m_extra = self._dspin(0, 0, 1_000_000, "kg")
        lay.addRow("추가 중량(슬래브 등):", self._m_extra)
        self._m_qty = self._spin(1, 1, 999)
        self._m_qty.setSuffix(" 개")
        lay.addRow("수량:", self._m_qty)
        return page

    # ── 패널 탭 ────────────────────────────────────────────
    def _build_panel_tab(self) -> QWidget:
        page = QWidget()
        lay = QFormLayout(page)
        self._p_name = QLineEdit("임시패널")
        lay.addRow("이름:", self._p_name)
        self._p_kind = QComboBox()
        self._p_kind.addItem("순수 바닥 (floor)", "floor")
        self._p_kind.addItem("독립 벽 (wall)", "wall")
        self._p_kind.addItem("L자 종속 (floor + 벽 1면)", "lshape_floor")
        self._p_kind.currentIndexChanged.connect(self._on_panel_kind_changed)
        lay.addRow("종류:", self._p_kind)

        self._p_width = self._spin(3000, 100, 100000)
        self._p_length = self._spin(6000, 100, 100000)
        self._p_thick = self._spin(150, 10, 5000)
        lay.addRow("폭(W):", self._p_width)
        lay.addRow("길이(L):", self._p_length)
        lay.addRow("두께(바닥/벽):", self._p_thick)
        self._p_beam = _make_section_combo()
        lay.addRow("보 단면:", self._p_beam)

        # wall 전용 그룹
        self._p_wall_box = QGroupBox("독립 벽 전용")
        wb = QFormLayout(self._p_wall_box)
        self._p_wall_col = _make_section_combo()
        self._p_wall_height = self._spin(2800, 100, 100000)
        wb.addRow("기둥 단면:", self._p_wall_col)
        wb.addRow("벽 높이:", self._p_wall_height)
        lay.addRow(self._p_wall_box)

        # L자 세그먼트 그룹
        self._p_seg_box = QGroupBox("L자 종속 벽 세그먼트 (1면)")
        sb = QFormLayout(self._p_seg_box)
        self._seg_side = QComboBox()
        for v, t in [(0, "0 하변"), (1, "1 우변"), (2, "2 상변"), (3, "3 좌변")]:
            self._seg_side.addItem(t, v)
        self._seg_offset = self._spin(0, 0, 100000)
        self._seg_length = self._spin(3000, 10, 100000)
        self._seg_height = self._spin(2800, 10, 100000)
        self._seg_thick = self._spin(150, 10, 5000)
        self._seg_col = _make_section_combo()
        self._seg_beam = _make_section_combo()
        sb.addRow("변(side):", self._seg_side)
        sb.addRow("시작 오프셋:", self._seg_offset)
        sb.addRow("세그먼트 길이:", self._seg_length)
        sb.addRow("벽 높이:", self._seg_height)
        sb.addRow("벽 두께:", self._seg_thick)
        sb.addRow("기둥 단면:", self._seg_col)
        sb.addRow("상단 보 단면:", self._seg_beam)
        lay.addRow(self._p_seg_box)

        self._p_extra = self._dspin(0, 0, 1_000_000, "kg")
        lay.addRow("추가 중량:", self._p_extra)
        self._p_qty = self._spin(1, 1, 999)
        self._p_qty.setSuffix(" 개")
        lay.addRow("수량:", self._p_qty)

        self._on_panel_kind_changed()  # 초기 가시성
        return page

    def _on_panel_kind_changed(self) -> None:
        kind = self._p_kind.currentData()
        self._p_wall_box.setVisible(kind == "wall")
        self._p_seg_box.setVisible(kind == "lshape_floor")

    # ── 위젯 헬퍼 ──────────────────────────────────────────
    def _spin(self, val: int, lo: int, hi: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setSuffix(" mm")
        return s

    def _dspin(self, val: float, lo: float, hi: float, suffix: str) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setDecimals(1)
        s.setSingleStep(10.0)
        s.setSuffix(" " + suffix)
        return s

    # ── 빌드 ──────────────────────────────────────────────
    def _on_accept(self) -> None:
        try:
            if self._tabs.currentIndex() == 0:
                items = self._build_modules()
            else:
                items = self._build_panels()
        except ValueError as e:
            self._err.setText(str(e))
            return
        except Exception as e:
            self._err.setText(f"{type(e).__name__}: {e}")
            return
        if not items:
            self._err.setText("수량이 0 입니다.")
            return
        self._built = items
        self.accept()

    def _build_modules(self) -> List[Item]:
        base_name = self._m_name.text().strip() or "임시모듈"
        qty = self._m_qty.value()
        col = _section_from_combo(self._m_col)
        beam = _section_from_combo(self._m_beam)
        out: List[Item] = []
        for i in range(qty):
            name = base_name if qty == 1 else f"{base_name}#{i + 1}"
            out.append(Module(
                name=name,
                width=float(self._m_width.value()),
                length=float(self._m_length.value()),
                height=float(self._m_height.value()),
                column_section=col,
                beam_section=beam,
                extra_weight_kg=float(self._m_extra.value()),
            ))
        return out

    def _build_panels(self) -> List[Item]:
        base_name = self._p_name.text().strip() or "임시패널"
        qty = self._p_qty.value()
        kind = self._p_kind.currentData()
        beam = _section_from_combo(self._p_beam)
        w = float(self._p_width.value())
        ln = float(self._p_length.value())
        th = float(self._p_thick.value())
        extra = float(self._p_extra.value())

        def _one(name: str) -> Panel:
            if kind == "wall":
                return Panel(
                    name=name, kind="wall", width=w, length=ln, thickness=th,
                    beam_section=beam,
                    column_section=_section_from_combo(self._p_wall_col),
                    wall_height=float(self._p_wall_height.value()),
                    extra_weight_kg=extra,
                )
            if kind == "lshape_floor":
                seg = WallSegment(
                    side=self._seg_side.currentData(),
                    start_offset_mm=float(self._seg_offset.value()),
                    length_mm=float(self._seg_length.value()),
                    height_mm=float(self._seg_height.value()),
                    thickness_mm=float(self._seg_thick.value()),
                    column_section=_section_from_combo(self._seg_col),
                    beam_section=_section_from_combo(self._seg_beam),
                )
                return Panel(
                    name=name, kind="floor", width=w, length=ln, thickness=th,
                    beam_section=beam, extra_weight_kg=extra,
                    wall_segments=(seg,),
                )
            # 순수 floor
            return Panel(
                name=name, kind="floor", width=w, length=ln, thickness=th,
                beam_section=beam, extra_weight_kg=extra,
            )

        out: List[Item] = []
        for i in range(qty):
            nm = base_name if qty == 1 else f"{base_name}#{i + 1}"
            out.append(_one(nm))
        return out

    def built_items(self) -> List[Item]:
        return self._built


__all__ = ["TempCargoDialog"]
