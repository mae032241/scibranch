"""Typed, user-facing input failures for the standalone figure scripts."""
class ToolInputError(ValueError):
    def __init__(self, code, message, *, path="$", **kwargs):
        self.code, self.path = code, path
        super().__init__(f"{code}: {message} ({path})")

def declared_violation(message, *, path="$", **kwargs):
    return ToolInputError("invalid_figure_request", message, path=path)
