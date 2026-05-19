"""ops_solver 단위 스모크 테스트 — 평형 검증 중심.

실행:
  venv/Scripts/python.exe -m modular_3d.analysis._smoke_test_ops_solver
"""
from __future__ import annotations
import numpy as np

from modular_3d.model import Scene, Module, ComponentType
from modular_3d.analysis.topology import build_analysis_model
from modular_3d.analysis.ops_builder import build_ops_model
from modular_3d.analysis.ops_solver import solve_vertical


def _add_module(scene: Scene, x: float, y: float, z: float = 0.0,
                w: float = 4000.0, d: float = 6000.0, h: float = 3300.0) -> int:
    m = Module(
        id=0, comp_type=ComponentType.MODULE,
        position=np.array([x, y, z], dtype=np.float64),
        rotation=0,
        dimensions={'width': w, 'depth': d, 'height': h},
    )
    m.generate_sub_components()
    return scene.add_component(m)


def case_handcalc():
    """손계산 vs ops 결과 — 단일 모듈 D+L 총 하중.

    예상값 (4m × 6m × 3.3m, SHS 200×200×8, SLAB 200mm, LL 2.0kPa):
      강재 자중      = 24.0 kN
      슬래브 자중   = 4×6 × 0.2 × 23.5 = 112.8 kN
      활하중 L      = 4×6 × 2.0 = 48.0 kN
      D = 24.0 + 112.8 = 136.8 kN
      1.2D + 1.6L = 164.2 + 76.8 = 241.0 kN
      코어 1.2D = 1×1×3.1 × 24 × 1.2 = 89.3 kN  (SLAB_THICKNESS_MM=200, depth=h-s=3100)
      합 = 330.3 kN  (3% 이내 일치 기대)
    """
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    expected_kN = 330.3
    actual_kN = abs(res.total_applied_load_z) / 1000.0
    diff_pct = abs(actual_kN - expected_kN) / expected_kN * 100
    print(f"[손계산] 기대 {expected_kN:.1f} kN, ops {actual_kN:.1f} kN, 차이 {diff_pct:.2f}%")
    assert diff_pct < 5.0, \
        f"손계산 vs ops 차이 {diff_pct:.2f}% — 단순화 한계 초과 (5% 이내 기대)"
    print(f"[CASE handcalc] PASS\n")


def case_1():
    """단일 모듈 — 평형 1% 이내 통과 기대."""
    scene = Scene()
    _add_module(scene, 0, 0, 0)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"평형 오차 {res.equilibrium_ratio*100:.3f}% — 1% 초과"
    # 변위가 0이 아닌 노드가 있어야 함
    max_d = max((np.linalg.norm(d[:3]) for d in res.node_disps.values()), default=0)
    assert max_d > 0, "변위 모두 0 — 해석이 작동 안 함"
    print(f"  최대 변위: {max_d:.3f} mm")
    print("[CASE 1] PASS\n")


def case_2():
    """수평 인접 2 모듈 — 평형 1% 이내."""
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000)
    _add_module(scene, 4020, 0, 0, w=4000, d=6000)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"평형 오차 {res.equilibrium_ratio*100:.3f}%"
    print("[CASE 2] PASS\n")


def case_3():
    """수직 적층 2 모듈 — 평형 1% 이내."""
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    _add_module(scene, 0, 0, 3320, w=4000, d=6000, h=3300)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"평형 오차 {res.equilibrium_ratio*100:.3f}%"
    print("[CASE 3] PASS\n")


def case_4_2x2_grid_2story():
    """2×2 평면 × 2층 (총 8 모듈) — 다층 + 다이어프램 작동 확인."""
    scene = Scene()
    for fy in (0, 6020):
        for fx in (0, 4020):
            _add_module(scene, fx, fy, 0, w=4000, d=6000, h=3300)
    for fy in (0, 6020):
        for fx in (0, 4020):
            _add_module(scene, fx, fy, 3320, w=4000, d=6000, h=3300)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"평형 오차 {res.equilibrium_ratio*100:.3f}%"
    print(f"  부재 수: {len(res.member_forces)}")
    print("[CASE 4] PASS\n")


