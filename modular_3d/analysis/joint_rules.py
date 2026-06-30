"""케이스별 명시 접합 규칙 (2026-05-13 신설).

[정책]
이전의 자동 휴리스틱(`_step_apply_interface_links` + `_apply_panel_column_supports`
등)을 폐기하고, 사용자가 의도한 결합 케이스만 명시적으로 추가하는 방식. 각 룰은:

  1) 독립적인 함수 한 개
  2) 고유한 rule_id 문자열 (spec/viewer 가 색 매핑에 사용)
  3) `om: OpsModel` 만 받고 OpenSees ops + spec 양쪽에 기록

으로 작성. build_ops_model 의 결합 단계 자리에서 한 줄씩 호출.

[현재 등록된 룰]
- R01_mod_mod_h : 모듈 ↔ 모듈 수평 인접 접합
  - 꼭지점-꼭지점 (점-점)
  - 꼭지점-모서리 (점-수평 보 직선 사영)
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple, Optional

import numpy as np
import openseespy.opensees as ops

from modular_3d.카탈로그.geometry import (
    CORE_WALL_DEFAULT_THICKNESS_MM,
    SECTION_W_MM,
    CORE_JOINT_GAP_MM,
    SPLIT_NODE_BASE_OFFSET,
    RULE_NODE_OFFSET_R03,
    RULE_NODE_OFFSET_R09,
)
from modular_3d.카탈로그.tolerances import (
    USER_ADD_SNAP_TOL_MM,
    USER_ADD_LINE_TOL_MM,
    USER_ADD_MIN_SEGMENT_MM,
)
from modular_3d._utils.debug import dprint


# ── 공용 상수 ─────────────────────────────────────────────────

# z 차이 임계 (mm) — 같은 층 판정
_Z_TOL = 5.0
# 평면 한 직각축 정렬 임계 (mm) — 대각선 페어 거부
_PERP_TOL = 5.0
# 다른 직각축 차이의 상한 (mm). 모듈 기둥 자연 갭 = 200(기둥 폭 200/2 × 2)
# + 20(설계 갭) + 5(부동소수 오차 마진) = 225. 이 거리 안쪽의 페어만 "맞닿는"
# 모듈로 인정. 같은 직선상이라도 멀리 떨어져 있으면 매칭 안 함.
_GAP_MAX = 225.0
# (b) 꼭지점-모서리 전용 직각 거리 임계 (mm). 코너(기둥 중심선) ↔ 인접 모듈
# 보 모서리(중심선) 자연 거리 ≈ 220mm. 임계가 모듈 폭보다 작으므로 마주보는
# 먼 변은 자동 배제 → 한 모듈당 인접 변 하나만 자연 통과. "가장 가까운 하나"
# 같은 상대 선택 불필요. 값 조정은 본 상수 한 줄.
_EDGE_PERP_MAX = _GAP_MAX
# R02 수직 적층 갭 상한 (mm). 아래 모듈 천장 코너 ↔ 위 모듈 바닥 코너 z 차이
# ≈ 220mm (모듈 천장 z=3200, 윗 모듈 바닥 z=3420). 5mm 부동소수 마진 포함 225.
_VSTACK_MAX = 225.0


# ── 공간 격자(해시) — 코너 짝짓기 후보 축소 (2026-06-05) ────────
# R01·R02·R03 의 코너 매칭은 원래 모든 컴포넌트 쌍을 전수 비교(O(N²))했다.
# 두 코너가 결합 후보가 되려면 평면 거리가 매칭 임계(_GAP_MAX=225) 이내여야
# 하므로, 바닥을 _GRID_CELL 간격 격자로 나눠 코너를 칸에 담아두고 "자기 칸 +
# 8 이웃 칸(3×3)" 만 보면 후보가 빠짐없이 포함된다(셀 크기 ≥ 임계라 |dx|·|dy|≤225
# 인 쌍은 칸 차이가 최대 1 → 3×3 안). 후보만 줄일 뿐 순회 순서·판정·등록은
# 기존과 동일하게 유지하므로 결과는 100% 보존된다(회귀 0).
_GRID_CELL = _GAP_MAX   # 225.0 — 매칭 임계와 동일. 줄이면 누락 위험.


def _cell_of(coord) -> Tuple[int, int]:
    """평면 좌표 → 격자 칸 인덱스 (음수 좌표는 floor 나눗셈으로 안전)."""
    return (int(coord[0] // _GRID_CELL), int(coord[1] // _GRID_CELL))


def _cell_of3(coord) -> Tuple[int, int, int]:
    """xyz 좌표 → 3D 격자 칸. 코너 매칭은 z 정렬(≤_Z_TOL)만 결합하므로 z 도
    칸으로 나눠 같은 층 후보만 보게 한다(다층에서 같은 xy 칸에 모든 층 코너가
    몰려 O(N×층) 으로 폭증하던 것을 막음). 층 간격(3420) ≫ 셀(225)."""
    return (int(coord[0] // _GRID_CELL), int(coord[1] // _GRID_CELL),
            int(coord[2] // _GRID_CELL))


def _build_corner_grid(om, corners: Dict[int, List[int]]
                       ) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
    """{cid: [nid]} → {(ix, iy): [(cid, nid), ...]}. 코너의 xy 격자 버킷.

    [주의] 2D(z 무시) — _apply_vstack_rule 처럼 *위아래 층*(z 다름) 코너를
    수직 결합하는 룰이 이 격자를 직접 순회하므로 z 를 칸으로 나누면 안 된다.
    z 정렬(같은 층)만 보는 코너-엣지 룰은 _build_corner_grid_z(3D)를 쓴다.
    """
    grid: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for cid, nids in corners.items():
        for nid in nids:
            c = om.node_tags.get(nid)
            if c is None:
                continue
            grid.setdefault(_cell_of(c), []).append((cid, nid))
    return grid


def _build_corner_grid_z(om, corners: Dict[int, List[int]]
                         ) -> Dict[Tuple[int, int, int], List[Tuple[int, int]]]:
    """{cid: [nid]} → {(ix, iy, iz): [(cid, nid), ...]}. 코너의 xyz 격자 버킷.
    z 정렬(≤_Z_TOL)만 결합하는 룰(코너-엣지) 전용 — 같은 층 후보만 본다."""
    grid: Dict[Tuple[int, int, int], List[Tuple[int, int]]] = {}
    for cid, nids in corners.items():
        for nid in nids:
            c = om.node_tags.get(nid)
            if c is None:
                continue
            grid.setdefault(_cell_of3(c), []).append((cid, nid))
    return grid


def _neighbor_cids(grid: Dict[Tuple[int, int], List[Tuple[int, int]]],
                   coord, self_cid: int) -> Set[int]:
    """coord 주변 3×3 칸에 있는, self_cid 가 아닌 컴포넌트 ID 집합.

    이 집합에 든 cid 만 매칭 후보로 보면 된다 — 멀리 떨어진 컴포넌트는
    애초에 후보에서 빠진다. 결과는 전수 비교와 동일(임계 이내는 모두 포함).
    """
    ix, iy = _cell_of(coord)
    out: Set[int] = set()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for cid, _nid in grid.get((ix + dx, iy + dy), ()):
                if cid != self_cid:
                    out.add(cid)
    return out


def _neighbor_cids_z(grid: Dict[Tuple[int, int, int], List[Tuple[int, int]]],
                     coord, self_cid: int) -> Set[int]:
    """coord 주변 3×3×3 칸의, self_cid 가 아닌 컴포넌트 ID 집합(z 분리).

    _build_corner_grid_z 와 쌍 — z 정렬(같은 층)만 보는 코너-엣지 룰 전용.
    같은 xy 다른 층 코너가 한 칸에 몰리지 않아 다층에서 O(N) 으로 동작한다.
    """
    ix, iy, iz = _cell_of3(coord)
    out: Set[int] = set()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for cid, _nid in grid.get((ix + dx, iy + dy, iz + dz), ()):
                    if cid != self_cid:
                        out.add(cid)
    return out


def _build_edge_grid(om, edges: Dict[int, List[Tuple[int, int, int]]]
                     ) -> Dict[Tuple[int, int], Set[int]]:
    """{cid: [(mid, nA, nB)]} → {(ix, iy): {cid, ...}}. 보(모서리)의 격자 등록.

    [정책 2026-06-05 — 보 rasterize]
    보는 점이 아니라 선이라 여러 칸에 걸친다(모듈 보는 최대 30칸). 코너가 보에
    직각으로 닿을(perp ≤ 225) 가능성이 있는 칸을 빠짐없이 덮으려면, 보의 양 끝점
    bbox 를 칸 단위로 1칸씩 넓혀(직각 ±1칸 = 225mm 여유) 그 안의 모든 칸에 cid 를
    등록한다. 모듈 보가 100% 축정렬이라 bbox 가 한 축으로 얇아 등록 칸이 적다.
    값은 코너 후보 판정용이라 cid 집합만 저장(보 식별자는 호출자가 edges 로 순회).
    """
    grid: Dict[Tuple[int, int], Set[int]] = {}
    for cid, elist in edges.items():
        for _mid, nA, nB in elist:
            cA = om.node_tags.get(nA)
            cB = om.node_tags.get(nB)
            if cA is None or cB is None:
                continue
            ixA, iyA = _cell_of(cA)
            ixB, iyB = _cell_of(cB)
            ix0, ix1 = (ixA, ixB) if ixA <= ixB else (ixB, ixA)
            iy0, iy1 = (iyA, iyB) if iyA <= iyB else (iyB, iyA)
            for ix in range(ix0 - 1, ix1 + 2):
                for iy in range(iy0 - 1, iy1 + 2):
                    grid.setdefault((ix, iy), set()).add(cid)
    return grid


def _edge_cell_cids(edge_grid: Dict[Tuple[int, int], Set[int]],
                    coord) -> Set[int]:
    """coord 가 든 칸에 등록된 보의 컴포넌트 ID 집합 — (b) 후보 cid.

    보가 자기 지나는 칸 전체 + 직각 ±1칸에 등록돼 있으므로, coord 가 그 보에
    225mm 이내로 닿으면 반드시 같은 칸에서 만난다(후보 누락 0 = 결과 보존).
    """
    return edge_grid.get(_cell_of(coord), _EMPTY_CID_SET)


_EMPTY_CID_SET: Set[int] = set()


# ── 노드 식별 헬퍼 ─────────────────────────────────────────────

def _collect_module_nodes(om) -> Dict[int, List[int]]:
    """모듈 컴포넌트(MODULE, sub_index=0) 별 노드 ID 집합 반환.

    종속 캔틸 노드는 토폴로지의 _consolidate_dependent_nodes 가 이미 부모 모듈
    노드와 같은 nid 로 통합했으므로, 부모 모듈 nid 집합에 자동 포함됨.

    Returns: {comp_id: [node_id, ...]}
    """
    am = om.analysis_model
    if am is None or not getattr(am, 'comp_to_nodes', None):
        return {}
    # MODULE role 부재가 등록된 컴포넌트만 추림 — role 인덱스 사용.
    module_cids: Set[int] = set()
    for mid in am.members_by_role_prefix('module_'):
        m = am.members[mid]
        if m.source_comp_ids:
            module_cids.add(m.source_comp_ids[0])
    out: Dict[int, List[int]] = {}
    for cid in module_cids:
        nids = am.comp_to_nodes.get(cid, [])
        if nids:
            out[cid] = list(nids)
    return out


def _module_corners(om, nids: List[int]) -> List[int]:
    """모듈의 꼭지점 노드 ID 추출 — z 레벨별 4 코너.

    [정책 2026-05-14 수직모듈 대응]
    단층 모듈은 z 레벨 2개 → 8 코너. 수직모듈(Vertical3Module) 은 z 레벨 4개
    → 16 코너. bbox 의 (x_min/x_max) × (y_min/y_max) 4 조합을 **각 z 레벨마다**
    추출. 수직모듈의 기둥 분절 경계 노드(중간 z 레벨) 도 코너로 포함되어,
    그 높이에서 다른 모듈과 (a)/(b) 매칭이 가능해진다.
    """
    nid_list = [n for n in nids if n in om.node_tags]
    if len(nid_list) < 8:
        return []
    coords = np.array([om.node_tags[n] for n in nid_list])
    xs = coords[:, 0]; ys = coords[:, 1]; zs = coords[:, 2]
    xmin, xmax = xs.min(), xs.max()
    ymin, ymax = ys.min(), ys.max()
    # z 레벨 클러스터링 — 5mm 이내는 같은 레벨.
    z_levels: List[float] = []
    for z in sorted(set(round(float(v), 1) for v in zs)):
        if not z_levels or abs(z - z_levels[-1]) > _Z_TOL:
            z_levels.append(z)
    chosen: List[int] = []
    for zlv in z_levels:
        for tx, ty in ((xmin, ymin), (xmax, ymin),
                       (xmin, ymax), (xmax, ymax)):
            best_nid = None
            best_d = float('inf')
            for nid in nid_list:
                c = om.node_tags[nid]
                if abs(c[2] - zlv) > _Z_TOL:
                    continue
                d = (c[0] - tx) ** 2 + (c[1] - ty) ** 2
                if d < best_d:
                    best_d = d
                    best_nid = nid
            if best_nid is not None and best_nid not in chosen:
                chosen.append(best_nid)
    return chosen


def _horizontal_edges(om, corners: List[int], role_ok,
                      allowed_nodes: Optional[Set[int]] = None
                      ) -> List[Tuple[int, int, int]]:
    """수평 보 member → [(mid, nA, nB), ...].

    AnalysisModel.members 중 role_ok(role) 이 참이고 kind='beam' 이며 두 끝점이
    모두 "허용 노드" 에 속하고 같은 z(수평) 인 부재. member id 를 함께 반환해야
    (b) 케이스에서 그 보 element 를 분할할 수 있다.

    role_ok: callable(role:str)->bool. 모듈은 r.startswith('module'),
             패널은 r == 'floor_edge_beam'.

    allowed_nodes: 허용 노드 집합. None 이면 corners 집합 그대로 사용(패널 등
        한 부재의 두 끝이 항상 코너인 경우). 모듈처럼 중간 부재가 박혀 host
        보가 sub-부재로 쪼개진 경우, corners 외에 sub-부재의 mid 끝점(코너
        아님) 도 같은 cid 의 노드 집합에 포함되어야 sub-부재가 모서리로 잡힌다.
        호출자가 cid 의 전체 노드 집합을 넘기면 됨.
    """
    am = om.analysis_model
    if am is None:
        return []
    chk_set = set(allowed_nodes) if allowed_nodes is not None else set(corners)
    out: List[Tuple[int, int, int]] = []
    for mid, m in am.members.items():
        if m.kind != 'beam' or not role_ok(m.role or ''):
            continue
        if m.n1 in chk_set and m.n2 in chk_set:
            z1 = om.node_tags.get(m.n1)
            z2 = om.node_tags.get(m.n2)
            if z1 is None or z2 is None:
                continue
            if abs(z1[2] - z2[2]) <= _Z_TOL:
                out.append((mid, m.n1, m.n2))
    return out


def _module_vertical_edges(om, corners: List[int],
                           allowed_nodes: Optional[Set[int]] = None
                           ) -> List[Tuple[int, int, int]]:
    """수직모듈의 기둥 분절 member → [(mid, nA, nB), ...].

    [정책 2026-05-14]
    수직모듈(z 레벨 > 2) 의 기둥(role='module_column', kind='column') 분절만
    반환. 단층 모듈(z 레벨 2개) 은 기둥이 한 층 높이라 다른 모듈 코너가 중간에
    닿을 일이 없으므로 빈 리스트 — (b) 수직 매칭 대상에서 제외.

    수직모듈 기둥은 3 분절(level 0→1→2→3) 로 등록돼 있어 각 분절이 코너-코너.
    일반 모듈의 천장 코너가 어느 분절 중간에 사영되면 그 분절을 분할.

    allowed_nodes: 허용 노드 집합. None 이면 corners 집합 그대로. 중간기둥이
        수직모듈 기둥에 박혀 host 분절이 sub-분절로 쪼개진 경우, 호출자가 cid
        전체 노드 집합을 넘기면 sub-분절(끝점 한쪽이 코너 아닌 mid 끝점) 도
        모서리로 잡힘.
    """
    am = om.analysis_model
    if am is None:
        return []
    # z 레벨 수로 수직모듈 판정 — 2 이하면 단층 → 대상 아님.
    zs = set(round(om.node_tags[n][2], 1) for n in corners if n in om.node_tags)
    if len(zs) <= 2:
        return []
    chk_set = set(allowed_nodes) if allowed_nodes is not None else set(corners)
    out: List[Tuple[int, int, int]] = []
    # role 인덱스로 module_ 시작 부재만 추림 (전체 순회 회피).
    for mid in am.members_by_role_prefix('module_'):
        m = am.members[mid]
        if m.kind != 'column':
            continue
        if m.n1 in chk_set and m.n2 in chk_set:
            c1 = om.node_tags.get(m.n1)
            c2 = om.node_tags.get(m.n2)
            if c1 is None or c2 is None:
                continue
            # 수직 분절 — z 차이가 있어야 기둥.
            if abs(c1[2] - c2[2]) > _Z_TOL:
                out.append((mid, m.n1, m.n2))
    return out


# ── 결합 등록 헬퍼 ─────────────────────────────────────────────

# ── 접합부 오버라이드 게이트 (2026-05-25) ─────────────────────
# 사용자가 변경/제거한 컴포넌트 간 접합을 equalDOF 등록 직전에 반영하는 관문.
# 모든 룰(R01~R09)의 등록 지점이 이 게이트를 거친다. om.joint_overrides 가
# 비어 있으면(평상시) 즉시 원래 dofs 를 반환 → 기존 동작과 100% 동일(회귀 0).


def _node_xy(om, tag):
    """노드의 평면 좌표(x, y). om.node_tags(tag→coord)에서 직접 조회.
    사영점·split 노드도 등록과 동시에 node_tags 에 들어가므로 모두 포함."""
    c = om.node_tags.get(tag)
    if c is None:
        return None
    return (float(c[0]), float(c[1]))


def _resolve_override_dofs(om, master, slave, dofs, rule_id=''):
    """접합 오버라이드를 반영한 최종 자유도 묶음.

    오버라이드가 없거나(평상시) 매칭 안 되면 원래 dofs 를 그대로 반환.
    매칭되면:
      remove → None (호출자가 이 결합 등록을 건너뜀)
      rigid  → (1,2,3,4,5,6)
      pin    → (1,2,3)
    다층 자동 적용: 같은 평면 위치(xy)면 z(층) 무관하게 매칭되므로 한 오버라이드가
    모든 층 접합에 적용된다.
    rule_id: 등록 중인 결합의 종류. 오버라이드에 rule_id 가 지정돼 있으면 같은
    종류만 매칭(같은 위치 겹친 다른 종류 수직 접합 구분).
    """
    overrides = getattr(om, 'joint_overrides', None)
    if not overrides:
        return tuple(dofs)
    ma = _node_xy(om, master)
    mb = _node_xy(om, slave)
    if ma is None or mb is None:
        return tuple(dofs)
    cma = om.node_tags.get(master)
    cmb = om.node_tags.get(slave)
    maz = float(cma[2]) if cma is not None else None
    mbz = float(cmb[2]) if cmb is not None else None
    from modular_3d.model.joint_override import match_override
    ov = match_override(overrides, ma, mb, rule_id=rule_id,
                        ma_z=maz, mb_z=mbz)
    if ov is None:
        return tuple(dofs)
    return ov.effective_dofs()   # remove → None


def _register_pin_link(om, master: int, slave: int,
                       rule_id: str, kind: str,
                       dofs: Tuple[int, ...] = (1, 2, 3),
                       masters_seen: Optional[Set[int]] = None) -> bool:
    """equalDOF 핀 결합 등록 (OpenSees + spec 동시).

    Returns: True 등록 성공, False 충돌로 스킵.

    [정책 2026-05-14 노드 역할 — 체인 허용]
    세 모듈 접점(A-B-C) 에서 (a) 에서 slave 가 된 코너가 (b) 에서 master 로
    쓰여야 세 결합이 모두 성립한다 (예: B→A 종속 + Qb→B 종속). 순환만 아니면
    OpenSees Penalty 가 체인 구속을 처리하므로 허용한다. 금지하는 것은:
      - 한 노드를 두 번 slave (같은 DOF 다중 constrained) → Penalty 충돌
      - master 인 노드를 다시 slave (순환 위험)
    허용하는 것:
      - 한 노드 master 다회 (여러 모듈과 결합)
      - slave 였던 노드를 master 로 (체인 — 순환 아님)

    추적:
      - om.constrained_node_ids : slave 로 쓰인 노드 집합
      - masters_seen            : master 로 쓰인 노드 집합 (호출자가 룰 단위로
                                  관리. None 이면 임시 집합 — 추적 안 됨)
    """
    if masters_seen is None:
        masters_seen = set()
    # slave 후보가 이미 slave 였으면 거부 — 한 노드 다중 종속은 Penalty 충돌.
    if slave in om.constrained_node_ids:
        return False
    # slave 후보가 master 였으면 거부 — master 노드를 종속시키면 순환 위험.
    if slave in masters_seen:
        return False
    # master 후보는 제약 없음 — 이미 slave 였던 노드도 master 가능(체인).
    if master == slave:
        return False
    _dofs = _resolve_override_dofs(om, master, slave, dofs, rule_id)
    if _dofs is None:
        return False   # remove 오버라이드 — 이 결합 건너뜀
    try:
        ops.equalDOF(master, slave, *_dofs)
    except Exception as e:
        dprint('joint_rules', f'[joint_rules] equalDOF({master},{slave},{_dofs}) 실패: {e}')
        om.registration_failures.append(
            (slave, f'equalDOF master={master} dofs={_dofs}: {e}'))
        return False
    om.constrained_node_ids.add(slave)
    masters_seen.add(master)
    if om.spec is not None:
        from modular_3d.analysis.model_spec import EqualDofRec
        om.spec.equal_dofs.append(EqualDofRec(
            master=master, slave=slave, dofs=tuple(_dofs),
            kind=kind, rule_id=rule_id,
        ))
    return True


# ── 코너-코너/코너-모서리 매칭 코어 (R01·R04 공유) ───────────

def _apply_corner_edge_rule(
    om,
    dofs: Tuple[int, ...],
    corners: Dict[int, List[int]],
    edges: Dict[int, List[Tuple[int, int, int]]],
    vedges: Dict[int, List[Tuple[int, int, int]]],
    rule_id: str,
    edge_split_map: Dict[int, List[Tuple[int, np.ndarray, str]]],
    masters_seen: Optional[Set[int]] = None,
) -> int:
    """코너-코너(a) + 코너-모서리(b) 매칭 코어. R01(모듈)·R04(패널) 공유.

    corners/edges/vedges 는 호출자가 컴포넌트 종류에 맞게 추출해 넘긴다:
      - corners[cid] : 코너 노드 ID 목록
      - edges[cid]   : 수평 모서리 [(mid, nA, nB)]
      - vedges[cid]  : 수직 모서리(수직모듈 기둥). 패널·단층은 빈 리스트.

    매칭 케이스:
      a) 꼭지점-꼭지점: z 차이 ≤ 5mm + 한 축 정렬(≤5mm) + 다른 축 갭(5~225mm).
      b) 꼭지점-모서리: 코너가 다른 컴포넌트 모서리에 직각 사영. 수평 보는 xy
         사영, 수직 기둥은 z 축 사영. 사영점 Q 에 노드 신설 + element 분할 +
         P↔Q equalDOF.

    [정책]
      - 한 코너 P 가 여러 컴포넌트에 닿으면 각 컴포넌트당 1쌍. P 를 master 로
        고정해 다회 결합.
      - 한 컴포넌트 안에서는 (a) gap 최소 하나 / (b) 임계 이하.
      - (a) 우선 — (a) 매칭된 컴포넌트는 (b) 스킵(컴포넌트 단위 양보).
      - 체인 허용, 역방향 중복(순환) 차단.
    """
    if len(corners) < 2:
        return 0
    registered = 0
    if masters_seen is None:
        masters_seen = set()
    # (a) 에서 매칭된 (코너 nP, 상대 cid) 페어 — (b) 의 컴포넌트 단위 양보용.
    matched_pc: Set[Tuple[int, int]] = set()

    # ── (a) 꼭지점-꼭지점 매칭 ──────────────────────────────
    # [성능 2026-06-05] cid_b 후보를 격자로 제한 — nP 주변 칸에 코너를 둔
    # 컴포넌트만 본다. cid_b 순회 순서(corners 삽입 순서)와 best_per_module 선택·
    # 등록 순서는 그대로라 결과 100% 보존(회귀 0).
    # [성능 추가] (a)는 z차 ≤5(같은 층)만 매칭하므로 z 분리 격자(3×3×3)를 써서
    # 같은 xy 다른 층 코너가 한 칸에 몰리지 않게 한다(다층 O(N×층)→O(N)).
    grid_a = _build_corner_grid_z(om, corners)
    for cid_a in corners:
        for nP in corners[cid_a]:
            cP = om.node_tags[nP]
            cand_a = _neighbor_cids_z(grid_a, cP, cid_a)
            best_per_module: Dict[int, Tuple[float, int]] = {}
            for cid_b in corners:
                if cid_b == cid_a or cid_b not in cand_a:
                    continue
                for nb in corners[cid_b]:
                    cb = om.node_tags[nb]
                    dx = abs(cP[0] - cb[0])
                    dy = abs(cP[1] - cb[1])
                    dz = abs(cP[2] - cb[2])
                    if dz > _Z_TOL:
                        continue
                    # [정책 2026-05-14] "한 축 ≤ 5 + 다른 축 ≤ 225" — 같은 점
                    # (둘 다 ≤5) 도 매칭 허용. 캔틸 보 자유단처럼 다른 컴포넌트
                    # 코너와 동일 xy 에 놓이는 케이스를 위해 "다른 축 > 5" 강제
                    # 제거. 대각선(둘 다 > 5) 만 거부.
                    cond_x = dx <= _PERP_TOL and dy <= _GAP_MAX
                    cond_y = dy <= _PERP_TOL and dx <= _GAP_MAX
                    if not (cond_x or cond_y):
                        continue
                    gap = dy if cond_x else dx
                    cur = best_per_module.get(cid_b)
                    if cur is None or gap < cur[0]:
                        best_per_module[cid_b] = (gap, nb)
            for cid_b, (gap, nb) in best_per_module.items():
                if (nP, cid_b) in matched_pc:
                    continue
                ok = _register_pin_link(
                    om, nP, nb,
                    rule_id=rule_id,
                    kind='corner_corner',
                    dofs=dofs, masters_seen=masters_seen,
                )
                if ok:
                    registered += 1
                    matched_pc.add((nP, cid_b))
                    matched_pc.add((nb, cid_a))

    # ── (b) 꼭지점-모서리 매칭 ──────────────────────────────
    # 사영점은 외부 edge_split_map 에 누적 — 모든 룰 끝나고 apply_all_joint_rules
    # 가 _split_edges_and_link_corners 를 한 번만 호출해 일괄 분할.
    # [성능 2026-06-05] cid_b 후보를 보 격자로 제한 — nP 칸에 보를 둔 컴포넌트만
    # 본다. cid_b 순회 순서·보 순회·사영점 누적 순서는 그대로(결과 보존, 회귀 0).
    edge_grid_b = _build_edge_grid(om, edges)
    vedge_grid_b = _build_edge_grid(om, vedges)
    for cid_a in corners:
        for nP in corners[cid_a]:
            cP = om.node_tags[nP]
            cand_e = _edge_cell_cids(edge_grid_b, cP)
            cand_v = _edge_cell_cids(vedge_grid_b, cP)
            # 수평 보 모서리
            for cid_b, edge_list in edges.items():
                if cid_b == cid_a or cid_b not in cand_e:
                    continue
                if (nP, cid_b) in matched_pc:
                    continue
                for mid, nA, nB in edge_list:
                    cA = om.node_tags[nA]
                    cB = om.node_tags[nB]
                    if abs(cP[2] - cA[2]) > _Z_TOL:
                        continue
                    if abs(cP[2] - cB[2]) > _Z_TOL:
                        continue
                    AB = cB - cA
                    AP = cP - cA
                    L2 = float(np.dot(AB[:2], AB[:2]))
                    if L2 < 1e-6:
                        continue
                    t = float(np.dot(AP[:2], AB[:2])) / L2
                    t_min = _PERP_TOL / np.sqrt(L2)
                    if not (t_min < t < 1.0 - t_min):
                        continue
                    proj = cA + AB * t
                    perp = float(np.linalg.norm((cP - proj)[:2]))
                    if perp > _EDGE_PERP_MAX:
                        continue
                    edge_split_map.setdefault(mid, []).append((nP, proj, rule_id))

            # 수직 기둥 모서리 (수직모듈 — 패널은 빈 리스트라 통과)
            for cid_b, vedge_list in vedges.items():
                if cid_b == cid_a or cid_b not in cand_v:
                    continue
                if (nP, cid_b) in matched_pc:
                    continue
                for mid, nA, nB in vedge_list:
                    cA = om.node_tags[nA]
                    cB = om.node_tags[nB]
                    zlo, zhi = (cA[2], cB[2]) if cA[2] < cB[2] else (cB[2], cA[2])
                    if not (zlo + _PERP_TOL < cP[2] < zhi - _PERP_TOL):
                        continue
                    perp = float(np.hypot(cP[0] - cA[0], cP[1] - cA[1]))
                    if perp > _EDGE_PERP_MAX:
                        continue
                    proj = np.array([cA[0], cA[1], cP[2]], dtype=float)
                    edge_split_map.setdefault(mid, []).append((nP, proj, rule_id))

    if registered:
        pass
    return registered


# ── R01 — 모듈-모듈 수평 접합 ─────────────────────────────────

RULE_ID_MOD_MOD_H = 'R01_mod_mod_h'


def apply_module_module_horizontal(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
    edge_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray, str]]]] = None,
    masters_seen: Optional[Set[int]] = None,
) -> int:
    """R01 — 모듈 ↔ 모듈 수평 인접 접합. _apply_corner_edge_rule 코어 사용.

    모듈별 코너(단층 8 / 수직모듈 16) + 수평 보 모서리 + 수직모듈 기둥 모서리
    를 추출해 코어에 위임. 상세 정책은 _apply_corner_edge_rule docstring 참조.
    """
    mod_nids = _collect_module_nodes(om)
    corners: Dict[int, List[int]] = {}
    edges: Dict[int, List[Tuple[int, int, int]]] = {}
    vedges: Dict[int, List[Tuple[int, int, int]]] = {}
    for cid, nids in mod_nids.items():
        c8 = _module_corners(om, nids)
        if not c8:
            continue
        corners[cid] = c8
        # [2026-05-16] allowed_nodes=set(nids) — 중간기둥/중간보가 박혀 host
        # 보·기둥이 sub-부재로 쪼개진 경우에도 sub-부재가 모서리로 잡히도록
        # cid 의 전체 노드 집합을 허용 노드로 넘김. (코너만 보면 sub-부재의
        # mid 끝점 쪽이 코너 아니라 누락됨.)
        node_set = set(nids)
        edges[cid] = _horizontal_edges(
            om, c8, lambda r: r.startswith('module'),
            allowed_nodes=node_set)
        vedges[cid] = _module_vertical_edges(om, c8, allowed_nodes=node_set)
    # [2026-05-14] 캔틸레버 보를 모듈 자료에 합침. 자유단 = 코너, 부재 = 모서리.
    # 다른 모듈/캔틸이 캔틸 보 자유단(코너) 또는 부재(모서리) 와 매칭.
    cants_corners, cants_edges = _collect_cantilever_beam_data(om)
    for cid, free in cants_corners.items():
        corners[cid] = free
        edges[cid] = cants_edges.get(cid, [])
        vedges[cid] = []   # 캔틸 보엔 수직 모서리 없음
    if len(corners) < 2:
        return 0
    if edge_split_map is None:
        edge_split_map = {}
    return _apply_corner_edge_rule(
        om, dofs, corners, edges, vedges,
        RULE_ID_MOD_MOD_H, edge_split_map, masters_seen)


def _split_edges_and_link_corners(
    om,
    edge_split_map: Dict[int, List[Tuple[int, np.ndarray, str]]],
    dofs: Tuple[int, ...],
) -> int:
    """(b) 보조 — 모서리 보를 사영점에서 분할하고 코너 노드와 결합.

    [정책 2026-05-16 — 룰 통합 분할]
    R01·R03·R04 가 각자 분할하지 않고 모든 사영점을 외부 edge_split_map 에
    누적한 뒤 본 함수가 마지막에 한 번만 호출되어 보별 일괄 분할. 이로써:
      - 같은 보에 두 룰의 사영점이 모이면 둘 다 살아남아 두 점 분할 (예:
        짧은 캔틸 보의 왼쪽 = 모듈 코너 / 오른쪽 = 패널 코너).
      - "이미 분할된 보 — 스킵" 차단 자체가 발생 안 함.
      - 같은 위치(±5mm) 사영점은 같은 Q 로 묶이고 첫 결합만 등록 (기존 거리
        가까우면 접합 안 하는 차단은 _register_pin_link 가 그대로 유지).

    edge_split_map 의 값은 (nP_or_N1, proj, rule_id) 튜플 리스트.
    rule_id 는 사영점마다 보존되어 N1↔Q 결합 색이 룰별로 구분됨.

    [CoT] 처리 순서 (mid 별):
      1. 사영점들을 보 방향 매개변수 t_abs 순으로 정렬, 같은 t(±5mm) 묶기.
      2. ops.remove 로 기존 보 element 제거 + om/spec 정리.
      3. 각 사영점에 새 노드 Q 등록 (am.nodes / om.node_tags / spec.nodes).
      4. prev → Q sub-element 등록 (om.beam_elements / spec.beams).
      5. P ↔ Q equalDOF 등록 (사영점에 저장된 rule_id 사용).
      6. 마지막 prev → m.n2 sub-element 등록.
      7. om.member_to_split_ele_tags / spec.member_to_split_tags 기록.

    [함정] ops.remove('element', tag) 후 sub-element 들을 새 tag 로 재등록해야
    한다. 자중 적용(load_calculator) 은 member_to_split_ele_tags 를 보고
    분할된 보를 인식하므로 반드시 갱신.

    Returns: 등록된 P↔Q 결합 수.
    """
    # 룰 간 공유되지 않는 내부 마스터 추적 — 같은 Q 에 묶인 추가 사영점 거부용.
    masters_seen: Set[int] = set()
    if not edge_split_map:
        return 0
    am = om.analysis_model
    if am is None:
        return 0

    from modular_3d.analysis.ops_builder import (
        _section_props, _vecxz_for_member, _geom_transf_tag,
    )
    from modular_3d.analysis.constants import STEEL_E_MPA, STEEL_G_MPA
    from modular_3d.analysis.topology import AnalysisNode, AnalysisMember
    from modular_3d.analysis.model_spec import NodeRec, BeamRec

    # 새 노드/요소 태그 — 기존과 충돌 안 하게 SPLIT_NODE_BASE_OFFSET 영역 사용.
    next_node = (max(om.node_tags.keys()) + SPLIT_NODE_BASE_OFFSET
                 if om.node_tags else SPLIT_NODE_BASE_OFFSET)
    next_ele = max(om.beam_elements.keys()) + 1 if om.beam_elements else 1
    # sub-member id — am.members 의 기존 id 와 충돌 안 하게 동적 부여.
    # 모든 split sub 가 같은 id 공간(부재 ID)이라 충돌 위험 0 — 새 sub 만들 때마다
    # 현재 최대값 + 1.
    next_sub_mid = max(am.members.keys()) + 1 if am.members else 1
    registered = 0

    for mid, plist in edge_split_map.items():
        m = am.members.get(mid)
        if m is None:
            continue
        ele_tag = om.member_to_ele_tag.get(mid)
        if ele_tag is None:
            # 이미 분할된 보 — 본 룰 단독 실행 흐름에선 발생하지 않음.
            continue
        c1 = am.nodes[m.n1].coord
        c2 = am.nodes[m.n2].coord
        bvec = c2 - c1
        blen = float(np.linalg.norm(bvec))
        if blen < 1.0:
            continue
        bdir = bvec / blen

        # 1. 사영점 t_abs 정렬 + 중복(±5mm) 묶기 (rule_id 같이 보존)
        raw: List[Tuple[float, int, np.ndarray, str]] = []
        for nP, proj, rid in plist:
            t_abs = float(np.dot(proj - c1, bdir))
            if t_abs < 1.0 or t_abs > blen - 1.0:
                continue   # 보 끝 정확 일치 → (a) 영역
            raw.append((t_abs, nP, proj, rid))
        raw.sort(key=lambda x: x[0])
        deduped: List[Tuple[float, int, np.ndarray, str]] = []
        # idx → 같은 사영점에 묶인 추가 (nP, rule_id) 들
        extra_at: Dict[int, List[Tuple[int, str]]] = {}
        for entry in raw:
            if deduped and abs(entry[0] - deduped[-1][0]) < 5.0:
                extra_at.setdefault(len(deduped) - 1, []).append(
                    (entry[1], entry[3]))
                continue
            deduped.append(entry)
        if not deduped:
            continue

        # 2. 기존 element 제거
        ops.remove('element', ele_tag)
        om.beam_elements.pop(ele_tag, None)
        if om.spec is not None:
            om.spec.beams = [b for b in om.spec.beams if b.tag != ele_tag]
        om.member_to_ele_tag.pop(mid, None)
        sub_tags: List[int] = []

        # 단면·좌표변환은 원본 보 그대로
        A, Iy, Iz, J = _section_props(m)
        vec_xz = _vecxz_for_member(c1, c2)
        tt = _geom_transf_tag(m.kind, vec_xz)
        src_cid = m.source_comp_ids[0] if m.source_comp_ids else 0

        prev_node = m.n1
        for idx, (t_abs, nP, proj, rule_id) in enumerate(deduped):
            # 3. 새 노드 Q (보 직선 위 사영점 — 보가 휘지 않게)
            Q = next_node
            next_node += 1
            ops.node(Q, float(proj[0]), float(proj[1]), float(proj[2]))
            om.node_tags[Q] = proj.copy()
            am.nodes[Q] = AnalysisNode(
                id=Q, coord=proj.copy(), source_comp_id=src_cid,
            )
            if om.spec is not None:
                om.spec.nodes.append(NodeRec(
                    tag=Q, coord=proj.copy(), role='split_proj',
                    source_comp_id=src_cid,
                ))
            # 4. sub-element prev → Q
            ops.element('elasticBeamColumn', next_ele, prev_node, Q,
                        A, STEEL_E_MPA, STEEL_G_MPA, J, Iy, Iz, tt)
            om.beam_elements[next_ele] = (prev_node, Q, m.kind, m.role)
            if om.spec is not None:
                om.spec.beams.append(BeamRec(
                    tag=next_ele, n1=prev_node, n2=Q,
                    kind=m.kind, role=m.role,
                    section_w=float(m.section_w),
                    section_h=float(m.section_h),
                    section_t=float(m.section_t),
                    source_comp_ids=list(m.source_comp_ids),
                ))
            # am.members 에 sub 정식 등록 — 단일 진실 자료원 유지.
            # is_split_sub=True 로 표시해 자중·물량 후처리가 중복 처리 안 함.
            sub_mid = next_sub_mid
            next_sub_mid += 1
            am.members[sub_mid] = AnalysisMember(
                id=sub_mid, n1=prev_node, n2=Q, kind=m.kind, role=m.role,
                section_w=float(m.section_w), section_h=float(m.section_h),
                section_t=float(m.section_t),
                source_comp_ids=list(m.source_comp_ids),
                merge_group=m.merge_group,
                is_split_sub=True, parent_member_id=mid,
            )
            sub_tags.append(next_ele)
            next_ele += 1
            # 5. P ↔ Q 결합. P 를 master 로 (P 는 여러 모듈과 결합 가능),
            #    Q 는 새 노드라 slave 로 한 번만. rule_id 는 사영점별로 저장
            #    된 값 사용 — 와이어프레임 색이 룰별로 분리됨.
            ok = _register_pin_link(om, nP, Q,
                                     rule_id=rule_id,
                                     kind='corner_edge',
                                     dofs=dofs, masters_seen=masters_seen)
            if ok:
                registered += 1
            # 같은 사영점(±5mm)에 묶인 추가 마스터들 — Q 가 첫 결합으로 이미
            # constrained 라 _register_pin_link 의 일반 검사는 거부함. 그러나
            # 짧은 캔틸 보 양쪽에서 모듈·패널이 거의 같은 위치로 동시 사영되는
            # 케이스는 둘 다 살아남아야 하므로(R02/R03 직접결합과 동일 정책)
            # Penalty handler 가 과구속을 견디는 가정으로 검사 우회 강제 등록.
            from modular_3d.analysis.model_spec import EqualDofRec
            for extra_nP, extra_rid in extra_at.get(idx, []):
                if extra_nP == Q:
                    continue   # 자기 자신 결합 방지
                _dofs = _resolve_override_dofs(om, extra_nP, Q, dofs, extra_rid)
                if _dofs is None:
                    continue   # remove 오버라이드 — 이 결합 건너뜀
                try:
                    ops.equalDOF(extra_nP, Q, *_dofs)
                except Exception as e:
                    dprint('joint_rules', f'[joint_rules] equalDOF({extra_nP},{Q}) 묶음 강제 등록 실패: {e}')
                    om.registration_failures.append(
                        (Q, f'equalDOF 묶음 강제 master={extra_nP}: {e}'))
                    continue
                om.constrained_node_ids.add(Q)
                if om.spec is not None:
                    om.spec.equal_dofs.append(EqualDofRec(
                        master=extra_nP, slave=Q, dofs=tuple(_dofs),
                        kind='corner_edge', rule_id=extra_rid,
                    ))
                registered += 1
            prev_node = Q

        # 6. 마지막 segment prev → m.n2
        ops.element('elasticBeamColumn', next_ele, prev_node, m.n2,
                    A, STEEL_E_MPA, STEEL_G_MPA, J, Iy, Iz, tt)
        om.beam_elements[next_ele] = (prev_node, m.n2, m.kind, m.role)
        if om.spec is not None:
            om.spec.beams.append(BeamRec(
                tag=next_ele, n1=prev_node, n2=m.n2,
                kind=m.kind, role=m.role,
                section_w=float(m.section_w),
                section_h=float(m.section_h),
                section_t=float(m.section_t),
                source_comp_ids=list(m.source_comp_ids),
            ))
        # 마지막 sub 도 am.members 에 등록.
        sub_mid_last = next_sub_mid
        next_sub_mid += 1
        am.members[sub_mid_last] = AnalysisMember(
            id=sub_mid_last, n1=prev_node, n2=m.n2, kind=m.kind, role=m.role,
            section_w=float(m.section_w), section_h=float(m.section_h),
            section_t=float(m.section_t),
            source_comp_ids=list(m.source_comp_ids),
            merge_group=m.merge_group,
            is_split_sub=True, parent_member_id=mid,
        )
        sub_tags.append(next_ele)
        next_ele += 1

        # 7. 분할 매핑 기록 — 자중 적용용
        if sub_tags:
            om.member_to_split_ele_tags[mid] = sub_tags
            if om.spec is not None:
                om.spec.member_to_split_tags[mid] = sub_tags[:]

    # [성능 2026-05-18] split sub 가 am.members 에 추가되었으므로 role 인덱스
    # 무효화. 다음 호출이 lazy 빌드로 신선한 인덱스 받음.
    if registered and hasattr(am, 'invalidate_indices'):
        am.invalidate_indices()
    return registered


# ── 수직 적층 매칭 코어 (R02·R04 공유) ───────────────────────

def _apply_vstack_rule(
    om,
    dofs: Tuple[int, ...],
    corners: Dict[int, List[int]],
    rule_id: str,
    z_max: float,
) -> int:
    """수직 적층 코너-코너 매칭 코어. R02(모듈)·R04수직(패널) 공유.

    [정책 2026-05-14 — 단순화]
    서로 다른 두 컴포넌트의 코너가 xy 같고(±5mm) z 갭(5~z_max mm) 이면 무조건
    equalDOF 핀 결합. 충돌 검사 없음 — ops_solver 의 Penalty handler(1e14) 가
    과구속을 견딘다. master/slave 는 z 작은 쪽 = master.

    Args:
      corners: {cid: [nid, ...]}
      rule_id: 와이어프레임 색 구분용.
      z_max:   z 갭 상한 (모듈은 _VSTACK_MAX).
    """
    if len(corners) < 2:
        return 0
    from modular_3d.analysis.model_spec import EqualDofRec
    registered = 0
    seen: Set[Tuple[int, int]] = set()
    # [성능 2026-06-05] 전수 cid 쌍 비교(O(N²)) → 격자 후보 비교(O(N)).
    # 각 코너는 주변 3×3 칸의 다른 cid 코너하고만 비교한다. xy 정렬(±5mm)이
    # 조건이라 후보는 같은 칸 근처뿐 — 멀리 떨어진 코너는 자동 제외. master/slave
    # 는 z 작은 쪽으로 결정적이고 seen 으로 중복을 막으므로, 한 쌍을 양방향으로
    # 만나도 한 번만 등록된다(결과 = 전수 비교와 동일, 회귀 0).
    grid = _build_corner_grid(om, corners)
    for cid_a in corners:
        for nA in corners[cid_a]:
            cA = om.node_tags[nA]
            ix, iy = _cell_of(cA)
            for dcx in (-1, 0, 1):
                for dcy in (-1, 0, 1):
                    for cid_b, nB in grid.get((ix + dcx, iy + dcy), ()):
                        if cid_b == cid_a:
                            continue
                        cB = om.node_tags[nB]
                        if abs(cA[0] - cB[0]) > _PERP_TOL:
                            continue
                        if abs(cA[1] - cB[1]) > _PERP_TOL:
                            continue
                        dz = abs(cA[2] - cB[2])
                        if not (_PERP_TOL < dz <= z_max):
                            continue
                        master, slave = (nA, nB) if cA[2] < cB[2] else (nB, nA)
                        if (master, slave) in seen:
                            continue
                        seen.add((master, slave))
                        _dofs = _resolve_override_dofs(om, master, slave, dofs,
                                                       rule_id)
                        if _dofs is None:
                            continue   # remove 오버라이드 — 이 결합 건너뜀
                        try:
                            ops.equalDOF(master, slave, *_dofs)
                        except Exception as e:
                            dprint('joint_rules', f'[joint_rules] equalDOF({master},{slave}) 실패: {e}')
                            om.registration_failures.append(
                                (slave, f'equalDOF master={master}: {e}'))
                            continue
                        om.constrained_node_ids.add(slave)
                        if om.spec is not None:
                            om.spec.equal_dofs.append(EqualDofRec(
                                master=master, slave=slave, dofs=tuple(_dofs),
                                kind='vstack', rule_id=rule_id,
                            ))
                        registered += 1
    return registered


# ── R02 — 모듈-모듈 수직 적층 접합 ────────────────────────────

RULE_ID_MOD_MOD_V = 'R02_mod_mod_v'


def _collect_mid_column_nodes(om) -> Dict[int, List[int]]:
    """중간기둥(role='mid_column') 컴포넌트별 끝점 노드 → {cid: [nid, ...]}.

    [정책 2026-06-05 — R02 중간기둥 수직 적층]
    중간기둥은 층마다 별도 컴포넌트로 같은 평면 위치에 z=k·H 로 복제된다.
    각 중간기둥 부재의 두 끝점(바닥·상단)을 컴포넌트 ID 별로 모아
    _apply_vstack_rule 에 넘기면, 아래층 상단(z≈3200) ↔ 위층 하단(z≈3420,
    갭 ≈220mm)이 모듈 코너와 똑같은 R02 코어로 자연 결합된다. 서로 다른 cid
    끼리만 비교하므로(같은 중간기둥의 상단·하단 z 갭은 3200>225 라 어차피 제외)
    인접 층 상단↔하단 한 쌍만 매칭된다.

    [함정] 중간기둥 끝점이 _split_hosts_for_mid_endpoints 로 모듈 보에 통합되어
    좌표가 옮겨진 경우에도 om.node_tags 의 현재 좌표를 쓰므로 정렬 판정이 정확.
    """
    am = om.analysis_model
    if am is None:
        return {}
    out: Dict[int, List[int]] = {}
    for mid in am.members_by_role('mid_column'):
        m = am.members[mid]
        if not m.source_comp_ids:
            continue
        cid = m.source_comp_ids[0]
        bucket = out.setdefault(cid, [])
        for nid in (m.n1, m.n2):
            if nid in om.node_tags and nid not in bucket:
                bucket.append(nid)
    return out


def apply_module_module_vertical(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
) -> int:
    """R02 — 모듈 ↔ 모듈 수직 적층 접합. _apply_vstack_rule 코어 사용.

    하부 모듈 천장 코너 ↔ 상부 모듈 바닥 코너 (xy 정렬 + z 갭 5~225mm).
    [2026-06-05] 모듈 내부 중간기둥의 위·아래층 적층도 같은 R02 규칙으로 결합.
    """
    n = 0
    mod_nids = _collect_module_nodes(om)
    if len(mod_nids) >= 2:
        corners: Dict[int, List[int]] = {}
        for cid, nids in mod_nids.items():
            c = _module_corners(om, nids)
            if c:
                corners[cid] = c
        if len(corners) >= 2:
            n += _apply_vstack_rule(
                om, dofs, corners, RULE_ID_MOD_MOD_V, _VSTACK_MAX)
    # 중간기둥 끝점도 별도 vstack — 모듈 코너 집합과 분리해 호출하므로 모듈
    # 코너↔중간기둥 끝점 교차 매칭이 생기지 않는다(같은 평면이라도 종류별 격리).
    mc_corners = _collect_mid_column_nodes(om)
    if len(mc_corners) >= 2:
        n += _apply_vstack_rule(
            om, dofs, mc_corners, RULE_ID_MOD_MOD_V, _VSTACK_MAX)
    return n


# ── R03 — 바닥패널-모듈 접합 ──────────────────────────────────

RULE_ID_PANEL_MOD = 'R03_panel_mod'


# 패널류(바닥패널 + 캔틸레버 슬래브) 부재 role 집합.
_PANEL_LIKE_ROLES = {'floor_edge_beam', 'cantilever_slab_beam'}


def _collect_cantilever_beam_data(
    om,
) -> Tuple[Dict[int, List[int]], Dict[int, List[Tuple[int, int, int]]]]:
    """캔틸레버 보 → ({cid: [자유단nid]}, {cid: [(mid, n1, n2)]}).

    [정책 2026-05-16 — 부모 종류 무관 robust 자유단 식별]
    부모가 모듈인지 패널인지 사전에 모르는 상태에서도 동작하도록, "다른 컴포
    넌트와 nid 를 공유하는 끝점 = anchor (부모와 통합됨), 자기 cid 에만 등장
    하는 끝점 = 자유단" 으로 식별. _consolidate_dependent_nodes 가 부모와의
    통합으로 anchor 측 nid 를 부모 cid 노드 자료와 같은 ID 로 만들기 때문에
    comp_to_nodes 두 cid 에 동시 등장 ↔ 한 cid 에만 등장이 자연 구분된다.

    이전 정책("부모 모듈 노드 집합 비교")은 캔틸 보 부모가 패널인 경우(코드
    상 가능) 양 끝 모두 자유단으로 잘못 식별되는 한계가 있었음.

    edges 는 _horizontal_edges 의 "두 끝점 corners" 조건을 우회 — 캔틸 보
    부재의 한 끝(anchor) 이 corners 에 없어도 모서리 segment 로 등록.
    """
    am = om.analysis_model
    if am is None:
        return {}, {}
    # 각 nid 가 등장하는 cid 수 — 2 이상이면 다른 컴포넌트와 공유 = anchor 후보.
    nid_cid_count: Dict[int, int] = {}
    for _cid, _nids in am.comp_to_nodes.items():
        for _nid in _nids:
            nid_cid_count[_nid] = nid_cid_count.get(_nid, 0) + 1

    cants_corners: Dict[int, List[int]] = {}
    cants_edges: Dict[int, List[Tuple[int, int, int]]] = {}
    # role 인덱스로 cantilever_beam 만 추림.
    for mid in am.members_by_role('cantilever_beam'):
        m = am.members[mid]
        if not m.source_comp_ids:
            continue
        cid = m.source_comp_ids[0]
        nids = am.comp_to_nodes.get(cid, [])
        # 자유단 = 다른 cid 와 공유 안 되는 끝점(=부모와 통합 안 된 끝점).
        free = [n for n in nids
                if n in om.node_tags and nid_cid_count.get(n, 0) <= 1]
        if free:
            cants_corners[cid] = free
        cants_edges.setdefault(cid, []).append((mid, m.n1, m.n2))
    # [진단] 캔틸 보 자유단 좌표 출력.
    _dbg = []
    for cid in sorted(cants_corners):
        for nid in cants_corners[cid]:
            c = om.node_tags[nid]
            _dbg.append(f'cid{cid}/free#{nid}=({c[0]:.0f},{c[1]:.0f},{c[2]:.0f})')
    return cants_corners, cants_edges


def _collect_panel_like_corners(om) -> Dict[int, List[int]]:
    """패널류 컴포넌트별 코너 노드 ID → {cid: [nid, ...]}.

    [정책 2026-05-14]
    바닥패널과 캔틸레버 슬래브를 동등하게 패널류로 취급하되, anchor 제외는
    캔틸 슬래브에만 적용한다:
      - FloorPanel(floor_edge_beam)        : 4 코너 전부.
        캔틸이 바닥패널 한 변에 붙으면 그 변의 코너 2개가 캔틸 anchor 와 같은
        nid 로 통합되어 anchor_ids 에 들어가지만, 바닥패널 입장에선 정상
        코너이므로 유지해야 한다. (이 일괄 제외가 버그였음 — 바닥패널이 4→2)
      - CantileverSlab(cantilever_slab_beam): 자유단 코너만. anchor 코너
        (부모와 맞닿는 쪽) 는 _consolidate_dependent_nodes 가 부모 패널/모듈
        노드와 같은 nid 로 통합해 이미 결합돼 있으므로 제외.

    anchor 노드는 am.cantilever_anchor_node_ids 로 식별.
    """
    am = om.analysis_model
    if am is None or not getattr(am, 'comp_to_nodes', None):
        return {}
    # cid → 패널류 종류. FloorPanel 은 floor_edge_beam, CantileverSlab 은
    # cantilever_slab_beam 부재만 가지므로 role 로 1:1 구분 가능.
    cid_kind: Dict[int, str] = {}
    # role 인덱스로 두 role 만 직접 추림 (전체 순회 회피).
    for mid in am.members_by_role('floor_edge_beam'):
        m = am.members[mid]
        if m.source_comp_ids:
            cid_kind[m.source_comp_ids[0]] = 'panel'
    for mid in am.members_by_role('cantilever_slab_beam'):
        m = am.members[mid]
        if m.source_comp_ids:
            cid_kind[m.source_comp_ids[0]] = 'cantilever'
    anchor_ids = getattr(am, 'cantilever_anchor_node_ids', set())
    out: Dict[int, List[int]] = {}
    for cid, kind in cid_kind.items():
        raw = am.comp_to_nodes.get(cid, [])
        if kind == 'cantilever':
            # 캔틸 슬래브 — anchor(부모와 통합된 코너) 제외, 자유단만.
            nids = [n for n in raw
                    if n in om.node_tags and n not in anchor_ids]
        else:
            # 바닥패널 — 4 코너 전부. anchor 제외 안 함.
            nids = [n for n in raw if n in om.node_tags]
        if nids:
            out[cid] = nids
    # [진단 2026-05-14] 패널류 수집 결과 확인용.
    _dbg = [f'cid{cid}({cid_kind[cid]}/코너{len(out.get(cid, []))})'
            for cid in sorted(cid_kind)]
    return out


def apply_panel_module(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
    edge_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray, str]]]] = None,
) -> int:
    """R03 — 바닥패널 ↔ 모듈 천장 접합. 패널이 모듈에 의존하는 단방향.

    [정책 2026-05-14]
    바닥패널은 모듈과 모듈 사이에 끼어 대공간을 만드는 부재. 패널 꼭지점이
    모듈 천장(천장 코너 또는 천장보) 과 결합한다.

    매칭 (패널 꼭지점 P → 모듈 천장):
      - xy 평면: R01 (a)(b) 와 동일.
          (a) 모듈 코너: 한 축 정렬(≤5mm) + 다른 축 갭(5~225mm)
          (b) 모듈 수평 보: 보에 직각 사영, perp ≤ _EDGE_PERP_MAX
      - z 축: |dz| ∈ (5mm, 225mm] — 패널과 가까운 모듈 레벨만 매칭.
        role 로 "천장" 을 좁히지 않고 z 조건이 천장 레벨을 자연 선택하므로,
        수직모듈의 중간 층 천장(level 1·2) 도 패널 결합 대상이 된다.

    z 차이는 중간 노드 N1 으로 두 직각 단계로 분리:
      - N1 = (P 의 xy, 모듈 천장 z)
      - 결합① P ↔ N1   : 순수 수직 핀 (dx=dy=0)
      - 결합② N1 ↔ 모듈 천장 코너 M (또는 천장보 사영점 Q) : 순수 수평 핀

    [정책]
      - 패널 꼭지점이 master, 모듈 측이 slave (패널이 모듈에 의존).
      - 한 모듈 안에서는 (a) 가장 가까운 코너 / (b) 가장 가까운 보 하나만.
      - (a) 우선 — (a) 에서 매칭된 모듈은 (b) 스킵(모듈 단위 양보).
      - 여러 모듈에 동시에 닿으면 각 모듈당 1쌍.
      - 충돌 검사 없음 — Penalty handler 가 과구속을 견딤(R02 와 동일 정책).

    Returns: 등록된 equalDOF 쌍 수 (수직+수평 단계 모두 카운트).
    """
    panel_corners = _collect_panel_like_corners(om)
    if not panel_corners:
        return 0
    mod_nids = _collect_module_nodes(om)
    if not mod_nids:
        return 0
    am = om.analysis_model

    # 모듈별 전체 코너 + 전체 수평 보.
    # [정책 2026-05-14 수직모듈 대응]
    # role 로 "천장" 을 좁히지 않는다. 수직모듈은 z 레벨 4개인데 role 부여가
    # level 0~2 = 'module_bottom_beam', level 3(옥상)만 'module_top_beam' 이라,
    # role 로 거르면 중간 층 천장(level 1·2) 이 통째로 빠진다. 대신 아래의
    # z 차이 조건(dz ∈ (5, 225]) 이 패널 높이에 맞는 레벨만 자연 선택한다 —
    # 단층 모듈 바닥보는 dz 가 크므로(≈3400) 자동 제외, 수직모듈은 패널과
    # 가까운 레벨만 매칭.
    mod_corners: Dict[int, List[int]] = {}
    mod_edges: Dict[int, List[Tuple[int, int, int]]] = {}
    for cid, nids in mod_nids.items():
        c = _module_corners(om, nids)
        if not c:
            continue
        mod_corners[cid] = c
        # [2026-05-16] allowed_nodes — 중간 부재로 분할된 sub-부재도 인식.
        mod_edges[cid] = _horizontal_edges(
            om, c, lambda r: r.startswith('module'),
            allowed_nodes=set(nids))
    # [2026-05-14] 캔틸레버 보도 모듈 자리에 합침 — 패널이 캔틸 보 자유단(코너)
    # 또는 부재(모서리) 와 매칭 가능하게.
    cants_corners, cants_edges = _collect_cantilever_beam_data(om)
    for cid, free in cants_corners.items():
        mod_corners[cid] = free
        mod_edges[cid] = cants_edges.get(cid, [])

    if not mod_corners:
        return 0

    # [성능 2026-06-05] 패널 꼭지점이 닿을 모듈 코너·보 후보를 격자로 제한.
    # _match_pass 의 (a) 코너 루프·(b) 보 루프가 nP 칸 후보 모듈만 보도록 격자를
    # 넘긴다(순회 순서·선택은 그대로 → 결과 보존).
    mod_grid = _build_corner_grid(om, mod_corners)
    mod_edge_grid = _build_edge_grid(om, mod_edges)

    # [진단 2026-05-14] 패널 꼭지점 z 와 모듈 코너 z 의 실제 분포 출력.
    # 매칭 안 되는 원인이 z 차이가 매칭 범위 밖인지 확인용.
    _pzs = sorted(set(round(om.node_tags[n][2], 1)
                      for ns in panel_corners.values() for n in ns))
    _mzs = sorted(set(round(om.node_tags[n][2], 1)
                      for c in mod_corners.values() for n in c))

    from modular_3d.analysis.model_spec import EqualDofRec, NodeRec
    from modular_3d.analysis.topology import AnalysisNode

    registered = 0
    # N1 노드 태그 — 기존·split 노드(10000+)와 충돌 안 하게 RULE_NODE_OFFSET_R03(20000)+ 영역.
    next_node = max(om.node_tags.keys(), default=0) + RULE_NODE_OFFSET_R03
    # 사영점은 외부 edge_split_map 에 누적 — apply_all_joint_rules 가 마지막에
    # 한 번 _split_edges_and_link_corners 호출해 룰 간 일괄 분할.
    if edge_split_map is None:
        edge_split_map = {}

    def _make_n1(px: float, py: float, mz: float, source_comp_id: int = 0) -> int:
        """중간 노드 N1 = (px, py, mz) 등록 — ops/om/am/spec 동시. nid 반환.

        source_comp_id: 가교가 흡수될 본체 ID (다이어프램 흡수 매칭용).
                        호출처에서 모듈 보 측 본체 ID 를 넘긴다.
        """
        nonlocal next_node
        nid = next_node
        next_node += 1
        coord = np.array([px, py, mz], dtype=float)
        ops.node(nid, float(px), float(py), float(mz))
        om.node_tags[nid] = coord
        am.nodes[nid] = AnalysisNode(
            id=nid, coord=coord.copy(), source_comp_id=source_comp_id)
        if om.spec is not None:
            om.spec.nodes.append(NodeRec(
                tag=nid, coord=coord.copy(), role='panel_z_route',
                source_comp_id=source_comp_id))
        return nid

    def _link(master: int, slave: int, kind: str) -> None:
        """equalDOF 등록 — 충돌 검사 없음(Penalty handler 가 과구속 견딤)."""
        nonlocal registered
        _dofs = _resolve_override_dofs(om, master, slave, dofs, RULE_ID_PANEL_MOD)
        if _dofs is None:
            return   # remove 오버라이드 — 이 결합 건너뜀
        try:
            ops.equalDOF(master, slave, *_dofs)
        except Exception as e:
            dprint('joint_rules', f'[joint_rules] equalDOF({master},{slave}) 실패: {e}')
            om.registration_failures.append(
                (slave, f'equalDOF master={master}: {e}'))
            return
        om.constrained_node_ids.add(slave)
        if om.spec is not None:
            om.spec.equal_dofs.append(EqualDofRec(
                master=master, slave=slave, dofs=tuple(_dofs),
                kind=kind, rule_id=RULE_ID_PANEL_MOD))
        registered += 1

    def _match_pass(nP: int, cP, matched_modules: Set[int],
                    rect_mode: bool) -> bool:
        """패널 꼭지점 nP 한 개에 대해 (a)(b) 매칭 한 패스.

        rect_mode=True  : 직각 결합만 (dz ∈ (5, 225], N1 중간 노드 두 단계).
        rect_mode=False : 수평 결합만 (dz ≤ 5, N1 없이 직접 결합).

        Returns: 이 패스에서 하나라도 결합됐으면 True.
        """
        found = False
        # ── (a) 패널 꼭지점 ↔ 모듈 코너 ──────────────────
        # 격자 후보 — nP 주변 3×3 칸에 코너를 둔 모듈만 본다(나머지 동일).
        cand_mods = _neighbor_cids(mod_grid, cP, -1)
        for mcid, tc in mod_corners.items():
            if mcid in matched_modules or mcid not in cand_mods:
                continue
            best: Optional[Tuple[float, int]] = None  # (gap, nM)
            for nM in tc:
                cM = om.node_tags[nM]
                dx = abs(cP[0] - cM[0])
                dy = abs(cP[1] - cM[1])
                dz = abs(cP[2] - cM[2])
                # [정책 2026-05-14] 같은 점(dx,dy ≤ 5) 도 (a) 매칭 허용 —
                # 캔틸 보 자유단처럼 모듈 코너와 동일 xy 케이스 대응.
                cond_x = dx <= _PERP_TOL and dy <= _GAP_MAX
                cond_y = dy <= _PERP_TOL and dx <= _GAP_MAX
                if not (cond_x or cond_y):
                    continue
                # z 모드 필터 — 직각이면 (5, 225], 수평이면 (≤5).
                if rect_mode:
                    if not (_PERP_TOL < dz <= _GAP_MAX):
                        continue
                else:
                    if dz > _PERP_TOL:
                        continue
                gap = dy if cond_x else dx
                if best is None or gap < best[0]:
                    best = (gap, nM)
            if best is not None:
                nM = best[1]
                mz = float(om.node_tags[nM][2])
                # 가교 노드 소속 = 모듈 본체 ID (다이어프램 흡수 매칭용).
                mod_cid = am.nodes[nM].source_comp_id
                if rect_mode:
                    # 높이 다름 — N1 중간 노드로 수직 + 수평 두 단계.
                    n1 = _make_n1(cP[0], cP[1], mz, source_comp_id=mod_cid)
                    _link(nP, n1, 'panel_module_vert')    # ① 수직
                    _link(n1, nM, 'panel_module_corner')  # ② 수평
                else:
                    # 같은 높이 — N1 없이 직접 결합.
                    _link(nP, nM, 'panel_module_corner')
                matched_modules.add(mcid)
                found = True

        # ── (b) 패널 꼭지점 ↔ 모듈 수평 보 ───────────────
        # 격자 후보 — nP 칸에 보를 둔 모듈만 본다(순회 순서·선택 동일).
        cand_edge_mods = _edge_cell_cids(mod_edge_grid, cP)
        for mcid, tedges in mod_edges.items():
            if mcid in matched_modules or mcid not in cand_edge_mods:
                continue   # (a) 에서 이미 결합 — 모듈 단위 양보
            best_b: Optional[Tuple[float, int, np.ndarray, float]] = None
            for mid, nA, nB in tedges:
                cA = om.node_tags[nA]
                cB = om.node_tags[nB]
                dz = abs(cP[2] - cA[2])
                if rect_mode:
                    if not (_PERP_TOL < dz <= _GAP_MAX):
                        continue
                else:
                    if dz > _PERP_TOL:
                        continue
                AB = cB - cA
                AP = cP - cA
                L2 = float(np.dot(AB[:2], AB[:2]))
                if L2 < 1e-6:
                    continue
                t = float(np.dot(AP[:2], AB[:2])) / L2
                t_min = _PERP_TOL / np.sqrt(L2)
                if not (t_min < t < 1.0 - t_min):
                    continue
                proj = cA + AB * t   # 보 위 사영점 (z = 보 z)
                perp = float(np.linalg.norm((cP - proj)[:2]))
                if perp > _EDGE_PERP_MAX:
                    continue
                if best_b is None or perp < best_b[0]:
                    best_b = (perp, mid, proj, float(cA[2]))
            if best_b is not None:
                _perp, mid, proj, mz = best_b
                if rect_mode:
                    # 가교 소속 = 모듈 보가 속한 본체 ID.
                    mod_member = am.members.get(mid)
                    mod_cid = (mod_member.source_comp_ids[0]
                               if mod_member and mod_member.source_comp_ids
                               else 0)
                    # N1 중간 노드로 수직 분리 → N1↔Q 는 보 분할 공유 함수.
                    n1 = _make_n1(cP[0], cP[1], mz, source_comp_id=mod_cid)
                    _link(nP, n1, 'panel_module_vert')
                    edge_split_map.setdefault(mid, []).append(
                        (n1, proj, RULE_ID_PANEL_MOD))
                else:
                    # 같은 높이 — N1 없이 패널 꼭지점 직접 보 분할에 투입.
                    edge_split_map.setdefault(mid, []).append(
                        (nP, proj, RULE_ID_PANEL_MOD))
                matched_modules.add(mcid)
                found = True
        return found

    # [정책 2026-05-17] FP-종속벽 공유 코너에서는 rect 패스 차단.
    # 종속 벽 하단 코너 nid = FP 코너 nid (토폴로지 자동 통합). 그 노드는
    # 사용자 사양상 "위층 모듈 기둥 하단(같은 z) 과 수평 결합" 을 원하고,
    # "아래층 모듈 천장보로 N1 라우팅(rect) 결합" 은 아래로 가는 벽 상단·
    # 모듈 기둥 상단 루트(R05+R06) 와 겹쳐 불필요. rect 패스가 먼저 잡히면
    # 같은-z 패스가 skip 되어 위층 모듈 기둥 하단 결합이 누락되므로, 이
    # 노드에서만 rect 를 통째로 건너뛰고 같은-z 패스만 시도.
    walls_for_skip = _collect_wall_data(om)
    fp_wall_shared: Set[int] = set()
    for _wcid, _wdat in walls_for_skip.items():
        if _wdat.get('is_merged'):
            fp_wall_shared.update(_wdat['bot'])

    # 패널 꼭지점 단위로 직각 우선 — 직각이 하나라도 되면 수평은 시도 안 함.
    # (이중 접합 방지: "수직 내려갔다 수평" 으로 되던 꼭지점이 동시에 완전
    #  수평으로도 결합되던 문제 차단.)
    for pcid, pnodes in panel_corners.items():
        for nP in pnodes:
            cP = om.node_tags[nP]
            matched_modules: Set[int] = set()
            if nP in fp_wall_shared:
                # FP-종속벽 공유 코너 — rect 차단, 같은-z 만 시도
                _match_pass(nP, cP, matched_modules, rect_mode=False)
            else:
                rect_found = _match_pass(
                    nP, cP, matched_modules, rect_mode=True)
                if not rect_found:
                    _match_pass(nP, cP, matched_modules, rect_mode=False)

    # 천장보 분할 + N1 ↔ Q 결합은 외부 일괄 분할로 옮김 — apply_all_joint_rules
    # 가 R01·R03·R04 사영점이 모두 누적된 후 _split_edges_and_link_corners 를
    # 한 번만 호출. 사영점에 RULE_ID_PANEL_MOD 가 저장돼 있어 N1↔Q 결합 색이
    # R03(연두) 로 정상 매핑됨.
    if registered:
        pass
    return registered


# ── R04 — 패널-패널 접합 ──────────────────────────────────────

RULE_ID_PANEL_PANEL = 'R04_panel_panel'


def apply_panel_panel_horizontal(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
    edge_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray, str]]]] = None,
) -> int:
    """R04 — 패널류 ↔ 패널류 수평 접합. 모듈-모듈(R01) 과 동일 로직.

    패널류 = 바닥패널 + 캔틸레버 슬래브. 평면 사각형(z 한 레벨). 코너 +
    가장자리보(role ∈ _PANEL_LIKE_ROLES) 를 추출해 _apply_corner_edge_rule
    코어에 위임 — R01 과 완전히 같은 (a) 꼭지점-꼭지점 + (b) 꼭지점-모서리.
    패널류는 수직 모서리(기둥) 가 없으므로 vedges 는 빈 리스트.

    캔틸레버 슬래브는 자유단 코너 2개만 — anchor 코너는 부모와 nid 통합되어
    이미 결합돼 있음(_collect_panel_like_corners 참조). 자유단 코너 사이의
    끝변 보 1개가 가장자리보로 잡힘.
    """
    panel_corners = _collect_panel_like_corners(om)
    if len(panel_corners) < 2:
        return 0
    corners: Dict[int, List[int]] = {}
    edges: Dict[int, List[Tuple[int, int, int]]] = {}
    vedges: Dict[int, List[Tuple[int, int, int]]] = {}
    for cid, nids in panel_corners.items():
        corners[cid] = nids
        edges[cid] = _horizontal_edges(
            om, nids, lambda r: r in _PANEL_LIKE_ROLES)
        vedges[cid] = []
    if edge_split_map is None:
        edge_split_map = {}
    return _apply_corner_edge_rule(
        om, dofs, corners, edges, vedges,
        RULE_ID_PANEL_PANEL, edge_split_map)


# ── 벽패널 자료 수집 헬퍼 (R05·R06·R07 공유) ─────────────────

def _collect_wall_data(om) -> Dict[int, Dict]:
    """벽 컴포넌트별 노드/종속여부 → {cid: {'top':[nid],'bot':[nid],
                                          'all':[nid],'is_merged':bool}}.

    [정책]
      - 벽 cid 식별: am.members 중 role 이 'wall_' 로 시작하는 부재의 source_cid.
      - 종속(merged) 판별: 같은 cid 안에 role='wall_bottom_runner' 부재가 있으면
        비종속(독립). 없으면 종속 — topology._extract_wall 가 합체 시 하부 런너
        생성을 생략하므로(skip_bottom = merged_fp_id is not None).
      - top/bot 노드: 코너 z 값 클러스터링(±_Z_TOL) 으로 위/아래 레벨 분리.
        종속 벽의 하단 코너는 토폴로지가 이미 FP 코너 nid 와 통합한 상태라
        wall cid 의 comp_to_nodes 에 같은 nid 가 들어있음.

    [함정]
      - comp_to_nodes 에는 다른 부재(중간기둥/이웃 벽) 가 박혀 분할된 sub-노드도
        포함될 수 있음. 코너 추출은 z 최저/최고 레벨 기준으로 한정하지 말고,
        bbox xy 양 끝 + z 최저/최고로 4 코너만 선별.
    """
    am = om.analysis_model
    if am is None or not getattr(am, 'comp_to_nodes', None):
        return {}
    wall_has_bot: Dict[int, bool] = {}
    # role 인덱스로 wall_ 시작만 추림.
    for mid in am.members_by_role_prefix('wall_'):
        m = am.members[mid]
        role = m.role or ''
        if not m.source_comp_ids:
            continue
        cid = m.source_comp_ids[0]
        if cid not in wall_has_bot:
            wall_has_bot[cid] = False
        if role == 'wall_bottom_runner':
            wall_has_bot[cid] = True

    out: Dict[int, Dict] = {}
    for cid, has_bot in wall_has_bot.items():
        is_merged = not has_bot
        nids_raw = am.comp_to_nodes.get(cid, [])
        nids = [n for n in nids_raw if n in om.node_tags]
        if not nids:
            continue
        zs = [om.node_tags[n][2] for n in nids]
        z_min, z_max = float(min(zs)), float(max(zs))
        top = [n for n in nids if abs(om.node_tags[n][2] - z_max) <= _Z_TOL]
        bot = [n for n in nids if abs(om.node_tags[n][2] - z_min) <= _Z_TOL]
        out[cid] = {
            'top': top, 'bot': bot, 'all': list(nids),
            'is_merged': is_merged,
            'z_top': z_max, 'z_bot': z_min,
        }
    # [진단] 벽 자료 출력.
    _dbg = [f"cid{cid}({'M' if d['is_merged'] else 'I'}/"
            f"top{len(d['top'])}@{d['z_top']:.0f}/bot{len(d['bot'])}@{d['z_bot']:.0f})"
            for cid, d in sorted(out.items())]
    return out



def _collect_floor_panel_corners(om) -> Dict[int, List[int]]:
    """바닥패널만의 코너 → {cid: [4 nid]}.

    R03 의 _collect_panel_like_corners 는 캔틸 슬래브도 포함하므로, R05 의 벽-바닥
    수직 접합 후보로는 캔틸 슬래브 제외. role 'floor_edge_beam' 부재만 가진 cid.
    """
    am = om.analysis_model
    if am is None or not getattr(am, 'comp_to_nodes', None):
        return {}
    fp_cids: Set[int] = set()
    for mid in am.members_by_role('floor_edge_beam'):
        m = am.members[mid]
        if m.source_comp_ids:
            fp_cids.add(m.source_comp_ids[0])
    out: Dict[int, List[int]] = {}
    for cid in fp_cids:
        nids = [n for n in am.comp_to_nodes.get(cid, [])
                if n in om.node_tags]
        if nids:
            out[cid] = nids
    return out


# ── R05 — 벽 ↔ 바닥패널 수직 접합 ─────────────────────────────

RULE_ID_WALL_FLOOR_V = 'R05_wall_floor_v'


def apply_wall_floor_vertical(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
) -> int:
    """R05 — 벽패널 코너 ↔ 바닥패널 코너 수직 핀 접합.

    [정책]
      - 종속(합체) 벽: 하단 코너는 이미 FP nid 와 토폴로지 통합돼 있어 추가 결합
        불필요. **상단 코너**(다음 층 바닥과의 z 갭) 만 후보.
      - 비종속 벽: **하단·상단 코너 모두** 후보.
          - 하단: 같은 층 바닥과 z 갭 ≈ 200mm
          - 상단: 다음 층 바닥과 z 갭 ≈ 80~120mm
      - 매칭: xy 정렬(≤_PERP_TOL) + z 갭 ∈ (_PERP_TOL, _GAP_MAX].
      - z 작은 쪽 = master (R02 vstack 정책 동일).
      - Penalty handler 의존 — 충돌 검사 없이 강제 등록.
    """
    walls = _collect_wall_data(om)
    if not walls:
        return 0
    floors = _collect_floor_panel_corners(om)
    if not floors:
        return 0
    from modular_3d.analysis.model_spec import EqualDofRec
    registered = 0
    seen: Set[Tuple[int, int]] = set()
    for wcid, wdat in walls.items():
        cand = list(wdat['top'])
        if not wdat['is_merged']:
            cand += list(wdat['bot'])
        for nW in cand:
            cW = om.node_tags[nW]
            for fcid, fnids in floors.items():
                for nF in fnids:
                    if nW == nF:
                        continue   # 이미 통합된 같은 노드
                    cF = om.node_tags[nF]
                    if abs(cW[0] - cF[0]) > _PERP_TOL:
                        continue
                    if abs(cW[1] - cF[1]) > _PERP_TOL:
                        continue
                    dz = abs(cW[2] - cF[2])
                    if not (_PERP_TOL < dz <= _GAP_MAX):
                        continue
                    master, slave = (nW, nF) if cW[2] < cF[2] else (nF, nW)
                    if (master, slave) in seen:
                        continue
                    seen.add((master, slave))
                    _dofs = _resolve_override_dofs(om, master, slave, dofs,
                                                   RULE_ID_WALL_FLOOR_V)
                    if _dofs is None:
                        continue   # remove 오버라이드 — 이 결합 건너뜀
                    try:
                        ops.equalDOF(master, slave, *_dofs)
                    except Exception as e:
                        dprint('joint_rules', f'[joint_rules][R05] equalDOF({master},{slave}) 실패: {e}')
                        om.registration_failures.append(
                            (slave, f'R05 equalDOF master={master}: {e}'))
                        continue
                    om.constrained_node_ids.add(slave)
                    if om.spec is not None:
                        om.spec.equal_dofs.append(EqualDofRec(
                            master=master, slave=slave, dofs=tuple(_dofs),
                            kind='wall_floor_vstack',
                            rule_id=RULE_ID_WALL_FLOOR_V,
                        ))
                    registered += 1
    if registered:
        pass
    return registered


# ── R06 — 벽 ↔ 모듈 (캔틸 보 자유단 포함) ─────────────────────

RULE_ID_WALL_MOD = 'R06_wall_mod'


def apply_wall_module(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
    edge_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray, str]]]] = None,
) -> int:
    """R06 — 벽 상단 코너 ↔ 모듈(천장보·기둥) + 캔틸 보 자유단. 같은-z 만.

    [정책 2026-05-17 — 정정]
      - **같은 z(±_PERP_TOL) 매칭만** 수행. 직각(z 갭) 매칭은 R05 (벽-바닥)
        가 담당하므로 R06 에서 시도하지 않는다. 사용자 사양: "벽-모듈은
        같은 레벨에 있을 때만 모듈-모듈처럼 접합". 윗층 바닥과의 z 갭 결합은
        벽-바닥 R05 가 별도로 처리하므로 R06 에 z 갭 라우팅을 두면 같은-z
        매칭이 통째로 누락되는 부작용(rect_found=True 면 같은-z 패스 skip)
        만 남는다.
      - **벽 후보 코너 = 상단 2 코너만**. 종속 벽이라도 하단은 이미 FP 코너
        nid 와 통합돼 R03(패널-모듈) 이 동일 결합을 처리하므로, R06 에서
        하단 후보로 잡으면 같은 master/slave 가 R03·R06 양쪽에서 중복 등록.
        종속/비종속 무관, 상단 2 코너로 통일.
      - 매칭은 R01 (a)(b) 와 동일 형식:
          (a) 모듈 코너: 한 축 ≤ _PERP_TOL + 다른 축 ≤ _GAP_MAX (같은 z)
          (b) 모듈 수평 보: 보 직각 사영, perp ≤ _EDGE_PERP_MAX (같은 z)
      - (a) 우선 — (a) 매칭된 모듈은 (b) 스킵(모듈 단위 양보).
      - 한 모듈당 1쌍. 여러 모듈 동시 닿으면 각 모듈당 1쌍.
      - 캔틸 보 자유단은 _collect_cantilever_beam_data 로 모듈 자료에 합쳐 처리.
      - 같은 nid 페어(자동 통합)는 nM==nP 로 자연 차단.

    Returns: 등록된 equalDOF 쌍 수.
    """
    walls = _collect_wall_data(om)
    if not walls:
        return 0
    mod_nids = _collect_module_nodes(om)
    if not mod_nids:
        return 0

    # 벽 후보: 상단 2 코너만 (종속·비종속 무관).
    wall_masters: Dict[int, List[int]] = {
        wcid: list(wdat['top']) for wcid, wdat in walls.items()
    }

    # [2026-05-17 분리] R06 은 순수 벽-모듈만 처리. 캔틸 보-벽 결합은
    # 별도 룰 R08(apply_wall_cantilever_beam) 가 담당.
    mod_corners: Dict[int, List[int]] = {}
    mod_edges: Dict[int, List[Tuple[int, int, int]]] = {}
    mod_vedges: Dict[int, List[Tuple[int, int, int]]] = {}
    for cid, nids in mod_nids.items():
        c = _module_corners(om, nids)
        if not c:
            continue
        mod_corners[cid] = c
        mod_edges[cid] = _horizontal_edges(
            om, c, lambda r: r.startswith('module'),
            allowed_nodes=set(nids))
        # 수직모듈 통기둥 분절 — 단층 모듈은 빈 리스트.
        mod_vedges[cid] = _module_vertical_edges(
            om, c, allowed_nodes=set(nids))
    if not mod_corners:
        return 0

    # [성능 2026-06-05] (a)코너·(b)수평보·(c)수직기둥 후보를 격자로 제한.
    # 벽 상단 코너 nP 칸 후보 모듈만 본다(순회 순서·선택 동일 → 결과 보존).
    mod_grid = _build_corner_grid(om, mod_corners)
    mod_edge_grid = _build_edge_grid(om, mod_edges)
    mod_vedge_grid = _build_edge_grid(om, mod_vedges)

    from modular_3d.analysis.model_spec import EqualDofRec

    registered = 0
    if edge_split_map is None:
        edge_split_map = {}

    def _link(master: int, slave: int, kind: str) -> None:
        nonlocal registered
        _dofs = _resolve_override_dofs(om, master, slave, dofs, RULE_ID_WALL_MOD)
        if _dofs is None:
            return   # remove 오버라이드 — 이 결합 건너뜀
        try:
            ops.equalDOF(master, slave, *_dofs)
        except Exception as e:
            dprint('joint_rules', f'[joint_rules][R06] equalDOF({master},{slave}) 실패: {e}')
            om.registration_failures.append(
                (slave, f'R06 equalDOF master={master}: {e}'))
            return
        om.constrained_node_ids.add(slave)
        if om.spec is not None:
            om.spec.equal_dofs.append(EqualDofRec(
                master=master, slave=slave, dofs=tuple(_dofs),
                kind=kind, rule_id=RULE_ID_WALL_MOD))
        registered += 1

    for wcid, wnids in wall_masters.items():
        for nP in wnids:
            cP = om.node_tags[nP]
            matched_modules: Set[int] = set()
            cand_c = _neighbor_cids(mod_grid, cP, -1)
            cand_e = _edge_cell_cids(mod_edge_grid, cP)
            cand_v = _edge_cell_cids(mod_vedge_grid, cP)

            # (a) 벽 상단 코너 ↔ 모듈/캔틸 코너 (같은 z 만)
            for mcid, tc in mod_corners.items():
                if mcid in matched_modules or mcid not in cand_c:
                    continue
                best: Optional[Tuple[float, int]] = None
                for nM in tc:
                    if nM == nP:
                        continue
                    cM = om.node_tags[nM]
                    dx = abs(cP[0] - cM[0])
                    dy = abs(cP[1] - cM[1])
                    dz = abs(cP[2] - cM[2])
                    if dz > _PERP_TOL:
                        continue
                    cond_x = dx <= _PERP_TOL and dy <= _GAP_MAX
                    cond_y = dy <= _PERP_TOL and dx <= _GAP_MAX
                    if not (cond_x or cond_y):
                        continue
                    gap = dy if cond_x else dx
                    if best is None or gap < best[0]:
                        best = (gap, nM)
                if best is not None:
                    nM = best[1]
                    _link(nP, nM, 'wall_module_corner')
                    matched_modules.add(mcid)

            # (b) 벽 상단 코너 ↔ 모듈/캔틸 수평 보 사영 (같은 z 만)
            for mcid, tedges in mod_edges.items():
                if mcid in matched_modules or mcid not in cand_e:
                    continue
                best_b: Optional[Tuple[float, int, np.ndarray]] = None
                for mid, nA, nB in tedges:
                    cA = om.node_tags[nA]
                    cB = om.node_tags[nB]
                    dz = abs(cP[2] - cA[2])
                    if dz > _PERP_TOL:
                        continue
                    AB = cB - cA
                    AP = cP - cA
                    L2 = float(np.dot(AB[:2], AB[:2]))
                    if L2 < 1e-6:
                        continue
                    t = float(np.dot(AP[:2], AB[:2])) / L2
                    t_min = _PERP_TOL / np.sqrt(L2)
                    if not (t_min < t < 1.0 - t_min):
                        continue
                    proj = cA + AB * t
                    perp = float(np.linalg.norm((cP - proj)[:2]))
                    if perp > _EDGE_PERP_MAX:
                        continue
                    if best_b is None or perp < best_b[0]:
                        best_b = (perp, mid, proj)
                if best_b is not None:
                    _perp, mid, proj = best_b
                    edge_split_map.setdefault(mid, []).append(
                        (nP, proj, RULE_ID_WALL_MOD))
                    matched_modules.add(mcid)

            # (c) 벽 상단 코너 ↔ 수직모듈 기둥 분절 사영. z 방향 모서리이므로
            # 코너 z 가 분절 z 범위 안쪽 + xy 거리 ≤ _EDGE_PERP_MAX.
            # [2026-05-17] 사양 "벽-수직모듈 = 벽-모듈" — 통기둥 중간 z 에 벽
            # 상단이 닿으면 그 점에서 분절 분할 + 결합. 단층 모듈만 쓰면
            # mod_vedges 가 비어 자연 통과.
            for mcid, vedge_list in mod_vedges.items():
                if mcid in matched_modules or mcid not in cand_v:
                    continue
                for mid, nA, nB in vedge_list:
                    cA = om.node_tags[nA]
                    cB = om.node_tags[nB]
                    zlo, zhi = ((cA[2], cB[2]) if cA[2] < cB[2]
                                else (cB[2], cA[2]))
                    if not (zlo + _PERP_TOL < cP[2] < zhi - _PERP_TOL):
                        continue
                    perp = float(np.hypot(cP[0] - cA[0], cP[1] - cA[1]))
                    if perp > _EDGE_PERP_MAX:
                        continue
                    proj = np.array([cA[0], cA[1], cP[2]], dtype=float)
                    edge_split_map.setdefault(mid, []).append(
                        (nP, proj, RULE_ID_WALL_MOD))
                    matched_modules.add(mcid)
                    break   # 한 모듈당 1쌍

    if registered:
        pass
    return registered


# ── R08 — 벽 상단 코너 ↔ 캔틸레버 보 자유단 ──────────────────

RULE_ID_WALL_CANT = 'R08_wall_cant'


def apply_wall_cantilever_beam(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
    edge_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray, str]]]] = None,
) -> int:
    """R08 — 벽 상단 코너 ↔ 캔틸 보 자유단/모서리 (같은 z 만).

    [정책]
      사용자 사양 "캔틸레버보의 자유단이 벽패널의 보나 기둥 상단(코너부)에
      접합. 캔틸레버보는 3방향으로 열려있으므로 최대 3개까지 동시 접합 가능."
      - 벽 후보 = 상단 2 코너 (종속·비종속 무관). 캔틸 자유단 z 가 벽 상단 z
        와 같은 레벨 — 사용자가 "기둥의 상부 코너 레벨은 종속이든 종속안 되든
        같다" 명시.
      - 매칭: 같은 z(±_PERP_TOL) + xy 한 축 정렬(≤_PERP_TOL) + 다른 축
        ≤_GAP_MAX (R01/R06 (a) 동일 형식). 보 모서리 사영(b) 도 같은 형식.
      - 한 캔틸 cid 당 1쌍. 캔틸 보는 cid 별로 따로 셈해지므로 세 다른 캔틸
        보가 한 벽 코너에 동시 닿으면 3쌍 자연 등록.

    [구현]
      mod_corners/mod_edges 자리에 캔틸 자유단/모서리만 넣어 R06 와 동일한
      (a)(b) 매칭 로직 사용. R06 의 vedges 처리는 캔틸엔 수직 모서리 없으므로
      생략.
    """
    walls = _collect_wall_data(om)
    if not walls:
        return 0
    cants_corners, cants_edges = _collect_cantilever_beam_data(om)
    if not cants_corners:
        return 0

    wall_masters: Dict[int, List[int]] = {
        wcid: list(wdat['top']) for wcid, wdat in walls.items()
    }

    from modular_3d.analysis.model_spec import EqualDofRec
    registered = 0
    if edge_split_map is None:
        edge_split_map = {}

    def _link(master: int, slave: int, kind: str) -> None:
        nonlocal registered
        _dofs = _resolve_override_dofs(om, master, slave, dofs, RULE_ID_WALL_CANT)
        if _dofs is None:
            return   # remove 오버라이드 — 이 결합 건너뜀
        try:
            ops.equalDOF(master, slave, *_dofs)
        except Exception as e:
            dprint('joint_rules', f'[joint_rules][R08] equalDOF({master},{slave}) 실패: {e}')
            om.registration_failures.append(
                (slave, f'R08 equalDOF master={master}: {e}'))
            return
        om.constrained_node_ids.add(slave)
        if om.spec is not None:
            om.spec.equal_dofs.append(EqualDofRec(
                master=master, slave=slave, dofs=tuple(_dofs),
                kind=kind, rule_id=RULE_ID_WALL_CANT))
        registered += 1

    for wcid, wnids in wall_masters.items():
        for nP in wnids:
            cP = om.node_tags[nP]
            matched_cants: Set[int] = set()
            # (a) 벽 상단 코너 ↔ 캔틸 보 자유단 (같은 z)
            for ccid, free in cants_corners.items():
                if ccid in matched_cants:
                    continue
                best: Optional[Tuple[float, int]] = None
                for nC in free:
                    if nC == nP:
                        continue
                    cC = om.node_tags[nC]
                    dx = abs(cP[0] - cC[0])
                    dy = abs(cP[1] - cC[1])
                    dz = abs(cP[2] - cC[2])
                    if dz > _PERP_TOL:
                        continue
                    cond_x = dx <= _PERP_TOL and dy <= _GAP_MAX
                    cond_y = dy <= _PERP_TOL and dx <= _GAP_MAX
                    if not (cond_x or cond_y):
                        continue
                    gap = dy if cond_x else dx
                    if best is None or gap < best[0]:
                        best = (gap, nC)
                if best is not None:
                    _link(nP, best[1], 'wall_cant_corner')
                    matched_cants.add(ccid)

            # (b) 벽 상단 코너 ↔ 캔틸 보 모서리 사영 (같은 z)
            for ccid, edge_list in cants_edges.items():
                if ccid in matched_cants:
                    continue
                best_b: Optional[Tuple[float, int, np.ndarray]] = None
                for mid, nA, nB in edge_list:
                    cA = om.node_tags[nA]
                    cB = om.node_tags[nB]
                    if abs(cP[2] - cA[2]) > _PERP_TOL:
                        continue
                    AB = cB - cA
                    AP = cP - cA
                    L2 = float(np.dot(AB[:2], AB[:2]))
                    if L2 < 1e-6:
                        continue
                    t = float(np.dot(AP[:2], AB[:2])) / L2
                    t_min = _PERP_TOL / np.sqrt(L2)
                    if not (t_min < t < 1.0 - t_min):
                        continue
                    proj = cA + AB * t
                    perp = float(np.linalg.norm((cP - proj)[:2]))
                    if perp > _EDGE_PERP_MAX:
                        continue
                    if best_b is None or perp < best_b[0]:
                        best_b = (perp, mid, proj)
                if best_b is not None:
                    _perp, mid, proj = best_b
                    edge_split_map.setdefault(mid, []).append(
                        (nP, proj, RULE_ID_WALL_CANT))
                    matched_cants.add(ccid)

    if registered:
        pass
    return registered


# ── R07 — 벽 ↔ 벽 수평 접합 ──────────────────────────────────

RULE_ID_WALL_WALL_H = 'R07_wall_wall_h'


def apply_wall_wall_horizontal(
    om,
    dofs: Tuple[int, ...] = (1, 2, 3),
    edge_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray, str]]]] = None,
) -> int:
    """R07 — 벽 코너 ↔ 다른 벽 코너. 코너-코너만(노드끼리).

    [정책 2026-05-17 — 사양 정정]
      사용자 사양 "벽패널-벽패널: 모듈과 모듈 접합처럼 노드끼리만. 코너에서
      코너 접합. 벽 코너에서 다른 벽 기둥 중간에 접합하는 일은 없음." 에
      따라 (b) 수평 모서리 매칭과 (c) 수직 기둥 모서리 매칭을 모두 차단.
      코너-코너 (a) 매칭만 유지.

      - corners = 상단·하단 z 레벨의 노드만 (분할 mid 노드 제외).
      - edges/vedges = 빈 리스트 → _apply_corner_edge_rule 의 (b)·(c) 분기는
        자연 통과(빈 dict 순회).
      - 자동 병합으로 같은 nid 가 된 페어는 (nP == nM) 조건으로 자연 차단.
      - z 다른 두 벽 코너는 (a) 매칭의 dz ≤ _Z_TOL 검사로 자연 거부.

    [회귀 안전]
      이전에는 vedges 가 wall_column 분절을 모서리로 잡아, 다른 벽 코너가
      벽 기둥 직선 위에 사영되면 결합을 등록했음. 사용자 사양 위반이라
      제거. mid_beam/mid_column 으로 wall_column 이 분할된 케이스에도
      mid 노드는 corners 에서 제외되고 vedges 도 비어 있어 추가 결합 X.
    """
    walls = _collect_wall_data(om)
    if len(walls) < 2:
        return 0
    corners: Dict[int, List[int]] = {}
    edges: Dict[int, List[Tuple[int, int, int]]] = {}
    vedges: Dict[int, List[Tuple[int, int, int]]] = {}
    for wcid, wdat in walls.items():
        # 코너 = z 양 끝 레벨 노드만 (분할 mid 노드 제외).
        corners[wcid] = list(wdat['top']) + list(wdat['bot'])
        edges[wcid] = []   # (b) 코너-수평모서리 매칭 차단
        vedges[wcid] = []  # (c) 코너-수직모서리 매칭 차단
    if edge_split_map is None:
        edge_split_map = {}
    return _apply_corner_edge_rule(
        om, dofs, corners, edges, vedges,
        RULE_ID_WALL_WALL_H, edge_split_map)


# ── R09 — 코어벽·코어슬래브 ↔ 외부 부재 노드 ──────────────────

RULE_ID_CORE = 'R09_core'

# 코어 거리 임계 — 사용자 사양 (2026-05-17 수정 / 2026-06-05 코어 갭 100 분리).
# 결합 방향(예: y 평행 결합): dy ≤ (코어벽 절반두께+부재 절반+코어갭+마진) = 355mm.
# 정렬 방향(x): dx ≤ (코어벽 절반두께-부재 절반+마진) = 55mm. (갭 무관 — 코어 면을
# 따라가는 방향이라 갭이 늘어도 그대로.) (x ↔ y 반대 케이스도 동일.)
# [함정] 결합 거리 갭 항은 CORE_JOINT_GAP_MM(코어 전용, 100) 을 쓴다. 배치 자동 갭
# (auto_snap.gap_between)의 코어↔타부재 값과 반드시 같은 상수여야 한다 — 어긋나면
# 부재가 임계 밖으로 밀려 코어 결합이 끊긴다.
_CORE_NUMERIC_MARGIN_MM = 5.0
_CORE_LATERAL_MAX = (
    CORE_WALL_DEFAULT_THICKNESS_MM / 2.0
    + SECTION_W_MM / 2.0
    + CORE_JOINT_GAP_MM
    + _CORE_NUMERIC_MARGIN_MM
)  # 기본 = 355mm (코어 갭 100)
_CORE_ALIGN_TOL = (
    CORE_WALL_DEFAULT_THICKNESS_MM / 2.0
    - SECTION_W_MM / 2.0
    + _CORE_NUMERIC_MARGIN_MM
)  # 기본 = 55mm

# 코어 가로/세로 선의 role 집합 (X 대각 제외).
_CORE_LINE_ROLES_AXIS = (
    'core_column', 'core_top_runner', 'core_bottom_runner',
    'core_ceiling_runner',   # [2026-06-03] 천장보 레벨 코어 수평선(천장 접합 사영용)
    'core_slab_beam',
    # (트러스 격자 폐기로 core_truss_v/h 제거됨)
)


def _collect_core_data(om):
    """코어 컴포넌트의 노드 + 축평행 선 → (core_nids, core_lines).

    core_nids: List[int] — 코어벽·코어슬래브에 속한 노드 ID 모두.
    core_lines: List[(mid, n1, n2, axis)] — axis ∈ {'x','y','z'}.
        선이 축에 평행(다른 두 축 차이 ≤ _Z_TOL) 한 부재만. 대각 제외.
    """
    am = om.analysis_model
    if am is None:
        return set(), []
    core_nids: Set[int] = set()
    core_lines: List[Tuple[int, int, int, str]] = []
    # role 인덱스 — _CORE_LINE_ROLES_AXIS 각 role 별 캐시 합집합.
    candidate_mids: List[int] = []
    for role in _CORE_LINE_ROLES_AXIS:
        candidate_mids.extend(am.members_by_role(role))
    for mid in candidate_mids:
        m = am.members[mid]
        c1 = om.node_tags.get(m.n1)
        c2 = om.node_tags.get(m.n2)
        if c1 is None or c2 is None:
            continue
        core_nids.add(m.n1)
        core_nids.add(m.n2)
        dx = abs(c1[0] - c2[0])
        dy = abs(c1[1] - c2[1])
        dz = abs(c1[2] - c2[2])
        # 어느 축에 평행?
        if dx > _Z_TOL and dy <= _Z_TOL and dz <= _Z_TOL:
            axis = 'x'
        elif dy > _Z_TOL and dx <= _Z_TOL and dz <= _Z_TOL:
            axis = 'y'
        elif dz > _Z_TOL and dx <= _Z_TOL and dy <= _Z_TOL:
            axis = 'z'
        else:
            continue   # 대각·기울어진 부재 제외
        core_lines.append((mid, m.n1, m.n2, axis))

    # [2026-06-24 MVLEM] 코어벽이 막대(core_column) 대신 AnalysisModel.walls 4노드로
    #   바뀌었다 → walls 노드도 코어측으로 수집(직접 매칭용). 노드는 중심선(half_t)이라
    #   기존 wide-column 과 같은 위치 → R09 거리 임계(_CORE_LATERAL_MAX) 그대로 유효
    #   (offset 보정 불필요). walls 변 선사영은 MVLEM 4노드가 중간분할 불가라 제외 —
    #   코너 직접매칭으로 충분한지 검증, 부족 시 P3b-2 에서 보강.
    for w in getattr(am, 'walls', {}).values():
        for nid in (w.n_bl, w.n_br, w.n_tr, w.n_tl):
            if om.node_tags.get(nid) is not None:
                core_nids.add(nid)
    return core_nids, core_lines


def apply_core_joint(
    om,
    dofs_6: Tuple[int, ...] = (1, 2, 3, 4, 5, 6),
    edge_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray, str]]]] = None,
    core_split_map: Optional[Dict[int, List[Tuple[int, np.ndarray]]]] = None,
) -> int:
    """R09 — 외부 부재 노드 ↔ 코어벽·코어슬래브 (노드 + 축평행 선) 6 DOF 강결합.

    [정책 — 사용자 사양 2026-05-17]
      - 외부 부재(모듈/패널/벽/캔틸 + 모든 컴포넌트) 의 **모든 노드** 가 결합 후보.
      - 코어 측 결합 대상:
          · 노드: 코어벽 외곽·격자 노드 + 코어슬래브 노드 모두.
          · 선: 코어 가로·세로 선 (X 대각 제외). x·y·z 축에 평행한 부재만.
      - 매칭 조건 (수평 결합만):
          · z 정렬 ≤ _Z_TOL
          · (x 정렬 ≤ _Z_TOL AND y 거리 ≤ _CORE_LATERAL_MAX) 또는 반대
      - master/slave: 외부 노드 = master, 코어 측 = slave (R03/R06 와 일관).
      - 결합 DOF: 6 (병진+회전 강결합).
      - 사영점 처리: 코어 선 위 사영점에 새 노드 신설 + 선 분할 + 결합.
        사영점은 edge_split_map 에 누적되어 _split_edges_and_link_corners 가
        일괄 처리.

    [축 평행 선의 사영 분기]
      코어 선이 z 평행(column): Q' = (선.x, 선.y, P.z). 선 z 범위 안.
      코어 선이 x 평행(수평보): Q' = (P.x, 선.y, 선.z). 선 x 범위 안.
      코어 선이 y 평행(수평보): Q' = (선.x, P.y, 선.z). 선 y 범위 안.
      각 분기에서 결합선이 x 또는 y 평행인지 추가 검사.
    """
    am = om.analysis_model
    if am is None:
        return 0
    core_nids, core_lines = _collect_core_data(om)
    if not core_nids and not core_lines:
        return 0
    # 외부 노드 = 모든 노드 중 코어 노드 아닌 것.
    external_nids: List[int] = [
        nid for nid in om.node_tags.keys() if nid not in core_nids
    ]
    if not external_nids:
        return 0
    if edge_split_map is None:
        edge_split_map = {}
    if core_split_map is None:
        core_split_map = {}

    from modular_3d.analysis.model_spec import EqualDofRec
    registered = 0

    def _link(master: int, slave: int, kind: str) -> None:
        nonlocal registered
        if master == slave:
            return
        _dofs = _resolve_override_dofs(om, master, slave, dofs_6, RULE_ID_CORE)
        if _dofs is None:
            return   # remove 오버라이드 — 이 결합 건너뜀
        try:
            ops.equalDOF(master, slave, *_dofs)
        except Exception as e:
            dprint('joint_rules', f'[joint_rules][R09] equalDOF({master},{slave}) 실패: {e}')
            om.registration_failures.append(
                (slave, f'R09 equalDOF master={master}: {e}'))
            return
        om.constrained_node_ids.add(slave)
        if om.spec is not None:
            om.spec.equal_dofs.append(EqualDofRec(
                master=master, slave=slave, dofs=tuple(_dofs),
                kind=kind, rule_id=RULE_ID_CORE))
        registered += 1

    # [정책 2026-06-03 사용자] 한 외부 노드는 같은 축(x/y) 방향으로 R09 결합을
    # 최대 1개만 가진다 — x 1개 + y 1개는 허용, x 2개·y 2개는 금지. 같은 축에
    # 후보가 여럿이면 결합선이 가장 짧은(가장 가까운 코어 부위) 1개만 채택한다.
    # 결합선 축 = 결합선 벡터(외부노드→코어/사영점)의 큰 성분(x/y).
    # (a) 코어 노드 직접 매칭과 (b) 코어 선 사영 후보를 모두 모아 거리로 경쟁시킴.
    #   best[(nP, axis)] = (dist, kind, payload)
    #     kind='node' → payload=nQ (직접 결합) / kind='edge' → payload=(mid, proj)
    best: Dict[Tuple[int, str], Tuple[float, str, object]] = {}

    def _consider(nP_: int, axis_c: str, dist: float, kind: str, payload) -> None:
        key = (nP_, axis_c)
        cur = best.get(key)
        if cur is None or dist < cur[0]:
            best[key] = (dist, kind, payload)

    # (a) 외부 노드 ↔ 코어 노드 직접 매칭 후보.
    # [성능] z 정렬(≤_Z_TOL=5)만 결합되므로, 코어 노드를 z 버킷(정수 mm)으로
    # 색인해 같은 높이 후보만 본다. 외부노드×코어 전수 O(N^2) → O(N). z 레벨
    # 간격(층 3420) ≫ _Z_TOL 이라 버킷이 층을 정확히 가른다. 결과 동일.
    from collections import defaultdict as _dd
    _dz = int(_Z_TOL) + 2
    _core_by_z = _dd(list)
    for _nQ in core_nids:
        _cQ = om.node_tags[_nQ]
        _core_by_z[round(_cQ[2])].append((_nQ, _cQ))
    for nP in external_nids:
        cP = om.node_tags[nP]
        _zc = round(cP[2])
        for _zk in range(_zc - _dz, _zc + _dz + 1):
            for nQ, cQ in _core_by_z.get(_zk, ()):
                if abs(cP[2] - cQ[2]) > _Z_TOL:
                    continue
                dx = abs(cP[0] - cQ[0])
                dy = abs(cP[1] - cQ[1])
                # 정렬 임계 _CORE_ALIGN_TOL(=55mm), 결합 거리 _CORE_LATERAL_MAX(=275mm).
                cond_x = dx <= _CORE_ALIGN_TOL and (_Z_TOL < dy <= _CORE_LATERAL_MAX)
                cond_y = dy <= _CORE_ALIGN_TOL and (_Z_TOL < dx <= _CORE_LATERAL_MAX)
                if not (cond_x or cond_y):
                    continue
                # 결합선 축 = 큰 성분 (cond_x→dy 큼→'y' / cond_y→dx 큼→'x')
                axis_c = 'x' if dx > dy else 'y'
                _consider(nP, axis_c, float(np.hypot(dx, dy)), 'node', nQ)

    # (b) 외부 노드 ↔ 코어 축평행 선 사영점 후보.
    # [성능] 선의 z(c1[2]) 버킷으로 같은 높이만 비교(O(N)). axis=='z'(코어 column
    # 사영)는 정책상 비활성이라 색인에서 아예 제외. 결과 동일.
    _lines_by_z = _dd(list)
    for (mid, n1, n2, axis) in core_lines:
        if axis == 'z':
            # [2026-05-28 사용자 정책] 코어 column (z 평행) 사영 비활성 —
            # n1.z ≠ 사영점.z 라 시공에 없는 수직 결합(모델링 인공물)이 됨.
            continue
        _lines_by_z[round(om.node_tags[n1][2])].append((mid, n1, n2, axis))
    for nP in external_nids:
        cP = om.node_tags[nP]
        _zc = round(cP[2])
        for _zk in range(_zc - _dz, _zc + _dz + 1):
            for mid, n1, n2, axis in _lines_by_z.get(_zk, ()):
                c1 = om.node_tags[n1]
                c2 = om.node_tags[n2]
                if axis == 'x':
                    # 수평보 (x 평행). 사영점 = (P.x, 선.y, 선.z), 결합선은 y 방향.
                    if abs(cP[2] - c1[2]) > _Z_TOL:
                        continue
                    x_lo = min(c1[0], c2[0])
                    x_hi = max(c1[0], c2[0])
                    if not (x_lo + _Z_TOL < cP[0] < x_hi - _Z_TOL):
                        continue
                    dy = abs(cP[1] - c1[1])
                    if not (_Z_TOL < dy <= _CORE_LATERAL_MAX):
                        continue
                    proj = np.array([cP[0], c1[1], c1[2]], dtype=float)
                    axis_c, dist = 'y', dy
                else:   # axis == 'y' — 결합선은 x 방향.
                    if abs(cP[2] - c1[2]) > _Z_TOL:
                        continue
                    y_lo = min(c1[1], c2[1])
                    y_hi = max(c1[1], c2[1])
                    if not (y_lo + _Z_TOL < cP[1] < y_hi - _Z_TOL):
                        continue
                    dx = abs(cP[0] - c1[0])
                    if not (_Z_TOL < dx <= _CORE_LATERAL_MAX):
                        continue
                    proj = np.array([c1[0], cP[1], c1[2]], dtype=float)
                    axis_c, dist = 'x', dx
                _consider(nP, axis_c, float(dist), 'edge', (mid, proj))

    # 채택 — 외부노드별·축별 최소거리 후보만 등록.
    # [함정 — 옛 단순화의 버그] (b) 사영 결합은 과거엔 사영점에 새 노드를 만들어
    # 코어 선의 첫 끝점에 6DOF 강결합했다(결합선이 선 길이만큼 길어지는 버그).
    # 지금은 core_split_map 에 누적만 하고 _split_core_lines 가 선을 사영점에서
    # 실제 분할 + 외부노드와 짧게(≤275mm) 결합한다(R01~R08 분할 방식과 동일).
    for (nP, axis_c), (dist, kind, payload) in best.items():
        if kind == 'node':
            # [2026-06-03] 코어=master(retained), 외부=slave(constrained)로 반전.
            # 코어 노드를 전부 6DOF 고정하므로 코어가 slave 면 과구속 충돌 → 코어를
            # master 로 둬야 fix 와 호환(외부가 고정 코어를 따라 구속).
            _link(payload, nP, 'core_node')
        else:
            mid, proj = payload
            core_split_map.setdefault(mid, []).append((nP, proj))

    if registered:
        pass
    return registered


def _split_core_lines(
    om,
    core_split_map: Dict[int, List[Tuple[int, np.ndarray]]],
    dofs_6: Tuple[int, ...],
) -> int:
    """R09 보조 — 코어 수평선을 외부 부재 사영점에서 분할 + 외부노드 6DOF 결합.

    `_split_edges_and_link_corners`(R01~R08 강재 보 전용) 와 골격은 같으나 코어는
    truss(core_truss_h) 와 콘크리트 보(runner/slab_beam) 가 섞여 있어 sub-element
    등록을 부재 종류별로 분기하고, 결합을 핀이 아닌 6 DOF 로 등록한다.

    [CoT] mid 별 처리:
      1. 사영점들을 선 방향 t 로 정렬 + ±5mm 중복 묶기 (선 끝 근처는 (a) 직접
         매칭 영역이라 제외).
      2. ops.remove 로 기존 코어 선 element 제거 + om/spec 정리.
      3. 각 사영점에 새 노드 Q 등록.
      4. prev→Q sub-element 등록 (truss→Truss+9001 / 콘크리트→elasticBeamColumn+RC).
      5. 외부노드 nP ↔ Q 6 DOF equalDOF (kind='core_edge').
      6. 마지막 prev→n2 sub-element.
      7. member_to_split 기록 (자중·물량 후처리용 — ops_solver 가 truss sub 는
         노드하중으로 자동 분기).

    Returns: 등록된 nP↔Q 결합 수.
    """
    if not core_split_map:
        return 0
    am = om.analysis_model
    if am is None:
        return 0

    from modular_3d.analysis.ops_builder import (
        _section_props, _vecxz_for_member, _geom_transf_tag,
        RC_WALL_E_MPA, RC_WALL_G_MPA,
    )
    from modular_3d.analysis.constants import STEEL_E_MPA, STEEL_G_MPA
    from modular_3d.analysis.topology import AnalysisNode, AnalysisMember
    from modular_3d.analysis.model_spec import NodeRec, BeamRec, EqualDofRec

    # truss material tag 는 ops_builder._step_register_members 가 9001 로 등록 —
    # R09 는 그 단계 뒤에 호출되므로 ops 에 살아있다. (함정: 값 바뀌면 동기화 필요)
    TRUSS_MAT_TAG = 9001
    RC_BEAM_ROLES = ('core_column', 'core_slab_beam',
                     'core_top_runner', 'core_bottom_runner')

    next_node = (max(om.node_tags.keys()) + SPLIT_NODE_BASE_OFFSET
                 if om.node_tags else SPLIT_NODE_BASE_OFFSET)
    next_ele = max(om.beam_elements.keys()) + 1 if om.beam_elements else 1
    next_sub_mid = max(am.members.keys()) + 1 if am.members else 1
    registered = 0

    for mid, plist in core_split_map.items():
        m = am.members.get(mid)
        if m is None:
            continue
        ele_tag = om.member_to_ele_tag.get(mid)
        if ele_tag is None:
            continue   # 이미 분할/제거된 선 — 스킵
        c1 = am.nodes[m.n1].coord
        c2 = am.nodes[m.n2].coord
        bvec = c2 - c1
        blen = float(np.linalg.norm(bvec))
        if blen < 1.0:
            continue
        bdir = bvec / blen

        # 1. 사영점 t 정렬 + ±5mm 중복 묶기
        raw: List[Tuple[float, int, np.ndarray]] = []
        for nP, proj in plist:
            t_abs = float(np.dot(proj - c1, bdir))
            if t_abs < 1.0 or t_abs > blen - 1.0:
                continue   # 선 끝 = (a) 직접 매칭 영역
            raw.append((t_abs, nP, proj))
        raw.sort(key=lambda x: x[0])
        deduped: List[Tuple[float, int, np.ndarray]] = []
        extra_at: Dict[int, List[int]] = {}   # 같은 사영점에 묶인 추가 외부노드
        for entry in raw:
            if deduped and abs(entry[0] - deduped[-1][0]) < 5.0:
                extra_at.setdefault(len(deduped) - 1, []).append(entry[1])
                continue
            deduped.append(entry)
        if not deduped:
            continue

        # 2. 기존 element 제거
        ops.remove('element', ele_tag)
        om.beam_elements.pop(ele_tag, None)
        if om.spec is not None:
            om.spec.beams = [b for b in om.spec.beams if b.tag != ele_tag]
        om.member_to_ele_tag.pop(mid, None)
        sub_tags: List[int] = []

        # 단면·재료 — 부재 종류 보존
        A, Iy, Iz, J = _section_props(m)
        is_truss = (m.kind == 'truss')
        tt = None
        if not is_truss:
            vec_xz = _vecxz_for_member(c1, c2)
            tt = _geom_transf_tag(m.kind, vec_xz)
            if m.role in RC_BEAM_ROLES:
                E_use, G_use = RC_WALL_E_MPA, RC_WALL_G_MPA
            else:
                E_use, G_use = STEEL_E_MPA, STEEL_G_MPA
        src_cid = m.source_comp_ids[0] if m.source_comp_ids else 0

        def _add_sub(a: int, b: int) -> None:
            nonlocal next_ele, next_sub_mid
            if is_truss:
                ops.element('Truss', next_ele, a, b, A, TRUSS_MAT_TAG)
            else:
                ops.element('elasticBeamColumn', next_ele, a, b,
                            A, E_use, G_use, J, Iy, Iz, tt)
            om.beam_elements[next_ele] = (a, b, m.kind, m.role)
            if om.spec is not None:
                om.spec.beams.append(BeamRec(
                    tag=next_ele, n1=a, n2=b, kind=m.kind, role=m.role,
                    section_w=float(m.section_w), section_h=float(m.section_h),
                    section_t=float(m.section_t),
                    source_comp_ids=list(m.source_comp_ids)))
            sub_mid = next_sub_mid
            next_sub_mid += 1
            am.members[sub_mid] = AnalysisMember(
                id=sub_mid, n1=a, n2=b, kind=m.kind, role=m.role,
                section_w=float(m.section_w), section_h=float(m.section_h),
                section_t=float(m.section_t),
                source_comp_ids=list(m.source_comp_ids),
                merge_group=m.merge_group,
                is_split_sub=True, parent_member_id=mid)
            sub_tags.append(next_ele)
            next_ele += 1

        prev_node = m.n1
        for idx, (t_abs, nP, proj) in enumerate(deduped):
            # 3. 새 노드 Q (선 직선 위 사영점)
            Q = next_node
            next_node += 1
            ops.node(Q, float(proj[0]), float(proj[1]), float(proj[2]))
            om.node_tags[Q] = proj.copy()
            am.nodes[Q] = AnalysisNode(
                id=Q, coord=proj.copy(), source_comp_id=src_cid)
            if om.spec is not None:
                om.spec.nodes.append(NodeRec(
                    tag=Q, coord=proj.copy(), role='core_proj',
                    source_comp_id=src_cid))
            # 4. sub-element prev → Q
            _add_sub(prev_node, Q)
            # 5. 외부노드 ↔ Q 6 DOF 결합 (+같은 사영점에 묶인 추가 외부노드)
            for mP in [nP] + extra_at.get(idx, []):
                if mP == Q:
                    continue
                # [2026-06-03] 코어 사영점 Q=master(retained), 외부 mP=slave
                # (constrained)로 반전. 코어 노드 전부 6DOF 고정과 호환(코어=master).
                _d = _resolve_override_dofs(om, Q, mP, dofs_6, RULE_ID_CORE)
                if _d is None:
                    continue   # remove 오버라이드
                try:
                    ops.equalDOF(Q, mP, *_d)
                except Exception as e:
                    om.registration_failures.append(
                        (mP, f'R09 split equalDOF master={Q}: {e}'))
                    continue
                om.constrained_node_ids.add(mP)
                if om.spec is not None:
                    om.spec.equal_dofs.append(EqualDofRec(
                        master=Q, slave=mP, dofs=tuple(_d),
                        kind='core_edge', rule_id=RULE_ID_CORE))
                registered += 1
            prev_node = Q

        # 6. 마지막 segment prev → n2
        _add_sub(prev_node, m.n2)

        # 7. 분할 매핑 기록 (자중 적용용)
        if sub_tags:
            om.member_to_split_ele_tags[mid] = sub_tags
            if om.spec is not None:
                om.spec.member_to_split_tags[mid] = sub_tags[:]

    if registered and hasattr(am, 'invalidate_indices'):
        am.invalidate_indices()
    return registered


# ── 룰 일괄 적용 ──────────────────────────────────────────────

def apply_all_joint_rules(om, dofs: Tuple[int, ...] = (1, 2, 3)) -> int:
    """모든 케이스별 접합 룰을 순차 적용.

    각 룰은 독립. R01·R04 는 _apply_corner_edge_rule 코어, R02 는
    _apply_vstack_rule 코어를 공유. 충돌 검사 없이 단순 등록하고 ops_solver 의
    Penalty handler 가 과구속을 견딘다.

    [정책 2026-05-16 — 룰 통합 분할]
    R01·R03·R04 가 보 모서리에 만드는 사영점은 본 함수가 만드는 공통
    edge_split_map 에 누적되고, 모든 룰이 끝난 뒤 _split_edges_and_link_corners
    가 한 번만 호출되어 보별로 일괄 분할. 짧은 캔틸 보처럼 한 보에 두 룰의
    사영점이 동시에 떨어지는 케이스를 모두 살리고, "이미 분할된 보" 차단이
    발생하지 않도록 함.

    build_ops_model 의 결합 단계 자리에서 본 함수 한 줄만 호출.
    """
    # 룰 간 공유되는 사영점 누적 dict — {mid: [(nP_or_N1, proj, rule_id), ...]}
    edge_split_map: Dict[int, List[Tuple[int, np.ndarray, str]]] = {}
    n = 0
    n += apply_module_module_horizontal(om, dofs,
                                        edge_split_map=edge_split_map)  # R01
    n += apply_module_module_vertical(om, dofs)                         # R02
    n += apply_panel_module(om, dofs,
                            edge_split_map=edge_split_map)               # R03
    n += apply_panel_panel_horizontal(om, dofs,
                                       edge_split_map=edge_split_map)    # R04
    n += apply_wall_floor_vertical(om, dofs)                              # R05
    n += apply_wall_module(om, dofs,
                           edge_split_map=edge_split_map)                  # R06
    n += apply_wall_wall_horizontal(om, dofs,
                                     edge_split_map=edge_split_map)        # R07
    n += apply_wall_cantilever_beam(om, dofs,
                                     edge_split_map=edge_split_map)        # R08
    # 일괄 분할 — 룰들이 누적한 사영점을 보별로 한 번에 처리.
    n += _split_edges_and_link_corners(om, edge_split_map, dofs)
    # R09 — (a) 직접 노드 매칭 + (b) 사영점을 core_split_map 에 누적.
    # [함정·2026-06-08] 코어 접합은 수직(UZ=3)을 묶으면 안 된다. 매 층마다 모듈
    # 노드를 코어벽에 UZ 까지 강결합하면 수직 강성이 큰 코어가 인접 모듈을
    # 떠받쳐 모듈 기둥이 중력을 못 받고(축력≈0), 그 하중이 경계 보로 새어
    # 전단으로 터진다(코어 접합 모듈 위치마다 NG, 적용하중≫반력 = 평형 붕괴).
    # 코어는 횡력 전달(다이어프램)만 담당해야 하므로 수평(1,2)+회전(4,5,6)만
    # 묶고 UZ 는 푼다 → 모듈이 제 기둥으로 중력을 내림. (A:NG137→0·평형회복,
    # B/C 회귀 없음 검증.)
    core_dofs = (1, 2, 4, 5, 6)
    core_split_map: Dict[int, List[Tuple[int, np.ndarray]]] = {}
    n += apply_core_joint(om, core_dofs, core_split_map=core_split_map)      # R09
    # 코어 선을 사영점에서 분할 + 외부노드 결합 (truss/콘크리트 종류 보존, UZ 제외).
    n += _split_core_lines(om, core_split_map, core_dofs)
    return n


# ── 사용자 신규 접합 (2026-05-25 — 모서리 위 점 분할 재설계) ──
# 자동 규칙 R01~R09 다음. 사용자 추가 접합은 성질에 따라 R10(핀)·R11(강접)으로
# 구분 — 와이어프레임 색·범례에서 핀/강접 추가가 따로 보인다.
RULE_ID_USER_ADD = 'USER_ADD'        # 구버전 저장본 호환(성질 미구분)
RULE_ID_USER_PIN = 'R10_user_pin'    # 사용자 추가 — 핀
RULE_ID_USER_RIGID = 'R11_user_rigid'  # 사용자 추가 — 강접


def _seg_seg_closest(p1, p2, p3, p4):
    """두 선분 [p1,p2],[p3,p4]의 최근접점 쌍(Q12, Q34)과 거리."""
    d1 = p2 - p1
    d2 = p4 - p3
    r = p1 - p3
    a = float(np.dot(d1, d1))
    e = float(np.dot(d2, d2))
    f = float(np.dot(d2, r))
    eps = 1e-9
    if a < eps and e < eps:
        return p1.copy(), p3.copy(), float(np.linalg.norm(p1 - p3))
    if a < eps:
        s = 0.0
        t = float(np.clip(f / e, 0.0, 1.0))
    else:
        c = float(np.dot(d1, r))
        if e < eps:
            t = 0.0
            s = float(np.clip(-c / a, 0.0, 1.0))
        else:
            b = float(np.dot(d1, d2))
            denom = a * e - b * b
            s = float(np.clip((b * f - c * e) / denom, 0.0, 1.0)) if denom > eps else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = float(np.clip(-c / a, 0.0, 1.0))
            elif t > 1.0:
                t = 1.0
                s = float(np.clip((b - c) / a, 0.0, 1.0))
    q1 = p1 + s * d1
    q2 = p3 + t * d2
    return q1, q2, float(np.linalg.norm(q1 - q2))


def candidate_joint_points(om, p, exclude_comp=0,
                           max_dist=400.0, tol=60.0, right_angle=False):
    """첫 점 p 에서 접합 가능한 다른 부재 위 점 목록 — 접합 추가 두번째 점 후보.

    right_angle=False(직선): p 와 축정렬(x·y·z 중 한 축만 다름) + max_dist 이내 +
    다른 컴포넌트 보 위. p 의 3축선과 각 보 선분 최근접점으로.
    right_angle=True(직각·R03식): p 의 평면 위치에서 수직으로 내린/올린 다른 높이의
    보 위에서, 그 평면 위치에 가장 가까운 점(직각으로 닿을 수 있는 점)을 후보로.
    반환: [(x, y, z, comp_id), ...] (중복 ±tol 제거).
    """
    am = getattr(om, 'analysis_model', None)
    if am is None:
        return []
    P = np.asarray(p, dtype=float)
    out = []
    if right_angle:
        az = float(P[2])
        p0 = np.array([P[0], P[1], 0.0])
        for mid, m in am.members.items():
            if getattr(m, 'is_split_sub', False) or m.kind != 'beam':
                continue
            cid = m.source_comp_ids[0] if m.source_comp_ids else 0
            if exclude_comp and cid == exclude_comp:
                continue
            c1 = np.asarray(am.nodes[m.n1].coord, dtype=float)
            c2 = np.asarray(am.nodes[m.n2].coord, dtype=float)
            mz = 0.5 * (float(c1[2]) + float(c2[2]))
            if abs(mz - az) <= tol:
                continue   # 같은 높이 — 직선용(직각 아님)
            a2 = np.array([c1[0], c1[1], 0.0])
            b2 = np.array([c2[0], c2[1], 0.0])
            blen = float(np.linalg.norm(b2 - a2))
            if blen < 1.0:
                continue
            proj2, dxy, t = _user_point_seg(p0, a2, b2)
            if t * blen < USER_ADD_MIN_SEGMENT_MM or (1.0 - t) * blen < USER_ADD_MIN_SEGMENT_MM:
                continue   # 보 끝점 영역 제외
            if dxy <= tol or dxy > max_dist:
                continue   # 평면으로 안 떨어지면 수직 직선(직각 아님)
            q = np.array([proj2[0], proj2[1], mz])
            if float(np.linalg.norm(q - P)) > max_dist:
                continue
            out.append((float(q[0]), float(q[1]), float(q[2]), int(cid)))
    else:
        axes = (np.array([1.0, 0.0, 0.0]),
                np.array([0.0, 1.0, 0.0]),
                np.array([0.0, 0.0, 1.0]))
        for mid, m in am.members.items():
            if getattr(m, 'is_split_sub', False) or m.kind != 'beam':
                continue
            cid = m.source_comp_ids[0] if m.source_comp_ids else 0
            if exclude_comp and cid == exclude_comp:
                continue
            c1 = np.asarray(am.nodes[m.n1].coord, dtype=float)
            c2 = np.asarray(am.nodes[m.n2].coord, dtype=float)
            for ai, e in enumerate(axes):
                a0 = P - max_dist * e
                a1 = P + max_dist * e
                qb, qa, dist = _seg_seg_closest(c1, c2, a0, a1)
                if dist > tol:
                    continue
                q = P.copy()
                q[ai] = qb[ai]
                if float(np.linalg.norm(q - qb)) > tol:
                    continue
                dd = float(np.linalg.norm(q - P))
                if dd < 1.0 or dd > max_dist:
                    continue
                out.append((float(q[0]), float(q[1]), float(q[2]), int(cid)))
    uniq = []
    for q in out:
        if not any(abs(q[0] - u[0]) < tol and abs(q[1] - u[1]) < tol
                   and abs(q[2] - u[2]) < tol for u in uniq):
            uniq.append(q)
    return uniq


def _user_point_seg(p, a, b):
    """점 p 와 선분 a-b: (투영점, 거리, 매개변수 t∈[0,1]) 반환."""
    ab = b - a
    ll = float(np.dot(ab, ab))
    if ll < 1e-9:
        return a.copy(), float(np.linalg.norm(p - a)), 0.0
    t = float(np.clip(np.dot(p - a, ab) / ll, 0.0, 1.0))
    proj = a + t * ab
    return proj, float(np.linalg.norm(p - proj)), t


def _split_member_at_point(om, point, line_tol=USER_ADD_LINE_TOL_MM):
    """point 에 가장 가까운 보 element 를 그 점에서 분할하고 새 노드 tag 반환.
    가까운 보가 없거나 끝점 근처(스냅 영역)면 None.

    [2026-05-25] 미분할 원본뿐 아니라 **이미 자동분할된 보(sub)** 도 대상으로
    한다(om.beam_elements 전체 순회). 그래야 1층 바닥·최상층처럼 자동분할이
    일어난 층에서도 모서리 중간 점 접합이 된다. 단면·부모는 ele→원본 역맵으로
    추적해 member_to_split_ele_tags 를 일관되게 갱신(자중 분포 유지)."""
    am = om.analysis_model
    if am is None:
        return None
    from modular_3d.analysis.ops_builder import (
        _section_props, _vecxz_for_member, _geom_transf_tag)
    from modular_3d.analysis.constants import STEEL_E_MPA, STEEL_G_MPA
    from modular_3d.analysis.topology import AnalysisNode, AnalysisMember
    from modular_3d.analysis.model_spec import NodeRec, BeamRec
    P = np.asarray(point, dtype=float)
    # element → 원본 부재 mid (단면·자중 추적용).
    ele_to_parent = {}
    for mid, et in om.member_to_ele_tag.items():
        ele_to_parent[et] = mid
    for mid, ets in om.member_to_split_ele_tags.items():
        for et in ets:
            ele_to_parent[et] = mid
    best_ele = None
    best_d = line_tol
    best_proj = None
    best_n1 = best_n2 = None
    for ele, (n1, n2, kind, role) in list(om.beam_elements.items()):
        if kind != 'beam':
            continue
        cc1 = om.node_tags.get(n1)
        cc2 = om.node_tags.get(n2)
        if cc1 is None or cc2 is None:
            continue
        cc1 = np.asarray(cc1, dtype=float)
        cc2 = np.asarray(cc2, dtype=float)
        blen = float(np.linalg.norm(cc2 - cc1))
        if blen < 1.0:
            continue
        proj, d, t = _user_point_seg(P, cc1, cc2)
        # 끝점 50mm 이내는 분할 안 함(스냅 영역).
        if t * blen < USER_ADD_MIN_SEGMENT_MM or (1.0 - t) * blen < USER_ADD_MIN_SEGMENT_MM:
            continue
        if d < best_d:
            best_d, best_ele, best_proj = d, ele, proj
            best_n1, best_n2 = n1, n2
    if best_ele is None:
        return None
    n1, n2, kind, role = om.beam_elements[best_ele]
    parent_mid = ele_to_parent.get(best_ele)
    pm = am.members.get(parent_mid) if parent_mid is not None else None
    # 단면·메타는 원본 부재에서. (없으면 기본 SHS 200×200 으로 안전 폴백.)
    if pm is not None:
        A, Iy, Iz, J = _section_props(pm)
        src_cid = pm.source_comp_ids[0] if pm.source_comp_ids else 0
        sw, sh, st = pm.section_w, pm.section_h, pm.section_t
        sect_type = getattr(pm, 'section_type', 'shs')
        mg = pm.merge_group
        scids = list(pm.source_comp_ids)
    else:
        from modular_3d.analysis.section_catalog import SHS_200x200x8 as _P
        A, Iy, Iz, J = _P['A'], _P['Iy'], _P['Iz'], _P['J']
        src_cid = 0
        sw, sh, st = 200.0, 200.0, 8.0
        sect_type = 'shs'
        mg = 0
        scids = []
    cc1 = np.asarray(om.node_tags[n1], dtype=float)
    cc2 = np.asarray(om.node_tags[n2], dtype=float)
    vec_xz = _vecxz_for_member(cc1, cc2)
    tt = _geom_transf_tag(kind, vec_xz)
    # 새 노드 Q (SPLIT 영역 — 빌드 청소 대상이라 다음 빌드에서 재생성됨).
    Q = max(max(om.node_tags.keys()) + 1, SPLIT_NODE_BASE_OFFSET)
    ops.node(Q, float(best_proj[0]), float(best_proj[1]), float(best_proj[2]))
    om.node_tags[Q] = best_proj.copy()
    am.nodes[Q] = AnalysisNode(id=Q, coord=best_proj.copy(),
                               source_comp_id=src_cid)
    if om.spec is not None:
        om.spec.nodes.append(NodeRec(
            tag=Q, coord=best_proj.copy(), role='user_add_split',
            source_comp_id=src_cid))
    # 기존 element 제거
    try:
        ops.remove('element', best_ele)
    except Exception:
        pass
    om.beam_elements.pop(best_ele, None)
    if om.spec is not None:
        om.spec.beams = [b for b in om.spec.beams if b.tag != best_ele]
    # 분할 대상이 미분할 원본이면 member_to_ele_tag 에서 제거.
    if parent_mid is not None and om.member_to_ele_tag.get(parent_mid) == best_ele:
        om.member_to_ele_tag.pop(parent_mid, None)
    next_ele = max(om.beam_elements.keys()) + 1 if om.beam_elements else 1
    next_sub = max(am.members.keys()) + 1 if am.members else 1
    new_sub_tags = []
    for (na, nb) in ((n1, Q), (Q, n2)):
        ops.element('elasticBeamColumn', next_ele, na, nb,
                    A, STEEL_E_MPA, STEEL_G_MPA, J, Iy, Iz, tt)
        om.beam_elements[next_ele] = (na, nb, kind, role)
        if om.spec is not None:
            om.spec.beams.append(BeamRec(
                tag=next_ele, n1=na, n2=nb, kind=kind, role=role,
                section_w=float(sw), section_h=float(sh), section_t=float(st),
                source_comp_ids=list(scids)))
        am.members[next_sub] = AnalysisMember(
            id=next_sub, n1=na, n2=nb, kind=kind, role=role,
            section_w=float(sw), section_h=float(sh), section_t=float(st),
            section_type=sect_type, source_comp_ids=list(scids),
            merge_group=mg, is_split_sub=True,
            parent_member_id=parent_mid if parent_mid is not None else next_sub)
        new_sub_tags.append(next_ele)
        next_ele += 1
        next_sub += 1
    # member_to_split_ele_tags 갱신 — 분할된 best_ele 를 두 새 sub 로 교체.
    if parent_mid is not None:
        cur = [t for t in om.member_to_split_ele_tags.get(parent_mid, [])
               if t != best_ele]
        cur += new_sub_tags
        om.member_to_split_ele_tags[parent_mid] = cur
        if om.spec is not None:
            om.spec.member_to_split_tags[parent_mid] = list(cur)
    return Q


def _can_anchor(om, x, y, z, snap_tol=USER_ADD_SNAP_TOL_MM, line_tol=USER_ADD_LINE_TOL_MM, on_edge=False):
    """(x,y,z) 에 접합 끝점을 만들 수 있는가 — 노드 스냅 또는 보 분할 가능 여부만
    판단(노드를 실제로 만들지 않음). 접합 실패 층에 분할 노드가 남는 것을 막기
    위해, 두 끝점이 모두 가능한 층에서만 실제 분할/연결을 하도록 사전 검사한다.

    on_edge=True 면 노드 스냅을 쓰지 않고 보 분할만으로 가능한지 검사한다
    (사용자가 '선 위 점'을 골랐으면 그 자리에 노드를 새로 만들어야 하므로)."""
    P = np.array([x, y, z], dtype=float)
    if not on_edge:
        for tag, c in om.node_tags.items():
            if float(np.linalg.norm(P - np.asarray(c, dtype=float))) <= snap_tol:
                return True
    for ele, (n1, n2, kind, role) in om.beam_elements.items():
        if kind != 'beam':
            continue
        c1 = om.node_tags.get(n1)
        c2 = om.node_tags.get(n2)
        if c1 is None or c2 is None:
            continue
        c1 = np.asarray(c1, dtype=float)
        c2 = np.asarray(c2, dtype=float)
        blen = float(np.linalg.norm(c2 - c1))
        if blen < 1.0:
            continue
        proj, d, t = _user_point_seg(P, c1, c2)
        if t * blen < USER_ADD_MIN_SEGMENT_MM or (1.0 - t) * blen < USER_ADD_MIN_SEGMENT_MM:
            continue
        if d <= line_tol:
            return True
    return False


def _anchor_node_at(om, x, y, z, snap_tol=USER_ADD_SNAP_TOL_MM, line_tol=USER_ADD_LINE_TOL_MM, on_edge=False):
    """(x,y,z) 위치의 접합 끝점 노드 — 가까운 기존 노드가 있으면 스냅,
    없으면 가장 가까운 보를 분할해 새 노드. 둘 다 없으면 None.

    on_edge=True 면 노드 스냅을 건너뛰고 그 자리에 보를 분할해 새 노드를
    만든다 — 사용자가 '선 위 점'을 골랐을 때 가까운 기존 노드로 끌려가지 않게."""
    P = np.array([x, y, z], dtype=float)
    if not on_edge:
        best_n = None
        best_d = snap_tol
        for tag, c in om.node_tags.items():
            d = float(np.linalg.norm(P - np.asarray(c, dtype=float)))
            if d <= best_d:
                best_d, best_n = d, tag
        if best_n is not None:
            return best_n
    return _split_member_at_point(om, P, line_tol)


def _anchor_zs(om, x, y, tol):
    """평면 위치 (x,y) 에 존재하는 z 집합 — 그 위치의 노드 z + 그 xy 를 지나는
    수평 보의 z. 다층 복제를 위해 '같은 위치의 모든 층'을 모은다.

    [함정] member_to_ele_tag 는 자동 분할(R01·R03 등)된 보를 빼버려서, 분할된
    층(예: 1층 바닥·최상층)이 누락된다. 대신 am.members 의 원본 보(is_split_sub
    아닌 것 — 분할돼도 원본은 am.members 에 남음)를 순회해 모든 층을 모은다."""
    zs = set()
    for tag, c in om.node_tags.items():
        if abs(float(c[0]) - x) <= tol and abs(float(c[1]) - y) <= tol:
            zs.add(round(float(c[2]), 1))
    am = om.analysis_model
    if am is not None:
        p0 = np.array([x, y, 0.0])
        for mid, m in am.members.items():
            if getattr(m, 'is_split_sub', False) or m.kind != 'beam':
                continue
            c1 = am.nodes[m.n1].coord
            c2 = am.nodes[m.n2].coord
            a2 = np.array([c1[0], c1[1], 0.0])
            b2 = np.array([c2[0], c2[1], 0.0])
            proj, d, t = _user_point_seg(p0, a2, b2)
            if d <= tol:
                zc = float(c1[2]) + t * (float(c2[2]) - float(c1[2]))
                zs.add(round(zc, 1))
    return zs


def apply_added_joints(om, tol: float = None) -> int:
    """사용자 신규 접합(kind='add')을 등록 — 모서리 위 점도 지원.

    각 add 의 두 끝점(A=a_xy/z_a, B=b_xy/z_b):
      - 끝점 자리에 가까운 기존 노드가 있으면 그 노드(꼭지점 스냅),
        없으면 그 자리에서 가장 가까운 보를 분할해 새 노드 생성.
      - 두 노드를 add_dofs(핀/강접)로 연결.
    다층 복제: A 평면 위치에 존재하는 모든 층(z)을 순회하며 같은 접합을 만든다.

    [함정] equalDOF 는 한 노드를 두 번 slave 로 잡으면 Penalty 충돌. slave 가
    이미 종속이면 master/slave 를 swap, 둘 다 종속이면 건너뜀.
    build_ops_model 에서 apply_all_joint_rules 직후 호출(자동 결합·분할 뒤).
    """
    overrides = getattr(om, 'joint_overrides', None)
    if not overrides:
        return 0
    from modular_3d.model.joint_override import MATCH_TOL_MM
    from modular_3d.analysis.model_spec import EqualDofRec, NodeRec
    from modular_3d.analysis.topology import AnalysisNode
    if tol is None:
        tol = MATCH_TOL_MM
    am = om.analysis_model
    seen_pairs = set()
    counter = [0]

    def _emit(master, slave, dofs, rid, force=False):
        """equalDOF 등록(slave 중복 swap/skip + spec 기록). 성공 시 카운트.

        force=True 면 종속 검사를 건너뛰고 강제 등록(Penalty handler 가 과구속
        견딤) — 직각접합 체인(위→N1→아래)은 N1·아래가 이미 종속이라도 두 결합을
        모두 만들어야 하므로(R03 의 _link 와 동일 정책)."""
        if not force:
            if slave in om.constrained_node_ids:
                if master in om.constrained_node_ids:
                    return
                master, slave = slave, master
        if master == slave:
            return
        key = (min(master, slave), max(master, slave))
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        try:
            ops.equalDOF(master, slave, *dofs)
        except Exception as e:
            om.registration_failures.append(
                (slave, f'USER_ADD equalDOF master={master}: {e}'))
            return
        om.constrained_node_ids.add(slave)
        if om.spec is not None:
            om.spec.equal_dofs.append(EqualDofRec(
                master=master, slave=slave, dofs=tuple(dofs),
                kind='user_add', rule_id=rid))
        counter[0] += 1

    def _mk_n1(px, py, pz, cid):
        """직각(ㄴ자)용 중간 노드 N1 — role='panel_z_route' 라 빌드가 회전
        자유도를 자동 fix(자체 부재 없는 가교라 mechanism 방지). SPLIT 영역 tag."""
        nid = max(max(om.node_tags.keys()) + 1, SPLIT_NODE_BASE_OFFSET)
        coord = np.array([px, py, pz], dtype=float)
        ops.node(nid, float(px), float(py), float(pz))
        om.node_tags[nid] = coord
        if am is not None:
            am.nodes[nid] = AnalysisNode(id=nid, coord=coord.copy(),
                                         source_comp_id=cid)
        if om.spec is not None:
            om.spec.nodes.append(NodeRec(
                tag=nid, coord=coord.copy(), role='panel_z_route',
                source_comp_id=cid))
        return nid

    for ov in overrides:
        if getattr(ov, 'kind', '') != 'add':
            continue
        ax, ay = float(ov.a_xy[0]), float(ov.a_xy[1])
        bx, by = float(ov.b_xy[0]), float(ov.b_xy[1])
        dz_rel = float(ov.z_b) - float(ov.z_a)
        dofs = tuple(ov.add_dofs) if ov.add_dofs else (1, 2, 3)
        # 성질에 따라 rule_id 구분: 강접(회전 자유도 포함)=R11, 핀=R10.
        is_rigid = any(x in (4, 5, 6) for x in dofs)
        rid = RULE_ID_USER_RIGID if is_rigid else RULE_ID_USER_PIN
        single = getattr(ov, 'single_layer', False)
        right = getattr(ov, 'right_angle', False)
        # 끝점 종류 — 선 위 점이면 그 자리에 보 분할(노드 추가), 꼭지점이면 노드 스냅.
        a_edge = getattr(ov, 'a_on_edge', False)
        b_edge = getattr(ov, 'b_on_edge', False)
        # single_layer 면 클릭한 그 층(z_a)만, 아니면 모든 층(분할 전 수집).
        if single:
            a_zs = [round(float(ov.z_a), 1)]
        else:
            a_zs = sorted(_anchor_zs(om, ax, ay, tol))
        for za in a_zs:
            zb = za + dz_rel
            # 양쪽 끝점을 만들 수 있는 층만 — 한쪽만 가능하면 분할 노드가 접합
            # 없이 남으므로(노드 생성 전에) 건너뛴다.
            if not _can_anchor(om, ax, ay, za, on_edge=a_edge):
                continue
            if not _can_anchor(om, bx, by, zb, on_edge=b_edge):
                continue
            nA = _anchor_node_at(om, ax, ay, za, on_edge=a_edge)
            nB = _anchor_node_at(om, bx, by, zb, on_edge=b_edge)
            if nA is None or nB is None:
                continue
            if right:
                # ㄴ자(R03 방식) — 위 점에서 아래 점 높이로 수직 내린 N1 경유.
                cA = np.asarray(om.node_tags[nA], dtype=float)
                cB = np.asarray(om.node_tags[nB], dtype=float)
                if cA[2] >= cB[2]:
                    nU, cU, nD, cD = nA, cA, nB, cB
                else:
                    nU, cU, nD, cD = nB, cB, nA, cA
                # N1 의 source_comp_id 는 0(중립). 아래 점 comp 와 같게 두면
                # 'N1↔아래' 수평 결합이 같은 컴포넌트 내부로 분류돼 픽킹에서
                # 빠진다(→ 수평 선택·제거 불가). 0 이면 두 결합 다 픽킹된다.
                n1 = _mk_n1(float(cU[0]), float(cU[1]), float(cD[2]), 0)
                # 체인 강제 등록 — N1·아래가 이미 종속이어도 두 결합 모두 생성.
                _emit(nU, n1, dofs, rid, force=True)   # ① 수직
                _emit(n1, nD, dofs, rid, force=True)   # ② 수평
            else:
                _emit(nA, nB, dofs, rid)
    n = counter[0]
    if n:
        pass
    return n


def remove_dangling_bridge_nodes(om) -> int:
    """접합이 모두 제거되어 허공에 남은 가교 중간노드(N1, panel_z_route)를 정리.

    직각(ㄴ자)접합의 중간노드 N1 은 자체 부재 없이 두 결합(위↔N1, N1↔아래)으로만
    구조에 매달린 가교다. 사용자가 그 직각접합을 제거하면 두 결합이 모두 등록에서
    빠지지만(게이트가 None 반환), N1 노드 자체는 이미 생성돼 허공에 남는다.
    링크(equalDOF)도 부재(beam element)도 없는 N1 을 삭제한다.

    [호출 시점] 회전 자유도 자동 fix(build_ops_model 6-c) 이전 — 이미 fix 된
    노드를 지우면 ops.remove 가 꼬일 수 있으므로 apply_added_joints 직후 호출.

    Returns: 삭제한 노드 수.
    """
    if om.spec is None:
        return 0
    # 등록된 결합·부재에 쓰인 노드(살아있는 노드).
    linked = set()
    for ed in om.spec.equal_dofs:
        linked.add(ed.master)
        linked.add(ed.slave)
    in_beam = set()
    for (n1, n2, kind, role) in om.beam_elements.values():
        in_beam.add(n1)
        in_beam.add(n2)
    removed = 0
    for nr in list(om.spec.nodes):
        if getattr(nr, 'role', '') != 'panel_z_route':
            continue
        tag = nr.tag
        if tag in linked or tag in in_beam:
            continue   # 아직 결합/부재에 매달려 있음 — 유지.
        try:
            ops.remove('node', tag)
        except Exception as e:
            om.registration_failures.append(
                (tag, f'허공 가교노드 제거 실패: {e}'))
            continue
        om.node_tags.pop(tag, None)
        om.spec.nodes = [n for n in om.spec.nodes if n.tag != tag]
        if om.analysis_model is not None:
            om.analysis_model.nodes.pop(tag, None)
        removed += 1
    if removed:
        pass
    return removed
