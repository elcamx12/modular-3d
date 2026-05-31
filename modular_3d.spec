# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# [2026-05-27 _ctypes DLL load failed 수정]
# venv 가 Anaconda Python(modular312) 위에 올라가 있어 _ctypes.pyd 가
# conda 환경의 Library\bin\ffi-8.dll 에 의존. PyInstaller 가 자동으로
# 못 잡으므로 명시 포함. sys.base_prefix 가 conda env 루트를 가리킴.
_conda_bin = os.path.join(sys.base_prefix, 'Library', 'bin')
if os.path.isdir(_conda_bin):
    for _dll in os.listdir(_conda_bin):
        if _dll.lower().endswith('.dll'):
            binaries.append((os.path.join(_conda_bin, _dll), '.'))

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
#   대상: 트럭 카탈로그 JSON, 참고자료 references/*, 자재비 단가 JSON.
#   (도로 카탈로그 road_limits.json 은 2026-05-26 폐지 — 현장 제한으로 대체.)
datas += [
    ('modular_3d/transport/data/trucks.json', 'modular_3d/transport/data'),
    ('modular_3d/transport/data/README.md', 'modular_3d/transport/data'),
    ('modular_3d/transport/references', 'modular_3d/transport/references'),
    ('modular_3d/카탈로그/material_prices.json', 'modular_3d/카탈로그'),
    # [2026-05-27 평가 탭 Phase R] 공정표 HTML + refs PNG 동결 빌드 포함.
    # QWebEngineView 가 QUrl.fromLocalFile 로 HTML 로드 → 옆 refs/ 의 PNG·index.json 도 필요.
    ('modular_3d/schedule/모듈러주택_공정표.html', 'modular_3d/schedule'),
    ('modular_3d/schedule/refs', 'modular_3d/schedule/refs'),
    # [2026-05-31 병합] three.js 뷰어 HTML 템플릿 — Path(__file__).parent 로 read_text.
    #   누락 시 동결 빌드에서 'template missing' → 운송/배치 3D 뷰어가 깨진다.
    ('modular_3d/render/viewer_three_template.html', 'modular_3d/render'),
    ('modular_3d/ui/alignment/alignment_view_three_template.html', 'modular_3d/ui/alignment'),
    # [2026-05-31 병합] 모듈 정의 라이브러리 — main_3d 가 __file__ 기준 로드.
    ('modular_3d/definition_library/definitions.json', 'modular_3d/definition_library'),
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
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='modular_3d',
)
