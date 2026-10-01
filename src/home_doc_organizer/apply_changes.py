"""第3・4段階: 承認後の実行（REQUIREMENTS.md 7-4・CEO指示2026-08-16で自動削除に変更）。

- 「承認」列にOK等の記入がある行のみ処理対象。
- 分類フォルダへは **コピー** で複製する（同名衝突は上書きせず連番付与＝naming.safe_copy が保証）。
- 受信箱の元ファイルは、コピー成功後に**自動削除する**（CEO指示2026-08-16「コピー先へ移動したら
  自動的に元ファイルを削除したい」）。以前は毎回都度確認だったが、この指示で運用変更。
  ただし絶対原則1の精神（バックアップ無しに消さない）は維持し、`_元ファイル保管`に複製済みで
  あることを確認できた場合のみ削除する（cleanup_inbox.is_archived と同じ判定を使う）。
  複製の確認が取れない場合は削除せずログに理由を残し、そのまま受信箱に残す（安全側）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import cleanup_inbox, config, extract, learning, logger
from .naming import safe_copy
from .proposal import REQUIRED_CSV_COLUMNS

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
    deleted_source: bool = False


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
        fieldnames = list(reader.fieldnames or [])
        # 重複列を拒否する: csv.DictReader は同名列があると**後ろの値**を採用するため、
        # 見た目の「承認」が空欄でも末尾に重複した「承認=OK」があれば実行されてしまう。
        if len(fieldnames) != len(set(fieldnames)):
            raise ValueError(f"変更案CSVに重複した列があります: {fieldnames}: {csv_path}")
        # 先頭は必須列が定義順どおりに並んでいること。後ろへ情報列が増えるのは許す
        # （`発行元の根拠` のような後から追記した列は古いCSVには存在しない／表計算ソフトが
        # 末尾に空列を足すことがある）。途中への列挿入・列順の入れ替えは拒否する。
        if fieldnames[: len(REQUIRED_CSV_COLUMNS)] != REQUIRED_CSV_COLUMNS:
            raise ValueError(
                f"変更案CSVの列構成が不正です（先頭{len(REQUIRED_CSV_COLUMNS)}列が"
                f"必須列と一致しません）: {fieldnames}: {csv_path}"
            )
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

            # 学習: CEOが承認した分類結果を今後のために記憶する（失敗しても実行結果には
            # 影響させない＝コピー自体は既に成功しているため）。
            try:
                source_text = extract.extract_text(resolved_src)
                learning.learn_from_applied_row(root, row, source_text)
            except Exception:  # noqa: BLE001 - 学習は付随機能。失敗してもapply自体は成功扱い
                pass
        except (OSError, ValueError, KeyError) as exc:
            # KeyError=CSV列欠落、ValueError=カテゴリ/パス不正、OSError=I/O失敗。
            # いずれも1行の失敗でバッチ全体を止めない（8章）。
            logger.log_operation(
                root, logger.OP_RENAME_EXEC, src_raw, "", logger.RESULT_NG, detail=str(exc), when=when
            )
            results.append(ApplyResult(row_index=i, source_path=src_raw, ok=False, error=str(exc)))
            continue

        # 推定で埋めた発行元の行は、実行ログ側にもその旨を残す（CSVは承認時に書き換えられうるが
        # ログは追記専用なので「どの実ファイルが推定値の名前で置かれたか」が後から追える）。
        # 根拠が推定でない行のログ内容は従来と同一（既存カテゴリのログを変えない）。
        # `row.get(...)` は、ヘッダーより少ないセル数の行では None を返す（Excel/Numbersや
        # 手修正で起きる）。ここはコピー成功後なので、素の文字列操作で AttributeError を
        # 出すとバッチの残り全行が未処理のまま止まる＝必ず正規化してから判定する。
        issuer_source = config.parse_issuer_source(row.get("発行元の根拠"))
        detail = (
            f"発行元の根拠={issuer_source}"
            if issuer_source == config.ISSUER_SOURCE_FALLBACK
            else ""
        )
        try:
            logger.log_operation(
                root,
                logger.OP_RENAME_EXEC,
                str(resolved_src),
                str(dest),
                logger.RESULT_OK,
                detail=detail,
                when=when,
            )
        except OSError:
            # ここはコピー成功後・受信箱削除前。ログ書き込みの失敗で例外を投げると、
            # 「コピー済みなのに元ファイルが残り、残りの行も未処理」のまま終わり、次回の
            # auto-run が同じファイルを再処理して連番コピー（_2）を作ってしまう。
            # ログが書けない状況（容量不足・権限）でも後処理と残りの行は進める。
            pass

        deleted_source = _delete_source_after_copy(root, resolved_src, when=when)
        results.append(
            ApplyResult(
                row_index=i,
                source_path=str(resolved_src),
                ok=True,
                dest_path=str(dest),
                deleted_source=deleted_source,
            )
        )

    return results


def _delete_source_after_copy(root: Path, src: Path, when: datetime) -> bool:
    """コピー成功直後に受信箱の元ファイルを自動削除する（CEO指示2026-08-16）。

    `_元ファイル保管` に複製済みであることを確認できた場合のみ削除する。確認できない
    場合は削除せず理由をログに残す（絶対原則1の精神＝バックアップ無しに消さない、は維持）。
    削除自体の失敗はコピーの成功結果には影響させない（apply全体は成功扱いのまま）。
    """
    if not cleanup_inbox.is_archived(root, src):
        logger.log_operation(
            root,
            logger.OP_INBOX_DELETE,
            str(src),
            "",
            logger.RESULT_NG,
            detail="_元ファイル保管に複製が確認できないため自動削除をスキップ（受信箱に残置）",
            when=when,
        )
        return False
    try:
        src.unlink()
    except OSError as exc:
        logger.log_operation(
            root, logger.OP_INBOX_DELETE, str(src), "", logger.RESULT_NG, detail=str(exc), when=when
        )
        return False
    logger.log_operation(
        root,
        logger.OP_INBOX_DELETE,
        str(src),
        "",
        logger.RESULT_OK,
        detail="apply成功後の自動削除（_元ファイル保管に複製済みを確認済み）",
        when=when,
    )
    return True
