"""A very small Markdown subset renderer.

Enough for the findings text that ships with a benchmark board: headings, paragraphs,
lists, tables, blockquotes, bold/italic/code. Not a general Markdown implementation.
"""
from __future__ import annotations

import html
import re

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = INLINE_CODE.sub(r"<code>\1</code>", out)
    out = BOLD.sub(r"<strong>\1</strong>", out)
    out = ITALIC.sub(r"<em>\1</em>", out)
    out = LINK.sub(r'<a href="\2">\1</a>', out)
    return out


def _is_table_divider(line: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:|-]+\|[\s:|-]*", line.strip())) and "-" in line


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    list_open: str | None = None

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            out.append(f"</{list_open}>")
            list_open = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            close_list()
            i += 1
            continue

        # A line that is already HTML passes through untouched. Generated tables and
        # figures are substituted into the narrative as markup, and escaping them
        # would turn a table into a paragraph of angle brackets.
        if stripped.startswith("<") and not stripped.startswith("<-"):
            close_list()
            block = [lines[i]]
            i += 1
            while i < len(lines) and lines[i].strip():
                block.append(lines[i])
                i += 1
            out.append("\n".join(block))
            continue

        # Table: header row followed by a divider row.
        if stripped.startswith("|") and i + 1 < len(lines) and _is_table_divider(lines[i + 1]):
            close_list()
            headers = _cells(stripped)
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(_cells(lines[i]))
                i += 1
            head = "".join(f"<th>{_inline(h)}</th>" for h in headers)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body
            )
            out.append(f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
            continue

        heading = re.match(r"(#{1,4})\s+(.*)", stripped)
        if heading:
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if stripped in {"---", "***", "___"}:
            close_list()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            close_list()
            quoted: list[str] = []
            # Consecutive > lines are one quote, not one box per line.
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(quoted))}</blockquote>")
            continue

        bullet = re.match(r"[-*]\s+(.*)", stripped)
        if bullet:
            if list_open != "ul":
                close_list()
                out.append("<ul>")
                list_open = "ul"
            out.append(f"<li>{_inline(bullet.group(1))}</li>")
            i += 1
            continue

        numbered = re.match(r"\d+[.)]\s+(.*)", stripped)
        if numbered:
            if list_open != "ol":
                close_list()
                out.append("<ol>")
                list_open = "ol"
            out.append(f"<li>{_inline(numbered.group(1))}</li>")
            i += 1
            continue

        close_list()
        paragraph = [stripped]
        i += 1
        # A line only ends the paragraph if it actually starts a block: "*emphasis"
        # at the start of a line is not a bullet.
        while i < len(lines) and lines[i].strip() and not re.match(
            r"(?:[-*+]\s|#{1,4}\s|>\s|\||\d+[.)]\s|---|\*\*\*|___)", lines[i].strip()
        ):
            paragraph.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(paragraph))}</p>")

    close_list()
    return "\n".join(out)
