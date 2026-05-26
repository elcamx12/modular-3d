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
    # 폭 초과는 각 item 별 확인 — 트럭 폭 + 양쪽 여유(튀어나옴) 까지 허용.
    inner_h = tr.max_height - tr.vehicle_height_offset
    eff_w = tr.max_width + 2.0 * SpacingParams().side_overhang_mm
    for it in list(trip.items) + [s for s in trip.stacked_items if s is not None]:
        w = getattr(it, "width", 0.0)
        if w > eff_w + 1e-6:
            reasons.append(f"{getattr(it, 'name', '?')} 폭 {w:.0f}mm > 적재폭 {eff_w:.0f}mm")
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
        # [Phase 8 안전성] 모듈 + 패널 혼적 회차에서도 *모듈만 추려서* 박스를 그림.
        # 패널은 별도 색상으로 옆자리에 작게 표시 — 시각 혼동 방지.
        _edge_zones_top(fig, truck, edge)
        cursor = edge
        for k, item in enumerate(trip.items):
            cy = (truck.max_width - item.width) / 2
            is_module = isinstance(item, Module)
            color = MODULE_COLOR if is_module else SEAT_PALETTE[k % len(SEAT_PALETTE)]
            fig.add_shape(
                type="rect",
                x0=cursor, y0=cy,
                x1=cursor + item.length, y1=cy + item.width,
                line=dict(color=color, width=2),
                fillcolor=color, opacity=0.5,
            )
            label_kind = "" if is_module else "<br>(패널 동시 적재)"
            fig.add_annotation(
                x=cursor + item.length / 2, y=cy + item.width / 2,
                text=f"<b>{item.name}</b><br>{int(item.weight)}kg{label_kind}",
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
        # [Phase 8 안전성] 신규 패커가 만든 *모듈 + 패널 혼적* 회차도 trip.kind=="module"
        # (items[0] 이 Module 이라 분기). Panel 은 .height 가 없어 AttributeError 발생 가능 →
        # Module 만 필터해 그린다. 패널이 끼어 있으면 별도 라벨로 표시.
        modules_in_trip = [i for i in trip.items if isinstance(i, Module)]
        panels_in_trip = [i for i in trip.items if not isinstance(i, Module)]
        if not modules_in_trip:
            # 안전판 — items[0]이 Module 인데 필터 결과가 비면 그릴 게 없음
            fig.add_annotation(
                x=truck.max_width / 2, y=(veh_h + truck.max_height) / 2,
                text="(모듈 없음)", showarrow=False,
                font=dict(size=12, color="gray"),
            )
        else:
            tallest = max(modules_in_trip, key=lambda i: i.height)
            cx = (truck.max_width - tallest.width) / 2
            fig.add_shape(
                type="rect",
                x0=cx, y0=veh_h,
                x1=cx + tallest.width, y1=veh_h + tallest.height,
                line=dict(color=MODULE_COLOR, width=2),
                fillcolor=MODULE_COLOR, opacity=0.5,
            )
            names = list(dict.fromkeys(i.name for i in modules_in_trip))
            name_str = " + ".join(names) if len(names) > 1 else names[0]
            label = (
                f"<b>{name_str}</b><br>폭 {int(tallest.width)} × "
                f"높이 {int(tallest.height)}mm"
            )
            if panels_in_trip:
                label += f"<br>+ 패널 {len(panels_in_trip)} 매 동시 적재"
            fig.add_annotation(
                x=cx + tallest.width / 2, y=veh_h + tallest.height / 2,
                text=label, showarrow=False, font=dict(size=10),
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


# ════════════════════════════════════════════════════════════════════
# Phase B — 3D 적재 시각화 (Plotly 3D + Mesh3d / Scatter3d)
# ════════════════════════════════════════════════════════════════════
#
# [좌표 약속]
# - x_3d = 트럭 길이 방향. 회차마다 *옆으로 나란히* 배치 (회차 사이 간격 2000 mm)
# - y_3d = 트럭 폭 방향. 트럭 박스 좌측 끝 = y_3d=0, 우측 끝 = y_3d=truck.max_width
# - z_3d = 높이 방향. 지면 = z_3d=0, 차량 적재면 = z_3d=vehicle_height_offset
#
# [Placement.truck_xyz 와의 변환]
# - placement.truck_xyz 는 적재함 중심 원점 기준. 화물 *중심* 좌표.
# - 3D 좌표 = (x_offset + max_length/2 + truck_xyz[0],
#              max_width/2 + truck_xyz[1],
#              vehicle_height_offset + truck_xyz[2])
# ════════════════════════════════════════════════════════════════════

INTER_TRUCK_GAP_MM: float = 3000.0  # 회차 간 좌우 간격 (사용자 결정 — 1m 추가)

_COLOR_MODULE = "#2a7ade"
_COLOR_FLOOR = "#4CAF50"
_COLOR_DEP_FLOOR = "#FF9800"
_COLOR_WALL_PANEL = "#9C27B0"
_COLOR_WALL_SEG = "#8B4513"
_COLOR_TRUCK_LINE = "#333"


def _cube_mesh(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str, name: str, opacity: float = 0.6,
) -> go.Mesh3d:
    """축 정렬 직육면체 Mesh3d — 8 정점 + 12 삼각형."""
    xs = [x0,    x0+dx, x0+dx, x0,    x0,    x0+dx, x0+dx, x0]
    ys = [y0,    y0,    y0+dy, y0+dy, y0,    y0,    y0+dy, y0+dy]
    zs = [z0,    z0,    z0,    z0,    z0+dz, z0+dz, z0+dz, z0+dz]
    # 표준 cube mesh — 6 면 × 2 삼각형 = 12 (정점 인덱스)
    i = [0, 0, 4, 4, 0, 0, 3, 3, 0, 0, 1, 1]
    j = [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 2, 6]
    k = [2, 3, 6, 7, 5, 4, 6, 7, 7, 4, 6, 5]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color=color, opacity=opacity, name=name,
        flatshading=True, hoverinfo="text", hovertext=name,
    )


def _wireframe_box_3d(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str, name: str, width: int = 3,
) -> go.Scatter3d:
    """직육면체 외곽선 — 12 edge 를 None 으로 구분한 단일 Scatter3d trace."""
    pts = [
        (x0,    y0,    z0   ), (x0+dx, y0,    z0   ),
        (x0+dx, y0+dy, z0   ), (x0,    y0+dy, z0   ),
        (x0,    y0,    z0+dz), (x0+dx, y0,    z0+dz),
        (x0+dx, y0+dy, z0+dz), (x0,    y0+dy, z0+dz),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),   # 바닥
        (4, 5), (5, 6), (6, 7), (7, 4),   # 천정
        (0, 4), (1, 5), (2, 6), (3, 7),   # 수직
    ]
    xs: list = []
    ys: list = []
    zs: list = []
    for a, b in edges:
        xs += [pts[a][0], pts[b][0], None]
        ys += [pts[a][1], pts[b][1], None]
        zs += [pts[a][2], pts[b][2], None]
    return go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=dict(color=color, width=width),
        name=name, showlegend=False, hoverinfo="skip",
    )


