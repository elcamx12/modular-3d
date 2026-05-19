"""R09 가 코어슬래브 변(`core_slab_beam`)에 사영 결합을 실제로 몇 번 시도하고,
몇 번 fall-through 되는지 + 어느 조건에서 막히는지 카운트.

[목적] 사용자 보고 "코어슬래브 가장자리에 접합 안 된다" 의 진짜 원인 식별:
  - 후보 자체가 없는가? (z 정렬 안 맞음, 거리 너무 멈)
  - 후보는 있는데 사영 조건에서 fall-through 되는가?
  - 사영점은 만들어지는데 c1 슬레이브 묶음이라 시각상 변 위에 안 보이는가?
"""
from __future__ import annotations

import os
import sys
import numpy as np
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_MYPROJ = os.path.dirname(os.path.dirname(_HERE))
_VENV_PY = os.path.join(_MYPROJ, 'venv', 'Scripts', 'python.exe')
if os.path.isfile(_VENV_PY) and os.path.abspath(sys.executable) != os.path.abspath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, __file__] + sys.argv[1:])
if _MYPROJ not in sys.path:
    sys.path.insert(0, _MYPROJ)
os.environ['MODULAR_DEBUG_OFF'] = '1'

SCENE_PATH = r'C:\Users\이건영\Desktop\종설\scene5.json'

_Z_TOL = 5.0
_CORE_LATERAL_MAX = 275.0  # joint_rules.py 기본값
_CORE_ALIGN_TOL = 55.0


