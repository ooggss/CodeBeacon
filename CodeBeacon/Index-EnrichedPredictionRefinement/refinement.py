import os
import json
import sys
import time
from datetime import datetime


def parse_output_json(result_text):
    """从模型输出文本中解析出 JSON list。"""
    try:
        output_json = json.loads(result_text.strip())
    except json.JSONDecodeError:
        try:
            output_json = json.loads(result_text.strip().split("```json")[1].split("```")[0].strip())
        except:
            return None
        if not output_json:
            return None

    if not isinstance(output_json, list):
        return None
    return output_json


def _safe_f1(precision, recall):
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def compute_metrics(output_entries, expected_answers):
    """计算完整的指标集合，返回 dict。
    包括 name/location/combined 各自的 precision, recall, F1。
    """
    n_expected = len(expected_answers)
    n_output = len(output_entries)

    zero_metrics = {
        "matched": 0, "reward": 0,
        "name_recall": 0, "name_precision": 0, "name_f1": 0,
        "location_recall": 0, "location_precision": 0, "location_f1": 0,
        "pair_recall": 0, "pair_precision": 0, "pair_f1": 0,
    }

    if n_expected == 0 or n_output == 0:
        return zero_metrics

    matched_expected = set()
    name_matched_expected = set()
    location_matched_expected = set()
    name_matched_output = set()
    location_matched_output = set()
    pair_matched_output = set()

    for j, result in enumerate(output_entries):
        if not ("name" in result and "location" in result):
            continue

        for i, exp in enumerate(expected_answers):
            if i not in name_matched_expected and exp['name'].strip() in result['name'].strip():
                name_matched_expected.add(i)
            if i not in location_matched_expected and exp['location'].strip() in result['location'].strip():
                location_matched_expected.add(i)

        for exp in expected_answers:
            if exp['name'].strip() in result['name'].strip():
                name_matched_output.add(j)
                break
        for exp in expected_answers:
            if exp['location'].strip() in result['location'].strip():
                location_matched_output.add(j)
                break

        for i, exp in enumerate(expected_answers):
            if i in matched_expected:
                continue
            if exp['name'].strip() in result['name'].strip() and exp['location'].strip() in result['location'].strip():
                matched_expected.add(i)
                pair_matched_output.add(j)
                break

    matched = len(matched_expected)

    name_recall = len(name_matched_expected) / n_expected
    name_precision = len(name_matched_output) / n_output
    name_f1 = _safe_f1(name_precision, name_recall)

    location_recall = len(location_matched_expected) / n_expected
    location_precision = len(location_matched_output) / n_output
    location_f1 = _safe_f1(location_precision, location_recall)

    pair_recall = matched / n_expected
    pair_precision = len(pair_matched_output) / n_output
    pair_f1 = _safe_f1(pair_precision, pair_recall)

    wrong_count = max(n_output - matched, 0)
    penalty = wrong_count / (n_expected + wrong_count)
    reward = pair_recall - 0.5 * penalty

    return {
        "matched": matched, "reward": max(0.0, reward),
        "name_recall": name_recall, "name_precision": name_precision, "name_f1": name_f1,
        "location_recall": location_recall, "location_precision": location_precision, "location_f1": location_f1,
        "pair_recall": pair_recall, "pair_precision": pair_precision, "pair_f1": pair_f1,
    }


METRIC_KEYS = [
    "name_recall", "name_precision", "name_f1",
    "location_recall", "location_precision", "location_f1",
    "pair_recall", "pair_precision", "pair_f1",
    "reward",
]


def reward_function_from_outputs(merged_outputs, expected_answers):
    """基于已合并的 output list 计算指标，返回 metrics_dict。"""
    return compute_metrics(merged_outputs, expected_answers)


