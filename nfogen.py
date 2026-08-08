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
from itertools import zip_longest
from pathlib import Path

# --- field schema ------------------------------------------------------------
# One source of truth. Drives argparse flags, interactive prompts, and render.
#   key    : internal name / config key / flag name
#   label  : shown in the rendered nfo (info rows) or as a section header
#   help   : argparse + prompt help text
#   kind   : "line"  scalar shown as an aligned INFO row
#            "list"  many values, shown space-joined in its own section
#            "block" free text, wrapped in its own section
#   col    : "L"/"R" panel column for "line" fields; None otherwise

FIELDS = [
    ("title",      "TITLE",      "release title (required)",            "line",  None),
    # --- release information (left panel) ---
    ("date",       "DATE",       "release date",                        "line",  "L"),
    ("source",     "SOURCE",     "source (BluRay, WEB-DL, CD, ...)",    "line",  "L"),
    ("cracker",    "CRACKER",    "cracker / cracked by",                "line",  "L"),
    ("supplier",   "SUPPLIER",   "supplier / supplied by",              "line",  "L"),
    ("packager",   "PACKAGER",   "packager / packed by",                "line",  "L"),
    ("protection", "PROTECTION", "protection type",                     "line",  "L"),
    ("install",    "INSTALL",    "install method",                      "line",  "L"),
    ("rating",     "RATING",     "rating",                              "line",  "L"),
    # --- game / media information (right panel) ---
    ("type",       "TYPE",       "release type (MOVIE, TV, APP, ...)",  "line",  "R"),
    ("publisher",  "PUBLISHER",  "publisher / vendor",                  "line",  "R"),
    ("format",     "FORMAT",     "container / format",                  "line",  "R"),
    ("disks",      "DISKS",      "number of disks / archives",          "line",  "R"),
    ("video",      "VIDEO",      "video codec / details",               "line",  "R"),
    ("audio",      "AUDIO",      "audio codec / details",               "line",  "R"),
    ("resolution", "RESOLUTION", "resolution",                          "line",  "R"),
    ("language",   "LANGUAGE",   "language(s)",                         "line",  "R"),
    ("genre",      "GENRE",      "genre",                               "line",  "R"),
    ("runtime",    "RUNTIME",    "runtime / duration",                  "line",  "R"),
    ("size",       "SIZE",       "total size",                          "line",  "R"),
    ("files",      "FILES",      "file count / listing",                "line",  "R"),
    ("url",        "URL",        "reference url (imdb, homepage, ...)", "line",  "R"),
    # --- free-form sections ---
    ("notes",      "NOTES",      "free-text notes / description",       "block", None),
    ("greets",     "GREETS",     "groups to greet (space/comma sep)",   "list",  None),
]
FIELD_KINDS = {k: kind for k, _, _, kind, _ in FIELDS}
INFO_KEYS = [k for k, _, _, kind, _ in FIELDS if kind == "line" and k != "title"]

DEFAULT_SITE = "krz.sh"
DEFAULT_GROUP = "KRZ"
DEFAULT_WIDTH = 79  # DOS 80-column standard, less one margin column
DEFAULT_STYLE = "double"
DEFAULT_FOOTER = (
    "SUPPORT THE COMPANIES THAT PRODUCE QUALITY SOFTWARE\n"
    "if you enjoyed this release, buy it!"
)
DEFAULT_LAYOUT = "rows"
PANEL_TITLES = ("Release Information", "Game Information")

# Box-drawing character sets. "single"/"double" are CP437-encodable; "block"
# is a solid fill frame. Keys: corners + horizontal/vertical + tee separators.
# jt/jm/jb are the down-tee, cross, and up-tee used where the panel's
# vertical column divider meets a horizontal rule.
STYLES = {
    "single": dict(tl="┌", tr="┐", bl="└", br="┘", h="─", v="│",
                   ls="├", rs="┤", jt="┬", jm="┼", jb="┴"),
    "double": dict(tl="╔", tr="╗", bl="╚", br="╝", h="═", v="║",
                   ls="╠", rs="╣", jt="╦", jm="╬", jb="╩"),
    "block":  dict(tl="█", tr="█", bl="█", br="█", h="█", v="█",
                   ls="█", rs="█", jt="█", jm="█", jb="█"),
}

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
        _, label, help_text, _, _ = next(f for f in FIELDS if f[0] == key)
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


