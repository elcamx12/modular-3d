"""접합부 오버라이드 — 직렬화·매칭·빌드 반영(다층 자동) 테스트.

검증 범위(1단계 — 기존 접합 제거/변경):
  1. JointOverride 직렬화 라운드트립.
  2. match_override 양방향 + tol.
  3. Scene.set_joint_override 교체(같은 접합 최신 우선).
  4. scene_io 라운드트립.
  5. 빌드 통합 — remove 가 같은 평면 위치(xy)의 모든 층 수직 접합 제거(다층 자동).
  6. 빌드 통합 — rigid 가 매칭 접합 dofs 를 (1..6)로, 다른 접합은 핀 유지.
"""
import numpy as np
import openseespy.opensees as ops

from modular_3d.model.joint_override import (
    JointOverride, match_override, same_joint, PIN_DOFS, RIGID_DOFS,
)
from modular_3d.model.core import Scene, ComponentType
from modular_3d.model.multi_floor import create_multi_floor_group
from modular_3d.analysis.topology import build_analysis_model, AnalysisModel
from modular_3d.analysis.ops_builder import build_ops_model, OpsModel
from modular_3d.analysis.joint_rules import apply_added_joints
from modular_3d.analysis.model_spec import ModelSpec, NodeRec
from modular_3d.io.scene_io import scene_to_state_dict, state_dict_to_scene


# ── 단위: 직렬화 ──────────────────────────────────────────────

def test_serialize_roundtrip():
    ov = JointOverride(kind='rigid', a_xy=(100.0, 200.0), b_xy=(100.0, 5000.0),
                       a_group=3, b_group=3, z_a=0.0, z_b=3420.0,
                       add_dofs=RIGID_DOFS)
    d = ov.to_dict()
    ov2 = JointOverride.from_dict(d)
    assert ov2.kind == 'rigid'
    assert ov2.a_xy == (100.0, 200.0)
    assert ov2.b_xy == (100.0, 5000.0)
    assert ov2.a_group == 3 and ov2.b_group == 3
    assert ov2.add_dofs == RIGID_DOFS


def test_effective_dofs():
    assert JointOverride('remove', (0, 0), (0, 0)).effective_dofs() is None
    assert JointOverride('rigid', (0, 0), (0, 0)).effective_dofs() == RIGID_DOFS
    assert JointOverride('pin', (0, 0), (0, 0)).effective_dofs() == PIN_DOFS


# ── 단위: 매칭 ────────────────────────────────────────────────

def test_match_bidirectional_and_tol():
    ov = JointOverride('rigid', a_xy=(0.0, 0.0), b_xy=(3000.0, 0.0))
    # 정방향
    assert match_override([ov], (0.0, 0.0), (3000.0, 0.0)) is ov
    # 역방향 (master/slave 순서 무관)
    assert match_override([ov], (3000.0, 0.0), (0.0, 0.0)) is ov
    # tol 이내 흔들림 흡수
    assert match_override([ov], (10.0, -5.0), (2990.0, 3.0)) is ov
    # tol 밖 → 매칭 없음
    assert match_override([ov], (0.0, 0.0), (3200.0, 0.0)) is None
    # add 는 기존-접합 매칭 대상 아님
    add = JointOverride('add', (0.0, 0.0), (3000.0, 0.0))
    assert match_override([add], (0.0, 0.0), (3000.0, 0.0)) is None


def test_match_latest_wins():
    a = JointOverride('rigid', (0.0, 0.0), (3000.0, 0.0))
    b = JointOverride('pin', (0.0, 0.0), (3000.0, 0.0))
    assert match_override([a, b], (0.0, 0.0), (3000.0, 0.0)) is b


def test_match_by_rule_id():
    """같은 평면 위치(수직 겹침)에서 rule_id 로 종류를 구분해 매칭."""
    a = JointOverride('rigid', (0.0, 0.0), (0.0, 0.0), rule_id='R02_mod_mod_v')
    b = JointOverride('pin', (0.0, 0.0), (0.0, 0.0), rule_id='R03_panel_mod')
    # 등록 중인 종류에 맞는 오버라이드만 매칭.
    assert match_override([a, b], (0.0, 0.0), (0.0, 0.0),
                          rule_id='R02_mod_mod_v') is a
    assert match_override([a, b], (0.0, 0.0), (0.0, 0.0),
                          rule_id='R03_panel_mod') is b
    # rule_id 빈 오버라이드는 종류 무관(하위호환).
    c = JointOverride('remove', (0.0, 0.0), (0.0, 0.0))
    assert match_override([c], (0.0, 0.0), (0.0, 0.0),
                          rule_id='R02_mod_mod_v') is c


