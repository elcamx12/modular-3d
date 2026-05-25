# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
hiddenimports += collect_submodules('modular_3d')
# [Phase 9] 운송 모듈 명시 hiddenimports — collect_submodules 가 잡지만 안전망.
#   WebEngine 임베드 + 부트스트랩 동결 호환 (운송프로그램병합준비.md Phase 9 점검).
hiddenimports += [
    'PyQt5.QtWebEngineWidgets', 'PyQt5.QtWebChannel',
    'modular_3d._bootstrap',
]
tmp_ret = collect_all('openseespy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('openseespywin')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('vispy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PyQt5')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# 운송 탭 차트(plotly.io → QtWebEngine HTML) — plotly 패키지·데이터 수집.
tmp_ret = collect_all('plotly')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# [Phase 9] 운송 데이터 파일 번들 — collect_submodules 는 .py 만 잡으므로
#   JSON/마크다운 데이터는 명시 포함해야 동결 빌드에서 카탈로그 로드 성공.
#   대상: 트럭/도로 카탈로그 JSON, 참고자료 references/*, 자재비 단가 JSON.
datas += [
    ('modular_3d/transport/data/trucks.json', 'modular_3d/transport/data'),
    ('modular_3d/transport/data/road_limits.json', 'modular_3d/transport/data'),
    ('modular_3d/transport/data/README.md', 'modular_3d/transport/data'),
    ('modular_3d/transport/references', 'modular_3d/transport/references'),
    ('modular_3d/카탈로그/material_prices.json', 'modular_3d/카탈로그'),
]


a = Analysis(
    ['run_modular_3d.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='modular_3d',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='modular_3d',
)
