"""Base Messaging Gateway interface and Multi-Channel Router."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from core.database import WorkspaceDatabase
from core.models import TaskRecord


class BaseMessagingGateway(ABC):
    """Abstract adapter for sending and receiving messages from external apps."""

    channel_name: str = "base"

    @abstractmethod
    def send_message(self, message: str, recipient_id: Optional[str] = None) -> bool:
        """Send a message to the external platform."""
        raise NotImplementedError


class MessagingRouter:
    """Routes outbound Communicator and Watcher messages to the appropriate platform."""

    def __init__(self, db: WorkspaceDatabase) -> None:
        self.db = db
        self.gateways: Dict[str, BaseMessagingGateway] = {}

    def register(self, gateway: BaseMessagingGateway) -> None:
        self.gateways[gateway.channel_name] = gateway

    def dispatch_task_response(self, task: TaskRecord, message: str) -> None:
        """Send the Communicator's final response back to the originating channel."""
        self.flush_outbox()

    def flush_outbox(self):
        for _ in range(5):
            item = self.db.claim_outbound()
            if not item:
                break
            gw = self.gateways.get(item["channel"])
            try:
                delivered = bool(gw and gw.send_message(item["message"], recipient_id=item["recipient"]))
            except Exception:
                delivered = False
            self.db.finish_outbound(item["task_id"], delivered, item['revision'])
            self.db.log_event("MESSAGE_OUTBOUND", f"gateway:{item['channel']}",
                f"Delivery {'succeeded' if delivered else 'queued for retry'}", task_id=item["task_id"],
                payload={"delivered": delivered, "attempt": item["attempts"] + 1})

    def broadcast_notification(self, message: str, channels: Optional[List[str]] = None) -> None:
        """Broadcast a Watcher alert or digest to all enabled gateways."""
        targets = channels or list(self.gateways.keys())
        delivered = True
        for ch in targets:
            gw = self.gateways.get(ch)
            if gw:
                delivered = gw.send_message(message) and delivered
        return delivered