def test_set_override_keeps_different_rule():
    """같은 위치라도 rule_id 가 다르면 둘 다 유지, 같으면 교체."""
    scene = Scene()
    scene.set_joint_override(JointOverride('rigid', (0.0, 0.0), (0.0, 0.0),
                                           rule_id='R02_mod_mod_v'))
    scene.set_joint_override(JointOverride('pin', (0.0, 0.0), (0.0, 0.0),
                                           rule_id='R03_panel_mod'))
    assert len(scene.joint_overrides) == 2   # 다른 종류 → 둘 다
    # 같은 종류 재변경 → 교체.
    scene.set_joint_override(JointOverride('remove', (0.0, 0.0), (0.0, 0.0),
                                           rule_id='R02_mod_mod_v'))
    r02 = [o for o in scene.joint_overrides if o.rule_id == 'R02_mod_mod_v']
    assert len(r02) == 1 and r02[0].kind == 'remove'
    assert len(scene.joint_overrides) == 2


def test_match_override_single_layer_z():
    """single_layer 면 같은 xy 라도 z(층)가 맞아야 매칭('이 층만')."""
    a = JointOverride('remove', (0.0, 0.0), (0.0, 0.0),
                      z_a=0.0, z_b=100.0, single_layer=True)
    # 그 층(z 0~100) 노드쌍 → 매칭.
    assert match_override([a], (0.0, 0.0), (0.0, 0.0),
                          ma_z=0.0, mb_z=100.0) is a
    # 다른 층(z 3000~3100) → 매칭 안 됨.
    assert match_override([a], (0.0, 0.0), (0.0, 0.0),
                          ma_z=3000.0, mb_z=3100.0) is None
    # single_layer=False 면 z 무관(모든 층).
    b = JointOverride('remove', (0.0, 0.0), (0.0, 0.0),
                      z_a=0.0, z_b=100.0, single_layer=False)
    assert match_override([b], (0.0, 0.0), (0.0, 0.0),
                          ma_z=3000.0, mb_z=3100.0) is b


def test_set_override_single_layer_keeps_other_floor():
    """single_layer 변경은 층(z)별로 별개 — 다른 층 변경을 지우지 않는다."""
    scene = Scene()
    scene.set_joint_override(JointOverride('remove', (0.0, 0.0), (0.0, 0.0),
                                           z_a=0.0, z_b=100.0,
                                           single_layer=True, rule_id='R02'))
    scene.set_joint_override(JointOverride('remove', (0.0, 0.0), (0.0, 0.0),
                                           z_a=3000.0, z_b=3100.0,
                                           single_layer=True, rule_id='R02'))
    assert len(scene.joint_overrides) == 2   # 다른 층 → 둘 다 유지
    # 같은 층 재변경 → 교체.
    scene.set_joint_override(JointOverride('rigid', (0.0, 0.0), (0.0, 0.0),
                                           z_a=0.0, z_b=100.0,
                                           single_layer=True, rule_id='R02'))
    assert len(scene.joint_overrides) == 2
    z0 = [o for o in scene.joint_overrides if abs(o.z_a) < 1]
    assert len(z0) == 1 and z0[0].kind == 'rigid'


def test_same_joint():
    ov = JointOverride('remove', (0.0, 0.0), (3000.0, 0.0))
    assert same_joint(ov, (0.0, 0.0), (3000.0, 0.0))
    assert same_joint(ov, (3000.0, 0.0), (0.0, 0.0))   # 양방향
    assert not same_joint(ov, (0.0, 0.0), (9999.0, 0.0))


# ── 단위: Scene 헬퍼 ──────────────────────────────────────────

def test_scene_set_override_replaces_same_joint():
    scene = Scene()
    scene.set_joint_override(JointOverride('rigid', (0.0, 0.0), (0.0, 0.0)))
    scene.set_joint_override(JointOverride('pin', (0.0, 0.0), (0.0, 0.0)))
    # 같은 접합이므로 교체 — 1개, 최신(pin)만.
    assert len(scene.joint_overrides) == 1
    assert scene.joint_overrides[0].kind == 'pin'
    # 다른 접합은 누적.
    scene.set_joint_override(JointOverride('remove', (5000.0, 0.0), (5000.0, 0.0)))
    assert len(scene.joint_overrides) == 2


