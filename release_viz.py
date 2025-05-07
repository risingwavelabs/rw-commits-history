#!/usr/bin/env python3
"""
Generate RisingWave release timeline (sweet Gantt-style).

Steps
-----
1. 通过 GitHub API + git merge-base 拿到各 release-X.Y 分支上的
   - branch_creation     (code-freeze)
   - first_release       (vX.Y.Z)
   - last_release
   - last_commit         (HEAD of release branch)
2. 整理成 pandas.DataFrame
3. 用 matplotlib.broken_barh 画 3 段彩条：
   Pre-release / Release / Maintenance
"""

import os
import re
import argparse
import subprocess
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from github import Github
from tqdm import tqdm
from dotenv import load_dotenv

# ---------- ① 数据抓取 ---------- #

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise RuntimeError("请在 .env / ENV 里配置 GITHUB_TOKEN")

g = Github(GITHUB_TOKEN)
repo = g.get_repo("risingwavelabs/risingwave")


def get_release_branches():
    """返回 [(version, github.Branch), ...]，按版本号排序"""
    branches = []
    for br in repo.get_branches():
        if br.name.startswith("release-"):
            ver = br.name.replace("release-", "")
            if re.match(r"^\d+\.\d+$", ver):  # 过滤掉 release-docs 等
                branches.append((ver, br))
    branches.sort(key=lambda t: [int(x) for x in t[0].split(".")])
    return branches


def git_merge_base_date(branch_name: str, repo_path: str) -> datetime | None:
    """用 git merge-base 取分支与 main 分叉时间"""
    try:
        sha = subprocess.check_output(
            [
                "git",
                "-C",
                repo_path,
                "merge-base",
                f"origin/{branch_name}",
                "origin/main",
            ],
            text=True,
        ).strip()
        ts = subprocess.check_output(
            ["git", "-C", repo_path, "show", "-s", "--format=%ci", sha], text=True
        ).strip()
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return None


def get_releases_for_version(version: str):
    rels = []
    rc_rels = []
    for r in repo.get_releases():
        if r.tag_name.startswith(f"v{version}."):
            if "single-node" in r.tag_name:
                # skip
                continue
            if "rc" in r.tag_name:
                rc_rels.append(r)
            else:
                rels.append(r)
    rels.sort(key=lambda r: r.created_at)
    rc_rels.sort(key=lambda r: r.created_at)
    return rels, rc_rels


