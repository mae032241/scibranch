"""A prospective internal route; durable records remain owned by their publishers."""
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Route:
    role: str | None
    parents: tuple[int, ...] = ()
    instruction: str = ''
    evidence_return: dict | None = None
    current_h: int | None = None
    current_w: int | None = None
    correction_request: dict | None = None
    author_correction: dict | None = None

    def __post_init__(self):
        object.__setattr__(self, 'parents', tuple(self.parents))
