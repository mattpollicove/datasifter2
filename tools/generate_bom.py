"""Generate a platform-specific CycloneDX BOM and its Markdown inventory."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import sys
from collections import deque
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def source_version(root: Path) -> str:
    tree = ast.parse((root / "src" / "datasifter" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise ValueError("Source application version is missing.")


def resolve_libraries(
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    groups = {
        "runtime": manifest["project"]["dependencies"],
        "build-system": manifest["build-system"]["requires"],
        **manifest["project"].get("optional-dependencies", {}),
    }
    queue = deque(
        (Requirement(text), group, None)
        for group, requirements in groups.items()
        for text in requirements
    )
    records: dict[str, dict[str, Any]] = {}
    edges: dict[str, set[str]] = {}
    direct: set[str] = set()
    visited: set[tuple[str, str, str]] = set()
    while queue:
        requirement, group, parent = queue.popleft()
        # A dependency's extra markers are evaluated when enqueued below.
        if parent is None and requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(requirement.name)
        distribution = metadata.distribution(name)
        if requirement.specifier and not requirement.specifier.contains(distribution.version):
            raise ValueError(f"{name} {distribution.version} does not satisfy {requirement}")
        ref = f"pkg:pypi/{quote(name)}@{quote(distribution.version)}"
        if parent is None:
            direct.add(ref)
        else:
            edges.setdefault(parent, set()).add(ref)
        if name not in records:
            component: dict[str, Any] = {
                "type": "library", "bom-ref": ref,
                "name": distribution.metadata["Name"] or name,
                "version": distribution.version, "purl": ref,
                "properties": [],
            }
            license_expression = distribution.metadata.get("License-Expression")
            license_text = distribution.metadata.get("License", "").strip()
            if license_expression:
                component["licenses"] = [{"expression": license_expression}]
            elif (
                license_text and license_text.casefold() != "unknown"
                and "\n" not in license_text and len(license_text) < 200
            ):
                component["licenses"] = [{"license": {"name": license_text}}]
            records[name] = component
        properties = records[name]["properties"]
        property_value = {"name": "datasifter:dependency-group", "value": group}
        if property_value not in properties:
            properties.append(property_value)
        edges.setdefault(ref, set())
        for extra in sorted(requirement.extras | {""}):
            key = (name, extra, group)
            if key in visited:
                continue
            visited.add(key)
            for text in distribution.requires or []:
                dependency = Requirement(text)
                if dependency.marker is None or dependency.marker.evaluate({"extra": extra}):
                    queue.append((dependency, group, ref))
    return (
        sorted(records.values(), key=lambda component: component["name"].casefold()),
        [{"ref": ref, "dependsOn": sorted(children)} for ref, children in sorted(edges.items())],
        direct,
    )


def file_component(path: Path, root: Path, category: str) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    return {
        "type": "file",
        "bom-ref": f"file:{relative}",
        "name": relative,
        "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(path.read_bytes()).hexdigest()}],
        "properties": [{"name": "datasifter:category", "value": category}],
    }


def create_bom(root: Path) -> dict[str, Any]:
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    libraries, dependencies, direct = resolve_libraries(manifest)
    version = source_version(root)
    application_ref = f"datasifter:{version}"
    python_ref = f"cpython:{platform.python_version()}"
    components = libraries + [{
        "type": "application", "bom-ref": python_ref,
        "name": "CPython", "version": platform.python_version(),
        "properties": [{"name": "datasifter:category", "value": "interpreter"}],
    }]
    for directory in ("src", "tools", "tests"):
        components.extend(
            file_component(path, root, "test" if directory == "tests" else "source")
            for path in sorted((root / directory).rglob("*.py"))
        )
    components.append(file_component(root / "datasifter.spec", root, "build"))
    components.append(file_component(root / "pyproject.toml", root, "build"))
    packaged = root / "dist" / "DataSifter 2.0"
    if packaged.exists():
        components.extend(
            file_component(path, root, "packaged-native")
            for path in sorted(packaged.rglob("*"))
            if path.is_file() and path.suffix.casefold() in {".dll", ".pyd", ".exe", ".so", ".dylib"}
        )
    dependencies.append({
        "ref": application_ref,
        "dependsOn": sorted(direct | {c["bom-ref"] for c in components if c not in libraries}),
    })
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid4()}", "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": {
                "type": "application", "bom-ref": application_ref,
                "name": manifest["project"]["name"], "version": version,
            },
            "properties": [
                {"name": "datasifter:platform", "value": platform.platform()},
                {"name": "datasifter:scope",
                 "value": "Current-platform installed project dependency closure, source, and "
                          "available packaged native files; not the entire Python environment."},
            ],
        },
        "components": components, "dependencies": dependencies,
    }


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(bom: dict[str, Any]) -> str:
    lines = [
        "# DataSifter bill of materials", "",
        f"Application version: **{bom['metadata']['component']['version']}**",
        f"Generated: {bom['metadata']['timestamp']}", "",
        "Generated from the project manifest, installed distribution metadata, source files,",
        "and available packaged native binaries. Exact versions reflect this build environment.",
        "Includes direct and transitive runtime, local-AI, build, test, and BOM-tool dependencies.",
        "Platform-conditioned dependencies for other operating systems are not installed here",
        "and are not claimed as resolved. This is not a license audit or vulnerability scan.", "",
        "When generated outside a build, packaged-file hashes describe the last available build,",
        "not proof that those binaries incorporate current source changes.", "",
        "Regenerate with `python tools\\generate_bom.py`; standalone builds regenerate automatically.",
        "Dependency relationships and complete SHA-256 hashes are in [bom.json](./bom.json).", "",
        "## Python libraries", "",
        "| Library | Installed version | Dependency groups | License metadata |",
        "|---|---|---|---|",
    ]
    for component in bom["components"]:
        if component["type"] != "library":
            continue
        licenses = [
            license.get("expression") or license.get("license", {}).get("name", "")
            for license in component.get("licenses", [])
        ]
        groups = sorted(p["value"] for p in component.get("properties", []))
        lines.append(
            f"| {markdown_cell(component['name'])} | {component['version']} | "
            f"{', '.join(groups)} | {markdown_cell('; '.join(licenses) or 'Not declared')} |"
        )
    lines.extend([
        "", "## Internal components and build/test sources", "",
        "These file components are maintained with the application version; hashes identify",
        "their exact contents. See the specification and README for feature descriptions.", "",
        "| Component | Category |", "|---|---|",
    ])
    for component in bom["components"]:
        if component["type"] == "file" and component["properties"][0]["value"] != "packaged-native":
            name = component["name"]
            lines.append(f"| [{name}](./{name}) | {component['properties'][0]['value']} |")
    python = next(c for c in bom["components"] if c["name"] == "CPython")
    lines.extend([
        "", "## Interpreter and packaged native components", "",
        f"Build interpreter: CPython **{python['version']}**.",
        "Native DLLs/extensions include embedded Qt, cryptography, and llama.cpp components",
        "where present. Filenames and hashes are observed, not inferred versions or licenses.",
        "The enclosing Python distribution is listed above where identifiable; embedded",
        "third-party versions/licenses require vendor build information and are not guessed.", "",
        "| Packaged file | SHA-256 |", "|---|---|",
    ])
    native = [
        c for c in bom["components"]
        if c["type"] == "file" and c["properties"][0]["value"] == "packaged-native"
    ]
    if not native:
        lines.append("| No standalone build available | Not inventoried |")
    for component in native:
        lines.append(f"| `{component['name']}` | `{component['hashes'][0]['content']}` |")
    lines.extend([
        "", "## External inputs and services", "",
        "- LDAP v3 directory: user-provided endpoint; server product/version is not known.",
        "- GGUF model: optional user-selected file; no model is shipped or inventoried.",
        "- OS credential vault and certificate trust facilities: platform-provided services.",
        "- Python standard library: part of the CPython runtime listed above.", "",
    ])
    return "\n".join(lines)


def generate(root: Path) -> None:
    bom = create_bom(root)
    text = json.dumps(bom, indent=2) + "\n"
    errors = JsonStrictValidator(SchemaVersion.V1_6).validate_str(text)
    if errors:
        raise ValueError(f"CycloneDX schema validation failed: {errors}")
    references = {bom["metadata"]["component"]["bom-ref"]} | {
        component["bom-ref"] for component in bom["components"]
    }
    for dependency in bom["dependencies"]:
        if dependency["ref"] not in references or not set(dependency["dependsOn"]) <= references:
            raise ValueError("BOM dependency graph contains unresolved references.")
    (root / "bom.json").write_text(text, encoding="utf-8")
    (root / "BOM.md").write_text(render_markdown(bom), encoding="utf-8")
    print(f"Generated validated BOM: {len(bom['components'])} components.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    generate(parser.parse_args().root.resolve())
