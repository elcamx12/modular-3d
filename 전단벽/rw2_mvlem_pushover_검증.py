# -*- coding: utf-8 -*-
"""RW2 벽체 단일 MVLEM Pushover 검증 — 전단벽 해석 1차 검증.

[목적] OpenSeesPy MVLEM 요소로 만든 단일 벽체를 단조(monotonic) pushover 로
       밀어 '밑면전단력 - 상부 횡변위' 곡선을 뽑고, RW2 실험 포락선과 대조해
       MVLEM 모델이 실제 전단벽 거동을 재현하는지 확인한다.
       (강체 고정 우회를 버리고 fiber/MVLEM 으로 가기 위한 토대 검증.)

[정답지] 같은 폴더 RW2_검증_정답지.md (Thomsen & Wallace 2004 / PEER 607).
[단위계] N, mm, MPa (=N/mm²).
[실행]  이 .py 더블클릭(venv 자기-재실행 가드 포함) → 같은 폴더에
        rw2_pushover_곡선.png, rw2_pushover_데이터.csv 생성.

[주의] 콘크리트는 Concrete02 근사(정답지의 ConcreteCM r-형상계수는 미반영).
       후포락(파괴부) 변형률·잔류는 공학적 가정값이며, 1차 검증은 초기강성·
       항복점·최대강도의 포락 일치 정도로 판단한다(저 drift 과대평가는 monotonic
       특성으로 감안 — PEER §9.2).
"""
import os
import sys
import subprocess


# ── venv 자기-재실행 가드 (더블클릭 실행 지원) ──────────────
def _ensure_venv():
    here = os.path.dirname(os.path.abspath(__file__))         # 전단벽 폴더
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

# ── RW2 정답지 수치 (N, mm, MPa) ───────────────────────────
H = 3660.0          # 벽 높이
LW = 1219.0         # 벽 길이(폭)
T = 102.0           # 두께
N_ELE = 16          # 높이방향 MVLEM 요소 수
C_ROT = 0.4         # 회전중심 파라미터

# 단면 8 fiber — 폭(mm), 철근비, 경계요소(횡구속) 여부
FIB_W = [190.5, 38.1, 190.5, 190.5, 190.5, 190.5, 38.1, 190.5]
FIB_RHO = [0.0293, 0.0, 0.0033, 0.0033, 0.0033, 0.0033, 0.0, 0.0293]
FIB_CONF = [True, False, False, False, False, False, False, True]
N_FIB = 8

# 콘크리트 (Concrete02 근사) — fpc·epsc0 는 정답지, fpcu·epsU 는 공학 가정
FC_UNCONF, EPSC0_UNCONF = -42.8, -0.0021      # 웹 비횡구속
FC_CONF, EPSC0_CONF = -47.6, -0.0033          # 경계 횡구속
FPCU_UNCONF, EPSU_UNCONF = -8.56, -0.006      # 잔류≈0.2fpc, 비횡구속 극한변형
FPCU_CONF, EPSU_CONF = -14.3, -0.018          # 횡구속은 연성↑ (극한변형 크게)
LAMBDA = 0.1                                    # 재재하 강성비
FT = 2.03                                       # 인장강도(정답지)
ETS = FT / 0.002                                # 인장연화 기울기 근사

# 철근 Steel02 (Menegotto-Pinto) — 정답지 Table 7.2 의 '인장' 보정값 사용.
#   (휨강도는 인장철근이 지배 → 인장강성 보정 반영한 fy·b 가 실험에 더 맞다.
#    Steel02 는 fy 대칭이라 인장값으로 등록; 압축측은 콘크리트와 함께라 영향 작다.)
FY_3, B_3 = 395.0, 0.0185       # 경계 #3 (인장)
FY_2, B_2 = 336.0, 0.035        # 웹 #2 (인장)
ES = 200000.0
R0, CR1, CR2 = 20.0, 0.925, 0.15  # OpenSees Steel02 표준 권장 전이 파라미터

