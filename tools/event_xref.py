#!/usr/bin/env python3
"""Event-dispatch cross-reference generator for KOReader.

KOReader dispatches UI events through string-composed method names:
``Event:new(name)`` derives ``handler = "on"..name`` (frontend/ui/event.lua)
and EventListener looks it up as ``self[event.handler]``
(frontend/ui/widget/eventlistener.lua). That indirection defeats both LSP
navigation and naive grep. This tool statically cross-references every event
EMIT site with every ``on<Name>`` HANDLER definition and writes a committed,
greppable index.

Emit idioms covered:
  - literal ``Event:new("Name", ...)`` (incl. via sendEvent/broadcastEvent/
    handleEvent wrappers)
  - dispatcher actions: ``event = "Name"`` fields (frontend/dispatcher.lua
    settingsList + Dispatcher:registerAction tables anywhere)
  - config options: ``event = "Name"`` fields under frontend/ui/data/
  - ``key_events`` / ``ges_events`` tables (event fired is the table key
    unless an ``event = "Override"`` field is present; see
    frontend/ui/widget/container/inputcontainer.lua)
  - bare-string ``sendEvent("Name")`` / ``broadcastEvent("Name")`` (flagged:
    a string where an Event object is expected is likely a bug)
  - dynamic ``Event:new(<expr>)`` sites are listed as unresolved, never
    silently dropped.

Regenerate after adding/renaming events or on* handlers:

    python tools/event_xref.py

Verify the committed index is current (CI-friendly; ignores the commit line):

    python tools/event_xref.py --check
"""

import argparse
import io
import os
import re
import subprocess
import sys
from collections import defaultdict

DEFAULT_OUT = os.path.join("doc", "events_xref.md")

SCAN_ROOTS = ["frontend", "plugins"]
SCAN_FILES = ["reader.lua"]

