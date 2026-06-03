"""공정표 어댑터 — 우리 모델(scene + ProjectSettings) → 팀원 HTML 입력 직렬화.

[설계 근거]
- `공정표_이식_계획서.md` §3 Phase B.
- 출력: dict {summary: ..., components: [...]} — 팀원 HTML 의 importScene 이 그대로 받음.
- 좌표 정책: position 을 항상 **min-corner**(앵커=0, 회전=0) 로 정규화해 export.
  팀원 box() 가 0도 분기만 타도록 만들어 회전·앵커 다양성 흡수.
- summary 우선 — readModuleJoints / 좌표 폴백 경로를 거치지 않게 충분히 채운다.

[모듈-모듈 접합 카운트]
- 수직접합: floor_index>0 인 *일반 모듈* 만 × 4 (꼭지점 기둥). 수직3층모듈 자체엔
  층간 수직접합 없음(한 덩어리).
- 수평접합: 층별로 그 층에 존재하는 모듈(수직모듈은 관통 층 모두 포함)의
  인접쌍 수 합산 — C3 정정 반영.

[모듈 최대 중량]
- 운송 어댑터의 산출값을 받을 수 있으면 그것을 사용, 아니면 면적 기반 어림으로
  보수적 추정. HTML 의 moduleTypes 입력으로 사용자가 보정 가능.

[의존성]
- 기본 summary 산출: model/core.py 의 ComponentType / Component 만 읽음(가벼움).
- 카테고리별 접합 카운트(r02/r05/bolt_horiz_per_floor)는 scene 인자가 들어왔을 때만
  topology.build_analysis_model + ops_builder.build_ops_model 을 호출해 spec
  접합 리스트를 룰별로 집계. transport 어댑터엔 여전히 무의존.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np

from ..model.core import (
    Component,
    ComponentType,
    Module,
    FloorPanel,
    StructWall,
    Vertical3Module,
    CoreSlab,
)


# ── 좌표 정규화 ─────────────────────────────────────────────
def _aabb_xy_extent(comp: Component) -> Tuple[float, float, float, float, float, float]:
    """월드 AABB (xmin, ymin, zmin, xmax, ymax, zmax) — 회전·앵커 모두 반영."""
    try:
        verts = comp.get_world_corners()
        if verts is None or len(verts) == 0:
            raise ValueError("no corners")
        xmin, ymin, zmin = float(verts[:, 0].min()), float(verts[:, 1].min()), float(verts[:, 2].min())
        xmax, ymax, zmax = float(verts[:, 0].max()), float(verts[:, 1].max()), float(verts[:, 2].max())
        return xmin, ymin, zmin, xmax, ymax, zmax
    except Exception:
        # 폴백: position + dimensions 그대로 (rotation 무시)
        x = float(comp.position[0]); y = float(comp.position[1]); z = float(comp.position[2])
        w = float(comp.dimensions.get("width", 0.0))
        d = float(comp.dimensions.get("depth", 0.0))
        h = float(comp.dimensions.get("height", 0.0))
        return x, y, z, x + w, y + d, z + h


def _to_jsonable(v: Any) -> Any:
    """numpy / 비표준 자료형을 JSON 안전형으로 변환."""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, np.ndarray):
        return [_to_jsonable(x) for x in v.tolist()]
    if isinstance(v, dict):
        return {str(k): _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return v


def _normalize_joint_records(records: Iterable[Any]) -> List[Dict[str, Any]]:
    """joint_records 의 numpy 값들을 모두 native 형으로 변환."""
    out: List[Dict[str, Any]] = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        out.append({str(k): _to_jsonable(v) for k, v in r.items()})
    return out


def _component_to_export_dict(comp: Component) -> Dict[str, Any]:
    """컴포넌트를 팀원 HTML 이 기대하는 component dict 로 직렬화.

    - position 은 AABB.min (rotation=0, anchor=0 으로 export).
    - dimensions.width/depth/height 는 월드 AABB 변(회전이 이미 반영된 길이).
    - joint_records 는 numpy 자료형을 native 로 정규화해 그대로 전달.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = _aabb_xy_extent(comp)
    dim = dict(comp.dimensions or {})
    dim["width"] = float(xmax - xmin)
    dim["depth"] = float(ymax - ymin)
    dim["height"] = float(zmax - zmin)
    dim = {str(k): _to_jsonable(v) for k, v in dim.items()}
    out: Dict[str, Any] = {
        "id": int(comp.id),
        "comp_type": comp.comp_type.value if isinstance(comp.comp_type, ComponentType) else str(comp.comp_type),
        "position": [float(xmin), float(ymin), float(zmin)],
        "rotation": 0,
        "anchor": 0,
        "dimensions": dim,
        "floor_index": int(getattr(comp, "floor_index", 0)),
        "group_id": int(getattr(comp, "group_id", 0)),
        "sub_index": int(getattr(comp, "sub_index", 0)),
        "joint_records": _normalize_joint_records(getattr(comp, "joint_records", []) or []),
    }
    return out


