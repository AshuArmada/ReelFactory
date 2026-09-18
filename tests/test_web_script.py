"""The script editor: writing, comparing, picking, saving -- and above all
keeping each line's words and its photo attached to each other.

The failure this file mostly guards against is silent: a photo that drifts one
row out of step is not an error anywhere, it just makes a video where the
words describe the wrong picture.
"""
from __future__ import annotations

import re
import pytest

from conftest import form, live_html, read_yaml, selected_photos


WRITE = "/products/test-rack/script"
VARIANTS = "/products/test-rack/script/variants"
PICK = "/products/test-rack/script/pick"
SAVE = "/products/test-rack/script/save"
LOAD = "/products/test-rack/script/load"
DELETE_SAVED = "/products/test-rack/script/saved/delete"
CLEAR_SAVED = "/products/test-rack/script/saved/clear"
CLEAR_DRAFT = "/products/test-rack/script/clear"


def write_script(client, lang="hi"):
    return client.post(WRITE, data={"lang": lang, "script": "template"}).get_data(as_text=True)


def rows(html, lang="hi", prefix=""):
    """(roles, vos, overlays, photos) as the page currently shows them."""
    live = live_html(html)
    return (
        re.findall(rf'name="{prefix}seg_role_{lang}"[^>]*>(.*?)</select>', live, re.S),
        re.findall(rf'<textarea name="{prefix}seg_vo_{lang}"[^>]*>(.*?)</textarea>', live, re.S),
        re.findall(rf'name="{prefix}seg_overlay_{lang}" value="([^"]*)"', live),
        selected_photos(html),
    )


def editor_form(vos, photos, lang="hi", overlays=None, roles=None):
    pairs = []
    for i, vo in enumerate(vos):
        pairs += [
            (f"seg_role_{lang}", (roles or ["custom"] * len(vos))[i]),
            (f"seg_vo_{lang}", vo),
            (f"seg_overlay_{lang}", (overlays or [""] * len(vos))[i]),
            (f"seg_photo_{lang}", photos[i]),
        ]
    return form(("lang", lang), ("script", "template"), *pairs)


# ------------------------------------------------------------------ writing


def test_a_fresh_script_pairs_every_line_with_a_photo(client):
    html = write_script(client)
    _roles, vos, _ov, photos = rows(html)
    assert len(vos) >= 3
    assert len(photos) == len(vos)
    # The default is the cycle the render would have used anyway.
    assert photos[:3] == ["1.jpg", "2.jpg", "3.jpg"]


def test_photos_cycle_when_there_are_more_lines_than_photos(client):
    _r, vos, _o, photos = rows(write_script(client))
    expected = [f"{(i % 3) + 1}.jpg" for i in range(len(vos))]
    assert photos == expected


def test_both_languages_get_their_own_rows(client):
    html = client.post(WRITE, data={"lang": ["hi", "en"], "script": "template"}).get_data(as_text=True)
    live = live_html(html)
    assert 'name="seg_photo_hi"' in live and 'name="seg_photo_en"' in live


def test_the_editor_offers_every_photo_for_every_line(client):
    live = live_html(write_script(client))
    block = re.search(r'<select name="seg_photo_hi".*?</select>', live, re.S).group(0)
    assert block.count("<option") == 3


def test_rewrite_sends_current_draft_and_only_changes_requested_language(client, monkeypatch):
    from reelfactory import cli
    from reelfactory.script import Segment
    seen = []

    def rewrite(prod, brand, lang, args):
        seen.append((lang, args.steer))
        return [Segment("hook", "New opening", "New")]

    monkeypatch.setattr(cli, "_build_segments", rewrite)
    data = editor_form(["Keep this opening"], ["3.jpg"])
    data.setlist("lang", ["hi", "en"])
    data["script"] = "ai"
    data["steer"] = "Keep the opening and shorten the rest"
    data["rewrite_lang"] = "hi"
    data["seg_vo_en"] = "Untouched English"
    data["seg_photo_en"] = "2.jpg"
    html = client.post(WRITE, data=data).get_data(as_text=True)
    assert len(seen) == 1 and seen[0][0] == "hi"
    assert "Keep this opening" in seen[0][1] and data["steer"] in seen[0][1]
    assert "Untouched English" in html
    assert selected_photos(html) == ["3.jpg", "2.jpg"]


