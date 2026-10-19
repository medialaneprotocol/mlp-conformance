import json
import os
import re
import sys

corpus = sys.argv[1]
scenario_expected = int(sys.argv[2])
profile_expected = int(sys.argv[3])
case_expected = int(sys.argv[4])
update_mode = sys.argv[5] == "1"

problems = []


def ok(name, detail=""):
    print("ok   %s%s" % (name, (" — " + detail) if detail else ""))


def fail(name, detail):
    problems.append((name, detail))
    print("FAIL %s — %s" % (name, detail))


def defer(name, detail):
    print("DEFER %s — %s" % (name, detail))


HEX16 = re.compile(r"^[0-9a-f]{16}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
INVARIANTS = ["INV-%d" % i for i in range(1, 11)]
STORIES = ["S-%d" % i for i in range(1, 16)]
# The simulator cases of the acceptance matrix (`04-acceptance-matrix.md`,
# "Simulator" column), frozen here so the corpus cannot shrink quietly.
MATRIX_CASES = {
    "S-1": ["t2_typing_echo_within_budget"],
    "S-2": ["t3_scroll_converges_within_plan_deadline"],
    "S-3": [
        "t4_outage_survives_and_resumes",
        "t4_input_deadline_bounds",
        "t4_no_stuck_keys",
    ],
    "S-4": [
        "t4_flap_five_cycles_no_operator_action",
        "t4_flap_ladder_bounded",
    ],
    "S-5": ["t3_anchor_plan_atomic_and_monotone"],
    "S-6": ["t3_input_priority_under_plan", "t2_input_priority_under_plan"],
    "S-7": [
        "t3_memory_bounded_under_continuous_motion",
        "t4_memory_bounded_with_plans",
    ],
    "S-8": ["t3_recovery_fully_attributed"],
    "S-9": ["t1_resume_rebases_not_resets", "t1_media_lane_resume_control_lane_intact"],
    "S-10": [
        "t3_pressed_set_releases_on_park_and_resume",
        "input_fuzz_no_stuck_keys",
    ],
    "S-11": ["t4_typing_text_first_floor", "t4_no_audio_default"],
    "S-12": [
        "s2_path_selection_hysteresis",
        "s2_path_demotion_on_degradation",
        "s2_no_switch_during_plan",
    ],
    "S-13": ["t2_single_lane_priority_preserved"],
    "S-14": ["t0_identical_to_baseline_behaviour"],
    "S-15": ["lane_unsupported_degrades_not_fails"],
}
# The tiers each story is run at (the acceptance matrix's Tier row). A story
# may need more than one scenario — S-6 is "T2 and T3 (both asserted)" and the
# matrix names one simulator case per tier — so the corpus holds one scenario
# per (story, tier) where the tiers differ, and this table is the identity the
# gate enforces: the union of a story's scenarios' `tiers` must equal its row.
MATRIX_TIERS = {
    "S-1": ["T2"],
    "S-2": ["T3"],
    "S-3": ["T4"],
    "S-4": ["T4"],
    "S-5": ["T3"],
    "S-6": ["T2", "T3"],
    "S-7": ["T3", "T4"],
    "S-8": ["T3"],
    "S-9": ["T1"],
    "S-10": ["T3"],
    "S-11": ["T4"],
    "S-12": ["T4"],
    "S-13": ["T2"],
    "S-14": ["T0"],
    "S-15": ["T0"],
}
# The invariants each story's Simulator row names, required of the story as a
# whole (at least one of its scenarios asserts each). S-12…S-15 name none:
# their assertions are case-level in the runner, which is why their scenarios
# carry no `Assert` requirement.
MATRIX_INVARIANTS = {
    "S-1": ["INV-8", "INV-2"],
    "S-2": ["INV-2", "INV-7"],
    "S-3": ["INV-9", "INV-3", "INV-4"],
    "S-4": ["INV-9", "INV-4"],
    "S-5": ["INV-1", "INV-2", "INV-6"],
    "S-6": ["INV-8", "INV-7"],
    "S-7": ["INV-5"],
    "S-8": ["INV-10"],
    "S-9": ["INV-2"],
    "S-10": ["INV-4"],
    "S-11": ["INV-8", "INV-2"],
}
# The canonical representative point of each tier (design §3.1, refinements
# N-1…N-4).
CANONICAL = {
    "t0": dict(tier="T0", rtt_ms=10.0, jitter_ms=2.0, loss_permille=0,
               rate_bps_down=100_000_000, rate_bps_up=10_000_000, seed=1000),
    "t1": dict(tier="T1", rtt_ms=50.0, jitter_ms=10.0, loss_permille=1,
               rate_bps_down=5_000_000, rate_bps_up=1_000_000, seed=1001),
    "t2": dict(tier="T2", rtt_ms=150.0, jitter_ms=40.0, loss_permille=20,
               rate_bps_down=1_000_000, rate_bps_up=256_000, seed=1002),
    "t3": dict(tier="T3", rtt_ms=400.0, jitter_ms=100.0, loss_permille=50,
               rate_bps_down=256_000, rate_bps_up=128_000, seed=1003),
    "t4": dict(tier="T4", rtt_ms=1500.0, jitter_ms=500.0, loss_permille=350,
               rate_bps_down=32_000, rate_bps_up=32_000, seed=1004),
}
# The envelope band each tier promises (design §3.1). A non-canonical profile
# (a flap variant, a blackhole) must still sit inside its tier's band.
BAND = {
    "T0": dict(rtt=(0.0, 10.0), jitter=(0.0, 2.0), loss=(0, 0),
               down=(100_000_000, 10**12), up=(10_000_000, 10**12)),
    "T1": dict(rtt=(0.0, 50.0), jitter=(0.0, 10.0), loss=(0, 1),
               down=(5_000_000, 10**12), up=(1_000_000, 10**12)),
    "T2": dict(rtt=(0.0, 150.0), jitter=(0.0, 40.0), loss=(0, 20),
               down=(1_000_000, 10**12), up=(256_000, 10**12)),
    "T3": dict(rtt=(0.0, 400.0), jitter=(0.0, 100.0), loss=(0, 50),
               down=(256_000, 512_000), up=(128_000, 10**12)),
    "T4": dict(rtt=(0.0, 2000.0), jitter=(0.0, 1000.0), loss=(0, 500),
               down=(32_000, 64_000), up=(32_000, 10**12)),
}
STEP_FORMS = {
    "Advance", "Link", "CaptureChange", "ViewerInput",
    "ViewerDisconnect", "ViewerReconnect", "HostReconnect",
    "PathSwitch", "Assert",
}
INPUT_FORMS = {"PointerMove", "PointerButton", "Key", "Wheel", "PressedSet"}
INPUT_UNITS = {"ReleaseAll"}
SCREEN_WIDTH, SCREEN_HEIGHT = 1920, 1080
PROFILE_FIELDS = [
    "rtt_ms", "jitter_ms", "jitter_dist", "loss_permille", "burst_loss",
    "reorder_permille", "duplicate_permille", "rate_bps_down", "rate_bps_up",
    "mtu_bytes", "outage_schedule", "ack_delay_ms", "seed",
]
UNSAFE_TYPES = (str, bytes, list, dict, type(None))


def load(path, where):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as error:  # noqa: BLE001 - the message is the finding
        fail(where, "%s: %s" % (os.path.basename(path), error))
        return None


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def step_token(step):
    """The generator's step spelling for one validated step."""
    if isinstance(step, str):
        return {"ViewerDisconnect": "disconnect", "ViewerReconnect": "reconnect",
                "HostReconnect": "host_reconnect"}[step]
    form, value = next(iter(step.items()))
    if form == "Advance":
        return "advance:%d" % value
    if form == "Link":
        return "link:%s" % value["profile"]
    if form == "CaptureChange":
        return "change:%d,%d,%d,%d" % (value["x"], value["y"], value["width"], value["height"])
    if form == "ViewerInput":
        if isinstance(value, str):
            return "input:release_all"
        return "input:%s" % {
            "PointerMove": "pointer_move", "PointerButton": "pointer_button",
            "Key": "key", "Wheel": "wheel", "PressedSet": "pressed_set",
            "Clipboard": "clipboard",
        }[next(iter(value))]
    if form == "PathSwitch":
        return "path:%s" % value
    if form == "Assert":
        return "assert:%s" % value.split("-")[1]
    raise AssertionError("unvalidated step %r" % (step,))


def check_region(where, region):
    if not isinstance(region, dict):
        fail(where, "CaptureChange is not an object")
        return
    fields = ("x", "y", "width", "height")
    if any(not isinstance(region.get(f), int) or isinstance(region.get(f), bool) for f in fields):
        fail(where, "CaptureChange x/y/width/height must be integers")
        return
    if region["x"] < 0 or region["y"] < 0 or region["width"] < 1 or region["height"] < 1:
        fail(where, "CaptureChange must have positive width/height and non-negative x/y")
    if region["x"] + region["width"] > SCREEN_WIDTH or region["y"] + region["height"] > SCREEN_HEIGHT:
        fail(where, "CaptureChange is outside the canonical %dx%d screen"
             % (SCREEN_WIDTH, SCREEN_HEIGHT))


def check_input(where, kind):
    if isinstance(kind, str):
        if kind not in INPUT_UNITS:
            fail(where, "input %r is not ReleaseAll" % kind)
        return
    if not isinstance(kind, dict) or len(kind) != 1:
        fail(where, "ViewerInput must be exactly one input object")
        return
    form, value = next(iter(kind.items()))
    if form not in INPUT_FORMS:
        fail(where, "unknown input form %r" % form)
        return
    if form == "PointerMove":
        if not isinstance(value, dict) or not all(
            isinstance(value.get(f), int) and not isinstance(value.get(f), bool)
            for f in ("x", "y")
        ):
            fail(where, "PointerMove needs integer x/y")
    elif form == "PointerButton":
        if (not isinstance(value, dict) or value.get("button") not in ("left", "middle", "right")
                or not isinstance(value.get("pressed"), bool)):
            fail(where, "PointerButton needs button left|middle|right and pressed")
    elif form == "Key":
        if (not isinstance(value, dict) or not isinstance(value.get("code"), str)
                or not re.match(r"^[A-Za-z0-9]+$", value.get("code", ""))
                or not isinstance(value.get("pressed"), bool)):
            fail(where, "Key needs a DOM code and pressed")
    elif form == "Wheel":
        if not isinstance(value, dict) or not isinstance(value.get("delta"), int) \
                or isinstance(value.get("delta"), bool) or value.get("delta") == 0:
            fail(where, "Wheel needs a non-zero integer delta")
    elif form == "PressedSet":
        if (not isinstance(value, dict)
                or not isinstance(value.get("keys"), list)
                or not isinstance(value.get("buttons"), list)
                or any(not isinstance(k, str) for k in value.get("keys", []))
                or any(not isinstance(b, str) for b in value.get("buttons", []))):
            fail(where, "PressedSet needs string arrays keys/buttons")


# ── 1. the directory ────────────────────────────────────────────────────────
mark = len(problems)
readme = os.path.join(corpus, "README.md")
profile_dir = os.path.join(corpus, "profiles")
scenario_dir = os.path.join(corpus, "scenarios")
decision_dir = os.path.join(corpus, "decisions")
missing = [path for path in (readme, profile_dir, scenario_dir, decision_dir)
           if not os.path.exists(path)]
if missing:
    fail("corpus: files present",
         "missing " + ", ".join(os.path.relpath(path, corpus) for path in missing))
else:
    with open(readme, "r", encoding="utf-8") as handle:
        readme_text = handle.read()
    # The README must state how to regenerate the traces, so that nobody
    # edits a trace by hand because they could not find the command.
    if "conformance-gate.sh --update" not in readme_text:
        fail("corpus: files present", "README.md does not state the regeneration command")
if len(problems) == mark:
    ok("corpus: files present", "README.md, profiles/, scenarios/, decisions/")

# ── 2. profiles ─────────────────────────────────────────────────────────────
mark = len(problems)
profiles = {}
profile_paths = sorted(
    name for name in os.listdir(profile_dir) if name.endswith(".json")
) if os.path.isdir(profile_dir) else []
for name in profile_paths:
    doc = load(os.path.join(profile_dir, name), "profiles: parse")
    if doc is None:
        continue
    stem = name[:-5]
    if doc.get("schema") != 1:
        fail("profiles: parse", "%s: schema is %r, expected 1" % (name, doc.get("schema")))
    if doc.get("id") != stem:
        fail("profiles: parse", "%s: id %r does not equal the file stem" % (name, doc.get("id")))
    if doc.get("kind") not in ("tier", "flap", "blackhole"):
        fail("profiles: parse", "%s: unknown kind %r" % (name, doc.get("kind")))
    if doc.get("tier") not in BAND:
        fail("profiles: parse", "%s: unknown tier %r" % (name, doc.get("tier")))
    profiles[stem] = doc
if len(problems) == mark and len(profiles) != profile_expected:
    fail("profiles: parse", "%d profiles, expected %d" % (len(profiles), profile_expected))
if len(problems) == mark:
    ok("profiles: parse", "%d profiles" % len(profiles))

mark = len(problems)
for pid, doc in sorted(profiles.items()):
    where = "profiles: fields"
    if doc.get("kind") not in ("tier", "flap", "blackhole"):
        continue
    for field in PROFILE_FIELDS:
        if field not in doc:
            fail(where, "%s: field %s is missing" % (pid, field))
    if doc.get("jitter_dist") not in ("Normal", "Pareto"):
        fail(where, "%s: jitter_dist %r" % (pid, doc.get("jitter_dist")))
    if doc.get("mtu_bytes") not in (1150, 65536):
        fail(where, "%s: mtu_bytes %r is neither 1150 (DC) nor 65536 (stream)"
             % (pid, doc.get("mtu_bytes")))
    for field in ("rtt_ms", "jitter_ms"):
        if not is_number(doc.get(field)) or doc.get(field) < 0:
            fail(where, "%s: %s must be a non-negative number" % (pid, field))
    for field in ("loss_permille", "reorder_permille", "duplicate_permille",
                  "rate_bps_down", "rate_bps_up", "seed", "ack_delay_ms"):
        value = doc.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            fail(where, "%s: %s must be a non-negative integer" % (pid, field))
    burst = doc.get("burst_loss")
    if burst is not None and (
        not isinstance(burst, dict)
        or not isinstance(burst.get("mean_burst"), int)
        or burst.get("mean_burst") < 1
    ):
        fail(where, "%s: burst_loss must be null or {\"mean_burst\": >=1}" % pid)
    schedule = doc.get("outage_schedule")
    if not isinstance(schedule, list):
        fail(where, "%s: outage_schedule is not an array" % pid)
    else:
        cursor = -1
        for entry in schedule:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("start_ms"), int)
                    or not isinstance(entry.get("dur_ms"), int)
                    or entry.get("start_ms") < 0 or entry.get("dur_ms") < 1):
                fail(where, "%s: outage entry %r is malformed" % (pid, entry))
                continue
            if entry["start_ms"] <= cursor:
                fail(where, "%s: outage entries must be ordered and disjoint" % pid)
            cursor = entry["start_ms"] + entry["dur_ms"]
