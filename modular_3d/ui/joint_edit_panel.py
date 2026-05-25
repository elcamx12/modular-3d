"""접합부 조정 탭 우측 위젯 — 결합 토글·옵션·범례.

[정책 2026-05-13 접합부조정탭 Phase 3]
디자인 탭의 `DesignPropertiesPanel` 안에 더미로 박혀 있던 "접합부 변경 모드"
체크박스·색상 범례·합성거동 옵션을 본 위젯으로 옮겨와, 새 탭(접합부 조정) 의
우측 패널로 사용한다.

본 위젯은 현재 **더미** — 토글이 실제 결합 로직에 영향을 주지 않는다. 후속
작업에서:
  - 접합부 편집 활성 토글이 켜지면 viewer 의 결합 픽킹 모드 활성.
  - 합성거동 옵션이 spec 의 모듈 보 쌍에 합성 단면 플래그를 박음.
  - 범례 칸은 Phase 5 의 rule_id 색 매핑을 표시.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QCheckBox,
    QPushButton, QGroupBox,
)


class JointEditPanel(QWidget):
    """접합부 조정 탭 우측 패널.

    [시그널]
    - diaphragm_toggle(bool): 다이어프램 마스터↔슬레이브 표시 토글 — viewer
      의 `set_show_diaphragms` 에 연결.
    [향후 시그널 예정]
    - joint_edit_toggled(bool): 접합부 편집 활성 토글 변화 — 결합 픽킹 모드.
    - composite_action_changed(bool): 합성거동 옵션 변화 — spec 빌드 시 합성 단면.
    """

    # [2026-05-13 접합부조정탭] 다이어프램 토글 — 구조해석 탭에서 이동.
    # 후속 작업에서 다이어프램 재정의 시에도 본 탭에서 시각 확인 가능하게.
    diaphragm_toggle = pyqtSignal(bool)
    # [2026-05-17] 규칙 ID 별 시각화 가시성 토글 — viewer 의
    # set_rule_visibility 에 연결. 실제 접합은 유지, 단순 시각화 토글.
    rule_visibility_changed = pyqtSignal(str, bool)
    # [2026-05-25 접합부 변경 1단계] 접합 편집 모드 토글 — 켜지면 3D 좌클릭이
    # 부재 픽킹 대신 가장 가까운 컴포넌트 간 접합 픽킹으로 동작.
    edit_mode_toggled = pyqtSignal(bool)
    # 선택 접합에 대한 변경 요청 — 'remove'/'pin'/'rigid'.
    joint_change_requested = pyqtSignal(str)
    # 선택 접합의 사용자 변경 취소(자동 규칙 복귀) 요청.
    joint_revert_requested = pyqtSignal()
    # [2026-05-25 2단계] 접합 추가 모드 토글 — 켜지면 3D 좌클릭이 두 점(노드)을
    # 차례로 선택해 신규 접합을 만든다. 편집 모드와 상호 배타.
    add_mode_toggled = pyqtSignal(bool)
    # [2026-05-25] 저장 요청 — 현재 디자인 + 접합 변경을 건물 파일(기존 양식)로
    # 저장. 배치 설계 탭의 저장과 같은 동작(접합 오버라이드 포함). 불러오기는
    # 배치 설계 탭에서만(접합 탭엔 저장만).
    save_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # rule_id → 체크박스 매핑 (refresh_legend 호출마다 재구성)
        self._rule_checks: dict = {}
        # rule_id → 가시성 상태 (refresh 시 보존)
        self._rule_visible: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # ── 제목 ───────────────────────────────────────
        title = QLabel('접합부 설계')
        title.setStyleSheet('font-weight: bold; font-size: 12px;')
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)

        # ── 접합부 편집 활성 토글 ──────────────────────
        joint_title = QLabel('접합부 변경 모드')
        joint_title.setStyleSheet('font-weight: bold; font-size: 11px;')
        lay.addWidget(joint_title)

        self._joint_toggle = QCheckBox('접합부 편집 활성')
        self._joint_toggle.setToolTip(
            '켜면 3D 좌클릭이 가장 가까운 컴포넌트 간 접합을 선택한다. '
            '모듈 내부(보-기둥)는 편집 대상이 아니다.'
        )
        self._joint_toggle.toggled.connect(self._on_edit_toggle)
        lay.addWidget(self._joint_toggle)

        # ── 선택 접합 정보 + 변경 버튼 ─────────────────
        self._sel_group = QGroupBox('선택 접합')
        sel_lay = QVBoxLayout(self._sel_group)
        sel_lay.setContentsMargins(6, 6, 6, 6)
        sel_lay.setSpacing(4)
        self._sel_info = QLabel('편집을 켜고 접합을 클릭하세요.')
        self._sel_info.setWordWrap(True)
        self._sel_info.setStyleSheet('font-size: 11px;')
        sel_lay.addWidget(self._sel_info)

        self._edit_single_chk = QCheckBox('이 층만 적용 (기본: 모든 층)')
        self._edit_single_chk.setToolTip(
            '체크하면 제거·핀·강접을 클릭한 그 층의 접합에만 적용합니다. '
            '끄면 같은 평면 위치의 모든 층에 일괄 적용합니다.')
        sel_lay.addWidget(self._edit_single_chk)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self._btn_remove = QPushButton('제거')
        self._btn_remove.setToolTip('이 접합을 완전히 없앤다(두 부재 분리).')
        self._btn_remove.clicked.connect(
            lambda: self.joint_change_requested.emit('remove'))
        self._btn_pin = QPushButton('핀')
        self._btn_pin.setToolTip('이 접합을 핀(회전 자유)으로 만든다.')
        self._btn_pin.clicked.connect(
            lambda: self.joint_change_requested.emit('pin'))
        self._btn_rigid = QPushButton('강접')
        self._btn_rigid.setToolTip('이 접합을 강접(회전 구속)으로 만든다.')
        self._btn_rigid.clicked.connect(
            lambda: self.joint_change_requested.emit('rigid'))
        btn_row.addWidget(self._btn_remove)
        btn_row.addWidget(self._btn_pin)
        btn_row.addWidget(self._btn_rigid)
        sel_lay.addLayout(btn_row)

        lay.addWidget(self._sel_group)
        self.clear_selection()   # 초기 버튼 비활성

        # 전체 초기화 — 선택과 무관하게 항상 활성. 모든 접합 변경(제거·핀·강접·
        # 추가)을 지우고 자동 규칙 상태로 되돌린다.
        self._btn_revert = QPushButton('모든 접합 변경 초기화')
        self._btn_revert.setToolTip(
            '이 디자인의 모든 접합 변경(제거·핀·강접·추가)을 지우고 '
            '자동 규칙 상태로 되돌립니다.')
        self._btn_revert.clicked.connect(self.joint_revert_requested.emit)
        lay.addWidget(self._btn_revert)

        # 저장 — 현재 디자인 + 접합 변경을 건물 파일(기존 양식)로 저장. 배치
        # 설계 탭의 저장과 동일(접합 오버라이드 포함). 불러오기는 배치 설계 탭에서.
        self._btn_save = QPushButton('디자인 + 접합 변경 저장')
        self._btn_save.setToolTip(
            '현재 디자인과 접합 변경을 건물 파일(기존 저장과 같은 양식)로 '
            '저장합니다. 저장한 파일은 배치 설계·모듈 정의 탭에서 불러올 수 있습니다.')
        self._btn_save.clicked.connect(self.save_requested.emit)
        lay.addWidget(self._btn_save)

        # ── 접합 추가 모드 (2단계) ─────────────────────
        self._add_group = QGroupBox('접합 추가')
        add_lay = QVBoxLayout(self._add_group)
        add_lay.setContentsMargins(6, 6, 6, 6)
        add_lay.setSpacing(4)
        self._add_toggle = QCheckBox('접합 추가 모드')
        self._add_toggle.setToolTip(
            '켜면 3D 좌클릭으로 두 점(노드)을 차례로 선택해 신규 접합을 만든다. '
            '두 점은 다른 부재 + 400mm 이내 + 수평/수직(한 축만 차이) 이어야 한다.'
        )
        self._add_toggle.toggled.connect(self._on_add_toggle)
        add_lay.addWidget(self._add_toggle)
        self._add_rigid_chk = QCheckBox('강접으로 추가 (기본: 핀)')
        add_lay.addWidget(self._add_rigid_chk)
        self._add_single_chk = QCheckBox('이 층만 추가 (다른 층 복제 안 함)')
        self._add_single_chk.setToolTip(
            '체크하면 클릭한 그 층의 접합만 만들고 다른 층에는 복제하지 않습니다.')
        add_lay.addWidget(self._add_single_chk)
        self._add_right_chk = QCheckBox('직각(ㄴ자) 결합')
        self._add_right_chk.setToolTip(
            '두 점이 평면·높이 모두 다를 때, 중간 노드를 거쳐 수직+수평의 ㄴ자로 '
            '직각 결합합니다(R03 방식). 끄면 두 점을 직선으로 연결합니다.')
        add_lay.addWidget(self._add_right_chk)
        self._add_hint = QLabel('추가 모드를 켜고 점1을 클릭하세요.')
        self._add_hint.setWordWrap(True)
        self._add_hint.setStyleSheet('font-size: 11px; color: #555;')
        add_lay.addWidget(self._add_hint)
        lay.addWidget(self._add_group)

        # ── 합성거동 옵션 (더미) ──────────────────────
        composite_label = QLabel(
            '상부모듈 바닥보 -\n하부모듈 천창보 합성거동'
        )
        composite_label.setWordWrap(True)
        composite_label.setStyleSheet('font-size: 11px;')
        lay.addWidget(composite_label)
        self._composite_action_chk = QCheckBox('적용')
        self._composite_action_chk.setEnabled(False)
        self._composite_action_chk.setToolTip(
            '체크 시 두 보가 하나의 합성 단면으로 거동한다고 가정. '
            '현재 옵션만 노출 — 추후 해석 로직에 반영 예정.'
        )
        lay.addWidget(self._composite_action_chk)

        sep_d = QFrame()
        sep_d.setFrameShape(QFrame.HLine)
        lay.addWidget(sep_d)

        # ── 다이어프램 시각화 토글 ────────────────────
        # [2026-05-13 접합부조정탭] 구조해석 탭의 다이어프램 체크박스를 본 탭으로
        # 이동. 다음 작업에서 다이어프램 재정의 시 본 탭에서 켜고 끄며 확인.
        diaph_title = QLabel('다이어프램 시각화')
        diaph_title.setStyleSheet('font-weight: bold; font-size: 11px;')
        lay.addWidget(diaph_title)
        self._diaphragm_cb = QCheckBox('다이어프램 표시')
        self._diaphragm_cb.setToolTip(
            '컴포넌트별 강체 다이어프램(모듈·바닥패널·코어슬래브 단위) — '
            '마스터에서 슬레이브로 뻗는 회색 스포크선과 청록 반투명 면을 표시.'
        )
        self._diaphragm_cb.toggled.connect(self.diaphragm_toggle.emit)
        lay.addWidget(self._diaphragm_cb)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        lay.addWidget(sep2)

        # ── 규칙 ID 범례 placeholder (Phase 5 채움) ────
        legend_title = QLabel('규칙 ID 범례')
        legend_title.setStyleSheet('font-weight: bold; font-size: 11px;')
        lay.addWidget(legend_title)

        # spec.iter_* 의 rule_id 집합을 읽어 동적 갱신. 초기 상태는 비어 있고,
        # 와이어프레임 프리뷰 시 refresh_legend 가 채움.
        self._legend_holder = QWidget()
        self._legend_lay = QVBoxLayout(self._legend_holder)
        self._legend_lay.setContentsMargins(0, 0, 0, 0)
        self._legend_lay.setSpacing(2)
        lay.addWidget(self._legend_holder)

        lay.addStretch(1)

    # ── 모드 토글 (편집 ↔ 추가 배타) ─────────────────────
    def _on_edit_toggle(self, on: bool):
        """편집 모드 토글 — 켜면 추가 모드 해제."""
        if on and self._add_toggle.isChecked():
            self._add_toggle.setChecked(False)   # add_mode_toggled(False) 발신
        self.edit_mode_toggled.emit(on)

    def _on_add_toggle(self, on: bool):
        """추가 모드 토글 — 켜면 편집 모드 해제."""
        if on and self._joint_toggle.isChecked():
            self._joint_toggle.setChecked(False)  # edit_mode_toggled(False) 발신
        if on:
            self._add_hint.setText('점1을 클릭하세요.')
        else:
            self._add_hint.setText('추가 모드를 켜고 점1을 클릭하세요.')
        self.add_mode_toggled.emit(on)

    def is_add_rigid(self) -> bool:
        """접합 추가 시 강접으로 만들지(체크) 여부."""
        return self._add_rigid_chk.isChecked()

    def is_add_single_layer(self) -> bool:
        """접합 추가 시 이 층만(다른 층 복제 안 함) 여부."""
        return self._add_single_chk.isChecked()

    def is_add_right_angle(self) -> bool:
        """접합 추가 시 직각(ㄴ자) 결합 여부."""
        return self._add_right_chk.isChecked()

    def is_edit_single_layer(self) -> bool:
        """접합 제거·편집 시 이 층만 적용 여부(체크) — 끄면 모든 층 일괄."""
        return self._edit_single_chk.isChecked()

    def set_add_hint(self, text: str):
        """접합 추가 안내 문구 갱신."""
        self._add_hint.setText(text)

    # ── 선택 접합 표시 (2026-05-25) ──────────────────────
    def show_selected_joint(self, info: dict):
        """픽킹된 접합 정보를 표시하고 변경 버튼을 활성화한다."""
        a = info.get('a_comp', 0)
        b = info.get('b_comp', 0)
        is_rigid = bool(info.get('is_rigid', False))
        prop = '강접' if is_rigid else '핀'
        ax, ay = info.get('a_xy', (0, 0))
        bx, by = info.get('b_xy', (0, 0))
        vertical = abs(ax - bx) < 1.0 and abs(ay - by) < 1.0
        kind = '수직(적층)' if vertical else '수평(인접)'
        rid = info.get('rule_id', '')
        # comp id 0 은 보조점(사영점·다이어프램 마스터 등) — "부재 #0" 대신 표기.
        a_label = f'부재 #{a}' if a else '보조점'
        b_label = f'부재 #{b}' if b else '보조점'
        self._sel_info.setText(
            f'{a_label} ↔ {b_label}\n'
            f'현재 성질: {prop}\n'
            f'유형: {kind}\n'
            f'규칙: {rid}\n'
            f'(같은 평면 위치의 모든 층에 함께 적용)'
        )
        for btn in (self._btn_remove, self._btn_pin, self._btn_rigid):
            btn.setEnabled(True)

    def clear_selection(self):
        """선택 해제 — 변경 버튼 비활성(전체 초기화 버튼은 항상 활성)."""
        on = (self._joint_toggle.isChecked()
              if hasattr(self, '_joint_toggle') else False)
        self._sel_info.setText(
            '접합을 클릭하세요.' if on else '편집을 켜고 접합을 클릭하세요.')
        for btn in (self._btn_remove, self._btn_pin, self._btn_rigid):
            btn.setEnabled(False)

    # ── 외부 갱신 훅 ───────────────────────────────────
    def refresh_legend(self, rule_ids):
        """spec 에서 추출한 rule_id 집합으로 범례를 새로 그림.

        [정책 2026-05-13]
        - rule_ids: Iterable[str] — spec 의 결합 레코드에서 모은 rule_id 집합.
        - viewer.OPS_RULE_ID_COLOR 매핑 dict 를 참조해 매핑된 색을 함께 표기.
          매핑이 없는 rule_id 는 회색 폴백 표시.

        [정책 2026-05-17 — 가시성 토글]
        각 rule_id 줄에 체크박스를 두어 클릭 시 rule_visibility_changed
        시그널(rule_id, visible) 발신. 시각화만 영향(실제 접합 유지).
        이전 refresh 에서의 가시성 상태(_rule_visible) 는 보존되어 다음
        refresh 에서 같은 rule_id 가 다시 등장해도 켬/끔 상태 유지.
        """
        # 기존 위젯 모두 비움.
        while self._legend_lay.count() > 0:
            item = self._legend_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._rule_checks = {}

        try:
            from modular_3d.render.viewer import OPS_RULE_ID_COLOR
        except ImportError:
            OPS_RULE_ID_COLOR = {}
        seen = sorted(set(rule_ids))
        if not seen:
            empty = QLabel('  (결합 없음)')
            empty.setStyleSheet('color: #888; font-size: 10px;')
            self._legend_lay.addWidget(empty)
            return
        for rid in seen:
            col = OPS_RULE_ID_COLOR.get(rid)
            if col is None:
                hex_color = '#888888'
            else:
                r, g, b = (int(c * 255) for c in col[:3])
                hex_color = f'#{r:02x}{g:02x}{b:02x}'
            # 한 줄 = 체크박스(가시) + 색 점 + rule_id 라벨.
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(4)
            chk = QCheckBox()
            visible_init = self._rule_visible.get(rid, True)
            chk.setChecked(visible_init)
            chk.setToolTip(
                f'{rid} 결합선 시각화 토글 (실제 접합은 유지)'
            )
            # 클로저로 rid 캡쳐 — 토글 시 시그널 발신 + 내부 상태 갱신.
            def _on_toggle(state, _rid=rid):
                vis = bool(state)
                self._rule_visible[_rid] = vis
                self.rule_visibility_changed.emit(_rid, vis)
            chk.toggled.connect(_on_toggle)
            self._rule_checks[rid] = chk
            row_lay.addWidget(chk)
            line = QLabel(f'⬤  {rid}')
            line.setStyleSheet(f'color: {hex_color}; font-size: 10px;')
            row_lay.addWidget(line)
            row_lay.addStretch(1)
            self._legend_lay.addWidget(row)
