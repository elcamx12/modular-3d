# Modular 3D — PC 모듈러 구조해석

졸업 종설 (2026, 아주대학교 건축공학과) — 학부 수준 단순화 모델로 PC 모듈러 건물의 구조해석 자동화.

**최근 갱신 (2026-05-19)** — 물량산출 탭 가독성 개선 (응력비 트리 = 구조해석 트리 100% 공유), 기둥 K 층구간 DP 최적분할, 분할 부재 1 본 통합, 한글 라벨 일관성, 코어 강재 격리 보장. 자세한 내용은 `설명서.md` §12-D ~ §12-H.

## 개요

PyQt5 + vispy 3D 뷰어로 모듈러 부재를 격자에 배치하고 (F5), OpenSeesPy 로 3D 프레임 해석 (F6) 까지 한 번에 처리하는 데스크톱 프로그램.

- **부재 타입 8 종**: 모듈, 바닥패널, 구조벽, 캔틸레버 보·슬래브, 중간보·기둥, 수직 3층 모듈
- **다층 자동 적층**: 1 층 디자인을 N 층으로 자동 복제
- **5 케이스 해석**: D+L (수직), Ex/Ey (등가정적 지진), Wx/Wy (풍)
- **변형 형상 / 컨투어 / 응력비 시각화**

## 설치

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

## 실행

```bash
python -m modular_3d
```

또는

```bash
python -m modular_3d.ui.main_3d
```

## 기본 키 매핑

| 키 | 모드 |
|---|---|
| F5 | 평면 정렬 도크 (placement) — 격자에 부재 배치 |
| F6 | 구조해석 도크 — OpenSees 모델 빌드 + 5 케이스 해석 |
| 1~8 | 부재 타입 (F5 캔버스 안에서) |
| R / V | 회전 / 앵커 변경 (F5 PREVIEW 중) |
| M | 자유 이동 (F5 SELECTED 시) |
| Ctrl+Z | Undo |

## 구조

```
modular_3d/
  model/      데이터 모델 (Component, Scene)
  io/         scene_io (JSON 저장·불러오기)
  render/     viewer, mesh_builder, snap
  ui/         controls + alignment + analysis_panel + main_3d
  analysis/   topology, ops_builder, ops_solver, load_calculator,
              section_design, strength_check, quantity_takeoff
```

## 주요 가정 (학부 수준 단순화)

| 항목 | 값 |
|---|---|
| 강재 단면 | SHS 200×200×8 (모든 부재 동일) |
| 슬래브 | 200 mm 합성데크 (강체 다이어프램) |
| 접합 | 컴포넌트 내부 강접합 / 컴포넌트 사이 핀 |
| 기초 | 6 DOF 완전 고정 |
| 활하중 | 2.0 kN/m² (KBC 2022 주거) |
| 지진 | 등가정적 R=8.0 (모듈러 단일 시스템) |
| 풍 | 기본풍속 35 m/s |

상세 가정·임계값: `modular_3d/analysis/가정사항.md`, `modular_3d/JOINT_POLICY.md`

## 검증

자동 회귀 테스트 — `regression.py --capture` / `--compare`. 본 세션 종료 시 평형 오차 0.00 % (scene3 / synth_b / synth_c) 통과.

## 상태

- ✅ 평형 0.00% (3 시나리오)
- ✅ F5 / F6 정상 동작
- ⚠️ 외부 도구 (SAP2000 등) 와의 부재력 절대값 비교는 미실시
- ⚠️ R 계수, 가상 RC 코어 위치, 단일 단면 가정 등 학부 수준 단순화 — 실설계 시 재검토 필요

## 라이선스

졸업 종설 학습용. 외부 사용 시 사용자께 문의.
