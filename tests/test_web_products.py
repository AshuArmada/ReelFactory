"""Dashboard, product CRUD, duplicate/delete, and photo ordering."""
from __future__ import annotations

from reelfactory.config import Product

from conftest import field_values, form, make_product, read_yaml, write_yaml


# ----------------------------------------------------------------- dashboard


def test_dashboard_lists_products_and_readiness(client):
    html = client.get("/").get_data(as_text=True)
    assert "Test Rack" in html
    assert 'class="check-list"' in html


def test_dashboard_survives_a_broken_product(client, project, photos):
    make_product(project, "broken", {"name_en": "X"}, photos)   # no name_hi
    html = client.get("/").get_data(as_text=True)
    assert "broken" in html
    assert "Test Rack" in html          # the good one still renders


def test_dashboard_survives_a_broken_brand(client, project):
    (project / "brand.yaml").write_text("name: [oops\n", encoding="utf-8")
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "couldn't be loaded" in html
    assert "brand.yaml" in html                     # says which file
    assert "/brand" in html                         # and offers the way to fix it


def test_notice_is_shown(client):
    assert "Hello there" in client.get("/?notice=Hello+there").get_data(as_text=True)


# ------------------------------------------------------------------- create


def test_create_requires_a_slug(client):
    resp = client.post("/products/new", data={"name_en": "A", "name_hi": "B"})
    assert resp.status_code == 400


def test_create_rejects_a_duplicate_slug(client):
    resp = client.post("/products/new", data={"slug": "test-rack", "name_en": "A", "name_hi": "B"})
    assert resp.status_code == 400
    assert "already exists" in resp.get_data(as_text=True)


def test_create_then_edit_round_trip(client, project):
    client.post("/products/new", data={"slug": "new-thing", "name_en": "New", "name_hi": "नया"})
    data = read_yaml(project / "products" / "new-thing" / "product.yaml")
    assert data["name_en"] == "New"
    assert (project / "products" / "new-thing" / "photos").is_dir()


# ---------------------------------------------------------------- duplicate


def test_duplicate_copies_details_and_photos(client, project):
    resp = client.post("/products/test-rack/duplicate", data={"new_slug": "rack-two"})
    copy = project / "products" / "rack-two"
    assert resp.status_code == 302 and "rack-two" in resp.headers["Location"]
    assert read_yaml(copy / "product.yaml")["name_en"] == "Test Rack"
    assert sorted(p.name for p in (copy / "photos").iterdir()) == ["1.jpg", "2.jpg", "3.jpg"]


def test_duplicate_without_a_name_picks_the_next_free_one(client, project):
    client.post("/products/test-rack/duplicate", data={})
    client.post("/products/test-rack/duplicate", data={})
    assert (project / "products" / "test-rack-copy").is_dir()
    assert (project / "products" / "test-rack-copy-2").is_dir()


def test_duplicate_refuses_an_existing_name(client, project):
    resp = client.post("/products/test-rack/duplicate", data={"new_slug": "test-rack"})
    # The notice travels as a query string, so spaces arrive as '+'.
    assert "already+exists" in resp.headers["Location"]


def test_duplicate_does_not_copy_finished_videos(client, project):
    out = project / "out" / "test-rack"
    out.mkdir(parents=True)
    (out / "test-rack_hi_9x16.mp4").write_bytes(b"x")
    client.post("/products/test-rack/duplicate", data={"new_slug": "rack-two"})
    assert not (project / "out" / "rack-two").exists()


def test_duplicate_of_a_missing_product_is_404(client):
    assert client.post("/products/nope/duplicate", data={}).status_code == 404


# ------------------------------------------------------------------- delete


def test_delete_requires_the_name_typed_back(client, project):
    resp = client.post("/products/test-rack/delete", data={"confirm_slug": "test-rac"})
    assert (project / "products" / "test-rack").is_dir()
    assert "Nothing+was+deleted" in resp.headers["Location"]


def test_delete_with_no_confirmation_at_all_is_refused(client, project):
    client.post("/products/test-rack/delete", data={})
    assert (project / "products" / "test-rack").is_dir()


def test_delete_removes_the_product(client, project):
    resp = client.post("/products/test-rack/delete", data={"confirm_slug": "test-rack"})
    assert not (project / "products" / "test-rack").exists()
    assert "Deleted" in resp.headers["Location"]


def test_delete_keeps_finished_videos_unless_asked(client, project):
    out = project / "out" / "test-rack"
    out.mkdir(parents=True)
    (out / "v.mp4").write_bytes(b"x")

    client.post("/products/test-rack/delete", data={"confirm_slug": "test-rack"})
    assert (out / "v.mp4").exists()


def test_delete_can_take_the_videos_too(client, project):
    out = project / "out" / "test-rack"
    out.mkdir(parents=True)
    (out / "v.mp4").write_bytes(b"x")

    client.post("/products/test-rack/delete",
                data={"confirm_slug": "test-rack", "delete_outputs": "on"})
    assert not out.exists()


def test_delete_of_a_missing_product_is_404(client):
    assert client.post("/products/nope/delete", data={"confirm_slug": "nope"}).status_code == 404


def test_delete_cannot_escape_the_products_folder(client, project):
    for slug in ("../..", "..%2f..", "..", "sub/dir", "%2e%2e"):
        resp = client.post(f"/products/{slug}/delete", data={"confirm_slug": slug})
        assert resp.status_code in (404, 308), slug
    assert (project / "brand.yaml").exists()
    assert (project / "products" / "test-rack").is_dir()


