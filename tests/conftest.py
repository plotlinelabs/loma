"""Unit tests use explicit unisolated development mode, never deployment defaults.

Tests of the OCI boundary override this fixture to exercise runsc configuration.
Real sandbox execution is an opt-in integration test on an isolation-capable host.
"""
import pytest


@pytest.fixture(autouse=True)
def local_worker_development_mode(monkeypatch):
    monkeypatch.setenv('LOMA_ENV', 'development')
    monkeypatch.setenv('LOMA_WORKER_SANDBOX', 'development')
