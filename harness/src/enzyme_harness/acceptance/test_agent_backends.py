"""Behavioral checks for agent selection, model routes, and the dsh backend."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from enzyme_harness.agents import backend_for
from enzyme_harness.agents.dsh import extract_json, normalize_usage
from enzyme_harness.native import run
from enzyme_harness.native_report import token_usage
from enzyme_harness.store import Task, selection

FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, uuid
if '--version' in sys.argv:
 print(os.environ.get('FAKE_CODEX_VERSION','codex-cli 0.164.0')); raise SystemExit(0)
args=sys.argv[1:]
assert args[0]=='exec'
root=pathlib.Path(os.environ['ENZYME_HARNESS_ROOT']); tid=os.environ['ENZYME_HARNESS_TASK_ID']
task=root/'tasks'/tid
if 'resume' in args:
 sid=args[args.index('resume')+1]
else:
 sid=str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':sid}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':4,'output_tokens':2,'reasoning_output_tokens':1}}),flush=True)
'''

FAKE_DSH = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, uuid
args=sys.argv[1:]
if '--version' in args:
 print(os.environ.get('FAKE_DSH_VERSION','0.2.2-rc.1')); raise SystemExit(0)
root=pathlib.Path(os.environ['ENZYME_HARNESS_ROOT']); tid=os.environ['ENZYME_HARNESS_TASK_ID']
task=root/'tasks'/tid
assert '--profile' in args and args[args.index('--profile')+1]=='headless'
assert '--json' in args and '--approval-policy' in args
patches=[args[i+1] for i,a in enumerate(args) if a=='--patch']
assert patches and pathlib.Path(patches[-1]).is_file()
mode=args[args.index('--permission-mode')+1]
expected=os.environ.get('FAKE_DSH_MODE')
assert not expected or mode==expected, mode
home=pathlib.Path(os.environ['DSH_HOME'])
assert (home/'profiles/headless/package.json').is_file(), home
sid=args[args.index('--session-id')+1] if '--session-id' in args else 'session-'+str(uuid.uuid4())
prompt=sys.stdin.read()
print(json.dumps({'type':'session','sessionId':sid,'cwd':os.getcwd()}),flush=True)
print(json.dumps({'type':'status','phase':'turn_start','turn':1}),flush=True)
print(json.dumps({'type':'tool_call','callId':'call-1','tool':'bash','input':{'command':'echo hi'}}),flush=True)
print(json.dumps({'type':'tool_result','callId':'call-1','status':'completed'}),flush=True)
print(json.dumps({'type':'status','phase':'step_end','turn':1,'step':1,
 'usage':{'inputTokens':11,'outputTokens':4,'cacheReadTokens':2,'reasoningTokens':1}}),flush=True)
main_attempts=len(list((task/'calls').glob('*-main')))
if 'Independently review' in prompt:
 role='review'
elif 'Diagnose the failure' in prompt:
 role='repair'
else:
 role='main'
path=task/'workspace/result.txt'
if role=='main':
 if not path.exists(): path.write_text('native result')
 value={'status':'COMPLETED','summary':'done','artifacts':[str(path)],'limitations':[],'questions':[]}
 text=json.dumps(value)
 if os.environ.get('FAKE_DSH_BAD_JSON')=='1' and main_attempts==1: text='not json at all'
elif role=='review':
 assert path.read_text()=='native result'
 value={'status':'PASS','summary':'reviewed','findings':['supported'],'evidence':[str(path)],
        'repair_request':'','limitations':[]}
 text=json.dumps(value)
else:
 value={'status':'FIXED','summary':'no code change','diagnosis':'fixture','evidence_paths':[],'resume_from':'main','changed_files':[]}
 text=json.dumps(value)
print(json.dumps({'type':'final','sessionId':sid,'reason':{'kind':'completed'},'text':text}),flush=True)
'''


def set_request(task, **fields):
    request = task.request(); request.update(fields)
    (task.path / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2) + '\n')


def make_task(root, script, request):
    binary = Path(root) / ('fake-' + request.get('agent', 'codex'))
    binary.write_text(script)
    binary.chmod(0o755)
    body = {'goal': 'test agent backend', 'python': sys.executable, 'sandbox_backend': 'none',
            'agent_binary': str(binary)}
    body.update(request)
    return Task.create(root, body)


class Selection(unittest.TestCase):
    def test_defaults_follow_the_selected_agent(self):
        self.assertEqual(selection({}, {'agent': 'codex'})['model'], 'gpt-6-luna')
        self.assertEqual(selection({}, {'agent': 'dsh'})['model'], 'deepseek-flash')
        self.assertEqual(selection({}, {'agent': 'dsh'})['provider'], 'deepseek-official')
        self.assertIsNone(selection({}, {'agent': 'codex'})['provider'])

    def test_explicit_config_wins_only_for_its_own_agent(self):
        config = {'agent': 'codex', 'model': 'gpt-6-luna', 'provider': None}
        self.assertEqual(selection(config, {})['model'], 'gpt-6-luna')
        self.assertEqual(selection(config, {'agent': 'dsh'})['model'], 'deepseek-flash')
        self.assertEqual(selection(config, {'agent': 'dsh', 'model': 'custom'})['model'], 'custom')

    def test_provider_belongs_to_dsh_only(self):
        with self.assertRaisesRegex(ValueError, 'PROVIDER_REQUIRES_AGENT'):
            selection({}, {'agent': 'codex', 'provider': 'openai'})
        with self.assertRaisesRegex(ValueError, 'AGENT_UNSUPPORTED'):
            selection({}, {'agent': 'other'})
        with self.assertRaisesRegex(ValueError, 'SANDBOX_BACKEND_INVALID'):
            selection({}, {'sandbox_backend': 'chroot'})

    def test_config_for_another_agent_does_not_leak(self):
        config = {'agent': 'codex', 'model': 'gpt-6-luna', 'agent_binary': '/opt/codex',
                  'agent_version': 'codex-cli 0.164.0', 'codex_binary': '/opt/codex'}
        chosen = selection(config, {'agent': 'dsh'})
        self.assertEqual(chosen['model'], 'deepseek-flash')
        self.assertEqual(chosen['agent_binary'], 'dsh')
        self.assertIsNone(chosen['agent_version'])
        # A dsh-only overlay in the config does not block a Codex task.
        dsh_config = {'agent': 'dsh', 'dsh_patch': '/tmp/overlay.yml'}
        self.assertIsNone(selection(dsh_config, {'agent': 'codex'})['dsh_patch'])

    def test_legacy_codex_binary_is_accepted(self):
        chosen = selection({}, {'codex_binary': '/tmp/codex'})
        self.assertEqual(chosen['agent'], 'codex')
        self.assertEqual(chosen['agent_binary'], '/tmp/codex')


class Composition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.codex = make_task(self.root, FAKE_CODEX, {'agent': 'codex'})

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, task, role='main'):
        backend = backend_for(task)
        folder = backend.records() / ('0001-' + role)
        folder.mkdir(parents=True)
        work = task.path / 'workspace'
        if role == 'repair':
            work = task.path / 'repair-work'; work.mkdir(exist_ok=True)
        return backend, backend.prepare(role, folder, work, 'prompt'), folder

    def test_codex_main_role_is_wrapped_and_full_access(self):
        set_request(self.codex, sandbox_backend='auto')
        with patch('enzyme_harness.agents.codex.bwrap_usable', return_value=True):
            _, launch, _ = self.prepare(self.codex)
        self.assertEqual(launch['command'][0], 'bwrap')
        self.assertIn('--dev-bind', launch['command'])
        self.assertEqual(launch['sandbox'], 'danger-full-access')
        self.assertFalse(launch['unconfined'])

    def test_codex_review_uses_read_only_when_enforced(self):
        set_request(self.codex, sandbox_backend='auto')
        _, launch, _ = self.prepare(self.codex, 'review')
        self.assertEqual(launch['sandbox'], 'read-only')
        self.assertIn('--ignore-user-config', launch['command'])
        self.assertFalse(launch['unconfined'])

    def test_unconfined_codex_roles_are_explicit(self):
        task = make_task(self.root, FAKE_CODEX, {'agent': 'codex', 'sandbox_backend': 'none'})
        _, launch, _ = self.prepare(task, 'review')
        self.assertEqual(launch['sandbox'], 'danger-full-access')
        self.assertTrue(launch['unconfined'])
        _, main, _ = self.prepare(task, 'main')
        self.assertEqual(main['command'][0], str(Path(task.request()['agent_binary'])))
        self.assertTrue(main['unconfined'])

    def test_codex_accepts_the_installed_version_unless_pinned(self):
        task = make_task(self.root, FAKE_CODEX, {'agent': 'codex'})
        with patch.dict(os.environ, {'FAKE_CODEX_VERSION': 'codex-cli 9.9.9'}):
            self.assertEqual(backend_for(task).verify(), 'codex-cli 9.9.9')
        pinned = make_task(self.root, FAKE_CODEX, {'agent': 'codex', 'agent_version': 'codex-cli 9.9.9'})
        with patch.dict(os.environ, {'FAKE_CODEX_VERSION': 'codex-cli 9.9.9'}):
            self.assertEqual(backend_for(pinned).verify(), 'codex-cli 9.9.9')
        with self.assertRaisesRegex(RuntimeError, 'AGENT_VERSION_NOT_VERIFIED:codex'):
            backend_for(pinned).verify()


class Dsh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dsh_main_and_review_complete_with_usage_and_route_patch(self):
        task = make_task(self.root, FAKE_DSH, {'agent': 'dsh', 'agent_version': '0.2.2-rc.1'})
        result = run(task)
        self.assertEqual(result['status'], 'COMPLETED', result)
        self.assertEqual(result['calls'], 2)
        request = task.request()
        self.assertEqual(request['model'], 'deepseek-flash')
        self.assertEqual(request['provider'], 'deepseek-official')
        rows = yaml.safe_load((task.path / 'calls/0001-main/patch.yml').read_text())
        by_id = {row['id']: row['config'] for row in rows}
        self.assertEqual(by_id['agent-default-model']['provider'], 'deepseek-official')
        self.assertEqual(by_id['agent-default-model']['model'], 'deepseek-flash')
        self.assertEqual(by_id['session-persistence-jsonl']['root'], str(task.path / 'dsh/sessions'))
        self.assertEqual(by_id['storage-json']['root'], str(task.path / 'dsh/storages'))
        self.assertTrue((task.path / 'dsh/home/profiles/headless/package.json').is_file())
        usage = token_usage(task)
        self.assertEqual(usage['agents'], ['dsh'])
        self.assertEqual(usage['input_tokens'], 26)
        self.assertEqual(usage['cached_input_tokens'], 4)
        self.assertEqual(usage['output_tokens'], 8)
        self.assertEqual(usage['reasoning_output_tokens'], 2)
        self.assertEqual(usage['session_count'], 2)
        self.assertEqual(usage['tool_calls'], {'bash': 2})
        self.assertEqual(usage['unconfined_calls'], 2)
        report = (task.path / 'reports/report.md').read_text()
        self.assertIn('代理：dsh', report)
        self.assertIn('模型：deepseek-flash', report)
        self.assertIn('不受 OS 沙箱限制', report)
        self.assertTrue((task.path / 'reports/report.html').is_file())

    def test_dsh_reuses_main_session_after_a_bad_structured_answer(self):
        task = make_task(self.root, FAKE_DSH, {'agent': 'dsh'})
        with patch.dict(os.environ, {'FAKE_DSH_BAD_JSON': '1'}):
            result = run(task)
        self.assertEqual(result['status'], 'COMPLETED', result)
        self.assertEqual(result['calls'], 3)
        mains = sorted((task.path / 'calls').glob('*-main/events.jsonl'))
        ids = [json.loads(path.read_text().splitlines()[0])['sessionId'] for path in mains]
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])
        self.assertIn('not a valid structured response', (task.path / 'calls/0002-main/prompt.txt').read_text())

    def test_dsh_permission_modes_and_bwrap_wrapper(self):
        task = make_task(self.root, FAKE_DSH, {'agent': 'dsh', 'sandbox_backend': 'none'})
        backend = backend_for(task)
        folder = backend.records() / '0001-main'; folder.mkdir(parents=True)
        work = task.path / 'workspace'
        launch = backend.prepare('main', folder, work, 'prompt')
        self.assertEqual(launch['sandbox'], 'danger-full-access')
        self.assertNotEqual(launch['command'][0], 'bwrap')
        self.assertIn('--permission-mode', launch['command'])
        self.assertEqual(launch['command'][launch['command'].index('--permission-mode') + 1], 'danger-full-access')
        for role, mode in (('review', 'danger-full-access'), ('repair', 'danger-full-access')):
            launch = backend.prepare(role, folder, work, 'prompt')
            self.assertEqual(launch['sandbox'], mode)
        set_request(task, sandbox_backend='auto')
        backend = backend_for(task)
        with patch('enzyme_harness.agents.dsh.bwrap_usable', return_value=True):
            wrapped = backend.prepare('main', folder, work, 'prompt')
        self.assertEqual(wrapped['command'][0], 'bwrap')
        state = str(task.path / 'dsh')
        self.assertIn(state, wrapped['command'])
        review = backend.prepare('review', folder, work, 'prompt')
        self.assertEqual(review['sandbox'], 'read-only')
        self.assertEqual(review['command'][review['command'].index('--permission-mode') + 1], 'read-only')

    def test_dsh_openai_route_adds_a_pi_ai_provider_row(self):
        task = make_task(self.root, FAKE_DSH, {'agent': 'dsh', 'provider': 'openai', 'model': 'gpt-5.2',
                                               'reasoning_effort': 'high'})
        backend = backend_for(task)
        folder = backend.records() / '0001-main'; folder.mkdir(parents=True)
        backend.prepare('main', folder, task.path / 'workspace', 'prompt')
        rows = {row['id']: row['config'] for row in yaml.safe_load((folder / 'patch.yml').read_text())}
        self.assertEqual(rows['agent-default-model'], {'provider': 'openai', 'model': 'gpt-5.2',
                                                       'reasoningEffort': 'high'})
        self.assertEqual(rows['llm-pi-ai']['providers'], {'openai': {}})

    def test_dsh_explicit_providers_replace_the_catalog_default(self):
        providers = {'gateway': {'api': 'openai-completions', 'baseURL': 'https://example.invalid/v1',
                                 'apiKeyEnv': 'GATEWAY_KEY'}}
        task = make_task(self.root, FAKE_DSH, {'agent': 'dsh', 'provider': 'gateway', 'model': 'gw-1',
                                               'dsh_providers': providers})
        backend = backend_for(task)
        folder = backend.records() / '0001-main'; folder.mkdir(parents=True)
        backend.prepare('main', folder, task.path / 'workspace', 'prompt')
        rows = {row['id']: row['config'] for row in yaml.safe_load((folder / 'patch.yml').read_text())}
        self.assertEqual(rows['llm-pi-ai']['providers'], providers)

    def test_dsh_accepts_the_installed_version_unless_pinned(self):
        task = make_task(self.root, FAKE_DSH, {'agent': 'dsh'})
        with patch.dict(os.environ, {'FAKE_DSH_VERSION': '1.2.3'}):
            self.assertEqual(backend_for(task).verify(), '1.2.3')

    def test_dsh_explicit_pin_mismatch_blocks_without_repair(self):
        bad = make_task(self.root, FAKE_DSH, {'agent': 'dsh', 'agent_version': '9.9.9'})
        with self.assertRaisesRegex(RuntimeError, 'AGENT_VERSION_NOT_VERIFIED:dsh'):
            backend_for(bad).verify()
        result = run(bad)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('AGENT_VERSION_NOT_VERIFIED:dsh', result['blocker'])
        self.assertEqual(result['repairs'], 0)
        self.assertEqual(result['calls'], 0)


class Response(unittest.TestCase):
    def test_extracts_the_first_json_object(self):
        self.assertEqual(extract_json('{"status":"PASS"}'), {'status': 'PASS'})
        self.assertEqual(extract_json('```json\n{"status":"PASS"}\n```'), {'status': 'PASS'})
        self.assertEqual(extract_json('Result follows.\n{"status":"PASS","summary":"ok"}\nDone.'),
                         {'status': 'PASS', 'summary': 'ok'})
        with self.assertRaisesRegex(ValueError, 'DSH_FINAL_MESSAGE_NOT_JSON'):
            extract_json('no object here')
        with self.assertRaisesRegex(ValueError, 'DSH_EMPTY_FINAL_MESSAGE'):
            extract_json('   ')

    def test_usage_normalization_reports_uncached_input(self):
        self.assertEqual(normalize_usage({'inputTokens': 5, 'cacheReadTokens': 2, 'cacheWriteTokens': 1,
                                          'outputTokens': 3, 'reasoningTokens': 1}),
                         {'input_tokens': 8, 'cached_input_tokens': 3, 'output_tokens': 3,
                          'reasoning_output_tokens': 1, 'total_tokens': None})
        self.assertIsNone(normalize_usage(None))


if __name__ == '__main__':
    unittest.main()
