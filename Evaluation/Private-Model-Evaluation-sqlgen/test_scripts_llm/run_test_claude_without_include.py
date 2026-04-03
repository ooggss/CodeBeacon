import asyncio
import json
import os
import sys
from typing import List, Dict

from generation_claude import chat_completion
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

CONCURRENCY_LIMIT = 15

# 评测队列和控制变量
evaluation_queue = queue.Queue()
evaluation_done = threading.Event()  # 用于通知评测线程结束
all_generation_done = threading.Event()  # 用于标记所有生成任务是否完成
build_lock = threading.Lock()  # 用于保证同时只有一个 build.sh 在执行

def sync_generate_with_messages(model_name, messages):
    """同步版本的 generate_with_messages，用于在线程池中执行"""
    # 由于 generate_with_messages 是 async 但内部是同步调用，我们需要在新的事件循环中运行
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            chat_completion(model_name, messages)
        )
        return result
    finally:
        loop.close()


async def generation(executor, model_name, query, semaphore):
    async with semaphore:
        loop = asyncio.get_event_loop()
        
        for attempt in range(1):
            # 在线程池中执行同步的 API 调用
            result = None
            output = ""
            try:
                
                output = await loop.run_in_executor(
                    executor,
                    partial(
                        sync_generate_with_messages,
                        model_name=model_name,
                        messages=[
                            {
                                "role": "user",
                                "content": query["instruction"] 
                                # + query["ground truth context"]
                                # + query["rag_retrieval_results_text"]
                                #  + "<think>OK, I need to"
                            }
                        ],
                    )
                )
                text_content = output['text'].strip()
                
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
                    "output": output,
                    "message" : "",
                    "reasoning" : "",
                    "text" : "",
                    "result" : None
                }
            else:
                query["output"] = {
                    "messages": output['messages'],
                    "reasoning": output['reasoning'],
                    "text": output['text'],
                    "usage": output['usage'],
                    "result": result
                }

            print("success")
            return query
        
        # query["output"] = None
        # return query


async def synthesize(queries: List[Dict], model_name, output_file: str):
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    
    
    with ThreadPoolExecutor(max_workers=CONCURRENCY_LIMIT) as executor:
        tasks = [
            asyncio.create_task(generation(executor, model_name, query, semaphore))
            for query in queries
        ]
        print(f"开始并发执行 {len(tasks)} 个任务...\n")
        results = await asyncio.gather(*tasks)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

def get_test_case_content(original_test_case_content: str, namespace_name: str = ""):
    original_test_case_content = original_test_case_content.split("\n")
    for i, line in enumerate(original_test_case_content):
        if line.strip().endswith(".hpp\""):
            original_test_case_content[i] = "#include \"implementation.hpp\""
            break
    
    # if namespace_name:
    #     # 必须在所有 #include 之后插入 using namespace，
    #     # 否则 #include 被包在 namespace 块内会导致 max_align_t 等符号解析失败
    #     last_include_idx = -1
    #     for i, line in enumerate(original_test_case_content):
    #         if line.strip().startswith("#include"):
    #             last_include_idx = i
    #     if last_include_idx >= 0:
    #         original_test_case_content.insert(last_include_idx + 1, f"using namespace {namespace_name};")
    #     else:
    #         original_test_case_content.insert(0, f"using namespace {namespace_name};")
    return "\n".join(original_test_case_content)

def _extract_include_path(line: str):
    """从 #include 行中提取头文件路径，如 '#include <sqlgen/sqlite.hpp>' → 'sqlgen/sqlite.hpp'"""
    m = re.match(r'\s*#\s*include\s*[<"]([^>"]+)[>"]', line)
    return m.group(1) if m else None


def _extract_lib_identifiers(header_path: str) -> set:
    """
    从头文件路径提取可用于匹配的库标识符集合。
    例如:
      'sqlgen.hpp'          → {'sqlgen.hpp', 'sqlgen'}
      'sqlgen/sqlite.hpp'   → {'sqlgen/sqlite.hpp', 'sqlgen', 'sqlite.hpp'}
      'rfl/json.hpp'        → {'rfl/json.hpp', 'rfl', 'json.hpp'}
      'filesystem'          → {'filesystem'}
    """
    identifiers = {header_path}
    parts = header_path.split('/')
    root = re.sub(r'\.(hpp|h|hxx)$', '', parts[0])
    identifiers.add(root)
    if len(parts) > 1:
        identifiers.add(parts[-1])
    return identifiers


