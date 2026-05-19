"""analysis 패키지 공용 유틸리티."""
from __future__ import annotations

from typing import Tuple
import numpy as np


def round_key(p: np.ndarray) -> Tuple[int, int, int]:
    """좌표를 1 mm 단위 정수 키로 반올림 — 노드 병합/접합부 매칭용."""
    return (int(round(float(p[0]))),
            int(round(float(p[1]))),
            int(round(float(p[2]))))
