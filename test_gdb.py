#!/usr/bin/env python3
"""Test GDB tracing and memory tracking on all example programs."""

import subprocess
import sys

sys.path.insert(0, "/app/srcs")

from gdb_tracer import trace_pointer, check_gdb_available
from memory_tracker import find_root_cause_from_trace

print("GDB available:", check_gdb_available())
print()

# Test cases: (name, executable, alloc_file, alloc_line, alloc_var,
#              backtrace_functions, caller_file, caller_line,
#              expected_type, expected_function)
TESTS = [
    (
        "test_nofree (never freed)",
        "/app/examples/test_nofree/leaky",
        "leaky.c", 9, "temp",
        ["type1_example", "main"],
        "leaky.c", 18,
        2, "type1_example",
    ),
    (
        "test_lost (pointer lost)",
        "/app/examples/test_lost/leaky",
        "leaky.c", 20, "p",
        ["ft_a", "main"],
        "leaky.c", 35,
        2, "ft_a",
    ),
    (
        "test_struct (container freed before content)",
        "/app/examples/test_struct/leaky",
        "leaky.c", 53, "data",
        ["level_5_alloc", "level_4", "level_3", "level_2", "level_1", "main"],
        "leaky.c", 60,
        3, "level_1",
    ),
    (
        "test_linked_list (loop + indirect free)",
        "/app/examples/test_linked_list/leaky",
        "leaky.c", 20, "n",
        ["create_node", "build_list", "main"],
        "leaky.c", 38,
        3, "partial_cleanup",
    ),
    (
        "test_cond (conditional leak)",
        "/app/examples/test_cond/leaky",
        "leaky.c", 9, "buf",
        ["create_buffer", "process", "main"],
        "leaky.c", 18,
        2, "process",
    ),
    (
        "test_reuse (pointer reuse)",
        "/app/examples/test_reuse/leaky",
        "leaky.c", 9, "ptr",
        ["main"],
        "", 0,
        2, "main",
    ),
    (
        "test_scope (scope leak)",
        "/app/examples/test_scope/leaky",
        "leaky.c", 9, "tmp",
        ["init_data", "main"],
        "leaky.c", 15,
        2, "init_data",
    ),
    (
        "test_chain (conditional path)",
        "/app/examples/test_chain/leaky",
        "leaky.c", 9, "buf",
        ["allocate", "run", "main"],
        "leaky.c", 30,
        2, "run",
    ),
    (
        "test_swap (struct content forgotten)",
        "/app/examples/test_swap/leaky",
        "leaky.c", 18, "p->value",
        ["create_pair", "main"],
        "leaky.c", 27,
        3, "main",
    ),
    (
        "test_array (off-by-one cleanup)",
        "/app/examples/test_array/leaky",
        "leaky.c", 14, "arr[i]",
        ["create_array", "main"],
        "leaky.c", 39,
        3, "cleanup",
    ),
    (
        "test_pass (pointer passed as argument)",
        "/app/examples/test_pass/leaky",
        "leaky.c", 9, "buf",
        ["create", "main"],
        "leaky.c", 23,
        1, "main",
    ),
]

passed = 0
failed = 0
errors = []

for name, exe, af, al, av, bt, cf, cl, exp_type, exp_func in TESTS:
    print(f"=== {name} ===")

    # Compile
    src = exe + ".c"
    subprocess.run(["gcc", "-g", "-O0", "-o", exe, src], check=True)

    # Trace
    result = trace_pointer(exe, af, al, av, bt, cf, cl)

    if not result["success"]:
        print(f"  FAIL: trace failed — {result['error']}")
        failed += 1
        errors.append(f"{name}: trace failed")
        print()
        continue

    # Run tracker
    root_cause = find_root_cause_from_trace(
        result["trace"], result["free_events"]
    )

    if not root_cause:
        print(f"  FAIL: no root cause found")
        failed += 1
        errors.append(f"{name}: no root cause found")
        print()
        continue

    # Check results
    got_type = root_cause["leak_type"]
    got_func = root_cause["function"]
    ok = True

    if got_type != exp_type:
        print(f"  FAIL: expected type {exp_type}, got {got_type}")
        ok = False

    if got_func != exp_func:
        print(f"  FAIL: expected function '{exp_func}', got '{got_func}'")
        ok = False

    if ok:
        print(f"  PASS (type={got_type}, function={got_func})")
        passed += 1
    else:
        failed += 1
        errors.append(f"{name}: type={got_type} func={got_func}")
        # Print debug info for failed tests
        print(f"  trace steps: {len(result['trace'])}")
        for s in root_cause["steps"]:
            print(f"    {s}")

    print()

# Summary
print("=" * 50)
print(f"Results: {passed} passed, {failed} failed, {len(TESTS)} total")

if errors:
    print("\nFailed tests:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("\nAll tests passed!")
    sys.exit(0)
