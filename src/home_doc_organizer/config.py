"""フォルダ構成・パス設定。

実パスは環境変数 HOME_DOC_ROOT で上書きできる（デフォルトはCEO確認済みの実iCloudパス）。
テスト・デモ実行では必ず HOME_DOC_ROOT で一時ディレクトリに差し替えること
（本番の「書類整理ルート」を誤って書き換えないため）。
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "書類整理ルート"
)

INBOX = "00_受信箱"
CATEGORY_TAX = "01_税金"
CATEGORY_BANK = "02_銀行"
CATEGORY_INSURANCE = "03_保険"
CATEGORY_ID = "04_身分証"
CATEGORY_OTHER = "05_その他"
# 給与明細・賞与明細の専用カテゴリ。既存カテゴリはすべて「連番_名称」のトップレベル
# フォルダ1階層で、apply_changes はカテゴリフォルダ直下へコピーする設計（入れ子の
# サブフォルダはカテゴリモデルに存在しない）。そのため 05_その他 配下ではなく
# 同じ並びのトップレベルとして新設する。既存フォルダには触らない冪等な追加
# （init_folders が存在しないものだけ作る／safe_copy も書き込み時に mkdir する）。
CATEGORY_PAYROLL = "06_給与"
NEEDS_REVIEW = "_要確認"
ORIGINAL_ARCHIVE = "_元ファイル保管"
PROPOSALS = "_変更案"
LOGS = "_ログ"
LEARNING = "_学習データ"

CATEGORY_FOLDERS = (
    CATEGORY_TAX,
    CATEGORY_BANK,
    CATEGORY_INSURANCE,
    CATEGORY_ID,
    CATEGORY_OTHER,
    CATEGORY_PAYROLL,
)

ALL_FOLDERS = (
    INBOX,
    *CATEGORY_FOLDERS,
    NEEDS_REVIEW,
    ORIGINAL_ARCHIVE,
    PROPOSALS,
    LOGS,
    LEARNING,
)

# 分類キー（classify.py の判定結果）→ 実フォルダ名
CATEGORY_KEY_TO_FOLDER = {
    "税金": CATEGORY_TAX,
    "銀行": CATEGORY_BANK,
    "保険": CATEGORY_INSURANCE,
    "身分証": CATEGORY_ID,
    "その他": CATEGORY_OTHER,
    "給与": CATEGORY_PAYROLL,
}

# 逆引き（実フォルダ名 → 分類キー）。学習機能が「承認された提案カテゴリ」から
# category_key を復元するために使う。
FOLDER_TO_CATEGORY_KEY = {v: k for k, v in CATEGORY_KEY_TO_FOLDER.items()}

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic"}

# --- 発行元(判定)の根拠（T-1230） ----------------------------------------------------
#
# 「実際に読み取れた値」と「推定で埋めた値」を混ぜないための機械可読な印。
# 変更案CSVの `発行元の根拠` 列・備考（`issuer_source=...`）・操作ログに同じ値が出る。
# classify.py / proposal.py / learning.py の3つから参照するため、他モジュールへ依存しない
# この config に定義を置く（classify ⇄ learning の循環importを避ける）。
ISSUER_SOURCE_TEXT = "text"  # 本文・ファイル名から実際に読み取れた（FACT）
ISSUER_SOURCE_LEARNED = "learned"  # 過去に決定者が承認・訂正した学習ルール由来
ISSUER_SOURCE_FALLBACK = "fallback"  # 在籍期間との照合による推定（読み取り値ではない）
ISSUER_SOURCE_UNKNOWN = "unknown"  # 読み取れず・推定もできず（発行元は「不明」のまま）

ISSUER_SOURCE_LABELS = {
    ISSUER_SOURCE_TEXT: "text（本文からの読み取り値）",
    ISSUER_SOURCE_LEARNED: "learned（決定者が承認した学習ルール）",
    ISSUER_SOURCE_FALLBACK: "fallback（在籍期間照合による推定・読み取り値ではない）",
    ISSUER_SOURCE_UNKNOWN: "unknown（読み取れず）",
}

def parse_issuer_source(value: object) -> str:
    """変更案CSVの `発行元の根拠` セルを ISSUER_SOURCE_* のどれかに正規化する。

    CSVにはラベル付きの値（"fallback（在籍期間照合による推定・読み取り値ではない）"）を
    書いているため、素の値とラベルの両方を受ける。`None`（DictReader は行末のセルが
    欠けていると None を返す）・空欄・見慣れない値はすべて `unknown` に倒す。
    前方一致で判定すると "fallback-typo" のような壊れた値まで推定扱いになり、監査データの
    破損を黙って通してしまうため、完全一致だけを認める。
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return ISSUER_SOURCE_UNKNOWN
    for source, label in ISSUER_SOURCE_LABELS.items():
        if text in (source, label):
            return source
    return ISSUER_SOURCE_UNKNOWN


