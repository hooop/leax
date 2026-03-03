"""
Display Module for Vex

Formats and displays analysis results in the terminal.
"""

import os
import re
import sys
from typing import Optional

from code_extractor import _find_source_file
from colors import (
    RESET,
    GREEN,
    DARK_GREEN,
    LIGHT_YELLOW,
    DARK_YELLOW,
    DARK_PINK,
    RED,
    GRAY,
)
from type_defs import ValgrindError, MistralAnalysis, RealCause, CleanedCodeLines


def _build_header(error_number: int, total_errors: int) -> str:
    """
    Build the header with half-block frame.

    Args:
        error_number: Current leak number
        total_errors: Total number of leaks

    Returns:
        Formatted header with ANSI colors
    """

    leak_text = f"Leak {error_number} / {total_errors}"

    header = f"\n\033[38;5;224m│ Vex Analysis\n│ {leak_text}{RESET}\n"

    return header


def _build_valgrind_section(error: ValgrindError) -> str:
    """
    Builds the Valgrind output section with proper colors.

    Args:
        error: Error dictionary (type, bytes, blocks, backtrace, etc.)

    Returns:
        Formatted section with ANSI colors
    """

    output = ""

    # First line with complete info
    bytes_info = f"{error.get('bytes', '?')} bytes"
    if error.get("blocks"):
        bytes_info += f" in {error['blocks']} blocks"
    bytes_info += f" are {error.get('type', 'unknown')}"

    output += f"{LIGHT_YELLOW}{bytes_info}\n"

    # Malloc line (system) - use captured one or fallback
    allocation = error.get("allocation_line", "    at malloc (system allocator)")
    output += f"{allocation}\n"

    # Backtrace in Valgrind order (allocation → main)
    if error.get("backtrace"):
        backtrace_reversed = list(reversed(error["backtrace"]))
        for frame in backtrace_reversed:
            output += f"    by {frame.get('function', '?')} ({frame.get('file', '?')}:{frame.get('line', '?')})\n"

    output += f"{RESET}\n"

    return output


def _build_analysis_section(analysis: MistralAnalysis) -> str:
    """
    Builds the Vex analysis section with leak type and diagnosis.

    Args:
        analysis: Dictionary returned by Mistral

    Returns:
        Formatted section with ANSI colors
    """

    leak_type_labels = {
        1: "Memory was never freed",
        2: "Pointer was lost before freeing memory",
        3: "No pointer can access this memory anymore",
    }

    # Title
    output = f"{GREEN}• Diagnosis{RESET}\n\n"

    # Leak type
    leak_type = analysis.get("leak_type", 0)
    if leak_type in leak_type_labels:
        output += f"{DARK_YELLOW} ➤ {leak_type_labels[leak_type]}{RESET}\n\n"

    # diagnosis
    diagnosis = analysis.get("diagnosis", "No diagnosis available")
    output += f"{LIGHT_YELLOW}{diagnosis}{RESET}\n\n"

    return output


def _build_reasoning_section(analysis: MistralAnalysis) -> str:
    """
    Builds the reasoning section.

    Args:
        analysis: Dictionary returned by Mistral

    Returns:
        Formatted section with ANSI colors
    """

    reasoning = analysis.get("reasoning", [])

    if not reasoning:
        return ""

    # Title
    output = f"{GREEN}• Memory Trace{RESET}\n\n"

    # Each step
    for i, etape in enumerate(reasoning, 1):
        output += f"{DARK_YELLOW} {i}{DARK_YELLOW} ➤ {LIGHT_YELLOW}{etape}{RESET}\n"

    output += "\n"

    return output


