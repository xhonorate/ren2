"""Roleplay: a live conversation with the cast, kept out of the graph.

Improv, which this replaces, tried to be a conversation and a graph editor at
once: every typed line became a `say` node and every reply was a generation
spliced into a scene's walk. Most of that module was splice points, rewind
bounded by the scene, and reconnecting scene exits -- and the cost was not just
complexity. A turn paid for the whole scene pipeline (prose pass, structuring
pass, staging reconciliation, nodes, edges, variants, staleness), and it was
prompted with a scene-writing prompt that had to be argued out of emitting
CHOICE blocks.

So a conversation lives in its own two tables (`db.rp_conversations`,
`db.rp_messages`) and touches nothing the compiler reads. The one bridge is an
explicit, one-way port that builds fresh nodes from a finished conversation.

Three things carry the quality, and all three are in `assemble_turn`:

* **The transcript is sent as real `user`/`assistant` turns.** This is the whole
  ballgame. A 24B instruct model is trained on that shape, and it stops at the
  user's turn by reflex -- so "never write the player's lines" is enforced by
  the structure of the request rather than by a regex racing the token stream.
  `turn_end` survives below as a safety net and should essentially never fire.
* **The directive goes on the LAST user turn**, not in the system prompt and not
  as a trailing system message. Last position is where a local model actually
  obeys; and appending rather than adding keeps the list strictly alternating,
  which Mistral-family chat templates require.
* **Memory is summaries, not transcript.** Earlier scenes arrive as their
  distilled summaries in the system block. The previous scene's tail is included
  too, but under a heading saying it is over -- sending it as turns reads as the
  scene still being live and the model just carries on with it.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .. import store
from ..graph import stat_defaults
from ..models import (EMOTIONS, EXPRESSIONS, POSES, Character, NodeType,
                      Persona, RPConversation, RPMessage)
from . import context as ctx_mod


# --------------------------------------------------------------------------
# the player's text
# --------------------------------------------------------------------------
#
# The player types one box, and it can carry three different things:
#
#   Fine. *drops into the chair* [Alice should not let this go]
#
# Speech is the default. `*asterisks*` are something they do. `[brackets]` are a
# note to the writer about where the scene should go -- said by nobody, heard by
# nobody. They are stored exactly as typed (so an edit shows what was written)
# and separated here, on the way into the prompt.

_STEER = re.compile(r"\[([^\]\n]*)\]")
_ACTION = re.compile(r"\*([^*\n]+)\*")


def split_player_text(text: str) -> dict[str, Any]:
    """`{spoken, actions, steer}` from one typed message.

    `spoken` keeps its asterisk actions in place -- the model reads that
    convention natively and the two are interleaved on purpose ("Fine. *sits*"
    is one gesture, not a line plus a stage direction). `actions` is extracted
    separately only for the port, which has to turn each one into its own
    narration beat.
    """
    raw = (text or "").strip()
    steer = " ".join(m.strip() for m in _STEER.findall(raw) if m.strip())
    body = _STEER.sub("", raw)
    actions = [m.strip() for m in _ACTION.findall(body) if m.strip()]
    # Collapse the holes the bracket removal left, so "Fine. [note] Really."
    # does not reach the model as "Fine.  Really."
    body = re.sub(r"[ \t]{2,}", " ", body).strip()
    return {"spoken": body, "actions": actions, "steer": steer}


# --------------------------------------------------------------------------
# the safety net
# --------------------------------------------------------------------------
#
# Carried over from services/improv.py, where it was load-bearing: everything
# went to the model as one system+user pair, so it wrote the player's lines
# routinely and this cut it off mid-stream. With a real multi-turn request it
# should never fire. It stays because the cost of being wrong is asymmetric --
# a reply that puts words in the player's mouth is the one failure that cannot
# be walked back by the next turn -- and because a hand-edited `rp.system` can
# always undo the framing that makes it unnecessary.

# A line spoken by someone: a name, an optional performance bracket, a colon.
# No quote is required, so a bare `You: I nod.` still counts as the player's
# turn.
_SPEAKER_HEAD = re.compile(
    r"^[ \t*]*(?P<name>[^:\"\n()\[\]*]{1,40}?)[ \t*]*(?:[(\[][^)\]\n]*[)\]][ \t]*)?:")
# The author-facing blocks a scene generation ends on. A conversation has no use
# for either, and both are long.
_END_BLOCK = re.compile(r"^[ \t]*(?:CHOICE|CONTINUE)[ \t]*:", re.IGNORECASE)
# Turn markers from a generic chat transcript. A model that starts writing these
# has stopped answering and started transcribing a conversation -- it is
# reproducing the exchange rather than continuing it, and everything from the
# marker on is a replay of what it can already see. Observed for real from
# Cydonia 24B: `Human: Nothing. Just walking.` / `AI: Alice (smirking...`
# mid-reply, which is how the whole transcript came back as one message.
_TURN_MARKER = re.compile(
    r"^[ \t]*(?:Human|AI|User|Assistant|System)[ \t]*:", re.IGNORECASE)


def player_names(persona: Persona, characters: list[Character]) -> set[str]:
    """Every way a reply might head a line with the player's name.

    `you` is always in: the house style examples write the player as `You:`, and
    a writer model copies examples harder than it follows rules. A non-player
    character literally called one of these keeps their name -- being able to
    play a character called "You" matters less than not silently truncating
    every reply she speaks first in.
    """
    names = {"you", (persona.name or "").strip().lower()}
    for c in characters:
        if c.is_player:
            names |= {c.display_name.strip().lower(), c.id.lower(),
                      c.id.replace("_", " ").lower()}
    others = {c.display_name.strip().lower() for c in characters if not c.is_player}
    return {n for n in names if n and n not in others}


def turn_end(text: str, names: set[str]) -> Optional[int]:
    """Where a reply stops being the cast's turn, or None.

    Checked against partial text as it streams, so a player line is caught as
    soon as its colon arrives -- before a word of it has been written.
    """
    pos = 0
    for line in (text or "").splitlines(keepends=True):
        if _END_BLOCK.match(line) or _TURN_MARKER.match(line):
            return pos
        head = _SPEAKER_HEAD.match(line)
        if head and head.group("name").strip().lower() in names:
            return pos
        pos += len(line)
    return None


def trim_reply(text: str, names: set[str]) -> str:
    cut = turn_end(text, names)
    return (text if cut is None else text[:cut]).strip()


# A line that is nothing but a bracketed aside. The output format forbids square
# brackets outright, so one of these is always a format violation -- and the two
# it produces are both bad in the same way. A model hands the directive back
# (`[The scene begins.]`, `[Write what happens next.]`), or it writes the player's
# side as a stage direction (`[You are sitting on a stool by the counter]`), which
# is exactly the thing the prompt spends a section forbidding. Dropping the line
# is right in both cases: there is never anything in it worth keeping.
_BRACKET_LINE = re.compile(r"^[ \t>*_-]*[\[(][^\]\)]*[\]\)][ \t*_]*$")


#: A line short enough that finding it in the prompt is a coincidence rather than
#: a copy. `"Closed."` appears in a character card and is also a perfectly good
#: line of dialogue.
_ECHO_MIN_CHARS = 24


def strip_prompt_echo(reply: str, system: str) -> str:
    """Drop leading lines the reply copied out of the system prompt.

    Measured on Cydonia 24B v4.3 and the same underlying fault as `echoes`: on the
    opening turn there is no assistant message to continue, so the model continues
    the *prompt* instead, and replies begin by reciting the character card --

        26, female.
        Who they are: Runs the hardware shop her father left her...
        SPEECH -- short declaratives. Rarely finishes a thought...

    Worth catching in its own right, and worth catching separately from `echoes`:
    it is not a repeat of a previous reply, it hits the very first turn of a scene
    (where there is nothing to fall back on), and it silently poisons any
    measurement of how much narration an opening contains, because recited card
    lines score as narration.

    Only leading lines, and only ones long enough that the match cannot be
    chance. Nothing legitimate opens a scene by quoting the cast list.
    """
    if not system:
        return reply.strip()
    haystack = _norm(system)
    lines = reply.splitlines()

    # Walk the leading run and remember the last point that was a *confirmed* long
    # copy. Three cases, and the middle one is the subtle one:
    #
    #  * long and in the prompt  -> a copy. Confirm, and cut to here.
    #  * short and in the prompt -> carry on, but do not confirm. This is what
    #    lets `26, female.` through -- nine characters, far too short to match on
    #    by itself, and the card lines worth removing sit behind it.
    #  * not in the prompt       -> the reply proper has started. Stop.
    #
    # A short line is only stepped over when it appears in the prompt too.
    # Stepping over any short line instead would swallow a legitimate one-line
    # opening that happened to be followed by a recited card line.
    cut = 0
    at = 0
    while at < len(lines):
        probe = _norm(lines[at])
        if not probe:
            at += 1
            continue
        if probe not in haystack:
            break
        at += 1
        if len(probe) >= _ECHO_MIN_CHARS:
            cut = at

    # Nothing was confirmed, so the short lines at the front were just a short
    # opening line and are left alone.
    return "\n".join(lines[cut:]).strip()


# A well-formed spoken line: `Name (face, feeling): "words"`.
_SAY_LINE = re.compile(
    r'^\s*(?P<name>[^:"\n()\[\]]{1,40}?)\s*[(\[](?P<tags>[^)\]\n]*)[)\]]\s*:\s*(?P<rest>.*)$')
# Dialogue that kept its performance bracket and lost only the name:
#     (thinking, quieter): "Or did you come here to do something."
_ORPHAN_TAGGED = re.compile(r'^\s*[(\[](?P<tags>[^)\]\n]*)[)\]]\s*:\s*(?P<rest>["“].*)$')
# Dialogue that is one quoted span and nothing else -- no name, no bracket.
_ORPHAN_BARE = re.compile(r'^\s*(?P<rest>["“][^"”]*["”])\s*$')


def attribute_orphans(text: str) -> str:
    """Give a speaker back to dialogue that lost its name.

    Measured on 24 such lines across a bench run: 19 of them are a single quoted
    span, and 7 of those had kept the performance bracket and dropped only the
    name. So most of this failure is recoverable from context rather than being
    genuinely ambiguous, and recovering it matters -- a line with no speaker
    cannot be routed to a character's voice and cannot become a say node, so it is
    lost twice over.

    Only ever attributed to a speaker already named EARLIER IN THE SAME REPLY.
    A reply that opens on an orphan line is left alone: with two characters on
    stage there is nothing to infer from, and guessing wrong puts words in the
    wrong mouth, which is worse than leaving the line unattributed.

    Lines with more than one quoted span are also left alone. Those are dialogue
    with narration wedged between, and splitting them correctly is guesswork.
    """
    out: list[str] = []
    speaker = ""
    last_tags = ""
    for line in text.splitlines():
        said = _SAY_LINE.match(line)
        if said:
            speaker = said.group("name").strip()
            last_tags = said.group("tags").strip()
            out.append(line)
            continue
        if speaker:
            tagged = _ORPHAN_TAGGED.match(line)
            if tagged:
                out.append(f"{speaker} ({tagged.group('tags').strip()}): "
                           f"{tagged.group('rest').strip()}")
                continue
            bare = _ORPHAN_BARE.match(line)
            if bare:
                # Their previous bracket rather than `neutral`: this is the same
                # person carrying on, so continuity is the better guess, and
                # defaulting to neutral would flatten the voice on every repair.
                out.append(f"{speaker} ({last_tags or 'neutral, neutral'}): "
                           f"{bare.group('rest').strip()}")
                continue
        out.append(line)
    return "\n".join(out)


def clean_reply(text: str, names: set[str], system: str = "",
                attribute: bool = True) -> str:
    """Everything a raw reply needs before it becomes a message.

    Trims at the player's turn, drops a recitation of the prompt, drops
    bracket-only lines, gives a speaker back to dialogue that lost its name, and
    collapses the blank runs that removing a line leaves.

    Order matters: the prompt recitation goes before anything else (it is only
    recognisable at the front), and orphan attribution goes last, so it sees the
    lines that survive rather than ones about to be removed.

    `attribute=False` skips the repair, and exists for the prompt bench. Measuring
    format compliance *after* repairing it would score the safety net rather than
    the prompt, and hide exactly the differences a format-targeted round is
    looking for.
    """
    body = trim_reply(text, names)
    if system:
        body = strip_prompt_echo(body, system)
    if attribute:
        body = attribute_orphans(body)
    kept: list[str] = []
    for line in body.splitlines():
        if _BRACKET_LINE.match(line):
            continue
        if not line.strip() and (not kept or not kept[-1].strip()):
            continue                    # no leading or doubled blank lines
        kept.append(line.rstrip())
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept).strip()


# --------------------------------------------------------------------------
# the regurgitation guard
# --------------------------------------------------------------------------
#
# A separate failure from writing the player's lines, and a worse-looking one:
# instead of continuing the scene the model restates the reply it just gave, or
# the whole transcript, sometimes with invented `Human:` / `AI:` markers between
# the parts.
#
# It is a model-level artifact rather than a prompt one -- measured on Cydonia
# 24B v4.3, it reproduces near-deterministically for *particular* generated
# passages and not at all for others of the same length, shape and position, and
# it survives every change to the surrounding request (shorter system prompt, no
# directive, a different opening turn, an extra exchange in front). So it cannot
# be prompted away, and it is caught here instead.
#
# Deliberately compared on a normalised prefix rather than on the whole text: a
# regurgitation always *starts* by repeating, and by the end it has usually drifted
# into new material, so a whole-text similarity score would miss most of them.

_NORM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NORM.sub(" ", (text or "").lower()).strip()


#: Normalised characters of agreement that count as "this is the same passage".
#: Long enough that two replies opening on the same short line ("Alice: Well?")
#: are not called a repeat -- that happens legitimately.
ECHO_PREFIX = 60


def echoes(reply: str, previous: str) -> bool:
    """Is this reply just the previous one again?"""
    a, b = _norm(reply), _norm(previous)
    if len(a) < ECHO_PREFIX or len(b) < ECHO_PREFIX:
        return False
    return a[:ECHO_PREFIX] == b[:ECHO_PREFIX]


#: Appended to the directive on the retry after a regurgitation. Blunt on
#: purpose: it is read once, by a model that has just demonstrated it needs it.
RETRY_NUDGE = (
    "[Your last attempt repeated the previous reply word for word. Do not restate "
    "anything already written above. Start from the very next thing that happens, "
    "and write something shorter than you were about to.]")


def strip_echo(reply: str, previous: str) -> str:
    """Drop a repeat of `previous` from the front of `reply`, keeping the rest.

    The salvage path, and worth having rather than failing the turn outright: a
    regurgitation is usually a repeat *followed by* real new material, so there is
    a genuine reply in there behind the copy. Measured against a model that does
    this reliably, throwing the whole generation away loses a good beat perhaps
    two times in three.

    Line by line and on normalised text, because that is the shape the failure
    actually takes -- an exact replay, sometimes reflowed. A paraphrase is not
    caught here and should not be: a reply that covers the same ground in new
    words is a weak reply, not a duplicated one, and deleting half of it would be
    worse than leaving it.
    """
    old = [_norm(line) for line in (previous or "").splitlines()]
    old = [line for line in old if line]
    if not old:
        return reply.strip()

    lines = (reply or "").splitlines()
    at = 0
    matched = 0
    while at < len(lines) and matched < len(old):
        current = _norm(lines[at])
        if not current:                 # blank lines carry no evidence either way
            at += 1
            continue
        if current != old[matched]:
            break
        at += 1
        matched += 1

    # Only a substantially complete repeat counts. One shared opening line is a
    # character saying the same thing twice, which people do.
    if matched < len(old) and matched < 3:
        return reply.strip()
    return "\n".join(lines[at:]).strip()


# --------------------------------------------------------------------------
# persona
# --------------------------------------------------------------------------

def resolve_persona(project, characters: dict[str, Character]) -> Persona:
    """The reader, falling back to the project's player character.

    A project that already has an `is_player` character should need no setup at
    all, so an empty persona field borrows from them rather than rendering
    blank. Only the two fields that have an equivalent are borrowed: the
    player's booru-tagged appearance is written for an image pipeline, but
    `compose_appearance` is the same prose rendering the character cards use, so
    it reads correctly here.
    """
    stored = getattr(project, "roleplay", None)
    persona = (stored.persona if stored is not None else Persona()).model_copy()
    player = next((c for c in characters.values() if c.is_player), None)
    if not persona.name.strip():
        persona.name = player.display_name if player is not None else "You"
    if not persona.description.strip() and player is not None:
        persona.description = ctx_mod.compose_appearance(player)
    return persona


def persona_block(persona: Persona) -> str:
    bits = [f"The reader plays {persona.name}."]
    if persona.description.strip():
        bits.append(f"What the cast see: {persona.description.strip().rstrip('.')}.")
    if persona.notes.strip():
        bits.append(persona.notes.strip())
    return "\n".join(bits)


# --------------------------------------------------------------------------
# rendering a conversation
# --------------------------------------------------------------------------

def message_text(message: RPMessage) -> str:
    """The active take's text, or the message's own.

    A take is only ever chosen for a cast message; `text` stays in step with the
    active take so that anything reading rows straight out of the table -- the
    port, the summarizer -- does not have to know about swipes.
    """
    if message.role == "cast" and message.active_take:
        for take in message.takes:
            if take.id == message.active_take:
                return take.text
    return message.text


def transcript(messages: list[RPMessage], persona: Persona) -> str:
    """The conversation as flat text, for a summarizer or a port.

    Not what goes to the model on a turn -- that is a real message list (see
    `assemble_turn`). The player is written `You:` here because that is the house
    format the port and the style corpus both use.
    """
    out: list[str] = []
    for message in messages:
        body = message_text(message).strip()
        if not body:
            continue
        if message.role == "player":
            out.append(f"{persona.name}: {body}")
        else:
            out.append(body)
    return "\n\n".join(out)


def title_for(conv: RPConversation, messages: list[RPMessage]) -> str:
    """A name for an untitled conversation: its context line, else its opening."""
    if conv.title.strip():
        return conv.title.strip()
    source = conv.scene_context.strip()
    if not source:
        first = next((message_text(m) for m in messages if message_text(m).strip()), "")
        # Strip the performance bracket off a dialogue line so the title is
        # words rather than notation.
        source = re.sub(r"^[^:\n]{1,40}\([^)\n]*\):\s*", "", first.strip().splitlines()[0]
                        if first.strip() else "")
        source = source.strip('"').strip()
    if not source:
        return "Untitled scene"
    return source[:60].rstrip(" .,") + ("…" if len(source) > 60 else "")


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------

def earlier_scenes(project_id: str, conv: RPConversation) -> list[RPConversation]:
    """The unhidden scenes before this one, in list order.

    Deliberately derived from the list itself rather than from a comparison on
    `sort_key`: the list is what the author sees and re-orders, so "earlier"
    means "above it on that list" and nothing else. Comparing keys directly was
    also a bug waiting to happen -- two rows with equal keys made neither one
    earlier than the other, and the memory block came back empty for both.

    A conversation that has been hidden is not in the list this walks, so it
    cannot be an ancestor of anything.
    """
    listed = store.list_conversations(project_id, conv.character_id,
                                      include_hidden=False)
    at = next((i for i, c in enumerate(listed) if c.id == conv.id), None)
    if at is None:
        # This conversation is itself hidden, so it is not in the list. Fall back
        # to the key comparison; being hidden should not also blind it.
        return [c for c in listed if c.sort_key < conv.sort_key]
    return listed[:at]


def memory_block(project_id: str, conv: RPConversation) -> str:
    """Summaries of the earlier scenes with this character, oldest first.

    Scope is deliberate:

    * **Earlier than this one** on the chronological list -- which is the manual
      order, so re-ordering the list re-orders the memory with it.
    * **Not hidden.** That switch exists so a scene can be kept for reading but
      taken out of continuity, which is the cheap fix for a scene that went
      somewhere the author does not want remembered.
    * **Summarized.** An open conversation has no summary yet, and sending its
      raw transcript here would swamp the prompt. Its title goes in instead, so
      the model knows something happened without being told what -- better than
      a silent gap it will confidently write over.
    """
    earlier = earlier_scenes(project_id, conv)
    lines: list[str] = []
    for index, other in enumerate(earlier, 1):
        head = f"### Scene {index}: {title_for(other, [])}"
        if other.summary.strip():
            lines.append(f"{head}\n{other.summary.strip()}")
        else:
            lines.append(f"{head}\n(this scene has not been summarized -- "
                         f"treat it as having happened, with the details unknown)")
    return "\n\n".join(lines)


def previous_tail(project_id: str, conv: RPConversation, persona: Persona,
                  limit: int) -> str:
    """The end of the previous scene, verbatim, purely so speech style carries.

    In the system block under a heading saying it is finished -- never as turns.
    Sent as turns it reads as the scene still being live and the model continues
    it instead of starting the new one.
    """
    if limit <= 0:
        return ""
    earlier = earlier_scenes(project_id, conv)
    if not earlier:
        return ""
    messages = store.list_messages(earlier[-1].id)
    return transcript(messages[-limit:], persona)


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

#: Turns at the end of the transcript that trimming will never drop. Below this
#: the model loses what it is replying to, which is worse than any prompt being
#: over budget.
FLOOR_TURNS = 4


def _cast_of(conv: RPConversation, characters: dict[str, Character]) -> list[Character]:
    return [characters[cid] for cid in conv.everyone()
            if cid in characters and not characters[cid].is_player]


def cast_names(cast: list[Character]) -> str:
    names = [c.display_name.strip() for c in cast]
    if not names:
        return "the narrator"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def build_system(project_id: str, conv: RPConversation,
                 characters: dict[str, Character], project,
                 persona: Persona, cast: list[Character],
                 *, include_roster: bool = True, memory: str = "",
                 previous: str = "") -> str:
    """Render `rp.system`. Pure, so the budget loop can re-render cheaply."""
    flags = stat_defaults(project, characters.values())
    cards = "\n\n".join(
        ctx_mod.character_card(project_id, c, project, flags, conv.nsfw) for c in cast)
    cast_ids = {c.id for c in cast}
    roster = "\n".join(
        ctx_mod.roster_line(c) for cid, c in sorted(characters.items())
        if cid not in cast_ids and not c.is_player) if include_roster else ""

    examples_tpl = store.active_template(project_id, "rp.style_examples")
    examples = ctx_mod.render(examples_tpl.body if examples_tpl else "",
                              nsfw=conv.nsfw).strip()

    tpl = store.active_template(project_id, "rp.system")
    return ctx_mod.render(
        tpl.body if tpl else "",
        persona_name=persona.name,
        persona=persona_block(persona),
        cast_names=cast_names(cast),
        character_cards=cards,
        roster=roster,
        world=ctx_mod.world_block(project) if project is not None else "",
        style_notes=(project.world.style_notes if project is not None else ""),
        pov=(project.world.pov if project is not None else "second person"),
        tense=(project.world.tense if project is not None else "present"),
        nsfw=conv.nsfw,
        memory=memory,
        previous=previous,
        scene_context=conv.scene_context.strip(),
        examples=examples,
        # The performance vocabularies. A tag outside these lists is clamped to
        # `neutral` on the way to TTS and on the way into a ported node, so the
        # lists have to be in the prompt.
        expressions=", ".join(EXPRESSIONS),
        emotions=", ".join(EMOTIONS),
        poses=", ".join(POSES),
    ).strip()


def build_directive(project_id: str, persona: Persona, cast: list[Character],
                    *, silent: bool, opening: bool, steer: str) -> str:
    tpl = store.active_template(project_id, "rp.turn_directive")
    return ctx_mod.render(
        tpl.body if tpl else "",
        persona_name=persona.name,
        cast_names=cast_names(cast),
        reply_length=store.get_setting("rp_reply_length", "one to four beats"),
        silent=silent,
        opening=opening,
        steer=steer,
    ).strip()


def assemble_turn(project_id: str, conv: RPConversation, *,
                  history: Optional[list[RPMessage]] = None) -> dict[str, Any]:
    """Everything the model is sent for one reply.

    The turn being answered is taken from `history` rather than passed in: by the
    time a reply job runs, the player's message is already a row (the UI showed
    it the moment they pressed send). So the last message, if it is theirs, is
    lifted off the transcript and becomes the final user turn that carries the
    directive.

    That one rule makes three cases fall out for free:

    * They typed something -> their message is last -> it is the turn.
    * They sent nothing -> no row was written -> a cast message is last ->
      `silent`, and the directive tells the model to carry the moment alone.
    * A reply failed or was cancelled -> their message is still last and still
      unanswered -> it is simply answered now, rather than being treated as a
      silence.

    `history` is passed explicitly by a regeneration, which hands over the
    transcript with the reply being redone already removed.

    Returns the message list plus the pieces it was built from, so
    `/rp/conversations/{id}/preview` can show exactly what went to the model.
    """
    project = store.get_project(project_id)
    characters = {c.id: c for c in store.list_characters(project_id)}
    persona = resolve_persona(project, characters)
    cast = _cast_of(conv, characters)
    messages = list(history if history is not None else store.list_messages(conv.id))

    player_text = ""
    if messages and messages[-1].role == "player":
        player_text = message_text(messages.pop())

    parsed = split_player_text(player_text)
    # Empty means "carry on without me" -- an explicit move, not a mistake, so
    # the directive says so rather than the prompt being left to guess.
    silent = not parsed["spoken"]
    opening = not any(m.role == "cast" for m in messages)

    prev_limit = int(store.get_setting("rp_prev_scene_tail", 6) or 0)
    memory = memory_block(project_id, conv)
    previous = previous_tail(project_id, conv, persona, prev_limit)

    def system_for(*, roster: bool, mem: str, prev: str) -> str:
        return build_system(project_id, conv, characters, project, persona, cast,
                            include_roster=roster, memory=mem, previous=prev)

    directive = build_directive(project_id, persona, cast, silent=silent,
                                opening=opening, steer=parsed["steer"])

    # The turn the reply answers. A silent turn still needs a user message --
    # the list has to end on one -- so it carries the directive alone.
    last_user = "\n\n".join(x for x in (parsed["spoken"], directive) if x)

    history_budget = int(store.get_setting("rp_history_budget", 5000) or 5000)
    turns = _as_turns(messages, persona)
    turns, dropped = _trim_turns(turns, history_budget)

    system = system_for(roster=True, mem=memory, prev=previous)
    out = _messages(system, turns, last_user)

    # The overall ceiling, cut in the order that costs the scene least: the
    # one-line roster for people who are not here, then the previous scene's
    # style sample, then the oldest remembered scenes, then more of the
    # transcript. The cards and the last few turns are never touched.
    budget = int(store.get_setting("rp_context_budget", 0) or 0)
    roster_on, mem, prev = True, memory, previous
    mem_scenes = [block for block in memory.split("\n\n") if block.strip()]
    while budget and _cost(out) > budget:
        if roster_on:
            roster_on = False
        elif prev:
            prev = ""
        elif len(mem_scenes) > 1:
            mem_scenes = mem_scenes[1:]
            mem = "\n\n".join(mem_scenes)
        elif len(turns) > FLOOR_TURNS:
            turns = turns[2:]
            dropped += 2
        else:
            break
        system = system_for(roster=roster_on, mem=mem, prev=prev)
        out = _messages(system, turns, last_user)

    estimate = _cost(out)
    return {
        "messages": out,
        "system": system,
        "directive": directive,
        "cast": [c.id for c in cast],
        "cast_names": cast_names(cast),
        "persona_name": persona.name,
        "silent": silent,
        "opening": opening,
        "steer": parsed["steer"],
        "dropped_turns": dropped,
        "token_estimate": estimate,
        "over_budget": max(0, estimate - budget) if budget else 0,
    }


def _as_turns(messages: list[RPMessage], persona: Persona) -> list[dict[str, str]]:
    """The stored conversation as chat turns.

    Consecutive messages of the same role are merged. That is not the rare edge
    case it looks like: every silent turn -- the player pressing "carry on"
    without typing -- produces a second cast message with no player message
    between, so a scene with two silences in it has two such pairs. A chat
    template that requires alternation errors on the whole request rather than
    on the offending pair, so they cannot be passed through.

    A player turn is stripped of its bracketed directions the same way the
    current turn is. They were notes to the writer, said by nobody and heard by
    nobody; replayed verbatim in the transcript they read as something the
    player announced out loud, and the model starts answering them.
    """
    turns: list[dict[str, str]] = []
    for message in messages:
        body = message_text(message).strip()
        if message.role == "player":
            body = split_player_text(body)["spoken"]
        if not body:
            continue
        role = "user" if message.role == "player" else "assistant"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + body
            continue
        turns.append({"role": role, "content": body})
    return turns


def _trim_turns(turns: list[dict[str, str]], budget: int) -> tuple[list[dict[str, str]], int]:
    """The longest suffix of the transcript that fits, never under FLOOR_TURNS."""
    if not budget:
        return turns, 0
    kept: list[dict[str, str]] = []
    used = 0
    for turn in reversed(turns):
        cost = ctx_mod.est_tokens(turn["content"])
        if used + cost > budget and len(kept) >= FLOOR_TURNS:
            break
        used += cost
        kept.insert(0, turn)
    return kept, len(turns) - len(kept)


#: Stands in front of a transcript that opens on a reply, so the reply survives.
OPENING_TURN = "[The scene begins.]"


def _messages(system: str, turns: list[dict[str, str]],
              last_user: str) -> list[dict[str, str]]:
    """system, then the transcript, then the turn being answered.

    The list always ends on a `user` message, and after the system message it
    alternates.

    A transcript that opens on an assistant turn is the normal case, not an odd
    one: a scene started from a context line begins with the cast opening it,
    and there is no player message in front of that. Those turns used to be
    dropped to restore alternation -- which threw away the opening of the scene,
    the single most load-bearing thing in the whole transcript. The symptom was
    the next reply re-establishing the room the scene had already established,
    and the one after that restating the conversation from the top, because as
    far as the model could see it had begun in mid-air.

    So a synthetic opener goes in front instead. It costs five tokens and keeps
    the scene's first beat.
    """
    body = list(turns)
    out = [{"role": "system", "content": system}]
    if body and body[0]["role"] == "assistant":
        out.append({"role": "user", "content": OPENING_TURN})
    out += body
    if out[-1]["role"] == "user":
        # A silent turn after a player message with no reply: fold the directive
        # into that message rather than opening a second user turn.
        out[-1] = {"role": "user", "content": out[-1]["content"] + "\n\n" + last_user}
    else:
        out.append({"role": "user", "content": last_user})
    return out


def _cost(messages: list[dict[str, str]]) -> int:
    return sum(ctx_mod.est_tokens(m["content"]) for m in messages)


# --------------------------------------------------------------------------
# port to a canvas scene
# --------------------------------------------------------------------------
#
# The one bridge out of this module, and deliberately a copy rather than a
# link: the scene it builds is ordinary graph content from that point on, and
# editing it never writes back here.
#
# Almost all of this is code, not a model, which is the point of keeping cast
# lines in house performance notation all along. `Alice (smirking, amused,
# 0.6): "line"` is exactly what `generation.extract_performance` already parses,
# so a finished conversation is already 90% of a structured scene and the LLM
# cannot hallucinate the conversation away. What code has to do is the three
# things the chat format carries that the graph format does not: the player's
# `*actions*`, their `[steer]` notes, and their name.

# Third person singular back to the base form, for a player action typed as
# `*leans in*`. Narration in this house style is second person ("you lean in"),
# so the verb has to lose its `-s`. The rule is regular in this direction; only
# a handful of verbs are not, and they are listed.
_IRREGULAR = {"is": "are", "has": "have", "does": "do", "goes": "go",
              "was": "were", "says": "say"}
# Words that end in -s but are not a conjugated verb, so stripping the s would
# mangle them. A player action starting with one of these is left alone.
_NOT_A_VERB = {"yes", "this", "his", "hers", "its", "us", "thus", "across",
               "unless", "perhaps", "always"}


def _second_person(action: str) -> str:
    """`leans in` -> `You lean in.`  `I lean in` -> `You lean in.`"""
    body = action.strip().strip("*").strip()
    if not body:
        return ""
    words = body.split()
    head = words[0]
    lowered = head.lower()
    if lowered in {"i", "im", "i'm"}:
        # Already first person: swapping the pronoun is the whole job, because
        # "I lean" and "you lean" take the same verb form.
        rest = " ".join(words[1:])
        body = f"You {'are ' + rest if lowered != 'i' else rest}".strip()
    elif lowered in _IRREGULAR:
        body = " ".join(["You", _IRREGULAR[lowered]] + words[1:])
    elif lowered in _NOT_A_VERB or not lowered.endswith("s"):
        # Not something we can conjugate with any confidence -- a fragment like
        # `*a long pause*` is not a verb phrase at all. Leave the words as the
        # player wrote them; `rp.to_scene` tidies these if it is enabled.
        body = body[0].upper() + body[1:]
        return body if body[-1] in ".!?…" else body + "."
    elif lowered.endswith("ies") and len(lowered) > 4:
        body = " ".join(["You", head[:-3] + "y"] + words[1:])
    elif lowered.endswith(("sses", "shes", "ches", "xes", "zes", "oes")):
        body = " ".join(["You", head[:-2]] + words[1:])
    else:
        body = " ".join(["You", head[:-1]] + words[1:])
    body = body[0].upper() + body[1:]
    return body if body[-1] in ".!?…" else body + "."


def port_player(text: str) -> list[str]:
    """One typed player message as house-format lines, in the order typed.

    `*leans in* What did you say` is an action and then a line, not a line and
    then an action, so this walks the string rather than reusing
    `split_player_text` -- that one aggregates, which is right for a prompt and
    wrong for a transcript.

    `[steer]` notes are dropped outright. They were never story: nobody said
    them and nobody heard them, and a node made from one would be a stage
    direction sitting in the middle of the scene.
    """
    # Same hole-collapsing as `split_player_text`, for the same reason: the
    # bracket sat inside a sentence, and "Fine.   Really." reaches the graph as
    # a say node with the gap still in it.
    body = re.sub(r"[ 	]{2,}", " ", _STEER.sub(" ", text or ""))
    out: list[str] = []
    cursor = 0
    for match in _ACTION.finditer(body):
        said = body[cursor:match.start()].strip()
        if said:
            out.append(f'You: "{said}"')
        narrated = _second_person(match.group(1))
        if narrated:
            out.append(narrated)
        cursor = match.end()
    tail = body[cursor:].strip()
    if tail:
        out.append(f'You: "{tail}"')
    return out


def player_tag(text: str, persona: Persona) -> str:
    """The persona's name becomes `{player}`, which is what the graph reads.

    Ren'Py substitutes the tag at runtime, so a scene that hardcodes the name
    is a scene that calls the reader by somebody else's name the moment they
    set their own. Skipped for a name that is already a pronoun or too short to
    match safely -- "You" as a persona name would turn every second-person
    narration line into `{player}`.
    """
    name = (persona.name or "").strip()
    if len(name) < 3 or name.lower() in {"you", "player", "reader", "me", "mc"}:
        return text
    return re.sub(rf"\b{re.escape(name)}\b", "{player}", text, flags=re.IGNORECASE)


def port_prose(messages: list[RPMessage], persona: Persona) -> str:
    """A finished conversation as house-format prose, ready to be structured.

    Cast messages pass through almost untouched -- they are already written in
    the notation the structuring pass expects. Blank-separated, because the
    segmenter splits on lines and a paragraph break is how the corpus separates
    a run of dialogue from a paragraph of narration.
    """
    blocks: list[str] = []
    for message in messages:
        raw = message_text(message).strip()
        if not raw:
            continue
        if message.role == "player":
            lines = port_player(raw)
        else:
            lines = []
            for line in raw.splitlines():
                stripped = line.strip()
                # A reply that is nothing but a bracket is the model talking to
                # itself about the scene, not a beat in it.
                if not stripped or _BRACKET_LINE.match(stripped):
                    continue
                said = _SPEAKER_HEAD.match(stripped)
                if said and _is_player_name(said.group("name"), persona):
                    # The model is not supposed to be able to do this, and
                    # `turn_end` normally catches it. If one gets through it
                    # ports as the player's own line -- stripped of its
                    # performance bracket, because they have no sprite to wear
                    # it -- rather than as a character who does not exist.
                    stripped = f'You: {stripped[said.end():].strip()}'
                lines.append(stripped)
        if lines:
            blocks.append(player_tag("\n".join(lines), persona))
    return "\n\n".join(blocks)


def _is_player_name(name: str, persona: Persona) -> bool:
    cleaned = (name or "").strip().lower()
    return cleaned in {"you", (persona.name or "").strip().lower()} and bool(cleaned)


def ported_scenes(project_id: str) -> dict[str, str]:
    """`{conversation id: scene node id}` for everything already ported.

    Recorded on the scene node (`data["_rp_conv"]`) rather than on the
    conversation row, because a port's result is a fact about the graph: delete
    the scene and the conversation is un-ported, with nothing to clean up. It
    also keeps the two tables free of graph ids, which is the boundary this
    whole module is built on.
    """
    out: dict[str, str] = {}
    for node in store.list_nodes(project_id, include_orphaned=True):
        if node.type != NodeType.scene:
            continue
        conv_id = (node.data or {}).get("_rp_conv")
        if conv_id:
            out.setdefault(str(conv_id), node.id)
    return out
