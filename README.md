# GitHub Startup Scout

GitHubに毎日大量に作られる新規リポジトリの中から、スタートアップ・プロダクト候補を自動で絞り込むバッチCLIツール。シード期の投資家がソーシング効率化に使うことを想定している。

AIコストゼロ。全フィルタがルールベースで動作する（L5bのみオプションでClaude Haiku使用）。

---

## 主な機能

- **L1フィルタ** — メタデータのみで高速スクリーニング（作成日・Star数・言語・除外キーワード）
- **L2フィルタ** — READMEやファイル構成をスコアリング（プロダクトらしさを点数化）
- **L3フィルタ** — Japan affinityを判定（ひらがな/カタカナ、.jpドメイン、オーナーのlocationなど）
- **L5フィルタ** — LP/サービスURLの発見・日本向け判定・企業名/代表者名の抽出
- **時間窓モード** — 30分単位でクエリを分割し、GitHub Search APIの1000件上限を回避
- **チェックポイント再開** — ネットワーク断や中断後も途中から自動再開
- **Streamlit UI** — 取得済み結果をブラウザで確認・フィルタリング

```
GitHub Search API
      ↓
  L1フィルタ  ← メタデータのみ (stars / age / language / keywords)
      ↓
  L2フィルタ  ← README / ファイル構成 / スコアリング (score ≥ 3.0)
      ↓
  L3フィルタ  ← Japan affinityスコア (score ≥ 3 → pass)
      ↓
  output/filtered_repos_*.jsonl / .csv
```

---

## ディレクトリ構成

```
github_scout/
├── main.py                  # CLIエントリポイント
├── run_with_retry.sh        # 自動再起動ループスクリプト
├── run_l5.py                # L5 (LP URL発見・企業情報抽出) ランナー
├── run_l5b_haiku.py         # L5b (Claude Haiku による企業名抽出) ランナー
├── viewer.py                # Streamlit UIダッシュボード
├── requirements.txt
├── .env.example
├── config/
│   ├── filters.yaml         # 全フィルタ条件の設定ファイル
│   └── preferences.yaml     # UI表示の設定
├── github_scout/            # パッケージ本体
│   ├── adapters/
│   │   └── github_api.py    # GitHub Search / Contents API クライアント
│   ├── filters/
│   │   ├── layer1.py
│   │   ├── layer2.py
│   │   ├── layer3.py
│   │   └── layer5.py
│   ├── models.py            # RepoRecord データモデル
│   ├── pipeline.py          # L1→L2→L3 パイプライン
│   ├── output.py            # JSONL / CSV 出力
│   ├── checkpoint.py        # 日単位チェックポイント
│   └── window_checkpoint.py # 時間窓チェックポイント
├── output/                  # 出力ファイル (gitignore推奨)
└── tests/
```

---

## セットアップ

```bash
git clone <このリポジトリ>
cd github_scout

# 仮想環境の作成と依存パッケージのインストール
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 環境変数ファイルの作成
cp .env.example .env
# .env を編集して必要な値を記入（「環境変数」セクション参照）
```

---

## 環境変数

`.env.example` をコピーして `.env` を作成し、以下の値を設定する。

| 変数名 | 必須 | 説明 |
|---|---|---|
| `GITHUB_TOKEN` | 推奨 | GitHub Personal Access Token。未設定でも動くが、レート制限が厳しくなる（検索API: 10回/分 → 30回/分） |
| `ANTHROPIC_API_KEY` | L5bのみ | Claude Haiku で企業名・代表者名を抽出する場合のみ必要。L5aまでなら不要 |

`.env.example`:
```
GITHUB_TOKEN=
ANTHROPIC_API_KEY=
```

> `.env` ファイルは絶対にコミットしないこと。

---

## 実行方法

### 基本実行（直近7日間）

```bash
python main.py
```

### 日付範囲を指定

```bash
python main.py --date-from 2026-04-07 --date-to 2026-04-14
```

### 時間窓モード（長期間取得時の推奨設定）

GitHub Search APIは1クエリあたり最大1000件しか返せない。長期間や多件数を取得する場合は `--window-minutes 30` で30分単位に分割する。

```bash
python main.py --date-from 2026-03-24 --date-to 2026-04-17 --window-minutes 30
```

### 自動再起動ループ（バックグラウンド実行）

ネットワーク断やクラッシュで中断されても、チェックポイントから自動再開する。

