"""
多API推理过程生成器（Rationalize 模式）
================

基于 extracted_functionality_descriptions.json 中的多功能描述和
total_used_dependencies / total_similarities_by_project 数据，
生成多keyword推理过程的采样。

将正确答案通过 system prompt 告知模型，
让模型生成自然的分析推理过程（Rationalization）。
优势：采样成功率接近100%，推理过程无 artifact
"""

import os
import json
import sys
import re
import logging
import time
import uuid
import argparse
from datetime import datetime
from jinja2 import Template
from multiprocessing import Pool, cpu_count

from generation import generate_with_messages

CURR_UUID = str(uuid.uuid4())

PROJECT_DESCRIPTIONS = {
    "LEANN": "LEANN is an innovative vector database system that achieves 97% storage savings through graph-based selective recomputation, transforming laptops into powerful local RAG systems capable of semantic search across diverse personal data sources including documents, emails, chat histories, and browser data, all while maintaining zero accuracy loss with complete privacy protection and zero cloud costs.",
    "Hexi": "Hexi is a lightweight C++23 header-only library designed for safe handling of network and binary data, providing zero-overhead type-safe serialization/deserialization through compile-time optimization, with built-in bounds checking and endianness conversion support, widely used in network protocol implementation and game emulator development.",
    "reaction": "Reaction is a high-performance C++20 reactive programming framework that brings React/Vue-style dataflow patterns to native C++, achieving 2-3x faster performance than rxcpp through automatic DAG dependency tracking and zero-cost abstractions, particularly suited for UI data binding, game logic state management, and real-time financial calculations.",
    "sqlgen": "sqlgen is a modern type-safe C++20 ORM framework inspired by Python's SQLAlchemy and Rust's Diesel, providing SQL injection protection and complex query composition through compile-time type checking and fluent API, supporting PostgreSQL, MySQL, and SQLite with deep integration with reflect-cpp for efficient ETL pipelines."
}


def parse_answer_from_dependency(dep):
    """从 total_used_dependencies 的一个 item 中提取 name 和 location。"""
    name = dep.get("dependency_name", "").split('::')[-1].strip()
    desc = dep.get("dependency_location") or ""

    location = desc.split('the file')[-1].strip().replace('`', '').strip()
    type = desc.strip().split(' ')[0].strip()

    return {"name": name, "location": location, "type": type}


def get_answer_entities_with_code(item, answers):
    """从 total_similarities_by_project 中提取 answer 实体对应的源代码。"""
    code_map = {}
    for project_content in item.get('total_similarities_by_project', {}).values():
        for sim_item in project_content.get('top_10_similar', []):
            target_answer_raw = sim_item.get('target_answer', '')
            name_matches = re.findall(r'`(.*?)`', target_answer_raw)
            name = name_matches[0].strip() if name_matches else ''
            loc_matches = re.findall(r'file `(.*?)`', target_answer_raw)
            location = loc_matches[0].strip() if loc_matches else ''
            code_map[(name, location)] = sim_item.get('target_entity_code', '')

    enriched = []
    for answer in answers:
        detail = dict(answer)
        detail['source_code'] = ''
        for (cand_name, cand_loc), code in code_map.items():
            if answer['name'].strip() in cand_name and answer['location'].strip() in cand_loc:
                detail['source_code'] = code
                break
        enriched.append(detail)
    return enriched


def generate_prompt_for_item_rationalize(item, template, answers):
    """为单个 item 生成 rationalize 模式的 prompt。

    答案实体（含源码）通过 system prompt 提供给模型，
    user prompt 保持与推理时一致（不含候选列表和数量约束）。
    """
    task = item['functionality_description'].strip()
    answer_details = get_answer_entities_with_code(item, answers)

    prompt_content = template.render(
        task=task,
        answers=answer_details
    )

    return prompt_content


def generate_reasoning_content(model_name, task, prompt_id):
    """调用模型生成推理内容。"""
    prompt = task["prompt"]

    message_pattern = re.compile(
        r'(<message role="(system|user|assistant)">)(.*?)(</message>)',
        re.DOTALL,
    )
    messages = message_pattern.findall(prompt)
    system_prompt = ""
    user_prompt = ""

    for match in messages:
        role = match[1]
        content = match[2].strip()
        if role == "system":
            system_prompt = content
        elif role == "user":
            user_prompt = content

    result = generate_with_messages(
        model_name,
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        prompt_id,
        args={"thinking": {"type": "enabled"}}
    )

    return result


def verify_reasoning_quality(reasoning, expected_answers):
    """验证推理过程是否提及了预期的代码实体名称。

    返回 (mentioned_count, total_expected)
    """
    if not reasoning:
        return 0, len(expected_answers)
    mentioned = sum(1 for ans in expected_answers if ans['name'] in reasoning)
    return mentioned, len(expected_answers)


