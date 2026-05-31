"""종속 부재 자동 스냅 (Phase 2 — 2026-05-11, v2 사용자 피드백 반영).

[모델 매핑 사실관계 — model/core.py 기준]
- CantileverBeam.dimensions['width'] = 보 길이(로컬 +x). 한 끝(x=0) 이 부모 닿는 쪽.
- CantileverSlab.dimensions['width'] = 돌출 길이(로컬 +x), ['depth'] = 폭(로컬 +y).
  x=0 변이 부모 접합면.
- MidBeam.dimensions['width'] = 보 길이(로컬 +x).
- MidColumn 200×200 고정.
- rotation 단위: 도(0/90/180/270), z 축 시계 반대 회전. local +x 가 월드 좌표계에서
  어느 방향이 되는지를 결정.

[방향 정책 — 사용자 피드백 v2]
- 캔틸레버보 / 캔틸레버슬래브: 부모 외곽 변에 **수직** 으로 돌출 (모듈 외곽 보에
  나란히 붙이는 게 아님).
- 꼭지점 스냅: 캔틸레버 보·슬래브는 변의 양 끝 꼭지점 중 마우스 가까운 1개에 정확히
  부재의 부모 닿는 모서리가 위치. 변 중간 슬라이딩 X.
- 중간기둥: 변 슬라이딩 (그대로).
- 중간보: 길이 자동, 부모 모듈 내 인접 기둥 → 기둥 / 없으면 부모 외곽 변까지.
"""
from __future__ import annotations

from typing import Tuple, Optional


from modular_3d.model import ComponentType
from modular_3d._utils.geometry import xy_bbox


SLAB_MAX_WIDTH_MM = 3400.0


def _nearest_edge(wx: float, wy: float, bbox) -> str:
    """bbox 4 변 중 가장 가까운 변 라벨 ('L','R','B','T')."""
    ex0, ey0, ex1, ey1 = bbox
    options = [
        ('L', abs(wx - ex0)),
        ('R', abs(wx - ex1)),
        ('B', abs(wy - ey0)),
        ('T', abs(wy - ey1)),
    ]
    options.sort(key=lambda t: t[1])
    return options[0][0]


def _nearest_vertex_on_edge(wx: float, wy: float, edge: str, bbox):
    """edge 위 양 끝 꼭지점 중 마우스 가까운 1 개의 (x, y) 반환."""
    ex0, ey0, ex1, ey1 = bbox
    if edge == 'L':
        v1, v2 = (ex0, ey0), (ex0, ey1)
    elif edge == 'R':
        v1, v2 = (ex1, ey0), (ex1, ey1)
    elif edge == 'B':
        v1, v2 = (ex0, ey0), (ex1, ey0)
    else:  # T
        v1, v2 = (ex0, ey1), (ex1, ey1)
    d1 = (v1[0] - wx) ** 2 + (v1[1] - wy) ** 2
    d2 = (v2[0] - wx) ** 2 + (v2[1] - wy) ** 2
    return v1 if d1 <= d2 else v2


def _is_inside(wx, wy, bbox, margin=0.0) -> bool:
    ex0, ey0, ex1, ey1 = bbox
    return (ex0 - margin) <= wx <= (ex1 + margin) and \
           (ey0 - margin) <= wy <= (ey1 + margin)


