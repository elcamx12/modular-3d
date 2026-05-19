"""
PPO 블록 배치 학습 — 위치 자동결정 + 기본 부재 + 좌우대칭 2세대

배치 순서: 왼쪽 아래 → 위로 쭉 → 오른쪽 이동 → 다시 아래→위
PPO는 "어떤 기본 부재를 놓을지"만 선택 (~10 액션).
후처리:
  1. 캔틸레버: 각 부재의 장변 양 끝에 빈 공간이 있으면 규칙 기반 배치
  2. Mod→Pnl: Mod1/2가 장변 양쪽 지지되면 PnlT2/T3로 변환
"""

import random
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpl_patches
from pathlib import Path

from block_config import (
    BLOCK_M, MOD_SHORT_BLOCKS, blocks_to_m,
    CANT_OPTIONS_BLOCKS,
    load_outlines, load_catalogs,
    catalog_to_piece_types, outline_to_block_mask_symmetric,
)
from network import PolicyValueNet
from ppo_trainer import PPOTrainer

# ── 설정 ─────────────────────────────────────────────────────
N_EPISODES = 30000
N_VEC_ENVS = 32
LOG_EVERY = 200
CATALOG_IDX = 0
OUTLINE_IDX = 0  # 단일 외곽선 사용 (-1이면 전체)


