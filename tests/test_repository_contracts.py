from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from scenara_model.dataset_versions import DatasetVersionReference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = ROOT.parent / "scenara-contracts"


def test_locked_repository_contract_release_and_consumer_examples() -> None:
    lock = yaml.safe_load((ROOT / "configs/contracts/repository-contracts.yml").read_text(encoding="utf-8"))
    assert lock["version"] == "1.2.0"
    manifest_path = CONTRACTS_ROOT / "contracts" / "repository" / "v1.2.0" / "manifest.json"
    content = manifest_path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == lock["manifest_sha256"]
    manifest = json.loads(content)
    contracts = {item["contract_id"]: item for item in manifest["contracts"]}
    assert set(lock["required_contracts"]) <= set(contracts)

    dataset_example = json.loads(
        (CONTRACTS_ROOT / contracts["dataset-version-input"]["example_path"]).read_text(encoding="utf-8")
    )
    reference = DatasetVersionReference.model_validate(dataset_example)
    assert reference.domain == "behavior"
    assert reference.annotation_schema_ids == ("scenara.behavior.action.v1",)

    package = json.loads(
        (CONTRACTS_ROOT / contracts["model-package-admission"]["example_path"]).read_text(encoding="utf-8")
    )
    assert package["artifact_format"] == "bundle"
    assert package["domain"] == "behavior"
    assert package["artifact_files"]
