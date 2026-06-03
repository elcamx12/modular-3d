"""
다층 그룹 생성 헬퍼 — F5 배치 모드용.

부재 1개를 배치하면 N층 자동 복제하며, 같은 group_id를 부여한다.
floor_index 분포는 부재 타입별로 다르다 (설계서 4번 표 참조).

| 부재         | 개수    | z(층 인덱스 k)            |
|--------------|---------|----------------------------|
| 모듈         | N       | k=0..N-1, z=k·H            |
| 바닥패널     | N+1     | k=0..N,   z=k·H            |
| 벽패널(병합) | N       | k=0..N-1, z=k·H            |
| 벽패널(비병합)| N       | k=0..N-1, z=k·H + 200      |
| 캔틸레버보   | N       | k=0..N-1, z=k·H + (H-200)  |
| 캔틸레버슬래브(패널종속) | N+1 | k=0..N, z=k·H        |
| 캔틸레버슬래브(모듈종속) | N+1 | k=0..N-1, z=k·H + 옥상 z=N·H |
| 중간보       | N       | k=0..N-1, 모듈 천장/바닥에 따라 |
| 중간기둥     | N       | k=0..N-1, z=k·H            |

이 모듈은 종속 부재의 위치/회전/앵커 자동 계산은 하지 않는다.
종속 분기는 단계 4에서 ops_builder/floor_analyzer 측 부담.
"""
from typing import List, Tuple, Optional, Dict
import numpy as np

from modular_3d.model import (
    ComponentType, Component, Module, FloorPanel, StructWall,
    CantileverBeam, CantileverSlab, MidBeam, MidColumn,
    Vertical3Module, Scene,
)
from modular_3d.analysis.constants import (
    FLOOR_HEIGHT, SECTION_W_MM, VERTICAL_MODULE_FLOORS,
    MODULE_HEIGHT_MM, MODULE_JOINT_GAP_MM,
)


# ── 부재 타입별 z 분포 함수 ────────────────────────────────

