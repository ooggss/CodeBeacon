#!/usr/bin/env python3
"""
API Combination Generator
=========================

基于API功能描述和种子组合数据，利用LLM生成高质量的跨模块API组合。

方法：
1. Bootstrap自举扩增：基于种子组合的协同模式，迭代生成新组合
2. 场景驱动生成：LLM生成应用场景，从API库中检索匹配的API
3. 质量过滤：去重、多样性检查、覆盖度监控

使用方法：
    python3 generate_api_combination.py \
        --func_desc_dir <功能描述数据目录> \
        --output_dir <输出目录> \
        --model_name <模型名称> \
        --project_name <项目名称> \
        [--seed_tasks <种子组合文件1> <种子组合文件2> ...] \
        [--num_combinations <每轮每批生成数量>] \
        [--num_rounds <Bootstrap轮数>] \
        [--num_batches_per_round <每轮并行批次数>] \
        [--apis_per_prompt <每次提供给LLM的API数量>] \
        [--num_processes <并行进程数>]
"""

import os
import json
import sys
import re
import logging
import time
import random
import argparse
from datetime import datetime
from multiprocessing import Pool, cpu_count
from collections import Counter
import openai


api_key = os.environ.get("DS_API_KEY")
api_base = 'https://api.lkeap.cloud.tencent.com/v1'
client = openai.OpenAI(api_key=api_key, base_url=api_base)

PROJECT_TO_LANGUAGE = {
    "reaction": "C++",
    "Hexi": "C++",
    "LEANN": "Python",
    "sqlgen": "C++",
}

# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def extract_summary(text):
    """从 <summary>...</summary> 标签中提取功能摘要"""
    match = re.search(r'<summary>(.*?)</summary>', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def load_api_catalog(func_desc_dir, project_name=None):
    """
    加载所有API功能描述，构建索引化的API目录。

    每个API包含: entity_name, entity_location, entity_code, dependencies, summary
    """
    catalog = []
    seen = set()

    for filename in sorted(os.listdir(func_desc_dir)):
        if not filename.endswith('.json') or "functional_description" not in filename:
            continue
        filepath = os.path.join(func_desc_dir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for item in data:
            if project_name and item.get("project_name") != project_name:
                continue
            if item["entity_info"]["original_info"].get("function_access_specifier", "") == "private":
                continue
            entity_info = item.get("entity_info", {})
            name = entity_info.get("entity_name", "")
            location = entity_info.get("entity_location", "")
            code = entity_info.get("entity_code", "")
            deps = entity_info.get("dependencies", [])
            summary = extract_summary(item.get("text", ""))

            key = f"{name}||{location}"
            if key in seen or not name or not summary:
                continue
            seen.add(key)

            catalog.append({
                "entity_name": name,
                "entity_location": location,
                "entity_code": code,
                "dependencies": deps,
                "summary": summary,
            })
        # break
    return catalog


def load_seed_combinations(seed_files):
    """从已有的task文件中加载种子API组合，预计算每个种子涉及的文件集合。支持传入单个文件路径或多个文件路径列表。"""
    if isinstance(seed_files, str):
        seed_files = [seed_files]
    tasks = []
    for sf in seed_files:
        with open(sf, 'r', encoding='utf-8') as f:
            tasks.extend(json.load(f))

    seeds = []
    for task in tasks:
        source_code_dict = task.get("entity_info", {})["dependencies"]
        if source_code_dict:
            api_names = [item["dependency_name"] for item in source_code_dict]
            files = set()
            for item in source_code_dict:
                loc = item.get("dependency_location", "")
                fm = re.search(r'file `([^`]+)`', loc)
                if fm:
                    files.add(fm.group(1))
            func_desc = ""
            text = task.get("text", "")
            if "<summary>" in text and "</summary>" in text:
                func_desc = text.split("<summary>")[1].split("</summary>")[0].strip()
            seeds.append({
                "apis": api_names,
                "functional_description": func_desc,
                "source_code_dict": source_code_dict,
                "_api_set": set(api_names),
                "_file_set": files,
            })
    return seeds

    seen = set()
    unique_seeds = []
    for s in seeds:
        key = tuple(sorted(s["apis"]))
        if key not in seen:
            seen.add(key)
            unique_seeds.append(s)
    return unique_seeds


# ---------------------------------------------------------------------------
# Format Conversion
# ---------------------------------------------------------------------------

def entity_to_source_code_dict_item(entity):
    """将API catalog中的entity转换为source_code_dict格式的item"""
    dep_deps = []
    for dep in entity.get("dependencies", []):
        dep_deps.append({
            "dependency_name": dep.get("dependency_name", ""),
            "dependency_code": dep.get("dependency_code", ""),
            "dependency_location": dep.get("dependency_location", ""),
            "member_functions": dep.get("member_functions", []),
        })

    return {
        "dependency_name": entity["entity_name"],
        "dependency_code": entity["entity_code"],
        "dependency_location": entity["entity_location"],
        "dependency_dependencies": dep_deps,
        "member_functions": entity.get("member_functions", []),
    }


# ---------------------------------------------------------------------------
# Prompt Construction
# ---------------------------------------------------------------------------

def get_file_from_location(location):
    """从entity_location中提取文件路径"""
    match = re.search(r'file `([^`]+)`', location)
    return match.group(1) if match else "unknown"


def build_api_list_text(api_subset):
    """构建API列表文本，按文件分组以便LLM理解结构"""
    file_groups = {}
    for i, api in enumerate(api_subset):
        file_path = get_file_from_location(api["entity_location"])
        file_groups.setdefault(file_path, []).append((i, api))

    lines = []
    for file_path in sorted(file_groups.keys()):
        lines.append(f"\n--- File: {file_path} ---")
        for idx, api in file_groups[file_path]:
            lines.append(
                f"  [{idx}] {api['entity_name']}: {api['summary']}"
            )

    return "\n".join(lines)


def split_seeds_into_chunks(seeds, chunk_size=5):
    """将种子组合按chunk_size分成多个chunk，供不同batch使用不同的few-shot示例"""
    if not seeds:
        return [[]]
    chunks = [
        seeds[i:i + chunk_size]
        for i in range(0, len(seeds), chunk_size)
    ]
    return chunks


def select_relevant_seeds(seeds, api_subset, chunk_size=5):
    """
    为指定的 api_subset 选择功能上最相关的种子组合，
    返回按 chunk_size 分组的列表（每组作为一个 prompt 的 few-shot）。

    流程:
    1. 对所有种子按与 api_subset 的相关性打分并排序
    2. 取出所有相关种子 (score > 0)
    3. 如果不足 chunk_size 的整数倍，用随机的无关种子补齐
    4. 按 chunk_size 切分成多组返回

    Returns:
        list[list[seed]]: 每个子列表包含 chunk_size 个种子
    """
    if not seeds:
        return [[]]
    if len(seeds) <= chunk_size:
        return [list(seeds)]

    api_subset_names = set(a["entity_name"] for a in api_subset)
    api_subset_files = set(
        get_file_from_location(a["entity_location"]) for a in api_subset
    )

    scored = []
    for seed in seeds:
        seed_names = seed.get("_api_set", set(seed["apis"]))
        seed_files = seed.get("_file_set", set())

        name_overlap = len(seed_names & api_subset_names)
        file_overlap = len(seed_files & api_subset_files)
        score = name_overlap * 2 + file_overlap

        scored.append((score, seed))

    scored.sort(key=lambda x: x[0], reverse=True)

    relevant = [s for sc, s in scored if sc > 0]
    irrelevant = [s for sc, s in scored if sc == 0]

    if not relevant:
        random.shuffle(irrelevant)
        relevant = irrelevant[:chunk_size]

    remainder = len(relevant) % chunk_size
    if remainder != 0:
        pad_needed = chunk_size - remainder
        random.shuffle(irrelevant)
        relevant.extend(irrelevant[:pad_needed])

    chunks = [
        relevant[i:i + chunk_size]
        for i in range(0, len(relevant), chunk_size)
    ]
    return chunks


def sample_apis_relevant_to_seeds(catalog, seed_chunk, sample_size, coverage_counter=None):
    """
    为一组种子组合采样相关的 API。

    策略：
    1. 计算 catalog 中每个 API 与 seed_chunk 的相关性
       - API 名称出现在种子组合中 → 权重 2
       - API 所在文件与种子组合重叠 → 权重 1
    2. 相关 API 优先选入（占 50%），其余用多样性采样补齐（占 50%）
       这样既保证与种子相关，又引入新的跨模块 API 来产生新组合
    """
    seed_api_names = set()
    seed_files = set()
    for seed in seed_chunk:
        seed_api_names.update(seed.get("_api_set", set(seed["apis"])))
        seed_files.update(seed.get("_file_set", set()))

    scored = []
    for api in catalog:
        name_match = 2 if api["entity_name"] in seed_api_names else 0
        file_path = get_file_from_location(api["entity_location"])
        file_match = 1 if file_path in seed_files else 0
        scored.append((name_match + file_match, api))

    scored.sort(key=lambda x: x[0], reverse=True)

    relevant = [api for sc, api in scored if sc > 0]
    others = [api for sc, api in scored if sc == 0]

    n_relevant = min(len(relevant), sample_size // 2)
    if coverage_counter:
        relevant.sort(key=lambda a: coverage_counter.get(a["entity_name"], 0))
    selected = relevant[:n_relevant]
    selected_keys = set(f"{a['entity_name']}||{a['entity_location']}" for a in selected)

    n_other = sample_size - len(selected)
    if coverage_counter:
        others.sort(key=lambda a: coverage_counter.get(a["entity_name"], 0))
        selected += others[:n_other]
    else:
        random.shuffle(others)
        selected += others[:n_other]

    return selected


def build_seed_examples_text(seeds):
    """构建种子组合示例文本（直接使用传入的全部seeds，分chunk逻辑在调用方处理）"""
    if not seeds:
        return "(No existing examples available)"

    lines = []
    for i, seed in enumerate(seeds):
        api_names = seed["apis"]
        lines.append(f"  Example {i + 1}: [{', '.join(api_names)}]")
        func_desc = seed.get("functional_description", "")
        if func_desc:
            lines.append(f"  Functional Description:\n{func_desc}")
    return "\n".join(lines)


def generate_combinations_prompt(api_subset, seeds, project_name, num_to_generate=10):
    """构建让LLM生成API组合的完整prompt"""
    api_list = build_api_list_text(api_subset)
    seed_text = build_seed_examples_text(seeds)

    system_prompt = (
        f"You are an expert {PROJECT_TO_LANGUAGE[project_name]} developer with deep knowledge of the {project_name} library. "
        f"Your task is to identify groups of APIs that would naturally be used together "
        f"in real-world development scenarios."
    )

    user_prompt = f"""# Available APIs from the {project_name} Library
{api_list}

# Existing API Combinations (for reference)
{seed_text}

# Task
Generate {num_to_generate} new, diverse API combinations from the catalog above. For each combination, also provide a functional description following the rules below.

## Combination Requirements
1. Each combination must contain 2-5 APIs, referenced by their [index] numbers
2. APIs in each combination should naturally collaborate to accomplish a **concrete** development task
3. **Strongly prefer cross-file combinations** — APIs from DIFFERENT files that serve a COMMON purpose are much more valuable than APIs from the same file
4. Each combination must be DISTINCT from the examples above
5. Each combination should target a DIFFERENT development scenario
6. Think about real-world usage: initialization + processing + cleanup, input + transform + output, etc.

## Functional Description Requirements
For each combination, provide:
- `detail_description`: A detailed description of the practical development scenario this API combination serves. Explain what each API contributes and how they work together to accomplish a real-world task. Cover: what inputs are needed, what outputs are produced, key processing steps, and typical usage contexts.
- `summary`: 2-5 bullet points, each describing one concrete task a developer would accomplish using this API combination. Write from the developer's perspective.

**Critical rules for descriptions:**
1. **NEVER use testing-related language.** Words like "test", "verify", "validate", "assert", "expect", "check correctness", "fixture", "mock", "stub" are strictly FORBIDDEN.
2. **DO NOT use any specific code entity names** (function names, class names, method names, variable names, or macro names from the API catalog) in the summary. Use only generic programming concepts and natural language.
3. **Be concrete and actionable.** Describe specific operations (e.g., "serialize a struct to binary format and convert byte order for network transmission") rather than abstract concepts (e.g., "a mechanism for data processing").
4. Frame everything as a **real application use case**, not a test or verification.

# Output Format
Return ONLY a JSON array (no other text before or after):
```json
[
    {{
        "detail_description": "Detailed description of the practical scenario ...",
        "summary": "1. First concrete developer task ...\n2. Second concrete developer task ...\n3. ...",
        "api_indices": [0, 3, 7]
    }}
]
```
Use the exact [index] numbers from the catalog. Ensure valid JSON."""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ---------------------------------------------------------------------------
# LLM Interaction
# ---------------------------------------------------------------------------

def call_llm(messages, model_name, max_retries=100):
    """调用LLM，带重试逻辑"""
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                stream=False,
                temperature=0.85,
                top_p=0.9,
                max_completion_tokens=16000,
            )
            text = completion.choices[0].message.content
            usage = {
                "completion_tokens": completion.usage.completion_tokens,
                "prompt_tokens": completion.usage.prompt_tokens,
                "total_tokens": completion.usage.total_tokens,
            }
            return text, usage
        except Exception as e:
            logging.error(f"LLM call error (attempt {attempt + 1}/{max_retries}): {e}")
            time.sleep(min(2 ** attempt, 30))
    return None, {}


def parse_combination_response(text, api_subset):
    """解析LLM返回的JSON，提取API组合"""
    json_str = None
    json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)

    if not json_str:
        logging.error("Failed to extract JSON from LLM response")
        return []

    try:
        combinations = json.loads(json_str)
    except json.JSONDecodeError as e:
        logging.error(f"JSON parse error: {e}")
        return []

    results = []
    for combo in combinations:
        indices = combo.get("api_indices", [])
        detail_desc = combo.get("detail_description", "")
        summary = combo.get("summary", "")

        apis = []
        valid = True
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(api_subset):
                apis.append(api_subset[idx])
            else:
                logging.warning(f"Invalid API index {idx} (max={len(api_subset) - 1}), skipping combination")
                valid = False
                break

        if valid and len(apis) >= 2:
            results.append({
                "detail_description": detail_desc,
                "summary": summary,
                "apis": [a["entity_name"] for a in apis],
                "source_code_dict": [entity_to_source_code_dict_item(a) for a in apis],
            })

    return results


# ---------------------------------------------------------------------------
# Sampling & Filtering
# ---------------------------------------------------------------------------

def sample_diverse_apis(catalog, sample_size, coverage_counter=None):
    """
    多样性采样：优先选覆盖度低的API，并确保跨文件分布。

    策略：
    - 第1轮(无coverage)：按文件round-robin采样
    - 后续轮次：70%来自低覆盖API，30%来自高覆盖API
    """
    if len(catalog) <= sample_size:
        return list(catalog)

    if coverage_counter:
        sorted_apis = sorted(
            catalog, key=lambda a: coverage_counter.get(a["entity_name"], 0)
        )
        low_coverage = sorted_apis[: len(sorted_apis) // 2]
        high_coverage = sorted_apis[len(sorted_apis) // 2 :]

        n_low = min(len(low_coverage), int(sample_size * 0.7))
        n_high = sample_size - n_low
        n_high = min(n_high, len(high_coverage))

        sampled = random.sample(low_coverage, n_low)
        sampled += random.sample(high_coverage, n_high)
        return sampled

    file_groups = {}
    for api in catalog:
        file_key = get_file_from_location(api["entity_location"])
        file_groups.setdefault(file_key, []).append(api)

    sampled = []
    sampled_set = set()
    file_keys = list(file_groups.keys())
    random.shuffle(file_keys)

    round_idx = 0
    while len(sampled) < sample_size and round_idx < len(catalog):
        file_key = file_keys[round_idx % len(file_keys)]
        group = file_groups[file_key]
        if group:
            api = random.choice(group)
            api_key = f"{api['entity_name']}||{api['entity_location']}"
            if api_key not in sampled_set:
                sampled_set.add(api_key)
                sampled.append(api)
        round_idx += 1

    return sampled


def filter_combinations_dedup(all_combinations):
    """精确去重：基于API名称集合去重，要求至少2个API"""
    seen_sets = set()
    filtered = []

    for combo in all_combinations:
        api_names = tuple(sorted(
            item["dependency_name"] for item in combo["source_code_dict"]
        ))
        if len(api_names) < 2:
            continue
        if api_names in seen_sets:
            continue
        seen_sets.add(api_names)
        filtered.append(combo)

    return filtered


# ---------------------------------------------------------------------------
# Quality Filter 1: Functional Coherence Validation (LLM-based)
# ---------------------------------------------------------------------------

def build_coherence_check_prompt(combo, project_name):
    """构建功能一致性检验的prompt"""
    detail_description = combo.get("detail_description", "N/A")
    summary = combo.get("summary", "N/A")
    api_lines = []
    for i, item in enumerate(combo["source_code_dict"]):
        name = item["dependency_name"]
        location = item.get("dependency_location", "")
        code_preview = item.get("dependency_code", "")
        api_lines.append(f"{i + 1}. **{name}** ({location})\nCode:\n```\n{code_preview}\n```")
    api_text = "\n".join(api_lines)

    summary_lines = []
    for api_info in combo.get("_api_summaries", []):
        summary_lines.append(f"  - {api_info['name']}: {api_info['summary']}")
    summary_text = "\n".join(summary_lines) if summary_lines else "  (summaries not available)"

    system_prompt = (
        f"You are a senior {PROJECT_TO_LANGUAGE.get(project_name, 'C++')} engineer "
        f"reviewing proposed API combinations for the {project_name} library. "
        f"You evaluate whether groups of APIs form coherent, practical development scenarios."
    )

    user_prompt = f"""# API Combination to Evaluate

## Claimed Scenario
{summary}

## APIs in This Combination
{api_text}

## Functional Summaries
{summary_text}

# Evaluation Criteria
1. **Functional coherence**: Do these APIs logically collaborate to accomplish the stated scenario? Could a developer realistically need all of them together?
2. **Necessity**: Is every API in the combination actually needed for the scenario, or are some irrelevant padding?
3. **Practicality**: Is the scenario realistic (something a real developer would implement), not contrived or artificial?

# Scoring Guide
- 1 = APIs are unrelated; the combination is nonsensical
- 2 = Weak connection; most APIs don't contribute to the same goal
- 3 = Acceptable; APIs share a loose logical relationship but the scenario feels forced
- 4 = Good; APIs clearly work together for a practical scenario
- 5 = Excellent; APIs form a natural, tight-knit combination for a real-world task

# Output
Return ONLY a JSON object (no other text):
{{"score": <1-5>, "reason": "1-2 sentence explanation"}}"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_coherence_response(text):
    """解析一致性检验的LLM响应"""
    json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', text, re.DOTALL)
    if not json_match:
        return None

    try:
        result = json.loads(json_match.group(0))
        score = result.get("score")
        if isinstance(score, (int, float)) and 1 <= score <= 5:
            return {
                "score": int(score),
                "reason": result.get("reason", ""),
            }
    except (json.JSONDecodeError, TypeError):
        pass

    score_match = re.search(r'"score"\s*:\s*(\d)', text)
    if score_match:
        return {"score": int(score_match.group(1)), "reason": ""}

    return None


def check_coherence_worker(args):
    """功能一致性检验的worker函数（用于多进程并行）"""
    combo_idx, combo, project_name, model_name = args

    messages = build_coherence_check_prompt(combo, project_name)
    text, usage = call_llm(messages, model_name, max_retries=5)

    if text is None:
        logging.warning(f"Coherence check #{combo_idx}: LLM call failed, assigning score=3")
        return combo_idx, {"score": 3, "reason": "LLM call failed, default score"}, messages, text

    result = parse_coherence_response(text)
    if result is None:
        logging.warning(f"Coherence check #{combo_idx}: Failed to parse response, assigning score=3")
        return combo_idx, {"score": 3, "reason": "Parse failed, default score"}, messages, text

    # print(f"messages: {messages}")
    logging.info(
        f"Coherence check #{combo_idx}: score={result['score']} "
        f"apis=[{', '.join(combo['apis'])}] reason={result['reason']}"
    )
    return combo_idx, result, messages, text


def filter_by_coherence(combinations, project_name, model_name, threshold=3,
                        num_processes=None, catalog_lookup=None):
    """
    功能一致性过滤：调用LLM对每个组合打分，过滤低于阈值的组合。

    Args:
        combinations: 待过滤的组合列表
        project_name: 项目名
        model_name: LLM模型名
        threshold: 分数阈值，低于此分数的组合被过滤 (默认3)
        num_processes: 并行进程数
        catalog_lookup: API名称到summary的映射字典，用于丰富prompt
    Returns:
        过滤后的组合列表（每个combo增加 coherence_score 和 coherence_reason 字段）
    """
    if not combinations:
        return []

    logging.info(
        f"Running coherence validation on {len(combinations)} combinations "
        f"(threshold={threshold}) ..."
    )

    if catalog_lookup:
        for combo in combinations:
            combo["_api_summaries"] = []
            for api_name in combo["apis"]:
                if api_name in catalog_lookup:
                    combo["_api_summaries"].append({
                        "name": api_name,
                        "summary": catalog_lookup[api_name],
                    })

    worker_args = [
        (i, combo, project_name, model_name)
        for i, combo in enumerate(combinations)
    ]
    # worker_args = worker_args[:1]

    n_proc = num_processes or cpu_count()
    n_proc = min(n_proc, len(worker_args))

    with Pool(processes=n_proc) as pool:
        results = pool.map(check_coherence_worker, worker_args)

    score_map = {}
    llm_io_map = {}
    for idx, res, messages, text in results:
        score_map[idx] = res
        llm_io_map[idx] = (messages, text)

    filtered = []
    score_dist = Counter()
    for i, combo in enumerate(combinations):
        res = score_map.get(i, {"score": 3, "reason": "missing"})
        combo["coherence_score"] = res["score"]
        combo["coherence_reason"] = res["reason"]
        if i in llm_io_map:
            combo["coherence_messages"] = llm_io_map[i][0]
            combo["coherence_response"] = llm_io_map[i][1]
        combo.pop("_api_summaries", None)
        score_dist[res["score"]] += 1

        if res["score"] >= threshold:
            filtered.append(combo)

    logging.info(f"Coherence score distribution: {dict(sorted(score_dist.items()))}")
    logging.info(
        f"Coherence filter: {len(filtered)}/{len(combinations)} passed "
        f"(threshold>={threshold})"
    )

    return filtered


# ---------------------------------------------------------------------------
# Quality Filter 2: Diversity Filtering (Jaccard Similarity)
# ---------------------------------------------------------------------------

# def jaccard_similarity(set_a, set_b):
#     """计算两个集合的Jaccard相似度"""
#     if not set_a and not set_b:
#         return 1.0
#     intersection = len(set_a & set_b)
#     union = len(set_a | set_b)
#     return intersection / union if union > 0 else 0.0


# def filter_by_diversity(combinations, max_jaccard=0.7):
#     """
#     多样性过滤：移除API集合高度重叠的组合。

#     使用贪心策略：按coherence_score降序排列，依次检查每个组合是否与
#     已保留组合的Jaccard相似度超过阈值。超过则丢弃。

#     Args:
#         combinations: 待过滤的组合列表
#         max_jaccard: 最大允许的Jaccard相似度 (默认0.7)
#     Returns:
#         过滤后的多样化组合列表
#     """
#     if not combinations:
#         return []

#     sorted_combos = sorted(
#         combinations,
#         key=lambda c: c.get("coherence_score", 3),
#         reverse=True,
#     )

#     kept = []
#     kept_sets = []

#     for combo in sorted_combos:
#         api_set = frozenset(
#             item["dependency_name"] for item in combo["source_code_dict"]
#         )

#         too_similar = False
#         for existing_set in kept_sets:
#             if jaccard_similarity(api_set, existing_set) > max_jaccard:
#                 too_similar = True
#                 break

#         if not too_similar:
#             kept.append(combo)
#             kept_sets.append(api_set)

#     logging.info(
#         f"Diversity filter: {len(kept)}/{len(combinations)} kept "
#         f"(max_jaccard={max_jaccard})"
#     )

#     return kept


def compute_coverage(combinations, catalog):
    """计算并报告API覆盖率统计"""
    coverage = Counter()
    for combo in combinations:
        for item in combo["source_code_dict"]:
            coverage[item["dependency_name"]] += 1

    total_apis = len(catalog)
    covered = len(coverage)
    uncovered = [
        api["entity_name"]
        for api in catalog
        if api["entity_name"] not in coverage
    ]

    logging.info(f"Coverage: {covered}/{total_apis} APIs ({covered / total_apis * 100:.1f}%)")
    if uncovered:
        logging.info(f"Uncovered APIs (first 20): {uncovered[:20]}")

    return coverage


# ---------------------------------------------------------------------------
# Worker Function
# ---------------------------------------------------------------------------

def generate_batch(args):
    """并行生成的worker函数"""
    batch_id, api_subset, seeds, project_name, model_name, num_to_generate = args

    messages = generate_combinations_prompt(
        api_subset, seeds, project_name, num_to_generate
    )
    # print(f"generate_batch messages: {messages}")
    text, usage = call_llm(messages, model_name)

    if text is None:
        logging.error(f"Batch {batch_id}: LLM call failed")
        return []

    combinations = parse_combination_response(text, api_subset)
    for combo in combinations:
        combo["generate_messages"] = messages
        combo["generate_response"] = text
    logging.info(
        f"Batch {batch_id}: Generated {len(combinations)} combinations "
        f"(tokens: {usage.get('total_tokens', '?')})"
    )
    return combinations


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate API combinations using LLM-driven Bootstrap + Scenario approach"
    )
    parser.add_argument(
        "--func_desc_dir", required=True,
        help="Directory containing functional description JSON files",
    )
    parser.add_argument("--output_dir", required=True, help="Output directory")
    parser.add_argument("--model_name", default="deepseek-v3.2", help="LLM model name")
    parser.add_argument("--project_name", required=True, help="Project name filter")
    parser.add_argument(
        "--seed_tasks", nargs="+", default=None,
        help="Seed task file(s) (JSON) for bootstrap initialization, supports multiple files",
    )
    parser.add_argument(
        "--num_combinations", type=int, default=10,
        help="Number of combinations to request per LLM call",
    )
    parser.add_argument(
        "--num_rounds", type=int, default=5,
        help="Number of bootstrap rounds",
    )
    parser.add_argument(
        "--num_batches_per_round", type=int, default=5,
        help="Number of parallel LLM calls per round",
    )
    parser.add_argument(
        "--apis_per_prompt", type=int, default=80,
        help="Max APIs included in each LLM prompt",
    )
    parser.add_argument(
        "--num_processes", type=int, default=None,
        help="Parallel processes (default: cpu_count)",
    )
    parser.add_argument(
        "--coherence_threshold", type=int, default=3,
        help="Min coherence score (1-5) to keep a combination (default: 3)",
    )
    parser.add_argument(
        "--skip_coherence", action="store_true",
        help="Skip LLM-based coherence validation (faster but lower quality)",
    )
    parser.add_argument(
        "--input_combinations", nargs="+", default=None,
        help="Pre-generated combination JSON file(s). When provided, skip Phase 2 & 3 "
             "and directly run Quality Filtering Pipeline (Phase 4+)",
    )
    args = parser.parse_args()

    # ---- Logging ----
    os.makedirs(args.output_dir, exist_ok=True)
    log_file = os.path.join(
        args.output_dir,
        f'generation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )

    num_processes = args.num_processes or cpu_count()

    # ---- Phase 1: Load API Catalog ----
    logging.info("Phase 1: Loading API catalog ...")
    catalog = load_api_catalog(args.func_desc_dir, args.project_name)
    logging.info(f"Loaded {len(catalog)} APIs for project '{args.project_name}'")

    if not catalog:
        logging.error("No APIs found. Check --project_name and --func_desc_dir.")
        return

    file_dist = Counter(
        get_file_from_location(a["entity_location"]) for a in catalog
    )
    logging.info(f"APIs distributed across {len(file_dist)} files")

    # ---- Load or Generate combinations ----
    if args.input_combinations:
        logging.info(
            f"Loading pre-generated combinations from {len(args.input_combinations)} file(s): "
            f"{args.input_combinations} ..."
        )
        all_combinations = []
        for combo_file in args.input_combinations:
            with open(combo_file, 'r', encoding='utf-8') as f:
                datas = json.load(f)
            logging.info(f"  Loaded {len(datas)} combinations from {combo_file}")
            for data in datas:
                if data["coherence_score"] >= 4:
                    all_combinations.append(data)
        logging.info(f"Total loaded: {len(all_combinations)} combinations, skipping Phase 2 & 3")

        if args.seed_tasks:
            logging.info("Computing API coverage against seed tasks ...")
            seeds = load_seed_combinations(args.seed_tasks)

            seed_api_set = set()
            for s in seeds:
                for item in s.get("source_code_dict", []):
                    loc = item.get("dependency_location", "")
                    fp = get_file_from_location(loc)
                    seed_api_set.add((item["dependency_name"], fp))

            def _compute_combo_api_set(combinations):
                result = set()
                for c in combinations:
                    for item in c.get("source_code_dict", []):
                        loc = item.get("dependency_location", "")
                        fp = get_file_from_location(loc)
                        result.add((item["dependency_name"], fp))
                return result

            combo_api_set = _compute_combo_api_set(all_combinations)
            uncovered = seed_api_set - combo_api_set
            coverage_pct = len(seed_api_set - uncovered) / len(seed_api_set) * 100 if seed_api_set else 0
            logging.info(
                f"  Seed APIs (name, file): {len(seed_api_set)}, "
                f"Covered: {len(seed_api_set - uncovered)}, "
                f"Uncovered: {len(uncovered)}, "
                f"Coverage: {coverage_pct:.1f}%"
            )
            if uncovered:
                uncovered_sorted = sorted(uncovered, key=lambda x: (x[1], x[0]))
                for name, fp in uncovered_sorted:
                    logging.info(f"    Uncovered: {name} @ {fp}")
            # return

            # ---- Supplementary Bootstrap Generation for uncovered seed APIs ----
            if uncovered:
                logging.info(
                    f"Starting supplementary generation to cover {len(uncovered)} uncovered seed APIs ..."
                )
                catalog_lookup_by_key = {}
                for api in catalog:
                    fp = get_file_from_location(api["entity_location"])
                    catalog_lookup_by_key[(api["entity_name"], fp)] = api

                max_supplement_rounds = args.num_rounds * 3
                for sup_round in range(1, max_supplement_rounds + 1):
                    uncovered_catalog = []
                    for key in uncovered:
                        if key in catalog_lookup_by_key:
                            uncovered_catalog.append(catalog_lookup_by_key[key])
                    if not uncovered_catalog:
                        logging.info("All uncovered APIs resolved (not in catalog), stopping.")
                        break

                    logging.info(
                        f"=== Supplement Round {sup_round}/{max_supplement_rounds} "
                        f"({len(uncovered_catalog)} uncovered APIs in catalog) ==="
                    )
                    # return

                    extra_needed = max(0, args.apis_per_prompt - len(uncovered_catalog))
                    if extra_needed > 0:
                        covered_catalog = [
                            api for api in catalog
                            if (api["entity_name"], get_file_from_location(api["entity_location"]))
                            not in uncovered
                        ]
                        extra = random.sample(
                            covered_catalog, min(extra_needed, len(covered_catalog))
                        )
                        api_subset = uncovered_catalog + extra
                    else:
                        api_subset = random.sample(uncovered_catalog, args.apis_per_prompt)

                    random.shuffle(api_subset)

                    relevant_chunks = select_relevant_seeds(seeds, api_subset, chunk_size=5)
                    batch_args = []
                    for chunk_idx, seed_chunk in enumerate(relevant_chunks):
                        current_seeds = seed_chunk.copy()
                        if all_combinations:
                            n_recent = min(3, len(all_combinations))
                            recent = random.sample(all_combinations, n_recent)
                            for combo in recent:
                                current_seeds.append({
                                    "apis": combo["apis"],
                                    "source_code_dict": combo["source_code_dict"],
                                })
                        batch_args.append((
                            f"SUP_R{sup_round}C{chunk_idx + 1}",
                            api_subset,
                            current_seeds,
                            args.project_name,
                            args.model_name,
                            args.num_combinations,
                        ))

                    with Pool(processes=min(num_processes, max(1, len(batch_args)))) as pool:
                        results = pool.map(generate_batch, batch_args)

                    new_combos = []
                    for batch_result in results:
                        new_combos.extend(batch_result)

                    new_combos = filter_combinations_dedup(new_combos)

                    existing_sets = set()
                    for c in all_combinations:
                        existing_sets.add(tuple(sorted(
                            item["dependency_name"] for item in c["source_code_dict"]
                        )))
                    truly_new = []
                    for c in new_combos:
                        key = tuple(sorted(
                            item["dependency_name"] for item in c["source_code_dict"]
                        ))
                        if key not in existing_sets:
                            truly_new.append(c)
                            existing_sets.add(key)

                    all_combinations.extend(truly_new)
                    logging.info(
                        f"Supplement Round {sup_round}: +{len(truly_new)} new "
                        f"(total: {len(all_combinations)})"
                    )

                    if truly_new:
                        temp_file = os.path.join(
                            args.output_dir,
                            f'combinations_supplement_{sup_round}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json',
                        )
                        with open(temp_file, 'w', encoding='utf-8') as f:
                            json.dump(truly_new, f, ensure_ascii=False, indent=2)

                    combo_api_set = _compute_combo_api_set(all_combinations)
                    uncovered = seed_api_set - combo_api_set
                    coverage_pct = len(seed_api_set - uncovered) / len(seed_api_set) * 100
                    logging.info(
                        f"  Updated coverage: {len(seed_api_set - uncovered)}/{len(seed_api_set)} "
                        f"({coverage_pct:.1f}%), still uncovered: {len(uncovered)}"
                    )

                    if not uncovered:
                        logging.info("100% seed API coverage achieved!")
                        break
                else:
                    logging.warning(
                        f"Reached max supplement rounds ({max_supplement_rounds}), "
                        f"still {len(uncovered)} uncovered APIs:"
                    )
                    for name, fp in sorted(uncovered, key=lambda x: (x[1], x[0])):
                        logging.warning(f"    {name} @ {fp}")
    else:
        # ---- Phase 2: Load Seed Combinations ----
        seeds = []
        if args.seed_tasks:
            logging.info(f"Phase 2: Loading seed combinations from {len(args.seed_tasks)} file(s): {args.seed_tasks} ...")
            seeds = load_seed_combinations(args.seed_tasks)
            logging.info(f"Loaded {len(seeds)} unique seed combinations")
        else:
            logging.info("Phase 2: No seed file provided, starting from scratch")

        # ---- Phase 3: Bootstrap Generation ----
        logging.info("Phase 3: Starting bootstrap generation ...")
        all_combinations = []
        coverage_counter = Counter()

        for round_num in range(args.num_rounds):
            logging.info(f"=== Round {round_num + 1}/{args.num_rounds} ===")
            batch_args = []

            for batch_id in range(args.num_batches_per_round):
                api_subset = sample_diverse_apis(
                    catalog,
                    args.apis_per_prompt,
                    coverage_counter if round_num > 0 else None,
                )

                relevant_chunks = select_relevant_seeds(
                    seeds, api_subset, chunk_size=5
                )
                for chunk_idx, seed_chunk in enumerate(relevant_chunks):
                    current_seeds = seed_chunk.copy()
                    if all_combinations:
                        n_recent = min(3, len(all_combinations))
                        recent = random.sample(all_combinations, n_recent)
                        for combo in recent:
                            current_seeds.append({
                                "apis": combo["apis"],
                                "source_code_dict": combo["source_code_dict"],
                            })

                    batch_args.append((
                        f"R{round_num + 1}B{batch_id + 1}C{chunk_idx + 1}",
                        api_subset,
                        current_seeds,
                        args.project_name,
                        args.model_name,
                        args.num_combinations,
                    ))

                # current_seeds = select_relevant_seeds(
                #     seeds, api_subset, max_seeds=5
                # )
                # if all_combinations:
                #     n_recent = min(3, len(all_combinations))
                #     recent = random.sample(all_combinations, n_recent)
                #     for combo in recent:
                #         current_seeds.append({
                #             "apis": combo["apis"],
                #             "source_code_dict": combo["source_code_dict"],
                #         })

                # batch_args.append((
                #     f"R{round_num + 1}B{batch_id + 1}",
                #     api_subset,
                #     current_seeds,
                #     args.project_name,
                #     args.model_name,
                #     args.num_combinations,
                # ))

            # 以api组合为核心
            # seed_chunks = split_seeds_into_chunks(seeds, chunk_size=5)
            # logging.info(
            #     f"Split {len(seeds)} seeds into {len(seed_chunks)} chunks"
            # )

            # batch_args = []
            # for chunk_idx, seed_chunk in enumerate(seed_chunks):
            #     api_subset = sample_apis_relevant_to_seeds(
            #         catalog,
            #         seed_chunk,
            #         args.apis_per_prompt,
            #         coverage_counter if round_num > 0 else None,
            #     )

            #     current_seeds = list(seed_chunk)
            #     if all_combinations:
            #         n_recent = min(3, len(all_combinations))
            #         recent = random.sample(all_combinations, n_recent)
            #         for combo in recent:
            #             current_seeds.append({
            #                 "apis": combo["apis"],
            #                 "source_code_dict": combo["source_code_dict"],
            #             })

            #     batch_args.append((
            #         f"R{round_num + 1}C{chunk_idx + 1}",
            #         api_subset,
            #         current_seeds,
            #         args.project_name,
            #         args.model_name,
            #         args.num_combinations,
            #     ))

            with Pool(processes=min(num_processes, len(batch_args))) as pool:
                results = pool.map(generate_batch, batch_args)

            round_combinations = []
            for batch_result in results:
                round_combinations.extend(batch_result)

            logging.info(
                f"Round {round_num + 1}: Generated {len(round_combinations)} raw combinations"
            )

            new_combos = filter_combinations_dedup(round_combinations)

            existing_sets = set()
            for c in all_combinations:
                existing_sets.add(tuple(sorted(
                    item["dependency_name"] for item in c["source_code_dict"]
                )))
            truly_new = []
            for c in new_combos:
                key = tuple(sorted(
                    item["dependency_name"] for item in c["source_code_dict"]
                ))
                if key not in existing_sets:
                    truly_new.append(c)
                    existing_sets.add(key)

            all_combinations.extend(truly_new)

            for combo in truly_new:
                for item in combo["source_code_dict"]:
                    coverage_counter[item["dependency_name"]] += 1

            logging.info(
                f"Round {round_num + 1}: +{len(truly_new)} new (total: {len(all_combinations)})"
            )

            temp_file = os.path.join(
                args.output_dir,
                f'combinations_round_{round_num + 1}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json',
            )
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(truly_new, f, ensure_ascii=False, indent=2)

    # ---- Phase 4: Quality Filtering Pipeline ----
    logging.info("Phase 4: Quality filtering pipeline ...")
    logging.info(f"  Starting with {len(all_combinations)} combinations")

    # Step 4a: Exact dedup
    all_combinations = filter_combinations_dedup(all_combinations)
    logging.info(f"  After exact dedup: {len(all_combinations)}")

    # Step 4b: Functional coherence validation (LLM-based)
    if not args.skip_coherence and all_combinations:
        catalog_lookup = {api["entity_name"]: api["summary"] for api in catalog}
        all_combinations = filter_by_coherence(
            all_combinations,
            project_name=args.project_name,
            model_name=args.model_name,
            threshold=args.coherence_threshold,
            num_processes=num_processes,
            catalog_lookup=catalog_lookup,
        )
        logging.info(f"  After coherence filter: {len(all_combinations)}")
    elif args.skip_coherence:
        logging.info("  Coherence validation skipped (--skip_coherence)")

    coverage = compute_coverage(all_combinations, catalog)

    # ---- Phase 5: Save outputs ----
    logging.info("Phase 5: Saving outputs ...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    full_output = os.path.join(
        args.output_dir, f'api_combinations_{args.project_name}_{ts}.json'
    )
    with open(full_output, 'w', encoding='utf-8') as f:
        json.dump(all_combinations, f, ensure_ascii=False, indent=2)
    logging.info(f"Full output saved to {full_output}")

    tasks_output = []
    for combo in all_combinations:
        task_item = {
            "source_code_dict": combo["source_code_dict"],
            "summary": combo.get("summary", ""),
            "detail_description": combo.get("detail_description", ""),
            "generate_messages": combo.get("generate_messages"),
            "generate_response": combo.get("generate_response"),
            "coherence_messages": combo.get("coherence_messages"),
            "coherence_response": combo.get("coherence_response"),
        }
        tasks_output.append(task_item)
    tasks_file = os.path.join(
        args.output_dir, f'tasks_{args.project_name}_{ts}.json'
    )
    with open(tasks_file, 'w', encoding='utf-8') as f:
        json.dump(tasks_output, f, ensure_ascii=False, indent=2)
    logging.info(f"Tasks format saved to {tasks_file}")

    logging.info("=== Final Summary ===")
    logging.info(f"Total unique combinations: {len(all_combinations)}")
    if catalog:
        logging.info(
            f"API coverage: {len(coverage)}/{len(catalog)} "
            f"({len(coverage) / len(catalog) * 100:.1f}%)"
        )
    if all_combinations:
        avg_size = sum(
            len(c["source_code_dict"]) for c in all_combinations
        ) / len(all_combinations)
        logging.info(f"Avg APIs per combination: {avg_size:.1f}")

    top_apis = coverage.most_common(10)
    logging.info(f"Most used APIs: {top_apis}")


if __name__ == "__main__":
    start = time.time()
    main()
    print(f"Total time: {(time.time() - start) / 60:.2f} minutes")
