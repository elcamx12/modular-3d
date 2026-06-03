"""단면 설계 탭 — 색 기반 그룹화 + 반복 수렴 엔진 (2026-05-31, P1).

[목적]
기존 section_design.py 의 1패스 정책(1종/2종/3종) 설계와 분리된, '단면 설계 탭' 전용
엔진. 스코프×입도 옵션으로 부재를 그룹화하고, 그룹별로 지배조합(envelope) 기준 NG 안 나는
최소 단면을 고른 뒤 그 단면을 부재에 써넣고(강성 반영) **재해석 → 재선정**을 단면이 안정될
때까지 반복(진짜 수렴)한다.

[수렴이 성립하는 이유]
ops_builder._section_props 가 부재의 design_section(또는 SHS 치수)으로 강성을 계산하도록
연결되어 있어, 단면을 부재에 써넣고 solve_all_cases(prebuilt_am=am) 로 다시 풀면 힘이
재분배된다. 미설계 부재는 공칭 단면 → 단면 설계 전 결과 불변(하위호환).

[분류 = 렌더링 색]
강재는 색(role) 기준 3종으로만 모인다.
  - 기둥(column): module_column / mid_column / wall_column
  - 천장보(ceil): module_top_beam / wall_top_runner / cantilever_beam
  - 바닥보(floor): module_bottom_beam / floor_edge_beam / wall_bottom_runner /
                   cantilever_slab_beam
코어(RC)·셸은 강재 단면 설계 대상이 아니므로 제외.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .topology import AnalysisModel, AnalysisMember, build_analysis_model
from .ops_solver import solve_all_cases
from .section_catalog import SHSSection, SHS_CATALOG
from .strength_check import check_member, MemberDemand
from .section_design import (
    classify_member, extract_envelope_demands, select_section_for_group,
    CAT_RC_CORE,
)


# ── 옵션 상수 ────────────────────────────────────────────────
# 스코프 축 (공간 범위)
SCOPE_ALL = 'all'          # 전체에서 통일
SCOPE_TYPE = 'type'        # 같은 타입(크기)끼리 통일 — 실배치 무시
SCOPE_TYPE_ROOM = 'type_room'  # 같은 타입 + 실배치까지 같은 것끼리

# 입도 축 (부재 묶음, 색 기준)
GRAN_ALL = 'all'                  # 전부 한 단면
GRAN_COL_BEAM = 'col_beam'        # 기둥 / 보(천장+바닥 묶음)
GRAN_COL_CEIL_FLOOR = 'col_ceil_floor'  # 기둥 / 천장보 / 바닥보


@dataclass
class ConvergeOptions:
    """단면 설계 옵션 (2축 + 기둥 층분할)."""
    scope: str = SCOPE_ALL
    granularity: str = GRAN_ALL
    column_segments: int = 1   # 기둥 층분할 구간 수 K (1=분할 없음). [7-1] DP 최적 분할.


@dataclass
class ConvergeResult:
    """수렴 결과."""
    member_sections: Dict[int, object]          # mid → SHSSection/HSection
    member_ratios: Dict[int, float]             # mid → 응력비
    member_critical: Dict[int, str]             # mid → 지배 항목
    group_of_member: Dict[int, str]             # mid → 그룹 키
    ng_member_ids: List[int]                     # 응력비 > 1 (NG)
    iterations: int                              # 실제 반복 횟수
    converged: bool                              # True=단면 안정, False=진동(안전측 종료)
    am: Optional[AnalysisModel] = None           # 수렴에 쓴 해석 모델(타입 파생·렌더용)
    ops_results: Optional[dict] = None           # 수렴 단면 기준 케이스별 해석 결과(층간변위비 검토용)


# ── 색(role) 분류 ────────────────────────────────────────────
_COL_ROLES = {'module_column', 'mid_column', 'wall_column'}
_CEIL_ROLES = {'module_top_beam', 'wall_top_runner', 'cantilever_beam'}
_FLOOR_ROLES = {'module_bottom_beam', 'floor_edge_beam',
                'wall_bottom_runner', 'cantilever_slab_beam'}


def _member_z(am: AnalysisModel, m: AnalysisMember) -> float:
    """부재 중점 z (mm). 노드 좌표 평균."""
    try:
        z1 = float(am.nodes[m.n1].coord[2])
        z2 = float(am.nodes[m.n2].coord[2])
        return (z1 + z2) * 0.5
    except Exception:
        return 0.0


def _midbeam_class(am: AnalysisModel, m: AnalysisMember) -> str:
    """mid_beam(분할보)을 소속 컴포넌트 z 범위로 천장/바닥 판별.

    소속 컴포넌트의 보 부재 z 범위 중점보다 위면 천장보, 아래면 바닥보.
    판별 불가 시 바닥보로 폴백(보수적: 바닥보가 보통 더 가혹).
    """
    cids = m.source_comp_ids or []
    zs: List[float] = []
    for cid in cids:
        for mid2 in am.comp_to_members.get(cid, []):
            m2 = am.members.get(mid2)
            if m2 is not None and m2.kind == 'beam':
                zs.append(_member_z(am, m2))
    if not zs:
        return 'floor'
    mid_z = (min(zs) + max(zs)) * 0.5
    return 'ceil' if _member_z(am, m) >= mid_z else 'floor'


def color_class(am: AnalysisModel, m: AnalysisMember) -> Optional[str]:
    """부재 → 색 클래스 'column' | 'ceil' | 'floor' | None(제외)."""
    if classify_member(m) == CAT_RC_CORE:
        return None
    r = m.role
    if r in _COL_ROLES:
        return 'column'
    if r in _CEIL_ROLES:
        return 'ceil'
    if r in _FLOOR_ROLES:
        return 'floor'
    if r == 'mid_beam':
        return _midbeam_class(am, m)
    if r == 'mid_column':
        return 'column'
    # 폴백 — kind 기준
    if m.kind == 'column':
        return 'column'
    if m.kind == 'beam':
        return 'floor'
    return None


def _gran_class(granularity: str, cc: str) -> str:
    """입도 옵션 + 색 클래스 → 묶음 클래스."""
    if granularity == GRAN_ALL:
        return 'steel'
    if granularity == GRAN_COL_BEAM:
        return 'column' if cc == 'column' else 'beam'
    # GRAN_COL_CEIL_FLOOR — 색 클래스 그대로
    return cc


def _floor_index(am: AnalysisModel, m: AnalysisMember) -> int:
    """기둥 층분할용 — 부재 중점 z 를 층고로 나눈 인덱스(근사)."""
    from .constants import FLOOR_HEIGHT
    z = _member_z(am, m)
    try:
        return int(z // max(float(FLOOR_HEIGHT), 1.0))
    except Exception:
        return 0


def _type_label_of_members(am: AnalysisModel, scene, scope: str
                           ) -> Dict[int, str]:
    """스코프=타입 계열일 때 부재 → 타입 라벨 매핑.

    SCOPE_TYPE      : 크기 글자만(예: '모듈A') — 실배치 무시하고 통합.
    SCOPE_TYPE_ROOM : 글자-번호(예: '모듈A-1') — 실배치까지 같은 것끼리.
    classify_component_types 실패 시 컴포넌트 id 로 폴백.
    """
    label_of: Dict[int, str] = {}
    comp_label: Dict[int, str] = {}
    try:
        from modular_3d.model.type_naming import classify_component_types
        comp_label = classify_component_types(scene) or {}
    except Exception:
        comp_label = {}

    def _scope_key(cid: int) -> str:
        lab = comp_label.get(cid)
        if not lab:
            return f'comp{cid}'
        if scope == SCOPE_TYPE:
            # '모듈A-1' → '모듈A' (마지막 '-번호' 제거)
            return lab.rsplit('-', 1)[0]
        return lab

    for mid, m in am.members.items():
        cids = m.source_comp_ids or []
        cid = cids[0] if cids else -1
        label_of[mid] = _scope_key(cid)
    return label_of


# ── 그룹화 ───────────────────────────────────────────────────
def build_section_groups(am: AnalysisModel, scene, options: ConvergeOptions,
                         locked_ids: Optional[Set[int]] = None
                         ) -> Dict[str, List[int]]:
    """스코프×입도(+기둥 층분할)로 부재를 그룹화. 잠긴 부재는 제외.

    그룹 키는 카탈로그 동질성을 위해 단면형식(shs/h)까지 포함한다.
    """
    locked_ids = locked_ids or set()
    label_of: Dict[int, str] = {}
    if options.scope in (SCOPE_TYPE, SCOPE_TYPE_ROOM):
        label_of = _type_label_of_members(am, scene, options.scope)

    groups: Dict[str, List[int]] = defaultdict(list)
    for mid, m in am.members.items():
        if mid in locked_ids:
            continue
        cc = color_class(am, m)
        if cc is None:
            continue  # 코어/비강재 제외
        gran = _gran_class(options.granularity, cc)
        scope_key = '' if options.scope == SCOPE_ALL else label_of.get(mid, '?')
        sectype = 'h' if (m.kind == 'beam'
                          and getattr(m, 'section_type', 'shs') == 'h') else 'shs'
        # [7-1] 기둥 K 층분할은 여기서 미리 쪼개지 않고 _select_all 에서 DP 최적 분할.
        key = f'{scope_key}|{gran}|{sectype}|'
        groups[key].append(mid)
    return dict(groups)


# ── 단면 write-back ──────────────────────────────────────────
def _apply_section(m: AnalysisMember, sec) -> None:
    """선정 단면을 부재에 써넣음 — 강성·자중·메쉬가 따라가게.

    - design_section 설정 → _section_props 가 그 강성을 사용(SHS·H 공통).
    - SHS 면 치수(w/h/t)도 갱신 → 자중·와이어프레임이 새 단면 반영.
      (H 는 치수로 자중 근사가 부정확 — 기존 한계 유지, design_section 으로 강성만 정확.)
    """
    m.design_section = sec
    if getattr(m, 'section_type', 'shs') != 'h':
        m.section_w = float(sec.w)
        m.section_h = float(sec.h)
        m.section_t = float(sec.t)


def _current_section(m: AnalysisMember):
    """부재의 현재 배정 단면(없으면 현 치수로 SHS 단면 구성)."""
    if m.design_section is not None:
        return m.design_section
    if getattr(m, 'section_type', 'shs') != 'h':
        return SHSSection.from_dimensions(
            float(m.section_w), float(m.section_h), float(m.section_t))
    return None  # H 미설계 — 공칭(별도 비교 불필요)


def _catalog_for(group_key: str):
    """그룹 키의 단면형식에 맞는 카탈로그."""
    if group_key.split('|')[2] == 'h':
        from modular_3d.카탈로그.h_sections import H_CATALOG
        return H_CATALOG
    return SHS_CATALOG


def _dp_split_floor_ranges(am: AnalysisModel,
                           demands: Dict[int, MemberDemand],
                           col_mids: List[int], n_segments: int,
                           catalog):
    """기둥 mids 를 층(_floor_index) 기준 K 연속구간으로 DP 최적 분할. [7-1]

    비용 = 채택 단면 단위중량 × 구간 기둥 총길이 + NG 페널티. 총 강재중량 최소가 되는
    K 구간 경계를 DP[K][N] 로 찾는다(section_design.split_columns_by_floor 와 동일 원리).
    Returns: [(mids, GroupDesign), ...] — 구간 수만큼.
    """
    if n_segments <= 1 or not col_mids:
        return [(list(col_mids), select_section_for_group(col_mids, demands, catalog))]
    by_floor: Dict[int, List[int]] = defaultdict(list)
    for mid in col_mids:
        m = am.members.get(mid)
        if m is not None:
            by_floor[_floor_index(am, m)].append(mid)
    floors = sorted(by_floor)
    N = len(floors)
    K = min(int(n_segments), N)
    if K <= 1:
        return [(list(col_mids), select_section_for_group(col_mids, demands, catalog))]

    cache: Dict[tuple, tuple] = {}

    def seg(i, j):
        if (i, j) not in cache:
            mids: List[int] = []
            tlen = 0.0
            for k in range(i, j + 1):
                for mid in by_floor[floors[k]]:
                    mids.append(mid)
                    tlen += am.get_member_length(mid)
            gd = select_section_for_group(mids, demands, catalog)
            w = gd.section.weight_per_m_kg * (tlen / 1000.0) + (1e9 if gd.ng_flag else 0.0)
            cache[(i, j)] = (w, gd, mids)
        return cache[(i, j)]

    INF = float('inf')
    dp = [[INF] * N for _ in range(K)]
    back = [[-1] * N for _ in range(K)]
    for n in range(N):
        dp[0][n] = seg(0, n)[0]
    for k in range(1, K):
        for n in range(k, N):
            best = INF
            bm = -1
            for m in range(k - 1, n):
                c = dp[k - 1][m] + seg(m + 1, n)[0]
                if c < best:
                    best = c
                    bm = m
            dp[k][n] = best
            back[k][n] = bm
    bounds: List[tuple] = []
    end = N - 1
    for k in range(K - 1, -1, -1):
        start = (back[k][end] + 1) if k > 0 else 0
        bounds.append((start, end))
        end = back[k][end]
    bounds.reverse()
    out = []
    for (i, j) in bounds:
        _, gd, mids = seg(i, j)
        out.append((mids, gd))
    return out


def _select_all(am: AnalysisModel, scene, options: ConvergeOptions,
                demands: Dict[int, MemberDemand],
                locked_ids: Set[int]):
    """그룹별 단면 선정 → (mid→sec, ratios, critical, group_of, ng_ids)."""
    groups = build_section_groups(am, scene, options, locked_ids)
    assign: Dict[int, object] = {}
    ratios: Dict[int, float] = {}
    critical: Dict[int, str] = {}
    group_of: Dict[int, str] = {}
    ng: List[int] = []

    def _record(gname, mids, gd):
        for mid in mids:
            assign[mid] = gd.section
            group_of[mid] = gname
            r = gd.member_results.get(mid)
            if r is not None:
                ratios[mid] = r.ratio_max
                critical[mid] = r.critical_item
                if r.ratio_max > 1.0:
                    ng.append(mid)

    for gkey, mids in groups.items():
        catalog = _catalog_for(gkey)
        is_col = gkey.split('|')[1] == 'column'
        # [7-1] 기둥 그룹 + K>=2 → 층 기준 DP 최적 K 구간 분할.
        if is_col and int(getattr(options, 'column_segments', 1) or 1) >= 2:
            for si, (seg_mids, gd) in enumerate(
                    _dp_split_floor_ranges(am, demands, mids,
                                           options.column_segments, catalog)):
                _record(f'{gkey}#F{si}', seg_mids, gd)
        else:
            _record(gkey, mids, select_section_for_group(mids, demands, catalog))

    # 잠긴 부재 — 현재 단면 고정, demand 로 응력비만 평가(표시·NG 판정용).
    for mid in locked_ids:
        m = am.members.get(mid)
        if m is None:
            continue
        sec = _current_section(m)
        assign[mid] = sec
        group_of[mid] = 'locked'
        d = demands.get(mid)
        if sec is not None and d is not None:
            r = check_member(sec, d)
            ratios[mid] = r.ratio_max
            critical[mid] = r.critical_item
            if r.ratio_max > 1.0:
                ng.append(mid)
    return assign, ratios, critical, group_of, ng


def _sig(assign: Dict[int, object]) -> tuple:
    """배정 시그니처 — (mid, 단면명) 정렬 튜플."""
    return tuple(sorted(
        (mid, getattr(sec, 'name', str(sec))) for mid, sec in assign.items()))


def _merge_safer(a: Dict[int, object], b: Dict[int, object]
                 ) -> Dict[int, object]:
    """두 배정에서 부재별 더 큰(안전측) 단면 채택 — 진동 종료용."""
    out: Dict[int, object] = dict(a)
    for mid, sec_b in b.items():
        sec_a = a.get(mid)
        if sec_a is None:
            out[mid] = sec_b
        else:
            area_a = getattr(sec_a, 'A', 0.0)
            area_b = getattr(sec_b, 'A', 0.0)
            out[mid] = sec_b if area_b > area_a else sec_a
    return out


# ── 수렴 루프 ────────────────────────────────────────────────
def expand_locks_to_members(am: AnalysisModel,
                            locks_by_comp: Dict[int, Dict[str, object]]
                            ) -> Dict[int, object]:
    """{comp_id: {색클래스: 단면}} → {mid: 단면}.

    수동 잠금(컴포넌트의 기둥/천장보/바닥보를 특정 단면으로 고정)을 부재 단위로 전개.
    """
    out: Dict[int, object] = {}
    if not locks_by_comp:
        return out
    for cid, class_map in locks_by_comp.items():
        if not class_map:
            continue
        for mid in am.comp_to_members.get(cid, []):
            m = am.members.get(mid)
            if m is None:
                continue
            cc = color_class(am, m)
            sec = class_map.get(cc) if cc else None
            if sec is None:
                continue
            # SHS 단면을 H형강 보에 적용하면 강성(Ix) 계산이 깨짐 → H 보는 잠금 제외
            # (P4c-2 콤보는 SHS 전용). 단면형식이 맞는 부재만 잠금.
            if (getattr(m, 'kind', '') == 'beam'
                    and getattr(m, 'section_type', 'shs') == 'h'
                    and getattr(sec, 'Iz', None) is not None
                    and getattr(sec, 'Ix', None) is None):
                continue
            out[mid] = sec
    return out


def _apply_locked_sections(am: AnalysisModel,
                           locked_sections: Dict[int, object]) -> None:
    """잠금 단면을 부재에 미리 써넣어 고정(design_section + SHS 치수)."""
    for mid, sec in (locked_sections or {}).items():
        m = am.members.get(mid)
        if m is not None and sec is not None:
            _apply_section(m, sec)


def converge_sections(scene, options: ConvergeOptions,
                      locked_ids: Optional[Set[int]] = None,
                      max_iter: int = 10,
                      prebuilt_am: Optional[AnalysisModel] = None,
                      locked_sections: Optional[Dict[int, object]] = None
                      ) -> ConvergeResult:
    """단면을 안정될 때까지 반복 수렴.

    절차(매 반복):
      1) 현재 am 단면으로 solve_all_cases → 전체 하중조합.
      2) extract_envelope_demands → 지배조합 demand.
      3) 그룹별 최소 단면 선정(잠긴 부재 제외·고정).
      4) 선정이 현재 배정과 같으면 종료(수렴). 진동(과거 시그니처 재등장)이면
         안전측(더 큰 단면) 채택 후 종료.
      5) 아니면 부재에 write-back(잠긴 부재 제외) 후 1로.

    Returns: ConvergeResult.
    """
    locked_ids = set(locked_ids or set())
    am = prebuilt_am if prebuilt_am is not None else build_analysis_model(scene)
    # 수동 잠금 — 단면을 부재에 미리 써넣어 고정하고, 그 부재는 그룹화/재선정에서 제외.
    if locked_sections:
        _apply_locked_sections(am, locked_sections)
        locked_ids |= set(locked_sections.keys())

    applied: Dict[int, object] = {}
    seen: Set[tuple] = set()
    ratios: Dict[int, float] = {}
    critical: Dict[int, str] = {}
    group_of: Dict[int, str] = {}
    ng: List[int] = []
    converged = False
    it = 0

    for it in range(1, max_iter + 1):
        # [2026-06-02 성능] 첫 반복만 모델 검증(verify=True). 이후 반복은 토폴로지·
        # 등록 구조가 동일(단면 강성만 변경)하므로 검증 생략 — 빌드가 가벼워진다.
        results = solve_all_cases(scene, prebuilt_am=am, verify=(it == 1))
        demands = extract_envelope_demands(am, results)
        assign, ratios, critical, group_of, ng = _select_all(
            am, scene, options, demands, locked_ids)

        new_sig = _sig(assign)
        # 적용 대상(잠긴 부재 제외)만 비교 — 잠긴 부재는 항상 고정.
        cmp_new = {mid: s for mid, s in assign.items() if mid not in locked_ids}
        cmp_app = {mid: s for mid, s in applied.items() if mid not in locked_ids}
        if _sig(cmp_new) == _sig(cmp_app):
            converged = True
            break
        if new_sig in seen:
            # 진동 — 안전측 병합 후 종료.
            merged = _merge_safer(applied, assign)
            for mid, sec in merged.items():
                if mid in locked_ids:
                    continue
                m = am.members.get(mid)
                if m is not None and sec is not None:
                    _apply_section(m, sec)
            applied = merged
            converged = False
            break
        seen.add(new_sig)
        for mid, sec in assign.items():
            if mid in locked_ids:
                continue
            m = am.members.get(mid)
            if m is not None and sec is not None:
                _apply_section(m, sec)
        applied = assign

    return ConvergeResult(
        member_sections=applied or {},
        member_ratios=ratios,
        member_critical=critical,
        group_of_member=group_of,
        ng_member_ids=sorted(set(ng)),
        iterations=it,
        converged=converged,
        am=am,
        ops_results=results,
    )


# ── 타입 -1-1 로컬 파생 + 타입별 요약 (P4a) ──────────────────
def _type_summary(am: AnalysisModel, secs: Dict[int, object],
                  ratios: Dict[int, float], mids: List[int]):
    """컴포넌트 부재들 → (요약 텍스트, 최대 응력비, OK 여부).

    색 클래스(기둥/천장보/바닥보)별로 단면명 + 그 클래스 최대 응력비를 묶어 표시.
    """
    by_cc: Dict[str, Dict[str, object]] = {}
    max_ratio = 0.0
    for mid in mids:
        m = am.members.get(mid)
        if m is None:
            continue
        cc = color_class(am, m)
        if cc is None:
            continue
        sec = secs.get(mid)
        r = float(ratios.get(mid, 0.0) or 0.0)
        if r > max_ratio:
            max_ratio = r
        slot = by_cc.setdefault(cc, {'name': getattr(sec, 'name', '?'), 'r': 0.0})
        if r > slot['r']:
            slot['r'] = r
        if sec is not None:
            slot['name'] = getattr(sec, 'name', '?')
    name_kr = {'column': '기둥', 'ceil': '천장보', 'floor': '바닥보'}
    parts = []
    for cc in ('column', 'ceil', 'floor'):
        if cc in by_cc:
            parts.append(f"{name_kr[cc]} {by_cc[cc]['name']} ({by_cc[cc]['r']:.2f})")
    return ' / '.join(parts), max_ratio, (max_ratio <= 1.0)


def _comp_section_sig(cid: int, am: AnalysisModel, secs: Dict[int, object],
                      scene=None) -> tuple:
    """컴포넌트 cid 의 단면 시그니처 — *자체 부재 + 종속 부재* 단면 모두 포함.

    [2026-06-01] 종전엔 자체 부재(comp_to_members)만 봐서, 종속 부재(캔틸보·슬래브·
    중간보·중간기둥)의 단면이 달라도 같은 타입으로 묶였다. scene 이 주어지면
    find_dependent_comp_ids 로 직접 종속 cid 를 찾아 그 단면도 시그니처에 합친다.
    자체/종속을 구분 표기(접두어)해 "자체는 같고 종속만 다른" 경우도 분리되게 한다.
    """
    own = sorted(getattr(secs.get(mid), 'name', '?')
                 for mid in am.comp_to_members.get(cid, []) if mid in secs)
    sig = ['own:' + n for n in own]
    if scene is not None:
        dep_names = []
        for dcid in find_dependent_comp_ids(scene, cid):
            for mid in am.comp_to_members.get(dcid, []):
                if mid in secs:
                    dep_names.append(getattr(secs.get(mid), 'name', '?'))
        sig += ['dep:' + n for n in sorted(dep_names)]
    return tuple(sig)


def _derive_from_labels(am: AnalysisModel, secs: Dict[int, object],
                        ratios: Dict[int, float], labels: Dict[int, str],
                        floor_of_comp: Dict[int, int] = None, scene=None):
    """base 라벨(모듈A-1)별로 단면 배정 시그니처가 다르면 -1/-2 로 분기.

    Returns: (types: List[dict], comp_type_label: Dict[comp_id, full_label]).
    types 각 항목: {label, rep_comp_id, member_ids, summary, max_ratio, ok,
                   comp_ids, floor_counts(층→개수), count(총개수)}.
    [2026-06-01] 시그니처에 *종속 부재 단면* 포함(scene 전달 시) — 자체 부재가 같아도
    종속(캔틸 등) 단면이 다르면 다른 타입으로 분기.
    """
    floor_of_comp = floor_of_comp or {}
    by_base: Dict[str, List[int]] = defaultdict(list)
    for cid, lab in labels.items():
        by_base[lab].append(cid)

    types: List[dict] = []
    comp_type_label: Dict[int, str] = {}
    for base in sorted(by_base):
        cids = by_base[base]
        sig_of: Dict[int, tuple] = {}
        for cid in cids:
            sig_of[cid] = _comp_section_sig(cid, am, secs, scene)
        # 시그니처 → 변형 순번(등장 순, comp id 정렬로 안정).
        sig_rank: Dict[tuple, int] = {}
        order: List[tuple] = []
        for cid in sorted(cids):
            s = sig_of[cid]
            if s not in sig_rank:
                sig_rank[s] = len(sig_rank) + 1
                order.append((s, cid))
        for cid in cids:
            comp_type_label[cid] = f'{base}-{sig_rank[sig_of[cid]]}'
        for s, rep_cid in order:
            label = f'{base}-{sig_rank[s]}'
            mids = am.comp_to_members.get(rep_cid, [])
            summary, mr, ok = _type_summary(am, secs, ratios, mids)
            # 이 변형에 속한 모든 컴포넌트 + 층별 개수 집계. [7B-4a]
            comp_ids = [cid for cid in cids if sig_of[cid] == s]
            floor_counts: Dict[int, int] = defaultdict(int)
            for cid in comp_ids:
                fl = floor_of_comp.get(cid)
                if fl is not None:
                    floor_counts[fl] += 1
            types.append({
                'label': label, 'rep_comp_id': rep_cid, 'member_ids': list(mids),
                'summary': summary, 'max_ratio': mr, 'ok': ok,
                'comp_ids': comp_ids, 'floor_counts': dict(floor_counts),
                'count': len(comp_ids),
            })
    types.sort(key=lambda d: d['label'])
    return types, comp_type_label


def find_dependent_comp_ids(scene, parent_comp_id: int) -> List[int]:
    """선택한 컴포넌트(같은 층 본체)에 *직접* 종속된 컴포넌트 id 목록.

    [7-6] group_id 는 한 배치의 *전 층 복제본 + 모든 종속*을 묶으므로(multi_floor),
    group_id 로 찾으면 다른 층 캔틸/슬래브까지 끌려온다. 종속 부재는 생성 시 같은 층
    본체의 comp_id 를 parent_id 로 박으므로(multi_floor), parent_id == 선택 컴포넌트로
    매칭해야 *그 층의* 캔틸보/슬래브만 정확히 잡힌다.
    """
    comps = getattr(scene, 'components', {}) or {}
    if comps.get(parent_comp_id) is None:
        return []
    pid = int(parent_comp_id)
    out: List[int] = []
    for cid, c in comps.items():
        if cid == parent_comp_id:
            continue
        if int(getattr(c, 'parent_id', 0) or 0) == pid:
            out.append(cid)
    return out


def sections_by_color_class(result: ConvergeResult, comp_id: int
                            ) -> Dict[str, object]:
    """컴포넌트의 색 클래스(column/ceil/floor) → 수렴 배정 단면.

    같은 컴포넌트+클래스 내 부재는 (스코프×입도 그룹화상) 보통 같은 단면을 공유 →
    대표 1개를 채택. P4b 컴포넌트 3D 가 클래스별 단면 치수를 메쉬에 입히는 데 사용.
    """
    out: Dict[str, object] = {}
    am = result.am
    if am is None:
        return out
    secs = result.member_sections
    for mid in am.comp_to_members.get(comp_id, []):
        m = am.members.get(mid)
        if m is None:
            continue
        cc = color_class(am, m)
        sec = secs.get(mid)
        if cc and sec is not None:
            out[cc] = sec
    return out


def converge_result_to_design_result(result: ConvergeResult,
                                     policy_name: str = '단면설계'):
    """수렴 결과(ConvergeResult) → 물량 기계용 DesignResult (단일 정책).

    [P5 단일 출처 통합] 물량 탭은 build_quantity_report 가 DesignResult 에서
    member_to_group / groups[].section / member_ratios / member_critical / ng_groups
    만 읽는다. 수렴 확정 단면을 그 형태로 감싸 기존 물량 기계를 그대로 재사용한다
    (정책 1/2/3종 재해석 불필요 — 단면 설계 결과가 단일 출처).
    """
    from .section_design import DesignResult, GroupDesign
    by_group: Dict[str, List[int]] = defaultdict(list)
    for mid, g in result.group_of_member.items():
        by_group[g].append(mid)
    ng_set = set(result.ng_member_ids or [])
    groups: Dict[str, object] = {}
    for g, mids in by_group.items():
        sec = None
        for mid in mids:
            if mid in result.member_sections:
                sec = result.member_sections[mid]
                break
        if sec is None:
            continue   # 단면 미배정 그룹은 물량 집계에서 제외(방어).
        max_r = max((float(result.member_ratios.get(m, 0.0) or 0.0)
                     for m in mids), default=0.0)
        ng = any(m in ng_set for m in mids)
        groups[g] = GroupDesign(
            group_name=g, member_ids=list(mids), section=sec,
            max_ratio=max_r, ng_flag=ng, member_results={})
    return DesignResult(
        policy=policy_name, groups=groups,
        member_to_group=dict(result.group_of_member),
        member_ratios=dict(result.member_ratios),
        member_critical=dict(result.member_critical),
    )


def _is_indep_steel_comp(scene, cid: int) -> bool:
    """타입 목록 대상 = 강재 독립부재(본체)만. [7-5]

    코어(CORE/CORE_SLAB)·종속 부재(sub_index>0, 캔틸 등)는 제외.
    """
    comps = getattr(scene, 'components', {}) or {}
    c = comps.get(cid)
    if c is None:
        return False
    if int(getattr(c, 'sub_index', 0) or 0) != 0:
        return False
    # 바닥패널에 흡수된 벽패널(merged_fp_id)은 별도 타입이 아니라 부모 패널 구성에
    # 포함된다 → 단면설계 타입 목록에서 제외(라벨은 배치설계 표시용으로만 부여).
    if getattr(c, 'merged_fp_id', None):
        return False
    try:
        from modular_3d.model import ComponentType as _CT
        return getattr(c, 'comp_type', None) in (
            _CT.MODULE, _CT.FLOOR_PANEL, _CT.STRUCT_WALL, _CT.VERTICAL_MODULE)
    except Exception:
        return True


def derive_section_types(result: ConvergeResult, scene):
    """수렴 결과 + scene → 타입 목록(-1-1) + 컴포넌트별 타입 라벨.

    [7-5] 타입 목록에는 강재 독립부재(모듈/바닥패널/벽패널/수직모듈)만 — 코어·종속 제외.
    """
    am = result.am
    if am is None:
        return [], {}
    try:
        from modular_3d.model.type_naming import classify_component_types
        labels = classify_component_types(scene) or {}
    except Exception:
        labels = {}
    # 강재 독립부재만 남김(코어/종속 제외).
    labels = {cid: lab for cid, lab in labels.items()
              if _is_indep_steel_comp(scene, cid)}
    # 컴포넌트 → 층번호(1-based) — 타입별 층별 개수 집계용. [7B-4a]
    comps = getattr(scene, 'components', {}) or {}
    floor_of_comp = {cid: int(getattr(comps.get(cid), 'floor_index', 0) or 0) + 1
                     for cid in labels}
    return _derive_from_labels(am, result.member_sections,
                               result.member_ratios, labels, floor_of_comp,
                               scene=scene)
