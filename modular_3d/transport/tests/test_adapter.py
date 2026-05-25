"""Phase 3 단위 테스트 — adapter.

[검증 시나리오]
1. 빈 씬 → modules·panels 빈 리스트
2. design_result None → TransportError
3. 모듈 1 개 → 변환 성공 + 단면 매핑
4. FloorPanel + merged_wall × 1 → kind='floor' + wall_segments 1개
5. FloorPanel + merged_wall × 2 → wall_segments 2개
6. 독립 StructWall → kind='wall'
7. Core/CoreSlab → excluded
8. to_transport_section 변환 정확성
9. 라벨 형식 ("a모듈-1F#1")
10. MidBeam/MidColumn → excluded + 부모 모듈 extra_weight 가산
11. Vertical3Module → 눕힌 매핑 (length=height, width=width, height=depth)
"""
from __future__ import annotations

import numpy as np
import pytest

from modular_3d.model.core import (
    Scene, instantiate, ComponentType,
)
from modular_3d.카탈로그.steel_sections import SHS_CATALOG, find_section_by_name
from modular_3d.transport.adapter import (
    TransportOptions, TransportError,
    build_transport_input, to_transport_section, build_comp_meta,
)


# ── stub AnalysisModel / DesignResult ───────────────────
class _StubMember:
    def __init__(self, mid, n1, n2, kind, role=""):
        self.id = mid
        self.n1 = n1
        self.n2 = n2
        self.kind = kind
        self.role = role


class _StubNode:
    def __init__(self, nid, coord):
        self.id = nid
        self.coord = np.array(coord, dtype=float)


class _StubAM:
    def __init__(self):
        self.nodes = {}
        self.members = {}
        self.comp_to_members = {}


class _StubGroup:
    def __init__(self, name, section):
        self.group_name = name
        self.section = section
        self.ng_flag = False


class _StubDR:
    def __init__(self, policy="3종"):
        self.policy = policy
        self.groups = {}
        self.member_to_group = {}


def _attach_module_to_model(am, dr, comp, cid, col_sec_name, beam_sec_name):
    """씬 모듈 cid 에 대해 stub 모델 + design_result 채우기."""
    col_sec = find_section_by_name(col_sec_name)
    beam_sec = find_section_by_name(beam_sec_name)
    # 노드 8 개, 부재 12 개 (4 기둥 + 8 보)
    base_nid = max(am.nodes.keys(), default=0) + 1
    base_mid = max(am.members.keys(), default=0) + 1
    corners = comp.get_world_corners()
    nids = []
    for c in corners:
        am.nodes[base_nid] = _StubNode(base_nid, c)
        nids.append(base_nid)
        base_nid += 1
    # 기둥 4 (꼭짓점 0->4, 1->5, 2->6, 3->7)
    mids = []
    for k in range(4):
        am.members[base_mid] = _StubMember(base_mid, nids[k], nids[k + 4], "column")
        dr.member_to_group[base_mid] = "columns"
        mids.append(base_mid)
        base_mid += 1
    # 보 8 (윗 4 + 아래 4 — 인접 노드 페어)
    bot_pairs = [(0, 1), (1, 2), (2, 3), (3, 0)]
    top_pairs = [(4, 5), (5, 6), (6, 7), (7, 4)]
    for k1, k2 in bot_pairs + top_pairs:
        am.members[base_mid] = _StubMember(base_mid, nids[k1], nids[k2], "beam")
        dr.member_to_group[base_mid] = "beams"
        mids.append(base_mid)
        base_mid += 1
    am.comp_to_members[cid] = mids
    if "columns" not in dr.groups:
        dr.groups["columns"] = _StubGroup("columns", col_sec)
    if "beams" not in dr.groups:
        dr.groups["beams"] = _StubGroup("beams", beam_sec)