class MultiOutlineEnv:
    """위치 자동결정 + 기본 부재 + 2세대 PPO 환경."""

    def __init__(self, outlines, catalog):
        self.outlines = outlines
        self.catalog = catalog
        self.piece_types = catalog_to_piece_types(catalog)

        # 좌우대칭 2세대 마스크 사전계산
        self.envs_cache = {}
        max_rows, max_cols = 0, 0
        for ol in outlines:
            mask, origin = outline_to_block_mask_symmetric(ol['coords_m'])
            r, c = mask.shape
            max_rows = max(max_rows, r)
            max_cols = max(max_cols, c)
            self.envs_cache[ol['id']] = (mask, origin)

        self.n_rows = max_rows
        self.n_cols = max_cols
        self.n_channels = 3

        # 액션 = (pt_idx, rot) — 기본 부재만
        self.all_actions = []
        for pt_idx, (pw, ph, name) in enumerate(self.piece_types):
            self.all_actions.append((pt_idx, 0))
            if pw != ph:
                self.all_actions.append((pt_idx, 1))
        self.n_actions = len(self.all_actions)

        # 고유 (w,h) 쌍
        wh_set = set()
        for pt_idx, rot in self.all_actions:
            pw, ph, _ = self.piece_types[pt_idx]
            w, h = (pw, ph) if rot == 0 else (ph, pw)
            wh_set.add((w, h))
        self._unique_wh = list(wh_set)

        # 패딩 + 배치 가능 셀 사전계산
        self.padded_masks = {}
        self.placeable_masks = {}
        for oid, (mask, origin) in self.envs_cache.items():
            padded = np.zeros((max_rows, max_cols), dtype=bool)
            r, c = mask.shape
            padded[:r, :c] = mask

            placeable = np.zeros((max_rows, max_cols), dtype=bool)
            for w, h in self._unique_wh:
                for br in range(max_rows - h + 1):
                    for bc in range(max_cols - w + 1):
                        if padded[br:br+h, bc:bc+w].sum() >= w * h * 0.7:
                            placeable[br, bc] = True
            self.padded_masks[oid] = (padded, origin)
            self.placeable_masks[oid] = placeable

        self.reset()

    # ── 위치 자동결정 ────────────────────────────────────────

    def _find_next_position(self):
        """열 우선(왼→오), 같은 열에서 행 우선(아래→위)."""
        for bc in range(self.n_cols):
            for br in range(self.n_rows):
                if (self._placeable[br, bc]
                        and self.block_occupied[br, bc] == 0):
                    return (br, bc)
        return None

    # ── reset / step ─────────────────────────────────────────

    def reset(self):
        self.current_outline = random.choice(self.outlines)
        oid = self.current_outline['id']
        self.outline_mask, self.origin_m = self.padded_masks[oid]
        self._placeable = self.placeable_masks[oid]
        self.unit_blocks = int(self.outline_mask.sum())

        self.block_occupied = np.zeros(
            (self.n_rows, self.n_cols), dtype=np.int32)
        self.pieces = []
        self.placed_area_blocks = 0

        self._state = np.zeros(
            (self.n_channels, self.n_rows, self.n_cols), dtype=np.float32)
        self._state[0] = self.outline_mask.astype(np.float32)
        self._next_pos = self._find_next_position()
        return self._state.copy()

    def step(self, action_idx):
        pt_idx, rot = self.all_actions[action_idx]
        pw, ph, name = self.piece_types[pt_idx]
        w, h = (pw, ph) if rot == 0 else (ph, pw)
        br, bc = self._next_pos

        pid = len(self.pieces) + 1
        self.block_occupied[br:br+h, bc:bc+w] = pid
        self.pieces.append((pt_idx, br, bc, rot))
        self.placed_area_blocks += w * h

        self._state[1, br:br+h, bc:bc+w] = 1.0
        n_types = len(self.piece_types)
        self._state[2, br:br+h, bc:bc+w] = (pt_idx + 1) / n_types

        self._next_pos = self._find_next_position()
        done = self.is_done()
        return self._state.copy(), done

    # ── 유효 마스크 ──────────────────────────────────────────

    def get_valid_mask(self):
        mask = np.zeros(self.n_actions, dtype=bool)
        if self._next_pos is None:
            return mask

        br0, bc0 = self._next_pos
        for act_idx, (pt_idx, rot) in enumerate(self.all_actions):
            pw, ph, name = self.piece_types[pt_idx]
            w, h = (pw, ph) if rot == 0 else (ph, pw)

            if br0 + h > self.n_rows or bc0 + w > self.n_cols:
                continue
            region = self.block_occupied[br0:br0+h, bc0:bc0+w]
            if region.any():
                continue
            outline_region = self.outline_mask[br0:br0+h, bc0:bc0+w]
            if outline_region.sum() < w * h * 0.7:
                continue
            mask[act_idx] = True

        return mask

    def is_done(self):
        if self._next_pos is None:
            return True
        return not self.get_valid_mask().any()

    # ── 평가 (캔틸레버 포함) ─────────────────────────────────

    def evaluate(self):
        """기본 배치 커버리지 + 캔틸레버 보너스."""
        if not self.pieces:
            return 0.0
        occ = (self.block_occupied > 0)
        covered = float((occ & self.outline_mask).sum())
        coverage = covered / max(1.0, self.unit_blocks)
        total_placed = float(occ.sum())
        outside = float((occ & ~self.outline_mask).sum())
        overhang = outside / max(1.0, total_placed) if total_placed > 0 else 0

        # 캔틸레버 보너스: 추가 커버리지
        cant_blocks = self._count_cantilever_coverage()
        cant_bonus = cant_blocks / max(1.0, self.unit_blocks)

        return 3.0 * (coverage + cant_bonus * 0.5) - 2.0 * overhang

    def _count_cantilever_coverage(self):
        """규칙 기반 캔틸레버가 추가로 덮는 블록 수."""
        total = 0
        for cant_br, cant_bc, cant_w, cant_h in self._get_cantilevers():
            region = self.outline_mask[cant_br:cant_br+cant_h,
                                       cant_bc:cant_bc+cant_w]
            total += int(region.sum())
        return total

    def _get_cantilevers(self):
        """각 부재의 장변 양 끝에 캔틸레버 배치 시도.
        캔틸레버끼리 겹치지 않도록 임시 마스크 사용.
        반환: [(br, bc, w, h), ...] — 캔틸레버 영역 목록."""
        result = []
        # 기존 점유 + 이미 배치된 캔틸레버 추적
        cant_occ = (self.block_occupied != 0).copy()

        for i, (pt_idx, br, bc, rot) in enumerate(self.pieces):
            pw, ph, name = self.piece_types[pt_idx]
            w, h = (pw, ph) if rot == 0 else (ph, pw)

            # [CoT] 캔틸레버는 장변 방향으로 연장, 폭 = 단변
            #   rot=0: 장변=세로(h), 단변=가로(w) → 위/아래로 연장
            #   rot=1: 장변=가로(w), 단변=세로(h) → 좌/우로 연장
            if rot == 0:
                # 아래쪽 캔틸레버
                for cl in reversed(CANT_OPTIONS_BLOCKS):
                    if br - cl < 0:
                        continue
                    if cant_occ[br-cl:br, bc:bc+w].any():
                        continue
                    cant_occ[br-cl:br, bc:bc+w] = True
                    result.append((br - cl, bc, w, cl))
                    break
                # 위쪽 캔틸레버
                for cl in reversed(CANT_OPTIONS_BLOCKS):
                    if br + h + cl > self.n_rows:
                        continue
                    if cant_occ[br+h:br+h+cl, bc:bc+w].any():
                        continue
                    cant_occ[br+h:br+h+cl, bc:bc+w] = True
                    result.append((br + h, bc, w, cl))
                    break
            else:
                # 왼쪽 캔틸레버
                for cl in reversed(CANT_OPTIONS_BLOCKS):
                    if bc - cl < 0:
                        continue
                    if cant_occ[br:br+h, bc-cl:bc].any():
                        continue
                    cant_occ[br:br+h, bc-cl:bc] = True
                    result.append((br, bc - cl, cl, h))
                    break
                # 오른쪽 캔틸레버
                for cl in reversed(CANT_OPTIONS_BLOCKS):
                    if bc + w + cl > self.n_cols:
                        continue
                    if cant_occ[br:br+h, bc+w:bc+w+cl].any():
                        continue
                    cant_occ[br:br+h, bc+w:bc+w+cl] = True
                    result.append((br, bc + w, cl, h))
                    break

        return result

    # ── 후처리: 캔틸레버 + Mod→Pnl 변환 ──────────────────────

    def post_process(self):
        """캔틸레버 배치 + Mod→Pnl 변환.
        반환: [(pt_idx, br, bc, rot, display_name), ...]
        + self.cantilevers: [(br, bc, w, h), ...]"""
        self.cantilevers = self._get_cantilevers()

        result = []
        for i, (pt_idx, br, bc, rot) in enumerate(self.pieces):
            pw, ph, name = self.piece_types[pt_idx]
            w, h = (pw, ph) if rot == 0 else (ph, pw)
            pid = i + 1
            display_name = name

            # Mod1/2 → PnlT2/T3 변환 (장변 양쪽 지지 체크)
            if name.startswith('Mod'):
                if rot == 0:
                    left = 0
                    if bc > 0:
                        e = self.block_occupied[br:br+h, bc-1]
                        left = int(np.sum((e > 0) & (e != pid)))
                    right = 0
                    if bc + w < self.n_cols:
                        e = self.block_occupied[br:br+h, bc+w]
                        right = int(np.sum((e > 0) & (e != pid)))
                    if left >= 2 and right >= 2:
                        tag = 'PnlT2' if 'Mod1' in name else 'PnlT3'
                        display_name = tag
                else:
                    top = 0
                    if br > 0:
                        e = self.block_occupied[br-1, bc:bc+w]
                        top = int(np.sum((e > 0) & (e != pid)))
                    bottom = 0
                    if br + h < self.n_rows:
                        e = self.block_occupied[br+h, bc:bc+w]
                        bottom = int(np.sum((e > 0) & (e != pid)))
                    if top >= 2 and bottom >= 2:
                        tag = 'PnlT2' if 'Mod1' in name else 'PnlT3'
                        display_name = tag

            result.append((pt_idx, br, bc, rot, display_name))
        return result

    # ── 유틸 ─────────────────────────────────────────────────

    def clone(self):
        new = MultiOutlineEnv.__new__(MultiOutlineEnv)
        for attr in ['outlines', 'catalog', 'piece_types',
                     'envs_cache', 'padded_masks', 'placeable_masks',
                     '_unique_wh',
                     'n_rows', 'n_cols', 'n_channels',
                     'all_actions', 'n_actions']:
            setattr(new, attr, getattr(self, attr))
        new.reset()
        return new


