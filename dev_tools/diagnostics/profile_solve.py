"""scene.json 1 회 풀이의 단계별 소요시간 측정.

목적: 어디가 진짜 병목인지 숫자로 확인.
측정 대상:
  1) load_scene
  2) build_analysis_model (am)
  3) build_ops_model × 5 (case 별)
     - 그 안에서 apply_all_joint_rules → R01~R09 각 룰
     - _build_diaphragms_and_core
     - 나머지 (노드/요소 등록 + ops 도메인 등)
  4) solve_* × 5

[정책]
- 시간 측정 외 사이드이펙트 없음 (D+L 평형 검증은 별개).
- dprint 잡음을 줄이기 위해 MODULAR_DEBUG_OFF=1 자동 설정.
- 단발성 진단. 결과 보고 → 결정 → 폐기 or 유지.
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from typing import Dict, List

# venv 자체 재실행 가드.
_HERE = os.path.dirname(os.path.abspath(__file__))
_MYPROJ = os.path.dirname(os.path.dirname(_HERE))         # my_project/
_VENV_PY = os.path.join(_MYPROJ, 'venv', 'Scripts', 'python.exe')
if os.path.isfile(_VENV_PY) and os.path.abspath(sys.executable) != os.path.abspath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, __file__] + sys.argv[1:])

if _MYPROJ not in sys.path:
    sys.path.insert(0, _MYPROJ)

# 디버그 출력 차단 — 시간 측정 잡음 제거.
os.environ['MODULAR_DEBUG_OFF'] = '1'

SCENE_PATH = r'C:\Users\이건영\Desktop\종설\scene.json'

# ─────────────────────────────────────────────────────────
# 단계별 timer
# ─────────────────────────────────────────────────────────
_phase_times: Dict[str, List[float]] = defaultdict(list)


class T:
    """with T('label'): 블록 시간을 _phase_times[label] 에 누적."""
    def __init__(self, label: str):
        self.label = label
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self
    def __exit__(self, *a):
        dt = time.perf_counter() - self.t0
        _phase_times[self.label].append(dt)


def _patch_rule_timers():
    """joint_rules 의 R01~R09 진입함수를 래핑해서 각각 시간 누적."""
    from modular_3d.analysis import joint_rules as jr

    targets = [
        ('R01_module_module_horizontal',  'apply_module_module_horizontal'),
        ('R02_module_module_vertical',    'apply_module_module_vertical'),
        ('R03_panel_module',              'apply_panel_module'),
        ('R04_panel_panel_horizontal',    'apply_panel_panel_horizontal'),
        ('R05_wall_floor_vertical',       'apply_wall_floor_vertical'),
        ('R06_wall_module',               'apply_wall_module'),
        ('R07_wall_wall_horizontal',      'apply_wall_wall_horizontal'),
        ('R08_wall_cantilever_beam',      'apply_wall_cantilever_beam'),
        ('R09_core_joint',                'apply_core_joint'),
        ('Rsplit_edges_and_link_corners', '_split_edges_and_link_corners'),
    ]
    for label, fn_name in targets:
        if not hasattr(jr, fn_name):
            continue
        orig = getattr(jr, fn_name)
        def make_wrapper(label=label, orig=orig):
            def wrapper(*a, **kw):
                t0 = time.perf_counter()
                try:
                    return orig(*a, **kw)
                finally:
                    _phase_times[label].append(time.perf_counter() - t0)
            wrapper.__name__ = orig.__name__
            return wrapper
        setattr(jr, fn_name, make_wrapper())


def _patch_builder_timers():
    """build_ops_model 내부 주요 _step_* 도 래핑."""
    from modular_3d.analysis import ops_builder as ob

    targets = [
        ('build_step_register_nodes',         '_step_register_nodes'),
        ('build_step_register_members',       '_step_register_members'),
        ('build_step_fix_base_nodes',         '_step_fix_base_nodes'),
        ('build_diaphragms_and_core',         '_build_diaphragms_and_core'),
        ('build_apply_all_joint_rules_total', None),  # 별도 처리
    ]
    for label, fn_name in targets:
        if fn_name is None:
            continue
        if not hasattr(ob, fn_name):
            continue
        orig = getattr(ob, fn_name)
        def make_wrapper(label=label, orig=orig):
            def wrapper(*a, **kw):
                t0 = time.perf_counter()
                try:
                    return orig(*a, **kw)
                finally:
                    _phase_times[label].append(time.perf_counter() - t0)
            wrapper.__name__ = orig.__name__
            return wrapper
        setattr(ob, fn_name, make_wrapper())


def _patch_topmost_timers():
    """build_ops_model · solve_vertical · solve_seismic · solve_wind 도 래핑."""
    from modular_3d.analysis import ops_builder as ob
    from modular_3d.analysis import ops_solver as os_mod

    pairs = [
        (ob, 'build_ops_model',  'TOP_build_ops_model'),
        (os_mod, 'solve_vertical', 'TOP_solve_vertical'),
        (os_mod, 'solve_seismic',  'TOP_solve_seismic'),
        (os_mod, 'solve_wind',     'TOP_solve_wind'),
        (os_mod, 'calculate_loads','TOP_calculate_loads'),
    ]
    for mod, fn_name, label in pairs:
        if not hasattr(mod, fn_name):
            continue
        orig = getattr(mod, fn_name)
        def make_wrapper(label=label, orig=orig):
            def wrapper(*a, **kw):
                t0 = time.perf_counter()
                try:
                    return orig(*a, **kw)
                finally:
                    _phase_times[label].append(time.perf_counter() - t0)
            wrapper.__name__ = orig.__name__
            return wrapper
        setattr(mod, fn_name, make_wrapper())
    # solve_all_cases 가 자체 모듈 내부에서 직접 `from ... import build_ops_model`
    # 으로 다시 가져오는 패턴이 있을 수 있어, 모듈 globals 도 덮어쓰기.
    if hasattr(os_mod, 'build_ops_model'):
        os_mod.build_ops_model = ob.build_ops_model


def _patch_topology_timers():
    """build_analysis_model 내부 4 단계 + _extract_* 누적 시간."""
    from modular_3d.analysis import topology as tp

    # 후속 단계 4 개 — 통째로 1회씩 호출됨.
    phase_fns = [
        ('TP_consolidate_dependent_nodes',  '_consolidate_dependent_nodes'),
        ('TP_split_hosts_for_mid_endpoints','_split_hosts_for_mid_endpoints'),
        ('TP_merge_panel_overlaps',         '_merge_panel_overlaps_and_check'),
        ('TP_merge_duplicate_members',      '_merge_duplicate_members'),
        ('TP_build_diaphragms',             '_build_diaphragms'),
    ]
    for label, fn_name in phase_fns:
        if not hasattr(tp, fn_name):
            continue
        orig = getattr(tp, fn_name)
        def make_wrapper(label=label, orig=orig):
            def wrapper(*a, **kw):
                t0 = time.perf_counter()
                try:
                    return orig(*a, **kw)
                finally:
                    _phase_times[label].append(time.perf_counter() - t0)
            wrapper.__name__ = orig.__name__
            return wrapper
        setattr(tp, fn_name, make_wrapper())

    # _extract_* 컴포넌트별 추출기 — 컴포넌트 타입별 누적시간 + 호출수.
    extract_fns = [
        '_extract_module',
        '_extract_floor_panel',
        '_extract_struct_wall',
        '_extract_cantilever_beam',
        '_extract_cantilever_slab',
        '_extract_mid_beam',
        '_extract_mid_column',
        '_extract_vertical_module',
        '_extract_core',
        '_extract_core_slab',
    ]
    # _EXTRACTORS dict 는 모듈 로드 시점에 원본 함수 참조로 채워져 있으므로
    # 모듈 attr 만 덮어도 _extract_one 이 옛 참조를 그대로 호출 → dict 도 갱신.
    for fn_name in extract_fns:
        if not hasattr(tp, fn_name):
            continue
        label = 'EX_' + fn_name.replace('_extract_', '')
        orig = getattr(tp, fn_name)
        def make_wrapper(label=label, orig=orig):
            def wrapper(*a, **kw):
                t0 = time.perf_counter()
                try:
                    return orig(*a, **kw)
                finally:
                    _phase_times[label].append(time.perf_counter() - t0)
            wrapper.__name__ = orig.__name__
            return wrapper
        wrapped = make_wrapper()
        setattr(tp, fn_name, wrapped)
        # _EXTRACTORS dict 안의 원본 참조도 동일 wrapper 로 교체.
        if hasattr(tp, '_EXTRACTORS'):
            for k, v in list(tp._EXTRACTORS.items()):
                if v is orig:
                    tp._EXTRACTORS[k] = wrapped


def _print_report():
    print('\n' + '=' * 70)
    print('단계별 소요시간 보고')
    print('=' * 70)
    rows = []
    for label, times in _phase_times.items():
        total = sum(times)
        n = len(times)
        avg = total / n if n else 0.0
        rows.append((total, n, avg, label))
    rows.sort(reverse=True)  # 총시간 내림차순
    print(f'{"총시간(s)":>10}  {"호출수":>5}  {"평균(s)":>9}  라벨')
    print('-' * 70)
    for total, n, avg, label in rows:
        print(f'{total:>10.3f}  {n:>5d}  {avg:>9.4f}  {label}')
    print('=' * 70)


def main():
    print(f'[1/4] scene 로드: {SCENE_PATH}')
    from modular_3d.io.scene_io import load_scene
    with T('load_scene'):
        scene, n_floors = load_scene(SCENE_PATH)
    print(f'  컴포넌트 수: {len(scene.components)}')

    # 측정용 monkey-patch 는 import 직후, 사용 전에.
    _patch_rule_timers()
    _patch_builder_timers()
    _patch_topmost_timers()
    _patch_topology_timers()

    print('[2/4] build_analysis_model')
    from modular_3d.analysis.topology import build_analysis_model
    with T('build_analysis_model'):
        am = build_analysis_model(scene)
    print(f'  노드 수: {len(am.nodes)}, 부재 수: {len(am.members)}')

    print('[3/4] solve_all_cases (build_ops_model × 5 + solve × 5 내부 포함)')
    from modular_3d.analysis.ops_solver import solve_all_cases
    with T('solve_all_cases_total'):
        results = solve_all_cases(scene, prebuilt_am=am)
    print(f'  케이스 수: {len(results)} → {list(results.keys())}')

    # build_ops_model 자체 시간 = solve_all_cases_total - (solve_* 시간)
    # solve_* 는 ops_solver 내부 함수 — 추가 패치 없이는 분리 안 됨.
    # 대신 solve_all_cases 본체에서 build_ops_model 호출 횟수=5 라서
    # 룰 호출 횟수(각 R01~R09 가 5번) 합으로 역추산 가능.

    _print_report()


if __name__ == '__main__':
    main()
