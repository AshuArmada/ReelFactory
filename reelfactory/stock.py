"""Find and download free-licence stock photos to use as product shots.

Not every video has photos to start from. A shop wanting a reel about
"supermarket shelving" may have nothing usable on their phone, and the whole
pipeline downstream of `products/<slug>/photos/` assumes something is in
there. This module fills that folder from Pexels and Pixabay, whose licences
both allow free commercial use with no attribution required.

Three things here are deliberate:

* **Sizes are reported as downloaded, not as advertised.** Pixabay's API
  describes the photographer's original, but the file it actually serves
  (`largeImageURL`) is capped at 1280px on the longest side. Quoting the
  original's dimensions would promise a sharpness the downloaded file cannot
  deliver, so every result is measured after the cap -- see `_capped`.

* **Results are judged by the renderer's own rule.** `review()` runs
  `render.photo_notes` over the search results before anything is downloaded,
  so "this will look soft in a reel" is decided by exactly the code that
  warns about it later on the product page. `only_sharp()` filters on
  `render.MAX_UPSCALE` for the same reason.

* **Only the two stock CDNs are ever fetched.** The web UI posts back the
  URLs it displayed, and a URL arriving in a form is not a reason to make a
  request to any host in the world -- `_allowed` refuses everything else.

API keys are never read from `brand.yaml`, exactly as for the LLM providers:
environment variables, a `.env` next to brand.yaml, or an explicit argument.
Both keys are free and neither is required -- with only one set, that source
is searched alone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import IMAGE_EXTS, next_photo_index, read_yaml, write_yaml
from .render import ASPECTS, MAX_UPSCALE, photo_notes

ROOT = Path(__file__).resolve().parent.parent

SOURCES = ("pexels", "pixabay")
ORIENTATIONS = ("portrait", "landscape", "square", "any")
DEFAULT_ORIENTATION = "portrait"   # reels are 9:16; a landscape photo loses its sides

SEARCH_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 60
MAX_BYTES = 30 * 1024 * 1024       # a stock JPEG is a few MB; this is a runaway guard
PER_SOURCE_CAP = 80                # Pexels' own per_page maximum

# Where the file the user gets actually tops out, per source. Pexels serves the
# photographer's original; Pixabay caps its public download.
PIXABAY_LARGE_CAP = 1280
PIXABAY_FULL_HD_CAP = 1920

ENV_VARS = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}
_ENV_KEY_NAMES = {
    "pexels": {"pexels_api_key", "pexels_key"},
    "pixabay": {"pixabay_api_key", "pixabay_key"},
}

# Hosts a picked result may be downloaded from. Matched on the exact host or
# any subdomain of it.
ALLOWED_HOSTS = ("pexels.com", "pixabay.com")

SIGNUP = {
    "pexels": "https://www.pexels.com/api/",
    "pixabay": "https://pixabay.com/api/docs/",
}


class StockError(RuntimeError):
    pass


@dataclass
class Photo:
    """One search result, as it would arrive on disk."""
    key: str          # "pexels-12345" -- unique, and safe as a filename
    url: str          # the file to download
    thumb: str        # small preview, for the picker
    source: str
    credit: str       # photographer, for the provenance record
    page: str         # where the photo lives, for anyone who wants to link it
    width: int        # of the downloaded file, not of the original
    height: int
    query: str = ""

    @property
    def size_label(self) -> str:
        return f"{self.width}×{self.height}"

    @property
    def ext(self) -> str:
        suffix = Path(urlparse(self.url).path).suffix.lower()
        return suffix if suffix in IMAGE_EXTS else ".jpg"


# ------------------------------------------------------------------- keys


def resolve_key(source: str, explicit: str | None = None) -> str | None:
    """The key for one source, or None. Never raises: having only one of the
    two keys, or neither, is a normal way to use the tool."""
    if source not in SOURCES:
        raise StockError(f"Unknown photo source {source!r}. Choose from {', '.join(SOURCES)}.")
    return (
        explicit
        or os.environ.get(ENV_VARS[source])
        or _load_dotenv(_ENV_KEY_NAMES[source])
    )


def configured(keys: dict | None = None) -> list:
    """Which sources can actually be searched right now."""
    keys = keys or {}
    return [s for s in SOURCES if resolve_key(s, keys.get(s))]


def setup_help(sources=None) -> str:
    """What to do about having no key, named for the sources being asked for."""
    wanted = list(sources or SOURCES)
    lines = [f"  {s}: get a free key at {SIGNUP[s]}, then set {ENV_VARS[s]}" for s in wanted]
    return (
        "No stock photo API key found. Both are free and take about a minute:\n"
        + "\n".join(lines)
        + "\nSet it as an environment variable (setx PEXELS_API_KEY \"...\" on Windows, "
        "then open a new terminal) or add it to the .env file next to brand.yaml."
    )


def _load_dotenv(names: set) -> str | None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().strip("'\"")
        if name.lower() not in names:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            return value
    return None


# ----------------------------------------------------------------- search


def search(query: str, count: int = 8, sources=None, orientation: str = DEFAULT_ORIENTATION,
           keys: dict | None = None, timeout: int = SEARCH_TIMEOUT) -> list:
    """Up to `count` results for `query`, newest-first per source, interleaved.

    Interleaved rather than concatenated: asking both sources and then taking
    the first eight would otherwise mean eight Pexels photos and the Pixabay
    key going unused every time.

    A source with no key is skipped silently -- that is just a source the user
    has not signed up for. Only having *none* of the requested sources
    available is an error, and it explains how to fix it.
    """
    query = (query or "").strip()
    if not query:
        raise StockError("Type what to search for, e.g. 'steel shelving' or 'grocery store aisle'.")
    if orientation not in ORIENTATIONS:
        raise StockError(f"Unknown orientation {orientation!r}. Choose from {', '.join(ORIENTATIONS)}.")
    wanted = [s for s in (sources or SOURCES) if s in SOURCES] or list(SOURCES)
    keys = keys or {}

    usable = [(s, resolve_key(s, keys.get(s))) for s in wanted]
    usable = [(s, k) for s, k in usable if k]
    if not usable:
        raise StockError(setup_help(wanted))

    count = max(1, count)
    per_source = min(max(count, 3), PER_SOURCE_CAP)
    fetch = {"pexels": _search_pexels, "pixabay": _search_pixabay}
    batches = [fetch[s](query, per_source, orientation, key, timeout) for s, key in usable]

    out, seen = [], set()
    for i in range(max((len(b) for b in batches), default=0)):
        for batch in batches:
            if i >= len(batch) or batch[i].key in seen:
                continue
            seen.add(batch[i].key)
            out.append(batch[i])
            if len(out) >= count:
                return out
    return out


def _search_pexels(query, count, orientation, key, timeout) -> list:
    params = {"query": query, "per_page": count}
    if orientation != "any":
        params["orientation"] = orientation
    data = _get(
        "https://api.pexels.com/v1/search", "pexels",
        params=params, headers={"Authorization": key}, timeout=timeout,
    )
    out = []
    for p in data.get("photos", []):
        src = p.get("src") or {}
        # 'original' is the photographer's file, so the width/height the API
        # reports are the ones this download really has.
        url = src.get("original")
        if not url:
            continue
        out.append(Photo(
            key=f"pexels-{p.get('id')}",
            url=url,
            thumb=src.get("medium") or src.get("tiny") or url,
            source="pexels",
            credit=p.get("photographer") or "",
            page=p.get("url") or "",
            width=int(p.get("width") or 0),
            height=int(p.get("height") or 0),
            query=query,
        ))
    return out


def _search_pixabay(query, count, orientation, key, timeout) -> list:
    # Pixabay's own vocabulary: 'vertical'/'horizontal', and no square option.
    facing = {"portrait": "vertical", "landscape": "horizontal"}.get(orientation, "all")
    data = _get(
        "https://pixabay.com/api/", "pixabay",
        params={
            "key": key, "q": query, "image_type": "photo", "orientation": facing,
            "per_page": min(max(count, 3), 200), "safesearch": "true",
        },
        timeout=timeout,
    )
    out = []
    for p in data.get("hits", []):
        # fullHDURL only exists for accounts granted full API access; without
        # it the largest public file is largeImageURL, and both are capped
        # well below the original the API describes.
        url = p.get("fullHDURL") or p.get("largeImageURL")
        if not url:
            continue
        cap = PIXABAY_FULL_HD_CAP if p.get("fullHDURL") else PIXABAY_LARGE_CAP
        w, h = _capped(int(p.get("imageWidth") or 0), int(p.get("imageHeight") or 0), cap)
        out.append(Photo(
            key=f"pixabay-{p.get('id')}",
            url=url,
            thumb=p.get("webformatURL") or p.get("previewURL") or url,
            source="pixabay",
            credit=p.get("user") or "",
            page=p.get("pageURL") or "",
            width=w,
            height=h,
            query=query,
        ))
    return out


def _capped(width: int, height: int, cap: int):
    """The original's shape, scaled down to what the served file tops out at."""
    longest = max(width, height)
    if longest <= cap or longest == 0:
        return width, height
    scale = cap / longest
    return int(round(width * scale)), int(round(height * scale))


