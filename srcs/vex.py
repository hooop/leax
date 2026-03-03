#!/usr/bin/env python3

"""
Vex - Valgrind Error eXplorer.

Command-line tool for analyzing memory leaks in C programs.
Integrates Valgrind execution, source code extraction, memory tracking
analysis, and AI-powered explanations.

Usage: ./vex.py <executable> [args...]
"""

import sys
import threading
import time
from typing import Optional

from builder import rebuild_project
from code_extractor import extract_call_stack
from colors import RESET, RED
from display import display_analysis
from gdb_tracer import trace_pointer, check_gdb_available
from memory_tracker import (
    find_root_cause,
    find_root_cause_from_trace,
    convert_extracted_code,
    extract_left_side,
    is_malloc,
)
from menu import interactive_menu
from mistral_analyzer import analyze_with_mistral, MistralAPIError
from type_defs import ParsedValgrindReport, ValgrindError
from valgrind_parser import parse_valgrind_report
from valgrind_runner import (
    run_valgrind,
    ExecutableNotFoundError,
    ValgrindError as ValgrindRunnerError,
)
from welcome import (
    clear_screen,
    display_logo,
    start_spinner,
    stop_spinner,
    start_block_spinner,
    stop_block_spinner,
    display_summary,
)

# Return codes
SUCCESS = 0
ERROR = 1


def print_error(message: str) -> None:
    """
    Display a formatted error message.

    Args:
        message: Error message to display.
    """

    print(f"\nError : {message}\n", file=sys.stderr)


def _run_valgrind_analysis(
    executable: str, program_args: list[str]
) -> ParsedValgrindReport:
    """
    Execute Valgrind and parse the report.

    Args:
        executable: Path to the executable.
        program_args: Program arguments.

    Returns:
        Dictionary containing has_leaks, summary, and leaks.
    """

    # Build complete command
    full_command = executable
    if program_args:
        full_command += " " + " ".join(program_args)

    # Execute Valgrind
    t = start_spinner("Running Valgrind")
    valgrind_output = run_valgrind(full_command)
    stop_spinner(t, "Running Valgrind")

    # Parse report
    t = start_spinner("Parsing report")
    parsed_data = parse_valgrind_report(valgrind_output)
    stop_spinner(t, "Parsing report")

    return parsed_data


def _reanalyze_after_compilation(
    full_command: str, initial_leak_count: int
) -> Optional[tuple[list[ValgrindError], int]]:
    """
    Re-run Valgrind after compilation and display delta.

    Args:
        full_command: Complete command (executable + args).
        initial_leak_count: Number of leaks before fix.

    Returns:
        None if all leaks resolved.
        (parsed_errors, new_leak_count) otherwise.
    """

    clear_screen()
    print("\033[?25l", end="", flush=True)

    # Re-run Valgrind
    t = start_spinner("Running Valgrind")
    valgrind_output = run_valgrind(full_command)
    stop_spinner(t, "Running Valgrind")

    # Re-parse
    t = start_spinner("Parsing report")
    parsed_data = parse_valgrind_report(valgrind_output)
    stop_spinner(t, "Parsing report")

    # Check if leaks remain
    new_leak_count = len(parsed_data.get("leaks", []))

    if new_leak_count == 0:
        print(f"\n{RED}All leaks resolved !{RESET}\n")
        return None

    # Display delta
    resolved_count = initial_leak_count - new_leak_count
    resolved_word = "leak resolved" if resolved_count == 1 else "leaks resolved"
    detected_word = "leak detected" if new_leak_count == 1 else "leaks detected"

    if new_leak_count < initial_leak_count:
        print(f"\n{RED}{resolved_count} {resolved_word}{RESET}")
    else:
        print(f"\n{RED}Still {new_leak_count} {detected_word}{RESET}")

    # Update data
    parsed_errors = parsed_data.get("leaks", [])

    # Display Valgrind summary
    display_summary(parsed_data)

    # Display menu
    choice = interactive_menu(["Continue analysis", "Quit Vex"])
    if choice == 1:  # "Quit Vex" selected
        print()
        return None

    # Re-extract code
    _extract_source_code(parsed_errors)

    return (parsed_errors, new_leak_count)


def _extract_source_code(parsed_errors: list[ValgrindError]) -> None:
    """
    Extract source code for each leak if not already done.

    Args:
        parsed_errors: List of parsed Valgrind errors.
    """

    if not parsed_errors[0].get("extracted_code"):
        clear_screen()
        t = start_spinner("Extracting source code")

        for error in parsed_errors:
            if "backtrace" in error and error["backtrace"]:
                error["extracted_code"] = extract_call_stack(error["backtrace"])
            else:
                error["extracted_code"] = []

        stop_spinner(t, "Extracting source code")


