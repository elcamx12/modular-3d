"""비교 탭 — case scene 으로 2D 평면도 / 실배치 QPixmap 렌더.

[2026-06-07] 비교 탭의 케이스 박스 뷰 3종 중 두 개를 scene 에서 직접 그린다.
거실 단면(section_viewer.render_section_pixmap)과 같은 패턴 — scene 의 1층
(floor_index==0) 부재·실을 월드 xy 평면으로 투영해 픽스맵으로 만든다.

- render_plan_pixmap   : 2D 평면도 — 부재를 5종(가로/세로모듈·바닥/벽/종속패널)
                          + 코어로 색 구분. 글자/치수 없음.
- render_rooms_pixmap  : 실배치 — 실 용도색 + 개구부 + 내벽 + 내부기둥.

[정책]
- 평면설계 2D 뷰와 동일하게 1층(floor_index==0)만 그린다(다층은 같은 xy 라 겹침).
- 가로모듈=MODULE, 세로모듈=VERTICAL_MODULE (사용자 확정: 세로모듈=수직모듈).
- 종속패널=종속부재(내벽·캔틸레버·중간보/기둥 등 본체가 아닌 부재).
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF,
)

from modular_3d.model import ComponentType as _CT
from modular_3d.ui.alignment.alignment_helpers import (
    LAYER_BOTTOM, xy_bbox, iter_component_rects,
)


# ── 색 팔레트 (연한 톤 — 흰 배경에서 부드럽게) ──────────────
# RGBA. 코어는 사용자 요청대로 투명한 회색.
_CAT_COLORS = {
    'h_module':   (130, 170, 215, 210),   # 가로모듈 — 차분한 파랑
    'v_module':   (120, 195, 188, 210),   # 세로모듈(수직) — 청록
    'floor':      (165, 210, 150, 210),   # 바닥패널 — 연두(밝게)
    'wall':       (55, 125, 60, 225),     # 벽패널 — 더 진한 초록(바닥패널 같은 계열)
    'cantilever': (230, 185, 120, 215),   # 캔틸레버(슬래브·보) — 호박색
    'struct':     (105, 118, 140, 215),   # 구조(중간 보·기둥) — 스틸 슬레이트
    'core':       (150, 150, 150, 80),    # 코어 — 투명 회색
}

# 구조(골조) 보·기둥 색 — 보는 옅은 슬레이트, 기둥은 빨간 팔레트색으로 강조.
_STRUCT_BEAM_RGB = (142, 154, 174)   # 보 — 옅은 슬레이트
_STRUCT_COL_RGB = (205, 85, 85)      # 기둥 — 팔레트 빨강
_STRUCT_COL_EDGE = (160, 55, 55)     # 기둥 외곽선 — 진한 빨강

# 역할별 알파 — 평면설계 2D 방식. 슬래브(내부)는 옅게, 기둥·보·벽은 진하게
# 위에 올려 모듈 내부 골조(기둥·보)가 보이도록. (흰 배경 기준으로 상향)
_NORMAL_ALPHAS = {'slab': 115, 'wall': 205, 'beam': 220, 'column': 255}
# 코어 — 사용자 요청대로 투명한 회색. 전 역할을 옅게.
_CORE_ALPHAS = {'slab': 42, 'wall': 95, 'beam': 95, 'column': 110}

# 실배치 — 특정 실 용도 색 오버라이드(전역 카탈로그는 안 건드림, 이 뷰 전용).
_ROOM_COLOR_OVERRIDE = {
    'bathroom': (170, 200, 235),   # 욕실/화장실 — 파스텔 블루
}

# 실 라벨 표시명 오버라이드(긴 이름 줄임) — 라벨에만 적용(범례 X).
_ROOM_LABEL_NAME = {
    'bathroom': '화장실',          # '욕실/화장실' → '화장실'
}

# 실 라벨 글자 크기 — 큰 실/작은 실 2단계 고정(픽스맵 픽셀). 큰 실이 더 크게.
_BIG_ROOM_TYPES = {'living', 'bedroom', 'kitchen', 'dining', 'common'}
_LABEL_PX_BIG = 45     # 거실·침실·주방 등 (예시 15P)
_LABEL_PX_SMALL = 30   # 화장실·드레스룸·현관·발코니 등 (예시 10P)

# 카테고리 한글 라벨 (범례용).
CAT_LABELS = {
    'h_module':   '가로모듈',
    'v_module':   '세로모듈',
    'floor':      '바닥패널',
    'wall':       '벽패널',
    'cantilever': '캔틸레버',
    'struct':     '구조(보·기둥)',
    'core':       '코어',
}

# 2D 평면도 그리는 순서 — 큰 본체 먼저, 캔틸레버·구조·벽 나중(위에 보이게).
_DRAW_ORDER = ['core', 'h_module', 'v_module', 'floor', 'wall',
               'cantilever', 'struct']

_PAD = 28
_OUTLINE_GRAY = (205, 205, 205)   # 실배치 배경 부재 윤곽


# ── 부재 → 5종+코어 카테고리 ───────────────────────────────
def _category(comp) -> Optional[str]:
    """부재를 2D 평면도 색 카테고리로 분류. 대상 아니면 None."""
    ct = getattr(comp, 'comp_type', None)
    if ct == _CT.MODULE:
        return 'h_module'
    if ct == _CT.VERTICAL_MODULE:
        return 'v_module'
    if ct == _CT.FLOOR_PANEL:
        return 'floor'
    if ct == _CT.STRUCT_WALL:
        return 'wall'
    if ct in (_CT.CORE, _CT.CORE_SLAB):
        return 'core'
    # [정책 2026-06-07] 2D 평면도에서 내벽(칸막이)·개구부는 숨긴다 — 본체/구조
    #   부재만 깔끔히 색 구분. 내벽은 '실배치' 뷰에서만 표시.
    if ct == _CT.INTERIOR_WALL:
        return None
    # [정책 2026-06-07] 종속패널 분류 폐지. 캔틸레버 계열(슬래브·보)은 별도
    #   '캔틸레버' 카테고리(범례 노출), 중간보·중간기둥은 '구조(보·기둥)'로 분리.
    #   중간보가 패널처럼 색칠되던 문제 해소 + 캔틸레버를 범례에 명시.
    if ct in (_CT.CANTILEVER_SLAB, _CT.CANTILEVER_BEAM):
        return 'cantilever'
    return 'struct'


def _is_floor0(comp) -> bool:
    return int(getattr(comp, 'floor_index', 0) or 0) == 0


# ── 월드 bbox / 좌표 변환 ──────────────────────────────────
def _floor0_bbox(scene) -> Optional[Tuple[float, float, float, float]]:
    """1층 부재 전체의 월드 xy bbox. 부재 없으면 실 폴리곤으로 폴백."""
    xs0, ys0, xs1, ys1 = [], [], [], []
    for comp in scene.components.values():
        if not _is_floor0(comp):
            continue
        try:
            x0, y0, x1, y1 = xy_bbox(comp)
        except Exception:
            continue
        xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
    if not xs0:
        # 부재가 없으면 실 폴리곤 범위라도
        for room in getattr(scene, 'rooms', {}).values():
            if int(getattr(room, 'floor_index', 0) or 0) != 0:
                continue
            for (px, py) in room.polygon:
                xs0.append(px); ys0.append(py); xs1.append(px); ys1.append(py)
    if not xs0:
        return None
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _make_canvas(bbox, max_size: int) -> Tuple[QPixmap, Callable[[float, float], QPointF]]:
    """bbox 비율에 맞춘 흰 픽스맵 + 월드(x,y)→픽셀 변환 함수 생성.

    월드 +Y(위)를 화면 위로 보내기 위해 y 를 뒤집는다.
    """
    x0, y0, x1, y1 = bbox
    ww = max(x1 - x0, 1.0)
    wh = max(y1 - y0, 1.0)
    aspect = ww / wh
    if aspect >= 1.0:
        cw = int(max_size); ch = int(max_size / aspect)
    else:
        ch = int(max_size); cw = int(max_size * aspect)
    cw = max(300, cw) + 2 * _PAD
    ch = max(300, ch) + 2 * _PAD
    scale = min((cw - 2 * _PAD) / ww, (ch - 2 * _PAD) / wh)
    # 가운데 정렬 오프셋
    off_x = (cw - ww * scale) / 2.0
    off_y = (ch - wh * scale) / 2.0

    def to_px(wx: float, wy: float) -> QPointF:
        sx = off_x + (wx - x0) * scale
        sy = off_y + (y1 - wy) * scale   # y 뒤집기
        return QPointF(sx, sy)

    pix = QPixmap(cw, ch)
    pix.fill(Qt.white)
    return pix, to_px


def _fill_rect(p: QPainter, to_px, rect, color: QColor) -> None:
    x0, y0, x1, y1 = rect
    a = to_px(x0, y0)
    b = to_px(x1, y1)
    p.fillRect(QPolygonF([a, QPointF(b.x(), a.y()), b,
                          QPointF(a.x(), b.y())]).boundingRect(), color)


def _fill_poly(p: QPainter, to_px, pts, color: QColor,
               pen: Optional[QPen] = None) -> None:
    poly = QPolygonF([to_px(float(pt[0]), float(pt[1])) for pt in pts])
    p.setPen(pen if pen is not None else Qt.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(poly)


def _draw_rect_px(p: QPainter, to_px, rect, brush: QColor,
                  pen: Optional[QPen] = None) -> None:
    """월드 rect(x0,y0,x1,y1)를 화면 사각형으로 채움(+선택 펜)."""
    x0, y0, x1, y1 = rect
    a = to_px(x0, y1)   # 좌상 (월드 y_max → 화면 위)
    b = to_px(x1, y0)   # 우하
    p.setPen(pen if pen is not None else Qt.NoPen)
    p.setBrush(QBrush(brush))
    p.drawRect(int(a.x()), int(a.y()), int(b.x() - a.x()), int(b.y() - a.y()))


def _core_slab_polygon(comp):
    """코어 슬래브가 폴리곤(L/U자)이면 실제 외곽 점 목록(slab.corners) 반환, 아니면 None.

    [함정] dimensions['polygon'] 은 폴리곤 여부 플래그일 뿐, 실제 그릴 외곽은
    slab.corners 다(평면설계 alignment_view 와 동일).
    """
    if getattr(comp, 'comp_type', None) != _CT.CORE_SLAB:
        return None
    if not (hasattr(comp, 'dimensions') and comp.dimensions.get('polygon')):
        return None
    slab = getattr(comp, 'slab', None)
    if slab is None:
        return None
    try:
        return [(float(c[0]), float(c[1])) for c in slab.corners]
    except Exception:
        return None


def _draw_component_roles(p: QPainter, to_px, comp, rgb, alphas, edge: QColor,
                          struct_rgb=None, struct_edge: Optional[QColor] = None) -> None:
    """부재의 구성요소를 역할(slab→wall→beam→column) 순서·알파로 그린다.

    슬래브는 옅게 깔고 기둥·보·벽을 진하게 위에 올려 골조가 드러나게.
    struct_rgb 가 주어지면 보·기둥(골조)은 부재 색이 아니라 그 '구조' 색으로
    그린다 — 모듈/패널의 보·기둥까지 모두 '구조'로 통일. (코어는 None 으로 호출.)
    """
    r, g, b = rgb
    try:
        rects = list(iter_component_rects(comp, LAYER_BOTTOM))
    except Exception:
        rects = []
    if not rects:
        try:
            _draw_rect_px(p, to_px, xy_bbox(comp), QColor(r, g, b, alphas['slab']))
        except Exception:
            pass
        return
    for role in ('slab', 'wall', 'beam', 'column'):
        # 보·기둥은 구조 색(있으면)으로 — 보는 옅게, 기둥은 진하게. 슬래브·벽은 부재 색.
        if struct_rgb is not None and role == 'beam':
            cr, cg, cb = _STRUCT_BEAM_RGB
            pen = None
        elif struct_rgb is not None and role == 'column':
            cr, cg, cb = _STRUCT_COL_RGB
            pen = QPen(struct_edge, 1) if struct_edge else None
        else:
            cr, cg, cb = r, g, b
            pen = QPen(edge, 1) if role == 'column' else None
        color = QColor(cr, cg, cb, alphas[role])
        for rect, rl in rects:
            if rl == role:
                _draw_rect_px(p, to_px, rect, color, pen=pen)


# ── 2D 평면도 — 부재 색 구분 ───────────────────────────────
def render_plan_pixmap(scene: Any, max_size: int = 1600) -> Optional[QPixmap]:
    """1층 부재를 5종+코어 색으로 칠한 2D 평면도. 글자 없음. 실패 시 None."""
    try:
        bbox = _floor0_bbox(scene)
        if bbox is None:
            return None
        pix, to_px = _make_canvas(bbox, max_size)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)

        # 카테고리별 버킷 → _DRAW_ORDER 순서로 그려 종속/벽이 모듈 위에 오게.
        buckets = {k: [] for k in _CAT_COLORS}
        for comp in scene.components.values():
            if not _is_floor0(comp):
                continue
            cat = _category(comp)
            if cat in buckets:
                buckets[cat].append(comp)

        # [함정] 부재 1개를 그리는 순서는 슬래브(옅게)→벽→보→기둥(진하게) 고정.
        #   순서가 바뀌면 골조(기둥·보)가 슬래브에 덮여 안 보인다.
        #   코어 슬래브는 사각 bbox 가 아니라 실제 폴리곤(slab.corners)으로 그려
        #   엉뚱하게 튀어나오지 않게 한다.
        # 보·기둥(골조)은 전부 '구조' 색으로 통일 — 모듈/패널의 보·기둥 포함.
        sr, sg, sb, _sa = _CAT_COLORS['struct']
        struct_rgb = (sr, sg, sb)
        struct_edge = QColor(_STRUCT_COL_EDGE[0], _STRUCT_COL_EDGE[1],
                             _STRUCT_COL_EDGE[2], 220)
        for cat in _DRAW_ORDER:
            r, g, b, _a = _CAT_COLORS[cat]
            alphas = _CORE_ALPHAS if cat == 'core' else _NORMAL_ALPHAS
            edge = QColor(max(0, r - 45), max(0, g - 45), max(0, b - 45),
                          110 if cat == 'core' else 180)
            # 코어는 골조도 코어 색(투명 회색) 유지, 그 외는 구조 색으로.
            srgb = None if cat == 'core' else struct_rgb
            sedge = None if cat == 'core' else struct_edge
            for comp in buckets[cat]:
                cpoly = _core_slab_polygon(comp)
                if cpoly is not None:
                    _fill_poly(p, to_px, cpoly,
                               QColor(r, g, b, alphas['slab']),
                               pen=QPen(edge, 1.0))
                    continue
                _draw_component_roles(p, to_px, comp, (r, g, b), alphas, edge,
                                      struct_rgb=srgb, struct_edge=sedge)
        p.end()
        return pix
    except Exception:
        import traceback
        print("[compare_plan_render plan] 오류:\n" + traceback.format_exc(),
              flush=True)
        return None


def _room_rgb(key, rt) -> Tuple[int, int, int]:
    """실 용도색 — 오버라이드(예: 화장실 파스텔 블루) 우선, 없으면 카탈로그 색."""
    if key in _ROOM_COLOR_OVERRIDE:
        return _ROOM_COLOR_OVERRIDE[key]
    if rt is not None:
        return (rt.color[0], rt.color[1], rt.color[2])
    return (170, 170, 170)


# ── 실배치 — 실 용도색 + 골조(보·기둥) + 개구부 + 내벽 ──────
def render_rooms_pixmap(scene: Any, max_size: int = 1600) -> Optional[QPixmap]:
    """1층 실 구성(용도색) + 개구부 + 내벽 + 내부기둥. 실패 시 None."""
    try:
        from modular_3d.카탈로그.room_types import get_room_type
        from modular_3d.render.opening_mesh import opening_xy_polygons

        bbox = _floor0_bbox(scene)
        if bbox is None:
            return None
        pix, to_px = _make_canvas(bbox, max_size)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)

        # [그리는 순서] 코어(배경 회색) → 실(용도색) → 골조(보·기둥) → 개구부 → 내벽.
        #   골조를 실 위에 올려 기둥·보 격자가 또렷이 보이도록 한다.

        # 0) 코어 — 투명 회색 배경(계단실 영역이 빈 흰칸으로 남지 않도록).
        core_edge = QColor(120, 120, 120, 110)
        for comp in scene.components.values():
            if not _is_floor0(comp):
                continue
            if getattr(comp, 'comp_type', None) not in (_CT.CORE, _CT.CORE_SLAB):
                continue
            cpoly = _core_slab_polygon(comp)
            if cpoly is not None:
                _fill_poly(p, to_px, cpoly, QColor(150, 150, 150, 55),
                           pen=QPen(core_edge, 1.0))
            else:
                _draw_component_roles(p, to_px, comp, (150, 150, 150),
                                      _CORE_ALPHAS, core_edge)

        # 1) 실 — 용도색 채움(반투명, 파스텔). 화장실 등은 오버라이드 색.
        for room in getattr(scene, 'rooms', {}).values():
            if int(getattr(room, 'floor_index', 0) or 0) != 0:
                continue
            if len(room.polygon) < 3:
                continue
            rt = get_room_type(room.room_type)
            col = _room_rgb(room.room_type, rt)
            # 파스텔톤이되 옅은 용도색(현관 베이지 등)도 보이도록 알파 확보.
            fill = QColor(col[0], col[1], col[2], 150)
            edge = QPen(QColor(col[0], col[1], col[2], 195), 1.0)
            _fill_poly(p, to_px, room.polygon, fill, pen=edge)

        # 2) 골조 — 실배치 전용 파스텔톤(은은하게). 기둥=파스텔 코랄, 보=연한 슬레이트.
        beam_col = QColor(175, 186, 203, 175)
        col_col = QColor(222, 146, 146, 215)
        col_pen = QPen(QColor(198, 120, 120, 200), 1)
        # 코어는 위에서 따로 그렸고, 중간보·중간기둥은 실배치에서 제외(요청).
        _frame_skip = (_CT.CORE, _CT.CORE_SLAB, _CT.MID_BEAM, _CT.MID_COLUMN)
        for comp in scene.components.values():
            if not _is_floor0(comp):
                continue
            if getattr(comp, 'comp_type', None) in _frame_skip:
                continue
            try:
                rects = list(iter_component_rects(comp, LAYER_BOTTOM))
            except Exception:
                continue
            for rect, role in rects:
                if role == 'beam':
                    _draw_rect_px(p, to_px, rect, beam_col)
                elif role == 'column':
                    _draw_rect_px(p, to_px, rect, col_col, pen=col_pen)

        # 3) 개구부 — 흰 구멍 + 옅은 외곽선(슬래브·벽 모두).
        op_fill = QColor(255, 255, 255, 235)
        op_pen = QPen(QColor(120, 120, 120, 200), 1.0)
        for comp in scene.components.values():
            if not _is_floor0(comp):
                continue
            try:
                polys = opening_xy_polygons(comp)
            except Exception:
                continue
            for _idx, pts, _kind in polys:
                if pts:
                    _fill_poly(p, to_px, pts, op_fill, pen=op_pen)

        # 4) 내벽(칸막이) — 연회색 얇은 선으로 위에. (골조 아닌 비내력 벽)
        wall_col = QColor(150, 156, 168, 200)
        for comp in scene.components.values():
            if not _is_floor0(comp):
                continue
            if getattr(comp, 'comp_type', None) == _CT.INTERIOR_WALL:
                try:
                    _fill_rect(p, to_px, xy_bbox(comp), wall_col)
                except Exception:
                    pass

        # 5) 실 이름 — 검정 글씨로 실 가운데에(맨 위). 실 크기에 맞춰 글자 크기 조절.
        #    프레젠테이션 폰트(Freesentation) 적용 — 등록 보장 후 Medium 굵기.
        try:
            from modular_3d.ui.fonts import F_BODY, ensure_fonts_loaded
            ensure_fonts_loaded()
            label_font = QFont(F_BODY)
            label_font.setWeight(QFont.Medium)
        except Exception:
            label_font = QFont()
        p.setPen(QColor(25, 25, 25))
        for room in getattr(scene, 'rooms', {}).values():
            if int(getattr(room, 'floor_index', 0) or 0) != 0:
                continue
            poly = getattr(room, 'polygon', []) or []
            if len(poly) < 3:
                continue
            rt = get_room_type(room.room_type)
            # 표시명: 오버라이드(화장실) 우선, 없으면 용도명.
            name = _ROOM_LABEL_NAME.get(
                room.room_type, rt.name if rt is not None else str(room.room_type))
            # 큰 실/작은 실 2단계 고정 크기로 통일(자동축소 없음).
            fsize = (_LABEL_PX_BIG if room.room_type in _BIG_ROOM_TYPES
                     else _LABEL_PX_SMALL)
            label_font.setPixelSize(fsize)
            p.setFont(label_font)
            cx, cy = room.centroid()
            c = to_px(float(cx), float(cy))
            # 무게중심에 가운데 정렬 — 넉넉한 rect 로 폭 무관하게 중앙 배치.
            trect = QRectF(c.x() - 1500.0, c.y() - fsize, 3000.0, fsize * 2.0)
            p.drawText(trect, Qt.AlignCenter, name)
        p.end()
        return pix
    except Exception:
        import traceback
        print("[compare_plan_render rooms] 오류:\n" + traceback.format_exc(),
              flush=True)
        return None


# ── 범례 항목 — (라벨, (r,g,b)) 목록 ───────────────────────
def plan_legend_items(scene: Any) -> List[Tuple[str, Tuple[int, int, int]]]:
    """2D 평면도 범례 — scene 에 실제 존재하는 부재 카테고리만 _DRAW_ORDER 순서로."""
    present = set()
    try:
        for comp in scene.components.values():
            if not _is_floor0(comp):
                continue
            cat = _category(comp)
            if cat is not None:
                present.add(cat)
    except Exception:
        return []
    # 면(슬래브·벽) 카테고리 먼저 — struct 는 보·기둥으로 따로 분리해 표기.
    items = []
    for cat in _DRAW_ORDER:
        if cat == 'struct':
            continue
        if cat in present:
            r, g, b, _a = _CAT_COLORS[cat]
            items.append((CAT_LABELS[cat], (r, g, b)))
    # 모듈/패널/벽/캔틸레버는 모두 보·기둥(골조)을 품으므로, 하나라도 있으면
    # 보(옅은 슬레이트)·기둥(빨강)을 각각 범례에 노출 — 실제 색과 일치.
    if present & {'h_module', 'v_module', 'floor', 'wall', 'cantilever', 'struct'}:
        items.append(('보', _STRUCT_BEAM_RGB))
        items.append(('기둥', _STRUCT_COL_RGB))
    return items


def rooms_legend_items(scene: Any) -> List[Tuple[str, Tuple[int, int, int]]]:
    """실배치 범례 — scene 1층에 존재하는 실 용도만(중복 제거, 등장 순서)."""
    try:
        from modular_3d.카탈로그.room_types import get_room_type
    except Exception:
        return []
    seen = []
    seen_keys = set()
    for room in getattr(scene, 'rooms', {}).values():
        if int(getattr(room, 'floor_index', 0) or 0) != 0:
            continue
        if len(getattr(room, 'polygon', []) or []) < 3:
            continue
        key = room.room_type
        if key in seen_keys:
            continue
        seen_keys.add(key)
        rt = get_room_type(key)
        label = rt.name if rt is not None else str(key)
        col = _room_rgb(key, rt)   # 오버라이드(화장실 파스텔 블루 등) 반영
        seen.append((label, col))
    # 코어(계단실)가 1층에 있으면 범례에 회색으로 추가.
    for comp in getattr(scene, 'components', {}).values():
        if (_is_floor0(comp)
                and getattr(comp, 'comp_type', None) in (_CT.CORE, _CT.CORE_SLAB)):
            seen.append(('코어', (150, 150, 150)))
            break
    return seen