def _attach_floor_panel(am, dr, comp, cid, beam_sec_name):
    beam_sec = find_section_by_name(beam_sec_name)
    base_nid = max(am.nodes.keys(), default=0) + 1
    base_mid = max(am.members.keys(), default=0) + 1
    corners = comp.get_world_corners()
    nids = []
    for c in corners[:4]:
        am.nodes[base_nid] = _StubNode(base_nid, c)
        nids.append(base_nid)
        base_nid += 1
    mids = []
    for k1, k2 in [(0, 1), (1, 2), (2, 3), (3, 0)]:
        am.members[base_mid] = _StubMember(base_mid, nids[k1], nids[k2], "beam")
        dr.member_to_group[base_mid] = "floor_panel_beam"
        mids.append(base_mid)
        base_mid += 1
    am.comp_to_members[cid] = mids
    if "floor_panel_beam" not in dr.groups:
        dr.groups["floor_panel_beam"] = _StubGroup("floor_panel_beam", beam_sec)


def _attach_struct_wall(am, dr, comp, cid, col_sec_name, beam_sec_name):
    col_sec = find_section_by_name(col_sec_name)
    beam_sec = find_section_by_name(beam_sec_name)
    base_nid = max(am.nodes.keys(), default=0) + 1
    base_mid = max(am.members.keys(), default=0) + 1
    corners = comp.get_world_corners()
    nids = []
    for c in corners:
        am.nodes[base_nid] = _StubNode(base_nid, c)
        nids.append(base_nid)
        base_nid += 1
    mids = []
    # 2 기둥 + 2 보 단순 가정
    for k in (0, 1):
        am.members[base_mid] = _StubMember(base_mid, nids[k], nids[k + 4], "column")
        dr.member_to_group[base_mid] = "wall_panel_column"
        mids.append(base_mid)
        base_mid += 1
    for k1, k2 in ((0, 1), (4, 5)):
        am.members[base_mid] = _StubMember(base_mid, nids[k1], nids[k2], "beam")
        dr.member_to_group[base_mid] = "wall_panel_beam"
        mids.append(base_mid)
        base_mid += 1
    am.comp_to_members[cid] = mids
    if "wall_panel_column" not in dr.groups:
        dr.groups["wall_panel_column"] = _StubGroup("wall_panel_column", col_sec)
    if "wall_panel_beam" not in dr.groups:
        dr.groups["wall_panel_beam"] = _StubGroup("wall_panel_beam", beam_sec)


# ── 픽스처 ──────────────────────────────────────────────
SHS_NAME = "SHS 200x200x8"
SHS_SMALL = "SHS 150x150x6"


def _make_scene_module(scene, x=0, y=0, w=3000, d=6000, h=3400):
    pos = np.array([float(x), float(y), 0.0])
    dims = {"width": w, "depth": d, "height": h}
    comp = instantiate(ComponentType.MODULE, pos, dims, 0, 0)
    cid = scene.add_component(comp)
    return cid, comp


# ── 시나리오 1: 빈 씬 ───────────────────────────────────
def test_empty_scene():
    scene = Scene()
    am = _StubAM()
    dr = _StubDR()
    # design_result.groups 비어있음 → TransportError
    with pytest.raises(TransportError):
        build_transport_input(scene, am, dr, "3종")


# ── 시나리오 2: design_result None → TransportError ──────
def test_none_design_result_raises():
    scene = Scene()
    am = _StubAM()
    with pytest.raises(TransportError):
        build_transport_input(scene, am, None, "3종")


def test_none_scene_raises():
    am = _StubAM()
    dr = _StubDR()
    with pytest.raises(TransportError):
        build_transport_input(None, am, dr, "3종")


# ── 시나리오 3: 모듈 1 개 변환 ─────────────────────────
def test_single_module_converts():
    scene = Scene()
    cid, comp = _make_scene_module(scene)
    am, dr = _StubAM(), _StubDR()
    _attach_module_to_model(am, dr, comp, cid, SHS_NAME, SHS_SMALL)

    ti = build_transport_input(scene, am, dr, "3종")
    assert len(ti.modules) == 1
    tm = ti.modules[0]
    assert tm.width == 3000
    assert tm.length == 6000
    assert tm.height == 3400
    assert tm.column_section.name == SHS_NAME
    assert tm.beam_section.name == SHS_SMALL
    # extra_weight 가 슬래브 자중 만큼 + 비내력벽 4 면 (단일 모듈이라 모두 외부)
    # 슬래브: 3*6 m² × 0.15 m × 2400 kg/m³ = 6480 kg
    assert tm.extra_weight_kg > 6000


