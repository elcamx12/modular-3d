"""OpenSeesPy 기반 정적 해석.

Phase 2: 수직 D+L
Phase 3: 횡력 (등가정적 지진 Ex/Ey + 풍 Wx/Wy)

처리 흐름 (D+L):
  1) load_calculator.calculate_loads() 로 부재별 LoadCase 추출
     (1방향/2방향 슬래브 모두 LoadCase 에 등가 평균 UDL 로 누적되어 있음)
  2) RC 코어 column 자중 추가
  3) timeSeries + pattern + eleLoad (1.2D + 1.6L)
  4) Linear 정적 해석
  5) 결과 추출 (분할 보 sub-element 포함) + 평형 검증

처리 흐름 (지진 Ex/Ey, KDS 41 17 00 등가정적):
  1) 지진중량 W = D 합산 (주거이므로 활하중 미포함)
  2) 베이스 전단 V = Cs·W, Cs = SDS·Ie/R 등 KDS 41 17 00 식
  3) 층별 분배 Fx = (wx·hx^k / Σ wi·hi^k)·V
  4) 각 층 다이어프램 마스터 노드에 X(또는 Y) 절점력
  5) 새 hLoad pattern 으로 별도 해석 후 결과 추출

처리 흐름 (풍 Wx/Wy, KDS 41 12 00 단순화):
  1) 기본풍속 35 m/s → 속도압 q = 0.613·V²
  2) 층별 풍하중 = q·Gf·Cf·A_proj
  3) 다이어프램 마스터 노드에 절점력
  4) 정적 해석

가정사항.md 참조.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple
import numpy as np

import openseespy.opensees as ops

from modular_3d.analysis.topology import AnalysisModel
from modular_3d.analysis.ops_builder import OpsModel
from modular_3d.analysis.constants import (
    LOAD_COMBO_DL, LOAD_COMBO_LL,
    STEEL_UNIT_WEIGHT_N_MM3, CONCRETE_UNIT_WEIGHT_N_MM3,
)
from modular_3d.analysis.load_calculator import (
    calculate_loads, LoadResult, LoadCase,
)
from modular_3d._utils.debug import dprint


# ── 공용 솔버 설정 ────────────────────────────────────────────
# Penalty 핸들러 계수 — rigidDiaphragm/equalDOF/rigidLink 안정성 확보 목적.
# 세 솔버(수직·지진·풍) 모두 동일 값 사용. 한 곳에서 조정하도록 헬퍼화.
_PENALTY_ALPHA = 1.0e14


def _apply_penalty_constraints() -> None:
    """OpenSees constraints handler 를 Penalty(α, α) 로 설정."""
    ops.constraints('Penalty', _PENALTY_ALPHA, _PENALTY_ALPHA)


# ── 결과 데이터 ────────────────────────────────────────────────


@dataclass
class MemberForce:
    """부재 양단 단면력 (OpenSees 로컬 좌표).

    elasticBeamColumn 3D `eleResponse 'localForce'` 반환값:
      [N_i, Vy_i, Vz_i, T_i, My_i, Mz_i, N_j, Vy_j, Vz_j, T_j, My_j, Mz_j]
    부호 컨벤션: 시작단(i)에서 부재가 받는 힘.
    """
    member_id: int
    ele_tag: int
    L: float
    f_i: np.ndarray = field(default_factory=lambda: np.zeros(6))   # [N, Vy, Vz, T, My, Mz]
    f_j: np.ndarray = field(default_factory=lambda: np.zeros(6))

    @property
    def N_max_abs(self) -> float:
        return max(abs(self.f_i[0]), abs(self.f_j[0]))

    @property
    def V_max_abs(self) -> float:
        return max(abs(self.f_i[1]), abs(self.f_i[2]),
                   abs(self.f_j[1]), abs(self.f_j[2]))

    @property
    def M_max_abs(self) -> float:
        return max(abs(self.f_i[4]), abs(self.f_i[5]),
                   abs(self.f_j[4]), abs(self.f_j[5]))


@dataclass
class OpsResults:
    case_name: str
    node_disps: Dict[int, np.ndarray] = field(default_factory=dict)        # nid → (6,)
    base_reactions: Dict[int, np.ndarray] = field(default_factory=dict)    # nid → (6,)
    member_forces: Dict[int, MemberForce] = field(default_factory=dict)    # AnalysisMember.id → MF
    # 5 station 분포 단면력 (Phase 5, 설계서_부재호버.md).
    # 각 부재마다 (5, 6) 배열 = [station=0/0.25/0.5/0.75/1.0] × [N, Vy, Vz, T, My, Mz]
    # 단위 N, N·mm. 부호는 OpenSees localForce 규약(인장 +, 압축 −) 그대로.
    member_stations: Dict[int, np.ndarray] = field(default_factory=dict)
    total_applied_load_z: float = 0.0    # N (음수 = 중력)
    total_applied_load_x: float = 0.0    # N (횡력 X)
    total_applied_load_y: float = 0.0    # N (횡력 Y)
    total_base_reaction_z: float = 0.0   # N
    total_base_reaction_x: float = 0.0   # N
    total_base_reaction_y: float = 0.0   # N
    equilibrium_ratio: float = 0.0
    # 층별 횡변위 (다이어프램 마스터 노드 기준)
    story_disp: Dict[float, Tuple[float, float]] = field(default_factory=dict)
    # 층별 베이스 전단 (해당 층까지 누적)
    base_shear: Tuple[float, float] = (0.0, 0.0)

    def summary(self) -> str:
        is_lateral = abs(self.total_applied_load_x) + abs(self.total_applied_load_y) > 1.0
        lines = [
            f"=== OpsResults [{self.case_name}] ===",
            f"  부재 수: {len(self.member_forces)}",
        ]
        if is_lateral:
            lines.extend([
                f"  적용 횡력 Vx     : {self.total_applied_load_x/1000:.2f} kN",
                f"  적용 횡력 Vy     : {self.total_applied_load_y/1000:.2f} kN",
                f"  베이스 전단 Rx   : {self.total_base_reaction_x/1000:.2f} kN",
                f"  베이스 전단 Ry   : {self.total_base_reaction_y/1000:.2f} kN",
            ])
            if self.story_disp:
                lines.append("  층별 횡변위 (mm):")
                for z in sorted(self.story_disp.keys()):
                    dx, dy = self.story_disp[z]
                    lines.append(f"    z={z:6.0f} mm:  δx={dx:7.3f}  δy={dy:7.3f}")
        else:
            lines.extend([
                f"  베이스 반력 합 Rz : {self.total_base_reaction_z/1000:.2f} kN",
                f"  적용하중 합     Wz: {self.total_applied_load_z/1000:.2f} kN (음수=중력)",
            ])
        lines.append(f"  평형 오차       : {self.equilibrium_ratio*100:.3f} %")
        return "\n".join(lines)


# ── 헬퍼 ──────────────────────────────────────────────────────


def _extract_member_forces(om: 'OpsModel', am: AnalysisModel,
                           res: 'OpsResults') -> None:
    """모든 부재 단면력 추출.

    분할되지 않은 부재: member_to_ele_tag 의 단일 ele_tag 에서 localForce 그대로.
    분할된 부재: member_to_split_ele_tags 의 sub-element 들 중 첫 sub 의 i단,
                마지막 sub 의 j단을 원래 부재 양단 단면력으로 사용.
    """
    # 1) 단일 요소 부재
    for mid, ele_tag in om.member_to_ele_tag.items():
        try:
            f = ops.eleResponse(ele_tag, 'localForce')
            if f is None:
                continue
            n = len(f)
            f_i = np.zeros(6, dtype=np.float64)
            f_j = np.zeros(6, dtype=np.float64)
            if n >= 12:
                # frame element — 양단 6 원소
                arr = np.array(f, dtype=np.float64)
                f_i = arr[:6].copy(); f_j = arr[6:12].copy()
            elif n >= 2:
                # 사용자 보고 #4 — truss element 는 localForce 가 짧음(보통
                # axialForce 양단 2 원소 또는 6 원소). axial 만 N(0)/N(6) 자리에 채워
                # 시각화에 0 으로 안 보이도록 보강.
                arr = np.array(f, dtype=np.float64)
                # axial 추정: 첫 원소 = i단 N, 마지막 원소 = j단 N (또는 1번)
                f_i[0] = arr[0]
                f_j[0] = arr[-1] if n != 2 else -arr[0]
            else:
                # 빈/이상 응답 — 건너뜀
                continue
            res.member_forces[mid] = MemberForce(
                member_id=mid, ele_tag=ele_tag,
                L=am.get_member_length(mid),
                f_i=f_i, f_j=f_j,
            )
        except Exception:
            pass
    # 2) 분할된 부재 — 첫 sub 의 i단 + 마지막 sub 의 j단 합성
    for mid, sub_tags in om.member_to_split_ele_tags.items():
        if not sub_tags:
            continue
        try:
            f_first = ops.eleResponse(sub_tags[0], 'localForce')
            f_last = ops.eleResponse(sub_tags[-1], 'localForce')
            if (f_first is None or len(f_first) < 12
                    or f_last is None or len(f_last) < 12):
                continue
            f_first_arr = np.array(f_first, dtype=np.float64)
            f_last_arr = np.array(f_last, dtype=np.float64)
            res.member_forces[mid] = MemberForce(
                member_id=mid, ele_tag=sub_tags[0],
                L=am.get_member_length(mid),
                f_i=f_first_arr[:6].copy(),
                f_j=f_last_arr[6:12].copy(),
            )
        except Exception:
            pass


def _compute_station_forces(f_i: np.ndarray, f_j: np.ndarray,
                             L: float, n_stations: int = 5) -> np.ndarray:
    """Phase 5 — 양단 단면력 + 등분포 가정으로 station n 점 분포 계산.

    OpenSees elasticBeamColumn 은 section 응답을 갖지 않아 직접 station
    추출이 불가능하다. 대신 부재에 걸린 분포하중을 등분포로 가정하고
    양단 단면력으로부터 station 분포를 해석적으로 계산한다.

    부호 약속:
      f_i, f_j : OpenSees localForce. i / j 단에서 부재가 받는 6 원소 힘.
      등분포 wy, wz (로컬) 가 걸리면 양단 평형으로 wy ≈ -(Vy_i + Vy_j)/L.

    계산식:
      N(x), T(x): 분포 축력/비틀림 없으므로 양단 직선 보간.
      Vy(x), Vz(x): 양단 직선 보간 — 등분포 효과 자동 반영.
      Mz(x), My(x): 양단 직선 보간 + 등분포 mid-span 부풀음 (w·L²/8).

    n_stations 가 5 면 비율 0 / 0.25 / 0.5 / 0.75 / 1.0.
    """
    xs = np.linspace(0.0, 1.0, n_stations)
    out = np.zeros((n_stations, 6), dtype=np.float64)
    fi = np.asarray(f_i, dtype=np.float64).reshape(-1)
    fj = np.asarray(f_j, dtype=np.float64).reshape(-1)
    if fi.size < 6 or fj.size < 6:
        return out
    # i / j 단에서 부재가 받는 힘. 등분포 없으면 fi = -fj (대칭).
    # station 단면력은 좌측 free body 평형의 결과로 양단에서 [+f_i, -f_j].
    fi_end = fi.copy()
    fj_end = -fj.copy()
    # 직선 보간 (N, Vy, Vz, T) — 분포 효과 양단에 다 누적되어 있음
    for k in (0, 1, 2, 3):
        out[:, k] = (1.0 - xs) * fi_end[k] + xs * fj_end[k]
    # 모멘트 양단 직선
    Mz_lin = (1.0 - xs) * fi_end[5] + xs * fj_end[5]
    My_lin = (1.0 - xs) * fi_end[4] + xs * fj_end[4]
    # 등분포 mid-span 부풀음: wy·L²/8, 분포 형상은 4·xn·(1-xn)
    bulge = 4.0 * xs * (1.0 - xs)  # 0 / 0.75 / 1.0 / 0.75 / 0
    if L > 1e-9:
        wy = -(fi[1] + fj[1]) / L   # +y 방향 등분포 추정
        wz = -(fi[2] + fj[2]) / L
        out[:, 5] = Mz_lin + bulge * (wy * L * L / 8.0)
        out[:, 4] = My_lin + bulge * (wz * L * L / 8.0)
    else:
        out[:, 5] = Mz_lin
        out[:, 4] = My_lin
    return out


_STATION_PER_SUB = 7   # 사용자 결정: 5 → 7 (가독성 향상)


def _diagnose_zero_members(am: AnalysisModel, res: 'OpsResults') -> None:
    """사용자 보고: 0 단면력 부재가 많음.

    풀이 직후 진단 — am.members 중 단면력 추출이 누락됐거나 모든 단면력이
    거의 0 인 마스터 부재 리스트를 콘솔에 출력. 정상 가능 원인:
      - 강체 결합 슬레이브 (R09 코어 묶음 등)
      - OpenSees 등록 단계에서 제외된 부재 (truss 등 특수 케이스)
      - 외력을 받지 않는 격리 부재
    """
    missing = []
    zero = []
    for mid, m in am.members.items():
        if getattr(m, 'is_split_sub', False):
            continue
        mf = res.member_forces.get(mid)
        if mf is None:
            missing.append((mid, m.role))
            continue
        f_max = max(np.max(np.abs(mf.f_i)), np.max(np.abs(mf.f_j)))
        if f_max < 1e-3:
            zero.append((mid, m.role))
    if missing:
        roles = sorted({r for _, r in missing})
        print(f'[F6 DIAG] OpenSees 미등록 마스터 부재 {len(missing)} 개 — '
              f'roles: {roles}')
    if zero:
        roles = sorted({r for _, r in zero})
        print(f'[F6 DIAG] 단면력 ≈ 0 마스터 부재 {len(zero)} 개 — '
              f'roles: {roles}')


def _extract_member_stations(om: 'OpsModel', am: AnalysisModel,
                             res: 'OpsResults') -> None:
    """Phase 5 — 풀이 직후 모든 부재의 분포 단면력을 캐시.

    정책:
      - 분할 안 된 부재: 양단 + 등분포 가정으로 _STATION_PER_SUB 점 해석 계산.
      - 분할된 부재(member_to_split_ele_tags): 각 sub-element 에 대해
        그 sub 자체의 양단 localForce 와 sub 길이로 _STATION_PER_SUB 점
        해석 계산 → 모든 sub 의 station 을 그대로 이어 붙임. sub 경계에
        다른 부재가 연결돼 점하중이 걸린 경우, 인접 sub 끝점과 다음 sub
        시작점이 다른 값을 가지면서 자연스러운 jump 가 표시된다.
    """
    n = _STATION_PER_SUB
    # 1) 분할 안 된 부재
    for mid in om.member_to_ele_tag.keys():
        mf = res.member_forces.get(mid)
        if mf is None:
            continue
        L = am.get_member_length(mid)
        res.member_stations[mid] = _compute_station_forces(
            mf.f_i, mf.f_j, L, n_stations=n)

    # 2) 분할된 부재 — sub 마다 7 점 station 계산 후 vstack.
    for mid, sub_tags in om.member_to_split_ele_tags.items():
        if not sub_tags:
            continue
        sub_arrays = []
        for et in sub_tags:
            try:
                f = ops.eleResponse(et, 'localForce')
                if f is None or len(f) < 12:
                    continue
                f_arr = np.array(f, dtype=np.float64)
                fi = f_arr[:6]; fj = f_arr[6:12]
                en1, en2, _kind, _r = om.beam_elements[et]
                c1 = om.node_tags[en1]; c2 = om.node_tags[en2]
                L_sub = float(np.linalg.norm(c2 - c1))
                sub_st = _compute_station_forces(fi, fj, L_sub, n_stations=n)
                sub_arrays.append(sub_st)
            except Exception:
                continue
        if not sub_arrays:
            continue
        res.member_stations[mid] = np.vstack(sub_arrays)


def _consolidate_member_loads(am: AnalysisModel, lr: LoadResult) -> Dict[int, Tuple[float, float, float]]:
    """부재별 (D_self, D_slab, L) [N/mm] 반환.

    LoadCase 에 이미 1방향/2방향 슬래브 등가 평균 UDL 이 모두 누적되어 있어
    그대로 꺼내 쓴다.
    """
    out: Dict[int, Tuple[float, float, float]] = {}
    for mid in am.members:
        lc = lr.member_loads.get(mid, LoadCase())
        out[mid] = (lc.self_weight, lc.slab_dead, lc.live)
    return out


def _apply_eleload_factored(om: OpsModel, mid: int,
                            d_self: float, d_slab: float, live: float) -> float:
    """1.2D + 1.6L 계수하중을 부재 한 개에 eleLoad 로 적용.

    Returns: 적용 총 하중 z 성분 [N] (음수 = 중력).
    """
    w_factored = (LOAD_COMBO_DL * (d_self + d_slab) + LOAD_COMBO_LL * live)
    if w_factored < 1e-9:
        return 0.0
    # 분할된 보(member_to_split_ele_tags)는 모든 sub-element에 동일 분포하중 적용
    # (단위길이당 같은 wY/wZ 가 길이 segment 합 = 원래 보 자중과 동등)
    split_tags = om.member_to_split_ele_tags.get(mid)
    if split_tags:
        ele_tags = split_tags
    else:
        single = om.member_to_ele_tag.get(mid)
        if single is None:
            return 0.0
        ele_tags = [single]

    # 첫 ele_tag로 방향 판정 (모든 sub-element 같은 방향)
    n1, n2, _kind0, _role = om.beam_elements[ele_tags[0]]
    c1 = om.node_tags[n1]
    c2 = om.node_tags[n2]
    axis = c2 - c1
    L0 = float(np.linalg.norm(axis))
    if L0 < 1e-9:
        return 0.0
    u = axis / L0
    # 분할된 보의 z 성분은 모든 sub-element 길이 합으로 누적
    total_L = 0.0
    for et in ele_tags:
        en1, en2, kind, _r = om.beam_elements[et]
        ec1 = om.node_tags[en1]
        ec2 = om.node_tags[en2]
        Le = float(np.linalg.norm(ec2 - ec1))
        total_L += Le
        # [2026-05-18] truss element 는 eleLoad('-beamUniform') 가 silently
        # 무시됨 → 양 끝 노드에 nodal load (Fz=-w·L/2) 로 분배 적용.
        # 코어벽 truss 자중이 평형식에 누락되는 버그 수정.
        if kind == 'truss':
            P_half = w_factored * Le / 2.0
            ops.load(en1, 0.0, 0.0, -P_half, 0.0, 0.0, 0.0)
            ops.load(en2, 0.0, 0.0, -P_half, 0.0, 0.0, 0.0)
        elif abs(u[2]) > 0.99:
            sign = -1.0 if u[2] > 0 else 1.0
            ops.eleLoad('-ele', et, '-type', '-beamUniform', 0.0, 0.0,
                        sign * w_factored)
        else:
            ops.eleLoad('-ele', et, '-type', '-beamUniform', 0.0, -w_factored)
    return -w_factored * total_L   # 적용 하중 z 합 (음수)


# [2026-05-13 가상 코어 제거] _apply_core_self_weight 함수 폐기.
# 가상 RC 코어 column 자중을 1m × 1m 정사각형 단면(CORE_A) 으로 일률 적용하던
# 함수. wide-column 전환 이후 사용자 코어 부재는 일반 frame 으로 등록되어
# _consolidate_member_loads → _apply_eleload_factored 경로로 정확한 단면별
# 자중이 부여되므로 본 함수는 이중 적용/잘못된 단면 적용의 원인.


# ── 메인 솔버 ─────────────────────────────────────────────────


def solve_vertical(om: OpsModel, scene,
                   load_result: LoadResult | None = None) -> OpsResults:
    """수직(D+L = 1.2D + 1.6L) 정적 해석.

    om 은 build_ops_model() 직후 상태여야 함 (ops 모델 등록 완료).
    load_result 가 주어지면 calculate_loads 를 다시 부르지 않고 재사용.
    Returns: OpsResults
    """
    am = om.analysis_model

    # 1) 부재별 하중 (캐시가 있으면 재사용)
    lr = load_result if load_result is not None else calculate_loads(scene, am)
    consolidated = _consolidate_member_loads(am, lr)

    # 2) 하중 패턴 등록
    ops.timeSeries('Constant', 1)
    ops.pattern('Plain', 1, 1)

    import os as _os
    _DBG_VLOAD = _os.environ.get('DEBUG_VLOAD', '0') == '1'

    # 디버그용 노드 → role 매핑 (fix 반력 분해 출력)
    nid_to_role_map: Dict[int, Set[str]] = {}
    if _DBG_VLOAD:
        for m in am.members.values():
            for nid in (m.n1, m.n2, getattr(m, 'n3', None), getattr(m, 'n4', None)):
                if nid is None:
                    continue
                nid_to_role_map.setdefault(nid, set()).add(m.role or '?')

    total_applied_z = 0.0
    z_frame = 0.0
    z_by_role: Dict[str, float] = {}
    for mid, (d_self, d_slab, live) in consolidated.items():
        contrib = _apply_eleload_factored(om, mid, d_self, d_slab, live)
        z_frame += contrib
        if _DBG_VLOAD:
            m = am.members.get(mid)
            role = m.role if m else '?'
            z_by_role[role] = z_by_role.get(role, 0.0) + contrib
    total_applied_z += z_frame
    if _DBG_VLOAD:
        dprint('DBG-VLOAD', '[DBG-VLOAD] frame 자중 role 별 합:')
        for role in sorted(z_by_role.keys()):
            dprint('role', f'   {role:30s} {z_by_role[role]/1000:9.2f} kN')
    # [2026-05-13 가상 코어 제거] _apply_core_self_weight 호출 폐기. 코어
    # 부재 자중은 일반 frame eleLoad 경로에서 이미 처리됨.

    if _DBG_VLOAD:
        dprint('DBG-VLOAD', f'[DBG-VLOAD] frame eleLoad 합     = {z_frame/1000:.2f} kN')
        dprint('DBG-VLOAD', f'[DBG-VLOAD] 적용 z 자중 총합       = {total_applied_z/1000:.2f} kN')

    # 3) 해석 시스템 설정
    # rigidDiaphragm + equalDOF + rigidLink 조합에서 'Transformation' 은
    # 일부 노드 반력에 미세 오차(0.5~2%)가 누적되는 경우가 있으므로 Penalty 사용.
    _apply_penalty_constraints()
    ops.numberer('RCM')
    ops.system('BandGen')
    ops.test('NormDispIncr', 1.0e-6, 50)
    ops.algorithm('Linear')
    ops.integrator('LoadControl', 1.0)
    ops.analysis('Static')

    ok = ops.analyze(1)
    if ok != 0:
        raise RuntimeError(f"OpenSees analyze 실패 (return code {ok})")

    # 4) 결과 추출
    res = OpsResults(case_name='1.2D+1.6L')
    res.total_applied_load_z = total_applied_z

    # 4-a) 노드 변위
    for nid in om.node_tags.keys():
        try:
            d = ops.nodeDisp(nid)
            res.node_disps[nid] = np.array(d, dtype=np.float64)
        except Exception:
            pass

    # 4-b) 베이스 반력 (z 성분 합 평형 검증용)
    #
    # [평형 검증 정책 — 2026-05-13]
    # Penalty 핸들러 하에서 equalDOF 로 묶인 노드(특히 보 분할로 만들어진
    # 분할 노드) 에 미세 잔여 응력(잔여 반력) 이 누적된다. 같은 사영점에 + / -
    # 가 교차해 모델 전체로는 자체 평형이지만, fixed_nodes 만 보고 합산하면
    # 그 잔여 분이 빠져 평형 오차가 과대 계산된다.
    # → 외력 회수 평형 검증은 "모든 노드의 반력 합" 으로 수행. 모델이 실제
    #   평형이면 적용 자중과 일치한다. fix 노드 / 잔여 노드 구분은 별도 보관.
    ops.reactions()
    total_rz = 0.0
    rz_by_role: Dict[str, float] = {}
    # 1) fix 노드 반력 — 표준 base 반력 (시각·보고용).
    for nid in om.fixed_nodes:
        try:
            r = ops.nodeReaction(nid)
            r_arr = np.array(r, dtype=np.float64)
            res.base_reactions[nid] = r_arr
            if _DBG_VLOAD:
                roles = sorted(nid_to_role_map.get(nid, {'(no-role)'}))
                key = '+'.join(roles) if roles else '(no-role)'
                rz_by_role[key] = rz_by_role.get(key, 0.0) + r_arr[2]
        except Exception:
            pass
    if _DBG_VLOAD:
        dprint('DBG-VLOAD', '[DBG-VLOAD] fix 노드 반력 role 별 합:')
        for r_name in sorted(rz_by_role.keys()):
            dprint('r_name', f'   {r_name:40s} {rz_by_role[r_name]/1000:9.2f} kN')
    # [2026-05-13 가상 코어 제거] om.core 분기 폐기 — 사용자 코어 base 반력은
    # om.fixed_nodes 의 _step_fix_base_nodes 가 등록한 분기로 이미 포함됨.

    # 2) 평형 검증용 — 모든 노드 반력 합 (Penalty 잔여 노드까지 포함).
    #    이 값이 적용 자중과 일치해야 모델이 평형이라고 판정한다.
    # [2026-05-18 silent fail 카운트] 회수 실패 노드 수를 기록해 평형 오차가
    # "실제 0" 인지 "회수 누락 0" 인지 구분 가능하게 한다.
    _reaction_miss = 0
    try:
        all_tags = ops.getNodeTags()
        for nid in all_tags:
            try:
                r = ops.nodeReaction(nid)
                if len(r) >= 3:
                    total_rz += r[2]
                else:
                    _reaction_miss += 1
            except Exception:
                _reaction_miss += 1
        if _reaction_miss:
            dprint('ops_solver', f'[ops_solver] solve_vertical: nodeReaction 회수 실패 노드 {_reaction_miss} 개 (평형 합산에서 누락).')
    except Exception:
        # 폴백 — 옛 방식 (fix 노드 합).
        total_rz = sum(r[2] for r in res.base_reactions.values())
    res.total_base_reaction_z = total_rz

    # 4-c) 부재 단면력 (분할 보 포함)
    _extract_member_forces(om, am, res)
    # Phase 5 — 5 station 분포 단면력 캐시 (호버 정보창 AFD/SFD/BMD 용)
    _extract_member_stations(om, am, res)
    _diagnose_zero_members(am, res)

    # 5) 평형 검증
    if abs(total_applied_z) > 1.0:
        # total_applied_z 는 음수 (중력), total_rz 는 양수 (위쪽 반력). 합이 0이어야 평형.
        residual = total_rz + total_applied_z
        res.equilibrium_ratio = abs(residual) / abs(total_applied_z)

    return res


# ── Phase 3: 횡력 (등가정적 지진 + 풍) ─────────────────────────

# 가정사항.md §8 기반 한국 기준 디폴트
SDS_DEFAULT = 0.500   # g — 단주기 설계스펙트럼 가속도 (서울 + 지반 S4 보수 가정)
SD1_DEFAULT = 0.200   # g — 1초 주기 설계스펙트럼 가속도
IE_DEFAULT = 1.0      # 일반 주거
R_DEFAULT = 8.0       # 강구조 모멘트골조 (모듈 핀접합 + 사용자 RC 코어)
G_M_S2 = 9.80665      # 표준 중력

# 풍하중 (KDS 41 12 00 단순화)
WIND_V0_M_S = 35.0    # 서울 기본풍속
WIND_GF = 0.85        # 가스트영향계수
WIND_CF = 1.3         # 풍력계수 (전체 건물 단순)


def _compute_floor_seismic_weight(om: OpsModel, lr: LoadResult) -> Dict[float, float]:
    """각 층의 지진중량 W (D 만, 활하중 미포함) [N].

    층 분류 규칙:
      - 보(수평 부재): 양 끝 z 평균이 가장 가까운 층에 전부 귀속.
      - 기둥(수직 부재): 양 끝 z 가 다른 두 층에 걸쳐 있으므로 자중을
        위/아래 층에 절반씩 분배 (정석).
    """
    am = om.analysis_model
    consol = _consolidate_member_loads(am, lr)

    # 층 z 리스트 (다이어프램 기준)
    floor_zs = sorted(d.z_level for d in om.diaphragms)
    if not floor_zs:
        return {}

    weights: Dict[float, float] = {z: 0.0 for z in floor_zs}
    for mid, (d_self, d_slab, _live) in consol.items():
        m = am.members[mid]
        L = am.get_member_length(mid)
        z1 = float(am.nodes[m.n1].coord[2])
        z2 = float(am.nodes[m.n2].coord[2])
        total_w = (d_self + d_slab) * L
        if m.kind == 'column' and abs(z1 - z2) > 50.0:
            # 기둥: 위/아래 층 절반씩
            z_low, z_high = (z1, z2) if z1 < z2 else (z2, z1)
            best_low = min(floor_zs, key=lambda fz: abs(fz - z_low))
            best_high = min(floor_zs, key=lambda fz: abs(fz - z_high))
            weights[best_low] += 0.5 * total_w
            weights[best_high] += 0.5 * total_w
        else:
            # 보: 가장 가까운 층 한 곳에
            z_avg = 0.5 * (z1 + z2)
            best_z = min(floor_zs, key=lambda fz: abs(fz - z_avg))
            weights[best_z] += total_w

    # [2026-05-13 가상 코어 제거] 가상 코어 column 자중 분배(CORE_A 기반) 폐기.
    # 사용자 코어 부재는 일반 column 으로 등록되어 위 루프(_consolidate_member_loads)
    # 에서 이미 정확한 단면별 자중이 층별 분배됨.
    return weights


def _seismic_base_shear(W_total: float,
                        sds: float = SDS_DEFAULT,
                        sd1: float = SD1_DEFAULT,
                        Ie: float = IE_DEFAULT, R: float = R_DEFAULT,
                        T: float = 0.5) -> float:
    """KDS 41 17 00 등가정적 베이스 전단 V [N]."""
    Cs = sds * Ie / R
    Cs_max = sd1 * Ie / max(T * R, 1e-6)
    Cs_min = max(0.044 * sds * Ie, 0.01)
    Cs = max(min(Cs, Cs_max), Cs_min)
    return Cs * W_total


def _seismic_floor_distribution(weights: Dict[float, float],
                                base_z: float, V: float,
                                k: float = 1.0) -> Dict[float, float]:
    """층별 횡력 Fx 분배 (KDS 41 17 00).

    Fx_i = (w_i · h_i^k / Σ w_j · h_j^k) · V
    h_i = 베이스부터 해당 층까지 높이 (mm).
    """
    out: Dict[float, float] = {}
    items = [(z, w, max(z - base_z, 0.0)) for z, w in weights.items()]
    denom = sum(w * (h ** k) for _, w, h in items)
    if denom < 1e-9:
        return out
    for z, w, h in items:
        out[z] = (w * (h ** k) / denom) * V
    return out


def _wind_floor_distribution(om: OpsModel, direction: str = 'X') -> Dict[float, float]:
    """KDS 41 12 00 단순화: 층별 풍하중 [N].

    각 층 풍하중 = q · Gf · Cf · A_proj
    A_proj = 건물 폭(직각 방향) × 층고
    q = 0.613 · V0² [N/m²] (V0 m/s)
    """
    if not om.diaphragms or not om.node_tags:
        return {}
    coords = np.array(list(om.node_tags.values()))
    x_min, y_min = coords[:, 0].min(), coords[:, 1].min()
    x_max, y_max = coords[:, 0].max(), coords[:, 1].max()
    width_mm = (y_max - y_min) if direction.upper() == 'X' else (x_max - x_min)

    q_n_m2 = 0.613 * WIND_V0_M_S ** 2   # N/m²
    # 사용자 진단 (2026-05-18): 같은 z 의 컴포넌트별 다이어프램이 여러 개라
    # 중복 z 가 sorted 리스트에 그대로 들어가던 결과 forces[z] 값이 다음 dup z
    # iteration 에서 0 으로 덮어써졌다. set 으로 unique z 만 사용.
    floor_zs = sorted(set(d.z_level for d in om.diaphragms))
    base_z = om.base_z
    out: Dict[float, float] = {}
    for i, z in enumerate(floor_zs):
        z_below = floor_zs[i - 1] if i > 0 else base_z
        h_floor_mm = z - z_below
        # A_proj = width × h_floor (mm² → m²)
        A_m2 = (width_mm * h_floor_mm) * 1e-6        # mm² → m²
        F = q_n_m2 * WIND_GF * WIND_CF * A_m2        # (N/m²)·m² = N
        out[z] = F                                   # 단위 = N
    return out


def _apply_horizontal_loads(om: OpsModel,
                            forces_by_z: Dict[float, float],
                            direction: str,
                            pattern_tag: int = 2,
                            ts_tag: int = 2) -> Tuple[float, float]:
    """층별 횡력을 다이어프램 master 노드에 절점력으로 적용.

    [정책 2026-05-13 가상 코어 제거]
    가상 코어 cantilever column 및 master ↔ core rigidLink 가 모두 폐기되어
    옛 이중 구속 우려가 사라졌다. 횡력은 다이어프램 master 에 직접 가하고,
    사용자 코어 부재는 다이어프램의 슬레이브로 묶여 있어 master 의 X·Y·Rz
    자유도가 자동으로 코어 강성에 결합됨.

    Returns: (총 적용력 X, 총 적용력 Y) [N]
    """
    ops.timeSeries('Constant', ts_tag)
    ops.pattern('Plain', pattern_tag, ts_tag)

    # 사용자 진단 (2026-05-18): 같은 z 평면에 컴포넌트별 다이어프램이 여러 개
    # 있을 때, dict 컴프리헨션 `{d.z_level: d.master_node}` 가 마지막 1 개만
    # 살리고 나머지 master 들을 버려서 풍/지진 횡력이 작은 격리 다이어프램
    # (캔틸 free end 등) 에만 가해지던 결함을 수정.
    # 해결: z 별 모든 master 를 모아 slave 수(질량 비례 근사) 로 가중 분배.
    from collections import defaultdict
    z_to_masters = defaultdict(list)
    for d in om.diaphragms:
        w = max(len(d.slave_nodes), 1)
        z_to_masters[d.z_level].append((d.master_node, w))

    Vx_total = 0.0
    Vy_total = 0.0
    for z, F in forces_by_z.items():
        masters_w = z_to_masters.get(z)
        if not masters_w:
            # 가장 가까운 z 폴백
            tgt_z = min(z_to_masters.keys(), key=lambda mz: abs(mz - z))
            masters_w = z_to_masters[tgt_z]
        total_w = sum(w for _, w in masters_w)
        if total_w <= 0:
            continue
        for master, w in masters_w:
            F_share = F * (w / total_w)
            if direction.upper() == 'X':
                ops.load(master, F_share, 0.0, 0.0, 0.0, 0.0, 0.0)
                Vx_total += F_share
            else:
                ops.load(master, 0.0, F_share, 0.0, 0.0, 0.0, 0.0)
                Vy_total += F_share
    return Vx_total, Vy_total


def _extract_lateral_results(om: OpsModel, res: OpsResults) -> None:
    """변위 + 베이스 반력 + 부재력 추출 + 층변위 산정.

    [BUGFIX 2026-05-08] Transformation 제약은 다이어프램 slave 의 묶인 자유도
    (xy, RZ) 를 시스템에서 제거하므로 ops.nodeDisp(slave) 가 0 을 반환한다.
    시각화·컨투어가 모든 노드 변위를 보여줄 수 있도록, slave 의 xy/RZ 를
    master 의 변위로부터 후처리로 채워 넣는다.

    rigidDiaphragm 변환 관계 (perpDirn=3, 평면 xy):
      slave_x  = master_x  − master_RZ × (slave_y0 − master_y0)
      slave_y  = master_y  + master_RZ × (slave_x0 − master_x0)
      slave_RZ = master_RZ
      slave_z, slave_RX, slave_RY 는 자유 → 그대로 nodeDisp 결과 사용.
    """
    am = om.analysis_model

    # 노드 변위 — 우선 ops.nodeDisp 그대로 추출
    for nid in om.node_tags.keys():
        try:
            d = ops.nodeDisp(nid)
            res.node_disps[nid] = np.array(d, dtype=np.float64)
        except Exception:
            pass

    # 다이어프램 slave 의 xy/RZ 를 master 변위로부터 채움.
    for d in om.diaphragms:
        try:
            md = ops.nodeDisp(d.master_node)
        except Exception:
            continue
        if md is None or len(md) < 6:
            continue
        m_ux, m_uy, _m_uz, _m_rx, _m_ry, m_rz = (float(x) for x in md[:6])
        try:
            mc = ops.nodeCoord(d.master_node)
        except Exception:
            continue
        m_x0, m_y0 = float(mc[0]), float(mc[1])
        for snid in d.slave_nodes:
            slave_disp = res.node_disps.get(snid)
            slave_coord = om.node_tags.get(snid)
            if slave_coord is None:
                continue
            if slave_disp is None:
                slave_disp = np.zeros(6, dtype=np.float64)
                res.node_disps[snid] = slave_disp
            s_x0, s_y0 = float(slave_coord[0]), float(slave_coord[1])
            # 강체 평면 회전 적용
            slave_disp[0] = m_ux - m_rz * (s_y0 - m_y0)
            slave_disp[1] = m_uy + m_rz * (s_x0 - m_x0)
            slave_disp[5] = m_rz

    # 베이스 반력 (Rx, Ry, Rz)
    # [평형 검증 정책 — 2026-05-13] solve_vertical 과 동일 — 모든 노드 반력 합으로
    # 평형 검증 (Penalty 잔여 응력 노드 포함). fix 노드 반력은 별도 표에 보관.
    ops.reactions()
    # 1) fix 노드 반력 — 표준 base 반력 표 (시각·보고용).
    for nid in om.fixed_nodes:
        try:
            r = np.array(ops.nodeReaction(nid), dtype=np.float64)
            res.base_reactions[nid] = r
        except Exception:
            pass
    # [2026-05-13 가상 코어 제거] om.core 분기 폐기 — 사용자 코어 base 반력은
    # 위의 om.fixed_nodes 루프에서 이미 포함됨.

    # 2) 평형 검증용 — 모든 노드 반력 합.
    # [2026-05-18 silent fail 카운트] 횡력 평형도 회수 실패 누락이 0% 처럼
    # 보이지 않도록 실패 건수 보고.
    Rx = Ry = Rz = 0.0
    _reaction_miss = 0
    try:
        all_tags = ops.getNodeTags()
        for nid in all_tags:
            try:
                r = ops.nodeReaction(nid)
                if len(r) >= 3:
                    Rx += r[0]; Ry += r[1]; Rz += r[2]
                else:
                    _reaction_miss += 1
            except Exception:
                _reaction_miss += 1
        if _reaction_miss:
            dprint('ops_solver', f'[ops_solver] solve_lateral: nodeReaction 회수 실패 노드 {_reaction_miss} 개 (평형 합산에서 누락).')
    except Exception:
        for r in res.base_reactions.values():
            Rx += r[0]; Ry += r[1]; Rz += r[2]
    res.total_base_reaction_x = Rx
    res.total_base_reaction_y = Ry
    res.total_base_reaction_z = Rz

    # 층별 변위 (다이어프램 마스터)
    for d in om.diaphragms:
        try:
            disp = ops.nodeDisp(d.master_node)
            res.story_disp[d.z_level] = (float(disp[0]), float(disp[1]))
        except Exception:
            pass

    # 부재 단면력 (분할 보 포함)
    _extract_member_forces(om, am, res)
    # Phase 5 — 5 station 분포 단면력 캐시 (호버 정보창 AFD/SFD/BMD 용)
    _extract_member_stations(om, am, res)
    _diagnose_zero_members(am, res)

    # 평형 검증 (적용 횡력 vs 베이스 전단)
    applied_h = abs(res.total_applied_load_x) + abs(res.total_applied_load_y)
    react_h = abs(res.total_base_reaction_x) + abs(res.total_base_reaction_y)
    if applied_h > 1.0:
        res.equilibrium_ratio = abs(applied_h - react_h) / applied_h
    res.base_shear = (res.total_base_reaction_x, res.total_base_reaction_y)


def solve_seismic(om: OpsModel, scene, direction: str = 'X',
                  load_result: LoadResult | None = None) -> OpsResults:
    """등가정적 지진 해석 (Ex 또는 Ey).

    주의: ops 상태가 build_ops_model() 직후여야 함. 이전 해석의 패턴이 남아 있으면 충돌.
    """
    direction = direction.upper()
    case_name = f'E{direction.lower()}'

    # 지진중량 + 베이스 전단 + 층별 분배 (LoadResult 캐시 재사용)
    am = om.analysis_model
    lr = load_result if load_result is not None else calculate_loads(scene, am)
    weights = _compute_floor_seismic_weight(om, lr)
    W_total = sum(weights.values())
    V = _seismic_base_shear(W_total)
    forces = _seismic_floor_distribution(weights, om.base_z, V)

    Vx, Vy = _apply_horizontal_loads(om, forces, direction)

    # 해석 — 횡력 케이스는 Transformation 제약 사용.
    # [BUGFIX 2026-05-08] Penalty 제약은 rigidDiaphragm + rigidLink 이중 체인에서
    # 외력을 흡수해 변위·반력이 0 으로 떨어지는 현상이 있어 Transformation 으로 교체.
    # 수직(D+L) 케이스는 외력이 elemental load 라 Penalty 가 정상 작동, 그대로 유지.
    _apply_penalty_constraints()
    ops.numberer('RCM')
    ops.system('BandGen')
    ops.test('NormDispIncr', 1.0e-6, 50)
    ops.algorithm('Linear')
    ops.integrator('LoadControl', 1.0)
    ops.analysis('Static')
    if ops.analyze(1) != 0:
        raise RuntimeError(f'OpenSees analyze 실패 [{case_name}]')

    res = OpsResults(case_name=case_name)
    res.total_applied_load_x = Vx
    res.total_applied_load_y = Vy
    _extract_lateral_results(om, res)
    return res


def solve_wind(om: OpsModel, scene, direction: str = 'X') -> OpsResults:
    """풍하중 해석 (Wx 또는 Wy)."""
    direction = direction.upper()
    case_name = f'W{direction.lower()}'

    forces = _wind_floor_distribution(om, direction)
    Vx, Vy = _apply_horizontal_loads(om, forces, direction)

    # solve_seismic 과 동일 사유로 Transformation 사용.
    _apply_penalty_constraints()
    ops.numberer('RCM')
    ops.system('BandGen')
    ops.test('NormDispIncr', 1.0e-6, 50)
    ops.algorithm('Linear')
    ops.integrator('LoadControl', 1.0)
    ops.analysis('Static')
    if ops.analyze(1) != 0:
        raise RuntimeError(f'OpenSees analyze 실패 [{case_name}]')

    res = OpsResults(case_name=case_name)
    res.total_applied_load_x = Vx
    res.total_applied_load_y = Vy
    _extract_lateral_results(om, res)
    return res


def solve_all_cases(scene, *, prebuilt_am=None) -> Dict[str, OpsResults]:
    """5 케이스(D+L, Ex, Ey, Wx, Wy) 자동 해석 — 케이스마다 ops 모델 재빌드.

    Args:
        scene: 해석 대상 Scene.
        prebuilt_am: 외부에서 이미 build_analysis_model(scene) 호출한 결과를
            그대로 재사용. None 이면 내부에서 한 번 더 빌드.
            (controls_f6 등이 자체 토폴로지 빌드를 마친 후 본 함수를 호출하면
            am 재빌드 1 회 절감.)
            **ops 도메인 캐싱은 별개 — 5 케이스마다 ops.wipe + 재빌드.**

    Returns:
        {'D+L': res, 'Ex': res, 'Ey': res, 'Wx': res, 'Wy': res}

    [코어 필수 검증 — 2026-05-12 Q55 (가)]
    Scene 에 RC 코어(Component.CORE) 가 0 개이면 해석 자체를 차단.
    저장은 항상 허용되지만 해석 실행 시점에 강제. (사용자가 작업 중간 결과를
    저장할 수 있고, 해석을 돌리려면 코어 1 장 이상 배치 필요.)
    """
    from modular_3d.model import Core as _Core
    has_core = any(isinstance(c, _Core) for c in scene.components.values())
    if not has_core:
        raise RuntimeError(
            '해석 차단: 씬에 RC 코어가 1 장도 없습니다. '
            '팔레트 9 (RC 코어) 로 코어벽을 배치한 뒤 다시 해석해 주세요.'
        )

    from modular_3d.analysis.topology import build_analysis_model
    from modular_3d.analysis.ops_builder import build_ops_model

    out: Dict[str, OpsResults] = {}
    am = prebuilt_am if prebuilt_am is not None else build_analysis_model(scene)

    # 부재별 하중은 한 번만 계산하고 모든 케이스가 재사용
    lr_cached = calculate_loads(scene, am)

    # D+L
    om = build_ops_model(am, scene=scene)
    out['D+L'] = solve_vertical(om, scene, load_result=lr_cached)

    # Ex
    om = build_ops_model(am, scene=scene)
    out['Ex'] = solve_seismic(om, scene, 'X', load_result=lr_cached)

    # Ey
    om = build_ops_model(am, scene=scene)
    out['Ey'] = solve_seismic(om, scene, 'Y', load_result=lr_cached)

    # Wx
    om = build_ops_model(am, scene=scene)
    out['Wx'] = solve_wind(om, scene, 'X')

    # Wy
    om = build_ops_model(am, scene=scene)
    out['Wy'] = solve_wind(om, scene, 'Y')

    return out
