"""Phase 9 단위 테스트 — run_validation 스크립트.

[검증 범위]
- 작은 픽스처 (min, small) 로 빠르게 실행 → 보고서 파일 생성
- 보고서 안에 필수 키워드 포함
- 합격 기준 임계값이 사용자 결정값 (5%, 0%)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modular_3d.transport.benchmarks.run_validation import (
    PASS_MAX_REGRESSION_PCT,
    PASS_MEAN_SAVING_PCT,
    run_validation,
)


def test_pass_criteria_constants():
    """Phase 1 결정 — 합격 기준 5% 평균 + 0% 회귀."""
    assert PASS_MEAN_SAVING_PCT == 5.0
    assert PASS_MAX_REGRESSION_PCT == 0.0


def test_run_validation_small_creates_report(tmp_path: Path):
    """min + small 픽스처만 빠르게 측정 → 보고서 생성 + 필수 섹션 존재."""
    output = tmp_path / "report.md"
    path = run_validation(
        output_path=output,
        fixture_names=["min", "small"],
        cost_modes=["fixed_per_trip"],
    )
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# 운송 검증 보고서" in text
    assert "min" in text
    assert "small" in text
    assert "fixed_per_trip" in text
    assert "합격 기준" in text


def test_run_validation_three_modes(tmp_path: Path):
    """3 비용 모드 모두 측정 — 보고서 안에 세 모드 모두 등장."""
    output = tmp_path / "report.md"
    path = run_validation(
        output_path=output,
        fixture_names=["min"],
        cost_modes=["fixed_per_trip", "freight_table", "per_km"],
    )
    text = path.read_text(encoding="utf-8")
    assert "fixed_per_trip" in text
    assert "freight_table" in text
    assert "per_km" in text


def test_run_validation_overwrites_existing(tmp_path: Path):
    """매 실행 덮어쓰기 — 기존 내용은 사라짐."""
    output = tmp_path / "report.md"
    output.write_text("기존 내용 — 덮어써져야 함", encoding="utf-8")
    run_validation(
        output_path=output,
        fixture_names=["min"],
        cost_modes=["fixed_per_trip"],
    )
    text = output.read_text(encoding="utf-8")
    assert "기존 내용" not in text
    assert "운송 검증 보고서" in text
