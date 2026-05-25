"""개구부 크기 입력 다이얼로그.

[정책 2026-05-24 디자인 탭 2분리 — 3단계 개구부]
- 문/창 종류 구분 없이 '크기만' 입력(사용자 결정).
- 대상(벽/슬래브)을 고스트로 올려놓기 전에 크기를 먼저 받으므로 종류 무관 통일
  필드: 가로·세로 + 바닥높이(벽일 때만 의미, 슬래브는 무시).
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QDialogButtonBox,
)


class OpeningSizeDialog(QDialog):
    """개구부 크기 입력 — 가로·세로 + 바닥높이(벽 전용)."""

    def __init__(self, parent=None, w=900, h=1200, sill=900):
        super().__init__(parent)
        self.setWindowTitle("개구부 크기")
        self.setMinimumWidth(300)
        v = QVBoxLayout(self)

        def _spin(lo, hi, val, step=50):
            s = QSpinBox()
            s.setRange(lo, hi); s.setSingleStep(step); s.setValue(int(val))
            s.setSuffix(" mm")
            return s

        self._w = _spin(100, 20000, w)
        self._h = _spin(100, 20000, h)
        self._sill = _spin(0, 5000, sill)
        for label, widget in (("가로:", self._w), ("세로:", self._h),
                              ("바닥높이(벽 전용):", self._sill)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(widget, stretch=1)
            v.addLayout(row)

        hint = QLabel("크기 확정 후 고스트가 마우스를 따라갑니다. "
                      "벽=세로는 높이, 바닥높이는 개구부 하단 z. "
                      "슬래브=가로×세로 평면, 바닥높이 무시.")
        hint.setStyleSheet("color:#666; font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def result_size(self) -> dict:
        return {'w': float(self._w.value()), 'h': float(self._h.value()),
                'sill': float(self._sill.value())}


def ask_opening_size(parent=None, w=900, h=1200, sill=900) -> Optional[dict]:
    """모달 — {'w','h','sill'} 또는 취소 시 None."""
    dlg = OpeningSizeDialog(parent, w, h, sill)
    if dlg.exec_() == QDialog.Accepted:
        return dlg.result_size()
    return None


__all__ = ["OpeningSizeDialog", "ask_opening_size"]
