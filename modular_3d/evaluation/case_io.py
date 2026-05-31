"""평가 케이스 저장·로드 (.case.json).

[설계 근거]
- 평가탭_구축_계획서.md §3-7, Phase N.
- 완전 스냅샷 — scene + ProjectSettings + 평가 화면 표시값.
- 다른 탭의 원본 결과(DesignResult·PackResult)는 직렬화가 까다로워 *어댑터 산출
  dict* 만 저장. 비교 후 "이 안으로 마감 작업 이어가기" 는 scene 을 다시 불러와
  앞 탭들을 재계산하는 흐름.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Dict, Optional


_CASE_KIND = "modular_case_v1"


def _safe_asdict(obj: Any) -> Any:
    """ProjectSettings 같은 dataclass 를 dict 로. 실패 시 빈 dict."""
    if obj is None:
        return {}
    if is_dataclass(obj):
        try:
            return asdict(obj)
        except Exception:
            return {}
    if isinstance(obj, dict):
        return obj
    return {}


def save_case(
    path: str,
    scene_state: Optional[Dict[str, Any]],
    project_settings: Any,
    evaluation_data: Optional[Dict[str, Any]],
    name: Optional[str] = None,
    layout_png_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    """케이스 파일 저장.

    Args:
        path: 저장할 파일 경로 (.case.json 권장)
        scene_state: io.scene_io.scene_to_state_dict() 결과 또는 None
        project_settings: ProjectSettings 인스턴스 또는 None
        evaluation_data: build_evaluation_data() 결과 또는 None
        name: 케이스 이름 (선택)
        layout_png_bytes: 1층 평면도 PNG raw bytes (선택). 비교탭에서 표시용.

    Returns:
        직렬화된 dict (호출자 확인용)
    """
    # [2026-05-31] 비교탭에서 평면도를 보여주기 위해 PNG 바이트를 base64 로
    # 인라인 저장. 단일 .case.json 파일 정책 유지 (PDF 별도 없음).
    import base64
    layout_b64 = None
    if layout_png_bytes:
        try:
            layout_b64 = base64.b64encode(layout_png_bytes).decode("ascii")
        except Exception:
            layout_b64 = None

    case = {
        "kind": _CASE_KIND,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "name": name or "",
        "scene": scene_state or {},
        "project_settings": _safe_asdict(project_settings),
        "results": {
            "evaluation": evaluation_data or {},
        },
        "images": {
            "layout_png_b64": layout_b64,
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False, indent=2)
    return case


def load_case(path: str) -> Dict[str, Any]:
    """케이스 파일 로드. kind 검증.

    Returns:
        파일 dict. kind 가 다르면 ValueError.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("kind") != _CASE_KIND:
        raise ValueError(f"케이스 파일 형식이 다릅니다: kind={data.get('kind')}")
    return data


__all__ = ["save_case", "load_case"]
