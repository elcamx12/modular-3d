"""부재 분류 + 그룹핑 + 단면 자동 선정.

물량산출 Phase 2 — 설계서 2-A ~ 2-E 섹션 구현.

흐름:
  AnalysisModel + OpsResults → classify_member → group_members
  → extract_demand → select_section_for_group → DesignResult
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

import numpy as np

from .topology import AnalysisModel, AnalysisMember
from .ops_solver import OpsResults, MemberForce
from .section_catalog import SHSSection, SHS_CATALOG
from .strength_check import (
    MemberDemand, CheckResult, check_member,
)


# ── 정책 정의 ─────────────────────────────────────────────────
POLICY_1 = '1종'
POLICY_2 = '2종'
POLICY_3 = '3종'
ALL_POLICIES = (POLICY_1, POLICY_2, POLICY_3)


# ── 부재 분류 카테고리 ──────────────────────────────────────
CAT_MODULE_COLUMN = 'module_column'
CAT_MODULE_BEAM = 'module_beam'
CAT_CANTILEVER_BEAM = 'cantilever_beam'
CAT_FLOOR_PANEL_BEAM = 'floor_panel_beam'
CAT_WALL_PANEL_COLUMN = 'wall_panel_column'
CAT_WALL_PANEL_BEAM = 'wall_panel_beam'
# RC 코어 — 강재 산정·집계 대상에서 제외 (vs 보고서 #45/#46/#57 정정).
CAT_RC_CORE = 'rc_core'

# role → 카테고리 매핑
_ROLE_TO_CATEGORY: Dict[str, str] = {
    'module_column':       CAT_MODULE_COLUMN,
    'mid_column':          CAT_MODULE_COLUMN,    # 중간노드 분할 기둥
    'module_top_beam':     CAT_MODULE_BEAM,
    'module_bottom_beam':  CAT_MODULE_BEAM,
    'mid_beam':            CAT_MODULE_BEAM,      # 중간노드 분할 보
    'cantilever_beam':     CAT_CANTILEVER_BEAM,
    'cantilever_slab_beam': CAT_CANTILEVER_BEAM,
    'floor_edge_beam':     CAT_FLOOR_PANEL_BEAM,
    'wall_column':         CAT_WALL_PANEL_COLUMN,
    'wall_top_runner':     CAT_WALL_PANEL_BEAM,
    'wall_bottom_runner':  CAT_WALL_PANEL_BEAM,
    # RC 코어 부재 — 강재 카탈로그 산정에서 분리
    'core_column':         CAT_RC_CORE,
    'core_slab_beam':      CAT_RC_CORE,
}


def classify_member(member: AnalysisMember) -> str:
    """role 기반 카테고리 분류.

    role 미매핑 시 kind 로 fallback. shell kind 는 RC 코어로 본다 (강재 셸
    부재는 본 시스템에 없음 — 강재 셸이 도입되면 별도 매핑 추가).
    """
    cat = _ROLE_TO_CATEGORY.get(member.role)
    if cat is not None:
        return cat
    # fallback
    if member.kind == 'shell':
        return CAT_RC_CORE
    if member.kind == 'column':
        return CAT_MODULE_COLUMN
    return CAT_MODULE_BEAM


# ── 그룹 정책 ────────────────────────────────────────────────
def group_categories(policy: str) -> Dict[str, List[str]]:
    """정책별 그룹명 → 포함 카테고리 리스트.

    1종: 'all_steel' = 전체
    2종: 'columns'   = 모듈기둥 + 벽패널기둥
         'beams'     = 모듈보 + 캔틸 + 바닥패널보 + 벽패널보
    3종: 'columns'    = 모듈기둥 + 벽패널기둥
         'beams'      = 모듈보 + 바닥패널보 + 벽패널보
         'cantilevers' = 캔틸레버 보
    """
    if policy == POLICY_1:
        return {'all_steel': [
            CAT_MODULE_COLUMN, CAT_MODULE_BEAM,
            CAT_CANTILEVER_BEAM, CAT_FLOOR_PANEL_BEAM,
            CAT_WALL_PANEL_COLUMN, CAT_WALL_PANEL_BEAM,
        ]}
    if policy == POLICY_2:
        return {
            'columns': [CAT_MODULE_COLUMN, CAT_WALL_PANEL_COLUMN],
            'beams':   [CAT_MODULE_BEAM, CAT_CANTILEVER_BEAM,
                        CAT_FLOOR_PANEL_BEAM, CAT_WALL_PANEL_BEAM],
        }
    if policy == POLICY_3:
        return {
            'columns':     [CAT_MODULE_COLUMN, CAT_WALL_PANEL_COLUMN],
            'beams':       [CAT_MODULE_BEAM, CAT_FLOOR_PANEL_BEAM,
                            CAT_WALL_PANEL_BEAM],
            'cantilevers': [CAT_CANTILEVER_BEAM],
        }
    raise ValueError(f"unknown policy: {policy}")


def group_members(am: AnalysisModel, policy: str
                  ) -> Dict[str, List[int]]:
    """정책에 따라 부재 ID 를 그룹별로 분류.

    Returns:
        dict[group_name → list[member_id]]
    """
    cats_map = group_categories(policy)
    # 카테고리 → 그룹명 역인덱스
    cat_to_group: Dict[str, str] = {}
    for gname, cats in cats_map.items():
        for c in cats:
            cat_to_group[c] = gname

    groups: Dict[str, List[int]] = {g: [] for g in cats_map}
    for mid, m in am.members.items():
        cat = classify_member(m)
        gname = cat_to_group.get(cat)
        if gname is None:
            continue
        groups[gname].append(mid)
    return groups


# ── 부재 demand 추출 ────────────────────────────────────────
def _kind_from_category(category: str) -> str:
    """6 카테고리를 strength_check 의 3 kind 로 단순화."""
    if category in (CAT_MODULE_COLUMN, CAT_WALL_PANEL_COLUMN):
        return 'column'
    if category == CAT_CANTILEVER_BEAM:
        return 'cantilever'
    return 'beam'


def extract_demand(am: AnalysisModel, mid: int,
                   ops_results: OpsResults) -> MemberDemand:
    """OpsResults 의 부재 양단 단면력에서 절댓값 최댓값을 추출.

    OpenSees localForce 형식: f_i, f_j = [N, Vy, Vz, T, My, Mz]
    """
    member = am.members[mid]
    L = am.get_member_length(mid)
    cat = classify_member(member)
    kind = _kind_from_category(cat)

    mf: Optional[MemberForce] = ops_results.member_forces.get(mid)
    if mf is None:
        return MemberDemand(P=0, Mz=0, My=0, Vy=0, Vz=0,
                            L=L, kind=kind)

    # 양단 절댓값 max — 가장 가혹한 단면력 채택
    P = max(abs(mf.f_i[0]), abs(mf.f_j[0]))
    # 부호 보존: 압축이면 음수, 인장이면 양수
    # f_i[0] = 시작단에서 부재가 받는 축력. 압축 = 음수.
    # 두 단부 평균 부호로 결정 (보수적: 둘 중 압축이면 압축)
    if mf.f_i[0] < 0 or mf.f_j[0] < 0:
        P = -P
    Vy = max(abs(mf.f_i[1]), abs(mf.f_j[1]))
    Vz = max(abs(mf.f_i[2]), abs(mf.f_j[2]))
    My = max(abs(mf.f_i[4]), abs(mf.f_j[4]))
    Mz = max(abs(mf.f_i[5]), abs(mf.f_j[5]))

    return MemberDemand(
        P=P, Mz=Mz, My=My, Vy=Vy, Vz=Vz,
        L=L, kind=kind, K=1.0,
    )


def extract_all_demands(am: AnalysisModel, ops_results: OpsResults
                        ) -> Dict[int, MemberDemand]:
    """모든 부재의 demand 를 한꺼번에 추출."""
    return {mid: extract_demand(am, mid, ops_results)
            for mid in am.members.keys()}


def extract_envelope_demands(am: AnalysisModel,
                              all_results: Dict[str, OpsResults]
                              ) -> Dict[int, MemberDemand]:
    """(2026-05-19 작업 5) 모든 하중조합의 단면력 절댓값 최대로 envelope demand 생성.

    [근거] "지배조합" 옵션 — 5 케이스 중 가장 가혹한 단면력을 부재별로 골라
    설계하면 어떤 단일 조합에서도 단면이 안전하다. 사용자가 "지배조합" 선택 시
    이 demand 로 design_for_policy 가 돈다.

    부호 정책:
      - P: 어떤 케이스든 압축(음수) 이면 압축 부호 유지(보수적).
      - V/M: 절댓값 최대만 의미 있음.
    """
    keys = list(am.members.keys())
    out: Dict[int, MemberDemand] = {}
    for mid in keys:
        member = am.members[mid]
        L = am.get_member_length(mid)
        cat = classify_member(member)
        kind = _kind_from_category(cat)
        P_abs = 0.0
        P_compressive = False
        Vy = 0.0; Vz = 0.0; My = 0.0; Mz = 0.0
        for res in all_results.values():
            mf = res.member_forces.get(mid)
            if mf is None:
                continue
            p_here = max(abs(mf.f_i[0]), abs(mf.f_j[0]))
            if p_here > P_abs:
                P_abs = p_here
            if mf.f_i[0] < 0 or mf.f_j[0] < 0:
                P_compressive = True
            Vy = max(Vy, abs(mf.f_i[1]), abs(mf.f_j[1]))
            Vz = max(Vz, abs(mf.f_i[2]), abs(mf.f_j[2]))
            My = max(My, abs(mf.f_i[4]), abs(mf.f_j[4]))
            Mz = max(Mz, abs(mf.f_i[5]), abs(mf.f_j[5]))
        P = -P_abs if P_compressive else P_abs
        out[mid] = MemberDemand(P=P, Mz=Mz, My=My, Vy=Vy, Vz=Vz,
                                 L=L, kind=kind, K=1.0)
    return out


def design_all_policies_envelope(am: AnalysisModel,
                                   all_results: Dict[str, OpsResults],
                                   catalog: List[SHSSection] = SHS_CATALOG
                                   ) -> Dict[str, DesignResult]:
    """모든 정책 산정을 envelope demand 기반으로 수행 (지배조합)."""
    demands = extract_envelope_demands(am, all_results)
    return {p: design_for_policy(am, demands, p, catalog)
            for p in ALL_POLICIES}


# ── 그룹 단면 자동 선정 ─────────────────────────────────────
@dataclass
class GroupDesign:
    """그룹 단면 산정 결과."""
    group_name: str
    member_ids: List[int]
    section: SHSSection
    max_ratio: float
    ng_flag: bool                              # 어떤 후보도 OK 못 했음
    member_results: Dict[int, CheckResult]      # 채택 단면 기준 부재별 결과


def select_section_for_group(member_ids: List[int],
                             demands: Dict[int, MemberDemand],
                             catalog: List[SHSSection] = SHS_CATALOG
                             ) -> GroupDesign:
    """카탈로그를 단면적 오름차순 순회 → 그룹 모든 부재 OK 인 첫 단면 채택.

    OK 단면이 없으면 가장 큰 단면 채택 + ng_flag=True.
    """
    if not member_ids:
        # 빈 그룹 — 가장 작은 단면 + 비어있음
        return GroupDesign(
            group_name='', member_ids=[],
            section=catalog[0], max_ratio=0.0,
            ng_flag=False, member_results={},
        )

    last_results: Dict[int, CheckResult] = {}
    last_max = 0.0
    for sec in catalog:
        results: Dict[int, CheckResult] = {}
        max_ratio = 0.0
        all_ok = True
        for mid in member_ids:
            d = demands.get(mid)
            if d is None:
                continue
            r = check_member(sec, d)
            results[mid] = r
            if r.ratio_max > max_ratio:
                max_ratio = r.ratio_max
            if not r.ok:
                all_ok = False
        last_results = results
        last_max = max_ratio
        if all_ok:
            return GroupDesign(
                group_name='', member_ids=member_ids,
                section=sec, max_ratio=max_ratio,
                ng_flag=False, member_results=results,
            )

    # 어떤 단면도 OK 못 함 — 가장 큰 단면(마지막) 채택 + 경고
    return GroupDesign(
        group_name='', member_ids=member_ids,
        section=catalog[-1], max_ratio=last_max,
        ng_flag=True, member_results=last_results,
    )


# ── 통합 산정 ────────────────────────────────────────────────
@dataclass
class DesignResult:
    """한 정책에 대한 전체 산정 결과."""
    policy: str
    groups: Dict[str, GroupDesign]                  # 그룹명 → GroupDesign
    member_to_group: Dict[int, str]                  # 부재ID → 그 부재 소속 그룹명
    member_ratios: Dict[int, float] = field(default_factory=dict)
    member_critical: Dict[int, str] = field(default_factory=dict)

    @property
    def ng_groups(self) -> List[str]:
        return [g for g, gd in self.groups.items() if gd.ng_flag]


def design_for_policy(am: AnalysisModel, demands: Dict[int, MemberDemand],
                      policy: str,
                      catalog: List[SHSSection] = SHS_CATALOG
                      ) -> DesignResult:
    """단일 정책에 대해 그룹별 단면 산정."""
    groups_def = group_members(am, policy)
    groups: Dict[str, GroupDesign] = {}
    member_to_group: Dict[int, str] = {}
    member_ratios: Dict[int, float] = {}
    member_critical: Dict[int, str] = {}

    for gname, mids in groups_def.items():
        gd = select_section_for_group(mids, demands, catalog)
        gd.group_name = gname
        groups[gname] = gd
        for mid in mids:
            member_to_group[mid] = gname
            r = gd.member_results.get(mid)
            if r is not None:
                member_ratios[mid] = r.ratio_max
                member_critical[mid] = r.critical_item

    return DesignResult(
        policy=policy, groups=groups,
        member_to_group=member_to_group,
        member_ratios=member_ratios,
        member_critical=member_critical,
    )


def design_all_policies(am: AnalysisModel, ops_results: OpsResults,
                        catalog: List[SHSSection] = SHS_CATALOG
                        ) -> Dict[str, DesignResult]:
    """1종/2종/3종 모두 산정 → dict[policy → DesignResult]."""
    demands = extract_all_demands(am, ops_results)
    return {p: design_for_policy(am, demands, p, catalog)
            for p in ALL_POLICIES}


# ── 기둥 그룹 층구간 최적 분할 (2026-05-19) ─────────────────
def split_columns_by_floor(
        am: 'AnalysisModel',
        demands: Dict[int, MemberDemand],
        base_result: DesignResult,
        floor_of_mid: Dict[int, int],
        n_segments: int,
        catalog: List[SHSSection] = SHS_CATALOG,
) -> DesignResult:
    """기둥 그룹을 층 범위 K개 구간으로 동적계획법 최적 분할.

    동기: 1층 기둥은 위층 누적 하중을 다 받지만 최상층 기둥은 가벼움.
    한 단면으로 전체를 설계하면 상층부 강재가 과도. 사용자가 K(구간 수)
    를 주면 "총 강재 중량 최소" 가 되도록 층 범위를 K 구간으로 자른다.

    인자
      base_result : 분할 전 정책 결과 (1·2·3종 모두 가능). 'columns'
                    그룹이 없으면 base_result 그대로 반환.
      floor_of_mid: 기둥 부재 mid → 층 번호(1부터). UI 측이 부재 z 평균
                    기반으로 만들어 넘김.
      n_segments  : 1 이면 분할 없음 → base_result 반환.

    반환
      새 DesignResult — 'columns' 그룹이 'columns_F{a}_{b}' 식 K 그룹으로
      쪼개진다. 기존 보/캔틸 그룹은 그대로.
    """
    if n_segments <= 1:
        return base_result
    if 'columns' not in base_result.groups:
        return base_result

    col_mids = base_result.groups['columns'].member_ids
    if not col_mids:
        return base_result

    # 층별 부재 분류
    by_floor: Dict[int, List[int]] = defaultdict(list)
    for mid in col_mids:
        f = floor_of_mid.get(mid)
        if f is None:
            continue
        by_floor[f].append(mid)
    floors = sorted(by_floor.keys())
    N = len(floors)
    K = min(n_segments, N)
    if K <= 1:
        return base_result

    # 구간 [i..j] (floors index) 비용 = 채택 단면 단위중량 × 그룹 길이 합
    # 단면 미선정/NG 시 가장 큰 단면 + 큰 페널티.
    def seg_cost(i: int, j: int):
        mids: List[int] = []
        total_len = 0.0
        for k in range(i, j + 1):
            for mid in by_floor[floors[k]]:
                mids.append(mid)
                total_len += am.get_member_length(mid)
        gd = select_section_for_group(mids, demands, catalog)
        weight = gd.section.weight_per_m_kg * (total_len / 1000.0)
        penalty = 1e9 if gd.ng_flag else 0.0
        return weight + penalty, gd, mids

    # DP[k][n] : 처음 n+1 층(floors[0..n]) 을 k+1 구간으로 나눈 최소 비용
    INF = float('inf')
    dp = [[INF] * N for _ in range(K)]
    back = [[-1] * N for _ in range(K)]
    cost_cache: Dict[tuple, tuple] = {}

    def cost(i: int, j: int):
        if (i, j) not in cost_cache:
            cost_cache[(i, j)] = seg_cost(i, j)
        return cost_cache[(i, j)]

    for n in range(N):
        dp[0][n] = cost(0, n)[0]

    for k in range(1, K):
        for n in range(k, N):
            best = INF; bm = -1
            for m in range(k - 1, n):
                c = dp[k - 1][m] + cost(m + 1, n)[0]
                if c < best:
                    best = c; bm = m
            dp[k][n] = best
            back[k][n] = bm

    # 역추적 — 구간 경계
    bounds: List[tuple] = []
    end = N - 1
    for k in range(K - 1, -1, -1):
        start = (back[k][end] + 1) if k > 0 else 0
        bounds.append((start, end))
        end = back[k][end]
    bounds.reverse()

    # 새 그룹 구성 — 'columns' 제거, 'columns_F{a}_{b}' K 개 추가
    new_groups = {g: gd for g, gd in base_result.groups.items()
                  if g != 'columns'}
    new_m2g = {mid: g for mid, g in base_result.member_to_group.items()
               if g != 'columns'}
    new_ratios = dict(base_result.member_ratios)
    new_crit = dict(base_result.member_critical)
    # 기존 기둥 부재의 ratio/critical 은 새 단면으로 덮어쓸 것
    for mid in col_mids:
        new_ratios.pop(mid, None)
        new_crit.pop(mid, None)

    for (i, j) in bounds:
        f_a = floors[i]; f_b = floors[j]
        gname = (f"columns_F{f_a}_{f_b}" if f_a != f_b
                 else f"columns_F{f_a}")
        _, gd, mids = cost(i, j)
        gd.group_name = gname
        new_groups[gname] = gd
        for mid in mids:
            new_m2g[mid] = gname
            r = gd.member_results.get(mid)
            if r is not None:
                new_ratios[mid] = r.ratio_max
                new_crit[mid] = r.critical_item

    return DesignResult(
        policy=base_result.policy,
        groups=new_groups,
        member_to_group=new_m2g,
        member_ratios=new_ratios,
        member_critical=new_crit,
    )


__all__ = [
    'POLICY_1', 'POLICY_2', 'POLICY_3', 'ALL_POLICIES',
    'CAT_MODULE_COLUMN', 'CAT_MODULE_BEAM',
    'CAT_CANTILEVER_BEAM', 'CAT_FLOOR_PANEL_BEAM',
    'CAT_WALL_PANEL_COLUMN', 'CAT_WALL_PANEL_BEAM',
    'classify_member', 'group_categories', 'group_members',
    'extract_demand', 'extract_all_demands', 'extract_envelope_demands',
    'design_all_policies_envelope',
    'GroupDesign', 'DesignResult',
    'select_section_for_group',
    'design_for_policy', 'design_all_policies',
    'split_columns_by_floor',
]
