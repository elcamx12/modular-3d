"""접합부 설계 탭 컨트롤러 — main_3d 의 접합부 핸들러 18개를 분리.

[설계]
- MainWindow 가 self._joint_ctrl = JointEditController(self) 로 보유.
- self.window 로 main 참조. 공유 위젯(_viewer, _scene, _joint_panel)은
  self.window._xxx 로 접근.
- 상태 변수 9개(_joint_edit_mode, _joint_add_*, _joint_selected, _joint_om,
  _joint_am, _joint_last_design_sig)는 controller 가 자기 소유.
- 시그널 connect 와 외부 진입점 호출은 main 의 __init__ / _on_tab_changed /
  eventFilter 에서 self._joint_ctrl.method() 형태로 호출.

[2026-05-27 3-B Phase 3 분리] 자세한 의존성 분석은 클로드가 찾은 문제점.md 참조.
"""
from __future__ import annotations

import numpy as np
from PyQt5.QtWidgets import QMessageBox

from modular_3d._utils.debug import dprint


class JointEditController:
    """접합부 설계 탭의 인터랙션 핸들러를 main 에서 분리한 컨트롤러."""

    def __init__(self, window):
        self.window = window
        # 접합부 편집/추가 상태
        self._joint_edit_mode = False
        self._joint_add_mode = False
        self._joint_add_first = None
        self._joint_add_cands = []      # 첫 점 선택 후 접합 가능 후보점들
        self._joint_add_snap = None     # 현재 마우스가 스냅한 후보(x,y,z,comp)
        self._joint_selected = None
        self._joint_om = None
        self._joint_am = None
        self._joint_last_design_sig = None

    # ── 외부에서 호출되는 게터/리셋 ──────────────────────
    def is_edit_mode(self) -> bool:
        return self._joint_edit_mode

    def is_add_mode(self) -> bool:
        return self._joint_add_mode

    def clear_selection(self) -> None:
        """탭 전환 시 외부에서 호출 — 선택 해제."""
        self._joint_selected = None
        self._reset_add_progress()

    def _run_joint_edit_preview(self, force: bool = False):
        """접합부 조정 탭 진입 시 spec 만 빌드해 와이어프레임 표시.

        [정책 2026-05-13 접합부조정탭]
        구조해석 탭의 `_run_structural_analysis` 와 달리 **solve_all_cases 를
        부르지 않는다**. build_analysis_model + build_ops_model 까지만 — spec
        알갱이가 채워지면 viewer.show_ops_view 가 _draw_from_spec 로 와이어프
        레임을 그린다.

        [함정] 이미 ops 뷰가 켜져 있으면(예: 구조해석 탭에서 본 탭으로 이동) 또
        토글되어 꺼지는 일이 없도록 활성 상태일 땐 build 자체를 생략한다. 단,
        force=True 면(접합 변경 직후 등) 활성 상태여도 강제 재빌드한다 —
        오버라이드 반영 결과를 즉시 와이어프레임에 반영하기 위함.
        """
        if not self.window._scene.components:
            dprint('JOINT-EDIT', '[JOINT-EDIT] Scene 이 비어 있음 — 프리뷰 생략')
            return
        v = self.window._viewer
        # [2026-05-25 D4 수정] ops 뷰가 이미 활성이어도 _joint_om 이 아직 없으면
        # (구조해석 탭 경유로 접합 탭 첫 진입 등) 빌드해 접합 픽킹용 모델을 확보한다.
        # 그렇지 않으면 픽킹이 None 모델을 참조해 무반응.
        if (not force and self._joint_om is not None
                and hasattr(v, 'is_ops_view_active') and v.is_ops_view_active()):
            dprint('JOINT-EDIT', '[JOINT-EDIT] ops 뷰 이미 활성 — 프리뷰 재실행 생략')
            return
        try:
            from modular_3d.analysis.topology import build_analysis_model
            from modular_3d.analysis.ops_builder import build_ops_model
        except ImportError as e:
            dprint('JOINT-EDIT', f'[JOINT-EDIT] 해석 모듈 import 실패: {e}')
            return
        try:
            # 토폴로지 공유 제공자 경유 — 실패 시 기존 빌드 폴백(동작 보존).
            try:
                from modular_3d.analysis.model_provider import get_analysis_model
                am = get_analysis_model(self.window._scene)
            except Exception:
                am = build_analysis_model(self.window._scene)
            om_view = build_ops_model(am, scene=self.window._scene)
            # 접합 픽킹을 위해 마지막 빌드 결과 보관.
            self._joint_om = om_view
            self._joint_am = am
            dprint('JOINT-EDIT', '[JOINT-EDIT] spec 빌드 완료 — solve 생략. 와이어프레임 표시.')
            v.show_ops_view(om_view)
            # [Phase 5] 범례 동적 갱신 — spec 의 모든 결합 레코드에서 rule_id 수집.
            try:
                spec = getattr(om_view, 'spec', None)
                if spec is not None:
                    rule_ids = set()
                    for e in spec.iter_equal_dofs():
                        rule_ids.add(getattr(e, 'rule_id', 'legacy_auto'))
                    for r in spec.iter_rigid_links():
                        rule_ids.add(getattr(r, 'rule_id', 'legacy_auto'))
                    for d in spec.iter_diaphragms():
                        rule_ids.add(getattr(d, 'rule_id', 'legacy_auto'))
                    self.window._joint_panel.refresh_legend(rule_ids)
            except Exception as e2:
                dprint('JOINT-EDIT', f'[JOINT-EDIT] 범례 갱신 경고: {type(e2).__name__}: {e2}')
        except Exception as e:
            dprint('JOINT-EDIT', f'[JOINT-EDIT] 프리뷰 중 오류: {type(e).__name__}: {e}')
            QMessageBox.warning(
                self.window, '접합부 설계 프리뷰 실패',
                f'와이어프레임 빌드 중 오류:\n\n{type(e).__name__}: {e}',
            )

    def _on_joint_edit_mode(self, on: bool):
        """접합 편집 모드 토글. 끄면 선택·강조 정리."""
        self._joint_edit_mode = bool(on)
        self._joint_selected = None
        self.window._joint_panel.clear_selection()
        if hasattr(self.window._viewer, 'clear_ops_joint_highlight'):
            self.window._viewer.clear_ops_joint_highlight()

    def _on_joint_pick(self, pos):
        """3D 좌클릭 → 가장 가까운 컴포넌트 간 접합 선택·강조."""
        om = self._joint_om
        am = self._joint_am
        if om is None:
            return
        info = self.window._viewer.pick_ops_joint_at(pos, om, am)
        if info is None:
            self._joint_selected = None
            self.window._joint_panel.clear_selection()
            self.window._viewer.clear_ops_joint_highlight()
            return
        self._joint_selected = info
        self.window._joint_panel.show_selected_joint(info)
        self._highlight_selected_joint(info)

    def _highlight_selected_joint(self, info):
        """선택 접합 강조 — 직각접합이면 위-N1-아래 체인, 아니면 한 결합."""
        om = self._joint_om
        if info.get('right_angle') and info.get('n1_tag') is not None:
            chain = self._right_angle_chain(om, info['n1_tag'])
            self.window._viewer.highlight_ops_joint_chain(om, chain)
        else:
            self.window._viewer.highlight_ops_joint(om, info['master'], info['slave'])

    def _right_angle_chain(self, om, n1_tag):
        """중간 노드 n1_tag 를 공유하는 직각접합 결합들의 노드 체인 [위, N1, 아래].
        rule_id 무관 — 사용자추가(R10/R11) + 자동(R03) 모두 N1 공유로 수집."""
        nodes = set()
        for ed in om.spec.equal_dofs:
            if n1_tag in (ed.master, ed.slave):
                nodes.add(ed.master)
                nodes.add(ed.slave)
        ends = [t for t in nodes if t != n1_tag and t in om.node_tags]
        ends.sort(key=lambda t: om.node_tags[t][2])
        if len(ends) >= 2:
            return [ends[-1], n1_tag, ends[0]]   # 위(z큰) - N1 - 아래
        return [n1_tag] + ends

    def _find_n1_tag_by_xy(self, om, n1_xy):
        """평면 위치로 중간 노드(N1, panel_z_route) tag 찾기(다층 중 첫 매칭)."""
        if not n1_xy:
            return None
        from modular_3d.model.joint_override import MATCH_TOL_MM as _T
        for t, c in om.node_tags.items():
            if abs(c[0] - n1_xy[0]) <= _T and abs(c[1] - n1_xy[1]) <= _T:
                nr = om.spec.node(t) if om.spec is not None else None
                if nr is not None and getattr(nr, 'role', '') == 'panel_z_route':
                    return t
        return None

    def _find_add_override_by_n1(self, n1_xy):
        """N1 평면 위치로 직각 add 오버라이드 역추적. N1.xy = 위(z 큰) 점 xy."""
        if not n1_xy:
            return None
        from modular_3d.model.joint_override import MATCH_TOL_MM as _T
        for o in self.window._scene.joint_overrides:
            if getattr(o, 'kind', '') != 'add' or not getattr(o, 'right_angle', False):
                continue
            if float(o.z_a) >= float(o.z_b):
                ux, uy = o.a_xy
            else:
                ux, uy = o.b_xy
            if abs(ux - n1_xy[0]) <= _T and abs(uy - n1_xy[1]) <= _T:
                return o
        return None

    def _comp_group(self, comp_id: int) -> int:
        """컴포넌트 id → group_id (보조 식별). 없으면 0."""
        comp = self.window._scene.components.get(comp_id)
        return int(getattr(comp, 'group_id', 0) or 0) if comp is not None else 0

    def _on_joint_change(self, kind: str):
        """선택 접합에 변경(remove/pin/rigid) 적용 → 같은 xy 모든 층 → 재빌드.

        - 자동 접합: remove/rigid/pin 오버라이드(게이트)로 처리.
        - 사용자 추가 접합(USER_ADD): 그 add 오버라이드를 직접 삭제(remove)하거나
          add_dofs 를 핀/강접으로 바꾼다(추가 접합은 게이트를 안 거치므로).
        """
        info = self._joint_selected
        if info is None:
            return
        from modular_3d.model.joint_override import (
            JointOverride, PIN_DOFS, RIGID_DOFS, same_joint)
        rid0 = info.get('rule_id', '')
        is_user_add = (rid0 == 'USER_ADD'
                       or rid0.startswith('R10') or rid0.startswith('R11'))
        if is_user_add and info.get('right_angle'):
            # 직각접합 — 중간 노드로 add 오버라이드 1개를 역추적해 통째로 처리.
            ov = self._find_add_override_by_n1(info.get('n1_xy'))
            if ov is not None:
                if kind == 'remove':
                    if ov in self.window._scene.joint_overrides:
                        self.window._scene.joint_overrides.remove(ov)
                else:
                    ov.add_dofs = RIGID_DOFS if kind == 'rigid' else PIN_DOFS
        elif is_user_add:
            ax, bx = info['a_xy'], info['b_xy']
            if kind == 'remove':
                self.window._scene.joint_overrides = [
                    o for o in self.window._scene.joint_overrides
                    if not (getattr(o, 'kind', '') == 'add'
                            and same_joint(o, ax, bx))
                ]
            else:
                dofs = RIGID_DOFS if kind == 'rigid' else PIN_DOFS
                for o in self.window._scene.joint_overrides:
                    if getattr(o, 'kind', '') == 'add' and same_joint(o, ax, bx):
                        o.add_dofs = dofs
                        break
        elif info.get('right_angle') and info.get('n1_tag') is not None:
            # 자동 직각접합(R03 등) — N1 공유 체인 두 결합(위↔N1 수직, N1↔아래
            # 수평)을 통째로 게이트 처리. 한 결합만 바꾸면 ㄴ자가 반쪽만 변경됨.
            single = self.window._joint_panel.is_edit_single_layer()
            om = self._joint_om
            rid = str(rid0)
            chain = self._right_angle_chain(om, info['n1_tag'])
            for i in range(len(chain) - 1):
                u, w = chain[i], chain[i + 1]
                cu = om.node_tags.get(u)
                cw = om.node_tags.get(w)
                if cu is None or cw is None:
                    continue
                self.window._scene.set_joint_override(JointOverride(
                    kind=kind,
                    a_xy=(float(cu[0]), float(cu[1])),
                    b_xy=(float(cw[0]), float(cw[1])),
                    z_a=float(cu[2]), z_b=float(cw[2]),
                    rule_id=rid, single_layer=single,
                ))
        else:
            # rule_id 저장 — 같은 평면 위치에 겹친 다른 종류 수직 접합과 구분해
            # 이 종류에만 변경이 적용되도록. single_layer 면 이 층만 적용.
            single = self.window._joint_panel.is_edit_single_layer()
            ov = JointOverride(
                kind=kind, a_xy=info['a_xy'], b_xy=info['b_xy'],
                z_a=float(info.get('a_z', 0.0)),
                z_b=float(info.get('b_z', 0.0)),
                a_group=self._comp_group(info['a_comp']),
                b_group=self._comp_group(info['b_comp']),
                rule_id=str(info.get('rule_id', '')),
                single_layer=single,
            )
            self.window._scene.set_joint_override(ov)
        self._run_joint_edit_preview(force=True)
        # 변경 결과를 패널/강조에 반영.
        if kind == 'remove':
            self._joint_selected = None
            self.window._joint_panel.clear_selection()
            self.window._viewer.clear_ops_joint_highlight()
            self._warn_if_unstable()   # 제거로 불안정해졌는지 즉시 경고
        else:
            info['is_rigid'] = (kind == 'rigid')
            self.window._joint_panel.show_selected_joint(info)
            if info.get('right_angle'):
                # 재빌드로 N1 tag 가 바뀌므로 평면 위치로 다시 찾아 체인 강조.
                n1 = self._find_n1_tag_by_xy(self._joint_om, info.get('n1_xy'))
                if n1 is not None:
                    info['n1_tag'] = n1
                    self.window._viewer.highlight_ops_joint_chain(
                        self._joint_om,
                        self._right_angle_chain(self._joint_om, n1))
                else:
                    self.window._viewer.clear_ops_joint_highlight()
            else:
                self.window._viewer.highlight_ops_joint(
                    self._joint_om, info['master'], info['slave'])

    def _warn_if_unstable(self):
        """현재 접합 프리뷰 모델이 mechanism(불안정) 위험이면 경고(차단 없음).

        사용자 사양: 제거는 허용하되 경고만. self_check 가 고립 노드/특이강성을
        감지하면 다이얼로그 + 좌측 빨강 강조로 알린다."""
        om = self._joint_om
        if om is None:
            return
        try:
            from modular_3d.analysis.ops_builder import self_check
            res = self_check(om)
        except Exception:
            return
        if not getattr(res, 'is_critical', False):
            if hasattr(self.window._viewer, 'hide_unstable_warning'):
                self.window._viewer.hide_unstable_warning()
            return
        issues = getattr(res, 'issues', []) or []
        QMessageBox.warning(
            self.window, '구조 불안정 경고',
            '접합 제거로 구조가 불안정(mechanism)해질 수 있습니다.\n'
            '제거는 적용되었으나, 구조해석 탭에서 상세를 확인하세요.\n\n'
            + '\n'.join('· ' + str(s) for s in issues[:5]))
        nid = getattr(res, 'problem_node_ids', None)
        if nid and hasattr(self.window._viewer, 'show_unstable_warning'):
            self.window._viewer.show_unstable_warning(om, nid)

    def _on_joint_revert(self):
        """모든 접합 변경 초기화 → 자동 규칙 상태로(확인 후)."""
        if not self.window._scene.joint_overrides:
            return
        ret = QMessageBox.question(
            self.window, '모든 접합 변경 초기화',
            '이 디자인의 모든 접합 변경(제거·핀·강접·추가)을 지우고\n'
            '자동 규칙 상태로 되돌립니다. 계속할까요?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self.window._scene.clear_joint_overrides()
        self._run_joint_edit_preview(force=True)
        self._joint_selected = None
        self.window._joint_panel.clear_selection()
        self.window._viewer.clear_ops_joint_highlight()

    def _design_signature(self):
        """현재 디자인(부재 구성)의 시그니처 — 부재 id·위치·치수·회전 기반.
        접합 탭 진입 사이에 디자인이 바뀌었는지 비교하는 데 쓴다."""
        parts = []
        for cid, c in sorted(self.window._scene.components.items()):
            pos = tuple(round(float(v), 1) for v in c.position)
            dims = tuple(sorted((k, round(float(v), 1))
                                for k, v in c.dimensions.items()))
            parts.append((cid, pos, dims, int(getattr(c, 'rotation', 0))))
        return hash(tuple(parts))

    def _maybe_prompt_joint_choice(self):
        """접합 탭 진입 시 접합 오버라이드 처리 정책(3 케이스).

        (1) 불러오기 직후(+디자인 미변경): '저장본 사용 / 새 자동계산' 선택.
        (2) 직전 접합 탭 진입 이후 디자인이 바뀜: 무조건 리셋(자동 규칙).
        (3) 디자인 미변경 + 접합 변경 후 왕복: '유지 / 초기화' 선택.
        프리뷰(_run_joint_edit_preview) 전에 호출된다.
        """
        scene = self.window._scene
        cur_sig = self._design_signature()
        last_sig = getattr(self, '_joint_last_design_sig', None)
        pending = getattr(scene, '_joint_overrides_pending_choice', False)

        if pending and scene.joint_overrides:
            # 케이스 1 — 불러온 저장본.
            box = QMessageBox(self)
            box.setWindowTitle('접합 설정 불러오기')
            box.setText('저장된 접합 변경 사항이 있습니다.\n'
                        '저장된 접합을 사용할까요, 아니면 새로 자동 계산할까요?')
            box.addButton('저장된 접합 사용', QMessageBox.AcceptRole)
            new_btn = box.addButton('새로 자동 계산', QMessageBox.DestructiveRole)
            box.exec_()
            if box.clickedButton() is new_btn:
                scene.clear_joint_overrides()
            scene._joint_overrides_pending_choice = False
        elif (scene.joint_overrides and last_sig is not None
              and cur_sig != last_sig):
            # 케이스 2 — 디자인이 바뀌면 접합 변경이 위치와 안 맞을 수 있어 무조건 리셋.
            scene.clear_joint_overrides()
            QMessageBox.information(
                self.window, '접합 초기화',
                '디자인이 변경되어 기존 접합 변경을 초기화하고\n'
                '자동 규칙으로 다시 계산합니다.')
        elif (scene.joint_overrides and last_sig is not None
              and cur_sig == last_sig):
            # 케이스 3 — 디자인 그대로 + 접합 변경 후 재진입.
            ret = QMessageBox.question(
                self.window, '접합 설정',
                '저장된 접합 변경을 그대로 유지할까요?\n'
                "'아니오'를 누르면 초기화하고 자동 규칙으로 계산합니다.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret == QMessageBox.No:
                scene.clear_joint_overrides()
        self._joint_last_design_sig = cur_sig

    def _on_joint_add_mode(self, on: bool):
        """접합 추가 모드 토글. 끄면 진행 중 첫 점·후보·고스트 정리."""
        self._joint_add_mode = bool(on)
        if hasattr(self.window._viewer, 'clear_ops_joint_highlight'):
            self.window._viewer.clear_ops_joint_highlight()
        self._reset_add_progress()

    def _reset_add_progress(self):
        """접합 추가 진행 상태(첫 점·후보·고스트·선) 모두 정리."""
        self._joint_add_first = None
        self._joint_add_cands = []
        self._joint_add_snap = None
        v = self.window._viewer
        for m in ('clear_joint_ghost', 'clear_joint_candidates',
                  'clear_joint_line_ghost'):
            if hasattr(v, m):
                getattr(v, m)()

    def _validate_add(self, a: dict, b: dict, right_angle: bool = False):
        """신규 접합 두 점 검증(모서리 위 점). 반환 (가능여부, 안내문).
        right_angle=True 면 직각(ㄴ자) — 평면·높이 둘 다 다른 두 점을 허용."""
        import math
        ca = int(a.get('comp', 0) or 0)
        cb = int(b.get('comp', 0) or 0)
        if ca != 0 and cb != 0 and ca == cb:
            return False, '같은 부재의 두 점입니다. 서로 다른 부재의 점을 골라야 합니다.'
        ax, ay, az = a['point']
        bx, by, bz = b['point']
        dist = math.dist((ax, ay, az), (bx, by, bz))
        if dist > 400.0:
            return False, f'두 점이 너무 멉니다 ({dist:.0f}mm > 400mm).'
        tol = 50.0
        dx, dy, dz = abs(ax - bx), abs(ay - by), abs(az - bz)
        if right_angle:
            if not (dz > tol and (dx > tol or dy > tol)):
                return False, ('직각(ㄴ자) 접합은 평면과 높이가 모두 다른 '
                               '두 점이어야 합니다.')
        else:
            if (dx > tol) + (dy > tol) + (dz > tol) != 1:
                return False, ('두 점은 수평 또는 수직(한 축만 차이)으로만 '
                               '연결할 수 있습니다. (직각 결합은 옵션을 켜세요.)')
        return True, ''

    def _update_joint_ghost(self, pos):
        """접합 추가 모드 — 마우스 위치 미리보기.
        첫 점 전: 모서리 점 고스트. 첫 점 후: 후보에 스냅(하늘색→초록) + 선 고스트."""
        om = self._joint_om
        am = self._joint_am
        if om is None:
            return
        if self._joint_add_first is None:
            nd = self.window._viewer.pick_ops_edge_point_at(pos, om, am)
            if nd is None:
                self.window._viewer.clear_joint_ghost()
            else:
                self.window._viewer.show_joint_ghost(nd['point'], nd['snapped'])
            return
        # 첫 점 이후 — 후보 우선 스냅.
        p_first = self._joint_add_first['point']
        hit = self.window._viewer.pick_nearest_candidate(pos, self._joint_add_cands)
        if hit is not None:
            q, _idx = hit
            self._joint_add_snap = q
            self.window._viewer.show_joint_ghost((q[0], q[1], q[2]), True)
            self.window._viewer.show_joint_line_ghost(p_first, (q[0], q[1], q[2]))
            return
        self._joint_add_snap = None
        nd = self.window._viewer.pick_ops_edge_point_at(pos, om, am)
        if nd is None:
            self.window._viewer.clear_joint_ghost()
            self.window._viewer.clear_joint_line_ghost()
        else:
            self.window._viewer.show_joint_ghost(nd['point'], nd['snapped'])
            self.window._viewer.show_joint_line_ghost(p_first, nd['point'])

    def _on_joint_add_pick(self, pos):
        """접합 추가 모드 좌클릭 — 모서리 위 점 두 개로 신규 접합 생성.
        둘째 점은 스냅한 후보가 있으면 그 후보를, 없으면 모서리 점을 쓴다."""
        om = self._joint_om
        am = self._joint_am
        if om is None:
            return
        if self._joint_add_first is None:
            nd = self.window._viewer.pick_ops_edge_point_at(pos, om, am)
            if nd is None:
                return
            self._joint_add_first = nd
            self.window._viewer.show_joint_ghost(nd['point'], nd['snapped'])
            from modular_3d.analysis.joint_rules import candidate_joint_points
            cands = candidate_joint_points(
                om, nd['point'], int(nd.get('comp', 0) or 0),
                right_angle=self.window._joint_panel.is_add_right_angle())
            self._joint_add_cands = cands
            self._joint_add_snap = None
            self.window._viewer.show_joint_candidates(cands)
            self.window._joint_panel.set_add_hint('점2를 클릭하세요(하늘색 점에 스냅).')
            return
        a = self._joint_add_first
        snap = self._joint_add_snap
        if snap is not None:
            # 후보점(다른 부재 보 위 선점) — 노드 추가형. 색이 바뀐(스냅된)
            # 그대로 실제 접합도 그 자리에 노드를 만들어 결합한다.
            b = {'point': (snap[0], snap[1], snap[2]),
                 'comp': int(snap[3]) if len(snap) > 3 else 0,
                 'snapped': False}
        else:
            nd = self.window._viewer.pick_ops_edge_point_at(pos, om, am)
            if nd is None:
                return
            b = nd
        # 끝점 종류 — 선 위 점(주황·미스냅)이면 그 자리에 보 분할로 노드 생성,
        # 꼭지점(초록·스냅)이면 기존 노드 스냅. 사용자가 본 색과 실제 접합 일치.
        a_on_edge = not bool(a.get('snapped', False))
        b_on_edge = not bool(b.get('snapped', False))
        right = self.window._joint_panel.is_add_right_angle()
        single = self.window._joint_panel.is_add_single_layer()
        ok, msg = self._validate_add(a, b, right_angle=right)
        if not ok:
            QMessageBox.information(self.window, '접합 추가 불가', msg)
            self._reset_add_progress()
            self.window._joint_panel.set_add_hint('점1을 다시 클릭하세요.')
            return
        from modular_3d.model.joint_override import (
            JointOverride, PIN_DOFS, RIGID_DOFS)
        rigid = self.window._joint_panel.is_add_rigid()
        ax, ay, az = a['point']
        bx, by, bz = b['point']
        ov = JointOverride(
            kind='add', a_xy=(ax, ay), b_xy=(bx, by),
            z_a=az, z_b=bz,
            a_group=self._comp_group(int(a.get('comp', 0) or 0)),
            b_group=self._comp_group(int(b.get('comp', 0) or 0)),
            add_dofs=(RIGID_DOFS if rigid else PIN_DOFS),
            single_layer=single, right_angle=right,
            a_on_edge=a_on_edge, b_on_edge=b_on_edge,
        )
        self.window._scene.set_joint_override(ov)
        self._run_joint_edit_preview(force=True)
        self._reset_add_progress()
        self.window._joint_panel.set_add_hint('추가됨. 다음 점1을 클릭하세요.')
