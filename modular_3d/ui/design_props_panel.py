"""우측 디자인 속성 패널 — 활성/선택 부재 정보.

[현재 구현]
- 활성 부재 라벨 (팔레트에서 선택한 부재)
- 선택 부재 정보 라벨 (씬에서 클릭 선택한 부재)

[2026-05-24] AI 최적배치 입력 폼 제거 — AI 최적배치 기능은 만들지 않는다.

회전·앵커 콤보박스는 만들지 않는다 (사용자 결정 — R/V 키 기존 동작 유지).

[이력 2026-05-13] 접합부 변경 모드 / 합성거동 옵션은
`ui/joint_edit_panel.py` 의 JointEditPanel 로 이동하여, 새 '접합부 조정' 탭의
우측 패널에 마운트됨.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QComboBox,
)

from modular_3d.model import ComponentType, Scene, effective_beam_section_type


# 부재 타입별 한글 이름 (single source of truth)
from modular_3d.model import TYPE_NAMES


# 보 단면 타입 직접 선택 가능 부재 / 종속(부모 따라감) / 보 없음.
_DIRECT_BEAM_TYPES = {
    ComponentType.MODULE, ComponentType.FLOOR_PANEL, ComponentType.VERTICAL_MODULE,
}
_DEP_BEAM_TYPES = {
    ComponentType.CANTILEVER_BEAM, ComponentType.CANTILEVER_SLAB, ComponentType.MID_BEAM,
}
_NO_BEAM_TYPES = {
    ComponentType.MID_COLUMN, ComponentType.CORE, ComponentType.CORE_SLAB,
    ComponentType.INTERIOR_WALL,
}


class DesignPropertiesPanel(QWidget):
    """디자인 탭 우측 패널 — 활성/선택 부재 정보 + 보 단면 타입 선택."""

    # (comp_id, 'shs'|'h') — 선택 부재의 보 단면 타입 변경 요청.
    beam_section_changed = pyqtSignal(int, str)

    def __init__(self, scene: Scene, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._scene = scene
        self._sel_id = -1
        self._loading = False
        self._setup_ui()
        self.refresh_active(None)
        self.refresh_selected(-1)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        title = QLabel('속성 패널')
        title.setStyleSheet('font-weight: bold; font-size: 12px;')
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)

        # ── 선택 부재 정보 ([E2] 활성 부재 표시 제거 — 선택 부재만) ─────
        self._selected_label = QLabel('선택 부재: (없음)')
        self._selected_label.setStyleSheet('font-size: 11px;')
        self._selected_label.setWordWrap(True)
        self._selected_label.setMinimumWidth(0)
        lay.addWidget(self._selected_label)

        # ── 보 단면 타입 (각형강관 / H형강) ─────────────
        self._sec_row = QWidget()
        sec_lay = QHBoxLayout(self._sec_row)
        sec_lay.setContentsMargins(0, 0, 0, 0)
        self._sec_caption = QLabel('보 단면:')
        self._sec_caption.setStyleSheet('font-size: 11px;')
        sec_lay.addWidget(self._sec_caption)
        self._sec_combo = QComboBox()
        self._sec_combo.addItem('각형강관', 'shs')
        self._sec_combo.addItem('H형강', 'h')
        self._sec_combo.currentIndexChanged.connect(self._on_sec_changed)
        sec_lay.addWidget(self._sec_combo, stretch=1)
        lay.addWidget(self._sec_row)
        self._sec_row.setVisible(False)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        lay.addWidget(sep2)

        # ── 해석 좌표 안내 ─────────────────────────────
        # 모듈 외관 z = 0 ~ 3400 mm 이지만 해석 모델의 보 중심선은
        # z = 0 ~ 3200 (=h - SECTION_W=200) 위치. 사용자가 코어 슬래브 z 와
        # 보 격자 끝 z 차이를 헷갈리지 않도록 한 줄로 안내.
        _z_note = QLabel('해석 좌표: 보 중심선 z = h − 200 mm '
                         '(예: 모듈 천장보 = z 3200, 외관 = z 3400)')
        _z_note.setStyleSheet('color: #666; font-size: 10px;')
        _z_note.setWordWrap(True)
        lay.addWidget(_z_note)

        # (2026-05-13) 코어 CAD 입력 섹션 제거 — 코어벽 만드는 기능(팔레트) +
        # 코어 슬래브 생성 버튼이 별도 제공되므로 본 자리 불필요.

        # [2026-05-13 접합부조정탭 Phase 3] 접합부 변경 토글 + 합성거동 옵션은
        # 새 '접합부 조정' 탭의 JointEditPanel 로 이동. 본 패널에선 제거.

        # [2026-05-24] AI 최적배치 실행 입력 제거 — AI 최적배치는 만들지 않는다.
        # (층수는 F5 패널 상단 SpinBox 가 단일 컨트롤이므로 본 패널 불필요.)

        lay.addStretch(1)

    # ── 외부 API ─────────────────────────────────────

    def refresh_active(self, comp_type: Optional[ComponentType]):
        """[E2] 활성 부재 표시 제거 — 선택 부재만 표시하므로 동작 없음."""
        return

    def refresh_selected(self, comp_id: int):
        """선택 부재 변경 시 호출 — 라벨 + 보 단면 타입 콤보 갱신.

        [E3·E4] ID 미표기. 타입은 모듈A-1 체계(독립부재) 또는 타입명(종속/기타),
        위치 + 부재/컴포넌트 크기(width×depth×height) 표기.
        """
        self._sel_id = int(comp_id)
        if comp_id <= 0 or comp_id not in self._scene.components:
            self._selected_label.setText('선택 부재: (없음)')
            self._sec_row.setVisible(False)
            return
        comp = self._scene.components[comp_id]
        # [E4] 타입 표기 — 독립부재는 '모듈A-1', 종속/기타는 타입명.
        from modular_3d.model.type_naming import classify_component_types
        labels = classify_component_types(self._scene)
        type_label = labels.get(comp_id) or TYPE_NAMES.get(
            comp.comp_type, str(comp.comp_type))
        x, y, z = (float(comp.position[0]),
                   float(comp.position[1]),
                   float(comp.position[2]))
        # [E3] 크기 — dimensions 중 있는 값만 (width×depth×height).
        d = getattr(comp, 'dimensions', {}) or {}
        size_parts = [f'{float(d[k]):.0f}' for k in ('width', 'depth', 'height')
                      if d.get(k)]
        size_str = '×'.join(size_parts) if size_parts else '-'
        self._selected_label.setText(
            f'선택 부재: {type_label}\n'
            f'  위치=({x:.0f}, {y:.0f}, {z:.0f})\n'
            f'  크기={size_str} mm'
        )
        self._refresh_section_combo(comp)

    def _refresh_section_combo(self, comp):
        """보 단면 타입 콤보 — 직접선택 부재는 활성, 종속/합체는 부모 따라감(비활성)."""
        ct = comp.comp_type
        if ct in _NO_BEAM_TYPES:
            self._sec_row.setVisible(False)
            return
        # 합체 구조벽(merged_fp_id) 또는 종속 부재면 부모를 따라감 → 비활성 표시.
        is_merged_wall = (ct == ComponentType.STRUCT_WALL
                          and getattr(comp, 'merged_fp_id', None))
        is_direct = (ct in _DIRECT_BEAM_TYPES
                     or (ct == ComponentType.STRUCT_WALL and not is_merged_wall))
        eff = effective_beam_section_type(comp, self._scene)
        self._loading = True
        idx = self._sec_combo.findData(eff)
        if idx >= 0:
            self._sec_combo.setCurrentIndex(idx)
        self._loading = False
        self._sec_combo.setEnabled(bool(is_direct))
        self._sec_caption.setText('보 단면:' if is_direct else '보 단면(부모 따라감):')
        self._sec_row.setVisible(True)

    def _on_sec_changed(self):
        if self._loading or self._sel_id <= 0:
            return
        if not self._sec_combo.isEnabled():
            return
        self.beam_section_changed.emit(self._sel_id, self._sec_combo.currentData())

