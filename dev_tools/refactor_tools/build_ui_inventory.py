"""UI 인터랙션 자동 추출.

modular_3d 의 모든 .py 를 읽어서 *사용자 입력 → 코드 응답* 흐름을 추출:
- pyqtSignal 정의 (시그널 카탈로그)
- .connect(...) 호출 (시그널 → 슬롯 매핑)
- Qt 이벤트 핸들러 (eventFilter, keyPressEvent, mousePressEvent, ...)
- installEventFilter 호출
- QShortcut / setShortcut (단축키)
- vispy 이벤트 connect (canvas.events.*.connect)
- QWebChannel registerObject (브리지 등록점)

산출물: modular_3d/UI_마이그레이션/02_UI_인터랙션_자동추출.md

목적: UI 마이그레이션 (vispy → three.js) 시 *기능 누락 0* 보증.
모든 시그널·이벤트가 시나리오 카탈로그(03) 어딘가에 등장해야 한다는
완전성 검증의 기준점.
"""
import ast
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'modular_3d'
OUT_DIR = ROOT / 'UI_마이그레이션'
OUT_PATH = OUT_DIR / '02_UI_인터랙션_자동추출.md'

# Qt 이벤트 핸들러 메소드명
QT_EVENT_METHODS = {
    'eventFilter', 'keyPressEvent', 'keyReleaseEvent',
    'mousePressEvent', 'mouseMoveEvent', 'mouseReleaseEvent',
    'mouseDoubleClickEvent', 'wheelEvent',
    'enterEvent', 'leaveEvent', 'focusInEvent', 'focusOutEvent',
    'dragEnterEvent', 'dragMoveEvent', 'dropEvent',
    'paintEvent', 'resizeEvent', 'closeEvent', 'showEvent', 'hideEvent',
    'contextMenuEvent',
}

# vispy 이벤트 핸들러 메소드명 (베이스 클래스 override 패턴)
VISPY_EVENT_METHODS = {
    'viewbox_mouse_event',
    'on_mouse_press', 'on_mouse_move', 'on_mouse_release',
    'on_mouse_wheel', 'on_mouse_double_click',
    'on_key_press', 'on_key_release',
    'on_draw', 'on_resize', 'on_close',
}

# 컨트롤러 진입점 (Qt eventFilter → controller 분기 패턴)
# main_3d.py 가 vispy 직접 이벤트 대신 Qt eventFilter 로 가로채고
# controller.on_qt_* 로 분기하는 구조. 마이그레이션 시 직접 대응 필요.
CONTROLLER_INPUT_METHODS = {
    'on_qt_mouse_press', 'on_qt_mouse_move', 'on_qt_mouse_release',
    'on_qt_mouse_wheel', 'on_qt_key_press', 'on_qt_key_release',
}


def _node_src(node, src_lines: list[str]) -> str:
    """ast 노드를 원본 한 줄로 (가능하면)."""
    try:
        s = ast.unparse(node)
    except Exception:
        ln = getattr(node, 'lineno', None)
        if ln is None:
            return '?'
        return src_lines[ln - 1].strip()
    if len(s) > 120:
        s = s[:117] + '...'
    return s