# ── 모듈/수직모듈 관통 층 산출 ─────────────────────────────
_MODULE_FLOOR_HEIGHT_MM = 3000.0   # 표준 1층 높이 (관통 층수 추정용)


def _module_floor_span(comp: Component) -> Tuple[int, int]:
    """이 모듈이 점유하는 [시작층, 끝층] (inclusive).

    - 일반 모듈: (f, f)
    - 수직3층모듈: height 로 관통 층수 추정.
    """
    f0 = int(getattr(comp, "floor_index", 0))
    if isinstance(comp, Vertical3Module):
        h = float(comp.dimensions.get("height", 0.0))
        k = max(1, int(round(h / _MODULE_FLOOR_HEIGHT_MM)))
        return f0, f0 + k - 1
    return f0, f0


# ── 인접 모듈쌍 카운트 ─────────────────────────────────────
_ADJ_TOL_MM = 400.0      # 인접 허용 간격(팀원 코드 T)
_OVERLAP_REJECT_MM = 50  # 그 이상 겹치면 비정상으로 제외


def _adjacent_pairs(boxes: List[Tuple[float, float, float, float]]) -> int:
    """팀원 countHorizJointsFloor0 와 동일 산식: 인접쌍 카운트."""
    n = 0
    L = len(boxes)
    for i in range(L):
        a = boxes[i]
        for k in range(i + 1, L):
            b = boxes[k]
            ox = min(a[2], b[2]) - max(a[0], b[0])  # x 겹침
            oy = min(a[3], b[3]) - max(a[1], b[1])  # y 겹침
            if ox > _OVERLAP_REJECT_MM and oy > _OVERLAP_REJECT_MM:
                continue   # 비정상 겹침 — 제외
            t_x = (abs(a[2] - b[0]) <= _ADJ_TOL_MM or abs(b[2] - a[0]) <= _ADJ_TOL_MM) and oy > -_ADJ_TOL_MM
            t_y = (abs(a[3] - b[1]) <= _ADJ_TOL_MM or abs(b[3] - a[1]) <= _ADJ_TOL_MM) and ox > -_ADJ_TOL_MM
            if t_x or t_y:
                n += 1
    return n


# ── LEVEL 매핑 헬퍼 (접합부 설계 LEVEL 규칙 적용) ──────────────
def _level_of_joint(z: float, level_count: int, rule_id: str, floor_h: float) -> int:
    """접합 z 좌표 + R규칙 → 어느 층 접합인지 (0-indexed).

    [LEVEL 구간 정의 — 사용자 정책 2026-05-28]
    한 층 = 모듈 본체(3200) + 갭(220) = 3420 (floor_h).
    그러나 LEVEL 경계는 "모듈 본체 상단" 즉 z=3200, 6620, 10040, ...
    갭(3200~3420 등)은 위층에 귀속.

      1층 = [0,         3200]               = [0, MODULE_BODY]
      2층 = (3200,      6620]               = (MODULE_BODY, MODULE_BODY+H]
      3층 = (6620,     10040]               = (MODULE_BODY+H, MODULE_BODY+2H]
      n층 = (MODULE_BODY+(n-2)H,  MODULE_BODY+(n-1)H]

    이 구간 정의 하나로 R01·R02~R09 모두 자연스럽게 분류됨:
      - R01: master z = slave z (같은 평면). z=3200(1F 천장) → 1층, z=3420(2F 바닥) → 2층.
      - R02~R09: master/slave z 다름. max(mz,sz) 가 위 모듈 바닥에 떨어져
        자연스럽게 위층으로 매핑됨 (강봉 z=3420 → 2층 작업).

    [경계 z 처리]
    z = 3200, 6620, 10040, ... 정확히 경계에 떨어진 R01 은 "아래층" 에 포함.
    R02~R09 는 max(mz, sz) 가 모듈 갭 위쪽(>= 모듈본체+갭) 에 있어 경계에 떨어질 일 없음.
    """
    if level_count <= 0:
        return 0
    # 모듈 천장 강재 z = MODULE_HEIGHT_MM - SECTION_H_MM
    # MODULE_HEIGHT_MM=3400 은 모듈 외곽 박스 높이지만, 분석 모델은 모듈 천장
    # 코너 노드를 강재 단면의 lower face (=3400-200=3200) 에 배치. 사용자의
    # LEVEL 정의 ("1층=[0,3200]") 가 이 강재 z 기준이므로 같은 값 사용.
    try:
        from modular_3d.analysis.constants import (
            MODULE_HEIGHT_MM, SECTION_H_MM)
        MOD = float(MODULE_HEIGHT_MM) - float(SECTION_H_MM)
    except Exception:
        MOD = 3200.0
    # 각 층 상단 z (= 모듈 본체 끝): top_i = MOD + i * floor_h
    # top_0 = 3200, top_1 = 6620, top_2 = 10040, ...
    if rule_id in ('R01_mod_mod_h', 'R06_wall_mod', 'R07_wall_wall_h'):
        # R01·R06·R07: 경계 z 는 아래층 포함 (z <= top_i).
        # R01 = 모듈-모듈 수평, R06 = 벽-모듈, R07 = 벽-벽 수평
        # — 셋 다 같은 평면(수평) 결합이고, 경계 z 는 아래층 작업으로 본다.
        # z=3200 → 1층, z=6620 → 2층.
        for i in range(level_count):
            if z <= MOD + i * floor_h + 1e-6:
                return i
        return level_count - 1
    # 그 외 R02·R03·R04·R05·R08·R09: 경계 z 는 위층 포함
    # (z < top_i 인 가장 작은 i).
    # z=3200 → 2층 (모듈 본체 끝이지만 위층 패널·강봉의 작업 평면),
    # z=3420 → 2층, z=6620 → 3층.
    # ★ R09 (코어 6 DOF) 도 이 분기 — 코어벽 강결이 경계 z 에 떨어지면 위층 작업.
    for i in range(level_count):
        if z < MOD + i * floor_h - 1e-6:
            return i
    return level_count - 1


