"""
Scene 저장/불러오기 — JSON 라운드트립 + 구버전 마이그레이션 통합 (단계 3 단순화).

[좌표 단위 정책]
JSON 안 position / dimensions / 모든 좌표 = **mm (밀리미터)**.
m 단위 옛 JSON 은 지원하지 않음 — 외부에서 m 단위 데이터를 받으면 호출자가
`* 1000` 으로 변환 후 load_scene 에 전달해야 함.

[구성]
- save_scene / load_scene: 직렬화 + 역직렬화 + 옛 → 새 ID 재배선
- snap_n_floors_to_three: 층수 3 배수 스냅
- _auto_match_wall_to_fp / _auto_estimate_parent_id: 구버전 scene 호환성 추정
- 부재 클래스 dispatch: `model.core.TYPE_TO_CLASS` 단일 진실 원천

[이력]
- 2026-05-08: 별도 scene_migration.py 모듈을 본 파일로 다시 합침 (분리 효과 미미했음).
"""
import json
from typing import Tuple, Dict
import numpy as np

from modular_3d.model import (
    Scene, ComponentType, TYPE_TO_CLASS, StructWall, FloorPanel,
)
from modular_3d._utils.debug import dprint


# 구버전 호환성 추정 임계값 — 토폴로지·ops_builder 의 fuzzy 매칭과 별개.
_WALL_FP_AUTO_MATCH_MAX_XY_MM = 500.0


# ─── 층수 스냅 ──────────────────────────────────────────────

