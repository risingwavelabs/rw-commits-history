from pybuildkite.buildkite import Buildkite, BuildState
from datetime import datetime
from github import Github, Auth
import os

github_token = os.environ.get("GITHUB_TOKEN")
auth = Auth.Token(github_token)
g = Github(auth=auth)

buildkite_token = os.environ.get("BUILDKITE_TOKEN")
buildkite = Buildkite()
buildkite.set_access_token(buildkite_token)


def get_build(page):
    return buildkite.builds().list_all_for_pipeline(
        "risingwavelabs", "docker", page=page, with_pagination=True
    )


nightly_builds = []
earliest = "20240901"


def do(builds):
    for build in builds.body:
        image_tag = build["env"]["IMAGE_TAG"] if "IMAGE_TAG" in build["env"] else ""
        is_cron_build = build["source"] == "schedule"
        # ignore special tags like nightly-20230913-fix
        is_manual_build = image_tag.startswith("nightly-") and len(image_tag) == len(
            "nightly-20231023"
        )
        docker_job_passed = False
        for job in build["jobs"]:
            if (
                job["name"] == "multi-arch-image-create-push"
                and job["state"] == "passed"
            ):
                docker_job_passed = True
                break
        if not (is_cron_build or is_manual_build) or not docker_job_passed:
            continue

        if is_manual_build:
            date = image_tag[8:]
        else:
            # 2023-10-23T12:00:35.649Z -> 20231023
            created_at = datetime.strptime(build["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
            date = created_at.strftime("%Y%m%d")

        commit = build["commit"]

        # skip same commit
        if len(nightly_builds) > 0 and commit == nightly_builds[-1][1]:
            continue
        nightly_builds.append((f"{date}", f"{commit}"))


builds = get_build(1)

do(builds)
while nightly_builds[-1][0] >= earliest and builds.next_page:
    builds = get_build(builds.next_page)
    do(builds)

print("# Nightly builds")
print("<details><summary>list of all night builds</summary>")
print()
for date, commit in nightly_builds:
    print(f"- `{date}` `{commit}`")
print("</details>")
print()


i = 0

print("# Commit history")
print()
print("> [!TIP]")
print("> A release's SHA is the first commit's SHA listed in its section.")
print()
print("## Unreleased")

# get all the commits from risingwavelabs/risingwave
risingwave = g.get_repo("risingwavelabs/risingwave")
commits = []
# get first 100 commits's sha and message

for commit in risingwave.get_commits():
    sha = commit.sha
    message = commit.commit.message
    title = message.split("\n")[0]
    # title ends with  (#12924)
    pr_number = title.split(" ")[-1][2:-1]

    if sha == nightly_builds[i][1]:
        # in output, nightly image contains the commit below, but doesn't contain the commit above
        print()
        print(f"## nightly-{nightly_builds[i][0]}")
        if i != len(nightly_builds) - 1:
            print(
                f"[compare changes with previous nightly build](https://github.com/risingwavelabs/risingwave/compare/{nightly_builds[i+1][1]}...{nightly_builds[i][1]})"
            )
        i += 1
    pr_url = f"https://github.com/risingwavelabs/risingwave/pull/{pr_number}"
    print(f"- `{sha}` [{title}]({pr_url})")
    if i == len(nightly_builds):
        break
