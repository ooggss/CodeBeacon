#!/bin/bash

# sqlgen 测试用例构建脚本

set -e  # 遇到错误立即退出

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}sqlgen 测试用例构建脚本${NC}"
echo -e "${GREEN}========================================${NC}"

# 获取项目根目录
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQLGEN_DIR="/data/workspace/Private-Model-Evaluation-sqlgen/third_patry_project/sqlgen"
# SQLGEN_DIR="${PROJECT_DIR}/../../../third_patry_project/sqlgen"

echo -e "\n${YELLOW}[步骤 1/5]${NC} 检查 sqlgen 目录..."
if [ ! -d "$SQLGEN_DIR" ]; then
    echo -e "${RED}错误: 找不到 sqlgen 目录: $SQLGEN_DIR${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} sqlgen 目录存在"

echo -e "\n${YELLOW}[步骤 2/5]${NC} 初始化 vcpkg（如果需要）..."
if [ ! -f "$SQLGEN_DIR/vcpkg/vcpkg" ]; then
    echo "正在初始化 vcpkg..."
    cd "$SQLGEN_DIR"
    git submodule update --init --recursive
    if [ ! -f "./vcpkg/bootstrap-vcpkg.sh" ]; then
        git clone https://github.com/microsoft/vcpkg.git vcpkg
    fi
    ./vcpkg/bootstrap-vcpkg.sh
    cd "$PROJECT_DIR"
    echo -e "${GREEN}✓${NC} vcpkg 初始化完成"
else
    echo -e "${GREEN}✓${NC} vcpkg 已经初始化"
fi

echo -e "\n${YELLOW}[步骤 3/5]${NC} 配置构建选项..."

# 默认选项
BUILD_TYPE="${BUILD_TYPE:-Release}"
ENABLE_MYSQL="${ENABLE_MYSQL:-OFF}"
ENABLE_POSTGRES="${ENABLE_POSTGRES:-OFF}"
ENABLE_SQLITE="${ENABLE_SQLITE:-ON}"
BUILD_DIR="${BUILD_DIR:-build}"
CLEAN_BUILD="${CLEAN_BUILD:-no}"
ENABLE_COVERAGE="${ENABLE_COVERAGE:-ON}"

# 如果启用覆盖率，强制使用 Debug 模式
if [ "$ENABLE_COVERAGE" = "ON" ] || [ "$ENABLE_COVERAGE" = "1" ] || [ "$ENABLE_COVERAGE" = "yes" ]; then
    ENABLE_COVERAGE="ON"
    BUILD_TYPE="Debug"
    echo -e "${YELLOW}注意：启用覆盖率，自动切换到 Debug 模式${NC}"
fi

echo "  构建类型: $BUILD_TYPE"
echo "  MySQL 支持: $ENABLE_MYSQL"
echo "  PostgreSQL 支持: $ENABLE_POSTGRES"
echo "  SQLite 支持: $ENABLE_SQLITE"
echo "  构建目录: $BUILD_DIR"
echo "  清理重建: $CLEAN_BUILD"
echo "  代码覆盖率: $ENABLE_COVERAGE"

# 如果需要清理构建
if [ "$CLEAN_BUILD" = "yes" ] || [ "$CLEAN_BUILD" = "1" ]; then
    if [ -d "$PROJECT_DIR/$BUILD_DIR" ]; then
        echo -e "${YELLOW}清理旧的构建目录...${NC}"
        rm -rf "$PROJECT_DIR/$BUILD_DIR"
        echo -e "${GREEN}✓${NC} 构建目录已清理"
    fi
fi

echo -e "\n${YELLOW}[步骤 4/5]${NC} 运行 CMake 配置..."
export CC=gcc
export CXX=g++
cmake -S "$PROJECT_DIR" -B "$PROJECT_DIR/$BUILD_DIR" \
    -G "Unix Makefiles" \
    -DCMAKE_MAKE_PROGRAM="$(command -v make)" \
    -DCMAKE_C_COMPILER="$(command -v gcc)" \
    -DCMAKE_CXX_COMPILER="$(command -v g++)" \
    -DCMAKE_CXX_STANDARD=20 \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DSQLGEN_MYSQL="$ENABLE_MYSQL" \
    -DSQLGEN_POSTGRES="$ENABLE_POSTGRES" \
    -DSQLGEN_SQLITE3="$ENABLE_SQLITE" \
    -DENABLE_COVERAGE="$ENABLE_COVERAGE"

echo -e "${GREEN}✓${NC} CMake 配置完成"

# echo -e "\n${YELLOW}[步骤 5/5]${NC} 编译项目..."
# cmake --build "$PROJECT_DIR/$BUILD_DIR" -j "$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"

# echo -e "\n${GREEN}========================================${NC}"
# echo -e "${GREEN}构建成功!${NC}"
# echo -e "${GREEN}========================================${NC}"

# 列出生成的可执行文件
# echo -e "\n生成的可执行文件:"
# find "$PROJECT_DIR/$BUILD_DIR" -maxdepth 1 -type f -executable -exec basename {} \; 2>/dev/null || \
# find "$PROJECT_DIR/$BUILD_DIR" -maxdepth 1 -type f -perm +111 -exec basename {} \; 2>/dev/null || \
# echo "  example_test (预期)"

# echo -e "\n运行示例测试:"
# echo -e "  ${YELLOW}cd $PROJECT_DIR${NC}"
# echo -e "  ${YELLOW}./$BUILD_DIR/example_test${NC}"

# echo -e "\n使用帮助:"
# echo -e "  设置环境变量来自定义构建:"
# echo -e "    ${YELLOW}BUILD_TYPE=Debug ./build.sh${NC}          # Debug 构建"
# echo -e "    ${YELLOW}ENABLE_MYSQL=ON ./build.sh${NC}           # 启用 MySQL"
# echo -e "    ${YELLOW}ENABLE_POSTGRES=ON ./build.sh${NC}        # 启用 PostgreSQL"
# echo -e "    ${YELLOW}BUILD_DIR=my_build ./build.sh${NC}        # 自定义构建目录"
# echo -e "    ${YELLOW}CLEAN_BUILD=yes ./build.sh${NC}           # 清理后重新构建"
# echo -e "    ${YELLOW}ENABLE_COVERAGE=ON CLEAN_BUILD=yes ./build.sh${NC}  # 启用代码覆盖率"

# echo -e "\n${RED}开始测试...${NC}"
# cd build
# ctest --output-on-failure

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