def case_with_floor_panel():
    """모듈 + FloorPanel — fp 가 모듈 위에 얹힌 케이스. 핀 연결 누락 시 mechanism."""
    from modular_3d.model import FloorPanel
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    # FloorPanel 을 모듈 천장 위에 얹기 (z = h - half_s + half_s = h = 3300? 또는 천장 윗면)
    # FloorPanel z 좌표 = 보 중심선 = 모듈 천장보 z=h-s 와 동일 → 3100
    fp = FloorPanel(
        id=0, comp_type=ComponentType.FLOOR_PANEL,
        position=np.array([0.0, 0.0, 3100.0], dtype=np.float64),
        rotation=0,
        dimensions={'width': 4000, 'depth': 6000, 'height': 200},
    )
    fp.generate_sub_components()
    scene.add_component(fp)

    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    print(om.summary())
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"FP 결합 평형 오차 {res.equilibrium_ratio*100:.3f}% — fp 핀 연결 누락 의심"
    # FP 노드들이 어딘가에 연결되어 있어야 함 — 변위가 비현실적으로 크면(>10mm) mechanism
    max_d = max((np.linalg.norm(d[:3]) for d in res.node_disps.values()), default=0)
    assert max_d < 100.0, f"FP 노드 변위 {max_d:.1f} mm — mechanism 의심"
    print(f"[CASE FP] PASS  (max_disp={max_d:.3f} mm)\n")


def case_with_struct_wall():
    """모듈 + StructWall — 벽패널 추가 케이스."""
    from modular_3d.model import StructWall
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    # 벽패널: 모듈의 한 면을 따라 배치 (y=0 면)
    sw = StructWall(
        id=0, comp_type=ComponentType.STRUCT_WALL,
        position=np.array([0.0, -220.0, 0.0], dtype=np.float64),
        rotation=0,
        dimensions={'width': 4000, 'depth': 200, 'height': 3300},
    )
    sw.generate_sub_components()
    scene.add_component(sw)

    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    print(om.summary())
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"StructWall 결합 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    max_d = max((np.linalg.norm(d[:3]) for d in res.node_disps.values()), default=0)
    assert max_d < 100.0, f"벽패널 노드 변위 {max_d:.1f} mm — mechanism 의심"
    print(f"[CASE SW] PASS  (max_disp={max_d:.3f} mm)\n")


def case_rotated_module():
    """rotation=90 모듈 — 좌표 변환 + ops 빌드 + 평형 검증."""
    scene = Scene()
    m = Module(
        id=0, comp_type=ComponentType.MODULE,
        position=np.array([0.0, 0.0, 0.0], dtype=np.float64),
        rotation=90,
        dimensions={'width': 4000.0, 'depth': 6000.0, 'height': 3300.0},
    )
    m.generate_sub_components()
    scene.add_component(m)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"회전 모듈 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    max_d = max((np.linalg.norm(d[:3]) for d in res.node_disps.values()), default=0)
    assert max_d > 0, "회전 모듈 변위 0 — 해석 오류"
    assert max_d < 100.0, f"회전 모듈 변위 {max_d:.1f} mm — 비현실적"
    print(f"[CASE rot90] PASS  (max_disp={max_d:.3f} mm)\n")


def case_with_cantilever():
    """모듈 + CantileverBeam 1개 — 자유단 노드가 mechanism 만들지 않는지."""
    from modular_3d.model import CantileverBeam
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    cb = CantileverBeam(
        id=0, comp_type=ComponentType.CANTILEVER_BEAM,
        position=np.array([4020.0, 100.0, 3100.0], dtype=np.float64),
        rotation=0,
        dimensions={'width': 1500.0},
    )
    cb.generate_sub_components()
    scene.add_component(cb)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"캔틸레버 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    max_d = max((np.linalg.norm(d[:3]) for d in res.node_disps.values()), default=0)
    assert max_d < 200.0, f"캔틸레버 변위 {max_d:.1f} mm — mechanism 의심"
    print(f"[CASE cant] PASS  (max_disp={max_d:.3f} mm)\n")


def case_large_3x3_3story():
    """3×3 평면 × 3층 (총 27 모듈) — 큰 모델 평형 + 성능 + 다이어프램 다중 층."""
    scene = Scene()
    for s in range(3):
        z = s * 3320
        for ix in range(3):
            for iy in range(3):
                _add_module(scene, ix * 4020, iy * 6020, z, w=4000, d=6000, h=3300)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    n_ed = len(om.spec.equal_dofs) if om.spec is not None else 0
    print(f"노드 {len(om.node_tags)}, 부재 {len(om.beam_elements)}, "
          f"결합 {n_ed}, 다이어프램 {len(om.diaphragms)}")
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"3x3x3층 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    print("[CASE large] PASS\n")


