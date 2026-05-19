"""최소 이벤트 진단 — vispy 이벤트가 아예 안 오는지 확인."""
import os, sys

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

from vispy import scene
from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

canvas = scene.SceneCanvas(keys='interactive', show=True, bgcolor='black', size=(800, 600))
view = canvas.central_widget.add_view()
view.camera = scene.TurntableCamera(distance=5, up='z')

# 큐브 하나 띄우기
from vispy.scene import visuals
import numpy as np
cube = visuals.Box(width=1, height=1, depth=1, color='cyan', parent=view.scene)

print('='*50)
print('  이벤트 진단 — 아래 동작을 하고 콘솔 출력 확인')
print('  1) 3D 영역 클릭')
print('  2) 키보드 아무키 누르기')
print('  3) 마우스 이동')
print('  4) 왼클릭, 오른클릭, 가운데클릭')
print('='*50)

@canvas.events.key_press.connect
def on_key(event):
    print(f'[KEY_PRESS] key={event.key}, text={repr(event.text)}')

@canvas.events.mouse_press.connect
def on_press(event):
    print(f'[MOUSE_PRESS] button={event.button}, pos={event.pos}')

@canvas.events.mouse_move.connect
def on_move(event):
    print(f'[MOUSE_MOVE] pos={event.pos}, buttons={event.buttons}')

@canvas.events.mouse_release.connect
def on_release(event):
    print(f'[MOUSE_RELEASE] button={event.button}')

@canvas.events.mouse_wheel.connect
def on_wheel(event):
    print(f'[MOUSE_WHEEL] delta={event.delta}')

print('윈도우가 떴으면 위 동작을 해주세요. Ctrl+C로 종료.')
app.exec_()