def _floor_z_list(comp_type: ComponentType, n_floors: int,
                  merge_with_panel: bool = False,
                  cantilever_slab_on_module: bool = False,
                  interior_wall_plus_top: bool = False,
                  ) -> List[float]:
    """부재 타입과 종속 옵션에 따라 각 floor_index 별 z 좌표 리스트 반환.

    반환: [z_0, z_1, ...] — floor_index와 1:1 대응.
    H = FLOOR_HEIGHT, S = SECTION_W_MM.
    """
    H = float(FLOOR_HEIGHT)               # 층-층 거리 = h + gap = 3420
    S = float(SECTION_W_MM)                # 단면 폭 = 200
    h = float(MODULE_HEIGHT_MM)            # 단층 모듈 자체 높이 = 3400
    # [함정] 모듈 천장보 z 오프셋은 (h - S) 이지 (H - S) 가 아니다 — 갭(20mm)
    # 만큼 차이남. 기존 코드는 H = h 로 가정해서 일치했으나 H = h + gap 로
    # 명시화한 뒤 캔틸레버·중간보 z 가 20mm 위로 어긋나 부모 매칭이 실패함.
    H_TOP = h - S                          # 모듈 내 천장보 로컬 z = 3200

    if comp_type == ComponentType.MODULE:
        return [k * H for k in range(n_floors)]

    elif comp_type == ComponentType.VERTICAL_MODULE:
        # 수직 3층 모듈: 한 개체가 3개 층을 차지 → n_floors 는 3 의 배수.
        # 인스턴스 수 = n_floors / 3, 각 인스턴스 z = k · 3 · H.
        # [함정] 3 의 배수 검증은 호출자(controls.py / alignment_view.py) 책임.
        n_inst = n_floors // VERTICAL_MODULE_FLOORS
        return [k * VERTICAL_MODULE_FLOORS * H for k in range(n_inst)]

    elif comp_type == ComponentType.FLOOR_PANEL:
        # [2026-05-12 v8] 1층 바닥(z=0) 포함. 사용자 의도: 모듈 base 슬래브와 같은
        # 평면에 패널이 같이 깔린다. range(0, N+1) 로 0/1F/2F/.../옥상까지 N+1장.
        return [k * H for k in range(0, n_floors + 1)]

    elif comp_type == ComponentType.STRUCT_WALL:
        # 병합 ON: 패널과 같은 z (k=0..N-1)
        # 병합 OFF: 패널 위로 200 (k=0..N-1)
        offset = 0.0 if merge_with_panel else S
        return [k * H + offset for k in range(n_floors)]

    elif comp_type == ComponentType.INTERIOR_WALL:
        # 내벽: 부모 따라 복제. position.z = k·H (부모 base 와 동일 평면).
        # wall_fill 로컬 z(half_s..h-s-half_s)가 같은 층 모듈 벽과 flush.
        # 부모가 바닥패널/패널레벨이면 0..N(N+1장), 모듈/모듈종속이면 0..N-1(N장).
        if interior_wall_plus_top:
            return [k * H for k in range(n_floors + 1)]
        return [k * H for k in range(n_floors)]

    elif comp_type == ComponentType.CANTILEVER_BEAM:
        # 각 층 모듈 천장보 레벨 (N개). 1층 바닥(z=0)은 패널·슬래브와 마찬가지로
        # 지면 슬래브 처리 영역이라 별도 캔틸 보 불필요.
        return [k * H + H_TOP for k in range(n_floors)]

    elif comp_type == ComponentType.CANTILEVER_SLAB:
        if cantilever_slab_on_module:
            # [2026-05-11 v6] 모듈 종속 시 모듈의 **바닥 슬래브 레벨** = 모듈 base z (k·H).
            # 기존엔 모듈 천장보(k·H + H_TOP) 레벨로 잘못 매핑되어 슬래브가 모듈 천장에
            # 떠 있는 형태였음. 사용자 요청에 따라 모듈 슬래브와 같은 레벨로 변경.
            return [k * H for k in range(n_floors)]
        else:
            # 패널 종속: 패널과 동일 위치. v6 에서 패널이 1층(k=0) 부터 시작하므로 동일.
            return [k * H for k in range(0, n_floors + 1)]

    elif comp_type == ComponentType.MID_BEAM:
        # 중간보: 한 번 배치하면 각 층의 바닥(k·H)과 천장(k·H + H_TOP) 두 곳에
        # 자동 생성. 인덱스 짝수=바닥, 홀수=천장. mid_beam_level 은 호출자가
        # 인덱스 패리티에 따라 'bottom'/'top' 으로 덮어씀.
        zs: List[float] = []
        for k in range(n_floors):
            zs.append(k * H)             # 바닥 레벨
            zs.append(k * H + H_TOP)     # 천장 레벨
        return zs

    elif comp_type == ComponentType.MID_COLUMN:
        return [k * H for k in range(n_floors)]

    elif comp_type == ComponentType.CORE:
        # RC 코어벽: floor_index 0..N (총 N+1 장). z = k·H (각 층 base).
        # 옥상(k=N) 코어 윗단 = N·H + 3400 = 모듈 한 장 더 쌓은 높이 (Q46).
        return [k * H for k in range(n_floors + 1)]

    elif comp_type == ComponentType.CORE_SLAB:
        # RC 코어 슬래브: floor_index 0..N (총 N+1 장).
        # 슬래브 상면 = k·H + h (= 그 층 코어벽 윗면). position.z = 슬래브 하면 =
        # 상면 - 두께. 슬래브 두께는 인스턴스마다 다를 수 있으므로 본 z_list 는
        # **상면 기준** 으로 반환. 실제 position.z 보정은 _generate_core_slabs 측 책임.
        return [k * H + h for k in range(n_floors + 1)]

    return [0.0]


# ── Component 팩토리 ──────────────────────────────────────

def _instantiate(comp_type: ComponentType, position: np.ndarray,
                 dims: dict, rotation: int, anchor: int,
                 beam_section_type: str = 'shs') -> Component:
    """Component 서브클래스 생성 — model.core.instantiate 위임 (단계 2 통합)."""
    from modular_3d.model.core import instantiate
    return instantiate(comp_type, position, dims, rotation, anchor,
                       beam_section_type)