RX_EVENT_NEW = re.compile(r"Event:new\s*\(\s*(.)")
RX_EVENT_NEW_LITERAL = re.compile(r"""Event:new\s*\(\s*(["'])([A-Za-z_]\w*)\1""")
RX_EVENT_NEW_DYNAMIC = re.compile(r"Event:new\s*\(\s*([^\s\"'][^,)]*)")
RX_BARE_STRING_SEND = re.compile(
    r"""(sendEvent|broadcastEvent|handleEvent)\s*\(\s*(["'])([A-Za-z_]\w*)\2\s*[,)]"""
)
RX_EVENT_FIELD = re.compile(r"""\bevent\s*=\s*(["'])([A-Za-z_]\w*)\1""")
RX_HANDLER_FUNC = re.compile(r"^\s*function\s+([A-Za-z_][\w.]*)[:.](on[A-Z]\w*)\s*\(")
RX_HANDLER_TABLE = re.compile(r"\b(on[A-Z]\w*)\s*=\s*function\b")
RX_DYNAMIC_HANDLER = re.compile(r"""\[\s*["']on["']\s*\.\.|["']on["']\s*\.\.""")
RX_BLOCK_OPEN = re.compile(r"\b(key_events|ges_events)\s*=\s*\{")
RX_BLOCK_ENTRY_ASSIGN = re.compile(
    r"\b(?:key_events|ges_events)\.([A-Za-z_]\w*)\s*=\s*\{"
)
RX_ENTRY_KEY = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*\{")
RX_WRAPPER = re.compile(r"\b(sendEvent|broadcastEvent|handleEvent|handleInputEvent)\s*\($")


def strip_comment(line):
    """Remove a -- line comment, respecting simple single/double quotes."""
    in_str = None
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'":
            in_str = c
        elif c == "-" and i + 1 < n and line[i + 1] == "-":
            return line[:i]
        i += 1
    return line


def brace_delta(line):
    """Net {..} depth change of a line, ignoring braces inside quotes."""
    depth = 0
    in_str = None
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'":
            in_str = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return depth


class Site:
    __slots__ = ("path", "line", "kind", "extra")

    def __init__(self, path, line, kind, extra=""):
        self.path = path
        self.line = line
        self.kind = kind
        self.extra = extra

    def key(self):
        return (self.path, self.line, self.kind)


def collect_files(repo_root):
    files = []
    for name in SCAN_FILES:
        p = os.path.join(repo_root, name)
        if os.path.isfile(p):
            files.append(name)
    for root in SCAN_ROOTS:
        base = os.path.join(repo_root, root)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for fn in sorted(filenames):
                if fn.endswith(".lua"):
                    rel = os.path.relpath(os.path.join(dirpath, fn), repo_root)
                    files.append(rel.replace(os.sep, "/"))
    return files


def classify_wrapper(lines, idx, col):
    """Kind for an Event:new at lines[idx] offset col: check the same line
    before the match, then the previous line's tail for a wrapper call."""
    before = lines[idx][:col]
    for name, kind in (
        ("broadcastEvent", "broadcastEvent"),
        ("sendEvent", "sendEvent"),
        ("handleInputEvent", "handleInputEvent"),
        ("handleEvent", "handleEvent"),
    ):
        if name + "(" in before.replace(" (", "("):
            return kind
    if idx > 0 and RX_WRAPPER.search(strip_comment(lines[idx - 1]).rstrip()):
        return RX_WRAPPER.search(strip_comment(lines[idx - 1]).rstrip()).group(1)
    return "literal"


class Scanner:
    def __init__(self, repo_root):
        self.repo_root = repo_root
        self.emits = defaultdict(list)          # name -> [Site]
        self.handlers = defaultdict(list)       # name -> [Site] (extra=class)
        self.dynamic_emits = []                 # [Site] extra=expression
        self.dynamic_handlers = []              # [Site] extra=line text
        self.unparsed_blocks = []               # [Site]
        self.n_event_new = 0
        self.n_func_handlers = 0
        self.n_table_handlers = 0

    def scan_file(self, rel):
        path = os.path.join(self.repo_root, rel)
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.read().splitlines()
        lines = [strip_comment(l) for l in raw_lines]
        in_dispatcher = rel == "frontend/dispatcher.lua"
        in_uidata = rel.startswith("frontend/ui/data/")
        block_lines = self.scan_key_ges_blocks(rel, lines)

        for idx, line in enumerate(lines):
            lineno = idx + 1

            # --- Event:new sites ---------------------------------------
            for m in RX_EVENT_NEW.finditer(line):
                if re.match(r"^\s*function\s+Event:new", line):
                    continue  # the definition in frontend/ui/event.lua
                self.n_event_new += 1
                lit = RX_EVENT_NEW_LITERAL.match(line, m.start())
                if lit:
                    kind = classify_wrapper(lines, idx, m.start())
                    self.emits[lit.group(2)].append(Site(rel, lineno, kind))
                else:
                    dyn = RX_EVENT_NEW_DYNAMIC.match(line, m.start())
                    expr = dyn.group(1).strip() if dyn else "?"
                    self.dynamic_emits.append(
                        Site(rel, lineno, "dynamic", expr)
                    )

            # --- bare-string sendEvent/broadcastEvent -------------------
            for m in RX_BARE_STRING_SEND.finditer(line):
                self.emits[m.group(3)].append(
                    Site(rel, lineno, m.group(1) + "-bare-string",
                         "string passed where an Event object is expected; likely a bug")
                )

            # --- event = "Name" fields ----------------------------------
            if lineno not in block_lines:
                for m in RX_EVENT_FIELD.finditer(line):
                    if in_dispatcher:
                        kind = "dispatcher-action"
                    elif in_uidata:
                        kind = "config-option"
                    else:
                        kind = "event-field"
                    self.emits[m.group(2)].append(Site(rel, lineno, kind))

            # --- handler definitions ------------------------------------
            m = RX_HANDLER_FUNC.match(line)
            if m:
                self.n_func_handlers += 1
                self.handlers[m.group(2)].append(
                    Site(rel, lineno, "high", m.group(1))
                )
            else:
                for m in RX_HANDLER_TABLE.finditer(line):
                    self.n_table_handlers += 1
                    self.handlers[m.group(1)].append(
                        Site(rel, lineno, "low", "")
                    )

            # --- dynamic "on" .. construction ---------------------------
            if RX_DYNAMIC_HANDLER.search(line):
                mode = "creates handler" if re.search(r"\]\s*=", line) else "lookup"
                self.dynamic_handlers.append(
                    Site(rel, lineno, mode, raw_lines[idx].strip())
                )

    def scan_key_ges_blocks(self, rel, lines):
        """Parse key_events/ges_events tables. Returns the set of line
        numbers covered by the blocks (so the event-field scan skips them —
        entry-level event= overrides are recorded here instead)."""
        covered = set()
        idx = 0
        n = len(lines)
        while idx < n:
            line = lines[idx]
            m = RX_BLOCK_ENTRY_ASSIGN.search(line)
            if m:
                end = self.parse_entry(rel, lines, idx, m.group(1),
                                       "key_events" if "key_events" in m.group(0)
                                       else "ges_events", covered)
                idx = end + 1
                continue
            m = RX_BLOCK_OPEN.search(line)
            if m:
                kind = m.group(1)
                end = self.parse_block(rel, lines, idx, m.end() - 1, kind, covered)
                idx = end + 1
                continue
            idx += 1
        return covered

    def parse_block(self, rel, lines, start_idx, open_col, kind, covered):
        """Parse a `key_events = { ... }` block; depth-1 keys are events."""
        depth = 0
        entry_name = None
        entry_override = None
        entry_line = None
        n = len(lines)
        idx = start_idx
        while idx < n and idx < start_idx + 500:
            seg = lines[idx][open_col:] if idx == start_idx else lines[idx]
            covered.add(idx + 1)
            if idx > start_idx and depth == 1 and entry_name is None:
                km = RX_ENTRY_KEY.match(seg)
                if km:
                    entry_name = km.group(1)
                    entry_line = idx + 1
                    entry_override = None
            if entry_name is not None:
                om = RX_EVENT_FIELD.search(seg)
                if om:
                    entry_override = om.group(2)
            depth += brace_delta(seg)
            if entry_name is not None and depth <= 1:
                self.emits[entry_override or entry_name].append(
                    Site(rel, entry_line, kind,
                         "via table key %s" % entry_name if entry_override
                         else "")
                )
                entry_name = None
            if depth <= 0:
                if idx == start_idx:  # single-line block
                    for km in re.finditer(r"([A-Za-z_]\w*)\s*=\s*\{", seg[1:]):
                        self.emits[km.group(1)].append(
                            Site(rel, idx + 1, kind)
                        )
                return idx
            idx += 1
        self.unparsed_blocks.append(Site(rel, start_idx + 1, kind))
        return min(start_idx + 500, n - 1)

    def parse_entry(self, rel, lines, start_idx, name, kind, covered):
        """Parse `self.key_events.Foo = { ... }` single-entry assignment."""
        depth = 0
        override = None
        n = len(lines)
        idx = start_idx
        while idx < n and idx < start_idx + 100:
            line = lines[idx]
            covered.add(idx + 1)
            om = RX_EVENT_FIELD.search(line)
            if om:
                override = om.group(2)
            depth += brace_delta(line)
            if idx > start_idx or depth <= 0:
                if depth <= 0:
                    self.emits[override or name].append(
                        Site(rel, start_idx + 1, kind,
                             "" if not override else "via table key %s" % name)
                    )
                    return idx
            idx += 1
        self.unparsed_blocks.append(Site(rel, start_idx + 1, kind))
        return start_idx


def git_commit(repo_root):
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def fmt_site(s):
    extra = " — %s" % s.extra if s.extra and s.kind not in ("high", "low") else ""
    return "- %s:%d (%s)%s" % (s.path, s.line, s.kind, extra)


def fmt_handler(s):
    cls = s.extra + "." if s.extra else ""
    return "- %s%s — %s:%d (%s)" % (cls, "", s.path, s.line, s.kind)


def render(scanner, repo_root):
    emits = scanner.emits
    handlers = scanner.handlers
    handler_events = {h[2:]: h for h in handlers}  # strip "on"

    names = sorted(set(emits) | set(handler_events))
    no_handler = sorted(n for n in emits if n not in handler_events)
    no_emitter = sorted(n for n in handler_events if n not in emits)

    out = []
    w = out.append
    w("# Event-dispatch cross-reference")
    w("")
    w("Generated by `tools/event_xref.py` — do not edit by hand.")
    w("Regenerate with: `python tools/event_xref.py` (from the repo root).")
    w("Source commit: %s" % git_commit(repo_root))
    w("")
    w("An event `Name` is delivered to methods called `onName` "
      "(`frontend/ui/event.lua` composes `handler = \"on\"..name`; "
      "`frontend/ui/widget/eventlistener.lua` dispatches `self[event.handler]`). "
      "For `key_events`/`ges_events` sites the fired event is the table key "
      "unless an `event = \"Override\"` field is present "
      "(`frontend/ui/widget/container/inputcontainer.lua`).")
    w("")
    w("Emitter kinds: literal, sendEvent, broadcastEvent, handleEvent, "
      "handleInputEvent, dispatcher-action (frontend/dispatcher.lua "
      "settingsList), config-option (frontend/ui/data), event-field "
      "(other `event = \"X\"` tables, e.g. Dispatcher:registerAction), "
      "key_events, ges_events, *-bare-string (suspicious). Handler "
      "confidence: high = `function Class:onName`, low = `onName = function` "
      "(may be a plain callback, not an event handler).")
    w("")
    w("## Summary")
    w("")
    w("- Distinct event names: %d" % len(names))
    w("- Emit sites: %d (plus %d unresolved dynamic)" % (
        sum(len(v) for v in emits.values()), len(scanner.dynamic_emits)))
    w("- Handler definitions: %d function-style, %d table-style; "
      "%d distinct `on*` names" % (
        scanner.n_func_handlers, scanner.n_table_handlers, len(handlers)))
    w("- Events with no handler found: %d; handlers with no static "
      "emitter: %d" % (len(no_handler), len(no_emitter)))
    if scanner.unparsed_blocks:
        w("- Unparsed key_events/ges_events sites: %d (listed below)"
          % len(scanner.unparsed_blocks))
    w("")
    w("## Events")
    w("")
    for name in names:
        w("### %s" % name)
        w("")
        w("Emitters:")
        sites = sorted(emits.get(name, []), key=Site.key)
        if sites:
            for s in sites:
                w(fmt_site(s))
        else:
            w("- (none found statically — dynamic emit, key/gesture, or "
              "external caller)")
        w("")
        w("Handlers (on%s):" % name)
        hsites = sorted(handlers.get("on" + name, []), key=Site.key)
        if hsites:
            for s in hsites:
                cls = ("%s." % s.extra) if s.extra else ""
                w("- %son%s — %s:%d (%s)" % (cls, name, s.path, s.line, s.kind))
        else:
            w("- (none found)")
        w("")
    w("## Unresolved dynamic emits")
    w("")
    w("`Event:new(<expression>)` with a non-literal name; resolve by reading "
      "the site.")
    w("")
    for s in sorted(scanner.dynamic_emits, key=Site.key):
        w("- %s:%d — `Event:new(%s`" % (s.path, s.line, s.extra))
    w("")
    w("## Dynamic handler construction/lookup sites")
    w("")
    for s in sorted(scanner.dynamic_handlers, key=Site.key):
        w("- %s:%d (%s) — `%s`" % (s.path, s.line, s.kind, s.extra))
    w("")
    if scanner.unparsed_blocks:
        w("## Unparsed key_events/ges_events sites")
        w("")
        for s in sorted(scanner.unparsed_blocks, key=Site.key):
            w("- %s:%d (%s)" % (s.path, s.line, s.kind))
        w("")
    w("## Events with no handler found")
    w("")
    w("Emitted, but no static `on<Name>` definition was found (may be "
      "handled by a dynamically created handler, or dead).")
    w("")
    for name in no_handler:
        w("- %s (%d emit sites)" % (name, len(emits[name])))
    w("")
    w("## Handlers with no static emitter")
    w("")
    w("Defined, but no static emit site was found (may be fired via a "
      "dynamic emit, a key/gesture table this tool missed, or — for "
      "low-confidence entries — just a callback that is not an event "
      "handler at all).")
    w("")
    for name in no_emitter:
        sites = handlers["on" + name]
        conf = "low-confidence only — possibly a callback" \
            if all(s.kind == "low" for s in sites) else "has function-style definition"
        w("- %s (%d definitions; %s)" % (name, len(sites), conf))
    w("")
    return "\n".join(out)


def comparable(text):
    return "\n".join(
        l for l in text.splitlines() if not l.startswith("Source commit:")
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed index is stale "
                         "(comparison ignores the Source commit line)")
    args = ap.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    if not os.path.isdir(os.path.join(repo_root, "frontend")):
        sys.exit("error: %s does not look like the KOReader repo root"
                 % repo_root)

    scanner = Scanner(repo_root)
    for rel in collect_files(repo_root):
        scanner.scan_file(rel)
    content = render(scanner, repo_root) + "\n"

    out_path = os.path.join(repo_root, args.out)
    if args.check:
        try:
            with io.open(out_path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            sys.exit("stale: %s does not exist — run tools/event_xref.py"
                     % args.out)
        if comparable(existing) != comparable(content):
            sys.exit("stale: %s is out of date — run tools/event_xref.py"
                     % args.out)
        print("ok: %s is current" % args.out)
        return
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote %s (%d event names, %d emit sites, %d handler defs)" % (
        args.out, len(set(scanner.emits) | {h[2:] for h in scanner.handlers}),
        sum(len(v) for v in scanner.emits.values()),
        scanner.n_func_handlers + scanner.n_table_handlers))


if __name__ == "__main__":
    main()
