"""
배치 RL 환경 — 개선판 (2D 상태 + 위치 선택 액션)

상태: (3, GRID_ROWS, GRID_COLS) — CNN 입력
  ch0: outline_mask   (1=내부, 0=외부)
  ch1: occupied_mask   (1=배치됨, 0=비어있음)
  ch2: element_type    (module=1.0, panel=0.7, T3=0.5, cant=0.3, 비어있음=0)

액션: (element_type, bay_idx, y_slot) → ~150개
  element_type: 5종 (mod_0, mod_1, panel, T3, cant)
  bay_idx: 0~MAX_BAYS-1
  y_slot: 0~MAX_Y_SLOTS-1 (1.65m 간격)

보상: 커버리지 증가 + 장스팬 보너스 + 에피소드 종료 시 최종 보너스
"""

import numpy as np
import random

from placer import PlacementEngine, get_bay_ranges

# ============================================================
# 그리드 & 액션 상수
# ============================================================
CELL_SIZE    = 1.65       # 그리드 셀 크기 (반모듈)
MOD_WIDTH    = 3.3
MAX_BAYS     = 6          # 최대 베이 수
MAX_Y_SLOTS  = 8          # 최대 y 슬롯 수 (1.65m × 8 = 13.2m)
N_ELEM_TYPES = 5          # mod_0, mod_1, panel, T3, cantilever
N_CHANNELS   = 3          # 상태 채널 수

# 고정 그리드 크기 (모든 외곽선에 대해 패딩)
GRID_ROWS    = 10         # y 방향 (1.65m × 10 = 16.5m)
GRID_COLS    = 12         # x 방향 (1.65m × 12 = 19.8m)

N_ACTIONS    = N_ELEM_TYPES * MAX_BAYS * MAX_Y_SLOTS  # 5×6×8 = 240

# 보상 가중치
W_COVERAGE       = 3.0
W_LONGSPAN       = 1.0    # 모듈+패널 공존 보너스
W_FINAL_BONUS    = 1.0    # 에피소드 종료 시 최종 커버리지 보너스

# 부재 타입별 상태 값
ELEM_STATE_VAL = {
    0: 1.0,    # mod_0
    1: 1.0,    # mod_1
    2: 0.7,    # panel
    3: 0.5,    # T3
    4: 0.3,    # cantilever
}


