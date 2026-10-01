"""PLC tag table emitters: a structured table model, CSV, and SimaticML XML."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
import os
from datetime import datetime, timezone
from typing import Dict, List
from xml.sax.saxutils import escape

from . import devices as dev
from .model import Spec

# Order the tables appear in, so output is stable and readable.
TABLE_ORDER = ["Inputs", "Outputs", "AnalogInputs", "AnalogOutputs"]


@dataclass
class Tag:
    name: str
    datatype: str
    address: str
    comment: str


def build_tables(spec: Spec) -> Dict[str, List[Tag]]:
    """Group every resolved signal into its PLC tag table."""
    tables: Dict[str, List[Tag]] = {}
    for eq in spec.equipment:
        for sig in eq.signals:
            tables.setdefault(sig.table, []).append(
                Tag(sig.tag, sig.datatype, sig.address, sig.comment)
            )

    def sort_key(tag: Tag):
        # Sort by numeric address so the table reads like a wiring list.
        try:
            body = tag.address.lstrip("%").lstrip("IQ").lstrip("W")
            if "." in body:
                byte, bit = body.split(".")
                return (int(byte), int(bit))
            return (int(body), 0)
        except (ValueError, AttributeError):
            return (10**9, 0)

    ordered: Dict[str, List[Tag]] = {}
    for name in TABLE_ORDER:
        if name in tables:
            ordered[name] = sorted(tables[name], key=sort_key)
    for name in sorted(tables):
        if name not in ordered:
            ordered[name] = sorted(tables[name], key=sort_key)
    return ordered


def emit_csv(tables: Dict[str, List[Tag]]) -> str:
    """One flat CSV of every tag - the handover sheet for panel builders."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Table", "Name", "DataType", "Address", "Comment"])
    for table, tags in tables.items():
        for tag in tags:
            writer.writerow([table, tag.name, tag.datatype, tag.address, tag.comment])
    return out.getvalue()


class _IdGen:
    def __init__(self):
        self._next = 0

    def take(self) -> str:
        value = self._next
        self._next += 1
        return str(value)


def emit_xml(table_name: str, tags: List[Tag], engineering_version: str = "V21",
             culture: str = "en-US") -> str:
    """Render one PLC tag table as SimaticML.

    The Openness driver creates tags through the API by default, which needs no
    schema at all. This XML exists for the offline route: importing a tag table
    into TIA by hand, or into a project the driver cannot reach.

    Verify the header against a real export from your own TIA version once - see
    docs/03-simaticml-and-source-formats.md, "Adopting your own export as the template".
    """
    ids = _IdGen()
    now = _created_timestamp()

    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<Document>"]
    lines.append(f'  <Engineering version="{escape(engineering_version)}" />')
    lines += [
        "  <DocumentInfo>",
        f"    <Created>{now}</Created>",
        "    <ExportSetting>WithDefaults</ExportSetting>",
        "  </DocumentInfo>",
    ]
    lines.append(f'  <SW.Tags.PlcTagTable ID="{ids.take()}">')
    lines += ["    <AttributeList>", f"      <Name>{escape(table_name)}</Name>", "    </AttributeList>"]
    lines.append("    <ObjectList>")

    for tag in tags:
        tag_id = ids.take()
        lines.append(f'      <SW.Tags.PlcTag ID="{tag_id}" CompositionName="Tags">')
        lines += [
            "        <AttributeList>",
            f"          <DataTypeName>{escape(tag.datatype)}</DataTypeName>",
            "          <ExternalAccessible>true</ExternalAccessible>",
            "          <ExternalVisible>true</ExternalVisible>",
            "          <ExternalWritable>true</ExternalWritable>",
            f"          <LogicalAddress>{escape(tag.address)}</LogicalAddress>",
            f"          <Name>{escape(tag.name)}</Name>",
            "        </AttributeList>",
        ]
        if tag.comment:
            text_id = ids.take()
            item_id = ids.take()
            lines += [
                "        <ObjectList>",
                f'          <MultilingualText ID="{text_id}" CompositionName="Comment">',
                "            <ObjectList>",
                f'              <MultilingualTextItem ID="{item_id}" CompositionName="Items">',
                "                <AttributeList>",
                f"                  <Culture>{escape(culture)}</Culture>",
                f"                  <Text>{escape(tag.comment)}</Text>",
                "                </AttributeList>",
                "              </MultilingualTextItem>",
                "            </ObjectList>",
                "          </MultilingualText>",
                "        </ObjectList>",
            ]
        lines.append("      </SW.Tags.PlcTag>")

    lines += ["    </ObjectList>", "  </SW.Tags.PlcTagTable>", "</Document>"]
    return "\n".join(lines) + "\n"


def summarise(tables: Dict[str, List[Tag]]) -> str:
    """Human-readable I/O count, used in the build report."""
    counts = {
        "digital inputs": len(tables.get("Inputs", [])),
        "digital outputs": len(tables.get("Outputs", [])),
        "analog inputs": len(tables.get("AnalogInputs", [])),
        "analog outputs": len(tables.get("AnalogOutputs", [])),
    }
    return ", ".join(f"{v} {k}" for k, v in counts.items() if v)


def highest_addresses(tables: Dict[str, List[Tag]]) -> Dict[str, str]:
    """Highest address used per area - tells you how much I/O to actually buy."""
    result = {}
    for table, tags in tables.items():
        if tags:
            result[table] = tags[-1].address
    return result


def kind_of_table(table: str) -> str:
    for kind, name in dev.KIND_TABLE.items():
        if name == table:
            return kind
    return dev.DI


def _created_timestamp() -> str:
    """The <Created> stamp: fixed, so the same spec always produces the same bytes.

    Wall-clock time here made every build differ from the last, which defeats both
    diffing generated output in review and the claim that a spec fully determines
    what it produces. Nothing reads this value - the driver creates tags through the
    API, and TIA ignores it on import. SOURCE_DATE_EPOCH, the reproducible-builds
    convention, sets a real date for anyone who wants one.
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    moment = (datetime.fromtimestamp(int(epoch), timezone.utc) if epoch.isdigit()
              else datetime(2000, 1, 1, tzinfo=timezone.utc))
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
