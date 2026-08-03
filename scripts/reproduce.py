"""One command that reproduces everything this repository can currently prove.

Part 10 asks for a path an independent person can run to reproduce the
principal results. The honest version of that command, today, reports three
kinds of outcome and never confuses them:

- `pass`        — the check ran and met its criterion
- `unavailable` — the check cannot run here, with the reason stated
- `fail`        — the check ran and did not meet its criterion

The distinction is the whole point. Most of this program's headline numbers are
`unavailable`: they need a real model, a Quest 3, or human participants. A
reproduction script that printed a green tick for the parts that happen to be
runnable would be reporting the completeness of the harness as though it were
the completeness of the research.

Exit code is non-zero only for `fail`. An `unavailable` check is not a broken
build; it is an honest gap, and it is listed as one.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"


@dataclass
class Check:
    check_id: str
    description: str
    state: str
    detail: str
    seconds: float = 0.0


def _run(check_id: str, description: str, body: Callable[[], tuple[str, str]]) -> Check:
    started = time.perf_counter()
    try:
        state, detail = body()
    except Exception as exc:  # noqa: BLE001 - a check must never abort the run
        state, detail = FAIL, f"{type(exc).__name__}: {exc}"
    return Check(check_id, description, state, detail, time.perf_counter() - started)


# --- checks -------------------------------------------------------------------


def check_git_provenance() -> tuple[str, str]:
    """Whether a result produced here could name the code that produced it."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return UNAVAILABLE, f"git is not usable here: {exc}"
    if revision.returncode != 0:
        return UNAVAILABLE, "not a git repository"

    head = revision.stdout.strip()[:12]
    lines = [line for line in status.stdout.splitlines() if line.strip()]
    untracked = [line[3:] for line in lines if line.startswith("??")]
    research_untracked = [
        path
        for path in untracked
        if path.startswith(
            (
                "src/open_llm_vtuber/evaluation",
                "src/open_llm_vtuber/provenance",
                "src/open_llm_vtuber/authorization",
                "research/",
            )
        )
    ]
    if research_untracked:
        # Part 11 exists for exactly this. A manifest that records a revision
        # not containing its own inputs has not recorded anything.
        return FAIL, (
            f"HEAD {head} does not contain {len(research_untracked)} research "
            f"path(s); every manifest naming this revision is misleading "
            f"(e.g. {research_untracked[0]})"
        )
    if lines:
        return PASS, f"HEAD {head}, {len(lines)} uncommitted change(s)"
    return PASS, f"HEAD {head}, clean tree"


def check_research_contract() -> tuple[str, str]:
    from src.open_llm_vtuber.evaluation.research_contract import (
        load_research_contract,
    )

    contract = load_research_contract(ROOT / "research" / "contract.json")
    revision = contract.get("contract_revision", "unknown")
    metrics = len(contract.get("metrics", []))
    conditions = len(contract.get("conditions", []))
    return PASS, (
        f"contract revision {revision} validates "
        f"({conditions} conditions, {metrics} metrics)"
    )


def check_scenario_suite() -> tuple[str, str]:
    from src.open_llm_vtuber.evaluation.scenarios import load_suite

    path = ROOT / "research" / "scenarios" / "embodied-agent-bench.json"
    if not path.exists():
        return UNAVAILABLE, "scenario suite has not been authored"
    suite = load_suite(path)
    coverage = suite.coverage()
    if coverage.missing_areas:
        return FAIL, f"capability areas with no scenarios: {coverage.missing_areas}"
    if coverage.total < 100:
        return FAIL, f"{coverage.total} scenarios, below the 100 the gate requires"
    return PASS, (
        f"{coverage.total} scenarios, all areas covered, "
        f"held-out digest verified, {coverage.adversarial} adversarial"
    )


def check_held_out_is_frozen() -> tuple[str, str]:
    from src.open_llm_vtuber.evaluation.scenarios import ScenarioError, load_suite

    path = ROOT / "research" / "scenarios" / "embodied-agent-bench.json"
    if not path.exists():
        return UNAVAILABLE, "scenario suite has not been authored"
    suite = load_suite(path)
    try:
        suite.select(split="held_out")
    except ScenarioError:
        return (
            PASS,
            "the held-out split refuses to be selected without an explicit flag",
        )
    return FAIL, "the held-out split can be taken without acknowledgement"


def check_benchmark_results() -> tuple[str, str]:
    """The Part 8 outcome gates. Unmeasured, and said so."""
    return UNAVAILABLE, (
        "no system has been run against the suite; full-task success and "
        "forbidden-effect count need a real model in the loop (Part 12)"
    )


