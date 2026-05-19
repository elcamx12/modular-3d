"""
모듈 / 캔틸레버 / 플로어패널 타입 정의 및 시각화

각 요소는 독립된 타입으로만 정의함.
외곽선 위에 배치할 때 자유롭게 조합 예정.

[모듈]
- 타입 1: 눕힘, 평면 length(6~8) × 3.3 m, 높이 3.3 m
- 타입 2: 눕힘, 평면 length(8~10) × 3.3 m, 높이 3.3 m
- 타입 3: 세움, 평면 3.3 × 3.3 m, 높이 9.9 m  (3개 층 분)

[캔틸레버]
- 돌출 0.5 ~ 2.0 m (연속)
- 폭 3.3 m (부착 대상의 단변과 일치 예정)
- 지금은 독립 타입. 배치 단계에서 단변에만 부착.

[플로어패널]
- 타입 1: 길이 4~5 m (연속),
          폭은 (모듈길이 + 옵션 캔틸)에 가장 잘 맞는 N을 기준으로
          {3.0, 3.3, 3.5} m 중 2개 선택 → 동일 길이·다른 폭 2개 인스턴스
- 타입 2: 모듈 타입 1 (6~8 m) 바닥판 (+캔틸 슬래브),
          길이 = 모듈길이 + 캔틸(0 or 0.5~2), 폭 3.3 m
- 타입 3: 모듈 타입 2 (8~10 m) 바닥판 (+캔틸 슬래브),
          길이 = 모듈길이 + 캔틸(0 or 0.5~2), 폭 3.3 m
"""

import random
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches


# ============================================================
# 공통 스펙
# ============================================================
MODULE_WIDTH         = 3.3    # 모듈 단변 (m)
DEFAULT_STORY_HEIGHT = 3.3    # 기본 층고 (m)

TYPE1_LENGTH_RANGE = (6.0, 8.0)    # 연속
TYPE2_LENGTH_RANGE = (8.0, 10.0)   # 연속
TYPE3_STAND_HEIGHT = 9.9           # 세워진 상태의 높이 (= 원래 길이, 고정)

CANTILEVER_RANGE   = (0.5, 2.0)    # 연속

FLOOR_PANEL_LENGTH_RANGE  = (4.0, 5.0)                           # 플로어패널 타입1 길이 (연속)
FLOOR_PANEL_WIDTH_OPTIONS = [3.0, 3.1, 3.2, 3.3, 3.4, 3.5]       # 플로어패널 타입1 폭 후보 (이산, 0.1m 간격)


# ============================================================
# Module 데이터 클래스
# ============================================================
@dataclass
class Module:
    type_id: int
    length: float                 # 설계 장변 (6~8 / 8~10 / 9.9)
    width: float = MODULE_WIDTH   # 단변
    is_standing: bool = False     # True = 세워서 배치 (Type 3)
    x: float = 0.0                # 평면 좌하단 x
    y: float = 0.0                # 평면 좌하단 y

    @property
    def plan_w(self):
        """평면도상 가로(x) 치수"""
        # 세운 모듈은 평면상 단변 × 단변 정사각형
        return self.width if self.is_standing else self.length

    @property
    def plan_d(self):
        """평면도상 세로(y) 치수"""
        return self.width

    @property
    def height(self):
        """실제 수직 높이(층고)"""
        return self.length if self.is_standing else DEFAULT_STORY_HEIGHT

    @property
    def plan_area(self):
        return self.plan_w * self.plan_d


# ============================================================
# Cantilever 데이터 클래스
# ============================================================
@dataclass
class Cantilever:
    extension: float                 # 돌출 길이 (m), 0.5~2.0
    width: float = MODULE_WIDTH      # 폭 (m), 단변 고정
    orientation: str = 'horizontal'  # 'horizontal'=extension이 x방향
    x: float = 0.0
    y: float = 0.0

    @property
    def plan_w(self):
        return self.extension if self.orientation == 'horizontal' else self.width

    @property
    def plan_d(self):
        return self.width if self.orientation == 'horizontal' else self.extension

    @property
    def plan_area(self):
        return self.extension * self.width


# ============================================================
# CantileverBeam 데이터 클래스
# ============================================================
CANTILEVER_BEAM_RANGE = (0.5, 2.0)    # 캔틸레버 보 돌출 범위 (연속)

@dataclass
class CantileverBeam:
    extension: float                 # 돌출 길이 (m), 0.5~2.0
    width: float = MODULE_WIDTH      # 폭 (m), 단변 고정
    orientation: str = 'horizontal'  # 'horizontal'=extension이 x방향
    x: float = 0.0
    y: float = 0.0

    @property
    def plan_w(self):
        return self.extension if self.orientation == 'horizontal' else self.width

    @property
    def plan_d(self):
        return self.width if self.orientation == 'horizontal' else self.extension

    @property
    def plan_area(self):
        return self.extension * self.width


