"""카테고리별 진단 로그 토글.

[목적]
프로젝트 전체에 분산된 진단 print 가 47 곳. 정상 흐름에선 유용하지만 회귀
검증·자동 테스트·exe 배포 시 콘솔 노이즈가 됨. 본 모듈은 카테고리별
ON/OFF 토글을 제공해 노이즈를 선택적으로 차단할 수 있게 한다.

[사용]
호출자 모듈:
    from modular_3d._utils.debug import dprint
    dprint('joint_rules', '[joint_rules][R02_mod_mod_v] 수직 적층 결합', 8, '쌍')

소비자 (점검·테스트 흐름):
    from modular_3d._utils import debug
    debug.disable('joint_rules')           # joint_rules 카테고리만 끔
    debug.disable_all()                    # 전부 끔
    debug.enable('joint_rules')            # 다시 켬
    with debug.silenced():                 # context manager — 블록 안만 끔
        run_regression()

환경변수:
    MODULAR_DEBUG_OFF=joint_rules,scene_io  # 시작 시 자동 비활성화

[정책]
- 기본값: 모든 카테고리 ON (운영 흐름 변화 0).
- 카테고리 이름은 print 안 `[카테고리]` 태그와 1:1.
- 알려지지 않은 카테고리는 자동으로 ON 으로 등록 (silent 기본).
"""
from __future__ import annotations

import os
import contextlib
from typing import Set


# 비활성화된 카테고리 집합. 비어있으면 모두 ON.
_DISABLED: Set[str] = set()


def _load_env_defaults() -> None:
    """환경변수에서 초기 OFF 카테고리 로드."""
    off_str = os.environ.get('MODULAR_DEBUG_OFF', '')
    if off_str:
        for cat in off_str.split(','):
            cat = cat.strip()
            if cat:
                _DISABLED.add(cat)


_load_env_defaults()


def dprint(category: str, *args, **kwargs) -> None:
    """카테고리가 활성 상태일 때만 print 호출.

    print 와 동일한 시그니처 (sep, end, file 등 그대로 전달).
    카테고리가 비활성이면 호출 부담만 무시.
    """
    if category in _DISABLED:
        return
    print(*args, **kwargs)


def disable(category: str) -> None:
    """특정 카테고리 비활성화."""
    _DISABLED.add(category)


def enable(category: str) -> None:
    """특정 카테고리 활성화 (기본 상태로 복원)."""
    _DISABLED.discard(category)


def disable_all(*, known: tuple = (
    'joint_rules', 'ops_builder', 'ops_solver', 'topology',
    'scene_io', 'joint_recorder', 'viewer', 'diaphragm',
    'VIS', 'DBG-DIA', 'ANALYSIS',
)) -> None:
    """알려진 모든 카테고리 비활성화. 알려지지 않은 카테고리는 영향 없음."""
    _DISABLED.update(known)


def enable_all() -> None:
    """모든 카테고리 활성화 (비활성 집합 비우기)."""
    _DISABLED.clear()


def is_enabled(category: str) -> bool:
    return category not in _DISABLED


@contextlib.contextmanager
def silenced(*categories: str):
    """context manager — 지정 카테고리(없으면 모두) 를 블록 안 OFF.

    예:
        with silenced('joint_rules', 'scene_io'):
            run_build()
    """
    if categories:
        prev = {c: c in _DISABLED for c in categories}
        for c in categories:
            _DISABLED.add(c)
        try:
            yield
        finally:
            for c, was_off in prev.items():
                if not was_off:
                    _DISABLED.discard(c)
    else:
        prev_all = set(_DISABLED)
        disable_all()
        try:
            yield
        finally:
            _DISABLED.clear()
            _DISABLED.update(prev_all)
