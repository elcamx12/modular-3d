"""Phase 3 단위 테스트 — wall_classifier.

[검증 시나리오 (사용자 결정 ⑥-1~5 반영)]
1. 단일 모듈 → 4 면 ratio≈1
2. 두 모듈 좌우 인접 (20mm 갭) → 마주보는 1 면 ratio≈0, 나머지 6 면 ratio≈1
3. 부분 인접 (한쪽 끝만 맞춤) → ratio 중간값
4. 통로 너무 넓음 (200mm 간격) → 둘 다 ratio≈1
5. FloorPanel 장애물 옵션 토글 ON/OFF → 다른 결과
6. 수직 3 모듈 12 면 분류 길이
7. disabled 옵션 → ratio=0
8. 가중평균 단위중량 계산
"""
from __future__ import annotations

import numpy as np
import pytest

from modular_3d.model.core import (
    Scene, instantiate, ComponentType,
)
from modular_3d.transport.wall_classifier import (
    classify_module, classify_module_face, classify_vertical3_module,
    face_unit_weight, ClassifierOptions, FaceClass, FACE_NAMES,
)


def _make_module(scene, x, y, w=3000.0, d=6000.0, h=3400.0):
    pos = np.array([float(x), float(y), 0.0])
    dims = {"width": w, "depth": d, "height": h}
    comp = instantiate(ComponentType.MODULE, pos, dims, rotation=0, anchor=0)
    cid = scene.add_component(comp)
    return cid, comp


def _make_floor_panel(scene, x, y, w=3000.0, d=3000.0, z=0.0):
    pos = np.array([float(x), float(y), float(z)])
    dims = {"width": w, "depth": d, "height": 200.0}
    comp = instantiate(ComponentType.FLOOR_PANEL, pos, dims, rotation=0, anchor=0)
    cid = scene.add_component(comp)
    return cid, comp


# ── 시나리오 1: 단일 모듈 ────────────────────────────────
def test_single_module_all_faces_exterior():
    scene = Scene()
    _, m = _make_module(scene, 0, 0)
    res = classify_module(m, scene)
    for face in FACE_NAMES:
        assert res[face].exterior_ratio == pytest.approx(1.0, abs=0.01), face


# ── 시나리오 2: 두 모듈 좌우 인접 (20mm 갭) ───────────────
def test_two_modules_adjacent_one_face_interior():
    scene = Scene()
    # 모듈 A 가 x=0~3000, 모듈 B 가 x=3020~6020 (20mm 갭)
    _, mA = _make_module(scene, 0, 0)
    _, mB = _make_module(scene, 3020, 0)
    rA = classify_module(mA, scene)
    rB = classify_module(mB, scene)
    # A 의 우면(+X) = B 와 마주봄 → ratio ≈ 0
    assert rA["우면"].exterior_ratio < 0.05
    # B 의 좌면(-X) 도 동일
    assert rB["좌면"].exterior_ratio < 0.05
    # 나머지 면은 외부
    for face in ("전면", "후면", "좌면"):
        assert rA[face].exterior_ratio == pytest.approx(1.0, abs=0.05)
    for face in ("전면", "후면", "우면"):
        assert rB[face].exterior_ratio == pytest.approx(1.0, abs=0.05)


# ── 시나리오 3: 부분 인접 ────────────────────────────────
def test_partial_adjacency_ratio_mid():
    scene = Scene()
    # 모듈 A: x=0~3000, depth 6000 (y=0~6000)
    # 모듈 B: 같은 +X 쪽, depth 3000 (y=0~3000) — A 의 우면 절반만 가림
    _, mA = _make_module(scene, 0, 0, w=3000, d=6000)
    _, mB = _make_module(scene, 3020, 0, w=3000, d=3000)
    rA = classify_module(mA, scene)
    # A 우면(+X) 의 길이 = depth(6000). 절반(3000)만 B 가 가림 → ratio ≈ 0.5
    assert 0.4 < rA["우면"].exterior_ratio < 0.6


# ── 시나리오 4: 통로 너무 넓음 ──────────────────────────
def test_wide_gap_all_exterior():
    scene = Scene()
    _, mA = _make_module(scene, 0, 0)
    _, mB = _make_module(scene, 3200, 0)   # 200mm 갭 — 20+5 = 25mm 한도 초과
    rA = classify_module(mA, scene)
    assert rA["우면"].exterior_ratio == pytest.approx(1.0, abs=0.05)


# ── 시나리오 5: FloorPanel 옵션 토글 ─────────────────────
def test_floor_panel_obstacle_toggle():
    scene = Scene()
    _, mA = _make_module(scene, 0, 0)
    # 모듈 우면(+X) 쪽에 발코니 패널 (20mm 갭으로 인접)
    _make_floor_panel(scene, 3020, 0, w=3000, d=6000, z=0)
    on = classify_module_face(mA, "우면", scene,
                                 ClassifierOptions(include_floor_panels=True))
    off = classify_module_face(mA, "우면", scene,
                                  ClassifierOptions(include_floor_panels=False))
    assert on.exterior_ratio < 0.1     # 발코니가 가림 → 내부
    assert off.exterior_ratio > 0.9    # 발코니 무시 → 외부


# ── 시나리오 6: 수직 3 모듈 12 면 ───────────────────────
def test_vertical3_module_12_faces():
    scene = Scene()
    pos = np.array([0.0, 0.0, 0.0])
    dims = {"width": 3400.0, "depth": 3400.0, "height": 10240.0}
    v3 = instantiate(ComponentType.VERTICAL_MODULE, pos, dims, 0, 0)
    scene.add_component(v3)
    res = classify_vertical3_module(v3, scene)
    assert len(res) == 12
    # 모든 면이 외부 (다른 부재 없음)
    for (f_idx, face), fc in res.items():
        assert 0 <= f_idx <= 2
        assert face in FACE_NAMES
        assert fc.exterior_ratio == pytest.approx(1.0, abs=0.05)


# ── 시나리오 7: disabled 옵션 ──────────────────────────
def test_disabled_returns_zero_ratio():
    scene = Scene()
    _, m = _make_module(scene, 0, 0)
    fc = classify_module_face(m, "전면", scene, ClassifierOptions(enabled=False))
    assert fc.exterior_ratio == 0.0


# ── 가중평균 단위중량 ───────────────────────────────────
def test_face_unit_weight_weighted_average():
    # ratio=0.4 → 0.4×55 + 0.6×30 = 22 + 18 = 40
    fc = FaceClass(exterior_ratio=0.4)
    assert face_unit_weight(fc, 30, 55) == pytest.approx(40.0)
    # 전부 외부
    assert face_unit_weight(FaceClass(exterior_ratio=1.0), 30, 55) == 55
    # 전부 내부
    assert face_unit_weight(FaceClass(exterior_ratio=0.0), 30, 55) == 30
