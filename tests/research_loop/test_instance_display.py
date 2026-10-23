"""Saved UI reads never reconstruct execution, qualification or byte identity."""
import io
import json
import os

import pytest

from scidiscovery.research_loop import contracts as c, execution, instance_display as display


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def scene(tmp_path, *, attempt=0):
    root = tmp_path / 'instance'
    record = root / 'native-evidence/001'
    source = root / 'project/nodes/001/artifacts/z%20literal.csv'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'x,y\r\n1,2\r\n')
    material = dict(source=str(source), path='inputs/parents/001/artifacts/z%20literal.csv',
                    size_bytes=source.stat().st_size)
    seal = dict(materials=[material], raw_ref='saved raw identity',
                observations=dict(path='host-history/observations/001.json', size_bytes=0))
    if attempt:
        seal['execution_ref'] = dict(attempt=attempt, chain=['never rebuild this chain'])
    write(record / 'result-identity.json', seal)
    write(record / 'result.json', dict(role='H', saved='canonical result'))
    raw_record = record / f'continuations/{attempt:03d}' if attempt else record
    write(raw_record / 'science-output.json', dict(conclusion='saved scientific finding'))
    write(root / 'jobs/001.json', dict(step=1, role='H'))
    write(root / 'settings.json', dict(goal='saved goal'))
    write(root / 'host-history/observations/001.json',
          dict(binding=dict(source_ref='a changed saved binding'), managed_calls=[]))
    return root, record, source


def read(original):
    with original:
        output = io.BytesIO()
        original.check()
        original.copy_to(output)
        assert original.size == len(output.getvalue())
        return output.getvalue()


def test_saved_display_never_revalidates_or_samples_file_generation(tmp_path, monkeypatch):
    root, _, source = scene(tmp_path, attempt=3)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}

    def unexpected(*args, **kwargs):
        pytest.fail('UI saved reads must not revalidate or sample file generation')

    original_stat = os.stat
    def metadata_stat(path, *args, **kwargs):
        # Fork classification may inspect its manifest location; saved scientific
        # files still must not be sampled for a generation or integrity check.
        if os.fspath(path) == str(root/'host-runtime/fork/manifest.json'):
            return original_stat(path, *args, **kwargs)
        return unexpected(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        for name in ('settings', 'digest', 'regular', 'validate_seal'):
            if hasattr(c, name):
                patch.setattr(c, name, unexpected)
        for name in ('origin', 'reference', 'record', 'records'):
            patch.setattr(execution, name, unexpected)
        patch.setattr(os, 'stat', metadata_stat)
        patch.setattr(os, 'fstat', unexpected)
        assert display.settings(root) == dict(goal='saved goal')
        node = display.node(root, 1)
        assert node['payload']['conclusion'] == 'saved scientific finding'
        assert node['result']['saved'] == 'canonical result'
        assert display.observation(root, 1, node)['binding']['source_ref'] == 'a changed saved binding'
        assert json.loads(read(display.science(root, 1))) == node['payload']
        assert display.materials(root, 1)['items'] == [dict(index=1, name='z%20literal.csv', size_bytes=10)]
        original, name = display.artifact(root, 1, 1)
        assert name == 'z%20literal.csv'
        assert read(original) == b'x,y\r\n1,2\r\n'
    assert before == {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_download_reads_saved_location_without_checking_old_digest_or_size(tmp_path):
    root, _, source = scene(tmp_path)
    source.write_bytes(b'Current bytes at the saved location, with a different length.\x00')
    original, _ = display.artifact(root, 1, 1)
    assert read(original) == source.read_bytes()
    assert original.stream.closed


@pytest.mark.parametrize('legacy_source', [False, True])
def test_archived_member_streams_only_the_saved_object(tmp_path, legacy_source):
    root, record, _ = scene(tmp_path)
    archive = root / 'host-history/objects' / ('a' * 64)
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b'An existing saved archive object.\x00\xff')
    source = root / 'project/nodes/001/work/calls/call-1/outputs/curve.csv' if legacy_source else archive
    member = dict(source=str(source), path='inputs/parents/001/artifacts/experiments/call-1/artifacts/curve.csv',
                  size_bytes=123)
    seal = json.loads((record / 'result-identity.json').read_text())
    seal['materials'] = [member]
    write(record / 'result-identity.json', seal)
    if legacy_source:
        write(root / 'host-history/observations/001.json', dict(managed_calls=[dict(
            call_id='call-1', scope=dict(step=1), outputs=[dict(name='curve.csv',
                path=source.relative_to(root).as_posix(), snapshot=archive.relative_to(root).as_posix(),
                retention='archived')])]))
    original, name = display.artifact(root, 1, 1)
    assert name == 'experiments/call-1/artifacts/curve.csv'
    assert read(original) == archive.read_bytes()


def test_exact_member_index_and_saved_output_scope_still_restrict_downloads(tmp_path):
    root, record, source = scene(tmp_path)
    for index in (0, -1, True, 2, '../private'):
        with pytest.raises(ValueError, match='index'):
            display.artifact(root, 1, index)
    private = root / 'execution-authority/private.json'
    private.parent.mkdir()
    private.write_bytes(b'private data')
    seal_path = record / 'result-identity.json'
    seal = json.loads(seal_path.read_text())
    seal['materials'][0]['source'] = str(private)
    write(seal_path, seal)
    with pytest.raises(ValueError, match='output owner'):
        display.artifact(root, 1, 1)
    seal['materials'][0]['source'] = str(source)
    write(seal_path, seal)
    source.unlink()
    source.symlink_to(private)
    with pytest.raises(ValueError, match='instance location'):
        display.artifact(root, 1, 1)
    seal['materials'][0]['path'] = 'inputs/parents/001/artifacts/../private.json'
    write(seal_path, seal)
    with pytest.raises(ValueError, match='node-owned name'):
        display.artifact(root, 1, 1)


def test_missing_or_malformed_saved_documents_are_reported_without_recovery(tmp_path):
    root, record, _ = scene(tmp_path)
    (record / 'science-output.json').unlink()
    with pytest.raises(ValueError, match='missing|inaccessible'):
        display.node(root, 1)
    with pytest.raises(ValueError, match='missing|inaccessible'):
        display.science(root, 1)
    # A material page reads its saved inventory without opening the raw output.
    assert display.materials(root, 1)['total'] == 1
    (record / 'result-identity.json').write_text('{', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        display.node(root, 1)


def test_pagination_and_observation_are_selected_from_saved_node_only(tmp_path):
    root, record, _ = scene(tmp_path)
    page = display.materials(root, 1, after=0, limit=1)
    assert page['total'] == 1 and page['next_after'] is None
    assert display.materials(root, 1, after=1)['items'] == []
    for kwargs in (dict(after=-1), dict(after=True), dict(limit=0), dict(limit=101)):
        with pytest.raises(ValueError, match='continuation'):
            display.materials(root, 1, **kwargs)
    seal = json.loads((record / 'result-identity.json').read_text())
    seal.pop('observations')
    write(record / 'result-identity.json', seal)
    assert display.observation(root, 1) is None
    seal['observations'] = dict(path='execution-authority/private.json')
    with pytest.raises(ValueError, match='escaped its node'):
        display.observation(root, 1, node=dict(seal=seal))
