"""Real, deterministic tools the agents use to inspect a local repository."""
from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox",
             ".mypy_cache", ".pytest_cache", ".idea", ".next", "target", "vendor"}
LANGS = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
         ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
         ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++", ".cs": "C#",
         ".swift": "Swift", ".sh": "Shell"}
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "generic_secret": re.compile(r"(?i)(?:api[_-]?key|secret|passwd|password)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"),
}


def _git(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def iter_files(repo: Path):
    for path in repo.rglob("*"):
        if path.is_file() and not (set(path.relative_to(repo).parts) & SKIP_DIRS):
            yield path


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


# ---------------------------------------------------------------- git historian
def git_stats(repo: Path) -> dict:
    if not (repo / ".git").exists():
        return {"available": False, "reason": "not a git repository"}
    log = _git(repo, "log", "--since=12 months ago", "--no-merges", "--date=short",
               "--pretty=format:%an|%ad|%s")
    commits = [line.split("|", 2) for line in log.splitlines() if line.count("|") >= 2]
    authors = Counter(c[0] for c in commits)
    months = Counter(c[1][:7] for c in commits)
    churn: Counter = Counter()
    for line in _git(repo, "log", "--since=6 months ago", "--name-only", "--pretty=format:").splitlines():
        if line.strip():
            churn[line.strip()] += 1
    top_share = (authors.most_common(1)[0][1] / len(commits)) if commits else 0.0
    return {
        "available": True,
        "total_commits": int((_git(repo, "rev-list", "--count", "HEAD").strip() or 0)),
        "commits_last_12_months": len(commits),
        "active_authors_last_12_months": len(authors),
        "top_author_share": round(top_share, 2),
        "commits_per_month": dict(sorted(months.items())),
        "most_changed_files_6m": churn.most_common(10),
        "last_commit": _git(repo, "log", "-1", "--date=short", "--pretty=format:%ad %s").strip(),
        "branches": len(_git(repo, "branch", "--all").splitlines()),
        "uncommitted_changes": len(_git(repo, "status", "--porcelain").splitlines()),
    }


# ---------------------------------------------------------------- code scanner
def scan_code(repo: Path) -> dict:
    loc: Counter = Counter()
    big_files, markers, secrets = [], Counter(), []
    for path in iter_files(repo):
        lang = LANGS.get(path.suffix.lower())
        if not lang:
            continue
        n = count_lines(path)
        loc[lang] += n
        rel = str(path.relative_to(repo))
        if n > 800:
            big_files.append((rel, n))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for marker in ("TODO", "FIXME", "HACK", "XXX"):
            markers[marker] += text.count(marker)
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(text):
                secrets.append({"file": rel, "kind": name})  # never record the value itself
    return {
        "lines_of_code": dict(loc.most_common()),
        "total_loc": sum(loc.values()),
        "files_over_800_lines": sorted(big_files, key=lambda x: -x[1])[:10],
        "todo_markers": dict(markers),
        "possible_secrets": secrets[:20],
    }


# ---------------------------------------------------------------- dependency auditor
def audit_dependencies(repo: Path) -> dict:
    found: dict[str, list[dict]] = {}
    for req in repo.glob("requirements*.txt"):
        deps = []
        for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith(("-", "git+")):
                deps.append({"spec": line, "pinned": "==" in line})
        found[req.name] = deps
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            specs = data.get("project", {}).get("dependencies", [])
            found["pyproject.toml"] = [{"spec": s, "pinned": "==" in s} for s in specs]
        except tomllib.TOMLDecodeError:
            found["pyproject.toml"] = []
    package_json = repo / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            found["package.json"] = [{"spec": f"{k}@{v}", "pinned": bool(re.fullmatch(r"\d[\w.\-]*", v))}
                                     for k, v in deps.items()]
        except json.JSONDecodeError:
            found["package.json"] = []
    lockfiles = [p.name for p in repo.iterdir() if p.name in
                 {"poetry.lock", "Pipfile.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                  "uv.lock", "Cargo.lock", "go.sum"}]
    total = sum(len(v) for v in found.values())
    unpinned = sum(1 for v in found.values() for d in v if not d["pinned"])
    return {"manifests": {k: len(v) for k, v in found.items()}, "total_dependencies": total,
            "unpinned": unpinned, "lockfiles": lockfiles,
            "unpinned_examples": [d["spec"] for v in found.values() for d in v if not d["pinned"]][:15]}


# ---------------------------------------------------------------- test inspector
def inspect_tests(repo: Path) -> dict:
    test_files, test_loc, code_loc = [], 0, 0
    for path in iter_files(repo):
        if path.suffix.lower() not in LANGS:
            continue
        n = count_lines(path)
        name = path.name.lower()
        parts = {p.lower() for p in path.relative_to(repo).parts}
        if name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.ts",
                                                      ".spec.js", "_test.go")) or parts & {"tests", "test", "__tests__"}:
            test_files.append(str(path.relative_to(repo)))
            test_loc += n
        else:
            code_loc += n
    ci = [p for p in (".github/workflows", ".gitlab-ci.yml", ".circleci", "azure-pipelines.yml", "Jenkinsfile")
          if (repo / p).exists()]
    configs = [p for p in ("pytest.ini", "tox.ini", "setup.cfg", ".coveragerc", "jest.config.js", "ruff.toml",
                           ".eslintrc.json", ".pre-commit-config.yaml", "mypy.ini") if (repo / p).exists()]
    return {"test_files": len(test_files), "test_loc": test_loc, "code_loc": code_loc,
            "test_to_code_ratio": round(test_loc / code_loc, 2) if code_loc else 0.0,
            "ci_configs": ci, "quality_configs": configs, "sample_tests": test_files[:8],
            "has_readme": any((repo / n).exists() for n in ("README.md", "README.rst", "README")),
            "has_license": any((repo / n).exists() for n in ("LICENSE", "LICENSE.md", "COPYING"))}
