#!/usr/bin/env bash
# The Media Lane Protocol conformance gate.
#
# The corpus is the protocol's shared, executable description of what it
# promises. This gate is what keeps it honest:
#
#   1. the static validator checks the corpus's own shape — every profile is
#      a canonical tier point, every scenario's steps parse, the acceptance
#      matrix's coverage is complete, and every trace is clean;
#   2. the generator replays the committed scenarios through the reference
#      implementation and writes the golden decision traces, which the gate
#      then compares byte for byte with the committed ones;
#   3. the generator's "no corpus" marker is counted and required to be zero,
#      so a deleted or unreadable corpus cannot pass as a green run that
#      compared nothing.
#
# Usage:
#   scripts/conformance-gate.sh             # run the gate
#   scripts/conformance-gate.sh --update    # regenerate the traces in place
#   scripts/conformance-gate.sh --self-test # check the checker
#
# The gate needs a Rust toolchain and Python 3, and nothing else: it must run
# in CI, in a container, and on a developer machine with the same result.
#
# The reference implementation is expected next to this repository as
# `../mlp-core`; `MLP_CORE_DIR` overrides that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORPUS_DIR="$REPO_ROOT"
CORE_DIR="${MLP_CORE_DIR:-$REPO_ROOT/../mlp-core}"
LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/mlp-conformance-XXXXXX")"
trap 'rm -rf "$LOGDIR"' EXIT

SCENARIO_COUNT_EXPECTED=16
PROFILE_COUNT_EXPECTED=7
SIMULATOR_CASE_COUNT_EXPECTED=25

UPDATE=0
SELFTEST=0
for argument in "$@"; do
    case "$argument" in
        --update) UPDATE=1 ;;
        --self-test) SELFTEST=1 ;;
        *) printf 'mlp-conformance-gate: unknown argument %s\n' "$argument" >&2; exit 2 ;;
    esac
done

# ── the static validator ────────────────────────────────────────────────────
#
# It is its own process, so its verdict lines are folded into this shell's
# ledger here: its FAIL lines are the gate's failures, and a static run that
# failed must not be able to exit 0.

static_check() {
    local log="$LOGDIR/corpus-static.log" rc=0
    MLP_PROGRAM_OUT="$LOGDIR/program.txt" \
    MLP_CORE_DIR="$CORE_DIR" \
        python3 "$CORPUS_DIR/tools/corpus_static.py" "$CORPUS_DIR" \
            "$SCENARIO_COUNT_EXPECTED" "$PROFILE_COUNT_EXPECTED" \
            "$SIMULATOR_CASE_COUNT_EXPECTED" "$UPDATE" >"$log" 2>&1 || rc=$?
    cat "$log"

    local good bad
    good="$(grep -c '^ok ' "$log" || true)"
    bad="$(grep -c '^FAIL ' "$log" || true)"
    PASSES=$((PASSES + ${good:-0}))
    FAILURES=$((FAILURES + ${bad:-0}))

    if [ "$rc" = "2" ]; then
        printf 'mlp-conformance-gate: the static validator could not run\n' >&2
        exit 2
    fi
    if [ "$rc" != "0" ] && [ "${bad:-0}" = "0" ]; then
        printf 'mlp-conformance-gate: the static validator failed without a verdict\n' >&2
        FAILURES=$((FAILURES + 1))
    fi
}

# ── the generator ───────────────────────────────────────────────────────────
#
# It is not a crate: the gate compiles it with rustc against the already-built
# reference library, so the corpus adds no dependency to anything.

