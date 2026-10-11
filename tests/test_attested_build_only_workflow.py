"""Run the shipped publisher's transfer checks, without network or publication."""
import hashlib
import json
from pathlib import Path
import subprocess
import textwrap
import zipfile

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/auto-release.yml"


def _workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def _transfer_script():
    step = _workflow().split("- name: Download and verify exact attested candidate", 1)[1]
    python = step.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(python)


def _candidate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    archive = dist / "Blender-PDF-Importer_v1.0.108.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("pdf_vector_importer/__init__.py", "# neutral transfer fixture\n")
    env = {
        "GITHUB_REPOSITORY": "BlueCollar-Systems/PDF-Importer-Blender",
        "BUILD_REF": "a" * 40,
        "GITHUB_SHA": "b" * 40,
        "GITHUB_WORKFLOW_REF": "BlueCollar-Systems/PDF-Importer-Blender/.github/workflows/auto-release.yml@refs/heads/main",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1",
        "CANDIDATE_ATTEMPT": "1",
        "RELEASE_VERSION": "1.0.108",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: env["BUILD_REF"] + "\n")
    record = {
        "schema": "bcs.blender_release_candidate/1.0",
        "repository": env["GITHUB_REPOSITORY"],
        "source_sha": env["BUILD_REF"],
        "workflow_sha": env["GITHUB_SHA"],
        "workflow_ref": env["GITHUB_WORKFLOW_REF"],
        "run_id": 12345,
        "run_attempt": 1,
        "version": env["RELEASE_VERSION"],
        "artifact": {
            "name": archive.name,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size_bytes": archive.stat().st_size,
        },
    }
    path = dist / "release-candidate-provenance.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path, archive, record


def test_false_dispatch_cannot_reach_mutating_job():
    workflow = _workflow()
    trigger = workflow.split("on:", 1)[1].split("env:", 1)[0]
    assert "publish_release:" in trigger
    assert "type: boolean" in trigger and "default: false" in trigger
    builder, publisher = workflow.split("  publish:\n", 1)
    assert "if: github.event_name == 'workflow_dispatch' && inputs.publish_release == true && needs.release.result == 'success'" in publisher
    assert "contents: write" not in builder
    assert "contents: read" in builder and "attestations: write" in builder
    for mutation in ("git tag", "git push", "gh release create", "gh release upload",
                     "gh release edit", "audit-existing-tag", "POST /repos/{owner}/{repo}/dispatches"):
        assert mutation not in builder
        assert mutation in publisher
    assert "uses: actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d" in builder
    assert "name: ${{ steps.candidate.outputs.artifact_name }}" in builder
    assert publisher.index("gh attestation verify") < publisher.index("gh release create")
    for flag in ("--signer-workflow", "--signer-digest", "--source-digest",
                 "--source-ref", "--deny-self-hosted-runners"):
        assert flag in publisher


def test_shipped_transfer_check_accepts_unchanged_bound_zip(tmp_path, monkeypatch):
    _candidate(tmp_path, monkeypatch)
    exec(compile(_transfer_script(), str(WORKFLOW), "exec"), {})


def test_shipped_record_emits_signed_producer_identity(tmp_path, monkeypatch):
    path, _archive, _record = _candidate(tmp_path, monkeypatch)
    output = tmp_path / "step-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    step = _workflow().split("- name: Record exact release candidate", 1)[1]
    script = step.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    exec(compile(textwrap.dedent(script), str(WORKFLOW), "exec"), {})
    assert json.loads(path.read_text(encoding="utf-8"))["run_attempt"] == 1
    assert output.read_text(encoding="utf-8").splitlines() == [
        "run_attempt=1", "artifact_name=attested-release-candidate-12345-1",
    ]


def test_publisher_only_retry_uses_the_original_producer_attempt(tmp_path, monkeypatch):
    _candidate(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    exec(compile(_transfer_script(), str(WORKFLOW), "exec"), {})
    workflow = _workflow()
    assert 'candidate_artifact: ${{ steps.candidate.outputs.artifact_name }}' in workflow
    assert 'candidate_run_attempt: ${{ steps.candidate.outputs.run_attempt }}' in workflow
    publisher = workflow.split("  publish:\n", 1)[1]
    assert '--name "$CANDIDATE_ARTIFACT" --dir dist' in publisher
    assert '"attested-release-candidate-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"' not in publisher


@pytest.mark.parametrize("change", ["source", "workflow", "run", "version", "bytes", "size_type", "extra_key", "duplicate_key", "nan"])
def test_shipped_transfer_check_rejects_corruption(tmp_path, monkeypatch, change):
    path, archive, record = _candidate(tmp_path, monkeypatch)
    if change == "source":
        record["source_sha"] = "c" * 40
    elif change == "workflow":
        record["workflow_sha"] = "c" * 40
    elif change == "run":
        record["run_attempt"] = True
    elif change == "version":
        record["version"] = "1.0.107"
    elif change == "bytes":
        archive.write_bytes(archive.read_bytes() + b"changed")
    elif change == "size_type":
        record["artifact"]["size_bytes"] = float(record["artifact"]["size_bytes"])
    elif change == "extra_key":
        record["artifact"]["unsafe"] = True
    path.write_text(json.dumps(record), encoding="utf-8")
    if change == "duplicate_key":
        path.write_text(path.read_text(encoding="utf-8").replace('"run_attempt": 1', '"run_attempt": 1, "run_attempt": 1'), encoding="utf-8")
    elif change == "nan":
        path.write_text(path.read_text(encoding="utf-8").replace('"run_attempt": 1', '"run_attempt": NaN'), encoding="utf-8")
    with pytest.raises((SystemExit, ValueError)):
        exec(compile(_transfer_script(), str(WORKFLOW), "exec"), {})
