"""A/B harness for the Roleplay prompt templates.

Prompt tuning by reading replies and forming an impression does not converge: the
failures that matter here are *distributional* -- every reply the same length,
every line tagged `neutral`, dialogue and narration alternating forever -- and you
cannot see a distribution by looking at one sample. So this measures instead.

    .venv/Scripts/python bench_roleplay.py --round 1 --seeds 2

Every variant is scored on identical inputs: the fixtures below are pre-authored
conversation states, never the model's own output, so nothing a variant does can
change what the next variant sees.

What is measured is only ever what the user actually complained about, and each
metric is written so that HIGHER IS BETTER and 1.0 is perfect. Nothing here scores
prose quality -- that is not measurable and is what the human rounds are for. This
exists to throw out the variants that are measurably broken, so that human
attention is spent on a shortlist.
"""
from __future__ import annotations

import argparse
import collections
import asyncio
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db, store                                          # noqa: E402
from app.clients import lmstudio                                   # noqa: E402
from app.models import (Character, Personality, Persona, RPConversation,  # noqa: E402
                        RPMessage, RolePlay)
from app.services import roleplay                                  # noqa: E402

BENCH_PROJECT = "RP bench"

# --------------------------------------------------------------------------
# the cast, fixed so a run is comparable with the run before it
# --------------------------------------------------------------------------

ALICE = Character(
    id="alice", display_name="Alice", age=26,
    personality=Personality(
        summary="Runs the hardware shop her father left her. Competent, dry, tired.",
        speech_style=("Short declaratives. Rarely finishes a thought she thinks is "
                      "obvious. Never asks how you feel."),
        speech_register="plain, unhurried, a little blunt",
        wants="to be taken seriously without having to ask",
        fears="that staying here was the whole decision, already made",
        relationship_to_player="Knew you at school. Has been almost-friendly for a month.",
        likes="early mornings, fixing things that are nearly beyond fixing",
        dislikes="being managed, small talk about the weather",
    ))
BEA = Character(
    id="bea", display_name="Bea", age=31,
    personality=Personality(
        summary="Alice's older sister. Warm, talks too much, misses nothing.",
        speech_style="Long sentences that double back. Asks three questions at once.",
        relationship_to_player="Decided she likes you before meeting you.",
    ))
PLAYER = Character(id="me", display_name="Sam", age=27, gender="male", is_player=True)


# --------------------------------------------------------------------------
# fixtures: identical inputs for every variant
# --------------------------------------------------------------------------

@dataclass
class Fixture:
    name: str
    context: str
    # (role, text) in order. The last player message, if any, is the turn.
    history: list[tuple[str, str]] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    summary: str = ""


OPENING = ("Alice (neutral, tired): \"We close in ten minutes.\"\n"
           "\n"
           "She goes back to squaring up the notes in the till drawer.")