def _find_line_number(filepath: str, code_to_find: str) -> Optional[int]:
    """
    Searches for the line number of code in a source file.
    """
    # Use the robust search from code_extractor
    found_path = _find_source_file(filepath)

    if not found_path:
        return None

    try:
        with open(found_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (IOError, UnicodeDecodeError):
        return None

    code_clean = code_to_find.strip()

    for i, line in enumerate(lines, start=1):
        if line.strip() == code_clean:
            return i

    return None


def _clean_and_sort_code_lines(
    source_file: str, cause: RealCause
) -> Optional[CleanedCodeLines]:
    """
    Cleans and sorts code lines according to their actual position in the file.
    Removes duplicates and lines in wrong order.

    Args:
        source_file: Source file path
        cause: real_cause dict from Mistral

    Returns:
        Cleaned lines with line numbers, or None if not found
    """
    # 1. Find root_cause line number
    root_code = cause.get("root_cause_code", "")
    root_line = _find_line_number(source_file, root_code)

    if not root_line:
        return None

    # 2. Process contributing_codes
    contributing = []
    seen_codes = set()  # To avoid duplicates

    for contrib in cause.get("contributing_codes", []):
        code = contrib.get("code", "").strip()

        # Ignore if duplicate
        if code in seen_codes:
            continue

        # Ignore if equal to root_cause
        if code == root_code.strip():
            continue

        line_num = _find_line_number(source_file, code)

        # Ignore if not found or after root_cause
        if not line_num or line_num >= root_line:
            continue

        seen_codes.add(code)
        contributing.append(
            {"line": line_num, "code": code, "comment": contrib.get("comment")}
        )

    # Sort by ascending line number
    contributing.sort(key=lambda x: x["line"])

    # 3. Process context_before
    context_before = None
    context_before_code = cause.get("context_before_code", "").strip()

    if context_before_code:
        # Ignore if already in contributing or equal to root
        if (
            context_before_code not in seen_codes
            and context_before_code != root_code.strip()
        ):
            ctx_line = _find_line_number(source_file, context_before_code)

            # Must be before root_cause
            if ctx_line and ctx_line < root_line:
                context_before = {"line": ctx_line, "code": context_before_code}

    # 4. Process context_after
    context_after = None
    context_after_code = cause.get("context_after_code", "").strip()

    if context_after_code:
        # Ignore if already seen or equal to root
        if (
            context_after_code not in seen_codes
            and context_after_code != root_code.strip()
        ):
            ctx_line = _find_line_number(source_file, context_after_code)

            # Must be after root_cause
            if ctx_line and ctx_line > root_line:
                context_after = {"line": ctx_line, "code": context_after_code}

    return {
        "root_line": root_line,
        "root_code": root_code,
        "root_comment": cause.get("root_cause_comment", ""),
        "contributing": contributing,
        "context_before": context_before,
        "context_after": context_after,
    }


def _build_code_section(error: ValgrindError, analysis: MistralAnalysis) -> str:
    """
    Builds the code section with source code and root cause.

    Args:
        error: Error dictionary
        analysis: Dictionary returned by Mistral

    Returns:
        Formatted section with ANSI colors
    """

    cause = analysis.get("real_cause")
    if not cause:
        return ""

    # SPECIAL CASE: closing brace (Type 2 at end of function)
    # or end of program (Type 1, memory never freed)
    root_code_str = cause.get("root_cause_code", "").strip()
    if root_code_str in ("}", "end of program"):
        target_function = cause.get("function")
        extracted_code = error.get("extracted_code", [])

        cleaned = None
        for frame in extracted_code:
            if frame.get("function") == target_function:
                # Take last line
                code_lines = frame.get("code", "").strip().split("\n")
                if code_lines:
                    last_line = code_lines[-1]  # "127: }"

                    # Extract number: "127: }" → 127
                    match = re.match(r"(\d+):", last_line)
                    if match:
                        line_num = int(match.group(1))

                        # Create cleaned directly
                        cleaned = {
                            "root_line": line_num,
                            "root_code": "}",
                            "root_comment": cause.get("root_cause_comment", ""),
                            "contributing": [],
                            "context_before": None,
                            "context_after": None,
                        }

                        # Skip _clean_and_sort_code_lines() and go to display
                        break

        if not cleaned:
            # Function not in extracted_code (e.g. detected via GDB
            # param_mapping).  Fall back to root_cause metadata.
            rc = error.get("root_cause", {})
            rc_line = rc.get("line")
            if rc_line == "}":
                # Use the line number from the trace step.
                # The Mistral analysis stores the line in real_cause.
                line_num = cause.get("line_number")
                if line_num:
                    cleaned = {
                        "root_line": line_num,
                        "root_code": "}",
                        "root_comment": cause.get("root_cause_comment", ""),
                        "contributing": [],
                        "context_before": None,
                        "context_after": None,
                    }
            if not cleaned:
                output = f"{GREEN}• Root Cause{RESET}\n\n"
                output += (
                    f"{LIGHT_YELLOW}Impossible de localiser le code source.{RESET}\n\n"
                )
                return output
    else:
        # NORMAL CASE: call _clean_and_sort_code_lines()
        source_file = cause.get("file", error.get("file", "unknown"))
        cleaned = _clean_and_sort_code_lines(source_file, cause)

        if not cleaned:
            output = f"{GREEN}• Root Cause{RESET}\n\n"
            output += (
                f"{LIGHT_YELLOW}Impossible de localiser le code source.{RESET}\n\n"
            )
            return output

    # Title
    output = f"{GREEN}• Root Cause{RESET}\n\n"

    # Get source file
    source_file = cause.get("file", error.get("file", "unknown"))

    # File and function
    display_function = cause.get("function", error.get("function", "unknown"))

    output += f"{LIGHT_YELLOW}File     : {source_file}:{cleaned['root_line']}\n"
    output += f"Function : {display_function}(){RESET}\n\n"

    # Build ordered list of all lines to display
    lines_to_display = []

    # root_cause
    lines_to_display.append(
        {
            "line": cleaned["root_line"],
            "code": cleaned["root_code"],
            "comment": cleaned["root_comment"],
            "is_root": True,
        }
    )

    # Display with gap detection
    for i, item in enumerate(lines_to_display):
        # Display "-" if gap detected
        if i > 0:
            prev_line = lines_to_display[i - 1]["line"]
            curr_line = item["line"]
            if curr_line - prev_line > 1:
                output += f"      {GRAY}-{RESET}\n"

        # Display line
        if item["is_root"]:
            output += f"{DARK_PINK} ➤ {item['line']} | {item['code']}{RESET}"
            if item["comment"]:
                output += f"  {GRAY}// {item['comment']}{RESET}"
            output += "\n"
        else:
            # Normal line
            output += f"   {item['line']} | {item['code']}"
            if item["comment"]:
                output += f"  {GRAY}// {item['comment']}{RESET}"
            output += "\n"

    output += "\n"

    return output


def _build_solution_section(analysis: MistralAnalysis) -> str:
    """
    Builds the solution section with principle and code.

    Args:
        analysis: Dictionary returned by Mistral

    Returns:
        Formatted section with ANSI colors
    """

    output = f"{GREEN}• Proposed Solution{RESET}\n\n"

    resolution = analysis.get("resolution_principle", "No resolution proposed")
    output += f"{LIGHT_YELLOW}{resolution}{RESET}\n\n"

    # Resolution code
    if analysis.get("resolution_code"):
        output += f"{analysis['resolution_code']}\n\n"

    return output


def _build_explanations_section(analysis: MistralAnalysis) -> str:
    """
    Builds the explanations section.

    Args:
        analysis: Dictionary returned by Mistral

    Returns:
        Formatted section with ANSI colors
    """

    output = ""

    # Explanation content
    explanations = analysis.get("explanations", "No explanation available")
    output += f"{LIGHT_YELLOW}{explanations}{RESET}\n"

    return output


def display_analysis(
    error: ValgrindError,
    analysis: MistralAnalysis,
    error_number: int = 1,
    total_errors: int = 1,
    show_details: bool = False,
) -> None:
    """
    Displays an analysis in formatted way in the terminal.

    Args:
        error: Error dictionary (type, bytes, file, line, etc.)
        analysis: Dictionary returned by Mistral (parsed JSON) or dict with 'error'
        error_number: Current error number
        total_errors: Total number of errors
        show_details: If True, expand Valgrind output and Explanation sections
    """
    print(_build_header(error_number, total_errors))

    detail_hint = (
        f"{GRAY}Press [d] to hide{RESET}"
        if show_details
        else f"{GRAY}Press [d] for details{RESET}"
    )

    print(f"{GREEN}• Valgrind Output {detail_hint}{RESET}\n")
    if show_details:
        print(_build_valgrind_section(error))

    # If error in Mistral analysis
    if "error" in analysis:
        print(f"Mistral error : {analysis['error']}")
        if "raw" in analysis:
            print(f"\nRaw response :\n{analysis['raw']}")
        return

    print(_build_analysis_section(analysis))

    print(_build_reasoning_section(analysis))

    print(_build_code_section(error, analysis))

    print(_build_solution_section(analysis))

    print(f"{GREEN}• Explanation {detail_hint}{RESET}\n")
    if show_details:
        print(_build_explanations_section(analysis))


def display_leak_menu() -> str:
    """
    Displays the menu after analyzing a leak.

    Returns:
        "verify", "skip", or "quit"
    """

    print(GRAY + "v ▸ Verify  ‧  n ▸ Next  ‧  q ▸ Quit")
    print("")

    while True:
        choice = input(DARK_GREEN + "vex > " + RESET).strip().lower()
        if choice in ("", "v", "verify"):
            os.system("clear")
            return "verify"
        elif choice in ("n", "next"):
            os.system("clear")
            return "skip"
        elif choice in ("q", "quit"):
            os.system("clear")
            return "quit"
        else:
            print(RED + "Invalid choice." + RESET)
            sys.stdout.write("\033[F")
            sys.stdout.write("\033[F")
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.flush()
