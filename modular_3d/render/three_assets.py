"""Three.js 라이브러리 로컬 인라인 주입 — CDN 의존 제거(오프라인 지원).

[배경]
3개 three.js 뷰(정의/배치뷰·정렬뷰·운송/물량뷰)가 각자 CDN(cdnjs/jsdelivr)에서
three.min.js·OrbitControls·CSS2DRenderer 를 받아왔다. 따라서 인터넷이 없으면
모든 three.js 화면이 깨졌다. 본 모듈은 그 라이브러리들을 `render/vendor/` 에
로컬 동봉하고, HTML 의 CDN <script src> 태그를 *인라인 소스* 로 치환한다.

[왜 인라인인가]
뷰는 setHtml(html, about:blank) 로 로드된다. baseUrl 이 about:blank 이고 프로젝트
경로에 한글(비ASCII)이 섞여 있어 file:// 상대 참조가 불안정하다. 라이브러리
소스를 HTML 에 직접 박으면 경로·인코딩·네트워크에 전혀 의존하지 않는다.

[안전 폴백]
vendor 파일이 없으면 해당 태그를 그대로 둔다 → 기존 CDN 동작으로 폴백(무해).
"""
from __future__ import annotations

import re
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent / "vendor"

# (정규식 패턴, vendor 파일명) — 세 템플릿이 공통으로 쓰는 CDN 스크립트 태그.
_PATTERNS = [
    (r'<script[^>]*src="[^"]*three\.min\.js"[^>]*>\s*</script>', "three.min.js"),
    (r'<script[^>]*src="[^"]*OrbitControls\.js"[^>]*>\s*</script>', "OrbitControls.js"),
    (r'<script[^>]*src="[^"]*CSS2DRenderer\.js"[^>]*>\s*</script>', "CSS2DRenderer.js"),
]


def _read(name: str) -> str | None:
    """vendor 파일 내용. 없으면 None(폴백 신호)."""
    p = _VENDOR / name
    try:
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


def inline_three_libs(html: str) -> str:
    """HTML 의 three.js CDN <script src> 3종을 로컬 인라인 소스로 치환.

    vendor 파일이 없는 항목은 원본(CDN) 태그를 유지(안전 폴백).
    JS 내부의 백슬래시·그룹기호와 re.sub 치환 충돌을 피하려고 lambda 치환 사용.
    """
    for pat, name in _PATTERNS:
        src = _read(name)
        if src is None:
            continue  # 폴백: CDN 태그 그대로
        html = re.sub(
            pat,
            lambda _m, _s=src: "<script>\n" + _s + "\n</script>",
            html,
            count=1,
        )
    return html


def vendor_ready() -> bool:
    """세 라이브러리가 모두 로컬에 있으면 True(오프라인 가능)."""
    return all((_VENDOR / n).exists() for _, n in _PATTERNS)
