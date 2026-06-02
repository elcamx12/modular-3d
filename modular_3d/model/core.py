"""
데이터 모델 — 부재, 씬, 액션 정의.
Vispy/Qt 의존성 없음 (순수 데이터).
"""
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
import numpy as np

# 단면 폭 — 카탈로그 단일 진실 원천. 모든 generate_sub_components 에서 사용.
from modular_3d.카탈로그.geometry import SECTION_W_MM as _SECTION_W
from modular_3d.카탈로그.geometry import (
    NONBEARING_WALL_THICKNESS_MM as _NB_WALL_T,
)


# ── 앱 상태 ──────────────────────────────────────────────────

class AppState(Enum):
    IDLE = auto()
    PLACEMENT_PREVIEW = auto()
    DIMENSION_INPUT = auto()
    SELECTED = auto()
    MOVING = auto()


class ComponentType(Enum):
    MODULE = 'module'
    FLOOR_PANEL = 'floor_panel'
    STRUCT_WALL = 'struct_wall'

    # 내벽(비내력 칸막이벽) — 사용자가 세대 내부에 직접 배치하는 벽 (2026-05-24).
    # 구조벽과 별개. 기둥·보·런너 없이 채움 판 1장만. 두께 100, 높이 자동(부모).
    # 부모(모듈·바닥패널·캔틸레버슬래브)에 종속해 다층 복제된다. 하중·물량 0
    # (순수 시각). 개구부를 뚫을 수 있다. 반투명 렌더링.
    INTERIOR_WALL = 'interior_wall'

    CANTILEVER_BEAM = 'cantilever_beam'
    CANTILEVER_SLAB = 'cantilever_slab'
    MID_BEAM = 'mid_beam'
    MID_COLUMN = 'mid_column'

    # 수직 3층 모듈 — 한 칸이 3개 층을 차지한다.
    # 외형: 가로 3400 × 세로 3400 × 높이 (3400×3 + 20×2) = 10240 mm.
    # 내부 슬래브 3개 (1F·2F·3F 바닥). 옥상 슬래브는 multi_floor 측에서
    # 별도 FloorPanel 1개 동반 생성 (단층 모듈 + FloorPanel 패턴 일관성).
    VERTICAL_MODULE = 'vertical_module'

    # RC 코어 (2026-05-11 추가) — 전단벽 한 장 단위.
    # 사용자가 1F 한 장 배치 → multi_floor 가 floor_index 0..N 총 N+1 장 자동 복제.
    # 옥상 한 장 더 = 모듈 한 층 더 쌓은 높이까지 솟음. 같은 그룹에 CORE_SLAB
    # 자동 동반 생성 (모든 층 0..N, 각 층 코어벽 윗면에 깔림, 보 없는 순수 RC 판).
    # OpenSees: wide-column 모델 (단일 frame element, 양 끝 노드를 다이어프램에
    # equalDOF(1,2,6) 로 묶음). 1F 베이스는 6DOF 완전고정. 자세한 사양은
    # modular_3d/RC코어_설계서.md 참조.
    CORE = 'core'
    CORE_SLAB = 'core_slab'


# ── 기본 구조 요소 ──────────────────────────────────────────

@dataclass
class BeamData:
    start: np.ndarray          # (3,) 월드 좌표 mm
    end: np.ndarray            # (3,)
    section_w: float = 200.0   # 단면 폭
    section_h: float = 200.0   # 단면 높이
    section_t: float = 8.0     # 두께
    # 단면 형상 종류 — 'shs'(각형강관) | 'h'(H형강). 보 전용(기둥은 항상 shs).
    # 부재의 beam_section_type 이 generate_sub_components 에서 각 보로 전파된다.
    # 3D 메시·구조해석·물량이 이 값으로 단면 형상을 분기한다. 실제 치수는
    # 구조해석 자동설계가 타입별 카탈로그(SHS/H)에서 결정.
    section_type: str = 'shs'


@dataclass
class ColumnData:
    base: np.ndarray           # (3,) 하부
    top: np.ndarray            # (3,) 상부
    section_w: float = 200.0
    section_h: float = 200.0
    section_t: float = 8.0


@dataclass
class SlabData:
    corners: np.ndarray        # (4, 3) 네 꼭지점
    thickness: float = 150.0


# ── 회전 유틸 ────────────────────────────────────────────────

def _rotate_point(x: float, y: float, rotation: int) -> Tuple[float, float]:
    """로컬 좌표 (x, y)를 rotation 각도만큼 원점 기준 회전."""
    if rotation == 0:
        return x, y
    elif rotation == 90:
        return -y, x
    elif rotation == 180:
        return -x, -y
    elif rotation == 270:
        return y, -x
    return x, y


def _rotate_local_to_world(lx: float, ly: float, lz: float,
                           rotation: int,
                           pos: np.ndarray) -> np.ndarray:
    """로컬 좌표를 회전 + 평행이동하여 월드 좌표로 변환."""
    rx, ry = _rotate_point(lx, ly, rotation)
    return pos + np.array([rx, ry, lz], dtype=np.float64)


def make_local_to_world(ax: float, ay: float, rotation: int,
                        pos: np.ndarray):
    """부재의 anchor·rotation·position 을 캡처한 로컬→월드 변환 closure 반환.

    각 sub-component 생성/추출 시 자기 부재의 ax/ay/rotation/pos 를 captures
    해서 (lx, ly, lz) → 월드 좌표 변환 함수를 만든다. 이전엔 21개 nested
    `def to_world` 가 같은 closure 패턴을 반복했는데, 본 factory 한 줄로 통일.
    분리 의도(부재별 캡처)는 보존.
    """
    def to_world(lx, ly, lz):
        return _rotate_local_to_world(lx - ax, ly - ay, lz, rotation, pos)
    return to_world


# ── 부재 클래스 ──────────────────────────────────────────────

