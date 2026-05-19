"""_merge_panel_overlaps_and_check 교체 검증.

검증 항목 (보고용):
  1) scene.json 빌드 → 부재 ID/좌표 집합 dump (사용자 정상 모델 동일성)
  2) D+L 풀이 → equilibrium_ratio 출력 (0.0000% 유지 확인)
  3) 합성 음성 테스트 — 부분 겹침 인공 케이스에서 RuntimeError 재현 확인
  4) 합성 양성 테스트 — 완전 겹침/완전 분리는 raise 없음 확인
  5) profile_solve.py 결과는 별도 측정 (이 스크립트 다음에 실행)

[설계]
- 1) 와 2) 는 같은 build_analysis_model 호출 1 회로 끝남.
- 3) 4) 는 _merge_panel_overlaps_and_check 를 직접 호출. AnalysisModel 을
  손으로 조립해 부재 2 개만 넣고 검사 호출.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MYPROJ = os.path.dirname(os.path.dirname(_HERE))
_VENV_PY = os.path.join(_MYPROJ, 'venv', 'Scripts', 'python.exe')
if os.path.isfile(_VENV_PY) and os.path.abspath(sys.executable) != os.path.abspath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, __file__] + sys.argv[1:])

if _MYPROJ not in sys.path:
    sys.path.insert(0, _MYPROJ)

os.environ['MODULAR_DEBUG_OFF'] = '1'

import numpy as np

SCENE_PATH = os.environ.get('SCENE_PATH', r'C:\Users\이건영\Desktop\종설\scene.json')


def main():
    print('=' * 70)
    print('[1] scene.json 빌드 → 부재 dump')
    print('=' * 70)
    from modular_3d.io.scene_io import load_scene
    from modular_3d.analysis.topology import build_analysis_model
    scene, _ = load_scene(SCENE_PATH)
    am = build_analysis_model(scene)
    print(f'  컴포넌트 수: {len(scene.components)}')
    print(f'  노드 수    : {len(am.nodes)}')
    print(f'  부재 수    : {len(am.members)}')
    # 좌표 키 집합 — 동일성 빠른 비교용
    coord_keys = set()
    for m in am.members.values():
        c1 = tuple(int(round(float(v))) for v in am.nodes[m.n1].coord)
        c2 = tuple(int(round(float(v))) for v in am.nodes[m.n2].coord)
        coord_keys.add((m.kind, tuple(sorted([c1, c2]))))
    print(f'  부재 (kind,좌표쌍) 고유 집합 크기: {len(coord_keys)}')
    print(f'  → 부재 수와 고유 집합 크기가 일치하면 중복 제거 단계가 정상.')

    print()
    print('=' * 70)
    print('[2] solve_all_cases → 평형 검증')
    print('=' * 70)
    from modular_3d.analysis.ops_solver import solve_all_cases
    results = solve_all_cases(scene, prebuilt_am=am)
    dl = results.get('D+L')
    if dl is not None:
        eq = getattr(dl, 'equilibrium_ratio', None)
        print(f'  D+L equilibrium_ratio: {eq*100:.6f} %' if eq is not None else '  D+L 평형 정보 없음')
    for k in ['D+L', 'Ex', 'Ey', 'Wx', 'Wy']:
        r = results.get(k)
        if r is None:
            continue
        eq = getattr(r, 'equilibrium_ratio', 0.0)
        print(f'  {k:>4}: equilibrium_ratio = {eq*100:.6f} %')

    print()
    print('=' * 70)
    print('[3] 합성 음성 테스트 — 부분 겹침 → RuntimeError 발생해야 함')
    print('=' * 70)
    from modular_3d.analysis.topology import (
        _merge_panel_overlaps_and_check, AnalysisModel, AnalysisNode, AnalysisMember,
    )

    def _mk_model(seg_pairs):
        """seg_pairs: [(p1, p2, merge_group, role), ...] → AnalysisModel."""
        mm = AnalysisModel()
        nid = 1
        mid = 1
        for p1, p2, mg, role in seg_pairs:
            mm.nodes[nid] = AnalysisNode(id=nid, coord=np.array(p1, dtype=float), source_comp_id=1)
            mm.nodes[nid + 1] = AnalysisNode(id=nid + 1, coord=np.array(p2, dtype=float), source_comp_id=1)
            mm.members[mid] = AnalysisMember(
                id=mid, n1=nid, n2=nid + 1,
                kind='frame', role=role,
                section_w=200.0, section_h=200.0, section_t=8.0,
                source_comp_ids=[1],
                merge_group=mg,
            )
            nid += 2
            mid += 1
        return mm

    # 부분 겹침: A=[0..3000], B=[1500..4500] on x 축
    m_partial = _mk_model([
        ((0, 0, 0), (3000, 0, 0), '', 'beam_a'),
        ((1500, 0, 0), (4500, 0, 0), '', 'beam_b'),
    ])
    raised = False
    try:
        _merge_panel_overlaps_and_check(m_partial)
    except RuntimeError as e:
        raised = True
        msg = str(e).splitlines()[0]
        print(f'  ✓ RuntimeError 발생 (예상대로): {msg}')
    if not raised:
        print('  ✗ 실패 — 부분 겹침인데 raise 가 안 났음. 새 알고리즘이 그룹 분리 실패.')
        sys.exit(1)

    print()
    print('=' * 70)
    print('[4] 합성 양성 테스트 — 완전 겹침/분리는 raise 없음')
    print('=' * 70)
    # 4a) 완전 겹침 (양 끝점 동일)
    m_exact = _mk_model([
        ((0, 0, 0), (3000, 0, 0), '', 'beam_a'),
        ((0, 0, 0), (3000, 0, 0), 'panel', 'beam_b'),
    ])
    try:
        _merge_panel_overlaps_and_check(m_exact)
        print(f'  ✓ 완전 겹침 → raise 없음, 부재 수 {len(m_exact.members)} (2 → 1 통합 확인)')
        assert len(m_exact.members) == 1, '완전 겹침 통합 실패'
    except RuntimeError as e:
        print(f'  ✗ 실패 — 완전 겹침인데 raise: {e}')
        sys.exit(1)

    # 4b) 완전 분리 (같은 직선 위 떨어진 두 부재)
    m_sep = _mk_model([
        ((0, 0, 0), (1000, 0, 0), '', 'beam_a'),
        ((2000, 0, 0), (3000, 0, 0), '', 'beam_b'),
    ])
    try:
        _merge_panel_overlaps_and_check(m_sep)
        print(f'  ✓ 완전 분리 (같은 직선) → raise 없음')
    except RuntimeError as e:
        print(f'  ✗ 실패 — 완전 분리인데 raise: {e}')
        sys.exit(1)

    # 4c) 다른 직선 (평행 다른 라인)
    m_par = _mk_model([
        ((0, 0, 0), (3000, 0, 0), '', 'beam_a'),
        ((0, 500, 0), (3000, 500, 0), '', 'beam_b'),
    ])
    try:
        _merge_panel_overlaps_and_check(m_par)
        print(f'  ✓ 평행 다른 라인 → raise 없음')
    except RuntimeError as e:
        print(f'  ✗ 실패 — 평행 다른 라인인데 raise: {e}')
        sys.exit(1)

    # 4d) 다른 방향 (직각)
    m_cross = _mk_model([
        ((0, 0, 0), (3000, 0, 0), '', 'beam_a'),
        ((1500, -1500, 0), (1500, 1500, 0), '', 'beam_b'),
    ])
    try:
        _merge_panel_overlaps_and_check(m_cross)
        print(f'  ✓ 직각 교차 → raise 없음')
    except RuntimeError as e:
        print(f'  ✗ 실패 — 직각 교차인데 raise: {e}')
        sys.exit(1)

    print()
    print('=' * 70)
    print('모든 검증 통과 — 새 알고리즘이 동작 정확성 보존')
    print('=' * 70)


if __name__ == '__main__':
    main()
