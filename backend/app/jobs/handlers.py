"""Job handlers -- every long-running operation in the tool.

LLM, image, TTS and LoRA training all contend for one GPU, so these run behind
the queue's GPU lock rather than being called inline from a request.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .. import db, store
from ..clients import comfyui, lmstudio
from ..graph import Graph, resolve_state, slugify, stat_defaults
from ..models import (POSES, AssetStatus, AuditionTake, GenMeta, Location, Node,
                      NodeType, RPMessage, RPTake, SheetPoolItem)
from ..services import (
    assets,
    batch as batch_svc,
    build,
    context as ctx_mod,
    generation,
    images,
    roleplay,
    staleness,
    tts,
)
from ..services.schemas import RP_SEED_SCHEMA
from .queue import JobContext, handler

#: Ceiling on candidates per CG render. Not a VRAM limit -- a 24 GB card holds
#: eight 1344x768 SDXL latents comfortably -- but a limit on how long one job
#: sits on the GPU lock, and on how many images a person will actually look at
#: before picking one.
MAX_CG_BATCH = 8

#: Takes one voice re-take may roll. Unlike a CG batch these run one after
#: another (a TTS workflow makes one clip), so the cap is on GPU-lock time.
MAX_VOICE_TAKES = 6


def _graph(project_id: str) -> Graph:
    return Graph.build(
        store.list_nodes(project_id, include_orphaned=False),
        store.list_edges(project_id),
    )


def _require_node(node_id: str) -> Node:
    node = store.get_node(node_id)
    if node is None:
        raise ValueError(f"node {node_id} no longer exists")
    return node


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------

@handler("llm.generate_scene")
async def generate_scene(ctx: JobContext) -> dict[str, Any]:
    """The generation pipeline, end to end, materialized into the graph.

    Prose (warm, unconstrained, and it tags each line's performance as it
    writes it) -> structure (cold, segmentation only) -> apply + reconcile
    (code, no LLM: put each written tag on its node, stage anyone about to
    speak who is not on stage, and drop staging that changes nothing).
    """
    anchor_id = ctx.payload["anchor_id"]
    handle = ctx.payload.get("handle", "out")
    brief = ctx.payload.get("brief", "")
    branch_hint = ctx.payload.get("branch_hint", "")
    instruction = ctx.payload.get("instruction", "")
    continuation = ctx.payload.get("continuation", "")
    materialize = ctx.payload.get("materialize", True)

    graph = _graph(ctx.project_id)
    anchor = _require_node(anchor_id)
    characters = store.list_characters(ctx.project_id)
    locations = store.list_locations(ctx.project_id)
    project = store.get_project(ctx.project_id)
    # Declared variables plus every character stat, because a choice option
    # nudging `affection_diana` is the main thing stats exist for -- and the
    # schema only lets the model write names on this list.
    var_names = [v.name for v in (getattr(project, "variables", None) or []) if v.name]
    for name in sorted(stat_defaults(project, characters)):
        if name not in var_names:
            var_names.append(name)

    await ctx.progress(0.05, "assembling path context")
    assembled = await ctx_mod.assemble(
        ctx.project_id, graph, anchor_id, brief=brief, branch_hint=branch_hint,
        extra_instruction=instruction, continuation=continuation,
    )

    over = (f" -- {assembled.over_budget} tok over budget, "
            f"{len(assembled.present)} characters on stage" if assembled.over_budget else "")
    await ctx.progress(0.15, f"pass 1: prose (~{assembled.token_estimate} tok context){over}")

    prose, prose_meta = await generation.prose_pass(ctx.project_id, assembled)
    if not prose.strip():
        raise RuntimeError("the prose pass returned nothing -- is a model loaded in LM Studio?")
    # Author-facing "where this could go" notes, peeled off before anything
    # else sees the passage -- they are not story content and must never reach
    # the structuring pass.
    prose, continuations = generation.split_continuations(prose)
    # How each line is played is written on the line by the writer. It comes
    # off here, so the segmenter only ever sees `Name: "line"` -- and so a tag
    # can never drift onto the line next to the one it was written for.
    prose, performance = generation.extract_performance(prose)

    ctx.check_cancelled()
    await ctx.progress(0.65, "pass 2: structuring")
    parsed, struct_meta = await generation.structure_pass(
        ctx.project_id, prose,
        [c.id for c in characters],
        [l.id for l in locations],
        # What is already true when this passage starts, so the pass stages
        # only actual changes instead of re-declaring the room every time.
        state_block=ctx_mod.state_block(
            assembled.state, {c.id: c for c in characters}, {l.id: l for l in locations}
        ),
        outfits_by_character={c.id: [o.id for o in c.outfits] for c in characters},
        # Flag writes are restricted to declared variables: an invented name
        # compiles to a `default` nothing ever reads.
        variables=var_names,
        # And the stats among them come with their scale, or a `+=` is picked
        # blind -- +1 on a 0-100 ladder is twenty warm choices per band.
        stat_schema=ctx_mod.stat_schema_block(project, characters),
    )

    ctx.check_cancelled()
    char_ids = [c.id for c in characters]
    parsed = generation.apply_performance(parsed, performance)
    # Deterministic: whether a speaker is on stage, and whether a stage node
    # changes anything at all, are facts about the running present-set rather
    # than judgment calls.
    parsed = generation.reconcile_staging(
        parsed, list(assembled.state.present), char_ids,
        {c.id for c in characters if c.is_player},
        location=assembled.state.location,
        time_of_day=assembled.state.time_of_day,
        weather=assembled.state.weather,
    )

    gen_id = ctx.job_id
    result: dict[str, Any] = {
        "gen_id": gen_id,
        "prose": prose,
        "parsed": parsed,
        "scene_title": parsed.get("scene_title", ""),
        "node_count": len(parsed.get("nodes") or []),
        "prose_model": prose_meta.model,
        "struct_model": struct_meta.model,
        "context_tokens": assembled.token_estimate,
        "context_over_budget": assembled.over_budget,
        "context_dropped_cards": assembled.dropped_cards,
        "continuations": continuations,
        # Enough to reverse this generation for undo/redo (item 10).
        "anchor_id": anchor_id,
        "handle": handle,
    }

    if not materialize:
        return result

    await ctx.progress(0.85, "writing nodes")
    new_nodes, new_edges = generation.build_nodes(
        parsed, gen_id, char_ids, anchor, handle, var_names
    )
    for node in new_nodes:
        node.text_status = AssetStatus.ready
    inserted = generation.insert_chain(ctx.project_id, anchor_id, handle, new_nodes, new_edges)

    for node in new_nodes:
        stored = store.get_node(node.id)
        if stored is None:
            continue
        staleness.evaluate(stored)
        # Record this take so a later regeneration never destroys it.
        generation.add_variant(ctx.project_id, stored, "text", dict(stored.data), prose_meta)

    # Park the continuation suggestions on the last node of the chain, which is
    # where "carry on from here" belongs. Written after the variant loop on
    # purpose: they are authoring scaffolding, not part of the take, and a
    # variant that carried them would restore stale suggestions on a flip-back.
    #
    # A chain ending in a choice gets none -- the choice *is* what happens next,
    # and offering a second set of directions beside it just muddies which one
    # the player sees.
    if continuations and new_nodes and new_nodes[-1].type != NodeType.choice:
        tail = store.get_node(new_nodes[-1].id)
        if tail is not None:
            tail.data = {**(tail.data or {}), "_continuations": continuations}
            store.update_node(ctx.project_id, tail)

    result.update(inserted)
    await ctx.progress(1.0, f"{len(new_nodes)} nodes")
    return result


# --------------------------------------------------------------------------
# roleplay (services/roleplay.py)
# --------------------------------------------------------------------------

@handler("rp.reply")
async def rp_reply(ctx: JobContext) -> dict[str, Any]:
    """One turn of a live conversation.

    One LLM call and one row written -- no structuring pass, no nodes, no
    staleness. The whole shape of the request (a real multi-turn message list
    ending on the player's turn) is what keeps the reply to the cast's side of
    the scene; `roleplay.clean_reply` is a net under that, not the mechanism.

    `message_id` in the payload makes this a regeneration: the transcript is
    assembled as it stood before that reply, and the new text lands as another
    take on the same message rather than as a new one.
    """
    conv = store.get_conversation(ctx.payload["conv_id"])
    if conv is None:
        raise ValueError("that conversation is gone")
    target_id = ctx.payload.get("message_id") or ""

    messages = store.list_messages(conv.id)
    target = next((m for m in messages if m.id == target_id), None) if target_id else None
    if target_id and target is None:
        raise ValueError("that reply is gone -- nothing to regenerate")
    history = [m for m in messages if target is None or m.seq < target.seq]

    await ctx.progress(0.05, "assembling the scene")
    assembled = roleplay.assemble_turn(ctx.project_id, conv, history=history)

    characters = store.list_characters(ctx.project_id)
    project = store.get_project(ctx.project_id)
    persona = roleplay.resolve_persona(project, {c.id: c for c in characters})
    names = roleplay.player_names(persona, characters)

    over = f" -- {assembled['over_budget']} tok over budget" if assembled["over_budget"] else ""
    await ctx.progress(0.15, f"writing (~{assembled['token_estimate']} tok context){over}")

    tpl = store.active_template(ctx.project_id, "rp.system")
    params = dict((tpl.params if tpl else {}) or {})
    model = store.get_setting("lm_studio_prose_model", "")

    loop = asyncio.get_running_loop()
    last_sent = 0.0

    async def on_text(text: str) -> bool:
        nonlocal last_sent
        ctx.check_cancelled()
        cut = roleplay.turn_end(text, names)
        shown = text if cut is None else text[:cut]
        # Throttled: an event per token floods every open tab's queue for no
        # visible difference.
        if cut is not None or loop.time() - last_sent >= 0.08:
            last_sent = loop.time()
            await ctx.stream(shown)
        # True ends the request, which closes the connection, which is what
        # actually stops LM Studio sampling.
        return cut is not None

    # What the last reply was, so a regurgitation of it can be spotted. Taken
    # from the history this turn was assembled against, not from the table, so a
    # retake compares against the reply *before* the one being redone.
    last_reply = next((roleplay.message_text(m) for m in reversed(history)
                       if m.role == "cast"), "")

    async def write(extra: str = "") -> str:
        messages = assembled["messages"]
        if extra:
            messages = [*messages[:-1],
                        {"role": "user",
                         "content": messages[-1]["content"] + "\n\n" + extra}]
        raw = await lmstudio.chat_stream(messages, model=model, params=params,
                                        on_text=on_text)
        return roleplay.clean_reply(raw, names, assembled["system"])

    reply = await write()
    # One retry, with the nudge. See services.roleplay.echoes: this is a
    # model-level artifact that no change to the prompt prevented, so it is
    # caught after the fact rather than argued out of.
    if last_reply and roleplay.echoes(reply, last_reply):
        await ctx.progress(0.6, "that reply repeated the last one -- writing again")
        reply = await write(roleplay.RETRY_NUDGE)

    await ctx.stream(reply, done=True)
    if not reply.strip():
        raise RuntimeError(
            f"the reply went straight to {persona.name}'s line and left the cast "
            "nothing to say -- try again, or add a direction in [brackets]")
    salvaged = False
    if last_reply and roleplay.echoes(reply, last_reply):
        # Both attempts copied. Keep whatever real material sits behind the copy
        # rather than losing the turn: some model builds do this on nearly every
        # turn (measured on Cydonia 24B v4.3), and failing them all would make the
        # mode unusable on a model that is otherwise writing well.
        trimmed = roleplay.strip_echo(reply, last_reply)
        if len(trimmed) < 40:
            raise RuntimeError(
                "the writer repeated its previous reply twice over and wrote "
                "nothing new behind it. That is a fault in the model build rather "
                "than in the prompt -- a different Cydonia release or quantisation "
                "usually fixes it. Editing the last reply also changes what it is "
                "copying.")
        reply, salvaged = trimmed, True
        await ctx.stream(reply, done=True)

    take = RPTake(id=store.new_id("k_"), text=reply, model=model or "(loaded model)",
                  params=params, created_at=store.now())
    if target is not None:
        # A swipe. The take that was showing stays in the list, so arrowing back
        # to it always works.
        target.takes = list(target.takes) + [take]
        target.active_take = take.id
        target.text = reply
        target.voice = []          # the old clips voiced different words
        store.save_message(target)
        message = target
    else:
        message = RPMessage(id=store.new_id("m_"), conv_id=conv.id,
                            seq=store.next_seq(conv.id), role="cast", text=reply,
                            takes=[take], active_take=take.id, created_at=store.now())
        store.insert_message(message)

    store.save_conversation(conv)          # bumps updated_at, so the list re-sorts
    await ctx.progress(1.0, "salvaged a repeated reply" if salvaged else "replied")
    return {"conv_id": conv.id, "message": message.model_dump(),
            "regenerated": bool(target), "salvaged": salvaged,
            "context_tokens": assembled["token_estimate"],
            "dropped_turns": assembled["dropped_turns"]}


@handler("rp.summarize")
async def rp_summarize(ctx: JobContext) -> dict[str, Any]:
    """Distill a finished conversation into memory for the scenes after it.

    Written onto the conversation rather than into the `summaries` table: that
    cache is keyed on a graph upstream hash, which a conversation has no
    equivalent of, and this summary is editable by hand -- it is content, not a
    cache entry.
    """
    conv = store.get_conversation(ctx.payload["conv_id"])
    if conv is None:
        raise ValueError("that conversation is gone")
    if conv.summary_edited and not ctx.payload.get("force"):
        return {"skipped": "this summary has been edited by hand"}

    characters = {c.id: c for c in store.list_characters(ctx.project_id)}
    project = store.get_project(ctx.project_id)
    persona = roleplay.resolve_persona(project, characters)
    messages = store.list_messages(conv.id)
    if not messages:
        raise ValueError("there is nothing in this conversation to summarize")

    await ctx.progress(0.2, "reading the scene back")
    text = roleplay.transcript(messages, persona)
    # Trimmed from the front: the end of a scene is what the next one follows
    # from, and PLOT can be reconstructed from a partial transcript far better
    # than BETWEEN THEM can.
    budget = int(store.get_setting("rp_context_budget", 12000) or 12000)
    while ctx_mod.est_tokens(text) > budget and "\n\n" in text:
        text = text.split("\n\n", 1)[1]

    cast = [characters[cid].display_name for cid in conv.everyone()
            if cid in characters and not characters[cid].is_player]
    tpl = store.active_template(ctx.project_id, "rp.summarize")
    prompt = ctx_mod.render(
        tpl.body if tpl else "Summarize:\n{{ transcript }}",
        transcript=text, scene_context=conv.scene_context.strip(),
        persona_name=persona.name,
        cast_names=roleplay.cast_names([characters[cid] for cid in conv.everyone()
                                        if cid in characters
                                        and not characters[cid].is_player]) or "the cast",
    )
    model = store.get_setting("lm_studio_struct_model", "") or store.get_setting(
        "lm_studio_prose_model", "")
    await ctx.progress(0.4, "distilling")
    summary = (await lmstudio.chat(
        [{"role": "system", "content": "You write compact story-memory summaries."},
         {"role": "user", "content": prompt}],
        model=model, params=dict((tpl.params if tpl else {}) or {}),
    )).strip()
    if not summary:
        raise RuntimeError("the summarizer returned nothing")

    conv = store.get_conversation(conv.id) or conv
    conv.summary = summary
    conv.state = "ended"
    if not conv.title.strip():
        conv.title = roleplay.title_for(conv, messages)
    store.save_conversation(conv)
    await ctx.progress(1.0, "summarized")
    return {"conv_id": conv.id, "summary": summary, "cast": cast}


@handler("rp.seed")
async def rp_seed(ctx: JobContext) -> dict[str, Any]:
    """Propose openings for the next scene, from what has happened so far."""
    character_id = ctx.payload["character_id"]
    character = store.get_character(ctx.project_id, character_id)
    if character is None:
        raise ValueError("no such character")
    count = max(1, min(int(ctx.payload.get("count") or 3), 5))

    characters = {c.id: c for c in store.list_characters(ctx.project_id)}
    project = store.get_project(ctx.project_id)
    persona = roleplay.resolve_persona(project, characters)

    # Every summarized, unhidden scene with this character -- the same memory a
    # turn would see, so a proposal cannot contradict what the cast remember.
    blocks = [f"- {roleplay.title_for(c, [])}: {c.summary.strip()}"
              for c in store.list_conversations(ctx.project_id, character_id,
                                                include_hidden=False)
              if c.summary.strip()]

    tpl = store.active_template(ctx.project_id, "rp.scene_seed")
    prompt = ctx_mod.render(
        tpl.body if tpl else "", persona_name=persona.name,
        character_name=character.display_name, memory="\n".join(blocks), count=count,
    )
    model = store.get_setting("lm_studio_struct_model", "") or store.get_setting(
        "lm_studio_prose_model", "")
    await ctx.progress(0.3, "thinking of somewhere to start")
    raw = await lmstudio.chat(
        [{"role": "system", "content": "You propose scene openings. You return JSON."},
         {"role": "user", "content": prompt}],
        model=model, params=dict((tpl.params if tpl else {}) or {}),
        json_schema=RP_SEED_SCHEMA, schema_name="rp_seed",
    )
    try:
        ideas = [str(x).strip() for x in (json.loads(raw).get("ideas") or []) if str(x).strip()]
    except ValueError as exc:
        raise RuntimeError(f"the seed pass did not return valid JSON: {raw[:300]}") from exc
    await ctx.progress(1.0, f"{len(ideas)} ideas")
    return {"character_id": character_id, "ideas": ideas[:count]}


@handler("rp.port")
async def rp_port(ctx: JobContext) -> dict[str, Any]:
    """Copy a finished conversation into the graph as a new scene.

    One way, on purpose. The scene this writes is ordinary graph content from
    here on: editing it never writes back to the conversation, and re-porting
    builds a second scene rather than trying to merge into the first.

    Mostly code. Cast lines were written in house performance notation all
    along, so `extract_performance` parses them exactly as it parses a prose
    generation, and the only LLM call on the default path is the structuring
    pass -- the same cold segmenter the canvas uses. `rp.to_scene` is an
    optional tidy-up in front of it (`polish`), for inferring poses and
    splitting run-on narration; it is off by default because every LLM pass
    over a finished conversation is a chance to lose a line of it.
    """
    conv = store.get_conversation(ctx.payload["conv_id"])
    if conv is None:
        raise ValueError("that conversation is gone")
    messages = store.list_messages(conv.id)
    if not messages:
        raise ValueError("there is nothing in this conversation to port")

    characters = store.list_characters(ctx.project_id)
    by_id = {c.id: c for c in characters}
    locations = store.list_locations(ctx.project_id)
    project = store.get_project(ctx.project_id)
    persona = roleplay.resolve_persona(project, by_id)

    await ctx.progress(0.1, "rewriting the transcript")
    prose = roleplay.port_prose(messages, persona)
    if not prose.strip():
        raise ValueError("this conversation has no lines to port")

    if ctx.payload.get("polish"):
        tpl = store.active_template(ctx.project_id, "rp.to_scene")
        model = store.get_setting("lm_studio_struct_model", "") or store.get_setting(
            "lm_studio_prose_model", "")
        await ctx.progress(0.25, "pass 1: tidying")
        polished = (await lmstudio.chat(
            [{"role": "system", "content": "You prepare a transcript for segmentation. "
                                           "You never rewrite the words."},
             {"role": "user", "content": ctx_mod.render(
                 tpl.body if tpl else "{{ transcript }}",
                 transcript=prose, poses=", ".join(POSES))}],
            model=model, params=dict((tpl.params if tpl else {}) or {}),
        )).strip()
        # A polish pass that came back empty, or that dropped a third of the
        # conversation, has failed rather than tidied. The un-polished prose is
        # already correct, so the port carries on with it instead of stopping.
        if len(polished) > len(prose) * 0.6:
            prose = polished

    ctx.check_cancelled()
    prose, performance = generation.extract_performance(prose)

    var_names = [v.name for v in (getattr(project, "variables", None) or []) if v.name]
    for name in sorted(stat_defaults(project, characters)):
        if name not in var_names:
            var_names.append(name)

    await ctx.progress(0.45, "pass 2: structuring")
    parsed, struct_meta = await generation.structure_pass(
        ctx.project_id, prose,
        [c.id for c in characters],
        [l.id for l in locations],
        # Deliberately no state block. A ported scene starts from nothing on
        # stage, which is what makes `reconcile_staging` walk the cast on
        # before their first line -- in a conversation nobody was ever staged.
        outfits_by_character={c.id: [o.id for o in c.outfits] for c in characters},
        variables=var_names,
        stat_schema=ctx_mod.stat_schema_block(project, characters),
    )

    ctx.check_cancelled()
    char_ids = [c.id for c in characters]
    parsed = generation.apply_performance(parsed, performance)
    parsed = generation.reconcile_staging(
        parsed, [], char_ids, {c.id for c in characters if c.is_player},
        location=conv.location_id, time_of_day=conv.time_of_day,
    )

    await ctx.progress(0.8, "writing nodes")
    title = parsed.get("scene_title") or roleplay.title_for(conv, messages)
    existing = store.list_nodes(ctx.project_id, include_orphaned=True)
    scene = Node(
        id=store.new_id("n_"), type=NodeType.scene,
        data={
            "title": title, "background": conv.location_id,
            "time_of_day": conv.time_of_day, "nsfw": conv.nsfw,
            "brief": conv.scene_context,
            # What this scene was made from. The only record of the port --
            # see roleplay.ported_scenes for why it lives here and not on the
            # conversation row.
            "_rp_conv": conv.id,
        },
        # Clear of everything already on the canvas: a ported scene has no
        # anchor, so there is no column for it to grow out of.
        x=max((n.x for n in existing), default=0.0) + 520,
        y=min((n.y for n in existing), default=0.0),
        slug=slugify(title or "scene", "scene"),
    )
    store.insert_node(ctx.project_id, scene)

    gen_id = ctx.job_id
    new_nodes, new_edges = generation.build_nodes(parsed, gen_id, char_ids, scene, "out",
                                                  var_names)
    for node in new_nodes:
        node.text_status = AssetStatus.ready
    inserted = generation.insert_chain(ctx.project_id, scene.id, "out", new_nodes, new_edges)

    for node in new_nodes:
        stored = store.get_node(node.id)
        if stored is not None:
            staleness.evaluate(stored)

    await ctx.progress(1.0, f"{len(new_nodes)} nodes")
    return {"conv_id": conv.id, "scene_id": scene.id, "scene_title": title,
            "node_count": len(new_nodes), "struct_model": struct_meta.model,
            "prose": prose, **inserted}


@handler("llm.regenerate_from_node")
async def regenerate_from_node(ctx: JobContext) -> dict[str, Any]:
    """Keep everything upstream, park the downstream subtree, re-run."""
    node_id = ctx.payload["node_id"]
    node = _require_node(node_id)

    await ctx.progress(0.05, "parking the downstream subtree")
    orphaned = generation.orphan_subtree(ctx.project_id, node_id, include_self=True)

    incoming = [e for e in store.list_edges(ctx.project_id) if e.target == node_id]
    if not incoming:
        generation.restore_orphans(ctx.project_id, orphaned)
        raise RuntimeError("cannot regenerate from a node with no predecessor")
    anchor_id, handle = incoming[0].source, incoming[0].source_handle

    ctx.payload.update({"anchor_id": anchor_id, "handle": handle})
    try:
        result = await generate_scene(ctx)
    except Exception:
        generation.restore_orphans(ctx.project_id, orphaned)
        raise
    result["orphaned"] = orphaned
    result["regen_head"] = node_id
    return result


@handler("llm.summarize")
async def summarize_path(ctx: JobContext) -> dict[str, Any]:
    graph = _graph(ctx.project_id)
    node_id = ctx.payload["node_id"]
    characters = {c.id: c for c in store.list_characters(ctx.project_id)}
    chain = graph.path_to(node_id)
    await ctx.progress(0.3, f"summarizing {len(chain)} nodes")
    summary, tail = await ctx_mod.rolling_summary(
        ctx.project_id, graph, node_id, chain, characters,
        int(store.get_setting("verbatim_token_budget", 2000)),
    )
    return {"summary": summary, "verbatim_nodes": len(tail)}


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

async def _progress_bridge(ctx: JobContext, floor: float, ceiling: float):
    async def report(fraction: float, message: str) -> None:
        await ctx.progress(floor + (ceiling - floor) * fraction, message)
    return report


def cg_inputs(project_id: str, node: Node, payload: Optional[dict[str, Any]] = None
              ) -> dict[str, Any]:
    """Everything a CG render needs, resolved from the node and the path.

    Shared by the render job and the prompt-preview endpoint, so what the
    inspector shows you is what ComfyUI is handed -- a preview assembled by a
    second, slightly different code path is worse than no preview.
    """
    payload = payload or {}
    project = store.get_project(project_id)
    graph = _graph(project_id)
    characters = {c.id: c for c in store.list_characters(project_id)}
    locations = {l.id: l for l in store.list_locations(project_id)}
    state = resolve_state(graph.path_to(node.id), include_last=True)
    d = node.data or {}

    # The node's hand-picked cast wins over who happens to be on stage; the
    # payload can override it again so the inspector can preview a cast before
    # it is saved.
    override = payload.get("subjects")
    if override is None:
        override = d.get("cg_subjects") or None
    subjects = ctx_mod.cg_subjects(
        characters, state, payload.get("speaker") or d.get("speaker") or "",
        limit=int(payload.get("max_subjects") or 2), override=override)

    # The sprite pose is a fallback only, and only the half of it that says
    # what the body is doing -- the camera is the director's call in a CG, and
    # the enum key itself is not a tag at all (images.POSE_CG_TAGS).
    action = (payload.get("action") or d.get("cg_action", "")
              or images.POSE_CG_TAGS.get(d.get("pose", ""), ""))
    # This shot's own tag switches. A payload override lets the inspector
    # preview a change before it is saved, the same way `subjects` does.
    hidden = images.hidden_set(
        payload.get("hidden_tags")
        if payload.get("hidden_tags") is not None else d.get("cg_hidden_tags"))
    # The other direction: tags the framing table would cut that this shot
    # wants anyway. A close-up composed over a shoulder can show a skirt.
    forced = images.hidden_set(
        payload.get("forced_tags")
        if payload.get("forced_tags") is not None else d.get("cg_forced_tags"))
    prompt = images.assemble_cg_prompt(
        project, subjects, state, locations,
        camera=payload.get("camera") or d.get("cg_camera", ""),
        expression=d.get("expression", "neutral"),
        action=action,
        extra_negative=payload.get("negative") or d.get("cg_negative", ""),
        lora_mode=payload.get("lora") or d.get("cg_lora", ""),
        shot_hint=payload.get("shot_hint") or d.get("shot_hint", ""),
        hidden=hidden,
        viewer=next((c for c in characters.values() if c.is_player), None),
        # The director's English read of the beat, written before it picked a
        # single tag. Natural language rides along as support for the tags.
        description=payload.get("description") or d.get("cg_description", ""),
        forced=forced,
    )
    focal = subjects[0] if subjects else None
    lora, weight = images.cg_lora_for(focal, payload.get("lora") or d.get("cg_lora", ""))
    face = (images.assemble_face_prompt(project, focal, state,
                                        d.get("expression", "neutral"), hidden=hidden)
            if focal is not None else None)
    face_lora, _ = (images.lora_slot(focal, "cg" if focal.assets.lora_cg else "sprite")
                    if focal is not None else ("", 0.0))
    return {
        "prompt": prompt, "subjects": subjects, "state": state,
        "lora": lora, "lora_weight": weight,
        "face_prompt": face, "face_lora": face_lora,
        "face_lora_weight": focal.assets.face_lora_weight if focal else 0.9,
    }


@handler("image.generate")
async def generate_image(ctx: JobContext) -> dict[str, Any]:
    """Event CG for one node."""
    node = _require_node(ctx.payload["node_id"])

    d = node.data or {}
    distill_note = ""
    if (ctx.payload.get("distill_if_missing")
            and not (d.get("cg_camera") or d.get("cg_action"))):
        await ctx.progress(0.03, "distilling the shot")
        try:
            assembled = ctx_mod.assemble_cg(ctx.project_id, _graph(ctx.project_id), node.id)
            fields, _meta = await generation.cg_director_pass(ctx.project_id, assembled)
            node = _require_node(node.id)
            node.data = {**(node.data or {}), **fields}
            store.update_node(ctx.project_id, node)
        except Exception as exc:  # noqa: BLE001
            # Never fail a render over the director. This path is the overnight
            # batch, where LM Studio may well not be up -- falling back to the
            # shot hint gives a worse CG, which beats an empty one.
            distill_note = f"rendered from the shot hint: the director failed ({exc})"

    resolved = cg_inputs(ctx.project_id, node, ctx.payload)
    prompt = resolved["prompt"]

    seed = images.new_seed(ctx.payload.get("seed"))

    # The face-detail pass is what lets the main render run the character LoRA
    # weak or not at all (the CG default) and still come back looking like her.
    # Skipped when the install lacks the Impact Pack, or when there is no
    # character in the shot to have a face.
    want_face = (ctx.payload.get("face_detail")
                 if ctx.payload.get("face_detail") is not None
                 else bool(store.get_setting("cg_face_detail", True)))
    face_prompt = None
    face_note = ""
    if want_face and resolved["face_prompt"] is not None:
        if await images.face_detail_available():
            face_prompt = resolved["face_prompt"]
        else:
            face_note = ("no face-detail pass: ComfyUI has no FaceDetailer / "
                         "UltralyticsDetectorProvider node (install "
                         "ComfyUI-Impact-Pack, or turn cg_face_detail off)")

    # Candidates to choose between, rather than one roll of the dice. The cost
    # of a CG is not the sampling, it is the round trip -- queue, wait, look,
    # dislike, click again -- and a batch collapses N of those into one. Only
    # ever what the caller asked for: the unattended paths (batch fill, stale
    # sweep) leave it at 1, because nobody is there to pick and four times the
    # renders is four times the overnight hours.
    batch = max(1, min(int(ctx.payload.get("batch") or 1), MAX_CG_BATCH))

    workflow = images.build_workflow(
        prompt,
        width=int(ctx.payload.get("width") or store.get_setting("cg_width", 1344)),
        height=int(ctx.payload.get("height") or store.get_setting("cg_height", 768)),
        seed=seed, prefix=f"cg_{node.id[:8]}",
        lora=resolved["lora"], lora_weight=resolved["lora_weight"],
        face_prompt=face_prompt, face_lora=resolved["face_lora"],
        face_lora_weight=resolved["face_lora_weight"],
        batch=batch,
    )

    node.image_status = AssetStatus.running
    store.update_node(ctx.project_id, node)
    await ctx.progress(0.1, "queued in ComfyUI"
                            + (f" (batch of {batch})" if batch > 1 else ""))

    hashes = await images.run_and_store(
        workflow, on_progress=await _progress_bridge(ctx, 0.1, 0.95),
        # Per image, because a batch's images are not interchangeable and the
        # gallery has to be able to say which of them it is showing.
        meta_fn=lambda index: {"node": node.id, "seed": seed,
                               "prompt": prompt["positive"],
                               **({"batch_index": index, "batch": batch}
                                  if batch > 1 else {})},
    )

    node = _require_node(node.id)
    data = dict(node.data or {})
    data["image_hash"] = hashes[0]
    data["cg"] = True
    node.data = data
    node.image_status = AssetStatus.ready
    staleness.stamp(node, "image")
    # One take per candidate, so the strip in the inspector *is* the picker --
    # clicking one applies it through the same apply_variant a rollback uses.
    # `batch_index` rides in the payload because the seed alone does not
    # identify which image of a batch this was (images.build_workflow).
    takes = [
        generation.add_variant(
            ctx.project_id, node, "image",
            {"seed": seed, "prompt": prompt,
             **({"batch_index": index, "batch": len(hashes)} if len(hashes) > 1 else {})},
            GenMeta(seed=seed, params=prompt, timestamp=store.now()),
            asset_hash=digest)
        for index, digest in enumerate(hashes)
    ]
    # add_variant makes each new take active in turn; the node shows the first
    # one, so that is the take that has to end up marked active.
    if len(takes) > 1:
        node.active_variant = takes[0].id
        store.update_node(ctx.project_id, node)

    result = {"hash": hashes[0], "hashes": hashes, "count": len(hashes),
              "seed": seed, "prompt": prompt,
              "face_detail": face_prompt is not None}
    notes = [n for n in (distill_note, face_note) if n]
    if notes:
        result["note"] = "; ".join(notes)
    return result


@handler("image.refine")
async def refine_image(ctx: JobContext) -> dict[str, Any]:
    """Re-sample an existing take instead of rolling a new one from noise.

    The case this exists for: a CG whose composition, lighting and expression
    are all right and whose left hand is wrong. Re-rolling throws away the four
    good things to fix the one bad one, and the seed does not let you keep
    them. This starts from the image and denoises it, optionally only inside a
    region the author painted.

    The output is a take on the same node, so the variant strip, the rollback
    and the staleness stamp all behave exactly as they do for a fresh render --
    a refinement is a candidate like any other, and picking the original back
    is one click.
    """
    node = _require_node(ctx.payload["node_id"])
    d = node.data or {}
    # An explicit take wins, so the strip can refine a candidate that is not
    # the one currently applied to the node.
    source_hash = ctx.payload.get("source_hash") or d.get("image_hash")
    if not source_hash:
        raise RuntimeError("nothing to refine: this node has no image yet")

    resolved = cg_inputs(ctx.project_id, node, ctx.payload)
    prompt = resolved["prompt"]
    seed = images.new_seed(ctx.payload.get("seed"))
    denoise = images.clamp_denoise(ctx.payload.get("denoise", 0.45))
    batch = max(1, min(int(ctx.payload.get("batch") or 1), MAX_CG_BATCH))

    await ctx.progress(0.05, "staging the source image")
    source_name = await images.stage_asset(source_hash, f"vn_src_{source_hash[:16]}.png")

    mask_name = ""
    mask_bytes = images.decode_mask(ctx.payload.get("mask") or "")
    if mask_bytes:
        mask_name = await images.stage_image(
            mask_bytes, f"vn_mask_{node.id[:8]}_{seed}.png")

    workflow = images.build_img2img_workflow(
        prompt, source_image=source_name, seed=seed,
        prefix=f"refine_{node.id[:8]}", denoise=denoise,
        lora=resolved["lora"], lora_weight=resolved["lora_weight"],
        mask_image=mask_name, batch=batch,
    )

    node.image_status = AssetStatus.running
    store.update_node(ctx.project_id, node)
    await ctx.progress(0.12, f"refining at denoise {denoise:g}"
                             + (" inside the painted region" if mask_name else ""))

    hashes = await images.run_and_store(
        workflow, on_progress=await _progress_bridge(ctx, 0.12, 0.95),
        meta_fn=lambda index: {
            "node": node.id, "seed": seed, "prompt": prompt["positive"],
            "refined_from": source_hash, "denoise": denoise,
            "masked": bool(mask_name),
            **({"batch_index": index, "batch": batch} if batch > 1 else {}),
        },
    )

    node = _require_node(node.id)
    data = dict(node.data or {})
    data["image_hash"] = hashes[0]
    node.data = data
    node.image_status = AssetStatus.ready
    staleness.stamp(node, "image")
    takes = [
        generation.add_variant(
            ctx.project_id, node, "image",
            {"seed": seed, "prompt": prompt, "denoise": denoise,
             "refined_from": source_hash, "masked": bool(mask_name),
             **({"batch_index": index, "batch": len(hashes)} if len(hashes) > 1 else {})},
            GenMeta(seed=seed, params=prompt, timestamp=store.now()),
            asset_hash=digest)
        for index, digest in enumerate(hashes)
    ]
    if len(takes) > 1:
        node.active_variant = takes[0].id
        store.update_node(ctx.project_id, node)

    return {"hash": hashes[0], "hashes": hashes, "count": len(hashes),
            "seed": seed, "denoise": denoise, "masked": bool(mask_name),
            "refined_from": source_hash}


@handler("image.cg_prompt")
async def distill_cg_prompt(ctx: JobContext) -> dict[str, Any]:
    """Run the CG director over one node and write the tags onto it.

    Separate from image.generate so the distilled prompt lands in the inspector
    where it can be read and edited *before* anything is rendered -- the whole
    point of exposing it. Re-running overwrites all three fields, so hand edits
    are lost on a re-distill -- the trade for one plain editable field per
    prompt slot instead of a pile of "has this been touched?" state.

    `skip_if_set` makes that overwrite conditional, which is what lets the
    batch queue a director job per undirected CG without racing the render it
    precedes: whichever runs first, the node ends up directed exactly once.
    """
    node = _require_node(ctx.payload["node_id"])
    if ctx.payload.get("skip_if_set") and not batch_svc.needs_direction(node.data or {}):
        return {"skipped": "the node already has a shot to work from",
                "cg_camera": (node.data or {}).get("cg_camera", ""),
                "cg_action": (node.data or {}).get("cg_action", "")}
    graph = _graph(ctx.project_id)
    await ctx.progress(0.1, "assembling context")
    assembled = ctx_mod.assemble_cg(ctx.project_id, graph, node.id,
                                    instruction=ctx.payload.get("instruction", ""))
    await ctx.progress(0.3, "directing the shot")
    fields, _meta = await generation.cg_director_pass(ctx.project_id, assembled)

    node = _require_node(node.id)
    data = dict(node.data or {})
    data.update(fields)
    node.data = data
    store.update_node(ctx.project_id, node)
    return {**fields, "subjects": assembled["subjects"],
            "token_estimate": assembled["token_estimate"]}


@handler("image.background")
async def generate_background(ctx: JobContext) -> dict[str, Any]:
    location_id = ctx.payload["location_id"]
    variant = ctx.payload.get("variant") or ctx.payload.get("time_of_day") or "day"
    weather = ctx.payload.get("weather", "")
    project = store.get_project(ctx.project_id)
    location = next((l for l in store.list_locations(ctx.project_id) if l.id == location_id), None)
    if location is None:
        raise ValueError(f"unknown location {location_id}")

    prompt = images.assemble_background_prompt(project, location, variant, weather)
    seed = images.new_seed(ctx.payload.get("seed"))
    workflow = images.build_workflow(
        prompt,
        width=int(ctx.payload.get("width") or 1344),
        height=int(ctx.payload.get("height") or 768),
        seed=seed, prefix=f"bg_{location_id}_{variant}",
    )
    await ctx.progress(0.1, "queued in ComfyUI")
    hashes = await images.run_and_store(
        workflow, on_progress=await _progress_bridge(ctx, 0.1, 0.95),
        meta={"location": location_id, "variant": variant, "seed": seed},
    )

    location.variants[variant] = hashes[0]
    store.save_location(ctx.project_id, location)
    return {"hash": hashes[0], "variant": variant, "seed": seed, "prompt": prompt}


@handler("image.sprite")
async def generate_sprite(ctx: JobContext) -> dict[str, Any]:
    """One character x outfit x pose x expression sprite, background removed."""
    character = store.get_character(ctx.project_id, ctx.payload["character_id"])
    if character is None:
        raise ValueError("unknown character")
    if character.age < 18:
        raise ValueError("character is under 18 and cannot reach the image pipeline")

    # Falling through to outfits[0] on a character with no default picks
    # whatever happens to be first -- which is the nude entry often enough to
    # matter, and writes an outfit-less sprite key nothing can ever show.
    outfit_id = ctx.payload.get("outfit_id") or character.default_outfit or next(
        (o.id for o in character.outfits if o.id != "none"),
        character.outfits[0].id if character.outfits else "")
    expression = ctx.payload.get("expression", "neutral")
    pose = ctx.payload.get("pose") or "front"
    project = store.get_project(ctx.project_id)

    prompt = images.assemble_sprite_prompt(
        project, character, outfit_id, expression, pose,
        extra_positive=ctx.payload.get("extra_positive", ""),
        extra_negative=ctx.payload.get("extra_negative", ""),
    )
    seed = images.new_seed(ctx.payload.get("seed"))
    can_rembg = await images.rembg_available()
    workflow = images.build_workflow(
        prompt,
        width=int(store.get_setting("image_width", 832)),
        height=int(store.get_setting("image_height", 1216)),
        seed=seed, prefix=f"sprite_{character.id}_{outfit_id}_{pose}_{expression}",
        lora=character.assets.lora or "", lora_weight=character.assets.lora_weight,
        sprite=True, rembg_available=can_rembg,
    )
    await ctx.progress(0.1, "queued in ComfyUI" + ("" if can_rembg else " (no rembg node found)"))
    hashes = await images.run_and_store(
        workflow, on_progress=await _progress_bridge(ctx, 0.1, 0.95),
        meta={"character": character.id, "outfit": outfit_id, "pose": pose,
              "expression": expression},
    )

    character = store.get_character(ctx.project_id, character.id)
    key = ctx.payload.get("slot") or images.sprite_slot(outfit_id, pose, expression)
    character.assets.sprite_sheet[key] = hashes[0]
    store.save_character(ctx.project_id, character)
    return {"hash": hashes[0], "slot": key, "seed": seed,
            "background_removed": can_rembg, "prompt": prompt}


@handler("image.profile")
async def generate_profile_image(ctx: JobContext) -> dict[str, Any]:
    """One headshot for the character overview grid.

    Identity tags plus the caller's expression, framed as an upper-body
    portrait on a plain background (images.assemble_portrait_prompt). Loads the
    CG LoRA if the character has one, else the Sprite LoRA, else none. The
    result is written onto character.assets.profile_image and nowhere else --
    it never reaches the compiled game.
    """
    character = store.get_character(ctx.project_id, ctx.payload["character_id"])
    if character is None:
        raise ValueError("unknown character")
    if character.age < 18:
        raise ValueError("character is under 18 and cannot reach the image pipeline")

    project = store.get_project(ctx.project_id)
    expression = ctx.payload.get("expression", "neutral")
    outfit_id = ctx.payload.get("outfit_id") or character.default_outfit

    prompt = images.assemble_portrait_prompt(project, character, expression, outfit_id)
    lora_name, lora_weight = images.portrait_lora(character)
    seed = images.new_seed(ctx.payload.get("seed"))
    workflow = images.build_workflow(
        prompt,
        width=int(store.get_setting("profile_width", 768)),
        height=int(store.get_setting("profile_height", 768)),
        seed=seed, prefix=f"profile_{character.id}",
        lora=lora_name, lora_weight=lora_weight,
    )
    await ctx.progress(0.1, "queued in ComfyUI")
    hashes = await images.run_and_store(
        workflow, on_progress=await _progress_bridge(ctx, 0.1, 0.95),
        meta={"character": character.id, "profile": True, "expression": expression,
              "seed": seed})

    character = store.get_character(ctx.project_id, character.id)
    character.assets.profile_image = hashes[0]
    store.save_character(ctx.project_id, character)
    return {"hash": hashes[0], "seed": seed, "expression": expression, "prompt": prompt}


@handler("image.sheet")
async def generate_character_sheet(ctx: JobContext) -> dict[str, Any]:
    """`count` sampled outfit/angle/expression combinations at one fixed seed
    -- the Sprite LoRA bootstrap.

    Sampled rather than a full product: outfits x angles x expressions reaches
    three figures on a character with a few outfits, while the LoRA wants
    15-30 good images (images.sheet_plan). Outfits still default to all of
    them -- one outfit only would train a LoRA that fuses the character to
    that outfit (lora-training.md, "train on a mix").

    Every image is the sprite look on purpose: solo, full body, white
    background (image.sheet_wrapper). This LoRA's whole job is drawing
    sprites, so its dataset matches what it will be asked for. Variety --
    action, framing, backdrops, other people in frame -- belongs to the CG
    LoRA and its own training set (image.cg_set), not here.

    Every image produced is APPENDED to character.assets.sheet_pool, never
    replacing what's already there -- re-running this (e.g. after touching up
    one outfit's tags) grows the working set instead of throwing the last run
    away. Saved after each image, so cancelling partway keeps what finished.
    """
    character = store.get_character(ctx.project_id, ctx.payload["character_id"])
    if character is None:
        raise ValueError("unknown character")
    if character.age < 18:
        raise ValueError("character is under 18 and cannot reach the image pipeline")

    project = store.get_project(ctx.project_id)
    angles = ctx.payload.get("angles") or images.DEFAULT_SHEET_ANGLES
    expressions = ctx.payload.get("expressions") or images.DEFAULT_SHEET_EXPRESSIONS
    outfit_ids = (ctx.payload.get("outfit_ids")
                  or [o.id for o in character.outfits] or [""])
    count = max(1, int(ctx.payload.get("count") or 30))
    seed = images.new_seed(ctx.payload.get("seed"))   # fixed across the whole batch,
                                                       # outfits included -- same noise,
                                                       # only the outfit tokens change,
                                                       # which is what keeps the face
                                                       # comparable across outfits too
    plan = images.sheet_plan(angles, expressions, outfit_ids, count=count, seed=seed)

    produced: list[dict[str, Any]] = []
    # A `count` past the size of the grid repeats combinations (sheet_plan).
    # The batch seed is one number, so a repeated cell would re-render the
    # exact same picture -- same prompt, same noise -- and the content-addressed
    # store would hand back the hash that is already in the pool. Nudging the
    # seed by how many times this cell has come up keeps the first pass of
    # every combination on the shared seed (faces stay comparable for culling)
    # and makes each repeat an actual second take.
    repeats: dict[tuple[str, str, str], int] = {}
    for i, item in enumerate(plan):
        ctx.check_cancelled()
        outfit_id = item["outfit_id"]
        cell = (item["outfit_id"], item["angle"], item["expression"])
        repeat = repeats.get(cell, 0)
        repeats[cell] = repeat + 1
        item_seed = (seed + repeat - 1) % (2 ** 31 - 1) + 1
        prompt = images.assemble_sprite_prompt(
            project, character, outfit_id, item["expression"], angle=item["angle"],
            wrapper="image.sheet_wrapper")
        workflow = images.build_workflow(
            prompt, width=int(store.get_setting("image_width", 832)),
            height=int(store.get_setting("image_height", 1216)),
            seed=item_seed, prefix=f"sheet_{character.id}_{outfit_id or 'default'}_{i:03d}",
            lora=character.assets.lora or "", lora_weight=character.assets.lora_weight,
        )
        await ctx.progress(i / max(1, len(plan)),
                           f"{i + 1}/{len(plan)}: {outfit_id or 'default'} · "
                           f"{item['angle'][:24]} / {item['expression']}")
        hashes = await images.run_and_store(
            workflow, meta={"character": character.id, "sheet": True,
                            **item, "seed": item_seed}
        )
        pool_item = SheetPoolItem(hash=hashes[0], seed=item_seed, **item)
        produced.append(pool_item.model_dump())

        # Re-fetch + append + save per image (not once at the end) so a
        # cancelled run still keeps everything generated so far.
        character = store.get_character(ctx.project_id, character.id)
        character.assets.sheet_pool.append(pool_item)
        store.save_character(ctx.project_id, character)

    return {"count": len(produced), "seed": seed, "items": produced,
            "pool_size": len(character.assets.sheet_pool)}


@handler("image.cg_set")
async def generate_cg_set(ctx: JobContext) -> dict[str, Any]:
    """The CG LoRA's own training set, shot with the Sprite LoRA.

    The Sprite LoRA learned from the character sheet, so everything it has
    ever seen is a studio shot -- one framing, one backdrop, one distance.
    Training a second LoRA on more of the same teaches it nothing new. This
    job spends the Sprite LoRA's one real strength (it knows the face) on the
    thing it lacks (variety): the character in the project's actual
    locations, at varied angles, framings and times of day, with a *fresh
    seed per image* -- the sheet's fixed seed exists to make faces comparable
    for culling, and here it would just produce the same picture repeatedly.

    Output lands in `cg_pool`, never `sheet_pool` -- the CG LoRA is culled,
    captioned and trained as its own dataset, on its own tab, so it can be
    grown, pruned and retrained without touching the sprite set at all.

    Three things the sheet holds constant are variables here, because an event
    CG is the opposite of a sprite:

    - **action** -- a CG prompt is mostly context and action, not a pose enum,
      so the dataset has to have taught the LoRA that its subject's body moves.
    - **company** -- a CG is shot from the player's point of view, so someone
      else is often in frame or right against the camera. Trained only on solo
      studio shots, a LoRA fights every two-hander and every close POV moment
      (defaults.CG_SET_COMPANY; the second figure is faceless on purpose).
    - **framing and backdrop** -- real project locations at varied distances.

    Every list-valued field (`outfit_ids`, `angles`, `expressions`, `actions`,
    `company`, `framings`, `times`, `scenes`) is *sampled independently* per
    image rather than combined into a product -- `count` sets how many images
    to shoot. Passing single-element lists for all of them (as the pool's
    "regenerate this one" does) pins every image to that one recipe; only the
    seed still varies.
    """
    character = store.get_character(ctx.project_id, ctx.payload["character_id"])
    if character is None:
        raise ValueError("unknown character")
    if character.age < 18:
        raise ValueError("character is under 18 and cannot reach the image pipeline")
    if not character.assets.lora:
        raise RuntimeError(
            f"{character.display_name} has no Sprite LoRA yet. The CG training set "
            "is shot *with* it -- run the character sheet, cull, caption and train "
            "the Sprite LoRA first.")

    project = store.get_project(ctx.project_id)
    location_ids = ctx.payload.get("location_ids") or []
    locations = [l for l in store.list_locations(ctx.project_id)
                if not location_ids or l.id in location_ids]
    count = max(1, int(ctx.payload.get("count") or 24))
    times = ctx.payload.get("times") or ["day", "evening", "night", "morning"]
    # Empty in the normal bulk call (the sampler falls back to the full
    # vocabulary); set to a single-element list by "regenerate this one" to
    # pin that image's exact recipe.
    scenes_override = ctx.payload.get("scenes") or []

    plan = images.cg_set_plan(
        count,
        angles=ctx.payload.get("angles") or [],
        expressions=ctx.payload.get("expressions") or [],
        outfit_ids=(ctx.payload.get("outfit_ids")
                    or [o.id for o in character.outfits] or [""]),
        actions=ctx.payload.get("actions") or [],
        company=ctx.payload.get("company") or [],
        framings=ctx.payload.get("framings") or [],
    )

    produced: list[dict[str, Any]] = []
    for i, recipe in enumerate(plan):
        ctx.check_cancelled()
        if scenes_override:
            scene = scenes_override[i % len(scenes_override)]
        elif locations:
            # A real location beats a synthetic backdrop here: it is the
            # lighting and the clutter behind the character that the sheet
            # never taught.
            loc = locations[i % len(locations)]
            loc_tags, lighting = ctx_mod.location_tags(loc, times[i % len(times)], "")
            scene = ", ".join(p for p in (loc_tags or loc.name, lighting) if p)
        else:
            scene = images.SHEET_SCENES[i % len(images.SHEET_SCENES)]

        prompt = images.assemble_cg_set_prompt(
            project, character, recipe["outfit_id"], recipe["expression"],
            angle=recipe["angle"], action=recipe["action"],
            company=recipe["company"], framing=recipe["framing"], scene=scene)
        seed = images.new_seed(None)
        workflow = images.build_workflow(
            prompt, width=int(store.get_setting("image_width", 832)),
            height=int(store.get_setting("image_height", 1216)),
            seed=seed, prefix=f"cgset_{character.id}_{i:03d}",
            lora=character.assets.lora, lora_weight=character.assets.lora_weight,
        )
        await ctx.progress(i / count,
                           f"{i + 1}/{count}: {recipe['framing']} · "
                           f"{recipe['action'][:20]} / {recipe['expression']}")
        hashes = await images.run_and_store(
            workflow, meta={"character": character.id, "cg_set": True,
                            **recipe, "scene": scene, "seed": seed})
        item = SheetPoolItem(hash=hashes[0], scene=scene, seed=seed, **recipe)
        produced.append(item.model_dump())

        character = store.get_character(ctx.project_id, character.id)
        character.assets.cg_pool.append(item)
        store.save_character(ctx.project_id, character)

    return {"count": len(produced), "items": produced,
            "pool_size": len(character.assets.cg_pool)}


@handler("image.caption")
async def caption_sheet(ctx: JobContext) -> dict[str, Any]:
    """Auto-caption pool images with a WD14 tagger node. Captions are written
    back onto the named pool (matched by hash), not just returned, so they
    survive a page reload and a later training run doesn't need them passed
    back in."""
    character_id: str = ctx.payload.get("character_id") or ""
    hashes: list[str] = ctx.payload.get("hashes") or []
    # Which pool to write captions back onto -- the Sprite LoRA's sheet_pool or
    # the CG LoRA's own cg_pool. They are captioned and trained separately, so
    # this has to be told which one it is looking at rather than guessing by
    # hash membership.
    pool_name: str = ctx.payload.get("pool") or "sheet"
    captions: dict[str, str] = {}
    classes = dict(store.get_setting("node_classes", {}) or {})
    tagger_class = classes.get("tagger_class", "WD14Tagger|pysssss")

    try:
        info = await comfyui.object_info()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"ComfyUI unreachable: {exc}") from exc
    if tagger_class not in info:
        raise RuntimeError(
            f"ComfyUI has no '{tagger_class}' node. Install a WD14 tagger node or set "
            "node_classes.tagger_class in settings."
        )

    for i, asset_hash in enumerate(hashes):
        ctx.check_cancelled()
        path = assets.path_for(asset_hash)
        if path is None:
            continue
        uploaded = await comfyui.upload_image(path.read_bytes(), f"{asset_hash}.png")
        workflow = images.get_workflow("tagger")
        from ..services import workflows as wf_mod
        workflow = wf_mod.fill(workflow, {
            "image": uploaded.get("name", f"{asset_hash}.png"),
            "tagger_class": tagger_class,
            "tagger_model": classes.get("tagger_model", "wd-v1-4-moat-tagger-v2"),
        })
        entry = await comfyui.run_workflow(workflow)
        text = ""
        for node_out in (entry.get("outputs") or {}).values():
            for key in ("text", "tags", "string"):
                if node_out.get(key):
                    value = node_out[key]
                    text = value[0] if isinstance(value, list) else str(value)
                    break
        captions[asset_hash] = text
        await ctx.progress((i + 1) / max(1, len(hashes)), f"captioned {i + 1}/{len(hashes)}")

    if character_id and captions:
        character = store.get_character(ctx.project_id, character_id)
        if character is not None:
            pool = character.assets.cg_pool if pool_name == "cg" else character.assets.sheet_pool
            for pool_item in pool:
                if pool_item.hash in captions:
                    pool_item.caption = captions[pool_item.hash]
            store.save_character(ctx.project_id, character)

    return {"captions": captions, "count": len(captions)}


# --------------------------------------------------------------------------
# LoRA training (Kohya)
# --------------------------------------------------------------------------

def _kohya_python(kohya_path: str) -> str:
    """Interpreter for the training subprocess.

    Kohya's deps (torch, accelerate, bitsandbytes) live in its own venv, not
    the backend's. Prefer an explicit `kohya_python` setting, else the venv
    sitting next to the scripts, else bare "python" (doc lora-training.md S3.3).
    """
    explicit = store.get_setting("kohya_python", "")
    if explicit:
        return explicit
    base = Path(kohya_path)
    for rel in ("venv/Scripts/python.exe", "venv/bin/python",
                ".venv/Scripts/python.exe", ".venv/bin/python"):
        if (base / rel).exists():
            return str(base / rel)
    return "python"


def _publish_to_comfyui(produced: Path) -> tuple[str, Optional[str]]:
    """Make a freshly trained LoRA loadable by ComfyUI without the manual step.

    ComfyUI's `LoraLoader` resolves names relative to `ComfyUI/models/loras/`,
    never by absolute path (doc lora-training.md S8). If `comfyui_lora_dir` is
    set, hardlink the file into it -- falling back to a copy across volumes --
    and return the bare filename to store on the character. If it is not set,
    return the absolute path unchanged: generation then fails with the S8
    error, which is the pre-existing behaviour and tells the user what to do.

    Returns `(value for character.assets.lora, note for the job result or None)`.
    """
    lora_dir = store.get_setting("comfyui_lora_dir", "")
    if not lora_dir:
        return str(produced), None
    dest = Path(lora_dir) / produced.name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)       # refresh the link on every retrain
        try:
            os.link(produced, dest)
        except OSError:
            shutil.copy2(produced, dest)   # different volume -- no hardlink
    except OSError as exc:  # noqa: BLE001
        return str(produced), (
            f"could not place the LoRA in {lora_dir} ({exc}) -- left the absolute "
            "path; copy it into ComfyUI/models/loras/ by hand (doc S8)"
        )
    return produced.name, f"linked into {lora_dir} as {produced.name}"


@handler("lora.train")
async def train_lora(ctx: JobContext) -> dict[str, Any]:
    """Write a Kohya dataset + config and run sdxl_train_network.py.

    ~20-40 min on a 4090 for an SDXL character LoRA. Dataset quality matters
    far more than size: 20 clean varied images beat 100 messy ones (doc S4).
    """
    character = store.get_character(ctx.project_id, ctx.payload["character_id"])
    if character is None:
        raise ValueError("unknown character")

    kohya_path = store.get_setting("kohya_path", "")
    if not kohya_path or not Path(kohya_path).exists():
        raise RuntimeError("kohya_path is not set in settings -- point it at your kohya_ss "
                           "checkout to enable LoRA training")

    # Which slot the result lands in, and which pool its default dataset comes
    # from. The Sprite LoRA trains on sheet_pool (the character sheet); the CG
    # LoRA trains on cg_pool (image.cg_set's own varied dataset) -- separate
    # pools, culled and captioned separately, because the two datasets have
    # nothing in common but the character.
    slot = "cg" if str(ctx.payload.get("slot", "sprite")) == "cg" else "sprite"
    pool = character.assets.cg_pool if slot == "cg" else character.assets.sheet_pool
    pool_label = "CG LoRA pool" if slot == "cg" else "Sprite LoRA pool"

    # No hashes given -> train on the whole pool (the normal path: the UI
    # never asks the user to pick images individually, only to delete what
    # they don't want -- doc lora-training.md S5). Explicit hashes/captions
    # stay supported for the hand-assembled-dataset path (any Gallery hashes,
    # not just pool ones).
    if ctx.payload.get("hashes"):
        hashes: list[str] = ctx.payload["hashes"]
        captions: dict[str, str] = ctx.payload.get("captions") or {}
    else:
        hashes = [item.hash for item in pool]
        captions = {item.hash: item.caption for item in pool}
    if len(hashes) < 8:
        raise RuntimeError(f"only {len(hashes)} images in the {pool_label}; 15-30 varied "
                           "images is the working range for a character LoRA")

    trigger = character.assets.trigger or f"chr_{character.id}"
    repeats = int(ctx.payload.get("repeats", 10))
    # Namespaced by slot so training both LoRAs for one character can't have
    # the CG run's dataset directory collide with (and silently overwrite)
    # the Sprite run's.
    dataset = db.ROOT / "lora" / character.id / slot / f"{repeats}_{trigger}"
    dataset.mkdir(parents=True, exist_ok=True)
    for old in dataset.glob("*"):
        old.unlink()

    for i, asset_hash in enumerate(hashes):
        src = assets.path_for(asset_hash)
        if src is None:
            continue
        (dataset / f"{i:03d}.png").write_bytes(src.read_bytes())
        caption = captions.get(asset_hash, "")
        tags = f"{trigger}, {caption}".strip().strip(",")
        (dataset / f"{i:03d}.txt").write_text(tags, encoding="utf-8")

    output_dir = db.ROOT / "lora" / character.id / slot / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = ctx.payload.get("output_name") or f"chr_{character.id}_{slot}"

    script = Path(kohya_path) / "sdxl_train_network.py"
    if not script.exists():
        raise RuntimeError(f"{script} not found -- is kohya_path the kohya_ss root?")

    cmd = [
        ctx.payload.get("python") or _kohya_python(kohya_path), str(script),
        "--pretrained_model_name_or_path", ctx.payload.get("base_model")
            or store.get_setting("lora_base_model", ""),
        "--train_data_dir", str(dataset.parent),
        "--output_dir", str(output_dir),
        "--output_name", output_name,
        "--resolution", ctx.payload.get("resolution", "1024,1024"),
        "--network_module", "networks.lora",
        "--network_dim", str(ctx.payload.get("network_dim", 32)),
        "--network_alpha", str(ctx.payload.get("network_alpha", 16)),
        "--learning_rate", str(ctx.payload.get("learning_rate", 1e-4)),
        "--max_train_epochs", str(ctx.payload.get("epochs", 10)),
        "--train_batch_size", str(ctx.payload.get("batch_size", 1)),
        "--mixed_precision", "bf16", "--save_precision", "bf16",
        "--optimizer_type", "AdamW8bit",
        "--sdpa", "--cache_latents", "--gradient_checkpointing",
        "--save_model_as", "safetensors",
    ]
    if not cmd[3]:
        raise RuntimeError("set lora_base_model in settings (path to the SDXL base checkpoint)")

    await ctx.progress(0.02, f"training on {len(hashes)} images")

    # Kohya switches on DDP the moment it sees >1 visible CUDA device
    # (library/accelerator_setup.py), but we launch with plain `python`, not
    # `accelerate launch` -- so no process group is ever initialized and
    # Accelerator() dies with "Default process group has not been initialized".
    # Pin the trainer to a single GPU (the 5080 by default) so device_count()==1
    # and it stays single-process. `lora_gpu` overrides which index; also drop any
    # distributed env vars leaking in from the shell that started the backend.
    gpu = str(ctx.payload.get("gpu") if ctx.payload.get("gpu") is not None
              else store.get_setting("lora_gpu", "0"))
    train_env = {k: v for k, v in os.environ.items()
                 if k not in ("RANK", "LOCAL_RANK", "WORLD_SIZE", "LOCAL_WORLD_SIZE",
                              "MASTER_ADDR", "MASTER_PORT", "ACCELERATE_USE_CPU")}
    train_env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    train_env["CUDA_VISIBLE_DEVICES"] = gpu
    # Kohya's log lines contain Japanese; the child's piped stdout would default
    # to the Windows ANSI codepage (cp1252) and die with UnicodeEncodeError.
    train_env["PYTHONUTF8"] = "1"
    train_env["PYTHONIOENCODING"] = "utf-8"

    # Feed Kohya's step counter back into the job bar. Its tqdm line is
    # `steps:  12%|...| 240/2000 [..., 2.9it/s]`, redrawn with \r rather than
    # \n, so read raw and split on both. Map steps -> 0.02..0.98.
    loop = asyncio.get_running_loop()
    step_re = re.compile(r"^steps:.*?\b(\d+)/(\d+)\b")

    def _report(frac: float, msg: str) -> None:
        asyncio.run_coroutine_threadsafe(ctx.progress(frac, msg), loop)

    def _run() -> tuple[int, str]:
        proc = subprocess.Popen(cmd, cwd=kohya_path, env=train_env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
        tail: list[str] = []
        buf = ""
        last = 0.0
        stream = proc.stdout
        assert stream is not None
        while True:
            chunk = stream.read(512)
            if not chunk:
                break
            buf += chunk
            pieces = re.split(r"[\r\n]", buf)
            buf = pieces.pop()
            for piece in pieces:
                piece = piece.strip()
                if not piece:
                    continue
                tail.append(piece)
                del tail[:-60]
                m = step_re.match(piece)
                if m and int(m.group(2)):
                    frac = 0.02 + 0.96 * int(m.group(1)) / int(m.group(2))
                    if frac - last >= 0.01:
                        last = frac
                        _report(frac, f"step {m.group(1)}/{m.group(2)}")
        proc.wait()
        return proc.returncode, "\n".join(tail)

    code, output = await loop.run_in_executor(None, _run)
    if code != 0:
        raise RuntimeError(f"kohya exited {code}:\n{output[-3000:]}")

    produced = output_dir / f"{output_name}.safetensors"
    lora_ref, publish_note = _publish_to_comfyui(produced)
    character = store.get_character(ctx.project_id, character.id)
    if slot == "cg":
        character.assets.lora_cg = lora_ref
    else:
        character.assets.lora = lora_ref
    character.assets.trigger = trigger
    store.save_character(ctx.project_id, character)
    result = {"lora": lora_ref, "lora_path": str(produced), "trigger": trigger,
              "slot": slot, "images": len(hashes), "log_tail": output[-2000:]}
    if publish_note:
        result["note"] = publish_note
    return result


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------

@handler("tts.line")
async def synthesize_line(ctx: JobContext) -> dict[str, Any]:
    node = _require_node(ctx.payload["node_id"])
    d = node.data or {}
    if node.type != NodeType.say:
        raise ValueError("only say nodes carry voice lines")

    character = store.get_character(ctx.project_id, d.get("speaker") or "")
    if character is None:
        raise ValueError("say node has no valid speaker")
    if character.is_player or not character.voice.voiced:
        return {"skipped": "player character lines are not voiced"}

    # One take unless the caller asks for more. A first voicing is right most
    # of the time, and the unattended paths (voice all, stale sweep, fill) never
    # ask. `takes` > 1 is a re-take: someone has heard a wrong read and wants
    # several to choose between by ear. Each take rolls its own seed -- the same
    # seed hands back the same read, since TTS Audio Suite caches on it.
    takes = max(1, min(int(ctx.payload.get("takes") or 1), MAX_VOICE_TAKES))
    seeds = ([int(ctx.payload.get("seed", 0) or 0)] if takes == 1
             else [images.new_seed() for _ in range(takes)])

    node.audio_status = AssetStatus.running
    store.update_node(ctx.project_id, node)

    outs: list[dict[str, Any]] = []
    for index, seed in enumerate(seeds):
        ctx.check_cancelled()
        floor, ceiling = 0.1 + 0.8 * index / takes, 0.1 + 0.8 * (index + 1) / takes
        await ctx.progress(floor, f"voicing {character.display_name}"
                                  + (f" (take {index + 1}/{takes})" if takes > 1 else ""))
        outs.append(await tts.synthesize(
            text=d.get("text", ""), character=character,
            emotion=d.get("emotion", "neutral"), intensity=float(d.get("intensity", 0.5)),
            seed=seed, delivery=d.get("delivery", ""),
            on_progress=await _progress_bridge(ctx, floor, ceiling),
        ))

    node = _require_node(node.id)
    first = outs[0]
    data = dict(node.data or {})
    data["voice_path"] = first["voice_path"]
    data["voice_hash"] = first["hash"]
    node.data = data
    node.audio_status = AssetStatus.ready
    staleness.stamp(node, "audio", voice=staleness.voice_id(character))
    # One variant per take, so the take strip is the picker. The first take is
    # the one put on the node; the take that was there before stays a variant.
    variants = [
        generation.add_variant(
            ctx.project_id, node, "audio",
            {"voice_path": out["voice_path"], "resolved": out["resolved"], "seed": out["seed"],
             **({"take_index": index, "takes": takes} if takes > 1 else {})},
            GenMeta(seed=out["seed"], params=out["resolved"], timestamp=store.now()),
            asset_hash=out["hash"])
        for index, out in enumerate(outs)
    ]
    if len(variants) > 1:
        node.active_variant = variants[0].id
        store.update_node(ctx.project_id, node)
    return {**first, "takes": [{"hash": out["hash"], "seed": out["seed"]} for out in outs]}


@handler("tts.cast")
async def cast_voice(ctx: JobContext) -> dict[str, Any]:
    """Audition the seed clip: one line per emotion, text matched to it (doc S5).

    Timbre is pinned by the clip, so takes that differ only by sampler seed are
    near-identical and tell you nothing. Emotion is the axis that actually moves
    -- and the one that leaks timbre -- so each take renders a different emotion
    at a line written for it. Deterministic (`use_random` off) so re-auditioning
    after an `emotion_scale` / `speed` change is a clean A/B.
    """
    character = store.get_character(ctx.project_id, ctx.payload["character_id"])
    if character is None:
        raise ValueError("unknown character")

    script = tts.casting_script(
        ctx.payload.get("script"), ctx.payload.get("lines"),
        int(ctx.payload.get("takes", 0) or 0),
    )
    candidates: list[dict[str, Any]] = []

    seed_clip = await tts.stage_reference_clip(
        ctx.payload.get("seed_clip") or character.voice.reference_clip or ""
    )
    if not seed_clip:
        raise RuntimeError(
            "casting needs a seed clip -- upload one on the Voice tab first, or "
            "pass seed_clip. IndexTTS-2.5 clones a clip; it cannot synthesise a "
            "voice from the text description alone."
        )

    for i, take in enumerate(script):
        ctx.check_cancelled()
        emotion, line = take["emotion"], take["text"]
        await ctx.progress(i / max(1, len(script)),
                           f"audition {i + 1}/{len(script)}: {emotion}")
        resolved = tts.resolve_emotion(emotion, float(take["intensity"]), character)
        workflow = tts.build_workflow(
            line, seed_clip, resolved, f"cast_{character.id}_{emotion}", 0,
        )
        entry = await comfyui.run_workflow(workflow)
        clips = comfyui.collect_audio(entry)
        if not clips:
            continue
        clip = clips[0]
        data = await comfyui.view(clip["filename"], clip["subfolder"], clip["type"])
        ext = Path(clip["filename"]).suffix.lstrip(".") or "wav"
        # Paced the way a line would be: the emotion map's speed (whisper 0.85,
        # excited 1.08) is half of what makes a delivery recognisable. The
        # shipped template paces inside the model, so the raw clip already
        # carries it; `encode_speed` only stretches the preview for a template
        # that cannot.
        digest = assets.put(data, kind="voice_ref", ext=ext,
                            meta={"character": character.id, "casting": True,
                                  "emotion": emotion, "intensity": take["intensity"]})
        preview, encoded = await tts.to_ogg(digest, speed=tts.encode_speed(resolved["speed"]))
        candidates.append({
            "hash": digest, "preview_hash": preview, "encoded": encoded,
            "emotion": emotion, "intensity": take["intensity"], "line": line,
            "ext": ext, "adoptable": bool(take.get("adoptable")), "resolved": resolved,
        })

    if not candidates:
        raise RuntimeError("no candidate clips were produced -- check the TTS workflow")

    # Store the takes on the character so the Voice tab still shows them after a
    # navigation, and bin the previous run's clips -- one raw + one ogg per
    # emotion, which otherwise piles up in the gallery on every test.
    character = store.get_character(ctx.project_id, character.id) or character
    fresh = {c["hash"] for c in candidates} | {c["preview_hash"] for c in candidates}
    dropped = tts.discard_audition(character, keep=fresh)
    character.voice.audition = [
        AuditionTake(hash=c["hash"], preview_hash=c["preview_hash"], emotion=c["emotion"],
                     intensity=float(c["intensity"]), line=c["line"], ext=c["ext"])
        for c in candidates
    ]
    store.save_character(ctx.project_id, character)

    return {"candidates": candidates, "lines": [c["line"] for c in candidates],
            "line": candidates[0]["line"], "replaced": dropped}


# --------------------------------------------------------------------------
# batch + build
# --------------------------------------------------------------------------

@handler("batch.regenerate_stale")
async def regenerate_stale(ctx: JobContext) -> dict[str, Any]:
    """Run this before every build (doc S6)."""
    from .queue import enqueue
    stale = staleness.stale_nodes(ctx.project_id)
    kinds = ctx.payload.get("kinds") or ["images", "audio"]
    queued: list[str] = []
    if "images" in kinds:
        for entry in stale["images"]:
            queued.append(enqueue(ctx.project_id, "image.generate", {"node_id": entry["id"]},
                                  label="stale CG", node_id=entry["id"]))
    if "audio" in kinds:
        for entry in stale["audio"]:
            queued.append(enqueue(ctx.project_id, "tts.line", {"node_id": entry["id"]},
                                  label="stale voice", node_id=entry["id"]))
    await ctx.progress(1.0, f"queued {len(queued)} regenerations")
    return {"queued": queued, "count": len(queued),
            "images": len(stale["images"]), "audio": len(stale["audio"])}


@handler("build.export")
async def export_project(ctx: JobContext) -> dict[str, Any]:
    await ctx.progress(0.2, "compiling IR to Ren'Py")
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: build.export(ctx.project_id, ctx.payload.get("copy_assets", True))
    )
    await ctx.progress(0.9, f"{len(result['files'])} files written")
    if ctx.payload.get("launch"):
        result["launch"] = await build.launch(ctx.project_id)
    return result


@handler("build.lint")
async def lint_project(ctx: JobContext) -> dict[str, Any]:
    await ctx.progress(0.3, "running renpy lint")
    return await build.lint(ctx.project_id)


@handler("build.web")
async def web_build_project(ctx: JobContext) -> dict[str, Any]:
    """Export, then have Ren'Py emit a browser-playable (Emscripten) package.

    Slow -- Ren'Py repacks every asset -- so it is a job, not a request.
    """
    result: dict[str, Any] = {}
    if ctx.payload.get("export", True):
        await ctx.progress(0.1, "compiling IR to Ren'Py")
        result["export"] = await asyncio.get_event_loop().run_in_executor(
            None, lambda: build.export(ctx.project_id, True)
        )
    await ctx.progress(0.35, "renpy distribute --package web (several minutes)")
    web = await build.web_build(ctx.project_id)
    result.update(web)
    if not web.get("ok"):
        tail = (web.get("output") or "").strip().splitlines()[-12:]
        raise RuntimeError(
            (web.get("reason") or "web build failed")
            + ("\n\n" + "\n".join(tail) if tail else "")
        )
    await ctx.progress(1.0, "web build ready")
    return result


@handler("tags.import")
async def import_booru_tags(ctx: JobContext) -> dict[str, Any]:
    """Fetch the booru tag dictionary. A job because it is a few dozen HTTP
    round trips, not because it is heavy -- it never touches the GPU."""
    from ..services import tags as tag_svc

    await ctx.progress(0.05, "asking Danbooru for the tag list")
    result = await tag_svc.import_from_danbooru(
        min_count=int(ctx.payload.get("min_count") or tag_svc.DEFAULT_MIN_COUNT),
        on_progress=ctx.progress,
    )
    await ctx.progress(1.0, f"{result['imported']} tags cached")
    return result


@handler("batch.fill_missing")
async def fill_missing(ctx: JobContext) -> dict[str, Any]:
    """Queue every asset the project is missing -- built for an overnight run.

    Ordering matters, and on one GPU it matters twice over.

    The CG director goes first, because it is the only LM Studio work in the
    run and `services.vram` charges a model load every time the card changes
    hands: directing first means the batch swaps once (LLM, then ComfyUI for
    the rest) instead of three times. It costs nothing to move -- direction
    reads the script, not the rendered assets.

    Then backgrounds and sprites, because a CG reads better once its location
    exists and they are the cheapest frames; then the CGs that consume the
    director's output; then voice.
    """
    from .queue import enqueue

    kinds = set(ctx.payload.get("kinds")
                or ["backgrounds", "sprites", "cg_prompts", "cgs", "voice"])
    include_stale = bool(ctx.payload.get("include_stale", True))
    limit = int(ctx.payload.get("limit") or 0)

    report = await asyncio.get_event_loop().run_in_executor(
        None, lambda: batch_svc.scan(ctx.project_id, include_stale)
    )

    queued: list[str] = []
    dropped = 0

    def push(kind: str, job_kind: str, payload: dict, label: str,
             node_id: Optional[str] = None) -> None:
        nonlocal dropped
        if kind not in kinds:
            return
        if limit and len(queued) >= limit:
            dropped += 1
            return
        queued.append(enqueue(ctx.project_id, job_kind, payload, label=label, node_id=node_id))

    # The whole LLM block, up front and contiguous, so the run costs one model
    # swap instead of three. The worker claims GPU work oldest-first and one at
    # a time (created_at, rowid), so every director job here really does run
    # ahead of every render below it.
    #
    # Both ends stay idempotent anyway: `skip_if_set` stops the director
    # redoing a node the render already distilled, and `distill_if_missing`
    # stops a render shooting a node whose direction never landed. That covers
    # the cases queue order cannot -- a director job that failed or was
    # cancelled, and `gpu_serialize` turned off, which drops the one-at-a-time
    # guarantee along with the lock. Either order: directed once, rendered once.
    for item in report["cg_prompts"]:
        push("cg_prompts", "image.cg_prompt",
             {"node_id": item["node_id"], "skip_if_set": True},
             "direct CG shot", node_id=item["node_id"])
    if "cg_prompts" in kinds:
        await ctx.progress(0.1, f"{len(queued)} CG shots to direct")

    for item in report["backgrounds"]:
        push("backgrounds", "image.background",
             {"location_id": item["location_id"], "variant": item["variant"]},
             f"bg {item['location_id']}/{item['variant']}")
    await ctx.progress(0.3, f"{len(queued)} queued")

    for item in report["sprites"]:
        push("sprites", "image.sprite",
             {"character_id": item["character_id"], "outfit_id": item["outfit_id"],
              "expression": item["expression"], "slot": item["slot"]},
             f"sprite {item['character_id']}/{item['slot']}")
    await ctx.progress(0.5, f"{len(queued)} queued")

    for item in report["cgs"]:
        # `distill_if_missing` runs the director inside the render job for a
        # node that has never been through it -- otherwise an overnight batch
        # renders every CG from the segmenter's rough shot hint, which is the
        # problem the director exists to fix. It is one job rather than two so
        # the pair can't be split by `limit` or reordered by a timestamp tie,
        # and it never touches a node that already has a prompt: a batch must
        # not overwrite your edits.
        push("cgs", "image.generate",
             {"node_id": item["node_id"], "distill_if_missing": True},
             "event CG", node_id=item["node_id"])
    await ctx.progress(0.75, f"{len(queued)} queued")

    for item in report["voice"]:
        push("voice", "tts.line", {"node_id": item["node_id"], "seed": 0},
             f"voice {item['speaker']}", node_id=item["node_id"])

    await ctx.progress(1.0, f"queued {len(queued)} jobs")
    return {
        "queued": len(queued),
        "job_ids": queued[:500],
        "counts": report["counts"],
        "blocked": report["blocked"],
        "estimate_seconds": report["estimate_seconds"],
        # What the cap actually cost, rather than `total > limit`: with kinds
        # deselected the run can be well under the cap while the project as a
        # whole is over it, and reporting that as truncated sent you looking
        # for work that was never asked for.
        "truncated_by_limit": bool(limit and dropped),
        "dropped_by_limit": dropped,
    }
