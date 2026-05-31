"""Phase 9 — 검증 보고서 자동 생성 (`run_validation`).

[설계 근거]
- `운송 로직 의사코드.md` § 2.10 그대로 구현.
- 5 사이즈 픽스처 × 3 비용 모드 × {기존 packer, 신규 packer} 돌리고
  `modular_3d/운송 검증 보고서.md` 매 실행 덮어쓰기.
- VND 무한 vs 500 회 두 결과도 함께 측정 (계획서 § 6.1, Q14).
- 신규 평균 비용 절감률 5 % 이상 + 모든 픽스처에서 ≥ 0 % (회귀 0 건) 목표.

[기존 packer 와의 비교]
- 기존: `transport/legacy_packer.py` (snapshot 의 FFD 코드 — Phase 8 직전)
- 신규: `transport/packer.py` (현재 — pack_all_seeds + VND + balance_trips)
- 비용 계산은 동일한 EcoOptions + compute_cost 사용 (모드 비교 가능)

[실행]
    python -m modular_3d.transport.benchmarks.run_validation
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from modular_3d.transport import legacy_packer
from modular_3d.transport.packer import PackResult
from modular_3d.transport.packer_core import EcoOptions, default_eco_options
from modular_3d.transport.packer_meta import (
    compute_cost, lower_bound_cost, pack_all_seeds,
)
from modular_3d.transport.tests.transport_v2_fixtures import (
    Fixture, generate_all_fixtures,
)


# ── 합격 기준 (Phase 1 결정, 계획서 § 17 / 의사코드 § 2.10) ──────
PASS_MEAN_SAVING_PCT: float = 5.0   # 신규 평균 비용이 기존보다 5% 이상 낮을 것
PASS_MAX_REGRESSION_PCT: float = 0.0  # 어느 픽스처에서도 신규 > 기존 불가


@dataclass
class _OneRun:
    """한 픽스처 × 비용 모드 × packer 의 실행 결과."""
    label: str               # "old" / "new_inf" / "new_500"
    pack: PackResult
    cost: float
    elapsed_ms: float
    blocked_count: int
    n_trips: int
    avg_weight_util: float
    avg_length_util: float
    early_exit: Optional[bool] = None  # 신규만
    lower_bound: Optional[float] = None  # 신규만


def _measure(label: str, run_fn, eco: EcoOptions, cost_mode: str,
             lower_bound: Optional[float] = None,
             early_exit: Optional[bool] = None) -> _OneRun:
    """한 packer 실행 측정 — 시간 / 비용 / 적재율."""
    t0 = time.perf_counter()
    pack = run_fn()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    cost = compute_cost(pack, cost_mode, eco)
    n = pack.total_trips
    wu = (sum(t.weight_utilization for t in pack.trips) / n) if n else 0.0
    lu = (sum(t.length_utilization for t in pack.trips) / n) if n else 0.0
    return _OneRun(
        label=label, pack=pack, cost=cost, elapsed_ms=elapsed_ms,
        blocked_count=len(pack.blocked), n_trips=n,
        avg_weight_util=wu, avg_length_util=lu,
        early_exit=early_exit, lower_bound=lower_bound,
    )


def _run_fixture_mode(
    fx: Fixture, cost_mode: str, eco: EcoOptions,
) -> Tuple[_OneRun, _OneRun, _OneRun]:
    """한 픽스처 × 한 비용 모드 → (old, new_inf, new_500) 3 결과 반환."""
    items = list(fx.modules) + list(fx.panels)

    # ── 기존 packer (FFD) — 비용은 동일한 EcoOptions + cost_mode 로 측정
    old_run = _measure(
        "old",
        lambda: legacy_packer.pack_items(fx.modules, fx.panels, fx.trucks,
                                          fx.site, fx.spacing),
        eco, cost_mode,
    )

    # ── 신규 (VND 무한 — 더 이상 개선 없음까지)
    lb_holder: List[float] = []
    ee_holder: List[bool] = []

    def _run_new_inf() -> PackResult:
        best, meta = pack_all_seeds(
            items, fx.trucks, fx.site, fx.spacing,
            cost_mode=cost_mode, eco_options=eco,
            apply_vnd=True, vnd_max_iter=None,
        )
        lb_holder.append(meta["lower_bound"])
        ee_holder.append(meta["early_exit"])
        return best

    new_inf_run = _measure("new_inf", _run_new_inf, eco, cost_mode)
    new_inf_run.lower_bound = lb_holder[0] if lb_holder else None
    new_inf_run.early_exit = ee_holder[0] if ee_holder else None

    # ── 신규 (VND 500 회 상한)
    def _run_new_500() -> PackResult:
        best, _meta = pack_all_seeds(
            items, fx.trucks, fx.site, fx.spacing,
            cost_mode=cost_mode, eco_options=eco,
            apply_vnd=True, vnd_max_iter=500,
        )
        return best

    new_500_run = _measure("new_500", _run_new_500, eco, cost_mode)

    return old_run, new_inf_run, new_500_run


def _format_section(
    fx_name: str, cost_mode: str,
    old: _OneRun, new_inf: _OneRun, new_500: _OneRun,
) -> str:
    """한 픽스처 × 비용 모드 의 보고서 섹션 (markdown 표)."""
    saving_inf_pct = (
        (old.cost - new_inf.cost) / old.cost * 100.0 if old.cost > 0 else 0.0
    )
    saving_500_pct = (
        (old.cost - new_500.cost) / old.cost * 100.0 if old.cost > 0 else 0.0
    )
    inf_vs_500_diff = (
        (new_500.cost - new_inf.cost) / new_inf.cost * 100.0
        if new_inf.cost > 0 else 0.0
    )

    lines = [
        f"### {fx_name} / 모드 `{cost_mode}`",
        "",
        "| 지표 | 기존 packer (FFD) | 신규 (VND 무한) | 신규 (VND 500회) |",
        "|---|---:|---:|---:|",
        f"| 회차 수 | {old.n_trips} | {new_inf.n_trips} | {new_500.n_trips} |",
        f"| 총 비용 (원) | {old.cost:,.0f} | {new_inf.cost:,.0f} | {new_500.cost:,.0f} |",
        f"| 평균 무게 적재율 (%) | {old.avg_weight_util:.1f} | {new_inf.avg_weight_util:.1f} | {new_500.avg_weight_util:.1f} |",
        f"| 평균 길이 적재율 (%) | {old.avg_length_util:.1f} | {new_inf.avg_length_util:.1f} | {new_500.avg_length_util:.1f} |",
        f"| 막힌 화물 수 | {old.blocked_count} | {new_inf.blocked_count} | {new_500.blocked_count} |",
        f"| 패킹 시간 (ms) | {old.elapsed_ms:.1f} | {new_inf.elapsed_ms:.1f} | {new_500.elapsed_ms:.1f} |",
        "",
        f"- **기존 대비 신규 절감률** (VND 무한): **{saving_inf_pct:+.2f} %**",
        f"- **기존 대비 신규 절감률** (VND 500회): **{saving_500_pct:+.2f} %**",
        f"- **VND 무한 vs 500 회 차이**: {inf_vs_500_diff:+.2f} % (양수 = 500회가 더 비쌈)",
    ]
    if new_inf.lower_bound is not None:
        lb_reach = (
            "도달" if new_inf.early_exit else "미도달"
        )
        lines.append(
            f"- **Lower Bound**: {new_inf.lower_bound:,.0f} 원 ({lb_reach})"
        )
    lines.append("")
    return "\n".join(lines)


def _format_summary(rows: List[Tuple[str, str, float, float, float]]) -> str:
    """전체 픽스처 × 모드 요약 — 합격 기준 비교.

    rows: [(fixture, mode, saving_inf_pct, saving_500_pct, lb_pct), ...]
    """
    if not rows:
        return ""
    saving_inf_list = [r[2] for r in rows]
    saving_500_list = [r[3] for r in rows]
    mean_inf = sum(saving_inf_list) / len(saving_inf_list)
    min_inf = min(saving_inf_list)
    mean_500 = sum(saving_500_list) / len(saving_500_list)
    min_500 = min(saving_500_list)

    pass_mean_inf = mean_inf >= PASS_MEAN_SAVING_PCT
    pass_max_inf = min_inf >= PASS_MAX_REGRESSION_PCT - 1e-6
    overall = "✅ 통과" if (pass_mean_inf and pass_max_inf) else "❌ 미달"

    lines = [
        "## 0. 합격 기준 검토",
        "",
        f"- 합격 기준 (Phase 1 결정): 평균 절감률 ≥ {PASS_MEAN_SAVING_PCT:.1f} % AND 최소 절감률 ≥ {PASS_MAX_REGRESSION_PCT:.1f} %",
        f"- **VND 무한** — 평균 절감률 {mean_inf:+.2f} % / 최저 절감률 {min_inf:+.2f} %",
        f"- **VND 500회** — 평균 절감률 {mean_500:+.2f} % / 최저 절감률 {min_500:+.2f} %",
        f"- **종합**: {overall}",
        "",
        "| 픽스처 | 모드 | 신규 무한 절감률 | 신규 500회 절감률 |",
        "|---|---|---:|---:|",
    ]
    for fx, mode, s_inf, s_500, _lb in rows:
        lines.append(f"| {fx} | `{mode}` | {s_inf:+.2f} % | {s_500:+.2f} % |")
    lines.append("")
    return "\n".join(lines)


def run_validation(
    output_path: Optional[Path] = None,
    fixture_names: Optional[List[str]] = None,
    cost_modes: Optional[List[str]] = None,
) -> Path:
    """모든 픽스처 × 비용 모드 × {기존, 신규} 돌리고 MD 보고서 생성.

    Args:
        output_path: 출력 경로 — None 이면 `modular_3d/운송 검증 보고서.md`
        fixture_names: 측정할 픽스처 이름 — None 이면 모두
        cost_modes: 측정할 비용 모드 — None 이면 3 모드 모두

    Returns:
        실제 보고서 파일 경로.
    """
    if output_path is None:
        output_path = Path(__file__).resolve().parents[2] / "운송 검증 보고서.md"
    if cost_modes is None:
        cost_modes = ["fixed_per_trip", "freight_table", "per_km"]

    eco = default_eco_options()
    fixtures = generate_all_fixtures()
    if fixture_names is not None:
        fixtures = [f for f in fixtures if f.name in fixture_names]

    lines: List[str] = [
        "# 운송 검증 보고서",
        "",
        f"- 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 대상: `legacy_packer.py` (기존 FFD) vs `packer.py` (신규 Best-Fit + VND + 무게중심 보정)",
        f"- 비용 모드: {', '.join(f'`{m}`' for m in cost_modes)}",
        f"- 픽스처: {', '.join(f.name for f in fixtures)}",
        "",
    ]

    summary_rows: List[Tuple[str, str, float, float, float]] = []
    section_lines: List[str] = []

    for fx in fixtures:
        section_lines.append(f"## 픽스처: {fx.name}")
        section_lines.append("")
        section_lines.append(
            f"- 모듈 {len(fx.modules)} / 패널 {len(fx.panels)} / "
            f"트럭 {len(fx.trucks)} 종"
        )
        section_lines.append("")
        for cost_mode in cost_modes:
            old, new_inf, new_500 = _run_fixture_mode(fx, cost_mode, eco)
            section_lines.append(_format_section(fx.name, cost_mode,
                                                   old, new_inf, new_500))
            saving_inf = (
                (old.cost - new_inf.cost) / old.cost * 100.0
                if old.cost > 0 else 0.0
            )
            saving_500 = (
                (old.cost - new_500.cost) / old.cost * 100.0
                if old.cost > 0 else 0.0
            )
            lb_pct = (
                (new_inf.cost - (new_inf.lower_bound or 0)) /
                (new_inf.lower_bound or 1) * 100.0
                if new_inf.lower_bound else 0.0
            )
            summary_rows.append((fx.name, cost_mode, saving_inf, saving_500, lb_pct))

    lines.append(_format_summary(summary_rows))
    lines.extend(section_lines)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    path = run_validation()
    print(f"보고서 생성: {path}")


__all__ = [
    "PASS_MEAN_SAVING_PCT",
    "PASS_MAX_REGRESSION_PCT",
    "run_validation",
]
