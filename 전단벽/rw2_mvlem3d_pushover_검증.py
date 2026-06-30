# -*- coding: utf-8 -*-
"""P1: RW2 벽체 단일 MVLEM_3D Pushover 3D 재검증.

[목적] 2D MVLEM 검증(rw2_mvlem_pushover_검증.py)을 본체와 같은 3D·ndf=6 환경의
       MVLEM_3D 요소로 재현. 초기강성·항복강도가 2D/실험과 ±15% 일치하는지 확인.
       + D2 offset 후속: 노드를 벽 중심면(y=0) vs 외면(y=T/2)에 둘 때 면내 거동
       차이를 정량 비교(외면 배치가 면내 pushover 에 영향 없으면 offset 자유).

[정답지] RW2_검증_정답지.md / MVLEM_3D_API참조.md. 단위 N·mm·MPa.
[실행]  더블클릭(venv 자기재실행) → rw2_mvlem3d_곡선.png, rw2_mvlem3d_데이터.csv 생성.

[모델] 벽면 = XZ 평면(X=길이 LW, Z=높이 H), 두께방향 = Y. 각 높이레벨 좌/우 2노드,
       MVLEM_3D 한 요소 = 4노드(하좌·하우·상우·상좌, 반시계). 베이스 2노드 6DOF 고정.
"""
import os
import sys
import subprocess


def _ensure_venv():
    here = os.path.dirname(os.path.abspath(__file__))
    venv_py = os.path.join(os.path.dirname(here), "venv", "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        return
    if os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(venv_py):
        return
    raise SystemExit(subprocess.call([venv_py, os.path.abspath(__file__)] + sys.argv[1:]))


_ensure_venv()

import numpy as np
import openseespy.opensees as ops

HERE = os.path.dirname(os.path.abspath(__file__))

# ── RW2 정답지 (N, mm, MPa) — 2D 검증과 동일 ──
H, LW, T, N_ELE, C_ROT = 3660.0, 1219.0, 102.0, 16, 0.4
FIB_W = [190.5, 38.1, 190.5, 190.5, 190.5, 190.5, 38.1, 190.5]
FIB_RHO = [0.0293, 0.0, 0.0033, 0.0033, 0.0033, 0.0033, 0.0, 0.0293]
FIB_CONF = [True, False, False, False, False, False, False, True]
N_FIB = 8

FC_UNCONF, EPSC0_UNCONF = -42.8, -0.0021
FC_CONF, EPSC0_CONF = -47.6, -0.0033
FPCU_UNCONF, EPSU_UNCONF = -8.56, -0.006
FPCU_CONF, EPSU_CONF = -14.3, -0.018
LAMBDA, FT = 0.1, 2.03
ETS = FT / 0.002
FY_3, B_3 = 395.0, 0.0185
FY_2, B_2 = 336.0, 0.035
ES = 200000.0
R0, CR1, CR2 = 20.0, 0.925, 0.15
EC = 31030.0
G_MOD = EC / 2.4

AG = LW * T
H_ELE = H / N_ELE
K_SHEAR = G_MOD * AG / H_ELE        # 2D 검증서 발견: -matShear 는 N/mm 직접 (G·A/h)
AXIAL = 0.10 * AG * 42.8
DRIFT_MAX = 0.025
TOP_DISP_MAX = DRIFT_MAX * H
N_STEPS = 250

M_CONC_UNCONF, M_CONC_CONF, M_STEEL_3, M_STEEL_2, M_SHEAR = 1, 2, 3, 4, 5

# 노드 id: 레벨 i 의 좌 = 2*i, 우 = 2*i+1 (i=0..N_ELE)
def _nL(i): return 2 * i
def _nR(i): return 2 * i + 1


def build_3d(y_off):
    """RW2 단일 벽 MVLEM_3D 모델 + 일정 축력. y_off = 노드 Y(중심면 0 / 외면 T/2)."""
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)

    for i in range(N_ELE + 1):
        z = i * H_ELE
        ops.node(_nL(i), 0.0, y_off, z)
        ops.node(_nR(i), LW, y_off, z)
    # 베이스(z=0) 2노드 완전고정
    ops.fix(_nL(0), 1, 1, 1, 1, 1, 1)
    ops.fix(_nR(0), 1, 1, 1, 1, 1, 1)

    ops.uniaxialMaterial("Concrete02", M_CONC_UNCONF,
                         FC_UNCONF, EPSC0_UNCONF, FPCU_UNCONF, EPSU_UNCONF, LAMBDA, FT, ETS)
    ops.uniaxialMaterial("Concrete02", M_CONC_CONF,
                         FC_CONF, EPSC0_CONF, FPCU_CONF, EPSU_CONF, LAMBDA, FT, ETS)
    ops.uniaxialMaterial("Steel02", M_STEEL_3, FY_3, ES, B_3, R0, CR1, CR2)
    ops.uniaxialMaterial("Steel02", M_STEEL_2, FY_2, ES, B_2, R0, CR1, CR2)
    ops.uniaxialMaterial("Elastic", M_SHEAR, K_SHEAR)

    mat_conc = [M_CONC_CONF if FIB_CONF[k] else M_CONC_UNCONF for k in range(N_FIB)]
    mat_steel = [M_STEEL_3 if k in (0, N_FIB - 1) else M_STEEL_2 for k in range(N_FIB)]
    thick = [T] * N_FIB

    # MVLEM_3D 요소: 4노드 반시계(하좌→하우→상우→상좌)
    for i in range(1, N_ELE + 1):
        hl, hr = _nL(i - 1), _nR(i - 1)
        tl, tr = _nL(i), _nR(i)
        ops.element("MVLEM_3D", i, hl, hr, tr, tl, N_FIB,
                    "-thick", *thick, "-width", *FIB_W, "-rho", *FIB_RHO,
                    "-matConcrete", *mat_conc, "-matSteel", *mat_steel,
                    "-matShear", M_SHEAR, "-CoR", C_ROT)

    # 일정 축력 — 상단 2노드에 Z(수직) 압축 분배
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(_nL(N_ELE), 0.0, 0.0, -AXIAL / 2.0, 0.0, 0.0, 0.0)
    ops.load(_nR(N_ELE), 0.0, 0.0, -AXIAL / 2.0, 0.0, 0.0, 0.0)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-6, 300)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 0.1)
    ops.analysis("Static")
    if ops.analyze(10) != 0:
        raise RuntimeError("축력(중력) 해석 수렴 실패")
    ops.loadConst("-time", 0.0)


