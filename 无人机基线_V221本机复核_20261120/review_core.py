"""Score V221 project log exports for human review, without reference labels."""

from collections import Counter
import csv
from hashlib import sha256
from io import StringIO
import json
import math
from pathlib import Path
from statistics import median


APP = Path(__file__).resolve().parent
EXAMPLE = APP / 'local_data' / 'V221真实回放.csv'
KEY = ('parent_sequence', 'run_id', 'use_scope', 'input_seq', 'input_ns', 'condition')
UPSTREAM = ('common', 'identity_status', 'eight_identity')
STEREO = ('constant', 'translation_3', 'rotation_3')
FEATURES = ('visual_inliers', 'visual_inlier_ratio', 'gyro_disagreement_deg', 'band_power_m2_s2',
            'cadence_phase_max_ns', 'current_speed_m_s', 'speed_times_visual_inlier_ratio',
            'speed_times_gyro_disagreement')
CHANNELS = (UPSTREAM, STEREO, ('ready', *FEATURES))
COLUMNS = (*KEY, *(field for channel in CHANNELS for field in channel))
STATES = ('待复核', '证据不足', '未触发')
CONDITIONS = {'natural': '自然回放', 'blur': '受控模糊', 'occlusion': '受控遮挡',
              'clock': '受控时钟偏移', 'image_lag': '受控旧图'}


class InputError(ValueError):
    """A log cannot be interpreted under the V221 input contract."""


def read_text(path):
    return Path(path).read_text(encoding='utf-8-sig')


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def csv_rows(text, columns):
    table = csv.DictReader(StringIO(text.lstrip('\ufeff'), newline=''), strict=True)
    header = table.fieldnames or []
    if len(set(header)) != len(header) or not set(columns).issubset(header):
        raise InputError('CSV 表头与 V221 复核格式不符。请选择合并日志，或同时导入三张项目导出表。')
    rows = []
    for row in table:
        if None in row or any(row[name] is None for name in columns):
            raise InputError(f'第 {table.line_num} 行的列数与表头不一致。')
        rows.append({name: row[name].strip() for name in columns})
    if not rows:
        raise InputError('日志中没有数据行。')
    return rows


def indexed(rows):
    values = {}
    for row in rows:
        key = tuple(row[name] for name in KEY)
        if key in values:
            raise InputError('发现重复的运行、帧号和时间戳组合。')
        values[key] = row
    return values


def merge_tables(texts):
    """Match the actual V209, V220 and V221 exports by exact input identity."""
    sources = {}
    for text in texts:
        header = set(next(csv.reader(StringIO(text.lstrip('\ufeff'))), []))
        channels = [channel for channel in CHANNELS if set(channel).issubset(header)]
        if len(channels) != 1 or channels[0] in sources:
            raise InputError('三表导入需要上游动作、双目分数和八列特征各一份。')
        channel = channels[0]
        sources[channel] = indexed(csv_rows(text, (*KEY, *channel)))
    if set(sources) != set(CHANNELS):
        raise InputError('三表导入需要上游动作、双目分数和八列特征各一份。')
    upstream, stereo, features = (sources[channel] for channel in CHANNELS)
    if upstream.keys() != stereo.keys() or not upstream.keys() <= features.keys():
        raise InputError('三表的运行、帧号或时间戳无法逐条对齐。')
    return [{**row, **stereo[key], **features[key]} for key, row in upstream.items()]


def load_policy():
    policy = json.loads(read_text(APP / 'policy.json'))
    model = policy['model']
    if tuple(model['features']) != FEATURES:
        raise RuntimeError('V221 模型的输入列与评分入口不符。')
    return policy


