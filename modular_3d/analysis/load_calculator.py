"""하중 계산 — 자중 + 슬래브 사하중 + 활하중을 부재별 등분포로 변환.

처리 흐름:
  1) 모든 부재(보/기둥)에 대해 강재 자중을 등분포 자중(N/mm)으로 추가.
  2) 슬래브 보유 컴포넌트(Module / FloorPanel / CantileverSlab)에 대해
     슬래브 자중과 활하중을 1방향/2방향 판별 후 둘레 보에 등가 UDL 로 분배.
  3) 결과는 LoadResult.member_loads[mem_id] 의 LoadCase 에 누적.

LoadCase 는 사하중/활하중을 분리 보관하므로 ops_builder 에서 1.2D + 1.6L
하중조합을 그대로 적용할 수 있다.

단위: N, mm. 분포하중은 항상 −Global Z 방향 (중력) 이며 부호는 양수로 저장,
실제 부호 적용은 ops_builder 에서 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import numpy as np

from modular_3d.model import (
    Scene, Module, FloorPanel, StructWall, CantileverSlab, Vertical3Module,
    Core, CoreSlab, ComponentType,
)
from modular_3d.analysis.topology import AnalysisModel, AnalysisMember
from modular_3d.analysis.constants import (
    SECTION_W_MM, SECTION_H_MM, SECTION_T_MM, SHS_200x200x8,
    STEEL_UNIT_WEIGHT_N_MM3, CONCRETE_UNIT_WEIGHT_N_MM3,
    SLAB_THICKNESS_MM, LIVE_LOAD_N_MM2,
    LOAD_COMBO_DL, LOAD_COMBO_LL,
    SLAB_ONE_WAY_RATIO,
    MODULE_HEIGHT_MM, FLOOR_HEIGHT,
)


# ── 데이터 구조 ──────────────────────────────────────────────

@dataclass
class LoadCase:
    """한 부재가 받는 등분포 하중 (모두 N/mm, 양수, 중력 방향).

    self_weight : 부재 자체 강재 자중
    slab_dead   : 분배받은 슬래브 자중
    live        : 분배받은 활하중
    """
    self_weight: float = 0.0
    slab_dead: float = 0.0
    live: float = 0.0

    @property
    def total_dead(self) -> float:
        return self.self_weight + self.slab_dead

    @property
    def factored(self) -> float:
        """1.2 D + 1.6 L  (양수, N/mm)."""
        return LOAD_COMBO_DL * self.total_dead + LOAD_COMBO_LL * self.live


@dataclass
class BeamShare:
    """슬래브에서 한 둘레 보로 분배된 하중의 패턴 + peak 값.

    pattern:
      - 'uniform'   : 등분포 (1방향 슬래브의 장변 보)
      - 'triangle'  : 삼각형 (2방향 슬래브의 단변 보)
      - 'trapezoid' : 사다리꼴 (2방향 슬래브의 장변 보)
    peak_w_factored:
      - 분포 프로파일의 **peak(중앙 최대)** 값 [N/mm], 1.2D + 1.6L 적용 후.
      - 시각화에서 화살표 길이/프로파일 높이에 사용.
      - LoadCase.factored 는 등가 평균 UDL 이므로 값이 다르다.
    """
    pattern: str
    peak_w_factored: float


@dataclass
class SlabDistribution:
    """한 슬래브의 분배 메타 — 하중 흐름 시각화 Stage 1 용.

    corners 는 시각화 면(반투명 Mesh) 을 그리기 위한 월드좌표 점.
    직사각형 슬래브는 4 개, 캔틸레버는 4 개(3보 U 끝단).
    """
    source_comp_id: int
    slab_kind: str                 # 'module_floor' | 'floor_panel' | 'cantilever_slab'
    corners: List[np.ndarray]
    one_way: bool
    short_len: float               # 단변 길이 (캔틸레버는 0)
    long_len: float                # 장변 길이 (캔틸레버는 0)
    dead_pressure_n_mm2: float
    live_pressure_n_mm2: float
    short_beam_ids: List[int] = field(default_factory=list)
    long_beam_ids: List[int] = field(default_factory=list)
    beam_shares: Dict[int, BeamShare] = field(default_factory=dict)


@dataclass
class LoadResult:
    member_loads: Dict[int, LoadCase] = field(default_factory=dict)

    # Stage 1 시각화용 — 슬래브 판정 + 분배 패턴 기록
    slab_distributions: List[SlabDistribution] = field(default_factory=list)

    # 검증/디버그 통계
    total_self_weight_n: float = 0.0
    total_slab_dead_n: float = 0.0
    total_live_n: float = 0.0

    @property
    def total_factored_n(self) -> float:
        return (LOAD_COMBO_DL * (self.total_self_weight_n + self.total_slab_dead_n)
                + LOAD_COMBO_LL * self.total_live_n)

    def get(self, mem_id: int) -> LoadCase:
        if mem_id not in self.member_loads:
            self.member_loads[mem_id] = LoadCase()
        return self.member_loads[mem_id]

    def summary(self) -> str:
        n = len(self.member_loads)
        return "\n".join([
            f"=== LoadResult ===",
            f"  하중 받는 부재 수: {n}",
            f"  강재 자중 합계  : {self.total_self_weight_n / 1000:.2f} kN",
            f"  슬래브 자중 합계: {self.total_slab_dead_n / 1000:.2f} kN",
            f"  활하중 합계     : {self.total_live_n / 1000:.2f} kN",
            f"  사하중 D 합계   : {(self.total_self_weight_n + self.total_slab_dead_n) / 1000:.2f} kN",
            f"  활하중 L 합계   : {self.total_live_n / 1000:.2f} kN",
            f"  계수하중(1.2D+1.6L): {self.total_factored_n / 1000:.2f} kN",
        ])


# ── 1) 강재 자중 ─────────────────────────────────────────────

def _section_area(m: AnalysisMember) -> float:
    """부재 단면적 mm².

    [분기]
    - role='core_column' (RC 코어벽 솔리드): section_w × section_h
    - 그 외 (강재 SHS 200×200×8): SHS 단면적
    """
    if m.role == 'core_column':
        return float(m.section_w) * float(m.section_h)
    return SHS_200x200x8['A']


def _apply_self_weight(model: AnalysisModel, result: LoadResult) -> None:
    """모든 frame 부재에 자중 적용.

    [재료별 단위 무게]
    - role='core_column' → 콘크리트 단위 무게 (구버전 wide-column)
    - 그 외 frame → 강재 단위 무게

    [정책 2026-05-12] 셸 부재(코어벽·코어 슬래브)는 본 함수에서 제외.
    셸의 콘크리트 자중은 ops_solver._apply_shell_self_weight 가 그룹 base 노드에
    직접 부여한다. 셸 부재에 빔 분포 하중을 호출하면 OpenSees 가 silently 무시
    → 적용 자중 합에는 들어가지만 반력에는 잡히지 않아 평형 오차 발생.
    """
    for mid, m in model.members.items():
        if m.kind == 'shell':
            continue   # 셸은 _apply_shell_self_weight 가 처리
        if getattr(m, 'is_split_sub', False):
            continue   # split sub-member 는 원본 mid 의 자중을 sub_tags 로 분배받음
        A = _section_area(m)
        L = model.get_member_length(mid)
        if m.role == 'core_column':
            w = A * CONCRETE_UNIT_WEIGHT_N_MM3
        else:
            w = A * STEEL_UNIT_WEIGHT_N_MM3
        lc = result.get(mid)
        lc.self_weight += w
        result.total_self_weight_n += w * L


# ── 2) 슬래브 → 보 분배 ──────────────────────────────────────

def _classify_4_beams_by_direction(
    member_ids: List[int], model: AnalysisModel
) -> Tuple[List[int], List[int], float, float]:
    """4 개의 둘레 보를 길이별로 두 그룹으로 분류.

    Returns:
        long_ids  : 긴 변 보 ID 2개
        short_ids : 짧은 변 보 ID 2개
        long_len  : 긴 변 길이
        short_len : 짧은 변 길이
    """
    if len(member_ids) != 4:
        raise ValueError(f"4 개 보 기대, 실제 {len(member_ids)}")
    lens = [(mid, model.get_member_length(mid)) for mid in member_ids]
    lens.sort(key=lambda x: x[1])
    short_len = lens[0][1]
    long_len = lens[-1][1]
    short_ids = [lens[0][0], lens[1][0]]
    long_ids = [lens[2][0], lens[3][0]]
    return long_ids, short_ids, long_len, short_len


def _group_perimeter_beams_by_direction(
    member_ids: List[int], model: AnalysisModel
) -> Tuple[List[int], List[int], float, float]:
    """둘레 보 N개(한 변이 여러 토막날 수 있음)를 장축/단축 방향으로 분류.

    [함정] module_bottom_beam 은 모듈 내부 중간보가 긴 변에 붙으면 그 변이 2토막
    이상으로 쪼개져 4개가 아니라 6·8개가 된다. _classify_4_beams_by_direction 은
    정확히 4개를 요구하므로 그런 모듈의 바닥하중이 통째로 누락됐다(바닥패널 없는
    모듈전용 건물 강재 물량 ½ 버그). 본 함수는 보가 어느 축에 평행한지(방향) +
    슬래브 범위로 분류하므로 토막 수와 무관하게 동작한다.

    깨끗한 4개일 때는 _classify_4_beams_by_direction 과 동일 결과(회귀 0). 같은
    변의 토막들은 같은 그룹에 들어가고, _distribute_slab_pressure 가 보별로
    '단위길이당 UDL' 을 더하므로 토막 길이의 합 = 변 길이가 되어 총하중 보존.

    Returns: (long_ids, short_ids, long_len, short_len)  중심선 기준 변 길이.
    """
    dirs: Dict[int, np.ndarray] = {}
    pts: List[Tuple[float, float]] = []
    for mid in member_ids:
        m = model.members[mid]
        c1 = model.nodes[m.n1].coord
        c2 = model.nodes[m.n2].coord
        v = np.array([c2[0] - c1[0], c2[1] - c1[1]], dtype=float)
        L = float(np.hypot(v[0], v[1]))
        if L < 1e-9:
            continue
        dirs[mid] = v / L
        pts.append((float(c1[0]), float(c1[1])))
        pts.append((float(c2[0]), float(c2[1])))
    if not dirs:
        return [], [], 0.0, 0.0
    ref = next(iter(dirs.values()))
    perp = np.array([-ref[1], ref[0]], dtype=float)
    group_par: List[int] = []    # ref 에 평행한 보
    group_perp: List[int] = []   # ref 에 수직인 보
    for mid, u in dirs.items():
        if abs(float(np.dot(u, ref))) >= 0.7071:
            group_par.append(mid)
        else:
            group_perp.append(mid)
    arr = np.array(pts, dtype=float)
    span_par = float((arr @ ref).max() - (arr @ ref).min())
    span_perp = float((arr @ perp).max() - (arr @ perp).min())
    # ref 에 평행한 보는 ref 축을 따라 뻗는다 → 그 변의 길이 = span_par.
    # span_par 이 장변이면 group_par 가 장변 보.
    if span_par >= span_perp:
        return group_par, group_perp, span_par, span_perp
    return group_perp, group_par, span_perp, span_par


def _distribute_slab_pressure(
    pressure_n_mm2: float,
    long_ids: List[int],
    short_ids: List[int],
    long_len: float,
    short_len: float,
    result: LoadResult,
    is_live: bool,
) -> None:
    """슬래브 압력(N/mm²) 을 둘레 보에 등가 평균 UDL 로 분배.

    1방향(b/a >= 2): 긴 변 보 2개에 w·a/2 균일 UDL.
    2방향:
      - 단변 보(삼각형): 등가 평균 = peak/2 = p·a/4
      - 장변 보(사다리꼴): 등가 평균 = (p·a/2) · (1 − a/(2·b))
    시각화용 peak 프로파일은 _build_beam_shares 가 별도로 계산.
    """
    a = short_len
    b = long_len
    if a < 1e-9 or b < 1e-9:
        return
    if b / a >= SLAB_ONE_WAY_RATIO:
        # 1방향: 긴 변 보 2개에 균일 UDL
        udl_long = pressure_n_mm2 * a / 2.0
        _accumulate(result, long_ids, udl_long, is_live)
    else:
        # 2방향: 등가 평균 UDL 을 LoadCase 에 직접 누적
        udl_short = pressure_n_mm2 * a / 4.0           # 삼각형 평균
        udl_long = (pressure_n_mm2 * a / 2.0) * max(0.0, (1.0 - a / (2.0 * b)))
        _accumulate(result, short_ids, udl_short, is_live)
        _accumulate(result, long_ids, udl_long, is_live)


def _build_beam_shares(
    one_way: bool,
    long_ids: List[int],
    short_ids: List[int],
    long_len: float,
    short_len: float,
    p_dead: float,
    p_live: float,
) -> Dict[int, BeamShare]:
    """분포 프로파일 peak(중앙 최대) 기준 BeamShare 딕셔너리 생성.

    Peak 값 (factored):
      - 1방향 long: p·a/2 (그대로 등분포이므로 peak=avg)
      - 2방향 short: peak = p·a/2 (삼각형 중앙 최대, 평균 = p·a/4)
      - 2방향 long: peak = p·a/2 (사다리꼴 평평 구간 = 평균 × (4b/(2b-a)))
    여기서 p = 1.2*p_dead + 1.6*p_live.
    """
    p_f = LOAD_COMBO_DL * p_dead + LOAD_COMBO_LL * p_live
    a = short_len
    b = long_len
    shares: Dict[int, BeamShare] = {}
    if one_way:
        peak_long = p_f * a / 2.0
        for mid in long_ids:
            shares[mid] = BeamShare('uniform', peak_long)
        for mid in short_ids:
            shares[mid] = BeamShare('uniform', 0.0)   # 1방향은 단변 보가 분담 안 함
    else:
        peak_short = p_f * a / 2.0   # 삼각형 peak
        peak_long = p_f * a / 2.0    # 사다리꼴 peak (평평 구간 높이)
        for mid in short_ids:
            shares[mid] = BeamShare('triangle', peak_short)
        for mid in long_ids:
            shares[mid] = BeamShare('trapezoid', peak_long)
    return shares


def _collect_rect_corners(mem_ids: List[int], model: AnalysisModel) -> List[np.ndarray]:
    """4 개 둘레 보의 노드 집합에서 4 코너(unique 좌표) 추출 후
    중심을 기준으로 각도 정렬 → 사각형 순서로 반환."""
    seen_keys = set()
    pts: List[np.ndarray] = []
    for mid in mem_ids:
        for nid in (model.members[mid].n1, model.members[mid].n2):
            c = model.nodes[nid].coord
            key = (round(float(c[0]), 2), round(float(c[1]), 2), round(float(c[2]), 2))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            pts.append(c.copy())
    if len(pts) != 4:
        return pts   # 비정상 케이스 — 호출자에서 처리
    arr = np.array(pts)
    cx, cy = arr[:, 0].mean(), arr[:, 1].mean()
    angles = np.arctan2(arr[:, 1] - cy, arr[:, 0] - cx)
    order = np.argsort(angles)
    return [pts[i] for i in order]


def _find_beam_between_nodes(
    anchor_nids: List[int],
    exclude_mids: List[int],
    model: AnalysisModel,
) -> Optional[int]:
    """anchor_nids 두 노드를 양 끝으로 갖는 보 부재를 찾는다 (exclude 제외).

    캔틸레버 접합면의 4번째 보(모듈/FP 소속)를 탐색하는 용도.
    정확 매칭이 안 되면 좌표 근접(2mm) 매칭으로 폴백.
    """
    if len(anchor_nids) != 2:
        return None
    nid_a, nid_b = anchor_nids
    exclude = set(exclude_mids)
    # 1차: 정확히 같은 node id를 공유하는 보
    for mid, m in model.members.items():
        if mid in exclude or m.kind != 'beam':
            continue
        if getattr(m, 'is_split_sub', False):
            continue
        if {m.n1, m.n2} == {nid_a, nid_b}:
            return mid
    # 2차: 좌표 근접 매칭 (노드 병합 전에 다른 id일 수 있음)
    tol = 2.0
    coord_a = model.nodes[nid_a].coord
    coord_b = model.nodes[nid_b].coord
    for mid, m in model.members.items():
        if mid in exclude or m.kind != 'beam':
            continue
        if getattr(m, 'is_split_sub', False):
            continue
        c1 = model.nodes[m.n1].coord
        c2 = model.nodes[m.n2].coord
        match_ab = (np.linalg.norm(c1 - coord_a) < tol and
                    np.linalg.norm(c2 - coord_b) < tol)
        match_ba = (np.linalg.norm(c1 - coord_b) < tol and
                    np.linalg.norm(c2 - coord_a) < tol)
        if match_ab or match_ba:
            return mid
    return None


def _accumulate(result: LoadResult, mem_ids: List[int], udl: float, is_live: bool) -> None:
    for mid in mem_ids:
        lc = result.get(mid)
        if is_live:
            lc.live += udl
        else:
            lc.slab_dead += udl


def _slab_pressures() -> Tuple[float, float]:
    """(슬래브 자중 압력, 활하중 압력)  N/mm². 수직3층모듈 등 기본 압력용."""
    p_dead = SLAB_THICKNESS_MM * CONCRETE_UNIT_WEIGHT_N_MM3   # N/mm²
    p_live = LIVE_LOAD_N_MM2
    return p_dead, p_live


# ── 슬래브 상면 z / 옥상 검출 / 실 면적가중 압력 (5단계) ──────────

# kN/m² → N/mm² 환산 계수 (1 kN/m² = 1000 N / 1e6 mm² = 1e-3 N/mm²).
_KN_M2_TO_N_MM2 = 1.0e-3


def _slab_top_z(comp) -> Optional[float]:
    """부재 슬래브 상면 월드 z (mm). 슬래브 없으면 None.

    SlabData.corners 는 슬래브 하면 월드 좌표 → 상면 = 하면 + thickness.
    수직3층모듈(slabs 리스트)은 가장 높은 슬래브 상면.
    """
    slab = getattr(comp, 'slab', None)
    if slab is not None:
        return float(slab.corners[:, 2].min()) + float(slab.thickness)
    slabs = getattr(comp, 'slabs', None)
    if slabs:
        return max(float(s.corners[:, 2].min()) + float(s.thickness)
                   for s in slabs)
    return None


def _compute_roof_top_z(scene: Scene) -> Optional[float]:
    """옥상(지붕) 레벨 = 최상단 바닥패널 슬래브 상면 z. 없으면 None.

    [정책]
    - 옥상은 최상층 위에 까는 지붕 슬래브이며, 본 프로그램에서는 바닥패널(FloorPanel)
      로 만든다 → 옥상 검출 후보를 바닥패널로 한정한다.
    - 모듈 바닥 슬래브는 '그 층 거주 바닥'이라 옥상이 아니다(후보 제외) — 옥상
      슬래브 없이 모듈만 쌓아도 최상층 거주공간이 옥상으로 오판되지 않는다.
    - 코어 슬래브는 일반 층보다 솟은 옥탑이라 후보에서 제외(애초에 FloorPanel 아님).
    """
    from modular_3d.analysis.room_loads import detect_roof_top_z
    zs: List[float] = []
    for comp in scene.components.values():
        if not isinstance(comp, FloorPanel):
            continue
        z = _slab_top_z(comp)
        if z is not None:
            zs.append(z)
    return detect_roof_top_z(zs)


# 비내력벽 단위 질량 (kg/m²) — 운송 탭과 동일값. 내벽은 항상 내부 30.
_INTERIOR_WALL_UNIT_KG_M2 = 30.0
_EXTERIOR_WALL_UNIT_KG_M2 = 55.0


def _wall_face_openings(comp, face: str) -> list:
    """comp.openings 중 해당 face 의 개구부만(레거시 face 없으면 'wall' 간주)."""
    out = []
    for o in (getattr(comp, 'openings', None) or []):
        if o.get('face', 'wall') == face:
            out.append(o)
    return out


def _wall_panel_net_area_mm2(corners, openings) -> float:
    """벽 채움 판 순면적 mm² = (길이 u × 높이 v) − 개구부 면적 합. 음수면 0."""
    c = np.asarray(corners, dtype=np.float64)
    u = float(np.linalg.norm(c[1] - c[0]))
    v = float(np.linalg.norm(c[3] - c[0]))
    gross = u * v
    op_area = 0.0
    for o in (openings or []):
        op_area += float(o.get('w', 0.0)) * float(o.get('h', 0.0))
    return max(0.0, gross - op_area)


def _wall_panel_weight_n(net_area_mm2: float, unit_kg_m2: float) -> float:
    """벽 무게 N = 순면적(m²) × 단위질량(kg/m²) × 중력가속도."""
    from modular_3d.카탈로그.materials import G_M_PER_S2
    return (net_area_mm2 / 1.0e6) * unit_kg_m2 * G_M_PER_S2


def _interior_wall_sdl_on_slab(comp, iw_by_parent, slab_area_mm2: float) -> float:
    """comp(슬래브 부재) 위에 종속된 내벽들의 무게를 면적평균 사하중(N/mm²)으로.

    내벽(INTERIOR_WALL)의 parent_id 가 이 슬래브 부재면 그 무게(개구부 차감)를
    합산해 슬래브 면적으로 나눈다 — 노드 추가 없이 슬래브 압력에 얹는 근사.
    [2026-06-02 성능] 부모별 내벽 색인(iw_by_parent: parent_id → [내벽])을 받아
    이 슬래브에 종속된 내벽만 즉시 조회한다(기존 scene 전체 순회 = O(N²) 제거).
    """
    if slab_area_mm2 <= 1e-6:
        return 0.0
    total_w = 0.0
    for c in iw_by_parent.get(comp.id, ()):
        wf = getattr(c, 'wall_fill', None)
        if wf is None:
            continue
        net = _wall_panel_net_area_mm2(
            wf.corners, _wall_face_openings(c, 'wall'))
        total_w += _wall_panel_weight_n(net, _INTERIOR_WALL_UNIT_KG_M2)
    return total_w / slab_area_mm2


def _comp_slab_pressures(comp, scene: Scene,
                         roof_top_z: Optional[float],
                         rooms_by_floor, iw_by_parent) -> Tuple[float, float]:
    """부재 슬래브의 (사하중 압력, 활하중 압력) N/mm² — 실 면적가중 + 옥상 + 내벽.

    사하중 = 슬래브 자중(두께×콘크리트) + 부가 고정하중(SDL) + 내벽 면적평균 자중.
    활하중 = 실 용도 면적가중(빈 영역 주거 기본). 옥상 레벨이면 지붕값 강제.
    """
    from modular_3d.analysis.room_loads import area_weighted_loads, is_roof_level
    from modular_3d.카탈로그.room_types import (
        ROOF_LIVE_LOAD_KN_M2, ROOF_SDL_KN_M2,
    )
    from modular_3d._utils.geometry import xy_bbox

    p_slab_self = SLAB_THICKNESS_MM * CONCRETE_UNIT_WEIGHT_N_MM3   # N/mm²
    rect = xy_bbox(comp)
    slab_area = abs((rect[2] - rect[0]) * (rect[3] - rect[1]))
    top_z = _slab_top_z(comp)
    if top_z is not None and is_roof_level(top_z, roof_top_z):
        live_kn, sdl_kn = ROOF_LIVE_LOAD_KN_M2, ROOF_SDL_KN_M2
    else:
        fi = getattr(comp, 'floor_index', 0)
        rooms = rooms_by_floor.get(fi, ())
        live_kn, sdl_kn = area_weighted_loads(rect, rooms)
    p_live = live_kn * _KN_M2_TO_N_MM2
    p_sdl = sdl_kn * _KN_M2_TO_N_MM2
    p_iw = _interior_wall_sdl_on_slab(comp, iw_by_parent, slab_area)
    return p_slab_self + p_sdl + p_iw, p_live


def _distribute_vertical3_slabs(
    comp: Vertical3Module, cid: int, model: AnalysisModel,
    result: LoadResult, p_dead: float, p_live: float,
) -> None:
    """수직 3층 모듈의 3 슬래브 (1F·2F·3F 바닥) 를 z 레벨별 4 둘레 보에 분배.

    옥상 슬래브는 모듈에 포함되지 않음 — 필요하면 FloorPanel 별도 배치.

    [CoT] 처리 순서:
    1. comp 의 모든 보 부재 수집 (24 = bottom 12 + top 12).
    2. 각 슬래브의 z_top (= 보 중심선 + half_s) 을 기준으로 가장 가까운
       z 의 보 4개를 둘레로 선택.
    3. _distribute_slab_pressure 로 각 슬래브 분배 + slab_distributions 기록.
    """
    s = SECTION_W_MM
    half_s = s / 2.0
    h = float(MODULE_HEIGHT_MM)
    H = float(FLOOR_HEIGHT)   # 3420 — 모듈 내부 층-층 거리

    # comp 의 모든 보 멤버 수집
    all_beams = [mid for mid in model.comp_to_members.get(cid, [])
                 if mid in model.members
                 and model.members[mid].kind == 'beam']

    # 슬래브별 (받침보 z 중심선) 매핑 — 1F·2F·3F 바닥만 (module_bottom_beam, z = k·H).
    slab_specs = [
        ('vertical_module_floor_1F', 0.0,   comp.dimensions.get('width', 3400.0),
         comp.dimensions.get('depth', 3400.0)),
        ('vertical_module_floor_2F', H,     comp.dimensions.get('width', 3400.0),
         comp.dimensions.get('depth', 3400.0)),
        ('vertical_module_floor_3F', 2 * H, comp.dimensions.get('width', 3400.0),
         comp.dimensions.get('depth', 3400.0)),
    ]

    pos_z = float(comp.position[2])
    for slab_kind, beam_local_z, slab_w_real, slab_d_real in slab_specs:
        target_z = pos_z + beam_local_z
        # 그 z 레벨에 가장 가까운 4 보 (둘레) 선택 — tol 5mm 이내 z 매칭.
        cand = []
        for mid in all_beams:
            m = model.members[mid]
            zmid = 0.5 * (model.nodes[m.n1].coord[2] + model.nodes[m.n2].coord[2])
            if abs(zmid - target_z) <= 5.0:
                cand.append(mid)
        if len(cand) != 4:
            continue

        long_ids, short_ids, long_len, short_len = (
            _classify_4_beams_by_direction(cand, model))
        slab_area_real = slab_w_real * slab_d_real
        slab_area_centerline = long_len * short_len
        area_factor = (slab_area_real / slab_area_centerline
                       if slab_area_centerline > 1e-6 else 1.0)
        p_dead_eff = p_dead * area_factor
        p_live_eff = p_live * area_factor
        one_way = (long_len / short_len) >= SLAB_ONE_WAY_RATIO

        _distribute_slab_pressure(p_dead_eff, long_ids, short_ids,
                                  long_len, short_len, result, is_live=False)
        _distribute_slab_pressure(p_live_eff, long_ids, short_ids,
                                  long_len, short_len, result, is_live=True)
        result.total_slab_dead_n += p_dead * slab_area_real
        result.total_live_n += p_live * slab_area_real

        beam_shares = _build_beam_shares(
            one_way, long_ids, short_ids, long_len, short_len,
            p_dead_eff, p_live_eff,
        )
        corners = _collect_rect_corners(cand, model)
        result.slab_distributions.append(SlabDistribution(
            source_comp_id=cid,
            slab_kind=slab_kind,
            corners=corners,
            one_way=one_way,
            short_len=short_len,
            long_len=long_len,
            dead_pressure_n_mm2=p_dead,
            live_pressure_n_mm2=p_live,
            short_beam_ids=list(short_ids),
            long_beam_ids=list(long_ids),
            beam_shares=beam_shares,
        ))


def _apply_slab_loads(scene: Scene, model: AnalysisModel, result: LoadResult) -> None:
    """슬래브 하중을 둘레 보에 분배.

    [5단계] 일반 슬래브(Module/FloorPanel/CantileverSlab)는 부재마다 실 용도
    면적가중 활하중 + 부가 고정하중(SDL)을 적용하고, 옥상 레벨 슬래브는 지붕값을
    강제한다. 수직3층모듈은 전역 기본 압력 유지(실 매칭 추후).
    """
    roof_top_z = _compute_roof_top_z(scene)
    p_dead_v, p_live_v = _slab_pressures()   # 수직3층모듈 기본 압력
    # [2026-06-02 성능] 슬래브 부재마다 scene 전체를 훑던 O(N²) 두 곳 제거 —
    # "층별 실"·"부모별 내벽" 색인을 1회 구축해 _comp_slab_pressures 가 즉시 조회.
    rooms_by_floor = {}
    for r in getattr(scene, 'rooms', {}).values():
        rooms_by_floor.setdefault(getattr(r, 'floor_index', 0), []).append(r)
    iw_by_parent = {}
    for c in scene.components.values():
        if c.comp_type == ComponentType.INTERIOR_WALL:
            iw_by_parent.setdefault(getattr(c, 'parent_id', 0), []).append(c)

    for cid, comp in scene.components.items():
        # 수직 3층 모듈은 슬래브 4개(1F·2F·3F 바닥 + 옥상)를 자체 보유 →
        # z 레벨로 보를 그룹핑해 슬래브마다 4개 둘레 보를 찾아 분배.
        if isinstance(comp, Vertical3Module):
            _distribute_vertical3_slabs(comp, cid, model, result,
                                        p_dead_v, p_live_v)
            continue

        # 일반 슬래브 보유 부재 — 부재별 실 면적가중 압력(활+SDL) + 옥상 검출.
        if isinstance(comp, (Module, FloorPanel, CantileverSlab)):
            p_dead, p_live = _comp_slab_pressures(
                comp, scene, roof_top_z, rooms_by_floor, iw_by_parent)

        # 어떤 부재가 슬래브를 지지하는지 role 로 결정
        if isinstance(comp, Module):
            roles = {'module_bottom_beam'}
            slab_kind = 'module_floor'
        elif isinstance(comp, FloorPanel):
            roles = {'floor_edge_beam'}
            slab_kind = 'floor_panel'
        elif isinstance(comp, CantileverSlab):
            # 캔틸레버 3개 보 + 접합면 4번째 보 = 4변 슬래브 분배
            cant_mids = [mid for mid in model.comp_to_members.get(cid, [])
                         if model.members[mid].role == 'cantilever_slab_beam']
            if not cant_mids:
                continue
            # 접합면 4번째 보 탐색: 캔틸레버 3보의 양 끝 노드 중
            # 다른 캔틸레버 보와 공유하지 않는 단독 노드 2개 = 접합면 양 끝
            cant_node_count: Dict[int, int] = {}
            for mid in cant_mids:
                m = model.members[mid]
                cant_node_count[m.n1] = cant_node_count.get(m.n1, 0) + 1
                cant_node_count[m.n2] = cant_node_count.get(m.n2, 0) + 1
            # 한 번만 등장하는 노드 = 접합면 양 끝 (p_a, p_d)
            anchor_nids = [nid for nid, cnt in cant_node_count.items() if cnt == 1]
            fourth_mid = _find_beam_between_nodes(anchor_nids, cant_mids, model)
            if fourth_mid is not None:
                mem_ids = cant_mids + [fourth_mid]
            else:
                mem_ids = cant_mids
            if len(mem_ids) == 4:
                # 4변 슬래브 분배 (모듈/FP과 동일)
                long_ids, short_ids, long_len, short_len = (
                    _classify_4_beams_by_direction(mem_ids, model))
                slab_area = long_len * short_len
                one_way = (long_len / short_len) >= SLAB_ONE_WAY_RATIO
                _distribute_slab_pressure(
                    p_dead, long_ids, short_ids, long_len, short_len,
                    result, is_live=False)
                _distribute_slab_pressure(
                    p_live, long_ids, short_ids, long_len, short_len,
                    result, is_live=True)
                result.total_slab_dead_n += p_dead * slab_area
                result.total_live_n += p_live * slab_area
                beam_shares = _build_beam_shares(
                    one_way, long_ids, short_ids, long_len, short_len,
                    p_dead, p_live)
                corners = _collect_rect_corners(mem_ids, model)
                result.slab_distributions.append(SlabDistribution(
                    source_comp_id=cid,
                    slab_kind='cantilever_slab',
                    corners=corners,
                    one_way=one_way,
                    short_len=short_len,
                    long_len=long_len,
                    dead_pressure_n_mm2=p_dead,
                    live_pressure_n_mm2=p_live,
                    short_beam_ids=list(short_ids),
                    long_beam_ids=list(long_ids),
                    beam_shares=beam_shares,
                ))
            else:
                # 4번째 보 못 찾음 — 기존 3보 균등 분배 (폴백)
                w_dim = comp.dimensions.get('width', 0.0)
                d_dim = comp.dimensions.get('depth', 0.0)
                area = w_dim * d_dim
                total_dead = p_dead * area
                total_live = p_live * area
                beam_shares = {}
                for mid in cant_mids:
                    bL = model.get_member_length(mid)
                    if bL < 1e-9:
                        continue
                    lc = result.get(mid)
                    udl_dead = (total_dead / 3.0) / bL
                    udl_live = (total_live / 3.0) / bL
                    lc.slab_dead += udl_dead
                    lc.live += udl_live
                    beam_shares[mid] = BeamShare(
                        'uniform',
                        LOAD_COMBO_DL * udl_dead + LOAD_COMBO_LL * udl_live)
                result.total_slab_dead_n += total_dead
                result.total_live_n += total_live
                corners = _collect_rect_corners(cant_mids, model)
                result.slab_distributions.append(SlabDistribution(
                    source_comp_id=cid, slab_kind='cantilever_slab',
                    corners=corners, one_way=False,
                    short_len=0.0, long_len=0.0,
                    dead_pressure_n_mm2=p_dead, live_pressure_n_mm2=p_live,
                    short_beam_ids=[], long_beam_ids=list(cant_mids),
                    beam_shares=beam_shares))
            continue
        else:
            continue

        mem_ids = [mid for mid in model.comp_to_members.get(cid, [])
                   if mid in model.members
                   and model.members[mid].role in roles
                   and not getattr(model.members[mid], 'is_split_sub', False)]
        # 패널 보가 모듈 보에 병합된 경우: source_comp_ids 로 추가 탐색
        if isinstance(comp, (FloorPanel, StructWall)):
            seen = set(mem_ids)
            for mid, m in model.members.items():
                if mid not in seen and m.kind == 'beam' and cid in m.source_comp_ids:
                    if getattr(m, 'is_split_sub', False):
                        continue
                    mem_ids.append(mid)
                    seen.add(mid)
        # [함정] 모듈 내부 중간보가 긴 변에 붙으면 그 변이 2토막 이상으로 쪼개져
        # module_bottom_beam 이 4개가 아니라 6·8개가 된다. 과거 'len!=4 → continue'
        # 는 그런 모듈의 바닥하중을 통째로 누락시켰다(바닥패널 없는 모듈전용
        # 건물의 강재 물량이 ½ 로 떨어지던 버그). 방향+범위 분류로 N개를 처리.
        if len(mem_ids) < 2:
            continue

        long_ids, short_ids, long_len, short_len = (
            _group_perimeter_beams_by_direction(mem_ids, model))
        # 실제 슬래브 면적 = 컴포넌트 dimensions (보 중심선 사각형이 아닌 외곽).
        # 보 중심선 면적 (long_len × short_len) 만 쓰면 보 외측 슬래브가 누락되어
        # 약 8% 자중·활하중 부족 (4m × 6m 모듈 기준 24m² → 22.04m²).
        slab_w_real = float(comp.dimensions.get('width', long_len))
        slab_d_real = float(comp.dimensions.get('depth', short_len))
        slab_area_real = slab_w_real * slab_d_real

        # 방어: 한 방향만 잡힌 비정상 perimeter → 총하중을 둘레보 길이비로 균등
        # UDL 분배(총량 보존). 정상 모듈/패널은 두 방향이 모두 있어 안 탄다.
        if not long_ids or not short_ids or short_len < 1e-9 or long_len < 1e-9:
            valid_ids = [mid for mid in mem_ids
                         if model.get_member_length(mid) > 1e-9]
            tot_len = sum(model.get_member_length(mid) for mid in valid_ids)
            if tot_len > 1e-9 and slab_area_real > 0:
                _accumulate(result, valid_ids,
                            p_dead * slab_area_real / tot_len, is_live=False)
                _accumulate(result, valid_ids,
                            p_live * slab_area_real / tot_len, is_live=True)
                result.total_slab_dead_n += p_dead * slab_area_real
                result.total_live_n += p_live * slab_area_real
            continue

        slab_area_centerline = long_len * short_len
        # pressure 보정: 분배 식은 보 중심선 기반이지만 적용량은 실제 면적에 맞춤
        if slab_area_centerline > 1e-6:
            area_factor = slab_area_real / slab_area_centerline
        else:
            area_factor = 1.0
        p_dead_eff = p_dead * area_factor
        p_live_eff = p_live * area_factor
        one_way = (long_len / short_len) >= SLAB_ONE_WAY_RATIO

        _distribute_slab_pressure(p_dead_eff, long_ids, short_ids, long_len, short_len, result, is_live=False)
        _distribute_slab_pressure(p_live_eff, long_ids, short_ids, long_len, short_len, result, is_live=True)
        result.total_slab_dead_n += p_dead * slab_area_real
        result.total_live_n += p_live * slab_area_real

        # Stage 1 시각화 메타 기록 (보정된 effective pressure 사용)
        beam_shares = _build_beam_shares(
            one_way, long_ids, short_ids, long_len, short_len,
            p_dead_eff, p_live_eff,
        )
        corners = _collect_rect_corners(mem_ids, model)
        result.slab_distributions.append(SlabDistribution(
            source_comp_id=cid,
            slab_kind=slab_kind,
            corners=corners,
            one_way=one_way,
            short_len=short_len,
            long_len=long_len,
            dead_pressure_n_mm2=p_dead,
            live_pressure_n_mm2=p_live,
            short_beam_ids=list(short_ids),
            long_beam_ids=list(long_ids),
            beam_shares=beam_shares,
        ))


# ── 코어 슬래브 자중 ──────────────────────────────────────────

def _apply_core_slab_self_weight(scene: Scene, model: AnalysisModel,
                                  result: LoadResult) -> None:
    """CoreSlab 자중을 같은 그룹의 코어 column 부재들에 균등 분배.

    [근거]
    CoreSlab 은 토폴로지에 노드/멤버를 만들지 않음 (rigidDiaphragm 으로만 표현).
    그러나 자중은 실재하므로 그 층 코어 column 들에 등분포 형태로 추가해
    OpenSees 평형식에 포함시킨다. (단위 길이당 N/mm 형태로 LoadCase.self_weight 에 누적.)

    분배 규칙:
    - 한 코어 슬래브의 총 무게 W = w·d·t · γ_concrete
    - 같은 group_id + 같은 floor_index 의 코어 column 부재 K 개 → 각 column 에
      W / K 분의 무게를 추가. column 의 길이로 나눠 단위 길이당 분포로 환산.
    - column 이 0 개면(데이터 깨짐) 건너뜀.
    """
    # group_id, floor_index → 코어 column mid 리스트
    core_cols_by_key: Dict[Tuple[int, int], List[int]] = {}
    for mid, m in model.members.items():
        if m.role != 'core_column':
            continue
        if getattr(m, 'is_split_sub', False):
            continue
        # member.source_comp_ids[0] = Core comp_id → scene 에서 group/floor 확인
        if not m.source_comp_ids:
            continue
        owner = scene.components.get(m.source_comp_ids[0])
        if owner is None:
            continue
        key = (getattr(owner, 'group_id', 0), getattr(owner, 'floor_index', 0))
        core_cols_by_key.setdefault(key, []).append(mid)

    for cid, comp in scene.components.items():
        if not isinstance(comp, CoreSlab):
            continue
        w = float(comp.dimensions.get('width', 0.0))
        d = float(comp.dimensions.get('depth', 0.0))
        t = float(comp.dimensions.get('thickness', 180.0))
        if w <= 0 or d <= 0 or t <= 0:
            continue
        W_total = w * d * t * CONCRETE_UNIT_WEIGHT_N_MM3   # N
        key = (getattr(comp, 'group_id', 0), getattr(comp, 'floor_index', 0))
        col_mids = core_cols_by_key.get(key, [])
        if not col_mids:
            continue
        share_per_col = W_total / len(col_mids)
        for mid in col_mids:
            L = model.get_member_length(mid)
            if L <= 0:
                continue
            w_udl = share_per_col / L     # N/mm (단위 길이당)
            lc = result.get(mid)
            lc.slab_dead += w_udl         # 슬래브 사하중 카테고리에 누적
            result.total_slab_dead_n += share_per_col


# ── 벽 채움 자중 → 보 선하중 (5단계) ─────────────────────────

# wall_fills 인덱스 → 운송 wall_classifier 면 이름.
# model/core.Module.wall_fills 순서 = [front(y-), back(y+), left(x-), right(x+)].
# wall_classifier 면 매핑   = 전면(y=0), 후면(y=d), 좌면(x=0), 우면(x=w).
_WALLFILL_FACE_NAME = {0: '전면', 1: '후면', 2: '좌면', 3: '우면'}


def _nearest_beam_to_xy(model: AnalysisModel, beam_ids: List[int],
                        xy) -> Optional[int]:
    """주어진 보들 중 중점 xy 가 xy 에 가장 가까운 보 id. 없으면 None."""
    best, best_d = None, float('inf')
    for mid in beam_ids:
        m = model.members[mid]
        bm = 0.5 * (model.nodes[m.n1].coord[:2] + model.nodes[m.n2].coord[:2])
        d = float(np.linalg.norm(np.asarray(xy) - bm))
        if d < best_d:
            best_d, best = d, mid
    return best


def _apply_wall_loads(scene: Scene, model: AnalysisModel,
                      result: LoadResult) -> None:
    """모듈 4면 벽 + 벽패널 채움 자중을 변의 보에 선하중으로 추가.

    - 단위질량: 운송 wall_classifier 의 외부/내부 자동판별(외부 55 / 내부 30
      가중평균). 모듈은 면별로, 벽패널은 양면 평균.
    - 개구부 면적은 차감. 무게는 그 변에 가장 가까운 보의 self_weight 에 등분포.
    (내벽은 _comp_slab_pressures 에서 슬래브 면적평균으로 별도 처리.)
    """
    try:
        from modular_3d.transport.wall_classifier import (
            classify_module, classify_independent_wall, face_unit_weight,
            ClassifierOptions,
        )
    except Exception as e:
        from modular_3d._utils.debug import log_warn
        log_warn(f"wall_classifier 임포트 실패 — 벽 자중 skip: {e}", cat='load_calculator')
        return
    copts = ClassifierOptions()
    INT, EXT = _INTERIOR_WALL_UNIT_KG_M2, _EXTERIOR_WALL_UNIT_KG_M2
    # [2026-06-02 성능] 비내력벽 내/외부 분류는 xy 평면의 인접 부재 노출 패턴만
    # 의존하고 층(z)에는 무관 → 같은 평면 위치의 벽(=다층 복제)은 분류가 동일.
    # (comp_type, rotation, xy 위치)를 키로 1회만 분류하고 같은 키는 재사용해
    # 층수만큼 반복되던 O(N²) 분류를 1/층수 로 줄인다. 결과 동일성은 회귀 검증.
    def _wkey(c):
        return (c.comp_type, getattr(c, 'rotation', 0),
                round(float(c.position[0]), 1), round(float(c.position[1]), 1))
    _module_face_cache = {}
    _struct_wall_r_cache = {}

    for cid, comp in scene.components.items():
        if isinstance(comp, Module):
            wfs = getattr(comp, 'wall_fills', None) or []
            if not wfs:
                continue
            _k = _wkey(comp)
            faces = _module_face_cache.get(_k)
            if faces is None:
                try:
                    faces = classify_module(comp, scene, copts)
                except Exception:
                    faces = {}
                _module_face_cache[_k] = faces
            beams = [mid for mid in model.comp_to_members.get(cid, [])
                     if mid in model.members
                     and model.members[mid].role == 'module_bottom_beam'
                     and not getattr(model.members[mid], 'is_split_sub', False)]
            if not beams:
                continue
            for i, wf in enumerate(wfs):
                fc = faces.get(_WALLFILL_FACE_NAME.get(i))
                unit = face_unit_weight(fc, INT, EXT) if fc is not None else EXT
                net = _wall_panel_net_area_mm2(
                    wf.corners, _wall_face_openings(comp, f'wall_{i}'))
                W = _wall_panel_weight_n(net, unit)
                if W <= 0:
                    continue
                wc = np.asarray(wf.corners, dtype=np.float64)[:, :2].mean(axis=0)
                best = _nearest_beam_to_xy(model, beams, wc)
                if best is None:
                    continue
                L = model.get_member_length(best)
                if L <= 0:
                    continue
                result.get(best).self_weight += W / L
                result.total_self_weight_n += W

        elif isinstance(comp, StructWall):
            wf = getattr(comp, 'wall_fill', None)
            if wf is None:
                continue
            _k = _wkey(comp)
            r = _struct_wall_r_cache.get(_k)
            if r is None:
                try:
                    fcs = classify_independent_wall(comp, scene, copts)
                    r = 0.5 * (fcs['전면'].exterior_ratio + fcs['후면'].exterior_ratio)
                except Exception:
                    r = 1.0
                _struct_wall_r_cache[_k] = r
            unit = r * EXT + (1.0 - r) * INT
            net = _wall_panel_net_area_mm2(
                wf.corners, _wall_face_openings(comp, 'wall'))
            W = _wall_panel_weight_n(net, unit)
            if W <= 0:
                continue
            runners = [mid for mid in model.comp_to_members.get(cid, [])
                       if mid in model.members
                       and model.members[mid].role == 'wall_bottom_runner'
                       and not getattr(model.members[mid], 'is_split_sub', False)]
            if not runners:
                continue
            best = runners[0]
            L = model.get_member_length(best)
            if L <= 0:
                continue
            result.get(best).self_weight += W / L
            result.total_self_weight_n += W


# ── 메인 ─────────────────────────────────────────────────────

def calculate_loads(scene: Scene, model: AnalysisModel) -> LoadResult:
    """Scene + AnalysisModel → 부재별 LoadCase."""
    result = LoadResult()
    _apply_self_weight(model, result)
    _apply_slab_loads(scene, model, result)
    # 모듈 4면 벽·벽패널 채움 자중 → 변 보 선하중 (내벽은 슬래브 압력에 포함).
    _apply_wall_loads(scene, model, result)
    # (2026-05-12) 코어 슬래브 자중은 이제 실제 셸 자중으로 처리
    # — _apply_core_slab_self_weight 호출 제거 (구버전 core_column 가정 dead code).
    return result
