"""学習機能（CEO指示2026-08-16「将来的には自動振り分けされるように学習して」）。

やり方: LLM等の外部APIは使わない（トークンコスト0を維持するため・launchd無人実行の
前提と矛盾しないようにするため）。代わりに、承認された変更案の「発行元(判定)」列を
決定的なキーワードとして _学習データ/learned_rules.json に蓄積し、次回以降の
classify_document はまずこの学習ルールを試す、というシンプルな記憶ベースの学習。

- 学習元: apply_changes.py で承認行が実際にコピーされた時点の「提案カテゴリ・
  書類種別(判定)・発行元(判定)」列（CEOがCSVを訂正してから承認した場合はその
  訂正後の値が入っている＝これが正解データになる）。
- 学習の適用先: classify.classify_file(path, root=...) が、キーワード辞書より先に
  学習ルールを試す。一致すれば確信度「高」・理由「学習済みルールに一致」で即答する。
- 同じ発行元名の記録が複数回積み重なっても、ファイルは常に1エントリに正規化・上書き
  更新する（無限に増えて遅くならないようにするため）。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config

LEARNED_RULES_FILE = "learned_rules.json"


def _rules_path(root: Path) -> Path:
    return config.folder_path(root, config.LEARNING) / LEARNED_RULES_FILE


def load_rules(root: Path) -> list[dict]:
    path = _rules_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 破損していても学習機能無しで動作を継続する（8章の精神：処理を止めない）
        return []
    return data if isinstance(data, list) else []


def save_rule(root: Path, keyword: str, category_key: str, doc_type: str, issuer: str) -> None:
    """承認済みの分類結果を、今後のマッチに使えるキーワードとして記憶する。"""
    keyword = keyword.strip()
    if not keyword or not category_key:
        return

    rules = [r for r in load_rules(root) if r.get("keyword") != keyword]
    rules.append(
        {
            "keyword": keyword,
            "category_key": category_key,
            "doc_type": doc_type,
            "issuer": issuer,
        }
    )

    path = _rules_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def match(root: Path, text: str) -> dict | None:
    """本文に学習済みキーワードが含まれていれば、そのルールを返す。"""
    if not text:
        return None
    for rule in load_rules(root):
        keyword = rule.get("keyword", "")
        if keyword and keyword in text:
            return rule
    return None


def learn_from_applied_row(root: Path, row: dict, source_text: str) -> None:
    """apply_changes.py から呼ばれる: 承認・実行されたCSV行から学習ルールを1件蓄積する。

    「提案カテゴリ」が実カテゴリフォルダ（_要確認ではない）で、かつ「発行元(判定)」が
    ソース本文に実在する場合のみ学習する（本文に存在しないキーワードを覚えても
    次回一致しないため無意味・かつ誤爆のもとになる）。
    """
    target_folder = row.get("提案カテゴリ", "")
    category_key = config.FOLDER_TO_CATEGORY_KEY.get(target_folder)
    if category_key is None:  # _要確認 など、確定カテゴリでない行は学習しない
        return

    issuer = row.get("発行元(判定)", "").strip()
    if not issuer or issuer == "不明":
        return
    if config.parse_issuer_source(row.get("発行元の根拠")) == config.ISSUER_SOURCE_FALLBACK:
        # 推定で埋めた発行元（在籍期間照合）は学習しない。本文に実在しないので通常は
        # 次の `issuer not in source_text` でも落ちるが、「推定値は学習しない」という要件は
        # 本文との一致という副作用に頼らず、根拠の列で明示的に担保する（T-1230）。
        # 列が無い古い変更案CSVでは空文字＝この条件に当たらない（従来どおりの挙動）。
        return
    if issuer not in source_text:
        return  # 本文に存在しないキーワードは次回一致しないため学習しない

    doc_type = row.get("書類種別(判定)", "").strip() or "不明"
    save_rule(root, keyword=issuer, category_key=category_key, doc_type=doc_type, issuer=issuer)
