# transport/data — 패키지 내장 카탈로그

본 디렉토리는 운송 시뮬레이션의 **패키지 내장(고정) 카탈로그**다. 모든
사용자가 공통으로 보는 기본값으로, 프로젝트별 편집은 별도 위치
(`<프로젝트루트>/transport_config/`)에서 오버라이드한다 (Phase 1 catalog_io
구현 시 적용).

## 파일

- **trucks.json** — 트럭 카탈로그 5종 (운송프로그램 원본 + curb_weight_kg, trailer_length_mm 추정 디폴트 + active 플래그)
- **road_limits.json** — 도로 한도 3종 (광로/일반도로/이면도로)

## 운송프로그램 원본과의 차이

1. **trucks.json 신규 필드**
   - `curb_weight_kg`: 트럭 자체 중량 (kg). 도로 한도 비교 시 화물 무게에 더해 GVW 산출.
     운송프로그램 원본은 단순화로 화물만 비교했으나(B-1) 우리는 정밀화함 (사용자 결정 ⑨-3).
   - `trailer_length_mm`: 트레일러 자체 길이 (mm). 도로 한도 비교 시 화물 길이에 더해 전장 산출 (B-12 정밀화).
   - `active`: 패킹 후보 포함 여부. A-frame 트럭은 원본 코드에서 wall 패널 호환표에서 빠져 있어 (B-4) `active=false` 로 둔다.
   - `note`: 각 트럭 디폴트값의 근거·메모 (사람용).

2. **road_limits.json**: 원본 그대로 (수정 없음).

3. **sections.json 제외**: 우리 단면 카탈로그(`카탈로그/sections.py` 의 `SHS_CATALOG`)가 단일 진실원이므로 운송 측 sections.json은 가져오지 않는다. 어댑터(Phase 3) 가 우리 `SHSSection` → 운송 `Section` 으로 변환.

## 차종별 디폴트 추정치 근거

차체중량·트레일러길이는 한국 시판 트레일러 카탈로그 평균값을 토대로 한 추정 디폴트이며, 실측 데이터가 들어오면 운송탭 UI 에서 사용자가 직접 수정 가능 (Phase 9 의 카탈로그 편집 다이얼로그).
