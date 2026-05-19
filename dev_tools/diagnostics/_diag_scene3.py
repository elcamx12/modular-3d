# -*- coding: utf-8 -*-
"""scene3.json 평형 진단 스크립트.

D+L 케이스로 해석을 돌려서:
- 적용 하중 합 (Wz)
- 베이스 반력 합 (Rz)
- 평형 오차 (%)
- fixed_nodes 와 core base_node 의 개별 반력
- 노드별 적용 외력 합 (eleLoad 검증)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # my_project/
sys.path.insert(0, str(ROOT))

from modular_3d.io.scene_io import load_scene
from modular_3d.analysis.topology import build_analysis_model
from modular_3d.analysis.ops_builder import build_ops_model
from modular_3d.analysis.ops_solver import solve_all_cases


def main(scene_path: str) -> None:
    scene, n_floors = load_scene(scene_path)
    print(f"[load] n_floors={n_floors}")
    print(f"[load] components: {len(scene.components)}")

    am = build_analysis_model(scene)
    print(f"[topo] nodes: {len(am.nodes)}, members: {len(am.members)}")

    om = build_ops_model(am, scene=scene)
    print(f"[ops]  fixed_nodes: {len(om.fixed_nodes)}")
    # [2026-05-13 가상 코어 제거] om.core 분기 폐기.
    print(f"[ops]  diaphragms: {len(om.diaphragms)}")

    # 모든 케이스 실행
    results = solve_all_cases(scene)
    for name, res in results.items():
        print(f"\n=== {name} ===")
        print(res.summary())
        print(f"  Wz applied = {res.total_applied_load_z/1000:.3f} kN")
        print(f"  Rz reaction = {res.total_base_reaction_z/1000:.3f} kN")
        if abs(res.total_applied_load_z) > 1.0:
            residual = res.total_base_reaction_z + res.total_applied_load_z
            print(f"  residual = {residual/1000:.3f} kN ({res.equilibrium_ratio*100:.3f} %)")
        # fixed 노드별 Rz 분포
        if name == 'D+L':
            print(f"\n  -- fixed node reactions (Rz, kN) --")
            entries = []
            for nid, r in res.base_reactions.items():
                entries.append((nid, r[2] / 1000))
            entries.sort(key=lambda x: x[0])
            tot = 0.0
            for nid, rz in entries:
                tot += rz
                print(f"    nid={nid:4d}  Rz={rz:10.3f}")
            print(f"    -- sum = {tot:.3f} kN ({len(entries)} nodes) --")


if __name__ == '__main__':
    # 기본 scene = 프로젝트 내 회귀 테스트 데이터 (상대 경로).
    # exe 배포·다른 머신 호환을 위해 사용자 계정명이 박힌 절대 경로 폐기.
    # ROOT = my_project. 회귀 scene 은 dev_tools/refactor_tools 안.
    default_scene = ROOT / 'dev_tools' / 'refactor_tools' / 'regression_scenes' / 'synth_b.json'
    scene = sys.argv[1] if len(sys.argv) > 1 else str(default_scene)
    main(scene)
