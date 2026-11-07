"""Behavioral checks for the native task and detached-process workflow."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from enzyme_harness.files import sha, write
from enzyme_harness.native import run, _paths, _path_hints
from enzyme_harness.native_process import inspect, start, wait
from enzyme_harness.native_report import token_usage
from enzyme_harness.store import Task

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, uuid, time, signal
if '--version' in sys.argv:
 print('codex-cli 0.164.0'); raise SystemExit(0)
args=sys.argv[1:]
assert not any('mcp_servers.harness' in x for x in args)
assert '--sandbox' in args
root=pathlib.Path(os.environ['ENZYME_HARNESS_ROOT']); tid=os.environ['ENZYME_HARNESS_TASK_ID']
task=root/'tasks'/tid; output=pathlib.Path(args[args.index('-o')+1]); folder=output.parent
role=output.stem.split('-',1)[1] if folder.name=='.codex-output' else folder.name.split('-',1)[1]
if role=='main':
 assert '--ignore-user-config' not in args
 assert args[args.index('--sandbox')+1]=='danger-full-access'
 assert (task/'workspace/.agents/skills').is_dir()
 if os.environ.get('NATIVE_TEST_CONTROLLER_KILL')=='1': time.sleep(3)
 if os.environ.get('NATIVE_TEST_CODEX_KILL')=='1' and not (task/'processes/detached/invocation.json').exists():
  from enzyme_harness.store import Task
  from enzyme_harness.native_process import start
  print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}),flush=True)
  start(Task(root,tid),'detached',[sys.executable,'-c','import time;time.sleep(2);open("detached.txt","w").write("ok")'],'.',outputs=['detached.txt'])
  os.kill(os.getpid(),signal.SIGKILL)
 path=task/'workspace/result.txt'
 if not path.exists(): path.write_text('native result')
 main_attempts=len(list((task/'calls').glob('*-main')))
 artifact=str(task/'workspace/missing.txt') if os.environ.get('NATIVE_TEST_BAD_MAIN_PATH')=='1' and main_attempts==1 else str(path)
 fail=os.environ.get('NATIVE_TEST_OUTER_REPAIR')=='1' and len(list((task/'calls').glob('*-main')))==1
 value={'status':'FAILED' if fail else 'COMPLETED','summary':'fixture failure' if fail else 'done','artifacts':[artifact],'limitations':[],'questions':[]}
elif role=='repair':
 assert args[args.index('--sandbox')+1] in {'workspace-write','danger-full-access'}
 failure=next((task/'repairs').glob('*/failure.json'))
 value={'status':'FIXED','summary':'Resume with corrected command','diagnosis':'Fixture repair','evidence_paths':[str(failure)],'resume_from':'main','changed_files':[]}
else:
 assert args[args.index('--sandbox')+1] in {'read-only','danger-full-access'}
 path=task/'workspace/result.txt'; assert path.read_text()=='native result'
 repair=os.environ.get('NATIVE_TEST_REPAIR')=='1' and len(list((task/'calls').glob('*-review')))==1
 bad_path=os.environ.get('NATIVE_TEST_BAD_REVIEW_PATH')=='1' and len(list((task/'calls').glob('*-review')))==1
 missing_evidence=os.environ.get('NATIVE_TEST_MISSING_REVIEW_EVIDENCE')=='1' and len(list((task/'calls').glob('*-review')))==1
 evidence=[] if missing_evidence else ['missing/evidence.json'] if bad_path else [str(path)]
 value={'status':'REPAIR' if repair else 'PASS','summary':'reviewed','findings':['supported'],'evidence':evidence,'repair_request':'correct explanation' if repair else '','limitations':[]}
output.write_text(json.dumps(value))
sid=args[args.index('resume')+1] if 'resume' in args else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':sid}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':4,'output_tokens':2,'reasoning_output_tokens':1}}),flush=True)
'''


class NativeWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.fake = self.root/'codex'; self.fake.write_text(FAKE); self.fake.chmod(0o755)
        self.task = Task.create(self.root, {'goal':'test native task','codex_binary':str(self.fake),'python':sys.executable,'sandbox_backend':'none'})

    def tearDown(self): self.tmp.cleanup()

    def test_native_main_review_and_report(self):
        result = run(self.task)
        self.assertEqual(result['status'], 'COMPLETED', result)
        self.assertEqual(result['calls'], 2)
        self.assertEqual(self.task.request()['model'], 'gpt-6-luna')
        self.assertTrue((self.task.path/'reports/report.html').is_file())
        self.assertEqual(token_usage(self.task)['input_tokens'], 20)
        self.assertTrue(all(row['source_sha256']==row['snapshot_sha256'] for row in json.loads((self.task.path/'skills.json').read_text())['skills']))
        prompt=(self.task.path/'calls/0001-main/prompt.txt').read_text()
        self.assertIn('max_science_seconds=0',prompt)
        self.assertIn('scripts may depend on the original Core package layout',prompt)
        self.assertIn('--root '+str(self.root)+' process run',prompt)

    def test_markdown_artifact_path_is_normalized(self):
        artifact=self.task.path/'workspace/result.txt';artifact.write_text('ok')
        self.assertEqual(_paths(self.task,['[result]('+str(artifact)+')'])[0]['path'],str(artifact))
        self.assertEqual(_paths(self.task,['Evidence `'+str(artifact)+'` supports the result.'])[0]['path'],str(artifact))

    def test_relative_paths_support_task_root_and_workspace(self):
        request=self.task.path/'request.json';request.write_text('{}')
        result=self.task.path/'workspace/result.json';result.write_text('{}')
        self.assertEqual(_paths(self.task,['request.json'])[0]['path'],str(request))
        self.assertEqual(_paths(self.task,['workspace/result.json'])[0]['path'],str(result))

    def test_path_hints_resolve_typoed_task_id_only_as_a_suggestion(self):
        receipt=self.task.path/'processes/audit/receipt.json';receipt.parent.mkdir(parents=True);receipt.write_text('{}')
        wrong=self.root/'tasks'/('f'*32)/'processes/audit/receipt.json'
        hints=_path_hints(self.task,[str(wrong)])
        self.assertEqual(hints[0]['task_local_candidates'][0]['path'],str(receipt))
        self.assertEqual(_path_hints(self.task,[str(receipt)]),[])
        with self.assertRaisesRegex(ValueError,'TASK_ARTIFACT_MISSING_OR_OUTSIDE_TASK'):
            _paths(self.task,[str(wrong)])

    def test_review_path_error_retries_review_without_repeating_main(self):
        with patch.dict(os.environ, {'NATIVE_TEST_BAD_REVIEW_PATH':'1'}): result=run(self.task)
        self.assertEqual(result['status'],'COMPLETED',result)
        self.assertEqual(result['calls'],3)
        self.assertEqual(len(list((self.task.path/'calls').glob('*-main'))),1)
        self.assertEqual(len(list((self.task.path/'calls').glob('*-review'))),2)
        self.assertEqual(len(list((self.task.path/'processes').glob('*/invocation.json'))),0)

    def test_review_missing_evidence_retries_review_without_repeating_main(self):
        with patch.dict(os.environ, {'NATIVE_TEST_MISSING_REVIEW_EVIDENCE':'1'}): result=run(self.task)
        self.assertEqual(result['status'],'COMPLETED',result)
        self.assertEqual(result['calls'],3)
        self.assertEqual(len(list((self.task.path/'calls').glob('*-main'))),1)
        self.assertEqual(len(list((self.task.path/'calls').glob('*-review'))),2)

    def test_main_path_error_retries_artifact_list_without_repeating_science(self):
        with patch.dict(os.environ, {'NATIVE_TEST_BAD_MAIN_PATH':'1'}): result=run(self.task)
        self.assertEqual(result['status'],'COMPLETED',result)
        self.assertEqual(result['calls'],3)
        self.assertEqual(len(list((self.task.path/'calls').glob('*-main'))),2)
        self.assertEqual(len(list((self.task.path/'calls').glob('*-review'))),1)
        self.assertIn('task_local_candidates',(self.task.path/'calls/0002-main/prompt.txt').read_text())

    def test_process_idempotency_and_conflict(self):
        command = [sys.executable, '-c', 'print("once")']
        start(self.task, 'same', command, '.')
        receipt = wait(self.task, 'same')
        self.assertEqual(receipt['status'], 'COMPLETED')
        self.assertEqual(start(self.task, 'same', command, '.')['status'], 'COMPLETED')
        with self.assertRaisesRegex(ValueError, 'PROCESS_ID_CONFLICT'):
            start(self.task, 'same', [sys.executable, '-c', 'print("twice")'], '.')

    def test_process_cannot_overwrite_previous_outputs(self):
        start(self.task, 'first', [sys.executable, '-c', 'print("ok")'], '.', outputs=['results/first.json'])
        wait(self.task, 'first')
        with self.assertRaisesRegex(ValueError, 'PROCESS_OUTPUT_ALREADY_CLAIMED'):
            start(self.task, 'second', [sys.executable, '-c', 'print("no")'], '.', outputs=['results'])

    def test_modified_output_invalidates_receipt(self):
        command = [sys.executable, '-c', 'open("out.txt","w").write("original")']
        start(self.task, 'original', command, '.', outputs=['out.txt']); self.assertEqual(wait(self.task, 'original')['status'], 'COMPLETED')
        (self.task.path/'workspace/out.txt').write_text('changed')
        self.assertEqual(inspect(self.task, 'original')['status'], 'INVALIDATED')
        self.assertEqual(run(self.task)['status'], 'BLOCKED')

    def test_review_feedback_resumes_same_main_session_without_new_science(self):
        with patch.dict(os.environ, {'NATIVE_TEST_REPAIR':'1'}): result = run(self.task)
        self.assertEqual(result['status'], 'COMPLETED', result)
        self.assertEqual(result['calls'], 4)
        self.assertEqual(len(list((self.task.path/'processes').glob('*/invocation.json'))), 0)
        ids=[]
        for path in sorted((self.task.path/'calls').glob('*-main/events.jsonl')):
            ids.append(json.loads(path.read_text().splitlines()[0])['thread_id'])
        self.assertEqual(len(ids),2);self.assertEqual(ids[0],ids[1])

    def test_outer_repair_returns_to_same_main(self):
        with patch.dict(os.environ, {'NATIVE_TEST_OUTER_REPAIR':'1'}):
            first=run(self.task); self.assertEqual(first['status'],'RESTART_REQUIRED',first)
            final=run(self.task)
        self.assertEqual(final['status'],'COMPLETED',final)
        self.assertEqual(final['repairs'],1)

    def test_unknown_never_restarts(self):
        folder = self.task.path/'processes/lost'; folder.mkdir(parents=True)
        write(folder/'invocation.json', {'process_id':'lost'})
        self.assertEqual(inspect(self.task, 'lost')['status'], 'UNKNOWN')
        self.assertEqual(run(self.task)['status'], 'BLOCKED')
        self.assertEqual(self.task.state()['calls'], 0)

    def test_scientific_process_survives_initiator_exit(self):
        code = ('import os,sys;from enzyme_harness.store import Task;'
                'from enzyme_harness.native_process import start;'
                't=Task(sys.argv[1],sys.argv[2]);'
                'start(t,"long",[sys.executable,"-c",'
                '\'import time;time.sleep(1);open("finished.txt","w").write("ok")\'],".",outputs=["finished.txt"]);'
                'os._exit(0)')
        initiating = subprocess.run([sys.executable, '-c', code, str(self.root), self.task.id], check=False)
        self.assertEqual(initiating.returncode, 0)
        result = wait(self.task, 'long')
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual((self.task.path/'workspace/finished.txt').read_text(), 'ok')

    def test_controller_crash_reconnects_existing_codex(self):
        env=dict(os.environ);env['NATIVE_TEST_CONTROLLER_KILL']='1'
        code='import sys;from enzyme_harness.store import Task;from enzyme_harness.native import run;run(Task(sys.argv[1],sys.argv[2]))'
        proc=subprocess.Popen([sys.executable,'-c',code,str(self.root),self.task.id],env=env)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and not self.task.state().get('pending_native'):time.sleep(.05)
        self.assertIsNotNone(self.task.state().get('pending_native'))
        proc.kill();proc.wait()
        result=run(self.task)
        self.assertEqual(result['status'],'COMPLETED',result)
        self.assertEqual(result['calls'],2)

    def test_codex_crash_waits_for_detached_process_then_resumes(self):
        with patch.dict(os.environ, {'NATIVE_TEST_CODEX_KILL':'1'}):
            first=run(self.task)
            self.assertEqual(first['status'],'BLOCKED',first)
            final=run(self.task)
        self.assertEqual(final['status'],'COMPLETED',final)
        self.assertEqual((self.task.path/'workspace/detached.txt').read_text(),'ok')
        self.assertEqual(inspect(self.task,'detached')['status'],'COMPLETED')
        self.assertEqual(len(list((self.task.path/'processes').glob('*/invocation.json'))),1)

    def test_completed_output_before_state_commit_is_recovered(self):
        path=self.task.path/'workspace/result.txt';path.write_text('native result')
        folder=self.task.path/'calls/0001-main';folder.mkdir(parents=True)
        sid='11111111-1111-4111-8111-111111111111'
        (folder/'events.jsonl').write_text('\n'.join(json.dumps(x) for x in [{'type':'thread.started','thread_id':sid},{'type':'turn.completed','usage':{'input_tokens':1}}]))
        write(folder/'output.json',{'status':'COMPLETED','summary':'done','artifacts':[str(path)],'limitations':[],'questions':[]})
        self.task.update(calls=1,pending_native={'role':'main','folder':str(folder),'identity':{'pid':999999,'started':0,'boot_id':'missing'}})
        result=run(self.task)
        self.assertEqual(result['status'],'COMPLETED',result)
        self.assertEqual(result['calls'],2)

    def test_explicit_unenforced_science_budget_rejected(self):
        with self.assertRaisesRegex(ValueError, 'NATIVE_BUDGET_UNSUPPORTED'):
            Task.create(self.root, {'goal':'limited','budget':{'max_scientific_runs':1}})

    def test_usage_deduplicates_resumed_session(self):
        for n, value in [(1,10),(2,15)]:
            folder=self.task.path/'calls'/f'{n:04d}-main';folder.mkdir(parents=True)
            (folder/'events.jsonl').write_text('\n'.join(json.dumps(x) for x in [
                {'type':'thread.started','thread_id':'same'},
                {'type':'turn.completed','usage':{'input_tokens':value,'cached_input_tokens':value-2,'output_tokens':value,'reasoning_output_tokens':1}}]))
        self.assertEqual(token_usage(self.task)['input_tokens'],15)

    def test_legacy_status_and_report_are_read_only(self):
        from enzyme_harness.cli import main, status_view
        self.task.update(workflow='agent-led/v1'); before=sha(self.task.path/'state.json')
        self.assertTrue(status_view(self.task)['activity']['legacy_read_only'])
        self.assertEqual(main(['--root',str(self.root),'task','report',self.task.id]),0)
        self.assertEqual(sha(self.task.path/'state.json'),before)
        self.assertTrue((self.root/'legacy_reports'/self.task.id/'report.html').is_file())




if __name__ == '__main__': unittest.main()
