"""구조해석·단면설계 단계별 시간 측정(프로파일) — 병목 위치 근거 확보용.

[목적]
빌드 / 솔브(행렬분해) / 하중계산 / 단면산정·수렴이 각각 얼마나 걸리는지 실측해,
어디를 더 줄여야 효과가 큰지 근거를 만든다. (성능 재구조화 후 상태 측정)

[사용]
  python -m modular_3d.analysis.tests.perf_probe --scene "...scene.json"

측정 결과는 [PERF] 접두어로 출력한다(디버그 로그와 구분).
"""
from __future__ import annotations

import argparse
import sys
from time import perf_counter


def _t(fn):
    """fn() 실행 시간(초)과 반환값."""
    t0 = perf_counter()
    r = fn()
    return perf_counter() - t0, r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    args = ap.parse_args()

    from modular_3d.io.scene_io import load_scene
    from modular_3d.analysis.topology import build_analysis_model
    from modular_3d.analysis.ops_builder import build_ops_model
    from modular_3d.analysis.load_calculator import calculate_loads
    from modular_3d.analysis.ops_solver import (
        solve_vertical, solve_seismic, solve_wind, solve_all_cases,
        _reset_domain_between_cases,
    )
    from modular_3d.analysis.section_converge import (
        converge_sections, ConvergeOptions,
    )

    # 0) 로드
    dt_load, (scene, n_floors) = _t(lambda: load_scene(args.scene))

    # 0b) 접합부 설계(접합 기록) — 접합부 설계 탭의 자동계산에 해당
    from modular_3d.model.joint_recorder import record_joints
    dt_joint, n_joint = _t(lambda: record_joints(scene))

    # 1) 토폴로지(해석모델) 빌드
    dt_topo, am = _t(lambda: build_analysis_model(scene))

    # 2) ops 모델 빌드 — 워밍업 1회 후 3회 평균
    build_ops_model(am, scene=scene)
    builds = []
    for _ in range(3):
        dt, _om = _t(lambda: build_ops_model(am, scene=scene))
        builds.append(dt)
    dt_build = sum(builds) / len(builds)

    # 3) 하중 계산
    dt_loads, lr = _t(lambda: calculate_loads(scene, am))

    # 4) 솔브 단독(빌드 제외) — 깨끗한 도메인에서 6 성분, 케이스 사이 리셋
    om = build_ops_model(am, scene=scene)
    dt_D, _ = _t(lambda: solve_vertical(om, scene, load_result=lr,
                                        dl_factor=1.0, ll_factor=0.0, case_name='D'))
    _reset_domain_between_cases()
    dt_L, _ = _t(lambda: solve_vertical(om, scene, load_result=lr,
                                        dl_factor=0.0, ll_factor=1.0, case_name='L'))
    _reset_domain_between_cases()
    dt_Ex, _ = _t(lambda: solve_seismic(om, scene, 'X', load_result=lr))
    _reset_domain_between_cases()
    dt_Ey, _ = _t(lambda: solve_seismic(om, scene, 'Y', load_result=lr))
    _reset_domain_between_cases()
    dt_Wx, _ = _t(lambda: solve_wind(om, scene, 'X'))
    _reset_domain_between_cases()
    dt_Wy, _ = _t(lambda: solve_wind(om, scene, 'Y'))
    dt_solve6 = dt_D + dt_L + dt_Ex + dt_Ey + dt_Wx + dt_Wy

    # 5) 구조해석 1회 전체(현재 구조: 빌드1 + 솔브6 + 하중1 + 추출)
    dt_all, _ = _t(lambda: solve_all_cases(scene, prebuilt_am=am))

    # 6) 단면설계 수렴 전체 + 반복 횟수
    dt_conv, conv = _t(lambda: converge_sections(
        scene, ConvergeOptions(), prebuilt_am=am))
    iters = getattr(conv, 'iterations', -1)

    # ── 보고 ──
    def line(label, sec):
        print(f"[PERF] {label:32s} {sec*1000:9.1f} ms")

    print(f"[PERF] ===== 단계별 시간 (n_floors={n_floors}) =====")
    line("0. scene 로드", dt_load)
    print(f"[PERF] 0b. 접합부 설계(record_joints)   {dt_joint*1000:9.1f} ms"
          f"  (접합 {n_joint}개)")
    line("1. 토폴로지 빌드(1회)", dt_topo)
    line("2. ops 모델 빌드(1회 평균)", dt_build)
    line("3. 하중 계산(1회)", dt_loads)
    print(f"[PERF] 4. 솔브 단독 6성분 합           {dt_solve6*1000:9.1f} ms"
          f"  (D{dt_D*1000:.0f}/L{dt_L*1000:.0f}/Ex{dt_Ex*1000:.0f}"
          f"/Ey{dt_Ey*1000:.0f}/Wx{dt_Wx*1000:.0f}/Wy{dt_Wy*1000:.0f})")
    line("5. 구조해석 1회 전체", dt_all)
    line("6. 단면설계 수렴 전체", dt_conv)
    print(f"[PERF] 6-a. 수렴 반복 횟수            {iters}")
    if iters > 0:
        line("6-b. 수렴 반복 1회 평균", dt_conv / iters)
    print("[PERF] ===== 해석 두 단계 분해 =====")
    # 구조해석 1회 = 빌드1 + (솔브6 + 하중 + 추출). 빌드 비중 추정.
    others = max(dt_all - dt_build, 0.0)
    if dt_all > 0:
        print(f"[PERF] 구조해석 1회 중 빌드 비중 ≈ {100*dt_build/dt_all:5.1f}%"
              f"  (빌드 {dt_build*1000:.0f}ms / 나머지 {others*1000:.0f}ms)")
    print("[PERF] ===== 탭별 작업 시간 (사용자 질문) =====")
    print(f"[PERF] 접합부 설계        {dt_joint*1000:9.0f} ms")
    print(f"[PERF] 구조해석(1회)       {dt_all*1000:9.0f} ms")
    print(f"[PERF] 단면설계(수렴 {iters}회)  {dt_conv*1000:9.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
