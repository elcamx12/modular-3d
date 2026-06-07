"""홈 패널 — 프로그램 진입 시 첫 화면.

[설계 근거]
- 단독 랜딩 페이지 (QStackedWidget page 0). 탭 UI 와 분리.
- 좌측 큰 카드 안에 *프로젝트 설정 폼 전체* 를 인라인으로 인스턴스화.
  카드 우측 하단의 [새 프로젝트 시작] 버튼이 폼 값을 ProjectSettings 에
  적용한 뒤 시그널 발화 → MainWindow 가 모듈 정의 탭으로 전환.

[2026-06-01 핵심 결정]
- 랜딩 폼과 다이얼로그 폼은 같은 클래스 ProjectSettingsForm 의 인스턴스다.
  과거에 두 곳 위젯 코드를 따로 유지하면서 GroupSeparatorShown 같은 사소한
  속성이 어긋나 표시 값이 달라 보이는 문제가 있었다. 단일 진실 출처로 일원화.

[디자인 토큰]
- 페이지 배경  #EDF2F7 / 카드 #FFFFFF / 보더 #DDE4ED
- 헤드라인  #1F4E79 / 본문 #1F2A37 / 보조 #5B6573
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from modular_3d.ui.project_settings import ProjectSettings, ProjectSettingsForm
from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded


# ── 토큰 ──────────────────────────────────────────────────
# 종합/비교 탭과 동일한 컬러 팔레트 — 톤 통일.
_PAGE_BG     = "#EDF2F7"
_CARD_BG     = "#FFFFFF"
_CARD_BORDER = "#DDE4ED"
_HEAD_FG     = "#1F4E79"
_BODY_FG     = "#1F2A37"
_SUB_FG      = "#5B6573"
_ACCENT      = "#1F4E79"
_ACCENT_HOV  = "#163A5E"
_ACCENT_SOFT = "#F5F8FF"

# 폰트 규칙 (사용자 확정):
# - 묻는 글/단독 제목/안내문 → Paperlogy (F_HEAD)
# - 정보 값 / 한 줄 안 라벨+값 혼합 / 버튼 / 입력 컨트롤 → Freesentation (F_BODY)


# ── 보조 빌더 ─────────────────────────────────────────────
def _card(min_height: int = 0) -> QFrame:
    f = QFrame()
    f.setObjectName("homeCard")
    f.setStyleSheet(
        f"QFrame#homeCard {{ background: {_CARD_BG};"
        f" border: 1px solid {_CARD_BORDER}; border-radius: 10px; }}"
    )
    if min_height > 0:
        f.setMinimumHeight(min_height)
    return f


def _h(label: str, size: int = 15, weight: int = 700,
       color: str = _HEAD_FG) -> QLabel:
    """제목/헤드라인 — Paperlogy."""
    lbl = QLabel(label)
    lbl.setStyleSheet(
        f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
        f" color: {color}; font-size: {size}px; font-weight: {weight};"
        " background: transparent;"
    )
    return lbl


def _p(label: str, size: int = 14, color: str = _SUB_FG) -> QLabel:
    """본문 안내문 — Paperlogy (묻는/안내 글)."""
    lbl = QLabel(label)
    lbl.setStyleSheet(
        f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
        f" color: {color}; font-size: {size}px; background: transparent;"
    )
    lbl.setWordWrap(True)
    return lbl


def _step_box(index: int, title: str, desc: str, outputs: str = "") -> QFrame:
    f = QFrame()
    f.setObjectName("stepBox")
    f.setStyleSheet(
        f"QFrame#stepBox {{ background: {_ACCENT_SOFT};"
        f" border: 1px solid #D7E2F0; border-radius: 8px; }}"
    )
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(6)
    # 번호 — Freesentation (숫자 값), 13→16
    num = QLabel(f"{index:02d}")
    num.setStyleSheet(
        f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
        f" color: {_ACCENT}; font-size: 16px; font-weight: 800;"
        " background: transparent; letter-spacing: 1px;"
    )
    # 단계 제목 (배치 설계/접합부 설계/구조해석/물량/운송/공정표) — Paperlogy, 17→22
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(
        f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
        f" color: {_BODY_FG}; font-size: 22px; font-weight: 700;"
        " background: transparent;"
    )
    # 단계 설명 — Paperlogy (안내문), 13→15
    desc_lbl = QLabel(desc)
    desc_lbl.setStyleSheet(
        f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
        f" color: {_SUB_FG}; font-size: 15px; background: transparent;"
    )
    desc_lbl.setWordWrap(True)
    v.addWidget(num)
    v.addWidget(title_lbl)
    v.addWidget(desc_lbl)
    if outputs:
        # "산출 · ..." — 한 줄 안 라벨+값 혼합 → Freesentation, 12→14
        out_lbl = QLabel(f"산출 · {outputs}")
        out_lbl.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {_ACCENT}; font-size: 14px; font-weight: 700;"
            " background: transparent; padding-top: 4px;"
            " border-top: 1px dashed #C9D6E8; margin-top: 4px;"
        )
        out_lbl.setWordWrap(True)
        v.addWidget(out_lbl)
    v.addStretch(1)
    return f


# ── 메인 위젯 ─────────────────────────────────────────────
class HomePanel(QWidget):
    """프로그램 첫 화면 — 랜딩 + 시작 트리거."""

    start_new_project_requested = pyqtSignal()

    def __init__(self, settings: Optional[ProjectSettings] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        ensure_fonts_loaded()  # Paperlogy/Freesentation 등록 보장
        self._settings = settings if settings is not None else ProjectSettings()
        self.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 헤더 — 종합탭 톤(옅은 푸른 흰색 #F5F8FF + 다크블루 글자) ──
        header = QFrame()
        header.setObjectName("homeHeader")
        header.setStyleSheet(
            "QFrame#homeHeader {"
            f" background: {_ACCENT_SOFT};"   # #F5F8FF — 옅은 푸른 흰색
            f" border-bottom: 1px solid {_CARD_BORDER}; }}"
        )
        h_lay = QVBoxLayout(header)
        h_lay.setContentsMargins(36, 28, 36, 28)
        h_lay.setSpacing(4)
        # 메인 타이틀 — Paperlogy, 키움 28→34
        title = QLabel("모듈러 설계 프로그램")
        title.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_HEAD_FG}; font-size: 34px; font-weight: 800;"
            " background: transparent;"
        )
        # 서브타이틀 — Paperlogy (안내성 문구), 15→18
        subtitle = QLabel("Modular Housing Design Suite — "
                          "평면설계부터 공정·비용 분석까지")
        subtitle.setStyleSheet(
            f"font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            f" color: {_SUB_FG}; font-size: 18px; background: transparent;"
        )
        h_lay.addWidget(title)
        h_lay.addWidget(subtitle)
        root.addWidget(header)

        # ── 본문 ──────────────────────────────────────────
        body = QWidget()
        body.setStyleSheet(f"QWidget {{ background: {_PAGE_BG}; }}")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(36, 28, 36, 28)
        body_lay.setSpacing(20)
        root.addWidget(body, stretch=1)

        # [2026-06-07] 공통 설정 폼(시작하기 카드) 제거 — 진입 화면에는 작업 순서만
        #   남긴다. 공통 설정은 메뉴의 [프로젝트 설정] 에서 관리한다.

        # ── 작업 순서 ─────────────────────────────────────
        flow_card = _card(min_height=320)
        fc = QVBoxLayout(flow_card)
        fc.setContentsMargins(28, 24, 28, 24)
        fc.setSpacing(14)
        fc.addWidget(_h("작업 순서", size=24))
        fc.addWidget(_p(
            "각 단계의 결과가 다음 단계로 자동 전달됩니다. "
            "왼쪽에서 오른쪽으로 진행하세요.",
            size=16,
        ))
        grid = QGridLayout()
        grid.setSpacing(14)
        steps = [
            ("평면 설계",  "층별 모듈·코어·패널을 평면에 배치",
             "2D 평면도 · 컴포넌트 트리"),
            ("접합부 설계", "모듈-모듈·모듈-코어 연결 부재 결정",
             "캔틸레버보·중간기둥 자동 배치"),
            ("구조해석",  "응력비·층간변위 확인, 단면 수렴",
             "5단계 응력비 색·NG 부재 목록"),
            ("물량",      "정책(1·2·3종) 별 강재·슬래브 산출",
             "재료비·강재 본수 표"),
            ("운송",      "회차·평균 적재·운송비 산출",
             "트럭 회차표 · 적재율 %"),
            ("공정표",    "착공일 기준 공기·간트 자동 생성",
             "총 공기 일수 · 월별 간트"),
        ]
        for idx, item in enumerate(steps):
            r, c = divmod(idx, 3)
            sb = _step_box(idx + 1, item[0], item[1], outputs=item[2])
            sb.setMinimumHeight(120)
            grid.addWidget(sb, r, c)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        fc.addLayout(grid, stretch=1)

        # ── 작업 순서 박스 오른쪽 하단 — 새 프로젝트 시작 버튼 ──
        # [2026-06-07] 시작하기 카드를 없애고 시작 버튼을 작업 순서 박스 안
        #   오른쪽 아래로 옮긴다.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addStretch(1)
        btn_new = QPushButton("새 프로젝트 시작")
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.setMinimumHeight(42)
        btn_new.setStyleSheet(
            "QPushButton {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" background: {_ACCENT}; color: white; border: none;"
            " border-radius: 6px; font-size: 14px; font-weight: 700;"
            " padding: 8px 24px; }"
            f"QPushButton:hover {{ background: {_ACCENT_HOV}; }}"
        )
        btn_new.clicked.connect(self._on_start_clicked)
        btn_row.addWidget(btn_new)
        fc.addLayout(btn_row)

        body_lay.addWidget(flow_card, stretch=4)

        # ── 푸터 ──────────────────────────────────────────
        # 버전 표기는 정보값이므로 Freesentation
        footer = QLabel("© Modular Housing Design Suite · v2026.06")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet(
            f"font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" color: {_SUB_FG}; font-size: 13px; background: transparent;"
            " padding: 14px 0 20px 0;"
        )
        root.addWidget(footer)

    # ── 단독 큰 시작하기 카드 ─────────────────────────────
    def _build_start_card(self) -> QFrame:
        """헤더 + 폼(2 컬럼) + 우측 하단 시작 버튼.

        [2026-06-01] ProjectSettingsForm(layout_mode="two-column") 으로
        폼 전체를 임베드. 다이얼로그와 *같은 폼 클래스* — 값 일치 보장.
        """
        card = _card(min_height=260)
        v = QVBoxLayout(card)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(12)

        v.addWidget(_h("시작하기", size=26))
        v.addWidget(_p(
            "사업 공통 설정을 입력하고 우측 하단의 [새 프로젝트 시작] 을 누르세요.",
            size=16,
        ))

        # 폼 인스턴스 — 다이얼로그와 동일한 클래스를 사용해 값/표시 일치.
        self._form = ProjectSettingsForm(
            self._settings, layout_mode="two-column",
            on_open_catalog=None,  # 카탈로그 진입은 메뉴바·운송탭에서.
        )
        # 폼 안의 위젯들에도 카드의 글꼴/세부 스타일 적용 — 다이얼로그 기본 모양 유지.
        # 폰트 규칙:
        # - QGroupBox 타이틀 / QLabel (묻는 글, 예: "지역") → Paperlogy (F_HEAD)
        # - QSpinBox/QComboBox/QDateEdit/QCheckBox 안 값(입력 결과) → Freesentation (F_BODY)
        # 그룹박스 타이틀(운송(공통), 공기, 현장 지역•착공일, 현장 운송 제한(공통),
        # 비내력벽 단위중량(공통)) 폰트 크기 13 → 17. 내부 라벨/입력 13 → 14.
        self._form.setStyleSheet(
            "QGroupBox {"
            f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            " font-size: 17px; font-weight: 700;"
            f" color: {_HEAD_FG}; border: 1px solid #DDE4ED;"
            " border-radius: 8px; margin-top: 16px;"
            " padding: 16px 14px 10px 14px; }"
            "QGroupBox::title { subcontrol-origin: margin;"
            " left: 12px; padding: 0 6px; background: white; }"
            "QLabel {"
            f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
            " font-size: 14px; color: #1F2A37;"
            " background: transparent; }"
            "QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            " font-size: 14px; padding: 3px 6px;"
            " border: 1px solid #DDE4ED; border-radius: 4px;"
            " background: white; min-height: 24px; }"
            "QCheckBox {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            " font-size: 14px; }"
        )
        v.addWidget(self._form, stretch=1)

        # ── 우측 하단 시작 버튼 ─────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addStretch(1)
        btn_new = QPushButton("새 프로젝트 시작")
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.setMinimumHeight(42)
        # 버튼 안 글자 — 비교탭 "파일 가져오기" 와 동일하게 Freesentation
        btn_new.setStyleSheet(
            "QPushButton {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" background: {_ACCENT}; color: white; border: none;"
            " border-radius: 6px; font-size: 14px; font-weight: 700;"
            " padding: 8px 24px; }"
            f"QPushButton:hover {{ background: {_ACCENT_HOV}; }}"
        )
        btn_new.clicked.connect(self._on_start_clicked)
        btn_row.addWidget(btn_new)
        v.addLayout(btn_row)

        return card

    # ── 클릭 핸들러 ───────────────────────────────────────
    def _on_start_clicked(self) -> None:
        """[2026-06-07] 진입 화면에서 공통 설정 폼을 제거했으므로 적용 단계 없이
        바로 시그널 발화. 공통 설정은 메뉴의 [프로젝트 설정] 에서 관리한다."""
        form = getattr(self, '_form', None)
        if form is not None:
            try:
                form.apply_to_settings()
            except Exception:
                pass
        self.start_new_project_requested.emit()

    # ── 외부 API ─────────────────────────────────────────
    def reload_from_settings(self) -> None:
        """settings 가 외부에서 변경된 뒤(예: 다이얼로그 OK) 폼 위젯 갱신.
        진입 화면 폼 제거 후에는 폼이 없으면 조용히 무시."""
        form = getattr(self, '_form', None)
        if form is None:
            return
        try:
            form.load_from_settings()
        except Exception:
            pass
