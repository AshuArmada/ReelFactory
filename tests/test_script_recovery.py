"""Malformed model output can be repaired, but required copy rules must hold."""
import json

import pytest

from reelfactory import ad_prompt
from reelfactory.config import Brand, Product
from reelfactory import script


def roofing_brief(tmp_path):
    return Product(
        slug='roofing-language-check', dir=tmp_path, photos=[],
        name_en='Colour-coated roofing sheets', name_hi='कलर कोटेड छत की चादरें',
        usp_hi=['उपलब्ध रंगों में से पसंद का रंग चुन सकते हैं।',
                'छत की लंबाई के अनुसार चादरें काटी जा सकती हैं।',
                'नाप, चादरों और फिटिंग की जानकारी एक ही टीम से मिलती है।'],
        audience='घर की छत बनवाने वाले ग्राहक', target_seconds=30,
    ), Brand(name='शर्मा स्टील वर्क्स', phone='+91 98765 43210')


def test_roofing_brief_asks_for_connected_speech_without_invented_benefits(tmp_path):
    product, brand = roofing_brief(tmp_path)
    prompt = ad_prompt.build_prompt(product, brand, 'hi', product.usp_hi)
    assert product.name_hi in prompt
    assert all(usp in prompt for usp in product.usp_hi)
    assert 'one conversation first' in prompt
    assert 'Do not manufacture a benefit' in prompt
    assert 'not repeat the entire sentence' in prompt


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


def test_failed_retry_does_not_return_a_draft_that_breaks_copy_rules(tmp_path):
    product, brand, rows = brief(tmp_path)
    product.must_say = ['Ask for a demo']
    responses = iter([json.dumps({'segments': rows}), '{}'])
    with pytest.raises(ValueError, match='still breaks your instructions'):
        ad_prompt.write_with_length_retry(product, brand, 'en', product.usp_en, '',
                                         lambda prompt: next(responses))


def test_hindi_draft_gets_edited_with_original_facts_and_instructions(tmp_path):
    product, brand, rows = brief(tmp_path)
    rows[0]['vo'] = 'नई छत के लिए रंग और साइज़ मैसेच ज़रूर हॉ।'
    corrected = [dict(row, vo='अपने घर के लिए सही रंग की कुर्सी चुनिए।', overlay='पसंद का रंग')
                 for row in rows]
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return json.dumps({'segments': rows if len(prompts) == 1 else corrected})
    result = ad_prompt.write_with_length_retry(product, brand, 'hi', product.usp_hi,
                                              'Keep the opening friendly', model)
    assert len(prompts) == 2
    for text in ('HINDI EDITOR PASS', product.usp_hi[0], 'Keep the opening friendly', rows[0]['vo']):
        assert text in prompts[1]
    assert result[0].vo == corrected[0]['vo']
    assert result[0].overlay == corrected[0]['overlay']
    assert [s.role for s in result] == [s['role'] for s in rows]


@pytest.mark.parametrize('failure', ['missing_phrase', 'empty_caption', 'long_caption', 'wrong_language', 'bad_json'])
def test_hindi_editor_failure_never_returns_unreviewed_copy(tmp_path, failure):
    product, brand, rows = brief(tmp_path)
    product.must_say = ['नीला रंग']
    original = [dict(row, vo='अपने घर के लिए नीला रंग चुनिए और कुर्सी देखिए।', overlay='नीला रंग')
                for row in rows]
    edited = [dict(row) for row in original]
    if failure == 'missing_phrase':
        for row in edited:
            row['vo'] = 'अपने घर के लिए यह कुर्सी चुनिए।'
    elif failure == 'empty_caption':
        edited[0]['overlay'] = ''
    elif failure == 'long_caption':
        edited[0]['overlay'] = 'शब्द ' * 10
    elif failure == 'wrong_language':
        product.must_say = []
        for row in edited:
            row['vo'] = 'Choose a chair for your home.'
    responses = iter([json.dumps({'segments': original}),
                      '{}' if failure == 'bad_json' else json.dumps({'segments': edited})])
    with pytest.raises(ValueError):
        ad_prompt.write_with_length_retry(product, brand, 'hi', product.usp_hi, '',
                                         lambda prompt: next(responses))


@pytest.mark.parametrize('lang', ['en', 'hi'])
@pytest.mark.parametrize('tone', ['value', 'premium', 'trust'])
def test_template_hooks_use_current_product_without_inventing_rack_claims(tmp_path, lang, tone):
    product, brand, _ = brief(tmp_path)
    product.tone = tone
    for variant in range(3):
        hook = script.build(product, brand, lang, variant)[0].vo
        assert 'Chair' in hook
        assert not any(claim in hook for claim in ('rack', 'thousands', 'years', 'रैक', 'हज़ारों', 'सालों'))