def enrich_output_json(output_json, project_name, keyword_to_filepath, filepath_to_keyword):
    """根据映射表对 output_json 进行补全扩展。
    - 用 name 查 keyword_to_filepath 补全 location
    - 用 location 查 filepath_to_keyword 补全 name
    返回去重后的扩展列表。
    """
    kw2fp = keyword_to_filepath.get(project_name, {})
    fp2kw = filepath_to_keyword.get(project_name, {})

    enriched = []
    seen = set()

    def add_entry(name, location):
        key = (name.strip(), location.strip())
        if key not in seen:
            seen.add(key)
            enriched.append({"name": name.strip(), "location": location.strip()})

    for entry in output_json:
        if not ("name" in entry and "location" in entry):
            continue
        name = entry["name"].strip()
        location = entry["location"].strip()

        add_entry(name, location)

        if name in kw2fp:
            if len(kw2fp[name]) > 3:
                continue
            for fp in kw2fp[name]:
                add_entry(name, fp)

        if location in fp2kw:
            for kw in fp2kw[location]:
                add_entry(kw, location)

    return enriched


def detect_project_from_expected(expected_answers, filepath_to_keyword, project_names):
    """根据 expected_answers 的 location 判断所属 project。"""
    for exp in expected_answers:
        loc = exp.get('location', '').strip()
        if not loc:
            continue
        for pname in project_names:
            if loc in filepath_to_keyword.get(pname, {}):
                return pname
    return None


