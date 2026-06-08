import json

from sre_bridge.incident_writer import write_incident_artifacts


def test_write_incident_artifacts_creates_bob_handoff(tmp_path):
    incident_path = write_incident_artifacts(
        alert="time-series-query has read timeouts and socket growth",
        namespace="si",
        final_report="Runtime evidence points to read timeout growth.",
        model="gpt-5.5",
        provider="openai",
        config_path="config.yaml",
        incident_dir=str(tmp_path),
        service="time-series-query",
        target_repo="/path/to/work/repo",
    )

    assert incident_path.name.endswith("-time-series-query")
    assert sorted(path.name for path in incident_path.iterdir()) == [
        "bob-task.md",
        "dispatch.json",
        "report.json",
        "report.md",
        "validation-plan.md",
    ]

    bob_task = (incident_path / "bob-task.md").read_text(encoding="utf-8")
    assert "You are the only coding agent allowed to inspect or modify the target repository." in bob_task
    assert "The SRE agent has investigated runtime evidence only." in bob_task
    assert "Do not edit files in this phase." in bob_task
    assert "Stop after Phase 1 and wait for approval." in bob_task

    report = json.loads((incident_path / "report.json").read_text(encoding="utf-8"))
    assert report["alert"] == "time-series-query has read timeouts and socket growth"
    assert report["service"] == "time-series-query"
    assert report["artifact_files"] == [
        "report.md",
        "report.json",
        "bob-task.md",
        "validation-plan.md",
        "dispatch.json",
    ]

    dispatch = json.loads((incident_path / "dispatch.json").read_text(encoding="utf-8"))
    assert dispatch["status"] == "ready_for_bob"
    assert dispatch["target_repo_path"] == "/path/to/work/repo"
    assert dispatch["bob_task_file"] == str((incident_path / "bob-task.md").resolve())
    assert dispatch["report_file"] == str((incident_path / "report.md").resolve())
    assert dispatch["validation_plan_file"] == str((incident_path / "validation-plan.md").resolve())


def test_incident_slug_falls_back_to_alert_when_service_missing(tmp_path):
    incident_path = write_incident_artifacts(
        alert="HTTP 500s in API /checkout!",
        namespace="payments",
        final_report="No code action taken.",
        model="gemma3:12b",
        provider="ollama",
        config_path="config.yaml",
        incident_dir=str(tmp_path),
    )

    assert incident_path.name.endswith("-http-500s-in-api-checkout")
    dispatch = json.loads((incident_path / "dispatch.json").read_text(encoding="utf-8"))
    assert dispatch["target_repo_path"] is None