# --- 在籍期間（給与・賞与明細の発行元フォールバック用・T-1230） -----------------------
#
# 何のための設定か:
#   実物の給与明細はスキャンPDFで pypdf もOCRも1文字も返さないものがある（9/10件が該当・実測）。
#   本文が無いので発行元（勤務先）が読めず、ファイル名（`202601月給与.pdf`）にも社名が無い。
#   そこで「支給年月が在籍期間の中なら、その期間の勤務先を発行元として推定する」という
#   最後の手段を用意する。ファイル名の支給年月＋この在籍期間表だけが根拠であり、
#   **書類から読み取った値ではない**（classify.py 側で issuer_source="fallback" として区別する）。
#
# なぜ設定値として外に出すか:
#   勤務先は転職で変わる。社名をコードへ埋め込むと将来必ず腐り、「もう辞めた会社の名前を
#   今月の明細に書く」という捏造を生む。既定値はコードに持つが、
#   `_学習データ/employment_periods.json` を置けばそちらが**完全に優先**される
#   （既定値とのマージはしない＝転職後に古い社名が残らないようにするため）。
#
# ファイル形式（JSON配列）:
#   [{"employer": "株式会社◯◯", "start": "202410", "end": "202603"},
#    {"employer": "株式会社△△", "start": "202604", "verified_through": "202609"}]
#   - `start`/`end`/`verified_through` は YYYYMM。
#   - `end` を省略 / null にすると「現在も在籍」。
#   - `verified_through` は「在籍を一次資料で確認できている最終月」。在籍中（end なし）の
#     期間はそのままでは終わりが無く、転職後に設定を直し忘れると**新しい勤務先の明細へ
#     前職の社名を書いてしまう**。そこで推定に使える上限を
#     `verified_through`（省略時は `start`）＋ EMPLOYMENT_INFERENCE_GRACE_MONTHS までに
#     区切る（それ以降は推定せず `不明` に戻る＝設定の更新が必要だと分かる）。
#
# 既定値の出典: private/20260906_職務経歴書（株式会社エフティグループ 2024年10月〜現在在職）。
# `verified_through` は実物の給与明細で在籍を確認できている最終月（2026年9月分）。
DEFAULT_EMPLOYMENT_PERIODS: tuple[dict[str, str | None], ...] = (
    {
        "employer": "株式会社エフティグループ",
        "start": "202410",
        "end": None,
        "verified_through": "202609",
    },
)

EMPLOYMENT_PERIODS_FILE = "employment_periods.json"
# 在籍期間表の読み込み上限（これを超えるファイルは読まずに推定を無効化する）。
EMPLOYMENT_PERIODS_MAX_BYTES = 1_000_000

# 在籍中（end なし）の期間について、確認済み月から何か月先までを推定に使ってよいか。
# 在籍確認が取れていない先の月に勤務先名を書き続けないための上限。
EMPLOYMENT_INFERENCE_GRACE_MONTHS = 12

EMPLOYMENT_PERIOD_KEYS = frozenset({"employer", "start", "end", "verified_through"})
EMPLOYMENT_YEAR_MIN = 1990
EMPLOYER_NAME_MAX_CHARS = 100
# 発行元として使わせない値。CSV・ファイル名・issuer_source の語と混同させないため。
EMPLOYER_RESERVED_NAMES = frozenset({"不明", "text", "learned", "fallback", "unknown"})
# 表計算ソフトが数式として解釈する先頭文字（CSVインジェクション対策）。
EMPLOYER_FORBIDDEN_FIRST_CHARS = "=+-@\t\r"


