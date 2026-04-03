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
import requests
import uuid
from datetime import datetime
import openai
from jinja2 import Template
import copy
from multiprocessing import Pool, cpu_count
from generation import generate, generate_with_messages
# import tqdm


with open(sys.argv[4], 'r', encoding='utf-8') as file:
    project_info = json.load(file)

with open(sys.argv[5], 'r', encoding='utf-8') as template_file:
    template_content = template_file.read()
template = Template(template_content)

project_name = sys.argv[3]

# 生成唯一的UUID用于标识本次运行
CURR_UUID = str(uuid.uuid4())



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
    prompt = task["prompt"]
    entity_info = task["entity_info"]
    
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
        tags=["<detail functional description of code element>", "</detail functional description of code element>", "<summary>", "</summary>"],
        # tags=["<detail functional description of api combination>", "</detail functional description of api combination>", "<summary>", "</summary>"],
        args={"thinking": {"type": "enabled"}}
        )
    result["project_name"] = project_name
    result["entity_info"] = entity_info
    
    
    return result

def generate_question_prompt(entity_info):
    """
    生成单个问题的提示词
    
    基于给定的参数生成完整的问题提示词，包括实体信息、问题内容、类型、难度等。
    使用Jinja2模板系统生成结构化的提示词。
    
    Args:
        entity_name (str): 实体名称
        question_content (str): 问题内容描述
        question_type (str): 问题类型（问答题/填空题/编程题）
        difficulty (str): 难度等级
        source_code_dict (dict): 源代码文件字典
        outline_prompt_file (str): 大纲模板文件路径
        
    Returns:
        dict: 包含prompt和source_code_dict的任务字典
    """
    
    prompt = template.render(
        entity_info=entity_info,
    )

    return {
        "prompt": prompt.replace("\t", " "),
        "entity_info": entity_info
    }


def get_global_variable_location(global_variable_info):
    file_path = global_variable_info["file"]
    try:
        file_info = project_info[file_path]
    except:
        print(f"file {file_path} not found")
        return ""

    for namespace_info in file_info["namespaces"]:
        for field_info in namespace_info["fields"]:
            if field_info["field_name"] != global_variable_info["name"] and global_variable_info["definition"] != field_info["field_declaration"].strip():
                continue
            return f"global variable `{field_info['field_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`"

    
    for field_info in file_info["fields"]:
        if field_info["field_name"] != global_variable_info["name"] and global_variable_info["definition"] != field_info["field_declaration"].strip():
            continue
        return f"global variable `{field_info['field_name']}` in the file `{file_path}`"
        



def get_custom_type_location(custom_type_info):
    file_path = custom_type_info.get("file", custom_type_info.get("custom_type_definition_file_path", ""))
    try:
        file_info = project_info[file_path]
    except:
        print(f"file {file_path} not found")
        return ""

    definition = custom_type_info.get("definition", custom_type_info.get("custom_type_definition", "")).strip()
    name = custom_type_info.get("name", custom_type_info.get("custom_type_name", ""))
    for namespace_info in file_info["namespaces"]:
        for class_info in namespace_info["classes"]:
            if class_info["class_name"] != name and definition != class_info["class_definition"].strip():
                continue
            return f"class `{class_info['class_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`"

        for class_info in namespace_info["structs"]:
            if class_info["struct_name"] != name and definition != class_info["struct_definition"].strip():
                continue
            return f"struct `{class_info['struct_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`"
                
        for define_info in namespace_info["#defines"]:
            if define_info["define_name"] != name and definition != define_info["define_definition"].strip():
                continue
            return f"define `{define_info['define_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`"

    
    for class_info in file_info["classes"]:
        if class_info["class_name"] != name and definition != class_info["class_definition"].strip():
            continue
        return f"class `{class_info['class_name']}` in the file `{file_path}`"

    for class_info in file_info["structs"]:
        if class_info["struct_name"] != name and definition != class_info["struct_definition"].strip():
            continue
        return f"struct `{class_info['struct_name']}` in the file `{file_path}`"
            
    for define_info in file_info["#defines"]:
        if define_info["define_name"] != name and definition != define_info["define_definition"].strip():
            continue
        return f"define `{define_info['define_name']}` in the file `{file_path}`"
    
    print(f"custom type {name} in file {file_path} not found")
    return ""


