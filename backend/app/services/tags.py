"""The booru tag dictionary: what the checkpoint actually knows.

Two jobs, and they want opposite things from the same data.

**Autocomplete** wants breadth. Any field whose text reaches ComfyUI verbatim --
a CG's camera and action, a character's marks and accessories, an outfit's
tags, a location's tags, every negative -- is a field where a typo is silent.
`school unifrom` does not fail, it just quietly conditions on nothing. So the
editor needs to suggest from everything the model was trained on.

**The shot director** wants the opposite: a short curated palette
(`defaults.CG_VOCAB`), because a 7-12B picks better from 276 options than it
recalls from thirty thousand. What the dictionary gives *that* pass is not
vocabulary but a spell-check -- `known()` and `snap()` let
`generation.clean_cg_tags` drop a tag the checkpoint has never seen and
correct a near miss, instead of passing "warm atmosphere" through to a render
where it does nothing.

The corpus is fetched from Danbooru at runtime rather than vendored: it is
~30k rows, it changes, and the machine running this already has the network
access to get it. Nothing here is required for the app to work -- with no
corpus imported, autocomplete falls back to CG_VOCAB and the director's
validator stays off.
"""
from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import httpx

from .. import db, store
from ..defaults import CG_VOCAB, CG_VOCAB_NSFW

# Tags below this many posts are not reliably learned by an SDXL/Illustrious
# checkpoint -- they are in the training data as a handful of images, which is
# indistinguishable from noise at inference. Keeping them would make the
# autocomplete longer and worse.
DEFAULT_MIN_COUNT = 100

# Danbooru's tag categories. Only `general` is useful here: character and
# copyright tags name specific existing characters, and identity in this tool
# comes from the character record instead (doc S4).
CATEGORY_GENERAL = 0

API = "https://danbooru.donmai.us/tags.json"
PAGE_SIZE = 1000
MAX_PAGES = 60          # 60k tags is far past where post_count stops mattering


def corpus_path() -> Path:
    return db.ROOT / "tags" / "danbooru.json"


