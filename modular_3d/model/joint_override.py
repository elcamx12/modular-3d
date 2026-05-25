"""접합부 오버라이드 — 사용자가 컴포넌트 간 접합을 직접 제거/변경/추가한 기록.

배경:
  컴포넌트 간 접합(모듈↔모듈, 바닥패널↔기둥, 코어 등)은 매 해석마다
  analysis/joint_rules 의 R01~R09 가 자동 생성한다(기본 핀). 사용자가 이를
  직접 바꾸고 싶을 때, 자동 규칙을 건드리지 않고 그 위에 덮어쓰는 계층이
  JointOverride 다. 해석 빌드의 equalDOF 등록 게이트가 이 목록을 참조해
  매칭되는 접합을 제거/강접/핀으로 바꾼다.

다층 자동 적용(핵심):
  접합은 두 노드 사이. 다층 복제본은 같은 평면 위치(xy)에서 층(z)만 바뀌며
  반복된다("위로 쭉"). 따라서 오버라이드를 두 끝점의 평면 좌표쌍(a_xy, b_xy)으로
  식별하면 z(층) 무관하게 같은 xy 의 모든 층 접합에 자동 적용된다. 상대가 없는
  층은 매칭 실패로 자연 건너뜀.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# 매칭 허용 오차 — 노드 격자 간격(보통 3000mm+)에 비해 충분히 작아 인접 접합
# 오매칭이 없으면서, 사영점·부동소수 흔들림은 흡수하는 값.
MATCH_TOL_MM = 50.0

# 접합 성질별 자유도 묶음. (1,2,3)=병진만(핀), (1..6)=병진+회전(강접).
PIN_DOFS: Tuple[int, ...] = (1, 2, 3)
RIGID_DOFS: Tuple[int, ...] = (1, 2, 3, 4, 5, 6)


@dataclass
class JointOverride:
    """접합 하나에 대한 사용자 변경 기록.

    kind:
      'remove' — 그 접합을 완전히 없앤다(equalDOF 미등록).
      'rigid'  — 기존 접합을 강접으로(자유도 1~6).
      'pin'    — 기존 접합을 핀으로 되돌림(자유도 1,2,3).
      'add'    — 두 점을 골라 신규 접합 생성(2단계).
    a_xy, b_xy:
      두 끝점의 평면 좌표(mm). 층(z) 무관 매칭 키. 수평 접합이면 두 xy 가
      다르고, 수직 접합이면 같다(이 차이로 수평/수직 접합이 구분된다).
    a_group, b_group:
      두 끝점이 속한 컴포넌트의 group_id(보조 식별 — 같은 xy 다른 그룹 구분용).
    z_a, z_b:
      (add 전용) 두 점의 z(mm). 신규 접합 생성 시 노드 탐색에 사용.
    add_dofs:
      (add 전용) 신규 접합 성질. PIN_DOFS 또는 RIGID_DOFS.
    """

    kind: str
    a_xy: Tuple[float, float]
    b_xy: Tuple[float, float]
    a_group: int = 0
    b_group: int = 0
    z_a: float = 0.0
    z_b: float = 0.0
    add_dofs: Tuple[int, ...] = PIN_DOFS
    # 접합 종류 식별(rule_id) — 같은 평면 위치에 수직 접합이 2종 겹칠 때
    # (예: 모듈↔모듈 R02 + 바닥패널↔모듈 R03) 그 종류에만 변경을 적용하기 위함.
    # 빈 문자열이면 종류 무관(모든 겹친 접합에 적용 — 하위호환).
    rule_id: str = ''
    # (add 전용) True 면 다층 복제하지 않고 클릭한 그 층의 접합만 추가.
    single_layer: bool = False
    # (add 전용) True 면 두 점을 직선이 아니라 ㄴ자(중간 노드 경유, 수직+수평)로
    # 직각 결합(R03 방식). 두 점이 평면·높이 둘 다 어긋날 때 사용.
    right_angle: bool = False
    # (add 전용) 끝점이 "보 위 선점(노드 추가형)" 인지 "기존 꼭지점(노드 스냅형)"
    # 인지. True 면 그 자리에 보를 분할해 새 노드를 만들어 접합(가까운 노드로
    # 끌려가지 않음). False 면 가까운 기존 노드에 스냅. 사용자가 추가 시 스냅한
    # 점의 종류(주황=선/초록=꼭지점)를 그대로 실제 접합에 반영하기 위함.
    a_on_edge: bool = False
    b_on_edge: bool = False

    # ── 직렬화 ──
    def to_dict(self) -> Dict:
        return {
            'kind': self.kind,
            'a_xy': [float(self.a_xy[0]), float(self.a_xy[1])],
            'b_xy': [float(self.b_xy[0]), float(self.b_xy[1])],
            'a_group': int(self.a_group),
            'b_group': int(self.b_group),
            'z_a': float(self.z_a),
            'z_b': float(self.z_b),
            'add_dofs': [int(d) for d in self.add_dofs],
            'rule_id': str(self.rule_id),
            'single_layer': bool(self.single_layer),
            'right_angle': bool(self.right_angle),
            'a_on_edge': bool(self.a_on_edge),
            'b_on_edge': bool(self.b_on_edge),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> 'JointOverride':
        ax = d.get('a_xy', [0.0, 0.0])
        bx = d.get('b_xy', [0.0, 0.0])
        return cls(
            kind=str(d.get('kind', 'pin')),
            a_xy=(float(ax[0]), float(ax[1])),
            b_xy=(float(bx[0]), float(bx[1])),
            a_group=int(d.get('a_group', 0)),
            b_group=int(d.get('b_group', 0)),
            z_a=float(d.get('z_a', 0.0)),
            z_b=float(d.get('z_b', 0.0)),
            add_dofs=tuple(int(x) for x in d.get('add_dofs', PIN_DOFS)),
            rule_id=str(d.get('rule_id', '')),
            single_layer=bool(d.get('single_layer', False)),
            right_angle=bool(d.get('right_angle', False)),
            a_on_edge=bool(d.get('a_on_edge', False)),
            b_on_edge=bool(d.get('b_on_edge', False)),
        )

    def effective_dofs(self) -> Optional[Tuple[int, ...]]:
        """이 오버라이드가 만들 자유도 묶음. remove 면 None(등록 안 함)."""
        if self.kind == 'remove':
            return None
        if self.kind == 'rigid':
            return RIGID_DOFS
        if self.kind == 'pin':
            return PIN_DOFS
        if self.kind == 'add':
            return tuple(self.add_dofs)
        return None


def _xy_close(p, q, tol: float) -> bool:
    """두 평면 좌표가 tol(mm) 이내인가."""
    return (abs(float(p[0]) - float(q[0])) <= tol
            and abs(float(p[1]) - float(q[1])) <= tol)


def match_override(overrides: List[JointOverride],
                   ma_xy, mb_xy, rule_id=None,
                   ma_z=None, mb_z=None,
                   tol: float = MATCH_TOL_MM) -> Optional[JointOverride]:
    """노드쌍(평면 좌표 ma_xy, mb_xy)에 매칭되는 기존-접합 오버라이드 반환.

    remove/rigid/pin 만 대상(add 는 신규 생성이라 매칭이 아니라 별도 등록).
    양방향 비교 — 룰마다 master/slave 순서가 다를 수 있으므로
    (a≈ma, b≈mb) 또는 (a≈mb, b≈ma) 면 매칭. 여러 개면 마지막(최근) 것 우선.

    rule_id: 등록 중인 접합의 종류. 오버라이드에 rule_id 가 지정돼 있으면(빈
    문자열 아님) 종류가 같아야 매칭 — 같은 평면 위치에 겹친 다른 종류 수직
    접합을 구분해 그 종류에만 적용한다. 오버라이드 rule_id 가 비었으면 종류 무관.
    """
    found: Optional[JointOverride] = None
    for ov in overrides:
        if ov.kind not in ('remove', 'rigid', 'pin'):
            continue
        ov_rid = getattr(ov, 'rule_id', '')
        if ov_rid and rule_id is not None and ov_rid != rule_id:
            continue
        # single_layer 면 그 층(z)에만 — z 도 일치해야 매칭('이 층만' 제거/편집).
        if (getattr(ov, 'single_layer', False)
                and ma_z is not None and mb_z is not None):
            zf = (abs(float(ov.z_a) - ma_z) <= tol
                  and abs(float(ov.z_b) - mb_z) <= tol)
            zb = (abs(float(ov.z_a) - mb_z) <= tol
                  and abs(float(ov.z_b) - ma_z) <= tol)
            if not (zf or zb):
                continue
        fwd = _xy_close(ov.a_xy, ma_xy, tol) and _xy_close(ov.b_xy, mb_xy, tol)
        bwd = _xy_close(ov.a_xy, mb_xy, tol) and _xy_close(ov.b_xy, ma_xy, tol)
        if fwd or bwd:
            found = ov  # 나중 것이 우선(사용자 최신 변경)
    return found


def same_joint(ov: JointOverride, a_xy, b_xy, tol: float = MATCH_TOL_MM) -> bool:
    """오버라이드가 가리키는 접합이 노드쌍(a_xy, b_xy)과 같은 접합인가(양방향)."""
    fwd = _xy_close(ov.a_xy, a_xy, tol) and _xy_close(ov.b_xy, b_xy, tol)
    bwd = _xy_close(ov.a_xy, b_xy, tol) and _xy_close(ov.b_xy, a_xy, tol)
    return fwd or bwd
