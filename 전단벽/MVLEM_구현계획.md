# 코어벽 MVLEM_3D 비선형 전환 — 구현 계획 (확정 2026-06-24)

탐색 T1~T8 종합. 충돌검토(MVLEM_A전환_충돌검토.md)·API(MVLEM_3D_API참조.md)·
정답지(RW2_검증_정답지.md) 와 함께 참조. 백업 `_snapshots/2026-06-25_MVLEM_A전환전_modular_3d`.

## 확정 방향(탐색 결론)
- 자료구조(D1): **별도 AnalysisWall 신설**(막대 전제 코드 격리).
- 연결(D2): **직접 묶기**(코어=master 변형, 외부 slave). **offset = 두께/2 보정**(노드 외면 배치로 자동 or 별도).
- 요소: MVLEM_3D(4노드 반시계·6DOF·본체호환), 재료 Concrete02/Steel02(2D검증 재사용), 전단 K=G·A/h.
- 걷어낼 것: 코어벽 막대(core_column+runner) 하나뿐(shell/truss 없음). 코어슬래브는 유지.
- 식별 갱신: 이름표(role) 기반만(단면설계 제외·단면력·렌더). 타입 기반(물량·평가·UI)은 무관.

---

## P1 — MVLEM_3D 단일벽 3D 재검증
- **목표**: 2D RW2 검증을 3D·ndf6·MVLEM_3D 로 재현 + **D2 offset 후속(노드 중심면 vs 외면 면외거동 확인)**.
- **변경**: 신규 독립 스크립트 `전단벽/rw2_mvlem3d_pushover_검증.py`(본체 무관).
- **산출물**: 3D MVLEM_3D 단일벽 monotonic pushover 곡선 PNG + 수치, RW2 실험밴드 대조, 노드위치 2케이스 비교.
- **점검기준**: 초기강성·항복강도가 2D검증/실험과 ±15% 일치. 노드 중심면/외면 거동차 정량화.
- **검증**: 곡선 PNG·CSV, 2D 결과 대조.
- **롤백**: 스크립트 삭제(본체 무영향).

## P2 — AnalysisWall 자료구조 + topology 변환
- **목표**: 별도 `AnalysisWall`(4코너 노드·m·fiber 폭/철근비/재료·전단·source_comp_id) 신설,
  `AnalysisModel.walls` 추가. `topology._extract_core` 가 코어벽→AnalysisWall(get_world_corners 8코너에서
  4노드 유도, 다층 노드공유, 이형 교차노드 공유). 코어슬래브(core_slab_beam)는 기존 막대 유지.
- **변경**: topology.py(AnalysisWall dataclass·walls·_extract_core 재작성), io/scene_io.py(D4 하위호환).
- **산출물**: build_analysis_model 이 코어벽→AnalysisWall, 슬래브→막대.
- **점검기준**: 실제 씬(Ascene.json) 로드 시 코어벽이 AnalysisWall 로, 4노드 좌표 정확, 다층 적층 노드공유,
  골든 테스트 통과(비코어). 막대 전제 코드(get_member_length 등)가 walls 안 건드림.
- **검증**: 런타임 스크립트(코어벽 AnalysisWall 수·노드 좌표 확인).
- **롤백**: topology/_extract_core 백업 원복.
- **★D4 (하위호환)**: 기존 저장 씬은 dimensions(width/depth/height)만 → get_world_corners 로 4노드 유도
  가능(추가 저장 불필요할 수 있음). model_type 플래그 필요 여부를 P2 에서 판단·결정.

## P3 — ops_builder MVLEM_3D 등록 + 고정 + R09 재설계
- **목표**: AnalysisModel.walls→MVLEM_3D 등록(walls 루프, member_to_ele_tag·core_ele_tags 재사용),
  fiber 재료 등록, **베이스 노드만 고정**(_step_fix_base_nodes 코어 전노드→베이스만),
  **R09 재설계**(코어=master 변형 허용, offset 두께/2 보정, _CORE_LATERAL_MAX 외면 기준 재조정).