# --------------------------------------------------------------------------
# semantic categories
# --------------------------------------------------------------------------
# Danbooru's own `category` field only separates general/artist/character/
# copyright/meta, which is no help when the question is "is this a pose or a
# hairstyle". These buckets are for the human scanning an autocomplete list,
# so they are named after what the author is looking for when they type.
#
# Order matters: the first rule that matches wins, so the specific patterns
# have to come before the general ones.
CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    # Subject counts first, and on their own: `1girl` is not a body tag, it is
    # the single most important tag in an anime prompt and it is the one thing
    # `images.group_count_tag` computes rather than accepts.
    ("count", re.compile(
        r"^(\d+(girl|boy|other)s?|solo|solo focus|multiple (girls|boys|others)|"
        r"group|duo|trio|quartet|hetero|yuri|yaoi|no humans)$")),
    ("perspective", re.compile(
        r"\b(shot|focus|angle|view|pov|framing|close-?up|portrait|"
        r"from (above|below|side|behind|front)|cropped|out of frame|perspective|"
        r"foreshortening|fisheye|upside-?down|profile|zoom layer|panorama|"
        r"dutch angle|straight-?on|facing (viewer|away))\b")),
    ("light", re.compile(
        r"\b(lighting|lit|backlit|shadow|silhouette|glow|glowing|lens flare|"
        r"god rays|bloom|vignett\w*|chiaroscuro|caustics|bokeh|depth of field|"
        r"blurry|light rays|sunlight|moonlight|spotlight|backlighting|"
        r"rim light|dappled|gradient|light particles)\b")),
    # Image-level facts about the picture rather than its content.
    ("meta", re.compile(
        r"\b(comic|censored|uncensored|mosaic censoring|bar censor|artist name|"
        r"speech bubble|text|watermark|signature|username|logo|border|"
        r"translated|commentary|dated|web address|character name|"
        r"virtual youtuber|chibi|parody|meme|reference|photoshop \(medium\)|"
        r"lineart|sketch|monochrome|greyscale|halftone|screentones|"
        r"multiple views|character sheet|turnaround|variant set)\b")),
    ("quality", re.compile(
        r"\b(quality|masterpiece|absurdres|highres|lowres|resolution|"
        r"jpeg artifacts|scan|official art|game cg|realistic|photorealistic)\b")),
    ("expression", re.compile(
        r"\b(smile|smiling|grin|frown|blush|blushing|crying|tears|pout|smirk|"
        r"laughing|angry|sad|surprised|embarrassed|expression\w*|ahegao|"
        r"open mouth|closed mouth|parted lips|clenched teeth|sweatdrop|"
        r"nervous|worried|serious|happy|scowl|glare|wince|smug|"
        r"tongue out|drunk|blank eyes|empty eyes)\b")
     ),
    # Emoticon tags: the punctuation *is* the expression. Two tests, because
    # `^_^` has no letters at all while `:d`, `:o` and `xd` are mostly
    # punctuation with one letter riding along.
    ("expression", re.compile(r"^[^a-z]{2,}$")),
    ("expression", re.compile(r"^[a-z]?[^a-z\s]{1,3}[a-z]?$")),
    # Eye and brow *appearance*, which is what the character editor's own
    # booru-tag fields are for. Before `gaze`, because "blue eyes" is a colour
    # and "looking at viewer" is a direction, and the colour reading has to
    # win for the tag that names one.
    ("eyes", re.compile(
        r"\b((blue|red|purple|green|brown|yellow|pink|black|grey|gray|aqua|"
        r"orange|white|silver|gold|amber|violet|multicoloured|multicolored|"
        r"heterochromia)\s+eyes|eyes?\s+(visible|closed)|eyelashes|eyebrows?|"
        r"pupils?|iris|sclera|eyeshadow|eyeliner|mascara|v-shaped eyebrows|"
        r"thick eyebrows|no eyes|glowing eyes|constricted pupils)\b")),
    ("gaze", re.compile(
        r"\b(looking|eye contact|staring|averting|glance|"
        r"closed eyes|half-closed eyes|one eye closed|wink|eyes visible)\b")),
    ("hair", re.compile(
        r"\b(hair|bangs|ponytail|twintails?|braid|braids|ahoge|hairband|updo|"
        r"sidelocks?|bun|bob cut|dreadlocks|afro|hime cut|widow's peak|"
        r"forelock|antenna hair|cowlick)\b")),
    # Worn, and removable: the character's accessories field feeds this
    # straight into every prompt, so it earns a bucket of its own rather than
    # being lumped in with clothing.
    ("accessory", re.compile(
        r"\b(jewelry|earrings?|necklace|choker|bracelet|anklet|ring|piercing|"
        r"glasses|sunglasses|eyewear|monocle|eyepatch|mask|hairclip|hairpin|"
        r"hair ornament|hair bow|hair flower|hair ribbon|headphones|headband|"
        r"halo|horns?|crown|tiara|veil|badge|pin|watch|belt|"
        r"bag|backpack|purse|umbrella|scarf|collar|leash|cuffs|"
        r"bandaid|bandage|goggles|helmet)\b")),
    ("clothing", re.compile(
        r"\b(uniform|serafuku|dress|skirt|miniskirt|shirt|blouse|jacket|coat|"
        r"sweater|hoodie|cardigan|vest|blazer|robe|cloak|cape|apron|"
        r"swimsuit|bikini|kimono|yukata|hakama|leotard|bodysuit|"
        r"pantyhose|thighhighs|kneehighs|socks|stockings|legwear|tights|"
        r"shoes|boots|sandals|heels|sneakers|footwear|slippers|"
        r"hat|cap|beret|hood|headwear|gloves|mittens|"
        r"sleeves|sleeveless|shorts|pants|trousers|jeans|leggings|overalls|"
        r"clothes|clothing|costume|outfit|attire|frills|ruffles|lace|"
        r"necktie|bowtie|tie|ribbon|bow|suspenders|"
        r"naked|nude|topless|bottomless|undressed|undressing|"
        r"lingerie|underwear|panties|bra|briefs|thong|garter|"
        r"midriff|cleavage|navel cutout|see-through|wet clothes|torn clothes|"
        r"crop top|tank top|camisole|turtleneck|off-shoulder|halterneck|"
        r"armor|armour|neckerchief|sash|shawl|poncho|kilt|"
        r"swim (trunks|briefs)|one-piece|two-piece)\b")),
    ("action", re.compile(
        r"\b(hug|hugging|kiss|kissing|grab|grabbing|hold|holding|carry|carrying|"
        r"sex|fellatio|cunnilingus|handjob|paizuri|penetration|insertion|"
        r"masturbation|groping|fingering|licking|biting|"
        r"missionary|doggystyle|cowgirl|spitroast|spooning|straddling|mounting|"
        r"reaching|pointing|waving|shushing|beckoning|facepalm|salute|"
        r"eating|drinking|smoking|reading|writing|singing|dancing|fighting|"
        r"punching|kicking|throwing|catching|pulling|pushing|"
        r"headpat|handshake|high five|fist bump|piggyback|princess carry)\b")),
    ("posture", re.compile(
        r"\b(standing|sitting|kneeling|lying|squatting|crouching|leaning|"
        r"bent over|arched back|all fours|crawling|position|pose|stance|"
        r"tiptoes|on (back|side|stomach)|straddle|seiza|wariza|yokozuwari|"
        r"legs? (up|apart|crossed|together|lock)|knees?|spread legs|"
        r"arms? (up|behind|at side|crossed|support)|outstretched|"
        r"jumping|running|walking|falling|floating|flying|midair|"
        r"upright|reclining|fetal position|handstand|split)\b")),
    ("body", re.compile(
        r"\b(collarbone|nape|shoulders?|neck|armpits?|navel|stomach|belly|back|"
        r"spine|thighs?|feet|foot|soles|toes|barefoot|hands?|palms?|fingers?|"
        r"lips|tongue|teeth|fang|wrist|ankle|elbow|knee|"
        r"breasts?|nipples?|areolae|ass|butt|hips|waist|abs|muscle|muscular|"
        r"skin|body|figure|penis|pussy|vagina|testicles|groin|pubic|anus|"
        r"animal ears?|cat ears|fox ears|dog ears|rabbit ears|pointy ears|ears?|"
        r"mole|freckles|nail polish|fingernails|toenails|nails|dimples|"
        r"dark-skinned|light-skinned|pale skin|tan(ned)? skin|"
        r"tail|wings?|claws?|fangs?|scales|fur|horn)\b")),
    ("detail", re.compile(
        r"\b(sweat|steam|breath|goosebumps|trembling|wet|dirty|blood|bandage\w*|"
        r"scar|petals|particles|dust|embers|reflection|saliva|drooling|"
        r"motion (blur|lines)|speed lines|sparkle|glitter|smoke|"
        r"aura|heart|spark|tear|bruise|dirt|mud|splash|cum|"
        r"star \(symbol\)|heart \(symbol\)|musical note|emphasis lines)\b")),
    ("scene", re.compile(
        r"\b(indoors|outdoors|background|scenery|sky|clouds?|rain|snow|fog|mist|"
        r"sunset|sunrise|night|day|dusk|dawn|twilight|"
        r"room|building|street|road|forest|woods|beach|ocean|sea|city|town|"
        r"window|door|wall|floor|ceiling|stairs|bridge|fence|"
        r"bed|chair|table|desk|couch|sofa|bench|"
        r"hospital|classroom|school|kitchen|bathroom|bedroom|office|shop|"
        r"cafe|restaurant|train|car|bus|park|garden|field|mountain|river|"
        r"snowing|raining|storm|wind|weather|water|grass|tree|"
        r"underwater|rooftop|hallway|corridor|alley|shrine|temple|church)\b")),
    # Things held, worn-adjacent, or in frame. Last, because plenty of object
    # names also appear inside a clothing or scene tag and those readings win.
    ("object", re.compile(
        r"\b(weapon|sword|knife|gun|rifle|staff|wand|bow \(weapon\)|shield|"
        r"food|drink|cup|mug|bottle|glass|plate|bowl|chopsticks|fork|spoon|"
        r"flower|flowers|rose|petal|leaf|plant|"
        r"book|paper|pen|pencil|phone|cellphone|camera|computer|laptop|"
        r"bag|box|basket|toy|doll|plushie|balloon|candle|lamp|"
        r"instrument|guitar|piano|violin|"
        r"animal|cat|dog|bird|fish|horse|rabbit)\b")),
]

