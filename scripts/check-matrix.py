#!/usr/bin/env python3
"""Check the corpus's acceptance matrix.

The matrix is the corpus's map of the specification's acceptance stories
(MLP spec annex S) onto the scenarios that cover them. A story with no
scenario is a promise nobody checks, and a scenario with no story is a
document nobody can place.

The check is small and it is the corpus's own: the harness owns the shape of
a scenario and of a trace, and this file owns the coverage.

Usage: python3 scripts/check-matrix.py <corpus-dir> <stories> <scenarios>
"""

import json
import os
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print("check-matrix: a corpus directory, a story count and a scenario count", file=sys.stderr)
        return 2
    corpus = sys.argv[1]
    stories_expected = int(sys.argv[2])
    scenarios_expected = int(sys.argv[3])

    problems: list[str] = []

    def fail(detail: str) -> None:
        problems.append(detail)
        print("FAIL the acceptance matrix — %s" % detail)

    try:
        with open(os.path.join(corpus, "matrix.json"), encoding="utf-8") as handle:
            matrix = json.load(handle)
    except Exception as error:  # noqa: BLE001 - the message is the finding
        fail("the matrix could not be read: %s" % error)
        return 1

    scenario_dir = os.path.join(corpus, "scenarios")
    committed = sorted(
        name[:-5]
        for name in os.listdir(scenario_dir)
        if name.endswith(".json")
    ) if os.path.isdir(scenario_dir) else []

    if matrix.get("schema") != 1:
        fail("the matrix's schema is %r, expected 1" % matrix.get("schema"))
    stories = matrix.get("stories")
    if not isinstance(stories, list) or not stories:
        fail("the matrix holds no story")
        return 1

    identifiers = [story.get("story") for story in stories]
    expected = ["S-%d" % number for number in range(1, stories_expected + 1)]
    if identifiers != expected:
        fail("the stories are %s, expected %s" % (identifiers, expected))
    if len(stories) != stories_expected:
        fail("the matrix holds %d stories, expected %d" % (len(stories), stories_expected))

    named: list[str] = []
    cases: list[str] = []
    for story in stories:
        identifier = story.get("story")
        if not isinstance(story.get("intent"), str) or len(story.get("intent", "")) < 20:
            fail("%s has no intent" % identifier)
        scenarios = story.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            fail("%s names no scenario" % identifier)
            continue
        for scenario in scenarios:
            if scenario not in committed:
                fail("%s names %s, which the corpus does not commit" % (identifier, scenario))
            named.append(scenario)
        story_cases = story.get("cases")
        if not isinstance(story_cases, list) or not story_cases:
            fail("%s names no case" % identifier)
            continue
        cases.extend(story_cases)

    missing = sorted(set(committed) - set(named))
    if missing:
        fail("the matrix does not name %s" % ", ".join(missing))
    duplicated = sorted({name for name in named if named.count(name) > 1})
    if duplicated:
        fail("the matrix names %s twice" % ", ".join(duplicated))
    if len(set(cases)) != len(cases):
        fail("the matrix names a case twice")
    if len(committed) != scenarios_expected:
        fail("the corpus holds %d scenarios, expected %d" % (len(committed), scenarios_expected))

    if problems:
        return 1
    print(
        "ok   the acceptance matrix — %d stories, %d scenarios, %d cases"
        % (len(stories), len(committed), len(cases))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
