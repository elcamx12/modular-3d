"""AlignmentCanvas 의 paintEvent + 보조 그리기 Mixin.

[설계]
- AlignmentCanvas 는 본 Mixin 을 inherit. self._world_to_screen / self._layer 등 상태 사용.
- 본 모듈은 paintEvent 와 직접 그리기 헬퍼만 담당.
"""
from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPolygonF

from modular_3d.model import (
    Module, FloorPanel, StructWall, CantileverBeam, CantileverSlab,
    MidBeam, MidColumn, ComponentType, TYPE_NAMES,
)
from modular_3d.ui.alignment.alignment_helpers import (
    LAYER_BOTTOM, LAYER_TOP,
    TYPE_COLORS,
    STATE_IDLE, STATE_SELECTED, STATE_DIRECTION, STATE_REFERENCE,
    STATE_DEPENDENCY_PICK,
    ALIGN_TOL, GAP_20, SNAP_20, SNAP_220, EPS, EDGE_PICK_PX,
    xy_bbox as _xy_bbox,
    allowed_parent_types as _allowed_parent_types,
    iter_component_rects as _iter_component_rects,
    visible_ids as _visible_ids,
)


class AlignmentCanvasPaintMixin:
    """paintEvent + 직접 그리기 헬퍼."""

    def _draw_vline(self, p, world_x, color, width):
        sx, _ = self._world_to_screen(world_x, 0)
        p.setPen(QPen(color, width))
        p.drawLine(int(sx), 0, int(sx), self.height())

    def _draw_hline(self, p, world_y, color, width):
        _, sy = self._world_to_screen(0, world_y)
        p.setPen(QPen(color, width))
        p.drawLine(0, int(sy), self.width(), int(sy))

    def _draw_bbox_edges(self, p, comp, color, width):
        x0, y0, x1, y1 = _xy_bbox(comp)
        sx0, sy0 = self._world_to_screen(x0, y0)
        sx1, sy1 = self._world_to_screen(x1, y1)
        p.setPen(QPen(color, width))
        p.drawLine(int(sx0), int(sy0), int(sx0), int(sy1))
        p.drawLine(int(sx1), int(sy0), int(sx1), int(sy1))
        p.drawLine(int(sx0), int(sy0), int(sx1), int(sy0))
        p.drawLine(int(sx0), int(sy1), int(sx1), int(sy1))

    # ── paintEvent ─────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        p.setFont(QFont("Malgun Gothic", 9))
        fm = p.fontMetrics()

        scene = self._controller._scene.components
        vis_ids = self._current_visible_ids()
        mis = self._misaligned_set(self._selected_id) if self._selected_id > 0 else {}

        # 1) 내부 구성 요소를 역할별로 그림 (뒤→앞: slab → wall → beam → column)
        role_alphas = {'slab': 55, 'wall': 140, 'beam': 185, 'column': 235}

        # 단계 4: DEPENDENCY_PICK 상태에서 후보 부재 강조
        dep_candidate_ids = set()
        if (self._state == STATE_DEPENDENCY_PICK
                and self._f5_pending_dep_type is not None):
            allowed = _allowed_parent_types(self._f5_pending_dep_type)
            # floor_index=0 본체만 (UI는 1층만 그리므로)
            for cid in vis_ids:
                comp = scene.get(cid)
                if comp is None:
                    continue
                if (comp.comp_type in allowed
                        and getattr(comp, 'floor_index', 0) == 0
                        and getattr(comp, 'sub_index', 0) == 0):
                    dep_candidate_ids.add(cid)

        # 단계 5: SELECTED 시 같은 그룹 부재 옅게 강조 (다층 일체화 가시화)
        same_group_ids = set()
        if self._selected_id > 0:
            sel = scene.get(self._selected_id)
            sel_gid = getattr(sel, 'group_id', 0) if sel is not None else 0
            if sel_gid > 0:
                for cid in vis_ids:
                    if cid == self._selected_id:
                        continue
                    c = scene.get(cid)
                    if c is None:
                        continue
                    if getattr(c, 'group_id', 0) == sel_gid:
                        same_group_ids.add(cid)

        # 합체 파트너(벽 ↔ FP) 도 선택된 것처럼 표시 — 사용자가 한쪽을 클릭해도
        # 합체된 상대편이 같이 강조되도록.
        merged_partner_ids = set()
        if self._selected_id > 0:
            sel = scene.get(self._selected_id)
            if sel is not None:
                if isinstance(sel, StructWall) and sel.merged_fp_id is not None:
                    merged_partner_ids.add(sel.merged_fp_id)
                elif isinstance(sel, FloorPanel) and sel.merged_wall_ids:
                    merged_partner_ids.update(sel.merged_wall_ids)

        def _pick_base_color(cid, comp):
            """상태에 따라 기본 색(R,G,B) 반환."""
            if cid == self._selected_id or cid in merged_partner_ids:
                return (80, 160, 255)
            if cid in mis:
                return (220, 60, 60)
            if cid in dep_candidate_ids:
                # 종속 후보: 밝은 노란-녹 강조 (CC3 ㄱ)
                return (255, 240, 100)
            if cid in same_group_ids:
                # 같은 그룹: 옅은 청록 (단계 5)
                return (120, 180, 220)
            return TYPE_COLORS.get(comp.comp_type, (150, 150, 150))

        for role in ('slab', 'wall', 'beam', 'column'):
            alpha = role_alphas[role]
            for cid in vis_ids:
                comp = scene[cid]
                base = _pick_base_color(cid, comp)
                fill = QColor(base[0], base[1], base[2], alpha)
                # 기둥은 살짝 진한 외곽선, 나머지는 펜 없음
                if role == 'column':
                    edge = QColor(
                        max(0, base[0] - 40),
                        max(0, base[1] - 40),
                        max(0, base[2] - 40), 255)
                    p.setPen(QPen(edge, 1))
                else:
                    p.setPen(Qt.NoPen)
                p.setBrush(QBrush(fill))
                for rect, rl in _iter_component_rects(comp, self._layer):
                    if rl != role:
                        continue
                    x0, y0, x1, y1 = rect
                    sx0, sy0 = self._world_to_screen(x0, y0)
                    sx1, sy1 = self._world_to_screen(x1, y1)
                    rx = min(sx0, sx1)
                    ry = min(sy0, sy1)
                    rw = abs(sx1 - sx0)
                    rh = abs(sy1 - sy0)
                    p.drawRect(int(rx), int(ry), int(rw), int(rh))

        # 1-b) 부재 외곽 bbox 윤곽선 + 라벨 + 오차 텍스트
        for cid in vis_ids:
            comp = scene[cid]
            x0, y0, x1, y1 = _xy_bbox(comp)
            sx0, sy0 = self._world_to_screen(x0, y0)
            sx1, sy1 = self._world_to_screen(x1, y1)
            rx = min(sx0, sx1)
            ry = min(sy0, sy1)
            rw = abs(sx1 - sx0)
            rh = abs(sy1 - sy0)

            r, g, b = TYPE_COLORS.get(comp.comp_type, (150, 150, 150))
            if cid == self._selected_id or cid in merged_partner_ids:
                edge = QColor(120, 200, 255, 255)
                ew = 2
            elif cid in mis:
                edge = QColor(255, 100, 100, 255)
                ew = 2
            elif (cid == self._hover_id
                  and self._state == STATE_REFERENCE
                  and cid != self._selected_id):
                edge = QColor(255, 220, 100, 255)
                ew = 2
            else:
                edge = QColor(r, g, b, 180)
                ew = 1

            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(edge, ew))
            p.drawRect(int(rx), int(ry), int(rw), int(rh))

            tname = TYPE_NAMES.get(comp.comp_type, '부재')
            p.setPen(QColor(240, 240, 240))
            p.drawText(int(rx + 4), int(ry + 13), f'{tname}#{cid}')

            if cid in mis:
                dx, dy = mis[cid]
                parts = []
                if dx is not None:
                    parts.append(f'Δx={dx:.0f}')
                if dy is not None:
                    parts.append(f'Δy={dy:.0f}')
                p.setPen(QColor(255, 230, 230))
                p.drawText(int(rx + 4), int(ry + 27), ' / '.join(parts))

        # 2) 선택 부재의 4 모서리 파랑 강조 (합체 파트너도 동일 강조)
        if self._selected_id > 0 and self._selected_id in scene:
            self._draw_bbox_edges(
                p, scene[self._selected_id], QColor(120, 200, 255), 2)
        for pid in merged_partner_ids:
            if pid in scene:
                self._draw_bbox_edges(
                    p, scene[pid], QColor(120, 200, 255), 2)

        # 3) 방향 모드: 해당 축 모서리 2개 노란색
        if self._state in (STATE_DIRECTION, STATE_REFERENCE) and self._selected_id > 0:
            comp = scene.get(self._selected_id)
            if comp is not None:
                x0, y0, x1, y1 = _xy_bbox(comp)
                yellow = QColor(255, 220, 80)
                if self._direction == 'X':
                    self._draw_vline(p, x0, yellow, 3)
                    self._draw_vline(p, x1, yellow, 3)
                elif self._direction == 'Y':
                    self._draw_hline(p, y0, yellow, 3)
                    self._draw_hline(p, y1, yellow, 3)

        # 4) 기준 모서리 녹색 굵게
        if self._state == STATE_REFERENCE and self._reference_edge is not None:
            axis, coord = self._reference_edge
            green = QColor(120, 255, 120)
            if axis == 0:
                self._draw_vline(p, coord, green, 4)
            else:
                self._draw_hline(p, coord, green, 4)

        # 5) 대상 호버 → 마우스 좌표에 가까운 동방향 모서리 강조 + 스냅 안내
        if (self._state == STATE_REFERENCE and self._hover_id > 0
                and self._hover_id != self._selected_id
                and self._hover_id in scene):
            target_comp = scene[self._hover_id]
            axis, ref_coord = self._reference_edge
            tgt = self._pick_target_edge(target_comp, axis)
            color = QColor(255, 180, 60)
            if axis == 0:
                self._draw_vline(p, tgt, color, 3)
            else:
                self._draw_hline(p, tgt, color, 3)

            delta_exact = tgt - ref_coord
            hint = (f'[1: -220({delta_exact - SNAP_220:+.0f})  '
                    f'2: 정확({delta_exact:+.0f})  '
                    f'3: +220({delta_exact + SNAP_220:+.0f})  '
                    f'4: -20({delta_exact - SNAP_20:+.0f})  '
                    f'5: +20({delta_exact + SNAP_20:+.0f})]')
            p.setPen(QColor(255, 240, 180))
            p.drawText(10, self.height() - 36, hint)

        # 5b) 실(Room) 색면 + 라벨 (부재 위에 반투명)
        self._draw_rooms(p)

        # 5c) 개구부(Opening) 외곽 표시 + 미리보기 고스트
        self._draw_openings(p)
        self._draw_opening_preview(p)

        # 5d) 실 이동/복사 미리보기 고스트
        self._draw_room_preview(p)

        # 5e) 정의 가져오기 그룹 미리보기 고스트
        self._draw_import_preview(p)

        # 6) 상단 상태 표시
        layer_txt = '하부 레벨' if self._layer == LAYER_BOTTOM else '상부 레벨'
        state_txt = {
            STATE_IDLE: '부재 클릭 / 1~7 배치',
            STATE_SELECTED: 'X·Y 키로 이동 방향 선택 / Del 삭제',
            STATE_DIRECTION: '노란 모서리 클릭 → 기준 확정',
            STATE_REFERENCE: '대상 부재에 호버 후 1·2·3·4·5 스냅 이동',
            STATE_DEPENDENCY_PICK: '노란 강조 부재 클릭 → 부모 확정',
        }.get(self._state, '')
        if self._f5_in_preview:
            state_txt = '캔버스 클릭으로 위치 확정 (R: 회전, V: 앵커)'
        if getattr(self, '_edit_mode', 'component') == 'room':
            if getattr(self, '_room_draw_active', False):
                state_txt = ('실 그리기: 좌클릭=점, Enter=완료, '
                             '우클릭/Back=취소점, Esc=취소')
            elif getattr(self, '_room_preview', None) is not None:
                state_txt = ('실 배치: 클릭=확정 · V=기준꼭짓점 · R=90°회전 · Esc=취소')
            else:
                state_txt = ('실: 클릭=선택 · M=이동 · C=복사 · R=회전 · Del=삭제 · '
                             'Z=되돌리기 / "실 그리기"로 새로')
        if getattr(self, '_edit_mode', 'component') == 'opening':
            if getattr(self, '_op_preview', None) is not None:
                state_txt = ('개구부 배치: 대상 면 위 클릭 확정 · '
                             'R=가로세로 · V=기준점 · Esc=취소')
            else:
                state_txt = ('개구부: 클릭=선택 · Del=삭제 · M=이동 · C=복사 / '
                             '"개구부 추가" 버튼으로 새로')
        if getattr(self, '_import_preview', None) is not None:
            state_txt = '정의 가져오기: 클릭=배치 · R=회전 · V=기준점 · Esc=취소'
        p.setPen(QColor(220, 220, 255))
        p.drawText(10, 16, f'[{layer_txt}]  {state_txt}')

        # 7) F5 PREVIEW: 2D 반투명 고스트 + 스냅 마커
        if self._f5_in_preview:
            self._draw_f5_ghost(p)

        # 7b) 실 그리기 진행 중 폴리곤(고무줄선)
        if getattr(self, '_room_draw_active', False):
            self._draw_room_in_progress(p)

        p.end()

        # UI 마이그레이션 M8-a — three.js sync 트리거를 update() 오버라이드로
        # 이전. paintEvent 는 vispy 표시용으로만 남고, three.js 갱신은 update()
        # 에서 수행 (vispy 2D 표시 제거 후에도 동작하도록).

    # ── F5 2D 고스트/스냅 마커 그리기 ─────────────────

    def _draw_f5_ghost(self, p):
        """F5 PREVIEW 시 마우스 위치에 반투명 고스트 부재 + 스냅 마커 그림."""
        corners = self._f5_compute_ghost_corners()
        if corners is None:
            return
        # 4꼭지점 → 화면 좌표 폴리곤
        screen_pts = []
        for cx, cy in corners:
            sx, sy = self._world_to_screen(cx, cy)
            screen_pts.append(QPointF(sx, sy))
        if not screen_pts:
            return
        poly = QPolygonF(screen_pts)
        p.setBrush(QBrush(QColor(80, 200, 255, 90)))
        p.setPen(QPen(QColor(80, 200, 255, 220), 2))
        p.drawPolygon(poly)

        # 앵커 위치 표시 — 마우스 커서가 곧 앵커 위치. R/V 키로 회전·앵커가
        # 바뀌어도 노란점은 항상 마우스에 따라가도록 mouse_world 의 화면 좌표
        # 그대로 사용 (코너 0 좌표로 그리면 회전 시 노란점이 코너 따라 이동함).
        mx, my = self._world_to_screen(*self._f5_mouse_world)
        p.setBrush(QBrush(QColor(255, 200, 80, 230)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(mx, my), 5, 5)

        # 스냅 마커 (월드 꼭지점 후보에 스냅된 경우)
        if self._f5_snap_point is not None:
            sx, sy = self._world_to_screen(*self._f5_snap_point)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 220, 80, 240), 2))
            p.drawEllipse(QPointF(sx, sy), 8, 8)
            # 십자
            p.drawLine(int(sx - 10), int(sy), int(sx + 10), int(sy))
            p.drawLine(int(sx), int(sy - 10), int(sx), int(sy + 10))

    # ── 실(Room) 그리기 (2026-05-24 2단계) ─────────────
    def _draw_rooms(self, p):
        """완성된 실들을 반투명 색면 + 외곽선 + 라벨로 그림."""
        from modular_3d.카탈로그.room_types import ROOM_TYPE_BY_KEY
        scene = self._controller._scene
        rooms = getattr(scene, 'rooms', {})
        if not rooms:
            return
        sel_rid = getattr(self, '_selected_room_id', -1)
        for rid, room in rooms.items():
            poly = getattr(room, 'polygon', None)
            if not poly or len(poly) < 3:
                continue
            rt = ROOM_TYPE_BY_KEY.get(getattr(room, 'room_type', ''))
            col = rt.color if rt is not None else (160, 160, 160)
            screen_pts = [QPointF(*self._world_to_screen(x, y)) for (x, y) in poly]
            qpoly = QPolygonF(screen_pts)
            is_sel = (rid == sel_rid)
            # 선택 실은 채움 진하게 + 굵은 흰 테두리로 강조
            p.setBrush(QBrush(QColor(col[0], col[1], col[2], 110 if is_sel else 70)))
            if is_sel:
                p.setPen(QPen(QColor(255, 255, 255, 240), 3))
            else:
                p.setPen(QPen(QColor(col[0], col[1], col[2], 220), 2))
            p.drawPolygon(qpoly)
            # 라벨(용도명) — 무게중심
            cx, cy = room.centroid()
            lsx, lsy = self._world_to_screen(cx, cy)
            label = rt.name if rt is not None else '실'
            p.setPen(QColor(20, 20, 20))
            p.drawText(int(lsx) + 1, int(lsy) + 1, label)
            p.setPen(QColor(255, 255, 255))
            p.drawText(int(lsx), int(lsy), label)

    def _draw_openings(self, p):
        """부재 개구부를 평면(xy) 사각형 외곽으로 표시. 선택분은 강조."""
        from modular_3d.render.opening_mesh import opening_xy_polygons
        scene = self._controller._scene.components
        sel = getattr(self, '_selected_opening', None)
        for cid, comp in scene.items():
            if getattr(comp, 'floor_index', 0) != 0:
                continue
            polys = opening_xy_polygons(comp)
            for (idx, pts, kind) in polys:
                screen_pts = [QPointF(*self._world_to_screen(x, y)) for (x, y) in pts]
                qpoly = QPolygonF(screen_pts)
                is_sel = (sel is not None and sel[0] == cid and sel[1] == idx)
                if is_sel:
                    p.setBrush(QBrush(QColor(255, 120, 60, 110)))
                    p.setPen(QPen(QColor(255, 255, 255, 240), 3))
                else:
                    # 개구부 = 뚫린 곳: 어두운 채움 + 주황 외곽
                    p.setBrush(QBrush(QColor(20, 20, 20, 120)))
                    p.setPen(QPen(QColor(255, 150, 60, 230), 2))
                p.drawPolygon(qpoly)

    def _draw_import_preview(self, p):
        """정의 가져오기 그룹 고스트(부재 footprint + 실 폴리곤, 점선)를 2D 표시."""
        pv = getattr(self, '_import_preview', None)
        if pv is None or pv.get('_wx') is None:
            return
        from modular_3d.model.definition_place import transform_point
        pivot = self._import_pivot()
        rot = pv['rot']
        target = (pv['_wx'], pv['_wy'])

        def _T(poly):
            return [QPointF(*self._world_to_screen(
                *transform_point(x, y, pivot, rot, target))) for (x, y) in poly]

        # 부재 footprint (청록 점선)
        p.setBrush(QBrush(QColor(80, 200, 255, 60)))
        p.setPen(QPen(QColor(80, 200, 255, 230), 2, Qt.DashLine))
        for fp in pv['footprints']:
            if len(fp) >= 3:
                p.drawPolygon(QPolygonF(_T(fp)))
        # 실 폴리곤 (녹색 점선)
        p.setBrush(QBrush(QColor(120, 220, 140, 60)))
        p.setPen(QPen(QColor(120, 220, 140, 230), 2, Qt.DashLine))
        for rp in pv['room_polys']:
            if len(rp) >= 3:
                p.drawPolygon(QPolygonF(_T(rp)))
        # 기준점(마우스) 마커
        mx, my = self._world_to_screen(pv['_wx'], pv['_wy'])
        p.setBrush(QBrush(QColor(255, 200, 80, 240)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(mx, my), 6, 6)

    def _draw_room_preview(self, p):
        """실 이동/복사 미리보기 폴리곤(녹색 점선) + 기준 꼭짓점 마커."""
        pv = getattr(self, '_room_preview', None)
        if pv is None:
            return
        poly = self._room_preview_polygon()
        if not poly or len(poly) < 2:
            return
        screen_pts = [QPointF(*self._world_to_screen(x, y)) for (x, y) in poly]
        p.setBrush(QBrush(QColor(120, 220, 140, 80)))
        p.setPen(QPen(QColor(120, 220, 140, 240), 2, Qt.DashLine))
        p.drawPolygon(QPolygonF(screen_pts))
        # 기준 꼭짓점(V) 강조
        ai = pv['anchor'] % len(poly)
        ax, ay = self._world_to_screen(*poly[ai])
        p.setBrush(QBrush(QColor(255, 200, 80, 240)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(ax, ay), 6, 6)

    def _draw_opening_preview(self, p):
        """개구부 고스트 미리보기를 2D 평면 사각형(주황 점선)으로 표시."""
        pv = getattr(self, '_op_preview', None)
        if pv is None or pv.get('target', -1) <= 0:
            return
        from modular_3d.render.opening_mesh import opening_xy_poly
        comp = self._controller._scene.components.get(pv['target'])
        if comp is None:
            return
        ew = pv['h'] if pv['rot'] == 90 else pv['w']
        eh = pv['w'] if pv['rot'] == 90 else pv['h']
        op = {'face': pv.get('face', 'slab'), 'u': pv['u'], 'v': pv['v'],
              'w': ew, 'h': eh}
        pts = opening_xy_poly(comp, op)
        if not pts:
            return
        screen_pts = [QPointF(*self._world_to_screen(x, y)) for (x, y) in pts]
        p.setBrush(QBrush(QColor(255, 150, 60, 90)))
        p.setPen(QPen(QColor(255, 150, 60, 240), 2, Qt.DashLine))
        p.drawPolygon(QPolygonF(screen_pts))

    def _draw_room_in_progress(self, p):
        """실 그리기 중 — 확정점·연결선·고무줄선·점 마커."""
        pts = list(getattr(self, '_room_points', []))
        cur = getattr(self, '_room_cursor_world', None)
        # 확정 점들 연결선
        screen_pts = [self._world_to_screen(x, y) for (x, y) in pts]
        p.setPen(QPen(QColor(120, 220, 140, 230), 2))
        for i in range(1, len(screen_pts)):
            x0, y0 = screen_pts[i - 1]
            x1, y1 = screen_pts[i]
            p.drawLine(int(x0), int(y0), int(x1), int(y1))
        # 고무줄선(마지막 점 → 현재 커서) + 닫힘 미리보기(커서 → 첫 점)
        if screen_pts and cur is not None:
            cxs, cys = self._world_to_screen(*cur)
            lx, ly = screen_pts[-1]
            p.setPen(QPen(QColor(120, 220, 140, 150), 1, Qt.DashLine))
            p.drawLine(int(lx), int(ly), int(cxs), int(cys))
            if len(screen_pts) >= 2:
                fx, fy = screen_pts[0]
                p.drawLine(int(cxs), int(cys), int(fx), int(fy))
        # 점 마커
        p.setPen(Qt.NoPen)
        for i, (sx, sy) in enumerate(screen_pts):
            # 첫 점은 강조(닫기 기준)
            if i == 0:
                p.setBrush(QBrush(QColor(255, 200, 80, 240)))
                p.drawEllipse(QPointF(sx, sy), 6, 6)
            else:
                p.setBrush(QBrush(QColor(120, 220, 140, 240)))
                p.drawEllipse(QPointF(sx, sy), 4, 4)
        # 꼭짓점 스냅 마커
        snap_v = getattr(self, '_room_snap_point', None)
        if snap_v is not None:
            sx, sy = self._world_to_screen(*snap_v)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 220, 80, 240), 2))
            p.drawEllipse(QPointF(sx, sy), 8, 8)
            p.drawLine(int(sx - 10), int(sy), int(sx + 10), int(sy))
            p.drawLine(int(sx), int(sy - 10), int(sx), int(sy + 10))

    # ── F5 스냅 + 2D 고스트 헬퍼 ──────────────────────

    def _f5_world_snap(self, wx, wy, threshold_mm=None):
        """마우스 월드 좌표에서 가장 가까운 1층 본체 꼭지점에 스냅.
        후보 없으면 100mm 그리드 스냅(c3 ㄱ).
        그 다음 자동 갭 보정(2026-05-11) — 새 부재 외곽이 기존 부재 외곽과 100mm
        이내일 때 20mm(또는 0mm) 갭으로 자동 정렬.

        반환: (snapped_wx, snapped_wy, snap_point_or_None)
        snap_point: (cx, cy) 또는 None (그리드 폴백 시)
        """
        if threshold_mm is None:
            from modular_3d.카탈로그.tolerances import HOVER_SNAP_RADIUS_MM
            threshold_mm = float(HOVER_SNAP_RADIUS_MM)
        scene = self._controller._scene.components
        best_d = float('inf')
        best_p = None
        # 자기 자신 그룹은 후보 제외(있다면)
        for cid, comp in scene.items():
            if (getattr(comp, 'floor_index', 0) != 0
                    or getattr(comp, 'sub_index', 0) != 0):
                continue
            x0, y0, x1, y1 = _xy_bbox(comp)
            for cx, cy in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
                d = ((wx - cx) ** 2 + (wy - cy) ** 2) ** 0.5
                if d < best_d and d <= threshold_mm:
                    best_d = d
                    best_p = (cx, cy)
        if best_p is not None:
            snapped_x, snapped_y, snap_p = float(best_p[0]), float(best_p[1]), best_p
        else:
            # 그리드 폴백
            snapped_x = round(wx / 100.0) * 100.0
            snapped_y = round(wy / 100.0) * 100.0
            snap_p = None

        # 자동 갭 보정 — 활성 부재 정보가 있을 때만 (PREVIEW / DEPENDENCY_PICK 시점)
        snapped_x, snapped_y = self._apply_auto_gap(snapped_x, snapped_y)
        # 종속 부재(캔틸/중간보·기둥) 강제 정렬 — _f5_dep_meta 가 있을 때만
        snapped_x, snapped_y = self._apply_dep_snap(snapped_x, snapped_y)
        return snapped_x, snapped_y, snap_p

    # 2026-05-11 자동 갭 보정 헬퍼 — Controller 의 활성 부재 정보를 읽어
    #   render/auto_snap.apply_auto_gap 으로 위임.
    def _apply_auto_gap(self, wx: float, wy: float):
        ctrl = self._controller
        # PREVIEW 상태에서는 _f5_pending_dims 가 채워져 있음
        ctype = getattr(ctrl, '_current_comp_type', None)
        dims = getattr(ctrl, '_f5_pending_dims', None)
        if ctype is None or dims is None:
            return wx, wy
        # 캔틸레버/중간보·기둥 등 종속 부재는 별도 흐름(_f5_on_dependency_pick) 에서
        # 처리하므로 일반 자동 갭에서 제외 (Phase 1 정책)
        from modular_3d.model import ComponentType as _CT
        if ctype in (_CT.CANTILEVER_BEAM, _CT.CANTILEVER_SLAB,
                     _CT.MID_BEAM, _CT.MID_COLUMN, _CT.INTERIOR_WALL):
            return wx, wy
        try:
            from modular_3d.render.auto_snap import apply_auto_gap
        except Exception:
            return wx, wy
        rotation = int(getattr(ctrl, '_preview_rotation', 0) or 0)
        anchor = int(getattr(ctrl, '_preview_anchor', 0) or 0)
        new_x, new_y, _did = apply_auto_gap(
            wx, wy, ctype, dims, rotation, anchor, ctrl._scene,
        )
        return new_x, new_y

    # 2026-05-11 Phase 2 — 종속 부재 강제 정렬 헬퍼.
    # Controller._f5_dep_meta 에 parent_id 가 있을 때만 동작 (DEPENDENCY_PICK 이후 PREVIEW).
    def _apply_dep_snap(self, wx: float, wy: float):
        ctrl = self._controller
        meta = getattr(ctrl, '_f5_dep_meta', None)
        if meta is None:
            return wx, wy
        parent_id = meta.get('parent_id')
        parent = ctrl._scene.components.get(parent_id) if parent_id else None
        if parent is None:
            return wx, wy
        ctype = getattr(ctrl, '_current_comp_type', None)
        dims = getattr(ctrl, '_f5_pending_dims', None)
        if ctype is None or dims is None:
            return wx, wy
        from modular_3d.model import ComponentType as _CT
        try:
            from modular_3d.render import dep_snap
        except Exception:
            return wx, wy
        rot = int(getattr(ctrl, '_preview_rotation', 0) or 0)
        anc = int(getattr(ctrl, '_preview_anchor', 0) or 0)
        # (정책 c 단계 2026-05-12) 사용자가 R/V 키로 한 번이라도 회전·앵커를
        # 변경한 후엔 dep_snap 의 자동 회전·앵커 변경을 차단. 위치만 갱신.
        user_lock = bool(getattr(ctrl, '_f5_user_rv_override', False))

        if ctype == _CT.CANTILEVER_BEAM:
            r = dep_snap.snap_cantilever_beam(wx, wy, parent, rot, dims)
            if r is None:
                # [v5] 장변에는 배치 금지 — 마우스 위치 그대로 두고 스냅 무산
                return wx, wy
            new_x, new_y, new_rot = r
            if not user_lock:
                ctrl._preview_rotation = int(new_rot)
                ctrl._preview_anchor = 0
            return new_x, new_y
        if ctype == _CT.CANTILEVER_SLAB:
            r = dep_snap.snap_cantilever_slab(wx, wy, parent, dims, anc)
            if r is None:
                return wx, wy
            new_x, new_y, new_dims, new_rot = r
            ctrl._f5_pending_dims = new_dims
            if not user_lock:
                ctrl._preview_rotation = int(new_rot)
                ctrl._preview_anchor = 0
            return new_x, new_y
        if ctype == _CT.MID_COLUMN:
            return dep_snap.snap_mid_column(wx, wy, parent, dims)
        if ctype == _CT.MID_BEAM:
            new_x, new_y, new_dims, new_rot = dep_snap.snap_mid_beam(
                wx, wy, parent, ctrl._scene, rot, dims,
            )
            ctrl._f5_pending_dims = new_dims
            if not user_lock:
                ctrl._preview_rotation = int(new_rot)
                ctrl._preview_anchor = 0
            return new_x, new_y
        return wx, wy

    def _f5_compute_ghost_corners(self):
        """현재 PREVIEW 부재의 1층 본체 4꼭지점(월드 XY) 반환. 없으면 None."""
        ctrl = self._controller
        dims = getattr(ctrl, '_f5_pending_dims', None)
        if dims is None:
            return None
        wx, wy = self._f5_mouse_world
        comp_type = ctrl._current_comp_type
        rot = ctrl._preview_rotation
        anc = ctrl._preview_anchor
        # 임시 Component 생성 → 바닥 4꼭지점
        from modular_3d.ui.controls import _create_component
        pos = np.array([wx, wy, 0.0])
        comp = _create_component(comp_type, pos, dims, rot, anc)
        corners = comp.get_world_corners()
        return [(float(c[0]), float(c[1])) for c in corners[:4]]

    # ── 마우스 이벤트 ─────────────────────────────────
