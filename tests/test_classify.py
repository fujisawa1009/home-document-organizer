import pytest

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


def test_classify_file_uses_filename_as_hint_when_ocr_is_garbled(tmp_path, monkeypatch):
    """免許証はOCR精度が特に厳しく本文からキーワードを拾えないことがある。
    受信箱に入れる前にファイル名へヒントを入れれば、OCRが失敗しても分類できること。"""
    src = tmp_path / "免許証_表.jpg"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        lambda path: "59% 10有 9日生\n06370\n9012118038",  # 実際のOCR結果を模した文字化け
    )

    result = classify_file(src)
    assert result.category_key == "身分証"
    assert result.doc_type == "運転免許証"
    assert result.suggested_folder == config.CATEGORY_ID


def test_classify_file_uses_subfolder_name_as_hint(tmp_path, monkeypatch):
    """00_受信箱/01_証明写真/IMG_1234.jpg のように、受信箱直下のサブフォルダ名も
    分類ヒントとして使われること（CEO指示2026-08-16）。"""
    subdir = tmp_path / "01_証明写真"
    subdir.mkdir()
    src = subdir / "IMG_1234.jpg"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        lambda path: "",  # OCRは何も拾えない想定（写真のみ・文字なし）
    )

    result = classify_file(src)
    assert result.category_key == "身分証"
    assert result.doc_type == "証明写真"
    assert result.suggested_folder == config.CATEGORY_ID


def test_classify_file_without_filename_hint_falls_back_to_needs_review(tmp_path, monkeypatch):
    src = tmp_path / "IMG_6930.jpg"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        lambda path: "59% 10有 9日生\n06370\n9012118038",
    )
    result = classify_file(src)
    assert result.confidence == "低"
    assert result.suggested_folder == config.NEEDS_REVIEW


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


# --- 給与・賞与明細カテゴリ（06_給与）--------------------------------------------
#
# 2026-09-29 の自動振り分けで給与明細10件が誤分類された（9件が `_要確認` で
# ファイル名が `不明_不明`、1件が本文の「住民税」に反応して
# `01_税金/住民税決定通知書` 行き）。その再発防止のテスト群。

# 2026年6月分の実物PDF（テキスト層あり）から抽出した本文をそのまま使う。
# 税金・保険カテゴリのキーワード（住民税/所得税/健康保険料/厚生年金保険料）を
# 実際に含んでいることが、このテストの価値の源泉。
REAL_PAYSLIP_TEXT = (
    "社員ｺｰﾄﾞ：0327312社員氏名：藤澤　祐太様親展\n"
    "給与明細書事業所名：ｴﾌﾃｨｸﾞﾙｰﾌﾟｸﾞﾙｰﾌﾟ名:2026年6月給与 所属･部門名：ｼｽﾃﾑ企画課 "
    "ｼｽﾃﾑ企画課控除支給 勤怠金額項目 項目項目 金額 時間･日数本人給健康保険料出勤日数"
    "     280,000      23,100       21.00職能給介護保険料稼動日数           0        3,760"
    "       21.00確定拠出年金手当厚生年金保険料      46,000      43,005            "
    "職級手当雇用保険料有休残           0        2,343       10.50役職手当社会保険計"
    "     134,000      72,208            通勤手当所得税       8,680      14,700            "
    "住民税                  18,300総支給額控除合計差引支給額     468,680     105,208     363,472\n"
    "株式会社エフティグループ"
)


def test_payslip_is_not_absorbed_by_tax_category():
    """給与明細は「住民税」「所得税」「健康保険料」を必ず含むが、税金・保険ではなく
    給与カテゴリへ入ること（2026-09-29の実誤分類の再発防止）。"""
    result = classify_document(REAL_PAYSLIP_TEXT, ".pdf", FIXED_MTIME)

    assert result.category_key == "給与"
    assert result.suggested_folder == config.CATEGORY_PAYROLL
    assert result.suggested_folder != config.CATEGORY_TAX
    assert result.doc_type == "給与明細"


