"""在籍期間表（給与明細の発行元フォールバックの土台・T-1230）の検証。

この表は「書類から読み取れなかった発行元を推定で埋める」唯一の根拠なので、
壊れた設定を推測で補わないこと（＝捏造しないこと）を単体で固定しておく。
"""

from pathlib import Path

from home_doc_organizer import config


def test_default_periods_cover_current_employment():
    periods = config.load_employment_periods()
    assert len(periods) == 1
    period = periods[0]
    assert period.employer == "株式会社エフティグループ"
    assert period.covers(2024, 10) is True  # 在籍開始月
    assert period.covers(2024, 9) is False  # 開始前
    assert period.covers(2030, 1) is True  # end=None（現在も在籍）＝上限なし
    assert period.label() == "2024-10〜現在"


def test_closed_period_label_and_bounds():
    (period,) = config.parse_employment_periods(
        [{"employer": "株式会社前職", "start": "202410", "end": "202603"}]
    )
    assert period.covers(2026, 3) is True
    assert period.covers(2026, 4) is False
    assert period.label() == "2024-10〜2026-03"


def test_any_invalid_entry_disables_the_whole_table():
    """不正な要素が1件でもあれば表全体を無効化する（推測で補わない）。

    部分的に生き残った表で推定を続けると、書き間違えた行の月を「在籍期間外」と誤って扱い、
    別の勤務先名を当ててしまう。無効化＝推定が働かず `不明` のまま残る、が安全側。
    """
    assert config.parse_employment_periods("まるごと文字列") == ()
    assert config.parse_employment_periods([{"start": "202410"}]) == ()  # employer欠落
    assert config.parse_employment_periods([{"employer": " ", "start": "202410"}]) == ()
    assert config.parse_employment_periods([{"employer": "A"}]) == ()  # start欠落
    assert config.parse_employment_periods([{"employer": "A", "start": "2024-10"}]) == ()
    assert config.parse_employment_periods([{"employer": "A", "start": "202413"}]) == ()  # 13月
    # end が start より前＝矛盾した設定は採用しない
    assert (
        config.parse_employment_periods(
            [{"employer": "A", "start": "202410", "end": "202409"}]
        )
        == ()
    )
    # 1件でも不正なら、同じ配列の妥当な要素も採用しない
    assert (
        config.parse_employment_periods(
            [{"employer": "A", "start": "20241"}, {"employer": "B", "start": "202410"}]
        )
        == ()
    )
    # キーのタイプミス（"ends"）も設定ミスとして全体を無効化する
    assert (
        config.parse_employment_periods([{"employer": "A", "start": "202410", "ends": "202501"}])
        == ()
    )


def test_employer_name_validation_blocks_dangerous_values():
    """勤務先名はCSV・ログ・ファイル名に出るため、危険な値を設定段で弾く。"""
    # 表計算ソフトで数式として解釈される先頭文字（CSVインジェクション）
    assert config.parse_employment_periods([{"employer": "=HYPERLINK(1)", "start": "202410"}]) == ()
    # 制御文字
    assert config.parse_employment_periods([{"employer": "A\nB", "start": "202410"}]) == ()
    # 長すぎる値
    assert config.parse_employment_periods([{"employer": "あ" * 101, "start": "202410"}]) == ()
    # issuer_source や「不明」と混同させる予約値
    assert config.parse_employment_periods([{"employer": "不明", "start": "202410"}]) == ()
    assert config.parse_employment_periods([{"employer": "fallback", "start": "202410"}]) == ()


def test_year_out_of_range_is_rejected():
    assert config.parse_employment_periods([{"employer": "A", "start": "000001"}]) == ()
    assert config.parse_employment_periods([{"employer": "A", "start": "999912"}]) == ()


def test_open_period_has_inference_limit_from_verified_through():
    """在籍中（end なし）の期間にも推定の上限がある＝開放区間を推定に使わない。"""
    (period,) = config.parse_employment_periods(
        [{"employer": "A", "start": "202410", "verified_through": "202609"}]
    )
    assert period.end_ym is None  # 在籍期間としては終わりが無い
    assert period.covers(2030, 1) is True
    # 推定に使えるのは確認済み月＋猶予（既定12か月）まで
    assert period.inference_limit_ym(12) == 202709
    # verified_through を省略した場合は start からの猶予になる（無制限にはならない）
    (bare,) = config.parse_employment_periods([{"employer": "A", "start": "202604"}])
    assert bare.inference_limit_ym(12) == 202704


def test_empty_end_string_means_still_employed():
    (period,) = config.parse_employment_periods(
        [{"employer": "A", "start": "202410", "end": ""}]
    )
    assert period.end_ym is None


def test_broken_file_disables_fallback_instead_of_using_default(tmp_path: Path):
    """ファイルが置かれているのに読めない場合は既定値へ戻さず空を返す
    （戻すと転職後に古い勤務先名を書いてしまうため、安全側＝推定しないに倒す）。"""
    root = tmp_path / "書類整理ルート"
    path = config.folder_path(root, config.LEARNING) / config.EMPLOYMENT_PERIODS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れたJSON", encoding="utf-8")

    assert config.load_employment_periods(root) == ()


def test_file_replaces_default_without_merging(tmp_path: Path):
    root = tmp_path / "書類整理ルート"
    path = config.folder_path(root, config.LEARNING) / config.EMPLOYMENT_PERIODS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[{"employer": "株式会社転職先", "start": "202604"}]', encoding="utf-8")

    periods = config.load_employment_periods(root)
    assert [p.employer for p in periods] == ["株式会社転職先"]


def test_verified_through_after_end_is_rejected():
    """終了済みの期間で確認済み月が終了月より後＝矛盾した設定は受理しない。"""
    assert (
        config.parse_employment_periods(
            [
                {
                    "employer": "株式会社A",
                    "start": "202401",
                    "end": "202412",
                    "verified_through": "202601",
                }
            ]
        )
        == ()
    )


def test_parse_issuer_source_normalizes_csv_cell():
    """CSVの `発行元の根拠` セルは素の値・ラベル付き値の両方を受け、壊れた値は unknown。"""
    assert config.parse_issuer_source("fallback") == config.ISSUER_SOURCE_FALLBACK
    assert (
        config.parse_issuer_source(config.ISSUER_SOURCE_LABELS[config.ISSUER_SOURCE_FALLBACK])
        == config.ISSUER_SOURCE_FALLBACK
    )
    assert config.parse_issuer_source(" text ") == config.ISSUER_SOURCE_TEXT
    # 前方一致では通さない（監査データの破損を黙って推定扱いにしない）
    assert config.parse_issuer_source("fallback-typo") == config.ISSUER_SOURCE_UNKNOWN
    assert config.parse_issuer_source(None) == config.ISSUER_SOURCE_UNKNOWN
    assert config.parse_issuer_source("") == config.ISSUER_SOURCE_UNKNOWN
