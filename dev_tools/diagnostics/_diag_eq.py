"""scene3 평형 오차 12.78 % 의 정확한 위치 추적.

- 각 컴포넌트 group 별 적용 하중 합 vs 그 group 의 베이스 노드 반력 합
- 어느 group 의 부재 부하가 잃어버리는지 식별
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modular_3d.io.scene_io import load_scene
from modular_3d.analysis.topology import build_analysis_model
from modular_3d.analysis.ops_builder import build_ops_model
from modular_3d.analysis.ops_solver import solve_all_cases


def main(scene_path: str):
    scene, n_floors = load_scene(scene_path)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_all_cases(scene)['D+L']

    # 1) Group 별 컴포넌트 분류
    print("=== 컴포넌트 group 분포 ===")
    by_group = defaultdict(list)
    for cid, comp in scene.components.items():
        gid = getattr(comp, 'group_id', 0)
        by_group[gid].append(cid)
    for gid in sorted(by_group):
        comps = by_group[gid]
        types = defaultdict(int)
        for cid in comps:
            types[scene.components[cid].comp_type.value] += 1
        type_str = ' '.join(f'{k}={v}' for k, v in types.items())
        # group 의 1층 본체 컴포넌트 위치
        body_cid = None
        for cid in comps:
            c = scene.components[cid]
            if (getattr(c, 'sub_index', 0) == 0
                    and getattr(c, 'floor_index', 0) == 0):
                body_cid = cid
                break
        body_pos = ''
        if body_cid is not None:
            p = scene.components[body_cid].position
            body_pos = f' @ ({p[0]:6.0f},{p[1]:6.0f})'
        print(f"  group {gid:2d} ({len(comps):3d} comps): {type_str}{body_pos}")

    # 2) Fixed 노드의 컴포넌트 추적 (어느 group 의 컴포넌트에 속하는지)
    print()
    print("=== Fixed 노드별 Rz + 소속 group ===")
    # node id → component id
    nid_to_cid = {}
    for cid, comp in scene.components.items():
        # Module/Vertical3Module 의 columns 를 추적
        if not hasattr(comp, 'columns'):
            continue
        for col in comp.columns:
            # column.base 의 좌표와 가까운 노드를 찾기
            pass

    # 더 단순: am.nodes 에서 컴포넌트 ID 매핑이 있는지 확인
    # 없으면 좌표로 부재 bounding box 안에 들어가는지 추정
    import numpy as np
    nid_to_source = {}
    for nid, n in am.nodes.items():
        src = getattr(n, 'source_component_id', None)
        if src is not None:
            nid_to_source[nid] = src
            continue
        # 좌표로 추정 — 가장 가까운 모듈/Vertical3 의 column/center 위치
        coord = np.asarray(n.coord)
        best_cid = None
        best_d = float('inf')
        for cid, comp in scene.components.items():
            if not hasattr(comp, 'columns') or not comp.columns:
                continue
            for col in comp.columns:
                cb = np.asarray(col.base)
                d = float(np.linalg.norm(coord[:2] - cb[:2]))
                # z 도 가까워야 함 (column z range)
                z_min = min(col.base[2], col.top[2])
                z_max = max(col.base[2], col.top[2])
                if not (z_min - 100 <= coord[2] <= z_max + 100):
                    continue
                if d < best_d:
                    best_d = d
                    best_cid = cid
        if best_cid is not None and best_d < 50.0:
            nid_to_source[nid] = best_cid
        else:
            nid_to_source[nid] = None

    # 베이스 노드 반력 출력 + group_id 매핑
    # [2026-05-13 가상 코어 제거] om.core 분기 폐기 — 사용자 코어 base 는
    # om.fixed_nodes 에 이미 포함됨.
    fixed_set = set(om.fixed_nodes)
    rxs = []
    for nid, r in res.base_reactions.items():
        cid = nid_to_source.get(nid)
        gid = scene.components[cid].group_id if cid in scene.components else None
        rxs.append((nid, r[2] / 1000, cid, gid))
    rxs.sort(key=lambda x: x[0])
    for nid, rz, cid, gid in rxs:
        print(f"  nid={nid:6d}  Rz={rz:8.2f} kN  comp={cid}  group={gid}")

    # 3) Group 별 반력 합산
    print()
    print("=== Group 별 베이스 반력 합 (Rz) ===")
    by_gid = defaultdict(float)
    for nid, rz, cid, gid in rxs:
        if gid is not None:
            by_gid[gid] += rz
    total = 0.0
    for gid in sorted(by_gid):
        print(f"  group {gid:2d}: {by_gid[gid]:9.2f} kN")
        total += by_gid[gid]
    print(f"  -- 합계 (group 분류된 것): {total:.2f} kN")

    # 4) 적용 하중 vs 반력
    print()
    applied = res.total_applied_load_z / 1000
    react = res.total_base_reaction_z / 1000
    print(f"=== 평형 ===")
    print(f"  적용 하중 Wz: {applied:9.2f} kN")
    print(f"  반력 합 Rz   : {react:9.2f} kN")
    print(f"  잔여        : {(react + applied):9.2f} kN ({100 * abs(react + applied) / abs(applied):.2f} %)")


if __name__ == '__main__':
    # 기본 scene = 프로젝트 내 회귀 테스트 데이터 (상대 경로).
    # exe 배포·다른 머신 호환을 위해 사용자 계정명이 박힌 절대 경로 폐기.
    # parents[1] = dev_tools, sibling 폴더 refactor_tools 안에서 scene 가져오기.
    _dev_tools = Path(__file__).resolve().parents[1]
    default_scene = _dev_tools / 'refactor_tools' / 'regression_scenes' / 'synth_b.json'
    p = sys.argv[1] if len(sys.argv) > 1 else str(default_scene)
    main(p)