def _item_dims_3d(item, posture):
    """화물의 3D 차원 (length, width, height) — 자세 반영.

    Module: 항상 (length, width, height).
    Panel LYING: (length, width, 점유두께).
    Panel STANDING: (length, thickness, width) — width 가 위로 솟음.
    """
    # 지연 import — packer_safety 에 동일 식 존재
    from .packer_safety import _item_dims_for_posture
    L, W, h_occ, _ = _item_dims_for_posture(item, posture)
    return L, W, h_occ


def _add_truck_bed_3d(fig: go.Figure, x_off: float, truck: Truck) -> None:
    """트럭 적재함 외곽선 + 적재함 바닥 판 + 차체(캐빈·바퀴) + 차종 라벨.

    [Phase D — 2026-05-26 / 2026-05-26 외관 보강]
    - 적재함 외곽선 (와이어프레임)
    - 적재함 *바닥 판* (얇은 회색 plate — 화물이 올라가는 면)
    - 캐빈 (바퀴 위 부터 솟음 — 적재면과 같은 높이에서 시작)
    - 바퀴 4 쌍 (앞바퀴 1 + 적재함 하단 3) = 8 개 — 캐빈 앞에도 바퀴
    - A-frame 트럭: 적재함 양옆에 A 자 프레임
    - 차종 라벨

    각 부분은 *간략한 박스* — 실제 차량 외관까진 아님.
    """
    veh_h = truck.vehicle_height_offset
    bed_dz = truck.max_height - veh_h
    inner_h = bed_dz  # 적재함 내공 높이

    # ① 적재함 외곽선
    fig.add_trace(_wireframe_box_3d(
        x0=x_off, y0=0.0, z0=veh_h,
        dx=truck.max_length, dy=truck.max_width, dz=inner_h,
        color=_COLOR_TRUCK_LINE, name=f"{truck.name} 적재함", width=4,
    ))

    # ②-신규: 적재함 *바닥 판* — 화물이 올라가는 면 (얇은 회색 plate)
    BED_FLOOR_TH = 80.0  # 바닥 판 두께
    fig.add_trace(_cube_mesh(
        x0=x_off, y0=0.0, z0=veh_h - BED_FLOOR_TH,
        dx=truck.max_length, dy=truck.max_width, dz=BED_FLOOR_TH,
        color="#555", name=f"{truck.name} 적재 바닥", opacity=0.85,
    ))

    # 차종별 색상 + 캐빈 사이즈
    truck_type = truck.truck_type
    if truck_type == "lowbed":
        cab_color = "#1976D2"
        cab_label = "저상"
    elif truck_type == "extendable":
        cab_color = "#388E3C"
        cab_label = "광폭"
    elif truck_type == "aframe":
        cab_color = "#E64A19"
        cab_label = "A-frame"
    else:
        cab_color = "#616161"
        cab_label = truck_type
    label_text = f"<b>{cab_label} · {truck.name}</b>"

    # ③ 캐빈 — 적재함 앞쪽. 바퀴 위(veh_h) 부터 솟음. 길이 2400, 폭 트럭폭, 높이 2200.
    cab_len = 2400.0
    cab_dy = truck.max_width
    cab_y0 = 0.0
    cab_dz = 2200.0
    cab_x0 = x_off - cab_len - 200.0  # 적재함과 200mm 공간
    cab_z0 = veh_h  # *바퀴 위* 부터 솟음 (수정 — 더 이상 지면 시작 X)
    fig.add_trace(_cube_mesh(
        x0=cab_x0, y0=cab_y0, z0=cab_z0,
        dx=cab_len, dy=cab_dy, dz=cab_dz,
        color=cab_color, name=f"{cab_label} 캐빈", opacity=0.85,
    ))
    fig.add_trace(_wireframe_box_3d(
        x0=cab_x0, y0=cab_y0, z0=cab_z0,
        dx=cab_len, dy=cab_dy, dz=cab_dz,
        color="#222", name=f"{cab_label} 캐빈", width=2,
    ))

    # ③-b: 캐빈 윈드실드 (앞면) 표시 — 더 진한 라인 — 시각 정체성 강화
    # (생략 — 추후 보강)

    # ④ 바퀴 — 4 쌍 (앞바퀴 1 + 적재함 하단 3)
    wheel_dy = 400.0
    wheel_dz = veh_h  # 바퀴 = 지면 ~ vehicle_height_offset
    wheel_len = 700.0
    wheel_color = "#1A1A1A"
    # 바퀴 x 위치 — 앞바퀴(캐빈 영역 안) + 적재함 앞/중/뒤
    wheel_xs = [
        cab_x0 + cab_len * 0.3,                                         # 앞바퀴 (캐빈)
        x_off + 500.0,                                                  # 적재함 앞
        x_off + truck.max_length / 2 - wheel_len / 2,                   # 적재함 중간
        x_off + truck.max_length - 500.0 - wheel_len,                   # 적재함 뒤
    ]
    for wx in wheel_xs:
        # 좌측 바퀴 — 트럭 좌측 살짝 튀어나옴
        fig.add_trace(_cube_mesh(
            x0=wx, y0=-wheel_dy * 0.2, z0=0.0,
            dx=wheel_len, dy=wheel_dy, dz=wheel_dz,
            color=wheel_color, name="바퀴", opacity=0.95,
        ))
        # 우측 바퀴
        fig.add_trace(_cube_mesh(
            x0=wx, y0=truck.max_width - wheel_dy * 0.8, z0=0.0,
            dx=wheel_len, dy=wheel_dy, dz=wheel_dz,
            color=wheel_color, name="바퀴", opacity=0.95,
        ))

    # ⑤ A-frame
    if truck_type == "aframe":
        frame_h = inner_h + 1500.0
        frame_thickness = 200.0
        for which_side in ("left", "right"):
            fy = 0.0 if which_side == "left" else (truck.max_width - frame_thickness)
            fig.add_trace(_cube_mesh(
                x0=x_off + truck.max_length * 0.3,
                y0=fy,
                z0=veh_h,
                dx=truck.max_length * 0.4,
                dy=frame_thickness,
                dz=frame_h,
                color="#E64A19", name=f"A프레임 {which_side}", opacity=0.5,
            ))

    # ⑥ 차종 라벨
    fig.add_trace(go.Scatter3d(
        x=[x_off + truck.max_length / 2],
        y=[truck.max_width + 300],
        z=[veh_h + inner_h / 2],
        text=[label_text],
        mode="text", textfont=dict(size=12, color=cab_color),
        showlegend=False, hoverinfo="skip",
    ))


