"""Installed execution adapter factory and nonserialized host gate services."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

HOST_EXECUTION_GATE_KEY = 'host_execution_gate'
HOST_EXECUTION_GATE_SCHEMA = 'scidiscovery.host-execution-gate.v1'
HOST_EXECUTION_GATE_CONTRACT = MappingProxyType(dict(version=1,
    source='nonserialized_execution_host_services', admission='prepare_and_submit', resources='control_ledger'))






@dataclass(frozen=True, slots=True)
class ExecutionHostServices:
    """Core-created, nonserialized services for one verified native task.

    A trusted factory supplies its policy provider; JSON cannot select a gate.
    """

    gate: Callable[[Callable[..., Any]], Callable[..., Mapping[str, Any]]]
    call_identity: Callable[..., str]
    implementation_inputs: Any | None = None
    operation_context: Any | None = None


@dataclass(frozen=True, slots=True)
class RuntimePluginContext:
    """Startup context for installed trusted control-runtime code."""

    plugin_id: str
    mode: Literal["control", "local_worker"]
    config_path: Path
    config_bytes: bytes
    state_root: Path
    execution_host_services: ExecutionHostServices | None = None
    frozen_execution_interface: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RuntimePluginContribution:
    execution_adapters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        adapters = dict(self.execution_adapters)
        if any(
            not key or value is None
            for key, value in adapters.items()
        ):
            raise ValueError("runtime plugin contribution contains an invalid binding")
        object.__setattr__(self, "execution_adapters", MappingProxyType(adapters))


@dataclass(frozen=True, slots=True)
class RuntimePluginFactory:
    """One installed, trusted startup factory; not an Agent capability."""

    build: Callable[[RuntimePluginContext], RuntimePluginContribution]
    configure: Callable[..., dict] | None = None
    requires_config_file: bool = False
    def __post_init__(self) -> None:
        if not callable(self.build):
            raise TypeError("runtime plugin factory must be callable")
        if self.configure is not None and not callable(self.configure):
            raise TypeError("runtime plugin configuration callback must be callable")
        if type(self.requires_config_file) is not bool:
            raise TypeError("runtime plugin requires_config_file must be boolean")


__all__ = [
    "RuntimePluginContext",
    "ExecutionHostServices",
    "HOST_EXECUTION_GATE_KEY", "HOST_EXECUTION_GATE_SCHEMA", "HOST_EXECUTION_GATE_CONTRACT",
    "RuntimePluginContribution",
    "RuntimePluginFactory",
]
