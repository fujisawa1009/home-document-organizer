"""CLIエントリポイント（プロトタイプ・手動実行）。

例:
  python -m home_doc_organizer init
  python -m home_doc_organizer scan-inbox
  python -m home_doc_organizer propose
  python -m home_doc_organizer apply --csv "_変更案/変更案_20260816_120000.csv"
  python -m home_doc_organizer cleanup-inbox
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .apply_changes import apply_approved_changes
from .cleanup_inbox import cleanup_inbox
from .inbox_scan import archive_inbox
from .init_folders import init_folders
from .proposal import generate_proposal_csv


def _add_root_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="書類整理ルートのパス（省略時は環境変数 HOME_DOC_ROOT または既定のiCloudパス）",
    )


def cmd_init(args: argparse.Namespace) -> int:
    root = config.get_root(args.root)
    created = init_folders(root)
    print(f"書類整理ルート: {root}")
    if created:
        print("作成したフォルダ:")
        for p in created:
            print(f"  - {p}")
    else:
        print("既に全フォルダが存在します（変更なし）")
    return 0


def cmd_scan_inbox(args: argparse.Namespace) -> int:
    root = config.get_root(args.root)
    results = archive_inbox(root)
    ok = sum(1 for r in results if r.ok)
    ng = len(results) - ok
    print(f"受信箱スキャン完了: {len(results)}件（成功{ok}件 / 失敗{ng}件）")
    for r in results:
        if not r.ok:
            print(f"  [失敗] {r.source}: {r.error}")
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    root = config.get_root(args.root)
    path, rows = generate_proposal_csv(root)
    print(f"変更案CSVを作成しました: {path}")
    print(f"件数: {len(rows)}")
    for row in rows:
        print(f"  - {row.source_name} → [{row.suggested_category}] {row.suggested_filename}（確信度:{row.confidence}）")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    root = config.get_root(args.root)
    csv_path = args.csv
    results = apply_approved_changes(root, csv_path=csv_path)
    ok = sum(1 for r in results if r.ok)
    ng = len(results) - ok
    print(f"承認済み行の実行完了: {len(results)}件（成功{ok}件 / 失敗{ng}件）")
    for r in results:
        if r.ok:
            print(f"  [OK] {r.source_path} → {r.dest_path}")
        else:
            print(f"  [NG] {r.source_path}: {r.error}")
    return 0


def cmd_cleanup_inbox(args: argparse.Namespace) -> int:
    root = config.get_root(args.root)
    results = cleanup_inbox(root)
    deleted = sum(1 for r in results if r.deleted)
    print(f"受信箱クリーンアップ: 対象{len(results)}件中 削除{deleted}件")
    for r in results:
        status = "削除" if r.deleted else f"スキップ（{r.reason}）"
        print(f"  - {r.path.name}: {status}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="home-doc-organizer", description="家庭書類自動整理システム")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="第1段階: フォルダ構成を初期化")
    _add_root_arg(p_init)
    p_init.set_defaults(func=cmd_init)

    p_scan = sub.add_parser("scan-inbox", help="第1段階: 受信箱を元ファイル保管へ複製")
    _add_root_arg(p_scan)
    p_scan.set_defaults(func=cmd_scan_inbox)

    p_propose = sub.add_parser("propose", help="第2段階: 内容を分類し変更案.csvを作成")
    _add_root_arg(p_propose)
    p_propose.set_defaults(func=cmd_propose)

    p_apply = sub.add_parser("apply", help="第3・4段階: 承認済み行を実行（コピー・リネーム・分類配置）")
    _add_root_arg(p_apply)
    p_apply.add_argument("--csv", type=Path, default=None, help="対象の変更案CSV（省略時は最新のもの）")
    p_apply.set_defaults(func=cmd_apply)

    p_cleanup = sub.add_parser("cleanup-inbox", help="保管済み受信箱ファイルの削除（1件ずつ対話確認）")
    _add_root_arg(p_cleanup)
    p_cleanup.set_defaults(func=cmd_cleanup_inbox)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
