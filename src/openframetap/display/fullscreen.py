"""Fullscreen policy kept in the application pipeline, never GNOME settings."""

from __future__ import annotations


def sink_properties(*, fullscreen: bool, sync: bool) -> tuple[str, ...]:
    values = [f"sync={'true' if sync else 'false'}"]
    if fullscreen:
        values.append("fullscreen=true")
    return tuple(values)
