"""Repo Health Auditor - audit any local repository with a six-agent LangGraph team.

    python main.py /path/to/repo --out report.md
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from auditor.graph import build_graph


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="path to the repository to audit")
    ap.add_argument("--out", default="health_report.md", help="where to write the Markdown report")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"not a directory: {repo}", file=sys.stderr)
        return 2
    state = build_graph().invoke({"repo": str(repo), "facts": {}, "findings": {}})
    Path(args.out).write_text(state["report"], encoding="utf-8")
    print(f"Health score: {state['score']}/100  ->  {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
