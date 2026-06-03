"""부재 타입 분류 — '모듈A-1' 라벨 (표시 전용, 구조 계산과 무관).

[규칙 — 2026-05-30 사용자 확정]
- 대상: *독립* 부재(모듈 / 바닥패널 / 벽패널 / 수직 3층 모듈)의 본체(sub_index==0).
  (합체 벽패널·종속 부재는 자체 타입을 안 받고 부모 구성에 포함된다.)
- 1차 글자: 외형 치수(width·depth·height, 5mm 허용오차)가 다르면 A, B, …
- 2차 번호: 같은 글자(크기) 안에서 '구성 시그니처'(실 배치 + 종속 부재)별 -1, -2, …
  · 같은 xy·층만 다른 복제, 또는 위치는 달라도 실배치까지 같은 부재 → 같은 번호로 통합.
  · 크기는 같지만 실배치가 다르면 -1, -2 로 분리.
- 표기: f'{타입명}{글자}-{번호}'  (예: '모듈A-1').

[주의] 3차 상세(A-1-1, 단면 설계 결과 반영)는 물량 단계에서 별도로 덧붙인다(여기선 미포함).
계산 축(역할 카테고리 그룹화)과는 완전히 분리 — 본 라벨은 트리/패널 표시에만 쓴다.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict

from modular_3d.model import ComponentType, TYPE_NAMES
from modular_3d._utils.format import alpha_label


_TOL = 5.0  # mm — 외형 치수·좌표 비교 허용오차

# 자체 타입을 받는 독립 부재
_INDEP_TYPES = {
    ComponentType.MODULE,
    ComponentType.FLOOR_PANEL,
    ComponentType.STRUCT_WALL,
    ComponentType.VERTICAL_MODULE,
}


def _q(v: float) -> float:
    """5mm 단위 양자화 — 허용오차 안 차이를 같은 값으로 본다."""
    return round(float(v) / _TOL) * _TOL


def _size_key(comp) -> tuple:
    """외형 치수 키 — [회전대칭] 폭·깊이는 정렬(min,max)해 90° 회전(폭↔깊이 교환)을
    같은 크기로 본다. 높이는 그대로."""
    d = comp.dimensions
    w = _q(d.get('width', 0.0))
    dp = _q(d.get('depth', 0.0))
    h = _q(d.get('height', 0.0))
    return (min(w, dp), max(w, dp), h)


def _rot2d_int(x: float, y: float, deg: int):
    """90° 배수 정수 회전(부동소수 드리프트 없음). 반시계 +."""
    deg %= 360
    if deg == 90:
        return (-y, x)
    if deg == 180:
        return (-x, -y)
    if deg == 270:
        return (y, -x)
    return (x, y)


# D4 (정사각형 대칭군) — 회전 4 + 반사 4. 각 (변환함수, 축교환여부).
# 축교환(swap)=True 면 종속부재의 width↔depth 도 바뀐다.
_D4 = [
    (lambda x, y: (x, y), False),
    (lambda x, y: (-y, x), True),
    (lambda x, y: (-x, -y), False),
    (lambda x, y: (y, -x), True),
    (lambda x, y: (x, -y), False),    # x축 대칭
    (lambda x, y: (-x, y), False),    # y축 대칭
    (lambda x, y: (y, x), True),      # 대각 대칭
    (lambda x, y: (-y, -x), True),    # 반대각 대칭
]


def _canon_deps(deps) -> tuple:
    """종속부재(로컬 상대좌표) 목록을 D4(회전+대칭) 정준화 — 8 변환 중 최소 튜플.

    회전대칭·x/y 대칭 배치를 같은 시그니처로 본다. deps 원소 = (type, x, y, w, d, h).
    """
    if not deps:
        return ()
    best = None
    for fn, swap in _D4:
        items = []
        for (typ, x, y, w, d, h) in deps:
            nx, ny = fn(x, y)
            nw, nd = (d, w) if swap else (w, d)
            items.append((typ, _q(nx), _q(ny), _q(nw), _q(nd), _q(h)))
        cand = tuple(sorted(items))
        if best is None or cand < best:
            best = cand
    return best


def _opening_key(o, width: float, depth: float) -> tuple:
    """개구부 1건을 회전·대칭(D4) 불변 키로. [벽 개구부 통일]

    - 벽 개구부: 벽 방향 클래스(폭벽/깊이벽; 면 인덱스 짝/홀 관례) + '가까운 끝에서의
      거리'(min(u, len-u-w)) + 높이/크기 → 좌우 대칭·평행벽(180°)·회전 모두 동일.
    - 슬래브 개구부: 개구부 중심을 모듈 중심 기준 |cx|,|cy| 정렬 → D4 불변.
    """
    face = str(o.get('face', ''))
    u = float(o.get('u', 0.0) or 0.0)
    v = float(o.get('v', 0.0) or 0.0)
    w = float(o.get('w', 0.0) or 0.0)
    h = float(o.get('h', 0.0) or 0.0)
    if face.startswith('wall_'):
        try:
            idx = int(face.split('_')[1])
        except Exception:
            idx = 0
        wall_len = width if (idx % 2 == 0) else depth
        cls = 'w' if (idx % 2 == 0) else 'd'   # 폭방향/깊이방향 벽(평행벽 동일시)
        pos = min(u, max(wall_len - u - w, 0.0))   # 가까운 끝에서 거리(거울 불변)
        return ('wall', cls, _q(pos), _q(v), _q(w), _q(h))
    if face == 'slab':
        cx = abs(u + w / 2.0 - width / 2.0)
        cy = abs(v + h / 2.0 - depth / 2.0)
        a, b = sorted((_q(cx), _q(cy)))
        return ('slab', a, b, _q(min(w, h)), _q(max(w, h)))
    return ('other', face, _q(u), _q(v), _q(w), _q(h))


def _poly_area(poly) -> float:
    """폴리곤 면적(shoelace, 절댓값)."""
    n = len(poly)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def _signature(comp, scene) -> tuple:
    """구성 시그니처 — 종속 부재 + 실 배치를 *모듈 상대 좌표* 로 모은 정렬 튜플.

    위치(절대 xy)에 무관 — 같은 구성이면 어디에 있든 같은 시그니처가 되어
    같은 번호로 통합된다. 회전이 다르면 다른 구성으로 본다(rot 포함).
    """
    px = float(comp.position[0])
    py = float(comp.position[1])
    rot = int(getattr(comp, 'rotation', 0))
    gid = int(getattr(comp, 'group_id', 0) or 0)
    fi = int(getattr(comp, 'floor_index', 0))

    # ── 종속 부재 (같은 group_id, sub_index>0) — *모듈 로컬* 상대 위치·치수 ──
    #   [회전대칭] 월드 상대좌표는 회전 시 달라지므로, comp.rotation 을 풀어 모듈 로컬
    #   좌표로 정규화한다. 이후 _canon_deps 가 D4(회전+대칭) 최소로 정준화.
    deps = []
    if gid:
        for c2 in scene.components.values():
            if int(getattr(c2, 'group_id', 0) or 0) != gid:
                continue
            if int(getattr(c2, 'sub_index', 0)) == 0:
                continue
            dd = c2.dimensions
            lx, ly = _rot2d_int(float(c2.position[0]) - px,
                                float(c2.position[1]) - py, -rot)
            deps.append((
                getattr(c2.comp_type, 'value', str(c2.comp_type)),
                lx, ly,
                float(dd.get('width', 0.0)),
                float(dd.get('depth', 0.0)),
                float(dd.get('height', 0.0)),
            ))
    # [2026-06-01] 흡수된 벽패널(merged_wall_ids) — sub_index=0·다른 group_id 라
    #   위 루프에 안 잡힌다. 그러나 "벽이 붙은 패널 vs 안 붙은 패널"은 다른 타입이어야
    #   하므로(사용자), merged_wall_ids 로 직접 찾아 종속 구성에 포함한다.
    for wid in (getattr(comp, 'merged_wall_ids', None) or []):
        w = scene.components.get(wid)
        if w is None:
            continue
        wd = w.dimensions
        wlx, wly = _rot2d_int(float(w.position[0]) - px,
                              float(w.position[1]) - py, -rot)
        deps.append((
            'merged_wall',
            wlx, wly,
            float(wd.get('width', 0.0)),
            float(wd.get('depth', 0.0)),
            float(wd.get('height', 0.0)),
        ))

    # ── 실(Room) — 같은 층 + 모듈 footprint 안. [10] 면적 비율(%) 구성으로 정밀화 ──
    #   모듈 하나에 여러 실이 들어갈 수 있으므로, 각 실이 모듈 바닥면적의 몇 %인지
    #   (실종류, 비율%, live, sdl) 로 기록 → 위치 무관 + 구성이 같으면 같은 시그니처.
    rooms = []
    try:
        corners = comp.get_world_corners()
        xmin, xmax = float(corners[:, 0].min()), float(corners[:, 0].max())
        ymin, ymax = float(corners[:, 1].min()), float(corners[:, 1].max())
        module_area = max((xmax - xmin) * (ymax - ymin), 1.0)
    except Exception:
        xmin = xmax = ymin = ymax = None
        module_area = 1.0
    if xmin is not None:
        for room in (getattr(scene, 'rooms', {}) or {}).values():
            if int(getattr(room, 'floor_index', 0)) != fi:
                continue
            try:
                cx, cy = room.centroid()
            except Exception:
                continue
            if xmin <= cx <= xmax and ymin <= cy <= ymax:
                area = _poly_area(getattr(room, 'polygon', []) or [])
                frac_pct = round(area / module_area * 100.0)   # 모듈 면적 대비 %(1% 단위)
                rooms.append((
                    str(getattr(room, 'room_type', '')),
                    int(frac_pct),
                    _q(float(getattr(room, 'live_load_override', 0) or 0)),
                    _q(float(getattr(room, 'sdl_override', 0) or 0)),
                ))

    # ── 개구부(opening) — 회전·대칭(D4) 불변 키로. [벽 개구부 통일] ──
    #   개구부가 (대칭 무시하고) 다르면 다른 타입, 회전/거울대칭이면 같은 타입.
    width = float(comp.dimensions.get('width', 0.0))
    depth = float(comp.dimensions.get('depth', 0.0))
    openings = []
    for o in (getattr(comp, 'openings', None) or []):
        try:
            openings.append(_opening_key(o, width, depth))
        except Exception:
            continue

    # [회전대칭] rot 자체는 시그니처에서 제외(같은 모듈을 90°/대칭 돌려도 동일 타입).
    return (_canon_deps(deps), tuple(sorted(rooms)), tuple(sorted(openings)))


def classify_component_types(scene) -> Dict[int, str]:
    """장면의 독립 부재 본체에 '모듈A-1' 라벨 부여 → {comp_id: label}.

    종속/합체 부재는 라벨을 받지 않는다(딕셔너리에 없음).
    """
    # 1. 대상 수집 — 독립 부재 본체만
    indep = []
    for cid, c in scene.components.items():
        if c.comp_type not in _INDEP_TYPES:
            continue
        if int(getattr(c, 'sub_index', 0)) != 0:
            continue
        if (c.comp_type == ComponentType.STRUCT_WALL
                and getattr(c, 'merged_fp_id', None)):
            continue
        indep.append((cid, c))

    labels: Dict[int, str] = {}
    by_type: Dict[object, list] = defaultdict(list)
    for cid, c in indep:
        by_type[c.comp_type].append((cid, c))

    for ctype, items in by_type.items():
        tname = TYPE_NAMES.get(ctype, '부재')
        # 2. 1차 글자 — 외형 치수별 A, B, … (등장 순서 = 먼저 배치한 크기가 A)
        seen_sizes = []
        for cid, c in sorted(items, key=lambda t: t[0]):
            s = _size_key(c)
            if s not in seen_sizes:
                seen_sizes.append(s)
        size_letter = {s: alpha_label(i).upper()
                       for i, s in enumerate(seen_sizes)}
        # 3. 글자별로 묶어 2차 번호 — 구성 시그니처별 -1, -2, …
        by_letter: Dict[str, list] = defaultdict(list)
        for cid, c in items:
            by_letter[size_letter[_size_key(c)]].append((cid, c))
        for letter, group in by_letter.items():
            sig_num: Dict[tuple, int] = {}
            for cid, c in group:
                sig = _signature(c, scene)
                if sig not in sig_num:
                    sig_num[sig] = len(sig_num) + 1
                labels[cid] = f'{tname}{letter}-{sig_num[sig]}'

    # ── 종속 부재 라벨 — 부모(독립부재) 라벨 + 종속명 + 순번 ──
    # 예: '모듈A-2 캔틸레버보1'. 부모가 독립부재(라벨 보유)인 경우만.
    # 순번은 (부모 라벨, 종속 타입) 안에서 *부모 상대 위치* 별 — 다층 복제는 같은 번호.
    gid_parent: Dict[int, tuple] = {}   # group_id → (부모 라벨, 부모 comp)
    for cid, c in scene.components.items():
        if int(getattr(c, 'sub_index', 0)) == 0 and cid in labels:
            gid = int(getattr(c, 'group_id', 0) or 0)
            if gid:
                gid_parent[gid] = (labels[cid], c)
    dep_pos_num: Dict[tuple, Dict[tuple, int]] = {}
    for cid in sorted(scene.components.keys()):
        c = scene.components[cid]
        if int(getattr(c, 'sub_index', 0)) == 0:
            continue
        gid = int(getattr(c, 'group_id', 0) or 0)
        parent = gid_parent.get(gid)
        if parent is None:
            continue   # 부모가 독립부재 아님(코어 등) → 라벨 미부여
        parent_label, pcomp = parent
        dep_tname = TYPE_NAMES.get(c.comp_type, str(c.comp_type))
        lx = _q(float(c.position[0]) - float(pcomp.position[0]))
        ly = _q(float(c.position[1]) - float(pcomp.position[1]))
        key = (parent_label, dep_tname)
        posmap = dep_pos_num.setdefault(key, {})
        if (lx, ly) not in posmap:
            posmap[(lx, ly)] = len(posmap) + 1
        labels[cid] = f"{parent_label} {dep_tname}{posmap[(lx, ly)]}"

    # ── 흡수(합체) 벽패널 라벨 — 부모 바닥패널 라벨 + '벽패널' + 순번 ──
    # [2026-06-01] merged_fp_id 로 바닥패널에 흡수된 StructWall 은 sub_index==0·
    # group_id 가 패널과 달라 위 종속 로직(group_id 매칭)에 안 잡힌다. 그래서
    # 라벨이 비어 배치설계에 부모 없이 나왔다. 부모 패널 기준으로 직접 부여.
    wall_tname = TYPE_NAMES.get(ComponentType.STRUCT_WALL, '벽패널')
    merged_pos_num: Dict[str, Dict[tuple, int]] = {}
    for cid in sorted(scene.components.keys()):
        c = scene.components[cid]
        if c.comp_type != ComponentType.STRUCT_WALL:
            continue
        mfp = getattr(c, 'merged_fp_id', None)
        if not mfp:
            continue
        parent_label = labels.get(int(mfp))
        pcomp = scene.components.get(int(mfp))
        if not parent_label or pcomp is None:
            continue   # 부모 패널이 라벨 없음(이례) → 미부여
        lx = _q(float(c.position[0]) - float(pcomp.position[0]))
        ly = _q(float(c.position[1]) - float(pcomp.position[1]))
        posmap = merged_pos_num.setdefault(parent_label, {})
        if (lx, ly) not in posmap:
            posmap[(lx, ly)] = len(posmap) + 1
        labels[cid] = f"{parent_label} {wall_tname}{posmap[(lx, ly)]}"

    # ── RC 코어 — 코어벽 'RC코어벽{n}'(xy 위치별, 층 무관), 코어 슬래브 'RC코어 슬래브' ──
    core_xy_num: Dict[tuple, int] = {}
    for cid in sorted(scene.components.keys()):
        c = scene.components[cid]
        if c.comp_type == ComponentType.CORE:
            xy = (_q(float(c.position[0])), _q(float(c.position[1])))
            if xy not in core_xy_num:
                core_xy_num[xy] = len(core_xy_num) + 1
            labels[cid] = f"RC코어벽{core_xy_num[xy]}"
        elif c.comp_type == ComponentType.CORE_SLAB:
            labels[cid] = "RC코어 슬래브"

    return labels


def dump_type_signatures(scene) -> None:
    """[9C-1 임시 진단] 각 독립부재의 cid/label/size/signature 를 콘솔에 출력.

    타입이 c3 에서 안 올라가거나 동일 모듈이 안 합쳐지는 원인 파악용 — 두 '동일' 모듈의
    signature 가 실제로 다른지(=실/회전/층/종속 데이터 차이) 확인한다. 원인 확정 후 제거.
    """
    try:
        labels = classify_component_types(scene)
    except Exception as e:
        from modular_3d._utils.debug import log_error
        log_error(f'classify 실패: {e}', cat='type_naming', exc=True)
        return
    comps = getattr(scene, 'components', {}) or {}
    for cid in sorted(comps):
        if cid not in labels:
            continue   # 종속·코어 등 라벨 미부여
        c = comps[cid]
        try:
            sig = _signature(c, scene)
            sz = _size_key(c)
        except Exception as e:
            sig, sz = f'ERR {e}', None


__all__ = ['classify_component_types', 'dump_type_signatures']
