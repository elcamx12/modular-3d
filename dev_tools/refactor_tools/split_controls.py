"""controls.py 의 F5 / F6 메소드 블록을 Mixin 클래스로 분리.

전략 (Mixin pattern):
- F5Mixin: set_f5_dock 부터 _f5_on_delete 까지 (L1395-L1991, 597 줄)
- F6Mixin: _open_alignment_view 부터 set_quantity_reports 까지 (L1993-L2433, 441 줄)
- Controller 본체: 두 Mixin 을 inherit → self.x 결합 그대로 살아있음
- Mixin 들은 controls.py 의 내부 helper (DEFAULT_DIMS, TYPE_NAMES, _create_component 등)
  를 그대로 import 해서 사용

라인 번호는 본 스크립트 실행 시점의 controls.py 기준. 한 번만 실행 후 폐기.
"""
from pathlib import Path

# 옛 modular_3d/_refactor_tools 에선 parents[1]=modular_3d.
# 새 dev_tools/refactor_tools 에선 parents[2]/modular_3d.
ROOT = Path(__file__).resolve().parents[2] / 'modular_3d'
CONTROLS = ROOT / 'controls.py'

src = CONTROLS.read_text(encoding='utf-8')
lines = src.split('\n')


def find_method_line(name: str) -> int:
    """def name 형태의 첫 발견 라인 (0-based)."""
    target = f'    def {name}('
    for i, line in enumerate(lines):
        if line.startswith(target):
            return i
    raise ValueError(f'method {name} not found')


# F5 영역: set_f5_dock 부터 _f5_on_delete (마지막 메소드의 다음 def 시작 1줄 전까지)
f5_start = find_method_line('set_f5_dock')
# F5 의 마지막 메소드 _f5_on_delete 의 끝 = 다음 def 시작 라인의 직전
f6_start_line = find_method_line('_open_alignment_view')

# F6 끝 = 다음 def 가 _cancel_placement (F5/F6 가 아닌 일반)
f6_end_marker = find_method_line('_cancel_placement')

# F5 블록: f5_start ~ (f6_start_line - 1)
# F6 블록: f6_start_line ~ (f6_end_marker - 1)

# 블록 직전에 있을 수 있는 빈 줄·주석은 메소드에 함께 묶어 유지.
# 다만 # ── 섹션 ── 주석들은 Mixin 안에 가두지 말고 controls.py 에 남기지 않는다.

# F5 직전의 섹션 주석을 함께 잡기 위해 한 줄 위로 (빈줄/주석 처리)
# 현재 형식상 set_f5_dock 직전에는 다른 메소드(_restore_children) 의 끝이 있다.
# Mixin 으로 옮길 때 빈줄 1개를 시작에 두는 정도로 충분.

f5_block = lines[f5_start:f6_start_line]
f6_block = lines[f6_start_line:f6_end_marker]

# Mixin 헤더는 다음과 같이 작성:
F5_HEADER = '''"""controls.py 의 F5 (도크 배치 모드) 관련 메소드 분리.

[설계]
- F5Mixin 은 Controller 가 inherit 한다 (Mixin 패턴).
- Controller 의 모든 self.* 상태 (self._scene, self._viewer, self._snap 등) 를 그대로 사용.
- 본 모듈 자체는 인스턴스화하지 않는다.

[원칙]
- 본 파일에는 F5 도크 패널 관련 로직만 둔다.
- 다른 메소드를 추가할 때는 self.* 결합도가 너무 높지 않은지 검토.
"""
from __future__ import annotations

from typing import List

import numpy as np

from modular_3d.model import ComponentType
from modular_3d.ui.controls_geom import create_component as _create_component
from modular_3d.render.mesh_builder import build_ghost_component_mesh

# TYPE_NAMES 는 controls.py 에 정의 — 순환 import 회피 위해 함수 안에서 import.


class F5Mixin:
    """F5 배치 모드 메소드 모음 — Controller 가 inherit."""

'''

F6_HEADER = '''"""controls.py 의 F6 (구조해석 도크) 관련 메소드 분리.

[설계]
- F6Mixin 은 Controller 가 inherit 한다 (Mixin 패턴).
- Controller 의 self.* 상태 (self._scene, self._viewer, self._dim_panel 등) 를 그대로 사용.
- 본 모듈 자체는 인스턴스화하지 않는다.

[원칙]
- 본 파일에는 F6 도크 패널 + 해석 결과 시각화 로직만 둔다.
"""
from __future__ import annotations

import numpy as np


class F6Mixin:
    """F6 해석 모드 메소드 모음 — Controller 가 inherit."""

'''

# Mixin 본문은 메소드들의 들여쓰기(4칸)를 그대로 유지하면 클래스 메소드가 됨.
f5_text = F5_HEADER + '\n'.join(f5_block) + '\n'
f6_text = F6_HEADER + '\n'.join(f6_block) + '\n'

(ROOT / 'controls_f5.py').write_text(f5_text, encoding='utf-8')
(ROOT / 'controls_f6.py').write_text(f6_text, encoding='utf-8')

# controls.py 에서 두 블록 제거 + Mixin import + class 상속 변경
# 1) class Controller: → class Controller(F5Mixin, F6Mixin):
# 2) F5/F6 블록 제거 (역순으로 제거해야 라인 번호 유지)
# 3) F5Mixin/F6Mixin import 추가

new_lines = list(lines)

# 역순 제거
del new_lines[f6_start_line:f6_end_marker]
del new_lines[f5_start:f6_start_line]

# 클래스 헤더 변경
for i, line in enumerate(new_lines):
    if line.startswith('class Controller:'):
        new_lines[i] = 'class Controller(F5Mixin, F6Mixin):'
        break
    if line.startswith('class Controller('):
        # 이미 inherit 가 있다면 건드리지 않음
        break

# import 추가 — 마지막 from import 다음에 배치
imports_to_add = [
    'from modular_3d.controls_f5 import F5Mixin',
    'from modular_3d.controls_f6 import F6Mixin',
]
# 마지막 from 줄 찾기
last_from_idx = -1
for i, line in enumerate(new_lines[:60]):
    if line.startswith('from modular_3d') or line.startswith('from PyQt'):
        last_from_idx = i
if last_from_idx >= 0:
    insert_at = last_from_idx + 1
    for j, imp in enumerate(imports_to_add):
        new_lines.insert(insert_at + j, imp)

CONTROLS.write_text('\n'.join(new_lines), encoding='utf-8')

print(f'[OK] F5 추출: {len(f5_block)} 줄 → controls_f5.py')
print(f'[OK] F6 추출: {len(f6_block)} 줄 → controls_f6.py')
print(f'[OK] controls.py: {len(lines)} → {len(new_lines)} 줄')
