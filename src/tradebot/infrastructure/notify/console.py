from __future__ import annotations

from datetime import datetime

from rich.console import Console


class ConsoleNotifier:
    """Prints messages to the terminal (NotifierPort)."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    async def send(self, text: str) -> None:
        self.console.print(f"[dim]{datetime.now():%H:%M:%S}[/] {text}")
