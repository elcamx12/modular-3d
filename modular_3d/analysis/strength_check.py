"""KBC 2022 LRFD 강재 단면 강도 검토.

물량산출/단면 자동 산정 Phase 1 — 설계서 1-B 섹션 구현.

검토 항목 (모두 응력비 ratio = demand / capacity 형태):
    1. 축력      check_axial      — 압축 좌굴 + 인장
    2. 휨        check_bending    — 강축·약축 동시 (단순 합)
    3. 전단      check_shear      — 강축·약축 중 큰 값
    4. 조합      check_combined   — KBC H1.1 P-M 상관식
    5. 처짐      check_deflection — 사용성 (L/360 일반, L/180 캔틸)

단위: N, mm, MPa
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .constants import STEEL_E_MPA
from .section_catalog import (
    SHSSection,
    STEEL_FY_MPA,
    PHI_T, PHI_C, PHI_B, PHI_V,
)


# ── 처짐 한계 ─────────────────────────────────────────────────
DEFL_LIMIT_BEAM_DENOM = 360.0       # 일반 보 L/360
DEFL_LIMIT_CANTILEVER_DENOM = 180.0  # 캔틸레버 L/180


@dataclass
class MemberDemand:
    """부재 요구 내력 (해석 결과로부터 추출).

    필드:
        P       : 축력 (N) — 인장 양수, 압축 음수
        Mz      : 강축 휨모멘트 (N·mm) — y축 회전축 (h 방향 휨)
        My      : 약축 휨모멘트 (N·mm)
        Vy      : y방향 전단 (N)
        Vz      : z방향 전단 (N)
        L       : 부재 실길이 (mm)
        kind    : 'column' | 'beam' | 'cantilever'
        K       : 유효좌굴길이 계수 (기본 1.0 — 양단 핀)

    NOTE: 처짐은 단면 의존이므로 demand 에 직접 저장하지 않는다.
          check_deflection(section, demand) 에서 단면별 추정.
    """
    P: float
    Mz: float
    My: float
    Vy: float
    Vz: float
    L: float
    kind: str = 'beam'
    K: float = 1.0


@dataclass
class CheckResult:
    """단면 검토 결과 — 5개 항목 + 최댓값 + 지배 항목."""
    ratio_axial: float
    ratio_bend: float
    ratio_shear: float
    ratio_combined: float
    ratio_defl: float
    ratio_max: float
    critical_item: str    # 'axial' | 'bend' | 'shear' | 'combined' | 'defl'
    ok: bool

    @classmethod
    def from_ratios(cls,
                    ra: float, rb: float, rs: float,
                    rc: float, rd: float) -> "CheckResult":
        items = {'axial': ra, 'bend': rb, 'shear': rs,
                 'combined': rc, 'defl': rd}
        critical = max(items, key=lambda k: items[k])
        rmax = items[critical]
        return cls(
            ratio_axial=ra, ratio_bend=rb, ratio_shear=rs,
            ratio_combined=rc, ratio_defl=rd,
            ratio_max=rmax, critical_item=critical,
            ok=(rmax <= 1.0),
        )


# ── 1. 축력 ─────────────────────────────────────────────────
def check_axial(section: SHSSection, P: float, L: float, K: float = 1.0) -> float:
    """축력 검토 (압축이면 좌굴 포함, 인장이면 항복).

    압축 (P < 0):
        Fe = π² E / (KL/r)²
        λc² = Fy / Fe
        Fcr = 0.658^λc² · Fy   (λc ≤ 1.5)
            = 0.877 / λc² · Fy (λc > 1.5)
        ratio = |P| / (φc · Fcr · A)

    인장 (P ≥ 0):
        ratio = P / (φt · Fy · A)
    """
    if abs(P) < 1e-6:
        return 0.0

    if P < 0:
        # 압축
        r = min(section.ry, section.rz)
        slenderness = (K * L) / r
        if slenderness < 1e-6:
            Fcr = STEEL_FY_MPA
        else:
            Fe = math.pi ** 2 * STEEL_E_MPA / (slenderness ** 2)
            lambda_c_sq = STEEL_FY_MPA / Fe
            lambda_c = math.sqrt(lambda_c_sq)
            if lambda_c <= 1.5:
                Fcr = (0.658 ** lambda_c_sq) * STEEL_FY_MPA
            else:
                Fcr = (0.877 / lambda_c_sq) * STEEL_FY_MPA
        Pn = Fcr * section.A
        return abs(P) / (PHI_C * Pn)
    else:
        # 인장
        Pn = STEEL_FY_MPA * section.A
        return P / (PHI_T * Pn)


# ── 2. 휨 ───────────────────────────────────────────────────
def check_bending(section: SHSSection, Mz: float, My: float) -> float:
    """휨 검토 — 강축+약축 단순 합 (보수적).

    Mn = Fy · S
    ratio = |Mz|/(φb·Fy·Sy) + |My|/(φb·Fy·Sz)

    SHS 박스 단면은 횡-비틀림 좌굴 영향이 작아 단순 항복식 사용.
    """
    cap_z = PHI_B * STEEL_FY_MPA * section.Sy
    cap_y = PHI_B * STEEL_FY_MPA * section.Sz
    rz = abs(Mz) / cap_z if cap_z > 0 else 0.0
    ry = abs(My) / cap_y if cap_y > 0 else 0.0
    return rz + ry


# ── 3. 전단 ─────────────────────────────────────────────────
def check_shear(section: SHSSection, Vy: float, Vz: float) -> float:
    """전단 검토 — 강·약축 중 큰 값.

    Vn = 0.6 · Fy · Av
    """
    cap_y = PHI_V * 0.6 * STEEL_FY_MPA * section.Avy
    cap_z = PHI_V * 0.6 * STEEL_FY_MPA * section.Avz
    ry_ = abs(Vy) / cap_y if cap_y > 0 else 0.0
    rz_ = abs(Vz) / cap_z if cap_z > 0 else 0.0
    return max(ry_, rz_)


# ── 4. 조합 (P-M 상관) ──────────────────────────────────────
def check_combined(section: SHSSection, P: float, Mz: float, My: float,
                   L: float, K: float = 1.0) -> float:
    """KBC H1.1 P-M 조합 상관식.

    Pr/Pc + (8/9)(Mrz/Mcz + Mry/Mcy) ≤ 1.0   (Pr/Pc ≥ 0.2)
    Pr/(2Pc) + (Mrz/Mcz + Mry/Mcy) ≤ 1.0     (Pr/Pc < 0.2)

    Pc = φc·Pn (압축) 또는 φt·Pn (인장)
    Mcz = φb·Fy·Sy, Mcy = φb·Fy·Sz
    """
    # 축력 capacity (Pc)
    if P < 0:
        # 압축: 좌굴 capacity
        r = min(section.ry, section.rz)
        slenderness = (K * L) / r
        if slenderness < 1e-6:
            Fcr = STEEL_FY_MPA
        else:
            Fe = math.pi ** 2 * STEEL_E_MPA / (slenderness ** 2)
            lambda_c_sq = STEEL_FY_MPA / Fe
            lambda_c = math.sqrt(lambda_c_sq)
            if lambda_c <= 1.5:
                Fcr = (0.658 ** lambda_c_sq) * STEEL_FY_MPA
            else:
                Fcr = (0.877 / lambda_c_sq) * STEEL_FY_MPA
        Pc = PHI_C * Fcr * section.A
    else:
        Pc = PHI_T * STEEL_FY_MPA * section.A

    Pr_over_Pc = abs(P) / Pc if Pc > 0 else 0.0

    Mcz = PHI_B * STEEL_FY_MPA * section.Sy
    Mcy = PHI_B * STEEL_FY_MPA * section.Sz
    Mrz_over_Mcz = abs(Mz) / Mcz if Mcz > 0 else 0.0
    Mry_over_Mcy = abs(My) / Mcy if Mcy > 0 else 0.0

    moment_term = Mrz_over_Mcz + Mry_over_Mcy

    if Pr_over_Pc >= 0.2:
        return Pr_over_Pc + (8.0 / 9.0) * moment_term
    else:
        return Pr_over_Pc / 2.0 + moment_term


# ── 5. 처짐 (사용성) ────────────────────────────────────────
def estimate_deflection(section: SHSSection, Mz: float, L: float,
                        kind: str) -> float:
    """강축 모멘트로부터 단면 의존 처짐 추정.

    하중 패턴 가정:
      - 일반 보(균일분포하중): δ ≈ (5/48) · |Mmax| · L² / (E · Iy)
      - 캔틸레버(분포하중)   : δ ≈ (1/4)  · |Mmax| · L² / (E · Iy)

    단면이 바뀌면 Iy 가 바뀌므로 처짐도 자동 갱신된다.
    기둥은 처짐 무관 → 0 반환.
    """
    if kind == 'column' or L <= 0 or section.Iy <= 0:
        return 0.0
    if kind == 'cantilever':
        coeff = 1.0 / 4.0
    else:
        coeff = 5.0 / 48.0
    return coeff * abs(Mz) * (L ** 2) / (STEEL_E_MPA * section.Iy)


def check_deflection(section: SHSSection, Mz: float, L: float, kind: str) -> float:
    """처짐 검토 — 단면 의존 추정 처짐을 한계와 비교.

    일반 보   : limit = L / 360
    캔틸레버  : limit = L / 180
    기둥      : 0
    """
    if kind == 'column' or L <= 0:
        return 0.0
    if kind == 'cantilever':
        limit = L / DEFL_LIMIT_CANTILEVER_DENOM
    else:
        limit = L / DEFL_LIMIT_BEAM_DENOM
    if limit <= 0:
        return 0.0
    delta = estimate_deflection(section, Mz, L, kind)
    return delta / limit


# ── 통합 검토 ───────────────────────────────────────────────
def check_member(section: SHSSection, demand: MemberDemand) -> CheckResult:
    """5개 항목 전부 계산 → CheckResult 반환."""
    ra = check_axial(section, demand.P, demand.L, demand.K)
    rb = check_bending(section, demand.Mz, demand.My)
    rs = check_shear(section, demand.Vy, demand.Vz)
    rc = check_combined(section, demand.P, demand.Mz, demand.My,
                        demand.L, demand.K)
    rd = check_deflection(section, demand.Mz, demand.L, demand.kind)
    return CheckResult.from_ratios(ra, rb, rs, rc, rd)


__all__ = [
    'MemberDemand', 'CheckResult',
    'check_axial', 'check_bending', 'check_shear',
    'check_combined', 'check_deflection',
    'estimate_deflection', 'check_member',
    'DEFL_LIMIT_BEAM_DENOM', 'DEFL_LIMIT_CANTILEVER_DENOM',
]