FIXTURES = [
    Fixture(
        name="open",
        context="Sam has come by the shop just before closing, with no real reason.",
    ),
    Fixture(
        name="short-reply",
        context="Sam has come by the shop just before closing, with no real reason.",
        history=[("cast", OPENING), ("player", "Nothing. Just walking.")],
    ),
    Fixture(
        name="action-and-steer",
        context="Sam has come by the shop just before closing, with no real reason.",
        history=[
            ("cast", OPENING),
            ("player", "Nothing. Just walking."),
            ("cast", 'Alice (smirking, amused, 0.4): "Walking. Right."'),
            ("player", "*picks a bolt off the counter and turns it over* "
                       "[Alice should ask him something she has been putting off]"),
        ],
    ),
    Fixture(
        name="silence",
        context="Sam has come by the shop just before closing, with no real reason.",
        history=[
            ("cast", OPENING),
            ("player", "Nothing. Just walking."),
            ("cast", 'Alice (looking_away, anxious, 0.5): "Can I ask you something."'),
        ],
    ),
    Fixture(
        name="one-word",
        context="Sam has come by the shop just before closing, with no real reason.",
        history=[
            ("cast", OPENING),
            ("player", "Nothing. Just walking."),
            ("cast", 'Alice (looking_away, anxious, 0.5): "Can I ask you something."'),
            ("cast", "She puts the notes down without counting the rest of them."),
            ("player", "Maybe."),
        ],
    ),
    Fixture(
        name="two-cast",
        context="Bea has turned up at the shop while Sam is already there.",
        cast=["bea"],
        history=[
            ("cast", 'Bea (grinning, excited, 0.6): "So this is where you get to."\n'
                     "\n"
                     "She is through the door before Alice has looked up.\n"
                     "\n"
                     'Alice (frowning, irritated): "It is a shop. People come in."'),
            ("player", "She does have a point."),
        ],
    ),
    Fixture(
        name="with-memory",
        context="A week later. Sam is back, and neither of them has mentioned it.",
        summary=("PLOT: Sam came by at closing. Alice nearly asked him something and "
                 "stopped. He left without pushing.\n"
                 "BETWEEN THEM: She has started speaking first. She is aware he "
                 "noticed her hesitate, and has not forgiven herself for it.\n"
                 "ESTABLISHED: The shop shuts at six. She has her father's keys on a "
                 "ring she never puts down. It rained that evening.\n"
                 "UNRESOLVED: What she was going to ask."),
        history=[("cast", 'Alice (neutral, neutral): "You are back, then."'),
                 ("player", "I am.")],
    ),
]


# --------------------------------------------------------------------------
# scoring -- higher is better, 1.0 is perfect, every metric a stated complaint
# --------------------------------------------------------------------------

_SAY = re.compile(r'^\s*([^:"\n()\[\]]{1,40}?)\s*[(\[]([^)\]\n]*)[)\]]\s*:\s*(.*)$')
_SAY_UNTAGGED = re.compile(r'^\s*([^:"\n()\[\]]{1,40}?)\s*:\s*["“](.*)$')
_BRACKET_LINE = re.compile(r"^\s*[\[(][^\]\)]*[\]\)]\s*$")
_ATTRIBUTED = re.compile(r'["”]\s*,?\s*(?:she|he|they)\s+(?:says|said|asks|asked|adds)',
                         re.IGNORECASE)
# The constructions the prompt bans outright.
_SLOP = [
    re.compile(r"\w\s+--\s+\w"),                               # em dash as a pause
    re.compile(r"\bnot\s+(?:a|an|the)?\s*\w+[.,]\s*(?:but|it was)\b", re.IGNORECASE),
    re.compile(r"\b(?:a beat pass|the silence (?:stretch|hung)|a long moment)",
               re.IGNORECASE),
]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


@dataclass
class Reply:
    fixture: str
    seed: int
    text: str

    @property
    def lines(self) -> list[str]:
        return [l for l in self.text.splitlines() if l.strip()]


#: A quoted stretch of at least three words. Used to catch dialogue that was
#: written without the `Name (tags):` prefix, which is the failure the `say`
#: patterns below cannot see -- a line like
#:
#:     "Right." (looking_up, neutral) "Anything else you need?"
#:
#: matches neither, so it used to be filed as narration. That scored `format` a
#: perfect 1.000 on output containing unattributable speech, and deflated
#: `dialogue_ratio` at the same time by counting spoken words as narration. Both
#: numbers were wrong in the same place and in opposite directions.
_QUOTED_SPEECH = re.compile(r'["“][^"”]*(?:\s+\S+){2,}[^"”]*["”]')


def beats(reply: Reply) -> list[tuple[str, str]]:
    """(kind, tags) per line.

    kind is say / say-untagged / orphan-speech / narration / bracket.
    `orphan-speech` is a line that is plainly dialogue but names nobody.
    """
    out = []
    for line in reply.lines:
        if _BRACKET_LINE.match(line):
            out.append(("bracket", ""))
        elif (m := _SAY.match(line)):
            out.append(("say", m.group(2)))
        elif _SAY_UNTAGGED.match(line):
            out.append(("say-untagged", ""))
        elif _QUOTED_SPEECH.search(line):
            out.append(("orphan-speech", ""))
        else:
            out.append(("narration", ""))
    return out


