"""카테고리별 진단 로그 + 통합 로거 (콘솔 + 파일).

[목적]
프로젝트 전체에 분산된 진단 print 를 한 곳으로 모으고, 레벨(정보/경고/오류)과
*파일 출력* 을 더한다. `.py` 더블클릭 실행이라 콘솔을 못 봐도, 오류가 로그
파일(my_project/logs/modular_3d.log)에 남아 사후 추적이 된다.

[호환]
기존 `dprint(category, ...)` API 는 그대로 유지(카테고리 토글). 다만 이제 print
대신 통합 로거(INFO)로 흘러 콘솔 + 파일 양쪽에 남는다. disable/enable/silenced 등
카테고리 제어도 그대로.

[추가 API]
    from modular_3d._utils.debug import log_info, log_warn, log_error
    log_warn('단가 미입력', cat='quantity')
    log_error('해석 실패', cat='ops', exc=True)   # exc=True → 스택트레이스 첨부

[환경변수]
    MODULAR_DEBUG_OFF=joint_rules,scene_io   # 시작 시 해당 카테고리 dprint OFF

[정책]
- dprint(=INFO) 는 카테고리 토글 대상. WARN/ERROR 는 항상 기록(토글 무관).
- 콘솔 핸들러는 cp949 인코딩 깨짐(UnicodeEncodeError)에도 죽지 않도록 안전 처리.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import Set


# 비활성화된 카테고리 집합. 비어있으면 모두 ON.
_DISABLED: Set[str] = set()


def _load_env_defaults() -> None:
    """환경변수에서 초기 OFF 카테고리 로드."""
    off_str = os.environ.get('MODULAR_DEBUG_OFF', '')
    if off_str:
        for cat in off_str.split(','):
            cat = cat.strip()
            if cat:
                _DISABLED.add(cat)


_load_env_defaults()


# ── 통합 로거 (지연 초기화) ──────────────────────────────────
class _SafeStreamHandler(logging.StreamHandler):
    """콘솔 인코딩(cp949) 깨짐에도 죽지 않는 StreamHandler.

    한글·이모지 메시지가 cp949 콘솔에서 UnicodeEncodeError 를 내도, 그 핸들러가
    핫패스를 중단시키지 않도록 ASCII 대체로 폴백한다.
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            super().emit(record)
        except UnicodeEncodeError:
            try:
                msg = self.format(record).encode('ascii', 'replace').decode('ascii')
                self.stream.write(msg + self.terminator)
                self.flush()
            except Exception:
                pass
        except Exception:
            pass


_LOGGER: logging.Logger | None = None
_LOG_PATH: Path | None = None


def _resolve_log_path() -> Path | None:
    """로그 파일 경로(my_project/logs/modular_3d.log). 실패 시 None."""
    try:
        # _utils/debug.py → _utils → modular_3d → my_project
        root = Path(__file__).resolve().parents[2]
        log_dir = root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / 'modular_3d.log'
    except Exception:
        return None


def _get_logger() -> logging.Logger:
    global _LOGGER, _LOG_PATH
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger('modular_3d')
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # 루트 로거 중복 출력 방지

    # 파일 핸들러 — utf-8, 모든 레벨. 한글 경로라도 파이썬 open 은 정상 처리.
    _LOG_PATH = _resolve_log_path()
    if _LOG_PATH is not None:
        try:
            fh = logging.FileHandler(_LOG_PATH, encoding='utf-8')
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                '%(asctime)s %(levelname)s %(message)s', '%H:%M:%S'))
            logger.addHandler(fh)
        except Exception:
            pass

    # 콘솔 핸들러 — INFO+, 메시지만(기존 print 느낌 유지), cp949 안전.
    try:
        sh = _SafeStreamHandler(sys.stdout)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(sh)
    except Exception:
        pass

    _LOGGER = logger
    return logger


def log_path() -> Path | None:
    """현재 로그 파일 경로(없으면 None)."""
    _get_logger()
    return _LOG_PATH


# ── stdout/stderr → 파일 복제(tee) ──────────────────────────
_TEE_INSTALLED = False


