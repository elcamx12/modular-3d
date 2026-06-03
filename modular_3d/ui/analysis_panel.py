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
    QTreeWidget, QTreeWidgetItem, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QRadioButton, QButtonGroup,
    QSplitter, QHeaderView, QAbstractItemView, QSizePolicy, QStyle,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QBrush, QStandardItem, QStandardItemModel
from PyQt5.QtCore import Qt as _Qt

# 한글 라벨 매핑 — 모델 단일 진실 원천 재사용
from modular_3d.model import TYPE_NAMES, ComponentType  # noqa: E402
from modular_3d.카탈로그.geometry import FLOOR_HEIGHT  # noqa: E402
from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded  # noqa: E402


# ── 종합탭 톤 디자인 토큰 ─────────────────────────────────
_PAGE_BG     = "#EDF2F7"
_CARD_BG     = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEAD_FG     = "#1F4E79"
_BODY_FG     = "#1F2A37"
_SUB_FG      = "#5B6573"

# 패널 전체 스타일시트 — 구조해석·물량·운송 공유.
# - QLabel(묻는 글, 예: "하중조합:", "그룹 정책:") → Paperlogy
# - QComboBox/표/트리(값·데이터) → Freesentation, 흰 카드 톤
# - 트리는 숫자 정렬 위해 별도 Consolas setFont 유지(스타일시트보다 우선).
_ANALYSIS_QSS = (
    f"AnalysisPanel {{ background: {_PAGE_BG}; }}"
    "QLabel {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 18px; font-weight: 700; color: {_HEAD_FG};"
    " background: transparent; }"
    "QComboBox {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 18px; color: {_BODY_FG}; padding: 6px 10px;"
    f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
    " background: white; min-height: 28px; }"
    "QRadioButton {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 18px; font-weight: 700; color: {_HEAD_FG};"
    " background: transparent; }"
    "QTreeWidget, QTableWidget {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" color: {_BODY_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 8px; }"
    "QHeaderView::section {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 17px; font-weight: 700; color: {_HEAD_FG};"
    f" background: {_PAGE_BG}; border: none;"
    f" border-bottom: 1px solid {_CARD_BORDER}; padding: 6px 8px;"
    " }"
)


class _AlignedHeader(QHeaderView):
    """컬럼별 헤더 텍스트 정렬을 *직접 그려* 보장하는 헤더.

    [2026-06-02] 스타일시트(QHeaderView::section)가 적용되면 Qt 는 헤더의
    setTextAlignment(role) 을 무시하고 라벨을 왼쪽으로 그린다. QSS `text-align`
    은 QHeaderView 에 효과가 없다(QPushButton/QProgressBar 전용). 그래서 부재
    트리의 숫자 열(L·N·V·M) 헤더가 아래 값(오른쪽 정렬)과 어긋났다.
    → 기본 그리기로 배경/하단선을 그린 뒤, 라벨 영역을 헤더 배경색으로 덮고
       지정 정렬로 다시 그려서 스타일시트와 무관하게 정렬을 강제한다.
    """

    def __init__(self, parent=None, *, font_px: int = 17):
        super().__init__(Qt.Horizontal, parent)
        self._aligns: Dict[int, int] = {}
        self._hbg = QColor(_PAGE_BG)        # 헤더 배경(= QSS background)
        self._hfg = QColor(_HEAD_FG)        # 헤더 글자색(= QSS color)
        self._hline = QColor(_CARD_BORDER)  # 헤더 하단 경계선(= QSS border-bottom)
        # QSS 와 동일한 헤더 폰트(Paperlogy Bold) — 직접 그릴 때 일치시킴.
        #   font_px 로 패널별 헤더 크기를 맞춘다(구조해석 17 / 물량 14 등).
        self._hfont = QFont(F_HEAD)
        self._hfont.setPixelSize(font_px)
        self._hfont.setBold(True)

    def set_column_alignment(self, col: int, align: int) -> None:
        self._aligns[col] = int(align)
        self.updateSection(col)

    def paintSection(self, painter, rect, logicalIndex):  # noqa: N802
        # [2026-06-02] 기본 그리기(super)를 호출하지 않고 배경·하단선·라벨을 *전부
        #   직접* 그린다. 이전엔 super 가 라벨을 왼쪽으로 그린 뒤 덮어쓰려 했으나
        #   그 덮기·재출력이 화면에 반영되지 않아(헤더가 계속 왼쪽으로 보임) 방식을
        #   바꿈. 직접 그리면 경쟁하는 왼쪽 라벨이 없어 지정 정렬로만 그려진다.
        painter.save()
        # 배경(= QSS background)
        painter.fillRect(rect, self._hbg)
        # 하단 1px 경계선(= QSS border-bottom)
        painter.setPen(self._hline)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        # 라벨 — 컬럼별 지정 정렬(없으면 왼쪽)
        model = self.model()
        text = model.headerData(logicalIndex, Qt.Horizontal, Qt.DisplayRole) \
            if model is not None else None
        if text not in (None, ""):
            align = self._aligns.get(logicalIndex, int(Qt.AlignLeft))
            # 숫자 셀(QStyledItemDelegate)과 같은 좌우 여백(textMargin)을 써서
            #   헤더 글자 오른쪽 끝과 숫자 오른쪽 끝(일의 자리)을 같은 선에 맞춘다.
            tm = self.style().pixelMetric(QStyle.PM_FocusFrameHMargin, None, self) + 1
            painter.setPen(self._hfg)
            painter.setFont(self._hfont)
            painter.drawText(rect.adjusted(tm, 0, -tm, 0),
                             int(align) | int(Qt.AlignVCenter), str(text))
        painter.restore()


