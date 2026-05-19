"""
PPO 트레이너 — 기존 learning/ppo_trainer.py 구조 재활용
Phase 2(중간기둥), 구조해석 제거. 커버리지만 목표.
"""

import math
import time
import numpy as np
import torch
import torch.nn.functional as F


class PPOTrainer:

    def __init__(self, env, network,
                 gamma=0.99, lam=0.95,
                 clip_eps=0.2, epochs_per_update=4,
                 vf_coef=0.5, batch_size=256,
                 n_vec_envs=32,
                 lr_start=5e-4, lr_end=1e-4,
                 ent_start=0.01, ent_end=0.002):
        self.env = env
        self.network = network
        self.gamma = gamma
        self.lam = lam
        self.clip_eps = clip_eps
        self.epochs_per_update = epochs_per_update
        self.ent_coef = ent_start
        self.vf_coef = vf_coef
        self.batch_size = batch_size
        self.n_vec_envs = n_vec_envs
        self.lr_start = lr_start
        self.lr_end = lr_end
        self.ent_start = ent_start
        self.ent_end = ent_end

        self.envs = [env] + [env.clone() for _ in range(n_vec_envs - 1)]
        self.optimizer = torch.optim.Adam(network.parameters(), lr=lr_start)

    # ── 에피소드 수집 ────────────────────────────────────────

    def collect_episodes(self):
        self.network.eval()
        N = self.n_vec_envs
        n_actions = self.env.n_actions
        state_shape = (self.env.n_channels, self.env.n_rows, self.env.n_cols)

        states = np.zeros((N, *state_shape), dtype=np.float32)
        prev_scores = np.zeros(N, dtype=np.float32)
        for i, e in enumerate(self.envs):
            states[i] = e.reset()
            prev_scores[i] = e.evaluate()

        active = np.ones(N, dtype=bool)
        buffers = [{'states': [], 'actions': [], 'log_probs': [],
                     'values': [], 'rewards': [], 'masks': []}
                   for _ in range(N)]

        while active.any():
            active_idx = np.where(active)[0]
            valid_masks = np.zeros((len(active_idx), n_actions), dtype=bool)
            still_active = []

            for j, i in enumerate(active_idx):
                vm = self.envs[i].get_valid_mask()
                if not vm.any():
                    active[i] = False
                else:
                    valid_masks[j] = vm
                    still_active.append((j, i))

            if not still_active:
                break

            batch_idx = [j for j, _ in still_active]
            env_idx = [i for _, i in still_active]
            states_batch = states[env_idx]
            vm_batch = valid_masks[batch_idx]

            probs_batch, values_batch = self.network.predict_batch(states_batch)

            for k, (_, i) in enumerate(still_active):
                probs = probs_batch[k] * vm_batch[k]
                ps = probs.sum()
                if ps > 0:
                    probs /= ps
                else:
                    probs = vm_batch[k].astype(np.float32) / vm_batch[k].sum()

                action = np.random.choice(n_actions, p=probs)
                log_prob = np.log(probs[action] + 1e-8)

                buffers[i]['states'].append(states[i].copy())
                buffers[i]['actions'].append(action)
                buffers[i]['log_probs'].append(log_prob)
                buffers[i]['values'].append(values_batch[k])
                buffers[i]['masks'].append(vm_batch[k].copy())

                new_state, done = self.envs[i].step(action)
                states[i] = new_state

                curr_score = self.envs[i].evaluate()
                reward = curr_score - prev_scores[i]
                buffers[i]['rewards'].append(reward)
                prev_scores[i] = curr_score

                if done:
                    active[i] = False

        episodes = []
        for i in range(N):
            buf = buffers[i]
            if not buf['rewards']:
                continue
            advantages, returns = self._compute_gae(
                buf['rewards'], buf['values'], 0.0)
            episodes.append({
                'states': np.array(buf['states']),
                'actions': np.array(buf['actions']),
                'log_probs': np.array(buf['log_probs'], dtype=np.float32),
                'advantages': advantages,
                'returns': returns,
                'masks': buf['masks'],
                'final_score': prev_scores[i],
            })
        return episodes

    # ── GAE ───────────────────────────────────────────────────

    def _compute_gae(self, rewards, values, next_value):
        n = len(rewards)
        advantages = np.zeros(n, dtype=np.float32)
        returns = np.zeros(n, dtype=np.float32)
        gae = 0.0
        nv = next_value
        for t in reversed(range(n)):
            delta = float(rewards[t]) + self.gamma * nv - values[t]
            gae = delta + self.gamma * self.lam * gae
            advantages[t] = gae
            returns[t] = advantages[t] + values[t]
            nv = values[t]
        return advantages, returns

    # ── PPO 업데이트 ──────────────────────────────────────────

    def update(self, episodes):
        valid_eps = [ep for ep in episodes if len(ep['states']) > 0]
        if not valid_eps:
            return {'policy_loss': 0, 'value_loss': 0, 'entropy': 0}

        all_states = np.concatenate([ep['states'] for ep in valid_eps])
        all_actions = np.concatenate([ep['actions'] for ep in valid_eps])
        all_log_probs = np.concatenate([ep['log_probs'] for ep in valid_eps])
        all_advantages = np.concatenate([ep['advantages'] for ep in valid_eps])
        all_returns = np.concatenate([ep['returns'] for ep in valid_eps])
        all_masks = []
        for ep in valid_eps:
            all_masks.extend(ep['masks'])

        if len(all_advantages) > 1:
            all_advantages = ((all_advantages - all_advantages.mean())
                              / (all_advantages.std() + 1e-8))

        n = len(all_states)
        dev = self.network.device

        states_t = torch.FloatTensor(all_states).to(dev)
        actions_t = torch.LongTensor(all_actions).to(dev)
        old_lp_t = torch.FloatTensor(all_log_probs).to(dev)
        adv_t = torch.FloatTensor(all_advantages).to(dev)
        ret_t = torch.FloatTensor(all_returns).to(dev)
        masks_t = torch.FloatTensor(np.array(all_masks)).to(dev)

        self.network.train()
        tp, tv, te, nu = 0, 0, 0, 0

        for _ in range(self.epochs_per_update):
            indices = torch.randperm(n, device=dev)
            for start in range(0, n, self.batch_size):
                end = min(start + self.batch_size, n)
                idx = indices[start:end]

                logits, values = self.network(states_t[idx])
                ml = logits + (masks_t[idx] - 1) * 1e8
                lp = F.log_softmax(ml, dim=1)
                pr = F.softmax(ml, dim=1)

                new_lp = lp.gather(1, actions_t[idx].unsqueeze(1)).squeeze(1)
                ratio = torch.exp(new_lp - old_lp_t[idx])
                s1 = ratio * adv_t[idx]
                s2 = torch.clamp(ratio, 1 - self.clip_eps,
                                 1 + self.clip_eps) * adv_t[idx]
                p_loss = -torch.min(s1, s2).mean()
                v_loss = F.mse_loss(values.squeeze(1), ret_t[idx])
                entropy = -(pr * lp).sum(dim=1).mean()

                loss = (p_loss + self.vf_coef * v_loss
                        - self.ent_coef * entropy)

                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    self.network.parameters(), 1.0)
                self.optimizer.step()

                tp += p_loss.item()
                tv += v_loss.item()
                te += entropy.item()
                nu += 1

        return {'policy_loss': tp / max(1, nu),
                'value_loss': tv / max(1, nu),
                'entropy': te / max(1, nu)}

    # ── 스케줄 ────────────────────────────────────────────────

    def _update_schedule(self, progress):
        cosine = 0.5 * (1 + math.cos(math.pi * progress))
        new_lr = self.lr_end + (self.lr_start - self.lr_end) * cosine
        for pg in self.optimizer.param_groups:
            pg['lr'] = new_lr
        self.ent_coef = (self.ent_start
                         + (self.ent_end - self.ent_start) * progress)
        return new_lr

    # ── 학습 루프 ─────────────────────────────────────────────

    def train(self, n_episodes=30000, log_every=200):
        best_score = -float('inf')
        all_scores = []
        N = self.n_vec_envs

        print(f'\nPPO 학습 시작')
        print(f'  장치: {self.network.device}')
        print(f'  에피소드: {n_episodes}')
        print(f'  동시 환경: {N}')
        print(f'  그리드: {self.env.n_rows}×{self.env.n_cols}')
        print(f'  액션 수: {self.env.n_actions}')
        params = sum(p.numel() for p in self.network.parameters())
        print(f'  파라미터: {params:,}')
        print('-' * 60)

        t0 = time.time()
        ep_count = 0
        episode_buffer = []

        while ep_count < n_episodes:
            progress = min(ep_count / n_episodes, 1.0)
            cur_lr = self._update_schedule(progress)

            batch_episodes = self.collect_episodes()

            for ep in batch_episodes:
                ep_count += 1
                if ep_count > n_episodes:
                    break
                episode_buffer.append(ep)
                all_scores.append(ep['final_score'])
                if ep['final_score'] > best_score:
                    best_score = ep['final_score']

            if episode_buffer:
                self.update(episode_buffer)
                episode_buffer = []

            if ep_count % log_every < N or ep_count >= n_episodes:
                elapsed = time.time() - t0
                eta = elapsed / max(ep_count, 1) * (n_episodes - ep_count)
                recent = all_scores[-log_every:]
                avg = np.mean(recent)
                mx = np.max(recent)
                print(f'  [{ep_count:>6}/{n_episodes}] '
                      f'best={best_score:.3f} avg={avg:.3f} '
                      f'max_r={mx:.3f} | '
                      f'lr={cur_lr:.1e} ent={self.ent_coef:.4f} | '
                      f'ETA {eta:.0f}s')

        elapsed = time.time() - t0
        print(f'\n학습 완료! best={best_score:.3f} 시간={elapsed:.0f}s')
        return best_score, all_scores

    def save_model(self, path):
        torch.save(self.network.state_dict(), path)

    def load_model(self, path):
        self.network.load_state_dict(
            torch.load(path, map_location=self.network.device))
