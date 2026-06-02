"""단면설계 결과 → 배치 컴포넌트 렌더 형상(BeamData/ColumnData) write-back (L1-1).

[배경 — WYSIWYG 붕괴]
단면설계가 보를 H400, 기둥을 250각으로 수렴시켜도 BeamData/ColumnData 의
section_w/h/t 는 200 고정이라 3D 솔리드 뷰가 설계 결과를 안 보여줬다. 본 패스가
수렴 직후 해석부재의 설계 단면을 *그 부재가 나온 컴포넌트*의 렌더 형상으로 옮긴다.

[안전성 — 렌더 전용]
section_w/h/t 는 render/mesh_builder 만 실시간 사용. 구조해석·물량은 별도 경로
(AnalysisMember.design_section 또는 고정 공칭값)를 쓰므로, 본 갱신은 화면에만
영향(해석 결과·물량 불변, 시각만 정확해짐).

[정밀도 — 컴포넌트 일괄(사용자 결정)]
같은 컴포넌트의 기둥들엔 기둥 설계단면, 보들엔 보 설계단면을 적용(역할 대분류).
같은 역할 내 부재별(예: 상부보 vs 하부보) 차이는 뭉갠다. 정밀 매핑은 후속 여지.

[H형강 한계]
H 단면의 외형 폭·높이는 design_section 에서 가져오나, 웨브/플랜지 두께는 별도
관리(현 한계). SHS 는 w/h/t 모두 정확.
"""
from modular_3d.model.core import BeamData, ColumnData


def _design_dims(m):
    """해석부재의 표시용 (w, h, t) — design_section 우선(H 포함), 없으면 현 치수."""
    sec = getattr(m, 'design_section', None)
    if sec is not None:
        w = getattr(sec, 'w', None)
        h = getattr(sec, 'h', None)
        t = getattr(sec, 't', None)
        if w and h:
            return (float(w), float(h),
                    float(t) if t else float(getattr(m, 'section_t', 8.0)))
    w = getattr(m, 'section_w', None)
    h = getattr(m, 'section_h', None)
    if w and h:
        return (float(w), float(h), float(getattr(m, 'section_t', 8.0)))
    return None


def _iter_member_data(comp):
    """comp 의 BeamData/ColumnData 전부 산출(단일 속성·리스트 모두)."""
    for v in vars(comp).values():
        if isinstance(v, (BeamData, ColumnData)):
            yield v
        elif isinstance(v, (list, tuple)):
            for it in v:
                if isinstance(it, (BeamData, ColumnData)):
                    yield it


def apply_design_sections_to_scene(scene, am) -> int:
    """수렴된 해석부재 단면을 scene 컴포넌트의 렌더 형상으로 반영.

    Args:
        scene: 배치 Scene (components: id→Component).
        am: 단면설계가 design_section 을 써넣은 AnalysisModel
            (comp_to_members: comp.id→[mid], members: mid→AnalysisMember 필요).
    Returns:
        갱신한 BeamData/ColumnData 개수.
    """
    if scene is None or am is None:
        return 0
    comp_to_members = getattr(am, 'comp_to_members', None)
    members = getattr(am, 'members', None)
    components = getattr(scene, 'components', None)
    if not comp_to_members or not members or not components:
        return 0

    n = 0
    for cid, mids in comp_to_members.items():
        # 이 컴포넌트의 역할 대분류별 대표 단면 수집(기둥 / 보).
        col_dims = None
        beam_dims = None
        for mid in mids:
            m = members.get(mid)
            if m is None:
                continue
            dims = _design_dims(m)
            if dims is None:
                continue
            role = getattr(m, 'role', '') or ''
            if 'column' in role:
                col_dims = dims
            elif getattr(m, 'kind', '') == 'beam':
                beam_dims = dims
        comp = components.get(cid)
        if comp is None:
            continue
        for md in _iter_member_data(comp):
            dims = col_dims if isinstance(md, ColumnData) else beam_dims
            if dims is None:
                continue
            md.section_w, md.section_h, md.section_t = dims
            n += 1
    return n


__all__ = ['apply_design_sections_to_scene']
