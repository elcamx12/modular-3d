"""구조해석(solve_all_cases) 회귀 검증 — 성능 재구조화 전/후 결과 동일성 확인.

[목적]
"6 성분마다 모델 재빌드" → "모델 1빌드 + 케이스별 재사용" 재구조화(1순위 성능
개선)가 **해석 결과를 바꾸지 않음**을 자동으로 보장하기 위한 안전망.

[사용]
  # 1) 재구조화 전 — 현재 코드로 기준값 저장
  python -m modular_3d.analysis.tests.regression_solve --mode save \
      --scene "C:/Users/이건영/Desktop/종설/scene.json" \
      --baseline "C:/Users/이건영/Desktop/종설/regression_baseline.json"

  # 2) 재구조화 후 — 같은 입력으로 풀어 기준값과 대조
  python -m modular_3d.analysis.tests.regression_solve --mode check \
      --scene "...scene.json" --baseline "...regression_baseline.json"

check 모드는 케이스별 부재력·노드변위·반력·층변위·합계를 비교해 최대 오차를
출력하고, 허용오차를 넘으면 종료코드 1(FAIL)로 끝난다.

비교 규칙: 분모 = max(|a|, |b|). 분모가 ABS_FLOOR 미만이면 절대오차로 판정
(0 근처 값의 상대오차 폭주 방지). 기본 상대 1e-6, 절대바닥 1e-3 N(=0.001N, 무시 가능).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

import numpy as np


# ── 허용오차 ───────────────────────────────────────────────────
REL_TOL = 1.0e-6      # 상대오차 허용
ABS_FLOOR = 1.0e-3    # 분모가 이 값 미만이면 절대오차로 비교 (N·mm 단위)


# ── 결과 → 직렬화 가능 dict ────────────────────────────────────
def _arr(a) -> list:
    """numpy 배열/시퀀스를 float 리스트로."""
    return [float(x) for x in np.asarray(a, dtype=np.float64).ravel()]


def _serialize_results(results: Dict[str, Any]) -> Dict[str, Any]:
    """{case_name → OpsResults} → 순수 dict(JSON 직렬화 가능)."""
    out: Dict[str, Any] = {}
    for case, res in results.items():
        mf = {}
        for mid, f in res.member_forces.items():
            # 양단 12성분 [f_i(6) + f_j(6)]
            mf[str(mid)] = _arr(f.f_i) + _arr(f.f_j)
        nd = {str(nid): _arr(v) for nid, v in res.node_disps.items()}
        br = {str(nid): _arr(v) for nid, v in res.base_reactions.items()}
        sd = {str(z): _arr(v) for z, v in res.story_disp.items()}
        out[case] = {
            "member_forces": mf,
            "node_disps": nd,
            "base_reactions": br,
            "story_disp": sd,
            "totals": {
                "applied_z": float(res.total_applied_load_z),
                "applied_x": float(res.total_applied_load_x),
                "applied_y": float(res.total_applied_load_y),
                "base_rz": float(res.total_base_reaction_z),
                "base_rx": float(res.total_base_reaction_x),
                "base_ry": float(res.total_base_reaction_y),
                "equilibrium_ratio": float(res.equilibrium_ratio),
            },
        }
    return out


# ── 해석 실행 ──────────────────────────────────────────────────
def _run(scene_path: str) -> Dict[str, Any]:
    from modular_3d.io.scene_io import load_scene
    from modular_3d.analysis.topology import build_analysis_model
    from modular_3d.analysis.ops_solver import solve_all_cases

    scene, n_floors = load_scene(scene_path)
    # model_provider 캐시를 우회해 순수 빌드(단독 실행이라 캐시 의미 없음).
    am = build_analysis_model(scene)
    results = solve_all_cases(scene, prebuilt_am=am)
    return _serialize_results(results)


# ── 비교 ───────────────────────────────────────────────────────
def _cmp_value(a: float, b: float) -> float:
    """두 스칼라의 (상대 또는 절대) 오차. 분모가 작으면 절대오차."""
    denom = max(abs(a), abs(b))
    if denom < ABS_FLOOR:
        return abs(a - b)
    return abs(a - b) / denom


def _cmp_seq(a: list, b: list, path: str, worst: dict) -> None:
    if len(a) != len(b):
        worst.setdefault("shape", []).append(path)
        return
    for i, (x, y) in enumerate(zip(a, b)):
        e = _cmp_value(x, y)
        if e > worst["max_err"]:
            worst["max_err"] = e
            worst["where"] = f"{path}[{i}] ({x:.6g} vs {y:.6g})"


def _cmp_dict_of_seq(da: dict, db: dict, prefix: str, worst: dict) -> None:
    keys = set(da) | set(db)
    for k in keys:
        if k not in da or k not in db:
            worst.setdefault("missing", []).append(f"{prefix}/{k}")
            continue
        _cmp_seq(da[k], db[k], f"{prefix}/{k}", worst)


def _compare(base: Dict[str, Any], cur: Dict[str, Any]) -> dict:
    worst = {"max_err": 0.0, "where": "", "missing": [], "shape": []}
    cases = set(base) | set(cur)
    for case in sorted(cases):
        if case not in base or case not in cur:
            worst["missing"].append(f"case:{case}")
            continue
        b, c = base[case], cur[case]
        _cmp_dict_of_seq(b["member_forces"], c["member_forces"], f"{case}/MF", worst)
        _cmp_dict_of_seq(b["node_disps"], c["node_disps"], f"{case}/ND", worst)
        _cmp_dict_of_seq(b["base_reactions"], c["base_reactions"], f"{case}/BR", worst)
        _cmp_dict_of_seq(b["story_disp"], c["story_disp"], f"{case}/SD", worst)
        # 합계 스칼라
        for tk in b["totals"]:
            e = _cmp_value(b["totals"][tk], c["totals"].get(tk, 0.0))
            if e > worst["max_err"]:
                worst["max_err"] = e
                worst["where"] = f"{case}/totals/{tk}"
    return worst


# ── main ───────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["save", "check"], required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--baseline", required=True)
    args = ap.parse_args()

    cur = _run(args.scene)

    if args.mode == "save":
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
        n_cases = len(cur)
        n_mf = sum(len(v["member_forces"]) for v in cur.values())
        print(f"[SAVE] 기준값 저장 완료 → {args.baseline}")
        print(f"       케이스 {n_cases}개, 부재력 항목 총 {n_mf}개")
        return 0

    # check
    with open(args.baseline, "r", encoding="utf-8") as f:
        base = json.load(f)
    worst = _compare(base, cur)
    print(f"[CHECK] 최대 오차 = {worst['max_err']:.3e}  (허용 {REL_TOL:.1e})")
    if worst["where"]:
        print(f"        위치: {worst['where']}")
    if worst["missing"]:
        print(f"        누락/추가 항목 {len(worst['missing'])}개: {worst['missing'][:10]}")
    if worst["shape"]:
        print(f"        형상 불일치 {len(worst['shape'])}개: {worst['shape'][:10]}")
    ok = (worst["max_err"] <= REL_TOL
          and not worst["missing"] and not worst["shape"])
    print("[CHECK] 결과:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
