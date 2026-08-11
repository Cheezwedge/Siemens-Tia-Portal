"""The build plan: the single file the C# Openness driver consumes.

The driver deliberately contains no engineering knowledge. Everything it needs -
which CPU, which modules, which addresses, which sources to import in which
order - is decided here, where it can be reviewed and diffed.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import emit_hmi, emit_tags
from .model import Spec

PLAN_SCHEMA = 1

# Fixed driver pipeline. Stages run in this order; a stage listed in "skip" is
# passed over. Keeping the order here rather than in C# makes it reviewable.
PIPELINE = [
    "create_project",
    "create_plc",
    "add_plc_modules",
    "create_hmi",
    "create_io_devices",
    "build_subnet",
    "assign_io_devices",
    "create_tag_tables",
    "delete_blocks",
    "import_sources",
    "create_instance_dbs",
    "import_ob",
    "create_hmi_connection",
    "create_hmi_tags",
    "create_hmi_screens",
    "compile",
    "save",
]


def build(spec: Spec, source_files: List[str], tables, skip: List[str] | None = None) -> Dict[str, Any]:
    plan: Dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "generated_by": "tiagen",
        "pipeline": PIPELINE,
        "skip": skip or [],
        "project": {
            "name": spec.machine,
            "directory": spec.project.get("directory", ""),
            "author": spec.project.get("author", "tiagen"),
            "comment": spec.project.get("comment", ""),
            "tia_version": spec.project.get("tia_version", "V21"),
        },
        "devices": {
            "plc": _plc(spec),
            "hmi": _hmi(spec),
            "io_devices": [_io_device(d) for d in spec.network.get("io_devices") or []],
        },
        "network": _network(spec),
        "software": {
            "external_sources": [
                {"file": f, "order": index} for index, f in enumerate(source_files)
            ],
            # The default Main [OB1] is replaced by the generated SCL OB.
            "delete_before_import": [spec.ob_main],
            "instance_dbs": [
                {
                    "name": spec.db_machine,
                    "from_fb": spec.fb_machine,
                    "optimized": bool(spec.options.get("optimized_access", True)),
                }
            ],
            "ob_source": _ob_source_name(spec, source_files),
            "tag_tables": [
                {
                    "name": name,
                    "tags": [
                        {
                            "name": t.name,
                            "datatype": t.datatype,
                            "address": t.address,
                            "comment": t.comment,
                        }
                        for t in tags
                    ],
                }
                for name, tags in tables.items()
            ],
        },
        "hmi_software": _hmi_software(spec),
        "compile": True,
        "verification": {
            "expect_compile_errors": 0,
            "expect_blocks": _expected_blocks(spec),
        },
    }
    return plan


def _plc(spec: Spec) -> Dict[str, Any]:
    plc = spec.plc
    return {
        "name": plc["name"],
        "order_number": str(plc["order_number"]).strip(),
        "firmware": plc.get("firmware", "V1.1"),
        "device_name": plc["name"],
        "ip": plc.get("ip", ""),
        "subnet_mask": plc.get("subnet_mask", "255.255.255.0"),
        "gateway": plc.get("gateway", ""),
        "profinet_name": plc.get("profinet_name", plc["name"].lower().replace("_", "-")),
        "modules": [_module(m) for m in plc.get("modules") or []],
    }


def _hmi(spec: Spec):
    if not spec.hmi:
        return None
    hmi = spec.hmi
    return {
        "name": hmi["name"],
        "order_number": str(hmi["order_number"]).strip(),
        "firmware": hmi.get("firmware", ""),
        "device_name": hmi["name"],
        "ip": hmi.get("ip", ""),
        "runtime": hmi.get("runtime", "unified"),
        "resolution": hmi.get("resolution", "1920x1080"),
    }


def _io_device(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": raw["name"],
        "order_number": str(raw["order_number"]).strip(),
        "firmware": raw.get("firmware", ""),
        "type_identifier": raw.get("type_identifier", ""),
        "ip": raw.get("ip", ""),
        "profinet_name": raw.get("profinet_name", raw["name"].lower().replace("_", "-")),
        "modules": [_module(m) for m in raw.get("modules") or []],
    }


def _module(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": raw["name"],
        "order_number": str(raw["order_number"]).strip(),
        "slot": int(raw["slot"]),
        "firmware": raw.get("firmware", ""),
    }


def _network(spec: Spec) -> Dict[str, Any]:
    nodes = []
    if spec.plc.get("ip"):
        nodes.append({
            "device": spec.plc["name"],
            "ip": spec.plc["ip"],
            "subnet_mask": spec.plc.get("subnet_mask", "255.255.255.0"),
            "gateway": spec.plc.get("gateway", ""),
            "role": "io_controller",
        })
    if spec.hmi and spec.hmi.get("ip"):
        nodes.append({
            "device": spec.hmi["name"],
            "ip": spec.hmi["ip"],
            "subnet_mask": spec.plc.get("subnet_mask", "255.255.255.0"),
            "role": "hmi",
        })
    for d in spec.network.get("io_devices") or []:
        if d.get("ip"):
            nodes.append({
                "device": d["name"],
                "ip": d["ip"],
                "subnet_mask": spec.plc.get("subnet_mask", "255.255.255.0"),
                "role": "io_device",
            })
    return {
        "subnet_name": spec.network.get("subnet_name", "PN_IE_1"),
        "io_system_name": f"{spec.plc['name']}.PROFINET_IO-System",
        "nodes": nodes,
    }


def _hmi_software(spec: Spec) -> Dict[str, Any]:
    if not spec.hmi:
        return {"enabled": False}
    tags = emit_hmi.hmi_tags(spec)
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for tag in tags:
        tables.setdefault(tag["table"], []).append(tag)
    plan = emit_hmi.screen_plan(spec)
    return {
        "enabled": True,
        "connection": {
            "name": "HMI_Connection_1",
            "partner": spec.plc["name"],
            "hmi": spec.hmi["name"],
            "protocol": "Ethernet",
        },
        "tag_tables": [
            {
                "name": name,
                "tags": [
                    {
                        "name": t["name"],
                        "datatype": t["datatype"],
                        "plc_tag": t["plc_tag"],
                        "access": t["access"],
                        "comment": t["comment"],
                    }
                    for t in items
                ],
            }
            for name, items in tables.items()
        ],
        "screens": [
            {"name": s["name"], "title": s["title"], "kind": s["kind"],
             "object_count": len(s["objects"])}
            for s in plan["screens"]
        ],
        "start_screen": plan["start_screen"],
        "alarms": emit_hmi.alarm_list(spec),
        # Carried in the plan so the step texts are reviewable and diffable with
        # everything else. The driver has no stage that applies them yet, so today
        # they are imported by hand - see docs/11.
        "text_lists": _text_lists(spec),
    }


def _text_lists(spec: Spec) -> List[Dict[str, Any]]:
    if not spec.sequence:
        return []
    from . import emit_seq

    return emit_seq.text_lists(spec, spec.sequence)


def _ob_source_name(spec: Spec, source_files: List[str]) -> str:
    for f in source_files:
        if f.endswith(f"{spec.ob_main}.scl"):
            return f
    return ""


def _expected_blocks(spec: Spec) -> List[str]:
    from . import devices as dev

    blocks = ["UDT_DevIf", "FB_ModeManager"]
    blocks += sorted({e.typedef.fb for e in spec.controlled() if e.typedef.fb})
    blocks += [spec.udt_auto, spec.udt_alarms, spec.udt_cmd]
    if spec.sequence:
        blocks += [spec.udt_cond, spec.fb_sequence]
    blocks += [spec.fb_machine, spec.db_machine, spec.ob_main]
    return blocks
