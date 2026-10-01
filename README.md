# home-document-organizer

家庭書類自動整理システム（iPhoneでスキャンした税金・銀行・保険・身分証などの書類を、受信箱フォルダに入れるだけで自動的に分類・リネーム・整理するプロトタイプ）。

- 要件設計書: [`REQUIREMENTS.md`](./REQUIREMENTS.md)
- 実装範囲・原則（元ファイル削除禁止／上書き禁止／全操作ログ化）は `REQUIREMENTS.md` の「2. 絶対原則」を参照。
  **原則4（承認後のみ実行）は`auto-run`パイプラインに限り改訂済み**（2章の改訂注記参照）。
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
.venv/bin/python -m home_doc_organizer propose        # 第2段階: 分類→変更案.csv作成（承認は空欄）
.venv/bin/python -m home_doc_organizer apply          # 第3・4段階: 承認済み行のみ実行（手動2段階フロー用）
.venv/bin/python -m home_doc_organizer cleanup-inbox  # 保管済み受信箱ファイルの削除（1件ずつ対話確認）
.venv/bin/python -m home_doc_organizer auto-run       # scan-inbox+propose(全行自動承認)+applyを一括無人実行
```

`00_受信箱`直下に加え、1階層下のサブフォルダの中身もスキャン対象（フォルダ名は分類ヒントに使われる。
例: `00_受信箱/免許証/写真.jpg`）。ファイル名（拡張子除く）も同様にヒントとして使われる。

## 学習機能

キーワード辞書に無い書類（例: クレジットカード等）は初回`_要確認`に振り分けられるが、
分類結果（提案カテゴリ／書類種別(判定)／発行元(判定)）を訂正してから承認すると、
その内容が `_学習データ/learned_rules.json` に記憶される。以後、同じ発行元名が
本文・ファイル名・サブフォルダ名に含まれる書類は自動的に高確信度で分類される
（LLM不使用・トークンコスト0）。詳細は `src/home_doc_organizer/learning.py` を参照。

## 在籍期間の設定（給与明細の発行元フォールバック）

スキャンした給与明細はテキスト層が無くOCRも1文字も返さないことがある（実物10件中9件）。
その場合、発行元（勤務先）は本文からは読めないため、**支給年月が在籍期間内であれば
その期間の勤務先を推定で埋める**（`classify._issuer_from_employment`）。推定値は読み取り値と
区別して記録される（変更案CSVの `発行元の根拠` 列／備考の `issuer_source=fallback`／
操作ログの「発行元推定」）。在籍期間外・期間が重複して一意でない・支給年月が読めない場合は
`不明` のまま残す。

転職したら `_学習データ/employment_periods.json` を置いて既定値を差し替える
（既定値とマージせず完全に置き換わる。値が1件でも不正なら表全体を無効化＝推定しない）。

```json
[{"employer": "株式会社◯◯", "start": "202410", "end": "202603"},
 {"employer": "株式会社△△", "start": "202604", "verified_through": "202609"}]
```

`end` を省略／null にすると「現在も在籍」。在籍中の期間は
`verified_through`（在籍を一次資料で確認できている最終月・省略時は `start`）＋12か月までしか
推定に使わない＝それ以降は `不明` に戻るので、設定の更新が必要だと分かる。
詳細は `REQUIREMENTS.md` の 3-2 節。

## 自動化（launchd・稼働中）

`scripts/home-doc-scan.sh`（親リポ側）が受信箱を30分毎に監視し、新規ファイルがあれば
`auto-run`（確信度に関わらず自動でコピー・受信箱の元ファイル自動削除まで実行）を実行し、
結果をTelegram/LINEへ事後通知する（CEO指示2026-08-16：外出先からの自動振り分け対応）。
誤分類時は`_元ファイル保管`の原本から復旧・再訂正できる。
ジョブ台帳: `.company/jobs/registry.yml` の `home-doc-scan`。
