"""
부재 카탈로그 생성기  (Step 3a)

한 카탈로그(catalog) = "치수 한 세트"
- 평면 하나에 이 카탈로그를 적용하면 그 치수를 반복 사용하여 배치
- 500 평면 × 30 카탈로그 = 15,000 배치 실험의 기반

카탈로그 구성:
  - Module Type 3 : 3.3×3.3, H 9.9m (항상 고정, 선언적)
  - Horizontal modules : 1개 또는 2개
  - Cantilever extension : 1개 값 (0.5~2.0m)
  - Floor panel T1 : 길이 4~5m (1개), 폭 2개 (3.0~3.5 중 최적)
  - Floor panel T2, T3 : 모듈로부터 파생 (장스팬 = 모듈길이 + 캔틸)

모듈 선택 규칙 (새 버전):
  [1-mod]  T3 + 1개 추가 모듈
           추가 모듈 길이 L ≤ 7.9m  (∵ 9.9와 2m+ 차이)
  [2-mod]  T3 + 2개 추가 모듈 (L1 from T1 범위, L2 from T2 범위)
           {9.9, L1, L2} pairwise ≥ 1m 차이
           → L2 ∈ [8.0, 8.9]
           → L1 ∈ [6.0, L2 - 1]  (최대 7.9)
"""

import json
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

from modules import (
    TYPE1_LENGTH_RANGE,
    TYPE2_LENGTH_RANGE,
    TYPE3_STAND_HEIGHT,
    CANTILEVER_RANGE,
    FLOOR_PANEL_LENGTH_RANGE,
    best_two_panel_widths,
)


# ============================================================
# 규칙 상수
# ============================================================
T3_LENGTH        = TYPE3_STAND_HEIGHT  # 9.9
MIN_PAIRWISE_GAP = 1.0                 # 2-mod 케이스의 pairwise 최소 차이
ONE_MOD_MAX_LEN  = 7.9                 # 1-mod 케이스 최대 길이
NUM_CATALOGS     = 30                  # 생성 개수
SEED             = 20260412


# ============================================================
# 카탈로그 데이터 클래스
# ============================================================
@dataclass
class ModuleCatalog:
    id: int
    case: str                              # '1-mod' or '2-mod'
    horizontal_modules: List[float]        # 추가 수평 모듈 길이 (1 또는 2개)
    cantilever_ext: float                  # 캔틸레버 돌출 (m)
    panel1_length: float                   # 플로어패널 T1 길이 (m)
    panel1_widths: Tuple[float, float]     # 플로어패널 T1 폭 2개
    panel1_ref_total: float                # 폭 선정 기준이 된 total_length
    panel1_width_devs: Tuple[float, float] # 각 폭의 오차

    # T3는 불변이라 필드에 적진 않지만 접근자 제공
    @property
    def t3(self) -> dict:
        return {'plan': (3.3, 3.3), 'height': T3_LENGTH}

    def summary(self) -> str:
        mods_str = ', '.join(f'{L:.2f}' for L in self.horizontal_modules)
        w1, w2 = self.panel1_widths
        d1, d2 = self.panel1_width_devs
        return (
            f"[Catalog {self.id:02d}]  case={self.case}\n"
            f"  T3 module         : 3.3 x 3.3  (H 9.9 m, fixed)\n"
            f"  horizontal mods   : [{mods_str}] m\n"
            f"  cantilever ext.   : {self.cantilever_ext:.2f} m\n"
            f"  panel T1 length   : {self.panel1_length:.2f} m\n"
            f"  panel T1 widths   : {w1} (dev {d1:.2f}) / {w2} (dev {d2:.2f})\n"
            f"  panel1 ref total  : {self.panel1_ref_total:.2f} m"
        )


# ============================================================
# 샘플러
# ============================================================
def _pick_1_module() -> List[float]:
    """1-mod 케이스: 추가 모듈 1개, 길이 ≤ 7.9m"""
    lo = TYPE1_LENGTH_RANGE[0]              # 6.0
    hi = min(TYPE1_LENGTH_RANGE[1], ONE_MOD_MAX_LEN)  # 7.9
    return [random.uniform(lo, hi)]