# ── 접합부 설계 결과(R01-R09 자동 + joint_overrides) → 카테고리별·층별 카운트 ──
def _categorize_joints_by_rule(
    scene: Any, floor_height_mm: float = _MODULE_FLOOR_HEIGHT_MM,
) -> Tuple[Dict[str, Dict[str, int]], int, int, int]:
    """공정표용 접합 카운트 — 자동 룰 R01-R09 + 사용자 수정(joint_overrides) 반영.

    구현: build_ops_model 이 om.spec.equal_dofs 에 rule_id 와 함께 모든 결합을
    적재한다. 같은 파이프라인이 joint_overrides 도 자동 반영 → 최종 결합 리스트.

    각 접합의 "그 접합이 속한 층" 산정:
      - 두 노드의 source_comp_id 로 컴포넌트 floor_index 조회.
      - 두 부재의 floor_index 중 위층(max). R02 처럼 위층 작업 시 발생하는 접합도
        자연스럽게 위층으로 잡힘. R01 천장보 접합도 그 층(아래 모듈) floor 로
        잡힘(둘 다 같은 floor_index 의 모듈 모서리이므로).
      - 분할점 노드(source_comp_id=0) 는 z 좌표 폴백.

    Returns:
        (floor_rule_counts, r02_max, r05_max, bolt_h_max, baseplate_count).
          floor_rule_counts = {"<floor>": {"<rule_id>": count, ...}, ...}
          baseplate_count = 6DOF 고정점(베이스플레이트-볼트 접합 지점) 개수.
            "LEVEL 0 세모 표시" 자리 = z_min 모듈/코어 기둥 베이스 + 1층 floor_panel 코너.
        scene 이 없거나 빌드 실패 시 ({}, 0, 0, 0, 0).
    """
    if scene is None:
        return {}, 0, 0, 0, 0
    try:
        from modular_3d.analysis.topology import build_analysis_model
        from modular_3d.analysis.ops_builder import build_ops_model
    except Exception:
        return {}, 0, 0, 0, 0
    try:
        # 토폴로지 공유 제공자 경유 — 실패 시 기존 빌드 폴백(동작 보존).
        try:
            from modular_3d.analysis.model_provider import get_analysis_model
            am = get_analysis_model(scene)
        except Exception:
            am = build_analysis_model(scene)
        om = build_ops_model(am, scene=scene)
    except Exception:
        return {}, 0, 0, 0, 0
    if getattr(om, 'spec', None) is None:
        return {}, 0, 0, 0, 0

    # 노드 tag → (source_comp_id, z 좌표)
    node_info: Dict[int, Tuple[int, float]] = {}
    # 노드 tag → (x, y, z) — 중복 dedupe 용 xy 버킷 산정에 사용
    node_xy: Dict[int, Tuple[float, float, float]] = {}
    for n in om.spec.nodes:
        node_info[int(n.tag)] = (int(getattr(n, 'source_comp_id', 0) or 0),
                                 float(n.coord[2]))
        node_xy[int(n.tag)] = (float(n.coord[0]), float(n.coord[1]),
                               float(n.coord[2]))

    # 컴포넌트 id → floor_index (층 개수 산정용)
    comp_floor: Dict[int, int] = {}
    try:
        comp_iter = scene.components.values() if hasattr(scene.components, 'values') else scene.components
    except Exception:
        comp_iter = []
    for c in comp_iter:
        try:
            comp_floor[int(c.id)] = int(getattr(c, 'floor_index', 0) or 0)
        except Exception:
            continue

    # LEVEL 경계 산정: FLOOR_HEIGHT (=3420mm, 모듈+갭) 기준. 메인 프로그램 상수 사용.
    try:
        from modular_3d.analysis.constants import FLOOR_HEIGHT
        H = float(FLOOR_HEIGHT)
    except Exception:
        H = float(floor_height_mm) + 20.0   # 폴백: 모듈 height + 갭 추정

    # 층 개수 = 모듈/수직3층모듈 의 최대 floor_index + 1 (+ 옥상 1)
    max_floor = max(comp_floor.values(), default=0)
    n_floors = max(1, max_floor + 1)   # 최소 1층

    BOLT_HORIZ_RULES = {
        'R01_mod_mod_h', 'R03_panel_mod', 'R04_panel_panel',
        'R06_wall_mod',  'R07_wall_wall_h', 'R08_wall_cant', 'R09_core',
    }

    from collections import defaultdict
    floor_rule: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # ── 중복 결합 dedupe ──
    # 같은 R-규칙 안에서 같은 (xy 버킷, z) 위치 결합은 하나로 합침.
    # 버킷 _XY_BUCKET = 100mm — 미세 오프셋(20mm) 복제 흡수.
    # 키: (rule_id, floor, xy_bucket_정렬쌍, z_쌍)
    _XY_BUCKET = 100.0
    seen_joints: set = set()

    def _bucket(v: float) -> int:
        return int(round(v / _XY_BUCKET))

    # ── 중복 모듈 검출 (위치 기반) ──
    # 같은 floor_index 모듈끼리 xy position 이 _POS_DUP_TOL 이내면 "듀얼 트럭
    # 배송된 하나의 큰 모듈" 로 간주 → 첫 번째만 canonical, 나머지는 duplicate.
    # R02 카운트 시 duplicate 측 노드가 끼면 skip → 한 모듈 분량만 카운트.
    _POS_DUP_TOL = 100.0   # mm
    duplicates: Set[int] = set()
    mods_by_floor: Dict[int, List] = defaultdict(list)
    try:
        from modular_3d.model import ComponentType as _CT
        _CT_MODULE = _CT.MODULE
    except Exception:
        _CT_MODULE = None
    for c in scene.components.values():
        ct = getattr(c, 'comp_type', None)
        if _CT_MODULE is not None and ct != _CT_MODULE:
            continue
        mods_by_floor[int(getattr(c, 'floor_index', 0) or 0)].append(c)
    for floor_idx, mods in mods_by_floor.items():
        canonicals = []
        for m in mods:
            mp = getattr(m, 'position', None)
            if mp is None:
                continue
            mx_, my_ = float(mp[0]), float(mp[1])
            is_dup = False
            for prev in canonicals:
                pp = prev.position
                if (abs(mx_ - float(pp[0])) < _POS_DUP_TOL
                        and abs(my_ - float(pp[1])) < _POS_DUP_TOL):
                    duplicates.add(int(m.id))
                    is_dup = True
                    break
            if not is_dup:
                canonicals.append(m)

    # 컴포넌트 id → comp_type (수직3층모듈 식별용)
    comp_type_map: Dict[int, str] = {}
    for c in comp_iter:
        try:
            comp_type_map[int(c.id)] = str(getattr(c, 'comp_type', '') or '')
        except Exception:
            continue

    for rec in om.spec.equal_dofs:
        rid = str(rec.rule_id or '')
        # ── R03 중복 제거 ──
        # R03 은 패널-모듈 결합 시 중간 노드 N1 을 끼우고 두 단계로 등록:
        #   ① P ↔ N1 (kind='panel_module_vert')   — 수직 핀
        #   ② N1 ↔ M (kind='panel_module_corner') — 수평 핀
        # 분석상으론 2개의 equalDOF 이지만 "물리적으로 1개의 접합 작업".
        # 공정 카운트에서는 ① 을 제외해 1접합=1카운트로 맞춤.
        if rid == 'R03_panel_mod' and str(getattr(rec, 'kind', '')) == 'panel_module_vert':
            continue
        # ── R09 구버전 호환 — 'core_proj_to_line' 제외 ──
        # [2026-06-03 코어 선 분할 도입] 이전엔 사영점을 코어 선 끝점 n1 에 6 DOF
        # 강결로 묶는 "글루" 결합(실제 시공 공정에 없는 모델링 인공물)이라 카운트
        # 제외했다. 현재 빌드는 코어 선을 사영점에서 분할하므로 이 kind 를 더 이상
        # 생성하지 않고 실제 접합인 'core_edge' 로 대체한다. 옛 저장본 spec 로드
        # 호환을 위해 가드만 유지(현재 빌드에선 항상 거짓).
        if rid == 'R09_core' and str(getattr(rec, 'kind', '')) == 'core_proj_to_line':
            continue
        m_id, mz = node_info.get(int(rec.master), (0, 0.0))
        s_id, sz = node_info.get(int(rec.slave), (0, 0.0))
        # ── 수직3층 모듈 내부 R02 제거 ──
        # vertical_module 은 공장에서 3층 일체로 제작된 단일 컴포넌트.
        # 그 내부의 level 0↔1, 1↔2 결합은 분석 모델에서 R02 (vstack) 으로
        # 자동 등록되지만 시공상으론 강봉 접합 작업이 없다(공장 출고 시 이미 일체화됨).
        # 양 노드가 동일 컴포넌트(vert_module) 소속이면 카운트에서 제외.
        if rid == 'R02_mod_mod_v' and m_id == s_id and m_id != 0:
            ct = comp_type_map.get(m_id, '')
            if ct in ('vertical_module',):
                continue
        # ── LEVEL 매핑: 사용자 정의 규칙 ──
        # · 양 노드 z 중 위쪽(max z) 기준으로 LEVEL 구간 산정.
        # · R01 (모듈 수평): 경계 z 는 아래층에 포함 → 1층 천장 z=H 의 R01 은 1층.
        # · R02~R09: 경계 z 는 위층에 포함 → R02 강봉이 z=H 에서 시작 → 2층.
        z_for_floor = max(mz, sz)
        f = _level_of_joint(z_for_floor, n_floors, rid, H)
        # ── xy 버킷 dedupe ──
        # 같은 R-규칙 + 같은 층 + 거의 같은 xy 위치(±100mm) 의 결합은
        # 하나의 물리적 접합으로 간주. master/slave 정렬해서 양방향 동등.
        mx, my, _mz = node_xy.get(int(rec.master), (0.0, 0.0, 0.0))
        sx, sy, _sz = node_xy.get(int(rec.slave),  (0.0, 0.0, 0.0))
        xy_m = (_bucket(mx), _bucket(my))
        xy_s = (_bucket(sx), _bucket(sy))
        # ── R02 중복 모듈 제외 ──
        # 같은 floor_index 에 위치가 거의 같은 모듈이 여러 개 등록되면
        # (듀얼 트럭 배송 / 모델링 중복) duplicate 모듈쪽 노드의 R02 는 skip.
        # → 한 모듈 분량의 strap 만 카운트.
        if rid == 'R02_mod_mod_v':
            if m_id in duplicates or s_id in duplicates:
                continue
        ep_a = (xy_m[0], xy_m[1], round(_mz, 1))
        ep_b = (xy_s[0], xy_s[1], round(_sz, 1))
        endpoints = tuple(sorted([ep_a, ep_b]))   # 방향 무관
        joint_key = (rid, f, endpoints)
        if joint_key in seen_joints:
            continue
        seen_joints.add(joint_key)
        floor_rule[f][rid] += 1
        # R10/R11(사용자 추가)·legacy_auto 도 그대로 들어감 — html 측에서 필요시 필터.

    # 호환용 max-per-floor (기존 키)
    def _row_bolt_h(d: Dict[str, int]) -> int:
        return sum(d.get(r, 0) for r in BOLT_HORIZ_RULES)
    r02_max  = max((d.get('R02_mod_mod_v', 0) for d in floor_rule.values()), default=0)
    r05_max  = max((d.get('R05_wall_floor_v', 0) for d in floor_rule.values()), default=0)
    bolt_max = max((_row_bolt_h(d) for d in floor_rule.values()), default=0)

    # ── 베이스플레이트 볼트접합 지점 카운트 ──
    # _step_fix_base_nodes + _step_fix_first_floor_panel_nodes 가 6DOF fix 등록.
    # 원본 fix 집합엔 다음이 모두 들어있음:
    #   (1) 모듈 column base (z_min)             → 베이스볼트 ✓ 카운트
    #   (2) 코어 column base (z=0)               → RC 골조, 베이스볼트 작업 없음 ✗
    #   (3) 코어벽 셸 하단 노드 (m.kind=='shell')→ RC ✗
    #   (4) 1층 floor_panel 코너                 → 베이스볼트 ✓
    #   (5) 다이어프램 master 노드               → 해석 인공물 ✗
    # → (2)(3)(5) 는 카운트에서 제외.
    #
    # 필터 조건:
    #   · 노드의 source_comp_id 가 코어/코어슬래브 컴포넌트면 skip (RC)
    #   · 노드 role 에 'diaphragm' 포함되면 skip (다이어프램 master)
    core_comp_ids: Set[int] = set()
    for c in scene.components.values():
        ct = str(getattr(c, 'comp_type', '') or '')
        if ct.endswith('CORE') or ct.endswith('CORE_SLAB') \
                or 'core' in ct.lower() or 'CORE' in ct:
            core_comp_ids.add(int(c.id))
    # 노드 tag → (source_comp_id, role)
    node_meta: Dict[int, Tuple[int, str]] = {}
    for n in om.spec.nodes:
        node_meta[int(n.tag)] = (
            int(getattr(n, 'source_comp_id', 0) or 0),
            str(getattr(n, 'role', '') or ''),
        )
    baseplate_count = 0
    for fx in om.spec.fixes:
        dofs = getattr(fx, 'dofs', None)
        if not isinstance(dofs, (tuple, list)) or len(dofs) < 3:
            continue
        if not all(int(d) == 1 for d in dofs[:3]):
            continue
        cid, role = node_meta.get(int(fx.tag), (0, ''))
        # (2)(3) 코어 RC — 베이스볼트 작업 없음
        if cid in core_comp_ids:
            continue
        # (5) 다이어프램 master — 해석 인공물
        if 'diaphragm' in role.lower():
            continue
        baseplate_count += 1

    # 외부 출력용 정형: 키를 문자열로 (JSON 호환)
    floor_counts_out: Dict[str, Dict[str, int]] = {
        str(f): dict(d) for f, d in floor_rule.items()
    }
    return floor_counts_out, int(r02_max), int(r05_max), int(bolt_max), int(baseplate_count)