def get_target_function_dependencies(target_function_info):
    try:
        file_info = project_info[target_function_info["file"]]
    except:
        print(f"file {target_function_info['file']} not found")
        if "test" not in target_function_info["file"]:
            print(target_function_info["definition"])
        return ""

    for namespace_info in file_info["namespaces"]:
        for class_info in namespace_info["classes"]:
            for function_info in class_info["functions"]:
                # if function_info["function_name"] != target_function_info["name"]:
                #     continue
                function_definition = function_info["function_definition"]
                if "function_definition_location" in function_info and "code" in function_info["function_definition_location"]:
                    function_definition = function_info["function_definition_location"]["code"]
                if function_definition.strip() != target_function_info["definition"].strip() and target_function_info["definition"].strip() not in function_definition.strip():
                    continue

                return f"function `{function_info['function_name']}` is located in namespace `{namespace_info['namespace_name']}` within the file `{target_function_info['file']}`"

        for class_info in namespace_info["structs"]:
            for function_info in class_info["functions"]:
                # if function_info["function_name"] != target_function_info["name"]:
                #     continue
                function_definition = function_info["function_definition"]
                if "function_definition_location" in function_info and "code" in function_info["function_definition_location"]:
                    function_definition = function_info["function_definition_location"]["code"]
                if function_definition.strip() != target_function_info["definition"].strip() and target_function_info["definition"].strip() not in function_definition.strip():
                    continue

                return f"function `{function_info['function_name']}` is located in namespace `{namespace_info['namespace_name']}` within the file `{target_function_info['file']}`"
                
                
        for function_info in namespace_info["functions"]:
            # if function_info["function_name"] != target_function_info["name"]:
            #     continue
            function_definition = function_info["function_definition"]
            if "function_definition_location" in function_info and "code" in function_info["function_definition_location"]:
                function_definition = function_info["function_definition_location"]["code"]
            if function_definition.strip() != target_function_info["definition"].strip() and target_function_info["definition"].strip() not in function_definition.strip():
                continue

            return f"function `{function_info['function_name']}` is located in namespace `{namespace_info['namespace_name']}` within the file `{target_function_info['file']}`"

    
    for class_info in file_info["classes"]:
        for function_info in class_info["functions"]:
            # if function_info["function_name"] != target_function_info["name"]:
            #     continue
            function_definition = function_info["function_definition"]
            if "function_definition_location" in function_info and "code" in function_info["function_definition_location"]:
                function_definition = function_info["function_definition_location"]["code"]
            if function_definition.strip() != target_function_info["definition"].strip() and target_function_info["definition"].strip() not in function_definition.strip():
                continue
            
            return f"function `{function_info['function_name']}` is located in the file `{target_function_info['file']}`"

    for class_info in file_info["structs"]:
        for function_info in class_info["functions"]:
            # if function_info["function_name"] != target_function_info["name"]:
            #     continue
            function_definition = function_info["function_definition"]
            if "function_definition_location" in function_info and "code" in function_info["function_definition_location"]:
                function_definition = function_info["function_definition_location"]["code"]
            if function_definition.strip() != target_function_info["definition"].strip() and target_function_info["definition"].strip() not in function_definition.strip():
                continue

            return f"function `{function_info['function_name']}` is located in the file `{target_function_info['file']}`"

    for function_info in file_info["functions"]:
        # if function_info["function_name"] != target_function_info["name"]:
        #     continue
        function_definition = function_info["function_definition"]
        if "function_definition_location" in function_info and "code" in function_info["function_definition_location"]:
            function_definition = function_info["function_definition_location"]["code"]
        if function_definition.strip() != target_function_info["definition"].strip() and target_function_info["definition"].strip() not in function_definition.strip():
            continue
        
        return f"function `{function_info['function_name']}` is located in the file `{target_function_info['file']}`"

    print(f"function {target_function_info['name']} in file {target_function_info['file']} not found")
    if "test" not in target_function_info["file"]:
        print(target_function_info["definition"])
    return ""