# ── 시나리오 4: 단일 종속 (L자) ──────────────────────────
def test_floor_panel_with_one_merged_wall():
    scene = Scene()
    # FloorPanel + StructWall 동일 위치에 합체
    fp_pos = np.array([0.0, 0.0, 0.0])
    fp = instantiate(ComponentType.FLOOR_PANEL, fp_pos,
                     {"width": 3000.0, "depth": 6000.0, "height": 200.0}, 0, 0)
    fp_cid = scene.add_component(fp)
    wall_pos = np.array([0.0, 0.0, 0.0])
    wall = instantiate(ComponentType.STRUCT_WALL, wall_pos,
                       {"width": 3000.0, "depth": 200.0, "height": 3000.0}, 0, 0)
    wall_cid = scene.add_component(wall)
    scene.merge_wall_to_fp(wall_cid, fp_cid)

    am, dr = _StubAM(), _StubDR()
    _attach_floor_panel(am, dr, fp, fp_cid, SHS_SMALL)
    _attach_struct_wall(am, dr, wall, wall_cid, SHS_NAME, SHS_SMALL)

    ti = build_transport_input(scene, am, dr, "3종")
    # FloorPanel 1 개만 panel 로, StructWall 은 wall_segment 에 흡수
    assert len(ti.panels) == 1
    p = ti.panels[0]
    assert p.kind == "floor"
    assert len(p.wall_segments) == 1
    assert p.wall_segments[0].length_mm == 3000
    assert p.wall_segments[0].height_mm == 3000
    assert p.wall_segments[0].thickness_mm == 200


# ── 시나리오 5: 2면 종속 (ㄷ자) ────────────────────────────
def test_floor_panel_with_two_merged_walls():
    scene = Scene()
    fp = instantiate(ComponentType.FLOOR_PANEL, np.array([0.0, 0.0, 0.0]),
                     {"width": 3000.0, "depth": 6000.0, "height": 200.0}, 0, 0)
    fp_cid = scene.add_component(fp)
    w1 = instantiate(ComponentType.STRUCT_WALL, np.array([0.0, 0.0, 0.0]),
                     {"width": 3000.0, "depth": 200.0, "height": 3000.0}, 0, 0)
    w1_cid = scene.add_component(w1)
    w2 = instantiate(ComponentType.STRUCT_WALL, np.array([0.0, 6000.0, 0.0]),
                     {"width": 3000.0, "depth": 200.0, "height": 3000.0}, 0, 0)
    w2_cid = scene.add_component(w2)
    scene.merge_wall_to_fp(w1_cid, fp_cid)
    scene.merge_wall_to_fp(w2_cid, fp_cid)

    am, dr = _StubAM(), _StubDR()
    _attach_floor_panel(am, dr, fp, fp_cid, SHS_SMALL)
    _attach_struct_wall(am, dr, w1, w1_cid, SHS_NAME, SHS_SMALL)
    _attach_struct_wall(am, dr, w2, w2_cid, SHS_NAME, SHS_SMALL)

    ti = build_transport_input(scene, am, dr, "3종")
    assert len(ti.panels) == 1
    p = ti.panels[0]
    assert len(p.wall_segments) == 2


# ── 시나리오 6: 독립 StructWall ─────────────────────────
def test_independent_struct_wall():
    scene = Scene()
    wall = instantiate(ComponentType.STRUCT_WALL, np.array([0.0, 0.0, 0.0]),
                       {"width": 3000.0, "depth": 200.0, "height": 3000.0}, 0, 0)
    wall_cid = scene.add_component(wall)
    am, dr = _StubAM(), _StubDR()
    _attach_struct_wall(am, dr, wall, wall_cid, SHS_NAME, SHS_SMALL)
    ti = build_transport_input(scene, am, dr, "3종")
    assert len(ti.panels) == 1
    p = ti.panels[0]
    assert p.kind == "wall"