def test_template_instructions_show_actionable_error_and_keep_draft(client):
    data = editor_form(["Keep me"], ["2.jpg"])
    data["steer"] = "Make it shorter"
    html = client.post(WRITE, data=data).get_data(as_text=True)
    assert "Choose Gemini or Local model" in html
    assert "Keep me" in html and selected_photos(html) == ["2.jpg"]


@pytest.mark.parametrize("writer, selected", [("ai", "ai"), ("grok", "template")])
def test_saved_script_restores_instructions_writer_and_language(client, writer, selected):
    data = editor_form(["English draft"], ["3.jpg"], lang="en")
    data["script"] = writer
    data["steer"] = "Use a friendly tone"
    data["save_name"] = "Friendly"
    client.post(SAVE, data=data)
    html = client.post(LOAD, data={"load_pick": "en:0", "lang": "hi"}).get_data(as_text=True)
    assert "Use a friendly tone" in html
    assert re.search(rf'value="{selected}"[^>]*checked', html)
    assert 'value="grok"' not in html
    assert re.search(r'name="lang" value="en"[^>]*checked', html)
    assert selected_photos(html) == ["3.jpg"]


# ------------------------------------------------------ editing round trips


def test_a_chosen_photo_survives_a_rewrite_request(client):
    _r, vos, _o, photos = rows(write_script(client))
    flipped = list(reversed(photos))

    html = client.post(WRITE, data=editor_form(vos, flipped)).get_data(as_text=True)
    _r2, vos2, _o2, photos2 = rows(html)
    # New words, but the pictures the user picked are still on the same rows.
    assert photos2[:len(vos2)] == flipped[:len(vos2)]


def test_blanking_a_line_drops_that_line_and_its_photo_together(client):
    _r, vos, _o, photos = rows(write_script(client))
    edited = list(vos)
    edited[1] = "   "                       # user clears the second line

    html = client.post(SAVE, data=form(
        *[(k, v) for k, v in editor_form(edited, photos).items(multi=True)],
        ("save_name", "x"),
    )).get_data(as_text=True)

    _r2, vos2, _o2, photos2 = rows(html)
    assert len(vos2) == len(vos) - 1
    assert photos2 == [p for i, p in enumerate(photos) if i != 1]


def test_blanking_the_first_line_shifts_nothing_downstream(client):
    _r, vos, _o, photos = rows(write_script(client))
    edited = list(vos)
    edited[0] = ""

    html = client.post(SAVE, data=form(
        *[(k, v) for k, v in editor_form(edited, photos).items(multi=True)],
        ("save_name", "x"),
    )).get_data(as_text=True)
    assert rows(html)[3] == photos[1:]


def test_an_unknown_photo_name_falls_back_instead_of_erroring(client):
    _r, vos, _o, photos = rows(write_script(client))
    broken = ["deleted.jpg"] + photos[1:]

    html = client.post(WRITE, data=editor_form(vos, broken)).get_data(as_text=True)
    assert rows(html)[3][0] in ("1.jpg", "2.jpg", "3.jpg")


# --------------------------------------------------------- compare and pick


def test_variants_each_carry_their_own_photos(client):
    html = client.post(VARIANTS, data={"lang": "hi", "script": "template"}).get_data(as_text=True)
    for idx in (0, 1, 2):
        vos = re.findall(rf'name="ver{idx}_seg_vo_hi" value="([^"]*)"', html)
        pics = re.findall(rf'name="ver{idx}_seg_photo_hi" value="([^"]*)"', html)
        if not vos:
            continue
        assert len(pics) == len(vos), f"version {idx} lost a photo"


def test_picking_one_version_carries_its_exact_photos_into_the_editor(client):
    html = client.post(VARIANTS, data={"lang": "hi", "script": "template"}).get_data(as_text=True)
    fields = {f: re.findall(rf'name="ver0_seg_{f}_hi" value="([^"]*)"', html)
              for f in ("role", "vo", "overlay", "photo")}
    flipped = list(reversed(fields["photo"]))

    pairs = [("lang", "hi"), ("script", "template"), ("pick_hi", "0")]
    for i in range(len(fields["vo"])):
        pairs += [("ver0_seg_role_hi", fields["role"][i]),
                  ("ver0_seg_vo_hi", fields["vo"][i]),
                  ("ver0_seg_overlay_hi", fields["overlay"][i]),
                  ("ver0_seg_photo_hi", flipped[i])]

    picked = client.post(PICK, data=form(*pairs)).get_data(as_text=True)
    assert selected_photos(picked) == flipped