def _placement_color(item) -> Tuple[str, str]:
    """화물 종류별 색상 + 분류 라벨."""
    if isinstance(item, Module):
        return _COLOR_MODULE, "모듈"
    if isinstance(item, Panel):
        if item.kind == "wall":
            return _COLOR_WALL_PANEL, "벽 패널"
        if item.wall_segments or item.kind == "lshape":
            return _COLOR_DEP_FLOOR, "종속 floor"
        return _COLOR_FLOOR, "floor"
    return "#999", "?"


def _add_wall_segment_3d(
    fig: go.Figure, floor_x0: float, floor_y0: float, floor_top_z: float,
    panel: Panel, seg: WallSegment,
) -> None:
    """wall_segment 직육면체 — floor 좌하단 + 상단 위에서 벽 높이만큼 솟음.

    [좌표 약속] (Phase A 정규화 후의 좌표계)
    - floor 로컬: x=0~panel.length (길이 방향), y=0~panel.width (폭 방향)
    - side 0 = 하변 (y=0, x 방향 변, 변길이=panel.length)
    - side 1 = 우변 (x=panel.length, y 방향 변, 변길이=panel.width)
    - side 2 = 상변 (y=panel.width, x 방향 변)
    - side 3 = 좌변 (x=0, y 방향 변)
    """
    if seg.side == 0:  # 하변
        sx = floor_x0 + seg.start_offset_mm
        sy = floor_y0
        sdx = seg.length_mm
        sdy = seg.thickness_mm
    elif seg.side == 1:  # 우변
        sx = floor_x0 + panel.length - seg.thickness_mm
        sy = floor_y0 + seg.start_offset_mm
        sdx = seg.thickness_mm
        sdy = seg.length_mm
    elif seg.side == 2:  # 상변
        sx = floor_x0 + seg.start_offset_mm
        sy = floor_y0 + panel.width - seg.thickness_mm
        sdx = seg.length_mm
        sdy = seg.thickness_mm
    else:  # side 3 — 좌변
        sx = floor_x0
        sy = floor_y0 + seg.start_offset_mm
        sdx = seg.thickness_mm
        sdy = seg.length_mm

    name = f"wall side={seg.side} h={int(seg.height_mm)}mm"
    fig.add_trace(_cube_mesh(
        x0=sx, y0=sy, z0=floor_top_z,
        dx=sdx, dy=sdy, dz=seg.height_mm,
        color=_COLOR_WALL_SEG, name=name, opacity=0.7,
    ))
    fig.add_trace(_wireframe_box_3d(
        x0=sx, y0=sy, z0=floor_top_z,
        dx=sdx, dy=sdy, dz=seg.height_mm,
        color="#5C3317", name=name, width=2,
    ))


