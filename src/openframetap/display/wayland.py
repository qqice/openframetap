"""Wayland environment helpers."""

from __future__ import annotations

from openframetap.display.session import DisplaySession, discover_active_wayland_session


def resolve_wayland_environment() -> tuple[DisplaySession, dict[str, str]]:
    session = discover_active_wayland_session()
    return session, session.environment()
