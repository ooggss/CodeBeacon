import asyncio
import json
import os
import sys
from typing import List, Dict

import random
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from functools import partial
import time
import uuid
import re
import subprocess
import shutil
import threading
import queue
from tqdm import tqdm
import gc  # 添加垃圾回收


CONCURRENCY_LIMIT = 10


env = os.environ.copy()
env.setdefault("ANTHROPIC_BASE_URL", "http://127.0.0.1:10086")

# 评测队列和控制变量
evaluation_queue = queue.Queue()
evaluation_done = threading.Event()  # 用于通知评测线程结束
all_generation_done = threading.Event()  # 用于标记所有生成任务是否完成
build_lock = threading.Lock()  # 用于保证同时只有一个 build.sh 在执行



def _run_agent_sync(instruction: str, model_name: str, cwd: str) -> subprocess.CompletedProcess:
    """在线程中执行的同步 agent 调用，避免阻塞事件循环"""
    result = subprocess.run(
        ["claude", "-p", instruction, "--model", model_name,
        "--allowedTools", "Read,Grep,Glob", "--max-turns", "15",
        "--output-format", "json"],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=500
    )
    return json.loads(result.stdout.decode())


async def generation(executor, model_name, query, semaphore, target_codebases_path: str):
    async with semaphore:
        loop = asyncio.get_event_loop()
        
        for attempt in range(10):
            print(f"start generation")
            # 必须在线程池中执行 subprocess.run，否则会阻塞事件循环导致串行
            start_time = time.time()
            instruction = query["instruction"] 
            try:
                response = await loop.run_in_executor(
                    executor,
                    _run_agent_sync,
                    instruction,
                    model_name,
                    target_codebases_path,
                )
                end_time = time.time()
                output = response["result"]
            except Exception as e:
                print(f"retry {attempt + 1} times, error: {e}")
                continue
            # print(f"response: {response}")
            if output.strip() == "":
                continue
            try:
                # 尝试多种解析方式
                result = None

                text_content = output.strip()
                
                
                
                # 方法1: 寻找```cpp...```块，使用非贪婪匹配
                pattern = re.compile(r'```cpp\s*(.*?)\s*```', re.DOTALL)
                match = pattern.search(text_content)
                if match:
                    result = match.group(1).strip()
                
                if result is None:
                    pattern = re.compile(r'```c\+\+\s*(.*?)\s*```', re.DOTALL)
                    match = pattern.search(text_content)
                    if match:
                        result = match.group(1).strip()
                    else:
                        result = text_content.strip()
                
                if result is None:
                    result = text_content.strip()
                
            except Exception as e:
                pass
            
            if result is None:
                query["output"] = {
                    "message" : instruction,
                    "reasoning" : "",
                    "text" : "",
                    "result" : None,
                    "time" : end_time - start_time,
                    "response": response
                }
            else:
                query["output"] = {
                    "message": instruction,
                    "reasoning": "",
                    "text": output,
                    "result": result,
                    "time" : end_time - start_time,
                    "response": response
                }

            print("success")
            return query
        
        # query["output"] = None
        # return query


async def synthesize(queries: List[Dict], model_name, output_file: str, target_codebases_path: str):
    print(f"max concurrency: {CONCURRENCY_LIMIT}")
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    
    
    with ThreadPoolExecutor(max_workers=CONCURRENCY_LIMIT) as executor:
        tasks = [
            asyncio.create_task(generation(executor, model_name, query, semaphore, target_codebases_path))
            for query in queries
        ]
        print(f"开始并发执行 {len(tasks)} 个任务...\n")
        results = await asyncio.gather(*tasks)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

def get_test_case_content(original_test_case_content: str):
    original_test_case_content = original_test_case_content.split("\n")
    for i, line in enumerate(original_test_case_content):
        if line.strip().startswith("#include \"") and line.strip().endswith(".hpp\"") and not line.strip().endswith("\"eas_common.hpp\""):
            original_test_case_content[i] = "#include \"implementation.hpp\""
            break
    return "\n".join(original_test_case_content)

