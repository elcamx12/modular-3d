"""중립 유틸 패키지 — ui/render/analysis 어디에도 의존하지 않는 도구.

[목적]
render ↔ ui 양방향 import 를 끊기 위해, 양쪽이 공유하는 순수 좌표·기하 유틸을
본 모듈에 모은다. ui/render 어디서든 안전하게 가져올 수 있다.
"""