# The curated groups already carry a hand-picked category, and it beats any
# pattern guess -- these are the tags somebody sat and classified.
CURATED: dict[str, str] = {}
for _group, _tags in {**CG_VOCAB, **CG_VOCAB_NSFW}.items():
    _mapped = {
        "framing": "perspective", "angle": "perspective", "pov": "perspective",
        "contact": "action", "hands": "action", "act": "action",
        "position": "posture", "state": "detail",
    }.get(_group, _group)
    for _t in _tags:
        CURATED.setdefault(_t.lower(), _mapped)


def categorize(name: str) -> str:
    """A bucket a human would look under. Never fails -- worst case "other"."""
    lowered = name.lower()
    if lowered in CURATED:
        return CURATED[lowered]
    spaced = lowered.replace("_", " ")
    for category, pattern in CATEGORY_RULES:
        if pattern.search(spaced):
            return category
    return "other"


CATEGORIES = ["count", "perspective", "posture", "action", "body", "gaze",
              "eyes", "expression", "detail", "light", "scene", "hair",
              "clothing", "accessory", "object", "meta", "quality", "other"]


# --------------------------------------------------------------------------
# the corpus
# --------------------------------------------------------------------------

_cache: dict[str, Any] = {"mtime": None, "rows": [], "by_name": {}}


