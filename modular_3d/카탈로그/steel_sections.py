"""KS D 3568 각형강관(SHS) 단면 카탈로그.

[단위] N, mm, MPa.
[재질] SS275 (Fy=275 MPa, Fu=410 MPa) — materials 모듈 참조.

자동 단면 산정 시 본 카탈로그를 단면적 오름차순으로 순회.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List
import math

from .materials import STEEL_DENSITY_KG_M3
from .geometry import SECTION_W_MM, SECTION_H_MM, SECTION_T_MM


def _shs_section_props(w: float, h: float, t: float) -> dict:
    """중공 각형강관 단면 특성 계산.

    Returns:
        A   : 단면적 (mm²)
        Iy  : y축 단면2차모멘트 (mm⁴) — 강축 (h 방향 휨)
        Iz  : z축 단면2차모멘트 (mm⁴)
        J   : 비틀림 상수 (mm⁴) — 박판 폐단면 근사 (Bredt)
        Avy : y방향 전단유효면적 (mm²)
        Avz : z방향 전단유효면적 (mm²)
    """
    # 외형 - 내형
    A = w * h - (w - 2 * t) * (h - 2 * t)
    Iy = (w * h**3 - (w - 2 * t) * (h - 2 * t) ** 3) / 12.0
    Iz = (h * w**3 - (h - 2 * t) * (w - 2 * t) ** 3) / 12.0
    # Bredt 근사 J = 4 * Am² * t / (둘레 중심선)
    Am = (w - t) * (h - t)
    perim = 2.0 * ((w - t) + (h - t))
    J = 4.0 * Am * Am * t / perim
    # 전단유효면적: 박스단면은 평행 두 벽이 전단을 받음
    Avy = 2.0 * t * (h - 2 * t)
    Avz = 2.0 * t * (w - 2 * t)
    return dict(A=A, Iy=Iy, Iz=Iz, J=J, Avy=Avy, Avz=Avz)


# 기본 SHS 200×200×8 (옛 SHS_200x200x8 단면 dict — 광범위 import 됨).
SHS_200x200x8 = _shs_section_props(SECTION_W_MM, SECTION_H_MM, SECTION_T_MM)


@dataclass(frozen=True)
class SHSSection:
    """각형강관 단면 (정사각 SHS)."""
    name: str
    w: float
    h: float
    t: float
    A: float
    Iy: float
    Iz: float
    J: float
    Avy: float
    Avz: float
    Sy: float
    Sz: float
    ry: float
    rz: float

    @classmethod
    def from_dimensions(cls, w: float, h: float, t: float) -> "SHSSection":
        """치수로부터 모든 단면 특성 계산."""
        props = _shs_section_props(w, h, t)
        Sy = props['Iy'] / (h / 2.0)
        Sz = props['Iz'] / (w / 2.0)
        ry = math.sqrt(props['Iy'] / props['A'])
        rz = math.sqrt(props['Iz'] / props['A'])

        def _fmt(x: float) -> str:
            return f"{x:g}"
        name = f"SHS {_fmt(w)}x{_fmt(h)}x{_fmt(t)}"
        return cls(
            name=name, w=w, h=h, t=t,
            A=props['A'], Iy=props['Iy'], Iz=props['Iz'], J=props['J'],
            Avy=props['Avy'], Avz=props['Avz'],
            Sy=Sy, Sz=Sz, ry=ry, rz=rz,
        )

    @property
    def weight_per_m_kg(self) -> float:
        """단위 길이당 중량 (kg/m)."""
        A_m3_per_m = self.A * 1e-6   # mm² × 1m → m³/m
        return A_m3_per_m * STEEL_DENSITY_KG_M3


# ── KS D 3568 표준 정사각 SHS 카탈로그 ──────────────────
_SHS_SPECS = [
    (75, 3.2),  (75, 4.5),
    (100, 3.2), (100, 4.5), (100, 6.0),
    (125, 4.5), (125, 6.0),
    (150, 4.5), (150, 6.0), (150, 8.0),
    (175, 6.0), (175, 8.0),
    (200, 6.0), (200, 8.0), (200, 10.0), (200, 12.0),
    (250, 8.0), (250, 10.0), (250, 12.0),
    (300, 9.0), (300, 12.0), (300, 16.0),
    (350, 12.0), (350, 16.0),
    (400, 12.0), (400, 16.0),
]


def build_shs_catalog() -> List[SHSSection]:
    """SHS 카탈로그를 단면적 오름차순으로 반환."""
    sections = [SHSSection.from_dimensions(w, w, t) for (w, t) in _SHS_SPECS]
    sections.sort(key=lambda s: s.A)
    return sections


# 모듈 임포트 시 1회 빌드
SHS_CATALOG: List[SHSSection] = build_shs_catalog()


def find_section_by_name(name: str) -> "SHSSection | None":
    """이름으로 카탈로그 검색."""
    for s in SHS_CATALOG:
        if s.name == name:
            return s
    return None


# ── 모듈 부재 표준 단면 (SHS 200×200×8) — 옛 controls_f6 매직넘버 정리 ──
# 옛 코드: 275.0 * 6144.0 = SECTION_PN_N, 275.0 * (37.39e6 / 100.0) = SECTION_MN_N_MM
# 본 모듈 단면 (SHS_200x200x8) 으로부터 정식 계산:
#   PN = Fy · A
#   MN = Fy · Sy  (Sy = Iy / (h/2))
from .materials import STEEL_FY_MPA as _FY

SECTION_PN_N = _FY * SHS_200x200x8['A']                         # 항복 축력
SECTION_MN_N_MM = _FY * (SHS_200x200x8['Iy'] / (SECTION_H_MM / 2.0))  # 항복 모멘트


__all__ = [
    '_shs_section_props',
    'SHS_200x200x8',
    'SHSSection',
    'SHS_CATALOG', 'build_shs_catalog', 'find_section_by_name',
    'SECTION_PN_N', 'SECTION_MN_N_MM',
]
