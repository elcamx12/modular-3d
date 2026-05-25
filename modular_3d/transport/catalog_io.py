"""트럭·도로 한도 카탈로그 로드/저장 (2 계층).

[2 계층 정책]
1. **패키지 내장(builtin)**: `modular_3d/transport/data/*.json`
   - 공통 기본값. 모든 사용자가 보는 카탈로그.
2. **프로젝트 오버라이드(project)**: `<project_root>/transport_config/*.json`
   - 사용자가 추가·수정한 카탈로그.
   - 같은 name 의 항목은 project 가 builtin 을 덮어쓴다 (overlay).
   - 디렉토리·파일이 없으면 자동 무시.

`load_all_trucks` / `load_all_roads` 는 두 계층을 머지해 반환한다.
`save_project_truck` / `save_project_road` 는 project 계층에만 저장한다
(내장은 항상 read-only).

[active 필터]
load_all_trucks(active_only=True) 는 active=True 인 트럭만 반환. 패킹
파이프라인(Phase 2 ~ )에서 후보 트럭 목록을 만들 때 사용.

[검증]
- JSON 형식 → 데이터클래스 변환에서 ValueError 발생 시 어느 파일의 어느
  항목인지 메시지에 포함해 추적 가능.
- validate_truck / validate_road 는 dict 인풋(예: UI 폼) 을 사전 검증하는
  편의 함수.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import Truck, RoadClass


# 패키지 내장 데이터 위치 (운송프로그램 원본 5종/3종).
_BUILTIN_DATA_DIR = Path(__file__).resolve().parent / "data"


def _project_data_dir(project_root: Path | str | None = None) -> Path:
    """프로젝트 오버라이드 디렉토리 경로 계산.

    project_root 미지정 시: 현재 작업 디렉토리(cwd) 기준 `transport_config/`.
    UI 가 명시적으로 프로젝트 루트를 알려주면 그 경로 사용.
    """
    base = Path(project_root) if project_root else Path.cwd()
    return base / "transport_config"


# ── 트럭 ──────────────────────────────────────────────────
def _load_trucks_from_file(path: Path) -> list[Truck]:
    """단일 JSON 파일에서 Truck 목록 로드. 검증 실패 시 위치 메시지 포함.

    [하위호환] 옛 파일에 더 이상 쓰지 않는 키(예: 제거된 hourly_rate_krw)가
    남아 있어도 깨지지 않도록, Truck 에 정의된 필드만 추려서 생성한다.
    """
    import dataclasses
    valid_fields = {f.name for f in dataclasses.fields(Truck)}
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"[{path}] 루트가 JSON 배열이 아닙니다.")
    trucks: list[Truck] = []
    for i, item in enumerate(raw):
        try:
            filtered = {k: v for k, v in item.items() if k in valid_fields}
            trucks.append(Truck(**filtered))
        except Exception as e:
            raise ValueError(f"[{path}] 항목 #{i} ({item.get('name', '?')}): {e}") from e
    return trucks


def load_builtin_trucks() -> list[Truck]:
    """패키지 내장 트럭 카탈로그 로드."""
    return _load_trucks_from_file(_BUILTIN_DATA_DIR / "trucks.json")


def load_project_trucks(project_root: Path | str | None = None) -> list[Truck]:
    """프로젝트 오버라이드 트럭 카탈로그 로드. 파일 없으면 빈 리스트."""
    p = _project_data_dir(project_root) / "trucks.json"
    if not p.exists():
        return []
    return _load_trucks_from_file(p)


def load_all_trucks(
    project_root: Path | str | None = None,
    active_only: bool = False,
) -> list[Truck]:
    """내장 + 프로젝트 머지. project 가 같은 name 의 builtin 항목을 덮어쓴다."""
    by_name: dict[str, Truck] = {t.name: t for t in load_builtin_trucks()}
    for t in load_project_trucks(project_root):
        by_name[t.name] = t  # 오버라이드
    trucks = list(by_name.values())
    if active_only:
        trucks = [t for t in trucks if t.active]
    return trucks


def save_project_truck(
    truck: Truck,
    project_root: Path | str | None = None,
) -> Path:
    """프로젝트 트럭 카탈로그에 1 종 저장(추가 또는 갱신).

    같은 name 이 이미 있으면 갱신, 없으면 추가. 디렉토리 자동 생성.
    """
    p = _project_data_dir(project_root) / "trucks.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if p.exists():
        with p.open("r", encoding="utf-8") as f:
            existing = json.load(f)
    new_dict = _truck_to_dict(truck)
    replaced = False
    for i, item in enumerate(existing):
        if item.get("name") == truck.name:
            existing[i] = new_dict
            replaced = True
            break
    if not replaced:
        existing.append(new_dict)
    with p.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return p


def _truck_to_dict(t: Truck) -> dict:
    return {
        "name": t.name,
        "truck_type": t.truck_type,
        "max_length": t.max_length,
        "max_width": t.max_width,
        "max_height": t.max_height,
        "max_weight": t.max_weight,
        "vehicle_height_offset": t.vehicle_height_offset,
        "curb_weight_kg": t.curb_weight_kg,
        "trailer_length_mm": t.trailer_length_mm,
        "active": t.active,
        "note": t.note,
    }


def validate_truck(data: dict) -> list[str]:
    """dict 인풋 검증. 에러 메시지 리스트 반환 (빈 리스트 = OK)."""
    try:
        Truck(**data)
        return []
    except (TypeError, ValueError) as e:
        return [str(e)]


# ── 도로 ──────────────────────────────────────────────────
def _load_roads_from_file(path: Path) -> list[RoadClass]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"[{path}] 루트가 JSON 배열이 아닙니다.")
    roads: list[RoadClass] = []
    for i, item in enumerate(raw):
        try:
            roads.append(RoadClass(**item))
        except Exception as e:
            raise ValueError(f"[{path}] 항목 #{i} ({item.get('name', '?')}): {e}") from e
    return roads


def load_builtin_roads() -> list[RoadClass]:
    return _load_roads_from_file(_BUILTIN_DATA_DIR / "road_limits.json")


def load_project_roads(project_root: Path | str | None = None) -> list[RoadClass]:
    p = _project_data_dir(project_root) / "road_limits.json"
    if not p.exists():
        return []
    return _load_roads_from_file(p)


def load_all_roads(project_root: Path | str | None = None) -> list[RoadClass]:
    by_name: dict[str, RoadClass] = {r.name: r for r in load_builtin_roads()}
    for r in load_project_roads(project_root):
        by_name[r.name] = r
    return list(by_name.values())


def save_project_road(
    road: RoadClass,
    project_root: Path | str | None = None,
) -> Path:
    p = _project_data_dir(project_root) / "road_limits.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if p.exists():
        with p.open("r", encoding="utf-8") as f:
            existing = json.load(f)
    new_dict = {
        "name": road.name,
        "max_length": road.max_length,
        "max_width": road.max_width,
        "max_height": road.max_height,
        "max_weight": road.max_weight,
    }
    replaced = False
    for i, item in enumerate(existing):
        if item.get("name") == road.name:
            existing[i] = new_dict
            replaced = True
            break
    if not replaced:
        existing.append(new_dict)
    with p.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return p


def validate_road(data: dict) -> list[str]:
    try:
        RoadClass(**data)
        return []
    except (TypeError, ValueError) as e:
        return [str(e)]


__all__ = [
    "load_builtin_trucks", "load_project_trucks", "load_all_trucks",
    "save_project_truck", "validate_truck",
    "load_builtin_roads", "load_project_roads", "load_all_roads",
    "save_project_road", "validate_road",
]
