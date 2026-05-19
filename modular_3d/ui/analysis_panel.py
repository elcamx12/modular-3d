"""메인 윈도우 오른쪽에 도킹되는 구조해석 결과 패널.

세 탭 구성: [요약] [부재별 내부력] [물량산출]
변형 형상은 좌측 3D 뷰(ops_view)에서 통합 표시 (사용자 결정 X3, 2026-04-29).

부재별 내부력 탭은 컴포넌트(모듈) 단위로 트리 그룹핑하며,
부재 클릭 시 member_selected 시그널을 emit 한다.

물량산출 탭(2026-05-08 추가):
- 정책 라디오버튼(1종/2종/3종) 으로 단면 그룹화 케이스 전환
- 그룹별 채택 단면, 본수표(합계 포함), 슬래브 부피, 부재별 응력비 표시
- 응력비 색상 5단계 (파/하/초/주/빨)
- 부재 행 클릭 시 member_selected 시그널 + ratio_view_changed 시그널 emit
"""
from typing import Dict, Optional
from collections import defaultdict

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QTextEdit, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QRadioButton, QButtonGroup,
    QSplitter, QHeaderView, QAbstractItemView, QCheckBox,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QBrush, QStandardItem, QStandardItemModel
from PyQt5.QtCore import Qt as _Qt

# 한글 라벨 매핑 — 모델 단일 진실 원천 재사용
from modular_3d.model import TYPE_NAMES, ComponentType  # noqa: E402
from modular_3d.카탈로그.geometry import FLOOR_HEIGHT  # noqa: E402


# ─────────────────────────────────────────────────────────
# 부재별 내부력 트리 가독성 매핑 (2026-05-18)
# 사용자 보고: 영어 role 라벨 + 좁은 단일컬럼에서 글씨 짤림.
# → 한국어 role 매핑 + 컴포넌트 위계(타입+xy위치+층) + 5 컬럼 분리.
# ─────────────────────────────────────────────────────────

# 부재 role(영어) → 한글 표시명. 트리 내부의 "역할" 묶음 헤더에 사용.
_ROLE_KO = {
    'module_column':         '기둥',
    'module_bottom_beam':    '바닥보',
    'module_top_beam':       '천장보',
    'floor_edge_beam':       '가장자리보',  # 길이 비교로 장변보/단변보 분기됨
    'wall_column':           '벽 기둥',
    'wall_bottom_runner':    '벽 하부보',
    'wall_top_runner':       '벽 상부보',
    'cantilever_beam':       '캔틸레버보',
    'cantilever_slab_beam':  '캔틸슬래브 보',
    'mid_beam':              '중간보',
    'mid_column':            '중간기둥',
    'core_column':           '코어 기둥',
    'core_bottom_runner':    '코어 하부보',
    'core_top_runner':       '코어 상부보',
    'core_truss_v':          '트러스(수직)',
    'core_truss_h':          '트러스(수평)',
    'core_truss_d':          '트러스(대각)',
    'core_slab_beam':        '슬래브 변',
}

# role 한글명의 정렬 우선순위 — 트리 내부 표시 순서.
_ROLE_KO_ORDER = [
    '기둥', '벽 기둥', '코어 기둥', '중간기둥',
    '천장보', '바닥보',
    '벽 상부보', '벽 하부보', '코어 상부보', '코어 하부보',
    '장변보', '단변보', '가장자리보',
    '중간보', '캔틸레버보', '캔틸슬래브 보',
    '트러스(수직)', '트러스(수평)', '트러스(대각)',
    '슬래브 변',
]

# 컴포넌트 타입(한글) 정렬 — RC 코어 계열을 맨 아래로.
_COMP_TYPE_ORDER = [
    '모듈', '수직 3층 모듈', '바닥패널', '구조벽',
    '캔틸레버보', '캔틸레버슬래브', '중간보', '중간기둥',
    'RC 코어', '코어 슬래브',
]


def _alpha_label(i: int) -> str:
    """0→a, 1→b, … 25→z, 26→aa, 27→ab …  컴포넌트 위치 인덱스 라벨."""
    s = ''
    i = int(i)
    while True:
        s = chr(ord('a') + (i % 26)) + s
        i = i // 26 - 1
        if i < 0:
            return s


def _role_ko_order_key(name: str) -> int:
    try:
        return _ROLE_KO_ORDER.index(name)
    except ValueError:
        return len(_ROLE_KO_ORDER)


def _comp_type_order_key(tname: str) -> int:
    try:
        return _COMP_TYPE_ORDER.index(tname)
    except ValueError:
        return len(_COMP_TYPE_ORDER)


# (2026-05-19 작업 4) 지배항목 영어 → 한글.
_CRITICAL_KO = {
    'axial':    '축력',
    'bend':     '휨',
    'shear':    '전단',
    'combined': '복합',
    'defl':     '처짐',
    '':         '',
}

# (2026-05-19 작업 4) member.kind 영어 → 한글.
_KIND_KO = {
    'beam':   '보',
    'column': '기둥',
    'shell':  '쉘',
}

# (2026-05-19 수정) section_design.group_categories 의 영어 그룹키 → 한글 표시.
# 그룹별 채택 단면 표의 "그룹" 컬럼에 사용. 내부 키(member_to_group 등)는
# 영어 그대로 유지 — 표시 단계에서만 변환.
_GROUP_KO = {
    'columns':     '기둥',
    'beams':       '보',
    'cantilevers': '캔틸레버',
    'all':         '전체',
}


def _group_label_ko(gname: str) -> str:
    """그룹 키 → 한글 표시명. 'columns_F{a}_{b}' / 'columns_F{a}' 동적 처리.

    예) 'columns'       → '기둥'
        'columns_F1_3'  → '기둥 (1~3층)'
        'columns_F1'    → '기둥 (1층)'
    """
    if gname in _GROUP_KO:
        return _GROUP_KO[gname]
    if gname.startswith('columns_F'):
        rest = gname[len('columns_F'):]
        if '_' in rest:
            a, b = rest.split('_', 1)
            return f'기둥 ({a}~{b}층)'
        return f'기둥 ({rest}층)'
    return gname


# (2026-05-13) 케이스 표시명 ↔ 내부 키 매핑.
# 콤보박스에 사용자 친화 하중조합 표기. 내부 키는 ops_solver.solve_all_cases
# 의 dict 키와 일치해야 함.
CASE_DISPLAY_MAP = {
    'ENVELOPE': '지배조합 (5케이스 envelope)',   # 2026-05-19 작업 5
    'D+L': '1.2D + 1.6L (중력)',
    'Ex':  '1.2D + 1.0L + 1.0Ex (X 지진)',
    'Ey':  '1.2D + 1.0L + 1.0Ey (Y 지진)',
    'Wx':  '1.2D + 1.0L + 1.0Wx (X 풍하중)',
    'Wy':  '1.2D + 1.0L + 1.0Wy (Y 풍하중)',
}
# 표시명 → 내부 키 역매핑 (사용자 선택 시).
CASE_DISPLAY_TO_KEY = {v: k for k, v in CASE_DISPLAY_MAP.items()}

# 비활성(추후 구현) 항목 — KBC 2022 §0303 추가 하중조합.
CASE_DISABLED_ITEMS = [
    '1.4D (자중 단독) — 추후 구현',
    '0.9D + 1.0Ex (X 지진 전도 방지) — 추후 구현',
    '0.9D + 1.0Ey (Y 지진 전도 방지) — 추후 구현',
    '0.9D + 1.0Wx (X 풍 전도 방지) — 추후 구현',
    '0.9D + 1.0Wy (Y 풍 전도 방지) — 추후 구현',
    '1.2D + 1.6L + 0.5S (설하중 동시) — 추후 구현',
    '1.2D + 1.0L + 0.2S + 1.0Ex (설+지진) — 추후 구현',
]


