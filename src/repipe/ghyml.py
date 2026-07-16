"""Minimal parser for GitHub Actions workflow files (.github/workflows/*.yml).

Zero-dep, like the bitbucket parser: we only need `name:` and the
`on: workflow_dispatch: inputs:` block, so this is an indentation scanner
tuned to that shape — not a general YAML parser. Reuses the ymlparse helpers.

Assumptions (documented, best-effort): standard space indentation; `on:` is a
top-level key (`on:` or the quoted `"on":`); inputs live under
`workflow_dispatch.inputs`. Only workflows that declare `workflow_dispatch` are
returned — those are the ones repipe can trigger on demand.
"""

import os
import re

from .model import Target, Variable
from .ymlparse import _indent, _is_skippable, _scalar, _find_key, classify_env


def _block_end(lines, start, indent, n):
    """First index >= start whose indent is <= `indent` (end of a nested block)."""
    for i in range(start, n):
        if _is_skippable(lines[i]):
            continue
        if _indent(lines[i]) <= indent:
            return i
    return n


def _find_on(lines, n):
    """Index of the top-level `on:` (or `"on":`) key, or -1."""
    for i in range(n):
        if _is_skippable(lines[i]) or _indent(lines[i]) != 0:
            continue
        if re.match(r'^"?on"?\s*:', lines[i].strip()):
            return i
    return -1


def _find_dispatch(lines, on_idx, n):
    """Return (present, wd_idx). wd_idx is the `workflow_dispatch:` line index
    when it's a mapping (may carry inputs), else -1 (inline/list form)."""
    m = re.match(r'^\s*"?on"?\s*:\s*(\S.*?)\s*(#.*)?$', lines[on_idx])
    if m and m.group(1):                      # inline: on: X  /  on: [a, b]
        inline = m.group(1).strip().strip("[]")
        tokens = [t for t in re.split(r"[,\s]+", inline) if t]
        return ("workflow_dispatch" in tokens), -1
    on_indent = _indent(lines[on_idx])
    end = _block_end(lines, on_idx + 1, on_indent, n)
    for i in range(on_idx + 1, end):
        if _is_skippable(lines[i]):
            continue
        s = lines[i].strip()
        if re.match(r"^workflow_dispatch\s*:", s):
            return True, i
        if re.match(r"^-\s*workflow_dispatch\s*$", s):   # list item, no inputs
            return True, -1
    return False, -1


def _parse_inputs(lines, wd_idx, n) -> list:
    """Parse `workflow_dispatch.inputs` into Variables."""
    wd_indent = _indent(lines[wd_idx])
    wd_end = _block_end(lines, wd_idx + 1, wd_indent, n)
    inputs_idx = _find_key(lines, "inputs", wd_idx + 1, wd_end)
    if inputs_idx == -1:
        return []
    inputs_indent = _indent(lines[inputs_idx])
    iend = _block_end(lines, inputs_idx + 1, inputs_indent, n)

    # Input names are the keys at the first indent level under `inputs:`.
    names, name_indent = [], None
    for i in range(inputs_idx + 1, iend):
        if _is_skippable(lines[i]):
            continue
        ind = _indent(lines[i])
        if name_indent is None:
            name_indent = ind
        if ind != name_indent:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(#.*)?$", lines[i].strip())
        if m:
            names.append((i, m.group(1)))

    variables = []
    for j, (idx, name) in enumerate(names):
        b_end = names[j + 1][0] if j + 1 < len(names) else iend
        default, vtype, options = None, None, []
        p = idx + 1
        while p < b_end:
            if _is_skippable(lines[p]):
                p += 1
                continue
            s = lines[p].strip()
            md = re.match(r"^default\s*:\s*(.+?)\s*$", s)
            if md:
                default = _scalar(md.group(1))
            mt = re.match(r"^type\s*:\s*(.+?)\s*$", s)
            if mt:
                vtype = _scalar(mt.group(1))
            if re.match(r"^options\s*:", s):
                o_indent = _indent(lines[p])
                q = p + 1
                while q < b_end:
                    if _is_skippable(lines[q]):
                        q += 1
                        continue
                    if _indent(lines[q]) <= o_indent:
                        break
                    mv = re.match(r"^-\s*(.+?)\s*$", lines[q].strip())
                    if mv:
                        options.append(_scalar(mv.group(1)))
                    q += 1
            p += 1
        allowed = options if vtype == "choice" else (
            ["true", "false"] if vtype == "boolean" else []
        )
        variables.append(Variable(name=name, default=default, allowed_values=allowed))
    return variables


def parse_workflow(text: str, filename: str):
    """Parse one workflow file → a Target, or None if it has no workflow_dispatch."""
    lines = text.split("\n")
    n = len(lines)

    display = None
    for i in range(n):                       # top-level scalar `name: <value>`
        if _is_skippable(lines[i]) or _indent(lines[i]) != 0:
            continue
        m = re.match(r"^name\s*:\s*(\S.*?)\s*(#.*)?$", lines[i].strip())
        if m:
            display = _scalar(m.group(1))
            break
    display = display or os.path.splitext(filename)[0]

    on_idx = _find_on(lines, n)
    if on_idx == -1:
        return None
    present, wd_idx = _find_dispatch(lines, on_idx, n)
    if not present:
        return None

    variables = _parse_inputs(lines, wd_idx, n) if wd_idx != -1 else []
    # `key` is the workflow filename — the GitHub dispatch API keys off it.
    return Target(name=display, env=classify_env(display), key=filename,
                  variables=variables)


def parse_workflows(wf_dir: str) -> list:
    """Parse every triggerable workflow in a .github/workflows directory."""
    targets = []
    for fn in sorted(os.listdir(wf_dir)):
        if not fn.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(wf_dir, fn), "r", encoding="utf-8") as f:
            t = parse_workflow(f.read(), fn)
        if t:
            targets.append(t)
    return targets