@dataclass
class Component:
    """모든 부재의 기본 클래스.

    [필드 적용 범위 — 단계 2 (2026-05-08) 정리]
    - 공통: id, comp_type, position, rotation, dimensions, anchor
    - 다층 그룹: group_id, floor_index, sub_index (모든 부재)
    - 종속 부재 전용: anchor_edge_id (CantileverBeam, CantileverSlab, MidBeam, MidColumn,
      StructWall 의 부모 모듈 모서리 0~3, 비종속이면 -1)
    - MidBeam 전용: mid_beam_level ('bottom'/'top'/None)
    - StructWall 전용: merge_with_panel, merged_fp_id, merged_wall_ids
    - UI 접합 (모든 종속 부재): parent_id (토폴로지가 좌표 추정 대신 이 값을 우선)
    """
    id: int
    comp_type: ComponentType
    position: np.ndarray            # (3,) 앵커 포인트 월드 좌표 (mm)
    rotation: int = 0               # 0, 90, 180, 270 (도)
    dimensions: Dict[str, float] = field(default_factory=dict)
    anchor: int = 0                 # 0=좌하, 1=우하, 2=우상, 3=좌상 (회전 전 로컬 기준)

    # ── 다층 그룹 식별자 (F5 배치 모드) ──────────────────────────
    # 같은 사용자 액션으로 생성된 부재들은 group_id 를 공유한다.
    # floor_index: 층 (0=1층, 1=2층, …, N=옥상)
    # sub_index  : 같은 층 안에서 본체(0) / 종속 부재(1+)
    # group_id=0 은 "그룹 미지정"(레거시/단독 부재)을 의미.
    group_id: int = 0
    floor_index: int = 0
    sub_index: int = 0

    # ── 종속 부재용 메타데이터 ──────────────────────────────────
    # anchor_edge_id: 부모 그룹의 모서리 ID(0=하단/1=우측/2=상단/3=좌측), -1=비종속
    anchor_edge_id: int = -1
    # mid_beam_level: 'bottom' / 'top' / None — MidBeam 만 의미 있음
    mid_beam_level: Optional[str] = None
    # merge_with_panel: StructWall 만 의미 있음 — FP 와 같은 z 레벨에서 강체 합체
    merge_with_panel: bool = False

    # ── UI 접합 정보 (2026-05 추가) ─────────────────────────────
    # parent_id: placement 시 스냅된 부모 컴포넌트 ID (0=부모 없음).
    # 토폴로지가 좌표 추정 대신 이 값을 우선 사용 — UI 인식 접합 관계 그대로
    # OpenSees 모델에 전달된다.
    parent_id: int = 0

    # ── UI 코너 접합 기록 (2026-05-12, 옵션 (나)) ───────────────
    # joint_records: 본 부재의 각 코너가 어떤 부재의 어떤 지점에 결합되는지를
    # UI 측 기하 입사 검사로 명시 기록. 해석 토폴로지가 거리 기반 매칭 대신
    # 본 기록을 1순위로 사용한다.
    # 각 record dict 구조:
    #   - corner_idx:     0..3 (본 부재 바닥 코너 0=LL,1=LR,2=UR,3=UL 로컬→월드)
    #   - target_comp_id: 결합 대상 컴포넌트 id
    #   - target_xy:      [x, y] mm — 대상 노드의 월드 xy
    #   - target_z:       z mm — 대상 노드의 월드 z (해석 측에서 해당 comp 의
    #                     노드들 중 가장 가까운 1개를 찾는데 사용)
    joint_records: List[Dict] = field(default_factory=list)

    # ── 개구부 (2026-05-24, 3단계) ──────────────────────────────
    # openings: 본 부재 면(슬래브 또는 벽체 채움)에 뚫는 사각 구멍 목록.
    # 부재 '면 로컬' 좌표(mm)로 보관 — 면의 코너[0]→[1]=u축, 코너[0]→[3]=v축.
    #   슬래브: u=폭(local x), v=깊이(local y), 관통=두께(z).
    #   벽체:   u=벽 길이,     v=높이(z),       관통=벽 두께.
    # 각 opening dict: {'u':mm, 'v':mm, 'w':mm, 'h':mm} (u,v=좌하단, w,h=크기).
    # 사용자 데이터이므로 generate_sub_components 가 건드리지 않는다.
    openings: List[Dict] = field(default_factory=list)

    # ── 보 단면 형상 타입 (2026-05-25) ──────────────────────────
    # beam_section_type: 'shs'(각형강관) | 'h'(H형강). 본 부재의 모든 보에 적용.
    # 기둥은 항상 각형강관(본 값 무시). 종속 부재(캔틸레버·중간보)와 바닥패널에
    # 합체된 구조벽은 부모/패널 타입을 따라가도록 배치 시 컨트롤러가 설정한다.
    # generate_sub_components 끝에서 _apply_beam_section_type 로 각 보에 전파.
    beam_section_type: str = 'shs'

    def _apply_beam_section_type(self):
        """본 부재의 모든 보(BeamData)에 beam_section_type 을 전파.

        각 서브클래스 generate_sub_components 마지막에서 호출 — translate 등으로
        보가 재생성돼도 타입이 유지된다(BeamData 기본은 'shs' 라 미호출 시 각형강관).
        """
        bt = getattr(self, 'beam_section_type', 'shs') or 'shs'
        for b in self._get_all_beams():
            b.section_type = bt

    def get_bounding_box(self) -> Tuple[np.ndarray, np.ndarray]:
        """AABB 바운딩박스 (min_xyz, max_xyz) 반환."""
        verts = self.get_world_corners()
        return verts.min(axis=0), verts.max(axis=0)

    def get_world_corners(self) -> np.ndarray:
        """바닥면 4 꼭지점 + 상단 4 꼭지점 = 8개 월드 좌표."""
        w = self.dimensions.get('width', 0)
        d = self.dimensions.get('depth', 0)
        h = self.dimensions.get('height', 0)

        # 앵커 오프셋 (앵커 기준으로 position이 어느 꼭지점인지)
        ax, ay = self._anchor_offset(w, d)

        # 로컬 8 꼭지점 (앵커 오프셋 적용)
        local_corners = []
        for z in [0, h]:
            for lx, ly in [(0, 0), (w, 0), (w, d), (0, d)]:
                local_corners.append((lx - ax, ly - ay, z))

        world = np.array([
            _rotate_local_to_world(lx, ly, lz, self.rotation, self.position)
            for lx, ly, lz in local_corners
        ])
        return world

    def _get_all_beams(self) -> list:
        """하위 부재 중 모든 보 목록 반환 (서브클래스에서 오버라이드)."""
        return []

    def get_snap_points(self) -> np.ndarray:
        """스냅 가능한 모든 점 반환 (바닥 꼭지점 + 보 중심/1/4)."""
        points = []
        # 바닥 4 꼭지점
        corners = self.get_world_corners()
        points.extend(corners[:4])
        # 보의 중심/1/4/3/4 지점
        for beam in self._get_all_beams():
            s = beam.start
            e = beam.end
            points.append((s + e) / 2.0)             # 중심
            points.append(s + (e - s) * 0.25)         # 1/4
            points.append(s + (e - s) * 0.75)         # 3/4
        if not points:
            return np.zeros((0, 3), dtype=np.float64)
        return np.array(points, dtype=np.float64)

    def _anchor_offset(self, w: float, d: float) -> Tuple[float, float]:
        """앵커 인덱스에 따른 로컬 오프셋."""
        if self.anchor == 0:   # 좌하
            return 0.0, 0.0
        elif self.anchor == 1: # 우하
            return w, 0.0
        elif self.anchor == 2: # 우상
            return w, d
        elif self.anchor == 3: # 좌상
            return 0.0, d
        return 0.0, 0.0


