"""Phase 6 단위 테스트 — TransportTab UI 위젯.

[환경 제약]
샌드박스 환경에서 QApplication 인스턴스화가 차단되므로(GUI 디스플레이 없음),
본 테스트는 (1) 모듈 임포트 가능성, (2) 클래스/메서드 시그니처, (3) 시그널
정의, (4) AnalysisPanel 통합 코드 경로 정적 검증만 수행한다.

GUI 동작 회귀(위젯 인스턴스 + 메서드 호출 + 렌더링)는 사용자가 직접 자기
데스크탑에서 modular_3d 메인 윈도우를 띄워 확인 (수동 회귀).

[검증]
- transport_panel 모듈 import 가능
- TransportTab 클래스 + 핵심 메서드/시그널 존재
- AnalysisPanel 의 _build_transport_tab / populate_transport / policy_changed
  시그널 존재 (정적)
"""
from __future__ import annotations

import inspect

import pytest

# ── 모듈 임포트 (PyQt5 / WebEngine 의존) ─────────────────
def test_transport_panel_module_imports():
    import modular_3d.ui.transport_panel as tp
    assert hasattr(tp, "TransportTab")


def test_analysis_panel_module_imports():
    import modular_3d.ui.analysis_panel as ap
    assert hasattr(ap, "AnalysisPanel")


# ── TransportTab 클래스 검증 ────────────────────────────
def test_transport_tab_has_signals():
    from modular_3d.ui.transport_panel import TransportTab
    # pyqtSignal 은 class attribute 로 등록되어 있음
    assert "transport_member_highlight" in dir(TransportTab)
    assert "transport_blocked" in dir(TransportTab)


def test_transport_tab_has_core_methods():
    from modular_3d.ui.transport_panel import TransportTab
    required = [
        "_build_ui", "_build_area_status", "_build_area_options",
        "_build_area_metrics", "_build_area_subtabs",
        "_build_subtab_trip_table", "_build_subtab_util_chart",
        "_build_subtab_economics",
        "set_scene_and_model", "set_design_result", "set_state",
        "_read_options", "_read_spacing", "_read_economics_options",
        "_run_transport",
        "_render_metrics", "_render_trip_table", "_render_util_chart",
        "_render_view_combo", "_render_views", "_render_economics",
        "_render_diagnostics", "_refresh_stage_indicators",
        "on_policy_sync", "on_scene_modified",
        "_on_trip_row_double_clicked", "_show_error",
    ]
    for name in required:
        assert hasattr(TransportTab, name), f"missing method: {name}"


def test_transport_tab_init_signature():
    from modular_3d.ui.transport_panel import TransportTab
    sig = inspect.signature(TransportTab.__init__)
    params = list(sig.parameters.keys())
    assert params[0] == "self"
    assert "parent" in params


# ── Phase 7~9 신규 메서드 정적 검증 (회귀 방지) ──────────
def test_transport_tab_phase7_9_methods():
    from modular_3d.ui.transport_panel import TransportTab
    required = [
        # Phase 7 — 카탈로그·override
        "_build_area_toolbar", "_build_area_overrides",
        "_open_catalog_dialog", "open_catalog_dialog", "_reload_catalog",
        "set_project_root", "_apply_overrides", "_render_override_table",
        "_on_override_changed", "_clear_overrides",
        # (회차표 우클릭 메뉴는 2026-05-26 제거됨)
        # 참고자료 / 공유 헬퍼 / 진단
        # (수동 시뮬레이션 관련 메서드는 2026-05-26 제거됨)
        "_build_area_references", "_open_references_dialog",
        "_render_trip_pair", "_diag_append",
    ]
    for name in required:
        assert hasattr(TransportTab, name), f"missing method: {name}"


def test_apply_overrides_signature():
    """_apply_overrides 는 PackResult 받아 PackResult 반환 (1 인자)."""
    from modular_3d.ui.transport_panel import TransportTab
    sig = inspect.signature(TransportTab._apply_overrides)
    params = list(sig.parameters.keys())
    assert params == ["self", "pack"]


def test_on_option_changed_takes_step():
    """[Phase 9 결정 3] _on_option_changed 가 step 인자 수신 (정밀 무효화)."""
    from modular_3d.ui.transport_panel import TransportTab
    sig = inspect.signature(TransportTab._on_option_changed)
    assert "step" in sig.parameters


# ── 다이얼로그 모듈 임포트 ──────────────────────────────
def test_dialog_modules_import():
    # (transport_temp_cargo_dialog 는 2026-05-26 수동 시뮬레이션 제거와 함께 삭제)
    from modular_3d.ui.transport_catalog_dialog import TransportCatalogDialog
    from modular_3d.ui.transport_references_dialog import TransportReferencesDialog
    assert TransportCatalogDialog is not None
    assert TransportReferencesDialog is not None


# ── AnalysisPanel 통합 ──────────────────────────────────
def test_analysis_panel_has_policy_changed_signal():
    from modular_3d.ui.analysis_panel import AnalysisPanel
    # PyQt 시그널은 class attribute. 단순 dir 검사.
    assert "policy_changed" in dir(AnalysisPanel)


def test_analysis_panel_has_build_transport_tab_method():
    from modular_3d.ui.analysis_panel import AnalysisPanel
    assert hasattr(AnalysisPanel, "_build_transport_tab")
    assert hasattr(AnalysisPanel, "populate_transport")
    assert hasattr(AnalysisPanel, "_on_transport_member_highlight")
    assert hasattr(AnalysisPanel, "_on_transport_blocked")


# ── _PIPELINE_LABELS 데이터 검증 ──────────────────────────
def test_pipeline_labels_constant():
    from modular_3d.ui.transport_panel import _PIPELINE_LABELS
    # 8단계 = 해석/단면/분할/분류/변환/패킹/운임/그림 = 8 항목
    assert len(_PIPELINE_LABELS) == 8
    names = [n for _, n in _PIPELINE_LABELS]
    for expected in ("해석", "단면", "분할", "분류", "변환", "패킹", "운임", "그림"):
        assert expected in names


# ── (스킵) GUI 인스턴스화 테스트 ─────────────────────────
@pytest.mark.skip(reason="sandbox 환경에서 QApplication 인스턴스화 불가 — 수동 회귀")
def test_gui_instance_smoke():
    """사용자가 실제 데스크탑에서 직접 modular_3d 메인 윈도우 띄워 확인.

    체크 리스트 (수동):
    1. 운송탭이 4번째 탭으로 보임
    2. 옵션 패널의 모든 SpinBox/Checkbox 동작
    3. [▷ 운송 계산 실행] 버튼이 처음엔 비활성
    4. 구조해석 + 단면 산정 후 활성
    5. 클릭 → 메트릭/회차표/적재율/도식/경제성 갱신
    6. 회차표 행 더블클릭 → 3D 씬 부재 강조
    7. 정책 변경 → 운송탭 라벨도 즉시 변경
    """
    pass