def test_real_payslip_extracts_issuer_and_payment_month():
    """発行元（法人格が前に付く表記）と支給年月が抽出され、確信度が高になること。
    日付は明細票が日を持たないため YYYYMM01 固定・推定扱いにはしない。"""
    result = classify_document(REAL_PAYSLIP_TEXT, ".pdf", FIXED_MTIME)

    assert result.issuer == "株式会社エフティグループ"
    assert result.date_str == "20260601"
    assert result.estimated_date is False
    assert result.confidence == "高"


def test_payslip_detected_from_filename_only_when_ocr_returns_nothing(tmp_path, monkeypatch):
    """スキャンPDFはOCRが1文字も返さない実物がある（実測）。その場合でも
    `202601月給与.pdf` というファイル名だけで給与カテゴリ・支給年月が決まること。"""
    src = tmp_path / "202601月給与.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    result = classify_file(src)
    assert result.category_key == "給与"
    assert result.suggested_folder == config.CATEGORY_PAYROLL
    assert result.doc_type == "給与明細"
    assert result.date_str == "20260101"
    assert result.estimated_date is False
    assert result.confidence == "中"  # 発行元が読めないので高にはしないが_要確認へも倒さない


def test_bonus_statement_is_distinguished_from_salary(tmp_path, monkeypatch):
    src = tmp_path / "202607月賞与.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    result = classify_file(src)
    assert result.category_key == "給与"
    assert result.doc_type == "賞与明細"
    assert result.date_str == "20260701"


def test_payslip_detected_from_field_markers_when_doc_title_is_garbled():
    """書類名も支給年月も読めないほどOCRが崩れても、明細票固有の欄名が閾値以上
    そろえば給与明細と判定できること。"""
    # 先頭の「###」は書類名が読めなかった状態の代用（「給与明細書」が崩れて拾えない想定）
    text = "### 本人給 役職手当 通勤手当 総支給額 控除合計 差引支給額"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key == "給与"
    assert result.doc_type == "給与明細"
    assert result.estimated_date is True  # 支給年月が読めないので更新日時で代用
    assert result.date_str == FIXED_MTIME.strftime("%Y%m%d")


def test_bonus_detected_from_bonus_only_field_markers():
    text = "### 基本賞与 賞与社会保険合計 総支給額 控除合計 差引支給額"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key == "給与"
    assert result.doc_type == "賞与明細"


def test_issuer_with_trailing_corporate_suffix():
    """法人格が後ろに付く表記（エフティグループ株式会社）でも発行元が取れること。"""
    text = "給与明細書 2026年6月給与 本人給 総支給額 差引支給額\nエフティグループ株式会社"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.issuer == "エフティグループ株式会社"


def test_residence_tax_notice_still_goes_to_tax_category():
    """既存カテゴリのリグレッション防止: 住民税決定通知書は従来どおり 01_税金。
    給与の優先判定に吸われないこと。"""
    text = "麹町区役所　住民税決定通知書　特別徴収税額　給与所得　2026年6月から"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key == "税金"
    assert result.suggested_folder == config.CATEGORY_TAX
    assert result.doc_type == "住民税決定通知書"


def test_withholding_slip_is_not_classified_as_payroll():
    """源泉徴収票は給与の集計表だが明細票ではない（従来どおり税金カテゴリ側で扱う）。"""
    text = "2026年分 給与所得の源泉徴収票 支払金額 給与所得控除後の金額 税務署"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key != "給与"
    assert result.suggested_folder != config.CATEGORY_PAYROLL


def test_generic_sentence_about_salary_is_not_classified_as_payroll():
    """「毎月の給与から差し引かれます」のような一般文で給与判定が発火しないこと
    （支給年月＋種別の隣接・欄名の閾値の両方を満たさない）。"""
    text = "保険料は毎月の給与から差し引かれます。ご契約内容のお知らせ　◯◯生命保険"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key == "保険"
    assert result.suggested_folder == config.CATEGORY_INSURANCE


