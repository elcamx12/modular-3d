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
    QDoubleSpinBox, QGridLayout, QSpinBox, QPushButton, QDialog,
    QDialogButtonBox,
)

from modular_3d.model import ComponentType, Scene, effective_beam_section_type
from modular_3d.model.core import CORE_REBAR_DEFAULT, normalize_core_rebar
from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded


# KS 이형철근 공칭 단면적 (mm²) — 배근 → 철근비 환산용.
_REBAR_AREA = {
    'D10': 71.33, 'D13': 126.7, 'D16': 198.6, 'D19': 286.5,
    'D22': 387.1, 'D25': 506.7, 'D29': 642.4, 'D32': 794.2,
}


# ── 종합탭 톤 디자인 토큰 ─────────────────────────────────
_PAGE_BG     = "#EDF2F7"
_CARD_BG     = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEAD_FG     = "#1F4E79"
_BODY_FG     = "#1F2A37"
_SUB_FG      = "#5B6573"


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


class RebarCalcDialog(QDialog):
    """배근(개수×직경) → 철근비 또는 철근 단면적 환산 보조 다이얼로그.

    두 모드:
    - 철근비 모드(기본): ρ = (개수×직경면적) / (콘크리트 폭×길이). 코어벽 경계/웹용.
    - 면적 모드(area_mode=True): As = 개수×직경면적 만 산출(콘크리트 칸 생략).
      슬래브 연결부 철근(수집재·다우얼)처럼 단면적 자체가 필요한 입력용.
    확인 시 호출처가 rho() 또는 area() 로 결과를 가져간다.
    """

    def __init__(self, init_w=0.0, init_len=0.0, title='배근 환산',
                 area_mode=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._rho = 0.0
        self._area_mode = bool(area_mode)
        lay = QVBoxLayout(self)
        grid = QGridLayout()

        self._n = QSpinBox()
        self._n.setRange(0, 500)
        self._n.setValue(8)
        self._dia = QComboBox()
        for _name, _area in _REBAR_AREA.items():
            self._dia.addItem(_name, _area)
        self._dia.setCurrentText('D25')
        grid.addWidget(QLabel('철근 개수'), 0, 0)
        grid.addWidget(self._n, 0, 1)
        grid.addWidget(QLabel('철근 직경'), 1, 0)
        grid.addWidget(self._dia, 1, 1)

        self._w = self._len = None
        if not self._area_mode:
            self._w = QDoubleSpinBox()
            self._w.setRange(1.0, 100000.0)
            self._w.setDecimals(0)
            self._w.setValue(float(init_w))
            self._w.setSuffix(' mm')
            self._len = QDoubleSpinBox()
            self._len.setRange(1.0, 100000.0)
            self._len.setDecimals(0)
            self._len.setValue(float(init_len))
            self._len.setSuffix(' mm')
            grid.addWidget(QLabel('콘크리트 폭'), 2, 0)
            grid.addWidget(self._w, 2, 1)
            grid.addWidget(QLabel('콘크리트 길이'), 3, 0)
            grid.addWidget(self._len, 3, 1)
        lay.addLayout(grid)

        self._result = QLabel()
        self._result.setWordWrap(True)
        lay.addWidget(self._result)

        self._n.valueChanged.connect(self._recalc)
        self._dia.currentIndexChanged.connect(self._recalc)
        if not self._area_mode:
            self._w.valueChanged.connect(self._recalc)
            self._len.valueChanged.connect(self._recalc)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._recalc()

    def _recalc(self, *args):
        a_s = self.area()
        if self._area_mode:
            self._result.setText(f'철근량 As = {a_s:.0f} mm²')
            return
        a_c = self._w.value() * self._len.value()
        self._rho = (a_s / a_c) if a_c > 0 else 0.0
        self._result.setText(
            f'철근비 = {self._rho * 100:.3f} %   '
            f'(As={a_s:.0f} mm², Ac={a_c:.0f} mm²)')

    def rho(self) -> float:
        return self._rho

    def area(self) -> float:
        return self._n.value() * float(self._dia.currentData())


class DesignPropertiesPanel(QWidget):
    """디자인 탭 우측 패널 — 활성/선택 부재 정보 + 보 단면 타입 선택."""

    # (comp_id, 'shs'|'h') — 선택 부재의 보 단면 타입 변경 요청.
    beam_section_changed = pyqtSignal(int, str)
    # (group_id, {rho_boundary, rho_web, fck, fy}) — 코어 그룹 철근 설정 변경 요청.
    #   코어 슬래브 선택 시 입력하며, 그 그룹의 코어벽 전체(전 층)에 적용된다.
    core_rebar_changed = pyqtSignal(int, dict)

    def __init__(self, scene: Scene, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._scene = scene
        self._sel_id = -1
        self._loading = False
        self._rebar_gid = 0   # 현재 철근 입력 대상 코어 그룹(0=없음)
        self._setup_ui()
        self.refresh_active(None)
        self.refresh_selected(-1)

    def _setup_ui(self):
        ensure_fonts_loaded()  # Paperlogy/Freesentation 등록 보장
        self.setStyleSheet(
            f"DesignPropertiesPanel {{ background: {_PAGE_BG}; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # 헤더 라벨 — Paperlogy, 종합탭 헤드라인 톤.
        title = QLabel('속성')
        title.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_HEAD_FG}; font-size: 17px; font-weight: 800;"
            " background: transparent; padding: 2px 0 4px 2px;"
        )
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {_CARD_BORDER}; background: {_CARD_BORDER};")
        sep.setFixedHeight(1)
        lay.addWidget(sep)

        # ── 선택 부재 정보 — 값/정보이므로 Freesentation, 흰 카드 안에. ─────
        self._selected_label = QLabel('선택 부재: (없음)')
        self._selected_label.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {_BODY_FG}; font-size: 15px; background: {_CARD_BG};"
            f" border: 1px solid {_CARD_BORDER}; border-radius: 8px;"
            " padding: 12px 14px; line-height: 150%;"
        )
        self._selected_label.setWordWrap(True)
        self._selected_label.setMinimumWidth(0)
        lay.addWidget(self._selected_label)

        # ── 보 단면 타입 (각형강관 / H형강) ─────────────
        self._sec_row = QWidget()
        sec_lay = QHBoxLayout(self._sec_row)
        sec_lay.setContentsMargins(0, 0, 0, 0)
        # 캡션은 묻는 글 → Paperlogy.
        self._sec_caption = QLabel('보 단면:')
        self._sec_caption.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_HEAD_FG}; font-size: 15px; font-weight: 700;"
            " background: transparent;"
        )
        sec_lay.addWidget(self._sec_caption)
        self._sec_combo = QComboBox()
        self._sec_combo.addItem('각형강관', 'shs')
        self._sec_combo.addItem('H형강', 'h')
        # 콤보 안 값은 Freesentation.
        self._sec_combo.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" font-size: 15px; padding: 5px 9px; color: {_BODY_FG};"
            f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
            f" background: {_CARD_BG}; min-height: 24px;"
        )
        self._sec_combo.currentIndexChanged.connect(self._on_sec_changed)
        sec_lay.addWidget(self._sec_combo, stretch=1)
        lay.addWidget(self._sec_row)
        self._sec_row.setVisible(False)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color: {_CARD_BORDER}; background: {_CARD_BORDER};")
        sep2.setFixedHeight(1)
        lay.addWidget(sep2)

        # ── 해석 좌표 안내 — 안내문이므로 Paperlogy ─────────────
        # 모듈 외관 z = 0 ~ 3400 mm 이지만 해석 모델의 보 중심선은
        # z = 0 ~ 3200 (=h - SECTION_W=200) 위치. 사용자가 코어 슬래브 z 와
        # 보 격자 끝 z 차이를 헷갈리지 않도록 한 줄로 안내.
        _z_note = QLabel('해석 좌표: 보 중심선 z = h − 200 mm '
                         '(예: 모듈 천장보 = z 3200, 외관 = z 3400)')
        _z_note.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_SUB_FG}; font-size: 11px; background: transparent;"
        )
        _z_note.setWordWrap(True)
        lay.addWidget(_z_note)

        # ── 코어 철근 (코어 슬래브 선택 시 표시 — 이 코어 그룹 전체에 적용) ─────
        #   경계요소·웹 수직 철근비 + 콘크리트/철근 강도. fiber 해석에 직접 반영된다.
        #   경계요소 길이·fiber 수는 자동(코드 규정). 미입력 그룹은 기본값 사용.
        self._rebar_box = QWidget()
        _rb = QVBoxLayout(self._rebar_box)
        _rb.setContentsMargins(0, 8, 0, 0)
        _rb.setSpacing(6)
        _rb_cap = QLabel('코어 철근 (이 코어 그룹 전체)')
        _rb_cap.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_HEAD_FG}; font-size: 15px; font-weight: 700;"
            " background: transparent;"
        )
        _rb.addWidget(_rb_cap)

        def _mk_spin(minimum, maximum, step, decimals, suffix):
            sp = QDoubleSpinBox()
            sp.setRange(minimum, maximum)
            sp.setSingleStep(step)
            sp.setDecimals(decimals)
            sp.setSuffix(suffix)
            sp.setStyleSheet(
                f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                f" font-size: 14px; padding: 3px 6px; color: {_BODY_FG};"
                f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
                f" background: {_CARD_BG}; min-height: 22px;"
            )
            sp.valueChanged.connect(self._on_rebar_changed)
            return sp

        def _mk_cap(text):
            q = QLabel(text)
            q.setStyleSheet(
                f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
                f" color: {_SUB_FG}; font-size: 13px; background: transparent;"
            )
            return q

        self._sp_rho_be = _mk_spin(0.0, 10.0, 0.1, 2, ' %')
        self._sp_rho_web = _mk_spin(0.0, 5.0, 0.1, 2, ' %')
        self._sp_fck = _mk_spin(10.0, 100.0, 1.0, 0, ' MPa')
        self._sp_fy = _mk_spin(200.0, 700.0, 10.0, 0, ' MPa')

        _grid = QGridLayout()
        _grid.setContentsMargins(0, 0, 0, 0)
        _grid.setHorizontalSpacing(8)
        _grid.setVerticalSpacing(6)
        def _mk_calc_btn(target):
            b = QPushButton('배근…')
            b.setStyleSheet(
                f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                f" font-size: 12px; padding: 2px 8px; color: {_HEAD_FG};"
                f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
                f" background: {_CARD_BG}; min-height: 22px;"
            )
            b.setToolTip('철근 개수·직경으로 철근비를 계산해 채웁니다')
            b.clicked.connect(lambda: self._open_rebar_calc(target))
            return b

        _grid.addWidget(_mk_cap('경계 철근비'), 0, 0)
        _grid.addWidget(self._sp_rho_be, 0, 1)
        _grid.addWidget(_mk_calc_btn('be'), 0, 2)
        _grid.addWidget(_mk_cap('웹 철근비'), 1, 0)
        _grid.addWidget(self._sp_rho_web, 1, 1)
        _grid.addWidget(_mk_calc_btn('web'), 1, 2)
        _grid.addWidget(_mk_cap('콘크리트 fck'), 2, 0)
        _grid.addWidget(self._sp_fck, 2, 1)
        _grid.addWidget(_mk_cap('철근 fy'), 3, 0)
        _grid.addWidget(self._sp_fy, 3, 1)
        _rb.addLayout(_grid)

        # ── 슬래브 연결부 철근 (지침 8장: 수집재 As,col1 · 다우얼 As,dw · 슬래브 fck) ──
        #   슬래브-벽체 격막 연결부 전단강도 검토용. 면적(mm²) 직접 또는 배근으로 환산.
        _sl_cap = QLabel('슬래브 연결부 철근 (지침 8장)')
        _sl_cap.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_HEAD_FG}; font-size: 14px; font-weight: 700;"
            " background: transparent; padding-top: 4px;"
        )
        _rb.addWidget(_sl_cap)

        self._sp_col_area = _mk_spin(0.0, 200000.0, 100.0, 0, ' mm²')
        self._sp_dowel_area = _mk_spin(0.0, 200000.0, 100.0, 0, ' mm²')
        self._sp_slab_fck = _mk_spin(10.0, 100.0, 1.0, 0, ' MPa')

        def _mk_area_btn(spin):
            b = QPushButton('배근…')
            b.setStyleSheet(
                f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
                f" font-size: 12px; padding: 2px 8px; color: {_HEAD_FG};"
                f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
                f" background: {_CARD_BG}; min-height: 22px;"
            )
            b.setToolTip('철근 개수·직경으로 단면적을 계산해 채웁니다')
            b.clicked.connect(lambda: self._open_area_calc(spin))
            return b

        _grid2 = QGridLayout()
        _grid2.setContentsMargins(0, 0, 0, 0)
        _grid2.setHorizontalSpacing(8)
        _grid2.setVerticalSpacing(6)
        _grid2.addWidget(_mk_cap('수집재 As'), 0, 0)
        _grid2.addWidget(self._sp_col_area, 0, 1)
        _grid2.addWidget(_mk_area_btn(self._sp_col_area), 0, 2)
        _grid2.addWidget(_mk_cap('다우얼 As'), 1, 0)
        _grid2.addWidget(self._sp_dowel_area, 1, 1)
        _grid2.addWidget(_mk_area_btn(self._sp_dowel_area), 1, 2)
        _grid2.addWidget(_mk_cap('슬래브 fck'), 2, 0)
        _grid2.addWidget(self._sp_slab_fck, 2, 1)
        _rb.addLayout(_grid2)

        _rb_note = QLabel('미입력 시 기본값(경계 2%·웹 0.4%·fck27·fy400) 적용')
        _rb_note.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_SUB_FG}; font-size: 11px; background: transparent;"
        )
        _rb_note.setWordWrap(True)
        _rb.addWidget(_rb_note)
        lay.addWidget(self._rebar_box)
        self._rebar_box.setVisible(False)

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
            self._rebar_box.setVisible(False)
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
        self._refresh_rebar_section(comp)

    def _refresh_section_combo(self, comp):
        """보 단면 타입 콤보 — 직접선택 부재는 활성, 종속/합체는 부모 따라감(비활성)."""
        ct = comp.comp_type
        if ct in _NO_BEAM_TYPES:
            self._sec_row.setVisible(False)
            return
        # 합체 벽패널(merged_fp_id) 또는 종속 부재면 부모를 따라감 → 비활성 표시.
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

    # ── 코어 철근 섹션 ───────────────────────────────
    def _refresh_rebar_section(self, comp):
        """코어 슬래브 선택 시에만 철근 입력 표시 — 그 그룹의 현재값(없으면 기본값)을
        4 필드에 채운다. 철근비는 내부 비율(0.02) ↔ 표시 %(2.0) 로 변환."""
        if comp.comp_type != ComponentType.CORE_SLAB:
            self._rebar_box.setVisible(False)
            self._rebar_gid = 0
            return
        gid = int(getattr(comp, 'group_id', 0))
        self._rebar_gid = gid
        # 배근 환산 기본 콘크리트 면적용 — 그룹 대표 코어벽 치수(첫 코어벽).
        self._rebar_wall_L, self._rebar_wall_t = 6000.0, 300.0
        for _c in self._scene.components.values():
            if (_c.comp_type == ComponentType.CORE
                    and int(getattr(_c, 'group_id', 0)) == gid):
                self._rebar_wall_L = float(_c.dimensions.get('width', 6000.0))
                self._rebar_wall_t = float(_c.dimensions.get('depth', 300.0))
                break
        cfg = normalize_core_rebar(self._scene.core_rebar.get(gid))
        # setValue 가 valueChanged 를 쏘므로 _loading 가드로 되먹임 방지.
        self._loading = True
        self._sp_rho_be.setValue(cfg['rho_boundary'] * 100.0)
        self._sp_rho_web.setValue(cfg['rho_web'] * 100.0)
        self._sp_fck.setValue(cfg['fck'])
        self._sp_fy.setValue(cfg['fy'])
        self._sp_col_area.setValue(cfg['slab_collector_area'])
        self._sp_dowel_area.setValue(cfg['slab_dowel_area'])
        self._sp_slab_fck.setValue(cfg['slab_fck'])
        self._loading = False
        self._rebar_box.setVisible(True)

    def _on_rebar_changed(self, *args):
        if self._loading or self._rebar_gid <= 0:
            return
        cfg = {
            'rho_boundary': self._sp_rho_be.value() / 100.0,
            'rho_web': self._sp_rho_web.value() / 100.0,
            'fck': self._sp_fck.value(),
            'fy': self._sp_fy.value(),
            'slab_collector_area': self._sp_col_area.value(),
            'slab_dowel_area': self._sp_dowel_area.value(),
            'slab_fck': self._sp_slab_fck.value(),
        }
        self.core_rebar_changed.emit(self._rebar_gid, cfg)

    def _open_rebar_calc(self, target: str):
        """배근 환산 다이얼로그 → 결과 철근비를 경계/웹 필드에 채운다(→ 시그널 발동).
        콘크리트 면적 기본값: 경계요소 = 두께 × lbe, 웹 = 두께 × 웹길이(대표 코어벽 추정)."""
        L = float(getattr(self, '_rebar_wall_L', 6000.0))
        t = float(getattr(self, '_rebar_wall_t', 300.0))
        lbe = min(max(0.15 * L, t), 0.4 * L)
        if target == 'be':
            w0, len0, title, sp = t, lbe, '경계요소 배근 → 철근비', self._sp_rho_be
        else:
            w0, len0, title, sp = (t, max(L - 2.0 * lbe, 1.0),
                                   '웹 배근 → 철근비', self._sp_rho_web)
        dlg = RebarCalcDialog(w0, len0, title=title, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            sp.setValue(dlg.rho() * 100.0)   # valueChanged → core_rebar_changed

    def _open_area_calc(self, target_spin):
        """배근 → 철근 단면적(개수×직경) 다이얼로그 → 결과 면적을 슬래브 철근 필드에 채운다."""
        dlg = RebarCalcDialog(title='배근 → 철근량', area_mode=True, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            target_spin.setValue(dlg.area())   # valueChanged → core_rebar_changed