def main():
    base = Path(__file__).parent

    all_outlines = load_outlines()
    catalogs = load_catalogs()
    catalog = catalogs[CATALOG_IDX]

    if OUTLINE_IDX >= 0:
        outlines = [all_outlines[OUTLINE_IDX]]
        print(f'외곽선: #{outlines[0]["id"]} (단일, 좌우대칭 2세대)')
    else:
        outlines = all_outlines
        print(f'외곽선: {len(outlines)}개 (좌우대칭 2세대)')
    print(f'카탈로그: #{catalog["id"]} ({catalog["case"]})')

    pieces = catalog_to_piece_types(catalog)
    for w, h, name in pieces:
        print(f'  {name}: {w}×{h}블록 = {w*BLOCK_M:.1f}m × {h*BLOCK_M:.1f}m')
    print(f'기본 부재: {len(pieces)}종')
    print(f'캔틸레버 옵션: {[blocks_to_m(c) for c in CANT_OPTIONS_BLOCKS]}m')

    env = MultiOutlineEnv(outlines, catalog)
    print(f'\n그리드: {env.n_rows}×{env.n_cols} (2세대)')
    print(f'액션 수: {env.n_actions} (기본 부재 선택)')

    network = PolicyValueNet(
        n_rows=env.n_rows,
        n_cols=env.n_cols,
        n_channels=env.n_channels,
        n_actions=env.n_actions,
    )
    params = sum(p.numel() for p in network.parameters())
    print(f'파라미터: {params:,}')

    trainer = PPOTrainer(env, network, n_vec_envs=N_VEC_ENVS)
    best_score, all_scores = trainer.train(
        n_episodes=N_EPISODES, log_every=LOG_EVERY)

    trainer.save_model(base / 'ppo_blocks_model.pt')
    plot_curve(all_scores, base / 'ppo_blocks_curve.png')
    visualize_best(env, network, outlines, catalog,
                   base / 'ppo_blocks_result.png')


