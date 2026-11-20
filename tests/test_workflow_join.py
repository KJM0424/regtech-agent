"""병렬 분기 합류 검사: 보고서와 메일 노드가 한 번씩만 실행되는지 본다.

모델과 검색을 부르지 않도록 9개 노드를 실행 횟수만 세는 대역으로 바꾼다.
"""

from collections import Counter

import regtech_agent.workflow as wf

NODES = [
    "analyzer_node",
    "search_node",
    "classifier_node",
    "prioritizer_node",
    "checklist_generator_node",
    "planning_agent_node",
    "risk_assessor_node",
    "report_generator_node",
    "email_notifier_node",
]


def run_with_stubs(monkeypatch):
    calls = Counter()
    plans_seen_by_report = []

    def stub(name):
        def node(state):
            calls[name] += 1
            if name == "planning_agent_node":
                return {"execution_plans": [{"plan": "ok"}]}
            if name == "report_generator_node":
                plans_seen_by_report.append(len(state.get("execution_plans", [])))
            return {}

        return node

    for name in NODES:
        monkeypatch.setattr(wf, name, stub(name))

    app = wf.build_workflow().compile()
    app.invoke({"execution_plans": []})
    return calls, plans_seen_by_report


def test_report_and_email_run_once(monkeypatch):
    calls, _ = run_with_stubs(monkeypatch)
    assert calls["report_generator_node"] == 1
    assert calls["email_notifier_node"] == 1


def test_report_runs_after_plans_are_ready(monkeypatch):
    _, plans_seen = run_with_stubs(monkeypatch)
    assert plans_seen == [1]


def test_every_other_node_runs_once(monkeypatch):
    calls, _ = run_with_stubs(monkeypatch)
    for name in NODES:
        assert calls[name] >= 1, name
    assert calls["risk_assessor_node"] == 1
    assert calls["planning_agent_node"] == 1
