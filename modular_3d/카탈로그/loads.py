"""하중·하중조합·압력 상수.

[단위] N, mm, MPa = N/mm². 압력은 N/mm².
"""

# ── 활하중 (KBC 주거) ─────────────────────────────────
LIVE_LOAD_KN_M2 = 2.0                       # 주거 활하중 2.0 kN/m²
LIVE_LOAD_N_MM2 = LIVE_LOAD_KN_M2 * 1e-3    # 2.0e-3 N/mm²

# ── LRFD 하중조합 계수 ──────────────────────────────
LOAD_COMBO_DL = 1.2   # 사하중 계수
LOAD_COMBO_LL = 1.6   # 활하중 계수

# ── 슬래브 1방향 / 2방향 판정 ────────────────────────
SLAB_ONE_WAY_RATIO = 2.0   # 장변/단변 ≥ 2.0 → 1방향


__all__ = [
    'LIVE_LOAD_KN_M2', 'LIVE_LOAD_N_MM2',
    'LOAD_COMBO_DL', 'LOAD_COMBO_LL',
    'SLAB_ONE_WAY_RATIO',
]
