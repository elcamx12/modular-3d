"""controls.py 의 F5 (도크 배치 모드) 관련 메소드 분리.

[설계]
- F5Mixin 은 Controller 가 inherit 한다 (Mixin 패턴).
- Controller 의 모든 self.* 상태 (self._scene, self._viewer, self._snap 등) 를 그대로 사용.
- 본 모듈 자체는 인스턴스화하지 않는다.

[원칙]
- 본 파일에는 F5 도크 패널 관련 로직만 둔다.
- 다른 메소드를 추가할 때는 self.* 결합도가 너무 높지 않은지 검토.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from modular_3d.model import ComponentType, TYPE_NAMES
from modular_3d.ui.controls_geom import create_component as _create_component
from modular_3d.render.mesh_builder import build_ghost_component_mesh

# [버그 수정 2026-05-08] TYPE_NAMES 가 model.core 로 이동 후 직접 import 가능.
# 옛 주석 "순환 import 회피" 는 model.core 통합 (단계 2) 으로 더 이상 안 적용.


class F5Mixin:
    """F5 배치 모드 메소드 모음 — Controller 가 inherit."""

    def _resolve_move_targets(self, comp_id: int) -> List[int]:
        """선택된 부재 ID 로부터 이동/회전 대상 ID 리스트 결정 (정책 b 단계, 멀티층 의식).

        [정책 2026-05-12 v2]
        - 종속 부재 (sub_index > 0):
            같은 group_id + 같은 sub_index 의 모든 부재 = 모든 층의 같은 종속 줄.
            (예: 1F 캔틸레버보 이동 → 2F·3F·옥상 같은 위치의 캔틸레버보 모두 따라감.)
        - 코어 (CORE / CORE_SLAB, sub=0):
            같은 group_id 의 모든 코어 부재 = ㄷ·ㅁ 코어 묶음 + 코어 슬래브 통째.
        - 모듈/패널/벽 본체 (그 외, sub=0):
            같은 group_id 전체 (본체 모든 층 + 종속 모든 층).
        """
        comp = self._scene.components.get(comp_id)
        if comp is None:
            return []
        gid = getattr(comp, 'group_id', 0)
        sub = getattr(comp, 'sub_index', 0)
        ctype = comp.comp_type

        # 코어벽 (CORE) — 같은 group_id + 같은 xy 의 모든 층 (한 줄만).
        # 다른 줄(다른 xy) 의 코어벽이나 코어 슬래브는 영향 없음.
        if ctype == ComponentType.CORE:
            if gid <= 0:
                return [comp_id]
            cx, cy = float(comp.position[0]), float(comp.position[1])
            return [cid for cid, c in self._scene.components.items()
                    if getattr(c, 'group_id', 0) == gid
                    and c.comp_type == ComponentType.CORE
                    and abs(float(c.position[0]) - cx) < 1.0
                    and abs(float(c.position[1]) - cy) < 1.0]

        # 코어 슬래브 (CORE_SLAB) — 같은 group_id 의 모든 코어 부재 (벽 모든 줄
        # + 슬래브 모든 층). 슬래브가 _next_sub_index 로 양수 sub_index 를 받아
        # 종속처럼 보여도 본 분기로 잡힘.
        if ctype == ComponentType.CORE_SLAB:
            if gid <= 0:
                return [comp_id]
            return [cid for cid, c in self._scene.components.items()
                    if getattr(c, 'group_id', 0) == gid
                    and c.comp_type in (ComponentType.CORE,
                                        ComponentType.CORE_SLAB)]

        # 종속 부재 — 같은 group_id + 같은 sub_index (모든 층)
        if sub > 0:
            if gid <= 0:
                return [comp_id]
            return [cid for cid, c in self._scene.components.items()
                    if getattr(c, 'group_id', 0) == gid
                    and getattr(c, 'sub_index', 0) == sub]

        # 모듈/패널/벽/V3M 본체 — 같은 그룹 전체
        # [2026-05-16 버그 수정] 합체(merge) 그룹 추가 — FP 와 합체된 벽패널은
        # 별도 group_id 라 같은 그룹 검색에서 누락된다. FP 면 merged_wall_ids
        # 의 벽들의 그룹도, 벽패널이면 merged_fp_id 의 FP 그룹 + 그 FP 의 다른
        # 합체 벽 그룹들도 모두 따라가야 한다.
        if gid > 0:
            gids: set = {gid}

            def _collect_merge_gids(c0):
                """c0 의 합체 관계를 따라 group_id 들을 gids 에 누적."""
                from modular_3d.model import FloorPanel, StructWall
                if isinstance(c0, FloorPanel):
                    for wid in getattr(c0, 'merged_wall_ids', []) or []:
                        wc = self._scene.components.get(wid)
                        if wc is None:
                            continue
                        wgid = int(getattr(wc, 'group_id', 0))
                        if wgid > 0:
                            gids.add(wgid)
                elif isinstance(c0, StructWall):
                    fp_id = getattr(c0, 'merged_fp_id', None)
                    if fp_id is not None:
                        fp = self._scene.components.get(fp_id)
                        if fp is not None:
                            fgid = int(getattr(fp, 'group_id', 0))
                            if fgid > 0:
                                gids.add(fgid)
                            for wid in getattr(fp, 'merged_wall_ids', []) or []:
                                wc = self._scene.components.get(wid)
                                if wc is None:
                                    continue
                                wgid = int(getattr(wc, 'group_id', 0))
                                if wgid > 0:
                                    gids.add(wgid)

            _collect_merge_gids(comp)
            return [cid for cid, c in self._scene.components.items()
                    if int(getattr(c, 'group_id', 0)) in gids]
        return [comp_id]

    def _refresh_2d(self):
        """부재 상태가 바뀐 직후 2D 캔버스 즉시 갱신.
        호출 누락 시 Z·배치·삭제 등에서 사용자 마우스 클릭 전까지 표시가 stale.
        """
        if (hasattr(self, '_f5_panel') and self._f5_panel is not None
                and hasattr(self._f5_panel, 'canvas')):
            self._f5_panel.canvas.update()

    def set_f5_dock(self, dock, panel):
        """main_3d 가 만든 F5 도킹 패널을 컨트롤러에 등록."""
        self._f5_dock = dock
        self._f5_panel = panel
        # 층수 변경 시그널 연결 — 단계 3d에서 다층 그룹 재생성 핸들러를 채움
        if hasattr(panel, 'floors_changed'):
            panel.floors_changed.connect(self._on_floors_changed)
        # 저장/불러오기 시그널
        if hasattr(panel, 'save_requested'):
            panel.save_requested.connect(self._f5_save_scene)
        if hasattr(panel, 'load_requested'):
            panel.load_requested.connect(self._f5_load_scene)
        # 캔버스에 컨트롤러 참조 + 1~7 키 콜백 연결
        if hasattr(panel, 'canvas'):
            panel.canvas.set_placement_callback(
                on_type_key=self._f5_on_type_key,
                on_canvas_click=self._f5_on_canvas_click,
                on_escape=self._f5_on_escape,
                on_rotate=self._f5_on_rotate,
                on_anchor=self._f5_on_anchor,
                on_canvas_move=self._f5_on_canvas_move,
                on_delete=self._f5_on_delete,
                on_dependency_pick=self._f5_on_dependency_pick,
                on_move_key=self._f5_on_move_key,
                on_undo=self._f5_on_undo,
                on_copy=self._f5_on_copy,
                on_room_complete=self._f5_on_room_complete,
                on_room_delete=self._delete_room,
                on_room_commit=self._room_commit,
                on_room_ghost=self._room_ghost,
                on_room_ghost_clear=self._room_ghost_clear,
                on_opening_add_start=self._opening_add_start,
                on_opening_commit=self._opening_commit,
                on_opening_ghost=self._opening_ghost,
                on_opening_ghost_clear=self._opening_ghost_clear,
                on_opening_delete=self._delete_opening,
                on_import_ghost=self._f5_on_import_ghost,
                on_import_commit=self._f5_on_import_commit,
                on_import_cancel=self._f5_on_import_cancel,
            )

    # ── F5 저장/불러오기 ──────────────────────────────────────

    def _f5_save_scene(self):
        """F5 패널 '저장' 버튼 → Scene + 층수를 JSON 파일로 저장."""
        from PyQt5.QtWidgets import QFileDialog
        from modular_3d.io.scene_io import save_scene
        path, _ = QFileDialog.getSaveFileName(
            self._f5_panel, '배치 저장', 'scene.json', 'JSON (*.json)')
        if not path:
            return
        n_floors = self._f5_panel.floors if self._f5_panel else 1
        try:
            n = save_scene(self._scene, n_floors, path)
            print(f'[SAVE] {n}개 부재 저장 → {path}')
            self._dim_panel.set_mode_text(f'[저장됨: {n}개]')
        except Exception as e:
            print(f'[SAVE] 실패: {e}')

    def _f5_load_scene(self):
        """F5 패널 '불러오기' 버튼 → JSON 파일 로드 후 Scene 재구성."""
        from PyQt5.QtWidgets import QFileDialog
        from modular_3d.io.scene_io import load_scene
        from modular_3d.render.mesh_builder import build_component_mesh
        path, _ = QFileDialog.getOpenFileName(
            self._f5_panel, '배치 불러오기', '', 'JSON (*.json)')
        if not path:
            return
        try:
            new_scene, n_floors = load_scene(path)
        except Exception as e:
            print(f'[LOAD] 실패: {e}')
            return

        # 기존 Scene/viewer/snap 모두 비우기 (실 3D 색면 포함)
        self._clear_rooms_3d()
        for cid in list(self._scene.components.keys()):
            self._viewer.remove_component_visual(cid)
            self._snap.remove_component(cid)
        self._scene.components.clear()
        self._scene.undo_stack.clear()
        self._scene.rooms.clear()

        # 새 Scene 의 부재들을 메인 Scene 에 등록 (id 재발급은 add_component 가 처리)
        for old_id, comp in new_scene.components.items():
            new_id = self._scene.add_component(comp)
            v, f, c = build_component_mesh(comp)
            self._viewer.add_component_visual(new_id, v, f, c)
            self._snap.add_component(new_id, comp)

        # 실(Room) 재등록 + 3D 색면 렌더
        for room in getattr(new_scene, 'rooms', {}).values():
            self._scene.add_room(room)
        self._render_all_rooms_3d()

        # 접합 오버라이드 복사 — 부재 ID 재배선과 무관(평면 좌표 식별).
        # 저장본이 있으면 접합 탭 진입 시 "저장본/새 계산" 1회 질문.
        self._scene.joint_overrides = list(
            getattr(new_scene, 'joint_overrides', []) or [])
        self._scene._joint_overrides_pending_choice = bool(
            self._scene.joint_overrides)

        # (2026-05-12) 불러오기로 들어간 add_component 의 'place' 액션들을 모두
        # 제거 — 사용자가 Z 키로 불러온 부재를 하나씩 지우는 일을 막음.
        # 불러오기 = 사용자 직전 액션이 아니므로 undo 대상에서 제외하는 게 정상.
        self._scene.undo_stack.clear()

        # 층수 spin 값 동기화 (시그널 안 발생시킴)
        if self._f5_panel is not None:
            self._f5_panel.set_floors(n_floors)

        # 2D 화면 즉시 갱신
        if self._f5_panel is not None and hasattr(self._f5_panel, 'canvas'):
            self._f5_panel.canvas.update()

        self._status.update_count(self._scene.component_count)
        print(f'[LOAD] {len(new_scene.components)}개 부재 ← {path} (N={n_floors})')
        self._dim_panel.set_mode_text(
            f'[불러옴: {len(new_scene.components)}개, N={n_floors}]')

    # ── 코어 슬래브 수동 생성/재생성 ───────────────────────────

    def regenerate_all_core_slabs(self, silent: bool = False):
        """좌측 팔레트의 '코어 슬래브 생성/재생성' 버튼 핸들러.

        [절차]
        1. Scene 의 모든 코어벽 group_id 수집.
        2. 각 그룹에 대해 그룹 내 코어벽 floor_index 최댓값을 N 으로 추정.
        3. 그룹 단위로 regenerate_core_slabs 호출 — 기존 슬래브 제거 + 재생성.
        4. viewer/snap 등록 갱신. Scene 에 사라진 옛 슬래브 visual 청소.
        """
        from modular_3d.model.multi_floor import regenerate_core_slabs
        from modular_3d.render.mesh_builder import build_component_mesh

        # 1. 코어벽 그룹별 (floor_index 최댓값, 슬래브 두께) 수집
        gid_max_floor: dict[int, int] = {}
        gid_slab_thk: dict[int, float] = {}
        for comp in self._scene.components.values():
            if comp.comp_type != ComponentType.CORE:
                continue
            gid = getattr(comp, 'group_id', 0)
            if gid <= 0:
                continue
            fi = int(getattr(comp, 'floor_index', 0))
            if fi > gid_max_floor.get(gid, -1):
                gid_max_floor[gid] = fi
            # 슬래브 두께: 첫 코어벽의 dimensions['slab_thickness']
            # (없으면 카탈로그 기본 CORE_SLAB_DEFAULT_THICKNESS_MM)
            if gid not in gid_slab_thk:
                from modular_3d.카탈로그.geometry import (
                    CORE_SLAB_DEFAULT_THICKNESS_MM as _CST_DEF,
                )
                gid_slab_thk[gid] = float(
                    comp.dimensions.get('slab_thickness', _CST_DEF))

        if not gid_max_floor:
            if not silent:
                print('[코어 슬래브] 씬에 코어벽이 없어 생성할 슬래브가 없습니다.')
                self._dim_panel.set_mode_text('[코어 슬래브: 코어벽 없음]')
            return

        # 2. 옛 슬래브 visual 청소를 위해 현재 슬래브 ID 미리 기록
        old_slab_ids = [
            cid for cid, c in self._scene.components.items()
            if c.comp_type == ComponentType.CORE_SLAB
        ]

        # 3. 그룹별로 재생성
        new_total = 0
        for gid, max_floor in gid_max_floor.items():
            slab_thk = gid_slab_thk.get(gid, 180.0)
            new_ids = regenerate_core_slabs(
                self._scene, gid, max_floor, slab_thickness=slab_thk)
            new_total += len(new_ids)

        # 4. 옛 슬래브 visual/snap 청소 (Scene 에서 pop 되어 더 이상 없음)
        for cid_old in old_slab_ids:
            if cid_old not in self._scene.components:
                self._viewer.remove_component_visual(cid_old)
                self._snap.remove_component(cid_old)

        # 5. 새 슬래브 visual/snap 등록
        for cid_s, comp_s in self._scene.components.items():
            if comp_s.comp_type != ComponentType.CORE_SLAB:
                continue
            if cid_s in self._viewer._component_visuals:
                continue
            v, f, cl = build_component_mesh(comp_s)
            self._viewer.add_component_visual(cid_s, v, f, cl)
            self._snap.add_component(cid_s, comp_s)

        self._status.update_count(self._scene.component_count)
        if not silent:
            print(f'[코어 슬래브] 그룹 {len(gid_max_floor)}개, 총 {new_total}장 재생성')
            self._dim_panel.set_mode_text(
                f'[코어 슬래브: 그룹 {len(gid_max_floor)}개 / {new_total}장]')
            self._refresh_2d()

    def _core_signature(self):
        """코어벽 구성 시그니처 — 변경 감지용.

        코어벽(슬래브 제외)의 그룹·위치·층·회전·앵커·치수를 모은 정렬 튜플.
        직전 시그니처와 비교해 *코어벽이 실제로 달라졌는지*만 가린다.
        """
        sig = []
        for c in self._scene.components.values():
            if c.comp_type != ComponentType.CORE:
                continue
            dims = tuple(sorted(
                (str(k), round(float(v), 1))
                for k, v in (c.dimensions or {}).items()
                if isinstance(v, (int, float))))
            sig.append((
                int(getattr(c, 'group_id', 0) or 0),
                round(float(c.position[0]), 1),
                round(float(c.position[1]), 1),
                round(float(c.position[2]), 1),
                int(getattr(c, 'floor_index', 0)),
                int(getattr(c, 'rotation', 0)),
                int(getattr(c, 'anchor', 0)),
                dims,
            ))
        return tuple(sorted(sig))

    def _auto_regen_core_slabs(self):
        """코어벽 *구성이 직전과 달라졌을 때만* 코어 슬래브를 조용히 재생성.

        코어벽 관련 동작(배치/이동/복사/회전/삭제/층변경)에서만 시그니처가
        바뀌므로, 코어와 무관한 조작(모듈 이동 등)에서는 아무 일도 하지 않는다.
        """
        sig = self._core_signature()
        if sig == getattr(self, '_last_core_sig', None):
            return
        self._last_core_sig = sig
        self.regenerate_all_core_slabs(silent=True)

    # ── 층수 변경 ───────────────────────────────────────────

    def _on_floors_changed(self, n: int):
        """층수 입력창 변경 콜백 (단계 3d + 종속 부재 보존 패치).

        [CoT] 처리 순서:
        1. 1층(floor_index=0) 본체(sub_index=0) + 종속(sub_index>0) 메타정보 분리 수집.
        2. Scene/viewer/snap 모두 비우기 + undo 스택 비움(Z2).
        3. 본체 먼저 N층 재생성 → 새 group_id 매핑(원본 gid → 새 gid).
        4. 종속 부재도 N(+1)층 재생성 — parent_group_id 매핑으로 부모 그룹 합류.
        """
        if getattr(self, 'single_floor_mode', False):
            # 정의 탭: 층수 복제 비활성(단일층 고정) — 다층 재생성하지 않음.
            return
        if n < 1 or n > 25:
            print(f'[F5] 층수 {n} 범위 초과 — 무시')
            return

        from modular_3d.model.multi_floor import create_multi_floor_group
        from modular_3d.render.mesh_builder import build_component_mesh

        # 1. 1층 메타정보 수집 — 본체와 종속 분리
        body_seeds = []  # sub_index == 0
        dep_seeds = []   # sub_index >  0
        # 종속 부재의 부모 comp_type 추적 (캔틸 슬래브 모듈/패널 종속 분기용)
        gid_to_body_type = {}
        for cid, comp in list(self._scene.components.items()):
            if getattr(comp, 'floor_index', 0) != 0:
                continue
            # 중간보는 같은 floor_index=0 안에 바닥+천장 2개가 있으므로 시드는
            # 'bottom' 만 채택 (재생성 시 multi_floor 가 다시 두 레벨 생성).
            if (comp.comp_type == ComponentType.MID_BEAM
                    and getattr(comp, 'mid_beam_level', None) != 'bottom'):
                continue
            # 구조벽: 합체된 FP의 그룹 ID 도 같이 기록 → 재생성 후 같은 FP
            # 그룹의 같은 floor FP 와 다시 merge_wall_to_fp 호출.
            merged_fp_id = getattr(comp, 'merged_fp_id', None)
            merged_fp_group = 0
            if merged_fp_id is not None:
                fp_comp = self._scene.components.get(merged_fp_id)
                if fp_comp is not None:
                    merged_fp_group = getattr(fp_comp, 'group_id', 0)

            info = {
                'comp_type': comp.comp_type,
                'position': comp.position.copy(),
                'dimensions': dict(comp.dimensions),
                'rotation': comp.rotation,
                'anchor': comp.anchor,
                'group_id': getattr(comp, 'group_id', 0),
                'sub_index': getattr(comp, 'sub_index', 0),
                'anchor_edge_id': getattr(comp, 'anchor_edge_id', -1),
                'mid_beam_level': getattr(comp, 'mid_beam_level', None),
                'merge_with_panel': getattr(comp, 'merge_with_panel', False),
                'merged_fp_group': merged_fp_group,  # 0 이면 합체 없음
            }
            if info['sub_index'] == 0:
                body_seeds.append(info)
                gid_to_body_type[info['group_id']] = info['comp_type']
            elif comp.comp_type == ComponentType.CORE_SLAB:
                # [정책 2026-05-12] 코어 슬래브는 자동 재생성하지 않는다.
                # 층수 변경 시 슬래브는 사라지며, 사용자가 좌측 팔레트의 "코어
                # 슬래브 생성/재생성" 버튼을 눌러 다시 만들어야 한다.
                continue
            else:
                dep_seeds.append(info)

        # 2. Scene/viewer/snap 모두 비우기
        all_ids = list(self._scene.components.keys())
        for cid in all_ids:
            self._viewer.remove_component_visual(cid)
            self._snap.remove_component(cid)
        self._scene.components.clear()
        self._scene.undo_stack.clear()

        # 3. 본체 N층 재생성 + group_id 매핑(원본 → 새)
        gid_map = {}
        # (구조벽 본체 시드 → 합체할 FP 의 새 그룹 ID) 보관 — FP 재생성 후 매칭.
        wall_seeds_to_merge: List[tuple] = []
        # 코어 그룹 통합: 모든 CORE 본체 시드를 같은 group_id 에 합류.
        # 첫 번째 CORE 시드만 새 그룹 발급, 나머지는 그 그룹에 추가.
        unified_core_gid = 0
        for seed in body_seeds:
            base_pos = seed['position'].copy()
            base_pos[2] = 0.0
            # CORE 그룹 통합 분기 — 두 번째 이후 CORE 시드는 같은 그룹 합류.
            if (seed['comp_type'] == ComponentType.CORE
                    and unified_core_gid > 0):
                # [정책 2026-05-12] 코어 슬래브 자동 재생성 폐지 — 코어벽만 N+1
                # 층 재생성하고 슬래브는 만들지 않는다. 사용자가 버튼으로 생성.
                from modular_3d.model.multi_floor import _floor_z_list
                from modular_3d.model.core import instantiate as _inst
                z_list = _floor_z_list(ComponentType.CORE, n_floors=n)
                ids = []
                for k, z in enumerate(z_list):
                    pos = base_pos.copy()
                    pos[2] = z
                    c_new = _inst(ComponentType.CORE, pos,
                                  seed['dimensions'],
                                  seed['rotation'], seed['anchor'])
                    c_new.group_id = unified_core_gid
                    c_new.floor_index = k
                    c_new.sub_index = 0
                    new_id = self._scene.add_component(c_new)
                    ids.append(new_id)
                new_gid = unified_core_gid
            else:
                new_gid, ids = create_multi_floor_group(
                    self._scene, seed['comp_type'], base_pos,
                    seed['dimensions'], seed['rotation'], seed['anchor'],
                    n_floors=n,
                    merge_with_panel=seed['merge_with_panel'],
                )
                if seed['comp_type'] == ComponentType.CORE:
                    unified_core_gid = new_gid
            gid_map[seed['group_id']] = new_gid
            for cid in ids:
                comp = self._scene.components[cid]
                v, f, c = build_component_mesh(comp)
                self._viewer.add_component_visual(cid, v, f, c)
                self._snap.add_component(cid, comp)
            # [정책 2026-05-12] 코어 슬래브 자동 생성 폐지 — 더 이상 슬래브가
            # 동반 생성되지 않으므로 viewer/snap 청소·등록 분기 불필요.
            # 구조벽 합체 정보 기록 (FP 본체가 모두 만들어진 뒤 일괄 처리)
            if (seed['comp_type'] == ComponentType.STRUCT_WALL
                    and seed.get('merged_fp_group', 0) > 0):
                wall_seeds_to_merge.append((ids, seed['merged_fp_group']))

        # 3-b. 구조벽 ↔ FP 합체 복원 — 같은 floor FP 찾아 merge_wall_to_fp 호출.
        for wall_ids, old_fp_gid in wall_seeds_to_merge:
            new_fp_gid = gid_map.get(old_fp_gid)
            if new_fp_gid is None:
                continue
            for wid in wall_ids:
                wcomp = self._scene.components.get(wid)
                if wcomp is None:
                    continue
                wfloor = getattr(wcomp, 'floor_index', 0)
                fp_target = None
                for fcid, fcomp in self._scene.components.items():
                    if (getattr(fcomp, 'group_id', 0) == new_fp_gid
                            and fcomp.sub_index == 0
                            and fcomp.floor_index == wfloor):
                        fp_target = fcid
                        break
                if fp_target is not None:
                    self._scene.merge_wall_to_fp(wid, fp_target)
                    # 층 변경 처리는 undo 스택 비운 상태이므로 merge 가 push 한
                    # 액션도 같이 비워 둔다.
                    if (self._scene.undo_stack
                            and self._scene.undo_stack[-1].action_type == 'merge'):
                        self._scene.undo_stack.pop()

        # 4. 종속 부재 재생성 — 부모 그룹의 새 gid 사용
        for seed in dep_seeds:
            parent_new_gid = gid_map.get(seed['group_id'])
            if parent_new_gid is None:
                # 부모 본체 사라짐 → 종속도 폐기
                continue
            parent_type = gid_to_body_type.get(seed['group_id'])
            cant_slab_on_module = (
                seed['comp_type'] == ComponentType.CANTILEVER_SLAB
                and parent_type == ComponentType.MODULE
            )
            base_pos = seed['position'].copy()
            base_pos[2] = 0.0
            _gid, ids = create_multi_floor_group(
                self._scene, seed['comp_type'], base_pos,
                seed['dimensions'], seed['rotation'], seed['anchor'],
                n_floors=n,
                parent_group_id=parent_new_gid,
                anchor_edge_id=seed['anchor_edge_id'],
                mid_beam_level=seed['mid_beam_level'],
                cantilever_slab_on_module=cant_slab_on_module,
            )
            for cid in ids:
                comp = self._scene.components[cid]
                v, f, c = build_component_mesh(comp)
                self._viewer.add_component_visual(cid, v, f, c)
                self._snap.add_component(cid, comp)

        self._status.update_count(self._scene.component_count)
        print(f'[F5 FLOORS] 본체 {len(body_seeds)}개 + 종속 {len(dep_seeds)}개 '
              f'→ N={n}층 재생성 완료')
        self._auto_regen_core_slabs()
        self._refresh_2d()

        # 옵션 (나) — 층수 변경으로 모든 부재가 재생성됐으므로 조인트 기록 재계산.
        try:
            from modular_3d.model.joint_recorder import record_joints
            record_joints(self._scene)
        except Exception:
            pass

    # ── F5 배치 흐름 (단계 3b) ─────────────────────────────────
    # 흐름: 1·2·3 키 → DIMENSION_INPUT(dim_panel 활성) → Enter
    #      → PLACEMENT_PREVIEW(F5 캔버스 고스트) → 마우스 클릭
    #      → 다층 그룹 생성 + 좌측 3D 갱신.
    # 4·5·6·7 종속 부재는 단계 4에서 DEPENDENCY_PICK 추가.

    def _f5_on_type_key(self, comp_type: ComponentType):
        """F5 캔버스에서 1~7 키 입력 → 배치 시작."""
        # [2026-05-24] 배치 탭 정의 라이브러리 인터셉트(버튼·1~9키 공용 funnel).
        # main 이 배치 컨트롤러에만 _type_select_intercept 를 주입한다. 저장된
        # 정의가 있으면 '새로/가져오기' 프롬프트를 띄우고, 가져오기로 처리되면
        # (=True) 기존 새로 만들기 흐름을 생략한다. 정의 탭 컨트롤러엔 미주입.
        _hook = getattr(self, '_type_select_intercept', None)
        if _hook is not None:
            try:
                if _hook(comp_type):
                    return
            except Exception as _e:
                print(f'[TYPE INTERCEPT] {_e}')
        # 종속 부재(4·5·6·7)는 단계 4에서 처리 — 임시로 일반 부재처럼 직진
        self._current_comp_type = comp_type
        self._preview_rotation = 0
        self._preview_anchor = 0
        # 새 PREVIEW 시작 — 사용자 R/V 잠금 해제
        self._f5_user_rv_override = False
        # F5 활성 표시(_on_dimension_confirmed에서 분기에 사용)
        self._f5_pending_placement = True
        # 사이즈 입력 패널 활성
        defaults = self._current_defaults()
        self._dim_panel.activate(comp_type, defaults)
        name = TYPE_NAMES.get(comp_type, '')
        self._dim_panel.set_mode_text(f'[F5 배치: {name} — 사이즈 입력]')

    def _f5_on_canvas_click(self, world_x: float, world_y: float):
        """F5 캔버스에서 PREVIEW 상태일 때 마우스 클릭 → 부재 생성 확정."""
        canvas = self._f5_panel.canvas if self._f5_panel else None
        if canvas is None:
            return
        if not getattr(canvas, '_f5_in_preview', False):
            return
        # 저장된 dims/comp_type/rotation/anchor 사용
        dims = getattr(self, '_f5_pending_dims', None)
        if dims is None:
            return
        comp_type = self._current_comp_type
        rotation = self._preview_rotation
        anchor = self._preview_anchor

        from modular_3d.model.multi_floor import create_multi_floor_group
        from modular_3d.render.mesh_builder import build_component_mesh
        import numpy as np

        # [정의 탭 단일층] 단일층 모드면 항상 3층 분량으로 생성(수직3층모듈이
        # 정확히 1개 만들어지는 최소 배수) 후, 배치 끝에서 floor_index>0 을 모두
        # 제거해 1층 1세트만 남긴다. 일반 모드는 패널 층수 그대로.
        if getattr(self, 'single_floor_mode', False):
            n_floors = 3
        else:
            n_floors = self._f5_panel.floors if self._f5_panel else 1
        merge = bool(dims.get('merge', False))
        base_pos = np.array([world_x, world_y, 0.0], dtype=np.float64)

        # [2026-05-16 정책 통합] C 키 복사 분기 제거 — _f5_on_copy 가 진입 시점에
        # deep copy 를 같은 위치에 박고 M 키 흐름으로 진입하도록 통합. 본 클릭
        # 시점엔 M 분기가 그대로 복사본을 새 위치로 이동시킴.

        # ── 자유 이동 분기 (M 키): _resolve_move_targets 결과 부재들만 이동 ──
        # (정책 b 단계 2026-05-12) 코어벽=자기만, 코어슬래브=슬래브+코어벽,
        # 모듈/패널 본체=같은 그룹 전체(본체+종속), 종속=자기만.
        target_ids = list(getattr(self, '_f5_move_target_ids', []) or [])
        move_gid = getattr(self, '_f5_move_group_id', 0)
        if target_ids and move_gid > 0:
            from modular_3d.model.multi_floor import translate_components
            from modular_3d.render.mesh_builder import build_component_mesh
            seed_id = getattr(self, '_f5_move_seed_id', target_ids[0])
            seed = self._scene.components.get(seed_id)
            if seed is None:
                self._f5_move_group_id = 0
                self._f5_move_target_ids = []
                return
            old_xy = (float(seed.position[0]), float(seed.position[1]))
            delta = np.array([world_x - old_xy[0], world_y - old_xy[1], 0.0])
            # 회전·앵커는 seed 부재(=사용자가 클릭한 본인) 에만 적용. 다른
            # 부재(같은 그룹 종속 / 코어벽)는 위치만 같이 이동, 회전·앵커 유지.
            new_rot = self._preview_rotation
            new_anc = self._preview_anchor

            # (그룹 undo) 이동 전 대상 부재들의 원래 상태 기록
            old_positions: dict = {}
            old_rotations: dict = {}
            old_anchors: dict = {}
            for cid_g in target_ids:
                c_g = self._scene.components.get(cid_g)
                if c_g is None:
                    continue
                old_positions[cid_g] = c_g.position.copy()
                old_rotations[cid_g] = int(c_g.rotation)
                old_anchors[cid_g] = int(c_g.anchor)

            # [2026-05-16 버그 수정 v3] 회전 + 앵커 변환을 종속/합체 부재 위치에
            # 모두 적용. 회전 델타는 본체 새 위치를 축으로 종속 위치 회전, 앵커
            # 델타는 본체의 시각적 이동량(앵커 오프셋 차이 + 새 rotation 적용)
            # 만큼 종속 위치 평행이동. R 만 누른 케이스·V 만 누른 케이스·둘 다
            # 누른 케이스 모두 자연 처리.
            #
            # 앵커 시각 이동량 계산:
            #   _anchor_offset 은 anchor 별 (ax, ay) 반환. generate_sub_components
            #   는 to_world(lx-ax, ly-ay) 로 sub 를 그리므로, 같은 본체.position
            #   에서 anchor 가 (old→new) 으로 바뀌면 sub 가 local 좌표계에서
            #   (ax_old - ax_new) 만큼 +x/+y 방향 이동. world 좌표로는 new_rot
            #   회전 적용. 종속도 그만큼 world 이동.
            import math
            seed_old_rot = int(getattr(seed, 'rotation', 0))
            seed_old_anc = int(getattr(seed, 'anchor', 0))
            delta_rot_deg = ((int(new_rot) - seed_old_rot) % 360 + 360) % 360
            if delta_rot_deg >= 180:
                delta_rot_deg -= 360
            cos_r = math.cos(math.radians(delta_rot_deg))
            sin_r = math.sin(math.radians(delta_rot_deg))

            # 앵커 변경에 따른 본체 시각적 이동량 (world 좌표). seed dimensions
            # 가 width/depth 둘 다 있을 때만 의미 — 그 외(예: 중간기둥 같은
            # 정사각/0 폭) 는 자동으로 0 이 되어 무영향.
            anc_dx_world = 0.0
            anc_dy_world = 0.0
            if int(new_anc) != seed_old_anc:
                w = float(seed.dimensions.get('width', 0.0) or 0.0)
                d = float(seed.dimensions.get('depth', 0.0) or 0.0)
                tmp_anc = seed.anchor
                seed.anchor = seed_old_anc
                ax_old, ay_old = seed._anchor_offset(w, d)
                seed.anchor = int(new_anc)
                ax_new, ay_new = seed._anchor_offset(w, d)
                seed.anchor = tmp_anc   # 본체 처리 분기에서 정식으로 다시 설정
                dax_local = ax_old - ax_new
                day_local = ay_old - ay_new
                rad_w = math.radians(int(new_rot))
                cos_w = math.cos(rad_w)
                sin_w = math.sin(rad_w)
                anc_dx_world = dax_local * cos_w - day_local * sin_w
                anc_dy_world = dax_local * sin_w + day_local * cos_w

            affected = translate_components(self._scene, target_ids, delta)
            seed_gid = int(getattr(seed, 'group_id', 0))
            seed_sub = int(getattr(seed, 'sub_index', 0))
            seed_new_x = float(seed.position[0])
            seed_new_y = float(seed.position[1])
            for cid in affected:
                c = self._scene.components.get(cid)
                if c is None:
                    self._viewer.remove_component_visual(cid)
                    self._snap.remove_component(cid)
                    continue
                c_gid = int(getattr(c, 'group_id', 0))
                c_sub = int(getattr(c, 'sub_index', 0))
                if c_gid == seed_gid and c_sub == seed_sub:
                    # 본체 — 절대 회전·앵커 적용 (position 은 translate_components 가 처리)
                    c.rotation = int(new_rot)
                    c.anchor = int(new_anc)
                else:
                    # 종속/합체 — 회전 변환 + 앵커 변환 둘 다 적용.
                    if cid != seed_id:
                        if delta_rot_deg != 0:
                            rel_x = float(c.position[0]) - seed_new_x
                            rel_y = float(c.position[1]) - seed_new_y
                            new_rel_x = rel_x * cos_r - rel_y * sin_r
                            new_rel_y = rel_x * sin_r + rel_y * cos_r
                            c.position[0] = seed_new_x + new_rel_x
                            c.position[1] = seed_new_y + new_rel_y
                            c.rotation = (int(c.rotation) + int(delta_rot_deg)) % 360
                        if anc_dx_world != 0.0 or anc_dy_world != 0.0:
                            c.position[0] += anc_dx_world
                            c.position[1] += anc_dy_world
                c.generate_sub_components()
                self._viewer.remove_component_visual(cid)
                v, f, cl = build_component_mesh(c)
                self._viewer.add_component_visual(cid, v, f, cl)
                self._snap.remove_component(cid)
                self._snap.add_component(cid, c)

            self._status.update_count(self._scene.component_count)
            print(f'[F5 이동] gid={move_gid} 위치 {old_xy} → '
                  f'({world_x:.0f},{world_y:.0f}) rot={new_rot} anc={new_anc}')

            # (2026-05-12 그룹 undo) group_move 액션 push — 사용자 Z 한 번에
            # 그룹 통째 원위치 복원.
            from modular_3d.model.core import Action as _Action
            self._scene.undo_stack.append(_Action(
                action_type='group_move',
                data={'old_positions': old_positions,
                      'old_rotations': old_rotations,
                      'old_anchors': old_anchors},
            ))

            # 옵션 (나) — 이동 후 조인트 기록 재계산.
            try:
                from modular_3d.model.joint_recorder import record_joints
                record_joints(self._scene)
            except Exception:
                pass

            # 미리보기 종료 → IDLE
            canvas._f5_in_preview = False
            self._viewer.set_ghost_enabled(False)
            self._viewer.clear_ghost()
            self._dim_panel.set_mode_text('[IDLE]')
            self._f5_pending_dims = None
            self._f5_dep_meta = None
            self._f5_move_group_id = 0
            self._f5_move_target_ids = []
            self._f5_move_seed_id = 0
            self._f5_user_rv_override = False
            # 코어벽을 M(이동)/C(복사 후 이동)로 옮겼으면 슬래브 자동 재생성
            # (코어벽 구성이 실제로 바뀐 경우에만).
            self._auto_regen_core_slabs()
            canvas.update()
            return

        # (2026-05-12 그룹 undo) 본 클릭으로 발생하는 모든 add_component / merge
        # push 를 단일 'group_place' 액션으로 묶음.
        self._scene.begin_group_action()

        # 단계 4: 종속 메타데이터 분기
        dep_meta = getattr(self, '_f5_dep_meta', None)
        parent_gid = 0
        anchor_edge_id = -1
        mid_beam_level = None
        cantilever_slab_on_module = False
        interior_wall_plus_top = False
        merge_target_fp_group = 0
        if dep_meta is not None:
            anchor_edge_id = dep_meta['anchor_edge_id']
            mid_beam_level = dep_meta['mid_beam_level']
            if comp_type == ComponentType.CANTILEVER_SLAB:
                cantilever_slab_on_module = (
                    dep_meta['parent_comp_type'] == ComponentType.MODULE)
            if comp_type == ComponentType.INTERIOR_WALL:
                # 부모가 바닥패널이면 0..N(N+1장), 모듈/캔틸레버슬래브면 0..N-1(N장).
                interior_wall_plus_top = (
                    dep_meta['parent_comp_type'] == ComponentType.FLOOR_PANEL)

        # 보 단면 타입 — 직접배치는 치수패널 선택값, 종속은 부모를 따라간다.
        beam_section_type = getattr(self, '_f5_pending_beam_section', 'shs')
        if dep_meta is not None and dep_meta.get('parent_id'):
            _pc = self._scene.components.get(dep_meta['parent_id'])
            if _pc is not None:
                beam_section_type = getattr(_pc, 'beam_section_type', 'shs')
            # 구조벽 합체 모드는 자기 그룹을 가져야 하므로 parent_group_id 는
            # 0 으로 둔다(=새 그룹 발급). 합체 정보만 별도로 보관.
            if comp_type == ComponentType.STRUCT_WALL:
                merge_target_fp_group = dep_meta.get('merge_target_fp_group', 0)
            else:
                parent_gid = dep_meta['parent_group_id']

        # CORE 그룹 통합 — 같은 씬에 이미 코어벽이 있으면 그 group_id 에 합류.
        # 모든 코어벽을 한 그룹으로 묶어 코어 슬래브가 ㄷ·ㅁ 외곽을 한꺼번에
        # 덮는 단일 직사각형으로 자동 생성되도록 한다.
        if (comp_type == ComponentType.CORE and parent_gid == 0):
            existing_core_gid = 0
            for c in self._scene.components.values():
                if c.comp_type == ComponentType.CORE:
                    existing_core_gid = getattr(c, 'group_id', 0)
                    if existing_core_gid > 0:
                        break
            if existing_core_gid > 0:
                # 기존 코어 그룹에 합류 — multi_floor 거치지 않고 직접 add.
                # [정책 2026-05-12] 슬래브 자동 재생성 폐지. 사용자가 버튼으로
                # 슬래브를 만든다.
                from modular_3d.model.multi_floor import _floor_z_list
                from modular_3d.model.core import instantiate as _inst
                z_list = _floor_z_list(ComponentType.CORE, n_floors=n_floors)
                ids = []
                for k, z in enumerate(z_list):
                    pos = base_pos.copy()
                    pos[2] = z
                    c_new = _inst(ComponentType.CORE, pos, dims,
                                  rotation, anchor)
                    c_new.group_id = existing_core_gid
                    c_new.floor_index = k
                    c_new.sub_index = 0   # 코어벽은 같은 그룹 안에서도 본체
                    new_id = self._scene.add_component(c_new)
                    ids.append(new_id)
                gid = existing_core_gid
            else:
                gid, ids = create_multi_floor_group(
                    self._scene, comp_type, base_pos, dims, rotation, anchor,
                    n_floors=n_floors,
                    merge_with_panel=merge,
                    parent_group_id=parent_gid,
                    anchor_edge_id=anchor_edge_id,
                    mid_beam_level=mid_beam_level,
                    cantilever_slab_on_module=cantilever_slab_on_module,
                )
        else:
            # 다층 그룹 생성 (일반 흐름)
            gid, ids = create_multi_floor_group(
                self._scene, comp_type, base_pos, dims, rotation, anchor,
                n_floors=n_floors,
                merge_with_panel=merge,
                parent_group_id=parent_gid,
                anchor_edge_id=anchor_edge_id,
                mid_beam_level=mid_beam_level,
                cantilever_slab_on_module=cantilever_slab_on_module,
                interior_wall_plus_top=interior_wall_plus_top,
                beam_section_type=beam_section_type,
            )

        # 구조벽 합체 처리: 사용자가 선택한 FP 그룹에서 같은 floor 의 FP 를 찾아
        # merge_wall_to_fp 호출.
        if comp_type == ComponentType.STRUCT_WALL and merge_target_fp_group > 0:
            for wall_id in ids:
                wall_comp = self._scene.components.get(wall_id)
                if wall_comp is None:
                    continue
                wall_floor = getattr(wall_comp, 'floor_index', 0)
                # 같은 FP 그룹 + 같은 floor_index 의 FP 찾기
                fp_target = None
                for fcid, fcomp in self._scene.components.items():
                    if (getattr(fcomp, 'group_id', 0) == merge_target_fp_group
                            and getattr(fcomp, 'sub_index', 0) == 0
                            and getattr(fcomp, 'floor_index', -1) == wall_floor):
                        fp_target = fcid
                        break
                if fp_target is not None:
                    ok = self._scene.merge_wall_to_fp(wall_id, fp_target)
                    print(f'[F5 WALL MERGE] wall #{wall_id} ↔ FP #{fp_target} '
                          f'(floor={wall_floor}) {"OK" if ok else "FAIL"}')

        # 좌측 3D 동기화
        for cid in ids:
            comp = self._scene.components[cid]
            v, f, c = build_component_mesh(comp)
            self._viewer.add_component_visual(cid, v, f, c)
            self._snap.add_component(cid, comp)
            # [DEBUG 2026-05-13] 모듈 부재 좌표 분석 — 사용자 보고 "기둥 안 보임/
            # 천장보 위치 어긋남" 핀포인트용. mesh 의 z 범위 와 부재별 좌표 출력.
            if comp.comp_type.value == 'module':
                cols = getattr(comp, 'columns', []) or []
                tb = getattr(comp, 'top_beams', []) or []
                bb = getattr(comp, 'bottom_beams', []) or []
                print(f'[DBG] #{cid} fi={comp.floor_index} pos.z={comp.position[2]:.0f}')
                print(f'      mesh z range: {v[:,2].min():.0f} ~ {v[:,2].max():.0f}')
                print(f'      columns({len(cols)}): '
                      f'{[(int(c.base[2]), int(c.top[2])) for c in cols]}')
                print(f'      top_beams({len(tb)}): z='
                      f'{[int(b.start[2]) for b in tb]}')
                print(f'      bottom_beams({len(bb)}): z='
                      f'{[int(b.start[2]) for b in bb]}')

        # (2026-05-12) 코어 슬래브 자동 동반 생성 폐지 — 사용자가 별도 버튼으로
        # 생성. 본 분기는 더 이상 필요 없음.

        # 합체 구조벽 등 종속 부재가 부모/패널 보 단면 타입을 따라가도록 동기화.
        self._sync_dependent_beam_sections()

        # [정의 탭 단일층] floor_index>0 부재 제거 — 정의는 1층 1세트만 유지.
        if getattr(self, 'single_floor_mode', False):
            self._collapse_to_single_floor()

        self._status.update_count(self._scene.component_count)
        if dep_meta is not None:
            sub_idx = self._scene.components[ids[0]].sub_index
            print(f'[F5 종속 배치] {comp_type.value} parent_gid={parent_gid} '
                  f'sub={sub_idx} edge={anchor_edge_id} '
                  f'level={mid_beam_level} count={len(ids)}')
        else:
            print(f'[F5 배치] {comp_type.value} group={gid}, ids={ids}, '
                  f'N={n_floors}')

        # 옵션 (나) — 배치 직후 UI 측 조인트 기록 재계산.
        # FP·CS 각 코너가 어떤 부재의 어느 기둥 단에 결합되는지를 명시 기록.
        try:
            from modular_3d.model.joint_recorder import record_joints
            n_rec = record_joints(self._scene)
            print(f'[F5 JOINT REC] {n_rec} 코너 기록 갱신')
        except Exception as e:
            print(f'[F5 JOINT REC] 실패: {e}')

        # (2026-05-12 그룹 undo) 본 클릭으로 push 된 모든 액션을 'group_place'
        # 1 개로 묶음 — 사용자 Z 한 번에 그룹 통째 사라짐.
        self._scene.end_group_action('group_place')

        # 미리보기 종료 → IDLE
        canvas._f5_in_preview = False
        self._viewer.set_ghost_enabled(False)
        self._viewer.clear_ghost()
        self._dim_panel.set_mode_text('[IDLE]')
        self._f5_pending_dims = None
        self._f5_dep_meta = None
        self._f5_user_rv_override = False
        self._auto_regen_core_slabs()
        self._refresh_2d()

    def _collapse_to_single_floor(self):
        """[정의 탭 단일층] floor_index>0 부재를 모두 제거.

        정의는 '건물 층 복제 없는 1개 인스턴스' 다. 모든 부재 타입에서
        floor_index==0 이 곧 1층(첫 인스턴스)이며(중간보는 같은 floor_index=0
        에 바닥+천장 2개), 수직3층모듈도 1개 인스턴스가 floor_index=0 이다.
        따라서 floor_index>0 을 제거하면 타입과 무관하게 정확히 1세트만 남는다.
        """
        to_remove = [cid for cid, c in self._scene.components.items()
                     if int(getattr(c, 'floor_index', 0)) > 0]
        for cid in to_remove:
            self._viewer.remove_component_visual(cid)
            self._snap.remove_component(cid)
            self._scene.remove_component(cid)
        if to_remove:
            self._refresh_2d()

    # ── 실(Room) 처리 (2026-05-24 2단계) ─────────────────
    def _f5_on_room_complete(self, points):
        """2D 캔버스에서 폴리곤을 닫으면 호출 — 이름·용도 입력 후 실 생성.

        [CoT] 절차:
        1. 점 3개 미만이면 무시(캔버스가 이미 거르지만 방어).
        2. 이름·용도 다이얼로그(취소 시 생성 안 함).
        3. Room 생성 → scene.add_room → 3D 반투명 색면 렌더 → 2D 갱신.
        """
        if not points or len(points) < 3:
            return
        from modular_3d.ui.room_dialog import ask_room_type
        from modular_3d.model.room import Room
        from modular_3d.카탈로그.room_types import get_room_type
        from modular_3d.model import Action
        parent = self._f5_panel if self._f5_panel else None
        room_type = ask_room_type(parent)
        if room_type is None:
            return  # 취소
        base_poly = [(float(x), float(y)) for (x, y) in points]
        # [정책 2026-05-28 다층] 모듈 배치처럼 실도 전체 층에 복제(옥상 제외 —
        # 옥상엔 해당 실이 없음). n_floors = 패널 층수(정의 탭은 1 고정 → 단층).
        n_floors = int(self._f5_panel.floors) if self._f5_panel else 1
        if n_floors < 1:
            n_floors = 1
        gid = self._next_room_group_id()
        self._scene.begin_group_action()
        first_rid = None
        for k in range(n_floors):
            room = Room(room_type=room_type, polygon=list(base_poly),
                        floor_index=k, group_id=gid)
            rid = self._scene.add_room(room)
            if first_rid is None:
                first_rid = rid
            self._scene.undo_stack.append(Action(
                action_type='room_add', data={'room_id': rid}))
            self._render_room_3d(room)
        self._scene.end_group_action('room_group')
        self._refresh_2d()
        rt = get_room_type(room_type)
        label = rt.name if rt is not None else room_type
        if self._dim_panel is not None:
            self._dim_panel.set_mode_text(f'[실 생성: {label}]')
        print(f'[ROOM] 실 #{first_rid} 생성: {label} ({room_type}), '
              f'{n_floors}층 복제 gid={gid}')

    def _next_room_group_id(self) -> int:
        """현재 Scene 실 중 가장 큰 group_id + 1 (모두 0이면 1)."""
        mx = 0
        for r in getattr(self._scene, 'rooms', {}).values():
            g = int(getattr(r, 'group_id', 0))
            if g > mx:
                mx = g
        return mx + 1

    def _room_group_members(self, room_id: int) -> List[int]:
        """같은 group_id 의 모든 실 id. group_id<=0 이면 자기 자신만."""
        r = self._scene.rooms.get(room_id)
        if r is None:
            return [room_id]
        g = int(getattr(r, 'group_id', 0))
        if g <= 0:
            return [room_id]
        ids = [rid for rid, rr in self._scene.rooms.items()
               if int(getattr(rr, 'group_id', 0)) == g]
        return ids if ids else [room_id]

    def _render_room_3d(self, room):
        """실 1건을 3D 반투명 색면으로 렌더(이미 있으면 갱신).

        [2026-05-28 다층] floor_index 에 따라 z 를 올려 해당 층 슬래브 위에 표시.
        """
        from modular_3d.render.room_mesh import build_room_mesh, ROOM_PLANE_Z_MM
        from modular_3d.카탈로그.room_types import ROOM_TYPE_BY_KEY
        from modular_3d.analysis.constants import FLOOR_HEIGHT
        rt = ROOM_TYPE_BY_KEY.get(getattr(room, 'room_type', ''))
        color = rt.color if rt is not None else (160, 160, 160)
        z = ROOM_PLANE_Z_MM + int(getattr(room, 'floor_index', 0)) * float(FLOOR_HEIGHT)
        mesh = build_room_mesh(room.polygon, color, z=z)
        if mesh is None:
            return
        v, f, c = mesh
        if hasattr(self._viewer, 'add_room_visual'):
            self._viewer.add_room_visual(room.id, v, f, c)

    def _render_all_rooms_3d(self):
        """씬의 모든 실을 3D 로 렌더(불러오기·작업공간 교체 후 호출)."""
        for room in getattr(self._scene, 'rooms', {}).values():
            self._render_room_3d(room)

    def _clear_rooms_3d(self):
        """3D 실 색면 전부 제거(작업공간 비우기 시)."""
        if hasattr(self._viewer, 'remove_room_visual'):
            for rid in list(getattr(self._scene, 'rooms', {}).keys()):
                self._viewer.remove_room_visual(rid)

    def _delete_room(self, room_id: int):
        """실 삭제 — 같은 group(모든 층)을 함께 제거 + 되돌리기 기록 + 2D 갱신."""
        from modular_3d.model import Action
        room = self._scene.rooms.get(room_id)
        if room is None:
            return
        members = self._room_group_members(room_id)
        self._scene.begin_group_action()
        for rid in members:
            rr = self._scene.rooms.get(rid)
            if rr is None:
                continue
            if hasattr(self._viewer, 'remove_room_visual'):
                self._viewer.remove_room_visual(rid)
            self._scene.remove_room(rid)
            self._scene.undo_stack.append(Action(
                action_type='room_del', data={'room': rr}))
        self._scene.end_group_action('room_group')
        self._refresh_2d()
        print(f'[ROOM] 실 #{room_id} 삭제 ({len(members)}층)')

    def _room_ghost(self, room_type: str, polygon):
        """실 이동/복사 미리보기 3D 고스트(반투명 색면) 갱신."""
        from modular_3d.render.room_mesh import build_room_mesh
        from modular_3d.카탈로그.room_types import ROOM_TYPE_BY_KEY
        rt = ROOM_TYPE_BY_KEY.get(room_type)
        color = rt.color if rt is not None else (160, 160, 160)
        mesh = build_room_mesh(polygon, color, alpha=0.5)
        if mesh is None:
            return
        v, f, c = mesh
        if hasattr(self._viewer, 'update_room_ghost'):
            self._viewer.update_room_ghost(v, f, c)

    def _room_ghost_clear(self):
        if hasattr(self._viewer, 'clear_room_ghost'):
            self._viewer.clear_room_ghost()

    def _room_commit(self, src_id: int, polygon, mode: str):
        """실 이동/복사 확정 — 같은 group(모든 층) 함께 + 되돌리기 + 3D/2D 갱신.

        [정책 2026-05-28 다층] 2D 편집은 1층 실에서 일어나며, 같은 group 의 다른
        층 실은 같은 xy 평면을 공유하므로 같은 새 polygon 으로 동기한다.
        복사는 원본 group 의 층 분포(floor_index 집합)를 그대로 새 group 으로 복제.
        """
        from modular_3d.model import Action
        from modular_3d.model.room import Room
        self._room_ghost_clear()
        poly = [(float(x), float(y)) for (x, y) in polygon]
        members = self._room_group_members(src_id)
        if mode == 'copy':
            src = self._scene.rooms.get(src_id)
            rtype = src.room_type if src is not None else 'living'
            floors = sorted({int(getattr(self._scene.rooms.get(rid), 'floor_index', 0))
                             for rid in members
                             if self._scene.rooms.get(rid) is not None})
            if not floors:
                floors = [0]
            gid = self._next_room_group_id()
            self._scene.begin_group_action()
            for k in floors:
                nr = Room(room_type=rtype, polygon=list(poly),
                          floor_index=k, group_id=gid)
                rid = self._scene.add_room(nr)
                self._scene.undo_stack.append(Action(
                    action_type='room_add', data={'room_id': rid}))
                self._render_room_3d(nr)
            self._scene.end_group_action('room_group')
        else:  # move (제자리 회전 포함)
            self._scene.begin_group_action()
            for rid in members:
                room = self._scene.rooms.get(rid)
                if room is None:
                    continue
                old_poly = [(float(x), float(y)) for (x, y) in room.polygon]
                room.polygon = list(poly)
                self._scene.undo_stack.append(Action(
                    action_type='room_move',
                    data={'room_id': rid, 'old_polygon': old_poly}))
                self._render_room_3d(room)
            self._scene.end_group_action('room_group')
        self._refresh_2d()
        print(f'[ROOM] commit {mode} → #{src_id} ({len(members)}층)')

    # ── 개구부(Opening) 처리 (2026-05-24 3단계) ───────────

    def _opening_add_start(self):
        """'개구부 추가' → 크기 다이얼로그 후 캔버스 고스트 미리보기 시작."""
        from modular_3d.ui.opening_dialog import ask_opening_size
        parent = self._f5_panel if self._f5_panel else None
        size = ask_opening_size(parent)
        if size is None:
            return
        canvas = self._f5_panel.canvas if self._f5_panel else None
        if canvas is not None and hasattr(canvas, 'begin_opening_preview'):
            canvas.begin_opening_preview(
                size['w'], size['h'], size['sill'], 'add')

    def _opening_ghost(self, comp_id: int, op: dict):
        """개구부 고스트(반투명 박스) 3D 갱신 — 미리보기 중 마우스 따라."""
        from modular_3d.render.opening_mesh import opening_world_box
        from modular_3d.render.mesh_builder import build_solid_box
        import numpy as np
        comp = self._scene.components.get(comp_id)
        if comp is None:
            return
        box = opening_world_box(comp, op)
        if box is None:
            return
        mn, mx = box
        color = np.array([1.0, 0.55, 0.15, 0.45], dtype=np.float32)  # 주황 반투명
        v, f, c = build_solid_box(mn, mx, color)
        if hasattr(self._viewer, 'update_opening_ghost'):
            self._viewer.update_opening_ghost(v, f, c)

    def _opening_ghost_clear(self):
        if hasattr(self._viewer, 'clear_opening_ghost'):
            self._viewer.clear_opening_ghost()

    def _opening_sync_targets(self, comp_id: int) -> List[int]:
        """개구부 다층 동기 대상 — 같은 group_id + 같은 sub_index 의 모든 층 부재 id.

        [정책 2026-05-28] 모듈·바닥패널 등 다층 그룹은 같은 group_id 를 공유하고
        floor_index 만 다르며 형상(치수·회전·앵커)이 동일하다 → 면 로컬 좌표가
        같으므로 같은 개구부 dict 를 모든 층에 그대로 적용해도 정합한다.
        group_id<=0(레거시/단독 부재)이면 자기 자신만 반환 — '새로 만드는 것만
        다층' 정책상 불러온 단층 데이터는 자기 부재만 영향.
        """
        comp = self._scene.components.get(comp_id)
        if comp is None:
            return [comp_id]
        gid = int(getattr(comp, 'group_id', 0))
        if gid <= 0:
            return [comp_id]
        sub = int(getattr(comp, 'sub_index', 0))
        ids = [cid for cid, c in self._scene.components.items()
               if int(getattr(c, 'group_id', 0)) == gid
               and int(getattr(c, 'sub_index', 0)) == sub]
        return ids if ids else [comp_id]

    @staticmethod
    def _find_matching_opening(comp, op: dict) -> Optional[int]:
        """comp.openings 에서 op 와 내용(u,v,w,h,face) 같은 첫 인덱스. 없으면 None.

        다층 동기 시 인덱스가 부재마다 다를 수 있어(기존 1층 개구부 등) 내용
        기반으로 매칭한다.
        """
        eps = 1e-6
        for i, o in enumerate(getattr(comp, 'openings', None) or []):
            if (abs(float(o.get('u', 0)) - float(op.get('u', 0))) < eps and
                    abs(float(o.get('v', 0)) - float(op.get('v', 0))) < eps and
                    abs(float(o.get('w', 0)) - float(op.get('w', 0))) < eps and
                    abs(float(o.get('h', 0)) - float(op.get('h', 0))) < eps and
                    o.get('face') == op.get('face')):
                return i
        return None

    def _opening_commit(self, target_id: int, op: dict, mode: str, src):
        """개구부 확정 — add/move/copy + 되돌리기 기록 + 3D/2D 갱신.

        [정책 2026-05-28 다층 적용] 모듈 배치가 전체 층에 복제되듯, 개구부도 같은
        group(같은 위치의 모든 층) 부재에 동기화한다. 여러 부재에 걸친 변경을
        'opening_group' 단일 그룹 액션으로 묶어 undo 한 번에 전부 복원.
        """
        from modular_3d.model import Action
        self._opening_ghost_clear()
        tgt = self._scene.components.get(target_id)
        if tgt is None:
            return
        if mode == 'move' and src is not None:
            cid, idx = src
            comp = self._scene.components.get(cid)
            if comp is None or not (0 <= idx < len(comp.openings)):
                return
            old_op = dict(comp.openings[idx])
            self._scene.begin_group_action()
            for tid in self._opening_sync_targets(cid):
                t = self._scene.components.get(tid)
                if t is None:
                    continue
                if tid == cid:
                    j = idx
                else:
                    j = self._find_matching_opening(t, old_op)
                    if j is None:
                        continue
                old_j = dict(t.openings[j])
                t.openings[j] = dict(op)
                self._scene.undo_stack.append(Action(
                    action_type='opening_move',
                    data={'comp_id': tid, 'index': j, 'old_op': old_j}))
                self._rebuild_component_visual(tid)
            self._scene.end_group_action('opening_group')
        else:  # add / copy → 대상 부재(+모든 층)에 새 개구부
            self._scene.begin_group_action()
            for tid in self._opening_sync_targets(target_id):
                t = self._scene.components.get(tid)
                if t is None:
                    continue
                t.openings.append(dict(op))
                self._scene.undo_stack.append(Action(
                    action_type='opening_add',
                    data={'comp_id': tid, 'index': len(t.openings) - 1}))
                self._rebuild_component_visual(tid)
            self._scene.end_group_action('opening_group')
        self._refresh_2d()
        print(f'[OPENING] commit {mode} → #{target_id} {op} '
              f'(동기 {len(self._opening_sync_targets(target_id))}개 층)')

    def _delete_opening(self, comp_id: int, index: int):
        """부재의 index 번째 개구부 삭제 + 되돌리기 기록 + 3D/2D 갱신.

        [정책 2026-05-28] 같은 group 의 모든 층 부재에서 내용이 같은 개구부를
        함께 삭제. 'opening_group' 그룹 액션으로 묶음.
        """
        from modular_3d.model import Action
        comp = self._scene.components.get(comp_id)
        if comp is None or not getattr(comp, 'openings', None):
            return
        if not (0 <= index < len(comp.openings)):
            return
        removed = dict(comp.openings[index])
        self._scene.begin_group_action()
        for tid in self._opening_sync_targets(comp_id):
            t = self._scene.components.get(tid)
            if t is None or not getattr(t, 'openings', None):
                continue
            if tid == comp_id:
                j = index
            else:
                j = self._find_matching_opening(t, removed)
                if j is None:
                    continue
            rem = dict(t.openings[j])
            t.openings.pop(j)
            self._scene.undo_stack.append(Action(
                action_type='opening_del',
                data={'comp_id': tid, 'index': j, 'op': rem}))
            self._rebuild_component_visual(tid)
        self._scene.end_group_action('opening_group')
        self._refresh_2d()
        print(f'[OPENING] #{comp_id} 개구부 {index} 삭제 (다층 동기)')

    def _rebuild_component_visual(self, comp_id: int):
        """부재 1개의 3D 메시를 다시 생성(개구부 변경 반영)."""
        from modular_3d.render.mesh_builder import build_component_mesh
        comp = self._scene.components.get(comp_id)
        if comp is None:
            return
        v, f, c = build_component_mesh(comp)
        self._viewer.remove_component_visual(comp_id)
        self._viewer.add_component_visual(comp_id, v, f, c)

    # ── 정의 가져오기 배치 (2026-05-24 4단계) ─────────────
    def _import_definition_start(self, name: str):
        """'가져오기' 선택 → 정의 복원 + 캔버스 그룹 고스트 미리보기 시작."""
        lib = getattr(self, '_definition_library', None)
        d = lib.get(name) if lib is not None else None
        if d is None:
            return
        def_scene, _nf = d.to_scene()
        comps = list(def_scene.components.values())
        if not comps:
            return
        # 템플릿 보관(확정까지) — 단일층 floor 0.
        self._import_templates = comps
        self._import_old_ids = [int(c.id) for c in comps]
        self._import_rooms = list(getattr(def_scene, 'rooms', {}).values())
        self._import_n_floors = self._f5_panel.floors if self._f5_panel else 3
        # 루트(부모 없는 본체) — 기준점 후보 = 루트 바닥 4코너 xy.
        root = next((c for c in comps
                     if int(getattr(c, 'parent_id', 0)) == 0
                     and int(getattr(c, 'sub_index', 0)) == 0), comps[0])
        corners = root.get_world_corners()[:4]
        pivots = [(float(c[0]), float(c[1])) for c in corners]
        # 2D footprint(부재 바닥 4코너) + 실 폴리곤.
        footprints = []
        for c in comps:
            fc = c.get_world_corners()[:4]
            footprints.append([(float(p[0]), float(p[1])) for p in fc])
        room_polys = [[(float(x), float(y)) for (x, y) in r.polygon]
                      for r in self._import_rooms]
        canvas = self._f5_panel.canvas if self._f5_panel else None
        if canvas is not None and hasattr(canvas, 'begin_import_preview'):
            canvas.begin_import_preview(pivots, footprints, room_polys)
        self._viewer.set_ghost_enabled(True)
        self._dim_panel.set_mode_text(
            f'[정의 가져오기: {name} — 마우스 이동, R 회전, V 기준점, 클릭 배치]')

    def _f5_on_import_ghost(self, target, rot_deg, pivot):
        """그룹 고스트(1층 합본 메시) 갱신 — 마우스 따라."""
        import copy as _copy
        import numpy as np
        from modular_3d.model.definition_place import transform_point
        from modular_3d.render.mesh_builder import (
            build_ghost_component_mesh, _merge_meshes)
        tmpls = getattr(self, '_import_templates', None)
        if not tmpls:
            return
        meshes = []
        for t in tmpls:
            clone = _copy.deepcopy(t)
            nx, ny = transform_point(float(clone.position[0]),
                                     float(clone.position[1]), pivot, rot_deg, target)
            clone.position = np.array([nx, ny, float(clone.position[2])],
                                      dtype=np.float64)
            clone.rotation = (int(clone.rotation) + int(rot_deg)) % 360
            clone.generate_sub_components()
            meshes.append(build_ghost_component_mesh(clone))
        if meshes:
            v, f, c = _merge_meshes(meshes)
            self._viewer.update_ghost(v, f, c)

    def _f5_on_import_cancel(self):
        """가져오기 취소 — 고스트/템플릿 정리."""
        self._viewer.set_ghost_enabled(False)
        self._viewer.clear_ghost()
        self._import_templates = None
        self._import_rooms = None
        self._dim_panel.set_mode_text('[IDLE]')

    def _f5_on_import_commit(self, target, rot_deg, pivot):
        """클릭 — 정의를 N층 복제 + 실 각층 복제로 씬에 삽입(되돌리기 기록)."""
        import copy as _copy
        import numpy as np
        from modular_3d.model import Action, FloorPanel, StructWall
        from modular_3d.model.definition_place import transform_point
        from modular_3d.model.multi_floor import next_group_id
        from modular_3d.model.room import Room
        from modular_3d.render.mesh_builder import build_component_mesh
        from modular_3d.카탈로그.geometry import FLOOR_HEIGHT

        tmpls = getattr(self, '_import_templates', None)
        if not tmpls:
            self._f5_on_import_cancel()
            return
        old_ids = self._import_old_ids
        rooms = getattr(self, '_import_rooms', None) or []
        n_floors = int(getattr(self, '_import_n_floors', 3) or 3)
        H = float(FLOOR_HEIGHT)
        new_gid = next_group_id(self._scene)

        self._scene.begin_group_action()
        for k in range(n_floors):
            zoff = k * H
            old_to_new: Dict[int, int] = {}
            created = []
            for old_id, t in zip(old_ids, tmpls):
                clone = _copy.deepcopy(t)
                clone.id = 0
                nx, ny = transform_point(float(clone.position[0]),
                                         float(clone.position[1]),
                                         pivot, rot_deg, target)
                clone.position = np.array(
                    [nx, ny, float(clone.position[2]) + zoff], dtype=np.float64)
                clone.rotation = (int(clone.rotation) + int(rot_deg)) % 360
                clone.group_id = new_gid
                clone.floor_index = k
                new_cid = self._scene.add_component(clone)
                old_to_new[old_id] = new_cid
                created.append((new_cid, clone))
            # 같은 층 안에서 부모/합체 관계 재배선(템플릿 옛 id → 새 id)
            for cid, clone in created:
                pid = int(getattr(clone, 'parent_id', 0) or 0)
                if pid in old_to_new:
                    clone.parent_id = old_to_new[pid]
                elif pid:
                    clone.parent_id = 0
                if isinstance(clone, FloorPanel):
                    clone.merged_wall_ids = [
                        old_to_new[x] for x in (clone.merged_wall_ids or [])
                        if x in old_to_new]
                elif isinstance(clone, StructWall):
                    fp = getattr(clone, 'merged_fp_id', None)
                    clone.merged_fp_id = old_to_new.get(fp) if fp in old_to_new else None
                clone.generate_sub_components()
                v, f, c = build_component_mesh(clone)
                self._viewer.add_component_visual(cid, v, f, c)
                self._snap.add_component(cid, clone)
        self._scene.end_group_action('group_place')

        # 실 — 각 층마다 복제(변환 적용).
        for k in range(n_floors):
            for r in rooms:
                poly = [transform_point(float(x), float(y), pivot, rot_deg, target)
                        for (x, y) in r.polygon]
                nr = Room(room_type=r.room_type, polygon=poly, floor_index=k)
                rid = self._scene.add_room(nr)
                self._scene.undo_stack.append(Action(
                    action_type='room_add', data={'room_id': rid}))
                self._render_room_3d(nr)

        self._viewer.set_ghost_enabled(False)
        self._viewer.clear_ghost()
        self._import_templates = None
        self._import_rooms = None
        self._status.update_count(self._scene.component_count)
        self._auto_regen_core_slabs()
        self._refresh_2d()
        self._dim_panel.set_mode_text('[정의 배치 완료]')
        print(f'[IMPORT] 정의 배치: {n_floors}층 복제, gid={new_gid}')

    def _update_room(self, room_id: int, room_type: str,
                     live_load: float = -1.0, sdl: float = -1.0):
        """실 용도·하중 수정 — 데이터 갱신 + 3D 색면 재렌더 + 2D 갱신.

        live_load/sdl 이 음수면 '용도 기본값 사용'(override 해제), 0 이상이면 그
        값을 사용자 지정 override 로 저장.
        """
        room = getattr(self._scene, 'rooms', {}).get(room_id)
        if room is None:
            return
        room.room_type = room_type
        room.live_load_override = None if live_load is None or live_load < 0 else float(live_load)
        room.sdl_override = None if sdl is None or sdl < 0 else float(sdl)
        self._render_room_3d(room)   # 색(용도) 바뀔 수 있어 재렌더
        self._refresh_2d()
        print(f'[ROOM] 실 #{room_id} 용도={room_type} '
              f'활하중override={room.live_load_override} SDLoverride={room.sdl_override}')

    def _sync_dependent_beam_sections(self):
        """종속·합체 부재의 beam_section_type 을 부모/패널 유효 타입에 맞춰 동기화.

        배치/합체 직후 호출 — 합체된 구조벽이 바닥패널 타입을 따라가는 등.
        변경된 부재만 재생성 + 3D 갱신.
        """
        from modular_3d.model import effective_beam_section_type
        from modular_3d.render.mesh_builder import build_component_mesh
        for cid, c in self._scene.components.items():
            eff = effective_beam_section_type(c, self._scene)
            if getattr(c, 'beam_section_type', 'shs') != eff:
                c.beam_section_type = eff
                c.generate_sub_components()
                v, f, col = build_component_mesh(c)
                self._viewer.add_component_visual(cid, v, f, col)

    def _set_beam_section_type(self, comp_id: int, sec_type: str):
        """선택 부재의 보 단면 타입 변경(각형강관/H형강) — 다층 복제본 + 종속 동기화.

        - 같은 group_id·같은 sub_index 의 본체 부재(그 위치 모든 층)에 일괄 적용.
        - 그 후 모든 부재의 유효 타입을 재계산해 종속(캔틸·중간보)·합체 구조벽이
          부모/패널 타입을 따라가도록 beam_section_type 을 동기화한다.
        - 변경된 부재만 generate + 좌측 3D 메시 재빌드 + 2D 갱신.
        """
        from modular_3d.model import effective_beam_section_type
        from modular_3d.render.mesh_builder import build_component_mesh
        scene = self._scene
        comp = scene.components.get(comp_id)
        if comp is None or sec_type not in ('shs', 'h'):
            return
        gid = getattr(comp, 'group_id', 0)
        sub = getattr(comp, 'sub_index', 0)
        changed = set()
        # 1) 본체 + 같은 그룹·같은 sub_index 다층 복제본에 적용.
        if gid > 0:
            for cid, c in scene.components.items():
                if (getattr(c, 'group_id', 0) == gid
                        and getattr(c, 'sub_index', 0) == sub):
                    if getattr(c, 'beam_section_type', 'shs') != sec_type:
                        c.beam_section_type = sec_type
                        changed.add(cid)
        else:
            if getattr(comp, 'beam_section_type', 'shs') != sec_type:
                comp.beam_section_type = sec_type
                changed.add(comp_id)
        # 2) 종속·합체 부재가 부모/패널 타입을 따라가도록 동기화.
        for cid, c in scene.components.items():
            eff = effective_beam_section_type(c, scene)
            if getattr(c, 'beam_section_type', 'shs') != eff:
                c.beam_section_type = eff
                changed.add(cid)
        # 3) 변경 부재 재생성 + 3D 갱신.
        for cid in changed:
            c = scene.components[cid]
            c.generate_sub_components()
            v, f, col = build_component_mesh(c)
            self._viewer.add_component_visual(cid, v, f, col)
        self._refresh_2d()
        print(f'[SECTION] #{comp_id} 보 단면 타입 → {sec_type} (변경 {len(changed)}개)')

    def _f5_on_escape(self):
        """F5 캔버스 Esc — 배치/이동/복사 취소."""
        self._f5_pending_placement = False
        self._f5_pending_dims = None
        self._f5_dep_meta = None
        self._f5_move_group_id = 0  # 이동 모드도 해제
        self._f5_move_target_ids = []
        self._f5_move_seed_id = 0
        self._f5_copy_source_ids = []
        self._f5_copy_seed_id = 0
        self._f5_user_rv_override = False
        if self._f5_panel and self._f5_panel.canvas:
            self._f5_panel.canvas._f5_in_preview = False
        self._viewer.set_ghost_enabled(False)  # 게이트 OFF
        self._viewer.clear_ghost()
        self._dim_panel.deactivate()
        self._dim_panel.set_mode_text('[IDLE]')

    # ── 단계 3c: R/V/Move/Delete ──────────────────────────────

    def _f5_update_ghost_at(self, world_x: float, world_y: float):
        """현재 pending dims/comp_type/rotation/anchor 로 좌측 3D 고스트 갱신."""
        dims = getattr(self, '_f5_pending_dims', None)
        if dims is None:
            return
        import numpy as np
        from modular_3d.model.multi_floor import _floor_z_list
        # 1층(floor=0) z 좌표 결정
        ct = self._current_comp_type
        merge = bool(dims.get('merge', False))
        z_list = _floor_z_list(ct, n_floors=1, merge_with_panel=merge)
        z_for_ghost = z_list[0] if z_list else 0.0

        ghost_pos = np.array([world_x, world_y, z_for_ghost], dtype=np.float64)
        ghost = _create_component(
            ct, ghost_pos, dims, self._preview_rotation, self._preview_anchor)
        verts, faces, colors = build_ghost_component_mesh(ghost)
        self._viewer.update_ghost(verts, faces, colors)

    def _f5_on_canvas_move(self, world_x: float, world_y: float):
        """F5 캔버스 마우스 이동 (PREVIEW) → 좌측 3D 고스트 갱신."""
        self._f5_update_ghost_at(world_x, world_y)

    def _f5_on_rotate(self):
        """R 키 — PREVIEW 고스트 90° 회전. 즉시 2D 화면 갱신.
        (정책 c 단계 2026-05-12) 사용자 R/V 입력 후 마우스 호버의 dep_snap
        자동 회전 변경 차단 — `_f5_user_rv_override` 플래그 ON.
        """
        self._preview_rotation = (self._preview_rotation + 90) % 360
        self._f5_user_rv_override = True
        canvas = self._f5_panel.canvas if self._f5_panel else None
        if canvas is not None:
            wx, wy = canvas._f5_mouse_world
            self._f5_update_ghost_at(wx, wy)
            canvas.update()
        print(f'[F5 ROTATE] {self._preview_rotation}°')

    def _f5_on_anchor(self):
        """V 키 — PREVIEW 고스트 앵커 변경. 즉시 2D 화면 갱신."""
        self._preview_anchor = (self._preview_anchor + 1) % 4
        self._f5_user_rv_override = True
        canvas = self._f5_panel.canvas if self._f5_panel else None
        if canvas is not None:
            wx, wy = canvas._f5_mouse_world
            self._f5_update_ghost_at(wx, wy)
            canvas.update()
        anchor_names = ['좌하', '우하', '우상', '좌상']
        print(f'[F5 ANCHOR] {anchor_names[self._preview_anchor]}')

    # ── 자유 이동 모드 (M 키) ──────────────────────────────────
    # 흐름: SELECTED → M → 그룹 정보 PREVIEW에 채움(_f5_move_group_id 설정)
    #      → 마우스 따라 반투명 고스트(R/V 작동) → 클릭 → 그룹 통째 이동.
    # 새 그룹 생성과 분기 — _f5_on_canvas_click 에서 _f5_move_group_id 유무로
    # 이동/생성 결정.

    def _f5_on_move_key(self, comp_id: int):
        """SELECTED + M 키 — 자유 이동 모드 진입.

        [정책 2026-05-12 b 단계] 이동 대상은 `_resolve_move_targets` 결과:
        본체 부재면 같은 그룹 전체 (코어 제외), 코어벽이면 자기 한 장,
        코어슬래브면 슬래브+코어벽, 종속 부재면 자기만.
        """
        comp = self._scene.components.get(comp_id)
        if comp is None:
            return
        target_ids = self._resolve_move_targets(comp_id)
        if not target_ids:
            print(f'[F5 M] #{comp_id} 이동 대상 결정 실패')
            return
        # seed = 사용자가 클릭한 부재 자신 (preview 의 dims/rotation/anchor 출처)
        seed = comp

        # PREVIEW 진입에 필요한 상태 채움
        self._current_comp_type = seed.comp_type
        self._preview_rotation = seed.rotation
        self._preview_anchor = seed.anchor
        self._f5_pending_dims = dict(seed.dimensions)
        self._f5_pending_dep_meta = None
        self._f5_dep_meta = None
        # 이동 모드 마커 — 대상 ID 리스트 + seed(=클릭한 부재) ID 저장
        self._f5_move_target_ids = list(target_ids)
        self._f5_move_seed_id = int(comp_id)
        # 구버전 호환: 일부 코드가 _f5_move_group_id > 0 으로 이동 모드 판정
        self._f5_move_group_id = getattr(comp, 'group_id', 0) or comp_id
        # 새 이동 세션 — 사용자 R/V 잠금 해제 (seed 의 기존 회전·앵커가 시작값)
        self._f5_user_rv_override = False

        # 캔버스 PREVIEW 진입 + 포커스
        if self._f5_panel and self._f5_panel.canvas:
            cv = self._f5_panel.canvas
            cv._f5_in_preview = True
            cv._selected_id = -1
            cv._state = 'idle'
            cv.setFocus()
            # 마우스 위치 초기화: 현재 부재 위치를 시드로
            cv._f5_mouse_world = (float(seed.position[0]), float(seed.position[1]))
            cv.update()

        # 좌측 3D 고스트 게이트 ON + 즉시 표시
        self._viewer.set_ghost_enabled(True)
        wx, wy = float(seed.position[0]), float(seed.position[1])
        self._f5_update_ghost_at(wx, wy)

        name = TYPE_NAMES.get(seed.comp_type, '')
        n_targets = len(target_ids)
        self._dim_panel.set_mode_text(
            f'[F5 이동: {name} #{comp_id} ({n_targets}개 같이 이동) — '
            f'클릭으로 위치 확정 / R: 회전 / V: 앵커]')
        print(f'[F5 M] 이동 진입 seed=#{comp_id} 대상 {n_targets}개 '
              f'pos=({wx:.0f},{wy:.0f}) rot={seed.rotation} anc={seed.anchor}')

    # ── 단계 4: 종속 부재 흐름 ───────────────────────────────

    def _interior_wall_height_for_parent(self, parent) -> float:
        """내벽 높이 = 부모 기준 자동 검출.

        - 부모가 모듈: 그 모듈 높이 그대로(사용자 커스텀 높이도 반영). 모듈은
          상부보(천장)가 있어 내벽 상단이 상부보 하면(h-s-half_s)에서 막힌다 —
          같은 층 모듈 4면 벽과 flush.
        - 부모가 바닥패널·캔틸레버슬래브: 천장(상부보)이 없고 다음 층 바닥이 천장이
          되는 구조라, 모듈 대비 상부보 높이(SECTION_W_MM=200)만큼 더 높여 다음 층
          바닥 레벨까지 올린다. 기준 높이는 같은 그룹 모듈 높이(없으면 표준 한 층).
        """
        from modular_3d.카탈로그.geometry import MODULE_HEIGHT_MM, SECTION_W_MM
        from modular_3d.model import ComponentType as _CT
        if parent.comp_type == _CT.MODULE:
            return float(parent.dimensions.get('height', MODULE_HEIGHT_MM))
        # 바닥패널·캔틸레버슬래브 — 같은 그룹 모듈 높이 검출 후 +200(천장보 높이).
        base_h = float(MODULE_HEIGHT_MM)
        gid = getattr(parent, 'group_id', 0)
        if gid:
            for c in self._scene.components.values():
                if (getattr(c, 'group_id', 0) == gid
                        and c.comp_type == _CT.MODULE):
                    base_h = float(c.dimensions.get('height', MODULE_HEIGHT_MM))
                    break
        return base_h + float(SECTION_W_MM)

    def _f5_on_dependency_pick(self, dep_type, parent_id: int,
                               anchor_edge_id: int, mid_level):
        """DEPENDENCY_PICK 단계에서 부모 부재 클릭 시 호출.

        흐름:
          - 일반 종속(캔틸/중간보·기둥): 부모 정보 저장 → DIMENSION_INPUT 진입.
          - 구조벽 합체 모드: dim 은 이미 입력됨 → 합체 대상 FP 만 저장하고
            바로 PLACEMENT_PREVIEW 진입.
        """
        parent = self._scene.components.get(parent_id)
        if parent is None:
            return
        parent_gid = getattr(parent, 'group_id', 0)
        if parent_gid <= 0:
            print(f'[F5 DEP] 부모 #{parent_id} group_id 미부여 — 무시')
            return

        # 구조벽 합체 분기: dim 이미 채움 → 바로 PREVIEW.
        if dep_type == ComponentType.STRUCT_WALL:
            self._current_comp_type = dep_type
            # 합체 메타: canvas_click 시점에 같은 FP 그룹의 같은 floor FP 찾아 merge
            self._f5_dep_meta = {
                'parent_group_id': parent_gid,
                'parent_id': parent_id,
                'anchor_edge_id': -1,
                'mid_beam_level': None,
                'parent_comp_type': parent.comp_type,
                'merge_target_fp_group': parent_gid,  # 합체 대상 FP 그룹
            }
            if self._f5_panel and self._f5_panel.canvas:
                self._f5_panel.canvas._f5_in_preview = True
                self._f5_panel.canvas.setFocus()
                self._f5_panel.canvas.update()
            self._viewer.set_ghost_enabled(True)
            self._dim_panel.set_mode_text(
                f'[F5 구조벽 합체: 바닥패널 #{parent_id} 선택됨 — '
                f'캔버스 클릭으로 위치 확정 / R: 회전 / V: 앵커]')
            print(f'[F5 WALL MERGE] FP #{parent_id} (gid={parent_gid}) 선택 → PREVIEW')
            return

        # F5 배치 흐름 시작
        self._current_comp_type = dep_type
        self._preview_rotation = 0
        self._preview_anchor = 0
        self._f5_pending_placement = True
        # 종속 메타데이터 보관 (canvas_click 시점에 사용)
        self._f5_dep_meta = {
            'parent_group_id': parent_gid,
            'parent_id': parent_id,
            'anchor_edge_id': anchor_edge_id,
            'mid_beam_level': mid_level,
            'parent_comp_type': parent.comp_type,
        }

        # 내벽: 부모 높이를 검출해 wall_height 에 저장 — dim 확정 시 height 채움.
        if dep_type == ComponentType.INTERIOR_WALL:
            self._f5_dep_meta['wall_height'] = self._interior_wall_height_for_parent(parent)

        # [2026-05-11 Phase 2 보정] 중간보(MID_BEAM) 는 dim_panel 단계를 생략한다.
        # 길이는 인접 기둥/외곽 보로 자동 결정되므로 사용자가 입력할 값이 없음.
        if dep_type == ComponentType.MID_BEAM:
            self._f5_pending_dims = {'width': 200.0, 'depth': 200.0, 'height': 200.0}
            self._f5_pending_placement = False
            if self._f5_panel and self._f5_panel.canvas:
                self._f5_panel.canvas._f5_in_preview = True
                self._f5_panel.canvas.setFocus()
                self._f5_panel.canvas.update()
            self._viewer.set_ghost_enabled(True)
            self._dim_panel.set_mode_text(
                f'[F5 중간보 — 캔버스 클릭으로 위치 확정 / R: 회전]')
            print(f'[F5 DEP PICK] MID_BEAM dim 생략 — 즉시 PREVIEW')
            return

        # 그 외 종속 부재: 기존 흐름 (dim → preview → click)
        defaults = self._current_defaults()
        self._dim_panel.activate(dep_type, defaults)
        name = TYPE_NAMES.get(dep_type, '')
        parent_name = TYPE_NAMES.get(parent.comp_type, '')
        self._dim_panel.set_mode_text(
            f'[F5 종속 배치: {name} → 부모 {parent_name}#{parent_id} '
            f'(edge={anchor_edge_id}{", "+mid_level if mid_level else ""}) — 사이즈 입력]')
        print(f'[F5 DEP PICK] {dep_type.value} parent_gid={parent_gid} '
              f'edge={anchor_edge_id} level={mid_level}')

    def _f5_on_undo(self):
        """Z 키 — 직전 사용자 액션 한 번 되돌리기 (그룹 단위)."""
        self._do_undo()
        # viewer/snap 상태 정리는 _do_undo 의 각 분기에서 처리됨.
        # 코어벽 조작 undo 시 슬래브를 코어벽 구성에 맞게 다시 정합.
        self._auto_regen_core_slabs()
        self._refresh_2d()

    def _f5_on_copy(self, comp_id: int):
        """C 키 — 선택된 부재의 복사 모드 진입.

        [정책 2026-05-16 — M 키 통합]
        이전: 별도의 복사 분기에서 클릭 시점에 deep copy + delta 적용. M 키
              분기와 회전·합체 보정이 따로 관리돼 한쪽 버그가 다른 쪽에 전이.
        현재: C 키 누르는 즉시 source_ids 의 deep copy 를 같은 위치에 박아
              두고(원본은 그대로), 그 복사본들을 seed 로 하여 M 키 이동
              흐름으로 진입. 사용자가 캔버스 클릭 시 M 분기가 그대로 동작해
              회전(같은 그룹+같은 sub_index 만 적용) · 합체 그룹 따라감 보정을
              자동 공유. Undo 한 번에 복사 통째 사라짐 ('group_place').

        [합체/페어 remap]
        deep copy 만 하면 복사본의 merged_fp_id / merged_wall_ids / parent_id
        가 원본 cid 를 그대로 가리켜 cross-link 되는 버그 발생. 본 함수가 새
        cid 를 발급한 직후 old→new cid 매핑으로 remap 한다. _floor_pairs /
        _child_pairs / _module_children / _child_parent (controls.py 관리 dict)
        도 같은 매핑으로 갱신.
        """
        import copy as _copy
        from modular_3d.model.multi_floor import next_group_id
        from modular_3d.model import FloorPanel, StructWall
        from modular_3d.render.mesh_builder import build_component_mesh

        comp = self._scene.components.get(comp_id)
        if comp is None:
            return
        source_ids = self._resolve_move_targets(comp_id)
        if not source_ids:
            print(f'[F5 C] #{comp_id} 복사 대상 결정 실패')
            return

        # 1) source 의 group_id 별로 새 gid 매핑 — _resolve_move_targets 가 합체로
        #    다른 group 까지 끌어와 여러 gid 가 섞일 수 있음. 각자 새 gid 발급.
        # [2026-05-16 버그 수정] next_group_id 는 "현재 scene 최대 gid + 1" 을
        # 반환한다. clone add 전이라 매 호출이 같은 값을 반환해 모든 새 gid 가
        # 같은 값으로 매핑되던 버그(FP'·wall' 이 같은 gid → M 분기에서 wall
        # rotation 이 FP rotation 으로 덮어써짐) — 시작값 받아서 로컬 증가.
        old_to_new_gid: Dict[int, int] = {}
        next_gid_counter = next_group_id(self._scene)
        for cid in source_ids:
            src = self._scene.components.get(cid)
            if src is None:
                continue
            old_gid = int(getattr(src, 'group_id', 0))
            if old_gid > 0 and old_gid not in old_to_new_gid:
                old_to_new_gid[old_gid] = next_gid_counter
                next_gid_counter += 1

        # 2) deep copy + add → new cid 발급 (remap 전 단계)
        self._scene.begin_group_action()
        old_to_new_cid: Dict[int, int] = {}
        clones: Dict[int, object] = {}   # new_cid → clone
        for cid in source_ids:
            src = self._scene.components.get(cid)
            if src is None:
                continue
            clone = _copy.deepcopy(src)
            clone.id = 0   # add_component 가 새로 발급
            old_gid = int(getattr(src, 'group_id', 0))
            if old_gid > 0:
                clone.group_id = old_to_new_gid[old_gid]
            # 위치는 일단 원본과 동일 — 이후 M 분기 이동으로 새 위치로 옮겨짐.
            new_id = self._scene.add_component(clone)
            old_to_new_cid[cid] = new_id
            clones[new_id] = clone

        # 3) 합체 / parent 관계 remap — 복사본 내부 cid 로만 연결
        for new_id, clone in clones.items():
            if isinstance(clone, FloorPanel):
                raw = list(getattr(clone, 'merged_wall_ids', None) or [])
                clone.merged_wall_ids = [
                    old_to_new_cid[x] for x in raw if x in old_to_new_cid
                ]
            elif isinstance(clone, StructWall):
                fp_id = getattr(clone, 'merged_fp_id', None)
                if fp_id is not None and fp_id in old_to_new_cid:
                    clone.merged_fp_id = old_to_new_cid[fp_id]
                else:
                    # 합체 대상이 복사 범위 밖이면 합체 해제 (원본 FP 와의 cross-link 방지)
                    clone.merged_fp_id = None
            # parent_id (mid_beam/mid_column 종속용) — 복사 범위 안이면 remap, 밖이면 그대로 두면
            # 원본 부모를 가리키게 됨. 일단 복사 범위 밖은 원본 유지(타당한 종속 의미).
            pid = getattr(clone, 'parent_id', None)
            if pid is not None and pid in old_to_new_cid:
                clone.parent_id = old_to_new_cid[pid]

        # 4) 비주얼 + 스냅 등록 (remap 후 sub-component 재생성)
        for new_id, clone in clones.items():
            clone.generate_sub_components()
            v, f, c = build_component_mesh(clone)
            self._viewer.add_component_visual(new_id, v, f, c)
            self._snap.add_component(new_id, clone)

        self._scene.end_group_action('group_place')

        # 5) controls.py 가 관리하는 페어/부모-자식 dict remap — 복사본들도
        #    원본과 동일한 페어/종속 구조를 갖도록.
        fp_dict = getattr(self, '_floor_pairs', None)
        if isinstance(fp_dict, dict):
            for old_cid, new_cid in old_to_new_cid.items():
                pair_old = fp_dict.get(old_cid)
                if pair_old is not None and pair_old in old_to_new_cid:
                    pair_new = old_to_new_cid[pair_old]
                    fp_dict[new_cid] = pair_new
        cp_dict = getattr(self, '_child_pairs', None)
        if isinstance(cp_dict, dict):
            for old_cid, new_cid in old_to_new_cid.items():
                sib_old = cp_dict.get(old_cid)
                if sib_old is not None and sib_old in old_to_new_cid:
                    cp_dict[new_cid] = old_to_new_cid[sib_old]
        mc_dict = getattr(self, '_module_children', None)
        cp2_dict = getattr(self, '_child_parent', None)
        if isinstance(mc_dict, dict) and isinstance(cp2_dict, dict):
            for old_cid, new_cid in old_to_new_cid.items():
                parent_old = cp2_dict.get(old_cid)
                if parent_old is not None and parent_old in old_to_new_cid:
                    parent_new = old_to_new_cid[parent_old]
                    mc_dict.setdefault(parent_new, set()).add(new_cid)
                    cp2_dict[new_cid] = parent_new
        copy_ids_set = getattr(self, '_copy_ids', None)
        if isinstance(copy_ids_set, set):
            for old_cid, new_cid in old_to_new_cid.items():
                if old_cid in copy_ids_set:
                    copy_ids_set.add(new_cid)

        # 6) M 키 이동 흐름 진입 — seed = 복사된 seed
        new_seed_id = old_to_new_cid.get(comp_id)
        new_seed = self._scene.components.get(new_seed_id) if new_seed_id else None
        if new_seed is None:
            print(f'[F5 C] seed 복사본 발급 실패 — 중단')
            return

        self._current_comp_type = new_seed.comp_type
        self._preview_rotation = int(new_seed.rotation)
        self._preview_anchor = int(new_seed.anchor)
        self._f5_pending_dims = dict(new_seed.dimensions)
        self._f5_pending_dep_meta = None
        self._f5_dep_meta = None
        # M 키 분기에서 사용할 이동 마커 — 복사본 ids 로 설정.
        self._f5_move_target_ids = list(clones.keys())
        self._f5_move_seed_id = int(new_seed_id)
        self._f5_move_group_id = int(getattr(new_seed, 'group_id', 0)) or int(new_seed_id)
        # 기존 복사 마커는 비움 — 본 분기 자체가 없어졌으므로.
        self._f5_copy_source_ids = []
        self._f5_copy_seed_id = 0
        self._f5_user_rv_override = False

        # 캔버스 PREVIEW 진입 + 포커스
        if self._f5_panel and self._f5_panel.canvas:
            cv = self._f5_panel.canvas
            cv._f5_in_preview = True
            cv._selected_id = -1
            cv._state = 'idle'
            cv.setFocus()
            cv._f5_mouse_world = (float(new_seed.position[0]),
                                  float(new_seed.position[1]))
            cv.update()

        # 좌측 3D 고스트 ON
        self._viewer.set_ghost_enabled(True)
        wx, wy = float(new_seed.position[0]), float(new_seed.position[1])
        self._f5_update_ghost_at(wx, wy)

        name = TYPE_NAMES.get(new_seed.comp_type, '')
        n_clones = len(clones)
        self._dim_panel.set_mode_text(
            f'[F5 복사+이동: {name} (복사본 #{new_seed_id}, {n_clones}개) — '
            f'클릭으로 위치 확정 / R: 회전 / V: 앵커]')
        print(f'[F5 C] deep copy {n_clones}개 발급 (원본 그대로) → '
              f'M 이동 흐름 진입 seed=#{new_seed_id}')

    def _f5_on_delete(self, group_id: int):
        """SELECTED + Delete — 다층 그룹 통째로 삭제 + 좌측 3D visual 제거.

        [2026-05-16 버그 수정] 합체 그룹까지 함께 삭제. group_id 안의 FP 의
        merged_wall_ids 의 wall 그룹, wall 의 merged_fp_id 의 FP 그룹, 그 FP 의
        다른 합체 wall 그룹들까지 BFS 로 모두 수집해서 한 번에 삭제. 이전엔
        같은 group_id 만 삭제해 합체된 벽패널이 잔존하던 버그.
        """
        from modular_3d.model.multi_floor import delete_group
        from modular_3d.model.core import Action as _Action
        from modular_3d.model import FloorPanel, StructWall

        # 합체 관계 BFS 로 추가 group_id 수집.
        gids: set = {int(group_id)}
        queue: list = [int(group_id)]
        while queue:
            gid = queue.pop()
            for c in list(self._scene.components.values()):
                if int(getattr(c, 'group_id', 0)) != gid:
                    continue
                if isinstance(c, FloorPanel):
                    for wid in getattr(c, 'merged_wall_ids', None) or []:
                        wc = self._scene.components.get(wid)
                        if wc is None:
                            continue
                        wgid = int(getattr(wc, 'group_id', 0))
                        if wgid > 0 and wgid not in gids:
                            gids.add(wgid)
                            queue.append(wgid)
                elif isinstance(c, StructWall):
                    fp_id = getattr(c, 'merged_fp_id', None)
                    if fp_id is None:
                        continue
                    fp = self._scene.components.get(fp_id)
                    if fp is None:
                        continue
                    fgid = int(getattr(fp, 'group_id', 0))
                    if fgid > 0 and fgid not in gids:
                        gids.add(fgid)
                        queue.append(fgid)
                    for wid in getattr(fp, 'merged_wall_ids', None) or []:
                        wc = self._scene.components.get(wid)
                        if wc is None:
                            continue
                        wgid = int(getattr(wc, 'group_id', 0))
                        if wgid > 0 and wgid not in gids:
                            gids.add(wgid)
                            queue.append(wgid)

        # 삭제 전 부재 데이터 보존 (Undo 복원용)
        deleted_comps = [
            c for c in self._scene.components.values()
            if int(getattr(c, 'group_id', 0)) in gids
        ]
        target_ids = [c.id for c in deleted_comps]
        # Scene 에서 제거 (각 group_id 별로)
        for g in gids:
            delete_group(self._scene, g)
        # 좌측 3D viewer + snap 에서도 제거
        for cid in target_ids:
            self._viewer.remove_component_visual(cid)
            self._snap.remove_component(cid)
        # (2026-05-12 그룹 undo) group_delete 액션 push — 합체 그룹 전체 복원용.
        self._scene.undo_stack.append(_Action(
            action_type='group_delete',
            data={'group_id': int(group_id),
                  'group_ids': sorted(gids),
                  'deleted_comps': deleted_comps},
        ))
        self._status.update_count(self._scene.component_count)
        print(f'[F5 DELETE] gids={sorted(gids)}, removed={target_ids}')
        self._auto_regen_core_slabs()
        self._refresh_2d()

