"""정의 가져오기 배치용 그룹 변환 헬퍼.

[정책 2026-05-24 디자인 탭 2분리 — 4단계 가져오기 배치]
- 저장된 정의(단일층 부재 묶음 + 실)를 배치 탭에 가져올 때, 그룹 전체를
  기준점(pivot) 중심으로 90° 단위 회전한 뒤 기준점이 마우스(target)에 오도록
  평행이동한다. 캔버스 고스트와 컨트롤러 확정이 동일 변환을 공유한다.
- 90/180/270° 회전만 지원(부재 rotation 속성과 일치).
"""
from __future__ import annotations

from typing import Tuple


def rotate_xy_deg(x: float, y: float, deg: int) -> Tuple[float, float]:
    """원점 기준 90° 단위 회전(반시계). deg 는 0/90/180/270 로 정규화."""
    d = int(deg) % 360
    if d == 90:
        return -y, x
    if d == 180:
        return -x, -y
    if d == 270:
        return y, -x
    return x, y


def transform_point(px: float, py: float,
                    pivot: Tuple[float, float], rot_deg: int,
                    target: Tuple[float, float]) -> Tuple[float, float]:
    """점(px,py)을 pivot 중심 rot_deg 회전 후, pivot→target 평행이동."""
    rx, ry = rotate_xy_deg(px - pivot[0], py - pivot[1], rot_deg)
    return target[0] + rx, target[1] + ry


__all__ = ["rotate_xy_deg", "transform_point"]