if len(problems) == mark:
    ok("profiles: fields", "LinkProfile fields of %d profiles" % len(profiles))

mark = len(problems)
for pid, doc in sorted(profiles.items()):
    where = "profiles: tier identity"
    kind = doc.get("kind")
    if kind == "tier":
        canonical = CANONICAL.get(pid)
        if canonical is None:
            fail(where, "%s: a tier profile must be named t0…t4" % pid)
            continue
        for field, expected in canonical.items():
            if doc.get(field) != expected:
                fail(where, "%s.%s is %r, the canonical point is %r"
                     % (pid, field, doc.get(field), expected))
        if doc.get("outage_schedule") != []:
            fail(where, "%s: a tier profile carries no outage schedule" % pid)
    elif kind == "flap":
        band = BAND.get(doc.get("tier"))
        if not band:
            continue
        for field, (low, high) in (("rtt_ms", band["rtt"]), ("jitter_ms", band["jitter"])):
            value = doc.get(field)
            if not is_number(value) or not low <= value <= high:
                fail(where, "%s.%s=%r is outside the %s band" % (pid, field, value, doc.get("tier")))
        for field, (low, high) in (("loss_permille", band["loss"]),
                                   ("rate_bps_down", band["down"]), ("rate_bps_up", band["up"])):
            value = doc.get(field)
            if not isinstance(value, int) or not low <= value <= high:
                fail(where, "%s.%s=%r is outside the %s band" % (pid, field, value, doc.get("tier")))
        flap = doc.get("flap")
        if (not isinstance(flap, dict)
                or not isinstance(flap.get("cycles"), int) or flap["cycles"] < 1
                or not isinstance(flap.get("down_ms"), int) or flap["down_ms"] < 1
                or not isinstance(flap.get("up_ms"), int) or flap["up_ms"] < 0):
            fail(where, "%s: flap must be {cycles>=1, down_ms>=1, up_ms>=0}" % pid)
        else:
            expected = [
                {"start_ms": i * (flap["down_ms"] + flap["up_ms"]), "dur_ms": flap["down_ms"]}
                for i in range(flap["cycles"])
            ]
            if doc.get("outage_schedule") != expected:
                fail(where, "%s: outage_schedule is not LinkProfile::flap(%d, %d, %d)"
                     % (pid, flap["cycles"], flap["down_ms"], flap["up_ms"]))
    elif kind == "blackhole":
        canonical = CANONICAL["t4"]
        for field, expected in canonical.items():
            if field in ("loss_permille", "seed"):
                continue
            if doc.get(field) != expected:
                fail(where, "%s.%s is %r, blackhole is the canonical t4 point with 100%% loss"
                     % (pid, field, doc.get(field)))
        if doc.get("loss_permille") != 1000:
            fail(where, "%s: blackhole must set loss_permille 1000" % pid)
        if doc.get("tier") != "T4" or doc.get("id") != "blackhole":
            fail(where, "%s: blackhole is the T4 availability profile named blackhole" % pid)
        if doc.get("outage_schedule") != []:
            fail(where, "%s: blackhole carries no outage schedule" % pid)