def get_member_functions(file_path, custom_type_info):
    if "custom_type_definition" in custom_type_info:
        return []
    try:
        file_info = project_info[file_path]
    except:
        print(f"file {file_path} not found")
        return []
    
    for namespace_info in file_info["namespaces"]:
        for class_info in namespace_info["classes"]:
            # print(json.dumps(custom_type_info, indent=4))
            if class_info["class_definition"].strip() == custom_type_info["definition"].strip():
                return [
                    {k: v for k, v in func.items() if k not in ("call_functions", "call_custom_types")}
                    for func in class_info["functions"]
                ]
        for class_info in namespace_info["structs"]:
            if class_info["struct_definition"].strip() == custom_type_info["definition"].strip():
                return [
                    {k: v for k, v in func.items() if k not in ("call_functions", "call_custom_types")}
                    for func in class_info["functions"]
                ]

    for class_info in file_info["classes"]:
        if class_info["class_definition"].strip() == custom_type_info["definition"].strip():
            return [
                {k: v for k, v in func.items() if k not in ("call_functions", "call_custom_types")}
                for func in class_info["functions"]
            ]
    for class_info in file_info["structs"]:
        if class_info["struct_definition"].strip() == custom_type_info["definition"].strip():
            return [
                {k: v for k, v in func.items() if k not in ("call_functions", "call_custom_types")}
                for func in class_info["functions"]
            ]

    return []

