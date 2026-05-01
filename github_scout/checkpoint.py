"""UnifiedCheckpoint — append-only checkpoint with last_completed_stage tracking.

設計原則:
  - write: 1件ずつ追記（クラッシュしても既存データは安全）
  - merge: tempfile → os.replace でアトミックに上書き（破損リスクなし）
  - resume: last_completed_stage で「どこまで終わったか」を管理
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ステージの実行順序。is_done() の比較に使う。
STAGE_ORDER = ["L1", "L2", "L3", "L4", "L5a", "L5b"]


class UnifiedCheckpoint:
    """
    Usage:
        cp = UnifiedCheckpoint(Path("output/checkpoint_run.jsonl"))
        cp.save(url, "L3", {"layer3_pass": True, "layer3_confidence": 0.8, ...})
        if cp.is_done(url, "L3"): ...
        cp.merge_into_jsonl(source_path)
    """

    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}   # url → 最新レコード
        if path.exists():
            self._load()

    # ── 読み込み ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """既存チェックポイントを読み込む。同一URLの場合は後の行が優先。"""
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    url = rec.get("url")
                    if url:
                        # 後の行（より進んだステージ）が優先
                        self._data[url] = rec
                except Exception:
                    pass
        logger.info("Checkpoint loaded: %d entries from %s", len(self._data), self.path.name)

    # ── 書き込み ─────────────────────────────────────────────────────────────

    def save(self, url: str, stage: str, data: dict) -> None:
        """1件をチェックポイントに追記する（crash-safe）。"""
        record = {
            "url": url,
            "last_completed_stage": stage,
            "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **data,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
        self._data[url] = record

    # ── 参照 ─────────────────────────────────────────────────────────────────

    def is_done(self, url: str, stage: str) -> bool:
        """指定ステージ以上まで完了しているか。"""
        rec = self._data.get(url)
        if not rec:
            return False
        completed = rec.get("last_completed_stage", "")
        try:
            return STAGE_ORDER.index(completed) >= STAGE_ORDER.index(stage)
        except ValueError:
            return False

    def all_urls(self) -> set:
        """チェックポイントに存在する全URLのセット。"""
        return set(self._data.keys())

    def count(self) -> int:
        return len(self._data)

    # ── マージ ────────────────────────────────────────────────────────────────

    def merge_into_jsonl(self, source_path: Path) -> None:
        """
        チェックポイントのデータをメインJSONLにアトミックにマージする。

        tempfile書き込み → os.replace() で破損リスクを排除。
        """
        if not source_path.exists():
            logger.warning("merge_into_jsonl: source not found: %s", source_path)
            return

        records = []
        actually_updated = 0
        with open(source_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                url = rec.get("url", "")
                if url in self._data:
                    # チェックポイントのフィールドで上書き（urlとlast_completed_stageを除く）
                    update = {
                        k: v for k, v in self._data[url].items()
                        if k not in ("url", "last_completed_stage")
                    }
                    rec.update(update)
                    actually_updated += 1
                records.append(rec)

        # アトミック書き込み
        tmp_path = source_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(str(tmp_path), str(source_path))
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        logger.info(
            "Checkpoint merged into %s (%d records written, %d updated from checkpoint, %d checkpoint-only skipped)",
            source_path.name, len(records), actually_updated, len(self._data) - actually_updated,
        )
