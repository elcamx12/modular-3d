# 코어벽 MVLEM_3D 비선형 전환(A안) — 충돌·수정 지점 종합

작성 2026-06-24. 본체(modular_3d)를 코어벽 MVLEM_3D + 전체 비선형 Pushover 로
전환할 때 깨지거나 손봐야 할 지점 전수 조사 결과(3갈래 심층 조사 종합).
백업: `_snapshots/2026-06-25_MVLEM_A전환전_modular_3d`.

---

## 0. 큰 그림 — 두 축을 동시에 바꿔야 함
- **해석 엔진**: 선형 1회 해석 → 비선형 반복(Newton)·점증 가력·변위제어 Pushover.
- **코어 모델**: wide-column(elastic) + 6DOF 고정 → MVLEM_3D(비선형, 변형 허용).
이 둘은 "코어=고정 강체"라는 같은 전제를 공유하므로 **반드시 짝지어** 바꿔야
한다. 한쪽만 바꾸면 과구속·특이행렬로 해석이 죽거나, 코어가 안 항복한다.

## 1. 치명 (안 고치면 해석 자체가 실패/무의미)

### 1-1. 코어 elastic 막대 경로 → MVLEM 단일화 (★T5 런타임 정정: 이중경로 아님)
**[2026-06-24 T5 런타임 확정]** 실제 씬(Ascene.json) build_analysis_model 결과 코어 부재
84개가 **전부 막대(beam 60 + column 24), shell 0·truss 0**. 즉 코어는 **단일 경로
(elasticBeamColumn)** 로만 모델링된다. ops_builder.py:373(ShellMITC4)·:394(Truss) 분기는
**도달불가 화석**(topology 가 shell/truss member 를 안 만듦). → 따라서 "이중경로 제거"는
불필요(없음). MVLEM 전환 시 걷어낼 것은 **코어벽 막대(core_column + core_*_runner)** 하나뿐.
코어슬래브(core_slab_beam·core_ceiling_runner)도 막대지만 MVLEM 대상은 코어'벽'뿐 → 슬래브는
별도 유지. shell/truss 화석 분기는 MVLEM 작업과 무관(정리 선택사항).

### 1-2. 코어 전노드 6DOF 고정 → 베이스만
`_step_fix_base_nodes`(ops_builder.py:494~540)가 role 'core_' 로 시작하는
**모든 노드를 6DOF 완전고정**. 그대로면 MVLEM 넣어도 벽이 굳어 wide-column 과 동일.
→ 코어 **베이스(최하단 z) 노드만** 고정하도록 재작성.

### 1-3. R09 master/slave 방향 모순
`joint_rules.py` apply_core_joint(2035~2208)·_split_core_lines(2216~2375)가
외부부재↔코어를 **코어=master(고정 전제), 외부=slave** 로 6DOF 강결합. 이 방향은
"코어가 고정이라 master 로 써야 충돌 없다"는 논리에 의존. 고정 풀면 → 고정 안 된
master 에 강결합 = 과구속·특이행렬. → master/slave 역전 또는 6DOF→병진(1,2,3)만 완화.

### 1-4. 해석 알고리즘 선형 고정
세 솔버(solve_vertical 511~520, solve_seismic 913~919, solve_wind 939~945)가
`algorithm('Linear')` + `integrator('LoadControl',1.0)` + `analyze(1)` 단발.
비선형이면 반복 자체가 안 일어남. → Newton(+ModifiedNewton/KrylovNewton 폴백)
+ 점증 N스텝 + 수렴 실패 시 스텝 분할 재시도. Pushover 는 DisplacementControl
(제어=최상층 다이어프램 master, dof=X/Y).

### 1-5. 선형 중첩 폐기
`solve_all_cases`(1078)가 기본 6성분(D,L,Ex,Ey,Wx,Wy)만 각 1회 풀고 **나머지
하중조합을 산술 합(선형 중첩)으로 합성**(_linear_combo 970, _pm_envelope 1042).
코드 주석(957~960)이 이미 "비선형 도입 시 즉시 무효, 조합별 직접해석 필요" 경고.
→ 하중조합별 직접 비선형 해석 루프로 재작성(해석 횟수↑, 성능 영향).

## 2. 높음 (결과·안정성)

### 2-1. P-Delta 부재
geomTransf 가 'Linear' 하나뿐(ops_builder.py:266). Pushover 는 2차효과·전도가
본질 → 없으면 연성·붕괴하중 과대평가, 항복 후 softening(하강곡선) 누락.
18층 사례일수록 치명. → 기둥·코어에 'PDelta'(큰 회전 예상 시 Corotational).

### 2-2. Penalty 핸들러 → 비선형 수렴 저해
세 솔버 모두 `Penalty(1e14)`(ops_solver.py:52,65). 선형 1회엔 무해하나 Newton
반복에서 1e14 가 강성행렬 대각 지배 → 코어 항복으로 강성 급락 시 조건수 악화·발산.
→ Transformation handler 전환(단, 같은 노드 다중구속 불가 → 기존 중복-slave 차단
로직 ops_builder.py:891,914,922 이 전제로 필요).

### 2-3. truss-only/z_route 노드 자동 회전 fix
회전강성 없는 노드 특이행렬 방지용 `fix(0,0,0,1,1,1)`(ops_builder.py:348,410,651)가
MVLEM_3D 노드(회전 DOF 사용)에 걸리면 모멘트 전달 차단. → MVLEM 노드 제외 가드.

