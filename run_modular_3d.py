"""modular_3d 실행 진입점 (PyInstaller 빌드용).

`python -m modular_3d` 와 동등.
"""
import os
import sys

# Qt 플랫폼 플러그인 경로 자동 설정
try:
    import PyQt5
    _pyqt5_dir = os.path.dirname(PyQt5.__file__)
    _plugin_path = os.path.join(_pyqt5_dir, "Qt5", "plugins")
    if os.path.isdir(_plugin_path):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugin_path, "platforms")
        os.environ["QT_PLUGIN_PATH"] = _plugin_path
except ImportError:
    pass

from modular_3d.ui.main_3d import main
main()
