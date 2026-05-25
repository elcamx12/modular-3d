"""참고자료 다이얼로그 (Phase 8 영역 ⑦).

운송 도메인 참고문서(운송차량 개요·도로 한도·적재 규정·KS 강재 단면제원 등)
를 탭으로 노출. 원본 마크다운 파일은 `modular_3d/transport/references/` 에
넣으면 파일명 순으로 자동 탭 생성된다.

[설계 결정]
- WebEngine 불필요 — QTextBrowser 의 setMarkdown 으로 충분 (Qt 5.14+).
- 파일이 없으면 안내 라벨만 표시 (사용자가 원본 파일을 채우면 자동 반영).
- 파일명 규칙 권장: `01_운송차량개요.md` ~ `08_KS강재_단면제원.md`. 숫자
  접두사로 정렬 순서 보장.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)


# 참고자료 마크다운 기본 위치 — transport/references/
_REF_DIR = Path(__file__).resolve().parent.parent / "transport" / "references"


class TransportReferencesDialog(QDialog):
    """운송 참고자료 모달 — references/*.md 자동 탭."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        ref_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self._ref_dir = Path(ref_dir) if ref_dir else _REF_DIR
        self.setWindowTitle("📖 운송 참고자료")
        self.resize(820, 620)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        md_files = self._discover_md_files()
        if not md_files:
            self._build_empty_state(root)
        else:
            tabs = QTabWidget()
            for path in md_files:
                tabs.addTab(self._build_md_view(path), self._tab_title(path))
            root.addWidget(tabs, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        reload_btn = QPushButton("새로고침")
        reload_btn.clicked.connect(self._reload)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(reload_btn)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _discover_md_files(self) -> List[Path]:
        if not self._ref_dir.is_dir():
            return []
        return sorted(self._ref_dir.glob("*.md"), key=lambda p: p.name)

    def _tab_title(self, path: Path) -> str:
        """파일명에서 숫자 접두사·확장자 제거한 보기용 제목."""
        stem = path.stem
        # "01_운송차량개요" → "운송차량개요"
        if "_" in stem and stem.split("_", 1)[0].isdigit():
            stem = stem.split("_", 1)[1]
        return stem

    def _build_md_view(self, path: Path) -> QWidget:
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        try:
            text = path.read_text(encoding="utf-8")
            # Qt 5.14+ 마크다운 렌더. 실패 시 평문 표시.
            if hasattr(browser, "setMarkdown"):
                browser.setMarkdown(text)
            else:
                browser.setPlainText(text)
        except Exception as e:
            browser.setPlainText(f"(파일 읽기 실패: {path.name})\n{e}")
        return browser

    def _build_empty_state(self, root: QVBoxLayout) -> None:
        msg = QLabel(
            "참고자료 마크다운 파일이 아직 없습니다.\n\n"
            f"다음 폴더에 원본 .md 파일을 넣으면 자동으로 탭이 생성됩니다:\n"
            f"  {self._ref_dir}\n\n"
            "권장 파일명 (정렬 순서 보장용 숫자 접두사):\n"
            "  01_운송차량개요.md\n"
            "  02_도로_한도.md\n"
            "  03_적재_규정.md\n"
            "  …\n"
            "  08_KS강재_단면제원.md"
        )
        msg.setStyleSheet("color: #555; padding: 16px;")
        msg.setWordWrap(True)
        root.addWidget(msg, stretch=1)

    def _reload(self) -> None:
        """references 폴더를 다시 읽어 다이얼로그 재구성."""
        # 기존 레이아웃 비우고 재빌드 — 간단히 새 인스턴스처럼 처리.
        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)  # 기존 레이아웃 분리
        self._build_ui()


__all__ = ["TransportReferencesDialog"]
