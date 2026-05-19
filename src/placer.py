"""
배치 알고리즘  (Step 3b) — 컬럼 기반

핵심 아이디어:
  외곽선은 베이(3.3m 폭 컬럼)로 구성됨.
  각 베이 안에서 세로(y 방향)로 부재를 쌓음:
    모듈(L×3.3) → 패널(pL×3.3) → 캔틸레버(cant×3.3)

  ┌─bay0──┬─bay1──┬─bay2──┐
  │Module │Module │Panel  │
  │ L×3.3 │ L×3.3 │pL×3.3│
  │       │       │       │
  │Panel  │Panel  │T3     │
  │pL×3.3 │pL×3.3 │3.3×3.3│
  │       │       │       │
  │Cant   │Cant   │Cant   │
  └───────┴───────┴───────┘

캔틸레버 규칙: 모듈/패널의 단변(상/하)에서만 뻗어나감
"""

import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpl_patches
from matplotlib.path import Path as MplPath
from pathlib import Path
from itertools import product

RES       = 0.1
MOD_WIDTH = 3.3


# ============================================================
# 그리드 래스터화
# ============================================================
def rasterize(coords, res=RES):
    path = MplPath(coords)
    xs_all = [c[0] for c in coords]
    ys_all = [c[1] for c in coords]
    minx, miny = min(xs_all), min(ys_all)
    maxx, maxy = max(xs_all), max(ys_all)
    nx = int(np.ceil((maxx - minx) / res))
    ny = int(np.ceil((maxy - miny) / res))
    xs = np.arange(nx) * res + minx + res / 2
    ys = np.arange(ny) * res + miny + res / 2
    xx, yy = np.meshgrid(xs, ys)
    pts = np.column_stack([xx.ravel(), yy.ravel()])
    inside = path.contains_points(pts).reshape(ny, nx)
    return inside, minx, miny, nx, ny


class PlacementEngine:
    def __init__(self, outline_coords):
        self.coords = outline_coords
        self.grid, self.ox, self.oy, self.nx, self.ny = rasterize(outline_coords)
        self.outline_cells = int(self.grid.sum())
        self.placements = []

    def _g(self, x, y, w, h):
        return (int(round((x - self.ox) / RES)),
                int(round((y - self.oy) / RES)),
                int(round(w / RES)),
                int(round(h / RES)))

    def can_place(self, x, y, w, h):
        ix, iy, gw, gh = self._g(x, y, w, h)
        if ix < 0 or iy < 0 or ix + gw > self.nx or iy + gh > self.ny:
            return False
        region = self.grid[iy:iy+gh, ix:ix+gw]
        return region.shape == (gh, gw) and region.all()

    def place(self, label, color, x, y, w, h):
        ix, iy, gw, gh = self._g(x, y, w, h)
        self.grid[iy:iy+gh, ix:ix+gw] = False
        self.placements.append((label, color, x, y, w, h))

    def try_place(self, label, color, x, y, w, h):
        if self.can_place(x, y, w, h):
            self.place(label, color, x, y, w, h)
            return True
        return False

    def coverage(self):
        filled = self.outline_cells - int(self.grid.sum())
        return filled / self.outline_cells if self.outline_cells > 0 else 0


# ============================================================
# 베이 정보 추출
# ============================================================
def get_bay_ranges(outline):
    """외곽선 데이터에서 각 베이의 (x_start, y_min, y_max) 추출"""
    bays_data = outline['bays']
    bay_ranges = []
    for i, b in enumerate(bays_data):
        x = i * MOD_WIDTH
        y_min = -b['front']
        y_max = b['depth'] - b['front']
        bay_ranges.append((x, y_min, y_max))
    return bay_ranges


# ============================================================
# 한 베이 안에서 세로로 채우기
# ============================================================
def fill_bay(eng, x, y_start, y_end, elements, direction='up'):
    """
    한 베이(3.3m 폭) 안에서 y 방향으로 부재를 차례로 쌓기.
    elements: [(label, color, height), ...] 순서대로 시도
    direction: 'up' = y_start부터 위로, 'down' = y_end부터 아래로
    """
    if direction == 'up':
        y = y_start
        for label, color, h in elements:
            while y + h <= y_end + 0.05:
                if eng.try_place(label, color, x, y, MOD_WIDTH, h):
                    y += h
                else:
                    break
    else:
        y = y_end
        for label, color, h in elements:
            while y - h >= y_start - 0.05:
                if eng.try_place(label, color, x, y - h, MOD_WIDTH, h):
                    y -= h
                else:
                    break


# ============================================================
# 캔틸레버: 모듈/패널의 상/하 단변에서만 뻗기
# ============================================================
def place_cantilevers(eng, cant_ext):
    base_elements = [(x, y, w, h)
                     for label, _, x, y, w, h in eng.placements
                     if not label.startswith('Cant')]

    for ex, ey, ew, eh in base_elements:
        # 상단에서 위로 뻗기 (3.3m 폭 × cant 높이)
        eng.try_place('Cant', '#FFD54F', ex, ey + eh, ew, cant_ext)
        # 하단에서 아래로 뻗기
        eng.try_place('Cant', '#FFD54F', ex, ey - cant_ext, ew, cant_ext)


# ============================================================
# 한 구성(fill order + direction)으로 전체 배치
# ============================================================
def run_one_config(outline, catalog, fill_order, direction):
    """
    fill_order: 각 베이마다 채울 부재 순서
      예: [('Mod', L), ('Panel', pL), ('T3', 3.3)]
    direction: 'up' 또는 'down'
    """
    eng = PlacementEngine(outline['coords'])
    bay_ranges = get_bay_ranges(outline)
    cant_ext = catalog['cantilever_ext']

    for x, y_min, y_max in bay_ranges:
        fill_bay(eng, x, y_min, y_max, fill_order, direction)

    # 캔틸레버 (모듈/패널에서만)
    place_cantilevers(eng, cant_ext)

    return eng


