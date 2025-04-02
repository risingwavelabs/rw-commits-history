#!/usr/bin/env python3
"""
Generates a visualization to track RisingWave release progress.

For each version, tracks 4 key timestamps:
1. Release branch created (merge-base)
2. First release (eg v2.0.1)
3. Last patch release (eg v2.0.6)
4. Last commit in the release branch (eg HEAD of release-2.0)
"""

import os
import re
import sys
import argparse
from datetime import datetime, timedelta
from github import Github
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import io
import base64
import subprocess
import tempfile
import shutil
from pathlib import Path
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# 加载 .env 文件
load_dotenv()

# 从环境变量获取 GitHub token
github_token = os.getenv("GITHUB_TOKEN")
if not github_token:
    raise ValueError(
        "GITHUB_TOKEN environment variable is not set. Please create a .env file based on .env.template"
    )

# 初始化 GitHub client
g = Github(github_token)
repo = g.get_repo("risingwavelabs/risingwave")


def debug_print(message):
    """Print debug messages if debug mode is enabled."""
    if os.getenv("DEBUG"):
        print(f"[DEBUG] {message}")


# Parse arguments
parser = argparse.ArgumentParser(
    description="Generate RisingWave release timeline visualization"
)
parser.add_argument("--debug", action="store_true", help="Enable debug output")
parser.add_argument(
    "--token", help="GitHub token (can also be set via GITHUB_TOKEN env var)"
)
parser.add_argument(
    "--output", default="release_timeline.png", help="Output image filename"
)

if __name__ == "__main__":
    args = parser.parse_args()
    if args.debug:
        DEBUG = True

    debug_print("Debug mode enabled")


def get_release_branches():
    """Get all release branches from the repository."""
    branches = []
    for branch in repo.get_branches():
        if branch.name.startswith("release-"):
            version = branch.name.replace("release-", "")
            # Filter out non-semantic version branches like "release-docs"
            if re.match(r"^\d+\.\d+$", version):
                branches.append((version, branch))

    # Sort branches by version
    branches.sort(key=lambda x: [int(n) for n in x[0].split(".")])
    return branches


def get_branch_creation_date(branch_name, repo_path=None):
    """Get the date when the branch was created (merge-base with main)."""
    try:
        # 使用git命令获取merge-base（分支创建点）
        debug_print(f"Getting merge-base for {branch_name} with main...")

        # 检查是否有本地仓库路径
        local_repo_path = os.path.expanduser("../risingwave")
        using_local_repo = False

        if repo_path:
            # 使用传入的仓库路径
            actual_repo_path = repo_path
            debug_print(f"Using provided repository at: {actual_repo_path}")
        elif os.path.exists(local_repo_path) and os.path.isdir(
            os.path.join(local_repo_path, ".git")
        ):
            # 使用本地仓库
            actual_repo_path = local_repo_path
            using_local_repo = True
            debug_print(f"Using local repository at: {actual_repo_path}")
        else:
            raise ValueError(
                "No repository path provided and no local repository found"
            )

        try:
            # 获取merge-base
            debug_print(f"Finding merge-base between {branch_name} and main...")
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    actual_repo_path,
                    "merge-base",
                    f"origin/{branch_name}",
                    "origin/main",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            merge_base_sha = result.stdout.strip()
            debug_print(f"Found merge-base SHA: {merge_base_sha}")

            # 获取该提交的时间
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    actual_repo_path,
                    "show",
                    "-s",
                    "--format=%ci",
                    merge_base_sha,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            commit_date_str = result.stdout.strip()
            debug_print(f"Commit date string: {commit_date_str}")

            # 解析日期字符串
            commit_date = datetime.strptime(commit_date_str, "%Y-%m-%d %H:%M:%S %z")
            debug_print(f"Parsed commit date: {commit_date}")

            return commit_date

        except Exception as e:
            raise e

    except Exception as e:
        print(f"Error getting branch creation date for {branch_name}: {e}")
        debug_print("Falling back to GitHub API method...")

        # 回退到GitHub API方法
        try:
            # 获取分支信息
            branch = repo.get_branch(branch_name)

            # 获取一组提交（最多100个）
            debug_print(f"Fetching commits for {branch_name} (fallback method)...")
            commits = list(repo.get_commits(sha=branch_name)[:100])

            # 如果有提交，使用最老的提交时间
            if commits:
                oldest_commit = commits[-1]
                debug_print(
                    f"Using oldest commit for {branch_name}: {oldest_commit.sha[:8]}"
                )
                return oldest_commit.commit.author.date

            # 最后使用当前分支HEAD提交时间
            debug_print(f"Using HEAD commit date for {branch_name}")
            return branch.commit.commit.author.date

        except Exception as nested_e:
            debug_print(f"Fallback method also failed: {nested_e}")
            # 使用默认日期
            return datetime.now() - timedelta(days=30)


