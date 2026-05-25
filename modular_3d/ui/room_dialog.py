"""실(Room) 용도 선택 다이얼로그.

[정책 2026-05-24 디자인 탭 2분리 — 2단계 실 배치]
- 폴리곤을 닫은 직후 호출 — 용도(KDS 41 12 00 연계 표준 목록)만 고른다.
  별도 이름은 두지 않는다(용도가 곧 구분).
- 용도 드롭다운은 카탈로그/room_types.ROOM_TYPES 단일 출처를 사용.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDialogButtonBox,
)

from modular_3d.카탈로그.room_types import (
    ROOM_TYPES, DEFAULT_ROOM_TYPE_KEY, get_room_type,
)


class RoomInfoDialog(QDialog):
    """실 용도 선택."""

    def __init__(self, parent=None, room_type_key: str = DEFAULT_ROOM_TYPE_KEY):
        super().__init__(parent)
        self.setWindowTitle("실 용도")
        self.setMinimumWidth(320)
        v = QVBoxLayout(self)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("용도:"))
        self._type_combo = QComboBox()
        for rt in ROOM_TYPES:
            self._type_combo.addItem(f"{rt.name}  (활하중 {rt.live_load_kn_m2:g} kN/㎡)",
                                     rt.key)
        sel_idx = 0
        for i, rt in enumerate(ROOM_TYPES):
            if rt.key == room_type_key:
                sel_idx = i
                break
        self._type_combo.setCurrentIndex(sel_idx)
        type_row.addWidget(self._type_combo, stretch=1)
        v.addLayout(type_row)

        # KDS 근거 안내
        self._hint = QLabel("")
        self._hint.setStyleSheet("color:#666; font-size:10px;")
        self._hint.setWordWrap(True)
        v.addWidget(self._hint)
        self._type_combo.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _update_hint(self):
        rt = get_room_type(self._type_combo.currentData())
        if rt is not None:
            self._hint.setText(
                f"KDS 41 12 00 분류: {rt.kds_category} · 등분포활하중 "
                f"{rt.live_load_kn_m2:g} kN/㎡ (5단계 하중 정의에서 적용)")

    def result_value(self) -> str:
        """선택한 용도 key 반환."""
        return self._type_combo.currentData()


def ask_room_type(parent=None,
                  room_type_key: str = DEFAULT_ROOM_TYPE_KEY) -> Optional[str]:
    """모달 다이얼로그 — 용도 key 또는 취소 시 None."""
    dlg = RoomInfoDialog(parent, room_type_key)
    if dlg.exec_() == QDialog.Accepted:
        return dlg.result_value()
    return None


__all__ = ["RoomInfoDialog", "ask_room_type"]
