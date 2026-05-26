"""공정표 탭 패널 — 팀원 HTML(모듈러주택_공정표.html) 임베드 + 자동주입.

[설계 근거]
- 공정표_이식_계획서.md Phase A·C.
- QWebEngineView 단일 위젯으로 팀원 HTML 을 그대로 띄운다.
- 탭 진입 시 우리 schedule_adapter 가 만든 데이터를 JS 의 importScene 으로 자동주입.
- 모듈 타입(multi-type)은 importScene 의 압축을 우회해 JS 전역 `moduleTypes` 에 직접 주입(B2 우회).

[정책]
- HTML 의 외부 의존성(CDN 등) 없음. refs/ 폴더의 PNG 만 상대경로로 참조 → 본 패널은
  HTML 파일을 *로컬 URL* 로 로드해야 함 (setHtml 으론 상대경로 이미지 못 가져옴).
- JS 호출은 *페이지 loadFinished 이후* 에만 안전. 그 전 호출은 큐에 쌓아 두었다가
  loadFinished 시그널에서 일괄 적용.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWidgets import QWidget, QVBoxLayout


# ─────────────────────────────────────────────────────────
# 공정표 HTML ↔ Python 브리지 (Phase L)
# ─────────────────────────────────────────────────────────
class _ScheduleBridge(QObject):
    """공정표 HTML 의 calc() 가 결과를 푸시할 통로.

    JS 측에서 `scheduleBridge.on_schedule_calculated(json_str)` 호출하면,
    Python 측 `schedule_payload_pushed(dict)` 시그널이 발화된다.
    """

    schedule_payload_pushed = pyqtSignal(dict)

    @pyqtSlot(str)
    def on_schedule_calculated(self, payload_json: str) -> None:
        try:
            data = json.loads(payload_json) if payload_json else {}
        except Exception:
            data = {}
        if isinstance(data, dict):
            self.schedule_payload_pushed.emit(data)


# 본 파일 위치 기준 ../schedule/ 폴더에 HTML 이 있음.
_HERE = Path(__file__).resolve().parent
_SCHEDULE_DIR = _HERE.parent / "schedule"
_SCHEDULE_HTML = _SCHEDULE_DIR / "모듈러주택_공정표.html"


class SchedulePanel(QWidget):
    """공정표 탭의 루트 위젯.

    레이아웃: QWebEngineView 가 페이지 전체를 채움.

    공개 API:
    - apply_scene_data(data): schedule_adapter.build_scene_data() 결과를 주입.
      페이지가 아직 로드 안 됐으면 큐에 저장 → loadFinished 시 자동 적용.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PyQt5.QtWebEngineWidgets import QWebEngineView

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._web = QWebEngineView()
        v.addWidget(self._web, stretch=1)

        self._page_loaded: bool = False
        self._pending_data: Optional[Dict[str, Any]] = None

        # [Phase L] QWebChannel + ScheduleBridge — 공정표 calc() 가 결과를 푸시.
        self._bridge = _ScheduleBridge()
        self._channel = QWebChannel()
        self._channel.registerObject("scheduleBridge", self._bridge)
        self._web.page().setWebChannel(self._channel)

        # 로드 완료 시 큐 처리
        self._web.loadFinished.connect(self._on_load_finished)

        if _SCHEDULE_HTML.exists():
            self._web.load(QUrl.fromLocalFile(str(_SCHEDULE_HTML)))
        else:
            self._web.setHtml(
                "<html><body style='margin:0;padding:32px;"
                "background:#0d1117;color:#e6edf3;font-family:Segoe UI,sans-serif;'>"
                "<h2>공정표 HTML 을 찾을 수 없습니다</h2>"
                f"<p>예상 경로: <code>{_SCHEDULE_HTML}</code></p>"
                "</body></html>"
            )

    def web_view(self):
        return self._web

    def bridge(self) -> "_ScheduleBridge":
        """외부(main)가 schedule_payload_pushed 시그널을 받기 위한 핸들."""
        return self._bridge

    # ── 자동주입 진입점 ──────────────────────────────────────
    def apply_scene_data(self, data: Dict[str, Any]) -> None:
        """schedule_adapter.build_scene_data() 결과를 페이지에 주입.

        구조: {summary, components, module_types}
        """
        if not isinstance(data, dict):
            return
        if not self._page_loaded:
            # 로드 전이면 큐에 저장 — loadFinished 에서 일괄 적용
            self._pending_data = data
            return
        self._apply_now(data)

    # ── 내부 ─────────────────────────────────────────────────
    def _on_load_finished(self, ok: bool) -> None:
        self._page_loaded = bool(ok)
        if ok and self._pending_data is not None:
            data = self._pending_data
            self._pending_data = None
            self._apply_now(data)

    def _apply_now(self, data: Dict[str, Any]) -> None:
        """JS 측에 importScene + moduleTypes + project(지역·착공일) 자동주입.

        [순서]
        1) importScene(json) 호출 — 세대층·층당세대·바닥면적·코어바닥면적·접합 카운트를 채움.
        2) module_types 가 있으면 JS 전역 `moduleTypes` 에 직접 박고 renderModuleTable() 재호출.
           (importScene 의 단일 압축을 우회 — B2)
        3) project.region_key 가 있으면 i_지역 select 에 적용하고 onRegionChange() 호출
           → 가동율 input 도 자동 갱신.
        4) project.start_date 가 있으면 pickedDate 갱신 + datePickerDisplay 라벨 갱신.
        """
        scene_payload = {
            "summary": data.get("summary", {}),
            "components": data.get("components", []),
        }
        module_types = data.get("module_types") or []
        project = data.get("project") or {}
        try:
            scene_js = json.dumps(scene_payload, ensure_ascii=False)
            types_js = json.dumps(module_types, ensure_ascii=False)
            project_js = json.dumps(project, ensure_ascii=False)
        except Exception:
            return
        # JS — importScene + moduleTypes 덮어쓰기 + project(지역/시작일) 적용.
        js = (
            "(function(){ try {"
            f"  var __scene = {scene_js};"
            f"  var __types = {types_js};"
            f"  var __proj  = {project_js};"
            "   if (typeof importScene === 'function') {"
            "     importScene(__scene, '메인 프로그램 자동주입');"
            "   }"
            "   if (Array.isArray(__types) && __types.length > 0"
            "       && typeof renderModuleTable === 'function') {"
            "     window.moduleTypes = __types;"
            "     renderModuleTable();"
            "   }"
            "   if (__proj && __proj.region_key"
            "       && typeof REGION_GADONG !== 'undefined'"
            "       && REGION_GADONG[__proj.region_key] != null) {"
            "     var sel = document.getElementById('i_지역');"
            "     if (sel) { sel.value = __proj.region_key; }"
            "     if (typeof onRegionChange === 'function') { onRegionChange(); }"
            "   }"
            "   if (__proj && __proj.start_date) {"
            "     var d = new Date(__proj.start_date);"
            "     if (!isNaN(d.getTime()) && typeof selectDate === 'function') {"
            "       selectDate(d.getFullYear(), d.getMonth(), d.getDate());"
            "     }"
            "   }"
            "   if (typeof calc === 'function') { calc(); }"
            " } catch(e) { console.error('schedule auto-inject:', e); } })();"
        )
        self._web.page().runJavaScript(js)