def get_entity_list(file_info, file_path, file_content):
    field_dependencies = [{
        "dependency_name": file_path.split("/")[-1],
        "dependency_code": file_content,
        "dependency_location": f"file `{file_path}`"
    }]

    entity_list = []
    for namespace_info in file_info["namespaces"]:
        for field_info in namespace_info["fields"]:
            entity_list.append({
                # "entity_name": f"field `{field_info['field_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{namespace_info['file_path']}`",
                "entity_name": field_info['field_name'],
                "entity_location": f"field `{field_info['field_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`",
                "entity_code": field_info['field_declaration'],
                "dependencies": field_dependencies,
                "original_info": field_info,
            })

        for class_info in namespace_info["classes"]:
            class_custom_type_dependencies = []
            for function_info in class_info["functions"]:
                dependencies = []

                for called_function_info in function_info["call_functions"]:
                    if called_function_info["type"] == "exact":
                        called_function_location = get_target_function_dependencies(called_function_info)
                        if "test" in called_function_info["file"] and called_function_location == "":
                            called_function_location = f"`{called_function_info['name']}` in the file `{called_function_info['file']}`"
                        dependencies.append({
                            "dependency_name": called_function_info["name"],
                            "dependency_code": called_function_info["definition"],
                            "dependency_location": called_function_location,
                        })
                    else:
                        for candidate in called_function_info["candidates"]:
                            called_function_location = get_target_function_dependencies(candidate)
                            if "test" in candidate["file"] and called_function_location == "":
                                called_function_location = f"`{candidate['name']}` in the file `{candidate['file']}`"
                            dependencies.append({
                                "dependency_name": candidate["name"],
                                "dependency_code": candidate["definition"],
                                "dependency_location": called_function_location,
                            })
                
                
                for called_custom_type_info in function_info["call_custom_types"]:
                    name = called_custom_type_info.get("name", called_custom_type_info.get("custom_type_name", ""))
                    if name:
                        dep_file_path = called_custom_type_info.get("file", called_custom_type_info.get("custom_type_definition_file_path", ""))
                        tmp_dependencies = {
                            "dependency_name": name,
                            "dependency_code": called_custom_type_info.get("definition", called_custom_type_info.get("custom_type_definition", "")),
                            "dependency_location": get_custom_type_location(called_custom_type_info) if "test" not in dep_file_path else f"`{name}` in the file `{dep_file_path}`",
                        }
                        dependencies.append(tmp_dependencies)
                        class_custom_type_dependencies.append(tmp_dependencies)
                    
                for called_global_variable_info in function_info["call_global_variables"]:
                    dependencies.append({
                        "dependency_name": called_global_variable_info["name"],
                        "dependency_code": called_global_variable_info["definition"],
                        "dependency_location": get_global_variable_location(called_global_variable_info),
                    })
                
                # print(json.dumps(function_info, indent=4))
                entity_list.append({
                    # "entity_name": f"function `{function_info['function_complete_header']}` in class `{class_info['class_name']}` in namespace `{namespace_info['namespace_name']}`",
                    "entity_name": function_info['function_name'],
                    "entity_location": f"function `{function_info['function_complete_header']}` in class `{class_info['class_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`",
                    "entity_code": function_info['function_definition'],
                    "dependencies": dependencies,
                    "original_info": function_info,
                })
            
            entity_list.append({
                # "entity_name": f"class `{class_info['class_name']}` in namespace `{namespace_info['namespace_name']}`",
                "entity_name": class_info['class_name'],
                "entity_location": f"class `{class_info['class_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`",
                "entity_code": class_info['class_definition'],
                "dependencies": class_custom_type_dependencies,
                "original_info": class_info,
            })

        for struct_info in namespace_info["structs"]:
            struct_custom_type_dependencies = []
            for function_info in struct_info["functions"]:
                dependencies = []

                for called_function_info in function_info["call_functions"]:
                    if called_function_info["type"] == "exact":
                        called_function_location = get_target_function_dependencies(called_function_info)
                        if "test" in called_function_info["file"] and called_function_location == "":
                            called_function_location = f"`{called_function_info['name']}` in the file `{called_function_info['file']}`"
                        dependencies.append({
                            "dependency_name": called_function_info["name"],
                            "dependency_code": called_function_info["definition"],
                            "dependency_location": called_function_location,
                        })
                    else:
                        for candidate in called_function_info["candidates"]:
                            called_function_location = get_target_function_dependencies(candidate)
                            if "test" in candidate["file"] and called_function_location == "":
                                called_function_location = f"`{candidate['name']}` in the file `{candidate['file']}`"
                            dependencies.append({
                                "dependency_name": candidate["name"],
                                "dependency_code": candidate["definition"],
                                "dependency_location": called_function_location,
                            })
                
                
                for called_custom_type_info in function_info["call_custom_types"]:
                    name = called_custom_type_info.get("name", called_custom_type_info.get("custom_type_name", ""))
                    if name:
                        dep_file_path = called_custom_type_info.get("file", called_custom_type_info.get("custom_type_definition_file_path", ""))
                        tmp_dependencies = {
                            "dependency_name": name,
                            "dependency_code": called_custom_type_info.get("definition", called_custom_type_info.get("custom_type_definition", "")),
                            "dependency_location": get_custom_type_location(called_custom_type_info) if "test" not in dep_file_path else f"`{name}` in the file `{dep_file_path}`",
                        }
                        dependencies.append(tmp_dependencies)
                        struct_custom_type_dependencies.append(tmp_dependencies)
                    
                for called_global_variable_info in function_info["call_global_variables"]:
                    dependencies.append({
                        "dependency_name": called_global_variable_info["name"],
                        "dependency_code": called_global_variable_info["definition"],
                        "dependency_location": get_global_variable_location(called_global_variable_info),
                    })
                
                entity_list.append({
                    # "entity_name": f"function `{function_info['function_complete_header']}` in struct `{struct_info['struct_name']}` in namespace `{namespace_info['namespace_name']}`",
                    "entity_name": function_info['function_name'],
                    "entity_location": f"function `{function_info['function_complete_header']}` in struct `{struct_info['struct_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`",
                    "entity_code": function_info['function_definition'],
                    "dependencies": dependencies,
                    "original_info": function_info,
                })
            
            entity_list.append({
                # "entity_name": f"struct `{struct_info['struct_name']}` in namespace `{namespace_info['namespace_name']}`",
                "entity_name": struct_info['struct_name'],
                "entity_location": f"struct `{struct_info['struct_name']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`",
                "entity_code": struct_info['struct_definition'],
                "dependencies": struct_custom_type_dependencies,
                "original_info": struct_info,
            })

        for function_info in namespace_info["functions"]:
            dependencies = []

            for called_function_info in function_info["call_functions"]:
                if called_function_info["type"] == "exact":
                    called_function_location = get_target_function_dependencies(called_function_info)
                    if "test" in called_function_info["file"] and called_function_location == "":
                        called_function_location = f"`{called_function_info['name']}` in the file `{called_function_info['file']}`"
                    dependencies.append({
                        "dependency_name": called_function_info["name"],
                        "dependency_code": called_function_info["definition"],
                        "dependency_location": called_function_location,
                    })
                else:
                    for candidate in called_function_info["candidates"]:
                        called_function_location = get_target_function_dependencies(candidate)
                        if "test" in candidate["file"] and called_function_location == "":
                            called_function_location = f"`{candidate['name']}` in the file `{candidate['file']}`"
                        dependencies.append({
                            "dependency_name": candidate["name"],
                            "dependency_code": candidate["definition"],
                            "dependency_location": called_function_location,
                        })
            
            for called_custom_type_info in function_info["call_custom_types"]:
                name = called_custom_type_info.get("name", called_custom_type_info.get("custom_type_name", ""))
                if name:
                    dep_file_path = called_custom_type_info.get("file", called_custom_type_info.get("custom_type_definition_file_path", ""))
                    dependencies.append({
                        "dependency_name": name,
                        "dependency_code": called_custom_type_info.get("definition", called_custom_type_info.get("custom_type_definition", "")),
                        "dependency_location": get_custom_type_location(called_custom_type_info) if "test" not in dep_file_path else f"`{name}` in the file `{dep_file_path}`",
                    })

            entity_list.append({
                # "entity_name": f"function `{function_info['function_complete_header']}` in namespace `{namespace_info['namespace_name']}`",
                "entity_name": function_info['function_name'],
                "entity_location": f"function `{function_info['function_complete_header']}` in namespace `{namespace_info['namespace_name']}` within the file `{file_path}`",
                "entity_code": function_info['function_definition'],
                "dependencies": dependencies,
                "original_info": function_info,
            })  
    
    for field_info in file_info["fields"]:
        entity_list.append({
            # "entity_name": f"field `{field_info['field_name']}` within the file `{file_path}`",
            "entity_name": field_info['field_name'],
            "entity_location": f"field `{field_info['field_name']}` within the file `{file_path}`",
            "entity_code": field_info['field_declaration'],
            "dependencies": field_dependencies,
            "original_info": field_info,
        })

    for class_info in file_info["classes"]:
        class_custom_type_dependencies = []
        for function_info in class_info["functions"]:
            dependencies = []
            for called_function_info in function_info["call_functions"]:
                if called_function_info["type"] == "exact":
                    called_function_location = get_target_function_dependencies(called_function_info)
                    if "test" in called_function_info["file"] and called_function_location == "":
                        called_function_location = f"`{called_function_info['name']}` in the file `{called_function_info['file']}`"
                    dependencies.append({
                        "dependency_name": called_function_info["name"],
                        "dependency_code": called_function_info["definition"],
                        "dependency_location": called_function_location,
                    })
                else:
                    for candidate in called_function_info["candidates"]:
                        called_function_location = get_target_function_dependencies(candidate)
                        if "test" in candidate["file"] and called_function_location == "":
                            called_function_location = f"`{candidate['name']}` in the file `{candidate['file']}`"
                        dependencies.append({
                            "dependency_name": candidate["name"],
                            "dependency_code": candidate["definition"],
                            "dependency_location": called_function_location,
                        })
            
            for called_custom_type_info in function_info["call_custom_types"]:
                name = called_custom_type_info.get("name", called_custom_type_info.get("custom_type_name", ""))
                if name:
                    dep_file_path = called_custom_type_info.get("file", called_custom_type_info.get("custom_type_definition_file_path", ""))
                    tmp_dependencies = {
                        "dependency_name": name,
                        "dependency_code": called_custom_type_info.get("definition", called_custom_type_info.get("custom_type_definition", "")),
                        "dependency_location": get_custom_type_location(called_custom_type_info) if "test" not in dep_file_path else f"`{name}` in the file `{dep_file_path}`",
                    }
                    dependencies.append(tmp_dependencies)
                    class_custom_type_dependencies.append(tmp_dependencies)
            entity_list.append({
                # "entity_name": f"function `{function_info['function_complete_header']}` in class `{class_info['class_name']}`",
                "entity_name": function_info['function_name'],
                "entity_location": f"function `{function_info['function_complete_header']}` in class `{class_info['class_name']}` within the file `{file_path}`",
                "entity_code": function_info['function_definition'],
                "dependencies": dependencies,
                "original_info": function_info,
            })
        entity_list.append({
            # "entity_name": f"class `{class_info['class_name']}`",
            "entity_name": class_info['class_name'],
            "entity_location": f"class `{class_info['class_name']}` within the file `{file_path}`",
            "entity_code": class_info['class_definition'],
            "dependencies": class_custom_type_dependencies,
            "original_info": class_info,
        })

    for struct_info in file_info["structs"]:
        struct_custom_type_dependencies = []
        for function_info in struct_info["functions"]:
            dependencies = []
            for called_function_info in function_info["call_functions"]:
                if called_function_info["type"] == "exact":
                    called_function_location = get_target_function_dependencies(called_function_info)
                    if "test" in called_function_info["file"] and called_function_location == "":
                        called_function_location = f"`{called_function_info['name']}` in the file `{called_function_info['file']}`"
                    dependencies.append({
                        "dependency_name": called_function_info["name"],
                        "dependency_code": called_function_info["definition"],
                        "dependency_location": called_function_location,
                    })
                else:
                    for candidate in called_function_info["candidates"]:
                        called_function_location = get_target_function_dependencies(candidate)
                        if "test" in candidate["file"] and called_function_location == "":
                            called_function_location = f"`{candidate['name']}` in the file `{candidate['file']}`"
                        dependencies.append({
                            "dependency_name": candidate["name"],
                            "dependency_code": candidate["definition"],
                            "dependency_location": called_function_location,
                        })
            
            for called_custom_type_info in function_info["call_custom_types"]:
                name = called_custom_type_info.get("name", called_custom_type_info.get("custom_type_name", ""))
                if name:
                    dep_file_path = called_custom_type_info.get("file", called_custom_type_info.get("custom_type_definition_file_path", ""))
                    tmp_dependencies = {
                        "dependency_name": name,
                        "dependency_code": called_custom_type_info.get("definition", called_custom_type_info.get("custom_type_definition", "")),
                        "dependency_location": get_custom_type_location(called_custom_type_info) if "test" not in dep_file_path else f"`{name}` in the file `{dep_file_path}`",
                    }
                    dependencies.append(tmp_dependencies)
                    struct_custom_type_dependencies.append(tmp_dependencies)
            entity_list.append({
                # "entity_name": f"function `{function_info['function_complete_header']}` in struct `{struct_info['struct_name']}`",
                "entity_name": function_info['function_name'],
                "entity_location": f"function `{function_info['function_complete_header']}` in struct `{struct_info['struct_name']}` within the file `{file_path}`",
                "entity_code": function_info['function_definition'],
                "dependencies": dependencies,
                "original_info": function_info,
            })
        entity_list.append({
            # "entity_name": f"struct `{struct_info['struct_name']}`",
            "entity_name": struct_info['struct_name'],
            "entity_location": f"struct `{struct_info['struct_name']}` within the file `{file_path}`",
            "entity_code": struct_info['struct_definition'],
            "dependencies": struct_custom_type_dependencies,
            "original_info": struct_info,
        })

    for function_info in file_info["functions"]:
        dependencies = []
        for called_function_info in function_info["call_functions"]:
            if called_function_info["type"] == "exact":
                called_function_location = get_target_function_dependencies(called_function_info)
                if "test" in called_function_info["file"] and called_function_location == "":
                    called_function_location = f"`{called_function_info['name']}` in the file `{called_function_info['file']}`"
                dependencies.append({
                    "dependency_name": called_function_info["name"],
                    "dependency_code": called_function_info["definition"],
                    "dependency_location": called_function_location,
                })
            else:
                for candidate in called_function_info["candidates"]:
                    called_function_location = get_target_function_dependencies(candidate)
                    if "test" in candidate["file"] and called_function_location == "":
                        called_function_location = f"`{candidate['name']}` in the file `{candidate['file']}`"
                    dependencies.append({
                        "dependency_name": candidate["name"],
                        "dependency_code": candidate["definition"],
                        "dependency_location": called_function_location,
                    })
        
        for called_custom_type_info in function_info["call_custom_types"]:
            name = called_custom_type_info.get("name", called_custom_type_info.get("custom_type_name", ""))
            if name:
                dep_file_path = called_custom_type_info.get("file", called_custom_type_info.get("custom_type_definition_file_path", ""))
                dependencies.append({
                    "dependency_name": name,
                    "dependency_code": called_custom_type_info.get("definition", called_custom_type_info.get("custom_type_definition", "")),
                    "dependency_location": get_custom_type_location(called_custom_type_info) if "test" not in dep_file_path else f"`{name}` in the file `{dep_file_path}`",
                })

        entity_list.append({
            # "entity_name": f"function `{function_info['function_complete_header']}`",
            "entity_name": function_info['function_name'],
            "entity_location": f"function `{function_info['function_complete_header']}` within the file `{file_path}`",
            "entity_code": function_info['function_definition'],
            "dependencies": dependencies,
            "original_info": function_info,
        })
    
    return entity_list


