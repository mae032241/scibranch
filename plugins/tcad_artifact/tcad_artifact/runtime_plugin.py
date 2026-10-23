"""TCAD transport configuration and construction, without Operation hosting."""
from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class TCADRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    transport: Literal["socket", "command"]
    socket_path: str | None = Field(default=None, min_length=1, max_length=4096)
    socket_timeout_seconds: float = Field(default=10.0, gt=0)
    command_config_path: str | None = Field(
        default=None, min_length=1, max_length=4096
    )

    @model_validator(mode="after")
    def _one_transport(self) -> "TCADRuntimeConfig":
        if self.transport == "socket":
            if self.socket_path is None or self.command_config_path is not None:
                raise ValueError("socket transport requires only socket_path")
            if not Path(self.socket_path).is_absolute():
                raise ValueError("TCAD socket path must be absolute")
        elif self.command_config_path is None or self.socket_path is not None:
            raise ValueError("command transport requires only command_config_path")
        elif not Path(self.command_config_path).is_absolute():
            raise ValueError("TCAD command config path must be absolute")
        return self



def build_adapter(config: TCADRuntimeConfig, *, state_root: Path):
    """Construct the existing transport without contacting a remote service."""
    if config.transport == "socket":
        from .execution_adapter import TCADExecutorAdapter

        assert config.socket_path is not None
        adapter = TCADExecutorAdapter(config.socket_path, timeout=config.socket_timeout_seconds)
    else:
        from .command_adapter import CommandTCADExecutorAdapter

        assert config.command_config_path is not None
        adapter = CommandTCADExecutorAdapter.from_file(
            config.command_config_path,
            local_result_root=state_root / "executor-results",
        )
    return adapter
