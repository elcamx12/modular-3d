"""구조해석 상수 — re-export shim (2026-05-12).

본 모듈은 호환성 유지용. 모든 상수는 `modular_3d/카탈로그/` 폴더 안에
목적별로 정의되어 있으며 본 모듈은 그것을 한 곳에서 import 해 재공개한다.

새 코드는 다음과 같이 import 권장:
    from modular_3d.카탈로그 import MODULE_HEIGHT_MM, FP_SUPPORT_TOL_XY
    from modular_3d.카탈로그.steel_sections import SHS_200x200x8

본 shim 은 옛 `from modular_3d.analysis.constants import *` 패턴을 그대로
지원한다.
"""
# noqa: F401, F403
from modular_3d.카탈로그.geometry import (
    MODULE_HEIGHT_MM, MODULE_JOINT_GAP_MM, FLOOR_HEIGHT,
    VERTICAL_MODULE_FLOORS,
    SECTION_W_MM, SECTION_H_MM, SECTION_T_MM,
    SLAB_THICKNESS_MM,
    CORE_TRUSS_GRID_MM,
)
from modular_3d.카탈로그.tolerances import (
    NODE_MERGE_TOL_MM, LINE_COLINEAR_TOL, OVERLAP_TOL_MM,
    JOINT_BRIDGE_TOL_MM, JOINT_HORIZ_CONNECT_MAX_MM,
    FP_SUPPORT_TOL_XY, FP_SUPPORT_TOL_Z,
    EQUILIBRIUM_TOL, BENCHMARK_TOL,
)
from modular_3d.카탈로그.materials import (
    G_M_PER_S2,
    STEEL_E_MPA, STEEL_NU, STEEL_G_MPA,
    STEEL_DENSITY_KG_M3,
    STEEL_UNIT_WEIGHT_N_M3, STEEL_UNIT_WEIGHT_N_MM3,
    CONCRETE_E_MPA, CONCRETE_DENSITY_KG_M3,
    CONCRETE_UNIT_WEIGHT_N_M3, CONCRETE_UNIT_WEIGHT_N_MM3,
)
from modular_3d.카탈로그.loads import (
    LIVE_LOAD_KN_M2, LIVE_LOAD_N_MM2,
    LOAD_COMBO_DL, LOAD_COMBO_LL,
    SLAB_ONE_WAY_RATIO,
)
from modular_3d.카탈로그.steel_sections import (
    _shs_section_props,
    SHS_200x200x8,
)
