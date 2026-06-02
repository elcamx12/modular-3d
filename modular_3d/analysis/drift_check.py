"""층간변위비(drift ratio) 검토 — KDS 41 17 지진 / 사용성 풍 (L4-1, read-only).

[배경]
해석은 층별 횡변위(OpsResults.story_disp: z레벨→(δx,δy))를 이미 구하지만, 이를
층고로 나눈 층간변위비를 허용한계와 비교하는 검토가 없었다. 본 모듈은 *해석
결과를 읽어* 층간변위비를 산출·판정만 한다 — 기존 해석/계산은 건드리지 않는다.

[허용한계 — 18~20층 공동주택 통상값 (사용자 확정, 상수 고정)]
  - 지진(Ex/Ey): 0.015·hsx (KDS 41 17 I등급 수준)
  - 풍(Wx/Wy) 사용성: 1/400·hsx
전도방지 조합(_OT, 0.9D±횡력)은 변위 검토 대상이 아니라 제외한다.

[한계 — 1차]
탄성 해석 변위 기준(변위증폭계수 Cd 미반영). 층고는 인접 다이어프램 z 차이로
산출하고, 최하층은 지면(변위 0, 고정) 기준으로 본다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# ── KDS 허용 층간변위비 (상수 고정) ──────────────────────────
SEISMIC_DRIFT_LIMIT = 0.015        # 지진: 0.015·층고
WIND_DRIFT_LIMIT = 1.0 / 400.0     # 풍 사용성: 1/400·층고


def _limit_for_case(case_key: str) -> Optional[float]:
    """케이스 키 → 허용 층간변위비. 지진 E·풍 W. 전도방지(_OT)·중력은 None(제외)."""
    c = (case_key or '').strip()
    if '_OT' in c.upper() or 'OT' == c.upper():
        return None
    cu = c.upper()
    if cu.startswith('E'):
        return SEISMIC_DRIFT_LIMIT
    if cu.startswith('W'):
        return WIND_DRIFT_LIMIT
    return None


@dataclass
class StoryDrift:
    """한 층(z_bot→z_top)의 층간변위비 검토 결과."""
    z_bot: float        # 아래 다이어프램 z (mm). 최하층은 0(지면).
    z_top: float        # 위 다이어프램 z (mm)
    height: float       # 층고 (mm)
    drift_x: float      # X 층간변위비 (무차원)
    drift_y: float      # Y 층간변위비
    limit: float        # 허용한계
    ok_x: bool
    ok_y: bool


def compute_story_drifts(res, limit: Optional[float]) -> List[StoryDrift]:
    """OpsResults 하나 → 층간변위비 리스트(아래→위).

    limit 가 None(중력/전도방지)이거나 story_disp 가 비면 빈 리스트.
    최하층은 지면(z=0, 변위 0) 기준. 인접 다이어프램 z 차이를 층고로 사용.
    """
    if limit is None:
        return []
    sd: Dict[float, tuple] = getattr(res, 'story_disp', None) or {}
    if not sd:
        return []
    out: List[StoryDrift] = []
    prev_z = 0.0
    prev_dx = 0.0
    prev_dy = 0.0
    for z in sorted(sd.keys()):
        dx, dy = sd[z]
        h = z - prev_z
        if h <= 0:
            prev_z, prev_dx, prev_dy = z, float(dx), float(dy)
            continue
        rx = abs(float(dx) - prev_dx) / h
        ry = abs(float(dy) - prev_dy) / h
        out.append(StoryDrift(
            z_bot=prev_z, z_top=z, height=h,
            drift_x=rx, drift_y=ry, limit=limit,
            ok_x=(rx <= limit), ok_y=(ry <= limit),
        ))
        prev_z, prev_dx, prev_dy = z, float(dx), float(dy)
    return out


def compute_all_drifts(results) -> Dict[str, List[StoryDrift]]:
    """해석 결과 모음 → {케이스키: [StoryDrift]}. 횡력(지진/풍) 케이스만 포함.

    results: {case_key: OpsResults} dict 또는 [OpsResults] 리스트.
      dict 면 키로 지진/풍 판별(정확). 리스트면 OpsResults.case_name 으로 판별.
    """
    if isinstance(results, dict):
        pairs = list(results.items())
    else:
        pairs = [(getattr(r, 'case_name', ''), r) for r in (results or [])]

    out: Dict[str, List[StoryDrift]] = {}
    for key, res in pairs:
        rows = compute_story_drifts(res, _limit_for_case(key))
        if rows:
            out[key] = rows
    return out


def worst_drift(rows: List[StoryDrift]):
    """층 목록에서 (최대 X비, 최대 Y비, NG 층 수) 요약."""
    if not rows:
        return (0.0, 0.0, 0)
    mx = max(r.drift_x for r in rows)
    my = max(r.drift_y for r in rows)
    ng = sum(0 if (r.ok_x and r.ok_y) else 1 for r in rows)
    return (mx, my, ng)


__all__ = [
    'SEISMIC_DRIFT_LIMIT', 'WIND_DRIFT_LIMIT',
    'StoryDrift', 'compute_story_drifts', 'compute_all_drifts', 'worst_drift',
]