def run_evaluation_for_single_file(input_json_file: str, output_json_dir: str, target_project_path: str):
    """
    对单个生成结果文件执行评测（与 run_evaluation.py 逻辑一致）
    """
    input_json_filename = os.path.basename(input_json_file)
    output_json = os.path.join(output_json_dir, input_json_filename)
    
    if os.path.exists(output_json):
        print(f"[评测] 已存在 {output_json}")
        return
    
    if not os.path.exists(output_json_dir):
        os.makedirs(output_json_dir)

    with open(input_json_file, 'r', encoding='utf-8') as f:
        results = json.load(f)
    
    print(f"[评测] 开始评测: {input_json_file}")
    
    compile_success_cnt = 0
    test_success_cnt = 0
    null_cnt = 0
    
    for result in tqdm(results):
        target_function_path = os.path.join(target_project_path, "include", f"implementation.hpp")
        test_case_path = os.path.join(target_project_path, "tests", f"tests.cpp")
        # target_function_path = result["file path"]
        generate_result = result["output"]["result"]
        test_case_content = result["test cases"]
        
        if not generate_result:
            null_cnt += 1
            continue
        
        # generate_result = generate_result.replace(
        #     "\ninline", 
        #     "\nusing namespace sqlgen;\nusing namespace sqlgen::literals;\ninline"
        # )
        
        result["evaluation_result"] = {}
        result["evaluation_message"] = {}
        
        # 备份
        shutil.copy(target_function_path, f"{target_function_path}.copy")
        
        try:
            print(f"start evaluation task: {result['id']}")
            # 写入结果
            with open(target_function_path, 'w', encoding='utf-8') as f:
                f.write(generate_result)
            
            with open(test_case_path, 'w', encoding='utf-8') as f:
                f.write(test_case_content)
            
            # 编译
            compile_proc = None
            while True:
                compile_proc = subprocess.run(
                    ["cmake", "--build", "build", "-j", "4"],
                    cwd=target_project_path,
                    capture_output=True
                )
                compile_output = compile_proc.stdout.decode('utf-8', errors='replace')
                compile_error = compile_proc.stderr.decode('utf-8', errors='replace')

                if "Killed signal terminated program cc1plus" in compile_error:
                    # 清理进程资源
                    continue
                else:
                    break
            
            result["evaluation_message"]["compile_result"] = {
                "output": compile_output,
                "error": compile_error
            }

            
            if compile_error:
                result["evaluation_result"]["compile_result"] = False
                result["evaluation_result"]["test_result"] = False
                print(f"task compiled failed: {result['id']}")
                continue
            else:
                result["evaluation_result"]["compile_result"] = True
            
                        
            # 立即释放原始输出内存和进程对象
            del compile_output, compile_error, compile_proc

            compile_success_cnt += 1
            
            # 测试
            test_proc = subprocess.run(
                ["ctest"],
                cwd=os.path.join(target_project_path, "build"),
                capture_output=True
            )
            test_output = test_proc.stdout.decode('utf-8', errors='replace')
            test_error = test_proc.stderr.decode('utf-8', errors='replace')
            
            result["evaluation_message"]["test_result"] = {
                "output": test_output,
                "error": test_error
            }
            
            if test_error:
                result["evaluation_result"]["test_result"] = False
                print(f"task test failed: {result['id']}")
            else:
                result["evaluation_result"]["test_result"] = True
                print(f"task test success: {result['id']}")
                test_success_cnt += 1
            
            # time.sleep(180)
        
        finally:
            # 恢复
            shutil.copy(f"{target_function_path}.copy", target_function_path)
            os.remove(f"{target_function_path}.copy")
    
    print(f"[评测] {input_json_filename} - compile_success: {compile_success_cnt}, test_success: {test_success_cnt}, null: {null_cnt}, total: {len(results)}")
    print(f"[评测] compile_rate: {compile_success_cnt/float(len(results)):.4f}, test_rate: {test_success_cnt/float(len(results)):.4f}")
    
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    
    print(f"[评测] 输出路径: {output_json}")
    
    # 显式释放内存
    del results
    gc.collect()

