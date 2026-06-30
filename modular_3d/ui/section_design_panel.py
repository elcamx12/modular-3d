"""단면 설계 탭 우측 옵션 패널 (2026-05-31, P3).

2축 옵션(스코프×입도) + 기둥 층분할 토글 + 적용 버튼.
'적용' 을 누르면 apply_requested 시그널을 emit → main_3d 가 현재 옵션으로
converge_sections 를 재호출하고 좌측 응력비 색을 갱신한다.

[설계 — 단면 설계 탭 계획서 1-5]
- 스코프(공간 범위, 택1): 전체 / 같은 타입끼리 / 같은 타입+실배치까지
- 입도(부재 묶음, 색 기준): 전부 한 단면 / 기둥·보 / 기둥·천장보·바닥보
- 기둥 층분할: 기둥을 층 단위로 나눠 통일(보조 토글)
- 기본값: 모두 통일(스코프=전체 × 입도=전부 한 단면) — 진입 시 자동 수렴.

P4 에서 이 패널 아래에 타입 목록 + 컴포넌트 3D 가 붙는다(지금은 자리만).
"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QRadioButton, QButtonGroup,
    QCheckBox, QPushButton, QLabel, QListWidget, QListWidgetItem, QComboBox,
    QSpinBox,
)
from PyQt5.QtCore import pyqtSignal, Qt

from modular_3d.analysis.section_converge import (
    ConvergeOptions,
    SCOPE_ALL, SCOPE_TYPE, SCOPE_TYPE_ROOM,
    GRAN_ALL, GRAN_COL_BEAM, GRAN_COL_CEIL_FLOOR,
)
from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded


# ── 종합탭 톤 디자인 토큰 ─────────────────────────────────
_PAGE_BG     = "#EDF2F7"
_CARD_BG     = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEAD_FG     = "#1F4E79"
_BODY_FG     = "#1F2A37"
_SUB_FG      = "#5B6573"
_ACCENT      = "#1F4E79"
_ACCENT_HOV  = "#163A5E"
_ACCENT_SOFT = "#F5F8FF"

# 패널 전체 스타일시트 — 종합/비교탭 톤.
# - GroupBox 타이틀 / QLabel / QRadioButton(묻는 글·옵션) → Paperlogy
# - QComboBox·QSpinBox·QListWidget(값·입력) → Freesentation, 흰 카드 톤
# - QPushButton → Freesentation 둥근 카드형(적용 버튼은 accent 채움 #applyBtn)
_SECTION_QSS = (
    f"SectionDesignPanel {{ background: {_PAGE_BG}; }}"
    "QGroupBox {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 17px; font-weight: 800; color: {_HEAD_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 10px; margin-top: 9px;"
    " padding: 8px 10px 6px 10px; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 12px;"
    f" padding: 0 6px; background: {_CARD_BG};"
    " }"
    "QLabel {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; color: {_BODY_FG}; background: transparent;"
    " }"
    "QRadioButton {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; color: {_BODY_FG}; background: transparent;"
    " padding: 1px 0; }"
    "QComboBox, QSpinBox {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; color: {_BODY_FG};"
    f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
    " background: white; padding: 4px 8px; min-height: 24px; }"
    "QListWidget {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; color: {_BODY_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 8px; }"
    "QPushButton {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; font-weight: 700; color: {_BODY_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 8px; padding: 8px 14px; }"
    f"QPushButton:hover {{ background: {_ACCENT_SOFT}; border-color: {_ACCENT};"
    f" color: {_HEAD_FG}; }}"
    f"QPushButton#applyBtn {{ background: {_ACCENT}; color: white;"
    f" border-color: {_ACCENT}; font-weight: 800; }}"
    f"QPushButton#applyBtn:hover {{ background: {_ACCENT_HOV};"
    f" border-color: {_ACCENT_HOV}; }}"
)


class SectionDesignPanel(QWidget):
    """단면 설계 옵션 패널."""

    apply_requested = pyqtSignal()
    # 타입 목록에서 선택 → (label, rep_comp_id). P4b 컴포넌트 3D 가 소비.
    type_selected = pyqtSignal(str, int)
    # [9-7] 단면 변경 — 콤보를 바꾸면 즉시 emit → main 이 대상에 반영·재수렴·전파.
    section_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # [2026-06-02 디자인 통일] 종합/비교탭 톤 적용 — 폰트 등록 + 패널 스타일시트.
        ensure_fonts_loaded()
        self.setStyleSheet(_SECTION_QSS)
        lay = QVBoxLayout(self)
        # [2026-06-07] 옵션 박스를 최대한 위로 — 상단 여백·항목 간격을 축소.
        lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(6)

        # [2026-06-07] 창 크기 고정으로 우측 패널 세로 공간이 부족해져
        # '단면 설계' 헤더 라벨을 제거(공간 절약).

        # ── 옵션 3열 동시 배치: [통일 범위] [묶음 입도] [기둥 층분할 구간 수(+적용)] ──
        opt_row = QHBoxLayout()
        opt_row.setSpacing(6)

        # 1열: 단면 공유 범위(스코프)
        scope_box = QGroupBox('단면 공유 범위')
        sv = QVBoxLayout(scope_box)
        sv.setSpacing(2); sv.setContentsMargins(8, 2, 8, 2)
        self._scope_group = QButtonGroup(self)
        self._scope_radios = []
        for i, (label, _val) in enumerate((
            ('전체에서 통일', SCOPE_ALL),
            ('크기가 동일한 타입끼리 통일', SCOPE_TYPE),
            ('완전히 동일한 타입끼리 통일', SCOPE_TYPE_ROOM),
        )):
            rb = QRadioButton(label)
            self._scope_group.addButton(rb, i)
            sv.addWidget(rb)
            self._scope_radios.append(rb)
        self._scope_radios[0].setChecked(True)   # 기본 = 전체
        sv.addStretch(1)
        opt_row.addWidget(scope_box)

        # 2열: 부재 구분(입도)
        gran_box = QGroupBox('부재 구분')
        gv = QVBoxLayout(gran_box)
        gv.setSpacing(2); gv.setContentsMargins(8, 2, 8, 2)
        self._gran_group = QButtonGroup(self)
        self._gran_radios = []
        for i, (label, _val) in enumerate((
            ('전부 한 단면', GRAN_ALL),
            ('기둥 / 보', GRAN_COL_BEAM),
            ('기둥 / 천장보 / 바닥보', GRAN_COL_CEIL_FLOOR),
        )):
            rb = QRadioButton(label)
            self._gran_group.addButton(rb, i)
            gv.addWidget(rb)
            self._gran_radios.append(rb)
        self._gran_radios[0].setChecked(True)    # 기본 = 전부 한 단면
        gv.addStretch(1)
        opt_row.addWidget(gran_box)

        # 3열: 기둥 층분할 구간 수(숫자칸) + 적용 버튼(아래) — 한 칸처럼.
        # [7-1] K 스핀박스(1=분할 없음). 기둥을 층 기준 K 구간으로 DP 최적 분할.
        seg_box = QGroupBox('기둥 층분할 구간 수')
        c3 = QVBoxLayout(seg_box)
        c3.setSpacing(2); c3.setContentsMargins(8, 2, 8, 2)
        self._col_seg_spin = QSpinBox()
        self._col_seg_spin.setMinimum(1)
        self._col_seg_spin.setMaximum(50)   # 설정 층수까지 — 수렴이 층수로 자동 클램프.
        self._col_seg_spin.setValue(1)
        self._col_seg_spin.setToolTip('1 = 분할 없음. K = 층을 K 구간으로 DP 최적 분할.')
        self._col_seg_spin.valueChanged.connect(self._update_option_summary)
        c3.addWidget(self._col_seg_spin)
        c3.addStretch(1)
        opt_row.addWidget(seg_box)

        # 3열 + 자연어 요약 + 적용 버튼을 하나의 바깥 박스로 묶음.
        self._option_box = QGroupBox('단면 설계 옵션')
        ob = QVBoxLayout(self._option_box)
        ob.setSpacing(4); ob.setContentsMargins(8, 2, 8, 4)
        ob.addLayout(opt_row)
        # [자연어 요약] 선택한 옵션을 사람이 읽기 쉬운 문장으로.
        self._opt_summary = QLabel('')
        self._opt_summary.setWordWrap(True)
        self._opt_summary.setStyleSheet(f'color:{_SUB_FG}; padding:2px;')
        ob.addWidget(self._opt_summary)
        # [2026-06-07] 수렴 상태(수렴 완료·NG 개수)를 '적용(재수렴)' 버튼 *왼쪽*에 둔다.
        sum_row = QHBoxLayout()
        self._status_label = QLabel('')
        self._status_label.setWordWrap(True)
        # 수렴 결과 요약은 값/숫자 → Freesentation.
        self._status_label.setStyleSheet(
            f"font-family:'{F_BODY}','Malgun Gothic',sans-serif;"
            f" font-size:14px; color:{_SUB_FG}; background:transparent;")
        sum_row.addWidget(self._status_label, stretch=1)
        self._apply_btn = QPushButton('적용 (재수렴)')
        self._apply_btn.setObjectName('applyBtn')   # accent 채움(스타일시트)
        self._apply_btn.clicked.connect(lambda: self.apply_requested.emit())
        sum_row.addWidget(self._apply_btn, alignment=Qt.AlignVCenter)
        ob.addLayout(sum_row)
        lay.addWidget(self._option_box)
        # 라디오 변경 시 요약 갱신.
        self._scope_group.buttonClicked.connect(self._update_option_summary)
        self._gran_group.buttonClicked.connect(self._update_option_summary)
        self._update_option_summary()

        # ── 단면 변경 (9-7) — 콤보 바꾸면 즉시 적용·전파 ──
        self._change_box = QGroupBox('단면 변경')
        lv = QVBoxLayout(self._change_box)
        # 현재 편집 대상(좌측 3D 클릭=컴포넌트 / 타입 목록 클릭=타입) 표시.
        self._lock_target_label = QLabel('대상: (선택 없음)')
        self._lock_target_label.setWordWrap(True)
        lv.addWidget(self._lock_target_label)
        from modular_3d.카탈로그.steel_sections import SHS_CATALOG
        self._suppress_combo = False   # prefill 중 신호 억제(자동 재적용 방지)
        self._lock_combos = {}
        # [2026-06-07] 기둥·천장보·바닥보를 한 줄에: 기둥 [콤보] 천장보 [콤보] 바닥보 [콤보].
        #   세로 공간 절약(3줄 → 1줄). 각 라벨+콤보를 같은 가로 줄에 순서대로 배치.
        row = QHBoxLayout()
        for cc, kr in (('column', '기둥'), ('ceil', '천장보'), ('floor', '바닥보')):
            row.addWidget(QLabel(kr))
            cb = QComboBox()
            cb.addItem('(자동)', None)          # 변경 안 함(자동 설계)
            for s in SHS_CATALOG:
                cb.addItem(s.name, s.name)
            cb.currentIndexChanged.connect(self._on_combo_changed)
            row.addWidget(cb, stretch=1)
            self._lock_combos[cc] = cb
        lv.addLayout(row)
        lay.addWidget(self._change_box)

        # ── 타입 목록 (P4a, -1-1 로컬 파생) ───────────────
        type_box = QGroupBox('타입 목록')
        tv = QVBoxLayout(type_box)
        self._type_list = QListWidget()
        self._type_list.currentRowChanged.connect(self._on_type_row_changed)
        # [2026-06-07] 10줄 고정(min=max) 해제 — 사용 가능한 세로 공간에 맞춰
        # 늘었다 줄었다 하도록 최소 높이만 두고(약 4줄) 스트레치로 확장.
        _rh = self._type_list.fontMetrics().height() + 6
        self._type_list.setMinimumHeight(_rh * 4 + 8)
        tv.addWidget(self._type_list, stretch=1)
        # 선택 타입 상세(단면/응력비/OK·NG).
        self._type_detail = QLabel('')
        self._type_detail.setWordWrap(True)
        # 선택 타입 상세는 단면명·응력비 등 값 → Freesentation.
        self._type_detail.setStyleSheet(
            f"font-family:'{F_BODY}','Malgun Gothic',sans-serif;"
            f" font-size:14px; color:{_BODY_FG}; background:transparent;")
        tv.addWidget(self._type_detail)

        # 선택 타입 단일 컴포넌트 3D (P4b-1, 색 메쉬). three.js 임베드.
        #   메시 주입은 main_3d._on_section_type_selected 가 set_component_mesh 로.
        #   [P4b-2 잔여] 단면 변형 반영·종속부재 포함·외곽고정 안쪽성장.
        self._comp_viewer = None
        try:
            from modular_3d.render.viewer_three import ViewerThree
            from PyQt5.QtWidgets import QSizePolicy
            self._comp_viewer = ViewerThree()
            w = self._comp_viewer.get_native_widget()
            # [2026-06-07] 3D 뷰가 타입 목록과 세로 공간을 반씩 나눠 세로로 길어지면
            #   모듈이 아래로 잘린 것처럼 보였다. stretch 를 빼고(목록이 남는 공간을
            #   가져감) 높이를 가로형(약 4:3)으로 고정해 안정적으로 보이게 한다.
            w.setMinimumHeight(300)
            w.setMaximumHeight(340)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            tv.addWidget(w)
        except Exception:
            self._comp_viewer = None
        lay.addWidget(type_box, stretch=1)

        # 타입 데이터 캐시 — populate_types 가 채움.
        self._types = []

    # ── 옵션 자연어 요약 ─────────────────────────────────
    def _update_option_summary(self, *_):
        """현재 선택(공유범위×구분×기둥분할)을 사람이 읽기 쉬운 문장으로."""
        opts = self.current_options()
        scope_txt = {
            SCOPE_ALL: '건물 전체에서',
            SCOPE_TYPE: '크기가 동일한 타입끼리',
            SCOPE_TYPE_ROOM: '완전히 동일한 타입끼리',
        }.get(opts.scope, '')
        gran_txt = {
            GRAN_ALL: '모든 부재를 한 단면으로 통일',
            GRAN_COL_BEAM: '기둥과 보 단위로 통일',
            GRAN_COL_CEIL_FLOOR: '기둥·천장보·바닥보 단위로 통일',
        }.get(opts.granularity, '')
        seg_txt = ''
        if opts.column_segments and int(opts.column_segments) >= 2:
            seg_txt = f', 기둥은 {int(opts.column_segments)}개 구간으로 분할'
        self._opt_summary.setText(f'→ {scope_txt} {gran_txt}{seg_txt}')

    # ── 타입 목록 ────────────────────────────────────────
    def populate_types(self, types) -> None:
        """수렴 결과 타입 목록 채우기.

        types: List[dict] (label/rep_comp_id/summary/max_ratio/ok). NG 는 [NG] 표시.
        """
        self._types = list(types or [])
        self._type_list.blockSignals(True)
        self._type_list.clear()
        for t in self._types:
            mark = '' if t.get('ok', True) else '  [NG]'
            # [2026-06-08] 층별 분해 표기 제거 — 우측 패널이 길어지는 문제 →
            #   "1층 2·2층 1" 식 대신 전체 합계만 "(N개)" 로 짧게 쓴다.
            cnt = t.get('count', 0)
            cstr = f"  ({cnt}개)" if cnt else ''
            it = QListWidgetItem(f"{t.get('label', '?')}{cstr}{mark}")
            it.setData(Qt.UserRole, t.get('rep_comp_id', -1))
            it.setData(Qt.UserRole + 1, t.get('label', ''))   # [7B-2] 라벨 매칭용
            self._type_list.addItem(it)
        self._type_list.blockSignals(False)
        if self._types:
            self._type_list.setCurrentRow(0)
        else:
            self._type_detail.setText('')
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        """[L6] 우측 패널 폭을 컨트롤 내용폭에 맞춰 최소화(잘림·가로스크롤 없이).

        표·트리가 없으므로 옵션/단면변경 박스의 권장폭과 타입 목록 항목 최대폭
        중 큰 값을 쓴다(3D 뷰어는 가변이라 제외). 부모 우측 pane 을 그 폭으로
        고정 — minimumWidth 만으로는 이미 넓어진 폭이 안 줄어들기 때문.
        """
        cands = [self._option_box.sizeHint().width(),
                 self._change_box.sizeHint().width()]
        if self._type_list.count() > 0:
            cands.append(self._type_list.sizeHintForColumn(0)
                         + 2 * self._type_list.frameWidth() + 24)
        w = max(cands) + 16   # 좌우 마진(6+6) + 안전 여유
        self.setMaximumWidth(16777215)
        self.setMinimumWidth(w)
        self._force_pane_width(w)
        self.updateGeometry()

    def _force_pane_width(self, w: int) -> None:
        """부모 우측 pane(_section_right_pane, 최소폭 380 박힘)을 내용폭으로 고정."""
        par = self.parentWidget()
        if par is None:
            return
        par.setMinimumWidth(0)
        par.setFixedWidth(int(w))

    def _on_type_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._types):
            self._type_detail.setText('')
            return
        t = self._types[row]
        ng = '' if t.get('ok', True) else '  ⚠ NG'
        self._type_detail.setText(
            f"{t.get('label', '?')}{ng}\n{t.get('summary', '')}\n"
            f"최대 응력비 {t.get('max_ratio', 0.0):.2f}")
        self.type_selected.emit(str(t.get('label', '')), int(t.get('rep_comp_id', -1)))

    # ── 옵션 매핑 ────────────────────────────────────────
    def current_options(self) -> ConvergeOptions:
        """현재 라디오/체크 상태 → ConvergeOptions."""
        scope = (SCOPE_ALL, SCOPE_TYPE, SCOPE_TYPE_ROOM)[
            max(0, self._scope_group.checkedId())]
        gran = (GRAN_ALL, GRAN_COL_BEAM, GRAN_COL_CEIL_FLOOR)[
            max(0, self._gran_group.checkedId())]
        return ConvergeOptions(
            scope=scope, granularity=gran,
            column_segments=int(self._col_seg_spin.value()),
        )

    def set_status(self, text: str) -> None:
        """수렴 결과 요약 표시(반복 횟수·수렴 여부·NG 수 등)."""
        self._status_label.setText(text or '')

    # ── 좌측 3D 선택 연동 (7B-2) ─────────────────────────
    def set_lock_target_label(self, text: str) -> None:
        """잠금/편집 대상 표시(예: '대상: 모듈A-1-1 (이 컴포넌트만)')."""
        self._lock_target_label.setText(text or '대상: (선택 없음)')

    def select_type_row(self, label: str) -> None:
        """타입 목록에서 해당 라벨 행을 *신호 없이* 선택(표시 동기용, 7B-2).

        currentRowChanged 가 재발동해 컴포넌트 3D 가 대표 comp 로 덮이는 것을 막기 위해
        blockSignals 로 감싼다.
        """
        if not label:
            return
        self._type_list.blockSignals(True)
        try:
            for i in range(self._type_list.count()):
                it = self._type_list.item(i)
                if it is not None and it.data(Qt.UserRole + 1) == label:
                    self._type_list.setCurrentRow(i)
                    break
        finally:
            self._type_list.blockSignals(False)

    def _on_combo_changed(self, *_):
        """[9-7] 사용자가 콤보를 바꾸면 즉시 단면 변경 emit. prefill 중엔 억제."""
        if getattr(self, '_suppress_combo', False):
            return
        self.section_changed.emit()

    def prefill_lock_combos(self, names_by_class: dict) -> None:
        """단면 변경 콤보를 선택 대상의 현재 단면명으로 미리 채움.

        [9-4] 그 컴포넌트에 그 색클래스 부재가 없으면(바닥패널의 기둥/천장보 등) '(자동)'
        으로 고정 + 비활성화(setEnabled False). 있으면 현재 단면으로 채우고 활성.
        [9-7] prefill 은 사용자 변경이 아니므로 신호 억제(자동 재적용 방지).
        """
        names_by_class = names_by_class or {}
        self._suppress_combo = True
        try:
            for cc, cb in getattr(self, '_lock_combos', {}).items():
                name = names_by_class.get(cc)
                if name:
                    cb.setEnabled(True)
                    idx = cb.findData(name)
                    cb.setCurrentIndex(idx if idx >= 0 else 0)
                else:
                    cb.setCurrentIndex(0)        # (자동)
                    cb.setEnabled(False)
        finally:
            self._suppress_combo = False

    def show_type_detail(self, label: str) -> None:
        """[9-5] 라벨에 해당하는 타입의 상세(단면/응력비/OK·NG)를 표시."""
        for t in getattr(self, '_types', []):
            if t.get('label') == label:
                ng = '' if t.get('ok', True) else '  ⚠ NG'
                self._type_detail.setText(
                    f"{label}{ng}\n{t.get('summary', '')}\n"
                    f"최대 응력비 {t.get('max_ratio', 0.0):.2f}")
                return
        self._type_detail.setText('')

    # ── 수동 잠금 (P4c-2) ────────────────────────────────
    def current_lock_choices(self) -> dict:
        """잠금 콤보 → {색클래스: 단면명}. '(자동)' 은 제외."""
        out = {}
        for cc, cb in getattr(self, '_lock_combos', {}).items():
            name = cb.currentData()
            if name:
                out[cc] = name
        return out

    # ── 컴포넌트 3D (P4b-1) ──────────────────────────────
    def set_component_mesh(self, verts, faces, colors) -> None:
        """선택 타입 컴포넌트 메시 표시(이전 것 교체)."""
        if self._comp_viewer is None:
            return
        try:
            self._comp_viewer.remove_component_visual(0)
        except Exception:
            pass
        try:
            self._comp_viewer.add_component_visual(0, verts, faces, colors)
        except Exception:
            pass

    def clear_component_view(self) -> None:
        if self._comp_viewer is None:
            return
        try:
            self._comp_viewer.remove_component_visual(0)
        except Exception:
            pass

    def set_component_dims(self, dx, dy, dz) -> None:
        """[7C-1] 컴포넌트 3D 에 캐드식 치수선(폭/깊이/높이, mm)."""
        if self._comp_viewer is None:
            return
        try:
            self._comp_viewer.set_dimension_lines(dx, dy, dz)
        except Exception:
            pass

    def set_component_members(self, members) -> None:
        """[7C-2] 색 클래스별 부재 메시(단면명 포함) 표시 — hover 시 단면명 툴팁."""
        if self._comp_viewer is None:
            return
        try:
            self._comp_viewer.set_component_members(members or [])
        except Exception:
            pass
