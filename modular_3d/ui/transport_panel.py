"""운송탭 UI — AnalysisPanel 운송 탭 (개편: 우측 단일 패널 + 중앙 3D 오버레이 실행).

[구조 — main_3d 운송 탭이 *중앙 3D + 우측 결과* 2단으로 조립]
    우측 결과 패널(_right_pane_scroll):
        ② 운송 옵션 (거리 / 운임 방식 / 트럭별 km단가 / 벽 단위중량)
        ③ 결과 요약 카드 (회차·평균적재율·거리·총 운임)
        ④ sub-탭 (회차표 / 적재율 / 경제성)
        ⑤ 회차별 트럭 override 표
        ⑦ 참고자료 버튼 (최하단)
    실행 버튼(_run_btn)은 main_3d 가 중앙 3D 뷰 우상단 오버레이로 reparent 한다.
    문제(운송 불가·경고)는 누적 패널 대신 알림창(QMessageBox)으로 표시한다.

[개편 이력 — 2026-05-31 운송탭 개편]
- 좌측 입력 패널·① 상태바·트럭 카탈로그 버튼·디버그 버튼·자동 재계산 제거.
- 비내력벽 자동판별 ON·격자 100mm·FloorPanel 장애물 포함은 코드 고정값.
- 진단 누적(QTextEdit) → 문제만 뜨는 알림창.

[외부 주입 API]
    tab.set_scene_and_model(scene, model)
    tab.set_design_result(design_result_dict, current_policy)
    tab.set_state(state) / tab.apply_project_settings(settings)
    tab.populate_transport(design_results, policy)  — main_3d 경유

[발신 시그널]
- transport_member_highlight(list[int]) — 회차표 행 더블클릭 시 cid 리스트
- transport_blocked(int) — 운송 불가 항목 수
- transport_pack_updated / transport_trip_clicked — 중앙 3D 도식 연동
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import plotly.io as pio
# matplotlib 한글 폰트 — 모듈 import 시점에 1 회 설정
# (적재율 sub-탭 막대그래프 라벨 "회차 #", "중량/길이 적재율 %" 한글 깨짐 방지)
try:
    import matplotlib
    matplotlib.rcParams["font.family"] = ["Malgun Gothic", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    pass

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from modular_3d.transport.adapter import (
    TransportError, TransportOptions, build_transport_input,
)
from modular_3d.transport.cache import TransportCache
from modular_3d.transport.catalog_io import load_all_trucks
from modular_3d.transport.economics import EconomicsOptions, compute_economics
from modular_3d.transport.models import SiteLimit, SpacingParams, Truck
from modular_3d.transport.packer import PackResult, Trip, recheck_trip_with_truck
from modular_3d.transport.visualizer import draw_rear_view, draw_top_view
from modular_3d.ui.transport_catalog_dialog import TransportCatalogDialog
from modular_3d.ui.transport_references_dialog import TransportReferencesDialog
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

# 패널 전체 스타일시트 — 종합/구조해석 탭과 동일 톤.
# - GroupBox 타이틀 / QLabel / QCheckBox(묻는 글·라벨) → Paperlogy
# - QComboBox·QSpinBox·QLineEdit·표(값·입력) → Freesentation, 흰 카드 톤
# - QHeaderView::section(표 머리) → Paperlogy (다른 탭과 동일)
# - QPushButton → Freesentation 둥근 카드형(실행 버튼은 accent #runBtn)
_TRANSPORT_QSS = (
    f"QWidget#transportWrap {{ background: {_PAGE_BG}; }}"
    "QScrollArea { background: transparent; border: none; }"
    "QGroupBox {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 17px; font-weight: 800; color: {_HEAD_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 10px; margin-top: 14px;"
    " padding: 14px 12px 12px 12px; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 12px;"
    f" padding: 0 6px; background: {_CARD_BG};"
    " }"
    "QLabel {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; color: {_BODY_FG}; background: transparent;"
    " }"
    "QCheckBox {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; font-weight: 700; color: {_HEAD_FG};"
    " background: transparent; }"
    "QComboBox, QSpinBox, QLineEdit {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; color: {_BODY_FG};"
    f" border: 1px solid {_CARD_BORDER}; border-radius: 6px;"
    " background: white; padding: 4px 8px; min-height: 24px; }"
    "QTableWidget {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 14px; color: {_BODY_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 8px; }"
    "QHeaderView::section {"
    f" font-family: '{F_HEAD}', 'Malgun Gothic', sans-serif;"
    f" font-size: 13px; font-weight: 700; color: {_HEAD_FG};"
    f" background: {_PAGE_BG}; border: none;"
    f" border-bottom: 1px solid {_CARD_BORDER}; padding: 5px 8px;"
    " }"
    "QPushButton {"
    f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
    f" font-size: 15px; font-weight: 700; color: {_BODY_FG};"
    f" background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};"
    " border-radius: 8px; padding: 8px 14px; }"
    f"QPushButton:hover {{ background: {_ACCENT_SOFT}; border-color: {_ACCENT};"
    f" color: {_HEAD_FG}; }}"
    f"QPushButton#runBtn {{ background: {_ACCENT}; color: white;"
    f" border-color: {_ACCENT}; font-weight: 800; padding: 9px 18px; }}"
    f"QPushButton#runBtn:hover {{ background: {_ACCENT_HOV};"
    f" border-color: {_ACCENT_HOV}; }}"
    "QPushButton#runBtn:disabled { background: #AEB6C2; color: #EEF1F5;"
    " border-color: #AEB6C2; }"
)


# 8 단계 라벨
_PIPELINE_LABELS = [
    (2, "해석"), (3, "단면"), (4, "분할"),
    (5, "분류"), (6, "변환"), (7, "패킹"),
    (8, "운임"), (None, "그림"),  # 8 번 운임 + 그림은 같은 묶음(시각화)
]


class TransportTab(QWidget):
    """운송탭 본체 — AnalysisPanel 의 4번째 탭으로 삽입."""

    transport_member_highlight = pyqtSignal(list)
    transport_blocked = pyqtSignal(int)
    # [Phase C — 2026-05-26] 운송 계산 완료 후 MainWindow 가 center pane 3D 도식을
    # 갱신할 수 있도록 PackResult + SpacingParams 신호 발신.
    transport_pack_updated = pyqtSignal(object, object)
    # [Phase E — 2026-05-26] 회차표 행 클릭 → 해당 회차 트럭 3D 강조.
    # trip_no (int) 만 발신. MainWindow 가 받아 3D 재렌더 with highlight.
    transport_trip_clicked = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # ── 외부 주입 상태 ─────────────────────────────────
        self._scene = None
        self._model = None
        # design_result_dict: {'1종': DesignResult, ...} 또는 단일
        self._design_results: Dict[str, object] = {}
        self._current_policy: str = "3종"
        # 캐시
        self._cache = TransportCache()
        # 마지막 결과 (UI 렌더용 — source_index 등)
        self._last_pack: Optional[PackResult] = None
        self._last_ti = None
        self._last_eco = None

        # 카탈로그 (project_root — 외부 주입 가능, 미주입 시 cwd 기준)
        # [Phase 7] _project_root 설정 후 _reload_catalog 호출하면 trucks/roads
        # 모두 재로드. 다이얼로그 저장 후 자동 동기화 진입점.
        self._project_root = None
        try:
            self._trucks: List[Truck] = load_all_trucks(active_only=True)
        except Exception:
            self._trucks = []
        # 현장 운송 제한 — 프로젝트 설정에서 주입(apply_project_settings). 미주입 시
        # 기본값(45t/3500/4500). [2026-06-04] GVW 기본 45t(사용자 확정).
        self._site_limit: SiteLimit = SiteLimit(
            max_gvw_kg=45000.0, max_width_mm=3500.0, max_height_mm=4500.0)
        # 프로젝트 설정 객체(운임 1회 고정비 등 읽기용). apply_project_settings 에서 주입.
        self._proj_settings = None

        # [Phase 7] 회차별 override 저장 — {trip_no: new_truck}.
        # 운송계산 재실행 시 자동 적용. recheck_trip_with_truck 실패하면
        # 셀에 빨강 표시 + 진단 메시지. _run_transport 끝에서 적용 단계 호출.
        self._trip_overrides: Dict[int, Truck] = {}
        # override 적용 후의 trip (사용자에게 보여줄 실제 trip 리스트)
        self._displayed_trips: List[Trip] = []

        # [개편] 자동 재계산 디바운스 타이머 제거 — 수동 실행만 지원.
        self._build_ui()
        self.set_state("NoDesign")

    # ── 프로젝트 설정 공통값 주입 ─────────────────────────
    def apply_project_settings(self, settings) -> None:
        """프로젝트 설정 공통값을 운송 옵션 위젯에 주입하고 읽기전용으로 만든다.

        [정책 2026-05-24 프로젝트 설정 2단계]
        - 운송 거리·운임 방식·트럭 종류별 km단가·비내력벽 단위중량(내/외)
          은 프로젝트 설정이 단일 출처. 운송 탭에서는 값을 보여주되 편집은
          막아(비활성=회색) 프로젝트 설정에서만 고치게 한다.
        - 운송 탭 진입 시마다 호출돼 최신 공통값으로 갱신된다(반영 시점=탭 진입).
        - 격자 크기·자동판별 ON·도로 등급·적재 간격 등은 공통 설정 대상이
          아니므로 그대로 편집 가능하게 둔다.
        """
        if settings is None:
            return
        self._proj_settings = settings
        tip = "프로젝트 설정에서 관리하는 공통 값입니다 (메뉴 줄 → 프로젝트 설정)."

        def _lock_spin(attr, value):
            w = getattr(self, attr, None)
            if w is not None:
                w.setValue(value)
                w.setEnabled(False)
                w.setToolTip(tip)

        _lock_spin('_distance_spin', int(settings.distance_km))
        _lock_spin('_lowbed_per_km_spin', int(settings.lowbed_per_km_krw))
        _lock_spin('_extend_per_km_spin', int(settings.extendable_per_km_krw))
        _lock_spin('_aframe_per_km_spin', int(settings.aframe_per_km_krw))
        # [2026-06-07] 트레일러별 1회 고정비도 프로젝트 설정값으로 잠금.
        _lock_spin('_lowbed_fixed_spin', int(getattr(settings, 'lowbed_fixed_krw', 1_000_000)))
        _lock_spin('_extend_fixed_spin', int(getattr(settings, 'extendable_fixed_krw', 1_200_000)))
        _lock_spin('_aframe_fixed_spin', int(getattr(settings, 'aframe_fixed_krw', 800_000)))
        # [2026-06-02] 벽 단위중량 입력은 운송 탭에서 제거됨 — 잠금 대상 아님.
        #   값은 _read_options 가 self._proj_settings 에서 직접 읽는다.

        combo = getattr(self, '_cost_mode_combo', None)
        if combo is not None:
            idx = combo.findData(settings.cost_mode)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setEnabled(False)
            combo.setToolTip(tip)
        # [2026-06-07] 운임 방식이 설정값으로 바뀌었으니 노출 행을 다시 맞춘다.
        self._update_cost_mode_rows()

        # 현장 운송 제한 — 프로젝트 설정 값으로 SiteLimit 구성 + 읽기전용 표시.
        gvw = (getattr(settings, 'site_limit_gvw_kg', 45000.0)
               if getattr(settings, 'site_limit_gvw_enabled', True) else None)
        sw = (getattr(settings, 'site_limit_width_mm', 3500.0)
              if getattr(settings, 'site_limit_width_enabled', True) else None)
        sh = (getattr(settings, 'site_limit_height_mm', 4500.0)
              if getattr(settings, 'site_limit_height_enabled', True) else None)
        self._site_limit = SiteLimit(max_gvw_kg=gvw, max_width_mm=sw,
                                     max_height_mm=sh)
        lbl = getattr(self, '_site_limit_label', None)
        if lbl is not None:
            def _f(v, unit):
                return f"{int(v):,}{unit}" if v is not None else "해당없음"
            lbl.setText(f"총중량 {_f(gvw, 'kg')} · 폭 {_f(sw, 'mm')} "
                        f"· 높이 {_f(sh, 'mm')}")
            lbl.setToolTip(tip)
        # 현장 제한 변경 가능성 → 패킹부터 무효화
        self._cache.invalidate_from(7)

    # ── UI 빌드 ───────────────────────────────────────────
    def _build_ui(self) -> None:
        # [개편] 좌측 입력 패널 제거 — 카탈로그 버튼·① 상태·옵션·참고자료가
        #   모두 좌측에 있었으나, 입력(옵션·참고자료)만 우측 단일 패널로 통합한다.
        #   카탈로그 버튼·① 상태는 폐지. main_3d 는 우측 pane(_right_pane_scroll)만
        #   reparent 한다(_left_pane_scroll 은 더는 만들지 않음 → main_3d 의
        #   좌측 부착 분기는 hasattr 가드로 자동 skip, Phase 4 에서 2단으로 정리).
        # [2026-06-02 디자인 통일] 종합/구조해석 탭 톤 적용.
        ensure_fonts_loaded()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 우측(단일) 패널 — 옵션 + 결과 ─────────────────
        self._right_pane_wrap = QWidget()
        self._right_pane_wrap.setObjectName("transportWrap")
        # [중요] 패널 QSS 는 *이 wrap* 에 건다 — main_3d 가 우측 패널(scroll+wrap)을
        #   TransportTab 밖으로 reparent 하므로, TransportTab(self)에 걸면 reparent 후
        #   QSS 가 내용물을 따라가지 못해 폰트가 빠진다(헤더·실행버튼처럼 직접 스타일한
        #   것만 남았던 원인). wrap 에 걸면 어디로 reparent 되든 자식에 계속 적용된다.
        self._right_pane_wrap.setStyleSheet(_TRANSPORT_QSS)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(self._right_pane_wrap)
        right_lay = QVBoxLayout(self._right_pane_wrap)
        right_lay.setContentsMargins(12, 12, 12, 12)
        right_lay.setSpacing(10)
        # [2026-06-07] '운송 계획' 헤더 라벨 제거 — 탭 제목과 중복, 세로 공간 확보.
        right_lay.addWidget(self._build_area_options())      # ② 옵션 + [▷ 실행]
        right_lay.addWidget(self._build_area_metrics())      # ③ 결과 요약
        right_lay.addWidget(self._build_area_subtabs(), stretch=1)  # ④ 회차표
        # [2026-06-07] ⑤ '회차별 트럭 변경' 표 제거 — 회차표 공간 확보.
        #   (override 표를 만들지 않으므로 _render_override_table 은 가드로 skip.)
        right_lay.addWidget(self._build_area_references())   # ⑦ 참고자료 (최하단)

        root.addWidget(right_scroll, stretch=1)
        # main_3d 가 reparent 시 사용. 좌측 pane 은 제거됨.
        self._right_pane_scroll = right_scroll

    def _build_area_overrides(self) -> QWidget:
        """[Phase 7] 영역 ⑤ — 회차별 트럭 override 표.

        패킹 결과가 나온 뒤, 사용자가 회차별로 다른 트럭을 강제 지정할 수
        있게 한다. 각 행: [회차 # / 원래 차량 / override 콤보 / 검증]
        override 콤보 변경 → recheck_trip_with_truck 으로 검증 → 실패 시
        검증 셀에 사유 표시 + override 폐기 + 콤보 원복.
        성공 시 회차표·도식·경제성 재렌더에 그대로 반영.
        """
        box = QGroupBox("회차별 트럭 변경 (override)")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 8)
        info = QLabel(
            "각 회차의 트럭을 강제 변경합니다. 회차표 행을 우클릭해도 동일 기능 사용 가능."
        )
        info.setStyleSheet(
            f"font-family:'{F_HEAD}','Malgun Gothic',sans-serif;"
            f" font-size:12px; color:{_SUB_FG}; background:transparent;")
        lay.addWidget(info)
        self._override_table = QTableWidget()
        self._override_table.setColumnCount(4)
        self._override_table.setHorizontalHeaderLabels(
            ["회차", "원래 차량", "변경 트럭", "검증 결과"]
        )
        oh = self._override_table.horizontalHeader()
        oh.setSectionResizeMode(QHeaderView.ResizeToContents)
        oh.setStretchLastSection(True)
        self._override_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._override_table.setAlternatingRowColors(True)
        self._override_table.setMaximumHeight(180)
        lay.addWidget(self._override_table)
        # [전체 초기화] 버튼 — 모든 override 폐기
        btn_row = QHBoxLayout()
        self._clear_overrides_btn = QPushButton("전체 override 초기화")
        self._clear_overrides_btn.clicked.connect(self._clear_overrides)
        btn_row.addWidget(self._clear_overrides_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        return box

    def _build_area_references(self) -> QWidget:
        """영역 ⑦ — [📖 참고자료] 버튼."""
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(2, 2, 2, 2)
        self._ref_btn = QPushButton("📖 참고자료")
        self._ref_btn.setToolTip("운송 도메인 참고문서 (references/*.md 자동 탭)")
        self._ref_btn.clicked.connect(self._open_references_dialog)
        # [2026-06-07] 참고자료 버튼 절반 크기(높이·폭) — 회차표에 공간 양보.
        self._ref_btn.setFixedHeight(20)
        self._ref_btn.setMaximumWidth(90)
        self._ref_btn.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 1px 6px; }")
        row.addWidget(self._ref_btn)
        row.addStretch(1)
        return wrap

    def _build_area_options(self) -> QWidget:
        """영역 ② — 운송 옵션 패널."""
        box = QGroupBox("운송 옵션")
        lay = QFormLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(4)
        self._opt_form = lay   # [2026-06-07] 운임 방식별 행 표시/숨김에 사용

        # 현장 운송 제한 — 프로젝트 설정 값(읽기전용 표시). 도로 등급 콤보 폐지.
        self._site_limit_label = QLabel("(프로젝트 설정에서 관리)")
        self._site_limit_label.setStyleSheet(
            f"font-family:'{F_BODY}','Malgun Gothic',sans-serif;"
            f" font-size:14px; color:{_SUB_FG}; background:transparent;")
        self._site_limit_label.setToolTip(
            "현장 운송 제한(총중량·폭·높이)은 프로젝트 설정에서 관리합니다.")
        lay.addRow("현장 운송 제한:", self._site_limit_label)

        # 거리
        self._distance_spin = QSpinBox()
        self._distance_spin.setRange(1, 9999)
        self._distance_spin.setValue(30)
        self._distance_spin.setSuffix(" km")
        lay.addRow("운송 거리 (편도):", self._distance_spin)

        # 적재 간격은 2026-05-26 부터 내장 고정값(수직100·수평100·양끝200·폭여유200)
        # 으로 처리 — 사용자 입력 제거.

        # 운임 방식 — 요금표(전국특송24시콜, 기본) / 트레일러별 km단가
        # 값은 프로젝트 설정에서 관리하므로 본 위젯들은 읽기전용으로 표시된다.
        self._cost_mode_combo = QComboBox()
        self._cost_mode_combo.addItem("요금표 (전국특송24시콜)", "freight_table")
        self._cost_mode_combo.addItem("트레일러별 km단가", "per_km")
        self._cost_mode_combo.addItem("트레일러별 1회 고정비", "fixed_per_trip")
        lay.addRow("운임 방식:", self._cost_mode_combo)

        # 트럭 종류별 km단가 (per_km 방식에서 사용)
        self._lowbed_per_km_spin = QSpinBox()
        self._lowbed_per_km_spin.setRange(0, 1_000_000)
        self._lowbed_per_km_spin.setValue(3500)
        self._lowbed_per_km_spin.setSuffix(" 원/km")
        lay.addRow("저상 km단가:", self._lowbed_per_km_spin)

        self._extend_per_km_spin = QSpinBox()
        self._extend_per_km_spin.setRange(0, 1_000_000)
        self._extend_per_km_spin.setValue(5000)
        self._extend_per_km_spin.setSuffix(" 원/km")
        lay.addRow("광폭 km단가:", self._extend_per_km_spin)

        self._aframe_per_km_spin = QSpinBox()
        self._aframe_per_km_spin.setRange(0, 1_000_000)
        self._aframe_per_km_spin.setValue(5000)
        self._aframe_per_km_spin.setSuffix(" 원/km")
        lay.addRow("A-frame km단가:", self._aframe_per_km_spin)

        # [2026-06-07] 트레일러별 1회 고정비 (fixed_per_trip 방식에서 사용).
        #   값은 프로젝트 설정에서 관리 → apply_project_settings 가 잠그고 채운다.
        self._lowbed_fixed_spin = QSpinBox()
        self._lowbed_fixed_spin.setRange(0, 100_000_000)
        self._lowbed_fixed_spin.setSingleStep(100_000)
        self._lowbed_fixed_spin.setValue(1_000_000)
        self._lowbed_fixed_spin.setSuffix(" 원/회")
        lay.addRow("저상 1회 고정비:", self._lowbed_fixed_spin)

        self._extend_fixed_spin = QSpinBox()
        self._extend_fixed_spin.setRange(0, 100_000_000)
        self._extend_fixed_spin.setSingleStep(100_000)
        self._extend_fixed_spin.setValue(1_200_000)
        self._extend_fixed_spin.setSuffix(" 원/회")
        lay.addRow("광폭 1회 고정비:", self._extend_fixed_spin)

        self._aframe_fixed_spin = QSpinBox()
        self._aframe_fixed_spin.setRange(0, 100_000_000)
        self._aframe_fixed_spin.setSingleStep(100_000)
        self._aframe_fixed_spin.setValue(800_000)
        self._aframe_fixed_spin.setSuffix(" 원/회")
        lay.addRow("A-frame 1회 고정비:", self._aframe_fixed_spin)

        # (2026-05-27 Phase 1) 캔틸 처리 라디오 폐지 — 캔틸레버는 항상 부모
        # 화물에 기하학적 종속 상태로 같은 회차에 운송된다.

        # [2026-06-02] 벽 단위중량 입력(내부/외부) 묶음 제거.
        #   이 두 칸은 프로젝트 설정값을 비춰주고 잠가두는 미러일 뿐이었다.
        #   단일 출처인 프로젝트 설정에서 값을 직접 읽으므로(_read_options) 운송
        #   탭에서는 입력 자체를 두지 않는다.

        # 실행 버튼 — [Phase 4] 옵션 패널에 두지 않는다. main_3d 가 중앙 3D 뷰
        #   우상단 오버레이로 reparent 한다. 여기서는 생성·스타일·연결만(부모 미지정 —
        #   self._run_btn 참조로 수명 유지, set_state 가 enable/text 제어).
        self._run_btn = QPushButton("▷ 운송 계산 실행")
        # 실행 버튼은 중앙 3D 위로 reparent 되는 오버레이라 패널 QSS 가 안 닿는다.
        #   → 버튼에 직접 accent 스타일을 준다(종합/비교탭 강조 버튼 톤).
        self._run_btn.setStyleSheet(
            "QPushButton {"
            f" font-family: '{F_BODY}', 'Malgun Gothic', sans-serif;"
            f" background-color: {_ACCENT}; color: white;"
            " padding: 9px 18px; font-size: 15px; font-weight: 800;"
            " border: none; border-radius: 8px; }"
            f"QPushButton:hover {{ background-color: {_ACCENT_HOV}; }}"
            "QPushButton:disabled { background-color: #AEB6C2; color: #EEF1F5; }"
        )
        self._run_btn.clicked.connect(self._run_transport)

        # 옵션 위젯 변경 → 캐시 무효화 배선(자동 실행은 제거됨).
        self._wire_auto_recompute()
        # [2026-06-07] 운임 방식에 따라 필요한 입력 행만 보이게 한다.
        self._cost_mode_combo.currentIndexChanged.connect(
            lambda *_: self._update_cost_mode_rows())
        self._update_cost_mode_rows()
        return box

    def _set_form_row_visible(self, field, visible: bool) -> None:
        """QFormLayout 의 한 행(라벨+입력)을 통째로 표시/숨김."""
        field.setVisible(visible)
        form = getattr(self, '_opt_form', None)
        if form is not None:
            lbl = form.labelForField(field)
            if lbl is not None:
                lbl.setVisible(visible)

    def _update_cost_mode_rows(self) -> None:
        """[2026-06-07] 선택된 운임 방식에 필요한 입력만 노출.

        - 요금표(freight_table): 운송 거리만(요금표가 거리로 조회). km단가·고정비 숨김.
        - 트레일러별 km단가(per_km): 운송 거리 + 종류별 km단가. 고정비 숨김.
        - 트레일러별 1회 고정비(fixed_per_trip): 종류별 1회 고정비만(거리 무관).
        """
        mode = self._cost_mode_combo.currentData() or 'fixed_per_trip'
        is_per_km = (mode == 'per_km')
        is_fixed = (mode == 'fixed_per_trip')
        # 운송 거리 — 고정비 방식은 거리 무관이라 숨김.
        self._set_form_row_visible(self._distance_spin, not is_fixed)
        # 종류별 km단가 — per_km 일 때만.
        for s in (self._lowbed_per_km_spin, self._extend_per_km_spin,
                  self._aframe_per_km_spin):
            self._set_form_row_visible(s, is_per_km)
        # 종류별 1회 고정비 — fixed_per_trip 일 때만.
        for s in (self._lowbed_fixed_spin, self._extend_fixed_spin,
                  self._aframe_fixed_spin):
            self._set_form_row_visible(s, is_fixed)
        # [2026-06-07] 결과 요약 '총 거리' 카드 — km단가 방식에서만 표시(거리 기반 운임).
        card = getattr(self, '_metric_cards', {}).get('total_distance')
        if card is not None:
            card.setVisible(is_per_km)

    def _wire_auto_recompute(self) -> None:
        """옵션 위젯 변경을 무효화 단계와 함께 연결.

        [Phase 9 점검 결정 3 — 캐시 정밀 무효화 매트릭스]
        분석 ⑧ 3번 표에 따라 각 옵션이 무효화하는 시작 단계가 다르다.
        Phase 6 의 보수적 일괄 invalidate_from(5) 를 단계별 정밀화로 교체:
          - 거리/왕복/운임방식/km단가 → [8] 운임만 (패킹·어댑터 재사용)
          - 적재 간격(gap/edge/stack)·도로 콤보 → [7] 패킹부터
          - 단위중량(내부/외부)·캔틸 라디오 → [6] 어댑터부터
          - 비내력벽 자동판별·격자 크기·FloorPanel 장애물 → [5] 분류부터
        """
        wiring = [
            # (시그널, 무효화 시작 단계)
            # 비용 옵션 변경 → 패킹부터 재계산 (트럭 선정 점수가 비용 인식)
            (self._distance_spin.valueChanged, 7),
            (self._cost_mode_combo.currentIndexChanged, 7),
            (self._lowbed_per_km_spin.valueChanged, 7),
            (self._extend_per_km_spin.valueChanged, 7),
            (self._aframe_per_km_spin.valueChanged, 7),
            # [2026-06-07] 1회 고정비 입력도 변경 시 패킹부터 무효화.
            (self._lowbed_fixed_spin.valueChanged, 7),
            (self._extend_fixed_spin.valueChanged, 7),
            (self._aframe_fixed_spin.valueChanged, 7),
            # [2026-06-02] 단위중량 입력 제거 — 변경 시 무효화 배선 대상 아님.
        ]
        for sig, step in wiring:
            sig.connect(lambda *a, s=step: self._on_option_changed(s))

    def _on_option_changed(self, step: int = 5) -> None:
        """옵션 변경 → 해당 단계부터 캐시 무효 + 자동 재계산 ON 이면 디바운스 실행.

        [Phase 9 결정 3] step 인자로 무효화 시작 단계를 받아 정밀 무효화.
        [Phase 6 점검 패치 — 결정 4] 디바운스 500ms 적용. SpinBox 연타 시
        마지막 변경 후 500ms 지나야 1회만 _run_transport. start() 재호출은
        자동 리셋.
        """
        # [개편] 자동 재계산 제거 — 옵션 변경 시 캐시 무효화만 수행, 실행은
        #   사용자가 명시적으로 [▷ 운송 계산 실행] 을 눌러야 한다.
        self._cache.invalidate_from(step)

    def _build_area_metrics(self) -> QWidget:
        """영역 ③ — 결과 요약 카드 (총 운임 포함)."""
        box = QGroupBox("결과 요약")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)
        self._metric_labels: Dict[str, QLabel] = {}
        self._metric_cards: Dict[str, QFrame] = {}   # 카드별 표시/숨김용
        for key, title in [
            ("module_trips", "모듈 회차"),
            ("panel_trips", "패널 회차"),
            ("total_trips", "총 회차"),
            # [2026-06-07] '평균 적재율' 카드 제거 — 사용자 요청.
            ("total_distance", "총 거리"),
            ("total_freight", "총 운임"),
        ]:
            card = QFrame()
            card.setFrameShape(QFrame.StyledPanel)
            card.setStyleSheet(
                f"background: {_ACCENT_SOFT}; border: 1px solid {_CARD_BORDER};"
                " border-radius: 8px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 6, 8, 6)
            t = QLabel(title)
            # 카드 제목(묻는 글) → Paperlogy.
            t.setStyleSheet(
                f"font-family:'{F_HEAD}','Malgun Gothic',sans-serif;"
                f" font-size:12px; font-weight:700; color:{_SUB_FG};"
                " background:transparent; border:none;")
            v = QLabel("—")
            # 값/숫자 → Freesentation.
            v.setStyleSheet(
                f"font-family:'{F_BODY}','Malgun Gothic',sans-serif;"
                f" font-size:18px; font-weight:800; color:{_HEAD_FG};"
                " background:transparent; border:none;")
            cl.addWidget(t)
            cl.addWidget(v)
            lay.addWidget(card)
            self._metric_labels[key] = v
            self._metric_cards[key] = card
        lay.addStretch(1)
        return box

    def _build_area_subtabs(self) -> QWidget:
        """영역 ④ — 회차표 / 적재율 / 경제성. (2026-05-26 Phase C: 도식 sub-탭 제거 —
        3D 적재 도식은 메인 화면 center pane 에서 표시.)

        [핫픽스 — 2026-05-26 Phase C]
        도식 sub-탭 위젯 생성도 *완전 제거*. 이전엔 "위젯은 만들되 addTab 안 함"
        으로 두려고 했으나 — 부모가 없는 위젯은 Qt 가 GC 해서 _view_combo 등이
        deleted C++ object 가 되어 _render_view_combo 호출 시 RuntimeError 발생.
        """
        # [2026-06-02] 회차표 탭 묶음 제거 — 탭이 "회차표" 하나뿐이라 어색했다.
        #   탭 위젯 대신 "회차표" 제목을 단 그룹박스로 표만 직접 보여준다.
        box = QGroupBox("회차표")
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(4, 4, 4, 4)
        box_lay.addWidget(self._build_subtab_trip_table())
        return box

    def _build_subtab_trip_table(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 2, 2)
        self._trip_table = QTableWidget()
        self._trip_table.setColumnCount(5)
        self._trip_table.setHorizontalHeaderLabels([
            "차량", "아이템", "화물중량/최대화물중량", "GVW(kg)", "운임",
        ])
        hh = self._trip_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        self._trip_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._trip_table.setAlternatingRowColors(True)
        self._trip_table.cellDoubleClicked.connect(self._on_trip_row_double_clicked)
        # [Phase E] 단일 클릭 — 3D 도식에서 해당 회차 강조 신호
        self._trip_table.cellClicked.connect(self._on_trip_row_clicked)
        # (2026-05-26) 회차 행 우클릭 컨텍스트 메뉴 제거 — 동작 불안정해
        # 아래 "회차별 트럭 변경" 표만 사용한다.
        lay.addWidget(self._trip_table)
        return page

    # ── 외부 주입 API ─────────────────────────────────────
    def set_scene_and_model(self, scene, model) -> None:
        self._scene = scene
        self._model = model
        self._cache.invalidate_from(2)
        self._refresh_stage_indicators()

    def set_design_result(self, design_result_or_dict, current_policy: str = "3종") -> None:
        """design_result 주입.

        인자가 dict 면 {policy → DesignResult}, 단일 DesignResult 도 허용.

        [Phase 6 점검 패치] design_result 가 주입되었다는 사실 자체가 구조해석
        + 단면 산정이 끝났음을 의미하므로 [2] 해석 단계 캐시 표식도 함께 채워서
        상태바 인디케이터가 [해석 ✓] 로 표시되게 한다. (실제 ops_results 객체를
        받지는 않지만, 단계별 ✓/✗ 시각화 목적에만 사용되는 표식이라 OK.)
        """
        if isinstance(design_result_or_dict, dict):
            self._design_results = dict(design_result_or_dict)
        else:
            self._design_results = {current_policy: design_result_or_dict}
        self._current_policy = current_policy
        self._cache.invalidate_from(3)
        # [2] 해석 단계 표식 — 외부에서 design_result 가 들어왔다는 것은 해석이
        # 완료되었다는 신호. UI 인디케이터용 truthy 마커만 채움.
        if self._cache.analysis_result is None:
            self._cache.analysis_result = "external"
        # 정책 캐시 채움
        if current_policy in self._design_results:
            self._cache.design_results[current_policy] = self._design_results[current_policy]
            self._cache.design_fp[current_policy] = f"design_{id(self._design_results[current_policy])}"
            self._cache.current_policy = current_policy
        self._update_policy_label()
        if self._design_results:
            self.set_state("Ready")
        self._refresh_stage_indicators()

    def set_state(self, state: str) -> None:
        """state ∈ {NoDesign, Ready, Computing, ResultShown, Error}."""
        self._state = state
        enabled = state in ("Ready", "ResultShown")
        self._run_btn.setEnabled(enabled)
        if state == "NoDesign":
            self._run_btn.setText("(구조해석 + 단면 산정을 먼저 실행하세요)")
        elif state == "Computing":
            self._run_btn.setText("⏳ 계산 중...")
        elif state == "Error":
            self._run_btn.setText("▷ 운송 계산 실행 (재시도)")
        else:
            self._run_btn.setText("▷ 운송 계산 실행")

    # ── 옵션 → 데이터클래스 ───────────────────────────────
    def _read_options(self) -> TransportOptions:
        # [개편] 비내력벽 자동판별 ON·격자 100mm·FloorPanel 장애물 포함은
        #   UI 에서 제거되어 항상 고정값으로 동작한다(사용자 결정 — 코드 기본값).
        # [2026-06-02] 벽 단위중량은 프로젝트 설정이 단일 출처. 운송 탭 입력 칸을
        #   제거했으므로 self._proj_settings 에서 직접 읽는다. 설정 주입 전이면
        #   기본값(내부 30 / 외부 55 kg/m²)을 사용한다.
        ps = getattr(self, '_proj_settings', None)
        interior_uw = float(getattr(ps, 'wall_interior_kg_m2', 30.0)) if ps is not None else 30.0
        exterior_uw = float(getattr(ps, 'wall_exterior_kg_m2', 55.0)) if ps is not None else 55.0
        return TransportOptions(
            include_cantilever=True,
            wall_classifier_enabled=True,
            interior_wall_unit_weight=interior_uw,
            exterior_wall_unit_weight=exterior_uw,
            wall_segment_size_mm=100.0,
            include_floor_panels_as_obstacle=True,
        )

    def _read_spacing(self) -> SpacingParams:
        # 적재 간격은 내장 고정값 (수직100·수평100·양끝200·폭여유200).
        return SpacingParams()

    def _read_economics_options(self) -> EconomicsOptions:
        ps = self._proj_settings
        return EconomicsOptions(
            distance_km=float(self._distance_spin.value()),
            cost_mode=self._cost_mode_combo.currentData() or "fixed_per_trip",
            lowbed_per_km_krw=float(self._lowbed_per_km_spin.value()),
            extendable_per_km_krw=float(self._extend_per_km_spin.value()),
            aframe_per_km_krw=float(self._aframe_per_km_spin.value()),
            # [2026-06-07] 1회 고정비는 전용 입력칸(프로젝트 설정값으로 잠김)에서 읽는다.
            lowbed_fixed_krw=float(self._lowbed_fixed_spin.value()),
            extendable_fixed_krw=float(self._extend_fixed_spin.value()),
            aframe_fixed_krw=float(self._aframe_fixed_spin.value()),
        )

    # ── 메인 실행 ─────────────────────────────────────────
    def _run_transport(self) -> None:
        if not self._design_results or self._current_policy not in self._design_results:
            self._show_error("구조해석 + 단면 산정을 먼저 실행하세요.")
            return
        site = self._site_limit
        self.set_state("Computing")
        # [2026-06-08] 진행 표시 콜백 — 모듈은 타입 하나 끝날 때마다, 패널은 1회
        #   완료 시 실행 버튼 텍스트에 표시. processEvents 로 즉시 다시 그린다
        #   (% 단위로 매번 갱신하지 않고 단계 단위로만).
        from PyQt5.QtWidgets import QApplication

        def _progress(msg: str) -> None:
            try:
                self._run_btn.setText(f"⏳ {msg}")
                QApplication.processEvents()
            except Exception:
                pass

        try:
            options = self._read_options()
            spacing = self._read_spacing()
            economics = self._read_economics_options()
            design = self._design_results[self._current_policy]
            pack = self._cache.get_or_compute_pack(
                self._scene, self._model, design, self._current_policy,
                options, self._trucks, site, spacing,
                economics=economics, progress=_progress,
            )
        except TransportError as e:
            self.set_state("Error")
            self._show_error(str(e))
            return
        except Exception as e:
            self.set_state("Error")
            self._show_error(f"{type(e).__name__}: {e}")
            return

        self._last_pack = pack
        self._last_ti = self._cache.transport_input

        # [2026-06-08] 패킹 실패(못 실은 부재 blocked) 있으면 바로 중단 + 경고창.
        blocked = getattr(pack, "blocked", None) or []
        if blocked:
            lines = []
            for entry in blocked[:8]:
                try:
                    item, reason = entry
                    nm = getattr(item, "name", "?")
                except Exception:
                    nm, reason = "?", str(entry)
                lines.append(f"· {nm} — {reason}")
            more = f"\n… 외 {len(blocked) - 8}건" if len(blocked) > 8 else ""
            self._run_btn.setText("▷ 운송 계산 실행 (재시도)")
            self.set_state("Error")
            self.transport_blocked.emit(len(blocked))
            QMessageBox.warning(
                self, "운송 패킹 실패",
                f"운송에 실을 수 없는 부재 {len(blocked)}건이 있어 계산을 중단했습니다.\n\n"
                + "\n".join(lines) + more
                + "\n\n트럭 카탈로그·현장 제한(GVW/폭/높이)·부재 치수를 확인하세요.")
            return

        # [2026-06-03 진단] 모듈 0회차 버그 추적 — 모듈은 추출됐는데 회차가 0인
        #   경우, 어디서 막혔는지(블록/적재실패/캐시) 콘솔에 한 줄 남긴다.
        #   정상(모듈 회차>0)이면 아무 출력 없음.
        try:
            from modular_3d._utils.debug import log_warn
            ti = self._cache.transport_input
            n_mod = len(ti.modules) if ti is not None else -1
            if n_mod > 0 and pack.module_trips == 0:
                blk = getattr(pack, 'blocked', []) or []
                reasons = "; ".join(str(b) for b in blk[:3])
                log_warn(
                    f"[운송] 모듈 추출 {n_mod}개인데 모듈 회차 0 — "
                    f"blocked {len(blk)}건 / total_trips {pack.total_trips}. "
                    f"트럭 {len(self._trucks)}대(모듈호환 "
                    f"{sum(1 for t in self._trucks if t.truck_type in ('lowbed','extendable'))}대), "
                    f"현장제한 GVW{self._site_limit.max_gvw_kg:.0f}/"
                    f"W{self._site_limit.max_width_mm:.0f}/"
                    f"H{self._site_limit.max_height_mm:.0f}. "
                    f"블록사유: {reasons or '없음(=캐시/적재로직 의심)'}",
                    cat='transport')
        except Exception:
            pass
        # [2026-05-26] override 적용 전 "원래 트럭 이름" 스냅샷.
        #   _render_override_table 에서 "원래 차량" 컬럼 표시에 사용.
        #   이전엔 trip.truck.name 을 그대로 썼는데, override 적용 후엔 그
        #   값이 새 트럭이 되어 "원래"·"변경"이 같게 보이는 시각 버그였음.
        self._original_truck_by_no = {
            t.trip_no: t.truck.name for t in pack.trips
        }

        # [Phase 7] override 적용 — 사용자가 회차별로 트럭 강제 변경한 항목이
        # 있으면 packing 결과를 그대로 두지 않고 recheck_trip_with_truck 으로
        # 새 trip 생성. 실패 항목은 override 폐기 + 경고 메시지 누적.
        # ※ 운임 계산보다 먼저 적용해야 운임이 override 트럭 단가를 반영한다.
        pack = self._apply_overrides(pack)
        self._displayed_trips = list(pack.trips)

        # 운임 계산 — override 적용된 pack 기준. economics 는 비용이 작아 항상
        # 재계산하므로 cache.get_or_compute_economics(원본 pack) 대신 직접 호출하고
        # 결과를 캐시에 반영([8] 인디케이터 표식용).
        eco = compute_economics(pack, self._read_economics_options())
        self._cache.economics_result = eco
        self._last_eco = eco

        self._render_metrics(pack)
        self._render_trip_table(pack)
        # [개편] 적재율·경제성 sub-탭 제거 — 해당 렌더 호출 폐지.
        #   총 운임은 결과 요약 카드, 회차별 운임은 회차표 컬럼으로 표시(eco 는 계산만).
        # 3D 적재 도식은 transport_pack_updated 신호로 MainWindow center pane 에서 처리.
        self._render_diagnostics(self._cache.transport_input)
        self._render_override_table()
        self._refresh_stage_indicators()

        if pack.blocked:
            self.transport_blocked.emit(len(pack.blocked))
        # 회차 0건 — 패킹은 성공했으나 결과적으로 어떤 부재도 못 실음. 사용자 알림.
        if pack.total_trips == 0:
            self._show_error(
                "운송 결과 0회차 — 트럭 카탈로그·현장 제한·부재 치수를 확인하세요. "
                "(운송 불가 사유가 있으면 별도 경고 창으로 안내됩니다.)")
        # [Phase C] center pane 3D 도식 갱신 신호
        self.transport_pack_updated.emit(pack, self._read_spacing())
        self.set_state("ResultShown")

    # ── 렌더링 ────────────────────────────────────────────
    def _render_metrics(self, pack: PackResult) -> None:
        self._metric_labels["module_trips"].setText(f"{pack.module_trips}")
        self._metric_labels["panel_trips"].setText(f"{pack.panel_trips}")
        self._metric_labels["total_trips"].setText(f"{pack.total_trips}")
        # [2026-06-07] '평균 적재율' 카드 제거 — 갱신 대상 아님.
        # 총 거리 = 편도거리 × 회차 × 2 (운임 모델이 항상 왕복 기준이므로 일치시킴).
        # (2026-05-24 운임 개편으로 왕복 토글 제거 — 항상 왕복으로 계산.)
        dist = self._distance_spin.value() * pack.total_trips * 2
        self._metric_labels["total_distance"].setText(f"{dist:,} km")
        # 총 운임 — 경제성 결과(_last_eco)의 합계. 미산출 시 "—".
        eco = getattr(self, "_last_eco", None)
        if eco is not None:
            self._metric_labels["total_freight"].setText(f"₩{eco.total_cost_krw:,.0f}")
        else:
            self._metric_labels["total_freight"].setText("—")

    def _item_type_label(self, item, type_labels: Dict[int, str]) -> str:
        """화물 item → 타입 분류 라벨('모듈A-1' 등). 못 찾으면 원래 name."""
        name = getattr(item, "name", "?")
        ti = getattr(self, "_last_ti", None)
        if ti is not None and type_labels:
            for cid in ti.source_index.get(name, []):
                lbl = type_labels.get(cid)
                if lbl:
                    return lbl
        return name

    def _format_trip_items(self, items, type_labels: Dict[int, str]) -> str:
        """회차 아이템 목록을 타입 라벨로 표기(같은 라벨은 '×n' 으로 묶음)."""
        from collections import Counter
        counts = Counter(self._item_type_label(i, type_labels) for i in items)
        parts = []
        for lbl, n in counts.items():
            parts.append(f"{lbl} ×{n}" if n > 1 else lbl)
        return ", ".join(parts)

    def _render_trip_table(self, pack: PackResult) -> None:
        self._trip_table.setRowCount(len(pack.trips))
        # 회차 번호는 좌측 행 헤더(1·2·3…)와 중복이라 컬럼에서 제거.
        # 운임 — 경제성 결과(_last_eco)의 회차별 운임을 trip_no 로 매칭(없으면 "—").
        eco = getattr(self, "_last_eco", None)
        cost_by_no: Dict[int, float] = (
            {tc.trip_no: tc.cost_krw for tc in eco.trips} if eco is not None else {}
        )
        # [2026-06-07] 아이템 표기를 단면설계·물량 탭과 동일한 "모듈A-1" 식 타입
        #   분류 라벨로 통일. item.name(예: 'a모듈-1F#42') → source_index 로 원본
        #   컴포넌트 cid 복원 → classify_component_types 라벨 조회.
        type_labels: Dict[int, str] = {}
        try:
            if self._scene is not None:
                from modular_3d.model.type_naming import classify_component_types
                type_labels = classify_component_types(self._scene) or {}
        except Exception:
            type_labels = {}
        for row, trip in enumerate(pack.trips):
            items_str = self._format_trip_items(trip.items, type_labels)
            stacked = [s for s in trip.stacked_items if s is not None]
            if stacked:
                items_str += " + 적층: " + self._format_trip_items(stacked, type_labels)
            # 화물중량/최대화물중량 — 분모는 트럭 적재 한도(truck.max_weight).
            cargo_max = f"{trip.cargo_weight:,.0f} / {trip.truck.max_weight:,.0f}"
            fare = cost_by_no.get(trip.trip_no)
            fare_str = f"₩{fare:,.0f}" if fare is not None else "—"
            for col, val in enumerate([
                trip.truck.name, items_str, cargo_max,
                f"{trip.gross_weight:,.0f}", fare_str,
            ]):
                cell = QTableWidgetItem(val)
                if col in (2, 3, 4):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._trip_table.setItem(row, col, cell)


    def _render_view_combo(self, pack: PackResult) -> None:
        # [Phase C 핫픽스 — 2026-05-26] 도식 sub-탭 폐기 — 위젯 자체가 GC.
        # 외부에서 호출돼도 안전하게 noop. 3D 도식은 MainWindow center pane.
        if not hasattr(self, "_view_combo") or self._view_combo is None:
            return
        try:
            # 위젯이 살아있는지 sip 검사
            self._view_combo.objectName()
        except RuntimeError:
            return
        self._view_combo.blockSignals(True)
        self._view_combo.clear()
        for trip in pack.trips:
            self._view_combo.addItem(
                f"#{trip.trip_no} · {trip.truck.name} · {len(trip.items)}매",
                trip.trip_no,
            )
        self._view_combo.blockSignals(False)
        if pack.trips:
            self._view_combo.setCurrentIndex(0)
            self._render_views(pack.trips[0].trip_no)
        else:
            empty_html = "<p style='color:gray;font-family:sans-serif'>(회차 없음)</p>"
            for web in (self._top_view_web, self._rear_view_web):
                web.setHtml(empty_html)

    def _on_view_combo_changed(self, idx: int) -> None:
        if idx < 0 or self._last_pack is None:
            return
        trip_no = self._view_combo.itemData(idx)
        if trip_no is not None:
            self._render_views(trip_no)

    def _render_views(self, trip_no: int) -> None:
        if self._last_pack is None:
            return
        # override 적용된 displayed_trips 우선, 없으면 원본 pack 에서 검색
        pool = self._displayed_trips or self._last_pack.trips
        trip = next((t for t in pool if t.trip_no == trip_no), None)
        if trip is None:
            return
        self._render_trip_pair(
            trip, self._top_view_web, self._rear_view_web,
            self._read_spacing(), kind_prefix="trip",
        )

    def _render_trip_pair(self, trip, top_web, rear_web, sp, kind_prefix="trip") -> None:
        """단일 trip 을 Top/Rear 두 WebEngineView 에 렌더 (공유 헬퍼).

        [Phase 8] 자동 결과 도식·수동 시뮬레이션 결과 도식 모두 본 메서드 재사용.
        kind_prefix 로 임시 HTML 파일명을 분리해 자동/수동 결과 파일 충돌 방지.

        [수정 3] setHtml/setContent 인메모리 dataURL 한도 회피 — 임시 HTML 파일
        + file:// URL load. 파일 위치는 ASCII 보장 (한글 경로면 가상드라이브 Q 드라이브).
        [수정 4] plotly 6.x 의 CSS `:focus-visible` 를 Chromium 87 이 못 파싱 →
        해당 룰만 치환 (시각 영향 0).
        [Phase 8 B] try/except + 진단 패널 출력 — 신규 패커가 만든 새 회차 형태
        (모듈+패널 혼적·4.5m 합산 모듈·STACK 다단 등) 에서 도식 렌더링이 실패하면
        ⑧ 진단 패널에 traceback 을 띄워 원인 추적 가능.
        """
        try:
            top_fig = draw_top_view(trip, trip.truck, sp)
            rear_fig = draw_rear_view(trip, trip.truck, sp)
            top_html = _strip_focus_visible_css(
                pio.to_html(top_fig, include_plotlyjs="inline", full_html=True))
            rear_html = _strip_focus_visible_css(
                pio.to_html(rear_fig, include_plotlyjs="inline", full_html=True))
            top_path = self._write_temp_html(f"{kind_prefix}_top", top_html)
            rear_path = self._write_temp_html(f"{kind_prefix}_rear", rear_html)
            if top_path:
                top_web.load(QUrl.fromLocalFile(top_path))
            if rear_path:
                rear_web.load(QUrl.fromLocalFile(rear_path))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            # [개편] 진단 누적 패널 폐지 — 도식 렌더 실패는 콘솔로만 기록(부차적).
            print(
                f"[도식 렌더 실패] 회차 #{getattr(trip, 'trip_no', '?')} "
                f"{type(e).__name__}: {e}\n{tb}", flush=True,
            )
            # 도식 위젯에 에러 메시지 직접 표시
            err_html = (
                "<div style='padding:20px;font-family:sans-serif;color:#cc0000'>"
                f"<b>도식 렌더링 실패</b><br>"
                f"회차 #{getattr(trip, 'trip_no', '?')}<br>"
                f"{type(e).__name__}: {e}<br>"
                "<small>자세한 내용은 ⑧ 진단 패널 확인</small></div>"
            )
            for web in (top_web, rear_web):
                try:
                    web.setHtml(err_html)
                except Exception:
                    pass

    def _write_temp_html(self, kind: str, html: str) -> str:
        """HTML 을 ASCII 경로 임시 파일로 저장. 반환 경로는 ASCII 보장.

        우선순위: 시스템 temp 가 ASCII 면 거기, 아니면 가상드라이브 Q 드라이브 Temp,
        그것도 안 되면 ProgramData fallback.
        """
        import tempfile
        candidates = []
        sys_tmp = tempfile.gettempdir()
        if sys_tmp.isascii():
            candidates.append(sys_tmp)
        # 가상드라이브 Q:\Temp (subst 매핑되어 있으면)
        for letter in "QRSTUVWXYZ":
            if os.path.exists(letter + ":\\"):
                candidates.append(letter + ":\\Temp")
                break
        candidates.append("C:\\ProgramData\\modular3d_temp")
        for d in candidates:
            try:
                os.makedirs(d, exist_ok=True)
                path = os.path.join(d, f"transport_{kind}.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
                if path.isascii():
                    return path
            except Exception:
                continue
        return ""


    def _render_diagnostics(self, ti) -> None:
        """[개편] 진단 누적 패널 폐지 — *정말 문제가 있을 때만* 알림창을 띄운다.

        표시 대상(문제):
        - ❌ 운송 불가(blocked) — 부재 이름 + 사유.
        - ⚠ 경고(diagnostics.warnings) — 어댑터가 실제 문제만 담는 채널.
        제외 대상(정보성, 사용자 결정):
        - ℹ info, 흡수/제외(excluded: 캔틸레버 부모 흡수·운송 대상 제외·코어).
        문제가 한 건도 없으면 아무 창도 띄우지 않는다.
        """
        problems: List[str] = []
        diag = getattr(ti, "diagnostics", None) if ti is not None else None
        if diag is not None:
            for w in diag.warnings:
                problems.append(f"⚠ {w}")
        if self._last_pack is not None:
            for it, reason in self._last_pack.blocked:
                problems.append(
                    f"❌ 운송 불가: {getattr(it, 'name', '?')}\n   사유: {reason}"
                )
        if problems:
            QMessageBox.warning(
                self, "운송 계산 — 확인 필요", "\n\n".join(problems)
            )

    # ── 상태 ──────────────────────────────────────────────
    def _refresh_stage_indicators(self) -> None:
        # [개편] ① 상태 패널(8단계 인디케이터) 제거 — 호출처가 여러 곳이라
        #   본문만 비워 안전하게 무력화한다(위젯 미존재).
        return

    def _update_policy_label(self) -> None:
        # [개편] ① 상태 패널의 정책 라벨 제거 — 데이터(_current_policy)는
        #   on_policy_sync 가 계속 관리하되, 라벨 갱신만 무력화한다.
        return

    # ── 외부 시그널 슬롯 (AnalysisPanel 이 forward) ──────────
    def on_policy_sync(self, policy: str) -> None:
        """물량탭에서 정책 변경 → 정책 라벨 갱신 + 캐시 무효."""
        if policy == self._current_policy:
            return
        self._current_policy = policy
        if policy in self._design_results:
            design = self._design_results[policy]
            self._cache.design_results[policy] = design
            self._cache.design_fp[policy] = f"design_{id(design)}"
            self._cache.current_policy = policy
        self._cache.invalidate_from(4)  # 분할 이하 무효
        # [개편] 정책 라벨·인디케이터·자동 재계산 제거. 데이터 무효화만 유지.

    def on_scene_modified(self) -> None:
        """씬 편집 → 해석부터 다 무효 표시. 자동 트리거 X."""
        self._cache.invalidate_from(2)
        self.set_state("NoDesign")
        self._refresh_stage_indicators()

    # ── 행 더블클릭 → 부재 하이라이트 시그널 ─────────────
    def _on_trip_row_double_clicked(self, row: int, col: int) -> None:
        if self._last_pack is None or self._last_ti is None:
            return
        trip = self._last_pack.trips[row]
        cids: List[int] = []
        for item in trip.items:
            cids.extend(self._last_ti.source_index.get(item.name, []))
        for stk in trip.stacked_items:
            if stk is not None:
                cids.extend(self._last_ti.source_index.get(stk.name, []))
        if cids:
            self.transport_member_highlight.emit(cids)

    # ── [Phase E + 2026-05-27 양방향 동기화] ──
    def _on_trip_row_clicked(self, row: int, col: int) -> None:
        """회차표 행 단일 클릭 → 통합 진입점 select_trip."""
        if self._last_pack is None:
            return
        pool = self._displayed_trips or self._last_pack.trips
        if row < 0 or row >= len(pool):
            return
        trip = pool[row]
        self.select_trip(trip.trip_no)

    def select_trip(self, trip_no: int) -> None:
        """공통 회차 선택 진입점 — 회차표 행 선택 후 3D 강조 신호 emit.

        외부(3D 트럭 클릭)·회차표 클릭의 hub. selectRow 는 cellClicked 발화 X.
        [개편] 회차표에 회차번호 컬럼이 없으므로(컬럼0=차량명) trips 순서로
        trip_no→row 를 매핑해 행을 찾는다.
        """
        pool = self._displayed_trips or (
            self._last_pack.trips if self._last_pack is not None else [])
        for row, trip in enumerate(pool):
            if trip.trip_no == trip_no:
                self._trip_table.selectRow(row)
                break
        self.transport_trip_clicked.emit(trip_no)

    # ── [Phase 7] override 적용 ───────────────────────────
    def _apply_overrides(self, pack: PackResult) -> PackResult:
        """패킹 결과에 사용자 override 를 일괄 적용.

        실패한 override 는 자동 폐기 + 진단 영역에 사유 메시지 추가.
        반환: 새 PackResult (trips 만 교체, 나머지 메트릭 재계산은 생략 —
        평균 적재율은 새 trip 들로 다시 계산).
        """
        if not self._trip_overrides:
            return pack
        site = self._site_limit
        spacing = self._read_spacing()
        orig_by_no = getattr(self, "_original_truck_by_no", {})
        new_trips: List[Trip] = []
        msgs: List[str] = []
        for trip in pack.trips:
            new_truck = self._trip_overrides.get(trip.trip_no)
            if new_truck is None:
                new_trips.append(trip)
                continue
            orig_name = orig_by_no.get(trip.trip_no, trip.truck.name)
            ok, msg, new_trip = recheck_trip_with_truck(
                trip, new_truck, site, spacing
            )
            if ok and new_trip is not None:
                new_trips.append(new_trip)
                msgs.append(
                    f"✓ 회차 #{trip.trip_no} 트럭 변경: '{orig_name}' → "
                    f"'{new_truck.name}'"
                )
            else:
                # 폐기 + 사용자 알림 (변경 사유 명확히)
                msgs.append(
                    f"⚠ 회차 #{trip.trip_no} 트럭 변경 거부 — '{new_truck.name}'\n"
                    f"  사유: {msg}\n  → 원래 트럭 '{orig_name}' 유지"
                )
                self._trip_overrides.pop(trip.trip_no, None)
                new_trips.append(trip)
        if msgs:
            QMessageBox.warning(self, "트럭 변경 거부", "\n\n".join(msgs))
        # PackResult.avg_utilization / total_trips 등은 모두 trips 기반 @property 라
        # trips 만 교체하면 자동 재계산된다 (avg_utilization 은 set 불가 — 대입 금지).
        # blocked 는 원본 유지. dataclasses.replace 로 새 인스턴스 생성.
        from dataclasses import replace as _dc_replace
        return _dc_replace(pack, trips=new_trips)

    def _render_override_table(self) -> None:
        """영역 ⑤ override 표 갱신 — 매 _run_transport 후 호출."""
        # [2026-06-07] override 표 UI 제거 → 표가 없으면 아무것도 안 함(가드).
        if getattr(self, '_override_table', None) is None:
            return
        trips = self._displayed_trips
        self._override_table.setRowCount(len(trips))
        for row, trip in enumerate(trips):
            # 회차 #
            no_item = QTableWidgetItem(str(trip.trip_no))
            no_item.setTextAlignment(Qt.AlignCenter)
            self._override_table.setItem(row, 0, no_item)
            # 원래 차량 — _run_transport 가 스냅샷한 pre-override 트럭 이름 사용.
            # (override 적용 후 trip.truck 은 새 트럭이므로 직접 읽으면 안 됨.)
            orig_by_no = getattr(self, "_original_truck_by_no", {})
            orig_name = orig_by_no.get(trip.trip_no, trip.truck.name)
            self._override_table.setItem(row, 1, QTableWidgetItem(orig_name))
            # 변경 콤보 — 모든 트럭(active+) 노출, 현재 truck 또는 override 미리 선택
            combo = QComboBox()
            combo.addItem("(원래대로)", None)
            for t in self._trucks:
                combo.addItem(t.name, t)
            sel_truck = self._trip_overrides.get(trip.trip_no)
            if sel_truck is not None:
                idx = combo.findText(sel_truck.name)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            # 변경 콜백 — trip_no 캡처
            trip_no = trip.trip_no
            combo.currentIndexChanged.connect(
                lambda _i, tn=trip_no, cb=combo: self._on_override_changed(tn, cb)
            )
            self._override_table.setCellWidget(row, 2, combo)
            # 검증 결과 셀 (override 적용 후 성공이면 ✓, 미설정이면 -)
            if trip_no in self._trip_overrides:
                v_item = QTableWidgetItem("✓ 적용됨")
                v_item.setForeground(QBrush(QColor("#1f7a1f")))
            else:
                v_item = QTableWidgetItem("—")
                v_item.setForeground(QBrush(QColor("#888")))
            self._override_table.setItem(row, 3, v_item)

    def _on_override_changed(self, trip_no: int, combo: QComboBox) -> None:
        """override 콤보 변경 → 즉시 검증 + 재실행 트리거.

        실패 시 _apply_overrides 가 폐기 + 진단 메시지 추가. 콤보 자체는
        다음 _render_override_table 호출에서 정리됨.
        """
        truck = combo.currentData()
        if truck is None:
            self._trip_overrides.pop(trip_no, None)
        else:
            self._trip_overrides[trip_no] = truck
        # 즉시 재실행 — 캐시 [패킹] 단계는 유지하고 override 만 다시 적용해도
        # 충분하지만, 운임도 동일 trip 기준이라 _run_transport 전체 재호출이 간단.
        # 옵션 변경과 달리 invalidate_from 호출하지 않음 (패킹 캐시 유지).
        self._run_transport()

    def _clear_overrides(self) -> None:
        if not self._trip_overrides:
            return
        self._trip_overrides.clear()
        self._run_transport()

    # (회차표 우클릭 컨텍스트 메뉴는 2026-05-26 제거 — 트럭 변경은 아래
    # "⑤ 회차별 트럭 변경" 표의 콤보로만 가능.)

    # ── [Phase 7] 카탈로그 다이얼로그 진입점 ──────────────
    def open_catalog_dialog(self) -> None:
        """외부(파일 메뉴 등) 에서도 호출 가능한 공용 진입점."""
        self._open_catalog_dialog()

    def _open_catalog_dialog(self) -> None:
        dlg = TransportCatalogDialog(self, project_root=self._project_root)
        # 다이얼로그가 catalog_changed 발화하면 즉시 카탈로그 재로드.
        # 모달 종료 후 한 번 더 안전망으로 reload (다이얼로그 닫힌 직후
        # 사용자가 콤보 갱신을 기대하므로).
        dlg.catalog_changed.connect(self._reload_catalog)
        dlg.exec_()
        self._reload_catalog()

    def _reload_catalog(self) -> None:
        """카탈로그 JSON 재로드 + 도로 콤보·override 콤보 갱신.

        ㅁ 캐시 무효화: 트럭 후보 목록이 바뀌면 [7] 패킹부터 재실행 필요.
        Phase 9 정밀 무효화 매트릭스 적용 전이라 보수적으로 invalidate_from(7).
        자동 재계산은 발동시키지 않음 — 사용자가 [▷ 운송 계산 실행] 누르도록.
        """
        try:
            self._trucks = load_all_trucks(self._project_root, active_only=True)
        except Exception as e:
            QMessageBox.warning(self, "트럭 카탈로그", f"재로드 실패: {e}")
            return
        # 수동 시뮬레이션·세션 커스텀 트럭은 2026-05-26 제거.
        # override 표는 다음 _run_transport 후 갱신되므로 즉시는 손대지 않음
        self._cache.invalidate_from(7)
        self._refresh_stage_indicators()
        # [개편] 재로드 성공은 정보성 → 알림 생략(카탈로그 다이얼로그가 피드백 제공).

    def set_project_root(self, root) -> None:
        """외부(MainWindow) 에서 프로젝트 루트 주입.

        호출 시 카탈로그 재로드. 호출 전에는 cwd 기준으로 동작.
        """
        from pathlib import Path as _P
        self._project_root = _P(root) if root else None
        self._reload_catalog()

    def _open_references_dialog(self) -> None:
        dlg = TransportReferencesDialog(self)
        dlg.exec_()

    # ── 에러 표시 ─────────────────────────────────────────
    def _show_error(self, msg: str) -> None:
        # [개편] 진단 누적 패널 폐지 → 오류는 알림창으로 직접 표시.
        QMessageBox.warning(self, "운송 오류", msg)


# ── plotly 6.x CSS 호환 처리 ─────────────────────────────
# `:focus-visible` 셀렉터를 Qt WebEngine 5.15 의 Chromium 87 이 파싱 못 해
# insertRule SyntaxError 발생 → plotly.js 전체 실행 중단. 단순 문자열 치환
# `:focus-visible` → `:focus` 로 해결. `:focus` 는 모든 브라우저 100% 지원,
# 동작 의미도 거의 동등 (키보드 포커스 윤곽선 — 마우스 클릭 시도 활성화되는
# 차이만 있음, 운송 도식과 무관).
def _strip_focus_visible_css(html: str) -> str:
    return html.replace(":focus-visible", ":focus")


# ── [Phase 8] 씬 부재(어댑터 결과) 다중 선택 다이얼로그 ───
