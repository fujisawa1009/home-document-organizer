"""reportlabで実際にPDFを生成し、pypdf抽出→分類までを通しで検証する（モックなし）。

reportlab未導入環境（pip install -e .[dev] していない場合）ではスキップする。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

reportlab = pytest.importorskip("reportlab")
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from home_doc_organizer import config
from home_doc_organizer.classify import classify_file

pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))


def _make_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path))
    c.setFont("HeiseiKakuGo-W5", 14)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()


def test_real_pdf_tax_certificate_is_classified_correctly(tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(
        pdf_path,
        ["麹町税務署", "納税証明書", "令和7年8月16日交付"],
    )

    result = classify_file(pdf_path)

    assert result.category_key == "税金"
    assert result.doc_type == "納税証明書"
    assert result.suggested_folder == config.CATEGORY_TAX