# ── 다층 그룹 ID 발급 ──────────────────────────────────────

def next_group_id(scene: Scene) -> int:
    """현재 Scene 안에서 가장 큰 group_id + 1 반환. 모두 0이면 1 반환."""
    max_gid = 0
    for comp in scene.components.values():
        gid = getattr(comp, 'group_id', 0)
        if gid > max_gid:
            max_gid = gid
    return max_gid + 1


# ── 다층 그룹 생성 메인 ────────────────────────────────────

def create_multi_floor_group(
    scene: Scene,
    comp_type: ComponentType,
    base_position: np.ndarray,   # (3,) 1층 부재 위치 (z는 함수가 무시)
    dims: dict,
    rotation: int,
    anchor: int,
    n_floors: int,
    *,
    merge_with_panel: bool = False,
    cantilever_slab_on_module: bool = False,
    interior_wall_plus_top: bool = False,
    parent_group_id: int = 0,
    anchor_edge_id: int = -1,
    mid_beam_level: Optional[str] = None,
    beam_section_type: str = 'shs',
) -> Tuple[int, List[int]]:
    """다층 그룹을 생성하고 (group_id, [comp_id_list]) 반환.

    [CoT] 처리 순서:
    1. group_id 발급 (parent_group_id가 있으면 거기에 합류, 없으면 새 발급).
    2. 부재 타입별 z 분포 계산.
    3. 같은 평면 위치 (x, y)로 각 층 부재 생성 → group_id/floor_index/sub_index 부여.
    4. Scene에 add_component → comp_id 반환.

    sub_index는 본체일 때 0. 종속 부재는 호출자가 별도 sub_index 지정 필요.
    """
    # 1. group_id 결정
    if parent_group_id > 0:
        gid = parent_group_id
        is_dependent = True
    else:
        gid = next_group_id(scene)
        is_dependent = False

    # 2. z 분포 결정
    z_list = _floor_z_list(
        comp_type, n_floors,
        merge_with_panel=merge_with_panel,
        cantilever_slab_on_module=cantilever_slab_on_module,
        interior_wall_plus_top=interior_wall_plus_top,
    )

    # 3·4. 각 층 부재 생성
    base_pos = np.array(base_position, dtype=np.float64)
    created_ids: List[int] = []

    # sub_index: 종속이면 다음 가용 값을 호출자가 줄 것이므로 여기서는
    # parent_group_id 유무로 0(본체) / 호출자 책임으로 분기.
    # 단계 3a: 본체 부재만 다루므로 sub_index=0.
    sub_idx = 0 if not is_dependent else _next_sub_index(scene, gid)

    # 벽패널 비합체: pos.z 를 +SECTION_W_MM 만큼 올리고(이미 _floor_z_list 에서
    # 적용됨) 추가로 height 를 -SECTION_W_MM 줄여 천장 높이를 유지.
    # 의도: 벽 하부 런너가 FP 보 상면에 정확히 안착, 벽 상부 런너가 모듈 천장보
    # 라인에 맞도록 (둘 다 단면 ±100mm 이 같은 면에서 만남, 겹침 X).
    # 합체 케이스: 벽과 FP 가 같은 z 평면에서 완전 겹침 → 토폴로지 통합 처리.
    eff_dims = dict(dims)
    if (comp_type == ComponentType.STRUCT_WALL
            and not merge_with_panel
            and 'height' in eff_dims):
        eff_dims['height'] = max(0.0, float(eff_dims['height']) - float(SECTION_W_MM))

    # 종속 부재일 때: 같은 그룹 + 같은 floor_index 의 본체 부재(sub_index=0) id 를
    # 미리 인덱싱해두면, 각 층 종속 부재의 parent_id 를 그 본체 id 로 박을 수 있다.
    # 이로써 UI 가 인식한 부모-자식 관계가 컴포넌트에 명시적으로 저장됨.
    parent_id_by_floor: Dict[int, int] = {}
    parent_is_vmodule = False
    if is_dependent:
        for cid_existing, comp_existing in scene.components.items():
            if (getattr(comp_existing, 'group_id', 0) == gid
                    and getattr(comp_existing, 'sub_index', 0) == 0):
                fi = getattr(comp_existing, 'floor_index', 0)
                parent_id_by_floor[fi] = cid_existing
                if comp_existing.comp_type == ComponentType.VERTICAL_MODULE:
                    parent_is_vmodule = True

    for floor_idx, z in enumerate(z_list):
        pos = base_pos.copy()
        pos[2] = z
        comp = _instantiate(comp_type, pos, eff_dims, rotation, anchor,
                            beam_section_type)
        # 그룹 메타데이터 부여
        comp.group_id = gid
        comp.sub_index = sub_idx
        comp.anchor_edge_id = anchor_edge_id
        # 중간보는 한 클릭당 (바닥, 천장) 2개씩 → 같은 floor 번호 + level 분기.
        if comp_type == ComponentType.MID_BEAM:
            comp.floor_index = floor_idx // 2
            comp.mid_beam_level = 'bottom' if (floor_idx % 2 == 0) else 'top'
        else:
            comp.floor_index = floor_idx
            comp.mid_beam_level = mid_beam_level
        # 벽패널 병합 플래그
        if comp_type == ComponentType.STRUCT_WALL:
            comp.merge_with_panel = bool(merge_with_panel)
        # 종속 부재면 같은 층 본체의 comp_id 를 parent_id 로 박는다.
        # [함정] 수직 3층 모듈 부모: 본체는 floor_index 0·1…(인스턴스 단위)뿐인데
        # 종속 내벽은 실제 층마다 1장이라 floor_index 0..N-1 이다. 내벽 floor_index 를
        # VERTICAL_MODULE_FLOORS(3)으로 나눠 그 내벽이 속한 수직모듈 인스턴스 번호로
        # 환산해 부모를 찾는다(0·1·2층→인스턴스0, 3·4·5층→인스턴스1).
        if is_dependent:
            if parent_is_vmodule:
                vm_inst = comp.floor_index // VERTICAL_MODULE_FLOORS
                comp.parent_id = parent_id_by_floor.get(vm_inst, 0)
            else:
                comp.parent_id = parent_id_by_floor.get(comp.floor_index, 0)

        comp_id = scene.add_component(comp)
        created_ids.append(comp_id)

    # [정책 2026-05-12] 코어 슬래브 자동 생성 폐지 — 사용자가 좌측 팔레트의
    # "코어 슬래브 생성/재생성" 버튼을 눌러야만 슬래브가 만들어진다.
    # 호출자가 필요한 시점에 직접 regenerate_core_slabs / regenerate_all_core_slabs
    # 를 호출해야 한다.

    return gid, created_ids


