"""One fresh API submission, no retries or grading checks before both submissions."""
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent
ZONE = timezone(timedelta(hours=8))
ENDPOINT = 'https://api.deepseek.com/chat/completions'
FILES = ['阅读说明.md', '评分表_v0.2.md', '评分记录_空白.csv'] + [f'案例/CASE_{n:03d}.txt' for n in range(4, 12)]
INSTRUCTION = '''请先阅读下方阅读说明和评分表v0.2，独立完成CASE_004至CASE_011第②项评分，填写自己的评分CSV并提交首次完整记录。
只依据本次提供的11个文件。不得联网、调用工具、查看另一评分者结果或参考答案，不接受逐案例提示，不补写案例理由。评分标准和CSV列名保持原样。不要求各等级出现特定次数。
本请求没有先前聊天历史；服务端历史、训练材料或隐藏条件无法确认。开始前如实声明本轮参考答案、参考分数、管理索引和静态审查记录的接触情况，不声称已隔离。
为保存首次提交，请仅输出一个JSON对象，包含两个字段：reference_exposure_statement（评分前的接触情况声明字符串）和csv（完整CSV文本字符串，包含模板原有表头和八行记录；正确转义逗号、引号和换行）。不要输出Markdown围栏，不在CSV外添加评分记录。
案例和评分条款引文应逐字复制，保留原文标点；节选连续片段时也不得把片段末尾标点改写。位置标签与引文仍使用模板原有字段，不改表头。
JSON结构示例：{"reference_exposure_statement":"如实声明","csv":"完整CSV文本"}。
开始时间可使用下方提供的API请求起始时刻，它是批次起始时间，不是逐条生成时间。无法取得准确提交时间时按阅读说明写“未确认”，管理员另存真实响应接收和锁定时间，不编造时间。'''

def sha(data):
    return hashlib.sha256(data).hexdigest().upper()

def stamp():
    return datetime.now(ZONE).isoformat()

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def prepare(rater, started_at):
    expected = json.loads((ROOT/'input_hashes.json').read_text(encoding='utf-8'))
    folder = ROOT/rater/'input'
    actual = sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file())
    if actual != sorted(FILES):
        raise ValueError('Input whitelist mismatch')
    parts, hashes = [], {}
    for name in FILES:
        data = (folder/name).read_bytes()
        hashes[name] = sha(data)
        if hashes[name] != expected[name]:
            raise ValueError('Frozen input changed: '+name)
        parts.append('【文件：'+name+'】\n'+data.decode('utf-8-sig'))
    text = INSTRUCTION+'\n\n评分者标识：'+rater+'\nAPI请求起始时刻：'+started_at+'\n\n'+'\n\n'.join(parts)
    settings = json.loads((ROOT/'settings.json').read_text(encoding='utf-8'))
    return dict(settings, messages=[{'role':'user','content':text}]), hashes

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rater', required=True, choices=['A','B'])
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--key-env', default='WHALE_DEEPSEEK_BALANCE_KEY')
    args = parser.parse_args()
    started_at = stamp()
    payload, hashes = prepare(args.rater, started_at)
    if not args.run:
        print(json.dumps({'rater':args.rater,'mode':'offline','hashes':hashes,'settings':{k:v for k,v in payload.items() if k!='messages'},'messages':1,'tools_supplied':False,'network_calls':0}, ensure_ascii=True))
        return
    key = os.environ.get(args.key_env, '').strip()
    if not key or '\n' in key or '\r' in key:
        raise ValueError('Credential unavailable; no request sent')
    folder = ROOT/args.rater/'submission'
    folder.mkdir(exist_ok=False)
    save(folder/'request.json', payload)
    meta = {'rater_id':args.rater,'endpoint':ENDPOINT,'model_requested':'DeepSeek-V4.1-Flash','api_model':'deepseek-flash',
            'settings':{k:v for k,v in payload.items() if k!='messages'},'material_hashes':hashes,
            'started_at':started_at,'attempts':1,'retries':0,'history_messages':0,'tools_supplied':False,
            'parent_context_submitted':False,'reference_answers_submitted':False,'opponent_results_submitted':False,
            'service_side_memory_and_hidden_settings':'未确认','seed':'不可控（未提供）',
            'time_definition':'started_at为API批次请求起始；received_at和locked_at为客户端实际时间；CSV提交时间无法确认时允许未确认'}
    save(folder/'metadata.json', meta)
    request = urllib.request.Request(ENDPOINT, method='POST', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
              headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    timer = time.perf_counter()
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=300) as response:
            raw = response.read()
            meta['http_status'] = response.status
        meta['received_at'] = stamp()
        (folder/'first_response.json').write_bytes(raw)
        result = json.loads(raw)
        meta['model_returned'] = result.get('model','未确认')
        meta['system_fingerprint'] = result.get('system_fingerprint','未确认')
        meta['usage'] = result.get('usage',{})
        choice = result['choices'][0]
        msg = choice['message']
        meta['finish_reason'] = choice.get('finish_reason')
        meta['tool_calls'] = msg.get('tool_calls') or msg.get('function_call') or []
        content = msg.get('content') or ''
        (folder/'first_output.txt').write_bytes(content.encode('utf-8'))
        (folder/'first_reasoning.txt').write_bytes((msg.get('reasoning_content') or '').encode('utf-8'))
        envelope = json.loads(content)
        save(folder/'original_submission.json', envelope)
        if not isinstance(envelope.get('csv'), str):
            raise ValueError('First output does not contain a CSV string; no retry')
        # Extract the model-authored CSV verbatim; do not repair, validate or rescore here.
        (folder/'submitted.csv').write_bytes(envelope['csv'].encode('utf-8-sig'))
    except urllib.error.HTTPError as exc:
        meta.update(http_status=exc.code,error='HTTP failure; no retry')
        (folder/'first_error.txt').write_bytes(exc.read())
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        meta['error'] = type(exc).__name__+': '+str(exc).replace(key,'[REDACTED]')
    finally:
        meta['elapsed_seconds'] = round(time.perf_counter()-timer,3)
        meta.setdefault('received_at', stamp())
        save(folder/'metadata.json',meta)
        save(folder/'submission_lock.json',{'rater_id':args.rater,'locked_at':stamp(),
             'files':[{'file':p.name,'sha256':sha(p.read_bytes())} for p in sorted(folder.iterdir()) if p.is_file()],
             'note':'首次原始提交；后续检查、讨论、裁定另存，不覆盖。'})
    print(json.dumps({'rater':args.rater,'http_status':meta.get('http_status'),'finish_reason':meta.get('finish_reason'),
         'error_type':meta.get('error','').split(':')[0] or None,'csv_saved':(folder/'submitted.csv').exists(),'locked':True}, ensure_ascii=True))

if __name__ == '__main__':
    main()