def _find_root_causes(
    parsed_errors: list[ValgrindError],
    executable: str,
) -> None:
    """
    Find root cause for each leak using GDB tracing or static analysis.

    Attempts GDB-based dynamic tracing first for accurate results through
    loops, branches, and indirect frees.  Falls back to static analysis
    if GDB is unavailable or tracing fails.

    Args:
        parsed_errors: List of parsed Valgrind errors with extracted code.
        executable:    Path to the compiled binary.
    """

    gdb_available = check_gdb_available()

    t = start_spinner("Analyzing memory paths")

    for error in parsed_errors:
        if not error.get("extracted_code"):
            continue

        root_cause = None

        # --- Try GDB dynamic tracing first -------------------------------
        if gdb_available:
            root_cause = _try_gdb_trace(error, executable)

        # --- Fallback to static analysis ---------------------------------
        if root_cause is None:
            root_cause = _try_static_analysis(error)

        if root_cause:
            error["root_cause"] = {
                "type": root_cause["leak_type"],
                "line": root_cause["line"],
                "line_number": root_cause.get("line_number"),
                "function": root_cause["function"],
                "file": root_cause["file"],
                "steps": root_cause["steps"],
                "gdb_trace": root_cause.get("gdb_trace"),
            }
        else:
            error["root_cause"] = None

    stop_spinner(t, "Analyzing memory paths")


def _try_gdb_trace(
    error: ValgrindError,
    executable: str,
) -> Optional[dict]:
    """
    Attempt to trace the leaked pointer using GDB.

    Extracts the allocation variable from the source code, then runs
    GDB to capture the real execution trace.

    Args:
        error:      Parsed Valgrind error with extracted code and backtrace.
        executable: Path to the compiled binary.

    Returns:
        A ``RootCauseInfo`` dict on success, or ``None`` on failure.
    """
    try:
        backtrace = error.get("backtrace", [])
        extracted_code = error.get("extracted_code", [])

        if not backtrace or not extracted_code:
            return None

        # The innermost frame (last in reversed backtrace) has the malloc.
        alloc_frame = backtrace[-1]
        alloc_file = alloc_frame["file"]
        alloc_line = alloc_frame["line"]

        # The caller frame (one level above) disambiguates when the
        # same allocation function is called from multiple sites.
        caller_file = ""
        caller_line = 0
        if len(backtrace) >= 2:
            caller_frame = backtrace[-2]
            caller_file = caller_frame["file"]
            caller_line = caller_frame["line"]

        # Extract the variable from the first function's code.
        alloc_var = _extract_alloc_variable(extracted_code, alloc_line)
        if not alloc_var:
            return None

        # Function names from the backtrace (innermost → outermost).
        backtrace_functions = [frame["function"] for frame in reversed(backtrace)]

        trace_result = trace_pointer(
            executable,
            alloc_file,
            alloc_line,
            alloc_var,
            backtrace_functions,
            caller_file,
            caller_line,
        )

        if not trace_result["success"] or not trace_result["trace"]:
            return None

        root_cause = find_root_cause_from_trace(
            trace_result["trace"],
            trace_result["free_events"],
        )
        if root_cause is not None:
            root_cause["gdb_trace"] = trace_result["trace"]
        return root_cause

    except Exception:
        return None


def _try_static_analysis(error: ValgrindError) -> Optional[dict]:
    """
    Fallback: find root cause using static code analysis.

    Args:
        error: Parsed Valgrind error with extracted code.

    Returns:
        A ``RootCauseInfo`` dict on success, or ``None`` on failure.
    """
    try:
        converted = convert_extracted_code(error["extracted_code"])
        return find_root_cause(converted)
    except Exception:
        return None


def _extract_alloc_variable(
    extracted_code: list,
    alloc_line: int = 0,
) -> Optional[str]:
    """
    Determine which variable receives the ``malloc`` return value.

    When *alloc_line* is provided (from Valgrind's backtrace), match that
    specific line number so the correct allocation is selected even when a
    function contains multiple ``malloc`` calls.  Falls back to the first
    ``malloc`` found if the line number cannot be matched.

    Args:
        extracted_code: List of ``ExtractedFunction`` dicts.
        alloc_line:     Source line number reported by Valgrind (0 = any).

    Returns:
        Variable name (e.g. ``"ptr"`` or ``"n->data"``), or ``None``.
    """
    if not extracted_code:
        return None

    fallback = None

    for func in extracted_code:
        for code_line in func.get("code", "").split("\n"):
            # Strip line number prefix  "23: code..." → "code..."
            line_num = 0
            if ":" in code_line:
                prefix = code_line[: code_line.index(":")].strip()
                try:
                    line_num = int(prefix)
                except ValueError:
                    pass
                actual_code = code_line[code_line.index(":") + 1 :]
            else:
                actual_code = code_line

            if is_malloc(actual_code):
                var = extract_left_side(actual_code)
                # Exact line match → return immediately
                if alloc_line and line_num == alloc_line:
                    return var
                # Remember first malloc as fallback
                if fallback is None:
                    fallback = var

    return fallback