# ── summary 빌드 ───────────────────────────────────────────
def _is_module_kind(comp: Component) -> bool:
    return isinstance(comp, (Module, Vertical3Module))


def _build_summary(
    comps: List[Component],
    module_weight_t_max: Optional[float],
    basement_floors: int,
) -> Dict[str, Any]:
    modules = [c for c in comps if _is_module_kind(c)]
    if not modules:
        return {
            "floors_above_ground": 0,
            "modules_total": 0,
            "modules_per_floor": 0,
            "footprint_area_m2": 0.0,
            "core_area_per_floor_m2": 0.0,
            "vertical_joints_total": 0,
            "horizontal_joints_total": 0,
            "module_max_weight_t": float(module_weight_t_max or 0.0),
            "basement_floors": int(basement_floors),
        }

    # 층 범위
    spans = [_module_floor_span(c) for c in modules]
    floors_above_ground = max(s[1] for s in spans) + 1

    # modules_per_floor — floor 0 에서 그 층을 점유하는 모듈 수 (수직모듈 포함)
    floor0_present = [
        c for c, s in zip(modules, spans) if s[0] <= 0 <= s[1]
    ]
    modules_per_floor = len(floor0_present)
    modules_total = len(modules)

    # footprint — floor 0 모듈 + floor 0 바닥패널의 AABB footprint 합(m²)
    footprint_mm2 = 0.0
    for c in floor0_present:
        xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
        footprint_mm2 += (xmax - xmin) * (ymax - ymin)
    for c in comps:
        if isinstance(c, FloorPanel) and int(getattr(c, "floor_index", 0)) == 0:
            xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
            footprint_mm2 += (xmax - xmin) * (ymax - ymin)
    footprint_area_m2 = footprint_mm2 / 1.0e6

    # 코어 한 층 면적 — CoreSlab 한 개의 width×depth (m²). 여러 개면 합산.
    core_area_m2 = 0.0
    seen_core_floors: set = set()
    for c in comps:
        if isinstance(c, CoreSlab):
            f = int(getattr(c, "floor_index", 0))
            if f in seen_core_floors:
                continue
            seen_core_floors.add(f)
            xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
            core_area_m2 += (xmax - xmin) * (ymax - ymin) / 1.0e6
            # 한 층 면적이면 충분하므로 첫 발견 층의 값만 사용
            break

    # 수직접합: floor_index>0 인 *일반 모듈* × 4 (수직3층모듈 제외)
    vertical_joints_total = sum(
        4 for c in comps if isinstance(c, Module) and int(getattr(c, "floor_index", 0)) > 0
    )

    # 수평접합: 층별 인접 모듈쌍 합산 (수직모듈은 관통 층 모두에 포함)
    horizontal_joints_total = 0
    for f in range(floors_above_ground):
        boxes_f: List[Tuple[float, float, float, float]] = []
        for c, s in zip(modules, spans):
            if s[0] <= f <= s[1]:
                xmin, ymin, _zmin, xmax, ymax, _zmax = _aabb_xy_extent(c)
                boxes_f.append((xmin, ymin, xmax, ymax))
        horizontal_joints_total += _adjacent_pairs(boxes_f)

    return {
        "floors_above_ground": int(floors_above_ground),
        "modules_total": int(modules_total),
        "modules_per_floor": int(modules_per_floor),
        "footprint_area_m2": round(footprint_area_m2, 1),
        "core_area_per_floor_m2": round(core_area_m2, 1),
        "vertical_joints_total": int(vertical_joints_total),
        "horizontal_joints_total": int(horizontal_joints_total),
        "module_max_weight_t": float(module_weight_t_max or 0.0),
        "basement_floors": int(basement_floors),
    }


