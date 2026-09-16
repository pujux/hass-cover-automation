"""Test session configuration: enable loading custom integrations."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable the custom_components/ directory for every test."""
    return
