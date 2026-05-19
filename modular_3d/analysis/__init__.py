"""구조해석 패키지 — OpenSeesPy 기반 (Phase 5 이후).

핵심 모듈:
- constants.py       : 재료/단면/하중 상수
- topology.py        : Scene → AnalysisModel (노드 + 부재 토폴로지)
- load_calculator.py : 자중 / 슬래브 사하중 / 활하중 분배
- ops_builder.py     : AnalysisModel → OpenSees 3D 프레임 모델 (노드 + 요소 + 인터페이스 핀 + 다이어프램)
- ops_solver.py      : 정적 해석 — D+L (1.2D+1.6L) + 등가정적 지진(Ex/Ey) + 풍(Wx/Wy) 5 케이스

가정사항·디폴트 값은 `가정사항.md` 참조.
"""
