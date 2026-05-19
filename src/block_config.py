"""
블록 설정 & 카탈로그→블록 변환

블록 크기: 0.3m (= 3.3m / 11)
- 3.3m = 11블록 (정확)
- 3.0m = 10블록 (정확)
- 3.5m ≈ 12블록 (3.6m, +0.1m)
"""

import json
import numpy as np
from pathlib import Path
from matplotlib.path import Path as MplPath

# ── 블록 상수 ────────────────────────────────────────────────
BLOCK_M = 0.3              # 블록 크기 (m)
MOD_SHORT_BLOCKS = 11      # 모듈 단변 3.3m = 11블록

# ── 치수 → 블록 변환 ─────────────────────────────────────────

def m_to_blocks(meters):
    """미터 → 블록 수 (반올림)."""
    return max(1, round(meters / BLOCK_M))


def blocks_to_m(blocks):
    """블록 수 → 미터."""
    return blocks * BLOCK_M


# ── 카탈로그 → 기본 부재 목록 ─────────────────────────────────

def catalog_to_piece_types(catalog):
    """
    카탈로그 → 기본 부재 리스트.
    각 원소: (width, height, name)
    ※ 캔틸레버는 배치 후 규칙 기반으로 추가
    ※ PnlT2/T3는 Mod1/2와 동일 치수 → 후처리에서 변환
    """
    mods = catalog['horizontal_modules']
    p1_len = catalog['panel1_length']
    p1_widths = catalog['panel1_widths']
    short = MOD_SHORT_BLOCKS  # 11블록 = 3.3m

    pieces = []

    m1_len = m_to_blocks(mods[0])
    pieces.append((short, m1_len, 'Mod1'))

    if len(mods) >= 2:
        m2_len = m_to_blocks(mods[1])
        pieces.append((short, m2_len, 'Mod2'))

    pieces.append((short, short, 'T3'))

    p1a_len = m_to_blocks(p1_len)
    p1a_w = m_to_blocks(p1_widths[0])
    pieces.append((p1a_w, p1a_len, 'PnlA'))

    p1b_w = m_to_blocks(p1_widths[1])
    pieces.append((p1b_w, p1a_len, 'PnlB'))

    return pieces


# ── 캔틸레버 옵션 ────────────────────────────────────────────
CANT_OPTIONS_M = [0.5, 1.0, 1.5, 2.0]
CANT_OPTIONS_BLOCKS = [m_to_blocks(m) for m in CANT_OPTIONS_M]


# ── 외곽선 → 블록 마스크 ──────────────────────────────────────

def outline_to_block_mask(coords_m, padding=2):
    """
    외곽선 좌표(m) → 블록 단위 마스크.

    Returns:
        mask: (n_rows, n_cols) bool 배열 — True=외곽선 내부
        origin_m: (ox, oy) 블록 그리드 원점 (미터)
    """
    coords = np.array(coords_m)
    xmin, ymin = coords.min(axis=0)
    xmax, ymax = coords.max(axis=0)

    ox = xmin - padding * BLOCK_M
    oy = ymin - padding * BLOCK_M

    n_cols = int(np.ceil((xmax - ox) / BLOCK_M)) + padding
    n_rows = int(np.ceil((ymax - oy) / BLOCK_M)) + padding

    path = MplPath(coords_m)
    mask = np.zeros((n_rows, n_cols), dtype=bool)

    centers = []
    indices = []
    for r in range(n_rows):
        for c in range(n_cols):
            cx = ox + (c + 0.5) * BLOCK_M
            cy = oy + (r + 0.5) * BLOCK_M
            centers.append([cx, cy])
            indices.append((r, c))

    if centers:
        inside = path.contains_points(centers)
        for k, (r, c) in enumerate(indices):
            mask[r, c] = inside[k]

    return mask, (ox, oy)


def outline_to_block_mask_symmetric(coords_m):
    """외곽선 좌우대칭 → 2세대 마스크 (세대끼리 맞붙음, 패딩 없음)."""
    mask_raw, origin_m = outline_to_block_mask(coords_m, padding=0)

    # 오른쪽 빈 열 제거 (외곽선이 닿지 않는 부분)
    col_has_content = mask_raw.any(axis=0)
    if col_has_content.any():
        rightmost = np.where(col_has_content)[0][-1]
        mask_trimmed = mask_raw[:, :rightmost + 1]
    else:
        mask_trimmed = mask_raw

    # 미러 → 맞붙이기
    mask_mirror = np.fliplr(mask_trimmed)
    mask_2unit = np.concatenate([mask_trimmed, mask_mirror], axis=1)

    return mask_2unit, origin_m


# ── 로드 유틸 ─────────────────────────────────────────────────

def load_outlines(path=None):
    if path is None:
        path = Path(__file__).parent / 'extracted_outlines' / 'outlines_84_final.json'
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_catalogs(path=None):
    if path is None:
        path = Path(__file__).parent / 'catalogs.json'
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)