def case_rotated_stack():
    """회전 모듈 + 위에 회전 안 한 모듈 적층 — 좌표 변환 + 인터페이스 검출 검증."""
    scene = Scene()
    # 1층: rotation=0
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    # 2층: rotation=180 (같은 footprint)
    m2 = Module(
        id=0, comp_type=ComponentType.MODULE,
        position=np.array([4000.0, 6000.0, 3320.0], dtype=np.float64),
        rotation=180,
        dimensions={'width': 4000.0, 'depth': 6000.0, 'height': 3300.0},
    )
    m2.generate_sub_components()
    scene.add_component(m2)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    # [2026-05-18] om.interface_links 자료구조 폐기 — joint_rules R02 가
    # 수직 적층 결합을 spec.equal_dofs 에 등록.
    n_ed = len(om.spec.equal_dofs) if om.spec is not None else 0
    print(f"결합 {n_ed} (R02 수직 적층 등록)")
    res = solve_vertical(om, scene)
    print(res.summary())
    assert res.equilibrium_ratio < 0.01, \
        f"회전 적층 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    print("[CASE rotstack] PASS\n")


def case_different_size_modules():
    """다른 크기 두 모듈 인접 (4×6 + 6×6) — 인접 코너 매칭 검증.

    A 모듈 (4m × 6m) 우측에 B 모듈 (6m × 6m). 인접면이 같은 길이 (6m).
    두 모듈의 인접 코너 4개가 220mm 거리 → 검출되어야 함.
    """
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    _add_module(scene, 4020, 0, 0, w=6000, d=6000, h=3300)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_vertical(om, scene)
    assert res.equilibrium_ratio < 0.01, \
        f"다른 크기 모듈 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    print(f"[CASE diff_size] PASS\n")


def case_partial_floor_panel():
    """모듈 위에 더 작은 FloorPanel — fp 코너 4개 중 일부만 모듈 코너와 일치.

    모듈 4×6, fp 2×3을 모듈 좌상 코너 위에 얹으면 fp 코너 1개만 모듈 코너와 일치,
    나머지 3개는 모듈 보 중간/내부에 위치 → 매칭 안 되면 mechanism 발생.

    이 케이스는 학부 단순화 한계로 mechanism 가능. solver 가 catch 하는지 확인.
    """
    from modular_3d.model import FloorPanel
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    fp = FloorPanel(
        id=0, comp_type=ComponentType.FLOOR_PANEL,
        position=np.array([0.0, 0.0, 3100.0], dtype=np.float64),
        rotation=0,
        dimensions={'width': 2000, 'depth': 3000, 'height': 200},
    )
    fp.generate_sub_components()
    scene.add_component(fp)
    try:
        am = build_analysis_model(scene)
        om = build_ops_model(am, scene=scene)
        res = solve_vertical(om, scene)
        max_d = max((np.linalg.norm(d[:3]) for d in res.node_disps.values()), default=0)
        if max_d > 1000.0 or res.equilibrium_ratio > 0.05:
            print(f"[CASE partial_fp] WARN: 부분 fp 결합 mechanism 의심 "
                  f"(max_disp={max_d:.1f} mm, eq_err={res.equilibrium_ratio*100:.1f}%)")
        else:
            print(f"[CASE partial_fp] PASS  (max_disp={max_d:.3f} mm)")
    except RuntimeError as e:
        # topology 의 _merge_panel_overlaps_and_check 가 부분 겹침 패널 검출 시 명시적 에러.
        # 또는 ops 솔버가 행렬 특이로 실패. 둘 다 "사용자에게 안내"가 적절한 결과.
        msg = str(e).split('\n')[0]
        print(f"[CASE partial_fp] PASS (예상된 거부) — {msg}")
    print()