def score(replies: list[Reply], player_names: set[str]) -> dict[str, float]:
    """One score per metric across a whole variant's output."""
    all_beats = [beats(r) for r in replies]
    flat = [b for bs in all_beats for b in bs]
    # Orphan speech counts as a spoken line for every purpose except being
    # correctly formatted -- it IS dialogue, it just names nobody.
    says = [b for b in flat if b[0] in ("say", "say-untagged", "orphan-speech")]

    # 1. Format: every spoken line is `Name (tags): "line"`. Three ways to fail --
    #    no bracket, prose with an attribution ("...," she says), and dialogue with
    #    no speaker at all.
    tagged = sum(1 for b in says if b[0] == "say")
    attributed = sum(1 for r in replies for l in r.lines if _ATTRIBUTED.search(l))
    fmt = (tagged / len(says)) if says else 0.0
    fmt *= 1.0 if not attributed else max(0.0, 1 - attributed / max(1, len(flat)))

    # 2. Emotion spread. The failure is a flat readout -- one feeling on nearly
    #    every line -- which also makes the voice a monotone, since the bracket is
    #    what TTS reads.
    #
    #    Measured as the share NOT carrying the single commonest feeling, not as
    #    distinct/total. The first formulation was wrong in a way worth recording:
    #    with ~38 tagged lines and 24 legal emotions, distinct/total is bounded
    #    well below 1.0 and punishes repetition that is correct. It scored every
    #    variant 0.38-0.44 and read as a serious failure, but the underlying data
    #    was 14-17 distinct feelings per run with `neutral` on only 3-5 lines --
    #    genuinely varied. The commonest tag was `tired`, on a character whose card
    #    says "Competent, dry, tired". That is characterisation working, and the
    #    metric was marking it down.
    #
    #    Full marks at a top share of 0.25 or less, which is roughly what the good
    #    observed runs do.
    feelings = []
    for b in says:
        if b[0] != "say":
            continue
        parts = [p.strip().lower() for p in b[1].split(",") if p.strip()]
        if parts:
            feelings.append(parts[1] if len(parts) > 1 else parts[0])
    if feelings:
        top = max(collections.Counter(feelings).values())
        variety = min(1.0, (1 - top / len(feelings)) / 0.75)
    else:
        variety = 0.0

    # 3. The player's side stays the player's. Any line written for them, and any
    #    bracket-only line (which is how a model narrates them).
    violations = sum(1 for r in replies
                     if roleplay.turn_end(r.text, player_names) is not None)
    violations += sum(1 for b in flat if b[0] == "bracket")
    pc = max(0.0, 1 - violations / max(1, len(replies)))

    # 4. Length variety across replies, as a coefficient of variation.
    #
    #    The opening turn is excluded: it legitimately has a room to establish, so
    #    leaving it in gave every variant a free 1.0 from the gap between it and
    #    everything else.
    #
    #    Full marks at CV 1.0, not 0.5. Measured across the first round, every
    #    variant sat between 0.65 and 0.90 with replies running from 7 to 138
    #    words -- so at a 0.5 target all five scored exactly 1.000 and the metric
    #    separated nothing. Worth knowing in itself: **uniform length was never
    #    one of this prompt's failures**, which is why the weight below is low. It
    #    stays in to catch a future variant that flattens.
    lengths = [len(_norm(r.text)) for r in replies if r.fixture != "open"]
    mean = statistics.fmean(lengths) if lengths else 0
    cv = (statistics.pstdev(lengths) / mean) if len(lengths) > 1 and mean else 0.0
    length = min(1.0, cv / 1.0)

    # 5. Openings differ. Two replies starting on the same three words is the tic
    #    that makes fifty replies feel like one.
    #
    #    Compared across FIXTURES WITHIN ONE SEED -- which is what a conversation
    #    is -- and never across seeds of the same fixture. Pooling every reply
    #    together instead meant the number was dominated by two seeds of one
    #    fixture opening alike, which is sampling variance and not the tic; it
    #    returned 0.643 for all five variants in the first round, an artifact that
    #    looked exactly like a real result.
    conv = [r for r in replies if r.fixture != "open"]
    groups = []
    for seed in sorted({r.seed for r in conv}):
        heads = [" ".join(_norm(r.text)[:3]) for r in conv
                 if r.seed == seed and _norm(r.text)]
        if heads:
            groups.append(len(set(heads)) / len(heads))
    openings = statistics.fmean(groups) if groups else 0.0

    # 6. Dialogue carries the scene; narration is the exception.
    dialogue = (len(says) / len(flat)) if flat else 0.0

    # 7. Not locked into say / narration / say / narration.
    #
    #    The floor is 4 beats, and getting there took two corrections. It started
    #    at 4, was lowered to 3 because only two replies in seven qualified, and is
    #    back at 4 because 3 was flagging good writing: a three-beat reply that
    #    alternates is `spoken, action, spoken` -- dialogue interrupted by something
    #    physical -- which is one of the better shapes available, not a tic. Five of
    #    the fifteen replies this metric was penalising were that. Scoring it as a
    #    failure cost about 0.15 on every variant and pointed a whole round at a
    #    problem that was partly an artifact.
    #
    #    Four beats is where mechanical alternation starts to read as mechanical.
    locked = 0
    counted = 0
    for bs in all_beats:
        kinds = [k for k, _ in bs if k != "bracket"]
        if len(kinds) < 4:
            continue
        counted += 1
        if all(a != b for a, b in zip(kinds, kinds[1:])):
            locked += 1
    lockstep = 1.0 - (locked / counted) if counted else 1.0

    # 8. Shape variety across replies -- the complaint the metric above cannot see.
    #    "Every reply built the same way" is about the DISTRIBUTION of shapes, not
    #    about any one reply being alternating, so it is measured as how much of the
    #    output the single commonest shape accounts for. A shape here is the
    #    sequence of spoken/narration beats: `NSNS`, `SNS`, and so on.
    #
    #    Full marks at a top share of 0.25 or less, the same calibration as emotion
    #    spread, and for the same reason: some repetition is correct.
    shapes = []
    for bs in all_beats:
        kinds = ["S" if k in ("say", "say-untagged", "orphan-speech") else "N"
                 for k, _ in bs if k != "bracket"]
        if kinds:
            shapes.append("".join(kinds))
    if shapes:
        top_shape = max(collections.Counter(shapes).values())
        shape_variety = min(1.0, (1 - top_shape / len(shapes)) / 0.75)
    else:
        shape_variety = 0.0

    # 8. None of the banned constructions.
    slop = sum(1 for r in replies for p in _SLOP if p.search(r.text))
    clean = max(0.0, 1 - slop / max(1, len(replies)))

    return {
        "format": round(fmt, 3),
        "emotion_spread": round(variety, 3),
        "player_respected": round(pc, 3),
        "length_variety": round(length, 3),
        "distinct_openings": round(openings, 3),
        "dialogue_ratio": round(dialogue, 3),
        "no_lockstep": round(lockstep, 3),
        "shape_variety": round(shape_variety, 3),
        "no_slop": round(clean, 3),
        # Not scored, but reported: the denominators, because a metric over two
        # samples looks like a result and is not one.
        "_n_replies": len(replies),
        "_n_say": len(says),
        "_n_orphan_speech": sum(1 for b in flat if b[0] == "orphan-speech"),
        "_n_narration": sum(1 for b in flat if b[0] == "narration"),
        "_n_rhythm_countable": counted,
        "_n_distinct_shapes": len(set(shapes)),
        # The length round's whole question: is there a floor, and does the model
        # ever write a genuinely short reply? `open` is excluded for the same reason
        # it is excluded from length_variety -- it has a room to set up.
        "_min_words": min(lengths) if lengths else 0,
        "_n_short": sum(1 for w in lengths if w <= 15),
    }