def _normalise(name: str) -> str:
    """Danbooru's `hair_between_eyes` -> the `hair between eyes` a prompt wants.

    Spaces, not underscores, because that is what this project already writes
    everywhere -- `CG_VOCAB`, `identity_tags`, every outfit's tag string -- and
    what Illustrious-family checkpoints are prompted with in practice. Mixing
    the two conventions inside one prompt is the thing to avoid; which one you
    pick matters much less.

    The exception is the emoticon tags, where the underscore is not a word
    separator but the face: `^_^`, `>_<`, `@_@`, `+_+`, `o_o`, `\\m/`. Those are
    real Danbooru tags with six-figure post counts, and "^ ^" is not the same
    token as "^_^" to a tokenizer -- it is nothing at all. So underscores are
    only spaced out when the tag has letters around them to separate.
    """
    text = (name or "").strip().lower()
    if not _WORDY.search(text):
        return text          # an emoticon: the punctuation *is* the tag
    return text.replace("_", " ")


# Is this tag made of words rather than of punctuation? Two letters in a row
# is the test, not one: `o_o`, `x_x` and `;_;` are emoticons that happen to
# contain letters, and a single-letter run is never a word worth separating.
# `hair_between_eyes` has runs of 4, 7 and 4; `^_^` and `\\||/` have none.
_WORDY = re.compile(r"[a-z]{2,}")


# In a ComfyUI/A1111 prompt, parentheses are attention-weight syntax, so a
# Danbooru disambiguation suffix like `heart_(symbol)` silently becomes a
# weighted group around the word "symbol" instead of naming the tag. Escaping
# them is what makes such a tag usable at all.
_PARENS = re.compile(r"([()])")


def for_prompt(name: str) -> str:
    """A tag as it should be pasted into a prompt field.

    Only escaping, no renaming: what the author sees in the autocomplete list
    is what lands in the field, minus the two characters that would otherwise
    be read as syntax rather than as text.
    """
    return _PARENS.sub(r"\\\1", _normalise(name))


def _fallback_rows() -> list[dict[str, Any]]:
    """The curated vocabulary, so autocomplete works before any import.

    No post counts to rank by, so these sort by category then alphabetically
    and are marked `curated` -- which the UI can say out loud, because "these
    are the 365 tags the shot director uses" is a different promise from "these
    are the 30,000 tags the checkpoint knows".
    """
    rows = []
    for name, category in sorted(CURATED.items()):
        rows.append({"name": name, "count": 0, "category": category,
                     "curated": True})
    return rows


