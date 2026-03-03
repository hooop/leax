"""
Mistral API Module for Vex

Sends memory leak analysis requests to Mistral AI and returns pedagogical explanations.
"""

import os
import json
from typing import Optional

from type_defs import ValgrindError, RootCauseInfo, MistralAnalysis

# Lazy-loaded client (mistralai import takes ~4s on ARM/Docker)
_client = None


def _get_client():
    """Return Mistral client, initializing on first call."""
    global _client
    if _client is not None:
        return _client

    from dotenv import load_dotenv
    from mistralai import Mistral

    load_dotenv()
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise ValueError(
            "MISTRAL_API_KEY is not set.\n"
            "Create a .env file with: MISTRAL_API_KEY=your_key"
        )
    _client = Mistral(api_key=api_key)
    return _client


def _clean_json_response(response: str) -> str:
    """
    Clean API response to extract pure JSON.

    Args:
        response: Raw response string from Mistral API.

    Returns:
        Cleaned JSON string ready for parsing.
    """

    response = response.strip()

    if "```" in response:
        start = response.find("{")
        end = response.rfind("}")
        if start != -1 and end != -1:
            response = response[start : end + 1]

    return response.strip()


def analyze_memory_leak(
    error_data: ValgrindError,
    code_context: str,
    root_cause: Optional[RootCauseInfo] = None,
) -> MistralAnalysis:
    """
    Analyze a memory leak using Mistral AI.

    Args:
        error_data: Valgrind error information.
        code_context: Formatted source code string.
        root_cause: Root cause identified by memory_tracker (optional).

    Returns:
        Structured analysis or dict with 'error' field on failure.
    """

    try:
        prompt = _build_prompt(error_data, code_context, root_cause)

        response = _call_mistral_api(prompt)

        # Nettoie la réponse
        cleaned = _clean_json_response(response)

        # Parse le JSON
        analysis = json.loads(cleaned)

        # Validation basique
        required_keys = [
            "leak_type",
            "diagnosis",
            "reasoning",
            "resolution_principle",
            "resolution_code",
            "explanations",
        ]

        for key in required_keys:
            if key not in analysis:
                raise ValueError(f"Missing key: {key}")

        # Injecter les données de root_cause dans la réponse
        if root_cause:
            analysis["leak_type"] = root_cause["type"]
            if "real_cause" not in analysis:
                analysis["real_cause"] = {}
            analysis["real_cause"]["file"] = root_cause.get("file", "unknown")
            analysis["real_cause"]["function"] = root_cause["function"]
            analysis["real_cause"]["root_cause_code"] = str(root_cause["line"]).strip()
            if root_cause.get("line_number"):
                analysis["real_cause"]["line_number"] = root_cause["line_number"]

        return analysis

    except json.JSONDecodeError as e:
        return {
            "error": f"Invalid JSON: {str(e)}",
            "raw": response if "response" in locals() else "N/A",
        }
    except Exception as e:
        return {"error": str(e)}


def _format_steps(steps: Optional[list[str]]) -> str:
    """
    Format memory tracking steps for prompt.

    Args:
        steps: List of tracking steps (optional).

    Returns:
        Formatted string with numbered steps.
    """

    if not steps:
        return "No steps available"

    formatted = ""
    for i, step in enumerate(steps, 1):
        formatted += f"  {i}. {step}\n"
    return formatted


def _format_gdb_trace(gdb_trace: list[dict]) -> str:
    """
    Format GDB execution trace for prompt.

    Each entry contains file, line, function and code representing
    the actual lines executed at runtime, in order.  Repeated loop
    iterations with identical line sequences are compressed to avoid
    sending huge traces to the LLM.

    Args:
        gdb_trace: List of trace steps from GDB.

    Returns:
        Formatted string showing executed lines in order.
    """

    if not gdb_trace:
        return ""

    compressed = _compress_trace(gdb_trace)

    formatted = ""
    for entry in compressed:
        if entry["type"] == "line":
            step = entry["step"]
            func = step.get("function", "?")
            line = step.get("line", "?")
            code = step.get("code", "").strip()
            if code:
                formatted += f"  {func}() line {line}: {code}\n"
            else:
                formatted += f"  {func}() line {line}\n"
        else:
            formatted += f"  [... same {entry['length']} lines repeated {entry['count']} more times]\n"
    return formatted


def _compress_trace(gdb_trace: list[dict]) -> list[dict]:
    """
    Compress repeated sequences in a GDB trace.

    Detects consecutive loop iterations that execute the exact same
    sequence of (function, line) pairs and replaces duplicates with
    a single occurrence plus a repetition count.

    Args:
        gdb_trace: Raw trace steps.

    Returns:
        List of entries, each either:
        - {"type": "line", "step": <original step>}
        - {"type": "repeat", "length": <sequence length>, "count": <extra repetitions>}
    """

    # Build signature list: (function, line) for each step.
    signatures = [(s.get("function", ""), s.get("line", 0)) for s in gdb_trace]
    n = len(signatures)

    result = []
    i = 0

    while i < n:
        # Try to find a repeating sequence starting at i.
        best_len = 0
        best_count = 0

        # Try sequence lengths from 2 to 20 lines.
        for seq_len in range(2, min(21, n - i + 1)):
            pattern = signatures[i : i + seq_len]
            count = 1
            j = i + seq_len
            while j + seq_len <= n and signatures[j : j + seq_len] == pattern:
                count += 1
                j += seq_len
            # Keep the best (most total lines compressed).
            if count >= 2 and count * seq_len > best_count * best_len:
                best_len = seq_len
                best_count = count

        if best_count >= 2:
            # Emit the first occurrence.
            for k in range(i, i + best_len):
                result.append({"type": "line", "step": gdb_trace[k]})
            # Emit compression marker for the rest.
            result.append(
                {
                    "type": "repeat",
                    "length": best_len,
                    "count": best_count - 1,
                }
            )
            i += best_len * best_count
        else:
            result.append({"type": "line", "step": gdb_trace[i]})
            i += 1

    return result


