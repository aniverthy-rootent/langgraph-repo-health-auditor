# Repo Health Auditor

**Framework:** LangGraph  
**Agents:** 6

Audits any local Git repository and writes a scored Markdown health report. Real tools read git history, scan code, parse dependency manifests and inspect tests; LLM agents interpret the facts.

> **Status:** this project was written as complete, runnable code but has **not been executed end to end against live LLM APIs** yet. It needs your own API key; expect to fix small issues from provider or library version changes. Issues and PRs welcome.

## Agents

| # | Agent | Role |
|---|---|---|
| 1 | Git Historian | commit cadence, authors, bus factor, churn hot-spots |
| 2 | Code Scanner | LOC by language, huge files, TODO markers, possible secrets |
| 3 | Dependency Auditor | manifests, unpinned versions, lockfiles |
| 4 | Test Inspector | test/code ratio, CI and quality configs |
| 5 | Risk Analyst | merges the four analyses into a typed risk register + score |
| 6 | Report Writer | final Markdown report with an action plan |

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env   # set LLM_SPEC and a key
python main.py /path/to/repo --out report.md
```

## How it works

Graph: four analysts run in parallel (fan-out), join at the Risk Analyst, then the Report Writer.

## Configuration

See `.env.example`. Models are chosen with one `LLM_SPEC` variable so you can switch provider without touching code.

## License

MIT