def _get(url, source, params=None, headers=None, timeout=SEARCH_TIMEOUT) -> dict:
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise StockError(f"Could not reach {source}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise StockError(
            f"{source} rejected the API key (HTTP {resp.status_code}). "
            f"Check {ENV_VARS[source]}, or get a new free key at {SIGNUP[source]}."
        )
    if resp.status_code == 429:
        raise StockError(
            f"{source} is rate limiting this key (HTTP 429). Both free tiers allow a few "
            "hundred searches an hour — wait a minute and try again."
        )
    if resp.status_code != 200:
        raise StockError(f"{source} returned HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise StockError(f"{source} returned something that was not JSON: {resp.text[:200]}") from exc
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------- judging the results


def review(photos, size=None) -> dict:
    """{photo.key: PhotoNote} -- how each result will fare in the render.

    Goes through `render.photo_notes`, the same function that warns about the
    photos already in a product, so a result described here as fine cannot be
    described as too small the moment it lands on disk.
    """
    size = size or ASPECTS["9:16"]
    sizes = {Path(p.key): (p.width, p.height) for p in photos if p.width and p.height}
    return {note.name: note for note in photo_notes(sizes, size)}


def only_sharp(photos, size=None) -> list:
    """Just the results with enough pixels to survive the Ken Burns zoom.

    Crops are left alone: a portrait search is already the right shape, and a
    photo losing a little at the edges is a judgement call the user can make
    by looking at it. Being too small to be sharp is not.
    """
    notes = review(photos, size)
    return [p for p in photos if (note := notes.get(p.key)) and note.upscale <= MAX_UPSCALE]


# --------------------------------------------------------------- downloading


def download(photos, dest_dir, timeout: int = DOWNLOAD_TIMEOUT, on_progress=None) -> list:
    """Save each photo into `dest_dir` as the next free 1.jpg, 2.jpg, ...

    Returns `[(path, photo)]` for the ones that arrived. A single failed
    download is not fatal -- one dead URL out of eight should not throw away
    the other seven -- so it is skipped and reported through `on_progress`.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    n = next_photo_index(dest)
    saved = []
    for photo in photos:
        try:
            blob = fetch_bytes(photo.url, timeout=timeout)
        except StockError as exc:
            if on_progress:
                on_progress(photo, None, str(exc))
            continue
        path = dest / f"{n}{photo.ext}"
        path.write_bytes(blob)
        n += 1
        saved.append((path, photo))
        if on_progress:
            on_progress(photo, path, "")
    return saved


def fetch_bytes(url: str, timeout: int = DOWNLOAD_TIMEOUT) -> bytes:
    if not _allowed(url):
        raise StockError(
            f"Refusing to download from {urlparse(url).hostname or url!r}: only "
            f"{' and '.join(ALLOWED_HOSTS)} are fetched."
        )
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        kind = resp.headers.get("Content-Type", "")
        if kind and not kind.startswith("image/"):
            raise StockError(f"that link returned {kind}, not an image")
        blob = b""
        for chunk in resp.iter_content(64 * 1024):
            blob += chunk
            if len(blob) > MAX_BYTES:
                raise StockError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    except requests.RequestException as exc:
        raise StockError(f"download failed: {exc}") from exc
    if not blob:
        raise StockError("the download was empty")
    return blob


def _allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


# ------------------------------------------------------------------ credits
#
# Its own file rather than a product.yaml field: Product.load rejects any key
# it has no dataclass field for, and where a photo came from is provenance,
# not a fact about the product that belongs in a script.


CREDITS_FILE = "photo_credits.yaml"


def credits_path(product_dir) -> Path:
    return Path(product_dir) / CREDITS_FILE


def record_credits(product_dir, saved, query: str = "") -> Path:
    """Note where each downloaded photo came from, keyed by its filename.

    Neither licence asks for attribution, so this is not a legal obligation --
    it is the one thing that cannot be reconstructed later. Six months on,
    "may we use this photo on a billboard" is unanswerable without it.
    """
    path = credits_path(product_dir)
    data = load_credits(product_dir)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    for file_path, photo in saved:
        data[Path(file_path).name] = {
            "source": photo.source,
            "credit": photo.credit,
            "page": photo.page,
            "query": photo.query or query,
            "added": stamp,
        }
    write_yaml(path, data)
    return path


def load_credits(product_dir) -> dict:
    """{filename: {source, credit, page, query, added}}, or {} if never used."""
    path = credits_path(product_dir)
    if not path.exists():
        return {}
    try:
        data = read_yaml(path)
    except (ValueError, FileNotFoundError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def forget_credits(product_dir, names) -> None:
    """Drop the record for photos that have been deleted, so the file does not
    fill up with entries for files nobody can look at any more."""
    data = load_credits(product_dir)
    remaining = {k: v for k, v in data.items() if k not in set(names)}
    if remaining == data:
        return
    if remaining:
        write_yaml(credits_path(product_dir), remaining)
    else:
        credits_path(product_dir).unlink(missing_ok=True)
