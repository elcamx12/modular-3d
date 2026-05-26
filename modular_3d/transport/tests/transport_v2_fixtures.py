"""Phase 3 — 새 패커 검증용 픽스처 자동 생성기.

[설계 근거]
- 운송 로직 계획서 § 10 (검증세트) 그대로 구현.
- 화물 종류별 다양성 (모듈 5 종 + floor 두께/길이/폭 다양 + wall 다양 +
  L자/2면/3면/4면 종속 + 장변·단변 결합 + 부분 벽) × 개수 5 단계.
- 고정 시드 → 매 실행 결과 동일. 사용자가 seed 인자로 변경 가능.

[사용처]
- Phase 3 단위 테스트 (자동 생성 + 다양성 강제)
- Phase 4~7 회귀 테스트 (단순 케이스 검증)
- Phase 9 검증 보고서 (모든 사이즈 × 비용 모드)

[기존 packer 호환성]
- 자동 생성된 모든 픽스처가 기존 packer.py 로 패킹 가능해야 한다 (오류 없이).
- Phase 3 의 단위 테스트가 이를 강제.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator, List, Tuple

from modular_3d.transport.models import (
    Module, Panel, Section, SiteLimit, SpacingParams, Truck, WallSegment,
)


# ── 공용 단면 (검증세트 전용 — 실 디자인 단면과 무관) ──────────
_SHS = Section(name="SHS200x8", section_type="SHS",
               width=200, height=200, thickness=8, weight_per_m=47.9)


# ── 픽스처 한 개의 구조 ────────────────────────────────────────
@dataclass(frozen=True)
class Fixture:
    """한 검증 픽스처 = (이름, 모듈 리스트, 패널 리스트, 트럭 리스트, 현장, 간격).

    Phase 9 의 run_validation 이 이 튜플을 받아 패킹 + 비용 측정.
    """
    name: str
    modules: List[Module]
    panels: List[Panel]
    trucks: List[Truck]
    site: SiteLimit
    spacing: SpacingParams


# ── 기본 트럭 세트 (현실적 — 저상 + 광폭 + A-frame) ────────────
def default_trucks() -> List[Truck]:
    """검증세트 전용 트럭 카탈로그. 3 종 × 한 대씩."""
    return [
        Truck(name="저상25t", truck_type="lowbed",
              max_length=12000, max_width=3000, max_height=4500, max_weight=25000,
              vehicle_height_offset=700, curb_weight_kg=14000, active=True),
        Truck(name="광폭28t", truck_type="extendable",
              max_length=18000, max_width=3400, max_height=4500, max_weight=28000,
              vehicle_height_offset=700, curb_weight_kg=16000, active=True),
        Truck(name="A프레임20t", truck_type="aframe",
              max_length=12000, max_width=3000, max_height=4500, max_weight=20000,
              vehicle_height_offset=700, curb_weight_kg=12000, active=True),
    ]


def default_site() -> SiteLimit:
    """현장 제한 — 폭·높이 광로급(트럭보다 넓음), GVW 해당없음."""
    return SiteLimit(max_gvw_kg=None, max_width_mm=3500, max_height_mm=4500)


# ── 모듈 생성기 — 5 사이즈 ────────────────────────────────────
def _make_module(name: str, length: float, width: float, height: float,
                 extra_kg: float) -> Module:
    return Module(
        name=name, length=length, width=width, height=height,
        column_section=_SHS, beam_section=_SHS, extra_weight_kg=extra_kg,
    )


def gen_modules(count: int, rng: random.Random) -> List[Module]:
    """count 개 모듈을 다양 사이즈로 생성.

    sizes = small(6×3×2.5m, 5t) / medium(9×3×3m, 10t) / large(12×3×3.2m, 15t)
          / xlarge(14×3.5×3.2m, 18t) / small4_5m(4.5×3×2.5m, 4t — 합산 검증용)

    count 비율: 골고루 — 작은 count 일수록 medium 위주, large/xlarge 는 적게.
    """
    catalog = [
        ("small",     6000.0, 3000.0, 2500.0, 5000.0),
        ("medium",    9000.0, 3000.0, 3000.0, 10000.0),
        ("large",    12000.0, 3000.0, 3200.0, 15000.0),
        ("xlarge",   14000.0, 3500.0, 3200.0, 18000.0),
        ("small4_5m", 4500.0, 3000.0, 2500.0, 4000.0),
    ]
    out: List[Module] = []
    for i in range(count):
        kind, L, W, H, w = catalog[i % len(catalog)]
        # 같은 종류 여러 개 — 약간씩 무게 흔들기 (재현 가능)
        wt = w * rng.uniform(0.95, 1.05)
        out.append(_make_module(f"M_{kind}_{i+1}", L, W, H, wt))
    return out


# ── 단순 floor 패널 — 두께 × 길이 × 폭 조합 ───────────────────
def gen_simple_floor_panels(count: int, rng: random.Random) -> List[Panel]:
    """순수 floor 패널을 두께/길이/폭 다양 조합으로 생성."""
    thickness_set = [100.0, 150.0, 200.0]
    length_set = [4000.0, 6000.0, 8000.0, 10000.0]
    width_set = [2400.0, 2800.0, 3000.0]
    out: List[Panel] = []
    for i in range(count):
        th = thickness_set[i % len(thickness_set)]
        L = length_set[(i // len(thickness_set)) % len(length_set)]
        W = width_set[(i // (len(thickness_set) * len(length_set))) % len(width_set)]
        # 무게 = 면적 × 두께 × 단위중량 가정 (콘크리트 24kN/m³ ≈ 2400kg/m³)
        # 단순화 — extra_weight_kg 만 사용
        extra = (L / 1000.0) * (W / 1000.0) * (th / 1000.0) * 2400.0
        out.append(Panel(name=f"F_{i+1}", kind="floor", width=W, length=L,
                         thickness=th, beam_section=_SHS,
                         extra_weight_kg=extra * rng.uniform(0.95, 1.05)))
    return out


# ── 단순 wall 패널 ────────────────────────────────────────────
def gen_simple_wall_panels(count: int, rng: random.Random) -> List[Panel]:
    """독립 wall 패널 — 높이·길이·두께 다양."""
    height_set = [2700.0, 3000.0, 3300.0]
    length_set = [3000.0, 6000.0, 9000.0]
    thickness_set = [150.0, 200.0]
    out: List[Panel] = []
    for i in range(count):
        H = height_set[i % len(height_set)]
        L = length_set[(i // len(height_set)) % len(length_set)]
        th = thickness_set[(i // (len(height_set) * len(length_set))) % len(thickness_set)]
        # 무게 = 단위면적 30kg/m² × 면적 가정 (내부 벽)
        extra = (L / 1000.0) * (H / 1000.0) * 30.0
        # wall 패널은 2D 부재: length=벽 길이, width=벽 높이, thickness=두께
        # 눕힘 자세 시 width 가 트럭 폭 방향(벽 높이 차원).
        # 세움 자세 시 width 가 위로 솟음 (A-frame 트럭).
        out.append(Panel(name=f"W_{i+1}", kind="wall",
                         width=H, length=L, thickness=th,
                         wall_height=H, beam_section=_SHS, column_section=_SHS,
                         extra_weight_kg=extra * rng.uniform(0.95, 1.05)))
    return out


# ── 종속 floor 패널 — L자/2면/3면/4면/부분벽/장단변결합 ───────
def _seg(side: int, panel_length: float, panel_width: float,
         seg_length: float = -1.0, start_offset: float = 0.0,
         seg_height: float = 3000.0, seg_thickness: float = 200.0) -> WallSegment:
    """변 인덱스에 따른 wall_segment 생성. seg_length=-1 이면 변 전체 길이.

    변 인덱스 0/2 (장변): 변 길이 = panel.length
    변 인덱스 1/3 (단변): 변 길이 = panel.width
    """
    if seg_length < 0:
        seg_length = panel_length if side in (0, 2) else panel_width
    return WallSegment(side=side, start_offset_mm=start_offset,
                       length_mm=seg_length, height_mm=seg_height,
                       thickness_mm=seg_thickness,
                       column_section=_SHS, beam_section=_SHS)


def gen_lshape_panels(count: int, rng: random.Random) -> List[Panel]:
    """L자 — 변 0/1/2/3 중 무작위 한 변에 wall_segment 1 개."""
    out: List[Panel] = []
    for i in range(count):
        side = rng.choice([0, 1, 2, 3])
        L = rng.choice([6000.0, 8000.0, 10000.0])
        W = rng.choice([2800.0, 3000.0])
        seg = _seg(side, L, W)
        out.append(Panel(name=f"L_{i+1}_s{side}", kind="floor",
                         width=W, length=L, thickness=150.0, beam_section=_SHS,
                         wall_segments=(seg,),
                         extra_weight_kg=(L/1000.0) * (W/1000.0) * 0.15 * 2400.0))
    return out


def gen_2face_panels(count: int, rng: random.Random) -> List[Panel]:
    """2면 종속 — 마주보는 변 (0,2) 또는 직각 변 (0,1) (1,3)."""
    combos = [(0, 2), (0, 1), (1, 3)]
    out: List[Panel] = []
    for i in range(count):
        sides = combos[i % len(combos)]
        L = rng.choice([6000.0, 8000.0])
        W = rng.choice([2800.0, 3000.0])
        segs = tuple(_seg(s, L, W) for s in sides)
        out.append(Panel(name=f"C2_{i+1}_{sides[0]}{sides[1]}", kind="floor",
                         width=W, length=L, thickness=150.0, beam_section=_SHS,
                         wall_segments=segs,
                         extra_weight_kg=(L/1000.0)*(W/1000.0) * 0.15 * 2400.0))
    return out


def gen_long_short_combo_panels(count: int, rng: random.Random) -> List[Panel]:
    """장변(0 또는 2) + 단변(1 또는 3) 결합 — 사용자가 명시 요청."""
    combos = [(0, 1), (0, 3), (2, 1), (2, 3)]
    out: List[Panel] = []
    for i in range(count):
        sides = combos[i % len(combos)]
        L = rng.choice([6000.0, 8000.0])
        W = rng.choice([2800.0, 3000.0])
        segs = tuple(_seg(s, L, W) for s in sides)
        out.append(Panel(name=f"LS_{i+1}_{sides[0]}{sides[1]}", kind="floor",
                         width=W, length=L, thickness=150.0, beam_section=_SHS,
                         wall_segments=segs,
                         extra_weight_kg=(L/1000.0)*(W/1000.0) * 0.15 * 2400.0))
    return out


def gen_3face_panels(count: int, rng: random.Random) -> List[Panel]:
    """3면 종속 — 4 가지 변 조합."""
    combos = [(0, 1, 2), (0, 2, 3), (1, 2, 3), (0, 1, 3)]
    out: List[Panel] = []
    for i in range(count):
        sides = combos[i % len(combos)]
        L = rng.choice([6000.0, 8000.0])
        W = rng.choice([2800.0, 3000.0])
        segs = tuple(_seg(s, L, W) for s in sides)
        out.append(Panel(name=f"C3_{i+1}", kind="floor",
                         width=W, length=L, thickness=150.0, beam_section=_SHS,
                         wall_segments=segs,
                         extra_weight_kg=(L/1000.0)*(W/1000.0) * 0.15 * 2400.0))
    return out


def gen_4face_panels(count: int, rng: random.Random) -> List[Panel]:
    """4면 종속 — 네 변 모두."""
    out: List[Panel] = []
    for i in range(count):
        L = rng.choice([6000.0, 8000.0])
        W = rng.choice([2800.0, 3000.0])
        segs = tuple(_seg(s, L, W) for s in (0, 1, 2, 3))
        out.append(Panel(name=f"C4_{i+1}", kind="floor",
                         width=W, length=L, thickness=150.0, beam_section=_SHS,
                         wall_segments=segs,
                         extra_weight_kg=(L/1000.0)*(W/1000.0) * 0.15 * 2400.0))
    return out


def gen_partial_wall_panels(count: int, rng: random.Random) -> List[Panel]:
    """부분 벽 — start_offset > 0, length_mm < 변 전체 길이."""
    out: List[Panel] = []
    for i in range(count):
        side = rng.choice([0, 1, 2, 3])
        L = rng.choice([6000.0, 8000.0])
        W = rng.choice([2800.0, 3000.0])
        var_len = L if side in (0, 2) else W
        seg_len = var_len * rng.uniform(0.3, 0.7)  # 30~70%
        offset = (var_len - seg_len) * rng.uniform(0.1, 0.5)
        seg = _seg(side, L, W, seg_length=seg_len, start_offset=offset)
        out.append(Panel(name=f"PW_{i+1}_s{side}", kind="floor",
                         width=W, length=L, thickness=150.0, beam_section=_SHS,
                         wall_segments=(seg,),
                         extra_weight_kg=(L/1000.0)*(W/1000.0) * 0.15 * 2400.0))
    return out


# ── 통합 — 화물 종류별 비율 분배 ────────────────────────────────
def gen_mixed_panels(panel_count: int, rng: random.Random) -> List[Panel]:
    """panel_count 개 패널을 8 종류로 골고루 분배.

    분배: simple_floor 25% + simple_wall 20% + L자 15% + 2면 10% +
          장단변결합 10% + 3면 7% + 4면 3% + 부분벽 10%
    """
    # 비례 분배 — 합계가 panel_count 정확히 일치하도록 보정.
    # max(1,...) 인플레이션 방지 (작은 count 에서 합이 부풀어 오름).
    n_floor = int(panel_count * 0.25)
    n_wall  = int(panel_count * 0.20)
    n_lshape= int(panel_count * 0.15)
    n_2face = int(panel_count * 0.10)
    n_ls    = int(panel_count * 0.10)
    n_3face = int(panel_count * 0.07)
    n_4face = int(panel_count * 0.03)
    n_partial = panel_count - (n_floor + n_wall + n_lshape + n_2face +
                                n_ls + n_3face + n_4face)
    if n_partial < 0:
        n_partial = 0

    panels: List[Panel] = []
    panels += gen_simple_floor_panels(n_floor, rng)
    panels += gen_simple_wall_panels(n_wall, rng)
    panels += gen_lshape_panels(n_lshape, rng)
    panels += gen_2face_panels(n_2face, rng)
    panels += gen_long_short_combo_panels(n_ls, rng)
    panels += gen_3face_panels(n_3face, rng)
    panels += gen_4face_panels(n_4face, rng)
    panels += gen_partial_wall_panels(n_partial, rng)
    return panels


# ── 사이즈별 픽스처 ────────────────────────────────────────────
# 계획서 § 10.3 정의 그대로
SIZE_PRESETS: List[Tuple[str, int, int]] = [
    # (이름, 모듈 수, 패널 수)
    ("min",    1, 1),
    ("small",  3, 8),
    ("medium", 10, 30),
    ("large",  30, 80),
    ("xlarge", 60, 150),
]


# ── 메인 진입점 — 모든 픽스처 생성 ───────────────────────────────
def generate_all_fixtures(seed: int = 42) -> List[Fixture]:
    """5 단계 사이즈의 픽스처 5 개를 고정 시드로 생성.

    Args:
        seed: random.Random 시드. 기본 42. 사용자가 다른 값 주면 다른 픽스처.

    Returns:
        Fixture 5 개 (min/small/medium/large/xlarge).
    """
    out: List[Fixture] = []
    for name, n_mod, n_panel in SIZE_PRESETS:
        rng = random.Random(seed + hash(name) % 1000)  # 사이즈별 독립 시드
        modules = gen_modules(n_mod, rng)
        panels = gen_mixed_panels(n_panel, rng)
        out.append(Fixture(
            name=name, modules=modules, panels=panels,
            trucks=default_trucks(), site=default_site(),
            spacing=SpacingParams(),
        ))
    return out


def generate_fixture(name: str, seed: int = 42) -> Fixture:
    """SIZE_PRESETS 중 한 이름의 픽스처만 생성.

    Args:
        name: "min" / "small" / "medium" / "large" / "xlarge"
        seed: random.Random 시드

    Raises:
        ValueError: 알 수 없는 사이즈 이름
    """
    for nm, n_mod, n_panel in SIZE_PRESETS:
        if nm == name:
            rng = random.Random(seed + hash(name) % 1000)
            modules = gen_modules(n_mod, rng)
            panels = gen_mixed_panels(n_panel, rng)
            return Fixture(
                name=name, modules=modules, panels=panels,
                trucks=default_trucks(), site=default_site(),
                spacing=SpacingParams(),
            )
    raise ValueError(f"unknown fixture name: {name}. valid: "
                     f"{[n for n, _, _ in SIZE_PRESETS]}")


__all__ = [
    "Fixture",
    "SIZE_PRESETS",
    "default_trucks",
    "default_site",
    "gen_modules",
    "gen_simple_floor_panels",
    "gen_simple_wall_panels",
    "gen_lshape_panels",
    "gen_2face_panels",
    "gen_long_short_combo_panels",
    "gen_3face_panels",
    "gen_4face_panels",
    "gen_partial_wall_panels",
    "gen_mixed_panels",
    "generate_all_fixtures",
    "generate_fixture",
]