# ── 사용 타입 자동 추출 (모듈 + 패널 통합, multi-type 직접 주입용) ──
def _module_types_from_components(
    comps: Iterable[Component], default_weight_t: float = 10.0,
) -> List[Dict[str, Any]]:
    """배치설계 부재(모듈·바닥패널·벽패널) 의 사용 타입별 **한 층당 개수**.

    [2026-05-29] 모듈만 다루던 함수를 패널(floor_panel, struct_wall) 까지 확장.
    공정표 UI 의 "사용 타입별 구성" 표가 모듈·패널 모두를 한 표에서 보여주도록.

    출력 항목 형식 동일:
        {name, w, l, n, wt, kind}
    kind ∈ {'module', 'floor_panel', 'struct_wall'}
    n   = 그 타입이 가장 많이 등장하는 층의 개수 (한 층당 개수).
    wt  = 모듈은 weight_t_estimate, 패널은 보수적 기본값.

    [모듈 폭/길이 정규화]
    AABB 회전 결과를 (작은 변, 큰 변) 으로 정규화 — A타입의 90도 회전본을
    동일 버킷으로 묶음.

    팀원 importScene 은 단일 항목으로 압축하지만, 본 어댑터는 multi-type 으로
    직접 JS 전역(moduleTypes)에 세팅해 B2 를 우회한다.

    [중요] `n` 의 의미는 "한 층의 그 타입 개수".
    HTML 의 calc() 가 `총모듈 = 세대층 × 층당세대 × Σ(n)` 로 계산하므로,
    여기서 n 에 전체 합(총개수) 을 넣으면 층수배 부풀려진 모듈이 잡힌다.
    → 같은 타입 모듈이 여러 층에 적층된 경우(적층식 모듈러의 일반 케이스)
      층수로 나누어 한 층 평균을 반환. floor_index 가 가장 많이 차는 층의
      개수를 우선 채택 (적층식이면 보통 모든 층이 동일).

    [수직3층 모듈]
    Vertical3Module 은 한 컴포넌트가 3개 층을 점유 → 모든 층에 1개씩 추가
    되도록 floor_span 만큼 가중 카운트.
    """
    # ── kind 별로 버킷팅 ──
    # kind ∈ {'module', 'vertical_module', 'floor_panel', 'struct_wall'}
    # 수직3층 모듈(Vertical3Module) 은 일반 모듈과 시공 방식이 다르므로(공장 일체 출고)
    # 분리해 표 행에 명시. calc 의 모듈 합산엔 둘 다 포함됨.
    per_floor_buckets: Dict[Tuple[str, int, int], Dict[int, int]] = {}
    weight_by_key: Dict[Tuple[str, int, int], float] = {}
    # 동일 (kind, w, l) 안에서 높이가 다를 수 있으므로 max 를 채택 (보수치).
    height_by_key: Dict[Tuple[str, int, int], float] = {}

    # 패널 기본 중량 (모듈처럼 운송 어댑터가 따로 계산해 주지 않으므로 보수치)
    PANEL_WT = {'floor_panel': 1.0, 'struct_wall': 0.8}
    # 한국어 표시명 prefix (UI 표 첫 칼럼).
    KIND_LABEL = {
        'module':          '모듈',
        'vertical_module': '수직3층 모듈',
        'floor_panel':     '바닥패널',
        'struct_wall':     '벽패널',
    }

    def _kind_of(comp: Component) -> Optional[str]:
        if isinstance(comp, Vertical3Module):
            return 'vertical_module'
        if isinstance(comp, Module):
            return 'module'
        if isinstance(comp, FloorPanel):
            return 'floor_panel'
        if isinstance(comp, StructWall):
            return 'struct_wall'
        return None

    for c in comps:
        kind = _kind_of(c)
        if kind is None:
            continue
        xmin, ymin, zmin, xmax, ymax, zmax = _aabb_xy_extent(c)
        w_m = round((xmax - xmin) / 1000.0, 2)
        l_m = round((ymax - ymin) / 1000.0, 2)
        h_m = round((zmax - zmin) / 1000.0, 2)
        a, b = sorted([w_m, l_m])
        key = (kind, int(a * 1000), int(b * 1000))
        # 동일 키 안에서 가장 큰 높이를 채택 (보수치).
        if h_m > height_by_key.get(key, 0.0):
            height_by_key[key] = h_m
        # 모듈/수직3층 모듈은 span 까지, 패널은 단일 층만 점유.
        if kind in ('module', 'vertical_module'):
            f0, f1 = _module_floor_span(c)
        else:
            f_panel = int(getattr(c, "floor_index", 0) or 0)
            f0, f1 = f_panel, f_panel
        bucket = per_floor_buckets.setdefault(key, {})
        for f in range(f0, f1 + 1):
            bucket[f] = bucket.get(f, 0) + 1
        # 중량: 모듈/수직3층 모듈은 운송 어댑터 산정값, 패널은 기본값.
        if kind in ('module', 'vertical_module'):
            wt = float(getattr(c, "weight_t_estimate", 0.0) or 0.0)
            if wt > weight_by_key.get(key, 0.0):
                weight_by_key[key] = wt
        else:
            weight_by_key.setdefault(key, PANEL_WT.get(kind, 1.0))

    # ── 정렬: kind 순(모듈→수직3층 모듈→바닥패널→벽패널) → 개수 많은 순 ──
    KIND_ORDER = {'module': 0, 'vertical_module': 1, 'floor_panel': 2, 'struct_wall': 3}

    def _sort_key(item):
        (k, w, l), bf = item
        max_n = max(bf.values()) if bf else 0
        return (KIND_ORDER.get(k, 9), -max_n, w, l)

    types: List[Dict[str, Any]] = []
    # kind 별 알파벳 카운터 — 모듈 A,B,C... / 수직3층 모듈 A,B,... / 바닥패널 A,B,... / 벽패널 A,B,...
    kind_alpha: Dict[str, int] = {'module': 0, 'vertical_module': 0,
                                   'floor_panel': 0, 'struct_wall': 0}
    for (kind, w_mm, l_mm), by_floor in sorted(per_floor_buckets.items(), key=_sort_key):
        if not by_floor:
            continue
        n_per_floor = max(by_floor.values())
        alpha = chr(ord('A') + kind_alpha[kind])
        kind_alpha[kind] += 1
        label = f"{KIND_LABEL[kind]} {alpha}"
        types.append({
            "name": label,
            "w": w_mm / 1000.0,
            "l": l_mm / 1000.0,
            "h": float(height_by_key.get((kind, w_mm, l_mm)) or 0.0),
            "n": int(n_per_floor),
            "wt": float(weight_by_key.get((kind, w_mm, l_mm)) or default_weight_t),
            "kind": kind,
        })
    if not types:
        types = [{"name": "기본타입", "w": 3.4, "l": 6.0, "h": 3.4, "n": 0,
                  "wt": default_weight_t, "kind": "module"}]
    return types


