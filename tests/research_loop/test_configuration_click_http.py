"""The settings link opens and saves explicit null plugin settings over HTTP."""
import http.client
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
from urllib.parse import urlsplit

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import configuration_updates, controller, instance_ui
from test_workspace_ui import controls, login, operator_key, request


@pytest.mark.process_e2e
def test_current_configuration_click_and_save_preserve_null_plugin_limits(tmp_path, execution_registry):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Read and retain explicit null configuration.')
    root = tmp_path / 'instance'
    limits = dict.fromkeys(('max_file_bytes', 'max_log_bytes', 'max_result_bytes', 'max_total_bytes'))
    controller.prepare(root, objective, public, ['never-launched'],
        max_tokens=4000, max_seconds=60, max_steps=7, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='local',
        execution_config=dict(executor='local_execution:command',
            configuration=dict(limits, run_argv=['never-launched'], max_seconds=1)))
    frozen = (root / 'settings.json').read_bytes()
    key = operator_key(tmp_path)
    source = Path(instance_ui.__file__).resolve().parents[2]
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(source), env.get('PYTHONPATH', '')]))
    process = subprocess.Popen([sys.executable, '-m', 'scidiscovery.research_loop.cli', 'serve-ui',
        '--root', str(root), '--approval-secret-file', str(key), '--port', '0'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    connection = None
    try:
        assert select.select([process.stdout], [], [], 8)[0], 'UI did not announce its endpoint'
        endpoint = json.loads(process.stdout.readline())
        assert endpoint['status'] == 'ui_listening'
        url = urlsplit(endpoint['management_url'])
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
        cookie = login(connection, url)
        status, settings, _ = request(connection, 'GET', '/settings', cookie=cookie)
        assert status == 200
        target = re.search(r'href="([^"]+)">当前研究配置</a>', settings.decode()).group(1)
        status, page, _ = request(connection, 'GET', target, cookie=cookie)
        assert status == 200 and '当前研究配置'.encode() in page
        fields = controls(page, action='configuration-update')
        nulls = [name[:-5] for name, value in fields.items() if name.endswith('.kind') and value == 'null']
        assert {fields[prefix+'.key'] for prefix in nulls} == set(limits)
        assert all(fields[prefix+'.value'] == '' for prefix in nulls)
        fields['cfg.agent.model'] = 'next-node-model'
        status, _, headers = request(connection, 'POST', '/control/configuration-update', cookie=cookie,
            fields=fields, origin='http://' + url.netloc)
        assert status == 303 and headers['Location'] == target
        status, page, _ = request(connection, 'GET', target, cookie=cookie)
        assert status == 200
        state = configuration_updates.status(root)
        assert state['settings']['model'] == 'next-node-model'
        assert all(state['settings']['execution_config']['configuration'][name] is None for name in limits)
        assert (root / 'settings.json').read_bytes() == frozen
        assert not (root / 'jobs').exists() or not any((root / 'jobs').iterdir())
    finally:
        if connection is not None:
            connection.close()
        if process.poll() is None:
            process.terminate()
        process.wait(5)
        process.stdout.close()
        process.stderr.close()
