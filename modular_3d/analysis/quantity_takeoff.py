"""물량 집계 — 강재 본수·길이·중량 + 슬래브 부피.

Phase 3 — 설계서 3-A ~ 3-E 섹션 구현.

흐름:
  AnalysisModel + DesignResult → aggregate_steel(per policy)
  scene → aggregate_slabs
  → QuantityReport
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from collections import defaultdict

import numpy as np

from ..model import Scene, ComponentType, SlabData, Core, CoreSlab
from .topology import AnalysisModel
from .section_catalog import SHSSection
from .section_design import DesignResult, ALL_POLICIES
from .constants import STEEL_DENSITY_KG_M3, CONCRETE_DENSITY_KG_M3


# ── 물량 자료형 ──────────────────────────────────────────────
@dataclass
class SteelLineItem:
    """강재 본수표 한 행."""
    section_name: str
    length_mm: float          # 같은 단면+같은 길이 묶음 (round 후)
    count: int
    total_length_m: float
    unit_weight_kg_m: float
    total_weight_ton: float
    is_total: bool = False


@dataclass
class SlabQuantity:
    """슬래브 부피 + 철근 추정 중량."""
    total_area_m2: float
    thickness_mm: float
    total_volume_m3: float
    rebar_ratio: float                 # 부피 비율 (예: 0.012 = 1.2%)
    rebar_weight_ton: float
    n_slabs: int


@dataclass
class CoreQuantity:
    """RC 코어 물량 — 코어벽(솔리드 직육면체) + 코어 슬래브 통합.

    설계서 §3.7 Q13: 모듈 콘크리트와 별도 카테고리로 산정. 단위는 모듈 슬래브와
    동일하게 콘크리트 m³, 철근 ton.
    """
    wall_volume_m3: float          # 코어벽 콘크리트 (Σ L × t × h)
    slab_volume_m3: float          # 코어 슬래브 콘크리트
    total_volume_m3: float         # 합산
    rebar_ratio: float
    rebar_weight_ton: float
    n_walls: int
    n_slabs: int


@dataclass
class QuantityReport:
    """1정책에 대한 종합 물량 보고서. 정책별 사전이 메인 진입점."""
    policy: str
    steel_items: List[SteelLineItem]
    slab: SlabQuantity
    sections_by_group: Dict[str, SHSSection]
    member_ratios: Dict[int, float]
    member_critical: Dict[int, str]
    ng_groups: List[str]
    # 2026-05-12 RC 코어 별도 카테고리
    core: 'CoreQuantity | None' = None


# ── 슬래브 면적 ──────────────────────────────────────────────
def _polygon_area_xy(corners: np.ndarray) -> float:
    """4-각 폴리곤의 xy 평면 면적 (Shoelace).

    corners: (4, 3) — z 는 무시 (수평 슬래브 가정)
    """
    if corners.shape[0] < 3:
        return 0.0
    x = corners[:, 0]
    y = corners[:, 1]
    n = len(x)
    s = 0.0
    for i in range(n):
        j = (i + 1) % n
        s += x[i] * y[j] - x[j] * y[i]
    return abs(s) * 0.5


def _collect_slabs(scene: Scene) -> List[SlabData]:
    """scene 의 모든 SlabData 수집 (모듈 내부 + 바닥패널 + 캔틸레버 슬래브).

    [제외]
    CoreSlab 은 RC 코어 별도 카테고리(aggregate_core)에서 처리되므로 제외.
    """
    slabs: List[SlabData] = []
    for comp in scene.components.values():
        # RC 코어 슬래브는 별도 카테고리
        if isinstance(comp, CoreSlab):
            continue
        # 모듈: slabs 리스트
        slist = getattr(comp, 'slabs', None)
        if slist:
            for s in slist:
                if s is not None:
                    slabs.append(s)
            continue
        # 단일 slab 속성 (FloorPanel, CantileverSlab 등)
        s = getattr(comp, 'slab', None)
        if s is not None:
            slabs.append(s)
    return slabs


# ── 슬래브 집계 ──────────────────────────────────────────────
DEFAULT_REBAR_RATIO = 0.012   # 1.2 vol% (KBC 표준 슬래브 평균 가정)


def aggregate_slabs(scene: Scene,
                    rebar_ratio: float = DEFAULT_REBAR_RATIO) -> SlabQuantity:
    """슬래브 부피 + 철근 추정 중량 (m³ 기준).

    면적 합산 방식: 두께가 다른 슬래브가 섞여 있어도 부피 = 면적 × 두께 합.
    표시용으로 평균 두께를 thickness_mm 에 기록.
    """
    slabs = _collect_slabs(scene)
    total_area_mm2 = 0.0
    total_volume_mm3 = 0.0
    for s in slabs:
        a = _polygon_area_xy(s.corners)
        total_area_mm2 += a
        total_volume_mm3 += a * s.thickness

    total_area_m2 = total_area_mm2 * 1e-6
    total_volume_m3 = total_volume_mm3 * 1e-9
    avg_thickness = (total_volume_mm3 / total_area_mm2
                     if total_area_mm2 > 0 else 0.0)

    # 철근 = 부피 × 비율 → 강재밀도(kg/m³) → ton
    rebar_volume_m3 = total_volume_m3 * rebar_ratio
    rebar_weight_ton = rebar_volume_m3 * STEEL_DENSITY_KG_M3 / 1000.0

    return SlabQuantity(
        total_area_m2=total_area_m2,
        thickness_mm=avg_thickness,
        total_volume_m3=total_volume_m3,
        rebar_ratio=rebar_ratio,
        rebar_weight_ton=rebar_weight_ton,
        n_slabs=len(slabs),
    )


# ── RC 코어 별도 집계 ────────────────────────────────────────

def aggregate_core(scene: Scene,
                   rebar_ratio: float = DEFAULT_REBAR_RATIO) -> CoreQuantity:
    """RC 코어벽(솔리드 직육면체) + 코어 슬래브의 콘크리트 + 철근 물량.

    [근거]
    설계서 §3.7 Q13: 모듈 슬래브와 분리해 RC 코어 별도 카테고리. 콘크리트 단위 m³,
    철근 단위 ton (모듈 슬래브와 동일 단위).

    [계산]
    - 코어벽 부피 = Σ (L × t × h) — Core.dimensions 의 width/depth/height
    - 코어 슬래브 부피 = Σ (w × d × thickness) — CoreSlab.dimensions
    - 철근 = 총부피 × rebar_ratio × 강재밀도
    """
    wall_vol_mm3 = 0.0
    slab_vol_mm3 = 0.0
    n_walls = 0
    n_slabs = 0
    for comp in scene.components.values():
        if isinstance(comp, Core):
            L = float(comp.dimensions.get('width', 0.0))
            t = float(comp.dimensions.get('depth', 0.0))
            h = float(comp.dimensions.get('height', 0.0))
            wall_vol_mm3 += L * t * h
            n_walls += 1
        elif isinstance(comp, CoreSlab):
            w = float(comp.dimensions.get('width', 0.0))
            d = float(comp.dimensions.get('depth', 0.0))
            thk = float(comp.dimensions.get('thickness', 180.0))
            slab_vol_mm3 += w * d * thk
            n_slabs += 1

    wall_volume_m3 = wall_vol_mm3 * 1e-9
    slab_volume_m3 = slab_vol_mm3 * 1e-9
    total_volume_m3 = wall_volume_m3 + slab_volume_m3
    rebar_volume_m3 = total_volume_m3 * rebar_ratio
    rebar_weight_ton = rebar_volume_m3 * STEEL_DENSITY_KG_M3 / 1000.0

    return CoreQuantity(
        wall_volume_m3=wall_volume_m3,
        slab_volume_m3=slab_volume_m3,
        total_volume_m3=total_volume_m3,
        rebar_ratio=rebar_ratio,
        rebar_weight_ton=rebar_weight_ton,
        n_walls=n_walls,
        n_slabs=n_slabs,
    )


# ── 강재 본수 집계 ──────────────────────────────────────────
LENGTH_ROUND_MM = 1.0    # 길이 그룹화 정확도 (1mm)


def aggregate_steel(am: AnalysisModel,
                    design_result: DesignResult) -> List[SteelLineItem]:
    """단면+길이 동일 부재를 한 줄로 묶어 본수표 생성.

    (2026-05-19 작업 2) 공장 제작 단위 1본 = 분할 전 원본 부재.
    같은 parent_member_id 를 가진 부재들의 길이를 합쳐 1본으로 집계.
    parent_member_id 가 None 인 부재는 그 자체가 1본.
    joint_rules R05/R06 분할(is_split_sub=True) 은 원본 mid 가 그대로 design
    매칭에 잡혀 길이 합산이 자연스럽게 이뤄지고 sub 들은 매칭 제외라 누락 위험
    없음. host 분할(_split_hosts_for_mid_endpoints) 은 두 부재 모두 정식
    부재 + 같은 parent_member_id root 를 갖도록 표시되어 여기서 합쳐진다.

    마지막 행은 합계 (is_total=True).
    """
    # 1) parent_root 별 (section_name, total_length) 누적.
    # parent_root: parent_member_id 가 있으면 그 값, 없으면 자기 mid.
    # 단, is_split_sub 인 부재는 원본 mid 가 design 매칭을 잡고 있으므로 건너뜀
    # (원본 길이 자체가 분할 전 통짜라고 가정 — joint_rules 정책).
    # 그룹 결정 우선순위: root 부재의 그룹 → 못 찾으면 sub 부재의 그룹.
    root_to_len: Dict[int, float] = defaultdict(float)
    root_to_section_name: Dict[int, str] = {}
    section_lookup: Dict[str, SHSSection] = {}

    for mid, member in am.members.items():
        # joint_rules 의 sub 는 원본이 자중·물량을 잡고 있어 길이 합산에서 제외
        if getattr(member, 'is_split_sub', False):
            continue
        gname = design_result.member_to_group.get(mid)
        if gname is None:
            continue
        gd = design_result.groups.get(gname)
        if gd is None:
            continue
        sec = gd.section
        L = am.get_member_length(mid)
        root = member.parent_member_id if member.parent_member_id is not None else mid
        root_to_len[root] += L
        # 그룹/단면은 root 의 것을 우선 — 보통 같은 그룹이지만 host 분할 sub 가
        # 다른 그룹으로 잡힐 경우 root 채택이 더 일관됨.
        if root not in root_to_section_name or root == mid:
            root_to_section_name[root] = sec.name
        section_lookup[sec.name] = sec

    # 2) (section_name, length_round) → [길이 리스트].
    bucket: Dict[Tuple[str, float], List[float]] = defaultdict(list)
    for root, total_L in root_to_len.items():
        sec_name = root_to_section_name[root]
        L_round = round(total_L / LENGTH_ROUND_MM) * LENGTH_ROUND_MM
        key = (sec_name, L_round)
        bucket[key].append(total_L)

    items: List[SteelLineItem] = []
    total_length_m = 0.0
    total_weight_ton = 0.0
    # 정렬: 단면 이름 → 길이 오름차순
    for (sec_name, L_round) in sorted(bucket.keys(),
                                       key=lambda k: (k[0], k[1])):
        lengths = bucket[(sec_name, L_round)]
        sec = section_lookup[sec_name]
        count = len(lengths)
        # 실제 길이의 평균이 아닌 round 값 사용 (집계의 일관성)
        item_total_length_m = (L_round * count) / 1000.0
        item_total_weight_ton = (sec.weight_per_m_kg * item_total_length_m) / 1000.0
        items.append(SteelLineItem(
            section_name=sec_name,
            length_mm=L_round,
            count=count,
            total_length_m=item_total_length_m,
            unit_weight_kg_m=sec.weight_per_m_kg,
            total_weight_ton=item_total_weight_ton,
        ))
        total_length_m += item_total_length_m
        total_weight_ton += item_total_weight_ton

    # 합계 행
    items.append(SteelLineItem(
        section_name='합계',
        length_mm=0.0,
        count=sum(it.count for it in items),
        total_length_m=total_length_m,
        unit_weight_kg_m=0.0,
        total_weight_ton=total_weight_ton,
        is_total=True,
    ))
    return items


# ── 통합 ────────────────────────────────────────────────────
def build_quantity_report(scene: Scene, am: AnalysisModel,
                          design_result: DesignResult) -> QuantityReport:
    """단일 정책에 대한 QuantityReport 생성."""
    steel = aggregate_steel(am, design_result)
    slab = aggregate_slabs(scene)
    core = aggregate_core(scene)
    sections_by_group = {gname: gd.section
                          for gname, gd in design_result.groups.items()}
    return QuantityReport(
        policy=design_result.policy,
        steel_items=steel,
        slab=slab,
        sections_by_group=sections_by_group,
        member_ratios=design_result.member_ratios,
        member_critical=design_result.member_critical,
        ng_groups=design_result.ng_groups,
        core=core,
    )


def build_all_reports(scene: Scene, am: AnalysisModel,
                      design_results: Dict[str, DesignResult]
                      ) -> Dict[str, QuantityReport]:
    """1종/2종/3종 모든 정책의 QuantityReport 생성."""
    return {p: build_quantity_report(scene, am, design_results[p])
            for p in ALL_POLICIES}


__all__ = [
    'SteelLineItem', 'SlabQuantity', 'CoreQuantity', 'QuantityReport',
    'aggregate_slabs', 'aggregate_steel', 'aggregate_core',
    'build_quantity_report', 'build_all_reports',
    'DEFAULT_REBAR_RATIO',
]