# ============================================================
# FloorPanel 데이터 클래스
# ============================================================
@dataclass
class FloorPanel:
    type_id: int                 # 1 / 2 / 3
    length: float                # 장변 (m)
    width: float                 # 단변 (m)
    x: float = 0.0
    y: float = 0.0
    based_on: str = ''           # 생성 근거 메모 (디버깅/표시용)

    @property
    def plan_w(self):
        return self.length

    @property
    def plan_d(self):
        return self.width

    @property
    def plan_area(self):
        return self.length * self.width


# ============================================================
# 팩토리 함수
# ============================================================
def make_type1(length=7.0, x=0.0, y=0.0):
    """타입1: 눕힘, 길이 6~8m 연속"""
    lo, hi = TYPE1_LENGTH_RANGE
    assert lo <= length <= hi, f"타입1 길이 범위는 {lo}~{hi} m"
    return Module(type_id=1, length=length, is_standing=False, x=x, y=y)


def make_type2(length=9.0, x=0.0, y=0.0):
    """타입2: 눕힘, 길이 8~10m 연속"""
    lo, hi = TYPE2_LENGTH_RANGE
    assert lo <= length <= hi, f"타입2 길이 범위는 {lo}~{hi} m"
    return Module(type_id=2, length=length, is_standing=False, x=x, y=y)


def make_type3(x=0.0, y=0.0):
    """타입3: 세움, 평면 3.3×3.3, 높이 9.9m 고정"""
    return Module(type_id=3, length=TYPE3_STAND_HEIGHT,
                  is_standing=True, x=x, y=y)


def make_cantilever(extension=1.5, orientation='horizontal', x=0.0, y=0.0):
    """캔틸레버 슬래브: 돌출 0.5~2.0m 연속, 폭 3.3m 고정"""
    lo, hi = CANTILEVER_RANGE
    assert lo <= extension <= hi, f"캔틸레버 슬래브 돌출 범위는 {lo}~{hi} m"
    assert orientation in ('horizontal', 'vertical')
    return Cantilever(extension=extension, orientation=orientation, x=x, y=y)


def make_cantilever_beam(extension=1.5, orientation='horizontal', x=0.0, y=0.0):
    """캔틸레버 보: 돌출 0.5~2.0m 연속, 폭 3.3m 고정"""
    lo, hi = CANTILEVER_BEAM_RANGE
    assert lo <= extension <= hi, f"캔틸레버 보 돌출 범위는 {lo}~{hi} m"
    assert orientation in ('horizontal', 'vertical')
    return CantileverBeam(extension=extension, orientation=orientation, x=x, y=y)


# ============================================================
# 플로어패널 팩토리 & 폭 선택 로직
# ============================================================
def sample_panel_type1_length():
    return random.uniform(*FLOOR_PANEL_LENGTH_RANGE)


def best_two_panel_widths(total_length):
    """
    total_length = 모듈 길이 + 옵션 캔틸레버 길이
    {3.0, 3.3, 3.5} 중 N*W(자연수 N)이 total_length에 가장 가까운 W 2개 선택.
    return: [(W, N, deviation), (W, N, deviation)]  오차 오름차순
    """
    scored = []
    for W in FLOOR_PANEL_WIDTH_OPTIONS:
        N = max(1, round(total_length / W))
        dev = abs(total_length - N * W)
        scored.append((dev, W, N))
    scored.sort()
    return [(W, N, dev) for dev, W, N in scored[:2]]


def make_panel_type1(total_length, length=None, x=0.0, y=0.0):
    """
    타입1: 길이 4~5m 연속, 폭은 total_length 기준 2개 선택 → 패널 2개 리턴
    """
    if length is None:
        length = sample_panel_type1_length()
    widths = best_two_panel_widths(total_length)
    panels = []
    for W, N, dev in widths:
        p = FloorPanel(
            type_id=1,
            length=length,
            width=W,
            x=x, y=y,
            based_on=f'total {total_length:.2f}m, N={N}, dev={dev:.2f}m',
        )
        panels.append(p)
    return panels


def make_panel_type2(module_length=None, cant_length=0.0, x=0.0, y=0.0):
    """
    타입2: 모듈 타입1 (6~8m) 바닥판 (+캔틸 슬래브)
    길이 = module_length + cant_length, 폭 = 3.3m
    """
    if module_length is None:
        module_length = random.uniform(*TYPE1_LENGTH_RANGE)
    total = module_length + cant_length
    return FloorPanel(
        type_id=2,
        length=total,
        width=MODULE_WIDTH,
        x=x, y=y,
        based_on=f'mod {module_length:.2f} + cant {cant_length:.2f}',
    )


