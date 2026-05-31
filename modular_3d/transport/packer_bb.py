"""Phase 5 — 패널 분기한정 패커 (Branch and Bound + Cuts).

[설계 근거]
사용자 결정 — 시드/그리디 휴리스틱 폐기, 결정적 수학적 절차로 교체.
모듈은 단순 매핑 (캐시 완료). 본 모듈은 *패널만* 분기한정으로 처리.

[규칙 (사용자 확정)]
- C1: 바닥 위 L자 적층 — 바닥 면적 ≥ L자 면적 (무게 무관)
- C2: 바닥 위 바닥 — 큰 면적 아래 (면적 같으면 무게 큰 게 아래)
- C3: L자 위 적층 절대 불가
- C4: L자 옆 적재 — 벽 두께+유격 100 차감, 하드코딩 X (형상별)
- C5: L자 옆 벽패널 — LYING/STANDING 모두 가능
- C6: 벽 STANDING 시 단변=높이 자동
- C7: 바닥 위 벽 적층 — 벽 무게중심이 바닥 테두리 안쪽
- B5: 바닥/벽 패널 회전 X, 단변=폭 강제
- B6: L자 패널은 180도 회전 가능 (벽 매달리는 변 다양)
- 컴포넌트 간 갭 ≥ 100mm (3D), 같은 컴포넌트 내부 갭 0

[Phase 분할]
- Phase 5-A (본 파일 일부): 적층 호환 그래프 — compute_stack_graph
- Phase 5-B: enumerate_trip_patterns
- Phase 5-C: pack_panels_bb_recurse (DFS + ban_list + LB + memo + dominance)
- Phase 5-D: quick_greedy_panels (초기 upper_bound)
- Phase 5-E: pack_panels_bb (진입점) + pack_items 통합
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from .models import Module, Panel, SiteLimit, SpacingParams, Truck


# ════════════════════════════════════════════════════════════════════
# 디버그 트레이스 — 환경변수 BB_TRACE 가 *파일 경로* 면 그 파일에 기록.
# Phase 5-K5 — 사용자가 운송 실행 후 파일 위치만 알려주면 분석 가능.
# 출력 형식 — 숫자 위주 (사용자 가독성보단 자동 분석 친화).
# ════════════════════════════════════════════════════════════════════
_TRACE_PATH = os.environ.get("BB_TRACE", "").strip()
_TRACE_FILE = None
_TRACE_SIG_MAP: Dict[tuple, int] = {}


def _trace_enabled() -> bool:
    return bool(_TRACE_PATH)


def set_trace_path(path: str) -> None:
    """런타임에 트레이스 경로 설정 (UI 디버그 버튼용). 빈 문자열 → 비활성화."""
    global _TRACE_PATH, _TRACE_SIG_MAP
    _TRACE_PATH = (path or "").strip()
    _TRACE_SIG_MAP = {}  # 시그니처 ID 재시작


def _trace_open() -> None:
    global _TRACE_FILE
    if _TRACE_PATH and _TRACE_FILE is None:
        try:
            _TRACE_FILE = open(_TRACE_PATH, "w", encoding="utf-8")
        except Exception:
            pass


def _trace_close() -> None:
    global _TRACE_FILE
    if _TRACE_FILE is not None:
        try:
            _TRACE_FILE.flush()
            _TRACE_FILE.close()
        except Exception:
            pass
        _TRACE_FILE = None


def _trace_write(line: str) -> None:
    if _TRACE_FILE is not None:
        try:
            _TRACE_FILE.write(line + "\n")
        except Exception:
            pass


def _sig_id_for(sig: tuple) -> int:
    """시그니처 → 짧은 정수 ID (트레이스 가독성)."""
    if sig not in _TRACE_SIG_MAP:
        _TRACE_SIG_MAP[sig] = len(_TRACE_SIG_MAP)
    return _TRACE_SIG_MAP[sig]


def _pattern_signature_str(pattern: FrozenSet[int], panels: Sequence[Panel]) -> str:
    """frozenset[int] → 'S0×3|S1×2' 형태 멀티셋 문자열."""
    counts: Dict[int, int] = {}
    for i in pattern:
        sid = _sig_id_for(_panel_signature(panels[i]))
        counts[sid] = counts.get(sid, 0) + 1
    parts = sorted(counts.items(), key=lambda kv: kv[0])
    return "|".join(f"S{sid}x{cnt}" for sid, cnt in parts)


# ════════════════════════════════════════════════════════════════════
# Phase 5-A — 적층 호환 그래프
# ════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class StackGraph:
    """패널 간 *적층 가능* / *옆자리 가능* 관계 사전 계산.

    [속성]
    - can_stack[(i, j)] = True ⇔ i 위에 j 적층 가능 (i 가 아래)
    - can_side[(i, j)] = True ⇔ i 와 j 옆자리(같은 회차 FLOOR) 가능
    - dep_inner_compat[(i, j)] = True ⇔ L자 i 의 free area 안에 j 들어감
    """
    n: int
    can_stack: Dict[Tuple[int, int], bool]
    can_side: Dict[Tuple[int, int], bool]
    dep_inner_compat: Dict[Tuple[int, int], bool]


def _is_floor_panel(p: Panel) -> bool:
    """순수 바닥패널 (wall_segments 없음, kind='floor')."""
    return p.kind == "floor" and not p.wall_segments


def _is_lshape_panel(p: Panel) -> bool:
    """L자/종속 floor 패널 (wall_segments 보유 또는 kind='lshape')."""
    return bool(p.wall_segments) or p.kind == "lshape"


def _is_wall_panel(p: Panel) -> bool:
    """벽 패널 (kind='wall')."""
    return p.kind == "wall"


def _panel_area(p: Panel) -> float:
    """패널 면적 (mm²) — width × length."""
    return float(p.width) * float(p.length)


def _panel_signature(p: Panel) -> tuple:
    """패널 *형상학적 동등성* 시그니처. 같은 시그니처 = swap 동등.

    [모순 5 해결 — 2026-05-27]
    같은 종류 패널이 여러 개 있을 때 인덱스 swap 으로 인한 동등 부분집합이
    중복 enumerate 되는 결함 차단. 시그니처에는 kind, 외곽 차원, 무게,
    wall_segments 의 (side/길이/두께/높이) 정렬 튜플 포함.

    body_parts 까지 비교하면 더 정확하나 *대부분 케이스* 는 외곽+wall_segments
    로 충분히 잡힘.
    """
    seg_sigs = tuple(sorted(
        (
            int(s.side),
            round(float(s.length_mm), 1),
            round(float(s.thickness_mm), 1),
            round(float(s.height_mm), 1),
            round(float(getattr(s, "weight_kg", 0.0)), 1),
        )
        for s in (p.wall_segments or [])
    ))
    return (
        p.kind,
        round(float(p.length), 1),
        round(float(p.width), 1),
        round(float(p.thickness), 1),
        round(float(p.weight), 1),
        seg_sigs,
    )


def _can_stack_on(base: Panel, top: Panel, sp: SpacingParams) -> bool:
    """base 위에 top 적층 가능한가? (규칙 C1, C2, C3, C7 적용).

    [규칙 정리]
    - C3: base 가 L자/종속 floor 면 *적층 불가* (어떤 top 도)
    - top 이 L자 → base 가 *순수 바닥* + 바닥 면적 ≥ L자 면적
    - top 이 바닥 → base 가 *순수 바닥* + 큰 면적 아래 (같으면 무게)
    - top 이 벽 → base 가 *순수 바닥* + 벽 무게중심이 바닥 테두리 안쪽
    """
    # C3 — L자 위 적층 절대 불가
    if _is_lshape_panel(base):
        return False
    # base 는 *순수 바닥* 이어야 안전 (벽패널 위 적층은 보수적으로 X)
    if not _is_floor_panel(base):
        return False

    base_area = _panel_area(base)
    top_area = _panel_area(top)

    if _is_lshape_panel(top):
        # C1 — 바닥 위 L자: 바닥 면적 ≥ L자 면적
        return base_area >= top_area - 1e-6
    if _is_floor_panel(top):
        # C2 — 큰 면적 아래 (같으면 무게)
        if base_area > top_area + 1e-6:
            return True
        if abs(base_area - top_area) < 1e-6 and base.weight >= top.weight - 1e-6:
            return True
        return False
    if _is_wall_panel(top):
        # C7 — 벽 무게중심이 바닥 테두리 안쪽
        # 벽 무게중심 ≈ 벽 자체 중심. 단순 LYING 가정 — 벽이 *바닥 위에 눕혀짐*.
        # 보수적 — 벽이 바닥 안에 *완전히 포함* 되면 무게중심 자동 안쪽.
        # 즉 벽 length ≤ base.length 그리고 벽 width ≤ base.width.
        if (top.length <= base.length + 1e-6
                and top.width <= base.width + 1e-6):
            return True
        # 벽이 바닥보다 *조금* 큰 경우 — 무게중심 안쪽 검사
        # 무게중심 = (벽 중심 x, 벽 중심 y). 벽이 바닥에 *중앙 정렬* 가정.
        # 바닥 테두리에서 *벽 무게중심까지 거리* = 바닥 길이/2.
        # 그 거리가 *벽 무게중심에서 벽 끝* 거리 (= 벽 길이/2) 보다 크거나 같으면 안쪽.
        # 즉 base.length/2 ≥ top.length/2 ⇔ base.length ≥ top.length. 이미 위 검사.
        # 따라서 단순 포함 검사만으로 충분.
        return False
    return False


def _can_side_by_side(
    a: Panel, b: Panel, truck_max_len: float, sp: SpacingParams,
) -> bool:
    """a 와 b 가 같은 트럭 FLOOR 자리 옆자리 가능한가?

    [기본 정책]
    - 두 패널 LYING + 단변=폭 강제 → 폭 한도 검사
    - 길이 합 + 간격 ≤ 트럭 유효 길이
    - 트럭 종류 호환은 후속 단계 (here 는 *그래프* 만)
    """
    edge = sp.truck_edge_clearance_mm
    gap = sp.panel_gap_mm
    usable = truck_max_len - 2.0 * edge
    total_len = a.length + b.length + gap
    return total_len <= usable + 1e-3


def _dep_inner_free_dims(parent: Panel, sp: SpacingParams) -> Tuple[float, float]:
    """L자 패널 안쪽 free area 차원 (free_width, free_length).

    벽 segment 가 점유한 변(과 두께)을 뺀 *유격 100 포함* 안쪽 영역.
    side 0/2 (하변/상변) 의 벽 → width 차감
    side 1/3 (우변/좌변) 의 벽 → length 차감
    같은 side 다중 세그는 최대 두께만 보수적 적용.
    """
    if not parent.wall_segments:
        # L자 아닌 경우 — 전체 사용
        return float(parent.width), float(parent.length)

    max_th_by_side = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    for s in parent.wall_segments:
        if s.thickness_mm > max_th_by_side[s.side]:
            max_th_by_side[s.side] = s.thickness_mm

    # 0/2 변 (하변·상변) 벽 → width 차감
    width_taken = max_th_by_side[0] + max_th_by_side[2]
    # 1/3 변 (우변·좌변) 벽 → length 차감
    length_taken = max_th_by_side[1] + max_th_by_side[3]
    # 유격 100mm
    gap_w = sp.lshape_stack_gap_mm if width_taken > 0 else 0.0
    gap_l = sp.lshape_stack_gap_mm if length_taken > 0 else 0.0
    free_w = float(parent.width) - width_taken - gap_w
    free_l = float(parent.length) - length_taken - gap_l
    return max(0.0, free_w), max(0.0, free_l)


def _can_fit_dep_inner(parent: Panel, cand: Panel, sp: SpacingParams) -> bool:
    """L자 parent 의 안쪽 free area 에 cand 들어가는가? (C4, C5).

    cand 는 *LYING* 가정 (단변=폭). free 영역에 *단변 ≤ free_width*,
    *장변 ≤ free_length* 만 OK. STANDING 의 경우 호출자가 별도 처리.
    """
    if not _is_lshape_panel(parent):
        return False
    free_w, free_l = _dep_inner_free_dims(parent, sp)
    # cand 의 단변 = width (B5 정책), 장변 = length
    return (cand.width <= free_w + 1e-6
            and cand.length <= free_l + 1e-6)


def compute_stack_graph(
    panels: Sequence[Panel],
    trucks: Sequence[Truck],
    sp: SpacingParams,
) -> StackGraph:
    """패널 N개의 적층/옆자리/DEP_INNER 가능 관계 사전 계산.

    [복잡도] O(N²) 페어와이즈. N=30 → 900 쌍.

    Returns:
        StackGraph — can_stack / can_side / dep_inner_compat 룩업 테이블.
    """
    n = len(panels)
    can_stack: Dict[Tuple[int, int], bool] = {}
    can_side: Dict[Tuple[int, int], bool] = {}
    dep_inner_compat: Dict[Tuple[int, int], bool] = {}

    # 호환 가능 가장 긴 트럭 길이 — 옆자리 검사 기준 (최대 트럭)
    max_truck_len = max((t.max_length for t in trucks if t.active), default=0.0)

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            pi = panels[i]
            pj = panels[j]
            # 적층 — i 가 아래, j 가 위
            can_stack[(i, j)] = _can_stack_on(pi, pj, sp)
            # 옆자리 — i, j 둘 다 FLOOR (보수적 — 길이 합산만 검사)
            can_side[(i, j)] = _can_side_by_side(pi, pj, max_truck_len, sp)
            # DEP_INNER — i 가 L자 parent, j 가 안쪽에 들어감
            dep_inner_compat[(i, j)] = _can_fit_dep_inner(pi, pj, sp)

    return StackGraph(
        n=n,
        can_stack=can_stack,
        can_side=can_side,
        dep_inner_compat=dep_inner_compat,
    )


# ════════════════════════════════════════════════════════════════════
# Phase 5-B — 회차 패턴 enumeration
# ════════════════════════════════════════════════════════════════════
def _trucks_compat_with(panels_subset: List[Panel], trucks: Sequence[Truck],
                        site: SiteLimit, sp: SpacingParams) -> List[Truck]:
    """이 패널 부분집합이 *적어도 한 트럭* 에 들어가는가? 가능한 트럭 리스트.

    빠른 검사: 각 active 트럭에 대해
      ① 총 무게 ≤ 트럭 적재 한도 + 차체 GVW
      ② 가장 큰 패널의 단변 ≤ 트럭 폭 + 양쪽 돌출
      ③ 가장 큰 패널의 높이 ≤ 트럭 내공
    상세 충돌 검사는 enumerate 결과 *각 패턴* 마다 별도 수행.
    """
    if not panels_subset:
        return []
    total_w = sum(p.weight for p in panels_subset)
    max_width = max(p.width for p in panels_subset)
    max_height = max(p.thickness for p in panels_subset)  # LYING 기본
    out: List[Truck] = []
    for t in trucks:
        if not t.active:
            continue
        # 무게 한도
        eff_limit = t.max_weight
        if site.max_gvw_kg is not None:
            gvw_room = site.max_gvw_kg - (t.curb_weight_kg or 0.0)
            if gvw_room <= 0:
                continue
            eff_limit = min(eff_limit, gvw_room)
        if total_w > eff_limit + 1e-3:
            continue
        # 폭 한도 (양쪽 ±200 돌출 허용)
        eff_truck_width = t.max_width + 2.0 * sp.side_overhang_mm
        if max_width > eff_truck_width + 1e-3:
            continue
        # 높이 한도 — 단순 검사 (적층 시 더 정밀)
        inner_h = t.max_height - t.vehicle_height_offset
        if max_height > inner_h + 1e-3:
            continue
        out.append(t)
    return out


def _max_weight_capacity(trucks: Sequence[Truck], site: SiteLimit) -> float:
    """현장 + 트럭 한도 고려 *가장 큰 가용 적재 한도* (kg)."""
    best = 0.0
    for t in trucks:
        if not t.active:
            continue
        cap = t.max_weight
        if site.max_gvw_kg is not None:
            cap = min(cap, site.max_gvw_kg - (t.curb_weight_kg or 0.0))
        if cap > best:
            best = cap
    return best


def enumerate_trip_patterns(
    root_idx: int,
    available: Set[int],
    panels: Sequence[Panel],
    graph: StackGraph,
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    max_size: Optional[int] = None,
) -> List[FrozenSet[int]]:
    """root_idx 를 포함하는 회차 패턴 부분집합 enumerate.

    [정책]
    - root 가 반드시 포함된 부분집합만 (대칭 깨기 — 큰 패널 우선)
    - 각 부분집합 = 한 회차에 같이 적재될 패널 인덱스 집합
    - 무게 한도 통과 만
    - *큰 부분집합 우선* yield (회차 수 적은 분배 첫 탐색)
    - 동등 부분집합 1번만 (frozenset 키 중복 제거)

    [생성 규칙 — 보수적]
    하나의 회차 안 패널들은 *어떻게든 트럭에 같이 들어가야* — 정확 충돌은
    후속 단계. 본 함수는 *최소 조건* (무게/폭/높이 + 적층/옆자리 관계 일부)
    만 검사. 정확 충돌은 enumerate 후 *각 후보 패턴* 마다 별도 호출.

    Returns:
        List[frozenset[int]] — root 포함 패턴 후보, 큰 부분집합 우선 정렬.
    """
    if root_idx not in available:
        return []

    max_cap = _max_weight_capacity(trucks, site)
    if max_cap <= 0:
        return []

    # 최대 패턴 크기 — 안전 상한 (메모리/시간 보호)
    if max_size is None:
        # 가장 가벼운 패널 × 최대 트럭 적재 = 최대 패널 수 (사용자 직관)
        min_weight = min((panels[i].weight for i in available), default=1.0)
        if min_weight > 0:
            max_size = max(1, int(max_cap / min_weight) + 1)
        else:
            max_size = len(available)
        max_size = min(max_size, len(available))

    root_panel = panels[root_idx]
    root_w = float(root_panel.weight)
    if root_w > max_cap + 1e-3:
        return []

    # 후보 (root 와 *어떤 식으로든* 같이 갈 수 있는 패널들)
    # — graph 의 can_stack / can_side / dep_inner_compat 중 어느 하나라도 True
    others = [
        j for j in available if j != root_idx
        and (graph.can_stack.get((root_idx, j), False)
             or graph.can_stack.get((j, root_idx), False)
             or graph.can_side.get((root_idx, j), False)
             or graph.dep_inner_compat.get((root_idx, j), False)
             or graph.dep_inner_compat.get((j, root_idx), False))
    ]
    # 큰 패널 우선 (대칭 깨기 + 분기 빨리 발견). 동률 시 인덱스 작은 게 먼저.
    others.sort(key=lambda j: (-_panel_area(panels[j]), j))

    # [모순 5 — 대칭 깨기] 시그니처별 인덱스 맵
    sig_of: Dict[int, tuple] = {i: _panel_signature(panels[i]) for i in available}

    # [디버그] enumerate 진입 상태 출력 — others 길이, max_cap, max_size
    if _trace_enabled():
        sig_groups: Dict[int, int] = {}
        for j in others:
            sid = _sig_id_for(sig_of[j])
            sig_groups[sid] = sig_groups.get(sid, 0) + 1
        groups_str = ",".join(
            f"S{sid}x{cnt}" for sid, cnt in sorted(sig_groups.items())
        )
        _trace_write(
            f"ENUM_INIT root={root_idx} root_w={root_w:.0f} "
            f"max_cap={max_cap:.0f} max_size={max_size} "
            f"others_n={len(others)} others_sigs=[{groups_str}]"
        )

    # 부분집합 enumerate — DFS, 큰 거 우선 추가, 무게 한도 가지치기
    results: List[FrozenSet[int]] = []
    seen: Set[FrozenSet[int]] = set()

    def _dfs(current: List[int], current_w: float, start: int) -> None:
        # 현재까지 패턴 — root + current 의 인덱스
        pattern = frozenset([root_idx] + current)
        if pattern not in seen:
            seen.add(pattern)
            results.append(pattern)
        if len(current) + 1 >= max_size:
            return
        included = set([root_idx] + current)
        for k in range(start, len(others)):
            j = others[k]
            # [모순 5 — 대칭 깨기] 같은 시그니처 더 작은 인덱스가 *available 에
            # 있고 아직 included 에 없음* → skip. swap 동등 패턴 중복 제거.
            sig_j = sig_of[j]
            sym_block = False
            for i in available:
                if i < j and sig_of.get(i) == sig_j and i not in included:
                    sym_block = True
                    break
            if sym_block:
                continue
            new_w = current_w + float(panels[j].weight)
            if new_w > max_cap + 1e-3:
                continue  # 무게 한도 초과 — 더 큰 부분집합 X
            current.append(j)
            _dfs(current, new_w, k + 1)
            current.pop()

    _dfs([], root_w, 0)

    # 큰 부분집합 우선 정렬 (DFS 채택 후 회차 수 적은 분배 첫 탐색)
    results.sort(key=lambda s: -len(s))
    return results


# ════════════════════════════════════════════════════════════════════
# Phase 5-C — Branch and Bound 코어
# ════════════════════════════════════════════════════════════════════
import math as _math


@dataclass
class BBContext:
    """탐색 컨텍스트 — 재귀 호출 간 공유 상태."""
    panels: Sequence[Panel]
    trucks: Sequence[Truck]
    site: SiteLimit
    sp: SpacingParams
    cost_mode: str
    economics: object  # EconomicsOptions
    graph: StackGraph
    # 동적 상태
    upper_bound: float = float("inf")
    # best_solution — 각 회차 (pattern, truck, placements). placements 는 BLF 결과.
    best_solution: Optional[List[Tuple[FrozenSet[int], Truck, List]]] = None
    ban_list: Set[FrozenSet[int]] = field(default_factory=set)
    memo: Dict[FrozenSet[int], float] = field(default_factory=dict)
    # 통계
    nodes_visited: int = 0
    bans_added: int = 0
    bound_cuts: int = 0
    memo_hits: int = 0
    # 메모리 한도
    memo_max_entries: int = 800_000  # ~4GB 안전 마진


def _trip_cost_estimate(
    pattern_panels: List[Panel], trucks: Sequence[Truck],
    site: SiteLimit, sp: SpacingParams,
    cost_mode: str, economics,
) -> Tuple[float, Optional[Truck]]:
    """이 회차 패턴의 *최저 비용 호환 트럭* 비용 추정.

    가장 싼 호환 트럭 = 무게/폭/높이 통과하는 트럭 중 1회 비용 최저.
    """
    compat = _trucks_compat_with(pattern_panels, trucks, site, sp)
    if not compat:
        return float("inf"), None
    best_cost = float("inf")
    best_truck: Optional[Truck] = None
    for t in compat:
        if cost_mode == "fixed_per_trip":
            c = economics.fixed_per_trip_rate
        elif cost_mode == "freight_table":
            c = economics.fixed_rate_for_truck_type(t.truck_type)
        elif cost_mode == "per_km":
            c = economics.km_rate_for_truck(t) * economics.round_trip_km
        else:
            c = 1.0
        if c < best_cost:
            best_cost = c
            best_truck = t
    return best_cost, best_truck


def _lower_bound_remaining(
    remaining: FrozenSet[int], ctx: BBContext,
) -> float:
    """남은 패널 운반 LB (무게 기반).

    LB = ceil(남은_총_무게 / 최대_트럭_적재) × 가장_싼_트럭_단가
    """
    if not remaining:
        return 0.0
    total_w = sum(float(ctx.panels[i].weight) for i in remaining)
    max_cap = _max_weight_capacity(ctx.trucks, ctx.site)
    if max_cap <= 0:
        return float("inf")
    n_trips_min = max(1, _math.ceil(total_w / max_cap))
    # 가장 싼 트럭 단가
    cheapest_cost = float("inf")
    for t in ctx.trucks:
        if not t.active:
            continue
        if ctx.cost_mode == "fixed_per_trip":
            c = ctx.economics.fixed_per_trip_rate
        elif ctx.cost_mode == "freight_table":
            c = ctx.economics.fixed_rate_for_truck_type(t.truck_type)
        elif ctx.cost_mode == "per_km":
            c = ctx.economics.km_rate_for_truck(t) * ctx.economics.round_trip_km
        else:
            c = 1.0
        if c < cheapest_cost:
            cheapest_cost = c
    if cheapest_cost == float("inf"):
        return float("inf")
    return n_trips_min * cheapest_cost


def _trip_collision_check(
    pattern: FrozenSet[int], ctx: BBContext, truck: Truck,
) -> Optional[List["Placement"]]:
    """이 회차의 패널들이 *실제로* 트럭에 동시 적재 가능한가?

    [Phase 5-H — 2026-05-27 BLF 통합]
    종전 보수적 길이 합산 폐기. compute_layout_blf 호출 — *부재 단위
    페어와이즈 충돌 검사 + 코너 포인트 enumerate*. 통과 시 Placement 리스트
    반환 (BB→Trip 변환 시 재사용), 실패 시 None.

    Returns:
        성공 — Placement 리스트
        실패 — None (ban_list 등록 대상)
    """
    panels_in_trip = [ctx.panels[i] for i in pattern]
    return compute_layout_blf(
        panels_in_trip, truck, ctx.sp, ctx.site, ctx.graph,
    )


def _is_dominated(
    remaining: FrozenSet[int], current_cost: float, ctx: BBContext,
) -> bool:
    """Dominance — 같은 남은 패널 상태에서 *더 싼 분배* 있으면 현재 폐기."""
    if remaining in ctx.memo:
        cached_best_continuation = ctx.memo[remaining]
        # 현재 누적 + LB > 캐시된 best_continuation 이면 dominated
        # 단순화 — memo 가 *그 상태에서의 best 분배 비용* 저장
        # 같은 상태 + 더 적은 cost 면 dominate
        # 본 구현은 *memo 가 best_continuation_cost* 저장하는 형태
        # 즉 remaining 상태에서 *마무리 비용* — 그 값이 작으면 더 싸게 마무리됨
        # 현 분배가 *부분 분배 진행 중* 이라 dominated 검사 어려움
        # 대신 *후속 가지치기* 에 memo 사용 (current_cost + cached_best ≥ upper_bound)
        return current_cost + cached_best_continuation >= ctx.upper_bound - 1e-3
    return False


def pack_panels_bb_recurse(
    decided: List[Tuple[FrozenSet[int], Truck, float, List]],
    remaining: FrozenSet[int],
    ctx: BBContext,
) -> None:
    """DFS 분배 재귀.

    decided: 이미 결정된 회차들 [(pattern, truck, cost, placements), ...]
    remaining: 남은 패널 인덱스 집합
    """
    ctx.nodes_visited += 1

    accumulated_cost = sum(c for _, _, c, _ in decided)

    # ── LB 컷 (보강 3) ──
    lb_remaining = _lower_bound_remaining(remaining, ctx)
    if accumulated_cost + lb_remaining >= ctx.upper_bound - 1e-3:
        ctx.bound_cuts += 1
        return

    # ── 종료 — 모든 패널 분배 완료 ──
    if not remaining:
        if accumulated_cost < ctx.upper_bound - 1e-3:
            ctx.upper_bound = accumulated_cost
            ctx.best_solution = [(p, t, pls) for p, t, _, pls in decided]
            if _trace_enabled():
                trips_summary = "+".join(
                    f"[{_pattern_signature_str(p, ctx.panels)}/{t.name}]"
                    for p, t, _, _ in decided
                )
                _trace_write(
                    f"NEW_BEST cost={accumulated_cost:.0f} trips={trips_summary}"
                )
        return

    # ── 메모이제이션 컷 (보강 2) ──
    if remaining in ctx.memo:
        cached_best_continuation = ctx.memo[remaining]
        if accumulated_cost + cached_best_continuation >= ctx.upper_bound - 1e-3:
            ctx.memo_hits += 1
            return

    # ── 큰 패널 루트 (보강 1) ──
    # 면적 동률 시 *인덱스 작은 것* — 대칭 깨기와 일관 (enumerate_trip_patterns
    # 의 시그니처 prefix 채택과 같은 캐노니컬 순서)
    root_idx = min(remaining, key=lambda i: (-_panel_area(ctx.panels[i]), i))

    # ── 후보 회차 패턴 enumerate ──
    candidates = enumerate_trip_patterns(
        root_idx, set(remaining), ctx.panels, ctx.graph,
        ctx.trucks, ctx.site, ctx.sp,
    )
    if _trace_enabled():
        _trace_write(
            f"ENUM depth={len(decided)} root={root_idx} n_cands={len(candidates)} "
            f"pats=[" + ",".join(
                _pattern_signature_str(p, ctx.panels) for p in candidates[:50]
            ) + ("..." if len(candidates) > 50 else "") + "]"
        )

    feasible_count = 0
    saved_upper_bound = ctx.upper_bound
    for pattern in candidates:
        # ban_list 컷
        if pattern in ctx.ban_list:
            continue

        pattern_panels = [ctx.panels[i] for i in pattern]

        # 무게/폭/높이 호환 트럭 모두 추출
        compat = _trucks_compat_with(pattern_panels, ctx.trucks, ctx.site, ctx.sp)
        if not compat:
            # 어떤 트럭에도 안 들어감 → 진짜 ban
            ctx.ban_list.add(pattern)
            ctx.bans_added += 1
            if _trace_enabled():
                tot_w = sum(p.weight for p in pattern_panels)
                max_w = max(p.width for p in pattern_panels)
                max_h = max(p.thickness for p in pattern_panels)
                _trace_write(
                    f"BAN_NOCOMPAT pat={_pattern_signature_str(pattern, ctx.panels)} "
                    f"w={tot_w:.0f} maxW={max_w:.0f} maxH={max_h:.0f}"
                )
            continue

        # 비용 오름차순 정렬 — 싼 트럭부터 시도
        def _truck_base_cost(t):
            if ctx.cost_mode == "fixed_per_trip":
                return ctx.economics.fixed_per_trip_rate
            if ctx.cost_mode == "freight_table":
                return ctx.economics.fixed_rate_for_truck_type(t.truck_type)
            if ctx.cost_mode == "per_km":
                return ctx.economics.km_rate_for_truck(t) * ctx.economics.round_trip_km
            return 1.0
        compat_sorted = sorted(compat, key=_truck_base_cost)

        sig_str = _pattern_signature_str(pattern, ctx.panels) if _trace_enabled() else ""

        # 모든 호환 트럭 시도 — 통과하는 첫 트럭 채택
        # (ban_list 가 *pattern 만* 키라 *작은 트럭 BLF 실패 시 큰 트럭으로* 같은
        #  패턴 재시도 못 했던 결함 수정 — 2026-05-27)
        # 결함 추가 수정 — bound_cut 만 발동(BLF 실제 시도 0 회)한 경우는 ban X.
        # bound_cut 은 *현재 누적비용 컨텍스트에 한정* 한 가지치기일 뿐 *패턴 자체
        # 불가능* 이 아니므로 ban 하면 더 얕은 재귀에서도 영구 제외 → 큰 패턴이
        # 점차 사라져 분산되는 결함. blf_attempted 플래그로 구분.
        trip_cost: Optional[float] = None
        trip_truck: Optional[Truck] = None
        trip_placements = None
        blf_attempted = False
        for cand_truck in compat_sorted:
            cand_cost = _truck_base_cost(cand_truck)
            # Bound 컷 — 이 트럭 비용 자체가 upper_bound 넘으면 더 비싼 트럭 시도 X
            if accumulated_cost + cand_cost >= ctx.upper_bound - 1e-3:
                ctx.bound_cuts += 1
                if _trace_enabled():
                    _trace_write(
                        f"CUT depth={len(decided)} pat={sig_str} "
                        f"truck={cand_truck.name} cost={cand_cost:.0f} "
                        f"acc={accumulated_cost:.0f} ub={ctx.upper_bound:.0f}"
                    )
                break
            # BLF 시도
            blf_attempted = True
            cand_placements = compute_layout_blf(
                pattern_panels, cand_truck, ctx.sp, ctx.site, ctx.graph,
            )
            if _trace_enabled():
                ok_flag = "OK" if cand_placements is not None else "NG"
                _trace_write(
                    f"TRY depth={len(decided)} pat={sig_str} "
                    f"truck={cand_truck.name} cost={cand_cost:.0f} -> {ok_flag}"
                )
            if cand_placements is not None:
                trip_cost = cand_cost
                trip_truck = cand_truck
                trip_placements = cand_placements
                break  # 가장 싼 통과 트럭 채택

        if trip_placements is None:
            # *BLF 시도했는데* 모두 실패한 경우만 진짜 ban.
            # bound_cut 으로 break 만 한 경우는 컨텍스트 한정이므로 ban X.
            if blf_attempted:
                ctx.ban_list.add(pattern)
                ctx.bans_added += 1
                if _trace_enabled():
                    _trace_write(f"BAN pat={sig_str}")
            continue

        feasible_count += 1
        new_remaining = remaining - pattern

        # 재귀 — decided 에 placements 도 함께 누적
        decided.append((pattern, trip_truck, trip_cost, trip_placements))
        pack_panels_bb_recurse(decided, new_remaining, ctx)
        decided.pop()

    # ── Constraint Propagation (보강 8) ──
    # root 가 어느 회차에도 못 들어가는 케이스는 위에서 *후보가 비어있음* 으로 처리됨
    # 추가 처리 불요

    # ── 메모 갱신 ──
    if ctx.upper_bound < saved_upper_bound:
        # 이 상태에서 upper_bound 갱신됨 → memo 에 *최저 마무리 비용* 저장
        best_continuation = ctx.upper_bound - accumulated_cost
        if best_continuation >= 0:
            # 메모리 한도 검사
            if len(ctx.memo) < ctx.memo_max_entries:
                ctx.memo[remaining] = best_continuation


# ════════════════════════════════════════════════════════════════════
# Phase 5-D — 초기 그리디 (upper_bound 시드)
# ════════════════════════════════════════════════════════════════════
def quick_greedy_panels(
    panels: Sequence[Panel],
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    graph: StackGraph,
    cost_mode: str,
    economics,
) -> Tuple[float, List[Tuple[FrozenSet[int], Truck, List]]]:
    """빠른 그리디 — 분배 1개 + 비용 + Placement. BB 초기 upper_bound 시드.

    [2026-05-27 재작성 — BLF 기반 + 트럭 업그레이드 허용]
    기존 그리디는 *첫 패널의 가장 싼 트럭* 으로 그룹 고정 → 나머지 패널이
    그 트럭에 안 들어가면 *새 회차로 분리* → 항상 N 대 분산 분배 → BB 시드
    너무 높음. 이제 다음으로 변경:

    - 각 패널 추가 시 *기존 트럭으로 BLF 시도*. 통과하면 추가.
    - 실패면 *더 큰 호환 트럭으로 그룹 업그레이드* 시도 — *비용 증분 < 새 회차 비용*
      범위 내에서 가장 싼 트럭. BLF 통과하면 그룹의 트럭/패널 갱신.
    - 그래도 안 되면 새 회차 (idx 단독 + 가장 싼 호환 트럭).
    - 모든 단계 BLF 통과만 채택 → 그리디 결과가 *실제 가능한 분배* 보장.

    이러면 그리디 자체가 *광폭 1대 묶기* 같은 좋은 분배 도출 가능 → BB
    upper_bound 낮음 → bound_cut 강력 → 시간↓ 결과↑.
    """
    if not panels:
        return 0.0, []

    def _truck_base_cost(t: Truck) -> float:
        if cost_mode == "fixed_per_trip":
            return economics.fixed_per_trip_rate
        if cost_mode == "freight_table":
            return economics.fixed_rate_for_truck_type(t.truck_type)
        if cost_mode == "per_km":
            return economics.km_rate_for_truck(t) * economics.round_trip_km
        return 1.0

    indices = list(range(len(panels)))
    indices.sort(key=lambda i: -_panel_area(panels[i]))

    # 각 그룹 상태 — (panels set, truck, placements, cost)
    groups: List[Dict] = []

    for idx in indices:
        # 새 회차 비용 (idx 단독 가장 싼 호환 트럭) — 업그레이드 한도 기준
        single_compat = _trucks_compat_with([panels[idx]], trucks, site, sp)
        if not single_compat:
            # 어떤 트럭에도 못 들어감 — 그리디 실패 (BB 도 의미 없음)
            return float("inf"), []
        new_trip_cost = min(_truck_base_cost(t) for t in single_compat)

        added = False
        for group in groups:
            new_set = group["panels"] | {idx}
            new_panel_list = [panels[i] for i in new_set]
            new_compat = _trucks_compat_with(new_panel_list, trucks, site, sp)
            if not new_compat:
                continue

            # 1차 — 기존 그룹 트럭으로 BLF
            if group["truck"] in new_compat:
                pls = compute_layout_blf(
                    new_panel_list, group["truck"], sp, site, graph,
                )
                if pls is not None:
                    group["panels"] = new_set
                    group["placements"] = pls
                    added = True
                    break

            # 2차 — 트럭 업그레이드 (기존보다 비싼 호환 트럭 중 비용 증분 < 새회차)
            old_cost = group["cost"]
            upgrade_sorted = sorted(new_compat, key=_truck_base_cost)
            upgrade_done = False
            for cand_truck in upgrade_sorted:
                cand_cost = _truck_base_cost(cand_truck)
                if cand_cost <= old_cost + 1e-3:
                    continue  # 기존보다 안 비싼 트럭 — 이미 1차에서 처리 됨
                increment = cand_cost - old_cost
                if increment >= new_trip_cost - 1e-3:
                    break  # 업그레이드 증분 ≥ 새 회차 비용 → 이득 없음 (정렬되어 있음)
                pls = compute_layout_blf(
                    new_panel_list, cand_truck, sp, site, graph,
                )
                if pls is not None:
                    group["truck"] = cand_truck
                    group["cost"] = cand_cost
                    group["panels"] = new_set
                    group["placements"] = pls
                    upgrade_done = True
                    break
            if upgrade_done:
                added = True
                break

        if added:
            continue

        # 새 회차 — idx 단독, 가장 싼 호환 트럭
        cheapest = min(single_compat, key=_truck_base_cost)
        cheapest_cost = _truck_base_cost(cheapest)
        pls = compute_layout_blf([panels[idx]], cheapest, sp, site, graph)
        if pls is None:
            # 단독 BLF 실패 — 다른 호환 트럭 시도
            pls = None
            cheapest = None
            cheapest_cost = float("inf")
            for cand_truck in sorted(single_compat, key=_truck_base_cost):
                trial = compute_layout_blf([panels[idx]], cand_truck, sp, site, graph)
                if trial is not None:
                    pls = trial
                    cheapest = cand_truck
                    cheapest_cost = _truck_base_cost(cand_truck)
                    break
            if pls is None:
                # 모든 호환 트럭에서 단독 BLF 실패 — 운반 불가
                return float("inf"), []
        groups.append({
            "panels": {idx},
            "truck": cheapest,
            "cost": cheapest_cost,
            "placements": pls,
        })

    # 결과 변환
    total_cost = sum(g["cost"] for g in groups)
    result = [
        (frozenset(g["panels"]), g["truck"], g["placements"])
        for g in groups
    ]
    return total_cost, result


# ════════════════════════════════════════════════════════════════════
# Phase 5-G — Bottom-Left Fill (BLF) 좌표 산출
# ════════════════════════════════════════════════════════════════════
# [정책 — 사용자 결정]
# 가능 위치 = *코너 포인트만* (트럭 좌하단 + 기존 패널의 우/위/뒤 끝). 그리드
# 100mm 모든 점 안 봄. 각 코너에 *부재 단위 페어와이즈 충돌 검사* (CollisionGrid).
# 정렬 — z↑ → x↑ → y↑ (Bottom-Left First). 적층 시 부모 상단 + 갭 100mm 정확.
# L자 180도 회전 + 벽 LYING/STANDING 시도 → 완전성 회복.
def _panel_dims_in_posture(
    p: Panel, posture: "Posture",
) -> Tuple[float, float, float]:
    """패널의 (length, width, height) — 자세 반영.

    [버그 fix — 2026-05-28]
    종전 외곽 thickness 만 반환 → body_parts (보 같은 골조 부재) 가 외곽보다
    *높을 때* 실제 박스가 외곽보다 두꺼움. BLF 가 적층/내공 검사 시 *과소 평가*
    → 충돌 발생. 이제 body_parts/attached_parts 의 *실제 z 점유* 까지 반영.
    """
    from .packer_types import Posture as _Posture
    if posture == _Posture.STANDING:
        return float(p.length), float(p.thickness), float(p.width)
    # LYING — 외곽 thickness 기준 시작
    H = float(p.thickness)
    # wall_segments 있으면 점유 두께 추가
    if p.wall_segments:
        max_seg = max(s.height_mm for s in p.wall_segments)
        H = max(H, float(p.thickness) + float(max_seg))
    if p.kind == "lshape" and p.wall_height > 0:
        H = max(H, float(p.thickness) + float(p.wall_height))
    # body_parts/attached_parts 의 실제 z 점유 — 보 + 슬래브 결합 등으로
    # 외곽 thickness 보다 큰 경우가 있음 (어댑터가 자동 채움)
    for bp in (getattr(p, "body_parts", None) or []):
        z_top = float(bp.z_mm) + float(bp.height_mm)
        if z_top > H:
            H = z_top
    for ap in (getattr(p, "attached_parts", None) or []):
        z_top = float(ap.z_mm) + float(ap.height_mm)
        if z_top > H:
            H = z_top
    return float(p.length), float(p.width), H


def _postures_to_try(p: Panel) -> List["Posture"]:
    """패널의 시도 자세 후보 — 규칙 C5/C6 (벽 LYING+STANDING)."""
    from .packer_types import Posture as _Posture
    if _is_wall_panel(p) and not p.wall_segments:
        return [_Posture.LYING, _Posture.STANDING]
    return [_Posture.LYING]


def _rotations_to_try(p: Panel) -> List[bool]:
    """패널의 시도 회전 후보 — 규칙 B6 (L자 180도 회전 가능).

    Returns:
        [False] — 회전 안 함만 시도
        [False, True] — 원본 + 180도 회전 둘 다 시도 (L자/wall_segments 보유 패널)
    """
    if _is_lshape_panel(p):
        return [False, True]
    return [False]


def _truck_compatible_for_posture(
    truck: Truck, p: Panel, posture: "Posture",
) -> bool:
    """트럭이 이 패널을 이 자세로 받을 수 있는가?

    - A-frame 트럭: 단순 wall STANDING 만
    - lowbed/extendable: LYING 만
    """
    from .packer_types import Posture as _Posture
    if truck.truck_type == "aframe":
        return (posture == _Posture.STANDING
                and _is_wall_panel(p) and not p.wall_segments)
    return posture == _Posture.LYING


def compute_layout_blf(
    panels_in_trip: List[Panel],
    truck: Truck,
    sp: SpacingParams,
    site: SiteLimit,
    graph: StackGraph,
) -> Optional[List["Placement"]]:
    """패널들을 트럭에 Bottom-Left Fill 알고리즘으로 배치.

    [Phase 5-G]
    가능 위치 = 코너 포인트만. 각 코너에서 부재 단위 페어와이즈 충돌 검사
    (CollisionGrid 가속). z↑·x↑·y↑ 정렬 (Bottom-Left First).
    L자 180도 회전 + 벽 LYING/STANDING 시도.
    적층 시 부모 상단 + 갭 100mm 정확.

    Returns:
        성공 — Placement 리스트 (트럭 *적재함 중심* 원점 기준 truck_xyz).
        실패 — None (한 패널이라도 못 들어가면).
    """
    from .collision import CollisionGrid
    from .packer_safety import boxes_of_component
    from .packer_types import Placement, PlacementSlot, Posture

    if not panels_in_trip:
        return []

    # 트럭 유효 영역
    edge = float(sp.truck_edge_clearance_mm)
    side_overhang = float(sp.side_overhang_mm)
    panel_gap = float(sp.panel_gap_mm)
    usable_length = truck.max_length - 2.0 * edge
    # 트럭 적재함 *좌하단* 좌표 (트럭 좌측 끝 + edge, 적재함 폭 좌측 끝)
    # 본 함수는 *적재함 좌측 끝 = x=0 기준 trip 좌표* 사용. 마지막에 Placement 의
    # truck_xyz 는 *적재함 중심* 원점이므로 변환.
    # 폭 한도 — 양쪽 ±200 돌출 → y ∈ [-side_overhang, truck.max_width + side_overhang]
    y_min_allowed = -side_overhang
    y_max_allowed = truck.max_width + side_overhang
    # 길이 — 앞뒤 edge 여유 → x ∈ [edge, edge + usable_length]
    x_min_allowed = edge
    x_max_allowed = edge + usable_length
    # 높이 — 적재함 내공
    inner_h = truck.max_height - truck.vehicle_height_offset
    z_min_allowed = 0.0
    z_max_allowed = inner_h
    if site.max_height_mm is not None:
        # 외측 높이 한도 — z + 화물 상단 + 차량 높이 오프셋
        z_max_allowed = min(
            z_max_allowed,
            float(site.max_height_mm) - float(truck.vehicle_height_offset),
        )

    # 면적 큰 순 정렬 (대칭 깨기 + 큰 거 우선 배치)
    indexed = list(enumerate(panels_in_trip))
    indexed.sort(key=lambda kv: -_panel_area(kv[1]))

    # 코너 포인트 set — (x, y, z) 후보 자리들
    corners: Set[Tuple[float, float, float]] = {
        (x_min_allowed, y_min_allowed, z_min_allowed),
        (x_min_allowed, 0.0, z_min_allowed),  # 폭 중앙 정렬 시작점도 시도
    }

    # 그리드 인덱스 + 배치된 패널 정보
    grid = CollisionGrid()
    placements: List[Placement] = []
    # 원래 순서 보존 위해 (orig_idx, placement) 저장 후 마지막 정렬
    placements_by_orig: Dict[int, Placement] = {}

    def _check_truck_bounds(
        x: float, y: float, z: float, L: float, W: float, H: float,
    ) -> bool:
        """트럭 한도 검사 (폭 ±200, 길이 앞뒤 100, 내공 높이)."""
        if x < x_min_allowed - 1e-3 or x + L > x_max_allowed + 1e-3:
            return False
        if y < y_min_allowed - 1e-3 or y + W > y_max_allowed + 1e-3:
            return False
        if z < z_min_allowed - 1e-3 or z + H > z_max_allowed + 1e-3:
            return False
        return True

    # 충돌 결함 추적용 (마지막 충돌 박스 페어)
    last_collision_info = {"nb": None, "ob": None}

    def _check_collision(
        new_boxes: List[Tuple[float, float, float, float, float, float]],
    ) -> bool:
        """새 박스들이 기존 박스들과 갭 100mm 이상 떨어졌는가?

        같은 owner 면 갭 0. 다른 owner 와는 갭 100mm 보장 (CollisionGrid margin).
        """
        for nb in new_boxes:
            near_owners = grid.query_near(nb, margin_mm=panel_gap)
            for other in near_owners:
                for ob in grid.boxes_of(other):
                    # 갭 100mm — 두 박스 거리 ≥ 100
                    if (nb[0] < ob[3] + panel_gap and nb[3] > ob[0] - panel_gap
                            and nb[1] < ob[4] + panel_gap and nb[4] > ob[1] - panel_gap
                            and nb[2] < ob[5] + panel_gap and nb[5] > ob[2] - panel_gap):
                        last_collision_info["nb"] = nb
                        last_collision_info["ob"] = ob
                        return False
        return True

    def _check_stack_rule(
        new_panel: Panel, cx: float, cy: float, cz: float,
        L: float, W: float,
    ) -> bool:
        """z>0 적층 시 *아래 패널과의 적층 규칙* 검사.

        cz=0 (바닥) 이면 항상 True. cz>0 이면 새 패널 바닥 (cx,cy,cz) 근방의
        owner 중 *상단이 cz - panel_gap 근처* 인 패널을 찾아 _can_stack_on 검사.
        하나라도 위반이면 False.

        [버그 fix — 2026-05-28]
        종전 graph.can_stack.get((below_owner_id, new_idx)) 검사는 *원본 panels
        인덱스 체계 vs panels_in_trip 인덱스 체계 불일치* 결함이 있었음. 이제는
        owner_id 로 panels_in_trip 의 *Panel 객체* 를 가져와서 _can_stack_on
        직접 호출 — 인덱스 무관, 안전.
        """
        if cz < 1e-3:
            return True
        # 새 패널 바닥면 근방 박스 (z 범위 살짝 아래)
        probe_box = (cx, cy, max(0.0, cz - panel_gap - 1.0),
                     cx + L, cy + W, cz - 1e-3)
        below_owners = grid.query_near(probe_box, margin_mm=panel_gap)
        for below_owner_id in below_owners:
            obs = grid.boxes_of(below_owner_id)
            # below_owner 의 상단(z_max)이 cz - panel_gap 부근이면 *바로 아래* 패널
            is_directly_below = False
            for ob in obs:
                if abs(ob[5] + panel_gap - cz) < 5.0:
                    # x/y 범위 겹침 확인
                    if (ob[0] < cx + L and ob[3] > cx
                            and ob[1] < cy + W and ob[4] > cy):
                        is_directly_below = True
                        break
            if is_directly_below:
                # below_owner_id 는 *panels_in_trip 의 인덱스*. Panel 객체 가져와
                # _can_stack_on 직접 호출 (graph 우회 — 인덱스 체계 안전).
                below_panel = panels_in_trip[below_owner_id]
                if not _can_stack_on(below_panel, new_panel, sp):
                    return False
        return True

    # 실패 추적용 카운터 (트레이스 enabled 일 때만 의미 있음)
    fail_stats = {"bounds": 0, "collision": 0, "stack": 0, "corners_tried": 0}

    for orig_idx, panel in indexed:
        # 자세 후보 — LYING / STANDING (벽 패널만)
        posture_candidates = _postures_to_try(panel)
        # 트럭 호환 자세만
        posture_candidates = [
            ps for ps in posture_candidates
            if _truck_compatible_for_posture(truck, panel, ps)
        ]
        if not posture_candidates:
            if _trace_enabled():
                _trace_write(
                    f"BLF_FAIL panel_idx={orig_idx} truck={truck.name} "
                    f"reason=no_posture"
                )
            return None  # 호환 자세 없음

        # 코너 정렬 — z↑ → x↑ → y↑ (Bottom-Left First)
        sorted_corners = sorted(corners, key=lambda c: (c[2], c[0], c[1]))

        # 회전 후보 — L자 패널은 [False, True] (180도 회전 시도), 그 외 [False]
        rotation_candidates = _rotations_to_try(panel)

        placed = False
        # 패널별 fail 카운터 초기화 (이 패널이 실패 시 출력)
        local_fail = {"bounds": 0, "collision": 0, "stack": 0, "corners_tried": 0}
        for posture in posture_candidates:
            if placed:
                break
            L_p, W_p, H_p = _panel_dims_in_posture(panel, posture)
            for rot180 in rotation_candidates:
                if placed:
                    break
                for cx, cy, cz in sorted_corners:
                    local_fail["corners_tried"] += 1
                    # 트럭 한도 검사
                    if not _check_truck_bounds(cx, cy, cz, L_p, W_p, H_p):
                        local_fail["bounds"] += 1
                        continue
                    # 부재 단위 박스 산출 (rotation_180 옵션 전달)
                    new_boxes = boxes_of_component(
                        panel, posture, (cx, cy, cz), rotation_180=rot180,
                    )
                    # 충돌 검사 (그리드 인덱스 가속)
                    if not _check_collision(new_boxes):
                        local_fail["collision"] += 1
                        continue
                    # 적층 규칙 검사 (z>0 시 _can_stack_on 직접 호출)
                    if not _check_stack_rule(panel, cx, cy, cz, L_p, W_p):
                        local_fail["stack"] += 1
                        continue
                    # 통과 — 채택
                    owner_id = orig_idx
                    grid.insert(new_boxes, owner_id)
                    # Placement 생성 — truck_xyz 는 *적재함 중심* 원점, *컴포넌트 중심*
                    center_x = cx + L_p / 2.0
                    center_y = cy + W_p / 2.0
                    tx_xyz = (
                        center_x - truck.max_length / 2.0,
                        center_y - truck.max_width / 2.0,
                        cz,
                    )
                    slot = PlacementSlot.FLOOR if cz < 1e-3 else PlacementSlot.STACK
                    # parent_idx — z>0 면 *적층* 표시 (balance_trips 슬라이드 방지)
                    parent_idx_marker = None if cz < 1e-3 else 0
                    pm = Placement(
                        item=panel, slot=slot, posture=posture,
                        truck_xyz=tx_xyz, parent_idx=parent_idx_marker,
                    )
                    placements_by_orig[orig_idx] = pm
                    # 코너 set 갱신 — 채택 패널의 우측/위쪽/뒤쪽 끝 + 갭.
                    # [버그 fix — 2026-05-28]
                    # 종전 _q (round 100 단위) 양자화는 *Python banker's rounding*
                    # 때문에 z=250 → 200 같이 *내려가서 갭 0* 발생 → 충돌. 양자화
                    # 제거하고 원본 좌표 그대로 사용. 또한 *적층 z* 는 외곽 H 가
                    # 아니라 *실제 박스 최대 z* 기준으로 (부재가 외곽보다 두꺼울
                    # 수 있음 — 보 + 슬래브 결합).
                    actual_z_max = max(b[5] for b in new_boxes) - cz  # 채택 박스의 실제 점유 z 높이
                    new_right = (cx + L_p + panel_gap, cy, cz)
                    new_back = (cx, cy + W_p + panel_gap, cz)
                    new_top = (cx, cy, cz + actual_z_max + panel_gap)
                    # 모서리 결합 코너 — 폭 중앙·뒷벽 정렬·상단 코너 후보 보강
                    new_right_back = (cx + L_p + panel_gap,
                                      cy + W_p + panel_gap, cz)
                    new_right_top = (cx + L_p + panel_gap, cy,
                                     cz + actual_z_max + panel_gap)
                    new_back_top = (cx, cy + W_p + panel_gap,
                                    cz + actual_z_max + panel_gap)
                    for new_corner in (new_right, new_back, new_top,
                                       new_right_back, new_right_top, new_back_top):
                        nx, ny, nz = new_corner
                        if (x_min_allowed - 1e-3 <= nx <= x_max_allowed + 1e-3
                                and y_min_allowed - 1e-3 <= ny <= y_max_allowed + 1e-3
                                and z_min_allowed - 1e-3 <= nz <= z_max_allowed + 1e-3):
                            corners.add(new_corner)
                    corners.discard((cx, cy, cz))
                    # 채택 패널이 *덮어버린 코너* 제거 — 시간 절감 (#6)
                    panel_min = (cx, cy, cz)
                    panel_max = (cx + L_p, cy + W_p, cz + H_p)
                    corners = {
                        c for c in corners
                        if not (panel_min[0] - 1e-3 <= c[0] < panel_max[0] - 1e-3
                                and panel_min[1] - 1e-3 <= c[1] < panel_max[1] - 1e-3
                                and panel_min[2] - 1e-3 <= c[2] < panel_max[2] - 1e-3)
                    }
                    placed = True
                    break

        if not placed:
            if _trace_enabled():
                nb_str = ""
                ob_str = ""
                if last_collision_info["nb"] is not None:
                    nb = last_collision_info["nb"]
                    ob = last_collision_info["ob"]
                    nb_str = (f" nb=[{nb[0]:.0f},{nb[1]:.0f},{nb[2]:.0f}~"
                              f"{nb[3]:.0f},{nb[4]:.0f},{nb[5]:.0f}]")
                    ob_str = (f" ob=[{ob[0]:.0f},{ob[1]:.0f},{ob[2]:.0f}~"
                              f"{ob[3]:.0f},{ob[4]:.0f},{ob[5]:.0f}]")
                _trace_write(
                    f"BLF_FAIL panel_idx={orig_idx} truck={truck.name} "
                    f"L={int(_panel_dims_in_posture(panel, posture_candidates[0])[0])} "
                    f"W={int(_panel_dims_in_posture(panel, posture_candidates[0])[1])} "
                    f"corners={local_fail['corners_tried']} "
                    f"bounds_nf={local_fail['bounds']} "
                    f"coll_nf={local_fail['collision']} "
                    f"stack_nf={local_fail['stack']} "
                    f"already_placed={len(placements_by_orig)}"
                    f"{nb_str}{ob_str}"
                )
            return None  # 이 패널 어느 자세·회전·코너에도 못 들어감

    # 원래 순서로 Placement 반환
    return [placements_by_orig[i] for i in range(len(panels_in_trip))
            if i in placements_by_orig]


# ════════════════════════════════════════════════════════════════════
# Phase 5-E — 진입점
# ════════════════════════════════════════════════════════════════════
def pack_panels_bb(
    panels: Sequence[Panel],
    trucks: Sequence[Truck],
    site: SiteLimit,
    sp: SpacingParams,
    cost_mode: str,
    economics,
) -> Tuple[List[Tuple[FrozenSet[int], Truck, List]], float, Dict]:
    """패널만 분기한정으로 패킹. 모듈은 외부 (pack_items) 가 단순 매핑.

    Returns:
        (회차 리스트, 총 비용, 통계 dict)
        회차 = (패널 인덱스 집합, 선택된 트럭, BLF Placement 리스트)
    """
    if not panels:
        return [], 0.0, {"nodes": 0, "bans": 0, "bound_cuts": 0, "memo_hits": 0}

    # 적층 호환 그래프 사전 계산
    graph = compute_stack_graph(panels, trucks, sp)

    # 초기 그리디 — upper_bound 시드
    initial_cost, initial_solution = quick_greedy_panels(
        panels, trucks, site, sp, graph, cost_mode, economics,
    )

    # 트레이스 시작
    if _trace_enabled():
        _trace_open()
        _trace_write(f"BB_START n_panels={len(panels)} initial_cost={initial_cost:.0f}")
        for ti, (pat, tr, _) in enumerate(initial_solution or []):
            _trace_write(
                f"INIT_TRIP {ti} pat={_pattern_signature_str(pat, panels)} "
                f"truck={tr.name}"
            )
        # 시그니처별 실제 패널 속성 dump — kind/dims/weight/wall_seg 보유 여부
        sig_to_panel: Dict[int, Panel] = {}
        for p in panels:
            sid = _sig_id_for(_panel_signature(p))
            if sid not in sig_to_panel:
                sig_to_panel[sid] = p
        for sid in sorted(sig_to_panel.keys()):
            p = sig_to_panel[sid]
            n_segs = len(p.wall_segments or [])
            n_body = len(getattr(p, "body_parts", None) or [])
            n_attached = len(getattr(p, "attached_parts", None) or [])
            _trace_write(
                f"SIG_DUMP S{sid} kind={p.kind} "
                f"L={p.length:.0f} W={p.width:.0f} T={p.thickness:.0f} "
                f"wt={p.weight:.0f} wall_segs={n_segs} "
                f"body_parts={n_body} attached_parts={n_attached} "
                f"is_lshape={_is_lshape_panel(p)} is_floor={_is_floor_panel(p)}"
            )
            # boxes_of_component 가 실제 반환하는 박스 차원도 dump
            try:
                from .packer_safety import boxes_of_component as _box_fn
                from .packer_types import Posture as _Posture
                _boxes = _box_fn(p, _Posture.LYING, (0.0, 0.0, 0.0), rotation_180=False)
                for bi, b in enumerate(_boxes):
                    _trace_write(
                        f"  BOX S{sid} #{bi} "
                        f"x=[{b[0]:.0f},{b[3]:.0f}] "
                        f"y=[{b[1]:.0f},{b[4]:.0f}] "
                        f"z=[{b[2]:.0f},{b[5]:.0f}]"
                    )
            except Exception as _e:
                _trace_write(f"  BOX S{sid} ERROR {type(_e).__name__}")

    if initial_cost == float("inf") or not initial_solution:
        # 그리디 실패 — 어떤 패널도 운반 불가능. BB 도 의미 없음.
        return [], 0.0, {
            "nodes": 0, "bans": 0, "bound_cuts": 0, "memo_hits": 0,
            "initial_failed": True,
        }

    # BB 컨텍스트
    ctx = BBContext(
        panels=panels,
        trucks=trucks,
        site=site,
        sp=sp,
        cost_mode=cost_mode,
        economics=economics,
        graph=graph,
        upper_bound=initial_cost,
        best_solution=initial_solution,
    )

    # 분기한정 시작
    remaining = frozenset(range(len(panels)))
    pack_panels_bb_recurse([], remaining, ctx)

    stats = {
        "nodes": ctx.nodes_visited,
        "bans": ctx.bans_added,
        "bound_cuts": ctx.bound_cuts,
        "memo_hits": ctx.memo_hits,
        "initial_cost": initial_cost,
        "final_cost": ctx.upper_bound,
        "memo_size": len(ctx.memo),
    }
    if _trace_enabled():
        _trace_write(
            f"BB_END nodes={ctx.nodes_visited} bans={ctx.bans_added} "
            f"bound_cuts={ctx.bound_cuts} memo_hits={ctx.memo_hits} "
            f"initial={initial_cost:.0f} final={ctx.upper_bound:.0f}"
        )
        if ctx.best_solution:
            for ti, (pat, tr, _) in enumerate(ctx.best_solution):
                _trace_write(
                    f"FINAL_TRIP {ti} pat={_pattern_signature_str(pat, panels)} "
                    f"truck={tr.name}"
                )
        # 파일 close 는 _pack_items_bb 의 balance_trips 후로 미룸 (좌표 비교 트레이스용)
    return ctx.best_solution or [], ctx.upper_bound, stats


__all__ = [
    "StackGraph",
    "BBContext",
    "compute_stack_graph",
    "enumerate_trip_patterns",
    "pack_panels_bb_recurse",
    "quick_greedy_panels",
    "pack_panels_bb",
    "_trip_cost_estimate",
    "_lower_bound_remaining",
    "_trucks_compat_with",
    # 헬퍼
    "_dep_inner_free_dims",
    "_can_fit_dep_inner",
    "_is_floor_panel",
    "_is_lshape_panel",
    "_is_wall_panel",
    "_panel_area",
    "_max_weight_capacity",
]
