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

# [중요] 이 폰트들은 weight 별로 *별도 패밀리명* 으로 등록됩니다:
#   "Freesentation 4 Regular", "Paperlogy 7 Bold" 등.
# CSS font-family 에 정확한 이름을 써야 합니다.

# 본문 (Freesentation)
F_BODY            = "Freesentation 4 Regular"
F_BODY_MEDIUM     = "Freesentation 5 Medium"
F_BODY_SEMIBOLD   = "Freesentation 6 SemiBold"
F_BODY_BOLD       = "Freesentation 7 Bold"

# 헤드라인 (Paperlogy)
F_HEAD            = "Paperlogy 7 Bold"
F_HEAD_SEMIBOLD   = "Paperlogy 6 SemiBold"
F_HEAD_EXTRABOLD  = "Paperlogy 8 ExtraBold"
F_HEAD_BLACK      = "Paperlogy 9 Black"
F_HEAD_REGULAR    = "Paperlogy 4 Regular"

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