def test_picking_several_versions_becomes_a_multi_build(client):
    html = client.post(VARIANTS, data={"lang": "hi", "script": "template"}).get_data(as_text=True)
    pairs = [("lang", "hi"), ("script", "template"), ("pick_hi", "0"), ("pick_hi", "1")]
    for idx in (0, 1):
        for f in ("role", "vo", "overlay", "photo"):
            for v in re.findall(rf'name="ver{idx}_seg_{f}_hi" value="([^"]*)"', html):
                pairs.append((f"ver{idx}_seg_{f}_hi", v))

    resp = client.post(PICK, data=form(*pairs)).get_data(as_text=True)
    assert 'id="multi-summary"' in resp
    # Count the fields, not the string: the page's own JS also mentions it.
    assert resp.count('<input type="hidden" name="build_versions"') == 2
    # Each queued video keeps its own photo assignment.
    assert 'name="ver0_seg_photo_hi"' in resp and 'name="ver1_seg_photo_hi"' in resp


# ------------------------------------------------------------ saved scripts


def test_saving_stores_the_photos_too(client, project):
    _r, vos, _o, photos = rows(write_script(client))
    flipped = list(reversed(photos))
    response = client.post(SAVE, data=form(
        *[(k, v) for k, v in editor_form(vos, flipped).items(multi=True)],
        ("save_name", "flipped"),
    ))
    saved = read_yaml(project / "products" / "test-rack" / "saved_scripts.yaml")
    assert [s["photo"] for s in saved["hi"][0]["segments"]] == flipped
    assert saved["hi"][0]["writer"] == "template"
    assert "Saved scripts for test-rack" in response.get_data(as_text=True)


def test_loading_a_saved_script_restores_its_photos(client):
    _r, vos, _o, photos = rows(write_script(client))
    flipped = list(reversed(photos))
    client.post(SAVE, data=form(
        *[(k, v) for k, v in editor_form(vos, flipped).items(multi=True)],
        ("save_name", "flipped"),
    ))
    html = client.post(LOAD, data={"lang": "hi", "load_pick": "hi:0"}).get_data(as_text=True)
    assert selected_photos(html) == flipped


def test_saving_needs_a_name(client, project):
    _r, vos, _o, photos = rows(write_script(client))
    resp = client.post(SAVE, data=editor_form(vos, photos))
    assert "Give the script a name" in resp.get_data(as_text=True)
    assert not (project / "products" / "test-rack" / "saved_scripts.yaml").exists()


def test_saving_nothing_is_refused(client):
    resp = client.post(SAVE, data=form(("lang", "hi"), ("save_name", "empty")))
    assert "nothing to save" in resp.get_data(as_text=True)


def test_deleting_a_saved_script(client, project):
    _r, vos, _o, photos = rows(write_script(client))
    client.post(SAVE, data=form(
        *[(k, v) for k, v in editor_form(vos, photos).items(multi=True)],
        ("save_name", "one"),
    ))
    html = client.post(DELETE_SAVED, data={"delete_pick": "hi:0"}).get_data(as_text=True)
    assert read_yaml(project / "products" / "test-rack" / "saved_scripts.yaml") == {}
    assert 'data-start-step="1"' in html
    assert "Saved script deleted." in html


def test_deleting_preserves_unsaved_draft_instructions_and_images(client, monkeypatch):
    from reelfactory import cli
    data = editor_form(["Library copy"], ["1.jpg"])
    data["save_name"] = "Saved version"
    client.post(SAVE, data=data)
    data = editor_form(["Unsaved opening", "", "Closing"], ["3.jpg", "2.jpg", "1.jpg"])
    data.update({"delete_pick": "hi:0", "steer": "Keep the opening", "save_name": "Next version",
                 "tts": "gemini", "voice_delivery": "Warm and relaxed"})
    monkeypatch.setattr(cli, "_build_segments", lambda *a, **k: pytest.fail("Deletion must not generate a script"))
    html = client.post(DELETE_SAVED, data=data).get_data(as_text=True)
    assert rows(html)[1] == ["Unsaved opening", "", "Closing"]
    assert selected_photos(html) == ["3.jpg", "2.jpg", "1.jpg"]
    for text in ('data-start-step="1"', "Keep the opening", 'value="Next version"', "Warm and relaxed"):
        assert text in html


