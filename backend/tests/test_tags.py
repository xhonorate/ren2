"""The booru tag dictionary: normalisation, search ranking, and the validator.

The normalisation tests are the load-bearing ones. A tag that round-trips
wrong does not fail anywhere -- it conditions on nothing and the image just
comes back slightly wrong, which is the hardest class of bug to notice in this
whole pipeline.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def app_modules():
    tmp = tempfile.mkdtemp(prefix="vn_tags_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from app import db, store
    db.init_db()
    from app import defaults
    from app.services import generation, tags
    for key, value in defaults.DEFAULT_SETTINGS.items():
        if store.get_setting(key, None) is None:
            store.set_setting(key, value)
    return {"tags": tags, "generation": generation, "defaults": defaults,
            "store": store, "db": db}


@pytest.fixture
def corpus(app_modules):
    """A tiny imported corpus, written the way the importer writes one."""
    tags = app_modules["tags"]
    path = tags.corpus_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"name": "close-up", "count": 500000, "category": "perspective"},
        {"name": "from below", "count": 300000, "category": "perspective"},
        {"name": "hands", "count": 250000, "category": "body"},
        {"name": "hand on another's shoulder", "count": 9000, "category": "action"},
        {"name": "closed eyes", "count": 800000, "category": "gaze"},
        {"name": "hair between eyes", "count": 700000, "category": "hair"},
        {"name": "^_^", "count": 120000, "category": "expression"},
        {"name": "o_o", "count": 4000, "category": "expression"},
        {"name": "heart (symbol)", "count": 5000, "category": "other"},
        {"name": "hug", "count": 200000, "category": "action"},
    ]
    path.write_text(json.dumps({
        "imported_at": "2026-01-01T00:00:00+00:00",
        "min_count": 100, "source": "test", "stopped": "test",
        "tags": rows,
    }), encoding="utf-8")
    tags._cache["mtime"] = None
    yield rows
    path.unlink(missing_ok=True)
    tags._cache["mtime"] = None


# --------------------------------------------------------------------------
# normalisation -- underscores, emoticons, parentheses
# --------------------------------------------------------------------------

def test_underscores_become_spaces_for_word_tags(app_modules):
    """Danbooru stores `hair_between_eyes`; this project writes spaces
    everywhere else (CG_VOCAB, identity_tags, outfit strings), and mixing the
    two conventions inside one prompt is the thing to avoid."""
    n = app_modules["tags"]._normalise
    assert n("hair_between_eyes") == "hair between eyes"
    assert n("hand_on_own_chest") == "hand on own chest"
    assert n("  FROM_BELOW  ") == "from below"


def test_emoticon_tags_keep_their_underscores(app_modules):
    """`^_^` and `o_o` are real tags with six-figure post counts, and in them
    the underscore is the face rather than a word separator -- "^ ^" is not a
    weaker version of "^_^", it is nothing at all. Two letters in a row is the
    test for a word, not one, or `o_o` and `x_x` get mangled."""
    n = app_modules["tags"]._normalise
    for emote in ("^_^", ">_<", "@_@", "+_+", "o_o", "x_x", "0_0", ";_;",
                  "._.", "u_u"):
        assert n(emote) == emote, emote


def test_parentheses_are_escaped_for_the_prompt(app_modules):
    """In a ComfyUI/A1111 prompt, parentheses are attention-weight syntax, so
    a Danbooru disambiguation suffix would silently become a weighted group
    around the wrong word instead of naming the tag."""
    tags = app_modules["tags"]
    assert tags.for_prompt("heart_(symbol)") == r"heart \(symbol\)"
    # And escaping is all it does -- the name is not otherwise rewritten.
    assert tags.for_prompt("hair_between_eyes") == "hair between eyes"
    assert tags.for_prompt("^_^") == "^_^"


def test_normalisation_is_idempotent(app_modules):
    """A tag pasted back in from a field must not drift on a second pass."""
    n = app_modules["tags"]._normalise
    for tag in ("hair between eyes", "close-up", "^_^", "o_o",
                "hand on another's shoulder"):
        assert n(n(tag)) == n(tag), tag


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def test_search_ranks_prefix_matches_by_popularity(app_modules, corpus):
    """Typing "hand" should offer `hands` (250k posts) before `hand on
    another's shoulder` (9k). Popularity decides within a tier: the short tag
    that was probably intended is also, in practice, the popular one."""
    # A wide limit, because the curated vocabulary is merged in and there are
    # a dozen legitimate "hand..." tags. What is under test is the order.
    hits = [h["name"] for h in app_modules["tags"].search("hand", limit=30)]
    assert hits[0] == "hands", hits
    assert "hand on another's shoulder" in hits
    # Every prefix match sorts before one that only matches mid-phrase.
    assert hits.index("hand up") < hits.index("own hands together")


def test_search_matches_at_word_starts_not_just_the_string_start(app_modules, corpus):
    """Half the useful tags are phrases, so "below" has to find "from below"
    -- but a word-start match must still lose to a real prefix match."""
    hits = [h["name"] for h in app_modules["tags"].search("below")]
    assert "from below" in hits


def test_search_can_be_scoped_to_categories(app_modules, corpus):
    """The camera field offers framing tags; the action field offers bodies.
    Both would be unusable if every field offered all thirty thousand."""
    tags = app_modules["tags"]
    framing = tags.search("close", categories=["perspective"])
    # `close to viewer` is a curated pov tag, filed under perspective too, so
    # the assertion is that everything returned is in the asked-for category
    # rather than that there is exactly one of them.
    assert "close-up" in [h["name"] for h in framing]
    assert all(h["category"] == "perspective" for h in framing)
    assert tags.search("close", categories=["clothing"]) == []


def test_search_needs_something_to_go_on(app_modules, corpus):
    assert app_modules["tags"].search("") == []
    assert app_modules["tags"].search("   ") == []


def test_search_finds_a_tag_typed_with_underscores(app_modules, corpus):
    """People paste from Danbooru, where tags have underscores."""
    hits = [h["name"] for h in app_modules["tags"].search("hair_between")]
    assert hits == ["hair between eyes"], hits


# --------------------------------------------------------------------------
# the fallback
# --------------------------------------------------------------------------

def test_autocomplete_works_before_any_import(app_modules):
    """The dictionary is an upgrade, not a setup step: with nothing imported
    the curated CG vocabulary stands in, and the UI is told which it is."""
    tags = app_modules["tags"]
    tags._cache["mtime"] = None
    status = tags.status()
    assert status["curated_only"] is True
    assert status["total"] > 300, "the curated vocabulary should still be there"
    assert [h["name"] for h in tags.search("from bel")] == ["from below"]


def test_curated_vocabulary_survives_an_import(app_modules, corpus):
    """A few curated phrasings are spelled differently on Danbooru, and the
    director is allowed to use them regardless of what the dump says."""
    tags = app_modules["tags"]
    names = {r["name"] for r in tags.load()}
    assert "imminent kiss" in names, "curated tags were dropped by the import"
    assert "close-up" in names
    assert tags.status()["curated_only"] is False


# --------------------------------------------------------------------------
# snapping and the director's validator
# --------------------------------------------------------------------------

def test_snap_fixes_the_near_misses_a_small_model_makes(app_modules, corpus):
    snap = app_modules["tags"].snap
    assert snap("hair_between_eyes") == "hair between eyes"
    assert snap("closed eye") == "closed eyes"        # missing plural
    assert snap("close up") == "close-up"            # hyphen drift
    assert snap("HUG") == "hug"
    assert snap("warm atmosphere") is None           # not a real tag at all
    assert snap("") is None


def test_the_validator_drops_tags_the_checkpoint_never_learned(app_modules, corpus):
    """The director really does invent these -- "warm atmosphere", "white
    knuckles" and "soft focus" between them accounted for thirty tags across
    one real project, none of which exist on Danbooru."""
    clean = app_modules["generation"].clean_cg_tags
    out = clean("close-up, warm atmosphere, white knuckles, soft focus, hug")
    assert out == "close-up, hug", out


def test_the_validator_snaps_rather_than_dropping_a_near_miss(app_modules, corpus):
    clean = app_modules["generation"].clean_cg_tags
    assert clean("close up, closed eye") == "close-up, closed eyes"


def test_the_validator_is_off_without_a_dictionary(app_modules):
    """With only the curated few hundred in memory, "unrecognised" would mean
    almost everything -- a validator that empties every prompt is far worse
    than one that does nothing."""
    tags, clean = app_modules["tags"], app_modules["generation"].clean_cg_tags
    tags._cache["mtime"] = None
    assert tags.has_corpus() is False
    # Unknown tags pass through, exactly as before the dictionary existed.
    assert "warm atmosphere" in clean("close-up, warm atmosphere")


def test_the_identity_ban_still_wins_over_the_dictionary(app_modules, corpus):
    """"closed eyes" is in both the ban's sights and the curated vocabulary,
    and the vocabulary has to keep winning -- it is a gaze direction, not an
    eye colour. But a real identity tag must not sneak back in just because
    Danbooru knows it."""
    clean = app_modules["generation"].clean_cg_tags
    assert clean("closed eyes") == "closed eyes"
    assert clean("hair between eyes") == "hair between eyes"   # curated
    # In the corpus, but banned and not curated.
    assert clean("blue eyes") == ""


# --------------------------------------------------------------------------
# categories
# --------------------------------------------------------------------------

def test_curated_tags_keep_their_hand_picked_category(app_modules):
    """Somebody sat and classified CG_VOCAB; that beats any pattern guess."""
    categorize = app_modules["tags"].categorize
    assert categorize("close-up") == "perspective"
    assert categorize("hug from behind") == "action"
    assert categorize("all fours") == "posture"
    assert categorize("collarbone") == "body"


def test_every_tag_lands_in_a_known_category(app_modules, corpus):
    tags = app_modules["tags"]
    for row in tags.load():
        assert row["category"] in tags.CATEGORIES, row
    # And the classifier never throws, whatever it is handed.
    for odd in ("", "   ", "^_^", "\\||/", "zzzz-nonsense", "0_0"):
        assert tags.categorize(odd) in tags.CATEGORIES


def test_the_import_job_never_takes_the_gpu(app_modules):
    """It is a few dozen HTTP round trips. Holding the GPU lock through it
    would stall a render queue behind a download."""
    from app.jobs import queue
    assert queue.is_gpu_kind("tags.import") is False


# --------------------------------------------------------------------------
# preference vs filter
# --------------------------------------------------------------------------

def test_preference_ranks_without_hiding(app_modules, corpus):
    """The classifier is a pattern guess -- it gets ~99% of the popular tags
    right and about half of the long tail. So the editor's fields express a
    *preference*, never a filter: the worst a wrong guess can then do is rank
    a tag second, rather than making it unreachable."""
    tags = app_modules["tags"]

    hits = tags.search("h", limit=20, prefer=["gaze"])
    names = [h["name"] for h in hits]
    # The preferred category leads...
    assert hits[0]["category"] == "gaze", names
    # ...but tags from other categories are still in the list.
    assert any(h["category"] != "gaze" for h in hits), names


def test_a_hard_filter_is_still_available(app_modules, corpus):
    """For deliberately browsing one category, as opposed to typing in a
    field that merely leans that way."""
    tags = app_modules["tags"]
    assert all(h["category"] == "perspective"
               for h in tags.search("close", categories=["perspective"]))
    assert tags.search("close", categories=["clothing"]) == []


def test_an_exact_match_outranks_the_preference(app_modules, corpus):
    """If the whole tag was typed, that is the tag -- whatever bucket the
    classifier put it in."""
    tags = app_modules["tags"]
    hits = tags.search("hug", limit=5, prefer=["perspective"])
    assert hits[0]["name"] == "hug", [h["name"] for h in hits]


def test_slots_are_reserved_so_a_preference_cannot_crowd_everything_out(
        app_modules, corpus):
    """A preference that fills every slot is a filter wearing a disguise. This
    is the case that caught it: ten slots, a dozen matches in the preferred
    category, and the one tag actually wanted pushed off the end -- reachable
    in principle, invisible in practice."""
    tags = app_modules["tags"]
    # Every "h..." tag in the fixture is preferred except `heart (symbol)`.
    hits = tags.search("h", limit=4, prefer=["gaze", "hair", "action", "body"])
    assert any(h["category"] not in ("gaze", "hair", "action", "body")
               for h in hits), [f'{h["name"]}:{h["category"]}' for h in hits]


def test_popularity_decides_within_a_tier(app_modules, corpus):
    """Length used to come first, which made a short obscure tag beat a long
    popular one. Where the short tag really is the intended one it is also the
    popular one, so counting posts gets both cases right."""
    tags = app_modules["tags"]
    hits = [h["name"] for h in tags.search("ha", limit=5)]
    # `hands` (250k) before `hand on another's shoulder` (9k).
    assert hits.index("hands") < hits.index("hand on another's shoulder")


# --------------------------------------------------------------------------
# categories are derived, not stored
# --------------------------------------------------------------------------

def test_categories_are_recomputed_from_the_name_on_load(app_modules, corpus):
    """Freezing them at import time meant every improvement to the classifier
    needed a re-import to take effect, and a corpus fetched under older rules
    kept answering with them. They are a function of the tag's own name, so
    they are computed where they are read."""
    import json
    tags = app_modules["tags"]

    path = tags.corpus_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Write a deliberately wrong category into the file.
    for row in payload["tags"]:
        if row["name"] == "close-up":
            row["category"] = "clothing"
    path.write_text(json.dumps(payload), encoding="utf-8")
    tags._cache["mtime"] = None

    loaded = {r["name"]: r["category"] for r in tags.load()}
    assert loaded["close-up"] == "perspective", (
        "the stored category was trusted instead of recomputed")
