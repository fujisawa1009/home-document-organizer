"""第2段階: 内容読み取り・分類ロジック（REQUIREMENTS.md 7-2）。

プロトタイプではキーワード・正規表現ベースのルールベース分類器を実装する
（9章「具体的な選定はClaude Codeに任せる」に基づく判断。将来的にOCR精度や
実データに応じてLLM/専用モデルへ差し替え可能なようクラス境界を分離してある）。

確信度が「低」、または分類できない場合は必ず `_要確認` へ倒す（絶対原則5）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
        "運転免許証": ["運転免許証"],
        "マイナンバーカード": ["マイナンバーカード", "個人番号カード"],
        "健康保険証": ["健康保険被保険者証", "健康保険証"],
        "パスポート": ["旅券", "パスポート"],
        "住民票": ["住民票の写し", "住民票"],
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

_WESTERN_DATE_RE = re.compile(r"(20\d{2})[年/\-\.](\d{1,2})[月/\-\.](\d{1,2})日?")
# 元号1年目は「元年」表記が一般的（例: 令和元年5月1日）。"\d{1,2}" だけでは拾えない。
_REIWA_DATE_RE = re.compile(r"令和\s*(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日")
_HEISEI_DATE_RE = re.compile(r"平成\s*(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日")


def _era_to_int(era_raw: str) -> int:
    return 1 if era_raw == "元" else int(era_raw)


@dataclass
class ClassificationResult:
    category_key: str  # "税金"/"銀行"/"保険"/"身分証"/"その他"/"" (不明)
    suggested_folder: str  # config.CATEGORY_* または config.NEEDS_REVIEW
    doc_type: str
    issuer: str
    date_str: str  # YYYYMMDD
    estimated_date: bool
    confidence: str  # "高"/"中"/"低"
    reason: str


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
    )


def classify_document(text: str, ext: str, mtime: datetime) -> ClassificationResult:
    ext = ext.lower()
    if ext not in config.SUPPORTED_EXTENSIONS:
        return _fallback_result(mtime, "対象外拡張子（処理対象は pdf/jpg/jpeg/png/heic のみ）")

    if not text.strip():
        return _fallback_result(mtime, "テキスト抽出不可（内容が読み取れない・破損/パスワード保護の可能性）")

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
    )


def classify_file(path: Path, root: Path | None = None) -> ClassificationResult:
    """root を渡すと、キーワード辞書より先に学習済みルール（learning.py）を試す。

    学習ルールは過去にCEOが承認した「発行元(判定)」をキーワードとして記憶したもの
    ＝人間確認済みの実例なので、確信度は無条件で「高」とする。
    """
    ext = path.suffix.lower()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    text = extract.extract_text(path)

    if root is not None:
        rule = learning.match(root, text)
        if rule is not None:
            return _from_learned_rule(rule, mtime)

    return classify_document(text, ext, mtime)
