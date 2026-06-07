"""ViewerStrangler — Viewer3D + ViewerThree 를 *동시 갱신* 하는 어댑터.

[설계 근거]
Controller 가 `self._viewer.add_component_visual(...)` 형태로 Viewer3D 인스턴스에
직접 호출하는 구조라서, *Controller 한 줄도 안 건드리고* 새 three.js 뷰어를 같이
갱신하려면 같은 시그너처의 어댑터가 필요.

[원칙]
- 모든 *변경* 메서드는 두 뷰어 모두 호출 (broadcast).
- *조회* 메서드 (is_*_active, get_scene_transform 등) 는 *Viewer3D 만* 응답.
  이유: 기존 코드가 Viewer3D 의 카메라·상태에 의존하는 흐름을 살려두기 위함.
- Viewer3D 에만 있는 메서드 (해석·운송·F6 ops 뷰 등) 는 그대로 vispy 만 호출.
  ViewerThree 는 해당 메서드를 *no-op* 으로 두므로 broadcast 안전.

[Strangler 단계]
- M2~M7: 두 뷰어 모두 살아 있음. 사용자가 양쪽 비교.
- M8: 검증 통과 후 Viewer3D 제거 → ViewerStrangler 도 제거하고 ViewerThree 만 남김.
"""
from __future__ import annotations

from typing import Any

from modular_3d.render.viewer import Viewer3D
from modular_3d.render.viewer_three import ViewerThree


# Viewer3D 에만 있고 ViewerThree 에 없는 메서드 — vispy 만 호출.
_VISPY_ONLY_METHODS = frozenset({
    # 해석·F6 ops 뷰
    'show_slab_overlay', 'hide_slab_overlay',
    'show_beam_udl_overlay', 'hide_beam_udl_overlay',
    'show_column_head_overlay', 'hide_column_head_overlay',
    'show_reaction_overlay', 'hide_reaction_overlay',
    'show_joint_overlay', 'hide_joint_overlay',
    'show_ops_view', 'hide_ops_view',
    'update_deformed_shape', 'hide_deformed_shape',
    'show_member_ratio_bands', 'show_member_contour', 'hide_member_contour',
    'show_unstable_warning', 'hide_unstable_warning',
    'highlight_ops_member', 'highlight_ops_joint', 'highlight_ops_node',
    'highlight_ops_member_hover', 'clear_ops_member_hover',
    'clear_ops_joint_highlight',
    'update_pinned_members',
    'highlight_ops_joint_chain',
    'pick_ops_member_at', 'pick_ops_joint_at',
    'set_show_diaphragms', 'set_show_rigid_links',
    'set_rule_visibility',
    # 실 색면 표시 토글 (2026-05-28) — 타 탭(접합/해석/물량)에선 vispy 만 표시되며
    # 거기서 실을 숨긴다. three.js 는 배치/정의 탭 전용이라 토글 불필요.
    'set_rooms_visible',
})

# 두 뷰어 모두 정의된 *broadcast* 메서드 — M2 단계의 핵심 시각 갱신
_BROADCAST_METHODS = frozenset({
    'add_component_visual', 'remove_component_visual',
    'add_room_visual', 'remove_room_visual',
    'update_ghost', 'clear_ghost', 'set_ghost_enabled',
    'update_opening_ghost', 'clear_opening_ghost',
    'update_room_ghost', 'clear_room_ghost',
    'show_snap_markers', 'hide_snap_marker',
    'show_selection_box', 'hide_selection_box',
    'highlight_member', 'clear_member_highlight',
    'set_dof_color_mode',
    # [3D 복제 최적화 2단계] 묶음 전송 — three 만 모았다가 1회 전송, vispy 는 no-op.
    'begin_batch', 'end_batch',
})

# Viewer3D 에서만 응답하는 *조회* 메서드 — 카메라·픽킹·상태 등
_VISPY_QUERY_METHODS = frozenset({
    'is_ops_view_active', 'is_deformed_shape_active',
    'is_slab_overlay_visible', 'is_beam_udl_overlay_visible',
    'is_column_head_overlay_visible', 'is_reaction_overlay_visible',
    'is_joint_overlay_visible',
    'screen_to_world_ray', 'get_scene_transform',
})


class ViewerStrangler:
    """Viewer3D + ViewerThree 어댑터.

    Controller / main_3d 가 보던 인터페이스를 그대로 노출하면서, *변경* 메서드는
    두 뷰어 모두 호출하고, *조회* 메서드는 Viewer3D 결과만 반환.
    """

    def __init__(self):
        self.vispy = Viewer3D()
        self.three = ViewerThree()

    # ── Qt 위젯 접근 (main_3d 의 좌 3D 영역 마운트용) ──
    def get_native_widget(self):
        """*기존 호출 호환* — Viewer3D 의 위젯 반환. 운영 코드 깨짐 방지.

        Strangler 마운트는 main_3d 가 별도로 self.vispy / self.three 의
        get_native_widget() 을 각각 가져가 세로 QSplitter 에 붙임.
        """
        return self.vispy.get_native_widget()

    # ── 광역 dispatch ────────────────────────────────────────
    def __getattr__(self, name: str) -> Any:
        """정의되지 않은 속성 접근 시 dispatch 규칙:

        - broadcast 메서드 → 두 뷰어 모두 호출 (반환값은 vispy 것)
        - vispy 전용 메서드 → vispy 만 호출
        - vispy 조회 메서드 → vispy 만 호출
        - 그 외 → vispy 의 속성 그대로 (캔버스·카메라 등)
        """
        # broadcast
        if name in _BROADCAST_METHODS:
            vispy_attr = getattr(self.vispy, name)
            three_attr = getattr(self.three, name, None)
            def _broadcast(*args, **kwargs):
                # three 먼저 호출 (vispy 실패 시에도 three 는 반영되게)
                if three_attr is not None:
                    try:
                        three_attr(*args, **kwargs)
                    except Exception as e:
                        from modular_3d._utils.debug import log_error
                        log_error(f'three.{name} 실패: {e}', cat='viewer_strangler', exc=True)
                return vispy_attr(*args, **kwargs)
            return _broadcast

        # vispy 전용 또는 vispy 조회 → vispy 위임
        if name in _VISPY_ONLY_METHODS or name in _VISPY_QUERY_METHODS:
            return getattr(self.vispy, name)

        # 그 외 (canvas, view, _component_visuals 등 속성 접근) — vispy 그대로
        return getattr(self.vispy, name)


__all__ = ['ViewerStrangler']
