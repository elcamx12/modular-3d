"""트럭 카탈로그 관리 다이얼로그 (Phase 7).

[목적]
운송탭 ②번 도로 콤보·트럭 후보 자동선택과 별도로, 사용자가 직접 트럭
카탈로그를 보고/추가/편집/삭제할 수 있는 별도 QDialog.

[2 계층 정책 — 사용자 결정 ⑨-5]
- 🔒 내장(builtin) 트럭: 읽기 전용. 편집/삭제 불가. 복제만 허용.
- 📁 프로젝트(project) 트럭: 사용자가 추가/편집/삭제 가능.
- 같은 name 의 항목은 프로젝트가 내장을 덮어쓴다 (catalog_io 머지 정책).

[clone-on-edit — 사용자 결정 ⑨-4]
내장 트럭을 [편집] 하려 하면 새 이름을 강제하고 프로젝트 트럭으로 신규 저장.
원본 내장 항목은 절대 변경되지 않는다.

[C-2 결정]
도로 한도(RoadClass) 는 UI 편집 차단. 본 다이얼로그는 트럭 탭만 제공.

[검증 (B-15)]
폼 입력값 → `validate_truck` 또는 `Truck(**data)` 호출 시 ValueError 메시지
사용자에게 직접 표시. 저장 버튼은 검증 실패 시 비활성화.

[저장 위치]
프로젝트 트럭은 `<project_root>/transport_config/trucks.json` 에 저장.
project_root 인자는 호출 측(TransportTab) 에서 전달.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from modular_3d.transport.catalog_io import (
    load_all_trucks, load_builtin_trucks, load_project_trucks,
    save_project_truck, validate_truck,
)
from modular_3d.transport.models import Truck, _TRUCK_TYPES


# 트럭 표 컬럼 정의 (출처/이름/종류/적재한도/활성)
_HEADERS = [
    "출처", "이름", "종류",
    "최대길이(mm)", "최대폭(mm)", "최대높이(mm)", "최대중량(kg)",
    "차체중량(kg)", "트레일러길이(mm)",
    "활성",
]


class _TruckEditDialog(QDialog):
    """단일 트럭 추가/편집 모달.

    내장 트럭 편집 모드(clone) 인 경우 name 입력란을 원본과 달라야 OK 로
    바꾸도록 검증. 신규 추가 모드도 같은 폼 재사용.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        initial: Optional[Truck],
        existing_names: set[str],
        clone_mode: bool,
    ) -> None:
        super().__init__(parent)
        self._initial = initial
        self._existing_names = existing_names
        self._clone_mode = clone_mode
        self.setWindowTitle(
            "트럭 복제 (내장 트럭은 복제만 가능)" if clone_mode
            else ("트럭 편집" if initial else "트럭 추가")
        )
        self.setMinimumWidth(420)
        self._build_ui()
        if initial:
            self._fill_from_truck(initial, force_blank_name=clone_mode)

    def _build_ui(self) -> None:
        lay = QFormLayout(self)
        self._name_edit = QLineEdit()
        lay.addRow("이름:", self._name_edit)

        self._type_combo = QComboBox()
        for t in sorted(_TRUCK_TYPES):
            self._type_combo.addItem(t)
        lay.addRow("종류:", self._type_combo)

        def _mk_spin(maxv: int, suffix: str) -> QSpinBox:
            s = QSpinBox(); s.setRange(0, maxv); s.setSuffix(" " + suffix)
            return s

        def _mk_dspin(maxv: float, suffix: str) -> QDoubleSpinBox:
            s = QDoubleSpinBox(); s.setRange(0, maxv); s.setSuffix(" " + suffix)
            s.setDecimals(1); s.setSingleStep(10.0)
            return s

        self._max_length = _mk_spin(999_999, "mm")
        self._max_length.setValue(13000)
        self._max_width = _mk_spin(99_999, "mm"); self._max_width.setValue(2500)
        self._max_height = _mk_spin(99_999, "mm"); self._max_height.setValue(3000)
        self._max_weight = _mk_dspin(200_000, "kg"); self._max_weight.setValue(25000)
        self._curb_weight = _mk_dspin(200_000, "kg")
        self._trailer_length = _mk_spin(999_999, "mm")
        self._height_offset = _mk_dspin(5000, "mm"); self._height_offset.setValue(700)
        self._active_cb = QCheckBox("패킹 후보로 사용 (active)")
        self._active_cb.setChecked(True)
        self._note_edit = QLineEdit()

        lay.addRow("최대 길이:", self._max_length)
        lay.addRow("최대 폭:", self._max_width)
        lay.addRow("최대 높이:", self._max_height)
        lay.addRow("최대 중량:", self._max_weight)
        lay.addRow("차체 자중 (curb):", self._curb_weight)
        lay.addRow("트레일러 길이:", self._trailer_length)
        lay.addRow("차체 높이 오프셋:", self._height_offset)
        lay.addRow(self._active_cb)
        lay.addRow("비고:", self._note_edit)

        # 검증 메시지 라벨 (B-15)
        self._err_label = QLabel("")
        self._err_label.setStyleSheet("color: #c0392b;")
        self._err_label.setWordWrap(True)
        lay.addRow(self._err_label)

        # 버튼
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)
        self._save_btn = btns.button(QDialogButtonBox.Save)

    def _fill_from_truck(self, t: Truck, force_blank_name: bool) -> None:
        self._name_edit.setText("" if force_blank_name else t.name)
        if force_blank_name:
            self._name_edit.setPlaceholderText(f"(원본: {t.name} — 새 이름 입력)")
        idx = self._type_combo.findText(t.truck_type)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        self._max_length.setValue(int(t.max_length))
        self._max_width.setValue(int(t.max_width))
        self._max_height.setValue(int(t.max_height))
        self._max_weight.setValue(float(t.max_weight))
        self._curb_weight.setValue(float(t.curb_weight_kg))
        self._trailer_length.setValue(int(t.trailer_length_mm))
        self._height_offset.setValue(float(t.vehicle_height_offset))
        self._active_cb.setChecked(bool(t.active))
        self._note_edit.setText(t.note or "")

    def _to_dict(self) -> dict:
        return {
            "name": self._name_edit.text().strip(),
            "truck_type": self._type_combo.currentText(),
            "max_length": float(self._max_length.value()),
            "max_width": float(self._max_width.value()),
            "max_height": float(self._max_height.value()),
            "max_weight": float(self._max_weight.value()),
            "vehicle_height_offset": float(self._height_offset.value()),
            "curb_weight_kg": float(self._curb_weight.value()),
            "trailer_length_mm": float(self._trailer_length.value()),
            "active": bool(self._active_cb.isChecked()),
            "note": self._note_edit.text().strip(),
        }

    def _on_accept(self) -> None:
        data = self._to_dict()
        # 이름 비검사
        if not data["name"]:
            self._err_label.setText("이름은 필수입니다.")
            return
        # 복제 모드 — 원본과 다른 이름이어야 함
        if self._clone_mode and self._initial and data["name"] == self._initial.name:
            self._err_label.setText(
                f"내장 트럭 '{self._initial.name}' 와 다른 이름을 입력하세요."
            )
            return
        # 중복 이름 검사 (편집 모드에서 자기 자신은 허용)
        if self._initial and not self._clone_mode:
            forbidden = self._existing_names - {self._initial.name}
        else:
            forbidden = self._existing_names
        if data["name"] in forbidden:
            self._err_label.setText(
                f"이미 존재하는 이름입니다: '{data['name']}'"
            )
            return
        # 데이터클래스 검증 (B-15 런타임 검증 활용)
        errs = validate_truck(data)
        if errs:
            self._err_label.setText("\n".join(errs))
            return
        self._result_truck = Truck(**data)
        self.accept()

    def result_truck(self) -> Optional[Truck]:
        return getattr(self, "_result_truck", None)


