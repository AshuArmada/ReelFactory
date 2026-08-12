"""The script editor: writing, comparing, picking, saving -- and above all
keeping each line's words and its photo attached to each other.

The failure this file mostly guards against is silent: a photo that drifts one
row out of step is not an error anywhere, it just makes a video where the
words describe the wrong picture.
"""
from __future__ import annotations

import re

from conftest import form, live_html, read_yaml, selected_photos


WRITE = "/products/test-rack/script"
VARIANTS = "/products/test-rack/script/variants"
PICK = "/products/test-rack/script/pick"
SAVE = "/products/test-rack/script/save"
LOAD = "/products/test-rack/script/load"
DELETE_SAVED = "/products/test-rack/script/saved/delete"


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
    client.post(SAVE, data=form(
        *[(k, v) for k, v in editor_form(vos, flipped).items(multi=True)],
        ("save_name", "flipped"),
    ))
    saved = read_yaml(project / "products" / "test-rack" / "saved_scripts.yaml")
    assert [s["photo"] for s in saved["hi"][0]["segments"]] == flipped


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
    client.post(DELETE_SAVED, data={"delete_pick": "hi:0"})
    assert read_yaml(project / "products" / "test-rack" / "saved_scripts.yaml") == {}


def test_loading_a_missing_saved_script_says_so(client):
    resp = client.post(LOAD, data={"lang": "hi", "load_pick": "hi:9"})
    assert resp.status_code == 400
    assert "could not be found" in resp.get_data(as_text=True)


# ------------------------------------------------------------------- errors


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
