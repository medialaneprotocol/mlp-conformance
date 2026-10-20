# mlp-conformance

The conformance corpus of the **Media Lane Protocol (MLP)**, and the gate that
keeps it honest.

The corpus is the protocol's shared, executable description of what it
promises. It is read by **both** ends of the protocol:

| Consumer | Resolves | Contract |
|---|---|---|
| The reference implementation | `tools/corpus-gen.rs`, compiled by the gate | translates every committed scenario into the simulator's builder API, runs it, and writes `decisions/` — the gate then regenerates and byte-compares |
| A browser implementation | `profiles/*.json` | reconstructs the identical link behaviour from the same committed profiles |

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
profiles/    one JSON document per link profile
scenarios/   one JSON document per acceptance story
decisions/   one JSON document per scenario: the golden decision trace
tools/       the corpus generator and the static validator
scripts/     the gate
```

## Running the gate

```bash
scripts/conformance-gate.sh             # run it
scripts/conformance-gate.sh --update    # regenerate the traces in place
scripts/conformance-gate.sh --self-test # check the checker
```

The gate needs a Rust toolchain and Python 3, and nothing else, so it runs
the same in CI, in a container, and on a developer machine. The reference
implementation is expected next to this repository as `../mlp-core`;
`MLP_CORE_DIR` overrides that.

## What the gate checks

1. **The corpus's own shape.** Every profile is a canonical tier point, every
   scenario's steps parse, the acceptance matrix's coverage is complete, and
   every trace is clean — no scenario may commit a run that broke an
   invariant.
2. **The traces are reproducible.** The generator replays the committed
   scenarios through the reference implementation and the result must be
   byte-identical to the committed traces.
3. **The corpus was actually read.** The generator's "no corpus" marker is
   counted and required to be zero, so a deleted or unreadable corpus cannot
   pass as a green run that compared nothing.
4. **The gate itself.** `--self-test` mutates a copy of the corpus three
   ways and requires each mutation to be noticed. A gate that cannot fail is
   not a gate.

## The profiles

The file name is the profile id (`t2.json` ⇒ id `t2`). A **tier** profile
(`t0`…`t4`) is a canonical point of the target envelope; its numbers are
fixed by the specification, and the gate rejects a tier profile that drifts
from them. A **flap** profile carries an outage schedule and is checked
against its tier's band.

## The scenarios

Each scenario is one acceptance story: the tier it targets, the steps that
drive it (`Link`, `Advance`, `CaptureChange`, `ViewerInput`, `Disconnect`,
`Reconnect`, `SenderReconnect`, `Path`, `Assert`), and the invariants it
asserts. The gate holds the matrix's case coverage frozen, so the corpus
cannot shrink quietly.

## Adding a scenario

1. Add `scenarios/<id>.json`.
2. Run `scripts/conformance-gate.sh --update` and commit the regenerated
   trace.
3. Add the case to the matrix in `tools/corpus_static.py` if it covers a
   case the matrix does not yet name.

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
| [`mlp-ts`](https://github.com/medialaneprotocol/mlp-ts) | the browser reference implementation |
| [`mlp-proto`](https://github.com/medialaneprotocol/mlp-proto) | the class bodies' wire schemas |
| [`mlp-conformance`](https://github.com/medialaneprotocol/mlp-conformance) | the conformance corpus and its gate |
