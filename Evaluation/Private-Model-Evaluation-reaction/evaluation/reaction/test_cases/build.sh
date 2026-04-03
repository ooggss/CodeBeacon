#!/bin/bash

# ============================================
# Reaction 测试用例构建脚本
# ============================================

# 设置错误时退出
set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================"
echo "Building Reaction Test Cases"
echo "======================================"

# 清理旧的构建目录（可选）
if [ "$1" == "clean" ]; then
    echo "Cleaning build directory..."
    rm -rf build
fi

# 创建构建目录
mkdir -p build
cd build

# 运行 CMake 配置
echo "Configuring with CMake..."
cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTS=ON

# 编译
# echo "Building..."
# cmake --build . -j$(nproc)

# # 运行测试
# echo "======================================"
# echo "Running Tests"
# echo "======================================"
# ctest --output-on-failure

# echo "======================================"
# echo "Build and Test Completed Successfully!"
# echo "======================================"

# ENABLE_COVERAGE=${ENABLE_COVERAGE:-ON}
# # 仅在启用覆盖率时生成报告
# if [ "$ENABLE_COVERAGE" = "ON" ]; then
#     echo ""
#     echo "======================================"
#     echo "生成覆盖率报告..."
#     echo "======================================"

#     # 检测 GCC 版本并选择合适的 gcov 工具
#     GCC_VERSION=13
#     GCOV_TOOL="gcov-${GCC_VERSION}"

#     if ! command -v "$GCOV_TOOL" &> /dev/null; then
#         echo "警告：未找到 $GCOV_TOOL，尝试使用默认的 gcov"
#         GCOV_TOOL="gcov"
#     fi

#     echo "使用工具：$GCOV_TOOL"

#     # 生成文本摘要
#     echo ""
#     echo "【覆盖率摘要 - tests 目录】"
#     echo "当前目录: $(pwd)"
#     # 使用绝对路径过滤 tests 目录下的源文件
#     gcovr --gcov-executable "$GCOV_TOOL" \
#           --root "$PROJECT_DIR" \
#           --filter "$PROJECT_DIR/tests/" \
#           --print-summary
# else
#     echo ""
#     echo -e "${YELLOW}提示：未启用代码覆盖率。如需生成覆盖率报告，请使用：${NC}"
#     echo -e "  ${YELLOW}ENABLE_COVERAGE=ON CLEAN_BUILD=yes ./build.sh${NC}"
# fi