# ── 시나리오 7: Core/CoreSlab → excluded ───────────────
def test_core_excluded():
    scene = Scene()
    core = instantiate(ComponentType.CORE, np.array([0.0, 0.0, 0.0]),
                       {"width": 3000.0, "depth": 300.0, "height": 3400.0}, 0, 0)
    core_cid = scene.add_component(core)
    am, dr = _StubAM(), _StubDR()
    am.comp_to_members[core_cid] = []   # 코어는 모델에서 어차피 별도 처리
    # dr 에 더미 그룹 1 개라도 있어야 (precondition)
    dr.groups["columns"] = _StubGroup("columns", find_section_by_name(SHS_NAME))
    ti = build_transport_input(scene, am, dr, "3종")
    assert len(ti.modules) == 0
    assert len(ti.panels) == 0
    assert any(ex.cid == core_cid for ex in ti.excluded)


# ── 시나리오 8: to_transport_section ───────────────────
def test_to_transport_section_roundtrip():
    shs = find_section_by_name(SHS_NAME)
    t = to_transport_section(shs)
    assert t.name == SHS_NAME
    assert t.section_type == "SHS"
    assert t.width == shs.w
    assert t.thickness == shs.t
    assert t.weight_per_m == pytest.approx(shs.weight_per_m_kg)


# ── 시나리오 9: 라벨 형식 ───────────────────────────────
def test_label_format():
    scene = Scene()
    cid, comp = _make_scene_module(scene, 0, 0)
    am, dr = _StubAM(), _StubDR()
    _attach_module_to_model(am, dr, comp, cid, SHS_NAME, SHS_SMALL)
    ti = build_transport_input(scene, am, dr, "3종")
    name = ti.modules[0].name
    # "a모듈-1F#cid" 형식
    assert "모듈" in name
    assert f"#{cid}" in name
    assert "1F" in name
    # source_index 역참조
    assert cid in ti.source_index[name]


# ── 시나리오 11: Vertical3Module 눕힌 매핑 ───────────────
def test_vertical3_lying_mapping():
    scene = Scene()
    v3 = instantiate(ComponentType.VERTICAL_MODULE, np.array([0.0, 0.0, 0.0]),
                     {"width": 3400.0, "depth": 3400.0, "height": 10240.0}, 0, 0)
    v3_cid = scene.add_component(v3)
    am, dr = _StubAM(), _StubDR()
    # v3 모듈에 임의로 기둥/보 mid 부여 (단순 stub — 노드/부재 1세트씩만)
    _attach_module_to_model(am, dr, v3, v3_cid, SHS_NAME, SHS_SMALL)
    ti = build_transport_input(scene, am, dr, "3종",
                                options=TransportOptions(treat_v3_module_as_lying=True))
    assert len(ti.modules) == 1
    tm = ti.modules[0]
    # 눕히기: length=원래 height(10240), width=원래 width(3400), height=원래 depth(3400)
    assert tm.length == 10240
    assert tm.width == 3400
    assert tm.height == 3400


# ── build_comp_meta 라벨 단위 ───────────────────────────
def test_build_comp_meta_alpha_label():
    scene = Scene()
    cid1, c1 = _make_scene_module(scene, 0, 0)
    cid2, c2 = _make_scene_module(scene, 6020, 0)  # 다른 xy
    am, dr = _StubAM(), _StubDR()
    _attach_module_to_model(am, dr, c1, cid1, SHS_NAME, SHS_SMALL)
    _attach_module_to_model(am, dr, c2, cid2, SHS_NAME, SHS_SMALL)
    meta = build_comp_meta(scene, am)
    # cid1 = 'a', cid2 = 'b' (xy 정렬 기준)
    labels = sorted({meta[c][0] for c in (cid1, cid2)})
    assert labels == ["a", "b"]
