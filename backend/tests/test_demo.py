"""The committed demo sample, its trained models and the golden-set CI gate (feat-018, 022)."""

import json
from pathlib import Path

import pytest

from issue_triage.cli import main
from issue_triage.infrastructure.docs_source import local_docs
from issue_triage.infrastructure.jsonl_store import JsonlIssueStore
from issue_triage.services.decision_model import EmbeddingClassifier
from issue_triage.services.routing import NeedsInfoModel, RoutingPolicy
from issue_triage.wiring import model_path

from .helpers import IsolatedSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO = REPO_ROOT / "samples" / "demo"
REPO = "astral-sh/uv"


def test_demo_sample_is_complete_and_scrubbed() -> None:
    issues = JsonlIssueStore(DEMO).read_all("astral-sh/uv")
    golden = json.loads((DEMO / "golden.json").read_text(encoding="utf-8"))
    raw = (DEMO / "github" / "astral-sh__uv" / "issues.jsonl").read_text(encoding="utf-8")

    assert len(issues) == 400
    assert set(golden) <= {i.number for i in issues}
    assert all(i.author.startswith("user-") or i.author == "ghost" for i in issues)
    assert "@gmail.com" not in raw and "ghp_" not in raw


def test_local_docs_read_the_committed_snapshot_without_the_notice() -> None:
    pages = local_docs(DEMO / "docs", "astral-sh/uv")

    assert len(pages) == 72
    assert "concepts/cache" in {p.key for p in pages}
    assert all(p.path != "NOTICE.md" for p in pages)


def test_demo_ships_the_evaluated_models_where_the_image_loads_them() -> None:
    settings = IsolatedSettings(data_dir=Path("/app/data"))
    models = DEMO / "models"
    vector = [[1.0 / 384**0.5] * 384]  # the embedding model's dimension (D-008)

    classifier = EmbeddingClassifier.load(models / model_path(settings, REPO, "type.json").name)
    needs_info = NeedsInfoModel.load(models / model_path(settings, REPO, "needs-info.json").name)
    policy = RoutingPolicy.load(models / model_path(settings, REPO, "routing.json").name)
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert classifier is not None and needs_info is not None and policy is not None
    assert sorted(classifier.classes) == ["bug", "enhancement", "other", "question"]
    assert 0.0 <= needs_info.probability(vector)[0] <= 1.0
    assert policy.needs_info_threshold is not None  # the calibrated policy (D-015)
    assert "ISSUE_TRIAGE_DATA_DIR=/app/data" in dockerfile
    image_models = model_path(settings, REPO, "x").parent.as_posix()
    assert f"COPY samples/demo/models {image_models}" in dockerfile


def golden_settings(tmp_path: Path) -> IsolatedSettings:
    evals = tmp_path / "evals"
    (evals / "datasets" / "golden").mkdir(parents=True)
    (evals / "results").mkdir()
    source = REPO_ROOT / "evals" / "datasets" / "golden" / "astral-sh__uv-golden.jsonl"
    (evals / "datasets" / "golden" / source.name).write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return IsolatedSettings(evals_dir=evals)


def test_golden_eval_writes_then_checks_a_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = golden_settings(tmp_path)
    baseline = settings.evals_dir / "results" / "golden-baseline-astral-sh__uv.json"

    assert main(["eval-golden"], settings) == 0
    first = json.loads(baseline.read_text())
    assert main(["eval-golden", "--check"], settings) == 0
    assert "ok" in capsys.readouterr().out

    baseline.write_text(json.dumps({**first, "macro_f1": first["macro_f1"] + 0.1}))
    assert main(["eval-golden", "--check"], settings) == 1  # a 0.1 drop fails CI
