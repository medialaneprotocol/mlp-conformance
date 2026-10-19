//! The corpus generator: replays the committed scenarios through the
//! reference simulator and writes the golden decision traces of `decisions/`.
//!
//! WHY THIS FILE EXISTS, AND WHY IT LIVES HERE
//! =============================================
//! The specification makes `decisions/*.json` the Rust-produced half of the
//! conformance corpus, and the gate the checker that regenerates and
//! byte-compares it. The simulator that produces those traces is
//! `mlp-core`'s (`src/testing/`), and the corpus is this directory; neither
//! may import the other's build, so this file is the bridge and it belongs to
//! the corpus:
//!
//!   * it is **not** a crate and not a workspace member — the gate compiles it
//!     with `rustc` against the already-built `mlp-core` rlib, so no manifest
//!     changes and no new dependency closure;
//!   * it contains **no policy**: the scenarios, the profiles and their numbers
//!     come from the committed JSON, rendered by the gate into the translation
//!     unit this program reads. A profile field here is a field there.
//!   * it fails loudly when a trace is not clean (`Trace::assert_clean`'s
//!     condition): a scenario whose run violates an invariant must not be
//!     committed silently as a "golden" trace.
//!
//! Input (rendered by the gate from the committed JSON, so the JSON stays the
//! single authority):
//!
//!   `MLP_CORPUS_PROGRAM`  path to the translation unit:
//!       `P|<id>|<dist>|<rtt>|<jitter>|<loss>|<burst|->|<reorder>|<dup>|<down>|<up>|<mtu>|<outages|->|<ack>|<seed>`
//!       `S|<id>|<story>|<tier>|<viewers>|<step>;<step>;…`
//!     with steps `advance:<ms>`, `link:<profile>`, `change:<x>,<y>,<w>,<h>`,
//!     `input:<kind>`, `disconnect`, `reconnect`, `host_reconnect`,
//!     `path:<ws|dc>`, `assert:<1..10>`.
//!
//! Output (`MLP_CORPUS_OUT`):
//!
//!   `<scenario-id>.json` = `{"schema":1,"scenario":…,"story":…,"tier":…,
//!   "runner":…,"digest":"<16 hex>","trace":<Trace::to_json() verbatim>}`
//!
//! and one line on stdout: `mlp-corpus: scenarios=<N> traces=<M>`.
//! When the translation unit is absent it prints `mlp-corpus: no corpus` and
//! writes nothing — the marker the gate counts and requires zero.

use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use mlp_core::media::MediaPath;
use mlp_core::media::cells::CellRect;
use mlp_core::media::input::InputKind;
use mlp_core::testing::lossy_link::{JitterDist, LinkProfile, Outage, Tier};
use mlp_core::testing::scenario::{Invariant, Scenario, Step, run};

const ABSENT_MARKER: &str = "mlp-corpus: no corpus";
const RUNNER: &str = "conformance/tools/corpus-gen.rs";

fn fail(message: &str) -> ExitCode {
    eprintln!("corpus-gen: {message}");
    ExitCode::from(2)
}

fn tier_of(name: &str) -> Option<Tier> {
    match name {
        "t0" => Some(Tier::T0),
        "t1" => Some(Tier::T1),
        "t2" => Some(Tier::T2),
        "t3" => Some(Tier::T3),
        "t4" => Some(Tier::T4),
        _ => None,
    }
}