def collect_release_data() -> pd.DataFrame:
    branches = get_release_branches()

    # 先确定本地仓库（加速 merge-base）
    local_repo = os.path.expanduser("../risingwave")
    if not (os.path.isdir(local_repo) and os.path.isdir(f"{local_repo}/.git")):
        tmp = tempfile.mkdtemp()
        local_repo = f"{tmp}/risingwave"
        print("Cloning repository …")
        subprocess.run(
            [
                "git",
                "clone",
                "--no-checkout",
                "https://github.com/risingwavelabs/risingwave.git",
                local_repo,
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", local_repo, "fetch", "--all"], check=True, capture_output=True
        )
    else:
        tmp = None  # 不用删

    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool, tqdm(
        total=len(branches), desc="Fetch branches"
    ) as bar:
        futures = {}
        for ver, br in branches:
            futures[pool.submit(process_branch, ver, br, local_repo)] = ver
        for fut in as_completed(futures):
            rows.append(fut.result())
            bar.update()

    if tmp:
        shutil.rmtree(tmp)

    df = pd.DataFrame(rows).sort_values("version_num")
    return df.reset_index(drop=True)


def process_branch(version, br, repo_path):
    releases, rc_releases = get_releases_for_version(version)
    row = {
        "version": version,
        "version_num": [int(x) for x in version.split(".")],  # 排序辅助
        "branch_creation": git_merge_base_date(br.name, repo_path),
        "first_release": releases[0].created_at if releases else pd.NaT,
        "last_release": releases[-1].created_at if releases else pd.NaT,
        "last_release_version": releases[-1].tag_name if releases else None,
        "last_commit": br.commit.commit.author.date,
        "rc_releases": rc_releases,
        "formal_releases": releases,
    }
    # 计算各段天数
    row["pre_days"] = days_between(row["branch_creation"], row["first_release"])
    row["live_days"] = days_between(row["first_release"], row["last_release"])
    row["maint_days"] = days_between(row["last_release"], row["last_commit"])
    return row


def days_between(a, b):
    return (b - a).days if pd.notna(a) and pd.notna(b) else None


# ---------- ② 绘图 ---------- #


def plot_release_timeline(df: pd.DataFrame, out_file="release_timeline.svg"):
    COLORS = dict(pre="#b3b3b3", live="#26c06f", maint="#e67e22")
    BAR_H = 0.55

    n = len(df)
    fig, ax = plt.subplots(figsize=(15, max(3, n * 0.55)))

    # y 轴
    ax.set_yticks(range(n))
    ax.set_yticklabels([f"v{s}" for s in df["version"]])
    ax.invert_yaxis()

    # x 轴
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate(rotation=45, ha="right")
    ax.grid(axis="x", color="#d0d0d0", linewidth=0.5, linestyle="--", alpha=0.6)

    # 画条
    for idx, row in df.iterrows():
        y = idx

        # 如果只有 branch_creation，使用 last_commit 作为 pre-release 的结束时间
        if pd.notna(row.branch_creation):
            if pd.isna(row.first_release):
                pre_end = row.last_commit
                pre_days = (pre_end - row.branch_creation).days
            else:
                pre_end = row.first_release
                pre_days = row.pre_days

            spans = {
                "pre": (row.branch_creation, pre_end, pre_days),
                "live": (row.first_release, row.last_release, row.live_days),
                "maint": (row.last_release, row.last_commit, row.maint_days),
            }

            for k, (start, end, days) in spans.items():
                if pd.isna(start) or pd.isna(end):
                    continue
                x = mdates.date2num(start)
                w = (end - start).days
                ax.broken_barh(
                    [(x, w)], (y - BAR_H / 2, BAR_H), facecolors=COLORS[k], zorder=2
                )

                # --- 天数标签 ---
                txt = f"{int(days)}d"
                if w > 25:
                    ax.text(
                        x + w / 2,
                        y,
                        txt,
                        va="center",
                        ha="center",
                        fontsize=8,
                        color="#444",
                        zorder=3,
                    )
                else:
                    ax.text(
                        x + w + 1,
                        y,
                        txt,
                        va="center",
                        ha="left",
                        fontsize=8,
                        color="#444",
                        zorder=3,
                    )

                # --- 起始日期 ---
                date_s = start.strftime("%Y-%m-%d")
                if w < 20:  # 条短 → 日期放条外
                    ax.text(
                        x - 0.6,
                        y - BAR_H / 2 - 0.06,
                        date_s,
                        rotation=90,
                        va="top",
                        ha="right",
                        fontsize=7,
                        color="#666",
                        zorder=3,
                    )
                else:
                    ax.text(
                        x + 0.2,
                        y - BAR_H / 2 - 0.06,
                        date_s,
                        rotation=90,
                        va="top",
                        ha="left",
                        fontsize=7,
                        color="#666",
                        zorder=3,
                    )

    # 图例
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in COLORS.values()]
    ax.legend(
        handles,
        ["Pre-release", "Release", "Maintenance"],
        bbox_to_anchor=(0.5, -0.2),
        loc="center left",
        frameon=False,
        fontsize=9,
    )

    plt.tight_layout()
    fig.savefig(out_file, dpi=300)
    plt.close(fig)
    print(f"Saved ⇢ {out_file}")


# ---------- ③ markdown 表 ---------- #


def to_markdown(df: pd.DataFrame) -> str:
    md = [
        "# RisingWave Release Timeline\n",
        f"![timeline]({Path('release_timeline.svg').name})\n",
        "## Details\n",
        "| Version | Branch creation | RC releases | Formal releases | Last commit |\n",
        "| :------ | :-------------- | :---------- | :-------------- | :---------- |\n",
    ]
    for _, r in df.iterrows():
        if r.last_release_version:
            last_release = f"{fmt_date(r.last_release)} ({r.last_release_version})"
        else:
            last_release = fmt_date(r.last_release)
        rc_release_str = "<br>".join(
            [
                f"{fmt_date(r.rc_releases[i].created_at)} ({r.rc_releases[i].tag_name})"
                for i in range(len(r.rc_releases))
            ]
        )
        formal_release_str = "<br>".join(
            [
                f"{fmt_date(r.formal_releases[i].created_at)} ({r.formal_releases[i].tag_name})"
                for i in range(len(r.formal_releases))
            ]
        )
        md.append(
            f"| v{r.version} | "
            f"{fmt_date(r.branch_creation)} | "
            f"{rc_release_str} | "
            f"{formal_release_str} | "
            f"{fmt_date(r.last_commit)} |\n"
        )
    md.append(f"\n*Generated {datetime.now():%Y-%m-%d}*")
    return "".join(md)


def fmt_date(x):
    return x.strftime("%Y-%m-%d") if pd.notna(x) else "—"


# ---------- ④ CLI ---------- #


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-o", "--out", default="release_timeline.svg", help="output image file"
    )
    args = parser.parse_args()

    print("Collecting release data …")
    df = collect_release_data()
    print(df)

    print("Plotting …")
    plot_release_timeline(df, out_file=args.out)

    md_path = Path(args.out).with_suffix(".md")
    md_path.write_text(to_markdown(df), encoding="utf-8")
    print(f"Markdown ⇢ {md_path}")


if __name__ == "__main__":
    main()