- **변경**: ops_builder.py(walls 등록·고정·재료·MVLEM 노드 회전fix 제외 가드), joint_rules.py(R09 offset/DOF).
- **산출물**: 코어벽 MVLEM_3D 요소 + 외부-코어 연결.
- **점검기준**: 단일 코어 1장 + 모듈 소형 모델 빌드·해석 수렴, 코어 변형 발생(고정 아님), 과구속/특이행렬 없음.
- **검증**: 소형 모델(코어1+모듈 몇개) 해석.
- **롤백**: ops_builder/joint_rules 백업 원복.

## P4 — 해석 비선형화
- **목표**: solve_pushover 신규(Newton + DisplacementControl 최상층 master + analyze N + V-δ 캡처),
  해석블록 비선형(algorithm/integrator/test), solve_all_cases 선형중첩 폐기→조합별 직접해석, P-Delta,
  Penalty→Transformation.
- **변경**: ops_solver.py(solve_pushover·비선형블록·중첩폐기), ops_builder.py(geomTransf PDelta).
- **산출물**: 전체 모델 pushover 역량곡선.
- **점검기준**: 전체 씬 pushover 수렴·발산없음, 역량곡선 타당.
- **검증**: 전체 씬 pushover 실행.
- **롤백**: ops_solver 백업 원복.
- **★D3 (Pushover 정책)**: 제어방식(변위제어 최상층 master)·목표변위(예: 2% drift)·횡력패턴(역삼각형
  vs 균등)·방향(X/Y). P4 진입 시 사용자 결정.

## P5 — 하류 정합
- **목표**: 코어 MVLEM 단면력/응력을 member_stations/member_forces 호환 형식으로 생성(_extract 분기),
  단면설계 CAT_RC_CORE 에 새 role 등록(코어 계속 제외), 렌더 viewer 새 role, drift 수치 검증.
- **변경**: ops_solver.py(_extract MVLEM), section_design.py(role), viewer.py(role).
- **점검기준**: 코어 단면력 표시 정상, 단면설계 코어 제외 유지, drift 정상, 골든 통과.
- **검증**: 결과 패널/렌더 확인 + 골든.
- **롤백**: 해당 파일 백업 원복.

## P6 — 전체 검수
- **목표**: 회귀 변화범위 검토(코어 외 의도외 변화 없나), 골든 전체 통과, pushover 수렴·타당, 하위호환
  (기존 저장 씬 로드), 이형단면(ㄷ/ㅁ자) 동작.
- **점검기준**: 골든 100% 통과, pushover 결과 공학적 타당, 기존 씬 정상 로드, 이형 코어 수렴.
- **검증**: 골든 + 회귀 + 여러 씬 + 이형 케이스.

---

## 페이즈별 공통 점검 절차 (각 P 종료 시 필수)
1. 구문검증(py_compile, venv).
2. 단위 실행/검증(해당 페이즈 산출물).
3. **사고 통한 문제탐색**: 전제위반? 경계조건(짧은벽·이형·회전·다층)? 회귀? 수렴? offset?
4. 발견 문제 수정 후 재검증.
5. 골든 테스트(test_golden_starter, 비코어 회귀 안전망) 통과 확인.
6. 로그 갱신 + (방향성 결정 아니면) 다음 예약.

## 방향성 결정 지점 (사용자 개입)
- **D3 (P4 진입)**: Pushover 정책(제어방식·목표변위·횡력패턴·방향).
- **D4 (P2 진행)**: 하위호환 — 기존 저장 씬 코어 처리(model_type 플래그 필요 여부).
- (D1 자료구조·D2 연결방식은 결정 완료.)

## 위험 요약
- 최대 난이도: P2(AnalysisWall 도입·topology 재작성) + P3(R09 offset·고정해제 과구속).
- 수렴 위험: P4(비선형+Penalty+코어항복). Transformation·스텝분할로 대응.
- 회귀 위험: P5 식별조건 누락 시 코어가 단면설계/렌더에서 오분류. 골든+수동확인.
