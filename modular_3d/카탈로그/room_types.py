"""실(공간) 용도 카탈로그 — 아파트 실용도 + KDS 41 12 00 활하중 연계.

[정책 2026-05-24 디자인 탭 2분리 — 2단계 실 배치]
- 실 배치 기능에서 사용자가 그린 실 영역에 붙이는 '용도' 목록의 단일 진실 원천.
- 각 용도는 KDS 41 12 00(건축물 설계하중) 등분포활하중 분류와 매핑한다 — 5단계
  하중 정의에서 이 live_load_kn_m2 값을 구조해석으로 전달해 자연 연계한다.

[활하중 값 출처 — KDS 41 12 00 표 3.2-1 (주거)]
- 주택(거실·침실 등 주거 실): 2.0 kN/m²
- 발코니: 인접 실 활하중의 1.5배(최대 5.0) → 주거 기준 3.0 채택
- 공동주택 공용실 / 로비·복도(공용): 5.0 kN/m²
- 계단(주거 전용): 2.0, (공용 계단): 5.0 — 본 목록은 세대 내 계단 기준 2.0
- 지붕(옥상): 1.0 kN/m²
※ 값은 5단계(하중 정의)에서 KDS 원문으로 정밀 확정·검증한다. 현재 값은 표준
  기반 잠정값이며, 본 파일을 단일 출처로 두어 5단계에서 한 곳만 갱신하면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class RoomType:
    """실 용도 1종.

    key: 내부 식별자(직렬화·매핑용, 변경 금지).
    name: 화면 표시명(한글).
    kds_category: KDS 41 12 00 등분포활하중 분류명(연계 근거).
    live_load_kn_m2: 등분포활하중(kN/m²) — 구조해석으로 전달(KDS 41 12 00).
    sdl_kn_m2: 부가 고정하중(마감 등, kN/m²) — 슬래브 자중과 별도로 더해지는
        마감·천장·설비 등가 고정하중. 실무 통용 마감 구성 합산값(사용자 수정 가능).
    color: 2D/3D 실 색면 기본색 (R, G, B) 0~255.
    """
    key: str
    name: str
    kds_category: str
    live_load_kn_m2: float
    sdl_kn_m2: float
    color: Tuple[int, int, int]


# ── 아파트 실용도 목록 (KDS 41 12 00 연계) ──────────────────────
# 순서 = 드롭다운 표시 순서. 거주 빈도가 높은 실을 위에 둔다.
# 컬럼: key, name, kds_category, 활하중(kN/m²), 부가고정하중 SDL(kN/m²), color
# SDL 기본값 근거: 주거 온돌·바닥 마감 1.5 / 욕실 방수+무근+타일 2.0 /
#   발코니·복도·공용·계단 1.5 / 지붕 방수+보호모르타르+단열 2.5. (실무 통용, 수정 가능)
ROOM_TYPES: List[RoomType] = [
    RoomType('living',    '거실',          '주택(주거 실)',         2.0, 1.5, (120, 180, 240)),
    RoomType('bedroom',   '침실',          '주택(주거 실)',         2.0, 1.5, (160, 210, 160)),
    RoomType('kitchen',   '주방',          '주택(주거 실)',         2.0, 1.5, (245, 200, 120)),
    RoomType('dining',    '식당',          '주택(주거 실)',         2.0, 1.5, (240, 175, 140)),
    RoomType('bathroom',  '욕실/화장실',   '주택(주거 실)',         2.0, 2.0, (140, 215, 220)),
    RoomType('balcony',   '발코니',        '발코니(인접실 1.5배)',  3.0, 1.5, (200, 225, 180)),
    RoomType('entrance',  '현관',          '주택(주거 실)',         2.0, 1.5, (210, 195, 170)),
    RoomType('dressroom', '드레스룸',      '주택(주거 실)',         2.0, 1.5, (205, 175, 220)),
    RoomType('utility',   '다용도실',      '주택(주거 실)',         2.0, 1.5, (190, 200, 210)),
    RoomType('storage',   '창고/팬트리',   '주택(주거 실)',         2.0, 1.5, (175, 175, 175)),
    RoomType('corridor',  '복도(공용)',    '로비 및 복도(공동주택)', 5.0, 1.5, (225, 205, 150)),
    RoomType('stairs',    '계단',          '계단(주거)',            2.0, 1.5, (200, 200, 160)),
    RoomType('common',    '공용실',        '공동주택 공용실',       5.0, 1.5, (220, 180, 180)),
    RoomType('roof',      '옥상',          '지붕',                  1.0, 2.5, (200, 220, 235)),
]

# ── 옥상(지붕) 자동 적용값 ───────────────────────────────────────
# 옥상은 사용자가 직접 'roof' 실을 그리지 않고, 구조 최상단 슬래브를 자동 검출해
# 적용한다(코어 옥탑 슬래브 제외). 검출된 옥상 슬래브엔 실 용도와 무관하게 아래
# 지붕 활하중을 강제하고, 부가 고정하중은 옥상 전용값을 쓴다.
ROOF_LIVE_LOAD_KN_M2 = 1.0   # 지붕 활하중 (KDS 41 12 00)
ROOF_SDL_KN_M2 = 2.5         # 옥상 마감(방수+보호모르타르+단열) 부가 고정하중

# 실을 안 그린 빈 슬래브 영역 기본 활하중(주거 가정).
DEFAULT_LIVE_LOAD_KN_M2 = 2.0
DEFAULT_SDL_KN_M2 = 1.5

# key → RoomType 빠른 조회.
ROOM_TYPE_BY_KEY = {rt.key: rt for rt in ROOM_TYPES}

# 기본 용도(다이얼로그 초기 선택) — 가장 흔한 거실.
DEFAULT_ROOM_TYPE_KEY = 'living'


def get_room_type(key: str) -> Optional[RoomType]:
    """key 로 RoomType 조회. 없으면 None."""
    return ROOM_TYPE_BY_KEY.get(key)


def room_type_names() -> List[Tuple[str, str]]:
    """드롭다운용 (key, 표시명) 목록 — 표시 순서 유지."""
    return [(rt.key, rt.name) for rt in ROOM_TYPES]


__all__ = [
    'RoomType', 'ROOM_TYPES', 'ROOM_TYPE_BY_KEY', 'DEFAULT_ROOM_TYPE_KEY',
    'get_room_type', 'room_type_names',
    'ROOF_LIVE_LOAD_KN_M2', 'ROOF_SDL_KN_M2',
    'DEFAULT_LIVE_LOAD_KN_M2', 'DEFAULT_SDL_KN_M2',
]
