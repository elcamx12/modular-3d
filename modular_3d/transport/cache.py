"""운송 파이프라인 8단계 캐시 (분석 ⑧).

[파이프라인]
[1] 씬 편집  →  [2] 구조해석  →  [3] 단면 산정  →  [4] 층구간 분할
  →  [5] 비내력벽 분류  →  [6] 어댑터 변환  →  [7] 패킹  →  [8] 운임/시각화

각 단계는 입력 fingerprint(fp) 를 캐시 키로 한다. 옵션을 살짝 바꿔도 그 위
단계까지는 캐시 재사용, 아래 단계만 다시 계산.

[사용자 결정 ⑧-1·⑧-2·⑧-3·⑧-4]
1. 자동 재계산 OFF 기본 — 본 모듈은 캐시만 책임지고 자동 실행 정책은
   운송탭 UI(Phase 6) 가 결정.
2. 디바운스 500ms — UI 측 책임.
3. 8단계 상태 인디케이터 — `cache_status_flags()` 가 단계별 hit/miss 반환.
4. 정책별 design 캐시 1개만 — `design_results` dict 가 현재 policy 키만 보관.

[비싼 단계 보호]
[2] 구조해석은 분 단위라 자동 트리거하지 않음. 본 모듈은 캐시만 두고,
호출자가 명시적으로 ops_results 를 set_analysis_result() 로 주입한다.

[fingerprint]
- scene_fp: 컴포넌트 (cid, type, dimensions, position, rotation, anchor,
  merged_*) 직렬화 → SHA-256.
- classify_fp: scene_fp + (gap_mm, eps_mm, segment_size_mm, include_floor_panels).
- ti_fp: seg_fp(policy, n) + classify_fp + transport_options_hash.
- pack_fp: ti_fp + trucks_fp + site(현장 제한) + spacing.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .adapter import TransportInput, TransportOptions
from .economics import EconomicsOptions, EconomicsResult, compute_economics
from .models import SiteLimit, SpacingParams, Truck
from .packer import PackResult, pack_items


# ── fingerprint 유틸 ──────────────────────────────────────
def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _coerce(v: Any) -> Any:
    """JSON 직렬화용 변환 — numpy/타입 안전."""
    if hasattr(v, "tolist"):
        return v.tolist()
    if hasattr(v, "value"):  # Enum
        return v.value
    return v


def compute_scene_fp(scene) -> str:
    """씬의 컴포넌트 구성으로 fingerprint 산출.

    포함: id, type, dimensions, position, rotation, anchor, merged_fp_id,
          merged_wall_ids, parent_id, anchor_edge_id.
    포함하지 않음: 캐싱 영향 없는 보조 필드(joint_records 좌표 등).
    """
    if scene is None or not hasattr(scene, "components"):
        return _sha256("None")
    items = []
    for cid, comp in sorted(scene.components.items()):
        items.append({
            "cid": cid,
            "type": _coerce(getattr(comp, "comp_type", None)),
            "dims": {k: _coerce(v) for k, v in getattr(comp, "dimensions", {}).items()},
            "pos": _coerce(getattr(comp, "position", None)),
            "rot": int(getattr(comp, "rotation", 0)),
            "anchor": int(getattr(comp, "anchor", 0)),
            "merged_fp": getattr(comp, "merged_fp_id", None),
            "merged_walls": list(getattr(comp, "merged_wall_ids", []) or []),
            "parent": int(getattr(comp, "parent_id", 0) or 0),
            "anchor_edge": int(getattr(comp, "anchor_edge_id", -1) or -1),
        })
    return _sha256(json.dumps(items, sort_keys=True, default=str))


def compute_classify_fp(scene_fp: str, classifier_opts) -> str:
    return _sha256(scene_fp + "|" + json.dumps({
        "gap_mm": getattr(classifier_opts, "gap_mm", 20.0),
        "eps_mm": getattr(classifier_opts, "eps_mm", 5.0),
        "segment_size_mm": getattr(classifier_opts, "segment_size_mm", 100.0),
        "include_floor_panels": getattr(classifier_opts, "include_floor_panels", True),
        "enabled": getattr(classifier_opts, "enabled", True),
    }, sort_keys=True))


def compute_seg_fp(design_fp: str, n_segments: int) -> str:
    return _sha256(f"{design_fp}|n_seg={n_segments}")


def compute_ti_fp(seg_fp: str, classify_fp: str, options: TransportOptions) -> str:
    opt = {
        "include_cantilever": options.include_cantilever,
        "wall_classifier_enabled": options.wall_classifier_enabled,
        "interior_wall_unit_weight": options.interior_wall_unit_weight,
        "exterior_wall_unit_weight": options.exterior_wall_unit_weight,
        "wall_segment_size_mm": options.wall_segment_size_mm,
        "include_floor_panels_as_obstacle": options.include_floor_panels_as_obstacle,
        "floor_slab_thickness_mm": options.floor_slab_thickness_mm,
        "concrete_density_kg_m3": options.concrete_density_kg_m3,
        "treat_v3_module_as_lying": options.treat_v3_module_as_lying,
        "mid_member_inclusion": options.mid_member_inclusion,
    }
    return _sha256(seg_fp + "|" + classify_fp + "|" + json.dumps(opt, sort_keys=True))


def compute_trucks_fp(trucks: Iterable[Truck]) -> str:
    items = []
    for t in trucks:
        items.append({
            "name": t.name, "type": t.truck_type,
            "L": t.max_length, "W": t.max_width, "H": t.max_height,
            "Wt": t.max_weight, "offset": t.vehicle_height_offset,
            "curb": t.curb_weight_kg,
            "active": t.active,
        })
    items.sort(key=lambda x: x["name"])
    return _sha256(json.dumps(items, sort_keys=True))


def compute_pack_fp(
    ti_fp: str, trucks_fp: str, site: SiteLimit, spacing: SpacingParams,
    economics=None,
) -> str:
    """패킹 캐시 키 — 입력 변경 시 자동 무효화.

    [2026-05-27] economics 옵션 (cost_mode, 단가, 거리) 도 포함 — 사용자가
    UI 에서 운임 방식 / 단가 / 거리를 바꾸면 *패킹도 재계산*. 종전엔 economics
    가 빠져 있어 운임만 갱신되고 트럭 선정이 stale 한 상태로 남았음.
    """
    site_dict = {"gvw": site.max_gvw_kg, "W": site.max_width_mm,
                 "H": site.max_height_mm}
    sp = {"gap": spacing.panel_gap_mm, "edge": spacing.truck_edge_clearance_mm,
          "lstack": spacing.lshape_stack_gap_mm}
    eco_dict = {}
    if economics is not None:
        eco_dict = {
            "mode": economics.cost_mode,
            "dist": economics.distance_km,
            "lkm": economics.lowbed_per_km_krw,
            "ekm": economics.extendable_per_km_krw,
            "akm": economics.aframe_per_km_krw,
            "lf": economics.lowbed_fixed_krw,
            "ef": economics.extendable_fixed_krw,
            "af": economics.aframe_fixed_krw,
        }
    return _sha256(ti_fp + "|" + trucks_fp + "|"
                   + json.dumps({"site": site_dict, "sp": sp, "eco": eco_dict},
                                sort_keys=True))


# ── 메인 캐시 데이터클래스 ────────────────────────────────
@dataclass
class TransportCache:
    """운송 파이프라인 8단계 lazy memoization (분석 ⑧).

    [필드 분류]
    - fingerprint 그룹: 각 단계의 입력 해시. 입력 변경 감지에 사용.
    - 결과 그룹: 각 단계 출력 캐시. 다음 호출 시 fp 일치 시 그대로 반환.

    [무효화 규약]
    invalidate_from(step) — step 이상의 모든 캐시 무효. 예: 단위중량 변경 →
    invalidate_from(6). 정책 변경 → invalidate_from(3).
    """
    # ── fingerprints ───────────────────────────────────
    scene_fp: str = ""
    analysis_fp: str = ""
    design_fp: Dict[str, str] = field(default_factory=dict)              # policy → fp
    seg_fp: Dict[Tuple[str, int], str] = field(default_factory=dict)
    classify_fp: str = ""
    ti_fp: str = ""
    pack_fp: str = ""

    # ── 결과 캐시 ──────────────────────────────────────
    analysis_result: Optional[Any] = None
    design_results: Dict[str, Any] = field(default_factory=dict)          # policy → DesignResult
    seg_results: Dict[Tuple[str, int], Any] = field(default_factory=dict)
    classify_result: Optional[Any] = None
    transport_input: Optional[TransportInput] = None
    pack_result: Optional[PackResult] = None
    economics_result: Optional[EconomicsResult] = None

    # ── 정책별 design 캐시 정책 (사용자 결정 ⑧-4): 1개만 보관 ──
    current_policy: str = ""

    def invalidate_from(self, step: int) -> None:
        """step 이상의 모든 캐시 무효 (1~8)."""
        if step <= 2:
            self.analysis_result = None
            self.analysis_fp = ""
        if step <= 3:
            self.design_results.clear()
            self.design_fp.clear()
            self.current_policy = ""
        if step <= 4:
            self.seg_results.clear()
            self.seg_fp.clear()
        if step <= 5:
            self.classify_result = None
            self.classify_fp = ""
        if step <= 6:
            self.transport_input = None
            self.ti_fp = ""
        if step <= 7:
            self.pack_result = None
            self.pack_fp = ""
        if step <= 8:
            self.economics_result = None

    def cache_status_flags(self) -> Dict[int, str]:
        """8단계 상태 (UI 상태바용).

        ✓ hit, ✗ stale/미실행. 회색은 UI 측에서 결과 객체 None 여부로 분기.
        """
        flags: Dict[int, str] = {}
        flags[2] = "✓" if self.analysis_result is not None else "✗"
        flags[3] = "✓" if self.design_results else "✗"
        flags[4] = "✓" if self.seg_results else "✗"
        flags[5] = "✓" if self.classify_result is not None else "✗"
        flags[6] = "✓" if self.transport_input is not None else "✗"
        flags[7] = "✓" if self.pack_result is not None else "✗"
        flags[8] = "✓" if self.economics_result is not None else "✗"
        return flags

    # ── 단계별 set/get (호출자가 직접 사용) ──────────────
    def set_analysis_result(self, scene_fp: str, ops_results) -> None:
        """[2] 구조해석 결과 주입 (UI '구조해석 실행' 버튼 → 호출).

        구조해석은 비싸므로 본 모듈이 자동 트리거하지 않고 외부에서 결과를
        주입받는다. scene_fp 가 일치하지 않으면 stale 표시.
        """
        self.invalidate_from(2)
        self.scene_fp = scene_fp
        self.analysis_fp = scene_fp  # 단순화: 동일 사용
        self.analysis_result = ops_results

    def get_or_compute_pack(
        self,
        scene,
        model,
        design_result,
        policy: str,
        options: TransportOptions,
        trucks: List[Truck],
        site: SiteLimit,
        spacing: SpacingParams,
        classifier_opts=None,
        economics: Optional[EconomicsOptions] = None,
    ) -> PackResult:
        """파이프라인 [5]→[7] 진입점.

        호출 전 [2]·[3]·[4] 는 호출자가 채워둬야 함 (구조해석/단면 산정).
        본 메서드는 어댑터([6]) + 패커([7]) 만 lazy 계산.

        [캐시 hit/miss]
        - classify_fp 일치 → wall_classifier 재사용 (어댑터 내부에서 호출되므로 본
          캐시에 직접 저장은 안 함, ti_fp 만으로 충분).
        - ti_fp 일치 → transport_input 재사용.
        - pack_fp 일치 → pack_result 즉시 반환.
        """
        from .adapter import build_transport_input
        from .wall_classifier import ClassifierOptions

        # scene fp 갱신 (변경 감지)
        new_scene_fp = compute_scene_fp(scene)
        if new_scene_fp != self.scene_fp:
            # 씬 변경 → [5] 부터 무효
            self.scene_fp = new_scene_fp
            self.invalidate_from(5)

        # classify fp
        if classifier_opts is None:
            classifier_opts = ClassifierOptions(
                segment_size_mm=options.wall_segment_size_mm,
                include_floor_panels=options.include_floor_panels_as_obstacle,
                enabled=options.wall_classifier_enabled,
            )
        new_classify_fp = compute_classify_fp(new_scene_fp, classifier_opts)
        if new_classify_fp != self.classify_fp:
            self.classify_fp = new_classify_fp
            self.classify_result = "classified"  # 표식만 (실제 결과는 어댑터 내부)
            self.invalidate_from(6)

        # ti_fp
        # design_fp 단순화: design_result 의 id 사용 (정밀 fp 는 호출자가 set)
        if not self.design_fp.get(policy):
            self.design_fp[policy] = f"design_{id(design_result)}"
            self.current_policy = policy
            self.design_results[policy] = design_result
        seg_key = (policy, 1)  # n_segments 는 호출자 design_result 에 이미 반영
        if seg_key not in self.seg_fp:
            self.seg_fp[seg_key] = compute_seg_fp(self.design_fp[policy], 1)
            self.seg_results[seg_key] = design_result

        new_ti_fp = compute_ti_fp(self.seg_fp[seg_key], new_classify_fp, options)
        if new_ti_fp != self.ti_fp or self.transport_input is None:
            self.ti_fp = new_ti_fp
            self.transport_input = build_transport_input(
                scene, model, design_result, policy, options,
            )
            self.invalidate_from(7)

        # pack_fp — economics 포함 (UI 비용 옵션 변경 시 자동 재계산)
        trucks_fp = compute_trucks_fp(trucks)
        new_pack_fp = compute_pack_fp(self.ti_fp, trucks_fp, site, spacing,
                                        economics=economics)
        if new_pack_fp != self.pack_fp or self.pack_result is None:
            self.pack_fp = new_pack_fp
            self.pack_result = pack_items(
                self.transport_input.modules, self.transport_input.panels,
                trucks, site, spacing,
                economics=economics,
            )
            self.invalidate_from(8)
        return self.pack_result

    def get_or_compute_economics(
        self, eco_options: EconomicsOptions,
    ) -> EconomicsResult:
        """[8] 운임 계산. pack_result 가 있어야 호출 가능."""
        if self.pack_result is None:
            raise RuntimeError("pack_result 가 없습니다 — get_or_compute_pack 먼저 호출.")
        # economics 는 비용 매우 작아 fp 비교 생략, 항상 재계산.
        self.economics_result = compute_economics(self.pack_result, eco_options)
        return self.economics_result


__all__ = [
    "TransportCache",
    "compute_scene_fp", "compute_classify_fp", "compute_seg_fp",
    "compute_ti_fp", "compute_trucks_fp", "compute_pack_fp",
]