class AnalysisPanel(QWidget):
    """F6 구조해석 결과를 표시하는 도킹 패널."""

    # 트리에서 부재(잎)를 클릭하면 단일 member_id 를 emit
    member_selected = pyqtSignal(int)
    # 트리에서 그룹 노드(모듈/층/역할 묶음)를 클릭하면 하위 mid 리스트 emit (2026-05-18)
    members_selected = pyqtSignal(list)
    # 케이스 콤보박스 변경 시 (케이스명) emit — controls 가 좌측 변형 형상 갱신에 사용 (Phase 4-B)
    case_changed = pyqtSignal(str)
    # 컨투어 종류 변경 — controls 가 viewer 색칠 갱신
    contour_changed = pyqtSignal(str)
    # 물량산출 탭에서 정책/탭 전환 시 — viewer 색상 모드 갱신용
    # ('off' | 'ratio:1종' | 'ratio:2종' | 'ratio:3종')
    ratio_view_changed = pyqtSignal(str)
    # [2026-05-13 접합부조정탭] 다이어프램 토글은 JointEditPanel 로 이동.
    # (Phase 9) 자유도 묶음 색을 DOF 수별로 분리 표시 토글.
    dof_color_toggle = pyqtSignal(bool)
    # (2026-05-19) 기둥 층구간 분할 수 변경 — controls 가 재산정 트리거.
    column_segments_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = None
        self._model = None
        self._scene = None                            # populate_ops 에서 주입
        self._all_results: Dict[str, object] = {}    # 케이스명 → OpsResults
        self._comp_labels: Dict[int, str] = {}
        # 부재 호버 정보창용 — mid → 트리 풀이름 ("1층 a모듈 기둥 #123") (Phase 4)
        self._member_full_label: Dict[int, str] = {}
        # 물량산출 탭용 자료 (build_all_reports 결과)
        self._quantity_reports: Dict[str, object] = {}    # 정책 → QuantityReport
        self._current_policy: str = '1종'

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # ── 하중조합 콤보 (2026-05-13 표기 명확화) ──────────
        # 표시: "1.2D + 1.6L (중력)" 같은 실제 조합식. 내부 키는 itemData 에.
        # 비활성 항목(추후 구현 KBC 추가 조합) 도 함께 보여 사용자에게 향후
        # 지원 예정을 안내.
        case_row = QHBoxLayout()
        case_row.addWidget(QLabel("하중조합:"))
        self._case_combo = QComboBox()
        self._populate_case_combo_initial()
        self._case_combo.currentIndexChanged.connect(self._on_case_index_changed)
        case_row.addWidget(self._case_combo, stretch=1)
        root.addLayout(case_row)

        # ── 컨투어 종류 (Phase 4-C) ────────────────────────
        contour_row = QHBoxLayout()
        contour_row.addWidget(QLabel("컨투어:"))
        self._contour_combo = QComboBox()
        self._contour_combo.addItems([
            "끄기", "축력비 N/Pn", "휨모멘트비 M/Mn",
            "조합응력비 N/Pn+M/Mn", "변위 |u| (양단 노드 평균)",
        ])
        self._contour_combo.currentTextChanged.connect(self._on_contour_changed)
        contour_row.addWidget(self._contour_combo, stretch=1)
        root.addLayout(contour_row)

        # ── 시각화 토글 ─────────────────────────────────
        # [2026-05-13 접합부조정탭] 다이어프램 토글은 JointEditPanel 로 이동.
        # DOF 색 모드는 디버깅 시 자유도 묶음 종류를 확인할 때 ON.
        toggle_row = QHBoxLayout()
        self._dof_color_cb = QCheckBox("자유도 색")
        self._dof_color_cb.setToolTip(
            "자유도 묶음을 DOF 수별로 분리 색상 표시 "
            "(1·2·3·6 DOF)."
        )
        self._dof_color_cb.toggled.connect(self.dof_color_toggle.emit)
        toggle_row.addWidget(self._dof_color_cb)
        toggle_row.addStretch(1)
        root.addLayout(toggle_row)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        # ── 탭 1: 요약 ─────────────────────────────────
        self._summary_text = QTextEdit()
        self._summary_text.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setPointSize(9)
        self._summary_text.setFont(mono)
        self._tabs.addTab(self._summary_text, "요약")

        # ── 탭 2: 부재별 내부력 (ops 모델 트리만, 다이어그램은 좌측 3D 뷰로 통합) ──
        member_page = QWidget()
        member_lay = QVBoxLayout(member_page)
        member_lay.setContentsMargins(0, 0, 0, 0)

        # 부재 내부력 트리 — 5 컬럼 분리로 글씨 짤림 해소 + 단위 헤더 노출.
        # 그룹/컴포넌트/역할 행은 첫 컬럼에만 라벨, 부재 행은 5 컬럼 모두 채움.
        # 부재 내부력 트리 — 5 컬럼 분리.
        # 사용자 정책: 가로 스크롤 비활성. 글씨 짤리지 않게 패널 자체
        # minimumWidth 를 내용 폭에 맞춰 동적으로 늘림 (_fit_panel_to_tree).
        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(["부재", "L (mm)", "N (kN)", "V (kN)", "M (kN·m)"])
        self._tree.setIndentation(16)
        tf = QFont("Consolas")
        tf.setPointSize(9)
        self._tree.setFont(tf)
        hdr = self._tree.header()
        for c in range(5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        # 가로 스크롤 비활성 — 패널 폭이 내용에 맞춰 늘어나는 정책
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.setTextElideMode(Qt.ElideNone)
        self._tree.currentItemChanged.connect(self._on_tree_item_changed)
        member_lay.addWidget(self._tree)

        self._tabs.addTab(member_page, "부재별 내부력")

        # ── 탭 3: 물량산출 (2026-05-08 신규) ─────────────────
        self._build_quantity_tab()
        # 탭 전환 시 ratio_view 시그널 — 물량산출 탭 진입/이탈에 따라 색상 모드 변경
        self._tabs.currentChanged.connect(self._on_tab_changed)

    # ── [2026-05-11] 메인 탭 분리용 서브탭 가시성 토글 ─────
    def select_envelope_case(self):
        """(2026-05-19 작업 5) 콤보박스를 '지배조합' 으로 자동 전환.

        main_3d 가 물량 탭 진입 시 호출 — 사용자가 항상 envelope 기준 응력비를
        먼저 보게 된다. itemData == 'ENVELOPE' 인 행을 찾아 currentIndex 설정.
        """
        for i in range(self._case_combo.count()):
            if self._case_combo.itemData(i, _Qt.UserRole) == 'ENVELOPE':
                self._case_combo.setCurrentIndex(i)
                return

    def select_dl_case(self):
        """(2026-05-19 작업 5) 콤보박스를 D+L 로 되돌림.

        구조해석 탭으로 돌아갈 때 — 단일 케이스 기준 단면력을 보고 싶을 것.
        """
        for i in range(self._case_combo.count()):
            if self._case_combo.itemData(i, _Qt.UserRole) == 'D+L':
                self._case_combo.setCurrentIndex(i)
                return

    def set_visible_subtabs(self,
                            show_summary: bool = True,
                            show_member: bool = True,
                            show_quantity: bool = True) -> None:
        """메인 윈도우의 [구조해석]/[물량] 탭에서 서브탭만 선택적으로 노출.

        구조해석 탭 → 요약·내부력만 / 물량 탭 → 물량산출만.
        PyQt5 5.15+ `setTabVisible` 사용. 활성 인덱스도 보이는 첫 탭으로 보정.
        """
        flags = [show_summary, show_member, show_quantity]
        for i, vis in enumerate(flags):
            if hasattr(self._tabs, 'setTabVisible'):
                self._tabs.setTabVisible(i, bool(vis))
            else:
                # 폴백: PyQt5 < 5.15 → 탭 자체는 못 숨김. 비활성화만.
                self._tabs.setTabEnabled(i, bool(vis))
        # 현재 인덱스가 숨겨졌다면 보이는 첫 탭으로 이동
        if not flags[self._tabs.currentIndex()]:
            for i, vis in enumerate(flags):
                if vis:
                    self._tabs.setCurrentIndex(i)
                    break

    def _build_quantity_tab(self):
        """물량산출 탭 위젯 구성."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # 1) 정책 라디오 버튼
        radio_row = QHBoxLayout()
        radio_row.addWidget(QLabel("그룹 정책:"))
        self._policy_group = QButtonGroup(page)
        self._radio_1 = QRadioButton("1종 (전체 동일)")
        self._radio_2 = QRadioButton("2종 (기둥/보)")
        self._radio_3 = QRadioButton("3종 (기둥/보/캔틸)")
        self._radio_1.setChecked(True)
        for i, rb in enumerate([self._radio_1, self._radio_2, self._radio_3]):
            self._policy_group.addButton(rb, i)
            radio_row.addWidget(rb)
        radio_row.addStretch(1)
        self._policy_group.buttonClicked.connect(self._on_policy_changed)
        lay.addLayout(radio_row)

        # (2026-05-19) 기둥 층구간 분할 입력칸 — 1 이면 분할 없음.
        # 사용자가 K 를 입력 + 적용 → DP 로 강재 중량 최소 분할.
        from PyQt5.QtWidgets import QSpinBox, QPushButton
        seg_row = QHBoxLayout()
        seg_row.addWidget(QLabel("기둥 층구간:"))
        self._col_seg_spin = QSpinBox()
        self._col_seg_spin.setMinimum(1)
        self._col_seg_spin.setMaximum(20)
        self._col_seg_spin.setValue(1)
        self._col_seg_spin.setFixedWidth(60)
        self._col_seg_spin.setToolTip(
            "기둥 그룹을 층 범위 K 구간으로 분할 — DP 최적 분할로 강재 중량 최소.\n"
            "1 = 분할 없음(기존 동작). 1종 정책에서는 의미 없음."
        )
        seg_row.addWidget(self._col_seg_spin)
        self._col_seg_apply = QPushButton("적용")
        self._col_seg_apply.setFixedWidth(50)
        self._col_seg_apply.clicked.connect(self._on_col_seg_apply)
        seg_row.addWidget(self._col_seg_apply)
        seg_row.addStretch(1)
        lay.addLayout(seg_row)

        # 2) 그룹 채택 단면 표
        # (2026-05-19 수정) Stretch → ResizeToContents — 셀 내용 폭에 맞춰
        # 컬럼이 늘어나고, 패널 자동 넓힘 함수가 그 합을 minimumWidth 로 잡아
        # 헤더·셀 모두 ... 잘림이 발생하지 않게 한다.
        lay.addWidget(QLabel("그룹별 채택 단면:"))
        self._group_table = QTableWidget()
        self._group_table.setColumnCount(7)
        self._group_table.setHorizontalHeaderLabels([
            "그룹", "채택 단면", "최대 응력비", "NG", "본수", "총 길이(m)", "총 중량(ton)"
        ])
        gh = self._group_table.horizontalHeader()
        gh.setSectionResizeMode(QHeaderView.ResizeToContents)
        gh.setStretchLastSection(False)
        self._group_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._group_table.setMaximumHeight(160)
        self._group_table.setTextElideMode(Qt.ElideNone)
        self._group_table.setWordWrap(False)
        self._group_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self._group_table)

        # 3) 강재 본수표
        lay.addWidget(QLabel("강재 본수표:"))
        self._steel_table = QTableWidget()
        self._steel_table.setColumnCount(5)
        self._steel_table.setHorizontalHeaderLabels([
            "단면", "길이(mm)", "본수", "총 길이(m)", "총 중량(ton)"
        ])
        sh = self._steel_table.horizontalHeader()
        sh.setSectionResizeMode(QHeaderView.ResizeToContents)
        sh.setStretchLastSection(False)
        self._steel_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._steel_table.setMaximumHeight(220)
        self._steel_table.setTextElideMode(Qt.ElideNone)
        self._steel_table.setWordWrap(False)
        self._steel_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self._steel_table)

        # 4) 슬래브 부피 표
        lay.addWidget(QLabel("슬래브 부피 (정책 무관):"))
        self._slab_table = QTableWidget()
        self._slab_table.setColumnCount(6)
        self._slab_table.setHorizontalHeaderLabels([
            "슬래브 수", "면적(m²)", "두께(mm)", "부피(m³)", "철근비율", "철근중량(ton)"
        ])
        slh = self._slab_table.horizontalHeader()
        slh.setSectionResizeMode(QHeaderView.ResizeToContents)
        slh.setStretchLastSection(False)
        self._slab_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._slab_table.setRowCount(1)
        self._slab_table.setMaximumHeight(70)
        self._slab_table.setTextElideMode(Qt.ElideNone)
        self._slab_table.setWordWrap(False)
        self._slab_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self._slab_table)

        # 5) 부재별 응력비 트리 (2026-05-19 작업 3 — 평면 표 → 모듈/층/역할 계층)
        # 위계: [모듈 그룹]   예) "A 모듈"
        #         [층 모듈]   예) "1층 A 모듈"
        #           [역할]    예) "기둥" / "천장보" / "바닥보"
        #             [부재]  #mid + 길이 + 단면 + 응력비 + 지배항목
        # 그룹 노드 클릭 → 하위 mid 일괄 강조, 잎 클릭 → 단일 부재 강조.
        lay.addWidget(QLabel("부재별 응력비 (행 클릭 → 3D 강조):"))
        self._ratio_tree = QTreeWidget()
        self._ratio_tree.setColumnCount(5)
        self._ratio_tree.setHeaderLabels([
            "부재", "길이(mm)", "단면", "응력비", "지배 항목"
        ])
        self._ratio_tree.setIndentation(16)
        rt_f = QFont("Consolas")
        rt_f.setPointSize(9)
        self._ratio_tree.setFont(rt_f)
        rt_hdr = self._ratio_tree.header()
        for c in range(5):
            rt_hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        rt_hdr.setStretchLastSection(False)
        self._ratio_tree.setUniformRowHeights(True)
        self._ratio_tree.setAlternatingRowColors(True)
        self._ratio_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._ratio_tree.setTextElideMode(Qt.ElideNone)
        self._ratio_tree.currentItemChanged.connect(self._on_ratio_tree_changed)
        lay.addWidget(self._ratio_tree, stretch=1)

        self._tabs.addTab(page, "물량산출")
        self._quantity_tab_index = self._tabs.count() - 1

    # ── 외부 API ────────────────────────────────────────

    def populate_ops(self, report_text: str, ops_model, analysis_model,
                     comp_labels: Dict[int, str], ops_results=None,
                     all_results=None, scene=None):
        """OpenSees 결과로 패널을 채운다.

        all_results: {'D+L': OpsResults, 'Ex': ..., 'Ey': ..., 'Wx': ..., 'Wy': ...}
                     주어지면 케이스 콤보박스로 전환 가능.
        ops_results: 초기 표시 케이스 (보통 D+L). all_results 우선.
        scene: Scene 인스턴스 — 부재 트리 위계(컴포넌트 타입/위치/층) 라벨링용.
               없으면 폴백 표시 사용.
        """
        self._store = None
        self._model = analysis_model
        self._ops_model = ops_model
        self._ops_results = ops_results
        self._all_results = all_results or {}
        self._comp_labels = comp_labels
        self._scene = scene

        # 탭 1
        self._summary_text.setPlainText(report_text)

        # (2026-05-13) 콤보박스는 초기 _populate_case_combo_initial 가 이미
        # 모든 활성 5 + 비활성 항목을 만들었으므로 별도 갱신 불필요.
        # _all_results 에 누락된 케이스가 있으면 _on_case_index_changed 가
        # 무시 처리(가드).

        # 초기 표시 케이스
        active = ops_results
        if active is None and 'D+L' in self._all_results:
            active = self._all_results['D+L']

        # 탭 2: AnalysisMember 트리
        self._fill_member_tree_ops(analysis_model, comp_labels, active)
        # 부재력 있으면 부재 탭 디폴트로
        self._tabs.setCurrentIndex(1 if active is not None else 0)

    def _populate_case_combo_initial(self):
        """콤보박스 초기 항목 — 활성 5 종 + 비활성 추가 조합 (KBC 향후 구현용)."""
        model = QStandardItemModel(self._case_combo)
        # 활성 항목 — 표시명 + 내부 키 itemData
        for key, display in CASE_DISPLAY_MAP.items():
            item = QStandardItem(display)
            item.setData(key, _Qt.UserRole)
            item.setFlags(item.flags() | _Qt.ItemIsEnabled | _Qt.ItemIsSelectable)
            model.appendRow(item)
        # 구분 항목 (비활성, 표시만)
        sep_item = QStandardItem('─── 추후 구현 ───')
        sep_item.setFlags(_Qt.NoItemFlags)
        model.appendRow(sep_item)
        # 비활성 항목 — 선택 불가
        for label in CASE_DISABLED_ITEMS:
            item = QStandardItem(label)
            item.setData('', _Qt.UserRole)
            item.setFlags(_Qt.NoItemFlags)   # 비활성
            model.appendRow(item)
        self._case_combo.setModel(model)
        self._case_combo.setCurrentIndex(0)

    def _on_case_index_changed(self, idx: int):
        """콤보박스 변경 → 부재 트리 갱신 + 시그널 emit (좌측 변형 갱신용).

        (2026-05-19 작업 5) 'ENVELOPE' 가상 케이스 — 부재 트리/변형형상은
        D+L 결과를 그대로 쓰고, controls 측이 case_changed 를 받아 물량
        보고서를 envelope 모드로 재산정한다.
        """
        if idx < 0:
            return
        case_name = self._case_combo.itemData(idx, _Qt.UserRole)
        if not case_name:
            return  # 비활성 항목 (실제론 선택 안 되지만 안전 가드)
        if not self._all_results or self._model is None:
            return
        # ENVELOPE 는 실제 단면력 케이스가 없으므로 D+L 로 트리/변형형상 표시.
        view_case = 'D+L' if case_name == 'ENVELOPE' else case_name
        res = self._all_results.get(view_case)
        if res is None:
            return
        self._ops_results = res
        self._fill_member_tree_ops(self._model, self._comp_labels, res)
        # 좌측 변형 형상 갱신 + 물량 재산정용 시그널.
        self.case_changed.emit(case_name)

    def _on_case_changed(self, case_name: str):
        """레거시 — currentTextChanged 호환 시그니처. 현재 호출 자리 없음."""
        if not self._all_results or self._model is None:
            return
        res = self._all_results.get(case_name)
        if res is None:
            return
        self._ops_results = res
        self._fill_member_tree_ops(self._model, self._comp_labels, res)
        self.case_changed.emit(case_name)

    def _on_contour_changed(self, kind: str):
        self.contour_changed.emit(kind)

    def _fill_member_tree_ops(self, model, comp_labels, ops_results=None):
        """ops 모델용 부재 트리 — 가독성 개선 (2026-05-18).

        위계 3 단:
          [최상위]  타입+위치 라벨   예) "a모듈"
            [중간]  층+타입+위치     예) "1층 a모듈"
              [역할] 한글 role 묶음  예) "기둥"·"천장보"·"바닥보"·"장변보"·"단변보"
                [부재] #mid + L/N/V/M (5 컬럼)

        - 같은 (컴포넌트 타입, xy 위치) 끼리는 같은 알파벳 라벨 (a, b, c …) 부여
        - 같은 (타입, xy) 안에서 z 좌표 정렬 → 1층/2층/3층 라벨
        - 컴포넌트 타입 정렬: 모듈·패널·벽·캔틸·중간… RC 코어 계열 맨 뒤
        - 바닥패널 가장자리보 4 개는 길이 비교로 자동 장변보/단변보 분기
        - scene 미주입 시 폴백 — 옛 단일컬럼 평탄 구조
        """
        self._tree.clear()
        # Phase 4 — 풀이름 캐시 초기화 (호버 정보창에서 사용)
        self._member_full_label = {}
        if model is None:
            return
        mfs = ops_results.member_forces if ops_results is not None else {}
        scene = getattr(self, '_scene', None)

        # ── 폴백 — scene 없음 ────────────────────────────
        if scene is None:
            self._fill_member_tree_flat(model, comp_labels, mfs)
            return

        # ── 1) 컴포넌트를 타입별로 분류 + xy 위치 라벨 + 층 라벨 부여 ──
        # by_type[type_name] = [(cid, comp), ...]
        by_type: Dict[str, list] = defaultdict(list)
        for cid, comp in scene.components.items():
            if cid not in model.comp_to_members:
                continue
            if not model.comp_to_members[cid]:
                continue
            tname = TYPE_NAMES.get(comp.comp_type, str(comp.comp_type))
            by_type[tname].append((cid, comp))

        # comp_meta[cid] = (xy_label, floor_num, type_name)
        comp_meta: Dict[int, tuple] = {}
        for tname, items in by_type.items():
            xy_set = sorted({(int(round(float(c.position[0]))),
                              int(round(float(c.position[1]))))
                             for _, c in items})
            xy_to_label = {xy: _alpha_label(i) for i, xy in enumerate(xy_set)}
            z_set = sorted({int(round(float(c.position[2]))) for _, c in items})
            z_to_floor = {z: i + 1 for i, z in enumerate(z_set)}
            for cid, c in items:
                xy = (int(round(float(c.position[0]))),
                      int(round(float(c.position[1]))))
                z = int(round(float(c.position[2])))
                comp_meta[cid] = (xy_to_label[xy], z_to_floor[z], tname)

        # (2026-05-19 수정) 물량 탭(_fill_ratio_table) 이 같은 메타를 재사용
        # 하도록 인스턴스 속성으로 저장 — "a모듈" 이라는 라벨이 두 탭에서
        # 동일한 부재 집합을 가리키도록 보장.
        self._comp_meta = comp_meta

        # ── 2) 최상위 묶음 키 = (타입, xy 라벨) ───────────────
        # 자식 항목: (floor, cid, sub_mids).
        # 단일층 컴포넌트는 sub_mids = 그 컴포넌트의 모든 부재.
        # 수직 3 층 모듈은 cid 하나가 3 entries 로 펼쳐짐 — 부재 평균 z
        # 가 어느 층 슬롯(0~FLOOR_HEIGHT / FLOOR_HEIGHT~2H / 2H~3H) 에
        # 속하느냐로 1·2·3 층 분류.
        top_groups: Dict[tuple, list] = defaultdict(list)
        for cid, (xy_label, floor, tname) in comp_meta.items():
            comp = scene.components.get(cid)
            mid_list = list(model.comp_to_members.get(cid, []))
            if (comp is not None
                    and comp.comp_type == ComponentType.VERTICAL_MODULE):
                by_floor_sub: Dict[int, list] = defaultdict(list)
                for mid in mid_list:
                    m = model.members.get(mid)
                    if m is None:
                        continue
                    n1 = model.nodes.get(m.n1)
                    n2 = model.nodes.get(m.n2)
                    if n1 is None or n2 is None:
                        continue
                    avg_z = (float(n1.coord[2]) + float(n2.coord[2])) / 2.0
                    # z=0 → 1층, z=FLOOR_HEIGHT → 2층 (그 층의 바닥보),
                    # z=2*FLOOR_HEIGHT → 3층, z=옥상 → 3층.
                    f_idx = max(1, int((avg_z + 1.0) / FLOOR_HEIGHT) + 1)
                    by_floor_sub[f_idx].append(mid)
                for f_idx, mids in by_floor_sub.items():
                    top_groups[(tname, xy_label)].append(
                        (f_idx, cid, mids))
            else:
                top_groups[(tname, xy_label)].append(
                    (floor, cid, mid_list))

        sorted_top = sorted(
            top_groups.keys(),
            key=lambda k: (_comp_type_order_key(k[0]), k[1]),
        )

        # ── 3) 트리 구성 ─────────────────────────────────
        for (tname, xy_label) in sorted_top:
            top_text = f"{xy_label}{tname}"
            top_item = QTreeWidgetItem([top_text, '', '', '', ''])
            top_item.setData(0, Qt.UserRole, None)
            f = top_item.font(0); f.setBold(True); top_item.setFont(0, f)

            for floor, cid, mid_list in sorted(top_groups[(tname, xy_label)],
                                                key=lambda e: (e[0], e[1])):
                comp_text = f"{floor}층 {xy_label}{tname}"
                comp_item = QTreeWidgetItem([comp_text, '', '', '', ''])
                comp_item.setData(0, Qt.UserRole, None)

                # 역할별 묶음
                by_role_ko: Dict[str, list] = defaultdict(list)
                for mid in mid_list:
                    m = model.members.get(mid)
                    if m is None:
                        continue
                    role_ko = self._classify_role_ko(m, model, mid, cid)
                    by_role_ko[role_ko].append(mid)

                for role_ko in sorted(by_role_ko.keys(),
                                       key=_role_ko_order_key):
                    role_item = QTreeWidgetItem(
                        [role_ko, '', '', '', ''])
                    role_item.setData(0, Qt.UserRole, None)

                    for mid in sorted(by_role_ko[role_ko]):
                        m = model.members[mid]
                        L = model.get_member_length(mid)
                        mf = mfs.get(mid)
                        if mf is not None:
                            cols = [
                                f"#{mid}",
                                f"{L:.0f}",
                                f"{mf.N_max_abs/1000:.1f}",
                                f"{mf.V_max_abs/1000:.1f}",
                                f"{mf.M_max_abs/1e6:.1f}",
                            ]
                        else:
                            cols = [f"#{mid}", f"{L:.0f}", '', '', '']
                        leaf = QTreeWidgetItem(cols)
                        leaf.setData(0, Qt.UserRole, mid)
                        # 호버 정보창용 풀이름 캐시 (Phase 4) —
                        # 예: "1층 a모듈 / 기둥 #123"
                        self._member_full_label[mid] = (
                            f"{floor}층 {xy_label}{tname} / "
                            f"{role_ko} #{mid}")
                        # 수치 컬럼 우측 정렬
                        for c in range(1, 5):
                            leaf.setTextAlignment(
                                c, Qt.AlignRight | Qt.AlignVCenter)
                        role_item.addChild(leaf)
                    if role_item.childCount() > 0:
                        comp_item.addChild(role_item)

                if comp_item.childCount() > 0:
                    top_item.addChild(comp_item)

            if top_item.childCount() > 0:
                self._tree.addTopLevelItem(top_item)

        # 처음엔 최상위 + 층 단위만 펴고, 역할 묶음은 접어 둠 — 답답함 완화.
        self._tree.expandToDepth(1)
        self._fit_panel_to_tree()

    def _classify_role_ko(self, m, model, mid: int, cid: int) -> str:
        """부재 role 영어 → 한글 표시명. floor_edge_beam 은 길이로 장/단변 분기."""
        role = m.role
        if role != 'floor_edge_beam':
            return _ROLE_KO.get(role, role)
        # 같은 패널의 가장자리보 4 개 — 길이로 장변/단변 분류
        same_role_lens = []
        for x in model.comp_to_members.get(cid, []):
            mx = model.members.get(x)
            if mx is not None and mx.role == 'floor_edge_beam':
                same_role_lens.append((x, model.get_member_length(x)))
        if not same_role_lens:
            return '가장자리보'
        L_max = max(L for _, L in same_role_lens)
        L_min = min(L for _, L in same_role_lens)
        if (L_max - L_min) < 50.0:  # 거의 정사각형 패널
            return '가장자리보'
        L = model.get_member_length(mid)
        return '장변보' if L >= (L_max + L_min) / 2 else '단변보'

    def _fill_member_tree_flat(self, model, comp_labels, mfs):
        """scene 미주입 폴백 — 옛 단일컬럼식 평탄 트리."""
        for comp_id in sorted(model.comp_to_members.keys()):
            mid_list = model.comp_to_members[comp_id]
            if not mid_list:
                continue
            label = comp_labels.get(comp_id, f"컴포넌트 #{comp_id}")
            group = QTreeWidgetItem([label, '', '', '', ''])
            group.setData(0, Qt.UserRole, None)
            for mid in sorted(mid_list):
                m = model.members.get(mid)
                if m is None:
                    continue
                L = model.get_member_length(mid)
                mf = mfs.get(mid)
                # (2026-05-19 작업 4) m.kind / m.role 한글 표시.
                kind_ko = _KIND_KO.get(m.kind, m.kind)
                role_ko = _ROLE_KO.get(m.role, m.role)
                if mf is not None:
                    cols = [
                        f"#{mid} [{kind_ko}] {role_ko}",
                        f"{L:.0f}",
                        f"{mf.N_max_abs/1000:.1f}",
                        f"{mf.V_max_abs/1000:.1f}",
                        f"{mf.M_max_abs/1e6:.1f}",
                    ]
                else:
                    cols = [f"#{mid} [{kind_ko}] {role_ko}",
                            f"{L:.0f}", '', '', '']
                child = QTreeWidgetItem(cols)
                child.setData(0, Qt.UserRole, mid)
                for c in range(1, 5):
                    child.setTextAlignment(
                        c, Qt.AlignRight | Qt.AlignVCenter)
                group.addChild(child)
            if group.childCount() > 0:
                self._tree.addTopLevelItem(group)
        self._tree.expandToDepth(0)
        self._fit_panel_to_tree()

    # ── 호버 정보창용 lookup API (Phase 4) ───────────────────
    def get_member_full_label(self, mid: int) -> str:
        """부재 mid 의 트리 위계 풀이름. 캐시 미스 시 폴백."""
        s = self._member_full_label.get(int(mid))
        if s:
            return s
        # 폴백 — 트리가 아직 안 그려졌거나 mid 가 누락된 경우
        if self._model is None:
            return f"부재 #{mid}"
        m = self._model.members.get(int(mid))
        if m is None:
            return f"부재 #{mid}"
        return f"{m.role} #{mid}"

    def get_current_policy(self) -> str:
        """현재 물량산출 라디오 정책. 초기 상태 폴백은 '1종'."""
        return getattr(self, '_current_policy', None) or '1종'

    def get_member_group_name(self, mid: int) -> str:
        """현 정책 기준 부재의 단면 그룹명. 없으면 ''."""
        cache = (getattr(self, '_member_to_group_by_policy', {})
                 .get(self.get_current_policy(), {}))
        return cache.get(int(mid), '')

    def get_member_section_name(self, mid: int) -> str:
        """현 정책 기준 부재의 채택 단면 명칭. 없으면 ''."""
        gname = self.get_member_group_name(mid)
        if not gname:
            return ''
        rep = self._quantity_reports.get(self.get_current_policy())
        if rep is None:
            return ''
        sec = rep.sections_by_group.get(gname)
        return sec.name if sec is not None else ''

    def get_member_ratio(self, mid: int):
        """현 정책 기준 부재 응력비. 없으면 None."""
        rep = self._quantity_reports.get(self.get_current_policy())
        if rep is None:
            return None
        return rep.member_ratios.get(int(mid))

    def _fit_panel_to_quantity(self):
        """물량산출 탭의 표·트리 컨텐츠 폭에 맞춰 패널 minimumWidth 갱신.

        (2026-05-19) 작업 1 — 물량 탭도 글씨 짤림 시 우측 패널이 자동으로
        넓어지도록. 부재별 응력비 트리·4 개 표 + 헤더 라벨까지 모두 검사.

        (2026-05-19 수정) 트리가 접혀 있으면 ResizeToContents 가 보이는
        항목만 기준으로 짧게 잡힘 → "다 펼쳤을 때의 최대 폭" 을 기준으로
        잡아야 사용자가 펼쳐도 짤리지 않는다. 측정 직전 expandAll →
        측정 → 원래 펼침 깊이(depth 1)로 복원.
        """
        from PyQt5.QtWidgets import QLabel as _QLabel
        from PyQt5.QtGui import QFontMetrics as _QFontMetrics
        max_w = 0
        # 1) 부재별 응력비 트리 — 모두 펼친 뒤 측정.
        ratio_tree = getattr(self, '_ratio_tree', None)
        if ratio_tree is not None:
            ratio_tree.expandAll()
            for c in range(ratio_tree.columnCount()):
                ratio_tree.resizeColumnToContents(c)
            w_tree = sum(ratio_tree.columnWidth(c)
                          for c in range(ratio_tree.columnCount()))
            w_tree += 2 * ratio_tree.frameWidth() + 32  # 인덴트·여백
            vb_t = ratio_tree.verticalScrollBar()
            if vb_t is not None:
                w_tree += vb_t.sizeHint().width()
            max_w = max(max_w, w_tree)
            # 펼침 복원 — 사용자가 보던 기본 펼침 깊이 1 (최상위+층까지).
            ratio_tree.collapseAll()
            ratio_tree.expandToDepth(1)
        # 2) 4 개 표 컨텐츠 폭 — 헤더 텍스트와 셀 내용 중 큰 쪽 채택.
        from PyQt5.QtGui import QFontMetrics as _QFM
        for table_attr in ('_group_table', '_steel_table',
                            '_slab_table'):
            t = getattr(self, table_attr, None)
            if t is None:
                continue
            t.resizeColumnsToContents()
            # 헤더 글씨 폭 직접 측정 → 컬럼 폭이 헤더보다 작으면 강제 확장.
            hdr = t.horizontalHeader()
            fm_hdr = _QFM(hdr.font())
            for c in range(t.columnCount()):
                hi = t.horizontalHeaderItem(c)
                ht = hi.text() if hi is not None else ''
                hdr_w = fm_hdr.horizontalAdvance(ht) + 24  # 정렬·여백
                if t.columnWidth(c) < hdr_w:
                    t.setColumnWidth(c, hdr_w)
            w = sum(t.columnWidth(c) for c in range(t.columnCount()))
            w += 2 * t.frameWidth() + 24
            vb = t.verticalScrollBar()
            if vb is not None:
                w += vb.sizeHint().width()
            max_w = max(max_w, w)
        # 3) 페이지 안에 살아 있는 모든 QLabel 의 자연 폭도 검사.
        quantity_page = None
        if hasattr(self, '_quantity_tab_index'):
            quantity_page = self._tabs.widget(self._quantity_tab_index)
        if quantity_page is not None:
            for lab in quantity_page.findChildren(_QLabel):
                fm = _QFontMetrics(lab.font())
                w = fm.horizontalAdvance(lab.text()) + 12
                max_w = max(max_w, w)
        panel_min = max(380, max_w + 12)
        self.setMinimumWidth(panel_min)
        self.updateGeometry()

    def _fit_panel_to_tree(self):
        """트리 컬럼 폭 합계에 맞춰 패널의 minimumWidth 를 갱신.

        사용자 정책: 가로 스크롤 비활성 + 내용 길이에 따라 도크 폭이
        자동 확장. 내용 짤림이 발생하지 않을 만큼만 최소폭으로 잡고,
        사용자가 마우스로 더 넓히는 것은 허용.

        - 빈 트리: 최소 200 px 정도로 작게 유지.
        - 내용 있음: 각 컬럼 ResizeToContents 폭 합 + 인덴트·여백.
        """
        # 컬럼별 ResizeToContents 폭은 setHeader 시점에 갱신되지만,
        # 확실하게 다시 한번 강제.
        for c in range(self._tree.columnCount()):
            self._tree.resizeColumnToContents(c)
        col_total = sum(self._tree.columnWidth(c)
                        for c in range(self._tree.columnCount()))
        # 인덴트·프레임·세로 스크롤바 여유.
        frame_pad = 2 * self._tree.frameWidth() + 24
        vbar = self._tree.verticalScrollBar()
        if vbar is not None and vbar.isVisible():
            frame_pad += vbar.sizeHint().width()
        tree_min = max(200, col_total + frame_pad)
        # 패널 자체 minimumWidth = 트리 minWidth + 레이아웃 마진.
        self._tree.setMinimumWidth(tree_min)
        panel_min = tree_min + 12
        self.setMinimumWidth(panel_min)
        self.updateGeometry()

    def clear(self):
        """패널 내용을 비운다."""
        self._store = None
        self._model = None
        self._summary_text.clear()
        self._tree.clear()

    # ── 탭 2: 부재 트리 (ops 모델 단일 소스) ───────────────

    def _on_tree_item_changed(self, current, _previous):
        """트리 선택 변경 → 단일 부재 / 그룹 모두 처리.

        잎(부재 행) 선택: member_selected(mid) — 기존 그대로
        그룹(모듈·층·역할) 선택: members_selected(mids 리스트) — 하위 leaf 모두 강조
        """
        if current is None:
            return
        mid = current.data(0, Qt.UserRole)
        if mid is not None:
            # 단일 부재 행
            self.member_selected.emit(int(mid))
            return
        # 그룹 노드 — 하위 leaf 의 mid 모두 수집
        mids = self._collect_leaf_mids(current)
        if mids:
            self.members_selected.emit(mids)

    def _collect_leaf_mids(self, item) -> list:
        """주어진 트리 아이템의 모든 자손 중 mid 가 달려 있는 leaf 의 mid 리스트."""
        out: list = []
        stack = [item]
        while stack:
            it = stack.pop()
            for i in range(it.childCount()):
                ch = it.child(i)
                stack.append(ch)
                v = ch.data(0, Qt.UserRole)
                if v is not None:
                    out.append(int(v))
        return out

    # 레거시 _fill_member_tree, _member_label, _ensure_diagram, populate, 변형 형상 위젯
    # 모두 Phase 5 에서 폐기됨. ops 모델 기반 _fill_member_tree_ops 와 좌측 viewer 가 대체.

    # ── 물량산출 탭 (2026-05-08) ─────────────────────────

    def get_column_segments(self) -> int:
        """(2026-05-19) 사용자가 입력한 기둥 층구간 수. 1=분할 없음."""
        return int(self._col_seg_spin.value())

    def _on_col_seg_apply(self):
        """(2026-05-19) '적용' 버튼 → controls 가 받아 물량 재산정."""
        self.column_segments_changed.emit(self.get_column_segments())

    def populate_quantity(self, quantity_reports: Dict[str, object]):
        """물량산출 자료를 받아 탭을 채운다.

        quantity_reports: {'1종': QuantityReport, '2종': ..., '3종': ...}
        """
        self._quantity_reports = quantity_reports or {}
        if not self._quantity_reports:
            return
        self._refresh_quantity_views()

    def _on_policy_changed(self, button):
        """라디오버튼 변경."""
        idx = self._policy_group.id(button)
        self._current_policy = ('1종', '2종', '3종')[idx]
        self._refresh_quantity_views()
        # 물량산출 탭이 활성 상태이면 viewer 색상 모드 갱신
        if self._tabs.currentIndex() == getattr(self, '_quantity_tab_index', -1):
            self.ratio_view_changed.emit(f'ratio:{self._current_policy}')

    def _on_tab_changed(self, idx: int):
        """탭 전환 — 물량산출 탭이면 ratio:정책 emit, 아니면 off."""
        if idx == getattr(self, '_quantity_tab_index', -1) and self._quantity_reports:
            self.ratio_view_changed.emit(f'ratio:{self._current_policy}')
        else:
            self.ratio_view_changed.emit('off')

    def _on_ratio_tree_changed(self, current, _previous):
        """응력비 트리 선택 → 잎이면 단일 부재 emit, 그룹이면 하위 mid 묶음 emit."""
        if current is None:
            return
        mid = current.data(0, Qt.UserRole)
        if mid is not None:
            self.member_selected.emit(int(mid))
            return
        mids = self._collect_leaf_mids(current)
        if mids:
            self.members_selected.emit(mids)

    def _refresh_quantity_views(self):
        """현재 정책 기준으로 모든 표를 다시 채운다."""
        rep = self._quantity_reports.get(self._current_policy)
        if rep is None:
            return

        # 그룹 단면 표
        groups = rep.sections_by_group
        ng_set = set(rep.ng_groups)
        # 그룹별 본수/길이/중량 합계 계산 (member_to_group 역매핑이 design_result 안에 있음)
        # 단순화: steel_items 에서 단면 매칭으로 합산은 어려우니 전체 합 1줄 + 그룹별은
        # 채택 단면만 표시한다. 본수/길이/중량 합은 본수표 합계로 대체.
        self._group_table.setRowCount(len(groups))
        for r, (gname, sec) in enumerate(groups.items()):
            # (2026-05-19 작업 2) 그룹 본수 = 분할 전 원본 부재 수.
            # parent_member_id 별 root 를 set 으로 모아 count — 같은 부모를
            # 가진 분할 sub 들은 1 본으로 묶임. joint_rules sub 는 제외.
            roots = set()
            for mid, g in self._iter_member_to_group(rep):
                if g != gname:
                    continue
                m = self._model.members.get(mid) if self._model else None
                if m is None or getattr(m, 'is_split_sub', False):
                    continue
                root = (m.parent_member_id
                         if m.parent_member_id is not None else mid)
                roots.add(root)
            n_members = len(roots)
            # 그룹 길이/중량 — am 이 없으므로 본수표에서 단면 매칭으로 근사
            grp_len_m, grp_wt_ton = self._sum_group_steel(rep, gname)
            # (2026-05-19 수정) 영어 그룹키 → 한글 표시.
            # 동적 'columns_F{a}_{b}' / 'columns_F{a}' 도 한글로.
            gname_ko = _group_label_ko(gname)
            self._group_table.setItem(r, 0, QTableWidgetItem(gname_ko))
            self._group_table.setItem(r, 1, QTableWidgetItem(sec.name))
            ratio_item = QTableWidgetItem(self._safe_group_max_ratio(rep, gname))
            self._group_table.setItem(r, 2, ratio_item)
            ng_text = '⚠ NG' if gname in ng_set else ''
            ng_item = QTableWidgetItem(ng_text)
            if gname in ng_set:
                ng_item.setForeground(QBrush(QColor(220, 30, 30)))
            self._group_table.setItem(r, 3, ng_item)
            self._group_table.setItem(r, 4, QTableWidgetItem(f"{n_members}"))
            self._group_table.setItem(r, 5, QTableWidgetItem(f"{grp_len_m:.2f}"))
            self._group_table.setItem(r, 6, QTableWidgetItem(f"{grp_wt_ton:.4f}"))

        # 강재 본수표
        items = rep.steel_items
        self._steel_table.setRowCount(len(items))
        for r, it in enumerate(items):
            sec_item = QTableWidgetItem(it.section_name)
            if it.is_total:
                f = sec_item.font(); f.setBold(True); sec_item.setFont(f)
            self._steel_table.setItem(r, 0, sec_item)
            len_text = f"{it.length_mm:.0f}" if not it.is_total else ""
            self._steel_table.setItem(r, 1, QTableWidgetItem(len_text))
            self._steel_table.setItem(r, 2, QTableWidgetItem(f"{it.count}"))
            self._steel_table.setItem(r, 3, QTableWidgetItem(f"{it.total_length_m:.2f}"))
            self._steel_table.setItem(r, 4, QTableWidgetItem(f"{it.total_weight_ton:.4f}"))
            if it.is_total:
                for c in range(5):
                    cell = self._steel_table.item(r, c)
                    if cell is not None:
                        f = cell.font(); f.setBold(True); cell.setFont(f)

        # 슬래브 표
        s = rep.slab
        self._slab_table.setItem(0, 0, QTableWidgetItem(f"{s.n_slabs}"))
        self._slab_table.setItem(0, 1, QTableWidgetItem(f"{s.total_area_m2:.2f}"))
        self._slab_table.setItem(0, 2, QTableWidgetItem(f"{s.thickness_mm:.0f}"))
        self._slab_table.setItem(0, 3, QTableWidgetItem(f"{s.total_volume_m3:.3f}"))
        self._slab_table.setItem(0, 4, QTableWidgetItem(f"{s.rebar_ratio*100:.2f}%"))
        self._slab_table.setItem(0, 5, QTableWidgetItem(f"{s.rebar_weight_ton:.3f}"))

        # 부재별 응력비 트리 (작업 3)
        self._fill_ratio_table(rep)
        # 물량 탭 컨텐츠 폭에 맞춰 패널 너비 자동 조정 (작업 1)
        self._fit_panel_to_quantity()

    def _fill_ratio_table(self, rep):
        """부재별 응력비 트리 — 구조해석탭(_fill_member_tree_ops) 과
        완전히 동일한 위계로 그린다 (2026-05-19 작업 3).

        - 묶음 키, 정렬, 라벨 양식, VERTICAL_MODULE 분리 로직 모두 동일.
        - 차이는 잎 행의 컬럼값뿐: 단면력 N/V/M 대신 단면명/응력비/지배항목.
        """
        if self._model is None or self._ratio_tree is None:
            return
        self._ratio_tree.clear()
        ratios = rep.member_ratios
        critical = rep.member_critical
        scene = getattr(self, '_scene', None)
        model = self._model

        if scene is None:
            return

        # ── 1) 구조해석 탭과 동일한 by_type / comp_meta 산정 ──
        by_type: Dict[str, list] = defaultdict(list)
        for cid, comp in scene.components.items():
            if cid not in model.comp_to_members:
                continue
            if not model.comp_to_members[cid]:
                continue
            tname = TYPE_NAMES.get(comp.comp_type, str(comp.comp_type))
            by_type[tname].append((cid, comp))

        comp_meta: Dict[int, tuple] = dict(getattr(self, '_comp_meta', {}) or {})
        if not comp_meta:
            for tname, items in by_type.items():
                xy_set = sorted({(int(round(float(c.position[0]))),
                                  int(round(float(c.position[1]))))
                                 for _, c in items})
                xy_to_label = {xy: _alpha_label(i)
                               for i, xy in enumerate(xy_set)}
                z_set = sorted({int(round(float(c.position[2])))
                                for _, c in items})
                z_to_floor = {z: i + 1 for i, z in enumerate(z_set)}
                for cid, c in items:
                    xy = (int(round(float(c.position[0]))),
                          int(round(float(c.position[1]))))
                    z = int(round(float(c.position[2])))
                    comp_meta[cid] = (xy_to_label[xy], z_to_floor[z], tname)

        # ── 2) top_groups[(tname, xy_label)] — 구조해석탭과 동일 ──
        top_groups: Dict[tuple, list] = defaultdict(list)
        for cid, (xy_label, floor, tname) in comp_meta.items():
            comp = scene.components.get(cid)
            mid_list = list(model.comp_to_members.get(cid, []))
            if (comp is not None
                    and comp.comp_type == ComponentType.VERTICAL_MODULE):
                by_floor_sub: Dict[int, list] = defaultdict(list)
                for mid in mid_list:
                    m = model.members.get(mid)
                    if m is None:
                        continue
                    n1 = model.nodes.get(m.n1)
                    n2 = model.nodes.get(m.n2)
                    if n1 is None or n2 is None:
                        continue
                    avg_z = (float(n1.coord[2]) + float(n2.coord[2])) / 2.0
                    f_idx = max(1, int((avg_z + 1.0) / FLOOR_HEIGHT) + 1)
                    by_floor_sub[f_idx].append(mid)
                for f_idx, mids in by_floor_sub.items():
                    top_groups[(tname, xy_label)].append(
                        (f_idx, cid, mids))
            else:
                top_groups[(tname, xy_label)].append(
                    (floor, cid, mid_list))

        sorted_top = sorted(
            top_groups.keys(),
            key=lambda k: (_comp_type_order_key(k[0]), k[1]),
        )

        # ── 3) 트리 빌드 — 구조해석탭과 동일 ─────────────────
        for (tname, xy_label) in sorted_top:
            top_text = f"{xy_label}{tname}"
            top_item = QTreeWidgetItem([top_text, '', '', '', ''])
            top_item.setData(0, Qt.UserRole, None)
            f = top_item.font(0); f.setBold(True); top_item.setFont(0, f)

            for floor, cid, mid_list in sorted(top_groups[(tname, xy_label)],
                                                key=lambda e: (e[0], e[1])):
                comp_text = f"{floor}층 {xy_label}{tname}"
                comp_item = QTreeWidgetItem([comp_text, '', '', '', ''])
                comp_item.setData(0, Qt.UserRole, None)

                # 역할별 묶음
                by_role_ko: Dict[str, list] = defaultdict(list)
                for mid in mid_list:
                    m = model.members.get(mid)
                    if m is None:
                        continue
                    role_ko = self._classify_role_ko(m, model, mid, cid)
                    by_role_ko[role_ko].append(mid)

                for role_ko in sorted(by_role_ko.keys(),
                                       key=_role_ko_order_key):
                    role_item = QTreeWidgetItem(
                        [role_ko, '', '', '', ''])
                    role_item.setData(0, Qt.UserRole, None)

                    # 잎: 응력비 내림차순 (위험 부재 먼저)
                    leaf_rows = []
                    for mid in by_role_ko[role_ko]:
                        m = model.members[mid]
                        L = model.get_member_length(mid)
                        gname = self._lookup_member_group(rep, mid)
                        sec = rep.sections_by_group.get(gname)
                        sec_name = sec.name if sec is not None else ''
                        ratio = ratios.get(mid, 0.0)
                        crit_en = critical.get(mid, '')
                        crit_ko = _CRITICAL_KO.get(crit_en, crit_en)
                        leaf_rows.append(
                            (mid, L, sec_name, ratio, crit_ko))
                    leaf_rows.sort(key=lambda r: r[3], reverse=True)

                    for (mid, L, sec_name, ratio, crit_ko) in leaf_rows:
                        cols = [
                            f"#{mid}",
                            f"{L:.0f}",
                            sec_name,
                            f"{ratio:.3f}",
                            crit_ko,
                        ]
                        leaf = QTreeWidgetItem(cols)
                        leaf.setData(0, Qt.UserRole, int(mid))
                        color = _ratio_to_qcolor(ratio)
                        brush = QBrush(color)
                        for c in range(5):
                            leaf.setBackground(c, brush)
                        for c in (1, 3):
                            leaf.setTextAlignment(
                                c, Qt.AlignRight | Qt.AlignVCenter)
                        role_item.addChild(leaf)
                    if role_item.childCount() > 0:
                        comp_item.addChild(role_item)

                if comp_item.childCount() > 0:
                    top_item.addChild(comp_item)

            if top_item.childCount() > 0:
                self._ratio_tree.addTopLevelItem(top_item)

        # 처음엔 최상위 + 층만 펼치고 역할은 접어 둠 — 구조해석탭과 동일.
        self._ratio_tree.expandToDepth(1)

    # 헬퍼 ─────────────────────────────────────────────
    def _iter_member_to_group(self, rep):
        """rep.member_ratios 키 + sections_by_group 의 한정으로 (mid, gname) 생성.

        rep 에 member_to_group 가 직접 노출되지 않아 우회: ratio 표가 가진 mid 들을
        다시 design 결과에 의존하지 않고 group 정보 보존을 위해 별도 헬퍼.
        실제로는 build_quantity_report 에서 design_result.member_to_group 를
        rep 에 옮겨 두는 편이 깔끔. 여기서는 캐시가 없으니 0 반환 후
        populate_quantity_full 에서 외부 주입.
        """
        # 외부에서 주입한 member_to_group 캐시 사용
        cache = getattr(self, '_member_to_group_by_policy', {}).get(self._current_policy)
        if not cache:
            return iter([])
        return cache.items()

    def _lookup_member_group(self, rep, mid: int) -> str:
        cache = getattr(self, '_member_to_group_by_policy', {}).get(self._current_policy, {})
        return cache.get(mid, '')

    def _safe_group_max_ratio(self, rep, gname: str) -> str:
        """rep 에 group max_ratio 보관이 없어 ratio 사전에서 그룹 부재만 추려 max."""
        cache = getattr(self, '_member_to_group_by_policy', {}).get(self._current_policy, {})
        ratios = [rep.member_ratios[mid] for mid, g in cache.items()
                  if g == gname and mid in rep.member_ratios]
        if not ratios:
            return '-'
        return f"{max(ratios):.3f}"

    def _sum_group_steel(self, rep, gname: str):
        """그룹별 길이·중량 합 — member_to_group 캐시 + members 길이 사용.

        (2026-05-19 작업 2) is_split_sub 부재는 원본이 자중을 잡고 있어 제외.
        host 분할로 만들어진 sub 들은 같은 parent_root 로 묶여 길이 자체는
        조각 합 = 원본 길이라 정상 합산.
        """
        if self._model is None:
            return 0.0, 0.0
        cache = getattr(self, '_member_to_group_by_policy', {}).get(self._current_policy, {})
        sec = rep.sections_by_group.get(gname)
        if sec is None:
            return 0.0, 0.0
        total_len_mm = 0.0
        for mid, g in cache.items():
            if g != gname:
                continue
            m = self._model.members.get(mid)
            if m is None:
                continue
            if getattr(m, 'is_split_sub', False):
                continue
            total_len_mm += self._model.get_member_length(mid)
        total_len_m = total_len_mm / 1000.0
        total_wt_ton = sec.weight_per_m_kg * total_len_m / 1000.0
        return total_len_m, total_wt_ton

    def populate_quantity_full(self, quantity_reports: Dict[str, object],
                               member_to_group_by_policy: Dict[str, Dict[int, str]]):
        """quantity_reports + 정책별 member_to_group 캐시 함께 주입.

        외부 호출 측에서 design_results 의 member_to_group 를 정책별로 모아
        넘긴다. 이게 있어야 그룹 단면표·부재별 응력비 표의 그룹 컬럼이 채워진다.
        """
        self._quantity_reports = quantity_reports or {}
        self._member_to_group_by_policy = member_to_group_by_policy or {}
        if self._quantity_reports:
            self._refresh_quantity_views()


# ── 모듈 수준 헬퍼 ─────────────────────────────────────────
# (2026-05-12 #024/#029) 3D 뷰 `_ratio_to_5band` 와 색·경계 통일.
# 3D 와 표 패널이 같은 응력비에 같은 색을 보여줘 사용자 혼동 제거.
_RATIO_COLOR_BANDS = [
    # (upper_bound_strict_less, (r, g, b))   응력비 < bound 이면 그 색
    (0.30, (51, 102, 255)),      # 파랑 (#3366FF) — 비경제
    (0.60, (102, 204, 255)),     # 하늘 (#66CCFF) — 여유
]
# (≤ bound 인 색)
_RATIO_COLOR_BANDS_LE = [
    (0.85, (77, 230, 77)),       # 초록 (#4DE64D) — 적정
    (1.00, (255, 153, 0)),       # 주황 (#FF9900) — 한계
]
_RATIO_COLOR_NG = (255, 51, 51)  # 빨강 (#FF3333) — NG


def _ratio_to_qcolor(ratio: float) -> QColor:
    """응력비 → 5단계 색상 (3D 뷰 `_ratio_to_5band` 와 동일 경계/색)."""
    for upper, rgb in _RATIO_COLOR_BANDS:
        if ratio < upper:
            return QColor(*rgb)
    for upper, rgb in _RATIO_COLOR_BANDS_LE:
        if ratio <= upper:
            return QColor(*rgb)
    return QColor(*_RATIO_COLOR_NG)