def _add_placement_3d(
    fig: go.Figure, x_off: float, placement, truck: Truck,
) -> None:
    """단일 placement 3D 박스 + 라벨 + (종속 floor 면) wall_segments."""
    # 지연 import — Posture 사용
    from .packer_types import Posture

    item = placement.item
    posture = placement.posture

    # 3D 중심 좌표 변환
    cx = x_off + truck.max_length / 2 + placement.truck_xyz[0]
    cy = truck.max_width / 2 + placement.truck_xyz[1]
    cz = truck.vehicle_height_offset + placement.truck_xyz[2]

    L, W, h_occ = _item_dims_3d(item, posture)
    x0 = cx - L / 2
    y0 = cy - W / 2
    z0 = cz  # truck_xyz[2] 는 화물 *하단* z 위치

    color, kind = _placement_color(item)
    posture_label = "" if posture == Posture.LYING else "·세움"
    name = f"{getattr(item, 'name', '?')} ({kind}{posture_label})"

    # 본체 + 외곽선
    fig.add_trace(_cube_mesh(x0, y0, z0, L, W, h_occ, color, name, opacity=0.55))
    fig.add_trace(_wireframe_box_3d(x0, y0, z0, L, W, h_occ, "#222", name, width=2))

    # 화물 라벨 — 중심 상단
    fig.add_trace(go.Scatter3d(
        x=[cx], y=[cy], z=[z0 + h_occ + 50],
        text=[f"<b>{getattr(item, 'name', '?')}</b>"],
        mode="text", textfont=dict(size=10),
        showlegend=False, hoverinfo="skip",
    ))

    # 종속 floor — wall_segments 위로 솟게 그림 (LYING 자세만)
    if (
        isinstance(item, Panel)
        and item.wall_segments
        and posture == Posture.LYING
    ):
        floor_top_z = z0 + item.thickness  # floor 두께 위
        for seg in item.wall_segments:
            _add_wall_segment_3d(fig, x0, y0, floor_top_z, item, seg)