def test_learned_payroll_rule_keeps_issuer_but_gets_payment_month(tmp_path, monkeypatch):
    """学習ルールは日付を覚えない（更新日時を採用する）。給与カテゴリだけは
    支給年月を優先判定から補い、月ごとに違うファイル名になること。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    learning.save_rule(
        root,
        keyword="給与明細書",
        category_key="給与",
        doc_type="給与明細",
        issuer="株式会社エフティグループ",
    )

    src = tmp_path / "202603月給与.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text", lambda path: "給与明細書 本人給 総支給額"
    )

    result = classify_file(src, root=root)
    assert result.category_key == "給与"
    assert result.issuer == "株式会社エフティグループ"  # 学習ルール側（決定者が確認済みの値）を残す
    assert result.date_str == "20260301"  # 支給年月はファイル名から補う
    assert result.estimated_date is False
    assert result.confidence == "高"


def test_learned_rule_for_other_categories_still_uses_mtime(tmp_path, monkeypatch):
    """給与以外のカテゴリでは学習ルールの挙動を一切変えないこと（日付は更新日時）。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    learning.save_rule(
        root, keyword="dカード", category_key="銀行", doc_type="クレジットカード", issuer="dカード"
    )
    src = tmp_path / "card.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text", lambda path: "dカードのご利用明細 2026年3月15日"
    )

    result = classify_file(src, root=root)
    mtime = datetime.fromtimestamp(src.stat().st_mtime)
    assert result.category_key == "銀行"
    assert result.date_str == mtime.strftime("%Y%m%d")
    assert result.estimated_date is True


def test_bonus_in_salary_subfolder_is_still_classified_as_bonus(tmp_path, monkeypatch):
    """`00_受信箱/給与明細/202607月賞与.pdf` のようにサブフォルダ名が「給与明細」でも、
    ファイル名の「賞与」が優先され賞与明細と判定されること。
    （サブフォルダ名もヒントとして本文へ連結されるため、書類名キーワードだけで決めると
    同月の給与明細とファイル名が衝突する。実物10件のドライランで実際に発生した。）"""
    subdir = tmp_path / "給与明細"
    subdir.mkdir()
    src = subdir / "202607月賞与.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    result = classify_file(src)
    assert result.doc_type == "賞与明細"
    assert result.date_str == "20260701"


def test_explicit_bonus_title_wins_over_month_label():
    """「賞与明細書」という明示の書類名は、支給年月の「◯月給与」表記より優先されること。"""
    text = "賞与明細書 2026年7月給与 基本賞与 総支給額 差引支給額"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.doc_type == "賞与明細"


def test_body_only_month_label_is_not_enough_to_be_payroll():
    """本文に「2026年6月給与」と書かれているだけでは給与判定しないこと。
    他カテゴリの書類の一文（「保険料は2026年6月給与から控除します」）と区別できないため、
    支給年月表記は①明示の書類名 or ②ファイル名ヒント or ③欄名 のいずれかと併せて初めて成立する。"""
    text = "ご契約内容のお知らせ　保険料は2026年6月給与から控除します　◯◯生命保険"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key == "保険"
    assert result.suggested_folder == config.CATEGORY_INSURANCE


def test_month_label_across_newlines_does_not_match():
    """OCRで別行になった「2026年」「6月」「給与から…」が偶然つながって
    給与判定されないこと（支給年月表記は改行を許さない）。"""
    text = "対象年度 2026年\n6月\n給与から控除する保険料について\n保険証券"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key != "給与"


def test_exclusion_keyword_does_not_reject_document_with_explicit_payroll_title():
    """明細の注記に「確定申告」等の税金カテゴリの語が混ざっても、明示の書類名があれば
    給与明細として扱うこと（除外語は絶対拒否ではなく対抗証拠）。"""
    text = "給与明細書 2026年6月給与 総支給額 控除合計 差引支給額 ※確定申告の際はこの明細を保管してください"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key == "給与"
    assert result.doc_type == "給与明細"


