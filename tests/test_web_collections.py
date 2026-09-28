"""Selected products become one editable reel, with matching scene photos."""
from werkzeug.datastructures import MultiDict
import json
import pytest

from conftest import PRODUCT, make_product, read_yaml
from reelfactory.config import Brand, Product
from reelfactory import cli, script
from reelfactory import ad_prompt, gemini, local_llm, photo_analysis
from conftest import write_yaml


def test_collection_preserves_all_products_and_photo_alignment(client, project, photos, monkeypatch):
    slugs = ["test-rack"]
    for index in range(3):
        slug = f"item-{index}"
        make_product(project, slug, dict(PRODUCT, name_en=f"Item {index}", price=f"Rs {index + 10}"), photos)
        slugs.append(slug)
    originals = [(project / "products" / slug / "product.yaml").read_bytes() for slug in slugs]
    response = client.post("/collections/new", data=MultiDict(
        [("name", "Summer collection")] + [("products", slug) for slug in slugs]))
    assert response.status_code == 302
    slug = response.location.split("/")[-2]
    folder = project / "products" / slug
    collection = Product.load(folder)
    assert collection.name_en == "Summer collection"
    assert read_yaml(folder / "collection.yaml")["products"] == slugs
    brand = Brand.load(project / "brand.yaml")
    for lang in ("hi", "en"):
        segments = script.build(collection, brand, lang)
        assert len(segments) == 6  # shared opening, four products, closing
        shots = cli._shot_photos(collection, len(segments))
        for index, source_slug in enumerate(slugs):
            source = Product.load(project / "products" / source_slug)
            assert source.name(lang) in segments[index + 1].vo
            assert source.price not in segments[index + 1].vo
            assert shots[index + 1].read_bytes() == source.photos[0].read_bytes()
            assert source_slug in shots[index + 1].name
    page = client.get(response.location)
    assert page.status_code == 200
    assert b"Item 2" in page.data
    calls = []

    def build_one(prod, brand, lang, aspects, outroot, args, **kwargs):
        calls.append(prod)
        assert len(script.build(prod, brand, lang)) == 6
        return []

    monkeypatch.setattr(cli, "build_one", build_one)
    result = client.post(response.location, data={"lang": "en", "aspect": "9:16"})
    assert result.status_code == 200
    assert len(calls) == 1
    assert calls[0].slug == slug
    assert originals == [(project / "products" / item / "product.yaml").read_bytes() for item in slugs]


def test_collection_rejects_invalid_selection_without_creating_draft(client, project, photos):
    make_product(project, "empty", PRODUCT, photos, n_photos=0)
    before = set((project / "products").iterdir())
    for selection in ([], ["test-rack"], ["test-rack", "test-rack"],
                      ["test-rack", "../outside"], ["test-rack", "missing"],
                      ["test-rack", "empty"]):
        response = client.post("/collections/new", data=MultiDict(
            [("products", slug) for slug in selection]))
        assert response.status_code == 302
        assert "notice=" in response.location
        assert set((project / "products").iterdir()) == before


def test_dashboard_has_collection_selection(client):
    page = client.get("/").get_data(as_text=True)
    assert 'action="/collections/new"' in page
    assert 'form="collection-form"' in page
    assert 'value="test-rack"' in page
    assert 'name="choose_photos" value="1"' in page


def test_photo_picker_creates_nothing_until_confirmed(client, project, photos):
    make_product(project, "table", dict(PRODUCT, name_en="Table"), photos)
    before = set((project / "products").iterdir())
    response = client.post("/collections/new", data=MultiDict([
        ("products", "test-rack"), ("products", "table"), ("choose_photos", "1")]))
    assert response.status_code == 200
    assert b"Choose your photos" in response.data
    assert b'name="photos_test-rack"' in response.data
    assert b'name="photos_table"' in response.data
    assert set((project / "products").iterdir()) == before


def test_only_selected_photos_and_observations_enter_collection(client, project, photos, monkeypatch):
    make_product(project, "table", dict(PRODUCT, name_en="Table"), photos)
    monkeypatch.setattr(photo_analysis, "prompt_block", lambda p: "Excluded combined summary")
    monkeypatch.setattr(photo_analysis, "status", lambda *a: {
        "fresh": True, "photos": [{"name": f"{i}.jpg", "summary": f"View {i}"} for i in range(1, 4)]})
    response = client.post("/collections/new", data=MultiDict([
        ("products", "test-rack"), ("products", "table"), ("photos_chosen", "1"),
        ("photos_test-rack", "2.jpg"), ("photos_table", "3.jpg")]))
    assert response.status_code == 302
    product = Product.load(project / "products" / response.location.split("/")[-2])
    assert len(product.photos) == 2
    for member, original, description in zip(product.collection_members, ["2.jpg", "3.jpg"], ["View 2", "View 3"]):
        assert list(member["media"]) == [original]
        assert description in member["visual_context"]
        assert "View 1" not in member["visual_context"]
        assert "Excluded combined summary" not in member["visual_context"]
        copied = product.dir / "photos" / member["media"][original]
        assert copied.read_bytes() == (project / "products" / member["slug"] / "photos" / original).read_bytes()
    assert client.get(response.location).status_code == 200


