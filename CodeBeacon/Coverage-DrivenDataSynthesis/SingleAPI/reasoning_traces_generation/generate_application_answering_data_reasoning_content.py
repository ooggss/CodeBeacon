"""
应用数据生成器
================

该脚本用于生成目标项目相关的应用题目数据，包括问答题、填空题和编程题。
支持处理目标项目的源代码，并使用OpenAI API生成题目内容。

主要功能：
- 从目标项目的源代码文件中提取实体信息
- 基于模板生成不同类型和难度的题目
- 支持并行处理以提高生成效率
- 自动验证生成内容的有效性，确保题目内容符合要求
"""

import os
import json
import sys
import re
import logging
import time
import uuid
from datetime import datetime
from jinja2 import Template
import re
from multiprocessing import Pool, cpu_count
from generation import generate_with_messages
# import tqdm


# 生成唯一的UUID用于标识本次运行
CURR_UUID = str(uuid.uuid4())

def generate_reasoning_content(model_name, task, prompt_id):
    prompt = task["prompt"]
    
    # 使用正则表达式从prompt中提取system_prompt和user_prompt
    message_pattern = re.compile(
        r'(<message role="(system|user|assistant)">)(.*?)(</message>)',
        re.DOTALL,
    )
    messages = message_pattern.findall(prompt)
    system_prompt = ""
    user_prompt = ""
    
    # 解析消息内容
    for match in messages:
        role = match[1]
        content = match[2].strip()
        if role == "system":
            system_prompt = content
        elif role == "user":
            user_prompt = content
    
    # 生成结果并添加source_code_dict
    result = generate_with_messages(
        model_name, 
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], 
        prompt_id,
        # tags=["<question>", "</question>", "<answer>", "</answer>", "<analysis>", "</analysis>"],
        # reasoning_tags=["Grading Rubric Requirements", "grading rubric requirements"],
        args={"thinking": {"type": "enabled"}}
    )

    return result

def process_batch(args):
    """
    处理单个批次的任务
    
    解析提示词中的system和user消息，然后调用生成函数。
    这个函数设计为可以被多进程调用。
    
    Args:
        args (tuple): 包含(model_name, task, prompt_id)的元组，其中task包含prompt和source_code_dict
        
    Returns:
        dict: 生成的结果字典，包含source_code_dict
    """
    
    
    model_name, task, prompt_id = args
    print(f"Generating reasoning content for {prompt_id}")

    for i in range(5):
        result = generate_reasoning_content(model_name, task, prompt_id)
        try:
            result_json = json.loads(result['text'])
            if len(result_json) == 1 and result_json[0]['name'] in task['answer']['name'] and result_json[0]['location'] == task['answer']['location']:
                result['generation_success'] = True
                break
            else:
                continue
        except:
            continue

    if not result.get('generation_success', False):
        result['generation_success'] = False

    result['repeated_times'] = i + 1
    result['answer'] = task['answer']
    result["original_item"] = task["original_item"]
    
    logging.info(f"finally successfully generated reasoning content for {prompt_id}")
    print(f"finally successfully generated reasoning content for {prompt_id}")

    return result

# 项目功能描述映射
PROJECT_DESCRIPTIONS = {
    "LEANN": "LEANN is an innovative vector database system that achieves 97% storage savings through graph-based selective recomputation, transforming laptops into powerful local RAG systems capable of semantic search across diverse personal data sources including documents, emails, chat histories, and browser data, all while maintaining zero accuracy loss with complete privacy protection and zero cloud costs.",
    "Hexi": "Hexi is a lightweight C++23 header-only library designed for safe handling of network and binary data, providing zero-overhead type-safe serialization/deserialization through compile-time optimization, with built-in bounds checking and endianness conversion support, widely used in network protocol implementation and game emulator development.",
    "reaction": "Reaction is a high-performance C++20 reactive programming framework that brings React/Vue-style dataflow patterns to native C++, achieving 2-3x faster performance than rxcpp through automatic DAG dependency tracking and zero-cost abstractions, particularly suited for UI data binding, game logic state management, and real-time financial calculations.",
    "sqlgen": "sqlgen is a modern type-safe C++20 ORM framework inspired by Python's SQLAlchemy and Rust's Diesel, providing SQL injection protection and complex query composition through compile-time type checking and fluent API, supporting PostgreSQL, MySQL, and SQLite with deep integration with reflect-cpp for efficient ETL pipelines."
}

def generate_prompt_for_item(item, template):
    """为单个item生成prompt内容"""
    functional_description = item['query'].strip()
    
    # 组织每个project的数据
    projects = {}
    for project_name, project_content in item['similarities_by_project'].items():
        projects[project_name] = {
            'description': PROJECT_DESCRIPTIONS[project_name],
            'items': []
        }
        for tmp_item in project_content['top_10_similar']:
            projects[project_name]['items'].append({
                'name': re.findall(r'`(.*?)`', tmp_item['target_answer'], re.DOTALL)[0].strip(),
                'type': tmp_item['target_answer'].split('`')[0].strip(),
                'target_query': tmp_item['target_query'].strip(),
                'target_entity_code': tmp_item['target_entity_code'],
                'target_answer': re.findall(r'file `(.*?)`', tmp_item['target_answer'], re.DOTALL)[0].strip(),
                'target_entity_dependency': tmp_item['target_entity_dependency']
            })
    
    # 渲染模板
    prompt_content = template.render(
        functional_description=functional_description,
        projects=projects
    )
    
    return prompt_content


