"""호환성 shim 13개 제거 + 모든 import 를 새 경로로 일괄 통일.

대상 .py 파일 = my_project/ 트리 전체 (단, venv·__pycache__·_snapshot_원본 제외).

치환 패턴: 옛 경로 → 새 경로 (13개)
"""
import re
from pathlib import Path

# 옛 modular_3d/_refactor_tools/file 에선 parents[3]=04_코드, parents[1]=modular_3d.
# 새 my_project/dev_tools/refactor_tools/file 에선 parents[3]=04_코드,
# parents[2]/modular_3d 가 운영 패키지.
PROJECT_ROOT = Path(__file__).resolve().parents[3]              # 04_코드/
MODULAR = Path(__file__).resolve().parents[2] / 'modular_3d'    # modular_3d/

# (옛 모듈 경로, 새 모듈 경로) — 정렬: 더 긴 패턴이 먼저 와야 충돌 없음
RENAMES = [
    ('modular_3d.alignment_helpers', 'modular_3d.ui.alignment_helpers'),
    ('modular_3d.alignment_view',    'modular_3d.ui.alignment_view'),
    ('modular_3d.analysis_panel',    'modular_3d.ui.analysis_panel'),
    ('modular_3d.controls_geom',     'modular_3d.ui.controls_geom'),
    ('modular_3d.controls',          'modular_3d.ui.controls'),
    ('modular_3d.main_3d',           'modular_3d.ui.main_3d'),
    ('modular_3d.mesh_builder',      'modular_3d.render.mesh_builder'),
    ('modular_3d.multi_floor',       'modular_3d.model.multi_floor'),
    ('modular_3d.scene_io',          'modular_3d.io.scene_io'),
    ('modular_3d.scene_migration',   'modular_3d.io.scene_migration'),
    ('modular_3d.snap',              'modular_3d.render.snap'),
    ('modular_3d.ui_panel',          'modular_3d.ui.ui_panel'),
    ('modular_3d.viewer',            'modular_3d.render.viewer'),
]

# 13개 shim 파일 (제거 대상)
SHIM_FILES = [
    MODULAR / 'alignment_helpers.py',
    MODULAR / 'alignment_view.py',
    MODULAR / 'analysis_panel.py',
    MODULAR / 'controls.py',
    MODULAR / 'controls_geom.py',
    MODULAR / 'main_3d.py',
    MODULAR / 'mesh_builder.py',
    MODULAR / 'multi_floor.py',
    MODULAR / 'scene_io.py',
    MODULAR / 'scene_migration.py',
    MODULAR / 'snap.py',
    MODULAR / 'ui_panel.py',
    MODULAR / 'viewer.py',
]


def should_skip(p: Path) -> bool:
    parts = set(p.parts)
    if '__pycache__' in parts or 'venv' in parts:
        return True
    if any('_snapshot_원본' in s for s in p.parts):
        return True
    if p in SHIM_FILES:
        return True  # shim 자체는 어차피 곧 삭제 → 치환 불필요
    return False


def patch_file(p: Path) -> int:
    """한 .py 파일에서 import 패턴 일괄 치환. 변경 횟수 반환."""
    src = p.read_text(encoding='utf-8')
    orig = src
    n_changes = 0
    for old, new in RENAMES:
        # `from <old> import` 패턴
        old_esc = re.escape(old)
        # \b 경계 사용 — 'modular_3d.controls' 가 'modular_3d.controls_geom' 의
        # 전방 일치로 잡히면 안 됨. 그래서 RENAMES 정렬을 길이순으로 했으나
        # 추가 안전장치로 뒤에 따라오는 글자가 [a-z_0-9] 가 아닌지 확인.
        pat_from = rf'from {old_esc}(?=[\s.])'
        pat_import = rf'import {old_esc}(?=[\s.])'
        new_src1, c1 = re.subn(pat_from, f'from {new}', src)
        new_src2, c2 = re.subn(pat_import, f'import {new}', new_src1)
        n_changes += c1 + c2
        src = new_src2
    if src != orig:
        p.write_text(src, encoding='utf-8')
    return n_changes


def main():
    changed_files = []
    total = 0
    for p in PROJECT_ROOT.rglob('*.py'):
        if should_skip(p):
            continue
        n = patch_file(p)
        if n > 0:
            changed_files.append((p, n))
            total += n

    print(f'[OK] {len(changed_files)} 파일에서 총 {total}개 import 치환')
    for p, n in changed_files:
        rel = p.relative_to(PROJECT_ROOT)
        print(f'  {rel}: {n}개')

    # shim 삭제
    print()
    deleted = 0
    for f in SHIM_FILES:
        if f.exists():
            f.unlink()
            deleted += 1
    print(f'[OK] {deleted}개 shim 파일 삭제')


if __name__ == '__main__':
    main()
