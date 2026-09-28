import json
from uuid import uuid4

from .dispatcher import CommandDispatcher
from .queue import QueueRepository
from .service import SyncService
from .handlers import register_handlers

queue_repository = QueueRepository()
dispatcher = CommandDispatcher()
register_handlers(dispatcher)
sync_service = SyncService(queue_repository, dispatcher)


class DurableCommandBus:
    def __init__(self, queue: QueueRepository, service: SyncService):
        self.queue = queue; self.service = service

    async def submit(self, entity_type: str, operation: str, payload: dict,
                     entity_id: str | None = None, operation_id: str | None = None) -> dict:
        operation_id = operation_id or str(uuid4())
        entity_id = entity_id or payload.get("id") or operation_id
        self.queue.enqueue(entity_type, str(entity_id), operation, json.dumps(payload, separators=(",", ":")), operation_id)
        self.service.wake()
        self.service.run_once()
        pending = self.queue.get(operation_id) is not None
        return {"operation_id": operation_id, "queued": pending, "synchronized": not pending}


command_bus = DurableCommandBus(queue_repository, sync_service)


def get_command_bus():
    return command_bus
