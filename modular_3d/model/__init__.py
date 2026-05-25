"""modular_3d.model 패키지 — 데이터 모델.

[구조]
- core.py        : Component / Module / FloorPanel / StructWall / 캔틸 / 중간보·기둥 /
                   Vertical3Module / Scene / Action / AppState / ComponentType /
                   BeamData / ColumnData / SlabData
- multi_floor.py : 다층 그룹 생성·삭제·이동 헬퍼

[호환성]
기존 `from modular_3d.model import X` 는 본 __init__.py 의 re-export 로 그대로 동작.
"""
from modular_3d.model.core import *  # noqa: F401, F403
from modular_3d.model.room import Room  # noqa: F401
from modular_3d.model.core import (  # noqa: F401
    AppState, ComponentType,
    BeamData, ColumnData, SlabData,
    Component, Module, FloorPanel, StructWall, InteriorWall,
    CantileverBeam, CantileverSlab, MidBeam, MidColumn, Vertical3Module,
    Core, CoreSlab,
    Scene, Action,
    # 내부 헬퍼 (밑줄 접두) 도 외부에서 import 하는 곳이 있어 재노출
    _rotate_point, _rotate_local_to_world,
    # 통합 dispatch (2026-05-08 단순화)
    TYPE_TO_CLASS, TYPE_NAMES, instantiate,
)