def per_fixture(replies: list[Reply]) -> dict[str, dict[str, float]]:
    """Dialogue ratio and length per fixture, so a bad average can be located.

    The average hid the finding that mattered most in the first run: overall
    dialogue ratio was 0.435, and almost all of the narration was in the opening
    turn -- one spoken line against eight of narration -- while the conversational
    turns were fine. A single number would have sent the next round after the
    wrong thing.
    """
    out: dict[str, dict[str, float]] = {}
    for fixture in {r.fixture for r in replies}:
        mine = [r for r in replies if r.fixture == fixture]
        flat = [b for r in mine for b in beats(r)]
        says = sum(1 for k, _ in flat if k.startswith("say") or k == "orphan-speech")
        out[fixture] = {
            "dialogue_ratio": round(says / len(flat), 3) if flat else 0.0,
            "words": round(statistics.fmean(len(_norm(r.text)) for r in mine), 1),
        }
    return out


# Weights: what the user said matters most, in order. `format` and
# `player_respected` are near-vetoes -- a variant that fails either is unusable
# whatever else it does well.
WEIGHTS = {
    "format": 3.0,
    "player_respected": 3.0,
    # Raised after the first round: the flat readout is the failure that is both
    # most visible in the text and most damaging downstream, since the bracket is
    # what the voice is read from. Every variant scored 0.40-0.50 here.
    "emotion_spread": 2.5,
    "dialogue_ratio": 2.0,       # 0.06-0.27 on the opening turn, every variant
    "distinct_openings": 1.5,
    "no_lockstep": 1.5,
    # The distributional half of the same complaint: not "is this reply
    # alternating" but "is every reply built the same way".
    "shape_variety": 1.5,
    "no_slop": 1.0,
    # Low on purpose: measured at CV 0.65-0.90, uniform length is not one of this
    # prompt's problems. Kept as a regression guard, not as a target.
    "length_variety": 0.5,
}


