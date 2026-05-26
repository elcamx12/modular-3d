"""Phase B 단위 테스트 — `draw_loaded_3d_view` (3D 적재 시각화).

[검증 범위]
- 빈 trips → 빈 Figure (안내 annotation)
- 1 회차 (모듈 1) → trace 생성 (적재함 외곽선 + 모듈 박스 + 라벨)
- 종속 floor + wall_segments → 바닥 + 벽 직육면체 trace
- 여러 회차 → x 오프셋이 누적 (옆으로 나란히)
- 단순 wall (STANDING 자세) — Posture.STANDING 도 정상 차원 계산
- 역호환 — placements 가 비어있는 Trip 도 items 로 그릴 수 있음
"""
from __future__ import annotations

import pytest

import plotly.graph_objects as go

from modular_3d.transport.models import (
    Module, Panel, Section, SpacingParams, Truck, WallSegment,
)
from modular_3d.transport.packer import PackResult, Trip
from modular_3d.transport.packer_core import (
    GREEDY_STRATEGIES, WEIGHT_SETS, default_eco_options, pack_one_seed,
)
from modular_3d.transport.packer_types import (
    Placement, PlacementSlot, Posture,
)
from modular_3d.transport.tests.transport_v2_fixtures import (
    default_site, default_trucks, generate_fixture,
)
from modular_3d.transport.visualizer import (
    INTER_TRUCK_GAP_MM,
    _cube_mesh,
    _wireframe_box_3d,
    draw_loaded_3d_view,
)


_SHS = Section(name="SHS200x8", section_type="SHS",
               width=200, height=200, thickness=8, weight_per_m=47.9)


def _lowbed() -> Truck:
    return Truck(name="저상25t", truck_type="lowbed",
                 max_length=12000, max_width=3000, max_height=4500,
                 max_weight=25000, vehicle_height_offset=700,
                 curb_weight_kg=14000, active=True)


def _module(name="M1", length=6000, width=3000, height=2700, extra=5000) -> Module:
    return Module(name=name, length=length, width=width, height=height,
                  column_section=_SHS, beam_section=_SHS,
                  extra_weight_kg=extra)


def _floor(name="F1", length=6000, width=2800, thickness=150, extra=2500) -> Panel:
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=thickness, beam_section=_SHS,
                 extra_weight_kg=extra)


def _lshape(name="L1", length=8000, width=3000, side=0,
            seg_h=3000, seg_t=200, extra=2500) -> Panel:
    seg_len = length if side in (0, 2) else width
    seg = WallSegment(
        side=side, start_offset_mm=0.0, length_mm=seg_len,
        height_mm=seg_h, thickness_mm=seg_t,
        column_section=_SHS, beam_section=_SHS,
    )
    return Panel(name=name, kind="floor", width=width, length=length,
                 thickness=150.0, beam_section=_SHS,
                 wall_segments=(seg,), extra_weight_kg=extra)


# ════════════════════════════════════════════════════════════════════
# §1 헬퍼 — _cube_mesh / _wireframe_box_3d
# ════════════════════════════════════════════════════════════════════
def test_cube_mesh_has_8_vertices_12_triangles():
    mesh = _cube_mesh(0, 0, 0, 1000, 500, 300, "#FF0000", "test")
    assert len(mesh.x) == 8
    assert len(mesh.y) == 8
    assert len(mesh.z) == 8
    assert len(mesh.i) == 12
    assert len(mesh.j) == 12
    assert len(mesh.k) == 12


def test_wireframe_box_has_12_edges():
    wf = _wireframe_box_3d(0, 0, 0, 1000, 500, 300, "#000", "test")
    # 12 edges × 3 points (start, end, None) = 36
    assert len(wf.x) == 36
    assert wf.mode == "lines"


# ════════════════════════════════════════════════════════════════════
# §2 빈 trips
# ════════════════════════════════════════════════════════════════════
def test_draw_empty_trips_returns_figure_with_annotation():
    fig = draw_loaded_3d_view([])
    assert isinstance(fig, go.Figure)
    # annotation 1 개 (회차 없음 안내)
    assert len(fig.layout.annotations) >= 1