@dataclass
class Module(Component):
    """모듈: 기둥 4 + 하부보 4 + 상부보 4 + 슬래브 1 + 비내력 벽 4면(채움).

    [2026-05-24 3단계] wall_fills: 4면 비내력 벽(채움 패널, 두께 100, 시각 전용).
    순서 = [front(y-), back(y+), left(x-), right(x+)] → 개구부 face 'wall_0'~'wall_3'.
    하중·물량에 들어가지 않음(구조벽 채움과 동일 — 순수 메시). 개구부로 뚫거나
    면 전체를 덮어 그 면 벽을 시각적으로 제거할 수 있다.
    """
    columns: List[ColumnData] = field(default_factory=list)
    bottom_beams: List[BeamData] = field(default_factory=list)
    top_beams: List[BeamData] = field(default_factory=list)
    slab: Optional[SlabData] = None
    wall_fills: List["WallPanelData"] = field(default_factory=list)

    def generate_sub_components(self):
        """dimensions + position + rotation으로 하위 부재 자동 생성."""
        w = self.dimensions['width']    # X방향
        d = self.dimensions['depth']    # Y방향
        h = self.dimensions['height']   # Z방향
        s = _SECTION_W                  # 단면 크기 (카탈로그)
        half_s = s / 2.0                # 100mm (기둥 면까지 인셋)

        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # 기둥 4개 (코너, 하부보 하면 ~ 상부보 상면)
        self.columns = []
        col_bot = -half_s          # 하부보 하면
        col_top = h - half_s       # 상부보 상면 (h - s + half_s)
        for cx, cy in [(half_s, half_s), (w - half_s, half_s),
                        (w - half_s, d - half_s), (half_s, d - half_s)]:
            self.columns.append(ColumnData(
                base=to_world(cx, cy, col_bot),
                top=to_world(cx, cy, col_top),
            ))

        # 하부 보 4개 (z=0, 기둥 면 ~ 기둥 면)
        self.bottom_beams = []
        # X방향 보 2개 (y=half_s, y=d-half_s)
        for by in [half_s, d - half_s]:
            self.bottom_beams.append(BeamData(
                start=to_world(s, by, 0),
                end=to_world(w - s, by, 0),
            ))
        # Y방향 보 2개 (x=half_s, x=w-half_s)
        for bx in [half_s, w - half_s]:
            self.bottom_beams.append(BeamData(
                start=to_world(bx, s, 0),
                end=to_world(bx, d - s, 0),
            ))

        # 상부 보 4개 (z=h-s)
        top_z = h - s
        self.top_beams = []
        for by in [half_s, d - half_s]:
            self.top_beams.append(BeamData(
                start=to_world(s, by, top_z),
                end=to_world(w - s, by, top_z),
            ))
        for bx in [half_s, w - half_s]:
            self.top_beams.append(BeamData(
                start=to_world(bx, s, top_z),
                end=to_world(bx, d - s, top_z),
            ))

        # 슬래브 (상면 = 보 상면 = z=half_s, 하면 = z=half_s-150)
        slab_top = half_s  # 100mm (보 중심 z=0, 단면 ±100 → 상면 100)
        slab_bot = half_s - 150.0  # -50mm
        self.slab = SlabData(
            corners=np.array([
                to_world(s, s, slab_bot),
                to_world(w - s, s, slab_bot),
                to_world(w - s, d - s, slab_bot),
                to_world(s, d - s, slab_bot),
            ]),
            thickness=150.0,
        )

        # 비내력 벽 4면 (채움 패널, 두께 100, 시각 전용) — 기둥 사이, 보 사이 높이.
        # 면 코너 순서 [원점, +u, +u+v, +v] (u=면 길이, v=높이 z) — 개구부 면 로컬과 일치.
        wt = _NB_WALL_T
        wz0 = half_s
        wz1 = h - s - half_s
        self.wall_fills = [
            # wall_0 front (y = half_s), u = x
            WallPanelData(corners=np.array([
                to_world(s, half_s, wz0), to_world(w - s, half_s, wz0),
                to_world(w - s, half_s, wz1), to_world(s, half_s, wz1)]),
                thickness=wt),
            # wall_1 back (y = d - half_s), u = x
            WallPanelData(corners=np.array([
                to_world(s, d - half_s, wz0), to_world(w - s, d - half_s, wz0),
                to_world(w - s, d - half_s, wz1), to_world(s, d - half_s, wz1)]),
                thickness=wt),
            # wall_2 left (x = half_s), u = y
            WallPanelData(corners=np.array([
                to_world(half_s, s, wz0), to_world(half_s, d - s, wz0),
                to_world(half_s, d - s, wz1), to_world(half_s, s, wz1)]),
                thickness=wt),
            # wall_3 right (x = w - half_s), u = y
            WallPanelData(corners=np.array([
                to_world(w - half_s, s, wz0), to_world(w - half_s, d - s, wz0),
                to_world(w - half_s, d - s, wz1), to_world(w - half_s, s, wz1)]),
                thickness=wt),
        ]
        self._apply_beam_section_type()

    def _get_all_beams(self) -> list:
        return self.bottom_beams + self.top_beams


