"""The map, drawn: a read-only web view over a world.

Two ways to get it. `orrery map` serves the page from a small standard-library HTTP
server that answers every question live from the engine. `orrery map --export` writes one
self-contained HTML file with the answers precomputed, for sharing a map with someone who
does not have Python — or a laptop that can reach the estate.

Neither one writes to the world. The page asks the same questions the CLI does, and gets
the same answers, because both go through `orrery.web.api`.
"""
from .api import (
    EXPORT_LIMIT,
    blast_payload,
    simulate_payload,
    snapshot_payload,
    world_payload,
)
from .server import export_html, serve

__all__ = [
    "EXPORT_LIMIT",
    "blast_payload",
    "export_html",
    "serve",
    "simulate_payload",
    "snapshot_payload",
    "world_payload",
]