# ── 캔틸레버보 ────────────────────────────────────────────
#
# 동작: 부모 외곽 변에 직각으로 돌출. 변 양 끝 꼭지점 중 마우스 가까운 곳에
# 부재 한 끝(부모 닿는 끝)이 정확히 위치.
#
# rotation 자동 결정 — 어느 변에 가까운가에 따라:
#   R 변(부모 우)  → 보가 +x 방향 돌출 (rotation=0)
#   L 변(부모 좌)  → 보가 -x 방향 돌출 (rotation=180)
#   T 변(부모 위)  → 보가 +y 방향 돌출 (rotation=90)
#   B 변(부모 아래)→ 보가 -y 방향 돌출 (rotation=270)
def snap_cantilever_beam(wx, wy, parent, rotation, dims):
    bbox = xy_bbox(parent)
    ex0, ey0, ex1, ey1 = bbox
    parent_w = ex1 - ex0
    parent_d = ey1 - ey0
    edge = _nearest_edge(wx, wy, bbox)
    # [v5] 장변(긴 변)에는 캔틸레버보 배치 금지. R/L 변에 붙으면 변 길이 = parent_d,
    # T/B 변에 붙으면 = parent_w. 그 변이 장변이면 None 반환 → 스냅 무산.
    edge_len = parent_d if edge in ('L', 'R') else parent_w
    long_side = max(parent_w, parent_d)
    if parent_w != parent_d and abs(edge_len - long_side) < 1e-6:
        return None
    vx, vy = _nearest_vertex_on_edge(wx, wy, edge, bbox)
    w = float(dims.get('width', 1500.0))
    from modular_3d.카탈로그.geometry import SECTION_W_MM
    s = float(SECTION_W_MM)  # 부재 단면(폭 방향)
    # 앵커=0 (좌하점) 좌표 결정 + 회전 자동
    if edge == 'R':
        rot = 0
        new_x = vx
        # 변 위 꼭지점 두 가지 — 위쪽 꼭지점이면 보가 그 꼭지점에서 +x 로 진행하되 y 방향은
        # 보 두께 만큼 안쪽으로(부모 변에 정렬 효과). 단순화: anchor 좌하점이 vx, vy 또는
        # vy - s (꼭지점이 부모 상단 코너인지 하단 코너인지에 따라).
        new_y = vy - s if vy == bbox[3] else vy
    elif edge == 'L':
        rot = 180
        # rotation 180 이면 좌하점이 보의 부모 닿는 끝 + 폭 방향 끝.
        new_x = vx
        new_y = vy if vy == bbox[3] else vy + s
    elif edge == 'T':
        rot = 90
        # [v4 좌표 수정] 좌상 코너에서는 보 단면이 부모 좌측 외곽 보(Y방향)와 일치하도록
        # pos.x = ex0 + s, 우상 코너는 pos.x = ex1. 단면이 부모 외곽과 어긋나지 않게.
        new_x = vx + s if vx == bbox[0] else vx
        new_y = vy
    else:  # B
        rot = 270
        # [v4 좌표 수정] 좌하 → pos.x = ex0, 우하 → pos.x = ex1 - s
        new_x = vx if vx == bbox[0] else vx - s
        new_y = vy
    return float(new_x), float(new_y), rot


# ── 캔틸레버슬래브 ─────────────────────────────────────────
#
# 동작: 부모 외곽 변에 슬래브 한 변(폭) 정렬, 변에 수직 돌출. 변 길이 ≤ 3400 만 후보.
# 슬래브 폭 = 부모 변 길이 자동, 돌출 = 사용자 입력(=원래 dim width 필드).
# 회전 자동 결정 (보와 동일 매핑).
# 꼭지점 스냅: 변의 양 끝 중 가까운 꼭지점에 슬래브 한 모서리(앵커=0 = 좌하점) 위치.
def snap_cantilever_slab(wx, wy, parent, dims, anchor):
    bbox = xy_bbox(parent)
    ex0, ey0, ex1, ey1 = bbox
    edge = _nearest_edge(wx, wy, bbox)

    edge_len = {
        'T': ex1 - ex0, 'B': ex1 - ex0,
        'L': ey1 - ey0, 'R': ey1 - ey0,
    }[edge]
    if edge_len > SLAB_MAX_WIDTH_MM:
        return None

    proj = float(dims.get('width', 1500.0))   # 사용자 입력 = 돌출
    new_dims = {'width': proj, 'depth': edge_len, 'height': 200.0}

    vx, vy = _nearest_vertex_on_edge(wx, wy, edge, bbox)

    # 회전 자동 + anchor 좌하점 좌표 결정. rotation 매핑은 빔과 동일:
    #   R → 0, L → 180, T → 90, B → 270
    # rotation 0 기준 슬래브 로컬: x=0..width(돌출), y=0..depth(폭). 좌하 꼭지점 = (0,0).
    # 회전 후 월드에서 좌하 꼭지점의 위치를 계산해 변의 끝 꼭지점에 일치시킨다.
    if edge == 'R':
        rot = 0
        # 슬래브가 +x 로 돌출, depth 가 +y. 좌하점이 변의 아래쪽 꼭지점에 일치.
        new_x, new_y = ex1, ey0
    elif edge == 'L':
        rot = 180
        # 슬래브가 -x 돌출, depth 가 -y. 앵커(좌하)는 회전 후 변의 위쪽 꼭지점.
        new_x, new_y = ex0, ey1
    elif edge == 'T':
        rot = 90
        new_x, new_y = ex1, ey1
    else:  # B
        rot = 270
        new_x, new_y = ex0, ey0

    return float(new_x), float(new_y), new_dims, rot


# ── 중간기둥 ──────────────────────────────────────────────
# 변 위 마우스 가까운 점에 슬라이딩 (기존 그대로).
def snap_mid_column(wx, wy, parent, dims):
    bbox = xy_bbox(parent)
    ex0, ey0, ex1, ey1 = bbox
    cw = float(dims.get('width', 200.0))
    cd = float(dims.get('depth', 200.0))
    edge = _nearest_edge(wx, wy, bbox)
    if edge == 'L':
        new_x = ex0
        new_y = max(ey0, min(ey1 - cd, wy - cd / 2.0))
    elif edge == 'R':
        new_x = ex1 - cw
        new_y = max(ey0, min(ey1 - cd, wy - cd / 2.0))
    elif edge == 'B':
        new_y = ey0
        new_x = max(ex0, min(ex1 - cw, wx - cw / 2.0))
    else:  # T
        new_y = ey1 - cd
        new_x = max(ex0, min(ex1 - cw, wx - cw / 2.0))
    return float(new_x), float(new_y)


