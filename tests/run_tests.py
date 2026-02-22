#!/usr/bin/env python3
"""
run_tests.py — Goal-Based Test Runner for the Barclays Mortgage Assistant.

Usage:
    python run_tests.py                    # run all scenarios
    python run_tests.py GBT-FTB-01        # run one specific scenario
    python run_tests.py GBT-FTB-01 GBT-FTB-03   # run specific set
    python run_tests.py --list             # list all available scenarios

Output:
    Per-test pass/fail report + overall summary table.
    Exit code 0 = all pass, 1 = one or more failures.
"""
import asyncio
import sys
import time
from pathlib import Path

# Allow running from both project root and tests/ directory
sys.path.insert(0, str(Path(__file__).parent))

from scenarios import SCENARIOS
from harness import TestResult


BANNER = """
╔══════════════════════════════════════════════════════════════╗
║       Barclays Mortgage Assistant — Goal-Based Tests         ║
║       ws://localhost:8000/ws                                 ║
╚══════════════════════════════════════════════════════════════╝
"""


async def run_scenario(test_id: str) -> TestResult:
    fn = SCENARIOS[test_id]
    print(f"  ▶ Running {test_id} ...", end=" ", flush=True)
    t0 = time.time()
    result = await fn()
    elapsed = time.time() - t0
    status = "✅" if result.passed() else "❌"
    print(f"{status}  ({elapsed:.1f}s)")
    return result


async def main(ids: list[str]) -> int:
    print(BANNER)
    print(f"Running {len(ids)} scenario(s):\n")

    results: list[TestResult] = []
    for test_id in ids:
        result = await run_scenario(test_id)
        results.append(result)
        # Small pause between tests to let server settle
        await asyncio.sleep(1.0)

    # ── Detailed report ───────────────────────────────────────────────────────
    print("\n" + "─" * 62)
    print("DETAILED RESULTS")
    print("─" * 62)
    for r in results:
        print(r.summary())

    # ── Summary table ─────────────────────────────────────────────────────────
    passed = [r for r in results if r.passed()]
    failed = [r for r in results if not r.passed()]

    print("\n" + "═" * 62)
    print("SUMMARY")
    print("═" * 62)
    for r in results:
        icon = "✅ PASS" if r.passed() else "❌ FAIL"
        check_summary = f"{sum(c[1] for c in r.checks)}/{len(r.checks)} checks"
        print(f"  {icon}  {r.test_id:<18} {check_summary}")

    print("─" * 62)
    print(f"  TOTAL: {len(passed)} passed, {len(failed)} failed out of {len(results)}")

    return 0 if not failed else 1


def parse_args() -> list[str]:
    args = sys.argv[1:]

    if "--list" in args:
        print("Available scenarios:")
        for k in sorted(SCENARIOS):
            fn = SCENARIOS[k]
            print(f"  {k}")
        sys.exit(0)

    if not args:
        return sorted(SCENARIOS.keys())

    # Validate
    bad = [a for a in args if a not in SCENARIOS]
    if bad:
        print(f"Unknown scenario IDs: {bad}")
        print(f"Available: {sorted(SCENARIOS.keys())}")
        sys.exit(1)

    return args


if __name__ == "__main__":
    ids = parse_args()
    exit_code = asyncio.run(main(ids))
    sys.exit(exit_code)
