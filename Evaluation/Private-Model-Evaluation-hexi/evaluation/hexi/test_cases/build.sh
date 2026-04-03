#!/bin/bash

# Hexi 测试用例构建脚本

set -e  # 遇到错误立即退出

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Hexi 测试用例构建脚本${NC}"
echo -e "${BLUE}========================================${NC}"

# 检查是否存在 build 目录
if [ -d "build" ]; then
    echo -e "${BLUE}清理旧的构建目录...${NC}"
    rm -rf build
fi

# 创建构建目录
echo -e "${BLUE}创建构建目录...${NC}"
mkdir -p build
cd build

# 运行 CMake 配置
echo -e "${BLUE}运行 CMake 配置...${NC}"
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTS=ON

# 编译
# echo -e "${BLUE}开始编译...${NC}"
# cmake --build . --config Release -j$(nproc)

# # 运行测试
# echo -e "${GREEN}========================================${NC}"
# echo -e "${GREEN}运行测试...${NC}"
# echo -e "${GREEN}========================================${NC}"
# ctest --output-on-failure

# echo -e "${GREEN}========================================${NC}"
# echo -e "${GREEN}构建成功！${NC}"
# echo -e "${GREEN}========================================${NC}"
# echo -e "测试可执行文件位于: ${BLUE}build/test_hexi_basic${NC}"
# echo -e "您可以直接运行: ${BLUE}./build/test_hexi_basic${NC}"

# echo ""
# echo "======================================"
# echo "生成覆盖率报告..."
# echo "======================================"

# # 检测 GCC 版本并选择合适的 gcov 工具
# GCC_VERSION=13
# GCOV_TOOL="gcov-${GCC_VERSION}"

# if ! command -v "$GCOV_TOOL" &> /dev/null; then
#     echo "警告：未找到 $GCOV_TOOL，尝试使用默认的 gcov"
#     GCOV_TOOL="gcov"
# fi

# echo "使用工具：$GCOV_TOOL"

# # 生成文本摘要
# echo ""
# echo "【覆盖率摘要 - 仅 tests 目录】"
# echo pwd: $(pwd)
# gcovr --gcov-executable "$GCOV_TOOL" --root .. --filter '../tests/.*' --print-summary