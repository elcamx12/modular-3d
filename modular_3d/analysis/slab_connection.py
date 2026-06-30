# -*- coding: utf-8 -*-
"""슬래브-벽체 격막 연결부 전단강도 검토 (모델링지침 2025 제8장, 식 8-1~8-6).

격막하중(수요) Vu 가 연결부 전단강도(강도) Vn 을 넘지 않는지 힘지배거동 안전성을
검토한다. 이 모듈은 **강도 Vn 산정(지침 정석 식)**을 담당하고, **수요 Vu 는 호출처가
주입**한다 — 현재는 간이(코어 총 횡력), 향후 코어 층별 결과 추출(#2)이 되면 주입값만
층별 정밀치로 교체하면 지침이 그리는 층별 연결부 검토(정석)가 완성된다(골격 불변).

[식 정리]
  Vn = min(Vn_a, Vn_b)
  Vn_a = T_col1 + 0.5·T_dw·cotθ + C_cb     (8-1a, 지압파괴 전)
  Vn_b = T_col1 + V_dw                      (8-1b, 지압파괴 후)
    T_col1 = fy·As,col1                      (8-2, 수집재철근 인장)
    T_dw   = min(fy·As,dw, Cds·sinθ)         (8-3, 다우얼 인장 — 스트럿압축 제한)
    C_cb   = 0.85·βn·fck,dw·hs·bw            (8-4, 벽 전면 지압)
    Cds    = 0.85·βs·fck·hs·lw·sinθ          (8-5, 스트럿 압축강도)
    V_dw   = min(μ·fy·As,dw, 0.2·fck·Ac,sw,
                 (3.3+0.08·fck)·Ac,sw, 11·Ac,sw)   (8-6, 전단마찰)
  Ac,sw = hs·lw,  βn=βs=0.6,  μ=1.0,  θ=45°
  단위: 길이 mm, 강도 MPa(=N/mm²) → 힘 N.
"""
import math
from dataclasses import dataclass

# 지침 고정 계수
BETA_N = 0.6        # 절점영역 콘크리트 유효압축 저감계수
BETA_S = 0.6        # 스트럿 유효압축 저감계수
MU = 1.0            # 접합면 마찰계수(일체 타설 또는 6mm 요철 거친면)
THETA_DEG = 45.0    # 스트럿 각도


@dataclass
class ConnectionStrength:
    """연결부 전단강도 산정 결과(단위 N)."""
    Vn: float        # = min(Vn_a, Vn_b) — 지배 강도
    Vn_a: float      # 지압파괴 전 (8-1a)
    Vn_b: float      # 지압파괴 후 (8-1b)
    T_col1: float    # 수집재 인장 (8-2)
    T_dw: float      # 다우얼 인장 (8-3, 제한 반영)
    C_cb: float      # 벽 전면 지압 (8-4)
    V_dw: float      # 전단마찰 (8-6, 상한 반영)
    governs: str     # '8-1a'(지압전) 또는 '8-1b'(지압후) 중 지배