# ════════════════════════════════════════════════════════════════════
# §3 1 회차 모듈
# ════════════════════════════════════════════════════════════════════
def test_draw_single_module_trip_has_traces():
    """모듈 1 회차 → 적재함 외곽선 + 차체(캐빈·바퀴) + 모듈 박스 + 라벨."""
    m = _module()
    placement = Placement(
        item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
        truck_xyz=(0.0, 0.0, 0.0), parent_idx=None,
    )
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[placement])
    fig = draw_loaded_3d_view([trip])
    # 적재함 외곽선 + 적재 바닥 + 캐빈 mesh + 캐빈 외곽선 + 바퀴 8 + 차종라벨 +
    # 모듈 mesh + 모듈 외곽선 + 화물 라벨 + 회차 라벨 = 최소 16 traces
    assert len(fig.data) >= 16
    # 모듈 mesh 가 trace 안에 존재
    module_meshes = [d for d in fig.data
                     if isinstance(d, go.Mesh3d) and "M1" in (d.hovertext or "")]
    assert len(module_meshes) >= 1


# ════════════════════════════════════════════════════════════════════
# §4 종속 floor + wall_segments
# ════════════════════════════════════════════════════════════════════
def test_draw_dependent_floor_includes_wall_segment_traces():
    """L 자 종속 floor → wall_segment 직육면체도 trace 로 들어감."""
    L = _lshape()
    placement = Placement(
        item=L, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
        truck_xyz=(0.0, 0.0, 0.0), parent_idx=None,
    )
    trip = Trip(trip_no=1, truck=_lowbed(), items=[L], placements=[placement])
    fig = draw_loaded_3d_view([trip])
    # wall_segment trace 가 들어가 있는지 (이름에 "wall side" 포함)
    wall_seg_traces = [d for d in fig.data
                       if "wall side" in (getattr(d, "name", "") or "")]
    assert len(wall_seg_traces) >= 1  # L 자 = 1 변 → mesh + wireframe = 2 trace 예상


def test_draw_4face_dependent_includes_4_walls():
    """4 면 종속 → 4 개 wall_segment 직육면체."""
    segs = tuple(
        WallSegment(
            side=s, start_offset_mm=0.0,
            length_mm=(8000 if s in (0, 2) else 3000),
            height_mm=3000.0, thickness_mm=200.0,
            column_section=_SHS, beam_section=_SHS,
        )
        for s in (0, 1, 2, 3)
    )
    p4 = Panel(name="C4", kind="floor", width=3000, length=8000, thickness=150,
               beam_section=_SHS, wall_segments=segs, extra_weight_kg=3000)
    placement = Placement(
        item=p4, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
        truck_xyz=(0.0, 0.0, 0.0), parent_idx=None,
    )
    trip = Trip(trip_no=1, truck=_lowbed(), items=[p4], placements=[placement])
    fig = draw_loaded_3d_view([trip])
    # 4 면 wall_segment Mesh3d 4 개
    wall_meshes = [d for d in fig.data
                   if isinstance(d, go.Mesh3d)
                   and "wall side" in (d.name or "")]
    assert len(wall_meshes) == 4


