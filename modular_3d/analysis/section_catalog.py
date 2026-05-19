"""SHS 단면 카탈로그 — re-export shim (2026-05-12).

모든 실제 정의는 `modular_3d/카탈로그/steel_sections.py` 에 있음.
본 모듈은 옛 import 호환성용.

새 코드는 다음과 같이 import 권장:
    from modular_3d.카탈로그.steel_sections import (
        SHSSection, SHS_CATALOG, build_shs_catalog, find_section_by_name,
    )
"""
# noqa: F401, F403
from modular_3d.카탈로그.steel_sections import (
    SHSSection,
    SHS_CATALOG, build_shs_catalog, find_section_by_name,
    _shs_section_props,
    SHS_200x200x8,
)
from modular_3d.카탈로그.materials import (
    STEEL_E_MPA, STEEL_DENSITY_KG_M3,
    STEEL_FY_MPA, STEEL_FU_MPA,
    PHI_T, PHI_C, PHI_B, PHI_V,
)


__all__ = [
    'SHSSection',
    'STEEL_FY_MPA', 'STEEL_FU_MPA',
    'PHI_T', 'PHI_C', 'PHI_B', 'PHI_V',
    'SHS_CATALOG', 'build_shs_catalog', 'find_section_by_name',
]
