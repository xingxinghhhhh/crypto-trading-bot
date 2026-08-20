import hashlib
import sys
import time
from pathlib import Path

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection as projection_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_materialization as module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_verification as verification_module
import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next((ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json"))
ROLLOVER = next((ROOT / "reports/prospective-epoch-closeout-rollover").glob("*.json"))
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
BUNDLE_ADMISSION_CONFIG = ROOT / bundle_admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / manifest_module.DEFAULT_CONFIG_FILENAME
DELIVERY_CONFIG = ROOT / delivery_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission_module.DEFAULT_CONFIG_FILENAME


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    # Keep the Windows temporary path short enough for the existing artifact
    # names used by the upstream handoff builders.
    root = tmp_path_factory.mktemp("cpm")
    bundle = bundle_module.build_prospective_evidence_operations_bundle(
        SNAPSHOT, BUNDLE_CONFIG, root / "bundle"
    )
    bundle_admission = bundle_admission_module.freeze_prospective_evidence_operations_bundle_admission(
        bundle.export_paths["report"], SNAPSHOT, BUNDLE_ADMISSION_CONFIG, root / "bundle-admission"
    )
    manifest = manifest_module.build_prospective_evidence_operations_handoff_manifest(
        bundle_admission.export_paths["report"],
        bundle.export_paths["report"],
        MANIFEST_CONFIG,
        root / "manifest",
    )
    delivery = delivery_module.build_prospective_evidence_operations_handoff_delivery(
        manifest.export_paths["report"],
        bundle_admission.export_paths["report"],
        bundle.export_paths["report"],
        DELIVERY_CONFIG,
        root / "delivery",
    )
    admission = admission_module.freeze_prospective_evidence_operations_handoff_delivery_admission(
        delivery.export_paths["report"], SNAPSHOT, ADMISSION_CONFIG, root / "delivery-admission"
    )
    return {
        "root": root,
        "delivery": Path(delivery.export_paths["report"]),
        "admission": Path(admission.export_paths["report"]),
    }


def _args(inputs: dict[str, Path]) -> tuple[Path, Path, Path, Path]:
    return inputs["admission"], inputs["delivery"], SNAPSHOT, ROLLOVER


def _output(monkeypatch: pytest.MonkeyPatch, inputs: dict[str, Path], name: str = "artifacts/projection") -> Path:
    repo = inputs["root"]
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: repo)
    path = repo / name
    return path


def test_materialize_writes_one_canonical_content_addressed_json(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _output(monkeypatch, inputs)
    result = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), output
    )
    path = Path(result.projection_path)
    expected = projection_module.format_governance_fresh_current_operations_handoff_projection(
        result.projection
    ).encode("utf-8")
    assert list(output.iterdir()) == [path]
    assert path.name == f"{module.ARTIFACT_PREFIX}.{result.projection_sha256}.json"
    assert path.read_bytes() == expected
    assert not path.read_bytes().endswith(b"\n")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result.projection_sha256


def test_materialized_projection_is_accepted_by_existing_verifier(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _output(monkeypatch, inputs)
    result = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), output
    )
    assert verification_module.verify_governance_fresh_current_operations_handoff_projection(
        result.projection_path, *_args(inputs)
    ) == result.projection


def test_repeated_materialize_is_idempotent_and_does_not_rewrite(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _output(monkeypatch, inputs)
    first = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), output
    )
    path = Path(first.projection_path)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    time.sleep(0.01)
    second = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), output
    )
    assert second == first
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert len(list(output.iterdir())) == 1


def test_same_projection_in_two_allowed_dirs_has_same_identity(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    first_output = _output(monkeypatch, inputs, "artifacts/one")
    first = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), first_output
    )
    second_output = _output(monkeypatch, inputs, "artifacts/two")
    second = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), second_output
    )
    assert first.projection_sha256 == second.projection_sha256
    assert Path(first.projection_path).read_bytes() == Path(second.projection_path).read_bytes()
    assert Path(first.projection_path).parent != Path(second.projection_path).parent


def test_existing_same_name_different_bytes_fails_without_overwrite(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _output(monkeypatch, inputs)
    first = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), output
    )
    path = Path(first.projection_path)
    original = path.read_bytes()
    path.write_bytes(original + b"tampered")
    with pytest.raises(MarketDataError, match="content collision"):
        module.materialize_governance_fresh_current_operations_handoff_projection(
            *_args(inputs), output
        )
    assert path.read_bytes() == original + b"tampered"


