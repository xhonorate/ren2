"""Ren'Py project export.

Regenerate only game/generated/. Never touch anything else -- that is what
lets you hand-edit the game freely alongside the tool (doc S7).
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import db, store
from ..compiler.renpy import CompileResult, compile_project
from ..graph import Graph, short_hash, slugify
from ..models import NodeType
from . import assets

GENERATED = "game/generated"

# Directory names inside the SDK that are Ren'Py's own. A project slugging to
# one of these would be catastrophic -- "game" shadows launcher/game and stops
# the SDK opening the launcher at all, "launcher" would bury the launcher
# itself -- so those get a prefix instead.
_RESERVED_SDK_NAMES = {
    "game", "data", "launcher", "renpy", "lib", "gui", "doc", "update",
    "sdk-fonts", "tmp", "the_question", "tutorial", "renpy.app",
}


def compile_ir(project_id: str) -> CompileResult:
    project = store.get_project(project_id)
    if project is None:
        raise ValueError(f"unknown project {project_id}")
    nodes = store.list_nodes(project_id, include_orphaned=False)
    edges = store.list_edges(project_id)
    characters = store.list_characters(project_id)
    locations = store.list_locations(project_id)
    return compile_project(
        project, nodes, edges, characters, locations,
        # The generated scale transforms need to know what the art is, so a
        # project that changed its sprite/CG dimensions still lands correctly.
        sprite_size=(int(store.get_setting("image_width", 832)),
                     int(store.get_setting("image_height", 1216))),
        bg_size=(int(store.get_setting("cg_width", 1344)),
                 int(store.get_setting("cg_height", 768))),
        presentation=_presentation_settings(),
    )


def _presentation_settings() -> dict[str, Any]:
    """Config -> Presentation, folded into the keys the compiler expects."""
    g = store.get_setting
    return {
        "accent_color": g("renpy_accent_color", "#e0819b") or "#e0819b",
        "choice_buttons": bool(g("renpy_choice_buttons", True)),
        "choice_dim": float(g("renpy_choice_dim", 0.6) or 0),
        "textbox_opacity": float(g("renpy_textbox_opacity", 0.85) or 0),
        "text_size": int(g("renpy_text_size", 33) or 33),
        "text_cps": int(g("renpy_text_cps", 0) or 0),
        "dialogue_font": (g("renpy_dialogue_font", "") or "").strip(),
        "quick_menu": bool(g("renpy_quick_menu", True)),
        "nvl_narration": bool(g("renpy_nvl_narration", False)),
    }


def project_folder_name(project) -> str:
    """A stable, readable directory name for one project.

    Two projects whose names slug the same are disambiguated by suffixing all
    but one with a short id. Which one keeps the bare slug is decided by
    (created_at, id) -- deterministic and stable, so a folder never moves under
    a project, though not necessarily creation order: store.now() only has
    second granularity, so same-second ties fall to the id.
    """
    slug = slugify(project.name, fallback="vn")
    if slug in _RESERVED_SDK_NAMES:
        return f"vn_{slug}"
    clashes = [p for p in store.list_projects()
               if slugify(p.name, fallback="vn") == slug]
    if len(clashes) > 1:
        clashes.sort(key=lambda p: (p.created_at or "", p.id))
        if clashes[0].id != project.id:
            return f"{slug}_{short_hash(project.id, 6)}"
    return slug


def project_dir(project_id: str) -> Path:
    """Where this project's Ren'Py game lives: one subfolder per project inside
    the SDK.

    That is the directory the launcher scans for projects
    (launcher/game/project.rpy -> scan_directory(config.renpy_base)), so every
    project shows up in its list with no extra configuration -- which is what
    you need to reach the one-time Web support download. Falls back to the
    tool's own builds directory when no SDK is configured.
    """
    project = store.get_project(project_id)
    sdk = renpy_sdk_root()
    if project is None or sdk is None or not sdk.is_dir():
        return db.BUILD_DIR / project_id
    return sdk / project_folder_name(project)


def is_renpy_sdk_root(path: Path) -> bool:
    """Is this the Ren'Py SDK itself rather than a game project?"""
    return ((path / "renpy").is_dir() and (path / "launcher").is_dir()
            and (path / "renpy.py").is_file())


