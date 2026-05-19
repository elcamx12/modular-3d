"""
PPO 학습 실행 — 개선판 (CNN + 50K 에피소드)

python train_ppo.py
"""

import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpl_patches
from pathlib import Path

from placement_env import PlacementEnv, N_CHANNELS, GRID_ROWS, GRID_COLS, N_ACTIONS
from ppo_simple import PolicyValueCNN, PPOTrainer
from placer import run_best, PlacementEngine, get_bay_ranges

# ============================================================
# 설정
# ============================================================
N_EPISODES   = 50000
N_VEC_ENVS   = 32
LOG_EVERY    = 200
N_COMPARE    = 6


def main():
    base = Path(__file__).parent

    with open(base / 'outlines_output' / 'outlines.json', 'r') as f:
        outlines = json.load(f)
    with open(base / 'catalogs.json', 'r') as f:
        catalogs = json.load(f)

    print(f'외곽선: {len(outlines)}개, 카탈로그: {len(catalogs)}개')

    # 환경 & CNN 네트워크
    env = PlacementEnv(outlines, catalogs)
    network = PolicyValueCNN(
        n_rows=GRID_ROWS,
        n_cols=GRID_COLS,
        n_channels=N_CHANNELS,
        n_actions=N_ACTIONS,
    )
    print(f'파라미터: {sum(p.numel() for p in network.parameters()):,}')

    # 학습
    trainer = PPOTrainer(env, network, n_vec_envs=N_VEC_ENVS)
    best_score, all_scores = trainer.train(
        n_episodes=N_EPISODES, log_every=LOG_EVERY)

    trainer.save_model(base / 'ppo_model.pt')
    print(f'[saved] ppo_model.pt')

    plot_training_curve(all_scores, base / 'ppo_training_curve.png')
    compare_ppo_vs_greedy(env, network, outlines, catalogs,
                          base / 'ppo_vs_greedy.png', n=N_COMPARE)


# ============================================================
# 학습 곡선
# ============================================================
def plot_training_curve(scores, out_path):
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(scores, alpha=0.2, color='blue', label='episode score')

    window = min(100, len(scores) // 10)
    if window > 1:
        ma = np.convolve(scores, np.ones(window) / window, mode='valid')
        ax.plot(range(window - 1, len(scores)), ma,
                color='red', linewidth=2, label=f'MA-{window}')

    ax.set_xlabel('Episode')
    ax.set_ylabel('Score')
    ax.set_title(f'PPO Training Curve ({len(scores)} episodes)')
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    print(f'[saved] {out_path}')


# ============================================================
# PPO vs 그리디 비교
# ============================================================
def run_ppo_episode(env, network, outline, catalog):
    """학습된 PPO로 greedy 정책 실행."""
    env.outline = outline
    env.catalog = catalog
    env.eng = PlacementEngine(outline['coords'])
    env.bay_ranges = get_bay_ranges(outline)
    env.n_bays = len(env.bay_ranges)

    all_x = [c[0] for c in outline['coords']]
    all_y = [c[1] for c in outline['coords']]
    env.origin_x = min(all_x)
    env.origin_y = min(all_y)

    mods = sorted(catalog['horizontal_modules'], reverse=True)
    env.elem_heights = {
        0: mods[0] if len(mods) > 0 else 0,
        1: mods[1] if len(mods) > 1 else 0,
        2: catalog['panel1_length'],
        3: 3.3,
        4: catalog['cantilever_ext'],
    }

    env.occupied_grid[:] = 0
    env.type_grid[:] = 0
    env._build_outline_grid()
    env.prev_score = 0.0

    state = env.get_state()
    done = False
    while not done:
        valid = env.get_valid_mask()
        if not valid.any():
            break
        probs, _ = network.predict(state)
        probs = probs * valid
        ps = probs.sum()
        if ps > 0:
            probs /= ps
        else:
            break
        action = np.argmax(probs)
        state, done = env.step(action)

    return env.eng


def compare_ppo_vs_greedy(env, network, outlines, catalogs, out_path, n=6):
    fig, axes = plt.subplots(n, 2, figsize=(14, n * 4))
    if n == 1:
        axes = axes.reshape(1, 2)

    indices = np.random.choice(len(outlines), size=n, replace=False)
    ppo_wins = 0

    for row, oi in enumerate(indices):
        ol = outlines[oi]
        cat = catalogs[0]

        eng_greedy = run_best(ol, cat)
        eng_ppo = run_ppo_episode(env, network, ol, cat)

        cov_g = eng_greedy.coverage()
        cov_p = eng_ppo.coverage()
        if cov_p >= cov_g:
            ppo_wins += 1

        for col, (eng, title) in enumerate([
            (eng_greedy, f'Greedy {cov_g*100:.1f}%'),
            (eng_ppo,    f'PPO {cov_p*100:.1f}%'),
        ]):
            ax = axes[row, col]
            xs = [c[0] for c in eng.coords]
            ys = [c[1] for c in eng.coords]
            ax.plot(xs, ys, 'k-', linewidth=1.5)
            ax.fill(xs, ys, alpha=0.08, color='gray')

            for label, color, x, y, w, h in eng.placements:
                rect = mpl_patches.Rectangle(
                    (x, y), w, h, linewidth=0.5,
                    edgecolor='black', facecolor=color, alpha=0.6)
                ax.add_patch(rect)

            ax.set_aspect('equal')
            ax.set_title(f'Outline #{oi} — {title}', fontsize=10)
            ax.grid(True, alpha=0.2)

    plt.suptitle(f'PPO vs Greedy — PPO wins {ppo_wins}/{n}',
                 fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f'[saved] {out_path}')
    print(f'PPO wins: {ppo_wins}/{n}')


if __name__ == '__main__':
    main()
