"""controls.py 의 F6 (구조해석 도크) 관련 메소드 분리.

[설계]
- F6Mixin 은 Controller 가 inherit 한다 (Mixin 패턴).
- Controller 의 self.* 상태 (self._scene, self._viewer, self._dim_panel 등) 를 그대로 사용.
- 본 모듈 자체는 인스턴스화하지 않는다.

[원칙]
- 본 파일에는 F6 도크 패널 + 해석 결과 시각화 로직만 둔다.
"""
from __future__ import annotations

import numpy as np

# [버그 수정 2026-05-08] Mixin 분리 시 누락된 import 복구.
# Mixin 메소드의 모듈 스코프에서 AppState / TYPE_NAMES 가 안 보여 F5/F6 작동 시 NameError.
from modular_3d.model import AppState, TYPE_NAMES
from modular_3d._utils.debug import dprint


# ── Phase 9 — 부재 클릭 고정 정보창 슬롯 시스템 (설계서_부재호버.md) ──
# 5 색 팔레트. 응력비/트리/스냅/코어 등 기존 색과 겹치지 않는 색만.
# 후보: 시안 / 마젠타 / 라임 / 티얼 / 다크오렌지.
_PIN_PALETTE = [
    (0.00, 0.78, 0.78, 1.0),   # 시안 #00C8C8
    (0.82, 0.00, 0.75, 1.0),   # 마젠타 #D000C0
    (0.66, 0.82, 0.00, 1.0),   # 라임 #A8D000
    (0.12, 0.72, 0.63, 1.0),   # 티얼 #1FB7A0
    (0.85, 0.48, 0.00, 1.0),   # 다크오렌지 #D87A00
]
_PIN_MAX_SLOTS = 5