# Why this is fatal rather than merely untidy: renpy.py's path_to_gamedir()
# searches basedir for "game", then "data", then "launcher/game", and takes the
# first that exists. A stock SDK has no game/, so a bare `renpy` falls through
# to launcher/game and you get the launcher. Export into the SDK root and our
# game/ wins that race -- the SDK's own executable stops opening the launcher
# and boots the exported project instead, with no way back except deleting it.
SDK_ROOT_HELP = (
    "{root} is the Ren'Py SDK itself, not a game project. Exporting here "
    "creates <sdk>/game/, which shadows launcher/game -- the SDK's renpy "
    "executable would stop opening the launcher and boot this game instead. "
    "Point the Ren'Py project directory at a folder of its own (leave it blank "
    "to use the tool's own builds directory). If a previous export already "
    "wrote <sdk>/game/, delete that folder to get the launcher back; nothing in "
    "it belongs to Ren'Py."
)


# --------------------------------------------------------------------------
# hand-owned scaffolding, written once and then left alone
# --------------------------------------------------------------------------

def _entry_script(result: CompileResult, project_name: str) -> str:
    target = result.entry or "start_placeholder"
    return f'''\
# Hand-written entry point. The authoring tool never overwrites this file.
# Edit it freely: add a prologue, a title screen hook, whatever you want.

label start:
    jump {target}

label after_load:
    return
'''


_CUSTOM = '''\
# Your own screens and logic. The authoring tool never overwrites this file.
# Anything the node graph cannot express belongs here (or in a Raw node).
#
# This file loads after game/generated/theme.rpy, so redefining a vn.* token
# here overrides Config -> Presentation, e.g.:
#     define vn.accent = "#8fd3ff"
#     define vn.choice_buttons = False
'''


