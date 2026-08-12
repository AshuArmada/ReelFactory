"""Brand / Product loading, and the photo ordering they depend on."""
from __future__ import annotations

import pytest

from reelfactory.config import Brand, Product, order_photos

from conftest import make_product, read_yaml, write_yaml


# ------------------------------------------------------------- order_photos


class FakePath:
    """order_photos only ever reads .name, so the unit tests don't need disk."""
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<{self.name}>"


def names(paths):
    return [p.name for p in paths]


def test_natural_order_by_default():
    # "10.jpg" must come after "9.jpg", not between "1" and "2".
    paths = [FakePath(n) for n in ("10.jpg", "2.jpg", "1.jpg", "9.jpg")]
    assert names(order_photos(paths, [])) == ["1.jpg", "2.jpg", "9.jpg", "10.jpg"]


def test_wanted_order_wins():
    paths = [FakePath(n) for n in ("1.jpg", "2.jpg", "3.jpg")]
    assert names(order_photos(paths, ["3.jpg", "1.jpg", "2.jpg"])) == ["3.jpg", "1.jpg", "2.jpg"]


def test_unlisted_photos_follow_in_natural_order():
    # Adding a photo must not require rewriting photo_order.
    paths = [FakePath(n) for n in ("1.jpg", "2.jpg", "3.jpg", "4.jpg")]
    assert names(order_photos(paths, ["3.jpg"])) == ["3.jpg", "1.jpg", "2.jpg", "4.jpg"]


def test_deleted_photo_in_the_list_is_ignored():
    # A stale entry must not resurrect a file or break the load.
    paths = [FakePath(n) for n in ("1.jpg", "2.jpg")]
    assert names(order_photos(paths, ["gone.jpg", "2.jpg"])) == ["2.jpg", "1.jpg"]


def test_duplicate_entries_do_not_duplicate_photos():
    paths = [FakePath(n) for n in ("1.jpg", "2.jpg")]
    assert names(order_photos(paths, ["2.jpg", "2.jpg"])) == ["2.jpg", "1.jpg"]


def test_empty_inputs():
    assert order_photos([], ["1.jpg"]) == []


# ------------------------------------------------------------------ Product


def test_product_photos_follow_photo_order(project):
    prod = project / "products" / "test-rack"
    data = read_yaml(prod / "product.yaml")
    data["photo_order"] = ["3.jpg", "1.jpg"]
    write_yaml(prod / "product.yaml", data)

    assert [p.name for p in Product.load(prod).photos] == ["3.jpg", "1.jpg", "2.jpg"]


def test_product_loads_when_photo_order_names_a_deleted_file(project):
    prod = project / "products" / "test-rack"
    data = read_yaml(prod / "product.yaml")
    data["photo_order"] = ["2.jpg", "1.jpg", "3.jpg"]
    write_yaml(prod / "product.yaml", data)
    (prod / "photos" / "2.jpg").unlink()

    assert [p.name for p in Product.load(prod).photos] == ["1.jpg", "3.jpg"]


def test_photo_order_must_be_a_list(project):
    prod = project / "products" / "test-rack"
    data = read_yaml(prod / "product.yaml")
    data["photo_order"] = "1.jpg"
    write_yaml(prod / "product.yaml", data)

    with pytest.raises(ValueError, match="photo_order"):
        Product.load(prod)


def test_unknown_field_is_still_rejected(project):
    # The strict-unknown-key rule is what forces new settings to be declared;
    # adding photo_order must not have loosened it.
    prod = project / "products" / "test-rack"
    data = read_yaml(prod / "product.yaml")
    data["photo_ordering"] = ["1.jpg"]
    write_yaml(prod / "product.yaml", data)

    with pytest.raises(ValueError, match="photo_ordering"):
        Product.load(prod)


def test_product_without_photos_dir_fails_clearly(project, tmp_path):
    prod = make_product(project, "no-photos", dict(name_en="X", name_hi="X"),
                        lambda d, n: None, n_photos=0)
    with pytest.raises(FileNotFoundError):
        Product.load(prod)


# -------------------------------------------------------------------- Brand


def test_brand_asset_paths_resolve_relative_to_brand_yaml(project):
    (project / "logo").mkdir()
    (project / "logo" / "l.png").write_bytes(b"x")
    data = read_yaml(project / "brand.yaml")
    data["logo"] = "logo/l.png"
    write_yaml(project / "brand.yaml", data)

    brand = Brand.load(project / "brand.yaml")
    assert brand.logo == str((project / "logo" / "l.png").resolve())


def test_brand_refuses_to_point_at_a_missing_asset(project):
    data = read_yaml(project / "brand.yaml")
    data["logo"] = "logo/nope.png"
    write_yaml(project / "brand.yaml", data)

    with pytest.raises(FileNotFoundError, match="logo"):
        Brand.load(project / "brand.yaml")


# ------------------------------------------------------- hand-edited files
#
# brand.yaml and product.yaml are documented as hand-editable, so a typo in
# one is an ordinary event. It has to arrive as ValueError -- what every
# caller already handles -- or it becomes a 500 on the page you would go to
# in order to fix it.


@pytest.mark.parametrize("broken", [
    "name: [oops\n",                    # unclosed flow sequence
    "name: 'unterminated\n",            # unterminated quote
    "a: 1\n b: 2\n",                    # bad indentation
    "\ttabs: are illegal\n",
])
def test_malformed_yaml_is_a_value_error(project, broken):
    (project / "brand.yaml").write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        Brand.load(project / "brand.yaml")
    assert "brand.yaml" in str(exc.value)


def test_the_error_points_at_the_line(project):
    (project / "brand.yaml").write_text("name: ok\nbad: [1, 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line"):
        Brand.load(project / "brand.yaml")


def test_malformed_product_yaml_is_a_value_error(project):
    spec = project / "products" / "test-rack" / "product.yaml"
    spec.write_text("name_en: [oops\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Product.load(spec.parent)


def test_a_yaml_file_that_is_not_a_mapping_is_rejected(project):
    (project / "brand.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        Brand.load(project / "brand.yaml")


def test_an_empty_file_loads_as_defaults(project):
    (project / "brand.yaml").write_text("", encoding="utf-8")
    assert Brand.load(project / "brand.yaml").name == "Your Brand"


def test_blank_asset_is_not_treated_as_a_path(project):
    # The web UI writes "" when you remove a logo; that must load, not raise.
    data = read_yaml(project / "brand.yaml")
    data["logo"] = ""
    write_yaml(project / "brand.yaml", data)

    assert Brand.load(project / "brand.yaml").logo == ""
