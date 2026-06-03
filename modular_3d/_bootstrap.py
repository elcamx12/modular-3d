r"""실행 환경 부트스트랩 — 모든 진입점이 공유.

[핵심 메커니즘]
Qt WebEngine 5.15 는 QTWEBENGINE_RESOURCES_PATH 환경변수를 무시하고
PyQt5.__file__ 의 절대경로 기반으로 resources/locales 를 직접 검색한다.
따라서 환경변수에 ASCII 경로를 넣어도 PyQt5 자체가 한글 경로에서 import
되면 WebEngine 이 한글 경로를 시도해 ICU mmap 실패한다.

해결책: PyQt5 import 가 일어나기 *전에* 환경 루트를 subst 가상드라이브에
매핑하고 sys.path 의 site-packages 경로를 그 가상드라이브로 교체.
그러면 PyQt5 가 Q:\Lib\site-packages\PyQt5 에서 import 되어 PyQt5.__file__
자체가 ASCII 가 된다.

[부수효과]
- 디스크에 새 파일 0 바이트 (가상 별명만)
- 시스템 폴더 안 건드림
- 종료 시 atexit 자동 해제
- 비정상 종료 후 좀비 드라이브 남으면 다음 실행 시 자동 정리
"""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import sys


# ── 상태 ─────────────────────────────────────────────
_subst_drive_letter: str | None = None
_bootstrap_done: bool = False


def _log(msg: str) -> None:
    """진단 메시지 즉시 flush."""
    try:
        pass
    except Exception:
        pass


