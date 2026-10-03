"""Request/task-local routing, with no process-wide account switching."""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock


current_workspace = ContextVar('current_user_workspace', default=None)


@dataclass
class UserWorkspace:
    user_id: int | None
    broker: object
    engine: object
    client: object
    quote_stream: object
    snapshot_at: object = None
    source_label: str | None = None
    selected_strategy: object = None
    mode: str = 'LIVE_COPY'
    sessions: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def risk(self):
        return self.broker.risk_manager


class UserWorkspaces:
    def __init__(self, legacy, factory):
        self.legacy = legacy
        self.factory = factory
        self.items = {}
        self.lock = RLock()

    def get(self, user_id):
        with self.lock:
            if user_id not in self.items:
                self.items[user_id] = self.factory(user_id)
            return self.items[user_id]

    def current(self):
        return current_workspace.get() or self.legacy


class ScopedProxy:
    def __init__(self, workspaces, field):
        object.__setattr__(self, '_workspaces', workspaces)
        object.__setattr__(self, '_field', field)

    def __getattr__(self, name):
        return getattr(getattr(self._workspaces.current(), self._field), name)

    def __setattr__(self, name, value):
        setattr(getattr(self._workspaces.current(), self._field), name, value)
