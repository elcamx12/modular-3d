"""운송 시뮬레이션 서브패키지.

본 패키지는 외부 운송프로그램(Song-Jung-Hun/-3-, Streamlit 기반)을
modular_3d 안에 병합한 결과물이다. 우리 씬의 부재 데이터를 운송용
입력으로 변환·패킹·시각화·운임계산까지 수행한다.

[디렉토리]
- data/                 — 트럭 카탈로그 (JSON)
- models.py             — 데이터클래스 (Phase 1)
- catalog_io.py         — 카탈로그 로드/저장 (Phase 1)
- limits.py             — 현장 제한(SiteLimit) + 트럭 한도 검사 (Phase 2)
- packer.py             — FFD 빈팩 (Phase 2)
- adapter.py            — 우리 씬 → 운송 입력 변환 (Phase 3)
- wall_classifier.py    — 비내력벽 외부/내부 자동판별 (Phase 3)
- visualizer.py         — top/rear 도면 (Phase 4)
- economics.py          — 운임 계산 (Phase 5)

[병합 진실원]
- 부재 라벨: 구조해석탭 comp_meta 그대로 재사용
- 슬래브 두께: 150mm 통일 (해석·시각·운송)
- 비내력벽: 내부 30 / 외부 55 kg/m² 면별 한 면 면적 합산
- 단면: 우리 SHS_CATALOG 단일 진실원 (sections.json 미사용)

상세는 modular_3d/운송프로그램병합준비.md 참조.
"""

__all__ = []
