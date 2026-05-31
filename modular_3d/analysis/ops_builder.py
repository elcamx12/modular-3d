"""OpenSeesPy 모델 빌더.

AnalysisModel(자체 토폴로지) → OpenSees 3D 프레임 모델 변환.

생성하는 것:
  1) 노드: AnalysisModel.nodes 1:1 매핑 (ndf=6)
  2) 보-기둥 요소: AnalysisModel.members 1:1 → elasticBeamColumn (강접 가정 — 같은 컴포넌트 내부)
  3) 인터페이스 핀 링크: 컴포넌트 경계의 인접 노드쌍 → equalDOF 1 2 3 (병진만 묶고 회전은 자유)
  4) 강체 다이어프램: 각 층(z 레벨) 슬래브 평면을 rigidDiaphragm 으로 묶음
  5) 지점: 1층 z_min의 모든 기둥 base 6DOF 고정

[이력 2026-05-13] 가상 RC 코어(자동 cantilever column fallback) 는 완전 제거.
사용자 RC 코어 0 장 시 `solve_all_cases` 입구에서 RuntimeError 로 차단되므로
가상 fallback 자체가 도달 불가능. 관련 상수·CoreInfo·om.core 필드 모두 정리.

가정사항.md 와 1:1 대응. 상수 변경 시 가정사항.md 도 함께 갱신할 것.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
import numpy as np

import openseespy.opensees as ops

from modular_3d.analysis.topology import (
    AnalysisModel, AnalysisNode, AnalysisMember,
)
from modular_3d.analysis.constants import (
    STEEL_E_MPA, STEEL_G_MPA,
    CONCRETE_E_MPA,
    SHS_200x200x8,
    NODE_MERGE_TOL_MM, JOINT_BRIDGE_TOL_MM, JOINT_HORIZ_CONNECT_MAX_MM,
    FLOOR_HEIGHT,
)
from modular_3d.카탈로그.geometry import SPLIT_NODE_BASE_OFFSET

# ── RC 코어벽 (사용자 입력 wide-column) 단면·재료 ────────────────
# 설계서 §3.3: E=27000 MPa, G=E/2.4 (ν=0.2 가정), 단면은 멤버별 t×L.
RC_WALL_E_MPA = CONCRETE_E_MPA
RC_WALL_G_MPA = CONCRETE_E_MPA / 2.4

# [2026-05-13 가상 코어 제거] CORE_SIZE_MM/CORE_E_MPA/CORE_G_MPA/CORE_A/CORE_I/
# CORE_J 상수 모두 제거. 가상 cantilever column fallback 폐기로 사용처 없음.

# ── 인터페이스 검출 허용오차 ────────────────────────────────────
# 가정사항.md §4 와 일치.
# 함정: 토폴로지가 부재 '중심선' 노드만 만들기 때문에 두 적층 모듈의 인접 노드는
#       물리적 갭 20mm 이 아니라 (각 기둥 폭 200/2)*2 + 20mm = 220mm 떨어져 있다.
#       (수평 인접도 동일 이유로 220mm — JOINT_HORIZ_CONNECT_MAX_MM=225mm 사용)
#       따라서 수직 임계값도 225mm 로 두어야 검출된다.
_IFACE_VERT_DZ = JOINT_HORIZ_CONNECT_MAX_MM    # 225mm (= 200/2 × 2 + 20 + 여유)
_IFACE_VERT_DXY = 5.0
_IFACE_HORIZ_DZ = 5.0
_IFACE_HORIZ_DIST = JOINT_HORIZ_CONNECT_MAX_MM   # 225mm

# 같은 노드로 간주하는 매우 가까운 거리 (이건 같은 컴포넌트 내부에서만)
_SAME_NODE_TOL = NODE_MERGE_TOL_MM

# 강체 링크에 사용하는 elasticBeamColumn 의 매우 큰 강성 — 카탈로그 참조.
from modular_3d.카탈로그.rigid import (
    RIGID_E_MPA as _RIGID_E,
    RIGID_A_MM2 as _RIGID_A,
    RIGID_I_MM4 as _RIGID_I,
    RIGID_J_MM4 as _RIGID_J,
)
from modular_3d._utils.debug import dprint

# ── 데이터 구조 ────────────────────────────────────────────────


# [2026-05-13 가상 코어 제거] CoreInfo 데이터클래스 폐기. 사용자 RC 코어는
# 일반 보-기둥(wide-column) 으로 등록되어 om.beam_elements 에 포함됨.
# [2026-05-18 InterfaceLink 폐기] 자동 휴리스틱 결합 폐기로 함께 제거.
# 결합 정보는 spec.equal_dofs / spec.rigid_links / spec.diaphragms 에 보관.


@dataclass
class DiaphragmInfo:
    """한 층의 강체 다이어프램 정보."""
    z_level: float
    master_node: int            # OpenSees 노드 ID (음수 영역)
    slave_nodes: List[int]      # AnalysisNode id 들 (양수)


@dataclass
class OpsModel:
    """OpenSees에 등록된 모델의 시각화·검증용 메타데이터.

    OpenSees 인스턴스는 ops 모듈 전역 상태이므로 본 객체는 '무엇을 등록했는지'만 기록.
    """
    analysis_model: AnalysisModel
    # OpenSees 노드 ID 매핑 (AnalysisNode.id == OpenSees nodeTag 로 통일)
    node_tags: Dict[int, np.ndarray] = field(default_factory=dict)   # {tag: coord}
    # 일반 elasticBeamColumn 요소: tag → (n1, n2, kind, role)
    beam_elements: Dict[int, Tuple[int, int, str, str]] = field(default_factory=dict)
    # ShellMITC4 요소: tag → (n1, n2, n3, n4, role) — 시각화 (4 변 outline) 용.
    # beam_elements 와 별개로 4 노드 정보를 보관해 wireframe 뷰에서 슬래브·
    # 코어벽 패널의 외곽선을 모두 그릴 수 있도록 한다.
    shell_elements: Dict[int, Tuple[int, int, int, int, str]] = field(default_factory=dict)
    # AnalysisMember.id → OpenSees element tag (Phase 2 부재력 추출용)
    member_to_ele_tag: Dict[int, int] = field(default_factory=dict)
    # 분할된 보의 mid → sub-element tag 리스트
    # (joint_rules 가 보 분할 결합 등록 시 채움.)
    # load_calculator 가 자중 적용 시 모든 sub-element 에 분포하중 적용.
    member_to_split_ele_tags: Dict[int, List[int]] = field(default_factory=dict)
    # RC 코어 column 요소 tag 리스트 (자중 적용 + 단면력 추출용)
    core_ele_tags: List[int] = field(default_factory=list)
    # [2026-05-13 가상 코어 제거] OpsModel.core 필드 폐기.
    # [2026-05-18 interface_links 폐기] joint_rules 결합은 spec 에 직접 기록.
    # [2026-05-18] 빌드 중 OpenSees 등록 실패 수집 채널 (fix / equalDOF /
    # rigidDiaphragm 공통). 실패 1 건이라도 있으면 self_check 가 critical
    # 격상해 사용자에게 가시화. 이전엔 print 한 줄로만 보고되어 묻혔다.
    # 항목 = (관련 노드 id, '카테고리: 상세 메시지') 형태.
    registration_failures: List[Tuple[int, str]] = field(default_factory=list)
    # [성능 2026-05-18] self_check 용 lazy 캐시 — member_nodes 집합.
    # build 1 회당 self_check 1 회면 빌드 비용과 동일하지만, 디버그 시
    # self_check 를 여러 번 호출하는 흐름에서 즉시 효과.
    _self_check_member_nodes_cache: Optional[Set[int]] = field(
        default=None, repr=False
    )
    # 다이어프램
    diaphragms: List[DiaphragmInfo] = field(default_factory=list)
    # 지점 노드 (6DOF 고정)
    fixed_nodes: List[int] = field(default_factory=list)
    # 베이스 z 레벨
    base_z: float = 0.0
    # equalDOF 의 slave 측 노드 집합 — joint_rules 가 같은 노드를 두 번 slave 로
    # 잡지 않도록 중복 검사 용도. (Penalty handler 가 과구속을 견디는 가정이라
    # 절대 차단은 아니고 룰 내부 매칭 순환 방지가 주 목적.)
    # [정책 2026-05-18 의미 명확화]
    # 옛 docstring 은 "다이어프램 slave 제외용" 이었으나 현재 다이어프램 빌더는
    # 본 집합 대신 om.fixed_nodes 만 보고 base-fix 노드를 제외한다. 즉 본 집합은
    # **다이어프램과 무관**. 다이어프램 빌더 재작성 시 본 집합을 다시 필터로 쓰면
    # 회기 막판에 봤던 "다이어프램 선 일부 누락" 버그가 재발하니 주의.
    constrained_node_ids: Set[int] = field(default_factory=set)
    # (2026-05-12 명세통합 Phase 3) ModelSpec 동시 출력.
    # build_ops_model 의 각 _step_* 가 OpsModel 에 등록할 때마다 같은 알갱이를
    # ModelSpec 에도 기록 → 두 객체가 거울 관계 유지.
    # 시각화·검증 단계에서 spec 소비. 등록기 자체는 Phase 5 에서 spec 기반 전환.
    spec: Optional[object] = None
    # 접합부 오버라이드 (2026-05-25) — 사용자가 변경/제거/추가한 컴포넌트 간 접합.
    # build_ops_model 이 scene.joint_overrides 를 복사해 부착하면 joint_rules 의
    # 등록 게이트(_resolve_override_dofs)가 참조한다. 비어 있으면 자동 규칙만 동작.
    joint_overrides: List = field(default_factory=list)

    # ── 디버그 ────────────────────────────────────────────────
    def summary(self) -> str:
        nb = sum(1 for (_, _, k, _) in self.beam_elements.values() if k == 'beam')
        nc = sum(1 for (_, _, k, _) in self.beam_elements.values() if k == 'column')
        return "\n".join([
            "=== OpsModel 요약 ===",
            f"  노드(일반)     : {len(self.node_tags)}",
            f"  요소(보)       : {nb}",
            f"  요소(기둥)     : {nc}",
            f"  다이어프램     : {len(self.diaphragms)}",
            f"  고정지점       : {len(self.fixed_nodes)}",
            f"  RC 코어 부재   : {len(self.core_ele_tags)}장",
        ])


# ── 인터페이스 검출 ────────────────────────────────────────────



# ── OpenSees 빌드 ──────────────────────────────────────────────


def _section_props(member: AnalysisMember) -> Tuple[float, float, float, float]:
    """부재 단면 (A, Iy, Iz, J) 반환.

    [분기]
    - role='core_column' (RC 코어벽 wide-column): 단면 = section_w(L) × section_h(t)
      솔리드 직사각형. A=L·t, I_strong=(t·L³)/12, I_weak=(L·t³)/12, J=근사식.
      OpenSees 의 부재 로컬 축 정의에 따라 Iy/Iz 매핑:
        - 기둥(국부 x = 글로벌 z) + vecxz=(1,0,0) → 국부 y 는 (-vecxz × x̂) 방향.
        - 강축 모멘트는 약축 I 와 짝지어지지 않도록 둘 다 정직하게 넣어둠 (§3.3).
    - 그 외 (강재 SHS 200×200×8): 기존 SHS_200x200x8 사용.
    """
    if member.role == 'core_column':
        L = float(member.section_w)
        t = float(member.section_h)
        A = L * t
        I_strong = t * (L ** 3) / 12.0   # 강축
        I_weak = L * (t ** 3) / 12.0     # 약축
        # 직사각형 비틀림 상수 — 근사식 J ≈ β · b·h³ (b = min, h = max).
        # β = (1/3) - 0.21·(b/h)·(1 - (b/h)⁴/12) (Roark 표 10.7, 길쭉한 단면).
        b = min(L, t)
        hh = max(L, t)
        beta = (1.0 / 3.0) - 0.21 * (b / hh) * (1.0 - (b / hh) ** 4 / 12.0)
        J = beta * b * (hh ** 3)
        return A, I_strong, I_weak, J
    if member.role == 'core_slab_beam':
        # 코어 슬래브 보 격자 — 폭(section_w=tributary) × 높이(section_h=두께)
        b = float(member.section_w)
        hh = float(member.section_h)
        A = b * hh
        Iy = b * (hh ** 3) / 12.0       # 슬래브 휨 (수평축 기준, 약축)
        Iz = hh * (b ** 3) / 12.0       # 슬래브 면내 (수직축 기준, 강축)
        b_min = min(b, hh)
        h_max = max(b, hh)
        beta = (1.0 / 3.0) - 0.21 * (b_min / h_max) * (1.0 - (b_min / h_max) ** 4 / 12.0)
        J = beta * b_min * (h_max ** 3)
        return A, Iy, Iz, J
    if member.role in ('core_truss_v', 'core_truss_h', 'core_truss_d'):
        # [정책 2026-05-17] 등가 트러스 격자 부재. Truss element 라 A 만 사용,
        # Iy/Iz/J 는 0 으로 둠. A = t × b (section_w × section_h) — _extract_struct_core
        # 가 부재별 등가 단면 폭/높이를 계산해 넣음.
        A = float(member.section_w) * float(member.section_h)
        return A, 0.0, 0.0, 0.0
    # [2026-05-31 단면 설계 수렴] 배정 단면(design_section)이 *있을 때만* 그 강성을 쓴다.
    #   - 단면 설계 탭의 수렴이 그룹 선정 단면을 부재에 write-back 한 경우만 해당.
    #   - 미설계(design_section=None) 부재는 아래 기존 분기(공칭) 그대로 → 단면 설계 전
    #     구조해석/물량 결과 *완전* 불변. 일부 부재는 section_w/h/t 가 비표준(슬래브 격자
    #     등) 이라 그 치수로 SHS 물성을 계산하면 특이강성→analyze 실패(-3) 가 나므로,
    #     미설계 부재는 반드시 고정 공칭(SHS_200x200x8)을 쓴다.
    ds = getattr(member, 'design_section', None)
    if ds is not None:
        # H형강 단면 객체는 Ix(강축)·Iy(약축) 보유 → Iz 자리에 Ix 매핑(strength_check 일관).
        if getattr(member, 'section_type', 'shs') == 'h':
            return ds.A, ds.Iy, ds.Ix, ds.J
        # 각형강관(SHSSection) — Iy/Iz 그대로.
        return ds.A, ds.Iy, ds.Iz, ds.J
    # H형강 보 — 공칭 H200×200 강성(자동설계 전 초기값). 강축(Ix)을 Iz 로,
    # 약축(Iy)을 Iy 로 매핑 → 해석 Mz(로컬 z 모멘트)=강축, strength_check 와 일관.
    if member.kind == 'beam' and getattr(member, 'section_type', 'shs') == 'h':
        hs = _nominal_h_section()
        return hs.A, hs.Iy, hs.Ix, hs.J
    # 각형강관(SHS) — 고정 공칭(기존 동작). 단면 설계로 design_section 이 박히면 위에서 처리.
    p = SHS_200x200x8
    return p['A'], p['Iy'], p['Iz'], p['J']


_NOMINAL_H_SECTION = None


def _nominal_h_section():
    """공칭 H200×200 단면(없으면 카탈로그 최소). 해석 초기 강성용 — 1회 캐시."""
    global _NOMINAL_H_SECTION
    if _NOMINAL_H_SECTION is None:
        from modular_3d.카탈로그.h_sections import H_CATALOG
        pick = None
        for s in H_CATALOG:
            if abs(s.H - 200.0) < 1.0 and abs(s.B - 200.0) < 1.0:
                pick = s
                break
        _NOMINAL_H_SECTION = pick if pick is not None else H_CATALOG[0]
    return _NOMINAL_H_SECTION


def _geom_transf_tag(kind: str, vec_xz: Tuple[float, float, float]) -> int:
    """OpenSees geomTransf 등록 + 태그 반환.

    elasticBeamColumn 3D는 전역→국부 변환 위해 vecxz(국부 x-z 평면 위 임의 벡터) 가 필요.
    - 기둥(국부 x = 글로벌 z): vecxz = (1, 0, 0)
    - 보 글로벌-X: 국부 x = (1,0,0), vecxz = (0, 0, 1)
    - 보 글로벌-Y: 국부 x = (0,1,0), vecxz = (0, 0, 1)
    - 보 사선:    vecxz = (0, 0, 1) 보통
    동일 vecxz면 같은 transf 태그 재사용.
    """
    # 호출자가 정확한 vec을 넘기게 하고, 여기서는 단순 캐싱
    key = (kind, round(vec_xz[0], 6), round(vec_xz[1], 6), round(vec_xz[2], 6))
    if key in _geom_transf_cache:
        return _geom_transf_cache[key]
    tag = len(_geom_transf_cache) + 1
    ops.geomTransf('Linear', tag, *vec_xz)
    _geom_transf_cache[key] = tag
    return tag


_geom_transf_cache: Dict[Tuple, int] = {}


def _vecxz_for_member(coord1: np.ndarray, coord2: np.ndarray) -> Tuple[float, float, float]:
    """부재 축에 수직이면서 'z 축에 가까운' 임의 벡터 반환 → vecxz."""
    axis = coord2 - coord1
    L = np.linalg.norm(axis)
    if L < 1e-9:
        return (0.0, 0.0, 1.0)
    u = axis / L
    # z 축이 부재 축과 평행하면(=기둥) vecxz = (1,0,0)
    if abs(u[2]) > 0.99:
        return (1.0, 0.0, 0.0)
    # 그 외에는 (0,0,1) 사용 — z 가 부재축에 충분히 수직
    return (0.0, 0.0, 1.0)


def _step_init_ops_domain() -> None:
    """1) OpenSees domain 초기화 (3D 6DOF). 모든 이전 ops 상태 삭제."""
    ops.wipe()
    ops.model('basic', '-ndm', 3, '-ndf', 6)
    _geom_transf_cache.clear()


def _step_register_nodes(om: OpsModel, am: AnalysisModel) -> None:
    """2) AnalysisNode.id 를 OpenSees nodeTag 로 사용해 노드 등록.

    [Phase 3] spec.nodes 에도 동시 추가.
    """
    from modular_3d.analysis.model_spec import NodeRec as _NR
    for nid, n in am.nodes.items():
        x, y, z = float(n.coord[0]), float(n.coord[1]), float(n.coord[2])
        ops.node(nid, x, y, z)
        om.node_tags[nid] = n.coord.copy()
        if om.spec is not None:
            om.spec.nodes.append(_NR(
                tag=nid, coord=n.coord.copy(),
                role='', source_comp_id=n.source_comp_id,
            ))


_shell_section_cache: Dict[Tuple[float, float], int] = {}


def _get_shell_section_tag(thickness: float, e_factor: float = 1.0) -> int:
    """셸 두께 + E 배율별 ElasticMembranePlateSection 등록 + 태그 반환 (캐싱).

    e_factor=1.0: 정상 강성 (코어벽).
    e_factor=1e-3: 시각화 + 자중 전용 (코어 슬래브) — 다이어프램과 면내 강성
                    충돌을 피하기 위해 약화. 다이어프램이 실제 강체 역할.
    """
    t_key = round(float(thickness), 3)
    e_key = round(float(e_factor), 6)
    key = (t_key, e_key)
    if key in _shell_section_cache:
        return _shell_section_cache[key]
    sec_tag = 100000 + len(_shell_section_cache) + 1
    ops.section('ElasticMembranePlateSection', sec_tag,
                RC_WALL_E_MPA * e_factor, 0.2, t_key, 0.0)
    _shell_section_cache[key] = sec_tag
    return sec_tag


def _step_register_members(om: OpsModel, am: AnalysisModel) -> None:
    """3) AnalysisMember 1:1 → OpenSees 요소 등록.

    [분기]
    - kind='shell' → ShellMITC4 (4 노드) + ElasticMembranePlateSection
    - kind='truss' → Truss element (콘크리트 Elastic uniaxialMaterial)
    - 그 외(beam/column) → elasticBeamColumn
    - role='core_column' (구버전 wide-column 호환) → 콘크리트 E/G
    - 일반 강재 → 강재 E/G

    [2026-05-17 — 코어벽 등가 트러스]
    내부 격자 노드 중 elasticBeamColumn 이 하나도 연결 안 된 노드(=truss
    only)는 회전 자유도(RX,RY,RZ) 가 사용되지 않아 전역 강성 매트릭스에
    0 행이 생겨 특이 행렬 발생. 본 함수 끝부분에서 그 노드들을 자동으로
    ops.fix(0,0,0,1,1,1) 처리 — 평면 모델의 변위 자유도만 활용.
    """
    _shell_section_cache.clear()
    # [2026-05-17] 콘크리트 Truss element 용 uniaxialMaterial Elastic 등록.
    # 캐시: 매 빌드마다 ops.wipe 후 재등록되므로 고유 큰 tag 사용.
    truss_mat_tag = 9001
    try:
        ops.uniaxialMaterial('Elastic', truss_mat_tag, RC_WALL_E_MPA)
    except Exception:
        pass   # 이미 등록된 경우 무시
    # 노드별 elasticBeamColumn 연결 여부 — truss only 노드 식별용.
    node_has_beam: Dict[int, bool] = {}
    for _m in am.members.values():
        if _m.kind != 'truss' and _m.kind != 'shell':
            node_has_beam[_m.n1] = True
            node_has_beam[_m.n2] = True
    truss_only_nids: Set[int] = set()

    next_tag = 1
    for mid, m in am.members.items():
        # ── 셸 분기 (RC 코어벽 + 코어 슬래브) ──
        # 코어 슬래브 셸 노드를 다이어프램 slave 에서 별도 제외 처리해
        # 강성 중복 없이 정상 강성(E=27000 MPa) 그대로 등록.
        if m.kind == 'shell':
            sec_tag = _get_shell_section_tag(m.section_w)
            ops.element('ShellMITC4', next_tag,
                        m.n1, m.n2, m.n3, m.n4, sec_tag)
            om.beam_elements[next_tag] = (m.n1, m.n2, 'shell', m.role)
            # 4 노드 outline 시각화용 (코어벽 / 코어 슬래브).
            om.shell_elements[next_tag] = (m.n1, m.n2, m.n3, m.n4, m.role)
            om.member_to_ele_tag[mid] = next_tag
            if m.role.startswith('core_'):
                om.core_ele_tags.append(next_tag)
            # [Phase 3] spec 에 ShellRec 추가.
            if om.spec is not None:
                from modular_3d.analysis.model_spec import ShellRec as _SR
                om.spec.shells.append(_SR(
                    tag=next_tag, n1=m.n1, n2=m.n2, n3=m.n3, n4=m.n4,
                    role=m.role, thickness=float(m.section_w),
                    source_comp_ids=list(m.source_comp_ids),
                ))
            next_tag += 1
            continue
        # ── truss 분기 (2026-05-17 코어벽 등가 트러스) ──
        if m.kind == 'truss':
            A_t, _, _, _ = _section_props(m)
            ops.element('Truss', next_tag, m.n1, m.n2, A_t, truss_mat_tag)
            om.beam_elements[next_tag] = (m.n1, m.n2, 'truss', m.role)
            om.member_to_ele_tag[mid] = next_tag
            if m.role.startswith('core_'):
                om.core_ele_tags.append(next_tag)
            if om.spec is not None:
                from modular_3d.analysis.model_spec import BeamRec as _BR
                om.spec.beams.append(_BR(
                    tag=next_tag, n1=m.n1, n2=m.n2,
                    kind='truss', role=m.role,
                    section_w=float(m.section_w),
                    section_h=float(m.section_h),
                    section_t=float(m.section_t),
                    source_comp_ids=list(m.source_comp_ids),
                ))
            # truss only 노드 후보 — 끝나고 일괄 fix.
            if not node_has_beam.get(m.n1, False):
                truss_only_nids.add(m.n1)
            if not node_has_beam.get(m.n2, False):
                truss_only_nids.add(m.n2)
            next_tag += 1
            continue
        # ── frame 분기 (기존) ──
        c1 = am.nodes[m.n1].coord
        c2 = am.nodes[m.n2].coord
        A, Iy, Iz, J = _section_props(m)
        vec_xz = _vecxz_for_member(c1, c2)
        tt = _geom_transf_tag(m.kind, vec_xz)
        if m.role in ('core_column', 'core_slab_beam'):
            E_use, G_use = RC_WALL_E_MPA, RC_WALL_G_MPA
        else:
            E_use, G_use = STEEL_E_MPA, STEEL_G_MPA
        ops.element('elasticBeamColumn', next_tag, m.n1, m.n2,
                    A, E_use, G_use, J, Iy, Iz, tt)
        om.beam_elements[next_tag] = (m.n1, m.n2, m.kind, m.role)
        om.member_to_ele_tag[mid] = next_tag
        if m.role in ('core_column', 'core_slab_beam'):
            om.core_ele_tags.append(next_tag)
        # [Phase 3] spec 에 BeamRec 추가.
        if om.spec is not None:
            from modular_3d.analysis.model_spec import BeamRec as _BR
            om.spec.beams.append(_BR(
                tag=next_tag, n1=m.n1, n2=m.n2, kind=m.kind, role=m.role,
                section_w=float(m.section_w), section_h=float(m.section_h),
                section_t=float(m.section_t),
                source_comp_ids=list(m.source_comp_ids),
            ))
        next_tag += 1

    # [2026-05-18] truss-only 노드 자동 fix — 회전 + 면외(out-of-plane) 변위.
    # 코어벽 truss 격자는 한 평면(예: XZ) 안에서만 강성을 제공하므로 그 평면에
    # 수직인 방향(예: Y) 변위가 어떤 truss 에도 안 잡혀 mechanism 발생.
    # 노드별 truss 방향들을 분석해 stiffness 가 0 인 변위 자유도도 함께 fix.
    if truss_only_nids:
        from collections import defaultdict
        truss_dirs_at_node: Dict[int, List[np.ndarray]] = defaultdict(list)
        for tag, (n1, n2, kind, role) in om.beam_elements.items():
            if kind != 'truss':
                continue
            if n1 not in am.nodes or n2 not in am.nodes:
                continue
            c1 = am.nodes[n1].coord
            c2 = am.nodes[n2].coord
            v = c2 - c1
            L = float(np.linalg.norm(v))
            if L <= 0:
                continue
            u = v / L
            if n1 in truss_only_nids:
                truss_dirs_at_node[n1].append(u)
            if n2 in truss_only_nids:
                truss_dirs_at_node[n2].append(u)

        n_oop_fixed = 0
        for nid in truss_only_nids:
            dirs = truss_dirs_at_node.get(nid, [])
            # 각 축에 대해 어떤 방향도 그 축 성분이 비-0 이면 변위 잡힘
            fix_xyz = [1, 1, 1]
            for i in range(3):
                for u in dirs:
                    if abs(u[i]) > 1e-3:
                        fix_xyz[i] = 0   # stiffness 잡힘 → 자유 유지
                        break
            try:
                ops.fix(nid, fix_xyz[0], fix_xyz[1], fix_xyz[2], 1, 1, 1)
                # spec 거울 기록 — 검증기·물량 산출이 fix 알갱이를 볼 수 있도록.
                if om.spec is not None:
                    from modular_3d.analysis.model_spec import FixRec
                    om.spec.fixes.append(FixRec(
                        tag=nid,
                        dofs=(fix_xyz[0], fix_xyz[1], fix_xyz[2], 1, 1, 1),
                    ))
                if sum(fix_xyz) > 0:
                    n_oop_fixed += 1
            except Exception as e:
                dprint('ops_builder', f'[ops_builder] truss-only nid={nid} fix 실패: {e}')
                # self_check 가 가시화하도록 누적.
                om.registration_failures.append((nid, f'truss-only OOP fix: {e}'))
        dprint('ops_builder', f'[ops_builder] truss-only 노드 {len(truss_only_nids)}개 회전 fix, 그 중 {n_oop_fixed}개는 면외 변위도 자동 fix')


def _step_fix_base_nodes(om: OpsModel, am: AnalysisModel) -> None:
    """5) 베이스 지점 — z_min 의 모든 기둥 base + 코어 셸 하단 노드 → 6DOF 고정.

    (2026-05-12 추가) core_column wide-column 의 1F 베이스 노드(z=0) 도 추가
    고정. 모듈 베이스는 z=-100, 코어 베이스는 z=0 으로 절대값이 다르므로
    z_min 만 보면 코어 베이스가 누락됨.
    """
    if not am.nodes:
        return
    z_min = min(n.coord[2] for n in am.nodes.values())
    om.base_z = z_min
    base_set: Set[int] = set()
    # 코어 부재의 1F 베이스 z 추정 — 가장 작은 core_column z.
    core_min_z: Optional[float] = None
    for m in am.members.values():
        if m.role == 'core_column':
            for nid in (m.n1, m.n2):
                z = float(am.nodes[nid].coord[2])
                if core_min_z is None or z < core_min_z:
                    core_min_z = z
    for m in am.members.values():
        if m.kind == 'column':
            n1 = am.nodes[m.n1]
            n2 = am.nodes[m.n2]
            for n in (n1, n2):
                if abs(n.coord[2] - z_min) <= _SAME_NODE_TOL:
                    base_set.add(n.id)
                # 코어 column 의 1F 베이스 (core_min_z) 도 고정
                if (m.role == 'core_column' and core_min_z is not None
                        and abs(n.coord[2] - core_min_z) <= _SAME_NODE_TOL):
                    base_set.add(n.id)
        elif m.kind == 'shell':
            # 셸 4 노드 중 z_min 인 것은 코어벽 하단 → 6DOF 고정.
            for nid in (m.n1, m.n2, m.n3, m.n4):
                n = am.nodes[nid]
                if abs(n.coord[2] - z_min) <= _SAME_NODE_TOL:
                    base_set.add(n.id)
    from modular_3d.analysis.model_spec import FixRec as _FR
    for nid in sorted(base_set):
        ops.fix(nid, 1, 1, 1, 1, 1, 1)
        if om.spec is not None:
            om.spec.fixes.append(_FR(tag=nid, dofs=(1, 1, 1, 1, 1, 1)))
    om.fixed_nodes = sorted(base_set)
    if om.spec is not None:
        om.spec.base_z = om.base_z



def build_ops_model(analysis_model: AnalysisModel, scene=None) -> OpsModel:
    """AnalysisModel → OpenSees 3D 프레임 모델 등록.

    [Pipeline — 단계 D (2026-05-08 정리)]
    각 단계는 _step_* 함수로 분리. 새 단계 추가 시 본 함수에 한 줄만 추가.

    [인자 정책 2026-05-18]
    scene 은 시그니처상 기본값이 있으나 **사실상 필수**. joint_rules 의
    R01~R09 룰이 scene 정보(컴포넌트 그룹·canonical 좌표·접합 기록 등)에
    의존하므로 scene 없이 빌드하면 결합이 누락된 반쪽 모델이 나온다.
    None 으로 들어오면 즉시 ValueError 로 차단.

    부작용:
      - ops.wipe() 호출 후 model('basic', '-ndm', 3, '-ndf', 6) 으로 초기화
      - 노드/요소/제약/지점 모두 등록

    Returns:
        OpsModel — 시각화·검증·후속 단계용 메타데이터
    """
    if scene is None:
        raise ValueError(
            'build_ops_model: scene 인자는 필수입니다. '
            'joint_rules(R01~R09)가 scene 의 컴포넌트 좌표·그룹 정보를 사용합니다. '
            '회기 호환을 위해 시그니처 기본값(None)은 유지하지만 None 호출은 차단됩니다.'
        )
    # 1) ops domain 초기화
    _step_init_ops_domain()
    # [Stage E] solve_all_cases 가 같은 am 으로 build_ops_model 을 5회 반복 호출.
    # 이전 호출에서 보 분할 등이 am.nodes 에 추가한 split 노드를 제거 — 다음
    # 호출 시 토폴로지가 깨끗한 상태에서 시작하도록.
    # split / 가교 노드 ID 는 SPLIT_NODE_BASE_OFFSET(=10000)+ 영역이므로
    # 그 범위만 제거. (룰별 offset 은 모두 이 값 이상으로 통일.)
    split_nids = [nid for nid in analysis_model.nodes if nid >= SPLIT_NODE_BASE_OFFSET]
    for nid in split_nids:
        del analysis_model.nodes[nid]
    # [2026-05-18 C-2] split sub-member 도 같이 청소 — am.members 에 정식
    # 등록되도록 바뀐 정책의 부수효과. 5 회 호출 누적 방지.
    split_mids = [mid for mid, m in analysis_model.members.items()
                  if getattr(m, 'is_split_sub', False)]
    for mid in split_mids:
        del analysis_model.members[mid]
    # [성능 2026-05-18] role 인덱스 캐시 무효화 — sub 청소 후 재빌드 필요.
    if hasattr(analysis_model, 'invalidate_indices'):
        analysis_model.invalidate_indices()
    om = OpsModel(analysis_model=analysis_model)
    # (2026-05-12 명세통합 Phase 3) ModelSpec 동시 출력 시작.
    # 각 _step_* 가 ops 에 등록할 때마다 spec 에도 알갱이 추가.
    # 빌드 완료 후 spec.validate() 로 자체 점검.
    from modular_3d.analysis.model_spec import ModelSpec
    om.spec = ModelSpec()

    # 1-b) 종속 캔틸레버 루트 좌표 정렬 (선택 — scene 있을 때만)
    if scene is not None:
        _snap_dependent_cantilever_to_module(analysis_model, scene)

    # 2) 노드 등록
    _step_register_nodes(om, analysis_model)

    # 3) 보-기둥 요소 등록
    _step_register_members(om, analysis_model)

    # [2026-05-13 접합부조정탭 — 결합 정책 전환]
    # 자동 휴리스틱 결합(_step_apply_interface_links, _inject_cantilever_*,
    # _apply_panel_column_supports, _split_module_beams_*) 일괄 폐기 →
    # joint_rules 의 케이스별 명시 룰(R01~R09) 로 대체.
    # (2026-05-13 wide-column 전환으로 셸 코어 폐기, 모서리 결합기 불필요.)

    # 4-rules) 케이스별 명시 접합 룰.
    from modular_3d.analysis.joint_rules import (
        apply_all_joint_rules, apply_added_joints,
        remove_dangling_bridge_nodes)
    # [2026-05-25] 사용자 접합 오버라이드를 om 에 부착 — 룰 등록 게이트
    # (_resolve_override_dofs)가 등록 직전에 참조해 제거/강접/핀을 반영.
    om.joint_overrides = list(getattr(scene, 'joint_overrides', []) or [])
    apply_all_joint_rules(om)
    # 4-add) 사용자 신규 접합(kind='add') — 자동 룰 위에 추가. 같은 평면 위치
    # 모든 층에 자동 복제.
    apply_added_joints(om)
    # 4-clean) 직각접합 제거로 두 결합이 모두 빠져 허공에 남은 가교 중간노드(N1)를
    # 정리. 회전 자유도 자동 fix(6-c) 이전에 — fix 된 노드 제거 회피.
    remove_dangling_bridge_nodes(om)

    # 5) 베이스 지점 (z_min 기둥 base 노드 6DOF 고정) — 결합 아님, 유지.
    _step_fix_base_nodes(om, analysis_model)

    # 6-c) [2026-05-18] 가교 노드 회전 자유도 자동 fix.
    # panel_z_route / core_proj 노드는 핀(1,2,3) + 6 DOF 강결 페어의 중간 노드라
    # 자체 element 없이 equalDOF chain 으로만 묶임 → 회전 자유도가 강성 없음.
    # 회전은 의미 없으므로 강제 fix → mechanism 방지.
    if om.spec is not None:
        from modular_3d.analysis.model_spec import FixRec
        n_rot_fix = 0
        for nr in om.spec.nodes:
            if nr.role in ('panel_z_route', 'core_proj'):
                try:
                    ops.fix(nr.tag, 0, 0, 0, 1, 1, 1)
                    # spec 거울 기록.
                    om.spec.fixes.append(FixRec(
                        tag=nr.tag, dofs=(0, 0, 0, 1, 1, 1),
                    ))
                    n_rot_fix += 1
                except Exception as e:
                    # [B-3 옵션 1] silent pass → 누적 채널로 가시화.
                    om.registration_failures.append(
                        (nr.tag, f'z_route 회전 fix: {e}')
                    )
        dprint('ops_builder', f'[ops_builder] z_route 중간 노드 {n_rot_fix}개 회전 자유도 자동 fix')

    # 7) 강체 다이어프램 — 유지(다이어프램 시각화 필요).
    _build_diaphragms_and_core(om)

    # [Phase 3] spec 빌드 후 인덱스 갱신 — 와이어프레임·검증기가 빠른 노드 조회 가능.
    if om.spec is not None:
        om.spec.refresh_index()
        # [Phase 7] 검증 스위치 — spec self-consistency + OpenSees 실등록 대조.
        # Q5=가 (불일치 시 빌드 실패) 정책으로 strict=True.
        # critical 항목만 차단 — 결합 충돌 같은 비-critical 항목은 issues 누적만.
        try:
            self_res = om.spec.validate(strict=False)
            if self_res.critical:
                raise RuntimeError(
                    f'[build_ops_model] ModelSpec self-validate critical '
                    f'{len(self_res)} 건:\n  - '
                    + '\n  - '.join(self_res.issues[:10])
                )
            ops_res = om.spec.verify_against_opensees(tol_mm=1e-2, strict=True)
        except RuntimeError as e:
            # 검증 실패 시에도 빌드 자체는 끝났으므로 om 을 반환할 수 있음.
            # 정책상 RuntimeError 를 재발생시켜 호출자에게 알림.
            dprint('ops_builder', f'[ops_builder] 검증 실패: {e}')
            raise

    return om


def _snap_dependent_cantilever_to_module(am: AnalysisModel, scene) -> None:
    """종속 캔틸레버 보/슬래브의 한 끝 노드 좌표를 부모 모듈의 가까운 노드 좌표로
    덮어씀(같은 좌표 = 거리 0).

    효과:
      - _detect_interfaces 가 dxy<1, dz<1 → merged_overlap (6DOF, 모멘트 접합)
        으로 자연 매칭
      - coord_a == coord_b 라 시각화 라인 길이 0 → 보가 늘어 보이는 문제 X
      - 캔틸 부재 길이 약간 줄어듦(갭 + 인셋 ≈ 120mm) — 정확도 미미

    적용 대상:
      - CANTILEVER_BEAM 종속(sub_index>0): 모든 케이스
      - CANTILEVER_SLAB 종속(sub_index>0): 부모(sub_index=0)가 MODULE 인 경우만.
        부모가 FLOOR_PANEL 인 경우는 패널 위에 그대로 얹히므로 스냅 불필요.
    """
    from modular_3d.model import ComponentType
    if scene is None:
        return
    # group_id 별 부모(sub_index=0) 컴포넌트 타입 사전
    parent_type_by_gid: Dict[int, ComponentType] = {}
    for c in scene.components.values():
        if getattr(c, 'sub_index', 0) == 0:
            gid = getattr(c, 'group_id', 0)
            if gid > 0:
                parent_type_by_gid[gid] = c.comp_type

    for cid, comp in scene.components.items():
        ct = comp.comp_type
        if ct not in (ComponentType.CANTILEVER_BEAM, ComponentType.CANTILEVER_SLAB):
            continue
        if getattr(comp, 'sub_index', 0) == 0:
            continue  # 종속(sub_index>0)만 처리
        parent_gid = getattr(comp, 'group_id', 0)
        if parent_gid <= 0:
            continue
        # 캔틸레버 슬래브는 부모가 MODULE 일 때만 스냅 — 패널 종속은 패널이
        # 처리하므로 노드 이동 시 패널-슬래브 정합이 깨짐.
        if ct == ComponentType.CANTILEVER_SLAB:
            ptype = parent_type_by_gid.get(parent_gid)
            if ptype != ComponentType.MODULE:
                continue
        # 부모 모듈 노드 모음 — 캔틸 floor_index 와 일치하는 모듈을 1순위로
        # 사용 (옛 scene 의 z 값이 어긋나 있어도 의도한 부모 층에 스냅되도록).
        # 매칭 모듈 없으면 같은 group_id 의 모든 모듈로 폴백.
        cant_nids = am.comp_to_nodes.get(cid, [])
        cant_floor = getattr(comp, 'floor_index', -1)
        mod_nids = []
        # 1순위: parent_gid + 같은 floor_index + MODULE
        for c in scene.components.values():
            if (getattr(c, 'group_id', 0) == parent_gid
                    and getattr(c, 'sub_index', 0) == 0
                    and getattr(c, 'floor_index', -1) == cant_floor
                    and c.comp_type == ComponentType.MODULE):
                mod_nids.extend(am.comp_to_nodes.get(c.id, []))
        # 폴백: 같은 group_id 의 모든 모듈
        if not mod_nids:
            for c in scene.components.values():
                if (getattr(c, 'group_id', 0) == parent_gid
                        and getattr(c, 'sub_index', 0) == 0
                        and c.comp_type == ComponentType.MODULE):
                    mod_nids.extend(am.comp_to_nodes.get(c.id, []))
        if not cant_nids or not mod_nids:
            continue
        # 가장 가까운 (캔틸 노드, 모듈 노드) 페어 찾기
        best_d = float('inf')
        best_pair = None
        for cnid in cant_nids:
            cn_coord = am.nodes[cnid].coord
            for mnid in mod_nids:
                mn_coord = am.nodes[mnid].coord
                d = float(np.linalg.norm(cn_coord - mn_coord))
                if d < best_d:
                    best_d = d
                    best_pair = (cnid, mnid)
        if best_pair is None:
            print(f'[DBG snap-cant] comp#{cid} 부모 모듈 노드 페어 못찾음')
            continue
        if best_d < 1.0:
            print(f'[DBG snap-cant] comp#{cid} 이미 정렬됨(거리 {best_d:.1f}mm) — 건너뜀')
            continue
        if best_d > 400.0:
            print(f'[DBG snap-cant] comp#{cid} 거리 {best_d:.0f}mm > 400 — 정렬 건너뜀')
            dprint('n', f'  캔틸 노드 좌표: {[am.nodes[n].coord.tolist() for n in cant_nids]}')
            print(f'  모듈 노드 좌표(가장 가까운): {am.nodes[best_pair[1]].coord.tolist()}')
            continue
        cnid, mnid = best_pair
        # 캔틸 보 전체를 평행 이동 — 보 길이 유지하면서 한 끝이 모듈 노드와
        # 정확히 일치하게. 한 노드만 옮기면 보가 갭만큼 늘어나는 문제 회피.
        delta = am.nodes[mnid].coord - am.nodes[cnid].coord
        for nid in cant_nids:
            am.nodes[nid].coord = am.nodes[nid].coord + delta
        kind_label = ('캔틸레버보' if ct == ComponentType.CANTILEVER_BEAM
                      else '캔틸레버슬래브')
        dprint('ops_builder', f'[ops_builder] 종속 {kind_label} 평행 이동: comp#{cid} delta={delta.tolist()} (원래 거리 {best_d:.0f}mm)')


def _build_diaphragms_and_core(om: OpsModel) -> None:
    """컴포넌트별 rigidDiaphragm 등록 (2026-05-17 per-component 전환).

    [정책 2026-05-17 per-component 다이어프램]
    각 본체 컴포넌트(Module, Vertical3Module 의 각 층, FloorPanel, CoreSlab)가
    자신만의 바닥 다이어프램을 가진다. 종속 캔틸 슬래브는 부모의 다이어프램에
    합쳐진다. 천장은 다이어프램 없음(보 격자만으로 횡강성 표현).

    토폴로지 단계(`_build_diaphragms`)에서 `am.diaphragms` 를 미리 채우고
    master 노드도 `am.nodes` 에 등록했으므로, 본 함수는 단순히
    rigidDiaphragm + master fix 만 호출한다.

    [구버전 호환]
    `am.diaphragms` 가 비어 있으면 (예: 일부 테스트) 다이어프램 빌드를 스킵.
    """
    import os
    _DBG = os.environ.get('DEBUG_DIAPHRAGM', '0') == '1'

    am_local = om.analysis_model
    if am_local is None or not getattr(am_local, 'diaphragms', None):
        if _DBG:
            dprint('DBG-DIA', '[DBG-DIA] am.diaphragms 비어있음 → 다이어프램 등록 스킵')
        return

    # [정책 2026-05-18 split 노드 좌표 기반 자동 흡수 — 2 단계]
    # 1) 폴리곤 외접 박스 안 흡수 (본체 내부 노드)
    # 2) 1) 에서 어느 다이어프램에도 못 들어간 노드는 같은 z 다이어프램 중
    #    master xy 가 가장 가까운 곳에 강제 흡수 (split/z_route 분기 노드 처리)
    #
    # [2026-05-18 시도/롤백 기록]
    # 사용자 보고("본인 5+상대 2=7") 로 컴포넌트 멤버십 제한을 시도했으나
    # R01/R03 등으로 통합된 결합 노드가 양쪽 컴포넌트 다이어프램에 동시 묶이는
    # 역할을 하던 게 끊겨 matrix singular 회귀 발생. 일단 원복.
    # 정확한 해결은 결합 노드 매핑(consolidate 결과) 을 활용하는 별도 작업.
    from collections import defaultdict
    Z_BUCKET = 50.0
    # 사용자 결정 (2026-05-19): 200 → 100. 모듈 갭 20mm 미배치 케이스에서
    # 인접 모듈 코너까지 흡수되던 결함 해결. 본인 부재 박스 + 모듈 갭 정도만 포함.
    XY_TOL = 100.0
    nodes_by_z: Dict[int, List[int]] = defaultdict(list)
    for nid, n in am_local.nodes.items():
        if nid not in om.node_tags:
            continue
        zk = int(round(float(n.coord[2]) / Z_BUCKET))
        nodes_by_z[zk].append(nid)

    # 다이어프램별 흡수 결과 누적
    dia_slaves: Dict[int, set] = {}   # master_nid → set(slave nids)
    dia_zk: Dict[int, int] = {}        # master_nid → zk
    absorbed_nodes: set = set()        # 1 단계에서 흡수된 노드 (다중 흡수 방지)

    for d in am_local.diaphragms:
        master = int(d.master_node_id)
        zk = int(round(float(d.z) / Z_BUCKET))
        dia_zk[master] = zk
        candidates = nodes_by_z.get(zk, [])
        if d.polygon:
            pts = np.array([np.asarray(p)[:2] for p in d.polygon])
            xmin, ymin = pts.min(axis=0) - XY_TOL
            xmax, ymax = pts.max(axis=0) + XY_TOL
        else:
            xmin = ymin = -1e18
            xmax = ymax = 1e18
        merged = set(d.slave_node_ids)
        for nid in candidates:
            if nid == master:
                continue
            xy = am_local.nodes[nid].coord
            if xmin <= xy[0] <= xmax and ymin <= xy[1] <= ymax:
                merged.add(nid)
                absorbed_nodes.add(nid)
        dia_slaves[master] = merged

    # 2 단계: orphan 노드(어느 다이어프램 박스에도 안 든 것)를 가장 가까운
    # 같은 z 다이어프램에 강제 흡수.
    master_coords: Dict[int, np.ndarray] = {
        d.master_node_id: am_local.nodes[d.master_node_id].coord
        for d in am_local.diaphragms
    }
    masters_by_zk: Dict[int, List[int]] = defaultdict(list)
    for mnid, mz in dia_zk.items():
        masters_by_zk[mz].append(mnid)
    fixed_set_2nd = set(om.fixed_nodes)
    for nid, n in am_local.nodes.items():
        if nid in absorbed_nodes or nid not in om.node_tags:
            continue
        if nid in master_coords:
            continue
        if nid in fixed_set_2nd:
            continue
        zk = int(round(float(n.coord[2]) / Z_BUCKET))
        best_master = None
        best_dist = float('inf')
        for mnid in masters_by_zk.get(zk, ()):
            mc = master_coords[mnid]
            dist = (mc[0] - n.coord[0]) ** 2 + (mc[1] - n.coord[1]) ** 2
            if dist < best_dist:
                best_dist = dist
                best_master = mnid
        if best_master is not None:
            dia_slaves[best_master].add(nid)

    n_dia = 0
    for d in am_local.diaphragms:
        master = int(d.master_node_id)
        d_slaves_full = list(dia_slaves.get(master, set(d.slave_node_ids)))
        # master 가 ops.node 에 등록돼 있는지 확인 (am.nodes 에 있으면 _step_register_nodes 에서 등록됨)
        if master not in om.node_tags:
            if _DBG:
                dprint('DBG-DIA', f'[DBG-DIA] master {master} 미등록 — 스킵 (comp_id={d.comp_id})')
            continue
        # [정책 2026-05-18 시각화-해석 분리]
        # 시각화용 슬레이브(viz_slaves)는 base-fix 노드 포함 → spec.diaphragms 에
        # 그대로 기록(viewer 가 41 개 모두 그릴 수 있도록).
        # 해석용 슬레이브(ops_slaves)는 base-fix 노드 제외 → ops.rigidDiaphragm 에
        # 넣는 페널티 행렬 안정. fix(1,1,1,1,1,1) 슬레이브를 rigidDiaphragm 에
        # 넣으면 같은 자유도에 페널티가 이중으로 걸려 행렬 특이가 됨.
        viz_slaves = [s for s in d_slaves_full
                      if s in om.node_tags and s != master]
        if len(viz_slaves) < 3:
            if _DBG:
                dprint('DBG-DIA', f'[DBG-DIA] comp_id={d.comp_id} z={d.z:.0f} viz 슬레이브 {len(viz_slaves)}개 → 스킵')
            continue
        fixed_set = set(om.fixed_nodes)
        ops_slaves = [s for s in viz_slaves if s not in fixed_set]

        if len(ops_slaves) >= 2:
            # 정상 다이어프램: rigidDiaphragm + master 면외 fix.
            try:
                ops.rigidDiaphragm(3, master, *ops_slaves)
                ops.fix(master, 0, 0, 1, 1, 1, 0)
            except Exception as e:
                om.registration_failures.append(
                    (master, f'rigidDiaphragm slaves={len(ops_slaves)}: {e}'))
            master_fix_dofs = (0, 0, 1, 1, 1, 0)
        else:
            # 1F 등 모든 슬레이브가 이미 base-fix → master 도 fully fix 로 처리
            # (rigidDiaphragm 안 호출, 해석 안정).
            try:
                ops.fix(master, 1, 1, 1, 1, 1, 1)
            except Exception as e:
                om.registration_failures.append(
                    (master, f'diaphragm master fully fix: {e}'))
            master_fix_dofs = (1, 1, 1, 1, 1, 1)

        # spec 기록 — 시각화용 viz_slaves + 해석용 ops_slaves 분리 보관.
        if om.spec is not None:
            from modular_3d.analysis.model_spec import (
                DiaphragmRec as _DR_d, FixRec as _FR_m,
            )
            om.spec.diaphragms.append(_DR_d(
                master=master, slaves=list(viz_slaves), z_level=float(d.z),
                rule_id='diaphragm_component',
                hidden=bool(getattr(d, 'hidden', False)),
                ops_slaves=list(ops_slaves),
            ))
            om.spec.fixes.append(_FR_m(
                tag=master, dofs=master_fix_dofs,
            ))
        om.diaphragms.append(DiaphragmInfo(
            z_level=float(d.z), master_node=master, slave_nodes=viz_slaves,
        ))
        # master 노드의 NodeRec role 보정 — _step_register_nodes 가 role='' 로 등록했으므로
        # diaphragm_master 로 표시(있는 경우).
        if om.spec is not None:
            for nr in om.spec.nodes:
                if nr.tag == master:
                    nr.role = 'diaphragm_master'
                    break
        n_dia += 1

    dprint('ops_builder', f'[ops_builder][diaphragm] 컴포넌트별 다이어프램 {n_dia}개 등록')


# ── 통계 / 자체 점검 ──────────────────────────────────────────


@dataclass
class SelfCheckResult:
    """self_check 의 구조화된 결과.

    is_critical=True 이면 mechanism 위험 → viewer 가 문제 노드/부재를 빨강 강조.
    list-호환 인터페이스 제공: 기존 `for s in issues`, `if issues:`, `len(issues)` 등 그대로 작동.
    """
    issues: List[str] = field(default_factory=list)
    problem_node_ids: Set[int] = field(default_factory=set)
    is_critical: bool = False

    def __bool__(self):
        return bool(self.issues)

    def __iter__(self):
        return iter(self.issues)

    def __len__(self):
        return len(self.issues)

    def __contains__(self, item):
        return item in self.issues

    def __getitem__(self, idx):
        return self.issues[idx]


def self_check(om: OpsModel) -> SelfCheckResult:
    """모델 빌드 후 자체 점검 — SelfCheckResult 반환.

    이전 버전은 List[str] 반환이었으므로 사용처가 list/bool 로 처리해도 동작하도록
    SelfCheckResult 가 list-호환 인터페이스 제공 (issues 직접 접근 가능).
    """
    res = SelfCheckResult()
    issues: List[str] = res.issues
    problem_nodes: Set[int] = res.problem_node_ids

    # 1) 모든 노드가 좌표를 가지는가
    for nid in om.node_tags:
        try:
            c = ops.nodeCoord(nid)
            if len(c) != 3:
                issues.append(f"노드 {nid} 좌표 차원 이상: {c}")
        except Exception as e:
            issues.append(f"노드 {nid} ops.nodeCoord 실패: {e}")

    # 2) 베이스 노드가 존재해야 함
    if not om.fixed_nodes:
        issues.append("고정 지점이 0개 — 베이스 노드 검출 실패")

    # 3) spec.equal_dofs 결합은 모두 다른 컴포넌트 사이여야 함 (joint_rules 결합 검증).
    if om.spec is not None:
        for ed in om.spec.equal_dofs:
            a = om.analysis_model.nodes.get(ed.master)
            b = om.analysis_model.nodes.get(ed.slave)
            if a is None or b is None:
                # 가교/master 노드(am.nodes 에 없는 ops-only) — 정상.
                continue
            if a.source_comp_id == b.source_comp_id and a.source_comp_id != 0:
                issues.append(
                    f"equalDOF 가 같은 컴포넌트 내부: {ed.master}-{ed.slave}"
                )

    # 4) 다이어프램 마스터 노드는 슬레이브 ≥ 3 이어야 의미 있음
    for d in om.diaphragms:
        if len(d.slave_nodes) < 3:
            issues.append(
                f"다이어프램 z={d.z_level:.0f} 슬레이브 {len(d.slave_nodes)}개 — 너무 적음"
            )

    # 4-b) 빌드 중 OpenSees 등록(fix / equalDOF / rigidDiaphragm) 실패가
    #      1 건이라도 있으면 critical 격상. 한 곳이라도 안전망이 풀리면
    #      mechanism / 결합 누락으로 해석 신뢰성이 무너지기 때문.
    if om.registration_failures:
        for nid, msg in om.registration_failures[:5]:
            issues.append(f"등록 실패 nid={nid}: {msg}")
        if len(om.registration_failures) > 5:
            issues.append(
                f"... (이하 등록 실패 {len(om.registration_failures)-5}건 생략)")
        problem_nodes.update(nid for nid, _ in om.registration_failures)
        res.is_critical = True

    # 5) 어떤 부재·다이어프램·결합에도 묶이지 않은 free 노드 검출.
    #    예: 어긋난 적층 시 매칭 실패한 column 의 한쪽 노드는 어디에도 안 묶여 mechanism.
    #
    # [정책 2026-05-18 — C-2 자료구조 정비 후]
    # split sub-member 가 am.members 에 정식 등록되도록 바뀌어, am 끝점이
    # 곧 ops.element 끝점과 1:1 보장. 별도 om.beam_elements 끝점 보강은
    # 더 이상 필요 없음. 단 다이어프램 master(ops-only 음수 영역) 와
    # joint_rules 가 만든 가교 노드(panel_z_route, core_proj — am 에 있지만
    # 부재 끝점은 아님) 는 spec.equal_dofs / spec.diaphragms 로 인정한다.
    am = om.analysis_model
    # [성능 2026-05-18] lazy 캐시 — 같은 om 으로 self_check 재호출 시 즉시 반환.
    if om._self_check_member_nodes_cache is not None:
        member_nodes = om._self_check_member_nodes_cache
    else:
        member_nodes = set()
        for m in am.members.values():
            member_nodes.add(m.n1)
            member_nodes.add(m.n2)
            # 셸 부재(ShellMITC4) 는 4 노드 — n3, n4 도 부재에 속한 노드로 인정.
            if m.kind == 'shell':
                if getattr(m, 'n3', 0):
                    member_nodes.add(m.n3)
                if getattr(m, 'n4', 0):
                    member_nodes.add(m.n4)
        # spec.equal_dofs / spec.diaphragms 끝점 — 가교 노드 및 다이어프램
        # master 인정 (am.members 끝점이 아니지만 강성 결합되어 free 아님).
        if om.spec is not None:
            for ed in om.spec.equal_dofs:
                member_nodes.add(ed.master)
                member_nodes.add(ed.slave)
            for d in om.spec.diaphragms:
                member_nodes.add(d.master)
                for s in d.slaves:
                    member_nodes.add(s)
        om._self_check_member_nodes_cache = member_nodes
    diaphragm_nodes: Set[int] = set()
    for d in om.diaphragms:
        diaphragm_nodes.update(d.slave_nodes)
        diaphragm_nodes.add(d.master_node)
    # spec.equal_dofs / spec.rigid_links 양 끝 노드 (joint_rules 결합 인정).
    bonded_nodes: Set[int] = set()
    if om.spec is not None:
        for ed in om.spec.equal_dofs:
            bonded_nodes.add(ed.master)
            bonded_nodes.add(ed.slave)
        for rl in getattr(om.spec, 'rigid_links', []):
            bonded_nodes.add(rl.master)
            bonded_nodes.add(rl.slave)
    fixed_set = set(om.fixed_nodes)

    suspected_free = []
    for nid in am.nodes:
        # 부재에 속하지 않으면서 다른 어디에도 안 묶이면 free
        if nid in member_nodes:
            continue
        if nid in diaphragm_nodes or nid in bonded_nodes or nid in fixed_set:
            continue
        suspected_free.append(nid)
    if suspected_free:
        issues.append(
            f"어떤 부재·다이어프램·결합에도 속하지 않는 free 노드 "
            f"{len(suspected_free)}개 — mechanism 위험 (예: {suspected_free[:5]})"
        )
        problem_nodes.update(suspected_free)
        res.is_critical = True

    # 6) 베이스 위 노드 중 column 부재가 base 에 닿지 않는 적층 모듈 검출.
    #    각 column 부재의 한쪽 끝이 base 에 fix 되어 있거나 노드 공유/결합으로 다른
    #    부재와 묶여 있어야 한다. 둘 다 아니면 column 이 floating.
    base_z = om.base_z
    # 노드 → 자신을 끝점으로 가지는 부재 ID 집합 (사전 빌드)
    node_to_member_ids: Dict[int, List[int]] = {}
    for mid, m in am.members.items():
        node_to_member_ids.setdefault(m.n1, []).append(mid)
        node_to_member_ids.setdefault(m.n2, []).append(mid)
    for m in am.members.values():
        if m.kind != 'column':
            continue
        n1, n2 = am.nodes[m.n1], am.nodes[m.n2]
        bot = n1 if n1.coord[2] <= n2.coord[2] else n2
        # 베이스에 위치한 column 은 OK
        if abs(bot.coord[2] - base_z) <= NODE_MERGE_TOL_MM:
            continue
        # 같은 bot 노드를 공유하는 다른 부재가 1개 이상 있으면 노드 공유 강결 → OK.
        shared_others = [mid for mid in node_to_member_ids.get(bot.id, [])
                         if mid != m.id]
        if shared_others:
            continue
        # spec.equal_dofs / spec.rigid_links / spec.diaphragms 결합이면 OK.
        if bot.id in bonded_nodes or bot.id in diaphragm_nodes:
            continue
        issues.append(
            f"적층 column #{m.id} 하단 노드 #{bot.id} "
            f"(z={bot.coord[2]:.0f}) 가 아래층/베이스에 연결되지 않음 — floating"
        )
        # 부재 양 끝 노드 모두 문제 노드로 표시 → 부재 자체가 빨강 강조됨
        problem_nodes.add(bot.id)
        problem_nodes.add(m.n1 if bot.id == m.n2 else m.n2)
        res.is_critical = True
        if sum(1 for s in issues if 'floating' in s) >= 5:
            issues.append(f"... (이하 floating column 검출 생략)")
            break

    return res
