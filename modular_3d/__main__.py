"""python -m modular_3d 로 실행 가능하게 하는 엔트리."""
import os, sys

# Qt 플랫폼 플러그인 경로 자동 설정 (conda env activate 없이 실행 시 대응)
try:
    import PyQt5
    _pyqt5_dir = os.path.dirname(PyQt5.__file__)
    _plugin_path = os.path.join(_pyqt5_dir, "Qt5", "plugins")
    if os.path.isdir(_plugin_path):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugin_path, "platforms")
        os.environ["QT_PLUGIN_PATH"] = _plugin_path
except ImportError:
    pass

# 패키지 상위 디렉토리를 sys.path에 추가 (직접 실행 대응)
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_pkg_dir)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from modular_3d.ui.main_3d import main
main()