def run_pushover():
    """상단 단조 X변위 pushover → (상부변위[], 밑면전단[])."""
    ctrl = _nL(N_ELE)
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    ops.load(_nL(N_ELE), 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    ops.load(_nR(N_ELE), 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    dU = TOP_DISP_MAX / N_STEPS
    ops.integrator("DisplacementControl", ctrl, 1, dU)
    ops.analysis("Static")

    disp, shear = [0.0], [0.0]
    for _ in range(N_STEPS):
        if ops.analyze(1) != 0:
            ops.algorithm("NewtonLineSearch")
            ok = ops.analyze(1)
            ops.algorithm("Newton")
            if ok != 0:
                print(f"  [경고] {len(disp)}스텝에서 수렴 중단", flush=True)
                break
        ops.reactions()
        d = ops.nodeDisp(ctrl, 1)
        v = -(ops.nodeReaction(_nL(0), 1) + ops.nodeReaction(_nR(0), 1))
        disp.append(d)
        shear.append(v)
    return np.array(disp), np.array(shear)


def run_case(y_off, label):
    print(f"[{label}] y_off={y_off} 빌드·해석...", flush=True)
    build_3d(y_off)
    return run_pushover()


def metrics(disp, shear):
    drift = disp / H * 100.0
    shear_kn = shear / 1000.0
    vmax = float(np.max(shear_kn))
    imax = int(np.argmax(shear_kn))
    k0_idx = np.searchsorted(drift, 0.1)
    k0 = (shear_kn[k0_idx] / disp[k0_idx]) if k0_idx < len(disp) and disp[k0_idx] > 0 else float("nan")
    return drift, shear_kn, vmax, drift[imax], k0


def main():
    print("RW2 단일 벽 MVLEM_3D Pushover 3D 재검증 시작...", flush=True)
    dC, sC = run_case(0.0, "center(y=0)")
    dF, sF = run_case(T / 2.0, "face(y=T/2)")

    drC, skC, vmaxC, dvmaxC, k0C = metrics(dC, sC)
    drF, skF, vmaxF, dvmaxF, k0F = metrics(dF, sF)

    # CSV
    csv_path = os.path.join(HERE, "rw2_mvlem3d_데이터.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("case,drift_%,base_shear_kN\n")
        for dr, sk in zip(drC, skC):
            f.write(f"center,{dr:.4f},{sk:.4f}\n")
        for dr, sk in zip(drF, skF):
            f.write(f"face,{dr:.4f},{sk:.4f}\n")

    # PNG
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(drC, skC, "-", color="#1a73e8", lw=2, label="MVLEM_3D center (y=0)")
    ax.plot(drF, skF, "--", color="#d93025", lw=1.8, label="MVLEM_3D face (y=T/2)")
    ax.axhspan(150, 165, color="#e8710a", alpha=0.15, label="RW2 test peak ~150-165 kN")
    ax.axhline(190, color="#80868b", ls=":", lw=1, label="2D verify max ~190 kN")
    ax.set_xlabel("Drift Ratio (%)")
    ax.set_ylabel("Base Shear (kN)")
    ax.set_title("RW2 Single Wall - MVLEM_3D Pushover (3D) Verification")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    png_path = os.path.join(HERE, "rw2_mvlem3d_곡선.png")
    fig.savefig(png_path, dpi=150)

    print("\n" + "=" * 56)
    print(" RW2 MVLEM_3D 3D Pushover 검증 결과")
    print("=" * 56)
    print(f"  [center y=0]  최대전단 {vmaxC:.1f} kN (drift {dvmaxC:.2f}%), 초기강성 {k0C:.1f} kN/mm")
    print(f"  [face y=T/2]  최대전단 {vmaxF:.1f} kN (drift {dvmaxF:.2f}%), 초기강성 {k0F:.1f} kN/mm")
    print(f"  중심면 vs 외면 최대전단 차이: {abs(vmaxC-vmaxF)/max(vmaxC,1e-9)*100:.2f} %")
    print("-" * 56)
    print("  [2D 검증 기준] 초기강성 ~24 kN/mm, 항복 150~165 kN, 최대 ~190 kN")
    print("  [판정] 초기강성·항복강도가 2D/실험과 ±15% 이내인지 곡선 대조")
    print("=" * 56)
    print(f"\n  곡선 : {png_path}")
    print(f"  데이터: {csv_path}", flush=True)


if __name__ == "__main__":
    main()