# The in-game presentation. Written once, then yours to edit -- the tool
# never regenerates it. Colours, sizes and toggles come from
# game/generated/theme.rpy (rewritten each compile from Config ->
# Presentation); the layout below is deliberately self-contained (no gui.rpy,
# no gui/*.png) so it always runs.
_SCREENS = '''\
## Hand-owned presentation -- written once by the authoring tool, never
## regenerated. Tune the knobs in Config -> Presentation (they land in
## game/generated/theme.rpy as vn.* tokens); rework anything else right here.


## Say screen #################################################################

screen say(who, what):

    window:
        id "window"

        if who is not None:
            window:
                id "namebox"
                style "namebox"
                text who id "who"

        text what id "what"

    if not renpy.variant("small"):
        add SideImage() xalign 0.0 yalign 1.0

init python:
    config.character_id_prefixes.append("namebox")

style say_window:
    xfill True
    yalign 1.0
    ysize 280
    padding (80, 28, 80, 32)
    background Solid(Color("#0f0d17", alpha=vn.textbox_alpha))

style namebox:
    xpos 44
    xanchor 0.0
    ypos -6
    yanchor 1.0
    padding (26, 8, 26, 10)
    background Solid(Color(vn.accent, alpha=0.92))

style say_label:
    color "#ffffff"
    size vn.dialogue_size - 3
    font vn.dialogue_font
    bold True

style say_dialogue:
    xfill True
    color "#f2f2f2"
    size vn.dialogue_size
    font vn.dialogue_font
    line_spacing 4


## Choice screen -- the in-scene menu ########################################

screen choice(items):

    if vn.choice_dim > 0.0:
        add Solid("#000000") alpha vn.choice_dim

    vbox:
        xalign 0.5
        yalign 0.5
        spacing 14

        for i in items:
            textbutton i.caption:
                action i.action
                style ("vn_choice_framed" if vn.choice_buttons else "vn_choice_plain")

style vn_choice_framed is default:
    xalign 0.5
    xminimum 560
    xmaximum 1060
    padding (46, 16, 46, 16)
    background "#0d0b14cc"
    hover_background Solid(Color(vn.accent, alpha=0.30))
    insensitive_background "#0d0b1455"

style vn_choice_framed_text is default:
    xalign 0.5
    text_align 0.5
    layout "subtitle"
    size vn.dialogue_size
    font vn.dialogue_font
    idle_color "#dcdce0"
    hover_color "#ffffff"
    insensitive_color "#79798a"

style vn_choice_plain is default:
    xalign 0.5
    padding (14, 6, 14, 6)
    hover_background "#ffffff12"

style vn_choice_plain_text is default:
    xalign 0.5
    text_align 0.5
    size vn.dialogue_size
    font vn.dialogue_font
    idle_color "#c8c8cf"
    hover_color vn.accent
    insensitive_color "#79798a"


## Quick menu ###############################################################

screen quick_menu():
    zorder 100

    if vn.quick_menu and quick_menu and not main_menu:
        hbox:
            style_prefix "quick"
            xalign 0.5
            yalign 1.0
            spacing 14

            textbutton _("Back") action Rollback()
            textbutton _("History") action ShowMenu("history")
            textbutton _("Skip") action Skip() alternate Skip(fast=True, confirm=True)
            textbutton _("Auto") action Preference("auto-forward", "toggle")
            textbutton _("Save") action ShowMenu("save")
            textbutton _("Prefs") action ShowMenu("preferences")

init python:
    config.overlay_screens.append("quick_menu")

default quick_menu = True

style quick_button is default:
    padding (7, 5, 7, 5)

style quick_button_text is default:
    size 20
    font vn.dialogue_font
    idle_color "#9a9aa4"
    hover_color "#ffffff"
    insensitive_color "#5a5a63"


## Skip indicator ###########################################################

screen skip_indicator():
    zorder 100

    if config.skipping and not main_menu:
        frame:
            xalign 0.5
            ypos 18
            padding (16, 6, 16, 6)
            background "#0d0b14cc"
            text _("Skipping"):
                size 20
                color "#dcdce0"
                font vn.dialogue_font

init python:
    config.overlay_screens.append("skip_indicator")


## NVL screen -- narration when Config -> Presentation turns it on ##########

screen nvl(dialogue, items=None):

    window:
        style "nvl_window"

        has vbox:
            spacing 14

        for d in dialogue:
            window:
                id d.window_id
                has vbox

                if d.who is not None:
                    text d.who id d.who_id
                text d.what id d.what_id

        for i in items:
            textbutton i.caption:
                action i.action
                style "nvl_menu_choice_button"

    add SideImage() xalign 0.0 yalign 1.0

style nvl_window:
    xfill True
    yfill True
    xalign 0.5
    padding (160, 70, 160, 70)
    background Solid(Color("#0f0d17", alpha=vn.textbox_alpha))

style nvl_label:
    color vn.accent

style nvl_menu_choice_button is vn_choice_framed:
    xalign 0.5

style nvl_menu_choice_button_text is vn_choice_framed_text

init python:
    config.nvl_list_length = 12
'''


def _options(project_name: str) -> str:
    return f'''\
## Written once by the authoring tool, then left alone.

## Ren'Py's own default is 800x600, which is far below the generated art.
## The generated scale transforms follow whatever you set here.
init python:
    gui.init(1920, 1080)

define config.name = _("{project_name}")
define gui.show_name = True
define config.version = "0.1"

define config.has_voice = True
define config.play_channel = "voice"

define build.name = "{project_name.replace(' ', '')}"
'''


