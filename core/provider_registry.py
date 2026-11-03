"""Explicit host-installed provider plugins; no dynamic imports from task prompts."""
from typing import Protocol
from core.models import AgentRunResult


class BaseAgentProvider(Protocol):
    def run(self, *, role, prompt, workspace, timeout, model, log_path, cancel_event) -> AgentRunResult: ...


class ProviderRegistry:
    def __init__(self):self.adapters={}
    def register(self,name,adapter):
        if not name or name in self.adapters or not callable(getattr(adapter,'run',None)):
            raise ValueError('Unique provider type and run adapter required')
        self.adapters[name]=adapter
    def get(self,name):return self.adapters.get(name)
