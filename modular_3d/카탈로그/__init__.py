"""매직넘버·재료·단면 카탈로그 — 본부 (2026-05-12 신규).

[목적]
프로젝트 전반에 분산된 매직넘버·재료·단면 상수를 본 폴더 한 곳에 모음.
"체육복 사이즈" 처럼 한 곳만 바꾸면 모든 사용처가 자동 따라가도록 단일
진실 원천 (Single Source of Truth) 구축.

[하위 모듈]
- geometry      : 부재 치수 (모듈 높이, 단면 폭, 갭, 슬래브 두께 등)
- tolerances    : 거리·임계 허용오차 (노드 병합, 스냅, 인터페이스 등)
- materials     : 강재·콘크리트 물성
- loads         : 하중·하중조합·압력
- steel_sections: SHS 강관 단면 카탈로그
- rigid         : OpenSees 가상 강체 강성

[사용]
    from modular_3d.카탈로그 import MODULE_HEIGHT_MM, SECTION_W_MM
    from modular_3d.카탈로그.geometry import FLOOR_HEIGHT
    from modular_3d.카탈로그.steel_sections import SHS_CATALOG

[호환성]
기존 `modular_3d.analysis.constants` 와 `modular_3d.analysis.section_catalog`
모듈은 본 카탈로그를 re-export 만 하는 shim 으로 남음. 옛 import 그대로 작동.
"""
from .geometry import *           # noqa: F401, F403
from .tolerances import *          # noqa: F401, F403
from .materials import *           # noqa: F401, F403
from .loads import *               # noqa: F401, F403
from .rigid import *               # noqa: F401, F403
