"""HTML-only labels. Plain strings remain subject to Jinja autoescaping.

No SWG string-table assets ship with this repository. A resolver can accept a
verified reference-to-value mapping; formatted fallbacks are not translations.
"""
import re
from sqlalchemy import text


def readable_identifier(value, default='Unknown'):
    if not isinstance(value, str) or not value.strip():
        return default
    return re.sub(r'[_\s]+', ' ', value.strip()).title()


class DisplayNames:
    def __init__(self, *, localization=None, taxonomy=None, creature_names=None):
        self.localization = localization or {}
        self.taxonomy = taxonomy or {}
        self.creature_names = creature_names or {}

    def localized(self, reference):
        if not isinstance(reference, str) or not re.fullmatch(r'@[^\s:]+:[^\s:]+', reference):
            return None
        value = self.localization.get(reference)
        return value.strip() if isinstance(value, str) and value.strip() and not value.startswith('@') else None

    def creature(self, creature):
        names = self.creature_names.get(creature.get('definition_id'), {})
        custom = names.get('customName')
        if custom and not custom.startswith('@'):
            return custom
        reference = names.get('objectName') or creature.get('localization_reference')
        for value in (custom, reference):
            resolved = self.localized(value)
            if resolved:
                return resolved
        name = creature.get('name')
        if name and name != creature.get('template_key') and not name.startswith('@'):
            return name
        return readable_identifier(creature.get('template_key'), 'Unnamed creature')

    def region(self, value):
        resolved = self.localized(value)
        if resolved:
            return resolved
        if isinstance(value, str) and value.startswith('@'):
            # Malformed references must not leak their table name into a label.
            value = value.split(':', 1)[1] if ':' in value else None
        return readable_identifier(value, 'Unnamed source definition')

    def classification(self, value):
        key = value.strip() if isinstance(value, str) else ''
        label = self.taxonomy.get(key)
        if label and label != key and not label.startswith('@'):
            return label
        category, separator, suffix = key.partition('_')
        if separator and category in ('hide', 'meat', 'bone', 'milk') and suffix:
            return readable_identifier(suffix + '_' + category)
        return readable_identifier(key, 'Unspecified resource class')


def presentation(connection, rows):
    """Bulk HTML-only lookups; never mutate public API projections."""
    ids = [row['definition_id'] for row in rows]
    names = {}
    if ids:
        for row in connection.execute(text('SELECT definition_id,kind,value FROM creature_names WHERE definition_id=ANY(:ids)'), {'ids': ids}).mappings():
            names.setdefault(row['definition_id'], {})[row['kind']] = row['value']
    taxonomy = dict(connection.execute(text('SELECT slug,display_name FROM resource_types')).all())
    return DisplayNames(taxonomy=taxonomy, creature_names=names)
