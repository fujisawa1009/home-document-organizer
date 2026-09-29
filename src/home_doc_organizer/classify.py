"""第2段階: 内容読み取り・分類ロジック（REQUIREMENTS.md 7-2）。

プロトタイプではキーワード・正規表現ベースのルールベース分類器を実装する
（9章「具体的な選定はClaude Codeに任せる」に基づく判断。将来的にOCR精度や
実データに応じてLLM/専用モデルへ差し替え可能なようクラス境界を分離してある）。

確信度が「低」、または分類できない場合は必ず `_要確認` へ倒す（絶対原則5）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from . import config, extract, learning

# category_key -> { 書類種別ラベル: [キーワード...] }
DOC_TYPE_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "税金": {
        "納税証明書": ["納税証明書", "納税証明"],
        "確定申告書": ["確定申告書", "確定申告"],
        "課税証明書": ["課税証明書", "課税証明", "非課税証明"],
        "固定資産税納税通知書": ["固定資産税"],
        "住民税決定通知書": ["住民税", "特別徴収税額"],
    },
    "銀行": {
        "残高証明書": ["残高証明"],
        "取引明細書": ["取引明細", "ご利用明細"],
        "通帳(写し)": ["普通預金通帳", "総合口座通帳", "通帳"],
        "ローン契約書": ["金銭消費貸借契約", "ローン契約"],
    },
    "保険": {
        "保険証券": ["保険証券"],
        "保険料控除証明書": ["保険料控除証明"],
        "契約内容のお知らせ": ["ご契約内容のお知らせ", "契約内容のお知らせ"],
    },
    "身分証": {
        "運転免許証": ["運転免許証", "免許証"],
        "マイナンバーカード": ["マイナンバーカード", "個人番号カード"],
        "健康保険証": ["健康保険被保険者証", "健康保険証"],
        "パスポート": ["旅券", "パスポート"],
        "住民票": ["住民票の写し", "住民票"],
        "証明写真": ["証明写真"],
    },
}

# category_key -> 一般キーワード（doc_type未特定でもカテゴリだけ推定するための補助）
GENERIC_KEYWORDS: dict[str, list[str]] = {
    "税金": ["税務署", "国税", "地方税", "税額", "課税"],
    "銀行": ["銀行", "信用金庫", "信用組合", "口座番号", "支店"],
    "保険": ["保険", "共済", "被保険者"],
    "身分証": ["公安委員会", "個人番号", "生年月日"],
}

ISSUER_SUFFIXES: dict[str, list[str]] = {
    "税金": ["税務署", "都税事務所", "県税事務所", "市役所", "区役所", "町役場", "村役場"],
    "銀行": ["銀行", "信用金庫", "信用組合"],
    "保険": ["生命保険", "損害保険", "海上火災保険", "少額短期保険", "共済"],
    "身分証": ["公安委員会", "市役所", "区役所", "町役場", "村役場"],
}

# --- 給与・賞与明細の優先判定（DOC_TYPE_KEYWORDS の一致数比較より先に走る） -------------
#
# なぜ優先判定が必要か（2026-09-29 に実測した誤分類の再発防止）:
#   給与明細は本文に「住民税」「所得税」「健康保険料」「厚生年金保険料」を必ず含むため、
#   DOC_TYPE_KEYWORDS の「一致数が最大のカテゴリを採る」方式に混ぜると税金・保険カテゴリへ
#   吸われる。実際に launchd の自動振り分けが給与明細1件を
#   `01_税金/住民税決定通知書` へ入れた（残り9件は手がかり不足で `_要確認`）。
#   そこで一致数を数える前に、給与明細だけが持つ強い識別語をここで先に判定する。

# (1) 書類名そのもの。最も確実な識別語。
PAYROLL_TITLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "賞与明細": ("賞与明細書", "賞与明細", "賞与支給明細"),
    "給与明細": ("給与明細書", "給与明細", "給料明細書", "給料明細", "給与支給明細"),
}

# 発行元（勤務先）の法人格。給与明細は「株式会社◯◯」と法人格が前に付く表記も
# 「◯◯株式会社」と後ろに付く表記もあるため、どちらも拾えるようにする。
# 既存カテゴリの `_find_issuer`（接尾辞方式）は触らず、給与専用の抽出関数を別に持つ
# ＝税金・銀行・保険・身分証の発行元抽出の挙動を変えないため。
PAYROLL_CORPORATE_FORMS: tuple[str, ...] = (
    "株式会社",
    "有限会社",
    "合同会社",
    "合資会社",
    "合名会社",
)

# 無人運転で1ファイルの処理に張り付かないための、判定に使うテキスト量の上限。
# 超える場合は先頭と末尾を残す（発行元はフッタに刷られるため末尾も必要）。
MAX_CLASSIFY_TEXT_CHARS = 200_000

# (2) 「202601月給与」「2026年6月給与」のような支給年月＋種別の表記。
#     決定者の明細はスキャンPDFでOCRが1文字も返さない実物があり（実測）、
#     その場合ファイル名だけが唯一の手がかりになる。
#     空白は半角スペース/タブのみ許し**改行は許さない**（NFKC正規化後に判定するので
#     全角スペースは半角に、全角数字は半角数字になっている）。改行を許すと
#     「対象年度 2026年\n6月\n給与から控除する…」のように別行が偶然つながって誤マッチする。
#     「月」と「給与/賞与」の間にも余分な文字を挟ませない＝隣接のみ許す。
# 種別の直後に続くと意味が変わる語は否定先読みで外す（「202601月給与振込」は銀行の
# 入出金明細、「2026年6月給与所得」は税の書類で、いずれも明細票ではない）。
_PAYROLL_KIND = r"(給与|給料|賞与)(?!振込|所得|収入|支払報告|支払明細書?の)"
_PAYROLL_YEAR_MONTH_RE = re.compile(
    r"(20[0-9]{2})[ \t]*年?[ \t]*(1[0-2]|0?[1-9])[ \t]*月[ \t]*" + _PAYROLL_KIND
)
# 和暦表記（令和8年6月給与／令和元年6月給与）。西暦表記と同じ厳しさで隣接のみ許す。
_PAYROLL_REIWA_YEAR_MONTH_RE = re.compile(
    r"令和[ \t]*(元|[0-9]{1,2})[ \t]*年[ \t]*(1[0-2]|0?[1-9])[ \t]*月[ \t]*" + _PAYROLL_KIND
)
# OCR誤読で桁が化けた年（例: 2096年）を素通しさせないための妥当な年の範囲。
PAYROLL_YEAR_MIN = 1990

# (3) 明細票の欄名。OCRが崩れて書類名を拾えなくても、欄名が複数揃えば明細と判定できる。
#     「中核」＝明細票（給与・賞与）にしか出ない合計欄。他カテゴリの書類には現れない。
PAYROLL_CORE_FIELD_MARKERS: tuple[str, ...] = (
    "差引支給額",
    "総支給額",
    "控除合計",
    "社会保険計",
    "支給控除一覧",
    "基本賞与",
    "賞与社会保険合計",
)
#     「補助」＝明細票らしいが単独では弱い欄名（求人票・就業規則等にも出うる）。
PAYROLL_SUPPORTING_FIELD_MARKERS: tuple[str, ...] = (
    "本人給",
    "基本給",
    "役職手当",
    "通勤手当",
    "出勤日数",
    "稼動日数",
    "有休残",
    "業績賞与",
    "決算賞与",
)
# 成立条件: 中核2件以上、または 中核1件＋補助3件以上。
# 単純な合計件数の閾値だと「基本給・役職手当・通勤手当」だけの求人票/規程で成立してしまうため、
# 明細票にしか出ない中核欄を必ず1件以上要求する。
PAYROLL_CORE_MARKER_MIN = 2
PAYROLL_SUPPORTING_MARKER_MIN = 3

# 賞与明細だけに出る欄名（欄名しか手がかりが無いときの給与/賞与の区別に使う）。
BONUS_ONLY_FIELD_MARKERS: tuple[str, ...] = ("基本賞与", "賞与社会保険合計", "業績賞与", "決算賞与")

# 給与判定の対抗証拠となる語。これらを含む書類は給与明細ではなく税金カテゴリの書類として
# 扱い、優先判定をスキップして既存ロジック（DOC_TYPE_KEYWORDS）に委ねる。
# 例: 住民税決定通知書は「給与所得」「月割額」等を含み、源泉徴収票は給与の集計表だが
#     いずれも明細票ではなく、従来どおり 01_税金 が正しい置き場所。
# ただし**絶対拒否にはしない**: 「給与明細書」という明示の書類名がある場合は本物の明細票と
# 見なして通す（明細の注記に「確定申告」等の語が混ざっただけで弾かれるのを避ける）。
PAYROLL_EXCLUSION_KEYWORDS: tuple[str, ...] = (
    "特別徴収税額",
    "住民税決定通知",
    "源泉徴収票",
    "扶養控除等申告書",
    "納税証明",
    "課税証明",
    "確定申告",
)

# 既存カテゴリの「書類名そのもの」キーワード。これらを含む書類は既存カテゴリの書類である
# 可能性が高いので、給与の優先判定に弱い証拠（欄名だけ）で吸わせない。
# 給与明細の本文にも現れてしまう語は除く（給与明細は必ず「住民税」欄を持つ）。
PAYROLL_COMPETING_TITLE_EXCEPTIONS = frozenset({"住民税"})
PAYROLL_COMPETING_DOC_TITLES: tuple[str, ...] = tuple(
    kw
    for doc_map in DOC_TYPE_KEYWORDS.values()
    for keywords in doc_map.values()
    for kw in keywords
    if kw not in PAYROLL_COMPETING_TITLE_EXCEPTIONS
)

_WESTERN_DATE_RE = re.compile(r"(20\d{2})[年/\-\.](\d{1,2})[月/\-\.](\d{1,2})日?")
# 元号1年目は「元年」表記が一般的（例: 令和元年5月1日）。"\d{1,2}" だけでは拾えない。
_REIWA_DATE_RE = re.compile(r"令和\s*(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日")
_HEISEI_DATE_RE = re.compile(r"平成\s*(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日")


def _era_to_int(era_raw: str) -> int:
    return 1 if era_raw == "元" else int(era_raw)


@dataclass
class ClassificationResult:
    category_key: str  # "税金"/"銀行"/"保険"/"身分証"/"給与"/"その他"/"" (不明)
    suggested_folder: str  # config.CATEGORY_* または config.NEEDS_REVIEW
    doc_type: str
    issuer: str
    date_str: str  # YYYYMMDD
    estimated_date: bool
    confidence: str  # "高"/"中"/"低"
    reason: str
    # 日付の精度。"day"=書類から日付まで読めた／"month"=月単位の書類で日は01固定
    # （給与・賞与明細）／"mtime"=読めずファイル更新日時で代用（estimated_date=True と対応）。
    # 既定は "day"（既存の呼び出し側を変えないため）。CSV表示の注記に使う。
    date_precision: str = "day"


def _find_issuer(text: str, category_key: str) -> str:
    for suffix in ISSUER_SUFFIXES.get(category_key, []):
        idx = text.find(suffix)
        if idx == -1:
            continue
        prefix_window = text[max(0, idx - 10) : idx]
        m = re.search(r"[^\s、。\n]*$", prefix_window)
        prefix = m.group(0) if m else ""
        return f"{prefix}{suffix}"
    return ""


def _extract_date(text: str) -> tuple[str, bool]:
    """本文中の日付を YYYYMMDD で返す。見つからなければ ("", True)。"""
    m = _WESTERN_DATE_RE.search(text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return f"{y:04d}{mo:02d}{d:02d}", False
    m = _REIWA_DATE_RE.search(text)
    if m:
        era_raw, mo, d = m.groups()
        era, mo, d = _era_to_int(era_raw), int(mo), int(d)
        y = 2018 + era  # 令和元年(1) = 2019
        return f"{y:04d}{mo:02d}{d:02d}", False
    m = _HEISEI_DATE_RE.search(text)
    if m:
        era_raw, mo, d = m.groups()
        era, mo, d = _era_to_int(era_raw), int(mo), int(d)
        y = 1988 + era  # 平成元年(1) = 1989
        return f"{y:04d}{mo:02d}{d:02d}", False
    return "", True


def _normalize_for_payroll(text: str) -> str:
    """全角数字・半角カナ等の表記ゆれを吸収し（NFKC）、長さに上限をかける。

    OCRやPDFのテキスト層は「２０２６年６月給与」「ｴﾌﾃｨｸﾞﾙｰﾌﾟ」のような表記で出てくる。
    正規表現やキーワード照合の前に一度だけ正規化しておけば、表記ごとにパターンを
    増やさずに済む。長さの上限は無人運転で1ファイルの処理に張り付かないための保険
    （先頭と末尾を残す＝発行元はフッタに刷られるため末尾も判定に要る）。
    """
    if len(text) > MAX_CLASSIFY_TEXT_CHARS:
        half = MAX_CLASSIFY_TEXT_CHARS // 2
        text = f"{text[:half]}\n...\n{text[-half:]}"
    return unicodedata.normalize("NFKC", text)


@dataclass(frozen=True)
class _PayrollPeriod:
    """支給年月と、その表記に添えられていた種別（給与/賞与）。

    年月と種別を1回の解析でまとめて返す＝別々に検索して「年月は6月の表記から、
    種別は別の行の表記から」という食い違いが起きないようにするため。
    """

    year: int
    month: int
    doc_type: str


def _find_payroll_period(text: str) -> _PayrollPeriod | None:
    """「202601月給与」「2026年6月給与」「令和8年6月給与」から支給年月と種別を読む。

    西暦・和暦の一致を**テキストに現れた順**で評価し、最初の妥当なものを採る。
    OCRで桁が化けた年（例: 2096年）は妥当な年の範囲から外れるので読み飛ばし、
    次の一致を見る（先頭の化けた1件で諦めない）。妥当な年の上限を「今年＋1年」に
    しているのは、翌年分の明細を先に受け取ることはあっても数十年先の明細は無いため。
    """
    year_max = datetime.now().year + 1
    candidates: list[tuple[int, _PayrollPeriod]] = []
    for m in _PAYROLL_YEAR_MONTH_RE.finditer(text):
        candidates.append((m.start(), _to_period(int(m.group(1)), m.group(2), m.group(3))))
    for m in _PAYROLL_REIWA_YEAR_MONTH_RE.finditer(text):
        year = 2018 + _era_to_int(m.group(1))  # 令和元年(1) = 2019
        candidates.append((m.start(), _to_period(year, m.group(2), m.group(3))))
    for _, period in sorted(candidates, key=lambda c: c[0]):
        if PAYROLL_YEAR_MIN <= period.year <= year_max:
            return period
    return None


def _to_period(year: int, month_raw: str, kind_raw: str) -> _PayrollPeriod:
    return _PayrollPeriod(
        year=year,
        month=int(month_raw),
        doc_type="賞与明細" if kind_raw == "賞与" else "給与明細",
    )


def _find_payroll_issuer(text: str) -> str:
    """給与・賞与明細の発行元（勤務先）を返す。見つからなければ空文字。

    法人格が前に付く表記（株式会社エフティグループ）と後ろに付く表記
    （エフティグループ株式会社）の両方を候補にし、**最後に現れた候補**を採る。
    明細票は発行者名をフッタ（最終行）に刷るのが通例で、本文に「株式会社からのお知らせ」の
    ような一般文が先に出ていても取り違えないため。
    注意: フッタに振込先・グループ会社名が並ぶ帳票では取り違えうる（`確信度:高` を
    付ける条件に発行元が読めたことを含めているので、誤りは変更案CSVで目視できる）。
    """
    name_re = r"[^\s、。:：/（）()0-9]{2,16}"
    # (出現位置, 表記の優先度, 発行元名)。優先度は 0=前置（株式会社◯◯）/ 1=後置（◯◯株式会社）。
    # 同じ位置で両方の解釈が立つときは前置を採る＝後置解釈は直前の任意文字列を拾うため、
    # 「…差引支給額株式会社」のように直前の欄名を巻き込むことがあるため。
    candidates: list[tuple[int, int, str]] = []
    for form in PAYROLL_CORPORATE_FORMS:
        for m in re.finditer(re.escape(form), text):
            after = re.match(name_re, text[m.end() : m.end() + 16])
            if after:
                candidates.append((m.start(), 0, f"{form}{after.group(0)}"))
            before = re.search(name_re + r"$", text[max(0, m.start() - 16) : m.start()])
            if before:
                candidates.append((m.start(), 1, f"{before.group(0)}{form}"))
    if not candidates:
        return ""
    # 位置が最後の候補を採る（明細票は発行者名をフッタに刷る）。同位置は前置表記を優先。
    return max(candidates, key=lambda c: (c[0], -c[1]))[2]


def _classify_payroll(
    text: str, mtime: datetime, hint_text: str = ""
) -> ClassificationResult | None:
    """給与明細・賞与明細なら結果を返す。該当しなければ None（既存ロジックへ委ねる）。

    `hint_text` はファイル名・受信箱サブフォルダ名だけを連結したもの（`text` の一部）。
    決定者が自分で付けた名前＝人が確認済みのラベルなので、本文（OCR結果）より強い証拠として
    扱う。実物のスキャンPDFは pypdf でもOCRでも1文字も取れないものがあり（実測）、
    その場合ファイル名の `202601月給与.pdf` が唯一の手がかりになる。

    カテゴリ成立の条件（いずれか1つ）:
      (1) 「給与明細書」「賞与明細書」等の明示の書類名が本文またはヒントにある
      (2) ヒント（ファイル名・サブフォルダ名）が支給年月＋種別の形をしている
      (3) 明細票固有の欄名がそろう（中核2件以上、または中核1件＋補助3件以上）

    本文中の支給年月表記だけ（=(1)(3)のどちらも無い）では成立させない。
    「保険料は2026年6月給与から控除します」のような他カテゴリの書類の一文と
    区別できないため（日付の補完にだけ使う）。

    日付は支給年月を採り、明細票が日を持たないことを `date_precision="month"` で明示する
    （日は命名規則 `YYYYMMDD_...` に合わせて 01 固定）。更新日時で代用した場合は
    従来どおり `estimated_date=True` / `date_precision="mtime"`。
    """
    text = _normalize_for_payroll(text)
    hint_text = _normalize_for_payroll(hint_text)

    bonus_title = any(kw in text for kw in PAYROLL_TITLE_KEYWORDS["賞与明細"])
    salary_title = any(kw in text for kw in PAYROLL_TITLE_KEYWORDS["給与明細"])
    hint_period = _find_payroll_period(hint_text) if hint_text else None
    body_period = _find_payroll_period(text)
    core_hits = sum(1 for m in PAYROLL_CORE_FIELD_MARKERS if m in text)
    supporting_hits = sum(1 for m in PAYROLL_SUPPORTING_FIELD_MARKERS if m in text)
    layout_match = core_hits >= PAYROLL_CORE_MARKER_MIN or (
        core_hits >= 1 and supporting_hits >= PAYROLL_SUPPORTING_MARKER_MIN
    )

    # 証拠の強さ（強い順）:
    #   strong = 「給与明細書」「賞与明細書」等の明示の書類名
    #   medium = ファイル名・受信箱サブフォルダ名が「◯年◯月給与」の形（人が付けた名前）
    #   weak   = 明細票固有の欄名がそろう（OCRが崩れても残る体裁の証拠）
    strong = bonus_title or salary_title
    medium = hint_period is not None
    weak = layout_match

    # 必要な証拠の量は、対抗する証拠の強さで変える。
    if any(kw in text for kw in PAYROLL_EXCLUSION_KEYWORDS):
        # 税金カテゴリの書類にしか出ない語がある（住民税決定通知書・源泉徴収票 等）。
        # 書類名の言及だけでは通さない＝税書類の本文に「詳細は給与明細書でご確認ください」と
        # 書かれているだけのケースを吸わないため、明細票の体裁かファイル名の裏付けも要求する。
        if not (strong and (medium or weak)):
            return None
    elif any(kw in text for kw in PAYROLL_COMPETING_DOC_TITLES):
        # 既存カテゴリの書類名がある（保険料控除証明書・ご利用明細 等）。
        # 欄名だけの弱い証拠で給与へ吸わない。
        if not (strong or (medium and weak)):
            return None
    elif not (strong or medium or weak):
        return None

    # 給与/賞与の判別は「そのファイル固有の手がかり」を優先する。
    # 受信箱のサブフォルダ名（例: `00_受信箱/給与明細/`）もヒントに入るため、書類名
    # キーワードだけで決めると同じフォルダに入れた賞与明細まで「給与明細」になる
    # （実測: `00_受信箱/給与明細/202607月賞与.pdf` が給与明細と判定され、同月の給与明細と
    # ファイル名が衝突した）。優先順位は
    #   ①「賞与明細書」という明示の書類名（最も強い）
    #   ② ヒントの支給年月＋種別（ファイル単位で確実）
    #   ③ 本文の支給年月＋種別
    #   ④「給与明細」という書類名（サブフォルダ名由来のことがあり粒度が粗い）
    #   ⑤ 欄名（賞与固有の欄名があれば賞与）
    if bonus_title:
        doc_type = "賞与明細"
    elif hint_period:
        doc_type = hint_period.doc_type
    elif body_period:
        doc_type = body_period.doc_type
    elif salary_title:
        doc_type = "給与明細"
    else:
        doc_type = "賞与明細" if any(m in text for m in BONUS_ONLY_FIELD_MARKERS) else "給与明細"

    period = hint_period or body_period
    if period:
        date_str = f"{period.year:04d}{period.month:02d}01"
        estimated = False
        precision = "month"
        source = "ファイル名" if hint_period else "本文"
        date_note = (
            f"支給年月{period.year}-{period.month:02d}を{source}から抽出"
            "（明細票は日を持たないため日は01固定）"
        )
    else:
        date_str, estimated = _extract_date(text)
        if date_str:
            precision = "day"
            date_note = "本文の日付を抽出"
        else:
            date_str = mtime.strftime("%Y%m%d")
            estimated = True
            precision = "mtime"
            date_note = "支給年月が読み取れず更新日時で代用"

    issuer = _find_payroll_issuer(text) or "不明"

    # 発行元が読めなくても「給与/賞与明細である」判定自体は確かなので `_要確認` へは倒さない
    # （倒すと誤分類前と同じ状態＝ファイル名が `不明_不明` に戻ってしまう）。
    confidence = "高" if issuer != "不明" and not estimated else "中"

    return ClassificationResult(
        category_key="給与",
        suggested_folder=config.CATEGORY_KEY_TO_FOLDER["給与"],
        doc_type=doc_type,
        issuer=issuer,
        date_str=date_str,
        estimated_date=estimated,
        confidence=confidence,
        reason=(
            f"給与/賞与明細の優先判定に一致（{doc_type}・欄名 中核{core_hits}件/補助{supporting_hits}件）"
            f"・{date_note}"
        ),
        date_precision=precision,
    )


def _fallback_result(mtime: datetime, reason: str) -> ClassificationResult:
    return ClassificationResult(
        category_key="",
        suggested_folder=config.NEEDS_REVIEW,
        doc_type="不明",
        issuer="不明",
        date_str=mtime.strftime("%Y%m%d"),
        estimated_date=True,
        confidence="低",
        reason=reason,
        date_precision="mtime",
    )


def classify_document(
    text: str, ext: str, mtime: datetime, hint_text: str = ""
) -> ClassificationResult:
    """`hint_text` はファイル名・受信箱サブフォルダ名だけを連結したもの（`text` の一部）。

    省略時は「本文だけが手がかり」として扱う（既存の呼び出し側の挙動は変わらない）。
    """
    ext = ext.lower()
    if ext not in config.SUPPORTED_EXTENSIONS:
        return _fallback_result(mtime, "対象外拡張子（処理対象は pdf/jpg/jpeg/png/heic のみ）")

    if not text.strip() and not hint_text.strip():
        return _fallback_result(mtime, "テキスト抽出不可（内容が読み取れない・破損/パスワード保護の可能性）")

    # 給与・賞与明細は税金/保険のキーワードを必ず含むため、一致数の比較より先に判定する
    payroll = _classify_payroll(text, mtime, hint_text=hint_text)
    if payroll is not None:
        return payroll

    best_category = ""
    best_doc_type = ""
    best_hits = 0
    for category_key, doc_map in DOC_TYPE_KEYWORDS.items():
        for doc_type, keywords in doc_map.items():
            hits = sum(text.count(kw) for kw in keywords)
            if hits > best_hits:
                best_hits = hits
                best_category = category_key
                best_doc_type = doc_type

    strong_match = best_hits > 0

    if not strong_match:
        # 具体的な書類種別は特定できないが、一般キーワードでカテゴリだけ推定を試みる
        generic_best_key = ""
        generic_best_hits = 0
        for category_key, keywords in GENERIC_KEYWORDS.items():
            hits = sum(text.count(kw) for kw in keywords)
            if hits > generic_best_hits:
                generic_best_hits = hits
                generic_best_key = category_key
        if generic_best_hits == 0:
            return _fallback_result(mtime, "書類種別・カテゴリともにキーワード一致なし")
        best_category = generic_best_key
        best_doc_type = "不明（要確認）"

    issuer = _find_issuer(text, best_category) or "不明"
    date_str, estimated = _extract_date(text)
    if not date_str:
        date_str = mtime.strftime("%Y%m%d")
        estimated = True

    if strong_match and issuer != "不明" and not estimated:
        confidence = "高"
        reason = f"書類種別キーワード一致（{best_doc_type}）・発行元・日付とも抽出済み"
    elif strong_match or issuer != "不明":
        confidence = "中"
        missing = []
        if issuer == "不明":
            missing.append("発行元")
        if estimated:
            missing.append("書類日付（更新日時で代用）")
        if not strong_match:
            missing.append("書類種別は一般キーワードのみで推定")
        reason = "一部項目が推定/未検出: " + "・".join(missing) if missing else "カテゴリ推定"
    else:
        confidence = "低"
        reason = "カテゴリの手がかりが弱く要確認"

    suggested_folder = (
        config.NEEDS_REVIEW if confidence == "低" else config.CATEGORY_KEY_TO_FOLDER[best_category]
    )

    return ClassificationResult(
        category_key=best_category,
        suggested_folder=suggested_folder,
        doc_type=best_doc_type,
        issuer=issuer,
        date_str=date_str,
        estimated_date=estimated,
        confidence=confidence,
        reason=reason,
        date_precision="mtime" if estimated else "day",
    )


def _from_learned_rule(rule: dict, mtime: datetime) -> ClassificationResult:
    category_key = rule["category_key"]
    return ClassificationResult(
        category_key=category_key,
        suggested_folder=config.CATEGORY_KEY_TO_FOLDER[category_key],
        doc_type=rule.get("doc_type") or "不明",
        issuer=rule.get("issuer") or "不明",
        date_str=mtime.strftime("%Y%m%d"),
        estimated_date=True,  # 学習ルールは日付までは覚えない（更新日時を採用）
        confidence="高",
        reason=f"学習済みルールに一致（キーワード: {rule['keyword']!r}）",
        date_precision="mtime",
    )


def classify_file(path: Path, root: Path | None = None) -> ClassificationResult:
    """root を渡すと、キーワード辞書より先に学習済みルール（learning.py）を試す。

    学習ルールは過去にCEOが承認した「発行元(判定)」をキーワードとして記憶したもの
    ＝人間確認済みの実例なので、確信度は無条件で「高」とする。

    ファイル名（拡張子を除く部分）・受信箱直下のサブフォルダ名も、OCR/テキスト抽出結果と
    合わせて判定材料にする。免許証など「セキュリティ模様・反射・小さい文字」でOCR精度が
    特に厳しい書類は、受信箱に入れる前にファイル名やサブフォルダ名へ「免許証」等の
    ヒントを入れてもらうことで、OCRが失敗しても確実に分類できるようにするため
    （CEO確認2026-08-16）。例: `00_受信箱/01_証明写真/IMG_1234.jpg` なら
    「01_証明写真」もヒントとして使われる。
    """
    ext = path.suffix.lower()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    ocr_text = extract.extract_text(path)

    hints = [path.stem]
    if path.parent.name and path.parent.name != config.INBOX:
        hints.insert(0, path.parent.name)  # サブフォルダ名（あれば）を最優先ヒントにする
    # ヒント（人が付けた名前）と本文（OCR結果）は連結して渡すが、どこまでがヒントかも
    # 別に渡す。給与明細の判定はヒント由来か本文由来かで証拠の強さが違うため。
    hint_text = "\n".join(hints)
    text = "\n".join(hints + ([ocr_text] if ocr_text else []))

    if root is not None:
        rule = learning.match(root, text)
        if rule is not None:
            learned = _from_learned_rule(rule, mtime)
            if learned.category_key != "給与":
                return learned
            # 給与カテゴリの学習ルールだけは裏付けを要求する。学習ルールのキーワードは
            # 決定者が承認した「発行元(判定)」＝勤務先の社名になりがちで、同じ勤務先が出す
            # 源泉徴収票・通知書・内定通知まで「給与明細」にしてしまう。明細票である裏付けが
            # 取れないときは学習ルールを使わず、通常のキーワード辞書経路へ落とす。
            payroll = _classify_payroll(text, mtime, hint_text=hint_text)
            if payroll is None:
                return classify_document(text, ext, mtime, hint_text=hint_text)
            return _merge_payroll_month(learned, payroll)

    return classify_document(text, ext, mtime, hint_text=hint_text)


def _merge_payroll_month(
    result: ClassificationResult, payroll: ClassificationResult
) -> ClassificationResult:
    """学習ルール由来の結果へ、優先判定が読み取った支給年月・書類種別を差し込む。

    学習ルールは発行元を覚える代わりに日付を覚えない（更新日時を採用する）。給与・賞与明細は
    毎月同じ発行元から届くため、学習ルールが登録されると本来ファイル名に入るべき支給年月が
    全件「処理した日」に置き換わり、月の違いが名前から消えてしまう。そこで給与カテゴリに限り、
    支給年月まで読めた場合（`date_precision == "month"`）だけ日付と書類種別を上書きする
    （発行元は学習ルール側＝決定者が確認済みの値を残す）。他カテゴリの挙動は一切変えない。
    """
    # 書類種別（給与明細/賞与明細）は常に実判定側を採る。学習ルールは「発行元」を覚える
    # 仕組みで、給与明細として登録したルールが賞与明細にも当たるため、学習ルール側の
    # 書類種別を残すと賞与明細が給与明細の名前で保存されてしまう。
    merged = replace(
        result, doc_type=payroll.doc_type, reason=f"{result.reason}／{payroll.reason}"
    )
    # 日付は支給年月まで読めたときだけ差し込む（読めなければ学習ルールどおり更新日時）。
    if payroll.date_precision != "month":
        return merged
    return replace(
        merged,
        date_str=payroll.date_str,
        estimated_date=False,
        date_precision="month",
    )