def connection_shear_strength(As_col1: float, As_dw: float, fy: float,
                              fck: float, fck_dw: float,
                              hs: float, bw: float, lw: float) -> ConnectionStrength:
    """슬래브-벽체 연결부 전단강도 Vn (식 8-1~8-6). 모든 입력 mm·MPa, 반환 N.

    Args:
        As_col1: 수집재철근 단면적(벽 관통/정착) [mm²]
        As_dw:   슬래브 다우얼철근 단면적 [mm²]
        fy:      철근 항복강도 [MPa]
        fck:     슬래브 격막 콘크리트 압축강도 [MPa]
        fck_dw:  슬래브·벽체 콘크리트 압축강도 중 작은 값 [MPa]
        hs:      슬래브 격막 두께 [mm]
        bw:      벽체 두께 [mm]
        lw:      벽체 길이 [mm]
    """
    th = math.radians(THETA_DEG)
    cot_t = 1.0 / math.tan(th)
    sin_t = math.sin(th)
    a_csw = hs * lw                                  # Ac,sw

    t_col1 = fy * As_col1                            # 8-2
    c_ds = 0.85 * BETA_S * fck * hs * lw * sin_t     # 8-5
    t_dw = min(fy * As_dw, c_ds * sin_t)             # 8-3 (스트럿압축 제한)
    c_cb = 0.85 * BETA_N * fck_dw * hs * bw          # 8-4
    v_dw = min(MU * fy * As_dw,                      # 8-6 (전단마찰 상한)
               0.2 * fck * a_csw,
               (3.3 + 0.08 * fck) * a_csw,
               11.0 * a_csw)

    vn_a = t_col1 + 0.5 * t_dw * cot_t + c_cb        # 8-1a
    vn_b = t_col1 + v_dw                             # 8-1b
    vn = min(vn_a, vn_b)
    return ConnectionStrength(
        Vn=vn, Vn_a=vn_a, Vn_b=vn_b,
        T_col1=t_col1, T_dw=t_dw, C_cb=c_cb, V_dw=v_dw,
        governs='8-1a' if vn_a <= vn_b else '8-1b')


def core_lateral_demand(am, scene, base_reactions, direction: str = 'X') -> dict:
    """코어 그룹별 격막하중 수요 Vu (N) — 간이: 베이스 수평반력 합.

    각 코어 그룹이 기초에 전달하는 수평 반력의 합 = 그 코어가 받는 총 횡력 =
    전 층 격막하중의 합(보수적). 한 연결부에 전체 횡력을 거는 셈이라 안전측이다.
    향후 #2(코어 층별 결과 추출) 완성 시, 이 합 대신 층별 격막하중으로 교체하면
    지침의 층별 연결부 검토(정석)가 된다 — 반환 형식은 동일(그룹→수요).

    [베이스 노드 식별] walls 의 z 최소 코너(n_bl/n_br)만 기초 고정 → base_reactions
    에 존재. 적층으로 위층 베이스는 아래층 top(비고정)과 통합되어 여기 안 잡힌다.
    """
    from modular_3d.model import Core
    dof = 0 if direction.upper() == 'X' else 1
    node_group = {}
    for w in am.walls.values():
        gid = -1
        for cid in w.source_comp_ids:
            c = scene.components.get(cid)
            if isinstance(c, Core):
                gid = int(getattr(c, 'group_id', 0))
                break
        z_base = min(float(am.nodes[w.n_bl].coord[2]),
                     float(am.nodes[w.n_br].coord[2]))
        for nid in (w.n_bl, w.n_br):
            if abs(float(am.nodes[nid].coord[2]) - z_base) < 1.0:
                node_group[nid] = gid
    demand = {}
    for nid, gid in node_group.items():
        r = base_reactions.get(nid)
        if r is None:
            continue
        demand[gid] = demand.get(gid, 0.0) + abs(float(r[dof]))
    return demand