class TransportCatalogDialog(QDialog):
    """트럭 카탈로그 관리 다이얼로그 (메인).

    [발신 시그널]
    - catalog_changed: 트럭 추가/편집/복제/삭제 후 발화. TransportTab 이
      받아서 자기 _trucks 재로드 + 회차별 override 콤보 갱신.
    """

    catalog_changed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        project_root: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self.setWindowTitle("트럭 카탈로그 관리")
        self.resize(900, 480)
        self._build_ui()
        self._reload_table()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        info = QLabel(
            "🔒 내장 트럭은 읽기 전용 — [복제] 로 사용자 트럭 생성 가능.  "
            "📁 프로젝트 트럭은 편집·삭제 가능.  "
            "도로 한도는 코드 JSON 만 — UI 편집 불가."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #555; padding: 4px;")
        root.addWidget(info)

        self._table = QTableWidget()
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._table, stretch=1)

        # 버튼 줄
        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("➕ 추가")
        self._edit_btn = QPushButton("✎ 편집")
        self._clone_btn = QPushButton("⎘ 복제")
        self._del_btn = QPushButton("🗑 삭제")
        close_btn = QPushButton("닫기")
        self._add_btn.clicked.connect(self._on_add)
        self._edit_btn.clicked.connect(self._on_edit)
        self._clone_btn.clicked.connect(self._on_clone)
        self._del_btn.clicked.connect(self._on_delete)
        close_btn.clicked.connect(self.accept)
        for b in (self._add_btn, self._edit_btn, self._clone_btn, self._del_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
        self._update_buttons_enabled()

    # ── 표 로드 ────────────────────────────────────────────
    def _reload_table(self) -> None:
        builtin_names = {t.name for t in load_builtin_trucks()}
        project_names = {t.name for t in load_project_trucks(self._project_root)}
        all_trucks = load_all_trucks(self._project_root)
        # 정렬: 출처(프로젝트가 우선 표시) → 이름
        def _src(name: str) -> int:
            return 0 if name in project_names else 1
        all_trucks.sort(key=lambda t: (_src(t.name), t.name))

        self._table.setRowCount(len(all_trucks))
        self._row_truck: list[tuple[Truck, str]] = []  # (truck, source)
        for row, t in enumerate(all_trucks):
            src = "📁 프로젝트" if t.name in project_names else "🔒 내장"
            self._row_truck.append((t, src))
            vals = [
                src, t.name, t.truck_type,
                f"{int(t.max_length):,}", f"{int(t.max_width):,}",
                f"{int(t.max_height):,}", f"{int(t.max_weight):,}",
                f"{int(t.curb_weight_kg):,}", f"{int(t.trailer_length_mm):,}",
                "✓" if t.active else "—",
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if src.startswith("🔒"):
                    item.setForeground(QBrush(QColor("#7a7a7a")))
                if col >= 3 and col <= 8:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._table.setItem(row, col, item)
        self._update_buttons_enabled()

    def _selected_row(self) -> int:
        rows = self._table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _selected(self) -> Optional[tuple[Truck, str]]:
        r = self._selected_row()
        if r < 0 or r >= len(self._row_truck):
            return None
        return self._row_truck[r]

    def _on_selection_changed(self) -> None:
        self._update_buttons_enabled()

    def _update_buttons_enabled(self) -> None:
        sel = self._selected()
        has_sel = sel is not None
        is_project = has_sel and sel[1].startswith("📁")
        self._edit_btn.setEnabled(has_sel and is_project)
        self._clone_btn.setEnabled(has_sel)
        self._del_btn.setEnabled(has_sel and is_project)

    # ── 액션 ──────────────────────────────────────────────
    def _existing_names(self) -> set[str]:
        return {t.name for t, _ in self._row_truck}

    def _on_add(self) -> None:
        dlg = _TruckEditDialog(self, initial=None,
                               existing_names=self._existing_names(),
                               clone_mode=False)
        if dlg.exec_() == QDialog.Accepted:
            t = dlg.result_truck()
            if t is None:
                return
            try:
                path = save_project_truck(t, self._project_root)
            except Exception as e:
                QMessageBox.critical(self, "저장 실패", str(e))
                return
            self._reload_table()
            self.catalog_changed.emit()
            self._info_saved(t.name, path)

    def _on_edit(self) -> None:
        sel = self._selected()
        if not sel:
            return
        t, src = sel
        if not src.startswith("📁"):
            QMessageBox.warning(
                self, "편집 불가",
                "내장 트럭은 편집할 수 없습니다. [복제] 로 사용자 트럭을 만드세요."
            )
            return
        dlg = _TruckEditDialog(self, initial=t,
                               existing_names=self._existing_names(),
                               clone_mode=False)
        if dlg.exec_() == QDialog.Accepted:
            new_t = dlg.result_truck()
            if new_t is None:
                return
            try:
                # 이름이 바뀌었으면 옛 항목은 그대로 둠 (수동 삭제 필요)
                # — 사용자에게 의도 확인
                if new_t.name != t.name:
                    rc = QMessageBox.question(
                        self, "이름 변경 확인",
                        f"이름을 '{t.name}' → '{new_t.name}' 으로 변경합니다.\n"
                        f"이전 이름의 항목을 삭제하시겠습니까?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    save_project_truck(new_t, self._project_root)
                    if rc == QMessageBox.Yes:
                        self._delete_project_truck(t.name)
                else:
                    save_project_truck(new_t, self._project_root)
            except Exception as e:
                QMessageBox.critical(self, "저장 실패", str(e))
                return
            self._reload_table()
            self.catalog_changed.emit()

    def _on_clone(self) -> None:
        sel = self._selected()
        if not sel:
            return
        t, _src = sel
        dlg = _TruckEditDialog(self, initial=t,
                               existing_names=self._existing_names(),
                               clone_mode=True)
        if dlg.exec_() == QDialog.Accepted:
            new_t = dlg.result_truck()
            if new_t is None:
                return
            try:
                path = save_project_truck(new_t, self._project_root)
            except Exception as e:
                QMessageBox.critical(self, "저장 실패", str(e))
                return
            self._reload_table()
            self.catalog_changed.emit()
            self._info_saved(new_t.name, path)

    def _on_delete(self) -> None:
        sel = self._selected()
        if not sel:
            return
        t, src = sel
        if not src.startswith("📁"):
            return
        rc = QMessageBox.question(
            self, "삭제 확인",
            f"프로젝트 트럭 '{t.name}' 을 삭제합니다. 계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if rc != QMessageBox.Yes:
            return
        try:
            self._delete_project_truck(t.name)
        except Exception as e:
            QMessageBox.critical(self, "삭제 실패", str(e))
            return
        self._reload_table()
        self.catalog_changed.emit()

    def _delete_project_truck(self, name: str) -> None:
        """프로젝트 trucks.json 에서 단일 트럭 삭제 (catalog_io 에 없어 인라인)."""
        import json
        base = Path(self._project_root) if self._project_root else Path.cwd()
        p = base / "transport_config" / "trucks.json"
        if not p.exists():
            return
        with p.open("r", encoding="utf-8") as f:
            existing = json.load(f)
        new_list = [item for item in existing if item.get("name") != name]
        with p.open("w", encoding="utf-8") as f:
            json.dump(new_list, f, ensure_ascii=False, indent=2)

    def _info_saved(self, name: str, path: Path) -> None:
        QMessageBox.information(
            self, "저장 완료",
            f"트럭 '{name}' 이(가) 저장되었습니다.\n경로: {path}"
        )


__all__ = ["TransportCatalogDialog"]