if len(problems) == mark:
    ok("profiles: tier identity", "canonical points and tier bands")

# ── 3. scenarios ────────────────────────────────────────────────────────────
mark = len(problems)
scenarios = {}
scenario_paths = sorted(
    name for name in os.listdir(scenario_dir) if name.endswith(".json")
) if os.path.isdir(scenario_dir) else []
for name in scenario_paths:
    doc = load(os.path.join(scenario_dir, name), "scenarios: parse")
    if doc is None:
        continue
    stem = name[:-5]
    if doc.get("schema") != 1:
        fail("scenarios: parse", "%s: schema is %r, expected 1" % (name, doc.get("schema")))
    if doc.get("id") != stem:
        fail("scenarios: parse", "%s: id %r does not equal the file stem" % (name, doc.get("id")))
    if doc.get("story") not in STORIES:
        fail("scenarios: parse", "%s: story %r is not S-1…S-15" % (name, doc.get("story")))
    if doc.get("tier") not in BAND:
        fail("scenarios: parse", "%s: tier %r" % (name, doc.get("tier")))
    tiers = doc.get("tiers")
    if (not isinstance(tiers, list) or not tiers or tiers[0] != doc.get("tier")
            or any(tier not in BAND for tier in tiers)
            or len(set(tiers)) != len(tiers)):
        fail("scenarios: parse",
             "%s: tiers must be a non-empty list starting with the scenario tier" % name)
    if doc.get("stage") not in ("S1", "S2"):
        fail("scenarios: parse", "%s: stage %r" % (name, doc.get("stage")))
    if not isinstance(doc.get("intent"), str) or len(doc.get("intent", "")) < 20:
        fail("scenarios: parse", "%s: intent must be a sentence" % name)
    if not isinstance(doc.get("simulator_cases"), list) or not doc["simulator_cases"]:
        fail("scenarios: parse", "%s: simulator_cases must be a non-empty array" % name)
    if not isinstance(doc.get("viewers"), int) or not 1 <= doc.get("viewers", 0) <= 8:
        fail("scenarios: parse", "%s: viewers must be 1…8" % name)
    if not isinstance(doc.get("steps"), list) or not doc.get("steps"):
        fail("scenarios: parse", "%s: steps must be a non-empty array" % name)
    scenarios[stem] = doc
