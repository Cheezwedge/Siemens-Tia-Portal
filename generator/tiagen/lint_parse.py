"""Reading exported TIA sources into the structures lint checks.

Three formats, at three levels of confidence:

* **SCL** (`.scl`) - fully parsed. This toolchain emits SCL, so the format is known
  rather than guessed, and TIA has been able to produce it from any block for years
  ("generate source from block"). Lint therefore works on an SCL export today,
  with no driver build and no new file format.
* **SimaticML** (`.xml`) - block identity, language and compile-unit count are read
  from the document. The schema-versioned namespaces are read, never assumed, so an
  export from any TIA build parses.
* **SIMATIC SD** (`.s7dcl`) - read as text, block name recovered where the header
  gives it away, `structured` left False. The structural parse lands when there is a
  real export to write it against; guessing at a text format and then generating
  against the guess is how a day disappears.

A block that is not structured is not silently treated as clean: `format_report`
says how many were read as text only, and rules needing structure skip them.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional
from xml.etree import ElementTree

from .lint import Block, Network, SourceFile

# SCL block openers. The name may be quoted or bare.
_SCL_OPEN = re.compile(
    r'^\s*(?P<kind>FUNCTION_BLOCK|FUNCTION|ORGANIZATION_BLOCK|DATA_BLOCK|TYPE)\s+'
    r'(?P<name>"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)',
    re.I,
)
_SCL_CLOSE = re.compile(
    r"^\s*(END_FUNCTION_BLOCK|END_FUNCTION|END_ORGANIZATION_BLOCK|END_DATA_BLOCK|END_TYPE)\b",
    re.I,
)
_SCL_KIND = {
    "function_block": "FB",
    "function": "FC",
    "organization_block": "OB",
    "data_block": "DB",
    "type": "UDT",
}

# A .s7dcl header names its block; the exact keyword is version dependent, so a
# few spellings are accepted and the whole line is kept for the report.
_SD_NAME = re.compile(
    r'^\s*(?:FUNCTION_BLOCK|FUNCTION|ORGANIZATION_BLOCK|DATA_BLOCK|TYPE|BLOCK)\s+'
    r'(?P<name>"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)',
    re.I,
)
_SD_LANGUAGE = re.compile(r"\b(LAD|FBD|SCL|STL|GRAPH)\b")


def read_file(path: str) -> SourceFile:
    text = _read_text(path)
    extension = os.path.splitext(path)[1].lower()
    kind = {".scl": "scl", ".s7dcl": "s7dcl", ".xml": "xml", ".s7res": "s7res"}.get(
        extension, "unknown"
    )
    source = SourceFile(path=path, text=text, kind=kind)

    if kind == "scl":
        source.blocks = parse_scl(text)
    elif kind == "xml":
        source.blocks = parse_simaticml(text, path)
    elif kind == "s7dcl":
        source.blocks = parse_s7dcl(text, path)
    return source


def _read_text(path: str) -> str:
    """Read a source file whatever it was encoded as.

    TIA writes UTF-8 with a BOM in some places and UTF-16 in others, and an export
    that came via a spreadsheet can be either. Getting this wrong yields a file that
    looks empty or looks like one enormous line, and the rules then report nothing -
    the worst failure mode a checker has.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    for bom, encoding in (
        (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"), (b"\xef\xbb\xbf", "utf-8-sig"),
    ):
        if raw.startswith(bom):
            return raw.decode(encoding, errors="replace")
    # No BOM: a UTF-16 file without one still shows as alternating NUL bytes.
    if raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# SCL
# --------------------------------------------------------------------------
def parse_scl(text: str) -> List[Block]:
    lines = text.splitlines()
    blocks: List[Block] = []
    current: Optional[Block] = None
    body: List[str] = []

    for index, line in enumerate(lines, start=1):
        if current is None:
            match = _SCL_OPEN.match(line)
            if match:
                current = Block(
                    name=match.group("name").strip('"'),
                    kind=_SCL_KIND.get(match.group("kind").lower(), ""),
                    language="SCL",
                    start_line=index,
                    structured=True,
                )
                # The comment preceding a block is its description, by convention in
                # this repo and in most hand-written SCL.
                current.comment = _preceding_comment(lines, index - 1)
                body = [line]
            continue

        body.append(line)
        if _SCL_CLOSE.match(line):
            current.text = "\n".join(body)
            # The convention in this repo, and in most hand-written SCL, is a
            # (* ... *) block documenting the block *after* its closer. Reading only
            # the text between the opener and closer loses it, and every block then
            # looks undocumented.
            current.title = _block_title(current.text) or _trailing_comment(lines, index)
            current.networks = _scl_regions(current, index)
            blocks.append(current)
            current = None
            body = []

    if current is not None:                     # unterminated block: keep what we have
        current.text = "\n".join(body)
        current.title = _block_title(current.text)
        blocks.append(current)
    return blocks


def _preceding_comment(lines: List[str], before_index: int) -> str:
    """Comment lines immediately above a declaration, read as its description.

    The generator's own header is excluded. "Generated by tiagen - do not edit" is
    provenance, not documentation, and counting it would make every generated block
    look documented when none of them are.
    """
    collected: List[str] = []
    index = before_index - 1
    while index >= 0:
        stripped = lines[index].strip()
        if stripped.startswith("//"):
            body = stripped.lstrip("/").strip()
            if "generated by tiagen" in body.lower() or "edit the spec" in body.lower():
                index -= 1
                continue
            collected.append(body)
        elif not stripped:
            if collected:
                break
        else:
            break
        index -= 1
    return " ".join(reversed(collected))


def _trailing_comment(lines: List[str], after_index: int) -> str:
    """A (* ... *) documentation block following a block's closer."""
    tail = "\n".join(lines[after_index:after_index + 60])
    match = re.search(r"\(\*(.*?)\*\)", tail, re.S)
    if not match:
        return ""
    # Only if it comes before the next declaration, otherwise it documents that one.
    before = tail[: match.start()]
    if _SCL_OPEN.search(before):
        return ""
    return " ".join(match.group(1).split())[:500]


def _block_title(text: str) -> str:
    """A (* ... *) block comment inside the block, used as its title."""
    match = re.search(r"\(\*(.*?)\*\)", text, re.S)
    if not match:
        return ""
    return " ".join(match.group(1).split())[:200]


def _scl_regions(block: Block, end_line: int) -> List[Network]:
    """SCL has no networks. The nearest equivalent worth reporting against is a
    section banner, which is how this repo's generated code is divided."""
    networks: List[Network] = []
    banner = re.compile(r"^\s*//\s*(\d+[a-z]?\.\s*.+)$")
    for offset, line in enumerate(block.text.splitlines()):
        match = banner.match(line)
        if match:
            networks.append(Network(
                number=len(networks) + 1,
                title=" ".join(match.group(1).split()),
                start_line=block.start_line + offset,
            ))
    return networks


# --------------------------------------------------------------------------
# SimaticML
# --------------------------------------------------------------------------
def parse_simaticml(text: str, path: str) -> List[Block]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return []

    blocks: List[Block] = []
    for element in root.iter():
        tag = _local_name(element.tag)
        if not tag.startswith(("SW.Blocks.", "SW.Types.")):
            continue
        kind = tag.split(".")[-1]
        attributes = _first_child(element, "AttributeList")
        name = _child_text(attributes, "Name") if attributes is not None else ""
        if not name:
            continue

        block = Block(
            name=name,
            kind={"FB": "FB", "FC": "FC", "OB": "OB", "GlobalDB": "DB",
                  "InstanceDB": "DB", "PlcStruct": "UDT"}.get(kind, kind),
            structured=True,
        )
        languages = set()
        for unit in element.iter():
            if _local_name(unit.tag) != "SW.Blocks.CompileUnit":
                continue
            unit_attributes = _first_child(unit, "AttributeList")
            language = _child_text(unit_attributes, "ProgrammingLanguage") if \
                unit_attributes is not None else ""
            if language:
                languages.add(language)
            block.networks.append(Network(
                number=len(block.networks) + 1,
                title=_multilingual_text(unit, "Title"),
                comment=_multilingual_text(unit, "Comment"),
            ))
        block.language = "/".join(sorted(languages))
        block.title = _multilingual_text(element, "Title")
        block.comment = _multilingual_text(element, "Comment")
        blocks.append(block)
    return blocks


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_child(element, name: str):
    if element is None:
        return None
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _child_text(element, name: str) -> str:
    child = _first_child(element, name)
    return (child.text or "").strip() if child is not None else ""


def _multilingual_text(element, composition: str) -> str:
    """Pull text out of a MultilingualText child with the given CompositionName."""
    for child in element:
        if _local_name(child.tag) != "MultilingualText":
            continue
        if child.get("CompositionName") != composition:
            continue
        texts = [
            (item.text or "").strip()
            for item in child.iter()
            if _local_name(item.tag) == "Text"
        ]
        joined = " ".join(t for t in texts if t)
        if joined:
            return " ".join(joined.split())[:500]
    return ""


# --------------------------------------------------------------------------
# SIMATIC SD
# --------------------------------------------------------------------------
def parse_s7dcl(text: str, path: str) -> List[Block]:
    """Recover a block's identity from a source document.

    Deliberately shallow. The declaration header is stable enough to name the block
    and usually to name its language, and that is enough for the text-level rules.
    Networks are left unparsed and `structured` stays False, so rules that need
    structure skip the block instead of quietly passing it.
    """
    lines = text.splitlines()
    name = ""
    kind = ""
    for line in lines[:80]:
        match = _SD_NAME.match(line)
        if match:
            name = match.group("name").strip('"')
            kind = _SCL_KIND.get(line.strip().split()[0].lower(), "")
            break
    if not name:
        name = os.path.splitext(os.path.basename(path))[0]

    language = ""
    found = _SD_LANGUAGE.search("\n".join(lines[:80]))
    if found:
        language = found.group(1)

    return [Block(
        name=name,
        kind=kind,
        language=language,
        text=text,
        start_line=1,
        structured=False,
    )]
