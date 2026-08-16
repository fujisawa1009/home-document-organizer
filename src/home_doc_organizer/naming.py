"""命名規則・安全コピーユーティリティ。

絶対原則（REQUIREMENTS.md 2章）を実装で担保する場所:
- 元ファイルは削除・移動しない（呼び出し側は必ず shutil.copy 系のみ使う）。
- 同名衝突は絶対に上書きしない（_2, _3 ... を付与）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# ファイル名として使えない/使いたくない文字 → 全角置換
_ILLEGAL_CHAR_MAP = {
    "/": "／",
    "\\": "＼",
    ":": "：",
    "*": "＊",
    "?": "？",
    '"': "”",
    "<": "＜",
    ">": "＞",
    "|": "｜",
}


def sanitize_component(value: str) -> str:
    """ファイル名の1要素（書類種別・発行元など）を安全な文字列にする。"""
    value = value.strip()
    for bad, good in _ILLEGAL_CHAR_MAP.items():
        value = value.replace(bad, good)
    # 制御文字は除去
    value = "".join(ch for ch in value if ch.isprintable())
    return value or "不明"


def build_filename(
    date_str: str,
    doc_type: str,
    issuer: str,
    ext: str,
    estimated: bool = False,
) -> str:
    """命名規則 `YYYYMMDD_書類種別_発行元.拡張子` に従ってファイル名を組み立てる。

    date_str は "YYYYMMDD" 形式で渡すこと（呼び出し側で検証済みとする）。
    estimated=True の場合、日付が読み取れず更新日時を採用したことを示す
    `_推定` を日付の直後に挿入する（例: 20260816_推定_納税証明書_税務署.pdf）。
    """
    doc_type = sanitize_component(doc_type)
    issuer = sanitize_component(issuer)
    ext = ext.lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    parts = [date_str, "推定", doc_type, issuer] if estimated else [date_str, doc_type, issuer]
    return "_".join(parts) + ext


def _validate_filename(filename: str) -> str:
    """dest_dir の外へ書き込ませないための最終防衛線。

    提案新ファイル名はCSV経由（人手編集・将来のTelegram/LINE承認連携）で
    改変されうるため、build_filename を経由しない値がここに来る前提で検証する。
    パス区切り・`..`・絶対パスを拒否する（パストラバーサル対策）。
    """
    p = Path(filename)
    if p.is_absolute() or p.name != filename or filename in ("", ".", ".."):
        raise ValueError(f"不正なファイル名です（パス区切り文字は使用不可）: {filename!r}")
    return filename


def unique_destination(dest_dir: Path, filename: str) -> Path:
    """dest_dir 内で filename と衝突しない一意なパスを返す（絶対に上書きしない）。

    同名が既にある場合は拡張子の直前に `_2`, `_3`, ... を付与する。
    注意: ここでの判定は参考用（check-then-act）。実際の書き込みは safe_copy が
    排他生成（O_CREAT|O_EXCL）で行うため、並行実行があっても上書きはされない。
    """
    filename = _validate_filename(filename)
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def safe_copy(src: Path, dest_dir: Path, filename: str) -> Path:
    """src を dest_dir へ filename としてコピーする（コピーのみ・絶対に上書きしない）。

    衝突検知(unique_destination)と実際の書き込みの間の競合（TOCTOU）でも上書きが
    起きないよう、実際のファイル作成は os.O_CREAT|O_EXCL で排他的に行う。他プロセスが
    同じ瞬間に同名を作っていた場合は FileExistsError を捕まえて次の空き名を探し直す。

    戻り値は実際に書き込んだパス（衝突があれば連番が付く）。
    """
    if not src.is_file():
        raise FileNotFoundError(f"コピー元がファイルではありません: {src}")
    filename = _validate_filename(filename)
    dest_dir.mkdir(parents=True, exist_ok=True)

    while True:
        dest = unique_destination(dest_dir, filename)
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue  # 他プロセスと競合＝次の空き名で再挑戦（上書きしない）
        break

    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            shutil.copyfileobj(inp, out)
        shutil.copystat(src, dest)
    except BaseException:
        dest.unlink(missing_ok=True)  # 書き込み失敗時に不完全なファイルを残さない
        raise
    return dest
