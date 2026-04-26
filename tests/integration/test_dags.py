"""Structural validation tests for Airflow DAGs.

These tests import the DAG modules and verify structure without a running
Airflow instance or database.  They require the 'airflow' package to be
installed; if it is not available they are skipped automatically.

Run:
    pytest tests/integration/test_dags.py -v
"""

import pytest

airflow = pytest.importorskip("airflow", reason="apache-airflow not installed")


# Import DAG modules after we know airflow is available.
from etl.dags import anomaly_detection_dag as _ad_module  # noqa: E402
from etl.dags import graph_builder_dag as _gb_module  # noqa: E402
from etl.dags import risk_scoring_dag as _rs_module  # noqa: E402


# ─── graph_builder_dag ────────────────────────────────────────────────────────


def test_graph_builder_dag_id():
    dag = _gb_module.graph_builder_dag.airflow_dag
    assert dag.dag_id == "graph_builder_dag"


def test_graph_builder_dag_has_six_tasks():
    dag = _gb_module.graph_builder_dag.airflow_dag
    assert len(dag.tasks) == 6, f"Expected 6 tasks, got {len(dag.tasks)}: {[t.task_id for t in dag.tasks]}"


def test_graph_builder_dag_task_ids():
    dag = _gb_module.graph_builder_dag.airflow_dag
    task_ids = {t.task_id for t in dag.tasks}
    expected = {
        "check_raw_events",
        "build_graph",
        "compute_metrics",
        "detect_silos",
        "score_risks",
        "flag_spof_critical",
    }
    assert expected == task_ids, f"Missing tasks: {expected - task_ids}"


def test_graph_builder_dag_schedule():
    dag = _gb_module.graph_builder_dag.airflow_dag
    # Airflow 2.9 stores schedule as a string or timetable
    schedule = getattr(dag, "schedule_interval", None) or str(getattr(dag, "timetable", ""))
    assert "0 2 * * *" in str(schedule), f"Unexpected schedule: {schedule}"


# ─── anomaly_detection_dag ────────────────────────────────────────────────────


def test_anomaly_detection_dag_id():
    dag = _ad_module.anomaly_detection_dag.airflow_dag
    assert dag.dag_id == "anomaly_detection_dag"


def test_anomaly_detection_dag_has_four_tasks():
    """3 ML tasks + 1 TriggerDagRunOperator."""
    dag = _ad_module.anomaly_detection_dag.airflow_dag
    assert len(dag.tasks) == 4, f"Expected 4 tasks, got {len(dag.tasks)}: {[t.task_id for t in dag.tasks]}"


def test_anomaly_detection_dag_includes_trigger():
    dag = _ad_module.anomaly_detection_dag.airflow_dag
    task_ids = {t.task_id for t in dag.tasks}
    assert "trigger_risk_scoring" in task_ids


def test_anomaly_detection_dag_schedule():
    dag = _ad_module.anomaly_detection_dag.airflow_dag
    schedule = getattr(dag, "schedule_interval", None) or str(getattr(dag, "timetable", ""))
    assert "0 3 * * 1" in str(schedule), f"Unexpected schedule: {schedule}"


# ─── risk_scoring_dag ─────────────────────────────────────────────────────────


def test_risk_scoring_dag_id():
    dag = _rs_module.risk_scoring_dag.airflow_dag
    assert dag.dag_id == "risk_scoring_dag"


def test_risk_scoring_dag_has_three_tasks():
    dag = _rs_module.risk_scoring_dag.airflow_dag
    assert len(dag.tasks) == 3, f"Expected 3 tasks, got {len(dag.tasks)}: {[t.task_id for t in dag.tasks]}"


def test_risk_scoring_dag_schedule_is_none():
    dag = _rs_module.risk_scoring_dag.airflow_dag
    # Schedule=None means it is externally triggered
    schedule = getattr(dag, "schedule_interval", None)
    assert schedule is None, f"Expected schedule=None, got {schedule!r}"


def test_risk_scoring_dag_task_ids():
    dag = _rs_module.risk_scoring_dag.airflow_dag
    task_ids = {t.task_id for t in dag.tasks}
    expected = {"resolve_snapshot_date", "score_risks", "flag_spof_critical"}
    assert expected == task_ids, f"Missing tasks: {expected - task_ids}"