def test_full_width_digits_are_normalized(tmp_path, monkeypatch):
    """全角数字・半角カナの表記ゆれ（NFKC正規化）でも支給年月が読めること。"""
    src = tmp_path / "２０２６年６月給与.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    result = classify_file(src)
    assert result.category_key == "給与"
    assert result.date_str == "20260601"


def test_reiwa_year_month_notation(tmp_path, monkeypatch):
    """和暦表記（令和8年6月給与）でも支給年月が読めること。"""
    src = tmp_path / "令和8年6月給与.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    result = classify_file(src)
    assert result.category_key == "給与"
    assert result.date_str == "20260601"  # 令和8年 = 2026年


def test_ocr_garbled_year_is_rejected():
    """OCRで桁が化けた年（2096年）を支給年月として採用しないこと。
    書類名があるので給与判定自体は成立するが、日付は更新日時へ退避する。"""
    text = "給与明細書 2096年6月給与 総支給額 控除合計 差引支給額"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key == "給与"
    assert result.date_str == FIXED_MTIME.strftime("%Y%m%d")
    assert result.estimated_date is True
    assert result.date_precision == "mtime"


def test_supporting_field_markers_alone_are_not_payroll():
    """「基本給・役職手当・通勤手当」だけの求人票・給与規程を給与明細と誤判定しないこと
    （明細票にしか出ない中核欄を必ず1件以上要求する）。"""
    text = "賃金規程　基本給　役職手当　通勤手当　の支給基準について"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.category_key != "給与"


def test_payroll_date_precision_is_month():
    """給与明細の日付精度が month（日は01固定）として記録されること。
    CSVの日付欄に「支給年月のみ」の注記を出すために使う。"""
    result = classify_document(REAL_PAYSLIP_TEXT, ".pdf", FIXED_MTIME)
    assert result.date_precision == "month"


def test_issuer_prefers_last_corporate_name_occurrence():
    """本文中に一般文の「株式会社…」が先に出ていても、フッタの発行者名を採ること
    （明細票は発行者名を最終行に刷るのが通例）。"""
    text = "株式会社からのお知らせ\n給与明細書 2026年6月給与 総支給額 差引支給額\n株式会社本命商事"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.issuer == "株式会社本命商事"


def test_learned_payroll_rule_requires_payslip_corroboration(tmp_path, monkeypatch):
    """給与カテゴリの学習ルールは勤務先の社名で当たりがちなので、明細票である裏付けが
    取れないときは使わないこと。同じ勤務先が出す源泉徴収票を給与明細にしないための防御。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    learning.save_rule(
        root,
        keyword="株式会社エフティグループ",
        category_key="給与",
        doc_type="給与明細",
        issuer="株式会社エフティグループ",
    )

    src = tmp_path / "scan001.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        lambda path: "2026年分 給与所得の源泉徴収票 株式会社エフティグループ 支払金額 麹町税務署",
    )

    result = classify_file(src, root=root)
    assert result.category_key != "給与"
    assert result.suggested_folder != config.CATEGORY_PAYROLL


@pytest.mark.parametrize(
    ("text", "expected_folder"),
    [
        # 税書類の本文が「給与明細書」に言及しているだけのケース（除外語あり＋体裁の裏付けなし）
        (
            "麹町区役所　住民税決定通知書　特別徴収税額　詳細は給与明細書でご確認ください",
            config.CATEGORY_TAX,
        ),
        # 源泉徴収票（給与の集計表だが明細票ではない）
        ("2026年分 給与所得の源泉徴収票 支払金額 麹町税務署", config.CATEGORY_TAX),
        # 銀行の入出金明細。ファイル名が「◯月給与振込」でも給与明細ではない
        ("202601月給与振込\nみずほ銀行 ご利用明細 口座番号1234567", config.CATEGORY_BANK),
        # 保険の書類に明細票の欄名らしい語が混ざっても、既存カテゴリの書類名を優先する
        ("保険料控除証明書　総支給額　控除合計　◯◯生命保険", config.CATEGORY_INSURANCE),
        # 身分証
        ("運転免許証　東京都公安委員会　2026年6月給与所得者", config.CATEGORY_ID),
    ],
)
def test_existing_categories_are_not_absorbed_by_payroll(text, expected_folder):
    """既存カテゴリ（税金・銀行・保険・身分証）の書類が給与の優先判定に吸われないこと。
    給与判定は既存ロジックより先に走るため、ここが退行防止の要。"""
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.suggested_folder == expected_folder
    assert result.category_key != "給与"


def test_learned_salary_rule_does_not_turn_bonus_into_salary(tmp_path, monkeypatch):
    """給与明細として登録した学習ルールが賞与明細に当たっても、書類種別は実判定側
    （賞与明細）を採ること。学習ルールの書類種別を残すと賞与が給与の名前で保存される。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    learning.save_rule(
        root,
        keyword="株式会社エフティグループ",
        category_key="給与",
        doc_type="給与明細",
        issuer="株式会社エフティグループ",
    )

    src = tmp_path / "scan002.pdf"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        # 支給年月の表記は無く、書類名と欄名だけが読めた状態
        lambda path: "賞与明細書 株式会社エフティグループ 基本賞与 総支給額 差引支給額",
    )

    result = classify_file(src, root=root)
    assert result.category_key == "給与"
    assert result.doc_type == "賞与明細"
    assert result.date_precision == "mtime"  # 支給年月が読めないので学習ルールどおり更新日時


