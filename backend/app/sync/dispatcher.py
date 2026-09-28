import json
from collections.abc import Callable

from sqlalchemy.orm import Session

Handler = Callable[[Session, str, dict], None]


class CommandDispatcher:
    def __init__(self):
        self.handlers: dict[str, Handler] = {}

    def register(self, entity_type: str, handler: Handler):
        self.handlers[entity_type] = handler

    def dispatch(self, db: Session, entity_type: str, operation: str, payload: str):
        handler = self.handlers.get(entity_type)
        if not handler:
            raise ValueError(f"No synchronization handler for {entity_type}")
        handler(db, operation, json.loads(payload))