class F6Mixin:
    """F6 해석 모드 메소드 모음 — Controller 가 inherit."""

    def _open_alignment_view(self):
        """F5 도킹 패널 토글. F6 ↔ F5 배타 관리(2-2).

        [버그 패치 — 사용자 보고 #2·3·4]
        - F6→F5 시 부재 메쉬 visible 명시 + selection_box·snap·ghost 정리
        - F5 도크에 포커스 있어도 F5/F6 키가 작동하도록 라우팅(AlignmentCanvas
          에서 ctrl 직접 호출).
        """
        f5_dock = getattr(self, '_f5_dock', None)
        if f5_dock is None:
            dprint('F5', '[F5] 도킹 패널 미등록 — main_3d.set_f5_dock 호출 누락?')
            return

        if f5_dock.isVisible():
            # 이미 켜져 있음 → 끄기
            f5_dock.setVisible(False)
            # 진행 중인 배치/종속/이동 상태 정리
            self._f5_pending_placement = False
            self._f5_pending_dims = None
            self._f5_dep_meta = None
            self._f5_move_group_id = 0
            if self._f5_panel is not None and self._f5_panel.canvas is not None:
                self._f5_panel.canvas._f5_in_preview = False
                self._f5_panel.canvas._f5_pending_dep_type = None
                self._f5_panel.canvas._state = 'idle'
                self._f5_panel.canvas.reset_state()
            self._viewer.set_ghost_enabled(False)  # 게이트 OFF
            self._viewer.clear_ghost()
            self._viewer.canvas.native.setFocus()
            dprint('F5', '[F5] 도킹 닫힘')
            return

        # F5 진입: F6 도킹 + ops 시각화 + 선택 박스 모두 정리 (2-3·8-7·버그 패치)
        if self._analysis_dock is not None and self._analysis_dock.isVisible():
            self._analysis_dock.setVisible(False)
        if self._viewer.is_ops_view_active():
            self._viewer.hide_ops_view()
            self._viewer.hide_unstable_warning()
            self._viewer.hide_deformed_shape()
        # 선택 강조 / 스냅 마커 / 고스트 / 부재 highlight 모두 정리 (#3)
        self._viewer.hide_selection_box()
        if hasattr(self._viewer, 'clear_member_highlight'):
            try:
                self._viewer.clear_member_highlight()
            except Exception:
                pass
        self._viewer.hide_snap_marker()
        self._viewer.set_ghost_enabled(False)  # 게이트 기본 OFF (PREVIEW 진입 시 ON)
        self._viewer.clear_ghost()
        # 컴포넌트 메쉬 visible 명시 (#2 — F6 디밍 후 안 보일 수 있음 방지)
        for cid, mesh in getattr(self._viewer, '_component_visuals', {}).items():
            try:
                mesh.visible = True
            except Exception:
                pass
        self._viewer.canvas.update()

        f5_dock.setVisible(True)
        # 캔버스에 포커스 — 키 입력 즉시 받게
        if self._f5_panel is not None:
            self._f5_panel.request_focus()
        # 메인 3D 컨트롤러 자체 상태도 IDLE로 리셋 (3D 측 잔존 PREVIEW/MOVING 방지)
        if self._state in (AppState.PLACEMENT_PREVIEW, AppState.MOVING,
                           AppState.DIMENSION_INPUT):
            self._set_state(AppState.IDLE)
        dprint('F5', '[F5] 도킹 열림')

    def _run_structural_analysis(self):
        """F6: OpenSeesPy 기반 구조해석 (Phase 1 = 모델 빌드 + 좌측 OpenSees 뷰).

        Phase 2 이후 수직(D+L) + 횡력(Ex/Ey/Wx/Wy) 해석 추가 예정.

        - Scene 이 비어 있으면 알림 후 종료
        - 성공 시 (ops_model, analysis_model) 을 self._last_ops_analysis 에 저장
        - 좌측 뷰: ops 모드(노드/엘리먼트) 로 전환 → 다시 F6 누르면 메쉬 모드 복귀
        """
        # F6 진입 시 F5 도킹은 강제 OFF (2-2 배타).
        # 사용자 보고 #4: Scene 비어있을 때도 F5는 닫혀야 토글이 정상 작동.
        f5_dock = getattr(self, '_f5_dock', None)
        f5_was_open = f5_dock is not None and f5_dock.isVisible()
        if f5_was_open:
            f5_dock.setVisible(False)
            dprint('ANALYSIS', '[ANALYSIS] F6 진입 — F5 도킹 닫음')
        # F6에서는 고스트가 어떤 경로로도 나오면 안 됨 — 게이트 강제 OFF
        self._viewer.set_ghost_enabled(False)
        self._viewer.clear_ghost()

        if not self._scene.components:
            dprint('ANALYSIS', '[ANALYSIS] Scene 이 비어 있음 — 해석 생략')
            # F5 만 닫고 종료(F6 토글 아님)
            return

        # 토글: ops 뷰가 이미 활성이면 비활성화 (변형 형상도 같이 끔)
        if self._viewer.is_ops_view_active():
            self._viewer.hide_ops_view()
            self._viewer.hide_deformed_shape()
            if getattr(self, '_deformed_check', None) is not None:
                self._deformed_check.blockSignals(True)
                self._deformed_check.setChecked(False)
                self._deformed_check.blockSignals(False)
            dprint('ANALYSIS', '[ANALYSIS] ops 뷰 OFF (메쉬 뷰 복귀)')
            self._viewer.canvas.native.setFocus()
            return

        try:
            from modular_3d.analysis.topology import build_analysis_model
            from modular_3d.analysis.ops_builder import build_ops_model, self_check
            from modular_3d.analysis.ops_solver import solve_all_cases
        except ImportError as e:
            dprint('ANALYSIS', f'[ANALYSIS] OpenSees 해석 모듈 import 실패: {e}')
            return

        dprint('ANALYSIS', '[ANALYSIS] F6 → OpenSees 하중조합 자동 해석 시작')
        try:
            # 토폴로지 공유 제공자 경유 — 해석·단면·접합뷰·공정이 같은 am 을
            # 재사용. 제공자 실패 시 기존 직접 빌드로 폴백(동작 보존).
            try:
                from modular_3d.analysis.model_provider import get_analysis_model
                am = get_analysis_model(self._scene)
            except Exception:
                am = build_analysis_model(self._scene)
            # 시각화용으로 한 번 빌드 (마지막 케이스의 ops 상태가 남음 — 시각화엔 영향 없음)
            om_view = build_ops_model(am, scene=self._scene)
            print(om_view.summary())
            check_result = self_check(om_view)
            # SelfCheckResult 인터페이스: .issues (List[str]), .problem_node_ids, .is_critical
            issues = check_result.issues
            critical = check_result.is_critical
            if issues:
                if critical:
                    dprint('ANALYSIS', '[ANALYSIS][중대 경고] mechanism 위험 — 좌측 빨강 강조 부재 점검:')
                else:
                    dprint('ANALYSIS', '[ANALYSIS][경고] self_check 이상 항목:')
                for s in issues:
                    print('  -', s)
            else:
                dprint('ANALYSIS', '[ANALYSIS] self_check 통과')

            # KDS 하중조합 일괄 해석 (1.4D, D+L, 지진/풍 ±조합, 전도방지)
            # [2026-06-02 성능] 위에서 get_analysis_model 로 확보한 am 을 그대로
            # 넘겨 토폴로지(해석모델) 중복 빌드를 1회 줄인다. 미전달 시 solve_all_cases
            # 가 build_analysis_model 을 다시 호출해(캐시 우회) 같은 토폴로지를 또 짓는다.
            results = solve_all_cases(self._scene, prebuilt_am=am)
            for name, res in results.items():
                print(res.summary())

            # 좌측 뷰 전환 (시각화는 om_view 사용)
            self._viewer.show_ops_view(om_view)
            res_dl = results.get('D+L')
            self._last_ops_analysis = (om_view, am, res_dl, results)

            # mechanism 위험 부재를 빨강 강조
            if critical and check_result.problem_node_ids:
                self._viewer.show_unstable_warning(
                    om_view, check_result.problem_node_ids)
            else:
                self._viewer.hide_unstable_warning()

            # 우측 패널 — 5 케이스 요약 + D+L 기준 부재력
            # [정책 안내] 시각 슬래브 두께(150mm) 와 해석 슬래브 두께(200mm) 가
            # 다르다. 사용자가 자중 보고서를 봤을 때 "왜 150mm 인데 자중이 크지?"
            # 라고 헷갈리지 않도록 첫 줄에 명시.
            from modular_3d.카탈로그.geometry import (
                SLAB_THICKNESS_MM as _SLAB_T,
                SLAB_THICKNESS_MODEL_MM as _SLAB_VIS_T,
            )
            report_lines = [
                f'[해석 정책] 슬래브 두께 = {_SLAB_T:.0f} mm '
                f'(화면 표시 = {_SLAB_VIS_T:.0f} mm — 사용자 사양으로 시각/해석 분리)',
                '',
                om_view.summary(),
                '',
            ]
            # 중대 경고는 맨 위에 눈에 띄게
            if critical:
                report_lines.insert(0, '!!!!! 중대 경고: mechanism 위험 — 좌측 빨강 강조 부재 점검 !!!!!')
                report_lines.insert(1, '')
            for name, res in results.items():
                report_lines.append(res.summary())
                if res.equilibrium_ratio < 0.01:
                    report_lines.append(
                        f'  >> 평형 통과 ({res.equilibrium_ratio*100:.3f}%)')
                else:
                    report_lines.append(
                        f'  >> !! 평형 실패 ({res.equilibrium_ratio*100:.3f}%) !!')
                report_lines.append('')
            if issues:
                header = '[중대 경고] mechanism 위험:' if critical else 'self_check 이상:'
                report_lines.append(header)
                report_lines.extend(f'  - {s}' for s in issues)
            self._populate_ops_panel(
                '\n'.join(report_lines), om_view, am, res_dl, results,
            )
            # 케이스 콤보박스가 D+L 로 리셋되므로 controls 측 캐시도 동기화
            self._current_case_name = 'D+L'
            # 이전에 선택한 컨투어 종류가 '끄기' 아니면 새 모델로 즉시 재적용
            if getattr(self, '_current_contour_kind', '끄기') != '끄기':
                self._refresh_contour()
            # 변형 형상 체크가 켜져 있다면 마찬가지로 재적용
            if (getattr(self, '_deformed_check', None) is not None
                    and self._deformed_check.isChecked()):
                self._refresh_deformed_view()
            dprint('ANALYSIS', '[ANALYSIS] 좌측 ops 뷰 ON — F6 다시 눌러 OFF')
        except Exception as e:
            import traceback
            dprint('ANALYSIS', f'[ANALYSIS] 실행 중 오류: {e}')
            traceback.print_exc()
        finally:
            self._viewer.canvas.native.setFocus()
        dprint('ANALYSIS', '[ANALYSIS] F6 → 종료')

    def _populate_ops_panel(self, report_text, ops_model, analysis_model,
                            ops_results=None, all_results=None):
        """우측 도킹 패널에 ops 모델 요약 + 부재력 표시 + 케이스 토글.

        2026-05-08: 물량산출 자동 실행 + 패널 주입 추가.
        D+L 결과(ops_results) 가 있으면 design_all_policies + build_all_reports
        호출 후 패널의 물량산출 탭과 controller 의 색상 시각화에 자료를 넘긴다.
        """
        if self._analysis_panel is None:
            return
        comp_labels = {}
        for comp_id, comp in self._scene.components.items():
            type_name = TYPE_NAMES.get(comp.comp_type, str(comp.comp_type))
            comp_labels[comp_id] = f"{type_name} #{comp_id}"
        # [P6b] 단면 설계 결과가 있으면 -1-1 변형 라벨을 트리에 주입(없으면 {} → 기존 A-1).
        try:
            slabels = {}
            sres = getattr(self, '_section_design_result', None)
            if sres is not None:
                from modular_3d.analysis.section_converge import derive_section_types
                _types, slabels = derive_section_types(sres, self._scene)
            if hasattr(self._analysis_panel, 'set_section_type_labels'):
                self._analysis_panel.set_section_type_labels(slabels or {})
        except Exception as e:
            dprint('QTY', f'[QTY] 단면 변형 라벨 주입 실패: {type(e).__name__}: {e}')
        self._analysis_panel.populate_ops(
            report_text, ops_model, analysis_model, comp_labels,
            ops_results, all_results, scene=self._scene,
        )

        # ── 물량산출 자동 실행 (Phase 6) ───────────────────
        # D+L 결과가 있을 때만 — 횡력은 아직 미사용 (사용자 결정)
        dl_res = ops_results
        if all_results and 'D+L' in all_results:
            dl_res = all_results['D+L']
        if dl_res is not None and analysis_model is not None:
            try:
                self._run_quantity_takeoff(analysis_model, dl_res)
            except Exception as e:
                import traceback
                dprint('QTY', f'[QTY] 물량산출 실패: {e}')
                traceback.print_exc()

        if self._analysis_dock is not None:
            self._analysis_dock.setVisible(True)

    def set_section_design_result(self, result):
        """[P5b] 단면 설계 탭의 확정 수렴 결과(ConvergeResult)를 보관.

        설정되면 이후 물량 산출이 이 결과를 *단일 출처* 로 사용(정책 1/2/3종 재산정
        대신). None 이면 기존 design_all_policies 경로로 폴백.
        """
        self._section_design_result = result

    def _quantity_from_section_design(self):
        """[P5b] 단면 설계 결과가 있으면 (design_results, am) 반환, 없으면 None.

        수렴 결과를 단일 DesignResult 로 변환해 *세 정책 키에 동일* 주입 →
        물량 패널(1/2/3종 라디오)을 건드리지 않고 단일 출처로 통일. 어느 정책을
        봐도 같은 단면 설계 결과가 나온다.
        """
        result = getattr(self, '_section_design_result', None)
        if result is None or getattr(result, 'am', None) is None:
            return None
        from modular_3d.analysis.section_converge import (
            converge_result_to_design_result)
        from modular_3d.analysis.section_design import ALL_POLICIES
        dr = converge_result_to_design_result(result)
        if not dr.groups:
            return None
        return {p: dr for p in ALL_POLICIES}, result.am

    def _run_quantity_takeoff(self, am, dl_results):
        """초기 진입 — D+L 결과로 단면 산정 + 물량 집계 → 패널·뷰어 주입.

        (2026-05-19 작업 5) 첫 진입 후 콤보박스에서 다른 케이스를 골라도
        _run_quantity_takeoff_for_case 가 재산정해 응력비가 갱신된다.
        """
        from modular_3d.analysis.section_design import (
            design_all_policies, ALL_POLICIES,
        )
        from modular_3d.analysis.quantity_takeoff import build_all_reports

        # [P5b] 단면 설계 결과가 있으면 단일 출처로 사용, 없으면 기존 정책 산정.
        sd = self._quantity_from_section_design()
        if sd is not None:
            design_results, am = sd
            dprint('QTY', '[QTY] 단면 설계 결과를 단일 출처로 물량 산출(초기)')
        else:
            design_results = design_all_policies(am, dl_results)
        reports = build_all_reports(self._scene, am, design_results)

        # 정책별 member_to_group 캐시 (패널 표 채우기용)
        m2g_by_policy = {p: design_results[p].member_to_group
                         for p in ALL_POLICIES}

        # 패널에 주입
        if hasattr(self._analysis_panel, 'populate_quantity_full'):
            self._analysis_panel.populate_quantity_full(reports, m2g_by_policy)

        # 컨트롤러에 reports 캐시 (3D 색상 시각화용)
        self.set_quantity_reports(reports)

        # Phase 6: 운송탭에 design_results 주입 + 컨트롤러 보관 (메인 운송 탭 진입 시 재사용)
        self._design_results = design_results
        if hasattr(self._analysis_panel, 'populate_transport'):
            policy = getattr(self._analysis_panel, '_current_policy', '3종')
            self._analysis_panel.populate_transport(design_results, policy)

        # 콘솔 요약
        dprint('QTY', '[QTY] 물량산출 자동 실행 완료')
        for policy, rep in reports.items():
            total_ton = rep.steel_items[-1].total_weight_ton if rep.steel_items else 0.0
            sec_summary = ', '.join(f'{g}:{s.name}'
                                     for g, s in rep.sections_by_group.items())
            print(f'  {policy}: {sec_summary}  / 강재 {total_ton:.3f} ton')
        slab = next(iter(reports.values())).slab
        print(f'  슬래브: {slab.total_volume_m3:.3f} m³, 철근 {slab.rebar_weight_ton:.3f} ton')

    def _run_quantity_takeoff_for_case(self, am, all_results, case_name: str):
        """(2026-05-19 작업 5) 선택 케이스에 대해 단면 산정 + 물량 집계 재실행.

        - 'ENVELOPE' : 전체 하중조합 단면력의 절댓값 최대(envelope) 로 demand.
        - 그 외 : 해당 단일 조합 단면력으로 demand.

        (2026-05-19 기둥 층구간 분할) 패널이 K>=2 를 들고 있으면 정책별
        DesignResult 를 split_columns_by_floor 로 후처리.
        """
        from modular_3d.analysis.section_design import (
            design_all_policies, design_all_policies_envelope, ALL_POLICIES,
            extract_envelope_demands, extract_all_demands,
            split_columns_by_floor,
        )
        from modular_3d.analysis.quantity_takeoff import build_all_reports
        from modular_3d.카탈로그.geometry import FLOOR_HEIGHT

        # [P5b] 단면 설계 결과가 있으면 케이스/K 분할 무시하고 단일 출처로.
        sd = self._quantity_from_section_design()
        if sd is not None:
            design_results, sam = sd
            reports = build_all_reports(self._scene, sam, design_results)
            m2g = {p: design_results[p].member_to_group for p in ALL_POLICIES}
            if hasattr(self._analysis_panel, 'populate_quantity_full'):
                self._analysis_panel.populate_quantity_full(reports, m2g)
            self.set_quantity_reports(reports)
            self._design_results = design_results
            if hasattr(self._analysis_panel, 'populate_transport'):
                policy = getattr(self._analysis_panel, '_current_policy', '3종')
                self._analysis_panel.populate_transport(design_results, policy)
            dprint('QTY', f'[QTY] 케이스 {case_name} — 단면 설계 단일 출처 사용')
            return

        if case_name == 'ENVELOPE':
            design_results = design_all_policies_envelope(am, all_results)
            demands = extract_envelope_demands(am, all_results)
        else:
            res = all_results.get(case_name)
            if res is None:
                return
            design_results = design_all_policies(am, res)
            demands = extract_all_demands(am, res)

        # 기둥 층구간 분할 — 사용자 입력 K 가 1 보다 크면 적용.
        n_seg = 1
        if hasattr(self._analysis_panel, 'get_column_segments'):
            try:
                n_seg = int(self._analysis_panel.get_column_segments())
            except Exception:
                n_seg = 1
        if n_seg >= 2:
            # 부재별 층 번호 계산 — 양단 노드 z 평균 / FLOOR_HEIGHT.
            floor_of_mid = {}
            for mid, m in am.members.items():
                n1 = am.nodes.get(m.n1); n2 = am.nodes.get(m.n2)
                if n1 is None or n2 is None:
                    continue
                avg_z = (float(n1.coord[2]) + float(n2.coord[2])) / 2.0
                floor_of_mid[mid] = max(1, int((avg_z + 1.0) / FLOOR_HEIGHT) + 1)
            design_results = {
                p: split_columns_by_floor(am, demands, dr,
                                            floor_of_mid, n_seg)
                for p, dr in design_results.items()
            }

        reports = build_all_reports(self._scene, am, design_results)
        m2g_by_policy = {p: design_results[p].member_to_group
                         for p in ALL_POLICIES}
        if hasattr(self._analysis_panel, 'populate_quantity_full'):
            self._analysis_panel.populate_quantity_full(reports, m2g_by_policy)
        self.set_quantity_reports(reports)
        # Phase 6: 운송탭에 design_results 주입 + 컨트롤러 보관.
        self._design_results = design_results
        if hasattr(self._analysis_panel, 'populate_transport'):
            policy = getattr(self._analysis_panel, '_current_policy', '3종')
            self._analysis_panel.populate_transport(design_results, policy)
        dprint('QTY',
                f'[QTY] 케이스 {case_name} (K={n_seg}) 물량/응력비 재산정 완료')

    def _on_column_segments_changed(self, n_seg: int):
        """(2026-05-19) 기둥 층구간 분할 '적용' 버튼 → 현재 케이스 재산정."""
        from PyQt5.QtCore import Qt as _Qt
        print(f'[QTY] 기둥 층구간 적용 클릭 — K={n_seg}')
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None or len(last_ops) < 4:
            print('[QTY] _last_ops_analysis 없음 — 먼저 F6 해석을 한 번 실행해야 함')
            return
        am = last_ops[1]
        all_results = last_ops[3]
        if not isinstance(all_results, dict) or am is None:
            print('[QTY] all_results 또는 am 누락')
            return
        case_name = getattr(self, '_current_case_name', 'D+L') or 'D+L'
        # 패널이 보고 있는 케이스(콤보 itemData=내부키) 우선 사용.
        try:
            combo = self._analysis_panel._case_combo
            sel = combo.itemData(combo.currentIndex(), _Qt.UserRole)
            if isinstance(sel, str) and sel:
                case_name = sel
        except Exception:
            pass
        print(f'[QTY] 재산정 시작 — case={case_name}, K={n_seg}')
        try:
            self._run_quantity_takeoff_for_case(am, all_results, case_name)
            print(f'[QTY] 재산정 완료')
        except Exception as e:
            from modular_3d._utils.debug import log_error
            log_error(f'기둥 층구간 재산정 실패: {e}', cat='controls_f6', exc=True)

    def attach_deformed_controls(self, checkbox, slider, label):
        """main_3d 가 만든 변형 형상 컨트롤 위젯들을 컨트롤러에 연결 (Phase 4-B)."""
        self._deformed_check = checkbox
        self._deformed_slider = slider
        self._deformed_label = label
        checkbox.toggled.connect(self._on_deformed_toggled)
        slider.valueChanged.connect(self._on_deformed_scale_changed)
        self._current_case_name = 'D+L'

    def _active_case_results(self):
        """현재 콤보박스에서 선택된 케이스의 OpsResults 반환 (없으면 None)."""
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None or len(last_ops) < 4:
            return None
        all_results = last_ops[3]
        case = getattr(self, '_current_case_name', 'D+L')
        return all_results.get(case) if isinstance(all_results, dict) else None

    def _on_panel_case_changed(self, case_name: str):
        """패널 케이스 콤보박스 변경 → 좌측 변형 + 컨투어 + 물량 재산정.

        (2026-05-19 작업 5) 'ENVELOPE' = 지배조합 = 전체 하중조합 envelope demand.
        D+L/Ex/Ey/Wx/Wy = 그 단일 케이스 demand. 케이스 바뀌면 물량 보고서를
        다시 산정해 응력비 트리·표 색상이 즉시 갱신된다.

        Phase 15 — 떠 있는 부재 정보창도 새 케이스 데이터로 재구성.
        """
        # ENVELOPE 는 변형형상/컨투어용으로는 D+L 결과를 쓰지만 _current_case_name
        # 은 'ENVELOPE' 그대로 유지 — _active_case_results 가 D+L 로 폴백.
        self._current_case_name = (
            'D+L' if case_name == 'ENVELOPE' else case_name)
        dprint('ANALYSIS', f'[ANALYSIS] 케이스 전환 → {case_name}')

        # 물량 보고서 재산정 — 단면 산정/응력비가 케이스에 따라 달라진다.
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is not None and len(last_ops) >= 4:
            am = last_ops[1]
            all_results = last_ops[3]
            if isinstance(all_results, dict) and am is not None:
                try:
                    self._run_quantity_takeoff_for_case(
                        am, all_results, case_name)
                except Exception as e:
                    import traceback
                    dprint('QTY', f'[QTY] 케이스 전환 물량 재산정 실패: {e}')
                    traceback.print_exc()

        if (getattr(self, '_deformed_check', None) is not None
                and self._deformed_check.isChecked()):
            self._refresh_deformed_view()
        if getattr(self, '_current_contour_kind', '끄기') != '끄기':
            self._refresh_contour()
        self._refresh_all_pinned_popups()

    def _refresh_all_pinned_popups(self):
        """Phase 15 — 떠 있는 클릭 고정 정보창의 본문을 현재 케이스로 갱신."""
        for s in self._pinned_slots():
            if s is None:
                continue
            popup = s.get('popup')
            mid = s.get('mid')
            if popup is None or mid is None:
                continue
            self._refresh_popup_content(popup, int(mid))

    def _on_deformed_toggled(self, checked: bool):
        if checked:
            self._refresh_deformed_view()
        else:
            self._viewer.hide_deformed_shape()

    def _on_deformed_scale_changed(self, value: int):
        if self._deformed_label is not None:
            self._deformed_label.setText(f"×{value}")
        if (getattr(self, '_deformed_check', None) is not None
                and self._deformed_check.isChecked()):
            self._refresh_deformed_view()

    def _refresh_deformed_view(self):
        """현재 케이스 + 슬라이더 배율로 좌측 변형 형상 그리기."""
        # ops 뷰가 꺼져 있으면 변형도 그리지 않음 (메쉬 위에 dead 레이어 방지)
        if not self._viewer.is_ops_view_active():
            self._viewer.hide_deformed_shape()
            return
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            return
        om = last_ops[0]
        res = self._active_case_results()
        if res is None:
            return
        scale = float(self._deformed_slider.value()) if self._deformed_slider else 100.0
        self._viewer.update_deformed_shape(om, res, scale)

    # ── 컨투어 (Phase 4-C) ──────────────────────────────────

    # SHS 200×200×8 + SS275 명목강도 — 카탈로그에서 정식 계산값 가져옴.
    # 옛 매직넘버 (275 × 6144, 275 × 37.39e6/100) 제거 후 단일 진실 원천 사용.
    from modular_3d.카탈로그.steel_sections import (
        SECTION_PN_N as _SECTION_PN_N,
        SECTION_MN_N_MM as _SECTION_MN_N_MM,
    )

    def _on_panel_contour_changed(self, kind: str):
        self._current_contour_kind = kind
        self._refresh_contour()

    def _refresh_contour(self):
        # ops 뷰가 꺼져 있으면 컨투어도 그리지 않음 (메쉬 위에 dead lines 방지)
        if not self._viewer.is_ops_view_active():
            return
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            return
        om = last_ops[0]
        res = self._active_case_results()
        kind = getattr(self, '_current_contour_kind', '끄기')
        if kind == '끄기' or res is None:
            self._viewer.hide_member_contour(om)
            return

        ratios: dict = {}
        if kind == '축력비 N/Pn':
            for mid, mf in res.member_forces.items():
                ratios[mid] = mf.N_max_abs / self._SECTION_PN_N
        elif kind == '휨모멘트비 M/Mn':
            for mid, mf in res.member_forces.items():
                ratios[mid] = mf.M_max_abs / self._SECTION_MN_N_MM
        elif kind == '조합응력비 N/Pn+M/Mn':
            for mid, mf in res.member_forces.items():
                r = (mf.N_max_abs / self._SECTION_PN_N
                     + mf.M_max_abs / self._SECTION_MN_N_MM)
                ratios[mid] = r
        elif kind.startswith('변위 |u|'):
            # 양 끝 노드 변위 평균 → 최대 변위 기준으로 정규화
            am = last_ops[1]
            disp_norms: dict = {}
            max_norm = 1e-9
            for mid, m in am.members.items():
                d1 = res.node_disps.get(m.n1)
                d2 = res.node_disps.get(m.n2)
                if d1 is None or d2 is None:
                    continue
                avg = 0.5 * (np.linalg.norm(d1[:3]) + np.linalg.norm(d2[:3]))
                disp_norms[mid] = float(avg)
                if avg > max_norm:
                    max_norm = avg
            for mid, val in disp_norms.items():
                ratios[mid] = val / max_norm
        else:
            self._viewer.hide_member_contour(om)
            return
        self._viewer.show_member_contour(om, ratios)

    def _update_ops_hover(self, pos):
        """Phase 2~3 (설계서_부재호버.md) — F6 ops 뷰에서 마우스 호버 부재 강조 + 정보창.

        ops 뷰가 active 이고 해석 결과가 있을 때만 동작. 호버한 부재가
        있으면 회색 강조 + 마우스 옆에 정보창. 임계값 안에 부재가 없으면
        강조 + 정보창 모두 제거.
        """
        if not self._viewer.is_ops_view_active():
            self._viewer.clear_ops_member_hover()
            self._hide_hover_popup()
            return
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            self._viewer.clear_ops_member_hover()
            self._hide_hover_popup()
            return
        om = last_ops[0]; am = last_ops[1]
        mid = self._viewer.pick_ops_member_at(pos, om, am)
        if mid is None:
            self._viewer.clear_ops_member_hover()
            self._hide_hover_popup()
            return
        m = am.members.get(mid)
        if m is None:
            self._viewer.clear_ops_member_hover()
            self._hide_hover_popup()
            return
        # Phase 13 — 호버 색 = 다음 클릭 시 받게 될 슬롯 색 미리보기.
        next_color = self._next_pin_color_preview()
        self._viewer.highlight_ops_member_hover(
            om, m.n1, m.n2, color=tuple(next_color))
        self._show_hover_popup_for(mid, pos)

    # ── Phase 3 정보창 골격 ───────────────────────────────────
    def _ensure_hover_popup(self):
        """첫 호버 시 정보창 위젯 lazy 생성. 부모는 캔버스 native widget."""
        if getattr(self, '_hover_popup', None) is not None:
            return
        try:
            from modular_3d.ui.member_info_popup import MemberInfoPopup
        except Exception:
            self._hover_popup = None
            return
        parent_widget = self._viewer.get_native_widget()
        if parent_widget is None:
            self._hover_popup = None
            return
        self._hover_popup = MemberInfoPopup(parent_widget)

    def _show_hover_popup_for(self, mid: int, pos):
        """호버 정보창 표시 — 마우스 옆 이동 + Phase 4 본문 갱신.

        Phase 13 연계: 호버 정보창 외곽선도 다음 배정 슬롯 색으로 그려서
        호버 강조선(같은 색)과 시각 연결.
        """
        self._ensure_hover_popup()
        popup = getattr(self, '_hover_popup', None)
        if popup is None:
            return
        popup.set_border_color(self._next_pin_color_preview())
        self._refresh_popup_content(popup, mid)
        popup.move_to_mouse(pos)
        popup.show()
        popup.raise_()

    def _refresh_popup_content(self, popup, mid: int):
        """Phase 4 — 부재 mid 정보로 정보창 본문 갱신.

        패널의 lookup API (`get_member_full_label` 등) + 현재 케이스
        결과(`MemberForce.f_i / f_j`) 를 사용한다.
        """
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            return
        am = last_ops[1]
        m = am.members.get(int(mid))
        if m is None:
            return
        L = am.get_member_length(int(mid))
        panel = self._analysis_panel
        if panel is None:
            full_label = f"{m.role} #{mid}"
            section_name = ''; ratio = None; policy = '1종'
        else:
            full_label = panel.get_member_full_label(mid)
            section_name = panel.get_member_section_name(mid)
            ratio = panel.get_member_ratio(mid)
            policy = panel.get_current_policy()
        # 현재 표시 케이스의 양단 단면력 — 패널이 들고 있는 것이 정답.
        f_i = None; f_j = None
        res = None
        if panel is not None:
            res = getattr(panel, '_ops_results', None) \
                  or panel._all_results.get('D+L')
        if res is None:
            res = getattr(self, '_ops_results', None)
        stations = None
        mf = None
        if res is not None:
            mf = res.member_forces.get(int(mid))
            if mf is not None:
                f_i = mf.f_i; f_j = mf.f_j
            # 분포 station (N, Vy, Vz, T, My, Mz) × n
            stations = res.member_stations.get(int(mid)) \
                if hasattr(res, 'member_stations') else None
        # 사용자 보고: 0 부재 일관성 — 진단 메시지를 정보창에 명시.
        # OpenSees 미등록(강체 슬레이브 등) / 단면력 거의 0 인 경우 표시.
        diag = None
        if mf is None:
            diag = "OpenSees 미등록 — 강체 결합 슬레이브 / R09 코어 묶음일 수 있음"
        else:
            import numpy as _np
            f_all = _np.concatenate([_np.asarray(mf.f_i).reshape(-1),
                                      _np.asarray(mf.f_j).reshape(-1)])
            if _np.max(_np.abs(f_all)) < 1e-3:
                diag = (f"단면력 모두 0 — 강체 결합 슬레이브 가능 "
                        f"(ele={mf.ele_tag})")
        popup.set_info(
            full_label=full_label, kind=m.kind, length_mm=L,
            section_name=section_name, policy=policy, ratio=ratio,
            f_i=f_i, f_j=f_j, stations=stations, diagnostic=diag,
        )

    def _hide_hover_popup(self):
        """호버 정보창 숨김. 위젯이 없으면 무시."""
        popup = getattr(self, '_hover_popup', None)
        if popup is None:
            return
        if popup.isVisible():
            popup.hide()

    # ── Phase 9: 클릭 고정 정보창 슬롯 시스템 ─────────────────
    def _pinned_slots(self):
        """슬롯 리스트 lazy init. 각 항목: dict(mid, popup, color) 또는 None.

        FIFO 정책 — 인덱스 0 이 가장 오래된 항목. 새 항목은 가장 오래된
        슬롯에 들어가고 그 자리는 끝으로 회전. 단순 list[None]*5 + 위치
        교체 방식이 가장 직관적이라 채택.
        """
        if getattr(self, '_pin_slots', None) is None:
            self._pin_slots = [None] * _PIN_MAX_SLOTS
        return self._pin_slots

    def _find_pinned_slot_for_mid(self, mid: int):
        """같은 부재가 이미 고정돼 있으면 그 슬롯 인덱스 반환, 없으면 None."""
        slots = self._pinned_slots()
        for i, s in enumerate(slots):
            if s is not None and s.get('mid') == int(mid):
                return i
        return None

    def _allocate_pinned_slot(self):
        """빈 슬롯 인덱스 반환. 다 차면 가장 오래된 슬롯(인덱스 0) 자동 해제.

        반환: (slot_idx, color_rgba)
        """
        slots = self._pinned_slots()
        # 빈 자리 찾기
        for i, s in enumerate(slots):
            if s is None:
                return i, _PIN_PALETTE[i]
        # 만석 → 가장 오래된 슬롯(0번) 닫고 그 자리 재사용
        self._free_pinned_slot(0)
        return 0, _PIN_PALETTE[0]

    def _free_pinned_slot(self, idx: int):
        """슬롯 닫기 — 정보창 destroy + 슬롯 None. 강조선은 호출자가 갱신."""
        slots = self._pinned_slots()
        if not (0 <= idx < len(slots)) or slots[idx] is None:
            return
        popup = slots[idx].get('popup')
        if popup is not None:
            popup.hide()
            popup.deleteLater()
        slots[idx] = None

    def _refresh_pinned_highlight(self):
        """모든 슬롯의 부재 강조선을 한 번에 viewer 에 보냄."""
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            self._viewer.update_pinned_members([])
            return
        om = last_ops[0]; am = last_ops[1]
        segs = []
        for s in self._pinned_slots():
            if s is None:
                continue
            m = am.members.get(int(s['mid']))
            if m is None:
                continue
            c1 = om.node_tags.get(m.n1)
            c2 = om.node_tags.get(m.n2)
            if c1 is None or c2 is None:
                continue
            segs.append(((c1, c2), s['color']))
        self._viewer.update_pinned_members(segs)

    def pin_member(self, mid: int, mouse_pos):
        """부재 클릭 시 호출 — 슬롯 할당 + 정보창 생성 + 강조선 갱신.

        같은 부재가 이미 고정돼 있으면 새로 띄우지 않고 기존 창을 유지.
        """
        if self._find_pinned_slot_for_mid(mid) is not None:
            return
        idx, color = self._allocate_pinned_slot()
        # 새 정보창 — 호버 팝업과 같은 클래스, 별도 인스턴스
        try:
            from modular_3d.ui.member_info_popup import MemberInfoPopup
        except Exception:
            return
        parent_widget = self._viewer.get_native_widget()
        if parent_widget is None:
            return
        popup = MemberInfoPopup(parent_widget)
        # 클릭 고정창 모드 — 마우스 이벤트 받음, × 버튼 표시 (드래그/닫기)
        popup.set_pinned_mode(True)
        popup.set_border_color(color)
        # Phase 11 — × 버튼 → 슬롯 해제
        popup.closed.connect(
            lambda mid=int(mid): self._close_pinned_by_mid(mid))
        # 본문 채우기
        self._refresh_popup_content(popup, mid)
        # Phase 12 — 등장 위치: 마우스 옆 + 기존 창과 거의 겹치면 캐스케이드.
        popup.move_to_mouse(mouse_pos)
        popup.show()
        popup.raise_()
        self._cascade_if_overlapped(popup)
        self._pinned_slots()[idx] = {
            'mid': int(mid), 'popup': popup, 'color': color,
        }
        self._refresh_pinned_highlight()

    def close_all_pinned(self):
        """Phase 14 — ESC 단축키 → 떠 있는 모든 정보창 + 강조선 동시 제거."""
        slots = self._pinned_slots()
        for i in range(len(slots)):
            self._free_pinned_slot(i)
        self._refresh_pinned_highlight()

    def _close_pinned_by_mid(self, mid: int):
        """Phase 11 — × 버튼 클릭으로 mid 슬롯 해제 + 강조선 갱신."""
        idx = self._find_pinned_slot_for_mid(mid)
        if idx is None:
            return
        self._free_pinned_slot(idx)
        self._refresh_pinned_highlight()

    def _next_pin_color_preview(self):
        """Phase 13 — 다음 클릭 시 배정될 슬롯 색.

        - 빈 슬롯이 있으면 그 슬롯의 팔레트 색
        - 만석이면 가장 오래된(인덱스 0) 슬롯의 색 (FIFO 로 곧 닫힐 자리)
        """
        slots = self._pinned_slots()
        for i, s in enumerate(slots):
            if s is None:
                return _PIN_PALETTE[i]
        return _PIN_PALETTE[0]

    def _cascade_if_overlapped(self, new_popup):
        """Phase 12 — 새 정보창이 기존 슬롯 창과 거의 겹치면 비킴.

        사용자 보고 #2 — 캐스케이드가 항상 +24, +24 방향이라 우하단 등장
        popup 은 영역 끝에 클램프되어 이동 불가했음. 부모 중심에서 멀어지는
        방향으로 동적 결정 + 방향이 막히면 반대 방향 폴백.
        """
        parent = self._viewer.get_native_widget()
        if parent is None:
            return
        max_iter = _PIN_MAX_SLOTS * 6  # 방향 폴백 여유
        step = 28
        threshold = 50
        for _ in range(max_iter):
            collided = False
            for s in self._pinned_slots():
                if s is None:
                    continue
                p = s.get('popup')
                if p is None or p is new_popup or not p.isVisible():
                    continue
                dx = abs(new_popup.x() - p.x())
                dy = abs(new_popup.y() - p.y())
                if dx < threshold and dy < threshold:
                    collided = True; break
            if not collided:
                return
            # 부모 영역 중심에서 멀어지는 방향으로 step.
            pw = parent.width(); ph = parent.height()
            cx = new_popup.x() + new_popup.width() / 2
            cy = new_popup.y() + new_popup.height() / 2
            sx = -step if cx > pw / 2 else step
            sy = -step if cy > ph / 2 else step
            max_x = pw - new_popup.width()
            max_y = ph - new_popup.height()
            nx = max(0, min(new_popup.x() + sx, max_x))
            ny = max(0, min(new_popup.y() + sy, max_y))
            # 클램프되어 이동 안 했으면 반대 방향 시도
            if nx == new_popup.x() and ny == new_popup.y():
                nx = max(0, min(new_popup.x() - sx, max_x))
                ny = max(0, min(new_popup.y() - sy, max_y))
            if nx == new_popup.x() and ny == new_popup.y():
                # 양방향 모두 막힘 — 더 이상 이동 불가 (영역 너무 작음)
                return
            new_popup.move(nx, ny)

    def _on_panel_members_selected(self, mids):
        """그룹 노드(모듈/층/역할) 클릭 → 하위 모든 부재를 한 번에 파란색 강조.

        viewer.highlight_ops_members 에 (n1, n2) 노드 id 쌍 리스트로 전달.
        ops 뷰가 꺼진 상태(메쉬 뷰)에서는 무시 — 단일 부재 강조와 동일 정책.
        """
        if not self._viewer.is_ops_view_active():
            return
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            return
        om = last_ops[0]; am = last_ops[1]
        pairs = []
        for mid in mids:
            m = am.members.get(int(mid))
            if m is None:
                continue
            pairs.append((m.n1, m.n2))
        if not pairs:
            self._viewer.clear_member_highlight()
            return
        self._viewer.highlight_ops_members(om, pairs)

    def _pin_member_at_canvas_corner(self, mid: int):
        """Phase 16 — 트리 잎 클릭 시 정보창을 캔버스 우하단에 등장.

        절대 픽셀 좌표가 아닌 캔버스 자체 우하단 기준 상대 좌표. 도킹
        패널 폭이 바뀌어도 영향 받지 않는다. 캐스케이드 규칙은 Phase 12
        와 공유.
        """
        # 같은 부재가 이미 고정돼 있으면 새로 안 띄움 (Phase 9 정책)
        if self._find_pinned_slot_for_mid(mid) is not None:
            return
        parent = self._viewer.get_native_widget()
        if parent is None:
            return
        # 정보창의 sizeHint 가 안정적이라 일단 디폴트 크기 가정 (240×?)
        # 우선 임시로 (parent.width-260, parent.height-260) 로 두고 등장 후
        # 자동 adjustSize 결과로 우하단 12 px 안쪽에 다시 맞춤.
        approx_w = 260; approx_h = 260
        x = parent.width() - approx_w - 12
        y = parent.height() - approx_h - 12
        fake_mouse = (x - 16, y - 16)   # move_to_mouse 의 HOVER_OFFSET 만큼 보정
        self.pin_member(mid, fake_mouse)

    def _on_panel_member_selected(self, mid: int):
        """도킹 패널에서 부재 클릭 → 좌측 ops 뷰에 파란색 강조.

        ops 뷰가 꺼진 상태(메쉬 뷰)에서는 강조 라인이 메쉬 위에 떠 어색하므로 무시.
        [버그 패치 — 사용자 보고 #1]: 진단 로그 추가 — 클릭 mid의 노드 좌표를
        출력하여 z축 반전 의심 시 즉시 확인 가능.
        """
        if not self._viewer.is_ops_view_active():
            return
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            return
        om = last_ops[0]
        am = last_ops[1]
        m = am.members.get(mid)
        if m is not None:
            c1 = om.node_tags.get(m.n1)
            c2 = om.node_tags.get(m.n2)
            comp_ids = m.source_comp_ids if hasattr(m, 'source_comp_ids') else []
            print(f'[F6 SELECT] mid={mid} kind={m.kind} role={m.role} '
                  f'comp={comp_ids} n1={m.n1}@{c1} n2={m.n2}@{c2}')
            self._viewer.highlight_ops_member(om, m.n1, m.n2)
            # Phase 16 — 트리 잎 클릭 → 캔버스 우하단으로 정보창 등장
            self._pin_member_at_canvas_corner(int(mid))

    # ── 물량산출 응력비 시각화 (2026-05-08) ──────────────────
    def _on_ratio_view_changed(self, mode: str):
        """analysis_panel.ratio_view_changed 시그널 핸들러.

        mode:
          'off'         : 색상 끄기 → 일반 검정선 복귀
          'ratio:1종'   : 정책별 부재 ratio 사전으로 5단계 색상
          'ratio:2종', 'ratio:3종' : 동일
        """
        self._current_ratio_view = mode
        if not self._viewer.is_ops_view_active():
            return
        last_ops = getattr(self, '_last_ops_analysis', None)
        if last_ops is None:
            return
        om = last_ops[0]
        if mode == 'off' or not mode.startswith('ratio:'):
            self._viewer.hide_member_contour(om)
            return
        policy = mode.split(':', 1)[1]
        rep = self._quantity_reports.get(policy)
        if rep is None:
            self._viewer.hide_member_contour(om)
            return
        ratios = rep.member_ratios or {}
        if not ratios:
            self._viewer.hide_member_contour(om)
            return
        self._viewer.show_member_ratio_bands(om, ratios)

    def set_quantity_reports(self, reports: dict):
        """Phase 6 통합 진입점에서 호출 — 정책별 QuantityReport 주입."""
        self._quantity_reports = reports or {}
        # 현재 색상 모드가 ratio 면 즉시 갱신
        if self._current_ratio_view.startswith('ratio:'):
            self._on_ratio_view_changed(self._current_ratio_view)

    # F7 시각화 모드 + Stage 1~4 오버레이는 Phase 5 에서 폐기됨.
    # OpenSees 기반 F6 + 우측 패널의 케이스/컨투어 토글이 모든 시각화를 대체한다.

