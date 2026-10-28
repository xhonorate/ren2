"""Roleplay mode: conversations, the assembled prompt, and the safety net.

The assertions that matter most here are about the *shape* of what goes to the
model, not about the text of any template. A conversation reply is one
`chat_stream` call whose message list is the whole of the contract:

* one system message, then strictly alternating user/assistant,
* ending on a user message,
* with the turn directive on that last user message and nowhere else.

Every one of those is what makes "never write the player's lines" the model's
default rather than something a regex has to win. A template edit is allowed to
change the words; nothing is allowed to change that shape, so it is pinned.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def client():
    tmp = tempfile.mkdtemp(prefix="vn_rp_test_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def project(client):
    pid = client.post("/api/projects", json={"name": "RP"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={
        "id": "alice", "display_name": "Alice", "age": 24})
    alice = client.get(f"/api/projects/{pid}/characters").json()[0]
    alice["personality"]["summary"] = "Blunt, warm underneath."
    alice["personality"]["speech_style"] = "Short sentences. Never explains herself."
    client.put(f"/api/projects/{pid}/characters/alice", json=alice)
    client.post(f"/api/projects/{pid}/characters", json={
        "id": "bea", "display_name": "Bea", "age": 27})
    client.post(f"/api/projects/{pid}/characters", json={
        "id": "me", "display_name": "Sam", "age": 25, "is_player": True})
    return pid


def new_conv(client, pid, **kw):
    body = {"character_id": "alice", **kw}
    r = client.post(f"/api/projects/{pid}/rp/conversations", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def add(client, pid, cid, role, text):
    """Write one message directly, so a transcript can be built without a model."""
    from app import store
    from app.models import RPMessage
    message = RPMessage(id=store.new_id("m_"), conv_id=cid, seq=store.next_seq(cid),
                        role=role, text=text, created_at=store.now())
    store.insert_message(message)
    return message


# --------------------------------------------------------------------------
# the cast list
# --------------------------------------------------------------------------

def test_cast_excludes_the_player_and_resolves_the_persona(client, project):
    out = client.get(f"/api/projects/{project}/rp/characters").json()
    ids = [c["id"] for c in out["characters"]]
    # You cannot have a conversation with yourself.
    assert "me" not in ids
    assert set(ids) == {"alice", "bea"}
    # With no persona saved, it falls back to the player character -- so a
    # project that already has one needs no setup at all.
    assert out["persona"]["name"] == "Sam"
    assert out["player"]["id"] == "me"


def test_persona_overrides_the_fallback_but_keeps_it_visible(client, project):
    client.put(f"/api/projects/{project}/rp/persona", json={
        "name": "Noor", "description": "", "notes": "works nights"})
    out = client.get(f"/api/projects/{project}/rp/persona").json()
    # What is stored stays exactly what was typed, so a blank field shows blank.
    assert out["persona"]["name"] == "Noor"
    assert out["persona"]["description"] == ""
    # What a prompt would use has the fallback applied.
    assert out["resolved"]["name"] == "Noor"
    client.put(f"/api/projects/{project}/rp/persona",
               json={"name": "", "description": "", "notes": ""})
    assert client.get(f"/api/projects/{project}/rp/persona").json()[
        "resolved"]["name"] == "Sam"


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------

def test_conversation_appears_in_every_cast_members_history(client, project):
    conv = new_conv(client, project, cast=["bea"], scene_context="The argument.")
    for who in ("alice", "bea"):
        ids = [c["id"] for c in client.get(
            f"/api/projects/{project}/rp/conversations?character_id={who}"
        ).json()["conversations"]]
        assert conv["id"] in ids, f"missing from {who}'s history"
    client.delete(f"/api/projects/{project}/rp/conversations/{conv['id']}")


def test_untitled_conversation_is_named_after_its_context(client, project):
    conv = new_conv(client, project, scene_context="First dinner at her flat.")
    assert conv["untitled"] is True
    assert conv["title"] == "First dinner at her flat"
    client.patch(f"/api/projects/{project}/rp/conversations/{conv['id']}",
                 json={"title": "Dinner"})
    named = client.get(
        f"/api/projects/{project}/rp/conversations/{conv['id']}").json()
    assert (named["title"], named["untitled"]) == ("Dinner", False)
    client.delete(f"/api/projects/{project}/rp/conversations/{conv['id']}")


def test_editing_a_summary_protects_it_from_being_rewritten(client, project):
    conv = new_conv(client, project)
    client.patch(f"/api/projects/{project}/rp/conversations/{conv['id']}",
                 json={"summary": "They argued about the car."})
    out = client.get(f"/api/projects/{project}/rp/conversations/{conv['id']}").json()
    # The flag is what stops ending the scene again from overwriting the
    # author's words with a fresh distillation.
    assert out["summary_edited"] is True
    client.delete(f"/api/projects/{project}/rp/conversations/{conv['id']}")


def test_deleting_a_conversation_takes_its_messages(client, project):
    from app import store
    conv = new_conv(client, project)
    add(client, project, conv["id"], "player", "Hey.")
    assert store.list_messages(conv["id"])
    client.delete(f"/api/projects/{project}/rp/conversations/{conv['id']}")
    assert store.list_messages(conv["id"]) == []


def test_reorder_rewrites_the_whole_list(client, project):
    """Order is what decides which summaries a later scene remembers, so a drag
    has to change what 'earlier' means for every scene after it -- not just the
    one that moved."""
    first = new_conv(client, project, title="one")
    second = new_conv(client, project, title="two")
    third = new_conv(client, project, title="three")
    ids = [third["id"], first["id"], second["id"]]
    assert client.post(f"/api/projects/{project}/rp/conversations/reorder",
                       json={"ids": ids}).json()["reordered"] == 3
    got = [c["id"] for c in client.get(
        f"/api/projects/{project}/rp/conversations?character_id=alice"
    ).json()["conversations"]]
    assert got == ids
    for cid in ids:
        client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


# --------------------------------------------------------------------------
# the assembled prompt -- the contract
# --------------------------------------------------------------------------

def test_prompt_is_one_system_then_strict_alternation_ending_on_the_player(client, project):
    conv = new_conv(client, project, scene_context="First dinner.")
    cid = conv["id"]
    add(client, project, cid, "cast", 'Alice (smirking, amused): "You\'re early."')
    add(client, project, cid, "player", "Traffic was kind.")
    add(client, project, cid, "cast", 'Alice (neutral, cold): "Sure it was."')
    add(client, project, cid, "player", "*sits down*")

    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    roles = [m["role"] for m in out["messages"]]

    assert roles[0] == "system"
    assert roles.count("system") == 1
    assert roles[-1] == "user", "the list must end on the player's turn"
    body = roles[1:]
    assert body[0] == "user", "a transcript may not open mid-reply"
    for a, b in zip(body, body[1:]):
        assert a != b, f"alternation broken: {roles}"
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_directive_rides_on_the_last_user_turn_only(client, project):
    """Last position is where a local model actually obeys, and appending rather
    than adding a message is what keeps the alternation Mistral templates need."""
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "cast", 'Alice (neutral, neutral): "Well?"')
    add(client, project, cid, "player", "Nothing.")

    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    directive = out["directive"]
    assert directive
    last = out["messages"][-1]["content"]
    assert last.endswith(directive)
    assert "Nothing." in last, "the player's own words must survive alongside it"
    # Nowhere else -- not in the system block, not on an earlier turn.
    assert directive not in out["system"]
    assert not any(directive in m["content"] for m in out["messages"][:-1])
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_an_empty_turn_is_a_silence_not_an_unanswered_message(client, project):
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "cast", 'Alice (neutral, neutral): "Say something."')

    # No player row was written, so the last message is the cast's.
    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    assert out["silent"] is True
    assert out["messages"][-1]["role"] == "user"

    # With a message of theirs pending, the same conversation is not a silence:
    # it is simply a turn that has not been answered yet.
    add(client, project, cid, "player", "Fine.")
    again = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    assert again["silent"] is False
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_a_fresh_conversation_is_an_opening(client, project):
    conv = new_conv(client, project, scene_context="She has just got in.")
    out = client.get(
        f"/api/projects/{project}/rp/conversations/{conv['id']}/preview").json()
    assert out["opening"] is True
    # One system message and one user message: there is nothing to alternate yet.
    assert [m["role"] for m in out["messages"]] == ["system", "user"]
    assert out["cast_names"] == "Alice"
    client.delete(f"/api/projects/{project}/rp/conversations/{conv['id']}")


def test_bracketed_steer_is_pulled_out_of_the_spoken_line(client, project):
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "cast", 'Alice (neutral, neutral): "Hm."')
    add(client, project, cid, "player", "Alright. [she should not let this go]")

    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    assert out["steer"] == "she should not let this go"
    last = out["messages"][-1]["content"]
    # The direction reaches the model as a direction, not as something said.
    assert "[she should not let this go]" not in last.split("[Write")[0]
    assert "Alright." in last
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_the_players_asterisk_action_stays_with_their_words(client, project):
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "cast", 'Alice (neutral, neutral): "Hm."')
    add(client, project, cid, "player", "Fine. *drops into the chair*")
    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    # Interleaved on purpose: "Fine. *sits*" is one gesture, not a line plus a
    # stage direction, and the model reads the convention natively.
    assert "Fine. *drops into the chair*" in out["messages"][-1]["content"]
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_the_cast_card_and_its_speech_line_reach_the_prompt(client, project):
    conv = new_conv(client, project)
    out = client.get(
        f"/api/projects/{project}/rp/conversations/{conv['id']}/preview").json()
    system = out["system"]
    assert "Alice" in system
    assert "Short sentences. Never explains herself." in system
    # A character who is not in the scene gets one line, not a card -- enough to
    # keep the model from inventing facts if somebody mentions them.
    assert "Bea" in system
    assert "Blunt, warm underneath." in system
    client.delete(f"/api/projects/{project}/rp/conversations/{conv['id']}")


def test_trimming_drops_the_oldest_turns_and_keeps_the_last_four(client, project):
    from app import store
    from app.services import roleplay

    conv = new_conv(client, project)
    cid = conv["id"]
    for i in range(30):
        add(client, project, cid, "cast",
            f'Alice (neutral, neutral): "{"line " * 60}{i}"')
        add(client, project, cid, "player", f"{'word ' * 60}{i}")

    store.set_setting("rp_history_budget", 400)
    try:
        out = roleplay.assemble_turn(project, store.get_conversation(cid))
    finally:
        store.set_setting("rp_history_budget", 5000)

    assert out["dropped_turns"] > 0, "a 60-turn transcript must not fit in 400 tokens"
    kept = [m for m in out["messages"] if m["role"] != "system"]
    assert len(kept) >= roleplay.FLOOR_TURNS
    # What survives is the END of the conversation -- the model must be able to
    # see what it is replying to.
    assert "29" in kept[-1]["content"]
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_memory_is_earlier_summaries_and_hiding_one_removes_it(client, project):
    first = new_conv(client, project, title="The argument")
    client.patch(f"/api/projects/{project}/rp/conversations/{first['id']}",
                 json={"summary": "They argued about the car and did not resolve it."})
    second = new_conv(client, project, title="After")

    out = client.get(
        f"/api/projects/{project}/rp/conversations/{second['id']}/preview").json()
    assert "argued about the car" in out["system"]

    # Hidden keeps the scene readable but takes it out of continuity.
    client.patch(f"/api/projects/{project}/rp/conversations/{first['id']}",
                 json={"hidden": True})
    hidden = client.get(
        f"/api/projects/{project}/rp/conversations/{second['id']}/preview").json()
    assert "argued about the car" not in hidden["system"]

    # And a scene never remembers itself, or anything after it.
    own = client.get(
        f"/api/projects/{project}/rp/conversations/{first['id']}/preview").json()
    assert "argued about the car" not in own["system"]
    for cid in (first["id"], second["id"]):
        client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


# --------------------------------------------------------------------------
# messages: takes, edits, rewind
# --------------------------------------------------------------------------

def test_rewind_deletes_from_a_message_onward(client, project):
    from app import store
    conv = new_conv(client, project)
    cid = conv["id"]
    a = add(client, project, cid, "cast", "one")
    b = add(client, project, cid, "player", "two")
    add(client, project, cid, "cast", "three")

    out = client.delete(f"/api/projects/{project}/rp/messages/{b.id}?rewind=true").json()
    assert len(out["deleted"]) == 2
    remaining = [m.id for m in store.list_messages(cid)]
    assert remaining == [a.id]
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_switching_take_switches_the_text_and_drops_stale_audio(client, project):
    from app import store
    from app.models import RPTake, RPVoiceClip

    conv = new_conv(client, project)
    cid = conv["id"]
    message = add(client, project, cid, "cast", "first take")
    message.takes = [RPTake(id="k1", text="first take"), RPTake(id="k2", text="second take")]
    message.active_take = "k1"
    message.voice = [RPVoiceClip(line=0, character_id="alice", hash="deadbeef")]
    store.save_message(message)

    out = client.patch(f"/api/projects/{project}/rp/messages/{message.id}",
                       json={"active_take": "k2"}).json()
    assert out["text"] == "second take"
    # The clips on file speak the other take's words, so they cannot be kept.
    assert out["voice"] == []
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_editing_a_reply_updates_the_take_it_is_showing(client, project):
    """Otherwise arrowing away from an edited take and back would silently
    restore the text the author just replaced."""
    from app import store
    from app.models import RPTake

    conv = new_conv(client, project)
    cid = conv["id"]
    message = add(client, project, cid, "cast", "as written")
    message.takes = [RPTake(id="k1", text="as written")]
    message.active_take = "k1"
    store.save_message(message)

    client.patch(f"/api/projects/{project}/rp/messages/{message.id}",
                 json={"text": "as edited"})
    stored = store.get_message(message.id)
    assert stored.text == "as edited"
    assert stored.takes[0].text == "as edited"
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_regenerate_drops_what_followed_and_keeps_the_reply_itself(client, project):
    from app import store
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "player", "Hey.")
    reply = add(client, project, cid, "cast", 'Alice (neutral, neutral): "Hey."')
    add(client, project, cid, "player", "How was it?")
    add(client, project, cid, "cast", 'Alice (neutral, tired): "Long."')

    out = client.post(f"/api/projects/{project}/rp/conversations/{cid}/regenerate",
                      json={"message_id": reply.id}).json()
    assert len(out["dropped"]) == 2
    # The reply being redone survives: the new text lands on it as another take,
    # so the one that was showing is never destroyed.
    assert reply.id in [m.id for m in store.list_messages(cid)]
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")
    store.get_conversation(cid)


def test_only_a_reply_can_be_regenerated(client, project):
    conv = new_conv(client, project)
    cid = conv["id"]
    mine = add(client, project, cid, "player", "Hey.")
    r = client.post(f"/api/projects/{project}/rp/conversations/{cid}/regenerate",
                    json={"message_id": mine.id})
    assert r.status_code == 409
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_taking_a_turn_reopens_an_ended_scene(client, project):
    """An ended scene is ended because it was summarized, and adding to it makes
    that summary stale."""
    conv = new_conv(client, project)
    cid = conv["id"]
    client.patch(f"/api/projects/{project}/rp/conversations/{cid}",
                 json={"state": "ended"})
    client.post(f"/api/projects/{project}/rp/conversations/{cid}/turn",
                json={"text": "One more thing."})
    assert client.get(
        f"/api/projects/{project}/rp/conversations/{cid}").json()["state"] == "open"
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_an_empty_turn_writes_no_message(client, project):
    from app import store
    conv = new_conv(client, project)
    cid = conv["id"]
    out = client.post(f"/api/projects/{project}/rp/conversations/{cid}/turn",
                      json={"text": "   "}).json()
    assert out["message"] is None
    assert store.list_messages(cid) == []
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


# --------------------------------------------------------------------------
# the safety net
# --------------------------------------------------------------------------

def test_turn_end_cuts_a_reply_that_writes_for_the_player():
    from app.models import Character, Persona
    from app.services import roleplay

    cast = [Character(id="alice", display_name="Alice"),
            Character(id="me", display_name="Sam", is_player=True)]
    names = roleplay.player_names(Persona(name="Sam"), cast)

    reply = ('Alice (smirking, amused): "You came."\n'
             'Sam: "I said I would."\n'
             'Alice (neutral, neutral): "You say a lot of things."')
    assert roleplay.trim_reply(reply, names) == 'Alice (smirking, amused): "You came."'

    # `You:` too -- the house style examples write the player that way, and a
    # writer model copies examples harder than it follows rules.
    assert roleplay.trim_reply('Alice (neutral, neutral): "Hm."\nYou: "Fine."',
                               names) == 'Alice (neutral, neutral): "Hm."'
    # A scene generation's closing blocks have no place in a conversation.
    assert roleplay.trim_reply('Alice (neutral, neutral): "Hm."\nCHOICE: what now',
                               names) == 'Alice (neutral, neutral): "Hm."'
    # And a reply that stays on the cast's side is left exactly alone.
    clean = 'Alice (neutral, neutral): "Hm."\nShe puts the kettle on.'
    assert roleplay.trim_reply(clean, names) == clean


def test_a_character_actually_called_you_keeps_their_name():
    """The net must not silently truncate every reply she speaks first in."""
    from app.models import Character, Persona
    from app.services import roleplay

    cast = [Character(id="you_chan", display_name="You"),
            Character(id="me", display_name="Sam", is_player=True)]
    names = roleplay.player_names(Persona(name="Sam"), cast)
    assert "you" not in names
    line = 'You (neutral, neutral): "That is my name."'
    assert roleplay.trim_reply(line, names) == line


def test_split_player_text_separates_speech_action_and_steer():
    from app.services import roleplay

    out = roleplay.split_player_text("Fine. *drops into the chair* [she should push]")
    assert out["spoken"] == "Fine. *drops into the chair*"
    assert out["actions"] == ["drops into the chair"]
    assert out["steer"] == "she should push"

    # A message that is only a direction is still a silence as far as speech
    # goes, so the turn carries on without them.
    only = roleplay.split_player_text("[the scene gets very tense]")
    assert only["spoken"] == ""
    assert only["steer"] == "the scene gets very tense"


def test_a_scene_that_opens_on_a_reply_keeps_that_reply(client, project):
    """The regression that mattered most.

    A scene started from a context line begins with the cast opening it, and
    there is no player message in front of that. Dropping the leading assistant
    turn to restore alternation threw the opening of the scene away -- and the
    next reply then re-established the room the scene had already established,
    and the one after that restated the whole conversation from the top, because
    as far as the model could see it had begun in mid-air.
    """
    from app.services import roleplay

    conv = new_conv(client, project, scene_context="He comes by the shop at closing.")
    cid = conv["id"]
    add(client, project, cid, "cast", "The lights are already off when you get there.")
    add(client, project, cid, "player", "Just walking.")
    add(client, project, cid, "cast", 'Alice (neutral, tired): "Closing."')
    add(client, project, cid, "player", "I know.")

    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    joined = " ".join(m["content"] for m in out["messages"])
    assert "The lights are already off" in joined, "the scene's opening was dropped"

    # Alternation is kept by standing a synthetic opener in front of it, not by
    # throwing it away.
    roles = [m["role"] for m in out["messages"]]
    assert roles[1] == "user"
    assert out["messages"][1]["content"] == roleplay.OPENING_TURN
    for a, b in zip(roles[1:], roles[2:]):
        assert a != b
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_an_old_steer_does_not_come_back_as_something_the_player_said(client, project):
    """A bracketed direction was a note to the writer -- said by nobody, heard by
    nobody. Replayed verbatim in the transcript it reads as an announcement, and
    the model starts answering it."""
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "cast", 'Alice (neutral, neutral): "Well?"')
    add(client, project, cid, "player", "Fine. [she should push him on it]")
    add(client, project, cid, "cast", 'Alice (glaring, irritated): "Fine what?"')
    add(client, project, cid, "player", "Nothing.")

    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    history = " ".join(m["content"] for m in out["messages"][:-1])
    assert "she should push him on it" not in history
    assert "Fine." in history, "their actual words must survive the strip"
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_silences_do_not_break_alternation(client, project):
    """Two silent turns in a row leave two cast messages with no player message
    between them -- the normal case, not an edge case."""
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "cast", "one")
    add(client, project, cid, "player", "two")
    add(client, project, cid, "cast", "three")
    add(client, project, cid, "cast", "four")       # a silence
    add(client, project, cid, "cast", "five")       # and another
    add(client, project, cid, "player", "six")

    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    roles = [m["role"] for m in out["messages"]][1:]
    for a, b in zip(roles, roles[1:]):
        assert a != b
    joined = " ".join(m["content"] for m in out["messages"])
    for word in ("one", "three", "four", "five"):
        assert word in joined, f"the merge dropped {word!r}"
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


def test_consecutive_same_role_messages_are_merged_not_sent_as_is(client, project):
    """A chat template that requires alternation errors on the whole request, so
    a pair left behind by a rewind or a hand edit cannot be passed through."""
    conv = new_conv(client, project)
    cid = conv["id"]
    add(client, project, cid, "cast", "one")
    add(client, project, cid, "cast", "two")
    add(client, project, cid, "player", "three")

    out = client.get(f"/api/projects/{project}/rp/conversations/{cid}/preview").json()
    roles = [m["role"] for m in out["messages"]][1:]
    for a, b in zip(roles, roles[1:]):
        assert a != b
    client.delete(f"/api/projects/{project}/rp/conversations/{cid}")


# --------------------------------------------------------------------------
# the regurgitation guard
# --------------------------------------------------------------------------

def test_echoes_catches_a_reply_that_restates_the_last_one():
    from app.services import roleplay

    previous = ("6:47 PM, and the shop is already dark inside except for the light "
                "behind the counter where Alice is closing out.\n\n"
                'Alice (neutral, tired): "You are early."')
    # The observed failure: the reply opens by reproducing the previous one and
    # only then drifts into new material, so only the prefix is compared.
    repeat = previous + "\n\nShe puts the till roll down and looks at you properly."
    assert roleplay.echoes(repeat, previous)
    # Re-wrapping and re-casing are not a defence: the comparison is on
    # alphanumerics only, so a reflowed copy still reads as the same passage.
    reflowed = " ".join(previous.upper().split()) + "\n\nShe looks up."
    assert roleplay.echoes(reflowed, previous)


def test_echoes_does_not_fire_on_two_replies_that_merely_start_alike():
    """Two replies opening on the same short line happens legitimately -- a
    character who answers "Well?" twice in a scene is not a bug."""
    from app.services import roleplay

    assert not roleplay.echoes('Alice (neutral, neutral): "Well?"',
                               'Alice (neutral, neutral): "Well?"')
    a = "She puts the kettle on and stands there with her back to you, waiting."
    b = "She puts the kettle on and then turns round, and she is not smiling now."
    assert not roleplay.echoes(a, b)


def test_invented_turn_markers_end_the_reply():
    """A model writing `Human:` has stopped answering and started transcribing;
    everything after it is a replay of what it can already see."""
    from app.models import Character, Persona
    from app.services import roleplay

    cast = [Character(id="alice", display_name="Alice"),
            Character(id="me", display_name="Sam", is_player=True)]
    names = roleplay.player_names(Persona(name="Sam"), cast)

    reply = ('Alice (smirking, amused): "Wasn\'t open yesterday."\n'
             "Human: Nothing. Just walking.\n"
             'AI: Alice (neutral, tired): "Need something."')
    assert roleplay.trim_reply(reply, names) == \
        'Alice (smirking, amused): "Wasn\'t open yesterday."'


def test_strip_echo_keeps_the_new_material_behind_a_repeat():
    from app.services import roleplay

    previous = ('Alice (neutral, tired): "We close in ten minutes."\n'
                "\n"
                "She finishes sorting a stack of twenties and starts on another.\n"
                "\n"
                "There are no other customers.")
    reply = previous + "\n\nShe looks up properly this time, and waits."
    assert roleplay.strip_echo(reply, previous) == \
        "She looks up properly this time, and waits."


def test_strip_echo_leaves_a_reply_that_merely_opens_alike():
    """One shared opening line is a character saying the same thing twice, which
    people do. Deleting half the reply over it would be worse than keeping it."""
    from app.services import roleplay

    previous = ('Alice (neutral, tired): "We close in ten minutes."\n'
                "\n"
                "She goes back to the till.")
    reply = ('Alice (neutral, tired): "We close in ten minutes."\n'
             "\n"
             "This time she says it looking straight at you.")
    assert roleplay.strip_echo(reply, previous) == reply


def test_clean_reply_drops_bracket_only_lines():
    """Two real leaks, both bracket-only lines and both format violations: the
    model handing the directive back, and the model writing the player's side as
    a stage direction."""
    from app.models import Character, Persona
    from app.services import roleplay

    cast = [Character(id="alice", display_name="Alice"),
            Character(id="me", display_name="Sam", is_player=True)]
    names = roleplay.player_names(Persona(name="Sam"), cast)

    # The synthetic opening turn, handed straight back.
    assert roleplay.clean_reply(
        '[The scene begins.]\n\nAlice (smirking, amused): "You came."', names
    ) == 'Alice (smirking, amused): "You came."'

    # The player narrated in brackets -- the thing the prompt most wants gone.
    assert roleplay.clean_reply(
        "[You are sitting on a stool by the counter, nursing a coffee.]\n"
        "\n"
        'Alice (neutral, tired): "We close in ten."', names
    ) == 'Alice (neutral, tired): "We close in ten."'

    # Brackets inside a line are left alone: only a line that is nothing but an
    # aside is a violation.
    kept = 'Alice (neutral, neutral): "It was [redacted], apparently."'
    assert roleplay.clean_reply(kept, names) == kept


def test_clean_reply_does_not_leave_holes_where_a_line_was():
    from app.models import Character, Persona
    from app.services import roleplay

    names = roleplay.player_names(
        Persona(name="Sam"), [Character(id="me", display_name="Sam", is_player=True)])
    out = roleplay.clean_reply(
        "[aside]\n\n\nShe puts the kettle on.\n\n[another aside]\n\nShe waits.\n\n",
        names)
    assert out == "She puts the kettle on.\n\nShe waits."


def test_strip_prompt_echo_drops_a_recited_character_card():
    """The opening-turn failure: with no assistant message to continue, the model
    continues the prompt instead and recites the cast list."""
    from app.models import Character, Persona
    from app.services import roleplay

    system = ("You are running a scene.\n\n### Alice (id: alice)\n26, female.\n"
              "Who they are: Runs the hardware shop her father left her. Competent, "
              "dry, tired.\nSPEECH -- write every one of their lines in this voice: "
              "Short declaratives.\n")
    names = roleplay.player_names(
        Persona(name="Sam"), [Character(id="me", display_name="Sam", is_player=True)])

    reply = ("26, female.\n"
             "Who they are: Runs the hardware shop her father left her. Competent, "
             "dry, tired.\n"
             "\n"
             'Alice (neutral, flat): "Sam."')
    assert roleplay.clean_reply(reply, names, system) == 'Alice (neutral, flat): "Sam."'


def test_strip_prompt_echo_keeps_a_line_that_merely_shares_words():
    """Only leading lines, and only ones long enough that the match cannot be
    chance -- a short line of dialogue can legitimately appear in a card."""
    from app.models import Character, Persona
    from app.services import roleplay

    system = 'Alice runs the hardware shop. SPEECH: short declaratives.'
    names = roleplay.player_names(
        Persona(name="Sam"), [Character(id="me", display_name="Sam", is_player=True)])

    # Short, so not treated as a copy even though the words are in the prompt.
    short = 'Alice (neutral, flat): "Short declaratives."'
    assert roleplay.clean_reply(short, names, system) == short

    # And a long line that is NOT in the prompt survives untouched.
    real = ("She puts the book down on the counter and does not mark her place, "
            "which is how you know she was not reading it.")
    assert roleplay.clean_reply(real, names, system) == real


def test_strip_prompt_echo_only_strips_from_the_front():
    """A card line quoted mid-reply is somebody reading it aloud, or a
    coincidence. Only a recitation the reply OPENS with is the known failure."""
    from app.models import Character, Persona
    from app.services import roleplay

    system = "Who they are: Runs the hardware shop her father left her, competently."
    names = roleplay.player_names(
        Persona(name="Sam"), [Character(id="me", display_name="Sam", is_player=True)])
    reply = ('Alice (neutral, flat): "Sam."\n'
             "Who they are: Runs the hardware shop her father left her, competently.")
    assert roleplay.clean_reply(reply, names, system) == reply


# --------------------------------------------------------------------------
# giving a speaker back to dialogue that lost its name
# --------------------------------------------------------------------------

def _names_for():
    from app.models import Character, Persona
    from app.services import roleplay
    return roleplay.player_names(
        Persona(name="Sam"), [Character(id="alice", display_name="Alice"),
                              Character(id="me", display_name="Sam", is_player=True)])


def test_orphan_line_that_kept_its_bracket_gets_the_name_back():
    """The commonest recoverable shape: the model kept the performance bracket and
    dropped only the name."""
    from app.services import roleplay

    reply = ('Alice (frowning, thinking, 0.4): "Walking."\n'
             '(thinking, quieter): "Or did you come here to do something."')
    assert roleplay.clean_reply(reply, _names_for()) == (
        'Alice (frowning, thinking, 0.4): "Walking."\n'
        'Alice (thinking, quieter): "Or did you come here to do something."')


def test_bare_orphan_line_inherits_the_speakers_last_bracket():
    """Their previous bracket rather than `neutral`: it is the same person
    carrying on, and defaulting to neutral would flatten the voice."""
    from app.services import roleplay

    reply = ('Alice (looking_away, anxious, 0.5): "Can I ask you something."\n'
             '"Bea said you went out to the harbor."')
    assert roleplay.clean_reply(reply, _names_for()) == (
        'Alice (looking_away, anxious, 0.5): "Can I ask you something."\n'
        'Alice (looking_away, anxious, 0.5): "Bea said you went out to the harbor."')


def test_an_orphan_before_any_speaker_is_left_alone():
    """With two characters on stage there is nothing to infer from, and guessing
    puts words in the wrong mouth."""
    from app.services import roleplay

    reply = '"Bea said you went out to the harbor."\n\nShe does not look up.'
    assert roleplay.clean_reply(reply, _names_for()) == reply


def test_a_line_with_two_quoted_spans_is_left_alone():
    """Dialogue with narration wedged between it. Splitting that correctly is
    guesswork, so it is not attempted."""
    from app.services import roleplay

    reply = ('Alice (neutral, tired): "We close in ten."\n'
             '"You still come by at six." She tilts her head. "Thought you were more '
             'regular about it."')
    assert roleplay.clean_reply(reply, _names_for()) == reply


def test_narration_is_never_mistaken_for_orphan_dialogue():
    from app.services import roleplay

    reply = ('Alice (neutral, tired): "We close in ten."\n'
             "She goes back to squaring up the notes in the till drawer.")
    assert roleplay.clean_reply(reply, _names_for()) == reply


# --------------------------------------------------------------------------
# port to a canvas scene
# --------------------------------------------------------------------------
#
# The port is mostly code, and this is where that pays off: everything below
# asserts on a pure function, with no model anywhere near it. The one LLM call
# on the default path is the structuring pass, which has its own tests.

def test_port_keeps_the_order_the_player_typed():
    from app.services import roleplay
    # An action written in front of a line is a thing done and then a thing
    # said. Aggregating the two (which is right for a prompt) would reverse it.
    assert roleplay.port_player("*leans in* What did you say?") == [
        "You lean in.", 'You: "What did you say?"']
    assert roleplay.port_player("Fine. *drops into the chair*") == [
        'You: "Fine."', "You drop into the chair."]


def test_port_drops_steer_notes_entirely():
    from app.services import roleplay
    # Nobody said it and nobody heard it, so there is no node it could become.
    out = roleplay.port_player("Fine. [she should not let this go] Really.")
    assert out == ['You: "Fine. Really."']
    assert "let this go" not in " ".join(out)


def test_port_conjugates_an_action_into_second_person():
    from app.services import roleplay
    assert roleplay.port_player("*sighs*") == ["You sigh."]
    assert roleplay.port_player("*watches her*") == ["You watch her."]
    assert roleplay.port_player("*tries the handle*") == ["You try the handle."]
    assert roleplay.port_player("*goes to the window*") == ["You go to the window."]
    assert roleplay.port_player("*is already standing*") == ["You are already standing."]
    assert roleplay.port_player("*I lean in*") == ["You lean in."]
    # Not a verb phrase at all -- left as written rather than mangled into
    # "You a long pause." `rp.to_scene` tidies these when it is enabled.
    assert roleplay.port_player("*a long pause*") == ["A long pause."]


def test_port_replaces_the_persona_name_with_the_player_tag(client, project):
    from app.models import Persona
    from app.services import roleplay
    conv = new_conv(client, project)
    add(client, project, conv["id"], "cast",
        'Alice (smirking, amused, 0.4): "You made it, Sam."')
    messages = _messages_of(conv["id"])
    prose = roleplay.port_prose(messages, Persona(name="Sam"))
    # Ren'Py substitutes the tag at runtime; a hardcoded name calls the reader
    # by somebody else's.
    assert "{player}" in prose and "Sam" not in prose


def test_port_leaves_a_pronoun_persona_alone(client, project):
    from app.models import Persona
    from app.services import roleplay
    conv = new_conv(client, project)
    add(client, project, conv["id"], "cast", "She looks at you and waits.")
    prose = roleplay.port_prose(_messages_of(conv["id"]), Persona(name="You"))
    # A persona literally called "You" would otherwise turn every line of
    # second-person narration into `{player}`.
    assert prose == "She looks at you and waits."


def test_port_keeps_cast_lines_in_house_notation(client, project):
    from app.models import Persona
    from app.services import generation, roleplay
    conv = new_conv(client, project)
    add(client, project, conv["id"], "cast",
        'Alice (smirking, amused, 0.6): "Ten minutes."\nShe locks the till.')
    add(client, project, conv["id"], "player", "*nods* Sure.")
    prose = roleplay.port_prose(_messages_of(conv["id"]), Persona(name="Sam"))
    # The whole reason cast lines kept the performance bracket: the existing
    # extractor parses the transcript with no rewrite and no second model.
    clean, beats = generation.extract_performance(prose)
    assert [b["kind"] for b in beats] == ["say", "narration", "narration", "say"]
    assert beats[0]["emotion"] == "amused" and beats[0]["intensity"] == 0.6
    assert beats[0]["text"] == "Ten minutes."
    assert beats[2]["text"] == "You nod."
    assert beats[3]["text"] == "Sure."


def test_port_rescues_a_reply_that_wrote_the_players_line(client, project):
    from app.models import Persona
    from app.services import roleplay
    conv = new_conv(client, project)
    add(client, project, conv["id"], "cast",
        'Alice (neutral, cold, 0.3): "Well?"\nSam (smiling, warm, 0.5): "I came anyway."')
    prose = roleplay.port_prose(_messages_of(conv["id"]), Persona(name="Sam"))
    # turn_end normally stops this ever being stored. If one gets through, it
    # ports as the player speaking -- stripped of the bracket, because they
    # have no sprite to wear it -- not as a character who does not exist.
    assert 'You: "I came anyway."' in prose
    assert "smiling, warm" not in prose


def test_port_drops_a_bracket_only_line(client, project):
    from app.models import Persona
    from app.services import roleplay
    conv = new_conv(client, project)
    add(client, project, conv["id"], "cast",
        '[The scene begins.]\nAlice (neutral, tired, 0.3): "You again."')
    prose = roleplay.port_prose(_messages_of(conv["id"]), Persona(name="Sam"))
    assert prose == 'Alice (neutral, tired, 0.3): "You again."'


def test_port_refuses_an_empty_conversation(client, project):
    conv = new_conv(client, project)
    r = client.post(f"/api/projects/{project}/rp/conversations/{conv['id']}/port")
    assert r.status_code == 409


def test_a_ported_conversation_reports_its_scene(client, project):
    from app import store
    from app.models import Node, NodeType
    conv = new_conv(client, project)
    add(client, project, conv["id"], "cast", "She waits.")
    assert client.get(
        f"/api/projects/{project}/rp/conversations/{conv['id']}").json()["scene_id"] is None
    # The port records itself on the scene node, not on the conversation row --
    # so deleting the scene un-ports it with nothing left to clean up.
    scene = Node(id=store.new_id("n_"), type=NodeType.scene,
                 data={"title": "Ported", "_rp_conv": conv["id"]}, slug="ported")
    store.insert_node(project, scene)
    assert client.get(
        f"/api/projects/{project}/rp/conversations/{conv['id']}"
    ).json()["scene_id"] == scene.id
    store.delete_node(project, scene.id)
    assert client.get(
        f"/api/projects/{project}/rp/conversations/{conv['id']}").json()["scene_id"] is None


def _messages_of(conv_id):
    from app import store
    return store.list_messages(conv_id)


def _await_job(client, job_id, timeout=30.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def test_porting_a_conversation_builds_a_scene_with_the_cast_staged(
        client, project, monkeypatch):
    """The whole port, end to end, with one fake model call.

    One call is the point: the transcript is already house format, so the only
    LLM on the default path is the cold segmenter. Everything else -- the
    player's actions, their name, staging the cast on before they speak -- is
    code.
    """
    import json

    from app.clients import lmstudio
    from app.services import generation

    calls: list[dict] = []

    async def fake_chat(messages, model="", params=None, json_schema=None, **kw):
        calls.append({"schema": json_schema is not None,
                      "prose": messages[-1]["content"]})
        return json.dumps({"scene_title": "The counter, closing time", "nodes": [
            {"type": "say", "speaker": "alice", "text": "Ten minutes."},
            {"type": "narration", "text": "She locks the till."},
            {"type": "narration", "text": "You nod."},
        ]})

    monkeypatch.setattr(lmstudio, "chat", fake_chat)
    monkeypatch.setattr(generation.lmstudio, "chat", fake_chat)

    client.put(f"/api/projects/{project}/rp/persona",
               json={"name": "Sam", "description": "", "notes": ""})
    conv = new_conv(client, project, scene_context="Closing time.")
    add(client, project, conv["id"], "cast",
        'Alice (smirking, amused, 0.6): "Ten minutes."\nShe locks the till.')
    add(client, project, conv["id"], "player", "*nods* [wrap it up]")

    job = _await_job(client, client.post(
        f"/api/projects/{project}/rp/conversations/{conv['id']}/port").json()["job_id"])
    assert job["status"] == "done", job.get("error")
    result = job["result"]

    # One model call, and the prose it was handed was already segmented-ready:
    # the steer note gone, the action conjugated, the bracket intact.
    assert len(calls) == 1 and calls[0]["schema"] is True
    assert "wrap it up" not in calls[0]["prose"]
    assert "You nod." in calls[0]["prose"]

    nodes = {n["id"]: n for n in client.get(f"/api/projects/{project}/graph").json()["nodes"]}
    scene = nodes[result["scene_id"]]
    assert scene["type"] == "scene"
    assert scene["data"]["title"] == "The counter, closing time"
    # The port records itself here and nowhere else.
    assert scene["data"]["_rp_conv"] == conv["id"]
    assert scene["data"]["brief"] == "Closing time."

    chain = [nodes[i] for i in result["inserted"]]
    # Nobody was ever on stage in a conversation, so reconcile_staging walks
    # Alice on before her first line -- otherwise her sprite is missing for
    # the whole scene.
    assert chain[0]["type"] == "stage"
    assert "alice" in (chain[0]["data"].get("show") or [])
    said = next(n for n in chain if n["type"] == "say")
    # The performance the model wrote during the conversation survives the
    # round trip onto the node, which is why cast lines keep the bracket.
    assert said["data"]["emotion"] == "amused"
    assert said["data"]["expression"] == "smirking"
    assert said["data"]["intensity"] == 0.6
    assert said["data"]["speaker"] == "alice"

    # And the conversation now knows which scene it became.
    assert client.get(
        f"/api/projects/{project}/rp/conversations/{conv['id']}"
    ).json()["scene_id"] == scene["id"]
