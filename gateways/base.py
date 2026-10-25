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
        channel = task.source_channel
        gw = self.gateways.get(channel) or self.gateways.get("console")
        delivered = False
        if gw:
            delivered = gw.send_message(message, recipient_id=task.source_user)

        self.db.log_event(
            event_type="MESSAGE_OUTBOUND",
            actor=f"gateway:{channel}",
            task_id=task.id,
            summary=f"Delivered task #{task.id} response to {channel} (user={task.source_user}, ok={delivered})",
            payload={"channel": channel, "recipient": task.source_user, "delivered": delivered},
        )

    def broadcast_notification(self, message: str, channels: Optional[List[str]] = None) -> None:
        """Broadcast a Watcher alert or digest to all enabled gateways."""
        targets = channels or list(self.gateways.keys())
        for ch in targets:
            gw = self.gateways.get(ch)
            if gw:
                gw.send_message(message)
