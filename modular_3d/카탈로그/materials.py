"""재료 상수 — 강재(SS275), 콘크리트(보통 강도).

[단위] N, mm, MPa = N/mm². 단위 무게는 N/mm³ (1e-9 환산).
"""
import math

# ── 중력 ─────────────────────────────────────────────────
G_M_PER_S2 = 9.80665   # 표준 중력 m/s²

# ── 강재 (각형강관 SS275) ───────────────────────────────
STEEL_E_MPA = 205000.0   # 탄성계수 (N/mm²)
STEEL_NU = 0.3           # 푸아송비
STEEL_G_MPA = STEEL_E_MPA / (2.0 * (1.0 + STEEL_NU))  # 전단탄성계수
STEEL_DENSITY_KG_M3 = 7850.0
STEEL_UNIT_WEIGHT_N_M3 = STEEL_DENSITY_KG_M3 * G_M_PER_S2          # ≈ 76988 N/m³
STEEL_UNIT_WEIGHT_N_MM3 = STEEL_UNIT_WEIGHT_N_M3 * 1e-9            # ≈ 7.70e-5 N/mm³

# 강재 재질 (SS275 — 항복강도·인장강도)
STEEL_FY_MPA = 275.0   # 항복강도
STEEL_FU_MPA = 410.0   # 인장강도

# 부분안전계수 (KDS 41 31 00 강구조 LRFD)
PHI_T = 0.9   # 인장
PHI_C = 0.9   # 압축
PHI_B = 0.9   # 휨
PHI_V = 0.9   # 전단

# ── 콘크리트 (PC 슬래브 / RC 코어) ──────────────────────
CONCRETE_E_MPA = 27000.0   # 약 27 GPa (보통 강도 가정)
CONCRETE_NU = 0.2          # 푸아송비 (RC 표준)
CONCRETE_G_MPA = CONCRETE_E_MPA / (2.0 * (1.0 + CONCRETE_NU))
CONCRETE_DENSITY_KG_M3 = 2400.0
CONCRETE_UNIT_WEIGHT_N_M3 = CONCRETE_DENSITY_KG_M3 * G_M_PER_S2    # ≈ 23536 N/m³
CONCRETE_UNIT_WEIGHT_N_MM3 = CONCRETE_UNIT_WEIGHT_N_M3 * 1e-9      # ≈ 2.35e-5 N/mm³

# RC 코어벽 약식 별칭 (옛 코드 호환)
RC_WALL_E_MPA = CONCRETE_E_MPA
RC_WALL_G_MPA = CONCRETE_E_MPA / 2.4   # 옛 약식 — 정확한 G 는 CONCRETE_G_MPA 사용


__all__ = [
    'G_M_PER_S2',
    'STEEL_E_MPA', 'STEEL_NU', 'STEEL_G_MPA',
    'STEEL_DENSITY_KG_M3',
    'STEEL_UNIT_WEIGHT_N_M3', 'STEEL_UNIT_WEIGHT_N_MM3',
    'STEEL_FY_MPA', 'STEEL_FU_MPA',
    'PHI_T', 'PHI_C', 'PHI_B', 'PHI_V',
    'CONCRETE_E_MPA', 'CONCRETE_NU', 'CONCRETE_G_MPA',
    'CONCRETE_DENSITY_KG_M3',
    'CONCRETE_UNIT_WEIGHT_N_M3', 'CONCRETE_UNIT_WEIGHT_N_MM3',
    'RC_WALL_E_MPA', 'RC_WALL_G_MPA',
]
