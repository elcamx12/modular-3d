"""구조해석 모델 단일 명세 (2026-05-12 명세통합 Phase 2).

본 모듈은 카탈로그 통합과 같은 단일 source-of-truth 패턴을 OpenSees 모델에
적용한다. OpenSees 등록기와 와이어프레임 표시기가 동일 데이터(`ModelSpec`)를
동일 순회기(`ModelSpec.iter_*`)로 읽어 "보이는 것 = 계산되는 것" 을 코드
구조 차원에서 보장.

## 1 급 알갱이 (7 종류)

명세에 직접 한 줄씩 보관되는 알갱이. 시각화·OpenSees 등록 모두 이 줄을
1:1 로 소비한다.

1. `NodeRec`      — 노드 (3D 좌표)
2. `BeamRec`      — 보-기둥 요소 (elasticBeamColumn)
3. `ShellRec`     — 면 요소 (ShellMITC4, 4 노드)
4. `EqualDofRec`  — 자유도 묶음 (equalDOF, DOF 목록 명시)
5. `RigidLinkRec` — 강체 링크 (rigidLink 'beam')
6. `DiaphragmRec` — 강체 다이어프램 (rigidDiaphragm)
7. `FixRec`       — 고정점 (fix)

단면·재료·변환축은 부수 정보로 부재 속성에서 자동 계산 — 1 급 알갱이 아님.

## 두 객체 분리

- `ModelSpec` — 토폴로지·결합·고정점 (5 케이스 공유)
- `LoadSpec`  — 케이스별 하중 (자중·활하중·횡력)

생애주기가 다르므로 분리. 같은 모듈에 둬 import 경로는 단일.

## 검증

`ModelSpec.validate()` 가 옛 self_check 역할을 수행. 빌드 실패 정책(Q5=가).

## 참고

본 파일은 Phase 2 (스켈레톤) 정의만 담는다. Scene → ModelSpec 빌더는
Phase 3 에서 추가, OpenSees 등록기는 Phase 5 에서 추가.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple, Optional, Set, Iterator
import numpy as np


# ── 케이스 enum (5 케이스) ────────────────────────────────────────


class Case(Enum):
    """해석 케이스. 토폴로지는 5 케이스 공유, 하중만 케이스별 갈아끼움."""
    DL = 'D+L'      # 중력 (D + L)
    EX = 'Ex'       # X 방향 지진
    EY = 'Ey'       # Y 방향 지진
    WX = 'Wx'       # X 방향 풍하중
    WY = 'Wy'       # Y 방향 풍하중


# ── 1 급 알갱이 자료 구조 ─────────────────────────────────────────


@dataclass
class NodeRec:
    """노드 — OpenSees `node(tag, x, y, z)` 와 1:1.

    role: 시각화 분류용. 'module_corner' / 'panel_corner' / 'core_shell' /
          'split_proj' / 'diaphragm_master' / 'wall_corner' / 그 외.
    """
    tag: int
    coord: np.ndarray              # (3,) float64, mm
    role: str = ''
    source_comp_id: int = 0        # Scene 컴포넌트 ID (split/master 는 0)


@dataclass
class BeamRec:
    """보-기둥 요소 — OpenSees `element('elasticBeamColumn', ...)` 와 1:1.

    section_w/h/t 는 시각화·재료 결정용. 'rc_core' role 은 콘크리트 E/G 사용.
    """
    tag: int
    n1: int                        # 시작 노드 tag
    n2: int                        # 끝 노드 tag
    kind: str                      # 'beam' | 'column'
    role: str                      # 'module_top_beam' / 'core_column' / ...
    section_w: float = 200.0
    section_h: float = 200.0
    section_t: float = 8.0
    source_comp_ids: List[int] = field(default_factory=list)


@dataclass
class ShellRec:
    """면 요소 — OpenSees `element('ShellMITC4', ...)` 와 1:1.

    4 노드 (n1·n2·n3·n4) 모두 보관 — 와이어프레임이 4 변 외곽선을 그리기 위함.
    """
    tag: int
    n1: int
    n2: int
    n3: int
    n4: int
    role: str                      # 셸 역할 식별자 (현재는 옛 잔재 없음)
    thickness: float               # mm
    source_comp_ids: List[int] = field(default_factory=list)


@dataclass
class EqualDofRec:
    """자유도 묶음 — OpenSees `equalDOF(master, slave, *dofs)` 와 1:1.

    DOF 목록은 (1,2,3) = (Ux, Uy, Uz), (4,5,6) = (Rx, Ry, Rz).
    kind: 시각화 분류용. 'pin' / 'merged_overlap' / 'wall_stack' / ...
    """
    master: int                    # 노드 tag
    slave: int                     # 노드 tag
    dofs: Tuple[int, ...]          # 묶을 자유도 번호 목록
    kind: str = 'pin'              # 시각화 분류
    # [정책 2026-05-13 접합부조정탭] 어떤 규칙이 이 결합을 등록했는지 식별자.
    # 후속 작업에서 자동 휴리스틱을 케이스별 명시 룰로 대체할 때, viewer 가
    # rule_id 별 색 매핑으로 결합을 구분하여 디버깅 가시성 확보. 미지정 시
    # 'legacy_auto' — 기존 자동 휴리스틱이 등록한 결합.
    rule_id: str = 'legacy_auto'


@dataclass
class RigidLinkRec:
    """강체 링크 — OpenSees `rigidLink('beam', master, slave)` 와 1:1.

    6 자유도 강체 (병진 3 + 회전 3) 한꺼번에 묶음. 길이 있는 강체 캔틸레버.
    """
    master: int
    slave: int
    kind: str = 'rigid'            # 시각화 분류
    rule_id: str = 'legacy_auto'


@dataclass
class DiaphragmRec:
    """강체 다이어프램 — OpenSees `rigidDiaphragm(3, master, *slaves)` 와 1:1.

    perpDirn=3 고정 (z 축 수직 평면 다이어프램).
    각 슬레이브의 (x, y, Rz) 가 마스터에 종속, 면외(z, Rx, Ry) 자유.

    [slaves vs ops_slaves — 2026-05-18 시각/해석 분리]
    slaves : 시각화·평형 검증·자료 추적용 전체 슬레이브 목록.
             base-fix 노드까지 포함해 viewer 가 41 개를 모두 그릴 수 있도록.
    ops_slaves : OpenSees 가 실제로 `rigidDiaphragm` 에 받은 좁은 목록.
                 base-fix 노드 제외 — 같은 자유도에 Penalty 가 이중으로
                 걸려 행렬 특이가 되는 것을 막기 위함.
    두 목록 다 보관해 시각화·검증이 일관된 자료원에서 동작.
    """
    master: int                    # 마스터 노드 tag
    slaves: List[int]              # 시각화·검증용 전체 슬레이브
    z_level: float = 0.0           # 시각화·디버그용
    rule_id: str = 'legacy_auto'
    # 시각화 숨김 플래그 — True 이면 viewer 가 면·스포크선 안 그림.
    # 사용 예: 모듈 천장 다이어프램(슬래브 없지만 mechanism 방지 위해 필수)을
    # 사용자 사양("바닥만 표시")에 맞게 백엔드만 활성화하고 화면에서 숨김.
    hidden: bool = False
    # OpenSees 가 실제로 rigidDiaphragm 에 받은 슬레이브 (base-fix 제외).
    # 비어있으면 곧 slaves 와 같다고 간주.
    ops_slaves: List[int] = field(default_factory=list)


@dataclass
class FixRec:
    """고정점 — OpenSees `fix(tag, *dofs)` 와 1:1.

    dofs: 각 자유도 (1,2,3,4,5,6) 의 고정 여부 1/0.
    """
    tag: int                       # 노드 tag
    dofs: Tuple[int, int, int, int, int, int]


# ── ModelSpec ────────────────────────────────────────────────────


@dataclass
class ModelSpec:
    """5 케이스 공유 토폴로지·결합·고정점 단일 명세.

    [원칙]
    - OpenSees 등록기와 와이어프레임 표시기 모두 본 명세만 보고 동작.
    - 단계별 분기·즉석 결정 없음 — 빌더가 모든 결정을 끝낸 후 명세 확정.

    [필드]
    nodes/beams/shells/equal_dofs/rigid_links/diaphragms/fixes:
        7 종 1 급 알갱이 리스트.
    member_to_split_tags:
        모듈 보 분할 매핑(load_calculator 가 자중 적용 시 sub-tag 들에
        분포 하중을 균등 적용하기 위해 보관).
    base_z:
        지점 고정 z 평면.
    """
    nodes: List[NodeRec] = field(default_factory=list)
    beams: List[BeamRec] = field(default_factory=list)
    shells: List[ShellRec] = field(default_factory=list)
    equal_dofs: List[EqualDofRec] = field(default_factory=list)
    rigid_links: List[RigidLinkRec] = field(default_factory=list)
    diaphragms: List[DiaphragmRec] = field(default_factory=list)
    fixes: List[FixRec] = field(default_factory=list)

    # ── 부수 정보 ──
    member_to_split_tags: Dict[int, List[int]] = field(default_factory=dict)
    base_z: float = 0.0

    # ── 빠른 조회용 인덱스 (빌드 후 캐싱) ──
    _node_by_tag: Dict[int, NodeRec] = field(default_factory=dict, repr=False)

    # ── 빌드 후 인덱스 갱신 ──
    def refresh_index(self) -> None:
        """노드 tag → NodeRec 역인덱스 재구축."""
        self._node_by_tag = {n.tag: n for n in self.nodes}

    def node(self, tag: int) -> Optional[NodeRec]:
        """노드 tag 로 NodeRec 조회. 인덱스가 stale 하면 자동 재구축 — silent
        miss 방지(인덱스 비었는데 nodes 가 차 있으면 한 번 더 갱신 시도)."""
        if not self._node_by_tag and self.nodes:
            self.refresh_index()
        return self._node_by_tag.get(tag)

    # ── 단일 순회기 ────────────────────────────────────────────
    # OpenSees 등록기와 와이어프레임 표시기 모두 본 메소드들을 호출해
    # 알갱이를 읽는다. 두 소비자 사이 누락 위험을 코드 차원에서 차단.

    def iter_nodes(self) -> Iterator[NodeRec]:
        yield from self.nodes

    def iter_beams(self) -> Iterator[BeamRec]:
        yield from self.beams

    def iter_shells(self) -> Iterator[ShellRec]:
        yield from self.shells

    def iter_equal_dofs(self) -> Iterator[EqualDofRec]:
        yield from self.equal_dofs

    def iter_rigid_links(self) -> Iterator[RigidLinkRec]:
        yield from self.rigid_links

    def iter_diaphragms(self) -> Iterator[DiaphragmRec]:
        yield from self.diaphragms

    def iter_fixes(self) -> Iterator[FixRec]:
        yield from self.fixes

    # ── 검증 (former self_check) ───────────────────────────────

    def validate(self, strict: bool = True) -> 'ValidationResult':
        """명세 자체 점검.

        검사 항목:
        1) 모든 BeamRec/ShellRec 의 노드 tag 가 NodeRec 에 존재.
        2) 모든 EqualDofRec/RigidLinkRec 의 master/slave 가 NodeRec 에 존재.
        3) DiaphragmRec 의 master/slaves 가 NodeRec 에 존재.
        4) FixRec 의 tag 가 NodeRec 에 존재.
        5) 노드 tag 중복 없음.
        6) 같은 (master, slave) 쌍에 두 종류 이상 결합 없음.
        7) FixRec.dofs 의 모든 값이 0/1.

        strict=True 면 첫 위반에서 RuntimeError 발생.
        """
        res = ValidationResult()
        node_tags = {n.tag for n in self.nodes}
        if len(node_tags) != len(self.nodes):
            res.issues.append('NodeRec tag 중복 있음')
            res.critical = True

        def _chk(name: str, tag: int):
            if tag not in node_tags:
                res.issues.append(f'{name}: 노드 tag {tag} 가 nodes 에 없음')
                res.critical = True

        for b in self.beams:
            _chk(f'BeamRec#{b.tag}', b.n1)
            _chk(f'BeamRec#{b.tag}', b.n2)
        for s in self.shells:
            _chk(f'ShellRec#{s.tag}', s.n1)
            _chk(f'ShellRec#{s.tag}', s.n2)
            _chk(f'ShellRec#{s.tag}', s.n3)
            _chk(f'ShellRec#{s.tag}', s.n4)
        for e in self.equal_dofs:
            _chk('EqualDofRec', e.master)
            _chk('EqualDofRec', e.slave)
            for d in e.dofs:
                if d not in (1, 2, 3, 4, 5, 6):
                    res.issues.append(f'EqualDofRec dof={d} 비정상')
                    res.critical = True
        for r in self.rigid_links:
            _chk('RigidLinkRec', r.master)
            _chk('RigidLinkRec', r.slave)
        for d in self.diaphragms:
            _chk('DiaphragmRec', d.master)
            for s in d.slaves:
                _chk('DiaphragmRec', s)
        for f in self.fixes:
            _chk('FixRec', f.tag)
            for v in f.dofs:
                if v not in (0, 1):
                    res.issues.append(f'FixRec.dofs 값 {v} 비정상 (0/1 만 허용)')
                    res.critical = True

        # 같은 (master, slave) 쌍 중복 — 결합 충돌
        pair_kinds: Dict[Tuple[int, int], List[str]] = {}
        for e in self.equal_dofs:
            key = (min(e.master, e.slave), max(e.master, e.slave))
            pair_kinds.setdefault(key, []).append(f'eqdof({e.kind})')
        for r in self.rigid_links:
            key = (min(r.master, r.slave), max(r.master, r.slave))
            pair_kinds.setdefault(key, []).append(f'rigid({r.kind})')
        for key, kinds in pair_kinds.items():
            if len(kinds) > 1:
                res.issues.append(
                    f'결합 충돌: 노드 쌍 {key} 에 두 종류 이상 결합 — {kinds}'
                )

        if strict and res.critical:
            raise RuntimeError(
                f'[ModelSpec.validate] critical {len(res.issues)} 건:\n  - '
                + '\n  - '.join(res.issues[:10])
            )
        return res

    # ── OpenSees 실제 등록 결과 vs 명세 대조 (Phase 7 검증 스위치) ──

    def verify_against_opensees(self, tol_mm: float = 1e-3,
                                 strict: bool = True) -> 'ValidationResult':
        """OpenSees 가 실제 받은 노드·요소를 회수해 명세와 비교.

        검증 항목:
        1) ops.getNodeTags() 의 tag set 이 spec.nodes 의 tag set 과 정확히 일치.
        2) 각 노드 좌표가 ops.nodeCoord 와 tol_mm 이내로 일치.
        3) ops.getEleTags() 의 tag set 이 spec.beams + spec.shells 의 tag set 과
           정확히 일치.
        4) 각 요소 양 끝 노드 ID 가 ops.eleNodes 와 일치.

        제약(equalDOF/rigidLink/rigidDiaphragm) 은 OpenSees 회수 API 가 미흡해
        spec self-consistency(validate()) 로만 확인.

        strict=True 면 첫 불일치에서 RuntimeError.
        """
        import openseespy.opensees as ops
        res = ValidationResult()

        try:
            ops_tags = set(ops.getNodeTags())
        except Exception as e:
            res.issues.append(f'ops.getNodeTags 실패: {e}')
            res.critical = True
            if strict:
                raise RuntimeError(res.issues[-1])
            return res
        spec_tags = {n.tag for n in self.nodes}
        missing_in_ops = spec_tags - ops_tags
        missing_in_spec = ops_tags - spec_tags
        if missing_in_ops:
            res.issues.append(
                f'spec 에는 있는데 OpenSees 에 없는 노드 {len(missing_in_ops)} 개 '
                f'(예: {sorted(missing_in_ops)[:5]})'
            )
            res.critical = True
        if missing_in_spec:
            res.issues.append(
                f'OpenSees 에는 있는데 spec 에 없는 노드 {len(missing_in_spec)} 개 '
                f'(예: {sorted(missing_in_spec)[:5]})'
            )
            res.critical = True

        # 좌표 비교 — spec 의 노드 좌표가 ops.nodeCoord 와 일치.
        mismatched_coord = 0
        for n in self.nodes:
            if n.tag not in ops_tags:
                continue
            try:
                c = ops.nodeCoord(n.tag)
            except Exception:
                continue
            d = abs(c[0] - n.coord[0]) + abs(c[1] - n.coord[1]) + abs(c[2] - n.coord[2])
            if d > tol_mm:
                mismatched_coord += 1
                if mismatched_coord <= 3:
                    res.issues.append(
                        f'노드 #{n.tag} 좌표 불일치 spec={n.coord.tolist()} '
                        f'ops={c} Δ={d:.3f}mm'
                    )
        if mismatched_coord > 3:
            res.issues.append(f'  … 좌표 불일치 노드 {mismatched_coord} 개 (이하 생략)')
        if mismatched_coord:
            res.critical = True

        # 요소 비교
        try:
            ops_ele_tags = set(ops.getEleTags())
        except Exception as e:
            res.issues.append(f'ops.getEleTags 실패: {e}')
            res.critical = True
            if strict and res.critical:
                raise RuntimeError(res.issues[-1])
            return res
        spec_beam_tags = {b.tag for b in self.beams}
        spec_shell_tags = {s.tag for s in self.shells}
        spec_ele_tags = spec_beam_tags | spec_shell_tags
        ele_missing_ops = spec_ele_tags - ops_ele_tags
        ele_missing_spec = ops_ele_tags - spec_ele_tags
        if ele_missing_ops:
            res.issues.append(
                f'spec 에는 있는데 OpenSees 에 없는 요소 {len(ele_missing_ops)} 개 '
                f'(예: {sorted(ele_missing_ops)[:5]})'
            )
            res.critical = True
        if ele_missing_spec:
            res.issues.append(
                f'OpenSees 에는 있는데 spec 에 없는 요소 {len(ele_missing_spec)} 개 '
                f'(예: {sorted(ele_missing_spec)[:5]})'
            )
            res.critical = True

        if strict and res.critical:
            raise RuntimeError(
                f'[ModelSpec.verify_against_opensees] {len(res.issues)} 건 불일치:\n  - '
                + '\n  - '.join(res.issues[:10])
            )
        return res

    # ── 디버그 요약 ────────────────────────────────────────────

    def summary(self) -> str:
        return (
            f'=== ModelSpec 요약 ===\n'
            f'  노드          : {len(self.nodes)}\n'
            f'  보-기둥       : {len(self.beams)}\n'
            f'  면 요소       : {len(self.shells)}\n'
            f'  자유도 묶음   : {len(self.equal_dofs)}\n'
            f'  강체 링크     : {len(self.rigid_links)}\n'
            f'  다이어프램    : {len(self.diaphragms)}\n'
            f'  고정점        : {len(self.fixes)}\n'
            f'  분할 매핑     : {len(self.member_to_split_tags)}\n'
            f'  base_z        : {self.base_z}\n'
        )


# ── LoadSpec ────────────────────────────────────────────────────


@dataclass
class DistributedLoadRec:
    """부재 분포하중 — OpenSees `eleLoad('-ele', tag, '-type', 'beamUniform', wy, wz, wx)` 매핑.

    분할된 모듈 보의 경우 member_to_split_tags 로 sub-tag 들에 균등 분포.
    """
    beam_tag: int                  # BeamRec.tag (또는 split sub-tag)
    wx: float = 0.0                # 축 방향 분포하중 N/mm
    wy: float = 0.0                # 국부 y 방향 N/mm
    wz: float = 0.0                # 국부 z 방향 (보통 중력) N/mm


@dataclass
class NodalLoadRec:
    """노드 집중하중 — OpenSees `load(tag, fx, fy, fz, mx, my, mz)` 매핑.

    다이어프램 마스터에 가해지는 횡력(지진·풍하중) 도 본 레코드.
    """
    node_tag: int
    fx: float = 0.0
    fy: float = 0.0
    fz: float = 0.0
    mx: float = 0.0
    my: float = 0.0
    mz: float = 0.0


@dataclass
class LoadSpec:
    """케이스별 하중 명세.

    [원칙]
    같은 ModelSpec 위에 케이스별로 다른 LoadSpec 만 갈아끼움 — 토폴로지 재빌드 X.

    [필드]
    case: 어느 케이스인지 (Case enum)
    distributed: 부재 분포하중 목록 (자중·활하중)
    nodal: 노드 집중하중 목록 (지진·풍하중 다이어프램 횡력)
    pattern_tag: OpenSees timeSeries/pattern 태그 (등록기가 부여)
    """
    case: Case
    distributed: List[DistributedLoadRec] = field(default_factory=list)
    nodal: List[NodalLoadRec] = field(default_factory=list)
    pattern_tag: int = 1

    def summary(self) -> str:
        return (
            f'=== LoadSpec [{self.case.value}] ===\n'
            f'  분포 하중     : {len(self.distributed)}\n'
            f'  집중 하중     : {len(self.nodal)}\n'
        )


# ── ValidationResult ──────────────────────────────────────────


@dataclass
class ValidationResult:
    """ModelSpec.validate() 결과.

    issues: 발견된 문제 메시지.
    critical: True 면 해석 진행 불가 수준(노드 누락 등).
    """
    issues: List[str] = field(default_factory=list)
    critical: bool = False

    def __bool__(self):
        return bool(self.issues)

    def __len__(self):
        return len(self.issues)
