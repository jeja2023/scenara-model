from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PRECISIONS = {"fp32", "fp16", "int8"}
SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ARTIFACT_PATTERN = re.compile(
    r"^(?P<family>[a-z0-9][a-z0-9_]*?)_v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))_(?P<precision>fp32|fp16|int8)\.onnx$"
)


@dataclass(frozen=True)
class ArtifactName:
    filename: str
    family: str
    version: str
    precision: str

    @property
    def stem(self) -> str:
        return self.filename.removesuffix(".onnx")


def is_semver(value: str) -> bool:
    return bool(SEMVER_PATTERN.match(value))


def parse_artifact_name(value: str | Path) -> ArtifactName:
    filename = Path(value).name
    match = ARTIFACT_PATTERN.match(filename)
    if not match:
        raise ValueError(
            "Artifact name must match <task>_<architecture>_v<semver>_<precision>.onnx "
            "with lowercase letters, digits, underscores, and precision fp32/fp16/int8"
        )
    return ArtifactName(
        filename=filename,
        family=match.group("family"),
        version=match.group("version"),
        precision=match.group("precision"),
    )

