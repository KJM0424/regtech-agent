"""RegTech 에이전트 실행 측정.

이 저장소의 에이전트 코드를 그대로 불러와 실제 모델과 검색을 호출하고,
노드별 실행 횟수·시간, 모델 호출 수와 토큰, 규제 건수, 출처 연결 비율을 잰다.

--join old : 수정 전 합류 방식 (엣지를 따로 두 개)
--join new : 수정 후 합류 방식 (두 갈래가 끝난 뒤 한 번)

사용:
    python scripts/measure.py --join old --run 1
"""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

import regtech_agent.workflow as wf  # noqa: E402
from regtech_agent.models import AgentState  # noqa: E402

NODES = {
    "analyzer": "analyzer_node",
    "searcher": "search_node",
    "classifier": "classifier_node",
    "prioritizer": "prioritizer_node",
    "checklist_generator": "checklist_generator_node",
    "risk_assessor": "risk_assessor_node",
    "planning_agent": "planning_agent_node",
    "report_generator": "report_generator_node",
    "email_notifier": "email_notifier_node",
}

current_node = contextvars.ContextVar("current_node", default="?")
lock = threading.Lock()
node_runs = Counter()
node_seconds = defaultdict(float)
llm_calls = Counter()
tokens = Counter()
report_inputs = []


def wrap_node(name, fn):
    def node(state):
        token = current_node.set(name)
        t0 = time.time()
        if name == "report_generator":
            with lock:
                report_inputs.append({
                    "execution_plans": len(state.get("execution_plans") or []),
                    "risk_items": len((state.get("risk_assessment") or {}).get("risk_items") or []),
                    "risk_total": (state.get("risk_assessment") or {}).get("total_risk_score"),
                })
        try:
            return fn(state)
        finally:
            with lock:
                node_runs[name] += 1
                node_seconds[name] += time.time() - t0
            current_node.reset(token)

    return node


_orig_invoke = ChatOpenAI.invoke


def counting_invoke(self, *args, **kwargs):
    out = _orig_invoke(self, *args, **kwargs)
    usage = getattr(out, "usage_metadata", None) or {}
    with lock:
        llm_calls[current_node.get()] += 1
        tokens["input"] += usage.get("input_tokens", 0)
        tokens["output"] += usage.get("output_tokens", 0)
    return out


ChatOpenAI.invoke = counting_invoke


def build(join: str) -> StateGraph:
    g = StateGraph(AgentState)
    for node, attr in NODES.items():
        g.add_node(node, wrap_node(node, getattr(wf, attr)))
    g.add_edge(START, "analyzer")
    g.add_edge("analyzer", "searcher")
    g.add_edge("searcher", "classifier")
    g.add_edge("classifier", "prioritizer")
    g.add_edge("prioritizer", "checklist_generator")
    g.add_edge("prioritizer", "risk_assessor")
    g.add_edge("checklist_generator", "planning_agent")
    if join == "old":
        g.add_edge("planning_agent", "report_generator")
        g.add_edge("risk_assessor", "report_generator")
    else:
        g.add_edge(["planning_agent", "risk_assessor"], "report_generator")
    g.add_edge("report_generator", "email_notifier")
    g.add_edge("email_notifier", END)
    return g


BUSINESS = {  # run_regtech_agent.py의 샘플 입력 그대로
    "industry": "배터리 제조",
    "product_name": "리튬이온 배터리",
    "raw_materials": "리튬, 코발트, 니켈, 흑연",
    "processes": ["원자재 혼합", "전극 제조", "셀 조립", "충방전 테스트"],
    "employee_count": 50,
    "sales_channels": ["B2B", "온라인"],
    "export_countries": ["미국", "일본"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--join", choices=["old", "new"], required=True)
    ap.add_argument("--run", type=int, default=1)
    a = ap.parse_args()

    out_dir = Path(__file__).resolve().parent / "runs" / f"{a.join}_{a.run}"
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(out_dir)  # 보고서(report/)가 측정 폴더 안에 생기게 한다

    initial = {
        "business_info": BUSINESS, "keywords": [], "search_results": [], "regulations": [],
        "final_output": {}, "checklists": [], "execution_plans": [],
        "risk_assessment": {"total_risk_score": 0.0, "high_risk_items": [], "risk_matrix": {}, "recommendations": []},
        "final_report": {"executive_summary": "", "key_insights": [], "action_items": [], "risk_highlights": [],
                         "next_steps": [], "full_markdown": "", "report_pdf_path": "", "citations": []},
        "email_status": {"success": False, "recipient": "", "error": "이메일 발송 전", "attachments": [], "attempted": False},
        "email_recipient": None,
    }
    t0 = time.time()
    final = build(a.join).compile().invoke(initial)
    total = time.time() - t0

    search_ids = {r.get("source_id") for r in final.get("search_results", [])}
    regs = final.get("regulations", [])
    with_source = [r for r in regs if r.get("sources")]
    linked = [r for r in regs if any(s.get("source_id") in search_ids and s.get("url") for s in r.get("sources", []))]
    unknown_ids = sum(1 for r in regs for s in r.get("sources", []) if s.get("source_id") not in search_ids)

    result = {
        "join": a.join, "run": a.run, "total_seconds": round(total, 1),
        "node_runs": dict(node_runs),
        "node_seconds": {k: round(v, 1) for k, v in node_seconds.items()},
        "llm_calls_by_node": dict(llm_calls), "llm_calls_total": sum(llm_calls.values()),
        "tokens": dict(tokens),
        "report_inputs_at_each_run": report_inputs,
        "search_results": len(final.get("search_results", [])),
        "regulations": len(regs),
        "regulations_with_any_source": len(with_source),
        "regulations_linked_to_a_search_result": len(linked),
        "source_ids_not_in_search_results": unknown_ids,
        "checklists": len(final.get("checklists", [])),
        "execution_plans": len(final.get("execution_plans", [])),
        "risk_total_score": (final.get("risk_assessment") or {}).get("total_risk_score"),
        "report_pdf": (final.get("final_report") or {}).get("report_pdf_path"),
        "report_citations": len((final.get("final_report") or {}).get("citations") or []),
    }
    (out_dir / "measure.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "final_state.json").write_text(json.dumps(final, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n===== 측정 결과 =====")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