build_generator() { # prints the binary path, or fails with its log
    local source="$CORPUS_DIR/tools/corpus-gen.rs"
    [ -f "$source" ] || die "the generator is missing at $source"

    local rlib_json="$LOGDIR/rlib.json"
    if ! ( cd "$CORE_DIR" && cargo build --features testing \
            --message-format=json --quiet ) >"$rlib_json" 2>"$LOGDIR/cargo.log"; then
        die "the reference library could not be built; see $LOGDIR/cargo.log"
    fi

    # Two lines out of the artifact message: the rlib to link and the directory
    # its own dependencies live in. Cargo reports the *uplifted* rlib and the
    # hashed metadata beside it; the hashed rlib is the one whose dependency
    # directory is `deps/`, which is where every transitive rlib is.
    local selection
    selection="$(python3 - "$rlib_json" <<'PY'
import json
import os
import sys

rlib = ""
meta = ""
with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("reason") != "compiler-artifact":
            continue
        if "mlp-core" not in message.get("package_id", ""):
            continue
        if message["target"].get("kind") != ["lib"]:
            continue
        if "testing" not in message.get("features", []):
            continue
        for name in message.get("filenames", []):
            if name.endswith(".rlib"):
                rlib = name
            elif name.endswith(".rmeta"):
                meta = name
if meta:
    hashed = meta[: -len(".rmeta")] + ".rlib"
    if os.path.exists(hashed):
        rlib = hashed
if not rlib:
    sys.exit("the build produced no rlib")
deps = os.path.dirname(rlib)
if os.path.basename(deps) == "deps":
    deps = os.path.dirname(rlib)
print(rlib)
print(deps)
PY
    )" || die "could not locate the reference library's rlib"

    local rlib deps binary
    rlib="$(printf '%s\n' "$selection" | sed -n '1p')"
    deps="$(printf '%s\n' "$selection" | sed -n '2p')"
    binary="$LOGDIR/corpus-gen"
    if ! rustc --edition 2021 -O \
            --extern "mlp_core=$rlib" \
            -L "dependency=$deps" \
            -o "$binary" "$source" 2>"$LOGDIR/rustc.log"; then
        die "the generator did not compile; see $LOGDIR/rustc.log"
    fi
    printf '%s' "$binary"
}

run_generator() { # outdir
    local out="$1" binary
    binary="$(build_generator)"
    ( cd "$CORPUS_DIR" && MLP_CORPUS_OUT="$out" \
        MLP_CORPUS_PROGRAM="$LOGDIR/program.txt" "$binary" )
}

# ── the dynamic half ────────────────────────────────────────────────────────

check_traces() {
    local scratch="$LOGDIR/decisions"
    rm -rf "$scratch"
    mkdir -p "$scratch"

    if ! run_generator "$scratch" >"$LOGDIR/runner.log" 2>&1; then
        fail "corpus: traces reproducible" "the generator exited non-zero; its log follows"
        tail -20 "$LOGDIR/runner.log" >&2
        return
    fi

    local absent
    absent="$(grep -c 'mlp-corpus: no corpus' "$LOGDIR/runner.log" || true)"
    if [ "$absent" != "0" ]; then
        fail "corpus: traces reproducible" \
            "the generator printed its no-corpus marker ${absent} time(s)"
        return
    fi

    local marker
    marker="$(grep -oE 'mlp-corpus: scenarios=[0-9]+ traces=[0-9]+' "$LOGDIR/runner.log" | tail -1)"
    if [ -z "$marker" ]; then
        fail "corpus: traces reproducible" "the generator printed no summary line"
        return
    fi
    local runner_scenarios runner_traces
    runner_scenarios="$(printf '%s' "$marker" | sed -E 's/.*scenarios=([0-9]+).*/\1/')"
    runner_traces="$(printf '%s' "$marker" | sed -E 's/.*traces=([0-9]+).*/\1/')"

    if [ "$runner_scenarios" != "$SCENARIO_COUNT_EXPECTED" ]; then
        fail "corpus: generator scenario count" \
            "expected $SCENARIO_COUNT_EXPECTED, the generator read $runner_scenarios"
        return
    fi

    if [ "$UPDATE" = "1" ]; then
        rm -rf "$CORPUS_DIR/decisions"
        cp -r "$scratch" "$CORPUS_DIR/decisions"
        ok "corpus: traces updated ($runner_traces)"
        return
    fi

    if diff -r --exclude=README.md "$scratch" "$CORPUS_DIR/decisions" >"$LOGDIR/diff.log" 2>&1; then
        ok "corpus: $runner_traces traces byte-identical"
    else
        fail "corpus: traces reproducible" \
            "the regenerated traces differ from the committed ones (run with --update)"
        sed -n '1,20p' "$LOGDIR/diff.log" >&2
    fi
}

# ── the checker's own check ─────────────────────────────────────────────────
#
# A gate that cannot fail is not a gate. Each case mutates a copy of the
# corpus and requires the gate to notice.