# ════════════════════════════════════════════════════════════════════
# §5 여러 회차 — x 오프셋 누적
# ════════════════════════════════════════════════════════════════════
def test_multiple_trips_have_x_offset_accumulated():
    """회차 2 개 → 두 번째 트럭의 x 좌표가 첫 번째보다 max_length + 2000 이상 큼."""
    m1 = _module(name="M1")
    m2 = _module(name="M2")
    truck = _lowbed()
    p1 = Placement(item=m1, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                   truck_xyz=(0, 0, 0), parent_idx=None)
    p2 = Placement(item=m2, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
                   truck_xyz=(0, 0, 0), parent_idx=None)
    t1 = Trip(trip_no=1, truck=truck, items=[m1], placements=[p1])
    t2 = Trip(trip_no=2, truck=truck, items=[m2], placements=[p2])
    fig = draw_loaded_3d_view([t1, t2])
    # 두 트럭 외곽선의 x 좌표 비교
    truck_wfs = [d for d in fig.data
                 if isinstance(d, go.Scatter3d) and d.line and d.line.width == 4]
    assert len(truck_wfs) == 2
    # 첫 트럭 x 범위와 두 번째 트럭 x 범위가 떨어져 있어야
    xs0 = [x for x in truck_wfs[0].x if x is not None]
    xs1 = [x for x in truck_wfs[1].x if x is not None]
    assert max(xs0) <= min(xs1) + 1e-6
    # gap = INTER_TRUCK_GAP_MM 이상 — 첫 트럭 *적재함 끝* 과 두 번째 트럭 *적재함 시작* 사이
    # 단, 두 번째 트럭의 *캐빈* 이 적재함 앞쪽으로 더 튀어나가 있음
    # 캐빈 길이(2400) + 캐빈 간격(200) 만큼 빼야 적재함 사이 간격이 됨
    cab_offset = 2400.0 + 200.0
    actual_bed_gap = (min(xs1) - max(xs0)) + cab_offset
    assert actual_bed_gap == pytest.approx(INTER_TRUCK_GAP_MM + cab_offset)


# ════════════════════════════════════════════════════════════════════
# §6 STANDING 자세 — 단순 wall
# ════════════════════════════════════════════════════════════════════
def test_standing_wall_panel_correct_dims():
    """단순 wall 의 STANDING 자세 → width 가 위로 솟음. mesh 차원이 맞는지."""
    w = Panel(name="W1", kind="wall", width=3000, length=6000, thickness=150,
              wall_height=3000, beam_section=_SHS, column_section=_SHS,
              extra_weight_kg=600)
    # A-frame 트럭에 세움 적재 가정
    af = Truck(name="A프레임20t", truck_type="aframe",
               max_length=12000, max_width=3000, max_height=4500,
               max_weight=20000, vehicle_height_offset=700,
               curb_weight_kg=12000, active=True)
    placement = Placement(
        item=w, slot=PlacementSlot.FLOOR, posture=Posture.STANDING,
        truck_xyz=(0.0, 0.0, 0.0), parent_idx=None,
    )
    trip = Trip(trip_no=1, truck=af, items=[w], placements=[placement])
    fig = draw_loaded_3d_view([trip])
    # 정상 생성 + 화물 mesh 의 dz (높이) == wall.width (=3000)
    cargo_meshes = [d for d in fig.data
                    if isinstance(d, go.Mesh3d) and "W1" in (d.hovertext or "")]
    assert len(cargo_meshes) >= 1
    zs = cargo_meshes[0].z
    dz = max(zs) - min(zs)
    assert dz == pytest.approx(3000, abs=1.0)


# ════════════════════════════════════════════════════════════════════
# §7 역호환 — placements 빈 Trip
# ════════════════════════════════════════════════════════════════════
def test_legacy_trip_without_placements():
    """placements 가 비어있는 (구 패커 결과) Trip 도 items 로 그릴 수 있음."""
    m = _module()
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[])
    fig = draw_loaded_3d_view([trip])
    # 적재함 외곽선 + 차체 + 모듈 mesh + 모듈 외곽선 + 회차 라벨 모두 존재
    # legacy 경로에서도 trace 가 충분히 들어감
    assert len(fig.data) >= 5
    # 모듈 mesh 존재 (legacy 경로 동작 확인)
    module_meshes = [d for d in fig.data
                     if isinstance(d, go.Mesh3d) and "M1" in (d.name or "")]
    assert len(module_meshes) >= 1