def test_scene_clear_override_at():
    scene = Scene()
    scene.set_joint_override(JointOverride('rigid', (0.0, 0.0), (0.0, 0.0)))
    assert scene.clear_joint_override_at((0.0, 0.0), (0.0, 0.0))
    assert len(scene.joint_overrides) == 0
    # 없는 접합 제거는 False.
    assert not scene.clear_joint_override_at((1.0, 1.0), (2.0, 2.0))


# ── 통합: scene_io 라운드트립 ─────────────────────────────────

def test_scene_io_roundtrip():
    scene = Scene()
    scene.set_joint_override(JointOverride('rigid', (100.0, 200.0), (100.0, 200.0),
                                           a_group=1, b_group=2))
    scene.set_joint_override(JointOverride('remove', (300.0, 0.0), (3300.0, 0.0)))
    data = scene_to_state_dict(scene, n_floors=1)
    assert 'joint_overrides' in data
    assert len(data['joint_overrides']) == 2
    scene2, _ = state_dict_to_scene(data)
    assert len(scene2.joint_overrides) == 2
    kinds = {o.kind for o in scene2.joint_overrides}
    assert kinds == {'rigid', 'remove'}


# ── 통합: 빌드 반영 (다층 자동) ───────────────────────────────

def _make_stacked_module(n_floors: int) -> Scene:
    """수직 n층 모듈 1개 그룹 씬 — R02 수직 적층 접합이 코너마다 생긴다."""
    scene = Scene()
    dims = {'width': 3400.0, 'depth': 6000.0, 'height': 3400.0}
    create_multi_floor_group(
        scene, ComponentType.MODULE, np.array([0.0, 0.0, 0.0]), dims,
        rotation=0, anchor=0, n_floors=n_floors)
    return scene


def _vstack_pairs(om):
    """vstack 결합의 (xy_key, dofs) 목록. 수직이라 master/slave xy 동일."""
    out = []
    for ed in om.spec.equal_dofs:
        if ed.kind != 'vstack':
            continue
        cm = om.node_tags.get(ed.master)
        if cm is None:
            continue
        xy = (round(float(cm[0]), 1), round(float(cm[1]), 1))
        out.append((xy, tuple(ed.dofs)))
    return out


def test_build_baseline_has_vstack():
    scene = _make_stacked_module(3)
    om = build_ops_model(build_analysis_model(scene), scene)
    pairs = _vstack_pairs(om)
    # 코너 4개 × (1-2층, 2-3층) = 8 수직 결합.
    assert len(pairs) == 8
    # 기본은 모두 핀.
    assert all(d == PIN_DOFS for _, d in pairs)


def test_build_remove_applies_all_floors():
    scene = _make_stacked_module(3)
    om0 = build_ops_model(build_analysis_model(scene), scene)
    pairs0 = _vstack_pairs(om0)
    target_xy = pairs0[0][0]
    n_before = sum(1 for xy, _ in pairs0 if xy == target_xy)
    assert n_before == 2   # 한 코너 = 1-2층 + 2-3층

    scene.set_joint_override(JointOverride('remove', target_xy, target_xy))
    om1 = build_ops_model(build_analysis_model(scene), scene)
    pairs1 = _vstack_pairs(om1)
    n_after = sum(1 for xy, _ in pairs1 if xy == target_xy)
    assert n_after == 0   # 모든 층에서 제거(다층 자동)
    assert len(pairs1) == len(pairs0) - n_before


def test_build_rigid_applies_all_floors():
    scene = _make_stacked_module(3)
    om0 = build_ops_model(build_analysis_model(scene), scene)
    target_xy = _vstack_pairs(om0)[0][0]

    scene.set_joint_override(JointOverride('rigid', target_xy, target_xy))
    om1 = build_ops_model(build_analysis_model(scene), scene)
    for xy, dofs in _vstack_pairs(om1):
        if xy == target_xy:
            assert dofs == RIGID_DOFS   # 매칭 접합 → 강접(모든 층)
        else:
            assert dofs == PIN_DOFS     # 그 외 → 핀 유지


def test_build_no_override_unchanged():
    """오버라이드 없으면 결합 개수·dofs 가 회귀 없이 동일해야 한다."""
    scene = _make_stacked_module(2)
    om = build_ops_model(build_analysis_model(scene), scene)
    pairs = _vstack_pairs(om)
    assert len(pairs) == 4          # 코너 4개 × (1-2층) = 4
    assert all(d == PIN_DOFS for _, d in pairs)


# ── 통합: 신규 접합 추가 (apply_added_joints) ─────────────────