def check_latency_budget() -> tuple[str, str]:
    """Read a latency report the *server* exported, not this process's own.

    The first version of this check called `recorder()` directly, which in a
    freshly started script is always an empty in-process recorder — so it
    reported `unavailable` no matter how the system actually performs, and
    could never have passed. A check that cannot pass is decoration.

    Latency is measured inside the running server, so reproducing it means
    reading what that server exported through `/api/operator/health`.
    """
    export = ROOT / "research" / "baselines" / "latency-report.json"
    if not export.exists():
        return UNAVAILABLE, (
            "no exported latency report; run the server, exercise it, and save "
            "GET /api/operator/health to research/baselines/latency-report.json. "
            "Note that capture and endpoint have no producer yet, so the "
            "end-to-end figure will still be unavailable"
        )
    payload = json.loads(export.read_text(encoding="utf-8"))
    figure = payload.get("latency", {}).get("speech_to_first_audio", {})
    median = figure.get("median_ms")
    verdict = figure.get("median_verdict")
    if median is None or verdict == UNAVAILABLE:
        return UNAVAILABLE, (
            "the exported report has no end-to-end figure; stages without a "
            "producer leave speech-to-first-audio uncomputable"
        )
    return (PASS if verdict == PASS else FAIL), f"median {median} ms ({verdict})"


def check_study_analysis() -> tuple[str, str]:
    """The analysis exists and runs; the data does not."""
    from src.open_llm_vtuber.evaluation.study_analysis import (
        STUDY_CONDITIONS,
        williams_sequences,
    )

    sequences = williams_sequences(STUDY_CONDITIONS)
    pairs: dict[tuple[str, str], int] = {}
    for sequence in sequences:
        for first, second in zip(sequence, sequence[1:]):
            pairs[(first, second)] = pairs.get((first, second), 0) + 1
    if len(set(pairs.values())) != 1:
        return FAIL, "the counterbalancing design is not carryover-balanced"
    return UNAVAILABLE, (
        f"analysis is written and its {len(sequences)}-sequence design is "
        "carryover-balanced, but no participant has been recruited and ethics "
        "approval is outstanding"
    )


# Modules the plan describes as parts of the running system. Offline tooling —
# the evaluation harness, the benchmark, the study analysis — is deliberately
# absent: it is *supposed* to sit outside the server.
SYSTEM_COMPONENTS = (
    "provenance.ledger",
    "provenance.policy",
    "authorization.kernel",
    "authorization.transaction",
    "autonomy.plan_state",
    "latency",
    "world_model",
    "semantic_world",
    "semantic_projection",
    "semantic_navigation",
)


