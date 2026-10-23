"""Media-type bound used by immutable execution file descriptors."""
from typing import Annotated
from pydantic import Field

MediaType = Annotated[str, Field(min_length=3, max_length=255)]