class _Tee:
    """원본 스트림 + 로그 파일에 동시 기록.

    원본 write 실패(cp949 인코딩 등)는 삼켜 핫패스를 중단시키지 않는다(D004 완화).
    파일 쪽은 utf-8 이라 한글·이모지도 온전히 남는다.
    """

    def __init__(self, original, fileobj):
        self._orig = original
        self._file = fileobj

    def write(self, s):
        try:
            self._orig.write(s)
        except Exception:
            pass
        try:
            self._file.write(s)
        except Exception:
            pass
        return len(s) if s else 0

    def flush(self):
        for st in (self._orig, self._file):
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self._orig.isatty())
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._orig, name)


def install_stdout_tee() -> "Path | None":
    """sys.stdout/stderr 를 콘솔 + logs/console.log(utf-8) 양쪽에 복제.

    `.py` 더블클릭 실행이라 콘솔을 못 봐도 모든 print 출력이 파일에 남는다.
    멱등(중복 설치 안 함). 구조적 로그(dprint/log_*)는 modular_3d.log 로 별도 기록.
    """
    global _TEE_INSTALLED
    if _TEE_INSTALLED:
        return None
    base = _resolve_log_path()
    if base is None:
        return None
    console_path = base.parent / 'console.log'
    try:
        fobj = open(console_path, 'a', encoding='utf-8', buffering=1)
    except Exception:
        return None
    try:
        sys.stdout = _Tee(sys.stdout, fobj)
        sys.stderr = _Tee(sys.stderr, fobj)
        _TEE_INSTALLED = True
    except Exception:
        return None
    return console_path


def _fmt(cat: str | None, msg: object) -> str:
    return f'[{cat}] {msg}' if cat else str(msg)


# ── 공개 출력 API ────────────────────────────────────────────
def dprint(category: str, *args, **kwargs) -> None:
    """카테고리가 활성일 때만 진단(INFO) 기록 — 콘솔 + 파일.

    기존 print 시그니처 호환(sep 존중, end/file 은 로거가 관리해 무시).
    카테고리가 비활성이면 아무것도 안 함.
    """
    if category in _DISABLED:
        return
    sep = kwargs.get('sep', ' ')
    msg = sep.join(str(a) for a in args)
    _get_logger().info(_fmt(category, msg))


def log_info(msg: object, *, cat: str | None = None) -> None:
    """정보 로그(콘솔 + 파일). cat 지정 시 카테고리 토글 대상."""
    if cat is not None and cat in _DISABLED:
        return
    _get_logger().info(_fmt(cat, msg))


def log_warn(msg: object, *, cat: str | None = None) -> None:
    """경고 로그 — 항상 기록(카테고리 토글 무관)."""
    _get_logger().warning(_fmt(cat, msg))


def log_error(msg: object, *, cat: str | None = None, exc: bool = False) -> None:
    """오류 로그 — 항상 기록. exc=True 면 현재 예외 스택트레이스 첨부."""
    _get_logger().error(_fmt(cat, msg), exc_info=exc)


# ── 카테고리 제어 ────────────────────────────────────────────
def disable(category: str) -> None:
    """특정 카테고리 비활성화."""
    _DISABLED.add(category)


def enable(category: str) -> None:
    """특정 카테고리 활성화 (기본 상태로 복원)."""
    _DISABLED.discard(category)


def disable_all(*, known: tuple = (
    'joint_rules', 'ops_builder', 'ops_solver', 'topology',
    'scene_io', 'joint_recorder', 'viewer', 'diaphragm',
    'VIS', 'DBG-DIA', 'ANALYSIS',
)) -> None:
    """알려진 모든 카테고리 비활성화. 알려지지 않은 카테고리는 영향 없음."""
    _DISABLED.update(known)


def enable_all() -> None:
    """모든 카테고리 활성화 (비활성 집합 비우기)."""
    _DISABLED.clear()


def is_enabled(category: str) -> bool:
    return category not in _DISABLED


@contextlib.contextmanager
def silenced(*categories: str):
    """context manager — 지정 카테고리(없으면 모두) 를 블록 안 OFF.

    예:
        with silenced('joint_rules', 'scene_io'):
            run_build()
    """
    if categories:
        prev = {c: c in _DISABLED for c in categories}
        for c in categories:
            _DISABLED.add(c)
        try:
            yield
        finally:
            for c, was_off in prev.items():
                if not was_off:
                    _DISABLED.discard(c)
    else:
        prev_all = set(_DISABLED)
        disable_all()
        try:
            yield
        finally:
            _DISABLED.clear()
            _DISABLED.update(prev_all)
