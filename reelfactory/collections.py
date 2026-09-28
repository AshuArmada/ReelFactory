"""Product-scoped context snapshots for collection reels."""
from __future__ import annotations

from dataclasses import fields
import json
import shutil

from . import photo_analysis
from .config import Product, write_yaml


def snapshot(product: Product, media: dict[str, str]) -> dict:
    facts = {
        field.name: getattr(product, field.name)
        for field in fields(Product)
        if field.name not in {"slug", "dir", "photos", "photo_order", "collection_members"}
    }
    return {
        "slug": product.slug, "facts": facts, "media": media,
        "visual_context": photo_analysis.prompt_block(product),
    }


def create_draft(selected: list[Product], name: str, draft) -> None:
    (draft / "photos").mkdir()
    members, photo_order = [], []
    for index, product in enumerate(selected, 1):
        media = {}
        for photo_index, photo in enumerate(product.photos, 1):
            filename = f"{index:04d}-{product.slug}-{photo_index:04d}{photo.suffix.lower()}"
            shutil.copy2(photo, draft / "photos" / filename)
            media[photo.name] = filename
            photo_order.append(filename)
        members.append(snapshot(product, media))
    data = {
        "name_en": name, "name_hi": name, "photo_order": photo_order,
        "collection_members": members,
        "target_seconds": max(35, len(members) * 8 + 5),
        "usp_en": [p.name_en for p in selected],
        "usp_hi": [p.name_hi for p in selected],
    }
    write_yaml(draft / "product.yaml", data)
    write_yaml(draft / "collection.yaml", {"products": [p.slug for p in selected]})


def member_product(member: dict, collection: Product) -> Product:
    return Product(slug=member["slug"], dir=collection.dir, photos=[], **member["facts"])


def prompt_block(product: Product) -> str:
    if not product.collection_members:
        return ""
    return "\n".join([
        "COLLECTION PRODUCT CONTEXT (one separate record per selected product):",
        "Cover EVERY product in this exact order, one usp scene per product, then one shared CTA.",
        "Each scene must say that product's exact name in the requested language.",
        "Use all supplied selling points, specs, offers, audience, tone and proof as context;",
        "select the most relevant details for the duration. Never transfer a price, offer,",
        "warranty, specification or claim from one product to another or invent a bundle deal.",
        "Apply each product's must_say and avoid rules to its scene. Use the collection goal",
        "and a single brand CTA; individual CTA settings are background context only.",
        "script_* and overlay_* are reference copy, not verified facts or fixed scripts.",
        "Visual observations describe appearance only; the product facts take precedence.",
        "The media mapping links original photo names in observations to collection filenames.",
        json.dumps(product.collection_members, ensure_ascii=False, default=str),
    ])


def scene_photos(product: Product, previous=None) -> list[str]:
    """Keep a chosen photo only when it belongs to that scene's product."""
    available = {p.name for p in product.photos}
    chosen = []
    for index, member in enumerate(product.collection_members):
        media = [name for name in member["media"].values() if name in available]
        if not media:
            raise ValueError(f"No collection photos remain for {member['slug']}. Recreate the collection with its photos.")
        wanted = previous[index] if previous and index < len(previous) else ""
        chosen.append(wanted if wanted in media else media[0])
    return chosen + chosen[-1:]
