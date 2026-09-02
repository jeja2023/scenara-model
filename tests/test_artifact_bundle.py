from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scenara_model.packaging.artifact_bundle import admission_payload, validate_artifact_bundle


def _write_bundle(
    root: Path,
    *,
    domain: str = "behavior",
    annotation_schema_id: str = "scenara.behavior.action.v1",
) -> str:
    root.mkdir()
    weight = root / "models" / "pptsm.pdparams"
    weight.parent.mkdir()
    weight.write_bytes(b"qualified-weight")
    card = root / "model-card.json"
    card.write_text(json.dumps({"model": domain}) + "\n", encoding="utf-8")
    files = []
    for path, media_type in ((weight, "application/octet-stream"), (card, "application/json")):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
                "media_type": media_type,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "model_id": f"scenara.{domain}.model",
        "version": "1.0.0",
        "domain": domain,
        "capability": f"{domain}_recognition",
        "adapter": "paddlevideo" if domain == "behavior" else "torchreid",
        "runtime_model_id": f"scenara.{domain}/model_v1",
        "artifact_format": "paddle",
        "files": files,
        "model_card_path": "model-card.json",
        "label_schema_ids": [annotation_schema_id],
    }
    (root / "bundle-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return hashlib.sha256((root / "bundle-manifest.json").read_bytes()).hexdigest()


def test_bundle_validation_and_admission_payload(workspace_tmp_path: Path) -> None:
    bundle = workspace_tmp_path / "bundle"
    digest = _write_bundle(bundle)
    validation = validate_artifact_bundle(bundle)
    assert validation.ok
    payload = admission_payload(
        validation,
        source_uri=f"oci://registry.example/behavior@sha256:{digest}",
        license_id="Apache-2.0",
        model_card_uri="https://artifacts.example/model-card.json#sha256=" + "a" * 64,
        evaluation_evidence=("https://artifacts.example/eval.json#sha256=" + "b" * 64,),
        vram_mb=6144,
        regression_samples=("behavior-regression-v1",),
    )
    assert payload["domain"] == "behavior"
    assert payload["artifact_format"] == "bundle"
    artifact_files = payload["artifact_files"]
    assert isinstance(artifact_files, list) and len(artifact_files) == 2


def test_bundle_validation_fails_on_digest_mismatch(workspace_tmp_path: Path) -> None:
    bundle = workspace_tmp_path / "bundle"
    _write_bundle(bundle)
    (bundle / "models" / "pptsm.pdparams").write_bytes(b"tampered")
    validation = validate_artifact_bundle(bundle)
    assert not validation.ok
    assert {issue.code for issue in validation.issues} >= {"bundle.size_mismatch", "bundle.digest_mismatch"}


def test_portrait_reid_bundle_requires_and_accepts_surveillance_review_schema(workspace_tmp_path: Path) -> None:
    bundle = workspace_tmp_path / "portrait-bundle"
    _write_bundle(
        bundle,
        domain="portrait",
        annotation_schema_id="scenara.portrait.surveillance-review.v1",
    )
    assert validate_artifact_bundle(bundle).ok