fn parse_profile(line: &str) -> Result<(String, LinkProfile), String> {
    let fields: Vec<&str> = line.split('|').collect();
    if fields.len() != 15 || fields[0] != "P" {
        return Err(format!("profile line has {} fields, expected 15", fields.len()));
    }
    let number = |index: usize| -> Result<f64, String> {
        fields[index]
            .parse::<f64>()
            .map_err(|error| format!("field {} ({:?}): {error}", index, fields[index]))
    };
    let unsigned = |index: usize| -> Result<u64, String> {
        fields[index]
            .parse::<u64>()
            .map_err(|error| format!("field {} ({:?}): {error}", index, fields[index]))
    };
    let jitter_dist = match fields[2] {
        "Normal" => JitterDist::Normal,
        "Pareto" => JitterDist::Pareto,
        other => return Err(format!("unknown jitter distribution {other}")),
    };
    let burst_loss = if fields[6] == "-" {
        None
    } else {
        Some(fields[6].parse::<u32>().map_err(|error| format!("burst_loss: {error}"))?)
    };
    let mut outage_schedule = Vec::new();
    if fields[12] != "-" {
        for entry in fields[12].split(',') {
            let (start, dur) = entry
                .split_once(':')
                .ok_or_else(|| format!("outage {entry:?} is not start:dur"))?;
            outage_schedule.push(Outage {
                start_ms: start.parse().map_err(|error| format!("outage start: {error}"))?,
                dur_ms: dur.parse().map_err(|error| format!("outage duration: {error}"))?,
            });
        }
    }
    Ok((
        fields[1].to_string(),
        LinkProfile {
            rtt_ms: number(3)?,
            jitter_ms: number(4)?,
            jitter_dist,
            loss_permille: unsigned(5)? as u32,
            burst_loss,
            reorder_permille: unsigned(7)? as u32,
            duplicate_permille: unsigned(8)? as u32,
            rate_bps_down: unsigned(9)?,
            rate_bps_up: unsigned(10)?,
            mtu_bytes: unsigned(11)? as u32,
            outage_schedule,
            ack_delay_ms: unsigned(13)?,
            seed: unsigned(14)?,
        },
    ))
}

fn parse_step(
    token: &str,
    profiles: &HashMap<String, LinkProfile>,
) -> Result<Step, String> {
    if let Some(ms) = token.strip_prefix("advance:") {
        return Ok(Step::Advance(
            ms.parse::<u64>().map_err(|error| format!("advance {ms:?}: {error}"))?,
        ));
    }
    if let Some(id) = token.strip_prefix("link:") {
        let profile = profiles
            .get(id)
            .ok_or_else(|| format!("unknown profile {id:?}"))?;
        return Ok(Step::Link(Box::new(profile.clone())));
    }
    if let Some(rect) = token.strip_prefix("change:") {
        let parts: Vec<&str> = rect.split(',').collect();
        if parts.len() != 4 {
            return Err(format!("change {rect:?} is not x,y,w,h"));
        }
        let value = |index: usize| -> Result<u32, String> {
            parts[index]
                .parse::<u32>()
                .map_err(|error| format!("change {rect:?}: {error}"))
        };
        return Ok(Step::CaptureChange(CellRect::new(
            value(0)?,
            value(1)?,
            value(2)?,
            value(3)?,
        )));
    }
    if let Some(kind) = token.strip_prefix("input:") {
        let kind = match kind {
            "pointer_move" => InputKind::PointerMove,
            "pointer_button" => InputKind::PointerButton,
            "key" => InputKind::Key,
            "wheel" => InputKind::Wheel,
            "clipboard" => InputKind::Clipboard,
            "release_all" => InputKind::ReleaseAll,
            "pressed_set" => InputKind::PressedSet,
            other => return Err(format!("unknown input kind {other:?}")),
        };
        return Ok(Step::ViewerInput(kind));
    }
    if let Some(path) = token.strip_prefix("path:") {
        let path = match path {
            "ws" => MediaPath::Ws,
            "dc" => MediaPath::DcDirect,
            other => return Err(format!("unknown path {other:?}")),
        };
        return Ok(Step::PathSwitch(path));
    }
    if let Some(invariant) = token.strip_prefix("assert:") {
        let number = invariant
            .parse::<usize>()
            .map_err(|error| format!("assert {invariant:?}: {error}"))?;
        if !(1..=10).contains(&number) {
            return Err(format!("assert {number} is not 1..=10"));
        }
        return Ok(Step::Assert(Invariant::all()[number - 1]));
    }
    match token {
        "disconnect" => Ok(Step::ViewerDisconnect),
        "reconnect" => Ok(Step::ViewerReconnect),
        "host_reconnect" => Ok(Step::HostReconnect),
        other => Err(format!("unknown step {other:?}")),
    }
}

