"""DAG: neo4j_import_dag — import graph snapshot into Neo4j.

Triggered automatically by graph_builder_dag after each daily graph build.
Can also be triggered manually from the Airflow UI.

Task chain:
    ensure_indexes
        → import_graph
            → verify_import
"""

import logging
import os
from datetime import timedelta

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "email_on_failure": False,
}


@dag(
    dag_id="neo4j_import_dag",
    description="Import daily graph snapshot into Neo4j for path and reachability queries",
    schedule=None,  # triggered externally by graph_builder_dag
    start_date=days_ago(1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["org-synapse", "neo4j", "graph"],
)
def neo4j_import_dag():

    @task()
    def ensure_indexes() -> dict:
        from etl.tasks.neo4j_import import task_ensure_indexes as _ensure
        return _ensure()

    @task()
    def import_graph(**context) -> dict:
        from etl.tasks.neo4j_import import task_import_graph as _import
        return _import(context["ds"], window_days=_WINDOW_DAYS)

    @task()
    def verify_import(**context) -> dict:
        from etl.tasks.neo4j_import import task_verify_import as _verify
        return _verify(context["ds"])

    idx = ensure_indexes()
    imported = import_graph()
    verified = verify_import()

    idx >> imported >> verified


neo4j_import_dag()
