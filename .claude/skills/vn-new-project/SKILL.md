---
name: vn-new-project
description: Start a new visual novel project - interview the human for the premise, create the project directory, write premise.md. First step of the vn pipeline.
---

# /vn-new-project [slug]

Read `DESIGN.md` §3.1 before starting.

1. If no slug was given, propose one from the first answers below.
2. Interview the human. Use AskUserQuestion for the closed questions and plain
   conversation for the open ones. Ask in two rounds, not one wall:
   - Round 1: genre & tone (offer 4 options plus Other), audience, content
     rating (`sfw` / `suggestive` / `explicit`), branching level (`linear` /
     `routes` / `branching`, explain each in one line, recommend `routes`).
   - Round 2: the player (name, who they are, how they speak), the setting in
     a sentence, what should make this one different, things to avoid.
   Accept short answers; it is your job to fill gaps with specific, interesting
   detail and say what you filled in.
3. `python -m vn new projects/<slug>` then write `projects/<slug>/premise.md`
   following the template headings exactly (the compiler reads `# Title` and
   `## The player` and `## Content rating`).
4. Offer three loglines; put the chosen one in the file.
5. **Checkpoint**: show the finished premise and ask for approval. Revise until
   approved. Then tell the human the next step is `/vn-style`.

Output: `projects/<slug>/` with `premise.md` filled in.
