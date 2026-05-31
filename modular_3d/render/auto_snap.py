"""자동 갭 스냅 모듈 (2026-05-11 신규).

배치 시 마우스 위치를 기존 부재 외곽으로부터 20mm(또는 0mm) 갭이 되도록 자동 보정.

[정책]
- 트리거: 새 부재 외곽 변과 기존 부재 외곽 변 사이 거리가 100mm 이내(±)일 때
- 보정: 마우스 위치(=앵커)를 자동 이동 (사용자 답 8.a "끌어 붙임")
- 다중 인접 짝이 있을 때는 가장 가까운 1쌍에만 적용 (Phase 1 단순화).
  추후 모든 짝 동시 만족 시도 가능(사용자 답 12.b).
- 갭 매트릭스:
    MODULE ↔ MODULE/FLOOR_PANEL/VERTICAL_MODULE        : 20mm
    FLOOR_PANEL ↔ FLOOR_PANEL/VERTICAL_MODULE/MODULE   : 20mm
    VERTICAL_MODULE ↔ * (모듈류)                       : 20mm
    STRUCT_WALL ↔ FLOOR_PANEL                          : 0mm  (벽은 패널 위)
    STRUCT_WALL ↔ MODULE                               : 0mm
    STRUCT_WALL ↔ STRUCT_WALL                          : 20mm
    CORE ↔ CORE                                        : 0mm  (조립용, 한 코어 시스템)
    CORE ↔ MODULE/FLOOR_PANEL/STRUCT_WALL/V3M/...      : 20mm (RC 코어 + 모듈 시공 분리)
    (그 외 짝은 기본 20mm — 안전 디폴트)

[적용 시점]
- 호버 미리보기 + 클릭 확정 둘 다 (사용자 답 1.c).
- `_f5_world_snap` 내부에서 꼭지점 스냅 후 추가로 호출.
"""
from __future__ import annotations

from typing import Tuple, Optional


from modular_3d.model import ComponentType, instantiate
from modular_3d._utils.geometry import xy_bbox


# 트리거 거리 — 새 부재 외곽 ↔ 기존 부재 외곽 거리가 이 값 이하일 때 자동 갭 발동
SNAP_TRIGGER_MM: float = 100.0

# 갭 값 (mm)
# 갭 정책 — 카탈로그에서 가져옴.
from modular_3d.카탈로그.geometry import MODULE_JOINT_GAP_MM as _GAP
DEFAULT_GAP: float = float(_GAP)
NO_GAP: float = 0.0


# 같은 그룹 부재 타입 (모듈류 — 서로 20mm 갭)
_MODULE_LIKE = frozenset({
    ComponentType.MODULE,
    ComponentType.FLOOR_PANEL,
    ComponentType.VERTICAL_MODULE,
})

# 종속 부재 (부모와 갭 0 으로 직접 정렬되는 부재들).
# 사용자 정책 2026-05-12 e 단계: 캔틸레버 보/슬래브·구조벽·중간보/기둥 모두
# 부모와 갭 없이 부모 모서리에 직접 정렬됨. 자동 갭 정책에서도 0 으로 명시.
_DEPENDENT_ZERO_GAP = frozenset({
    ComponentType.CANTILEVER_BEAM,
    ComponentType.CANTILEVER_SLAB,
    ComponentType.STRUCT_WALL,
    ComponentType.MID_BEAM,
    ComponentType.MID_COLUMN,
})


def gap_between(type_a: ComponentType, type_b: ComponentType) -> float:
    """두 부재 타입 쌍의 갭(mm) 결정."""
    # CORE ↔ CORE: 한 코어 시스템(ㄷ·ㅁ 조립) 으로 봐서 갭 0
    if type_a == ComponentType.CORE and type_b == ComponentType.CORE:
        return NO_GAP
    # CORE ↔ 타 부재: 시공 순서 분리 (RC 코어 선시공 + 모듈 후시공) → 갭 20
    if type_a == ComponentType.CORE or type_b == ComponentType.CORE:
        return DEFAULT_GAP
    # 종속 부재 (캔틸레버·구조벽·중간보·중간기둥) — 부모와 갭 0.
    # 정책 e 단계 2026-05-12: dep_snap 이 부모 모서리에 직접 정렬하므로
    # 자동 갭에서도 0 으로 통일.
    if type_a in _DEPENDENT_ZERO_GAP or type_b in _DEPENDENT_ZERO_GAP:
        # 단 구조벽 ↔ 구조벽 은 20mm (옛 정책 유지)
        if (type_a == ComponentType.STRUCT_WALL
                and type_b == ComponentType.STRUCT_WALL):
            return DEFAULT_GAP
        return NO_GAP
    # 모듈류 끼리: 20mm
    if type_a in _MODULE_LIKE and type_b in _MODULE_LIKE:
        return DEFAULT_GAP
    # 안전 디폴트
    return DEFAULT_GAP


