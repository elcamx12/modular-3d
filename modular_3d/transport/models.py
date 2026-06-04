"""운송 시뮬레이션 데이터모델.

운송프로그램 원본(`src/models.py`, Song-Jung-Hun/-3-) 이식 + 우리 시스템
요구 사항 반영. 단위 규약: 길이 mm, 중량 kg.

[원본과의 차이 — Phase 1 변경점]
1. **B-11 수정** (Module.weight 보 길이 2배 오버):
   원본 식 `(4*(w+l)*2)/1000` → 정정 `(4*(w+l))/1000`.
   근거: 4기둥 + (천장4보 + 바닥4보) = 8보. 8보의 총 길이 = 4(w+l).
   원본 식은 한 층에 보 16 개를 가정한 셈으로 2 배 오버 (플로어패널 식
   `2(w+l)` 와 비교해도 모듈만 비대칭).
2. **B-15 보강** (truck_type 런타임 검증):
   `@dataclass(frozen=True)` 의 typing.Literal 은 정적 힌트라 JSON 로드시
   값 검증을 하지 않는다 → `__post_init__` 에서 명시 검증.
   PanelKind, SectionType 도 동일 처리.
3. **정밀화 필드**:
   - `Truck.curb_weight_kg`: 차체 자체 중량 (kg). 현장 총중량(GVW) 검사 시
     화물에 합산 (limits.can_carry).
   - `Truck.active`: 패킹 후보 포함 여부 (B-4 A-frame 데드데이터 보존 정책).
   - (트레일러 길이 trailer_length_mm 는 2026-05-26 도로 등급 폐지와 함께 제거.)
4. **A-1 데이터모델 확장** (다면 종속 패널 일반화):
   원본 `Panel.kind ∈ {floor, wall, lshape}` 의 `lshape` 단일면 한계를
   극복하기 위해 `Panel.wall_segments: tuple[WallSegment, ...]` 신설.
   - 빈 튜플 + kind=floor → 순수 플로어
   - 1세그 + kind=floor → L자 (원본 lshape 와 의미상 동등)
   - 2세그 → ㄷ자, 3세그 → 3면, 4세그 → 4면 (이론상)
   - kind=wall → 독립 벽패널 (세그먼트 사용 안 함)
   원본 `wall_height` 단일 필드는 wall_segments[0].height_mm 로 흡수.
   `kind="lshape"` 도 호환을 위해 Literal 에 남겨두지만 신규 코드는
   kind="floor" + wall_segments 1개 형태를 사용한다.
5. **A-3 한 면 면적 합산 정책**:
   원본 운송프로그램 모듈 분기(`app.py:_module_form`) 와 같이 면별 단위
   중량 × 한 면 면적 합산. 양면 합산 옵션은 신설하지 않음. 어댑터
   (Phase 3) 가 4면 면별 unit weight (분석 ⑥ 가중평균) 를 곱해 합산.
6. **Section 카탈로그 비사용**:
   `load_sections` 는 본 패키지에서 제공하지 않는다. 우리 SHS_CATALOG
   (카탈로그/steel_sections.py) 가 단일 진실원이며 Phase 3 adapter 가
   `SHSSection → transport.Section` 변환 함수를 제공한다.

[차후 페이즈에서 변경 예정]
- Phase 2: Panel.weight 식이 wall_segments 합산으로 일반화됨
- Phase 3: from_shs(SHSSection) 클래스 생성자 추가
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple


# ── Literal 별칭 + 런타임 검증용 화이트리스트 ─────────────────
PanelKind = Literal["floor", "wall", "lshape"]
TruckType = Literal["lowbed", "extendable", "aframe"]
SectionType = Literal["SHS", "RHS", "H", "C", "L", "CFT"]

_PANEL_KINDS: frozenset[str] = frozenset({"floor", "wall", "lshape"})
_TRUCK_TYPES: frozenset[str] = frozenset({"lowbed", "extendable", "aframe"})
_SECTION_TYPES: frozenset[str] = frozenset({"SHS", "RHS", "H", "C", "L", "CFT"})

# 변 인덱스: 0=하변, 1=우변, 2=상변, 3=좌변 (윗면에서 본 부재 외주 시계방향)
_SIDE_INDEXES: frozenset[int] = frozenset({0, 1, 2, 3})

# 모듈 2개를 한 트럭에 옆자리로 합산(페어링)할 수 있는 1개당 최대 길이(mm).
# [함정] 패커 여러 경로(packer_core 배치/후처리, packer_meta 비용하한,
#   packer_local_search 국소탐색)가 이 값을 공유한다 — 한 곳만 바꾸면 비용
#   추정과 실제 배치가 어긋나므로 반드시 이 상수만 수정할 것.
# 트럭/도로 높이 4.5m(trucks.json max_height 등)와는 무관한 별개 값이다.
MODULE_PAIR_MAX_LEN_MM: float = 6000.0


@dataclass(frozen=True)
class Section:
    """KS 표준 강재 단면 (원본 그대로).

    weight_per_m = 단면적(mm²) × 강재 밀도(7,850 kg/m³) × 1m / 10⁹.
    """

    name: str
    section_type: SectionType
    width: float            # mm
    height: float           # mm
    thickness: float        # mm (SHS/RHS 두께, H/C 웨브 두께, L 변 두께)
    weight_per_m: float     # kg/m
    flange_thickness: float = 0.0

    def __post_init__(self) -> None:
        # B-15 와 같은 맥락의 런타임 검증.
        if self.section_type not in _SECTION_TYPES:
            raise ValueError(
                f"Section.section_type 값 '{self.section_type}' 은 허용 목록 "
                f"{sorted(_SECTION_TYPES)} 에 없음."
            )
        if self.width <= 0 or self.height <= 0 or self.thickness <= 0:
            raise ValueError(
                f"Section '{self.name}': width/height/thickness 는 양수여야 함."
            )
        if self.weight_per_m <= 0:
            raise ValueError(f"Section '{self.name}': weight_per_m 양수 필요.")


# 부재 종류 — 모든 Part 가 공유하는 화이트리스트
_PART_KINDS: frozenset[str] = frozenset({
    "column", "beam", "slab", "wall_fill", "core_wall",
})

# Part subkind — 시각화 라벨 + 색상 분기용 (선택 필드)
_PART_SUBKINDS: frozenset[str] = frozenset({
    "bottom", "top", "perim", "runner", "wall_seg_col", "wall_seg_beam", "",
})

# AttachedPart.source_kind — 어느 *종속 컴포넌트* 에서 온 부재인지
_SOURCE_KINDS: frozenset[str] = frozenset({
    "own",                     # 부모 자체 부재 (BodyPart 의미)
    "midbeam", "midcolumn",
    "cantilever_beam", "cantilever_slab",
})


@dataclass(frozen=True)
class BodyPart:
    """부모 화물(모듈·바닥패널·벽패널·수직3모듈) *자체* 부재 한 개의 박스.

    [데이터 흐름]
      배치 모델의 BeamData/ColumnData/SlabData/WallPanelData (월드 좌표)
        → 어댑터의 _nominal_aabb_*() 가 부모 nominal 좌표계 박스로 변환
        → 운송 X-Y 교환 매핑으로 운송 좌표계 박스로 변환
        → 본 BodyPart 1 개 생성
    부재 1 개 = Part 1 개. CantileverSlab 의 3 변 보 + 슬래브는 4 개의
    Part 로 풀어 첨부한다 (한 덩어리 AABB 로 묶지 않음 — 보가 사라짐 방지).
    """

    kind: str           # "column"|"beam"|"slab"|"wall_fill"|"core_wall"
    x_mm: float
    y_mm: float
    z_mm: float
    length_mm: float
    width_mm: float
    height_mm: float
    subkind: str = ""   # 시각화 라벨용 — "bottom"/"top"/"perim"/"runner"/...
    # ── 보 단면 형상 (H형강 운송 렌더용, 2026-05-28) ──
    # section_type: 'shs'(각형강관)|'h'(H형강). 보(kind="beam")만 의미.
    # web_dx/dy/dz: *운송 좌표계* 기준 웨브(강축) 방향 단위벡터. 건물에서 보의
    #   웨브는 연직(=운송 z)이라 정상 자세는 (0,0,1). 부재를 눕혀 운송하면
    #   좌표 remap 과 동일한 축 순열을 본 벡터에도 적용해 건물 단면을 그대로
    #   회전시킨다(시각화가 강축/약축을 정확히 표현).
    section_type: str = "shs"
    web_dx: float = 0.0
    web_dy: float = 0.0
    web_dz: float = 1.0

    def __post_init__(self) -> None:
        if self.kind not in _PART_KINDS:
            raise ValueError(
                f"BodyPart.kind '{self.kind}' 은 허용 목록 "
                f"{sorted(_PART_KINDS)} 에 없음."
            )
        if self.subkind not in _PART_SUBKINDS:
            raise ValueError(
                f"BodyPart.subkind '{self.subkind}' 은 허용 목록 "
                f"{sorted(_PART_SUBKINDS)} 에 없음."
            )
        if self.length_mm <= 0 or self.width_mm <= 0 or self.height_mm <= 0:
            raise ValueError(
                f"BodyPart({self.kind}): length/width/height 는 양수여야 함."
            )


@dataclass(frozen=True)
class AttachedPart:
    """부모 화물에 종속된 *자식 컴포넌트의 부재 한 개* 박스.

    [데이터 흐름]
      자식(CantileverBeam/CantileverSlab/MidBeam/MidColumn) 의 부재 데이터
      (BeamData/ColumnData/SlabData) — 월드 좌표
        → 어댑터의 _nominal_aabb_*() 가 부모 nominal 좌표계로 변환
        → 운송 좌표계 매핑
        → 본 AttachedPart 1 개 생성
    CantileverSlab 은 3 변 보 + 슬래브 = 4 개의 AttachedPart 로 풀어 첨부.

    [좌표계] 부모 nominal 좌표계 (운송 X-Y 교환 적용 후) 의 박스.
    부모가 회전·앵커 변해도 nominal 변환이 풀어주므로 위치 불변.
    """

    kind: str            # _PART_KINDS — "column"|"beam"|"slab"|"wall_fill"
    source_kind: str     # _SOURCE_KINDS — 어느 자식 종류에서 왔는지
    x_mm: float
    y_mm: float
    z_mm: float
    length_mm: float
    width_mm: float
    height_mm: float
    subkind: str = ""
    section_name: str = ""
    # ── 보 단면 형상 (H형강 운송 렌더용, 2026-05-28) — BodyPart 와 동일 의미 ──
    section_type: str = "shs"
    web_dx: float = 0.0
    web_dy: float = 0.0
    web_dz: float = 1.0

    def __post_init__(self) -> None:
        if self.kind not in _PART_KINDS:
            raise ValueError(
                f"AttachedPart.kind '{self.kind}' 은 허용 목록 "
                f"{sorted(_PART_KINDS)} 에 없음."
            )
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError(
                f"AttachedPart.source_kind '{self.source_kind}' 은 허용 목록 "
                f"{sorted(_SOURCE_KINDS)} 에 없음."
            )
        if self.subkind not in _PART_SUBKINDS:
            raise ValueError(
                f"AttachedPart.subkind '{self.subkind}'."
            )
        if self.length_mm <= 0 or self.width_mm <= 0 or self.height_mm <= 0:
            raise ValueError(
                f"AttachedPart({self.kind}): length/width/height 는 양수여야 함."
            )


@dataclass(frozen=True)
class WallSegment:
    """플로어 패널의 한 변(또는 변의 일부)에 매달린 벽 세그먼트.

    한 변 전체가 벽이면 start_offset_mm=0, length_mm=변의 길이.
    변의 일부만 벽이면 start_offset_mm/length_mm 로 부분 채움 표현.
    여러 조각이면 같은 side 에 WallSegment 를 복수 등록.
    """

    side: int                   # 0=하변(y=0), 1=우변(x=L), 2=상변(y=W), 3=좌변(x=0)
    start_offset_mm: float      # 해당 변의 시작점으로부터의 거리 (mm)
    length_mm: float            # 세그먼트 길이 (mm)
    height_mm: float            # 벽 높이 (mm)
    thickness_mm: float         # 벽 두께 (mm) — B-2 분리: 바닥 두께와 독립
    column_section: Section     # 양쪽 기둥 단면
    beam_section: Section       # 상단 보 단면 (하단 보는 부모 floor 패널 보)

    def __post_init__(self) -> None:
        if self.side not in _SIDE_INDEXES:
            raise ValueError(
                f"WallSegment.side 값 {self.side} 은 0/1/2/3 중 하나여야 함."
            )
        if self.length_mm <= 0 or self.height_mm <= 0 or self.thickness_mm <= 0:
            raise ValueError("WallSegment: length/height/thickness 는 양수여야 함.")
        if self.start_offset_mm < 0:
            raise ValueError("WallSegment.start_offset_mm 은 0 이상이어야 함.")


@dataclass(frozen=True)
class Module:
    """수평/수직 모듈. 무게는 프레임 + extra_weight_kg 자동 합산.

    [B-11 정정]
    원본은 보 길이 합산을 8(w+l) 로 계산해 한 층에 보 16개로 잡았음. 정정
    식은 4(w+l) — 천장보 2(w+l) + 바닥보 2(w+l) = 4(w+l) — 으로 보 8개
    (4기둥 변 × 2 단) 의 총 길이.
    """

    name: str
    width: float                    # mm
    length: float                   # mm
    height: float                   # mm
    column_section: Section         # 4 모서리 기둥 (대표 단면 — 시각화·표시용)
    beam_section: Section           # 천장·바닥보 (대표 단면 — 시각화·표시용)
    extra_weight_kg: float = 0.0    # 슬래브 + 비내력벽 + Mid부재 합산
    # [2026-06-04] 부재별 (단면×길이) 합으로 구한 *정확한* 프레임 강재 무게(kg).
    #   기둥/보 단면이 한 모듈 안에서 혼재해도 부재마다 제 단면으로 합산한다.
    #   None 이면 대표 단면 기반 근사식(하위호환)으로 계산.
    frame_weight_kg: Optional[float] = None
    # Phase 1 — 종속 부재(중간보·중간기둥·캔틸레버) 형상 첨부
    attached_parts: Tuple["AttachedPart", ...] = field(default_factory=tuple)
    # Phase 3 — 부모 자체 부재(기둥·보·슬래브·채움) 의 실제 월드 AABB 데이터
    body_parts: Tuple["BodyPart", ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.length <= 0 or self.height <= 0:
            raise ValueError(f"Module '{self.name}': 치수는 양수여야 함.")
        if self.extra_weight_kg < 0:
            raise ValueError(f"Module '{self.name}': extra_weight_kg 음수 불가.")

    @property
    def weight(self) -> float:
        # 정확 프레임 무게가 주어지면(어댑터가 부재별 단면×길이 합산) 그대로 사용.
        if self.frame_weight_kg is not None:
            return self.frame_weight_kg + self.extra_weight_kg
        # 폴백 — 대표 단면 기반 근사식(보·기둥 단일 단면 가정).
        col_total_m = (4 * self.height) / 1000.0
        # B-11 정정: 원본 `4*(w+l)*2` → `4*(w+l)`.
        # 4(w+l) = 천장 2(w+l) + 바닥 2(w+l), 즉 보 8개의 총 길이.
        beam_total_m = (4 * (self.width + self.length)) / 1000.0
        frame_w = (
            col_total_m * self.column_section.weight_per_m
            + beam_total_m * self.beam_section.weight_per_m
        )
        return frame_w + self.extra_weight_kg


@dataclass(frozen=True)
class Panel:
    """플로어/벽체 패널 (다면 종속 일반화 모델).

    [kind 별 의미]
    - "floor": 바닥 패널. wall_segments 가 0 개면 순수 floor, 1 개면 L 자
      (운송프로그램 원본 lshape 의 일반화), 2 개면 ㄷ 자, 3/4 개면 3·4 면.
    - "wall":  독립 벽 패널 (FloorPanel 종속 아님). wall_segments 미사용.
    - "lshape": 원본 운송프로그램 호환용 — 신규 코드는 사용하지 않는다.
      어댑터는 kind="floor" + wall_segments 1 개 로 생성한다.

    [B-2 정정] 원본은 lshape 의 바닥 두께와 벽 두께를 단일 `thickness`
    필드로 표현 → 이중 의미 충돌. WallSegment.thickness_mm 로 벽 두께를
    분리해 본 클래스의 `thickness` 는 바닥 두께만 의미하도록 정리.
    """

    name: str
    kind: PanelKind
    width: float                          # mm (X 방향 — 부재 폭)
    length: float                         # mm (Y 방향 — 부재 길이)
    thickness: float                      # mm (floor: 슬래브 두께 / wall: 벽 두께)
    beam_section: Section                 # floor: 둘레 보 / wall: 위·아래 보
    column_section: Section | None = None # wall 전용 (양쪽 기둥)
    wall_height: float = 0.0              # wall 전용 — 벽 부분 높이
    extra_weight_kg: float = 0.0          # 비내력벽 채움재 + 슬래브 자중
    # A-1: 다면 종속 표현용. floor 에서만 사용. 0~4 개.
    wall_segments: Tuple[WallSegment, ...] = field(default_factory=tuple)
    # Phase 1 — 종속 부재(캔틸레버 보·슬래브) 형상 첨부 (종속 floor 에서만 의미)
    attached_parts: Tuple["AttachedPart", ...] = field(default_factory=tuple)
    # Phase 3 — 부모 자체 부재(둘레보·슬래브 / 벽 기둥·런너·채움) 의 월드 AABB 데이터
    body_parts: Tuple["BodyPart", ...] = field(default_factory=tuple)
    # [2026-06-04 층묶음 복제] 원본 컴포넌트의 층/그룹 식별자. 어댑터가 생성 시점에
    #   comp.floor_index·comp.group_id 를 실어 보낸다. 운송 패커가 패널을 3층 묶음으로
    #   분류해 기준 묶음만 계산하고 나머지 층을 복제하는 데 사용. -1 = 미주입(기존 동작).
    floor_index: int = -1
    group_id: int = -1

    def __post_init__(self) -> None:
        if self.kind not in _PANEL_KINDS:
            raise ValueError(
                f"Panel.kind 값 '{self.kind}' 은 허용 목록 "
                f"{sorted(_PANEL_KINDS)} 에 없음."
            )
        if self.width <= 0 or self.length <= 0 or self.thickness <= 0:
            raise ValueError(f"Panel '{self.name}': 치수는 양수여야 함.")
        if self.extra_weight_kg < 0:
            raise ValueError(f"Panel '{self.name}': extra_weight_kg 음수 불가.")
        if self.kind in ("wall", "lshape") and self.column_section is None:
            raise ValueError(
                f"Panel '{self.name}': kind={self.kind} 는 column_section 필요."
            )
        if self.kind == "wall" and self.wall_segments:
            raise ValueError(
                f"Panel '{self.name}': kind=wall 은 wall_segments 미사용."
            )
        if self.wall_segments and self.kind == "floor":
            # 같은 side 중복은 허용 (변 일부 채움 다조각), 다만 합계 검사
            for seg in self.wall_segments:
                if seg.start_offset_mm + seg.length_mm > self._side_length(seg.side) + 1e-6:
                    raise ValueError(
                        f"Panel '{self.name}': wall_segment side={seg.side} "
                        f"가 변 길이 {self._side_length(seg.side)} 를 초과."
                    )

    def _side_length(self, side: int) -> float:
        """변 인덱스 → 변의 길이 (mm). 0/2 변 → 길이(length), 1/3 변 → 폭(width)."""
        return self.length if side in (0, 2) else self.width

    @property
    def weight(self) -> float:
        """[주의] Phase 1 은 운송프로그램 원본 식 그대로 보존.

        다면 종속(wall_segments 다수) 경우의 정밀 합산은 Phase 2 packer/
        adapter 에서 일반화 식으로 확장 예정. 본 메서드는 단일 종속 (L 자)
        또는 0 종속 (순수 floor) 까지만 정확.
        """
        if self.kind == "lshape" and self.column_section is not None:
            beam_total_m = (3 * self.width + 2 * self.length) / 1000.0
            col_total_m = (2 * self.wall_height) / 1000.0
            frame_w = (
                beam_total_m * self.beam_section.weight_per_m
                + col_total_m * self.column_section.weight_per_m
            )
            return frame_w + self.extra_weight_kg
        if self.kind == "wall" and self.column_section is not None:
            beam_total_m = (2 * self.length) / 1000.0
            col_total_m = (2 * self.width) / 1000.0
            frame_w = (
                beam_total_m * self.beam_section.weight_per_m
                + col_total_m * self.column_section.weight_per_m
            )
            return frame_w + self.extra_weight_kg
        if self.kind == "floor" and self.wall_segments:
            # A-1 일반화: 둘레 보 4 개 + 각 세그먼트별 (상단 보 + 양쪽 기둥)
            beam_total_m = (2 * (self.width + self.length)) / 1000.0
            frame_w = beam_total_m * self.beam_section.weight_per_m
            for seg in self.wall_segments:
                seg_beam_m = seg.length_mm / 1000.0
                seg_col_m = (2 * seg.height_mm) / 1000.0
                frame_w += (
                    seg_beam_m * seg.beam_section.weight_per_m
                    + seg_col_m * seg.column_section.weight_per_m
                )
            return frame_w + self.extra_weight_kg
        # 순수 floor: 둘레 보 4 개
        beam_total_m = (2 * (self.width + self.length)) / 1000.0
        return beam_total_m * self.beam_section.weight_per_m + self.extra_weight_kg


@dataclass(frozen=True)
class Truck:
    """모듈러 운송 차량.

    [원본과의 추가 필드]
    - curb_weight_kg : 차체 자체 중량. 현장 총중량(GVW) 한도 검사에 화물 무게와
      합산해 사용 (limits.can_carry).
    - active : 패킹 후보 포함 여부 (B-4 보존 정책).

    [truck_type 정책]
    - lowbed: 저상 트레일러. 모듈·플로어 패널.
    - extendable: 확장형 광폭. 광폭 모듈 가능.
    - aframe: A-frame 트레일러. 벽체 패널 세워서 (원본 _wall_panel_compatible_trucks
      목록에서 빠져 있어 사실상 데드 데이터. active=False 권장).
    """

    name: str
    truck_type: TruckType
    max_length: float
    max_width: float
    max_height: float
    max_weight: float
    vehicle_height_offset: float = 700.0
    # ── 신규 정밀화 필드 ─────────────────────────────────
    curb_weight_kg: float = 0.0
    active: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        # B-15: Literal 런타임 검증.
        if self.truck_type not in _TRUCK_TYPES:
            raise ValueError(
                f"Truck.truck_type 값 '{self.truck_type}' 은 허용 목록 "
                f"{sorted(_TRUCK_TYPES)} 에 없음."
            )
        for k in ("max_length", "max_width", "max_height", "max_weight"):
            if getattr(self, k) <= 0:
                raise ValueError(f"Truck '{self.name}': {k} 는 양수여야 함.")
        if self.vehicle_height_offset < 0:
            raise ValueError(
                f"Truck '{self.name}': vehicle_height_offset 음수 불가."
            )
        if self.curb_weight_kg < 0:
            raise ValueError(f"Truck '{self.name}': curb_weight_kg 음수 불가.")


@dataclass(frozen=True)
class SiteLimit:
    """현장 운송 제한 — 공장→현장 경로(진입로 포함)가 허용하는 한도.

    각 항목이 None 이면 "해당없음"(그 항목은 제한하지 않음 = 프리패스).
    - max_gvw_kg   : 차체 자체 무게 + 화물 무게(총중량 GVW) 한도. 트럭 적재능력
      (Truck.max_weight, 화물 단독) 과는 별개로 함께 검사한다.
    - max_width_mm : 화물 폭 한도.
    - max_height_mm: 지면에서 화물 꼭대기까지의 총높이 한도(차량 적재면 높이 포함).

    [도로 등급 카탈로그 폐지 — 2026-05-26]
    명명된 도로 등급(광로/일반/이면) 선택을 없애고, 사용자가 현장에 맞춰
    직접 넣는 본 3개 한도로 대체한다. 길이는 현장 제한을 두지 않고 트럭
    적재함 길이로만 본다.
    """

    max_gvw_kg: Optional[float] = None
    max_width_mm: Optional[float] = None
    max_height_mm: Optional[float] = None

    def __post_init__(self) -> None:
        for k in ("max_gvw_kg", "max_width_mm", "max_height_mm"):
            v = getattr(self, k)
            if v is not None and v <= 0:
                raise ValueError(
                    f"SiteLimit.{k} 는 None(해당없음) 또는 양수여야 함."
                )


@dataclass(frozen=True)
class SpacingParams:
    """패널 적재 간격 (mm). 2026-05-26 이후 사용자 입력이 아닌 내장 고정값.

    - panel_gap_mm: 패널 사이 수직(적층)·수평(나란히) 간격
    - truck_edge_clearance_mm: 트럭 앞뒤 결박 여유(길이 방향)
    - lshape_stack_gap_mm: 종속(L자) 패널 위 적층 수평 간격
    - side_overhang_mm: 화물이 트럭 폭보다 양쪽으로 각각 튀어나올 수 있는 여유
      (폭 허용 = 트럭 폭 + 2×side_overhang_mm)
    """

    panel_gap_mm: float = 100.0
    truck_edge_clearance_mm: float = 200.0
    lshape_stack_gap_mm: float = 100.0
    side_overhang_mm: float = 200.0

    def __post_init__(self) -> None:
        for k in ("panel_gap_mm", "truck_edge_clearance_mm",
                  "lshape_stack_gap_mm", "side_overhang_mm"):
            if getattr(self, k) < 0:
                raise ValueError(f"SpacingParams.{k} 음수 불가.")


__all__ = [
    "PanelKind", "TruckType", "SectionType",
    "Section", "WallSegment", "AttachedPart", "BodyPart", "Module", "Panel",
    "Truck", "SiteLimit", "SpacingParams",
]
