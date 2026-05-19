"""
정책+가치 CNN — 위치 자동결정 방식용
입력: (batch, 3, n_rows, n_cols)
출력: 정책 logits (부재 선택) + 가치
"""

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


def get_device():
    if torch.cuda.is_available():
        return torch.device('cuda')
    return torch.device('cpu')


class PolicyValueNet(nn.Module):

    def __init__(self, n_rows, n_cols, n_channels=3, n_actions=17):
        super().__init__()
        self.n_rows = n_rows
        self.n_cols = n_cols
        self.n_actions = n_actions
        self.device = get_device()

        self.conv1 = nn.Conv2d(n_channels, 32, kernel_size=3, padding=1)
        self.conv2 = nn.Conv2d(32, 64, kernel_size=3, padding=1)
        self.gap = nn.AdaptiveAvgPool2d(1)  # (B, 64, 1, 1)
        self.flat_size = 64

        # 정책 헤드
        self.policy_fc1 = nn.Linear(self.flat_size, 32)
        self.policy_fc2 = nn.Linear(32, n_actions)

        # 가치 헤드
        self.value_fc1 = nn.Linear(self.flat_size, 32)
        self.value_fc2 = nn.Linear(32, 1)

        self.to(self.device)

    def forward(self, x):
        h = F.relu(self.conv1(x))
        h = F.relu(self.conv2(h))
        h = self.gap(h).view(h.size(0), -1)

        p = F.relu(self.policy_fc1(h))
        policy_logits = self.policy_fc2(p)

        v = F.relu(self.value_fc1(h))
        value = self.value_fc2(v)

        return policy_logits, value

    def predict(self, state_np):
        if not hasattr(self, '_input_buf'):
            self._input_buf = torch.zeros(
                1, state_np.shape[0], self.n_rows, self.n_cols,
                dtype=torch.float32, device=self.device)
        self._input_buf[0] = torch.as_tensor(state_np, dtype=torch.float32)
        with torch.inference_mode():
            logits, value = self(self._input_buf)
            logits_np = logits.squeeze(0).cpu().numpy()
        logits_np -= logits_np.max()
        exp_l = np.exp(logits_np)
        probs = exp_l / exp_l.sum()
        return probs, value.item()

    def predict_batch(self, states_np):
        with torch.inference_mode():
            x = torch.as_tensor(states_np, dtype=torch.float32,
                                device=self.device)
            logits, values = self(x)
            logits_np = logits.cpu().numpy()
            values_np = values.squeeze(1).cpu().numpy()
        logits_np -= logits_np.max(axis=1, keepdims=True)
        exp_l = np.exp(logits_np)
        probs = exp_l / exp_l.sum(axis=1, keepdims=True)
        return probs, values_np