def make_panel_type3(module_length=None, cant_length=0.0, x=0.0, y=0.0):
    """
    타입3: 모듈 타입2 (8~10m) 바닥판 (+캔틸 슬래브)
    """
    if module_length is None:
        module_length = random.uniform(*TYPE2_LENGTH_RANGE)
    total = module_length + cant_length
    return FloorPanel(
        type_id=3,
        length=total,
        width=MODULE_WIDTH,
        x=x, y=y,
        based_on=f'mod {module_length:.2f} + cant {cant_length:.2f}',
    )


# ============================================================
# 시각화
# ============================================================
MODULE_COLORS = {1: '#4C9AFF', 2: '#FF8A65', 3: '#66BB6A'}
CANT_COLOR    = '#FFD54F'
PANEL_COLORS  = {1: '#BA68C8', 2: '#90A4AE', 3: '#A1887F'}


def draw_module(ax, mod):
    rect = patches.Rectangle(
        (mod.x, mod.y),
        mod.plan_w,
        mod.plan_d,
        linewidth=1.5, edgecolor='black',
        facecolor=MODULE_COLORS[mod.type_id], alpha=0.55,
    )
    ax.add_patch(rect)

    cx = mod.x + mod.plan_w / 2
    cy = mod.y + mod.plan_d / 2
    if mod.is_standing:
        label = f"Type {mod.type_id}\nH = {mod.height:.1f} m"
    else:
        label = f"Type {mod.type_id}  L = {mod.length:.1f} m"
    ax.text(cx, cy, label, ha='center', va='center',
            fontsize=8, fontweight='bold')


def draw_cantilever(ax, cant):
    rect = patches.Rectangle(
        (cant.x, cant.y),
        cant.plan_w,
        cant.plan_d,
        linewidth=1.5, edgecolor='black',
        facecolor=CANT_COLOR, alpha=0.55,
        hatch='//',
    )
    ax.add_patch(rect)

    cx = cant.x + cant.plan_w / 2
    cy = cant.y + cant.plan_d / 2
    ax.text(cx, cy, f"ext = {cant.extension:.1f} m",
            ha='center', va='center', fontsize=7, fontweight='bold')


def draw_panel(ax, panel):
    rect = patches.Rectangle(
        (panel.x, panel.y),
        panel.plan_w,
        panel.plan_d,
        linewidth=1.5, edgecolor='black',
        facecolor=PANEL_COLORS[panel.type_id], alpha=0.55,
        hatch='xx' if panel.type_id == 1 else None,
    )
    ax.add_patch(rect)

    cx = panel.x + panel.plan_w / 2
    cy = panel.y + panel.plan_d / 2
    ax.text(
        cx, cy,
        f"Panel Type {panel.type_id}\n"
        f"L {panel.length:.2f}  W {panel.width:.2f}",
        ha='center', va='center', fontsize=8, fontweight='bold',
    )


# ============================================================
# 연속 범위에서 길이 샘플링 (랜덤)
# ============================================================
MIN_T1_T2_GAP = 1.0   # 타입1과 타입2 길이 최소 차이 (m)


def sample_type1_length():
    return random.uniform(*TYPE1_LENGTH_RANGE)

def sample_type2_length():
    return random.uniform(*TYPE2_LENGTH_RANGE)

def sample_cantilever_extension():
    return random.uniform(*CANTILEVER_RANGE)


def sample_type1_and_type2_lengths():
    """
    모듈 타입1, 타입2 길이를 동시에 샘플.
    |L1 - L2| >= MIN_T1_T2_GAP (기본 1.0 m) 보장.
    타입2 범위가 타입1보다 항상 크거나 같으므로 L2 >= L1 + gap 으로 처리.
    """
    L1 = random.uniform(*TYPE1_LENGTH_RANGE)
    lo2 = max(TYPE2_LENGTH_RANGE[0], L1 + MIN_T1_T2_GAP)
    hi2 = TYPE2_LENGTH_RANGE[1]
    assert lo2 <= hi2, (
        f"gap {MIN_T1_T2_GAP} m 로는 샘플 불가: "
        f"L1={L1}, L2 범위 [{lo2}, {hi2}]"
    )
    L2 = random.uniform(lo2, hi2)
    return L1, L2


