"""The layer rules from docs/ARCHITECTURE.md, enforced on the import graph."""

import ast
import importlib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "issue_triage"
LAYERS = ["api", "services", "domain", "infrastructure"]

# Layer -> layers it must never import.
FORBIDDEN = {
    "domain": {"api", "services", "infrastructure"},
    "services": {"api"},
    "infrastructure": {"api", "services"},
}


def imported_layers(path: Path, package: Path = PACKAGE) -> set[str]:
    """Return the issue_triage layers a module imports, absolute or relative."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package_parts = path.relative_to(package.parent).parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package_parts[: len(package_parts) - node.level + 1])
                names = [".".join([*base, node.module] if node.module else base)]
            elif node.module:
                names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "issue_triage" and len(parts) > 1 and parts[1] in LAYERS:
                found.add(parts[1])
    return found


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_packages_exist(layer: str) -> None:
    assert importlib.import_module(f"issue_triage.{layer}").__doc__


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_higher_layers(layer: str) -> None:
    violations = {
        str(path.relative_to(PACKAGE)): bad
        for path in (PACKAGE / layer).rglob("*.py")
        if (bad := imported_layers(path) & FORBIDDEN[layer])
    }

    assert violations == {}


def test_import_scanner_detects_absolute_and_relative_imports(tmp_path: Path) -> None:
    package = tmp_path / "issue_triage"
    module = package / "domain" / "probe.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "import issue_triage.api\nfrom ..services import x\nfrom . import y\n", encoding="utf-8"
    )

    assert imported_layers(module, package) == {"api", "services", "domain"}
