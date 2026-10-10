"""One fresh DeepSeek API grading request per rater; default offline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT.parent / '评分者测试材料'
ENDPOINT = 'https://api.deepseek.com/chat/completions'
FILES = ['阅读说明.md', '评分表_v0.2.md', '共同材料.md', '分歧处理方法.md',
         '案例/CASE_001.txt', '案例/CASE_002.txt', '案例/CASE_003.txt']
ZONE = timezone(timedelta(hours=8))
INSTRUCTION = '''请使用本次提供的测试材料，独立评价三个CASE在评分表v0.2第②项“是否提供有依据的候选比较”上的表现。
先阅读统一说明。评分前如实说明是否曾接触本次案例的参考答案或判分依据；可区分本次请求提供情况与无法确认的历史/训练情况，不要声称已隔离。不得查看参考答案、与另一评分者讨论或接受逐案例提示。
每个案例记录CASE编号、数字分数或状态、支持判分的原句、判分理由和记录时间。遇到材料不足或适用条件问题，按评分表处理并注明原因，不替案例补写理由。
不得联网或调用工具，只依据本次正文提供的测试材料。只评价第②项，不判断真实地点或其他评分项。不要推测各分数的出现次数。提交首次完整记录后停止，不追加或修改记录。
为准确保存原始记录，请仅输出一个JSON对象，不要加Markdown围栏：
{
 "reference_exposure_statement": "评分前的如实声明",
 "records": [
  {
   "case_id": "CASE编号", "score_item": "②",
   "applicability": "适用/不适用/无法确认",
   "status": "数字评分/不适用/未评",
   "score": "实际为0、1、2的整数；非数字状态时为null",
   "supporting_quotes": [{"text":"逐字原句或未说明", "location":"字段或检查范围"}],
   "rubric_quotes": ["实际使用的v0.2条款原句"],
   "reason": "判分理由；非数字状态注明原因",
   "recorded_at": "运行器提供的记录时间"
  }
 ]
}
recorded_at采用下方提供的API评分请求起始时刻，表示本次记录批次的开始时间，不冒充逐条生成时刻。其他时间未知时不编造。'''

def sha(data):
    return hashlib.sha256(data).hexdigest().upper()

def save(path, obj):
    path.write_bytes(json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8'))

def prepare(rater, stamp):
    manifest = json.loads((PUBLIC/'材料清单.json').read_text(encoding='utf-8'))
    expected = {x['file']: x['sha256'] for x in manifest['files']}
    parts, hashes, texts = [], {}, {}
    for name in FILES:
        data = (PUBLIC/name).read_bytes()
        if sha(data) != expected[name]:
            raise ValueError('Frozen material hash mismatch: '+name)
        texts[name] = data.decode('utf-8-sig')
        hashes[name] = sha(data)
        parts.append('【'+name+'】\n'+texts[name])
    text = INSTRUCTION+'\n\n评分者标识：'+rater+'\n记录时间（请求起始时刻）：'+stamp+'\n\n'+'\n\n'.join(parts)
    payload = {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': text}],
               'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low', 'max_tokens': 8192,
               'stream': False, 'tool_choice': 'none', 'response_format': {'type': 'json_object'}}
    return payload, hashes, texts

def validate(value, texts, stamp):
    issues = []
    records = value.get('records', [])
    if {r.get('case_id') for r in records} != {'CASE_001','CASE_002','CASE_003'} or len(records)!=3:
        issues.append('Missing, extra, or duplicate case record')
    if not value.get('reference_exposure_statement'):
        issues.append('Missing exposure statement')
    for r in records:
        cid = r.get('case_id')
        case = texts.get('案例/'+str(cid)+'.txt', '')
        score, state, app = r.get('score'), r.get('status'), r.get('applicability')
        if state == '数字评分':
            if type(score) is not int or score not in (0,1,2) or app!='适用':
                issues.append(str(cid)+': inconsistent numeric score/state')
        elif state in ('未评','不适用'):
            if score is not None:
                issues.append(str(cid)+': nonnumeric state with number')
        else:
            issues.append(str(cid)+': invalid processing state')
        if r.get('score_item') != '②' or not r.get('reason'):
            issues.append(str(cid)+': item or reason missing')
        if r.get('recorded_at') != stamp:
            issues.append(str(cid)+': supplied recording time not preserved')
        if not r.get('supporting_quotes') or not r.get('rubric_quotes'):
            issues.append(str(cid)+': quotation missing')
        for quote in r.get('supporting_quotes', []):
            if quote.get('text')!='未说明' and quote.get('text','') not in case:
                issues.append(str(cid)+': supporting quote differs from case')
        for quote in r.get('rubric_quotes', []):
            if quote not in texts['评分表_v0.2.md']:
                issues.append(str(cid)+': rubric quote differs from source')
    return issues

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rater', required=True, choices=['R01','R02'])
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--key-env', default='WHALE_DEEPSEEK_BALANCE_KEY')
    args = parser.parse_args()
    stamp = datetime.now(ZONE).isoformat()
    payload, hashes, texts = prepare(args.rater, stamp)
    if not args.run:
        print(json.dumps({'rater':args.rater, 'mode':'offline', 'material_hashes':hashes,
                          'model':payload['model'], 'messages':1, 'tools_supplied':False,
                          'input_files':FILES, 'network_requests':0}, ensure_ascii=True))
        return
    key = os.environ.get(args.key_env, '').strip()
    if not key or '\n' in key or '\r' in key:
        raise ValueError('Credential not available; no request sent')
    folder = ROOT / args.rater
    folder.mkdir(exist_ok=False)
    save(folder/'request.json', payload)
    meta = {'rater_id':args.rater, 'model_requested':'DeepSeek-V4.1-Flash', 'api_model':'deepseek-flash',
            'endpoint':ENDPOINT, 'recorded_at':stamp, 'retries':0, 'attempts':1,
            'settings':{k:v for k,v in payload.items() if k!='messages'},
            'material_hashes':hashes, 'history_messages':0, 'tools_supplied':False,
            'parent_context_submitted':False, 'reference_answers_submitted':False,
            'service_side_memory_and_hidden_settings':'未确认', 'seed':'不可控（未提供）',
            'recording_time_definition':'API请求起始时刻；非逐条生成时刻'}
    save(folder/'metadata.json',meta)
    request = urllib.request.Request(ENDPOINT, method='POST', data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
             headers={'Authorization':'Bearer '+key, 'Content-Type':'application/json'})
    started = time.perf_counter()
    raw = None
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=300) as response:
            raw = response.read()
            meta['http_status'] = response.status
        (folder/'first_response.json').write_bytes(raw)
        result = json.loads(raw)
        meta['model_returned'] = result.get('model','未确认')
        meta['system_fingerprint'] = result.get('system_fingerprint','未确认')
        meta['usage'] = result.get('usage',{})
        choice = result['choices'][0]
        message = choice['message']
        meta['finish_reason'] = choice.get('finish_reason')
        meta['tool_calls'] = message.get('tool_calls') or message.get('function_call') or []
        content = message.get('content') or ''
        (folder/'first_output.txt').write_bytes(content.encode('utf-8'))
        (folder/'first_reasoning.txt').write_bytes((message.get('reasoning_content') or '').encode('utf-8'))
        decoded = json.loads(content)
        save(folder/'original_records.json', decoded)
        meta['format_checks'] = validate(decoded, texts, stamp)
    except urllib.error.HTTPError as exc:
        meta.update(http_status=exc.code, error='HTTP failure; no retry')
        (folder/'first_error.txt').write_bytes(exc.read())
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        meta['error'] = type(exc).__name__+': '+str(exc).replace(key,'[REDACTED]')
    finally:
        meta['elapsed_seconds'] = round(time.perf_counter()-started,3)
        meta['submitted_at'] = datetime.now(ZONE).isoformat()
        save(folder/'metadata.json',meta)
        originals = ['request.json','first_response.json','first_output.txt','first_reasoning.txt','original_records.json','first_error.txt','metadata.json']
        save(folder/'submission_lock.json',{'rater_id':args.rater,'locked_at':datetime.now(ZONE).isoformat(),
                'files':[{'file':name,'sha256':sha((folder/name).read_bytes())} for name in originals if (folder/name).exists()],
                'note':'First submission preserved; any later discussion or adjudication must be separate.'})
    print(json.dumps({'rater':args.rater,'saved':str(folder),'http_status':meta.get('http_status'),
                      'error_type':meta.get('error','').split(':')[0] or None,
                      'finish_reason':meta.get('finish_reason'),'locked':True,
                      'format_issue_count':len(meta.get('format_checks',[]))},ensure_ascii=True))

if __name__ == '__main__':
    main()
