from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text


INDEXER_SCHEMA_VERSION = "core3-resource-types-v1"
EDGE_SOURCE = "core3_resource_tree"
RESOURCE_TREE_SQL_RELATIVE_PATH = Path("MMOCoreORB/sql/datatables.sql")
RESOURCE_TREE_IFF_RELATIVE_PATH = Path("MMOCoreORB/datatables/resource/resource_tree.iff")

CORE3_ATTRIBUTE_TO_STAT = {
    "res_cold_resist": "CR",
    "res_conductivity": "CD",
    "res_decay_resist": "DR",
    "res_flavor": "FL",
    "res_heat_resist": "HR",
    "res_malleability": "MA",
    "res_potential_energy": "PE",
    "res_quality": "OQ",
    "res_shock_resistance": "SR",
    "res_toughness": "UT",
}
CORE3_STAT_CODES = tuple(sorted(CORE3_ATTRIBUTE_TO_STAT.values()))


@dataclass(frozen=True)
class SourceFileFingerprint:
    relative_path: str
    sha256: str
    byte_size: int
    parser_schema_version: str = INDEXER_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "parser_schema_version": self.parser_schema_version,
        }


@dataclass(frozen=True)
class Core3ResourceTreeRow:
    source_index: int
    source_type_id: str
    class_columns: tuple[str | None, ...]
    recycled: bool
    permanent: bool
    attributes: tuple[tuple[str, int, int], ...]
    resource_container_type: str | None
    random_name_class: str | None


@dataclass(frozen=True)
class StatRange:
    stat_code: str
    is_applicable: bool
    min_value: int | None = None
    max_value: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stat_code": self.stat_code,
            "is_applicable": self.is_applicable,
            "min_value": self.min_value,
            "max_value": self.max_value,
        }


@dataclass(frozen=True)
class NormalizedResourceType:
    source_type_id: str
    internal_name: str
    display_name: str | None
    display_name_resolved: bool
    direct_parents: tuple[str, ...]
    ancestors: tuple[str, ...]
    class_path: tuple[str, ...]
    stf_class_path: tuple[str, ...]
    stat_ranges: tuple[StatRange, ...]
    source_path: str
    source_fingerprint: str
    recycled: bool
    permanent: bool
    container_type: str | None
    random_name_class: str | None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type_id": self.source_type_id,
            "internal_name": self.internal_name,
            "display_name": self.display_name,
            "display_name_resolved": self.display_name_resolved,
            "direct_parents": list(self.direct_parents),
            "ancestors": list(self.ancestors),
            "class_path": list(self.class_path),
            "stf_class_path": list(self.stf_class_path),
            "stat_ranges": [item.to_dict() for item in self.stat_ranges],
            "source_path": self.source_path,
            "source_fingerprint": self.source_fingerprint,
            "recycled": self.recycled,
            "permanent": self.permanent,
            "container_type": self.container_type,
            "random_name_class": self.random_name_class,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ClosureRow:
    ancestor: str
    descendant: str
    depth: int

    def to_dict(self) -> dict[str, Any]:
        return {"ancestor": self.ancestor, "descendant": self.descendant, "depth": self.depth}


@dataclass
class ValidationResult:
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoveryResult:
    core3_root: Path
    repository_root: Path | None
    resource_tree_sql_path: Path
    resource_tree_iff_path: Path | None
    commit_sha: str | None


