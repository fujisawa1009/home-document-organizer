"""第2段階: PDF/画像からのテキスト抽出（REQUIREMENTS.md 7-2）。

- PDF: pypdf でテキスト抽出。
- 画像(jpg/jpeg/png/heic): OCR用の依存（pillow / pillow-heif / pytesseract）が
  入っていればOCRを試みる。未導入の環境でも動くよう、失敗時は空文字を返し
  呼び出し側の分類ロジックが「確信度:低→_要確認」に倒す。
"""

from __future__ import annotations

from pathlib import Path

try:
    from pypdf import PdfReader

    HAS_PDF = True
except ImportError:  # pragma: no cover - pypdf は必須依存だが念のため
    HAS_PDF = False

try:
    import pytesseract
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    HAS_OCR = True
except ImportError:
    HAS_OCR = False


def extract_pdf_text(path: Path) -> str:
    if not HAS_PDF:
        return ""
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # パスワード保護PDF: 8章「読み取れない場合→_要確認」に倒す。
            return ""
        texts = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(texts).strip()
    except Exception:
        # 破損PDF等。処理全体は止めず空文字で呼び出し元へ返す。
        return ""


def extract_image_text(path: Path) -> str:
    if not HAS_OCR:
        return ""
    try:
        image = Image.open(path)
        return pytesseract.image_to_string(image, lang="jpn+eng").strip()
    except Exception:
        return ""


def extract_text(path: Path) -> str:
    """拡張子に応じてテキストを抽出する。未対応拡張子は空文字。"""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf_text(path)
    if ext in {".jpg", ".jpeg", ".png", ".heic"}:
        return extract_image_text(path)
    return ""