def filter_model_includes(model_output: str, include_lines: list) -> str:
    """
    从模型输出中移除与 include_lines 可匹配的 #include 行。

    匹配逻辑：提取每条 #include 的「库名前缀」（路径第一段去掉扩展名），
    如果模型输出中某条 #include 的库名前缀与 include_lines 中的某条相同，
    则认为它们指向同一个库，将模型输出中的这条删除（由正确的 include_lines 替代）。
    """
    known_ids = set()
    for line in include_lines:
        path = _extract_include_path(line)
        if path:
            known_ids.update(_extract_lib_identifiers(path))

    output_lines = model_output.split('\n')
    filtered = []
    for line in output_lines:
        path = _extract_include_path(line)
        if path and _extract_lib_identifiers(path) & known_ids:
            continue
        filtered.append(line)
    return '\n'.join(filtered)


def get_generate_result(generate_result: str):
    generate_result = generate_result.split("\n")
    for i, line in enumerate(generate_result):
        stripped = line.strip()
        if stripped.startswith("namespace") and stripped.endswith("{"):
            ns_part = stripped[len("namespace"):stripped.rfind("{")].strip()
            # 不重命名嵌套命名空间（如 hexi::pmc），否则 namespace 内引用的库类型会找不到
            # 只重命名简单的用户自定义命名空间（如 namespace solution {）
            if "::" not in ns_part:
                generate_result[i] = "namespace test_namespace {"
            break
    return "\n".join(generate_result)

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
        if not result:
            continue
        if not result["output"]["result"]:
            continue
        target_function_path = os.path.join(target_project_path, "include", f"implementation.hpp")
        test_case_path = os.path.join(target_project_path, "tests", f"tests.cpp")
        # target_function_path = result["file path"]
        original_generate_result = result["output"]["result"]
        include_lines = result["include_lines"]
        filtered_generate_result = filter_model_includes(original_generate_result, include_lines)
        generate_result = "\n".join(include_lines) + "\n" + filtered_generate_result
        # generate_result = result["response"]
        # generate_result = result["ground truth"]
        # generate_result = get_generate_result(original_generate_result)

        test_case_content = result["test cases"]
        # with open(result["test case path"], 'r', encoding='utf-8') as f:
        #     test_case_content = f.read()
        namespace_name = "" if generate_result == original_generate_result else "test_namespace"
        test_case_content = get_test_case_content(test_case_content, namespace_name)
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
            # 写入结果
            with open(target_function_path, 'w', encoding='utf-8') as f:
                f.write(generate_result)
            
            with open(test_case_path, 'w', encoding='utf-8') as f:
                f.write(test_case_content)
            
            # 编译
            compile_proc = None
            max_compile_retries = 10  # 最大重试次数
            compile_retry_count = 0
            while compile_retry_count < max_compile_retries:
                try:
                    compile_proc = subprocess.run(
                        ["cmake", "--build", "build", "-j", "4"],
                        cwd=target_project_path,
                        capture_output=True,
                        timeout=300  # 5分钟超时
                    )
                    compile_output = compile_proc.stdout.decode('utf-8', errors='replace')
                    compile_error = compile_proc.stderr.decode('utf-8', errors='replace')

                    if "Killed signal terminated program cc1plus" in compile_error:
                        compile_retry_count += 1
                        print(f"[评测] 编译被杀死，重试 {compile_retry_count}/{max_compile_retries}")
                        continue
                    else:
                        break
                except subprocess.TimeoutExpired:
                    compile_retry_count += 1
                    print(f"[评测] 编译超时，重试 {compile_retry_count}/{max_compile_retries}")
                    compile_output = ""
                    compile_error = "编译超时"
                    continue
            
            if compile_retry_count >= max_compile_retries:
                compile_output = ""
                compile_error = "编译重试次数超限"
            
            result["evaluation_message"]["compile_result"] = {
                "output": compile_output,
                "error": compile_error
            }
            
            if compile_error:
                result["evaluation_result"]["compile_result"] = False
                result["evaluation_result"]["test_result"] = False
                continue
            else:
                result["evaluation_result"]["compile_result"] = True
            
                        
            # 立即释放原始输出内存和进程对象
            del compile_output, compile_error, compile_proc

            compile_success_cnt += 1
            
            # 测试
            try:
                test_proc = subprocess.run(
                    ["ctest"],
                    cwd=os.path.join(target_project_path, "build"),
                    capture_output=True,
                    timeout=120  # 2分钟超时
                )
                test_output = test_proc.stdout.decode('utf-8', errors='replace')
                test_error = test_proc.stderr.decode('utf-8', errors='replace')
            except subprocess.TimeoutExpired:
                test_output = ""
                test_error = "测试超时"
            
            result["evaluation_message"]["test_result"] = {
                "output": test_output,
                "error": test_error
            }
            
            if test_error:
                result["evaluation_result"]["test_result"] = False
            else:
                result["evaluation_result"]["test_result"] = True
                test_success_cnt += 1
        
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
        max_build_retries = 10  # 最大重试次数
        build_retry_count = 0
        while build_retry_count < max_build_retries:
            try:
                compile_proc = subprocess.run(
                    ["./build.sh"],
                    cwd=copy_dir,
                    capture_output=True,
                    timeout=600  # 10分钟超时
                )
                if compile_proc.returncode == 0:
                    print(f"[构建] {copy_dir} 构建成功, 耗时 {time.time() - start_time:.2f} 秒")
                    break
                else:
                    build_retry_count += 1
                    print(f"[构建] {copy_dir} 构建失败 (返回码: {compile_proc.returncode})，重试 {build_retry_count}/{max_build_retries}...")
                    time.sleep(0.1)
            except subprocess.TimeoutExpired:
                build_retry_count += 1
                print(f"[构建] {copy_dir} 构建超时，重试 {build_retry_count}/{max_build_retries}...")
        
        if build_retry_count >= max_build_retries:
            raise RuntimeError(f"[构建] {copy_dir} 构建失败，已达最大重试次数 {max_build_retries}")

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


