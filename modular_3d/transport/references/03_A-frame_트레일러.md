# A-frame 트레일러 — PC 벽체 패널 세워서 운송

## 출처
- Wells Concrete "Precast Concrete Transportation & Shipping Trailers" (https://www.wellsconcrete.com/about/news-insights/precast-shipping-trailers/)
- VicRoads (호주 빅토리아주) "Guide to restraining concrete panels and beams" (https://nationalprecast.com.au/wp-content/uploads/2015/06/VicRoads-restraint-guide.pdf)
- PCI Sandwich Wall Panels Guide (https://www.pci.org/PCI_Docs/Design_Resources/Misc/Sandwich%20Wall%20Panels%20Guide.pdf)
- Beardown Logistics "Best Practices for Concrete Precast Structure Hauling"

## 신뢰도
★★ — 산업협회·전문 운송사 자료

## 핵심 원리: 왜 세워서 운송하나?

PC 벽체 패널은 **세워서(on-edge)** 운송하는 게 표준 관행:
1. **휨 응력 최소화**: 눕히면 자중 처짐(self-weight deflection)이 발생해 패널 균열·파손 위험
2. **트럭 폭 한도 활용**: 패널 길이(9m) > 트럭 폭(3m). 눕히면 폭 방향 1매만 가능, 세우면 두께 방향 N매
3. **하역 효율**: 세운 채로 그대로 양중·설치 가능

## A-frame 트레일러 구조

```
        ╱│
       ╱ │  ← A형 받침대 (경사 30~60°)
      ╱  │      벽체 패널을 양쪽에서 기대어 세움
   ╱╱   │
  ╱     │
 [트레일러 적재함]
```

- 양쪽 경사면(A자 모양)에 벽체 패널을 **30~60° 각도로 기댄 채** 결박
- 각 면당 N매씩, 총 양쪽 합쳐 10~20매
- 적재중량 한도 내에서 매수 결정

## 제원 (표준)

| 항목 | 수치 |
|------|-----|
| 적재함 길이 | 12m |
| 적재함 폭 | 3.0m |
| A-frame 높이 | 약 3m (트레일러 위) |
| 적재중량 | 24t |

## 결박 사양 (VicRoads)

- 결박벨트 또는 체인 (8mm 또는 10mm 운송 체인 + 턴버클)
- 패널 각도 30~60° 유지
- 결박점이 패널 지지 영역과 일치해야 균열 방지

## 표준 PC 벽체 패널 사양 (Wells Concrete)

- 길이 30ft (9.1m), 약 10t
- 특수 광폭 패널: 최대 15t
- 본 시뮬레이터 디폴트: 9m × 3m × 0.15m, 1.1t/매

## 본 코드에서 사용된 값

`data/trucks.json` 의 A-frame 트레일러 항목:
- A-frame 트레일러: 길이 12000mm, 폭 3000mm, 적재 24000kg
- truck_type: "aframe" (벽체 패널 전용)

`src/packer.py` 에서 `Panel.kind == "wall"` 인 경우 세워서 적재:
- 폭 방향에 두께 N매 (트럭 폭 ÷ (패널 두께 + 더니지))
- 길이는 트럭 길이 안에 들어가는지 검사
- 높이는 패널 높이가 트럭 높이 한도 안에 들어가는지 검사
