#!/usr/bin/env bash
# The Media Lane Protocol conformance gate.
#
# The corpus is the protocol's shared, executable description of what it
# promises. This gate is what keeps it honest:
#
#   1. the harness — the protocol's verification layer — replays every
#      committed scenario and writes the decision trace it produced, and the
#      gate compares those traces with the committed ones, byte for byte;
#   2. the harness publishes the protocol's tier table, and the gate compares
#      the published table with the committed one, byte for byte;
#   3. the acceptance matrix names every story the specification states and
#      every scenario the corpus commits, and the gate checks it.
#
# The harness owns the shape: it reads the scenarios, it decides whether a
# run is clean, it writes the traces, and it writes the tier table. The
# corpus owns the content: the scenarios, the matrix, the table and the
# traces.
#
# Usage:
#   scripts/conformance-gate.sh             # run the gate
#   scripts/conformance-gate.sh --update    # regenerate the traces and the table in place
#   scripts/conformance-gate.sh --self-test # check the checker
#
# The gate needs a Rust toolchain and Python 3, and nothing else, so it runs
# the same in CI, in a container, and on a developer machine. The harness is
# expected next to this repository as `../mlp-harness`; `MLP_HARNESS_DIR`
# overrides that. `MLP_CORPUS_DIR` overrides the corpus's own directory, and
# the gate's self-test uses it to run the gate against a mutated copy.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORPUS_DIR="${MLP_CORPUS_DIR:-$REPO_ROOT}"
HARNESS_DIR="${MLP_HARNESS_DIR:-$REPO_ROOT/../mlp-harness}"
LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/mlp-conformance-XXXXXX")"
trap 'rm -rf "$LOGDIR"' EXIT

SCENARIO_COUNT_EXPECTED=16
STORY_COUNT_EXPECTED=15

UPDATE=0
SELFTEST=0
for argument in "$@"; do
    case "$argument" in
        --update) UPDATE=1 ;;
        --self-test) SELFTEST=1 ;;
        *) printf 'mlp-conformance-gate: unknown argument %s\n' "$argument" >&2; exit 2 ;;
    esac
done

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

# ── the harness ─────────────────────────────────────────────────────────────
#
# The corpus adds no dependency to anything: the gate builds the harness and
# runs its own command. The traces the harness writes are the corpus's
# documents.

build_harness() { # prints the binary path, or fails with its log
    if [ ! -d "$HARNESS_DIR" ]; then
        die "the harness is missing at $HARNESS_DIR (set MLP_HARNESS_DIR)"
    fi
    if ! ( cd "$HARNESS_DIR" && cargo build --release ) >"$LOGDIR/cargo.log" 2>&1; then
        die "the harness could not be built; see $LOGDIR/cargo.log"
    fi
    printf '%s' "$HARNESS_DIR/target/release/mlp-harness"
}

# ── the acceptance matrix ───────────────────────────────────────────────────
#
# The matrix is the corpus's map of the specification's acceptance stories
# (MLP spec annex S) onto the scenarios that cover them. A story with no
# scenario is a promise nobody checks, and a scenario with no story is a
# document nobody can place.

check_matrix() {
    local log="$LOGDIR/matrix.log" rc=0
    python3 "$CORPUS_DIR/scripts/check-matrix.py" \
        "$CORPUS_DIR" "$STORY_COUNT_EXPECTED" "$SCENARIO_COUNT_EXPECTED" >"$log" 2>&1 || rc=$?
    cat "$log"
    if [ "$rc" != "0" ]; then
        fail "the acceptance matrix covers every story" "see the output above"
        return
    fi
    ok "the acceptance matrix covers every story"
}

# ── the traces ──────────────────────────────────────────────────────────────

check_traces() {
    local scratch="$LOGDIR/decisions"
    local binary
    binary="$(build_harness)"
    rm -rf "$scratch"
    mkdir -p "$scratch"

    # The harness refuses a scenario that does not parse, and it fails when a
    # run breaks an invariant: a corpus may only commit clean runs.
    if ! "$binary" corpus \
            --scenarios "$CORPUS_DIR/scenarios" \
            --out "$scratch" >"$LOGDIR/runner.log" 2>&1; then
        fail "corpus: traces reproducible" "the harness reported a failure; its log follows"
        tail -20 "$LOGDIR/runner.log" >&2
        return
    fi
    local count
    count="$(find "$scratch" -name '*.json' | wc -l | tr -d ' ')"
    if [ "$count" != "$SCENARIO_COUNT_EXPECTED" ]; then
        fail "corpus: scenario count" \
            "expected $SCENARIO_COUNT_EXPECTED, the harness wrote $count"
        return
    fi

    if [ "$UPDATE" = "1" ]; then
        rm -rf "$CORPUS_DIR/decisions"
        cp -r "$scratch" "$CORPUS_DIR/decisions"
        ok "corpus: traces updated ($count)"
        return
    fi

    if diff -r "$scratch" "$CORPUS_DIR/decisions" >"$LOGDIR/diff.log" 2>&1; then
        ok "corpus: $count traces byte-identical"
    else
        fail "corpus: traces reproducible" \
            "the regenerated traces differ from the committed ones (run with --update)"
        sed -n '1,20p' "$LOGDIR/diff.log" >&2
    fi
}

