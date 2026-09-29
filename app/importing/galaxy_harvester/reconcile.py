from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


PLANET_NAME_TO_SLUG = {
    "corellia": "corellia",
    "dantooine": "dantooine",
    "dathomir": "dathomir",
    "endor": "endor",
    "lok": "lok",
    "naboo": "naboo",
    "rori": "rori",
    "talus": "talus",
    "tatooine": "tatooine",
    "yavin 4": "yavin4",
    "yavin4": "yavin4",
}


GH_TYPE_ALIASES_TO_CORE3 = {
    "meat_reptillian_corellia": "meat_reptilian_corellia",
    "meat_reptillian_dantooine": "meat_reptilian_dantooine",
    "meat_reptillian_dathomir": "meat_reptilian_dathomir",
    "meat_reptillian_endor": "meat_reptilian_endor",
    "meat_reptillian_lok": "meat_reptilian_lok",
    "meat_reptillian_naboo": "meat_reptilian_naboo",
    "meat_reptillian_rori": "meat_reptilian_rori",
    "meat_reptillian_talus": "meat_reptilian_talus",
    "meat_reptillian_tatooine": "meat_reptilian_tatooine",
    "meat_reptillian_yavin4": "meat_reptilian_yavin4",
}


@dataclass(frozen=True)
class TypeReconciliation:
    total_source_types: int
    exact_canonical_matches: int
    aliased_canonical_matches: int
    already_mapped_matches: int
    unresolved_types: tuple[str, ...]
    ambiguous_types: tuple[str, ...]
    mapping_by_source_type: dict[str, int | None]
    mapping_confidence_by_source_type: dict[str, str]

    def to_report(self) -> dict[str, Any]:
        return {
            "total_source_types": self.total_source_types,
            "exact_canonical_matches": self.exact_canonical_matches,
            "aliased_canonical_matches": self.aliased_canonical_matches,
            "already_mapped_matches": self.already_mapped_matches,
            "unresolved": len(self.unresolved_types),
            "unresolved_types": list(self.unresolved_types),
            "ambiguous_conflicting": len(self.ambiguous_types),
            "ambiguous_types": list(self.ambiguous_types),
        }


@dataclass(frozen=True)
class PlanetReconciliation:
    total_source_planets: int
    mapped_planets: int
    unresolved_planets: tuple[str, ...]
    mapping_by_source_planet_id: dict[str, int]
    slug_by_source_planet_id: dict[str, str]

    def to_report(self) -> dict[str, Any]:
        return {
            "total_source_planets": self.total_source_planets,
            "mapped_planets": self.mapped_planets,
            "unresolved_planets": list(self.unresolved_planets),
            "slug_by_source_planet_id": self.slug_by_source_planet_id,
        }


def reconcile_types(connection, source_types: dict[str, dict[str, Any]], source_instance_id: int) -> TypeReconciliation:
    canonical = dict(connection.execute(text("SELECT slug, id FROM resource_types")).all())
    existing_rows = connection.execute(
        text(
            """
            SELECT source_type_key, canonical_type_id
            FROM source_resource_types
            WHERE source_instance_id = :source_instance_id
            """
        ),
        {"source_instance_id": source_instance_id},
    ).all()
    existing = {row[0]: row[1] for row in existing_rows}
    mapping: dict[str, int | None] = {}
    confidences: dict[str, str] = {}
    exact = 0
    aliased = 0
    already = 0
    unresolved: list[str] = []
    ambiguous: list[str] = []
    for source_type in sorted(source_types):
        existing_id = existing.get(source_type)
        canonical_id = canonical.get(source_type)
        alias_target = GH_TYPE_ALIASES_TO_CORE3.get(source_type)
        alias_id = canonical.get(alias_target) if alias_target else None
        if existing_id is not None:
            expected_id = canonical_id if canonical_id is not None else alias_id
            if expected_id is not None and existing_id != expected_id:
                ambiguous.append(source_type)
                mapping[source_type] = None
                confidences[source_type] = "ambiguous_conflicting"
            else:
                mapping[source_type] = existing_id
                if canonical_id is not None and existing_id == canonical_id:
                    confidences[source_type] = "exact_identifier"
                elif alias_id is not None and existing_id == alias_id:
                    confidences[source_type] = "explicit_historical_alias"
                else:
                    confidences[source_type] = "already_mapped"
                already += 1
        elif canonical_id is not None:
            mapping[source_type] = canonical_id
            confidences[source_type] = "exact_identifier"
            exact += 1
        elif alias_id is not None:
            mapping[source_type] = alias_id
            confidences[source_type] = "explicit_historical_alias"
            aliased += 1
        else:
            mapping[source_type] = None
            confidences[source_type] = "unresolved"
            unresolved.append(source_type)
    return TypeReconciliation(
        total_source_types=len(source_types),
        exact_canonical_matches=exact,
        aliased_canonical_matches=aliased,
        already_mapped_matches=already,
        unresolved_types=tuple(unresolved),
        ambiguous_types=tuple(ambiguous),
        mapping_by_source_type=mapping,
        mapping_confidence_by_source_type=confidences,
    )


def reconcile_planets(connection, planets: dict[str, dict[str, Any]]) -> PlanetReconciliation:
    canonical = dict(connection.execute(text("SELECT slug, id FROM planets")).all())
    mapping: dict[str, int] = {}
    slugs: dict[str, str] = {}
    unresolved: list[str] = []
    for source_id, payload in sorted(planets.items(), key=lambda item: int(item[0])):
        name = payload["planet_name"]
        slug = PLANET_NAME_TO_SLUG.get(name.strip().casefold())
        if slug is None or slug not in canonical:
            unresolved.append(name)
            continue
        mapping[str(source_id)] = canonical[slug]
        slugs[str(source_id)] = slug
    return PlanetReconciliation(
        total_source_planets=len(planets),
        mapped_planets=len(mapping),
        unresolved_planets=tuple(unresolved),
        mapping_by_source_planet_id=mapping,
        slug_by_source_planet_id=slugs,
    )
