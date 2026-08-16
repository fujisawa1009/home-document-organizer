"""第2段階: 変更案.csv の作成（REQUIREMENTS.md 7-3）。

列構成はCEO承認済みの指示書どおり固定。Excel/Numbersでそのまま開けるよう
utf-8-sig（BOM付き）で出力する。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import classify, config, logger
from .inbox_scan import list_inbox_files
from .naming import build_filename, unique_destination

CSV_COLUMNS = [
    "元ファイル名",
    "元ファイルパス",
    "提案カテゴリ",
    "提案新ファイル名",
    "書類種別(判定)",
    "発行元(判定)",
    "書類日付(判定)",
    "確信度",
    "承認",
    "備考",
]


@dataclass
class ProposalRow:
    source_name: str
    source_path: str
    suggested_category: str
    suggested_filename: str
    doc_type: str
    issuer: str
    doc_date: str
    confidence: str
    reason: str

    def to_csv_row(self) -> list[str]:
        return [
            self.source_name,
            self.source_path,
            self.suggested_category,
            self.suggested_filename,
            self.doc_type,
            self.issuer,
            self.doc_date,
            self.confidence,
            "",  # 承認: 空欄（人間 or 承認チャネルが記入）
            self.reason,
        ]


def _classify_safely(root: Path, src: Path) -> classify.ClassificationResult:
    """classify_file の例外で propose 全体を止めない（8章）。

    失敗は _要確認 へ倒しつつ、CSVの備考だけでなく操作ログにも残す
    （7-5「分類エラー」は本来ここで記録されるべき失敗種別）。
    """
    try:
        return classify.classify_file(src)
    except Exception as exc:  # noqa: BLE001 - 1ファイルの想定外失敗で全体を止めない
        logger.log_operation(
            root, logger.OP_CLASSIFY_ERROR, str(src), "", logger.RESULT_NG, detail=str(exc)
        )
        return classify.ClassificationResult(
            category_key="",
            suggested_folder=config.NEEDS_REVIEW,
            doc_type="不明",
            issuer="不明",
            date_str=datetime.now().strftime("%Y%m%d"),
            estimated_date=True,
            confidence="低",
            reason=f"分類処理で例外が発生: {exc}",
        )


def build_proposal_rows(root: Path) -> list[ProposalRow]:
    rows: list[ProposalRow] = []
    for src in list_inbox_files(root):
        result = _classify_safely(root, src)
        new_filename = build_filename(
            result.date_str, result.doc_type, result.issuer, src.suffix, result.estimated_date
        )
        doc_date_display = f"{result.date_str[:4]}-{result.date_str[4:6]}-{result.date_str[6:8]}"
        if result.estimated_date:
            doc_date_display += "（推定・更新日時）"
        rows.append(
            ProposalRow(
                source_name=src.name,
                source_path=str(src),
                suggested_category=result.suggested_folder,
                suggested_filename=new_filename,
                doc_type=result.doc_type,
                issuer=result.issuer,
                doc_date=doc_date_display,
                confidence=result.confidence,
                reason=result.reason,
            )
        )
    return rows


def write_proposal_csv(root: Path, rows: list[ProposalRow], when: datetime | None = None) -> Path:
    when = when or datetime.now()
    proposal_dir = config.folder_path(root, config.PROPOSALS)
    proposal_dir.mkdir(parents=True, exist_ok=True)
    # 同一秒内に複数回 propose が走っても、既存の変更案（社長が承認記入済みの
    # 可能性がある）を上書きしない（絶対原則2）。衝突時は _2, _3 ... を付与。
    path = unique_destination(proposal_dir, f"変更案_{when.strftime('%Y%m%d_%H%M%S')}.csv")
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row.to_csv_row())

    logger.log_operation(
        root,
        logger.OP_PROPOSAL_CREATE,
        "",
        str(path),
        logger.RESULT_OK,
        detail=f"{len(rows)}件の変更案を作成",
        when=when,
    )
    return path


def generate_proposal_csv(root: Path, when: datetime | None = None) -> tuple[Path, list[ProposalRow]]:
    """受信箱を分類し `_変更案/変更案_YYYYMMDD_HHMMSS.csv` を書き出す。"""
    rows = build_proposal_rows(root)
    path = write_proposal_csv(root, rows, when=when)
    return path, rows
