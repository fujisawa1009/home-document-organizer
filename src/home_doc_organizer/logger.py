"""操作ログ（REQUIREMENTS.md 7-5）。

`_ログ/操作ログ_YYYYMMDD.csv` に日付ごとに追記する。ログ書き込み自体の失敗で
処理全体を止めないこと（8章「エラーで処理全体を止めない」の精神を維持するため、
呼び出し側でログ書き込みを try/except で囲む運用を想定）。
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from . import config

LOG_HEADER = ["実行日時", "操作種別", "元ファイルパス", "新ファイルパス", "結果", "詳細・エラー内容"]

OP_ARCHIVE_COPY = "元ファイル保管コピー"
OP_PROPOSAL_CREATE = "変更案作成"
OP_RENAME_EXEC = "リネーム実行"
OP_CLASSIFY_ERROR = "分類エラー"
OP_SKIP = "スキップ"
OP_INBOX_DELETE = "受信箱元ファイル削除"
# 発行元を書類から読み取れず、在籍期間照合で推定した（T-1230）。読み取り値との区別を
# 追記専用のログにも残すための種別。
OP_ISSUER_FALLBACK = "発行元推定"

RESULT_OK = "成功"
RESULT_NG = "失敗"


def log_path_for(root: Path, when: datetime) -> Path:
    log_dir = config.folder_path(root, config.LOGS)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"操作ログ_{when.strftime('%Y%m%d')}.csv"


def log_operation(
    root: Path,
    operation: str,
    src_path: str,
    dest_path: str,
    result: str,
    detail: str = "",
    when: datetime | None = None,
) -> Path:
    """1件の操作ログを追記する。戻り値は書き込んだログファイルのパス。"""
    when = when or datetime.now()
    path = log_path_for(root, when)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(LOG_HEADER)
        writer.writerow([when.isoformat(timespec="seconds"), operation, src_path, dest_path, result, detail])
    return path
