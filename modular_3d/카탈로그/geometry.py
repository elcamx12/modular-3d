"""부재 치수 상수 — 모듈 높이·단면 폭·갭·슬래브 두께 등.

[단위] mm, 정수형 가능하나 float 도 허용.
[변경 영향처]
- MODULE_HEIGHT_MM 변경 → model.core 의 모든 부재 height 기본값, controls
  DEFAULT_DIMS, multi_floor 의 _floor_z_list, ui_panel configure_for_type
- SECTION_W_MM 변경 → 모든 부재 sub_components 의 단면 인셋 (s = 200), DEFAULT_DIMS
- MODULE_JOINT_GAP_MM 변경 → 자동 갭 정책, V3M.gap, alignment_helpers
- FLOOR_HEIGHT (= MODULE_HEIGHT + GAP) → 자동 계산. 직접 변경 X.
"""

# ── 모듈 치수 ────────────────────────────────────────────────
# 단층 모듈 한 층 자체 높이 (기둥 길이; 보 단면 포함, 모듈 내부 천장-바닥 외형).
MODULE_HEIGHT_MM = 3400
# 적층 시 두 모듈 사이의 물리적 갭 (PC 철골 건식접합부 두께 ≈ 20mm).
MODULE_JOINT_GAP_MM = 20
# 코어(RC 코어벽/슬래브) ↔ 타 부재 사이 갭 (mm). RC 코어 선시공 + 모듈 후시공의
# 시공 오차·연결철물 공간을 고려해 모듈끼리(20mm)보다 크게 둔다. 코어끼리(코어벽
# ↔ 코어벽)는 한 시스템이라 갭 0(이 상수 미적용). 이 값을 바꾸면 배치 자동 갭
# (auto_snap.gap_between)과 R09 결합 거리 임계(_CORE_LATERAL_MAX)가 함께 갱신된다.
# [함정] 두 곳은 반드시 같은 상수를 참조해야 한다 — 한쪽만 바꾸면 부재가 멀어져
# 코어 결합이 끊기거나(배치만 변경) 효과가 없다(접합만 변경).
CORE_JOINT_GAP_MM = 100
# 층-층 거리(period) = 단층 모듈 높이 + 접합부 갭 = 3420mm.
# 다층 z 분포 계산(z_k = k·FLOOR_HEIGHT) 의 단위.
FLOOR_HEIGHT = MODULE_HEIGHT_MM + MODULE_JOINT_GAP_MM

# 수직 3층 모듈이 차지하는 층 수 (1 그리드 칸 = 3 층).
VERTICAL_MODULE_FLOORS = 3

# ── 부재 단면 (각형강관 SHS 200×200×8 기본) ──────────────────
SECTION_W_MM = 200.0
SECTION_H_MM = 200.0
SECTION_T_MM = 8.0

# ── 슬래브 두께 ──────────────────────────────────────────────
# 2026-05-19 변경: 시각·해석 두께를 150 으로 통일 (운송 매핑 일관성).
# 기존엔 시각 150 / 해석 200 분리였으나, 운송 적층 두께가 해석 자중 두께와
# 일치해야 이중계산·표시 오차가 사라지므로 단일값 채택.
SLAB_THICKNESS_MODEL_MM = 150.0
SLAB_THICKNESS_MM = 150.0
# 비내력 벽(채움 패널) 두께 — 모듈 벽 + 벽패널 채움 패널 공통. 하중·물량에
# 들어가지 않는 순수 시각 벽(2026-05-24 3단계). 기둥/보(SHS) 와 별개.
NONBEARING_WALL_THICKNESS_MM = 100.0
# 코어 슬래브 기본 두께.
CORE_SLAB_DEFAULT_THICKNESS_MM = 180.0
# 코어벽 기본 두께.
CORE_WALL_DEFAULT_THICKNESS_MM = 300.0
# 코어벽 등가 트러스 격자 단위 (mm) — Panagiotou 2012 권고값.
CORE_TRUSS_GRID_MM = 800.0


# ── ops-only 노드 ID offset 영역 ──────────────────────────────
# 토폴로지 단계 am.nodes 의 노드 ID 는 0 ~ 약 1만 미만. ops_builder /
# joint_rules 가 빌드 중 추가로 만드는 가교·분할 노드는 충돌 방지를 위해
# 10000 이상의 정해진 영역을 사용한다. build_ops_model 은 5 케이스 반복
# 호출 시 `>= SPLIT_NODE_BASE_OFFSET` 노드를 모두 청소해 토폴로지를
# 깨끗한 상태로 되돌린다. 룰별 offset 은 같은 빌드 안에서 영역이 겹치지
# 않도록 분리한 것.
SPLIT_NODE_BASE_OFFSET = 10000    # R02/R05 보 분할 노드
RULE_NODE_OFFSET_R03 = 20000      # R03 패널 N1 가교 노드
RULE_NODE_OFFSET_R06 = 30000      # R06 (미사용 가능 — 예약 영역)
RULE_NODE_OFFSET_R09 = 40000      # R09 코어 축선 사영 가교


__all__ = [
    'MODULE_HEIGHT_MM', 'MODULE_JOINT_GAP_MM', 'CORE_JOINT_GAP_MM', 'FLOOR_HEIGHT',
    'VERTICAL_MODULE_FLOORS',
    'SECTION_W_MM', 'SECTION_H_MM', 'SECTION_T_MM',
    'SLAB_THICKNESS_MODEL_MM', 'SLAB_THICKNESS_MM',
    'NONBEARING_WALL_THICKNESS_MM',
    'CORE_SLAB_DEFAULT_THICKNESS_MM', 'CORE_WALL_DEFAULT_THICKNESS_MM',
    'CORE_TRUSS_GRID_MM',
    'SPLIT_NODE_BASE_OFFSET',
    'RULE_NODE_OFFSET_R03', 'RULE_NODE_OFFSET_R06', 'RULE_NODE_OFFSET_R09',
]