if len(problems) == mark:
    ok("scenarios: parse", "%d scenarios" % len(scenarios))

mark = len(problems)
for sid, doc in sorted(scenarios.items()):
    where = "scenarios: steps"
    if not isinstance(doc.get("steps"), list):
        continue
    for index, step in enumerate(doc["steps"]):
        label = "%s step[%d]" % (sid, index)
        if isinstance(step, str):
            if step not in ("ViewerDisconnect", "ViewerReconnect", "HostReconnect"):
                fail(where, "%s: %r is not a declared unit step" % (label, step))
            continue
        if not isinstance(step, dict) or len(step) != 1:
            fail(where, "%s: a step must be one externally-tagged variant" % label)
            continue
        form, value = next(iter(step.items()))
        if form not in STEP_FORMS:
            fail(where, "%s: unknown step %r" % (label, form))
        elif form == "Advance":
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                fail(where, "%s: Advance must be a positive integer" % label)
        elif form == "Link":
            if not isinstance(value, dict) or not isinstance(value.get("profile"), str):
                fail(where, "%s: Link must be {\"profile\": \"<id>\"}" % label)
            elif value["profile"] not in profiles:
                fail(where, "%s: Link names unknown profile %r" % (label, value["profile"]))
            else:
                linked = profiles[value["profile"]]
                linked_tier = linked.get("tier")
                allowed = linked_tier in doc.get("tiers", [])
                # A blackhole is the tier-independent way a scenario expresses an
                # outage, so it is allowed from any tier.
                if linked.get("kind") == "blackhole":
                    allowed = True
                # The flap schedule is the T4 availability pattern (design §14.2).
                if linked.get("kind") == "flap" and "T4" in doc.get("tiers", []):
                    allowed = True
                if not allowed:
                    fail(where, "%s: Link %r is %s, the scenario declares tiers %s"
                         % (label, value["profile"], linked_tier, doc.get("tiers")))
        elif form == "CaptureChange":
            check_region("%s %s" % (where, label), value)
        elif form == "ViewerInput":
            check_input("%s %s" % (where, label), value)
        elif form == "PathSwitch":
            if value not in ("ws", "dc"):
                fail(where, "%s: PathSwitch %r is not ws|dc" % (label, value))
        elif form == "Assert":
            if value not in INVARIANTS:
                fail(where, "%s: Assert %r is not INV-1…INV-10" % (label, value))