@pytest.mark.parametrize("bad_output", ["reports/projection", "artifacts/../reports/projection"])
def test_reports_and_path_escape_are_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, bad_output: str
) -> None:
    output = _output(monkeypatch, inputs, bad_output)
    with pytest.raises(MarketDataError, match="output path escape"):
        module.materialize_governance_fresh_current_operations_handoff_projection(
            *_args(inputs), output
        )
    assert not output.exists()


def test_absolute_outside_artifacts_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _output(monkeypatch, inputs)
    outside = tmp_path / "outside"
    with pytest.raises(MarketDataError, match="output path escape"):
        module.materialize_governance_fresh_current_operations_handoff_projection(
            *_args(inputs), outside
        )
    assert not outside.exists()


def test_upstream_failure_happens_before_output_directory_creation(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _output(monkeypatch, inputs, "artifacts/not-created")

    def fail_projector(*_args: object) -> object:
        raise MarketDataError("superseded rollover")

    monkeypatch.setattr(module, "project_governance_fresh_current_operations_handoff", fail_projector)
    with pytest.raises(MarketDataError, match="superseded rollover"):
        module.materialize_governance_fresh_current_operations_handoff_projection(
            *_args(inputs), output
        )
    assert not output.exists()


def test_materializer_only_delegates_projection_and_formatter(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _output(monkeypatch, inputs)
    projection = {"projection": "value"}  # type: ignore[dict-item]
    calls: list[str] = []

    def fake_projector(*_args: object) -> dict[str, projection_module.JSONScalar]:
        calls.append("project")
        return projection

    def fake_formatter(value: object) -> str:
        calls.append("format")
        assert value is projection
        return '{"projection":"value"}'

    monkeypatch.setattr(module, "project_governance_fresh_current_operations_handoff", fake_projector)
    monkeypatch.setattr(module, "format_governance_fresh_current_operations_handoff_projection", fake_formatter)
    result = module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), output
    )
    assert calls == ["project", "format"]
    assert result.projection == projection


def test_materializer_does_not_discover_latest_or_write_business_tree(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _output(monkeypatch, inputs, "artifacts/no-business-tree")
    before = sorted(str(path.relative_to(inputs["root"])) for path in inputs["root"].rglob("*"))
    module.materialize_governance_fresh_current_operations_handoff_projection(
        *_args(inputs), output
    )
    after = sorted(str(path.relative_to(inputs["root"])) for path in inputs["root"].rglob("*"))
    assert all(not item.startswith("reports/") for item in after)
    new_items = {item.replace("\\", "/") for item in set(after) - set(before)}
    assert new_items == {
        "artifacts/no-business-tree",
        "artifacts/no-business-tree/"
        + next(path.name for path in output.iterdir()),
    }


def _cli_args(output: Path, inputs: dict[str, Path]) -> list[str]:
    return [
        "crypto-bot",
        "materialize-verified-current-operations-handoff-projection",
        "--delivery-admission",
        str(inputs["admission"]),
        "--handoff-delivery",
        str(inputs["delivery"]),
        "--current-operations-snapshot",
        str(SNAPSHOT),
        "--epoch-closeout-rollover",
        str(ROLLOVER),
        "--output-dir",
        str(output),
    ]


def test_cli_happy_path_reports_sha_and_exported_path(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(monkeypatch, inputs, "artifacts/cli")
    monkeypatch.setattr(sys, "argv", _cli_args(output, inputs))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.err == ""
    assert "consumer_projection_sha256: " in captured.out
    assert "exported_consumer_projection: " in captured.out
    path = Path(captured.out.split("exported_consumer_projection: ", 1)[1].strip())
    assert path.is_file()


def test_cli_missing_required_argument_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["crypto-bot", "materialize-verified-current-operations-handoff-projection"]
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "required" in captured.err


def test_cli_failure_has_empty_stdout(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _output(monkeypatch, inputs, "reports/cli-failure")
    monkeypatch.setattr(sys, "argv", _cli_args(output, inputs))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "materialization_failed" in captured.err