def plot_curve(scores, out_path):
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(scores, alpha=0.2, color='blue', label='episode score')
    window = min(100, len(scores) // 10)
    if window > 1:
        ma = np.convolve(scores, np.ones(window) / window, mode='valid')
        ax.plot(range(window - 1, len(scores)), ma,
                color='red', linewidth=2, label=f'MA-{window}')
    ax.set_xlabel('Episode')
    ax.set_ylabel('Score')
    ax.set_title(f'PPO Training ({len(scores)} episodes)')
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    print(f'[saved] {out_path}')


def visualize_best(env, network, outlines, catalog, out_path, n=6):
    n = min(n, len(outlines))
    fig, axes = plt.subplots(2, 3, figsize=(20, 12))
    axes = axes.flatten()
    indices = list(range(n)) if n <= len(outlines) else list(range(len(outlines)))

    for idx, oi in enumerate(indices):
        ol = outlines[oi]
        oid = ol['id']
        env.current_outline = ol
        env.outline_mask, env.origin_m = env.padded_masks[oid]
        env._placeable = env.placeable_masks[oid]
        env.unit_blocks = int(env.outline_mask.sum())
        env.block_occupied = np.zeros(
            (env.n_rows, env.n_cols), dtype=np.int32)
        env.pieces = []
        env.placed_area_blocks = 0
        env._state[:] = 0
        env._state[0] = env.outline_mask.astype(np.float32)
        env._next_pos = env._find_next_position()

        state = env._state.copy()
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

        processed = env.post_process()

        ax = axes[idx]
        mask = env.outline_mask
        ax.imshow(mask.astype(float), cmap='gray_r', alpha=0.15,
                  origin='lower',
                  extent=[0, env.n_cols * BLOCK_M,
                          0, env.n_rows * BLOCK_M])

        # 기본 부재 그리기
        for i, (pt_idx, br, bc, rot, dname) in enumerate(processed):
            pw, ph, _ = env.piece_types[pt_idx]
            w, h = (pw, ph) if rot == 0 else (ph, pw)
            x = bc * BLOCK_M
            y = br * BLOCK_M

            if 'PnlT' in dname:
                fc = (0.3, 0.8, 0.3, 0.5)
            elif dname.startswith('Pnl'):
                fc = (0.6, 0.9, 0.4, 0.5)
            else:
                fc = (0.3, 0.5, 0.9, 0.5)

            rect = mpl_patches.Rectangle(
                (x, y), w * BLOCK_M, h * BLOCK_M,
                facecolor=fc, alpha=0.5,
                edgecolor='black', linewidth=0.8)
            ax.add_patch(rect)

        # 캔틸레버 그리기
        for cbr, cbc, cw, ch in env.cantilevers:
            cx = cbc * BLOCK_M
            cy = cbr * BLOCK_M
            rect = mpl_patches.Rectangle(
                (cx, cy), cw * BLOCK_M, ch * BLOCK_M,
                facecolor=(1.0, 0.6, 0.2, 0.5), alpha=0.5,
                edgecolor='orange', linewidth=0.8, linestyle='--')
            ax.add_patch(rect)

        occ = (env.block_occupied > 0)
        covered = float((occ & env.outline_mask).sum())
        cov = covered / max(1, env.unit_blocks) * 100
        n_cant = len(env.cantilevers)
        n_pnl = sum(1 for *_, nm in processed if 'PnlT' in nm)
        ax.set_title(f'{ol["name"][:20]}\n커버리지 {cov:.1f}% | '
                     f'{len(processed)}부재 + {n_cant}캔틸 (패널 {n_pnl})',
                     fontsize=9)
        ax.set_aspect('equal')
        ax.grid(True, alpha=0.2)

    for j in range(n, len(axes)):
        axes[j].axis('off')

    plt.suptitle(f'PPO 블록 배치 — 2세대 (카탈로그 #{catalog["id"]})',
                 fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f'[saved] {out_path}')


if __name__ == '__main__':
    main()
