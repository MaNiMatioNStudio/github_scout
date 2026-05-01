"""WindowCheckpoint — 30分窓単位のチェックポイント管理。

完了した時間窓ラベルをJSONファイルに保存し、
バッチ再実行時にスキップできるようにする。
アトミック書き込みで破損リスクなし。
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class WindowCheckpoint:
    """
    Usage:
        cp = WindowCheckpoint(Path("output/wcheckpoint_2026-03-24_2026-04-17_w30.json"))
        if cp.is_done("2026-03-24T00:00"):
            continue
        cp.mark_done("2026-03-24T00:00")
    """

    def __init__(self, path: Path):
        self.path = path
        self._done: set = set()
        if path.exists():
            self._load()

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self._done = set(data.get("completed_windows", []))
        logger.info("WindowCheckpoint loaded: %d windows already done from %s",
                    len(self._done), self.path.name)

    def is_done(self, label: str) -> bool:
        return label in self._done

    def mark_done(self, label: str) -> None:
        self._done.add(label)
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"completed_windows": sorted(self._done)}, f, indent=2)
            tmp.replace(self.path)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise

    def count(self) -> int:
        return len(self._done)