@dataclass
class FloorPanel(Component):
    """바닥패널: 보 4개 + 슬래브 (기둥 없음)."""
    edge_beams: List[BeamData] = field(default_factory=list)
    slab: Optional[SlabData] = None
    merged_wall_ids: List[int] = field(default_factory=list)  # 합체된 벽패널 ID

    def generate_sub_components(self):
        w = self.dimensions['width']
        d = self.dimensions['depth']
        h = self.dimensions.get('height', 200.0)
        s = _SECTION_W
        half_s = s / 2.0
        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # 가장자리 보 4개 (z=0)
        self.edge_beams = []
        for by in [half_s, d - half_s]:
            self.edge_beams.append(BeamData(
                start=to_world(s, by, 0), end=to_world(w - s, by, 0)))
        for bx in [half_s, w - half_s]:
            self.edge_beams.append(BeamData(
                start=to_world(bx, s, 0), end=to_world(bx, d - s, 0)))

        # 슬래브 (상면 = 보 상면 = z=half_s)
        slab_bot = half_s - 150.0  # -50mm
        self.slab = SlabData(
            corners=np.array([
                to_world(s, s, slab_bot), to_world(w - s, s, slab_bot),
                to_world(w - s, d - s, slab_bot), to_world(s, d - s, slab_bot),
            ]), thickness=150.0)
        self._apply_beam_section_type()

    def _get_all_beams(self) -> list:
        return self.edge_beams


@dataclass
class WallPanelData:
    """벽체 채움 (솔리드 판)."""
    corners: np.ndarray   # (4, 3)
    thickness: float


@dataclass
class StructWall(Component):
    """구조벽: 기둥 2 + 런너 2 + 벽체 채움."""
    columns: List[ColumnData] = field(default_factory=list)
    top_runner: Optional[BeamData] = None
    bottom_runner: Optional[BeamData] = None
    wall_fill: Optional[WallPanelData] = None
    merged_fp_id: Optional[int] = None  # 합체된 플로어패널 ID (None=독립)

    def generate_sub_components(self):
        w = self.dimensions['width']   # 벽 길이
        d = self.dimensions['depth']   # 벽 두께 (200mm)
        h = self.dimensions['height']  # 벽 높이 (3400mm)
        s = _SECTION_W
        half_s = s / 2.0
        half_d = d / 2.0
        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # 양쪽 기둥 (하부런너 하면 ~ 상부런너 상면)
        col_bot = -half_s
        col_top = h - half_s  # h - s + half_s
        self.columns = [
            ColumnData(base=to_world(half_s, half_d, col_bot),
                       top=to_world(half_s, half_d, col_top)),
            ColumnData(base=to_world(w - half_s, half_d, col_bot),
                       top=to_world(w - half_s, half_d, col_top)),
        ]

        # 런너 (하부/상부)
        self.bottom_runner = BeamData(
            start=to_world(s, half_d, 0), end=to_world(w - s, half_d, 0))
        self.top_runner = BeamData(
            start=to_world(s, half_d, h - s), end=to_world(w - s, half_d, h - s))

        # 벽체 채움 (하부런너 상면 ~ 상부런너 하면)
        # [2026-05-24] 채움 패널 두께는 비내력 벽 두께(100) 고정 — 모듈 벽과 통일.
        # 구조 두께(d=기둥 간격)·해석은 그대로, 채움 시각 두께만 100.
        self.wall_fill = WallPanelData(
            corners=np.array([
                to_world(s, half_d, half_s), to_world(w - s, half_d, half_s),
                to_world(w - s, half_d, h - s - half_s), to_world(s, half_d, h - s - half_s),
            ]), thickness=_NB_WALL_T)
        self._apply_beam_section_type()

    def _get_all_beams(self) -> list:
        beams = []
        if self.bottom_runner:
            beams.append(self.bottom_runner)
        if self.top_runner:
            beams.append(self.top_runner)
        return beams


@dataclass
class InteriorWall(Component):
    """내벽(비내력 칸막이벽) — 채움 판 1장. 기둥·보·런너 없음.

    [형상]
    - dimensions.width  = 벽 길이 L (사용자 입력)
    - dimensions.depth  = 벽 두께 t (기본 100, 고정)
    - dimensions.height = 벽 높이 h (기본 3400 = 단층 모듈 높이, 자동)

    채움 판은 길이 전체(0..L)에 걸쳐 서고, z 범위는 모듈 4면 벽과 같은
    레벨(half_s .. h-s-half_s)로 맞춰 같은 층 모듈 벽과 면이 일치한다.
    개구부(face='wall')를 뚫을 수 있다. 하중·물량 0 (순수 시각 메시).
    """
    wall_fill: Optional[WallPanelData] = None

    def generate_sub_components(self):
        w = self.dimensions['width']                 # 벽 길이
        d = self.dimensions.get('depth', _NB_WALL_T)  # 벽 두께 (100)
        h = self.dimensions.get('height', 3400.0)    # 벽 높이
        s = _SECTION_W                               # 200 — z 범위를 모듈 벽과 flush
        half_s = s / 2.0
        half_d = d / 2.0
        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # 채움 판 — 면 코너 [원점, +u, +u+v, +v] (u=길이 x, v=높이 z).
        # 두께 방향(y)은 중심선 half_d 한 평면, 메시가 thickness 만큼 양쪽 확장.
        wz0 = half_s
        wz1 = h - s - half_s
        self.wall_fill = WallPanelData(
            corners=np.array([
                to_world(0, half_d, wz0), to_world(w, half_d, wz0),
                to_world(w, half_d, wz1), to_world(0, half_d, wz1),
            ]),
            thickness=d,
        )

    def _get_all_beams(self) -> list:
        return []


