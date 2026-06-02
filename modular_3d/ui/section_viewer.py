"""거실 단면 정보 다이얼로그 — 배치설계 탭의 "단면 정보" 버튼이 호출.

[2026-06-01] 1단계 골격
- 배치설계 탭에서 사용자가 `room_type='living'` 으로 지정한 거실 Room 을 찾는다.
- 거실 폴리곤의 *가장 긴 변* 을 외벽으로 간주 (사용자 의도: "외부와 가장 많은
  면적이 닿아있는 부분"). 그 변과 *평행* 한 직선이 거실 무게중심을 지나가도록
  단면선을 잡는다. 가장 긴 변이 X 방향이면 단면도 X 방향(특정 y 에서 자름),
  Y 방향이면 단면도 Y 방향.
- 단면선을 교차하는 1층·2층 모듈/벽/패널을 모아 QPainter 로 2D 단면 이미지
  생성. 1·2층만 표시(사용자 요구).

[정책]
- 좌표 단위 mm (씬 정책과 동일).
- 내부 로직(Scene, Component, Room) 변경 없음 — 읽기만.
- 단면 검출 정확도는 향후 강화: 지금은 축정렬(AABB) 가정.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
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


def compute_section_line(room: Any, inset_mm: float = 2000.0,
                          scene: Any = None) -> Optional[Dict[str, Any]]:
    """거실 polygon 의 **외벽(외부와 접한 변) 중 가장 긴 변** 과 평행 + 그
    외벽에서 거실 안쪽으로 `inset_mm` (기본 2 m) 만큼 이동한 단면선.

    scene 이 주어지면 외벽 검출(_find_exterior_edges) 수행, 없으면 fallback
    으로 가장 긴 변(_longest_edge) 사용 (기존 동작).
    """
    poly = list(getattr(room, 'polygon', []) or [])
    if not poly:
        return None
    cx, cy = _polygon_centroid(poly)
    edge = None
    if scene is not None:
        ext_edges = _find_exterior_edges(poly, (cx, cy), scene)
        if ext_edges:
            # 외벽 후보 중 가장 긴 것
            edge = max(ext_edges, key=lambda e: e[2])
    if edge is None:
        edge = _longest_edge(poly)
    if edge is None:
        return None
    p0, p1, L = edge
    dx_e = p1[0] - p0[0]
    dy_e = p1[1] - p0[1]
    # 외벽 변의 중점
    mx = (p0[0] + p1[0]) / 2.0
    my = (p0[1] + p1[1]) / 2.0
    # 변에 수직인 단위벡터 — polygon 내부(centroid) 방향으로 향하도록 부호 결정
    perp_x, perp_y = -dy_e, dx_e
    plen = math.hypot(perp_x, perp_y)
    if plen < 1e-9:
        return None
    perp_x /= plen
    perp_y /= plen
    # 변 중점→centroid 벡터와 같은 방향(안쪽)이 되도록
    if perp_x * (cx - mx) + perp_y * (cy - my) < 0:
        perp_x = -perp_x
        perp_y = -perp_y
    # 외벽에서 안쪽으로 inset_mm 이동한 절단점
    cut_x = mx + perp_x * inset_mm
    cut_y = my + perp_y * inset_mm

    # 가장 긴 변이 거의 X 방향이면 단면 축도 X (절단은 y=cut_y), 아니면 Y
    if abs(dx_e) >= abs(dy_e):
        axis = 'x'
        value = float(cut_y)
        room_extent = (min(p[0] for p in poly), max(p[0] for p in poly))
    else:
        axis = 'y'
        value = float(cut_x)
        room_extent = (min(p[1] for p in poly), max(p[1] for p in poly))

    return {
        'axis': axis,
        'value': value,
        'edge_length_mm': float(L),
        'room_extent': (float(room_extent[0]), float(room_extent[1])),
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
            f"외벽에서 {section.get('inset_mm', 2000):.0f} mm 안쪽 절단 · "
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

        # ── 거실 범위 음영 — 부재 z 범위 안에만 깔기 (반투명 빨강) ──
        re = section.get('room_extent')
        if re is not None:
            ra0, ra1 = float(re[0]), float(re[1])
            ra0c = max(ra0, a_min)
            ra1c = min(ra1, a_max)
            if ra1c > ra0c:
                # 반투명 빨강
                p.setBrush(QBrush(QColor(220, 60, 60, 90)))
                p.setPen(Qt.NoPen)
                shade_y_top = ty(z_max)
                shade_y_bot = ty(z_min)
                p.drawRect(QRectF(tx(ra0c), shade_y_top,
                                   (ra1c - ra0c) * sx,
                                   shade_y_bot - shade_y_top))
                # "거실" 텍스트 — 음영 영역 가운데에
                p.setFont(QFont(F_HEAD, 18, QFont.Bold))
                p.setPen(QPen(QColor(180, 30, 30), 1))
                room_cx = (tx(ra0c) + tx(ra1c)) / 2.0
                room_cy = (shade_y_top + shade_y_bot) / 2.0
                # drawText 정렬 위해 텍스트 폭 측정
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance("거실")
                p.drawText(QPointF(room_cx - tw / 2.0, room_cy + 6), "거실")

        # ── 부재 그리기 (거실 범위 안 부재는 외곽선 강조) ──
        def _in_room(c) -> bool:
            if re is None:
                return False
            return not (c['a1'] < re[0] or c['a0'] > re[1])

        # [2026-06-02] 진짜 2D 단면 — mesh 의 모든 삼각형을 절단 평면과 교차
        # 시킨 선분들을 그림. mesh 교차 선분이 없거나 빈약하면 AABB 사각형
        # fallback.
        # [2026-06-02] 부재 종류별 색 — 6 분류 (사용자 요청: 기둥/보 별도).
        # (fill, stroke) 쌍 — 사각형 fill + mesh 선분 stroke 둘 다 사용.
        # 범례와 정확히 일치.
        def _colors(tname: str) -> Tuple[QColor, QColor]:
            t = tname.lower()
            # 우선순위: column → beam → slab/panel → wall → core → module
            if 'column' in t:
                return QColor("#E74C3C"), QColor("#7A1A12")  # 빨강 — 기둥
            if 'beam' in t:
                return QColor("#F39C12"), QColor("#7A4D0A")  # 주황 — 보
            if 'slab' in t or 'panel' in t or 'floor' in t:
                return QColor("#BDBDBD"), QColor("#5A5A5A")  # 회색 — 슬래브/패널
            if 'wall' in t:
                return QColor("#5B9BD5"), QColor("#1F4E79")  # 파랑 — 벽
            if 'core' in t:
                return QColor("#9C7BC7"), QColor("#4B2E83")  # 보라 — 코어
            if 'module' in t:
                return QColor("#FFE9A8"), QColor("#1A1A1A")  # 베이지+검정 — 모듈
            return QColor("#F5F9FF"), QColor("#2C2C2C")

        # [2026-06-02] 부재 그리기 — mesh 교차 선분을 *삼각형의 vertex color*
        # 로 그림. mesh_builder 가 sub-part(기둥=빨강 0xCC3333, 바닥보=파랑
        # 0x2266CC, 천장보=밝은파랑 0x5599DD, 슬래브=베이지 0xD2D2BE, 벽=청록회색
        # 0x7799AA, 강철=#808080)별로 다른 색을 줘서, mesh 색을 그대로 사용하면
        # Module 내부 sub-part 가 자동으로 색 구분됨. fill 제거.
        for c in comps:
            tname = c['type']
            fallback_fill, fallback_pen = _colors(tname)
            highlight = _in_room(c)
            base_w = 2.4 if highlight else 1.6

            segs = c.get('segments') or []
            x0 = tx(c['a0']); x1 = tx(c['a1'])
            y_top = ty(c['z1']); y_bot = ty(c['z0'])

            # mesh 색 다양성 확인 — 단일색 이하면 type 기반 fill 도 함께
            seg_color_set = set()
            for seg in segs:
                if len(seg) >= 3 and seg[2] is not None:
                    seg_color_set.add((round(seg[2][0], 1),
                                       round(seg[2][1], 1),
                                       round(seg[2][2], 1)))
            need_type_fill = len(seg_color_set) <= 1

            if need_type_fill:
                # type 기반 fill 사각형 — mesh 색이 단일/없을 때 보장
                fill_alpha = QColor(fallback_fill); fill_alpha.setAlpha(180)
                p.setBrush(QBrush(fill_alpha))
                p.setPen(Qt.NoPen)
                p.drawRect(QRectF(x0, y_top, x1 - x0, y_bot - y_top))

            if segs and len(segs) >= 2:
                p.setBrush(Qt.NoBrush)
                for seg in segs:
                    a = seg[0]; b = seg[1]
                    seg_rgb = seg[2] if len(seg) >= 3 else None
                    if seg_rgb is not None:
                        col = QColor.fromRgbF(
                            max(0.0, min(1.0, seg_rgb[0])),
                            max(0.0, min(1.0, seg_rgb[1])),
                            max(0.0, min(1.0, seg_rgb[2])),
                        )
                    else:
                        col = fallback_pen
                    p.setPen(QPen(col, base_w))
                    p.drawLine(QPointF(tx(a[0]), ty(a[1])),
                               QPointF(tx(b[0]), ty(b[1])))
            else:
                # mesh 교차 없음 → 사각형 외곽선만
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(fallback_pen, base_w))
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

    # 거실 음영 (반투명 빨강)
    re = section.get('room_extent')
    if re is not None:
        ra0, ra1 = float(re[0]), float(re[1])
        ra0c = max(ra0, a_min)
        ra1c = min(ra1, a_max)
        if ra1c > ra0c:
            p.setBrush(QBrush(QColor(220, 60, 60, 90)))
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(tx(ra0c), ty(z_max),
                               (ra1c - ra0c) * s, (z_max - z_min) * s))

    # 부재
    def _colors(tname: str):
        t = tname.lower()
        if 'column' in t: return QColor("#E74C3C"), QColor("#7A1A12")
        if 'beam' in t: return QColor("#F39C12"), QColor("#7A4D0A")
        if 'slab' in t or 'panel' in t or 'floor' in t: return QColor("#BDBDBD"), QColor("#5A5A5A")
        if 'wall' in t: return QColor("#5B9BD5"), QColor("#1F4E79")
        if 'core' in t: return QColor("#9C7BC7"), QColor("#4B2E83")
        if 'module' in t: return QColor("#FFE9A8"), QColor("#1A1A1A")
        return QColor("#F5F9FF"), QColor("#2C2C2C")

    for c in comps:
        fallback_fill, fallback_pen = _colors(c['type'])
        segs = c.get('segments') or []
        x0, x1 = tx(c['a0']), tx(c['a1'])
        y_top, y_bot = ty(c['z1']), ty(c['z0'])
        # mesh 색 다양성 확인
        seg_color_set = set()
        for seg in segs:
            if len(seg) >= 3 and seg[2] is not None:
                seg_color_set.add((round(seg[2][0], 1), round(seg[2][1], 1), round(seg[2][2], 1)))
        if len(seg_color_set) <= 1:
            fa = QColor(fallback_fill); fa.setAlpha(180)
            p.setBrush(QBrush(fa)); p.setPen(Qt.NoPen)
            p.drawRect(QRectF(x0, y_top, x1 - x0, y_bot - y_top))
        if segs and len(segs) >= 2:
            p.setBrush(Qt.NoBrush)
            for seg in segs:
                a = seg[0]; b = seg[1]
                seg_rgb = seg[2] if len(seg) >= 3 else None
                if seg_rgb is not None:
                    col = QColor.fromRgbF(
                        max(0.0, min(1.0, seg_rgb[0])),
                        max(0.0, min(1.0, seg_rgb[1])),
                        max(0.0, min(1.0, seg_rgb[2])),
                    )
                else:
                    col = fallback_pen
                p.setPen(QPen(col, 1.2))
                p.drawLine(QPointF(tx(a[0]), ty(a[1])),
                           QPointF(tx(b[0]), ty(b[1])))
        else:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(fallback_pen, 1.2))
            p.drawRect(QRectF(x0, y_top, x1 - x0, y_bot - y_top))

    # 거실 텍스트 — 픽스맵 크기에 비례한 큰 폰트 (카드에 축소 표시돼도 잘 보이게)
    if re is not None:
        ra0c = max(float(re[0]), a_min)
        ra1c = min(float(re[1]), a_max)
        if ra1c > ra0c:
            font_px = max(40, int(min(cw, ch) * 0.07))
            font = QFont(F_HEAD)
            font.setPixelSize(font_px)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QPen(QColor(180, 30, 30), 1))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance("거실")
            th = fm.ascent()
            cx_room = (tx(ra0c) + tx(ra1c)) / 2.0
            cy_room = (ty(z_max) + ty(z_min)) / 2.0
            p.drawText(QPointF(cx_room - tw / 2.0, cy_room + th / 2.0), "거실")

    p.end()
    return pix


def show_section_dialog(scene: Any, parent: Optional[QWidget] = None,
                         capture_pixmap=None) -> None:
    """배치설계 탭의 '단면 정보' 버튼이 호출하는 진입점.

    capture_pixmap: 3D 뷰의 정면 클리핑 캡처 (QPixmap). main_3d._on_section_info_clicked
    가 viewer_three.capture_section_front 로 얻은 이미지를 전달.
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