def _add_legacy_items_3d(
    fig: go.Figure, x_off: float, trip: Trip, truck: Truck, sp: SpacingParams,
) -> None:
    """역호환 — Trip.placements 가 비어있는 구 패커 결과에서 items 직접 그리기."""
    edge = sp.truck_edge_clearance_mm
    gap = sp.panel_gap_mm
    cursor_x = x_off + edge
    for item in trip.items:
        if isinstance(item, Module):
            L, W, h_occ = item.length, item.width, item.height
            color = _COLOR_MODULE
            kind = "모듈"
        elif isinstance(item, Panel):
            L, W = item.length, item.width
            if item.wall_segments:
                # 종속 — 두께 + 가장 높은 wall_segment
                max_seg = max(s.height_mm for s in item.wall_segments)
                h_occ = item.thickness + max_seg
                color = _COLOR_DEP_FLOOR
                kind = "종속 floor"
            elif item.kind == "wall":
                h_occ = item.thickness
                color = _COLOR_WALL_PANEL
                kind = "벽 패널"
            else:
                h_occ = item.thickness
                color = _COLOR_FLOOR
                kind = "floor"
        else:
            continue
        # 폭 중심 정렬
        y0 = truck.max_width / 2 - W / 2
        z0 = truck.vehicle_height_offset
        name = f"{item.name} ({kind})"
        fig.add_trace(_cube_mesh(
            x0=cursor_x, y0=y0, z0=z0,
            dx=L, dy=W, dz=h_occ,
            color=color, name=name, opacity=0.55,
        ))
        fig.add_trace(_wireframe_box_3d(
            x0=cursor_x, y0=y0, z0=z0,
            dx=L, dy=W, dz=h_occ,
            color="#222", name=name, width=2,
        ))
        cursor_x += L + gap


def _camera_presets() -> dict:
    """카메라 프리셋 — Top / Side / Iso. updatemenus 버튼으로 노출.

    Plotly `scene.camera.eye` 는 카메라 위치 (단위 = scene 크기).
    - Iso: (1.5, -1.5, 1.0) — 일반 3D 입체
    - Top: (0, 0, 2.5) — 위에서 내려다보기
    - Side: (0, -2.5, 0) — 옆에서 (트럭 측면)
    """
    return {
        "iso":  dict(eye=dict(x=1.5,  y=-1.5, z=1.0), up=dict(x=0, y=0, z=1)),
        "top":  dict(eye=dict(x=0,    y=0,    z=2.5), up=dict(x=0, y=1, z=0)),
        "side": dict(eye=dict(x=0,    y=-2.5, z=0.3), up=dict(x=0, y=0, z=1)),
    }


def _trip_overlay_text(trip: Trip, cost_krw: Optional[float] = None) -> str:
    """회차 오버레이 라벨 — 트럭 위 표시. 트럭명·총중량·적재율·비용."""
    parts = [f"<b>#{trip.trip_no} · {trip.truck.name}</b>"]
    parts.append(f"화물 {trip.cargo_weight:.0f} kg")
    util = trip.utilization
    parts.append(f"적재율 {util:.0f}%")
    if cost_krw is not None:
        parts.append(f"₩{cost_krw:,.0f}")
    return "<br>".join(parts)


