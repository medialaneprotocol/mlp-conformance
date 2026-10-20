# mlp-conformance

The conformance corpus of the **Media Lane Protocol (MLP)**, and the gate that
keeps it honest.

The corpus is the protocol's shared, executable description of what it
promises. It is read by **both** ends of the protocol:

| Consumer | Resolves | Contract |
|---|---|---|
| The reference implementation | [`mlp-harness`](../mlp-harness), run by the gate | replays every committed scenario over the protocol's real machines and writes `decisions/` — the gate then regenerates and byte-compares |
| A browser implementation | `scenarios/*.json` | reconstructs the identical link behaviour from the same committed scenarios |

There is exactly **one** corpus directory. The gate asserts that both
consumers resolve to it, so a consumer pointed at a second copy fails there
rather than reading a file nothing regenerates.

## Why a mismatch is a failure

A decision trace is not documentation: it is the record of what the protocol
machines decided, step by step, under a committed link profile. A protocol
change that alters any decision changes the trace, and the gate regenerates it
and compares it **byte for byte**. A difference means the committed trace no
longer describes what the code does, so either the code change was not
reviewed as a protocol change, or the trace was edited by hand. Both are
failures by construction. When the change is intended, it is committed
together with the regenerated traces (`scripts/conformance-gate.sh --update`)
and the diff is part of the change's review.

## Layout

```
matrix.json    the acceptance matrix: the specification's stories, the
               scenarios that cover them, and what each one promises
scenarios/     one JSON document per acceptance story
decisions/     one JSON document per scenario: the golden decision trace
scripts/       the gate, and the matrix's own check
```

## Running the gate

```bash
scripts/conformance-gate.sh             # run it
scripts/conformance-gate.sh --update    # regenerate the traces in place
scripts/conformance-gate.sh --self-test # check the checker
```

The gate needs a Rust toolchain and Python 3, and nothing else, so it runs
the same in CI, in a container, and on a developer machine. The harness is
expected next to this repository as `../mlp-harness`; `MLP_HARNESS_DIR`
overrides that.

## What the gate checks

1. **The acceptance matrix is complete.** Every story the specification
   states has a scenario, every scenario the corpus commits is named by the
   matrix, and no case is named twice.
2. **The traces are reproducible.** The harness replays the committed
   scenarios through the protocol's real machines and the result must be
   byte-identical to the committed traces. The harness refuses a scenario
   that does not parse, and it fails when a run breaks an invariant: a
   corpus may only commit clean runs.
3. **The gate itself.** `--self-test` mutates a copy of the corpus three
   ways — a tampered trace, a scenario the matrix does not name, and a
   scenario that does not parse — and requires each mutation to be noticed.
   A gate that cannot fail is not a gate.

## The scenarios

Each scenario is one acceptance story, in the harness's scenario format: the
tier it targets, the link it starts on, the steps that drive it
(`SetLink`, `Advance`, `CaptureChange`, `ViewerInput`, `ViewerDisconnect`,
`ViewerReconnect`, `HostReconnect`, `PathSwitch`, `ScheduleOutage`,
`Assert`), and the invariants it asserts. The link profiles are carried in
the scenarios themselves, so a scenario is read on its own.

A scenario starts on its tier's canonical link — the tier's own point in the
target envelope — and a scenario that needs a different link says so with a
`SetLink` step.

## The matrix

`matrix.json` is the corpus's map of the specification's acceptance stories
(MLP spec annex S) onto the scenarios that cover them. Each story names the
scenarios that cover it, the cases it asserts, and the promise it makes in
one sentence.

## Adding a scenario

1. Add `scenarios/<id>.json`.
2. Add the scenario to its story in `matrix.json`.
3. Run `scripts/conformance-gate.sh --update` and commit the regenerated
   trace.

The trace is generated, never written by hand.

## Status

In development. One moving version, `v0.1.0`, naming the current commit:
there is exactly one tag, and a change moves it.

## License

Apache-2.0. See [LICENSE](LICENSE).

The reference implementations under [`mlp-core`](../mlp-core) and
[`mlp-ts`](../mlp-ts) are under PolyForm Noncommercial 1.0.0; this corpus is
not, so any implementation may validate itself against it.

## The sibling repositories

| repository | what it holds |
|---|---|
| [`mlp`](https://github.com/medialaneprotocol/mlp) | the normative specification |
| [`mlp-core`](https://github.com/medialaneprotocol/mlp-core) | the Rust reference implementation, and the protocol's command-line tool |
| [`mlp-harness`](https://github.com/medialaneprotocol/mlp-harness) | the verification layer: the link model, the driver, the invariants, the predictions |
| [`mlp-ts`](https://github.com/medialaneprotocol/mlp-ts) | the browser reference implementation |
| [`mlp-proto`](https://github.com/medialaneprotocol/mlp-proto) | the class bodies' wire schemas |
| [`mlp-conformance`](https://github.com/medialaneprotocol/mlp-conformance) | the conformance corpus and its gate |
