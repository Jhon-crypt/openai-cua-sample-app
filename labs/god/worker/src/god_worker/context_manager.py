"""T4-style context helpers: elide bulky tool results, then summarize when still large."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextManager:
    """Tracks tool-result strings locally; produces stubs + running summary for prompts."""

    soft_chars: int = 24_000
    hard_chars: int = 48_000
    keep_recent: int = 6
    events: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def add(self, tool: str, result_text: str, *, bulky: bool = False) -> None:
        body = result_text
        if bulky and len(body) > 400:
            body = body[:200] + f"… [elided {len(result_text)} chars]"
        self.events.append({"tool": tool, "body": body, "bulky": bulky, "full_len": len(result_text)})
        self._compact()

    def _total_chars(self) -> int:
        return sum(len(e["body"]) for e in self.events) + len(self.summary)

    def _compact(self) -> None:
        if self._total_chars() < self.soft_chars:
            return
        # M1: elide bulky middle events (keep preamble-less: drop oldest bulky bodies)
        middle_end = max(0, len(self.events) - self.keep_recent)
        for event in self.events[:middle_end]:
            if event.get("bulky") and len(event["body"]) > 120:
                fl = event.get("full_len", len(event["body"]))
                event["body"] = f"[stub:{event['tool']} elided {fl} chars]"
        if self._total_chars() < self.hard_chars:
            return
        # M3: fold oldest middle into summary
        fold = self.events[: max(0, len(self.events) - self.keep_recent)]
        if not fold:
            return
        lines = [f"- {e['tool']}: {e['body'][:160]}" for e in fold[-12:]]
        chunk = "\n".join(lines)
        self.summary = (self.summary + "\n" + chunk).strip()[-4_000:]
        self.events = self.events[-self.keep_recent :]

    def prompt_block(self) -> str:
        parts: list[str] = []
        if self.summary.strip():
            parts.append("Running summary of earlier tool work:\n" + self.summary.strip())
        recent = self.events[-self.keep_recent :]
        if recent:
            parts.append(
                "Recent tool results:\n"
                + "\n".join(f"* {e['tool']}: {e['body'][:300]}" for e in recent)
            )
        return "\n\n".join(parts)
