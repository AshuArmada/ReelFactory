"""A local model gets a separate required output slot for every planned scene."""
import json
import re

import pytest

from reelfactory import ad_prompt, local_llm, local_script
from reelfactory.config import Brand, Product
from reelfactory.script import Segment


@pytest.mark.parametrize('lang', ['en', 'hi'])
def test_ten_selling_points_have_fixed_slots_through_generation_and_edit(tmp_path, monkeypatch, lang):
    product = Product(slug='racks', dir=tmp_path, photos=[], name_en='Racks', name_hi='रैक',
                      usp_en=[f'Point {i}' for i in range(10)],
                      usp_hi=[f'जानकारी {i}' for i in range(10)], target_seconds=10)
    calls = []
    def completion(model, **kwargs):
        calls.append(kwargs)
        schema = kwargs['response_format']['json_schema']['schema']['properties']['segments']
        assert schema['type'] == 'object'
        assert schema['additionalProperties'] is False
        assert schema['required'] == [f'scene_{i:03}' for i in range(1, 14)]
        slots = schema['properties']
        assert [slot['properties']['role']['enum'] for slot in slots.values()] == (
            [['hook'], ['reveal']] + [['usp']] * 10 + [['cta']])
        content = {key: {'role': slot['properties']['role']['enum'][0],
                         'vo': f'अपने घर के लिए यह रैक देखें। {key}', 'overlay': key}
                   for key, slot in reversed(list(slots.items()))}
        return {'choices': [{'message': {'content': json.dumps({'segments': content})}}]}
    monkeypatch.setattr(local_llm, 'chat_completion', completion)
    result = local_script.build(product, Brand(), lang, base_url='http://localhost:11434/v1', api_key='dummy')
    assert len(calls) == (2 if lang == 'hi' else 1)
    assert [s.role for s in result] == ['hook', 'reveal'] + ['usp'] * 10 + ['cta']
    assert [s.overlay for s in result] == [f'scene_{i:03}' for i in range(1, 14)]
    assert all('scene_013: role=cta' in call['messages'][0]['content'] for call in calls)
    assert product.target_seconds == 10


def test_edit_schema_follows_actual_draft_when_optional_scene_is_omitted(tmp_path, monkeypatch):
    product = Product(slug='racks', dir=tmp_path, photos=[], name_en='Racks', name_hi='रैक',
                      usp_hi=['जानकारी'], proof_points=['Supplied proof'], target_seconds=10)
    roles = ['hook', 'reveal', 'usp', 'cta']
    rows = [dict(role=role, vo='अपने घर के लिए यह रैक देखें।', overlay='रैक देखें') for role in roles]
    calls = []
    def completion(model, **kwargs):
        schema = kwargs['response_format']['json_schema']['schema']['properties']['segments']
        calls.append(schema)
        if len(calls) == 1:
            # Compatibility with a server that returns the old array format.
            return {'choices': [{'message': {'content': json.dumps({'segments': rows})}}]}
        assert len(schema['required']) == 4
        assert schema['properties']['scene_004']['properties']['role']['enum'] == ['cta']
        return {'choices': [{'message': {'content': json.dumps({'segments': {
            f'scene_{i:03}': row for i, row in enumerate(rows, 1)}})}}]}
    monkeypatch.setattr(local_llm, 'chat_completion', completion)
    result = local_script.build(product, Brand(), 'hi', base_url='http://localhost:11434/v1', api_key='dummy')
    assert len(calls[0]['required']) == 5  # Initial brief can include proof.
    assert result == [Segment(**row) for row in rows]


@pytest.mark.parametrize('slots', [[], ['scene_001', 'scene_003'], ['scene_000'], ['scene_1'], ['other']])
def test_invalid_named_slots_are_rejected_not_dropped(slots):
    raw = json.dumps({'segments': {key: {'role': 'usp', 'vo': 'Words', 'overlay': 'Caption'} for key in slots}})
    with pytest.raises(ValueError):
        ad_prompt.parse_segments(raw)


@pytest.mark.parametrize('lang', ['en', 'hi'])
def test_collection_names_are_constrained_in_every_generation_and_editor_slot(tmp_path, monkeypatch, lang):
    names = ['Steel racks (6 ft.)', 'railing']
    members = [dict(slug=f'item-{i}', facts=dict(name_en=name, name_hi=name), media={})
               for i, name in enumerate(names)]
    product = Product(slug='collection', dir=tmp_path, photos=[], name_en='Range', name_hi='Range',
                      collection_members=members, usp_en=names, usp_hi=names, target_seconds=10)
    rows = [dict(role=role, vo=vo, overlay='जानकारी') for role, vo in [
        ('hook', 'अपने घर और दुकान के लिए हमारी रेंज देखें।'),
        ('usp', 'Steel racks (6 ft.) के बारे में हमसे जानकारी लें।'),
        ('usp', 'railing का अपनी पसंद का डिज़ाइन देखें।'),
        ('cta', 'जानकारी के लिए हमें मैसेज करें।'),
    ]]
    calls = []

    def completion(model, **kwargs):
        calls.append(kwargs)
        slots = kwargs['response_format']['json_schema']['schema']['properties']['segments']['properties']
        prompt = kwargs['messages'][0]['content']
        assert len(slots) == 4
        assert 'Latin-script name in Latin script' in prompt
        for index, name in enumerate(names, 2):
            pattern = slots[f'scene_{index:03}']['properties']['vo']['pattern']
            assert re.fullmatch(pattern, rows[index - 1]['vo'])
            assert not re.fullmatch(pattern, name)
            assert not re.fullmatch(pattern, 'यह रेलिंग देखें।')
            assert not re.fullmatch(pattern, name + '\", \"overlay\": \"Caption')
            # Python's re.escape also escapes spaces; local grammar parsers
            # require literal spaces, with only regex metacharacters escaped.
            assert r'\ ' not in pattern
            assert f'scene_{index:03}: role=usp; begin vo with the literal name "{name}"' in prompt
        assert not re.fullmatch(slots['scene_002']['properties']['vo']['pattern'], 'Steel racks (6 ftX)')
        assert 'pattern' not in slots['scene_001']['properties']['vo']
        assert 'pattern' not in slots['scene_004']['properties']['vo']
        return {'choices': [{'message': {'content': json.dumps({'segments': rows})}}]}

    monkeypatch.setattr(local_llm, 'chat_completion', completion)
    result = local_script.build(product, Brand(), lang, base_url='http://localhost:11434/v1', api_key='dummy')
    assert len(calls) == (2 if lang == 'hi' else 1)
    assert [s.vo for s in result] == [row['vo'] for row in rows]
    # A server ignoring the constraint still cannot quietly accept a renamed
    # product or a name appearing only in the caption.
    result[2] = Segment('usp', 'अपनी पसंद की रेलिंग का डिज़ाइन देखें।', 'railing')
    with pytest.raises(ValueError, match='changed or omitted a required product name') as exc:
        ad_prompt.validate_segments(result, names, product, Brand(), lang)
    assert 'scene_003' in str(exc.value)
    assert 'Every required scene must be included' not in str(exc.value)