### 2-4. 코어 단면력 추출 불일치
`_extract_member_forces`(ops_solver.py:180~238)·_extract_member_stations(315~366)가
모든 부재를 frame 12성분(양단 localForce) 전제로 뽑고, 코어는 "축력일정·휨미미"로
양단 2점만 표시. MVLEM_3D 는 벽 높이방향 다수 section 응력 반환 → 추출 경로·
호버 다이어그램(AFD/SFD/BMD) 을 벽 응력도로 재설계.

## 3. 중간 (후처리·표시)

- **물량**: 코어를 L×t×h 단일 직육면체로 부피 산출(quantity_takeoff.py:288~333),
  철근비 1.2% 일괄. 격자 다수 요소로 바뀌면 요소별 부피 합산·부위별 철근비로.
- **직렬화**: scene_io 가 dimensions(width/depth/height)만 저장, 로드 시 중앙점
  재생성. MVLEM 은 회전·앵커 반영 4모서리 격자좌표 필요 → 포맷 확장 +
  model_type('wide_column'/'mvlem_3d') 필드로 분기(하위호환).
- **코어 자중**: eleLoad 분포하중을 MVLEM 미지원 → truss 처럼 절점력 분배 경로.
- **횡력 패턴**: 기존 역삼각형 분포(_seismic_floor_distribution 677) 재사용,
  적분기만 변위제어로.
- **3D 메시**: build_component_class_meshes 가 comp.column 1개 전제(mesh_builder.py:519),
  build_core_mesh AABB → 회전 벽 과대표시. 4노드 폴리곤 벽면 분기 필요.
- **단면설계**: 코어는 이미 강재 설계 제외(CAT_RC_CORE) → 정책 유지, role/kind 필터만
  갱신. MVLEM 응력 기반 콘크리트 강도검토 모듈은 신규 개발 대상.

## 4. 낮음·향후
- **질량**: ops.mass 전무(grep 0건). Pushover 엔 불필요하나 모달 비례분포·시간이력
  전환 시 층질량 lumped 등록 필요(단위 N·mm·MPa 계 질량은 ton, 밀도 kg/m³→환산).
- **drift_check**: 다이어프램 master 변위 기준 — 코어 변형이 master 에 반영되는지 검증.
- **주석/코드 불일치**: 횡력 케이스 주석은 "Transformation" 이나 실제 Penalty 호출
  (ops_solver.py:909~913) → 정정.

## 5. 영향 없음 (안심)
운송(transport) 참조 없음 / 다층복제(multi_floor) dimensions 동일·투명 /
평가 어댑터(evaluation_adapter 216~225) 기하 입력값만 사용 / CoreSlab 슬래브
자동생성 영향 작음 / 단위계 N·mm·MPa 일관(Concrete02·Steel02 도입 무리 없음).

---

## 5-2. 데이터 흐름 영향 매트릭스 (2026-06-24 추적)

**상류(코어를 만드는 흐름)** — 수정 집중:
```
입력 → Core(형상값+ColumnData 화석) → scene_io(형상만 저장) → 다층복제
  → ①topology._extract_core(선 프레임) → ②ops_builder(elasticBeamColumn+전노드고정) → OpenSees
```
- ① topology: 코어를 'core_column'+runner 선 부재로 만듦. **AnalysisMember 자료구조가
  1D 보-기둥 전제 → MVLEM(다중 수직 fiber+전단스프링) 못 담음.** 새 멤버 kind/role 또는
  별도 표현 도입 필요 = 이번 전환의 핵심 난이도.
- ② ops_builder: elasticBeamColumn 등록 + _step_fix_base_nodes 전노드 고정 + elastic
  이중경로(shell/truss 분기는 도달불가 화석). MVLEM_3D 등록·base만 고정·화석 제거로.

**하류(결과 소비)** — 대부분 무관(코어를 '형상'/'이름표'로만 소비):
| 소비처 | 코어를 무엇으로 읽나 | MVLEM 영향 |
|---|---|---|
| 단면설계 | member_forces 읽되 CAT_RC_CORE 로 제외 | 무관(식별조건만 갱신) |
| 물량(콘크리트/철근) | Core.dimensions(L×t×h) 형상 직접 | 무관 |
| 물량(강재 제외) | component→member 매핑 | 무관 |
| 평가(유효면적) | Core.dimensions 면적 차감 | 무관 |
| 3D 형상 | get_world_corners 벽 패널 | 무관 |
| 층간변위(drift) | story_disp(다이어프램 master) | 코드 OK, 수치 검증 |
| **3D 코어 단면력 그래프** | member_stations(2,6)·member_forces | **데이터 생성부만 재정의**(그리기 호환) |

→ 결론: 진짜 작업은 거의 상류 ①②에 집중. 하류는 단면력 그래프 데이터 1곳 + drift 검증뿐.
   해석결과(frame 양단력)에 묶인 소비처가 사실상 없어 영향 범위가 작다.

## 권장 진행 순서(단계)
1. **MVLEM_3D 단일벽 3D 재검증** — 2D 검증을 3D 4노드 요소로 재현(요소 형태 다름).
2. **코어 모델 교체** — elastic 이중경로 제거 + MVLEM_3D + 고정 베이스만 + R09 재설계
   (1-1~1-3, 2-3 묶어서). 단일 코어 1개로 먼저.
3. **해석 엔진 비선형화** — Newton + 변위제어 Pushover + 중첩 폐기(1-4,1-5) + P-Delta(2-1)
   + Penalty→Transformation(2-2).
4. **하류 정합** — 단면력 추출(2-4)·물량·직렬화·메시.
5. **안정화·검증** — 전체 모델 Pushover 수렴·결과 타당성.
