from datetime import datetime
from pathlib import Path

from home_doc_organizer import config, learning
from home_doc_organizer.classify import classify_document, classify_file
from home_doc_organizer.init_folders import init_folders


FIXED_MTIME = datetime(2026, 1, 1)


def test_tax_document_high_confidence_with_reiwa_date():
    text = "麹町税務署　納税証明書　令和7年8月16日交付"
    result = classify_document(text, ".pdf", FIXED_MTIME)

    assert result.category_key == "税金"
    assert result.suggested_folder == config.CATEGORY_TAX
    assert result.doc_type == "納税証明書"
    assert result.issuer == "麹町税務署"
    assert result.date_str == "20250816"
    assert result.estimated_date is False
    assert result.confidence == "高"


def test_heisei_era_date_conversion():
    text = "納税証明書　平成31年4月1日"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.date_str == "20190401"


def test_reiwa_gannen_first_year_kanji_notation():
    text = "納税証明書　令和元年5月1日交付"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.date_str == "20190501"
    assert result.estimated_date is False


def test_heisei_gannen_first_year_kanji_notation():
    text = "納税証明書　平成元年1月8日"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.date_str == "19890108"
    assert result.estimated_date is False


def test_western_date_format():
    text = "納税証明書　2026/08/16 発行　税務署"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.date_str == "20260816"
    assert result.estimated_date is False


def test_generic_bank_keywords_only_gives_medium_confidence():
    text = "みずほ銀行　支店　口座番号1234567"
    result = classify_document(text, ".pdf", FIXED_MTIME)

    assert result.category_key == "銀行"
    assert result.doc_type == "不明（要確認）"
    assert result.issuer == "みずほ銀行"
    assert result.confidence == "中"
    assert result.suggested_folder == config.CATEGORY_BANK


def test_unsupported_extension_goes_to_needs_review():
    result = classify_document("何か本文", ".txt", FIXED_MTIME)
    assert result.suggested_folder == config.NEEDS_REVIEW
    assert result.confidence == "低"
    assert "対象外拡張子" in result.reason


def test_empty_text_goes_to_needs_review_with_estimated_date():
    result = classify_document("", ".pdf", FIXED_MTIME)
    assert result.suggested_folder == config.NEEDS_REVIEW
    assert result.confidence == "低"
    assert result.estimated_date is True
    assert result.date_str == FIXED_MTIME.strftime("%Y%m%d")


def test_no_keyword_match_goes_to_needs_review():
    text = "本日は晴天なり。特に意味のない文章です。"
    result = classify_document(text, ".jpg", FIXED_MTIME)
    assert result.suggested_folder == config.NEEDS_REVIEW
    assert result.confidence == "低"


def test_identification_document():
    text = "運転免許証　東京都公安委員会　有効期限"
    result = classify_document(text, ".jpg", FIXED_MTIME)
    assert result.category_key == "身分証"
    assert result.doc_type == "運転免許証"
    assert result.suggested_folder == config.CATEGORY_ID


def test_classify_file_prefers_learned_rule_over_keyword_dictionary(tmp_path, monkeypatch):
    """dカードのような未知の書類でも、一度CEOが承認すれば次回から学習ルールで
    高確信度に分類されること（キーワード辞書の更新は不要）。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    learning.save_rule(root, keyword="dカード", category_key="銀行", doc_type="クレジットカード", issuer="dカード")

    src = tmp_path / "card.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        lambda path: "d POINT CARD ... dカードのご利用明細",
    )

    result = classify_file(src, root=root)
    assert result.category_key == "銀行"
    assert result.doc_type == "クレジットカード"
    assert result.issuer == "dカード"
    assert result.confidence == "高"
    assert "学習済みルール" in result.reason


def test_classify_file_without_root_ignores_learning(tmp_path, monkeypatch):
    src = tmp_path / "card.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        lambda path: "d POINT CARD dカード会員 YUTA FUJISAWA 1234 5678 9012 3456",
    )
    result = classify_file(src)  # root省略＝学習ルールを見ない
    assert result.confidence == "低"  # どのキーワード辞書にも一致しないため要確認