@dataclass(frozen=True)
class NormalizedIndex:
    core3_root: str
    core3_commit_sha: str | None
    source_files: tuple[SourceFileFingerprint, ...]
    resource_types: tuple[NormalizedResourceType, ...]
    direct_edges: tuple[tuple[str, str], ...]
    closure: tuple[ClosureRow, ...]
    validation: ValidationResult
    source_of_truth: str
    resource_tree_iff_exists: bool
    tre_extraction_required: bool

    def summary(self) -> dict[str, Any]:
        unresolved_display_names = sum(1 for item in self.resource_types if not item.display_name_resolved)
        unresolved_parents = sum(1 for warning in [*self.validation.warnings, *self.validation.errors] if warning.startswith("missing-parent:"))
        stat_range_records = sum(len(item.stat_ranges) for item in self.resource_types)
        return {
            "core3_commit_sha": self.core3_commit_sha,
            "source_of_truth": self.source_of_truth,
            "resource_tree_iff_exists": self.resource_tree_iff_exists,
            "tre_extraction_required": self.tre_extraction_required,
            "parser_schema_version": INDEXER_SCHEMA_VERSION,
            "custom_classification_count": sum(not item.stat_ranges for item in self.resource_types),
            "resource_type_count": len(self.resource_types),
            "direct_edge_count": len(self.direct_edges),
            "closure_row_count": len(self.closure),
            "stat_range_count": stat_range_records,
            "unresolved_display_name_count": unresolved_display_names,
            "unresolved_parent_count": unresolved_parents,
            "validation_warning_count": len(self.validation.warnings),
            "validation_error_count": len(self.validation.errors),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": INDEXER_SCHEMA_VERSION,
            "core3_root": self.core3_root,
            "core3_commit_sha": self.core3_commit_sha,
            "source_of_truth": self.source_of_truth,
            "resource_tree_iff_exists": self.resource_tree_iff_exists,
            "tre_extraction_required": self.tre_extraction_required,
            "source_files": [item.to_dict() for item in self.source_files],
            "summary": self.summary(),
            "resource_types": [item.to_dict() for item in self.resource_types],
            "direct_edges": [{"parent": parent, "child": child} for parent, child in self.direct_edges],
            "closure": [item.to_dict() for item in self.closure],
            "validation": {
                "warnings": sorted(self.validation.warnings),
                "errors": sorted(self.validation.errors),
            },
        }


def stable_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint_path(path: Path, relative_to: Path | None = None) -> SourceFileFingerprint:
    data = path.read_bytes()
    relative_path = path.relative_to(relative_to).as_posix() if relative_to else path.name
    return SourceFileFingerprint(relative_path=relative_path, sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))


def discover_core3_inputs(core3_root: Path) -> DiscoveryResult:
    root = core3_root.resolve()
    candidates = [
        root / RESOURCE_TREE_SQL_RELATIVE_PATH,
        root / "BellumGero-Live" / RESOURCE_TREE_SQL_RELATIVE_PATH,
    ]
    sql_path = next((path for path in candidates if path.exists()), None)
    if sql_path is None:
        expected = ", ".join(str(path) for path in candidates)
        raise FileNotFoundError(f"Core3 resource tree SQL dump not found. Expected one of: {expected}")

    repository_root = _find_git_root(sql_path.parent)
    relative_sql = sql_path.relative_to(repository_root) if repository_root else RESOURCE_TREE_SQL_RELATIVE_PATH
    iff_path = (repository_root / RESOURCE_TREE_IFF_RELATIVE_PATH) if repository_root else (root / RESOURCE_TREE_IFF_RELATIVE_PATH)
    if not iff_path.exists():
        iff_path = None

    return DiscoveryResult(
        core3_root=root,
        repository_root=repository_root,
        resource_tree_sql_path=sql_path,
        resource_tree_iff_path=iff_path,
        commit_sha=_git_commit(repository_root) if repository_root else None,
    )


def _find_git_root(path: Path) -> Path | None:
    for candidate in [path, *path.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_commit(repository_root: Path | None) -> str | None:
    if repository_root is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repository_root.as_posix()}", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def parse_resource_tree_sql(path: Path) -> tuple[Core3ResourceTreeRow, ...]:
    return parse_resource_tree_sql_text(path.read_text(encoding="utf-8", errors="replace"))


def parse_resource_tree_sql_text(sql_text: str) -> tuple[Core3ResourceTreeRow, ...]:
    marker = "INSERT INTO `resource_resource_tree` VALUES "
    start = sql_text.find(marker)
    if start < 0:
        raise ValueError("resource_resource_tree INSERT statement not found")
    start += len(marker)
    end = sql_text.find(";\n", start)
    if end < 0:
        end = sql_text.find(";", start)
    tuples = _parse_sql_tuples(sql_text[start:end])
    rows = tuple(_row_from_values(values) for values in tuples)
    return rows


