"""State files: append-only JSONL logs plus small JSON files (dedup sets)."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .timeutil import iso, now_utc


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Store:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "sentiment").mkdir(exist_ok=True)

    def path(self, name: str) -> Path:
        return self.dir / name

    def append(self, name: str, record: dict, prefix: str = "e") -> dict:
        """Append one record. Existing lines are never edited."""
        rec = {"id": new_id(prefix), "ts": iso(now_utc()), **record}
        with self.path(name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return rec

    def read(self, name: str) -> list[dict]:
        p = self.path(name)
        if not p.exists():
            return []
        out = []
        with p.open(encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{p}:{n}: corrupt line ({e})") from e
        return out

    def load_json(self, name: str, default: Any) -> Any:
        p = self.path(name)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def save_json(self, name: str, obj: Any) -> None:
        p = self.path(name)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{p.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str, sort_keys=True)
        os.replace(tmp, p)
