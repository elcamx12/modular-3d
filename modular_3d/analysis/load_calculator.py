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
    """(슬래브 자중 압력, 활하중 압력)  N/mm²."""
    p_dead = SLAB_THICKNESS_MM * CONCRETE_UNIT_WEIGHT_N_MM3   # N/mm²
    p_live = LIVE_LOAD_N_MM2
    return p_dead, p_live


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
            print(f"[load] WARN: vertical_module #{cid} 슬래브 {slab_kind} 둘레 보가 "
                  f"4개 아님 ({len(cand)}, target_z={target_z:.0f}) — skip")
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
    """슬래브 하중을 둘레 보에 분배."""
    p_dead, p_live = _slab_pressures()

    for cid, comp in scene.components.items():
        # 수직 3층 모듈은 슬래브 4개(1F·2F·3F 바닥 + 옥상)를 자체 보유 →
        # z 레벨로 보를 그룹핑해 슬래브마다 4개 둘레 보를 찾아 분배.
        if isinstance(comp, Vertical3Module):
            _distribute_vertical3_slabs(comp, cid, model, result, p_dead, p_live)
            continue

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
        if len(mem_ids) != 4:
            print(f"[load] WARN: comp #{cid} 둘레 보가 4개 아님 ({len(mem_ids)}) — 슬래브 분배 skip")
            continue

        long_ids, short_ids, long_len, short_len = _classify_4_beams_by_direction(mem_ids, model)
        # 실제 슬래브 면적 = 컴포넌트 dimensions (보 중심선 사각형이 아닌 외곽).
        # 보 중심선 면적 (long_len × short_len) 만 쓰면 보 외측 슬래브가 누락되어
        # 약 8% 자중·활하중 부족 (4m × 6m 모듈 기준 24m² → 22.04m²).
        slab_w_real = float(comp.dimensions.get('width', long_len))
        slab_d_real = float(comp.dimensions.get('depth', short_len))
        slab_area_real = slab_w_real * slab_d_real
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


# ── 메인 ─────────────────────────────────────────────────────

def calculate_loads(scene: Scene, model: AnalysisModel) -> LoadResult:
    """Scene + AnalysisModel → 부재별 LoadCase."""
    result = LoadResult()
    _apply_self_weight(model, result)
    _apply_slab_loads(scene, model, result)
    # (2026-05-12) 코어 슬래브 자중은 이제 실제 셸 자중으로 처리
    # — _apply_core_slab_self_weight 호출 제거 (구버전 core_column 가정 dead code).
    return result
