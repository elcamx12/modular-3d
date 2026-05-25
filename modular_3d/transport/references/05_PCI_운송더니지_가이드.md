# PCI MNL-122 / Wells Concrete — 운송·하역 더니지 가이드

## 출처
- PCI Architectural Precast Concrete Manual MNL-122 (https://www.pci.org/PCI_Docs/PCI_Northeast/Technical_Resources/APC_MNL-122_Chapter6.pdf) — 6장 운송·하역
- PCI Industry Handbook 6th Edition (https://www.rexresearch1.com/ConcreteManufacture2Library/PCIDesignHandbkPrecastPrestressedConcreteMartin.pdf)
- Wells Concrete "Precast Concrete Transportation" (https://wells.build/resources/expert-insights/precast-concrete-transportation/)

## 신뢰도
★★ — 산업협회(PCI = Precast/Prestressed Concrete Institute) 표준

## 더니지(Dunnage)란

운송 시 패널과 패널, 패널과 트럭 짐칸 사이에 끼우는 **목재 받침대**.

### 역할
1. **결박벨트 통과 공간** 확보 (벨트가 패널 사이로 지나가야 결박 가능)
2. **지게차 포크 진입 공간** (하역 시 포크가 들어갈 수 있게)
3. **패널 모서리 보호** (운송 진동·충격으로 인한 모서리 파손 방지)
4. **수평 유지** (적층 시 매수마다 수평 보정)

## 표준 더니지 사양

| 항목 | 표준 범위 | 본 시뮬레이터 디폴트 |
|------|---------|---------------------|
| **패널 사이 더니지 두께** | 75~100mm | **100mm** |
| **적층 단 사이 더니지 두께** | 75~100mm | **100mm** |
| **재질** | 소프트우드 (소나무·더글라스 퍼) | 소나무 가정 |
| **단면** | 100×100mm 각재 (또는 100×150mm) | 100×100mm |
| **길이** | 트럭 폭과 일치 (보통 3000mm) | 3000mm |
| **단당 사용 개수** | 2~3개 (양 끝 + 중간) | 3개 |

## 결박 작업 공간

PCI 권장: 트럭 적재함 양 끝에 **150~200mm 결박 작업 공간** 확보 → 결박벨트 후크 위치.

## 본 코드에서 사용된 값

`src/models.py`의 `SpacingParams` dataclass:
- `panel_gap_mm = 100` (패널 사이 더니지)
- `truck_edge_clearance_mm = 200` (양끝 결박 공간)
- `dunnage_thickness_mm = 100` (적층 단 사이)

사용자가 사이드바 슬라이더로 50~200mm 범위에서 조정 가능.

## 더니지 무게 계산

본 자료의 단면(100×100mm) × 트럭 폭(3000mm) × 단당 3개 = 단별 더니지 부피.
목재 밀도는 [07_목재더니지_밀도_무게.md](07_목재더니지_밀도_무게.md) 참조.