def evaluation_worker(output_json_dir: str, target_project_path: str, max_workers: int = 5):
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
                    done_keys.append(future_key)
            
            for key in done_keys:
                del pending_futures[key]
            
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
        
        print(f"[评测线程] 共完成 {completed_count} 个评测任务")
    
    print("[评测线程] 评测工作线程结束")


def run_model_generation(model_name: str, input_file: str, output_dir: str, enable_evaluation: bool):
    """
    为单个模型执行10次串行生成
    每次生成完成后将评测任务放入队列
    """
    print(f"[模型 {model_name}] 开始生成任务")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for i in range(3):
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
        # queries = queries[:1]
        
        for query in queries:
            reference_code_content = "\n# Reference Code:\nBelow is the code from the third-party library `sqlgen` could be used to implement the function:\n"
            for location in query["locations"]:
                # location = location.split(":")[0].split(" line")[0].strip(" ,.")
                location = location.split(":")[0]
                try:
                    with open(location, 'r', encoding='utf-8') as f:
                        tmp = f.read()
                except:
                    try:
                        with open(os.path.join("/data/workspace/AgenticSearch/total_third_party_projects/sqlgen", location), 'r', encoding='utf-8') as f:
                            tmp = f.read()
                    except:
                        try:
                            with open(os.path.join("/data/workspace/AgenticSearch/total_third_party_projects", location), 'r', encoding='utf-8') as f:
                                tmp = f.read()
                        except:
                            print(f"error: {location}")
                            continue
                reference_code_content += "file path: " + location + "\n" + "file content: " + f"```cpp\n{tmp}\n```" + "\n"
            query["instruction"] = query["instruction"] + reference_code_content
        
        # queries = queries[:1]

        print(f"[模型 {model_name}] 开始第 {i} 次生成...")
        asyncio.run(synthesize(queries, model_name, output_file))
        print(f"[模型 {model_name}] 第 {i} 次生成完成: {output_file}")
        
        # 显式释放内存
        del queries
        gc.collect()
        
        # 生成完成后，将评测任务放入队列
        if enable_evaluation:
            evaluation_queue.put((model_name, output_file))
            print(f"[模型 {model_name}] 已提交评测任务: {output_file}")
    
    print(f"[模型 {model_name}] 所有生成任务完成")


if __name__ == "__main__":
    # print("wait")
    # time.sleep(60 * 5)
    
    # 参数1: 模型名称（多个模型用逗号分隔）
    model_names_str = sys.argv[1]
    model_names = [name.strip() for name in model_names_str.split(',')]
    
    input_file = sys.argv[2]
    output_dir = sys.argv[3]
    
    # 新增参数：评测输出目录和目标项目路径（可选）
    evaluation_output_dir = sys.argv[4] if len(sys.argv) > 4 else None
    target_project_path = sys.argv[5] if len(sys.argv) > 5 else None
    
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
            args=(model_name, input_file, os.path.join(output_dir, model_name), enable_evaluation),
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