def create_project_copy(source_dir: str, copy_dir: str) -> str:
    """
    创建项目的工作副本
    
    Args:
        source_dir: 源项目目录
        work_dir: 工作目录（存放副本的父目录）
        worker_id: 工作进程 ID
    
    Returns:
        副本目录路径
    """
    
    if os.path.exists(copy_dir):
        shutil.rmtree(copy_dir)
    
    # 复制项目（排除不必要的大文件）
    shutil.copytree(
        source_dir, 
        copy_dir,
        ignore=shutil.ignore_patterns(
            '*.o', '*.a', '.git', '__pycache__', 
            '*.pyc', 'CMakeFiles', 'Testing',
            "build"
        )
    )
    

    # 使用锁保证同时只有一个 build.sh 在执行，并重复执行直到成功
    with build_lock:
        start_time = time.time()
        while True:
            compile_proc = subprocess.run(
                ["./build.sh"],
                cwd=copy_dir,
                capture_output=True
            )
            if compile_proc.returncode == 0:
                print(f"[构建] {copy_dir} 构建成功, 耗时 {time.time() - start_time:.2f} 秒")
                break
            else:
                print(f"[构建] {copy_dir} 构建失败 (返回码: {compile_proc.returncode})，重试中...")
                time.sleep(0.1)  # 等待1秒后重试

    return copy_dir

def single_evaluation_task(task, output_json_dir: str, target_project_path: str):
    """
    执行单个评测任务
    
    Args:
        task: (model_name, input_json_file) 元组
        output_json_dir: 评测输出目录
        target_project_path: 目标项目路径
        task_id: 任务唯一ID，用于创建独立的项目副本
    """
    model_name, input_json_file = task
    
    input_json_filename = os.path.basename(input_json_file)
    output_json = os.path.join(output_json_dir, model_name, input_json_filename)
    if os.path.exists(output_json):
        print(f"[评测] 已存在 {output_json}")
        return

    copy_dir = None
    try:
        # 为每个并行任务创建唯一的项目副本路径
        copy_dir = create_project_copy(
            target_project_path, 
            target_project_path + "_" + input_json_file.replace(".json", "")
        )
        run_evaluation_for_single_file(input_json_file, os.path.join(output_json_dir, model_name), copy_dir)
        shutil.rmtree(copy_dir)
    except Exception as e:
        print(f"[评测线程] 评测失败: {input_json_file}, 错误: {e}")
    finally:
        # 确保无论是否异常都清理副本目录
        if copy_dir and os.path.exists(copy_dir):
            try:
                shutil.rmtree(copy_dir)
            except Exception as e:
                print(f"[评测线程] 清理副本目录失败: {copy_dir}, 错误: {e}")


def evaluation_worker(output_json_dir: str, target_project_path: str, max_workers: int = 4):
    """
    评测工作线程：从队列中并行取任务执行评测
    同一时刻可以有多个评测任务在执行
    
    Args:
        output_json_dir: 评测输出目录
        target_project_path: 目标项目路径
        max_workers: 最大并行评测任务数
    """
    from concurrent.futures import as_completed
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending_futures = {}  # 使用字典跟踪正在进行的任务
        completed_count = 0
        
        while True:
            # 清理已完成的 futures，及时释放内存
            done_keys = []
            for future_key, (future, task) in list(pending_futures.items()):
                if future.done():
                    try:
                        future.result()  # 获取结果（或异常）
                        completed_count += 1
                    except Exception as e:
                        print(f"[评测线程] 任务执行异常: {task}, 错误: {e}")
                    finally:
                        # 确保释放 future 对象
                        del future
                    done_keys.append(future_key)
            
            for key in done_keys:
                del pending_futures[key]
            
            # 定期清理内存
            if len(done_keys) > 0:
                gc.collect()
            
            try:
                # 尝试从队列获取任务，超时1秒
                task = evaluation_queue.get(timeout=1)
                if task is None:  # 收到结束信号
                    break
                
                # 提交任务到线程池并行执行
                future = executor.submit(
                    single_evaluation_task, 
                    task, 
                    output_json_dir, 
                    target_project_path
                )
                pending_futures[id(future)] = (future, task)
                evaluation_queue.task_done()
                
            except queue.Empty:
                # 队列为空，检查是否所有生成任务都完成了
                if all_generation_done.is_set() and evaluation_queue.empty():
                    break
                continue
        
        # 等待剩余的任务完成
        print(f"[评测线程] 等待剩余 {len(pending_futures)} 个并行评测任务完成...")
        for future, task in pending_futures.values():
            try:
                future.result()
                completed_count += 1
            except Exception as e:
                print(f"[评测线程] 任务执行异常: {task}, 错误: {e}")
            finally:
                # 确保释放 future 对象
                del future
        # 清理 pending_futures
        pending_futures.clear()
        gc.collect()
        
        print(f"[评测线程] 共完成 {completed_count} 个评测任务")
    
    print("[评测线程] 评测工作线程结束")


