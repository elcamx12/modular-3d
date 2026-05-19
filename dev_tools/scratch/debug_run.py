"""이벤트 디버그용 실행 스크립트 (옛 _legacy_scratch — 미사용)."""
import os, sys

# 2026-05-18 dev_tools/scratch 로 이동: 운영 패키지 부모 = parents[2] = my_project.
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_my_project = os.path.dirname(os.path.dirname(_pkg_dir))
if _my_project not in sys.path:
    sys.path.insert(0, _my_project)

_qt_plugin_path = os.path.join(_my_project, 'venv',
                               'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
if os.path.isdir(_qt_plugin_path):
    os.environ['QT_PLUGIN_PATH'] = os.path.abspath(_qt_plugin_path)

import vispy
vispy.use('pyqt5')

from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt
from modular_3d.model import Scene
from modular_3d.render.viewer import Viewer3D
from modular_3d.ui.ui_panel import DimensionInputPanel, StatusBarManager
from modular_3d.ui.controls import Controller


class DebugWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('[DEBUG] 모듈러 부재 스터디')
        self.resize(1400, 900)

        self._scene = Scene()
        self._viewer = Viewer3D()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        canvas_widget = self._viewer.get_native_widget()
        layout.addWidget(canvas_widget, stretch=1)

        self._dim_panel = DimensionInputPanel()
        layout.addWidget(self._dim_panel)
        self.setCentralWidget(central)

        self._status_mgr = StatusBarManager(self.statusBar())
        self._controller = Controller(
            self._viewer, self._dim_panel, self._status_mgr, self._scene,
        )

        # 디버그: 모든 vispy 이벤트를 로깅
        def debug_key(event):
            key = getattr(event, 'key', None)
            text = getattr(event, 'text', '')
            print(f'[KEY] key={key}, type(key)={type(key)}, text={repr(text)}')
            self._controller.on_key_press(event)

        def debug_mouse_move(event):
            # 너무 많이 출력되므로 10번에 1번만
            if not hasattr(self, '_move_count'):
                self._move_count = 0
            self._move_count += 1
            if self._move_count % 30 == 1:
                print(f'[MOVE] pos={event.pos}, button={getattr(event, "button", None)}, '
                      f'buttons={getattr(event, "buttons", None)}, '
                      f'state={self._controller._state}')
            self._controller.on_mouse_move(event)

        def debug_mouse_press(event):
            print(f'[PRESS] button={event.button}, pos={event.pos}, '
                  f'state={self._controller._state}')
            self._controller.on_mouse_press(event)

        self._viewer.canvas.events.key_press.connect(debug_key)
        self._viewer.canvas.events.mouse_move.connect(debug_mouse_move)
        self._viewer.canvas.events.mouse_press.connect(debug_mouse_press)

        canvas_widget.setFocusPolicy(Qt.StrongFocus)
        canvas_widget.setFocus()

        # 치수 패널 시그널 디버그
        self._dim_panel.confirmed.connect(
            lambda d: print(f'[DIM_CONFIRMED] {d}'))
        self._dim_panel.cancelled.connect(
            lambda: print(f'[DIM_CANCELLED]'))

        print('=== 디버그 모드 시작 ===')
        print('1키, 마우스 클릭, Enter 등을 눌러서 콘솔 출력을 확인하세요.')


if __name__ == '__main__':
    app = QApplication.instance() or QApplication(sys.argv)
    win = DebugWindow()
    win.show()
    sys.exit(app.exec_())
