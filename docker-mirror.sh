#!/bin/bash
set -e

# =====================================================================
# Docker 镜像镜像工具
#
# 功能：将 Docker 镜像从公共仓库拉取并推送到阿里云容器镜像服务
# 支持：从 .env 文件或环境变量加载配置
# 兼容：支持 macOS 和 Linux 系统
# =====================================================================

# ----- 颜色定义 -----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ----- 日志函数 -----
# 打印信息日志
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# 打印成功日志
success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# 打印警告日志
warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# 打印错误日志
error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# ----- 环境检查函数 -----
# 检查命令是否存在
check_cmd() {
    if ! command -v $1 &> /dev/null; then
        error "$1 命令未找到，请先安装"
    fi
}

# 检查 bash 版本
check_bash() {
    # 获取 bash 主版本号
    bash_version=$(bash --version | head -n 1 | sed -E 's/.*version ([0-9]+).*/\1/')

    if [ "$bash_version" -lt 4 ]; then
        warn "检测到 Bash 版本低于 4.0，将使用兼容模式"
    fi
}

# ----- 配置加载函数 -----
# 加载环境变量
load_config() {
    # 如果 .env 文件存在，则加载
    if [ -f .env ]; then
        info "从 .env 文件加载配置"
        export $(grep -v '^#' .env | xargs)
    fi

    # 检查必要的环境变量
    local missing_vars=()

    if [ -z "$ALIYUN_REGISTRY" ]; then
        missing_vars+=("ALIYUN_REGISTRY")
    fi

    if [ -z "$ALIYUN_NAME_SPACE" ]; then
        missing_vars+=("ALIYUN_NAME_SPACE")
    fi

    if [ -z "$ALIYUN_REGISTRY_USER" ]; then
        missing_vars+=("ALIYUN_REGISTRY_USER")
    fi

    if [ -z "$ALIYUN_REGISTRY_PASSWORD" ]; then
        missing_vars+=("ALIYUN_REGISTRY_PASSWORD")
    fi

    if [ ${#missing_vars[@]} -ne 0 ]; then
        error "缺少以下环境变量:
$(printf "  - %s\n" "${missing_vars[@]}")
请在 .env 文件中设置这些变量或直接在环境中设置"
    fi
}

# ----- Docker 操作函数 -----
# 登录到阿里云容器镜像服务
login_registry() {
    info "登录到阿里云容器镜像服务: $ALIYUN_REGISTRY"
    echo "$ALIYUN_REGISTRY_PASSWORD" | docker login -u "$ALIYUN_REGISTRY_USER" --password-stdin "$ALIYUN_REGISTRY"
    if [ $? -ne 0 ]; then
        error "登录失败，请检查凭据"
    fi
    success "登录成功"
}

# ----- 镜像处理函数 -----
# 处理镜像：拉取、标记、推送到阿里云
process_images() {
    local images_file="images.txt"

    if [ ! -f "$images_file" ]; then
        error "镜像列表文件 $images_file 不存在"
    fi

    info "开始处理镜像列表"

    # 创建临时文件用于存储重复的镜像名
    DUPLICATE_IMAGES_FILE=$(mktemp)
    TEMP_MAP_FILE=$(mktemp)

    # 第一阶段：预处理，检测重名镜像
    info "第一阶段：检测重名镜像"
    detect_duplicate_images "$images_file" "$DUPLICATE_IMAGES_FILE" "$TEMP_MAP_FILE"

    # 第二阶段：拉取和推送镜像
    info "第二阶段：拉取和推送镜像"
    pull_and_push_images "$images_file" "$DUPLICATE_IMAGES_FILE"

    # 清理临时文件
    rm -f "$DUPLICATE_IMAGES_FILE" "$TEMP_MAP_FILE"

    success "所有镜像处理完成"
}

# 检测重名镜像
detect_duplicate_images() {
    local images_file="$1"
    local duplicate_file="$2"
    local map_file="$3"

    while IFS= read -r line || [ -n "$line" ]; do
        # 忽略空行与注释
        [[ -z "$line" ]] && continue
        if echo "$line" | grep -q '^\s*#'; then
            continue
        fi

        # 获取镜像的完整名称，例如 kasmweb/nginx:1.25.3（命名空间/镜像名:版本号）
        image=$(echo "$line" | awk '{print $NF}')
        # 将 @sha256: 等字符删除
        image="${image%%@*}"

        # 解析镜像信息
        image_name_tag=$(echo "$image" | awk -F'/' '{print $NF}')
        name_space=$(echo "$image" | awk -F'/' '{if (NF==3) print $2; else if (NF==2) print $1; else print ""}')
        name_space="${name_space}_"  # 添加下划线，避免空值影响判断
        image_name=$(echo "$image_name_tag" | awk -F':' '{print $1}')

        # 检查是否已经存在该镜像名
        if grep -q "^${image_name}=" "$map_file"; then
            # 获取已存在的命名空间
            existing_namespace=$(grep "^${image_name}=" "$map_file" | cut -d= -f2)

            # 如果命名空间不同，则标记为重复
            if [ "$existing_namespace" != "$name_space" ]; then
                warn "发现重复的镜像名: $image_name (来自不同命名空间)"
                echo "$image_name" >> "$duplicate_file"
            fi
        else
            # 存储镜像名和命名空间的映射
            echo "${image_name}=${name_space}" >> "$map_file"
        fi
    done < "$images_file"
}

# 拉取和推送镜像
pull_and_push_images() {
    local images_file="$1"
    local duplicate_file="$2"

    while IFS= read -r line || [ -n "$line" ]; do
        # 忽略空行与注释
        [[ -z "$line" ]] && continue
        if echo "$line" | grep -q '^\s*#'; then
            continue
        fi

        # 拉取镜像
        info "拉取镜像: $line"
        docker pull $line

        # 解析平台信息
        platform=$(echo "$line" | awk -F'--platform[ =]' '{if (NF>1) print $2}' | awk '{print $1}')

        # 如果存在架构信息，将架构信息拼到镜像名称前面
        platform_prefix=""
        if [ -n "$platform" ]; then
            platform_prefix="${platform//\//_}_"
        fi

        # 获取镜像信息
        image=$(echo "$line" | awk '{print $NF}')
        image_name_tag=$(echo "$image" | awk -F'/' '{print $NF}')
        name_space=$(echo "$image" | awk -F'/' '{if (NF==3) print $2; else if (NF==2) print $1; else print ""}')
        image_name=$(echo "$image_name_tag" | awk -F':' '{print $1}')

        # 处理重名镜像
        name_space_prefix=""
        if grep -q "^${image_name}$" "$duplicate_file" && [ -n "${name_space}" ]; then
            name_space_prefix="${name_space}_"
        fi

        # 构建新镜像名
        image_name_tag="${image_name_tag%%@*}"  # 将 @sha256: 等字符删除
        new_image="$ALIYUN_REGISTRY/$ALIYUN_NAME_SPACE/$platform_prefix$name_space_prefix$image_name_tag"

        # 标记和推送镜像
        info "标记镜像: $image -> $new_image"
        docker tag $image $new_image

        info "推送镜像: $new_image"
        docker push $new_image

        # 清理镜像以释放空间
        info "清理镜像以释放空间"
        docker rmi $image
        docker rmi $new_image

        success "镜像 $image 处理完成，已推送到 $new_image"
    done < "$images_file"
}

# ----- 主函数 -----
main() {
    info "Docker 镜像镜像工具启动"

    # 检查环境
    check_cmd docker
    check_cmd awk
    check_cmd grep
    check_bash

    # 加载配置
    load_config

    # 登录阿里云
    login_registry

    # 处理镜像
    process_images

    success "任务完成"
}

# 执行主函数
main