if len(problems) == mark:
    ok("scenarios: steps", "step grammar, links, regions, invariants")

mark = len(problems)
stories = [doc.get("story") for doc in scenarios.values()]
if sorted(set(stories), key=lambda story: int(story.split("-")[1])) != STORIES:
    counts = {story: stories.count(story) for story in set(stories)}
    fail("scenarios: coverage",
         "stories are %s, expected each of S-1…S-15 at least once" % sorted(counts.items()))
else:
    seen_cases = []
    seen_tiers = {}
    for sid, doc in sorted(scenarios.items()):
        story = doc["story"]
        story_tiers = MATRIX_TIERS[story]
        for tier in doc.get("tiers", []):
            if tier not in story_tiers:
                fail("scenarios: coverage",
                     "%s exercises %s, which the matrix does not name for %s" % (sid, tier, story))
            seen_tiers.setdefault(story, set()).add(tier)
        for case in doc.get("simulator_cases", []):
            if case not in MATRIX_CASES[story]:
                fail("scenarios: coverage",
                     "%s names case %r, which the matrix does not list for %s" % (sid, case, story))
            seen_cases.append(case)
    for story in STORIES:
        required = set(MATRIX_TIERS[story])
        if seen_tiers.get(story, set()) != required:
            fail("scenarios: coverage",
                 "%s is run at %s, the matrix names %s"
                 % (story, sorted(seen_tiers.get(story, set())), sorted(required)))
        asserted = set()
        for doc in scenarios.values():
            if doc.get("story") != story:
                continue
            asserted.update(step["Assert"] for step in doc.get("steps", [])
                            if isinstance(step, dict) and "Assert" in step)
        for invariant in MATRIX_INVARIANTS.get(story, []):
            if invariant not in asserted:
                fail("scenarios: coverage",
                     "%s asserts %s, the matrix requires %s" % (story, sorted(asserted), invariant))
    expected_cases = [case for cases in MATRIX_CASES.values() for case in cases]
    if sorted(seen_cases) != sorted(expected_cases):
        fail("scenarios: coverage", "case coverage differs: missing %s, unexpected %s"
             % (sorted(set(expected_cases) - set(seen_cases)),
                sorted(set(seen_cases) - set(expected_cases))))
    if len(expected_cases) != case_expected:
        fail("scenarios: coverage", "the frozen matrix holds %d cases, this gate expects %d"
             % (len(expected_cases), case_expected))