def _fake_om(node_coords: dict, comp_of: dict):
    """ops 노드만 등록한 최소 om — apply_added_joints 단위 검증용.

    node_coords: {tag: (x,y,z)}, comp_of: {tag: source_comp_id}.
    """
    ops.wipe()
    ops.model('basic', '-ndm', 3, '-ndf', 6)
    om = OpsModel(analysis_model=AnalysisModel())
    om.spec = ModelSpec()
    for tag, (x, y, z) in node_coords.items():
        ops.node(tag, float(x), float(y), float(z))
        coord = np.array([x, y, z], dtype=float)
        om.node_tags[tag] = coord
        om.spec.nodes.append(
            NodeRec(tag, coord, source_comp_id=comp_of.get(tag, 0)))
    om.spec.refresh_index()
    return om


def _user_add_pairs(om):
    return [(ed.master, ed.slave, tuple(ed.dofs))
            for ed in om.spec.equal_dofs if ed.kind == 'user_add']


def test_add_serialize_roundtrip():
    ov = JointOverride('add', a_xy=(0.0, 0.0), b_xy=(0.0, 0.0),
                       z_a=0.0, z_b=100.0, add_dofs=RIGID_DOFS)
    ov2 = JointOverride.from_dict(ov.to_dict())
    assert ov2.kind == 'add'
    assert ov2.z_b == 100.0
    assert ov2.add_dofs == RIGID_DOFS


def test_add_vertical_single():
    om = _fake_om({1: (0, 0, 0), 2: (0, 0, 100)}, {1: 10, 2: 20})
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (0.0, 0.0),
                                        z_a=0.0, z_b=100.0)]
    n = apply_added_joints(om)
    assert n == 1
    pairs = _user_add_pairs(om)
    assert len(pairs) == 1
    assert set(pairs[0][:2]) == {1, 2}
    assert pairs[0][2] == PIN_DOFS


def test_add_horizontal_rigid():
    om = _fake_om({1: (0, 0, 0), 2: (100, 0, 0)}, {1: 10, 2: 20})
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (100.0, 0.0),
                                        z_a=0.0, z_b=0.0, add_dofs=RIGID_DOFS)]
    n = apply_added_joints(om)
    assert n == 1
    pairs = _user_add_pairs(om)
    assert len(pairs) == 1
    assert pairs[0][2] == RIGID_DOFS   # 강접으로 추가


def test_add_single_layer_no_multifloor():
    """single_layer=True 면 다른 층에 복제하지 않고 클릭한 층만 추가한다."""
    om = _fake_om({1: (0, 0, 0), 2: (0, 0, 100),
                   3: (0, 0, 200), 4: (0, 0, 300)},
                  {1: 10, 2: 20, 3: 30, 4: 40})
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (0.0, 0.0),
                                        z_a=0.0, z_b=100.0, single_layer=True)]
    apply_added_joints(om)
    pairs = [(ed.master, ed.slave) for ed in om.spec.equal_dofs
             if ed.kind == 'user_add']
    assert len(pairs) == 1                 # 다층 복제 안 함 — 1쌍만
    assert set(pairs[0]) == {1, 2}


def test_add_right_angle_makes_intermediate_node():
    """직각(ㄴ자) add 는 중간 노드(N1)를 거쳐 두 결합(수직+수평)을 만든다."""
    # A(0,0,300) 위 / B(200,0,0) 아래 — 평면·높이 둘 다 다름.
    om = _fake_om({1: (0, 0, 300), 2: (200, 0, 0)}, {1: 10, 2: 20})
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (200.0, 0.0),
                                        z_a=300.0, z_b=0.0,
                                        single_layer=True, right_angle=True)]
    apply_added_joints(om)
    adds = [ed for ed in om.spec.equal_dofs if ed.kind == 'user_add']
    assert len(adds) == 2                  # 수직 + 수평 두 결합
    # 중간 노드(panel_z_route role) = 위 점 평면(0,0) + 아래 점 높이(0).
    n1 = [nr for nr in om.spec.nodes if nr.role == 'panel_z_route']
    assert len(n1) == 1
    c = n1[0].coord
    assert abs(c[0] - 0.0) < 1 and abs(c[1] - 0.0) < 1 and abs(c[2] - 0.0) < 1


