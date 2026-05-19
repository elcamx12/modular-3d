"""부재 단위 호버/클릭 정보창 위젯 (설계서_부재호버.md).

Phase 3: 빈 흰 패널 골격 — 캔버스 위 absolute 배치, 둥근 모서리, 외곽선.
Phase 4 이후 텍스트·미니 그래프·× 버튼·드래그 이동·외곽선 색 등이
점진적으로 더해진다.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
from PyQt5.QtCore import Qt, QSize, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import QFont, QPainter, QPen, QBrush, QColor, QPolygonF
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton


def _fmt_kn(v_n: float) -> str:
    """N(뉴턴) → kN 문자열, 소수 1 자리."""
    return f"{v_n / 1000.0:+.1f}"


def _fmt_knm(v_nmm: float) -> str:
    """N·mm → kN·m 문자열, 소수 1 자리."""
    return f"{v_nmm / 1e6:+.1f}"


_KIND_KO = {'beam': '보', 'column': '기둥', 'truss': '트러스'}


# ── 부재 단면력 미니 그래프 (Phase 6/7/8) ────────────────────────
class ForceDiagramWidget(QWidget):
    """AFD/SFD/BMD 공통 미니 그래프 — QPainter 직접 그림 (경량, matplotlib X).

    데이터: 5 station 단면력 (kN 또는 kN·m). 좌→우 = i → j 단.
    표시: 위젯 중앙에 baseline, 분포 곡선 + 채우기, 좌상단에 제목 + max 값,
    양수 영역(인장/처짐+)은 fill_pos 색, 음수는 fill_neg 색.

    BMD 의 경우 `sign_invert=True` 로 두면 양수 모멘트가 baseline 아래쪽으로
    그려진다 — 처짐 방향(아래) 이 양수, 구조설계 관례.
    """

    def __init__(self, title: str, unit: str,
                  sign_invert: bool = False,
                  parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = title
        self._unit = unit
        self._sign_invert = sign_invert
        self._values: Optional[np.ndarray] = None   # (N,) station values
        self.setMinimumSize(200, 56)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(),
                           self.sizePolicy().verticalPolicy())
        # 채우기 색 (반투명) — 양수 / 음수 구분.
        self._fill_pos = QColor(80, 150, 220, 90)    # 옅은 청색
        self._fill_neg = QColor(220, 110, 80, 90)    # 옅은 적색
        self._line_color = QColor(40, 40, 40)
        self._base_color = QColor(120, 120, 120)
        self._title_color = QColor(40, 40, 40)

    def sizeHint(self) -> QSize:
        return QSize(220, 64)

    def set_values(self, values: Optional[Sequence[float]]):
        """station 값 배열 갱신 후 다시 그림. None 이면 빈 상태."""
        if values is None:
            self._values = None
        else:
            self._values = np.asarray(values, dtype=np.float64).reshape(-1)
        self.update()

    def set_title(self, title: str):
        """제목 갱신 (예: 'SFD (Vz)' — dominant 성분 표시)."""
        self._title = title
        self.update()

    # ── 그리기 ──────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width(); h = self.height()
        # 여백
        m_l, m_r, m_t, m_b = 8, 8, 16, 4
        px0 = m_l; px1 = w - m_r
        py_top = m_t; py_bot = h - m_b
        baseline_y = (py_top + py_bot) / 2.0
        half_h = (py_bot - py_top) / 2.0 - 2.0
        if half_h < 4.0:
            half_h = 4.0

        # 헤더: 제목 + max 값
        p.setPen(QPen(self._title_color))
        p.setFont(QFont('맑은 고딕', 8))
        max_text = ''
        if self._values is not None and self._values.size > 0:
            max_abs = float(np.max(np.abs(self._values)))
            if self._sign_invert:
                # BMD: 표시 부호 반전 — 최대 절댓값은 그대로
                pass
            max_text = f"max |·| = {max_abs:.2f} {self._unit}"
        header = f"{self._title} ({self._unit})" + (
            ("   " + max_text) if max_text else '')
        p.drawText(QRectF(m_l, 0, w - m_l - m_r, m_t),
                   Qt.AlignLeft | Qt.AlignVCenter, header)

        # baseline
        p.setPen(QPen(self._base_color, 1.0, Qt.DashLine))
        p.drawLine(int(px0), int(baseline_y), int(px1), int(baseline_y))

        if self._values is None or self._values.size == 0:
            return

        vals = self._values.copy()
        if self._sign_invert:
            vals = -vals
        max_abs = float(np.max(np.abs(vals)))
        if max_abs < 1e-9:
            # 분포 0 — baseline 만 그림
            return

        # station x 좌표
        n = vals.size
        xs = np.linspace(px0, px1, n)
        ys = baseline_y - (vals / max_abs) * half_h   # 양수는 위(Qt y-down)

        # 양수/음수 분리 채우기 — 한 폴리곤으로 처리.
        # 각 station 마다 baseline 과 곡선 사이를 사각형/사다리꼴 합으로.
        # 단순화: 전체 곡선을 polygon(베이스라인 닫음)으로 fill, color 는
        # 양수/음수 비율로 큰 쪽 색. 더 정확한 분리는 station 별 zero-crossing
        # 보간 필요 — 시각 단순함 우선.
        poly = QPolygonF()
        poly.append(QPointF(xs[0], baseline_y))
        for x, y in zip(xs, ys):
            poly.append(QPointF(float(x), float(y)))
        poly.append(QPointF(xs[-1], baseline_y))
        # 우세 색 결정
        avg = float(np.mean(vals))
        fill = self._fill_pos if avg >= 0 else self._fill_neg
        p.setBrush(QBrush(fill))
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly)

        # 곡선
        p.setPen(QPen(self._line_color, 1.6))
        p.setBrush(Qt.NoBrush)
        for i in range(n - 1):
            p.drawLine(QPointF(float(xs[i]), float(ys[i])),
                       QPointF(float(xs[i + 1]), float(ys[i + 1])))


# 위젯 기본 크기 — Phase 4 에서 텍스트가 들어가면 자동으로 늘어남.
_DEFAULT_W = 240
_DEFAULT_H = 70

# 마우스에서 살짝 떨어뜨려 띄울 오프셋(px). 커서가 정보창 위에 안 겹치게.
HOVER_OFFSET = (16, 16)


class MemberInfoPopup(QWidget):
    """부재 정보 떠다니는 패널 — 캔버스 위 absolute 위젯.

    Phase 3 골격:
      - 배경 흰색, 살짝 둥근 모서리, 회색 외곽선(나중에 슬롯 색으로 교체)
      - 마우스 통과(WA_TransparentForMouseEvents) — 호버 동안엔 클릭 패스스루
      - 본문은 빈 라벨 한 줄
    Phase 4 이후 텍스트 채우기·× 버튼·그래프 등이 더해진다.
    """

    # Phase 11 — × 버튼 클릭 시 부모(controls_f6)에 통보.
    closed = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        # 캔버스 위 절대 좌표 자식 위젯. 부모(캔버스 native widget) 위에 떠 있음.
        # 호버 동안엔 마우스 이벤트 패스스루 — Phase 10 에서 드래그 추가 시 변경.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMinimumSize(_DEFAULT_W, _DEFAULT_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        # 사용자 보고: 클릭 후 외곽선 색이 회색으로 돌아감 → styleSheet 기반
        # 배경/외곽선이 QOpenGLWidget 자식 환경에서 안정적으로 적용되지 않음.
        # paintEvent 에서 직접 그리는 방식으로 변경. 자식 QLabel 들은 투명
        # 배경만 유지.
        self._border_color = QColor(120, 120, 120)
        self._bg_color = QColor(255, 255, 255)
        self.setStyleSheet(
            "QLabel { background: transparent; border: none; color: #222222; }"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)

        # 본문 라벨 5 행 — Phase 4 에서 채워짐.
        title_font = QFont('맑은 고딕', 9); title_font.setBold(True)
        body_font = QFont('맑은 고딕', 9)
        mono_font = QFont('Consolas', 9)

        self._title = QLabel('—')        # "1층 a모듈 / 기둥 #123"
        self._title.setFont(title_font)
        self._meta = QLabel('—')         # "기둥 · L = 3420 mm · H-200×100"
        self._meta.setFont(body_font)
        self._ratio = QLabel('—')        # "응력비 1종: 0.42"
        self._ratio.setFont(body_font)
        self._line_i = QLabel('—')       # "i단 N=-12.3 V=4.5 M=8.7"
        self._line_i.setFont(mono_font)
        self._line_j = QLabel('—')       # "j단 ..."
        self._line_j.setFont(mono_font)
        # 0 부재 진단 메시지 — 평소엔 숨김, 0/미등록 시에만 노출.
        self._diag = QLabel('')
        self._diag.setFont(body_font)
        self._diag.setStyleSheet("color: #B26000;")
        self._diag.setWordWrap(True)
        self._diag.hide()
        for w in (self._title, self._meta, self._ratio,
                  self._line_i, self._line_j, self._diag):
            lay.addWidget(w)

        # 미니 그래프 3 개 — AFD/SFD/BMD (Phase 6/7/8)
        self._afd = ForceDiagramWidget('AFD', 'kN', sign_invert=False, parent=self)
        self._sfd = ForceDiagramWidget('SFD', 'kN', sign_invert=False, parent=self)
        # BMD: 처짐 방향(아래) 양수 — 부호 반전
        self._bmd = ForceDiagramWidget('BMD', 'kN·m', sign_invert=True, parent=self)
        for w in (self._afd, self._sfd, self._bmd):
            lay.addWidget(w)

        # Phase 11 — × 닫기 버튼. 헤더 없는 단순 디자인 — 위젯 우측 상단
        # 모서리에 absolute 위치. styleSheet 의 외곽선 안쪽 영역 안에 자연 배치.
        self._close_btn = QPushButton('×', self)
        self._close_btn.setFlat(True)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setFixedSize(18, 18)
        self._close_btn.setStyleSheet(
            "QPushButton {"
            "  border: none; background: transparent;"
            "  color: #666666; font-weight: bold; font-size: 13px;"
            "}"
            "QPushButton:hover { color: #D00000; }"
        )
        self._close_btn.clicked.connect(self._on_close_clicked)
        # 호버 임시 정보창에선 × 버튼 의미 없음 — Phase 9 의 클릭 고정창에서만
        # transparent for mouse 가 풀려 작동. 기본값은 hover 모드 → 숨김.
        self._close_btn.hide()
        self._close_btn.raise_()

        self.hide()

    def _on_close_clicked(self):
        """× 버튼 클릭 → closed 시그널 emit. 슬롯 매니저가 해제 처리."""
        self.closed.emit()

    def set_pinned_mode(self, pinned: bool):
        """호버 임시창 / 클릭 고정창 모드 전환 (자체점검 #5).

        호버 임시창: 마우스 패스스루, × 숨김 — 어차피 즉시 사라지므로 닫기 의미 X.
        클릭 고정창: 마우스 받음(드래그/×), × 보임.
        """
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not pinned)
        btn = getattr(self, '_close_btn', None)
        if btn is not None:
            btn.setVisible(pinned)

    def resizeEvent(self, ev):
        """× 버튼을 항상 우측 상단 모서리에 위치 유지."""
        super().resizeEvent(ev)
        btn = getattr(self, '_close_btn', None)
        if btn is not None:
            btn.move(self.width() - btn.width() - 4, 4)

    # ── 외부 API ─────────────────────────────────────────────────

    def set_border_color(self, rgba) -> None:
        """외곽선 색 갱신. paintEvent 가 즉시 다시 그림."""
        if rgba is None:
            self._border_color = QColor(120, 120, 120)
        else:
            r, g, b = rgba[0], rgba[1], rgba[2]
            if max(r, g, b) <= 1.001:
                r = int(r * 255); g = int(g * 255); b = int(b * 255)
            else:
                r = int(r); g = int(g); b = int(b)
            self._border_color = QColor(r, g, b)
        self.update()

    def paintEvent(self, ev):
        """배경 + 둥근 모서리 + 외곽선을 직접 그림."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1.0, 1.0,
                      self.width() - 2.0, self.height() - 2.0)
        p.setBrush(QBrush(self._bg_color))
        p.setPen(QPen(self._border_color, 2.0))
        p.drawRoundedRect(rect, 6.0, 6.0)
        # 자식 위젯들(QLabel · ForceDiagramWidget · 닫기 버튼) 은
        # Qt 가 별도 paintEvent 로 위에 그려 준다.

    def set_info(self,
                 *,
                 full_label: str,
                 kind: str,
                 length_mm: float,
                 section_name: str,
                 policy: str,
                 ratio: Optional[float],
                 f_i,
                 f_j,
                 stations=None,
                 diagnostic: Optional[str] = None) -> None:
        """Phase 4 — 부재 본문 텍스트 갱신.

        f_i, f_j: OpenSees 양단 단면력 6 원소 배열 [N, Vy, Vz, T, My, Mz]
                   단위 N, N·mm. 표시 시 kN, kN·m 로 환산.
        ratio: 현 정책 응력비. None 이면 '—' 표시.
        """
        kind_ko = _KIND_KO.get(kind, kind or '—')
        self._title.setText(full_label)
        meta_parts = [kind_ko, f"L = {length_mm:.0f} mm"]
        if section_name:
            meta_parts.append(section_name)
        self._meta.setText('  ·  '.join(meta_parts))
        if ratio is not None:
            self._ratio.setText(f"응력비 {policy}: {ratio:.3f}")
        else:
            self._ratio.setText(f"응력비 {policy}: —")
        # [2026-05-19] 강·약축 모두 표시. 이전엔 Vy/Mz 만 보여 보 자중처럼
        # 약축(Vz/My) 으로 들어가는 케이스에 0 으로 표시되는 결함 → 오른쪽
        # 트리(N_max/V_max/M_max) 와 값이 어긋남.
        # f = [N, Vy, Vz, T, My, Mz]
        if f_i is not None and len(f_i) >= 6:
            self._line_i.setText(
                f"i단  N={_fmt_kn(f_i[0])} kN  "
                f"Vy={_fmt_kn(f_i[1])}  Vz={_fmt_kn(f_i[2])} kN  "
                f"My={_fmt_knm(f_i[4])}  Mz={_fmt_knm(f_i[5])} kN·m")
        else:
            self._line_i.setText('i단  —')
        if f_j is not None and len(f_j) >= 6:
            self._line_j.setText(
                f"j단  N={_fmt_kn(f_j[0])} kN  "
                f"Vy={_fmt_kn(f_j[1])}  Vz={_fmt_kn(f_j[2])} kN  "
                f"My={_fmt_knm(f_j[4])}  Mz={_fmt_knm(f_j[5])} kN·m")
        else:
            self._line_j.setText('j단  —')
        # 진단 메시지 — 0 부재 등에서만 표시.
        if diagnostic:
            self._diag.setText(f"⚠ {diagnostic}")
            self._diag.show()
        else:
            self._diag.hide()
        # 미니 그래프 — stations: (n_st, 6) [N, Vy, Vz, T, My, Mz] (N, N·mm)
        # [2026-05-19] SFD/BMD 도 강·약축 dominant 자동 선택.
        # 이전: Vy/Mz 만 그려 보 자중처럼 약축으로 들어가는 케이스에 빈 그래프.
        # 변경: station 전체 절대값 최대인 성분(Vy vs Vz / My vs Mz) 선택,
        #       라벨에 성분 명시. 트리 V_max/M_max 와 일관.
        if stations is not None and len(stations) > 0:
            arr = np.asarray(stations, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[1] >= 6:
                # AFD: 축력 N
                self._afd.set_values(arr[:, 0] / 1000.0)        # N → kN
                # SFD: |Vy| 와 |Vz| 의 station 최대 절대값 비교 → dominant 선택
                vy = arr[:, 1]; vz = arr[:, 2]
                if float(np.max(np.abs(vz))) > float(np.max(np.abs(vy))):
                    self._sfd.set_title('SFD Vz')
                    self._sfd.set_values(vz / 1000.0)
                else:
                    self._sfd.set_title('SFD Vy')
                    self._sfd.set_values(vy / 1000.0)
                # BMD: |My| 와 |Mz| 비교 → dominant 선택
                my = arr[:, 4]; mz = arr[:, 5]
                if float(np.max(np.abs(my))) > float(np.max(np.abs(mz))):
                    self._bmd.set_title('BMD My')
                    self._bmd.set_values(my / 1e6)
                else:
                    self._bmd.set_title('BMD Mz')
                    self._bmd.set_values(mz / 1e6)
            else:
                self._afd.set_values(None)
                self._sfd.set_values(None)
                self._bmd.set_values(None)
        else:
            self._afd.set_values(None)
            self._sfd.set_values(None)
            self._bmd.set_values(None)
        self.adjustSize()

    def move_to_mouse(self, qt_pos: tuple):
        """마우스 Qt 좌표 옆 (offset 적용) 으로 이동. 부모 캔버스 영역 안에
        남도록 클램프한다.
        """
        parent = self.parentWidget()
        if parent is None:
            return
        x = int(qt_pos[0] + HOVER_OFFSET[0])
        y = int(qt_pos[1] + HOVER_OFFSET[1])
        max_x = parent.width() - self.width()
        max_y = parent.height() - self.height()
        x = max(0, min(x, max_x))
        y = max(0, min(y, max_y))
        self.move(x, y)

    # ── Phase 10: 드래그 이동 ───────────────────────────────
    def mousePressEvent(self, ev):
        """클릭 시작 — 드래그 anchor 기록."""
        if ev.button() == Qt.LeftButton:
            self._drag_anchor = ev.globalPos() - self.frameGeometry().topLeft()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        """드래그 — 부모 영역 안으로 클램프하며 이동."""
        anchor = getattr(self, '_drag_anchor', None)
        if anchor is None or not (ev.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(ev)
            return
        parent = self.parentWidget()
        if parent is None:
            super().mouseMoveEvent(ev)
            return
        # 글로벌 좌표 → 부모 좌표
        target_global = ev.globalPos() - anchor
        parent_origin = parent.mapToGlobal(parent.rect().topLeft())
        x = target_global.x() - parent_origin.x()
        y = target_global.y() - parent_origin.y()
        max_x = parent.width() - self.width()
        max_y = parent.height() - self.height()
        x = max(0, min(x, max_x))
        y = max(0, min(y, max_y))
        self.move(x, y)
        ev.accept()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_anchor = None
            ev.accept()
            return
        super().mouseReleaseEvent(ev)