def _build_prompt(
    error_data: ValgrindError,
    code_context: str,
    root_cause: Optional[RootCauseInfo] = None,
) -> str:
    """
    Build the prompt for Mistral API.

    Args:
        error_data: Valgrind error information.
        code_context: Formatted source code string.
        root_cause: Root cause identified by memory_tracker (optional).

    Returns:
        Complete prompt string for Mistral AI.
    """

    # Type de leak en texte
    type_labels = {
        1: "Type 1: Memory was never freed",
        2: "Type 2: Pointer was lost before freeing memory",
        3: "Type 3: Container was freed before its content",
    }

    # Infos root cause
    gdb_trace = root_cause.get("gdb_trace") if root_cause else None

    if root_cause:
        root_cause_section = f"""
====================================================
ROOT CAUSE (identified by analysis)
====================================================

{type_labels.get(root_cause["type"], "Unknown type")}

File      : {root_cause.get("file", "unknown")}
Function  : {root_cause["function"]}()
Line      : {str(root_cause["line"]).strip()}

Memory path:
{_format_steps(root_cause.get("steps", []))}
"""
    else:
        root_cause_section = """
====================================================
ROOT CAUSE
====================================================

Not identified (manual analysis required)
"""

    # GDB execution trace section (when available)
    if gdb_trace:
        execution_trace_section = f"""
====================================================
EXECUTION TRACE (lines actually executed at runtime)
====================================================

The following lines were executed IN ORDER during the program run.
Lines that do NOT appear here were NOT executed.
Use this trace as the primary source to understand what happened.

{_format_gdb_trace(gdb_trace)}
"""
    else:
        execution_trace_section = ""

    # Source code section label depends on whether we have a trace
    if gdb_trace:
        source_code_label = (
            "SOURCE CODE (full functions for context — use for proposing fixes)"
        )
    else:
        source_code_label = "SOURCE CODE"

    prompt = f"""You are a C and memory management expert. You must explain a memory leak in a pedagogical way.

====================================================
VALGRIND REPORT
====================================================

{error_data.get("bytes", "?")} bytes in {error_data.get("blocks", "?")} blocks are {error_data.get("type", "definitely lost")}
Allocation function: {error_data.get("function", "unknown")}()
File: {error_data.get("file", "unknown")}
Line: {error_data.get("line", "?")}
{execution_trace_section}
====================================================
{source_code_label}
====================================================

{code_context}
{root_cause_section}

====================================================
YOUR MISSION
====================================================

1. Explain the diagnosis in 2-3 clear sentences
2. Provide step-by-step pedagogical reasoning (based on the memory path above)
3. Identify important code lines (contributing_codes)
4. Propose a solution with corrective code

CRITICAL: If an execution trace is provided above, base your reasoning ONLY on the lines that were actually executed. A line that appears in the source code but NOT in the execution trace was NOT executed during this run. Do not assume it was.

====================================================
JSON FORMAT (only, no text around)
====================================================

{{
  "leak_type": {root_cause["type"] if root_cause else 1},
  "diagnosis": "<clear explanation of the problem in 2-3 sentences>",
  "reasoning": [
    "Transform each step from 'Memory path' above into a clear, factual sentence",
    "Use real variable names, line numbers, and function names from the steps",
    "Example: 'ALLOC: ptr in func()' becomes 'malloc() allocates memory for ptr in func()'",
    "Keep it factual and descriptive, not pedagogical"
  ],
  "real_cause": {{
    "file": "{root_cause.get("file", "unknown") if root_cause else "unknown"}",
    "function": "{root_cause["function"] if root_cause else "unknown"}",
    "owner": "<variable that should have freed the memory>",
    "root_cause_code": "{str(root_cause["line"]).strip() if root_cause else ""}",
    "root_cause_comment": "<why this line causes the leak>",
    "contributing_codes": [
      {{"code": "<important line>", "comment": "<its role in the leak>"}},
      {{"code": "<other line>", "comment": "<its role>"}}
    ],
    "context_before_code": "<line just before root cause or empty>",
    "context_after_code": "<line just after root cause or empty>"
  }},
  "resolution_principle": "In <function>(), <action to do> before/after <existing code line> or before function ends",
  "resolution_code": "<exact C code to insert>",
  "explanations": "<pedagogical explanation of why this solution works> + <best practice rule>"
}}

IMPORTANT RULES:
- Use simple and pedagogical language
- Reasoning must guide the user step by step
- In "reasoning": no numbering, maximum 15 words per step
- Don't copy raw steps, rephrase them in an understandable way
- contributing_codes: only lines BEFORE root_cause_code
- root_cause_comment: maximum 10 words
- resolution_principle: mention function, action, and reference line
- When the root cause is a closing brace (pointer lost at end of scope), the fix must be placed just before that closing brace, not earlier in the function
- JSON only, no text around
"""

    return prompt


def _call_mistral_api(prompt: str) -> str:
    """
    Execute Mistral API call.

    Args:
        prompt: Complete prompt string.

    Returns:
        Raw response content from Mistral API.

    Raises:
        Exception: If API call fails.
    """
    try:
        response = _get_client().chat.complete(
            model="mistral-small-latest", messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    except Exception as e:
        raise Exception(f"Mistral API call failed: {str(e)}")