# ── ProjectSettings 매핑 보조 ──────────────────────────────
def _region_key_for_html(region_city: Optional[str]) -> Optional[str]:
    """ProjectSettings.region_city("천안 충남") → HTML REGION_GADONG 키("천안") 추출.

    ProjectSettings 는 "도시 시도" 또는 단순 "도시" 형식을 모두 허용한다.
    HTML 키는 첫 단어(공백 앞)만 사용 — 매핑 실패 시 None 반환.
    """
    if not region_city:
        return None
    first = str(region_city).strip().split()[0] if str(region_city).strip() else ""
    return first or None


# ── 메인 진입점 ────────────────────────────────────────────
def build_scene_data(
    components: Iterable[Component],
    project_settings: Optional[Any] = None,
    module_weight_t_max: Optional[float] = None,
    module_default_weight_t: float = 10.0,
    scene: Optional[Any] = None,
) -> Dict[str, Any]:
    """씬 + 프로젝트 설정 → 팀원 HTML 이 받을 dict.

    Args:
        scene: 선택. 전달되면 접합부 설계 결과(R01-R09 자동 + joint_overrides
               반영) 기반 카테고리별 접합 카운트를 summary 에 추가 주입한다.
               (r02_per_floor / r05_per_floor / bolt_horiz_per_floor)
               미전달 시 공정표 HTML 이 vertical/horizontal joints_total fallback 사용.

    Returns:
        {
          "summary": {...},
          "components": [...],
          "module_types": [...],   # B2 우회용 (JS 전역에 직접 주입)
          "project": {             # Phase H — ProjectSettings 자동주입용
            "region_key": "천안" | None,
            "start_date": "2026-01-01" | None,
          }
        }
    """
    comps = list(components)

    # 지하 층수 — ProjectSettings 에 필드가 없으면 0.
    basement_floors = 0
    region_city = None
    start_date = None
    if project_settings is not None:
        basement_floors = int(getattr(project_settings, "basement_floors", 0) or 0)
        region_city = getattr(project_settings, "region_city", None)
        start_date = getattr(project_settings, "start_date", None)

    summary = _build_summary(
        comps, module_weight_t_max=module_weight_t_max, basement_floors=basement_floors,
    )
    # 접합부 설계 결과(자동 R01-R09 + joint_overrides) 카테고리별 카운트.
    # scene 인자가 들어오면 om.spec.equal_dofs 를 rule_id 별·층별로 집계.
    floor_rule_counts, r02_pf, r05_pf, bolt_pf, baseplate_cnt = _categorize_joints_by_rule(scene)
    # ── 진단 로그: 매트릭스 미수신 원인 추적용 (사용자 환경 디버그) ──
    try:
        _n_comps = len(comps)
        _n_floors = len(floor_rule_counts)
        _n_total = sum(sum(d.values()) for d in floor_rule_counts.values()) if floor_rule_counts else 0
    except Exception:
        pass
    summary["r02_per_floor"]        = r02_pf
    summary["r05_per_floor"]        = r05_pf
    summary["bolt_horiz_per_floor"] = bolt_pf
    # 신규: 층별 × R규칙별 카운트 매트릭스
    summary["floor_rule_counts"]    = floor_rule_counts
    # 1층 베이스플레이트-볼트 접합 지점 수 (LEVEL 0 세모 표시 = 6DOF fix 노드 합)
    summary["baseplate_count"]      = baseplate_cnt

    components_out = [_component_to_export_dict(c) for c in comps]
    module_types = _module_types_from_components(
        comps, default_weight_t=module_default_weight_t,
    )
    return {
        "summary": summary,
        "components": components_out,
        "module_types": module_types,
        "project": {
            "region_key": _region_key_for_html(region_city),
            "start_date": str(start_date) if start_date else None,
        },
    }


__all__ = [
    "build_scene_data",
]