if len(problems) == mark:
    ok("scenarios: coverage", "S-1…S-15, %d scenarios, %d cases"
       % (len(scenarios), case_expected))

# ── 4. the consumers ────────────────────────────────────────────────────────
mark = len(problems)
root = os.path.dirname(os.path.dirname(os.path.abspath(corpus)))
# The Rust side of the corpus is its own generator, which lives inside this
# directory and drives the simulator the reference implementation owns
# (`mlp-core/src/testing/scenario.rs`). It is compiled by the gate (see
# `build_generator` in the shell half) rather than by cargo, so that no
# crate's manifest is touched by the corpus.
generator = os.path.join(corpus, "tools/corpus-gen.rs")
core = os.environ.get("MLP_CORE_DIR") or os.path.join(root, "..", "mlp-core")
simulator = os.path.join(core, "src", "testing", "scenario.rs")
ts_consumer = os.path.join(root, "src/consumer.ts")
if not os.path.exists(generator):
    fail("consumers: cross-reference", "the corpus generator %s is missing"
         % os.path.relpath(generator, root))
else:
    with open(generator, "r", encoding="utf-8") as handle:
        source = handle.read()
    if "mlp_core::testing::scenario" not in source:
        fail("consumers: cross-reference",
             "the generator does not drive the simulator's scenario API")
    if "mlp-corpus: no corpus" not in source:
        fail("consumers: cross-reference", "the generator does not carry the no-corpus marker")