def extract(path: Path) -> dict:
    src = path.read_text(encoding='utf-8')
    src_lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {'error': str(e), 'path': str(path)}

    signals = []           # (name, lineno, signature, class_name)
    connects = []          # (lineno, left_repr, right_repr)
    qt_handlers = []       # (class_name, method, lineno)
    install_filters = []   # (lineno, target_repr)
    shortcuts = []         # (lineno, repr)
    vispy_connects = []    # (lineno, repr)  — events.X.connect 패턴
    vispy_overrides = []   # (class_name, method, lineno) — 베이스 override 패턴
    controller_inputs = [] # (class_name, method, lineno) — on_qt_* 진입점
    webchannel_regs = []   # (lineno, repr)

    # 시그널 정의 (pyqtSignal) — 클래스 본문 안
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.Assign):
                    if (isinstance(sub.value, ast.Call) and
                        ((isinstance(sub.value.func, ast.Name) and
                          sub.value.func.id == 'pyqtSignal') or
                         (isinstance(sub.value.func, ast.Attribute) and
                          sub.value.func.attr == 'pyqtSignal'))):
                        try:
                            sig = ', '.join(ast.unparse(a) for a in sub.value.args)
                        except Exception:
                            sig = '?'
                        for t in sub.targets:
                            if isinstance(t, ast.Name):
                                signals.append((t.id, sub.lineno, sig, node.name))

    # 클래스의 Qt / vispy / 컨트롤러 입력 메소드
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if sub.name in QT_EVENT_METHODS:
                        qt_handlers.append((node.name, sub.name, sub.lineno))
                    if sub.name in VISPY_EVENT_METHODS:
                        vispy_overrides.append((node.name, sub.name, sub.lineno))
                    if sub.name in CONTROLLER_INPUT_METHODS:
                        controller_inputs.append((node.name, sub.name, sub.lineno))

    # .connect / installEventFilter / setShortcut / vispy events / webchannel
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute):
            attr = f.attr
            if attr == 'connect':
                # 좌 = 시그널 표현 (f.value), 우 = 첫 인자 (슬롯)
                try:
                    left = ast.unparse(f.value)
                except Exception:
                    left = '?'
                right = '?'
                if node.args:
                    try:
                        right = ast.unparse(node.args[0])
                    except Exception:
                        right = '?'
                # vispy events 패턴
                if '.events.' in left or 'canvas.events' in left:
                    vispy_connects.append((node.lineno, f'{left}.connect({right})'))
                else:
                    if len(left) > 80:
                        left = left[:77] + '...'
                    if len(right) > 80:
                        right = right[:77] + '...'
                    connects.append((node.lineno, left, right))
            elif attr == 'installEventFilter':
                try:
                    target = ast.unparse(f.value)
                except Exception:
                    target = '?'
                try:
                    arg = ast.unparse(node.args[0]) if node.args else '?'
                except Exception:
                    arg = '?'
                install_filters.append((node.lineno, f'{target}.installEventFilter({arg})'))
            elif attr in ('setShortcut',):
                shortcuts.append((node.lineno, _node_src(node, src_lines)))
            elif attr == 'registerObject':
                # QWebChannel.registerObject
                webchannel_regs.append((node.lineno, _node_src(node, src_lines)))
        elif isinstance(f, ast.Name):
            if f.id == 'QShortcut':
                shortcuts.append((node.lineno, _node_src(node, src_lines)))

    return {
        'path': str(path.relative_to(ROOT.parent)).replace('\\', '/'),
        'lines': len(src_lines),
        'signals': signals,
        'connects': connects,
        'qt_handlers': qt_handlers,
        'install_filters': install_filters,
        'shortcuts': shortcuts,
        'vispy_connects': vispy_connects,
        'vispy_overrides': vispy_overrides,
        'controller_inputs': controller_inputs,
        'webchannel_regs': webchannel_regs,
    }


