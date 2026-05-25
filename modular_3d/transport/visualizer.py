"""운송 회차 시각화 (Plotly 기반).

운송프로그램 원본(`src/visualizer.py`, Song-Jung-Hun/-3-) 이식 + Phase 4 확장.

[원본과의 차이 — Phase 4 변경점]
- **A-1 일반화**: 원본은 lshape (한쪽 끝 벽 1개) 만 그렸음. 우리는 Panel.
  wall_segments 리스트의 각 항목(side 0=하/1=우/2=상/3=좌, 부분 채움 포함)을
  실제 위치에 별도 박스로 그린다. ㄷ자/3면/4면/부분벽 모두 자동 표현.
- **A-2 캔틸 합체**: embedded 모드(기본)는 어댑터가 부모 모듈 extra_weight 에
  흡수해 별도 panel 로 보내지 않음 → 본 시각화는 입력 그대로 그리면 자동
  반영됨. separate 모드는 별도 panel 로 들어오므로 일반 floor 처리.
- **B-9 적층 좌표 정정**: 원본의 단순 (cy + thickness + gap) 가 아니라, A-1
  일반화 wall_segments 의 점유 영역을 반영해 적층 패널 좌표를 정확히 산출.
- **B-16 화물 초과 가드**: 트럭 한도 초과(폭/길이/높이/중량) 시 트럭 외곽을
  빨강 굵은 선으로 표시하고 상단에 경고 텍스트.
- **색상 팔레트**: 원본 톤 기본 유지 + 다면 wall_segments 표시용 갈색 계열
  통일.

[보류]
- draw_3d_view: 분석 ③ 🟡 결정에 따라 본 페이즈에서는 미구현. 추후 필요시
  원본 코드 거의 그대로 이식 가능.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import plotly.graph_objects as go

from .models import Module, Panel, SpacingParams, Truck, WallSegment
from .packer import Trip


# ── 색상 ─────────────────────────────────────────────────
SEAT_PALETTE = ["#f4d35e", "#a8dadc", "#bde0fe", "#cdb4db", "#ffafcc", "#fdc4b6"]
LAYER_PALETTE = ["#1d3557", "#e63946", "#2a9d8f", "#9d0208", "#003049", "#7209b7"]
MODULE_COLOR = "#1f77b4"
FLOOR_COLOR = "#DEB887"          # 종속 floor 바닥판
WALL_SEG_COLOR = "#A0522D"       # wall_segment 표시
STACK_COLOR_FILL = "rgba(46,139,87,0.45)"
STACK_COLOR_EDGE = "#1a6b3c"
OVERLOAD_LINE = "#cc0000"


# ── 트럭 한도 초과 진단 (B-16) ────────────────────────────
def _overload_diagnose(trip: Trip) -> Optional[str]:
    """트럭 한도 대비 화물이 초과한 항목 메시지 (없으면 None)."""
    tr = trip.truck
    reasons: list[str] = []
    if trip.cargo_weight > tr.max_weight + 1e-6:
        reasons.append(
            f"중량 {trip.cargo_weight:.0f}kg > {tr.max_weight:.0f}kg"
        )
    # B-16 가드: 화물 길이가 트럭 유효 길이를 초과한 경우만 overload.
    # (이전 chained comparison `a > b > 0` 은 가독성·의도 모두 모호 — 정정.)
    if trip.usable_length_mm > 0 and trip.used_length_mm > trip.usable_length_mm + 1e-6:
        reasons.append(
            f"길이 {trip.used_length_mm:.0f}mm > 유효 {trip.usable_length_mm:.0f}mm"
        )
    # 폭/높이 초과는 각 item 별 확인
    inner_h = tr.max_height - tr.vehicle_height_offset
    for it in list(trip.items) + [s for s in trip.stacked_items if s is not None]:
        w = getattr(it, "width", 0.0)
        if w > tr.max_width + 1e-6:
            reasons.append(f"{getattr(it, 'name', '?')} 폭 {w:.0f}mm > {tr.max_width:.0f}mm")
    if not reasons:
        return None
    return "⚠ " + " | ".join(reasons)


# ── 트럭 외곽 (Top) ──────────────────────────────────────
def _truck_outline_top(fig: go.Figure, truck: Truck, overload: bool = False) -> None:
    color = OVERLOAD_LINE if overload else "black"
    width = 4 if overload else 3
    fig.add_shape(
        type="rect",
        x0=0, y0=0,
        x1=truck.max_length, y1=truck.max_width,
        line=dict(color=color, width=width),
        fillcolor="rgba(220,220,220,0.3)",
    )


def _truck_outline_rear(fig: go.Figure, truck: Truck, overload: bool = False) -> None:
    veh_h = truck.vehicle_height_offset
    line_color = OVERLOAD_LINE if overload else "dimgray"
    fig.add_shape(
        type="rect",
        x0=0, y0=0,
        x1=truck.max_width, y1=veh_h,
        line=dict(color=line_color, width=2),
        fillcolor="rgba(105,105,105,0.6)",
    )
    fig.add_annotation(
        x=truck.max_width / 2, y=veh_h / 2,
        text=f"<b>{truck.name}</b> 차체 ({int(veh_h)}mm)",
        showarrow=False, font=dict(size=10, color="white"),
    )
    fig.add_shape(
        type="rect",
        x0=0, y0=veh_h,
        x1=truck.max_width, y1=truck.max_height,
        line=dict(color=OVERLOAD_LINE if overload else "black",
                  width=3 if overload else 2,
                  dash="solid" if overload else "dot"),
        fillcolor="rgba(220,220,220,0.2)",
    )


def _edge_zones_top(fig: go.Figure, truck: Truck, edge: float) -> None:
    if edge <= 0:
        return
    for x0, x1 in [(0, edge), (truck.max_length - edge, truck.max_length)]:
        fig.add_shape(
            type="rect",
            x0=x0, y0=0, x1=x1, y1=truck.max_width,
            line=dict(color="orange", width=1, dash="dash"),
            fillcolor="rgba(255,165,0,0.15)",
        )


# ── A-1: wall_segments → Top view 그리기 ──────────────────
def _draw_wall_segments_top(
    fig: go.Figure, segs: Tuple[WallSegment, ...],
    base_x: float, base_y: float, panel_w: float, panel_l: float,
) -> None:
    """Top view 에서 종속 패널의 벽 세그먼트를 위에서 본 두께 박스로 표시.

    side 0=하변(y=base_y), 1=우변(x=base_x+panel_l), 2=상변(y=base_y+panel_w),
    3=좌변(x=base_x). 각 세그는 변 따라 start_offset_mm 부터 length_mm 길이.

    좌표계: Top view 에서 x = panel.length 방향, y = panel.width 방향.
    """
    for s in segs:
        th = s.thickness_mm
        if s.side == 0:  # 하변 (y 작음)
            x0 = base_x + s.start_offset_mm
            x1 = x0 + s.length_mm
            y0 = base_y
            y1 = y0 + th
        elif s.side == 2:  # 상변 (y 큼)
            x0 = base_x + s.start_offset_mm
            x1 = x0 + s.length_mm
            y1 = base_y + panel_w
            y0 = y1 - th
        elif s.side == 1:  # 우변 (x 큼)
            y0 = base_y + s.start_offset_mm
            y1 = y0 + s.length_mm
            x1 = base_x + panel_l
            x0 = x1 - th
        else:  # 좌변 (x 작음)
            y0 = base_y + s.start_offset_mm
            y1 = y0 + s.length_mm
            x0 = base_x
            x1 = x0 + th
        fig.add_shape(
            type="rect",
            x0=x0, y0=y0, x1=x1, y1=y1,
            line=dict(color="#5C3317", width=1),
            fillcolor=WALL_SEG_COLOR, opacity=0.65,
        )


# ── Top View ─────────────────────────────────────────────
def draw_top_view(trip: Trip, truck: Optional[Truck] = None,
                  sp: Optional[SpacingParams] = None) -> go.Figure:
    """위에서 본 적재 평면 (회차 1 개).

    truck, sp 미지정 시 trip.truck / 기본 SpacingParams 사용.
    """
    if truck is None:
        truck = trip.truck
    if sp is None:
        sp = SpacingParams()

    overload_msg = _overload_diagnose(trip)
    overload = overload_msg is not None
    fig = go.Figure()
    _truck_outline_top(fig, truck, overload=overload)
    edge = sp.truck_edge_clearance_mm
    gap = sp.panel_gap_mm

    if not trip.items:
        # 빈 회차
        fig.add_annotation(
            x=truck.max_length / 2, y=truck.max_width / 2,
            text="(빈 회차)", showarrow=False,
            font=dict(size=12, color="gray"),
        )
    elif trip.kind == "module":
        _edge_zones_top(fig, truck, edge)
        cursor = edge
        for k, item in enumerate(trip.items):
            cy = (truck.max_width - item.width) / 2
            fig.add_shape(
                type="rect",
                x0=cursor, y0=cy,
                x1=cursor + item.length, y1=cy + item.width,
                line=dict(color=MODULE_COLOR, width=2),
                fillcolor=MODULE_COLOR, opacity=0.5,
            )
            fig.add_annotation(
                x=cursor + item.length / 2, y=cy + item.width / 2,
                text=f"<b>{item.name}</b><br>{int(item.weight)}kg",
                showarrow=False, font=dict(size=11),
            )
            cursor += item.length + gap

    else:
        # 패널 — kind 분기
        sample = trip.items[0]
        is_dependent = (
            isinstance(sample, Panel)
            and ((sample.kind == "floor" and sample.wall_segments)
                 or sample.kind == "lshape")
        )
        is_wall = isinstance(sample, Panel) and sample.kind == "wall"

        _edge_zones_top(fig, truck, edge)
        if is_dependent:
            # 종속 패널 — 길이 방향 나란히 + wall_segments 표시 + 적층 표시
            cursor = edge
            for k, item in enumerate(trip.items):
                cy = (truck.max_width - item.width) / 2
                # 바닥판
                fig.add_shape(
                    type="rect",
                    x0=cursor, y0=cy,
                    x1=cursor + item.length, y1=cy + item.width,
                    line=dict(color="#8B4513", width=2),
                    fillcolor=SEAT_PALETTE[k % len(SEAT_PALETTE)],
                    opacity=0.7,
                )
                # wall_segments (A-1 일반화)
                if item.wall_segments:
                    _draw_wall_segments_top(
                        fig, item.wall_segments,
                        base_x=cursor, base_y=cy,
                        panel_w=item.width, panel_l=item.length,
                    )
                elif item.kind == "lshape":
                    # 원본 lshape 호환 — 한쪽 끝 (side=0) 단일 벽
                    fig.add_shape(
                        type="rect",
                        x0=cursor, y0=cy,
                        x1=cursor + item.length, y1=cy + item.thickness,
                        line=dict(color="#5C3317", width=1),
                        fillcolor=WALL_SEG_COLOR, opacity=0.6,
                    )
                # 적층 표시
                stk = trip.stacked_items[k] if k < len(trip.stacked_items) else None
                if stk is not None:
                    # 단순 표현: 패널 중앙에 점선 박스
                    sx_off = (item.length - stk.length) / 2
                    sy_off = (item.width - stk.width) / 2
                    fig.add_shape(
                        type="rect",
                        x0=cursor + sx_off, y0=cy + sy_off,
                        x1=cursor + sx_off + stk.length,
                        y1=cy + sy_off + stk.width,
                        line=dict(color=STACK_COLOR_EDGE, width=2, dash="dash"),
                        fillcolor=STACK_COLOR_FILL,
                    )
                # 라벨
                n_seg = len(item.wall_segments)
                kind_label = (
                    f"종속{n_seg}면" if n_seg > 0
                    else ("L자" if item.kind == "lshape" else "floor")
                )
                fig.add_annotation(
                    x=cursor + item.length / 2, y=cy + item.width / 2,
                    text=f"<b>{item.name}</b><br>{kind_label} "
                         f"{int(item.length)}×{int(item.width)}mm",
                    showarrow=False, font=dict(size=9),
                )
                cursor += item.length + gap

        else:
            # 순수 floor 또는 wall — 1단 ppr 매 평면
            ppr = max(trip.panels_per_row, 1)
            n_to_draw = min(ppr, len(trip.items))
            cursor = edge
            cy = (truck.max_width - sample.width) / 2
            line_color = "#8B0000" if is_wall else "#666"
            for k in range(n_to_draw):
                p = trip.items[k]
                fig.add_shape(
                    type="rect",
                    x0=cursor, y0=cy,
                    x1=cursor + p.length, y1=cy + p.width,
                    line=dict(color=line_color, width=2),
                    fillcolor=SEAT_PALETTE[k % len(SEAT_PALETTE)],
                    opacity=0.7,
                )
                fig.add_annotation(
                    x=cursor + p.length / 2, y=cy + p.width / 2,
                    text=f"<b>1단 · 자리 {k + 1}</b><br>{p.name}",
                    showarrow=False, font=dict(size=10),
                )
                cursor += p.length + gap
            if trip.n_layers > 1:
                fig.add_annotation(
                    x=truck.max_length / 2, y=truck.max_width + 250,
                    text=f"※ 본 평면이 위로 <b>{trip.n_layers}단</b> 적층 → Rear View 참조",
                    showarrow=False, font=dict(size=11, color="darkred"),
                )

    # B-16 overload 경고
    if overload_msg:
        fig.add_annotation(
            x=truck.max_length / 2, y=truck.max_width + 500,
            text=f"<b style='color:#cc0000'>{overload_msg}</b>",
            showarrow=False, font=dict(size=12),
        )

    fig.update_layout(
        title=f"#{trip.trip_no} {truck.name} — Top View",
        xaxis=dict(title="길이 (mm)", range=[-500, truck.max_length + 1200]),
        yaxis=dict(title="폭 (mm)", range=[-700, truck.max_width + 700],
                   scaleanchor="x", scaleratio=1),
        height=480, showlegend=False,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


# ── Rear View ────────────────────────────────────────────
def draw_rear_view(trip: Trip, truck: Optional[Truck] = None,
                   sp: Optional[SpacingParams] = None) -> go.Figure:
    """뒤에서 본 적재 단면 (회차 1 개)."""
    if truck is None:
        truck = trip.truck
    if sp is None:
        sp = SpacingParams()

    overload_msg = _overload_diagnose(trip)
    overload = overload_msg is not None
    fig = go.Figure()
    _truck_outline_rear(fig, truck, overload=overload)
    veh_h = truck.vehicle_height_offset
    gap = sp.panel_gap_mm

    if not trip.items:
        fig.add_annotation(
            x=truck.max_width / 2, y=(veh_h + truck.max_height) / 2,
            text="(빈 회차)", showarrow=False, font=dict(size=12, color="gray"),
        )
    elif trip.kind == "module":
        tallest = max(trip.items, key=lambda i: i.height)
        cx = (truck.max_width - tallest.width) / 2
        fig.add_shape(
            type="rect",
            x0=cx, y0=veh_h,
            x1=cx + tallest.width, y1=veh_h + tallest.height,
            line=dict(color=MODULE_COLOR, width=2),
            fillcolor=MODULE_COLOR, opacity=0.5,
        )
        names = list(dict.fromkeys(i.name for i in trip.items))
        name_str = " + ".join(names) if len(names) > 1 else names[0]
        fig.add_annotation(
            x=cx + tallest.width / 2, y=veh_h + tallest.height / 2,
            text=f"<b>{name_str}</b><br>폭 {int(tallest.width)} × 높이 {int(tallest.height)}mm",
            showarrow=False, font=dict(size=10),
        )

    else:
        sample = trip.items[0]
        is_dependent = (
            isinstance(sample, Panel)
            and ((sample.kind == "floor" and sample.wall_segments)
                 or sample.kind == "lshape")
        )
        is_wall = isinstance(sample, Panel) and sample.kind == "wall"

        if is_dependent:
            # 종속 패널 단면 — 첫 패널 기준 ㄴ자 형태
            cx = (truck.max_width - sample.width) / 2
            # 바닥판
            fig.add_shape(
                type="rect",
                x0=cx, y0=veh_h,
                x1=cx + sample.width, y1=veh_h + sample.thickness,
                line=dict(color="#8B4513", width=2),
                fillcolor=FLOOR_COLOR, opacity=0.85,
            )
            # 벽 부분 (wall_segments 중 가장 높은 것 한 줄 대표)
            max_seg_h = 0.0
            max_seg_t = sample.thickness
            if sample.wall_segments:
                worst = max(sample.wall_segments, key=lambda s: s.height_mm)
                max_seg_h = worst.height_mm
                max_seg_t = worst.thickness_mm
            elif sample.kind == "lshape":
                max_seg_h = sample.wall_height
                max_seg_t = sample.thickness
            if max_seg_h > 0:
                fig.add_shape(
                    type="rect",
                    x0=cx, y0=veh_h + sample.thickness,
                    x1=cx + max_seg_t,
                    y1=veh_h + sample.thickness + max_seg_h,
                    line=dict(color="#8B4513", width=2),
                    fillcolor=WALL_SEG_COLOR, opacity=0.85,
                )
            # 적층 패널 단면 (첫번째)
            stk0 = trip.stacked_items[0] if trip.stacked_items else None
            if stk0 is not None:
                h_gap = sp.lshape_stack_gap_mm
                stk_y0 = veh_h + sample.thickness + gap
                fig.add_shape(
                    type="rect",
                    x0=cx + max_seg_t + h_gap,
                    y0=stk_y0,
                    x1=cx + max_seg_t + h_gap + stk0.width,
                    y1=stk_y0 + stk0.thickness,
                    line=dict(color=STACK_COLOR_EDGE, width=2),
                    fillcolor=STACK_COLOR_FILL,
                )
                fig.add_annotation(
                    x=cx + max_seg_t + h_gap + stk0.width / 2,
                    y=stk_y0 + stk0.thickness / 2,
                    text=f"<b>▲ 적층</b><br>{stk0.name}",
                    showarrow=False, font=dict(size=9, color=STACK_COLOR_EDGE),
                )
            total_h = sample.thickness + max_seg_h
            n_stk = sum(1 for s in trip.stacked_items if s is not None)
            n_seg = len(sample.wall_segments)
            fig.add_annotation(
                x=truck.max_width / 2, y=veh_h + total_h + 350,
                text=f"<b>종속 {n_seg}면 패널 — {len(trip.items)}매"
                     f"{' + 적층 ' + str(n_stk) + '매' if n_stk else ''}</b><br>"
                     f"바닥 {int(sample.thickness)} + 벽 {int(max_seg_h)} mm",
                showarrow=False, font=dict(size=10, color="darkred"),
            )

        else:
            # 순수 floor / wall — 적층 단면
            ppr = max(trip.panels_per_row, 1)
            n_total = len(trip.items)
            used_layers = (n_total + ppr - 1) // ppr
            cx = (truck.max_width - sample.width) / 2
            cursor_y = veh_h
            line_color = "#8B0000" if is_wall else "black"
            for layer in range(used_layers):
                in_layer = min(ppr, n_total - layer * ppr)
                pos = ""
                if used_layers > 1:
                    if layer == 0:
                        pos = " (가장 아래)"
                    elif layer == used_layers - 1:
                        pos = " (가장 위)"
                fig.add_shape(
                    type="rect",
                    x0=cx, y0=cursor_y,
                    x1=cx + sample.width,
                    y1=cursor_y + sample.thickness,
                    line=dict(color=line_color, width=2),
                    fillcolor=LAYER_PALETTE[layer % len(LAYER_PALETTE)],
                    opacity=0.75,
                )
                fig.add_annotation(
                    x=cx + sample.width / 2,
                    y=cursor_y + sample.thickness / 2,
                    text=f"<b>{layer + 1}단{pos}</b> · {in_layer}매",
                    showarrow=False, font=dict(size=9, color="white"),
                )
                cursor_y += sample.thickness + gap
            total_h = used_layers * sample.thickness + (used_layers - 1) * gap
            fig.add_annotation(
                x=truck.max_width / 2, y=truck.max_height + 250,
                text=f"<b>{'벽체' if is_wall else '플로어'} 패널 {used_layers}단 적층</b> "
                     f"(높이 {int(total_h)}mm) · 각 단 {ppr}매 → 총 {n_total}매",
                showarrow=False, font=dict(size=10, color="darkred"),
            )

    if overload_msg:
        fig.add_annotation(
            x=truck.max_width / 2, y=truck.max_height + 500,
            text=f"<b style='color:#cc0000'>{overload_msg}</b>",
            showarrow=False, font=dict(size=12),
        )

    fig.update_layout(
        title=f"#{trip.trip_no} {truck.name} — Rear View",
        xaxis=dict(title="폭 (mm)", range=[-300, truck.max_width + 1200]),
        yaxis=dict(title="높이 (mm)", range=[-700, truck.max_height + 500],
                   scaleanchor="x", scaleratio=1),
        height=560, showlegend=False,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


__all__ = ["draw_top_view", "draw_rear_view"]
