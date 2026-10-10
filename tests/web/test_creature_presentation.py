import pytest
from jinja2 import Environment

from app.web.creature_presentation import DisplayNames, readable_identifier


@pytest.mark.parametrize('key,label', [
    ('bantha_matriarch', 'Bantha Matriarch'),
    ('ancient_bull_rancor', 'Ancient Bull Rancor'),
    ('aged_lantern_bird', 'Aged Lantern Bird'), ('bull_ronto', 'Bull Ronto'),
    ('unit_42_alpha', 'Unit 42 Alpha'), ('odd__name-7', 'Odd Name-7'),
    ('', 'Unknown'), (None, 'Unknown')])
def test_identifier_fallback(key, label):
    assert readable_identifier(key) == label


def test_verified_localization_mapping_and_custom_name_precedence():
    display = DisplayNames(localization={'@mob/creature_names:bantha': 'Bantha'},
        creature_names={1: {'objectName': '@mob/creature_names:bantha'}})
    item = {'definition_id': 1, 'template_key': 'bantha_matriarch', 'name': 'bantha_matriarch'}
    assert display.creature(item) == 'Bantha'
    display.creature_names[1]['customName'] = 'Named Bantha'
    assert display.creature(item) == 'Named Bantha'
    assert item['template_key'] == 'bantha_matriarch'


@pytest.mark.parametrize('reference', [None, '', '@broken', '@table:', '@:key', '@table:two:keys', '@table:key'])
def test_missing_or_malformed_creature_localization(reference):
    assert DisplayNames().creature({'template_key': 'bull_ronto', 'localization_reference':reference}) == 'Bull Ronto'


def test_regions_and_numeric_suffixes():
    display = DisplayNames(localization={'@tatooine_region_names:eastern_dune_sea': 'Eastern Dune Sea'})
    assert display.region('@tatooine_region_names:eastern_dune_sea') == 'Eastern Dune Sea'
    assert display.region('@tatooine_region_names:western_dune_sea_1') == 'Western Dune Sea 1'
    assert display.region('@tatooine_region_names:northeastern_oasis') == 'Northeastern Oasis'
    assert display.region('@malformed') == 'Unnamed source definition'


@pytest.mark.parametrize('key,label', [('bone_mammal','Mammal Bone'), ('hide_wooly','Wooly Hide'),
    ('meat_herbivore','Herbivore Meat'), ('milk_wild','Wild Milk'),
    ('hide_leathery','Leathery Hide'), ('meat_carnivore','Carnivore Meat'),
    ('unknown_class_2','Unknown Class 2'), ('','Unspecified resource class')])
def test_classification_fallbacks(key, label):
    assert DisplayNames().classification(key) == label


def test_taxonomy_precedence_and_autoescaping():
    display = DisplayNames(taxonomy={'hide_wooly':'Verified Wooly Hide'},
        localization={'@table:key':'<script>alert(1)</script>'})
    assert display.classification(' hide_wooly ') == 'Verified Wooly Hide'
    template = Environment(autoescape=True).from_string('{{ display.region(value) }}')
    rendered = template.render(display=display, value='@table:key')
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
