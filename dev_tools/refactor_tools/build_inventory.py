"""인벤토리 자동 추출.

modular_3d 의 모든 .py 를 읽어서 다음을 추출:
- 모듈 docstring
- 최상위 클래스 + 라인
- 최상위 함수 + 라인
- 클래스 메소드 (이름 + 라인)
- pyqtSignal 정의
- 모듈 레벨 상수 (대문자 시작)

산출물: 리팩토링_인벤토리.md
"""
import ast
import os
from datetime import date
from pathlib import Path

# 옛 위치 modular_3d/_refactor_tools 에선 parents[1]=modular_3d 였음.
# 새 위치 dev_tools/refactor_tools 에선 parents[2]/modular_3d 가 운영 패키지.
ROOT = Path(__file__).resolve().parents[2] / 'modular_3d'


def _signal_targets(node: ast.Assign) -> str | None:
    """pyqtSignal 호출이면 시그니처 문자열 반환."""
    if not isinstance(node.value, ast.Call):
        return None
    func = node.value.func
    name = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name != 'pyqtSignal':
        return None
    args = []
    for a in node.value.args:
        try:
            args.append(ast.unparse(a))
        except Exception:
            args.append('?')
    return ', '.join(args)


def _const_value(node: ast.Assign) -> str | None:
    """모듈 레벨 상수 (단일 대문자 시작 이름) 라면 짧은 값 반환."""
    if len(node.targets) != 1:
        return None
    t = node.targets[0]
    if not isinstance(t, ast.Name):
        return None
    if not t.id.isupper() and not (t.id[0].isupper() and '_' in t.id):
        # 대문자_언더스코어 형태만 상수로 간주
        if not t.id.isupper():
            return None
    try:
        s = ast.unparse(node.value)
    except Exception:
        return '?'
    if len(s) > 80:
        s = s[:77] + '...'
    return s


def extract(path: Path) -> dict:
    src = path.read_text(encoding='utf-8')
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {'error': str(e), 'path': str(path)}

    module_doc = ast.get_docstring(tree)
    classes = []
    functions = []
    constants = []
    signals = []  # 모듈 레벨 시그널 (드물지만)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = []
            class_signals = []
            class_doc = ast.get_docstring(node)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(sub) or ''
                    doc = doc.split('\n')[0][:80]
                    methods.append((sub.name, sub.lineno, doc))
                elif isinstance(sub, ast.Assign):
                    sig = _signal_targets(sub)
                    if sig is not None:
                        for t in sub.targets:
                            if isinstance(t, ast.Name):
                                class_signals.append((t.id, sub.lineno, sig))
            classes.append({
                'name': node.name,
                'lineno': node.lineno,
                'doc': (class_doc or '').split('\n')[0][:80],
                'methods': methods,
                'signals': class_signals,
                'bases': [ast.unparse(b) for b in node.bases],
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ''
            doc = doc.split('\n')[0][:80]
            functions.append((node.name, node.lineno, doc))
        elif isinstance(node, ast.Assign):
            cv = _const_value(node)
            if cv is not None:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        constants.append((t.id, node.lineno, cv))

    return {
        'path': str(path.relative_to(ROOT.parent)).replace('\\', '/'),
        'doc': (module_doc or '').split('\n')[0][:120],
        'classes': classes,
        'functions': functions,
        'constants': constants,
        'lines': len(src.splitlines()),
    }


def main():
    py_files = sorted(ROOT.rglob('*.py'))
    py_files = [
        p for p in py_files
        if '__pycache__' not in p.parts
        and '_snapshot_원본' not in str(p)
        and '_refactor_tools' not in p.parts
        and '팀원코드' not in p.parts   # 병합 대조용 임시 클론(별도 .git) 제외
    ]

    out_lines = ['# 인벤토리 (자동 생성)\n']
    out_lines.append(f'생성일: {date.today().isoformat()}  |  대상 파일 수: {len(py_files)}\n')
    out_lines.append('')
    out_lines.append('이 파일은 `dev_tools/refactor_tools/build_inventory.py` 가 자동 생성합니다. 직접 편집 금지.\n')
    out_lines.append('')
    out_lines.append('## 파일 인벤토리\n')

    for p in py_files:
        info = extract(p)
        if 'error' in info:
            out_lines.append(f'### {info["path"]}')
            out_lines.append(f'**파싱 에러**: {info["error"]}\n')
            continue
        out_lines.append(f'### `{info["path"]}` ({info["lines"]} 줄)')
        if info['doc']:
            out_lines.append(f'> {info["doc"]}\n')
        if info['constants']:
            out_lines.append('**모듈 상수**')
            for name, ln, v in info['constants']:
                out_lines.append(f'- `{name}` (L{ln}) = `{v}`')
            out_lines.append('')
        if info['functions']:
            out_lines.append('**최상위 함수**')
            for name, ln, doc in info['functions']:
                doc_part = f' — {doc}' if doc else ''
                out_lines.append(f'- `{name}()` (L{ln}){doc_part}')
            out_lines.append('')
        if info['classes']:
            out_lines.append('**클래스**')
            for c in info['classes']:
                bases = f'({", ".join(c["bases"])})' if c['bases'] else ''
                doc_part = f' — {c["doc"]}' if c['doc'] else ''
                out_lines.append(f'- `class {c["name"]}{bases}` (L{c["lineno"]}){doc_part}')
                if c['signals']:
                    for sname, sln, ssig in c['signals']:
                        out_lines.append(f'  - **시그널** `{sname} = pyqtSignal({ssig})` (L{sln})')
                if c['methods']:
                    for mname, mln, mdoc in c['methods']:
                        mdoc_part = f' — {mdoc}' if mdoc else ''
                        out_lines.append(f'  - `{mname}()` (L{mln}){mdoc_part}')
            out_lines.append('')
        out_lines.append('')

    out_path = ROOT / '인벤토리.md'
    out_path.write_text('\n'.join(out_lines), encoding='utf-8')
    print(f'[OK] {out_path} ({len(py_files)} 파일, {sum(1 for ln in out_lines)} 줄)')


if __name__ == '__main__':
    main()