@dataclass
class Vertical3Module(Component):
    """수직 3층 모듈: 한 칸이 3개 층을 차지하는 모듈.

    외형: 가로 3400 × 세로 3400 × 높이 10240 (= 3·3400 + 2·20).
    내부 구조:
      - 4 코너 기둥 (통기둥, 토폴로지에서 층 경계 노드로 분절됨)
      - 각 층 슬래브 받침보 4 (1F·2F·3F 바닥) + 옥상 프레임 보 4 (3F 상부)
        = 보 총 16 개. 단층 모듈처럼 "층마다 상부보+하부보 2줄" 두지 않음 —
        한 부재이므로 층 경계마다 슬래브 받침 1줄이면 충분.
      - 3 슬래브 (1F·2F·3F 바닥). 옥상 슬래브는 없음 — 필요하면 FloorPanel 별도 배치.

    [CoT] 층-층 거리 H = FLOOR_HEIGHT = 3420 mm.
    각 층 시작 z (모듈 로컬):
      - 1F 바닥보·슬래브: z=0,        1F 상부보: z=h-s=3200
      - 2F 바닥보·슬래브: z=H=3420,   2F 상부보: z=H+h-s=6620
      - 3F 바닥보·슬래브: z=2H=6840,  3F 상부보: z=2H+h-s=10040
    기둥은 z=-100(half_s 아래) ~ z=2H+h-half_s=10140 까지 연속.
    """
    columns: List[ColumnData] = field(default_factory=list)        # 4 (continuous)
    bottom_beams: List[List[BeamData]] = field(default_factory=list)  # [story][4]  슬래브 받침
    top_beams: List[BeamData] = field(default_factory=list)           # 4  옥상 프레임만
    slabs: List[SlabData] = field(default_factory=list)               # 3

    def generate_sub_components(self):
        # 가로/세로/단층높이 — 사용자 입력은 무시되고 강제 3400 으로 채움.
        # (controls.py 측 분기에서 이미 강제 처리되지만 안전망으로 한 번 더.)
        w = float(self.dimensions.get('width', 3400.0))
        d = float(self.dimensions.get('depth', 3400.0))
        h = 3400.0           # 단층 모듈 한 층의 높이 (보 단면 포함)
        gap = 20.0           # 모듈 내부 층-층 접합부 갭
        H = h + gap          # 층-층 거리 = 3420
        s = _SECTION_W
        half_s = s / 2.0

        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # ── 4 코너 기둥 (3층 통기둥, 분절은 토폴로지에서) ──
        col_bot = -half_s
        col_top = 2 * H + h - half_s   # = 6840 + 3300 = 10140
        self.columns = []
        for cx, cy in [(half_s, half_s), (w - half_s, half_s),
                       (w - half_s, d - half_s), (half_s, d - half_s)]:
            self.columns.append(ColumnData(
                base=to_world(cx, cy, col_bot),
                top=to_world(cx, cy, col_top),
            ))

        # ── 층별 슬래브 받침보 (1F·2F·3F 바닥, z = 0/H/2H) + 옥상 프레임 보 ──
        self.bottom_beams = []
        self.top_beams = []
        self.slabs = []
        for story in range(3):
            z_floor = story * H              # 0, 3420, 6840

            floor_beams: List[BeamData] = []
            for by in [half_s, d - half_s]:
                floor_beams.append(BeamData(
                    start=to_world(s, by, z_floor),
                    end=to_world(w - s, by, z_floor)))
            for bx in [half_s, w - half_s]:
                floor_beams.append(BeamData(
                    start=to_world(bx, s, z_floor),
                    end=to_world(bx, d - s, z_floor)))
            self.bottom_beams.append(floor_beams)

            # 슬래브 (1F·2F·3F 바닥) — 단층 모듈 패턴과 동일 (상면 z = z_floor + half_s).
            slab_bot_z = z_floor + half_s - 150.0
            self.slabs.append(SlabData(
                corners=np.array([
                    to_world(s, s, slab_bot_z),
                    to_world(w - s, s, slab_bot_z),
                    to_world(w - s, d - s, slab_bot_z),
                    to_world(s, d - s, slab_bot_z),
                ]),
                thickness=150.0,
            ))

        # ── 옥상 프레임 보 4 (3F 상부, z = 2H + h - s = 10040) ──
        # 슬래브 없는 단순 frame closure — 단층 모듈 최상층에 슬래브 안 깔았을 때
        # 천장보가 column tops 를 묶는 역할과 동일.
        roof_z = 2 * H + (h - s)             # 10040
        for by in [half_s, d - half_s]:
            self.top_beams.append(BeamData(
                start=to_world(s, by, roof_z),
                end=to_world(w - s, by, roof_z)))
        for bx in [half_s, w - half_s]:
            self.top_beams.append(BeamData(
                start=to_world(bx, s, roof_z),
                end=to_world(bx, d - s, roof_z)))
        self._apply_beam_section_type()


    def _get_all_beams(self) -> list:
        beams: List[BeamData] = []
        for fb in self.bottom_beams:
            beams.extend(fb)
        beams.extend(self.top_beams)
        return beams


@dataclass
class Core(Component):
    """RC 코어벽 — 한 장 단위. wide-column 모델용 단일 frame element 표현.

    [형상]
    - dimensions.width  = 벽 길이 L (StructWall 과 동일 컨벤션)
    - dimensions.depth  = 벽 두께 t (기본 300, 범위 200~500)
    - dimensions.height = 벽 높이 h (기본 3400, 단층 모듈과 동일)

    [구조 모델 (OpenSees, 페이즈 4)]
    - 벽 중앙 평면 좌표 (cx, cy) = (L/2, t/2) 에 수직 frame element 1 개.
    - 양 끝 노드: base = (cx, cy, 0), top = (cx, cy, h) — 모두 월드 좌표.
    - 단면: A = L·t, I_strong = (t·L³)/12, I_weak = (L·t³)/12 — 정직 계산 (Q35).
    - 재료: E=27000 MPa, G=E/2.4 (CONCRETE_E_MPA).
    - 1F 베이스 노드는 6DOF 완전고정. 각 층 노드는 다이어프램 master 에
      equalDOF(1,2,6).

    [멀티-층 / 슬래브]
    multi_floor.create_multi_floor_group 가 floor_index 0..N 총 N+1 장 자동 복제 +
    같은 group_id 에 CoreSlab N+1 장 자동 생성. 사용자 직접 배치는 코어벽만.
    """
    column: Optional[ColumnData] = None

    def generate_sub_components(self):
        w = self.dimensions['width']    # 벽 길이 L
        d = self.dimensions['depth']    # 벽 두께 t
        h = self.dimensions['height']   # 벽 높이

        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # 벽 중앙 평면 좌표 — wide-column frame 의 양 끝 노드 위치.
        cx = w / 2.0
        cy = d / 2.0

        # section_w / section_h 를 단면 폭 × 길이로 그대로 저장.
        # ColumnData 는 강관(SHS) 가정의 t 필드가 있지만 RC 는 솔리드라
        # 무관 — ops_builder 가 comp_type 으로 분기해 RC 단면식을 따로 적용.
        self.column = ColumnData(
            base=to_world(cx, cy, 0.0),
            top=to_world(cx, cy, h),
            section_w=w,
            section_h=d,
            section_t=d,  # RC 솔리드 — t 의미 없음, 단면식에서 미사용.
        )

    def _get_all_beams(self) -> list:
        return []


