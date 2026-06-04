"""거실 단면 정보 다이얼로그 — 배치설계 탭의 "단면 정보" 버튼이 호출.

[2026-06-01] 1단계 골격 / [2026-06-03] 로직 갱신
- 배치설계 탭에서 사용자가 `room_type='living'` 으로 지정한 거실 Room 을 찾는다.
- 거실 폴리곤의 외벽(외부 접촉 변)을 가로/세로 방향별로 합산해 *외부와 가장
  많이 닿은 면* 의 방향을 외벽 방향으로 본다. 그 방향과 *평행* 하게, 거실
  *무게중심* 을 지나는 단면선을 잡는다. (외벽이 가로면 단면축 X, 세로면 Y.)
- 단면선을 교차하는 1층·2층 모듈/벽/패널을 모아 QPainter 로 2D 단면 이미지
  생성. 1·2층만 표시(사용자 요구).
- 단면 표현: 거실 범위만 반투명 빨강, 그 외 부재는 색 없이 외곽선만(사용자 요구).

[정책]
- 좌표 단위 mm (씬 정책과 동일).
- 내부 로직(Scene, Component, Room) 변경 없음 — 읽기만.
- 단면 검출 정확도는 향후 강화: 지금은 축정렬(AABB) 가정.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
                          QPixmap)
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                              QVBoxLayout, QWidget)

try:
    from modular_3d.ui.fonts import F_BODY, F_HEAD
except Exception:
    F_BODY = "Malgun Gothic"
    F_HEAD = "Malgun Gothic"


# ── 기하 유틸 ──────────────────────────────────────────────────────────
def _polygon_centroid(poly: List[Tuple[float, float]]) -> Tuple[float, float]:
    """폴리곤 면적 가중 무게중심. Room.centroid 와 동일 공식."""
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    a = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-9:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    a *= 0.5
    return (cx / (6.0 * a), cy / (6.0 * a))


def _longest_edge(polygon: List[Tuple[float, float]]) -> Optional[Tuple[Tuple[float, float], Tuple[float, float], float]]:
    """폴리곤의 가장 긴 변 — (p0, p1, length). 사용자 의도: 외벽 추정."""
    n = len(polygon)
    if n < 2:
        return None
    best = None
    best_len = -1.0
    for i in range(n):
        p0 = polygon[i]
        p1 = polygon[(i + 1) % n]
        L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if L > best_len:
            best_len = L
            best = (p0, p1, L)
    return best


# ── 도메인 검출 ─────────────────────────────────────────────────────────
def find_living_room(scene: Any) -> Optional[Any]:
    """씬 안에서 첫 번째 거실(room_type='living') Room 객체 반환. 없으면 None."""
    rooms = getattr(scene, 'rooms', None)
    if not rooms:
        return None
    # rooms 가 dict 또는 list 둘 다 대응
    iterable = rooms.values() if hasattr(rooms, 'values') else rooms
    for room in iterable:
        if getattr(room, 'room_type', '') == 'living':
            return room
    return None


def _find_exterior_edges(poly: List[Tuple[float, float]],
                          centroid: Tuple[float, float],
                          scene: Any,
                          probe_dist: float = 300.0
                          ) -> List[Tuple[Tuple[float, float], Tuple[float, float], float]]:
    """거실 polygon 의 각 변 중 *외부와 접한 변* (외벽) 만 추출.

    판정: 변 중점에서 polygon centroid 반대 방향(=외부 방향)으로 probe_dist mm
    떨어진 점이 1F floor_index=0 부재의 AABB 안에 들어가지 않으면 외벽.
    """
    comps = getattr(scene, 'components', None)
    iterable = []
    if comps:
        try:
            iterable = list(comps.values() if hasattr(comps, 'values') else comps)
        except Exception:
            iterable = []
    # 1F 부재의 AABB 만 미리 추출
    floor1_aabbs: List[Tuple[float, float, float, float]] = []
    for c in iterable:
        try:
            if int(getattr(c, 'floor_index', 0) or 0) != 0:
                continue
            aabb = _component_aabb(c)
            if aabb is None:
                continue
            x0, x1, y0, y1, _, _ = aabb
            floor1_aabbs.append((x0, x1, y0, y1))
        except Exception:
            continue

    cx, cy = centroid
    out: List[Tuple[Tuple[float, float], Tuple[float, float], float]] = []
    n = len(poly)
    for i in range(n):
        p0 = poly[i]
        p1 = poly[(i + 1) % n]
        dx_e = p1[0] - p0[0]
        dy_e = p1[1] - p0[1]
        L = math.hypot(dx_e, dy_e)
        if L < 1e-6:
            continue
        mx = (p0[0] + p1[0]) / 2.0
        my = (p0[1] + p1[1]) / 2.0
        # 변에 수직, 외부(centroid 반대) 방향
        perp_x, perp_y = -dy_e, dx_e
        plen = math.hypot(perp_x, perp_y)
        perp_x /= plen
        perp_y /= plen
        # centroid 와 같은 방향이면 반대(외부) 로
        if perp_x * (cx - mx) + perp_y * (cy - my) > 0:
            perp_x = -perp_x
            perp_y = -perp_y
        probe_x = mx + perp_x * probe_dist
        probe_y = my + perp_y * probe_dist
        # 외부 probe 가 어떤 1F 부재 AABB 안에 있으면 내벽(공유), 없으면 외벽
        is_interior = False
        for (x0, x1, y0, y1) in floor1_aabbs:
            if x0 - 10 <= probe_x <= x1 + 10 and y0 - 10 <= probe_y <= y1 + 10:
                is_interior = True
                break
        if not is_interior:
            out.append((p0, p1, L))
    return out


def _polygon_axis_intersections(poly: List[Tuple[float, float]], axis: str,
                                 value: float, eps: float = 1.0
                                 ) -> List[Tuple[float, float]]:
    """단면선이 폴리곤 *내부* 를 지나는 구간 [a_start, a_end] 들을 반환.

    axis='x' → 단면 평면 y=value, 교차점의 x 좌표 구간.
    axis='y' → 단면 평면 x=value, 교차점의 y 좌표 구간.

    거실 색칠을 "단면선이 실제로 거실 안을 지나는 구간"으로 정확히 맞추기 위함.
    (기존엔 거실 bbox 전체를 칠해 거실이 아닌 곳까지 빨강이 되던 문제를 해결.)
    """
    n = len(poly)
    if n < 3:
        return []
    crossings: List[float] = []
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if axis == 'x':
            c0, c1, a0, a1 = y0, y1, x0, x1   # value 와 비교=y, 결과축=x
        else:
            c0, c1, a0, a1 = x0, x1, y0, y1   # value 와 비교=x, 결과축=y
        # 반열린 구간으로 꼭짓점 중복 카운트 방지
        if (c0 <= value < c1) or (c1 <= value < c0):
            t = (value - c0) / (c1 - c0)
            crossings.append(a0 + t * (a1 - a0))
    crossings.sort()
    # 교차점을 짝으로 묶으면 내부 구간 (even-odd rule)
    intervals: List[Tuple[float, float]] = []
    for k in range(0, len(crossings) - 1, 2):
        lo, hi = crossings[k], crossings[k + 1]
        if hi - lo > eps:
            intervals.append((lo, hi))
    return intervals


def _chain_segments_to_loops(segments: List[Any], tol: float = 1.5
                             ) -> List[List[Tuple[float, float]]]:
    """단면 슬라이스 선분들을 끝점 연결로 이어 닫힌 루프(폴리곤)로 묶는다.

    부재 mesh 를 평면으로 자르면 잘린 단면 외곽이 (a,z) 선분들로 나온다. 이를
    루프로 이어 *면* 으로 채우면 잘린 보/기둥/슬래브가 채워진 단면 형상으로 보인다.
    """
    segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for srec in segments:
        if len(srec) >= 2:
            a = (float(srec[0][0]), float(srec[0][1]))
            b = (float(srec[1][0]), float(srec[1][1]))
            if abs(a[0] - b[0]) > 1e-6 or abs(a[1] - b[1]) > 1e-6:
                segs.append((a, b))
    n = len(segs)
    used = [False] * n

    def near(p, q):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol

    loops: List[List[Tuple[float, float]]] = []
    for i in range(n):
        if used[i]:
            continue
        used[i] = True
        chain = [segs[i][0], segs[i][1]]
        extended = True
        while extended:
            extended = False
            if near(chain[-1], chain[0]):
                break
            tail = chain[-1]
            for j in range(n):
                if used[j]:
                    continue
                a, b = segs[j]
                if near(tail, a):
                    chain.append(b); used[j] = True; extended = True; break
                if near(tail, b):
                    chain.append(a); used[j] = True; extended = True; break
        if len(chain) >= 3:
            loops.append(chain)
    return loops


def _living_unit_bounds(scene: Any, poly: List[Tuple[float, float]],
                         centroid: Tuple[float, float]
                         ) -> Optional[Tuple[Tuple[float, float, float, float], set]]:
    """거실이 포함된 1층 단위(모듈/패널 등)들의 합집합 XY-AABB + 멤버 id 집합.

    사용자 정의의 "단위": 모듈만 있으면 거실이 든 모듈, 모듈+패널이면 둘을
    하나로 본다. footprint 가 거실의 20% 이상인 1층 부재 중, 거실 무게중심을
    품거나 거실 면적의 10% 이상을 덮는 것들을 단위로 모은다(기둥/보 등 가는
    부재는 면적 기준에서 제외). 반환 ((ux0,ux1,uy0,uy1), member_ids) 또는 None.
    """
    comps = getattr(scene, 'components', None)
    if not comps:
        return None
    iterable = comps.values() if hasattr(comps, 'values') else comps
    rx0 = min(p[0] for p in poly); rx1 = max(p[0] for p in poly)
    ry0 = min(p[1] for p in poly); ry1 = max(p[1] for p in poly)
    r_area = max((rx1 - rx0) * (ry1 - ry0), 1.0)
    cx, cy = centroid
    members: List[Tuple[float, float, float, float]] = []
    member_ids: set = set()
    for comp in iterable:
        try:
            if int(getattr(comp, 'floor_index', 0) or 0) != 0:
                continue
            aabb = _component_aabb(comp)
            if aabb is None:
                continue
            x0, x1, y0, y1, _, _ = aabb
        except Exception:
            continue
        c_area = max((x1 - x0) * (y1 - y0), 1.0)
        ox = max(0.0, min(x1, rx1) - max(x0, rx0))
        oy = max(0.0, min(y1, ry1) - max(y0, ry0))
        inter = ox * oy
        contains_centroid = (x0 <= cx <= x1 and y0 <= cy <= y1)
        big_enough = c_area >= 0.2 * r_area     # 모듈/패널 급만, 기둥·보 제외
        if big_enough and (contains_centroid or inter >= 0.1 * r_area):
            members.append((x0, x1, y0, y1))
            member_ids.add(id(comp))
    if not members:
        return None
    ux0 = min(m[0] for m in members); ux1 = max(m[1] for m in members)
    uy0 = min(m[2] for m in members); uy1 = max(m[3] for m in members)
    return (ux0, ux1, uy0, uy1), member_ids


def _rect_exterior_lengths(rect: Tuple[float, float, float, float], scene: Any,
                            member_ids: set, probe_dist: float = 300.0
                            ) -> Tuple[float, float]:
    """단위 사각형의 네 변 중 외부와 접한 변 길이를 방향별로 합산.

    반환 (ext_len_x, ext_len_y):
      ext_len_x = 외부와 접한 *가로(X평행)* 변(위/아래) 총 길이.
      ext_len_y = 외부와 접한 *세로(Y평행)* 변(좌/우) 총 길이.
    판정: 변의 25/50/75% 지점에서 바깥으로 probe_dist 나간 점이 단위 멤버가
    아닌 다른 1층 부재 AABB 안에 없으면(=과반 표결) 그 변은 외부.
    """
    ux0, ux1, uy0, uy1 = rect
    comps = getattr(scene, 'components', None)
    others: List[Tuple[float, float, float, float]] = []
    if comps:
        iterable = comps.values() if hasattr(comps, 'values') else comps
        for comp in iterable:
            try:
                if id(comp) in member_ids:
                    continue
                if int(getattr(comp, 'floor_index', 0) or 0) != 0:
                    continue
                aabb = _component_aabb(comp)
                if aabb is None:
                    continue
                x0, x1, y0, y1, _, _ = aabb
                others.append((x0, x1, y0, y1))
            except Exception:
                continue

    def _exterior_point(px: float, py: float) -> bool:
        for (x0, x1, y0, y1) in others:
            if x0 - 10 <= px <= x1 + 10 and y0 - 10 <= py <= y1 + 10:
                return False
        return True

    def _side_exterior(lo: float, hi: float, fixed: float,
                       along: str, outward: float) -> bool:
        votes = 0
        for frac in (0.25, 0.5, 0.75):
            a = lo + frac * (hi - lo)
            if along == 'x':
                px, py = a, fixed + outward * probe_dist
            else:
                px, py = fixed + outward * probe_dist, a
            if _exterior_point(px, py):
                votes += 1
        return votes >= 2

    w = ux1 - ux0   # 가로(X) 길이
    d = uy1 - uy0   # 세로(Y) 길이
    ext_len_x = 0.0
    ext_len_y = 0.0
    # 가로(X평행) 변 = 아래(y=uy0, 바깥 -y) / 위(y=uy1, 바깥 +y)
    if _side_exterior(ux0, ux1, uy0, 'x', -1.0):
        ext_len_x += w
    if _side_exterior(ux0, ux1, uy1, 'x', +1.0):
        ext_len_x += w
    # 세로(Y평행) 변 = 좌(x=ux0, 바깥 -x) / 우(x=ux1, 바깥 +x)
    if _side_exterior(uy0, uy1, ux0, 'y', -1.0):
        ext_len_y += d
    if _side_exterior(uy0, uy1, ux1, 'y', +1.0):
        ext_len_y += d
    return ext_len_x, ext_len_y


def compute_section_line(room: Any, inset_mm: float = 2000.0,
                          scene: Any = None) -> Optional[Dict[str, Any]]:
    """거실이 포함된 *단위(모듈/패널 묶음)* 의 **외부와 가장 많이 접한 면 방향**
    과 평행하게, 거실 **무게중심**을 지나는 단면선.

    [2026-06-04] 사용자 요구로 로직 변경:
      - 기준 단위: 거실이 든 1층 모듈/패널들의 합집합(_living_unit_bounds). 그
        사각형의 외부 접촉 변을 방향별(가로/세로)로 합산해 더 많이 접한 쪽을
        외벽 방향으로 채택(_rect_exterior_lengths).
      - 단위 검출/외벽 판정 실패 시 → 거실 폴리곤 외벽(_find_exterior_edges)
        → 그것도 실패 시 가장 긴 변(_longest_edge) 순으로 fallback.
      - 절단 위치: 거실 무게중심. (inset_mm 인자는 하위호환용·미사용.)
      - 거실이 2개 이상이어도 find_living_room 이 1개만 반환 → 1개만 사용.
    """
    poly = list(getattr(room, 'polygon', []) or [])
    if not poly:
        return None
    cx, cy = _polygon_centroid(poly)

    # ── 외벽 방향 결정 — 1) 단위 외부면 → 2) 거실 폴리곤 외벽 → 3) 최장변 ──
    ext_len_x = 0.0   # 가로(X 평행) 외부 접촉 총 길이
    ext_len_y = 0.0   # 세로(Y 평행) 외부 접촉 총 길이
    source = 'longest_edge'
    if scene is not None:
        unit = _living_unit_bounds(scene, poly, (cx, cy))
        if unit is not None:
            rect, member_ids = unit
            ext_len_x, ext_len_y = _rect_exterior_lengths(rect, scene, member_ids)
            if ext_len_x > 0.0 or ext_len_y > 0.0:
                source = 'unit'
        if ext_len_x <= 0.0 and ext_len_y <= 0.0:
            # 단위 외벽 검출 실패 → 거실 폴리곤 변 기준
            for (e0, e1, L) in _find_exterior_edges(poly, (cx, cy), scene):
                if abs(e1[0] - e0[0]) >= abs(e1[1] - e0[1]):
                    ext_len_x += L
                else:
                    ext_len_y += L
            if ext_len_x > 0.0 or ext_len_y > 0.0:
                source = 'room_edge'

    if ext_len_x <= 0.0 and ext_len_y <= 0.0:
        edge = _longest_edge(poly)
        if edge is None:
            return None
        e0, e1, L = edge
        horizontal = abs(e1[0] - e0[0]) >= abs(e1[1] - e0[1])
        dominant_len = L
    else:
        horizontal = ext_len_x >= ext_len_y
        dominant_len = max(ext_len_x, ext_len_y)

    # ── 절단선 — 외벽 방향과 평행, 무게중심을 지남 ──
    # 외부면이 가로(X평행)면 단면 평면 y=상수 → 단면축 'x'(X-Z 단면), 값=무게중심 y.
    # 외부면이 세로(Y평행)면 단면 평면 x=상수 → 단면축 'y'(Y-Z 단면), 값=무게중심 x.
    if horizontal:
        axis = 'x'
        value = float(cy)
        room_extent = (min(p[0] for p in poly), max(p[0] for p in poly))
    else:
        axis = 'y'
        value = float(cx)
        room_extent = (min(p[1] for p in poly), max(p[1] for p in poly))

    try:
        print(f"[section] source={source} ext_x={ext_len_x:.0f} ext_y={ext_len_y:.0f} "
              f"axis={axis} value={value:.0f} centroid=({cx:.0f},{cy:.0f})", flush=True)
    except Exception:
        pass

    return {
        'axis': axis,
        'value': value,
        'edge_length_mm': float(dominant_len),
        'room_extent': (float(room_extent[0]), float(room_extent[1])),
        # [2026-06-04] 거실 폴리곤 — 색칠을 단면선∩거실 구간으로 정확히 그리기 위함
        'room_poly': [(float(p[0]), float(p[1])) for p in poly],
        # [2026-06-04] 거실이 속한 층 — 빨강을 그 층 높이에만 칠해 위/아래 층으로
        # 번지지 않게(거실이 다른 층 모듈을 벗어나 보이던 문제).
        'room_floor': int(getattr(room, 'floor_index', 0) or 0),
        'inset_mm': float(inset_mm),
    }


def _component_aabb(comp: Any) -> Optional[Tuple[float, float, float, float, float, float]]:
    """Component 의 월드 AABB (x0,x1,y0,y1,z0,z1). 측정 실패 시 None.

    각 단계마다 try/except — 어떤 컴포넌트에서 메서드/속성 누락돼도
    전체 검출이 멈추지 않게 함.
    """
    # ── 1차 시도: get_world_corners() ──
    try:
        if hasattr(comp, 'get_world_corners'):
            corners = comp.get_world_corners()
            if corners is not None and len(corners) > 0:
                xs, ys, zs = [], [], []
                for c in corners:
                    try:
                        xs.append(float(c[0]))
                        ys.append(float(c[1]))
                        zs.append(float(c[2]) if len(c) >= 3 else 0.0)
                    except Exception:
                        continue
                if xs and ys:
                    x0, x1 = min(xs), max(xs)
                    y0, y1 = min(ys), max(ys)
                    z0 = min(zs) if zs else 0.0
                    z1 = max(zs) if zs else 0.0
                    # 높이가 0 이면 dimensions.height 보강
                    if abs(z1 - z0) < 1.0:
                        try:
                            d = getattr(comp, 'dimensions', {}) or {}
                            h = float(d.get('height', 0) or 0)
                            if h > 0:
                                z1 = z0 + h
                        except Exception:
                            pass
                    return (x0, x1, y0, y1, z0, z1)
    except Exception:
        pass

    # ── 2차 폴백: position + dimensions (회전 무시) ──
    try:
        if not hasattr(comp, 'position'):
            return None
        pos = comp.position
        d = getattr(comp, 'dimensions', {}) or {}
        if not isinstance(d, dict):
            d = {}
        w = float(d.get('width', 0) or 0)
        de = float(d.get('depth', 0) or 0)
        h = float(d.get('height', 0) or 0)
        px = float(pos[0])
        py = float(pos[1])
        pz = float(pos[2]) if (hasattr(pos, '__len__') and len(pos) >= 3) else 0.0
        return (px - w / 2.0, px + w / 2.0,
                py - de / 2.0, py + de / 2.0,
                pz, pz + h)
    except Exception:
        return None


def compute_building_bounds(scene: Any) -> Optional[Tuple[float, float, float, float]]:
    """씬 전체 부재의 X-Y AABB 범위 (x_min, x_max, y_min, y_max).

    단면 캔버스 가로 폭을 건물 전체 폭으로 확장하기 위함 — 거실쪽만 보이는
    문제를 진단/완화. 부재가 없으면 None.
    """
    comps = getattr(scene, 'components', None)
    if not comps:
        return None
    iterable = comps.values() if hasattr(comps, 'values') else comps
    xs0, xs1, ys0, ys1 = [], [], [], []
    for comp in iterable:
        try:
            aabb = _component_aabb(comp)
            if aabb is None:
                continue
            x0, x1, y0, y1, _, _ = aabb
            xs0.append(x0); xs1.append(x1)
            ys0.append(y0); ys1.append(y1)
        except Exception:
            continue
    if not xs0:
        return None
    return (min(xs0), max(xs1), min(ys0), max(ys1))


def slice_component_mesh(comp: Any, section: Dict[str, Any]
                          ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """[2026-06-02] 진짜 2D 단면 — 부재 mesh 의 모든 삼각형을 단면 평면과 교차
    시켜 절단 선분들을 추출.

    section['axis']='x' → 절단 평면 y = section['value']
    section['axis']='y' → 절단 평면 x = section['value']

    반환: 선분 리스트 [((a0, z0), (a1, z1)), ...]  — 단면 a-축, z-축 좌표.
    """
    try:
        from modular_3d.render.mesh_builder import build_component_mesh
        import numpy as np
        v, f, c = build_component_mesh(comp)
    except Exception:
        return []
    if v is None or f is None or len(v) == 0 or len(f) == 0:
        return []
    has_color = (c is not None) and (len(c) == len(v))
    # [2026-06-02] 진단 — mesh 색 다양성 콘솔 출력 (이 부재의 mesh 안에
    # sub-part 별로 다른 색이 들어있는지 확인용).
    try:
        if has_color and len(c) > 0:
            uniq = set()
            for row in c[:500]:
                uniq.add((round(float(row[0]), 2),
                          round(float(row[1]), 2),
                          round(float(row[2]), 2)))
            print(f"[mesh-color] {type(comp).__name__}: verts={len(v)} "
                  f"unique_colors={len(uniq)} 샘플={list(uniq)[:6]}",
                  flush=True)
        else:
            print(f"[mesh-color] {type(comp).__name__}: 색정보 없음 "
                  f"(c={None if c is None else c.shape})", flush=True)
    except Exception:
        pass
    axis = section['axis']
    val = float(section['value'])
    # 각 정점의 평면 부호거리 (axis='x' → d = v[:,1] - val)
    if axis == 'x':
        dist = v[:, 1] - val
    else:
        dist = v[:, 0] - val

    segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    EPS = 1e-6
    for tri in f:
        i0, i1, i2 = int(tri[0]), int(tri[1]), int(tri[2])
        d0, d1, d2 = dist[i0], dist[i1], dist[i2]
        if (d0 > EPS and d1 > EPS and d2 > EPS) or \
           (d0 < -EPS and d1 < -EPS and d2 < -EPS):
            continue
        pts: List[Tuple[float, float]] = []
        for (ia, ib) in ((i0, i1), (i1, i2), (i2, i0)):
            da, db = dist[ia], dist[ib]
            if da * db < 0:
                t = da / (da - db)
                px = v[ia] + t * (v[ib] - v[ia])
                if axis == 'x':
                    pts.append((float(px[0]), float(px[2])))
                else:
                    pts.append((float(px[1]), float(px[2])))
            elif abs(da) < EPS:
                if axis == 'x':
                    pts.append((float(v[ia][0]), float(v[ia][2])))
                else:
                    pts.append((float(v[ia][1]), float(v[ia][2])))
        uniq: List[Tuple[float, float]] = []
        for p in pts:
            if not any(abs(p[0] - q[0]) < 0.5 and abs(p[1] - q[1]) < 0.5 for q in uniq):
                uniq.append(p)
        if len(uniq) >= 2:
            # [2026-06-02] 삼각형의 mesh vertex color 평균 — 단면 선분 색.
            # mesh_builder 가 sub-part 별로 다른 색을 줘서 (기둥=빨강, 보=파랑 등),
            # 평균을 그대로 쓰면 단면 선이 그 부재 sub-part 색을 따름.
            tri_rgb = None
            if has_color:
                try:
                    avg = (c[i0] + c[i1] + c[i2]) / 3.0
                    tri_rgb = (float(avg[0]), float(avg[1]), float(avg[2]))
                except Exception:
                    tri_rgb = None
            segments.append((uniq[0], uniq[1], tri_rgb))
    return segments


def collect_section_components(scene: Any, section: Dict[str, Any],
                                max_floor: int = 2,
                                tol: float = 150.0) -> List[Dict[str, Any]]:
    """단면선을 교차하는 모듈 중 floor_index < max_floor (즉 1·2층) 만 모음."""
    comps = getattr(scene, 'components', None)
    if not comps:
        return []
    iterable = comps.values() if hasattr(comps, 'values') else comps
    axis = section['axis']
    val = float(section['value'])
    out: List[Dict[str, Any]] = []
    for comp in iterable:
        try:
            fi_raw = getattr(comp, 'floor_index', 0)
            fi = int(fi_raw) if fi_raw is not None else 0
        except Exception:
            fi = 0
        if fi >= max_floor:
            continue
        try:
            aabb = _component_aabb(comp)
        except Exception:
            aabb = None
        if aabb is None:
            continue
        try:
            x0, x1, y0, y1, z0, z1 = aabb
            crosses = False
            if axis == 'x':
                if (y0 - tol) <= val <= (y1 + tol):
                    crosses = True
                    record = {'comp': comp, 'a0': x0, 'a1': x1,
                              'z0': z0, 'z1': z1, 'floor_index': fi,
                              'type': type(comp).__name__}
            else:
                if (x0 - tol) <= val <= (x1 + tol):
                    crosses = True
                    record = {'comp': comp, 'a0': y0, 'a1': y1,
                              'z0': z0, 'z1': z1, 'floor_index': fi,
                              'type': type(comp).__name__}
            if crosses:
                # mesh 교차 선분 — 진짜 2D 단면 외곽선
                try:
                    record['segments'] = slice_component_mesh(comp, section)
                except Exception:
                    record['segments'] = []
                out.append(record)
        except Exception:
            continue

    # ── 같은 floor_index 부재의 바닥 레벨(z0) 통일 ──
    # 모듈/패널/벽마다 position[2] 기준점이 달라(중앙/모서리/앵커) z0 가
    # 들쭉날쭉 잡히는 문제. 같은 층은 같은 바닥 레벨로 모이는 게 맞으니,
    # 층별 z0 의 중앙값을 그 층 바닥으로 강제.
    if out:
        from statistics import median
        floors = {c['floor_index'] for c in out}
        floor_z0 = {fi: median([c['z0'] for c in out if c['floor_index'] == fi])
                    for fi in floors}
        floor_z1 = {fi: median([c['z1'] for c in out if c['floor_index'] == fi])
                    for fi in floors}
        for c in out:
            fi = c['floor_index']
            h_orig = c['z1'] - c['z0']
            # 슬래브·바닥(얇은 부재)은 그대로 두고, 모듈·벽 같은 큰 부재만
            # 층 바닥/천장에 맞춤. 두께 < 500mm 면 슬래브로 간주.
            if h_orig < 500:
                # 슬래브는 층 바닥(z0) 근처로 스냅
                c['z0'] = floor_z0[fi]
                c['z1'] = floor_z0[fi] + h_orig
            else:
                # 모듈 등은 층 바닥부터 층 천장까지로 정렬
                c['z0'] = floor_z0[fi]
                c['z1'] = floor_z1[fi]

    return out


# ── 다이얼로그 ──────────────────────────────────────────────────────────
class SectionViewerDialog(QDialog):
    """거실 단면 정보 — 1F + 2F 만 표시."""

    def __init__(self, scene: Any, parent: Optional[QWidget] = None,
                 capture_pixmap=None) -> None:
        """capture_pixmap: 3D 뷰의 정면 클리핑 캡처(QPixmap). 주어지면 2D
        그리기 대신 그 이미지를 그대로 표시. 없으면 2D fallback.
        """
        super().__init__(parent)
        self.setWindowTitle("거실 단면 정보 — 1F + 2F")
        self.resize(1700, 1080)
        self._scene = scene
        self._capture_pixmap = capture_pixmap
        self._build_ui()
        self._compute_and_render()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        # 상단 row — 타이틀 + 우상단 범례 (캔버스 밖)
        top_row = QHBoxLayout()
        title = QLabel("거실 단면 (외벽 평행 절단 · 1F+2F)")
        title.setStyleSheet(
            f"font-family: '{F_HEAD}'; font-weight: 700; font-size: 18px; color: #1F4E79;"
        )
        top_row.addWidget(title)
        top_row.addStretch(1)
        # [2026-06-02] 범례 제거 — 사용자 요청.
        v.addLayout(top_row)

        self._info = QLabel("")
        self._info.setStyleSheet(
            f"font-family: '{F_BODY}'; font-size: 13px; color: #324A5F; padding: 4px 0;"
        )
        self._info.setWordWrap(True)
        v.addWidget(self._info)

        self._canvas = QLabel()
        self._canvas.setMinimumHeight(920)
        self._canvas.setMinimumWidth(1400)
        self._canvas.setAlignment(Qt.AlignCenter)
        self._canvas.setStyleSheet(
            "background: #FFFFFF; border: 1.5px solid #C9D2DD; border-radius: 14px;"
        )
        v.addWidget(self._canvas, stretch=1)

        hb = QHBoxLayout()
        hb.addStretch(1)
        close_btn = QPushButton("닫기")
        close_btn.setStyleSheet(
            f"font-family: '{F_BODY}'; font-size: 13px; padding: 7px 22px;"
            " border: 1px solid #1F4E79; border-radius: 8px;"
            " background: #FFFFFF; color: #1F4E79;"
        )
        close_btn.clicked.connect(self.accept)
        hb.addWidget(close_btn)
        v.addLayout(hb)

    def _compute_and_render(self) -> None:
        room = find_living_room(self._scene)
        if room is None:
            self._info.setText(
                "거실(room_type='living')로 지정된 실이 없습니다. "
                "배치설계 탭에서 실 지정 후 '거실'을 선택하세요."
            )
            self._draw_empty("거실 미지정")
            return

        section = compute_section_line(room, inset_mm=2000.0, scene=self._scene)
        if section is None:
            self._info.setText("거실 폴리곤이 비어 있어 단면을 계산할 수 없습니다.")
            self._draw_empty("폴리곤 없음")
            return

        comps = collect_section_components(self._scene, section, max_floor=2)
        if not comps:
            self._info.setText(
                f"단면선 ({section['axis']} = {section['value']:.0f} mm) 을 "
                "교차하는 1F·2F 모듈이 없습니다."
            )
            self._draw_empty("교차 모듈 없음")
            return

        # 진단: 부재 타입별 카운트 + 색 분류 + mesh 색 다양성 (콘솔 + 안내문)
        type_counts: Dict[str, int] = {}
        mesh_color_variety: Dict[str, int] = {}
        for c in comps:
            t = c['type']
            type_counts[t] = type_counts.get(t, 0) + 1
            segs = c.get('segments') or []
            # 이 부재의 segment 색 다양성
            uniq_seg_colors = set()
            for seg in segs:
                if len(seg) >= 3 and seg[2] is not None:
                    uniq_seg_colors.add((round(seg[2][0], 1),
                                         round(seg[2][1], 1),
                                         round(seg[2][2], 1)))
            mesh_color_variety[t] = max(mesh_color_variety.get(t, 0),
                                         len(uniq_seg_colors))
        type_summary = " · ".join(f"{t} {n}" for t, n in sorted(type_counts.items()))
        color_diag = " · ".join(f"{t}:색{n}종" for t, n in sorted(mesh_color_variety.items()))
        print(f"[section_viewer] 단면축={section['axis']} 값={section['value']:.0f}mm "
              f"tol={150}mm 잡힌부재={len(comps)} ({type_summary})", flush=True)
        # 각 타입이 어느 색 카테고리로 분류되는지 출력
        def _classify(tname: str) -> str:
            t = tname.lower()
            if 'column' in t: return '기둥(빨강)'
            if 'beam' in t: return '보(주황)'
            if 'slab' in t or 'panel' in t or 'floor' in t: return '슬래브/패널(회색)'
            if 'wall' in t: return '벽(파랑)'
            if 'core' in t: return '코어(보라)'
            if 'module' in t: return '모듈(베이지)'
            return '기타'
        class_map = {t: _classify(t) for t in type_counts}
        print(f"[section_viewer] 색분류: {class_map}", flush=True)

        floors_str = ", ".join(f"{fi+1}F" for fi in sorted({c['floor_index'] for c in comps}))
        re = section.get('room_extent', (0.0, 0.0))
        self._info.setText(
            f"단면 축 = {section['axis'].upper()} · "
            f"외벽 평행 · 거실 무게중심 절단 · "
            f"잡힌 부재 {len(comps)}개 → [{type_summary}] · "
            f"mesh 색 다양성: [{color_diag}] · "
            f"거실 범위 {re[0]:.0f}~{re[1]:.0f}mm · 표시 층 = {floors_str}"
        )
        # 건물 전체 범위(단면 캔버스 가로 폭 확장용)
        self._building_bounds = compute_building_bounds(self._scene)
        self._draw_section(comps, section)

    # ── 캔버스 그리기 ─────────────────────────────────────────────
    def _draw_empty(self, msg: str) -> None:
        cw = max(800, self._canvas.width())
        ch = max(420, self._canvas.height())
        pix = QPixmap(cw, ch)
        pix.fill(QColor("#FFFFFF"))
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#9AA6B2"), 1))
        p.setFont(QFont(F_BODY, 14))
        p.drawText(pix.rect(), Qt.AlignCenter, msg)
        p.end()
        self._canvas.setPixmap(pix)

    def _draw_section(self, comps: List[Dict[str, Any]], section: Dict[str, Any]) -> None:
        cw = max(880, self._canvas.width())
        ch = max(440, self._canvas.height())
        pix = QPixmap(cw, ch)
        pix.fill(QColor("#FFFFFF"))
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)

        # 캔버스 가로 폭 = 건물 전체 평면 폭 (잡힌 부재만이 아니라).
        # "거실쪽만 보임" 진단을 위해 — 단면선이 통과하는 부재가 거실쪽뿐이면
        # 캔버스가 전체 폭이라 빈 공간이 보임 → 단면이 다른 실 부재를 안 지나감 확정.
        bb = getattr(self, '_building_bounds', None)
        if bb is not None:
            if section['axis'] == 'x':
                # 단면 가로축 = X
                a_min, a_max = float(bb[0]), float(bb[1])
            else:
                # 단면 가로축 = Y
                a_min, a_max = float(bb[2]), float(bb[3])
            # 잡힌 부재가 건물 범위 밖에 살짝 걸치는 경우 확장
            a_min = min(a_min, min(c['a0'] for c in comps))
            a_max = max(a_max, max(c['a1'] for c in comps))
        else:
            a_min = min(c['a0'] for c in comps)
            a_max = max(c['a1'] for c in comps)
        z_min = min(c['z0'] for c in comps)
        z_max = max(c['z1'] for c in comps)
        if a_max - a_min < 1.0:
            a_max = a_min + 1000.0
        if z_max - z_min < 1.0:
            z_max = z_min + 1000.0

        # [2026-06-02] padding 축소 — 단면 영역 더 크게. 좌측은 1F/2F 라벨
        # 공간으로 약간 남겨둠.
        pad_left = 50
        pad_right = 15
        pad_top = 15
        pad_bot = 15
        avail_w = cw - pad_left - pad_right
        avail_h = ch - pad_top - pad_bot
        # [2026-06-02] 비율 유지 — sx, sy 중 작은 쪽 채택. 단면을 캔버스
        # 가운데 정렬. 범례는 캔버스 밖 별도 위젯이라 겹침 걱정 X.
        sx = avail_w / (a_max - a_min)
        sy = avail_h / (z_max - z_min)
        s = min(sx, sy)
        sx = sy = s
        # 가운데 정렬
        ox = pad_left + (avail_w - (a_max - a_min) * s) / 2.0
        oy = pad_top + (avail_h - (z_max - z_min) * s) / 2.0

        def tx(a: float) -> float:
            return ox + (a - a_min) * s

        def ty(z: float) -> float:
            return oy + (z_max - z) * s

        # ── 거실 음영 — 단면선이 실제로 거실 내부를 지나는 구간만 반투명 빨강 ──
        # (라이브 경로 render_section_pixmap 과 동일 정책: bbox 전체 칠하지 않음)
        room_poly = section.get('room_poly')
        sec_axis = section.get('axis', 'x')
        living_intervals: List[Tuple[float, float]] = []
        if room_poly:
            for (lo, hi) in _polygon_axis_intersections(
                    room_poly, sec_axis, float(section['value'])):
                c0 = max(lo, a_min); c1 = min(hi, a_max)
                if c1 > c0:
                    living_intervals.append((c0, c1))
        # 빨강은 거실이 속한 *층(room_floor)* 의 높이 범위에만 칠한다.
        rf = section.get('room_floor', 0)
        floor_comps = [c for c in comps if c.get('floor_index') == rf]
        if floor_comps:
            rz0 = min(c['z0'] for c in floor_comps)
            rz1 = max(c['z1'] for c in floor_comps)
        else:
            rz0, rz1 = z_min, z_max
        shade_y_top = ty(rz1)
        shade_y_bot = ty(rz0)
        if living_intervals:
            p.setBrush(QBrush(QColor(220, 60, 60, 90)))
            p.setPen(Qt.NoPen)
            for (c0, c1) in living_intervals:
                p.drawRect(QRectF(tx(c0), shade_y_top,
                                   (c1 - c0) * sx, shade_y_bot - shade_y_top))
            # "거실" 텍스트 — 가장 넓은 거실 구간 가운데
            c0, c1 = max(living_intervals, key=lambda iv: iv[1] - iv[0])
            p.setFont(QFont(F_HEAD, 18, QFont.Bold))
            p.setPen(QPen(QColor(180, 30, 30), 1))
            room_cx = (tx(c0) + tx(c1)) / 2.0
            room_cy = (shade_y_top + shade_y_bot) / 2.0
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance("거실")
            p.drawText(QPointF(room_cx - tw / 2.0, room_cy + 6), "거실")

        # [2026-06-03] 사용자 요구 — 거실만 반투명 빨강, 그 외 부재는 *색 없이*
        # 외곽선(짙은 회색)만. 부재 채움색·mesh sub-part 색을 모두 제거하고
        # 단면 윤곽선만 단색으로 그린다. (라이브 경로 render_section_pixmap 과 동일)
        _OUTLINE = QColor("#333333")
        for c in comps:
            segs = c.get('segments') or []
            x0 = tx(c['a0']); x1 = tx(c['a1'])
            y_top = ty(c['z1']); y_bot = ty(c['z0'])
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_OUTLINE, 1.6))
            if segs and len(segs) >= 2:
                # 진짜 2D 단면 — mesh 교차 선분 윤곽
                for seg in segs:
                    a = seg[0]; b = seg[1]
                    p.drawLine(QPointF(tx(a[0]), ty(a[1])),
                               QPointF(tx(b[0]), ty(b[1])))
            else:
                # mesh 교차 없음 → AABB 사각형 윤곽 fallback
                p.drawRect(QRectF(x0, y_top, x1 - x0, y_bot - y_top))

        # 층 라벨 (좌측)
        p.setFont(QFont(F_HEAD, 12, QFont.Bold))
        p.setPen(QPen(QColor("#1F4E79"), 1))
        floors = sorted({c['floor_index'] for c in comps})
        for fi in floors:
            zs0 = [c['z0'] for c in comps if c['floor_index'] == fi]
            zs1 = [c['z1'] for c in comps if c['floor_index'] == fi]
            if zs0 and zs1:
                ymid = (ty(min(zs0)) + ty(max(zs1))) / 2.0
                p.drawText(QPointF(14, ymid + 5), f"{fi + 1}F")
                # 점선 가이드 (좌)
                p.setPen(QPen(QColor("#C9D2DD"), 1, Qt.DashLine))
                p.drawLine(QPointF(50, ty(min(zs0))),
                           QPointF(cw - pad_right, ty(min(zs0))))
                p.setPen(QPen(QColor("#1F4E79"), 1))

        # [2026-06-02] 범례는 캔버스 밖 별도 QLabel (다이얼로그 우상단)
        # 으로 옮김 → 단면과 절대 안 겹침.

        # [2026-06-01] 사용자 요청 — 하단 절단선 정보 라벨 제거

        # [2026-06-01] 사용자 요청 — 하단 척도 화살표 제거

        p.end()
        self._canvas.setPixmap(pix)


def render_section_pixmap(scene: Any, max_size: int = 2000,
                            height: Optional[int] = None) -> Optional[QPixmap]:
    """[2026-06-02] case scene 으로 거실 단면 QPixmap 생성.

    - 거실 양옆 *바로 인접한* 부재까지만 a 범위 (전체 건물 X)
    - 픽스맵 크기 = max_size × (단면 비율) — _AutoScalePixmapLabel 가 카드에
      fit 할 때 비율 그대로 가득 채워짐.
    - height 가 명시되면 (max_size, height) 캔버스 안에 비율 유지로 가운데 정렬
      (구버전 호환).
    """
    room = find_living_room(scene)
    if room is None:
        return None
    section = compute_section_line(room, inset_mm=2000.0, scene=scene)
    if section is None:
        return None
    comps = collect_section_components(scene, section, max_floor=2)
    if not comps:
        return None

    axis = section['axis']
    re = section.get('room_extent')

    # 거실 양옆 인접 부재까지 a 범위 제한 ─────────────────
    if re is not None:
        re_a0, re_a1 = float(re[0]), float(re[1])
        tol = 50.0
        # 좌측 인접 = a1 가 거실 a0 의 tol 이내 (또는 거실 a0 보다 작음)
        left_cands = [c for c in comps if c['a1'] <= re_a0 + tol]
        if left_cands:
            closest_left_a1 = max(c['a1'] for c in left_cands)
            # 그 a1 에 닿는 부재들 (한 모듈 안 여러 부재 모두 포함)
            left_group = [c for c in left_cands if abs(c['a1'] - closest_left_a1) < 200]
            a_min = min(c['a0'] for c in left_group)
        else:
            a_min = re_a0
        # 우측 인접
        right_cands = [c for c in comps if c['a0'] >= re_a1 - tol]
        if right_cands:
            closest_right_a0 = min(c['a0'] for c in right_cands)
            right_group = [c for c in right_cands if abs(c['a0'] - closest_right_a0) < 200]
            a_max = max(c['a1'] for c in right_group)
        else:
            a_max = re_a1
    else:
        a_min = min(c['a0'] for c in comps)
        a_max = max(c['a1'] for c in comps)

    # 그 a 범위와 겹치는 부재만 그림 ──────────────────────
    visible = [c for c in comps if not (c['a1'] < a_min - 1 or c['a0'] > a_max + 1)]
    if not visible:
        visible = comps

    z_min = min(c['z0'] for c in visible)
    z_max = max(c['z1'] for c in visible)
    if a_max - a_min < 1.0:
        a_max = a_min + 1000.0
    if z_max - z_min < 1.0:
        z_max = z_min + 1000.0

    # 픽스맵 크기 — 단면 비율에 맞춤 ────────────────────────
    a_range = a_max - a_min
    z_range = z_max - z_min
    aspect = a_range / z_range
    pad = 30
    if height is None:
        # 단면 비율에 맞춘 픽스맵 — 카드에 비율 유지로 가득 차도록
        if aspect >= 1.0:
            cw = int(max_size)
            ch = int(max_size / aspect)
        else:
            ch = int(max_size)
            cw = int(max_size * aspect)
        cw = max(400, cw + 2 * pad)
        ch = max(280, ch + 2 * pad)
    else:
        cw = max(400, int(max_size))
        ch = max(280, int(height))

    pix = QPixmap(cw, ch)
    pix.fill(QColor("#FFFFFF"))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    avail_w = cw - 2 * pad
    avail_h = ch - 2 * pad
    sx = avail_w / a_range
    sy = avail_h / z_range
    s = min(sx, sy)
    ox = pad + (avail_w - a_range * s) / 2.0
    oy = pad + (avail_h - z_range * s) / 2.0

    comps = visible  # 이후 루프가 visible 만 사용하도록

    def tx(a: float) -> float:
        return ox + (a - a_min) * s

    def ty(z: float) -> float:
        return oy + (z_max - z) * s

    # ── 거실 음영 (먼저 깔고, 그 위에 골조를 그려 빨강이 부재를 침범 안 하게) ──
    # 단면선이 실제로 거실 폴리곤 내부를 지나는 구간만, 거실이 속한 층 높이에만.
    room_poly = section.get('room_poly')
    living_intervals: List[Tuple[float, float]] = []
    if room_poly:
        for (lo, hi) in _polygon_axis_intersections(
                room_poly, axis, float(section['value'])):
            c0 = max(lo, a_min); c1 = min(hi, a_max)
            if c1 > c0:
                living_intervals.append((c0, c1))
    rf = section.get('room_floor', 0)
    floor_comps = [c for c in comps if c.get('floor_index') == rf]
    if floor_comps:
        rz0 = min(c['z0'] for c in floor_comps)
        rz1 = max(c['z1'] for c in floor_comps)
    else:
        rz0, rz1 = z_min, z_max
    if living_intervals:
        p.setBrush(QBrush(QColor(230, 110, 110)))   # 불투명 — 골조를 위에 덮음
        p.setPen(Qt.NoPen)
        for (c0, c1) in living_intervals:
            p.drawRect(QRectF(tx(c0), ty(rz1), (c1 - c0) * s, (rz1 - rz0) * s))

    # ── 진짜 단면 [2026-06-04] — 평면에 잘린 부재의 *잘린 단면* 을 면으로 채움 ──
    # [CoT] 투영이 아니라, 절단 평면이 실제로 자른 부재(보/기둥/슬래브/벽)의 절단
    # 단면만 그린다. mesh∩평면 선분(slice)을 닫힌 루프로 이어 면으로 채운다.
    # 개구부(창)는 even-odd 로 구멍 처리. 색 구분 없이 중립 회색(사용자 요구).
    # 거실 음영 *위에* 그려야 빨강이 보/기둥/슬래브를 침범하지 않는다.
    _FILL = QColor(150, 158, 168)
    _EDGE = QColor(60, 66, 74)
    for c in comps:
        loops = _chain_segments_to_loops(c.get('segments') or [])
        if loops:
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for loop in loops:
                path.moveTo(tx(loop[0][0]), ty(loop[0][1]))
                for (pa, pz) in loop[1:]:
                    path.lineTo(tx(pa), ty(pz))
                path.closeSubpath()
            p.setPen(QPen(_EDGE, 1.0))
            p.setBrush(QBrush(_FILL))
            p.drawPath(path)

    # 거실 텍스트 — 가장 넓은 거실 구간 가운데에 (픽스맵 크기 비례 큰 폰트)
    if living_intervals:
        c0, c1 = max(living_intervals, key=lambda iv: iv[1] - iv[0])
        font_px = max(40, int(min(cw, ch) * 0.07))
        font = QFont(F_HEAD)
        font.setPixelSize(font_px)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QPen(QColor(180, 30, 30), 1))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance("거실")
        th = fm.ascent()
        cx_room = (tx(c0) + tx(c1)) / 2.0
        cy_room = (ty(rz1) + ty(rz0)) / 2.0
        p.drawText(QPointF(cx_room - tw / 2.0, cy_room + th / 2.0), "거실")

    p.end()
    return pix


def show_section_dialog(scene: Any, parent: Optional[QWidget] = None,
                         capture_pixmap=None) -> None:
    """(구) 배치설계 탭 '단면 정보' 버튼 진입점 — 현재 버튼은 제거됨(미사용).
    비교 탭은 render_section_pixmap 을 직접 사용한다.

    capture_pixmap: 주어지면 2D 그리기 대신 그 이미지를 표시(현재 호출처 없음).
    """
    dlg = SectionViewerDialog(scene, parent, capture_pixmap=capture_pixmap)
    dlg.showMaximized()
    dlg.exec_()


__all__ = [
    "SectionViewerDialog",
    "show_section_dialog",
    "find_living_room",
    "compute_section_line",
    "collect_section_components",
]
