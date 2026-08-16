from pathlib import Path

import pytest

from home_doc_organizer.naming import (
    build_filename,
    safe_copy,
    sanitize_component,
    unique_destination,
)


def test_sanitize_component_replaces_illegal_chars():
    assert sanitize_component('A/B:C*D?E"F<G>H|I') == "A／B：C＊D？E”F＜G＞H｜I"


def test_sanitize_component_empty_falls_back():
    assert sanitize_component("   ") == "不明"


def test_build_filename_normal():
    name = build_filename("20260816", "納税証明書", "麹町税務署", ".pdf")
    assert name == "20260816_納税証明書_麹町税務署.pdf"


def test_build_filename_estimated_inserts_marker_after_date():
    name = build_filename("20260816", "納税証明書", "税務署", "pdf", estimated=True)
    assert name == "20260816_推定_納税証明書_税務署.pdf"


def test_unique_destination_no_collision(tmp_path: Path):
    dest = unique_destination(tmp_path, "file.pdf")
    assert dest == tmp_path / "file.pdf"


def test_unique_destination_increments_on_collision(tmp_path: Path):
    (tmp_path / "file.pdf").write_text("existing")
    dest = unique_destination(tmp_path, "file.pdf")
    assert dest == tmp_path / "file_2.pdf"

    (tmp_path / "file_2.pdf").write_text("existing2")
    dest2 = unique_destination(tmp_path, "file.pdf")
    assert dest2 == tmp_path / "file_3.pdf"


def test_safe_copy_never_overwrites_existing_file(tmp_path: Path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    src = src_dir / "a.pdf"
    src.write_bytes(b"new-content")

    existing = dest_dir / "target.pdf"
    existing.write_bytes(b"do-not-touch")

    result = safe_copy(src, dest_dir, "target.pdf")

    assert result == dest_dir / "target_2.pdf"
    assert existing.read_bytes() == b"do-not-touch"  # 既存ファイルは無傷
    assert result.read_bytes() == b"new-content"
    assert src.read_bytes() == b"new-content"  # コピー元も無傷（移動していない）


def test_safe_copy_raises_for_missing_source(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        safe_copy(tmp_path / "missing.pdf", tmp_path / "dest", "x.pdf")


@pytest.mark.parametrize(
    "malicious_filename",
    [
        "../../../../tmp/evil.pdf",
        "../evil.pdf",
        "sub/evil.pdf",
        "/etc/evil.pdf",
    ],
)
def test_safe_copy_rejects_path_traversal_filenames(tmp_path: Path, malicious_filename: str):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = src_dir / "a.pdf"
    src.write_bytes(b"data")

    with pytest.raises(ValueError):
        safe_copy(src, dest_dir, malicious_filename)

    # dest_dir の外に何も書かれていないこと
    assert not (tmp_path / "tmp" / "evil.pdf").exists()
    assert not (tmp_path / "evil.pdf").exists()


def test_unique_destination_rejects_path_traversal_filename(tmp_path: Path):
    with pytest.raises(ValueError):
        unique_destination(tmp_path, "../escape.pdf")


def test_safe_copy_concurrent_calls_never_overwrite(tmp_path: Path):
    """TOCTOU耐性: unique_destinationが決めた宛先を、書き込み直前に他プロセスが
    先に埋めていても（os.O_EXCLが競合を検知して）上書きしない。"""
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    src = src_dir / "a.pdf"
    src.write_bytes(b"first")

    # unique_destination が "target.pdf" を返すと想定した直後に、
    # 別プロセスが同じ名前を先に作ってしまった状況を再現する。
    import home_doc_organizer.naming as naming_mod

    real_unique_destination = naming_mod.unique_destination
    calls = {"n": 0}

    def flaky_unique_destination(dest_dir_arg, filename_arg):
        calls["n"] += 1
        result = real_unique_destination(dest_dir_arg, filename_arg)
        if calls["n"] == 1:
            # レース: 呼び出し直後に他プロセスが同名ファイルを先に作成
            result.write_bytes(b"raced-in-by-another-process")
        return result

    naming_mod.unique_destination = flaky_unique_destination
    try:
        dest = naming_mod.safe_copy(src, dest_dir, "target.pdf")
    finally:
        naming_mod.unique_destination = real_unique_destination

    # 競合で埋まっていた target.pdf は無傷、自分のデータは _2 に書かれる
    assert (dest_dir / "target.pdf").read_bytes() == b"raced-in-by-another-process"
    assert dest == dest_dir / "target_2.pdf"
    assert dest.read_bytes() == b"first"