# ============================================================
# 개별 타입 미리보기 (각 타입 1개씩, 길이는 연속 범위에서 샘플링)
# ============================================================
def visualize_types(out_path):
    # 타입1/타입2 길이 차이 >= 1m 보장
    L1, L2 = sample_type1_and_type2_lengths()
    t1 = make_type1(length=L1)
    t2 = make_type2(length=L2)
    t3 = make_type3()
    cant = make_cantilever(extension=sample_cantilever_extension())

    fig, axes = plt.subplots(1, 4, figsize=(19, 5.5))

    titles = [
        f'Module Type 1 (lying)\nlength in [6, 8] m   →  sampled {t1.length:.2f}',
        f'Module Type 2 (lying)\nlength in [8, 10] m  →  sampled {t2.length:.2f}',
        'Module Type 3 (standing)\nplan 3.3 x 3.3 m, H 9.9 m  (fixed)',
        f'Cantilever\next in [0.5, 2.0] m  →  sampled {cant.extension:.2f}',
    ]

    draw_module(axes[0], t1)
    draw_module(axes[1], t2)
    draw_module(axes[2], t3)
    draw_cantilever(axes[3], cant)

    for ax, title in zip(axes, titles):
        ax.set_xlim(-1, 12)
        ax.set_ylim(-1, 12)
        ax.set_aspect('equal')
        ax.grid(True, alpha=0.3, linestyle=':')
        ax.set_title(title, fontsize=10)
        ax.set_xlabel('x (m)')
        ax.set_ylabel('y (m)')

    plt.suptitle('Type Catalog — one sample per continuous type',
                 fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f'[saved] {out_path}')
    print(f'  Type 1 length     = {t1.length:.3f} m')
    print(f'  Type 2 length     = {t2.length:.3f} m')
    print(f'  Type 3 height     = {t3.height:.3f} m (fixed)')
    print(f'  Cantilever ext.   = {cant.extension:.3f} m')


# ============================================================
# 플로어패널 미리보기 (타입 1 두 변형 / 타입 2 / 타입 3)
# ============================================================
def visualize_panels(out_path):
    # 모듈 타입1/2 길이를 동시 샘플 (차이 >= 1m)
    L1, L2 = sample_type1_and_type2_lengths()

    # 타입1 패널: 모듈 1/2 중 하나 + 옵션 캔틸 → 폭 2개 선택
    base_mod_L = random.choice([L1, L2])
    cant1_L    = random.choice([0.0, sample_cantilever_extension()])
    total      = base_mod_L + cant1_L
    panel1_variants = make_panel_type1(total_length=total)

    # 타입2 패널: 모듈 타입1 기반 + 옵션 캔틸
    cant2_L = random.choice([0.0, sample_cantilever_extension()])
    panel2  = make_panel_type2(module_length=L1, cant_length=cant2_L)

    # 타입3 패널: 모듈 타입2 기반 + 옵션 캔틸
    cant3_L = random.choice([0.0, sample_cantilever_extension()])
    panel3  = make_panel_type3(module_length=L2, cant_length=cant3_L)

    fig, axes = plt.subplots(1, 3, figsize=(18, 6.5))

    # Panel 1: 두 변형을 위아래로 나란히
    p1a, p1b = panel1_variants
    p1a.x, p1a.y = 0.0, 0.0
    p1b.x, p1b.y = 0.0, p1a.plan_d + 1.2
    draw_panel(axes[0], p1a)
    draw_panel(axes[0], p1b)
    axes[0].set_title(
        'FloorPanel Type 1  (length 4~5 m continuous)\n'
        f'total = mod {base_mod_L:.2f} + cant {cant1_L:.2f} = {total:.2f} m\n'
        f'widths picked: {p1a.width}  &  {p1b.width}',
        fontsize=9,
    )

    # Panel 2
    draw_panel(axes[1], panel2)
    axes[1].set_title(
        'FloorPanel Type 2  (floor slab of Module Type 1)\n'
        f'{panel2.based_on}\n'
        f'L = {panel2.length:.2f},  W = {panel2.width}',
        fontsize=9,
    )

    # Panel 3
    draw_panel(axes[2], panel3)
    axes[2].set_title(
        'FloorPanel Type 3  (floor slab of Module Type 2)\n'
        f'{panel3.based_on}\n'
        f'L = {panel3.length:.2f},  W = {panel3.width}',
        fontsize=9,
    )

    for ax in axes:
        ax.set_xlim(-1, 14)
        ax.set_ylim(-1, 12)
        ax.set_aspect('equal')
        ax.grid(True, alpha=0.3, linestyle=':')
        ax.set_xlabel('x (m)')
        ax.set_ylabel('y (m)')

    plt.suptitle('Floor Panel Types', fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f'[saved] {out_path}')
    print(f'  Type 1  total = {total:.2f} m  →  widths {p1a.width} / {p1b.width}')
    print(f'  Type 2  L = {panel2.length:.2f} m  ({panel2.based_on})')
    print(f'  Type 3  L = {panel3.length:.2f} m  ({panel3.based_on})')


if __name__ == '__main__':
    base = Path(__file__).parent
    visualize_types(base / 'modules_preview.png')
    visualize_panels(base / 'panels_preview.png')