# ════════════════════════════════════════════════════════════════════
# §8 실제 패킹 결과로 통합 검증
# ════════════════════════════════════════════════════════════════════
def test_draw_real_pack_result_no_exception():
    """실제 픽스처로 신규 패커 → 결과를 3D 시각화 — 예외 없이."""
    fx = generate_fixture("small")
    items = list(fx.modules) + list(fx.panels)
    pack = pack_one_seed(items, fx.trucks, fx.site, fx.spacing,
                        GREEDY_STRATEGIES[0], WEIGHT_SETS[0],
                        "fixed_per_trip", default_eco_options())
    fig = draw_loaded_3d_view(pack.trips, fx.spacing)
    assert isinstance(fig, go.Figure)
    # 회차마다 적어도 트럭 외곽선이 있음
    assert len(fig.data) >= pack.total_trips


# ════════════════════════════════════════════════════════════════════
# §9 Phase D — 트럭 차체 (캐빈 / 바퀴 / 차종 라벨)
# ════════════════════════════════════════════════════════════════════
def test_truck_bed_includes_cabin_wheels_label():
    """1 회차 모듈 1 + 트럭 — 적재함 외곽선 + 캐빈 mesh + 바퀴 6 개 + 차종 라벨 포함."""
    m = _module()
    placement = Placement(
        item=m, slot=PlacementSlot.FLOOR, posture=Posture.LYING,
        truck_xyz=(0, 0, 0), parent_idx=None,
    )
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[placement])
    fig = draw_loaded_3d_view([trip])
    # 캐빈 mesh + 외곽선 = 2 trace
    cabin_meshes = [d for d in fig.data
                    if isinstance(d, go.Mesh3d) and "캐빈" in (d.name or "")]
    assert len(cabin_meshes) == 1
    # 바퀴 8 개 (4 쌍 — 앞바퀴 1 + 적재함 하단 3)
    wheel_meshes = [d for d in fig.data
                    if isinstance(d, go.Mesh3d) and d.name == "바퀴"]
    assert len(wheel_meshes) == 8
    # 차종 라벨 — Scatter3d text 에 "저상" 포함
    label_traces = [d for d in fig.data
                    if isinstance(d, go.Scatter3d) and d.mode == "text"
                    and d.text and "저상" in str(d.text[0])]
    assert len(label_traces) >= 1


def test_truck_type_specific_color():
    """차종별 캐빈 색상 — 저상 파랑 / 광폭 초록 / A-frame 주황."""
    truck_types = [
        ("lowbed", "#1976D2"),
        ("extendable", "#388E3C"),
        ("aframe", "#E64A19"),
    ]
    for truck_type, expected_color in truck_types:
        truck = Truck(
            name=f"test_{truck_type}", truck_type=truck_type,
            max_length=12000, max_width=3000, max_height=4500,
            max_weight=20000, vehicle_height_offset=700,
            curb_weight_kg=12000, active=True,
        )
        # A-frame 은 단순 wall 만 받음 — 임의의 wall 패널로
        if truck_type == "aframe":
            w = Panel(name="W_af", kind="wall", width=3000, length=6000,
                      thickness=150, wall_height=3000,
                      beam_section=_SHS, column_section=_SHS,
                      extra_weight_kg=600)
            placement = Placement(item=w, slot=PlacementSlot.FLOOR,
                                  posture=Posture.STANDING,
                                  truck_xyz=(0, 0, 0), parent_idx=None)
        else:
            placement = Placement(item=_module(), slot=PlacementSlot.FLOOR,
                                  posture=Posture.LYING,
                                  truck_xyz=(0, 0, 0), parent_idx=None)
        trip = Trip(trip_no=1, truck=truck, items=[placement.item],
                    placements=[placement])
        fig = draw_loaded_3d_view([trip])
        cabin = [d for d in fig.data
                 if isinstance(d, go.Mesh3d) and "캐빈" in (d.name or "")]
        assert len(cabin) == 1
        assert cabin[0].color == expected_color, (
            f"{truck_type} 캐빈 색상이 {expected_color} 아님: {cabin[0].color}"
        )


