"""Consumed author errors return to the original finite native session."""

import pytest

from scidiscovery.research_loop import contracts as c
from scidiscovery.research_loop.output_repair import OutputRepairRequired


@pytest.mark.parametrize('damage,field,expectation',[
    ('missing','summary','missing required field summary'),
    ('type','limitations','expected array'),
    ('enum','next_action','expected one of'),
])
def test_required_field_diagnosis_identifies_short_repair_without_rewriting_bytes(tmp_path,damage,field,expectation):
    value=dict(summary='Retained science',scientific_result='Original evidence',limitations=[],
        next_action='analyze',route_reason='Read the retained comparison.')
    if damage=='missing':value.pop(field)
    elif damage=='type':value[field]='wrong type'
    else:value[field]='not-an-action'
    path=tmp_path/'original.json';c.write(path,value);original=path.read_bytes()
    with pytest.raises(OutputRepairRequired) as caught:c.science_payload(path,'H')
    assert expectation in caught.value.diagnostic
    assert field in caught.value.diagnostic and len(caught.value.diagnostic.encode('utf-8'))<=512
    assert path.read_bytes()==original


def test_diagnostic_display_replaces_invalid_unicode_without_creating_a_science_gate():
    error=OutputRepairRequired('choice','Correct focus \ud800'+'界'*512)
    assert len(error.diagnostic.encode('utf-8'))<=512 and error.code=='choice'