def process_batch_rationalize(args):
    """rationalize 模式：答案已知，生成自然推理过程。

    验证标准更宽松：
    1. 推理过程提及了预期实体名称（主要指标）
    2. 输出 JSON 格式正确（次要，因为 transfer 时使用预定义 answers）
    """
    model_name, task, prompt_id, max_retries = args
    print(f"[rationalize] Generating for {prompt_id}")

    expected_answers = task['answers']
    best_result = {}
    best_mention_count = -1
    attempts = 0

    for i in range(max_retries):
        attempts = i + 1
        result = generate_reasoning_content(model_name, task, prompt_id)
        reasoning = result.get('reasoning', '')

        if not reasoning:
            continue

        mentioned, total_expected = verify_reasoning_quality(reasoning, expected_answers)

        if mentioned > best_mention_count:
            best_mention_count = mentioned
            best_result = result

        if mentioned == total_expected:
            break

    if not best_result:
        best_result = result if result else {}

    best_result['generation_success'] = best_mention_count > 0
    best_result['reasoning_mentioned'] = best_mention_count
    best_result['total_expected'] = len(expected_answers)
    best_result['repeated_times'] = attempts
    best_result['answers'] = expected_answers
    best_result['original_item'] = task['original_item']

    logging.info(f"[rationalize] {prompt_id}: success={best_result['generation_success']}, "
                 f"reasoning_mentioned={best_mention_count}/{len(expected_answers)}")
    print(f"[rationalize] Done {prompt_id}: success={best_result['generation_success']}, "
          f"reasoning_mentioned={best_mention_count}/{len(expected_answers)}")

    return best_result


def process_usage_data(input_json_path, template_path='prompt_multi_api_rationalize.j2', target_project=None):
    """从 extracted_functionality_descriptions.json 加载数据并生成 prompt 列表（rationalize 模式）。"""
    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()
    template = Template(template_content)

    tasks = []

    with open(input_json_path, 'r') as f:
        data = json.load(f)

    for item in data:
        if not item.get('total_used_dependencies') or not item.get('total_similarities_by_project'):
            continue

        answers = [parse_answer_from_dependency(dep) for dep in item['total_used_dependencies'] if "similarities_by_project" in dep]

        prompt = generate_prompt_for_item_rationalize(item, template, answers)

        tasks.append({
            "prompt": prompt,
            "answers": answers,
            "task": item['functionality_description'].strip(),
            "original_item": {
                "source_file": item.get("source_file", ""),
                "item_index": item.get("item_index", ""),
                "functionality_description": item.get("functionality_description", ""),
                "total_used_dependencies": item.get("total_used_dependencies", []),
            }
        })

    return tasks


def parse_args():
    parser = argparse.ArgumentParser(description="多API推理过程生成器（Rationalize 模式）")
    parser.add_argument("model_name", help="模型名称，如 deepseek-v3.2")
    parser.add_argument("input_json_path", help="输入 JSON 路径")
    parser.add_argument("target_project", help="目标项目名称")
    parser.add_argument("-n", "--num-processes", type=int, default=cpu_count(),
                        help="并行进程数（默认 CPU 核数）")
    parser.add_argument("--max-retries", type=int, default=3,
                        help="每个样本的最大重试次数（默认 3）")
    parser.add_argument("--batch-size", type=int, default=1110,
                        help="每批处理的任务数（默认 1110）")
    return parser.parse_args()


def main():
    args = parse_args()

    log_path = args.input_json_path.replace(
        '.json', f'_multi_api_reasoning_rationalize_supplemented_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    )
    file_handler = logging.FileHandler(log_path, mode='a')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )

    template_path = os.path.join(os.path.dirname(__file__), 'prompt_multi_api_rationalize.j2')

    tasks = process_usage_data(args.input_json_path, template_path, args.target_project)
    print(f"Total tasks: {len(tasks)}, mode: rationalize")

    output_base_dir = os.path.join(
        os.path.dirname(args.input_json_path),
        "reasoning_content_multi_api_rationalize_supplemented",
        os.path.basename(args.input_json_path).replace('.json', '')
    )
    os.makedirs(output_base_dir, exist_ok=True)

    print(f"Start to generate {len(tasks)} multi-api reasoning content (mode=rationalize)")
    logging.info(f"Start to generate {len(tasks)} multi-api reasoning content (mode=rationalize)")

    args.batch_size = len(tasks)
    with Pool(processes=args.num_processes) as pool:
        for batch_start in range(0, len(tasks), args.batch_size):
            batch_end = min(batch_start + args.batch_size, len(tasks))
            batch_tasks = tasks[batch_start:batch_end]

            print(f"Processing batch {batch_start // args.batch_size + 1}/"
                  f"{(len(tasks) + args.batch_size - 1) // args.batch_size}: "
                  f"tasks {batch_start + 1}-{batch_end}")
            logging.info(f"Processing batch {batch_start // args.batch_size + 1}/"
                         f"{(len(tasks) + args.batch_size - 1) // args.batch_size}: "
                         f"tasks {batch_start + 1}-{batch_end}")

            task_args = [
                (args.model_name, task, prompt_id, args.max_retries)
                for prompt_id, task in enumerate(batch_tasks, start=batch_start)
            ]

            batch_results = pool.map(process_batch_rationalize, task_args)

            success_count = sum(1 for r in batch_results if r.get('generation_success'))
            logging.warning(f'Completed {batch_end}/{len(tasks)} tasks, '
                            f'success: {success_count}/{len(batch_results)}')

            temp_output_file = os.path.join(
                output_base_dir,
                f'reasoning_content_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{len(batch_results)}.json'
            )
            with open(temp_output_file, 'w', encoding='utf-8') as f:
                json.dump(batch_results, f, ensure_ascii=False, indent=2)
            logging.warning(f"Saved {len(batch_results)} results to: {temp_output_file}")

            print(f"Completed batch {batch_start // args.batch_size + 1}, "
                  f"success: {success_count}/{len(batch_results)}, "
                  f"total: {batch_end}/{len(tasks)}")


if __name__ == "__main__":
    start = time.time()
    main()
    print(f"Total time cost: {(time.time() - start) / 60:.2f} minutes")

# 用法:
# python3 generate_multi_api_reasoning_content.py deepseek-v3.2 /path/to/data.json target_project -n 32
