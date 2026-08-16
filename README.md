# home-document-organizer

家庭書類自動整理システム（iPhoneでスキャンした税金・銀行・保険・身分証などの書類を、受信箱フォルダに入れるだけで自動的に分類・リネーム・整理するプロトタイプ）。

- 要件設計書: [`REQUIREMENTS.md`](./REQUIREMENTS.md)
- 実装範囲・原則（元ファイル削除禁止／上書き禁止／全操作ログ化／実行は承認後のみ）は `REQUIREMENTS.md` の「2. 絶対原則」を参照。
- 実装言語: Python（`REQUIREMENTS.md` 9章）

## セットアップ

```bash
brew install tesseract tesseract-lang   # OCR本体＋日本語データ（iPhoneスキャンPDFはテキスト層が無いため必須）
python3 -m venv .venv
.venv/bin/pip install -e ".[ocr,dev]"
```

## CLI

```bash
.venv/bin/python -m home_doc_organizer init          # 第1段階: フォルダ構成初期化
.venv/bin/python -m home_doc_organizer scan-inbox     # 第1段階: 受信箱→元ファイル保管へ複製
.venv/bin/python -m home_doc_organizer propose        # 第2段階: 分類→変更案.csv作成
.venv/bin/python -m home_doc_organizer apply          # 第3・4段階: 承認済み行のみ実行
.venv/bin/python -m home_doc_organizer cleanup-inbox  # 保管済み受信箱ファイルの削除（1件ずつ対話確認）
```

## 学習機能

キーワード辞書に無い書類（例: クレジットカード等）は初回`_要確認`に振り分けられるが、
CEOが変更案CSVの「提案カテゴリ／書類種別(判定)／発行元(判定)」を訂正してから承認すると、
その内容が `_学習データ/learned_rules.json` に記憶される。以後、同じ発行元名が本文に
含まれる書類は自動的に高確信度で分類される（LLM不使用・トークンコスト0）。詳細は
`src/home_doc_organizer/learning.py` を参照。

## 自動化（launchd）

`scripts/home-doc-scan.sh`（親リポ側）が受信箱を定期監視し、新規ファイルがあれば
scan-inbox→propose→Telegram/LINE通知を自動実行する（承認・実際の振り分けは含まない）。
ジョブ台帳: `.company/jobs/registry.yml` の `home-doc-scan`。有効化手順は
`scripts/launchd/com.rebell.home-doc-scan.plist` のコメント参照（CEOの最終GO待ち＝未ロード）。
