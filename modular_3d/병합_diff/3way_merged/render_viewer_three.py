"""ViewerThree — QWebEngineView + Three.js 기반 3D 뷰어 (M2 골격).

[설계 근거]
`Viewer3D` 와 *완전 동일한 인터페이스* 로 메서드를 미러링하여
Controller·main_3d 의 기존 호출 코드를 *전혀 손대지 않고* 새 뷰어를
병행 마운트할 수 있게 한다. 자세한 결정은 `UI_마이그레이션/05_M2_설계.md`.

[좌표·단위 정책]
- vispy Viewer3D 와 동일: x=길이, y=폭, z=높이, 단위 mm
- three.js 카메라 up = (0, 0, 1)
- 운송탭의 y↔z 교환·mm→m 환산은 *적용 안 함*. Strangler 비교 효율 ↑.

[구현 단계]
- M2-a (현재): 인터페이스 골격, JS 호출은 빈 함수
- M2-b: add_component_visual / remove_component_visual / add_room_visual /
        remove_room_visual 실 동작
- M2-c: 고스트·스냅 마커·선택 박스
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5.QtCore import QUrl, QObject, pyqtSlot
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel


_TEMPLATE_PATH = Path(__file__).resolve().parent / 'viewer_three_template.html'


class _ViewerThreeBridge(QObject):
    """JS → Python 메시지 브리지 (M4 단계에서 클릭 픽킹 등에 사용).

    M2 단계에선 사용 안 함. registerObject 만 해두고 슬롯은 비워둔다.
    """

    @pyqtSlot(str)
    def js_log(self, msg: str) -> None:
        """JS 측 디버그 로그를 Python 콘솔로 전달."""
        print(f'[ViewerThree JS] {msg}')


def _ndarray_to_list(arr: Optional[np.ndarray]) -> Optional[list]:
    """numpy → JSON 직렬화 가능 list. None 은 그대로 전달."""
    if arr is None:
        return None
    if isinstance(arr, np.ndarray):
        return arr.tolist()
    return list(arr)


def _to_json(payload: dict) -> str:
    """dict → JS 안전 JSON 문자열 (백슬래시 X)."""
    return json.dumps(payload, ensure_ascii=False)


class ViewerThree:
    """three.js 기반 3D 뷰어 — Viewer3D 와 동일 인터페이스.

    내부적으로 QWebEngineView 에 HTML 페이지를 로드하고,
    각 메서드는 `self._view.page().runJavaScript("...")` 로 JS 측 함수를 호출.
    """

    def __init__(self):
        self._view = QWebEngineView()
        # 페이지 로드 완료 전 호출된 JS 명령은 _pending_calls 에 큐잉 → 로드 후 일괄 실행
        self._loaded = False
        self._pending_calls: list[str] = []

        # WebChannel 브리지 — JS → Python (M4 이후 사용)
        self._channel = QWebChannel()
        self._bridge = _ViewerThreeBridge()
        self._channel.registerObject('bridge', self._bridge)
        self._view.page().setWebChannel(self._channel)

        # 페이지 로드 완료 시그널 — pending JS 호출 일괄 실행
        self._view.loadFinished.connect(self._on_load_finished)

        # HTML 템플릿 로드 (운송탭 패턴 동일 — 인라인 임베드)
        if _TEMPLATE_PATH.exists():
            html = _TEMPLATE_PATH.read_text(encoding='utf-8')
        else:
            html = '<html><body>ViewerThree template missing</body></html>'
        # three.js 라이브러리 CDN → 로컬 인라인(오프라인 지원). vendor 없으면 CDN 폴백.
        from modular_3d.render.three_assets import inline_three_libs
        html = inline_three_libs(html)
        # baseUrl 은 qrc:///qtwebchannel/qwebchannel.js 가 동작하도록 임의 file URL.
        self._view.setHtml(html, QUrl('about:blank'))

    # ── Qt 위젯 접근 ────────────────────────────────────────
    def get_native_widget(self):
        """main_3d 가 좌 3D 영역에 마운트할 때 사용."""
        return self._view

    # ── 내부 헬퍼 ───────────────────────────────────────────
    def _on_load_finished(self, ok: bool) -> None:
        self._loaded = bool(ok)
        if not ok:
            from modular_3d._utils.debug import log_error
            log_error('ViewerThree HTML 로드 실패', cat='viewer_three')
            return
        # pending JS 일괄 실행
        for js in self._pending_calls:
            self._view.page().runJavaScript(js)
        self._pending_calls.clear()

    def _run_js(self, js: str) -> None:
        """JS 명령 실행. 페이지 로드 전이면 큐에 적재."""
        if self._loaded:
            self._view.page().runJavaScript(js)
        else:
            self._pending_calls.append(js)

    # ────────────────────────────────────────────────────────
    # M2-b 대상 — 부재·실 메쉬 (현재는 골격, 본문 비어 있음)
    # ────────────────────────────────────────────────────────
    def add_component_visual(self, comp_id: int, vertices: np.ndarray,
                             faces: np.ndarray, face_colors: np.ndarray) -> None:
        """부재 메쉬 추가. Viewer3D.add_component_visual 와 동일 시그너처."""
        payload = _to_json({
            'comp_id': int(comp_id),
            'vertices': _ndarray_to_list(vertices),
            'faces': _ndarray_to_list(faces),
            'face_colors': _ndarray_to_list(face_colors),
        })
        self._run_js(f'window.addComponent({payload});')

    def remove_component_visual(self, comp_id: int) -> None:
        self._run_js(f'window.removeComponent({int(comp_id)});')

    def set_dimension_lines(self, dx=None, dy=None, dz=None) -> None:
        """[7C-1] 캐드식 치수선(폭/깊이/높이, mm) 표시. 인자 없으면 제거."""
        if dx is None or dy is None or dz is None:
            self._run_js('window.setDimensionLines(null);')
            return
        payload = _to_json({'dx': float(dx), 'dy': float(dy), 'dz': float(dz)})
        self._run_js(f'window.setDimensionLines({payload});')

    def set_component_members(self, members) -> None:
        """[7C-2] 색 클래스별 부재 메시(단면명 포함) 표시 — hover 시 단면명 툴팁.

        members: list of {'vertices','faces','face_colors'(ndarray), 'name'(str)}.
        빈 리스트면 제거.
        """
        ms = []
        for m in (members or []):
            ms.append({
                'vertices': _ndarray_to_list(m.get('vertices')),
                'faces': _ndarray_to_list(m.get('faces')),
                'face_colors': _ndarray_to_list(m.get('face_colors')),
                'name': str(m.get('name', '')),
            })
        payload = _to_json({'members': ms})
        self._run_js(f'window.setComponentMembers({payload});')

    def add_room_visual(self, room_id: int, vertices: np.ndarray,
                        faces: np.ndarray, face_colors: np.ndarray) -> None:
        payload = _to_json({
            'room_id': int(room_id),
            'vertices': _ndarray_to_list(vertices),
            'faces': _ndarray_to_list(faces),
            'face_colors': _ndarray_to_list(face_colors),
        })
        self._run_js(f'window.addRoom({payload});')

    def remove_room_visual(self, room_id: int) -> None:
        self._run_js(f'window.removeRoom({int(room_id)});')

    # ────────────────────────────────────────────────────────
    # M2-c 대상 — 고스트·스냅·선택 (현재는 골격)
    # ────────────────────────────────────────────────────────
    def update_ghost(self, vertices: np.ndarray, faces: np.ndarray,
                     face_colors: np.ndarray) -> None:
        payload = _to_json({
            'vertices': _ndarray_to_list(vertices),
            'faces': _ndarray_to_list(faces),
            'face_colors': _ndarray_to_list(face_colors),
        })
        self._run_js(f'window.updateGhost({payload});')

    def clear_ghost(self) -> None:
        self._run_js('window.clearGhost();')

    def set_ghost_enabled(self, enabled: bool) -> None:
        flag = 'true' if enabled else 'false'
        self._run_js(f'window.setGhostEnabled({flag});')

    def update_opening_ghost(self, vertices: np.ndarray, faces: np.ndarray,
                             face_colors: np.ndarray) -> None:
        payload = _to_json({
            'vertices': _ndarray_to_list(vertices),
            'faces': _ndarray_to_list(faces),
            'face_colors': _ndarray_to_list(face_colors),
        })
        self._run_js(f'window.updateOpeningGhost({payload});')

    def clear_opening_ghost(self) -> None:
        self._run_js('window.clearOpeningGhost();')

    def update_room_ghost(self, vertices: np.ndarray, faces: np.ndarray,
                          face_colors: np.ndarray) -> None:
        payload = _to_json({
            'vertices': _ndarray_to_list(vertices),
            'faces': _ndarray_to_list(faces),
            'face_colors': _ndarray_to_list(face_colors),
        })
        self._run_js(f'window.updateRoomGhost({payload});')

    def clear_room_ghost(self) -> None:
        self._run_js('window.clearRoomGhost();')

    def show_snap_markers(self, snap_point: np.ndarray,
                          all_vertices: np.ndarray) -> None:
        payload = _to_json({
            'snap_point': _ndarray_to_list(snap_point),
            'all_vertices': _ndarray_to_list(all_vertices),
        })
        self._run_js(f'window.showSnapMarkers({payload});')

    def hide_snap_marker(self) -> None:
        self._run_js('window.hideSnapMarker();')

    def show_selection_box(self, comp_id: int, bbox_min: np.ndarray,
                           bbox_max: np.ndarray) -> None:
        payload = _to_json({
            'comp_id': int(comp_id),
            'bbox_min': _ndarray_to_list(bbox_min),
            'bbox_max': _ndarray_to_list(bbox_max),
        })
        self._run_js(f'window.showSelectionBox({payload});')

    def hide_selection_box(self) -> None:
        self._run_js('window.hideSelectionBox();')

    # ────────────────────────────────────────────────────────
    # 해석·운송·F6 ops 뷰 등 — 디자인·정의 탭에선 안 쓰지만,
    # ViewerStrangler 가 broadcast 할 때 AttributeError 안 나게 no-op 정의
    # ────────────────────────────────────────────────────────
    def highlight_member(self, model, mid: int) -> None:
        pass

    def clear_member_highlight(self) -> None:
        pass

    def set_dof_color_mode(self, on: bool) -> None:
        pass

    def set_section_z(self, z: float, enabled: bool) -> None:
        """[B3] z축 단면 절단 — enabled 면 z(높이) 위를 잘라 평면을 본다."""
        self._run_js(
            f'window.setSectionZ({float(z)}, '
            f'{"true" if enabled else "false"});')

    def set_section_y(self, y: float, enabled: bool) -> None:
        """[2026-06-01] y축 단면 절단 — enabled 면 y 값 한쪽을 잘라 평면을 본다."""
        self._run_js(
            f'window.setSectionY({float(y)}, '
            f'{"true" if enabled else "false"});')

    def capture_section_front(self, cut_y: float) -> str:
        """[2026-06-02] 카메라를 외벽 정면 시점으로 이동 + Y축 클리핑 적용 후
        뷰를 PNG dataURL 로 캡처. 카메라·클리핑은 자동 원복. 비동기 JS 호출을
        QEventLoop 로 동기처럼 처리.

        반환: 'data:image/png;base64,...' 또는 '' (실패 시).
        """
        from PyQt5.QtCore import QEventLoop, QTimer
        result = {"data": "", "done": False}
        loop = QEventLoop()

        def _cb(data_url):
            result["data"] = data_url or ""
            result["done"] = True
            loop.quit()
        try:
            self._view.page().runJavaScript(
                f"window.captureSectionFront({float(cut_y)});", _cb)
        except Exception:
            return ""
        # 안전 타임아웃 — 2초 안에 콜백 안 오면 포기
        QTimer.singleShot(2000, loop.quit)
        loop.exec_()
        return result["data"]

    def is_ops_view_active(self) -> bool:
        return False

    def is_deformed_shape_active(self) -> bool:
        return False

    def is_slab_overlay_visible(self) -> bool:
        return False

    def is_beam_udl_overlay_visible(self) -> bool:
        return False

    def is_column_head_overlay_visible(self) -> bool:
        return False

    def is_reaction_overlay_visible(self) -> bool:
        return False

    def is_joint_overlay_visible(self) -> bool:
        return False

    def screen_to_world_ray(self, qt_pos):
        # M4 단계에서 JS 측 raycaster 결과로 구현. M2 에선 사용 안 함.
        return None, None

    def get_scene_transform(self):
        return None


__all__ = ['ViewerThree']
