"""Portable report from native agent results and process receipts."""
from pathlib import Path
import html
import json
import shutil
from .agents import records_for
from .agents.dsh import normalize_usage
from .files import load, sha, write
from .native import managed

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')


def token_usage(task):
    records = records_for(task)
    sessions = {}
    dsh_calls = {}
    dsh_session_ids = set()
    codex_sessions = set()
    tools = {}
    incomplete = []
    calls = 0
    unconfined = 0
    agents = set()
    for file in sorted(records.glob('*/events.jsonl')):
        calls += 1
        spec = load(file.parent / 'bootstrap.json', {})
        agent = spec.get('agent') or 'codex'
        agents.add(agent)
        if spec.get('unconfined'): unconfined += 1
        session = None; usage = None
        for line in file.read_text(errors='replace').splitlines():
            try: event = json.loads(line)
            except ValueError: continue
            if agent == 'codex':
                if event.get('type') == 'thread.started': session = event.get('thread_id')
                if event.get('type') == 'turn.completed': usage = event.get('usage')
                item = event.get('item', {})
                if event.get('type') == 'item.completed' and item.get('type') in {'command_execution', 'file_change', 'mcp_tool_call', 'web_search'}:
                    kind = item['type']; tools[kind] = tools.get(kind, 0) + 1
            else:
                if event.get('type') == 'session': session = event.get('sessionId')
                if event.get('type') == 'final' and event.get('sessionId'): session = event.get('sessionId')
                if event.get('type') == 'status' and event.get('phase') == 'step_end':
                    part = normalize_usage(event.get('usage'))
                    if part is not None:
                        usage = {key: (usage or {}).get(key, 0) + part[key] for key in FIELDS}
                if event.get('type') == 'tool_call':
                    kind = str(event.get('tool') or 'tool'); tools[kind] = tools.get(kind, 0) + 1
        if agent == 'codex' and session and usage:
            previous = sessions.get(session, {})
            sessions[session] = {key: max(previous.get(key, 0), usage.get(key, 0)) for key in
                                 ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')}
            codex_sessions.add(session)
        elif agent == 'codex':
            incomplete.append(file.parent.name)
        elif agent != 'codex' and session and usage:
            dsh_calls[file.parent.name] = usage
            dsh_session_ids.add(session)
        elif agent != 'codex':
            incomplete.append(file.parent.name)
    result = {key: sum(x.get(key, 0) for x in sessions.values()) for key in FIELDS}
    if dsh_calls:
        for key in FIELDS:
            result[key] += sum(x.get(key, 0) for x in dsh_calls.values())
    result['uncached_input_tokens'] = result['input_tokens'] - result['cached_input_tokens']
    result['session_count'] = len(codex_sessions) + len(dsh_session_ids)
    result['cli_calls'] = calls
    result['tool_calls'] = tools
    result['incomplete_usage_calls'] = incomplete
    result['unconfined_calls'] = unconfined
    result['agents'] = sorted(agents)
    return result


def agent_versions(task):
    versions = set()
    for spec in records_for(task).glob('*/bootstrap.json'):
        value = load(spec, {}).get('version')
        if value: versions.add(str(value))
    return sorted(versions)


def render(task):
    state = task.state(); folder = task.path / 'reports'; folder.mkdir(exist_ok=True)
    execution = load(task.path / state['execution']['path']) if state.get('execution') else None
    review = load(task.path / state['review']['path']) if state.get('review') else None
    request = task.request()
    links = []
    for item in (execution or {}).get('artifacts', []):
        source = Path(item['path'])
        if sha(source) != item['sha256']: raise ValueError('REPORT_ARTIFACT_CHANGED:' + str(source))
        target = folder / 'artifacts' / item['sha256'] / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            if source.is_dir(): shutil.copytree(source, target)
            else: shutil.copy2(source, target)
        if sha(target) != item['sha256']: raise ValueError('REPORT_COPY_CHANGED')
        links.append({'path': target.relative_to(folder).as_posix(), 'sha256': item['sha256']})
    data = {'task_id': task.id, 'status': state['status'], 'goal': request['goal'],
            'agent': request.get('agent') or 'codex', 'agent_versions': agent_versions(task),
            'model': request.get('model'), 'provider': request.get('provider'),
            'sandbox_backend': request.get('sandbox_backend') or 'auto',
            'execution': execution, 'review': review, 'processes': managed(task),
            'artifacts': links, 'token_usage': token_usage(task)}
    write(folder / 'report.json', data)
    route = '代理：' + str(data['agent'])
    if data['agent_versions']: route += '；版本：' + ', '.join(data['agent_versions'])
    route += '；模型：' + str(data['model'])
    if data['provider']: route += '；提供方：' + str(data['provider'])
    lines = ['# 任务报告', '', '任务：' + task.id + '；状态：' + state['status'], '', '## 请求', '',
             task.request()['goal'], '', '## 执行配置', '', route + '；沙箱：' + str(data['sandbox_backend']), '', '## 执行结果', '',
             (execution or {}).get('response', {}).get('summary', '尚无执行结果。'), '',
             '## 独立审查', '', (review or {}).get('summary', '尚无独立审查结果。'), '', '## 产物', '']
    lines += ['- [' + Path(x['path']).name + '](' + x['path'] + ')' for x in links]
    usage = data['token_usage']
    lines += ['', '## 资源与用量', '', json.dumps(usage, ensure_ascii=False), '']
    if usage.get('unconfined_calls'):
        lines += ['有 ' + str(usage['unconfined_calls']) + ' 次代理调用在 `sandbox_backend=none` 下不受 OS 沙箱限制；'
                  '审查产物摘要仍由控制器校验。', '']
    lines += ['计算输出与实验结果分开解释；最终结论以独立审查和原始证据为准。']
    (folder / 'report.md').write_text('\n'.join(lines) + '\n')
    body = ''.join('<p>' + html.escape(line) + '</p>' for line in lines if line and not line.startswith('- ['))
    body += '<ul>' + ''.join('<li><a href="' + html.escape(x['path'], quote=True) + '">' +
             html.escape(Path(x['path']).name) + '</a></li>' for x in links) + '</ul>'
    (folder / 'report.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>任务报告</title><body>' + body + '</body></html>')
    return str(folder / 'report.html')
