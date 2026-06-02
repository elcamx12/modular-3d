"""calculate_loads 정밀 프로파일 — 하중 계산의 진짜 병목 함수 찾기.

사용:
  python -m modular_3d.analysis.tests.prof_loads "C:/.../scene7.json"

calculate_loads 를 3회 돌려 자체시간(tottime) 상위 함수를 출력한다.
"""
import cProfile
import pstats
import sys

from modular_3d.io.scene_io import load_scene
from modular_3d.analysis.topology import build_analysis_model
from modular_3d.analysis.load_calculator import calculate_loads


def main() -> int:
    path = sys.argv[1]
    scene, _nf = load_scene(path)
    am = build_analysis_model(scene)
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(3):
        calculate_loads(scene, am)
    pr.disable()
    st = pstats.Stats(pr)
    st.sort_stats('tottime')
    print("===== calculate_loads tottime 상위 18 (3회 누적) =====")
    st.print_stats(18)
    return 0


if __name__ == "__main__":
    sys.exit(main())
