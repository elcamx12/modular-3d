"""부재 역할(role) 한글 분류 — analysis_panel·물량 분해기 공용.

[추출 근거 — 물량탭 개편 Phase 1]
부재 역할 한글 매핑(_ROLE_KO)과 길이 기반 가장자리보 장/단변 분기 로직
(_classify_role_ko)을 analysis_panel.AnalysisPanel 의 인스턴스 메서드에서
중립 함수로 추출. self 를 쓰지 않고 model 만 의존하므로 그대로 분리 가능.
물량 분해기(quantity_by_component)와 응력비 트리가 동일 분류를 공유한다.
"""
from __future__ import annotations


# 부재 role(영어) → 한글 표시명. 트리/물량 "역할" 묶음 헤더에 사용.
ROLE_KO = {
    'module_column':         '기둥',
    'module_bottom_beam':    '바닥보',
    'module_top_beam':       '천장보',
    'floor_edge_beam':       '가장자리보',  # 길이 비교로 장변보/단변보 분기됨
    'wall_column':           '벽 기둥',
    'wall_bottom_runner':    '벽 하부보',
    'wall_top_runner':       '벽 상부보',
    'cantilever_beam':       '캔틸레버보',
    'cantilever_slab_beam':  '캔틸슬래브 보',
    'mid_beam':              '중간보',
    'mid_column':            '중간기둥',
    'core_column':           '코어 기둥',
    'core_bottom_runner':    '코어 하부보',
    'core_top_runner':       '코어 상부보',
    'core_truss_v':          '트러스(수직)',
    'core_truss_h':          '트러스(수평)',
    'core_truss_d':          '트러스(대각)',
    'core_slab_beam':        '슬래브 변',
}

# role 한글명의 정렬 우선순위 — 트리/물량 표시 순서.
ROLE_KO_ORDER = [
    '기둥', '벽 기둥', '코어 기둥', '중간기둥',
    '천장보', '바닥보',
    '벽 상부보', '벽 하부보', '코어 상부보', '코어 하부보',
    '장변보', '단변보', '가장자리보',
    '중간보', '캔틸레버보', '캔틸슬래브 보',
    '트러스(수직)', '트러스(수평)', '트러스(대각)',
    '슬래브 변',
]


def role_ko_order_key(name: str) -> int:
    """역할 한글명 → 정렬 키. 미등록은 맨 뒤."""
    try:
        return ROLE_KO_ORDER.index(name)
    except ValueError:
        return len(ROLE_KO_ORDER)


def classify_role_ko(m, model, mid: int, cid: int) -> str:
    """부재 role 영어 → 한글 표시명. floor_edge_beam 은 길이로 장/단변 분기.

    [함정] floor_edge_beam 은 같은 패널의 가장자리보 4 개를 길이로 비교해
    장변보/단변보로 나눈다. 거의 정사각형(차 50mm 미만)이면 '가장자리보' 유지.
    """
    role = m.role
    if role != 'floor_edge_beam':
        return ROLE_KO.get(role, role)
    # 같은 패널의 가장자리보 길이 수집 — 길이로 장변/단변 분류
    same_role_lens = []
    for x in model.comp_to_members.get(cid, []):
        mx = model.members.get(x)
        if mx is not None and mx.role == 'floor_edge_beam':
            same_role_lens.append((x, model.get_member_length(x)))
    if not same_role_lens:
        return '가장자리보'
    L_max = max(L for _, L in same_role_lens)
    L_min = min(L for _, L in same_role_lens)
    if (L_max - L_min) < 50.0:  # 거의 정사각형 패널
        return '가장자리보'
    L = model.get_member_length(mid)
    return '장변보' if L >= (L_max + L_min) / 2 else '단변보'


__all__ = [
    'ROLE_KO', 'ROLE_KO_ORDER', 'role_ko_order_key', 'classify_role_ko',
]
