"""Prompt context assembly.

Context is the path from root to the insertion node, not a chat log (doc S2).
We walk *up* the graph, which is what makes branch-aware regeneration correct:
regenerating on branch B never sees branch A's content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from jinja2 import ChainableUndefined, Environment, StrictUndefined

from .. import store
from ..clients import lmstudio
from ..defaults import TIME_TAGS, WEATHER_TAGS, cg_vocab_block
from ..graph import Graph, PathState, resolve_state, stat_defaults, upstream_hash
from ..models import (EMOTIONS, EXPRESSIONS, POSES, Character, Location, Node,
                      NodeType)

_env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
# The fallback for a template naming something no longer passed. ChainableUndefined
# renders as "" and survives attribute access, so `{{ personality.nsfw }}` in a
# card template written before that field existed goes blank instead of raising.
#
# It used to build this with `undefined=type("U", (), {...})()` -- an *instance*
# where jinja2 wants the class -- so the fallback itself raised TypeError every
# time and `render` returned the unrendered template. A prompt full of literal
# `{% if %}` tags is not a degraded generation, it is a nonsense one, and it
# only ever happened on the path that exists to stop that.
_loose_env = Environment(undefined=ChainableUndefined, keep_trailing_newline=True)


def render(template: str, **vars_: Any) -> str:
    """Jinja-style interpolation, forgiving of missing variables."""
    try:
        return _env.from_string(template).render(**vars_)
    except Exception:
        # A user-edited template with an unknown variable should not hard-fail
        # a generation; fall back to leaving the unknown bits blank.
        try:
            return _loose_env.from_string(template).render(**vars_)
        except Exception:
            return template


def est_tokens(text: str) -> int:
    return max(1, int(len(text) / 3.6))


@dataclass
class AssembledContext:
    system: str = ""
    user: str = ""
    state: PathState = field(default_factory=PathState)
    present: list[str] = field(default_factory=list)
    summary: str = ""
    recent: str = ""
    token_estimate: int = 0
    needs_summary_for: list[str] = field(default_factory=list)
    # Set when the prompt could not be trimmed under context_token_budget.
    # The floor is the cards for everyone on stage plus the last few beats:
    # below that there is nothing left to cut that would not break the writing.
    over_budget: int = 0
    dropped_cards: list[str] = field(default_factory=list)

    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": self.system},
                {"role": "user", "content": self.user}]


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------

def stat_line(project, character: Character, flags: dict[str, Any]) -> str:
    """The character's stats as they stand on this path, one short clause each.

    This is what makes a static character card dynamic without asking the
    author to write a paragraph per relationship stage: the band label plus the
    raw value is enough for the writer to calibrate warmth, and a band's `note`
    is opt-in flavour for the characters that earn it.
    """
    if character.is_player:
        return ""
    stats = getattr(project, "character_stats", None) or []
    parts: list[str] = []
    for stat in stats:
        if not stat.in_card or not stat.id:
            continue
        var = stat.var_for(character.id)
        if var not in flags:
            continue
        value = flags[var]
        band = stat.band_for(value)
        name = stat.name or stat.id
        if band and band.label:
            text = f"{name} {value} ({band.label})"
            if band.note:
                text += f" -- {band.note}"
        else:
            text = f"{name} {value}"
        parts.append(text)
    return "; ".join(parts)


def stat_schema_block(project, characters) -> str:
    """The stat ladder, written for the pass that decides what a choice changes.

    The structuring prompt used to see nothing but a list of variable names, so
    a model could emit `affection_diana += 1` without any way to know what that
    was worth: nothing said the range was 0-100, or that the bands it has to
    cross to change how she behaves sit twenty points apart. At +1 a warm
    choice that is twenty choices per band -- a relationship that never moves
    across a whole playthrough, which is the failure this exists to stop.

    The step sizes are derived from the band spacing rather than written down,
    so retuning the ladder in Config retunes the advice with it instead of
    leaving the prompt quietly recommending the old scale.
    """
    stats = getattr(project, "character_stats", None) or []
    people = [c for c in (characters or []) if not c.is_player]
    if not stats or not people:
        return ""

    lines: list[str] = []
    for stat in stats:
        if not stat.id:
            continue
        name = stat.name or stat.id
        low = stat.min if stat.min is not None else 0
        high = stat.max if stat.max is not None else 100
        head = f"- `{stat.id}_<character>` -- {name}, {low} to {high}, starts at {stat.default}."
        lines.append(head)

        bands = sorted(stat.bands, key=lambda b: b.min)
        if bands:
            ladder = ", ".join(f"{b.min} {b.label}" for b in bands if b.label)
            if ladder:
                lines.append(f"  Bands: {ladder}.")
            gaps = [b.min - a.min for a, b in zip(bands, bands[1:]) if b.min > a.min]
            gap = min(gaps) if gaps else 0
            if gap >= 4:
                lines.append(
                    f"  A band is {gap} points wide and crossing one visibly changes how "
                    f"they behave. So a warm beat is +{max(1, round(gap / 10))}, a real "
                    f"turning point +{max(2, round(gap / 3))}, and nothing routine moves "
                    f"it at all.")
        if stat.note:
            lines.append(f"  {stat.note}")

    if not lines:
        return ""
    who = ", ".join(c.id for c in people)
    return ("These track how a relationship stands and move across the whole story, "
            "unlike a one-off flag:\n"
            + "\n".join(lines)
            + f"\n  One per character: {who}.")


def character_card(project_id: str, character: Character, project=None,
                   flags: Optional[dict[str, Any]] = None,
                   nsfw: bool = False) -> str:
    """Personality plus an appearance line composed from the wizard dropdowns --
    no booru tags, no free-typed identity line the writer has to be given by
    hand (doc S3). `flags` are the resolved path flags, so the card reports the
    relationship as it is *now* rather than at character-creation time.

    `nsfw` is the scene's flag, not the character's. It gates the personality's
    NSFW subsection: for an ordinary scene the text is never rendered, so it is
    not in the prompt at all rather than in it under an instruction to ignore
    it -- which is a distinction a 24B does not reliably hold.
    """
    tpl = store.active_template(project_id, "character.card_format")
    p = character.personality.model_dump()
    p.setdefault("quirks", [])          # keep a legacy card template renderable
    return render(
        tpl.body if tpl else "",
        nsfw=nsfw,
        id=character.id,
        display_name=character.display_name,
        age=character.age,
        gender=character.gender,
        is_player=character.is_player,
        appearance=compose_appearance(character),
        short_appearance=compose_appearance(character),   # legacy alias
        personality=p,
        voice=character.voice.model_dump(),
        stats=stat_line(project, character, flags or {}) if project is not None else "",
    ).strip()


def roster_line(character: Character) -> str:
    """One line for a character who is not in this scene.

    A full card for everyone in the project would swamp the prompt, but with
    *nothing* the writer invents facts the moment someone off-stage is
    mentioned -- which is how a sister acquires a job she has never had.
    """
    bits = [character.display_name]
    p = character.personality
    if p.summary:
        bits.append(p.summary.strip().rstrip("."))
    elif p.relationship_to_player:
        bits.append(p.relationship_to_player.strip().rstrip("."))
    return "- " + ". ".join(bits) + "."


def _readable(f) -> str:
    """The words a prompt should use for one tagged field.

    Normally the dropdown label ("arms crossed") -- it is written for a reader,
    where the booru tag ("crossed arms") is written for a checkpoint. A field
    set to "custom..." has no preset label, so the typed booru tag is all there
    is and it stands in; that is better than the field vanishing from the
    prompt, which is what used to happen to every custom entry.
    """
    return f.label.strip() or f.tag.strip()


def compose_appearance(c: Character) -> str:
    """A human-readable appearance line for the writer model, built from the
    dropdown labels plus the free-text body description. The image pipeline uses
    the booru tags instead (services/images.identity_tags); the two never mix."""
    a = c.appearance
    singles = (a.hair_color, a.hair_style, a.eye_color, a.eye_quality, a.face,
               a.skin_tone, a.build, a.height, a.bust, a.butt, a.rest_expression, a.posture)
    bits = [t for t in (_readable(f) for f in singles) if t]
    bits += [t for t in (_readable(f) for f in
                         (list(a.distinguishing) + list(a.accessories))) if t]
    line = ", ".join(bits)
    return line


def world_block(project) -> str:
    w = project.world
    lines = []
    if w.setting:
        lines.append(f"Setting: {w.setting}")
    if w.era:
        lines.append(f"Era: {w.era}")
    if w.themes:
        lines.append(f"Themes: {w.themes}")
    lines.append(f"POV: {w.pov}. Tense: {w.tense}.")
    return "\n".join(lines)


def state_block(state: PathState, characters: dict[str, Character],
                locations: dict[str, Location],
                skip_flags: Optional[set[str]] = None) -> str:
    """Where we are and who is here.

    `skip_flags` omits the character-stat variables, which the character cards
    already report in a form the writer can use ("affection 34 (warming)").
    Listing them here as well spends context saying the same thing twice, in a
    less useful shape.
    """
    lines: list[str] = []
    loc = locations.get(state.location or "")
    where = loc.name if loc else (state.location or "unspecified")
    when = state.time_of_day or "unspecified"
    lines.append(f"Location: {where}. Time: {when}." +
                 (f" Weather: {state.weather}." if state.weather else ""))
    if state.present:
        who = []
        for cid in state.present:
            c = characters.get(cid)
            name = c.display_name if c else cid
            outfit_id = state.outfits.get(cid)
            outfit = ""
            if c and outfit_id:
                match = next((o for o in c.outfits if o.id == outfit_id), None)
                outfit = f" in {match.name.lower()}" if match else ""
            who.append(f"{name}{outfit}")
        lines.append("Present: " + ", ".join(who))
    shown = {k: v for k, v in state.flags.items() if k not in (skip_flags or set())}
    if shown:
        lines.append("Flags on this path: " +
                     ", ".join(f"{k}={v}" for k, v in sorted(shown.items())))
    return "\n".join(lines)


def taken_options(graph: Graph, chain: list[Node]) -> dict[str, str]:
    """For each choice on this path, which option the path actually went down.

    A choice rendered as its full option list tells the writer model nothing
    about which branch it is continuing, and it reliably picks the wrong one --
    the branch reads like a different branch. The edge from the choice to the
    next node in the chain carries the option id in its source_handle, so the
    path itself knows the answer.
    """
    taken: dict[str, str] = {}
    for node, nxt in zip(chain, chain[1:]):
        if node.type not in (NodeType.choice, NodeType.hub):
            continue
        for e in graph.out_edges(node.id):
            if e.target == nxt.id and e.source_handle:
                taken[node.id] = e.source_handle
                break
    return taken


def node_to_line(node: Node, characters: dict[str, Character],
                 taken: Optional[dict[str, str]] = None) -> str:
    d = node.data or {}
    if node.type == NodeType.say:
        c = characters.get(d.get("speaker") or "")
        name = c.display_name if c else (d.get("speaker") or "???")
        delivery = f" ({d['delivery']})" if d.get("delivery") else ""
        return f'{name}{delivery}: "{d.get("text", "")}"'
    if node.type == NodeType.narration:
        return d.get("text", "")
    if node.type in (NodeType.choice, NodeType.hub):
        options = d.get("options") or []
        chosen_id = (taken or {}).get(node.id)
        if chosen_id:
            chosen = next((o for o in options if o.get("id") == chosen_id), None)
            if chosen:
                prompt = d.get("prompt", "")
                lead = f"{prompt} " if prompt else ""
                return f'[{lead}the player chose: "{chosen.get("label", "")}"]'
        opts = ", ".join(o.get("label", "") for o in options)
        return f"[choice: {d.get('prompt', '')} -> {opts}]"
    if node.type == NodeType.stage:
        parts = []
        if d.get("location"):
            parts.append(f"move to {d['location']}")
        if d.get("show"):
            parts.append("enter " + ", ".join(d["show"]))
        if d.get("hide"):
            parts.append("exit " + ", ".join(d["hide"]))
        return f"[{'; '.join(parts)}]" if parts else ""
    if node.type == NodeType.scene:
        return f"=== {d.get('title', 'Scene')} ==="
    if node.type == NodeType.set:
        return ""
    return ""


def transcript(nodes: list[Node], characters: dict[str, Character],
               taken: Optional[dict[str, str]] = None) -> str:
    lines = [node_to_line(n, characters, taken) for n in nodes]
    return "\n".join(l for l in lines if l)


# --------------------------------------------------------------------------
# rolling summary with caching
# --------------------------------------------------------------------------

def _tail_within_budget(chain: list[Node], characters: dict[str, Character],
                        taken: dict[str, str], budget: int,
                        scene_start: int = 0) -> list[Node]:
    """The longest suffix of the path that fits, never crossing scene_start."""
    tail: list[Node] = []
    used = 0
    for i in range(len(chain) - 1, scene_start - 1, -1):
        cost = est_tokens(node_to_line(chain[i], characters, taken))
        if used + cost > budget and len(tail) > 2:
            break
        used += cost
        tail.insert(0, chain[i])
    return tail or chain[scene_start:]


def scene_start_index(chain: list[Node], scene: Optional[Node]) -> int:
    """Where the current scene begins in the path, or 0."""
    if scene is None:
        return 0
    for i, node in enumerate(chain):
        if node.id == scene.id:
            return i
    return 0


def _nearby_summary(project_id: str, graph: Graph, chain: list[Node],
                    characters: dict[str, Character], taken: dict[str, str],
                    split_at: int, floor: int, tail_cost: int,
                    ceiling: int) -> Optional[tuple[str, int]]:
    """A cached summary for a split a little before `split_at`, if one exists.

    Returns (summary, split index). Looks back only as far as the tail stays
    under `ceiling` tokens, and never past `floor` -- the verbatim tail must
    not reach back into the previous scene.
    """
    candidates: list[int] = []
    cost = tail_cost
    at = split_at
    while at > floor:
        at -= 1
        cost += est_tokens(node_to_line(chain[at], characters, taken))
        if cost > ceiling:
            break
        candidates.append(at)
    if not candidates:
        return None
    stored = store.summaries_for_nodes(project_id, [chain[i - 1].id for i in candidates])
    for i in candidates:
        by_hash = stored.get(chain[i - 1].id)
        if not by_hash:
            continue
        text = by_hash.get(upstream_hash(graph, chain[i - 1].id))
        if text:
            return text, i
    return None


async def rolling_summary(project_id: str, graph: Graph, node_id: str,
                          chain: list[Node], characters: dict[str, Character],
                          verbatim_budget: int,
                          scene_start: int = 0) -> tuple[str, list[Node]]:
    """Summarize the oldest chunk once, cache it on the upstream hash.

    Without the cache you re-summarize on every generation and the tool feels
    unusably slow (doc S2).

    The verbatim tail never reaches back past `scene_start`. It is handed to
    the writer under the heading "the scene so far", so letting a small budget
    pull in the tail of the *previous* scene told the model that finished
    material was the scene it is continuing.
    """
    taken = taken_options(graph, chain)
    # Walk backwards accumulating the verbatim tail.
    tail: list[Node] = []
    used = 0
    split_at = 0
    for i in range(len(chain) - 1, -1, -1):
        line = node_to_line(chain[i], characters, taken)
        cost = est_tokens(line)
        if used + cost > verbatim_budget and len(tail) > 2:
            split_at = i + 1
            break
        if line:
            used += cost
        tail.insert(0, chain[i])
    else:
        split_at = 0

    if scene_start > split_at:
        split_at = scene_start
        tail = chain[split_at:]

    older = chain[:split_at]
    if not older:
        return "", tail

    # Summarizing a handful of lines costs an LLM call to produce something
    # worse than the lines themselves.
    threshold = int(store.get_setting("summarize_threshold", 0) or 0)
    older_tokens = est_tokens(transcript(older, characters, taken))
    if threshold and older_tokens < threshold:
        return "", tail

    key_node = older[-1]
    key = upstream_hash(graph, key_node.id)
    cached = store.get_summary(project_id, key)
    if cached:
        return cached, tail

    # The split point moves with every line added, and the cache is keyed on
    # it -- so on a scene longer than the verbatim budget, every single turn
    # missed and paid for a fresh summary of everything before it -- a second
    # LLM call in front of a generation that did not need one. A summary cached
    # a few lines back is just as good, as long as the verbatim tail it implies
    # stays within twice the budget; the older text is re-summarized once the
    # tail outgrows that, rather than on every turn.
    reused = _nearby_summary(project_id, graph, chain, characters, taken,
                             split_at, max(scene_start, 1), used, verbatim_budget * 2)
    if reused is not None:
        text, at = reused
        return text, chain[at:]

    text = transcript(older, characters, taken)
    if not text.strip():
        return "", tail

    tpl = store.active_template(project_id, "system.summarize")
    prompt = render(tpl.body if tpl else "Summarize:\n{{ text }}", text=text)
    model = store.get_setting("lm_studio_struct_model", "") or store.get_setting(
        "lm_studio_prose_model", "")
    params = (tpl.params if tpl else {}) or {}
    summary = await lmstudio.chat(
        [{"role": "system", "content": "You write compact story-memory summaries."},
         {"role": "user", "content": prompt}],
        model=model, params=params,
    )
    summary = summary.strip()
    if summary:
        store.put_summary(project_id, key_node.id, key, summary)
    return summary, tail


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def _continuation_mode(chain: list[Node], scene: Optional[Node]) -> str:
    """'start' if nothing has been written in this scene yet, else 'continue'.

    A continuation must not be told to "write the scene" -- that is what makes a
    weak model restart from the top or re-narrate earlier beats (item 13).
    """
    tail = chain
    if scene is not None:
        for i, node in enumerate(chain):
            if node.id == scene.id:
                tail = chain[i + 1:]
                break
    for node in tail:
        if node.type in (NodeType.say, NodeType.narration):
            return "continue"
    return "start"


async def assemble(
    project_id: str,
    graph: Graph,
    insertion_node_id: str,
    brief: str = "",
    branch_hint: str = "",
    extra_instruction: str = "",
    continuation: str = "",
    summarize: bool = True,
) -> AssembledContext:
    project = store.get_project(project_id)
    characters = {c.id: c for c in store.list_characters(project_id)}
    locations = {l.id: l for l in store.list_locations(project_id)}

    chain = graph.path_to(insertion_node_id)
    state = resolve_state(chain, include_last=True)

    scene = graph.owning_scene(insertion_node_id)
    scene_data = (scene.data or {}) if scene else {}
    scene_brief = brief or scene_data.get("brief", "")
    mode = _continuation_mode(chain, scene)

    # Stat defaults sit *under* the path flags, so a character nobody has
    # interacted with yet still reports a real starting value instead of
    # dropping off the card entirely.
    flags: dict[str, Any] = stat_defaults(project, characters.values())
    stat_var_names = set(flags)
    flags.update(state.flags)

    # Cards for everyone present now, everyone who has spoken anywhere on this
    # path (so an established voice is not dropped once they leave frame -- item
    # 15), and the player character.
    present_ids = list(state.present)
    for node in chain:
        if node.type == NodeType.say:
            sp = (node.data or {}).get("speaker")
            if sp and sp not in present_ids:
                present_ids.append(sp)
    for cid, c in characters.items():
        if c.is_player and cid not in present_ids:
            present_ids.append(cid)
    # The scene's own character list is an override for someone who has not
    # appeared yet: the writer cannot bring in a character it was never told
    # about, and nothing upstream can infer an entrance that has not happened.
    for cid in scene_data.get("characters") or []:
        if cid in characters and cid not in present_ids:
            present_ids.append(cid)
    if not present_ids:
        present_ids = list(characters)
    # Everyone on stage now plus the player: these cards are never dropped,
    # however tight the budget gets.
    core_ids = [cid for cid in present_ids
                if cid in characters and (cid in state.present or characters[cid].is_player)]

    # The adult register is a property of the scene, not of the project: a
    # story with one explicit scene in it is not an explicit story, and gating
    # on a world-bible rating meant every scene got the same register.
    nsfw = bool(scene_data.get("nsfw"))

    def build_cards(ids: list[str]) -> str:
        return "\n\n".join(
            character_card(project_id, characters[cid], project, flags, nsfw)
            for cid in ids if cid in characters)

    cards = build_cards(present_ids)
    # One line each for everyone else in the project. Cheap, and it stops the
    # writer inventing facts about an off-stage character the moment someone
    # mentions them.
    roster = "\n".join(roster_line(c) for cid, c in sorted(characters.items())
                       if cid not in present_ids)

    # Kept only for templates written against something the shipped default no
    # longer says: a hand-edited one may still carry "Content rating:
    # {{ rating }}" or "Tone: {{ tone }}", and under StrictUndefined a name
    # that is no longer passed is not a blank, it is a generation that fails.
    # Templates never edited adopt the shipped default automatically
    # (store.active_template), which uses none of these.
    rating = "adult (18+)" if nsfw else "general audiences"
    explicit = nsfw                       # `{% if explicit %}`, before the rename
    tone = "explicit" if nsfw else ""     # when tone was an enum with this in it

    examples_tpl = store.active_template(project_id, "system.style_examples")
    examples = render(
        examples_tpl.body if examples_tpl else "",
        # Lets one examples template carry both registers, gated inline, rather
        # than needing a separate template per register.
        nsfw=nsfw,
        explicit=explicit,
        rating=rating,
    ).strip()

    verbatim_budget = int(store.get_setting("verbatim_token_budget", 2000))
    scene_start = scene_start_index(chain, scene)
    if summarize:
        summary, tail = await rolling_summary(
            project_id, graph, insertion_node_id, chain, characters, verbatim_budget,
            scene_start=scene_start,
        )
    else:
        # Same bound as the summarizing path, minus the LLM call -- a flat
        # node count let a preview blow past the window on long narration.
        summary, tail = "", _tail_within_budget(
            chain, characters, taken_options(graph, chain), verbatim_budget, scene_start)

    task = ("Write the opening of this scene now." if mode == "start"
            else "Continue the scene from the line after the last one above. "
                 "Write only the next stretch.")
    tpl = store.active_template(project_id, "system.prose_gen")

    beat_target = store.get_setting("prose_beat_target", "6-12 beats (roughly 200-350 words)")
    pc_policy = store.get_setting("prose_pc_policy", "")

    def render_system(cards_text: str, tail_nodes: list[Node]) -> tuple[str, str]:
        """(verbatim tail, rendered system prompt) for one set of inputs."""
        recent = transcript(tail_nodes, characters, taken_options(graph, chain))
        # Fence the verbatim tail so the model treats it as done, not as
        # material to rework (item 13).
        if recent and mode == "continue":
            block = (
                recent
                + "\n\n--- END OF THE SCENE SO FAR ---\n"
                "Everything above has already happened. Write only what comes next. "
                "Do not repeat, rephrase, or recap the line above; start from the "
                "next beat."
            )
        else:
            block = recent
        return recent, render(
            tpl.body if tpl else "",
            style_notes=project.world.style_notes if project else "",
            pov=project.world.pov if project else "second person",
            tense=project.world.tense if project else "present",
            rating=rating,
            nsfw=nsfw,
            explicit=explicit,
            world=world_block(project) if project else "",
            character_cards=cards_text,
            roster=roster,
            examples=examples,
            state=state_block(state, characters, locations, stat_var_names),
            summary=summary or ("(the scene has not started yet)" if mode == "start"
                                else "(no earlier events on this path)"),
            recent=block or "(nothing yet)",
            brief=scene_brief or "Continue the scene naturally.",
            # Scene-wide targets, rendered under their own heading that says so.
            # Passed individually rather than as one pre-baked block so a
            # user-edited template can place or drop them separately.
            scene_length=scene_data.get("length", ""),
            must_hit=scene_data.get("must_hit", ""),
            ends_with=scene_data.get("ends_with", ""),
            tone=tone,
            task=task,
            beat_target=beat_target,
            pc_policy=pc_policy,
            # The performance vocabularies. The writer tags each line as it
            # writes it, and a tag outside these lists is clamped to `neutral`
            # on the way into the node -- so the lists have to be in the prompt.
            expressions=", ".join(EXPRESSIONS),
            emotions=", ".join(EMOTIONS),
            poses=", ".join(POSES),
        )

    recent, system = render_system(cards, tail)

    # The brief is deliberately NOT restated here. It is scene-scoped, and the
    # system prompt already carries it under its own `## Scene brief` heading
    # next to the scene-wide targets -- repeating it verbatim in the user turn
    # bought nothing and made the preview look like a bug. What belongs in this
    # turn is only what is specific to *this* generation: the branch it follows,
    # the direction the author picked, a one-off note, and the task.
    #
    # The "do NOT restart it" clause that used to ride along with the restated
    # brief is not lost: the tail fence, `{{ task }}` and the closing line below
    # all say it, and three statements of it was already one more than a 24B
    # needs (compare the five-`never` PC policy in defaults.SUPERSEDED_SETTINGS).
    user_parts = []
    if branch_hint:
        user_parts.append(f"This branch follows the player choice: {branch_hint}")
    if continuation:
        # One of the directions the last generation offered, picked by the
        # author. Framed as a direction rather than an instruction: it says
        # where the scene goes, not how to write it.
        user_parts.append(f"Take the scene in this direction: {continuation}")
    if extra_instruction:
        user_parts.append(extra_instruction)
    user_parts.append(
        "Continue from immediately after the last line of the scene so far. Do "
        "not recap or restart." if mode == "continue"
        else "Write the opening of the scene now.")

    user = "\n\n".join(user_parts)

    # Enforce the overall ceiling. This setting existed and was shown in the
    # config UI but was never read, so the prompt had no upper bound at all:
    # cards for everyone who had ever spoken, plus the tail, plus the summary,
    # could quietly exceed the model's window and the front of it -- the
    # format rules and style bible -- would be the part that fell out.
    budget = int(store.get_setting("context_token_budget", 0) or 0)
    card_ids = list(present_ids)
    if budget:
        while est_tokens(system) + est_tokens(user) > budget:
            # Cards for characters who are no longer on stage go first: their
            # voice matters less than the scene the model is writing.
            droppable = [cid for cid in card_ids if cid not in core_ids]
            if droppable:
                card_ids.remove(droppable[0])
                recent, system = render_system(build_cards(card_ids), tail)
                continue
            # Then shorten the verbatim tail, keeping the most recent beats.
            if len(tail) > 3:
                tail = tail[len(tail) // 4 or 1:]
                recent, system = render_system(build_cards(card_ids), tail)
                continue
            break

    ctx = AssembledContext(
        system=system,
        user=user,
        state=state,
        present=present_ids,
        summary=summary,
        recent=recent,
        dropped_cards=[cid for cid in present_ids if cid not in card_ids],
    )
    ctx.token_estimate = est_tokens(ctx.system) + est_tokens(ctx.user)
    if budget and ctx.token_estimate > budget:
        ctx.over_budget = ctx.token_estimate - budget
    return ctx


def location_subject(location: Optional[Location]) -> str:
    """What to call this place in an image prompt.

    The tags if the author wrote any, otherwise the location's own name --
    "Hospital Room" is a perfectly good SDXL subject, and an empty string is
    not. Without the fallback a CG in a location nobody had tagged came out
    with time-of-day and weather but no setting at all: correctly lit, in
    nowhere. `assemble_background_prompt` always had this fallback inline,
    which is why the same location produced a hospital background and a
    location-less CG.

    Lowercased because the rest of the prompt is booru tags, and a stray
    trailing space in a hand-typed name ("Home ") would otherwise survive
    into the prompt.
    """
    if location is None:
        return ""
    return (location.tags or location.name or "").strip().lower()


def location_tags(location: Optional[Location], time_of_day: Optional[str],
                  weather: Optional[str]) -> tuple[str, str]:
    """(location tags, lighting tags) for the image prompt."""
    loc_tags = location_subject(location)
    lighting = TIME_TAGS.get(time_of_day or "", "")
    weather_tag = WEATHER_TAGS.get(weather or "", "")
    if weather_tag:
        lighting = f"{lighting}, {weather_tag}".strip(", ")
    return loc_tags, lighting


# --------------------------------------------------------------------------
# CG director context
# --------------------------------------------------------------------------

def cg_subject_line(character: Character) -> str:
    """One line per character in the shot.

    Deliberately *not* character_card: the director must never write identity
    tags (they come from the character record, unchanged, every time -- doc
    S4), so handing it hair/eyes/build only invites it to. What it does need
    is enough of the person to read the beat correctly -- whether she steps
    closer or steps back -- which is the personality summary and the
    relationship, and nothing else.
    """
    p = character.personality
    bits = [f"{character.display_name} (id: {character.id})"]
    if p.summary:
        bits.append(p.summary.strip().rstrip("."))
    if p.relationship_to_player and not character.is_player:
        bits.append(f"toward the player: {p.relationship_to_player.strip().rstrip('.')}")
    return "- " + ". ".join(bits) + "."


def cg_subjects(characters: dict[str, Character], state: PathState,
                speaker_id: str = "", limit: int = 2,
                override: Optional[list[str]] = None) -> list[Character]:
    """Who the CG should contain, in priority order.

    The player character is the camera in a first/second-person VN, so they are
    never in the shot -- rendering them is the single most common way a CG
    comes out wrong. The speaker of this line leads (they are what the text is
    about), then whoever else is on stage. Capped, because past two subjects
    tag-only prompting blends faces and you need regional conditioning
    (doc S4, tier 2).

    `override` is the node's own cast, set by hand in the inspector, and it
    wins outright: the automatic answer is a guess from who happens to be on
    stage, and a shot of one person in a crowded room is a normal thing to
    want and was previously impossible to ask for. It is taken literally --
    order kept, no cap, and the player included if the author picked them,
    because every filter here exists to fix a *guess* and none of them should
    override an instruction. The two-subject advice is surfaced in the
    inspector instead, where it can be read and ignored knowingly.

    One definition, because the job handler, the prompt preview endpoint and
    the director all have to agree on who is in the picture.
    """
    if override:
        return [characters[cid] for cid in override if cid in characters]

    subjects: list[Character] = []
    if speaker_id:
        speaker = characters.get(speaker_id)
        if speaker is not None and not speaker.is_player:
            subjects.append(speaker)
    for cid in state.present:
        c = characters.get(cid)
        if c is not None and not c.is_player and c not in subjects:
            subjects.append(c)
    return subjects[:limit]


def assemble_cg(project_id: str, graph: Graph, node_id: str,
                instruction: str = "", beats: int = 8) -> dict[str, Any]:
    """Everything the CG director needs to distill one node into image tags.

    Returns the rendered messages plus the pieces they were built from, so the
    inspector can show what the director was actually looking at.
    """
    project = store.get_project(project_id)
    characters = {c.id: c for c in store.list_characters(project_id)}
    locations = {l.id: l for l in store.list_locations(project_id)}

    node = store.get_node(node_id)
    if node is None:
        raise ValueError(f"node {node_id} no longer exists")

    chain = graph.path_to(node_id)
    state = resolve_state(chain, include_last=True)
    taken = taken_options(graph, chain)

    d = node.data or {}
    # Same override the render honours: directing a shot for two people and
    # then rendering one is worse than either on its own.
    subjects = cg_subjects(characters, state, d.get("speaker") or "",
                           override=d.get("cg_subjects") or None)

    # The beats before this one, and the one after. The next beat matters: a
    # CG almost always illustrates the moment a line *lands*, and the reaction
    # is what says whether that landed as a flinch or a laugh.
    scene = graph.owning_scene(node_id)
    start = scene_start_index(chain, scene)
    prior = [n for n in chain[start:-1] if node_to_line(n, characters, taken)][-beats:]
    nxt = graph.default_successor(node_id)
    next_node = store.get_node(nxt) if nxt else None

    # The cameras this scene has already used. The prompt asks the director not
    # to shoot every CG the same way, and without this it had no way to know
    # what "the same way" was -- so it returned `close-up, from above, pov`
    # for four consecutive beats, each a defensible answer in isolation.
    shots = [(n.data or {}).get("cg_camera", "").strip()
             for n in chain[start:-1]
             if (n.data or {}).get("cg") and (n.data or {}).get("cg_camera")]
    recent_shots = "; ".join(shots[-3:])

    player = next((c for c in characters.values() if c.is_player), None)
    # The scene's own register, exactly as the prose pass reads it. Without
    # this the director saw one vocabulary for every scene, so the adult tags
    # could only ever be all-or-nothing across a whole project.
    nsfw = bool(((scene.data or {}) if scene is not None else {}).get("nsfw"))

    # Tags retrieved from the real dictionary for *this* beat, and after the
    # register is known, because the retrieval screens on it. The suggested
    # palette is a few hundred hand-picked tags -- the right size to prime
    # good choices, and far too small to describe an arbitrary moment. It has
    # no "arms around neck" in it, no "holding folder", no "windowsill".
    # Retrieval covers that gap without making the prompt unreadable, because
    # it only ever offers what the text of this beat actually asked for.
    from . import tags as tag_svc
    beat_text = " ".join(filter(None, [
        (d.get("text") or ""), d.get("shot_hint", ""), instruction]))
    found = tag_svc.candidates_for(beat_text, limit=60, nsfw=nsfw)
    candidates = ", ".join(r["name"] for r in found)
    tpl = store.active_template(project_id, "system.cg_director")
    system = render(
        tpl.body if tpl else "",
        vocabulary=cg_vocab_block(nsfw=nsfw),
        world=world_block(project) if project else "",
        state=state_block(state, characters, locations),
        cards="\n".join(cg_subject_line(c) for c in subjects) or
              "- nobody: this is a shot of the place itself, with no characters in it.",
        subjects=", ".join(c.display_name for c in subjects) or "(no characters -- the room only)",
        player_name=player.display_name if player else "",
        recent=transcript(prior, characters, taken) or "(this is the first beat)",
        moment=node_to_line(node, characters, taken) or "(this node has no text yet)",
        next_beat=(node_to_line(next_node, characters, taken) if next_node else ""),
        shot_hint=d.get("shot_hint", ""),
        recent_shots=recent_shots,
        candidates=candidates,
        instruction=instruction,
    )
    return {
        "system": system,
        "user": "Return the JSON for this shot now.",
        "subjects": [c.id for c in subjects],
        "state": state,
        "nsfw": nsfw,
        # The individual camera tags of the last few CGs, so the director pass
        # can hold the shot to the variety the prompt asks for instead of
        # hoping. `recent_shots` above is the same data rendered for reading.
        "recent_camera": [t.strip() for shot in shots[-3:]
                          for t in shot.split(",") if t.strip()],
        "token_estimate": est_tokens(system),
    }