def snap_n_floors_to_three(n: int) -> int:
    """층수를 가장 가까운 3 의 배수로 스냅 (동률은 내림). 최소 3.

    수직 3층 모듈 도입(2026-05) 후 모든 디자인은 3 배수 층수로 통일.
    """
    if n < 3:
        return 3
    lower = (n // 3) * 3
    upper = lower + 3
    return lower if (n - lower) <= (upper - n) else upper


# ─── 직렬화 ─────────────────────────────────────────────────

def save_scene(scene: Scene, n_floors: int, path: str) -> int:
    """Scene 의 모든 Component + 층수를 JSON 으로 저장.
    반환: 저장된 부재 수.
    """
    data = {
        'version': 1,
        'n_floors': int(n_floors),
        'components': [],
    }
    for cid, comp in scene.components.items():
        data['components'].append({
            'id': int(cid),
            'comp_type': comp.comp_type.value,
            'position': [float(comp.position[0]), float(comp.position[1]),
                         float(comp.position[2])],
            'rotation': int(comp.rotation),
            'dimensions': {k: float(v) for k, v in comp.dimensions.items()},
            'anchor': int(comp.anchor),
            'group_id': int(getattr(comp, 'group_id', 0)),
            'floor_index': int(getattr(comp, 'floor_index', 0)),
            'sub_index': int(getattr(comp, 'sub_index', 0)),
            'anchor_edge_id': int(getattr(comp, 'anchor_edge_id', -1)),
            'mid_beam_level': getattr(comp, 'mid_beam_level', None),
            'merge_with_panel': bool(getattr(comp, 'merge_with_panel', False)),
            'merged_fp_id': getattr(comp, 'merged_fp_id', None),
            'merged_wall_ids': list(getattr(comp, 'merged_wall_ids', []) or []),
            'parent_id': int(getattr(comp, 'parent_id', 0)),
            'joint_records': list(getattr(comp, 'joint_records', []) or []),
        })
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return len(data['components'])


def load_scene(path: str) -> Tuple[Scene, int]:
    """JSON 파일을 읽어 (Scene, n_floors) 반환.

    절차:
    1. JSON 역직렬화 + 부재 인스턴스화
    2. 옛 → 새 ID 매핑으로 합체·부모 reference 재배선
    3. 구버전 호환성 추정 (멱등)
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    scene = Scene()
    # 스키마 버전 검사 — 현재 version=1. 호환성 미보장 버전은 경고만 출력.
    file_ver = int(data.get('version', 1))
    if file_ver != 1:
        dprint('scene_io', f'[scene_io] 경고: 알 수 없는 스키마 버전 {file_ver} (지원 버전: 1). 호환성 미보장.')
    raw_n = int(data.get('n_floors', 1))
    n_floors = snap_n_floors_to_three(raw_n)
    if n_floors != raw_n:
        dprint('scene_io', f'[scene_io] 마이그레이션: 층수 {raw_n} → {n_floors} (3 의 배수 스냅)')
    id_map: dict = {}  # 옛 id → 새 id (Scene.add_component 가 새 id 부여)
    for d in data.get('components', []):
        ct_str = d['comp_type']
        try:
            ct = ComponentType(ct_str)
        except ValueError:
            dprint('scene_io', f'[scene_io] 알 수 없는 부재 타입: {ct_str} — 건너뜀')
            continue
        cls = TYPE_TO_CLASS.get(ct)
        if cls is None:
            dprint('scene_io', f'[scene_io] 알 수 없는 부재 타입: {ct_str} — 건너뜀')
            continue
        comp = cls(
            id=0,
            comp_type=ct,
            position=np.array(d['position'], dtype=np.float64),
            rotation=int(d.get('rotation', 0)),
            dimensions=dict(d.get('dimensions', {})),
            anchor=int(d.get('anchor', 0)),
        )
        comp.group_id = int(d.get('group_id', 0))
        comp.floor_index = int(d.get('floor_index', 0))
        comp.sub_index = int(d.get('sub_index', 0))
        comp.anchor_edge_id = int(d.get('anchor_edge_id', -1))
        comp.mid_beam_level = d.get('mid_beam_level', None)
        comp.merge_with_panel = bool(d.get('merge_with_panel', False))
        comp.parent_id = int(d.get('parent_id', 0))
        comp.joint_records = list(d.get('joint_records', []) or [])
        if hasattr(comp, 'merged_fp_id'):
            mfid = d.get('merged_fp_id', None)
            comp.merged_fp_id = int(mfid) if mfid is not None else None
        if hasattr(comp, 'merged_wall_ids'):
            comp.merged_wall_ids = list(d.get('merged_wall_ids', []) or [])
        comp.generate_sub_components()
        old_id = int(d.get('id', 0))
        new_id = scene.add_component(comp)
        if old_id > 0:
            id_map[old_id] = new_id

    # 옛 → 새 ID 매핑으로 합체 / parent reference 재배선.
    # 매칭 실패 시 silent fallback 대신 경고 출력 — 손상된 JSON 식별 용이.
    n_missing = 0
    for comp in scene.components.values():
        if hasattr(comp, 'merged_fp_id') and comp.merged_fp_id is not None:
            old = int(comp.merged_fp_id)
            new = id_map.get(old)
            if new is None:
                n_missing += 1
                dprint('scene_io', f'[scene_io] WARN: comp#{comp.id} 의 merged_fp_id={old} 매칭 실패 → None')
            comp.merged_fp_id = new
        if hasattr(comp, 'merged_wall_ids'):
            kept = []
            for w in comp.merged_wall_ids:
                if w in id_map:
                    kept.append(id_map[w])
                else:
                    n_missing += 1
                    dprint('scene_io', f'[scene_io] WARN: comp#{comp.id} 의 merged_wall_ids 항목 {w} 매칭 실패 → 제거')
            comp.merged_wall_ids = kept
        if getattr(comp, 'parent_id', 0) > 0:
            old = comp.parent_id
            new = id_map.get(old, 0)
            if new == 0:
                n_missing += 1
                dprint('scene_io', f'[scene_io] WARN: comp#{comp.id} 의 parent_id={old} 매칭 실패 → 0')
            comp.parent_id = new
    if n_missing > 0:
        dprint('scene_io', f'[scene_io] 총 {n_missing} 개 ID 재배선 실패 (구버전 JSON 또는 손상)')

    # 구버전 호환성 추정 (멱등 — 새 형식 scene 은 통과만)
    _auto_match_wall_to_fp(scene)
    _auto_estimate_parent_id(scene)

    # 옵션 (나) — UI 측 조인트 기록을 항상 재계산 (구버전 씬·재배선 후 일관성 확보).
    # 직렬화된 기록이 있어도 부재 id 가 재배정되었으므로 다시 만든다.
    try:
        from modular_3d.model.joint_recorder import record_joints
        n_rec = record_joints(scene)
        dprint('scene_io', f'[scene_io] 조인트 기록 재계산: {n_rec} 코너')
    except Exception as e:
        dprint('scene_io', f'[scene_io] 조인트 기록 재계산 실패: {e}')

    return scene, n_floors


# ─── 구버전 호환성 추정 ─────────────────────────────────────

def _auto_match_wall_to_fp(scene: Scene) -> int:
    """구버전 씬: wall.merge_with_panel=True 인데 merged_fp_id 가 비어 있는 경우.

    같은 floor_index + 평면(xy) 거리가 가장 가까운 FP 를 자동 매칭.
    Returns: 자동 매칭된 wall 수.
    """
    matched = 0
    for cid, comp in list(scene.components.items()):
        if not (isinstance(comp, StructWall)
                and getattr(comp, 'merge_with_panel', False)
                and comp.merged_fp_id is None):
            continue
        comp_floor = getattr(comp, 'floor_index', 0)
        wxy = np.array(comp.position[:2], dtype=np.float64)
        best_id = None
        best_d = float('inf')
        for fcid, fcomp in scene.components.items():
            if not isinstance(fcomp, FloorPanel):
                continue
            if getattr(fcomp, 'floor_index', 0) != comp_floor:
                continue
            fxy = np.array(fcomp.position[:2], dtype=np.float64)
            d = float(np.linalg.norm(wxy - fxy))
            if d < best_d:
                best_d = d
                best_id = fcid
        if best_id is not None and best_d < _WALL_FP_AUTO_MATCH_MAX_XY_MM:
            scene.merge_wall_to_fp(cid, best_id)
            if scene.undo_stack and scene.undo_stack[-1].action_type == 'merge':
                scene.undo_stack.pop()
            dprint('scene_io', f'[scene_io] 자동 합체: wall #{cid} ↔ FP #{best_id} (xy 거리 {best_d:.0f}mm)')
            matched += 1
    return matched


def _auto_estimate_parent_id(scene: Scene) -> int:
    """구버전 씬: parent_id 가 비어 있는 종속 부재의 부모를 추정.

    같은 group_id + 같은 floor_index 안에서 본체(sub_index=0) 부재를 부모로 채움.
    Returns: 채워진 종속 부재 수.
    """
    body_by_group_floor: Dict = {}
    for cid_b, comp_b in scene.components.items():
        if (getattr(comp_b, 'sub_index', 0) == 0
                and getattr(comp_b, 'group_id', 0) > 0):
            key = (comp_b.group_id, comp_b.floor_index)
            body_by_group_floor[key] = cid_b
    fixed = 0
    for cid_d, comp_d in scene.components.items():
        if getattr(comp_d, 'parent_id', 0) > 0:
            continue
        if getattr(comp_d, 'sub_index', 0) <= 0:
            continue
        key = (getattr(comp_d, 'group_id', 0),
               getattr(comp_d, 'floor_index', 0))
        body = body_by_group_floor.get(key)
        if body is not None and body != cid_d:
            comp_d.parent_id = body
            fixed += 1
    if fixed > 0:
        dprint('scene_io', f'[scene_io] 자동 부모 추정: {fixed} 종속 부재 parent_id 채움')
    return fixed
