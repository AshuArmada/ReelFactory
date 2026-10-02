"""Product-scoped context snapshots for collection reels."""
from __future__ import annotations

from . import telemetry

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
    visual_context = photo_analysis.prompt_block(product)
    if set(media) != {p.name for p in product.photos}:
        # A combined summary may describe excluded photos. Include only fresh
        # observations for the media the user actually selected.
        info = photo_analysis.status(product.dir, [p.name for p in product.photos])
        visual_context = ""
        if info["fresh"]:
            visual_context = "\n".join(
                f"- {row['name']}: {row.get('summary', '')}"
                for row in info["photos"] if isinstance(row, dict) and row.get("name") in media
            )
    return {
        "slug": product.slug, "facts": facts, "media": media,
        "visual_context": visual_context,
    }


@telemetry.traced('Create collection snapshot')
def create_draft(selected: list[Product], name: str, draft, photo_picks=None) -> None:
    (draft / "photos").mkdir()
    members, photo_order = [], []
    for index, product in enumerate(selected, 1):
        media = {}
        for photo_index, photo in enumerate(product.photos, 1):
            if photo_picks is not None and photo.name not in photo_picks[product.slug]:
                continue
            filename = f"{index:04d}-{product.slug}-{photo_index:04d}{photo.suffix.lower()}"
            shutil.copy2(photo, draft / "photos" / filename)
            media[photo.name] = filename
            photo_order.append(filename)
        members.append(snapshot(product, media))
    data = {
        "name_en": name, "name_hi": name, "photo_order": photo_order,
        "collection_members": members,
        "intent": "awareness",
        "target_seconds": max(35, len(members) * 8 + 10),
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
        "PURPOSE: tell one connected story introducing the range of products available at this business.",
        "Begin with a shared hook: a relatable situation or need supported by the supplied audience",
        "and product facts. Bring the viewer through the range as the story develops, then one shared CTA.",
        "Cover EVERY product in this exact order, one usp scene per product between the hook and CTA.",
        "Each product scene must say that product's exact name in the requested language.",
        "Connect scenes with natural transitions. Give each product a distinct place in the story;",
        "do not restart a sales pitch for each item or read a catalogue of names, prices and specs.",
        "Choose one relevant detail per product to explain its place in the range. Keep the rest",
        "as supporting context. Do not force unrelated products into a fictional project, bundle,",
        "compatibility claim or customer success story. When no shared use is supported, make the",
        "story a guided discovery of the shop's variety and the viewer's different choices.",
        "For awareness, leave out price recitals, discounts and urgency unless explicitly requested",
        "or required by a must_say rule. Close by inviting viewers to explore or ask about the range.",
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
        scene_index = index + 1  # The shared opening comes before the products.
        wanted = previous[scene_index] if previous and scene_index < len(previous) else ""
        chosen.append(wanted if wanted in media else media[0])
    opening = previous[0] if previous and previous[0] in available else chosen[0]
    closing = previous[-1] if previous and len(previous) == len(chosen) + 2 and previous[-1] in available else chosen[-1]
    return [opening] + chosen + [closing]


@telemetry.traced('Validate collection photo matching')
def render_photos(product: Product, segments, previous=None) -> list[str]:
    """Resolve edited scenes by product identity, not their former positions."""
    available = {p.name for p in product.photos}
    defaults = scene_photos(product)
    result = []
    for index, segment in enumerate(segments):
        wanted = previous[index] if previous and index < len(previous) else ""
        if wanted and wanted not in available:
            raise ValueError(f"Scene {index + 1}: the selected photo is missing. Choose another photo before building.")
        if segment.role in ("hook", "cta"):
            result.append(wanted or (defaults[0] if segment.role == "hook" else defaults[-1]))
            continue
        spoken = segment.vo.casefold()
        matches = [member for member in product.collection_members
                   if any(str(member["facts"].get(f"name_{lang}", "")).casefold() in spoken
                          for lang in ("en", "hi") if member["facts"].get(f"name_{lang}"))]
        if len(matches) == 1:
            member = matches[0]
            media = [name for name in member["media"].values() if name in available]
            if wanted and wanted not in media:
                raise ValueError(f"Scene {index + 1} talks about {member['facts']['name_en']}, "
                                 "but its photo belongs to another product. Choose a matching photo.")
            if not media:
                raise ValueError(f"No selected photos remain for {member['facts']['name_en']}.")
            result.append(wanted or media[0])
        elif wanted:
            # Generic or multi-product copy uses the user's explicit scene choice.
            result.append(wanted)
        else:
            raise ValueError(f"Choose a photo for scene {index + 1}; its product could not be identified from the narration.")
    return result