self_test() {
    local scratch status
    scratch="$(mktemp -d "${TMPDIR:-/tmp}/mlp-selftest-XXXXXX")"
    trap 'rm -rf "$scratch"' RETURN
    cp -r "$CORPUS_DIR/." "$scratch/"

    local checks=0 failures=0

    # 1. A profile whose numbers are not the canonical tier point must be
    #    noticed.
    python3 - "$scratch/profiles/t2.json" <<'PY'
import json
import sys

path = sys.argv[1]
profile = json.load(open(path))
profile["rtt_ms"] = 999.0
json.dump(profile, open(path, "w"), indent=2)
PY
    MLP_CORE_DIR="$CORE_DIR" python3 "$scratch/tools/corpus_static.py" "$scratch" \
        16 7 25 0 >"$scratch/verdict-1.txt" 2>&1 || true
    if grep -q '^FAIL profiles: tier identity' "$scratch/verdict-1.txt"; then
        checks=$((checks + 1))
    else
        echo "self-test: a non-canonical tier point was not noticed" >&2
        failures=$((failures + 1))
    fi

    # 2. A scenario that drops one of the matrix's simulator cases must be
    #    noticed.
    rm -f "$scratch/scenarios/s01-t2-typing-echo.json"
    MLP_CORE_DIR="$CORE_DIR" python3 "$scratch/tools/corpus_static.py" "$scratch" \
        16 7 25 0 >"$scratch/verdict-2.txt" 2>&1 || true
    if grep -q '^FAIL scenarios: coverage' "$scratch/verdict-2.txt"; then
        checks=$((checks + 1))
    else
        echo "self-test: a missing matrix case was not noticed" >&2
        failures=$((failures + 1))
    fi

    # 3. A decision trace that no longer matches its scenario must be
    #    noticed.
    cp "$REPO_ROOT/scenarios/s01-t2-typing-echo.json" "$scratch/scenarios/"
    cp "$REPO_ROOT/decisions/s01-t2-typing-echo.json" "$scratch/decisions/"
    python3 - "$scratch/decisions/s01-t2-typing-echo.json" <<'PY'
import json
import sys

path = sys.argv[1]
trace = json.load(open(path))
# A digest that is not 16 lowercase hex digits: the shape check must catch it.
trace["digest"] = "NOT-A-DIGEST"
json.dump(trace, open(path, "w"), indent=2)
PY
    MLP_CORE_DIR="$CORE_DIR" python3 "$scratch/tools/corpus_static.py" "$scratch" \
        16 7 25 0 >"$scratch/verdict-3.txt" 2>&1 || true
    if grep -q '^FAIL decisions: traces' "$scratch/verdict-3.txt"; then
        checks=$((checks + 1))
    else
        echo "self-test: a tampered trace was not noticed" >&2
        failures=$((failures + 1))
    fi

    if [ "$failures" -eq 0 ]; then
        echo "self-test: $checks mutation(s) rejected"
        return 0
    fi
    echo "self-test: $failures mutation(s) were not rejected" >&2
    return 1
}

# ── main ────────────────────────────────────────────────────────────────────

PASSES=0
FAILURES=0

ok() {
    PASSES=$((PASSES + 1))
    printf '  ok    %s\n' "$1"
}

fail() {
    FAILURES=$((FAILURES + 1))
    printf '  FAIL  %s\n' "$1" >&2
    if [ $# -gt 1 ]; then
        printf '        %s\n' "$2" >&2
    fi
}

die() {
    printf 'mlp-conformance-gate: %s\n' "$1" >&2
    exit 2
}

echo "mlp-conformance-gate: checking the corpus at $CORPUS_DIR"

if [ "$SELFTEST" = "1" ]; then
    if self_test; then
        exit 0
    fi
    exit 1
fi

if [ ! -d "$CORE_DIR" ]; then
    die "the reference implementation is missing at $CORE_DIR (set MLP_CORE_DIR)"
fi

static_check
check_traces

if [ "$FAILURES" -eq 0 ]; then
    echo "mlp-conformance-gate: all checks passed ($PASSES)"
    exit 0
fi
echo "mlp-conformance-gate: $FAILURES check(s) failed" >&2
exit 1
