"""Minimal YAML-subset parser for bitbucket-pipelines.yml.

Full YAML needs a third-party lib; we only need the `pipelines: custom:` block —
pipeline names and their `variables:` (name / default / allowed-values). So this
is an indentation scanner tuned to that shape, robust to the varying indentation
seen in real files.
"""

import re

from .model import Variable, Target


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_skippable(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _scalar(raw: str) -> str:
    """Strip surrounding quotes and inline comments from a YAML scalar."""
    raw = raw.strip()
    if raw and raw[0] not in "\"'":
        raw = raw.split("#", 1)[0].strip()
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        raw = raw[1:-1]
    return raw


def _find_key(lines, key, start, end, max_indent=None):
    """Index of the first `key:` (optionally `- key:`) line in [start, end)."""
    pat = re.compile(r"^-?\s*" + re.escape(key) + r"\s*:\s*(#.*)?$")
    for i in range(start, end):
        if _is_skippable(lines[i]):
            continue
        if max_indent is not None and _indent(lines[i]) > max_indent:
            continue
        if pat.match(lines[i].strip()):
            return i
    return -1


def _parse_variables(lines, start, end) -> list:
    """Parse the `variables:` sub-block within a pipeline's [start, end)."""
    var_idx = _find_key(lines, "variables", start, end)
    if var_idx == -1:
        return []
    var_indent = _indent(lines[var_idx])

    block = []
    for i in range(var_idx + 1, end):
        if _is_skippable(lines[i]):
            continue
        if _indent(lines[i]) <= var_indent:
            break
        block.append(i)
    if not block:
        return []

    name_line_idxs = [
        i for i in block if re.match(r"^-\s*name\s*:", lines[i].strip())
    ]

    variables = []
    for a, k in enumerate(name_line_idxs):
        m = re.match(r"^-\s*name\s*:\s*(.+?)\s*(#.*)?$", lines[k].strip())
        vname = _scalar(m.group(1))
        sub_end = name_line_idxs[a + 1] if a + 1 < len(name_line_idxs) else block[-1] + 1

        default = None
        allowed = []
        p = k + 1
        while p < sub_end:
            if _is_skippable(lines[p]):
                p += 1
                continue
            s = lines[p].strip()
            md = re.match(r"^default\s*:\s*(.+?)\s*$", s)
            if md:
                default = _scalar(md.group(1))
            if re.match(r"^allowed-values\s*:", s):
                av_indent = _indent(lines[p])
                q = p + 1
                while q < sub_end:
                    if _is_skippable(lines[q]):
                        q += 1
                        continue
                    if _indent(lines[q]) <= av_indent:
                        break
                    mv = re.match(r"^-\s*(.+?)\s*$", lines[q].strip())
                    if mv:
                        allowed.append(_scalar(mv.group(1)))
                    q += 1
            p += 1
        variables.append(Variable(name=vname, default=default, allowed_values=allowed))
    return variables


def classify_env(pipeline_name: str) -> str:
    """Prod if the name signals production/canary; QA otherwise."""
    up = pipeline_name.upper()
    return "prod" if ("PROD" in up or "CANARY" in up) else "qa"


def parse_pipelines_yml(text: str) -> list:
    """Extract custom pipeline Targets (name + variables + env) from the yml."""
    lines = text.split("\n")
    n = len(lines)

    pipelines_idx = _find_key(lines, "pipelines", 0, n, max_indent=0)
    if pipelines_idx == -1:
        return []
    pipelines_indent = _indent(lines[pipelines_idx])

    custom_idx = -1
    for i in range(pipelines_idx + 1, n):
        if _is_skippable(lines[i]):
            continue
        if _indent(lines[i]) <= pipelines_indent:
            break  # left the pipelines block
        if re.match(r"^custom\s*:\s*$", lines[i].strip()):
            custom_idx = i
            break
    if custom_idx == -1:
        return []
    custom_indent = _indent(lines[custom_idx])

    end = n
    pipeline_indent = None
    for i in range(custom_idx + 1, n):
        if _is_skippable(lines[i]):
            continue
        ind = _indent(lines[i])
        if ind <= custom_indent:
            end = i
            break
        if pipeline_indent is None:
            pipeline_indent = ind
    if pipeline_indent is None:
        return []

    name_positions = []
    for i in range(custom_idx + 1, end):
        if _is_skippable(lines[i]):
            continue
        if _indent(lines[i]) != pipeline_indent:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(#.*)?$", lines[i].strip())
        if m:
            name_positions.append((i, m.group(1)))

    targets = []
    for j, (idx, name) in enumerate(name_positions):
        block_start = idx + 1
        block_end = name_positions[j + 1][0] if j + 1 < len(name_positions) else end
        variables = _parse_variables(lines, block_start, block_end)
        targets.append(Target(name=name, env=classify_env(name), variables=variables))
    return targets
