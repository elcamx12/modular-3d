"""scene3 group 6 wall #27 column 의 실제 축력 추출 — 부재 단면력으로 직접 확인.

목표: wall #27 column 이 실제로 위층 하중을 받고 있는지 확인.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modular_3d.io.scene_io import load_scene
from modular_3d.analysis.topology import build_analysis_model
from modular_3d.analysis.ops_builder import build_ops_model
from modular_3d.analysis.ops_solver import solve_all_cases


def main(scene_path: str | None = None):
    # 기본 scene = 프로젝트 내 회귀 테스트 데이터 (상대 경로).
    if scene_path is None:
        # parents[1] = dev_tools, sibling 폴더 refactor_tools 안.
        _dev_tools = Path(__file__).resolve().parents[1]
        scene_path = str(_dev_tools / 'refactor_tools' / 'regression_scenes' / 'synth_b.json')
    scene, n = load_scene(scene_path)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_all_cases(scene)['D+L']

    # group 6 의 wall_column member 들 — 각 부재의 양단 axial force (N) 출력
    print('=== Group 6 wall_column 부재 축력 (N: 음수=압축) ===')
    for mid, m in am.members.items():
        if m.role != 'wall_column':
            continue
        if not m.source_comp_ids or m.source_comp_ids[0] not in (27, 28, 29, 30, 31, 32):
            continue
        wcid = m.source_comp_ids[0]
        c1 = am.nodes[m.n1].coord
        c2 = am.nodes[m.n2].coord
        mf = res.member_forces.get(mid)
        if mf is None:
            print(f'  wall #{wcid} mid={mid} (no force result)')
            continue
        N_i = mf.f_i[0] / 1000  # kN
        N_j = mf.f_j[0] / 1000
        print(f'  wall #{wcid} mid={mid}  '
              f'n{m.n1}@z={c1[2]:6.0f} ↔ n{m.n2}@z={c2[2]:6.0f}  '
              f'N_i={N_i:8.2f} kN  N_j={N_j:8.2f} kN')

    # [2026-05-18] om.interface_links 자료구조 폐기 — 벽 적층 결합은
    # om.spec.equal_dofs 의 wall_stack 류 rule_id 항목으로 조회.
    if om.spec is not None:
        ws = [ed for ed in om.spec.equal_dofs if 'wall' in (ed.rule_id or '').lower()]
        print(f'\n=== wall 관련 equal_dofs: {len(ws)} ===')
        for ed in ws[:30]:
            print(f'  {ed}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