def _next_sub_index(scene: Scene, group_id: int) -> int:
    """같은 group_id 안에서 사용된 가장 큰 sub_index + 1 반환."""
    max_sub = 0
    for comp in scene.components.values():
        if getattr(comp, 'group_id', 0) == group_id:
            si = getattr(comp, 'sub_index', 0)
            if si > max_sub:
                max_sub = si
    return max_sub + 1


# ── 코어 슬래브 자동 생성 / 갱신 ────────────────────────────

def _compute_core_slab_bbox_xy(
    scene: Scene, group_id: int, floor_index: int,
) -> Optional[Tuple[float, float, float, float]]:
    """그룹의 그 층 모든 코어벽들의 월드 xy bbox 반환.

    Returns:
        (min_x, min_y, max_x, max_y) — 코어벽이 1 장도 없으면 None.
    """
    xs: List[float] = []
    ys: List[float] = []
    for comp in scene.components.values():
        if (getattr(comp, 'group_id', 0) != group_id
                or getattr(comp, 'floor_index', 0) != floor_index
                or comp.comp_type != ComponentType.CORE):
            continue
        corners = comp.get_world_corners()  # (8, 3) 바닥 4 + 천장 4
        # xy 평면 bbox 만 필요 — 모든 코너 사용해도 z 무관.
        xs.extend(corners[:, 0].tolist())
        ys.extend(corners[:, 1].tolist())
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def regenerate_core_slabs(
    scene: Scene, group_id: int, n_floors: int,
    slab_thickness: float = None,
) -> List[int]:
    """그룹의 모든 층(0..n_floors) 코어 슬래브를 재계산 / 갱신.

    [절차]
    1. 같은 group_id 의 기존 CORE_SLAB 컴포넌트를 모두 제거 (Scene 직접 pop).
    2. 각 floor_index k=0..N 에 대해 그 층 코어벽들의 xy bbox 계산.
    3. bbox 가 있는 층에만 슬래브 1 개 생성 (회전 0, 앵커 0=좌하).
       슬래브 상면 z = k·H + MODULE_HEIGHT_MM. position.z = 상면 - thickness.

    Returns:
        새로 생성된 CoreSlab comp_id 리스트.
    """
    from modular_3d.model.core import instantiate
    from modular_3d.카탈로그.geometry import CORE_SLAB_DEFAULT_THICKNESS_MM
    if slab_thickness is None:
        slab_thickness = float(CORE_SLAB_DEFAULT_THICKNESS_MM)

    H = float(FLOOR_HEIGHT)
    h = float(MODULE_HEIGHT_MM)

    # 1. 기존 코어 슬래브 제거 (Scene.remove_component 는 undo 영향 X — 직접 pop)
    to_remove = [
        cid for cid, c in list(scene.components.items())
        if (getattr(c, 'group_id', 0) == group_id
            and c.comp_type == ComponentType.CORE_SLAB)
    ]
    for cid in to_remove:
        scene.components.pop(cid, None)

    # 2~3. 각 층 슬래브 생성
    created: List[int] = []
    for k in range(n_floors + 1):
        bbox = _compute_core_slab_bbox_xy(scene, group_id, k)
        if bbox is None:
            continue
        min_x, min_y, max_x, max_y = bbox
        w_slab = max_x - min_x
        d_slab = max_y - min_y
        if w_slab <= 0.0 or d_slab <= 0.0:
            continue

        # 같은 그룹·같은 층 코어벽 두께를 슬래브 dimensions 에 박음 (해석 측에서
        # 슬래브 외곽 격자를 t_wall/2 안쪽으로 후퇴시켜 코어벽 상단 노드와 자동
        # 노드 병합되도록 함 — 사용자 답변 F (a), 같은 그룹 두께 동일 가정).
        wall_t_in_group = 300.0  # 기본값
        for c in scene.components.values():
            if (getattr(c, 'group_id', 0) == group_id
                    and getattr(c, 'floor_index', 0) == k
                    and c.comp_type == ComponentType.CORE):
                wall_t_in_group = float(c.dimensions.get('depth', 300.0))
                break

        # 슬래브 상면 = k·H + h, 하면(=position.z) = 상면 - 두께.
        z_bot = k * H + h - float(slab_thickness)
        slab_pos = np.array([min_x, min_y, z_bot], dtype=np.float64)
        slab_dims = {
            'width': float(w_slab),
            'depth': float(d_slab),
            'thickness': float(slab_thickness),
            'height': float(slab_thickness),  # 일부 코드가 height 만 참조하는 경우 대비
            'wall_thickness': float(wall_t_in_group),
        }
        slab = instantiate(ComponentType.CORE_SLAB, slab_pos, slab_dims,
                           rotation=0, anchor=0)
        slab.group_id = group_id
        slab.floor_index = k
        slab.sub_index = _next_sub_index(scene, group_id)
        cid = scene.add_component(slab)
        # add_component 가 undo_stack 에 'place' 를 push 하지만, 슬래브는
        # 코어벽의 부속물이라 사용자 시점 undo 단위가 아님 — pop 해서 제거.
        # 안전망: 직전 항목이 '바로 이 슬래브의 place' 인지 component_id 일치 검사.
        if (scene.undo_stack
                and scene.undo_stack[-1].action_type == 'place'
                and scene.undo_stack[-1].data.get('component_id') == cid):
            scene.undo_stack.pop()
        created.append(cid)

    # [2026-05-17] 1층 바닥 코어슬래브 추가 — 기존 z_list 는 k·H+h(=각 층
    # 천장=다음 층 바닥) 만 포함해 1층 바닥(z≈0) 위치 슬래브가 빠짐.
    # 다른 1층 부재(FloorPanel z=0, 모듈 바닥보 z=0) 와 같은 z 레벨에
    # 노드가 오도록 pos.z = -slab_thickness 로 두면, 슬래브 상면 z = 0.
    # floor_index=0 의 기존 천장 슬래브와 구별을 위해 sub_index 새로 부여.
    bbox_floor1 = _compute_core_slab_bbox_xy(scene, group_id, 0)
    if bbox_floor1 is not None:
        min_x, min_y, max_x, max_y = bbox_floor1
        w_slab = max_x - min_x
        d_slab = max_y - min_y
        if w_slab > 0.0 and d_slab > 0.0:
            wall_t_in_group = 300.0
            for c in scene.components.values():
                if (getattr(c, 'group_id', 0) == group_id
                        and getattr(c, 'floor_index', 0) == 0
                        and c.comp_type == ComponentType.CORE):
                    wall_t_in_group = float(c.dimensions.get('depth', 300.0))
                    break
            z_bot = -float(slab_thickness)
            slab_pos = np.array([min_x, min_y, z_bot], dtype=np.float64)
            slab_dims = {
                'width': float(w_slab),
                'depth': float(d_slab),
                'thickness': float(slab_thickness),
                'height': float(slab_thickness),
                'wall_thickness': float(wall_t_in_group),
            }
            slab = instantiate(ComponentType.CORE_SLAB, slab_pos, slab_dims,
                               rotation=0, anchor=0)
            slab.group_id = group_id
            slab.floor_index = 0
            slab.sub_index = _next_sub_index(scene, group_id)
            cid = scene.add_component(slab)
            if (scene.undo_stack
                    and scene.undo_stack[-1].action_type == 'place'
                    and scene.undo_stack[-1].data.get('component_id') == cid):
                scene.undo_stack.pop()
            created.append(cid)
    return created


