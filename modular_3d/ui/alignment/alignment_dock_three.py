"""AlignmentDockPanelThree — 기존 AlignmentDockPanel 옆에 마운트되는 *읽기 전용*
three.js 2D 평면 뷰 어댑터 (M3 골격).

[설계 근거]
M3 단계는 입력 처리 없음 — 모든 마우스/키는 vispy AlignmentCanvas 가 받고,
three.js 측은 그 paint 상태를 *동기화 받아 표시* 만 한다.

[연결 흐름]
- main_3d / define_tab 이 AlignmentDockPanelThree 인스턴스화
- 자체 AlignmentCanvasThree 보유
- `attach_to(vispy_canvas)` 로 vispy AlignmentCanvas 의 `_three_sync_callback` 등록
- vispy paintEvent 끝마다 sync(state) → JS 측 applyPaintState(state)

자세한 결정은 `UI_마이그레이션/06_M3_설계.md`.
"""
from __future__ import annotations

import json

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QSizePolicy

from modular_3d.ui.alignment.alignment_view_three import AlignmentCanvasThree


class AlignmentDockPanelThree(QWidget):
    """three.js 2D 평면 뷰 패널.

    main_3d / define_tab 이 *기존 AlignmentDockPanel 과 나란히* 마운트 (세로 분할).
    `attach_to(vispy_canvas)` 로 vispy 측 paint 후크에 자기 sync 메서드 등록.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(80, 60)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        self._canvas_three = AlignmentCanvasThree()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._canvas_three.get_native_widget())

        # 후크 등록된 AlignmentCanvas 참조 (해제 시 사용)
        self._attached_canvas = None

        # ── 성능: sync throttle ──
        # paintEvent 는 마우스 무브마다 호출되어 매우 빈번하다. 매번 JSON
        # 직렬화 + runJavaScript + JS 전체 redraw 를 하면 끊긴다. 그래서
        # 받은 state 는 _pending 에 저장만 하고, 타이머가 ~40ms(=최대 25fps)
        # 간격으로 *가장 마지막 state 만* 실제 전송한다.
        # interval 16ms(=60fps). 콘텐츠 변경 감지(JS applyPaintState)로 실제
        # scene 재생성은 *내용 변경 시만* 일어나므로, 대부분의 flush 는 가벼운
        # 카메라(viewport) 갱신만 수행 → 팬·줌이 부드럽다.
        self._pending_state = None
        self._last_content_key = None  # viewport 제외 콘텐츠 해시 (변경 감지)
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(16)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

    @property
    def canvas(self) -> AlignmentCanvasThree:
        return self._canvas_three

    def attach_to(self, vispy_canvas) -> None:
        """vispy AlignmentCanvas 의 _three_sync_callback 에 자기 sync 등록.

        vispy 측 paintEvent 끝마다 sync(state) 가 호출되어 three.js 가 같이 갱신됨.
        AlignmentCanvas 본체에 후크 한 줄만 추가되어 있어야 함 (M3-b 단계 작업).
        """
        if vispy_canvas is None:
            return
        # 후크 필드가 없으면 동적으로 추가 — AlignmentCanvas 본체가 아직 후크
        # 미반영 상태여도 안전. (M3-a 골격 단계 대비)
        if not hasattr(vispy_canvas, '_three_sync_callback'):
            try:
                vispy_canvas._three_sync_callback = None
            except Exception:
                pass
        vispy_canvas._three_sync_callback = self.sync
        self._attached_canvas = vispy_canvas

        # M4-b — three.js 2D 입력(클릭) → vispy AlignmentCanvas 의 world 처리.
        # 같은 canvas 메서드를 부르므로 단일 상태 유지 (vispy 와 모델 공유).
        self._canvas_three.set_input_callbacks(
            on_click=self._on_three_click,
            on_move=self._on_three_move,
            on_key=self._on_three_key,
        )

    def detach(self) -> None:
        """등록 해제 (탭 제거 등에서 사용)."""
        if (self._attached_canvas is not None
                and hasattr(self._attached_canvas, '_three_sync_callback')):
            self._attached_canvas._three_sync_callback = None
        self._attached_canvas = None

    # ── M4-b — three.js 입력 → vispy canvas 위임 ──
    def _on_three_click(self, wx: float, wy: float, button: int,
                        ctrl: bool = False) -> None:
        """three.js 2D 클릭(world 좌표) → vispy AlignmentCanvas 의 기존 선택 로직.
        ctrl=True 면 다중선택 토글(Ctrl+클릭)."""
        cv = self._attached_canvas
        if cv is None or not hasattr(cv, 'handle_world_click'):
            return
        try:
            cv.handle_world_click(float(wx), float(wy), int(button), bool(ctrl))
        except Exception as e:
            print(f'[AlignmentDockPanelThree._on_three_click] {e}')

    def _on_three_move(self, wx: float, wy: float) -> None:
        """three.js 2D 호버(world 좌표) → vispy AlignmentCanvas 의 호버 처리."""
        cv = self._attached_canvas
        if cv is None or not hasattr(cv, 'handle_world_move'):
            return
        try:
            cv.handle_world_move(float(wx), float(wy))
        except Exception as e:
            print(f'[AlignmentDockPanelThree._on_three_move] {e}')

    def _on_three_key(self, js_key: str, text: str, shift: bool = False) -> None:
        """three.js 2D 키 입력 → vispy AlignmentCanvas 의 기존 키 처리.
        shift=True 면 뒤집기(Shift+X/Y) 판별에 쓰인다."""
        cv = self._attached_canvas
        if cv is None or not hasattr(cv, 'handle_key'):
            return
        try:
            cv.handle_key(str(js_key), str(text), bool(shift))
        except Exception as e:
            print(f'[AlignmentDockPanelThree._on_three_key] {e}')

    def sync(self, state: dict) -> None:
        """vispy AlignmentCanvas 의 paintEvent 가 호출하는 콜백.

        state = AlignmentCanvas._collect_paint_state() 결과.
        실제 전송은 throttle — 여기선 *가장 마지막 state 저장만*.
        """
        self._pending_state = state

    def _flush(self) -> None:
        """타이머 — pending state 가 있으면 그것만 three.js 에 전송.

        [성능 핵심] viewport 를 제외한 콘텐츠가 직전과 동일하면 (팬·줌 등),
        대용량 전체 state 전송·redraw 대신 *작은 viewport 메시지만* 보낸다.
        이게 vispy(in-process 직접 그리기) 와 three.js(JSON 전송) 의 비용 격차를
        메우는 핵심 — 부재 다수 시 끊김 제거.
        """
        if self._pending_state is None:
            return
        st = self._pending_state
        self._pending_state = None
        try:
            # 콘텐츠(viewport 제외) 해시 — 직전과 같으면 카메라만 갱신
            content = {k: v for k, v in st.items() if k != 'viewport'}
            try:
                ckey = json.dumps(content, ensure_ascii=False,
                                  sort_keys=True, default=str)
            except Exception:
                ckey = None
            if ckey is not None and ckey == self._last_content_key:
                vp = st.get('viewport')
                if vp is not None:
                    self._canvas_three.update_viewport_only(vp)
                return
            self._last_content_key = ckey
            self._canvas_three.apply_paint_state(st)
        except Exception as e:
            from modular_3d._utils.debug import log_error
            log_error(f'AlignmentDockPanelThree._flush 실패: {e}', cat='alignment', exc=True)


__all__ = ['AlignmentDockPanelThree']