def _parse_sql_tuples(values_text: str) -> list[list[Any]]:
    rows: list[list[Any]] = []
    i = 0
    while i < len(values_text):
        if values_text[i] != "(":
            i += 1
            continue
        i += 1
        row: list[Any] = []
        token = ""
        in_string = False
        while i < len(values_text):
            char = values_text[i]
            if in_string:
                if char == "\\" and i + 1 < len(values_text):
                    token += values_text[i + 1]
                    i += 2
                    continue
                if char == "'":
                    if i + 1 < len(values_text) and values_text[i + 1] == "'":
                        token += "'"
                        i += 2
                        continue
                    row.append(token)
                    token = ""
                    in_string = False
                    i += 1
                    continue
                token += char
                i += 1
                continue
            if char == "'":
                in_string = True
                token = ""
            elif char == ",":
                if token.strip():
                    row.append(_parse_atom(token.strip()))
                token = ""
            elif char == ")":
                if token.strip():
                    row.append(_parse_atom(token.strip()))
                rows.append(row)
                break
            else:
                token += char
            i += 1
        i += 1
    return rows


def _parse_atom(token: str) -> Any:
    if token.upper() == "NULL":
        return None
    return int(token)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _row_from_values(values: list[Any]) -> Core3ResourceTreeRow:
    if len(values) != 51:
        raise ValueError(f"resource_resource_tree row has {len(values)} columns; expected 51")
    attributes: list[tuple[str, int, int]] = []
    for attr_index in range(16, 27):
        attr_name = _clean(values[attr_index])
        if not attr_name:
            continue
        min_index = 27 + ((attr_index - 16) * 2)
        attributes.append((attr_name, int(values[min_index]), int(values[min_index + 1])))
    return Core3ResourceTreeRow(
        source_index=int(values[0]),
        source_type_id=str(_clean(values[1]) or ""),
        class_columns=tuple(_clean(value) for value in values[2:10]),
        recycled=bool(values[14]),
        permanent=bool(values[15]),
        attributes=tuple(attributes),
        resource_container_type=_clean(values[49]),
        random_name_class=_clean(values[50]),
    )


def normalize_rows(
    rows: tuple[Core3ResourceTreeRow, ...],
    source_path: str,
    source_fingerprint: str,
) -> tuple[tuple[NormalizedResourceType, ...], ValidationResult]:
    validation = ValidationResult()
    seen: set[str] = set()
    current_classes: list[str] = []
    current_stf_classes: list[str] = []
    normalized: list[NormalizedResourceType] = []

    for ordinal, row in enumerate(rows):
        warnings: list[str] = []
        if not row.source_type_id:
            validation.errors.append(f"missing-source-type-id:row-{ordinal}")
            continue
        if row.source_type_id in seen:
            validation.errors.append(f"duplicate-source-type-id:{row.source_type_id}")
        seen.add(row.source_type_id)

        if ordinal == 0 and row.source_type_id == "resource":
            class_path = tuple(value for value in row.class_columns if value)
            stf_path = ("resource",)
        else:
            for column_index in range(1, len(row.class_columns)):
                resource_class = row.class_columns[column_index]
                if not resource_class:
                    continue
                level = column_index - 1
                while len(current_stf_classes) > level:
                    current_stf_classes.pop()
                    current_classes.pop()
                current_stf_classes.append(row.source_type_id)
                current_classes.append(resource_class)
            stf_path = tuple(["resource", *current_stf_classes] if current_stf_classes[:1] != ["resource"] else current_stf_classes)
            class_path = tuple(["Resources", *current_classes] if current_classes[:1] != ["Resources"] else current_classes)

        display_name = class_path[-1] if class_path else None
        if display_name is None:
            warnings.append("display-name-unresolved")
        direct_parent = stf_path[-2] if len(stf_path) > 1 else None
        direct_parents = (direct_parent,) if direct_parent else ()
        ancestors = tuple(stf_path[:-1])
        stat_ranges = _normalize_stat_ranges(row, validation)
        normalized.append(
            NormalizedResourceType(
                source_type_id=row.source_type_id,
                internal_name=row.source_type_id,
                display_name=display_name,
                display_name_resolved=display_name is not None,
                direct_parents=direct_parents,
                ancestors=ancestors,
                class_path=class_path,
                stf_class_path=stf_path,
                stat_ranges=stat_ranges,
                source_path=source_path,
                source_fingerprint=source_fingerprint,
                recycled=row.recycled,
                permanent=row.permanent,
                container_type=row.resource_container_type,
                random_name_class=row.random_name_class,
                warnings=tuple(warnings),
            )
        )
        validation.warnings.extend(f"{row.source_type_id}:{warning}" for warning in warnings)

    validation = validate_normalized_types(tuple(normalized), validation)
    return tuple(sorted(normalized, key=lambda item: item.source_type_id)), validation


