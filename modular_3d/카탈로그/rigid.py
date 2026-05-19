"""OpenSees 가상 강체 강성 상수 (rigid arm 의 elasticBeamColumn 단면).

[목적]
ops_builder 가 rigidLink('beam', ...) 또는 짧은 강체 빔으로 두 노드를 강제
이동시킬 때 사용하는 큰 강성 값. 너무 크면 행렬 조건수 악화, 너무 작으면
강체 효과 미흡.

[변경 영향처]
ops_builder 의 모든 rigid arm 단면. 옛 코드는 함수 내부 매직넘버였음.
"""

# 가상 강체용 큰 강성·단면
RIGID_E_MPA = 1.0e9
RIGID_A_MM2 = 1.0e6
RIGID_I_MM4 = 1.0e12
RIGID_J_MM4 = 1.0e12


__all__ = ['RIGID_E_MPA', 'RIGID_A_MM2', 'RIGID_I_MM4', 'RIGID_J_MM4']
