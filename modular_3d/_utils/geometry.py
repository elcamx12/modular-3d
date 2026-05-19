"""기하 유틸 — 좌표 박스·범위 계산 등 ui/render 공용 함수.

[이력]
- 2026-05-18: render → ui 순환 의존 차단을 위해 ui/alignment_helpers.py 의
  xy_bbox 를 본 중립 모듈로 이동. alignment_helpers 는 본 모듈을 re-export.
"""
from __future__ import annotations


def xy_bbox(comp):
    """부재의 XY 바운딩박스 (x0, y0, x1, y1).

    comp 가 `get_bounding_box()` 메서드를 노출하는 모든 객체에 대해 동작.
    좌표는 mm 단위. z 는 무시하고 x/y 만 반환.
    """
    bmin, bmax = comp.get_bounding_box()
    return float(bmin[0]), float(bmin[1]), float(bmax[0]), float(bmax[1])
