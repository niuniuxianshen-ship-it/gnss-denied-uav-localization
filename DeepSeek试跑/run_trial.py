"""Independent DeepSeek image trials; default is offline and sends no requests."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import time
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error
import zlib

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://api.deepseek.com/chat/completions'
IMAGE_HASH = 'D6C75712367A7BCA76B176A9670ED5F7328A800FED063617AEEF4140F30F9288'
APPENDIX_HASH = '8DCFC001DDE496A16DD15335CEBC67846252DF68DF2AA864D35274542A287D37'
RULE = '三版均可依据画面中可见的信息、共同参考附录和模型已有地理知识进行判断，但不得联网、读取图片元数据或答案文件；模型已有地理知识不自动视为已验证事实，共同附录缺少相关条目也不自动构成必须拒答的理由。'
FIELDS = {
 'A': ['省级行政区', '置信分数'],
 'B': ['可见线索', '不可读或无法辨认的内容', '支持判断的线索', '冲突线索', '省级行政区', '置信分数'],
 'C': ['可见线索', '不可读或无法辨认的内容', '候选排序及支持依据', '区分候选所需的证据', '冲突线索', '最终回答', '第一候选置信分数']}
PROVINCES = set('北京市 天津市 河北省 山西省 内蒙古自治区 辽宁省 吉林省 黑龙江省 上海市 江苏省 浙江省 安徽省 福建省 江西省 山东省 河南省 湖北省 湖南省 广东省 广西壮族自治区 海南省 重庆市 四川省 贵州省 云南省 西藏自治区 陕西省 甘肃省 青海省 宁夏回族自治区 新疆维吾尔自治区'.split())

def sha(data):
    return hashlib.sha256(data).hexdigest().upper()

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def check_png(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not PNG')
    pos, chunks, pixels, ihdr = 8, [], bytearray(), None
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError('Truncated PNG')
        size = struct.unpack('>I', data[pos:pos+4])[0]
        kind = data[pos+4:pos+8]
        body = data[pos+8:pos+8+size]
        end = pos + size + 12
        if end > len(data) or zlib.crc32(kind+body) & 0xffffffff != struct.unpack('>I', data[end-4:end])[0]:
            raise ValueError('PNG CRC/size mismatch')
        chunks.append(kind.decode('ascii'))
        if kind not in (b'IHDR', b'IDAT', b'IEND'):
            raise ValueError('PNG contains ancillary/metadata chunks')
        if kind == b'IHDR':
            ihdr = struct.unpack('>IIBBBBB', body)
        if kind == b'IDAT':
            pixels.extend(body)
        pos = end
        if kind == b'IEND':
            break
    if pos != len(data) or not chunks or chunks[0] != 'IHDR' or chunks[-1] != 'IEND' or chunks.count('IHDR') != 1 or chunks.count('IEND') != 1:
        raise ValueError('Invalid PNG structure')
    if ihdr != (3000, 2000, 8, 2, 0, 0, 0):
        raise ValueError('Unexpected image dimensions/encoding')
    decoded = zlib.decompress(pixels)
    if len(decoded) != 2000 * (3000*3+1):
        raise ValueError('Invalid decoded image size')
    return {'dimensions': [3000, 2000], 'chunks': sorted(set(chunks)), 'crc_and_decompression': 'pass'}

def load_inputs():
    image = (ROOT/'inputs'/'001.png').read_bytes()
    appendix = (ROOT/'inputs'/'appendix.md').read_bytes()
    if sha(image) != IMAGE_HASH or sha(appendix) != APPENDIX_HASH:
        raise ValueError('Frozen image/appendix hash mismatch')
    png = check_png(image)
    prompts = {v: (ROOT/'prompts'/f'{v}.txt').read_text(encoding='utf-8-sig') for v in 'ABC'}
    for p in prompts.values():
        if p.count(RULE) != 1 or '只能依据画面中可见的信息及参考附录' in p:
            raise ValueError('Shared information rule missing or contradicted')
    return image, appendix, prompts, png

def build(prompt, appendix, image):
    return {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': prompt+'\n\n'+appendix.decode('utf-8-sig')},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,'+base64.b64encode(image).decode('ascii'), 'detail': 'original'}}]}],
        'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low', 'max_tokens': 4096,
        'stream': False, 'tool_choice': 'none', 'response_format': {'type': 'text'}}

def inspect_response(value, variant):
    choices = value.get('choices', [])
    if len(choices) != 1 or not isinstance(choices[0].get('message'), dict):
        raise ValueError('Expected one completion message')
    choice, issues = choices[0], []
    message = choice['message']
    text = message.get('content')
    if text is None:
        text = ''
    if not isinstance(text, str):
        raise ValueError('content is not text')
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    expected = FIELDS[variant]
    parsed = {}
    labels = []
    for line in lines:
        label, sep, body = line.partition('：')
        if not sep:
            issues.append('非字段行')
            continue
        labels.append(label)
        if label in parsed:
            issues.append('重复字段：'+label)
        parsed[label] = body.strip()
    if labels != expected:
        issues.append('字段缺失、额外内容或顺序不符')
    if any(not parsed.get(f) for f in expected):
        issues.append('空字段或缺失字段')
    answer = parsed.get('最终回答' if variant == 'C' else '省级行政区', '')
    confidence = parsed.get('第一候选置信分数' if variant == 'C' else '置信分数', '')
    if answer not in PROVINCES and not (variant == 'C' and answer == '无法判断'):
        issues.append('最终回答不是完整省级行政区或允许的拒答')
    valid_score = bool(re.fullmatch(r'(?:100|[1-9]?\d)(?:\.\d+)?', confidence)) and float(confidence) <= 100
    no_candidate = variant == 'C' and parsed.get('候选排序及支持依据', '').startswith('无有依据的候选')
    if not valid_score and not (no_candidate and confidence == '不适用'):
        issues.append('置信分数格式或范围不符')
    if no_candidate and confidence != '不适用':
        issues.append('无候选时置信分数应为不适用')
    tools = message.get('tool_calls') or message.get('function_call')
    tool_detected = bool(tools) or choice.get('finish_reason') == 'tool_calls'
    return {'answer': answer, 'confidence': confidence, 'format_issues': issues,
        'tool_calls': tools or [], 'tool_detected': tool_detected,
        'tool_note': '仅依据返回字段；服务端隐藏行为未确认',
        'finish_reason': choice.get('finish_reason'),
        'truncated_or_abnormal': choice.get('finish_reason') != 'stop',
        'model_returned': value.get('model', '未确认'),
        'system_fingerprint': value.get('system_fingerprint', '未确认'), 'usage': value.get('usage', {})}

def offline():
    image, appendix, prompts, png = load_inputs()
    sizes, normalized, checks = {}, [], []
    for v, p in prompts.items():
        body = build(p, appendix, image)
        parts = body['messages'][0]['content']
        assert len(body['messages']) == 1 and body['messages'][0]['role'] == 'user'
        assert base64.b64decode(parts[1]['image_url']['url'].split(',', 1)[1]) == image
        assert parts[0]['text'] == p+'\n\n'+appendix.decode('utf-8-sig')
        assert 'tools' not in body and body['tool_choice'] == 'none'
        sizes[v] = len(json.dumps(body, ensure_ascii=False).encode('utf-8'))
        assert sizes[v] < 48*1024*1024 and len(image) < 32*1024*1024
        normalized.append({k: val for k, val in body.items() if k != 'messages'})
    assert normalized[0] == normalized[1] == normalized[2]
    sample = {'model': 'fixture-not-a-model-run', 'choices': [{'message': {'content': '省级行政区：北京市\n置信分数：50'}, 'finish_reason': 'stop'}]}
    assert not inspect_response(sample, 'A')['format_issues']
    sample['choices'][0]['finish_reason'] = 'length'
    assert inspect_response(sample, 'A')['truncated_or_abnormal']
    sample['choices'][0]['message']['tool_calls'] = [{'type': 'function'}]
    assert inspect_response(sample, 'A')['tool_detected']
    sample['choices'][0]['message']['content'] = '省级行政区：北京\n置信分数：101'
    assert len(inspect_response(sample, 'A')['format_issues']) >= 2
    sample['choices'][0]['message']['content'] = '可见线索：测试\n不可读或无法辨认的内容：测试\n候选排序及支持依据：无有依据的候选\n区分候选所需的证据：测试\n冲突线索：未发现\n最终回答：无法判断\n第一候选置信分数：不适用'
    assert not inspect_response(sample, 'C')['format_issues']
    checks = ['PNG CRC、解压、尺寸及无附属块', '冻结图片/附录哈希', '三版共用规则无矛盾', '实际图片Base64往返', '一次单user消息且无历史', '参数一致及请求大小', '标准正文解析', 'length截断检测', 'tool_calls违规检测', '行政区/分数错误检测', 'C无候选拒答解析']
    result = {'mode': 'offline', 'network_requests': 0, 'model_runs': 0, 'passed_checks': checks,
        'image_sha256': sha(image), 'appendix_sha256': sha(appendix), 'png': png,
        'request_bytes': sizes, 'settings': normalized[0],
        'prompt_sha256': {v: sha(p.encode('utf-8')) for v,p in prompts.items()},
        'shared_rule': RULE, 'environment': {'service_side_memory': '未确认', 'hidden_instructions': '未确认', 'server_image_processing': '未确认', 'seed': '不可控（未发送）', 'temperature': '思考模式文档说明无效，未发送'}}
    save(ROOT/'离线检查.json', result)
    return result

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused; credentials may only go to official endpoint')

def run(key):
    image, appendix, prompts, _ = load_inputs()
    folder = ROOT/'runs'/datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((ROOT/'离线检查.json').read_text(encoding='utf-8'))
    manifest.update(mode='live', endpoint=ENDPOINT, retries=0, timeout_seconds=300, context='each request one user message; no previous response', isolation='未确认')
    save(folder/'manifest.json', manifest)
    summary = []
    opener = urllib.request.build_opener(NoRedirect())
    for v, p in prompts.items():
        body = build(p, appendix, image)
        (folder/f'{v}_input.txt').write_bytes(body['messages'][0]['content'][0]['text'].encode('utf-8'))
        audit = json.loads(json.dumps(body))
        audit['messages'][0]['content'][1]['image_url']['url'] = '<inline 001.png; sha256='+sha(image)+'>'
        save(folder/f'{v}_request_audit.json', audit)
        record = {'variant': v, 'started_at': datetime.now().astimezone().isoformat(), 'attempts': 1}
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode('utf-8'), headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'}, method='POST')
        print('Starting '+v, flush=True)
        started = time.perf_counter()
        failed = False
        try:
            with opener.open(request, timeout=300) as response:
                raw = response.read()
                record['http_status'] = response.status
                record['request_id'] = response.headers.get('x-request-id', '未提供')
            (folder/f'{v}_response.json').write_bytes(raw)
            value = json.loads(raw)
            choice = value['choices'][0]['message']
            (folder/f'{v}_output.txt').write_bytes((choice.get('content') or '').encode('utf-8'))
            (folder/f'{v}_reasoning.txt').write_bytes((choice.get('reasoning_content') or '').encode('utf-8'))
            record.update(inspect_response(value, v))
        except urllib.error.HTTPError as exc:
            record.update(http_status=exc.code, error='HTTP error; no retry')
            (folder/f'{v}_error.txt').write_bytes(exc.read())
            failed = True
        except (OSError, ValueError, KeyError, IndexError) as exc:
            record['error'] = type(exc).__name__+'; response/transport failure; no retry'
            record['error_detail'] = str(exc).replace(key, '[REDACTED]')
            failed = True
        record['elapsed_seconds'] = round(time.perf_counter()-started, 3)
        summary.append(record)
        save(folder/'summary.json', summary)
        if failed:
            print('Request failure retained; stopping before further requests.', flush=True)
            break
    rows = ['|版本|模型及设置|最终回答|置信分数|工具调用|格式/截断|耗时秒|', '|--|--|--|--|--|--|--|']
    for r in summary:
        rows.append('|'+ '|'.join([r['variant'], str(r.get('model_returned','未确认'))+'；low；4096；original',r.get('answer','未获得'),r.get('confidence','未获得'), '检测到' if r.get('tool_detected') else ('未发现；服务端未确认' if 'finish_reason' in r else '未确认'), str(r.get('format_issues',r.get('error',[])))+'；结束='+str(r.get('finish_reason','未确认')),str(r['elapsed_seconds'])])+'|')
    for v in 'ABC':
        if not any(r['variant']==v for r in summary):
            rows.append(f'|{v}|未运行|未获得|未获得|未确认|前序请求失败后停止|—|')
    text = '# 首次试跑记录\n\n'+'\n'.join(rows)+'\n\n每版最多一次请求，无纠正或重试。只提交匿名图片、版本提示词、共同附录；运行器未接入搜索、文件工具、技能或当前对话。服务端共享记忆、自定义指令、隐藏工具与图片处理未确认，不能声明已隔离。原始响应含服务返回的推理字段，正文单独保存。\n'
    for r in summary:
        path = folder/f"{r['variant']}_output.txt"
        if path.exists():
            text += '\n## '+r['variant']+' 首次正文\n\n```text\n'+path.read_text(encoding='utf-8')+'\n```\n'
    (folder/'试跑记录.md').write_text(text, encoding='utf-8')
    manifest.update(network_requests=len(summary), model_runs=sum(r.get('http_status') == 200 for r in summary),
                    run_completed=len(summary) == 3 and all(r.get('finish_reason') for r in summary))
    save(folder/'manifest.json', manifest)
    print('Saved: '+str(folder), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--key-env', default='DEEPSEEK_API_KEY')
    parser.add_argument('--key-file', type=Path, help='Explicit plain-token file; never copied or logged')
    args = parser.parse_args()
    result = offline()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.run:
        key = args.key_file.read_text(encoding='utf-8-sig').strip() if args.key_file else os.environ.get(args.key_env, '').strip()
        if not key or '\n' in key or '\r' in key:
            raise ValueError('Valid DeepSeek credential unavailable; no requests sent')
        run(key)

if __name__ == '__main__':
    main()
