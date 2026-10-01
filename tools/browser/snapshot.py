"""Parsing of Playwright MCP accessibility snapshots into structured elements.

The snapshot is the faithful page representation Mamba reasons about: it carries
roles, accessible names, element references, values, and URLs. Mamba parses it
itself (rather than asking an MCP-provided *code* representation) so element
targeting can be semantic — by role/name/label/text — and never by coordinates.
"""

from __future__ import annotations

import re
from typing import Iterable

from .types import BrowserElement

_NODE_RE = re.compile(
    r"^(?P<indent>\s*)-\s+"
    r"(?P<role>[A-Za-z_][\w-]*)"
    r"(?:\s+\"(?P<name>(?:[^\"\\]|\\.)*)\")?"
    r"(?P<rest>.*)$"
)
_REF_RE = re.compile(r"\[ref=([^\]]+)\]")
_URL_RE = re.compile(r"^\s*-?\s*/url:\s*(\S+)\s*$")
_VALUE_RE = re.compile(r"\[value=\"(?P<value>(?:[^\"\\]|\\.)*)\"\]")
_LEVEL_RE = re.compile(r"\[level=(\d+)\]")
_DISABLED_RE = re.compile(r"\[disabled\]")
#: Roles that represent something a user can act on.
INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "link",
        "button",
        "textbox",
        "searchbox",
        "combobox",
        "listbox",
        "option",
        "checkbox",
        "radio",
        "switch",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "tab",
        "spinbutton",
        "slider",
        "treeitem",
    }
)


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def parse_snapshot(snapshot: str) -> tuple[list[BrowserElement], dict[str, str]]:
    """Parse an accessibility snapshot into elements and a ref description map.

    Returns ``(elements, refs)`` where ``refs`` maps an element reference to a
    short human-readable description, which is what gets handed to the provider
    when an action must target that element.
    """
    elements: list[BrowserElement] = []
    refs: dict[str, str] = {}
    if not snapshot:
        return elements, refs

    lines = snapshot.splitlines()
    # Track the nearest ancestor that carries a name, to give context to refs
    # such as bare text boxes inside a labelled form group.
    context_stack: list[tuple[int, str]] = []

    for index, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip() or line.strip() == "```yaml" or line.strip() == "```":
            continue

        match = _NODE_RE.match(line)
        if not match:
            continue

        indent = len(match.group("indent"))
        role = match.group("role")
        name = _unescape(match.group("name") or "")
        rest = match.group("rest") or ""

        while context_stack and context_stack[-1][0] >= indent:
            context_stack.pop()

        ref_match = _REF_RE.search(rest)
        if not ref_match:
            # A named node without a reference still gives context to its children.
            if name:
                context_stack.append((indent, name))
            continue

        ref = ref_match.group(1).strip()
        # An href lives on a `/url:` line *below* this node and *above* any
        # sibling, so the scan walks the node's own line frontier: a following
        # line is part of this node only while it is indented deeper than the
        # previous line in the walk.
        url = ""
        frontier = indent
        for following in lines[index + 1 : index + 12]:
            if not following.strip():
                break
            following_indent = len(following) - len(following.lstrip())
            if following_indent <= frontier:
                break
            url_match = _URL_RE.match(following)
            if url_match:
                url = url_match.group(1).strip()
                break
            frontier = following_indent

        value = ""
        value_match = _VALUE_RE.search(rest)
        if value_match:
            value = _unescape(value_match.group("value"))
        elif name and role in ("textbox", "searchbox", "combobox"):
            # Playwright renders a filled field's current content as its name.
            value = name

        element_type = ""
        type_match = re.search(r"\[type=([^\]]+)\]", rest)
        if type_match:
            element_type = type_match.group(1).strip()

        element = BrowserElement(
            ref=ref,
            role=role,
            name=name,
            url=url,
            element_type=element_type,
            value=value,
            disabled=bool(_DISABLED_RE.search(rest)),
            context=tuple(label for _, label in context_stack),
        )
        elements.append(element)

        description = element.describe()
        if element.context:
            description = f"{description} (in {' > '.join(element.context)})"
        refs[ref] = description

        # A named node with a reference also provides context to its children.
        if name:
            context_stack.append((indent, name))

    return elements, refs


def extract_visible_text(snapshot: str) -> str:
    """Approximate the page's visible text from its accessibility snapshot.

    Text-bearing nodes (paragraphs, headings, static text, table cells) are
    collected in order; purely structural nodes are skipped.
    """
    if not snapshot:
        return ""

    text_roles = {
        "paragraph",
        "heading",
        "text",
        "statictext",
        "cell",
        "columnheader",
        "rowheader",
        "listitem",
        "blockquote",
        "caption",
        "code",
        "emphasis",
        "strong",
        "term",
        "definition",
        "link",
        "button",
        "label",
    }
    collected: list[str] = []
    seen: set[str] = set()

    for raw in snapshot.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = _NODE_RE.match(line)
        if not match:
            continue
        role = match.group("role").lower()
        name = _unescape(match.group("name") or "")
        if role == "text" or not name:
            # Nodes such as `- paragraph [ref=e3]: some text` carry their content
            # after the colon, including plain `- text: ...` nodes.
            _, sep, tail = line.partition(":")
            tail = tail.strip()
            if sep and tail and not tail.startswith("//"):
                name = tail
        if not name:
            continue
        if role not in text_roles:
            continue
        normalized = name.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            collected.append(normalized)

    return "\n".join(collected)


def element_summary(elements: Iterable[BrowserElement], *, limit: int = 60) -> str:
    """Render a compact, planner-readable list of interactive elements."""
    lines: list[str] = []
    for element in list(elements)[:limit]:
        if element.role not in INTERACTIVE_ROLES and not element.url:
            continue
        lines.append(f"- [{element.ref}] {element.describe()}")
    return "\n".join(lines)


__all__ = [
    "INTERACTIVE_ROLES",
    "element_summary",
    "extract_visible_text",
    "parse_snapshot",
]
