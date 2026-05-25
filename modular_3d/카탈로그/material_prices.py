"""자재 단가 DB 로더.

[정책 2026-05-24 자재비]
- 단가는 코드가 아니라 같은 폴더의 material_prices.json 에 둔다(사용자가 코드를
  건드리지 않고 단가를 갱신할 수 있게). 본 모듈은 그 json 을 읽어 주는 얇은
  로더일 뿐이다.
- 재료비(자재비)만 다룬다. 노무비·경비·공기 비용은 팀원(공기 탭) 담당이라
  여기서 다루지 않는다.
- 단가 단위 규약: 강재 원/ton, 콘크리트 원/㎥, 데크슬래브 원/㎡.

[근거] 단가 값과 출처는 json 의 각 항목 '출처' 필드에 기록한다. 출처는
사용자 '단가' 폴더의 2025 하반기 표준시장단가 등 공인 자료.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_JSON_PATH = Path(__file__).with_name("material_prices.json")


def load_material_prices() -> dict:
    """material_prices.json 전체를 dict 로 로드. 실패 시 빈 dict."""
    try:
        with _JSON_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_unit_price(key: str) -> Optional[float]:
    """단가 키 → 단가(숫자). 없거나 미입력(null)이면 None.

    key 예: '강재_각형강관_SHS', '콘크리트_레미콘', '데크슬래브', '강재_H형강'.
    """
    data = load_material_prices()
    item = data.get(key)
    if not isinstance(item, dict):
        return None
    val = item.get("단가")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


__all__ = ["load_material_prices", "get_unit_price"]
