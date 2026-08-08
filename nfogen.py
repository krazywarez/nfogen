#!/usr/bin/env python3
"""nfogen - a scene-style NFO generator.

Builds a boxed ASCII .nfo from CLI flags, an optional config file, and
interactive prompts for anything still missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import date
from pathlib import Path

# --- field schema ------------------------------------------------------------
# One source of truth. Drives argparse flags, interactive prompts, and render.
#   key    : internal name / config key / flag name
#   label  : shown in the rendered nfo (info rows) or as a section header
#   help   : argparse + prompt help text
#   kind   : "line"  scalar shown as an aligned INFO row
#            "list"  many values, shown space-joined in its own section
#            "block" free text, wrapped in its own section

FIELDS = [
    ("title",      "TITLE",      "release title (required)",            "line"),
    ("date",       "DATE",       "release date",                        "line"),
    ("type",       "TYPE",       "release type (MOVIE, TV, APP, ...)",  "line"),
    ("source",     "SOURCE",     "source (BluRay, WEB-DL, CD, ...)",    "line"),
    ("format",     "FORMAT",     "container / format",                  "line"),
    ("video",      "VIDEO",      "video codec / details",               "line"),
    ("audio",      "AUDIO",      "audio codec / details",               "line"),
    ("resolution", "RESOLUTION", "resolution",                          "line"),
    ("language",   "LANGUAGE",   "language(s)",                         "line"),
    ("genre",      "GENRE",      "genre",                               "line"),
    ("runtime",    "RUNTIME",    "runtime / duration",                  "line"),
    ("size",       "SIZE",       "total size",                          "line"),
    ("files",      "FILES",      "file count / listing",                "line"),
    ("url",        "URL",        "reference url (imdb, homepage, ...)", "line"),
    ("notes",      "NOTES",      "free-text notes / description",       "block"),
    ("greets",     "GREETS",     "groups to greet (space/comma sep)",   "list"),
]
FIELD_KINDS = {k: kind for k, _, _, kind in FIELDS}
INFO_KEYS = [k for k, _, _, kind in FIELDS if kind == "line" and k != "title"]

DEFAULT_SITE = "krz.sh"
DEFAULT_GROUP = "KRZ"
DEFAULT_WIDTH = 64
CONFIG_CANDIDATES = [
    Path("nfogen.toml"),
    Path(".nfogen.toml"),
    Path.home() / ".config" / "nfogen" / "config.toml",
]


# --- config ------------------------------------------------------------------
def load_config(explicit: str | None) -> dict:
    """Load defaults from a TOML or JSON file. Returns {} if none found."""
    path = None
    if explicit:
        path = Path(explicit)
        if not path.exists():
            sys.exit(f"nfogen: config not found: {explicit}")
    else:
        path = next((p for p in CONFIG_CANDIDATES if p.exists()), None)
    if path is None:
        return {}

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    try:
        import tomllib
    except ModuleNotFoundError:
        sys.exit("nfogen: TOML config needs Python 3.11+ (or use a .json file)")
    return tomllib.loads(text)


# --- interactive -------------------------------------------------------------
def prompt_missing(data: dict, force_all: bool) -> None:
    """Fill fields from stdin. Prompts every field when force_all, else only
    the missing ones. Enter keeps the current/blank value."""
    keys = [k for k, *_ in FIELDS] if force_all else \
        [k for k, *_ in FIELDS if not data.get(k)]
    if not keys:
        return
    print("nfogen: interactive mode (blank to skip)\n", file=sys.stderr)
    for key in keys:
        _, label, help_text, _ = next(f for f in FIELDS if f[0] == key)
        current = data.get(key, "")
        suffix = f" [{current}]" if current else ""
        try:
            answer = input(f"{label} ({help_text}){suffix}: ").strip()
        except EOFError:
            break
        if answer:
            data[key] = answer


# --- rendering ---------------------------------------------------------------
def _split_list(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p for p in str(value).replace(",", " ").split() if p]


def render(data: dict, group: str, site: str, width: int) -> str:
    inner = width - 4  # borders + one space of padding on each side
    top = "┌" + "─" * (width - 2) + "┐"
    sep = "├" + "─" * (width - 2) + "┤"
    bot = "└" + "─" * (width - 2) + "┘"

    def line(content: str = "") -> str:
        return "│ " + content.ljust(inner) + " │"

    def center(content: str) -> str:
        return "│ " + content.center(inner) + " │"

    out: list[str] = [top]

    # header: spaced title, then group tag + site
    title = str(data.get("title") or "UNTITLED").upper()
    spaced = " ".join(title)
    heading = spaced if len(spaced) <= inner else title
    out.append(center(_clip(heading, inner)))
    out.append(center(_clip(f"[ {group} ]   {site}", inner)))

    # info rows
    rows = [(label, str(data[key]))
            for key, label, _, _ in FIELDS
            if key in INFO_KEYS and data.get(key)]
    if rows:
        out.append(sep)
        lw = max(len(label) for label, _ in rows)
        avail = inner - (2 + lw + 2)
        for label, value in rows:
            wrapped = textwrap.wrap(value, avail) or [""]
            out.append(line(f"  {label:<{lw}}  {wrapped[0]}"))
            for cont in wrapped[1:]:
                out.append(line(" " * (2 + lw + 2) + cont))

    # notes block
    notes = str(data.get("notes") or "").strip()
    if notes:
        out.append(sep)
        out.append(line("  NOTES"))
        out.append(line())
        for para in notes.splitlines() or [""]:
            for wrapped in (textwrap.wrap(para, inner - 4) or [""]):
                out.append(line("  " + wrapped))

    # greets block
    greets = _split_list(data.get("greets") or [])
    if greets:
        out.append(sep)
        out.append(line("  GREETS"))
        out.append(line())
        for wrapped in textwrap.wrap("   ".join(greets), inner - 4):
            out.append(line("  " + wrapped))

    out.append(bot)
    return "\n".join(out) + "\n"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


# --- cli ---------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nfogen",
        description="Generate a scene-style ASCII .nfo file.",
    )
    p.add_argument("-c", "--config", metavar="PATH",
                   help="config file (.toml or .json) with field defaults")
    p.add_argument("-o", "--output", metavar="PATH",
                   help="write to PATH instead of stdout")
    p.add_argument("-g", "--group", help=f"release group (default {DEFAULT_GROUP})")
    p.add_argument("-s", "--site", help=f"site tag (default {DEFAULT_SITE})")
    p.add_argument("-w", "--width", type=int,
                   help=f"box width in chars (default {DEFAULT_WIDTH})")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="prompt for every field")
    p.add_argument("--no-input", action="store_true",
                   help="never prompt, even if fields are missing")
    for key, _, help_text, _ in FIELDS:
        p.add_argument(f"--{key}", help=help_text)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    # merge: config defaults, then any CLI flag that was supplied
    data = {k: config[k] for k, *_ in FIELDS if k in config and config[k] != ""}
    for key, *_ in FIELDS:
        val = getattr(args, key)
        if val is not None:
            data[key] = val
    data.setdefault("date", date.today().isoformat())

    group = args.group or config.get("group") or DEFAULT_GROUP
    site = args.site or config.get("site") or DEFAULT_SITE
    width = args.width or config.get("width") or DEFAULT_WIDTH

    interactive = sys.stdin.isatty() and not args.no_input
    if args.interactive:
        prompt_missing(data, force_all=True)
    elif not data.get("title") and interactive:
        prompt_missing(data, force_all=False)

    if not data.get("title"):
        sys.exit("nfogen: a title is required (use --title, a config, or -i)")

    text = render(data, group=group, site=site, width=width)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
