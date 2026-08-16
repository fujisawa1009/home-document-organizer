"""受信箱の元ファイル削除（REQUIREMENTS.md 7-4・11章のCEO確認事項）。

- 削除は `apply_approved_changes` の対象外＝完全に別コマンドとして分離。
- **`_元ファイル保管` に複製済みであることを確認できたファイルのみ**削除候補にする。
- 削除は自動実行しない。1件ごとに確認コールバック（既定は対話 input()）で
  明示的な同意を得たときのみ実行する（CEO確認2026-08-16「毎回都度確認」）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import config, logger
from .inbox_scan import list_inbox_files

ConfirmFn = Callable[[Path], bool]


def _default_confirm(path: Path) -> bool:
    answer = input(f"受信箱から削除してよいですか？（保管済み）: {path.name} [y/N]: ")
    return answer.strip().lower() in {"y", "yes"}


@dataclass
class CleanupResult:
    path: Path
    archived: bool
    deleted: bool
    reason: str = ""


def is_archived(root: Path, src: Path) -> bool:
    """`_元ファイル保管` に src の複製が実在するかを、命名規則（inbox_scan.archive_inbox が
    付与する `YYYYMMDD_HHMMSS_元ファイル名` 形式）に厳密一致で確認する。

    単純な文字列末尾一致（`endswith`）だと、例えば保管済みの `monthly_report.pdf` が
    未保管の `report.pdf` を"保管済み"と誤検知してしまう（`..._monthly_report.pdf` は
    `_report.pdf` で終わるため）。誤検知は削除確認プロンプトの前提を壊し誤削除に
    直結するため、タイムスタンプ接頭辞込みの完全一致で判定する。
    """
    archive_dir = config.folder_path(root, config.ORIGINAL_ARCHIVE)
    if not archive_dir.exists():
        return False
    pattern = re.compile(r"^\d{8}_\d{6}_" + re.escape(src.name) + r"$")
    return any(p.is_file() and pattern.match(p.name) for p in archive_dir.iterdir())


def cleanup_inbox(
    root: Path,
    confirm: ConfirmFn | None = None,
    when: datetime | None = None,
) -> list[CleanupResult]:
    confirm = confirm or _default_confirm
    when = when or datetime.now()
    results: list[CleanupResult] = []

    for src in list_inbox_files(root):
        if not is_archived(root, src):
            results.append(
                CleanupResult(path=src, archived=False, deleted=False, reason="未保管のため削除しない")
            )
            continue

        if not confirm(src):
            results.append(
                CleanupResult(path=src, archived=True, deleted=False, reason="社長未承認のためスキップ")
            )
            continue

        try:
            src.unlink()
            logger.log_operation(
                root,
                logger.OP_INBOX_DELETE,
                str(src),
                "",
                logger.RESULT_OK,
                detail="保管済み確認済み・社長承認のうえ削除",
                when=when,
            )
            results.append(CleanupResult(path=src, archived=True, deleted=True))
        except OSError as exc:
            logger.log_operation(
                root,
                logger.OP_INBOX_DELETE,
                str(src),
                "",
                logger.RESULT_NG,
                detail=str(exc),
                when=when,
            )
            results.append(CleanupResult(path=src, archived=True, deleted=False, reason=str(exc)))

    return results
