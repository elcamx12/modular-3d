"""scene3 group 6 wall+FP 타워의 노드·결합 체인 추적.

목표: 6 층짜리 wall 적층의 수직 하중 path 가 끊긴 지점 찾기.

검사 항목:
1. group 6 의 모든 wall 컴포넌트별 column 노드 좌표 + 노드 ID
2. floor i wall top z ↔ floor i+1 wall bottom z 사이 노드 merge / interface_link 존재 여부
3. group 1 FP 의 코너 노드 ↔ group 6 wall column top 노드 간 결합 (wall_panel_connections)
4. 6 층 누적 down 경로가 fixed_node 에 도달하는지
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np

from modular_3d.io.scene_io import load_scene
from modular_3d.analysis.topology import build_analysis_model
from modular_3d.analysis.ops_builder import build_ops_model


def main(scene_path: str):
    scene, n_floors = load_scene(scene_path)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)

    print('=== Group 6 wall 컴포넌트별 column 좌표 ===')
    for cid, comp in scene.components.items():
        if getattr(comp, 'group_id', 0) != 6:
            continue
        print(f'\n  wall #{cid} (floor {comp.floor_index}, sub {comp.sub_index})')
        print(f'    pos          : {comp.position}')
        print(f'    dims         : w={comp.dimensions.get("width")}, '
              f'd={comp.dimensions.get("depth")}, h={comp.dimensions.get("height")}')
        print(f'    merge_with_fp: {comp.merge_with_panel}, merged_fp_id: {comp.merged_fp_id}')
        if hasattr(comp, 'columns') and comp.columns:
            for i, col in enumerate(comp.columns):
                print(f'    col {i}: base={tuple(col.base)} top={tuple(col.top)}')

    print('\n=== Group 6 wall 의 column 노드 ID (am.nodes) ===')
    g6_node_ids = []
    for cid, comp in scene.components.items():
        if getattr(comp, 'group_id', 0) != 6:
            continue
        if not hasattr(comp, 'columns') or not comp.columns:
            continue
        for i, col in enumerate(comp.columns):
            # 가장 가까운 노드 찾기
            cb = np.asarray(col.base)
            ct = np.asarray(col.top)
            base_nid = None
            top_nid = None
            min_db = float('inf')
            min_dt = float('inf')
            for nid, n in am.nodes.items():
                nc = np.asarray(n.coord)
                db = float(np.linalg.norm(nc - cb))
                dt = float(np.linalg.norm(nc - ct))
                if db < min_db:
                    min_db = db
                    base_nid = nid
                if dt < min_dt:
                    min_dt = dt
                    top_nid = nid
            print(f'  wall #{cid} col {i}: base nid={base_nid}@{tuple(am.nodes[base_nid].coord)} '
                  f'(d={min_db:.1f})  top nid={top_nid}@{tuple(am.nodes[top_nid].coord)} '
                  f'(d={min_dt:.1f})')
            g6_node_ids.extend([base_nid, top_nid])

    print('\n=== Group 6 노드 z 좌표 분포 ===')
    z_groups = defaultdict(list)
    for nid in set(g6_node_ids):
        coord = am.nodes[nid].coord
        z = round(float(coord[2]) / 10) * 10  # 10mm 단위
        z_groups[z].append(nid)
    for z in sorted(z_groups):
        print(f'  z={z:6.0f}: {len(z_groups[z])}개 nodes — {sorted(z_groups[z])}')

    print('\n=== Group 1 FP 코너 노드 (am.nodes) ===')
    for cid, comp in scene.components.items():
        if getattr(comp, 'group_id', 0) != 1:
            continue
        # FP 코너 좌표 = position 기준 4 코너
        bmin, bmax = comp.get_bounding_box()
        corners = [
            (bmin[0], bmin[1], bmax[2]),
            (bmax[0], bmin[1], bmax[2]),
            (bmax[0], bmax[1], bmax[2]),
            (bmin[0], bmax[1], bmax[2]),
        ]
        print(f'  FP #{cid} (floor {comp.floor_index}) corners (top z={bmax[2]:.0f}):')
        for cor in corners:
            min_d = float('inf')
            best_nid = None
            for nid, n in am.nodes.items():
                d = float(np.linalg.norm(np.asarray(n.coord) - np.asarray(cor)))
                if d < min_d:
                    min_d = d
                    best_nid = nid
            print(f'    {cor} → nid={best_nid}@{tuple(am.nodes[best_nid].coord)} (d={min_d:.1f})')

    # [2026-05-18] am.wall_panel_connections / am.interface_links 자료구조 폐기.
    # joint_rules 의 결합은 om.spec.equal_dofs / spec.rigid_links 에 있음.
    print('\n=== om.spec.equal_dofs (group 6 ↔ 외부 결합) ===')
    if om.spec is not None:
        g6_ids = {27, 28, 29, 30, 31, 32}
        rel = []
        for ed in om.spec.equal_dofs:
            for nid in (ed.master, ed.slave):
                n = am.nodes.get(nid)
                if n is None:
                    continue
                if getattr(n, 'comp_id', -1) in g6_ids or getattr(n, 'source_comp_id', -1) in g6_ids:
                    rel.append(ed)
                    break
        print(f'  group 6 관련 equal_dofs: {len(rel)}개')
        for ed in rel[:10]:
            print(f'    {ed}')

    print('\n=== fixed_nodes 중 group 6 column base ===')
    for nid in om.fixed_nodes:
        n = am.nodes.get(nid)
        if n is None:
            continue
        coord = np.asarray(n.coord)
        for cid, comp in scene.components.items():
            if getattr(comp, 'group_id', 0) != 6:
                continue
            if not hasattr(comp, 'columns'):
                continue
            for i, col in enumerate(comp.columns):
                d = float(np.linalg.norm(coord - np.asarray(col.base)))
                if d < 5.0:
                    print(f'  fixed nid={nid}@{tuple(coord)} ← wall #{cid} col {i}')


if __name__ == '__main__':
    # 기본 scene = 프로젝트 내 회귀 테스트 데이터 (상대 경로).
    # parents[1] = dev_tools, sibling 폴더 refactor_tools 안에서 scene 가져오기.
    _dev_tools = Path(__file__).resolve().parents[1]
    default_scene = _dev_tools / 'refactor_tools' / 'regression_scenes' / 'synth_b.json'
    p = sys.argv[1] if len(sys.argv) > 1 else str(default_scene)
    main(p)