def get_releases_for_version(version):
    """Get all releases for a specific version."""
    releases = []
    for release in repo.get_releases():
        if (
            release.tag_name.startswith(f"v{version}.")
            and "rc" not in release.tag_name
            and "single-node" not in release.tag_name
        ):
            releases.append(release)

    # Sort releases by creation date
    releases.sort(key=lambda x: x.created_at)
    return releases


def get_last_commit_in_branch(branch):
    """Get the last commit in a branch."""
    return branch.commit.commit.author.date


def process_branch_data(branch_tuple, repo_path=None):
    """Process data for a single branch concurrently."""
    version, branch = branch_tuple
    releases = get_releases_for_version(version)

    branch_creation = get_branch_creation_date(branch.name, repo_path)
    first_release = releases[0].created_at if releases else None
    last_release = releases[-1].created_at if releases else None
    last_commit = get_last_commit_in_branch(branch)

    return {
        "version": version,
        "branch_name": branch.name,
        "branch_creation": branch_creation,
        "first_release": first_release,
        "last_release": last_release,
        "last_commit": last_commit,
        "first_release_tag": releases[0].tag_name if releases else None,
        "last_release_tag": releases[-1].tag_name if releases else None,
    }


def generate_timeline_data():
    """Generate timeline data for visualization."""
    branches = get_release_branches()
    release_data = []

    # 在线程池外克隆仓库
    temp_dir = None
    repo_path = None
    try:
        # 检查是否有本地仓库
        local_repo_path = os.path.expanduser("../risingwave")
        if os.path.exists(local_repo_path) and os.path.isdir(
            os.path.join(local_repo_path, ".git")
        ):
            repo_path = local_repo_path
            debug_print(f"Using local repository at: {repo_path}")
        else:
            # 创建临时目录并克隆仓库
            temp_dir = tempfile.mkdtemp()
            repo_path = os.path.join(temp_dir, "risingwave")
            debug_print(f"Creating temp directory for repository: {temp_dir}")

            # Clone the repository with all branches but without checking out
            debug_print("Cloning repository...")
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--no-checkout",
                    "https://github.com/risingwavelabs/risingwave.git",
                    repo_path,
                ],
                check=True,
                capture_output=True,
            )

            # Fetch all branches
            subprocess.run(
                ["git", "-C", repo_path, "fetch", "--all"],
                check=True,
                capture_output=True,
            )

        # 使用线程池并发处理每个分支的数据
        with ThreadPoolExecutor(max_workers=len(branches)) as executor:
            # 提交所有任务，传入repo_path
            future_to_branch = {
                executor.submit(
                    process_branch_data, branch_tuple, repo_path
                ): branch_tuple[0]
                for branch_tuple in branches
            }

            # 使用tqdm显示进度
            with tqdm(total=len(branches), desc="Fetching release data") as pbar:
                # 处理完成的任务
                for future in as_completed(future_to_branch):
                    version = future_to_branch[future]
                    try:
                        data = future.result()
                        if data:
                            release_data.append(data)
                    except Exception as e:
                        print(f"Error processing version {version}: {e}")
                    pbar.update(1)

    finally:
        # 清理临时目录
        if temp_dir:
            debug_print(f"Cleaning up temp directory: {temp_dir}")
            shutil.rmtree(temp_dir)

    # 按版本排序
    release_data.sort(key=lambda x: [int(n) for n in x["version"].split(".")])
    return release_data


