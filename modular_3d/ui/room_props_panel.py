"""우측 실(Room) 속성 패널 — 선택한 실의 용도·하중 편집 + 삭제.

[정책 2026-05-24 디자인 탭 2분리 — 2단계 실 배치 보강]
- 실 모드에서 실을 클릭해 선택하면 이 패널에 용도·활하중·부가하중이 표시되고,
  수정/삭제할 수 있다.
- 용도는 카탈로그/room_types.ROOM_TYPES 단일 출처. 변경 시 색·활하중·부가하중
  기본값이 따라 바뀐다.

[5단계 하중 정의]
- 활하중(kN/㎡)·부가 고정하중 SDL(kN/㎡) 입력 칸 추가. 용도를 바꾸면 그 용도의
  KDS/표준 기본값이 자동으로 채워지고, 사용자가 값을 바꾸면 그 값이 override 로
  저장된다(용도 기본값과 같으면 override 해제).
"""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QGroupBox, QLineEdit,
)

from modular_3d.카탈로그.room_types import ROOM_TYPES, get_room_type


class RoomPropertiesPanel(QWidget):
    """선택 실 편집 — 용도 + 활하중/부가하중 + 적용/삭제."""

    # (room_id, room_type_key, live_load, sdl) — 하중이 음수면 '용도 기본값(=override 해제)'.
    room_edited = pyqtSignal(int, str, float, float)
    # room_id
    room_delete_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._room_id = -1
        self._loading = False   # set_room/용도변경 중 자동채움 시 신호 억제
        self._build_ui()
        self.clear()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        box = QGroupBox("실 속성")
        bv = QVBoxLayout(box)

        # 용도
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("용도:"))
        self._type_combo = QComboBox()
        for rt in ROOM_TYPES:
            self._type_combo.addItem(rt.name, rt.key)
        type_row.addWidget(self._type_combo, stretch=1)
        bv.addLayout(type_row)

        # 활하중 (kN/㎡)
        live_row = QHBoxLayout()
        live_row.addWidget(QLabel("활하중:"))
        self._live_edit = QLineEdit()
        self._live_edit.setValidator(QDoubleValidator(0.0, 100.0, 2))
        live_row.addWidget(self._live_edit, stretch=1)
        live_row.addWidget(QLabel("kN/㎡"))
        bv.addLayout(live_row)

        # 부가 고정하중 SDL (kN/㎡)
        sdl_row = QHBoxLayout()
        sdl_row.addWidget(QLabel("부가하중:"))
        self._sdl_edit = QLineEdit()
        self._sdl_edit.setValidator(QDoubleValidator(0.0, 100.0, 2))
        sdl_row.addWidget(self._sdl_edit, stretch=1)
        sdl_row.addWidget(QLabel("kN/㎡"))
        bv.addLayout(sdl_row)

        # 분류 안내(읽기)
        self._info = QLabel("")
        self._info.setStyleSheet("color:#666; font-size:10px;")
        self._info.setWordWrap(True)
        bv.addWidget(self._info)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)

        # 버튼
        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("적용")
        self._del_btn = QPushButton("삭제")
        self._apply_btn.clicked.connect(self._on_apply)
        self._del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(self._del_btn)
        bv.addLayout(btn_row)

        self._empty = QLabel("실 모드에서 실을 클릭해 선택하세요.")
        self._empty.setStyleSheet("color:#999;")
        self._empty.setWordWrap(True)
        bv.addWidget(self._empty)

        v.addWidget(box)
        v.addStretch(1)

    def _on_type_changed(self):
        """용도 변경 → 그 용도 기본 활하중/부가하중으로 칸 자동 채움 + 안내 갱신."""
        rt = get_room_type(self._type_combo.currentData())
        if rt is not None:
            if not self._loading:
                # 사용자가 용도를 바꾸면 그 용도 기본값으로 리셋(override 해제 효과).
                self._live_edit.setText(f"{rt.live_load_kn_m2:g}")
                self._sdl_edit.setText(f"{rt.sdl_kn_m2:g}")
            self._info.setText(
                f"KDS 41 12 00: {rt.kds_category} · 기본 활하중 "
                f"{rt.live_load_kn_m2:g} · 기본 부가하중 {rt.sdl_kn_m2:g} kN/㎡")
        else:
            self._info.setText("")

    def set_room(self, room):
        """선택 실 정보를 채운다."""
        if room is None:
            self.clear()
            return
        self._loading = True
        self._room_id = int(getattr(room, 'id', -1))
        key = getattr(room, 'room_type', '')
        idx = self._type_combo.findData(key)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        # 유효 하중(override 있으면 그 값, 없으면 용도 기본값)으로 칸 채움.
        try:
            self._live_edit.setText(f"{room.effective_live_load():g}")
            self._sdl_edit.setText(f"{room.effective_sdl():g}")
        except Exception:
            rt = get_room_type(key)
            if rt is not None:
                self._live_edit.setText(f"{rt.live_load_kn_m2:g}")
                self._sdl_edit.setText(f"{rt.sdl_kn_m2:g}")
        self._loading = False
        self._on_type_changed()   # 안내문만 갱신(_loading=False 지만 칸은 위에서 채움)
        self._set_editing(True)

    def clear(self):
        """선택 없음 상태."""
        self._room_id = -1
        self._set_editing(False)

    def _set_editing(self, on: bool):
        for w in (self._type_combo, self._live_edit, self._sdl_edit,
                  self._apply_btn, self._del_btn, self._info):
            w.setVisible(on)
        self._empty.setVisible(not on)

    def _parse(self, edit) -> float:
        try:
            return float(edit.text())
        except (ValueError, TypeError):
            return -1.0

    def _on_apply(self):
        if self._room_id <= 0:
            return
        key = self._type_combo.currentData()
        rt = get_room_type(key)
        live = self._parse(self._live_edit)
        sdl = self._parse(self._sdl_edit)
        # 용도 기본값과 같으면 override 해제(-1 전달). 다르면 그 값.
        if rt is not None:
            if live < 0 or abs(live - rt.live_load_kn_m2) < 1e-9:
                live = -1.0
            if sdl < 0 or abs(sdl - rt.sdl_kn_m2) < 1e-9:
                sdl = -1.0
        self.room_edited.emit(self._room_id, key, float(live), float(sdl))

    def _on_delete(self):
        if self._room_id > 0:
            self.room_delete_requested.emit(self._room_id)


__all__ = ["RoomPropertiesPanel"]