if not os.path.exists(simulator):
    fail("consumers: cross-reference",
         "the simulator the corpus replays is missing: %s" % os.path.relpath(simulator, root))
if os.path.exists(ts_consumer):
    with open(ts_consumer, "r", encoding="utf-8") as handle:
        source = handle.read()
    matches = re.findall(r'"((?:\./|\.\./)+conformance[^"\n]*)"', source)
    if not matches:
        fail("consumers: cross-reference",
             "the TypeScript consumer does not name a path under conformance/transport")
    else:
        resolved = os.path.normpath(os.path.join(os.path.dirname(ts_consumer), matches[0]))
        if not (resolved == os.path.abspath(corpus)
                or resolved.startswith(os.path.abspath(corpus) + os.sep)):
            fail("consumers: cross-reference",
                 "the TypeScript consumer resolves to %s, not %s" % (resolved, os.path.abspath(corpus)))
if len(problems) == mark:
    ok("consumers: cross-reference",
       "the corpus generator drives the sans-IO simulator; the TypeScript consumer resolves here")

# ── 5. the traces ───────────────────────────────────────────────────────────
#
# One document per scenario, produced by the corpus generator (the shell half
# compiles and runs it). The wrapper carries the scenario identity and the
# simulator's own 16-hex digest; the trace itself is `Trace::to_json()` verbatim,
# which is what makes the file reviewable and byte-comparable. `violations` is
# the stress-free assertion: the simulator records one for every invariant a
# run breaks, so an empty list is a run that met the story it replays.
mark = len(problems)
runner_present = 1 if (os.path.exists(generator) or os.environ.get("RM_TRANSPORT_RUNNER")) else 0
trace_paths = sorted(
    name for name in os.listdir(decision_dir) if name.endswith(".json")
) if os.path.isdir(decision_dir) else []
required_trace_fields = ("name", "tier", "updates", "acks", "plans", "states", "violations", "counters", "link")
if update_mode:
    # `--update` exists to write this half: validating a set that is about to be
    # replaced would fail the very invocation that fixes it.
    defer("decisions: traces", "regenerated in place by --update")
