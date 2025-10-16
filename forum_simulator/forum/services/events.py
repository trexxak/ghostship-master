from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from django.db.models import Q
from django.utils import timezone

from forum.models import OracleDraw, TickLog


@dataclass
class SupernaturalEvent:
    tick: int
    timestamp: timezone.datetime
    omen: bool
    seance: bool
    energy: int
    energy_prime: int
    notes: List[str]
    omen_details: dict[str, object] | None = None
    seance_details: dict[str, object] | None = None

    @property
    def label(self) -> str:
        if self.omen and self.seance:
            return "Omen + Seance"
        if self.omen:
            return "Omen"
        if self.seance:
            return "Seance"
        return " anomaly"


def recent_supernatural_events(limit: int = 6) -> List[SupernaturalEvent]:
    draws = (
        OracleDraw.objects.filter(
            Q(alloc__specials__omen=True) | Q(alloc__specials__seance=True)
        )
        .order_by("-tick_number")[:limit]
    )
    tick_map = {
        log.tick_number: log
        for log in TickLog.objects.filter(
            tick_number__in=[draw.tick_number for draw in draws]
        )
    }
    events: List[SupernaturalEvent] = []
    for draw in draws:
        specials = (draw.alloc or {}).get("specials") or {}
        notes = list((draw.alloc or {}).get("notes") or [])
        omen_details = specials.get("omen_details")
        if isinstance(omen_details, dict):
            omen_details = dict(omen_details)
        else:
            omen_details = None
        seance_details = specials.get("seance_details")
        if isinstance(seance_details, dict):
            seance_details = dict(seance_details)
        else:
            seance_details = None
        log = tick_map.get(draw.tick_number)
        if log:
            for entry in log.events or []:
                if entry.get("type") == "specials":
                    extra_notes = entry.get("notes") or []
                    if isinstance(extra_notes, list):
                        notes.extend(extra_notes)
        events.append(
            SupernaturalEvent(
                tick=draw.tick_number,
                timestamp=draw.timestamp,
                omen=bool(specials.get("omen")),
                seance=bool(specials.get("seance")),
                energy=draw.energy,
                energy_prime=draw.energy_prime,
                notes=notes,
                omen_details=omen_details,
                seance_details=seance_details,
            )
        )
    return events


def banner_payload(events: Iterable[SupernaturalEvent]) -> Optional[dict[str, object]]:
    events = list(events)
    if not events:
        return None
    latest = events[0]
    if not latest.omen and not latest.seance:
        return None
    seance_label = (latest.seance_details or {}).get("label") if latest.seance_details else None
    seance_desc = (latest.seance_details or {}).get("description") if latest.seance_details else None
    omen_label = (latest.omen_details or {}).get("label") if latest.omen_details else None
    omen_desc = (latest.omen_details or {}).get("description") if latest.omen_details else None

    headline = "Omen ripples detected"
    tone = "omen"
    message = omen_desc or "Oracle deck whispers of interference. Stay alert for anomalies."
    if latest.seance and latest.omen:
        title = seance_label or "Seance surge"
        anomaly = omen_label or "Omen echo"
        headline = f"{title} + {anomaly}"
        tone = "seance"
        message = (seance_desc or "Veil is thin and responses will spike.") + " " + (omen_desc or "Moderators report oddities across the deck.")
    elif latest.seance:
        headline = seance_label or "Seance surge underway"
        tone = "seance"
        message = seance_desc or "Energy overflow. Threads will blaze. Keep trexxak supplied with data."
    elif latest.omen:
        headline = omen_label or headline
        message = omen_desc or message
    return {
        "headline": headline,
        "tone": tone,
        "message": message,
        "tick": latest.tick,
    }
