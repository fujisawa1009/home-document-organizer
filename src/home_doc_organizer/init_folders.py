"""第1段階: フォルダ構成の初期化（REQUIREMENTS.md 3章）。

既存フォルダには一切手を触れない（中身のファイルには無干渉）。
存在しないフォルダのみ作成する、冪等な処理。
"""

from __future__ import annotations

from pathlib import Path

from . import config


def init_folders(root: Path) -> list[Path]:
    """書類整理ルート配下の固定フォルダ構成を作る。作成したフォルダの一覧を返す。"""
    created: list[Path] = []
    root.mkdir(parents=True, exist_ok=True)
    for name in config.ALL_FOLDERS:
        path = config.folder_path(root, name)
        if not path.exists():
            path.mkdir(parents=True)
            created.append(path)
    return created
