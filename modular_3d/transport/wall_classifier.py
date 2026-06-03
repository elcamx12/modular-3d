"""비내력벽 외부/내부 자동 판별 (분석 ⑥).

[알고리즘 — 사용자 채택안]
20mm 갭 정책을 활용한 인접성 검사. 모듈의 한 면을 사방으로
`gap_mm + eps_mm` 만큼 외측 확장한 박스(slab) 를 만들어, 그 박스가 다른 부재
(다른 모듈·플로어패널·벽패널 등) 의 AABB 와 교차하면 그 영역은 "내부 노출",
나머지는 "외부 노출". 면을 segment_size_mm 단위로 잘라 외부 노출 길이 비율을
산출 → 분류 라벨 없이 비율 자체를 가중치로 사용 (사용자 결정 ⑥-1·2).

[가중치 사용]
`face_unit_weight(face_class, options)` =
    exterior_ratio × exterior_unit + (1 - exterior_ratio) × interior_unit

[수직 3 모듈]
한 부재가 3 층을 차지하므로 층별 4 면 × 3 층 = 12 면 분류
(사용자 결정 ⑥-4).

[FloorPanel 장애물]
사용자 결정 ⑥-3: 발코니/플로어패널이 모듈 면을 가리면 그 부분도 내부
노출로 본다. 기본 ON. UI 옵션 토글.

[모듈 회전 단순화]
씬의 모듈 회전은 0/90/180/270 (수직 회전) 뿐이므로 회전 후에도 모든 면이
축 정렬. 따라서 axis-aligned AABB 검사로 충분 (성능·정확성 모두 양호).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from ..model.core import (
    Component, ComponentType, Module, FloorPanel, StructWall,
    Vertical3Module, Core, CoreSlab, Scene,
)
from ..카탈로그.geometry import FLOOR_HEIGHT


# 면 이름 → outward normal (XY 평면, +Y=후면, -Y=전면, +X=우면, -X=좌면).
# 모듈/v3module 의 로컬 좌표계: width=X, depth=Y, height=Z. 회전은 +Z 축 기준.
FACE_NAMES: Tuple[str, str, str, str] = ("전면", "우면", "후면", "좌면")


# ── 결과 데이터클래스 ────────────────────────────────────
@dataclass
class FaceClass:
    """한 면의 외부 노출 비율 + 인접 부재 ID (디버그용).

    [사용자 결정 ⑥-1] kind 라벨 폐기. exterior_ratio 자체가 가중치.
    """
    exterior_ratio: float                     # 0.0 ~ 1.0
    contacting_cids: List[int] = field(default_factory=list)


# ── 옵션 ──────────────────────────────────────────────────
@dataclass(frozen=True)
class ClassifierOptions:
    """wall_classifier 호출 시 주는 옵션. 어댑터의 TransportOptions 일부만 추림."""
    gap_mm: float = 20.0                      # 모듈 사이 표준 갭
    eps_mm: float = 5.0                       # 안전 마진
    segment_size_mm: float = 100.0            # 면 세그먼트 분할 크기
    include_floor_panels: bool = True         # 사용자 결정 ⑥-3
    enabled: bool = True                      # OFF → 모두 내부로 (ratio=0)


# ── AABB 유틸 ────────────────────────────────────────────
def _component_aabb_xy(comp: Component) -> Tuple[np.ndarray, np.ndarray]:
    """컴포넌트의 XY 평면 AABB (min_xy, max_xy)."""
    mn, mx = comp.get_bounding_box()
    return mn[:2].copy(), mx[:2].copy()


def _component_z_range(comp: Component) -> Tuple[float, float]:
    """컴포넌트의 z 범위."""
    mn, mx = comp.get_bounding_box()
    return float(mn[2]), float(mx[2])


def _box_intersect(a_min: np.ndarray, a_max: np.ndarray,
                   b_min: np.ndarray, b_max: np.ndarray) -> bool:
    """축 정렬 박스 2D 교차."""
    return (a_max[0] > b_min[0] and a_min[0] < b_max[0]
            and a_max[1] > b_min[1] and a_min[1] < b_max[1])


def _z_overlap(a: Tuple[float, float], b: Tuple[float, float],
               tol: float = 1.0) -> bool:
    """z 범위 겹침 (tol mm 허용)."""
    return a[1] > b[0] - tol and a[0] < b[1] + tol


# ── 모듈 한 면 좌표 (axis-aligned, 회전 0/90/180/270 가정) ─────────
def _module_face_segment_xy(
    comp: Component, face_name: str, z_bot: float, z_top: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """모듈/v3module 한 면의 외측 평면 좌표 (XY) + outward normal.

    Returns:
        p0_xy, p1_xy, normal_xy — 면의 두 끝점(XY) 과 외측 단위 벡터(XY).
        면 길이 = ||p1 - p0||. z 는 외부에서 z_bot/z_top 로 제공.

    회전 후 좌표를 그대로 사용하기 위해 컴포넌트의 월드 코너 8 개에서
    아래쪽 4 개(z=z_bot) 를 추출하고, 회전을 고려한 면 매핑을 한다.
    """
    corners = comp.get_world_corners()
    # 아래쪽 4 개 = 0..3 (회전 전 로컬: (0,0), (w,0), (w,d), (0,d))
    # 회전 후 월드에서도 그 순서가 유지된다.
    c0 = corners[0][:2]   # 로컬 (0,0)
    c1 = corners[1][:2]   # 로컬 (w,0)
    c2 = corners[2][:2]   # 로컬 (w,d)
    c3 = corners[3][:2]   # 로컬 (0,d)
    # 면 매핑 — 로컬 (y=0)=전면, (x=w)=우면, (y=d)=후면, (x=0)=좌면.
    if face_name == "전면":
        p0, p1 = c0, c1
    elif face_name == "우면":
        p0, p1 = c1, c2
    elif face_name == "후면":
        p0, p1 = c2, c3
    elif face_name == "좌면":
        p0, p1 = c3, c0
    else:
        raise ValueError(f"unknown face_name: {face_name}")
    # outward normal — 면 위치에서 컴포넌트 중심으로 향하는 반대 방향
    center = (c0 + c1 + c2 + c3) / 4.0
    mid = (p0 + p1) / 2.0
    n_raw = mid - center
    n = n_raw / max(float(np.linalg.norm(n_raw)), 1e-9)
    return p0.copy(), p1.copy(), n


# ── 면 분류 핵심 ─────────────────────────────────────────
def _classify_face_xy(
    p0: np.ndarray, p1: np.ndarray, normal: np.ndarray,
    z_bot: float, z_top: float,
    others_aabb: List[Tuple[int, np.ndarray, np.ndarray, Tuple[float, float]]],
    options: ClassifierOptions,
) -> FaceClass:
    """면의 외측 슬랩을 segment 단위로 잘라 외부 비율 산출.

    others_aabb: [(cid, min_xy, max_xy, (z_min, z_max)), ...] — z 범위 함께.
    """
    if not options.enabled:
        return FaceClass(exterior_ratio=0.0, contacting_cids=[])

    face_vec = p1 - p0
    face_len = float(np.linalg.norm(face_vec))
    if face_len < 1e-6:
        return FaceClass(exterior_ratio=1.0, contacting_cids=[])
    u = face_vec / face_len               # 면 따라 진행 단위 벡터
    n = normal                            # 외측 단위 벡터
    off = options.gap_mm + options.eps_mm

    n_seg = max(int(face_len / options.segment_size_mm), 10)
    seg_len = face_len / n_seg
    contacting_cids: list = []
    interior_len = 0.0

    for i in range(n_seg):
        # 세그먼트의 base 두 끝점 (면 위)
        s0 = p0 + u * (i * seg_len)
        s1 = p0 + u * ((i + 1) * seg_len)
        # 외측으로 off 만큼 밀어 슬랩 박스 (4점 axis-aligned XY 영역)
        s0o = s0 + n * off
        s1o = s1 + n * off
        # 회전 0/90/180/270 가정 → axis-aligned AABB
        xs = [s0[0], s1[0], s0o[0], s1o[0]]
        ys = [s0[1], s1[1], s0o[1], s1o[1]]
        seg_min = np.array([min(xs), min(ys)])
        seg_max = np.array([max(xs), max(ys)])
        # 점이면 박스 폭이 0 일 수 있어 약간 부풀려야 함
        seg_min -= 0.5
        seg_max += 0.5

        hit = False
        for (cid, b_min, b_max, b_z) in others_aabb:
            if not _z_overlap((z_bot, z_top), b_z):
                continue
            if _box_intersect(seg_min, seg_max, b_min, b_max):
                contacting_cids.append(cid)
                hit = True
        if hit:
            interior_len += seg_len

    exterior_ratio = (face_len - interior_len) / face_len
    return FaceClass(
        exterior_ratio=max(0.0, min(1.0, exterior_ratio)),
        contacting_cids=list(dict.fromkeys(contacting_cids)),  # 중복 제거(순서 보존)
    )


# ── 공용: 다른 부재의 AABB 후보 목록 ──────────────────────
def _gather_obstacles(
    scene: Scene, exclude_ids: Iterable[int],
    include_floor_panels: bool,
) -> List[Tuple[int, np.ndarray, np.ndarray, Tuple[float, float]]]:
    """씬 안에서 인접 검사 대상이 되는 부재들의 AABB 목록.

    제외: Core, CoreSlab, MidBeam, MidColumn, CantileverBeam, CantileverSlab,
          InteriorWall(세대 내부 칸막이 — 외피 판별과 무관), 그리고 exclude_ids.
    옵션: FloorPanel 포함 여부 (사용자 결정 ⑥-3).
    """
    out = []
    excl = set(exclude_ids)
    for cid, comp in scene.components.items():
        if cid in excl:
            continue
        if isinstance(comp, (Core, CoreSlab)):
            continue
        if comp.comp_type in (ComponentType.MID_BEAM, ComponentType.MID_COLUMN,
                              ComponentType.CANTILEVER_BEAM, ComponentType.CANTILEVER_SLAB,
                              ComponentType.INTERIOR_WALL):
            continue
        if isinstance(comp, FloorPanel) and not include_floor_panels:
            continue
        mn_xy, mx_xy = _component_aabb_xy(comp)
        z_rng = _component_z_range(comp)
        out.append((cid, mn_xy, mx_xy, z_rng))
    return out


# ── 모듈 1 개 (단일 층) 분류 ─────────────────────────────
def classify_module_face(
    module: Component, face_name: str, scene: Scene,
    options: ClassifierOptions = ClassifierOptions(),
) -> FaceClass:
    """모듈의 한 면 → FaceClass."""
    z_bot, z_top = _component_z_range(module)
    p0, p1, n = _module_face_segment_xy(module, face_name, z_bot, z_top)
    obstacles = _gather_obstacles(scene, exclude_ids=[module.id],
                                   include_floor_panels=options.include_floor_panels)
    return _classify_face_xy(p0, p1, n, z_bot, z_top, obstacles, options)


def classify_module(
    module: Component, scene: Scene,
    options: ClassifierOptions = ClassifierOptions(),
) -> Dict[str, FaceClass]:
    """모듈 4 면 일괄 분류."""
    return {face: classify_module_face(module, face, scene, options)
            for face in FACE_NAMES}


# ── 수직 3 모듈 (층별 12 면) ──────────────────────────────
def classify_vertical3_module(
    v3: Vertical3Module, scene: Scene,
    options: ClassifierOptions = ClassifierOptions(),
) -> Dict[Tuple[int, str], FaceClass]:
    """수직 3 모듈을 층별로 12 면 분류 (사용자 결정 ⑥-4).

    Returns: {(floor_idx, face_name): FaceClass}  — floor_idx 0~2.
    """
    z0, z_top = _component_z_range(v3)
    n_floors = 3
    floor_h = FLOOR_HEIGHT  # 3420 (모듈 + 갭)

    out: Dict[Tuple[int, str], FaceClass] = {}
    # v3 의 4 변 XY 좌표는 모든 층에서 동일 (수직으로 곧음)
    obstacles = _gather_obstacles(scene, exclude_ids=[v3.id],
                                   include_floor_panels=options.include_floor_panels)
    for f_idx in range(n_floors):
        z_bot_f = z0 + f_idx * floor_h
        z_top_f = z_bot_f + floor_h
        for face_name in FACE_NAMES:
            p0, p1, n = _module_face_segment_xy(v3, face_name, z_bot_f, z_top_f)
            fc = _classify_face_xy(p0, p1, n, z_bot_f, z_top_f, obstacles, options)
            out[(f_idx, face_name)] = fc
    return out


# ── 독립 StructWall 양면 분류 (A-3) ───────────────────────
def classify_independent_wall(
    wall: StructWall, scene: Scene,
    options: ClassifierOptions = ClassifierOptions(),
) -> Dict[str, FaceClass]:
    """독립 벽 패널의 양면 분류.

    StructWall 은 두께 방향(d) 으로 두 면 — "표면", "이면".
    회전 0 기준: 표면=-Y(전면), 이면=+Y(후면).
    """
    z_bot, z_top = _component_z_range(wall)
    obstacles = _gather_obstacles(scene, exclude_ids=[wall.id],
                                   include_floor_panels=options.include_floor_panels)
    out: Dict[str, FaceClass] = {}
    for face in ("전면", "후면"):
        p0, p1, n = _module_face_segment_xy(wall, face, z_bot, z_top)
        out[face] = _classify_face_xy(p0, p1, n, z_bot, z_top, obstacles, options)
    return out


# ── 가중평균 단위중량 ─────────────────────────────────────
def face_unit_weight(
    face_class: FaceClass,
    interior_unit: float = 30.0,
    exterior_unit: float = 55.0,
) -> float:
    """가중평균 단위중량 (kg/m²) — 사용자 결정 ⑥-2.

    분류 라벨 없이 exterior_ratio 자체가 가중치.
    """
    r = face_class.exterior_ratio
    return r * exterior_unit + (1.0 - r) * interior_unit


__all__ = [
    "FACE_NAMES",
    "FaceClass", "ClassifierOptions",
    "classify_module_face", "classify_module",
    "classify_vertical3_module", "classify_independent_wall",
    "face_unit_weight",
]