def core_floor_max_demand(am, scene, wall_responses,
                          direction: str = 'X') -> dict:
    """코어 그룹별 '층별 격막하중의 최댓값' Vu (N) — 정석 수요(간이보다 정밀).

    각 코어벽(MVLEM)의 전단력 = 그 층 단면이 받는 누적 횡력. 한 층 슬래브가 전달하는
    격막하중 = 그 층 누적전단력 − 위층 누적전단력(층 유입분). 연결부는 층마다이므로
    각 층 격막하중 중 최댓값을 그룹 수요로 쓴다(가장 불리한 층). 그룹 전체를 한
    연결부에 거는 간이값(core_lateral_demand)보다 덜 보수적이고 지침 층별 검토에 가깝다.

    wall_responses: OpsResults.wall_responses (wall_id → {shear:[deform, force], ...}).

    [보류 — 2026-06-29] MVLEM 벽 전단력(shear[1])이 코어 베이스 반력과 11~25% 불일치
    (벽 내부 전단력 ≠ 슬래브가 전달하는 격막하중). 층별 수요 신뢰 검증 전까지 check 는
    간이(베이스 반력)를 유지한다. 정석 층별 수요는 다이어프램 제약력 또는 층별 노드 반력
    등 별도 산정법이 필요하다(후속).
    """
    from modular_3d.model.core import Core
    # 그룹·층별 코어벽 전단력 합
    gf = {}   # (gid, floor) → 전단력 합 [N]
    for wid, w in am.walls.items():
        resp = (wall_responses or {}).get(wid)
        if not resp:
            continue
        sh = resp.get('shear') or []
        if len(sh) < 2:
            continue
        shear = abs(float(sh[1]))   # [deform, force] → force
        gid, fl = -1, 0
        for cid in w.source_comp_ids:
            c = scene.components.get(cid)
            if isinstance(c, Core):
                gid = int(getattr(c, 'group_id', 0))
                fl = int(getattr(c, 'floor_index', 0))
                break
        gf[(gid, fl)] = gf.get((gid, fl), 0.0) + shear
    # 그룹별 층 누적전단력 → 층 격막하중(= 누적 차) 최댓값
    groups = {}
    for (gid, fl), v in gf.items():
        groups.setdefault(gid, {})[fl] = v
    out = {}
    for gid, fls in groups.items():
        floors = sorted(fls)
        max_story = 0.0
        for i, fl in enumerate(floors):
            v_fl = fls[fl]
            v_up = fls[floors[i + 1]] if i + 1 < len(floors) else 0.0
            max_story = max(max_story, abs(v_fl - v_up))
        out[gid] = max_story
    return out


def core_floor_demand_by_reactions(am, scene, core_node_reactions,
                                   direction: str = 'X') -> dict:
    """코어 그룹별 '층별 격막→코어 전달력'의 최댓값 Vu (N) — ★정석 수요(2026-07-01).

    각 층 코어 노드의 수평반력(다이어프램 제약 전달력, OpsResults.core_node_reactions)을
    (그룹, z층)별로 합하면 그 층 슬래브가 코어로 전달하는 힘이 된다. 상부층 합 = 베이스
    반력으로 평형이 맞아(벽 내부 전단력 core_floor_max_demand 와 달리) 신뢰할 수 있다.
    연결부는 층마다이므로 베이스(기초 반력) 제외 상부 각 층 전달력의 최댓값을 그룹 수요로.
    """
    from modular_3d.model import Core
    dof = 0 if direction.upper() == 'X' else 1
    # 코어 노드 → (gid, z)  (노드별 1회 — 적층 공유노드 중복합산 방지)
    node_gz = {}
    for w in am.walls.values():
        gid = -1
        for cid in w.source_comp_ids:
            c = scene.components.get(cid)
            if isinstance(c, Core):
                gid = int(getattr(c, 'group_id', 0))
                break
        for nid in (w.n_bl, w.n_br, w.n_tr, w.n_tl):
            if nid not in node_gz:
                node_gz[nid] = (gid, round(float(am.nodes[nid].coord[2]), -1))
    # (그룹, z) 별 수평반력 합
    gz = {}
    for nid, (gid, z) in node_gz.items():
        r = core_node_reactions.get(nid)
        if r is None:
            continue
        gz[(gid, z)] = gz.get((gid, z), 0.0) + float(r[dof])
    # 그룹별: 베이스(z 최소, 기초반력) 제외 상부층 전달력 abs 최댓값
    grp = {}
    for (gid, z), v in gz.items():
        grp.setdefault(gid, {})[z] = v
    out = {}
    for gid, zv in grp.items():
        if not zv:
            continue
        zmin = min(zv)
        stories = [abs(v) for z, v in zv.items() if abs(z - zmin) > 1.0]
        out[gid] = max(stories) if stories else abs(zv[zmin])
    return out


