"""The recommended-file button streams its exact retained output over HTTP."""
import html
import http.client
import re
import threading
from urllib.parse import quote, urlencode

import pytest

from scidiscovery.browser_access import BrowserAccessService, access_cookie
from scidiscovery.research_loop import contracts as c, instance_display as display, instance_ui, ui_view
from test_execution_authority import prepare


def saved_candidate(tmp_path):
    """Saved display records only; no native execution or scientific verdict."""
    root, key = prepare(tmp_path, enabled=False)
    call_id = '0003-retained-download-fixture'
    output_name = 'native/model_run1.pkl'
    name = f'experiments/{call_id}/artifacts/{output_name}'
    original = root / 'project/nodes/005/work/retained-experiments' / call_id / 'outputs' / output_name
    original.parent.mkdir(parents=True, exist_ok=True)
    payload = (b'Exact retained candidate original.\x00' * 5000)[:140294]
    assert len(payload) == 140294
    original.write_bytes(payload)
    authored_copy = root / 'project/nodes/005/artifacts/experiments' / call_id / output_name
    authored_copy.parent.mkdir(parents=True, exist_ok=True)
    authored_copy.write_bytes(b'A separately authored copy must not replace the selected original.')
    unrelated = root / 'project/nodes/005/artifacts/unrelated.txt'
    unrelated.write_bytes(b'An unrelated sealed member.')
    prefix = 'inputs/parents/005/artifacts/'
    materials = [
        dict(path=prefix + 'unrelated.txt', source=str(unrelated), size_bytes=unrelated.stat().st_size),
        dict(path=prefix + name, source=str(original), size_bytes=len(payload)),
        dict(path=prefix + f'experiments/{call_id}/{output_name}', source=str(authored_copy),
            size_bytes=authored_copy.stat().st_size),
    ]
    candidate_id = 'local-exact-retained-candidate'
    observation = dict(candidates=[], contexts=[], managed_calls=[dict(call_id=call_id,
        scope=dict(step=5), outputs=[dict(name=output_name,
            path=original.relative_to(root).as_posix(), snapshot=original.relative_to(root).as_posix(),
            size_bytes=len(payload), retention='archived')])])
    c.write(root / 'host-history/observations/005.json', observation)
    nodes = {}
    for step in range(1, 9):
        role = 'W' if step == 5 else 'A' if step == 8 else 'H'
        job = dict(step=step, role=role, direct_refs=[], history=dict(nodes=[]))
        scientific = dict(summary='Mechanical saved display fixture.',
            scientific_result='No scientific effectiveness is asserted.', limitations=['Fixture only'])
        if role == 'A':
            scientific.update(next_action='deliver', candidate_recommendation=candidate_id,
                route_reason='Display the exact declared candidate original.')
        seal = dict(materials=materials if step == 5 else [])
        if step == 5:
            seal['observations'] = dict(path='host-history/observations/005.json')
        record = root / 'native-evidence' / f'{step:03d}'
        c.write(root / 'jobs' / f'{step:03d}.json', job)
        c.write(record / 'science-output.json', scientific)
        c.write(record / 'result.json', scientific)
        c.write(record / 'result-identity.json', seal)
        nodes[step] = (job, scientific, seal)
    # Freeze the already resolved candidate projection to keep this HTTP
    # regression on button -> exact member -> authenticated download transport.
    # Candidate admission and scientific qualification are separate contracts.
    projection = dict(index=[dict(id=candidate_id, label='Declared candidate', observation_step=5,
        evaluation_key='fixture', metric=1, direction='max', status='ok')],
        references={candidate_id: 'W005-C001/artifacts/' + output_name},
        graph=dict(mechanisms={}, candidates={}), nominated_ids=[candidate_id],
        records=[dict(step=5, role='W', observation=observation)],
        sources=[(*nodes[5], None)], subject=nodes[8][0], recommended=candidate_id)
    return root, key, original, payload, name, projection, observation