@pytest.mark.parametrize("multi", [False, True])
def test_delete_preserves_comparison_or_build_versions(client, multi):
    data = editor_form(["Library copy"], ["1.jpg"])
    data["save_name"] = "Saved version"
    client.post(SAVE, data=data)
    data = form(("lang", "hi"), ("delete_pick", "hi:0"), ("steer", "Make it friendly"))
    for idx in range(2):
        data[f"ver{idx}_seg_vo_hi"] = f"Draft {idx}"
        data[f"ver{idx}_seg_photo_hi"] = f"{idx + 1}.jpg"
        if multi:
            data.add("build_versions", f"hi:{idx}")
    data["pick_hi"] = "1"
    html = client.post(DELETE_SAVED, data=data).get_data(as_text=True)
    assert 'data-start-step="1"' in html
    assert 'name="steer" value="Make it friendly"' in html
    for idx in range(2):
        assert f'name="ver{idx}_seg_vo_hi" value="Draft {idx}"' in html
    if multi:
        assert 'id="multi-summary"' in html
        assert html.count('<input type="hidden" name="build_versions"') == 2
    else:
        assert 'id="version-picker"' in html
        assert re.search(r'name="pick_hi" value="1"[^>]*checked', html)
        assert not re.search(r'name="pick_hi" value="0"[^>]*checked', html)


@pytest.mark.parametrize("writer", ["ai", "local"])
@pytest.mark.parametrize("endpoint", [WRITE, VARIANTS])
def test_rewrite_provider_receives_original_script_and_full_brief(client, project, monkeypatch, writer, endpoint):
    from reelfactory import gemini, local_llm, photo_analysis
    from conftest import write_yaml
    path = project / "products" / "test-rack" / "product.yaml"
    product = read_yaml(path)
    product.update(audience="Small shop owners", target_seconds=45,
                   must_say=["Ask for a demo"], avoid=["Unverified claim"],
                   script_en=["Pinned script must be revisable"])
    write_yaml(path, product)
    monkeypatch.setattr(photo_analysis, "prompt_block", lambda prod: "Photo notes: front view of the rack")
    provider = {"ai": gemini, "local": local_llm}[writer]
    monkeypatch.setattr(provider, "resolve_key", lambda *a: "test-key")
    monkeypatch.setattr(gemini, "resolve_backup_key", lambda *a: None)
    captured = []

    def capture(*args, **kwargs):
        captured.append(args[2]["contents"][0]["parts"][0]["text"] if writer == "ai"
                        else kwargs["messages"][0]["content"])
        raise {"ai": gemini.GeminiError, "local": local_llm.LocalLLMError}[writer]("Stopped after capture")

    monkeypatch.setattr(provider, "generate_content" if writer == "ai" else "chat_completion", capture)
    data = editor_form(["Original opening", "Original closing"], ["3.jpg", "1.jpg"], lang="en",
                       overlays=["Opening overlay", "Closing overlay"])
    data["script"] = writer
    data["steer"] = "Keep the opening and shorten the rest"
    html = client.post(endpoint, data=data).get_data(as_text=True)
    assert len(captured) == 1
    for text in ("Original opening", "Original closing", "Opening overlay", '"photo": "3.jpg"',
                 "Keep the opening and shorten the rest", "Test Rack", "Rs 4,499", "Test Steel Works",
                 "Small shop owners", "45-second", "Ask for a demo", "Unverified claim",
                 "Photo notes: front view of the rack", "Holds 150 kilos per shelf"):
        assert text in captured[0]
    assert "Original opening" in html  # A failed provider keeps the editable draft.


def test_loading_a_missing_saved_script_says_so(client):
    resp = client.post(LOAD, data={"lang": "hi", "load_pick": "hi:9"})
    assert resp.status_code == 400
    assert "could not be found" in resp.get_data(as_text=True)


def test_empty_comparison_selection_preserves_versions(client):
    data = form(("lang", "hi"), ("ver0_seg_vo_hi", "Keep this version"),
                ("ver0_seg_photo_hi", "3.jpg"), ("steer", "Keep the opening"))
    response = client.post(PICK, data=data)
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert 'id="version-picker"' in html
    assert 'name="ver0_seg_vo_hi" value="Keep this version"' in html
    assert 'name="ver0_seg_photo_hi" value="3.jpg"' in html
    assert "Choose at least one version" in html


