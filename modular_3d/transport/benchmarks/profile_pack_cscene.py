# -*- coding: utf-8 -*-
"""Cscene.json(3층) 헤드리스 파이프라인 측정 스크립트.

구조해석 → 단면산정 → 운송 입력 빌드 → pack_items 까지 GUI 없이 수행하고,
MODULAR_PACK_PROFILE=1 로 켜진 [PACK_PROF] 단계별 시간을 stderr 로 받는다.

호출 순서(자연어):
    1) scene_io.load_scene 으로 Scene 로드
    2) topology.build_analysis_model 로 해석 모델 빌드
    3) ops_solver.solve_all_cases 로 KDS 하중조합 전 케이스 해석(prebuilt_am 재사용)
    4) section_design.design_all_policies_envelope 로 정책별 DesignResult 생성
    5) adapter.build_transport_input 로 모듈/패널 운송 입력 빌드
    6) catalog_io.load_all_trucks 로 기본 트럭 카탈로그 로드
    7) packer.pack_items 로 패킹(시간 측정)

실행: my_project 를 루트로 두고 venv python 으로 직접 실행한다.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


# ── 측정 플래그는 무조건 import 보다 먼저 켠다 ───────────────────────
os.environ["MODULAR_PACK_PROFILE"] = "1"


# ── venv 자기-재실행 가드 ───────────────────────────────────────────
def _ensure_venv() -> None:
    """현재 인터프리터가 my_project/venv 가 아니면 venv python 으로 재실행."""
    root = Path(__file__).resolve().parents[3]  # .../my_project
    venv_py = root / "venv" / "Scripts" / "python.exe"
    try:
        cur = Path(sys.executable).resolve()
    except Exception:
        cur = None
    if venv_py.exists() and cur != venv_py.resolve():
        os.execv(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_venv()

# my_project 를 sys.path 에 추가 (modular_3d.xxx import 가능하도록)
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── 입력 파일 경로 ──────────────────────────────────────────────────
SCENE_PATH = r"C:\Users\이건영\Desktop\종설\Cscene.json"

DEFAULT_POLICY = "1종"  # 기본 정책 하나만 사용


def main() -> int:
    from modular_3d.io.scene_io import load_scene
    from modular_3d.analysis.topology import build_analysis_model
    from modular_3d.analysis.ops_solver import solve_all_cases
    from modular_3d.analysis.section_design import design_all_policies_envelope
    from modular_3d.transport.adapter import build_transport_input, TransportOptions
    from modular_3d.transport.catalog_io import load_all_trucks
    from modular_3d.transport.models import SiteLimit, SpacingParams
    from modular_3d.transport.packer import pack_items

    print(f"[측정] Scene 로드: {SCENE_PATH}", file=sys.stderr)
    t0 = time.perf_counter()
    scene, n_floors = load_scene(SCENE_PATH)
    print(f"[측정] Scene 로드 완료 ({(time.perf_counter()-t0)*1000:.1f} ms), "
          f"층수={n_floors}, 컴포넌트={len(scene.components)}", file=sys.stderr)

    # ── 구조해석 ────────────────────────────────────────────────────
    print("[측정] 해석 모델 빌드(build_analysis_model)…", file=sys.stderr)
    t0 = time.perf_counter()
    am = build_analysis_model(scene)
    print(f"[측정] build_analysis_model 완료 ({(time.perf_counter()-t0)*1000:.1f} ms)",
          file=sys.stderr)

    print("[측정] 구조해석(solve_all_cases)…", file=sys.stderr)
    t0 = time.perf_counter()
    all_results = solve_all_cases(scene, prebuilt_am=am)
    print(f"[측정] solve_all_cases 완료 ({(time.perf_counter()-t0)*1000:.1f} ms), "
          f"케이스={list(all_results.keys())}", file=sys.stderr)

    # ── 단면산정 ────────────────────────────────────────────────────
    print("[측정] 단면산정(design_all_policies_envelope)…", file=sys.stderr)
    t0 = time.perf_counter()
    design_by_policy = design_all_policies_envelope(am, all_results)
    print(f"[측정] 단면산정 완료 ({(time.perf_counter()-t0)*1000:.1f} ms), "
          f"정책={list(design_by_policy.keys())}", file=sys.stderr)

    policy = DEFAULT_POLICY if DEFAULT_POLICY in design_by_policy else next(iter(design_by_policy))
    design = design_by_policy[policy]
    print(f"[측정] 사용 정책: {policy} (그룹 {len(design.groups)}개)", file=sys.stderr)

    # ── 운송 입력 빌드 ──────────────────────────────────────────────
    print("[측정] 운송 입력 빌드(build_transport_input)…", file=sys.stderr)
    t0 = time.perf_counter()
    options = TransportOptions()
    ti = build_transport_input(scene, am, design, policy, options)
    print(f"[측정] build_transport_input 완료 ({(time.perf_counter()-t0)*1000:.1f} ms)",
          file=sys.stderr)

    # ── 트럭 / 현장제한 / 간격 ──────────────────────────────────────
    trucks = load_all_trucks(active_only=True)
    site = SiteLimit()       # 모든 항목 None = 현장 제한 프리패스(기본)
    spacing = SpacingParams()  # 내장 고정 간격

    n_mod = len(ti.modules)
    n_pan = len(ti.panels)
    n_truck = len(trucks)
    print(f"[규모] 모듈={n_mod}, 패널={n_pan}, 트럭={n_truck}대 "
          f"({[t.name for t in trucks]})", file=sys.stderr)
    # 패널 층(floor_index) 분포 — 어댑터 주입 검증용
    from collections import Counter
    fd = Counter(p.floor_index for p in ti.panels)
    print(f"[층분포] 패널 floor_index 분포 = {dict(sorted(fd.items()))}",
          file=sys.stderr)
    # 모듈 무게(정확 프레임 합산) + 단면 혼재 경고 검증
    print("[모듈무게] " + ", ".join(
        f"{m.name}={m.weight:.0f}kg" for m in ti.modules), file=sys.stderr)
    mix_warns = [w for w in ti.diagnostics.warnings if "혼재" in w]
    print(f"[혼재경고] {len(mix_warns)}건" +
          ("" if not mix_warns else " → " + " / ".join(mix_warns[:5])),
          file=sys.stderr)

    # ── 패킹 실행(측정) ────────────────────────────────────────────
    # 연속 3회 호출 — 1회차는 import·캐시 워밍업 포함, 2~3회차는 순수 연산.
    pack = None
    for run_i in range(1, 4):
        print(f"[측정] pack_items 호출 #{run_i}…", file=sys.stderr)
        sys.stderr.flush()
        t0 = time.perf_counter()
        pack = pack_items(ti.modules, ti.panels, trucks, site, spacing)
        total_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[측정] pack_items #{run_i} 전체 소요: {total_ms:.1f} ms",
              file=sys.stderr)
    print(f"[결과] 총 회차={pack.total_trips}, 막힌 화물={len(pack.blocked)}",
          file=sys.stderr)
    # 모듈 회차 구성 — 일반/수직 혼재 여부 추적
    print("[모듈회차] (트럭 / 화물 이름들):", file=sys.stderr)
    for t in pack.trips:
        if t.kind != "module":
            continue
        names = [getattr(it, "name", "?") for it in t.items]
        has_v = any("수직" in n for n in names)
        has_m = any("수직" not in n for n in names)
        tag = " ⚠혼재" if (has_v and has_m) else ""
        print(f"    회차{t.trip_no:>2}: {t.truck.name:<22} {names}{tag}",
              file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