class PlacementEnv:
    """2D 그리드 기반 배치 RL 환경."""

    def __init__(self, outlines, catalogs):
        self.outlines = outlines
        self.catalogs = catalogs

        # 네트워크 인터페이스용 속성
        self.n_rows = GRID_ROWS
        self.n_cols = GRID_COLS
        self.n_channels = N_CHANNELS
        self.n_actions = N_ACTIONS
        self.state_dim = N_CHANNELS * GRID_ROWS * GRID_COLS  # 호환용

        # 에피소드 상태 (reset에서 초기화)
        self.eng = None
        self.outline = None
        self.catalog = None
        self.bay_ranges = None
        self.n_bays = 0
        self.elem_heights = {}
        self.prev_score = 0.0

        # 2D 상태 배열
        self.outline_grid = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float32)
        self.occupied_grid = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float32)
        self.type_grid = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float32)

        # 그리드 ↔ 실좌표 변환
        self.origin_x = 0.0
        self.origin_y = 0.0

        self.pieces = []  # 호환용

    def clone(self):
        return PlacementEnv(self.outlines, self.catalogs)

    # ── 좌표 변환 ──────────────────────────────────────────────

    def _real_to_grid(self, x, y):
        """실좌표 → 그리드 인덱스."""
        gc = int(round((x - self.origin_x) / CELL_SIZE))
        gr = int(round((y - self.origin_y) / CELL_SIZE))
        return gr, gc

    def _fill_grid_rect(self, grid, x, y, w, h, value):
        """그리드 위에 직사각형 영역 채우기."""
        r0, c0 = self._real_to_grid(x, y)
        r1, c1 = self._real_to_grid(x + w, y + h)
        r0 = max(0, r0); c0 = max(0, c0)
        r1 = min(GRID_ROWS, r1); c1 = min(GRID_COLS, c1)
        if r1 > r0 and c1 > c0:
            grid[r0:r1, c0:c1] = value

    def _build_outline_grid(self):
        """외곽선을 2D 그리드 마스크로 래스터화."""
        self.outline_grid[:] = 0
        for i, (x, y_min, y_max) in enumerate(self.bay_ranges):
            self._fill_grid_rect(self.outline_grid, x, y_min,
                                  MOD_WIDTH, y_max - y_min, 1.0)

    # ── reset / step ──────────────────────────────────────────

    def reset(self):
        self.outline = random.choice(self.outlines)
        self.catalog = random.choice(self.catalogs)
        self.eng = PlacementEngine(self.outline['coords'])
        self.bay_ranges = get_bay_ranges(self.outline)
        self.n_bays = len(self.bay_ranges)

        # 원점: 모든 좌표의 최소값
        all_x = [c[0] for c in self.outline['coords']]
        all_y = [c[1] for c in self.outline['coords']]
        self.origin_x = min(all_x)
        self.origin_y = min(all_y)

        # 카탈로그에서 부재 높이
        mods = sorted(self.catalog['horizontal_modules'], reverse=True)
        self.elem_heights = {
            0: mods[0] if len(mods) > 0 else 0,
            1: mods[1] if len(mods) > 1 else 0,
            2: self.catalog['panel1_length'],
            3: MOD_WIDTH,
            4: self.catalog['cantilever_ext'],
        }

        # 그리드 초기화
        self.occupied_grid[:] = 0
        self.type_grid[:] = 0
        self._build_outline_grid()

        self.prev_score = 0.0
        return self.get_state()

    def step(self, action_idx):
        elem_type, bay_idx, y_slot = self._decode_action(action_idx)

        x, y_min, y_max = self.bay_ranges[bay_idx]
        y = y_min + y_slot * CELL_SIZE
        h = self.elem_heights[elem_type]

        labels = {0: f'Mod {h:.1f}', 1: f'Mod {h:.1f}',
                  2: 'Panel', 3: 'T3', 4: 'Cant'}
        colors = {0: '#4C9AFF', 1: '#4C9AFF',
                  2: '#BA68C8', 3: '#66BB6A', 4: '#FFD54F'}

        placed = self.eng.try_place(labels[elem_type], colors[elem_type],
                                     x, y, MOD_WIDTH, h)
        if placed:
            self._fill_grid_rect(self.occupied_grid, x, y, MOD_WIDTH, h, 1.0)
            self._fill_grid_rect(self.type_grid, x, y, MOD_WIDTH, h,
                                  ELEM_STATE_VAL[elem_type])

        valid = self.get_valid_mask()
        done = not valid.any()

        return self.get_state(), done

    # ── 상태 / 마스크 / 평가 ──────────────────────────────────

    def get_state(self):
        """(3, GRID_ROWS, GRID_COLS) 상태 텐서."""
        state = np.zeros((N_CHANNELS, GRID_ROWS, GRID_COLS), dtype=np.float32)
        state[0] = self.outline_grid
        state[1] = self.occupied_grid
        state[2] = self.type_grid
        return state

    def get_valid_mask(self):
        """유효 액션 마스크 (N_ACTIONS,)."""
        mask = np.zeros(N_ACTIONS, dtype=bool)

        for bi in range(min(self.n_bays, MAX_BAYS)):
            x, y_min, y_max = self.bay_ranges[bi]

            for et in range(N_ELEM_TYPES):
                h = self.elem_heights.get(et, 0)
                if h <= 0:
                    continue

                # 캔틸레버: 이 베이에 모듈/패널이 있어야만
                if et == 4:
                    has_base = any(
                        True for l, _, px, *_ in self.eng.placements
                        if not l.startswith('Cant') and abs(px - x) < 0.1)
                    if not has_base:
                        continue

                for ys in range(MAX_Y_SLOTS):
                    y = y_min + ys * CELL_SIZE
                    if y + h > y_max + 0.05:
                        break
                    if self.eng.can_place(x, y, MOD_WIDTH, h):
                        idx = self._encode_action(et, bi, ys)
                        mask[idx] = True

        return mask

    def evaluate(self):
        """배치 점수."""
        cov = self.eng.coverage()

        # 장스팬 보너스
        longspan = 0
        for bi in range(min(self.n_bays, MAX_BAYS)):
            x = self.bay_ranges[bi][0]
            has_mod = any(l.startswith('Mod') for l, _, px, *_ in self.eng.placements
                          if abs(px - x) < 0.1)
            has_pan = any(l.startswith('Pan') for l, _, px, *_ in self.eng.placements
                          if abs(px - x) < 0.1)
            if has_mod and has_pan:
                longspan += 1

        score = (W_COVERAGE * cov
                 + W_LONGSPAN * longspan / max(self.n_bays, 1))
        return score

    # ── 액션 인코딩/디코딩 ────────────────────────────────────

    def _encode_action(self, elem_type, bay_idx, y_slot):
        return elem_type * (MAX_BAYS * MAX_Y_SLOTS) + bay_idx * MAX_Y_SLOTS + y_slot

    def _decode_action(self, action_idx):
        elem_type = action_idx // (MAX_BAYS * MAX_Y_SLOTS)
        rem = action_idx % (MAX_BAYS * MAX_Y_SLOTS)
        bay_idx = rem // MAX_Y_SLOTS
        y_slot = rem % MAX_Y_SLOTS
        return elem_type, bay_idx, y_slot
