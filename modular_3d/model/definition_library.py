"""모듈/패널 정의 라이브러리 (DB).

[정책 2026-05-24 디자인 탭 2분리 — 1단계 골격]
- '정의' = 모듈 정의 탭에서 만든 컴포넌트 단위 1건. 루트(모듈/패널/수직모듈)에
  종속부재(중간보·중간기둥·캔틸레버보·캔틸레버슬래브)와 (추후)개구부·실·벽까지
  묶인 하나의 작업공간(Scene)을 직렬화해 보관한다.
- 직렬화는 scene_io 의 단일 경로(scene_to_state_dict / state_dict_to_scene)를
  그대로 재사용한다 — 저장/불러오기 포맷이 디자인 저장과 동일.
- 1단계는 세션 메모리 보관까지만. 추후 json 파일(DB)로 승격 예정.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from modular_3d.model import Scene
from modular_3d.io.scene_io import scene_to_state_dict, state_dict_to_scene


@dataclass
class ComponentDefinition:
    """정의 1건 — 이름 + 직렬화 상태(state dict) + 요약 메타.

    root_type: 루트(부모 없는) 부재의 comp_type 문자열. 배치 탭에서 1~9 버튼/키
    선택 시 이 타입으로 저장 정의를 거른다(예: 'module', 'floor_panel').
    """
    name: str
    state: dict                       # scene_to_state_dict 결과
    root_type: str = ""               # 루트 부재 comp_type 값
    component_count: int = 0
    type_summary: str = ""            # 예: 'module×1, mid_beam×4'

    def to_scene(self) -> Tuple[Scene, int]:
        """정의를 (Scene, n_floors) 로 복원."""
        return state_dict_to_scene(self.state)


def _detect_root_type(state: dict) -> str:
    """state dict 의 루트(부모 없는) 부재를 검사해 루트 타입을 반환.

    [저장 제약 — 강제]
    정의는 '하나의 루트 묶음'이어야 한다. 즉 부모 없는(parent_id==0) 부재들이
    모두 같은 group_id 에 속해야 한다(단일 모듈/패널 + 그 종속들, 또는 단일
    부재 하나). 그렇지 않으면(예: 모듈과 바닥패널이 둘 다 독립) ValueError.
    """
    comps = state.get('components', [])
    roots = [c for c in comps if int(c.get('parent_id', 0) or 0) == 0]
    if not roots:
        raise ValueError("정의에 루트(부모 없는) 부재가 없습니다.")
    groups = {int(c.get('group_id', 0) or 0) for c in roots}
    if len(groups) != 1 or (groups == {0} and len(roots) > 1):
        raise ValueError(
            "정의는 하나의 루트 묶음이어야 합니다 — 모든 부재가 한 "
            "모듈/패널에 종속되거나, 단일 부재 하나만 있어야 합니다.")
    return str(roots[0].get('comp_type', ''))


def _summarize(state: dict) -> Tuple[int, str]:
    """state dict 에서 (부재 수, 타입 요약 문자열) 계산."""
    comps = state.get('components', [])
    counts: Dict[str, int] = {}
    for c in comps:
        t = c.get('comp_type', '?')
        counts[t] = counts.get(t, 0) + 1
    summary = ", ".join(f"{t}×{n}" for t, n in sorted(counts.items()))
    return len(comps), summary


@dataclass
class DefinitionLibrary:
    """정의 라이브러리 — {이름 → ComponentDefinition}.

    [영구 저장]
    path 가 주어지면 추가/삭제 시 그 json 파일에 자동 저장하고, 생성 시 기존
    파일을 불러온다(프로그램을 다시 켜도 유지). path 가 None 이면 세션 메모리만.
    같은 이름 저장 시 덮어쓴다.
    """
    path: Optional[str] = None
    _items: Dict[str, ComponentDefinition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path:
            self._load()

    def add_from_scene(self, name: str, scene: Scene, n_floors: int = 3
                       ) -> ComponentDefinition:
        """현재 작업공간(Scene)을 이름 붙여 정의로 저장(추가/갱신)."""
        name = (name or "").strip()
        if not name:
            raise ValueError("정의 이름은 비어 있을 수 없습니다.")
        state = scene_to_state_dict(scene, n_floors)
        root_type = _detect_root_type(state)   # 제약 위반 시 ValueError
        count, summary = _summarize(state)
        d = ComponentDefinition(name=name, state=state, root_type=root_type,
                                component_count=count, type_summary=summary)
        self._items[name] = d
        self.save()
        return d

    def get(self, name: str) -> ComponentDefinition | None:
        return self._items.get(name)

    def list_names(self) -> List[str]:
        return sorted(self._items.keys())

    def remove(self, name: str) -> bool:
        if name in self._items:
            del self._items[name]
            self.save()
            return True
        return False

    def __len__(self) -> int:
        return len(self._items)

    # ── 영구 저장(json) ──────────────────────────────────
    def save(self) -> None:
        """path 가 있으면 전체 정의를 json 파일로 저장(디렉토리 자동 생성)."""
        if not self.path:
            return
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "definitions": [
                {
                    "name": d.name,
                    "root_type": d.root_type,
                    "component_count": d.component_count,
                    "type_summary": d.type_summary,
                    "state": d.state,
                }
                for d in self._items.values()
            ],
        }
        with p.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        """path 의 json 파일을 읽어 정의를 채운다. 없거나 손상 시 무시."""
        p = Path(self.path) if self.path else None
        if p is None or not p.exists():
            return
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self._items.clear()
        for item in data.get("definitions", []):
            name = item.get("name")
            if not name:
                continue
            self._items[name] = ComponentDefinition(
                name=name,
                state=item.get("state", {}),
                root_type=item.get("root_type", ""),
                component_count=int(item.get("component_count", 0)),
                type_summary=item.get("type_summary", ""),
            )


__all__ = ["ComponentDefinition", "DefinitionLibrary"]
