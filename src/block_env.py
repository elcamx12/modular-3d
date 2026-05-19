"""
블록 단위 배치 환경 — 외곽선 + 카탈로그 기반

기존 learning/block_env.py 구조를 재활용하되:
  - 입력: 외곽선 다각형(coords_m) + 카탈로그(부재 치수)
  - Phase 2(중간기둥), 코어/습식/방 정보 제거
  - 커버리지 최대화만 목표

상태: (3, n_rows, n_cols)
  ch0: outline_mask (1=내부, 0=외부)
  ch1: occupied_mask (1=배치됨)
  ch2: piece_type_map (부재 종류별 값)
"""

import numpy as np
from block_config import (
    BLOCK_M, catalog_to_piece_types, outline_to_block_mask,
)

# ── 보상 가중치 ──────────────────────────────────────────────
W_COVERAGE = 3.0
W_OVERHANG = 2.0     # 외곽선 밖 배치 패널티

N_CHANNELS = 3


class BlockEnv:
    """블록 단위 모듈 배치 환경."""

    def __init__(self, outline, catalog):
        """
        Args:
            outline: dict — coords_m, area_m2, ...
            catalog: dict — horizontal_modules, cantilever_ext, ...
        """
        self.outline = outline
        self.catalog = catalog

        # 부재 타입 생성
        self.piece_types = catalog_to_piece_types(catalog)

        # 외곽선 → 블록 마스크
        self.outline_mask, self.origin_m = outline_to_block_mask(
            outline['coords_m'])
        self.n_rows, self.n_cols = self.outline_mask.shape
        self.n_channels = N_CHANNELS

        # 세대 면적 (블록 수)
        self.unit_blocks = int(self.outline_mask.sum())

        # 액션 공간 구축
        self._build_action_space()
        self._build_action_regions()

        # 초기화
        self.reset()

    # ── 액션 공간 ────────────────────────────────────────────

    def _build_action_space(self):
        """모든 (piece_idx, row, col, rotation) 열거.
        외곽선 내부 비율 70% 이상인 위치만."""
        self.all_actions = []
        for pt_idx, (pw, ph, _) in enumerate(self.piece_types):
            for rot in (0, 1):
                w, h = (pw, ph) if rot == 0 else (ph, pw)
                if w == h and rot == 1:
                    continue
                area = w * h
                for br in range(self.n_rows - h + 1):
                    for bc in range(self.n_cols - w + 1):
                        region = self.outline_mask[br:br+h, bc:bc+w]
                        if region.sum() >= area * 0.7:
                            self.all_actions.append((pt_idx, br, bc, rot))

        self.n_actions = len(self.all_actions)
        self.action_to_idx = {a: i for i, a in enumerate(self.all_actions)}

    def _build_action_regions(self):
        """각 액션의 (br, bc, h, w)를 배열로 저장 (prefix sum 마스크용)."""
        n = len(self.all_actions)
        self._act_br = np.zeros(n, dtype=np.int32)
        self._act_bc = np.zeros(n, dtype=np.int32)
        self._act_h = np.zeros(n, dtype=np.int32)
        self._act_w = np.zeros(n, dtype=np.int32)
        for i, (pt_idx, br, bc, rot) in enumerate(self.all_actions):
            pw, ph, _ = self.piece_types[pt_idx]
            w, h = (pw, ph) if rot == 0 else (ph, pw)
            self._act_br[i] = br
            self._act_bc[i] = bc
            self._act_h[i] = h
            self._act_w[i] = w

    # ── reset / step ─────────────────────────────────────────

    def reset(self):
        self.block_occupied = np.zeros((self.n_rows, self.n_cols), dtype=np.int32)
        self.pieces = []
        self.placed_area_blocks = 0

        # 상태 텐서
        self._state = np.zeros((N_CHANNELS, self.n_rows, self.n_cols),
                               dtype=np.float32)
        self._state[0] = self.outline_mask.astype(np.float32)
        return self._state.copy()

    def step(self, action_idx):
        """액션 실행 → (state, done)."""
        pt_idx, br, bc, rot = self.all_actions[action_idx]
        pw, ph, _ = self.piece_types[pt_idx]
        w, h = (pw, ph) if rot == 0 else (ph, pw)

        pid = len(self.pieces) + 1
        self.block_occupied[br:br+h, bc:bc+w] = pid
        self.pieces.append((pt_idx, br, bc, rot))
        self.placed_area_blocks += w * h

        # 상태 업데이트
        self._state[1, br:br+h, bc:bc+w] = 1.0
        # 부재 종류 인코딩: 인덱스를 정규화
        n_types = len(self.piece_types)
        self._state[2, br:br+h, bc:bc+w] = (pt_idx + 1) / n_types

        done = self.is_done()
        return self._state.copy(), done

    # ── 유효 마스크 ──────────────────────────────────────────

    def get_valid_mask(self):
        """prefix sum 기반 유효 액션 마스크 + 맞닿기 제약."""
        occ = (self.block_occupied > 0)
        occ_f = occ.astype(np.float64)

        ps = np.cumsum(np.cumsum(occ_f, axis=0), axis=1)
        ps_pad = np.zeros((self.n_rows + 1, self.n_cols + 1), dtype=np.float64)
        ps_pad[1:, 1:] = ps

        r1 = self._act_br
        c1 = self._act_bc
        r2 = r1 + self._act_h
        c2 = c1 + self._act_w

        occ_sum = (ps_pad[r2, c2] - ps_pad[r1, c2]
                   - ps_pad[r2, c1] + ps_pad[r1, c1])
        no_overlap = occ_sum == 0

        if not self.pieces:
            return no_overlap

        # 8방향 팽창 → 인접 영역
        pad = np.zeros((self.n_rows + 2, self.n_cols + 2), dtype=bool)
        pad[1:-1, 1:-1] = occ
        dilated = np.zeros((self.n_rows, self.n_cols), dtype=bool)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                dilated |= pad[1+dr:self.n_rows+1+dr, 1+dc:self.n_cols+1+dc]
        adj = (dilated & ~occ).astype(np.float64)

        adj_ps = np.cumsum(np.cumsum(adj, axis=0), axis=1)
        adj_pad = np.zeros((self.n_rows + 1, self.n_cols + 1), dtype=np.float64)
        adj_pad[1:, 1:] = adj_ps

        adj_sum = (adj_pad[r2, c2] - adj_pad[r1, c2]
                   - adj_pad[r2, c1] + adj_pad[r1, c1])
        has_adj = adj_sum > 0

        return no_overlap & has_adj

    def is_done(self):
        """배치할 곳이 없으면 종료."""
        min_piece_area = min(pw * ph for pw, ph, _ in self.piece_types)
        if self.placed_area_blocks + min_piece_area > self.unit_blocks * 1.5:
            return True
        return not self.get_valid_mask().any()

    # ── 평가 ─────────────────────────────────────────────────

    def evaluate(self):
        """커버리지 기반 평가 + 구조 규칙 감점."""
        if not self.pieces:
            return 0.0

        occ = (self.block_occupied > 0)

        covered = float((occ & self.outline_mask).sum())
        coverage = covered / max(1.0, self.unit_blocks)

        total_placed = float(occ.sum())
        outside = float((occ & ~self.outline_mask).sum())
        overhang = outside / max(1.0, total_placed) if total_placed > 0 else 0

        # ── 구조 규칙 감점 ──
        penalty = 0.0
        for i, (pt_idx, br, bc, rot) in enumerate(self.pieces):
            pw, ph, name = self.piece_types[pt_idx]
            w, h = (pw, ph) if rot == 0 else (ph, pw)
            pid = i + 1

            if name.startswith('Cant'):
                attached = False
                if rot == 0:
                    if br > 0:
                        edge = self.block_occupied[br - 1, bc:bc + w]
                        if np.any((edge > 0) & (edge != pid)):
                            attached = True
                    if not attached and br + h < self.n_rows:
                        edge = self.block_occupied[br + h, bc:bc + w]
                        if np.any((edge > 0) & (edge != pid)):
                            attached = True
                else:
                    if bc > 0:
                        edge = self.block_occupied[br:br + h, bc - 1]
                        if np.any((edge > 0) & (edge != pid)):
                            attached = True
                    if not attached and bc + w < self.n_cols:
                        edge = self.block_occupied[br:br + h, bc + w]
                        if np.any((edge > 0) & (edge != pid)):
                            attached = True
                if not attached:
                    penalty += 0.3

            elif name.startswith('Pnl'):
                if rot == 0:
                    left = 0
                    if bc > 0:
                        e = self.block_occupied[br:br + h, bc - 1]
                        left = int(np.sum((e > 0) & (e != pid)))
                    right = 0
                    if bc + w < self.n_cols:
                        e = self.block_occupied[br:br + h, bc + w]
                        right = int(np.sum((e > 0) & (e != pid)))
                    if left < 2 or right < 2:
                        penalty += 0.2
                else:
                    top = 0
                    if br > 0:
                        e = self.block_occupied[br - 1, bc:bc + w]
                        top = int(np.sum((e > 0) & (e != pid)))
                    bottom = 0
                    if br + h < self.n_rows:
                        e = self.block_occupied[br + h, bc:bc + w]
                        bottom = int(np.sum((e > 0) & (e != pid)))
                    if top < 2 or bottom < 2:
                        penalty += 0.2

        return W_COVERAGE * coverage - W_OVERHANG * overhang - penalty

    # ── 유틸 ─────────────────────────────────────────────────

    def clone(self):
        """불변 속성 공유, 가변 상태만 초기화."""
        new = BlockEnv.__new__(BlockEnv)
        for attr in ['outline', 'catalog', 'piece_types',
                     'outline_mask', 'origin_m', 'n_rows', 'n_cols',
                     'n_channels', 'unit_blocks',
                     'all_actions', 'n_actions', 'action_to_idx',
                     '_act_br', '_act_bc', '_act_h', '_act_w']:
            setattr(new, attr, getattr(self, attr))
        new.reset()
        return new
