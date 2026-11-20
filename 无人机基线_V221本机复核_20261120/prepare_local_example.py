"""Bind the frozen V221 model and verify both interfaces' shared score path."""

from collections import Counter
import csv
import json
from math import isclose

from review_core import APP, COLUMNS, EXAMPLE, KEY, analyze, csv_rows, digest, indexed, merge_tables, read_text, score_rows


ROOT = APP.parent
V221 = ROOT / '无人机基线_V221双目与原特征联合训练_20261119'
V220 = ROOT / '无人机基线_V220剩余放行归因_20261119/comparison_001'
V219 = ROOT / '无人机基线_V219纵差校正开发_20261119/comparison_001'
V209 = ROOT / '无人机基线_V209采集身份与误拒_20261118/evaluation_001'


def verified(path, expected):
    if digest(path) != expected:
        raise RuntimeError(f'Frozen artifact changed: {path.name}')
    return path


def load(path):
    return json.loads(read_text(path))


def write_once(path, content):
    if path.exists():
        if path.read_text(encoding='utf-8') != content:
            raise RuntimeError(f'Existing local artifact differs: {path.name}')
        return
    with path.open('x', encoding='utf-8', newline='') as stream:
        stream.write(content)


def prepare():
    comparison = V221 / 'comparison_001/comparison.json'
    source = load(comparison)
    score_seal = load(verified(comparison.with_name('score_seal.json'), source['score_seal_sha256']))
    model_file = verified(comparison.with_name('model.json'), score_seal['model_sha256'])
    model = load(model_file)
    feature_seal = load(verified(V221 / 'features_001/features_seal.json', model['features_seal_sha256']))
    features = verified(V221 / 'features_001/features.csv', feature_seal['features_sha256'])

    visual_report = load(verified(V220 / 'diagnosis.json', source['v220_comparison_sha256']))
    visual_seal = load(verified(V220 / 'score_seal.json', visual_report['score_seal_sha256']))
    stereo = verified(V220 / 'scores.csv', visual_seal['score_file_sha256'])
    single_report = load(verified(V219 / 'comparison.json', visual_seal['source_comparison_sha256']))
    upstream = verified(V209 / 'joint_actions.csv', single_report['v209_actions_sha256'])

    selected = source['results']['eight_matched']['curve']['best_direct']
    policy = {'version': 'V221', 'model': model['models']['eight_matched'],
              'score_threshold': selected['threshold'],
              'single_threshold': single_report['results']['constant']['curve']['best_direct']['threshold'],
              'three_threshold': visual_report['results']['3']['shared']['best_direct']['threshold'],
              'source_sha256': {'v221_model': digest(model_file), 'v221_comparison': digest(comparison),
                                'v220_stereo': digest(stereo), 'v209_upstream': digest(upstream)}}
    texts = [read_text(path) for path in (upstream, stereo, features)]
    merged = merge_tables(texts)
    archive_file = verified(comparison.with_name('scores.csv'), score_seal['scores_sha256'])
    archive = indexed(csv_rows(read_text(archive_file), (*KEY, 'status', 'eight_matched')))
    groups = score_rows(merged, policy)
    selected_rows = {(parent, run, scope, str(row['seq']), str(row['stamp']), case): row
                     for (parent, run, scope, case), group in groups.items() for row in group}
    assert len(merged) == len(selected_rows) == len(archive) == 8550
    assert selected_rows.keys() == archive.keys()
    maximum = 0.0
    ready = 0
    action_counts = {}
    for record in merged:
        key = tuple(record[name] for name in KEY)
        computed, expected = selected_rows[key], archive[key]
        frozen_score = float(expected['eight_matched']) if expected['status'] == 'ready' else None
        if frozen_score is not None:
            ready += 1
            difference = abs(computed['score'] - frozen_score)
            maximum = max(maximum, difference)
            assert isclose(computed['score'], frozen_score, abs_tol=2e-15, rel_tol=0)
        else:
            assert computed['score'] is None
        rule_values = ((record['constant'], policy['single_threshold']),
                       (record['translation_3'], policy['three_threshold']),
                       (record['rotation_3'], policy['three_threshold']),
                       (expected['eight_matched'], policy['score_threshold']))
        upstream_action = record['eight_identity']
        predicted = ('reject' if upstream_action == 'allow' and any(value and float(value) > threshold
                     for value, threshold in rule_values) else upstream_action)
        assert computed['action'] == predicted
        action_counts.setdefault(record['condition'], Counter())[predicted] += 1

    for case, frozen in selected['result']['by_condition'].items():
        counts = Counter()
        for label_action, value in frozen['counts'].items():
            counts[label_action.partition('/')[2]] += value
        assert counts == action_counts[case]

    from io import StringIO
    buffer = StringIO(newline='')
    writer = csv.DictWriter(buffer, COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(merged)
    text = buffer.getvalue()
    report = analyze([text], policy)
    assert report == analyze(texts, policy)
    EXAMPLE.parent.mkdir(exist_ok=True)
    write_once(APP / 'policy.json', json.dumps(policy, ensure_ascii=False, indent=2) + '\n')
    write_once(EXAMPLE, text)
    proof = {'rows': len(merged), 'scored_rows': ready, 'maximum_absolute_difference': maximum,
             'action_mismatches': 0, 'merged_and_three_table_reports_match': True,
             'policy_sha256': digest(APP / 'policy.json'), 'local_example_sha256': digest(EXAMPLE),
             'conditions': action_counts, 'example_statuses': {row['condition']: row['counts'] for row in report}}
    write_once(APP / 'local_data/score_verification.json', json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    prepare()
