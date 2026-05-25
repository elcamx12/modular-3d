"""modular_3d 실행 진입점 (더블클릭/PyInstaller 빌드용).

`python -m modular_3d` 와 동등하지만 my_project 루트에서 더블클릭으로 실행
가능하도록 분리. 모든 부트스트랩 로직(venv 가드, subst 가상드라이브,
WebEngine 환경변수) 은 modular_3d/_bootstrap.py 에서 중앙 관리.
"""
import os
import sys

# 패키지 상위 디렉토리(=본 파일 위치) 를 sys.path 에 추가
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from modular_3d._bootstrap import bootstrap
bootstrap(project_root=_here)

from modular_3d.ui.main_3d import main
main()
