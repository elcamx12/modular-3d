"""해석 토폴로지 빌드 (중심선 모델 + 컴포넌트별 독립 노드).

단순화된 버전 — 직접강성법 연속보 해석용.
  - Module: 8노드 (바닥 4 + 천장 4), 12부재 (보 8 + 기둥 4)
    기둥은 바닥보~천장보 사이 1개 (stub 없음).
  - 컴포넌트 간 노드 자동 공유 X (독립 노드).
  - 패널류 완전 겹침 통합, 부분 겹침 RuntimeError.

좌표계 (model.py 규약 준수):
  - Module: position.z = 바닥보 중심선. 천장보 = z + (h - s).
    기둥은 바닥보(z=0)에서 천장보(z=h-s)까지.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import numpy as np

from modular_3d.model import (
    Scene, Component, ComponentType,
    Module, FloorPanel, StructWall,
    CantileverBeam, CantileverSlab, MidBeam, MidColumn,
    Vertical3Module,
    Core, CoreSlab,
    _rotate_local_to_world,
    make_local_to_world,
)
from modular_3d.analysis.constants import (
    NODE_MERGE_TOL_MM, LINE_COLINEAR_TOL, OVERLAP_TOL_MM,
    SECTION_W_MM, SECTION_H_MM, SECTION_T_MM,
    CORE_TRUSS_GRID_MM,
    MODULE_HEIGHT_MM, MODULE_JOINT_GAP_MM, FLOOR_HEIGHT,
)
from modular_3d.카탈로그.geometry import CORE_WALL_DEFAULT_THICKNESS_MM
from modular_3d.analysis._utils import round_key as _round_key


# ── 데이터 구조 ──────────────────────────────────────────────

@dataclass
class AnalysisNode:
    """해석용 절점 (월드 좌표 mm)."""
    id: int
    coord: np.ndarray   # (3,) float64
    source_comp_id: int

    def __repr__(self):
        x, y, z = self.coord
        return f"Node({self.id}, ({x:.1f},{y:.1f},{z:.1f}), comp={self.source_comp_id})"


@dataclass
class AnalysisMember:
    """해석용 부재 (1차원 보-기둥 요소).

    [참고] kind='shell' 잔재는 모두 폐기됨 (2026-05-13 코어 트러스 전환).
    n3, n4 필드는 옛 셸 호환 흔적으로 0 으로 유지.
    """
    id: int
    n1: int                 # start 노드 ID
    n2: int                 # end 노드 ID
    kind: str               # 'beam' | 'column'
    role: str               # 모듈상부보/모듈하부보/모듈기둥/캔틸레버보/core_* 등
    section_w: float
    section_h: float
    section_t: float
    # 보 단면 형상 — 'shs'(각형강관) | 'h'(H형강). 보만 의미(기둥/truss 는 'shs').
    # 소유 부재 comp.beam_section_type 에서 build_analysis_model 이 설정.
    section_type: str = 'shs'
    source_comp_ids: List[int] = field(default_factory=list)
    merge_group: Optional[str] = None
    # 셸 전용 추가 노드. kind != 'shell' 이면 0.
    n3: int = 0
    n4: int = 0
    # [2026-05-18 split sub-member 등록]
    # joint_rules R05/R06 등 보 분할로 만들어진 sub 부재 표식. True 이면:
    #   - load_calculator 가 자중·슬래브 분배 순회에서 제외 (원본 mid 가 이미
    #     자중을 잡고 sub 들은 sub_tags 로 그 자중을 분배받음).
    #   - quantity_takeoff 는 design_result.member_to_group 매칭이 None 이라
    #     자연 skip (원본 mid 만 group 매칭됨).
    #   - self_check 는 sub 도 am.members 끝점이라 free 판정에서 자동 인정.
    is_split_sub: bool = False
    # sub 일 때만 비-None — 원본 부재 ID 추적.
    parent_member_id: Optional[int] = None
    # [2026-05-31 단면 설계 수렴] 단면 설계 탭이 그룹 선정 단면을 write-back 한 결과.
    #   - SHSSection / HSection 객체. None 이면 미설계 → ops_builder._section_props 가
    #     공칭(치수 기반 SHS / 공칭 H)으로 계산(하위호환).
    #   - 설정되면 그 단면의 A·I·J 로 강성이 계산되어 재해석에 반영(수렴).
    design_section: Optional[object] = None

    def __repr__(self):
        return (f"Member({self.id}, {self.kind}, {self.n1}->{self.n2}, "
                f"role={self.role}, src={self.source_comp_ids})")


@dataclass
class Diaphragm:
    """단일 컴포넌트(또는 종속 캔틸 포함)의 바닥 다이어프램 그룹.

    OpenSees rigidDiaphragm(perpDirn=3, master, *slaves) 로 면내 강체 구속.
    master 노드는 슬레이브 좌표의 기하 중심(z=평균)에 새로 생성, model.nodes
    에 정식 등록된다(고유 gid).

    polygon: 시각화용 단순 폴리곤 (centroid 기준 각도 정렬). 4~N 점.
    """
    comp_id: int                              # 소속 본체 컴포넌트 id
    z: float = 0.0
    master_node_id: int = 0
    slave_node_ids: List[int] = field(default_factory=list)
    polygon: List[np.ndarray] = field(default_factory=list)
    # True 이면 시각화에서 숨김(천장 다이어프램 등 mechanism 방지용 백엔드 전용).
    hidden: bool = False


@dataclass
class AnalysisModel:
    nodes: Dict[int, AnalysisNode] = field(default_factory=dict)
    members: Dict[int, AnalysisMember] = field(default_factory=dict)
    comp_to_members: Dict[int, List[int]] = field(default_factory=dict)
    comp_to_nodes: Dict[int, List[int]] = field(default_factory=dict)
    diaphragms: List[Diaphragm] = field(default_factory=list)
    # 캔틸레버 슬래브의 anchor 코너 노드(부모 패널/모듈과 맞닿는 쪽 2 코너).
    # far 코너(자유단)만 인접 모듈 보에 연결되어야 하므로 매칭에서 제외 용도.
    cantilever_anchor_node_ids: set = field(default_factory=set)
    _length_cache: Dict[int, float] = field(default_factory=dict, repr=False)
    # [성능 2026-05-18] role 별 부재 mid 인덱스 — joint_rules R01~R09 가 매번
    # am.members.values() 전체를 role 필터링하던 9 회 순회를 캐시 조회로 대체.
    # 무효화 정책: members 변경 후 invalidate_indices() 호출.
    _role_index: Dict[str, List[int]] = field(default_factory=dict, repr=False)
    _role_prefix_index: Dict[str, List[int]] = field(default_factory=dict, repr=False)

    def invalidate_indices(self) -> None:
        """members 변경 후 캐시 무효화 — split sub 추가/제거 직후 호출."""
        self._role_index.clear()
        self._role_prefix_index.clear()

    def _build_role_indices(self) -> None:
        """role / role_prefix 인덱스 일괄 빌드."""
        self._role_index.clear()
        self._role_prefix_index.clear()
        for mid, m in self.members.items():
            role = m.role or ''
            self._role_index.setdefault(role, []).append(mid)
            # 'module_' / 'wall_' / 'core_' / 'cantilever' 등 prefix 캐시.
            for prefix in ('module_', 'wall_', 'core_', 'cantilever',
                           'mid_', 'floor_'):
                if role.startswith(prefix):
                    self._role_prefix_index.setdefault(prefix, []).append(mid)
                    break

    def members_by_role(self, role: str) -> List[int]:
        """role 정확 매칭 mid 리스트 (lazy 캐시). 빈 결과 = 빈 리스트."""
        if not self._role_index:
            self._build_role_indices()
        return self._role_index.get(role, [])

    def members_by_role_prefix(self, prefix: str) -> List[int]:
        """role.startswith(prefix) 매칭 mid 리스트 (lazy 캐시)."""
        if not self._role_prefix_index:
            self._build_role_indices()
        return self._role_prefix_index.get(prefix, [])

    def get_member_length(self, mem_id: int) -> float:
        cached = self._length_cache.get(mem_id)
        if cached is not None:
            return cached
        m = self.members[mem_id]
        L = float(np.linalg.norm(self.nodes[m.n2].coord - self.nodes[m.n1].coord))
        self._length_cache[mem_id] = L
        return L

    def base_node_ids(self) -> List[int]:
        if not self.nodes:
            return []
        zmin = min(n.coord[2] for n in self.nodes.values())
        return [nid for nid, n in self.nodes.items()
                if abs(n.coord[2] - zmin) <= NODE_MERGE_TOL_MM]


# ── 컴포넌트별 중심선 추출 ──────────────────────────────────

_SECTION_KW = dict(section_w=SECTION_W_MM, section_h=SECTION_H_MM, section_t=SECTION_T_MM)


@dataclass
class _LocalExtract:
    """단일 컴포넌트의 중심선 추출 결과 (글로벌 ID 할당 전)."""
    coords: Dict[Tuple[int, int, int], np.ndarray]
    members: List[dict]
    # 캔틸레버 슬래브 anchor 키(부모 면 쪽 2 코너) — 후속 매칭에서 제외용.
    anchor_keys: List[Tuple[int, int, int]] = field(default_factory=list)
    merge_group: Optional[str] = None


def _extract_module(comp: Module) -> _LocalExtract:
    """Module → 8 노드 / 12 부재 (4 코너 × 2 z레벨, 기둥 1개씩)."""
    w = comp.dimensions['width']
    d = comp.dimensions['depth']
    h = comp.dimensions['height']
    s = SECTION_W_MM
    half_s = s / 2.0

    ax, ay = comp._anchor_offset(w, d)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    corner_xys = [
        (half_s, half_s),
        (w - half_s, half_s),
        (w - half_s, d - half_s),
        (half_s, d - half_s),
    ]
    # 2 z 레벨: 바닥보(z=0), 천장보(z=h-s)
    z_levels = [0.0, h - s]

    coords: Dict[Tuple[int, int, int], np.ndarray] = {}
    node_keys: Dict[Tuple[int, int], Tuple[int, int, int]] = {}

    for ci, (cx, cy) in enumerate(corner_xys):
        for zi, lz in enumerate(z_levels):
            world = to_world(cx, cy, lz)
            key = _round_key(world)
            coords[key] = world
            node_keys[(ci, zi)] = key

    members: List[dict] = []

    # 기둥: 4 코너, 바닥보→천장보 (1개씩)
    for ci in range(4):
        members.append(dict(
            n1_key=node_keys[(ci, 0)],
            n2_key=node_keys[(ci, 1)],
            kind='column',
            role='module_column',
            **_SECTION_KW,
        ))

    # 하부보 z_idx=0 (사각형 둘레 4개)
    for i in range(4):
        members.append(dict(
            n1_key=node_keys[(i, 0)],
            n2_key=node_keys[((i + 1) % 4, 0)],
            kind='beam',
            role='module_bottom_beam',
            **_SECTION_KW,
        ))
    # 상부보 z_idx=1
    for i in range(4):
        members.append(dict(
            n1_key=node_keys[(i, 1)],
            n2_key=node_keys[((i + 1) % 4, 1)],
            kind='beam',
            role='module_top_beam',
            **_SECTION_KW,
        ))

    return _LocalExtract(coords=coords, members=members, merge_group=None)


def _extract_floor_panel(comp: FloorPanel) -> _LocalExtract:
    """FloorPanel → 4 노드 / 4 요소 (가장자리 보만)."""
    w = comp.dimensions['width']
    d = comp.dimensions['depth']
    s = SECTION_W_MM
    half_s = s / 2.0
    ax, ay = comp._anchor_offset(w, d)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    corner_xys = [
        (half_s, half_s),
        (w - half_s, half_s),
        (w - half_s, d - half_s),
        (half_s, d - half_s),
    ]
    coords: Dict[Tuple[int, int, int], np.ndarray] = {}
    node_keys: Dict[int, Tuple[int, int, int]] = {}
    for ci, (cx, cy) in enumerate(corner_xys):
        world = to_world(cx, cy, 0.0)
        key = _round_key(world)
        coords[key] = world
        node_keys[ci] = key

    members: List[dict] = []
    for i in range(4):
        members.append(dict(
            n1_key=node_keys[i],
            n2_key=node_keys[(i + 1) % 4],
            kind='beam',
            role='floor_edge_beam',
            **_SECTION_KW,
        ))
    return _LocalExtract(coords=coords, members=members, merge_group='panel')


def _extract_struct_wall(comp: StructWall) -> _LocalExtract:
    """StructWall → 4 노드 / 4 요소 (2 코너 × 2 z레벨, 기둥 1개씩 + 런너 2)."""
    w = comp.dimensions['width']
    d = comp.dimensions['depth']
    h = comp.dimensions['height']
    s = SECTION_W_MM
    half_s = s / 2.0
    half_d = d / 2.0
    ax, ay = comp._anchor_offset(w, d)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    corner_xys = [(half_s, half_d), (w - half_s, half_d)]
    z_levels = [0.0, h - s]

    coords: Dict[Tuple[int, int, int], np.ndarray] = {}
    node_keys: Dict[Tuple[int, int], Tuple[int, int, int]] = {}
    for ci, (cx, cy) in enumerate(corner_xys):
        for zi, lz in enumerate(z_levels):
            world = to_world(cx, cy, lz)
            key = _round_key(world)
            coords[key] = world
            node_keys[(ci, zi)] = key

    members: List[dict] = []
    # 기둥 2 × 1
    for ci in range(2):
        members.append(dict(
            n1_key=node_keys[(ci, 0)],
            n2_key=node_keys[(ci, 1)],
            kind='column',
            role='wall_column',
            **_SECTION_KW,
        ))
    # 런너 (하/상)
    # 합체 상태이면 bottom_runner 생략 (FP 보가 대신)
    skip_bottom = getattr(comp, 'merged_fp_id', None) is not None
    for zi, role in [(0, 'wall_bottom_runner'), (1, 'wall_top_runner')]:
        if zi == 0 and skip_bottom:
            continue
        members.append(dict(
            n1_key=node_keys[(0, zi)],
            n2_key=node_keys[(1, zi)],
            kind='beam',
            role=role,
            **_SECTION_KW,
        ))
    return _LocalExtract(coords=coords, members=members, merge_group='panel')


def _extract_cantilever_beam(comp: CantileverBeam) -> _LocalExtract:
    """CantileverBeam → 2 노드 / 1 요소."""
    w = comp.dimensions['width']
    s = SECTION_W_MM
    half_s = s / 2.0
    ax, ay = comp._anchor_offset(w, s)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    p1 = to_world(0, half_s, 0)
    p2 = to_world(w, half_s, 0)
    k1, k2 = _round_key(p1), _round_key(p2)
    # [이력 2026-05-14] anchor_keys 명시 시도 → 취소.
    # comp.anchor + rotation 조합 결과로 local x=0 면이 anchor 인지 자유단인지
    # 단정할 수 없다(scene1: anchor=1·rot=90 에선 local x=w 가 실제 anchor).
    # _consolidate 의 fallback (200mm 이내 best 자동 탐색) 이 더 robust 하므로
    # anchor_keys 빈 채로 두고, 자유단 식별은 joint_rules 에서 "모듈 노드와
    # 통합 안 된 끝점" 으로 판정.
    return _LocalExtract(
        coords={k1: p1, k2: p2},
        members=[dict(n1_key=k1, n2_key=k2, kind='beam', role='cantilever_beam', **_SECTION_KW)],
        merge_group=None,
    )


def _extract_cantilever_slab(comp: CantileverSlab) -> _LocalExtract:
    """CantileverSlab → 4 노드 / 3 요소 (U 자 보 3개)."""
    w = comp.dimensions['width']
    d = comp.dimensions['depth']
    s = SECTION_W_MM
    half_s = s / 2.0
    ax, ay = comp._anchor_offset(w, d)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    # 해석 모델은 모두 끝점 x=w 로 두어 캔틸레버보(같은 직선상에 별도 컴포넌트
    # 로 존재 가능, 길이 w)와 완전 겹침이 성립하도록 한다. 시각화(model.py)에서
    # 끝변만 x=w-half_s 로 안쪽 이동하는 처리는 단면 돌출 표시용일 뿐, 해석 부재
    # 중심선은 끝점 매칭이 더 중요하므로 x=w 유지.
    p_a = to_world(0, half_s, 0)
    p_b = to_world(w, half_s, 0)
    p_c = to_world(w, d - half_s, 0)
    p_d = to_world(0, d - half_s, 0)
    keys = [_round_key(p) for p in (p_a, p_b, p_c, p_d)]
    coords = {k: p for k, p in zip(keys, (p_a, p_b, p_c, p_d))}
    members = [
        dict(n1_key=keys[0], n2_key=keys[1], kind='beam', role='cantilever_slab_beam', **_SECTION_KW),
        dict(n1_key=keys[1], n2_key=keys[2], kind='beam', role='cantilever_slab_beam', **_SECTION_KW),
        dict(n1_key=keys[2], n2_key=keys[3], kind='beam', role='cantilever_slab_beam', **_SECTION_KW),
    ]
    # anchor 코너 = local x=0 면(p_a, p_d) — 부모 패널/모듈과 맞닿는 쪽.
    # 이쪽 코너는 부모 컴포넌트와 horizontal_adjacent / merged_overlap 으로
    # 자연스럽게 연결되므로, 추가로 모듈 보 분할 매칭은 하지 않는다.
    return _LocalExtract(coords=coords, members=members, merge_group=None,
                         anchor_keys=[keys[0], keys[3]])


def _extract_mid_beam(comp: MidBeam) -> _LocalExtract:
    w = comp.dimensions['width']
    s = SECTION_W_MM
    half_s = s / 2.0
    ax, ay = comp._anchor_offset(w, s)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    # [2026-06-07] 해석 와이어프레임은 양끝을 축방향으로 half_s(100mm)씩 연장한다.
    #   중간보 width w 는 기둥 '안쪽 면' 사이 거리라, 끝점이 기둥 중심선에서
    #   half_s 만큼 모자라 떠 버린다(주구조 미연결 → 불안정 메커니즘 → singular).
    #   양끝을 기둥 중심선까지 늘려 노드 병합으로 자연 연결시킨다.
    p1 = to_world(-half_s, half_s, 0)
    p2 = to_world(w + half_s, half_s, 0)
    k1, k2 = _round_key(p1), _round_key(p2)
    return _LocalExtract(
        coords={k1: p1, k2: p2},
        members=[dict(n1_key=k1, n2_key=k2, kind='beam', role='mid_beam', **_SECTION_KW)],
        merge_group=None,
    )


def _extract_mid_column(comp: MidColumn) -> _LocalExtract:
    h = comp.dimensions.get('height', float(MODULE_HEIGHT_MM))
    s = SECTION_W_MM
    half_s = s / 2.0
    ax, ay = comp._anchor_offset(s, s)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    p1 = to_world(half_s, half_s, 0.0)
    p2 = to_world(half_s, half_s, h - s)
    k1, k2 = _round_key(p1), _round_key(p2)
    return _LocalExtract(
        coords={k1: p1, k2: p2},
        members=[dict(n1_key=k1, n2_key=k2, kind='column', role='mid_column', **_SECTION_KW)],
        merge_group=None,
    )


def _extract_vertical_module(comp: Vertical3Module) -> _LocalExtract:
    """Vertical3Module → 16 노드 / 28 부재.

    [CoT] z 레벨 4개 (코너당 4 노드 = 16 노드 총):
      level 0: z=0              (1F 바닥보 + 1F 슬래브 받침)
      level 1: z=H=3420         (2F 바닥보 + 2F 슬래브 받침, 1F 천장 역할 겸함)
      level 2: z=2H=6840        (3F 바닥보 + 3F 슬래브 받침, 2F 천장 역할 겸함)
      level 3: z=2H+h-s=10040   (3F 상부 / 옥상 프레임)
    기둥: 코너당 3 분절(level 0→1→2→3) × 4 코너 = 12개.
    보: 4 z-level × 4 둘레 = 16개. 단층 모듈처럼 층 사이마다 2줄(하부+상부)
        둘 필요 없음 — 한 부재이므로 슬래브 받침 1줄로 충분.

    role 명을 단층 모듈과 동일하게 'module_column'/'module_bottom_beam'/
    'module_top_beam' 으로 부여 — interface 검출 등 기존 로직이 자동으로
    동작하도록. level 0~2 = bottom_beam, level 3 = top_beam.
    """
    # width/depth fallback 3400.0 은 정사각 모듈 우연한 일치 (전용 상수 없음).
    w = comp.dimensions.get('width', 3400.0)
    d = comp.dimensions.get('depth', 3400.0)
    h = float(MODULE_HEIGHT_MM)
    H = float(FLOOR_HEIGHT)   # = MODULE_HEIGHT_MM + MODULE_JOINT_GAP_MM = 3420
    s = SECTION_W_MM
    half_s = s / 2.0

    ax, ay = comp._anchor_offset(w, d)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    corner_xys = [
        (half_s, half_s),
        (w - half_s, half_s),
        (w - half_s, d - half_s),
        (half_s, d - half_s),
    ]
    z_levels = [
        0.0,             # 1F floor
        H,               # 2F floor   (3420)
        2 * H,           # 3F floor   (6840)
        2 * H + (h - s), # 옥상 frame (10040)
    ]

    coords: Dict[Tuple[int, int, int], np.ndarray] = {}
    node_keys: Dict[Tuple[int, int], Tuple[int, int, int]] = {}
    for ci, (cx, cy) in enumerate(corner_xys):
        for zi, lz in enumerate(z_levels):
            world = to_world(cx, cy, lz)
            key = _round_key(world)
            coords[key] = world
            node_keys[(ci, zi)] = key

    members: List[dict] = []

    # 기둥 — 각 코너 3 분절 (각 층 슬래브 받침 z 레벨에 분기 노드).
    for ci in range(4):
        for zi in range(len(z_levels) - 1):
            members.append(dict(
                n1_key=node_keys[(ci, zi)],
                n2_key=node_keys[(ci, zi + 1)],
                kind='column',
                role='module_column',
                **_SECTION_KW,
            ))

    # 보 — 각 z 레벨 둘레 4개. zi 0~2 = 슬래브 받침(bottom_beam), zi 3 = 옥상(top_beam).
    for zi in range(len(z_levels)):
        role = 'module_top_beam' if zi == len(z_levels) - 1 else 'module_bottom_beam'
        for i in range(4):
            members.append(dict(
                n1_key=node_keys[(i, zi)],
                n2_key=node_keys[((i + 1) % 4, zi)],
                kind='beam',
                role=role,
                **_SECTION_KW,
            ))

    return _LocalExtract(coords=coords, members=members, merge_group=None)


def _extract_core(comp: Core) -> _LocalExtract:
    """Core (RC 코어벽) → 고정 경계용 단순 선 프레임.

    [정책 2026-06-03 — 트러스 격자 폐기 → 고정 경계]
    코어를 강성 요소가 아니라 '바닥처럼 안 움직이는 고정 경계'로 본다
    (ops_builder._step_fix_base_nodes 가 코어 노드를 전부 6DOF 고정). 따라서
    면내 강성을 표현하던 트러스 등가 격자(column/runner/truss_v/h/d)를 버리고,
    R09 접합 인터페이스 + 고정 노드 확보용 단순 선만 만든다. 짧은벽·ㅗ자 연결에서
    격자가 mechanism 을 만들어 발산하던 문제가 원천 제거된다.

    [형상] 코어벽 길이방향 중심선(로컬 y=half_t)을 3 z 레벨에 수평선으로 생성:
      - z=0            : 바닥 (role core_bottom_runner)
      - z=h-SECTION_W  : 모듈 천장보 레벨 (role core_ceiling_runner) — 천장 접합
                         사영용. 모듈 천장보(z=h-SECTION_W)와 같은 평면이라 R09 성립.
      - z=h+gap        : 코어 슬래브 받침 (role core_top_runner)
    + 양 끝 수직 기둥선(role core_column, z 레벨 사이 분절)으로 형상·고정 노드 확보.
    강성은 무의미(전부 고정)하나 부재 등록/단면이 깨지지 않게 유효값을 유지한다.

    [짧은 벽] L<=t 면 수평선 길이가 0 이하 → 수직 기둥선 1줄(x 중앙)만 만든다.
    인접 벽·슬래브 노드와 _round_key 통합 또는 ㄷ자 빈공간 연결선으로 결합된다.
    """
    L = float(comp.dimensions['width'])
    t = float(comp.dimensions['depth'])
    h = float(comp.dimensions['height'])

    ax, ay = comp._anchor_offset(L, t)
    rot = comp.rotation
    pos = comp.position
    to_world = make_local_to_world(ax, ay, rot, pos)
    half_t = t / 2.0

    # z 레벨 — 바닥 / 슬래브받침. 중복 제거 후 정렬(예외 방어).
    # [2026-06-03] 천장보 레벨(z=h-SECTION_W) 수평선은 코어벽마다 독립이라 박스
    # 모서리에서 끊긴다 → 코어 슬래브가 천장보 레벨에도 폐곡선(core_ceiling_runner)을
    # 만들어 박스로 닫는다(_extract_core_slab). 여기선 바닥·슬래브받침 수평선 + 기둥만.
    z_floor = 0.0
    z_top = h + MODULE_JOINT_GAP_MM
    run_role = {z_floor: 'core_bottom_runner', z_top: 'core_top_runner'}
    z_levels = sorted({z_floor, z_top})

    coords: Dict[Tuple[int, int, int], np.ndarray] = {}
    members: List[dict] = []

    def _key(x: float, z: float) -> Tuple[int, int, int]:
        w = to_world(x, half_t, z)
        k = _round_key(w)
        coords[k] = w
        return k

    inner_width = L - t
    x0 = half_t           # 길이방향 끝(기둥 중심) — 외곽 두께 안쪽.
    x1 = L - half_t

    if inner_width > 1.0:
        # 정상 벽 — 각 z 레벨 수평선 + 양 끝 수직 기둥선.
        for z in z_levels:
            members.append(dict(
                n1_key=_key(x0, z), n2_key=_key(x1, z),
                kind='beam', role=run_role.get(z, 'core_ceiling_runner'),
                section_w=t, section_h=SECTION_W_MM, section_t=t,
            ))
        for xe in (x0, x1):
            for za, zb in zip(z_levels[:-1], z_levels[1:]):
                members.append(dict(
                    n1_key=_key(xe, za), n2_key=_key(xe, zb),
                    kind='column', role='core_column',
                    section_w=t, section_h=t, section_t=t,
                ))
    else:
        # 짧은 벽 — 수직 기둥선 1줄(x 중앙)만(수평선은 길이 0이라 생략).
        xc = L / 2.0
        for za, zb in zip(z_levels[:-1], z_levels[1:]):
            members.append(dict(
                n1_key=_key(xc, za), n2_key=_key(xc, zb),
                kind='column', role='core_column',
                section_w=t, section_h=t, section_t=t,
            ))

    return _LocalExtract(coords=coords, members=members, merge_group=None)


def _extract_core_slab_polygon(comp: CoreSlab, poly) -> _LocalExtract:
    """폴리곤 CoreSlab → N 코너 노드 + N 변 보(+천장 폐곡선).

    사각형 분기와 같은 z 규칙·역할(core_slab_beam / core_ceiling_runner)을 쓰되,
    벽두께 인셋은 생략(그린 폴리곤 변 그대로 — shapely 부재). 코어벽 상단 노드와의
    결합은 노드 병합 허용오차 + 다이어프램에 맡긴다.
    """
    t = float(comp.dimensions.get('thickness', 180.0))
    t_wall = float(comp.dimensions.get('wall_thickness',
                                       CORE_WALL_DEFAULT_THICKNESS_MM))
    W = float(comp.dimensions['width'])
    D = float(comp.dimensions['depth'])
    ax, ay = comp._anchor_offset(W, D)
    to_world = make_local_to_world(ax, ay, comp.rotation, comp.position)
    is_bottom_slab = float(comp.position[2]) < 0.0
    z_top_local = t if is_bottom_slab else t + MODULE_JOINT_GAP_MM

    n = len(poly)
    keys = []
    coords = {}
    for (px, py) in poly:
        p = to_world(float(px), float(py), z_top_local)
        k = _round_key(p)
        coords[k] = p
        keys.append(k)
    b_w, b_h = 1000.0, t
    members: List[dict] = []
    for i in range(n):
        j = (i + 1) % n
        if keys[i] == keys[j]:
            continue
        members.append(dict(n1_key=keys[i], n2_key=keys[j], kind='beam',
                            role='core_slab_beam',
                            section_w=b_w, section_h=b_h, section_t=t))
    if not is_bottom_slab:
        zc = z_top_local - (SECTION_W_MM + MODULE_JOINT_GAP_MM)
        qkeys = []
        for (px, py) in poly:
            p = to_world(float(px), float(py), zc)
            k = _round_key(p)
            coords[k] = p
            qkeys.append(k)
        for i in range(n):
            j = (i + 1) % n
            if qkeys[i] == qkeys[j]:
                continue
            members.append(dict(n1_key=qkeys[i], n2_key=qkeys[j], kind='beam',
                                role='core_ceiling_runner',
                                section_w=t_wall, section_h=SECTION_W_MM,
                                section_t=t_wall))
    return _LocalExtract(coords=coords, members=members, merge_group=None)


def _extract_core_slab(comp: CoreSlab) -> _LocalExtract:
    """CoreSlab → 외곽 보 격자 (4 변). 폴리곤이면 _extract_core_slab_polygon 위임.

    [정책 2026-05-17 X-대각 제거]
    셸(ShellMITC4 격자) 폐기. 코어 슬래브 면내 강성은 다이어프램(rigidDiaphragm)이
    전담하므로 가운데 X 대각(c0-mid-c2, c1-mid-c3) 및 mid 노드 제거.
    4 코너 노드 + 4 변 보만 유지.

    [부재 단면]
    - 보 폭: 1000mm (대표 폭)
    - 보 높이: 슬래브 두께 t
    """
    _poly = comp.dimensions.get('polygon')
    if _poly and len(_poly) >= 3:
        return _extract_core_slab_polygon(comp, _poly)
    W = float(comp.dimensions['width'])
    D = float(comp.dimensions['depth'])
    t = float(comp.dimensions.get('thickness', 180.0))
    t_wall = float(comp.dimensions.get('wall_thickness',
                                       CORE_WALL_DEFAULT_THICKNESS_MM))

    inset = t_wall / 2.0
    W_inner = max(W - t_wall, 100.0)
    D_inner = max(D - t_wall, 100.0)

    ax, ay = comp._anchor_offset(W, D)
    rot = comp.rotation
    pos = comp.position

    to_world = make_local_to_world(ax, ay, rot, pos)
    # [2026-05-18 R09 정렬 정책 — 옵션 B]
    # 이전: z_top_local = t (슬래브 윗면).
    # 문제: 슬래브 두께 t 가 모듈 조인트 갭(20mm) 과 다르면 frame 노드 z 가
    #       모듈 천장 코너 z 와 정확히 어긋남 → R09 (b) 슬래브 변 사영이
    #       _Z_TOL(5mm) 안에 못 들어와 거의 결합 안 됨 (실측 0.05%).
    # 해결: 슬래브 frame 노드 z 를 슬래브 윗면 + MODULE_JOINT_GAP_MM (= 모듈
    #       천장 코너 평면) 으로 이동. 슬래브 두께 변경에 자동 대응.
    # 부수: 슬래브 frame 자중·강성 작용 위치가 슬래브 윗면보다 20mm 위로
    #       올라가지만 모멘트 팔 영향 미미. 다이어프램은 같은 평면 → 일관성 향상.
    #
    # [함정 — 천장 슬래브 ↔ 1층 바닥 슬래브 구분 필수]
    # 위 +갭 정책은 '모듈 천장에 얹히는 천장 슬래브' 전용이다. 1층 바닥 코어
    # 슬래브(multi_floor.regenerate_core_slabs 가 pos.z=-thickness 로 추가)는
    # 상면이 지면(z=0)에 맞아야 하므로 갭을 더하면 노드가 20mm 떠 코어벽 바닥
    # (z=0)과 어긋난다. 바닥 슬래브는 pos.z<0 으로만 식별되므로 그때는 갭 제외.
    is_bottom_slab = float(pos[2]) < 0.0
    z_top_local = t if is_bottom_slab else t + MODULE_JOINT_GAP_MM

    # 4 노드 — 코너만 (다이어프램이 면내 강성 전담 → 중앙 mid 노드 불필요)
    p_c0 = to_world(inset,             inset,             z_top_local)
    p_c1 = to_world(inset + W_inner,   inset,             z_top_local)
    p_c2 = to_world(inset + W_inner,   inset + D_inner,   z_top_local)
    p_c3 = to_world(inset,             inset + D_inner,   z_top_local)
    k_c0 = _round_key(p_c0)
    k_c1 = _round_key(p_c1)
    k_c2 = _round_key(p_c2)
    k_c3 = _round_key(p_c3)
    coords = {k_c0: p_c0, k_c1: p_c1, k_c2: p_c2, k_c3: p_c3}

    # 단면: 폭 1000mm × 높이 t
    b_w = 1000.0
    b_h = t

    members: List[dict] = [
        # 4 변 보만 (X 대각 제거 — 다이어프램이 대체)
        dict(n1_key=k_c0, n2_key=k_c1, kind='beam', role='core_slab_beam',
             section_w=b_w, section_h=b_h, section_t=t),
        dict(n1_key=k_c1, n2_key=k_c2, kind='beam', role='core_slab_beam',
             section_w=b_w, section_h=b_h, section_t=t),
        dict(n1_key=k_c2, n2_key=k_c3, kind='beam', role='core_slab_beam',
             section_w=b_w, section_h=b_h, section_t=t),
        dict(n1_key=k_c3, n2_key=k_c0, kind='beam', role='core_slab_beam',
             section_w=b_w, section_h=b_h, section_t=t),
    ]
    # [2026-06-03] 천장보 레벨 폐곡선 — 천장 슬래브면 모듈 천장보 레벨(슬래브 받침
    # − SECTION_W − gap = 코어벽 base + h − SECTION_W)에도 같은 footprint 4변 보를
    # 만들어 박스로 닫는다. 코어벽 천장보 수평선이 모서리에서 끊기던 문제를 없애고,
    # R09 천장 접합(모듈 천장보 사영)이 이 폐곡선 위에서 성립한다.
    if not is_bottom_slab:
        zc = z_top_local - (SECTION_W_MM + MODULE_JOINT_GAP_MM)
        q = [to_world(inset, inset, zc),
             to_world(inset + W_inner, inset, zc),
             to_world(inset + W_inner, inset + D_inner, zc),
             to_world(inset, inset + D_inner, zc)]
        kq = [_round_key(p) for p in q]
        for i in range(4):
            coords[kq[i]] = q[i]
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
            members.append(dict(
                n1_key=kq[a], n2_key=kq[b], kind='beam',
                role='core_ceiling_runner',
                section_w=t_wall, section_h=SECTION_W_MM, section_t=t_wall))
    return _LocalExtract(coords=coords, members=members, merge_group=None)


_EXTRACTORS = {
    Module: _extract_module,
    FloorPanel: _extract_floor_panel,
    StructWall: _extract_struct_wall,
    CantileverBeam: _extract_cantilever_beam,
    CantileverSlab: _extract_cantilever_slab,
    MidBeam: _extract_mid_beam,
    MidColumn: _extract_mid_column,
    Vertical3Module: _extract_vertical_module,
    Core: _extract_core,
    CoreSlab: _extract_core_slab,
}


def _extract_one(comp: Component) -> Optional[_LocalExtract]:
    fn = _EXTRACTORS.get(type(comp))
    return fn(comp) if fn else None


# ── (삭제됨) _merge_adjacent_module_nodes ────────────────────
# 모듈 간 수평 접합이 없으므로 노드 병합 제거.
# 각 모듈은 독립 노드를 유지한다.


def _merge_duplicate_members(model: AnalysisModel) -> None:
    """동일 양단 노드를 가진 중복 부재 통합 (노드 병합 후 발생)."""
    pair_index: Dict[Tuple, int] = {}
    to_remove: List[int] = []

    for mid in sorted(model.members.keys()):
        m = model.members[mid]
        key = (m.kind, tuple(sorted([m.n1, m.n2])))
        if key in pair_index:
            existing = model.members[pair_index[key]]
            for sid in m.source_comp_ids:
                if sid not in existing.source_comp_ids:
                    existing.source_comp_ids.append(sid)
            to_remove.append(mid)
        else:
            pair_index[key] = mid

    if not to_remove:
        return

    removed_set = set(to_remove)
    for mid in to_remove:
        del model.members[mid]
    for cid in model.comp_to_members:
        model.comp_to_members[cid] = [
            m for m in model.comp_to_members[cid] if m not in removed_set
        ]


# ── 부재 통합 / 부분겹침 검출 ───────────────────────────────


# 플로어패널 꼭지점 ↔ 모듈 기둥/보/캔틸레버 매칭 허용오차.
# constants.py 의 FP_SUPPORT_TOL_XY/Z 가 단일 진실 원천.
from modular_3d.analysis.constants import FP_SUPPORT_TOL_XY, FP_SUPPORT_TOL_Z
from modular_3d._utils.debug import dprint


def _merge_panel_overlaps_and_check(model: AnalysisModel) -> None:
    """완전 겹친 부재 통합 (패널↔패널, 패널↔모듈) + 부분 겹침 검사.

    [성능 2026-05-18]
    이전 구현은 부재 N 개에 대해 모든 쌍 N(N-1)/2 를 colinear 판정 →
    O(N²). 사용자 scene 기준 약 1.7 초로 build_analysis_model 의 95 %.

    새 구현은 같은 직선 위에 놓인 부재끼리만 비교하면 충분하다는 사실을 이용:
      - 각 부재를 (정규화 방향벡터, 원점 수선의 발 좌표) 키로 그룹핑
      - 그룹 내에서만 1D 사영 좌표 t 정렬 후 인접 쌍만 비교
      - 정렬된 상태에서 tlo_j ≥ thi_i 가 되면 이후 모두 안 겹침 → 조기 탈출
    실측 1.7 s → 0.01 s 미만 (170× 가속).

    [정확성 보존]
    - 직선 키의 방향 양자화는 round 소수점 6 자리 — LINE_COLINEAR_TOL(1e-3) 보다
      1000 배 정밀. 토런스 경계에서 두 부재가 다른 그룹으로 분리될 가능성은
      축정렬·축회전 패턴인 정상 모델에서는 발생 안 함. 음성 테스트로 확인.
    - 완전 겹침 통합(1단계) 은 기존 dict 기반 그대로 — N 선형이라 변경 무용.
    """
    from collections import defaultdict
    members = list(model.members.values())
    nodes = model.nodes

    # 1) 완전 겹침 통합 — 모든 부재의 좌표 인덱스를 만든 뒤, 패널 부재가
    #    기존 부재와 겹치면 패널 부재를 삭제 (모듈 부재가 우선 생존).
    survivors_to_remove: List[int] = []
    coord_pair_index: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], int] = {}
    for m in members:
        if m.merge_group == 'panel':
            continue
        c1 = _round_key(nodes[m.n1].coord)
        c2 = _round_key(nodes[m.n2].coord)
        key = tuple(sorted([c1, c2]))
        coord_pair_index[key] = m.id
    for m in members:
        if m.merge_group != 'panel':
            continue
        c1 = _round_key(nodes[m.n1].coord)
        c2 = _round_key(nodes[m.n2].coord)
        key = tuple(sorted([c1, c2]))
        if key in coord_pair_index:
            existing_id = coord_pair_index[key]
            existing = model.members[existing_id]
            for sid in m.source_comp_ids:
                if sid not in existing.source_comp_ids:
                    existing.source_comp_ids.append(sid)
            survivors_to_remove.append(m.id)
        else:
            coord_pair_index[key] = m.id
    for mid in survivors_to_remove:
        del model.members[mid]
    removed_set = set(survivors_to_remove)
    if removed_set:
        for cid in model.comp_to_members:
            model.comp_to_members[cid] = [
                m for m in model.comp_to_members[cid] if m not in removed_set
            ]
    members = list(model.members.values())

    # 2) 부분 겹침 검사 — 같은 직선 그룹 사전 분할 후 그룹 내만 비교.
    # 검사 제외:
    #   - shell: 면 요소라 1D 겹침 검사 무의미
    #   - core_*: 자동 생성된 코어 frame, 슬래브 변 frame 과 부분 겹침이 의도된 정상.
    def _skip_overlap_check(m) -> bool:
        if m.kind == 'shell':
            return True
        if m.role.startswith('core_'):
            return True
        return False

    def _line_key(p1: np.ndarray, p2: np.ndarray):
        """부재의 직선 식별 키 + 정규화 방향 + 길이 반환.

        방향벡터 부호는 첫 0 아닌 성분이 양수가 되도록 통일 → u 와 -u 가
        같은 키로 모임. 직선의 원점 수선의 발(foot)을 1mm 정수로 양자화.
        """
        d = p2 - p1
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            return None
        u = d / L
        # 부호 통일
        for v in u:
            if abs(v) > 1e-9:
                if v < 0:
                    u = -u
                break
        foot = p1 - float(np.dot(p1, u)) * u
        u_q = (round(float(u[0]), 6),
               round(float(u[1]), 6),
               round(float(u[2]), 6))
        f_q = (int(round(float(foot[0]))),
               int(round(float(foot[1]))),
               int(round(float(foot[2]))))
        return (u_q, f_q), u, L

    # 그룹: 직선 키 → [(member, p1, p2, u_norm, length), ...]
    groups: Dict[Tuple, List[Tuple]] = defaultdict(list)
    for m in members:
        if _skip_overlap_check(m):
            continue
        p1 = nodes[m.n1].coord
        p2 = nodes[m.n2].coord
        ki = _line_key(p1, p2)
        if ki is None:
            continue
        key, u, L = ki
        groups[key].append((m, p1, p2, u, L))

    # 그룹별 1D 사영 정렬 후 인접 쌍만 비교 (조기 탈출 포함)
    for items in groups.values():
        if len(items) < 2:
            continue
        # 공통 origin = 첫 부재의 p1, 공통 방향 = 첫 부재의 u
        _, op1, _, ou, _ = items[0]
        spans = []  # (tlo, thi, m, p1, p2)
        for m, p1, p2, _u, _L in items:
            t1 = float(np.dot(p1 - op1, ou))
            t2 = float(np.dot(p2 - op1, ou))
            spans.append((min(t1, t2), max(t1, t2), m, p1, p2))
        spans.sort(key=lambda x: x[0])

        for i in range(len(spans)):
            tlo_i, thi_i, ai, pa1, pa2 = spans[i]
            for j in range(i + 1, len(spans)):
                tlo_j, thi_j, bj, pb1, pb2 = spans[j]
                # 정렬되어 있으므로 tlo_j 가 thi_i 보다 충분히 크면 이후 모두 안 겹침
                if tlo_j >= thi_i - OVERLAP_TOL_MM:
                    break
                ov_lo = max(tlo_i, tlo_j)
                ov_hi = min(thi_i, thi_j)
                ov = ov_hi - ov_lo
                if ov > OVERLAP_TOL_MM:
                    same = (abs(tlo_i - tlo_j) <= OVERLAP_TOL_MM and
                            abs(thi_i - thi_j) <= OVERLAP_TOL_MM)
                    if same:
                        continue
                    raise RuntimeError(
                        f"부분 겹침(케이스 B) 검출 — 해석 불가.\n"
                        f"  부재 A: {ai}\n"
                        f"           {pa1.tolist()} -> {pa2.tolist()}\n"
                        f"  부재 B: {bj}\n"
                        f"           {pb1.tolist()} -> {pb2.tolist()}\n"
                        f"  겹친 길이: {ov:.2f} mm\n"
                        f"  → 패널 배치를 수정하세요 (완전 겹침 또는 완전 분리만 허용)."
                    )


# ── 메인 빌드 ────────────────────────────────────────────────

def build_analysis_model(scene: Scene) -> AnalysisModel:
    """Scene → AnalysisModel."""
    # [2026-05-25 A7 수정] 종속(캔틸·중간보)·합체 벽패널의 보 단면 타입은
    # 부모/패널을 따라가야 한다. comp.beam_section_type 직접 사용은 UI 동기화가
    # 선행돼야만 옳아(불러오기 후 미동기화 시 어긋남) effective 로 항상 부모 추종.
    from modular_3d.model import effective_beam_section_type
    model = AnalysisModel()
    next_node_id = 1
    next_mem_id = 1

    for comp in scene.components.values():
        local = _extract_one(comp)
        if local is None:
            continue

        local_key_to_gid: Dict[Tuple[int, int, int], int] = {}
        for key, coord in local.coords.items():
            gid = next_node_id
            next_node_id += 1
            local_key_to_gid[key] = gid
            model.nodes[gid] = AnalysisNode(
                id=gid, coord=coord.copy(), source_comp_id=comp.id,
            )
            model.comp_to_nodes.setdefault(comp.id, []).append(gid)

        # 캔틸레버 슬래브 anchor 코너 글로벌 ID 등록
        for ak in local.anchor_keys:
            if ak in local_key_to_gid:
                model.cantilever_anchor_node_ids.add(local_key_to_gid[ak])

        for md in local.members:
            n1 = local_key_to_gid[md['n1_key']]
            n2 = local_key_to_gid[md['n2_key']]
            if n1 == n2:
                continue
            # 셸 요소: 4 노드 + kind='shell'. n3_key/n4_key 가 dict 에 있음.
            n3 = 0
            n4 = 0
            if md['kind'] == 'shell':
                n3 = local_key_to_gid[md['n3_key']]
                n4 = local_key_to_gid[md['n4_key']]
            mid = next_mem_id
            next_mem_id += 1
            # 보(kind='beam')만 소유 부재의 유효 단면 타입을 따른다(종속/합체는
            # 부모/패널 추종). 기둥/truss 는 항상 각형강관.
            sec_type = (effective_beam_section_type(comp, scene)
                        if md['kind'] == 'beam' else 'shs')
            model.members[mid] = AnalysisMember(
                id=mid, n1=n1, n2=n2,
                kind=md['kind'], role=md['role'],
                section_w=md['section_w'], section_h=md['section_h'], section_t=md['section_t'],
                section_type=sec_type,
                source_comp_ids=[comp.id],
                merge_group=local.merge_group,
                n3=n3, n4=n4,
            )
            model.comp_to_members.setdefault(comp.id, []).append(mid)

    # [2026-05-13 종속 관계 정책]
    # 종속 관계로 얽힌 부재의 인접 노드를 같은 nid 로 통합 → 같은 컴포넌트 내부의
    # 자연 강접합처럼 동작. _split_hosts / _resolve_* 호출 전에 처리해 후속 단계가
    # 통합된 nid 를 기준으로 작동하게 한다.
    # 대상:
    #   1) 캔틸레버 종속 (sub_index>0) anchor 노드 = 부모 모듈 같은 좌표 노드
    #   2) 합체 벽-FP (merged_fp_id) 벽 베이스 노드 = FP 같은 코너 노드
    #   3) 코어 다층 적층 (같은 group_id 다른 floor_index) 위층 base = 아래층 top
    # 4) 코어벽 모서리는 좌표 자체가 다름 (셸 두께 t/2 안쪽 격자) → 별도 결합기 유지.
    _consolidate_dependent_nodes(model, scene)

    # 중간보·중간기둥 끝점이 모듈 보(또는 다른 host 보) 내부에 떨어지면 host 를
    # 끝점에서 분할하고 노드를 공유 — mid_* 가 모듈에 구조적으로 종속되도록.
    _split_hosts_for_mid_endpoints(model)


    # 완전 겹침 통합 + 부분 겹침 검사 (좌표 기반, 노드 독립)
    _merge_panel_overlaps_and_check(model)
    _merge_duplicate_members(model)

    # 컴포넌트별 바닥 다이어프램 그룹 생성 (rigidDiaphragm 입력용).
    # 모든 통합/병합 완료 **이후** 에 호출 — 그래야 슬레이브 nid 가 안정적.
    _build_diaphragms(model, scene)
    return model


# ── 다이어프램 그룹 빌드 ─────────────────────────────────────


def _build_diaphragms(model: AnalysisModel, scene: Scene) -> None:
    """컴포넌트 단위 바닥 다이어프램 그룹 생성.

    [정책 2026-05-17]
    - 다이어프램 대상 본체: Module(바닥 1 레벨), Vertical3Module(층별 3 레벨,
      옥상 제외), FloorPanel(자체 1 레벨), CoreSlab(자체 1 레벨).
    - 천장은 다이어프램 없음(보 격자만으로 횡강성 표현, 과대평가 방지).
    - 종속 캔틸레버 슬래브(CantileverSlab with parent_id>0)는 부모 본체의
      같은 z 레벨 다이어프램에 슬레이브로 합쳐진다.
    - 각 그룹마다 기하 중심(centroid)에 master 노드를 새로 생성하여
      model.nodes 에 등록(고유 gid). ops_builder 가 rigidDiaphragm 등록.

    [CoT 분기]
      1) 본체 컴포넌트 순회 → (comp_id, z 레벨) 별 슬레이브 nid 수집
      2) 종속 캔틸 슬래브 합치기 — parent_id 의 다이어프램 그룹에 nid 추가
      3) 각 그룹마다 centroid 계산 + master 노드 생성 + polygon 정렬
    """
    from collections import defaultdict
    Z_BUCKET = 50.0  # 같은 z 레벨로 묶을 허용오차(mm)

    # 그룹 키: (comp_id, z_bucket_int) — 값: slave nid 리스트
    groups: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    group_z: Dict[Tuple[int, int], float] = {}  # 대표 z (평균)
    group_hidden: Dict[Tuple[int, int], bool] = {}  # 시각화 숨김 플래그

    # [2026-06-03] CoreSlab 제외 — 코어는 고정 경계라 다이어프램 불필요. 코어
    # 슬래브는 R09 접합용 선(4변 보)으로만 두고, 그 노드는 코어 고정에 포함된다.
    diaphragm_body_types = (Module, FloorPanel, Vertical3Module)

    # 1) 본체 컴포넌트별 z 레벨 그룹화
    # [정책 2026-05-18 바닥만 다이어프램]
    # 사용자 사양: 바닥에만 다이어프램. 천장은 보 격자 + truss 면외 fix +
    # z_route 회전 fix 로 mechanism 방지. 천장 자동 다이어프램 추가 분기 제거.
    for cid, comp in scene.components.items():
        if not isinstance(comp, diaphragm_body_types):
            continue
        nids = model.comp_to_nodes.get(cid, [])
        if not nids:
            continue
        zs_sorted = sorted({float(model.nodes[n].coord[2]) for n in nids})
        if not zs_sorted:
            continue
        # 대상 z 레벨 결정:
        # - Module: 바닥 z 만
        # - Vertical3Module: 옥상 제외한 3 개 층 바닥 z 만
        # - FloorPanel / CoreSlab: 자체 1 z 레벨만
        if isinstance(comp, Vertical3Module):
            target_zs = [(z, False) for z in zs_sorted[:-1]]
        else:
            target_zs = [(zs_sorted[0], False)]
        for tz, hidden_flag in target_zs:
            level_nids = [n for n in nids
                          if abs(float(model.nodes[n].coord[2]) - tz) <= 5.0]
            if len(level_nids) < 3:
                continue
            zk = int(round(tz / Z_BUCKET))
            key = (cid, zk)
            groups[key].extend(level_nids)
            group_z[key] = tz
            group_hidden[key] = hidden_flag

    # 2) 종속 캔틸레버 슬래브 합치기 — parent_id 의 같은 z 다이어프램에
    for cid, comp in scene.components.items():
        if not isinstance(comp, CantileverSlab):
            continue
        parent_id = int(getattr(comp, 'parent_id', 0))
        if parent_id <= 0:
            continue
        nids = model.comp_to_nodes.get(cid, [])
        if not nids:
            continue
        # 캔틸 슬래브 z (단일 레벨)
        z_cant = float(model.nodes[nids[0]].coord[2])
        zk = int(round(z_cant / Z_BUCKET))
        # 부모 그룹에 (정확 일치 OR ±1 버킷) 합치기
        for delta in (0, 1, -1):
            key = (parent_id, zk + delta)
            if key in groups:
                groups[key].extend(nids)
                break

    # 3) Diaphragm 객체 생성 + master 노드 등록
    next_node_id = (max(model.nodes.keys()) + 1) if model.nodes else 1
    for (cid, zk), nlist in groups.items():
        # 중복 제거 (consolidation 으로 같은 nid 가 두 위치에서 추가 가능)
        seen = set()
        unique = []
        for n in nlist:
            if n in seen or n not in model.nodes:
                continue
            seen.add(n)
            unique.append(n)
        if len(unique) < 3:
            continue
        coords = np.array([model.nodes[n].coord for n in unique])
        cx = float(np.mean(coords[:, 0]))
        cy = float(np.mean(coords[:, 1]))
        cz = float(np.mean(coords[:, 2]))
        # polygon: centroid 기준 각도 정렬 (단순 다각형)
        ang = np.arctan2(coords[:, 1] - cy, coords[:, 0] - cx)
        order = np.argsort(ang)
        polygon = [coords[i].copy() for i in order]
        # master 노드 등록 (고유 gid). source_comp_id = 본체 id.
        master_gid = next_node_id
        next_node_id += 1
        model.nodes[master_gid] = AnalysisNode(
            id=master_gid,
            coord=np.array([cx, cy, cz], dtype=np.float64),
            source_comp_id=cid,
        )
        model.diaphragms.append(Diaphragm(
            comp_id=cid,
            z=cz,
            master_node_id=master_gid,
            slave_node_ids=unique,
            polygon=polygon,
            hidden=group_hidden.get((cid, zk), False),
        ))


# ── 종속 관계 노드 통합 ──────────────────────────────────────


def _consolidate_dependent_nodes(model: AnalysisModel, scene: Scene) -> None:
    """종속 관계로 얽힌 부재의 인접 노드를 같은 nid 로 통합.

    효과: 통합된 노드들은 같은 컴포넌트 내부 노드처럼 자연 강접합으로 동작.
    별도 자유도 묶음 분기(wall_fp_merge, 캔틸 부모 결합, 코어 적층 핀) 불필요.

    [통합 대상]
    1) 캔틸레버 종속 (sub_index>0): anchor 노드 ↔ 부모 컴포넌트 같은 좌표 노드.
       - 부모는 같은 group_id + sub_index=0.
       - 캔틸 anchor 노드는 부모 모듈 / 부모 패널 등에서 닿는 쪽.
    2) 합체 벽-FP (StructWall.merged_fp_id): 벽 베이스 노드 ↔ FP 같은 코너 노드.
       - 같은 xy + 같은 z (≈1mm 이내) 페어를 통합.
    3) 코어 다층 적층 (같은 group_id, 인접 floor_index Core): 위층 base ↔ 아래층 top.
       - 좌표가 정확히 같지 않을 수 있으므로(z 갭 ~20mm) xy 일치 + z 갭 ≤ 30mm
         페어를 통합. keep 노드의 좌표 유지(아래층 top 좌표가 사실상 모서리).

    [구현 메모]
    - Union-find 로 통합 root 정규화(체인 안전).
    - 최소 nid 를 root 로 사용(노드 ID 부여 순서 보존).
    - 노드 사전·부재 사전·comp_to_nodes·cantilever_anchor_node_ids 모두 갱신.
    """
    pairs: List[Tuple[int, int]] = []  # (a, b) — 두 nid 를 같은 root 로 통합
    # 코어 모서리 통합 노드의 목표 좌표(두 벽 중심선 직각 교차점). 통합 후 적용.
    corner_targets: Dict[int, np.ndarray] = {}

    # 1) 캔틸레버 종속
    for cid, comp in scene.components.items():
        if not isinstance(comp, (CantileverBeam, CantileverSlab)):
            continue
        if getattr(comp, 'sub_index', 0) == 0:
            continue
        parent_gid = getattr(comp, 'group_id', 0)
        if parent_gid <= 0:
            continue
        parent_comp = None
        for c2 in scene.components.values():
            if (getattr(c2, 'group_id', 0) == parent_gid
                    and getattr(c2, 'sub_index', 0) == 0
                    and getattr(c2, 'floor_index', -1) == getattr(comp, 'floor_index', -1)):
                parent_comp = c2
                break
        if parent_comp is None:
            continue
        cant_nids = model.comp_to_nodes.get(cid, [])
        parent_nids = model.comp_to_nodes.get(parent_comp.id, [])
        # 캔틸 anchor 노드 (anchor_keys 가 model.cantilever_anchor_node_ids 에 등록)
        anchor_nids = [nid for nid in cant_nids
                       if nid in model.cantilever_anchor_node_ids]
        if not anchor_nids:
            # anchor 정보 없으면 캔틸 끝점 모두 후보로
            anchor_nids = cant_nids
        # [2026-05-13] 임계 1mm → 200mm 로 확장.
        # 캔틸 anchor 가 부모 모듈/패널 노드와 약 100mm (단면 절반) 떨어진 자리
        # 자연 발생. 임계 1mm 면 통합 안 되어 _snap 함수에 의존 — 그 함수는
        # 모듈 부모만 처리해 패널 부모 캔틸이 일관 안 됨. 임계 키워서 두 부모
        # 모두 토폴로지 단계에서 자연 통합.
        # [2026-05-19 길이 보존] anchor 노드를 부모 노드로 그냥 흡수하면 캔틸
        # free end 좌표는 그대로 남아 부재가 100mm (단면 절반) 만큼 늘어
        # 시각화/해석에서 길이 오류. 가장 가까운 anchor-부모 페어를 먼저 찾아
        # 그 delta 로 캔틸 전체를 평행이동한 뒤 통합 페어를 등록한다.
        # 결과: anchor 는 부모 노드와 정확히 일치(거리 0), free end 는 같은
        # delta 만큼 안쪽으로 이동 → 캔틸 길이 보존.
        if anchor_nids and parent_nids:
            best_global_d = 200.0
            best_pair_global = None
            for anc_nid in anchor_nids:
                anc = model.nodes.get(anc_nid)
                if anc is None:
                    continue
                for pnid in parent_nids:
                    p = model.nodes.get(pnid)
                    if p is None:
                        continue
                    d = float(np.linalg.norm(anc.coord - p.coord))
                    if d < best_global_d:
                        best_global_d = d
                        best_pair_global = (anc_nid, pnid)
            if best_pair_global is not None and best_global_d > 0.5:
                anc_nid_b, pnid_b = best_pair_global
                delta = model.nodes[pnid_b].coord - model.nodes[anc_nid_b].coord
                for nid in cant_nids:
                    n = model.nodes.get(nid)
                    if n is not None:
                        n.coord = n.coord + delta
        for anc_nid in anchor_nids:
            anc = model.nodes.get(anc_nid)
            if anc is None:
                continue
            best_pnid = None
            best_d = 200.0
            for pnid in parent_nids:
                p = model.nodes.get(pnid)
                if p is None:
                    continue
                d = float(np.linalg.norm(anc.coord - p.coord))
                if d < best_d:
                    best_d = d
                    best_pnid = pnid
            if best_pnid is not None:
                pairs.append((best_pnid, anc_nid))

    # 2) 합체 벽-FP
    for cid, comp in scene.components.items():
        if not isinstance(comp, StructWall):
            continue
        fp_id = getattr(comp, 'merged_fp_id', None)
        if fp_id is None:
            continue
        wall_nids = model.comp_to_nodes.get(cid, [])
        fp_nids = model.comp_to_nodes.get(fp_id, [])
        for wnid in wall_nids:
            wn = model.nodes.get(wnid)
            if wn is None:
                continue
            for fnid in fp_nids:
                fn = model.nodes.get(fnid)
                if fn is None:
                    continue
                d = float(np.linalg.norm(wn.coord - fn.coord))
                if d < 1.0:
                    pairs.append((fnid, wnid))
                    break

    # 2b) [2026-05-16] 같은 FP 에 합체된 벽패널 끼리 기둥 공유 자동 통합.
    # 사용자가 벽1·벽2 를 같은 FP 에 합체했고 두 벽의 기둥이 같은 위치(1mm
    # 이내)에 박혔다면 그 노드들을 한 nid 로 통합한다. 기둥이 공유 안 되면
    # 통합 안 일어남(노드 페어 자체가 없음). n-way(벽1·벽2·벽3 …)도 자동.
    # 같은 floor_index 끼리만 비교 — 다른 층 벽이 우연히 같은 xyz 에 있는
    # 케이스는 의미 없음.
    fp_to_walls: Dict[int, List[Tuple[int, int]]] = {}   # fp_cid → [(wall_cid, floor_idx)]
    for cid, comp in scene.components.items():
        if not isinstance(comp, StructWall):
            continue
        fp_id = getattr(comp, 'merged_fp_id', None)
        if fp_id is None:
            continue
        fi = int(getattr(comp, 'floor_index', 0))
        fp_to_walls.setdefault(int(fp_id), []).append((int(cid), fi))
    for fp_id, wall_list in fp_to_walls.items():
        for i in range(len(wall_list)):
            wid_a, fi_a = wall_list[i]
            nids_a = model.comp_to_nodes.get(wid_a, [])
            for j in range(i + 1, len(wall_list)):
                wid_b, fi_b = wall_list[j]
                if fi_a != fi_b:
                    continue   # 다른 층 — 비교 대상 아님
                nids_b = model.comp_to_nodes.get(wid_b, [])
                for nid_a in nids_a:
                    na = model.nodes.get(nid_a)
                    if na is None:
                        continue
                    for nid_b in nids_b:
                        nb = model.nodes.get(nid_b)
                        if nb is None:
                            continue
                        d = float(np.linalg.norm(na.coord - nb.coord))
                        if d < 1.0:
                            pairs.append((nid_a, nid_b))
                            break

    # 3) 코어 다층 적층 (같은 group_id 인접 floor_index)
    # [2026-06-03] 코어 노드는 전부 6DOF 고정(고정 경계)이라 다층 적층·모서리
    # 통합이 불필요하다. 게다가 통합 로직(xy 일치 + dz 5~500)이 새 선 모델의
    # 천장보 레벨 노드(z=3200)와 위층 base(z=3420, dz=220)를 잘못 합쳐 zero-length
    # 기둥선을 만든다 → 코어 통합을 비활성. 코어 연결은 R09 사영 + 빈공간 연결선이
    # 담당하고, 중복 좌표 노드는 전부 고정이라 무해하다.
    cores_by_gid: Dict[int, List[Tuple[int, object]]] = {}
    # [2026-05-13 wide-column 전환 후 단순화]
    # 같은 group_id 내 모든 코어 노드 페어 중 xy 일치(< 1mm) + z 갭 ≤ 500mm
    # 페어를 통합. 인접 층 적층이든 같은 층 모서리든 자동 처리(모서리는
    # 케이스 4 에서 별도 dxy ≤ 500mm 로 처리되므로 여기는 xy 정확 일치만).
    for gid, lst in cores_by_gid.items():
        all_nids: List[Tuple[int, np.ndarray, int]] = []
        for cid, _c in lst:
            for nid in model.comp_to_nodes.get(cid, []):
                n = model.nodes.get(nid)
                if n is not None:
                    all_nids.append((nid, n.coord, cid))
        for ii in range(len(all_nids)):
            for jj in range(ii + 1, len(all_nids)):
                nid_a, c_a, cid_a = all_nids[ii]
                nid_b, c_b, cid_b = all_nids[jj]
                if cid_a == cid_b:
                    continue
                dxy = float(np.linalg.norm(c_a[:2] - c_b[:2]))
                if dxy > 1.0:
                    continue
                dz = abs(float(c_a[2] - c_b[2]))
                # 같은 z(< MIN) 모서리는 케이스 4 가 처리, 너무 먼 페어(> MAX)는 제외.
                CORE_VERTICAL_DZ_MIN_MM = 5.0
                CORE_VERTICAL_DZ_MAX_MM = 500.0
                if dz < CORE_VERTICAL_DZ_MIN_MM or dz > CORE_VERTICAL_DZ_MAX_MM:
                    continue
                pairs.append((nid_a, nid_b))

    # 4) 코어벽 모서리 (같은 group_id 같은 floor_index 다른 cid)
    # [2026-05-13 wide-column 전환 후] 두 코어벽이 ㄷ자 등으로 만나는 모서리.
    # 디자인 단계에서 두 벽이 두께 t 만큼 어긋난 자리에 배치되어 column 끝점
    # 좌표가 정확히 일치하지 않음. 평면 거리 ≤ 임계 페어를 같은 nid 로 통합해
    # 시각상 한 점에서 만나도록 보정.
    CORE_CORNER_TOL = 500.0  # 두께 t(300) + 여유. 두 벽 끝점 사이 거리 한도.
    # [함정 — 통합 좌표를 생성순서(작은 nid)에 맡기면 모서리가 좌우 비대칭이 된다]
    # 두 벽 중심선은 ㄷ자 모서리에서 두께 절반씩 어긋난다(가로벽 중심선이 세로벽
    # 중심선까지 못 닿음). union-find 가 살리는 root 좌표를 그대로 두면, root 가
    # 가로벽이면 세로벽이 끌려와 사선이 되고, root 가 세로벽이면 가로벽이 끌려와
    # 사선 없이 늘어난다 — 즉 어느 쪽이 끌려가는지가 부재 생성순서에 좌우돼 한쪽
    # 모서리만 망가진다. 해결: 모서리 페어마다 '두 벽 중심선 직각 교차점'을 미리
    # 계산해 두고(corner_targets), 통합 후 살아남은 노드 좌표를 교차점으로 덮어쓴다.
    # → 양쪽 모서리 모두 가로벽이 교차점까지 늘어나고 세로벽은 그대로 → 좌우 대칭.
    for gid, lst in cores_by_gid.items():
        same_floor: Dict[int, List[Tuple[int, object]]] = {}
        for cid, c in lst:
            fi = getattr(c, 'floor_index', 0)
            same_floor.setdefault(fi, []).append((cid, c))
        for fi, sf_lst in same_floor.items():
            if len(sf_lst) < 2:
                continue
            comp_of = {cid: c for cid, c in sf_lst}
            cids = [x[0] for x in sf_lst]
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    ci, cj = cids[i], cids[j]
                    # 가로(rot%180==0) / 세로(rot%180==90) 판별 — 교차점 산출용
                    ri = int(round(float(getattr(comp_of[ci], 'rotation', 0)))) % 180
                    rj = int(round(float(getattr(comp_of[cj], 'rotation', 0)))) % 180
                    ci_nids = model.comp_to_nodes.get(ci, [])
                    cj_nids = model.comp_to_nodes.get(cj, [])
                    for nid_a in ci_nids:
                        na = model.nodes.get(nid_a)
                        if na is None:
                            continue
                        for nid_b in cj_nids:
                            nb = model.nodes.get(nid_b)
                            if nb is None:
                                continue
                            # 같은 z (다른 층 잘못 묶지 않기)
                            if abs(na.coord[2] - nb.coord[2]) > 5.0:
                                continue
                            dxy = float(np.linalg.norm(na.coord[:2] - nb.coord[:2]))
                            if dxy < 1.0 or dxy > CORE_CORNER_TOL:
                                continue
                            pairs.append((nid_a, nid_b))
                            # 한 벽 가로 + 한 벽 세로일 때만 직각 교차점 정의:
                            # 세로벽 노드의 x, 가로벽 노드의 y 를 취한다.
                            if ri != rj:
                                if ri == 90:   # ci 세로, cj 가로
                                    xc, yc = na.coord[0], nb.coord[1]
                                else:          # ci 가로, cj 세로
                                    xc, yc = nb.coord[0], na.coord[1]
                                tgt = np.array([xc, yc, na.coord[2]], dtype=np.float64)
                                corner_targets[nid_a] = tgt
                                corner_targets[nid_b] = tgt

    if not pairs:
        return

    # Union-find — 최소 nid 를 root 로 정규화
    parent: Dict[int, int] = {}
    def _find(x: int) -> int:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra == rb:
            return
        # 작은 nid 가 root
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    for a, b in pairs:
        _union(a, b)

    # 통합 매핑 (drop → keep)
    remap: Dict[int, int] = {}
    for nid in list(model.nodes.keys()):
        root = _find(nid)
        if root != nid:
            remap[nid] = root

    if not remap:
        return

    # 노드 제거 — drop 들
    for drop in remap:
        if drop in model.nodes:
            del model.nodes[drop]

    # 부재 끝 노드 ID 교체
    for m in model.members.values():
        if m.n1 in remap:
            m.n1 = remap[m.n1]
        if m.n2 in remap:
            m.n2 = remap[m.n2]
        if getattr(m, 'n3', 0) in remap:
            m.n3 = remap[m.n3]
        if getattr(m, 'n4', 0) in remap:
            m.n4 = remap[m.n4]

    # comp_to_nodes 교체 (drop → keep) + 중복 제거
    for cid, nids in list(model.comp_to_nodes.items()):
        new_nids: List[int] = []
        seen: set = set()
        for n in nids:
            nn = remap.get(n, n)
            if nn in seen:
                continue
            seen.add(nn)
            new_nids.append(nn)
        model.comp_to_nodes[cid] = new_nids

    # cantilever_anchor_node_ids 교체
    new_anchors: set = set()
    for nid in model.cantilever_anchor_node_ids:
        new_anchors.add(remap.get(nid, nid))
    model.cantilever_anchor_node_ids = new_anchors

    # 코어 모서리: 살아남은 노드 좌표를 두 벽 중심선 직각 교차점으로 보정.
    # (생성순서로 정해진 root 좌표를 덮어 좌우 모서리 동작을 대칭화 — 사선 제거)
    for nid, tgt in corner_targets.items():
        root = _find(nid)
        node = model.nodes.get(root)
        if node is not None:
            node.coord = tgt



# ── 중간보·중간기둥 ↔ host 보 분할 매칭 ────────────────────────

def _split_hosts_for_mid_endpoints(model: 'AnalysisModel') -> None:
    """mid_beam/mid_column 끝점이 다른 부재(주로 모듈 보) 직선 위에 떨어지면
    host 부재를 그 사영점에서 분할하고 mid_* 끝점 노드를 그 사영점으로 옮겨
    공유시킨다.

    [CoT]
    1. mid_beam/mid_column 부재의 끝점 노드 ID 수집.
    2. host 후보: mid_* 가 아닌 모든 보·기둥(주로 모듈 상·하부 보).
    3. 각 끝점에 대해 가장 가까운 host 부재 직선 위 사영점 찾기 (사영 거리 ≤
       TOL, 사영 매개변수 t 가 host 길이 안쪽 INSET 이내 제외).
    4. 끝점 노드 좌표를 사영점으로 정렬 → host 부재를 (n1→끝점) + (끝점→n2)
       두 부재로 분할.
    5. 같은 host 에 여러 끝점이 떨어지는 경우, 그때마다 분할된 sub-부재를
       다음 후보로 사용 (반복 처리).

    이 처리 후 mid_* 끝점은 host 보 위 고유 노드와 동일 ID 를 갖게 되어
    OpenSees 빌드에서 자연스럽게 같은 자유도 공유.
    """
    # 사용자가 모듈 외피(컬럼 중심선에서 half_s=100mm 떨어진 지점)에 mid_beam
    # 을 클릭하는 경우가 많으므로, half_s 정도의 사영 거리는 허용해야 한다.
    TOL = float(SECTION_W_MM)   # 200mm — 모듈 단면 폭 정도까지 허용
    INSET = 5.0    # host 양 끝 안쪽 여유 (코너 매칭은 별도)

    # 1) mid_* 끝점 노드 모음
    mid_endpoint_nids: List[int] = []
    for m in model.members.values():
        if m.role in ('mid_beam', 'mid_column'):
            mid_endpoint_nids.append(m.n1)
            mid_endpoint_nids.append(m.n2)
    # 중복 제거(순서 보존)
    seen = set()
    mid_endpoint_nids = [n for n in mid_endpoint_nids
                         if not (n in seen or seen.add(n))]
    if not mid_endpoint_nids:
        return

    next_nid = max(model.nodes.keys()) + 1 if model.nodes else 1
    next_mid = max(model.members.keys()) + 1 if model.members else 1

    # [성능] host 후보를 공간 격자(z버킷+xy칸)로 색인한다. 끝점 × 전체 host 전수
    # 비교(O(M·H), 18층 1800만 회)를 끝점이 든 칸의 host 만 보게 해 O(M) 으로
    # 낮춘다. 각 host 를 자기 bbox ±TOL 칸 모두에 등록하므로, 끝점이 든 칸만
    # 조회해도 사영거리 ≤TOL 후보가 빠짐없이 포함된다(결과 불변, 회귀 0).
    # → 사용자 의도("다른 층·먼 위치 모듈 말고 대상 위치 부재만")의 안전한 구현.
    from collections import defaultdict as _dd
    _CELL = TOL    # 200mm — 사영 임계와 동일(셀 ≥ 임계라야 누락 0)

    def _host_cells(m):
        c1 = model.nodes[m.n1].coord
        c2 = model.nodes[m.n2].coord
        zlo = int((min(c1[2], c2[2]) - TOL) // _CELL); zhi = int((max(c1[2], c2[2]) + TOL) // _CELL)
        xlo = int((min(c1[0], c2[0]) - TOL) // _CELL); xhi = int((max(c1[0], c2[0]) + TOL) // _CELL)
        ylo = int((min(c1[1], c2[1]) - TOL) // _CELL); yhi = int((max(c1[1], c2[1]) + TOL) // _CELL)
        for _zk in range(zlo, zhi + 1):
            for _ix in range(xlo, xhi + 1):
                for _iy in range(ylo, yhi + 1):
                    yield (_zk, _ix, _iy)

    _host_grid = _dd(list)

    def _register_host(hmid):
        for _cell in _host_cells(model.members[hmid]):
            _host_grid[_cell].append(hmid)

    for _hmid, _m in list(model.members.items()):
        if _m.role not in ('mid_beam', 'mid_column'):
            _register_host(_hmid)

    n_split = 0
    for end_nid in mid_endpoint_nids:
        end_coord = model.nodes[end_nid].coord
        _ekey = (int(end_coord[2] // _CELL), int(end_coord[0] // _CELL),
                 int(end_coord[1] // _CELL))
        best = None  # (dist, host_mid, t, proj)
        _seen_h = set()
        for hmid in _host_grid.get(_ekey, ()):
            if hmid in _seen_h:
                continue
            _seen_h.add(hmid)
            hm = model.members.get(hmid)
            if hm is None or hm.role in ('mid_beam', 'mid_column'):
                continue
            # 끝점 노드가 host 의 양 끝 노드면 이미 공유됨 — 건너뜀
            if end_nid == hm.n1 or end_nid == hm.n2:
                best = (0.0, None, 0.0, None)
                break
            c1 = model.nodes[hm.n1].coord
            c2 = model.nodes[hm.n2].coord
            bvec = c2 - c1
            blen = float(np.linalg.norm(bvec))
            if blen < 1.0:
                continue
            bdir = bvec / blen
            t = float(np.dot(end_coord - c1, bdir))
            if t < INSET or t > blen - INSET:
                continue
            proj = c1 + bdir * t
            d = float(np.linalg.norm(end_coord - proj))
            if d > TOL:
                continue
            if best is None or d < best[0]:
                best = (d, hmid, t, proj)

        if best is None or best[1] is None:
            continue
        d, hmid, t, proj = best
        hm = model.members[hmid]

        # 끝점 좌표를 사영점으로 정렬 (host 직선 위에 정확히)
        model.nodes[end_nid].coord = proj.copy()

        # host 분할: 기존 부재는 (n1 → end_nid) 로 단축, 신규 부재는 (end_nid → n2)
        original_n2 = hm.n2
        hm.n2 = end_nid
        new_mid = next_mid
        next_mid += 1
        # (2026-05-19 작업 2) 공장 제작 단위로 본수 합산하려면 분할된 두 sub 가
        # 원래 한 본이었다는 사실을 추적해야 한다. host 의 "원본 parent" 를
        # 결정: hm 이 이미 다른 분할 결과(parent_member_id 보유)면 그 root 를,
        # 아니면 hm.id 를 자신의 root 로 삼는다. 이후 aggregate_steel 이
        # parent_root 가 같은 부재들의 길이를 합산해 1본으로 집계한다.
        host_root = hm.parent_member_id if hm.parent_member_id is not None else hm.id
        if hm.parent_member_id is None:
            hm.parent_member_id = host_root  # 자기 자신을 root 로 명시
        model.members[new_mid] = AnalysisMember(
            id=new_mid, n1=end_nid, n2=original_n2,
            kind=hm.kind, role=hm.role,
            section_w=hm.section_w, section_h=hm.section_h, section_t=hm.section_t,
            source_comp_ids=list(hm.source_comp_ids),
            merge_group=hm.merge_group,
            parent_member_id=host_root,
        )
        # [성능] 분할 sub-host 도 격자에 등록 — 같은 host 에 떨어지는 다음 끝점이
        # 이 sub 직선을 후보로 볼 수 있게(반복 분할 정확성 보존).
        _register_host(new_mid)
        # comp_to_members 갱신 (sub 부재도 같은 컴포넌트 소속)
        for cid in hm.source_comp_ids:
            lst = model.comp_to_members.get(cid)
            if lst is not None and new_mid not in lst:
                lst.append(new_mid)
        # [2026-05-16] comp_to_nodes 갱신 — mid 끝점 nid 를 host 가 속한 컴포넌트
        # (보통 모듈) 의 노드 자료에 추가. 이게 빠지면 joint_rules 의 모서리
        # 추출이 sub-부재의 두 끝(코너 + mid 끝점) 중 mid 끝점 쪽을 cid 노드로
        # 인식 못 해, 중간 부재가 박힌 모듈 보가 (b) 모서리 매칭에서 제외됨.
        for cid in hm.source_comp_ids:
            nlst = model.comp_to_nodes.get(cid)
            if nlst is not None and end_nid not in nlst:
                nlst.append(end_nid)
        n_split += 1

    # [2026-06-07] 마무리 — 아직 떠있는 mid 끝점을 같은 좌표(≤MERGE_TOL) 주구조
    #   노드에 흡수해 연결한다. _extract_mid_beam 이 끝을 기둥 중심선까지 연장해도,
    #   build 가 컴포넌트별로 노드를 독립 생성하고 위 host 사영은 코너(t≈0)를
    #   제외하므로 같은 좌표라도 별개 노드로 남는다. 키(1mm 반올림)는 0.5 경계에서
    #   갈릴 수 있어 키 대신 '실제 거리'로 병합한다(부동소수점 안전).
    MERGE_TOL = 2.0
    _roles2 = {}
    for _m in model.members.values():
        _roles2.setdefault(_m.n1, set()).add(_m.role)
        _roles2.setdefault(_m.n2, set()).add(_m.role)
    _struct = [(nid, model.nodes[nid].coord) for nid in list(model.nodes)
               if (_roles2.get(nid, set()) - {'mid_beam', 'mid_column'})]
    if _struct:
        _sarr = np.array([c for _, c in _struct])
        _sids = [nid for nid, _ in _struct]
        _midends = set()
        for _m in model.members.values():
            if _m.role in ('mid_beam', 'mid_column'):
                _midends.add(_m.n1)
                _midends.add(_m.n2)
        for _e in list(_midends):
            if _e not in model.nodes:
                continue
            # 이미 주구조와 연결된 끝은 건너뜀
            if _roles2.get(_e, set()) - {'mid_beam', 'mid_column'}:
                continue
            _ec = model.nodes[_e].coord
            _d = np.linalg.norm(_sarr - _ec, axis=1)
            _j = int(np.argmin(_d))
            if float(_d[_j]) <= MERGE_TOL:
                _tgt = _sids[_j]
                if _tgt == _e:
                    continue
                for _mm in model.members.values():
                    if _mm.n1 == _e:
                        _mm.n1 = _tgt
                    if _mm.n2 == _e:
                        _mm.n2 = _tgt
                for _nlst in model.comp_to_nodes.values():
                    if _e in _nlst:
                        _nlst[:] = [_tgt if x == _e else x for x in _nlst]
                model.nodes.pop(_e, None)
                n_split += 1

    if n_split > 0:
        pass


# ── 디버그 요약 ──────────────────────────────────────────────

def summarize(model: AnalysisModel) -> str:
    n_nodes = len(model.nodes)
    n_mem = len(model.members)
    n_beam = sum(1 for m in model.members.values() if m.kind == 'beam')
    n_col = sum(1 for m in model.members.values() if m.kind == 'column')
    bases = model.base_node_ids()
    return "\n".join([
        f"=== AnalysisModel ===",
        f"  노드     : {n_nodes}",
        f"  부재     : {n_mem}  (보 {n_beam} / 기둥 {n_col})",
        f"  베이스 노드: {len(bases)}",
        f"  컴포넌트 수: {len(model.comp_to_members)}",
    ])