def overall(scores: dict[str, float]) -> float:
    total = sum(WEIGHTS.values())
    return round(sum(scores[k] * w for k, w in WEIGHTS.items()) / total, 3)


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

def setup_project() -> str:
    for p in store.list_projects():
        if p.name == BENCH_PROJECT:
            for conv in store.list_conversations(p.id):
                store.delete_conversation(conv.id)
            return p.id
    project = store.create_project(BENCH_PROJECT)
    project.world.setting = "A small coastal town. Present day."
    project.world.style_notes = "Restrained. Concrete detail. Nobody explains themselves."
    project.roleplay = RolePlay(persona=Persona(
        name="Sam", description="27, tall, works with his hands, back in town after "
                                "a decade away", notes="Grew up here. Left. Came back."))
    store.save_project(project)
    for c in (ALICE, BEA, PLAYER):
        store.save_character(project.id, c)
    return project.id


#: Set from --fixtures; empty means all of them.
ONLY: set[str] = set()


def chosen_fixtures() -> list[Fixture]:
    return [f for f in FIXTURES if not ONLY or f.name in ONLY]


def build(project_id: str, fixture: Fixture) -> RPConversation:
    """A fresh conversation in exactly the fixture's state."""
    if fixture.summary:
        past = RPConversation(
            id=store.new_id("rc_"), project_id=project_id, character_id="alice",
            title="Earlier", summary=fixture.summary, state="ended", sort_key=1.0)
        store.insert_conversation(past)
    conv = RPConversation(
        id=store.new_id("rc_"), project_id=project_id, character_id="alice",
        cast=fixture.cast, scene_context=fixture.context, sort_key=99.0)
    store.insert_conversation(conv)
    for role, text in fixture.history:
        store.insert_message(RPMessage(
            id=store.new_id("m_"), conv_id=conv.id, seq=store.next_seq(conv.id),
            role=role, text=text, created_at=store.now()))
    return conv


