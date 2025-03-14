#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Docker镜像镜像工具 - Python版本

此脚本用于将Docker镜像从公共仓库拉取并推送到阿里云容器镜像服务
支持从.env文件或环境变量加载配置
"""

import os
import sys
import subprocess
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import logging
import colorama
from colorama import Fore, Style
from dotenv import load_dotenv

# 初始化colorama
colorama.init()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

class ColoredFormatter(logging.Formatter):
    """自定义日志格式化器，添加颜色"""

    FORMATS = {
        logging.DEBUG: Fore.CYAN + "[DEBUG] " + Fore.RESET + "%(message)s",
        logging.INFO: Fore.BLUE + "[INFO] " + Fore.RESET + "%(message)s",
        logging.WARNING: Fore.YELLOW + "[WARNING] " + Fore.RESET + "%(message)s",
        logging.ERROR: Fore.RED + "[ERROR] " + Fore.RESET + "%(message)s",
        logging.CRITICAL: Fore.RED + Style.BRIGHT + "[CRITICAL] " + Style.RESET_ALL + "%(message)s",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

# 获取logger并设置格式化器
logger = logging.getLogger()
for handler in logger.handlers:
    handler.setFormatter(ColoredFormatter())

class DockerMirror:
    """Docker镜像镜像工具类"""

    def __init__(self):
        """初始化Docker镜像镜像工具"""
        self.load_env()
        self.images_file = "images.txt"
        self.duplicate_images: Set[str] = set()

    def load_env(self) -> None:
        """加载环境变量"""
        # 尝试从.env文件加载环境变量
        env_path = Path('.env')
        if env_path.exists():
            logger.info("从.env文件加载配置")
            load_dotenv(env_path)

        # 检查必要的环境变量
        missing_vars = []

        self.registry = os.getenv('ALIYUN_REGISTRY')
        if not self.registry:
            missing_vars.append('ALIYUN_REGISTRY')

        self.namespace = os.getenv('ALIYUN_NAME_SPACE')
        if not self.namespace:
            missing_vars.append('ALIYUN_NAME_SPACE')

        self.username = os.getenv('ALIYUN_REGISTRY_USER')
        if not self.username:
            missing_vars.append('ALIYUN_REGISTRY_USER')

        self.password = os.getenv('ALIYUN_REGISTRY_PASSWORD')
        if not self.password:
            missing_vars.append('ALIYUN_REGISTRY_PASSWORD')

        if missing_vars:
            logger.error("缺少以下环境变量:")
            for var in missing_vars:
                logger.error(f"  - {var}")
            logger.error("请在.env文件中设置这些变量或直接在环境中设置")
            sys.exit(1)

    def run_command(self, command: List[str], check: bool = True, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        """运行命令并返回结果"""
        try:
            result = subprocess.run(
                command,
                check=check,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                input=input_text
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"命令执行失败: {' '.join(command)}")
            logger.error(f"错误信息: {e.stderr}")
            if check:
                sys.exit(1)
            raise

    def docker_login(self) -> None:
        """登录到阿里云容器镜像服务"""
        logger.info(f"登录到阿里云容器镜像服务: {self.registry}")

        # 使用subprocess执行docker login命令
        result = self.run_command(
            ["docker", "login",
             "-u", self.username,
             "--password-stdin",
             self.registry],
            input_text=self.password
        )

        if result.returncode != 0:
            logger.error("登录失败，请检查凭据")
            logger.error(f"错误信息: {result.stderr}")
            sys.exit(1)

        logger.info("登录成功")

    def preprocess_images(self) -> None:
        """预处理镜像列表，检测重名镜像"""
        if not Path(self.images_file).exists():
            logger.error(f"镜像列表文件 {self.images_file} 不存在")
            sys.exit(1)

        logger.info("开始处理镜像列表")

        # 用于检测重名的临时映射
        temp_map: Dict[str, str] = {}

        with open(self.images_file, 'r') as f:
            for line in f:
                line = line.strip()

                # 忽略空行与注释
                if not line or line.startswith('#'):
                    continue

                # 获取镜像的完整名称
                image = line.split()[-1]

                # 将@sha256:等字符删除
                image = image.split('@')[0]
                logger.info(f"处理镜像: {image}")

                # 获取镜像名:版本号
                parts = image.split('/')
                image_name_tag = parts[-1]
                logger.info(f"镜像名:版本号: {image_name_tag}")

                # 获取命名空间
                if len(parts) == 3:
                    name_space = parts[1]
                elif len(parts) == 2:
                    name_space = parts[0]
                else:
                    name_space = ""
                logger.info(f"命名空间: {name_space}")

                # 这里不要是空值影响判断
                name_space = f"{name_space}_"

                # 获取镜像名
                image_name = image_name_tag.split(':')[0]
                logger.info(f"镜像名: {image_name}")

                # 检查是否重名
                if image_name in temp_map:
                    if temp_map[image_name] != name_space:
                        logger.warning(f"发现重复的镜像名: {image_name}")
                        self.duplicate_images.add(image_name)
                else:
                    temp_map[image_name] = name_space

    def process_images(self) -> None:
        """处理镜像：拉取、标记、推送到阿里云"""
        logger.info("开始拉取和推送镜像")

        with open(self.images_file, 'r') as f:
            for line in f:
                line = line.strip()

                # 忽略空行与注释
                if not line or line.startswith('#'):
                    continue

                # 解析平台信息
                platform_match = re.search(r'--platform[ =]([^\s]+)', line)
                platform = platform_match.group(1) if platform_match else ""

                # 获取镜像名称（最后一个参数）
                image = line.split()[-1]

                # 构建docker pull命令
                pull_cmd = ["docker", "pull"]
                if platform:
                    # 如果有平台参数，添加到命令中
                    pull_cmd.extend(["--platform", platform])
                pull_cmd.append(image)

                logger.info(f"拉取镜像: {' '.join(pull_cmd)}")
                self.run_command(pull_cmd)

                logger.info(f"平台架构: {platform}")

                # 如果存在架构信息 将架构信息拼到镜像名称前面
                if not platform:
                    platform_prefix = ""
                else:
                    platform_prefix = f"{platform.replace('/', '_')}_"
                logger.info(f"平台前缀: {platform_prefix}")

                # 获取镜像名:版本号
                parts = image.split('/')
                image_name_tag = parts[-1]

                # 获取命名空间
                if len(parts) == 3:
                    name_space = parts[1]
                elif len(parts) == 2:
                    name_space = parts[0]
                else:
                    name_space = ""

                # 获取镜像名
                image_name = image_name_tag.split(':')[0]

                name_space_prefix = ""
                # 如果镜像名重名
                if image_name in self.duplicate_images:
                    # 如果命名空间非空，将命名空间加到前缀
                    if name_space:
                        name_space_prefix = f"{name_space}_"

                # 将@sha256:等字符删除
                image_name_tag = image_name_tag.split('@')[0]
                new_image = f"{self.registry}/{self.namespace}/{platform_prefix}{name_space_prefix}{image_name_tag}"

                logger.info(f"标记镜像: docker tag {image} {new_image}")
                self.run_command(["docker", "tag", image, new_image])

                logger.info(f"推送镜像: docker push {new_image}")
                self.run_command(["docker", "push", new_image])

                logger.info("清理镜像以释放空间")
                self.run_command(["docker", "rmi", image], check=False)
                self.run_command(["docker", "rmi", new_image], check=False)

                logger.info(f"镜像 {image} 处理完成，已推送到 {new_image}")

        logger.info("所有镜像处理完成")

    def run(self) -> None:
        """运行Docker镜像镜像工具"""
        logger.info("Docker镜像镜像工具启动")
        self.docker_login()
        self.preprocess_images()
        self.process_images()
        logger.info("任务完成")

def main():
    """主函数"""
    try:
        mirror = DockerMirror()
        mirror.run()
    except KeyboardInterrupt:
        logger.info("用户中断，退出程序")
        sys.exit(0)
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()