"""실(Room) 용도별 하중 → 슬래브 적용을 위한 순수 기하 유틸 (5단계).

[역할]
- 슬래브(축정렬 xy 사각형) 위에 걸친 실 폴리곤들의 면적가중 활하중·부가고정하중
  (kN/m²)을 계산한다. 실이 안 덮은 빈 영역은 주거 기본값으로 채운다.
- 옥상(지붕) 레벨 검출: 슬래브 상면 z 중 최상단 레벨(코어 슬래브 제외는 호출측 책임).

[설계 원칙]
- vispy/Qt/OpenSees 비의존 — 순수 파이썬, 단위 테스트 가능.
- 좌표 단위 mm. 면적 mm². 하중 kN/m² (load_calculator 가 N/mm² 로 환산).
- 슬래브 면은 부재 회전 0/90/180/270 이라 항상 축정렬 사각형으로 근사(xy_bbox).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from modular_3d.카탈로그.room_types import (
    DEFAULT_LIVE_LOAD_KN_M2, DEFAULT_SDL_KN_M2,
)


Point = Tuple[float, float]


# ── 폴리곤 면적 / 사각형 클리핑 ──────────────────────────────────

def polygon_area(poly: Sequence[Point]) -> float:
    """단순 폴리곤 면적(절댓값, shoelace). 점 3개 미만이면 0."""
    n = len(poly)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def clip_polygon_to_rect(poly: Sequence[Point],
                         x0: float, y0: float,
                         x1: float, y1: float) -> List[Point]:
    """축정렬 사각형 [x0,x1]×[y0,y1] 으로 폴리곤 클리핑 (Sutherland–Hodgman).

    반환: 잘린 폴리곤 점 목록(볼록/오목 입력 모두 사각형 교집합은 단순 폴리곤).
    겹침이 없으면 빈 리스트.
    """
    if len(poly) < 3:
        return []
    xmin, xmax = min(x0, x1), max(x0, x1)
    ymin, ymax = min(y0, y1), max(y0, y1)

    def _clip_edge(pts: List[Point], inside, intersect) -> List[Point]:
        out: List[Point] = []
        n = len(pts)
        if n == 0:
            return out
        for i in range(n):
            cur = pts[i]
            prv = pts[i - 1]
            cur_in = inside(cur)
            prv_in = inside(prv)
            if cur_in:
                if not prv_in:
                    out.append(intersect(prv, cur))
                out.append(cur)
            elif prv_in:
                out.append(intersect(prv, cur))
        return out

    pts = list(poly)
    # 좌 (x >= xmin)
    pts = _clip_edge(pts, lambda p: p[0] >= xmin,
                     lambda a, b: _lerp_x(a, b, xmin))
    # 우 (x <= xmax)
    pts = _clip_edge(pts, lambda p: p[0] <= xmax,
                     lambda a, b: _lerp_x(a, b, xmax))
    # 하 (y >= ymin)
    pts = _clip_edge(pts, lambda p: p[1] >= ymin,
                     lambda a, b: _lerp_y(a, b, ymin))
    # 상 (y <= ymax)
    pts = _clip_edge(pts, lambda p: p[1] <= ymax,
                     lambda a, b: _lerp_y(a, b, ymax))
    return pts


def _lerp_x(a: Point, b: Point, x: float) -> Point:
    """선분 a-b 가 수직선 X=x 와 만나는 점."""
    ax, ay = a
    bx, by = b
    if abs(bx - ax) < 1e-12:
        return (x, ay)
    t = (x - ax) / (bx - ax)
    return (x, ay + t * (by - ay))


def _lerp_y(a: Point, b: Point, y: float) -> Point:
    """선분 a-b 가 수평선 Y=y 와 만나는 점."""
    ax, ay = a
    bx, by = b
    if abs(by - ay) < 1e-12:
        return (ax, y)
    t = (y - ay) / (by - ay)
    return (ax + t * (bx - ax), y)


def slab_room_overlap_area(rect: Tuple[float, float, float, float],
                           room_polygon: Sequence[Point]) -> float:
    """슬래브 사각형(x0,y0,x1,y1)과 실 폴리곤의 교집합 면적 mm²."""
    x0, y0, x1, y1 = rect
    clipped = clip_polygon_to_rect(room_polygon, x0, y0, x1, y1)
    return polygon_area(clipped)


# ── 면적가중 활하중 / 부가고정하중 ──────────────────────────────

def area_weighted_loads(rect: Tuple[float, float, float, float],
                        rooms_same_floor: Sequence) -> Tuple[float, float]:
    """슬래브(rect) 위 같은 층 실들의 면적가중 (활하중, 부가고정하중) kN/m².

    - 각 실의 교집합 면적 비율로 활하중·SDL 가중 평균.
    - 실이 안 덮은 빈 영역은 주거 기본값(DEFAULT_LIVE/SDL)으로 채운다.
    - 실끼리 겹쳐 덮은 면적 합이 슬래브를 초과하면 빈 영역은 0 으로 클램프.
    반환: (live_kn_m2, sdl_kn_m2).
    """
    x0, y0, x1, y1 = rect
    slab_area = abs((x1 - x0) * (y1 - y0))
    if slab_area < 1e-6:
        return DEFAULT_LIVE_LOAD_KN_M2, DEFAULT_SDL_KN_M2

    covered = 0.0
    live_sum = 0.0
    sdl_sum = 0.0
    for room in rooms_same_floor:
        poly = getattr(room, 'polygon', None)
        if not poly or len(poly) < 3:
            continue
        ov = slab_room_overlap_area(rect, poly)
        if ov <= 0.0:
            continue
        covered += ov
        live_sum += ov * room.effective_live_load()
        sdl_sum += ov * room.effective_sdl()

    empty = max(0.0, slab_area - covered)
    live = (live_sum + empty * DEFAULT_LIVE_LOAD_KN_M2) / slab_area
    sdl = (sdl_sum + empty * DEFAULT_SDL_KN_M2) / slab_area
    return live, sdl


# ── 옥상(지붕) 레벨 검출 ─────────────────────────────────────────

def detect_roof_top_z(slab_top_zs: Sequence[float]) -> Optional[float]:
    """슬래브 상면 z 목록에서 옥상 레벨(최댓값) 반환. 비면 None.

    호출측에서 코어 슬래브는 목록에서 제외하고 넘긴다(코어 옥탑 슬래브가 더 높아
    옥상 레벨을 오판하는 것 방지).
    """
    zs = [float(z) for z in slab_top_zs]
    if not zs:
        return None
    return max(zs)


def is_roof_level(slab_top_z: float, roof_top_z: Optional[float],
                  tol: float = 50.0) -> bool:
    """슬래브 상면 z 가 옥상 레벨(roof_top_z) 과 tol 이내면 옥상으로 판정."""
    if roof_top_z is None:
        return False
    return abs(float(slab_top_z) - float(roof_top_z)) <= tol


__all__ = [
    'polygon_area', 'clip_polygon_to_rect', 'slab_room_overlap_area',
    'area_weighted_loads', 'detect_roof_top_z', 'is_roof_level',
]
