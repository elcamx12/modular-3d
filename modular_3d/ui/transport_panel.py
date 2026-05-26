"""운송탭 UI — 분석 ⑤ 영역 ①~④ 구현 (Phase 6).

[탭 구조 — AnalysisPanel 의 4번째 탭으로 추가]
    영역 ① 상단 상태바 (정책 라벨 + 8단계 인디케이터)
    영역 ② 옵션 패널 (도로/거리/속도/간격/단가/캔틸/비내력벽 SpinBox + 자동재계산 OFF)
    영역 ③ 결과 메트릭 5 카드
    영역 ④ sub-탭 (회차표 / 적재율 / Top+Rear 도식 / 경제성)
    [▷ 운송 계산 실행] 버튼이 진입점 — 사용자 결정 ⑧-1: 자동 OFF 기본

[Phase 7 추가]
- ⑤ 회차별 트럭 override 표 — `_build_area_overrides`
- (회차표 우클릭 컨텍스트 메뉴는 2026-05-26 제거 — ⑤ 표 콤보만 사용)
- 상단 [트럭 카탈로그 관리...] 버튼 — `_open_catalog_dialog`

[영역 ⑥~⑧ 는 후속 페이즈]
- ⑥ 수동 시뮬레이션 → Phase 8
- ⑦ 참고자료 → Phase 8
- ⑧ 진단 영역 → Phase 9 (본 페이즈에서는 간단한 QTextEdit 만)

[외부 주입 API]
    tab.set_scene_and_model(scene, model)
    tab.set_design_result(design_result_dict, current_policy)
    tab.set_state(state)
호출 후 사용자가 [▷ 운송 계산 실행] 누르면 어댑터+패커+운임 자동 실행.

[발신 시그널]
- transport_member_highlight(list[int]) — 회차표 행 더블클릭 시 cid 리스트
- transport_blocked(int) — 운송 불가 항목 수
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict
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

from PyQt5.QtCore import Qt, QByteArray, QPoint, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from modular_3d.transport.adapter import (
    TransportError, TransportOptions, build_transport_input,
)
from modular_3d.transport.cache import TransportCache
from modular_3d.transport.catalog_io import load_all_trucks
from modular_3d.transport.economics import EconomicsOptions, compute_economics
from modular_3d.transport.models import SiteLimit, SpacingParams, Truck
from modular_3d.transport.packer import PackResult, Trip, pack_items, recheck_trip_with_truck
from modular_3d.transport.visualizer import draw_rear_view, draw_top_view
from modular_3d.ui.transport_catalog_dialog import TransportCatalogDialog
from modular_3d.ui.transport_references_dialog import TransportReferencesDialog


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
        # 너그러운 기본값(40t/3500/4500). 도로 등급 카탈로그는 2026-05-26 폐지.
        self._site_limit: SiteLimit = SiteLimit(
            max_gvw_kg=40000.0, max_width_mm=3500.0, max_height_mm=4500.0)
        # 프로젝트 설정 객체(운임 1회 고정비 등 읽기용). apply_project_settings 에서 주입.
        self._proj_settings = None

        # [Phase 7] 회차별 override 저장 — {trip_no: new_truck}.
        # 운송계산 재실행 시 자동 적용. recheck_trip_with_truck 실패하면
        # 셀에 빨강 표시 + 진단 메시지. _run_transport 끝에서 적용 단계 호출.
        self._trip_overrides: Dict[int, Truck] = {}
        # override 적용 후의 trip (사용자에게 보여줄 실제 trip 리스트)
        self._displayed_trips: List[Trip] = []

        # [Phase 6 점검 패치 — 결정 4] 자동 재계산 디바운스 (500ms 단발).
        # 사용자가 SpinBox 를 빠르게 연타해도 마지막 변경 후 500ms 지나야 1회만
        # _run_transport 호출. 같은 타이머를 start() 재호출하면 자동 리셋.
        # 사용자 결정 ⑧-2: 500ms.
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._run_transport)

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
        _lock_spin('_interior_spin', float(settings.wall_interior_kg_m2))
        _lock_spin('_exterior_spin', float(settings.wall_exterior_kg_m2))

        combo = getattr(self, '_cost_mode_combo', None)
        if combo is not None:
            idx = combo.findData(settings.cost_mode)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setEnabled(False)
            combo.setToolTip(tip)

        # 현장 운송 제한 — 프로젝트 설정 값으로 SiteLimit 구성 + 읽기전용 표시.
        gvw = (getattr(settings, 'site_limit_gvw_kg', 40000.0)
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
        # [2026-05-27] 좌·우 패널 분리.
        #   좌측 = 입력 (트럭 카탈로그 / 상태 / 옵션 + 운송 계산 실행 / 참고자료)
        #   우측 = 결과 (요약 / 회차표·적재율·경제성 / 회차별 트럭 변경 / 진단)
        # main_3d 운송 탭이 진입 시 *_left_pane_wrap* / *_right_pane_wrap* 을
        # 좌·우 영역에 *직접 reparent* 한다. TransportTab 자체로 표시될 땐 (다른
        # 곳에서 보임 가정 없음) splitter 안에 둘 다 들어있음.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 좌측 입력 패널 ─────────────────────────────────
        from PyQt5.QtWidgets import QSplitter
        self._left_pane_wrap = QWidget()
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(self._left_pane_wrap)
        left_lay = QVBoxLayout(self._left_pane_wrap)
        left_lay.setContentsMargins(4, 4, 4, 4)
        left_lay.setSpacing(8)
        left_lay.addWidget(self._build_area_toolbar())       # 카탈로그 버튼
        left_lay.addWidget(self._build_area_status())        # ① 상태
        left_lay.addWidget(self._build_area_options())       # ② 옵션 + [▷ 실행]
        left_lay.addWidget(self._build_area_references())    # ⑦ 참고자료
        left_lay.addStretch(1)

        # ── 우측 결과 패널 ─────────────────────────────────
        self._right_pane_wrap = QWidget()
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(self._right_pane_wrap)
        right_lay = QVBoxLayout(self._right_pane_wrap)
        right_lay.setContentsMargins(4, 4, 4, 4)
        right_lay.setSpacing(8)
        right_lay.addWidget(self._build_area_metrics())      # ③ 결과 요약
        right_lay.addWidget(self._build_area_subtabs(), stretch=1)  # ④ 회차표/적재율/경제성
        right_lay.addWidget(self._build_area_overrides())    # ⑤ 회차별 트럭 변경
        right_lay.addWidget(self._build_area_diagnostics())  # ⑧ 진단

        # ── TransportTab 본체 splitter (운송 탭에선 좌·우가 reparent 되어 빔) ─
        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.addWidget(left_scroll)
        self._main_splitter.addWidget(right_scroll)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes([320, 700])
        root.addWidget(self._main_splitter, stretch=1)
        # *_left_pane_wrap / _right_pane_wrap* 직접 노출 — main_3d 가 reparent 시 사용
        self._left_pane_scroll = left_scroll
        self._right_pane_scroll = right_scroll

    def _build_area_toolbar(self) -> QWidget:
        """[Phase 7] 운송탭 최상단 — 카탈로그 관리 버튼.

        파일 메뉴(MainWindow) 에서도 동일 진입점을 호출하지만, 운송탭
        내부에서 바로 접근할 수 있도록 버튼을 표면화.
        """
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(2, 2, 2, 2)
        self._catalog_btn = QPushButton("🚚 트럭 카탈로그 관리...")
        self._catalog_btn.setToolTip(
            "내장/프로젝트 트럭 카탈로그 보기 · 추가 · 편집 · 삭제 · 복제"
        )
        self._catalog_btn.clicked.connect(self._open_catalog_dialog)
        row.addWidget(self._catalog_btn)
        row.addStretch(1)
        return wrap

    def _build_area_overrides(self) -> QWidget:
        """[Phase 7] 영역 ⑤ — 회차별 트럭 override 표.

        패킹 결과가 나온 뒤, 사용자가 회차별로 다른 트럭을 강제 지정할 수
        있게 한다. 각 행: [회차 # / 원래 차량 / override 콤보 / 검증]
        override 콤보 변경 → recheck_trip_with_truck 으로 검증 → 실패 시
        검증 셀에 사유 표시 + override 폐기 + 콤보 원복.
        성공 시 회차표·도식·경제성 재렌더에 그대로 반영.
        """
        box = QGroupBox("⑤ 회차별 트럭 변경 (override)")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 8)
        info = QLabel(
            "각 회차의 트럭을 강제 변경합니다. 회차표 행을 우클릭해도 동일 기능 사용 가능."
        )
        info.setStyleSheet("color: #666; font-size: 11px;")
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
        row.addWidget(self._ref_btn)
        row.addStretch(1)
        return wrap

    def _build_area_status(self) -> QWidget:
        """영역 ① — 정책 라벨 + 8단계 인디케이터."""
        box = QGroupBox("① 상태")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        # 정책 동기화 라벨
        self._policy_label = QLabel("정책: <b>(미설정)</b>  ·  층구간 분할: 1")
        self._policy_label.setTextFormat(Qt.RichText)
        lay.addWidget(self._policy_label)
        # 8단계 인디케이터
        ind_row = QHBoxLayout()
        ind_row.setSpacing(4)
        self._stage_labels: Dict[int, QLabel] = {}
        for step, name in _PIPELINE_LABELS:
            lbl = QLabel(f"[{name} ?]")
            lbl.setStyleSheet("padding: 2px 6px; background: #eee; border-radius: 3px;")
            lbl.setToolTip(f"단계 {name} — ✓ 캐시 적중 / ✗ 재계산 필요")
            ind_row.addWidget(lbl)
            if step is not None:
                self._stage_labels[step] = lbl
        ind_row.addStretch(1)
        lay.addLayout(ind_row)
        return box

    def _build_area_options(self) -> QWidget:
        """영역 ② — 운송 옵션 패널."""
        box = QGroupBox("② 운송 옵션")
        lay = QFormLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(4)

        # 현장 운송 제한 — 프로젝트 설정 값(읽기전용 표시). 도로 등급 콤보 폐지.
        self._site_limit_label = QLabel("(프로젝트 설정에서 관리)")
        self._site_limit_label.setStyleSheet("color: #444;")
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

        # (2026-05-27 Phase 1) 캔틸 처리 라디오 폐지 — 캔틸레버는 항상 부모
        # 화물에 기하학적 종속 상태로 같은 회차에 운송된다.

        # 비내력벽 자동판별 그룹
        nb_box = QGroupBox("비내력벽 자동판별")
        nb_lay = QFormLayout(nb_box)
        nb_lay.setContentsMargins(8, 4, 8, 4)
        self._wall_classify_cb = QCheckBox("자동판별 ON")
        self._wall_classify_cb.setChecked(True)
        nb_lay.addRow(self._wall_classify_cb)
        self._interior_spin = QDoubleSpinBox()
        self._interior_spin.setRange(0.0, 999.0)
        self._interior_spin.setValue(30.0)
        self._interior_spin.setSuffix(" kg/m²")
        nb_lay.addRow("내부 단위중량:", self._interior_spin)
        self._exterior_spin = QDoubleSpinBox()
        self._exterior_spin.setRange(0.0, 999.0)
        self._exterior_spin.setValue(55.0)
        self._exterior_spin.setSuffix(" kg/m²")
        nb_lay.addRow("외부 단위중량:", self._exterior_spin)
        self._segment_spin = QSpinBox()
        self._segment_spin.setRange(10, 1000)
        self._segment_spin.setValue(100)
        self._segment_spin.setSuffix(" mm")
        nb_lay.addRow("격자 크기:", self._segment_spin)
        self._floor_obstacle_cb = QCheckBox("FloorPanel 도 인접 장애물로 포함")
        self._floor_obstacle_cb.setChecked(True)
        nb_lay.addRow(self._floor_obstacle_cb)
        lay.addRow(nb_box)

        # 자동 재계산 (기본 OFF — 사용자 결정 ⑧-1)
        self._auto_recompute_cb = QCheckBox("자동 재계산 (옵션 변경 시 자동 실행)")
        self._auto_recompute_cb.setChecked(False)
        lay.addRow(self._auto_recompute_cb)

        # 실행 버튼
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("▷ 운송 계산 실행")
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #2a7ade; color: white; "
            "padding: 6px 16px; font-weight: bold; border-radius: 3px; }"
            "QPushButton:hover { background-color: #1f5fb0; }"
            "QPushButton:disabled { background-color: #bbb; }"
        )
        self._run_btn.clicked.connect(self._run_transport)
        run_row.addWidget(self._run_btn)
        run_row.addStretch(1)
        run_wrap = QWidget(); run_wrap.setLayout(run_row)
        lay.addRow(run_wrap)

        # 자동 재계산 ON 시 위젯 변경 → 디바운스(Phase 9) 없이 즉시 실행
        self._wire_auto_recompute()
        return box

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
            (self._interior_spin.valueChanged, 6),
            (self._exterior_spin.valueChanged, 6),
            (self._wall_classify_cb.toggled, 5),
            (self._segment_spin.valueChanged, 5),
            (self._floor_obstacle_cb.toggled, 5),
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
        self._cache.invalidate_from(step)
        self._refresh_stage_indicators()
        if self._auto_recompute_cb.isChecked():
            self._debounce_timer.start()  # 500ms 단발 — 연타 시 자동 리셋

    def _build_area_metrics(self) -> QWidget:
        """영역 ③ — 5카드 메트릭."""
        box = QGroupBox("③ 결과 요약")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)
        self._metric_labels: Dict[str, QLabel] = {}
        for key, title in [
            ("module_trips", "모듈 회차"),
            ("panel_trips", "패널 회차"),
            ("total_trips", "총 회차"),
            ("avg_util", "평균 적재율"),
            ("total_distance", "총 거리"),
        ]:
            card = QFrame()
            card.setFrameShape(QFrame.StyledPanel)
            card.setStyleSheet("background: #f7f7f7; border-radius: 4px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(6, 4, 6, 4)
            t = QLabel(title)
            t.setStyleSheet("color: #555; font-size: 11px;")
            v = QLabel("—")
            v.setStyleSheet("font-size: 18px; font-weight: bold;")
            cl.addWidget(t)
            cl.addWidget(v)
            lay.addWidget(card)
            self._metric_labels[key] = v
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
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_subtab_trip_table(), "회차표")
        self._sub_tabs.addTab(self._build_subtab_util_chart(), "적재율")
        self._sub_tabs.addTab(self._build_subtab_economics(), "경제성")
        return self._sub_tabs

    def _build_subtab_trip_table(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 2, 2)
        self._trip_table = QTableWidget()
        self._trip_table.setColumnCount(7)
        self._trip_table.setHorizontalHeaderLabels([
            "회차", "차량", "종류", "아이템",
            "화물(kg)", "GVW(kg)", "적재율(%)",
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

    def _build_subtab_util_chart(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 2, 2)
        # matplotlib 캔버스 — 회차별 (중량/길이) 적재율 막대
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as Canvas
        self._util_fig = Figure(figsize=(5, 3), tight_layout=True)
        self._util_canvas = Canvas(self._util_fig)
        lay.addWidget(self._util_canvas)
        return page

    def _build_subtab_views(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 2, 2)
        # JS 콘솔 에러만 출력 (info/log 묵음 — 운영 시 시끄러움 방지)
        from PyQt5.QtWebEngineWidgets import QWebEnginePage

        class _ErrorOnlyPage(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, msg, line, src):
                # level: 0=Info, 1=Warning, 2=Error
                if int(level) >= 2:
                    print(f"[WEB JS ERR] L{line} {msg}", flush=True)
        self._DebugPage = _ErrorOnlyPage
        # 회차 선택 콤보
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("회차 선택:"))
        self._view_combo = QComboBox()
        self._view_combo.currentIndexChanged.connect(self._on_view_combo_changed)
        sel_row.addWidget(self._view_combo, stretch=1)
        sel_wrap = QWidget(); sel_wrap.setLayout(sel_row)
        lay.addWidget(sel_wrap)
        # 좌우 분할
        splitter = QSplitter(Qt.Horizontal)
        self._top_view_web = QWebEngineView()
        self._rear_view_web = QWebEngineView()
        for web in (self._top_view_web, self._rear_view_web):
            web.setMinimumHeight(360)
            # 디버그: JS console 메시지 + load 결과를 콘솔에 찍어 진단
            try:
                web.setPage(self._DebugPage(web))
            except Exception:
                pass
            # loadFinished 로그는 운영 시 필요 없음 — 실패 시에만 출력
            web.loadFinished.connect(
                lambda ok, w=web: (None if ok else print(
                    f"[WEB] load FAILED  url={w.url().toString()[:80]}", flush=True))
            )
        splitter.addWidget(self._top_view_web)
        splitter.addWidget(self._rear_view_web)
        splitter.setSizes([600, 400])
        lay.addWidget(splitter, stretch=1)
        return page

    def _build_subtab_economics(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 2, 2)
        self._eco_table = QTableWidget()
        self._eco_table.setColumnCount(6)
        self._eco_table.setHorizontalHeaderLabels([
            "회차", "차량", "방식", "거리(km)", "적용단가/요금", "운임(원)",
        ])
        eh = self._eco_table.horizontalHeader()
        eh.setSectionResizeMode(QHeaderView.ResizeToContents)
        eh.setStretchLastSection(True)
        self._eco_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._eco_table.setAlternatingRowColors(True)
        self._eco_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # [Phase E 추가 — 2026-05-27] 경제성 행 클릭도 회차표·3D 와 동기화
        self._eco_table.cellClicked.connect(self._on_eco_row_clicked)
        lay.addWidget(self._eco_table)
        # 총합 라벨
        self._eco_total_label = QLabel("총 운임: —")
        self._eco_total_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px;")
        lay.addWidget(self._eco_total_label)
        return page

    def _build_area_diagnostics(self) -> QWidget:
        """영역 ⑧ — 진단 (누적).

        [Phase 9 C-5 결정] 경고 누적 — 재계산해도 과거 경고 유지. 매 실행마다
        타임스탬프 헤더와 함께 append. [전체 지우기] 버튼만 제공.
        """
        box = QGroupBox("⑧ 진단 (누적)")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 8)
        self._diag_text = QTextEdit()
        self._diag_text.setReadOnly(True)
        self._diag_text.setMaximumHeight(150)
        self._diag_text.setFont(QFont("Consolas", 9))
        lay.addWidget(self._diag_text)
        btn_row = QHBoxLayout()
        clr_btn = QPushButton("전체 지우기")
        clr_btn.clicked.connect(self._diag_text.clear)
        btn_row.addWidget(clr_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        return box

    def _diag_append(self, text: str, header: bool = False) -> None:
        """[Phase 9 C-5] 진단 누적 추가 — 타임스탬프 prefix."""
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if header:
            self._diag_text.append(f"\n━━━ {ts} ━━━")
            self._diag_text.append(text)
        else:
            self._diag_text.append(f"[{ts}] {text}")

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
        return TransportOptions(
            include_cantilever=True,
            wall_classifier_enabled=self._wall_classify_cb.isChecked(),
            interior_wall_unit_weight=float(self._interior_spin.value()),
            exterior_wall_unit_weight=float(self._exterior_spin.value()),
            wall_segment_size_mm=float(self._segment_spin.value()),
            include_floor_panels_as_obstacle=self._floor_obstacle_cb.isChecked(),
        )

    def _read_spacing(self) -> SpacingParams:
        # 적재 간격은 내장 고정값 (수직100·수평100·양끝200·폭여유200).
        return SpacingParams()

    def _read_economics_options(self) -> EconomicsOptions:
        ps = self._proj_settings
        return EconomicsOptions(
            distance_km=float(self._distance_spin.value()),
            cost_mode=self._cost_mode_combo.currentData() or "freight_table",
            lowbed_per_km_krw=float(self._lowbed_per_km_spin.value()),
            extendable_per_km_krw=float(self._extend_per_km_spin.value()),
            aframe_per_km_krw=float(self._aframe_per_km_spin.value()),
            lowbed_fixed_krw=float(getattr(ps, 'lowbed_fixed_krw', 600000.0)),
            extendable_fixed_krw=float(getattr(ps, 'extendable_fixed_krw', 700000.0)),
            aframe_fixed_krw=float(getattr(ps, 'aframe_fixed_krw', 800000.0)),
        )

    # ── 메인 실행 ─────────────────────────────────────────
    def _run_transport(self) -> None:
        if not self._design_results or self._current_policy not in self._design_results:
            self._show_error("구조해석 + 단면 산정을 먼저 실행하세요.")
            return
        site = self._site_limit
        self.set_state("Computing")
        try:
            options = self._read_options()
            spacing = self._read_spacing()
            economics = self._read_economics_options()
            design = self._design_results[self._current_policy]
            pack = self._cache.get_or_compute_pack(
                self._scene, self._model, design, self._current_policy,
                options, self._trucks, site, spacing,
                economics=economics,
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
        self._render_util_chart(pack)
        # [Phase C 핫픽스] _render_view_combo 호출 제거 — 도식 sub-탭 폐기 후
        # 위젯 자체가 GC 되어 deleted C++ object 접근 RuntimeError 발생.
        # 3D 적재 도식은 transport_pack_updated 신호로 MainWindow center pane 에서 처리.
        self._render_economics(eco)
        self._render_diagnostics(self._cache.transport_input)
        self._render_override_table()
        self._refresh_stage_indicators()

        if pack.blocked:
            self.transport_blocked.emit(len(pack.blocked))
        # [Phase C] center pane 3D 도식 갱신 신호
        self.transport_pack_updated.emit(pack, self._read_spacing())
        self.set_state("ResultShown")

    # ── 렌더링 ────────────────────────────────────────────
    def _render_metrics(self, pack: PackResult) -> None:
        self._metric_labels["module_trips"].setText(f"{pack.module_trips}")
        self._metric_labels["panel_trips"].setText(f"{pack.panel_trips}")
        self._metric_labels["total_trips"].setText(f"{pack.total_trips}")
        self._metric_labels["avg_util"].setText(f"{pack.avg_utilization:.1f}%")
        # 총 거리 = 편도거리 × 회차 × 2 (운임 모델이 항상 왕복 기준이므로 일치시킴).
        # (2026-05-24 운임 개편으로 왕복 토글 제거 — 항상 왕복으로 계산.)
        dist = self._distance_spin.value() * pack.total_trips * 2
        self._metric_labels["total_distance"].setText(f"{dist:,} km")

    def _render_trip_table(self, pack: PackResult) -> None:
        self._trip_table.setRowCount(len(pack.trips))
        for row, trip in enumerate(pack.trips):
            items_str = ", ".join(getattr(i, "name", "?") for i in trip.items)
            stacked = [s for s in trip.stacked_items if s is not None]
            if stacked:
                items_str += " + 적층: " + ", ".join(getattr(s, "name", "?") for s in stacked)
            for col, val in enumerate([
                str(trip.trip_no), trip.truck.name, trip.kind, items_str,
                f"{trip.cargo_weight:,.0f}", f"{trip.gross_weight:,.0f}",
                f"{trip.utilization:.1f}",
            ]):
                cell = QTableWidgetItem(val)
                if col in (4, 5, 6):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._trip_table.setItem(row, col, cell)

    def _render_util_chart(self, pack: PackResult) -> None:
        self._util_fig.clear()
        ax = self._util_fig.add_subplot(111)
        if not pack.trips:
            ax.text(0.5, 0.5, "회차 없음", ha="center", va="center",
                    transform=ax.transAxes, color="gray")
        else:
            xs = list(range(1, len(pack.trips) + 1))
            wt = [t.weight_utilization for t in pack.trips]
            ln = [t.length_utilization for t in pack.trips]
            w = 0.4
            ax.bar([x - w / 2 for x in xs], wt, width=w, label="중량 적재율 %", color="#2a7ade")
            ax.bar([x + w / 2 for x in xs], ln, width=w, label="길이 적재율 %", color="#e07a3b")
            ax.set_xlabel("회차 #")
            ax.set_ylabel("적재율 (%)")
            ax.set_xticks(xs)
            ax.set_ylim(0, max(105, max(wt + ln) + 5))
            ax.axhline(100, color="red", linestyle="--", linewidth=0.8)
            ax.legend(loc="best", fontsize=8)
            ax.grid(axis="y", linestyle=":", alpha=0.5)
        self._util_canvas.draw_idle()

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
            # 진단 패널이 있으면 거기 출력, 없으면 콘솔
            if hasattr(self, "_diag_append"):
                self._diag_append(
                    f"[도식 렌더 실패] 회차 #{getattr(trip, 'trip_no', '?')} — {type(e).__name__}: {e}\n{tb}",
                    header=True,
                )
            else:
                print(f"[도식 렌더 실패] {tb}", flush=True)
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

    # 운임 방식 코드 → 화면 표시 라벨
    _MODE_LABELS = {"freight_table": "요금표", "per_km": "km단가"}

    def _render_economics(self, eco) -> None:
        self._eco_table.setRowCount(len(eco.trips))
        for row, tc in enumerate(eco.trips):
            mode_label = self._MODE_LABELS.get(tc.pricing_mode, tc.pricing_mode)
            for col, val in enumerate([
                str(tc.trip_no), tc.truck_name, mode_label,
                f"{tc.distance_km:.1f}", tc.rate_label,
                f"{tc.cost_krw:,.0f}",
            ]):
                cell = QTableWidgetItem(val)
                if col >= 3:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._eco_table.setItem(row, col, cell)
        # 운임 방식별 내역 표시
        mode = getattr(eco.options, "cost_mode", "freight_table")
        if mode == "freight_table":
            breakdown = "요금표(전국특송24시콜) 기준"
        else:
            breakdown = "트레일러별 km단가 기준"
        self._eco_total_label.setText(
            f"총 운임: <b>₩{eco.total_cost_krw:,.0f}</b>  "
            f"({breakdown})  "
            f"· 평균/회차 ₩{eco.average_per_trip_krw:,.0f}"
        )
        self._eco_total_label.setTextFormat(Qt.RichText)

    def _render_diagnostics(self, ti) -> None:
        """[2026-05-26] 진단 표시 — 사용자에게 의미 있는 항목만, 일관된 형식으로.

        - 코어/코어슬래브(RC) 는 운송 대상이 아니므로 표기하지 않음.
        - 캔틸레버·모듈 내부 보강재 같은 종속 부재는 개별 나열 대신 종류별
          요약 한 줄로(예: "캔틸레버 3개 → 부모 모듈에 흡수").
        - 운송 불가(blocked) 사유는 부재 이름 + 사유 한 묶음으로 표시.
        """
        lines: List[str] = []
        if ti is None:
            self._diag_append("(어댑터 결과 없음)", header=True)
            return
        diag = getattr(ti, "diagnostics", None)
        if diag is not None:
            for w in diag.warnings:
                lines.append(f"⚠ {w}")
            for info in diag.info:
                lines.append(f"ℹ {info}")
        # 제외 항목 — 코어는 숨김, 캔틸/내부보강재 등은 종류별 1줄 요약.
        from collections import defaultdict
        excl_by_type: Dict[str, int] = defaultdict(int)
        for ex in getattr(ti, "excluded", []):
            tn = getattr(ex, "type_name", "") or ""
            if "코어" in tn:
                continue  # RC 코어/슬래브는 표기 안 함
            excl_by_type[tn] += 1
        for tn, n in excl_by_type.items():
            # 캔틸레버·모듈 내부 보강재 같은 종속 부재는 부모에 흡수됨을 명시.
            if "캔틸" in tn or "보강재" in tn:
                lines.append(f"ℹ {tn} {n}개 → 부모 모듈에 흡수")
            else:
                lines.append(f"ℹ {tn} {n}개 — 운송 대상 제외")
        if self._last_pack is not None:
            for it, reason in self._last_pack.blocked:
                lines.append(
                    f"❌ 운송 불가: {getattr(it, 'name', '?')}\n  사유: {reason}"
                )
        if not lines:
            lines.append("(경고 없음)")
        self._diag_append("\n".join(lines), header=True)

    # ── 상태 ──────────────────────────────────────────────
    def _refresh_stage_indicators(self) -> None:
        flags = self._cache.cache_status_flags()
        for step, name in _PIPELINE_LABELS:
            if step is None:
                continue
            lbl = self._stage_labels[step]
            flag = flags.get(step, "✗")
            color = "#a3e635" if flag == "✓" else "#fca5a5"
            lbl.setText(f"[{name} {flag}]")
            lbl.setStyleSheet(
                f"padding: 2px 6px; background: {color}; border-radius: 3px;"
            )

    def _update_policy_label(self) -> None:
        self._policy_label.setText(
            f"정책: <b>{self._current_policy}</b>  "
            f"·  층구간 분할: <i>(물량탭 동기화)</i>"
        )

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
        self._update_policy_label()
        self._refresh_stage_indicators()
        if self._auto_recompute_cb.isChecked():
            self._run_transport()

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

    def _on_eco_row_clicked(self, row: int, col: int) -> None:
        """경제성 sub-탭 행 단일 클릭 → 동일 진입점.

        경제성 표의 첫 컬럼이 회차 번호 (str). int 변환 후 select_trip.
        """
        if self._last_pack is None:
            return
        item = self._eco_table.item(row, 0)
        if item is None:
            return
        try:
            trip_no = int(item.text())
        except ValueError:
            return
        self.select_trip(trip_no)

    def select_trip(self, trip_no: int) -> None:
        """공통 회차 선택 진입점 — 회차표·경제성표 둘 다 *프로그래밍 선택* 후 신호 emit.

        외부(3D 트럭 클릭) 에서도 호출 가능. 양방향 동기화의 hub.
        selectRow 는 cellClicked 를 발화 안 시켜서 무한 루프 X.
        """
        # 회차표 행 찾아 선택
        for row in range(self._trip_table.rowCount()):
            item = self._trip_table.item(row, 0)
            if item is not None and item.text() == str(trip_no):
                self._trip_table.selectRow(row)
                break
        # 경제성 표 행 찾아 선택
        for row in range(self._eco_table.rowCount()):
            item = self._eco_table.item(row, 0)
            if item is not None and item.text() == str(trip_no):
                self._eco_table.selectRow(row)
                break
        # 3D 도식 강조 신호
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
            self._diag_append("\n".join(msgs))
        # PackResult.avg_utilization / total_trips 등은 모두 trips 기반 @property 라
        # trips 만 교체하면 자동 재계산된다 (avg_utilization 은 set 불가 — 대입 금지).
        # blocked 는 원본 유지. dataclasses.replace 로 새 인스턴스 생성.
        from dataclasses import replace as _dc_replace
        return _dc_replace(pack, trips=new_trips)

    def _render_override_table(self) -> None:
        """영역 ⑤ override 표 갱신 — 매 _run_transport 후 호출."""
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
            self._diag_append(f"⚠ 트럭 카탈로그 재로드 실패: {e}")
            return
        # 수동 시뮬레이션·세션 커스텀 트럭은 2026-05-26 제거.
        # override 표는 다음 _run_transport 후 갱신되므로 즉시는 손대지 않음
        self._cache.invalidate_from(7)
        self._refresh_stage_indicators()
        # 진단에 알림 누적
        self._diag_append(f"ℹ 트럭 카탈로그 재로드 완료 ({len(self._trucks)} 종).")

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
        self._diag_append(f"❌ {msg}")


# ── plotly 6.x CSS 호환 처리 ─────────────────────────────
# `:focus-visible` 셀렉터를 Qt WebEngine 5.15 의 Chromium 87 이 파싱 못 해
# insertRule SyntaxError 발생 → plotly.js 전체 실행 중단. 단순 문자열 치환
# `:focus-visible` → `:focus` 로 해결. `:focus` 는 모든 브라우저 100% 지원,
# 동작 의미도 거의 동등 (키보드 포커스 윤곽선 — 마우스 클릭 시도 활성화되는
# 차이만 있음, 운송 도식과 무관).
def _strip_focus_visible_css(html: str) -> str:
    return html.replace(":focus-visible", ":focus")


# ── [Phase 8] 씬 부재(어댑터 결과) 다중 선택 다이얼로그 ───
