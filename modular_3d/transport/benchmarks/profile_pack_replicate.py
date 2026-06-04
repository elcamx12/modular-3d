# -*- coding: utf-8 -*-
"""층묶음 복제 패킹 검증 스크립트 (6층·18층 합성).

Cscene.json(floor 0~2 완전묶음 + floor 3 지붕)의 패널을 floor_index 만 바꿔 복제해
6층(2묶음)·18층(6묶음) 패널 집합을 합성하고, pack_items 에 직접 넣어 다음을 검증한다:
    1) 복제 경로 진입([PACK_PROF] "층묶음 복제 경로")
    2) 분기한정 호출이 층수와 무관히 2회(기준+자투리)인지
    3) 패널 보존 — 입력 패널 수 == 모든 회차 items 수(누락/중복 0)
    4) 패널 name 고유성(source_index 역매핑 전제)
    5) 18층이 유한 시간에 종료되는지

구조해석/단면산정은 Cscene 으로 1회만 하고, 패널을 합성 변형해 재사용한다.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from pathlib import Path


os.environ["MODULAR_PACK_PROFILE"] = "1"


def _ensure_venv() -> None:
    root = Path(__file__).resolve().parents[3]
    venv_py = root / "venv" / "Scripts" / "python.exe"
    try:
        cur = Path(sys.executable).resolve()
    except Exception:
        cur = None
    if venv_py.exists() and cur != venv_py.resolve():
        os.execv(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_venv()

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SCENE_PATH = r"C:\Users\이건영\Desktop\종설\Cscene.json"
DEFAULT_POLICY = "1종"


def _build_base_panels():
    """Cscene → ti.panels. 구조해석~단면산정~운송입력 1회."""
    from modular_3d.io.scene_io import load_scene
    from modular_3d.analysis.topology import build_analysis_model
    from modular_3d.analysis.ops_solver import solve_all_cases
    from modular_3d.analysis.section_design import design_all_policies_envelope
    from modular_3d.transport.adapter import build_transport_input, TransportOptions
    from modular_3d.transport.catalog_io import load_all_trucks
    from modular_3d.transport.models import SiteLimit, SpacingParams

    scene, _ = load_scene(SCENE_PATH)
    am = build_analysis_model(scene)
    all_results = solve_all_cases(scene, prebuilt_am=am)
    design_by_policy = design_all_policies_envelope(am, all_results)
    policy = DEFAULT_POLICY if DEFAULT_POLICY in design_by_policy else next(iter(design_by_policy))
    ti = build_transport_input(scene, am, design_by_policy[policy], policy, TransportOptions())
    trucks = load_all_trucks(active_only=True)
    return list(ti.panels), list(ti.modules), trucks, SiteLimit(), SpacingParams()


def _synth_building(base_panels, K: int):
    """floor 0~2(완전묶음 1개) × K 복제 + 맨 위 지붕층(원래 floor 3)을 최상단으로.

    각 복제 패널의 name 에 층묶음 접미사를 붙여 고유성 보장.
    반환: 합성 패널 리스트.
    """
    g0 = [p for p in base_panels if 0 <= p.floor_index <= 2]
    roof = [p for p in base_panels if p.floor_index == 3]
    out = []
    for k in range(K):
        for p in g0:
            out.append(replace(
                p, floor_index=p.floor_index + 3 * k, name=f"{p.name}@L{k}",
            ))
    top_floor = 3 * K
    for p in roof:
        out.append(replace(p, floor_index=top_floor, name=f"{p.name}@ROOF"))
    return out


def main() -> int:
    from modular_3d.transport.packer import pack_items

    print(f"[준비] Cscene 기준 패널 빌드…", file=sys.stderr)
    base_panels, base_modules, trucks, site, spacing = _build_base_panels()
    g0_n = len([p for p in base_panels if 0 <= p.floor_index <= 2])
    roof_n = len([p for p in base_panels if p.floor_index == 3])
    print(f"[준비] 기준 묶음(floor0~2) {g0_n}개 / 지붕(floor3) {roof_n}개", file=sys.stderr)

    ok = True
    for K, label in [(2, "6층"), (6, "18층")]:
        syn = _synth_building(base_panels, K)
        # 모듈도 K배 복제 (이름 고유화) — 실제 다층 건물 흉내
        mod_syn = []
        for k in range(K):
            for mm in base_modules:
                mod_syn.append(replace(mm, name=f"{mm.name}@L{k}"))
        n_in = len(syn)
        print(f"\n===== {label} 합성 (완전묶음 {K}개, 패널 {n_in}개, "
              f"모듈 {len(mod_syn)}개) =====", file=sys.stderr)
        sys.stderr.flush()
        t0 = time.perf_counter()
        pack = pack_items(mod_syn, syn, trucks, site, spacing)
        dt = (time.perf_counter() - t0) * 1000.0

        # 패널 보존은 패널 회차만 (모듈 회차 제외)
        n_out = sum(len(t.items) for t in pack.trips if t.kind != "module")
        names = [it.name for t in pack.trips if t.kind != "module" for it in t.items]
        uniq = len(names) == len(set(names))
        preserve = n_in == n_out

        print(f"[{label}] pack_items 종료: {dt:.1f} ms, 총 회차={pack.total_trips}",
              file=sys.stderr)
        print(f"[{label}] 패널 보존: 입력 {n_in} vs 출력 {n_out} → "
              f"{'OK' if preserve else '실패!'}", file=sys.stderr)
        print(f"[{label}] name 고유성: {'OK' if uniq else '중복!'} "
              f"({len(names)}개 중 고유 {len(set(names))}개)", file=sys.stderr)
        # 모듈 회차의 일반/수직 혼재 검사
        mix_trips = []
        for t in pack.trips:
            if t.kind != "module":
                continue
            nms = [getattr(it, "name", "?") for it in t.items]
            has_v = any("수직" in n for n in nms)
            has_m = any("수직" not in n for n in nms)
            if has_v and has_m:
                mix_trips.append((t.trip_no, nms))
        print(f"[{label}] 모듈 회차 혼재(일반+수직 한 트럭): {len(mix_trips)}건"
              + ("" if not mix_trips else
                 " ⚠ → " + "; ".join(f"회차{n}:{ns}" for n, ns in mix_trips[:5])),
              file=sys.stderr)
        # 모듈 회차 종류 나열 순서 (V=수직, M=일반) — 번갈아 나오는지 확인
        mod_seq = "".join(
            "V" if any("수직" in getattr(it, "name", "") for it in t.items) else "M"
            for t in pack.trips if t.kind == "module"
        )
        print(f"[{label}] 모듈 회차 종류 순서: {mod_seq}", file=sys.stderr)
        if not (preserve and uniq):
            ok = False

    print(f"\n[결과] 전체 검증 {'통과' if ok else '실패'}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