def load() -> list[dict[str, Any]]:
    """The imported corpus, or the curated fallback. Cached on file mtime."""
    path = corpus_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        if _cache["mtime"] != "fallback":
            rows = _fallback_rows()
            _cache.update(mtime="fallback", rows=rows,
                          by_name={r["name"]: r for r in rows})
        return _cache["rows"]

    if _cache["mtime"] != mtime:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("tags") or []
        except (OSError, ValueError):
            rows = _fallback_rows()
        # The curated tags are merged in even after an import: a handful of
        # them are phrasings Danbooru spells differently, and the director is
        # allowed to use them regardless of what the dump says.
        seen = {r["name"] for r in rows}
        rows = rows + [r for r in _fallback_rows() if r["name"] not in seen]
        # Categories are recomputed here rather than trusted from the file.
        # They are a pattern guess over the tag's own name, so freezing them at
        # import time meant every improvement to the classifier needed a
        # re-import to take effect -- and a corpus fetched under older rules
        # kept answering with them. Cheap enough: one pass per file change.
        for row in rows:
            row["category"] = categorize(row["name"])
        _cache.update(mtime=mtime, rows=rows,
                      by_name={r["name"]: r for r in rows})
    return _cache["rows"]


def status() -> dict[str, Any]:
    path = corpus_path()
    rows = load()
    imported = None
    min_count = None
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            imported = payload.get("imported_at")
            min_count = payload.get("min_count")
        except (OSError, ValueError):
            pass
    curated = sum(1 for r in rows if r.get("curated"))
    return {
        "total": len(rows),
        "curated_only": not path.exists(),
        "curated": curated,
        "imported_at": imported,
        "min_count": min_count,
        "path": str(path),
        "categories": {c: sum(1 for r in rows if r["category"] == c)
                       for c in CATEGORIES},
    }


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def search(query: str, limit: int = 12,
           categories: Optional[Iterable[str]] = None,
           prefer: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    """Rank tags for a partially typed token.

    Ranking is prefix-first, then word-start, then substring, and popularity
    within each of those. That order matters more than it looks: typing "hand"
    should offer `hands` before `hand on another's shoulder`, and both before
    `own hands together`, because the shortest exact-prefix match is nearly
    always the one being typed.

    `prefer` sorts some categories to the top without excluding anything, and
    is what the editor's fields use. `categories` is the hard filter, for
    browsing a category deliberately.

    The distinction is the whole reason both exist. Classification is a pattern
    guess over 24k tags: it gets 99% of the top two hundred and about half of
    the long tail, so a *filter* on the CG camera field would silently hide
    every framing tag the classifier happened to file under "other". A
    preference cannot do that -- the worst it does is rank something second.
    """
    q = _normalise(query)
    if not q:
        return []
    wanted = {c for c in (categories or []) if c}
    preferred = {c for c in (prefer or []) if c}
    rows = load()

    scored: list[tuple[int, int, int, int, str, dict]] = []
    for row in rows:
        if wanted and row["category"] not in wanted:
            continue
        name = row["name"]
        if name == q:
            tier = 0
        elif name.startswith(q):
            tier = 1
        elif re.search(r"(^|\s)" + re.escape(q), name):
            tier = 2
        elif q in name:
            tier = 3
        else:
            continue
        # An exact match outranks the preference -- if you typed the whole tag,
        # you meant that tag, whatever category it landed in.
        rank = 0 if (not preferred or tier == 0
                     or row["category"] in preferred) else 1
        # Popularity decides within a tier, with length only as a tiebreak.
        # Length used to come first, on the theory that the shortest prefix
        # match is the one being typed -- but that made `starfish` (30k posts)
        # beat `star (symbol)` (358k) for "star". Where the short tag really is
        # the intended one it is also the popular one, so counting posts gets
        # that case right too and this one as well.
        scored.append((rank, tier, -int(row.get("count") or 0), len(name),
                       name, row))

    scored.sort(key=lambda s: s[:5])
    limit = max(1, limit)
    if not preferred:
        return [row for *_rest, row in scored[:limit]]

    # A preference that fills every slot is a filter again. With ten slots and
    # a dozen framing matches, `star (symbol)` fell off the end of a search for
    # "star" in the camera field -- reachable in principle, invisible in
    # practice. So a quarter of the list is held back for everything else, and
    # given up only when there is nothing else to put there.
    reserved = min(max(2, limit // 4), limit - 1)
    head = [s for s in scored if s[0] == 0]
    tail = [s for s in scored if s[0] == 1]
    take_head = min(len(head), limit - min(len(tail), reserved))
    chosen = head[:take_head] + tail[:limit - take_head]
    return [row for *_rest, row in chosen]


# --------------------------------------------------------------------------
# what the director's output is checked against
# --------------------------------------------------------------------------

def known(tag: str) -> bool:
    """Is this a tag the checkpoint has actually been trained on?

    False for an empty corpus is deliberately NOT the answer -- see
    `has_corpus`. A validator that rejects everything because nothing has been
    imported would silently empty every director prompt.
    """
    load()
    return _normalise(tag) in _cache["by_name"]


def count_of(tag: str) -> int:
    """How many Danbooru posts carry this tag, 0 if it is not in the corpus.

    The director's validator floors on this. Membership alone is nearly the
    same test today -- the import already drops everything under
    DEFAULT_MIN_COUNT -- but the floor is a parameter the config page exposes
    (`importTags(minCount)`), so a corpus fetched at 10 would otherwise let
    the director spend tags the checkpoint has seen ten times. Curated tags
    carry no count and are exempt: they are hand-picked, not measured.
    """
    load()
    row = _cache["by_name"].get(_normalise(tag))
    return int((row or {}).get("count") or 0)


def has_corpus() -> bool:
    """Whether a real import exists, as opposed to the curated fallback.

    `generation.clean_cg_tags` uses this to decide whether it is entitled to
    drop an unrecognised tag at all: with only the curated 365 in memory,
    "unrecognised" means almost everything.
    """
    return corpus_path().exists()


# Cheap morphological retries, in the order worth trying. Nothing clever --
# a fuzzy matcher that rewrites one real tag into a different real tag is far
# worse than leaving a near-miss alone, so this only ever tries forms that are
# the *same* tag spelled differently.
def snap(tag: str) -> Optional[str]:
    """The canonical spelling of `tag`, or None if it is not a known tag.

    Handles the near misses a small model actually makes: underscores, a
    missing or extra plural, and hyphen/space drift ("close up" vs "close-up").
    """
    load()
    by_name = _cache["by_name"]
    base = _normalise(tag)
    if not base:
        return None

    candidates = [base]
    if base.endswith("s"):
        candidates.append(base[:-1])
    else:
        candidates.append(base + "s")
    candidates.append(base.replace("-", " "))
    candidates.append(base.replace(" ", "-"))
    candidates.append(base.replace("'", ""))
    for candidate in candidates:
        if candidate in by_name:
            return candidate
    return None


def check(names: Iterable[str]) -> list[dict[str, Any]]:
    """The tags in `names` the dictionary does not recognise, with a fix where
    there is an obvious one.

    Only director *output* was ever screened (`generation.clean_cg_tags`).
    Tags typed into a character or location record go straight to the renderer
    unchecked, and a tag that does not exist does not fail -- it conditions on
    nothing and the render comes back subtly wrong with no error anywhere.
    Two real examples from one project: `public hair` for `pubic hair`, and a
    location carrying `cou`.

    Returns only the misses, so an empty list means the record is clean. Empty
    while no corpus is imported, for the same reason `clean_cg_tags` stops
    validating there: with nothing to check against, "unrecognised" would mean
    everything and the warning would be worthless.
    """
    if not has_corpus():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in names or []:
        tag = (raw or "").strip()
        key = tag.lower()
        if not tag or key in seen:
            continue
        seen.add(key)
        if known(tag):
            continue
        suggestion = snap(tag)
        if suggestion and suggestion.lower() == key:
            continue
        out.append({"tag": tag, "suggestion": suggestion or ""})
    return out


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

# Danbooru asks for a descriptive User-Agent and, behind Cloudflare, enforces
# it: a browser-shaped string ("Mozilla/5.0 ... Chrome/131") gets a 403
# challenge page, while naming the tool gets a 200. That is the opposite of
# the usual advice, and worth stating plainly so nobody "fixes" this by
# pretending to be Chrome.
USER_AGENT = "vn-authoring-tool/1.0 (local visual-novel editor; personal use)"


def _ssl_context():
    """A TLS context that trusts what the *machine* trusts.

    httpx verifies against certifi, which is the right default and the wrong
    one on a machine whose HTTPS is intercepted -- antivirus web shields and
    corporate proxies re-sign every connection with a root that only exists in
    the Windows certificate store. certifi has never heard of it, so every
    external request dies with CERTIFICATE_VERIFY_FAILED, which reads like a
    network fault and is not one.

    `load_default_certs` reads the OS store, so this works in both cases.
    Falls back to httpx's default if the OS store is unavailable.
    """
    try:
        context = ssl.create_default_context()
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
        return context
    except Exception:  # noqa: BLE001 - any failure here means "use the default"
        return True


class TagImportError(RuntimeError):
    pass


def _explain(response) -> str:
    """Turn the two failures that actually happen into something actionable."""
    if response.status_code == 403 and "Just a moment" in response.text[:400]:
        return ("Cloudflare served a challenge instead of the API. This happens "
                "when the User-Agent looks like a browser rather than naming a "
                "tool -- Danbooru blocks the former on purpose.")
    if response.status_code == 429:
        return "Danbooru rate-limited the import. Wait a minute and re-run it."
    return f"Danbooru returned {response.status_code}: {response.text[:200]}"


async def import_from_danbooru(
    min_count: int = DEFAULT_MIN_COUNT,
    on_progress: Optional[Callable[[float, str], Any]] = None,
) -> dict[str, Any]:
    """Page Danbooru's tag list, newest-most-used first, and cache it.

    Ordered by post count and stopped as soon as the count drops below
    `min_count`, so the request count is proportional to how much of the tail
    is wanted rather than to the size of the whole site.
    """
    rows: list[dict[str, Any]] = []
    stopped = "page limit"

    async with httpx.AsyncClient(timeout=60.0, verify=_ssl_context(),
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept": "application/json"}) as client:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "search[order]": "count",
                "search[category]": CATEGORY_GENERAL,
                "search[hide_empty]": "yes",
                "only": "name,post_count,category",
                "limit": PAGE_SIZE,
                "page": page,
            }
            response = await client.get(API, params=params)
            if response.status_code >= 400:
                # Nothing partial is worth caching, and a silent half-import
                # would be worse than none -- the validator would then start
                # deleting real tags for being "unknown".
                if page == 1 or response.status_code != 429:
                    raise TagImportError(_explain(response))
                stopped = "rate limited partway through"
                break
            batch = response.json()
            if not batch:
                stopped = "end of list"
                break

            below = False
            for entry in batch:
                count = int(entry.get("post_count") or 0)
                if count < min_count:
                    below = True
                    continue
                name = _normalise(entry.get("name") or "")
                if not name:
                    continue
                rows.append({"name": name, "count": count,
                             "category": categorize(name)})
            if on_progress is not None:
                await on_progress(min(0.95, page / 25),
                                  f"{len(rows)} tags at >= {min_count} posts")
            # Danbooru is somebody's server and this is ~30 requests; the
            # pause costs seconds and keeps the import off their rate limiter.
            await asyncio.sleep(0.35)
            if below:
                stopped = f"reached the {min_count}-post floor"
                break

    # Dedupe, keeping the highest count -- paging by a changing ordering can
    # repeat a row across page boundaries.
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = best.get(row["name"])
        if current is None or row["count"] > current["count"]:
            best[row["name"]] = row
    final = sorted(best.values(), key=lambda r: -r["count"])

    path = corpus_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "imported_at": store.now(),
        "min_count": min_count,
        "source": API,
        "stopped": stopped,
        "tags": final,
    }), encoding="utf-8")
    _cache["mtime"] = None          # force a reload on the next read

    counts: dict[str, int] = {}
    for row in final:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return {"imported": len(final), "min_count": min_count, "stopped": stopped,
            "categories": counts, "path": str(path)}


