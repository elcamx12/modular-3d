"""합성 회귀 시나리오 (b)·(c) 생성.

(b) synth_b: 단층 모듈 2×2 그리드 + 캔틸레버 1개 + 벽패널 1개 + 3층
(c) synth_c: 수직 3층 모듈 1개 + 단층 모듈 1개 + 6층 (= 수직 2 + 단층 6)

산출물: regression_scenes/synth_b.json, regression_scenes/synth_c.json
"""
from pathlib import Path
import sys

# 프로젝트 루트(my_project)를 sys.path 에 추가
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from modular_3d.model import Scene, ComponentType  # noqa: E402
from modular_3d.model.multi_floor import create_multi_floor_group  # noqa: E402
from modular_3d.io.scene_io import save_scene  # noqa: E402

OUT_DIR = Path(__file__).parent / 'regression_scenes'
OUT_DIR.mkdir(exist_ok=True)


def build_synth_b() -> int:
    """단층 모듈 1개 + 3층 — 최소 유효 시나리오 (단층 회귀 검증용)."""
    scene = Scene()
    n_floors = 3
    create_multi_floor_group(
        scene, ComponentType.MODULE,
        base_position=np.array([0.0, 0.0, 0.0]),
        dims={'width': 3400.0, 'depth': 3400.0, 'height': 3400.0},
        rotation=0, anchor=0, n_floors=n_floors,
    )
    out = OUT_DIR / 'synth_b.json'
    n = save_scene(scene, n_floors, str(out))
    print(f'[synth_b] saved {n} components → {out}')
    return n


def build_synth_c() -> int:
    """수직 3층 모듈 1 + 단층 모듈 1 + 6층 (수직 2 인스턴스 + 단층 6 인스턴스)."""
    scene = Scene()
    n_floors = 6

    pitch = 3400.0 + 20.0

    # 단층 모듈 (group 1)
    create_multi_floor_group(
        scene, ComponentType.MODULE,
        base_position=np.array([0.0, 0.0, 0.0]),
        dims={'width': 3400.0, 'depth': 3400.0, 'height': 3400.0},
        rotation=0, anchor=0, n_floors=n_floors,
    )

    # 수직 3층 모듈 (group 2) — 옆 칸
    create_multi_floor_group(
        scene, ComponentType.VERTICAL_MODULE,
        base_position=np.array([pitch, 0.0, 0.0]),
        dims={'width': 3400.0, 'depth': 3400.0, 'height': 10240.0},
        rotation=0, anchor=0, n_floors=n_floors,
    )

    out = OUT_DIR / 'synth_c.json'
    n = save_scene(scene, n_floors, str(out))
    print(f'[synth_c] saved {n} components → {out}')
    return n


def main():
    print('=== Building synthetic regression scenes ===')
    nb = build_synth_b()
    nc = build_synth_c()
    print(f'[OK] synth_b={nb} comps, synth_c={nc} comps')


if __name__ == '__main__':
    main()
