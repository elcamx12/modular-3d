"""실 용도별 하중 유틸(room_loads) 순수함수 테스트 (5단계)."""
from modular_3d.model.room import Room
from modular_3d.analysis.room_loads import (
    polygon_area, slab_room_overlap_area, area_weighted_loads,
    detect_roof_top_z, is_roof_level,
)
from modular_3d.카탈로그.room_types import (
    DEFAULT_LIVE_LOAD_KN_M2, DEFAULT_SDL_KN_M2,
)


def test_polygon_area_square():
    sq = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert abs(polygon_area(sq) - 10000.0) < 1e-6


def test_overlap_full_inside():
    # 실이 슬래브 안에 완전히 들어감 → 교집합 = 실 면적
    rect = (0, 0, 1000, 1000)
    room = [(100, 100), (400, 100), (400, 400), (100, 400)]
    assert abs(slab_room_overlap_area(rect, room) - 90000.0) < 1e-3


def test_overlap_partial_clip():
    # 실이 슬래브 경계를 넘어감 → 교집합은 슬래브 안쪽만
    rect = (0, 0, 1000, 1000)
    room = [(500, 500), (1500, 500), (1500, 1500), (500, 1500)]
    # 겹치는 부분: 500..1000 × 500..1000 = 500×500 = 250000
    assert abs(slab_room_overlap_area(rect, room) - 250000.0) < 1e-3


def test_overlap_none():
    rect = (0, 0, 1000, 1000)
    room = [(2000, 2000), (3000, 2000), (3000, 3000), (2000, 3000)]
    assert slab_room_overlap_area(rect, room) == 0.0


def test_area_weighted_empty_slab_uses_default():
    # 실이 하나도 없으면 기본값
    rect = (0, 0, 1000, 1000)
    live, sdl = area_weighted_loads(rect, [])
    assert abs(live - DEFAULT_LIVE_LOAD_KN_M2) < 1e-9
    assert abs(sdl - DEFAULT_SDL_KN_M2) < 1e-9


def test_area_weighted_half_living_half_empty():
    # 슬래브 절반을 거실(활2.0/SDL1.5)이 덮고 절반은 비어있음(기본 2.0/1.5)
    rect = (0, 0, 1000, 1000)
    living = Room(room_type='living',
                  polygon=[(0, 0), (500, 0), (500, 1000), (0, 1000)])
    live, sdl = area_weighted_loads(rect, [living])
    # 거실·기본 모두 2.0/1.5 라 평균도 동일
    assert abs(live - 2.0) < 1e-9
    assert abs(sdl - 1.5) < 1e-9


def test_area_weighted_corridor_mix():
    # 절반 복도(활5.0), 절반 빈영역(2.0) → 평균 3.5
    rect = (0, 0, 1000, 1000)
    corridor = Room(room_type='corridor',
                    polygon=[(0, 0), (500, 0), (500, 1000), (0, 1000)])
    live, sdl = area_weighted_loads(rect, [corridor])
    assert abs(live - 3.5) < 1e-6


def test_area_weighted_override():
    # 사용자 오버라이드가 용도 기본값을 덮어쓴다
    rect = (0, 0, 1000, 1000)
    r = Room(room_type='living',
             polygon=[(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
             live_load_override=10.0, sdl_override=4.0)
    live, sdl = area_weighted_loads(rect, [r])
    assert abs(live - 10.0) < 1e-6
    assert abs(sdl - 4.0) < 1e-6


def test_roof_detection():
    zs = [100.0, 3520.0, 6940.0]   # 1·2·3층 슬래브 상면
    roof = detect_roof_top_z(zs)
    assert roof == 6940.0
    assert is_roof_level(6940.0, roof)
    assert is_roof_level(6940.0 + 30.0, roof)   # tol 이내
    assert not is_roof_level(3520.0, roof)


def test_roof_detection_empty():
    assert detect_roof_top_z([]) is None
    assert not is_roof_level(100.0, None)
