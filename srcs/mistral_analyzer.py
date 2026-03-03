"""
Mistral AI analyzer wrapper for Leax integration.

Adapts mistral_api.py for integration with leax main workflow.
"""

from mistral_api import analyze_memory_leak
from type_defs import ValgrindError, ExtractedFunction, MistralAnalysis


class MistralAPIError(Exception):
    """Raised when Mistral API call fails."""

    pass


def analyze_with_mistral(error_data: ValgrindError) -> MistralAnalysis:
    """
    Analyze a memory error with Mistral AI.

    Args:
        error_data: Valgrind error with backtrace, code context and root cause.

    Returns:
        Structured analysis from Mistral AI.

    Raises:
        MistralAPIError: If API call fails.
    """
    try:
        # Format extracted code
        code_context = _format_extracted_code(error_data.get("extracted_code", []))

        # Get root cause (computed by memory_tracker)
        root_cause = error_data.get("root_cause", None)

        # Call Mistral API via mistral_api.py
        analysis = analyze_memory_leak(error_data, code_context, root_cause)

        return analysis

    except Exception as e:
        raise MistralAPIError(f"Analysis failed: {str(e)}")


def _format_extracted_code(extracted_code: list[ExtractedFunction]) -> str:
    """Format extracted code from call stack for Mistral prompt.

    Args:
        extracted_code: List of functions extracted from stack frames.

    Returns:
        Formatted string with numbered functions and their source code.
    """

    if not extracted_code:
        return "=== No source code available ===\n"

    formatted = "=== CALL STACK WITH SOURCE CODE ===\n\n"

    for i, frame in enumerate(extracted_code, 1):
        code_lines = frame["code"].strip().split("\n")
        last_line_num = code_lines[-1].split(":")[0] if code_lines else "?"

        formatted += f"{'=' * 50}\n"
        formatted += f"FUNCTION {i}: {frame['function']}\n"
        formatted += f"File: {frame['file']}\n"
        formatted += f"Starts at line: {frame['line']}\n"
        formatted += f"Function ends: line {last_line_num}\n"
        formatted += f"{'=' * 50}\n"
        formatted += frame["code"]
        formatted += "\n\n"

    return formatted