def evaluate_with_enrich(keyword_to_filepath_path, filepath_to_keyword_path, result_json_paths):
    """读取映射表和结果文件，对 output_json 补全后重新计算 recall。"""
    with open(keyword_to_filepath_path, 'r', encoding='utf-8') as f:
        keyword_to_filepath = json.load(f)
    with open(filepath_to_keyword_path, 'r', encoding='utf-8') as f:
        filepath_to_keyword = json.load(f)

    project_names = list(keyword_to_filepath.keys())
    print(f"Loaded mappings for projects: {project_names}")

    all_file_results = []

    for path in result_json_paths:
        print(f"\n{'='*60}")
        print(f"File: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            results = json.load(f)

        total_before = {k: 0.0 for k in METRIC_KEYS}
        total_after = {k: 0.0 for k in METRIC_KEYS}
        total_fp_before = 0
        total_fp_after = 0
        valid_count = 0
        total_entries = 0
        total_name_not_found = 0
        total_fp_not_found = 0
        total_pair_not_found = 0

        for idx, item in enumerate(results):
            expected_answers = item.get('expected_answers', [])
            result_text = item.get('text', '')

            if not expected_answers:
                continue

            output_json = item.get('output_json', None)
            if output_json is None:
                output_json = parse_output_json(result_text)
            if not output_json:
                valid_count += 1
                continue

            matched_project = detect_project_from_expected(expected_answers, filepath_to_keyword, project_names)
            if not matched_project:
                text_lower = result_text.lower()
                for pname in project_names:
                    if pname.lower() in text_lower:
                        matched_project = pname
                        break

            proj_kw2fp = keyword_to_filepath.get(matched_project, {}) if matched_project else {}
            proj_fp2kw = filepath_to_keyword.get(matched_project, {}) if matched_project else {}
            all_known_names = set(proj_kw2fp.keys())
            all_known_fps = set(proj_fp2kw.keys())

            item_name_not_found = 0
            item_fp_not_found = 0
            item_pair_not_found = 0
            item_entry_count = 0
            for e in output_json:
                if not ("name" in e and "location" in e):
                    continue
                item_entry_count += 1
                name = e['name'].strip()
                location = e['location'].strip()
                name_ok = (not name) or (name in all_known_names)
                fp_ok = (not location) or (location in all_known_fps)
                if not name_ok:
                    item_name_not_found += 1
                if not fp_ok:
                    item_fp_not_found += 1
                pair_ok = name_ok and fp_ok and name and location and (location in proj_kw2fp.get(name, []))
                if not pair_ok:
                    item_pair_not_found += 1

            total_entries += item_entry_count
            total_name_not_found += item_name_not_found
            total_fp_not_found += item_fp_not_found
            total_pair_not_found += item_pair_not_found

            item['name_hallucination_ratio'] = item_name_not_found / item_entry_count if item_entry_count else 0
            item['fp_hallucination_ratio'] = item_fp_not_found / item_entry_count if item_entry_count else 0
            item['pair_hallucination_ratio'] = item_pair_not_found / item_entry_count if item_entry_count else 0

            metrics_before = reward_function_from_outputs(output_json, expected_answers)

            if matched_project:
                enriched = enrich_output_json(output_json, matched_project, keyword_to_filepath, filepath_to_keyword)
            else:
                enriched = output_json

            fp_before = len({e['location'].strip() for e in output_json if 'location' in e and e['location'].strip()})
            fp_after = len({e['location'].strip() for e in enriched if 'location' in e and e['location'].strip()})

            metrics_after = reward_function_from_outputs(enriched, expected_answers)

            item['enriched_output_json'] = enriched
            item['enriched_output_count'] = fp_after
            item['enriched_project'] = matched_project
            for k in METRIC_KEYS:
                item[f'{k}_before_enrich'] = metrics_before[k]
                item[f'{k}_after_enrich'] = metrics_after[k]

            for k in METRIC_KEYS:
                total_before[k] += metrics_before[k]
                total_after[k] += metrics_after[k]
            total_fp_before += fp_before
            total_fp_after += fp_after
            valid_count += 1

        all_file_results.append(results)

        if valid_count > 0:
            print(f"  Evaluated {valid_count} items")
            name_halluc_rate = total_name_not_found / total_entries if total_entries else 0
            fp_halluc_rate = total_fp_not_found / total_entries if total_entries else 0
            pair_halluc_rate = total_pair_not_found / total_entries if total_entries else 0
            print(f"\n  --- Hallucination (not in mapping) ---")
            print(f"  {'entity_name':>35s}: {total_name_not_found:>6d} / {total_entries:<6d} ({name_halluc_rate:.2%})")
            print(f"  {'file_path':>35s}: {total_fp_not_found:>6d} / {total_entries:<6d} ({fp_halluc_rate:.2%})")
            print(f"  {'(name, file_path) pair':>35s}: {total_pair_not_found:>6d} / {total_entries:<6d} ({pair_halluc_rate:.2%})")
            print(f"\n  --- Metrics comparison (before / after enrich) ---")
            print(f"  {'':>30s} {'BEFORE':>10s}  {'AFTER':>10s}")
            for k in METRIC_KEYS:
                print(f"  {('avg ' + k):>30s} {total_before[k]/valid_count:>10.4f}  {total_after[k]/valid_count:>10.4f}")
            print(f"  {'total unique file_paths':>30s} {total_fp_before:>10d}  {total_fp_after:>10d}")
            print(f"  {'avg file_paths per item':>30s} {total_fp_before/valid_count:>10.2f}  {total_fp_after/valid_count:>10.2f}")
        else:
            print("  No valid results.")

        output_path = path.replace('.json', '_enriched.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"  Saved to: {output_path}")

    if len(result_json_paths) > 1:
        print(f"\n{'='*60}")
        print(f"UNION recall (enriched) across {len(result_json_paths)} files")
        print(f"{'='*60}")

        num_tasks = min(len(r) for r in all_file_results)
        union_before = {k: 0.0 for k in METRIC_KEYS}
        union_after = {k: 0.0 for k in METRIC_KEYS}
        union_valid = 0
        merged_items = []

        for idx in range(num_tasks):
            expected_answers = None
            messages = None
            for file_results in all_file_results:
                ea = file_results[idx].get('expected_answers', [])
                if ea:
                    expected_answers = ea
                    break
            for file_results in all_file_results:
                msg = file_results[idx].get('messages')
                if msg:
                    messages = msg
                    break
            if not expected_answers:
                merged_items.append(None)
                continue

            merged_before = []
            merged_after = []
            seen_before = set()
            seen_after = set()

            for file_results in all_file_results:
                item = file_results[idx]
                oj = item.get('output_json') or parse_output_json(item.get('text', ''))
                if oj:
                    for e in oj:
                        if "name" in e and "location" in e:
                            k = (e['name'].strip(), e['location'].strip())
                            if k not in seen_before:
                                seen_before.add(k)
                                merged_before.append({"name": e['name'].strip(), "location": e['location'].strip()})
                ej = item.get('enriched_output_json') or oj
                if ej:
                    for e in ej:
                        if "name" in e and "location" in e:
                            k = (e['name'].strip(), e['location'].strip())
                            if k not in seen_after:
                                seen_after.add(k)
                                merged_after.append({"name": e['name'].strip(), "location": e['location'].strip()})

            if not merged_before and not merged_after:
                merged_items.append(None)
                continue

            matched_project = detect_project_from_expected(expected_answers, filepath_to_keyword, project_names)
            if not matched_project:
                for file_results in all_file_results:
                    mp = file_results[idx].get('enriched_project')
                    if mp:
                        matched_project = mp
                        break
            if not matched_project:
                text_lower = ''
                for file_results in all_file_results:
                    t = file_results[idx].get('text', '')
                    if t:
                        text_lower = t.lower()
                        break
                for pname in project_names:
                    if pname.lower() in text_lower:
                        matched_project = pname
                        break

            all_known_names = set(keyword_to_filepath.get(matched_project, {}).keys()) if matched_project else set()
            all_known_fps = set(filepath_to_keyword.get(matched_project, {}).keys()) if matched_project else set()

            def filter_valid_entries(entries):
                return [
                    e for e in entries
                    if (not e['name'] or e['name'] in all_known_names)
                    and (not e['location'] or e['location'] in all_known_fps)
                ]

            filtered_before = filter_valid_entries(merged_before)
            filtered_after = filter_valid_entries(merged_after)

            mb = reward_function_from_outputs(merged_before, expected_answers) if merged_before else {k: 0 for k in METRIC_KEYS + ["matched"]}
            ma = reward_function_from_outputs(merged_after, expected_answers) if merged_after else {k: 0 for k in METRIC_KEYS + ["matched"]}

            for k in METRIC_KEYS:
                union_before[k] += mb[k]
                union_after[k] += ma[k]
            union_valid += 1

            item_data = {
                "messages": messages,
                "expected_answers": expected_answers,
                "expected_count": len(expected_answers),
                "output_json_union": merged_before,
                "output_count_union": len(merged_before),
                "output_json_union_filtered": filtered_before,
                "output_count_union_filtered": len(filtered_before),
                "enriched_output_json_union": merged_after,
                "enriched_output_count_union": len(merged_after),
                "enriched_output_json_union_filtered": filtered_after,
                "enriched_output_count_union_filtered": len(filtered_after),
                "fp_before": len({e['location'] for e in merged_before if e['location']}),
                "fp_after": len({e['location'] for e in merged_after if e['location']}),
                "matched_before": mb['matched'],
                "matched_after": ma['matched'],
            }
            for k in METRIC_KEYS:
                item_data[f'{k}_before'] = mb[k]
                item_data[f'{k}_after'] = ma[k]
            merged_items.append(item_data)

        if union_valid > 0:
            print(f"  Evaluated {union_valid} tasks")
            print(f"  {'':>30s} {'BEFORE':>10s}  {'AFTER':>10s}")
            for k in METRIC_KEYS:
                print(f"  {('avg ' + k):>30s} {union_before[k]/union_valid:>10.4f}  {union_after[k]/union_valid:>10.4f}")

        merged_items_clean = [m for m in merged_items if m is not None]
        base_dir = os.path.dirname(result_json_paths[0])
        union_output_path = os.path.join(
            base_dir,
            f'evaluate_results_union_enriched_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{len(merged_items_clean)}.json'
        )
        with open(union_output_path, 'w', encoding='utf-8') as f:
            json.dump(merged_items_clean, f, ensure_ascii=False, indent=2)
        print(f"  Union merged results saved to: {union_output_path}")
        print(f"{'='*60}")


def main():
    if len(sys.argv) >= 5 and sys.argv[1] == '--eval-enrich':
        evaluate_with_enrich(sys.argv[2], sys.argv[3], sys.argv[4:])
        return

    print("Usage: python3 refinement.py --eval-enrich entity_name_to_file_path.json file_path_to_keyword.json result1.json [result2.json ...]")
    sys.exit(1)


if __name__ == "__main__":
    start = time.time()
    main()
    print(f"Total time cost: {(time.time() - start) / 60:.2f} minutes")