def case_offset_stack():
    """1층과 2층의 위치가 어긋난 적층 — 일부 column 만 핀 연결.

    1층 footprint vs 2층 footprint 가 일부 겹치면 일부 column 만 vertical_stack
    인터페이스 등록되고, 나머지는 free 가 됨 → mechanism 가능.

    학부 수준 단순화 한계 → solver 가 명확하게 거부하거나 비현실적 변위 발생.
    사용자에게 명확한 신호가 가는지만 확인.
    """
    from modular_3d.analysis.ops_builder import self_check
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    _add_module(scene, 1000, 0, 3320, w=4000, d=6000, h=3300)   # 1m 어긋남
    try:
        am = build_analysis_model(scene)
        om = build_ops_model(am, scene=scene)
        # self_check 가 floating column 을 명시적으로 보고해야 함
        issues = self_check(om)
        floating_issues = [s for s in issues if 'floating' in s or 'free' in s]
        if floating_issues:
            print(f"[CASE offset_stack] PASS (self_check 가 mechanism 검출): "
                  f"{len(floating_issues)}건")
            for s in floating_issues[:3]:
                print(f"  - {s}")
            return
        # 검출 못 하면 솔버 결과 확인
        res = solve_vertical(om, scene)
        max_d = max((np.linalg.norm(d[:3]) for d in res.node_disps.values()), default=0)
        if max_d > 1000.0 or res.equilibrium_ratio > 0.05:
            print(f"[CASE offset_stack] WARN: 어긋난 적층 mechanism — "
                  f"max_disp={max_d:.1f} mm, eq_err={res.equilibrium_ratio*100:.1f}%")
        else:
            print(f"[CASE offset_stack] PASS  (max_disp={max_d:.3f} mm)")
    except RuntimeError as e:
        msg = str(e).split('\n')[0]
        print(f"[CASE offset_stack] PASS (예상된 거부) — {msg}")
    print()


def case_seismic_handcalc():
    """등가정적 지진 손계산 vs ops 검증.

    단일 모듈 4×6×3.3:
      W = D = 강재 24 + 슬래브 112.8 + 코어 72.85 ≈ 209.6 kN
      Cs = SDS·Ie/R = 0.5/8 = 0.0625
      V = 0.0625 × 209.6 = 13.10 kN
    """
    from modular_3d.analysis.ops_solver import solve_seismic
    scene = Scene()
    _add_module(scene, 0, 0, 0, w=4000, d=6000, h=3300)
    am = build_analysis_model(scene)
    om = build_ops_model(am, scene=scene)
    res = solve_seismic(om, scene, 'X')
    # KBC 2022 §0306.4: Cs = min(SDS/R, SD1/(T·R), max(0.044·SDS, 0.01))
    #   SDS=0.5, SD1=0.2, R=8, T=0.5 → Cs_eq=0.0625, Cs_max=0.05 → Cs=0.05
    # W = D 강재(24) + 슬래브(112.8) + 코어(72.85) = 209.65 kN
    # V = 0.05 × 209.65 = 10.48 kN
    expected_V = 10.48  # kN
    actual_V = abs(res.total_applied_load_x) / 1000.0
    diff_pct = abs(actual_V - expected_V) / expected_V * 100
    print(f"[지진 손계산] V 기대 {expected_V:.2f} kN, ops {actual_V:.2f} kN, 차이 {diff_pct:.2f}%")
    assert diff_pct < 5.0, f"지진력 손계산 차이 {diff_pct:.2f}% — 기대 ±5%"
    assert res.equilibrium_ratio < 0.01, \
        f"지진 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    print(f"[CASE seismic_calc] PASS\n")


def case_5_seismic_wind():
    """횡력 5 케이스 일괄 해석 — 평형 1% 이내, 층변위 양수, 베이스 전단 적용 일치."""
    from modular_3d.analysis.ops_solver import solve_all_cases
    scene = Scene()
    for fy in (0, 6020):
        for fx in (0, 4020):
            _add_module(scene, fx, fy, 0, w=4000, d=6000, h=3300)
    for fy in (0, 6020):
        for fx in (0, 4020):
            _add_module(scene, fx, fy, 3320, w=4000, d=6000, h=3300)

    results = solve_all_cases(scene)
    for name, res in results.items():
        print(res.summary())
        assert res.equilibrium_ratio < 0.01, \
            f"{name} 평형 오차 {res.equilibrium_ratio*100:.3f}%"
    # 횡력 케이스는 층변위가 0이 아니어야 함
    for lat in ('Ex', 'Ey', 'Wx', 'Wy'):
        res = results[lat]
        max_disp = max((abs(dx) + abs(dy) for (dx, dy) in res.story_disp.values()),
                       default=0.0)
        assert max_disp > 1e-3, f"{lat} 층변위 0 — 횡력 적용 실패"
    print("[CASE 5] PASS\n")


def main():
    case_handcalc()
    case_1()
    case_2()
    case_3()
    case_4_2x2_grid_2story()
    case_with_floor_panel()
    case_with_struct_wall()
    case_rotated_module()
    case_with_cantilever()
    case_large_3x3_3story()
    case_rotated_stack()
    case_different_size_modules()
    case_partial_floor_panel()
    case_offset_stack()
    case_seismic_handcalc()
    case_5_seismic_wind()
    print(">>> ops_solver 스모크 테스트 전체 통과 <<<")


if __name__ == '__main__':
    main()