def _reachable_from_server() -> set[str]:
    """Modules the server can actually reach, following relative imports."""
    import ast

    source_root = ROOT / "src" / "open_llm_vtuber"
    modules: dict[str, Path] = {}
    for path in source_root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        parts = list(path.relative_to(source_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = path

    def imports_of(path: Path) -> set[str]:
        # A module's package is its path minus its own name, including for
        # __init__.py, whose package is the directory it sits in.
        package = list(path.relative_to(source_root).with_suffix("").parts)[:-1]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            return set()
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - (node.level - 1)]
                    target = base + (node.module.split(".") if node.module else [])
                    found.add(".".join(target))
                    for alias in node.names:
                        found.add(".".join(target + [alias.name]))
                elif node.module and "open_llm_vtuber" in node.module:
                    found.add(node.module.split("open_llm_vtuber.")[-1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "open_llm_vtuber" in alias.name:
                        found.add(alias.name.split("open_llm_vtuber.")[-1])
        return found

    seen: set[str] = set()
    queue = ["server"]
    while queue:
        name = queue.pop()
        if name in seen or name not in modules:
            continue
        seen.add(name)
        queue.extend(dep for dep in imports_of(modules[name]) if dep in modules)
    return seen


def check_integration() -> tuple[str, str]:
    """Whether components the plan credits are reachable from the server.

    Part 6 shipped 1,546 lines of semantic world model, projection, and
    navigation, proved capability parity across renderers, and wired none of it
    into the running system: the live proximity gate still reads
    `world_model`. The guarantees were real and were about a model the server
    never consults.

    A static import trace cannot prove a module is *used* well, but it does
    prove the difference between "integrated" and "sitting beside the system",
    and that is the failure this check exists to make unmissable.
    """
    reachable = _reachable_from_server()
    orphaned = [name for name in SYSTEM_COMPONENTS if name not in reachable]
    if orphaned:
        return FAIL, (
            f"{len(orphaned)} of {len(SYSTEM_COMPONENTS)} claimed system "
            f"components are not reachable from server.py: {orphaned}"
        )
    return PASS, f"all {len(SYSTEM_COMPONENTS)} claimed components are wired in"


def check_test_suite(skip: bool) -> tuple[str, str]:
    if skip:
        return UNAVAILABLE, "skipped by --fast"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    summary = tail[-1] if tail else "no output"
    return (PASS if result.returncode == 0 else FAIL), summary


# Credential shapes, as patterns rather than substrings. The first version of
# this check searched for bare `"sk-"` and `"client_secret"`, which fired on the
# word "risk-" in the research contract and on documentation naming an OAuth
# field. A secret scanner that cries wolf on prose gets switched off, so each
# pattern requires an actual secret-shaped *value*, not a mention of one.
_SECRET_PATTERNS = (
    (r"\bsk-[A-Za-z0-9]{20,}", "OpenAI-style key"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"\bAKIA[A-Z0-9]{16}\b", "AWS access key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    # A credential field assigned a long opaque literal, rather than merely named.
    (
        r"(api[_-]?key|client_secret|refresh_token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}",
        "assigned credential value",
    ),
)


def check_published_artifacts_carry_no_secrets() -> tuple[str, str]:
    """Scan what a release would publish for anything credential-shaped.

    This replaced a check that returned `pass` unconditionally with the text
    "no external account was required". It asserted a property instead of
    testing one, so it could not have caught the failure it named. Part 10's
    work assignment 6 asks for a secret review; this is that review, run every
    time rather than once.
    """
    published = [
        ROOT / "research",
        ROOT / "doc",
    ]
    findings: list[str] = []
    scanned = 0
    for root in published:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {
                ".json",
                ".md",
                ".yaml",
                ".yml",
            }:
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern, label in _SECRET_PATTERNS:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    findings.append(f"{path.relative_to(ROOT)}: {label}")
                    break
    if findings:
        return FAIL, f"credential-shaped strings in publishable files: {findings[:3]}"
    return PASS, f"{scanned} publishable files scanned, no credential markers"


# --- report -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="skip the test suite")
    parser.add_argument("--json", type=Path, help="write the report to a file")
    args = parser.parse_args()

    checks = [
        _run(
            "git-provenance",
            "Results can name the code that produced them",
            check_git_provenance,
        ),
        _run(
            "research-contract",
            "The frozen research contract validates",
            check_research_contract,
        ),
        _run(
            "scenario-suite",
            "EmbodiedAgentBench loads and meets its size gate",
            check_scenario_suite,
        ),
        _run(
            "held-out-frozen",
            "The evaluation split cannot be taken by accident",
            check_held_out_is_frozen,
        ),
        _run(
            "benchmark-results",
            "Full-task success and forbidden effects",
            check_benchmark_results,
        ),
        _run(
            "latency-budget",
            "Speech-to-first-audio against its budget",
            check_latency_budget,
        ),
        _run(
            "study-analysis",
            "Human-study design and pre-registered analysis",
            check_study_analysis,
        ),
        _run(
            "test-suite",
            "The repository's own test suite",
            lambda: check_test_suite(args.fast),
        ),
        _run(
            "integration",
            "Modules the plan calls system components are wired in",
            check_integration,
        ),
        _run(
            "secrets",
            "Publishable artifacts carry no credentials",
            check_published_artifacts_carry_no_secrets,
        ),
    ]

    width = max(len(check.check_id) for check in checks)
    print("\nEmbodied-agent reproduction report")
    print("=" * 72)
    for check in checks:
        marker = {PASS: "PASS", FAIL: "FAIL", UNAVAILABLE: "n/a "}[check.state]
        print(f"[{marker}] {check.check_id.ljust(width)}  {check.description}")
        print(f"         {' ' * width}  {check.detail}")

    passed = sum(1 for c in checks if c.state == PASS)
    failed = sum(1 for c in checks if c.state == FAIL)
    unavailable = sum(1 for c in checks if c.state == UNAVAILABLE)
    print("=" * 72)
    print(f"{passed} pass, {failed} fail, {unavailable} unavailable")
    if unavailable:
        print(
            "\nUnavailable checks are honest gaps, not build failures. They are\n"
            "the results this program has not yet earned; see Parts 11-14."
        )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "platform": {
                        "system": platform.system(),
                        "python": platform.python_version(),
                    },
                    "summary": {
                        "pass": passed,
                        "fail": failed,
                        "unavailable": unavailable,
                    },
                    "checks": [vars(check) for check in checks],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    # Only a genuine failure is a non-zero exit. An unmeasured result is not a
    # broken build, and treating it as one would train people to ignore it.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
