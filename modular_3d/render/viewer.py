"""
3D 렌더링 — Vispy SceneCanvas + 커스텀 카메라.
카메라 좌표계: vispy TurntableCamera 컨벤션
  - azim=0, elev=0 → 카메라가 -Y 방향에서 +Y를 바라봄
  - azimuth: Z축 기준 반시계 회전 (위에서 볼 때)
  - elevation: 수평면에서 아래로 회전 (양수=아래서 위를 올려다봄)
"""
import numpy as np
from vispy import scene
from vispy.scene import visuals
from typing import Dict, List, Tuple
from modular_3d._utils.debug import dprint


# ─────────────────────────────────────────────────────────────
# 구조해석 와이어프레임 색 단일 정의 (2026-05-12 명세통합 Phase 1)
# 색 의미 변경 / 통합 시 본 상수만 손대면 전체 표시가 일관되게 따라감.
# 통합 정책:
#   - 일반 보·기둥           : 검정
#   - 면 요소 외곽선(코어벽/슬래브) : 짙은 보라 (한 색)
#   - 요소 간 핀 결합(2/3 DOF)  : 노랑 (panel_module·wall_module·vertical_stack·
#                              horizontal_adjacent·wall_stack 통합)
#   - 완전 겹침 6 DOF 강체 결합 : 진한 자홍 (빨강 점과 충돌 회피)
#   - 일반 노드 점            : 빨강
#   - 베이스 고정점           : 검정 ▼
# ─────────────────────────────────────────────────────────────
OPS_COLOR_BEAM     = (0.05, 0.05, 0.05, 1.0)   # 검정 — 일반 보·기둥
OPS_COLOR_SHELL    = (0.45, 0.10, 0.55, 1.0)   # 짙은 보라 — 면 요소 외곽선
OPS_COLOR_CORE     = (0.45, 0.10, 0.55, 1.0)   # 짙은 보라 — RC 코어 골조 (셸과 통일)
OPS_COLOR_PIN      = (0.95, 0.85, 0.10, 1.0)   # 노랑 — 핀 결합 (2/3 DOF)
OPS_COLOR_RIGID6   = (0.70, 0.05, 0.50, 1.0)   # 진한 자홍 — 6 DOF 완전 겹침 강체
OPS_COLOR_NODE     = (0.30, 0.30, 0.30, 1.0)   # 어두운 회색 점 — 일반 노드
OPS_COLOR_NODE_EDGE = (0.15, 0.15, 0.15, 1.0)  # 어두운 회색 점 테두리
OPS_COLOR_BASE     = (0.00, 0.00, 0.00, 1.0)   # 검정 — 베이스 고정점 ▼

# 결합 종류 → 색 매핑 (interface_links 시각화용).
# 핀 결합 종류들은 모두 OPS_COLOR_PIN, 완전 겹침은 OPS_COLOR_RIGID6.
# 'wall_fp_merge' 는 사용자 관점에서 한 덩어리이므로 그리지 않음.
OPS_LINK_KIND_COLOR = {
    'panel_module':        OPS_COLOR_PIN,
    'wall_module':         OPS_COLOR_PIN,
    'vertical_stack':      OPS_COLOR_PIN,
    'horizontal_adjacent': OPS_COLOR_PIN,
    'wall_stack':          OPS_COLOR_PIN,
    'core_pin':            OPS_COLOR_PIN,
    'slab_edge_z':         OPS_COLOR_PIN,
    'merged_overlap':      OPS_COLOR_RIGID6,
}
OPS_LINK_SKIP_KINDS = {'wall_fp_merge'}

# [2026-05-13 접합부조정탭 Phase 5] rule_id → 색 매핑.
# spec 의 Link/Diaphragm/RigidLink 레코드에 부여된 rule_id 별로 결합선을 다른
# 색으로 그려, 사용자가 "이 결합이 어떤 규칙에 의해 등록됐는지" 한 눈에 식별
# 가능하게 한다. 본 dict 에 없는 rule_id 는 기존 kind/dof 색 매핑으로 폴백.
# 'legacy_auto' 는 일부러 매핑에 포함하지 않음 — 기존 색 유지(회귀 방지).
# 후속 작업에서 새 명시 룰 추가 시 본 dict 에 한 줄씩 등록.
OPS_RULE_ID_COLOR: Dict[str, Tuple[float, float, float, float]] = {
    # R01 — 모듈-모듈 수평 인접 핀 결합. 밝은 시안.
    'R01_mod_mod_h': (0.15, 0.85, 0.95, 1.0),
    # R02 — 모듈-모듈 수직 적층 핀 결합. 밝은 주황.
    'R02_mod_mod_v': (1.00, 0.55, 0.10, 1.0),
    # R03 — 바닥패널-모듈 천장 핀 결합. 밝은 연두.
    'R03_panel_mod': (0.55, 0.90, 0.25, 1.0),
    # R04 — 패널-패널 수평 핀 결합. 밝은 자홍.
    'R04_panel_panel': (0.95, 0.35, 0.85, 1.0),
    # R05 — 벽-바닥 수직 핀 결합. 밝은 노랑.
    'R05_wall_floor_v': (0.95, 0.90, 0.20, 1.0),
    # R06 — 벽-모듈 핀 결합 (캔틸 보 자유단 포함). 밝은 파랑.
    'R06_wall_mod':    (0.25, 0.45, 0.95, 1.0),
    # R07 — 벽-벽 수평 핀 결합. 밝은 보라.
    'R07_wall_wall_h': (0.65, 0.30, 0.95, 1.0),
    # R08 — 벽-캔틸레버 보 자유단 결합. 밝은 청록.
    'R08_wall_cant':   (0.10, 0.80, 0.70, 1.0),
    # R09 — 외부 부재 ↔ 코어 (벽·슬래브) 6 DOF 강결합. 짙은 적색.
    'R09_core':        (0.95, 0.20, 0.20, 1.0),
    # 사용자가 직접 추가한 신규 접합 — 핀(R10)·강접(R11) 구분.
    # (흰색은 밝은 배경에서 안 보여 채도 높은 색으로.)
    'USER_ADD':        (0.10, 0.90, 0.50, 1.0),  # 구버전 호환 — 민트
    'R10_user_pin':    (0.10, 0.90, 0.50, 1.0),  # 사용자 추가(핀) — 민트
    'R11_user_rigid':  (1.00, 0.20, 0.60, 1.0),  # 사용자 추가(강접) — 핫핑크
    # 다이어프램 시스템 (자동 휴리스틱 아닌 표준 가정).
    'diaphragm_floor':         (0.55, 0.55, 0.55, 0.55),  # 회색
    'diaphragm_slab_edge_z':   (0.55, 0.55, 0.55, 0.85),  # 진한 회색
}

# (Phase 8 토글) 다이어프램·강체 링크 색
OPS_COLOR_DIAPHRAGM = (0.55, 0.55, 0.55, 0.55)  # 회색 점선 (반투명)
OPS_COLOR_RIGIDLINK = (0.35, 0.35, 0.35, 0.85)  # 진한 회색 실선

# (Phase 9 토글) DOF 묶음 수별 색 분리 모드.
# 2 DOF (xy 핀): 연두 / 3 DOF (xyz 핀): 주황 / 6 DOF (강체): 자홍 / 1 DOF: 청록
OPS_COLOR_DOF_1 = (0.20, 0.80, 0.85, 1.0)   # 청록 — 1 DOF (horizontal_adjacent 등)
OPS_COLOR_DOF_2 = (0.50, 0.85, 0.20, 1.0)   # 연두 — 2 DOF (core_pin)
OPS_COLOR_DOF_3 = (1.00, 0.55, 0.10, 1.0)   # 주황 — 3 DOF (대다수 핀)
OPS_COLOR_DOF_6 = (0.70, 0.05, 0.50, 1.0)   # 진한 자홍 — 6 DOF (강체)


def _cam_axes(cam):
    """카메라의 world-space 위치, forward, right, up 벡터 계산."""
    elev = np.radians(cam.elevation)
    azim = np.radians(cam.azimuth)
    dist = cam.distance
    center = np.array(cam.center, dtype=np.float64)

    cos_e, sin_e = np.cos(elev), np.sin(elev)
    cos_a, sin_a = np.cos(azim), np.sin(azim)

    # 카메라 위치 (vispy 실측 결과에 기반)
    cam_pos = center + dist * np.array([
        sin_a * cos_e,
        -cos_a * cos_e,
        -sin_e,
    ])

    forward = center - cam_pos
    forward = forward / np.linalg.norm(forward)

    up_world = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, up_world)
    r_norm = np.linalg.norm(right)
    if r_norm < 1e-10:
        # forward가 Z축과 평행할 때 (위/아래에서 내려다봄)
        right = np.array([cos_a, sin_a, 0.0])
    else:
        right = right / r_norm

    up = np.cross(right, forward)
    up = up / np.linalg.norm(up)

    return cam_pos, forward, right, up


def _raycast_z0_from_cam(cam, ndc_x, ndc_y, canvas_size=None):
    """NDC 좌표에서 Z=0 평면 교차점 반환. None이면 교차 없음."""
    fov_rad = np.radians(cam.fov)
    half_h = np.tan(fov_rad / 2.0)
    if canvas_size:
        aspect = canvas_size[0] / canvas_size[1]
    else:
        aspect = 1.5
    half_w = half_h * aspect

    cam_pos, forward, right, up = _cam_axes(cam)

    ray_dir = forward + (ndc_x * half_w) * right + (ndc_y * half_h) * up
    ray_dir = ray_dir / np.linalg.norm(ray_dir)

    if abs(ray_dir[2]) < 1e-10:
        return None
    t = -cam_pos[2] / ray_dir[2]
    if t < 0:
        return None
    return cam_pos + t * ray_dir


