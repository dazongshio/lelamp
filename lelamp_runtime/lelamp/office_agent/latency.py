from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator

from .audit import AuditLogger


@dataclass
class LatencyProbe:
    label: str
    audit: AuditLogger | None = None
    enabled: bool = True
    _start: float = field(default_factory=perf_counter)
    _items: list[tuple[str, float]] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        start = perf_counter()
        try:
            yield
        finally:
            self.add(name, perf_counter() - start)

    def add(self, name: str, seconds: float) -> None:
        if self.enabled:
            self._items.append((name, seconds))

    def total_seconds(self) -> float:
        return perf_counter() - self._start

    def as_dict(self) -> dict[str, float | str]:
        data: dict[str, float | str] = {"label": self.label, "total": round(self.total_seconds(), 3)}
        for name, seconds in self._items:
            data[name] = round(seconds, 3)
        return data

    def print_summary(self) -> None:
        if not self.enabled:
            return
        parts = [f"{name}={seconds:.2f}s" for name, seconds in self._items]
        parts.append(f"total={self.total_seconds():.2f}s")
        print(f"[latency] {self.label}: " + " ".join(parts))
        if self.audit is not None:
            self.audit.record("assistant.latency", details=self.as_dict())