# ─────────────────────────────────────────────────────────
# 부재별 내부력 트리 가독성 매핑 (2026-05-18)
# 사용자 보고: 영어 role 라벨 + 좁은 단일컬럼에서 글씨 짤림.
# → 한국어 role 매핑 + 컴포넌트 위계(타입+xy위치+층) + 5 컬럼 분리.
# ─────────────────────────────────────────────────────────

# [2026-05-31 물량탭 Phase 1] 역할 한글 매핑·분류는 analysis/member_roles.py 로
# 추출(물량 분해기와 공유). 기존 이름은 별칭으로 유지 — 본 파일 호출부 회귀 방지.
from modular_3d.analysis.member_roles import (  # noqa: E402
    ROLE_KO as _ROLE_KO,
    ROLE_KO_ORDER as _ROLE_KO_ORDER,
    role_ko_order_key as _role_ko_order_key,
    classify_role_ko as _classify_role_ko_fn,
)

# 컴포넌트 타입(한글) 정렬 — RC 코어 계열을 맨 아래로.
_COMP_TYPE_ORDER = [
    '모듈', '수직 3층 모듈', '바닥패널', '벽패널',
    '캔틸레버보', '캔틸레버슬래브', '중간보', '중간기둥',
    'RC 코어', '코어 슬래브',
]


from modular_3d._utils.format import alpha_label as _alpha_label


# _role_ko_order_key 는 member_roles.role_ko_order_key 별칭(상단 import)으로 대체됨.


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
# [2026-05-30] 실제 KDS 하중조합으로 확장. 지진·풍 조합은 ± 양방향을 내부에서
# 포락(envelope)하므로 항목 자체는 하나. 내부 키는 ops_solver.solve_all_cases
# 의 반환 dict 키와 정확히 일치해야 한다.
CASE_DISPLAY_MAP = {
    'ENVELOPE': '지배조합 (전체 조합 envelope)',
    '1.4D':  '1.4D (자중 단독)',
    'D+L':   '1.2D + 1.6L (중력)',
    'Ex':    '1.2D + 1.0L ± 1.0Ex (X 지진)',
    'Ey':    '1.2D + 1.0L ± 1.0Ey (Y 지진)',
    'Wx':    '1.2D + 1.0L ± 1.0Wx (X 풍하중)',
    'Wy':    '1.2D + 1.0L ± 1.0Wy (Y 풍하중)',
    'Ex_OT': '0.9D ± 1.0Ex (X 지진 전도방지)',
    'Ey_OT': '0.9D ± 1.0Ey (Y 지진 전도방지)',
    'Wx_OT': '0.9D ± 1.0Wx (X 풍 전도방지)',
    'Wy_OT': '0.9D ± 1.0Wy (Y 풍 전도방지)',
}
# 표시명 → 내부 키 역매핑 (사용자 선택 시).
CASE_DISPLAY_TO_KEY = {v: k for k, v in CASE_DISPLAY_MAP.items()}

# [2026-05-30] 설하중 포함 조합은 콤보에서 완전히 제외(표기조차 안 함).
CASE_DISABLED_ITEMS: list = []


