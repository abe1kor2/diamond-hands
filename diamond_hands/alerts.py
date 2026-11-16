"""Alerts: one idea each, keyed so each fires once."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import DISCLAIMER
from .store import Store
from .timeutil import fmt_time, iso, now_utc

SENT = "alerts_sent.json"


@dataclass
class Alert:
    ticker: str
    rule: str
    level: str
    day: date
    headline: str
    details: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.ticker}|{self.rule}|{self.level}|{self.day.isoformat()}"

    def text(self) -> str:
        out = [f"[{self.ticker}] {self.headline}", *self.details]
        if self.sources:
            out.append("Source: " + "; ".join(self.sources) + ".")
        out.append(DISCLAIMER)
        return "\n".join(out)


class Dispatcher:
    """Filters out alerts already sent, logs the new ones, and remembers them."""

    def __init__(self, store: Store, logs_dir: Path, tz: str):
        self.store = store
        self.logs_dir = Path(logs_dir)
        self.tz = tz
        self.sent: dict[str, str] = store.load_json(SENT, {})

    def fresh(self, alerts: list[Alert]) -> list[Alert]:
        seen: set[str] = set()
        out = []
        for a in alerts:
            if a.key in self.sent or a.key in seen:
                continue
            seen.add(a.key)
            out.append(a)
        return out

    def send(self, alerts: list[Alert], dry_run: bool = False) -> list[Alert]:
        new = self.fresh(alerts)
        if not new or dry_run:
            return new
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_utc()
        with (self.logs_dir / "alerts.log").open("a", encoding="utf-8") as f:
            for a in new:
                f.write(f"--- {fmt_time(stamp, self.tz)} · {a.key}\n{a.text()}\n\n")
                self.sent[a.key] = iso(stamp)
        self.store.save_json(SENT, self.sent)
        return new
