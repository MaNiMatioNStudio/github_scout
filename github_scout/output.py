"""Write results to JSONL and CSV."""
import csv
import json
from pathlib import Path
from typing import IO

from .models import RepoRecord

_FIELDS = [
    "name", "owner", "url", "created_at", "language", "stars", "description", "homepage", "site_url",
    "layer1_pass", "layer1_reasons",
    "layer2_pass", "layer2_reasons", "score",
    "layer3_result", "layer3_score", "layer3_reasons",
    "layer5_pass", "layer5_reasons", "layer5_confidence",
    "layer5_lp_url", "layer5_company_name", "layer5_founder_name",
]


def to_dict(repo: RepoRecord) -> dict:
    return {
        "name": repo.name,
        "owner": repo.owner,
        "url": repo.url,
        "created_at": repo.created_at,
        "language": repo.language,
        "stars": repo.stars,
        "description": repo.description,
        "homepage": repo.homepage,
        "site_url": repo.site_url,
        "layer1_pass": repo.layer1_pass,
        "layer1_reasons": repo.layer1_reasons,
        "layer2_pass": repo.layer2_pass,
        "layer2_reasons": repo.layer2_reasons,
        "score": repo.score,
        "layer3_result": repo.layer3_result,
        "layer3_score": repo.layer3_score,
        "layer3_reasons": repo.layer3_reasons,
        "layer5_pass": repo.layer5_pass,
        "layer5_reasons": repo.layer5_reasons,
        "layer5_confidence": repo.layer5_confidence,
        "layer5_lp_url": repo.layer5_lp_url,
        "layer5_company_name": repo.layer5_company_name,
        "layer5_founder_name": repo.layer5_founder_name,
    }


def append_jsonl(repo: "RepoRecord", f: IO) -> None:
    """Append a single repo record to an already-open file handle (streaming write)."""
    f.write(json.dumps(to_dict(repo), ensure_ascii=False) + "\n")
    f.flush()


def write_csv_from_jsonl(jsonl_path: Path, csv_path: Path) -> None:
    """Generate a CSV from an existing JSONL file (used after streaming run)."""
    with open(jsonl_path, encoding="utf-8") as jf, \
         open(csv_path, "w", newline="", encoding="utf-8") as cf:
        writer = csv.DictWriter(cf, fieldnames=_FIELDS)
        writer.writeheader()
        for line in jf:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            for key in ("layer1_reasons", "layer2_reasons", "layer3_reasons"):
                if isinstance(d.get(key), list):
                    d[key] = " | ".join(str(x) for x in d[key])
            writer.writerow({k: d.get(k, "") for k in _FIELDS})