def test_invalid_saved_selection_preserves_working_draft(client):
    data = editor_form(["Unsaved opening"], ["3.jpg"])
    data["load_pick"] = "hi:-1"
    response = client.post(LOAD, data=data)
    assert response.status_code == 400
    assert rows(response.get_data(as_text=True))[1] == ["Unsaved opening"]


# ------------------------------------------------------------------- errors


def test_clear_saved_scripts_is_product_scoped_and_preserves_work(client, project):
    from conftest import write_yaml
    for lang in ("hi", "en"):
        data = editor_form(["Saved words"], ["1.jpg"], lang=lang)
        data["save_name"] = "A version"
        client.post(SAVE, data=data)
    other = project / "products" / "other" / "saved_scripts.yaml"
    write_yaml(other, {"hi": [{"name": "Keep this"}]})
    data = editor_form(["Unsaved words", ""], ["3.jpg", "2.jpg"])
    data["steer"] = "Make it shorter"
    html = client.post(CLEAR_SAVED, data=data).get_data(as_text=True)
    assert read_yaml(project / "products" / "test-rack" / "saved_scripts.yaml") == {}
    assert read_yaml(other) == {"hi": [{"name": "Keep this"}]}
    assert rows(html)[1] == ["Unsaved words", ""]
    assert selected_photos(html) == ["3.jpg", "2.jpg"]
    assert "Make it shorter" in html
    assert 'data-start-step="1"' in html
    assert "All saved scripts for this product cleared" in html


def test_clear_draft_keeps_library_and_build_settings(client, project):
    data = editor_form(["Saved words"], ["1.jpg"])
    data["save_name"] = "Keep this version"
    client.post(SAVE, data=data)
    path = project / "products" / "test-rack" / "saved_scripts.yaml"
    before = path.read_bytes()
    data["steer"] = "Discard these instructions"
    data["tts"] = "gemini"
    html = client.post(CLEAR_DRAFT, data=data).get_data(as_text=True)
    assert path.read_bytes() == before
    assert 'id="script-preview"' not in html
    assert 'data-start-step="1"' in html
    assert "Current draft cleared" in html
    assert "Discard these instructions" not in html
    assert re.search(r'value="gemini"[^>]*selected', html)
    assert 'id="preview-btn"' in html


def test_clear_failure_keeps_library_and_logs_error(client, project, monkeypatch):
    import importlib
    web = importlib.import_module("reelfactory.web.app")
    data = editor_form(["Saved words"], ["1.jpg"])
    data["save_name"] = "Keep this version"
    client.post(SAVE, data=data)
    path = project / "products" / "test-rack" / "saved_scripts.yaml"
    before = path.read_bytes()

    def fail_write(*args, **kwargs):
        raise PermissionError("Cannot write saved scripts")

    monkeypatch.setattr(web, "write_yaml", fail_write)
    html = client.post(CLEAR_SAVED, data=data).get_data(as_text=True)
    assert path.read_bytes() == before
    assert "Could not update the saved scripts" in html
    assert "All saved scripts for this product cleared" not in html
    assert "PermissionError" in (project / "logs" / "reelfactory.log").read_text(encoding="utf-8")


def test_clear_controls_use_post(client):
    assert client.get(CLEAR_SAVED).status_code == 405
    assert client.get(CLEAR_DRAFT).status_code == 405


def test_a_broken_product_is_reported_not_crashed(client, project):
    (project / "products" / "test-rack" / "product.yaml").write_text(
        "name_en: X\nbogus_field: 1\n", encoding="utf-8")
    resp = client.post(WRITE, data={"lang": "hi", "script": "template"})
    assert resp.status_code == 400
    assert "bogus_field" in resp.get_data(as_text=True)


def test_a_failed_rewrite_keeps_the_draft_on_screen(client, monkeypatch):
    _r, vos, _o, photos = rows(write_script(client))

    import reelfactory.cli as rf_cli
    monkeypatch.setattr(rf_cli, "_build_segments",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("writer exploded")))

    html = client.post(WRITE, data=editor_form(vos, photos)).get_data(as_text=True)
    assert "writer exploded" in html
    assert selected_photos(html) == photos          # the user's work is still there
