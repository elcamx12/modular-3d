"""modular_3d 실행 진입점 (더블클릭/PyInstaller 빌드용).

`python -m modular_3d` 와 동등하지만 my_project 루트에서 더블클릭으로 실행
가능하도록 분리. 모든 부트스트랩 로직(venv 가드, subst 가상드라이브,
WebEngine 환경변수) 은 modular_3d/_bootstrap.py 에서 중앙 관리.
"""
import os
import sys

# [2026-06-05 빌드배포 방어] 동결(frozen) exe 가 *다른 파이썬/아나콘다* 의
# 환경변수에 납치돼 stdlib(base64 등) 탐색이 깨지는 것을 방지한다. 팀원 PC 처럼
# PYTHONHOME/PYTHONPATH 가 설정돼 있으면 번들 대신 외부에서 모듈을 찾다
# "No module named base64" 류로 죽는다. 가장 먼저 비운다. (근본 차단은 동봉한
# 실행 배치 파일이 인터프리터 초기화 *전에* 비우는 것이고, 여기는 보조 방어막.)
if getattr(sys, "frozen", False):
    for _v in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        os.environ.pop(_v, None)
    os.environ.setdefault("PYTHONNOUSERSITE", "1")
    # sys.path 에 번들(_MEIPASS) 밖 경로가 끼어들었으면 제거 — 외부 stdlib 오염 차단.
    _mei = getattr(sys, "_MEIPASS", None)
    if _mei:
        _mei_abs = os.path.abspath(_mei)
        sys.path[:] = [p for p in sys.path
                       if (not p) or os.path.abspath(p).startswith(_mei_abs)]

# 패키지 상위 디렉토리(=본 파일 위치) 를 sys.path 에 추가
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from modular_3d._bootstrap import bootstrap
bootstrap(project_root=_here)

from modular_3d.ui.main_3d import main
main()
