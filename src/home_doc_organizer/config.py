"""フォルダ構成・パス設定。

実パスは環境変数 HOME_DOC_ROOT で上書きできる（デフォルトはCEO確認済みの実iCloudパス）。
テスト・デモ実行では必ず HOME_DOC_ROOT で一時ディレクトリに差し替えること
（本番の「書類整理ルート」を誤って書き換えないため）。
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ROOT = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "書類整理ルート"
)

INBOX = "00_受信箱"
CATEGORY_TAX = "01_税金"
CATEGORY_BANK = "02_銀行"
CATEGORY_INSURANCE = "03_保険"
CATEGORY_ID = "04_身分証"
CATEGORY_OTHER = "05_その他"
NEEDS_REVIEW = "_要確認"
ORIGINAL_ARCHIVE = "_元ファイル保管"
PROPOSALS = "_変更案"
LOGS = "_ログ"
LEARNING = "_学習データ"

CATEGORY_FOLDERS = (
    CATEGORY_TAX,
    CATEGORY_BANK,
    CATEGORY_INSURANCE,
    CATEGORY_ID,
    CATEGORY_OTHER,
)

ALL_FOLDERS = (
    INBOX,
    *CATEGORY_FOLDERS,
    NEEDS_REVIEW,
    ORIGINAL_ARCHIVE,
    PROPOSALS,
    LOGS,
    LEARNING,
)

# 分類キー（classify.py の判定結果）→ 実フォルダ名
CATEGORY_KEY_TO_FOLDER = {
    "税金": CATEGORY_TAX,
    "銀行": CATEGORY_BANK,
    "保険": CATEGORY_INSURANCE,
    "身分証": CATEGORY_ID,
    "その他": CATEGORY_OTHER,
}

# 逆引き（実フォルダ名 → 分類キー）。学習機能が「承認された提案カテゴリ」から
# category_key を復元するために使う。
FOLDER_TO_CATEGORY_KEY = {v: k for k, v in CATEGORY_KEY_TO_FOLDER.items()}

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic"}


def get_root(root: Path | str | None = None) -> Path:
    """書類整理ルートの実パスを返す。

    優先順位: 明示引数 > 環境変数 HOME_DOC_ROOT > デフォルト（実iCloudパス）。
    """
    if root is not None:
        return Path(root).expanduser()
    env = os.environ.get("HOME_DOC_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_ROOT


def folder_path(root: Path, name: str) -> Path:
    return root / name
