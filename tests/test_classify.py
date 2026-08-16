from datetime import datetime

from home_doc_organizer import config
from home_doc_organizer.classify import classify_document


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
