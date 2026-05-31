"""골든 마스터 회귀 시스템 (M1).

목적:
  UI 마이그레이션 (vispy → three.js) 전후로 *모델 상태가 완전히 동일* 한지
  자동 검증. 사용자의 시각·체험 검증과 분담되는 *AI 자동 검증* 영역.

원리:
  scene*.json 시나리오 파일을 `load_scene()` 으로 읽어 Scene 객체를 복원하고,
  `scene_to_state_dict()` 로 정규화 dict 를 생성. 그 dict 를 *baseline JSON*
  으로 저장해두면, 마이그레이션 후 같은 절차로 다시 dump 했을 때 *diff 0*
  여야 한다는 보증을 자동화할 수 있다.

사용법:
  --update : baseline 갱신 (의도된 변경 후 or 첫 실행). 기존 baseline 덮어쓰기.
  (옵션 없음) : 검증 모드. 현재 dump 와 baseline 비교. 차이 발견 시 exit 1.

baseline 위치:
  modular_3d/UI_마이그레이션/baselines/<scene_filename>.baseline.json

추가 시나리오:
  SCENES 리스트에 절대 경로 추가. 사용자가 손으로 만든 신규 .case.json 도 OK.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 패키지 import 경로 확보
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modular_3d.io.scene_io import load_scene, scene_to_state_dict  # noqa: E402

# ── 검증 대상 시나리오 ──────────────────────────────────
DESKTOP = Path('C:/Users/이건영/Desktop/종설')

SCENES: list[Path] = [
    DESKTOP / 'scene.json',
    DESKTOP / 'scene0-1.json',
    DESKTOP / 'scene1.json',
    DESKTOP / 'scene2.json',
    DESKTOP / 'scene3.json',
    DESKTOP / 'scene4.json',
    DESKTOP / 'scene5.json',
    DESKTOP / 'scene_merge_test.json',
    DESKTOP / 'scene4_tmp_merged.json',
]

BASELINE_DIR = PROJECT_ROOT / 'modular_3d' / 'UI_마이그레이션' / 'baselines'


def normalize(state: dict) -> dict:
    """비교용 정규화 — id 발급 순서가 deterministic 이라 그대로 두되,
    부동소수 jitter 가 비교를 깨지 않게 모든 float 를 0.001 정밀도로 라운드.
    """
    def _round(o: Any) -> Any:
        if isinstance(o, float):
            return round(o, 3)
        if isinstance(o, list):
            return [_round(x) for x in o]
        if isinstance(o, dict):
            return {k: _round(v) for k, v in o.items()}
        return o
    return _round(state)


def dump_scene(scene_path: Path) -> dict:
    """scene.json 한 개를 load 하고 정규화된 state dict 반환."""
    scene, n_floors = load_scene(str(scene_path))
    state = scene_to_state_dict(scene, n_floors)
    return normalize(state)


def diff_json(a: Any, b: Any, path: str = '') -> list[str]:
    """두 정규화 dict 사이 diff. 경로별 차이 문자열 list 반환."""
    diffs: list[str] = []
    if type(a) != type(b):
        diffs.append(f'{path}: type {type(a).__name__} != {type(b).__name__}')
        return diffs
    if isinstance(a, dict):
        keys = sorted(set(a.keys()) | set(b.keys()))
        for k in keys:
            sub = f'{path}.{k}' if path else k
            if k not in a:
                diffs.append(f'{sub}: 추가됨 ({b[k]!r})')
            elif k not in b:
                diffs.append(f'{sub}: 제거됨 ({a[k]!r})')
            else:
                diffs.extend(diff_json(a[k], b[k], sub))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f'{path}: 길이 {len(a)} → {len(b)}')
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diffs.extend(diff_json(x, y, f'{path}[{i}]'))
    else:
        if a != b:
            diffs.append(f'{path}: {a!r} != {b!r}')
    return diffs


def run(update: bool) -> int:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    fails: list[str] = []
    successes: list[str] = []
    missing: list[Path] = []

    for scene_path in SCENES:
        if not scene_path.exists():
            print(f'  [SKIP] {scene_path.name} — 파일 없음')
            missing.append(scene_path)
            continue

        try:
            state = dump_scene(scene_path)
        except Exception as e:
            fails.append(f'{scene_path.name}: load 실패 — {e}')
            continue

        baseline_path = BASELINE_DIR / f'{scene_path.stem}.baseline.json'

        if update or not baseline_path.exists():
            baseline_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding='utf-8')
            print(f'  [WRITE] {baseline_path.name} '
                  f'(components={len(state.get("components", []))}, '
                  f'rooms={len(state.get("rooms", []))})')
            successes.append(scene_path.name)
            continue

        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
        diffs = diff_json(baseline, state)
        if diffs:
            fails.append(f'{scene_path.name}: 차이 {len(diffs)}건')
            for d in diffs[:20]:
                fails.append(f'    {d}')
            if len(diffs) > 20:
                fails.append(f'    ... (+{len(diffs) - 20}건 더)')
        else:
            print(f'  [OK] {scene_path.name} — diff 0')
            successes.append(scene_path.name)

    # 요약
    print()
    print('=' * 60)
    if update:
        print(f'baseline 갱신 완료: {len(successes)}건 / 누락 {len(missing)}건')
        if missing:
            print('누락 파일:')
            for m in missing:
                print(f'  - {m}')
        return 0

    if fails:
        print(f'검증 실패: {len(fails)}건')
        for line in fails:
            print(f'  {line}')
        return 1

    print(f'검증 통과: {len(successes)}건 (모든 시나리오 baseline 일치, '
          f'diff 0)')
    return 0


def main():
    ap = argparse.ArgumentParser(description='골든 마스터 회귀 검증')
    ap.add_argument('--update', action='store_true',
                    help='baseline 갱신 (기존 덮어쓰기)')
    args = ap.parse_args()
    sys.exit(run(args.update))


if __name__ == '__main__':
    main()