def test_right_angle_both_links_when_lower_constrained():
    """아래 노드가 이미 종속(자동결합 slave)이어도 직각접합 두 결합이 모두 생성."""
    om = _fake_om({1: (0, 0, 300), 2: (200, 0, 0)}, {1: 10, 2: 20})
    om.constrained_node_ids.add(2)   # 아래 점이 이미 종속(R02 등 slave) 시뮬
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (200.0, 0.0),
                                        z_a=300.0, z_b=0.0,
                                        single_layer=True, right_angle=True)]
    apply_added_joints(om)
    adds = [ed for ed in om.spec.equal_dofs if ed.kind == 'user_add']
    assert len(adds) == 2   # 수직 + 수평 둘 다 (force 등록)


def test_add_rule_id_pin_vs_rigid():
    """핀 추가 → R10, 강접 추가 → R11 로 rule_id 가 구분된다."""
    from modular_3d.analysis.joint_rules import (
        RULE_ID_USER_PIN, RULE_ID_USER_RIGID)
    om = _fake_om({1: (0, 0, 0), 2: (0, 0, 100)}, {1: 10, 2: 20})
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (0.0, 0.0),
                                        z_a=0.0, z_b=100.0, add_dofs=PIN_DOFS)]
    apply_added_joints(om)
    rids = [ed.rule_id for ed in om.spec.equal_dofs if ed.kind == 'user_add']
    assert rids and all(r == RULE_ID_USER_PIN for r in rids)

    om2 = _fake_om({1: (0, 0, 0), 2: (0, 0, 100)}, {1: 10, 2: 20})
    om2.joint_overrides = [JointOverride('add', (0.0, 0.0), (0.0, 0.0),
                                         z_a=0.0, z_b=100.0, add_dofs=RIGID_DOFS)]
    apply_added_joints(om2)
    rids2 = [ed.rule_id for ed in om2.spec.equal_dofs if ed.kind == 'user_add']
    assert rids2 and all(r == RULE_ID_USER_RIGID for r in rids2)


def test_add_replicates_all_floors():
    """수직 add(0→100)가 모든 인접 층 노드쌍에 복제되어야 한다."""
    om = _fake_om(
        {1: (0, 0, 0), 2: (0, 0, 100), 3: (0, 0, 200), 4: (0, 0, 300)},
        {1: 10, 2: 20, 3: 30, 4: 40})
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (0.0, 0.0),
                                        z_a=0.0, z_b=100.0)]
    n = apply_added_joints(om)
    # (1,2) (2,3) (3,4) — 인접 층마다 한 쌍.
    assert n == 3
    pairs = {frozenset(p[:2]) for p in _user_add_pairs(om)}
    assert pairs == {frozenset({1, 2}), frozenset({2, 3}), frozenset({3, 4})}


def test_anchor_zs_includes_split_layers():
    """분할되어 member_to_ele_tag 에서 빠진 보의 층도 z 가 수집돼야 한다
    (am.members 원본 기반 — 1층 바닥·최상층 누락 수정 검증)."""
    from modular_3d.analysis.joint_rules import _anchor_zs
    from modular_3d.analysis.topology import AnalysisNode, AnalysisMember
    om = _fake_om({}, {})   # node_tags 비움 — 보로만 z 수집되는지 본다.
    am = om.analysis_model
    am.nodes[1] = AnalysisNode(id=1, coord=np.array([0.0, 0.0, 0.0]), source_comp_id=0)
    am.nodes[2] = AnalysisNode(id=2, coord=np.array([200.0, 0.0, 0.0]), source_comp_id=0)
    am.nodes[3] = AnalysisNode(id=3, coord=np.array([0.0, 0.0, 3420.0]), source_comp_id=0)
    am.nodes[4] = AnalysisNode(id=4, coord=np.array([200.0, 0.0, 3420.0]), source_comp_id=0)
    am.members[1] = AnalysisMember(id=1, n1=1, n2=2, kind='beam', role='b',
                                   section_w=200.0, section_h=200.0, section_t=8.0)
    am.members[2] = AnalysisMember(id=2, n1=3, n2=4, kind='beam', role='b',
                                   section_w=200.0, section_h=200.0, section_t=8.0)
    # member_to_ele_tag 는 비워둔다(= 두 보 모두 분할되어 빠진 상태 시뮬레이션).
    zs = _anchor_zs(om, 100.0, 0.0, 50.0)
    assert 0.0 in zs and 3420.0 in zs   # 두 층 모두 수집


