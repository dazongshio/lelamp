from __future__ import annotations


class ProjectionCatalogMixin:
    def projection_cards(self) -> list[dict[str, object]]:
        cards = []
        if not self.runtime.config.projection_dir.exists():
            return cards
        for path in sorted(self.runtime.config.projection_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)[:12]:
            text = path.read_text(encoding="utf-8", errors="replace")
            title = text.splitlines()[0].lstrip("# ").strip() if text.splitlines() else path.stem
            mode = "status"
            for line in text.splitlines():
                if line.lower().startswith("mode:"):
                    mode = line.split(":", 1)[1].strip()
                    break
            cards.append(
                {
                    "id": path.stem,
                    "title": title,
                    "subtitle": first_nonempty_line(text.splitlines()[2:]) or mode,
                    "mode": mode,
                    "accent": "green" if "success" in text.lower() or "ready" in text.lower() else "blue",
                    "created_at": time.strftime("%H:%M:%S", time.localtime(path.stat().st_mtime)),
                    "resolution": "1920 × 1080",
                    "path": str(path),
                    "html": markdown_to_html(text),
                }
            )
        return cards