# ============================================================
# 여러 구성 시도 → 최고 커버리지 선택
# ============================================================
def run_best(outline, catalog):
    mod_Ls = sorted(catalog['horizontal_modules'], reverse=True)
    pL = catalog['panel1_length']
    cant = catalog['cantilever_ext']

    # 채울 부재 순서 후보들
    fill_orders = []

    # 모듈(큰것) → 모듈(작은것) → 패널 → T3
    order_mod_first = []
    for L in mod_Ls:
        order_mod_first.append((f'Mod {L:.1f}', '#4C9AFF', L))
    order_mod_first.append(('Panel', '#BA68C8', pL))
    order_mod_first.append(('T3', '#66BB6A', MOD_WIDTH))
    fill_orders.append(order_mod_first)

    # 모듈(큰것) → T3 → 패널
    order_mod_t3 = []
    for L in mod_Ls:
        order_mod_t3.append((f'Mod {L:.1f}', '#4C9AFF', L))
    order_mod_t3.append(('T3', '#66BB6A', MOD_WIDTH))
    order_mod_t3.append(('Panel', '#BA68C8', pL))
    fill_orders.append(order_mod_t3)

    # 패널 → 모듈(큰것) → T3
    order_panel_first = [('Panel', '#BA68C8', pL)]
    for L in mod_Ls:
        order_panel_first.append((f'Mod {L:.1f}', '#4C9AFF', L))
    order_panel_first.append(('T3', '#66BB6A', MOD_WIDTH))
    fill_orders.append(order_panel_first)

    # 모듈(작은것) → 패널 → 모듈(큰것) → T3
    if len(mod_Ls) >= 2:
        order_alt = [
            (f'Mod {mod_Ls[-1]:.1f}', '#4C9AFF', mod_Ls[-1]),
            ('Panel', '#BA68C8', pL),
            (f'Mod {mod_Ls[0]:.1f}', '#4C9AFF', mod_Ls[0]),
            ('T3', '#66BB6A', MOD_WIDTH),
        ]
        fill_orders.append(order_alt)

    best = None
    best_info = ''

    for i, order in enumerate(fill_orders):
        for d in ['up', 'down']:
            eng = run_one_config(outline, catalog, order, d)
            info = f'order#{i} dir={d}'
            if best is None or eng.coverage() > best.coverage():
                best = eng
                best_info = info

    print(f'  Best config: {best_info}')
    return best


# ============================================================
# 시각화
# ============================================================
def visualize(eng, out_path):
    fig, ax = plt.subplots(figsize=(10, 8))

    xs = [c[0] for c in eng.coords]
    ys = [c[1] for c in eng.coords]
    ax.plot(xs, ys, 'k-', linewidth=2.5)
    ax.fill(xs, ys, alpha=0.08, color='gray')

    for label, color, x, y, w, h in eng.placements:
        rect = mpl_patches.Rectangle(
            (x, y), w, h, linewidth=0.8,
            edgecolor='black', facecolor=color, alpha=0.6,
        )
        ax.add_patch(rect)
        cx, cy = x + w / 2, y + h / 2
        fs = max(4, min(7, int(min(w, h) * 1.5)))
        ax.text(cx, cy, label, ha='center', va='center',
                fontsize=fs, fontweight='bold')

    legend_items = [
        ('Module',       '#4C9AFF'),
        ('T3',           '#66BB6A'),
        ('Floor Panel',  '#BA68C8'),
        ('Cantilever',   '#FFD54F'),
    ]
    handles = [mpl_patches.Patch(facecolor=c, edgecolor='black', label=l)
               for l, c in legend_items]
    ax.legend(handles=handles, loc='upper right', fontsize=8)

    cov = eng.coverage()
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3, linestyle=':')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')
    ax.set_title(f'Coverage {cov*100:.1f}%  ({len(eng.placements)} elements)')
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f'[saved] {out_path}')


# ============================================================
# 메인
# ============================================================
if __name__ == '__main__':
    base = Path(__file__).parent

    with open(base / 'outlines_output' / 'outlines.json', 'r') as f:
        outlines = json.load(f)
    with open(base / 'catalogs.json', 'r') as f:
        catalogs = json.load(f)

    # 여러 외곽선 × 카탈로그 조합 테스트
    n_test = min(3, len(outlines))
    for oi in range(n_test):
        ol = outlines[oi]
        cat = catalogs[0]

        print(f'\n=== Outline #{ol["id"]}, area={ol["area"]} m2, '
              f'{ol["n_bays"]} bays ===')
        print(f'  Catalog #{cat["id"]}: mods={cat["horizontal_modules"]}, '
              f'cant={cat["cantilever_ext"]:.2f}, '
              f'panelL={cat["panel1_length"]:.2f}')

        # 베이별 정보
        for i, b in enumerate(ol['bays']):
            y0 = -b['front']
            y1 = b['depth'] - b['front']
            print(f'  Bay {i}: x={i*3.3:.1f}~{(i+1)*3.3:.1f}, '
                  f'y={y0:.2f}~{y1:.2f}, depth={b["depth"]:.2f}')

        eng = run_best(ol, cat)

        cov = eng.coverage()
        counts = {}
        for label, *_ in eng.placements:
            key = label.split()[0]
            counts[key] = counts.get(key, 0) + 1
        print(f'  Coverage: {cov*100:.1f}%')
        print(f'  Breakdown: {counts}')

        suffix = f'_outline{oi}' if n_test > 1 else ''
        visualize(eng, base / f'placement_preview{suffix}.png')
