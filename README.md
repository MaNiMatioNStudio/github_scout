# GitHub Startup Scout

GitHubの新規リポジトリから「スタートアップ候補」を絞り込むバッチツール。

## 仕組み

```
GitHub Search API
      ↓ (5000件程度)
  Layer1 Filter        ← メタデータのみ (age / stars / language / keywords)
      ↓ (通過分のみ API追加取得)
  Layer2 Filter        ← README / ファイル構成 / キーワードスコアリング
      ↓
  filtered_repos.jsonl / .csv
```

## セットアップ

```bash
cd ~/github_scout
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env に GITHUB_TOKEN を記入
```

## 実行

```bash
# 直近7日間（デフォルト）
python main.py

# 日付範囲を指定
python main.py --date-from 2026-04-07 --date-to 2026-04-14

# フェッチ上限を変更（GitHub Search APIの上限は1000）
python main.py --max-results 1000

# 通過したrepoだけ保存
python main.py --passed-only

# 設定ファイルを変更
python main.py --config config/filters.yaml

# 出力先を変更
python main.py --output-dir /path/to/output
```

## 出力

`output/filtered_repos_YYYY-MM-DD_YYYY-MM-DD.jsonl` と `.csv` に保存される。

### JSONLサンプル

```json
{
  "name": "saas-starter",
  "owner": "alice",
  "url": "https://github.com/alice/saas-starter",
  "created_at": "2026-04-12T10:00:00Z",
  "language": "TypeScript",
  "stars": 3,
  "description": "A SaaS platform with dashboard and billing",
  "layer1_pass": true,
  "layer1_reasons": [
    "PASS: age 2d within 7d",
    "PASS: stars 3 in [0, 20]",
    "PASS: language 'TypeScript'",
    "PASS: no exclude keywords in name/description"
  ],
  "layer2_pass": true,
  "layer2_reasons": [
    "PASS: README found",
    "PASS: required files found: ['package.json']",
    "PASS: signal files found: ['src']",
    "PASS: positive keywords: ['platform', 'dashboard']",
    "PASS: score 3.50 >= threshold 2.0"
  ],
  "score": 3.5
}
```

## フィルタ設定

`config/filters.yaml` を編集することで全条件を変更できます。Pythonコードの変更は不要です。

| 設定キー | 説明 |
|---|---|
| `layer1.max_age_days` | 作成日からの最大日数 |
| `layer1.min_stars` / `max_stars` | Star数の範囲 |
| `layer1.allowed_languages` | 許可言語リスト（空=全言語許可） |
| `layer1.exclude_keywords` | name/descriptionに含まれたら除外 |
| `layer2.required_files` | あると加点されるファイル名（+0.5/個） |
| `layer2.signal_files` | シグナルになるファイル/ディレクトリ（+0.3/個） |
| `layer2.positive_keywords` | README/descriptionにあると加点（+0.5/個） |
| `layer2.negative_keywords` | あると減点（-1.0/個） |
| `layer2.min_score` | Layer2を通過するための最低スコア |

## テスト

```bash
pytest tests/ -v
```

## Layer3追加方法

`github_scout/filters/` に `layer3.py` を追加し、`pipeline.py` の `run_pipeline()` 末尾に呼び出しを追加するだけです。
`RepoRecord` に `layer3_pass` / `layer3_reasons` フィールドを追加してください。

## レート制限について

- 認証あり（GITHUB_TOKEN設定済み）: 検索API 30回/分、コンテンツAPI 5000回/時間
- 認証なし: 検索API 10回/分
- Layer2で1リポジトリにつき最大2リクエスト（README + ファイル一覧）消費します