def process_usage_data(data_usage):
    """
    处理用法数据，生成所有相关的问题提示词
    
    遍历给定的用法数据，为每个文件、实体、难度、问题类型的组合生成对应的提示词。
    这是数据生成的核心逻辑，会产生大量的提示词用于后续的批量处理。
    
    Args:
        data_usage (dict): 用法数据字典，包含文件名和实体信息
        dir_name (str): 源代码目录路径
        questions (dict): 问题类型配置字典
        outline_prompt_file (str): 大纲模板文件路径
        
    Returns:
        list: 生成的所有问题任务字典列表
    """
    tasks = []
    
    for file_path, file_info in data_usage.items():
        if "test/" in file_path or "tests/" in file_path:
        # if not "test/" in file_path and not "tests/" in file_path:
            continue
        with open(os.path.join("total_third_party_projects", project_name, file_path.lstrip('/')), 'r') as f:
            file_content = f.read()
        entity_list = get_entity_list(file_info, file_path, file_content)

        # 遍历所有难度级别
        # for difficulty in QUESTION_DIFFICULTY_EN:
        # 遍历文件中的所有实体类型和名称
        for entity_info in entity_list:
            # if entity_info["entity_name"] == "connect":
            #     print(json.dumps(entity_info, ensure_ascii=False, indent=2))
            #     print("--------------------------------")
            # 生成问题任务字典
            task = generate_question_prompt(
                entity_info,
            )
            tasks.append(task)
    
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
    output_directory = sys.argv[1]  # 输出目录
    model_name = sys.argv[2]        # 模型名称
    repeat_times = int(sys.argv[6]) if len(sys.argv) > 6 else 1
    num_processes = int(sys.argv[7]) if len(sys.argv) > 7 else cpu_count()  # 进程数，默认为CPU核心数

    # 配置日志系统
    # 文件处理器 - 记录所有信息到日志文件
    file_handler = logging.FileHandler(f'functional_description_generation-{model_name}-{datetime.now().strftime("%Y%m%d%H%M%S")}.log', mode='a')
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

    # 确保输出目录存在
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)
        
    # 加载源数据文件
    # 新版XWI用法数据 

    
    # 处理用法数据，生成所有问题提示词
    tasks = process_usage_data(project_info)

    old_tasks_path = f"{output_directory.replace('_fix', '')}/tasks.json"
    if os.path.exists(old_tasks_path):
        with open(old_tasks_path, 'r', encoding='utf-8') as f:
            old_tasks = json.load(f)
        tasks = [new for new, old in zip(tasks, old_tasks) if new['prompt'] != old['prompt']]

    print(len(tasks))
    # with open(f'{output_directory}/tasks_2.json', 'w') as f:
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

    for i in range(repeat_times):
        print(f"Start to generate {len(tasks)} questions for group {i+1}")
        logging.info(f"Start to generate {len(tasks)} questions for group {i+1}")
        # 使用多进程池进行并行处理
        with Pool(processes=num_processes) as pool:
            # 分批处理所有任务
            for batch_start in range(0, len(tasks), batch_size):
                batch_end = min(batch_start + batch_size, len(tasks))
                batch_tasks = tasks[batch_start:batch_end]
                
                print(f"Processing batch {batch_start//batch_size + 1}/{(len(tasks) + batch_size - 1)//batch_size}: tasks {batch_start+1}-{batch_end}")
                logging.info(f"Processing batch {batch_start//batch_size + 1}/{(len(tasks) + batch_size - 1)//batch_size}: tasks {batch_start+1}-{batch_end}")
                
                # 准备当前批次的任务参数
                task_args = [(model_name, task, prompt_id) for prompt_id, task in enumerate(batch_tasks, start=batch_start)]
                
                # 并行执行当前批次的所有任务
                batch_results = pool.map(process_batch, task_args)
                # 备用的串行处理代码（调试用）
                # batch_results = []
                # for task_arg in task_args:
                #     batch_results.append(process_batch(task_arg))
                
                # 记录批次完成情况
                logging.warning(f'Completed {batch_end}/{len(tasks)} tasks')
                
                # 保存批次结果到临时文件
                temp_output_file = os.path.join(output_directory, f'functional_description_generation_{model_name}_{CURR_UUID}_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{len(batch_results)}.json')
                with open(temp_output_file, 'w', encoding='utf-8') as f:
                    json.dump(batch_results, f, ensure_ascii=False, indent=2)
                logging.warning(f"Saved {len(batch_results)} questions to temporary file: {temp_output_file}")
                
                logging.warning(f"Completed batch {batch_start//batch_size + 1}, total completed: {batch_end}/{len(tasks)}")
                print(f"Completed batch {batch_start//batch_size + 1}, total completed: {batch_end}/{len(tasks)}")
    
if __name__ == "__main__":
    # 记录程序开始时间
    start = time.time()
    
    # 运行主函数
    main()
    
    # 输出总耗时
    print(f"Total time cost: {(time.time() - start)/60:.2f} minutes")
