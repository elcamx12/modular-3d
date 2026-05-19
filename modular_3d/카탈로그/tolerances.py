"""거리·임계 허용오차 — 노드 병합·스냅·인터페이스 등.

[변경 영향처]
- NODE_MERGE_TOL_MM 변경 → topology 의 모든 좌표 키 반올림·비교
- FP_SUPPORT_TOL_XY/Z → 패널 코너 ↔ 모듈 매칭 (분산 안 됨, 본 모듈만)
- SNAP_TRIGGER_MM → auto_snap, snap.apply_gap
- HOVER_SNAP_RADIUS_MM → alignment_view 의 _f5_world_snap
"""

# ── 좌표 비교 / 노드 병합 ──────────────────────────────────
# 1mm 이내 점은 동일 노드로 간주 (좌표 반올림 단위).
NODE_MERGE_TOL_MM = 1.0
# 두 벡터 방향 일치 판정 (정규화 후 cross norm) 허용오차.
LINE_COLINEAR_TOL = 1e-3
# 부재 겹침 판정 허용오차 (mm).
OVERLAP_TOL_MM = 1.0

# ── 모듈 간 접합부 ────────────────────────────────────────
# 적층 / 인접 모듈을 가상 커넥터로 다리 놓을 때의 거리 한계.
# 수직 갭 ≤ 20mm 인 두 모듈을 같은 그룹으로 묶을 때 여유 5mm.
JOINT_BRIDGE_TOL_MM = 25.0
# 수평 인접 모듈 기둥 중심 간 거리: half_s(100) + 갭(20) + half_s(100) + 여유(5) = 225.
JOINT_HORIZ_CONNECT_MAX_MM = 225.0

# ── 패널·슬래브 지지점 매칭 ────────────────────────────────
# FP/캔틸 슬래브 코너 ↔ 모듈 기둥·보·캔틸 끝 매칭 xy 거리.
# 모듈 코너 대각선에서 sqrt(220² + 220²) ≈ 311mm → 여유 두어 350.
FP_SUPPORT_TOL_XY = 350.0
# FP/슬래브와 지지점의 z 차이 제한.
FP_SUPPORT_TOL_Z = 500.0

# ── 자동 갭 / 스냅 ────────────────────────────────────────
# 새 부재 외곽 ↔ 기존 부재 외곽 거리 100mm 이내일 때 자동 갭 발동.
SNAP_TRIGGER_MM = 100.0
# 2D 캔버스 호버 시 부재 꼭지점 스냅 허용 반경 (mm).
HOVER_SNAP_RADIUS_MM = 300.0
# 2D 캔버스 그리드 스냅 단위.
GRID_SNAP_MM = 100.0

# ── 검증 ─────────────────────────────────────────────────
EQUILIBRIUM_TOL = 1e-3   # 평형 검증: 0.1% 이내
BENCHMARK_TOL = 1e-2    # 벤치마크: 1% 이내


__all__ = [
    'NODE_MERGE_TOL_MM', 'LINE_COLINEAR_TOL', 'OVERLAP_TOL_MM',
    'JOINT_BRIDGE_TOL_MM', 'JOINT_HORIZ_CONNECT_MAX_MM',
    'FP_SUPPORT_TOL_XY', 'FP_SUPPORT_TOL_Z',
    'SNAP_TRIGGER_MM', 'HOVER_SNAP_RADIUS_MM', 'GRID_SNAP_MM',
    'EQUILIBRIUM_TOL', 'BENCHMARK_TOL',
]