def _process_all_leaks(parsed_errors: list[ValgrindError], executable: str) -> str:
    """
    Process all leaks one by one.

    Args:
        parsed_errors: List of leaks to process.
        executable: Path to executable (for recompilation).

    Returns:
        "completed" if all processed, "need_recompile" if [v] chosen, "quit" if [q] chosen.
    """

    # Hide real cursor
    print("\033[?25l", end="", flush=True)

    t = start_block_spinner("Calling Mistral AI")

    for i, error in enumerate(parsed_errors, 1):
        try:
            # Hide cursor before spinner
            print("\033[?25l", end="", flush=True)

            # Start spinner for this leak
            t = start_block_spinner("Calling Mistral AI")
            time.sleep(0.1)

            # Analyze error
            analysis = analyze_with_mistral(error)

            # Stop spinner after analysis
            stop_block_spinner(t, "Calling Mistral AI")

            # Show real cursor
            print("\033[?25h", end="", flush=True)

            show_details = False

            while True:
                display_analysis(
                    error,
                    analysis,
                    error_number=i,
                    total_errors=len(parsed_errors),
                    show_details=show_details,
                )

                # Menu after each leak (d = toggle details)
                options = (
                    ["Verify", "Next leak", "Quit Vex"]
                    if len(parsed_errors) > 1
                    else ["Verify", "Quit Vex"]
                )
                menu_choice = interactive_menu(options, hotkeys={"d"})

                if menu_choice == "d":
                    # Toggle details and redisplay
                    show_details = not show_details
                    clear_screen()
                    continue

                choice = options[menu_choice].lower().split()[0]

                break

            if choice == "verify":
                # Recompile
                result = rebuild_project(executable)
                if not result["success"]:
                    print(result["output"])
                    input("\n[Press Enter to continue...]]")
                    continue

                return "need_recompile"

            elif choice == "next":
                clear_screen()
                # Skip to next
                if i < len(parsed_errors):
                    continue
                else:
                    # Was the last leak
                    return "completed"

            elif choice == "quit":
                print()
                return "quit"

        except MistralAPIError as e:
            if i == 1:
                stop_block_spinner(t, "Calling Mistral AI")
            print_error(f"Error analyzing leak #{i}: {e}")
            continue

    # If we reach here, all leaks processed
    return "completed"


def _parse_command_line() -> tuple[str, list[str], str]:
    """
    Parse command line arguments.

    Returns:
        Tuple: (executable, program_args, full_command).
    """

    executable = sys.argv[1]
    program_args = sys.argv[2:]

    # Build complete command
    full_command = executable
    if program_args:
        full_command += " " + " ".join(program_args)

    return (executable, program_args, full_command)


def main() -> int:
    """
    Main entry point for Vex.

    Returns:
        0 if success, 1 if error.
    """

    # Check arguments
    if len(sys.argv) < 2:
        return ERROR

    # Unpack returned tuple
    executable, program_args, full_command = _parse_command_line()

    try:
        clear_screen()

        # Hide real cursor
        print("\033[?25l", end="", flush=True)
        # time.sleep(0.2)
        display_logo()

        # Pre-import mistralai in background (heavy import: ~4s on ARM/Docker)
        threading.Thread(target=lambda: __import__("mistralai"), daemon=True).start()

        # Valgrind analysis, returns dictionary with all leaks
        parsed_data = _run_valgrind_analysis(executable, program_args)

        # If leak list is empty, exit
        parsed_errors = parsed_data.get("leaks", [])
        if not parsed_errors:
            print("\nNo memory leaks detected !\n")
            return SUCCESS

        # Display Valgrind summary
        display_summary(parsed_data)

        # Show real cursor
        print("\033[?25h", end="", flush=True)

        # Afficher le menu
        choice = interactive_menu(["Start analysis", "Quit Vex"])

        if choice == 1:  # "Quit" sélectionné
            print()
            return SUCCESS

        # ========================================
        # START ANALYSIS LOOP
        # ========================================

        # Store initial leak count (list size)
        initial_leak_count = len(parsed_errors)

        # Variable to track if we need to re-analyze
        need_reanalysis = False

        while True:
            # If need to re-analyze (after [v])
            if need_reanalysis:
                result = _reanalyze_after_compilation(full_command, initial_leak_count)
                if result is None:
                    return SUCCESS

                parsed_errors, initial_leak_count = result
                need_reanalysis = False

            # Hide cursor during analysis steps
            print("\033[?25l", end="", flush=True)

            # Extract source code
            _extract_source_code(parsed_errors)

            # Find root causes
            _find_root_causes(parsed_errors, executable)

            # Process all leaks
            status = _process_all_leaks(parsed_errors, executable)

            if status == "need_recompile":
                need_reanalysis = True
            elif status == "completed":
                print("\nAnalysis complete !\n")
                return SUCCESS
            elif status == "quit":
                return SUCCESS

    except ExecutableNotFoundError as e:
        print_error(str(e))
        return ERROR

    except ValgrindRunnerError as e:
        print_error(f"Issue with Valgrind :\n{e}")
        return ERROR

    except MistralAPIError as e:
        print_error(f"Issue with Mistra API :\n{e}")
        return ERROR

    except KeyboardInterrupt:
        print("\n\nAnalysis interrupted by user.\n")
        return SUCCESS

    except Exception as e:
        print_error(f"Unexpected error : {e}")
        import traceback

        traceback.print_exc()
        return ERROR

    finally:
        # Always restore cursor visibility on exit
        print("\033[?25h", end="", flush=True)


if __name__ == "__main__":
    sys.exit(main())