@pytest.mark.parametrize("picked", [None, "../product.yaml", "missing.jpg"])
def test_invalid_photo_selection_preserves_other_choices(client, project, photos, picked):
    make_product(project, "table", dict(PRODUCT, name_en="Table"), photos)
    before = set((project / "products").iterdir())
    data = MultiDict([("products", "test-rack"), ("products", "table"),
                      ("photos_chosen", "1"), ("photos_table", "2.jpg")])
    if picked:
        data.add("photos_test-rack", picked)
    response = client.post("/collections/new", data=data)
    assert response.status_code == 400
    assert b"Choose at least one available photo" in response.data
    assert b'name="photos_table" value="2.jpg" checked' in response.data
    assert set((project / "products").iterdir()) == before


def rich_collection(client, project, photos, monkeypatch):
    path = project / "products" / "test-rack" / "product.yaml"
    facts = read_yaml(path)
    facts.update(specs={"finish": "Rack zinc finish"}, audience="Rack retailers",
                 offer="Rack fitting included", warranty="Rack five-year warranty",
                 proof_points=["Rack certificate 123"], must_say=["Ask for rack fitting"],
                 avoid=["unbreakable"], script_en=["Old rack reference copy"])
    write_yaml(path, facts)
    make_product(project, "table", dict(PRODUCT, name_en="Display Table", name_hi="डिस्प्ले टेबल",
                 price="Rs 999", usp_en=["Table benefit one", "Table benefit two"],
                 specs={"finish": "Table red finish"}, audience="Table shoppers",
                 offer="Table delivery included", warranty="Table two-year warranty",
                 must_say=["Ask for table delivery"], script_en=["Old table reference copy"]), photos)
    monkeypatch.setattr(photo_analysis, "prompt_block", lambda prod: f"Visible appearance of {prod.slug}: 2.jpg")
    response = client.post("/collections/new", data=MultiDict([
        ("products", "test-rack"), ("products", "table")]))
    slug = response.location.split("/")[-2]
    return Product.load(project / "products" / slug)


@pytest.mark.parametrize("writer", ["ai", "local"])
@pytest.mark.parametrize("rewrite", [False, True])
def test_collection_provider_receives_full_context_and_maps_photos(client, project, photos, monkeypatch, writer, rewrite):
    product = rich_collection(client, project, photos, monkeypatch)
    provider = gemini if writer == "ai" else local_llm
    monkeypatch.setattr(provider, "resolve_key", lambda *a: "test-key")
    monkeypatch.setattr(gemini, "resolve_backup_key", lambda *a: None)
    captured = []
    segments = script.build(product, Brand(), "en")
    payload = json.dumps({"segments": [vars(segment) for segment in segments]})

    def generate(*args, **kwargs):
        prompt = args[2]["contents"][0]["parts"][0]["text"] if writer == "ai" else kwargs["messages"][0]["content"]
        captured.append(prompt)
        if writer == "ai":
            return {"candidates": [{"content": {"parts": [{"text": payload}]}}]}
        return {"choices": [{"message": {"content": payload}}]}

    monkeypatch.setattr(provider, "generate_content" if writer == "ai" else "chat_completion", generate)
    data = MultiDict([("lang", "en"), ("script", writer)])
    if rewrite:
        # Reordered old scenes must not put new rack copy over a table photo.
        for member in reversed(product.collection_members):
            data.add("seg_vo_en", "Original draft " + member["facts"]["name_en"])
            data.add("seg_photo_en", list(member["media"].values())[1])
        data["steer"] = "Make it conversational"
    response = client.post(f"/products/{product.slug}/script", data=data)
    assert response.status_code == 200
    assert captured  # The fixed single-product scripts must not bypass the AI.
    for prompt in captured:
        for fact in ("Rack zinc finish", "Rack retailers", "Rack fitting included",
                     "Rack five-year warranty", "Rack certificate 123", "Ask for rack fitting",
                     "unbreakable", "Old rack reference copy", "Table benefit two",
                     "Table red finish", "Table shoppers", "Table delivery included",
                     "Table two-year warranty", "Ask for table delivery", "Old table reference copy",
                     "Rs 999", "Visible appearance of table", "one shared CTA"):
            assert fact in prompt
        if rewrite:
            assert "Original draft Display Table" in prompt and "Make it conversational" in prompt
    from test_web_script import rows
    _, vos, _, picks = rows(response.get_data(as_text=True), "en")
    assert len(vos) == 4
    assert "Test Rack" in vos[1] and "Display Table" in vos[2]
    assert picks[1] in product.collection_members[0]["media"].values()
    assert picks[2] in product.collection_members[1]["media"].values()