def check_core_group_connections(am, scene, base_reactions,
                                 direction: str = 'X', phi: float = 0.75,
                                 wall_responses=None,
                                 core_node_reactions=None) -> dict:
    """코어 그룹별 슬래브-벽체 연결부 전단강도 검토. 반환 {group_id: 결과 dict}.

    - 수요 Vu = core_lateral_demand (그룹 베이스 수평반력 합).
    - 강도 ΣVn = 그룹 내 1층 코어벽 각각의 connection_shear_strength 합. 슬래브 철근
      (As,col1·As,dw·slab_fck)은 그룹 공통(core_rebar), 형상은 벽별(lw·bw)·슬래브 두께 hs.
    - 검토: Vu ≤ φ·ΣVn 이면 안전. (벽별 수요는 #2 코어 층별 결과 완성 시 정밀화 가능.)
    """
    from modular_3d.model.core import normalize_core_rebar, Core, CoreSlab
    # 정석 수요 우선순위: 코어 노드반력(다이어프램 전달력, 평형 검증·신뢰) >
    #   벽 전단력(구·불일치) > 간이(베이스 반력 합, 보수적 폴백).
    if core_node_reactions:
        demand = core_floor_demand_by_reactions(am, scene, core_node_reactions, direction)
        demand_mode = 'floor'
    elif wall_responses:
        demand = core_floor_max_demand(am, scene, wall_responses, direction)
        demand_mode = 'floor_wall'
    else:
        demand = core_lateral_demand(am, scene, base_reactions, direction)
        demand_mode = 'base'
    out = {}
    for gid, vu in demand.items():
        rc = normalize_core_rebar(scene.core_rebar.get(gid))
        as_col1 = rc['slab_collector_area']
        as_dw = rc['slab_dowel_area']
        fck_slab = rc['slab_fck']
        fck_wall = rc['fck']
        fy = rc['fy']
        # 그룹 슬래브 두께 hs (CoreSlab) — 없으면 기본 180mm
        hs = 180.0
        for c in scene.components.values():
            if isinstance(c, CoreSlab) and int(getattr(c, 'group_id', 0)) == gid:
                hs = float(c.dimensions.get('thickness', 180.0))
                break
        # 그룹 내 1층 코어벽별 연결부 강도 합
        sum_vn = 0.0
        n_wall = 0
        gov = set()
        for c in scene.components.values():
            if (isinstance(c, Core) and int(getattr(c, 'group_id', 0)) == gid
                    and int(getattr(c, 'floor_index', 0)) == 0):
                lw = float(c.dimensions['width'])
                bw = float(c.dimensions['depth'])
                s = connection_shear_strength(
                    as_col1, as_dw, fy, fck_slab, min(fck_slab, fck_wall), hs, bw, lw)
                sum_vn += s.Vn
                n_wall += 1
                gov.add(s.governs)
        phi_vn = phi * sum_vn
        ratio = (vu / phi_vn) if phi_vn > 0 else float('inf')
        out[gid] = {
            'Vu': vu, 'sumVn': sum_vn, 'phiVn': phi_vn,
            'ratio': ratio, 'safe': ratio <= 1.0,
            'n_wall': n_wall, 'governs': ','.join(sorted(gov)),
            'demand_mode': demand_mode,
        }
    return out


def check_connection(Vu: float, strength: ConnectionStrength,
                     phi: float = 0.75) -> dict:
    """수요 Vu(N) vs 설계강도 φ·Vn 힘지배거동 검토.

    phi: 전단 강도감소계수(KDS, 기본 0.75). 안전하면 OK.
    """
    v_design = phi * strength.Vn
    ratio = (Vu / v_design) if v_design > 0 else float('inf')
    return {
        'Vu': Vu, 'Vn': strength.Vn, 'phiVn': v_design,
        'ratio': ratio, 'safe': ratio <= 1.0, 'governs': strength.governs,
    }