# ── 중간보 ───────────────────────────────────────────────
# 길이 자동. rotation 도 마우스 위치로 자동(부모 모듈 내부에서 마우스가
# 가로로 길게 있으면 x보, 세로로 길게 있으면 y보).
def snap_mid_beam(wx, wy, parent, scene, rotation, dims):
    bbox = xy_bbox(parent)
    ex0, ey0, ex1, ey1 = bbox
    bd = 200.0  # 보 단면

    # [v4] R 키로 가로/세로 사용자 선택.
    #   rotation 0/180 → 가로(x 방향) 중간보
    #   rotation 90/270 → 세로(y 방향) 중간보
    is_x_beam = int(rotation) % 180 == 0

    # 같은 부모 그룹 내 1층 본체 중간기둥 검색
    parent_gid = getattr(parent, 'group_id', 0)
    columns = []
    for cid, comp in scene.components.items():
        if comp.comp_type != ComponentType.MID_COLUMN:
            continue
        if getattr(comp, 'floor_index', 0) != 0 or getattr(comp, 'sub_index', 0) != 0:
            continue
        cbox = xy_bbox(comp)
        ccx = 0.5 * (cbox[0] + cbox[2])
        ccy = 0.5 * (cbox[1] + cbox[3])
        if _is_inside(ccx, ccy, bbox, margin=20.0):
            columns.append((ccx, ccy, cbox))

    # [v5] 부모 외곽 보 두께만큼 안쪽이 사용 가능 영역.
    from modular_3d.카탈로그.geometry import SECTION_W_MM
    s_outer = float(SECTION_W_MM)
    inner_x0 = ex0 + s_outer
    inner_x1 = ex1 - s_outer
    inner_y0 = ey0 + s_outer
    inner_y1 = ey1 - s_outer

    if is_x_beam:
        # 보 중심 y: 기둥(있으면) 또는 마우스 y. 사용 가능 영역으로 clamp.
        pick = None
        if columns:
            pick = min(columns, key=lambda c: abs(c[1] - wy))
        beam_y_center = pick[1] if pick else wy
        beam_y_center = max(inner_y0 + bd / 2.0, min(inner_y1 - bd / 2.0, beam_y_center))
        anchor_y = beam_y_center - bd / 2.0
        # 길이: 같은 y 선상 기둥 중 마우스 좌·우 가장 가까운 1쌍.
        same_line_cols = [c for c in columns if abs(c[1] - beam_y_center) < 50.0]
        left_x = inner_x0   # [v5] 외곽 보 안쪽 한계
        right_x = inner_x1  # [v5]
        for cx, cy, cbox in same_line_cols:
            # 기둥은 사용 가능 영역 안에 있어야 의미 있음
            if cbox[2] <= wx and cbox[2] > left_x:
                left_x = cbox[2]
            if cbox[0] >= wx and cbox[0] < right_x:
                right_x = cbox[0]
        # 마우스가 기둥 위 → 그 기둥 양면을 끝점
        if pick is not None and pick[2][0] <= wx <= pick[2][2]:
            left_x = max(inner_x0, pick[2][0])
            right_x = min(inner_x1, pick[2][2])
        # 외곽 보 한계 강제 — 어떤 경우든 안쪽 한계 넘으면 안 됨
        left_x = max(inner_x0, left_x)
        right_x = min(inner_x1, right_x)
        length = max(200.0, right_x - left_x)
        new_dims = {'width': length, 'depth': bd, 'height': 200.0}
        return float(left_x), float(anchor_y), new_dims, 0
    else:
        pick = None
        if columns:
            pick = min(columns, key=lambda c: abs(c[0] - wx))
        beam_x_center = pick[0] if pick else wx
        beam_x_center = max(inner_x0 + bd / 2.0, min(inner_x1 - bd / 2.0, beam_x_center))
        anchor_x = beam_x_center - bd / 2.0
        same_line_cols = [c for c in columns if abs(c[0] - beam_x_center) < 50.0]
        bot_y = inner_y0
        top_y = inner_y1
        for cx, cy, cbox in same_line_cols:
            if cbox[3] <= wy and cbox[3] > bot_y:
                bot_y = cbox[3]
            if cbox[1] >= wy and cbox[1] < top_y:
                top_y = cbox[1]
        if pick is not None and pick[2][1] <= wy <= pick[2][3]:
            bot_y = max(inner_y0, pick[2][1])
            top_y = min(inner_y1, pick[2][3])
        bot_y = max(inner_y0, bot_y)
        top_y = min(inner_y1, top_y)
        length = max(200.0, top_y - bot_y)
        new_dims = {'width': length, 'depth': bd, 'height': 200.0}
        return float(anchor_x), float(bot_y), new_dims, 90