def test_classify_document_can_use_hint_text_when_body_is_empty():
    """本文が空でもヒント（ファイル名）だけで給与判定できること（公開関数の引数契約）。"""
    result = classify_document("", ".pdf", FIXED_MTIME, hint_text="202601月給与")
    assert result.category_key == "給与"
    assert result.date_str == "20260101"


def test_mixed_era_and_western_notation_takes_first_in_text_order():
    """西暦・和暦が混在する場合、テキストに現れた順で最初の妥当な支給年月を採ること。"""
    text = "給与明細書 令和8年6月給与 総支給額 差引支給額 ※前月は2026年5月給与でした"
    result = classify_document(text, ".pdf", FIXED_MTIME)
    assert result.date_str == "20260601"  # 令和8年6月 = 2026年6月（テキスト上で先に出る）


# --- 発行元の在籍期間照合フォールバック（T-1230） -------------------------------------
#
# 実物の給与明細10件のうち9件は pypdf もOCRも1文字も返さない（実測）。本文が無いので
# 発行元が読めず、ファイル名（`202601月給与.pdf`）にも社名が無いため全件 `_不明` になっていた。
# 「支給年月が在籍期間の中なら、その期間の勤務先を発行元として推定する」フォールバックを
# 足したので、効くケース（正例）と効いてはいけないケース（負例）の両方を固定する。


def _payroll_file(tmp_path: Path, name: str, monkeypatch, text: str = ""):
    """OCRが `text` を返すスキャンPDFを模したファイルを作る（既定は1文字も返さない）。"""
    src = tmp_path / name
    src.write_bytes(b"dummy")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: text)
    return src


def _employment_file(root: Path, payload: str) -> None:
    path = config.folder_path(root, config.LEARNING) / config.EMPLOYMENT_PERIODS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _months_from_now(delta_months: int) -> tuple[int, int]:
    now = datetime.now()
    total = now.year * 12 + (now.month - 1) + delta_months
    return total // 12, total % 12 + 1


def test_payroll_issuer_filled_from_employment_period(tmp_path, monkeypatch):
    """正例: 本文が1文字も読めなくても、支給年月が在籍期間内なら勤務先を推定して埋める。"""
    src = _payroll_file(tmp_path, "202601月給与.pdf", monkeypatch)

    result = classify_file(src)

    assert result.issuer == "株式会社エフティグループ"
    assert result.issuer_source == "fallback"
    assert "issuer_source=fallback" in result.reason  # 推定である印が備考に残る
    assert "在籍期間" in result.reason
    # 推定値は読み取り値と同じ扱いにしない＝確信度は「高」にしない
    assert result.confidence == "中"


