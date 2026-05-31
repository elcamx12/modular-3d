"""실(Room) 데이터 모델 — 2D 폴리곤으로 정의되는 공간.

[정책 2026-05-24 디자인 탭 2분리 — 2단계 실 배치]
- 실 = 사용자가 2D 평면뷰에서 점을 찍어 만든 폴리곤 영역 + 이름 + 용도.
- 부재(Component)와 별개의 경량 객체 — 메시 빌더/스냅 토폴로지에 들어가지 않고,
  2D 색면·라벨 + 3D 슬래브 위 반투명 색면으로만 표현된다(이번 단계 범위).
- 용도(room_type)는 카탈로그/room_types.ROOM_TYPES 의 key. 5단계에서 용도별
  활하중(KDS 41 12 00)을 구조해석으로 전달한다.
- 좌표 단위 = mm (부재·씬 전체 정책과 동일). 폴리곤은 월드 xy 평면 점 목록.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class Room:
    """실 1건 — 폴리곤(월드 xy mm) + 용도 + 소속 층.

    [정책 2026-05-24] 별도 이름은 두지 않는다 — 용도(room_type)가 곧 구분이다
    (거실/침실 등). 표시 라벨은 용도명을 그대로 쓴다.

    [5단계 하중] live_load_override / sdl_override 가 None 이면 용도 기본값
    (room_types 의 live_load_kn_m2 / sdl_kn_m2)을 쓰고, 사용자가 우측 속성
    패널에서 값을 바꾸면 그 값(kN/m²)을 여기에 저장해 용도 기본값을 덮어쓴다.
    """
    id: int = 0
    room_type: str = "living"           # room_types.ROOM_TYPES 의 key
    polygon: List[Tuple[float, float]] = field(default_factory=list)
    floor_index: int = 0
    # ── 다층 그룹 식별자 (2026-05-28) ──────────────────────────
    # 한 번의 실 배치는 전체 층(옥상 제외)에 복제되며 같은 group_id 를 공유한다.
    # 같은 group_id 의 실들은 이동/복사/삭제 시 함께 처리된다(모듈 group_id 와
    # 별개 네임스페이스 — 실끼리만 매칭). group_id=0 은 단독/레거시 실.
    group_id: int = 0
    live_load_override: Optional[float] = None   # 활하중 사용자 지정(kN/m²) 또는 None
    sdl_override: Optional[float] = None          # 부가고정하중 사용자 지정(kN/m²) 또는 None

    def effective_live_load(self) -> float:
        """유효 활하중(kN/m²) — override 우선, 없으면 용도 기본값."""
        if self.live_load_override is not None:
            return float(self.live_load_override)
        from modular_3d.카탈로그.room_types import get_room_type, DEFAULT_LIVE_LOAD_KN_M2
        rt = get_room_type(self.room_type)
        return float(rt.live_load_kn_m2) if rt else float(DEFAULT_LIVE_LOAD_KN_M2)

    def effective_sdl(self) -> float:
        """유효 부가고정하중(kN/m²) — override 우선, 없으면 용도 기본값."""
        if self.sdl_override is not None:
            return float(self.sdl_override)
        from modular_3d.카탈로그.room_types import get_room_type, DEFAULT_SDL_KN_M2
        rt = get_room_type(self.room_type)
        return float(rt.sdl_kn_m2) if rt else float(DEFAULT_SDL_KN_M2)

    def centroid(self) -> Tuple[float, float]:
        """폴리곤 무게중심(라벨 표시용). 점이 없으면 (0, 0).

        면적 가중 무게중심(폴리곤 공식). 면적이 0(직선·중복점)이면 좌표 평균으로
        폴백한다.
        """
        pts = self.polygon
        n = len(pts)
        if n == 0:
            return (0.0, 0.0)
        if n < 3:
            sx = sum(p[0] for p in pts) / n
            sy = sum(p[1] for p in pts) / n
            return (sx, sy)
        a = 0.0
        cx = 0.0
        cy = 0.0
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            cross = x0 * y1 - x1 * y0
            a += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        if abs(a) < 1e-9:
            sx = sum(p[0] for p in pts) / n
            sy = sum(p[1] for p in pts) / n
            return (sx, sy)
        a *= 0.5
        return (cx / (6.0 * a), cy / (6.0 * a))

    def to_dict(self) -> dict:
        """직렬화 dict (json 가능). scene_io 가 사용."""
        return {
            'id': int(self.id),
            'room_type': str(self.room_type),
            'polygon': [[float(x), float(y)] for (x, y) in self.polygon],
            'floor_index': int(self.floor_index),
            'group_id': int(self.group_id),
            'live_load_override': (None if self.live_load_override is None
                                   else float(self.live_load_override)),
            'sdl_override': (None if self.sdl_override is None
                             else float(self.sdl_override)),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Room":
        """직렬화 dict → Room. scene_io 가 사용. (옛 'name' 키는 무시.)"""
        poly = [(float(p[0]), float(p[1])) for p in d.get('polygon', [])]
        llo = d.get('live_load_override', None)
        sdlo = d.get('sdl_override', None)
        return cls(
            id=int(d.get('id', 0)),
            room_type=str(d.get('room_type', "living")),
            polygon=poly,
            floor_index=int(d.get('floor_index', 0)),
            group_id=int(d.get('group_id', 0)),
            live_load_override=(None if llo is None else float(llo)),
            sdl_override=(None if sdlo is None else float(sdlo)),
        )


__all__ = ["Room"]
