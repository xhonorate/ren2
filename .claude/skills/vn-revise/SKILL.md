---
name: vn-revise
description: Apply the human's review notes (review/notes/<scene>.yaml, written in the console) to a scene, re-validate, mark them applied, and update the ledger if continuity changed.
---

# /vn-revise <project> <scene id>

The console lets the human leave notes on lines of a scene; this skill is
how those notes become edits. It is also what the console's "Revise with
Claude" button runs (`claude -p`), so it must finish without asking anything.

1. Read `review/notes/<scene>.yaml`. Each note has `step` (the index in the
   expanded step list as `vn script`/the console show it), `kind`, `quote`
   (the line text at the time), `note` (what the human wants), `applied`.
   Only notes with `applied: false` are work.
2. Run `python -m vn pack <project> <scene>` and read it; the style and the
   voice sheets are binding for every edit.
3. Read `scenes/<scene>.yaml`. For each note, find the step by its `quote`
   first (text may have shifted), then by `step`. Make the change the note
   asks for, in that character's voice, keeping the scene's structure
   (levels, flags, `next`, the `nsfw` placeholder, the `set` step at the
   end of a spoke). A note that asks for something the style or the sheet
   forbids is applied in the nearest form that is allowed, and the reply
   below says so. A note on an explicit section (`nsfw` step) changes the
   brief's `## Direction`, not the scene.
4. `python -m vn validate <project> <scene>` must print `valid`.
5. If a fact changed (who knows what, an outfit, a thread), update the
   scene's entry in `ledger.yaml` or `ledger/<route>.yaml`.
6. Set `applied: true` on each note you applied and add `reply: "<one
   line: what changed>"`; leave a note you could not apply unapplied with
   `reply` saying why.
7. Append one line to `notes/<route>.md` (or `NOTES.md`): scene, notes applied.