fn main() -> ExitCode {
    let out_dir = match env::var("MLP_CORPUS_OUT") {
        Ok(value) if !value.is_empty() => PathBuf::from(value),
        _ => return fail("MLP_CORPUS_OUT is not set; nothing would be written"),
    };
    let program = match env::var("MLP_CORPUS_PROGRAM") {
        Ok(value) if !value.is_empty() => PathBuf::from(value),
        _ => {
            // The corpus is not on this host: print the marker the gate counts,
            // write nothing, and pass — a missing corpus must be visible as a
            // count, never as a smaller green run.
            println!("{ABSENT_MARKER}");
            return ExitCode::SUCCESS;
        }
    };
    if !Path::new(&program).is_file() {
        println!("{ABSENT_MARKER}");
        return ExitCode::SUCCESS;
    }

    let text = match fs::read_to_string(&program) {
        Ok(value) => value,
        Err(error) => return fail(&format!("cannot read {}: {error}", program.display())),
    };

    let mut profiles: HashMap<String, LinkProfile> = HashMap::new();
    let mut scenarios: Vec<(String, String, Tier, u8, String)> = Vec::new();
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        if let Some(rest) = line.strip_prefix('P') {
            let line = format!("P{rest}");
            match parse_profile(&line) {
                Ok((id, profile)) => {
                    profiles.insert(id, profile);
                }
                Err(error) => return fail(&error),
            }
        } else if let Some(rest) = line.strip_prefix('S') {
            let line = format!("S{rest}");
            let fields: Vec<&str> = line.split('|').collect();
            if fields.len() != 6 {
                return fail(&format!("scenario line has {} fields, expected 6", fields.len()));
            }
            let tier = match tier_of(fields[3]) {
                Some(tier) => tier,
                None => return fail(&format!("unknown tier {:?}", fields[3])),
            };
            let viewers: u8 = match fields[4].parse() {
                Ok(value) => value,
                Err(error) => return fail(&format!("viewers {:?}: {error}", fields[4])),
            };
            scenarios.push((
                fields[1].to_string(),
                fields[2].to_string(),
                tier,
                viewers,
                fields[5].to_string(),
            ));
        } else {
            return fail(&format!("unknown program line {line:?}"));
        }
    }

    if let Err(error) = fs::create_dir_all(&out_dir) {
        return fail(&format!("cannot create {}: {error}", out_dir.display()));
    }

    let mut dirty = 0usize;
    let mut traces = 0usize;
    for (id, story, tier, viewers, step_field) in &scenarios {
        let mut steps = Vec::new();
        if !step_field.is_empty() {
            for token in step_field.split(';') {
                match parse_step(token, &profiles) {
                    Ok(step) => steps.push(step),
                    Err(error) => return fail(&format!("{id}: {error}")),
                }
            }
        }
        let scenario = Scenario {
            name: id.clone(),
            tier: *tier,
            steps,
            viewers: *viewers,
        };
        let trace = run(&scenario);
        if !trace.violations.is_empty() {
            dirty += 1;
            eprintln!(
                "corpus-gen: {id} violated {} invariant(s): {:?}",
                trace.violations.len(),
                &trace.violations[..trace.violations.len().min(3)]
            );
        }
        let document = format!(
            "{{\"schema\":1,\"scenario\":{scenario_json},\"story\":{story_json},\
             \"tier\":{tier_json},\"runner\":{runner_json},\"digest\":{digest_json},\
             \"trace\":{trace_json}}}\n",
            scenario_json = json_string(id),
            story_json = json_string(story),
            tier_json = json_string(tier.as_str()),
            runner_json = json_string(RUNNER),
            digest_json = json_string(&trace.digest()),
            trace_json = trace.to_json(),
        );
        let path = out_dir.join(format!("{id}.json"));
        if let Err(error) = fs::write(&path, document) {
            return fail(&format!("cannot write {}: {error}", path.display()));
        }
        traces += 1;
    }

    println!("mlp-corpus: scenarios={} traces={traces}", scenarios.len());
    if dirty > 0 {
        eprintln!("corpus-gen: {dirty} trace(s) are not clean; see the lines above");
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}

/// A minimal JSON string encoder for the wrapper fields (the trace itself is
/// serialized by `Trace::to_json`).
fn json_string(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for character in value.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            control if control.is_control() => {
                out.push_str(&format!("\\u{:04x}", control as u32))
            }
            other => out.push(other),
        }
    }
    out.push('"');
    out
}
