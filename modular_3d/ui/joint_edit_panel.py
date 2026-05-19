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
        title = QLabel('접합부 조정')
        title.setStyleSheet('font-weight: bold; font-size: 12px;')
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)

        # ── 접합부 편집 활성 토글 (더미) ────────────────
        joint_title = QLabel('접합부 변경 모드')
        joint_title.setStyleSheet('font-weight: bold; font-size: 11px;')
        lay.addWidget(joint_title)

        self._joint_toggle = QCheckBox('접합부 편집 활성 (추후 구현)')
        self._joint_toggle.setEnabled(False)
        lay.addWidget(self._joint_toggle)

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