@pytest.mark.process_e2e
@pytest.mark.parametrize('mounted', [False, True])
def test_recommended_candidate_button_downloads_exact_retained_bytes_with_current_read_cookie(tmp_path, monkeypatch, mounted):
    root, key, original, payload, name, projection, observation = saved_candidate(tmp_path)
    host_root = tmp_path / 'workspace' if mounted else root
    if mounted:
        host_root.mkdir()
    options = dict(workspace=True, instance_roots=[root]) if mounted else {}
    ui = instance_ui.InstanceUI(host_root, secret_file=key, **options)
    child = ui._child(1) if mounted else ui
    monkeypatch.setattr(child, '_candidates', lambda *args, **options: projection)
    prefix = '/instances/1' if mounted else ''
    reader = ui_view.Reader(root)
    card = ui_view.result_card(reader, 8, projection=projection)
    member = ui_view.candidate_file(reader, card['chosen'], projection)
    assert member == dict(step=5, index=2, name=name,
        reference=dict(path=str(original), size_bytes=len(payload)))

    ready, servers, errors = threading.Event(), [], []
    server_type = instance_ui.ThreadingHTTPServer

    class Server(server_type):
        def serve_forever(self, **options):
            servers.append(self)
            ready.set()
            super().serve_forever(**options)

    monkeypatch.setattr(instance_ui, 'ThreadingHTTPServer', Server)
    original_ui = instance_ui.InstanceUI
    class PreparedUI(original_ui):
        def __new__(cls, *args, **options):
            return ui
    monkeypatch.setattr(instance_ui, 'InstanceUI', PreparedUI)
    monkeypatch.setattr(instance_ui, 'print', lambda *args, **options: None, raising=False)

    def serve():
        try:
            instance_ui.serve(host_root, secret_file=key, **options)
        except BaseException as error:
            errors.append(error)
            ready.set()

    worker = threading.Thread(target=serve)
    worker.start()
    connection = None
    try:
        assert ready.wait(5) and not errors
        connection = http.client.HTTPConnection('127.0.0.1', servers[0].server_port, timeout=5)
        connection.request('GET', prefix + '/view?' + urlencode({'token': child.read_token}))
        response = connection.getresponse()
        assert response.status == 200
        cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        response.read()
        connection.request('GET', prefix + '/results/8', headers={'Cookie': cookie})
        response = connection.getresponse()
        assert response.status == 200
        page = response.read().decode()
        button = re.search(r'<a href="([^"]+)" class="primary">下载推荐候选的研究文件：model_run1.pkl</a>', page)
        assert button is not None
        target = html.unescape(button[1])
        assert target == prefix + '/nodes/5/materials/2'
        connection.request('GET', target, headers={'Cookie': cookie})
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader('Content-Type') == 'application/octet-stream'
        assert response.getheader('Content-Disposition') == (
            "attachment; filename=scientific-deliverable; filename*=UTF-8''" + quote(name, safe=''))
        assert response.getheader('Content-Length') == str(len(payload))
        assert response.getheader('X-Content-Type-Options') == 'nosniff'
        assert "default-src 'none'" in response.getheader('Content-Security-Policy')
        assert response.read() == payload

        retired = BrowserAccessService(secret=child.secret, ui_session_id='retired-browser-session')
        retired_cookie = access_cookie(child.instance, retired.issue(child.instance, maintenance=False)).split(';', 1)[0]
        for headers in ({}, {'Cookie': retired_cookie}, {'Cookie': cookie, 'Host': 'other.invalid'}):
            connection.request('GET', target, headers=headers)
            response = connection.getresponse()
            assert response.status == 403
            assert response.read() != payload
        connection.request('GET', target + '?path=../private', headers={'Cookie': cookie})
        response = connection.getresponse()
        assert response.status in {400, 404}
        assert response.read() != payload

        # A saved output for another node cannot reuse this button's seal index.
        observation['managed_calls'][0]['scope']['step'] = 6
        c.write(root / 'host-history/observations/005.json', observation)
        connection.request('GET', target, headers={'Cookie': cookie})
        response = connection.getresponse()
        assert response.status == 500  # Corrupt saved control data is a diagnostic failure.
        assert response.read() != payload
    finally:
        if connection is not None:
            connection.close()
        if servers:
            servers[0].shutdown()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert original.read_bytes() == payload


@pytest.mark.parametrize('damage', ['foreign_source', 'other_call', 'other_snapshot', 'unbound_work', 'symlink'])
def test_retained_download_rejects_sources_outside_the_exact_saved_call_output(tmp_path, damage):
    root, _, original, payload, _, _, observation = saved_candidate(tmp_path)
    seal_path = root / 'native-evidence/005/result-identity.json'
    seal = c.read(seal_path)
    output = observation['managed_calls'][0]['outputs'][0]
    if damage == 'foreign_source':
        foreign = tmp_path / 'private.pkl'
        foreign.write_bytes(b'private bytes must not be served')
        seal['materials'][1]['source'] = str(foreign)
    elif damage == 'other_call':
        observation['managed_calls'][0]['call_id'] = 'another-call'
    elif damage == 'other_snapshot':
        output['snapshot'] = output['snapshot'].replace('/0003-retained-download-fixture/', '/another-call/')
    elif damage == 'unbound_work':
        unbound = root / 'project/nodes/005/work/private.pkl'
        unbound.write_bytes(b'An unrelated workspace file must not be served')
        seal['materials'][1]['source'] = str(unbound)
        output['path'] = output['snapshot'] = unbound.relative_to(root).as_posix()
    else:
        target = tmp_path / 'private.pkl'
        target.write_bytes(payload)
        original.unlink()
        original.symlink_to(target)
    c.write(seal_path, seal)
    c.write(root / 'host-history/observations/005.json', observation)
    with pytest.raises(ValueError, match='escaped|missing|inaccessible'):
        display.artifact(root, 5, 2)