# ── venv 가드 ────────────────────────────────────────
def _ensure_venv(project_root: str) -> None:
    venv_py = os.path.join(project_root, "venv", "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        _log(f"[VENV] venv 없음 — 현재 파이썬으로 진행 ({sys.executable})")
        return
    cur_n = os.path.normcase(os.path.abspath(sys.executable))
    tgt_n = os.path.normcase(os.path.abspath(venv_py))
    if cur_n == tgt_n:
        _log("[VENV] 이미 venv 파이썬 — 가드 skip")
        return
    _log(f"[VENV] 현재 {sys.executable}")
    _log(f"[VENV] → 재실행 {venv_py}")
    rc = subprocess.call([venv_py, sys.argv[0]] + sys.argv[1:])
    sys.exit(rc)


# ── subst 좀비 드라이브 자동 정리 ────────────────────
def _list_subst_mappings() -> dict[str, str]:
    """현재 시스템의 subst 매핑 dict {'Q:': 'C:\\...', ...}."""
    if os.name != "nt":
        return {}
    try:
        rc = subprocess.run(["subst"], shell=True, capture_output=True,
                            text=True, timeout=5)
        out = rc.stdout or ""
    except Exception:
        return {}
    mappings: dict[str, str] = {}
    # 예: "Q:\: => C:\Users\...\my_project\venv"
    # 한글 윈도우는 "Q:\: => ..." 또는 "Q:\: => ..." 일관 형식
    for line in out.splitlines():
        m = re.match(r"^([A-Z]):\\?:?\s*=>\s*(.+)$", line.strip())
        if m:
            mappings[m.group(1) + ":"] = m.group(2).strip()
    return mappings


def _cleanup_zombie_substs(env_root: str) -> None:
    """env_root 또는 그 부모를 가리키는 우리 좀비 매핑 해제."""
    if os.name != "nt":
        return
    env_root_n = os.path.normcase(os.path.abspath(env_root))
    project_root = os.path.dirname(env_root)  # venv 의 부모 = my_project
    project_root_n = os.path.normcase(os.path.abspath(project_root))
    for drive, target in _list_subst_mappings().items():
        try:
            target_n = os.path.normcase(os.path.abspath(target))
            if target_n == env_root_n or target_n == project_root_n:
                _log(f"[QT] 좀비 subst 정리: {drive} → {target}")
                subprocess.run(["subst", drive, "/D"], shell=True,
                               capture_output=True, timeout=5)
        except Exception:
            pass


def _find_free_drive_letter() -> str | None:
    for letter in "QRSTUVWXYZ":
        if not os.path.exists(letter + ":\\"):
            return letter
    return None


def _subst_mount(target_dir: str) -> str | None:
    """target_dir → 가상드라이브 매핑. 성공 시 'Q:' 같은 문자열."""
    global _subst_drive_letter
    if os.name != "nt":
        return None
    letter = _find_free_drive_letter()
    if letter is None:
        _log("[QT] subst 실패 — 사용 가능한 드라이브 글자(Q~Z) 없음")
        return None
    try:
        rc = subprocess.run(["subst", letter + ":", target_dir],
                            shell=True, capture_output=True, text=True, timeout=5)
        if rc.returncode != 0 or not os.path.exists(letter + ":\\"):
            _log(f"[QT] subst {letter}: 실패 — {rc.stderr or rc.stdout}")
            return None
        _subst_drive_letter = letter
        atexit.register(_subst_unmount)
        return letter + ":"
    except Exception as e:
        _log(f"[QT] subst 예외 — {type(e).__name__}: {e}")
        return None


def _subst_unmount() -> None:
    global _subst_drive_letter
    if not _subst_drive_letter:
        return
    try:
        subprocess.run(["subst", _subst_drive_letter + ":", "/D"],
                       shell=True, capture_output=True, timeout=5)
        _log(f"[QT] subst {_subst_drive_letter} 해제")
    except Exception:
        pass
    _subst_drive_letter = None


# ── sys.path + 환경변수 ASCII 화 ─────────────────────
def _ascii_rewrite_sys_path(env_root: str, drive: str) -> None:
    """sys.path 중 env_root 로 시작하는 항목을 가상드라이브 경로로 교체.

    이 단계가 핵심 — PyQt5 가 import 될 때 ASCII 경로에서 로드되어야 함.
    """
    env_root_n = os.path.normcase(os.path.abspath(env_root))
    new_paths: list[str] = []
    rewrote = 0
    for p in sys.path:
        try:
            pn = os.path.normcase(os.path.abspath(p))
            if pn.startswith(env_root_n):
                rel = os.path.relpath(p, env_root)
                new_paths.append(drive + os.sep + rel)
                rewrote += 1
            else:
                new_paths.append(p)
        except Exception:
            new_paths.append(p)
    sys.path[:] = new_paths
    _log(f"[QT] sys.path 가상드라이브 교체: {rewrote} 개 항목")

    # PyQt5 가 이미 import 됐으면 캐시 정리 (드물지만 안전망)
    purged = 0
    for mod in list(sys.modules):
        if mod == "PyQt5" or mod.startswith("PyQt5."):
            del sys.modules[mod]
            purged += 1
    if purged:
        _log(f"[QT] PyQt5 모듈 캐시 정리: {purged} 개")


# ── PyInstaller / 일반 환경 감지 ─────────────────────
def _detect_env_root() -> str | None:
    """현재 파이썬 환경의 루트 (venv 또는 conda env, 또는 _MEIPASS) 반환."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return meipass
    # sys.executable 의 부모의 부모 (예: venv/Scripts/python.exe → venv)
    return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))


# ── WebEngine 환경변수 설정 ──────────────────────────
def _setup_webengine_env(drive: str | None) -> None:
    """WebEngine 환경변수 설정. drive 가 있으면 가상드라이브 경로 사용.

    Qt 5.15 가 이 환경변수들을 무시한다는 보고가 있지만 일부 환경에선 도움됨.
    PyQt5.__file__ 이 ASCII 인 게 본질이고, 환경변수는 보조 안전망.
    """
    try:
        import PyQt5 as _PQ
        pyqt5_dir = os.path.dirname(_PQ.__file__)
    except Exception as e:
        _log(f"[QT] PyQt5 import 실패: {e}")
        return

    _log(f"[QT] PyQt5.__file__ 디렉토리: {pyqt5_dir}")
    _log(f"[QT]   ASCII 여부: {pyqt5_dir.isascii()}")

    plugin_path = os.path.join(pyqt5_dir, "Qt5", "plugins")
    qt_bin = os.path.join(pyqt5_dir, "Qt5", "bin")
    qt_resources = os.path.join(pyqt5_dir, "Qt5", "resources")
    qt_translations = os.path.join(pyqt5_dir, "Qt5", "translations")
    locales = os.path.join(qt_translations, "qtwebengine_locales")
    proc_exe = os.path.join(qt_bin, "QtWebEngineProcess.exe")

    if os.path.isdir(plugin_path):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(plugin_path, "platforms")
        os.environ["QT_PLUGIN_PATH"] = plugin_path
    if os.path.isdir(qt_bin):
        os.environ["PATH"] = qt_bin + os.pathsep + os.environ.get("PATH", "")
        if os.path.isfile(proc_exe):
            os.environ["QTWEBENGINEPROCESS_PATH"] = proc_exe
    if os.path.isdir(qt_resources):
        os.environ["QTWEBENGINE_RESOURCES_PATH"] = qt_resources
    if os.path.isdir(locales):
        os.environ["QTWEBENGINE_LOCALES_PATH"] = locales
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    _log(f"[QT] QTWEBENGINEPROCESS_PATH ASCII = "
         f"{os.environ.get('QTWEBENGINEPROCESS_PATH', '').isascii()}")


# ── WebEngine 선등록 ──────────────────────────────────
def _preimport_webengine() -> None:
    try:
        from PyQt5.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        from PyQt5 import QtWebEngineWidgets  # noqa: F401
        _log("[QT] QtWebEngineWidgets 선등록 OK")
    except Exception as e:
        _log(f"[QT] WebEngine 선등록 실패 (운송탭 비활성) — {type(e).__name__}: {e}")


# ── 진입점 ────────────────────────────────────────────
def bootstrap(project_root: str | None = None) -> None:
    """모든 진입점이 가장 먼저 호출. 멱등."""
    global _bootstrap_done
    if _bootstrap_done:
        return
    _bootstrap_done = True

    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    _log(f"[BOOT] modular_3d 부트스트랩 시작 — {project_root}")
    _log(f"[BOOT] sys.executable = {sys.executable}")

    # 1. venv 자기-재실행
    _ensure_venv(project_root)

    # 2. 환경 루트 감지 + 좀비 subst 정리
    env_root = _detect_env_root()
    if env_root:
        _log(f"[QT] 환경 루트: {env_root}  ASCII={env_root.isascii()}")
        _cleanup_zombie_substs(env_root)

    # 3. 환경 루트가 한글이면 subst + sys.path 재작성 (PyQt5 import 전!)
    drive = None
    if env_root and not env_root.isascii():
        drive = _subst_mount(env_root)
        if drive:
            _log(f"[QT] subst {drive} 매핑 → {env_root}")
            _ascii_rewrite_sys_path(env_root, drive)
        else:
            _log("[QT] subst 실패 — PyQt5 한글 경로 그대로 (WebEngine 실패 예상)")

    # 4. WebEngine 환경변수 (PyQt5 처음 import 발생)
    _setup_webengine_env(drive)

    # 5. WebEngine 선등록
    _preimport_webengine()

    _log("[BOOT] 부트스트랩 완료")