def run_model_generation(model_name: str, input_file: str, output_dir: str, enable_evaluation: bool, target_codebases_path: str):
    """
    为单个模型执行10次串行生成
    每次生成完成后将评测任务放入队列
    """
    print(f"[模型 {model_name}] 开始生成任务")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for i in range(1):
        output_file = os.path.join(output_dir, f"{os.path.basename(input_file).split('.')[0]}_{model_name}_results_{i}.json")
        
        # 检查是否已存在结果文件，跳过已完成的生成
        if os.path.exists(output_file):
            print(f"[模型 {model_name}] 第 {i} 次生成已存在，跳过: {output_file}")
            if enable_evaluation:
                evaluation_queue.put((model_name, output_file))
                print(f"[模型 {model_name}] 已提交评测任务: {output_file}")
            continue
        
        with open(input_file, 'r', encoding='utf-8') as f:
            queries = json.load(f)

        # for query in queries:
        #     reference_code_content = "\n# Reference Code:\nBelow is the code from the third-party library `Hexi` could be used to implement the function:\n"
        #     for location in query["locations"]:
        #         with open(os.path.join("/data/workspace/AgenticSearch/total_third_party_projects/Hexi", location), 'r', encoding='utf-8') as f:
        #             tmp = f.read()
        #         reference_code_content += "file path: " + location + "\n" + "file content: " + f"```cpp\n{tmp}\n```" + "\n"
        #     query["instruction"] = query["instruction"] + reference_code_content
        
        # queries = queries[:1]

        print(f"[模型 {model_name}] 开始第 {i} 次生成...")
        asyncio.run(synthesize(queries, model_name, output_file, target_codebases_path))
        print(f"[模型 {model_name}] 第 {i} 次生成完成: {output_file}")
        
        # 显式释放内存
        del queries
        gc.collect()
        
        # 生成完成后，将评测任务放入队列
        if enable_evaluation:
            evaluation_queue.put((model_name, output_file))
            print(f"[模型 {model_name}] 已提交评测任务: {output_file}")

        time.sleep(10)

    print(f"[模型 {model_name}] 所有生成任务完成")