def scaffold(root: Path, project_name: str, result: CompileResult) -> list[str]:
    created: list[str] = []
    (root / "game").mkdir(parents=True, exist_ok=True)
    for rel, content in (
        ("game/script.rpy", _entry_script(result, project_name)),
        ("game/custom.rpy", _CUSTOM),
        ("game/options.rpy", _options(project_name)),
        ("game/screens.rpy", _SCREENS),
    ):
        path = root / rel
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(rel)
    for folder in ("game/images/bg", "game/images/chars", "game/images/cg",
                   "game/audio/voice", "game/audio/music", "game/audio/sfx"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    return created


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def export(project_id: str, copy_assets: bool = True,
           dest: Optional[Path] = None) -> dict[str, Any]:
    """`dest` overrides the usual <sdk>/<project>/ location for a one-off
    export somewhere else (seed_demo, scripted builds)."""
    project = store.get_project(project_id)
    result = compile_ir(project_id)
    root = Path(dest) if dest else project_dir(project_id)
    if is_renpy_sdk_root(root):
        raise ValueError(SDK_ROOT_HELP.format(root=root))

    created = scaffold(root, project.name if project else "Visual Novel", result)

    generated_root = root / GENERATED
    if generated_root.exists():
        shutil.rmtree(generated_root)
    for rel, body in result.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    copied, missing = 0, []
    if copy_assets:
        for item in result.assets:
            if assets.copy_to(item["hash"], root / item["dest"]):
                copied += 1
            else:
                missing.append(item["dest"])
        copied_voice, missing_voice = _copy_voice_lines(project_id, root)
        copied += copied_voice
        missing.extend(missing_voice)

    return {
        "project_dir": str(root),
        "files": sorted(result.files),
        "labels": len(result.labels),
        "warnings": result.warnings,
        "stats": result.stats,
        "scaffolded": created,
        "assets_copied": copied,
        "assets_missing": missing[:40],
        "assets_missing_count": len(missing),
    }


def _copy_voice_lines(project_id: str, root: Path) -> tuple[int, list[str]]:
    copied, missing = 0, []
    for node in store.list_nodes(project_id, include_orphaned=False):
        data = node.data or {}
        voice_path = data.get("voice_path")
        voice_hash = data.get("voice_hash")
        if not voice_path:
            continue
        if voice_hash and assets.copy_to(voice_hash, root / "game" / voice_path):
            copied += 1
        else:
            missing.append(voice_path)
    return copied, missing


def dry_run(project_id: str) -> dict[str, Any]:
    """Compile without writing, for the in-editor script preview."""
    result = compile_ir(project_id)
    return {
        "files": result.files,
        "warnings": result.warnings,
        "labels": result.labels,
        "stats": result.stats,
        "assets": result.assets,
    }


def validate(project_id: str) -> dict[str, Any]:
    """Graph-level problems worth catching before a build."""
    nodes = store.list_nodes(project_id, include_orphaned=False)
    edges = store.list_edges(project_id)
    graph = Graph.build(nodes, edges)
    characters = {c.id for c in store.list_characters(project_id)}
    locations = {l.id for l in store.list_locations(project_id)}
    project = store.get_project(project_id)
    # What a Hub condition is allowed to name: a declared per-character stat,
    # or a declared story variable. Structured conditions exist so a typo is
    # caught here rather than silently evaluating False for the whole game.
    stat_ids = {s.id for s in (project.character_stats if project else [])}
    variable_names = {v.name for v in (project.variables if project else [])}
    routes = {((n.data or {}).get("route") or "").strip()
              for n in nodes if n.type == NodeType.scene}
    routes.discard("")

    problems: list[dict[str, str]] = []

    def flag(node_id: str, severity: str, message: str) -> None:
        problems.append({"node_id": node_id, "severity": severity, "message": message})

    def check_gates(node_id: str, what: str, holder: dict[str, Any]) -> None:
        """Structured gates name a declared stat, or they are a silent False.

        Hub topics, choice options and condition branches all gate the same
        way, and the whole reason the gate is picked rather than typed is that
        a typo is caught here instead of quietly locking content for the rest
        of the game.
        """
        for cond in holder.get("requires") or []:
            stat = (cond.get("stat") or "").strip()
            who = (cond.get("character") or "").strip()
            if not stat:
                flag(node_id, "error", f"{what} has a condition with no stat")
            elif who and stat not in stat_ids:
                flag(node_id, "error",
                     f"{what} gates on undeclared stat '{stat}'")
            elif not who and stat not in variable_names and stat not in stat_ids:
                flag(node_id, "error",
                     f"{what} gates on undeclared variable '{stat}'")
            if who and who not in characters:
                flag(node_id, "error",
                     f"{what} gates on unknown character '{who}'")

    for node in nodes:
        d = node.data or {}
        outs = graph.out_edges(node.id)
        if node.type == NodeType.choice:
            option_ids = {o.get("id") for o in (d.get("options") or [])}
            wired = {e.source_handle for e in outs}
            for opt in d.get("options") or []:
                check_gates(node.id,
                            f"choice option '{opt.get('label') or opt.get('id')}'", opt)
            for missing in sorted(option_ids - wired):
                label = next((o.get("label") for o in d["options"] if o.get("id") == missing), missing)
                flag(node.id, "error", f"choice option '{label}' goes nowhere")
            if len(option_ids) < 2:
                flag(node.id, "warning", "choice has fewer than two options")
        elif node.type == NodeType.hub:
            wired = {e.source_handle for e in outs}
            options = d.get("options") or []
            if not options:
                flag(node.id, "error", "hub has no options")
            for opt in options:
                name = opt.get("label") or opt.get("id") or "?"
                route = (opt.get("target_route") or "").strip()
                if route:
                    if route not in routes:
                        flag(node.id, "error",
                             f"hub option '{name}' targets route '{route}', "
                             "which no scene belongs to")
                elif opt.get("id") not in wired:
                    flag(node.id, "error", f"hub option '{name}' goes nowhere")
                check_gates(node.id, f"hub option '{name}'", opt)
            if (options and d.get("exhaust", True) and d.get("loop_back", True)
                    and not any(o.get("repeatable") for o in options)):
                flag(node.id, "warning",
                     "this hub drops every topic once taken and always comes "
                     "back to itself, so it eventually has nothing to show and "
                     "Ren'Py raises on an empty menu — mark the way out as "
                     "always available")
            # Ren'Py raises on a menu with nothing to show, and a hub that
            # hides every option is one bad stat away from exactly that.
            if options and all(
                    (opt.get("requires") or []) and opt.get("locked") != "grey"
                    for opt in options):
                flag(node.id, "warning",
                     "every hub option is conditional and hidden when locked — "
                     "the menu has nothing to show if none of them are met")

        elif node.type == NodeType.condition:
            wired = {e.source_handle for e in outs}
            for branch in d.get("branches") or []:
                if branch.get("id") not in wired:
                    flag(node.id, "error",
                         f"condition branch '{branch.get('expr') or 'else'}' goes nowhere")
                check_gates(node.id,
                            f"condition branch '{branch.get('expr') or 'else'}'", branch)
        elif node.type in (NodeType.jump, NodeType.call):
            # A route is a destination even with nothing wired: it resolves at
            # play time. What is worth flagging is a route name no scene
            # claims, which resolves to nothing forever.
            node_route = (d.get("target_route") or "").strip()
            if node_route and node_route not in routes:
                flag(node.id, "error",
                     f"{node.type.value} node targets route '{node_route}', "
                     f"which no scene belongs to")
            elif not outs and not node_route and not d.get("target_label"):
                flag(node.id, "error", f"{node.type.value} node has no target")
        elif node.type == NodeType.say:
            if not d.get("speaker"):
                flag(node.id, "error", "say node has no speaker")
            elif d["speaker"] not in characters:
                flag(node.id, "error", f"unknown speaker '{d['speaker']}'")
            if not (d.get("text") or "").strip():
                flag(node.id, "warning", "say node has no text")
        elif node.type == NodeType.scene:
            if d.get("background") and d["background"] not in locations:
                flag(node.id, "warning", f"unknown location '{d['background']}'")
        elif node.type == NodeType.narration:
            if not (d.get("text") or "").strip():
                flag(node.id, "warning", "narration node has no text")

        if node.type not in (NodeType.end, NodeType.jump, NodeType.choice,
                             NodeType.hub, NodeType.condition,
                             NodeType.call) and not outs:
            flag(node.id, "warning",
                 "node has no outgoing edge; it will emit `return` "
                 "(correct for a called topic, a dead end anywhere else)")

    for character in store.list_characters(project_id):
        if character.age < 18:
            flag(character.id, "error",
                 f"{character.display_name} is under 18 and cannot reach the image pipeline")
        if not character.is_player and character.voice.voiced and not character.voice.reference_clip:
            flag(character.id, "warning",
                 f"{character.display_name} has no voice reference clip")

    return {
        "problems": problems,
        "errors": sum(1 for p in problems if p["severity"] == "error"),
        "warnings": sum(1 for p in problems if p["severity"] == "warning"),
    }


# --------------------------------------------------------------------------
# launching Ren'Py
# --------------------------------------------------------------------------

def renpy_executable() -> Optional[Path]:
    sdk = store.get_setting("renpy_sdk_path", "")
    if not sdk:
        return None
    root = Path(sdk)
    for candidate in ("renpy.exe", "renpy.sh", "renpy.app/Contents/MacOS/renpy", "renpy.py"):
        path = root / candidate
        if path.exists():
            return path
    return None


async def launch(project_id: str) -> dict[str, Any]:
    exe = renpy_executable()
    root = project_dir(project_id)
    if exe is None:
        return {"launched": False,
                "reason": "renpy_sdk_path is not set in settings",
                "project_dir": str(root)}
    cmd = [str(exe), str(root)]
    if exe.suffix == ".py":
        cmd = ["python", str(exe), str(root)]

    def _spawn() -> int:
        proc = subprocess.Popen(cmd, cwd=str(exe.parent))
        return proc.pid

    pid = await asyncio.get_event_loop().run_in_executor(None, _spawn)
    return {"launched": True, "pid": pid, "project_dir": str(root), "cmd": cmd}


# --------------------------------------------------------------------------
# web (HTML) build -- playable in a browser, including a phone's
# --------------------------------------------------------------------------
#
# Ren'Py emits a web package (Emscripten: index.html + game.zip + the runtime)
# through the launcher's own `web_build` command -- NOT `distribute --package
# web`, which only makes the inner game.zip. The command is registered by the
# launcher project (launcher/game/web.rpy), so the base directory has to be the
# launcher, with the real project passed as an argument:
#
#     renpy <sdk>/launcher web_build <project> --destination <dir>
#
# It honours --destination exactly (wiping and recreating it), so there is
# nothing to go hunting for afterwards. It also drops a <destination>.zip
# sibling, which is a ready-to-upload bundle of the same files.
#
# Staged outside the Ren'Py project (data/web/<pid>/), or the next build would
# try to package the previous one.

WEB_STAGE = "web"

# What web_build prints when <sdk>/web is missing -- the one failure worth
# translating into instructions rather than dumping raw.
_NO_WEB_SUPPORT = "web support is not available"


def web_dir(project_id: str) -> Path:
    return db.ROOT / WEB_STAGE / project_id


def renpy_sdk_root() -> Optional[Path]:
    sdk = store.get_setting("renpy_sdk_path", "")
    return Path(sdk) if sdk else None


def web_support_installed() -> bool:
    """`<sdk>/web/` — downloaded once from the launcher, absent on a fresh SDK."""
    root = renpy_sdk_root()
    return bool(root and (root / "web" / "index.html").is_file())


def web_info(project_id: str) -> dict[str, Any]:
    """What the Build view needs to decide whether Play is available."""
    root = web_dir(project_id)
    index = root / "index.html"
    # `support` lets the Build view warn before you start a build that cannot
    # work, rather than after the SDK has told us so.
    support = web_support_installed()
    if not index.is_file():
        return {"built": False, "url": None, "built_at": None, "bytes": 0,
                "support": support}
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    built_at = datetime.fromtimestamp(index.stat().st_mtime, timezone.utc)
    return {
        "built": True,
        "url": f"/play/{project_id}/",
        "built_at": built_at.isoformat(timespec="seconds"),
        "bytes": total,
        "support": support,
    }


WEB_SUPPORT_HELP = (
    "Ren'Py's web support (RenPyWeb) is not installed in this SDK. It is a "
    "one-time download the launcher has to do: run the SDK's renpy executable "
    "with NO arguments to get the launcher, pick this project in the left-hand "
    "list, click Web in the actions column (next to Build Distributions), and "
    "accept the download prompt. Then try this build again."
)


async def web_build(project_id: str) -> dict[str, Any]:
    exe = renpy_executable()
    sdk = renpy_sdk_root()
    root = project_dir(project_id)
    if exe is None or sdk is None:
        return {"ok": False, "reason": "renpy_sdk_path is not set in settings"}
    if not (root / "game").is_dir():
        return {"ok": False,
                "reason": f"{root} has no game/ directory yet -- export the project first"}
    if not web_support_installed():
        return {"ok": False, "reason": WEB_SUPPORT_HELP}

    dest = web_dir(project_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), str(sdk / "launcher"), "web_build", str(root),
           "--destination", str(dest)]
    if exe.suffix == ".py":
        cmd = ["python", *cmd]

    def _run() -> tuple[int, str]:
        proc = subprocess.run(cmd, cwd=str(exe.parent), capture_output=True, timeout=1800)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")

    code, output = await asyncio.get_event_loop().run_in_executor(None, _run)

    if not (dest / "index.html").is_file():
        reason = ("Ren'Py ran but wrote no web package. Its own output is below.")
        if _NO_WEB_SUPPORT in output.lower():
            reason = WEB_SUPPORT_HELP
        return {"ok": False, "exit_code": code, "cmd": cmd,
                "output": output[-8000:], "reason": reason}

    info = web_info(project_id)
    return {"ok": True, "exit_code": code, "cmd": cmd, "output": output[-4000:],
            "web_dir": str(dest), **info}