def number(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('non-finite numeric value')
    return value


def model_score(row, model):
    terms = [(number(row[name]) - center) / scale * weight
             for name, center, scale, weight in zip(
                 FEATURES, model['center'], model['scale'], model['coef'][0], strict=True)]
    logit = sum(terms) + model['intercept'][0]
    if not math.isfinite(logit):
        raise ValueError('non-finite V221 logit')
    if logit >= 0:
        return 1 / (1 + math.exp(-logit))
    exp_logit = math.exp(logit)
    return exp_logit / (1 + exp_logit)


def decision(row, policy, score, single, triple):
    action = row['eight_identity']
    if row['identity_status'] == 'mismatch':
        return action, '待复核', '输入图像身份不一致'
    if action == 'reject':
        reason = '原接口判为不可用' if row['common'] == 'reject' else '上游八列评分触发'
        return action, '待复核', reason
    if action == 'abstain':
        return action, '证据不足', '原接口证据不足'
    if single is not None and single > policy['single_threshold']:
        return 'reject', '待复核', '双目与 VIO 单窗差异超线'
    if triple is not None and triple > policy['three_threshold']:
        return 'reject', '待复核', '双目与 VIO 连续三窗差异超线'
    if score is not None and score > policy['score_threshold']:
        return 'reject', '待复核', 'V221 八列评分超线'
    missing = []
    if score is None or single is None:
        missing.append('缺少双目或八列证据')
    if row['identity_status'] == 'source_unavailable':
        missing.append('采集身份未核验')
    if missing:
        return action, '证据不足', '、'.join(missing)
    return action, '未触发', '当前证据未触发复核提示'


def score_rows(rows, policy):
    groups = {}
    for index, row in enumerate(rows, 2):
        run = (row['parent_sequence'], row['run_id'], row['use_scope'], row['condition'])
        try:
            seq, stamp = int(row['input_seq']), int(row['input_ns'])
            if not all(run) or seq < 1 or stamp < 0 or run[2] != 'relative_query_H1_at_end':
                raise ValueError('invalid identity')
            if row['ready'] not in ('0', '1') or row['identity_status'] not in ('match', 'mismatch', 'source_unavailable'):
                raise ValueError('invalid evidence status')
            common, upstream = row['common'], row['eight_identity']
            if common not in ('allow', 'reject', 'abstain') or upstream not in ('allow', 'reject', 'abstain'):
                raise ValueError('invalid action')
            if (common == 'reject' or row['identity_status'] == 'mismatch') and upstream != 'reject':
                raise ValueError('contradictory actions')
            single, trans, rot = (number(row[name]) if row[name] else None for name in STEREO)
            if (trans is None) != (rot is None) or any(x is not None and x < 0 for x in (single, trans, rot)):
                raise ValueError('incomplete stereo evidence')
            triple = max(trans, rot) if trans is not None else None
            score = model_score(row, policy['model']) if row['ready'] == '1' else None
        except (OverflowError, ValueError) as error:
            raise InputError(f'第 {index} 条记录的身份、动作或数值无效。请核对项目导出。') from error
        action, state, reason = decision(row, policy, score, single, triple)
        groups.setdefault(run, []).append(dict(seq=seq, stamp=stamp, action=action, state=state,
                                               reason=reason, score=score, single=single, triple=triple))
    for group in groups.values():
        group.sort(key=lambda row: row['seq'])
        if any(b['seq'] <= a['seq'] or b['stamp'] <= a['stamp'] for a, b in zip(group, group[1:])):
            raise InputError('同一运行的帧号和时间戳必须一一对应并向前递增。')
    return groups


def segments(group):
    first_stamp = group[0]['stamp']
    steps = [b['stamp'] - a['stamp'] for a, b in zip(group, group[1:]) if b['seq'] == a['seq'] + 1]
    max_gap = 2 * median(steps) if steps else 0
    items = []
    previous = None
    for row in group:
        continuing = (previous is not None and row['seq'] == previous['seq'] + 1
                      and row['stamp'] - previous['stamp'] <= max_gap
                      and (row['state'], row['reason']) == (previous['state'], previous['reason']))
        when = round((row['stamp'] - first_stamp) / 1e9, 6)
        if not continuing:
            items.append(dict(state=row['state'], reason=row['reason'], first=row['seq'],
                              first_ns=str(row['stamp']), start=when, count=0,
                              score=None, single=None, triple=None))
        item = items[-1]
        item.update(last=row['seq'], last_ns=str(row['stamp']), end=when, count=item['count'] + 1)
        for field in ('score', 'single', 'triple'):
            value = row[field]
            if value is not None and (item[field] is None or value > item[field]):
                item[field] = value
        previous = row
    return items


def analyze(texts, policy):
    if len(texts) not in (1, 3):
        raise InputError('请选择一份合并日志，或三份项目导出表。')
    rows = csv_rows(texts[0], COLUMNS) if len(texts) == 1 else merge_tables(texts)
    report = []
    for (parent, run, _, case), group in score_rows(rows, policy).items():
        report.append(dict(parent=parent, run=run, condition=CONDITIONS.get(case, case),
                           counts=dict(Counter(row['state'] for row in group)),
                           total=len(group), segments=segments(group)))
    return report


EXPORT_FIELDS = ('parent', 'run', 'condition', 'state', 'reason', 'start', 'end', 'first', 'last',
                 'first_ns', 'last_ns', 'count', 'score', 'single', 'triple')


def for_spreadsheet(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def export_csv(run, items):
    stream = StringIO(newline='')
    writer = csv.DictWriter(stream, EXPORT_FIELDS)
    writer.writeheader()
    for item in items:
        data = {**{field: run[field] for field in EXPORT_FIELDS[:3]}, **item}
        writer.writerow({field: for_spreadsheet(data[field]) for field in EXPORT_FIELDS})
    return '\ufeff' + stream.getvalue()
