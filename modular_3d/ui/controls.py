"""
FSM 컨트롤러 — 상태 전환, 이벤트 처리, 레이캐스팅.
Qt 이벤트를 직접 수신 (vispy 이벤트 시스템 미사용).
"""
import numpy as np
from PyQt5.QtCore import Qt
from modular_3d.model import (
    AppState, ComponentType, Component, Module,
    FloorPanel, StructWall,
    CantileverBeam, CantileverSlab,
    MidBeam, MidColumn, Vertical3Module, Scene,
)
from typing import Optional, Tuple, Set, Dict
from modular_3d.render.mesh_builder import build_component_mesh, build_ghost_component_mesh
from modular_3d.render.viewer import Viewer3D, _cam_axes
from modular_3d.ui.ui_panel import DimensionInputPanel, StatusBarManager
from modular_3d.render.snap import SnapManager
from modular_3d.analysis.constants import FLOOR_HEIGHT, SECTION_W_MM, MODULE_HEIGHT_MM
from modular_3d.ui.controls_f5 import F5Mixin
from modular_3d.ui.controls_f6 import F6Mixin

# 키 → 부재 타입 매핑
KEY_TO_TYPE = {
    '1': ComponentType.MODULE,
    '2': ComponentType.FLOOR_PANEL,
    '3': ComponentType.STRUCT_WALL,
    '4': ComponentType.CANTILEVER_BEAM,
    '5': ComponentType.CANTILEVER_SLAB,
    '6': ComponentType.MID_BEAM,
    '7': ComponentType.MID_COLUMN,
    '8': ComponentType.VERTICAL_MODULE,   # 수직 3층 모듈
    '9': ComponentType.CORE,              # RC 코어벽 (한 장 단위, 자동 슬래브 동반)
}

# 부재 타입별 기본 치수
# [함정] MODULE/STRUCT_WALL/MID_COLUMN 의 height = 단층 모듈 한 층 높이(3400).
# FLOOR_HEIGHT(3420) 는 층-층 거리(=h+gap)이므로 height 와 다름. 혼동 주의.
from modular_3d.카탈로그.geometry import (
    CORE_WALL_DEFAULT_THICKNESS_MM as _CWT,
    CORE_SLAB_DEFAULT_THICKNESS_MM as _CST,
)
DEFAULT_DIMS = {
    # [2026-05-11 변경] 모듈 기본 3400×6820×3400, 바닥패널 3400×5000 (사용자 요청)
    ComponentType.MODULE:           {'width': 3400, 'depth': 6820, 'height': 3400},
    ComponentType.FLOOR_PANEL:      {'width': 3400, 'depth': 5000, 'height': 200},
    ComponentType.STRUCT_WALL:      {'width': 3400, 'depth': 200, 'height': 3400},
    # 내벽 — width=벽 길이, depth=두께(100 고정), height=3400(자동).
    ComponentType.INTERIOR_WALL:    {'width': 3000, 'depth': 100, 'height': 3400},
    ComponentType.CANTILEVER_BEAM:  {'width': 1500, 'depth': 200, 'height': 200},
    ComponentType.CANTILEVER_SLAB:  {'width': 1500, 'depth': 3400, 'height': 200},
    ComponentType.MID_BEAM:         {'width': 3400, 'depth': 200, 'height': 200},
    ComponentType.MID_COLUMN:       {'width': 200, 'depth': 200, 'height': 3400},
    # 수직 3층 모듈 — 가로/세로 3400 고정, 높이 = 3·3400 + 2·20 = 10240.
    ComponentType.VERTICAL_MODULE:  {'width': 3400, 'depth': 3400, 'height': 10240},
    # RC 코어 — width=벽 길이 L, depth=벽 두께 t, height=단층 높이.
    # slab_thickness 는 동반 생성되는 코어 슬래브의 두께(기본 180mm) — multi_floor 에서 추출.
    # 범위: t 200~500, L 300~20000 (설계서 §3.1).
    ComponentType.CORE:             {'width': 3400, 'depth': _CWT, 'height': 3400,
                                     'slab_thickness': _CST},
}

# 부재 타입별 한글 이름 — model.core.TYPE_NAMES 단일 진실 원천 (단계 2 통합)
from modular_3d.model import TYPE_NAMES  # noqa: E402


# 단계 5 (2026-05-08): 순수 기하 헬퍼 4개 (_ray_aabb_intersect, _create_component,
# _raycast_z0, _calc_mid_*_placement) 를 controls_geom.py 로 이관.
# 본 모듈에서는 import 후 _ prefix alias 로 재export — 옛 호출처 호환 유지.
from modular_3d.ui.controls_geom import (
    ray_aabb_intersect as _ray_aabb_intersect,
    create_component as _create_component,
    raycast_z0 as _raycast_z0,
    calc_mid_beam_placement as _calc_mid_beam_placement_pure,
    calc_mid_column_placement as _calc_mid_column_placement_pure,
)
from modular_3d._utils.debug import dprint