if __name__ == "__main__":
    # print("wait")
    # time.sleep(60 * 10)
    
    # 参数1: 模型名称（多个模型用逗号分隔）
    model_names_str = sys.argv[1]
    model_names = [name.strip() for name in model_names_str.split(',')]
    
    input_file = sys.argv[2]
    output_dir = sys.argv[3]
    
    target_codebases_path = sys.argv[4] 

    # 新增参数：评测输出目录和目标项目路径（可选）
    evaluation_output_dir = sys.argv[5] if len(sys.argv) > 5 else None
    target_project_path = sys.argv[6] if len(sys.argv) > 6 else None
    
    
    # 是否启用评测
    enable_evaluation = evaluation_output_dir is not None and target_project_path is not None

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    if enable_evaluation and not os.path.exists(evaluation_output_dir):
        os.makedirs(evaluation_output_dir)
    
    print(f"=== 多模型生成与评测 ===")
    print(f"模型列表: {model_names}")
    print(f"输入文件: {input_file}")
    print(f"输出目录: {output_dir}")
    print(f"评测启用: {enable_evaluation}")
    print(f"目标代码库路径: {target_codebases_path}")

    if enable_evaluation:
        print(f"评测输出目录: {evaluation_output_dir}")
        print(f"目标项目路径: {target_project_path}")
    print("=" * 40)
    
    # 启动评测工作线程（串行执行评测任务）
    evaluation_thread = None
    if enable_evaluation:
        evaluation_thread = threading.Thread(
            target=evaluation_worker,
            args=(evaluation_output_dir, target_project_path),
            daemon=True
        )
        evaluation_thread.start()
        print("[主进程] 评测工作线程已启动")
    
    # 为每个模型创建一个线程，并行执行生成任务
    model_threads = []
    for model_name in model_names:
        thread = threading.Thread(
            target=run_model_generation,
            args=(model_name, input_file, os.path.join(output_dir, model_name), enable_evaluation, target_codebases_path),
            name=f"Model-{model_name}"
        )
        model_threads.append(thread)
        thread.start()
        print(f"[主进程] 模型 {model_name} 的生成线程已启动")
    
    # 等待所有模型的生成线程完成
    for thread in model_threads:
        thread.join()
    
    print("\n[主进程] 所有模型的生成任务完成")
    
    # 标记所有生成任务完成
    all_generation_done.set()
    
    # 等待评测队列处理完毕
    if enable_evaluation:
        print("[主进程] 等待评测队列处理完毕...")
        evaluation_queue.join()  # 等待队列中所有任务完成
        
        # 发送结束信号给评测线程
        evaluation_queue.put(None)
        evaluation_thread.join()
        
        print("\n[主进程] 所有评测任务完成！")
    
    print("\n=== 所有任务完成！ ===")


# python3 run_test_private.py Qwen3-8B-api_oriented_rl_data_filtered-20251211-step65,Qwen3-8B-16GPU-2epochs-16k-highquality-filter-SFT-20251212,Qwen3-8B-32GPU-2epochs-8400iters-16k-highquality-20251210 test_case_en.json test_case_en_generate_result_20251214_without_reasoning_start_token test_case_en_evaluate_result_20251214_without_reasoning_start_token /data/workspace/Private-Model-Training/ConstructPrivateProject/search_library_usage/20251011121809/getml_sqlgen/sqlgen


# Qwen3-8B-32GPU-2epochs-4100iters-20251210,Qwen3-8B-32GPU-2epochs-4400iters-16k-highquality-20251209,Qwen3-8B-16GPU-2epochs-3100iters-4k-highquality-SFT-20251206,Qwen3-8B-16GPU-2epochs-4k-highquality-filter-SFT-200k-distill-random-20251208,Qwen3-8B-16GPU-2epochs-3100iters-20251208,Qwen3-8B-16GPU-2epochs-3100iters-16k-highquality-SFT-20251205,Qwen3-8B-16GPU-2epochs-4k-highquality-filter-SFT-200k-distill-20251207,Qwen3-8B-32GPU-2epochs-4k-highquality-filter-SFT-900k-distill-20251206,Qwen3-8B-16GPU-2epochs-4k-highquality-filter-SFT-200k-distill-20251206,Qwen3-8B-16GPU-2epochs-4k-highquality-filter-SFT-20251212,Qwen3-8B-16GPU-1epoch-4k-highquality-filter-SFT-20251212,Qwen3-8B-api_oriented_rl_data_filtered-20251212-step65,Qwen3-8B-api_oriented_rl_data_filtered-20251211-step65,Qwen3-8B-16GPU-2epochs-16k-highquality-filter-SFT-20251212,Qwen3-8B-32GPU-2epochs-8400iters-16k-highquality-20251210