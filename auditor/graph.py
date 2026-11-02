"""The six-agent LangGraph: four parallel analysts -> risk analyst -> report writer."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import tools


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class State(TypedDict, total=False):
    repo: str
    facts: Annotated[dict, _merge]        # raw tool output per agent
    findings: Annotated[dict, _merge]     # each analyst's written assessment
    risks: list[dict]
    score: int
    report: str


class Risk(BaseModel):
    title: str
    severity: Literal["low", "medium", "high", "critical"]
    evidence: str = Field(description="Concrete numbers or file names from the findings")
    recommendation: str


class RiskRegister(BaseModel):
    risks: list[Risk]


def llm():
    spec = os.environ.get("LLM_SPEC", "openai:gpt-4o-mini")  # "<provider>:<model>"
    provider, _, model = spec.partition(":")
    return init_chat_model(model, model_provider=provider, temperature=0.2)


def _text(msg) -> str:
    c = msg.content
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c if isinstance(b, dict))


def analyst(name: str, role: str, tool_fn):
    """An analyst agent = a real tool call + an LLM that interprets the facts."""
    system = (f"You are the {role} on a repository health audit team. You receive hard facts gathered by "
              "tools. Write 4-8 concise bullet points: what is healthy, what is concerning, and why. "
              "Quote specific numbers and file names. Do not invent facts that are not in the data.")

    def node(state: State) -> dict:
        facts = tool_fn(Path(state["repo"]))
        reply = llm().invoke([("system", system), ("human", json.dumps(facts, indent=2, default=str))])
        return {"facts": {name: facts}, "findings": {name: _text(reply)}}

    return node


def risk_analyst(state: State) -> dict:
    structured = llm().with_structured_output(RiskRegister)
    prompt = ("You are the Risk Analyst. Combine the four analyst assessments below into a prioritised risk "
              "register (max 10 risks). Severity must reflect real impact; cite evidence from the findings.\n\n"
              + "\n\n".join(f"## {k}\n{v}" for k, v in state["findings"].items()))
    register: RiskRegister = structured.invoke(prompt)
    weights = {"low": 3, "medium": 8, "high": 15, "critical": 25}
    score = max(0, 100 - sum(weights[r.severity] for r in register.risks))
    return {"risks": [r.model_dump() for r in register.risks], "score": score}


def report_writer(state: State) -> dict:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    risks = sorted(state["risks"], key=lambda r: order[r["severity"]])
    prompt = (f"You are the Report Writer. Write a Markdown repository health report for '{state['repo']}'.\n"
              f"Health score: {state['score']}/100 (computed from the risk register; do not change it).\n"
              "Sections: Executive Summary, Scorecard (table of the four areas), Top Risks (table: severity, "
              "title, evidence, recommendation), 30-day Action Plan. Use only the data provided.\n\n"
              f"Analyst findings:\n{json.dumps(state['findings'], indent=2)}\n\n"
              f"Risk register:\n{json.dumps(risks, indent=2)}")
    return {"report": _text(llm().invoke(prompt))}


def build_graph():
    g = StateGraph(State)
    g.add_node("git_historian", analyst("git_historian", "Git Historian", tools.git_stats))
    g.add_node("code_scanner", analyst("code_scanner", "Code Scanner", tools.scan_code))
    g.add_node("dependency_auditor", analyst("dependency_auditor", "Dependency Auditor", tools.audit_dependencies))
    g.add_node("test_inspector", analyst("test_inspector", "Test Inspector", tools.inspect_tests))
    g.add_node("risk_analyst", risk_analyst)
    g.add_node("report_writer", report_writer)
    analysts = ["git_historian", "code_scanner", "dependency_auditor", "test_inspector"]
    for a in analysts:
        g.add_edge(START, a)           # fan-out: the four analysts run in parallel
    g.add_edge(analysts, "risk_analyst")  # fan-in: wait for all four
    g.add_edge("risk_analyst", "report_writer")
    g.add_edge("report_writer", END)
    return g.compile()