def test_can_anchor_layer_gate():
    """_can_anchor 는 그 평면 위치·층에 노드/보가 있을 때만 True.
    (한쪽만 가능한 층을 걸러 접합 실패 층에 고아 분할 노드가 안 생기게 하는 관문.)"""
    from modular_3d.analysis.joint_rules import _can_anchor
    from modular_3d.analysis.topology import AnalysisNode, AnalysisMember
    om = _fake_om({}, {})
    am = om.analysis_model
    am.nodes[1] = AnalysisNode(id=1, coord=np.array([0.0, 0.0, 0.0]), source_comp_id=0)
    am.nodes[2] = AnalysisNode(id=2, coord=np.array([3000.0, 0.0, 0.0]), source_comp_id=0)
    om.node_tags[1] = np.array([0.0, 0.0, 0.0])
    om.node_tags[2] = np.array([3000.0, 0.0, 0.0])
    om.beam_elements[101] = (1, 2, 'beam', 'b')
    # 바닥(z0) 보 위 중간 → 가능.
    assert _can_anchor(om, 1500.0, 0.0, 0.0)
    # 같은 xy 천장(z3000)에는 보·노드가 없음 → 불가(그 층은 건너뜀).
    assert not _can_anchor(om, 1500.0, 0.0, 3000.0)


def test_on_edge_serialize_roundtrip():
    """선-분할 플래그(a_on_edge/b_on_edge)가 직렬화 왕복에서 보존된다."""
    ov = JointOverride('add', a_xy=(0.0, 0.0), b_xy=(200.0, 0.0),
                       z_a=0.0, z_b=0.0, a_on_edge=False, b_on_edge=True)
    ov2 = JointOverride.from_dict(ov.to_dict())
    assert ov2.a_on_edge is False
    assert ov2.b_on_edge is True


def test_can_anchor_on_edge_skips_node():
    """on_edge=True 면 노드 스냅을 안 쓰고 보 분할만 본다 —
    노드만 있고 보가 없으면 불가, 보가 있으면 가능."""
    from modular_3d.analysis.joint_rules import _can_anchor
    om = _fake_om({1: (1500.0, 0.0, 0.0)}, {1: 10})   # 노드만, 보 없음
    # 노드 스냅 허용(기본) → 그 자리에서 가능.
    assert _can_anchor(om, 1500.0, 0.0, 0.0, on_edge=False)
    # 선-분할 전용 → 보가 없으니 불가(노드로 끌려가지 않음).
    assert not _can_anchor(om, 1500.0, 0.0, 0.0, on_edge=True)
    # 보를 깔면 on_edge 로도 가능.
    om.node_tags[2] = np.array([0.0, 0.0, 0.0])
    om.node_tags[3] = np.array([3000.0, 0.0, 0.0])
    om.beam_elements[101] = (2, 3, 'beam', 'b')
    assert _can_anchor(om, 1500.0, 0.0, 0.0, on_edge=True)


def test_add_on_edge_creates_split_node_not_snap():
    """선 위 점(b_on_edge=True)으로 추가하면 가까운 기존 노드로 끌려가지 않고
    그 자리에 보를 분할해 새 노드를 만들어 접합한다(스냅 의미 보존)."""
    from modular_3d.analysis.joint_rules import SPLIT_NODE_BASE_OFFSET
    scene = _make_stacked_module(1)
    om = build_ops_model(build_analysis_model(scene), scene)
    am = om.analysis_model
    # 분할 가능한 보 하나를 골라 중앙 점(끝점에서 먼 mid-span)을 둘째 점으로.
    beams = [(mid, m) for mid, m in am.members.items()
             if m.kind == 'beam' and not getattr(m, 'is_split_sub', False)]
    mid, m = beams[0]
    c1 = np.asarray(am.nodes[m.n1].coord, dtype=float)
    c2 = np.asarray(am.nodes[m.n2].coord, dtype=float)
    midpt = 0.5 * (c1 + c2)
    # 첫 점은 그 보의 한 끝 코너(노드 스냅), 둘째 점은 mid-span(선 분할).
    a = tuple(float(v) for v in c1)
    b = tuple(float(v) for v in midpt)
    om.joint_overrides = [JointOverride(
        'add', a_xy=(a[0], a[1]), b_xy=(b[0], b[1]),
        z_a=a[2], z_b=b[2], single_layer=True,
        a_on_edge=False, b_on_edge=True)]
    n = apply_added_joints(om)
    assert n == 1
    pair = _user_add_pairs(om)[0]
    # 둘째 끝점은 SPLIT 영역의 새 노드여야 한다(기존 코너 노드가 아님).
    split_tag = [t for t in pair[:2] if t >= SPLIT_NODE_BASE_OFFSET]
    assert len(split_tag) == 1


