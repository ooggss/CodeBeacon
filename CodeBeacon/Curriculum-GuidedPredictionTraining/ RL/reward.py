import json
import re

def compute_score_search(result_text, expected_answers):
    """验证生成结果是否包含所有期望的依赖。

    返回 reward: float
    """
    try:
        output_json = json.loads(result_text)
    except json.JSONDecodeError:
        json_match = None
        try:
            json_match = json.loads(result_text.split("```json")[1].split("```")[0].strip())
        except:
            return 0
        if not json_match:
            return 0
        output_json = json_match

    if not isinstance(output_json, list):
        return 0


    matched = 0
    matched_expected = set()
    for result in output_json:
        if not ("name" in result and "location" in result):
            continue
        for i, exp in enumerate(expected_answers):
            if i in matched_expected:
                continue
            if exp['name'].strip() in result['name'].strip() and exp['location'].strip() in result['location'].strip():
                matched += 1
                matched_expected.add(i)
                break

    recall = matched / len(expected_answers)
    wrong_count = max(len(output_json) - matched, 0)
    penalty = wrong_count / (len(expected_answers) + wrong_count)
    reward = recall - 0.5 * penalty

    print(matched, len(expected_answers), len(output_json))
    return max(0.0, reward)