@dataclass
class CoreSlab(Component):
    """RC 코어 슬래브 — 보 없는 순수 RC 판. 코어벽 그룹의 종속 부재.

    [형상]
    - 평면 (width × depth) = 같은 그룹·같은 층 모든 코어벽을 덮는 최소 직사각형
      (Q50: bounding box). multi_floor 가 자동 계산해서 dimensions 에 박음.
    - 두께: 기본 180 mm, 사용자 조절 가능.
    - 보 없음 (Q51: 카세트 형식 아님 — 철골 미사용).

    [생성 책임]
    사용자 직접 배치 X. multi_floor 의 코어 슬래브 자동 생성 로직이 책임.
    코어벽 추가/삭제 시 같은 그룹 슬래브 재계산.

    [위치 규약]
    position.z = 슬래브 **하면** z (= 그 층 코어벽 윗면 - 슬래브 두께).
    슬래브 상면 = position.z + thickness = k·H + MODULE_HEIGHT_MM (옥상 코어 윗단).
    """
    slab: Optional[SlabData] = None

    def generate_sub_components(self):
        w = self.dimensions['width']
        d = self.dimensions['depth']
        t = self.dimensions.get('thickness', 180.0)

        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # 슬래브 하면 = position.z (월드).
        self.slab = SlabData(
            corners=np.array([
                to_world(0, 0, 0),
                to_world(w, 0, 0),
                to_world(w, d, 0),
                to_world(0, d, 0),
            ]),
            thickness=t,
        )

    def _get_all_beams(self) -> list:
        return []


@dataclass
class CantileverBeam(Component):
    """캔틸레버보: 단일 보."""
    beam: Optional[BeamData] = None

    def generate_sub_components(self):
        w = self.dimensions['width']   # 보 길이
        s = _SECTION_W
        half_s = s / 2.0
        ax, ay = self._anchor_offset(w, s)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        self.beam = BeamData(
            start=to_world(0, half_s, 0), end=to_world(w, half_s, 0))
        self._apply_beam_section_type()

    def _get_all_beams(self) -> list:
        return [self.beam] if self.beam else []


@dataclass
class CantileverSlab(Component):
    """캔틸레버 슬래브: 3변 보 + 슬래브."""
    beams: List[BeamData] = field(default_factory=list)
    slab: Optional[SlabData] = None

    def generate_sub_components(self):
        w = self.dimensions['width']   # 돌출 길이
        d = self.dimensions['depth']   # 폭
        s = _SECTION_W
        half_s = s / 2.0
        ax, ay = self._anchor_offset(w, d)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        # 3변 보 (접합면 제외: x=0 쪽이 모듈 접합면).
        # 좌·우변 빔(축이 길이 방향)은 공칭 길이 w 그대로 보존 → 끝점 x=w.
        # 끝변 빔(축이 폭 방향)만 x = w - half_s 로 안쪽 이동.
        # 이렇게 하면 끝변 빔 단면 외측면이 정확히 x=w 에 맞아 100mm 돌출 제거,
        # 좌·우변 빔 길이는 1500 유지. 끝변은 좌·우변의 내부점에서 T 접합.
        x_far = w - half_s
        self.beams = [
            BeamData(start=to_world(0, half_s, 0),
                     end=to_world(w, half_s, 0)),             # 좌변 (길이 w)
            BeamData(start=to_world(x_far, half_s, 0),
                     end=to_world(x_far, d - half_s, 0)),     # 끝변 (안쪽 이동)
            BeamData(start=to_world(w, d - half_s, 0),
                     end=to_world(0, d - half_s, 0)),         # 우변 (길이 w)
        ]

        # 슬래브 (상면 = 보 상면 = z=half_s)
        # 끝변(닫힌 ㅣ변, x=w 쪽)도 긴 변과 동일하게 단면폭 s 만큼 안쪽으로 들여
        # 끝변 보 안쪽 면(x=w-s)에 맞춘다. x=0 은 모듈 접합면(보 없음)이라 그대로 둔다.
        slab_bot = half_s - 150.0
        self.slab = SlabData(
            corners=np.array([
                to_world(0, s, slab_bot), to_world(w - s, s, slab_bot),
                to_world(w - s, d - s, slab_bot), to_world(0, d - s, slab_bot),
            ]), thickness=150.0)
        self._apply_beam_section_type()

    def _get_all_beams(self) -> list:
        return self.beams


@dataclass
class MidBeam(Component):
    """중간보: 단일 보 (모듈 내부 보강용)."""
    beam: Optional[BeamData] = None

    def generate_sub_components(self):
        w = self.dimensions['width']   # 보 길이
        s = _SECTION_W
        half_s = s / 2.0
        ax, ay = self._anchor_offset(w, s)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        self.beam = BeamData(
            start=to_world(0, half_s, 0), end=to_world(w, half_s, 0))
        self._apply_beam_section_type()

    def _get_all_beams(self) -> list:
        return [self.beam] if self.beam else []


@dataclass
class MidColumn(Component):
    """중간기둥: 단일 기둥."""
    column: Optional[ColumnData] = None

    def generate_sub_components(self):
        h = self.dimensions.get('height', 3400.0)
        s = _SECTION_W
        half_s = s / 2.0
        ax, ay = self._anchor_offset(s, s)
        rot = self.rotation
        pos = self.position

        to_world = make_local_to_world(ax, ay, rot, pos)
        self.column = ColumnData(
            base=to_world(half_s, half_s, -half_s),
            top=to_world(half_s, half_s, h - half_s))


# ── 액션 (Undo용) ───────────────────────────────────────────

@dataclass
class Action:
    """Undo 가능한 행동."""
    action_type: str   # 'place', 'delete', 'move'
    data: dict         # 복원에 필요한 데이터


# ── 씬 관리 ──────────────────────────────────────────────────