def render(data: dict, group: str, site: str, width: int,
           style: str = DEFAULT_STYLE, presents: bool = False,
           footer: str | None = None, layout: str = DEFAULT_LAYOUT,
           logo: str | None = None,
           panel_titles: tuple[str, str] = PANEL_TITLES) -> str:
    s = STYLES[style]
    h, v = s["h"], s["v"]
    inner = width - 4          # single-column content width (1 space padding)
    cl = (width - 7) // 2      # left panel content width
    cr = (width - 7) - cl      # right panel content width
    rfill = width - cl - 5     # h-run right of the divider in a rule

    def hrule(left: str, right: str, joint: str | None = None) -> str:
        if joint is None:
            return left + h * (width - 2) + right
        return left + h * (cl + 2) + joint + h * rfill + right

    def line(content: str = "") -> str:
        return f"{v} " + content.ljust(inner) + f" {v}"

    def center(content: str) -> str:
        return f"{v} " + content.center(inner) + f" {v}"

    def prow(left: str, right: str, centered: bool = False) -> str:
        fn = str.center if centered else str.ljust
        return (f"{v} " + fn(_clip(left, cl), cl)
                + f" {v} " + fn(_clip(right, cr), cr) + f" {v}")

    out: list[str] = []

    # logo art above the box, block-centered to the full width
    if logo:
        art = logo.rstrip("\n").split("\n")
        pad = max((width - max((len(a) for a in art), default=0)) // 2, 0)
        out.extend((" " * pad + a).rstrip() for a in art)
        out.append("")

    out.append(hrule(s["tl"], s["tr"]))

    # header: spaced title, then group tag + site, optional presents banner
    title = str(data.get("title") or "UNTITLED").upper()
    spaced = " ".join(title)
    heading = spaced if len(spaced) <= inner else title
    out.append(center(_clip(heading, inner)))
    out.append(center(_clip(f"[ {group} ]   {site}", inner)))
    if presents:
        out.append(center(_clip(f"-={{ {group.upper()} proudly presents }}=-", inner)))

    line_fields = [(k, lbl, col) for k, lbl, _, kind, col in FIELDS
                   if kind == "line" and k != "title" and data.get(k)]
    prev_panel = False

    def sep_before() -> str:
        nonlocal prev_panel
        joint = s["jb"] if prev_panel else None
        prev_panel = False
        return hrule(s["ls"], s["rs"], joint)

    # info: two-column panel or single-column rows
    if layout == "panel" and line_fields:
        left = [(lbl, str(data[k])) for k, lbl, col in line_fields if col == "L"]
        right = [(lbl, str(data[k])) for k, lbl, col in line_fields if col == "R"]
        lw_l = max((len(lbl) for lbl, _ in left), default=0)
        lw_r = max((len(lbl) for lbl, _ in right), default=0)

        def cell(pair, lw):
            return f" {pair[0]:<{lw}} : {pair[1]}" if pair else ""

        out.append(hrule(s["ls"], s["rs"], s["jt"]))
        out.append(prow(panel_titles[0], panel_titles[1], centered=True))
        out.append(hrule(s["ls"], s["rs"], s["jm"]))
        for lp, rp in zip_longest(left, right):
            out.append(prow(cell(lp, lw_l), cell(rp, lw_r)))
        prev_panel = True
    elif line_fields:
        out.append(hrule(s["ls"], s["rs"]))
        lw = max(len(lbl) for _, lbl, _ in line_fields)
        avail = inner - (2 + lw + 2)
        for k, lbl, _ in line_fields:
            wrapped = textwrap.wrap(str(data[k]), avail) or [""]
            out.append(line(f"  {lbl:<{lw}}  {wrapped[0]}"))
            for cont in wrapped[1:]:
                out.append(line(" " * (2 + lw + 2) + cont))

    # notes block
    notes = str(data.get("notes") or "").strip()
    if notes:
        out.append(sep_before())
        out.append(line("  NOTES"))
        out.append(line())
        for para in notes.splitlines() or [""]:
            for wrapped in (textwrap.wrap(para, inner - 4) or [""]):
                out.append(line("  " + wrapped))

    # greets block
    greets = _split_list(data.get("greets") or [])
    if greets:
        out.append(sep_before())
        out.append(line("  GREETS"))
        out.append(line())
        for wrapped in textwrap.wrap("   ".join(greets), inner - 4):
            out.append(line("  " + wrapped))

    # footer disclaimer, centered
    if footer:
        out.append(sep_before())
        out.append(line())
        for para in footer.splitlines():
            for wrapped in (textwrap.wrap(para, inner - 4) or [""]):
                out.append(center(wrapped))
        out.append(line())

    out.append(hrule(s["bl"], s["br"], s["jb"] if prev_panel else None))
    return "\n".join(out) + "\n"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 2] + ".."


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
    p.add_argument("--style", choices=sorted(STYLES),
                   help=f"box-drawing style (default {DEFAULT_STYLE})")
    p.add_argument("--layout", choices=["rows", "panel"],
                   help=f"info layout (default {DEFAULT_LAYOUT})")
    p.add_argument("--logo", metavar="PATH",
                   help="art file to place above the box")
    p.add_argument("--logo-encoding", choices=["cp437", "utf8"],
                   help="encoding of the logo file (default cp437)")
    p.add_argument("--encoding", choices=["utf8", "cp437"], default="utf8",
                   help="output encoding (default utf8; cp437 for true DOS nfos)")
    p.add_argument("--presents", action=argparse.BooleanOptionalAction,
                   default=None, help="show the 'proudly presents' banner")
    p.add_argument("--footer", action=argparse.BooleanOptionalAction,
                   default=None, help="show the closing disclaimer block")
    p.add_argument("--footer-text", help="custom disclaimer text")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="prompt for every field")
    p.add_argument("--no-input", action="store_true",
                   help="never prompt, even if fields are missing")
    for key, _, help_text, _, _ in FIELDS:
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
    style = args.style or config.get("style") or DEFAULT_STYLE

    def resolve(flag, key, default):
        if flag is not None:
            return flag
        return config.get(key, default)

    presents = resolve(args.presents, "presents", False)
    show_footer = resolve(args.footer, "footer", False)
    footer_text = args.footer_text or config.get("footer_text") or DEFAULT_FOOTER
    footer = footer_text if show_footer else None

    layout = args.layout or config.get("layout") or DEFAULT_LAYOUT
    panel_titles = (config.get("panel_left") or PANEL_TITLES[0],
                    config.get("panel_right") or PANEL_TITLES[1])

    logo = None
    logo_path = args.logo or config.get("logo")
    if logo_path:
        enc = args.logo_encoding or config.get("logo_encoding") or "cp437"
        try:
            logo = Path(logo_path).read_text(encoding=enc, errors="replace")
        except FileNotFoundError:
            sys.exit(f"nfogen: logo not found: {logo_path}")

    interactive = sys.stdin.isatty() and not args.no_input
    if args.interactive:
        prompt_missing(data, force_all=True)
    elif not data.get("title") and interactive:
        prompt_missing(data, force_all=False)

    if not data.get("title"):
        sys.exit("nfogen: a title is required (use --title, a config, or -i)")

    text = render(data, group=group, site=site, width=width, style=style,
                  presents=presents, footer=footer, layout=layout,
                  logo=logo, panel_titles=panel_titles)

    if args.encoding == "cp437":
        raw = text.encode("cp437", errors="replace")
        if args.output:
            Path(args.output).write_bytes(raw)
        else:
            sys.stdout.buffer.write(raw)
    elif args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