def _normalize_stat_ranges(row: Core3ResourceTreeRow, validation: ValidationResult) -> tuple[StatRange, ...]:
    by_code: dict[str, StatRange] = {}
    for attribute_name, min_value, max_value in row.attributes:
        stat_code = CORE3_ATTRIBUTE_TO_STAT.get(attribute_name)
        if stat_code is None:
            validation.warnings.append(f"unknown-core3-attribute:{row.source_type_id}:{attribute_name}")
            continue
        if min_value > max_value:
            validation.errors.append(f"invalid-stat-range:{row.source_type_id}:{stat_code}:{min_value}>{max_value}")
        by_code[stat_code] = StatRange(stat_code=stat_code, is_applicable=True, min_value=min_value, max_value=max_value)
    return tuple(by_code.get(code, StatRange(stat_code=code, is_applicable=False)) for code in CORE3_STAT_CODES)


def validate_normalized_types(
    resource_types: tuple[NormalizedResourceType, ...],
    validation: ValidationResult | None = None,
) -> ValidationResult:
    result = validation or ValidationResult()
    ids = {item.source_type_id for item in resource_types}
    edges = sorted((parent, item.source_type_id) for item in resource_types for parent in item.direct_parents)
    for parent, child in edges:
        if parent == child:
            result.errors.append(f"direct-self-edge:{child}")
        if parent not in ids:
            result.warnings.append(f"missing-parent:{child}:{parent}")
    _detect_cycles(edges, result)
    return result


def _detect_cycles(edges: list[tuple[str, str]], validation: ValidationResult) -> None:
    parents_by_child: dict[str, list[str]] = defaultdict(list)
    for parent, child in edges:
        parents_by_child[child].append(parent)
    for node in sorted(set(parents_by_child)):
        stack: list[str] = []

        def visit(current: str) -> None:
            if current in stack:
                cycle = "->".join([*stack[stack.index(current) :], current])
                validation.errors.append(f"cycle:{cycle}")
                return
            stack.append(current)
            for parent in parents_by_child.get(current, []):
                visit(parent)
            stack.pop()

        visit(node)