def test_remove_dangling_bridge_node():
    """직각접합 제거로 링크가 모두 사라진 허공 가교노드(N1)는 정리되고,
    아직 링크가 남은 가교노드는 유지된다."""
    from modular_3d.analysis.joint_rules import remove_dangling_bridge_nodes
    from modular_3d.analysis.model_spec import NodeRec, EqualDofRec
    om = _fake_om({1: (0, 0, 300), 2: (0, 0, 0)}, {1: 10, 2: 20})
    # 허공 가교노드 N1a — 링크·부재 없음.
    ops.node(100, 0.0, 0.0, 0.0)
    om.node_tags[100] = np.array([0.0, 0.0, 0.0])
    om.spec.nodes.append(NodeRec(tag=100, coord=np.array([0.0, 0.0, 0.0]),
                                 role='panel_z_route', source_comp_id=0))
    # 살아있는 가교노드 N1b — 링크 있음.
    ops.node(101, 50.0, 0.0, 0.0)
    om.node_tags[101] = np.array([50.0, 0.0, 0.0])
    om.spec.nodes.append(NodeRec(tag=101, coord=np.array([50.0, 0.0, 0.0]),
                                 role='panel_z_route', source_comp_id=0))
    om.spec.equal_dofs.append(EqualDofRec(
        master=1, slave=101, dofs=(1, 2, 3), kind='x', rule_id='R03_panel_mod'))
    removed = remove_dangling_bridge_nodes(om)
    assert removed == 1
    tags = {nr.tag for nr in om.spec.nodes}
    assert 100 not in tags        # 허공 노드 제거됨
    assert 101 in tags            # 링크 있는 가교노드 유지
    assert 100 not in om.node_tags


def test_candidate_joint_points():
    """첫 점에서 축정렬·400mm 이내 다른 부재 위 후보가 검출된다."""
    from modular_3d.analysis.joint_rules import candidate_joint_points
    scene = _make_stacked_module(1)
    om = build_ops_model(build_analysis_model(scene), scene)
    # 한 보의 끝점(코너) 근처를 첫 점으로.
    beams = [(n1, n2) for (n1, n2, k, r) in om.beam_elements.values()
             if k == 'beam']
    n1, n2 = beams[0]
    p = tuple(float(v) for v in om.node_tags[n1])
    cands = candidate_joint_points(om, p, exclude_comp=999999)  # 모든 부재 허용
    # 코너 근처에는 축정렬로 가까운 다른 보 위 점이 존재해야 한다.
    assert isinstance(cands, list)
    for c in cands:
        assert len(c) == 4   # (x, y, z, comp)
        import math
        d = math.dist(p, (c[0], c[1], c[2]))
        assert d <= 400.0 + 1.0
        # 축정렬: 한 축만 차이.
        difs = [abs(p[0] - c[0]) > 1, abs(p[1] - c[1]) > 1, abs(p[2] - c[2]) > 1]
        assert sum(difs) <= 1


def test_candidate_right_angle():
    """직각 모드 후보 = 첫 점에서 수직 내린 다른 높이 보 위, 평면으로 떨어진 점."""
    from modular_3d.analysis.joint_rules import candidate_joint_points
    from modular_3d.analysis.topology import AnalysisNode, AnalysisMember
    om = _fake_om({}, {})
    am = om.analysis_model
    # z=0 높이의 다른 부재 보 (평면상 y=200 으로 떨어져 있음).
    am.nodes[1] = AnalysisNode(id=1, coord=np.array([-100.0, 200.0, 0.0]), source_comp_id=2)
    am.nodes[2] = AnalysisNode(id=2, coord=np.array([500.0, 200.0, 0.0]), source_comp_id=2)
    am.members[1] = AnalysisMember(id=1, n1=1, n2=2, kind='beam', role='b',
                                   section_w=200.0, section_h=200.0, section_t=8.0,
                                   source_comp_ids=[2])
    # 첫 점 A(0,0,300) — z 다름·평면 다름.
    cands = candidate_joint_points(om, (0.0, 0.0, 300.0),
                                   exclude_comp=99, right_angle=True)
    # 후보 = (0,200,0) 근처 (A.xy 에서 수직 내린 z0 보 위 평면 최근접).
    assert any(abs(c[0]) < 60 and abs(c[1] - 200) < 60 and abs(c[2]) < 60
               for c in cands)
    # 모든 후보가 직각(평면·높이 둘 다 A 와 다름)이어야.
    for c in cands:
        assert abs(c[2] - 300.0) > 50    # 높이 다름
        assert (abs(c[0]) > 50 or abs(c[1]) > 50)   # 평면 다름


