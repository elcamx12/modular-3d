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


# 표준 1층 높이 (수직3층모듈 등 관통 층수 추정용).
MODULE_FLOOR_HEIGHT_MM = 3000.0


def module_floor_span(comp):
    """모듈이 점유하는 [시작층, 끝층] (inclusive).

    - 일반 모듈: (f, f)
    - 수직3층모듈: height 로 관통 층수 추정.

    공정표·평가 어댑터 양쪽에서 사용 — 함정: 출력이 1층 인덱스가 아닌
    floor_index(0-base) 기준. 호출 측에서 환산 필요하면 별도 처리.
    """
    from modular_3d.model import Vertical3Module
    f0 = int(getattr(comp, "floor_index", 0))
    if isinstance(comp, Vertical3Module):
        h = float(comp.dimensions.get("height", 0.0))
        k = max(1, int(round(h / MODULE_FLOOR_HEIGHT_MM)))
        return f0, f0 + k - 1
    return f0, f0
