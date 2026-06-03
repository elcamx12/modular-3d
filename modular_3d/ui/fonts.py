"""한국어 산세리프 폰트 등록 — Freesentation(본문) / Paperlogy(헤드라인).

[2026-06-01]
- source_repo/프리젠테이션/Freesentation-{1Thin..9Black}.ttf 9 weight
- source_repo/페이퍼로지/Paperlogy-{1Thin..9Black}.ttf 9 weight
- 앱 부팅 시 QFontDatabase.addApplicationFont 로 모두 등록.
- 패밀리 이름은 OS 에 설치되지 않은 상태라도 본 앱 내에서 사용 가능.

[사용]
    from modular_3d.ui.fonts import F_BODY, F_HEAD, ensure_fonts_loaded
    ensure_fonts_loaded()
    label.setStyleSheet(f"font-family: '{F_HEAD}'; font-weight: 800;")
"""
from __future__ import annotations

from pathlib import Path
from typing import Set

from PyQt5.QtGui import QFontDatabase


# 폰트 파일 위치 — source_repo 루트 기준 두 폴더.
_REPO_ROOT = Path(__file__).resolve().parents[2]  # source_repo
_DIR_FREESENT = _REPO_ROOT / "프리젠테이션"
_DIR_PAPERLOGY = _REPO_ROOT / "페이퍼로지"

# [중요] addApplicationFont 로 등록하면 모든 weight 가 *하나의 패밀리명*
#   ("Freesentation", "Paperlogy") 아래 묶입니다. weight 별 긴 이름
#   ("Freesentation 4 Regular" 등)은 패밀리로 매칭되지 않아 Malgun Gothic 으로
#   폴백됩니다. 따라서 family 에는 짧은 이름을 쓰고, 굵기는 CSS font-weight /
#   QFont.setWeight 로 지정해야 합니다.

# 본문 (Freesentation) — 굵기는 font-weight 로 제어
F_BODY            = "Freesentation"
F_BODY_MEDIUM     = "Freesentation"
F_BODY_SEMIBOLD   = "Freesentation"
F_BODY_BOLD       = "Freesentation"

# 헤드라인 (Paperlogy) — 굵기는 font-weight 로 제어
F_HEAD            = "Paperlogy"
F_HEAD_SEMIBOLD   = "Paperlogy"
F_HEAD_EXTRABOLD  = "Paperlogy"
F_HEAD_BLACK      = "Paperlogy"
F_HEAD_REGULAR    = "Paperlogy"

_loaded = False
_families: Set[str] = set()


def ensure_fonts_loaded() -> None:
    """앱 부팅 후 한 번 호출. 두 폰트의 모든 weight 를 application font 로 등록.

    이미 로드됐으면 즉시 반환.
    """
    global _loaded
    if _loaded:
        return
    for d in (_DIR_FREESENT, _DIR_PAPERLOGY):
        if not d.exists():
            continue
        for path in d.glob("*.ttf"):
            try:
                fid = QFontDatabase.addApplicationFont(str(path))
                if fid >= 0:
                    for fam in QFontDatabase.applicationFontFamilies(fid):
                        _families.add(str(fam))
            except Exception:
                pass
    _loaded = True


def loaded_families() -> Set[str]:
    """등록된 패밀리 이름 집합 (디버그 용)."""
    ensure_fonts_loaded()
    return set(_families)