class Scene:
    """부재 컬렉션 + Undo 스택."""

    def __init__(self):
        self.components: Dict[int, Component] = {}
        self.next_id: int = 1
        self.undo_stack: List[Action] = []
        # 실(Room) 컬렉션 — 부재와 별개. 2단계 실 배치(폴리곤 영역+용도).
        # Room import 는 순환참조 방지를 위해 add_room 내부에서 지연 임포트.
        self.rooms: Dict[int, "object"] = {}
        self.next_room_id: int = 1
        # 접합부 오버라이드 — 사용자가 컴포넌트 간 접합을 직접 제거/변경/추가한
        # 기록. 부재·실과 별개로 직렬화되며 undo 스택과도 분리(접합 편집은 자체
        # 토글 모드에서만 발생). JointOverride 목록.
        self.joint_overrides: List["object"] = []
        # 불러오기 직후 접합 탭 첫 진입 시 "저장본/새 자동계산" 1회 질문 대기
        # 플래그(런타임 전용 — 직렬화 안 함). 불러오기가 True 로 set, 질문 후 clear.
        self._joint_overrides_pending_choice: bool = False

    def add_room(self, room) -> int:
        """실 추가, ID 반환(undo 스택에는 넣지 않음 — 부재와 별개 흐름)."""
        room.id = self.next_room_id
        self.next_room_id += 1
        self.rooms[room.id] = room
        return room.id

    def remove_room(self, room_id: int):
        """실 삭제, 삭제된 실 반환(없으면 None)."""
        return self.rooms.pop(room_id, None)

    # ── 접합부 오버라이드 (2026-05-25) ───────────────────────────
    # 컴포넌트 간 접합의 사용자 변경 계층. 같은 평면 위치(xy)의 모든 층 접합에
    # 자동 적용된다(다층 복제는 group_id 공유 + 같은 xy 반복). 자세한 매칭
    # 규칙은 model/joint_override.py 참조.

    def set_joint_override(self, ov) -> None:
        """접합 오버라이드 추가. 같은 접합을 가리키는 동종 오버라이드는 교체.
        - remove/rigid/pin: 같은 접합의 기존 변경을 교체(사용자 최신 우선).
        - add: 같은 두 점의 기존 신규 접합을 교체(중복 누적 방지)."""
        from modular_3d.model.joint_override import same_joint
        if ov.kind in ('remove', 'rigid', 'pin'):
            ov_rid = getattr(ov, 'rule_id', '')
            ov_single = getattr(ov, 'single_layer', False)
            ov_za = float(getattr(ov, 'z_a', 0.0))
            ov_zb = float(getattr(ov, 'z_b', 0.0))

            def _dup(o):
                if getattr(o, 'kind', '') not in ('remove', 'rigid', 'pin'):
                    return False
                if not same_joint(o, ov.a_xy, ov.b_xy):
                    return False
                if getattr(o, 'rule_id', '') != ov_rid:
                    return False
                # 한쪽이라도 '이 층만'이면 층(z)까지 같아야 같은 접합으로 교체.
                if ov_single or getattr(o, 'single_layer', False):
                    oza = float(getattr(o, 'z_a', 0.0))
                    ozb = float(getattr(o, 'z_b', 0.0))
                    zf = abs(oza - ov_za) <= 50.0 and abs(ozb - ov_zb) <= 50.0
                    zb = abs(oza - ov_zb) <= 50.0 and abs(ozb - ov_za) <= 50.0
                    if not (zf or zb):
                        return False
                return True

            self.joint_overrides = [o for o in self.joint_overrides
                                    if not _dup(o)]
        elif ov.kind == 'add':
            self.joint_overrides = [
                o for o in self.joint_overrides
                if not (getattr(o, 'kind', '') == 'add'
                        and same_joint(o, ov.a_xy, ov.b_xy))
            ]
        self.joint_overrides.append(ov)

    def clear_joint_override_at(self, a_xy, b_xy) -> bool:
        """노드쌍에 매칭되는 기존-접합 오버라이드 제거(자동 규칙 복귀).
        하나라도 제거되면 True."""
        from modular_3d.model.joint_override import same_joint
        before = len(self.joint_overrides)
        self.joint_overrides = [
            o for o in self.joint_overrides
            if not (getattr(o, 'kind', '') in ('remove', 'rigid', 'pin')
                    and same_joint(o, a_xy, b_xy))
        ]
        return len(self.joint_overrides) < before

    def clear_joint_overrides(self) -> None:
        """모든 접합 오버라이드 제거(불러오기 시 '새로 자동계산' 선택용)."""
        self.joint_overrides = []

    def add_component(self, comp: Component) -> int:
        """부재 추가, ID 반환."""
        comp.id = self.next_id
        self.next_id += 1
        self.components[comp.id] = comp
        self.undo_stack.append(Action(
            action_type='place',
            data={'component_id': comp.id},
        ))
        return comp.id

    def remove_component(self, comp_id: int) -> Optional[Component]:
        """부재 삭제, 삭제된 부재 반환."""
        comp = self.components.pop(comp_id, None)
        return comp

    def undo(self) -> Optional[Action]:
        """마지막 액션 취소. 취소된 액션 반환 (실제 복원은 Controller에서)."""
        if not self.undo_stack:
            return None
        action = self.undo_stack.pop()
        if action.action_type == 'place':
            self.remove_component(action.data['component_id'])
        # delete / group_* : Controller에서 복원 처리
        return action

    # ── 그룹 액션 컨텍스트 (2026-05-12) ───────────────────────
    # begin_group_action 호출 후 발생한 모든 push 를 end_group_action 에서
    # 모아 단일 그룹 액션으로 묶음. 사용자 시점 "한 번의 액션 = 한 번의 undo"
    # 보장.

    def begin_group_action(self):
        """그룹 액션 시작 — undo_stack 의 현재 길이를 마크."""
        self._group_action_mark = len(self.undo_stack)

    def end_group_action(self, action_type: str, data: Optional[dict] = None):
        """그룹 액션 종료 — 시작 이후 push 된 모든 액션을 _inner_actions 로 묶고
        단일 action_type 액션 1 개 push.
        """
        mark = getattr(self, '_group_action_mark', None)
        if mark is None:
            return
        pushed = list(self.undo_stack[mark:])
        del self.undo_stack[mark:]
        if not pushed:
            # 묶을 게 없으면 그룹 액션도 push 안 함
            self._group_action_mark = None
            return
        merged = dict(data) if data else {}
        merged['_inner_actions'] = pushed
        self.undo_stack.append(Action(action_type=action_type, data=merged))
        self._group_action_mark = None

    # ── 벽패널-플로어패널 합체 ──

    def merge_wall_to_fp(self, wall_id: int, fp_id: int) -> bool:
        """벽패널을 플로어패널에 합체. 성공 시 True."""
        wall = self.components.get(wall_id)
        fp = self.components.get(fp_id)
        if not isinstance(wall, StructWall) or not isinstance(fp, FloorPanel):
            return False
        if wall.merged_fp_id is not None:
            return False  # 이미 합체됨
        wall.merged_fp_id = fp_id
        if wall_id not in fp.merged_wall_ids:
            fp.merged_wall_ids.append(wall_id)
        self.undo_stack.append(Action(
            action_type='merge',
            data={'wall_id': wall_id, 'fp_id': fp_id},
        ))
        return True

    def unmerge_wall_from_fp(self, wall_id: int) -> bool:
        """벽패널을 플로어패널에서 분리. 성공 시 True."""
        wall = self.components.get(wall_id)
        if not isinstance(wall, StructWall) or wall.merged_fp_id is None:
            return False
        fp_id = wall.merged_fp_id
        fp = self.components.get(fp_id)
        wall.merged_fp_id = None
        if fp is not None and isinstance(fp, FloorPanel):
            if wall_id in fp.merged_wall_ids:
                fp.merged_wall_ids.remove(wall_id)
        self.undo_stack.append(Action(
            action_type='unmerge',
            data={'wall_id': wall_id, 'fp_id': fp_id},
        ))
        return True

    def find_nearest_fp_for_wall(self, wall_id: int,
                                  tol: float = 50.0) -> Optional[int]:
        """벽패널 하부 런너와 가장 가까운 FP 가장자리 보를 가진 FP ID 반환.

        tol: 보 중심선 간 최대 거리 (mm). 기본 50mm.
        """
        wall = self.components.get(wall_id)
        if not isinstance(wall, StructWall) or wall.bottom_runner is None:
            return None
        w_mid = (wall.bottom_runner.start + wall.bottom_runner.end) / 2.0

        best_dist = tol
        best_fp_id: Optional[int] = None
        for comp in self.components.values():
            if not isinstance(comp, FloorPanel):
                continue
            for beam in comp.edge_beams:
                b_mid = (beam.start + beam.end) / 2.0
                dist = float(np.linalg.norm(w_mid - b_mid))
                if dist < best_dist:
                    best_dist = dist
                    best_fp_id = comp.id
        return best_fp_id

    @property
    def component_count(self) -> int:
        return len(self.components)


