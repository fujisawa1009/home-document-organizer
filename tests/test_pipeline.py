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
from home_doc_organizer.inbox_scan import archive_inbox
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


def test_full_flow_propose_approve_apply(root: Path, monkeypatch: pytest.MonkeyPatch):
    src = _put_inbox_file(root, "tax.pdf")

    monkeypatch.setattr(
        classify,
        "classify_file",
        lambda path, root=None: _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "麹町税務署"),
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
    assert dest.read_bytes() == src.read_bytes()

    # 元ファイル・受信箱は無傷（コピーのみ）
    assert src.exists()

    # 同じ承認済みCSVをもう一度適用しても上書きされず連番になる
    results2 = apply_approved_changes(root, csv_path=csv_path, when=FIXED_WHEN)
    assert results2[0].ok
    dest2 = Path(results2[0].dest_path)
    assert dest2 == root / config.CATEGORY_TAX / "20260810_納税証明書_麹町税務署_2.pdf"
    assert dest.exists()  # 1回目の結果も消えていない


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
        lambda path, root=None: _fake_classification("税金", config.CATEGORY_TAX, "納税証明書", "税務署"),
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
