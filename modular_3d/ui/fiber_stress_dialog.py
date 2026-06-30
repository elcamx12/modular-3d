# -*- coding: utf-8 -*-
"""코어 fiber 응력 다이어그램 다이얼로그 (matplotlib).

코어벽을 선택하면 그 벽 단면의 fiber 위치별 콘크리트·철근 응력을 그린다.
철근 응력이 항복강도(fy) 이상인 fiber 는 빨강으로 표시해 항복 위치를 보여준다.
"""
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QComboBox, QLabel

try:
    from matplotlib.backends.backend_qt5agg import (
        FigureCanvasQTAgg as _FigureCanvas)
    from matplotlib.figure import Figure
    _MPL = True
except Exception:
    _MPL = False


class FiberStressDialog(QDialog):
    """코어벽별 fiber 응력 다이어그램.

    wall_data: {label: {'widths':[...], 'conc':[MPa...], 'steel':[MPa...], 'fy':MPa}}
    """

    def __init__(self, wall_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle('코어 fiber 응력 다이어그램')
        self.resize(700, 560)
        self._data = wall_data or {}
        lay = QVBoxLayout(self)

        if not self._data:
            lay.addWidget(QLabel(
                '표시할 코어 응력 데이터가 없습니다 '
                '(코어 포함 모델로 구조해석을 먼저 실행하세요).'))
            return
        if not _MPL:
            lay.addWidget(QLabel('matplotlib 미설치 — 다이어그램 표시 불가.'))
            return

        self._combo = QComboBox()
        for label in self._data:
            self._combo.addItem(label)
        self._combo.currentTextChanged.connect(self._redraw)
        lay.addWidget(self._combo)

        self._fig = Figure(figsize=(6.6, 4.9))
        self._canvas = _FigureCanvas(self._fig)
        lay.addWidget(self._canvas)
        self._redraw(self._combo.currentText())

    def _redraw(self, label):
        d = self._data.get(label)
        if not d:
            return
        widths = d.get('widths') or []
        conc = d.get('conc') or []
        steel = d.get('steel') or []
        fy = float(d.get('fy', 400.0))
        # fiber 중심 위치(길이방향 누적)
        x, acc = [], 0.0
        for w in widths:
            x.append(acc + w / 2.0)
            acc += w
        n = min(len(x), len(conc), len(steel))
        if n == 0:
            return
        x = x[:n]
        conc = conc[:n]
        steel = steel[:n]
        bw = [max(widths[i] * 0.8, 1.0) for i in range(n)]

        self._fig.clear()
        ax1 = self._fig.add_subplot(211)
        ax1.bar(x, conc, width=bw, color='#7591B5')
        ax1.set_ylabel('Concrete (MPa)')
        ax1.set_title(label)
        ax1.grid(True, alpha=0.3)

        ax2 = self._fig.add_subplot(212)
        cols = ['#C0392B' if abs(s) >= fy * 0.99 else '#2E7D32' for s in steel]
        ax2.bar(x, steel, width=bw, color=cols)
        ax2.axhline(fy, color='#C0392B', ls='--', lw=0.8)
        ax2.axhline(-fy, color='#C0392B', ls='--', lw=0.8)
        ax2.set_ylabel('Steel (MPa)')
        ax2.set_xlabel('Fiber position along wall (mm)')
        ax2.grid(True, alpha=0.3)

        self._fig.tight_layout()
        self._canvas.draw()