def _pick_2_modules() -> Optional[List[float]]:
    """
    2-mod 케이스: T1 + T2 각 1개
    {9.9, L1, L2} pairwise >= 1m
      - |9.9 - L2| >= 1 → L2 ∈ [8, 8.9]
      - |L1 - L2| >= 1  → L1 ≤ L2 - 1
    """
    L2 = random.uniform(TYPE2_LENGTH_RANGE[0],
                        min(TYPE2_LENGTH_RANGE[1], T3_LENGTH - MIN_PAIRWISE_GAP))  # [8, 8.9]
    L1_hi = min(TYPE1_LENGTH_RANGE[1], L2 - MIN_PAIRWISE_GAP)
    if L1_hi < TYPE1_LENGTH_RANGE[0]:
        return None
    L1 = random.uniform(TYPE1_LENGTH_RANGE[0], L1_hi)
    return sorted([L1, L2])


def sample_catalog(cat_id: int, case: Optional[str] = None) -> ModuleCatalog:
    """
    case: '1-mod' / '2-mod' / None(랜덤 50:50)
    """
    if case is None:
        case = random.choice(['1-mod', '2-mod'])

    # 1. 수평 모듈 길이
    if case == '1-mod':
        mods = _pick_1_module()
    else:
        mods = None
        while mods is None:
            mods = _pick_2_modules()

    # 2. 캔틸레버 길이
    cant = random.uniform(*CANTILEVER_RANGE)

    # 3. 플로어패널 T1 기준 total = 대표 모듈 길이 (+ 캔틸)
    base_len = max(mods)
    use_cant = random.random() < 0.5
    ref_total = base_len + (cant if use_cant else 0.0)

    # 4. 패널 T1 길이
    panel1_L = random.uniform(*FLOOR_PANEL_LENGTH_RANGE)

    # 5. 패널 T1 폭 2개 선택 (6개 후보 중 오차 최소 2개)
    picked = best_two_panel_widths(ref_total)  # [(W, N, dev), (W, N, dev)]
    w1, w2 = picked[0][0], picked[1][0]
    d1, d2 = picked[0][2], picked[1][2]

    return ModuleCatalog(
        id=cat_id,
        case=case,
        horizontal_modules=mods,
        cantilever_ext=cant,
        panel1_length=panel1_L,
        panel1_widths=(w1, w2),
        panel1_ref_total=ref_total,
        panel1_width_devs=(d1, d2),
    )


def generate_catalogs(n: int = NUM_CATALOGS, seed: int = SEED,
                      mix_ratio: float = 0.5) -> List[ModuleCatalog]:
    """
    n: 카탈로그 개수
    mix_ratio: 1-mod 케이스 비율 (0.5 = 절반)
    """
    random.seed(seed)
    n_one = round(n * mix_ratio)
    n_two = n - n_one
    cases = ['1-mod'] * n_one + ['2-mod'] * n_two
    random.shuffle(cases)

    catalogs = []
    for i, c in enumerate(cases):
        catalogs.append(sample_catalog(i, case=c))
    return catalogs


# ============================================================
# 저장 / 출력
# ============================================================
def save_catalogs_json(catalogs: List[ModuleCatalog], path: Path):
    data = []
    for c in catalogs:
        d = asdict(c)
        # tuple → list 변환 (JSON 직렬화용)
        d['panel1_widths']     = list(d['panel1_widths'])
        d['panel1_width_devs'] = list(d['panel1_width_devs'])
        data.append(d)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def print_catalogs(catalogs: List[ModuleCatalog]):
    for c in catalogs:
        print(c.summary())
        print()
    # 케이스 분포
    n1 = sum(1 for c in catalogs if c.case == '1-mod')
    n2 = sum(1 for c in catalogs if c.case == '2-mod')
    print(f'[summary] total={len(catalogs)}  1-mod={n1}  2-mod={n2}')


if __name__ == '__main__':
    cats = generate_catalogs()
    print_catalogs(cats)

    out = Path(__file__).parent / 'catalogs.json'
    save_catalogs_json(cats, out)
    print(f'\n[saved] {out}')