```bash
# フォアグラウンド実行
bash run_with_retry.sh

# バックグラウンド実行
nohup bash run_with_retry.sh > /dev/null 2>&1 &
```

> `run_with_retry.sh` 内の日付・ログパスは用途に合わせて直接編集する。

### 進捗確認

```bash
# ログをリアルタイム確認
tail -f output/run_2026-03-24_2026-04-17_w30.log

# 時間窓チェックポイントの進捗を確認
cat output/wcheckpoint_2026-03-24_2026-04-17_w30.json | python3 -c "
import json,sys; d=json.load(sys.stdin); w=d['completed_windows']
print(f'完了: {len(w)}/1200窓 ({len(w)/1200*100:.1f}%)')
print(f'最後: {w[-1]}')
"
```

### L5 実行（LP URL発見・企業情報抽出）

```bash
# L5a: ルールベース（$0）
python run_l5.py --source output/filtered_repos_2026-03-17_2026-03-18.jsonl

# 中断後の再開
python run_l5.py --source output/filtered_repos_2026-03-17_2026-03-18.jsonl --resume

# L5b: Claude Haikuで企業名・代表者名を抽出（ANTHROPIC_API_KEY必要）
python run_l5b_haiku.py --source output/filtered_repos_2026-03-17_2026-03-18.jsonl
```

### Streamlit UI の起動

```bash
.venv/bin/streamlit run viewer.py
# → http://localhost:8501 で確認
```

### 主要オプション一覧

| オプション | デフォルト | 説明 |
|---|---|---|
| `--date-from` | 7日前 | 取得開始日 (YYYY-MM-DD) |
| `--date-to` | 今日 | 取得終了日 (YYYY-MM-DD) |
| `--window-minutes` | 0 | 時間窓の分数 (0=日単位モード) |
| `--max-results` | 5000 | 最大取得件数（日単位モード） |
| `--config` | config/filters.yaml | フィルタ設定ファイルパス |
| `--output-dir` | output | 出力ディレクトリ |
| `--passed-only` | false | L1〜L3を全通過したrepoのみ出力 |

---

## フィルタ設定

フィルタ条件はすべて `config/filters.yaml` で管理する。Pythonコードの変更は不要。

| 設定キー | 説明 |
|---|---|
| `layer1.max_age_days` | 作成日からの最大日数 |
| `layer1.min_stars` / `max_stars` | Star数の範囲（両端含む） |
| `layer1.allowed_languages` | 許可言語リスト（空リスト=全言語許可） |
| `layer1.exclude_keywords` | name/descriptionに含まれたら除外するキーワード |
| `layer2.required_files` | 存在すると加点されるファイル名（+0.5/個） |
| `layer2.signal_files` | シグナルになるファイル/ディレクトリ名（+0.3/個） |
| `layer2.positive_keywords` | README/descriptionにあると加点されるキーワード（+0.5/個） |
| `layer2.negative_keywords` | あると減点されるキーワード（-1.0/個） |
| `layer2.min_score` | L2を通過するための最低スコア（デフォルト: 3.0） |
| `layer3.pass_threshold` | Japan affinityスコアがこの値以上でpass（デフォルト: 3） |
| `layer3.fetch_owner_profile` | オーナーのGitHubプロフィールを取得するか（Tier2シグナル） |

---

## 出力フォーマット

`output/filtered_repos_YYYY-MM-DD_YYYY-MM-DD.jsonl` と `.csv` に保存される。

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
  "layer2_pass": true,
  "layer2_score": 3.5,
  "layer3_result": "pass"
}
```

---

## テスト

```bash
pytest tests/ -v
```

---

## 注意事項

- **GitHub Token**: Personal Access Token は `repo` スコープ不要。公開情報のみ取得するので `public_repo` または スコープなしで動作する
- **レート制限**: 認証あり → 検索API 30回/分、コンテンツAPI 5000回/時間。レートリミットに引っかかると自動で待機し、解除後に再開する
- **L2のAPI消費**: L1通過repoに対しREADME取得・ファイル一覧取得で最大2リクエスト発生する
- **`.env` ファイル**: 絶対にバージョン管理に含めないこと。`output/` ディレクトリも同様にgitignore推奨
- **L5b コスト**: Claude Haikuを使う `run_l5b_haiku.py` は1件あたり最大 $0.006。事前に予算上限を確認すること
