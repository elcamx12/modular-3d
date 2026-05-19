"""UI 측 조인트 기록기 (2026-05-12 신규, 옵션 (나)).

[목적]
화면에서 부재가 배치/이동된 직후 각 플로어패널·캔틸레버슬래브의 4 코너가
어떤 부재의 어떤 지점에 결합되는지를 기하 입사 검사로 명시 기록한다.
joint_rules 의 패널 룰(R03·R04·R05·R06) 이 거리 기반 fuzzy 매칭 대신 본
기록을 1순위로 사용한다. (옛 _resolve_floor_panel_supports 는 폐기됨.)

[배경]
이전: UI 자동 갭/스냅과 해석 거리 매칭이 별개로 동작. 1F 바닥패널 외톨이
코너처럼 350mm xy 허용오차를 넘어가는 경우 매칭 실패 → 평형 손실.
현재: 본 모듈이 UI 인식 접합을 그대로 기록 → 해석은 기록만 따름.

[기록 대상]
- FloorPanel        : 4 바닥 코너 → 하부 모듈/벽/V3M 의 기둥 노드
- CantileverSlab    : 4 바닥 코너 → 부모 모듈 코너 + far 코너 인접 기둥
                      (확장 여지, 현재는 FP 만 처리)

[매칭 규칙]
각 코너 (cx, cy, fp_z) 에 대해 씬 내 모든 후보 부재 (Module/StructWall/
Vertical3Module) 의 기둥 4 (or 2) 코너 xy 를 비교:
  - xy 거리 ≤ JOINT_TOL_XY (=350 mm)
  - z 거리 ≤ JOINT_TOL_Z (=500 mm) — 기둥 상단 또는 하단 중 가까운 쪽
가장 가까운 1개를 채택. 같으면 z 가 더 가까운 쪽.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import numpy as np

from modular_3d.model.core import (
    Component, FloorPanel, CantileverSlab, CantileverBeam,
    Module, StructWall, Vertical3Module, Core,
)
from modular_3d._utils.debug import dprint


# 코너-기둥 매칭 허용오차 — 해석 측 FP_SUPPORT_TOL_XY/Z 와 별개로
# UI 인식 단계에서 사용. 충분히 넉넉하게 잡아 거리 매칭의 사각지대를 흡수.
JOINT_TOL_XY = 350.0   # mm — 해석측 FP_SUPPORT_TOL_XY 와 일치
JOINT_TOL_Z = 500.0    # mm


def _column_xy_list(comp: Component) -> List[Tuple[float, float, float, float]]:
    """본 컴포넌트의 기둥들 (cx, cy, z_top, z_bot) 목록.

    Module/V3M : 4 코너 기둥
    StructWall : 2 코너 기둥
    Core       : 1 wide-column (4 코너 정보는 슬래브 매칭용 별도 처리)
    그 외      : 빈 리스트
    """
    out: List[Tuple[float, float, float, float]] = []
    if isinstance(comp, Module):
        for col in comp.columns:
            out.append((float(col.base[0]), float(col.base[1]),
                        float(col.top[2]), float(col.base[2])))
    elif isinstance(comp, Vertical3Module):
        for col in comp.columns:
            out.append((float(col.base[0]), float(col.base[1]),
                        float(col.top[2]), float(col.base[2])))
    elif isinstance(comp, StructWall):
        for col in comp.columns:
            out.append((float(col.base[0]), float(col.base[1]),
                        float(col.top[2]), float(col.base[2])))
    elif isinstance(comp, Core):
        if comp.column is not None:
            out.append((float(comp.column.base[0]), float(comp.column.base[1]),
                        float(comp.column.top[2]), float(comp.column.base[2])))
    return out


def _cantilever_endpoints(comp: Component) -> List[Tuple[float, float, float]]:
    """캔틸레버 부재의 자유단 끝점만 후보로 — (x, y, z) 목록.

    [후보 — 사용자 답 2.1]
    - CantileverBeam : 보의 끝점 중 부모와 멀리 떨어진 쪽 (자유단) 1 개.
    - CantileverSlab : 4 코너 중 부모(슬래브 anchor) 와 반대편 far 코너 2 개.

    [근거]
    anchor 코너는 부모 모듈/패널과 자연 결합되어 있어 다른 FP 코너가 매칭될
    이유가 없다. 자유단 끝만 외팔보 끝점 후보로 사용.
    """
    out: List[Tuple[float, float, float]] = []
    if isinstance(comp, CantileverBeam):
        if comp.beam is not None:
            # 부모와 멀리 떨어진 쪽 — local x=w 의 world 변환점.
            # CantileverBeam.generate_sub_components: beam.start = (x=0), beam.end = (x=w).
            # 자유단 = end.
            out.append((float(comp.beam.end[0]), float(comp.beam.end[1]),
                        float(comp.beam.end[2])))
    elif isinstance(comp, CantileverSlab):
        # CantileverSlab.generate_sub_components: anchor 코너는 local x=0 쪽 2 개.
        # 자유단 코너는 local x=w 쪽 2 개. get_world_corners 의 4 코너 순서
        # (LL, LR, UR, UL) — rotation 0 기준 LR(1), UR(2) 가 자유단.
        try:
            corners = comp.get_world_corners()[:4]
            for i in (1, 2):
                out.append((float(corners[i][0]), float(corners[i][1]),
                            float(corners[i][2])))
        except Exception as e:
            # silent fail 시 R03~R06 패널 룰이 자유단 좌표를 못 받아 결합이
            # 생성되지 않는 위험. 사용자가 모르고 지나가지 않도록 콘솔 보고.
            dprint('joint_recorder', f"[joint_recorder] CantileverSlab cid={getattr(comp, 'id', '?')} 자유단 코너 추출 실패: {e}")
    return out


def _fp_bottom_corners(comp: Component) -> np.ndarray:
    """FloorPanel/CantileverSlab 의 4 바닥 코너 (월드) — shape=(4,3).

    get_world_corners 는 8 코너 (바닥 4 + 상단 4) 반환. 바닥 4 만 잘라 씀.
    바닥 z = comp.position.z (FP slab 바닥 보 중심선 높이).
    """
    return comp.get_world_corners()[:4].copy()


def _best_column_for_corner(corner_xy: np.ndarray, corner_z: float,
                             scene_components: Dict[int, Component],
                             self_id: int) -> Optional[Dict]:
    """단일 코너에 대해 가장 가까운 기둥 단을 찾아 record dict 반환.

    Returns: {'target_comp_id', 'target_xy', 'target_z'} 또는 None.
    """
    best: Optional[Dict] = None
    best_xy: float = JOINT_TOL_XY
    best_dz: float = JOINT_TOL_Z
    for cid, comp in scene_components.items():
        if cid == self_id:
            continue

        # 1) 캔틸레버 끝점 후보 — 부재 자유단(외팔보 끝) 또는 슬래브 코너
        cant_pts = _cantilever_endpoints(comp)
        for cx, cy, cz in cant_pts:
            dz = abs(corner_z - cz)
            if dz > JOINT_TOL_Z:
                continue
            dxy = float(np.hypot(corner_xy[0] - cx, corner_xy[1] - cy))
            if dxy > JOINT_TOL_XY:
                continue
            better = (dxy < best_xy - 1.0
                      or (dxy < best_xy + 1.0 and dz < best_dz))
            if better:
                best_xy = dxy
                best_dz = dz
                best = {
                    'target_comp_id': int(cid),
                    'target_xy': [float(cx), float(cy)],
                    'target_z': float(cz),
                }

        # 2) 기둥 후보 (기존)
        cols = _column_xy_list(comp)
        if not cols:
            continue
        for (cx, cy, z_top, z_bot) in cols:
            # [의미적 규칙] 패널은 기둥 위에 얹히는 것이 정상.
            #   - 기둥 상단(z_top) ≤ 코너 z 이면 z_top 채택 (얹힘).
            #   - 그 외 (코너가 기둥 위로 멀거나 아래) → 가장 가까운 단으로 폴백.
            # 우선순위로 "위에 얹힘"을 항상 1순위로 본다.
            preferred_z: Optional[float] = None
            preferred_dz: float = float('inf')
            if z_top - corner_z <= 50.0:  # 코너가 기둥 상단보다 위(또는 50mm 안쪽 아래)
                preferred_z = z_top
                preferred_dz = abs(corner_z - z_top)
            elif z_bot - corner_z <= 50.0 and z_bot - corner_z >= -JOINT_TOL_Z:
                # 1F 바닥 패널처럼 코너 z 가 기둥 하단(=base, z=-100) 근방인 경우
                preferred_z = z_bot
                preferred_dz = abs(corner_z - z_bot)
            else:
                # 폴백: 가장 가까운 단
                dz_top = abs(corner_z - z_top)
                dz_bot = abs(corner_z - z_bot)
                if dz_top <= dz_bot:
                    preferred_z = z_top
                    preferred_dz = dz_top
                else:
                    preferred_z = z_bot
                    preferred_dz = dz_bot
            if preferred_dz > JOINT_TOL_Z:
                continue
            dxy = float(np.hypot(corner_xy[0] - cx, corner_xy[1] - cy))
            if dxy > JOINT_TOL_XY:
                continue
            # 우선순위: xy 가 더 가까우면 무조건 채택; 동률(1mm 이내) 이면 dz 더 가까운 쪽
            better = (dxy < best_xy - 1.0
                      or (dxy < best_xy + 1.0 and preferred_dz < best_dz))
            if better:
                best_xy = dxy
                best_dz = preferred_dz
                best = {
                    'target_comp_id': int(cid),
                    'target_xy': [float(cx), float(cy)],
                    'target_z': float(preferred_z),
                }
    return best


def record_joints(scene) -> int:
    """씬 전체를 스캔해 FloorPanel·CantileverSlab 의 joint_records 채움.

    멱등(idempotent) — 매번 새로 계산해 덮어쓴다.
    Returns: 기록된 코너 수 (모든 FP·CS 합산).
    """
    n_recorded = 0
    for cid, comp in scene.components.items():
        if not isinstance(comp, (FloorPanel, CantileverSlab)):
            continue
        try:
            corners = _fp_bottom_corners(comp)
        except Exception as e:
            # 코너 좌표 추출 실패 시 해당 패널의 조인트 기록이 비어 패널 룰이
            # 결합을 못 만든다. 사용자에게 알림.
            dprint('joint_recorder', f'[joint_recorder] cid={cid} 코너 추출 실패 → 조인트 기록 비움: {e}')
            comp.joint_records = []
            continue
        records: List[Dict] = []
        for i in range(4):
            cx, cy, cz = float(corners[i][0]), float(corners[i][1]), float(corners[i][2])
            rec = _best_column_for_corner(
                np.array([cx, cy]), cz, scene.components, cid,
            )
            if rec is None:
                continue
            rec['corner_idx'] = int(i)
            records.append(rec)
            n_recorded += 1
        comp.joint_records = records
    return n_recorded
