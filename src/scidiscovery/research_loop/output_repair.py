"""Author-correctable final response failures, never integrity or I/O failures."""
import json


class OutputRepairRequired(ValueError):
    """A known completed turn needs a corrected response in its original session."""

    def __init__(self, code, diagnostic):
        self.code = code
        text=diagnostic.encode('utf-8',errors='replace')[:512].decode('utf-8', errors='ignore')
        def wire_bytes(value):
            return len(json.dumps(json.dumps(value,ensure_ascii=False),ensure_ascii=False).encode('utf-8'))
        empty=wire_bytes('')
        low,high=0,len(text)
        while low<high:
            middle=(low+high+1)//2
            if wire_bytes(text[:middle])-empty<=512:low=middle
            else:high=middle-1
        self.diagnostic=text[:low]
        super().__init__(self.diagnostic)

    def request(self):
        return dict(code=self.code, diagnostic=self.diagnostic)


class WorkRepairRequired(OutputRepairRequired):
    """A schema-valid W response needs bounded scientific delivery correction."""

    def __init__(self, diagnostic):
        super().__init__('worker_delivery_incomplete', diagnostic)