def process_usage_data(input_json_path):
    """
    处理用法数据，生成所有相关的问题提示词
    
    遍历给定的用法数据，为每个文件、实体生成对应的提示词。
    这是数据生成的核心逻辑，会产生大量的提示词用于后续的批量处理。
    """
    
    with open('prompt_single_api.j2', 'r', encoding='utf-8') as f:
        template_content = f.read()
    template = Template(template_content)

    tasks = []

    with open(input_json_path, 'r') as f:
        data = json.load(f)
        for item in data:
            prompt = generate_prompt_for_item(item, template)
        

            tasks.append({
                "prompt": prompt,
                "answer": {
                    "type": item['query_answer'].split('`')[0].strip(),
                    "name": re.findall(r'`(.*?)`', item['query_answer'], re.DOTALL)[0].strip(),
                    "location": re.findall(r'file `(.*?)`', item['query_answer'], re.DOTALL)[0].strip()
                },
                "original_item": item
            })

        
    return tasks

def main():
    """
    主函数：协调整个数据生成流程
    
    1. 解析命令行参数
    2. 配置日志系统
    3. 加载源数据文件
    4. 生成所有问题提示词
    5. 批量并行处理生成题目
    6. 保存结果到文件
    """
    # 解析命令行参数
    model_name = sys.argv[1]        # 模型名称
    input_json_path = sys.argv[2]   # 输入JSON文件路径
    num_processes = int(sys.argv[3]) if len(sys.argv) > 3 else cpu_count()  # 进程数，默认为CPU核心数

    # 配置日志系统
    # 文件处理器 - 记录所有信息到日志文件
    file_handler = logging.FileHandler(input_json_path.replace('.json', f'_reasoning_content_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'), mode='a')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # 控制台处理器 - 只显示警告及以上级别的信息
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # 配置根日志记录器
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )

        

    
    # 处理用法数据，生成所有问题提示词
    tasks = process_usage_data(input_json_path)
    print(len(tasks))
    # with open(input_json_path.replace('.json', '_reasoning_content_tasks.json'), 'w') as f:
    #     json.dump(tasks, f, ensure_ascii=False, indent=2)
    # sys.exit()
    # 数据增强：重复生成多次以增加数据量
    # _tasks = copy.deepcopy(tasks)
    # for _ in range(4):
    #     tasks.extend(copy.deepcopy(_tasks))

    # logging.info(f"Generated {len(tasks)} questions")

    # 批量处理配置
    batch_size = 1000  # 每批处理1000个任务
    # tasks = tasks[:10]

    print(f"Start to generate {len(tasks)} reasoning content")
    logging.info(f"Start to generate {len(tasks)} reasoning content")

    if not os.path.exists(os.path.join(os.path.dirname(input_json_path), "reasoning_content_2", os.path.basename(input_json_path).replace('.json', ''))):
        os.makedirs(os.path.join(os.path.dirname(input_json_path), "reasoning_content_2", os.path.basename(input_json_path).replace('.json', '')))

    # 使用多进程池进行并行处理
    with Pool(processes=num_processes) as pool:
        # 分批处理所有任务
        for batch_start in range(0, len(tasks), batch_size):
            batch_end = min(batch_start + batch_size, len(tasks))
            batch_tasks = tasks[batch_start:batch_end]
            
            print(f"Processing batch {batch_start//batch_size + 1}/{(len(tasks) + batch_size - 1)//batch_size}: reasoning content {batch_start+1}-{batch_end}")
            logging.info(f"Processing batch {batch_start//batch_size + 1}/{(len(tasks) + batch_size - 1)//batch_size}: reasoning content {batch_start+1}-{batch_end}")
            
            # 准备当前批次的任务参数
            task_args = [(model_name, task, prompt_id) for prompt_id, task in enumerate(batch_tasks, start=batch_start)]
            
            # 并行执行当前批次的所有任务
            batch_results = pool.map(process_batch, task_args)
            # batch_results = []
            # 备用的串行处理代码（调试用）
            # batch_results = []
            # for task_arg in task_args:
            #     batch_results.append(process_batch(task_arg))
            
            # 记录批次完成情况
            logging.warning(f'Completed {batch_end}/{len(tasks)} tasks')
            
            # 保存批次结果到临时文件
            temp_output_file = os.path.join(os.path.dirname(input_json_path), "reasoning_content_2", os.path.basename(input_json_path).replace('.json', ''), f'reasoning_content_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{len(batch_results)}.json')
            with open(temp_output_file, 'w', encoding='utf-8') as f:
                json.dump(batch_results, f, ensure_ascii=False, indent=2)
            logging.warning(f"Saved {len(batch_results)} questions to temporary file: {temp_output_file}")
            
            logging.warning(f"Completed batch {batch_start//batch_size + 1}, total completed: {batch_end}/{len(tasks)}")
            print(f"Completed batch {batch_start//batch_size + 1}, total completed: {batch_end}/{len(tasks)}")
    
if __name__ == "__main__":
    # time.sleep(3600 * 4)
    # 记录程序开始时间
    start = time.time()
    
    # 运行主函数
    main()
    
    # 输出总耗时
    print(f"Total time cost: {(time.time() - start)/60:.2f} minutes")

#  python3 generate_application_answering_data_reasoning_content.py deepseek-v3.1-terminus api-oriented_rl_data_20251128/api-oriented_rl_data_processed_processed_filter_20251129124812.json
