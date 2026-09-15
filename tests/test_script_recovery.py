"""Malformed model output can be repaired, but required copy rules must hold."""
import json

import pytest

from reelfactory import ad_prompt
from reelfactory.config import Brand, Product
from reelfactory import script


def brief(tmp_path):
    product = Product(slug='demo', dir=tmp_path, name_en='Chair', name_hi='Chair', photos=[],
                      usp_en=['Blue finish'], usp_hi=['नीला रंग'], target_seconds=10)
    brand = Brand()
    rows = [
        {'role': step['role'], 'vo': 'See this blue chair for your home.', 'overlay': 'Blue chair'}
        for step in ad_prompt.segment_plan(product, brand, 'en', product.usp_en)
        for _ in range(step['count'])
    ]
    return product, brand, rows


def test_malformed_output_retries_with_original_brief(tmp_path):
    product, brand, rows = brief(tmp_path)
    requests = []

    def model(prompt):
        requests.append(prompt)
        return json.dumps({'segments': rows}) if len(requests) > 1 else '{"segments":[{"role":"hook","vo":"Incomplete"}]}'

    result = ad_prompt.write_with_length_retry(product, brand, 'en', product.usp_en,
                                              'Keep the opening friendly', model)
    assert len(requests) == 2 and result
    for text in ('Blue finish', 'Keep the opening friendly', 'FORMAT CORRECTION', 'overlay'):
        assert text in requests[1]


def test_copy_rules_are_not_silently_ignored_after_retry(tmp_path):
    product, brand, rows = brief(tmp_path)
    product.must_say = ['Ask for a demo']
    with pytest.raises(ValueError, match='still breaks your instructions'):
        ad_prompt.write_with_length_retry(product, brand, 'en', product.usp_en, '',
                                         lambda prompt: json.dumps({'segments': rows}))


@pytest.mark.parametrize('lang', ['en', 'hi'])
@pytest.mark.parametrize('tone', ['value', 'premium', 'trust'])
def test_template_hooks_use_current_product_without_inventing_rack_claims(tmp_path, lang, tone):
    product, brand, _ = brief(tmp_path)
    product.tone = tone
    for variant in range(3):
        hook = script.build(product, brand, lang, variant)[0].vo
        assert 'Chair' in hook
        assert not any(claim in hook for claim in ('rack', 'thousands', 'years', 'रैक', 'हज़ारों', 'सालों'))
