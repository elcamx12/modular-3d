"""포맷 유틸 — 라벨·통화·인덱스 등 ui/transport/analysis 공용 함수.

[이력]
- 2026-05-27: transport/adapter.py 와 ui/analysis_panel.py 의 동명 `_alpha_label`
  중복을 통합. 두 구현은 알고리즘이 달랐지만 0~1999 범위에서 결과 동일 검증 완료.
"""
from __future__ import annotations


def won(n: float) -> str:
    """원 단위 통화 라벨 — "1,234원" 형식. 천 단위 콤마 + "원" 접미.

    음수도 그대로 표시. 호출 측에서 음수 처리.
    """
    return f"{int(round(float(n))):,}원"


def alpha_label(i: int) -> str:
    """0→a, 1→b, … 25→z, 26→aa, 27→ab … 컴포넌트 위치 인덱스 라벨.

    함정: 입력이 음수면 무한 루프 — 호출 측에서 i ≥ 0 보장.
    """
    s = ''
    i = int(i)
    while True:
        s = chr(ord('a') + (i % 26)) + s
        i = i // 26 - 1
        if i < 0:
            return s
