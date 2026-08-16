"""第1段階: 受信箱スキャン＋元ファイル保管への複製（REQUIREMENTS.md 7-1・CEO指示2026-08-16で改訂）。

- `00_受信箱` 直下のファイルに加え、**1階層だけ**サブフォルダの中のファイルも対象にする
  （2階層以上のネストは対象外＝スコープを限定して挙動を予測可能に保つ）。
  旧仕様「サブフォルダは対象外でよい」はCEO指示（フォルダ名で書類種別を示したい）により改訂。
  サブフォルダ名は分類のヒントとして使う（classify.classify_file 参照）。
- 各ファイルは無条件でまず `_元ファイル保管/` へタイムスタンプ付きでコピーする
  （何があってもここに原本が残る状態を作る＝絶対原則1の生命線）。
- 1件の失敗で全体を止めない（8章）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, logger
from .naming import safe_copy


@dataclass
class ArchiveResult:
    source: Path
    archived_to: Path | None
    ok: bool
    error: str = ""


def list_inbox_files(root: Path) -> list[Path]:
    """00_受信箱直下＋1階層下のサブフォルダ内のファイル一覧（隠しファイルは除外）。

    サブフォルダ名（例: `01_証明写真/写真.jpg` の `01_証明写真`）は分類のヒントとして
    使われる（classify.classify_file）。2階層以上のネストは対象外。
    """
    inbox = config.folder_path(root, config.INBOX)
    if not inbox.exists():
        return []
    files: list[Path] = []
    for p in sorted(inbox.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(
                sorted(q for q in p.iterdir() if q.is_file() and not q.name.startswith("."))
            )
    return files


def archive_inbox(root: Path, when: datetime | None = None) -> list[ArchiveResult]:
    """受信箱の全ファイルを `_元ファイル保管/` へ複製する。結果一覧を返す。"""
    when = when or datetime.now()
    archive_dir = config.folder_path(root, config.ORIGINAL_ARCHIVE)
    ts = when.strftime("%Y%m%d_%H%M%S")
    results: list[ArchiveResult] = []

    for src in list_inbox_files(root):
        archived_filename = f"{ts}_{src.name}"
        try:
            dest = safe_copy(src, archive_dir, archived_filename)
            logger.log_operation(
                root,
                logger.OP_ARCHIVE_COPY,
                str(src),
                str(dest),
                logger.RESULT_OK,
                when=when,
            )
            results.append(ArchiveResult(source=src, archived_to=dest, ok=True))
        except OSError as exc:
            logger.log_operation(
                root,
                logger.OP_ARCHIVE_COPY,
                str(src),
                "",
                logger.RESULT_NG,
                detail=str(exc),
                when=when,
            )
            results.append(ArchiveResult(source=src, archived_to=None, ok=False, error=str(exc)))
    return results
