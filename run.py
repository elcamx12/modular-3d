"""
실행 런처 — 더블클릭으로 실행
venv python을 자동으로 찾아서 src 스크립트를 실행합니다.
"""

import subprocess
from pathlib import Path

BASE = Path(__file__).parent
VENV_PYTHON = BASE / 'venv' / 'Scripts' / 'python.exe'
SRC = BASE / 'src'

SCRIPTS = [
    ('outline_generator', '외곽선 생성 (1단계)'),
    ('catalog',           '카탈로그 생성 (2단계)'),
    ('run_all',           '전체 배치 실행 (3단계)'),
    ('train_ppo_blocks',  'PPO 블록 배치 학습 (4단계)'),
    ('placer',            '배치 미리보기'),
]


def run_script(name):
    script = SRC / f'{name}.py'
    if not script.exists():
        print(f'  [오류] {script} 파일이 없습니다.')
        return False
    print(f'\n=== {name}.py 실행 ===\n')
    result = subprocess.run([str(VENV_PYTHON), str(script)])
    if result.returncode != 0:
        print(f'\n  [오류] {name}.py 실행 실패 (코드 {result.returncode})')
        return False
    return True


def main():
    if not VENV_PYTHON.exists():
        print(f'[오류] venv python을 찾을 수 없습니다:\n  {VENV_PYTHON}')
        input('\n아무 키나 누르면 종료...')
        return

    while True:
        print('\n' + '=' * 40)
        print('  모듈러 배치 최적화 런처')
        print('=' * 40)
        for i, (name, desc) in enumerate(SCRIPTS, 1):
            print(f'  {i}. {desc}')
        print(f'  6. 파이프라인 전체 (1→2→3)')
        print(f'  0. 종료')
        print()

        choice = input('번호 입력: ').strip()

        if choice == '0':
            break
        elif choice == '6':
            for name, desc in SCRIPTS[:3]:
                if not run_script(name):
                    break
                print()
            print('\n=== 파이프라인 완료 ===')
        elif choice.isdigit() and 1 <= int(choice) <= len(SCRIPTS):
            run_script(SCRIPTS[int(choice) - 1][0])
        else:
            print('  잘못된 입력입니다.')

    print('\n종료합니다.')


if __name__ == '__main__':
    main()
