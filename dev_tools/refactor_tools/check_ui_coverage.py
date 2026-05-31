"""UI 인터랙션 완전성 검증.

`02_UI_인터랙션_자동추출.md` 의 모든 항목 (시그널·핸들러·진입점) 이
`03_시나리오_카탈로그.md` 어딘가에 등장하는지 자동 검사.

산출물: `04_완전성_검증.md` — 등장 빈도 + 누락 의심 목록.

마이그레이션 직전 다시 실행해서 *0 누락* 확보.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'modular_3d'
SRC_ROOT = ROOT
MIG_DIR = ROOT / 'UI_마이그레이션'
CATALOG_PATH = MIG_DIR / '03_시나리오_카탈로그.md'
INVENTORY_PATH = MIG_DIR / '02_UI_인터랙션_자동추출.md'
OUT_PATH = MIG_DIR / '04_완전성_검증.md'

QT_EVENT_METHODS = {
    'eventFilter', 'keyPressEvent', 'keyReleaseEvent',
    'mousePressEvent', 'mouseMoveEvent', 'mouseReleaseEvent',
    'mouseDoubleClickEvent', 'wheelEvent',
    'enterEvent', 'leaveEvent', 'focusInEvent', 'focusOutEvent',
    'dragEnterEvent', 'dragMoveEvent', 'dropEvent',
    'paintEvent', 'resizeEvent', 'closeEvent', 'showEvent', 'hideEvent',
    'contextMenuEvent',
}
VISPY_EVENT_METHODS = {
    'viewbox_mouse_event',
    'on_mouse_press', 'on_mouse_move', 'on_mouse_release',
    'on_mouse_wheel', 'on_mouse_double_click',
    'on_key_press', 'on_key_release',
    'on_draw', 'on_resize', 'on_close',
}
CONTROLLER_INPUT_METHODS = {
    'on_qt_mouse_press', 'on_qt_mouse_move', 'on_qt_mouse_release',
    'on_qt_mouse_wheel', 'on_qt_key_press', 'on_qt_key_release',
}

# 마이그레이션 대상이 정의·배치 탭이므로, 그 외 탭 전용 파일은 검증 면제.
# (있어도 좋고 없어도 좋고, 위양성 줄임)
SKIP_PATHS = (
    'modular_3d/ui/analysis_panel.py',
    'modular_3d/ui/evaluation_panel.py',
    'modular_3d/ui/joint_edit_panel.py',
    'modular_3d/ui/member_info_popup.py',
    'modular_3d/ui/transport_catalog_dialog.py',
    'modular_3d/ui/transport_panel.py',
    'modular_3d/ui/transport_references_dialog.py',
    'modular_3d/ui/schedule_panel.py',
    'modular_3d/ui/project_settings.py',
    'modular_3d/ui/opening_dialog.py',
    'modular_3d/ui/room_dialog.py',
)

# main_3d.py 안에 있는 클래스 중 운송 탭 전용 등 마이그레이션 무관
SKIP_CLASSES = (
    '_TransportBridge',  # 운송 탭 ↔ three.js 브리지 (이미 three.js)
)


def collect_inventory():
    """02 와 같은 추출 로직. 시그널 이름·이벤트 메소드 이름 dict."""
    signals = []           # (path, class, name)
    qt_handlers = []       # (path, class, method)
    vispy_overrides = []   # (path, class, method)
    ctrl_inputs = []       # (path, class, method)

    py_files = sorted(SRC_ROOT.rglob('*.py'))
    py_files = [
        p for p in py_files
        if '__pycache__' not in p.parts
        and '_snapshot' not in str(p).lower()
        and '_refactor_tools' not in p.parts
        and 'tests' not in p.parts
    ]

    for p in py_files:
        rel = str(p.relative_to(SRC_ROOT.parent)).replace('\\', '/')
        if rel in SKIP_PATHS:
            continue
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if node.name in SKIP_CLASSES:
                    continue
                for sub in node.body:
                    if isinstance(sub, ast.Assign):
                        if (isinstance(sub.value, ast.Call) and
                            ((isinstance(sub.value.func, ast.Name) and
                              sub.value.func.id == 'pyqtSignal') or
                             (isinstance(sub.value.func, ast.Attribute) and
                              sub.value.func.attr == 'pyqtSignal'))):
                            for t in sub.targets:
                                if isinstance(t, ast.Name):
                                    signals.append((rel, node.name, t.id))
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if sub.name in QT_EVENT_METHODS:
                            qt_handlers.append((rel, node.name, sub.name))
                        if sub.name in VISPY_EVENT_METHODS:
                            vispy_overrides.append((rel, node.name, sub.name))
                        if sub.name in CONTROLLER_INPUT_METHODS:
                            ctrl_inputs.append((rel, node.name, sub.name))

    return {
        'signals': signals,
        'qt_handlers': qt_handlers,
        'vispy_overrides': vispy_overrides,
        'ctrl_inputs': ctrl_inputs,
    }


def main():
    inv = collect_inventory()
    catalog = CATALOG_PATH.read_text(encoding='utf-8')

    # 이름 등장 빈도 (단어 경계 매칭)
    def count_name(name: str) -> int:
        return len(re.findall(rf'\b{re.escape(name)}\b', catalog))

    out = []
    out.append('# UI 인터랙션 완전성 검증 (M0 산출물)\n')
    out.append('자동 생성: `dev_tools/refactor_tools/check_ui_coverage.py`\n')
    out.append('')
    out.append('각 항목이 시나리오 카탈로그(03)에 등장하는지 검사. 등장 0회 = 누락 의심.')
    out.append('주의: 단순 단어 매칭이라 *위양성* 가능 (이름이 다른 곳에 자연 등장).')
    out.append('마이그레이션 대상 외 탭 전용 파일은 검증 면제.\n')
    out.append('')

    sections = [
        ('시그널 정의', inv['signals'], 'class', 'name'),
        ('Qt 이벤트 핸들러', inv['qt_handlers'], 'class', 'method'),
        ('vispy 메소드 override', inv['vispy_overrides'], 'class', 'method'),
        ('컨트롤러 진입점', inv['ctrl_inputs'], 'class', 'method'),
    ]

    total_items = 0
    total_zero = 0
    zero_list = []

    for title, items, key1, key2 in sections:
        out.append(f'## {title} ({len(items)} 건)\n')
        out.append('| 파일 | 클래스 | 이름 | 카탈로그 등장 |')
        out.append('|---|---|---|---|')
        for path, cls, name in items:
            n = count_name(name)
            mark = f'**0 — 누락 의심**' if n == 0 else f'{n}'
            out.append(f'| `{path}` | `{cls}` | `{name}` | {mark} |')
            total_items += 1
            if n == 0:
                total_zero += 1
                zero_list.append((title, path, cls, name))
        out.append('')

    out.append('---\n')
    out.append('## 요약\n')
    out.append(f'- 총 검증 항목: **{total_items}** 건')
    out.append(f'- 카탈로그 등장 0회 (누락 의심): **{total_zero}** 건\n')

    if zero_list:
        out.append('### 누락 의심 목록\n')
        for title, path, cls, name in zero_list:
            out.append(f'- [{title}] `{path}` · `{cls}.{name}`')
        out.append('')
        out.append('각 항목을 확인하여 다음 중 하나로 분류:')
        out.append('1. *실제 누락* → 카탈로그에 시나리오 추가')
        out.append('2. *죽은 코드* → 별도 정리 항목')
        out.append('3. *마이그레이션 무관* → SKIP_PATHS 에 추가 + 재실행')
    else:
        out.append('### 누락 없음 — 완전성 확보 ✓\n')

    MIG_DIR.mkdir(exist_ok=True)
    OUT_PATH.write_text('\n'.join(out), encoding='utf-8')
    print(f'[OK] {OUT_PATH}')
    print(f'  total={total_items} zero={total_zero}')


if __name__ == '__main__':
    main()
