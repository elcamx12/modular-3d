"""공유 해석모델(am) 제공자 — 토폴로지 중복 빌드 단일화 (3단계 첫 조각).

[배경]
같은 scene 토폴로지를 해석·단면설계·접합뷰·공정 4곳이 각자
build_analysis_model 로 따로 빌드해 왔다(탭 데이터흐름 지도 ②). 같은 입력을
4번 계산하는 낭비이자, "단일 진실원천 부재"의 출발점.

[설계]
get_analysis_model(scene) 하나로 통일. compute_scene_fp(scene) 지문으로 캐싱:
지문이 같으면 직전 am 재사용, 다르면 build_analysis_model 1회 후 캐시.
am(토폴로지)은 joint_overrides·정책·단면과 무관하므로 키가 scene_fp 만으로
충분하다(지도 ③: joint_overrides 는 OPS 단계서만 반영). → 안전·단순.

[함정 — 지연 청소(lazy clean) 대응]
build_ops_model 은 받은 am 을 *복사 없이 그 자리에서* 변형한다(분절 노드
>=SPLIT_NODE_BASE_OFFSET, is_split_sub 보조부재 추가). 청소는 함수 끝이 아니라
*다음 build_ops_model 시작 맨 앞*에서 일어난다. 따라서 빌드를 거치지 않고
am 을 직접 순회하는 소비자는 직전 빌드의 분절 잔여물을 볼 수 있다.
→ 제공자는 *반환 직전 항상* 분절 잔여물을 비우고 인덱스를 무효화해서,
   받는 쪽이 빌드를 거치든 직접 읽든 언제나 깨끗한 토폴로지를 보장한다.
   (빌드 경로는 어차피 시작 때 또 비우므로 동작 불변.)

[폴백]
제공자 호출에 실패하면 호출처가 기존 build_analysis_model 직접호출로
폴백하도록 한다(동작 보존). 제공자 자체는 예외를 숨기지 않고 전파한다.
"""
from __future__ import annotations

from modular_3d.카탈로그.geometry import SPLIT_NODE_BASE_OFFSET

# ── 캐시 (단일 슬롯) ──────────────────────────────────────────
# 메인 self._scene 은 단일 객체라 슬롯 1개로 충분. 비교탭의 오프라인 scene 등
# *다른* scene 으로 호출되면 슬롯이 교체된다(지문이 다르므로). 메인 scene 을
# 다시 요청하면 재빌드되지만, 첫 조각은 정확·안전을 캐시 적중률보다 우선한다.
_CACHE_FP: str | None = None
_CACHE_AM = None


def _clean_split_residue(am) -> None:
    """분절 노드(>=10000)·분절 보조부재 잔여물 제거 + 인덱스 무효화.

    build_ops_model 의 지연 청소 로직과 동일한 정리를, 반환 직전 선제 수행.
    """
    split_nids = [nid for nid in am.nodes if nid >= SPLIT_NODE_BASE_OFFSET]
    for nid in split_nids:
        del am.nodes[nid]
    split_mids = [mid for mid, m in am.members.items()
                  if getattr(m, 'is_split_sub', False)]
    for mid in split_mids:
        del am.members[mid]
    if hasattr(am, 'invalidate_indices'):
        am.invalidate_indices()


def get_analysis_model(scene):
    """scene 토폴로지의 공유 해석모델(am)을 반환. 지문 캐시 + 반환 전 청소.

    같은 scene 구성(compute_scene_fp 지문)이면 직전 am 을 재사용하고,
    달라졌으면 새로 빌드한다. 반환 직전 항상 분절 잔여물을 비운다.
    """
    global _CACHE_FP, _CACHE_AM
    # 지연 import — 모듈 로드 시점 순환/비용 회피(ops_builder 패턴 준수).
    from modular_3d.transport.cache import compute_scene_fp
    from modular_3d.analysis.topology import build_analysis_model

    fp = compute_scene_fp(scene)
    if _CACHE_AM is not None and _CACHE_FP == fp:
        am = _CACHE_AM
    else:
        am = build_analysis_model(scene)
        _CACHE_FP = fp
        _CACHE_AM = am
    _clean_split_residue(am)
    return am


def invalidate() -> None:
    """캐시 강제 폐기 — 진단·테스트용. 정상 흐름은 지문 변화로 자동 폐기."""
    global _CACHE_FP, _CACHE_AM
    _CACHE_FP = None
    _CACHE_AM = None


__all__ = ['get_analysis_model', 'invalidate']
