# -*- coding: utf-8 -*-
"""Pushover 역량곡선 표시 다이얼로그 (matplotlib).

solve_pushover 가 반환한 (상부변위 mm, 밑면전단 N) 점 목록을 방향별로 그린다.
matplotlib 미설치 시에는 곡선 데이터를 텍스트로 폴백 표시한다.
"""
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel

try:
    from matplotlib.backends.backend_qt5agg import (
        FigureCanvasQTAgg as _FigureCanvas)
    from matplotlib.figure import Figure
    _MPL = True
except Exception:
    _MPL = False


class CapacityCurveDialog(QDialog):
    """방향별 역량곡선(밑면전단-상부변위) 그래프.

    curves: {'X': [(disp_mm, shear_N), ...], 'Y': [...]}.
    """

    def __init__(self, curves, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Pushover 역량곡선')
        self.resize(660, 500)
        lay = QVBoxLayout(self)

        if not _MPL:
            # 폴백 — 정점값만 텍스트로.
            lines = ['matplotlib 미설치 — 곡선 데이터 요약:']
            for name, pts in curves.items():
                if pts:
                    dmax, vmax = pts[-1]
                    lines.append(f'  {name}방향: 최대 변위 {dmax:.1f} mm, '
                                 f'밑면전단 {vmax / 1000:.0f} kN')
            lay.addWidget(QLabel('\n'.join(lines)))
            return

        fig = Figure(figsize=(6.2, 4.6))
        canvas = _FigureCanvas(fig)
        ax = fig.add_subplot(111)
        for name, pts in curves.items():
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] / 1000.0 for p in pts]   # N → kN
            ax.plot(xs, ys, marker='.', markersize=4, label=f'{name}-dir')
        # 한글 폰트 깨짐 방지 — 축/제목은 영문 표기.
        ax.set_xlabel('Top displacement (mm)')
        ax.set_ylabel('Base shear (kN)')
        ax.set_title('Pushover capacity curve')
        ax.grid(True, alpha=0.3)
        if any(curves.values()):
            ax.legend()
        fig.tight_layout()
        lay.addWidget(canvas)
