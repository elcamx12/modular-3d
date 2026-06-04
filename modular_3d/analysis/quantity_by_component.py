"""컴포넌트별/타입별 물량 분해 — 물량탭 개편 Phase 1.

[목적]
기존 quantity_takeoff 는 프로젝트 *전체 합산* 만 제공한다. 물량탭 개편은 각
단면설계 타입(=같은 단면 구성 컴포넌트 묶음)별로 강재(역할·단면별)·데크·콘크리트
물량과 비용을 쪼개 보여줘야 한다. 본 모듈이 그 분해를 담당한다.

[핵심 불변식 — 검증]
Σ(타입별 모든 귀속 cid 의 강재 ton) == aggregate_steel 전체 ton (round 오차 내)
Σ(타입별 귀속 슬래브 면적·부피) == aggregate_slabs 전체
→ 이게 깨지면 귀속 누락(캔틸/Mid/흡수벽) 이 있다는 뜻. verify_against_totals 로 점검.

[귀속 규칙 — "타입에 없는 cid 전부 부모로"]
aggregate_steel 전체는 캔틸·Mid·흡수벽 강재를 모두 포함하나, 타입 목록
(derive_section_types)은 독립부재 본체만 담는다. 따라서:
- 캔틸레버보/슬래브, 중간보/기둥 → parent_id 의 부모 컴포넌트에 귀속.
- 흡수벽(StructWall.merged_fp_id 보유) → merged_fp_id 가 가리키는 바닥패널에 귀속.
- RC 코어/코어슬래브 → 운송·타입 양쪽 제외(전체 슬래브 합산에서도 제외됨).

[강재 본수 — aggregate_steel 과 동일 규칙]
parent_member_id 가 있으면 그 root, 없으면 자기 mid 를 root 로 길이 합산.
is_split_sub 부재는 제외(원본이 길이를 잡음). 분할 sub 도 같은 컴포넌트 소속이라
컴포넌트 경계를 넘지 않는다(topology). 따라서 컴포넌트 단위로 같은 규칙 적용 가능.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from ..model import (
    Scene, SlabData,
    Core, CoreSlab, CantileverBeam, CantileverSlab, MidBeam, MidColumn,
    StructWall,
)
from .topology import AnalysisModel
from .section_design import DesignResult
from .quantity_takeoff import _polygon_area_xy
from .member_roles import classify_role_ko, role_ko_order_key


# ── 자료형 ──────────────────────────────────────────────────
@dataclass
class CompSteelLine:
    """한 컴포넌트(또는 타입) 안의 역할·단면별 강재 한 줄."""
    role_ko: str
    section_name: str
    section_type: str          # 'shs' | 'h'
    count: int                 # 본수 (parent_root 기준)
    total_length_m: float
    total_weight_ton: float
    cost: float                # 원 (중량 × 강재 단가)


@dataclass
class ComponentQuantity:
    """단일 컴포넌트(cid)의 물량 — 본체 + 귀속(캔틸/Mid/흡수벽)."""
    cid: int
    steel_lines: List[CompSteelLine] = field(default_factory=list)
    deck_area_m2: float = 0.0
    concrete_m3: float = 0.0
    steel_cost: float = 0.0
    deck_cost: float = 0.0
    concrete_cost: float = 0.0

    @property
    def steel_weight_ton(self) -> float:
        return sum(l.total_weight_ton for l in self.steel_lines)

    @property
    def total_cost(self) -> float:
        return self.steel_cost + self.deck_cost + self.concrete_cost


@dataclass
class TypeQuantity:
    """단면설계 타입(label)별 물량 — 대표 1개 + 인스턴스 수."""
    type_label: str
    instance_count: int
    member_cids: List[int]                          # 이 타입의 독립부재 cid 목록
    rep: ComponentQuantity                          # 대표 1개(인스턴스당) 물량
    # 역할별 단면명 목록(3D 라벨용) — [(role_ko, section_name), ...]
    section_lines: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def type_total_cost(self) -> float:
        """타입 전체(= 대표 × 인스턴스 수) 비용."""
        return self.rep.total_cost * self.instance_count


@dataclass
class QuantityByComponent:
    """전체 분해 결과 — 타입 목록 + 프로젝트 총합."""
    types: List[TypeQuantity]
    project_steel_ton: float
    project_deck_m2: float
    project_concrete_m3: float
    project_total_cost: float


# ── 단가 묶음 ────────────────────────────────────────────────
@dataclass
class UnitPrices:
    steel_shs: Optional[float] = None     # 원/ton
    steel_h: Optional[float] = None       # 원/ton
    deck: Optional[float] = None          # 원/㎡
    concrete: Optional[float] = None      # 원/㎥
    rebar: Optional[float] = None         # 원/ton (RC 코어 이형철근)

    @classmethod
    def load(cls) -> "UnitPrices":
        from ..카탈로그.material_prices import get_unit_price
        return cls(
            steel_shs=get_unit_price('강재_각형강관_SHS'),
            steel_h=get_unit_price('강재_H형강'),
            deck=get_unit_price('데크슬래브'),
            concrete=get_unit_price('콘크리트_레미콘'),
            rebar=get_unit_price('철근_이형철근_SD400'),
        )

    def steel_unit_for(self, section_type: str) -> float:
        """강종별 ton 단가. H 단가 없으면 SHS 단가로 대체(전체 계산과 동일)."""
        if section_type == 'h':
            if self.steel_h is not None:
                return float(self.steel_h)
            return float(self.steel_shs or 0.0)
        return float(self.steel_shs or 0.0)


# ── 귀속 맵 ──────────────────────────────────────────────────
def build_child_to_parent(scene: Scene) -> Dict[int, int]:
    """타입에 안 잡히는 cid → 부모 cid 귀속 맵.

    - CantileverBeam/Slab, MidBeam/Column: parent_id → 부모.
    - StructWall(merged_fp_id 보유): merged_fp_id → 부모 바닥패널.
    코어 계열은 귀속하지 않음(전체에서도 제외되므로).
    """
    out: Dict[int, int] = {}
    for cid, comp in scene.components.items():
        if isinstance(comp, (CantileverBeam, CantileverSlab, MidBeam, MidColumn)):
            pid = int(getattr(comp, 'parent_id', 0) or 0)
            if pid:
                out[cid] = pid
        elif isinstance(comp, StructWall):
            mfp = getattr(comp, 'merged_fp_id', None)
            if mfp:
                out[cid] = int(mfp)
    return out


# ── 강재 집계 (컴포넌트 단위, aggregate_steel 동일 규칙) ──────
def _steel_lines_for_cids(
    cids: List[int], am: AnalysisModel, design_result: DesignResult,
    prices: UnitPrices,
) -> List[CompSteelLine]:
    """주어진 cid 들의 부재를 역할·단면별로 묶어 강재 줄 리스트 생성.

    parent_root 묶음(aggregate_steel 동일): root 별 길이 합산, is_split_sub 제외.
    역할은 root 부재 기준으로 분류(classify_role_ko). 본수 = root 개수.
    """
    from ..카탈로그.h_sections import HSection

    # root → (역할, 단면, 누적길이, root_cid)
    root_len: Dict[int, float] = defaultdict(float)
    root_role: Dict[int, str] = {}
    root_sec: Dict[int, object] = {}
    cid_set = set(cids)

    for cid in cids:
        for mid in am.comp_to_members.get(cid, []):
            m = am.members.get(mid)
            if m is None:
                continue
            if getattr(m, 'is_split_sub', False):
                continue
            gname = design_result.member_to_group.get(mid)
            if gname is None:
                continue
            gd = design_result.groups.get(gname)
            if gd is None or gd.section is None:
                continue
            sec = gd.section
            L = am.get_member_length(mid)
            root = m.parent_member_id if m.parent_member_id is not None else mid
            root_len[root] += L
            # 역할·단면은 root(자기 mid)일 때 우선 기록.
            if root not in root_sec or root == mid:
                root_sec[root] = sec
                root_role[root] = classify_role_ko(m, am, mid, cid)

    # (역할, 단면명) → [길이 리스트]
    bucket: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    sec_lookup: Dict[str, object] = {}
    for root, total_L in root_len.items():
        sec = root_sec.get(root)
        if sec is None:
            continue
        role = root_role.get(root, '')
        key = (role, sec.name)
        bucket[key].append(total_L)
        sec_lookup[sec.name] = sec

    lines: List[CompSteelLine] = []
    for (role, sec_name) in sorted(
            bucket.keys(), key=lambda k: (role_ko_order_key(k[0]), k[1])):
        lengths = bucket[(role, sec_name)]
        sec = sec_lookup[sec_name]
        count = len(lengths)
        total_len_m = sum(lengths) / 1000.0
        total_wt_ton = (sec.weight_per_m_kg * total_len_m) / 1000.0
        sec_type = 'h' if isinstance(sec, HSection) else 'shs'
        cost = total_wt_ton * prices.steel_unit_for(sec_type)
        lines.append(CompSteelLine(
            role_ko=role, section_name=sec_name, section_type=sec_type,
            count=count, total_length_m=total_len_m,
            total_weight_ton=total_wt_ton, cost=cost,
        ))
    return lines


# ── 슬래브 집계 (컴포넌트 단위) ──────────────────────────────
def _slabs_of_component(comp) -> List[SlabData]:
    """컴포넌트 본체 슬래브 리스트. CoreSlab 은 호출측에서 제외."""
    slist = getattr(comp, 'slabs', None)
    if slist:
        return [s for s in slist if s is not None]
    s = getattr(comp, 'slab', None)
    return [s] if s is not None else []


def _slab_area_vol(slabs: List[SlabData]) -> Tuple[float, float]:
    """슬래브 리스트 → (면적 m², 부피 m³)."""
    area_mm2 = 0.0
    vol_mm3 = 0.0
    for s in slabs:
        a = _polygon_area_xy(s.corners)
        area_mm2 += a
        vol_mm3 += a * s.thickness
    return area_mm2 * 1e-6, vol_mm3 * 1e-9


# ── 단일 컴포넌트 물량 ───────────────────────────────────────
def _component_quantity(
    cid: int, owned_cids: List[int], scene: Scene, am: AnalysisModel,
    design_result: DesignResult, prices: UnitPrices,
) -> ComponentQuantity:
    """cid(본체) + owned_cids(귀속 캔틸/Mid/흡수벽 포함, 본체 자신 포함)의 물량."""
    cq = ComponentQuantity(cid=cid)
    # 강재 — 본체 + 귀속 cid 전부
    cq.steel_lines = _steel_lines_for_cids(owned_cids, am, design_result, prices)
    cq.steel_cost = sum(l.cost for l in cq.steel_lines)
    # 슬래브 — 본체 + 귀속 컴포넌트의 슬래브 (코어 제외)
    slabs: List[SlabData] = []
    for c in owned_cids:
        comp = scene.components.get(c)
        if comp is None or isinstance(comp, (Core, CoreSlab)):
            continue
        slabs.extend(_slabs_of_component(comp))
    area_m2, vol_m3 = _slab_area_vol(slabs)
    cq.deck_area_m2 = area_m2
    cq.concrete_m3 = vol_m3
    cq.deck_cost = area_m2 * float(prices.deck or 0.0)
    cq.concrete_cost = vol_m3 * float(prices.concrete or 0.0)
    return cq


# ── 메인 진입점 ──────────────────────────────────────────────
def build_quantity_by_component(
    scene: Scene, am: AnalysisModel, design_result: DesignResult,
    types: List[dict], prices: Optional[UnitPrices] = None,
) -> QuantityByComponent:
    """타입 목록(derive_section_types) + 단일 출처 DesignResult → 타입별 물량 분해.

    types: derive_section_types 반환 dict 리스트. 각 항목 'label','comp_ids'… 사용.
    """
    if prices is None:
        prices = UnitPrices.load()

    child_to_parent = build_child_to_parent(scene)
    # 부모 cid → 귀속 자식 cid 목록 (역매핑)
    parent_to_children: Dict[int, List[int]] = defaultdict(list)
    for child, parent in child_to_parent.items():
        parent_to_children[parent].append(child)

    type_quantities: List[TypeQuantity] = []
    proj_steel = 0.0
    proj_deck = 0.0
    proj_conc = 0.0
    proj_cost = 0.0

    for t in types:
        label = t.get('label', '?')
        comp_ids = list(t.get('comp_ids', []) or [])
        if not comp_ids:
            continue
        # 대표 1개 = comp_ids 의 첫 cid(rep_comp_id 우선).
        rep_cid = int(t.get('rep_comp_id', comp_ids[0]))
        if rep_cid not in comp_ids:
            rep_cid = comp_ids[0]
        # 대표 컴포넌트가 소유하는 cid 집합 = 본체 + 귀속 자식 (표시용 대표값)
        owned_rep = [rep_cid] + parent_to_children.get(rep_cid, [])
        rep_q = _component_quantity(
            rep_cid, owned_rep, scene, am, design_result, prices)
        # 3D 라벨용 단면 줄 — (역할, 단면명)
        section_lines = [(l.role_ko, l.section_name) for l in rep_q.steel_lines]
        tq = TypeQuantity(
            type_label=label, instance_count=len(comp_ids),
            member_cids=comp_ids, rep=rep_q, section_lines=section_lines,
        )
        type_quantities.append(tq)
        # [정합성] 프로젝트 총합은 *대표 × N* 이 아니라 *전 인스턴스 실측 합산*.
        # 같은 타입이라도 인스턴스마다 길이·종속부재(캔틸) 수가 다를 수 있으므로,
        # 모든 comp_ids 의 본체 + 귀속 자식을 실제로 순회해 더해야 aggregate 전체와
        # 정확히 일치한다(검증식 불변식 보장).
        for cid in comp_ids:
            owned = [cid] + parent_to_children.get(cid, [])
            cq = _component_quantity(cid, owned, scene, am, design_result, prices)
            proj_steel += cq.steel_weight_ton
            proj_deck += cq.deck_area_m2
            proj_conc += cq.concrete_m3
            proj_cost += cq.total_cost

    return QuantityByComponent(
        types=type_quantities,
        project_steel_ton=proj_steel,
        project_deck_m2=proj_deck,
        project_concrete_m3=proj_conc,
        project_total_cost=proj_cost,
    )


# ── 코어 강재 제외 DesignResult (물량탭 전체표용) ────────────
def design_result_without_core(
    scene: Scene, am: AnalysisModel, design_result: DesignResult,
) -> DesignResult:
    """RC 코어 부재(mid)를 member_to_group 에서 제외한 DesignResult 사본.

    [근거 — 2026-06-01 사용자 결정]
    코어는 RC(철근콘크리트)라 강재 물량에서 빼야 일관(코어 콘크리트·철근은 이미
    자재비 제외). 그러나 `aggregate_steel` 은 코어 트러스/러너 강재를 member_to_group
    경유로 포함한다. 물량탭 전체 본수표·자재비표가 *타입별 분해(코어 제외)와 일치*
    하도록, 코어 cid 의 mid 를 member_to_group 에서 뺀 사본을 만들어 aggregate 에 넘긴다.
    원본 design_result·aggregate_steel 은 불변(다른 탭 회귀 방지).
    """
    from .section_design import DesignResult as _DR
    core_mids = set()
    for cid, comp in scene.components.items():
        if isinstance(comp, (Core, CoreSlab)):
            core_mids.update(am.comp_to_members.get(cid, []))
    if not core_mids:
        return design_result
    m2g = {mid: g for mid, g in design_result.member_to_group.items()
           if mid not in core_mids}
    return _DR(
        policy=design_result.policy,
        groups=design_result.groups,
        member_to_group=m2g,
        member_ratios=design_result.member_ratios,
        member_critical=design_result.member_critical,
    )


# ── 검증 ─────────────────────────────────────────────────────
def verify_against_totals(
    qbc: QuantityByComponent, scene: Scene, am: AnalysisModel,
    design_result: DesignResult, tol_rel: float = 1e-3,
) -> Dict[str, object]:
    """Σ(타입) == 전체 합산(aggregate_*) 인지 점검. 차이·통과여부 반환.

    [정합성] project_* 총합은 *전 인스턴스 실측 합산*(build_quantity_by_component 에서
    모든 comp_ids + 귀속 자식 순회). 따라서 인스턴스마다 길이·캔틸이 달라도 aggregate
    전체와 일치해야 한다. 불일치 시 귀속 누락(타입에 없는 cid 미귀속)을 의미.
    """
    from .quantity_takeoff import aggregate_steel, aggregate_slabs

    steel_items = aggregate_steel(am, design_result)
    total_steel_ton = next(
        (it.total_weight_ton for it in steel_items if it.is_total), 0.0)
    slab = aggregate_slabs(scene)

    def _rel_diff(a: float, b: float) -> float:
        denom = max(abs(b), 1e-9)
        return abs(a - b) / denom

    steel_diff = _rel_diff(qbc.project_steel_ton, total_steel_ton)
    deck_diff = _rel_diff(qbc.project_deck_m2, slab.total_area_m2)
    conc_diff = _rel_diff(qbc.project_concrete_m3, slab.total_volume_m3)

    return {
        'steel_ok': steel_diff <= tol_rel,
        'deck_ok': deck_diff <= tol_rel,
        'concrete_ok': conc_diff <= tol_rel,
        'steel': (qbc.project_steel_ton, total_steel_ton, steel_diff),
        'deck': (qbc.project_deck_m2, slab.total_area_m2, deck_diff),
        'concrete': (qbc.project_concrete_m3, slab.total_volume_m3, conc_diff),
    }


__all__ = [
    'CompSteelLine', 'ComponentQuantity', 'TypeQuantity', 'QuantityByComponent',
    'UnitPrices', 'build_child_to_parent', 'build_quantity_by_component',
    'verify_against_totals', 'design_result_without_core',
]
