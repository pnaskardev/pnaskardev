"""Render a box-drawn GitHub stats panel into README.md.

Reads the GitHub GraphQL API and rewrites the block between the
STATS:START and STATS:END markers. Run by .github/workflows/main.yml.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com/graphql"
README = "README.md"
START = "<!-- STATS:START -->"
END = "<!-- STATS:END -->"
STAMP = "updated"

WIDTH = 62  # inner width, matches the other panels in the README
BAR = 36
TOP_LANGS = 5

QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    followers { totalCount }
    repositories(
      first: 100
      after: $after
      ownerAffiliations: OWNER
      isFork: false
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
    }
  }
}
"""


def graphql(token, login, after=None):
    body = json.dumps({"query": QUERY, "variables": {"login": login, "after": after}})
    req = urllib.request.Request(
        API,
        data=body.encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{login}-readme-stats",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if "errors" in payload:
        raise RuntimeError(f"GraphQL error: {payload['errors']}")
    return payload["data"]["user"]


def collect(token, login):
    """Walk every page of repositories and total up stars and language bytes."""
    stars = 0
    languages = {}
    after = None
    user = None

    while True:
        user = graphql(token, login, after)
        repos = user["repositories"]
        for repo in repos["nodes"]:
            stars += repo["stargazerCount"]
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                languages[name] = languages.get(name, 0) + edge["size"]
        if not repos["pageInfo"]["hasNextPage"]:
            break
        after = repos["pageInfo"]["endCursor"]

    contrib = user["contributionsCollection"]
    return {
        "repos": user["repositories"]["totalCount"],
        "stars": stars,
        "followers": user["followers"]["totalCount"],
        "commits": contrib["totalCommitContributions"],
        "prs": contrib["totalPullRequestContributions"],
        "issues": contrib["totalIssueContributions"],
        "languages": languages,
    }


def row(text=""):
    if len(text) > WIDTH:
        text = text[:WIDTH]
    return "│" + text.ljust(WIDTH) + "│"


def cell(label, value):
    return f"{label:<10}{value:>4}      "


def render(data, synced):
    total = sum(data["languages"].values())
    ranked = sorted(data["languages"].items(), key=lambda kv: -kv[1])[:TOP_LANGS]

    lines = ["┌─ stats " + "─" * (WIDTH - 8) + "┐", row()]

    lines.append(row("  " + cell("REPOS", data["repos"])
                     + cell("STARS", data["stars"])
                     + cell("FOLLOWERS", data["followers"])))
    lines.append(row("  " + cell("COMMITS", data["commits"])
                     + cell("PRS", data["prs"])
                     + cell("ISSUES", data["issues"])))

    if ranked and total:
        lines.append(row())
        for name, size in ranked:
            pct = size / total * 100
            filled = round(pct / 100 * BAR)
            bar = "█" * filled + "░" * (BAR - filled)
            lines.append(row(f"  {name[:11]:<12}{bar}  {pct:5.1f} %"))

    lines.append(row())
    lines.append(row("  commits · prs · issues over the last 12 months"))
    lines.append(row(f"  {STAMP} {synced}"))
    lines.append("└" + "─" * WIDTH + "┘")

    for line in lines:
        assert len(line) == WIDTH + 2, (len(line), line)
    return "\n".join(lines)


def without_stamp(block):
    """Drop the timestamp line so a clock tick alone never counts as a change."""
    return "\n".join(l for l in block.splitlines() if f"  {STAMP} " not in l)


def splice(panel):
    with open(README, encoding="utf-8") as fh:
        text = fh.read()

    if START not in text or END not in text:
        raise SystemExit(f"{README} is missing the {START} / {END} markers")

    head, rest = text.split(START, 1)
    current, tail = rest.split(END, 1)
    block = f"\n\n```\n{panel}\n```\n\n"
    updated = f"{head}{START}{block}{END}{tail}"

    # Only rewrite when the numbers moved, otherwise CI commits every run.
    if without_stamp(current) == without_stamp(block):
        return False
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return True


def main():
    token = os.environ.get("GITHUB_TOKEN")
    login = os.environ.get("GITHUB_USER")
    if not token or not login:
        raise SystemExit("GITHUB_TOKEN and GITHUB_USER must be set")

    try:
        data = collect(token, login)
    except (urllib.error.URLError, RuntimeError) as exc:
        # A transient API failure must not rewrite the README with junk.
        print(f"stats: fetch failed, leaving README untouched: {exc}", file=sys.stderr)
        return 1

    synced = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    changed = splice(render(data, synced))
    print("stats: README updated" if changed else "stats: no change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