# ════════════════════════════════════════════════════════════════════
# §10 Phase E — 카메라 프리셋 / 강조 / 오버레이
# ════════════════════════════════════════════════════════════════════
def test_camera_preset_buttons_present():
    """updatemenus 에 Iso/Top/Side 버튼 3 개."""
    m = _module()
    placement = Placement(item=m, slot=PlacementSlot.FLOOR,
                          posture=Posture.LYING,
                          truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[placement])
    fig = draw_loaded_3d_view([trip])
    assert fig.layout.updatemenus is not None
    assert len(fig.layout.updatemenus) >= 1
    btns = fig.layout.updatemenus[0].buttons
    labels = [b.label for b in btns]
    assert any("Iso" in lbl for lbl in labels)
    assert any("Top" in lbl for lbl in labels)
    assert any("Side" in lbl for lbl in labels)


def test_highlight_trip_no_adds_red_outline():
    """highlight_trip_no=1 → 빨간 굵은 외곽선 trace 추가."""
    m = _module()
    placement = Placement(item=m, slot=PlacementSlot.FLOOR,
                          posture=Posture.LYING,
                          truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[placement])
    fig_no_hl = draw_loaded_3d_view([trip], highlight_trip_no=None)
    fig_hl = draw_loaded_3d_view([trip], highlight_trip_no=1)
    # 강조 trace 추가됨
    assert len(fig_hl.data) > len(fig_no_hl.data)
    hl_traces = [d for d in fig_hl.data
                 if isinstance(d, go.Scatter3d)
                 and (d.line.color if d.line else "") == "#cc0000"]
    assert len(hl_traces) == 1


def test_highlight_nonexistent_trip_no_no_change():
    """존재하지 않는 trip_no → 강조 추가 없음."""
    m = _module()
    placement = Placement(item=m, slot=PlacementSlot.FLOOR,
                          posture=Posture.LYING,
                          truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[placement])
    fig_no_hl = draw_loaded_3d_view([trip], highlight_trip_no=None)
    fig_hl = draw_loaded_3d_view([trip], highlight_trip_no=999)
    assert len(fig_hl.data) == len(fig_no_hl.data)


def test_trip_costs_in_overlay():
    """trip_costs 전달 → 오버레이 텍스트에 비용 표시."""
    m = _module()
    placement = Placement(item=m, slot=PlacementSlot.FLOOR,
                          posture=Posture.LYING,
                          truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=_lowbed(), items=[m], placements=[placement])
    fig = draw_loaded_3d_view([trip], trip_costs={1: 600_000.0})
    overlay_traces = [d for d in fig.data
                      if isinstance(d, go.Scatter3d) and d.mode == "text"
                      and d.text and "#1" in str(d.text[0])
                      and "600,000" in str(d.text[0])]
    assert len(overlay_traces) >= 1


def test_aframe_truck_has_extra_frame():
    """A-frame 트럭 → 양옆 A 프레임 박스 2 개 추가."""
    w = Panel(name="W", kind="wall", width=3000, length=6000, thickness=150,
              wall_height=3000, beam_section=_SHS, column_section=_SHS,
              extra_weight_kg=600)
    af = Truck(name="A프레임20t", truck_type="aframe",
               max_length=12000, max_width=3000, max_height=4500,
               max_weight=20000, vehicle_height_offset=700,
               curb_weight_kg=12000, active=True)
    placement = Placement(item=w, slot=PlacementSlot.FLOOR,
                          posture=Posture.STANDING,
                          truck_xyz=(0, 0, 0), parent_idx=None)
    trip = Trip(trip_no=1, truck=af, items=[w], placements=[placement])
    fig = draw_loaded_3d_view([trip])
    aframes = [d for d in fig.data
               if isinstance(d, go.Mesh3d)
               and (d.name or "").startswith("A프레임 ")]
    assert len(aframes) == 2  # 좌우 두 개 ("A프레임 left", "A프레임 right")