# --------------------------------------------------------------------------
# English -> candidate tags
# --------------------------------------------------------------------------

# Words that carry no image content. Kept short on purpose: this is a recall
# problem, not a precision one -- a candidate the tagger ignores costs a line
# of prompt, while one that never gets retrieved cannot be chosen at all.
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "by", "for", "from", "into", "onto", "with", "without", "as", "than",
    "that", "this", "these", "those", "it", "its", "is", "are", "was", "were",
    "be", "been", "being", "has", "have", "had", "do", "does", "did", "will",
    "would", "can", "could", "should", "not", "no", "any", "all", "some",
    "her", "his", "their", "your", "you", "she", "he", "they", "him", "them",
    "there", "here", "what", "when", "where", "who", "how", "just", "only",
    "very", "still", "even", "again", "more", "most", "much", "such", "own",
    "one", "two", "up", "down", "out", "off", "about", "after", "before",
}

# Categories whose tags the director is never allowed to write anyway -- they
# come from the character record. Retrieving them would spend the candidate
# budget on things `clean_cg_tags` is about to delete.
_SKIP_CATEGORIES = {"hair", "eyes", "clothing", "count", "quality", "meta"}

_WORD = re.compile(r"[a-z']+")


def _phrases(text: str) -> list[str]:
    """Search terms from a sentence, longest first.

    Bigrams before unigrams because the bigram is what finds the tag that
    actually exists: "arms around" reaches `arms around neck`, where "arms"
    alone reaches `arms up` and fifty others. Both are searched -- the bigram
    for precision, the unigram for the cases where the phrasing does not line
    up at all.
    """
    words = [w for w in _WORD.findall((text or "").lower())
             if len(w) > 2 and w not in _STOP]
    bigrams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    return bigrams + words