def main():
    py_files = sorted(ROOT.rglob('*.py'))
    py_files = [
        p for p in py_files
        if '__pycache__' not in p.parts
        and '_snapshot' not in str(p).lower()
        and '_refactor_tools' not in p.parts
        and 'tests' not in p.parts  # 테스트는 인터랙션 추출 대상 X
    ]

    # 카테고리별 누적
    all_signals = []
    all_connects = []
    all_qt_handlers = []
    all_install_filters = []
    all_shortcuts = []
    all_vispy = []
    all_vispy_overrides = []
    all_controller_inputs = []
    all_webchannel = []

    for p in py_files:
        info = extract(p)
        if 'error' in info:
            continue
        path = info['path']
        for s in info['signals']:
            all_signals.append((path, *s))
        for c in info['connects']:
            all_connects.append((path, *c))
        for h in info['qt_handlers']:
            all_qt_handlers.append((path, *h))
        for f in info['install_filters']:
            all_install_filters.append((path, *f))
        for sc in info['shortcuts']:
            all_shortcuts.append((path, *sc))
        for v in info['vispy_connects']:
            all_vispy.append((path, *v))
        for vo in info['vispy_overrides']:
            all_vispy_overrides.append((path, *vo))
        for ci in info['controller_inputs']:
            all_controller_inputs.append((path, *ci))
        for w in info['webchannel_regs']:
            all_webchannel.append((path, *w))

    out = []
    out.append('# UI 인터랙션 자동 추출 (M0 산출물)\n')
    out.append(f'생성일: {date.today().isoformat()}  |  대상 파일 수: {len(py_files)}\n')
    out.append('')
    out.append('이 파일은 `dev_tools/refactor_tools/build_ui_inventory.py` 가 자동 생성합니다.')
    out.append('직접 편집 금지. 마이그레이션 시 *모든 항목이 시나리오 카탈로그(03) 에 등장* 해야 누락 0 보증.\n')
    out.append('')

    out.append('## 요약\n')
    out.append(f'- pyqtSignal 정의: **{len(all_signals)}** 건')
    out.append(f'- .connect() 호출: **{len(all_connects)}** 건')
    out.append(f'- Qt 이벤트 핸들러 메소드: **{len(all_qt_handlers)}** 건')
    out.append(f'- installEventFilter 호출: **{len(all_install_filters)}** 건')
    out.append(f'- 단축키 (QShortcut/setShortcut): **{len(all_shortcuts)}** 건')
    out.append(f'- vispy 이벤트 connect 패턴: **{len(all_vispy)}** 건')
    out.append(f'- vispy 베이스 클래스 메소드 override: **{len(all_vispy_overrides)}** 건 (카메라 회전·줌·팬 등)')
    out.append(f'- 컨트롤러 on_qt_* 진입점: **{len(all_controller_inputs)}** 건 (Qt eventFilter → controller 분기)')
    out.append(f'- QWebChannel registerObject: **{len(all_webchannel)}** 건\n')
    out.append('')

    # 1. 시그널 정의
    out.append('## 1. pyqtSignal 정의 (시그널 카탈로그)\n')
    by_class = {}
    for path, name, ln, sig, cls in all_signals:
        by_class.setdefault((path, cls), []).append((name, ln, sig))
    for (path, cls), items in sorted(by_class.items()):
        out.append(f'### `{path}` — `class {cls}`')
        for name, ln, sig in items:
            out.append(f'- `{name} = pyqtSignal({sig})` (L{ln})')
        out.append('')
    out.append('')

    # 2. .connect() 매핑
    out.append('## 2. .connect() 매핑 — 시그널 → 슬롯\n')
    by_file = {}
    for path, ln, left, right in all_connects:
        by_file.setdefault(path, []).append((ln, left, right))
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for ln, left, right in items:
            out.append(f'- L{ln}: `{left}` → `{right}`')
        out.append('')
    out.append('')

    # 3. Qt 이벤트 핸들러
    out.append('## 3. Qt 이벤트 핸들러 메소드\n')
    by_file = {}
    for path, cls, method, ln in all_qt_handlers:
        by_file.setdefault(path, []).append((cls, method, ln))
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for cls, method, ln in items:
            out.append(f'- L{ln}: `class {cls}.{method}()`')
        out.append('')
    out.append('')

    # 4. installEventFilter
    out.append('## 4. installEventFilter 호출 (이벤트 가로채기)\n')
    by_file = {}
    for path, ln, rep in all_install_filters:
        by_file.setdefault(path, []).append((ln, rep))
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for ln, rep in items:
            out.append(f'- L{ln}: `{rep}`')
        out.append('')
    out.append('')

    # 5. 단축키
    out.append('## 5. 단축키 (QShortcut / setShortcut)\n')
    by_file = {}
    for path, ln, rep in all_shortcuts:
        by_file.setdefault(path, []).append((ln, rep))
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for ln, rep in items:
            out.append(f'- L{ln}: `{rep}`')
        out.append('')
    out.append('')

    # 6a. vispy 베이스 메소드 override (카메라 등)
    out.append('## 6a. vispy 베이스 클래스 메소드 override (마이그레이션 핵심 — 카메라 회전·줌·팬)\n')
    by_file = {}
    for path, cls, method, ln in all_vispy_overrides:
        by_file.setdefault(path, []).append((cls, method, ln))
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for cls, method, ln in items:
            out.append(f'- L{ln}: `class {cls}.{method}()`')
        out.append('')
    out.append('')

    # 6b. 컨트롤러 on_qt_* 진입점
    out.append('## 6b. 컨트롤러 진입점 (Qt eventFilter → controller.on_qt_*)\n')
    out.append('> 이 메소드들이 *모든 마우스·키 입력의 실제 처리 시작점* 입니다.')
    out.append('> three.js 마이그레이션 시 이 시그니처를 *그대로 유지* 하고 JS 측에서')
    out.append('> 이벤트를 직렬화 → QWebChannel → 동일한 메소드 호출 형태로 가야 함.\n')
    by_file = {}
    for path, cls, method, ln in all_controller_inputs:
        by_file.setdefault(path, []).append((cls, method, ln))
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for cls, method, ln in items:
            out.append(f'- L{ln}: `class {cls}.{method}()`')
        out.append('')
    out.append('')

    # 6c. vispy events
    out.append('## 6c. vispy events.X.connect 패턴 (있을 경우)\n')
    by_file = {}
    for path, ln, rep in all_vispy:
        by_file.setdefault(path, []).append((ln, rep))
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for ln, rep in items:
            out.append(f'- L{ln}: `{rep}`')
        out.append('')
    out.append('')

    # 7. QWebChannel
    out.append('## 7. QWebChannel registerObject (브리지 등록점)\n')
    by_file = {}
    for path, ln, rep in all_webchannel:
        by_file.setdefault(path, []).append((ln, rep))
    if not by_file:
        out.append('(없음)\n')
    for path, items in sorted(by_file.items()):
        out.append(f'### `{path}`')
        for ln, rep in items:
            out.append(f'- L{ln}: `{rep}`')
        out.append('')
    out.append('')

    OUT_DIR.mkdir(exist_ok=True)
    OUT_PATH.write_text('\n'.join(out), encoding='utf-8')
    print(f'[OK] {OUT_PATH}')
    print(f'  signals={len(all_signals)} connects={len(all_connects)} '
          f'qt_handlers={len(all_qt_handlers)} install={len(all_install_filters)} '
          f'shortcuts={len(all_shortcuts)} vispy_connect={len(all_vispy)} '
          f'vispy_override={len(all_vispy_overrides)} '
          f'ctrl_input={len(all_controller_inputs)} '
          f'webchannel={len(all_webchannel)}')


if __name__ == '__main__':
    main()
