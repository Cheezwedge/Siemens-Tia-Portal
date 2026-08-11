"""Tests for the generator. Run with: python -m unittest discover generator/tests

These check the things that silently break a real project: address allocation,
tag naming, the generated SCL actually referencing the right tags, and the
validation rules that stop a bad spec reaching TIA Portal.
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "generator"))

from tiagen import build as build_mod  # noqa: E402
from tiagen import emit_hmi, emit_scl, emit_tags, model, validate  # noqa: E402
from tiagen.model import SpecError  # noqa: E402


def spec_from(equipment, **overrides):
    raw = {
        "project": {"name": "Rig"},
        "plc": {"name": "PLC_1", "order_number": "6ES7214-1AH50-0XB0", "ip": "192.168.0.1"},
        "equipment": equipment,
    }
    raw.update(overrides)
    return model.from_dict(raw)


class TestAddressing(unittest.TestCase):
    def test_bits_allocate_sequentially_and_roll_over_bytes(self):
        spec = spec_from([
            {"name": f"S{i}", "type": "digital_input"} for i in range(10)
        ])
        addresses = [e.signals[0].address for e in spec.equipment]
        self.assertEqual(addresses[0], "%I0.0")
        self.assertEqual(addresses[7], "%I0.7")
        self.assertEqual(addresses[8], "%I1.0")
        self.assertEqual(addresses[9], "%I1.1")

    def test_inputs_and_outputs_use_independent_ranges(self):
        spec = spec_from([
            {"name": "In1", "type": "digital_input"},
            {"name": "Out1", "type": "digital_output"},
        ])
        self.assertEqual(spec.equipment[0].signals[0].address, "%I0.0")
        self.assertEqual(spec.equipment[1].signals[0].address, "%Q0.0")

    def test_words_step_by_two_bytes(self):
        spec = spec_from([
            {"name": "A1", "type": "analog_input"},
            {"name": "A2", "type": "analog_input"},
        ])
        self.assertEqual(spec.equipment[0].signals[0].address, "%IW64")
        self.assertEqual(spec.equipment[1].signals[0].address, "%IW66")

    def test_explicit_address_is_honoured_and_not_reused(self):
        spec = spec_from([
            {"name": "Fixed", "type": "digital_input", "address": "%I0.0"},
            {"name": "Auto1", "type": "digital_input"},
        ])
        self.assertEqual(spec.equipment[0].signals[0].address, "%I0.0")
        # The auto-allocated input must skip the reserved bit.
        self.assertEqual(spec.equipment[1].signals[0].address, "%I0.1")

    def test_explicit_address_reserved_out_of_order(self):
        # The fixed address comes later in the list but must still be respected.
        spec = spec_from([
            {"name": "Auto1", "type": "digital_input"},
            {"name": "Fixed", "type": "digital_input", "address": "%I0.0"},
        ])
        by_name = {e.name: e.signals[0].address for e in spec.equipment}
        self.assertEqual(by_name["Fixed"], "%I0.0")
        self.assertEqual(by_name["Auto1"], "%I0.1")

    def test_per_signal_override(self):
        spec = spec_from([{
            "name": "Pump", "type": "vfd_analog",
            "signals": {"speed_out": "%QW80"},
        }])
        pump = spec.equipment[0]
        self.assertEqual(pump.signal("speed_out").address, "%QW80")
        self.assertTrue(pump.signal("speed_out").explicit)

    def test_start_bytes_are_configurable(self):
        spec = spec_from(
            [{"name": "In1", "type": "digital_input"}],
            io={"di_start_byte": 4},
        )
        self.assertEqual(spec.equipment[0].signals[0].address, "%I4.0")

    def test_duplicate_explicit_address_is_rejected(self):
        with self.assertRaises(SpecError) as ctx:
            spec_from([
                {"name": "A", "type": "digital_input", "address": "%I0.0"},
                {"name": "B", "type": "digital_input", "address": "%I0.0"},
            ])
        self.assertIn("%I0.0", str(ctx.exception))

    def test_wrong_area_for_signal_is_rejected(self):
        with self.assertRaises(SpecError):
            spec_from([{"name": "A", "type": "digital_input", "address": "%Q0.0"}])

    def test_odd_word_address_is_rejected(self):
        with self.assertRaises(SpecError):
            spec_from([{"name": "A", "type": "analog_input", "address": "%IW65"}])


class TestSignalOptions(unittest.TestCase):
    def test_optional_feedback_can_be_switched_off(self):
        spec = spec_from([{
            "name": "M", "type": "motor_dol",
            "options": {"has_fault_feedback": False},
        }])
        motor = spec.equipment[0]
        self.assertTrue(motor.has("running_fb"))
        self.assertFalse(motor.has("fault_fb"))
        # Dropping the fault input must not leave a gap in the input addresses.
        self.assertEqual(motor.signal("running_fb").address, "%I0.0")

    def test_tag_names_follow_equipment_plus_suffix(self):
        spec = spec_from([{"name": "Conveyor", "type": "motor_dol"}])
        tags = {s.role: s.tag for s in spec.equipment[0].signals}
        self.assertEqual(tags["run_out"], "Conveyor_Run")
        self.assertEqual(tags["running_fb"], "Conveyor_Running")
        self.assertEqual(tags["fault_fb"], "Conveyor_Fault")

    def test_colliding_tag_names_are_rejected(self):
        # 'Pump_Run' would be produced both as a bare input and as the motor's output.
        with self.assertRaises(SpecError) as ctx:
            spec_from([
                {"name": "Pump_Run", "type": "digital_input"},
                {"name": "Pump", "type": "motor_dol"},
            ])
        self.assertIn("Pump_Run", str(ctx.exception))


class TestSpecRejection(unittest.TestCase):
    def test_unknown_type(self):
        with self.assertRaises(SpecError) as ctx:
            spec_from([{"name": "X", "type": "hydraulic_wombat"}])
        self.assertIn("hydraulic_wombat", str(ctx.exception))

    def test_scl_keyword_name(self):
        with self.assertRaises(SpecError):
            spec_from([{"name": "For", "type": "digital_input"}])

    def test_invalid_identifier(self):
        with self.assertRaises(SpecError):
            spec_from([{"name": "2Fast", "type": "digital_input"}])

    def test_missing_order_number(self):
        with self.assertRaises(SpecError):
            model.from_dict({"project": {"name": "P"}, "plc": {"name": "PLC_1"}})

    def test_project_name_must_be_an_identifier(self):
        with self.assertRaises(SpecError):
            model.from_dict({
                "project": {"name": "My Line!"},
                "plc": {"name": "PLC_1", "order_number": "6ES7214-1AH50-0XB0"},
            })


class TestValidation(unittest.TestCase):
    def test_unknown_interlock_is_an_error(self):
        spec = spec_from([{
            "name": "M", "type": "motor_dol",
            "options": {"interlock_from": ["Ghost"]},
        }])
        errors, _ = validate.check(spec)
        self.assertTrue(any("Ghost" in e for e in errors))

    def test_interlock_against_tag_only_equipment_is_an_error(self):
        spec = spec_from([
            {"name": "Sensor", "type": "digital_input"},
            {"name": "M", "type": "motor_dol", "options": {"interlock_from": ["Sensor"]}},
        ])
        errors, _ = validate.check(spec)
        self.assertTrue(any("Sensor" in e for e in errors))

    def test_out_of_order_limits_are_an_error(self):
        spec = spec_from([{
            "name": "Level", "type": "analog_input",
            "scaling": {"lo": 80.0, "hi": 20.0},
        }])
        errors, _ = validate.check(spec)
        self.assertTrue(any("out of order" in e for e in errors))

    def test_duplicate_ip_is_an_error(self):
        spec = spec_from(
            [],
            hmi={"name": "HMI_1", "order_number": "6AV2128-3MB06-0AX0", "ip": "192.168.0.1"},
        )
        errors, _ = validate.check(spec)
        self.assertTrue(any("192.168.0.1" in e for e in errors))

    def test_hmi_outside_plc_subnet_warns(self):
        spec = spec_from(
            [],
            hmi={"name": "HMI_1", "order_number": "6AV2128-3MB06-0AX0", "ip": "10.0.0.5"},
        )
        _, warnings = validate.check(spec)
        self.assertTrue(any("outside the PLC subnet" in w for w in warnings))

    def test_duplicate_control_role_is_an_error(self):
        spec = spec_from([
            {"name": "Pb1", "type": "digital_input", "options": {"control_role": "start"}},
            {"name": "Pb2", "type": "digital_input", "options": {"control_role": "start"}},
        ])
        errors, _ = validate.check(spec)
        self.assertTrue(any("control_role" in e for e in errors))

    def test_duplicate_module_slot_is_an_error(self):
        spec = spec_from([], plc={
            "name": "PLC_1", "order_number": "6ES7214-1AH50-0XB0",
            "modules": [
                {"name": "A", "order_number": "6ES7000-0AA00-0AA0", "slot": 2},
                {"name": "B", "order_number": "6ES7000-0AA00-0AA0", "slot": 2},
            ],
        })
        errors, _ = validate.check(spec)
        self.assertTrue(any("slot 2" in e for e in errors))

    def test_safety_input_always_warns_when_not_fail_safe(self):
        spec = spec_from([{"name": "EStop", "type": "estop"}])
        _, warnings = validate.check(spec)
        self.assertTrue(any(w.startswith("SAFETY:") for w in warnings))

    def test_valid_mlfb_does_not_warn(self):
        spec = spec_from([])
        _, warnings = validate.check(spec)
        self.assertFalse(any("does not look like a Siemens MLFB" in w for w in warnings))

    def test_bogus_mlfb_warns(self):
        spec = spec_from([], plc={"name": "PLC_1", "order_number": "NOT-A-PART-NUMBER"})
        _, warnings = validate.check(spec)
        self.assertTrue(any("does not look like a Siemens MLFB" in w for w in warnings))


class TestSclEmission(unittest.TestCase):
    def setUp(self):
        self.spec = spec_from([
            {"name": "EStop", "type": "estop"},
            {"name": "StopPb", "type": "digital_input", "options": {"control_role": "stop"}},
            {"name": "Conveyor", "type": "motor_dol"},
            {"name": "Gate", "type": "valve_single",
             "options": {"interlock_from": ["Conveyor"]}},
        ])
        self.fb = emit_scl.emit_fb_machine(self.spec)

    def test_every_device_gets_an_instance_and_a_call(self):
        # The declaration column is padded to the longest name, so match loosely.
        declarations = {
            line.split(":")[0].strip(): line.split(":")[1].strip()
            for line in self.fb.splitlines()
            if line.startswith("      ") and ' : "FB_' in line
        }
        self.assertEqual(declarations.get("Conveyor"), '"FB_Motor";')
        self.assertEqual(declarations.get("Gate"), '"FB_Valve";')
        self.assertIn("#Conveyor(Enable := #vEnable", self.fb)
        self.assertIn("#Gate(Enable := #vEnable", self.fb)

    def test_outputs_are_wired_to_the_allocated_tags(self):
        self.assertIn('RunOut => "Conveyor_Run"', self.fb)
        self.assertIn('OpenOut => "Gate_Open"', self.fb)

    def test_safety_status_aggregates_every_safety_input(self):
        self.assertIn('#vSafetyOk := "EStop_Ok"', self.fb)

    def test_hardwired_stop_is_wired_to_stopok(self):
        self.assertIn('StopOk := "StopPb"', self.fb)

    def test_hmi_stop_button_reaches_the_mode_manager(self):
        # Regression: Cmd.Stop used to be cleared without ever being read.
        self.assertIn("StopCmd := #Cmd.Stop", self.fb)

    def test_interlock_expression_references_the_other_device(self):
        self.assertIn("Interlock := #Conveyor.Hmi.Sts_Running", self.fb)

    def test_no_safety_input_falls_back_to_true(self):
        spec = spec_from([{"name": "M", "type": "motor_dol"}])
        self.assertIn("#vSafetyOk := TRUE;", emit_scl.emit_fb_machine(spec))

    def test_auto_struct_has_a_member_per_request(self):
        udt = emit_scl.emit_udt_auto(self.spec)
        self.assertIn("Conveyor_Start : Bool;", udt)
        self.assertIn("Conveyor_Stop", udt)
        self.assertIn("Gate_Open", udt)

    def test_empty_struct_gets_a_placeholder_member(self):
        # An empty STRUCT is not valid SCL.
        spec = spec_from([{"name": "Sensor", "type": "digital_input"}])
        udt = emit_scl.emit_udt_auto(spec)
        self.assertIn("Reserved : Bool;", udt)

    def test_alarm_struct_and_aggregation_agree(self):
        alarms = emit_scl.emit_udt_alarms(self.spec)
        self.assertIn("Conveyor_Fault : Bool;", alarms)
        self.assertIn("#Alarms.Conveyor_Fault := #Conveyor.Fault;", self.fb)
        self.assertIn("#DevicesHealthy := NOT #AnyFault;", self.fb)

    def test_warning_limits_do_not_block_the_machine(self):
        spec = spec_from([{
            "name": "Level", "type": "analog_input",
            "scaling": {"lo_lo": 5.0, "lo": 15.0, "hi": 90.0, "hi_hi": 97.0},
        }])
        fb = emit_scl.emit_fb_machine(spec)
        anyfault = fb.split("#AnyFault :=")[1].split(";")[0]
        self.assertIn("Level_LoLo", anyfault)
        self.assertIn("Level_HiHi", anyfault)
        self.assertNotIn("Level_Lo ", anyfault)
        self.assertNotIn("Level_Hi ", anyfault)

    def test_real_literals_always_carry_a_decimal_point(self):
        spec = spec_from([{
            "name": "Pump", "type": "vfd_analog",
            "scaling": {"eng_max": 50, "raw_max": 27648},
        }])
        fb = emit_scl.emit_fb_machine(spec)
        self.assertIn("SpeedMax := 50.0", fb)
        self.assertIn("RawMax := 27648.0", fb)

    def test_time_literals_are_rendered_in_ms(self):
        spec = spec_from([{
            "name": "M", "type": "motor_dol",
            "options": {"feedback_timeout_ms": 2500},
        }])
        self.assertIn("FbTimeout := T#2500MS", emit_scl.emit_fb_machine(spec))

    def test_ob_calls_the_instance_db(self):
        ob = emit_scl.emit_ob_main(self.spec)
        self.assertIn('"DB_Rig"();', ob)
        self.assertIn("ORGANIZATION_BLOCK", ob)


class TestTagTables(unittest.TestCase):
    def test_tables_are_split_by_area_and_sorted_by_address(self):
        spec = spec_from([
            {"name": "B", "type": "digital_input", "address": "%I2.0"},
            {"name": "A", "type": "digital_input", "address": "%I0.1"},
            {"name": "Out", "type": "digital_output"},
        ])
        tables = emit_tags.build_tables(spec)
        self.assertEqual([t.name for t in tables["Inputs"]], ["A", "B"])
        self.assertIn("Outputs", tables)

    def test_xml_is_well_formed_and_carries_the_addresses(self):
        import xml.etree.ElementTree as ET

        spec = spec_from([{"name": "Sensor", "type": "digital_input",
                           "description": "a & b <sensor>"}])
        tables = emit_tags.build_tables(spec)
        xml = emit_tags.emit_xml("Inputs", tables["Inputs"])
        root = ET.fromstring(xml)  # raises if the escaping is wrong
        self.assertEqual(root.tag, "Document")
        self.assertIn("<LogicalAddress>%I0.0</LogicalAddress>", xml)
        self.assertIn("a &amp; b &lt;sensor&gt;", xml)

    def test_xml_ids_are_unique(self):
        import xml.etree.ElementTree as ET

        spec = spec_from([{"name": f"S{i}", "type": "digital_input",
                           "description": f"sensor {i}"} for i in range(5)])
        tables = emit_tags.build_tables(spec)
        root = ET.fromstring(emit_tags.emit_xml("Inputs", tables["Inputs"]))
        ids = [el.get("ID") for el in root.iter() if el.get("ID") is not None]
        self.assertEqual(len(ids), len(set(ids)))

    def test_engineering_version_is_configurable(self):
        spec = spec_from([{"name": "S", "type": "digital_input"}])
        tables = emit_tags.build_tables(spec)
        xml = emit_tags.emit_xml("Inputs", tables["Inputs"], engineering_version="V20")
        self.assertIn('<Engineering version="V20" />', xml)


class TestFullBuild(unittest.TestCase):
    def _build(self, spec_path):
        out = tempfile.mkdtemp(prefix="tiagen-test-")
        spec = model.load(spec_path)
        return spec, build_mod.build(spec, out_dir=out), out

    def test_example_specs_build_cleanly(self):
        for name in ("minimal.yaml", "conveyor-line.yaml"):
            path = os.path.join(REPO_ROOT, "spec", "examples", name)
            with self.subTest(spec=name):
                spec, result, out = self._build(path)
                self.assertTrue(result.ok, msg=result.errors)
                self.assertIn("plan.json", result.files)
                self.assertIn("report.md", result.files)

                plan = json.load(open(os.path.join(out, "plan.json")))
                self.assertEqual(plan["schema"], 1)
                # Every source listed in the plan must exist on disk.
                for entry in plan["software"]["external_sources"]:
                    self.assertTrue(
                        os.path.exists(os.path.join(out, entry["file"])),
                        msg=f"{entry['file']} listed in the plan but not generated",
                    )

    def test_plan_lists_sources_in_dependency_order(self):
        path = os.path.join(REPO_ROOT, "spec", "examples", "conveyor-line.yaml")
        _, _, out = self._build(path)
        plan = json.load(open(os.path.join(out, "plan.json")))
        files = [e["file"] for e in plan["software"]["external_sources"]]
        # The type UDT must precede the FBs that declare it, and the machine FB
        # must precede the OB that calls it.
        self.assertLess(files.index("scl/10_UDT_DevIf.scl"), files.index("scl/20_FB_Motor.scl"))
        self.assertLess(
            files.index("scl/40_FB_BottleLine.scl"),
            files.index("scl/60_Main.scl"),
        )

    def test_instance_db_is_created_from_the_machine_fb(self):
        path = os.path.join(REPO_ROOT, "spec", "examples", "minimal.yaml")
        spec, _, out = self._build(path)
        plan = json.load(open(os.path.join(out, "plan.json")))
        db = plan["software"]["instance_dbs"][0]
        self.assertEqual(db["name"], spec.db_machine)
        self.assertEqual(db["from_fb"], spec.fb_machine)

    def test_hmi_plan_is_absent_without_an_hmi(self):
        path = os.path.join(REPO_ROOT, "spec", "examples", "minimal.yaml")
        _, result, out = self._build(path)
        plan = json.load(open(os.path.join(out, "plan.json")))
        self.assertFalse(plan["hmi_software"]["enabled"])
        self.assertNotIn("hmi/hmi_tags.json", result.files)

    def test_hmi_tags_point_at_the_instance_db(self):
        path = os.path.join(REPO_ROOT, "spec", "examples", "conveyor-line.yaml")
        spec, _, out = self._build(path)
        tags = json.load(open(os.path.join(out, "hmi", "hmi_tags.json")))
        running = next(t for t in tags if t["name"] == "InfeedConveyor_Sts_Running")
        self.assertEqual(
            running["plc_tag"], f"{spec.db_machine}.InfeedConveyor.Hmi.Sts_Running"
        )
        self.assertTrue(all(t["name"] for t in tags))
        self.assertEqual(len({t["name"] for t in tags}), len(tags), "duplicate HMI tag name")

    def test_alarm_triggers_match_the_alarm_udt_members(self):
        path = os.path.join(REPO_ROOT, "spec", "examples", "conveyor-line.yaml")
        spec, _, out = self._build(path)
        alarms = json.load(open(os.path.join(out, "hmi", "alarms.json")))
        udt = emit_scl.emit_udt_alarms(spec)
        for alarm in alarms:
            self.assertIn(f"{alarm['name']} ", udt)
            self.assertTrue(alarm["trigger_tag"].startswith(f"{spec.db_machine}.Alarms."))
        numbers = [a["number"] for a in alarms]
        self.assertEqual(numbers, sorted(set(numbers)))

    def test_screens_have_a_start_screen_and_no_empty_screen(self):
        path = os.path.join(REPO_ROOT, "spec", "examples", "conveyor-line.yaml")
        _, _, out = self._build(path)
        plan = json.load(open(os.path.join(out, "hmi", "screens.json")))
        self.assertEqual(plan["start_screen"], "Overview")
        names = [s["name"] for s in plan["screens"]]
        self.assertIn("Alarms", names)
        self.assertIn("Trends", names)  # the spec has an analog input
        self.assertEqual(len(names), len(set(names)))
        for screen in plan["screens"]:
            self.assertTrue(screen["objects"], msg=f"{screen['name']} has no objects")

    def test_unified_basic_panel_is_flagged_for_scripting_and_faceplates(self):
        spec = spec_from(
            [{"name": "Conv", "type": "motor_dol"}],
            hmi={"name": "HMI_1", "order_number": "6AV2128-3MB06-0AX0",
                 "runtime": "unified_basic", "ip": "192.168.0.10",
                 "resolution": "800x480"},
        )
        _, warnings = validate.check(spec)
        joined = " ".join(warnings)
        self.assertIn("no scripting engine", joined)
        self.assertIn("faceplate instances", joined)
        plan = emit_hmi.screen_plan(spec)
        self.assertFalse(plan["scripting"])
        self.assertEqual(plan["resolution"], {"width": 800, "height": 480})

    def test_missing_resolution_warns_rather_than_defaulting_silently(self):
        spec = spec_from(
            [{"name": "Conv", "type": "motor_dol"}],
            hmi={"name": "HMI_1", "order_number": "6AV2128-3MB06-0AX0",
                 "ip": "192.168.0.10"},
        )
        _, warnings = validate.check(spec)
        self.assertTrue(any("hmi.resolution is not set" in w for w in warnings))
        # The default still applies, so a spec without it builds rather than blocking.
        self.assertEqual(emit_hmi.screen_plan(spec)["resolution"]["width"], 1920)

    def test_errors_block_generation_unless_forced(self):
        spec = spec_from([{
            "name": "M", "type": "motor_dol", "options": {"interlock_from": ["Ghost"]},
        }])
        out = tempfile.mkdtemp(prefix="tiagen-test-")
        result = build_mod.build(spec, out_dir=out)
        self.assertFalse(result.ok)
        self.assertEqual(result.files, [])
        forced = build_mod.build(spec, out_dir=out, force=True)
        self.assertIn("plan.json", forced.files)


if __name__ == "__main__":
    unittest.main()
