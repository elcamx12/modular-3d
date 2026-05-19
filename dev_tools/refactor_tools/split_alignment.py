"""alignment_view.py 의 AlignmentCanvas 메소드를 PaintMixin / PickMixin 으로 분리.

PaintMixin: paintEvent + _draw_vline + _draw_hline + _draw_bbox_edges + _draw_f5_ghost
PickMixin : _hit_test_component + _pick_direction_edge + _pick_target_edge + _misaligned_set

class AlignmentCanvas(QLabel, PaintMixin, PickMixin):
"""
from pathlib import Path

# 옛 modular_3d/_refactor_tools 에선 parents[1]=modular_3d.
# 새 dev_tools/refactor_tools 에선 parents[2]/modular_3d.
ROOT = Path(__file__).resolve().parents[2] / 'modular_3d'
TARGET = ROOT / 'ui' / 'alignment_view.py'

src = TARGET.read_text(encoding='utf-8')
lines = src.split('\n')


def find_method_line(name: str, after: int = 0) -> int:
    target = f'    def {name}('
    for i in range(after, len(lines)):
        if lines[i].startswith(target):
            return i
    raise ValueError(f'method {name} not found after {after}')


pick_start = find_method_line('_hit_test_component')        # 249
draw_helpers_start = find_method_line('_draw_vline')        # 332
paint_event_start = find_method_line('paintEvent')           # 353
ghost_draw_start = find_method_line('_draw_f5_ghost')        # 570
mouse_press_start = find_method_line('mousePressEvent')      # 649

# Pick block: pick_start (_hit_test_component) ~ draw_helpers_start (_draw_vline 직전)
# Paint block: draw_helpers_start (_draw_vline) ~ mouse_press_start (mousePressEvent 직전)
# (paintEvent 와 _draw_f5_ghost 는 paint block 에 포함)

pick_block = lines[pick_start:draw_helpers_start]
paint_block = lines[draw_helpers_start:mouse_press_start]

PICK_HEADER = '''"""AlignmentCanvas 의 hit-test / edge picking Mixin.

[설계]
- AlignmentCanvas 는 본 Mixin 을 inherit. self.* 상태 (controller, _layer 등) 를 그대로 사용.
- 본 모듈은 캔버스 좌표 → 부재 ID / 모서리 ID 로의 변환만 담당.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt

from modular_3d.ui.alignment_helpers import (
    xy_bbox as _xy_bbox,
)


class AlignmentCanvasPickMixin:
    """클릭 좌표 ↔ 부재·모서리 매칭."""

'''

PAINT_HEADER = '''"""AlignmentCanvas 의 paintEvent + 보조 그리기 Mixin.

[설계]
- AlignmentCanvas 는 본 Mixin 을 inherit. self._world_to_screen / self._layer 등 상태 사용.
- 본 모듈은 paintEvent 와 직접 그리기 헬퍼만 담당.
"""
from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPolygonF

from modular_3d.model import (
    Module, FloorPanel, StructWall, CantileverBeam, CantileverSlab,
    MidBeam, MidColumn, ComponentType,
)
from modular_3d.ui.alignment_helpers import (
    xy_bbox as _xy_bbox,
    iter_component_rects as _iter_component_rects,
    visible_ids as _visible_ids,
)

# alignment_view.py 의 모듈 레벨 상수 (TYPE_COLORS, ALIGN_TOL 등) 은 같은 패키지
# 안에서 string 상수로 들고 다닐 수 있도록 동적 import — 순환 참조 회피.
def _av_consts():
    from modular_3d.ui import alignment_view as _av
    return _av


class AlignmentCanvasPaintMixin:
    """paintEvent + 직접 그리기 헬퍼."""

'''

pick_text = PICK_HEADER + '\n'.join(pick_block) + '\n'
paint_text = PAINT_HEADER + '\n'.join(paint_block) + '\n'

(ROOT / 'ui' / 'alignment_paint.py').write_text(paint_text, encoding='utf-8')
(ROOT / 'ui' / 'alignment_pick.py').write_text(pick_text, encoding='utf-8')

# alignment_view.py 본체에서 두 블록 제거 + Mixin import 추가 + 상속 변경
new_lines = list(lines)

# 역순 제거
del new_lines[draw_helpers_start:mouse_press_start]
del new_lines[pick_start:draw_helpers_start]

# class AlignmentCanvas(QLabel): → class AlignmentCanvas(QLabel, AlignmentCanvasPaintMixin, AlignmentCanvasPickMixin):
for i, line in enumerate(new_lines):
    if line.startswith('class AlignmentCanvas('):
        new_lines[i] = ('class AlignmentCanvas(QLabel, AlignmentCanvasPaintMixin, '
                        'AlignmentCanvasPickMixin):')
        break

# import 추가
imports_to_add = [
    'from modular_3d.ui.alignment_paint import AlignmentCanvasPaintMixin',
    'from modular_3d.ui.alignment_pick import AlignmentCanvasPickMixin',
]
last_from_idx = -1
for i, line in enumerate(new_lines[:90]):
    if line.startswith('from modular_3d') or line.startswith('from PyQt'):
        last_from_idx = i
if last_from_idx >= 0:
    insert_at = last_from_idx + 1
    for j, imp in enumerate(imports_to_add):
        new_lines.insert(insert_at + j, imp)

TARGET.write_text('\n'.join(new_lines), encoding='utf-8')

print(f'[OK] Pick 추출: {len(pick_block)} 줄 → ui/alignment_pick.py')
print(f'[OK] Paint 추출: {len(paint_block)} 줄 → ui/alignment_paint.py')
print(f'[OK] alignment_view.py: {len(lines)} → {len(new_lines)} 줄')