@dataclass(frozen=True)
class EmploymentPeriod:
    """在籍期間1件。`*_ym` は YYYYMM を整数にしたもの（比較しやすいため）。"""

    employer: str
    start_ym: int
    end_ym: int | None  # None = 現在も在籍
    verified_through_ym: int | None = None  # 在籍を一次資料で確認できている最終月

    def covers(self, year: int, month: int) -> bool:
        """実際の在籍期間に含まれるか（期間そのものの意味。推定の可否とは別）。"""
        ym = year * 100 + month
        if ym < self.start_ym:
            return False
        return self.end_ym is None or ym <= self.end_ym

    def inference_limit_ym(self, grace_months: int) -> int:
        """発行元の推定に使ってよい上限のYYYYMM（在籍中の期間にも必ず上限を作る）。"""
        if self.end_ym is not None:
            return self.end_ym
        base = self.verified_through_ym or self.start_ym
        total = (base // 100) * 12 + (base % 100 - 1) + grace_months
        return (total // 12) * 100 + (total % 12 + 1)

    def label(self) -> str:
        """人が読む期間表記（ログ・CSVの備考に出す）。"""
        end = _format_ym(self.end_ym) if self.end_ym else "現在"
        return f"{_format_ym(self.start_ym)}〜{end}"


def _format_ym(ym: int) -> str:
    return f"{ym // 100}-{ym % 100:02d}"


class EmploymentPeriodConfigError(ValueError):
    """在籍期間表の設定が不正。発行元の推定に使う値なので、推測で補わず全体を無効化する。"""


def _parse_ym(value: object, field: str, now: datetime | None = None) -> int:
    """"202410" のような YYYYMM を整数にする。形式・範囲が不正なら例外。"""
    if not isinstance(value, str):
        raise EmploymentPeriodConfigError(f"{field} は YYYYMM の文字列で指定してください: {value!r}")
    value = value.strip()
    if len(value) != 6 or not value.isdigit():
        raise EmploymentPeriodConfigError(f"{field} の形式が YYYYMM ではありません: {value!r}")
    year, month = int(value[:4]), int(value[4:])
    if not 1 <= month <= 12:
        raise EmploymentPeriodConfigError(f"{field} の月が不正です: {value!r}")
    if not EMPLOYMENT_YEAR_MIN <= year <= (now or datetime.now()).year + 1:
        raise EmploymentPeriodConfigError(f"{field} の年が許容範囲外です: {value!r}")
    return year * 100 + month


def _parse_employer(value: object) -> str:
    """勤務先名を検証する。ここを通った文字列がCSV・ログ・ファイル名に出る。"""
    if not isinstance(value, str):
        raise EmploymentPeriodConfigError(f"employer は文字列で指定してください: {value!r}")
    employer = value.strip()
    if not employer:
        raise EmploymentPeriodConfigError("employer が空です")
    if len(employer) > EMPLOYER_NAME_MAX_CHARS:
        raise EmploymentPeriodConfigError(f"employer が長すぎます（{len(employer)}文字）")
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in employer):
        # Cc=制御文字／Cf=書式制御文字（ゼロ幅文字・双方向制御）。後者はCSV・ログ・ファイル名で
        # 見た目を偽装できるため、どちらも設定段で拒否する。
        raise EmploymentPeriodConfigError("employer に制御文字が含まれています")
    if employer[0] in EMPLOYER_FORBIDDEN_FIRST_CHARS:
        raise EmploymentPeriodConfigError(
            f"employer の先頭文字が表計算ソフトで数式として解釈されます: {employer!r}"
        )
    if employer in EMPLOYER_RESERVED_NAMES:
        raise EmploymentPeriodConfigError(f"employer に予約値は使えません: {employer!r}")
    return employer


def parse_employment_periods(
    raw: object, now: datetime | None = None
) -> tuple[EmploymentPeriod, ...]:
    """JSON由来の生データを検証して EmploymentPeriod の並びにする。

    **1件でも不正があれば全体を空にする**（部分的に生き残った表で推定を続けると、
    書き間違えた行の月を「在籍期間外」と誤って扱い、別の勤務先名を当ててしまう）。
    空＝推定が働かず発行元は `不明` のまま残る、が安全側の既定動作。
    """
    try:
        return _parse_employment_periods_strict(raw, now)
    except EmploymentPeriodConfigError:
        return ()


def _parse_employment_periods_strict(
    raw: object, now: datetime | None = None
) -> tuple[EmploymentPeriod, ...]:
    if not isinstance(raw, list):
        raise EmploymentPeriodConfigError("在籍期間表はJSON配列で指定してください")
    periods: list[EmploymentPeriod] = []
    for item in raw:
        if not isinstance(item, dict):
            raise EmploymentPeriodConfigError(f"要素がオブジェクトではありません: {item!r}")
        unknown = set(item) - EMPLOYMENT_PERIOD_KEYS
        if unknown:
            # キーのタイプミス（"ends" 等）を黙って無視すると、意図した期間と違う表で
            # 推定してしまう。知らないキーがあれば設定ミスとして全体を無効化する。
            raise EmploymentPeriodConfigError(f"未知のキーがあります: {sorted(unknown)}")
        employer = _parse_employer(item.get("employer"))
        start_ym = _parse_ym(item.get("start"), "start", now)
        end_raw = item.get("end")
        if end_raw is None or (isinstance(end_raw, str) and not end_raw.strip()):
            end_ym = None
        else:
            end_ym = _parse_ym(end_raw, "end", now)
            if end_ym < start_ym:
                raise EmploymentPeriodConfigError(f"end が start より前です: {item!r}")
        verified_raw = item.get("verified_through")
        if verified_raw is None or (isinstance(verified_raw, str) and not verified_raw.strip()):
            verified_ym = None
        else:
            verified_ym = _parse_ym(verified_raw, "verified_through", now)
            if verified_ym < start_ym:
                raise EmploymentPeriodConfigError(
                    f"verified_through が start より前です: {item!r}"
                )
            if end_ym is not None and verified_ym > end_ym:
                # 在籍が終わっている期間の「確認済み月」が終了月より後＝矛盾した設定。
                # 現状は end が推定上限になるので誤推定には繋がらないが、厳格パースとして弾く。
                raise EmploymentPeriodConfigError(
                    f"verified_through が end より後です: {item!r}"
                )
        periods.append(
            EmploymentPeriod(
                employer=employer,
                start_ym=start_ym,
                end_ym=end_ym,
                verified_through_ym=verified_ym,
            )
        )
    return tuple(periods)


def load_employment_periods(
    root: Path | None = None, now: datetime | None = None
) -> tuple[EmploymentPeriod, ...]:
    """在籍期間表を返す。`_学習データ/employment_periods.json` があればそれを優先する。

    ファイルが置かれているのに壊れている（JSONとして読めない・値が不正）場合は、既定値へ
    戻さず**空を返す**＝発行元のフォールバックが働かず `不明` のまま残る。既定値へ戻すと
    「転職したので書き換えたファイルが壊れていた」ときに古い勤務先名を書いてしまうため、
    安全側（埋めない）に倒す。
    """
    if root is not None:
        path = folder_path(Path(root), LEARNING) / EMPLOYMENT_PERIODS_FILE
        if path.exists():
            try:
                if path.stat().st_size > EMPLOYMENT_PERIODS_MAX_BYTES:
                    # 想定外に巨大なファイルは読まない（無人運転でメモリ/CPUを食わせない）。
                    return ()
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                # 文字コードが壊れている場合も「読めない」と同じ扱い＝推定を無効化する。
                # ここで例外を漏らすと分類そのものが失敗し、給与明細が `_要確認` に落ちてしまう。
                return ()
            return parse_employment_periods(raw, now)
    return parse_employment_periods([dict(p) for p in DEFAULT_EMPLOYMENT_PERIODS], now)


def get_root(root: Path | str | None = None) -> Path:
    """書類整理ルートの実パスを返す。

    優先順位: 明示引数 > 環境変数 HOME_DOC_ROOT > デフォルト（実iCloudパス）。
    """
    if root is not None:
        return Path(root).expanduser()
    env = os.environ.get("HOME_DOC_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_ROOT


def folder_path(root: Path, name: str) -> Path:
    return root / name
