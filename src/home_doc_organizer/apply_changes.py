"""第3・4段階: 承認後の実行（REQUIREMENTS.md 7-4）。

- 「承認」列にOK等の記入がある行のみ処理対象。
- 元ファイルは絶対に変更しない。新しいファイル名・カテゴリフォルダへ **コピー** で複製するのみ。
- 同名衝突は上書きせず連番付与（naming.safe_copy が保証）。
- 受信箱の元ファイル削除は本モジュールの範囲外（絶対に自動実行しない。REQUIREMENTS.md 11章の
  CEO確認事項どおり、削除の是非は毎回別途確認する運用＝ cleanup_inbox.py が対話的に扱う）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, logger
from .naming import safe_copy
from .proposal import CSV_COLUMNS

# 「承認」列としてOKとみなす値（大小文字・前後空白を無視）。
# それ以外（空欄・不明な値・明示的なNG等）はすべて未承認として扱う＝安全側デフォルト。
APPROVED_VALUES = {"ok", "承認", "はい", "yes", "y", "go", "実行"}

# 提案カテゴリとして許容するフォルダ名（CSVを人手やチャネル経由で改変されても
# 想定外のパスへ書き込まない安全弁）。
ALLOWED_TARGET_FOLDERS = set(config.CATEGORY_FOLDERS) | {config.NEEDS_REVIEW}


@dataclass
class ApplyResult:
    row_index: int
    source_path: str
    ok: bool
    dest_path: str = ""
    error: str = ""


def is_approved(value: str) -> bool:
    return value.strip().lower() in APPROVED_VALUES


def _latest_proposal_csv(root: Path) -> Path | None:
    proposal_dir = config.folder_path(root, config.PROPOSALS)
    if not proposal_dir.exists():
        return None
    candidates = sorted(proposal_dir.glob("変更案_*.csv"))
    return candidates[-1] if candidates else None


def read_proposal_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"変更案CSVの列構成が不正です（不足列: {missing}）: {csv_path}")
        return list(reader)


def apply_approved_changes(
    root: Path,
    csv_path: Path | None = None,
    when: datetime | None = None,
) -> list[ApplyResult]:
    """承認済み行のみ、コピー・リネーム・分類フォルダへの複製を実行する。"""
    when = when or datetime.now()
    path = csv_path or _latest_proposal_csv(root)
    if path is None:
        raise FileNotFoundError("変更案CSVが見つかりません（先に generate_proposal_csv を実行してください）")

    rows = read_proposal_rows(path)
    results: list[ApplyResult] = []
    # 元ファイルパスがこの範囲外を指していたら実行しない（CSVが人手/外部チャネル経由で
    # 改変されても、受信箱の外にあるファイルを分類フォルダへコピーさせない安全弁）。
    inbox_dir = config.folder_path(root, config.INBOX).resolve()

    for i, row in enumerate(rows):
        src_raw = row.get("元ファイルパス", "")

        if not is_approved(row.get("承認", "")):
            # 未承認行も「見て・スキップした」ことを監査ログに残す（7-5「スキップ」）。
            logger.log_operation(
                root, logger.OP_SKIP, src_raw, "", logger.RESULT_OK, detail="未承認のため未実行", when=when
            )
            continue

        try:
            target_folder = row["提案カテゴリ"]
            filename = row["提案新ファイル名"]

            if target_folder not in ALLOWED_TARGET_FOLDERS:
                raise ValueError(f"未知の提案カテゴリのため実行不可: {target_folder!r}")

            src = Path(src_raw)
            if not src.is_file():
                raise FileNotFoundError("元ファイルが見つかりません（受信箱から移動・削除された可能性）")

            resolved_src = src.resolve()
            if not resolved_src.is_relative_to(inbox_dir):
                raise ValueError(f"元ファイルパスが受信箱の外を指しています（CSV改変の疑い）: {src_raw}")

            dest_dir = config.folder_path(root, target_folder)
            dest = safe_copy(resolved_src, dest_dir, filename)
        except (OSError, ValueError, KeyError) as exc:
            # KeyError=CSV列欠落、ValueError=カテゴリ/パス不正、OSError=I/O失敗。
            # いずれも1行の失敗でバッチ全体を止めない（8章）。
            logger.log_operation(
                root, logger.OP_RENAME_EXEC, src_raw, "", logger.RESULT_NG, detail=str(exc), when=when
            )
            results.append(ApplyResult(row_index=i, source_path=src_raw, ok=False, error=str(exc)))
            continue

        logger.log_operation(
            root, logger.OP_RENAME_EXEC, str(resolved_src), str(dest), logger.RESULT_OK, when=when
        )
        results.append(ApplyResult(row_index=i, source_path=str(resolved_src), ok=True, dest_path=str(dest)))

    return results
