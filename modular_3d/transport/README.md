# modular_3d.transport — 모듈러 운송 시뮬레이션

졸업종설 모듈러 평면 설계 결과를 **트럭 운송 회차·운임**으로 환산하는 패키지.
운송프로그램 원본(Song-Jung-Hun/-3-) 의 도메인 로직을 우리 씬·해석 시스템에
이식·일반화했다.

## 아키텍처 — 8 단계 파이프라인

```
[1] 씬 편집 (3D GUI)
  ↓
[2] 구조해석 (OpenSeesPy)        ── 비용 큼 (자동 트리거 X)
  ↓
[3] 단면 산정 (DesignResult)     ── 정책별(1·2·3종)
  ↓
[4] 층구간 분할 (n_segments)
  ↓
[5] 비내력벽 분류 (wall_classifier)
  ↓
[6] 어댑터 변환 (build_transport_input)  ── 씬 → 운송 Module/Panel
  ↓
[7] 패킹 (pack_items)            ── FFD 빈 패킹, 회차 산정
  ↓
[8] 운임 + 시각화 (economics / visualizer)
```

각 단계는 입력 fingerprint 를 캐시 키로 한다(`cache.py`). 옵션을 살짝 바꿔도
변경된 단계 아래만 재계산한다(분석 ⑧ 무효화 매트릭스 — `TransportTab._wire_auto_recompute`).

## 모듈 구성

| 파일 | 책임 |
|---|---|
| `models.py` | 도메인 데이터모델 — `Module` `Panel` `Truck` `RoadClass` `Section` `WallSegment` `SpacingParams`. frozen dataclass + 런타임 검증 |
| `limits.py` | `can_carry` — 단일 아이템 × 트럭 × 도로 4조건(길이·폭·높이·중량) 검사 |
| `packer.py` | `pack_items` — 모듈/패널 → 회차(Trip) 산정. `recheck_trip_with_truck` 트럭 교체 검증 |
| `adapter.py` | `build_transport_input` — 우리 씬+해석결과 → 운송 입력. 단면 룩업·extra_weight·라벨·source_index |
| `wall_classifier.py` | 비내력벽 내부/외부 면 분류 + 면별 단위중량 가중평균 (분석 ⑥) |
| `economics.py` | `compute_economics` — 회차별·총 운임. 거리(km) 기반 두 방식: 요금표 자동 / 트레일러별 km단가 |
| `visualizer.py` | `draw_top_view` / `draw_rear_view` — Plotly 적재 도식 |
| `cache.py` | `TransportCache` — 8단계 lazy memoization + `invalidate_from` |
| `manual_sim.py` | `run_manual_sim` — 수동 단일회차 가능성 판정(탐색·가설) |
| `catalog_io.py` | 트럭·도로 카탈로그 2계층(내장 + 프로젝트) 로드/저장 |
| `data/*.json` | 내장 트럭(`trucks.json`)·도로 한도(`road_limits.json`) 카탈로그 |
| `references/*.md` | 운송 도메인 참고문서 (운송탭 [📖 참고자료]) |

UI 는 `modular_3d/ui/transport_panel.py`(운송탭 본체) +
`transport_catalog_dialog.py`(트럭 관리) + `transport_temp_cargo_dialog.py`(임시
화물) + `transport_references_dialog.py`(참고자료).

## 카탈로그 2계층 정책

1. **내장(builtin)** `transport/data/*.json` — 읽기 전용 공통 기본값.
2. **프로젝트(project)** `<project_root>/transport_config/*.json` — 사용자
   추가·편집. 같은 name 은 프로젝트가 내장을 덮어씀(overlay).

도로 한도는 코드 JSON 만 — UI 편집 차단(C-2 결정). 트럭은 UI 에서 추가/편집/
복제/삭제 가능. 내장 트럭은 복제만(clone-on-edit).

## 도메인 용어

- **회차(Trip)**: 트럭 1대의 1회 운송. `cargo_weight`(화물), `gross_weight`(=화물+차체 GVW), `utilization`(중량·길이 적재율 중 큰 값).
- **종속 패널(dependent floor)**: 바닥 패널에 벽 세그먼트가 매달린 형태. `wall_segments` 0/1/2/3/4 개로 순수 floor / L자 / ㄷ자 / 3·4면 일반화(A-1).
- **광폭(wide)**: 폭 3.0m 초과 모듈 → 확장형(extendable) 트레일러 필요.
- **적재율(utilization)**: 중량적재율 = 화물/트럭한도, 길이적재율 = 사용길이/유효길이.
- **override**: 자동 패킹 결과의 특정 회차 트럭을 사용자가 강제 교체(미세 조정).
- **수동 시뮬레이션**: 자동 결과와 독립적으로 임의 화물+트럭+도로의 단일회차 가능성 탐색(가설 검증).

## 단위 규약

- 길이: mm, 중량: kg.

## 원본과의 주요 차이 (B-시리즈 정정)

- **B-11**: `Module.weight` 보 길이 2배 오버 정정 — `8(w+l)` → `4(w+l)`.
- **B-2**: L자 벽두께 ≠ 바닥두께 분리 — `WallSegment.thickness_mm` 신설.
- **B-3**: `recheck_trip_with_truck` 적층 무게 합산 누락 정정.
- **B-25**: `cargo_weight` vs `gross_weight` 의미 분리(차체 중량 포함 GVW).
- **B-15**: JSON 로드 시 `__post_init__` 런타임 검증.
- **B-21**: `pack_items` 미캐싱 → `TransportCache` 로 해결.
- **A-1**: lshape 단일면 한계 → `wall_segments` 다면 종속 일반화.

원본 Streamlit 앱(app.py) 의 UI 버그(B-19 단면 silent fallback, B-23 dict 중복,
B-27 사이드바 도로표, B-28 lshape 폼)는 해당 UI 를 포팅하지 않아 무관 — 우리는
씬 기반 자동 변환 + 자체 PyQt UI 를 사용한다.

## 테스트

```
venv/Scripts/python.exe -m pytest modular_3d/transport/tests/ -q
```

`test_integration.py` 가 씬→어댑터→패커→운임 E2E + 캐시 hit/miss + 수동 시뮬을 검증.
