"""A local model gets a separate required output slot for every planned scene."""
import json

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
