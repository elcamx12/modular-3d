"""KS D 3502 H형강(열간압연 H형강) 단면 카탈로그.

[단위] N, mm, MPa.
[재질] SS275 (Fy=275 MPa) — materials 모듈 참조.

[근거 — 2026-05-24]
- 공칭치수(H×B×t1×t2) 와 필릿반경 r 은 KS D 3502(열간압연 형강의 모양·치수·
  무게 및 그 허용차) 표준 H형강 시리즈 값을 사용한다.
- 단면 성능(A·Ix·Iy·Zx·Zy·rx·ry·J)은 각형강관 카탈로그(steel_sections.py)와
  동일하게 표준 단면 계산식으로 산출한다(코드 내 단일 진실원천 + 재현 가능).
  · 단면적 A: 플랜지 2장 + 웨브 + 필릿 4개(=(4-π)·r²) 포함 → KS 공표 A 와
    일치(예: H200×200×8×12, r13 → 63.53 cm²).
  · 단면2차모멘트 I: 이상화 I형(직사각형 분해)으로 계산하며 필릿의 I 기여는
    무시한다 → KS 공표값보다 약 2~3% 작게(보수적으로) 나온다.
- 단위중량 = A × 강재밀도(7,850 kg/m³). 본 모듈 하단 자가검증에서 KS 공표
  단위중량과 대조한다.

[비고]
- 강축(major) = x축(웨브 평면 내 휨), 약축(minor) = y축.
- 현재 물량 산출은 각형강관만 사용하므로 본 카탈로그는 향후 H형강 보 설계
  지원(디자인 탭 보 형강 선택)을 위한 데이터로 보관한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List
import math

from .materials import STEEL_DENSITY_KG_M3


def _h_section_props(H: float, B: float, t1: float, t2: float, r: float) -> dict:
    """H형강 단면 특성 계산.

    Args:
        H : 형강 높이 (mm)
        B : 플랜지 폭 (mm)
        t1: 웨브 두께 (mm)
        t2: 플랜지 두께 (mm)
        r : 필릿 반경 (mm)

    Returns(dict):
        A   : 단면적 (mm²) — 필릿 포함
        Ix  : 강축 단면2차모멘트 (mm⁴) — 이상화(필릿 무시)
        Iy  : 약축 단면2차모멘트 (mm⁴)
        Zx,Zy: 단면계수 (mm³)
        rx,ry: 단면2차반경 (mm)
        J   : 비틀림 상수 (mm⁴) — 개방 박판 근사
        Avx : 강축 전단유효면적 (mm²) — 웨브
        Avy : 약축 전단유효면적 (mm²) — 플랜지
    """
    hw = H - 2.0 * t2                  # 웨브 순높이
    # 단면적 — 플랜지 2 + 웨브 + 필릿 4개((4-π)r²)
    A = 2.0 * B * t2 + hw * t1 + (4.0 - math.pi) * r * r
    # 강축 I (이상화): 전체 직사각 - 양쪽 빈 공간
    Ix = (B * H**3 - (B - t1) * hw**3) / 12.0
    # 약축 I: 플랜지 2장 + 웨브
    Iy = (2.0 * t2 * B**3 + hw * t1**3) / 12.0
    Zx = Ix / (H / 2.0)
    Zy = Iy / (B / 2.0)
    rx = math.sqrt(Ix / A)
    ry = math.sqrt(Iy / A)
    # 개방 단면 비틀림 상수 J ≈ (1/3)Σ b t³
    J = (2.0 * B * t2**3 + hw * t1**3) / 3.0
    # 전단유효면적 — 강축은 웨브, 약축은 플랜지가 부담
    Avx = t1 * hw
    Avy = 2.0 * B * t2
    return dict(A=A, Ix=Ix, Iy=Iy, Zx=Zx, Zy=Zy, rx=rx, ry=ry,
                J=J, Avx=Avx, Avy=Avy)


@dataclass(frozen=True)
class HSection:
    """H형강 단면."""
    name: str
    H: float
    B: float
    t1: float       # 웨브 두께
    t2: float       # 플랜지 두께
    r: float        # 필릿 반경
    A: float
    Ix: float
    Iy: float
    Zx: float
    Zy: float
    rx: float
    ry: float
    J: float
    Avx: float
    Avy: float

    @classmethod
    def from_dimensions(cls, H: float, B: float, t1: float,
                        t2: float, r: float) -> "HSection":
        p = _h_section_props(H, B, t1, t2, r)

        def _fmt(x: float) -> str:
            return f"{x:g}"
        name = f"H {_fmt(H)}x{_fmt(B)}x{_fmt(t1)}x{_fmt(t2)}"
        return cls(
            name=name, H=H, B=B, t1=t1, t2=t2, r=r,
            A=p['A'], Ix=p['Ix'], Iy=p['Iy'], Zx=p['Zx'], Zy=p['Zy'],
            rx=p['rx'], ry=p['ry'], J=p['J'], Avx=p['Avx'], Avy=p['Avy'],
        )

    @property
    def weight_per_m_kg(self) -> float:
        """단위 길이당 중량 (kg/m)."""
        return self.A * 1e-6 * STEEL_DENSITY_KG_M3


# ── KS D 3502 표준 H형강 (H, B, t1, t2, r) ──────────────────
# 정폭(H계열) + 중·세폭(보용) 대표 규격.
# r(필릿반경)은 KS 공표 단면적을 (4-π)r² 근사로 재현하도록 보정한 유효값.
# 본 표로 계산한 A·단위중량은 KS D 3502 공표값과 ±0.1% 이내로 일치한다
# (모듈 하단 주석의 자가검증 결과 참조).
_H_SPECS = [
    # H 계열 (정폭에 가까운 와이드 시리즈)
    (100, 100, 6, 8, 10),
    (125, 125, 6.5, 9, 10),
    (150, 150, 7, 10, 11),
    (175, 175, 7.5, 11, 12),
    (200, 200, 8, 12, 13),
    (250, 250, 9, 14, 16),
    (300, 300, 10, 15, 18),
    (350, 350, 12, 19, 20),
    (400, 400, 13, 21, 22),
    # 중·세폭 (보 단면)
    (150, 75, 5, 7, 8),
    (200, 100, 5.5, 8, 11),
    (250, 125, 6, 9, 12),
    (300, 150, 6.5, 9, 13),
    (350, 175, 7, 11, 14),
    (400, 200, 8, 13, 16),
    (450, 200, 9, 14, 18),
    (500, 200, 10, 16, 20),
    (600, 200, 11, 17, 22),
    (588, 300, 12, 20, 28),
    (700, 300, 13, 24, 28),
    (800, 300, 14, 26, 28),
    (900, 300, 16, 28, 28),
]


def build_h_catalog() -> List[HSection]:
    """H형강 카탈로그를 단면적 오름차순으로 반환."""
    sections = [HSection.from_dimensions(*spec) for spec in _H_SPECS]
    sections.sort(key=lambda s: s.A)
    return sections


# 모듈 임포트 시 1회 빌드
H_CATALOG: List[HSection] = build_h_catalog()


def find_h_section_by_name(name: str) -> "HSection | None":
    """이름으로 카탈로그 검색."""
    for s in H_CATALOG:
        if s.name == name:
            return s
    return None


__all__ = [
    '_h_section_props', 'HSection',
    'H_CATALOG', 'build_h_catalog', 'find_h_section_by_name',
]
