"""Future rented/SSH compute boundary (provisioning). No vendor SDK, network calls or purchases.

Notebook/batch job lifecycles (Colab, Kaggle) live in ``core.compute_jobs``; this module only
describes renting a machine and stays disabled until an adapter is installed and enabled.
"""
from dataclasses import dataclass
import math
from typing import Protocol

PLATFORMS=('shadeform','vastai','runpod','ssh')


@dataclass(frozen=True)
class ComputeRequest:
    task_id: int
    hardware: str
    max_hours: float
    max_total_cost_usd: float
    checkpoint_destination: str
    def __post_init__(self):
        if self.task_id<1 or not self.hardware or not self.checkpoint_destination:
            raise ValueError('Task, hardware and explicit durable checkpoint destination are required')
        if (not math.isfinite(self.max_hours) or self.max_hours<=0 or
            not math.isfinite(self.max_total_cost_usd) or self.max_total_cost_usd<0):
            raise ValueError('Finite duration and nonnegative total cost cap are required')


class ProvisioningAdapter(Protocol):
    """Unknown API state is UNKNOWN, never STOPPED. Provision reconciles its durable
    request key before retry. Stop must verify release, not transport disconnect.
    Credentials remain on the host. Controller approval precedes any paid effect.
    """
    def provision(self, request: ComputeRequest, request_key: str) -> dict: ...
    def observe(self, remote_identity: dict) -> dict: ...
    def stop(self, remote_identity: dict) -> dict: ...
    def download(self, remote_identity: dict, files: list[str], destination: str) -> dict: ...


ComputeAdapter = ProvisioningAdapter  # legacy name


class ComputeRegistry:
    def __init__(self, config=None):
        self.config=config or {}
        self.adapters={}

    def register(self, name, adapter):
        if name not in PLATFORMS:
            raise ValueError('Unknown compute extension')
        if any(not callable(getattr(adapter,method,None)) for method in ('provision','observe','stop','download')):
            raise TypeError('Compute adapter must implement the complete lifecycle')
        self.adapters[name]=adapter

    def require(self, name):
        if name not in PLATFORMS:
            raise ValueError('Unknown compute extension')
        if not self.config.get(name,{}).get('enabled',False) or name not in self.adapters:
            raise RuntimeError(f'{name} compute is disabled or its adapter is not installed; no allocation attempted')
        return self.adapters[name]

    def capabilities(self):
        return [{'platform':name,'installed':name in self.adapters,
                 'enabled':bool(self.config.get(name,{}).get('enabled',False)) and name in self.adapters,
                 'requirements':['explicit owner approval','cost and time caps','durable request key',
                                 'remote identity reconciliation','checkpoint and verified shutdown']}
                for name in PLATFORMS]