def candidates_for(text: str, limit: int = 90, nsfw: bool = False,
                   per_phrase: int = 5,
                   always: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    """Tags worth offering a tagger that has to describe `text`.

    The point of this is reach. A curated list of a few hundred tags is small
    enough to put in a prompt but far too small to describe an arbitrary beat
    -- there is no `holding folder` in it, no `arms around neck`, no
    `windowsill`. Retrieval against the full dictionary gets those, while
    keeping the list that reaches the model short enough to constrain with.

    Deliberately generous: recall beats precision here, because the model is
    *choosing* from this list rather than being told to use all of it. A bad
    candidate is ignored; a missing one cannot be picked.

    Identity tags are dropped rather than offered, since `clean_cg_tags` would
    delete them a moment later, and on an SFW scene the adult half of the
    curated vocabulary is left out entirely.
    """
    from .generation import _BANNED_TAG        # local: generation imports this

    picked: dict[str, dict[str, Any]] = {}

    def offer(row: dict[str, Any]) -> None:
        name = row["name"]
        if name in picked:
            return
        if row["category"] in _SKIP_CATEGORIES:
            return
        # A parenthesised suffix is Danbooru disambiguating a name -- almost
        # always a franchise, a character or a medium. Searching the word
        # "vision" in an ordinary sentence otherwise returns `vision (genshin
        # impact)`, `night vision device` and `x-ray vision`, none of which
        # have anything to do with the beat. The handful of real exceptions
        # (`heart (symbol)`) are not worth the noise they let in with them.
        if "(" in name and name not in CURATED:
            return
        if _BANNED_TAG.search(name) and name not in CURATED:
            return
        picked[name] = row

    # The curated vocabulary first: it is hand-picked for exactly this job, so
    # it is the floor the retrieved tags build on rather than compete with.
    by_name = {r["name"]: r for r in load()}
    for name in (always or ()):
        row = by_name.get(_normalise(name))
        if row is not None:
            offer(row)

    for phrase in _phrases(text):
        for row in search(phrase, limit=per_phrase):
            offer(row)
        if len(picked) >= limit * 3:
            break

    rows = list(picked.values())
    if not nsfw:
        adult = {t.lower() for tags in CG_VOCAB_NSFW.values() for t in tags}
        rows = [r for r in rows if r["name"] not in adult]

    # Popularity last, as the tiebreak: everything here already matched the
    # text, so what is left to decide is which phrasings the checkpoint knows
    # best.
    rows.sort(key=lambda r: -int(r.get("count") or 0))
    return rows[:max(1, limit)]