def test_collection_context_survives_source_changes_and_keeps_all_media(client, project, photos, monkeypatch):
    product = rich_collection(client, project, photos, monkeypatch)
    assert len(product.photos) == 6
    assert not product.script_en  # A fresh AI draft is not pinned to a template.
    path = project / "products" / "table" / "product.yaml"
    facts = read_yaml(path)
    facts["price"] = "Rs 2000"
    write_yaml(path, facts)
    reloaded = Product.load(product.dir)
    assert reloaded.collection_members[1]["facts"]["price"] == "Rs 999"
    for member in reloaded.collection_members:
        assert len(member["media"]) == 3
        assert all((reloaded.dir / "photos" / name).is_file() for name in member["media"].values())


def test_collection_rejects_missing_or_reordered_products_and_scopes_rules(client, project, photos, monkeypatch):
    product = rich_collection(client, project, photos, monkeypatch)
    brand = Brand()
    segments = script.build(product, brand, "en")
    ad_prompt.validate_segments(segments, product.usp_en, product, brand)
    with pytest.raises(ValueError, match="collection scene must name"):
        ad_prompt.validate_segments([segments[0], segments[2], segments[1], segments[3]], product.usp_en, product, brand)
    with pytest.raises(ValueError, match="expected shape"):
        ad_prompt.validate_segments(segments[1:], product.usp_en, product, brand)
    assert not ad_prompt.check_guardrails(segments, product, "en")
    bad = [segments[0], script.Segment("usp", "Test Rack", "Rack"),
           script.Segment("usp", "Display Table. Ask for rack fitting. Ask for table delivery", "Table"), segments[3]]
    assert any("Ask for rack fitting" in problem for problem in ad_prompt.check_guardrails(bad, product, "en"))


def test_collection_rewrite_keeps_a_matching_alternate_photo(client, project, photos, monkeypatch):
    from reelfactory.collections import scene_photos
    product = rich_collection(client, project, photos, monkeypatch)
    picks = [list(member["media"].values())[1] for member in product.collection_members]
    assert scene_photos(product, [picks[0], *picks, picks[-1]])[1:-1] == picks


def test_collection_render_tracks_reordered_products_and_rejects_wrong_photos(client, project, photos, monkeypatch):
    from reelfactory.collections import render_photos
    product = rich_collection(client, project, photos, monkeypatch)
    segments = script.build(product, Brand(), "en")
    reordered = [segments[0], segments[2], segments[1], segments[3]]
    picks = render_photos(product, reordered)
    assert picks[1] in product.collection_members[1]["media"].values()
    assert picks[2] in product.collection_members[0]["media"].values()
    assert render_photos(product, reordered, picks) == picks
    wrong = [picks[0], picks[2], picks[1], picks[3]]
    with pytest.raises(ValueError, match="another product"):
        render_photos(product, reordered, wrong)
    with pytest.raises(ValueError, match="photo is missing"):
        render_photos(product, reordered, ["deleted.jpg", *picks[1:]])
    from test_end_to_end import Args
    from reelfactory import voice

    def unexpected_voice(*args, **kwargs):
        pytest.fail("A mismatched collection must be rejected before narration is generated")

    monkeypatch.setattr(voice, "synthesize", unexpected_voice)
    with pytest.raises(ValueError, match="another product"):
        cli.build_one(product, Brand(), "en", ["9:16"], project / "out", Args(),
                      segments=reordered, photo_names=wrong)


@pytest.mark.parametrize("lang", ["en", "hi"])
def test_collection_introduces_range_as_one_story(client, project, photos, monkeypatch, lang):
    from dataclasses import replace
    product = rich_collection(client, project, photos, monkeypatch)
    brand = Brand(name="Our Shop", default_intent="sell")
    # Existing collection snapshots without an explicit intent also use awareness.
    assert replace(product, intent="").resolve_intent(brand) == "awareness"
    segments = script.build(product, brand, lang)
    assert [s.role for s in segments] == ["hook", "usp", "usp", "cta"]
    assert "Our Shop" in segments[0].vo
    assert "Rs 999" not in " ".join(s.vo for s in segments)
    prompt = ad_prompt.build_prompt(product, brand, lang, product.usps(lang), "Show the range as a shop visit")
    for direction in ("one connected story", "shared hook", "natural transitions",
                      "do not restart a sales pitch", "guided discovery", "leave out price recitals",
                      "Products to weave into the story", "Show the range as a shop visit"):
        assert direction in prompt
    assert "Selling points to cover, one segment each" not in prompt
    # The introductory scene must not consume the first product's photo slot.
    shots = cli._shot_photos(product, len(segments))
    assert shots[1].name in product.collection_members[0]["media"].values()
    assert shots[2].name in product.collection_members[1]["media"].values()
    ad_prompt.validate_segments(segments, product.usps(lang), product, brand, lang)