class Controller(F5Mixin, F6Mixin):
    """앱 상태 관리 + Qt 이벤트 → 모델/뷰 연결."""

    def __init__(self, viewer: Viewer3D, dim_panel: DimensionInputPanel,
                 status_mgr: StatusBarManager, scene: Scene,
                 analysis_panel=None, analysis_dock=None):
        self._viewer = viewer
        self._dim_panel = dim_panel
        self._status = status_mgr
        self._scene = scene
        self._snap = SnapManager()
        self._analysis_panel = analysis_panel
        self._analysis_dock = analysis_dock
        # F5 도킹 패널 — main_3d.set_f5_dock 으로 주입됨
        self._f5_dock = None
        self._f5_panel = None
        self._f5_move_group_id = 0  # 자유 이동 모드 마커 (M 키)

        # 패널에서 부재 클릭 → 3D 하이라이트
        if analysis_panel is not None:
            analysis_panel.member_selected.connect(self._on_panel_member_selected)
            # 그룹 노드(모듈/층/역할) 클릭 시 다중 부재 강조 (2026-05-18)
            if hasattr(analysis_panel, 'members_selected'):
                analysis_panel.members_selected.connect(
                    self._on_panel_members_selected)
            # 케이스 콤보박스 변경 시 좌측 변형 형상 갱신 (Phase 4-B)
            if hasattr(analysis_panel, 'case_changed'):
                analysis_panel.case_changed.connect(self._on_panel_case_changed)
            # 컨투어 종류 변경 (Phase 4-C)
            if hasattr(analysis_panel, 'contour_changed'):
                analysis_panel.contour_changed.connect(self._on_panel_contour_changed)
            # 물량산출 탭 응력비 시각화 (2026-05-08)
            if hasattr(analysis_panel, 'ratio_view_changed'):
                analysis_panel.ratio_view_changed.connect(self._on_ratio_view_changed)
            # (Phase 8/9 명세통합) 시각화 토글들 — 직접 viewer 메소드 호출.
            # [2026-05-13 접합부조정탭] 다이어프램 토글은 JointEditPanel 로
            # 이동 — main_3d 에서 직접 wiring. 본 자리는 자유도 색만.
            if hasattr(analysis_panel, 'dof_color_toggle'):
                analysis_panel.dof_color_toggle.connect(
                    self._viewer.set_dof_color_mode)
            # (2026-05-19) 기둥 층구간 분할 — 적용 버튼 → 물량 재산정.
            if hasattr(analysis_panel, 'column_segments_changed'):
                analysis_panel.column_segments_changed.connect(
                    self._on_column_segments_changed)
        self._current_contour_kind = '끄기'
        self._current_ratio_view = 'off'    # 물량산출 색상 모드
        self._quantity_reports = {}          # 정책 → QuantityReport (Phase 6 에서 주입)

        self._state = AppState.IDLE
        self._preview_position = np.zeros(3)
        self._current_comp_type = ComponentType.MODULE
        self._preview_rotation = 0
        self._preview_anchor = 0
        self._snapped_comp_id = None  # 스냅된 대상 부재 ID
        self._last_mouse_pos = None   # 카메라 이동 후 재계산용
        self._selected_comp_id = None  # 선택된 부재 ID
        self._copy_dims = None         # 복사 시 원본 치수
        self._mid_auto_params = None    # 중간보/기둥 자동 계산 결과
        # F5 그룹/자식 관계 dict — 81개 callsite 가 직접 변경 (`self._floor_pairs[a]=b`)
        # 하므로 dict 그대로 둠. 캡슐화 시도(GroupState)는 caller 수정이 따라가지
        # 못해 효과 없었음 — 폐기 (단계 3 단순화 2026-05-08).
        self._floor_pairs: Dict[int, int] = {}              # comp_id ↔ paired_comp_id
        self._copy_ids: Set[int] = set()                    # 자동 복사본 id
        self._module_children: Dict[int, Set[int]] = {}     # module_id → children
        self._child_parent: Dict[int, int] = {}             # child_id → parent_id
        self._child_pairs: Dict[int, int] = {}              # child ↔ sibling child

        self._move_original_pos = None  # 이동 전 원래 위치
        self._move_original_rot = 0     # 이동 전 원래 회전
        self._move_original_anchor = 0  # 이동 전 원래 앵커
        self._move_rotation = 0         # 이동 중 회전값
        self._move_anchor = 0           # 이동 중 앵커값
        self._move_position = np.zeros(3)  # 이동 중 위치

        # 레거시 자체 솔버 결과 — Phase 5에서 폐기됨. 호환성 위해 None 유지.
        self._last_analysis = None
        # _cant_slab_z_mode: t 키와 함께 제거됨 (F5 단계 1c).
        # 캔틸레버 슬래브 z는 단계 4에서 종속 대상으로 결정될 예정.

        # 치수 패널 시그널 연결
        self._dim_panel.confirmed.connect(self._on_dimension_confirmed)
        self._dim_panel.cancelled.connect(self._on_dimension_cancelled)

        self._status.update_mode(self._state)
        self._status.update_count(self._scene.component_count)

    # ── 상태 전환 ────────────────────────────────────────────

    def _set_state(self, new_state: AppState):
        self._state = new_state
        self._status.update_mode(new_state)

    # ── 현재 기본 치수 ───────────────────────────────────────

    def _current_defaults(self) -> dict:
        return dict(DEFAULT_DIMS.get(self._current_comp_type,
                                     {'width': 3400, 'depth': 3400, 'height': 3400}))

    # ── 고스트 미리보기 갱신 ─────────────────────────────────

    def _update_preview(self, world_pos: np.ndarray):
        """고스트를 world_pos에 갱신 (타입별 z 시프트 포함)."""
        self._preview_position = world_pos.copy()
        self._preview_position[2] = self._preview_z_for_type(self._current_comp_type)

        comp = _create_component(
            self._current_comp_type, self._preview_position,
            self._current_defaults(), self._preview_rotation, self._preview_anchor,
        )
        verts, faces, colors = build_ghost_component_mesh(comp)
        self._viewer.update_ghost(verts, faces, colors)

    def _preview_z_for_type(self, ct: ComponentType) -> float:
        """부재 타입별 배치 z 레벨.

        [2026-05-11 v5 변경]
        - 바닥패널 z = 0 (1층 레벨) — 기존 FLOOR_HEIGHT(2층) 에서 변경.
        - 캔틸레버 슬래브 z = 0 (모듈 슬래브와 같은 레벨) — 기존 FLOOR_HEIGHT 에서 변경.
        """
        if ct == ComponentType.FLOOR_PANEL:
            return 0.0
        elif ct == ComponentType.CANTILEVER_SLAB:
            return 0.0
        elif ct == ComponentType.CANTILEVER_BEAM:
            # 모듈 천장보 레벨 = h - S (모듈 내부 천장보 z), H - S 가 아님!
            # H = h + gap 이라 (H-S) 는 갭 만큼 위로 어긋남.
            return float(MODULE_HEIGHT_MM - SECTION_W_MM)
        return 0.0

    # ── Qt 키보드 이벤트 ─────────────────────────────────────

    def on_qt_key_press(self, text: str, qt_key: int):
        # F5/F6 키는 main_3d.py 의 eventFilter 가 가로채 직접 처리하므로
        # 본 함수에는 도달하지 않음. 옛 분기는 제거됨.

        # F7 + 키 1/2/3/4 (레거시 시각화 모드) Phase 5 에서 폐기됨.
        # OpenSees 뷰는 F6 + 우측 패널 콤보박스로 모두 제어.
        if qt_key == Qt.Key_F7:
            dprint('ANALYSIS', '[ANALYSIS] F7 시각화 모드는 폐기됨 — F6 + 우측 패널 사용')
            return

        # Phase 14 (설계서_부재호버.md) — F6 ops 뷰 활성 시 ESC = 모든 정보창 닫기.
        # 디자인(편집) 탭의 기존 ESC 동작은 ops 뷰가 꺼져 있을 때 그대로 동작.
        if (qt_key == Qt.Key_Escape
                and self._viewer.is_ops_view_active()
                and hasattr(self, 'close_all_pinned')):
            self.close_all_pinned()
            return

        if self._state == AppState.IDLE:
            # 단계 6 (i1): 3D 뷰의 1~7 부재 타입 키는 폐기됨.
            # 배치는 F5 도킹 모드에서만 가능.
            if text.lower() == 'z':
                self._do_undo()

        elif self._state == AppState.SELECTED:
            if qt_key == Qt.Key_Delete:
                self._delete_selected()
            elif text.lower() == 'c':
                self._copy_selected()
            elif text.lower() == 'm':
                self._start_moving()
            elif qt_key == Qt.Key_Escape:
                self._deselect()
            elif text.lower() == 'z':
                self._deselect()
                self._do_undo()
            # 단계 6 (i1): SELECTED 상태에서도 1~7 부재 타입 키 폐기

        elif self._state == AppState.MOVING:
            if qt_key == Qt.Key_Escape:
                self._cancel_moving()
            elif text.lower() == 'x':
                self._start_axis_move('X')
            elif text.lower() == 'y':
                self._start_axis_move('Y')
            elif text.lower() == 'r':
                self._move_rotation = (self._move_rotation + 90) % 360
                self._update_moving_preview()
                print(f'[MOVE ROTATE] {self._move_rotation}°')
            elif text.lower() == 'v':
                self._move_anchor = (self._move_anchor + 1) % 4
                self._update_moving_preview()
                anchor_names = ['좌하', '우하', '우상', '좌상']
                print(f'[MOVE ANCHOR] {anchor_names[self._move_anchor]}')

        elif self._state == AppState.PLACEMENT_PREVIEW:
            # 단계 6 (i1): PREVIEW 중 1~7 부재 타입 변경 키도 폐기.
            # PREVIEW 자체가 더이상 진입되지 않으므로 이 분기는 사실상 비활성.
            if text.lower() == 'r':
                # 90° 회전
                self._preview_rotation = (self._preview_rotation + 90) % 360
                self._update_preview(self._preview_position)
                dprint('ROTATE', f'[ROTATE] {self._preview_rotation}°')

            elif text.lower() == 'v':
                # 앵커 변경
                self._preview_anchor = (self._preview_anchor + 1) % 4
                self._update_preview(self._preview_position)
                anchor_names = ['좌하', '우하', '우상', '좌상']
                dprint('ANCHOR', f'[ANCHOR] {anchor_names[self._preview_anchor]}')

            elif text.lower() == 'z':
                self._do_undo()

            elif qt_key == Qt.Key_Escape:
                self._cancel_placement()

    # ── Qt 마우스 이벤트 ─────────────────────────────────────

    def on_qt_mouse_move(self, pos: tuple):
        self._last_mouse_pos = pos
        self._update_from_mouse(pos)
        # F6 ops 뷰 활성 시 부재 호버 강조 (Phase 2, 설계서_부재호버.md)
        if hasattr(self, '_update_ops_hover'):
            self._update_ops_hover(pos)

    def refresh_after_camera(self):
        """카메라 이동/회전 후 고스트 위치 재계산."""
        if self._last_mouse_pos and self._state in (AppState.PLACEMENT_PREVIEW, AppState.MOVING):
            self._update_from_mouse(self._last_mouse_pos)

    def _snap_find(self, mouse_qt: tuple, exclude_comp_id=None):
        """스냅 탐색 헬퍼: vispy 씬 변환으로 find_nearest 호출.
        복사본(읽기전용) 컴포넌트는 스냅 대상에서 제외."""
        tr = self._viewer.get_scene_transform()
        return self._snap.find_nearest(
            mouse_qt, tr,
            exclude_comp_id=exclude_comp_id,
            exclude_ids=self._copy_ids,
        )

    def _update_from_mouse(self, pos: tuple):
        origin, direction = self._viewer.screen_to_world_ray(pos)
        hit = _raycast_z0(origin, direction)

        # 배치/이동 시 해당 부재의 z 평면에서 레이캐스트 — 마우스와 고스트 위치 일치
        #   - PLACEMENT_PREVIEW: 타입별 z (FP=0, CS=0, CB=3200, 나머지=0)
        #   - MOVING: 이동 중인 부재의 원래 z
        if self._state == AppState.PLACEMENT_PREVIEW:
            z_plane = self._preview_z_for_type(self._current_comp_type)
            if z_plane != 0.0:
                z_hit = _raycast_z0(origin, direction, z_plane)
                if z_hit is not None:
                    hit = z_hit
        elif self._state == AppState.MOVING and hasattr(self, '_move_original_pos'):
            z_plane = self._move_original_pos[2]
            if z_plane != 0.0:
                z_hit = _raycast_z0(origin, direction, z_plane)
                if z_hit is not None:
                    hit = z_hit

        if hit is not None:
            self._status.update_coords(hit[0], hit[1])

            if self._state == AppState.MOVING:
                snapped = np.round(hit / 100.0) * 100.0
                snapped[2] = self._move_original_pos[2]  # 원래 z 보존

                # 스냅 시도 (이동 중인 부재 자신은 제외)
                snap_result = self._snap_find(pos, exclude_comp_id=self._selected_comp_id)
                if snap_result is not None:
                    snap_pos, snap_comp_id, comp_all_verts = snap_result
                    snapped = snap_pos.copy()
                    snapped[2] = self._move_original_pos[2]  # 원래 z 보존

                    # 20mm 갭 적용
                    comp = self._scene.components.get(self._selected_comp_id)
                    snap_comp = self._scene.components.get(snap_comp_id)
                    if comp is not None and snap_comp is not None:
                        snapped = SnapManager.apply_gap(
                            snapped, comp.dimensions,
                            self._move_rotation, self._move_anchor,
                            snap_comp, gap=20.0,
                        )

                    self._viewer.show_snap_markers(snap_pos, comp_all_verts)
                else:
                    self._viewer.hide_snap_marker()

                self._move_position = snapped.copy()
                self._update_moving_preview()
                return

            if self._state == AppState.PLACEMENT_PREVIEW:
                # 100mm 그리드 스냅
                snapped = np.round(hit / 100.0) * 100.0
                snapped[2] = self._preview_z_for_type(self._current_comp_type)

                # 꼭지점 스냅 시도
                snap_result = self._snap_find(pos)

                if snap_result is not None:
                    snap_pos, snap_comp_id, comp_all_verts = snap_result
                    self._snapped_comp_id = snap_comp_id
                    snap_comp = self._scene.components.get(snap_comp_id)

                    # ── 중간보/기둥: 자동 배치 계산 ──
                    if self._current_comp_type in (ComponentType.MID_BEAM, ComponentType.MID_COLUMN):
                        if snap_comp is not None:
                            if self._current_comp_type == ComponentType.MID_BEAM:
                                auto = self._calc_mid_beam_placement(snap_pos, snap_comp)
                            else:
                                auto = self._calc_mid_column_placement(snap_pos, snap_comp)

                            if auto is not None:
                                pos, rot, dims = auto
                                self._mid_auto_params = {
                                    'position': pos, 'rotation': rot, 'dims': dims,
                                }
                                ghost = _create_component(
                                    self._current_comp_type, pos, dims, rot, 0)
                                verts, faces, colors = build_ghost_component_mesh(ghost)
                                self._viewer.update_ghost(verts, faces, colors)
                                self._viewer.show_snap_markers(snap_pos, comp_all_verts)
                                return
                        # 계산 실패 → 자동 파라미터 해제
                        self._mid_auto_params = None
                        self._viewer.show_snap_markers(snap_pos, comp_all_verts)

                    else:
                        # ── 일반 부재: 20mm 갭 적용 ──
                        snapped = snap_pos.copy()
                        snapped[2] = self._preview_z_for_type(self._current_comp_type)
                        if snap_comp is not None:
                            snapped = SnapManager.apply_gap(
                                snapped, self._current_defaults(),
                                self._preview_rotation, self._preview_anchor,
                                snap_comp, gap=20.0,
                            )
                        self._viewer.show_snap_markers(snap_pos, comp_all_verts)
                else:
                    self._snapped_comp_id = None
                    self._mid_auto_params = None
                    self._viewer.hide_snap_marker()

                self._update_preview(snapped)

    def on_qt_mouse_press(self, button, pos: tuple):
        if button != Qt.LeftButton:
            return

        # Phase 9 — F6 ops 뷰 활성 시 부재 클릭 → 정보창 고정.
        # ops 뷰에서는 컴포넌트 빨간 박스가 의미 없으므로 픽킹/선택 흐름을
        # 단락하고 호버 시스템 픽킹 결과로 슬롯 할당.
        if self._viewer.is_ops_view_active():
            last_ops = getattr(self, '_last_ops_analysis', None)
            if last_ops is not None:
                om = last_ops[0]; am = last_ops[1]
                mid = self._viewer.pick_ops_member_at(pos, om, am)
                if mid is not None and hasattr(self, 'pin_member'):
                    self.pin_member(mid, pos)
            return

        if self._state == AppState.IDLE or self._state == AppState.SELECTED:
            # Ray-AABB 피킹
            picked = self._pick_component(pos)
            if picked is not None:
                self._select_component(picked)
            elif self._state == AppState.SELECTED:
                self._deselect()

        elif self._state == AppState.MOVING:
            self._confirm_moving()

        elif self._state == AppState.PLACEMENT_PREVIEW:
            self._set_state(AppState.DIMENSION_INPUT)
            # 중간보/기둥 자동 계산 치수 우선
            if self._mid_auto_params is not None:
                dims = dict(self._mid_auto_params['dims'])
                self._preview_position = self._mid_auto_params['position'].copy()
                self._preview_rotation = self._mid_auto_params['rotation']
            elif self._copy_dims:
                dims = self._copy_dims
            else:
                dims = self._current_defaults()
            self._dim_panel.activate(
                self._current_comp_type, dims,
            )

    # ── 치수 패널 콜백 ───────────────────────────────────────

    def _on_dimension_confirmed(self, dims: dict):
        # 축 이동 모드인 경우
        if self._dim_panel._move_mode:
            self._confirm_axis_move()
            return

        # F5 모드 분기 (단계 3b): 사이즈 확정 → PLACEMENT_PREVIEW 진입
        # (3D 흐름과 순서가 반대 — F5는 dim → preview → click → create)
        if getattr(self, '_f5_pending_placement', False):
            # 수직 3층 모듈: 사용자가 W/D/H 를 임의로 바꿨더라도 강제로
            # (3400, 3400, 10240) 로 덮어쓴다 — 치수 고정 사양.
            if self._current_comp_type == ComponentType.VERTICAL_MODULE:
                dims = {'width': 3400.0, 'depth': 3400.0, 'height': 10240.0}
            # 내벽: height 는 '자동'(0) 으로 들어오므로 부모 검출 높이로 채운다.
            if self._current_comp_type == ComponentType.INTERIOR_WALL:
                meta = getattr(self, '_f5_dep_meta', None)
                wall_h = (meta or {}).get('wall_height') if meta else None
                if not wall_h:
                    from modular_3d.카탈로그.geometry import MODULE_HEIGHT_MM
                    wall_h = float(MODULE_HEIGHT_MM)
                dims = dict(dims)
                dims['height'] = float(wall_h)
            # 보 단면 타입 — 콤보 보이는 부재만 dims 에 들어옴(없으면 각형강관).
            dims = dict(dims)
            self._f5_pending_beam_section = dims.pop('section_type', 'shs')
            self._f5_pending_dims = dict(dims)
            self._f5_pending_placement = False
            self._dim_panel.deactivate()

            # 구조벽 + 바닥패널 병합 체크박스 ON: DEPENDENCY_PICK 진입 →
            # 사용자가 합체할 FP 를 명시적으로 클릭하게 한다.
            ct = self._current_comp_type
            wants_merge = bool(dims.get('merge', False))
            if ct == ComponentType.STRUCT_WALL and wants_merge:
                self._f5_dep_meta = None  # FP 픽 후 채움
                if self._f5_panel and self._f5_panel.canvas:
                    self._f5_panel.canvas.enter_dependency_pick(ct)
                    self._f5_panel.canvas.setFocus()
                self._dim_panel.set_mode_text(
                    '[F5 구조벽 합체: 노란 강조된 바닥패널을 클릭]')
                dprint('F5', '[F5] 구조벽 dims 확정 → DEPENDENCY_PICK (합체할 FP 클릭)')
                return

            # 캔버스를 preview 상태로 (마우스 클릭 대기)
            # [버그 패치] R/V 키가 작동하려면 캔버스에 포커스가 있어야 함.
            # dim_panel.deactivate() 후 LineEdit이 포커스를 잃지만 자동으로
            # 캔버스로 돌아가지 않으므로 명시적 setFocus 호출.
            if self._f5_panel and self._f5_panel.canvas:
                self._f5_panel.canvas._f5_in_preview = True
                self._f5_panel.canvas.setFocus()
                self._f5_panel.canvas.update()
            # 좌측 3D 고스트 게이트 ON
            self._viewer.set_ghost_enabled(True)
            name = TYPE_NAMES.get(self._current_comp_type, '')
            self._dim_panel.set_mode_text(
                f'[F5 배치: {name} — 캔버스 클릭으로 위치 확정 / R: 회전 / V: 앵커]')
            dprint('F5', f'[F5] dims 확정 → PREVIEW 진입 ({name})')
            return

        # 중간보/기둥: 자동 계산된 position/rotation 사용
        if self._mid_auto_params is not None:
            pos = self._mid_auto_params['position'].copy()
            rot = self._mid_auto_params['rotation']
            anchor = 0
            self._mid_auto_params = None
        else:
            pos = self._preview_position
            rot = self._preview_rotation
            anchor = self._preview_anchor

        # ── z 시프트: 부재 타입별 배치 z 결정 ──
        ct = self._current_comp_type
        pos[2] = self._preview_z_for_type(ct)

        comp = _create_component(ct, pos, dims, rot, anchor)
        comp_id = self._scene.add_component(comp)
        verts, faces, colors = build_component_mesh(comp)
        self._viewer.add_component_visual(comp_id, verts, faces, colors)
        self._snap.add_component(comp_id, comp)
        dprint('PLACED', f'[PLACED] {comp.comp_type.value} #{comp_id} z={pos[2]:.0f}')

        # ── 중간보/기둥: 스냅 대상 모듈에 종속 등록 + 페어에도 동일 배치 ──
        if ct in (ComponentType.MID_BEAM, ComponentType.MID_COLUMN):
            snapped_id = self._snapped_comp_id
            if snapped_id is not None:
                self._module_children.setdefault(snapped_id, set()).add(comp_id)
                self._child_parent[comp_id] = snapped_id
                # UI 가 인식한 부모-자식 관계를 컴포넌트에 박아둔다.
                # 이 값이 scene.json 으로 직렬화되어 토폴로지에 그대로 전달됨.
                comp.parent_id = snapped_id

            # 중간보: 바닥보에 배치 시 같은 모듈의 천장보에도 자동 배치
            top_copy_id = None
            if ct == ComponentType.MID_BEAM and snapped_id is not None:
                snap_c = self._scene.components.get(snapped_id)
                if snap_c is not None:
                    h_mod = snap_c.dimensions.get('height', 3400.0)
                    top_z = pos[2] + h_mod - SECTION_W_MM
                    top_pos = pos.copy()
                    top_pos[2] = top_z
                    top_comp = _create_component(ct, top_pos, dims.copy(), rot, anchor)
                    top_copy_id = self._scene.add_component(top_comp)
                    self._scene.undo_stack.pop()
                    tv, tf, tc = build_component_mesh(top_comp)
                    self._viewer.add_component_visual(top_copy_id, tv, tf, tc)
                    self._snap.add_component(top_copy_id, top_comp)
                    self._module_children.setdefault(snapped_id, set()).add(top_copy_id)
                    self._child_parent[top_copy_id] = snapped_id
                    top_comp.parent_id = snapped_id
                    self._child_pairs[comp_id] = top_copy_id
                    self._child_pairs[top_copy_id] = comp_id
                    self._copy_ids.add(top_copy_id)
                    dprint('AUTO', f'[AUTO] 천장보 {comp.comp_type.value} #{top_copy_id} z={top_z:.0f}')

            if snapped_id is not None and snapped_id in self._floor_pairs:
                pair_id = self._floor_pairs[snapped_id]
                pair_comp = self._scene.components.get(pair_id)
                if pair_comp is not None:
                    z_offset = pair_comp.position[2] - self._scene.components[snapped_id].position[2]
                    # 바닥보 복사
                    mirror_pos = pos.copy()
                    mirror_pos[2] = pos[2] + z_offset
                    mirror_comp = _create_component(ct, mirror_pos, dims.copy(), rot, anchor)
                    mid = self._scene.add_component(mirror_comp)
                    self._scene.undo_stack.pop()
                    mv, mf, mc = build_component_mesh(mirror_comp)
                    self._viewer.add_component_visual(mid, mv, mf, mc)
                    self._snap.add_component(mid, mirror_comp)
                    self._module_children.setdefault(pair_id, set()).add(mid)
                    self._child_parent[mid] = pair_id
                    mirror_comp.parent_id = pair_id
                    self._copy_ids.add(mid)
                    dprint('AUTO', f'[AUTO] 페어 {comp.comp_type.value} #{mid} z={mirror_pos[2]:.0f}')

                    # 중간보: 페어 모듈의 천장보에도 복사
                    if ct == ComponentType.MID_BEAM:
                        h_pair = pair_comp.dimensions.get('height', 3400.0)
                        top_pair_pos = mirror_pos.copy()
                        top_pair_pos[2] = mirror_pos[2] + h_pair - SECTION_W_MM
                        top_pair = _create_component(ct, top_pair_pos, dims.copy(), rot, anchor)
                        tp_id = self._scene.add_component(top_pair)
                        self._scene.undo_stack.pop()
                        tpv, tpf, tpc = build_component_mesh(top_pair)
                        self._viewer.add_component_visual(tp_id, tpv, tpf, tpc)
                        self._snap.add_component(tp_id, top_pair)
                        self._module_children.setdefault(pair_id, set()).add(tp_id)
                        self._child_parent[tp_id] = pair_id
                        top_pair.parent_id = pair_id
                        self._copy_ids.add(tp_id)
                        # 천장보 쌍끼리 child_pairs 연결
                        if top_copy_id is not None:
                            self._child_pairs[top_copy_id] = tp_id
                            self._child_pairs[tp_id] = top_copy_id
                        # 바닥보 쌍끼리 child_pairs 연결
                        self._child_pairs[comp_id] = mid
                        self._child_pairs[mid] = comp_id
                        dprint('AUTO', f'[AUTO] 페어 천장보 {comp.comp_type.value} #{tp_id} z={top_pair_pos[2]:.0f}')
                    else:
                        self._child_pairs[comp_id] = mid
                        self._child_pairs[mid] = comp_id

        # ── 층간 복사: 모든 일반 부재에 대해 copy 생성 ──
        # (중간보/기둥은 위에서 child_pairs로 처리, 여기서 제외)
        if ct not in (ComponentType.MID_BEAM, ComponentType.MID_COLUMN):
            copy_pos = pos.copy()
            copy_pos[2] = pos[2] + FLOOR_HEIGHT
            copy_comp = _create_component(ct, copy_pos, dims.copy(), rot, anchor)
            copy_id = self._scene.add_component(copy_comp)
            self._scene.undo_stack.pop()  # 복사본 자동 undo 제거
            cv, cf, cc = build_component_mesh(copy_comp)
            self._viewer.add_component_visual(copy_id, cv, cf, cc)
            self._snap.add_component(copy_id, copy_comp)
            self._floor_pairs[comp_id] = copy_id
            self._floor_pairs[copy_id] = comp_id
            self._copy_ids.add(copy_id)
            dprint('AUTO', f'[AUTO] 층간 복사 #{copy_id} z={copy_pos[2]:.0f}')

        self._copy_dims = None  # 복사 치수 리셋
        self._status.update_count(self._scene.component_count)
        self._dim_panel.deactivate()
        self._set_state(AppState.PLACEMENT_PREVIEW)
        self._viewer.canvas.native.setFocus()

    def _on_dimension_cancelled(self):
        """치수 입력 Esc — 정책 (b) 2026-05-12: IDLE 로 완전 취소.
        옛 동작 (PLACEMENT_PREVIEW / MOVING 복귀) 폐기.
        """
        self._dim_panel.deactivate()
        self._viewer.clear_ghost()
        self._viewer.hide_snap_marker()
        self._viewer.hide_selection_box()
        self._copy_dims = None
        self._mid_auto_params = None
        self._selected_comp_id = None
        self._set_state(AppState.IDLE)
        self._viewer.canvas.native.setFocus()

    # ── Undo ─────────────────────────────────────────────────

    def _do_undo(self):
        action = self._scene.undo()
        if action is None:
            return
        if action.action_type == 'place':
            comp_id = action.data['component_id']

            # children (중간보/기둥)도 같이 제거
            self._delete_children(comp_id)

            # child_pairs sibling (중간보/기둥의 층간 복사본)도 제거
            sib_id = self._child_pairs.get(comp_id)
            if sib_id is not None:
                sib_comp = self._scene.components.pop(sib_id, None)
                if sib_comp is not None:
                    self._viewer.remove_component_visual(sib_id)
                    self._snap.remove_component(sib_id)
                # 부모/sibling 관계 정리
                parent_of_sib = self._child_parent.pop(sib_id, None)
                if parent_of_sib is not None:
                    self._module_children.get(parent_of_sib, set()).discard(sib_id)
                self._child_pairs.pop(sib_id, None)
                self._child_pairs.pop(comp_id, None)
                self._copy_ids.discard(sib_id)
                print(f'[UNDO place] sibling #{sib_id}')
            # 자신의 부모 관계도 정리
            own_parent = self._child_parent.pop(comp_id, None)
            if own_parent is not None:
                self._module_children.get(own_parent, set()).discard(comp_id)

            self._viewer.remove_component_visual(comp_id)
            self._snap.remove_component(comp_id)

            # 페어(층간 복사본)도 같이 제거
            pair_id = self._floor_pairs.get(comp_id)
            if pair_id is not None:
                pair_comp = self._scene.components.get(pair_id)
                if pair_comp is not None:
                    self._scene.components.pop(pair_id, None)
                self._viewer.remove_component_visual(pair_id)
                self._snap.remove_component(pair_id)
                self._floor_pairs.pop(comp_id, None)
                self._floor_pairs.pop(pair_id, None)
                self._copy_ids.discard(pair_id)
                print(f'[UNDO place] 층간 복사 #{pair_id}')

            self._status.update_count(self._scene.component_count)
            print(f'[UNDO place] #{comp_id}')

        elif action.action_type == 'delete':
            # 삭제 복원: 부재를 다시 씬에 추가
            comp_id = action.data['component_id']
            comp = action.data['component']
            self._scene.components[comp_id] = comp
            verts, faces, colors = build_component_mesh(comp)
            self._viewer.add_component_visual(comp_id, verts, faces, colors)
            self._snap.add_component(comp_id, comp)

            # 페어 모듈도 복원
            pair_id = action.data.get('pair_id')
            pair_comp = action.data.get('pair_component')
            if pair_id is not None and pair_comp is not None:
                self._scene.components[pair_id] = pair_comp
                pv, pf, pc = build_component_mesh(pair_comp)
                self._viewer.add_component_visual(pair_id, pv, pf, pc)
                self._snap.add_component(pair_id, pair_comp)
                # 페어 관계 + 복사본 마크 복원
                self._floor_pairs[comp_id] = pair_id
                self._floor_pairs[pair_id] = comp_id
                self._copy_ids.add(pair_id)
                print(f'[UNDO delete] 층간 복사 #{pair_id}')

            # children (중간보/기둥) 복원
            deleted_children = action.data.get('deleted_children', {})
            children_parent_map = action.data.get('children_parent_map', {})
            if deleted_children:
                self._restore_children(deleted_children, children_parent_map)
                print(f'[UNDO delete] children {len(deleted_children)}개 복원')

            self._status.update_count(self._scene.component_count)
            print(f'[UNDO delete] #{comp_id}')

        elif action.action_type == 'move':
            # 이동 복원: 원래 위치로
            comp_id = action.data['component_id']
            comp = self._scene.components.get(comp_id)
            if comp is not None:
                comp.position = action.data['old_position'].copy()
                comp.rotation = action.data['old_rotation']
                comp.anchor = action.data.get('old_anchor', comp.anchor)
                comp.generate_sub_components()
                self._viewer.remove_component_visual(comp_id)
                verts, faces, colors = build_component_mesh(comp)
                self._viewer.add_component_visual(comp_id, verts, faces, colors)
                self._snap.remove_component(comp_id)
                self._snap.add_component(comp_id, comp)
                print(f'[UNDO move] #{comp_id}')

            # 페어 모듈도 원래 위치로
            pair_id = action.data.get('pair_id')
            pair_old_pos = action.data.get('pair_old_position')
            if pair_id is not None and pair_old_pos is not None:
                pair_comp = self._scene.components.get(pair_id)
                if pair_comp is not None:
                    pair_comp.position = pair_old_pos.copy()
                    pair_comp.rotation = action.data['old_rotation']
                    pair_comp.anchor = action.data.get('old_anchor', pair_comp.anchor)
                    pair_comp.generate_sub_components()
                    self._viewer.remove_component_visual(pair_id)
                    pv, pf, pc = build_component_mesh(pair_comp)
                    self._viewer.add_component_visual(pair_id, pv, pf, pc)
                    self._snap.remove_component(pair_id)
                    self._snap.add_component(pair_id, pair_comp)
                    print(f'[UNDO move] 페어 #{pair_id}')

            # children (중간보/기둥)도 원래 위치로
            children_old = action.data.get('children_old', {})
            for mid_id, mid_old_pos in children_old.items():
                mid_comp = self._scene.components.get(mid_id)
                if mid_comp is not None:
                    mid_comp.position = mid_old_pos.copy()
                    mid_comp.generate_sub_components()
                    self._viewer.remove_component_visual(mid_id)
                    cv, cf, cc = build_component_mesh(mid_comp)
                    self._viewer.add_component_visual(mid_id, cv, cf, cc)
                    self._snap.remove_component(mid_id)
                    self._snap.add_component(mid_id, mid_comp)

            # sibling child도 원래 위치로
            sibling_id = action.data.get('sibling_id')
            sibling_old_pos = action.data.get('sibling_old_position')
            if sibling_id is not None and sibling_old_pos is not None:
                sib_comp = self._scene.components.get(sibling_id)
                if sib_comp is not None:
                    sib_comp.position = sibling_old_pos.copy()
                    sib_comp.generate_sub_components()
                    self._viewer.remove_component_visual(sibling_id)
                    sv, sf, sc = build_component_mesh(sib_comp)
                    self._viewer.add_component_visual(sibling_id, sv, sf, sc)
                    self._snap.remove_component(sibling_id)
                    self._snap.add_component(sibling_id, sib_comp)
                    print(f'[UNDO move] sibling #{sibling_id}')

            # merge 그룹도 원래 위치로
            merge_old = action.data.get('merge_old_positions', {})
            for mid, mid_old_pos in merge_old.items():
                mc = self._scene.components.get(mid)
                if mc is not None:
                    mc.position = mid_old_pos.copy()
                    mc.generate_sub_components()
                    self._viewer.remove_component_visual(mid)
                    mv, mf, mcc = build_component_mesh(mc)
                    self._viewer.add_component_visual(mid, mv, mf, mcc)
                    self._snap.remove_component(mid)
                    self._snap.add_component(mid, mc)
            if merge_old:
                print(f'[UNDO move] merge 그룹 {len(merge_old)}개 복원')

        elif action.action_type == 'merge':
            # 합체 취소 = 분리
            wall_id = action.data['wall_id']
            fp_id = action.data['fp_id']
            wall = self._scene.components.get(wall_id)
            fp = self._scene.components.get(fp_id)
            if isinstance(wall, StructWall):
                wall.merged_fp_id = None
            if isinstance(fp, FloorPanel) and wall_id in fp.merged_wall_ids:
                fp.merged_wall_ids.remove(wall_id)
            print(f'[UNDO merge] 구조벽 #{wall_id} ← 바닥패널 #{fp_id} 분리')
            self._refresh_all_meshes()

        elif action.action_type == 'unmerge':
            # 분리 취소 = 합체
            wall_id = action.data['wall_id']
            fp_id = action.data['fp_id']
            wall = self._scene.components.get(wall_id)
            fp = self._scene.components.get(fp_id)
            if isinstance(wall, StructWall):
                wall.merged_fp_id = fp_id
            if isinstance(fp, FloorPanel) and wall_id not in fp.merged_wall_ids:
                fp.merged_wall_ids.append(wall_id)
            print(f'[UNDO unmerge] 구조벽 #{wall_id} → 바닥패널 #{fp_id} 합체 복원')
            self._refresh_all_meshes()

        elif action.action_type == 'group_place':
            # 그룹 배치 취소 — _inner_actions 안의 'place'·'merge' 일괄 되돌리기
            inner = action.data.get('_inner_actions', [])
            removed_ids = []
            for ia in inner:
                if ia.action_type == 'place':
                    cid = ia.data['component_id']
                    if cid in self._scene.components:
                        self._scene.components.pop(cid, None)
                        self._viewer.remove_component_visual(cid)
                        self._snap.remove_component(cid)
                        removed_ids.append(cid)
                elif ia.action_type == 'merge':
                    wid = ia.data['wall_id']
                    fpid = ia.data['fp_id']
                    w = self._scene.components.get(wid)
                    fp = self._scene.components.get(fpid)
                    if isinstance(w, StructWall):
                        w.merged_fp_id = None
                    if isinstance(fp, FloorPanel) and wid in fp.merged_wall_ids:
                        fp.merged_wall_ids.remove(wid)
            self._status.update_count(self._scene.component_count)
            print(f'[UNDO group_place] 부재 {len(removed_ids)}개 제거')

        elif action.action_type == 'group_move':
            # 그룹 이동 취소 — 저장된 부재별 원래 position 복원
            old_positions = action.data.get('old_positions', {})
            for cid, old_pos in old_positions.items():
                comp = self._scene.components.get(cid)
                if comp is None:
                    continue
                comp.position = np.array(old_pos, dtype=np.float64)
                # rotation/anchor 도 같이 복원
                old_rot = action.data.get('old_rotations', {}).get(cid)
                old_anc = action.data.get('old_anchors', {}).get(cid)
                if old_rot is not None:
                    comp.rotation = int(old_rot)
                if old_anc is not None:
                    comp.anchor = int(old_anc)
                comp.generate_sub_components()
                self._viewer.remove_component_visual(cid)
                v, f, c = build_component_mesh(comp)
                self._viewer.add_component_visual(cid, v, f, c)
                self._snap.remove_component(cid)
                self._snap.add_component(cid, comp)
            print(f'[UNDO group_move] {len(old_positions)}개 부재 원래 위치 복원')

        elif action.action_type == 'group_delete':
            # 그룹 삭제 취소 — 저장된 부재 데이터 다시 add
            deleted_comps = action.data.get('deleted_comps', [])
            for comp in deleted_comps:
                if comp.id <= 0:
                    continue
                self._scene.components[comp.id] = comp
                v, f, c = build_component_mesh(comp)
                self._viewer.add_component_visual(comp.id, v, f, c)
                self._snap.add_component(comp.id, comp)
            self._status.update_count(self._scene.component_count)
            print(f'[UNDO group_delete] 부재 {len(deleted_comps)}개 복원')

        elif action.action_type in ('opening_add', 'opening_del', 'opening_move'):
            # 개구부 되돌리기 (3단계) — 해당 부재 메시 재생성으로 반영.
            cid = action.data['comp_id']
            idx = action.data['index']
            comp = self._scene.components.get(cid)
            if comp is not None:
                ops = getattr(comp, 'openings', None)
                if ops is not None:
                    if action.action_type == 'opening_add':
                        if 0 <= idx < len(ops):
                            ops.pop(idx)
                    elif action.action_type == 'opening_del':
                        ops.insert(min(idx, len(ops)), dict(action.data['op']))
                    else:  # opening_move
                        if 0 <= idx < len(ops):
                            ops[idx] = dict(action.data['old_op'])
                    self._viewer.remove_component_visual(cid)
                    v, f, c = build_component_mesh(comp)
                    self._viewer.add_component_visual(cid, v, f, c)
                    print(f'[UNDO {action.action_type}] #{cid}')

        elif action.action_type == 'room_add':
            rid = action.data['room_id']
            self._scene.rooms.pop(rid, None)
            if hasattr(self._viewer, 'remove_room_visual'):
                self._viewer.remove_room_visual(rid)
            print(f'[UNDO room_add] #{rid}')

        elif action.action_type == 'room_del':
            room = action.data['room']
            self._scene.rooms[room.id] = room
            if hasattr(self, '_render_room_3d'):
                self._render_room_3d(room)
            print(f'[UNDO room_del] #{room.id}')

        elif action.action_type == 'room_move':
            rid = action.data['room_id']
            room = self._scene.rooms.get(rid)
            if room is not None:
                room.polygon = [(float(x), float(y))
                                for (x, y) in action.data['old_polygon']]
                if hasattr(self, '_render_room_3d'):
                    self._render_room_3d(room)
                print(f'[UNDO room_move] #{rid}')

    # ── 피킹 (Ray-AABB) ───────────────────────────────────────

    def _pick_component(self, pos: tuple) -> Optional[int]:
        """화면 좌표에서 레이를 쏴서 가장 가까운 부재 ID 반환."""
        origin, direction = self._viewer.screen_to_world_ray(pos)
        best_t = float('inf')
        best_id = None
        for comp_id, comp in self._scene.components.items():
            bbox_min, bbox_max = comp.get_bounding_box()
            t = _ray_aabb_intersect(origin, direction, bbox_min, bbox_max)
            if t is not None and t < best_t:
                best_t = t
                best_id = comp_id
        return best_id

    def _select_component(self, comp_id: int):
        """부재 선택 + 하이라이트 표시. 복사본이면 마스터로 리다이렉트.

        합체된 벽패널/바닥패널은 선택 박스를 두 부재의 합집합 bbox 로 확장 →
        시각적으로 같이 선택되었음을 표시한다.
        """
        # 복사본 → 마스터로 리다이렉트
        if comp_id in self._copy_ids:
            master_id = self._floor_pairs.get(comp_id)
            if master_id is not None:
                comp_id = master_id
                dprint('SELECT', f'[SELECT] 복사본 → 마스터 #{comp_id}로 리다이렉트')
        self._selected_comp_id = comp_id
        comp = self._scene.components[comp_id]

        # 합체 파트너 수집
        partner_ids: List[int] = []
        if isinstance(comp, StructWall) and comp.merged_fp_id is not None:
            partner_ids.append(comp.merged_fp_id)
        elif isinstance(comp, FloorPanel) and comp.merged_wall_ids:
            partner_ids.extend(comp.merged_wall_ids)

        bbox_min, bbox_max = comp.get_bounding_box()
        bbox_min = bbox_min.copy()
        bbox_max = bbox_max.copy()
        for pid in partner_ids:
            pcomp = self._scene.components.get(pid)
            if pcomp is None:
                continue
            pmin, pmax = pcomp.get_bounding_box()
            bbox_min = np.minimum(bbox_min, pmin)
            bbox_max = np.maximum(bbox_max, pmax)

        self._viewer.show_selection_box(comp_id, bbox_min, bbox_max)
        self._set_state(AppState.SELECTED)
        name = TYPE_NAMES.get(comp.comp_type, '')
        if partner_ids:
            dprint('SELECT', f'[SELECT] {name} #{comp_id} (+합체 파트너 {partner_ids})')
        else:
            dprint('SELECT', f'[SELECT] {name} #{comp_id}')

    def _deselect(self):
        """선택 해제."""
        self._selected_comp_id = None
        self._viewer.hide_selection_box()
        self._set_state(AppState.IDLE)
        dprint('DESELECT', '[DESELECT]')

    # ── 삭제/복사 ────────────────────────────────────────────

    def _delete_selected(self):
        """선택된 부재 삭제. 페어 모듈도 같이 삭제."""
        if self._selected_comp_id is None:
            return
        comp_id = self._selected_comp_id
        comp = self._scene.components.get(comp_id)
        if comp is None:
            self._deselect()
            return

        from modular_3d.model import Action

        # 페어 모듈도 같이 삭제
        pair_id = self._floor_pairs.get(comp_id)
        pair_comp = None
        if pair_id is not None:
            pair_comp = self._scene.components.get(pair_id)

        # children (중간보/기둥) 삭제 + undo용 보존
        children_parent_map = {}
        for mid_id in self._get_all_children(comp_id):
            parent = self._child_parent.get(mid_id)
            if parent is not None:
                children_parent_map[mid_id] = parent
        deleted_children = self._delete_children(comp_id)

        # Undo 스택에 삭제 액션 추가 (페어 + children 정보 포함)
        self._scene.undo_stack.append(Action(
            action_type='delete',
            data={
                'component_id': comp_id, 'component': comp,
                'pair_id': pair_id, 'pair_component': pair_comp,
                'deleted_children': deleted_children,
                'children_parent_map': children_parent_map,
            },
        ))
        self._scene.components.pop(comp_id, None)
        self._viewer.remove_component_visual(comp_id)
        self._snap.remove_component(comp_id)

        # 페어 삭제
        if pair_id is not None and pair_comp is not None:
            self._scene.components.pop(pair_id, None)
            self._viewer.remove_component_visual(pair_id)
            self._snap.remove_component(pair_id)
            dprint('DELETE', f'[DELETE] 페어 #{pair_id}')

        # _floor_pairs / _copy_ids 정리
        self._floor_pairs.pop(comp_id, None)
        if pair_id is not None:
            self._floor_pairs.pop(pair_id, None)
            self._copy_ids.discard(pair_id)
        self._copy_ids.discard(comp_id)

        self._status.update_count(self._scene.component_count)
        self._viewer.hide_selection_box()
        self._selected_comp_id = None
        self._set_state(AppState.IDLE)
        dprint('DELETE', f'[DELETE] #{comp_id}')

    def _copy_selected(self):
        """선택된 부재와 같은 타입/치수로 배치 모드 진입."""
        if self._selected_comp_id is None:
            return
        comp = self._scene.components.get(self._selected_comp_id)
        if comp is None:
            self._deselect()
            return

        self._current_comp_type = comp.comp_type
        self._preview_rotation = comp.rotation
        self._preview_anchor = comp.anchor
        # 치수를 기본값 딕셔너리에 덮어쓰기 (일회성)
        self._copy_dims = dict(comp.dimensions)
        self._viewer.hide_selection_box()
        self._selected_comp_id = None
        self._set_state(AppState.PLACEMENT_PREVIEW)
        name = TYPE_NAMES.get(comp.comp_type, '')
        dprint('COPY', f'[COPY] {name} → 배치 모드')

    # ── 합체/분리 ─────────────────────────────────────────────
    # g 키 토글 함수는 F5 모드 전환과 함께 제거됨 (설계서 §7).
    # 병합은 이제 dim 패널의 "바닥패널과 병합" 체크박스로 트리거되며,
    # 단계 1d에서 새 핸들러가 추가됨.
    # 머지 인프라(merge_wall_to_fp / unmerge_wall_from_fp / find_nearest_fp_for_wall)
    # 자체는 그대로 남겨둔다.

    def _refresh_all_meshes(self):
        """모든 컴포넌트 메쉬 재생성 (합체/분리 후 시각화 업데이트)."""
        for cid, comp in self._scene.components.items():
            comp.generate_sub_components()
            verts, faces, colors = build_component_mesh(comp)
            self._viewer.remove_component_visual(cid)
            self._viewer.add_component_visual(cid, verts, faces, colors)

    # ── 이동 ─────────────────────────────────────────────────

    def _start_moving(self):
        """선택된 부재의 이동 모드 시작."""
        if self._selected_comp_id is None:
            return
        comp = self._scene.components.get(self._selected_comp_id)
        if comp is None:
            self._deselect()
            return
        self._move_original_pos = comp.position.copy()
        self._move_original_rot = comp.rotation
        self._move_original_anchor = comp.anchor
        self._move_rotation = comp.rotation
        self._move_anchor = comp.anchor
        self._move_position = comp.position.copy()
        self._viewer.hide_selection_box()
        self._set_state(AppState.MOVING)
        dprint('MOVE', f'[MOVE] 이동 시작 #{self._selected_comp_id}')

    def _update_moving_preview(self):
        """이동 중 고스트로 미리보기."""
        comp = self._scene.components.get(self._selected_comp_id)
        if comp is None:
            return
        # 고스트로 새 위치 표시
        ghost = _create_component(
            comp.comp_type, self._move_position,
            comp.dimensions, self._move_rotation, self._move_anchor,
        )
        verts, faces, colors = build_ghost_component_mesh(ghost)
        self._viewer.update_ghost(verts, faces, colors)

    def _confirm_moving(self):
        """이동 확정."""
        comp_id = self._selected_comp_id
        comp = self._scene.components.get(comp_id)
        if comp is None:
            self._cancel_moving()
            return

        from modular_3d.model import Action
        old_pos = self._move_original_pos.copy()
        old_rot = self._move_original_rot
        old_anchor = self._move_original_anchor

        # 페어 모듈 정보 수집
        pair_id = self._floor_pairs.get(comp_id)
        pair_comp = self._scene.components.get(pair_id) if pair_id is not None else None
        pair_old_pos = pair_comp.position.copy() if pair_comp is not None else None

        # children (중간보/기둥) 원래 위치 수집
        children_old = {}  # child_id → old_position
        for mid in self._get_all_children(comp_id):
            mid_comp = self._scene.components.get(mid)
            if mid_comp is not None:
                children_old[mid] = mid_comp.position.copy()

        # 자신이 child(중간보/기둥)인 경우, sibling도 함께 이동
        sibling_id = self._child_pairs.get(comp_id)
        sibling_comp = self._scene.components.get(sibling_id) if sibling_id is not None else None
        sibling_old_pos = sibling_comp.position.copy() if sibling_comp is not None else None

        # Undo 스택에 이동 액션 추가 (페어 + children + sibling 정보 포함)
        self._scene.undo_stack.append(Action(
            action_type='move',
            data={
                'component_id': comp_id,
                'old_position': old_pos,
                'old_rotation': old_rot,
                'old_anchor': old_anchor,
                'new_position': self._move_position.copy(),
                'new_rotation': self._move_rotation,
                'new_anchor': self._move_anchor,
                'pair_id': pair_id,
                'pair_old_position': pair_old_pos,
                'children_old': children_old,
                'sibling_id': sibling_id,
                'sibling_old_position': sibling_old_pos,
            },
        ))

        # 이동 delta 계산
        delta = self._move_position - old_pos

        # 부재 위치/회전/앵커 갱신
        comp.position = self._move_position.copy()
        comp.rotation = self._move_rotation
        comp.anchor = self._move_anchor
        comp.generate_sub_components()

        # 비주얼 갱신
        self._viewer.remove_component_visual(comp_id)
        verts, faces, colors = build_component_mesh(comp)
        self._viewer.add_component_visual(comp_id, verts, faces, colors)

        # 스냅 캐시 갱신
        self._snap.remove_component(comp_id)
        self._snap.add_component(comp_id, comp)

        # 페어 모듈도 같이 이동
        if pair_comp is not None:
            z_offset = pair_old_pos[2] - old_pos[2]
            pair_new_pos = self._move_position.copy()
            pair_new_pos[2] = self._move_position[2] + z_offset
            pair_comp.position = pair_new_pos
            pair_comp.rotation = self._move_rotation
            pair_comp.anchor = self._move_anchor
            pair_comp.generate_sub_components()
            self._viewer.remove_component_visual(pair_id)
            pv, pf, pc = build_component_mesh(pair_comp)
            self._viewer.add_component_visual(pair_id, pv, pf, pc)
            self._snap.remove_component(pair_id)
            self._snap.add_component(pair_id, pair_comp)
            dprint('MOVE', f'[MOVE] 페어 #{pair_id} 동기화')

        # children (중간보/기둥)도 같은 delta만큼 이동
        for mid_id in children_old:
            self._move_child(mid_id, delta)

        # sibling child도 같은 XY delta만큼 이동 (Z는 페어 모듈 z_offset 유지)
        if sibling_comp is not None:
            sib_delta = delta.copy()
            sib_delta[2] = 0  # 페어 모듈끼리 Z 오프셋이 따로 있으므로 XY만
            self._move_child(sibling_id, sib_delta)
            dprint('MOVE', f'[MOVE] sibling child #{sibling_id} 동기화')

        # 합체(merge)된 컴포넌트도 함께 이동
        #   - 발견된 merge 대상 + 그들의 floor_pair까지 수집
        #   - XY delta만 적용 (Z는 각자 보존)
        moved_ids = {comp_id}
        if pair_id is not None:
            moved_ids.add(pair_id)
        moved_ids.update(children_old.keys())
        if sibling_id is not None:
            moved_ids.add(sibling_id)

        merge_old_positions = self._move_merge_group(moved_ids, delta)

        # undo 데이터에 merge 이동 정보 추가
        if merge_old_positions:
            self._scene.undo_stack[-1].data['merge_old_positions'] = merge_old_positions

        self._viewer.clear_ghost()
        self._select_component(comp_id)
        print(f'[MOVE DONE] #{comp_id}')

    def _move_merge_group(self, already_moved: set, delta: np.ndarray) -> dict:
        """합체(merge) 관계로 연결된 컴포넌트를 XY delta만큼 이동.
        Returns: {comp_id: old_position} (undo용)
        """
        # merge 대상 수집: 이미 이동된 comp/pair에서 탐색
        #   FP → merged_wall_ids + pair,  StructWall → merged_fp_id + pair + FP의 다른 merged walls
        merge_targets = set()
        for cid in list(already_moved):
            c = self._scene.components.get(cid)
            if c is None:
                continue
            if isinstance(c, FloorPanel) and c.merged_wall_ids:
                for wid in c.merged_wall_ids:
                    merge_targets.add(wid)
                    wp = self._floor_pairs.get(wid)
                    if wp is not None:
                        merge_targets.add(wp)
            if isinstance(c, StructWall) and c.merged_fp_id is not None:
                fp_id = c.merged_fp_id
                merge_targets.add(fp_id)
                fpp = self._floor_pairs.get(fp_id)
                if fpp is not None:
                    merge_targets.add(fpp)
                fp = self._scene.components.get(fp_id)
                if isinstance(fp, FloorPanel):
                    for wid in fp.merged_wall_ids:
                        merge_targets.add(wid)
                        wp = self._floor_pairs.get(wid)
                        if wp is not None:
                            merge_targets.add(wp)

        merge_targets -= already_moved

        delta_xy = delta.copy()
        delta_xy[2] = 0.0

        old_positions = {}
        for mid in merge_targets:
            mc = self._scene.components.get(mid)
            if mc is None:
                continue
            old_positions[mid] = mc.position.copy()
            mc.position = mc.position + delta_xy
            mc.generate_sub_components()
            self._viewer.remove_component_visual(mid)
            verts, faces, colors = build_component_mesh(mc)
            self._viewer.add_component_visual(mid, verts, faces, colors)
            self._snap.remove_component(mid)
            self._snap.add_component(mid, mc)

        if old_positions:
            dprint('MOVE', f'[MOVE] merge 그룹 {len(old_positions)}개 동기화')
        return old_positions

    def _cancel_moving(self):
        """이동 취소 — 원래 위치 보존 + IDLE 로 완전 취소 (정책 b 2026-05-12)."""
        self._viewer.clear_ghost()
        self._viewer.hide_snap_marker()
        self._viewer.hide_selection_box()
        self._selected_comp_id = None
        self._set_state(AppState.IDLE)

    # ── 축 이동 (X/Y + 거리 입력) ───────────────────────────

    def _start_axis_move(self, axis: str):
        """MOVING 상태에서 X/Y 축 선택 → 거리 입력 모드."""
        self._move_axis = axis
        self._viewer.clear_ghost()
        self._viewer.hide_snap_marker()
        self._dim_panel.activate_move(axis)
        self._set_state(AppState.DIMENSION_INPUT)
        print(f'[AXIS MOVE] {axis}축 거리 입력')

    def _confirm_axis_move(self):
        """축 이동 거리 확정."""
        dist = self._dim_panel.get_move_distance()
        self._dim_panel.deactivate()

        comp_id = self._selected_comp_id
        comp = self._scene.components.get(comp_id)
        if comp is None:
            self._set_state(AppState.IDLE)
            return

        from modular_3d.model import Action
        old_pos = self._move_original_pos.copy()
        old_rot = self._move_original_rot
        old_anchor = self._move_original_anchor

        # 축에 따른 delta 계산
        delta = np.zeros(3)
        if self._move_axis == 'X':
            delta[0] = dist
        else:
            delta[1] = dist

        new_pos = old_pos + delta

        # 페어 모듈 정보 수집
        pair_id = self._floor_pairs.get(comp_id)
        pair_comp = self._scene.components.get(pair_id) if pair_id is not None else None
        pair_old_pos = pair_comp.position.copy() if pair_comp is not None else None

        # children 원래 위치 수집
        children_old = {}
        for mid in self._get_all_children(comp_id):
            mid_comp = self._scene.components.get(mid)
            if mid_comp is not None:
                children_old[mid] = mid_comp.position.copy()

        # 자신이 child인 경우, sibling
        sibling_id = self._child_pairs.get(comp_id)
        sibling_comp = self._scene.components.get(sibling_id) if sibling_id is not None else None
        sibling_old_pos = sibling_comp.position.copy() if sibling_comp is not None else None

        # Undo 스택
        self._scene.undo_stack.append(Action(
            action_type='move',
            data={
                'component_id': comp_id,
                'old_position': old_pos,
                'old_rotation': old_rot,
                'old_anchor': old_anchor,
                'new_position': new_pos.copy(),
                'new_rotation': old_rot,
                'new_anchor': old_anchor,
                'pair_id': pair_id,
                'pair_old_position': pair_old_pos,
                'children_old': children_old,
                'sibling_id': sibling_id,
                'sibling_old_position': sibling_old_pos,
            },
        ))

        # 부재 이동
        comp.position = new_pos.copy()
        comp.generate_sub_components()
        self._viewer.remove_component_visual(comp_id)
        verts, faces, colors = build_component_mesh(comp)
        self._viewer.add_component_visual(comp_id, verts, faces, colors)
        self._snap.remove_component(comp_id)
        self._snap.add_component(comp_id, comp)

        # 페어 이동
        if pair_comp is not None:
            z_offset = pair_old_pos[2] - old_pos[2]
            pair_new_pos = new_pos.copy()
            pair_new_pos[2] = new_pos[2] + z_offset
            pair_comp.position = pair_new_pos
            pair_comp.generate_sub_components()
            self._viewer.remove_component_visual(pair_id)
            pv, pf, pc = build_component_mesh(pair_comp)
            self._viewer.add_component_visual(pair_id, pv, pf, pc)
            self._snap.remove_component(pair_id)
            self._snap.add_component(pair_id, pair_comp)

        # children 이동
        for mid_id in children_old:
            self._move_child(mid_id, delta)

        # sibling child 이동 (XY만)
        if sibling_comp is not None:
            sib_delta = delta.copy()
            sib_delta[2] = 0
            self._move_child(sibling_id, sib_delta)
            print(f'[AXIS MOVE] sibling child #{sibling_id} 동기화')

        # 합체(merge)된 컴포넌트도 함께 이동
        moved_ids = {comp_id}
        if pair_id is not None:
            moved_ids.add(pair_id)
        moved_ids.update(children_old.keys())
        if sibling_id is not None:
            moved_ids.add(sibling_id)

        merge_old_positions = self._move_merge_group(moved_ids, delta)
        if merge_old_positions:
            self._scene.undo_stack[-1].data['merge_old_positions'] = merge_old_positions

        self._viewer.canvas.native.setFocus()
        self._select_component(comp_id)
        axis = self._move_axis
        print(f'[AXIS MOVE DONE] #{comp_id} {axis}={dist:.0f}mm')

    # ── 중간보/기둥 자동 배치 계산 ────────────────────────────
    # 단계 5 (2026-05-08): 본문은 controls_geom 으로 이관. 본 메소드는 위임만 한다.

    def _calc_mid_beam_placement(self, snap_pos, snap_comp):
        """스냅 지점에서 중간보 자동 배치 (controls_geom 위임)."""
        return _calc_mid_beam_placement_pure(snap_pos, snap_comp)

    def _calc_mid_column_placement(self, snap_pos, snap_comp):
        """스냅 지점에 중간기둥 자동 배치 (controls_geom 위임)."""
        return _calc_mid_column_placement_pure(snap_pos, snap_comp)

    # ── Children (중간보/기둥 종속) 헬퍼 ─────────────────────

    def _get_all_children(self, comp_id: int) -> set:
        """comp_id와 그 페어의 모든 children ID를 반환."""
        result = set(self._module_children.get(comp_id, set()))
        pair_id = self._floor_pairs.get(comp_id)
        if pair_id is not None:
            result |= self._module_children.get(pair_id, set())
        return result

    def _move_child(self, child_id: int, delta: np.ndarray):
        """child를 delta만큼 이동 + 비주얼/스냅 갱신."""
        child_comp = self._scene.components.get(child_id)
        if child_comp is None:
            return
        child_comp.position = child_comp.position + delta
        child_comp.generate_sub_components()
        self._viewer.remove_component_visual(child_id)
        cv, cf, cc = build_component_mesh(child_comp)
        self._viewer.add_component_visual(child_id, cv, cf, cc)
        self._snap.remove_component(child_id)
        self._snap.add_component(child_id, child_comp)

    def _delete_children(self, comp_id: int) -> dict:
        """comp_id와 그 페어의 모든 children 삭제. {child_id: Component} 반환."""
        deleted = {}
        for mid_id in list(self._get_all_children(comp_id)):
            mid_comp = self._scene.components.pop(mid_id, None)
            if mid_comp is not None:
                deleted[mid_id] = mid_comp
                self._viewer.remove_component_visual(mid_id)
                self._snap.remove_component(mid_id)
            # 종속 관계 정리
            self._child_parent.pop(mid_id, None)
            self._copy_ids.discard(mid_id)
            # sibling 페어 링크 정리
            sib = self._child_pairs.pop(mid_id, None)
            if sib is not None:
                self._child_pairs.pop(sib, None)
        # children 집합 정리
        self._module_children.pop(comp_id, None)
        pair_id = self._floor_pairs.get(comp_id)
        if pair_id is not None:
            self._module_children.pop(pair_id, None)
        return deleted

    def _restore_children(self, deleted_children: dict, parent_map: dict):
        """삭제된 children 복원. parent_map: {child_id: parent_id}."""
        for mid_id, mid_comp in deleted_children.items():
            self._scene.components[mid_id] = mid_comp
            cv, cf, cc = build_component_mesh(mid_comp)
            self._viewer.add_component_visual(mid_id, cv, cf, cc)
            self._snap.add_component(mid_id, mid_comp)
            parent_id = parent_map.get(mid_id)
            if parent_id is not None:
                self._module_children.setdefault(parent_id, set()).add(mid_id)
                self._child_parent[mid_id] = parent_id
                mid_comp.parent_id = parent_id

        # sibling 페어 링크 재구성: 같은 XY/타입을 가진 child 쌍 매칭
        restored_ids = list(deleted_children.keys())
        for i, a_id in enumerate(restored_ids):
            if a_id in self._child_pairs:
                continue
            a = deleted_children[a_id]
            for b_id in restored_ids[i+1:]:
                if b_id in self._child_pairs:
                    continue
                b = deleted_children[b_id]
                if (a.comp_type == b.comp_type and
                    abs(a.position[0] - b.position[0]) < 1.0 and
                    abs(a.position[1] - b.position[1]) < 1.0 and
                    self._child_parent.get(a_id) != self._child_parent.get(b_id)):
                    self._child_pairs[a_id] = b_id
                    self._child_pairs[b_id] = a_id
                    # 높은 z가 복사본
                    if a.position[2] > b.position[2]:
                        self._copy_ids.add(a_id)
                    else:
                        self._copy_ids.add(b_id)
                    break

    # ── 내부 ─────────────────────────────────────────────────

    def _cancel_placement(self):
        self._viewer.clear_ghost()
        self._viewer.hide_snap_marker()
        self._dim_panel.deactivate()
        self._copy_dims = None
        self._mid_auto_params = None
        self._set_state(AppState.IDLE)