def generate_visualization(release_data, output_file="release_timeline.png"):
    """Generate a visual timeline of releases."""
    # Define colors - using a more pleasing color scheme
    BACKGROUND_COLOR = (245, 245, 245)  # 浅灰色背景
    HEADER_BACKGROUND = (230, 230, 230)  # 更浅的灰色头部背景
    BRANCH_COLOR = (180, 180, 180)  # 中灰色 code freeze
    ACTIVE_COLOR = (46, 204, 113)  # 柔和的绿色 release
    RELEASE_COLOR = (230, 126, 34)  # 柔和的橙色 unreleased cherry-pick
    TEXT_COLOR = (70, 70, 70)  # 深灰色文字
    TEXT_COLOR_LIGHT = (120, 120, 120)  # 浅灰色文字（用于天数）
    GRID_COLOR = (180, 180, 180)  # 网格线颜色，加深一点

    # Image settings - adjusted for better spacing
    width = 1600  # 增加宽度
    height = 100 * (len(release_data) + 3)  # 增加高度以容纳顶部日期
    img = Image.new("RGB", (width, height), color=BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    # Find min and max dates for scaling
    all_dates = []
    for data in release_data:
        for date_field in [
            "branch_creation",
            "first_release",
            "last_release",
            "last_commit",
        ]:
            if data[date_field]:
                all_dates.append(data[date_field])

    min_date = min(all_dates)
    max_date = max(all_dates)
    date_range = (max_date - min_date).total_seconds()

    # Create font - adjusted sizes
    try:
        font = ImageFont.truetype("Arial", 20)  # 增大基础字体
        big_font = ImageFont.truetype("Arial", 24)  # 增大标题字体
        small_font = ImageFont.truetype("Arial", 16)  # 增大小字体
    except IOError:
        font = ImageFont.load_default()
        big_font = ImageFont.load_default()
        small_font = font

    # 创建版本标签头部
    header_height = 160  # 增加头部高度以容纳日期
    draw.rectangle([0, 0, width, header_height], fill=HEADER_BACKGROUND)

    # 计算最右侧边界
    rightmost_edge = width - 120

    # 归一化日期到可用宽度的辅助函数
    def normalize_date(date):
        if not date:
            return None
        time_diff = (date - min_date).total_seconds()
        return (time_diff / date_range) * (rightmost_edge - 200) + 200

    # 计算两个日期之间的天数
    def days_between(start_date, end_date):
        if not start_date or not end_date:
            return None
        return (end_date - start_date).days

    # 设置对角线布局参数 - 调整间距
    start_offset_y = header_height + 40  # 增加头部下方间距
    row_height = 80  # 增加行高
    bar_height = 40  # 减小条形图高度以获得更好的视觉效果

    # 首先绘制所有的虚线，确保它们在背景层
    for i, data in enumerate(release_data):
        if data["branch_creation"]:
            date_x = normalize_date(data["branch_creation"])
            row_y = start_offset_y + i * row_height
            bar_center_y = row_y + bar_height / 2

            # 从顶部日期位置画到对应方块位置
            for y in range(60, int(bar_center_y), 6):  # 使用更大的间距
                draw.line(
                    [(date_x, y), (date_x, y + 3)],  # 更短的实线部分
                    fill=GRID_COLOR,
                    width=1,
                )

    # 绘制日期标签
    date_y_positions = []  # 用于跟踪日期标签的Y位置
    date_x_positions = []  # 用于跟踪日期标签的X位置

    for i, data in enumerate(release_data):
        if data["branch_creation"]:
            date_x = normalize_date(data["branch_creation"])
            date_str = data["branch_creation"].strftime("%Y-%m-%d")
            date_width = draw.textlength(date_str, font=small_font)

            # 检查是否与之前的日期重叠
            y_pos = 20  # 默认Y位置
            if date_x_positions:
                # 检查与前一个日期的距离
                prev_x = date_x_positions[-1]
                prev_width = draw.textlength(
                    data["branch_creation"].strftime("%Y-%m-%d"), font=small_font
                )
                if abs(date_x - prev_x) < (prev_width + date_width) / 2 + 20:
                    # 如果重叠，将此日期放在下一行
                    y_pos = 40

            date_x_positions.append(date_x)
            date_y_positions.append(y_pos)

            # 绘制日期
            draw.text(
                (date_x - date_width / 2, y_pos),
                date_str,
                font=small_font,
                fill=TEXT_COLOR,
            )

    # 绘制版本标签
    for i in range(len(release_data)):
        data = release_data[i]
        if i == len(release_data) - 1:
            version = "main"
        else:
            version = f"v{release_data[i+1]['version']}"
        # 使用代码冻结日期的x坐标来对齐版本号
        if data["branch_creation"]:
            x = normalize_date(data["branch_creation"])
            version_width = draw.textlength(version, font=big_font)
            draw.text(
                (x - version_width / 2, header_height - 40),
                version,
                font=big_font,
                fill=TEXT_COLOR,
            )

    # 绘制每个版本的时间线
    for i, data in enumerate(release_data):
        version = f"v{data['version']}"
        row_y = start_offset_y + i * row_height
        bar_center_y = row_y + bar_height / 2
        bar_top = bar_center_y - bar_height / 2
        bar_bottom = bar_center_y + bar_height / 2

        # 获取时间点
        branch_created_x = normalize_date(data["branch_creation"])
        first_release_x = normalize_date(data["first_release"])
        last_release_x = normalize_date(data["last_release"])
        last_commit_x = normalize_date(data["last_commit"])

        if branch_created_x and last_commit_x:
            # 1. Code freeze period
            if branch_created_x:
                end = first_release_x if first_release_x else last_commit_x
                data_end = (
                    data["first_release"] if first_release_x else data["last_commit"]
                )
                draw.rectangle(
                    [branch_created_x, bar_top, end, bar_bottom],
                    fill=BRANCH_COLOR,
                    outline=None,
                )
                days = days_between(data["branch_creation"], data_end)
                if days is not None:
                    text = f"{days}d"
                    text_width = draw.textlength(text, font=small_font)
                    text_x = (
                        branch_created_x + (end - branch_created_x - text_width) / 2
                    )
                    text_y = bar_top - 25  # 保持在上方
                    draw.text(
                        (text_x, text_y), text, font=small_font, fill=TEXT_COLOR_LIGHT
                    )

            # 2. Release period
            if first_release_x and last_release_x:
                draw.rectangle(
                    [first_release_x, bar_top, last_release_x, bar_bottom],
                    fill=ACTIVE_COLOR,
                    outline=None,
                )
                text_x = first_release_x + 15
                text_y = bar_center_y - 10
                draw.text((text_x, text_y), version, font=font, fill=BACKGROUND_COLOR)

                days = days_between(data["first_release"], data["last_release"])
                if days is not None:
                    text = f"{days}d"
                    text_width = draw.textlength(text, font=small_font)
                    text_x = (
                        first_release_x
                        + (last_release_x - first_release_x - text_width) / 2
                    )
                    text_y = bar_top - 25  # 移到上方
                    draw.text(
                        (text_x, text_y), text, font=small_font, fill=TEXT_COLOR_LIGHT
                    )

            # 3. Maintenance period
            if last_release_x and last_commit_x:
                draw.rectangle(
                    [last_release_x, bar_top, last_commit_x, bar_bottom],
                    fill=RELEASE_COLOR,
                    outline=None,
                )
                days = days_between(data["last_release"], data["last_commit"])
                if days is not None:
                    text = f"{days}d"
                    text_width = draw.textlength(text, font=small_font)
                    text_x = (
                        last_release_x
                        + (last_commit_x - last_release_x - text_width) / 2
                    )
                    text_y = bar_top - 25  # 移到上方
                    draw.text(
                        (text_x, text_y), text, font=small_font, fill=TEXT_COLOR_LIGHT
                    )

    # Draw legend in the bottom left corner
    legend_x = 50
    legend_y = height - 120  # 120 pixels from bottom
    legend_box_width = 30
    legend_box_height = 20
    legend_text_offset = 40
    legend_spacing = 40

    # Draw legend boxes and labels
    legend_items = [
        (BRANCH_COLOR, "Pre-Release Period:  code freeze ~ first release"),
        (ACTIVE_COLOR, "Release Period:      first release ~ last patch release"),
        (RELEASE_COLOR, "Maintenance Period: last patch release ~ last commit"),
    ]

    for i, (color, label) in enumerate(legend_items):
        # Draw colored rectangle
        box_y = legend_y + i * legend_spacing
        draw.rectangle(
            [legend_x, box_y, legend_x + legend_box_width, box_y + legend_box_height],
            fill=color,
            outline=None,
        )
        # Draw label
        draw.text(
            (legend_x + legend_box_width + legend_text_offset, box_y + 2),
            label,
            font=font,
            fill=TEXT_COLOR,
        )

    # Save the image
    img.save(output_file)
    print(f"Visualization saved to {output_file}")

    # Return the image data for embedding in README
    with open(output_file, "rb") as img_file:
        img_data = base64.b64encode(img_file.read()).decode("utf-8")
    return img_data


def generate_markdown(release_data):
    """Generate markdown content for README."""
    md = "# RisingWave Release Timeline\n\n"
    md += f"![Release Timeline](./release_timeline.png)\n\n"

    md += "## Release Details\n\n"
    md += "| Version | Branch Created | First Release | Last Release | Last Commit |\n"
    md += "| :------ | :------------- | :------------ | :----------- | :---------- |\n"

    for data in release_data:
        branch_date = (
            data["branch_creation"].strftime("%b %d, %Y")
            if data["branch_creation"]
            else "N/A"
        )
        first_rel = (
            f"**{data['first_release_tag']}**<br>({data['first_release'].strftime('%b %d, %Y')})"
            if data["first_release"]
            else "N/A"
        )
        last_rel = (
            f"**{data['last_release_tag']}**<br>({data['last_release'].strftime('%b %d, %Y')})"
            if data["last_release"]
            else "N/A"
        )
        last_commit = (
            data["last_commit"].strftime("%b %d, %Y") if data["last_commit"] else "N/A"
        )

        md += f"| v{data['version']} | {branch_date} | {first_rel} | {last_rel} | {last_commit} |\n"

    md += "\n*Generated on " + datetime.now().strftime("%b %d, %Y %H:%M:%S") + "*\n"
    return md


def main():
    print("Fetching release data from GitHub...")
    release_data = generate_timeline_data()
    print(f"Found {len(release_data)} releases")

    output_file = (
        args.output
        if __name__ == "__main__" and hasattr(args, "output")
        else "release_timeline.png"
    )
    debug_print(f"Using output file: {output_file}")

    print("Generating visualization...")
    img_data = generate_visualization(release_data, output_file=output_file)

    print("Generating markdown...")
    md_content = generate_markdown(release_data)
    with open(output_file.replace(".png", ".md"), "w") as f:
        f.write(md_content)

    print("Done!")


if __name__ == "__main__":
    main()
