"""AlignmentCanvasThree — QWebEngineView + Three.js 기반 2D 평면 뷰 (M3 골격).

[설계 근거]
기존 AlignmentCanvas (vispy 기반 QLabel + QPainter) 와 *나란히* 마운트되어
같은 paint 상태를 *읽기 전용* 으로 표시한다. 입력 처리 없음.

자세한 결정은 `UI_마이그레이션/06_M3_설계.md`.

[데이터 흐름]
- AlignmentCanvas.paintEvent → _three_sync_callback(state_dict) 호출
- AlignmentDockPanelThree 가 콜백에 자기 sync 메서드 등록
- sync() 가 AlignmentCanvasThree.apply_paint_state(state) 호출
- 본 클래스가 state 를 JSON 직렬화 후 JS `window.applyPaintState(state)` 실행

[구현 단계]
- M3-a (현재): JS 호출 큐잉, JS API 시그너처만 — 본문 빈 함수
- M3-b: 부재 사각형 + 실 폴리곤 + viewport 동기화 구현
- M3-c: 선택·정렬·고스트·스냅·도움말
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import QUrl, QObject, pyqtSlot
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel


_TEMPLATE_PATH = Path(__file__).resolve().parent / 'alignment_view_three_template.html'


class _AlignmentThreeBridge(QObject):
    """JS → Python 브리지.

    M4 — three.js 2D 가 마우스 입력 주체가 되어 클릭·호버를 Python 으로 전달.
    실제 처리는 콜백(on_click_cb 등)에 연결됨 — AlignmentDockPanelThree 가
    vispy AlignmentCanvas 의 world 좌표 처리 메서드로 위임 (M4-b).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_click_cb = None   # fn(wx, wy, button, ctrl)
        self.on_move_cb = None    # fn(wx, wy)
        self.on_key_cb = None      # fn(key, text, shift)

    @pyqtSlot(str)
    def js_log(self, msg: str) -> None:
        pass

    @pyqtSlot(float, float, int, bool)
    def on_2d_click(self, wx: float, wy: float, button: int,
                    ctrl: bool = False) -> None:
        if self.on_click_cb is not None:
            try:
                self.on_click_cb(wx, wy, button, bool(ctrl))
            except Exception as e:
                print(f'[bridge.on_2d_click] {e}')

    @pyqtSlot(float, float)
    def on_2d_move(self, wx: float, wy: float) -> None:
        if self.on_move_cb is not None:
            try:
                self.on_move_cb(wx, wy)
            except Exception as e:
                print(f'[bridge.on_2d_move] {e}')

    @pyqtSlot(str, str, bool)
    def on_2d_key(self, js_key: str, text: str, shift: bool = False) -> None:
        if self.on_key_cb is not None:
            try:
                self.on_key_cb(js_key, text, bool(shift))
            except Exception as e:
                print(f'[bridge.on_2d_key] {e}')


def _to_json(payload) -> str:
    """JSON 직렬화 — JS 안전 (백슬래시 이스케이프 처리는 json 모듈이 자동)."""
    return json.dumps(payload, ensure_ascii=False, default=_json_default)


def _json_default(o):
    """numpy / tuple / 기타 객체 직렬화 fallback."""
    try:
        import numpy as np
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
    except ImportError:
        pass
    if isinstance(o, tuple):
        return list(o)
    # ComponentType / enum 등
    if hasattr(o, 'value'):
        return o.value
    return str(o)


class AlignmentCanvasThree:
    """three.js 기반 2D 평면 뷰어 — *읽기 전용* 표시 위젯.

    AlignmentCanvas 와 *같은 world 좌표 (mm)* 를 그대로 사용. 단위/축 변환 없음.
    OrthographicCamera 로 위에서 내려다보는 평면 시점.
    """

    def __init__(self):
        self._view = QWebEngineView()
        self._loaded = False
        self._pending_calls: list[str] = []

        # WebChannel — JS 측 디버그 로그용
        self._channel = QWebChannel()
        self._bridge = _AlignmentThreeBridge()
        self._channel.registerObject('bridge', self._bridge)
        self._view.page().setWebChannel(self._channel)

        self._view.loadFinished.connect(self._on_load_finished)

        # HTML 인라인 로드
        if _TEMPLATE_PATH.exists():
            html = _TEMPLATE_PATH.read_text(encoding='utf-8')
        else:
            html = ('<html><body>AlignmentCanvasThree template missing</body>'
                    '</html>')
        # three.js 라이브러리 CDN → 로컬 인라인(오프라인 지원). vendor 없으면 CDN 폴백.
        from modular_3d.render.three_assets import inline_three_libs
        html = inline_three_libs(html)
        self._view.setHtml(html, QUrl('about:blank'))

    # ── Qt 위젯 접근 ────────────────────────────────────────
    def get_native_widget(self):
        return self._view

    # ── M4 — JS 입력 콜백 연결 (AlignmentDockPanelThree 가 호출) ──
    def set_input_callbacks(self, on_click=None, on_move=None, on_key=None):
        """three.js 2D 의 클릭/호버/키 입력을 Python 처리 함수에 연결."""
        if on_click is not None:
            self._bridge.on_click_cb = on_click
        if on_move is not None:
            self._bridge.on_move_cb = on_move
        if on_key is not None:
            self._bridge.on_key_cb = on_key

    # ── 내부 헬퍼 ───────────────────────────────────────────
    def _on_load_finished(self, ok: bool) -> None:
        self._loaded = bool(ok)
        if not ok:
            from modular_3d._utils.debug import log_error
            log_error('AlignmentCanvasThree HTML 로드 실패', cat='alignment')
            return
        for js in self._pending_calls:
            self._view.page().runJavaScript(js)
        self._pending_calls.clear()

    def _run_js(self, js: str) -> None:
        if self._loaded:
            self._view.page().runJavaScript(js)
        else:
            self._pending_calls.append(js)

    # ────────────────────────────────────────────────────────
    # 외부 API — AlignmentDockPanelThree 가 호출
    # ────────────────────────────────────────────────────────
    def apply_paint_state(self, state: dict) -> None:
        """paint 상태 전체를 JS 측에 전달. JS 가 *전체 redraw*."""
        payload = _to_json(state)
        self._run_js(f'window.applyPaintState({payload});')

    def update_viewport_only(self, viewport: dict) -> None:
        """카메라(viewport) 만 갱신 — 팬·줌 시 대용량 전송·redraw 회피.

        콘텐츠(부재·실·하이라이트)가 그대로일 때 사용. 작은 JSON 만 전송.
        """
        payload = _to_json(viewport)
        self._run_js(f'window.updateViewportOnly({payload});')


__all__ = ['AlignmentCanvasThree']
