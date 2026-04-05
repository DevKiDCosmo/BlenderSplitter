"""View model projection from runtime status to UI panel data."""

from __future__ import annotations

from dataclasses import dataclass

from src.runtime.facade import PanelStatus


@dataclass
class UiPanelModel:
    headline: str
    role: str
    workers_online: int
    render_progress: str

    @classmethod
    def from_status(cls, status: PanelStatus) -> "UiPanelModel":
        if status.render_total > 0:
            progress = f"{status.render_done}/{status.render_total}"
        else:
            progress = "0/0"
        return cls(
            headline=status.status_line,
            role=status.role,
            workers_online=status.workers_online,
            render_progress=progress,
        )
