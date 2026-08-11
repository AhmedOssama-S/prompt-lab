"""Append-only JSONL logging for Compare/Auto-Judge runs. See PROMPT_LAB_PLAN.md section 6.6."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

RUNS_FILE = Path(__file__).resolve().parent / "runs.jsonl"


def append_run(record: Dict[str, Any]) -> None:
    """Appends one JSON object per line. Adds a timestamp if the caller didn't set one."""
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
