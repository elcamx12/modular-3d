"""물량산출 + 단면 자동 산정 기능 — 설계 (CoT 한글 각주 only)

이 파일은 코드 한 줄 없이 한글 주석만으로 설계 의도를 적어둔 문서다.
Phase 진입 시 해당 섹션을 다시 읽고 실제 코드 작성에 들어간다.
모든 Phase 종료 후 이 설계 파일과 실제 구현 파일을 비교하여 일관성을 검증한다.

==============================================================================
0. 사용자 확정 사항 요약 (질문 모드 결과)
==============================================================================
- 산출 대상  : 강재 부재 + 슬래브(콘크리트 부피 + 철근 비율) + 바닥패널(콘크리트 카세트)
              + 벽패널(강재 프레임만, 콘크리트/철근 없음)
              RC 코어는 산출 제외
- 산출 단위  : 강재 → 톤·본수·길이, 슬래브/바닥패널 → 부피
- 단가       : 본 작업에서 제외 (추후 확장)
- 단면 카탈로그 : KS D 3568 각형강관 SHS 75×75×3.2 ~ 400×400×16 (시판 표준)
- 강재 재질  : SS275 (Fy = 275 MPa, KS D 3503)
- 설계 기준  : KDS 41 31 00 건축물 강구조 설계기준 (한계상태설계, LRFD)
- 하중조합   : 1.2D + 1.6L (중력만, 횡력 케이스는 본 작업 제외)
- 산정 흐름  : 현재 200×200 결과로 1 패스 — 응력 검토 → 후보 단면 중 가장 가벼운 OK 선택
- 그룹 정책  : 1종 / 2종 / 3종 케이스 3개 동시 산정
              · 1종 = 모든 강재 1단면
              · 2종 = 기둥류 / 보류
              · 3종 = 기둥류 / 일반 보 / 캔틸레버 보
- 그룹 단면  : 그룹 내 최대 응력비 기준
- 검토 항목  : 축력(좌굴 포함) + 휨 + 전단 + 조합(P-M) + 처짐
- 처짐 한계  : 일반 보 L/360, 캔틸 L/180
- 좌굴 K     : 1.0 (양단 핀), 비지지길이 = 부재 실길이
- 본수 집계  : 단면+길이 동일 → 한 종, 합계 행 포함
- UI         : 구조해석 다이얼로그 "물량산출" 탭 추가
              · 라디오버튼 1종/2종/3종 선택
              · 단면 그룹 표 (단면·본수·길이·중량)
              · 슬래브/바닥패널 부피 표
              · 부재별 응력비 표 (선택 시 3D 강조)
              · 합계 행
- 시각화     : 응력비 5단계 색상 (빨/주/초/하/파)
- NG 처리    : 가장 큰 후보(400×400×16) 강제 채택 + 경고 라벨
- 영속화     : scene.json 저장 안 함 — 리포트 only
- 엑셀       : 미사용

==============================================================================
1. Phase 1 — 단면 카탈로그 + 응력 검토 함수
==============================================================================
파일: modular_3d/analysis/section_catalog.py (신규)
파일: modular_3d/analysis/strength_check.py (신규)

[1-A] section_catalog.py
- 데이터 클래스 SHSSection
  · 필드: name(str, "SHS 200x200x8"), w, h, t, A, Iy, Iz, J, Avy, Avz, Sy, Sz, ry, rz
       · Sy = Iy / (h/2) 단면계수 (강축)
       · Sz = Iz / (w/2)
       · ry = sqrt(Iy/A), rz = sqrt(Iz/A) 회전반경
  · 클래스 메서드 from_dimensions(w, h, t) → constants._shs_section_props 호출 + 위 추가 항목 계산
  · 속성 weight_per_m_kg = A_m2 × density (kg/m, 발주·물량용)
- 카탈로그 함수 build_shs_catalog() → list[SHSSection]
  · KS D 3568 표준 정사각 SHS:
    (75, 3.2), (75, 4.5),
    (100, 3.2), (100, 4.5), (100, 6.0),
    (125, 4.5), (125, 6.0),
    (150, 4.5), (150, 6.0), (150, 8.0),
    (175, 6.0), (175, 8.0),
    (200, 6.0), (200, 8.0), (200, 10.0), (200, 12.0),
    (250, 8.0), (250, 10.0), (250, 12.0),
    (300, 9.0), (300, 12.0), (300, 16.0),
    (350, 12.0), (350, 16.0),
    (400, 12.0), (400, 16.0),
  · 정렬: A 오름차순 (단면적 작은 → 큰 순). 자동 산정 시 앞에서부터 OK 검사
- 상수 STEEL_FY_MPA = 275.0 (SS275)
- 상수 STEEL_FU_MPA = 410.0
- 부분안전계수: φb = 0.9 (휨), φc = 0.9 (압축), φv = 0.9 (전단) — KDS 41 31 00 강구조 LRFD

[1-B] strength_check.py
- 입력 자료형 MemberDemand
  · 필드: P (축력 N, 압축 음수), Mz (강축 휨 N·mm), My (약축 휨 N·mm),
         Vz (전단 N), Vy (전단 N), L (부재 길이 mm),
         kind (str: 'column'/'beam'/'cantilever'), K (좌굴계수, 기본 1.0)
  · NOTE: 처짐(delta)은 단면 의존이므로 demand 에 저장하지 않고
          check_deflection(section, Mz, L, kind) 에서 매번 추정한다.
          - 일반 보 : δ ≈ (5/48)·|Mz|·L² / (E·Iy)  (균일분포 가정)
          - 캔틸    : δ ≈ (1/4)·|Mz|·L² / (E·Iy)
          단면이 바뀌면 처짐도 자동 갱신.
- 출력 자료형 CheckResult
  · 필드: ratio_axial, ratio_bend, ratio_shear, ratio_combined, ratio_defl,
         ratio_max (위 5개 중 최댓값), ok (bool, ratio_max ≤ 1.0)
         critical_item (str, 어느 항목이 지배했는지)

- 함수 check_axial(section, P, L, K=1.0) → ratio
  · 압축이면 KDS 41 31 00 좌굴식: φc·Pn = φc·Fcr·A
    Fe = π²E / (KL/r)², r = min(ry, rz)
    λc² = Fy/Fe
    Fcr = (0.658^λc²) × Fy (λc ≤ 1.5) | (0.877/λc²) × Fy (λc > 1.5)
    ratio = |P| / (φc · Fcr · A)
  · 인장이면 ratio = |P| / (φt · Fy · A), φt = 0.9
- 함수 check_bending(section, Mz, My) → ratio
  · 강축: φb·Mn = φb·Fy·Sy (단순화, 횡좌굴은 SHS 박스라 영향 작음)
  · 약축: φb·Fy·Sz
  · ratio = |Mz|/(φb·Fy·Sy) + |My|/(φb·Fy·Sz)  (단순 합 — 보수적)
- 함수 check_shear(section, Vy, Vz) → ratio
  · φv·Vn = φv · 0.6·Fy · Av
  · ratio = max(|Vy|/(φv·0.6·Fy·Avy), |Vz|/(φv·0.6·Fy·Avz))
- 함수 check_combined(section, P, Mz, My, L, K) → ratio
  · KDS 41 31 00 H1.1 상관식:
    P/(φPn) ≥ 0.2 → P/(φPn) + (8/9)·(Mz/φMnz + My/φMny) ≤ 1.0
    P/(φPn) < 0.2 → P/(2·φPn) + (Mz/φMnz + My/φMny) ≤ 1.0
- 함수 check_deflection(section, Mz, L, kind) → ratio
  · 단면+모멘트로 처짐 추정 (estimate_deflection) 후 한계와 비교
  · kind='cantilever' → limit = L/180
  · 그 외 → limit = L/360
  · ratio = estimated_delta / limit
- 함수 check_member(section, demand) → CheckResult
  · 위 5개 함수 호출, 각 ratio 계산
  · ratio_max = max, critical_item 지정
  · ok = ratio_max ≤ 1.0

==============================================================================
2. Phase 2 — 부재 그룹핑 + 단면 자동 선정
==============================================================================
파일: modular_3d/analysis/section_design.py (신규)

[2-A] 부재 분류기
- 함수 classify_member(member, scene) → str
  · 입력: AnalysisMember + 원본 컴포넌트 정보
  · 출력 카테고리:
    'module_column'    : 모듈(단층/수직3층)의 기둥
    'module_beam'      : 모듈의 보 (top/bottom beam)
    'cantilever_beam'  : CantileverBeam 컴포넌트의 보
    'floor_panel_beam' : FloorPanel 의 보
    'wall_panel_column': WallPanel 의 기둥
    'wall_panel_beam'  : WallPanel 의 보
  · 분류 근거:
    - member.kind 가 'module_column' / 'module_top_beam' / 'module_bottom_beam' →
      앞 두 개로 매핑
    - member.kind 가 'panel_*' 인 경우 컴포넌트 ComponentType 으로 분기

[2-B] 그룹 정책 (1종/2종/3종)
- 함수 group_members(members, policy='1종'|'2종'|'3종') → dict[group_name → list[member]]
  · 1종: 모두 'all_steel' 한 그룹
  · 2종:
    'columns'  = module_column + wall_panel_column
    'beams'    = module_beam + cantilever_beam + floor_panel_beam + wall_panel_beam
  · 3종:
    'columns'  = module_column + wall_panel_column
    'beams'    = module_beam + floor_panel_beam + wall_panel_beam
    'cantilevers' = cantilever_beam

[2-C] 부재 demand 추출
- 함수 extract_demand(member, ops_results) → MemberDemand
  · ops_results 에서 1.2D+1.6L 케이스의 부재 내부력 추출
    · P, Mz, My, Vz, Vy 는 element_forces 에서 양 단부 절댓값 최댓값
  · L = 부재 실길이 (노드 좌표 차이 norm)
  · delta_max = 부재 양 단부 변위 + 중간 처짐 추정
    · 중간 처짐은 ops 의 elementResponse 'localDeformations' 활용 또는
      간이식 5wL⁴/(384EI) 사용 (가용 데이터에 따라 결정)
  · kind = classify 결과를 'column'/'beam'/'cantilever' 로 단순화

[2-D] 그룹 단면 자동 선정
- 함수 select_section_for_group(members, demands, catalog) → (SHSSection, max_ratio, ng_flag)
  · 카탈로그를 단면적 오름차순 순회
  · 각 후보 단면에 대해 그룹의 모든 부재 demand 를 check_member 로 검토
  · 그룹 최대 ratio 가 1.0 이하면 그 단면 채택, 즉시 break
  · 어떤 후보도 OK 못 하면 카탈로그 마지막(=가장 큰) 단면 채택 + ng_flag=True
  · 반환: (선택 단면, 채택 단면 기준 그룹 최대 ratio, NG 플래그)

[2-E] 통합 산정
- 함수 design_all_policies(members, ops_results, catalog) → dict[policy → DesignResult]
  · '1종','2종','3종' 각각에 대해
    · group_members 로 분할
    · 각 그룹에 select_section_for_group 적용
    · 그룹별 결과 + 부재별 ratio 모두 보관
- DesignResult 자료형
  · sections : dict[group → SHSSection]
  · group_max_ratio : dict[group → float]
  · ng_groups : list[str]
  · member_ratios : dict[member_id → float]    # 그 정책에서 그 부재가 속한 단면 기준
  · member_critical : dict[member_id → str]    # 어느 항목이 지배했는지

==============================================================================
3. Phase 3 — 물량 집계
==============================================================================
파일: modular_3d/analysis/quantity_takeoff.py (신규)

[3-A] 강재 본수 집계
- 함수 aggregate_steel(members, design_result) → list[SteelLineItem]
  · SteelLineItem 필드:
    section_name, length_mm, count, total_length_m, unit_weight_kg_m, total_weight_ton
  · 알고리즘:
    1) (단면, 길이를 round(_,1) mm) 키로 그룹화 → count 집계
    2) total_length_m = length_mm × count / 1000
    3) total_weight_ton = unit_weight_kg_m × total_length_m / 1000
  · 마지막에 합계 SteelLineItem 추가 (section_name='합계')

[3-B] 슬래브 부피 집계
- 함수 aggregate_slabs(scene) → SlabQuantity
  · SlabQuantity 필드:
    total_area_m2, thickness_mm, total_volume_m3, rebar_ratio, rebar_weight_ton
  · 알고리즘:
    · scene 의 모든 SlabData 면적 합산 (모듈 내부 슬래브 + 바닥패널)
    · 두께 = SLAB_THICKNESS_MM (200)
    · 부피 = 면적 × 두께
    · 철근 비율 = 0.012 (1.2 vol%, 표준 슬래브 평균 가정)
    · 철근 중량 = 부피 × ratio × 7850 kg/m³

[3-C] 바닥패널은 슬래브 산정에 통합
- 사용자 답변: 바닥패널은 콘크리트 카세트 → 슬래브와 동일 처리
- 단, 바닥패널의 '강재 프레임'(기둥·보)은 강재 집계에 포함 (3-A 에서 자동 처리)

[3-D] 벽패널은 강재만
- 강재 프레임만 3-A 에서 처리, 슬래브 없음

[3-E] 통합
- 함수 build_quantity_report(scene, members, design_results) → QuantityReport
  · QuantityReport 필드:
    steel_by_policy : dict[policy → list[SteelLineItem]]
    slab : SlabQuantity
    sections_by_policy : dict[policy → dict[group → SHSSection]]
    member_ratios_by_policy : dict[policy → dict[member_id → float]]
    ng_by_policy : dict[policy → list[str]]

==============================================================================
4. Phase 4 — UI 탭 (구조해석 패널)
==============================================================================
파일: modular_3d/analysis_panel.py (수정)
NOTE: 실제 파일명은 analysis_panel.py (AnalysisPanel 도킹 패널). 기존에 두 개 탭
      "요약" / "부재별 내부력" 존재. member_selected 시그널 이미 정의됨 → 재사용.

[4-A] 신규 탭 "물량산출"
- 기존 탭("요약", "부재별 내부력") 옆에 추가
- 상단 라디오버튼: "1종" / "2종" / "3종" — 선택 시 표 갱신
- 중단 1: 단면 그룹 표 (QTableWidget)
  · 컬럼: 그룹명 · 채택 단면 · 그룹 최대 ratio · NG 플래그 · 본수 합 · 길이 합(m) · 중량 합(ton)
- 중단 2: 강재 본수표
  · 컬럼: 단면 · 길이(mm) · 본수 · 총 길이(m) · 총 중량(ton)
  · 마지막 합계 행
- 중단 3: 슬래브 부피표
  · 면적 · 두께 · 부피 · 철근비율 · 철근중량
- 하단: 부재별 응력비 표 (선택형)
  · 컬럼: 부재 ID · 종류 · 그룹 · 길이 · 단면 · ratio_max · 지배항목
  · 정렬 가능, 행 클릭 시 3D 뷰에서 해당 부재 강조 (시그널 emit)

[4-B] 시그널 연결
- 부재별 응력비 표 셀 클릭 → emit member_selected(member_id)
- main_window.py 에서 이 시그널을 받아 3D 뷰의 highlight_member(id) 호출
  · "부재별 내부력" 탭에 이미 같은 패턴이 있음 — 그대로 재사용

==============================================================================
5. Phase 5 — 3D 뷰 색상 시각화
==============================================================================
파일: modular_3d/controls.py (수정) — 기존 contour_changed 핸들러 패턴 재사용
파일: modular_3d/viewer.py 또는 ops_view 측 (set_member_colors API 신규/확장)

[5-A] 색상 5단계 매핑
- 함수 ratio_to_color(ratio) → (r, g, b)
  · ratio < 0.3        → 파랑 (0.2, 0.4, 1.0)   # 비경제
  · 0.3 ≤ ratio < 0.6  → 하늘 (0.4, 0.8, 1.0)   # 여유
  · 0.6 ≤ ratio ≤ 0.85 → 초록 (0.3, 0.9, 0.3)   # 적정
  · 0.85 < ratio ≤ 1.0 → 주황 (1.0, 0.6, 0.0)   # 한계
  · ratio > 1.0        → 빨강 (1.0, 0.2, 0.2)   # NG

[5-B] 활성화 트리거
- 사용자가 "물량산출" 탭으로 전환할 때 → 3D 뷰 색상 모드 = 'ratio'
  · 각 부재 selected_policy 기준 ratio 로 색상 적용
- 다른 탭으로 전환 시 → 색상 모드 = 'default' 복원

[5-C] 부재 강조 (선택)
- 4-B 의 member_selected 시그널 수신 → 해당 부재만 노란색 굵은 선으로 오버레이
- "부재별 내부력" 탭의 highlight 함수 재사용 가능

==============================================================================
6. Phase 6 — 통합 진입점 + 테스트
==============================================================================
- 함수 run_quantity_takeoff(scene, am, ops_results) → QuantityReport
  · Phase 2~3 통합 호출
- 호출 시점: 사용자가 "구조해석 실행" 후 다이얼로그 열렸을 때 자동 실행
  · 또는 "물량산출" 탭 첫 진입 시 lazy 실행 (해석 결과 무거우니 lazy 권장)

[6-A] 자체 점검 체크리스트 (각 Phase 완료 후)
- 단위 일관성 (N, mm, MPa)
- 단면 카탈로그 모든 항목 _shs_section_props 와 일치
- 그룹별 최대 ratio 가 정말 그 그룹 부재 중 최댓값인지
- 본수 합 × 단위중량 = 톤 정합
- NG 부재 색상이 진짜 빨강인지

==============================================================================
7. 후속 확장 (현재 작업 외)
==============================================================================
- 횡력 케이스 포함 (Ex/Ey/Wx/Wy 버그 해결 후)
- RC 코어 산정
- 단가 입력 → 공사비 추정
- 엑셀/CSV 내보내기
- 그룹 정책 사용자 정의 (예: 층별 분리)

==============================================================================
8. 자료 흐름 다이어그램
==============================================================================
scene + AnalysisModel
        │
        ▼
   solve_vertical (1.2D+1.6L) → OpsResults (element_forces, node_disps)
        │
        ▼
   extract_demand (per member) → list[MemberDemand]
        │
        ▼
   classify_member → group 분류
        │
        ▼
   group_members(policy='1종'/'2종'/'3종')
        │
        ▼
   select_section_for_group (per group, per policy)
        │
        ▼
   DesignResult (per policy)
        │
        ├──► aggregate_steel → SteelLineItem
        │
        └──► QuantityReport
                  │
                  ▼
             UI 탭 표시 + 3D 색상

끝.
"""
