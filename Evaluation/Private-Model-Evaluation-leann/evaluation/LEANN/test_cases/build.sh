#!/bin/bash

# LEANN 测试用例构建脚本

set -e  # 遇到错误立即退出

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}LEANN 测试用例构建脚本${NC}"
echo -e "${GREEN}========================================${NC}"

# 获取项目根目录
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "\n${YELLOW}[步骤 1/4]${NC} 检查Python环境..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到 python3${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}✓${NC} Python版本: $PYTHON_VERSION"

echo -e "\n${YELLOW}[步骤 2/4]${NC} 检查依赖..."
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    # 检查并修复 NumPy 版本兼容性问题（PyTorch 和 FAISS 需要 NumPy 1.x）
    echo "检查 NumPy 版本兼容性..."
    if python3 -c "import numpy" 2>/dev/null; then
        NUMPY_VERSION=$(python3 -c "import numpy; print(numpy.__version__)" 2>/dev/null)
        echo "当前 NumPy 版本: $NUMPY_VERSION"
        
        # 检查是否是 NumPy 2.x（会导致 "Numpy is not available" 错误）
        MAJOR_VERSION=$(python3 -c "import numpy; print(numpy.__version__.split('.')[0])" 2>/dev/null)
        if [ "$MAJOR_VERSION" = "2" ]; then
            echo -e "${YELLOW}检测到 NumPy 2.x 版本，PyTorch 和 FAISS 需要 NumPy 1.x${NC}"
            echo "正在降级 NumPy 到 1.x 版本（这可能需要几分钟）..."
            python3 -m pip install --upgrade "numpy>=1.20.0,<2.0.0" --force-reinstall --no-cache-dir || {
                echo -e "${RED}错误: NumPy 降级失败，请手动运行: pip install 'numpy>=1.20.0,<2.0.0' --force-reinstall${NC}"
                exit 1
            }
            echo -e "${GREEN}✓${NC} NumPy 已降级"
        else
            echo "NumPy 版本兼容 (1.x)"
        fi
    else
        echo "NumPy 未安装，将在安装依赖时安装..."
    fi
    
    echo "安装依赖包..."
    python3 -m pip install -q -r "$PROJECT_DIR/requirements.txt" || {
        echo -e "${YELLOW}警告: 部分依赖安装失败，尝试继续...${NC}"
    }
    echo -e "${GREEN}✓${NC} 依赖检查完成"
else
    echo -e "${YELLOW}警告: 未找到 requirements.txt${NC}"
fi

echo -e "\n${YELLOW}[步骤 3/4]${NC} 验证文件结构..."
if [ ! -d "$PROJECT_DIR/include" ]; then
    echo -e "${RED}错误: 未找到 include 目录${NC}"
    exit 1
fi
if [ ! -d "$PROJECT_DIR/tests" ]; then
    echo -e "${RED}错误: 未找到 tests 目录${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} 文件结构正确"

echo -e "\n${YELLOW}[步骤 4/4]${NC} 运行测试..."

# 将include目录添加到PYTHONPATH
export PYTHONPATH="$PROJECT_DIR/include:$PYTHONPATH"

# 设置OpenMP和PyTorch相关环境变量以避免崩溃（特别是在macOS上）
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export KMP_DUPLICATE_LIB_OK=TRUE
export KMP_BLOCKTIME=0
export TOKENIZERS_PARALLELISM=false
export OMP_DISPLAY_ENV=FALSE

# 验证NumPy可用性（解决 "Numpy is not available" 错误）
echo -e "\n${YELLOW}验证NumPy可用性...${NC}"
python3 -c "
import numpy as np
print(f'NumPy version: {np.__version__}')
print('NumPy is available: ✓')
" || {
    echo -e "${RED}错误: NumPy不可用，请确保已正确安装${NC}"
    exit 1
}

# 运行pytest
cd "$PROJECT_DIR"

# 检查是否指定了特定测试用例 (参数格式: case1, case2, ... 或 test_case1, test_case2, ...)
if [ -n "$1" ]; then
    # 处理参数，确保格式为 test_case{N}
    TEST_CASE="$1"
    if [[ ! "$TEST_CASE" =~ ^test_ ]]; then
        TEST_CASE="test_$TEST_CASE"
    fi
    TEST_FILE="tests/${TEST_CASE}.py"
    
    if [ -f "$TEST_FILE" ]; then
        echo -e "${GREEN}运行指定测试: $TEST_FILE${NC}"
        python3 -m pytest "$TEST_FILE" -v --tb=short
    else
        echo -e "${RED}错误: 测试文件不存在: $TEST_FILE${NC}"
        exit 1
    fi
else
    # 运行所有测试
    python3 -m pytest tests/ -v --tb=short
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}测试完成!${NC}"
echo -e "${GREEN}========================================${NC}"