EC = 31030.0                     # 콘크리트 탄성계수(정답지)
G_MOD = EC / 2.4                 # 전단탄성계수 G ≈ Ec/2.4 (ν≈0.2), MPa

AG = LW * T                      # 전단면적
H_ELE = H / N_ELE                # 요소 1개 높이
# [함정] MVLEM 의 -matShear 는 '힘-변형' 강성(N/mm)을 직접 받는다(면적·높이 미반영).
#   따라서 전단탄성계수 G(MPa) 를 그대로 주면 안 되고 K = G·A/h 로 환산해 줘야 한다.
#   (G 를 그대로 주면 단면적배 만큼 물러져 변형이 전부 전단으로 빠지고 휨이 죽는다.)
K_SHEAR = G_MOD * AG / H_ELE     # 요소 전단강성 N/mm
AXIAL = 0.10 * AG * 42.8         # 일정 축력 0.10 Ag f'c ≈ 532 kN
DRIFT_MAX = 0.025                # pushover 목표 2.5% drift
TOP_DISP_MAX = DRIFT_MAX * H     # 상부 최대 변위 ≈ 91.5 mm
N_STEPS = 250

# 재료 태그
M_CONC_UNCONF, M_CONC_CONF, M_STEEL_3, M_STEEL_2, M_SHEAR = 1, 2, 3, 4, 5


def build_model():
    """RW2 단일 벽체 MVLEM 모델 + 일정 축력 적용."""
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)

    h_ele = H / N_ELE
    for i in range(N_ELE + 1):
        ops.node(i, 0.0, i * h_ele)
    ops.fix(0, 1, 1, 1)            # 바닥 완전고정

    # 재료
    ops.uniaxialMaterial("Concrete02", M_CONC_UNCONF,
                         FC_UNCONF, EPSC0_UNCONF, FPCU_UNCONF, EPSU_UNCONF,
                         LAMBDA, FT, ETS)
    ops.uniaxialMaterial("Concrete02", M_CONC_CONF,
                         FC_CONF, EPSC0_CONF, FPCU_CONF, EPSU_CONF,
                         LAMBDA, FT, ETS)
    ops.uniaxialMaterial("Steel02", M_STEEL_3, FY_3, ES, B_3, R0, CR1, CR2)
    ops.uniaxialMaterial("Steel02", M_STEEL_2, FY_2, ES, B_2, R0, CR1, CR2)
    ops.uniaxialMaterial("Elastic", M_SHEAR, K_SHEAR)

    # fiber 별 재료 배열 — 경계=횡구속+#3, 웹=비횡구속+#2
    mat_conc = [M_CONC_CONF if FIB_CONF[k] else M_CONC_UNCONF for k in range(N_FIB)]
    mat_steel = [M_STEEL_3 if k in (0, N_FIB - 1) else M_STEEL_2 for k in range(N_FIB)]
    thick = [T] * N_FIB

    for i in range(1, N_ELE + 1):
        ops.element("MVLEM", i, 0.0, i - 1, i, N_FIB, C_ROT,
                    "-thick", *thick,
                    "-width", *FIB_W,
                    "-rho", *FIB_RHO,
                    "-matConcrete", *mat_conc,
                    "-matSteel", *mat_steel,
                    "-matShear", M_SHEAR)

    # 일정 축력(중력) — 상단 노드에 압축
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(N_ELE, 0.0, -AXIAL, 0.0)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-6, 200)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 0.1)
    ops.analysis("Static")
    if ops.analyze(10) != 0:
        raise RuntimeError("축력(중력) 해석 수렴 실패")
    ops.loadConst("-time", 0.0)


