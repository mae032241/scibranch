"""Author-correctable final response failures, never integrity or I/O failures."""


class OutputRepairRequired(ValueError):
    """A known completed turn needs a corrected response in its original session."""

    def __init__(self, code, diagnostic):
        self.code = code
        self.diagnostic = diagnostic.encode('utf-8')[:512].decode('utf-8', errors='ignore')
        super().__init__(self.diagnostic)

    def request(self):
        return dict(code=self.code, diagnostic=self.diagnostic)


class WorkRepairRequired(OutputRepairRequired):
    """A schema-valid W response needs bounded scientific delivery correction."""

    def __init__(self, diagnostic):
        super().__init__('worker_delivery_incomplete', diagnostic)
