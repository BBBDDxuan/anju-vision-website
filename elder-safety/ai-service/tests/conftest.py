"""Use fresh approved temporary paths; preserve all previous test runs."""
from pathlib import Path
import uuid

import pytest


def pytest_configure(config):
    var = Path(__file__).resolve().parents[1] / "var"
    requested = config.option.basetemp
    target = Path(requested).resolve() if requested else var / f"test-{uuid.uuid4().hex}" / "tmp"
    runtime = var.parent.parent / "runtime"
    relative = target.relative_to(var.resolve()) if target.is_relative_to(var.resolve()) else None
    module_target = bool(relative and relative.parts and relative.parts[0].startswith("test-"))
    shared_target = target.parent == runtime.resolve() and target.name.startswith("pytest-")
    if not (module_target or shared_target) or target.exists():
        raise pytest.UsageError("use a new, nonexistent ai-service/var/test-*/tmp or project runtime/pytest-* path")
    # Shared runtime is owned by the main test script. Do not create its
    # basetemp here: pytest creates the selected directory on first use.
    if module_target:
        target.parent.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(target)
