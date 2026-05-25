"""python -m modular_3d 진입점.

모든 부트스트랩 로직(venv 가드, subst 가상드라이브, WebEngine 환경변수,
QtWebEngineWidgets 선등록) 은 modular_3d/_bootstrap.py 에서 중앙 관리.
본 파일은 그 부트스트랩 호출 후 main() 실행만.

같은 부트스트랩을 사용하는 다른 진입점: run_modular_3d.py
"""
import os
import sys

# 패키지 상위 디렉토리를 sys.path 에 추가 (직접 실행 대응)
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_pkg_dir)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from modular_3d._bootstrap import bootstrap
bootstrap()

from modular_3d.ui.main_3d import main
main()