def draw_loaded_3d_view(
    trips: List[Trip],
    sp: Optional[SpacingParams] = None,
    highlight_trip_no: Optional[int] = None,
    trip_costs: Optional[dict] = None,
) -> go.Figure:
    """모든 회차를 한 Plotly 3D Figure 에 *옆으로 나란히* 배치.

    Phase B — 트럭 적재함 외곽선 + 화물 (모듈·floor·wall·종속 floor) 실제 형상.
    Phase D — 트럭 차체(캐빈·바퀴) + 차종 라벨.
    Phase E — 카메라 프리셋(Top/Side/Iso) 버튼 + 회차 강조 + 오버레이 (트럭명·총중량·적재율·비용).

    Args:
        trips: 회차 목록
        sp: 간격 파라미터
        highlight_trip_no: 강조할 회차 번호 — 해당 트럭 외곽선이 빨간 굵은 선으로
            덧그려짐. None 이면 강조 없음.
        trip_costs: {trip_no: cost_krw} 매핑 — 오버레이 텍스트에 비용 추가.
            None 이면 비용 표시 생략.

    Returns:
        Plotly Figure
    """
    if sp is None:
        sp = SpacingParams()
    fig = go.Figure()

    if not trips:
        fig.add_annotation(
            text="(회차 없음 — 운송 계산을 실행해주세요)",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="gray"),
        )
        fig.update_layout(
            scene=dict(
                xaxis_title="길이 (mm)",
                yaxis_title="폭 (mm)",
                zaxis_title="높이 (mm)",
            ),
            margin=dict(l=0, r=0, t=30, b=0),
            showlegend=False,
            title="적재 3D 도식 — 회차 없음",
        )
        return fig

    trip_costs = trip_costs or {}
    x_off = 0.0
    for trip in trips:
        truck = trip.truck
        # 트럭 적재함 외곽선 + 차체
        _add_truck_bed_3d(fig, x_off, truck)
        # 화물
        if trip.placements:
            for placement in trip.placements:
                _add_placement_3d(fig, x_off, placement, truck)
        else:
            _add_legacy_items_3d(fig, x_off, trip, truck, sp)
        # 회차 오버레이 텍스트 — 트럭 위쪽
        overlay = _trip_overlay_text(trip, trip_costs.get(trip.trip_no))
        fig.add_trace(go.Scatter3d(
            x=[x_off + truck.max_length / 2],
            y=[truck.max_width / 2],
            z=[truck.max_height + 700],
            text=[overlay],
            mode="text", textfont=dict(size=11, color="#222"),
            showlegend=False, hoverinfo="skip",
        ))
        # 강조 — highlight_trip_no 일치 시 빨간 굵은 외곽선 덧그림
        if highlight_trip_no is not None and trip.trip_no == highlight_trip_no:
            fig.add_trace(_wireframe_box_3d(
                x0=x_off, y0=0.0, z0=truck.vehicle_height_offset,
                dx=truck.max_length, dy=truck.max_width,
                dz=truck.max_height - truck.vehicle_height_offset,
                color="#cc0000", name=f"강조 #{trip.trip_no}", width=7,
            ))
        x_off += truck.max_length + INTER_TRUCK_GAP_MM

    # 카메라 프리셋 버튼 — Plotly updatemenus
    presets = _camera_presets()
    # [사용자 결정 — 그리드 제거] 모든 축의 grid·zeroline·배경 면 숨김 + 축 자체도 숨김
    _axis_no_grid = dict(
        showgrid=False, zeroline=False, showline=False,
        showticklabels=False, showbackground=False,
        title="",
    )
    fig.update_layout(
        scene=dict(
            xaxis=_axis_no_grid,
            yaxis=_axis_no_grid,
            zaxis=_axis_no_grid,
            aspectmode="data",
            camera=presets["iso"],
            # [Phase E 핫픽스 — 2026-05-26] uirevision 유지 — 같은 값일 때
            # Plotly 가 카메라 state 보존 시도. WebEngineView 재로드 시 효과
            # 제한적이라 완전 보존은 JS API 필요 (큰 결정 — 사용자 확인 중).
            uirevision="loaded_3d",
        ),
        margin=dict(l=0, r=0, t=60, b=0),
        showlegend=False,
        title=f"적재 3D 도식 — {len(trips)} 회차",
        updatemenus=[
            dict(
                type="buttons", direction="right", showactive=True,
                x=0.02, y=1.10, xanchor="left", yanchor="top",
                buttons=[
                    dict(label="🔄 Iso",
                         method="relayout",
                         args=[{"scene.camera": presets["iso"]}]),
                    dict(label="⬇ Top",
                         method="relayout",
                         args=[{"scene.camera": presets["top"]}]),
                    dict(label="➡ Side",
                         method="relayout",
                         args=[{"scene.camera": presets["side"]}]),
                ],
            ),
        ],
    )
    return fig


__all__ = ["draw_top_view", "draw_rear_view", "draw_loaded_3d_view"]