def test_add_none_when_no_partner():
    """짝 노드가 없으면(거리 밖) 등록 0."""
    om = _fake_om({1: (0, 0, 0), 2: (9999, 0, 0)}, {1: 10, 2: 20})
    om.joint_overrides = [JointOverride('add', (0.0, 0.0), (100.0, 0.0),
                                        z_a=0.0, z_b=0.0)]
    assert apply_added_joints(om) == 0


def test_add_dedup_in_scene():
    """같은 두 점 add 를 두 번 넣으면 Scene 이 중복 제거(1개 유지)."""
    scene = Scene()
    scene.set_joint_override(JointOverride('add', (0.0, 0.0), (0.0, 0.0),
                                           z_a=0.0, z_b=100.0))
    scene.set_joint_override(JointOverride('add', (0.0, 0.0), (0.0, 0.0),
                                           z_a=0.0, z_b=100.0, add_dofs=RIGID_DOFS))
    adds = [o for o in scene.joint_overrides if o.kind == 'add']
    assert len(adds) == 1
    assert adds[0].add_dofs == RIGID_DOFS   # 최신 것


# ── 통합: 모서리 중간 점 분할 (재설계) ────────────────────────

def test_add_splits_beam_at_midpoint():
    """모서리(보) 중간 점에 add 하면 그 보가 분할되고 새 결합이 생긴다."""
    scene = _make_stacked_module(1)
    om0 = build_ops_model(build_analysis_model(scene), scene)
    n_ele0 = len(om0.beam_elements)
    beams = [(t, n1, n2) for t, (n1, n2, k, r) in om0.beam_elements.items()
             if k == 'beam']
    assert len(beams) >= 2
    # 서로 다른 두 보의 중간점.
    _, a1, b1 = beams[0]
    _, a2, b2 = beams[1]
    m1 = (om0.node_tags[a1] + om0.node_tags[b1]) / 2.0
    m2 = (om0.node_tags[a2] + om0.node_tags[b2]) / 2.0

    scene.set_joint_override(JointOverride(
        'add',
        (float(m1[0]), float(m1[1])), (float(m2[0]), float(m2[1])),
        z_a=float(m1[2]), z_b=float(m2[2])))
    om1 = build_ops_model(build_analysis_model(scene), scene)
    # 두 보가 각각 분할(둘로) → element 최소 2개 증가.
    assert len(om1.beam_elements) >= n_ele0 + 2
    # 새 user_add 결합 + 분할점 노드(role) 존재.
    assert any(ed.kind == 'user_add' for ed in om1.spec.equal_dofs)
    assert any(nr.role == 'user_add_split' for nr in om1.spec.nodes)


def test_add_midpoint_multifloor():
    """모서리 중간 점 add 도 모든 층에 복제된다(보가 층마다 분할)."""
    scene = _make_stacked_module(3)
    om0 = build_ops_model(build_analysis_model(scene), scene)
    # 1층 바닥보 하나의 중간점 + 그와 직각인 다른 보 중간점.
    beams = [(t, n1, n2) for t, (n1, n2, k, r) in om0.beam_elements.items()
             if k == 'beam']
    # z 가 가장 낮은(1층) 보 두 개 선택.
    def _zmid(nn):
        return (om0.node_tags[nn[1]][2] + om0.node_tags[nn[2]][2]) / 2.0
    beams.sort(key=_zmid)
    _, a1, b1 = beams[0]
    m1 = (om0.node_tags[a1] + om0.node_tags[b1]) / 2.0
    # 같은 층(z 동일)의 다른 보 찾기.
    z1 = float(m1[2])
    other = None
    for t, n1, n2 in beams[1:]:
        mm = (om0.node_tags[n1] + om0.node_tags[n2]) / 2.0
        if abs(float(mm[2]) - z1) < 1.0 and (abs(mm[0] - m1[0]) > 1 or abs(mm[1] - m1[1]) > 1):
            other = mm
            break
    assert other is not None
    scene.set_joint_override(JointOverride(
        'add',
        (float(m1[0]), float(m1[1])), (float(other[0]), float(other[1])),
        z_a=z1, z_b=float(other[2])))
    om1 = build_ops_model(build_analysis_model(scene), scene)
    # 3개 층에 user_add 결합이 복제(상대 보가 있는 층마다).
    n_add = sum(1 for ed in om1.spec.equal_dofs if ed.kind == 'user_add')
    assert n_add >= 2   # 최소 여러 층 복제
