from pathlib import Path

from home_doc_organizer import config, learning
from home_doc_organizer.init_folders import init_folders


def _root(tmp_path: Path) -> Path:
    r = tmp_path / "書類整理ルート"
    init_folders(r)
    return r


def test_match_with_no_rules_returns_none(tmp_path: Path):
    root = _root(tmp_path)
    assert learning.match(root, "dカードのご利用明細") is None


def test_save_and_match_rule(tmp_path: Path):
    root = _root(tmp_path)
    learning.save_rule(root, keyword="dカード", category_key="銀行", doc_type="クレジットカード", issuer="dカード")

    rule = learning.match(root, "本文中にdカードという文字列が含まれる")
    assert rule is not None
    assert rule["category_key"] == "銀行"
    assert rule["doc_type"] == "クレジットカード"
    assert rule["issuer"] == "dカード"


def test_match_returns_none_when_keyword_absent(tmp_path: Path):
    root = _root(tmp_path)
    learning.save_rule(root, keyword="dカード", category_key="銀行", doc_type="クレジットカード", issuer="dカード")
    assert learning.match(root, "全く関係ない文章です") is None


def test_save_rule_overwrites_same_keyword(tmp_path: Path):
    root = _root(tmp_path)
    learning.save_rule(root, keyword="dカード", category_key="銀行", doc_type="旧種別", issuer="dカード")
    learning.save_rule(root, keyword="dカード", category_key="銀行", doc_type="新種別", issuer="dカード")

    rules = learning.load_rules(root)
    assert len(rules) == 1
    assert rules[0]["doc_type"] == "新種別"


def test_learn_from_applied_row_skips_needs_review(tmp_path: Path):
    root = _root(tmp_path)
    row = {
        "提案カテゴリ": config.NEEDS_REVIEW,
        "書類種別(判定)": "クレジットカード",
        "発行元(判定)": "dカード",
    }
    learning.learn_from_applied_row(root, row, "dカードのご案内")
    assert learning.load_rules(root) == []


def test_learn_from_applied_row_skips_when_issuer_not_in_text(tmp_path: Path):
    root = _root(tmp_path)
    row = {
        "提案カテゴリ": config.CATEGORY_BANK,
        "書類種別(判定)": "クレジットカード",
        "発行元(判定)": "dカード",
    }
    learning.learn_from_applied_row(root, row, "本文にキーワードが含まれない")
    assert learning.load_rules(root) == []


def test_learn_from_applied_row_skips_when_issuer_unknown(tmp_path: Path):
    root = _root(tmp_path)
    row = {
        "提案カテゴリ": config.CATEGORY_BANK,
        "書類種別(判定)": "クレジットカード",
        "発行元(判定)": "不明",
    }
    learning.learn_from_applied_row(root, row, "不明という単語を含む本文")
    assert learning.load_rules(root) == []


def test_learn_from_applied_row_saves_valid_rule(tmp_path: Path):
    root = _root(tmp_path)
    row = {
        "提案カテゴリ": config.CATEGORY_BANK,
        "書類種別(判定)": "クレジットカード",
        "発行元(判定)": "dカード",
    }
    learning.learn_from_applied_row(root, row, "d POINT CARD ... dカードのご利用明細")

    rules = learning.load_rules(root)
    assert len(rules) == 1
    assert rules[0] == {
        "keyword": "dカード",
        "category_key": "銀行",
        "doc_type": "クレジットカード",
        "issuer": "dカード",
    }


def test_learn_skips_rows_whose_issuer_was_inferred(tmp_path: Path):
    """推定で埋めた発行元（在籍期間照合）は学習しない（T-1230）。

    推定値を学習すると、次回以降は「学習済みルールに一致・確信度高」として扱われ、
    推定だったことが消えてしまう。本文に実在するかどうかに頼らず、根拠の列で弾く。
    """
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    row = {
        "提案カテゴリ": config.CATEGORY_PAYROLL,
        "書類種別(判定)": "給与明細",
        "発行元(判定)": "株式会社エフティグループ",
        "発行元の根拠": "fallback（在籍期間照合による推定・読み取り値ではない）",
    }
    # 本文に社名が実在していても（＝従来の条件は通る）学習しない
    learning.learn_from_applied_row(root, row, "株式会社エフティグループ 給与明細書")

    assert learning.load_rules(root) == []


def test_learn_still_works_for_issuer_read_from_text(tmp_path: Path):
    """読み取り値（text）の行は従来どおり学習する（リグレッション防止）。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    row = {
        "提案カテゴリ": config.CATEGORY_PAYROLL,
        "書類種別(判定)": "給与明細",
        "発行元(判定)": "株式会社エフティグループ",
        "発行元の根拠": "text（本文からの読み取り値）",
    }
    learning.learn_from_applied_row(root, row, "株式会社エフティグループ 給与明細書")

    assert [r["keyword"] for r in learning.load_rules(root)] == ["株式会社エフティグループ"]
