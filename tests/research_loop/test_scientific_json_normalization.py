"""Narrow authored-JSON framing keeps exact originals and existing repair bounds."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, continuation
from scidiscovery.research_loop.output_repair import OutputRepairRequired


SCIENCE = dict(summary='原始观察', scientific_result='Uncertainty remains.', limitations=[],
    extra_science={'自由字段': ['Retain authored detail.']})


@pytest.mark.parametrize(('prefix', 'suffix', 'kind'), [
    ('', '', None), (' \n', '\t\n', None), ('\ufeff ', '', 'bom'),
    ('```json\n', '\n```', 'fenced'), ('\ufeff\n```\r\n', '\r\n```\n', 'bom+fenced'),
])
def test_single_scientific_object_normalizes_without_changing_original(tmp_path, prefix, suffix, kind):
    raw = tmp_path/'science-output.json'
    original = (prefix+json.dumps(SCIENCE, ensure_ascii=False)+suffix).encode('utf-8')
    raw.write_bytes(original)
    reference = c.file_ref(raw)
    assert c.decode_scientific_output(original.decode('utf-8')) == (SCIENCE, kind)
    assert c.read_scientific_output(raw) == c.science_payload(raw, 'E') == SCIENCE
    assert raw.read_bytes() == original and c.file_ref(raw) == reference
    if kind is not None:
        with pytest.raises(json.JSONDecodeError):
            c.read(raw)  # Control-record parsing stays strict.


@pytest.mark.parametrize('text', [
    'A result follows: {}', '{} {}', '{} trailing', '[{}]', 'null',
    '```JSON\n{}\n```', '```json\n{}\n```\nExplanation', '```json {}\n```',
    '\ufeff\ufeff{}', '{}\ufeff', '```json\n\ufeff{}\n```',
])
def test_scientific_parser_rejects_ambiguous_or_nonobject_output(tmp_path, text):
    raw = tmp_path/'science-output.json'
    raw.write_text(text, encoding='utf-8')
    with pytest.raises(OutputRepairRequired) as caught:
        c.science_payload(raw, 'E')
    assert caught.value.code == 'invalid_json'


def test_normalized_object_keeps_existing_field_repair(tmp_path):
    raw = tmp_path/'science-output.json'
    raw.write_text('```json\n'+json.dumps(dict(SCIENCE, summary=7))+'\n```', encoding='utf-8')
    with pytest.raises(OutputRepairRequired) as caught:
        c.science_payload(raw, 'E')
    assert caught.value.code == 'invalid_fields'


def test_accepted_work_repair_reads_normalized_original_without_rejudging_fields(tmp_path, monkeypatch):
    root = tmp_path/'instance'
    prior = c.host_record(c.workspace(root, 1))
    c.write(root/'jobs/001.json', dict(role='W'))
    prior.mkdir(parents=True, exist_ok=True)
    raw = prior/'science-output.json'
    raw.write_text('```json\n{"stage_intent":"review"}\n```', encoding='utf-8')
    c.write(prior/'work-evidence.json', {})
    c.write(prior/'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=dict(unknown_steps=[]), raw_ref=c.file_ref(raw)))
    request = dict(code='worker_delivery_incomplete', diagnostic='Original accepted diagnosis',
        source_attempt=0, outcome_ref=c.record_ref(prior/'native-outcome.json'),
        raw_ref=c.file_ref(raw), work_evidence_ref=c.record_ref(prior/'work-evidence.json'))
    intent = dict(attempt=1, continuation_kind='repair', work_repair=request)
    c.write(prior/'continuations/001/native-intent.json', intent)
    monkeypatch.setattr(continuation.configuration_updates, 'for_step', lambda *args: {})
    assert continuation._accepted_repair_request(root, 1, intent, prior) == request
