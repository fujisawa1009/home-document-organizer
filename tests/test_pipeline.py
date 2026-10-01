"""第1〜4段階を通した統合テスト（tmp_pathを書類整理ルートとして使用）。

安全原則（元ファイル不変・上書き禁止・全操作ログ化・承認後のみ実行）を
実際のファイルシステム操作で検証する。
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pytest

from home_doc_organizer import classify, config
from home_doc_organizer.apply_changes import apply_approved_changes
from home_doc_organizer.cleanup_inbox import cleanup_inbox
from home_doc_organizer.inbox_scan import archive_inbox, list_inbox_files
from home_doc_organizer.init_folders import init_folders
from home_doc_organizer.proposal import CSV_COLUMNS, generate_proposal_csv

FIXED_WHEN = datetime(2026, 8, 16, 12, 0, 0)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "書類整理ルート"
    init_folders(r)
    return r


def _put_inbox_file(root: Path, name: str, content: bytes = b"dummy-pdf-bytes") -> Path:
    p = config.folder_path(root, config.INBOX) / name
    p.write_bytes(content)
    return p


def _put_inbox_subfolder_file(
    root: Path, subfolder: str, name: str, content: bytes = b"dummy-pdf-bytes"
) -> Path:
    d = config.folder_path(root, config.INBOX) / subfolder
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(content)
    return p


def _fake_classification(category_key: str, folder: str, doc_type: str, issuer: str):
    return classify.ClassificationResult(
        category_key=category_key,
        suggested_folder=folder,
        doc_type=doc_type,
        issuer=issuer,
        date_str="20260810",
        estimated_date=False,
        confidence="高",
        reason="テスト用固定分類",
    )


def test_init_folders_creates_all_and_is_idempotent(tmp_path: Path):
    r = tmp_path / "書類整理ルート"
    created = init_folders(r)
    assert len(created) == len(config.ALL_FOLDERS)
    for name in config.ALL_FOLDERS:
        assert (r / name).is_dir()

    # 2回目は何も作らない（既存フォルダに無干渉）
    (r / config.INBOX / "keep.txt").write_text("keep me")
    created2 = init_folders(r)
    assert created2 == []
    assert (r / config.INBOX / "keep.txt").read_text() == "keep me"


def test_archive_inbox_copies_without_touching_original(root: Path):
    src = _put_inbox_file(root, "sample.pdf")
    original_bytes = src.read_bytes()

    results = archive_inbox(root, when=FIXED_WHEN)

    assert len(results) == 1
    assert results[0].ok
    archived = results[0].archived_to
    assert archived is not None
    assert archived.exists()
    assert archived.read_bytes() == original_bytes
    # 元ファイルは受信箱にそのまま残る（削除・移動していない）
    assert src.exists()
    assert src.read_bytes() == original_bytes

    log_path = config.folder_path(root, config.LOGS) / "操作ログ_20260816.csv"
    assert log_path.exists()
    with log_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "実行日時",
        "操作種別",
        "元ファイルパス",
        "新ファイルパス",
        "結果",
        "詳細・エラー内容",
    ]
    assert rows[1][1] == "元ファイル保管コピー"
    assert rows[1][4] == "成功"


def test_list_inbox_files_includes_one_level_subfolder(root: Path):
    """CEO指示2026-08-16: フォルダ名で書類種別を示せるよう、1階層のサブフォルダの
    中身も受信箱スキャン対象にする（例: 00_受信箱/01_証明写真/写真.jpg）。"""
    top = _put_inbox_file(root, "top.pdf")
    nested = _put_inbox_subfolder_file(root, "01_証明写真", "photo.jpg")
    # 隠しファイル・隠しフォルダ内は対象外のまま
    _put_inbox_file(root, ".DS_Store")
    (config.folder_path(root, config.INBOX) / "01_証明写真" / ".DS_Store").write_bytes(b"x")
    # 2階層目は対象外（1階層までのスコープ限定）
    deep_dir = config.folder_path(root, config.INBOX) / "01_証明写真" / "さらに下"
    deep_dir.mkdir(parents=True)
    (deep_dir / "deep.jpg").write_bytes(b"x")

    found = list_inbox_files(root)

    assert set(found) == {top, nested}


def test_archive_inbox_handles_subfolder_file(root: Path):
    nested = _put_inbox_subfolder_file(root, "01_証明写真", "photo.jpg")
    original_bytes = nested.read_bytes()

    results = archive_inbox(root, when=FIXED_WHEN)

    assert len(results) == 1
    assert results[0].ok
    assert results[0].archived_to.read_bytes() == original_bytes
    assert nested.exists()  # 元ファイルは無傷


def test_full_flow_propose_approve_apply(root: Path, monkeypatch: pytest.MonkeyPatch):
    src = _put_inbox_file(root, "tax.pdf")
    original_bytes = src.read_bytes()

    monkeypatch.setattr(
        classify,
        "classify_file",
        lambda path, root=None, **kwargs: _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "麹町税務署"),
    )

    archive_inbox(root, when=FIXED_WHEN)
    csv_path, rows = generate_proposal_csv(root, when=FIXED_WHEN)

    assert len(rows) == 1
    row = rows[0]
    assert row.source_path == str(src)
    assert row.suggested_category == config.CATEGORY_TAX
    assert row.suggested_filename == "20260810_納税証明書_麹町税務署.pdf"

    # 承認列は空欄で出力される（自動承認しない）
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == CSV_COLUMNS
        first = next(reader)
        assert first["承認"] == ""

    # まだ未承認 → apply しても何も実行されない
    results_before = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert results_before == []
    assert not (root / config.CATEGORY_TAX / "20260810_納税証明書_麹町税務署.pdf").exists()
    assert src.exists()  # 未承認の間は元ファイルにも一切触れない

    # Telegram/LINE等で承認が返ってきた想定＝CSVの承認列にOKを書き込む
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows_data = list(reader)
    rows_data[0]["承認"] = "OK"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_data)

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert len(results) == 1
    assert results[0].ok
    dest = Path(results[0].dest_path)
    assert dest == root / config.CATEGORY_TAX / "20260810_納税証明書_麹町税務署.pdf"
    assert dest.read_bytes() == original_bytes

    # CEO指示2026-08-16: コピー成功後は受信箱の元ファイルを自動削除する
    # （`_元ファイル保管`に複製済みであることを確認したうえで削除）
    assert results[0].deleted_source is True
    assert not src.exists()
    archived = list((root / config.ORIGINAL_ARCHIVE).glob("*_tax.pdf"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == original_bytes  # バックアップは無傷

    # 元ファイルが既に無い状態で同じ承認済みCSVを再適用しても、クラッシュせず
    # 「見つからない」失敗として記録される（再コピーはできない＝当然の帰結）
    results2 = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert len(results2) == 1
    assert results2[0].ok is False
    assert "見つかりません" in results2[0].error
    assert dest.exists()  # 1回目の結果は影響を受けない


def test_apply_does_not_delete_source_when_not_archived(root: Path, monkeypatch: pytest.MonkeyPatch):
    """apply前にscan-inboxを飛ばしていた（＝_元ファイル保管に複製が無い）場合は、
    コピーは実行しても元ファイルの自動削除はスキップする（安全側フォールバック）。"""
    src = _put_inbox_file(root, "tax.pdf")
    # 意図的に archive_inbox を呼ばない＝_元ファイル保管に複製が存在しない状態を作る

    monkeypatch.setattr(
        classify,
        "classify_file",
        lambda path, root=None, **kwargs: _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "麹町税務署"),
    )
    csv_path, _ = generate_proposal_csv(root, when=FIXED_WHEN)
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows_data = list(reader)
    rows_data[0]["承認"] = "OK"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_data)

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert results[0].ok
    assert results[0].deleted_source is False
    assert src.exists()  # 複製未確認のため削除されない


def test_apply_rejects_unknown_target_folder(root: Path):
    src = _put_inbox_file(root, "evil.pdf")
    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_test.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "元ファイル名": src.name,
                "元ファイルパス": str(src),
                "提案カテゴリ": "../../../../tmp",
                "提案新ファイル名": "pwned.pdf",
                "書類種別(判定)": "x",
                "発行元(判定)": "x",
                "書類日付(判定)": "2026-01-01",
                "確信度": "高",
                "承認": "OK",
                "備考": "",
            }
        )

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert len(results) == 1
    assert results[0].ok is False
    assert "未知の提案カテゴリ" in results[0].error


def test_cleanup_inbox_only_deletes_after_archive_and_confirmation(root: Path):
    a = _put_inbox_file(root, "a.pdf")
    b = _put_inbox_file(root, "b.pdf")

    # a のみ事前に保管済みにしておく
    archive_inbox(root, when=FIXED_WHEN)

    confirmations = []

    def confirm(path: Path) -> bool:
        confirmations.append(path.name)
        return path.name == "a.pdf"

    results = cleanup_inbox(root, confirm=confirm, when=FIXED_WHEN)

    by_name = {r.path.name: r for r in results}
    assert by_name["a.pdf"].deleted is True
    assert not a.exists()
    assert by_name["b.pdf"].deleted is False
    assert b.exists()
    # confirm は両方archived済みのファイルに対してのみ呼ばれる（今回は両方archive済み）
    assert set(confirmations) == {"a.pdf", "b.pdf"}


def test_cleanup_inbox_never_deletes_unarchived_file_even_if_confirmed(root: Path):
    only = _put_inbox_file(root, "not_archived.pdf")

    results = cleanup_inbox(root, confirm=lambda p: True, when=FIXED_WHEN)

    assert len(results) == 1
    assert results[0].archived is False
    assert results[0].deleted is False
    assert only.exists()


def test_cleanup_inbox_does_not_false_positive_on_substring_suffix_match(root: Path):
    """archived 'monthly_report.pdf' must not make unarchived 'report.pdf' look archived
    just because its archived filename happens to end with '_report.pdf'."""
    archived_only = _put_inbox_file(root, "monthly_report.pdf")
    not_archived = _put_inbox_file(root, "report.pdf")

    archive_inbox(root, when=FIXED_WHEN)  # archives both files that currently exist
    # simulate: only monthly_report.pdf actually got archived (e.g. report.pdf added later)
    for p in config.folder_path(root, config.ORIGINAL_ARCHIVE).iterdir():
        if p.name.endswith("_report.pdf") and "monthly" not in p.name:
            p.unlink()  # remove report.pdf's own archive copy, keep monthly_report.pdf's

    results = cleanup_inbox(root, confirm=lambda p: True, when=FIXED_WHEN)
    by_name = {r.path.name: r for r in results}

    assert by_name["monthly_report.pdf"].archived is True
    assert by_name["report.pdf"].archived is False  # must NOT be a false positive
    assert not_archived.exists()  # never deleted
    assert archived_only.name == "monthly_report.pdf"


def test_apply_logs_skip_for_unapproved_rows(root: Path, monkeypatch: pytest.MonkeyPatch):
    _put_inbox_file(root, "unapproved.pdf")
    monkeypatch.setattr(
        classify,
        "classify_file",
        lambda path, root=None, **kwargs: _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "税務署"),
    )
    archive_inbox(root, when=FIXED_WHEN)
    csv_path, _ = generate_proposal_csv(root, when=FIXED_WHEN)  # 承認列は空欄のまま

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert results == []

    log_path = config.folder_path(root, config.LOGS) / "操作ログ_20260816.csv"
    with log_path.open(encoding="utf-8-sig") as f:
        log_rows = list(csv.reader(f))
    assert any(r[1] == "スキップ" for r in log_rows[1:])


def test_apply_rejects_source_path_outside_inbox(root: Path, tmp_path: Path):
    outside_file = tmp_path / "secret.pdf"
    outside_file.write_bytes(b"top-secret")

    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_test.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "元ファイル名": outside_file.name,
                "元ファイルパス": str(outside_file),
                "提案カテゴリ": config.CATEGORY_TAX,
                "提案新ファイル名": "stolen.pdf",
                "書類種別(判定)": "x",
                "発行元(判定)": "x",
                "書類日付(判定)": "2026-01-01",
                "確信度": "高",
                "承認": "OK",
                "備考": "",
            }
        )

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert len(results) == 1
    assert results[0].ok is False
    assert "受信箱の外" in results[0].error
    assert not (root / config.CATEGORY_TAX / "stolen.pdf").exists()


def test_learning_loop_end_to_end(root: Path, monkeypatch: pytest.MonkeyPatch):
    """CEOが一度だけ手直しして承認すると、同じ発行元の次のファイルは
    キーワード辞書の更新なしで自動的に高確信度分類されるようになること。"""
    # 既存キーワード辞書に一切ヒットしない文言にする（学習前は必ず低確信度になることの
    # 前提を守るため。「ご利用明細」等は銀行の取引明細書キーワードと衝突するので避ける）。
    ocr_text = "d POINT CARD dカード会員 YUTA FUJISAWA 1234 5678 9012 3456"
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: ocr_text)

    # 1回目: 受信箱にdカードを入れて propose すると、キーワード辞書に無いので要確認
    _put_inbox_file(root, "card1.pdf")
    archive_inbox(root, when=FIXED_WHEN)
    csv_path, rows = generate_proposal_csv(root, when=FIXED_WHEN)
    assert rows[0].confidence == "低"
    assert rows[0].suggested_category == config.NEEDS_REVIEW

    # CEOがCSVを訂正してから承認（発行元(判定)はOCR本文に実在する文字列にする）
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows_data = list(reader)
    rows_data[0]["提案カテゴリ"] = config.CATEGORY_BANK
    rows_data[0]["書類種別(判定)"] = "クレジットカード"
    rows_data[0]["発行元(判定)"] = "dカード"
    rows_data[0]["提案新ファイル名"] = "20260810_クレジットカード_dカード.pdf"
    rows_data[0]["承認"] = "OK"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_data)

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert results[0].ok
    assert (root / config.CATEGORY_BANK / "20260810_クレジットカード_dカード.pdf").exists()

    # 学習ルールが1件保存されていること
    from home_doc_organizer import learning

    saved = learning.load_rules(root)
    assert len(saved) == 1
    assert saved[0]["issuer"] == "dカード"

    # 2回目: 別のdカード書類を入れると、今度は自動で高確信度・銀行フォルダ提案になる
    _put_inbox_file(root, "card2.pdf")
    archive_inbox(root, when=FIXED_WHEN)
    _, rows2 = generate_proposal_csv(root, when=FIXED_WHEN)
    card2_row = next(r for r in rows2 if r.source_name == "card2.pdf")
    assert card2_row.confidence == "高"
    assert card2_row.suggested_category == config.CATEGORY_BANK
    assert card2_row.doc_type == "クレジットカード"
    assert "学習済みルール" in card2_row.reason


def test_propose_survives_one_file_raising_during_classification(root: Path, monkeypatch: pytest.MonkeyPatch):
    _put_inbox_file(root, "broken.pdf")
    _put_inbox_file(root, "ok.pdf")

    def flaky_classify(path: Path, root=None):
        if path.name == "broken.pdf":
            raise RuntimeError("破損PDFの読み取りに失敗")
        return _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "税務署")

    monkeypatch.setattr(classify, "classify_file", flaky_classify)

    csv_path, rows = generate_proposal_csv(root, when=FIXED_WHEN)
    assert len(rows) == 2  # 1件失敗しても全体は止まらない

    broken_row = next(r for r in rows if r.source_name == "broken.pdf")
    assert broken_row.suggested_category == config.NEEDS_REVIEW
    assert broken_row.confidence == "低"

    log_path = config.folder_path(root, config.LOGS) / "操作ログ_20260816.csv"
    with log_path.open(encoding="utf-8-sig") as f:
        log_rows = list(csv.reader(f))
    assert any(r[1] == "分類エラー" for r in log_rows[1:])


def test_generate_proposal_csv_auto_approve_writes_ok_for_every_row(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    _put_inbox_file(root, "high.pdf")
    _put_inbox_file(root, "unknown.pdf")

    def fake_classify(path: Path, root=None, **kwargs):
        if path.name == "high.pdf":
            return _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "税務署")
        return classify.ClassificationResult(
            category_key="",
            suggested_folder=config.NEEDS_REVIEW,
            doc_type="不明",
            issuer="不明",
            date_str="20260101",
            estimated_date=True,
            confidence="低",
            reason="キーワード一致なし",
        )

    monkeypatch.setattr(classify, "classify_file", fake_classify)

    csv_path, rows = generate_proposal_csv(root, when=FIXED_WHEN, auto_approve=True)
    assert len(rows) == 2
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        approvals = [r["承認"] for r in reader]
    assert approvals == ["OK", "OK"]  # 確信度に関わらず全行OK


def test_auto_run_flow_files_everything_including_low_confidence(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    """CEO指示2026-08-16: 外出先からの自動振り分け。確信度が低い書類も
    _要確認へ自動でコピーされ、受信箱の元ファイルは自動削除されること。"""
    _put_inbox_file(root, "tax.pdf")
    _put_inbox_file(root, "mystery.pdf")

    def fake_classify(path: Path, root=None, **kwargs):
        if path.name == "tax.pdf":
            return _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "税務署")
        return classify.ClassificationResult(
            category_key="",
            suggested_folder=config.NEEDS_REVIEW,
            doc_type="不明",
            issuer="不明",
            date_str="20260101",
            estimated_date=True,
            confidence="低",
            reason="キーワード一致なし",
        )

    monkeypatch.setattr(classify, "classify_file", fake_classify)

    archive_inbox(root, when=FIXED_WHEN)
    csv_path, _ = generate_proposal_csv(root, when=FIXED_WHEN, auto_approve=True)
    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)

    assert len(results) == 2
    assert all(r.ok for r in results)
    assert all(r.deleted_source for r in results)

    assert (root / config.CATEGORY_TAX / "20260810_納税証明書_税務署.pdf").exists()
    needs_review_files = list((root / config.NEEDS_REVIEW).iterdir())
    assert len(needs_review_files) == 1  # 低確信度でも_要確認へ自動でコピーされる

    # 受信箱は空になっている（自動削除・サブフォルダ含め対象外の隠しファイルのみ許容）
    remaining = [p for p in config.folder_path(root, config.INBOX).iterdir() if not p.name.startswith(".")]
    assert remaining == []


def test_auto_run_files_payslip_into_payroll_category(root: Path, monkeypatch: pytest.MonkeyPatch):
    """給与明細が `06_給与` へ実際にコピーされること（apply_changes の許可フォルダにも
    新カテゴリが含まれている＝カテゴリ追加が最後のコピー段まで通ること）。
    2026-09-29 に `01_税金/住民税決定通知書` と `_要確認` へ誤分類された件の再発防止。"""
    _put_inbox_file(root, "202601月給与.pdf")

    # OCRが1文字も返さないスキャンPDFを模す（実物10件のうち9件がこの状態）。
    # ファイル名ヒントだけで給与カテゴリへ入ることを、本物の分類器で確認する。
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    archive_inbox(root, when=FIXED_WHEN)
    csv_path, rows = generate_proposal_csv(root, when=FIXED_WHEN, auto_approve=True)
    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)

    assert rows[0].suggested_category == config.CATEGORY_PAYROLL
    # 日付欄は「支給年月のみ・日は01固定」と注記され、実在の1日付と誤読されないこと
    assert "支給年月のみ" in rows[0].doc_date
    assert len(results) == 1 and results[0].ok
    # 発行元は本文から読めないが、支給年月2026-01が在籍期間内なので勤務先を推定して埋める
    # （T-1230。推定であることは `発行元の根拠` 列・備考・操作ログで区別できる）。
    assert (
        root / config.CATEGORY_PAYROLL / "20260101_給与明細_株式会社エフティグループ.pdf"
    ).exists()
    assert list((root / config.NEEDS_REVIEW).iterdir()) == []
    assert list((root / config.CATEGORY_TAX).iterdir()) == []


def test_proposal_csv_marks_inferred_issuer_and_logs_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    """推定で埋めた発行元が、読み取り値と区別できる形でCSVと操作ログに残ること（T-1230）。

    発行元を推定で埋めると `_不明` は消えるが、そのままでは「読み取れた値」と見分けが
    つかない。CSVの `発行元の根拠` 列・備考の `issuer_source=fallback`・追記専用の操作ログ
    の3か所で区別できることを固定する。
    """
    _put_inbox_file(root, "202601月給与.pdf")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    archive_inbox(root, when=FIXED_WHEN)
    csv_path, rows = generate_proposal_csv(root, when=FIXED_WHEN, auto_approve=True)

    assert rows[0].issuer == "株式会社エフティグループ"
    with csv_path.open(encoding="utf-8-sig") as f:
        first = next(csv.DictReader(f))
    assert first["発行元(判定)"] == "株式会社エフティグループ"
    assert first["発行元の根拠"].startswith("fallback")
    assert "読み取り値ではない" in first["発行元の根拠"]
    assert "issuer_source=fallback" in first["備考"]

    log_text = (
        config.folder_path(root, config.LOGS) / f"操作ログ_{FIXED_WHEN:%Y%m%d}.csv"
    ).read_text(encoding="utf-8-sig")
    assert "発行元推定" in log_text
    assert "issuer_source=fallback" in log_text

    # 実行（コピー）ログ側にも根拠が残る＝どの実ファイルが推定値の名前で置かれたか追える
    apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    log_text = (
        config.folder_path(root, config.LOGS) / f"操作ログ_{FIXED_WHEN:%Y%m%d}.csv"
    ).read_text(encoding="utf-8-sig")
    assert "発行元の根拠=fallback" in log_text

    # 推定値は学習ルールに取り込まれない（次回「読み取れた値」に化けない）
    rules_path = config.folder_path(root, config.LEARNING) / "learned_rules.json"
    assert not rules_path.exists()


def test_proposal_csv_marks_issuer_read_from_text_as_fact(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    """本文から読めた発行元は `text`（読み取り値）として記録され、推定と混ざらないこと。"""
    _put_inbox_file(root, "payslip.pdf")
    monkeypatch.setattr(
        "home_doc_organizer.extract.extract_text",
        lambda path: "給与明細書 2026年6月給与 本人給 総支給額 差引支給額\n株式会社エフティグループ",
    )

    archive_inbox(root, when=FIXED_WHEN)
    csv_path, _ = generate_proposal_csv(root, when=FIXED_WHEN, auto_approve=True)

    with csv_path.open(encoding="utf-8-sig") as f:
        first = next(csv.DictReader(f))
    assert first["発行元の根拠"].startswith("text")
    assert "issuer_source=" not in first["備考"]
    log_text = (
        config.folder_path(root, config.LOGS) / f"操作ログ_{FIXED_WHEN:%Y%m%d}.csv"
    ).read_text(encoding="utf-8-sig")
    assert "発行元推定" not in log_text


def test_old_proposal_csv_without_new_column_is_still_applicable(root: Path):
    """`発行元の根拠` 列を持たない古い変更案CSVでも apply が通ること
    （列を後ろへ追記しただけなので、既に `_変更案` に残っているCSVを壊さない）。"""
    src = _put_inbox_file(root, "old.pdf")
    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_20260817_000000.csv"
    old_columns = [c for c in CSV_COLUMNS if c != "発行元の根拠"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=old_columns)
        writer.writeheader()
        writer.writerow(
            {
                "元ファイル名": src.name,
                "元ファイルパス": str(src),
                "提案カテゴリ": config.CATEGORY_TAX,
                "提案新ファイル名": "20260810_納税証明書_麹町税務署.pdf",
                "書類種別(判定)": "納税証明書",
                "発行元(判定)": "麹町税務署",
                "書類日付(判定)": "2026-08-10",
                "確信度": "高",
                "承認": "OK",
                "備考": "",
            }
        )

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)

    assert len(results) == 1 and results[0].ok
    assert (root / config.CATEGORY_TAX / "20260810_納税証明書_麹町税務署.pdf").exists()


def _write_rows_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _approved_row(src: Path, filename: str) -> dict[str, str]:
    return {
        "元ファイル名": src.name,
        "元ファイルパス": str(src),
        "提案カテゴリ": config.CATEGORY_TAX,
        "提案新ファイル名": filename,
        "書類種別(判定)": "納税証明書",
        "発行元(判定)": "麹町税務署",
        "書類日付(判定)": "2026-08-10",
        "確信度": "高",
        "承認": "OK",
        "備考": "",
    }


def test_apply_tolerates_rows_with_missing_trailing_cell(root: Path):
    """ヘッダーは新11列だがデータ行が10セルしかないCSV（Excel/Numbersや手修正で起きる）でも、
    コピー後に落ちずバッチの全行が処理されること。

    `csv.DictReader` は足りないセルを None で埋めるため、素の文字列操作をすると
    `safe_copy` 成功後・受信箱削除前に AttributeError で止まり、残り全行が未処理のまま
    次回のauto-runで再処理（連番コピー）される事故になる。
    """
    src1 = _put_inbox_file(root, "a.pdf")
    src2 = _put_inbox_file(root, "b.pdf")
    archive_inbox(root, when=FIXED_WHEN)  # 複製済みでないと安全弁で元ファイルを消さないため
    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_20260817_000000.csv"
    # ヘッダーは11列、各行は末尾セル（発行元の根拠）が欠けた10セル
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for src, name in ((src1, "20260810_納税証明書_麹町税務署.pdf"), (src2, "20260811_納税証明書_麹町税務署.pdf")):
            row = _approved_row(src, name)
            writer.writerow([row[c] for c in CSV_COLUMNS if c != "発行元の根拠"])

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)

    assert len(results) == 2
    assert all(r.ok for r in results)
    assert all(r.deleted_source for r in results)


def test_apply_rejects_duplicated_control_column(root: Path):
    """重複列を拒否する。`csv.DictReader` は同名列の**後ろ**の値を採るため、見た目の
    「承認」が空欄でも末尾に重複した「承認=OK」があれば実行されてしまう。"""
    src = _put_inbox_file(root, "a.pdf")
    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_20260817_000001.csv"
    row = _approved_row(src, "20260810_納税証明書_麹町税務署.pdf")
    row["承認"] = ""
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([*CSV_COLUMNS, "承認"])
        writer.writerow([*[row.get(c, "") for c in CSV_COLUMNS], "OK"])

    with pytest.raises(ValueError, match="重複"):
        apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert src.exists()  # 1行も実行されていない


def test_apply_rejects_column_inserted_before_required_columns(root: Path):
    """必須列の前・途中への列挿入や列順の入れ替えは拒否する（列位置で安全弁を張るため）。"""
    src = _put_inbox_file(root, "a.pdf")
    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_20260817_000002.csv"
    row = _approved_row(src, "20260810_納税証明書_麹町税務署.pdf")
    _write_rows_csv(csv_path, ["メモ", *CSV_COLUMNS], [{"メモ": "x", **row}])

    with pytest.raises(ValueError, match="列構成が不正"):
        apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)


def test_apply_allows_extra_trailing_column(root: Path):
    """末尾に見知らぬ列が増えるのは許す（表計算ソフトが空列を足すことがある）。"""
    src = _put_inbox_file(root, "a.pdf")
    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_20260817_000003.csv"
    row = _approved_row(src, "20260810_納税証明書_麹町税務署.pdf")
    _write_rows_csv(csv_path, [*CSV_COLUMNS, "メモ"], [{**row, "メモ": "手書き"}])

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert len(results) == 1 and results[0].ok


def test_fallback_log_failure_does_not_stop_proposal(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    """推定の監査ログ書き込みが失敗しても、変更案の生成自体は止めないこと
    （給与の推定行だけが新しい停止経路になり、同じバッチの他カテゴリまで巻き込むのを防ぐ）。"""
    _put_inbox_file(root, "202601月給与.pdf")
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    from home_doc_organizer import logger as logger_mod

    real_log = logger_mod.log_operation

    def flaky(root_, operation, *args, **kwargs):
        if operation == logger_mod.OP_ISSUER_FALLBACK:
            raise OSError("disk full")
        return real_log(root_, operation, *args, **kwargs)

    monkeypatch.setattr("home_doc_organizer.proposal.logger.log_operation", flaky)

    csv_path, rows = generate_proposal_csv(root, when=FIXED_WHEN, auto_approve=True)

    assert len(rows) == 1
    assert rows[0].issuer == "株式会社エフティグループ"
    assert csv_path.exists()


def test_classification_result_positional_and_replace_compat():
    """`issuer_source` を末尾に足しただけなので、位置引数での生成も `replace` も壊れないこと。"""
    from dataclasses import replace

    result = classify.ClassificationResult(
        "税金",
        config.CATEGORY_TAX,
        "納税証明書",
        "麹町税務署",
        "20260810",
        False,
        "高",
        "テスト",
    )
    assert result.issuer_source == classify.ISSUER_SOURCE_UNKNOWN  # 根拠は推測で埋めない

    fallback = replace(result, issuer_source=classify.ISSUER_SOURCE_FALLBACK)
    assert replace(fallback, doc_type="別種別").issuer_source == classify.ISSUER_SOURCE_FALLBACK


def test_apply_continues_when_success_log_write_fails(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    """コピー成功後のログ書き込みが失敗しても、受信箱削除と残りの行の処理を続けること。

    ここで例外が漏れると「コピー済みなのに元ファイルが残り、残りの行は未処理」となり、
    次回のauto-runが同じファイルを再処理して連番コピー（_2）を作る事故になる。
    """
    src1 = _put_inbox_file(root, "a.pdf")
    src2 = _put_inbox_file(root, "b.pdf")
    archive_inbox(root, when=FIXED_WHEN)
    csv_path = config.folder_path(root, config.PROPOSALS) / "変更案_20260817_000004.csv"
    _write_rows_csv(
        csv_path,
        CSV_COLUMNS,
        [
            _approved_row(src1, "20260810_納税証明書_麹町税務署.pdf"),
            _approved_row(src2, "20260811_納税証明書_麹町税務署.pdf"),
        ],
    )

    from home_doc_organizer import logger as logger_mod

    real_log = logger_mod.log_operation

    def flaky(root_, operation, *args, **kwargs):
        if operation == logger_mod.OP_RENAME_EXEC:
            raise OSError("disk full")
        return real_log(root_, operation, *args, **kwargs)

    monkeypatch.setattr("home_doc_organizer.apply_changes.logger.log_operation", flaky)

    results = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)

    assert len(results) == 2
    assert all(r.ok and r.deleted_source for r in results)
    assert not src1.exists() and not src2.exists()


def test_employment_table_is_read_once_per_batch(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    """在籍期間表はバッチ開始時に1回だけ読む（処理中に設定が書き換わっても
    同じバッチの前半と後半で発行元の判定が変わらない・iCloudのreadを繰り返さない）。"""
    for name in ("202601月給与.pdf", "202602月給与.pdf", "202603月給与.pdf"):
        _put_inbox_file(root, name)
    monkeypatch.setattr("home_doc_organizer.extract.extract_text", lambda path: "")

    calls: list[object] = []
    real = config.load_employment_periods

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr("home_doc_organizer.proposal.config.load_employment_periods", counting)
    monkeypatch.setattr("home_doc_organizer.classify.config.load_employment_periods", counting)

    _, rows = generate_proposal_csv(root, when=FIXED_WHEN, auto_approve=True)

    assert len(rows) == 3
    assert all(r.issuer == "株式会社エフティグループ" for r in rows)
    assert len(calls) == 1