async def generate(messages: list[dict[str, str]], model: str, params: dict,
                   seed: int, label: str, fixture: str) -> Optional[str]:
    """One generation, surviving the model going away underneath it.

    A run is 126 requests over half an hour, and anything that interrupts LM
    Studio in that window used to lose the whole run: one 400 from an unloaded
    model raised, and the seventy-nine generations already completed went with it.
    Measured the hard way.

    So a failure costs one sample and says so. Reloading is attempted once,
    because the common cause is exactly that -- the model is no longer resident --
    and `lmstudio.load` is the documented way back.
    """
    for attempt in (1, 2):
        try:
            return await lmstudio.chat(messages, model=model,
                                       params={**params, "seed": 1000 + seed})
        except lmstudio.LMStudioError as exc:
            print(f"    !! {label} / {fixture} seed {seed}: {str(exc)[:110]}",
                  file=sys.stderr)
            if attempt == 2:
                return None
            ok, note = await lmstudio.load(model)
            print(f"    .. reload: {note[:110]}", file=sys.stderr)
            if not ok:
                return None
    return None


async def run_variant(project_id: str, label: str, bodies: dict[str, str],
                      params: dict, seeds: int, model: str
                      ) -> tuple[list[Reply], dict]:
    """Run one variant. `bodies` maps a template key to the body to test with.

    Both `rp.system` and `rp.turn_directive` are variable, because the first round
    showed the thing most worth fixing lives in the directive: the opening turn
    came back at a dialogue ratio of 0.11 against 0.50-1.00 for every
    conversational turn, and the opening directive is what asks for it.

    A key named `setting_<name>` overrides that setting for this variant instead of
    being saved as a template. Some of what the prompt says is not IN the prompt --
    `{{ reply_length }}` is the `rp_reply_length` setting -- and a round about reply
    length that could not vary it would be testing the wrong text.
    """
    restore: dict[str, Any] = {}
    for key, body in bodies.items():
        if key.startswith("setting_"):
            name = key[len("setting_"):]
            restore[name] = store.get_setting(name)
            store.set_setting(name, body)
            continue
        store.save_template_version(project_id, key, body,
                                    params if key == "rp.system" else {})
    characters = store.list_characters(project_id)
    persona = roleplay.resolve_persona(store.get_project(project_id),
                                       {c.id: c for c in characters})
    names = roleplay.player_names(persona, characters)

    replies: list[Reply] = []
    for fixture in chosen_fixtures():
        for conv in store.list_conversations(project_id):
            store.delete_conversation(conv.id)
        conv = build(project_id, fixture)
        assembled = roleplay.assemble_turn(project_id, conv)
        for seed in range(seeds):
            raw = await generate(assembled["messages"], model, params, seed, label,
                                 fixture.name)
            if raw is None:
                continue
            replies.append(Reply(fixture.name, seed,
                                 roleplay.clean_reply(raw, names, assembled["system"],
                                                     attribute=False)))
            print(f"    {label:<22} {fixture.name:<18} seed {seed}  "
                  f"{len(replies[-1].text):>4} chars", file=sys.stderr)
    for name, was in restore.items():
        store.set_setting(name, was)

    if not replies:
        return [], {"label": label, "scores": score([], names), "overall": 0.0,
                    "per_fixture": {}, "failed": "every generation failed"}
    scores = score(replies, names)
    return replies, {"label": label, "scores": scores, "overall": overall(scores),
                     "per_fixture": per_fixture(replies)}