def run_pushover():
    """상부 단조 횡변위 pushover → (상부변위[], 밑면전단[]) 반환."""
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    ops.load(N_ELE, 1.0, 0.0, 0.0)         # 단위 횡하중(변위제어용)

    dU = TOP_DISP_MAX / N_STEPS
    ops.integrator("DisplacementControl", N_ELE, 1, dU)
    ops.analysis("Static")

    disp, shear = [0.0], [0.0]
    for _ in range(N_STEPS):
        if ops.analyze(1) != 0:
            # 수렴 실패 시 알고리즘 바꿔 한 번 더
            ops.algorithm("NewtonLineSearch")
            ok = ops.analyze(1)
            ops.algorithm("Newton")
            if ok != 0:
                print(f"  [경고] {len(disp)}스텝에서 수렴 중단", flush=True)
                break
        ops.reactions()
        d = ops.nodeDisp(N_ELE, 1)
        v = -ops.nodeReaction(0, 1)        # 밑면 수평반력 = 밑면전단
        disp.append(d)
        shear.append(v)
    return np.array(disp), np.array(shear)


def save_outputs(disp, shear):
    """곡선 PNG + 데이터 CSV 저장 + 핵심 지표 출력."""
    drift = disp / H * 100.0               # %
    shear_kn = shear / 1000.0              # kN

    # CSV
    csv_path = os.path.join(HERE, "rw2_pushover_데이터.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("상부변위_mm,drift_%,밑면전단_kN\n")
        for d, dr, v in zip(disp, drift, shear_kn):
            f.write(f"{d:.4f},{dr:.4f},{v:.4f}\n")

    # 곡선 PNG
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # PNG 라벨은 영어 — DejaVu 폰트에 한글 글리프가 없어 그래프가 깨진다.
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(drift, shear_kn, "-", color="#1a73e8", lw=2,
            label="MVLEM pushover (analysis)")
    # RW2 실험 참고선 — 정밀 대조는 PEER Fig 7.27 그래프와 직접 비교
    ax.axhspan(150, 165, color="#e8710a", alpha=0.15,
               label="RW2 test peak ~150-165 kN (ref)")
    ax.set_xlabel("Drift Ratio (%)")
    ax.set_ylabel("Base Shear (kN)")
    ax.set_title("RW2 Single Wall - MVLEM Pushover Verification")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    png_path = os.path.join(HERE, "rw2_pushover_곡선.png")
    fig.savefig(png_path, dpi=150)

    # 핵심 지표
    vmax = float(np.max(shear_kn))
    imax = int(np.argmax(shear_kn))
    # 초기강성(초기 0.1% drift 구간 할선)
    k0_idx = np.searchsorted(drift, 0.1)
    k0 = (shear_kn[k0_idx] / disp[k0_idx]) if k0_idx < len(disp) and disp[k0_idx] > 0 else float("nan")

    print("\n" + "=" * 52)
    print(" RW2 MVLEM Pushover 검증 결과")
    print("=" * 52)
    print(f"  최종 도달 drift     : {drift[-1]:.2f} %")
    print(f"  최대 밑면전단       : {vmax:.1f} kN  (drift {drift[imax]:.2f}%)")
    print(f"  초기강성(~0.1%할선) : {k0:.1f} kN/mm")
    print(f"  적용 축력           : {AXIAL/1000.0:.0f} kN (0.10 Ag f'c)")
    print("-" * 52)
    print("  [참고] RW2 실험 최대 횡하중 ≈ 150~165 kN")
    print("         정밀 대조는 PEER 보고서 Fig 7.27 곡선과 직접 비교")
    print("=" * 52)
    print(f"\n  곡선 : {png_path}")
    print(f"  데이터: {csv_path}", flush=True)


def main():
    print("RW2 단일 벽체 MVLEM Pushover 검증 시작...", flush=True)
    build_model()
    print(f"  모델 구성 완료 — MVLEM {N_ELE}요소, fiber {N_FIB}개, 축력 {AXIAL/1000:.0f}kN", flush=True)
    disp, shear = run_pushover()
    save_outputs(disp, shear)


if __name__ == "__main__":
    main()