class CustomCamera(scene.TurntableCamera):
    """
    가운데 버튼 = Orbit, Shift+가운데 = Pan, 휠 = 커서방향 줌.
    왼클릭/오른클릭은 카메라가 무시 (도구용으로 전달).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._drag_mode = None   # 'rotate' or 'pan'
        self._drag_start = None  # 드래그 시작 마우스 위치
        self._drag_azim0 = 0
        self._drag_elev0 = 0
        self._drag_center0 = None

    def viewbox_mouse_event(self, event):
        if event.type == 'mouse_wheel':
            self._zoom_toward_cursor(event)
            event.handled = True
            return

        # 가운데 버튼 (vispy button 3)
        if event.type == 'mouse_press' and event.button == 3:
            self._drag_start = np.array(event.pos, dtype=np.float64)
            if 'Shift' in event.modifiers:
                self._drag_mode = 'pan'
                self._drag_center0 = np.array(self.center, dtype=np.float64)
            else:
                self._drag_mode = 'rotate'
                self._drag_azim0 = self.azimuth
                self._drag_elev0 = self.elevation
            event.handled = True
            return

        if event.type == 'mouse_move' and self._drag_mode and self._drag_start is not None:
            pos = np.array(event.pos, dtype=np.float64)
            delta = pos - self._drag_start

            if self._drag_mode == 'rotate':
                # dx → azimuth, dy → elevation
                sensitivity = 0.3
                self.azimuth = self._drag_azim0 - delta[0] * sensitivity
                self.elevation = np.clip(
                    self._drag_elev0 + delta[1] * sensitivity, -90, 90
                )
                self.view_changed()

            elif self._drag_mode == 'pan':
                _, forward, right, _ = _cam_axes(self)
                # 수평: right 벡터 (이미 Z=0), 수직: forward를 Z=0에 투영
                forward_flat = forward.copy()
                forward_flat[2] = 0.0
                fn = np.linalg.norm(forward_flat)
                if fn > 1e-10:
                    forward_flat = forward_flat / fn
                # 위에서 아래를 볼 때 (elevation < 0) 상하 팬 반전
                vert_sign = -1.0 if self.elevation < 0 else 1.0
                # 화면 1px당 이동량: distance 비례
                scale = self.distance * 0.001
                shift = -delta[0] * scale * right + delta[1] * vert_sign * scale * forward_flat
                self.center = tuple(self._drag_center0 + shift)
                self.view_changed()

            event.handled = True
            return

        if event.type == 'mouse_release' and event.button == 3:
            self._drag_mode = None
            self._drag_start = None
            event.handled = True
            return

        # 왼클릭/오른클릭은 카메라가 처리하지 않음

    def _zoom_toward_cursor(self, event):
        """마우스 커서 방향으로 확대/축소. 매 휠 이벤트마다 현재 카메라로 재계산."""
        factor = 0.85 if event.delta[1] > 0 else 1.0 / 0.85

        # 커서 위치 → NDC
        canvas = self._viewbox.canvas
        if canvas is None:
            self.distance *= factor
            self.view_changed()
            return

        w, h = canvas.size
        # vispy event.pos는 vispy 좌표 (y=0이 아래)
        ndc_x = (2.0 * event.pos[0] / w) - 1.0
        ndc_y = (2.0 * event.pos[1] / h) - 1.0  # vispy는 이미 y-up

        hit = _raycast_z0_from_cam(self, ndc_x, ndc_y, canvas_size=(w, h))
        if hit is not None:
            center = np.array(self.center, dtype=np.float64)
            # center를 커서 방향으로 이동 (줌에 비례)
            shift = (hit[:3] - center) * (1.0 - factor)
            self.center = tuple(center + shift)

        self.distance *= factor
        self.view_changed()


class Viewer3D:
    """Vispy 3D 뷰어 관리."""

    def __init__(self):
        self.canvas = scene.SceneCanvas(
            keys=None,
            show=False,
            bgcolor='white',
            size=(1200, 800),
        )
        self.view = self.canvas.central_widget.add_view()

        # 카메라
        self.view.camera = CustomCamera(
            distance=20000,
            fov=45,
            elevation=30,
            azimuth=-60,
            up='z',
            center=(3000, 3000, 0),
        )

        # XYZ 축
        self._create_axis_arrows()

        # 부재 비주얼 관리
        self._component_visuals: Dict[int, visuals.Mesh] = {}
        # ops 뷰 토글 시 face_colors 원본 백업 — __init__ 에서 단일 진입점.
        self._comp_orig_face_colors: Dict[int, np.ndarray] = {}

        # 고스트 미리보기
        self._ghost_mesh = visuals.Mesh(parent=self.view.scene)
        self._ghost_mesh.visible = False
        self._ghost_mesh.order = 10
        self._ghost_mesh.set_gl_state('translucent', depth_test=True)

        # 스냅 하이라이트 마커 (항상 메쉬 위에 그려지도록)
        self._snap_marker = visuals.Markers(parent=self.view.scene)
        self._snap_marker.visible = False
        self._snap_marker.order = -1  # 다른 메쉬보다 나중에 렌더링
        self._snap_marker.set_gl_state('translucent', depth_test=False)

        # 선택 하이라이트 (빨간 와이어프레임 박스)
        self._selection_box = visuals.Line(parent=self.view.scene)
        self._selection_box.visible = False
        self._selection_box.order = -2
        self._selection_box.set_gl_state('translucent', depth_test=False)
        self._selected_comp_id: int = -1

        # ── 해석 부재 하이라이트 (패널에서 부재 클릭 시) ──
        self._member_highlight = visuals.Line(parent=self.view.scene)
        self._member_highlight.visible = False
        self._member_highlight.order = 16
        self._member_highlight.set_gl_state('translucent', depth_test=False)

        # ── 부재 호버 강조 (Phase 2, 설계서_부재호버.md) ──
        # 마우스 호버 시 회색 임시 강조. 클릭 고정 강조(_member_highlight) 와
        # 별도 비주얼이라 호버 깜빡임이 트리 선택 강조에 영향을 주지 않는다.
        # Phase 13 에서 색이 "다음 배정 슬롯 색" 으로 교체된다.
        self._member_hover = visuals.Line(parent=self.view.scene)
        self._member_hover.visible = False
        self._member_hover.order = 17
        self._member_hover.set_gl_state('translucent', depth_test=False)

        # ── 클릭 고정 다중 부재 강조 (Phase 9, 설계서_부재호버.md) ──
        # 최대 5 슬롯의 클릭 고정 부재를 슬롯별 색으로 동시 표시.
        # per-vertex color 사용 → 단일 visuals.Line + segments 모드.
        self._pinned_member_lines = visuals.Line(parent=self.view.scene)
        self._pinned_member_lines.visible = False
        self._pinned_member_lines.order = 18
        self._pinned_member_lines.set_gl_state('translucent', depth_test=False)

        # ── 하중 흐름 오버레이 Stage 3: 기둥 상단 축력 수치 ──
        self._col_head_text = visuals.Text(
            text='', color=(0.12, 0.31, 0.62, 1.0),
            font_size=120, bold=True,
            parent=self.view.scene,
            anchor_x='left', anchor_y='bottom',
        )
        self._col_head_text.visible = False
        self._col_head_text.order = 17
        self._col_head_text.set_gl_state(depth_test=False)

        # ── 하중 흐름 오버레이 Stage 1: 슬래브 판정 + 분배 패턴 ──
        # (a) 슬래브 면 반투명 Mesh — 1-way/2-way/cantilever 색 구분
        self._slab_mesh = visuals.Mesh(parent=self.view.scene)
        self._slab_mesh.visible = False
        self._slab_mesh.order = 13   # 부재 메쉬(0) 위, 텍스트(17) 아래
        self._slab_mesh.set_gl_state('translucent', depth_test=False, cull_face=False)

        # (b) 각 보의 분배 패턴 프로파일 Line (uniform/triangle/trapezoid 모양)
        self._slab_profile_line = visuals.Line(parent=self.view.scene)
        self._slab_profile_line.visible = False
        self._slab_profile_line.order = 16
        self._slab_profile_line.set_gl_state('translucent', depth_test=False)

        # (c) 슬래브 중앙에 "1-way/2-way (b/a=…)" 판정 라벨
        self._slab_label_text = visuals.Text(
            text='', color=(0.10, 0.10, 0.10, 1.0),
            font_size=140, bold=True,
            parent=self.view.scene,
            anchor_x='center', anchor_y='center',
        )
        self._slab_label_text.visible = False
        self._slab_label_text.order = 18
        self._slab_label_text.set_gl_state(depth_test=False)

        # (d) 각 보 패턴 옆에 peak 수치 표시
        self._slab_peak_text = visuals.Text(
            text='', color=(0.15, 0.35, 0.85, 1.0),
            font_size=100, bold=True,
            parent=self.view.scene,
            anchor_x='center', anchor_y='bottom',
        )
        self._slab_peak_text.visible = False
        self._slab_peak_text.order = 18
        self._slab_peak_text.set_gl_state(depth_test=False)

        # ── 하중 흐름 오버레이 Stage 2: 보 UDL 프로파일 ──
        # 각 보 부재 아래(-z)로 사각형 프로파일을 그리고 수치를 표시한다.
        self._beam_udl_line = visuals.Line(parent=self.view.scene)
        self._beam_udl_line.visible = False
        self._beam_udl_line.order = 16
        self._beam_udl_line.set_gl_state('translucent', depth_test=False)

        self._beam_udl_text = visuals.Text(
            text='', color=(0.0, 0.45, 0.18, 1.0),
            font_size=110, bold=True,
            parent=self.view.scene,
            anchor_x='center', anchor_y='top',
        )
        self._beam_udl_text.visible = False
        self._beam_udl_text.order = 17
        self._beam_udl_text.set_gl_state(depth_test=False)

        # ── 하중 흐름 오버레이 Stage 4: 베이스 반력 ──
        self._reaction_line = visuals.Line(parent=self.view.scene)
        self._reaction_line.visible = False
        self._reaction_line.order = 16
        self._reaction_line.set_gl_state('translucent', depth_test=False)

        self._reaction_text = visuals.Text(
            text='', color=(0.84, 0.16, 0.16, 1.0),
            font_size=140, bold=True,
            parent=self.view.scene,
            anchor_x='left', anchor_y='bottom',
        )
        self._reaction_text.visible = False
        self._reaction_text.order = 17
        self._reaction_text.set_gl_state(depth_test=False)

        # ── 접합부 시각화 ──
        self._joint_markers = visuals.Markers(parent=self.view.scene)
        self._joint_markers.visible = False
        self._joint_markers.order = 15
        self._joint_markers.set_gl_state('translucent', depth_test=False)

        self._joint_lines = visuals.Line(parent=self.view.scene)
        self._joint_lines.visible = False
        self._joint_lines.order = 14
        self._joint_lines.set_gl_state('translucent', depth_test=False)

        self._joint_pin_lines = visuals.Line(parent=self.view.scene)
        self._joint_pin_lines.visible = False
        self._joint_pin_lines.order = 16
        self._joint_pin_lines.set_gl_state('translucent', depth_test=False)

        self._joint_text = visuals.Text(
            text='', color=(0.80, 0.20, 0.60, 1.0),
            font_size=100, bold=True,
            parent=self.view.scene,
            anchor_x='left', anchor_y='bottom',
        )
        self._joint_text.visible = False
        self._joint_text.order = 18
        self._joint_text.set_gl_state(depth_test=False)

    def _create_axis_arrows(self):
        """원점에 XYZ 축 화살표."""
        length = 1500.0
        axis_data = np.array([
            [0, 0, 0], [length, 0, 0],
            [0, 0, 0], [0, length, 0],
            [0, 0, 0], [0, 0, length],
        ], dtype=np.float32)
        axis_colors = np.array([
            [1, 0, 0, 1], [1, 0, 0, 1],
            [0, 0.7, 0, 1], [0, 0.7, 0, 1],
            [0, 0, 1, 1], [0, 0, 1, 1],
        ], dtype=np.float32)
        self._axis_lines = visuals.Line(
            pos=axis_data, color=axis_colors,
            connect='segments', width=2,
            parent=self.view.scene,
        )
        for label, pos, color in [
            ('X', (length + 200, 0, 0), 'red'),
            ('Y', (0, length + 200, 0), 'green'),
            ('Z', (0, 0, length + 200), 'blue'),
        ]:
            visuals.Text(
                text=label, pos=pos, color=color,
                font_size=100, parent=self.view.scene,
                anchor_x='center', anchor_y='center',
            )

    # ── 부재 비주얼 관리 ─────────────────────────────────────

    def add_component_visual(self, comp_id: int, vertices, faces, face_colors):
        # [2026-05-25 버그수정] 같은 comp_id 의 기존 메시를 먼저 scene 에서 제거.
        # 안 하면 dict 만 덮어쓰고 옛 메시가 parent 채로 화면에 잔류(유령 메시) →
        # ops 뷰 디밍(dict 순회)에서 빠져 면으로 보이고, 삭제/되돌리기로도 안
        # 지워진다. 단면 타입 변경(_set_beam_section_type / _sync_dependent_beam_
        # sections)이 remove 없이 add 만 호출하던 경로에서 드러난 버그.
        self.remove_component_visual(comp_id)
        mesh = visuals.Mesh(
            vertices=vertices, faces=faces,
            face_colors=face_colors, shading=None,
            parent=self.view.scene,
        )
        self._component_visuals[comp_id] = mesh
        # 반투명 면 포함 시(내벽·구조벽 채움·모듈 4면벽 alpha<1) translucent gl_state.
        # depth_test=True 라 불투명 강재(alpha=1)는 그대로 가림, 벽만 비친다.
        try:
            _fc = np.asarray(face_colors, dtype=np.float32)
            if _fc.size and float(_fc[:, 3].min()) < 0.999:
                mesh.set_gl_state('translucent', depth_test=True, cull_face=False)
        except Exception:
            pass
        # ops 뷰 토글 시 복원용 — 원본 face_colors 를 영구 보관 (mesh_data 추출은
        # vispy 버전에 따라 None 을 반환할 수 있어 신뢰 불가).
        if not hasattr(self, '_comp_orig_face_colors'):
            self._comp_orig_face_colors = {}
        try:
            orig_arr = np.array(face_colors, dtype=np.float32).copy()
            self._comp_orig_face_colors[comp_id] = orig_arr
        except Exception:
            orig_arr = None

        # ops 뷰가 활성 중에 새 컴포넌트가 추가되면 곧장 디밍 처리해야 시각 일관성 유지.
        # 단 ops 모델 자체에는 새 컴포넌트가 반영 안 됨 → 사용자에게 F6 갱신 안내 필요.
        if getattr(self, '_ops_view_active', False) and orig_arr is not None:
            n_faces = len(orig_arr)
            if n_faces > 0:
                gray = np.tile(np.array([[0.65, 0.65, 0.65, 0.18]],
                                        dtype=np.float32), (n_faces, 1))
                mesh.set_data(face_colors=gray)
                mesh.set_gl_state('translucent', depth_test=False, cull_face=False)
                dprint('VIEWER', '[VIEWER] ops 뷰 활성 중 새 컴포넌트 추가 — F6 다시 눌러 해석 갱신')
        self.canvas.update()

    def remove_component_visual(self, comp_id: int):
        mesh = self._component_visuals.pop(comp_id, None)
        if mesh is not None:
            mesh.parent = None
            self.canvas.update()
        if hasattr(self, '_comp_orig_face_colors'):
            self._comp_orig_face_colors.pop(comp_id, None)

    # ── 실(Room) 비주얼 관리 (2026-05-24 2단계) ──────────────
    # 실은 부재와 별개의 반투명 평면 색면. depth_test 끄지 않고 translucent
    # gl_state 로 부재 위에 비치게 그린다.

    def add_room_visual(self, room_id: int, vertices, faces, face_colors):
        """실 폴리곤 반투명 메시 추가(같은 id 가 있으면 먼저 제거)."""
        if not hasattr(self, '_room_visuals'):
            self._room_visuals = {}
        self.remove_room_visual(room_id)
        mesh = visuals.Mesh(
            vertices=vertices, faces=faces,
            face_colors=face_colors, shading=None,
            parent=self.view.scene,
        )
        mesh.set_gl_state('translucent', depth_test=False, cull_face=False)
        self._room_visuals[room_id] = mesh
        self.canvas.update()

    def remove_room_visual(self, room_id: int):
        if not hasattr(self, '_room_visuals'):
            self._room_visuals = {}
            return
        mesh = self._room_visuals.pop(room_id, None)
        if mesh is not None:
            mesh.parent = None
            self.canvas.update()

    def set_rooms_visible(self, visible: bool):
        """모든 실 색면 메시의 표시/숨김 토글.

        [정책 2026-05-28] 접합부설계·구조해석·물량 탭은 와이어프레임만 의미가
        있어 실 색면이 공중에 떠 보이면 혼란스럽다. 그 탭 진입 시 숨기고,
        배치설계·정의 탭(실 편집)에서 다시 표시. 메시는 유지(재생성 불필요).
        """
        if not hasattr(self, '_room_visuals'):
            self._room_visuals = {}
            return
        for mesh in self._room_visuals.values():
            if mesh is not None:
                mesh.visible = bool(visible)
        self.canvas.update()

    # ── 개구부 고스트 (2026-05-24 3단계) ─────────────────────
    # 개구부 배치/이동 미리보기 — 반투명 박스(부재 고스트와 별개).

    def update_opening_ghost(self, vertices, faces, face_colors):
        """개구부 고스트 박스 갱신(없으면 생성)."""
        if not hasattr(self, '_op_ghost_mesh') or self._op_ghost_mesh is None:
            self._op_ghost_mesh = visuals.Mesh(
                vertices=vertices, faces=faces, face_colors=face_colors,
                shading=None, parent=self.view.scene)
            self._op_ghost_mesh.set_gl_state(
                'translucent', depth_test=False, cull_face=False)
        else:
            self._op_ghost_mesh.set_data(
                vertices=vertices, faces=faces, face_colors=face_colors)
        self._op_ghost_mesh.visible = True
        self.canvas.update()

    def clear_opening_ghost(self):
        if getattr(self, '_op_ghost_mesh', None) is not None:
            self._op_ghost_mesh.visible = False
            self.canvas.update()

    # ── 실 고스트 (2026-05-24 3단계 — 실 이동/복사 미리보기) ──
    def update_room_ghost(self, vertices, faces, face_colors):
        if not hasattr(self, '_room_ghost_mesh') or self._room_ghost_mesh is None:
            self._room_ghost_mesh = visuals.Mesh(
                vertices=vertices, faces=faces, face_colors=face_colors,
                shading=None, parent=self.view.scene)
            self._room_ghost_mesh.set_gl_state(
                'translucent', depth_test=False, cull_face=False)
        else:
            self._room_ghost_mesh.set_data(
                vertices=vertices, faces=faces, face_colors=face_colors)
        self._room_ghost_mesh.visible = True
        self.canvas.update()

    def clear_room_ghost(self):
        if getattr(self, '_room_ghost_mesh', None) is not None:
            self._room_ghost_mesh.visible = False
            self.canvas.update()

    # ── 고스트 미리보기 ──────────────────────────────────────

    # ── 고스트 게이트 ───────────────────────────────────────
    # 좌측 3D 고스트 메쉬는 F5 PREVIEW(배치/이동) 활성 시에만 보여야 한다.
    # 그 외(F5 OFF, F6, F5 IDLE 등) 호출되는 update_ghost는 무시 — 잔존 고스트
    # 방지용 차단 게이트.

    def set_ghost_enabled(self, enabled: bool):
        """F5 PREVIEW 진입/이탈 시 controller가 호출."""
        self._ghost_enabled = bool(enabled)
        if not enabled:
            if hasattr(self, '_ghost_mesh'):
                self._ghost_mesh.visible = False
                self.canvas.update()

    def update_ghost(self, vertices, faces, face_colors):
        # 게이트 닫혀 있으면 데이터 갱신/visible 변경 모두 무시
        if not getattr(self, '_ghost_enabled', False):
            self._ghost_mesh.visible = False
            return
        self._ghost_mesh.set_data(
            vertices=vertices, faces=faces,
            face_colors=face_colors,
        )
        self._ghost_mesh.shading = None
        self._ghost_mesh.visible = True
        self.canvas.update()

    def clear_ghost(self):
        self._ghost_mesh.visible = False
        self.canvas.update()

    # ── 스냅 마커 ────────────────────────────────────────────

    def show_snap_markers(self, snap_point: np.ndarray, all_vertices: np.ndarray):
        """스냅 대상 부재의 모든 꼭지점을 노란 점으로, 스냅된 점은 크게 표시."""
        if len(all_vertices) == 0:
            self._snap_marker.visible = False
            return

        # Z를 약간 올려서 바닥 메쉬에 가려지지 않게
        raised = all_vertices.copy().astype(np.float32)
        raised[:, 2] += 50.0

        n = len(raised)
        sizes = np.full(n, 14, dtype=np.float32)
        face_colors = np.tile([1.0, 1.0, 0.0, 1.0], (n, 1)).astype(np.float32)

        # 스냅된 점 = 큰 주황 점으로 강조
        for i, v in enumerate(all_vertices):
            if np.linalg.norm(v - snap_point) < 1.0:
                sizes[i] = 22
                face_colors[i] = [1.0, 0.3, 0.0, 1.0]

        self._snap_marker.set_data(
            pos=raised,
            face_color=face_colors,
            edge_color=(1.0, 0.3, 0.0, 1.0),
            size=sizes,
        )
        self._snap_marker.visible = True
        self.canvas.update()

    def hide_snap_marker(self):
        self._snap_marker.visible = False
        self.canvas.update()

    # ── 선택 하이라이트 ─────────────────────────────────────

    def show_selection_box(self, comp_id: int, bbox_min: np.ndarray, bbox_max: np.ndarray):
        """[Phase 0 — 부재 호버 시스템] 컴포넌트 빨간 테두리 강조 비활성.

        예전엔 AABB 바운딩박스를 빨간 와이어프레임으로 표시했으나,
        마우스 좌표 전달 결함으로 어차피 잘 안 맞았고, 부재 단위 호버
        시스템(설계서_부재호버.md 참고)으로 대체된다. 시각 표시는
        끄되, `_selected_comp_id` 캐시 갱신은 보존해 컴포넌트 선택을
        키로 쓰는 이동/복사/삭제 흐름과의 호환성을 유지한다.
        """
        self._selected_comp_id = comp_id
        # 비주얼 표시 코드 제거 — _selection_box 는 항상 invisible.

    def hide_selection_box(self):
        """선택 하이라이트 숨기기 — 비주얼 자체가 항상 invisible 이라 캐시만 갱신."""
        self._selection_box.visible = False
        self._selected_comp_id = -1

    # ── 해석 부재 하이라이트 ─────────────────────────────────

    def highlight_member(self, model, mid: int):
        """해석 부재(mid)를 두꺼운 노란 선으로 하이라이트."""
        m = model.members.get(mid)
        if m is None:
            self.clear_member_highlight()
            return
        n1 = model.nodes.get(m.n1)
        n2 = model.nodes.get(m.n2)
        if n1 is None or n2 is None:
            self.clear_member_highlight()
            return
        pos = np.array([n1.coord, n2.coord], dtype=np.float32)
        self._member_highlight.set_data(
            pos=pos, color=(0.10, 0.40, 1.00, 1.0),   # 파란색 강조 (사용자 결정 C5)
            connect='strip', width=6.0,
        )
        self._member_highlight.visible = True
        self.canvas.update()

    def clear_member_highlight(self):
        """해석 부재 하이라이트 숨김."""
        self._member_highlight.visible = False
        self.canvas.update()

    # ── Stage 1: 슬래브 판정 + 분배 패턴 ────────────────────
    @staticmethod
    def _pattern_factor(pattern: str, s: float,
                        short_len: float, long_len: float, beam_L: float) -> float:
        """프로파일 높이 비율 (peak 기준 0~1). s ∈ [0, 1] 은 보 축 파라미터."""
        if pattern == 'uniform':
            return 1.0
        if pattern == 'triangle':
            # 이등변 삼각형: 양 끝 0, 중앙 1
            return 1.0 - abs(2.0 * s - 1.0)
        if pattern == 'trapezoid':
            # 장변 보 위 사다리꼴: 양쪽 short_len/2 만큼 경사, 가운데 평평
            if beam_L < 1e-9:
                return 0.0
            slope_frac = (short_len / 2.0) / beam_L
            if slope_frac <= 0.0:
                return 1.0
            if s < slope_frac:
                return s / slope_frac
            if s > 1.0 - slope_frac:
                return (1.0 - s) / slope_frac
            return 1.0
        return 0.0

    def show_slab_overlay(self, model, load_result) -> float:
        """Stage 1: 슬래브 면 + 판정 라벨 + 분배 패턴 프로파일 + peak 수치.

        Returns: max peak UDL [kN/m] — UI 로그용.
        """
        if model is None or load_result is None:
            self.hide_slab_overlay()
            return 0.0
        slabs = getattr(load_result, 'slab_distributions', None) or []
        if not slabs:
            self.hide_slab_overlay()
            return 0.0

        # 색상 (알파 포함, translucent 블렌딩)
        COLOR_ONEWAY = np.array([0.70, 0.85, 1.00, 0.28], dtype=np.float32)
        COLOR_TWOWAY = np.array([0.70, 1.00, 0.75, 0.28], dtype=np.float32)
        COLOR_CANT   = np.array([1.00, 0.88, 0.60, 0.28], dtype=np.float32)

        # peak 정규화 기준
        peak_max = 0.0
        for sd in slabs:
            for bs in sd.beam_shares.values():
                if bs.peak_w_factored > peak_max:
                    peak_max = bs.peak_w_factored
        if peak_max < 1e-9:
            self.hide_slab_overlay()
            return 0.0
        OFFSET_MAX_MM = 400.0

        verts = []
        faces = []
        face_colors = []

        labels = []
        label_pos = []

        profile_segments = []

        peak_texts = []
        peak_text_pos = []

        for sd in slabs:
            corners = sd.corners
            if len(corners) != 4:
                continue   # 비정상 — Mesh 생략

            base_i = len(verts)
            for c in corners:
                verts.append([float(c[0]), float(c[1]), float(c[2])])
            faces.append([base_i, base_i + 1, base_i + 2])
            faces.append([base_i, base_i + 2, base_i + 3])

            if sd.slab_kind == 'cantilever_slab':
                col = COLOR_CANT
                lbl = "Cantilever"
            elif sd.one_way:
                col = COLOR_ONEWAY
                ratio = sd.long_len / max(sd.short_len, 1.0)
                lbl = f"1-way (b/a={ratio:.2f})"
            else:
                col = COLOR_TWOWAY
                ratio = sd.long_len / max(sd.short_len, 1.0)
                lbl = f"2-way (b/a={ratio:.2f})"
            face_colors.append(col)
            face_colors.append(col)

            c_arr = np.array([[float(c[0]), float(c[1]), float(c[2])] for c in corners],
                             dtype=np.float64)
            center = c_arr.mean(axis=0)
            labels.append(lbl)
            label_pos.append([center[0], center[1], center[2] + 40.0])

            # 각 보의 분배 패턴 polyline
            for mid, bs in sd.beam_shares.items():
                if mid not in model.members:
                    continue
                if bs.peak_w_factored < 1e-9:
                    continue   # 1-way 슬래브의 단변 보 (분담 0)
                m = model.members[mid]
                n1 = np.array(model.nodes[m.n1].coord, dtype=np.float64)
                n2 = np.array(model.nodes[m.n2].coord, dtype=np.float64)
                beam_L = float(np.linalg.norm(n2 - n1))
                if beam_L < 1e-9:
                    continue
                beam_mid = 0.5 * (n1 + n2)
                # 슬래브 안쪽 방향 — 수평 성분만
                to_center = center - beam_mid
                to_center[2] = 0.0
                tc_norm = np.linalg.norm(to_center)
                if tc_norm < 1e-9:
                    continue
                inside = to_center / tc_norm

                peak_h = OFFSET_MAX_MM * (bs.peak_w_factored / peak_max)
                Nsamp = 25
                prev = None
                for k in range(Nsamp):
                    s = k / (Nsamp - 1)
                    f = self._pattern_factor(bs.pattern, s, sd.short_len, sd.long_len, beam_L)
                    base_pt = n1 + (n2 - n1) * s
                    pt = base_pt + inside * (peak_h * f)
                    if prev is not None:
                        profile_segments.append(prev.astype(np.float32))
                        profile_segments.append(pt.astype(np.float32))
                    prev = pt

                # 중앙에 peak 수치 (offset 위치)
                mid_pt = 0.5 * (n1 + n2) + inside * peak_h
                peak_texts.append(f"peak={bs.peak_w_factored:.2f} kN/m")
                peak_text_pos.append([float(mid_pt[0]), float(mid_pt[1]),
                                      float(mid_pt[2]) + 30.0])

        if not verts:
            self.hide_slab_overlay()
            return 0.0

        V = np.array(verts, dtype=np.float32)
        F = np.array(faces, dtype=np.uint32)
        FC = np.array(face_colors, dtype=np.float32)
        self._slab_mesh.set_data(vertices=V, faces=F, face_colors=FC)
        self._slab_mesh.visible = True

        if profile_segments:
            self._slab_profile_line.set_data(
                pos=np.array(profile_segments, dtype=np.float32),
                connect='segments', width=2.5,
                color=(0.12, 0.30, 0.78, 1.0),
            )
            self._slab_profile_line.visible = True
        else:
            self._slab_profile_line.visible = False

        if labels:
            self._slab_label_text.text = labels
            self._slab_label_text.pos = np.array(label_pos, dtype=np.float32)
            self._slab_label_text.visible = True
        else:
            self._slab_label_text.visible = False

        if peak_texts:
            self._slab_peak_text.text = peak_texts
            self._slab_peak_text.pos = np.array(peak_text_pos, dtype=np.float32)
            self._slab_peak_text.visible = True
        else:
            self._slab_peak_text.visible = False

        self.canvas.update()
        return peak_max   # kN/m (= N/mm)

    def hide_slab_overlay(self):
        self._slab_mesh.visible = False
        self._slab_profile_line.visible = False
        self._slab_label_text.visible = False
        self._slab_peak_text.visible = False
        self.canvas.update()

    def is_slab_overlay_visible(self) -> bool:
        return bool(self._slab_mesh.visible)

    # ── Stage 2: 보 UDL 프로파일 ────────────────────────────
    def show_beam_udl_overlay(self, model, load_result) -> float:
        """각 보(beam) 부재 아래에 factored UDL 프로파일(직사각형) + 수치 표시.

        factored = 1.2 D + 1.6 L (N/mm). 1 N/mm = 1 kN/m 이므로 그대로 kN/m 로 표기.
        반환값: max factored UDL [kN/m] — UI 로그용.
        """
        if not model or not model.members or load_result is None:
            self.hide_beam_udl_overlay()
            return 0.0

        beam_rows = []
        for mid, m in model.members.items():
            if m.kind != 'beam':
                continue
            lc = load_result.member_loads.get(mid)
            if lc is None:
                continue
            w = lc.factored   # N/mm = kN/m
            beam_rows.append((mid, m, w))

        if not beam_rows:
            self.hide_beam_udl_overlay()
            return 0.0

        w_max = max((w for _, _, w in beam_rows), default=0.0)
        if w_max < 1e-9:
            self.hide_beam_udl_overlay()
            return 0.0

        # 최대 UDL 을 400 mm 길이로 매핑 (모델 크기 무관 상수)
        SCALE_MM = 400.0

        line_pts = []
        texts = []
        text_pos = []
        for mid, m, w in beam_rows:
            n1 = model.nodes[m.n1].coord.astype(np.float32)
            n2 = model.nodes[m.n2].coord.astype(np.float32)
            h = float(SCALE_MM * (w / w_max))
            down = np.array([0.0, 0.0, -h], dtype=np.float32)
            q1 = n1 + down
            q2 = n2 + down
            # 사각형 3변 (좌·우·하) — 상변은 부재 자체와 겹침
            line_pts.extend([n1, q1])
            line_pts.extend([n2, q2])
            line_pts.extend([q1, q2])
            # 수치 위치: 프로파일 아래 중앙
            mid_pt = 0.5 * (q1 + q2)
            texts.append(f"{w:.2f} kN/m")
            text_pos.append([float(mid_pt[0]),
                             float(mid_pt[1]),
                             float(mid_pt[2]) - 40.0])

        self._beam_udl_line.set_data(
            pos=np.array(line_pts, dtype=np.float32),
            connect='segments', width=2.0,
            color=(0.0, 0.55, 0.22, 1.0),
        )
        self._beam_udl_line.visible = True
        self._beam_udl_text.text = texts
        self._beam_udl_text.pos = np.array(text_pos, dtype=np.float32)
        self._beam_udl_text.visible = True
        self.canvas.update()
        return w_max   # kN/m

    def hide_beam_udl_overlay(self):
        self._beam_udl_line.visible = False
        self._beam_udl_text.visible = False
        self.canvas.update()

    def is_beam_udl_overlay_visible(self) -> bool:
        return bool(self._beam_udl_line.visible)

    # ── Stage 3: 기둥 상단 축력 ─────────────────────────────
    def show_column_head_overlay(self, model, store) -> float:
        """각 기둥(column) 부재의 상단 노드에 해당 기둥이 받는 축력 N 을 텍스트로 표시.

        부호 규약: N > 0 인장, N < 0 압축 (MemberForces 규약).
        반환값: |N| 최댓값 [kN] — UI 로그용.
        """
        if not store.member_forces or not model.members:
            self.hide_column_head_overlay()
            return 0.0

        texts = []
        positions = []
        n_max_abs = 0.0
        for mid, m in model.members.items():
            if m.kind != 'column':
                continue
            mf = store.member_forces.get(mid)
            if mf is None:
                continue
            n1 = model.nodes.get(m.n1)
            n2 = model.nodes.get(m.n2)
            if n1 is None or n2 is None:
                continue
            # 상단 노드 = z 큰 쪽. i-end/j-end 축력을 골라 표시.
            if n1.coord[2] >= n2.coord[2]:
                top_coord = n1.coord
                n_val = mf.N_i
            else:
                top_coord = n2.coord
                n_val = mf.N_j
            texts.append(f"{n_val / 1000.0:+.1f}")
            # 텍스트 위치를 노드 위로 살짝 띄움
            positions.append([float(top_coord[0]) + 30.0,
                              float(top_coord[1]) + 30.0,
                              float(top_coord[2]) + 30.0])
            if abs(n_val) > n_max_abs:
                n_max_abs = abs(n_val)

        if not texts:
            self.hide_column_head_overlay()
            return 0.0

        self._col_head_text.text = texts
        self._col_head_text.pos = np.array(positions, dtype=np.float32)
        self._col_head_text.visible = True
        self.canvas.update()
        return n_max_abs / 1000.0

    def hide_column_head_overlay(self):
        self._col_head_text.visible = False
        self.canvas.update()

    def is_column_head_overlay_visible(self) -> bool:
        return bool(self._col_head_text.visible)

    # ── Stage 4: 베이스 반력 ────────────────────────────────
    def show_reaction_overlay(self, model, store) -> float:
        """각 베이스 노드 위에 수직 반력 Rz 화살표 + 수치 표시.

        반환값: Rz 최대값 [kN] — UI 로그용.
        """
        if not store.base_reactions:
            self.hide_reaction_overlay()
            return 0.0

        rz_values = {nid: reac[2] for nid, reac in store.base_reactions.items()}
        rz_abs_max = max((abs(v) for v in rz_values.values()), default=0.0)
        if rz_abs_max < 1e-6:
            self.hide_reaction_overlay()
            return 0.0

        # 화살표 최대 길이: 1500 mm (모델 크기와 무관하게 상수)
        ARROW_LEN_MM = 1500.0

        line_pts = []
        texts = []
        text_pos = []
        for nid, rz in rz_values.items():
            node = model.nodes.get(nid)
            if node is None:
                continue
            p0 = node.coord
            length = ARROW_LEN_MM * (abs(rz) / rz_abs_max)
            sign = 1.0 if rz >= 0 else -1.0   # 양수 → 위로
            p1 = np.array([p0[0], p0[1], p0[2] + length * sign], dtype=np.float32)
            line_pts.append(p0.astype(np.float32))
            line_pts.append(p1)
            # 화살표 머리 — V 자 2 선
            head = length * 0.12
            p1a = p1 + np.array([head, 0.0, -head * sign], dtype=np.float32)
            p1b = p1 + np.array([-head, 0.0, -head * sign], dtype=np.float32)
            line_pts.append(p1); line_pts.append(p1a)
            line_pts.append(p1); line_pts.append(p1b)
            texts.append(f"Rz={rz / 1000.0:+.1f} kN")
            text_pos.append([float(p1[0]) + 40.0,
                             float(p1[1]) + 40.0,
                             float(p1[2])])

        if not line_pts:
            self.hide_reaction_overlay()
            return 0.0

        self._reaction_line.set_data(
            pos=np.array(line_pts, dtype=np.float32),
            connect='segments', width=3.0,
            color=(0.84, 0.16, 0.16, 1.0),
        )
        self._reaction_line.visible = True
        self._reaction_text.text = texts
        self._reaction_text.pos = np.array(text_pos, dtype=np.float32)
        self._reaction_text.visible = True
        self.canvas.update()
        return rz_abs_max / 1000.0

    def hide_reaction_overlay(self):
        self._reaction_line.visible = False
        self._reaction_text.visible = False
        self.canvas.update()

    def is_reaction_overlay_visible(self) -> bool:
        return bool(self._reaction_line.visible)

    # ── 접합부 시각화 ────────────────────────────────────────

    def show_joint_overlay(self, model) -> int:
        """해석에서 실제 사용하는 beam_line 데이터로 접합부 시각화.

        build_beam_lines(model, include_columns=True) 결과만 사용:
        - pins[i]=True  → 핀접합 (초록 선, 라인 직교 방향)
        - pins[i]=False (내부 노드, 양끝 제외) → 강접합 (빨강 점)
        보(X/Y)와 기둥(Z) 모두 동일한 경로로 처리. 특수 케이스 없음.

        Returns: 접합부 총 개수.
        """
        from modular_3d.analysis.beam_line_builder import build_beam_lines

        marker_pos = []
        marker_colors = []
        line_verts = []
        line_connect = []
        pin_line_verts = []
        pin_line_connect = []
        texts = []
        text_pos = []

        COLOR_RIGID = (0.90, 0.20, 0.20, 1.0)
        COLOR_PIN   = (0.10, 0.75, 0.30, 1.0)
        PIN_HALF_LEN = 300.0

        n_joints = 0
        beam_lines = build_beam_lines(model, include_columns=True)

        for bli in beam_lines:
            n_nodes = len(bli.node_coords)
            pins = bli.beam_line.pins if bli.beam_line.pins else [False] * n_nodes

            # 라인 연결선 (회색)
            for i in range(n_nodes):
                coord = bli.node_coords[i]
                vi = len(line_verts)
                line_verts.append(coord.tolist())
                if i > 0:
                    line_connect.append([vi - 1, vi])

            # 라인 방향 벡터 → 직교 방향 (핀 선 방향)
            p0 = bli.node_coords[0]
            p1 = bli.node_coords[-1]
            beam_dir = p1 - p0
            beam_dir_norm = float(np.linalg.norm(beam_dir))
            if beam_dir_norm < 1.0:
                continue
            beam_u = beam_dir / beam_dir_norm
            perp = np.cross(beam_u, np.array([0.0, 0.0, 1.0]))
            if np.linalg.norm(perp) < 0.1:
                perp = np.cross(beam_u, np.array([1.0, 0.0, 0.0]))
            perp = perp / np.linalg.norm(perp)

            # Z방향(기둥)은 십자 표시 (X+Y 두 방향 선)
            is_vertical = (bli.direction == 'Z')
            if is_vertical:
                perp2 = np.array([0.0, 1.0, 0.0])
                perp = np.array([1.0, 0.0, 0.0])

            # 각 내부 노드 처리 (양 끝 제외 — 끝점은 접합부가 아님)
            for i in range(1, n_nodes - 1):
                coord = bli.node_coords[i]
                is_pin = pins[i] if i < len(pins) else False

                if is_pin:
                    # 핀접합: 직교 방향 초록 선
                    vi = len(pin_line_verts)
                    pin_line_verts.append((coord - perp * PIN_HALF_LEN).tolist())
                    pin_line_verts.append((coord + perp * PIN_HALF_LEN).tolist())
                    pin_line_connect.append([vi, vi + 1])
                    if is_vertical:
                        vi = len(pin_line_verts)
                        pin_line_verts.append((coord - perp2 * PIN_HALF_LEN).tolist())
                        pin_line_verts.append((coord + perp2 * PIN_HALF_LEN).tolist())
                        pin_line_connect.append([vi, vi + 1])
                    texts.append('핀')
                    text_pos.append((coord + np.array([30, 30, 50])).tolist())
                    n_joints += 1
                else:
                    # 강접합: 빨강 점
                    marker_pos.append(coord.tolist())
                    marker_colors.append(COLOR_RIGID)
                    n_joints += 1

        # ── 렌더 ──
        if marker_pos:
            pos = np.array(marker_pos, dtype=np.float32)
            colors = np.array(marker_colors, dtype=np.float32)
            self._joint_markers.set_data(
                pos, face_color=colors, edge_color=colors,
                size=14, edge_width=0,
            )
            self._joint_markers.visible = True

        if line_verts and line_connect:
            verts = np.array(line_verts, dtype=np.float32)
            connect = np.array(line_connect, dtype=np.uint32)
            self._joint_lines.set_data(
                pos=verts, connect=connect,
                color=(0.5, 0.5, 0.5, 0.6), width=2,
            )
            self._joint_lines.visible = True

        if pin_line_verts and pin_line_connect:
            verts = np.array(pin_line_verts, dtype=np.float32)
            connect = np.array(pin_line_connect, dtype=np.uint32)
            self._joint_pin_lines.set_data(
                pos=verts, connect=connect,
                color=COLOR_PIN, width=6.0,
            )
            self._joint_pin_lines.visible = True

        if texts:
            self._joint_text.text = texts
            self._joint_text.pos = np.array(text_pos, dtype=np.float32)
            self._joint_text.visible = True

        self.canvas.update()
        return n_joints

    def hide_joint_overlay(self):
        self._joint_markers.visible = False
        self._joint_lines.visible = False
        self._joint_pin_lines.visible = False
        self._joint_text.visible = False
        self.canvas.update()

    def is_joint_overlay_visible(self) -> bool:
        return bool(self._joint_markers.visible)

    # ── 레이캐스팅 ───────────────────────────────────────────

    def screen_to_world_ray(self, qt_pos) -> Tuple[np.ndarray, np.ndarray]:
        """
        Qt 화면 좌표 (Y=0이 위, 아래로 증가) → (ray_origin, ray_direction).

        [버그 패치 — 사용자 보고 #1]
        Qt 좌표는 y-down이고 NDC는 y-up이므로 ndc_y는 부호 반전 필수.
        기존: ndc_y = (2*y/h) - 1 → 반전 누락 → 화면 위쪽 클릭 시 ray가
        아래쪽으로 가서 z 반전 효과 발생(1층 아래 클릭 → 2층 부재 잡힘).
        """
        cam = self.view.camera
        w, h = self.canvas.size

        ndc_x = (2.0 * qt_pos[0] / w) - 1.0
        # Qt y-down → NDC y-up 반전
        ndc_y = 1.0 - (2.0 * qt_pos[1] / h)

        fov_rad = np.radians(cam.fov)
        aspect = w / h
        half_h = np.tan(fov_rad / 2.0)
        half_w = half_h * aspect

        cam_pos, forward, right, up = _cam_axes(cam)

        ray_dir = forward + (ndc_x * half_w) * right + (ndc_y * half_h) * up
        ray_dir = ray_dir / np.linalg.norm(ray_dir)

        return cam_pos, ray_dir


    def get_scene_transform(self):
        """vispy 씬→캔버스 변환 객체 반환 (3D world → 2D canvas pixels)."""
        # view.scene의 자식 노드를 통해야 카메라 투영이 포함됨
        return self._snap_marker.transforms.get_transform('visual', 'canvas')

    # ── 부재 단위 화면거리 픽킹 (Phase 1, 설계서_부재호버.md) ──────
    def pick_ops_member_at(self, qt_pos, ops_model, am, threshold: float = 8.0):
        """F6 ops 뷰에서 Qt 마우스 좌표에 가장 가까운 부재 mid 를 반환.

        - vispy scene transform 으로 부재 양 끝점을 화면(픽셀) 좌표로 사영
        - perspective divide 까지 적용 (사용자 메모: _cam_axes 수동 투영은
          z 높이에서 오차 큼 — scene transform 사용 필수)
        - 카메라 뒤(w<=0) 부재는 제외
        - 점-선분 2D 거리가 임계값(기본 8 px) 이내인 부재 중 최소거리 반환
        - 조건 만족 부재 없으면 None

        threshold 단위는 픽셀.
        """
        if am is None or not am.members:
            return None
        if not getattr(self, '_ops_initialized', False):
            return None
        if ops_model is None or not ops_model.node_tags:
            return None
        tr = self.get_scene_transform()
        if tr is None:
            return None

        # 부재 양 끝 3D 좌표를 (2N, 3) 으로 모으기. ops_model.node_tags 는
        # ops 노드 id → 3D 좌표 dict 이며, am 의 노드 id 와 동일.
        # 사용자 보고 — 부재 일관성: am.members 에 마스터+sub 가 모두 들어
        # 있어 같은 직선에서 마스터와 sub 둘 다 픽킹되던 문제.  sub 부재
        # (is_split_sub=True) 는 픽킹 후보에서 제외해 마스터만 잡히도록 통일.
        mids = []
        coords = []
        for mid, m in am.members.items():
            if getattr(m, 'is_split_sub', False):
                continue
            c1 = ops_model.node_tags.get(m.n1)
            c2 = ops_model.node_tags.get(m.n2)
            if c1 is None or c2 is None:
                continue
            mids.append(mid)
            coords.append(c1); coords.append(c2)
        if not mids:
            return None
        pts = np.asarray(coords, dtype=np.float32)
        mapped = tr.map(pts)   # (2N, 4)
        # perspective divide
        w = mapped[:, 3:4]
        safe_w = np.where(np.abs(w) < 1e-12, 1.0, w)
        xy = (mapped[:, :2] / safe_w).astype(np.float32)
        p1 = xy[0::2]; p2 = xy[1::2]

        # 점-선분 거리 (벡터화)
        mxy = np.array([qt_pos[0], qt_pos[1]], dtype=np.float32)
        ab = p2 - p1
        ab_len2 = (ab * ab).sum(axis=1)
        ap = mxy - p1
        denom = np.where(ab_len2 < 1e-9, 1.0, ab_len2)
        t = np.clip((ap * ab).sum(axis=1) / denom, 0.0, 1.0)
        proj = p1 + t[:, None] * ab
        d = np.linalg.norm(mxy - proj, axis=1)

        # 카메라 뒤 부재 제외 (양 끝 어느 한쪽이라도 w<=0)
        w_flat = w.squeeze(-1)
        behind = (w_flat[0::2] <= 0.0) | (w_flat[1::2] <= 0.0)
        d = np.where(behind, np.inf, d)

        idx = int(np.argmin(d))
        if d[idx] > threshold:
            return None
        return mids[idx]

    def pick_ops_joint_at(self, qt_pos, ops_model, am, threshold: float = 12.0):
        """F6 ops 뷰에서 마우스에 가장 가까운 컴포넌트 간 접합을 선택.

        접합 = 두 노드(master, slave) 사이. 두 노드를 잇는 선분에 대한 점-선분
        2D 거리가 최소인 접합을 반환한다. pick_ops_member_at 과 동일하게
        vispy scene transform + perspective divide 로 화면 좌표를 구한다.

        후보는 spec.equal_dofs 중 **컴포넌트 간** 결합만. 같은 컴포넌트 내부
        보조 결합(코어 사영점 등, 두 끝 source_comp_id 가 같고 0 이 아님)은 제외.

        반환(없으면 None): 선택 접합 정보 dict —
          master/slave(노드 tag), a_xy/b_xy(평면 좌표), a_z/b_z,
          a_comp/b_comp(source_comp_id), dofs(현재 자유도), is_rigid, rule_id.
        threshold 단위는 픽셀.
        """
        if ops_model is None or getattr(ops_model, 'spec', None) is None:
            return None
        if not getattr(self, '_ops_initialized', False):
            return None
        spec = ops_model.spec
        if not spec.equal_dofs or not ops_model.node_tags:
            return None
        tr = self.get_scene_transform()
        if tr is None:
            return None

        def _comp(tag):
            nr = spec.node(tag)
            if nr is not None:
                return int(getattr(nr, 'source_comp_id', 0) or 0)
            an = am.nodes.get(tag) if am is not None else None
            return int(getattr(an, 'source_comp_id', 0) or 0) if an is not None else 0

        def _is_n1(tag):
            """가교 중간노드(N1, role='panel_z_route')인가 — 직각(ㄴ자)접합 식별."""
            nr = spec.node(tag)
            return nr is not None and getattr(nr, 'role', '') == 'panel_z_route'

        recs = []
        coords = []
        hidden = getattr(self, '_hidden_rule_ids', set())
        for ed in spec.equal_dofs:
            c1 = ops_model.node_tags.get(ed.master)
            c2 = ops_model.node_tags.get(ed.slave)
            if c1 is None or c2 is None:
                continue
            # 시각화로 끈 종류(rule_id)는 선택 대상에서 제외 — 같은 위치에 겹친
            # 수직 접합 중 보이는 것만 고르도록.
            if getattr(ed, 'rule_id', '') in hidden:
                continue
            ca = _comp(ed.master)
            cb = _comp(ed.slave)
            # N1(panel_z_route) 가교가 끼는 직각접합 수평결합은 두 끝이 같은
            # 모듈 컴포넌트(N1 소속=모듈 본체)라도 편집 대상 — 같은-컴포넌트
            # 필터를 면제한다. (R03 자동 직각접합 수평결합 선택 불가 원인.)
            is_bridge = _is_n1(ed.master) or _is_n1(ed.slave)
            # 같은 컴포넌트 내부 보조 결합 제외 — 컴포넌트 간 접합만 편집 대상.
            if (not is_bridge) and ca != 0 and cb != 0 and ca == cb:
                continue
            recs.append((ed, c1, c2, ca, cb))
            coords.append(c1)
            coords.append(c2)
        if not recs:
            return None

        pts = np.asarray(coords, dtype=np.float32)
        mapped = tr.map(pts)
        w = mapped[:, 3:4]
        safe_w = np.where(np.abs(w) < 1e-12, 1.0, w)
        xy = (mapped[:, :2] / safe_w).astype(np.float32)
        p1 = xy[0::2]
        p2 = xy[1::2]

        mxy = np.array([qt_pos[0], qt_pos[1]], dtype=np.float32)
        ab = p2 - p1
        ab_len2 = (ab * ab).sum(axis=1)
        ap = mxy - p1
        denom = np.where(ab_len2 < 1e-9, 1.0, ab_len2)
        t = np.clip((ap * ab).sum(axis=1) / denom, 0.0, 1.0)
        proj = p1 + t[:, None] * ab
        d = np.linalg.norm(mxy - proj, axis=1)

        w_flat = w.squeeze(-1)
        behind = (w_flat[0::2] <= 0.0) | (w_flat[1::2] <= 0.0)
        d = np.where(behind, np.inf, d)

        idx = int(np.argmin(d))
        if d[idx] > threshold:
            return None
        ed, c1, c2, ca, cb = recs[idx]
        dofs = tuple(ed.dofs)
        is_rigid = any(x in (4, 5, 6) for x in dofs)
        # 직각접합(USER_ADD + 중간 노드 N1=panel_z_route) 식별 — 선택·제거를
        # 직각접합 단위(N1 + 두 결합)로 다루기 위해 N1 좌표를 함께 돌려준다.
        rid = getattr(ed, 'rule_id', '')
        n1_tag = None
        for nt in (ed.master, ed.slave):
            nr = spec.node(nt)
            if nr is not None and getattr(nr, 'role', '') == 'panel_z_route':
                n1_tag = nt
                break
        # 직각(ㄴ자)접합 식별 — rule_id 무관, N1 가교 노드가 끼면 직각접합.
        # 사용자추가(R10/R11) + 자동(R03) 모두 포함.
        is_ra = (n1_tag is not None)
        n1c = ops_model.node_tags.get(n1_tag) if n1_tag is not None else None
        return {
            'master': int(ed.master), 'slave': int(ed.slave),
            'a_xy': (float(c1[0]), float(c1[1])),
            'b_xy': (float(c2[0]), float(c2[1])),
            'a_z': float(c1[2]), 'b_z': float(c2[2]),
            'a_comp': ca, 'b_comp': cb,
            'dofs': dofs, 'is_rigid': is_rigid,
            'rule_id': rid,
            'right_angle': bool(is_ra),
            'n1_tag': int(n1_tag) if n1_tag is not None else None,
            'n1_xy': ((float(n1c[0]), float(n1c[1]))
                      if n1c is not None else None),
            'n1_z': float(n1c[2]) if n1c is not None else None,
        }

    def highlight_ops_joint_chain(self, ops_model, node_tags,
                                  color=(1.0, 0.85, 0.0, 1.0), width=6.0):
        """여러 노드를 잇는 접합 체인 강조(직각접합 위-N1-아래 폴리라인)."""
        coords = []
        for t in node_tags:
            c = ops_model.node_tags.get(t)
            if c is not None:
                coords.append(c)
        if len(coords) < 2:
            self.clear_ops_joint_highlight()
            return
        self._ensure_joint_highlight_visuals()
        pos = np.array(coords, dtype=np.float32)
        self._joint_hl_line.set_data(pos=pos, color=color,
                                     connect='strip', width=width)
        self._joint_hl_line.visible = True
        self._joint_hl_pts.set_data(pos=pos, face_color=color, size=13)
        self._joint_hl_pts.visible = True
        self.canvas.update()

    # ── OpenSees 모델 시각화 ────────────────────────────────
    # F6 후 좌측 메쉬 뷰 → 노드/엘리먼트 뷰로 전환.
    # 노드: 빨간 점, 일반 부재: 검정선, 인터페이스 핀 링크: 노란선,
    # RC 코어: 짙은 보라, 베이스 지점: 검정 ▼ 마커, 슬래브/패널: 반투명 회색

    def _ensure_ops_visuals(self):
        """OpenSees 시각화 visual들 lazy 생성."""
        if getattr(self, '_ops_initialized', False):
            return
        self._ops_node_markers = visuals.Markers(parent=self.view.scene)
        self._ops_node_markers.visible = False
        self._ops_node_markers.order = 20
        self._ops_node_markers.set_gl_state('translucent', depth_test=False)

        self._ops_member_lines = visuals.Line(parent=self.view.scene)
        self._ops_member_lines.visible = False
        self._ops_member_lines.order = 19
        self._ops_member_lines.set_gl_state('translucent', depth_test=False)

        self._ops_iface_lines = visuals.Line(parent=self.view.scene)
        self._ops_iface_lines.visible = False
        self._ops_iface_lines.order = 19
        self._ops_iface_lines.set_gl_state('translucent', depth_test=False)

        self._ops_core_lines = visuals.Line(parent=self.view.scene)
        self._ops_core_lines.visible = False
        self._ops_core_lines.order = 19
        self._ops_core_lines.set_gl_state('translucent', depth_test=False)

        # ShellMITC4 요소 외곽선 (코어벽 / 코어 슬래브 / 일반 슬래브 shell).
        # 4 노드 quad 의 4 변을 모두 그려 사용자에게 면 요소 존재를 시각화.
        self._ops_shell_lines = visuals.Line(parent=self.view.scene)
        self._ops_shell_lines.visible = False
        self._ops_shell_lines.order = 18
        self._ops_shell_lines.set_gl_state('translucent', depth_test=False)

        # (Phase 8) 강체 다이어프램 마스터 ↔ 슬레이브 연결선 (회색 점선).
        # 토글 ON 시에만 표시. master 가 평균 위치라 슬레이브가 많아 시각 노이즈
        # 발생 가능 → 기본 OFF.
        self._ops_diaphragm_lines = visuals.Line(parent=self.view.scene)
        self._ops_diaphragm_lines.visible = False
        self._ops_diaphragm_lines.order = 17
        self._ops_diaphragm_lines.set_gl_state('translucent', depth_test=False)

        # [2026-05-17] 다이어프램 영역 반투명 면(폴리곤 fan-triangulation).
        # 각 컴포넌트의 다이어프램 다각형을 master(centroid) 기준 부채꼴로 채움.
        self._ops_diaphragm_mesh = visuals.Mesh(parent=self.view.scene)
        self._ops_diaphragm_mesh.visible = False
        self._ops_diaphragm_mesh.order = 16
        self._ops_diaphragm_mesh.set_gl_state('translucent', depth_test=False,
                                              cull_face=False, blend=True)

        # (Phase 8) 강체 링크 (진한 회색 실선) — 토글 ON 시 표시.
        self._ops_rigidlink_lines = visuals.Line(parent=self.view.scene)
        self._ops_rigidlink_lines.visible = False
        self._ops_rigidlink_lines.order = 17
        self._ops_rigidlink_lines.set_gl_state('translucent', depth_test=False)

        self._ops_support_markers = visuals.Markers(parent=self.view.scene)
        self._ops_support_markers.visible = False
        self._ops_support_markers.order = 21
        self._ops_support_markers.set_gl_state('translucent', depth_test=False)

        # 원본 face_colors 백업 dict 는 add_component_visual 에서 채움 (영구 보관)
        if not hasattr(self, '_comp_orig_face_colors'):
            self._comp_orig_face_colors: Dict[int, np.ndarray] = {}
        self._ops_view_active: bool = False
        # (Phase 8) 토글 상태 — 기본 OFF.
        self._show_diaphragms: bool = False
        self._show_rigid_links: bool = False
        # (Phase 9) DOF 묶음 색 분리 토글 — OFF 면 핀/6 DOF 단순 색.
        self._dof_color_mode: bool = False
        # [2026-05-17] 규칙 ID 별 시각화 토글 — 이 집합에 든 rule_id 의 결합선은
        # 시각화에서 제외. 실제 결합(spec/ops) 은 그대로 유지(단순 시각 토글).
        self._hidden_rule_ids: set = set()
        # spec 보관 (토글 시 재그리기용)
        self._last_spec = None
        self._last_om = None
        self._ops_initialized = True

    # ── (Phase 8/9) 토글 메소드 ─────────────────────────────────

    def set_show_diaphragms(self, on: bool):
        """강체 다이어프램 마스터↔슬레이브 연결선 표시 토글."""
        self._show_diaphragms = bool(on)
        if self._ops_view_active and self._last_spec is not None:
            self._draw_from_spec(self._last_spec, self._last_om)

    def set_show_rigid_links(self, on: bool):
        """강체 링크 표시 토글."""
        self._show_rigid_links = bool(on)
        if self._ops_view_active and self._last_spec is not None:
            self._draw_from_spec(self._last_spec, self._last_om)

    def set_dof_color_mode(self, on: bool):
        """자유도 묶음 색을 DOF 수별로 분리해 표시할지 토글."""
        self._dof_color_mode = bool(on)
        if self._ops_view_active and self._last_spec is not None:
            self._draw_from_spec(self._last_spec, self._last_om)

    def set_rule_visibility(self, rule_id: str, visible: bool):
        """규칙 ID 별 결합선 시각화 토글 (단순 시각 — 실제 결합은 유지).

        [정책]
        spec/ops 의 결합 데이터는 그대로. 본 메서드는 _draw_from_spec 의
        그리기 루프에서 해당 rule_id 의 결합선만 skip 하도록 hidden 집합을
        갱신하고 즉시 재그리기. 다른 룰·노드·부재 표시에 영향 없음.
        """
        if visible:
            self._hidden_rule_ids.discard(rule_id)
        else:
            self._hidden_rule_ids.add(rule_id)
        if self._ops_view_active and self._last_spec is not None:
            self._draw_from_spec(self._last_spec, self._last_om)

    def _draw_from_spec(self, spec, ops_model=None):
        """ModelSpec 단일 순회기로 좌측 뷰를 그린다 (Phase 6 — 단일화 핵심).

        OpenSees 등록기와 본 표시기가 동일 spec 알갱이를 1:1 소비 → "보이는
        것 = 계산되는 것" 코드 구조 보장.

        토글 OFF 인 항목(다이어프램·강체 링크) 은 그리지 않음.
        """
        # 토글 시 재그리기용 보관.
        self._last_spec = spec
        self._last_om = ops_model
        # ── 노드 (일반 노드 회색 점) ──
        nodes = list(spec.iter_nodes())
        if nodes:
            pos = np.array([n.coord for n in nodes], dtype=np.float32)
            n_all = len(pos)
            colors = np.tile(np.array([list(OPS_COLOR_NODE)],
                                       dtype=np.float32), (n_all, 1))
            # 노드 점 크기 — 회색이라 작게 둬도 식별 가능. (Phase 색 정리)
            sizes = np.full(n_all, 4.0, dtype=np.float32)
            # [2026-05-13 가상 코어 제거] virtual_core_* 분기 폐기 — spec 에 더는
            # 등록되지 않음. 다이어프램 master 노드만 시각 노이즈 감소를 위해 축소.
            for i, n in enumerate(nodes):
                if n.role == 'diaphragm_master':
                    sizes[i] = 3.0
            self._ops_node_markers.set_data(
                pos=pos, face_color=colors,
                edge_color=OPS_COLOR_NODE_EDGE, size=sizes,
            )
            self._ops_node_markers.visible = True

        # ── 보-기둥 — 일반 vs RC 코어 분리 ──
        regular_segs: List[np.ndarray] = []
        core_segs: List[np.ndarray] = []
        for b in spec.iter_beams():
            n1 = spec.node(b.n1)
            n2 = spec.node(b.n2)
            if n1 is None or n2 is None:
                continue
            if b.role == 'rc_core' or b.role.startswith('core_'):
                core_segs.append(n1.coord); core_segs.append(n2.coord)
            else:
                regular_segs.append(n1.coord); regular_segs.append(n2.coord)
        if regular_segs:
            arr = np.array(regular_segs, dtype=np.float32)
            self._ops_member_lines.set_data(
                pos=arr, color=OPS_COLOR_BEAM,
                connect='segments', width=2.0,
            )
            self._ops_member_lines.visible = True
        if core_segs:
            arr = np.array(core_segs, dtype=np.float32)
            self._ops_core_lines.set_data(
                pos=arr, color=OPS_COLOR_CORE,
                connect='segments', width=3.0,
            )
            self._ops_core_lines.visible = True

        # ── 면 요소 — 4 변 외곽선 짙은 보라 ──
        shell_segs: List[np.ndarray] = []
        for s in spec.iter_shells():
            ns = [spec.node(s.n1), spec.node(s.n2),
                  spec.node(s.n3), spec.node(s.n4)]
            if any(x is None for x in ns):
                continue
            cs = [x.coord for x in ns]
            for a, c in ((0, 1), (1, 2), (2, 3), (3, 0)):
                shell_segs.append(cs[a]); shell_segs.append(cs[c])
        if shell_segs:
            arr = np.array(shell_segs, dtype=np.float32)
            self._ops_shell_lines.set_data(
                pos=arr, color=OPS_COLOR_SHELL,
                connect='segments', width=1.6,
            )
            self._ops_shell_lines.visible = True

        # ── 자유도 묶음 (DOF 색 모드 토글 가능) ──
        # 기본 모드(OFF): 핀 노랑 / 6 DOF 진한 자홍
        # DOF 색 모드(ON): 1/2/3/6 DOF 별 청록·연두·주황·자홍 분리
        iface_segs: List[np.ndarray] = []
        iface_colors: List[Tuple[float, float, float, float]] = []
        kind_count: Dict[str, int] = {}
        for e in spec.iter_equal_dofs():
            kind_count[e.kind] = kind_count.get(e.kind, 0) + 1
            if e.kind in OPS_LINK_SKIP_KINDS:
                continue
            # [2026-05-17] 규칙 ID 시각화 토글 — 본 룰 ID 가 hidden 집합에
            # 있으면 결합선 그리기 skip. 실제 결합(ops)·spec 데이터는 영향 없음.
            if getattr(e, 'rule_id', '') in self._hidden_rule_ids:
                continue
            n1 = spec.node(e.master)
            n2 = spec.node(e.slave)
            if n1 is None or n2 is None:
                continue
            # [2026-05-13 접합부조정탭 Phase 5] rule_id 매핑 우선.
            # 매핑에 등록된 rule_id 면 그 색으로 그림. 'legacy_auto' 같은 미등록
            # 항목은 기존 kind/dof 색으로 폴백 — 회귀 없음.
            rule_color = OPS_RULE_ID_COLOR.get(getattr(e, 'rule_id', ''))
            if rule_color is not None:
                col = rule_color
            elif self._dof_color_mode:
                # DOF 수별 색 분리.
                dof_n = len(e.dofs)
                if dof_n >= 6:
                    col = OPS_COLOR_DOF_6
                elif dof_n == 3:
                    col = OPS_COLOR_DOF_3
                elif dof_n == 2:
                    col = OPS_COLOR_DOF_2
                else:
                    col = OPS_COLOR_DOF_1
            else:
                # 기본: 6 DOF 자홍, 나머지 핀 노랑.
                if len(e.dofs) >= 6:
                    col = OPS_COLOR_RIGID6
                else:
                    col = OPS_LINK_KIND_COLOR.get(e.kind, OPS_COLOR_PIN)
            iface_segs.append(n1.coord); iface_segs.append(n2.coord)
            iface_colors.append(col); iface_colors.append(col)
        if iface_segs:
            arr = np.array(iface_segs, dtype=np.float32)
            cols = np.array(iface_colors, dtype=np.float32)
            self._ops_iface_lines.set_data(
                pos=arr, color=cols,
                connect='segments', width=3.5,
            )
            self._ops_iface_lines.visible = True
            dprint('VIS-spec', f'[VIS-spec] 자유도 묶음 분포: {kind_count}')
        else:
            # [2026-05-17] 모든 룰을 끈 경우 등 결합선이 0 개일 때 visible=False
            # 로 이전 프레임의 잔존 데이터를 가림. set_data 호출만 안 하면 이전
            # 데이터가 그대로 남아 토글이 즉시 반영 안 되는 버그.
            self._ops_iface_lines.visible = False

        # ── (Phase 8 토글) 강체 다이어프램 master ↔ slaves ──
        if self._show_diaphragms:
            dseg: List[np.ndarray] = []
            # 폴리곤 면 채우기용: vertices(N,3) + faces(M,3 triangle indices)
            mesh_verts: List[np.ndarray] = []
            mesh_faces: List[Tuple[int, int, int]] = []
            for d in spec.iter_diaphragms():
                # hidden 플래그된 다이어프램(천장 mechanism 방지용)은 시각화 제외
                if getattr(d, 'hidden', False):
                    continue
                m = spec.node(d.master)
                if m is None:
                    continue
                slave_coords: List[np.ndarray] = []
                for snid in d.slaves:
                    sn = spec.node(snid)
                    if sn is None:
                        continue
                    dseg.append(m.coord); dseg.append(sn.coord)
                    slave_coords.append(np.asarray(sn.coord, dtype=np.float32))
                if len(slave_coords) >= 3:
                    # master xy 기준 각도 정렬 → 부채꼴 다각형
                    mx = float(m.coord[0]); my = float(m.coord[1])
                    angs = [np.arctan2(p[1] - my, p[0] - mx) for p in slave_coords]
                    order = np.argsort(angs)
                    sorted_pts = [slave_coords[i] for i in order]
                    base_idx = len(mesh_verts)
                    mesh_verts.append(np.asarray(m.coord, dtype=np.float32))
                    for p in sorted_pts:
                        mesh_verts.append(p)
                    n = len(sorted_pts)
                    for i in range(n):
                        ni = (i + 1) % n
                        mesh_faces.append((base_idx,
                                           base_idx + 1 + i,
                                           base_idx + 1 + ni))
            if dseg:
                arr = np.array(dseg, dtype=np.float32)
                self._ops_diaphragm_lines.set_data(
                    pos=arr, color=OPS_COLOR_DIAPHRAGM,
                    connect='segments', width=1.0,
                )
                self._ops_diaphragm_lines.visible = True
            else:
                self._ops_diaphragm_lines.visible = False
            if mesh_verts and mesh_faces:
                vtx = np.asarray(mesh_verts, dtype=np.float32)
                fcs = np.asarray(mesh_faces, dtype=np.uint32)
                # 옅은 청록색 반투명 (alpha=0.18) — 모듈/슬래브 표면과 구분.
                face_color = (0.30, 0.75, 0.95, 0.18)
                self._ops_diaphragm_mesh.set_data(
                    vertices=vtx, faces=fcs, color=face_color,
                )
                self._ops_diaphragm_mesh.visible = True
            else:
                self._ops_diaphragm_mesh.visible = False
        else:
            self._ops_diaphragm_lines.visible = False
            self._ops_diaphragm_mesh.visible = False

        # ── (Phase 8 토글) 강체 링크 ──
        if self._show_rigid_links:
            rseg: List[np.ndarray] = []
            for r in spec.iter_rigid_links():
                m = spec.node(r.master)
                s = spec.node(r.slave)
                if m is None or s is None:
                    continue
                rseg.append(m.coord); rseg.append(s.coord)
            if rseg:
                arr = np.array(rseg, dtype=np.float32)
                self._ops_rigidlink_lines.set_data(
                    pos=arr, color=OPS_COLOR_RIGIDLINK,
                    connect='segments', width=2.5,
                )
                self._ops_rigidlink_lines.visible = True
            else:
                self._ops_rigidlink_lines.visible = False
        else:
            self._ops_rigidlink_lines.visible = False

        # ── 고정점 (완전 6 DOF 고정만 ▼ 표시) ──
        # 다이어프램 master 노드는 fully fix 라도 ▼ 표시 제외 — 모든 슬레이브가
        # 이미 base-fix 라 안정성용으로 fully fix 처리한 것일 뿐, 실제 지지점
        # (base support) 이 아니다. 사용자 보고: 1F 다이어프램 중심에 ▼ 가
        # 간헐적으로 생기는 결함의 시각적 원인.
        full_fix_pts: List[np.ndarray] = []
        for f in spec.iter_fixes():
            if all(v == 1 for v in f.dofs):
                n = spec.node(f.tag)
                if n is not None and getattr(n, 'role', '') != 'diaphragm_master':
                    full_fix_pts.append(n.coord)
        if full_fix_pts:
            arr = np.array(full_fix_pts, dtype=np.float32)
            colors = np.tile(np.array([list(OPS_COLOR_BASE)],
                                       dtype=np.float32), (len(full_fix_pts), 1))
            self._ops_support_markers.set_data(
                pos=arr, face_color=colors,
                edge_color=OPS_COLOR_BASE,
                size=np.full(len(full_fix_pts), 14.0, dtype=np.float32),
                symbol='triangle_down',
            )
            self._ops_support_markers.visible = True

        self._ops_view_active = True
        self.canvas.update()

    def show_ops_view(self, ops_model):
        """OpenSees 모델을 좌측 뷰에 표시 — ModelSpec 단일 순회기 사용 (Phase 6).

        ops_model 은 build_ops_model 의 결과(OpsModel). 본 메소드는 그 안에
        들어 있는 spec(ModelSpec) 을 단일 순회해 화면을 그린다 — OpenSees 가
        받은 알갱이와 화면이 그리는 알갱이가 1:1 보장.

        spec 이 없으면(예전 빌드 결과) 레거시 경로로 fallback 한다.

        그리는 알갱이:
        - 노드: 빨간 점 (모든 NodeRec)
        - 보-기둥: 검정 선 + 짙은 보라(rc_core)
        - 면 요소: 짙은 보라 4 변 외곽선
        - 자유도 묶음: 노랑(핀)·진한 자홍(6 DOF 강체)
        - 강체 다이어프램 / 강체 링크: Phase 8 토글
        - 고정점: 검정 ▼ (완전 고정 6 DOF 만 표시)
        """
        self._ensure_ops_visuals()
        if ops_model is None:
            return

        # 1) 기존 메쉬 디밍 — add_component_visual 시 영구 보관한 원본 사용.
        # 함정: 한 번 alpha<1 로 set_data 하면 vispy 가 GL blend 를 자동으로 켜는데,
        #       나중에 alpha=1 로 복원해도 blend 가 OFF 로 자동 전환되지 않아
        #       복원 후에도 메쉬가 반투명하게 보이는 버그가 있다.
        #       → set_gl_state 를 명시적으로 토글해야 함.
        if not hasattr(self, '_comp_orig_face_colors'):
            self._comp_orig_face_colors = {}
        for cid, mesh in self._component_visuals.items():
            orig = self._comp_orig_face_colors.get(cid)
            if orig is None or len(orig) == 0:
                continue
            n_faces = len(orig)
            gray = np.tile(np.array([[0.65, 0.65, 0.65, 0.18]],
                                    dtype=np.float32), (n_faces, 1))
            mesh.set_data(face_colors=gray)
            mesh.set_gl_state('translucent', depth_test=False, cull_face=False)

        # 2) spec 기반 그리기 (Phase 6 단일 순회기) — spec 이 있으면 우선 사용.
        spec = getattr(ops_model, 'spec', None)
        if spec is not None:
            self._draw_from_spec(spec, ops_model)
            return

        # ── 레거시 경로 (spec 없는 옛 빌드 결과 호환) ──
        # 2) 노드 (빨간 점)
        node_ids = list(ops_model.node_tags.keys())
        node_pos = np.array([ops_model.node_tags[nid] for nid in node_ids],
                            dtype=np.float32)
        n = len(node_pos)
        if n > 0:
            node_colors = np.tile(np.array([list(OPS_COLOR_NODE)],
                                            dtype=np.float32), (n, 1))
            sizes = np.full(n, 4.0, dtype=np.float32)
            self._ops_node_markers.set_data(
                pos=node_pos, face_color=node_colors,
                edge_color=OPS_COLOR_NODE_EDGE, size=sizes,
            )
            self._ops_node_markers.visible = True

        # 3) 일반 부재 (검정 선) + RC 코어 분리 (짙은 보라)
        ndmap = ops_model.node_tags
        regular_segs: List[np.ndarray] = []
        core_segs: List[np.ndarray] = []
        for tag, (n1, n2, _kind, role) in ops_model.beam_elements.items():
            c1 = ndmap.get(n1)
            c2 = ndmap.get(n2)
            if c1 is None or c2 is None:
                continue
            if role == 'rc_core':
                core_segs.append(c1)
                core_segs.append(c2)
            else:
                regular_segs.append(c1)
                regular_segs.append(c2)

        if regular_segs:
            arr = np.array(regular_segs, dtype=np.float32)
            self._ops_member_lines.set_data(
                pos=arr, color=OPS_COLOR_BEAM,
                connect='segments', width=2.0,
            )
            self._ops_member_lines.visible = True
        if core_segs:
            arr = np.array(core_segs, dtype=np.float32)
            self._ops_core_lines.set_data(
                pos=arr, color=OPS_COLOR_CORE,
                connect='segments', width=3.0,
            )
            self._ops_core_lines.visible = True

        # 3-b) ShellMITC4 요소 — 4 노드 quad 의 4 변을 모두 outline 으로 그림.
        # beam_elements 는 n1-n2 한 변만 저장하므로 슬래브·벽이 사실상 안 보였다.
        # shell_elements 의 (n1,n2,n3,n4) 로 외곽선 segment 4 개씩 추가.
        # 정책(Phase 1 통합): 코어벽·코어 슬래브·기타 면 요소를 한 색(짙은 보라)
        # 으로 통일. 역할 구분은 토글 모드에서만 별도 색으로 분리(Phase 9).
        shell_segs: List[np.ndarray] = []
        shell_dict = getattr(ops_model, 'shell_elements', {}) or {}
        for _tag, (s1, s2, s3, s4, _srole) in shell_dict.items():
            cs = [ndmap.get(s1), ndmap.get(s2), ndmap.get(s3), ndmap.get(s4)]
            if any(c is None for c in cs):
                continue
            # quad 4 변: (n1-n2), (n2-n3), (n3-n4), (n4-n1)
            for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
                shell_segs.append(cs[a])
                shell_segs.append(cs[b])
        if shell_segs:
            arr = np.array(shell_segs, dtype=np.float32)
            self._ops_shell_lines.set_data(
                pos=arr, color=OPS_COLOR_SHELL,
                connect='segments', width=1.6,
            )
            self._ops_shell_lines.visible = True

        # 4) 인터페이스 결합 표시 — [2026-05-18] 폐기.
        #     자동 휴리스틱 결합 폐기로 om.interface_links 자료구조 자체가 삭제됨.
        #     joint_rules 가 등록하는 결합은 spec.equal_dofs / spec.rigid_links
        #     에 저장되며, 별도 시각화 정책 도입 전까지 그리지 않음.

        # 5) 베이스 지점 마커 (검정 큰 점, 향후 ▼ 모양 sym 으로 교체 가능)
        if ops_model.fixed_nodes:
            base_pos = np.array(
                [ops_model.node_tags[nid] for nid in ops_model.fixed_nodes
                 if nid in ops_model.node_tags],
                dtype=np.float32,
            )
            if len(base_pos) > 0:
                colors = np.tile(np.array([list(OPS_COLOR_BASE)],
                                          dtype=np.float32),
                                 (len(base_pos), 1))
                self._ops_support_markers.set_data(
                    pos=base_pos, face_color=colors,
                    edge_color=OPS_COLOR_BASE,
                    size=np.full(len(base_pos), 14.0, dtype=np.float32),
                    symbol='triangle_down',
                )
                self._ops_support_markers.visible = True

        self._ops_view_active = True
        self.canvas.update()

    def hide_ops_view(self):
        """OpenSees 뷰 비활성 → 원래 메쉬 색상 복원."""
        self._ensure_ops_visuals()
        if not self._ops_view_active:
            return

        # 메쉬 색상 복원 — add_component_visual 시 영구 보관한 원본을 사용.
        # 함정: face_colors 만 set_data 로 복원해도 vispy 의 GL blend 상태가 켜진 채로
        #       남아 있어 메쉬가 여전히 반투명하게 보인다 → set_gl_state 명시 필수.
        # 단, 원본이 이미 alpha<1 인 컴포넌트(반투명 칸막이 등)는 translucent 유지.
        if hasattr(self, '_comp_orig_face_colors'):
            for cid, mesh in self._component_visuals.items():
                orig = self._comp_orig_face_colors.get(cid)
                if orig is not None:
                    mesh.set_data(face_colors=orig)
                    # 원본 alpha 분석 → opaque vs translucent 결정
                    try:
                        min_alpha = float(np.min(orig[:, 3]))
                    except Exception:
                        min_alpha = 1.0
                    if min_alpha >= 0.999:
                        mesh.set_gl_state('opaque', depth_test=True, cull_face=False)
                        mesh.update_gl_state(blend=False)
                    else:
                        mesh.set_gl_state('translucent', depth_test=True, cull_face=False)

        for v in (self._ops_node_markers, self._ops_member_lines,
                  self._ops_iface_lines, self._ops_core_lines,
                  self._ops_shell_lines,
                  self._ops_diaphragm_lines, self._ops_diaphragm_mesh,
                  self._ops_rigidlink_lines,
                  self._ops_support_markers):
            v.visible = False
        # 불안정 경고도 함께 정리
        if getattr(self, '_unstable_initialized', False):
            self._unstable_node_markers.visible = False
            self._unstable_member_lines.visible = False
            self._unstable_active = False

        self._ops_view_active = False
        self.canvas.update()

    def is_ops_view_active(self) -> bool:
        return getattr(self, '_ops_view_active', False)

    # ── 변형 형상 (Phase 4-B) ───────────────────────────────

    def update_deformed_shape(self, ops_model, ops_results, scale: float):
        """ops 모델 위에 변형 형상을 덧그린다.

        - 변형된 노드 좌표 = 원좌표 + scale × 변위(병진 3 DOF)
        - 변형된 일반 부재선 = 변형된 양 끝 노드 잇는 선 (반투명 파랑)
        - 인터페이스 핀 링크 + RC 코어도 동일 처리
        - undeformed 는 그대로 유지하고, deformed 만 추가 레이어로 표시
        """
        self._ensure_deformed_visuals()
        if ops_model is None or ops_results is None:
            self.hide_deformed_shape()
            return

        # 1) 변형 좌표 사전 구축
        disp = ops_results.node_disps
        deformed: Dict[int, np.ndarray] = {}
        for nid, base in ops_model.node_tags.items():
            d = disp.get(nid)
            if d is not None and len(d) >= 3:
                deformed[nid] = base + scale * np.array(d[:3], dtype=np.float64)
            else:
                deformed[nid] = base.copy()

        # 2) 부재 segments
        regular_segs: List[np.ndarray] = []
        core_segs: List[np.ndarray] = []
        for tag, (n1, n2, _kind, role) in ops_model.beam_elements.items():
            c1 = deformed.get(n1)
            c2 = deformed.get(n2)
            # 코어 노드는 ops_model.node_tags 에 없을 수 있음 — 변위도 없으므로 원좌표 사용
            if c1 is None or c2 is None:
                try:
                    import openseespy.opensees as ops
                    if c1 is None:
                        c1 = np.array(ops.nodeCoord(n1))
                    if c2 is None:
                        c2 = np.array(ops.nodeCoord(n2))
                except Exception as e:
                    # 변형 시각화에서 부재 한 본이 누락됨 — 사용자가 화면에서
                    # "왜 이 부재 안 보이지?" 모르고 지나가지 않도록 콘솔 보고.
                    dprint('viewer', f'[viewer] 변형 시각화 — 부재 끝점 ({n1},{n2}) 좌표 회수 실패, 부재 스킵: {e}')
                    continue
            if role == 'rc_core':
                core_segs.append(c1); core_segs.append(c2)
            else:
                regular_segs.append(c1); regular_segs.append(c2)

        if regular_segs:
            arr = np.array(regular_segs, dtype=np.float32)
            self._deformed_member_lines.set_data(
                pos=arr, color=(0.10, 0.40, 1.00, 0.85),
                connect='segments', width=2.5,
            )
            self._deformed_member_lines.visible = True

        if core_segs:
            arr = np.array(core_segs, dtype=np.float32)
            self._deformed_core_lines.set_data(
                pos=arr, color=(OPS_COLOR_CORE[0], OPS_COLOR_CORE[1],
                                OPS_COLOR_CORE[2], 0.85),
                connect='segments', width=3.0,
            )
            self._deformed_core_lines.visible = True

        # 3) 인터페이스 링크 — [2026-05-18] 폐기 (자료구조 삭제).

        # 4) 변형 후 노드 점 (작은 파란 점)
        node_pos = np.array(list(deformed.values()), dtype=np.float32)
        if len(node_pos):
            colors = np.tile(np.array([[0.10, 0.40, 1.00, 1.0]], dtype=np.float32),
                             (len(node_pos), 1))
            self._deformed_node_markers.set_data(
                pos=node_pos, face_color=colors,
                edge_color=(0.0, 0.2, 0.7, 1.0),
                size=np.full(len(node_pos), 5.0, dtype=np.float32),
            )
            self._deformed_node_markers.visible = True

        self._deformed_active = True
        self.canvas.update()

    def hide_deformed_shape(self):
        self._ensure_deformed_visuals()
        for v in (self._deformed_member_lines, self._deformed_core_lines,
                  self._deformed_iface_lines, self._deformed_node_markers):
            v.visible = False
        self._deformed_active = False
        self.canvas.update()

    def _ensure_deformed_visuals(self):
        if getattr(self, '_deformed_initialized', False):
            return
        self._deformed_member_lines = visuals.Line(parent=self.view.scene)
        self._deformed_member_lines.visible = False
        self._deformed_member_lines.order = 22
        self._deformed_member_lines.set_gl_state('translucent', depth_test=False)

        self._deformed_core_lines = visuals.Line(parent=self.view.scene)
        self._deformed_core_lines.visible = False
        self._deformed_core_lines.order = 22
        self._deformed_core_lines.set_gl_state('translucent', depth_test=False)

        self._deformed_iface_lines = visuals.Line(parent=self.view.scene)
        self._deformed_iface_lines.visible = False
        self._deformed_iface_lines.order = 22
        self._deformed_iface_lines.set_gl_state('translucent', depth_test=False)

        self._deformed_node_markers = visuals.Markers(parent=self.view.scene)
        self._deformed_node_markers.visible = False
        self._deformed_node_markers.order = 23
        self._deformed_node_markers.set_gl_state('translucent', depth_test=False)

        self._deformed_active = False
        self._deformed_initialized = True

    def is_deformed_shape_active(self) -> bool:
        return getattr(self, '_deformed_active', False)

    # ── 컨투어 (Phase 4-C) ────────────────────────────────

    @staticmethod
    def _ratio_to_jet(r: float) -> Tuple[float, float, float, float]:
        """비율 r ∈ [0, 1] → jet 컬러맵 (blue→cyan→green→yellow→red).

        r > 1 도 허용 (red 로 고정).
        """
        r = max(0.0, min(r, 1.0))
        if r < 0.25:
            t = r / 0.25
            return (0.0, t, 1.0, 1.0)            # blue → cyan
        if r < 0.5:
            t = (r - 0.25) / 0.25
            return (0.0, 1.0, 1.0 - t, 1.0)      # cyan → green
        if r < 0.75:
            t = (r - 0.5) / 0.25
            return (t, 1.0, 0.0, 1.0)            # green → yellow
        t = (r - 0.75) / 0.25
        return (1.0, 1.0 - t, 0.0, 1.0)          # yellow → red

    @staticmethod
    def _ratio_to_5band(r: float) -> Tuple[float, float, float, float]:
        """응력비 → 5단계 색상 (물량산출 탭 시각화).

        파랑(<0.30) / 하늘(<0.60) / 초록(≤0.85) / 주황(≤1.0) / 빨강(>1.0)
        """
        if r < 0.30:
            return (0.20, 0.40, 1.00, 1.0)       # 파랑 — 비경제
        if r < 0.60:
            return (0.40, 0.80, 1.00, 1.0)       # 하늘 — 여유
        if r <= 0.85:
            return (0.30, 0.90, 0.30, 1.0)       # 초록 — 적정
        if r <= 1.00:
            return (1.00, 0.60, 0.00, 1.0)       # 주황 — 한계
        return (1.00, 0.20, 0.20, 1.0)           # 빨강 — NG

    def show_member_ratio_bands(self, ops_model, ratios: Dict[int, float]):
        """5단계 색상으로 부재 색칠 (물량산출 탭 진입 시 호출).

        show_member_contour 와 동일 구조이나 _ratio_to_5band 를 사용.
        """
        self._ensure_ops_visuals()
        # (2026-05-12 #132) ele_tag → mid 역인덱스 1 회 구축 (O(N²) → O(N)).
        ele_to_mid = {tag: mid for mid, tag in
                      ops_model.member_to_ele_tag.items()}
        regular_segs: List[np.ndarray] = []
        seg_colors: List[Tuple[float, float, float, float]] = []
        for tag, (n1, n2, _kind, role) in ops_model.beam_elements.items():
            if role == 'rc_core':
                continue
            c1 = ops_model.node_tags.get(n1)
            c2 = ops_model.node_tags.get(n2)
            if c1 is None or c2 is None:
                continue
            mid = ele_to_mid.get(tag)
            r = ratios.get(mid, 0.0) if mid is not None else 0.0
            color = self._ratio_to_5band(r)
            regular_segs.append(c1); regular_segs.append(c2)
            seg_colors.append(color); seg_colors.append(color)

        if regular_segs:
            arr = np.array(regular_segs, dtype=np.float32)
            col = np.array(seg_colors, dtype=np.float32)
            self._ops_member_lines.set_data(
                pos=arr, color=col, connect='segments', width=4.0,
            )
            self._ops_member_lines.visible = True
        self.canvas.update()

    def show_member_contour(self, ops_model, ratios: Dict[int, float]):
        """일반 부재선을 부재별 ratio (0..1+) 에 따라 jet 컬러맵으로 색칠.

        ratios: AnalysisMember.id → ratio (0=파랑, 1=빨강, >1 도 빨강 고정)
        """
        self._ensure_ops_visuals()
        # (2026-05-12 #132) ele_tag → mid 역인덱스 1 회 구축.
        ele_to_mid = {tag: mid for mid, tag in
                      ops_model.member_to_ele_tag.items()}
        # 일반 부재 segments + 색
        regular_segs: List[np.ndarray] = []
        seg_colors: List[Tuple[float, float, float, float]] = []
        for tag, (n1, n2, _kind, role) in ops_model.beam_elements.items():
            if role == 'rc_core':
                continue
            c1 = ops_model.node_tags.get(n1)
            c2 = ops_model.node_tags.get(n2)
            if c1 is None or c2 is None:
                continue
            mid = ele_to_mid.get(tag)
            r = ratios.get(mid, 0.0) if mid is not None else 0.0
            color = self._ratio_to_jet(r)
            regular_segs.append(c1); regular_segs.append(c2)
            seg_colors.append(color); seg_colors.append(color)

        if regular_segs:
            arr = np.array(regular_segs, dtype=np.float32)
            col = np.array(seg_colors, dtype=np.float32)
            self._ops_member_lines.set_data(
                pos=arr, color=col, connect='segments', width=3.5,
            )
            self._ops_member_lines.visible = True
        self.canvas.update()

    def show_unstable_warning(self, ops_model, problem_node_ids):
        """불안정 구조 경고 — 문제 노드 + 그 노드를 포함한 부재를 **빨간색** 강조.

        사용자가 mechanism 발생 위치를 즉시 알아볼 수 있도록,
        - 문제 노드: 큰 빨간 원 (size 18, edge 진한 빨강)
        - 문제 부재: 일반 검정선 위에 빨간 굵은 선 오버레이 (별도 Line visual)
        """
        self._ensure_ops_visuals()
        self._ensure_unstable_visuals()

        if not problem_node_ids:
            self.hide_unstable_warning()
            return

        # 1) 문제 노드 좌표
        bad_nodes = [(nid, ops_model.node_tags[nid])
                     for nid in problem_node_ids
                     if nid in ops_model.node_tags]
        if not bad_nodes:
            self.hide_unstable_warning()
            return

        node_pos = np.array([c for _, c in bad_nodes], dtype=np.float32)
        n = len(node_pos)
        colors = np.tile(np.array([[1.0, 0.0, 0.0, 1.0]], dtype=np.float32),
                         (n, 1))
        self._unstable_node_markers.set_data(
            pos=node_pos, face_color=colors,
            edge_color=(0.6, 0.0, 0.0, 1.0),
            size=np.full(n, 18.0, dtype=np.float32),
        )
        self._unstable_node_markers.visible = True

        # 2) 문제 노드를 한쪽 끝이라도 포함하는 부재 → 빨간 굵은 선
        bad_set = set(problem_node_ids)
        bad_segs: List[np.ndarray] = []
        for tag, (n1, n2, _kind, role) in ops_model.beam_elements.items():
            if role == 'rc_core':
                continue
            if n1 in bad_set or n2 in bad_set:
                c1 = ops_model.node_tags.get(n1)
                c2 = ops_model.node_tags.get(n2)
                if c1 is not None and c2 is not None:
                    bad_segs.append(c1)
                    bad_segs.append(c2)
        if bad_segs:
            arr = np.array(bad_segs, dtype=np.float32)
            self._unstable_member_lines.set_data(
                pos=arr, color=(1.0, 0.05, 0.05, 1.0),
                connect='segments', width=5.0,
            )
            self._unstable_member_lines.visible = True

        self._unstable_active = True
        self.canvas.update()

    def hide_unstable_warning(self):
        self._ensure_unstable_visuals()
        self._unstable_node_markers.visible = False
        self._unstable_member_lines.visible = False
        self._unstable_active = False
        self.canvas.update()

    def _ensure_unstable_visuals(self):
        if getattr(self, '_unstable_initialized', False):
            return
        self._unstable_node_markers = visuals.Markers(parent=self.view.scene)
        self._unstable_node_markers.visible = False
        self._unstable_node_markers.order = 25   # 가장 위
        self._unstable_node_markers.set_gl_state('translucent', depth_test=False)

        self._unstable_member_lines = visuals.Line(parent=self.view.scene)
        self._unstable_member_lines.visible = False
        self._unstable_member_lines.order = 24
        self._unstable_member_lines.set_gl_state('translucent', depth_test=False)

        self._unstable_active = False
        self._unstable_initialized = True

    def hide_member_contour(self, ops_model):
        """컨투어 해제 → 일반 부재 검정 단색으로 복귀."""
        self._ensure_ops_visuals()
        regular_segs: List[np.ndarray] = []
        for tag, (n1, n2, _kind, role) in ops_model.beam_elements.items():
            if role == 'rc_core':
                continue
            c1 = ops_model.node_tags.get(n1)
            c2 = ops_model.node_tags.get(n2)
            if c1 is None or c2 is None:
                continue
            regular_segs.append(c1); regular_segs.append(c2)
        if regular_segs:
            arr = np.array(regular_segs, dtype=np.float32)
            self._ops_member_lines.set_data(
                pos=arr, color=(0.05, 0.05, 0.05, 1.0),
                connect='segments', width=2.0,
            )
            self._ops_member_lines.visible = True
        self.canvas.update()

    def highlight_ops_member(self, ops_model, n1: int, n2: int):
        """ops 모델에서 (n1, n2) 부재를 파란색 강조."""
        c1 = ops_model.node_tags.get(n1)
        c2 = ops_model.node_tags.get(n2)
        if c1 is None or c2 is None:
            self.clear_member_highlight()
            return
        pos = np.array([c1, c2], dtype=np.float32)
        self._member_highlight.set_data(
            pos=pos, color=(0.10, 0.40, 1.00, 1.0),
            connect='strip', width=6.0,
        )
        self._member_highlight.visible = True
        self.canvas.update()

    def update_pinned_members(self, segments_with_colors, width=5.0):
        """Phase 9 — 슬롯별 클릭 고정 부재 강조 갱신.

        segments_with_colors: list of ((c1, c2), (r, g, b, a))
          c1, c2 는 3D 월드 좌표(np.ndarray shape=(3,)), 색은 0~1 RGBA.
        빈 리스트면 비주얼 invisible.
        width: 선 두께(기본 5.0). [9-3] 단면설계 하이라이트는 더 굵게 호출.
        """
        if not segments_with_colors:
            self._pinned_member_lines.visible = False
            self.canvas.update()
            return
        pts = []
        cols = []
        for (c1, c2), col in segments_with_colors:
            pts.append(c1); pts.append(c2)
            cols.append(col); cols.append(col)
        self._pinned_member_lines.set_data(
            pos=np.asarray(pts, dtype=np.float32),
            color=np.asarray(cols, dtype=np.float32),
            connect='segments', width=float(width),
        )
        self._pinned_member_lines.visible = True
        self.canvas.update()

    def highlight_ops_member_hover(self, ops_model, n1: int, n2: int,
                                     color=(0.45, 0.45, 0.45, 0.95),
                                     width: float = 4.5):
        """Phase 2 — 부재 호버 회색 강조. 기본 회색, Phase 13 에서 다음
        배정 슬롯 색으로 교체된다.
        """
        c1 = ops_model.node_tags.get(n1)
        c2 = ops_model.node_tags.get(n2)
        if c1 is None or c2 is None:
            self.clear_ops_member_hover()
            return
        pos = np.array([c1, c2], dtype=np.float32)
        self._member_hover.set_data(
            pos=pos, color=color, connect='strip', width=width,
        )
        self._member_hover.visible = True
        self.canvas.update()

    def clear_ops_member_hover(self):
        """부재 호버 강조 제거."""
        if self._member_hover.visible:
            self._member_hover.visible = False
            self.canvas.update()

    # ── 접합 선택 강조 (2026-05-25 접합부 변경) ──────────────
    def _ensure_joint_highlight_visuals(self):
        """접합 선택 강조용 Line/Markers lazy 생성."""
        if getattr(self, '_joint_hl_line', None) is not None:
            return
        self._joint_hl_line = visuals.Line(parent=self.view.scene)
        self._joint_hl_line.order = 25
        self._joint_hl_line.set_gl_state('translucent', depth_test=False)
        self._joint_hl_pts = visuals.Markers(parent=self.view.scene)
        self._joint_hl_pts.order = 26
        self._joint_hl_pts.set_gl_state('translucent', depth_test=False)

    def highlight_ops_joint(self, ops_model, master: int, slave: int,
                            color=(1.0, 0.85, 0.0, 1.0), width: float = 6.0):
        """선택 접합 강조 — 두 노드를 잇는 굵은 선 + 끝점 마커(노란색).

        수직 접합은 두 노드가 거의 같은 위치(z만 다름)라 짧은 수직 선분으로,
        수평 접합은 떨어진 두 코너를 잇는 선분으로 보인다.
        """
        c1 = ops_model.node_tags.get(master)
        c2 = ops_model.node_tags.get(slave)
        if c1 is None or c2 is None:
            self.clear_ops_joint_highlight()
            return
        self._ensure_joint_highlight_visuals()
        pos = np.array([c1, c2], dtype=np.float32)
        self._joint_hl_line.set_data(
            pos=pos, color=color, connect='strip', width=width)
        self._joint_hl_line.visible = True
        self._joint_hl_pts.set_data(pos=pos, face_color=color, size=13)
        self._joint_hl_pts.visible = True
        self.canvas.update()

    def clear_ops_joint_highlight(self):
        """접합 선택 강조 제거."""
        changed = False
        if getattr(self, '_joint_hl_line', None) is not None and self._joint_hl_line.visible:
            self._joint_hl_line.visible = False
            changed = True
        if getattr(self, '_joint_hl_pts', None) is not None and self._joint_hl_pts.visible:
            self._joint_hl_pts.visible = False
            changed = True
        if changed:
            self.canvas.update()

    def highlight_ops_node(self, ops_model, tag,
                           color=(0.1, 0.8, 0.2, 1.0)):
        """단일 노드 강조 — 접합 추가 모드의 첫 점 표시(초록 마커)."""
        c = ops_model.node_tags.get(tag)
        if c is None:
            self.clear_ops_joint_highlight()
            return
        self._ensure_joint_highlight_visuals()
        pos = np.array([c], dtype=np.float32)
        self._joint_hl_pts.set_data(pos=pos, face_color=color, size=15)
        self._joint_hl_pts.visible = True
        self._joint_hl_line.visible = False
        self.canvas.update()

    def pick_ops_node_at(self, qt_pos, ops_model, threshold: float = 14.0):
        """마우스에 가장 가까운 ops 노드 반환 — 접합 추가 시 두 점 선택용.

        pick_ops_member_at 과 동일한 scene transform + perspective divide 로
        모든 노드를 화면 투영 → 점-점 2D 거리 최소 노드.

        반환(없으면 None): {'tag': nid, 'xy': (x,y), 'z': z,
                            'coord': (x,y,z)}. threshold 단위 픽셀.
        """
        if ops_model is None or not ops_model.node_tags:
            return None
        if not getattr(self, '_ops_initialized', False):
            return None
        tr = self.get_scene_transform()
        if tr is None:
            return None
        tags = []
        coords = []
        for tag, c in ops_model.node_tags.items():
            tags.append(tag)
            coords.append(c)
        pts = np.asarray(coords, dtype=np.float32)
        mapped = tr.map(pts)
        w = mapped[:, 3:4]
        safe_w = np.where(np.abs(w) < 1e-12, 1.0, w)
        xy = (mapped[:, :2] / safe_w).astype(np.float32)
        mxy = np.array([qt_pos[0], qt_pos[1]], dtype=np.float32)
        d = np.linalg.norm(xy - mxy, axis=1)
        w_flat = w.squeeze(-1)
        d = np.where(w_flat <= 0.0, np.inf, d)
        idx = int(np.argmin(d))
        if d[idx] > threshold:
            return None
        tag = tags[idx]
        c = ops_model.node_tags[tag]
        return {
            'tag': int(tag),
            'xy': (float(c[0]), float(c[1])),
            'z': float(c[2]),
            'coord': (float(c[0]), float(c[1]), float(c[2])),
        }

    def pick_ops_edge_point_at(self, qt_pos, ops_model, am,
                               threshold: float = 10.0, snap_mm: float = 150.0):
        """모서리(보) 위에서 마우스에 가장 가까운 점을 반환 — 접합 추가용.

        pick_ops_member_at 과 동일하게 보 선분을 화면 투영해 최근접 선분을
        찾되, 그 선분 위 투영점(3D 좌표)을 함께 돌려준다. 투영점이 끝점(노드)에
        snap_mm(3D mm) 이내면 그 노드로 스냅한다.

        반환(없으면 None): {'point':(x,y,z), 'snapped':bool, 'node':tag|None,
                            'comp':source_comp_id, 'mid':member_id}.
        """
        if am is None or not am.members:
            return None
        if not getattr(self, '_ops_initialized', False):
            return None
        if ops_model is None or not ops_model.node_tags:
            return None
        tr = self.get_scene_transform()
        if tr is None:
            return None
        mids = []
        coords = []
        for mid, m in am.members.items():
            if getattr(m, 'is_split_sub', False):
                continue
            if m.kind != 'beam':
                continue   # 보만 — 모서리 점 접합은 보 대상.
            c1 = ops_model.node_tags.get(m.n1)
            c2 = ops_model.node_tags.get(m.n2)
            if c1 is None or c2 is None:
                continue
            mids.append(mid)
            coords.append(c1)
            coords.append(c2)
        if not mids:
            return None
        pts = np.asarray(coords, dtype=np.float32)
        mapped = tr.map(pts)
        w = mapped[:, 3:4]
        safe_w = np.where(np.abs(w) < 1e-12, 1.0, w)
        xy = (mapped[:, :2] / safe_w).astype(np.float32)
        p1 = xy[0::2]
        p2 = xy[1::2]
        mxy = np.array([qt_pos[0], qt_pos[1]], dtype=np.float32)
        ab = p2 - p1
        ab2 = (ab * ab).sum(axis=1)
        ap = mxy - p1
        denom = np.where(ab2 < 1e-9, 1.0, ab2)
        t = np.clip((ap * ab).sum(axis=1) / denom, 0.0, 1.0)
        proj = p1 + t[:, None] * ab
        d = np.linalg.norm(mxy - proj, axis=1)
        w_flat = w.squeeze(-1)
        behind = (w_flat[0::2] <= 0.0) | (w_flat[1::2] <= 0.0)
        d = np.where(behind, np.inf, d)
        idx = int(np.argmin(d))
        if d[idx] > threshold:
            return None
        mid = mids[idx]
        # 화면상 최근접 비율 s → 원근 보정 객체 비율 tt.
        # 카메라가 원근(fov)이라 화면 균등 s 와 3D 균등 t 가 다르다. 보정 없이
        # 3D 직선보간하면 점이 마우스에서 미세하게 어긋남(유격). 끝점 깊이 w(clip)
        # 가중 보간으로 화면 s 에 정확히 대응하는 3D 점을 얻는다.
        s = float(t[idx])
        w1 = float(w_flat[2 * idx])
        w2 = float(w_flat[2 * idx + 1])
        if w1 > 1e-9 and w2 > 1e-9:
            denom = (1.0 - s) / w1 + s / w2
            tt = (s / w2) / denom if denom > 1e-12 else s
        else:
            tt = s
        m = am.members[mid]
        c1 = np.asarray(ops_model.node_tags[m.n1], dtype=float)
        c2 = np.asarray(ops_model.node_tags[m.n2], dtype=float)
        pt = c1 + tt * (c2 - c1)
        blen = float(np.linalg.norm(c2 - c1))
        snapped = False
        node = None
        if blen > 1.0 and tt * blen <= snap_mm:
            pt = c1.copy()
            node = int(m.n1)
            snapped = True
        elif blen > 1.0 and (1.0 - tt) * blen <= snap_mm:
            pt = c2.copy()
            node = int(m.n2)
            snapped = True
        cid = int(m.source_comp_ids[0]) if m.source_comp_ids else 0
        return {
            'point': (float(pt[0]), float(pt[1]), float(pt[2])),
            'snapped': snapped, 'node': node, 'comp': cid, 'mid': int(mid),
        }

    def _ensure_joint_ghost(self):
        if getattr(self, '_joint_ghost_pts', None) is not None:
            return
        self._joint_ghost_pts = visuals.Markers(parent=self.view.scene)
        self._joint_ghost_pts.order = 27
        self._joint_ghost_pts.set_gl_state('translucent', depth_test=False)

    def show_joint_ghost(self, point, snapped: bool = False):
        """접합 추가 시 클릭 전 미리보기 마커. 스냅(꼭지점)이면 초록, 아니면 주황."""
        self._ensure_joint_ghost()
        color = (0.1, 0.85, 0.2, 0.95) if snapped else (1.0, 0.6, 0.0, 0.95)
        pos = np.array([point], dtype=np.float32)
        self._joint_ghost_pts.set_data(pos=pos, face_color=color, size=14)
        self._joint_ghost_pts.visible = True
        self.canvas.update()

    def clear_joint_ghost(self):
        if (getattr(self, '_joint_ghost_pts', None) is not None
                and self._joint_ghost_pts.visible):
            self._joint_ghost_pts.visible = False
            self.canvas.update()

    # ── 접합 추가 후보점·선 고스트 (2026-05-25) ──────────────
    def _ensure_cand_visuals(self):
        if getattr(self, '_joint_cand_pts', None) is None:
            self._joint_cand_pts = visuals.Markers(parent=self.view.scene)
            self._joint_cand_pts.order = 26
            self._joint_cand_pts.set_gl_state('translucent', depth_test=False)
        if getattr(self, '_joint_line_ghost', None) is None:
            self._joint_line_ghost = visuals.Line(parent=self.view.scene)
            self._joint_line_ghost.order = 26
            self._joint_line_ghost.set_gl_state('translucent', depth_test=False)

    def show_joint_candidates(self, points, color=(0.2, 0.9, 1.0, 0.9)):
        """접합 가능한 후보점들을 하늘색 마커로 표시(첫 점 선택 후)."""
        self._ensure_cand_visuals()
        if not points:
            self._joint_cand_pts.visible = False
            self.canvas.update()
            return
        pos = np.array([(p[0], p[1], p[2]) for p in points], dtype=np.float32)
        self._joint_cand_pts.set_data(pos=pos, face_color=color, size=11)
        self._joint_cand_pts.visible = True
        self.canvas.update()

    def clear_joint_candidates(self):
        if (getattr(self, '_joint_cand_pts', None) is not None
                and self._joint_cand_pts.visible):
            self._joint_cand_pts.visible = False
            self.canvas.update()

    def show_joint_line_ghost(self, p1, p2,
                              color=(1.0, 0.85, 0.0, 0.9), width=3.0):
        """첫 점→현재 점을 잇는 접합 미리보기 선."""
        self._ensure_cand_visuals()
        pos = np.array([p1, p2], dtype=np.float32)
        self._joint_line_ghost.set_data(pos=pos, color=color,
                                        connect='strip', width=width)
        self._joint_line_ghost.visible = True
        self.canvas.update()

    def clear_joint_line_ghost(self):
        if (getattr(self, '_joint_line_ghost', None) is not None
                and self._joint_line_ghost.visible):
            self._joint_line_ghost.visible = False
            self.canvas.update()

    def pick_nearest_candidate(self, qt_pos, points, threshold: float = 18.0):
        """후보점들을 화면 투영해 마우스에 가장 가까운 것 반환 (point, idx)|None."""
        if not points:
            return None
        if not getattr(self, '_ops_initialized', False):
            return None
        tr = self.get_scene_transform()
        if tr is None:
            return None
        pts = np.array([(p[0], p[1], p[2]) for p in points], dtype=np.float32)
        mapped = tr.map(pts)
        w = mapped[:, 3:4]
        safe_w = np.where(np.abs(w) < 1e-12, 1.0, w)
        xy = (mapped[:, :2] / safe_w).astype(np.float32)
        mxy = np.array([qt_pos[0], qt_pos[1]], dtype=np.float32)
        d = np.linalg.norm(xy - mxy, axis=1)
        w_flat = w.squeeze(-1)
        d = np.where(w_flat <= 0.0, np.inf, d)
        idx = int(np.argmin(d))
        if d[idx] > threshold:
            return None
        return points[idx], idx

    def highlight_ops_members(self, ops_model, node_pairs):
        """ops 모델에서 여러 부재를 한 번에 파란색 강조.

        node_pairs: List[(n1, n2)] — am 의 노드 id 쌍 리스트.
        세그먼트 모드로 한 번의 set_data 호출 → 다수 부재 동시 표시.
        """
        segs = []
        for n1, n2 in node_pairs:
            c1 = ops_model.node_tags.get(n1)
            c2 = ops_model.node_tags.get(n2)
            if c1 is None or c2 is None:
                continue
            segs.append(c1); segs.append(c2)
        if not segs:
            self.clear_member_highlight()
            return
        pos = np.array(segs, dtype=np.float32)
        self._member_highlight.set_data(
            pos=pos, color=(0.10, 0.40, 1.00, 1.0),
            connect='segments', width=5.0,
        )
        self._member_highlight.visible = True
        self.canvas.update()

    def highlight_ops_node(self, ops_model, nid: int):
        """ops 모델에서 노드 nid 를 파란색 점으로 강조."""
        c = ops_model.node_tags.get(nid)
        if c is None:
            return
        # 기존 member_highlight Line 을 0 길이 segment 로 사용 X
        # 별도 Markers 로 처리하는 게 깔끔하지만, 단일 노드 강조용 임시 해결:
        # Line 으로 매우 짧은 segment 표시 — 시각적으로 점처럼 보임
        eps = 50.0
        pos = np.array([c, c + np.array([0, 0, eps])], dtype=np.float32)
        self._member_highlight.set_data(
            pos=pos, color=(0.10, 0.40, 1.00, 1.0),
            connect='strip', width=10.0,
        )
        self._member_highlight.visible = True
        self.canvas.update()

    # ── 위젯 ─────────────────────────────────────────────────

    def get_native_widget(self):
        return self.canvas.native
