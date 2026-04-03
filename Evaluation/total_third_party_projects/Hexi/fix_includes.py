#!/usr/bin/env python3
"""
将 tests_split 目录下所有 cpp 文件中的 #include "xxx.cpp" 改成 #include "xxx.hpp"
"""

import os
import re
import glob

def fix_includes_in_file(filepath):
    """修复单个文件中的 include 语句"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 匹配 #include "xxx.cpp" 并替换为 #include "xxx.hpp"
    # 模式：#include "任意文件名.cpp"
    pattern = r'#include\s+"([^"]+)\.cpp"'
    replacement = r'#include "\1.hpp"'
    
    new_content = re.sub(pattern, replacement, content)
    
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False

def main():
    # tests_split 目录路径
    tests_split_dir = os.path.dirname(os.path.abspath(__file__))
    tests_split_dir = os.path.join(tests_split_dir, 'tests_split')
    
    # 获取所有 cpp 文件
    cpp_files = glob.glob(os.path.join(tests_split_dir, '*.cpp'))
    
    modified_count = 0
    for filepath in cpp_files:
        if fix_includes_in_file(filepath):
            print(f"已修改: {os.path.basename(filepath)}")
            modified_count += 1
    
    print(f"\n总共修改了 {modified_count} 个文件")

if __name__ == '__main__':
    main()


