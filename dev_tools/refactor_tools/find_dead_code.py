"""정적 dead code 후보 식별.

각 .py 의 최상위 함수·클래스·메소드 이름을 모은 뒤, modular_3d 전체 트리에서
참조 횟수를 센다. 참조가 정의 자신 1 회뿐이면 후보. 외부 호출처가 없는 helper 도
경계 케이스로 함께 표시.

주의:
- 동적 호출 (`getattr(obj, name)`, dispatch 테이블, Qt 시그널-슬롯 자동 연결)은
  잡지 못한다. 후보를 나열만 하고 삭제는 사용자가 결정.
- 단축 키 매핑 같은 dict 값은 호출처로 안 잡혀 false positive 가 많을 수 있다.

산출물: 리팩토링_죽은코드후보.md
"""
import ast
import re
from collections import defaultdict
from pathlib import Path

# 옛 modular_3d/_refactor_tools 에선 parents[1]=modular_3d.
# 새 dev_tools/refactor_tools 에선 parents[2]/modular_3d.
ROOT = Path(__file__).resolve().parents[2] / 'modular_3d'


def _summarize_node(src_lines, node) -> str:
    """함수/메소드 노드의 기능 한 줄 요약.

    우선순위:
    1. docstring 첫 줄 (있으면)
    2. 본문 첫 비어있지 않은 라인 (주석 포함, 80자 자름)
    """
    doc = ast.get_docstring(node)
    if doc:
        first = doc.strip().split('\n')[0].strip()
        if len(first) > 100:
            first = first[:97] + '...'
        return first
    # docstring 없음 — 본문 첫 라인
    start = node.body[0].lineno - 1 if node.body else node.lineno
    end = min(start + 5, len(src_lines))
    for i in range(start, end):
        line = src_lines[i].strip()
        if not line:
            continue
        if len(line) > 100:
            line = line[:97] + '...'
        return line
    return '(빈 함수)'


def list_targets():
    """(name, kind, file, lineno, summary) 목록."""
    out = []
    for p in ROOT.rglob('*.py'):
        if '__pycache__' in p.parts:
            continue
        if '_snapshot_원본' in str(p):
            continue
        if '_refactor_tools' in p.parts:
            continue
        try:
            src = p.read_text(encoding='utf-8')
            tree = ast.parse(src)
        except Exception:
            continue
        src_lines = src.splitlines()
        rel = str(p.relative_to(ROOT.parent)).replace('\\', '/')
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                summary = (ast.get_docstring(node) or '').strip().split('\n')[0]
                if len(summary) > 100:
                    summary = summary[:97] + '...'
                out.append((node.name, 'class', rel, node.lineno, summary))
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # dunder / 표준 Qt 오버라이드는 dead code 후보에서 제외
                        if sub.name.startswith('__'):
                            continue
                        if sub.name in {'eventFilter', 'keyPressEvent', 'mousePressEvent',
                                        'mouseMoveEvent', 'mouseReleaseEvent', 'paintEvent',
                                        'closeEvent', 'resizeEvent', 'wheelEvent',
                                        'setupUi', 'on_draw', 'on_resize'}:
                            continue
                        s = _summarize_node(src_lines, sub)
                        out.append((sub.name, f'method({node.name})', rel, sub.lineno, s))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith('__'):
                    continue
                s = _summarize_node(src_lines, node)
                out.append((node.name, 'func', rel, node.lineno, s))
    return out


def count_refs():
    """이름별 참조 횟수 (정의 자신 포함 grep). 메소드는 .name 호출도 카운트."""
    files = [
        p for p in ROOT.rglob('*.py')
        if '__pycache__' not in p.parts
        and '_snapshot_원본' not in str(p)
        and '_refactor_tools' not in p.parts
    ]
    blob = ''
    for p in files:
        try:
            blob += p.read_text(encoding='utf-8') + '\n'
        except Exception:
            continue

    refs = defaultdict(int)

    def count(name: str) -> int:
        # \b 경계로 같은 이름 단어만
        return len(re.findall(rf'\b{re.escape(name)}\b', blob))

    return count


def main():
    targets = list_targets()
    counter = count_refs()
    candidates = []  # (refs, name, kind, file, lineno, summary)
    for name, kind, file, ln, summary in targets:
        n = counter(name)
        if n <= 1:
            candidates.append((n, name, kind, file, ln, summary))

    out = ['# Dead Code 후보 (정적 분석 자동)\n']
    out.append(f'생성일: 2026-05-08  |  검사 대상 함수·클래스·메소드 수: {len(targets)}\n')
    out.append('')
    out.append('이 파일은 `_refactor_tools/find_dead_code.py` 가 자동 생성합니다.')
    out.append('')
    out.append('## 주의')
    out.append('- 동적 호출 (`getattr`, dispatch dict, 시그널-슬롯 자동연결, 메인스크립트 진입점) 은')
    out.append('  잡지 못합니다. **삭제는 사용자가 직접 검증 후 결정**.')
    out.append('- dunder (`__init__`, `__main__` 등) 와 Qt 표준 오버라이드 (`keyPressEvent` 등) 는')
    out.append('  자동으로 후보에서 제외했습니다.')
    out.append('- "기능 설명" 은 docstring 또는 본문 첫 라인 자동 추출 — 부정확할 수 있음.')
    out.append('')
    out.append(f'## 참조 0~1 회 후보 ({len(candidates)} 개)')
    out.append('')
    candidates.sort(key=lambda x: (x[3], x[4]))

    # 파일별로 그룹핑해서 출력
    current_file = None
    for refs, name, kind, file, ln, summary in candidates:
        if file != current_file:
            out.append('')
            out.append(f'### `{file}`')
            out.append('')
            current_file = file
        out.append(f'- **`{name}`** ({kind}, L{ln}, 참조 {refs}회)')
        if summary:
            out.append(f'  - 기능: {summary}')
        else:
            out.append(f'  - 기능: (docstring·본문 추출 실패)')

    out.append('')
    out.append('## 사용자 결정 절차')
    out.append('')
    out.append('1. 위 목록의 각 항목에 대해, 사용자가 "정말 안 쓰는지" 검증')
    out.append('2. 안 쓰는 것만 따로 골라서 클로드에게 "이 항목 삭제" 명령')
    out.append('3. 클로드는 명령받은 항목만 삭제 (자동 삭제 금지)')

    out_path = ROOT / '리팩토링_죽은코드후보.md'
    out_path.write_text('\n'.join(out), encoding='utf-8')
    print(f'[OK] {out_path} ({len(candidates)} 후보)')


if __name__ == '__main__':
    main()
