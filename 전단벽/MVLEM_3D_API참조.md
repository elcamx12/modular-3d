# MVLEM_3D API 참조 (T1 탐색 결과, 2026-06-24)

출처: OpenSees 공식 문서. 우리 venv 에서 생성 성공 확인됨(앞선 가용성 테스트).
용도: P1~P3 구현 시 이 스펙대로 코딩.

## 시그니처 (openseespy)
```
element('MVLEM_3D', eleTag, iNode, jNode, kNode, lNode, m,
        '-thick', [t1..tm], '-width', [w1..wm], '-rho', [r1..rm],
        '-matConcrete', [c1..cm], '-matSteel', [s1..sm], '-matShear', shearTag,
        [ '-CoR', c, '-ThickMod', tMod, '-Poisson', Nu, '-Density', Dens ])
```

## 노드 규약 ★
- **4노드, 반시계방향**: iNode→jNode→kNode→lNode. 사각형 벽 요소.
- 한 요소 = 벽 한 판의 한 조각(한 층 또는 소성힌지 분할 단위).
- 높이방향 적층: 아래 요소 top 2노드 = 위 요소 bottom 2노드(노드 공유).

## 자유도 ★ (본체와 호환!)
- **노드당 6 DOF**(ux,uy,uz, θx,θy,θz). 모델 `-ndm 3 -ndf 6` 필수 = **본체와 정확히 일치.**
- 면내(in-plane): fiber 기반 **비선형**.
- 면외(out-of-plane)·drilling: Kirchhoff plate **선형탄성**. ThickMod(기본 0.63=0.25·Ig)·Poisson(기본 0.25)로 스케일.

## 인자
| 인자 | 형식 | 개수 | 의미 |
|---|---|---|---|
| m | int | — | 수직 fiber(매크로) 수 |
| -thick | float[] | m | fiber 두께(벽면 수직 방향) |
| -width | float[] | m | fiber 폭(벽 길이방향 분할) |
| -rho | float[] | m | fiber별 철근비 |
| -matConcrete | int[] | m | fiber별 콘크리트 재료 태그 |
| -matSteel | int[] | m | fiber별 철근 재료 태그 |
| -matShear | int | 1 | 면내 전단 재료 태그(단일) |
| -CoR | float | 1 | 회전중심 c (기본 0.4) |
| -ThickMod | float | 1 | 면외 휨강성 배율 (기본 0.63) |
| -Poisson | float | 1 | 면외 포아송비 (기본 0.25) |
| -Density | float | 1 | 질량밀도 (기본 0.0) |

## 단위
- N·mm·MPa 일관. 본체·2D검증과 동일 → 변환 불필요.

## 우리 적용 메모
- **2D MVLEM 검증 자산 재사용 가능**: 재료(Concrete02 웹/경계, Steel02), fiber 폭/철근비
  배열, 전단재료 G·A/h 환산(2D 검증서 발견한 함정) 그대로. 단 노드가 2점→4점, 전단강성
  환산 시 면적·높이 재확인 필요(P1 에서 검증).
- **면외 탄성**이 이형단면(ㄷ/ㅁ자)에서 직교 벽판 연결에 핵심 — 한 벽판 면외 = 직교 벽판
  면내. 교차부 노드 공유로 연결(모델링지침 p22). T3/P2 에서 형상 처리.
- **전단재료**: 2D 는 단일 수평스프링이었음. 3D 도 -matShear 단일. RW2 는 선형탄성으로 시작.
- **recorder**: globalForce, Curvature, Shear_Force_Deformation, Fiber_Strain,
  Fiber_Stress_Concrete/Steel 가능 → 코어 단면력/응력 추출(하류 P5)에 사용.

## 미해결(후속 탐색/검증)
- 옵션 인자(-CoR 등) 포함 생성·거동은 P1 단일벽 3D 재검증서 확인.
- 적층 시 노드 공유·전단강성 단위, 면외 plate 강성의 코어 적절성.
- SFI_MVLEM_3D(전단-휨 상호작용)는 후순위 옵션(FSAM 재료 필요).
