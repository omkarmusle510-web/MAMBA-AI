"""Speech text normalization for voice output."""

from __future__ import annotations

import re


def normalize_speech_text(text: str) -> str:
    """Normalize formatted Markdown text into natural, fluent spoken text for TTS.

    Preserves semantic content (numbers, math symbols, filenames, paths, URLs)
    while removing Markdown presentation formatting (headings, bold, italics,
    bullet symbols, code fences, link brackets, and table pipes).
    """
    if not text or not text.strip():
        return ""

    lines = text.splitlines()
    normalized_lines: list[str] = []
    in_code_block = False
    code_block_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Handle fenced code blocks
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_block_lines = []
            else:
                in_code_block = False
                if code_block_lines:
                    # Summarize code or speak lines cleanly
                    code_content = " ".join(l.strip() for l in code_block_lines if l.strip())
                    if len(code_content) > 150:
                        code_content = code_content[:140] + "..."
                    normalized_lines.append(f"Code: {code_content}")
                code_block_lines = []
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        # Skip horizontal rules
        if re.match(r"^[-*_]{3,}$", stripped):
            continue

        # Skip table divider rows like |---|---|
        if re.match(r"^\|?[\s:-]+\|[\s:|-]+$", stripped):
            continue

        # Convert table rows (| Col1 | Col2 |)
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|") if c.strip()]
            if cells:
                normalized_lines.append(", ".join(cells) + ".")
            continue

        # Headings: # Heading -> Heading.
        heading_match = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading_match:
            heading_text = heading_match.group(1).strip()
            if not heading_text.endswith((".", "!", "?", ":")):
                heading_text += "."
            normalized_lines.append(heading_text)
            continue

        # Blockquotes: > Quote -> Quote
        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ").strip()

        # Unordered list items: - Item or * Item -> Item.
        list_match = re.match(r"^[-*+]\s+(.+)$", stripped)
        if list_match:
            item_text = list_match.group(1).strip()
            if not item_text.endswith((".", "!", "?", ",", ";")):
                item_text += "."
            normalized_lines.append(item_text)
            continue

        # Ordered list items: 1. Item -> 1, Item.
        num_list_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if num_list_match:
            num = num_list_match.group(1)
            item_text = num_list_match.group(2).strip()
            if not item_text.endswith((".", "!", "?", ",", ";")):
                item_text += "."
            normalized_lines.append(f"{num}, {item_text}")
            continue

        if stripped:
            normalized_lines.append(stripped)

    # Join lines with spaces
    joined = " ".join(normalized_lines)

    # Markdown links: [Link text](http://...) -> Link text
    joined = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", joined)

    # Inline code: `code` -> code
    joined = re.sub(r"`([^`]+)`", r"\1", joined)

    # Bold and Italic: ***text***, **text**, *text*, __text__, _text_
    joined = re.sub(r"\*\*\*(.*?)\*\*\*", r"\1", joined)
    joined = re.sub(r"\*\*(.*?)\*\*", r"\1", joined)
    joined = re.sub(r"\*(.*?)\*", r"\1", joined)
    joined = re.sub(r"___(.*?)___", r"\1", joined)
    joined = re.sub(r"__(.*?)__", r"\1", joined)

    # Normalize multiple punctuation or spaces
    joined = re.sub(r"\s+", " ", joined).strip()
    joined = re.sub(r"\s+([.,!?:])", r"\1", joined)
    joined = re.sub(r"([.,!?])\1+", r"\1", joined)

    return joined