# ── ComponentType → 클래스/한글이름 단일 dispatch (2026-05-08 단순화) ───
# scene_io, multi_floor, mesh_builder, controls 등에 흩어진 8 분기 isinstance / dict
# 들을 본 모듈로 통합. 새 부재 타입 추가 시 본 두 dict 만 갱신하면 됨.

TYPE_TO_CLASS: Dict[ComponentType, type] = {
    ComponentType.MODULE:           Module,
    ComponentType.FLOOR_PANEL:      FloorPanel,
    ComponentType.STRUCT_WALL:      StructWall,
    ComponentType.INTERIOR_WALL:    InteriorWall,
    ComponentType.CANTILEVER_BEAM:  CantileverBeam,
    ComponentType.CANTILEVER_SLAB:  CantileverSlab,
    ComponentType.MID_BEAM:         MidBeam,
    ComponentType.MID_COLUMN:       MidColumn,
    ComponentType.VERTICAL_MODULE:  Vertical3Module,
    ComponentType.CORE:             Core,
    ComponentType.CORE_SLAB:        CoreSlab,
}

TYPE_NAMES: Dict[ComponentType, str] = {
    ComponentType.MODULE:           '모듈',
    ComponentType.FLOOR_PANEL:      '바닥패널',
    ComponentType.STRUCT_WALL:      '구조벽',
    ComponentType.INTERIOR_WALL:    '내벽',
    ComponentType.CANTILEVER_BEAM:  '캔틸레버보',
    ComponentType.CANTILEVER_SLAB:  '캔틸레버슬래브',
    ComponentType.MID_BEAM:         '중간보',
    ComponentType.MID_COLUMN:       '중간기둥',
    ComponentType.VERTICAL_MODULE:  '수직 3층 모듈',
    ComponentType.CORE:             'RC 코어',
    ComponentType.CORE_SLAB:        '코어 슬래브',
}


def instantiate(comp_type: ComponentType, position: np.ndarray,
                dims: dict, rotation: int = 0, anchor: int = 0,
                beam_section_type: str = 'shs') -> Component:
    """Component 서브클래스 생성 + 하위 부재 자동 생성.

    [통합 — 단계 2 (2026-05-08)]
    이전: ui/controls_geom.create_component, model/multi_floor._instantiate 가
         같은 8 분기 isinstance 체인을 각자 보유.
    현재: 본 함수 하나로 통합. TYPE_TO_CLASS dict 룩업 → 인스턴스화.

    beam_section_type: 'shs'|'h' — 생성 직후 generate_sub_components 에서 보로 전파.
    """
    cls = TYPE_TO_CLASS.get(comp_type, Module)  # 미지의 타입은 Module 폴백
    comp = cls(
        id=0, comp_type=comp_type, position=position.copy(),
        rotation=rotation, dimensions=dict(dims), anchor=anchor,
        beam_section_type=beam_section_type,
    )
    comp.generate_sub_components()
    return comp


def effective_beam_section_type(comp, scene) -> str:
    """부재의 유효 보 단면 타입 — 종속/합체는 부모/패널을 따라간다.

    - 캔틸레버보·캔틸레버슬래브·중간보: parent_id 부모의 타입.
    - 바닥패널에 합체된 구조벽(merged_fp_id): 그 바닥패널의 타입.
    - 그 외(모듈·바닥패널·수직3층모듈·독립 구조벽): 자기 beam_section_type.
    """
    ct = comp.comp_type
    if ct in (ComponentType.CANTILEVER_BEAM, ComponentType.CANTILEVER_SLAB,
              ComponentType.MID_BEAM):
        pid = getattr(comp, 'parent_id', 0)
        parent = scene.components.get(pid) if pid else None
        if parent is not None:
            return getattr(parent, 'beam_section_type', 'shs')
    if ct == ComponentType.STRUCT_WALL and getattr(comp, 'merged_fp_id', None):
        fp = scene.components.get(comp.merged_fp_id)
        if fp is not None:
            return getattr(fp, 'beam_section_type', 'shs')
    return getattr(comp, 'beam_section_type', 'shs')