def report(results: list[dict]) -> None:
    results.sort(key=lambda r: -r["overall"])
    keys = list(WEIGHTS)
    width = max(len(r["label"]) for r in results) + 2
    print("\n" + " " * width + "  ".join(f"{k[:9]:>9}" for k in keys) + "   OVERALL")
    for r in results:
        row = "  ".join(f"{r['scores'][k]:>9.3f}" for k in keys)
        print(f"{r['label']:<{width}}{row}   {r['overall']:>7.3f}")

    n = results[0]["scores"]
    print(f"\nsamples: {n['_n_replies']} replies per variant, "
          f"{n['_n_rhythm_countable']} long enough to score rhythm")

    # Raw counts, not just the ratios. A format score of 0.90 against 0.95 is two
    # orphan lines against one, and at that sample size the difference is not a
    # result -- printing the denominator is what stops it being read as one.
    print("\nraw counts per variant")
    print(f"{'':<26}{'spoken':>8}{'narration':>11}{'orphaned':>10}{'orphan %':>10}"
          f"{'shapes':>8}{'shortest':>10}{'<=15w':>7}")
    for r in results:
        s = r["scores"]
        says, orphans = s["_n_say"], s.get("_n_orphan_speech", 0)
        share = (100 * orphans / says) if says else 0.0
        print(f"{r['label']:<26}{says:>8}{s['_n_narration']:>11}"
              f"{orphans:>10}{share:>9.1f}%{s.get('_n_distinct_shapes', 0):>8}"
              f"{s.get('_min_words', 0):>10}{s.get('_n_short', 0):>7}")

    fixtures = sorted(results[0].get("per_fixture", {}))
    if fixtures:
        print("\ndialogue ratio by fixture (words in brackets)")
        print(" " * width + "  ".join(f"{f[:13]:>15}" for f in fixtures))
        for r in results:
            cells = []
            for f in fixtures:
                pf = r["per_fixture"][f]
                cells.append(f"{pf['dialogue_ratio']:>7.2f} ({pf['words']:>4.0f})")
            print(f"{r['label']:<{width}}" + "  ".join(cells))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="", help="path to a JSON file of {label: body}")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--model", default="")
    ap.add_argument("--out", default="")
    # Re-measure a finished run instead of generating again. Every reply is saved,
    # so improving a metric costs nothing and never invalidates an earlier round.
    ap.add_argument("--rescore", default="", help="path to a previous --out file")
    # Narrow to particular fixtures. Useful when one turn is the whole question
    # -- the opening turn was -- and re-running all seven to answer it spends
    # most of the GPU time on turns that are already fine.
    #
    # Read the individual metrics, not OVERALL, when this is set: several of them
    # exclude the opening turn by design, so `--fixtures open` leaves them with no
    # data and they come back 0.000, which drags a meaningless OVERALL down with
    # them.
    ap.add_argument("--fixtures", default="", help="comma-separated fixture names")
    args = ap.parse_args()

    db.init_db()
    global ONLY
    ONLY = {f.strip() for f in args.fixtures.split(',') if f.strip()}

    if args.rescore:
        saved = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        project_id = setup_project()
        characters = store.list_characters(project_id)
        persona = roleplay.resolve_persona(store.get_project(project_id),
                                          {c.id: c for c in characters})
        names = roleplay.player_names(persona, characters)
        rescored = []
        for label, rows in saved["replies"].items():
            replies = [Reply(r["fixture"], r["seed"], r["text"]) for r in rows]
            scores = score(replies, names)
            rescored.append({"label": label, "scores": scores,
                             "overall": overall(scores),
                             "per_fixture": per_fixture(replies)})
        report(rescored)
        if args.out:
            saved["results"] = rescored
            Path(args.out).write_text(json.dumps(saved, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
            print(f"\nwrote {args.out}")
        return

    project_id = setup_project()
    model = args.model or store.get_setting("lm_studio_prose_model", "") or ""

    from app.defaults import RP_SAMPLING, _RP_SYSTEM, _RP_TURN
    if args.variants:
        raw = json.loads(Path(args.variants).read_text(encoding="utf-8"))
    else:
        raw = {"shipped default": _RP_SYSTEM}

    # A variant is either a bare system body, or {system, directive} for one that
    # changes the post-history instruction too. Anything left out falls back to the
    # shipped default, so a directive-only variant is a one-key object.
    variants: dict[str, dict[str, str]] = {}
    for label, value in raw.items():
        if isinstance(value, str):
            variants[label] = {"rp.system": value, "rp.turn_directive": _RP_TURN}
        else:
            variants[label] = {
                "rp.system": value.get("system", _RP_SYSTEM),
                "rp.turn_directive": value.get("directive", _RP_TURN),
            }

    params = {**RP_SAMPLING}
    results = []
    everything = {}
    for label, bodies in variants.items():
        replies, result = await run_variant(project_id, label, bodies, params,
                                           args.seeds, model)
        results.append(result)
        everything[label] = [{"fixture": r.fixture, "seed": r.seed, "text": r.text}
                             for r in replies]

    report(results)

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"model": model, "results": results, "replies": everything},
            indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