def test_payroll_issuer_not_filled_before_employment_start(tmp_path, monkeypatch):
    """負例（最重要）: 在籍開始前（2024-09）の支給年月には適用しない＝捏造防止。"""
    src = _payroll_file(tmp_path, "202409月給与.pdf", monkeypatch)

    result = classify_file(src)

    assert result.category_key == "給与"  # 給与明細という判定自体は変わらない
    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"
    assert result.confidence == "中"


def test_payroll_issuer_not_filled_after_employment_end(tmp_path, monkeypatch):
    """負例: 在籍期間が終わっている（転職後）の月には前職の社名を書かない。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    _employment_file(
        root, '[{"employer": "株式会社前職", "start": "202410", "end": "202603"}]'
    )
    src = _payroll_file(tmp_path, "202604月給与.pdf", monkeypatch)

    result = classify_file(src, root=root)

    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"


def test_payroll_issuer_not_filled_for_far_future_month(tmp_path, monkeypatch):
    """負例: 「現在も在籍」の期間は上限が無いので、未来の支給年月は推定しない
    （翌月分までは許すが、それ以上先は在籍しているか分からない）。"""
    year, month = _months_from_now(6)
    src = _payroll_file(tmp_path, f"{year}{month:02d}月給与.pdf", monkeypatch)

    result = classify_file(src)

    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"


def test_payroll_issuer_not_filled_when_payment_month_unreadable():
    """負例: 支給年月が読めない（更新日時で代用した）明細には適用しない。
    更新日時はスキャンした日であって支給年月ではないため。"""
    text = "### 本人給 役職手当 通勤手当 総支給額 控除合計 差引支給額"
    result = classify_document(text, ".pdf", FIXED_MTIME)

    assert result.category_key == "給与"
    assert result.estimated_date is True
    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"


def test_payroll_issuer_read_from_text_wins_over_fallback():
    """本文から読める明細は従来どおり実判定を優先する（推定で上書きしない）。"""
    text = "給与明細書 2026年6月給与 本人給 総支給額 差引支給額\n株式会社別会社"
    result = classify_document(text, ".pdf", FIXED_MTIME)

    assert result.issuer == "株式会社別会社"
    assert result.issuer_source == "text"
    assert result.confidence == "高"


def test_employment_periods_file_overrides_default(tmp_path, monkeypatch):
    """在籍期間は設定ファイルで差し替えられる（転職してもコードを直さずに済む）。
    既定値とマージせず完全に置き換える＝古い勤務先名が残らないこと。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    _employment_file(root, '[{"employer": "株式会社転職先", "start": "202604"}]')
    src = _payroll_file(tmp_path, "202605月給与.pdf", monkeypatch)

    result = classify_file(src, root=root)

    assert result.issuer == "株式会社転職先"
    assert result.issuer_source == "fallback"


def test_broken_employment_periods_file_leaves_issuer_unknown(tmp_path, monkeypatch):
    """設定ファイルが壊れているときは既定値へ戻さず `不明` のまま残す
    （戻すと「書き換えたが壊れていた」ときに古い勤務先名を書いてしまう）。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    _employment_file(root, "{壊れたJSON")
    src = _payroll_file(tmp_path, "202601月給与.pdf", monkeypatch)

    result = classify_file(src, root=root)

    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"


def test_overlapping_employment_periods_are_not_guessed(tmp_path, monkeypatch):
    """期間が重なっていて勤務先が一意に決まらない月は推定しない（当てずっぽうを避ける）。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    _employment_file(
        root,
        '[{"employer": "株式会社A", "start": "202410", "end": "202612"},'
        ' {"employer": "株式会社B", "start": "202601"}]',
    )
    src = _payroll_file(tmp_path, "202603月給与.pdf", monkeypatch)

    result = classify_file(src, root=root)

    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"