def build_direct_edges(resource_types: tuple[NormalizedResourceType, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted({(parent, item.source_type_id) for item in resource_types for parent in item.direct_parents}))


def build_closure(resource_type_ids: set[str], direct_edges: tuple[tuple[str, str], ...]) -> tuple[ClosureRow, ...]:
    parents_by_child: dict[str, set[str]] = defaultdict(set)
    for parent, child in direct_edges:
        parents_by_child[child].add(parent)

    rows: dict[tuple[str, str], int] = {(node, node): 0 for node in resource_type_ids}
    for descendant in sorted(resource_type_ids):
        queue = deque((parent, 1) for parent in sorted(parents_by_child.get(descendant, set())))
        visited: dict[str, int] = {}
        while queue:
            ancestor, depth = queue.popleft()
            if ancestor in visited and visited[ancestor] <= depth:
                continue
            visited[ancestor] = depth
            if ancestor in resource_type_ids:
                key = (ancestor, descendant)
                rows[key] = min(rows.get(key, depth), depth)
                for parent in sorted(parents_by_child.get(ancestor, set())):
                    queue.append((parent, depth + 1))
    return tuple(ClosureRow(ancestor=a, descendant=d, depth=depth) for (a, d), depth in sorted(rows.items()))


def build_index(core3_root: Path, spawn_path: Path | None = None) -> NormalizedIndex:
    discovery = discover_core3_inputs(core3_root)
    relative_base = discovery.repository_root or discovery.core3_root
    fingerprint = fingerprint_path(discovery.resource_tree_sql_path, relative_base)
    rows = parse_resource_tree_sql(discovery.resource_tree_sql_path)
    resource_types, validation = normalize_rows(rows, fingerprint.relative_path, fingerprint.sha256)
    fingerprints = [fingerprint]
    if spawn_path is not None:
        spawn_path = spawn_path if spawn_path.is_absolute() else relative_base / spawn_path
        if not spawn_path.is_file():
            raise FileNotFoundError(f"Optional spawn file not found: {spawn_path}")
        spawn_fingerprint = fingerprint_path(spawn_path)
        spawn_fingerprint = SourceFileFingerprint(str(spawn_path.resolve()), spawn_fingerprint.sha256, spawn_fingerprint.byte_size)
        fingerprints.append(spawn_fingerprint)
        from .spawn_classifications import extend_classifications
        resource_types = extend_classifications(resource_types, spawn_path.read_text(encoding="utf-8"), spawn_fingerprint, validation)
    direct_edges = build_direct_edges(resource_types)
    closure = build_closure({item.source_type_id for item in resource_types}, direct_edges)
    return NormalizedIndex(
        core3_root=str(discovery.core3_root),
        core3_commit_sha=discovery.commit_sha,
        source_files=tuple(fingerprints),
        resource_types=resource_types,
        direct_edges=direct_edges,
        closure=closure,
        validation=validation,
        source_of_truth=fingerprint.relative_path,
        resource_tree_iff_exists=discovery.resource_tree_iff_path is not None,
        tre_extraction_required=False,
    )


def write_index_json(index: NormalizedIndex, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(stable_json_bytes(index.to_dict()) + b"\n")


def load_index(connection, index: NormalizedIndex) -> dict[str, int]:
    if index.validation.errors:
        raise ValueError("Cannot load Core3 resource type index with validation errors")

    plan = inspect_database(connection, index)
    if plan["conflicts"]:
        raise ValueError(f"Cannot load taxonomy: {plan['conflicts']}")
    source_instance_id = connection.execute(
        text("SELECT id FROM source_instances WHERE code = 'bellum-gero-live'")
    ).scalar_one()

    existing_mappings = dict(connection.execute(text(
        "SELECT source_type_key, canonical_type_id FROM source_resource_types "
        "WHERE source_instance_id = :id AND canonical_type_id IS NOT NULL"
    ), {"id": source_instance_id}).all())
    existing_ids = set(connection.execute(text("SELECT id FROM resource_types")).scalars())
    resource_type_ids: dict[str, int] = {}
    for item in index.resource_types:
        if item.source_type_id in existing_mappings:
            resource_type_ids[item.source_type_id] = existing_mappings[item.source_type_id]
            continue
        metadata = json.dumps(
            {
                "source": EDGE_SOURCE,
                "source_path": item.source_path,
                "source_fingerprint": item.source_fingerprint,
                "class_path": list(item.class_path),
                "stf_class_path": list(item.stf_class_path),
                "recycled": item.recycled,
                "permanent": item.permanent,
            },
            sort_keys=True,
        )
        row = connection.execute(
            text(
                """
                INSERT INTO resource_types
                    (slug, display_name, kind, is_spawnable, core3_stf_name, core3_display_class,
                     container_type, inventory_type, metadata)
                VALUES
                    (:slug, :display_name, 'core3', :is_spawnable, :core3_stf_name, :core3_display_class,
                     :container_type, :inventory_type, CAST(:metadata AS jsonb))
                ON CONFLICT (slug) DO UPDATE SET slug = resource_types.slug
                RETURNING id
                """
            ),
            {
                "slug": item.source_type_id,
                "display_name": item.display_name or item.source_type_id,
                "is_spawnable": len(item.direct_parents) > 0 and (item.random_name_class is not None or not item.stat_ranges),
                "core3_stf_name": item.source_type_id,
                "core3_display_class": item.display_name,
                "container_type": item.container_type,
                "inventory_type": item.random_name_class,
                "metadata": metadata,
            },
        ).one()
        resource_type_ids[item.source_type_id] = row[0]

    source_type_ids: dict[str, int] = {}
    for item in index.resource_types:
        metadata = json.dumps({"source": EDGE_SOURCE, "source_path": item.source_path, "source_fingerprint": item.source_fingerprint}, sort_keys=True)
        row = connection.execute(
            text(
                """
                INSERT INTO source_resource_types
                    (source_instance_id, source_type_key, source_type_name, canonical_type_id,
                     mapping_status, mapping_confidence, metadata)
                VALUES
                    (:source_instance_id, :source_type_key, :source_type_name, :canonical_type_id,
                     'mapped', 'authoritative', CAST(:metadata AS jsonb))
                ON CONFLICT (source_instance_id, source_type_key) DO UPDATE SET
                    source_type_key = source_resource_types.source_type_key
                RETURNING id
                """
            ),
            {
                "source_instance_id": source_instance_id,
                "source_type_key": item.source_type_id,
                "source_type_name": item.display_name,
                "canonical_type_id": resource_type_ids[item.source_type_id],
                "metadata": metadata,
            },
        ).one()
        source_type_ids[item.source_type_id] = row[0]

    for parent, child in index.direct_edges:
        if parent not in resource_type_ids or child not in resource_type_ids:
            continue
        if resource_type_ids[parent] == resource_type_ids[child]:
            continue
        connection.execute(
            text(
                """
                INSERT INTO resource_type_edges (parent_type_id, child_type_id, source)
                VALUES (:parent_type_id, :child_type_id, :source)
                ON CONFLICT DO NOTHING
                """
            ),
            {"parent_type_id": resource_type_ids[parent], "child_type_id": resource_type_ids[child], "source": EDGE_SOURCE},
        )

    for row in index.closure:
        if row.ancestor not in resource_type_ids or row.descendant not in resource_type_ids:
            continue
        connection.execute(
            text(
                """
                INSERT INTO resource_type_closure (ancestor_type_id, descendant_type_id, depth)
                VALUES (:ancestor_type_id, :descendant_type_id, :depth)
                ON CONFLICT (ancestor_type_id, descendant_type_id) DO UPDATE SET depth = LEAST(resource_type_closure.depth, EXCLUDED.depth)
                """
            ),
            {
                "ancestor_type_id": resource_type_ids[row.ancestor],
                "descendant_type_id": resource_type_ids[row.descendant],
                "depth": row.depth,
            },
        )

    for item in index.resource_types:
        resource_type_id = resource_type_ids[item.source_type_id]
        if resource_type_id in existing_ids:
            continue
        for stat_range in item.stat_ranges:
            connection.execute(
                text(
                    """
                    INSERT INTO resource_type_stat_ranges
                        (resource_type_id, source_resource_type_id, stat_code, min_value, max_value, is_applicable)
                    VALUES
                        (:resource_type_id, NULL, :stat_code, :min_value, :max_value, :is_applicable)
                    """
                ),
                {
                    "resource_type_id": resource_type_id,
                    "stat_code": stat_range.stat_code,
                    "min_value": stat_range.min_value,
                    "max_value": stat_range.max_value,
                    "is_applicable": stat_range.is_applicable,
                },
            )

    return {
        "resource_types": len(index.resource_types),
        "source_resource_types": len(source_type_ids),
        "direct_edges": len(index.direct_edges),
        "closure_rows": len(index.closure),
        "stat_ranges": sum(len(item.stat_ranges) for item in index.resource_types),
    }


def inspect_database(connection, index: NormalizedIndex) -> dict[str, Any]:
    """Read-only plan; existing mappings and canonical rows are never replaced."""
    mapping_rows = connection.execute(text(
        "SELECT source_type_key, canonical_type_id, mapping_status FROM source_resource_types "
        "WHERE source_instance_id = (SELECT id FROM source_instances WHERE code = 'bellum-gero-live')"
    )).all()
    mappings = {row.source_type_key: row.canonical_type_id for row in mapping_rows}
    mapping_statuses = {row.source_type_key: row.mapping_status for row in mapping_rows}
    canonical = dict(connection.execute(text("SELECT slug, id FROM resource_types")).all())
    slugs = set(canonical)
    conflicts = [f"unresolved-existing-mapping:{item.source_type_id}" for item in index.resource_types if item.source_type_id in mappings and (mappings[item.source_type_id] is None or mapping_statuses[item.source_type_id] != "mapped")]
    projected = {item.source_type_id: str(mappings.get(item.source_type_id) or canonical.get(item.source_type_id) or f"new:{item.source_type_id}") for item in index.resource_types}
    existing_edges = {(str(parent), str(child), source) for parent, child, source in connection.execute(text("SELECT parent_type_id, child_type_id, source FROM resource_type_edges"))}
    edges = {(parent, child) for parent, child, _ in existing_edges}
    edges.update((projected[parent], projected[child]) for parent, child in index.direct_edges if parent in projected and child in projected and projected[parent] != projected[child])
    graph_validation = ValidationResult()
    _detect_cycles(sorted(edges), graph_validation)
    conflicts.extend(graph_validation.errors)
    existing_closure = {(str(ancestor), str(descendant)): depth for ancestor, descendant, depth in connection.execute(text("SELECT ancestor_type_id, descendant_type_id, depth FROM resource_type_closure"))}
    proposed_closure = [row.to_dict() for row in index.closure
                        if row.ancestor in projected and row.descendant in projected
                        and (existing_closure.get((projected[row.ancestor], projected[row.descendant])) is None
                             or row.depth < existing_closure[(projected[row.ancestor], projected[row.descendant])])]
    return {
        "conflicts": conflicts,
        "newly_discovered_classifications": [item.to_dict() for item in index.resource_types if not item.stat_ranges],
        "existing_mappings": {item.source_type_id: mappings[item.source_type_id] for item in index.resource_types if item.source_type_id in mappings},
        "proposed_resource_type_inserts": [item.source_type_id for item in index.resource_types if item.source_type_id not in mappings and item.source_type_id not in slugs],
        "proposed_mapping_inserts": [item.source_type_id for item in index.resource_types if item.source_type_id not in mappings],
        "existing_mapping_statuses": {key: mapping_statuses[key] for key in projected if key in mapping_statuses},
        "proposed_edge_inserts": [{"parent": parent, "child": child} for parent, child in index.direct_edges
                                  if parent in projected and child in projected and projected[parent] != projected[child]
                                  and (projected[parent], projected[child], EDGE_SOURCE) not in existing_edges],
        "proposed_closure_upserts": proposed_closure,
        "proposed_stat_range_inserts": sum(len(item.stat_ranges) for item in index.resource_types
                                          if item.source_type_id not in mappings and item.source_type_id not in slugs),
        "validation_errors": index.validation.errors,
        "hierarchy": {"edges": len(index.direct_edges), "closure": len(index.closure), "operation": "insert missing only; retain existing minimum depths"},
    }


def _print_summary(index: NormalizedIndex, output_path: Path | None = None) -> None:
    summary = index.summary()
    for key in sorted(summary):
        print(f"{key}: {summary[key]}")
    print("source_files:")
    for source_file in index.source_files:
        print(f"  - {source_file.relative_path} sha256={source_file.sha256} bytes={source_file.byte_size}")
    custom_types = [item.source_type_id for item in index.resource_types if not item.stat_ranges]
    if custom_types:
        print("newly_discovered_classifications:")
        for key in custom_types:
            print(f"  - {key}")
    if index.validation.warnings:
        print("warnings:")
        for warning in sorted(index.validation.warnings)[:25]:
            print(f"  - {warning}")
    if index.validation.errors:
        print("errors:")
        for error in sorted(index.validation.errors):
            print(f"  - {error}")
    if output_path is not None:
        print(f"json_artifact: {output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or load Bellum Gero Core3 resource type taxonomy.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Parse and validate Core3 resource types without database writes.")
    inspect_parser.add_argument("--core3-root", required=True, type=Path)
    inspect_parser.add_argument("--output", type=Path, default=Path(".phase2-output/core3_resource_types.json"))

    load_parser = subparsers.add_parser("load", help="Load normalized Core3 resource types into the Phase 1 database.")
    load_parser.add_argument("--core3-root", required=True, type=Path)
    load_parser.add_argument("--dry-run", action="store_true", help="Parse and validate only; do not write to PostgreSQL.")

    for command_parser in (inspect_parser, load_parser):
        command_parser.add_argument("--spawn-file", type=Path, help="Optional resource_manager_spawns.lua (relative to Core3 repository or absolute).")
    load_parser.add_argument("--compare-database", action="store_true", help="Read existing mappings and proposed inserts, including during dry-run.")
    args = parser.parse_args(argv)
    try:
        index = build_index(args.core3_root, args.spawn_file) if args.spawn_file is not None else build_index(args.core3_root)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Taxonomy input error: {exc}\n")

    if args.command == "inspect":
        write_index_json(index, args.output)
        _print_summary(index, args.output)
        return 1 if index.validation.errors else 0

    if args.command == "load":
        _print_summary(index)
        database_conflicts = []
        if args.compare_database:
            from app.db.session import make_engine
            with make_engine().connect() as connection:
                plan = inspect_database(connection, index)
                database_conflicts = plan["conflicts"]
                print(json.dumps(plan, sort_keys=True))
        if args.dry_run:
            print("dry_run: true")
            return 1 if index.validation.errors or database_conflicts else 0
        from app.db.session import make_engine

        with make_engine().begin() as connection:
            result = load_index(connection, index)
        print("loaded:")
        for key in sorted(result):
            print(f"  {key}: {result[key]}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
