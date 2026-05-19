"""
전체 실행: 1000 평면 × 30 카탈로그 = 30,000 배치

각 평면마다:
  - 30개 카탈로그 전부 적용
  - 각 카탈로그별 최고 커버리지 배치 저장
  - 30개 중 최적(커버리지 최고) 카탈로그 기록

출력:
  results/
    all_results.json      — 30,000개 전체 결과 요약
    best_per_outline.json — 평면별 최적 카탈로그 & 커버리지
    coverage_matrix.npy   — (1000×30) 커버리지 행렬
    coverage_hist.png     — 최적 커버리지 분포
    top36_grid.png        — 상위 36개 배치 시각화
"""

import json
import time
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpl_patches
from pathlib import Path

from placer import PlacementEngine, get_bay_ranges, fill_bay, place_cantilevers, run_best


def main():
    base = Path(__file__).parent
    out_dir = base / 'results'
    out_dir.mkdir(exist_ok=True)

    # 데이터 로드
    with open(base / 'outlines_output' / 'outlines.json', 'r') as f:
        outlines = json.load(f)
    with open(base / 'catalogs.json', 'r') as f:
        catalogs = json.load(f)

    n_outlines = len(outlines)
    n_catalogs = len(catalogs)
    total = n_outlines * n_catalogs

    print(f'=== 전체 실행 시작 ===')
    print(f'  평면: {n_outlines}개')
    print(f'  카탈로그: {n_catalogs}개')
    print(f'  총 배치: {total}개')
    print()

    # 결과 저장용
    coverage_matrix = np.zeros((n_outlines, n_catalogs))
    all_results = []
    best_per_outline = []

    # 상위 배치 저장 (시각화용)
    top_placements = []  # (coverage, outline_idx, catalog_idx, engine)

    t0 = time.time()

    for oi, ol in enumerate(outlines):
        best_cov = -1
        best_cat_idx = -1
        best_eng = None

        for ci, cat in enumerate(catalogs):
            eng = run_best(ol, cat)
            cov = eng.coverage()
            coverage_matrix[oi, ci] = cov

            all_results.append({
                'outline_id': ol['id'],
                'catalog_id': cat['id'],
                'coverage': round(cov, 4),
                'n_elements': len(eng.placements),
            })

            if cov > best_cov:
                best_cov = cov
                best_cat_idx = ci
                best_eng = eng

        best_per_outline.append({
            'outline_id': ol['id'],
            'outline_area': ol['area'],
            'best_catalog_id': best_cat_idx,
            'best_coverage': round(best_cov, 4),
            'n_elements': len(best_eng.placements),
        })

        # 상위 배치 저장 (시각화용, 상위 36개 유지)
        top_placements.append((best_cov, oi, best_cat_idx, best_eng))
        top_placements.sort(key=lambda x: -x[0])
        if len(top_placements) > 36:
            top_placements = top_placements[:36]

        # 진행 상황 출력
        elapsed = time.time() - t0
        done = (oi + 1) * n_catalogs
        speed = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / speed if speed > 0 else 0
        print(f'\r  [{oi+1}/{n_outlines}] '
              f'best_cov={best_cov*100:.1f}% (cat#{best_cat_idx}) '
              f'| {speed:.0f} 배치/초 | ETA {eta:.0f}초',
              end='', flush=True)

    elapsed_total = time.time() - t0
    print(f'\n\n=== 완료: {elapsed_total:.1f}초 ({total}개 배치) ===\n')

    # ============================================================
    # 결과 저장
    # ============================================================

    # 1. 전체 결과
    with open(out_dir / 'all_results.json', 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=1)
    print(f'[saved] all_results.json ({len(all_results)}개)')

    # 2. 평면별 최적
    with open(out_dir / 'best_per_outline.json', 'w', encoding='utf-8') as f:
        json.dump(best_per_outline, f, ensure_ascii=False, indent=2)
    print(f'[saved] best_per_outline.json ({len(best_per_outline)}개)')

    # 3. 커버리지 행렬
    np.save(out_dir / 'coverage_matrix.npy', coverage_matrix)
    print(f'[saved] coverage_matrix.npy ({coverage_matrix.shape})')

    # 4. 통계 출력
    best_covs = [b['best_coverage'] for b in best_per_outline]
    print(f'\n--- 커버리지 통계 (평면별 최적) ---')
    print(f'  평균: {np.mean(best_covs)*100:.1f}%')
    print(f'  최소: {np.min(best_covs)*100:.1f}%')
    print(f'  최대: {np.max(best_covs)*100:.1f}%')
    print(f'  중앙값: {np.median(best_covs)*100:.1f}%')
    print(f'  90%이상: {sum(1 for c in best_covs if c >= 0.9)}개 '
          f'({sum(1 for c in best_covs if c >= 0.9)/len(best_covs)*100:.0f}%)')

    # 5. 커버리지 히스토그램
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist([c * 100 for c in best_covs], bins=30, edgecolor='black', alpha=0.7)
    ax.axvline(np.mean(best_covs) * 100, color='red', linestyle='--',
               label=f'mean {np.mean(best_covs)*100:.1f}%')
    ax.set_xlabel('Best Coverage (%)')
    ax.set_ylabel('Count')
    ax.set_title(f'Best Coverage Distribution ({n_outlines} outlines × {n_catalogs} catalogs)')
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / 'coverage_hist.png', dpi=120)
    plt.close()
    print(f'[saved] coverage_hist.png')

    # 6. 상위 36개 배치 격자 시각화
    plot_top_grid(top_placements, outlines, catalogs, out_dir / 'top36_grid.png')


def plot_top_grid(top_placements, outlines, catalogs, out_path):
    n = min(len(top_placements), 36)
    if n == 0:
        return
    cols = 6
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3, rows * 3))
    axes = np.atleast_1d(axes).flatten()

    for i in range(n):
        cov, oi, ci, eng = top_placements[i]
        ax = axes[i]

        xs = [c[0] for c in eng.coords]
        ys = [c[1] for c in eng.coords]
        ax.plot(xs, ys, 'k-', linewidth=1.5)
        ax.fill(xs, ys, alpha=0.08, color='gray')

        for label, color, x, y, w, h in eng.placements:
            rect = mpl_patches.Rectangle(
                (x, y), w, h, linewidth=0.5,
                edgecolor='black', facecolor=color, alpha=0.6,
            )
            ax.add_patch(rect)

        ax.set_aspect('equal')
        ax.set_title(f'#{oi} cat{ci}\n{cov*100:.1f}%', fontsize=8)
        ax.tick_params(labelsize=5)

    for j in range(n, len(axes)):
        axes[j].axis('off')

    plt.suptitle('Top 36 Placements by Coverage', fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f'[saved] {out_path}')


if __name__ == '__main__':
    main()
