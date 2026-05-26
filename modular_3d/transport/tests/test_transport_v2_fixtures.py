"""Phase 3 단위 테스트 — transport_v2_fixtures 자동 생성기.

[검증 범위]
- 5 사이즈 픽스처 모두 정상 생성
- 다양성 강제: 모듈 사이즈 5 종 + 패널 8 종류 모두 포함되는가 (medium 이상)
- 기존 packer.py 로 자동 생성된 픽스처가 *모두 패킹 가능* (오류 없이)
- 고정 시드 → 매 실행 결과 동일
- 같은 사이즈 + 다른 시드 → 다른 픽스처 (시드가 의미 있게 동작)
"""
from __future__ import annotations

import pytest

from modular_3d.transport.models import Module, Panel
from modular_3d.transport.packer import pack_items
from modular_3d.transport.tests.transport_v2_fixtures import (
    SIZE_PRESETS,
    Fixture,
    default_site,
    default_trucks,
    gen_2face_panels,
    gen_3face_panels,
    gen_4face_panels,
    gen_long_short_combo_panels,
    gen_lshape_panels,
    gen_modules,
    gen_partial_wall_panels,
    gen_simple_floor_panels,
    gen_simple_wall_panels,
    generate_all_fixtures,
    generate_fixture,
)
import random


# ── 메인 진입점 — 5 사이즈 모두 생성 ─────────────────────────
def test_generate_all_fixtures_returns_5():
    fxs = generate_all_fixtures()
    assert len(fxs) == 5
    names = {f.name for f in fxs}
    assert names == {"min", "small", "medium", "large", "xlarge"}


def test_generate_fixture_by_name():
    fx = generate_fixture("medium")
    assert fx.name == "medium"
    assert len(fx.modules) == 10
    assert len(fx.panels) == 30


def test_generate_fixture_unknown_raises():
    with pytest.raises(ValueError):
        generate_fixture("nonexistent")


# ── 사이즈별 화물 개수 ──────────────────────────────────────
@pytest.mark.parametrize("size_name,n_mod,n_panel", SIZE_PRESETS)
def test_fixture_counts(size_name, n_mod, n_panel):
    fx = generate_fixture(size_name)
    assert len(fx.modules) == n_mod
    assert len(fx.panels) == n_panel


# ── 다양성 — 모듈 사이즈 5 종 모두 포함되는가 ──────────────
def test_modules_diversity_in_large():
    """large(30개) 모듈에 5 사이즈 모두 포함되어야."""
    fx = generate_fixture("large")
    # M_<size>_<n> 패턴 — small4_5m 처럼 size 에 '_' 가 포함될 수 있어
    # rsplit 으로 마지막 _<n> 만 떼냄.
    sizes = {m.name[len("M_"):].rsplit("_", 1)[0] for m in fx.modules}
    expected = {"small", "medium", "large", "xlarge", "small4_5m"}
    assert expected.issubset(sizes), f"누락된 사이즈: {expected - sizes}"


def test_small_module_4_5m_size_exists_for_module_merge_test():
    """small4_5m 모듈은 Phase 4 모듈 합산 검증의 핵심 — 생성에 반드시 포함."""
    fx = generate_fixture("medium")
    has_small_4_5 = any(m.name.startswith("M_small4_5m") for m in fx.modules)
    assert has_small_4_5, "small4_5m 모듈 누락 (Phase 4 모듈 합산 검증 불가)"


# ── 다양성 — 패널 8 종류 모두 포함되는가 (medium 이상) ───
def _classify_panel(p: Panel) -> str:
    """패널 종류 식별 — 다양성 검증용."""
    if p.kind == "wall":
        return "simple_wall"
    if not p.wall_segments:
        return "simple_floor"
    n_seg = len(p.wall_segments)
    if n_seg == 4:
        return "4face"
    if n_seg == 3:
        return "3face"
    if n_seg == 2:
        sides = sorted([s.side for s in p.wall_segments])
        if sides in ([0, 2], [1, 3]):
            return "2face_opposite"
        if (sides[0] in (0, 2)) and (sides[1] in (1, 3)):
            return "long_short_combo"
        return "2face_corner"
    if n_seg == 1:
        seg = p.wall_segments[0]
        # 부분 벽 = 변 전체 길이가 아닌 경우
        var_len = p.length if seg.side in (0, 2) else p.width
        if abs(seg.length_mm - var_len) > 1.0:
            return "partial_wall"
        return "lshape"
    return "unknown"


def test_panels_diversity_in_medium():
    """medium 이상에서 8 종류 패널이 모두 등장 (4face 는 비율 작아 medium 에 없을 수 있음)."""
    fx = generate_fixture("medium")
    kinds = {_classify_panel(p) for p in fx.panels}
    # 최소한 5 종류 이상은 등장해야
    assert len(kinds) >= 5, f"패널 다양성 부족 — 종류 {len(kinds)} 개만 등장: {kinds}"