elif not trace_paths:
    fail("decisions: traces",
         "no trace: regenerate with `just transport-vectors --update`")
else:
    by_scenario = {}
    for name in trace_paths:
        doc = load(os.path.join(decision_dir, name), "decisions: traces")
        if doc is None:
            continue
        where = "decisions: traces"
        if doc.get("schema") != 1:
            fail(where, "%s: schema %r" % (name, doc.get("schema")))
        scenario = doc.get("scenario")
        if scenario not in scenarios:
            fail(where, "%s: scenario %r is not committed" % (name, scenario))
            continue
        if name != scenario + ".json":
            fail(where, "%s: name does not match scenario %r" % (name, scenario))
        if scenario in by_scenario:
            fail(where, "%s: a second trace for %s" % (name, scenario))
        by_scenario[scenario] = doc
        story = scenarios[scenario].get("story")
        tier = scenarios[scenario].get("tier")
        if doc.get("story") != story:
            fail(where, "%s: story %r, the scenario is %r" % (name, doc.get("story"), story))
        if doc.get("tier") != tier.lower():
            fail(where, "%s: tier %r, the scenario is %r" % (name, doc.get("tier"), tier.lower()))
        if not isinstance(doc.get("runner"), str) or not doc.get("runner"):
            fail(where, "%s: runner is missing" % name)
        if not isinstance(doc.get("digest"), str) or not HEX16.match(doc.get("digest", "")):
            fail(where, "%s: digest is not 16 lowercase hex digits" % name)
        trace = doc.get("trace")
        if not isinstance(trace, dict):
            fail(where, "%s: trace is not an object" % name)
            continue
        for field in required_trace_fields:
            if field not in trace:
                fail(where, "%s: trace.%s is missing" % (name, field))
        if trace.get("name") != scenario:
            fail(where, "%s: trace.name %r is not the scenario id" % (name, trace.get("name")))
        if trace.get("tier") != tier:
            fail(where, "%s: trace.tier %r is not %r" % (name, trace.get("tier"), tier))
        violations = trace.get("violations")
        if not isinstance(violations, list):
            fail(where, "%s: trace.violations is not an array" % name)
        elif violations:
            fail(where, "%s: the run broke %d invariant(s): %s"
                 % (name, len(violations), violations[:2]))
    missing_traces = sorted(set(scenarios) - set(by_scenario))
    if missing_traces:
        fail("decisions: traces", "no trace for %s" % ", ".join(missing_traces))
if not update_mode and trace_paths and len(problems) == mark:
    ok("decisions: traces", "%d traces, one per scenario, all clean" % len(trace_paths))

print("corpus-static: scenarios=%d traces=%d runner_present=%d profiles=%d"
      % (len(scenarios), len(trace_paths), runner_present, len(profiles)))
program_out = os.environ.get("MLP_PROGRAM_OUT", "")
if program_out and not problems:
    with open(program_out, "w", encoding="utf-8") as handle:
        for pid, doc in sorted(profiles.items()):
            outages = "-" if not doc["outage_schedule"] else ",".join(
                "%d:%d" % (entry["start_ms"], entry["dur_ms"]) for entry in doc["outage_schedule"])
            burst = "-" if doc["burst_loss"] is None else str(doc["burst_loss"]["mean_burst"])
            handle.write("P|%s|%s|%.1f|%.1f|%d|%s|%d|%d|%d|%d|%d|%s|%d|%d\n" % (
                pid, doc["jitter_dist"], float(doc["rtt_ms"]), float(doc["jitter_ms"]),
                doc["loss_permille"], burst, doc["reorder_permille"], doc["duplicate_permille"],
                doc["rate_bps_down"], doc["rate_bps_up"], doc["mtu_bytes"], outages,
                doc["ack_delay_ms"], doc["seed"]))
        for sid, doc in sorted(scenarios.items()):
            handle.write("S|%s|%s|%s|%d|%s\n" % (
                sid, doc["story"], doc["tier"].lower(), doc["viewers"],
                ";".join(step_token(step) for step in doc["steps"])))
    print("mlp-corpus: program=%s" % program_out)
sys.exit(1 if problems else 0)