"""Load/save the working agenda for a given date. The server is otherwise
stateless: every tool call loads by date, mutates the in-memory
AgendaState, saves by date. Nothing touches disk until render_pdf is
explicitly asked to keep a copy."""
from __future__ import annotations

import datetime

from magenda.agenda_state import AgendaState
from magenda.errors import MagendaError

__all__ = ["create", "load", "save", "agenda_exists"]

# Working agendas, keyed by date. Process-lifetime only -- an agenda that
# hasn't been rendered/exported is lost if the server restarts.
_STORE: dict[datetime.date, AgendaState] = {}


def create(date: datetime.date) -> AgendaState:
    state = AgendaState.create(date)
    _STORE[date] = state
    return state


def load(date: datetime.date) -> AgendaState:
    state = _STORE.get(date)
    if state is None:
        raise MagendaError(f"no agenda exists for {date.isoformat()} yet; call create_agenda first")
    return state


def save(date: datetime.date, state: AgendaState) -> None:
    _STORE[date] = state


def agenda_exists(date: datetime.date) -> bool:
    return date in _STORE
