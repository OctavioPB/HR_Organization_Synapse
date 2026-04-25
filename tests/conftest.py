import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: marks tests requiring external services (Kafka + Postgres via Docker). "
        "Run with: pytest -m integration",
    )