async def open_launcher() -> dict[str, Any]:
    """Spawn the SDK launcher GUI, where Build Distributions and the one-time
    Web support download live.

    Deliberately passes NO argument. `renpy` with no basedir sets
    config.basedir to the SDK root, and the launcher's DLC downloader reads
    <basedir>/update/current.json to know what it may fetch. Passing
    <sdk>/launcher explicitly -- tempting, because path_to_gamedir() prefers
    basedir/game over launcher/game -- moves that lookup to
    <sdk>/launcher/update/, which does not exist, and every download then fails
    with "this project does not support updating". So a stray <sdk>/game has to
    be reported rather than dodged.
    """
    exe = renpy_executable()
    sdk = renpy_sdk_root()
    if exe is None or sdk is None:
        return {"launched": False, "reason": "renpy_sdk_path is not set in settings"}
    if not (sdk / "launcher").is_dir():
        return {"launched": False,
                "reason": f"no launcher/ directory under {sdk} -- is that the SDK root?"}
    stray = sdk / "game"
    if stray.is_dir():
        return {"launched": False, "sdk_game_dir": str(stray), "reason": (
            f"{stray} exists, so launching Ren'Py would boot that game instead "
            "of the launcher (path_to_gamedir prefers basedir/game over "
            "launcher/game). Delete that folder -- it is left over from an old "
            "export and nothing in it belongs to Ren'Py."
        )}

    cmd = ["python", str(exe)] if exe.suffix == ".py" else [str(exe)]

    def _spawn() -> int:
        return subprocess.Popen(cmd, cwd=str(exe.parent)).pid

    pid = await asyncio.get_event_loop().run_in_executor(None, _spawn)
    return {"launched": True, "pid": pid, "cmd": cmd}


async def lint(project_id: str) -> dict[str, Any]:
    """Run Ren'Py's own lint over the exported project."""
    exe = renpy_executable()
    root = project_dir(project_id)
    if exe is None:
        return {"ok": False, "reason": "renpy_sdk_path is not set in settings"}
    cmd = [str(exe), str(root), "lint"]
    if exe.suffix == ".py":
        cmd = ["python", str(exe), str(root), "lint"]

    def _run() -> tuple[int, str]:
        proc = subprocess.run(cmd, cwd=str(exe.parent), capture_output=True, timeout=300)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")

    code, output = await asyncio.get_event_loop().run_in_executor(None, _run)
    return {"ok": code == 0, "exit_code": code, "output": output[-8000:]}