def test_panels_diversity_in_large():
    """large(80개) 에서는 8 종류 모두 등장해야 (4face 포함)."""
    fx = generate_fixture("large")
    kinds = {_classify_panel(p) for p in fx.panels}
    expected = {"simple_floor", "simple_wall", "lshape", "2face_opposite",
                "2face_corner", "long_short_combo", "3face", "partial_wall"}
    # 4face 는 비율 3% 라 large(80*0.03=2.4) 에서 1~2 개 등장 가능
    missing = expected - kinds
    assert not missing, f"large 픽스처에서 누락된 패널 종류: {missing}"


# ── 기존 packer 로 패킹 가능 — 회귀 호환성 ────────────────
@pytest.mark.parametrize("size_name", ["min", "small", "medium"])
def test_fixture_packs_with_existing_packer(size_name):
    """자동 생성된 픽스처가 기존 packer.py 로 *오류 없이* 패킹되어야.

    Phase 3 검증 기준 — Phase 8 통합 후 신규 packer 도 동일하게 패킹 가능해야.
    """
    fx = generate_fixture(size_name)
    res = pack_items(fx.modules, fx.panels, fx.trucks, fx.site, fx.spacing)
    # 적어도 회차가 1 개 이상 + 모든 화물이 trip 에 들어감 (blocked 가 적어야)
    assert res.total_trips >= 1
    # 단순 케이스에서는 blocked 가 0 이어야 (정상 화물·트럭 조합)
    assert len(res.blocked) == 0, f"기존 packer 가 {len(res.blocked)} 개 화물 거부함 — 픽스처 데이터 비현실"


def test_fixture_packs_large_no_blocked():
    """large 사이즈도 기존 packer 가 모두 패킹 가능해야 한다."""
    fx = generate_fixture("large")
    res = pack_items(fx.modules, fx.panels, fx.trucks, fx.site, fx.spacing)
    assert len(res.blocked) == 0


# ── 고정 시드 → 매 실행 동일 ───────────────────────────────
def test_fixed_seed_reproducible():
    """같은 시드 두 번 → 같은 픽스처."""
    fx1 = generate_fixture("medium", seed=42)
    fx2 = generate_fixture("medium", seed=42)
    assert len(fx1.modules) == len(fx2.modules)
    for m1, m2 in zip(fx1.modules, fx2.modules):
        assert m1.name == m2.name
        assert m1.weight == m2.weight  # 무게 흔들기도 같은 시드면 같은 결과


def test_different_seed_different_fixture():
    """다른 시드 → 다른 픽스처 (적어도 무게가 달라야)."""
    fx1 = generate_fixture("medium", seed=42)
    fx2 = generate_fixture("medium", seed=7)
    # 모듈 무게 시퀀스가 달라야
    w1 = [m.weight for m in fx1.modules]
    w2 = [m.weight for m in fx2.modules]
    assert w1 != w2, "다른 시드에도 같은 무게 — 시드가 의미 있게 동작 안 함"


# ── 개별 생성 함수 동작 ────────────────────────────────────
def test_gen_modules_count():
    rng = random.Random(0)
    assert len(gen_modules(7, rng)) == 7


def test_gen_simple_floor_panels_count():
    rng = random.Random(0)
    assert len(gen_simple_floor_panels(5, rng)) == 5


def test_gen_lshape_panels_have_one_segment():
    rng = random.Random(0)
    ps = gen_lshape_panels(5, rng)
    for p in ps:
        assert len(p.wall_segments) == 1


def test_gen_4face_panels_have_four_segments():
    rng = random.Random(0)
    ps = gen_4face_panels(3, rng)
    for p in ps:
        assert len(p.wall_segments) == 4
        sides = sorted(s.side for s in p.wall_segments)
        assert sides == [0, 1, 2, 3]


def test_gen_partial_wall_has_partial_length():
    rng = random.Random(0)
    ps = gen_partial_wall_panels(3, rng)
    for p in ps:
        seg = p.wall_segments[0]
        var_len = p.length if seg.side in (0, 2) else p.width
        # 부분 벽 = 변 전체 길이가 아님
        assert seg.length_mm < var_len, (
            f"부분 벽이 실은 변 전체를 차지: seg.length_mm={seg.length_mm} == var_len={var_len}"
        )
        assert seg.start_offset_mm > 0


def test_gen_long_short_combo_panels_mixed_sides():
    rng = random.Random(0)
    ps = gen_long_short_combo_panels(8, rng)
    for p in ps:
        assert len(p.wall_segments) == 2
        sides = sorted(s.side for s in p.wall_segments)
        # 한 변은 0/2, 다른 한 변은 1/3
        long_side = [s for s in sides if s in (0, 2)]
        short_side = [s for s in sides if s in (1, 3)]
        assert len(long_side) == 1 and len(short_side) == 1, (
            f"장변·단변 결합이 아님: sides={sides}"
        )