# ── 그룹 일괄 삭제 ────────────────────────────────────────

def delete_group(scene: Scene, group_id: int) -> List[int]:
    """그룹 통째로 삭제. 삭제된 comp_id 리스트 반환."""
    target_ids = [
        cid for cid, comp in scene.components.items()
        if getattr(comp, 'group_id', 0) == group_id
    ]
    for cid in target_ids:
        scene.components.pop(cid, None)
    return target_ids


# ── 그룹 일괄 이동 ────────────────────────────────────────

def translate_components(scene: Scene, comp_ids: List[int],
                          delta: np.ndarray) -> List[int]:
    """주어진 comp_id 들만 (dx, dy, dz) 이동. 영향받은 comp_id 리스트 반환.

    `translate_group` 의 일반화 — 정책에 따라 미리 결정된 부재 집합을 이동.
    """
    affected: List[int] = []
    for cid in comp_ids:
        comp = scene.components.get(cid)
        if comp is None:
            continue
        comp.position = comp.position + np.array(delta, dtype=np.float64)
        comp.generate_sub_components()
        affected.append(cid)
    return affected


def translate_group(scene: Scene, group_id: int, delta: np.ndarray) -> List[int]:
    """그룹 통째로 (dx, dy, dz) 이동. 영향받은 comp_id 리스트 반환.

    [정책 2026-05-12] 코어 그룹 이동 시에도 슬래브 자동 재생성 안 함.
    슬래브가 코어벽과 함께 그대로 평행이동만 됨 (이미 같은 group_id 로 묶여
    있으므로 위 루프에서 position 갱신됨). 코어벽 치수/배치가 바뀌어 슬래브
    bbox 를 다시 계산해야 한다면 사용자가 좌측 팔레트의 "코어 슬래브 생성/
    재생성" 버튼을 눌러야 한다.
    """
    affected: List[int] = []
    for cid, comp in scene.components.items():
        if getattr(comp, 'group_id', 0) == group_id:
            comp.position = comp.position + np.array(delta, dtype=np.float64)
            comp.generate_sub_components()
            affected.append(cid)
    return affected
