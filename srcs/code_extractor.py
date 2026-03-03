"""
Code Extractor for Leax

Extracts complete C functions from source files based on line numbers.
Uses brace counting to identify function boundaries.
"""

import os
from typing import Optional

from type_defs import StackFrame, ExtractedFunction


def extract_function(filepath: str, line_number: int) -> Optional[str]:
    """
    Extract the complete function containing the specified line.

    Args:
        filepath: Path to the C source file
        line_number: Line number within the function

    Returns:
        The complete function code, or None if extraction fails
    """
    # If file doesn't exist, try to find it
    if not os.path.exists(filepath):
        filepath = _find_source_file(filepath)
        if not filepath:
            return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (IOError, UnicodeDecodeError):
        return None

    if line_number < 1 or line_number > len(lines):
        return None

    # Find function start by going backwards
    start_line = _find_function_start(lines, line_number - 1)
    if start_line is None:
        return None

    # Find function end by counting braces
    end_line = _find_function_end(lines, start_line)
    if end_line is None:
        return None

    # Extract from start to end of function
    result = []
    for i in range(start_line, end_line + 1):
        line_num = i + 1
        result.append(f"{line_num}: {lines[i]}")
    return "".join(result)


def _find_function_start(lines: list[str], from_line: int) -> Optional[int]:
    """
    Find the start of a function by going backwards from a line.
    Looks for opening brace at the start of a line or after a closing parenthesis.

    Args:
        lines: List of file lines
        from_line: Index to start searching backwards from (0-indexed)

    Returns:
        Index of the line where the function starts, or None
    """
    for i in range(from_line, -1, -1):
        line = lines[i].strip()

        # Skip empty lines and preprocessor directives
        if not line or line.startswith("#"):
            continue

        # Function typically starts with opening brace
        if line.endswith("{"):
            # Go back to find the function signature
            func_start = i
            while func_start > 0 and not lines[func_start - 1].strip().startswith(
                (
                    "static",
                    "void",
                    "int",
                    "char",
                    "float",
                    "double",
                    "long",
                    "short",
                    "unsigned",
                )
            ):
                func_start -= 1
                # Stop if we hit an empty line or closing brace
                if not lines[func_start].strip() or lines[func_start].strip() == "}":
                    func_start += 1
                    break
            return func_start

    return None


def _find_function_end(lines: list[str], start_line: int) -> Optional[int]:
    """
    Find the end of a function by counting braces from the start.

    Args:
        lines: List of file lines
        start_line: Index where the function starts (0-indexed)

    Returns:
        Index of the line where the function ends, or None
    """
    brace_count = 0
    found_opening = False

    for i in range(start_line, len(lines)):
        line = lines[i]

        # Count braces
        for char in line:
            if char == "{":
                brace_count += 1
                found_opening = True
            elif char == "}":
                brace_count -= 1

                # Function ends when braces balance
                if found_opening and brace_count == 0:
                    return i

    return None


def extract_call_stack(stack_frames: list[StackFrame]) -> list[ExtractedFunction]:
    """
    Extract all functions from a call stack, from main to the problematic function.

    Args:
        stack_frames: List of stack frames from valgrind_parser
                     Each frame should have 'file', 'line', and 'function' keys

    Returns:
        List of dicts with extracted code for each frame:
        [
            {
                'file': 'path/to/file.c',
                'function': 'function_name',
                'line': 42,
                'code': '... function code ...'
            },
            ...
        ]
    """
    extracted = []

    # Process frames in reverse order (from main to problem)
    for frame in reversed(stack_frames):
        # Skip system functions (no file path or in system directories)
        if not frame.get("file") or _is_system_file(frame["file"]):
            continue

        code = extract_function(frame["file"], frame["line"])

        if code:
            extracted.append(
                {
                    "file": frame["file"],
                    "function": frame.get("function", "unknown"),
                    "line": frame["line"],
                    "code": code,
                }
            )

    return extracted


def _is_system_file(filepath: str) -> bool:
    """
    Check if a file is a system file (libc, standard library, etc).

    Args:
        filepath: Path to check

    Returns:
        True if it's a system file to skip
    """
    system_paths = ["/usr/include/", "/usr/lib/", "libc", "libpthread"]
    return any(path in filepath for path in system_paths)


def _find_source_file(filename: str) -> Optional[str]:
    """
    Search for source file by intelligently traversing directory tree.
    Finds project root first, then searches recursively from there.

    Args:
        filename: Name of the file to find (e.g., "push_swap_utils.c")

    Returns:
        Full path to the file if found, None otherwise
    """
    import subprocess
    import os

    basename = os.path.basename(filename)

    # Project markers that indicate root directory
    project_markers = ["Makefile", "CMakeLists.txt", "src", "include", ".git"]

    def find_project_root(start_dir):
        """Find project root by looking for markers, up to 3 levels up"""
        current = start_dir
        levels_checked = 0

        while levels_checked < 3:
            if any(
                os.path.exists(os.path.join(current, marker))
                for marker in project_markers
            ):
                return current

            parent = os.path.dirname(current)
            if parent == current:  # Already at filesystem root
                break
            current = parent
            levels_checked += 1

        return start_dir  # Fallback to current directory

    # Determine best search starting point
    search_root = find_project_root(os.getcwd())

    # Build list of paths to search
    search_paths = [search_root]

    # Add common source directories if they exist
    for common_dir in ["src", "source", "sources", "lib", "include"]:
        candidate_path = os.path.join(search_root, common_dir)
        if os.path.exists(candidate_path) and os.path.isdir(candidate_path):
            search_paths.append(candidate_path)

    # Search in each path
    for search_path in search_paths:
        try:
            result = subprocess.run(
                ["find", search_path, "-name", basename, "-type", "f"],
                capture_output=True,
                text=True,
                timeout=2,
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split("\n")[0]
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            continue

    return None


def format_for_ai(extracted_functions: list[ExtractedFunction]) -> str:
    """
    Format extracted functions into a clean string for AI analysis.

    Args:
        extracted_functions: List of extracted function dicts

    Returns:
        Formatted string ready to send to Mistral AI
    """
    if not extracted_functions:
        return "No source code could be extracted."

    output = []
    output.append("=== CALL STACK WITH SOURCE CODE ===\n")

    for i, func in enumerate(extracted_functions, 1):
        output.append(f"\n--- Function {i}: {func['function']} ---")
        output.append(f"File: {func['file']}")
        output.append(f"Line: {func['line']}\n")
        output.append(func["code"])
        output.append("\n")

    return "\n".join(output)