def test_only_the_canonical_slug_is_accepted(client, project):
    # Every link in the app is built from a real slug. Anything that merely
    # normalises to one didn't come from this UI -- and on a case-insensitive
    # filesystem "Test-Rack" would otherwise delete test-rack.
    for slug in ("Test-Rack", "test_rack", " test-rack"):
        resp = client.post(f"/products/{slug}/delete", data={"confirm_slug": slug})
        assert resp.status_code == 404, slug
    assert (project / "products" / "test-rack").is_dir()


# ------------------------------------------------------------- photo order


def base_edit_form(*extra):
    return form(("name_en", "Test Rack"), ("name_hi", "टेस्ट रैक"), *extra)


def test_positions_are_written_as_photo_order(client, project):
    data = base_edit_form(
        ("photo_name", "1.jpg"), ("photo_pos", "3"),
        ("photo_name", "2.jpg"), ("photo_pos", "1"),
        ("photo_name", "3.jpg"), ("photo_pos", "2"),
    )
    client.post("/products/test-rack/edit", data=data)

    spec = read_yaml(project / "products" / "test-rack" / "product.yaml")
    assert spec["photo_order"] == ["2.jpg", "3.jpg", "1.jpg"]
    assert [p.name for p in Product.load(project / "products" / "test-rack").photos] \
        == ["2.jpg", "3.jpg", "1.jpg"]


def test_the_edit_page_shows_photos_in_the_saved_order(client, project):
    client.post("/products/test-rack/edit", data=base_edit_form(
        ("photo_name", "1.jpg"), ("photo_pos", "2"),
        ("photo_name", "2.jpg"), ("photo_pos", "1"),
        ("photo_name", "3.jpg"), ("photo_pos", "3"),
    ))
    html = client.get("/products/test-rack/edit").get_data(as_text=True)
    assert field_values(html, "photo_name") == ["2.jpg", "1.jpg", "3.jpg"]
    # Positions are renumbered 1..n on the way out, never left as typed.
    assert field_values(html, "photo_pos") == ["1", "2", "3"]


def test_unparseable_positions_keep_their_place(client, project):
    client.post("/products/test-rack/edit", data=base_edit_form(
        ("photo_name", "1.jpg"), ("photo_pos", ""),
        ("photo_name", "2.jpg"), ("photo_pos", "abc"),
        ("photo_name", "3.jpg"), ("photo_pos", "3"),
    ))
    order = read_yaml(project / "products" / "test-rack" / "product.yaml")["photo_order"]
    assert order == ["1.jpg", "2.jpg", "3.jpg"]


def test_duplicate_positions_break_the_tie_by_document_order(client, project):
    client.post("/products/test-rack/edit", data=base_edit_form(
        ("photo_name", "3.jpg"), ("photo_pos", "1"),
        ("photo_name", "1.jpg"), ("photo_pos", "1"),
        ("photo_name", "2.jpg"), ("photo_pos", "1"),
    ))
    order = read_yaml(project / "products" / "test-rack" / "product.yaml")["photo_order"]
    assert order == ["3.jpg", "1.jpg", "2.jpg"]


def test_deleting_a_photo_drops_it_from_the_order(client, project):
    client.post("/products/test-rack/edit", data=base_edit_form(
        ("photo_name", "1.jpg"), ("photo_pos", "1"),
        ("photo_name", "2.jpg"), ("photo_pos", "2"),
        ("photo_name", "3.jpg"), ("photo_pos", "3"),
        ("delete_photo", "2.jpg"),
    ))
    spec = read_yaml(project / "products" / "test-rack" / "product.yaml")
    assert spec["photo_order"] == ["1.jpg", "3.jpg"]
    assert not (project / "products" / "test-rack" / "photos" / "2.jpg").exists()


def test_an_order_naming_a_file_not_on_disk_is_ignored(client, project):
    client.post("/products/test-rack/edit", data=base_edit_form(
        ("photo_name", "../../brand.yaml"), ("photo_pos", "1"),
        ("photo_name", "1.jpg"), ("photo_pos", "2"),
    ))
    spec = read_yaml(project / "products" / "test-rack" / "product.yaml")
    assert spec["photo_order"] == ["1.jpg"]


def test_saving_with_no_photo_fields_clears_the_order(client, project):
    prod = project / "products" / "test-rack"
    data = read_yaml(prod / "product.yaml")
    data["photo_order"] = ["3.jpg"]
    write_yaml(prod / "product.yaml", data)

    client.post("/products/test-rack/edit", data=base_edit_form())
    assert "photo_order" not in read_yaml(prod / "product.yaml")


def test_editing_other_fields_does_not_lose_the_order(client, project):
    client.post("/products/test-rack/edit", data=base_edit_form(
        ("price", "Rs 1"),
        ("photo_name", "3.jpg"), ("photo_pos", "1"),
        ("photo_name", "1.jpg"), ("photo_pos", "2"),
        ("photo_name", "2.jpg"), ("photo_pos", "3"),
    ))
    spec = read_yaml(project / "products" / "test-rack" / "product.yaml")
    assert spec["price"] == "Rs 1"
    assert spec["photo_order"] == ["3.jpg", "1.jpg", "2.jpg"]