def main():
    from modular_3d.io.scene_io import load_scene
    from modular_3d.analysis.topology import build_analysis_model

    scene, _ = load_scene(SCENE_PATH)
    am = build_analysis_model(scene)
    nodes = am.nodes
    members = am.members

    # 1) core_slab_beam 부재 추출 — 그 4 변이 가장자리.
    slab_edges = []  # (mid, c1, c2, axis)
    for mid, m in members.items():
        if m.role != 'core_slab_beam':
            continue
        c1 = nodes[m.n1].coord
        c2 = nodes[m.n2].coord
        dx = abs(c1[0] - c2[0])
        dy = abs(c1[1] - c2[1])
        dz = abs(c1[2] - c2[2])
        if dx > _Z_TOL and dy <= _Z_TOL and dz <= _Z_TOL:
            axis = 'x'
        elif dy > _Z_TOL and dx <= _Z_TOL and dz <= _Z_TOL:
            axis = 'y'
        else:
            axis = '?'
        slab_edges.append((mid, c1.copy(), c2.copy(), axis))
    print(f'core_slab_beam 부재 수: {len(slab_edges)} (axis 분포: '
          f'{Counter(e[3] for e in slab_edges)})')

    # 2) 코어 노드 집합 — apply_core_joint 와 같은 방식으로 계산.
    _CORE_LINE_ROLES_AXIS = (
        'core_column', 'core_top_runner', 'core_bottom_runner',
        'core_truss_v', 'core_truss_h', 'core_slab_beam',
    )
    core_nids = set()
    for role in _CORE_LINE_ROLES_AXIS:
        for mid in am.members_by_role(role):
            m = am.members[mid]
            core_nids.add(m.n1)
            core_nids.add(m.n2)
    external_nids = [nid for nid in nodes.keys() if nid not in core_nids]
    print(f'코어 노드 수: {len(core_nids)}, 외부 노드 수: {len(external_nids)}')

    # 3) 코어슬래브 z 분포 + 외부 노드 z 분포 헤드
    slab_zs = sorted({round(float((c1[2] + c2[2]) / 2), 1) for _, c1, c2, _ in slab_edges})
    print(f'core_slab_beam z 값: {slab_zs}')
    ext_z_count = Counter(round(float(nodes[nid].coord[2]), 0) for nid in external_nids)
    top_ext_z = sorted(ext_z_count.items(), key=lambda x: -x[1])[:10]
    print(f'외부 노드 z 빈도 상위 10: {top_ext_z}')

    # 3.5) (a) 직접 노드 매칭 카운트 — 슬래브 변 끝점(=코어 노드) ↔ 외부 노드
    print(f'\n[(a) 직접 노드 매칭] 슬래브 frame 끝점 노드와 외부 노드 매칭')
    a_success_by_z = defaultdict(int)
    slab_corner_nids = set()
    for mid, m in members.items():
        if m.role != 'core_slab_beam':
            continue
        slab_corner_nids.add(m.n1)
        slab_corner_nids.add(m.n2)
    for nP in external_nids:
        cP = nodes[nP].coord
        for nQ in slab_corner_nids:
            cQ = nodes[nQ].coord
            dz = abs(cP[2] - cQ[2])
            if dz > _Z_TOL:
                continue
            dx = abs(cP[0] - cQ[0]); dy = abs(cP[1] - cQ[1])
            cond_x = dx <= _CORE_ALIGN_TOL and (_Z_TOL < dy <= _CORE_LATERAL_MAX)
            cond_y = dy <= _CORE_ALIGN_TOL and (_Z_TOL < dx <= _CORE_LATERAL_MAX)
            if cond_x or cond_y:
                a_success_by_z[round(float(cQ[2]), 0)] += 1
    print(f'  슬래브 끝점 (a) 매칭 성공 수 / z: {dict(a_success_by_z)}')

    # 4) 각 슬래브 변별로 사영 후보 카운트 + fall-through 이유 분류.
    reasons = Counter()
    success = 0
    success_per_edge = []
    fail_lat_per_edge = []
    near_misses = []  # (외부 nid, edge mid, dx, dy, dz)
    for mid, c1, c2, axis in slab_edges:
        if axis == '?':
            continue
        per_edge_success = 0
        per_edge_lat_fail = 0
        for nP in external_nids:
            cP = nodes[nP].coord
            dz = abs(cP[2] - c1[2])
            if dz > _Z_TOL:
                reasons['dz>_Z_TOL'] += 1
                continue
            if axis == 'x':
                x_lo = min(c1[0], c2[0]); x_hi = max(c1[0], c2[0])
                if not (x_lo + _Z_TOL < cP[0] < x_hi - _Z_TOL):
                    reasons['x_range_out'] += 1
                    continue
                dy = abs(cP[1] - c1[1])
                if not (_Z_TOL < dy <= _CORE_LATERAL_MAX):
                    reasons[f'dy={int(dy)} out of (_Z_TOL,_CORE_LATERAL_MAX]'] += 1
                    per_edge_lat_fail += 1
                    if _Z_TOL < dy <= 500.0:  # 근접 실패 — 보고용
                        near_misses.append((nP, mid, 0.0, dy, dz))
                    continue
                reasons['axis=x success'] += 1
                success += 1
                per_edge_success += 1
            else:  # axis == 'y'
                y_lo = min(c1[1], c2[1]); y_hi = max(c1[1], c2[1])
                if not (y_lo + _Z_TOL < cP[1] < y_hi - _Z_TOL):
                    reasons['y_range_out'] += 1
                    continue
                dx = abs(cP[0] - c1[0])
                if not (_Z_TOL < dx <= _CORE_LATERAL_MAX):
                    reasons[f'dx={int(dx)} out of (_Z_TOL,_CORE_LATERAL_MAX]'] += 1
                    per_edge_lat_fail += 1
                    if _Z_TOL < dx <= 500.0:
                        near_misses.append((nP, mid, dx, 0.0, dz))
                    continue
                reasons['axis=y success'] += 1
                success += 1
                per_edge_success += 1
        success_per_edge.append((mid, per_edge_success))
        fail_lat_per_edge.append((mid, per_edge_lat_fail))

    print(f'\n[사영 시도 결과] 성공 {success} 건')
    print('이유 분포 (상위 15):')
    for k, v in reasons.most_common(15):
        print(f'  {v:>6}  {k}')
    print(f'\n변별 성공 수 (상위 5): {sorted(success_per_edge, key=lambda x: -x[1])[:5]}')
    print(f'변별 측방거리 실패 수 (상위 5): {sorted(fail_lat_per_edge, key=lambda x: -x[1])[:5]}')

    # 5) 근접 실패 — 측방거리가 (275, 500] 범위에서 막힌 케이스 샘플
    if near_misses:
        print(f'\n근접 실패 (lateral 5~500mm 범위, _CORE_LATERAL_MAX 안 들어옴) 샘플 10:')
        for nP, mid, dx, dy, dz in near_misses[:10]:
            cP = nodes[nP].coord
            print(f'  외부 nid={nP} coord={cP.tolist()} → core_slab_edge mid={mid} '
                  f'dx={dx:.1f} dy={dy:.1f} dz={dz:.1f}')

    # 6) 코어슬래브 변의 끝점 좌표 + z 별 변 mid 매핑
    print(f'\n각 슬래브 변 양 끝점 좌표 (전체):')
    by_z = defaultdict(list)
    for mid, c1, c2, axis in slab_edges:
        by_z[round(float(c1[2]), 0)].append((mid, axis))
    for z, items in sorted(by_z.items()):
        print(f'  z={z}: {items}')

    # 7) 3층 천장 (z 가 가장 높은 슬래브 또는 그 위) 부근 노드 상세
    # 슬래브 z 들의 최고값 부근에 어떤 외부 노드가 있는지, 측방거리는 얼마인지.
    print(f'\n[상세] 각 슬래브 변 z 평면별 외부 노드 분포 + 사영 가능성')
    for slab_z in sorted({round(float(c1[2]), 0) for _, c1, _, _ in slab_edges}):
        # 같은 z 평면 외부 노드 (±5mm)
        near = [nid for nid in external_nids
                if abs(float(nodes[nid].coord[2]) - slab_z) <= 5.0]
        print(f'  slab z={slab_z}: 같은 z 외부 노드 {len(near)} 개')
        if not near:
            continue
        # 이 z 평면 슬래브 변 4개 추출
        edges_at_z = [(mid, c1, c2, axis) for mid, c1, c2, axis in slab_edges
                      if abs(float(c1[2]) - slab_z) <= 5.0]
        for nid in near[:20]:
            cP = nodes[nid].coord
            # 가장 가까운 변과 측방거리
            best = None
            for mid, c1, c2, axis in edges_at_z:
                if axis == 'x':
                    x_lo = min(c1[0], c2[0]); x_hi = max(c1[0], c2[0])
                    in_range = x_lo - 5 <= cP[0] <= x_hi + 5
                    lat = abs(cP[1] - c1[1])
                elif axis == 'y':
                    y_lo = min(c1[1], c2[1]); y_hi = max(c1[1], c2[1])
                    in_range = y_lo - 5 <= cP[1] <= y_hi + 5
                    lat = abs(cP[0] - c1[0])
                else:
                    continue
                if best is None or lat < best[1]:
                    best = (mid, lat, in_range, axis)
            src = nodes[nid].source_comp_id
            scomp = scene.components.get(src) if src else None
            stype = type(scomp).__name__ if scomp else '?'
            if best:
                mid, lat, in_range, axis = best
                print(f'    ext nid={nid} ({stype}#{src}) coord={[round(float(v),0) for v in cP]} '
                      f'→ 가장가까운 변 mid={mid}({axis}축) 측방거리={lat:.0f}mm, 변범위안={in_range}')
            else:
                print(f'    ext nid={nid} ({stype}#{src}) coord={[round(float(v),0) for v in cP]} → 후보변 없음')

    # 8) 사용자 보고: 모듈 천장 코너 z 가 통합 후 어디로 갔나
    # 모듈 컴포넌트의 천장 코너 nid 추적
    from modular_3d.model import Module
    print(f'\n[모듈 천장 코너 노드 추적]')
    for cid, comp in scene.components.items():
        if not isinstance(comp, Module):
            continue
        nids = am.comp_to_nodes.get(cid, [])
        zs = sorted({round(float(nodes[n].coord[2]), 0) for n in nids})
        print(f'  Module #{cid} pos={comp.position.tolist()} → 컴포 노드 z 분포: {zs} ({len(nids)} 노드)')


if __name__ == '__main__':
    main()