def _new_bbox(ctype: ComponentType, wx: float, wy: float,
              dims: dict, rotation: int, anchor: int) -> Tuple[float, float, float, float]:
    """1층 본체 XY bbox 반환.

    (2026-05-12 #133) 호버마다 instantiate(...)+sub_components 생성하는 비용을
    피하기 위해 width/depth/rotation/anchor 만으로 4 코너 직접 계산.
    외형 사각형이라 본체 부재(모듈/패널/벽/V3M 등) 의 sub_components 생성과
    결과 bbox 가 동일.
    """
    try:
        w = float(dims.get('width', 0.0))
        d = float(dims.get('depth', 0.0))
        # 앵커 오프셋 — 앵커 인덱스가 어느 코너인지에 따라 local 좌하점이 이동
        anc_offsets = {0: (0.0, 0.0), 1: (w, 0.0), 2: (w, d), 3: (0.0, d)}
        ax, ay = anc_offsets.get(int(anchor), (0.0, 0.0))
        # 로컬 4 코너 (좌하/우하/우상/좌상) — 앵커 오프셋 보정 후
        local = [(0.0 - ax, 0.0 - ay), (w - ax, 0.0 - ay),
                 (w - ax, d - ay), (0.0 - ax, d - ay)]
        # 회전 매트릭스 (90/180/270 도)
        rot = int(rotation) % 360
        xs: list = []
        ys: list = []
        for lx, ly in local:
            if rot == 0:
                rx, ry = lx, ly
            elif rot == 90:
                rx, ry = -ly, lx
            elif rot == 180:
                rx, ry = -lx, -ly
            else:  # 270
                rx, ry = ly, -lx
            xs.append(wx + rx)
            ys.append(wy + ry)
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        # 안전 폴백: 부재 정보 부족 시 작은 점 박스
        return (wx, wy, wx, wy)


def apply_auto_gap(wx: float,
                   wy: float,
                   ctype: ComponentType,
                   dims: dict,
                   rotation: int,
                   anchor: int,
                   scene) -> Tuple[float, float, bool]:
    """현재 마우스 월드 좌표(wx, wy) 를 자동 갭으로 보정한 새 좌표를 반환.

    Returns (new_wx, new_wy, snapped):
        new_wx, new_wy: 보정 후 마우스 좌표 (앵커가 자동 이동된 결과)
        snapped: 보정이 일어났는지 여부 (단순 알림용)
    """
    nx0, ny0, nx1, ny1 = _new_bbox(ctype, wx, wy, dims, rotation, anchor)

    best = None  # (axis, delta, score)

    for cid, comp in scene.components.items():
        # 1층 본체만 비교 (UI는 1층만 그리고 다층은 자동 복사)
        if getattr(comp, 'floor_index', 0) != 0 or getattr(comp, 'sub_index', 0) != 0:
            continue
        try:
            ex0, ey0, ex1, ey1 = xy_bbox(comp)
        except Exception:
            continue

        gap = gap_between(ctype, comp.comp_type)

        # ── x 방향 인접 (y 범위가 겹쳐야 후보) ─────────────
        if not (ny1 <= ey0 or ey1 <= ny0):
            # case A: 새 부재 왼쪽 변 vs 기존 오른쪽 변
            d = nx0 - ex1
            if -10.0 < d and abs(d - gap) <= SNAP_TRIGGER_MM:
                delta_x = gap - d
                score = abs(d - gap)
                if best is None or score < best[2]:
                    best = ('x', delta_x, score)
            # case B: 새 부재 오른쪽 변 vs 기존 왼쪽 변
            d = ex0 - nx1
            if -10.0 < d and abs(d - gap) <= SNAP_TRIGGER_MM:
                delta_x = -(gap - d)
                score = abs(d - gap)
                if best is None or score < best[2]:
                    best = ('x', delta_x, score)

        # ── y 방향 인접 (x 범위가 겹쳐야 후보) ─────────────
        if not (nx1 <= ex0 or ex1 <= nx0):
            # case C: 새 부재 아래 변 vs 기존 위 변
            d = ny0 - ey1
            if -10.0 < d and abs(d - gap) <= SNAP_TRIGGER_MM:
                delta_y = gap - d
                score = abs(d - gap)
                if best is None or score < best[2]:
                    best = ('y', delta_y, score)
            # case D: 새 부재 위 변 vs 기존 아래 변
            d = ey0 - ny1
            if -10.0 < d and abs(d - gap) <= SNAP_TRIGGER_MM:
                delta_y = -(gap - d)
                score = abs(d - gap)
                if best is None or score < best[2]:
                    best = ('y', delta_y, score)

    if best is None:
        return wx, wy, False
    axis, delta, _ = best
    if axis == 'x':
        return wx + delta, wy, True
    return wx, wy + delta, True
