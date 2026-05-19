"""회귀 스모크 테스트 — 베이스라인 생성/비교.

사용법:
  베이스라인 저장: python regression.py --capture
  현재 결과 비교 : python regression.py --compare

대상 시나리오: scene3 (실 데이터) + synth_b (단층 다양) + synth_c (수직3층 포함)

수집 메트릭 (단계별):
  1. load     : 컴포넌트 수, n_floors
  2. topology : 노드/부재/인터페이스 링크/캔틸 anchor/벽패널 결합 수
  3. ops_build: fixed 노드/다이어프램 수
  4. solve    : D+L, Ex, Ey, Wx, Wy 5케이스 적용 하중·반력·평형오차·최대 변위·최대 모멘트

비교 허용 오차: 상대 0.1% + 절대 1mm / 1kN / 1kNm (작은 값에서 부동소수 흔들림 허용).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from modular_3d.io.scene_io import load_scene  # noqa: E402
from modular_3d.analysis.topology import build_analysis_model  # noqa: E402
from modular_3d.analysis.ops_builder import build_ops_model  # noqa: E402
from modular_3d.analysis.ops_solver import solve_all_cases  # noqa: E402

BASELINE_DIR = Path(__file__).parent / 'regression_baselines'
SCENE_DIR = Path(__file__).parent / 'regression_scenes'

# 시나리오 목록 (이름, 경로)
SCENARIOS = [
    ('scene3', Path(r'C:\Users\이건영\Desktop\종설\scene3.json')),
    ('synth_b', SCENE_DIR / 'synth_b.json'),
    ('synth_c', SCENE_DIR / 'synth_c.json'),
]

# 비교 허용 오차
REL_TOL = 1e-3      # 0.1 % 상대
ABS_TOL_MM = 1.0    # 1 mm
ABS_TOL_KN = 1.0    # 1 kN (= 1000 N)
ABS_TOL_KNM = 1.0   # 1 kN·m


def _capture_one(scene_path: Path) -> Dict[str, Any]:
    """한 시나리오의 메트릭을 dict 로 반환."""
    out: Dict[str, Any] = {'path': str(scene_path)}

    # 1. Load
    scene, n_floors = load_scene(str(scene_path))
    out['load'] = {
        'n_components': len(scene.components),
        'n_floors': int(n_floors),
    }

    # 2. Topology
    am = build_analysis_model(scene)
    out['topology'] = {
        'n_nodes': len(am.nodes),
        'n_members': len(am.members),
        'n_cantilever_anchor_node_ids': len(getattr(am, 'cantilever_anchor_node_ids', [])),
    }

    # 3. Ops build
    om = build_ops_model(am, scene=scene)
    out['ops'] = {
        'n_fixed_nodes': len(om.fixed_nodes),
        'n_diaphragms': len(om.diaphragms),
        'n_beam_elements': len(getattr(om, 'beam_elements', {})),
    }

    # 4. Solve
    cases: Dict[str, Any] = {}
    try:
        results = solve_all_cases(scene)
    except Exception as e:
        out['solve_error'] = f'{type(e).__name__}: {e}'
        traceback.print_exc()
        return out

    for name, res in results.items():
        # 부재 모멘트 최대 (kN·m)
        m_max = 0.0
        for mf in res.member_forces.values():
            m_max = max(m_max, mf.M_max_abs)
        # 변위 최대 (mm)
        d_max = 0.0
        for v in res.node_disps.values():
            arr = np.asarray(v, dtype=float).flatten()
            if arr.size >= 3:
                d_max = max(d_max, float(np.linalg.norm(arr[:3])))

        cases[name] = {
            'applied_load': {
                'x_kN': res.total_applied_load_x / 1000,
                'y_kN': res.total_applied_load_y / 1000,
                'z_kN': res.total_applied_load_z / 1000,
            },
            'base_reaction': {
                'x_kN': res.total_base_reaction_x / 1000,
                'y_kN': res.total_base_reaction_y / 1000,
                'z_kN': res.total_base_reaction_z / 1000,
            },
            'equilibrium_ratio': float(res.equilibrium_ratio),
            'max_disp_mm': float(d_max),
            'max_moment_kNm': float(m_max / 1e6),  # N·mm → kN·m
            'n_member_forces': len(res.member_forces),
        }
    out['solve'] = cases
    return out


def capture_all() -> None:
    BASELINE_DIR.mkdir(exist_ok=True)
    summary = {}
    for name, path in SCENARIOS:
        if not path.exists():
            print(f'[skip] {name}: {path} 없음')
            continue
        print(f'\n=== {name} ({path.name}) ===')
        try:
            data = _capture_one(path)
        except Exception as e:
            print(f'[fail] {name}: {type(e).__name__}: {e}')
            traceback.print_exc()
            continue
        out_path = BASELINE_DIR / f'baseline_{name}.json'
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                            encoding='utf-8')
        # 요약
        if 'load' in data:
            summary[name] = {
                'comps': data['load']['n_components'],
                'nodes': data['topology']['n_nodes'],
                'members': data['topology']['n_members'],
            }
            if 'solve' in data and 'D+L' in data['solve']:
                eq = data['solve']['D+L']['equilibrium_ratio'] * 100
                summary[name]['DL_eq_err_%'] = round(eq, 3)
        print(f'[ok] {out_path.name}')
    print('\n=== 요약 ===')
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _close(a: float, b: float, tol_abs: float) -> bool:
    """상대 0.1% 또는 절대 tol_abs 이내면 일치."""
    diff = abs(a - b)
    if diff <= tol_abs:
        return True
    base = max(abs(a), abs(b))
    if base < 1e-9:
        return diff <= tol_abs
    return (diff / base) <= REL_TOL


def _diff_keys(name: str, base: Any, curr: Any, fails: list,
               tol_abs: float = 0.0):
    """재귀적으로 dict/list 비교, 숫자는 _close 로, 기타는 ==."""
    if isinstance(base, dict) and isinstance(curr, dict):
        keys = sorted(set(base) | set(curr))
        for k in keys:
            if k == 'path':
                continue  # 경로 비교 제외
            sub_tol = tol_abs
            if k.endswith('_kN'):
                sub_tol = ABS_TOL_KN
            elif k.endswith('_kNm') or k.endswith('_moment_kNm'):
                sub_tol = ABS_TOL_KNM
            elif k.endswith('_mm'):
                sub_tol = ABS_TOL_MM
            _diff_keys(f'{name}.{k}', base.get(k), curr.get(k), fails, sub_tol)
        return
    if isinstance(base, (int, float)) and isinstance(curr, (int, float)):
        if not _close(float(base), float(curr), tol_abs):
            fails.append((name, base, curr))
        return
    if base != curr:
        fails.append((name, base, curr))


def compare_all() -> int:
    if not BASELINE_DIR.exists():
        print('[err] 베이스라인 폴더 없음. 먼저 --capture 실행.')
        return 2
    n_fail = 0
    for name, path in SCENARIOS:
        baseline_path = BASELINE_DIR / f'baseline_{name}.json'
        if not baseline_path.exists():
            print(f'[skip] {name}: 베이스라인 없음 ({baseline_path.name})')
            continue
        if not path.exists():
            print(f'[skip] {name}: 시나리오 없음')
            continue
        print(f'\n=== {name} ===')
        try:
            curr = _capture_one(path)
        except Exception as e:
            print(f'[fail] {name}: 실행 실패: {type(e).__name__}: {e}')
            n_fail += 1
            continue
        base = json.loads(baseline_path.read_text(encoding='utf-8'))
        fails: list = []
        _diff_keys(name, base, curr, fails)
        if fails:
            print(f'[FAIL] {len(fails)} 개 차이:')
            for path_str, b, c in fails[:30]:
                print(f'  {path_str}: base={b!r}  curr={c!r}')
            n_fail += 1
        else:
            print('[OK] 모든 메트릭 일치')
    print(f'\n=== 회귀 결과: {len(SCENARIOS) - n_fail} pass / {n_fail} fail ===')
    return 0 if n_fail == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--capture', action='store_true',
                   help='현재 결과를 베이스라인으로 저장')
    g.add_argument('--compare', action='store_true',
                   help='베이스라인과 현재 결과 비교')
    args = ap.parse_args()
    if args.capture:
        capture_all()
        return 0
    return compare_all()


if __name__ == '__main__':
    sys.exit(main())
