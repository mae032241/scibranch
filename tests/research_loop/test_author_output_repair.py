"""Author layout diagnoses stay bounded before any native work is admitted."""
from scidiscovery.research_loop.output_repair import OutputRepairRequired


def test_repair_diagnostic_is_bounded_in_utf8_bytes():
    diagnosis=OutputRepairRequired('layout','界'*512).diagnostic
    assert diagnosis and len(diagnosis.encode('utf-8'))<=512