def test_employment_fallback_does_not_leak_into_other_categories(tmp_path, monkeypatch):
    """既存4カテゴリ（税金/銀行/保険/身分証）の挙動は一切変えない。
    発行元が読めない税書類は従来どおり `不明` のまま（勤務先名で埋めない）。"""
    result = classify_document("納税証明書 2026年6月1日 交付", ".pdf", FIXED_MTIME)

    assert result.category_key == "税金"
    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"


def test_payroll_issuer_not_filled_beyond_verified_through(tmp_path, monkeypatch):
    """負例: 在籍中（end なし）でも「在籍を確認できている月＋猶予」を超えた支給年月には
    適用しない。転職して在籍期間表を直し忘れたときに前職の社名を書き続けないため。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    # 在籍確認は2025-01まで＝推定に使える上限は2026-01（猶予12か月）
    _employment_file(
        root,
        '[{"employer": "株式会社エフティグループ", "start": "202410",'
        ' "verified_through": "202501"}]',
    )
    src = _payroll_file(tmp_path, "202603月給与.pdf", monkeypatch)

    result = classify_file(src, root=root)

    assert result.issuer == "不明"
    assert result.issuer_source == "unknown"
    assert "在籍確認済みの範囲" in result.reason

    # 上限内（2026-01）の月なら従来どおり推定が働く
    src_in_range = _payroll_file(tmp_path, "202601月給与.pdf", monkeypatch)
    in_range = classify_file(src_in_range, root=root)
    assert in_range.issuer == "株式会社エフティグループ"
    assert in_range.issuer_source == "fallback"


def test_unknown_issuer_source_value_is_rejected():
    """根拠の値は定義済みのものだけ（未知の値を黙って記録させない）。"""
    from home_doc_organizer.classify import ClassificationResult

    with pytest.raises(ValueError):
        ClassificationResult(
            category_key="給与",
            suggested_folder=config.CATEGORY_PAYROLL,
            doc_type="給与明細",
            issuer="株式会社A",
            date_str="20260101",
            estimated_date=False,
            confidence="中",
            reason="テスト",
            issuer_source="でっちあげ",
        )


def test_future_month_judgement_uses_the_given_now(tmp_path, monkeypatch):
    """「未来の支給年月か」の判定は渡された実行時刻で決まること（実時計に依存しない）。

    バッチ開始時刻を渡す運用にしているので、月末深夜に日付が変わっても1回の実行の中で
    判定が揺れない（同じ支給年月のファイルが処理順によって推定される/されないに分かれない）。
    """
    src = _payroll_file(tmp_path, "202701月給与.pdf", monkeypatch)

    # 実行時刻が2026-12 → 翌月分(2027-01)は許容範囲内
    assert classify_file(src, now=datetime(2026, 12, 31, 23, 59)).issuer == "株式会社エフティグループ"
    # 実行時刻が2026-11 → 2か月先なので推定しない
    assert classify_file(src, now=datetime(2026, 11, 30, 0, 0)).issuer == "不明"


def test_employment_periods_snapshot_argument_wins_over_file(tmp_path, monkeypatch):
    """バッチ開始時に読んだ在籍期間表のスナップショットを渡せること
    （処理中に設定ファイルが書き換わってもバッチ内で判定が揺れない）。"""
    root = tmp_path / "書類整理ルート"
    init_folders(root)
    _employment_file(root, '[{"employer": "株式会社ファイル側", "start": "202410"}]')
    snapshot = config.parse_employment_periods(
        [{"employer": "株式会社スナップショット側", "start": "202410", "verified_through": "202609"}]
    )
    src = _payroll_file(tmp_path, "202601月給与.pdf", monkeypatch)

    result = classify_file(src, root=root, employment_periods=snapshot)

    assert result.issuer == "株式会社スナップショット側"
