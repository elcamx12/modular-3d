"""핵심 계산부 골든/속성 테스트 — 스타터 묶음 (리팩토링 안전망 1단계).

[목적] 3~7단계 구조 리팩토링 중 계산이 틀어지면 즉시 잡는 그물.
- 골든: 손계산 정답과 직접 비교(기하 인셋·캔틸 회귀가드·지진 Cs·트리뷰테리·면적).
- 속성/불변식: 선형성·부호(강도검토).
실제 코드 동작만 검증한다. 앱/Qt/렌더 미사용.
"""
import numpy as np
import pytest

from modular_3d.model.core import (
    instantiate, ComponentType, _rotate_point, _SECTION_W,
)


def _span(corners, axis):
    a = corners[:, axis]
    return float(a.max() - a.min())


# ── 1. 기하: 90° 회전 사분면 ────────────────────────────────
def test_rotate_point_quadrants():
    assert _rotate_point(1.0, 0.0, 0) == (1.0, 0.0)
    assert _rotate_point(1.0, 0.0, 90) == pytest.approx((0.0, 1.0))
    assert _rotate_point(1.0, 0.0, 180) == pytest.approx((-1.0, 0.0))
    assert _rotate_point(1.0, 0.0, 270) == pytest.approx((0.0, -1.0))


# ── 2. 기하: 모듈 보·슬래브 단면폭 인셋 + 부재 개수 ─────────
def test_module_geometry_inset_and_counts():
    s = _SECTION_W
    w, d, h = 3400.0, 3000.0, 3400.0
    m = instantiate(ComponentType.MODULE, np.zeros(3),
                    {'width': w, 'depth': d, 'height': h})
    assert len(m.columns) == 4
    assert len(m.bottom_beams) == 4
    assert len(m.top_beams) == 4
    # 슬래브는 4변 단면폭(s) 인셋 → 폭이 양쪽 s 씩 줄어든다.
    assert _span(m.slab.corners, 0) == pytest.approx(w - 2 * s)
    assert _span(m.slab.corners, 1) == pytest.approx(d - 2 * s)


# ── 3. 기하: 캔틸레버 슬래브 끝변 비돌출(수정 회귀가드) ─────
def test_cantilever_slab_no_overhang():
    s = _SECTION_W
    w, d = 1500.0, 3000.0   # width=돌출 길이, depth=폭
    c = instantiate(ComponentType.CANTILEVER_SLAB, np.zeros(3),
                    {'width': w, 'depth': d})
    # 끝변은 단면폭 인셋, 접합면(x=0)은 그대로 → x 폭 = w - s (w 아님!)
    assert _span(c.slab.corners, 0) == pytest.approx(w - s)
    assert _span(c.slab.corners, 1) == pytest.approx(d - 2 * s)


# ── 4. 지진: 등가정적 Cs 상·하한 클램프 ─────────────────────
def test_seismic_cs_clamping():
    from modular_3d.analysis.ops_solver import _seismic_base_shear
    W = 1.0e6  # N
    # (a) 정상 구간: Cs = sds·Ie/R = 0.5/8 = 0.0625
    v = _seismic_base_shear(W, sds=0.5, sd1=0.4, Ie=1.0, R=8.0, T=0.5)
    assert v == pytest.approx(0.0625 * W, rel=1e-6)
    # (b) 하한 바인딩: 큰 R → Cs_min = max(0.044·sds·Ie, 0.01) = 0.022
    v = _seismic_base_shear(W, sds=0.5, sd1=0.4, Ie=1.0, R=1000.0, T=0.5)
    assert v == pytest.approx(0.022 * W, rel=1e-6)
    # (c) 상한 바인딩: Cs_max = sd1·Ie/(T·R) = 0.6/(1·8) = 0.075
    v = _seismic_base_shear(W, sds=1.0, sd1=0.6, Ie=1.0, R=8.0, T=1.0)
    assert v == pytest.approx(0.075 * W, rel=1e-6)


# ── 5. 하중: 슬래브 트리뷰테리 등가 UDL 분배 ────────────────
def test_slab_tributary_distribution():
    from modular_3d.analysis.load_calculator import (
        _distribute_slab_pressure, LoadResult,
    )
    p = 1.0e-3  # N/mm²
    # 1방향(b/a≥2): 긴 변 보에 p·a/2, 단변엔 0
    res = LoadResult()
    a, b = 2000.0, 5000.0
    _distribute_slab_pressure(p, [1, 2], [3, 4], b, a, res, False)
    assert res.get(1).slab_dead == pytest.approx(p * a / 2.0)
    assert res.get(3).slab_dead == pytest.approx(0.0)
    # 2방향: 단변 삼각형 평균 p·a/4, 장변 사다리꼴 (p·a/2)(1 − a/(2b))
    res2 = LoadResult()
    a, b = 3000.0, 4000.0
    _distribute_slab_pressure(p, [1, 2], [3, 4], b, a, res2, False)
    assert res2.get(3).slab_dead == pytest.approx(p * a / 4.0)
    assert res2.get(1).slab_dead == pytest.approx(
        (p * a / 2.0) * (1.0 - a / (2.0 * b)))


# ── 6. 강도: 인장 검토 선형성·영점(불변식) ──────────────────
def test_axial_tension_linearity():
    import types
    from modular_3d.analysis.strength_check import check_axial
    sec = types.SimpleNamespace(A=1000.0, ry=80.0, rz=80.0)
    assert check_axial(sec, 0.0, 3000.0) == 0.0
    r1 = check_axial(sec, 100000.0, 3000.0)
    r2 = check_axial(sec, 200000.0, 3000.0)
    assert r1 > 0.0
    assert r2 == pytest.approx(2.0 * r1)


# ── 7. 운송: 박스 충돌·갭 ───────────────────────────────────
def test_collision_overlap_and_gap():
    from modular_3d.transport.collision import boxes_overlap, boxes_gap_ok
    # 박스 = (xmin, ymin, zmin, xmax, ymax, zmax)
    b1 = (0.0, 0.0, 0.0, 1000.0, 1000.0, 500.0)
    b2 = (1200.0, 0.0, 0.0, 2200.0, 1000.0, 500.0)  # x로 200 떨어짐
    assert not boxes_overlap(b1, b2)
    assert boxes_gap_ok(b1, b2, 100.0)      # 200 ≥ 100 → OK
    assert not boxes_gap_ok(b1, b2, 300.0)  # 200 < 300 → 위반
    b3 = (500.0, 0.0, 0.0, 1500.0, 1000.0, 500.0)   # b1 과 겹침
    assert boxes_overlap(b1, b3)


# ── 8. 물량: 폴리곤 면적(shoelace 절댓값) ───────────────────
def test_polygon_area_shoelace():
    from modular_3d.model.type_naming import _poly_area
    sq = [(0.0, 0.0), (2.0, 0.0), (2.0, 3.0), (0.0, 3.0)]
    assert _poly_area(sq) == pytest.approx(6.0)