# ── the tier table ──────────────────────────────────────────────────────────
#
# The tiers are the protocol's public contract, and the harness owns their
# numbers. The corpus publishes them as data, so a consumer reads the tier's
# envelope instead of copying it. The table is generated by the harness and
# compared byte for byte, exactly as the traces are: a table that was edited
# by hand no longer says what the harness says, and a table the harness
# changed without the corpus noticing is the same failure the other way.

check_tiers() {
    local scratch="$LOGDIR/tiers.json"
    local binary
    binary="$(build_harness)"

    if ! "$binary" tiers --out "$scratch" >"$LOGDIR/tiers.log" 2>&1; then
        fail "corpus: the tier table is the harness's own" "the harness reported a failure; its log follows"
        tail -20 "$LOGDIR/tiers.log" >&2
        return
    fi

    if [ "$UPDATE" = "1" ]; then
        cp "$scratch" "$CORPUS_DIR/tiers.json"
        ok "corpus: the tier table updated"
        return
    fi

    if [ ! -f "$CORPUS_DIR/tiers.json" ]; then
        fail "corpus: the tier table is the harness's own" \
            "$CORPUS_DIR/tiers.json is absent (run with --update)"
        return
    fi

    if cmp -s "$scratch" "$CORPUS_DIR/tiers.json"; then
        ok "corpus: the tier table is byte-identical"
    else
        fail "corpus: the tier table is the harness's own" \
            "the published table differs from the committed one (run with --update)"
        diff -u "$CORPUS_DIR/tiers.json" "$scratch" | sed -n '1,20p' >&2
    fi
}

# ── the checker's own check ─────────────────────────────────────────────────
#
# A gate that cannot fail is not a gate. Each case mutates a copy of the
# corpus and runs the gate against it, and each mutation must be noticed.

self_test() {
    local scratch
    scratch="$(mktemp -d "${TMPDIR:-/tmp}/mlp-selftest-XXXXXX")"
    trap 'rm -rf "$scratch"' RETURN
    cp -r "$CORPUS_DIR/." "$scratch/"

    local checks=0 failures=0

    # 1. A trace that no longer matches its scenario must be noticed.
    python3 - "$scratch/decisions/s01-t2-typing-echo.json" <<'PY'
import json
import sys

path = sys.argv[1]
document = json.load(open(path))
document["digest"] = "0" * 16
json.dump(document, open(path, "w"), indent=2)
PY
    if MLP_CORPUS_DIR="$scratch" "$REPO_ROOT/scripts/conformance-gate.sh" >/dev/null 2>&1; then
        echo "self-test: a tampered trace was not noticed" >&2
        failures=$((failures + 1))
    else
        checks=$((checks + 1))
    fi

    # 2. A scenario the matrix does not name must be noticed.
    python3 - "$scratch/matrix.json" <<'PY'
import json
import sys

path = sys.argv[1]
document = json.load(open(path))
document["stories"][0]["scenarios"] = []
json.dump(document, open(path, "w"), indent=2)
PY
    if MLP_CORPUS_DIR="$scratch" "$REPO_ROOT/scripts/conformance-gate.sh" >/dev/null 2>&1; then
        echo "self-test: a scenario the matrix does not name was not noticed" >&2
        failures=$((failures + 1))
    else
        checks=$((checks + 1))
    fi

    # 3. A scenario that does not parse must be noticed.
    printf 'not a scenario' >"$scratch/scenarios/s01-t2-typing-echo.json"
    if MLP_CORPUS_DIR="$scratch" "$REPO_ROOT/scripts/conformance-gate.sh" >/dev/null 2>&1; then
        echo "self-test: a scenario that does not parse was not noticed" >&2
        failures=$((failures + 1))
    else
        checks=$((checks + 1))
    fi

    # 4. A tier table that no longer says what the harness says must be
    #    noticed: the table is the protocol's envelope, and a hand-edited copy
    #    of it is a second contract that drifts.
    python3 - "$scratch/tiers.json" <<'PY'
import json
import sys

path = sys.argv[1]
document = json.load(open(path))
document["tiers"][0]["profile"]["down"]["rate_bps"] = 1
json.dump(document, open(path, "w"), indent=2)
PY
    if MLP_CORPUS_DIR="$scratch" "$REPO_ROOT/scripts/conformance-gate.sh" >/dev/null 2>&1; then
        echo "self-test: a tampered tier table was not noticed" >&2
        failures=$((failures + 1))
    else
        checks=$((checks + 1))
    fi

    if [ "$failures" -eq 0 ]; then
        echo "self-test: $checks mutation(s) rejected"
        return 0
    fi
    echo "self-test: $failures mutation(s) were not rejected" >&2
    return 1
}

# ── main ────────────────────────────────────────────────────────────────────

echo "mlp-conformance-gate: checking the corpus at $CORPUS_DIR"

if [ "$SELFTEST" = "1" ]; then
    build_harness >/dev/null
    if self_test; then
        exit 0
    fi
    exit 1
fi

check_matrix
check_tiers
check_traces

if [ "$FAILURES" -eq 0 ]; then
    echo "mlp-conformance-gate: all checks passed ($PASSES)"
    exit 0
fi
echo "mlp-conformance-gate: $FAILURES check(s) failed" >&2
exit 1