class AnalysisPanel(QWidget):
    """F6 구조해석 결과를 표시하는 도킹 패널."""

    # 트리에서 부재(잎)를 클릭하면 단일 member_id 를 emit
    member_selected = pyqtSignal(int)
    # 트리에서 그룹 노드(모듈/층/역할 묶음)를 클릭하면 하위 mid 리스트 emit (2026-05-18)
    members_selected = pyqtSignal(list)
    # 케이스 콤보박스 변경 시 (케이스명) emit — controls 가 좌측 변형 형상 갱신에 사용 (Phase 4-B)
    case_changed = pyqtSignal(str)
    # [2026-05-30] 컨투어 종류 콤보·자유도 색 토글 제거 — 관련 시그널도 삭제.
    #   (응력비 색칠은 물량 탭 ratio_view_changed 가 별개로 담당 — 유지.)
    # 물량산출 탭에서 정책/탭 전환 시 — viewer 색상 모드 갱신용
    # ('off' | 'ratio:1종' | 'ratio:2종' | 'ratio:3종')
    ratio_view_changed = pyqtSignal(str)
    # [2026-05-13 접합부조정탭] 다이어프램 토글은 JointEditPanel 로 이동.
    # (2026-05-19) 기둥 층구간 분할 수 변경 — controls 가 재산정 트리거.
    column_segments_changed = pyqtSignal(int)
    # (2026-05-19 Phase 6) 정책 라디오 변경 — 운송탭 캐시 무효화 트리거.
    policy_changed = pyqtSignal(str)

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
        # [P6b] 단면 설계 -1-1 변형 라벨(comp_id→'모듈A-1-1'). 비면 기본 'A-1' 사용.
        self._section_type_labels: Dict[int, str] = {}

        ensure_fonts_loaded()  # Paperlogy/Freesentation 등록 보장
        self.setStyleSheet(_ANALYSIS_QSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── 하중조합 콤보 (2026-05-13 표기 명확화) ──────────
        # [2026-05-27] 운송 sub-탭에서 hide 가능하도록 행 전체를 QWidget 으로 감쌈.
        self._case_row_widget = QWidget()
        case_row = QHBoxLayout(self._case_row_widget)
        case_row.setContentsMargins(0, 0, 0, 0)
        case_row.addWidget(QLabel("하중조합:"))
        self._case_combo = QComboBox()
        self._populate_case_combo_initial()
        self._case_combo.currentIndexChanged.connect(self._on_case_index_changed)
        case_row.addWidget(self._case_combo, stretch=1)
        root.addWidget(self._case_row_widget)

        # [2026-05-30] 컨투어 종류 콤보·자유도 색 토글 제거(사용자 요청).
        #   - 좌측 3D 뷰의 컨투어 색칠 기능 자체를 더 이상 노출하지 않는다.
        #   - 물량 탭의 응력비 색칠(ratio_view_changed)은 별개 경로라 그대로 유지.

        self._tabs = QTabWidget()
        # [2026-05-30 폭 분리 핵심] QTabWidget(내부 QStackedLayout)의 최소 폭은
        # 기본적으로 *모든 페이지 중 가장 넓은 페이지*(=물량산출)로 잡힌다. 그러면
        # 부재 내부력 탭을 봐도 패널이 물량 폭 밑으로 못 줄어 두 탭 폭이 같아진다.
        # 가로 정책을 Ignored 로 두면 탭 위젯의 폭 힌트를 부모가 무시 → 각 탭에서
        # setMinimumWidth(_fit_*)로 준 폭이 그대로 적용되어 구조해석/물량 폭이 분리.
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        self._tabs.setSizePolicy(_QSP.Ignored, _QSP.Expanding)
        root.addWidget(self._tabs)

        # [2026-05-30] '요약' 탭 제거 — 구조해석 탭에는 '부재별 내부력' 하나만
        # 남으므로, 탭 헤더(QTabBar)를 숨겨 탭 없이 표(트리)만 보이게 한다.
        # (물량·운송 탭은 별도 메인 탭에서 set_visible_subtabs 로 노출되며,
        #  그 맥락에서는 헤더가 어차피 단일 탭이라 시각적 영향 없음.)
        self._tabs.tabBar().hide()

        # ── 탭: 부재별 내부력 (ops 모델 트리만, 다이어그램은 좌측 3D 뷰로 통합) ──
        member_page = QWidget()
        member_lay = QVBoxLayout(member_page)
        member_lay.setContentsMargins(0, 0, 0, 0)

        # 부재 내부력 트리 — 5 컬럼 분리로 글씨 짤림 해소 + 단위 헤더 노출.
        # 그룹/컴포넌트/역할 행은 첫 컬럼에만 라벨, 부재 행은 5 컬럼 모두 채움.
        # 부재 내부력 트리 — 5 컬럼 분리.
        # 사용자 정책: 가로 스크롤 비활성. 글씨 짤리지 않게 패널 자체
        # minimumWidth 를 내용 폭에 맞춰 동적으로 늘림 (_fit_panel_to_tree).
        self._tree = QTreeWidget()
        # [2026-06-02] 정렬을 직접 그리는 전용 헤더로 교체 — 스타일시트가 적용된
        #   기본 헤더는 setTextAlignment 를 무시해 숫자 열 헤더가 왼쪽으로 그려진다.
        self._tree.setHeader(_AlignedHeader(self._tree))
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(["부재", "L (mm)", "N (kN)", "V (kN)", "M (kN·m)"])
        self._tree.setIndentation(16)
        # [2026-06-02] 부재 트리 글자 키움 9 → 12pt — 가독성 + 패널 폭 자동 확대
        #   (_fit_panel_to_tree 가 폰트 메트릭으로 폭 계산 → 3D 카드는 그만큼 줄어듦).
        tf = QFont("Consolas")
        tf.setPointSize(11)   # 사용자 요청으로 추가 축소 — M 컬럼 잘림 완화
        self._tree.setFont(tf)
        # [2026-06-02] 부재명(col0)은 폭 제한(Interactive) + 긴 이름 "…" 줄임.
        #   col0 가 내용폭만큼 넓어지면 L·N·V·M 이 오른쪽으로 밀려 M 이 잘렸다.
        #   col0 를 capped 폭으로 고정해 숫자 열을 왼쪽으로 당기고, 숫자 열 1~4 는
        #   내용폭(ResizeToContents)으로 값·헤더가 같은 열에 정렬되게 한다.
        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        for c in range(1, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(False)
        # [2026-06-02] 헤더 정렬을 숫자 셀과 맞춤 — 값은 오른쪽 정렬인데 헤더는
        #   왼쪽으로 그려져 어긋났다. 전용 헤더(_AlignedHeader)가 직접 그려서
        #   숫자 열(1~4) 은 오른쪽, 부재명(0) 은 왼쪽으로 정렬한다.
        if isinstance(hdr, _AlignedHeader):
            hdr.set_column_alignment(0, Qt.AlignLeft)
            for c in range(1, 5):
                hdr.set_column_alignment(c, Qt.AlignRight)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        # 가로 스크롤 비활성 — 패널 폭이 내용에 맞춰 늘어나는 정책
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 긴 부재명은 "…" 로 줄여 숫자 열을 침범하지 않게.
        self._tree.setTextElideMode(Qt.ElideRight)
        self._tree.currentItemChanged.connect(self._on_tree_item_changed)
        member_lay.addWidget(self._tree)

        # [2026-06-02 완전 분리] 부재력 트리는 탭 묶음(_tabs) 밖, root 에 직접 둔다.
        # 탭 묶음(QStackedLayout)은 숨은 페이지(물량/운송)의 최소폭까지 전체 최소폭에
        # 반영해, 부재력만 봐도 패널이 그 폭 밑으로 안 줄던 문제(우측 흰 공간)를 해소.
        root.addWidget(member_page)

        # ── 탭 3: 물량산출 (2026-05-08 신규) ─────────────────
        self._build_quantity_tab()
        # ── 탭 4: 운송 (2026-05-19 Phase 6 신규) ───────────
        self._build_transport_tab()
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
                            show_quantity: bool = True,
                            show_transport: bool = False) -> None:
        """메인 윈도우의 [구조해석]/[물량]/[운송] 탭에서 서브탭만 선택적으로 노출.

        구조해석 탭 → 요약·내부력만 / 물량 탭 → 물량산출만 / 운송 탭 → 운송만.
        PyQt5 5.15+ `setTabVisible` 사용. 활성 인덱스도 보이는 첫 탭으로 보정.

        [Phase 6 변경 — 2026-05-19]
        운송 sub-탭(4번째) 추가에 따라 show_transport 파라미터 추가. 기본 False
        라 기존 호출부(3 플래그) 도 그대로 호환. 운송 sub-탭이 아직 생성되지
        않은 환경(WebEngine 미설치 등)에서도 self._tabs.count() 안쪽만 처리.
        """
        # [2026-05-30] '요약' 탭 제거 — 실제 탭은 [부재별 내부력, 물량산출, 운송].
        # show_summary 인자는 호출부 호환 위해 유지하되 무시한다(요약 탭 없음).
        # [2026-06-02 완전 분리] 부재력 트리는 _tabs 밖(root 직접)이라 항상 표시.
        # _tabs 는 물량/운송 서브탭 전용(인덱스 0=물량, 1=운송). 둘 다 숨기면
        # (구조해석 탭) 묶음 자체를 hide 해 부재력 트리 폭에 전혀 영향 없게 한다.
        tab_flags = [show_quantity, show_transport]
        n = min(len(tab_flags), self._tabs.count())
        for i in range(n):
            vis = tab_flags[i]
            if hasattr(self._tabs, 'setTabVisible'):
                self._tabs.setTabVisible(i, bool(vis))
            else:
                self._tabs.setTabEnabled(i, bool(vis))
        self._tabs.setVisible(any(tab_flags[:n]))
        cur = self._tabs.currentIndex()
        if any(tab_flags[:n]) and (cur < 0 or cur >= n or not tab_flags[cur]):
            for i in range(n):
                if tab_flags[i]:
                    self._tabs.setCurrentIndex(i)
                    break
        # [2026-05-30] 폭 분리 — 진입한 메인 탭의 내용폭으로 패널 폭을 재고정.
        #   구조해석(부재 트리) / 물량 은 각자 fit 으로 min==max 고정,
        #   운송은 자유 폭이라 고정 해제(최대폭 풀기).
        if show_member:
            self._fit_panel_to_tree()
        elif show_quantity:
            self._fit_panel_to_quantity()
        elif show_transport:
            # 운송은 splitter 가 폭을 관리 — 패널·부모 pane 고정 모두 해제.
            self.setMaximumWidth(16777215)   # QWIDGETSIZE_MAX
            par = self.parentWidget()
            if par is not None:
                par.setMaximumWidth(16777215)
                par.setMinimumWidth(0)

    def set_visualization_controls_visible(self, show: bool) -> None:
        """[2026-05-27] 상단 *하중조합 / 컨투어 / 자유도 색* 위젯 + 탭바 일괄 hide.

        운송 sub-탭에선 이 컨트롤들이 의미 없어 사용자가 제거 요청. 다른 탭(구조
        해석·물량) 복귀 시 show=True 로 다시 표시.
        """
        # [2026-05-30] 컨투어·자유도 행 제거 — 하중조합 행만 남음.
        for attr in ("_case_row_widget",):
            w = getattr(self, attr, None)
            if w is not None:
                w.setVisible(show)
        # sub-탭 헤더(QTabBar)는 요약 탭 제거 후 항상 숨김 상태이므로 별도 토글 불필요.

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
        # [P5c] 단면 선택은 단면 설계 탭(스코프×입도)으로 일원화 → 물량 탭 정책
        # 라디오는 숨긴다(삭제 대신 컨테이너 숨김 = 가역). P5b 단일 출처에서 reports 는
        # 정책 무관 동일하므로 _current_policy 기본값('1종')으로 조회해도 정상 동작.
        from PyQt5.QtWidgets import QWidget as _QW
        self._policy_row_widget = _QW()
        self._policy_row_widget.setLayout(radio_row)
        self._policy_row_widget.setVisible(False)
        lay.addWidget(self._policy_row_widget)

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
        self._steel_table.setColumnCount(6)
        self._steel_table.setHorizontalHeaderLabels([
            "단면", "길이(mm)", "본수", "총 길이(m)", "총 중량(ton)", "금액(원)"
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

        # 4-B) 자재비(재료비) 총액표 — 강재 + 데크슬래브 + 콘크리트
        # [2026-05-24] 단가 DB(material_prices.json) × 물량. 코어·노무·경비 제외.
        lay.addWidget(QLabel("자재비 (재료비):"))
        self._matcost_table = QTableWidget()
        self._matcost_table.setColumnCount(4)
        self._matcost_table.setHorizontalHeaderLabels([
            "항목", "물량", "단가", "금액(원)"
        ])
        mch = self._matcost_table.horizontalHeader()
        mch.setSectionResizeMode(QHeaderView.ResizeToContents)
        mch.setStretchLastSection(True)
        self._matcost_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._matcost_table.setRowCount(4)   # 강재 / 데크슬래브 / 콘크리트 / 합계
        self._matcost_table.setMaximumHeight(150)
        self._matcost_table.setTextElideMode(Qt.ElideNone)
        self._matcost_table.setWordWrap(False)
        self._matcost_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self._matcost_table)

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

    # ── 운송탭 (Phase 6) ────────────────────────────────────
    def _build_transport_tab(self):
        """운송탭 — TransportTab 위젯 인스턴스를 4번째 탭으로 추가.

        분석 ⑤ 영역 ①~④ + ⑧ 진단(간단판). 영역 ⑤~⑦ 는 Phase 7·8 에서 확장.
        """
        # 지연 임포트: PyQtWebEngine 미설치 / OpenGL 컨텍스트 미설정 환경 보호.
        # ImportError 만이 아니라 RuntimeError (QtWebEngineWidgets must be
        # imported before QCoreApplication...) 도 같이 잡아서 placeholder 표시.
        try:
            from modular_3d.ui.transport_panel import TransportTab
        except Exception as e:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.addWidget(QLabel(
                f"운송탭 사용 불가:\n{type(e).__name__}: {e}\n\n"
                "조치:\n"
                "1. modular_3d/__main__.py 의 venv 자기-재실행 가드가 적용된 빌드로 다시 실행\n"
                "2. 여전히 같은 메시지면: pip install PyQtWebEngine matplotlib plotly"
            ))
            self._tabs.addTab(page, "운송")
            self._transport_tab = None
            self._transport_tab_index = self._tabs.count() - 1
            return
        self._transport_tab = TransportTab(self)
        # 운송탭의 시그널을 AnalysisPanel 이 forward — Controller 가 구독.
        self._transport_tab.transport_member_highlight.connect(
            self._on_transport_member_highlight
        )
        self._transport_tab.transport_blocked.connect(
            self._on_transport_blocked
        )
        self._tabs.addTab(self._transport_tab, "운송")
        self._transport_tab_index = self._tabs.count() - 1

    def populate_transport(self, design_results: Dict[str, object],
                            policy: str = "3종") -> None:
        """외부에서 design_result 와 정책을 운송탭에 주입.

        Controller 가 단면 산정 완료 시 호출.
        """
        if getattr(self, '_transport_tab', None) is None:
            return
        self._transport_tab.set_scene_and_model(self._scene, self._model)
        self._transport_tab.set_design_result(design_results, policy)

    def _on_transport_member_highlight(self, cids: list) -> None:
        """운송 회차표 더블클릭 → cid 리스트. Controller 가 구독 시 forward."""
        # members_selected 시그널을 통해 기존 강조 채널 재사용
        self.members_selected.emit(list(cids))

    def _on_transport_blocked(self, n: int) -> None:
        """운송 불가 항목 알림 — 사용자 시야로 격상.

        진단 영역에도 사유가 표시되지만 사용자가 즉시 못 보는 경우가 많아
        상단 상태바에 카운트만 짧게 띄운다. 자세한 사유는 진단 sub-탭에서.
        """
        try:
            sb = self.window().statusBar() if self.window() else None
            if sb is not None:
                sb.showMessage(
                    f'운송 결과 — 실을 수 없는 항목 {n}개. 진단 탭에서 사유 확인.',
                    8000)
        except Exception:
            pass

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

        # [2026-05-30] '요약' 탭 제거 — report_text 는 더 이상 표시하지 않는다
        # (인자는 호출부 호환 위해 유지). 부재별 내부력 트리만 채운다.

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
        # [2026-05-30] 요약 탭 제거로 부재별 내부력이 첫 탭(인덱스 0).
        self._tabs.setCurrentIndex(0)

    def _populate_case_combo_initial(self):
        """콤보박스 초기 항목 — 활성 5 종 + 비활성 추가 조합 (KDS 향후 구현용)."""
        model = QStandardItemModel(self._case_combo)
        # 활성 항목 — 표시명 + 내부 키 itemData
        for key, display in CASE_DISPLAY_MAP.items():
            item = QStandardItem(display)
            item.setData(key, _Qt.UserRole)
            item.setFlags(item.flags() | _Qt.ItemIsEnabled | _Qt.ItemIsSelectable)
            model.appendRow(item)
        # 비활성(추후 구현) 항목이 있을 때만 구분선 + 비활성 항목 추가.
        if CASE_DISABLED_ITEMS:
            sep_item = QStandardItem('─── 추후 구현 ───')
            sep_item.setFlags(_Qt.NoItemFlags)
            model.appendRow(sep_item)
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

    def _order_top_labels(self, scene, comp_meta, label_tname, keys):
        """최상위 라벨 정렬 — 종속 부재 라벨을 부모 독립부재 라벨 바로 뒤에 둔다.

        [2026-05-30] 예: '모듈A-2 캔틸레버보1' 은 '모듈A-2' 바로 아래에 나열.
        - 부모-종속 관계는 group_id 로 역추적: sub_index>0(종속)인 부재의
          같은 group_id 본체(sub_index==0)가 부모이고, 그 본체의 라벨을 쓴다.
        - 독립/기타 라벨: (자기 타입순서, 라벨, 0, '').
        - 종속 라벨: (부모 타입순서, 부모라벨, 1, 종속라벨) → 부모 직후 묶임.
        구조해석 탭(부재별 내부력)·물량 탭(응력비) 두 트리가 공유.
        """
        label_cids: Dict[str, list] = defaultdict(list)
        for _cid, meta in comp_meta.items():
            label_cids[meta[0]].append(_cid)

        def _parent_label_of(lbl):
            for _cid in label_cids.get(lbl, []):
                comp = scene.components.get(_cid) if scene is not None else None
                if comp is None:
                    continue
                if int(getattr(comp, 'sub_index', 0)) == 0:
                    return None   # 독립 부재 본체
                gid = int(getattr(comp, 'group_id', 0) or 0)
                if not gid:
                    return None
                for c2id, c2 in scene.components.items():
                    if (int(getattr(c2, 'group_id', 0) or 0) == gid
                            and int(getattr(c2, 'sub_index', 0)) == 0):
                        pmeta = comp_meta.get(c2id)
                        if pmeta is not None:
                            return pmeta[0]
                return None
            return None

        def _key(lbl):
            parent = _parent_label_of(lbl)
            if parent is None:
                return (_comp_type_order_key(label_tname.get(lbl, '')),
                        lbl, 0, '')
            return (_comp_type_order_key(label_tname.get(parent, '')),
                    parent, 1, lbl)

        return sorted(keys, key=_key)

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

        # comp_meta[cid] = (top_label, floor_num, type_name)
        # [2026-05-30 D3] 독립 부재(모듈/패널/벽/수직)는 classify_component_types
        # 의 '모듈A-1' 라벨, 종속/기타 부재는 기존 방식(타입+xy 알파벳)으로
        # 최상위 라벨을 만든다.
        from modular_3d.model.type_naming import classify_component_types
        type_labels = classify_component_types(scene)
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
                # [P6b] 단면 설계 변형 라벨(있으면 'A-1-1') 우선, 없으면 기존 'A-1'.
                top_label = (self._section_type_labels.get(cid)
                             or type_labels.get(cid))
                if top_label is None:
                    # 종속/기타 — 기존 'a모듈' 형식(알파벳 + 타입명).
                    top_label = f"{xy_to_label[xy]}{tname}"
                comp_meta[cid] = (top_label, z_to_floor[z], tname)

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
        top_groups: Dict[str, list] = defaultdict(list)
        label_tname: Dict[str, str] = {}
        for cid, (top_label, floor, tname) in comp_meta.items():
            label_tname[top_label] = tname
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
                    top_groups[top_label].append((f_idx, cid, mids))
            else:
                top_groups[top_label].append((floor, cid, mid_list))

        sorted_top = self._order_top_labels(
            scene, comp_meta, label_tname, top_groups.keys())

        # ── 3) 트리 구성 ─────────────────────────────────
        for top_label in sorted_top:
            top_text = top_label
            top_item = QTreeWidgetItem([top_text, '', '', '', ''])
            top_item.setData(0, Qt.UserRole, None)
            f = top_item.font(0); f.setBold(True); top_item.setFont(0, f)

            for floor, cid, mid_list in sorted(top_groups[top_label],
                                                key=lambda e: (e[0], e[1])):
                comp_text = f"{top_label} {floor}층"
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
                            f"{top_label} {floor}층 / {role_ko} #{mid}")
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
        """부재 role 영어 → 한글 표시명. 공용 함수(member_roles)에 위임."""
        return _classify_role_ko_fn(m, model, mid, cid)

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

    def set_section_type_labels(self, labels) -> None:
        """[P6b] 단면 설계 -1-1 변형 라벨(comp_id→'모듈A-1-1') 주입.

        다음 트리 채우기부터 top_label 이 이 라벨로 오버라이드된다(없으면 기본 'A-1').
        빈 dict/ None 이면 기존 동작(단면 설계 미실행) — 완전 가역.
        """
        self._section_type_labels = dict(labels or {})

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
        # [2026-06-02] 물량 서브탭이 *화면에 보일 때만* 패널 폭을 고정한다.
        # 구조해석 탭에선 부재력 트리가 _tabs 밖으로 분리되고 _tabs(물량/운송)는
        # 숨겨지는데, 여기서 par 폭을 건드리면 방금 잡은 부재력 폭(477)을 물량 폭
        # (567)으로 덮어써 우측 흰 공간이 생겼다(2026-06-02 진단으로 확정).
        if self._tabs.isVisible():
            self.setMaximumWidth(16777215)
            self.setMinimumWidth(panel_min)
            self._force_pane_width(panel_min)
        self.updateGeometry()

    def _fit_panel_to_tree(self):
        """트리 내용폭(헤더 + 모든 행의 실제 텍스트)에 맞춰 패널·우측 pane 폭 고정.

        [2026-06-01] 기존 sizeHintForColumn 방식은 col0 Stretch·트리 펼침/현재폭
        상태에 따라 값이 진동(예: 265↔567)·과대됐다(실제 내용보다 넓게 잡힘).
        → 항목 텍스트 폭을 *직접* 재서 트리 상태와 무관한 일관 내용폭을 산출한다.
        col0 = 부재명 + 깊이별 들여쓰기, 숫자칸 = 값/헤더 중 큰 폭. 펼침 여부 무관.
        """
        tree = self._tree
        ncol = tree.columnCount()
        fm = tree.fontMetrics()
        hdr = tree.header()
        hfm = hdr.fontMetrics() if hdr is not None else fm
        CELL_PAD = 12   # 셀 좌우 여백(텍스트 1px 잘림 방지)
        indent = tree.indentation()
        # 헤더 폭으로 초기화.
        cw = [0] * ncol
        hi = tree.headerItem()
        for c in range(ncol):
            t = hi.text(c) if hi is not None else ''
            cw[c] = hfm.horizontalAdvance(t) + CELL_PAD
        # 모든 항목(펼침 무관) 순회 — col0 만 깊이 들여쓰기 가산.
        stk = [(tree.topLevelItem(i), 0)
               for i in range(tree.topLevelItemCount())]
        while stk:
            it, d = stk.pop()
            if it is None:
                continue
            for c in range(ncol):
                w = fm.horizontalAdvance(it.text(c)) + CELL_PAD
                if c == 0:
                    w += (d + 1) * indent
                if w > cw[c]:
                    cw[c] = w
            for k in range(it.childCount()):
                stk.append((it.child(k), d + 1))
        # [2026-06-02] col0(부재명) 폭 상한 — 긴 그룹명이 L·N·V·M 을 오른쪽으로
        #   밀어 M 이 잘리던 문제 해결. 상한을 넘으면 "…" 로 줄여 표시(ElideRight).
        #   숫자 열(1~4)은 내용폭 그대로라 값·헤더가 같은 열에 정렬된다.
        # [2026-06-02] 상한 300 → 200: 부재명 열을 더 좁혀 숫자 열(L·N·V·M)을 왼쪽으로
        #   당긴다. 패널을 넓히면 창 폭 부족 시 오른쪽(M·스크롤바)이 잘리므로, 내용을
        #   왼쪽으로 당기는 쪽이 창 폭과 무관하게 M·스크롤바 간격을 확보한다.
        COL0_MAX = 200
        cw[0] = min(cw[0], COL0_MAX)
        # 측정한 폭을 실제 컬럼에 반영 — col0 는 Interactive 라 직접 폭 지정.
        tree.setColumnWidth(0, int(cw[0]))
        # [2026-06-02] 컬럼 폭 합을 폰트 추정(cw)이 아니라 *Qt 실제 폭*으로 계산한다.
        #   진단 결과 ResizeToContents 실제 폭(합 568)이 폰트 추정(합 489)보다 79px
        #   넓어, 추정 기준 패널이 항상 모자라 M 열이 스크롤바 밑으로 잘렸다.
        #   실제 폭으로 잡으면 패널이 내용+스크롤바+여백을 정확히 담는다.
        col_total = sum(tree.columnWidth(c) for c in range(ncol))
        frame_pad = 2 * tree.frameWidth()
        # [2026-06-02] 세로 스크롤바 폭을 *항상* 미리 확보한다.
        #   기존엔 isVisible() 일 때만 더했는데, fit 호출 시점엔 트리가 아직
        #   채워지기 전이라 스크롤바가 숨어 있다가 나중에 부재 목록이 길어지면
        #   생겨서 그 폭(~17px)만큼 M(마지막) 열이 잘렸다. 항상 예약해 해결.
        vbar = tree.verticalScrollBar()
        sb_w = vbar.sizeHint().width() if vbar is not None else 0
        if sb_w <= 0:
            sb_w = 18   # 스타일에 따라 0 이 나오면 표준 폭으로 보정
        # [2026-06-02] M(마지막) 열과 세로 스크롤바 사이 여백.
        #   스크롤바 폭만 예약하면 M 열이 스크롤바에 바짝 붙어 잘려 보인다.
        #   추가 여백(SB_GAP)을 둬 마지막 열이 스크롤바와 떨어지게 한다.
        SB_GAP = 20
        frame_pad += sb_w + SB_GAP
        tree_min = max(200, col_total + frame_pad)
        tree.setMinimumWidth(tree_min)
        # [2026-06-02] 구조해석 우측 패널 최소 폭 바닥값 — 3D 카드를 확실히 좁힘.
        #   부재 트리 텍스트가 짧아 내용폭만으론 패널이 충분히 안 넓어지던 문제 보정.
        # tree_min 에 이미 프레임+스크롤바가 포함됨. 여기에 root 좌우 마진(8+8=16)
        #   + 안전 여유(8) 를 더해 M 열 끝이 확실히 보이게 한다.
        # [2026-06-02] 바닥값 700 → 520: 패널을 '내용 + 스크롤바 + 여백' 만큼만 잡는다.
        #   바닥값이 너무 크면 창 폭이 부족할 때 패널이 창 밖으로 밀려 M·스크롤바가
        #   잘렸다. tree_min(=col_total+프레임+스크롤바+여백)이 floor 보다 크면 그 값을
        #   쓰므로, 내용이 많으면 자동으로 넓어지고 적으면 창에 들어오게 좁아진다.
        panel_min = max(tree_min + 24, 520)
        # 부모 우측 pane 을 내용폭으로 고정(minimumWidth 만으론 이미 넓어진 폭이
        # 안 줄어듦). 직접 측정이라 호출마다 같은 값 → 진동 없음.
        # 숨은 서브탭은 set_visible_subtabs 에서 가로 Ignored 처리되어 패널 최소폭을
        # 안 키운다. 따라서 self 최대폭은 풀어둔다(다른 메인탭서 넓은 서브탭 노출 시
        # 클리핑 방지). 패널 폭은 부모 pane setFixedWidth 로 고정.
        self.setMaximumWidth(16777215)
        self.setMinimumWidth(panel_min)
        self._force_pane_width(panel_min)
        self.updateGeometry()

    def _force_pane_width(self, w: int):
        """부모 우측 pane 의 폭을 내용폭 w 로 강제 고정.

        [2026-05-30] AnalysisPanel 은 main_3d 의 _analysis_right_pane /
        _quantity_right_pane (QHBoxLayout 의 비-stretch 우측 칸)에 reparent 되어
        들어간다. minimumWidth 만으로는 이미 넓어진 폭이 안 줄어 두 탭 폭이
        같아 보였다(직전 증상). 부모 pane 에 setFixedWidth 를 걸어 실제 폭을
        내용폭으로 못 박는다. 각 탭이 자기 fit 에서 자기 pane 을 고정 → 분리.
        """
        par = self.parentWidget()
        if par is None:
            return
        # pane 은 minimumWidth(380) 가 박혀 있으므로 먼저 풀고 고정.
        par.setMinimumWidth(0)
        par.setFixedWidth(int(w))


    def clear(self):
        """패널 내용을 비운다."""
        self._store = None
        self._model = None
        self._tree.clear()

    # ── 부재 트리 (ops 모델 단일 소스) ───────────────

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
        # Phase 6 운송탭 동기화 — 정책 변경을 운송탭에 즉시 알림.
        self.policy_changed.emit(self._current_policy)
        if hasattr(self, '_transport_tab') and self._transport_tab is not None:
            self._transport_tab.on_policy_sync(self._current_policy)

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

        # 자재 단가 로드 + 자재비 산출 (2026-05-24)
        # 단가는 material_prices.json. 강재(각형강관)·데크슬래브·레미콘만.
        from modular_3d.카탈로그.material_prices import get_unit_price
        from modular_3d.analysis.quantity_takeoff import compute_material_cost
        _steel_unit = get_unit_price('강재_각형강관_SHS')
        _steel_h_unit = get_unit_price('강재_H형강')
        _deck_unit = get_unit_price('데크슬래브')
        _concrete_unit = get_unit_price('콘크리트_레미콘')
        matcost = compute_material_cost(rep, _steel_unit, _deck_unit,
                                        _concrete_unit,
                                        steel_h_unit_per_ton=_steel_h_unit)

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
            # 금액(원) = 총 중량(ton) × 강재 단가(원/ton). 단가 없으면 '—'.
            if _steel_unit is not None:
                amt_text = f"{it.total_weight_ton * _steel_unit:,.0f}"
            else:
                amt_text = "—"
            self._steel_table.setItem(r, 5, QTableWidgetItem(amt_text))
            if it.is_total:
                for c in range(6):
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

        # 자재비(재료비) 총액표 채우기 (2026-05-24)
        self._fill_material_cost_table(matcost)

        # 부재별 응력비 트리 (작업 3)
        self._fill_ratio_table(rep)
        # 물량 탭 컨텐츠 폭에 맞춰 패널 너비 자동 조정 (작업 1)
        self._fit_panel_to_quantity()

    def _fill_material_cost_table(self, mc):
        """자재비 총액표 — 강재/데크슬래브/콘크리트/합계 (2026-05-24).

        mc: MaterialCost. 단가 미입력(0) 이면 단가칸을 '미입력' 으로 표시.
        """
        from modular_3d._utils.format import won as _won
        rows = [
            ("강재(각형강관)", f"{mc.steel_ton:.4f} ton",
             (f"{mc.steel_unit:,.0f} 원/ton" if mc.steel_unit else "미입력"),
             _won(mc.steel_cost)),
            ("데크슬래브", f"{mc.deck_area_m2:.2f} ㎡",
             (f"{mc.deck_unit:,.0f} 원/㎡" if mc.deck_unit else "미입력"),
             _won(mc.deck_cost)),
            ("콘크리트(레미콘)", f"{mc.concrete_m3:.3f} ㎥",
             (f"{mc.concrete_unit:,.0f} 원/㎥" if mc.concrete_unit else "미입력"),
             _won(mc.concrete_cost)),
            ("합계", "", "", _won(mc.total_cost)),
        ]
        for r, (name, qty, unit, amt) in enumerate(rows):
            for c, txt in enumerate((name, qty, unit, amt)):
                cell = QTableWidgetItem(txt)
                if r == 3:   # 합계 행 볼드
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                self._matcost_table.setItem(r, c, cell)

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
            # [D4] _comp_meta 미생성 폴백 — 구조해석탭과 동일한 라벨 규칙으로 직접 생성.
            from modular_3d.model.type_naming import classify_component_types
            type_labels = classify_component_types(scene)
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
                    top_label = (type_labels.get(cid)
                                 or f"{xy_to_label[xy]}{tname}")
                    comp_meta[cid] = (top_label, z_to_floor[z], tname)

        # ── 2) top_groups[top_label] — 구조해석탭과 동일 ──
        top_groups: Dict[str, list] = defaultdict(list)
        label_tname: Dict[str, str] = {}
        for cid, (top_label, floor, tname) in comp_meta.items():
            label_tname[top_label] = tname
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
                    top_groups[top_label].append((f_idx, cid, mids))
            else:
                top_groups[top_label].append((floor, cid, mid_list))

        sorted_top = self._order_top_labels(
            scene, comp_meta, label_tname, top_groups.keys())

        # ── 3) 트리 빌드 — 구조해석탭과 동일 ─────────────────
        for top_label in sorted_top:
            top_text = top_label
            top_item = QTreeWidgetItem([top_text, '', '', '', ''])
            top_item.setData(0, Qt.UserRole, None)
            f = top_item.font(0); f.setBold(True); top_item.setFont(0, f)

            for floor, cid, mid_list in sorted(top_groups[top_label],
                                                key=lambda e: (e[0], e[1])):
                comp_text = f"{top_label} {floor}층"
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
