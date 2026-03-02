"""
GDB-based pointer tracer for Vex.

Traces the real execution path of a leaked pointer by running the program
under GDB. Uses GDB's Python API via a generated batch script to:
  1. Break at the allocation site identified by Valgrind.
  2. Capture the allocated address.
  3. Step through relevant functions, recording each executed line.
  4. Detect free() calls on the tracked address.

This module produces a flat execution trace that memory_tracker can
consume to perform root-cause analysis on the *real* execution path,
handling loops, branches, and indirect frees that static analysis misses.
"""

import json
import os
import subprocess
import tempfile
from typing import Optional

from type_defs import GdbTraceResult, TraceStep, FreeEvent

# Sentinel markers used to delimit JSON output in GDB's stdout.
_TRACE_BEGIN = "VEX_TRACE_BEGIN"
_TRACE_END = "VEX_TRACE_END"


# =============================================================================
# PUBLIC API
# =============================================================================

def trace_pointer(
    executable: str,
    alloc_file: str,
    alloc_line: int,
    alloc_var: str,
    backtrace_functions: list[str],
    caller_file: str = "",
    caller_line: int = 0,
) -> GdbTraceResult:
    """
    Trace a leaked pointer through real program execution using GDB.

    Args:
        executable:           Path to the compiled binary (must include debug
                              symbols, compiled with ``-g -O0``).
        alloc_file:           Source file where the leaking ``malloc`` occurs.
        alloc_line:           Line number of the ``malloc`` call.
        alloc_var:            Variable that receives the ``malloc`` result
                              (e.g. ``"ptr"`` or ``"n->data"``).
        backtrace_functions:  Function names from the Valgrind backtrace,
                              ordered from the innermost (allocation) to the
                              outermost (``main``).
        caller_file:          Source file of the caller in the backtrace
                              (one level above allocation).  Used to
                              disambiguate when the same function is called
                              from multiple sites.
        caller_line:          Line number in the caller.

    Returns:
        A ``GdbTraceResult`` dict.  On success ``result["success"]`` is
        ``True`` and ``result["trace"]`` contains the ordered execution
        steps.  On failure ``result["error"]`` describes the problem.
    """
    # Generate the GDB Python script.
    script = _generate_gdb_script(
        alloc_file, alloc_line, alloc_var, backtrace_functions,
        caller_file, caller_line,
    )

    # Run GDB in batch mode.
    raw_output = _run_gdb(executable, script)
    if raw_output is None:
        return _error_result("GDB execution failed or timed out.")

    # Parse the structured JSON emitted by the script.
    result = _parse_trace_output(raw_output)
    if result is None:
        return _error_result(
            "Could not parse GDB trace output. "
            "The program may have crashed before the allocation."
        )

    # Resolve source-code text for every trace step.
    _resolve_trace_code(result["trace"])

    return result


def check_gdb_available() -> bool:
    """Return ``True`` if GDB is installed and reachable on ``$PATH``."""
    try:
        proc = subprocess.run(
            ["gdb", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# =============================================================================
# GDB SCRIPT GENERATION
# =============================================================================

def _generate_gdb_script(
    alloc_file: str,
    alloc_line: int,
    alloc_var: str,
    backtrace_functions: list[str],
    caller_file: str = "",
    caller_line: int = 0,
) -> str:
    """
    Build a self-contained Python script for GDB batch execution.

    The script is designed to run inside GDB's embedded Python interpreter
    (``gdb -batch -x script.py``).  It prints a JSON object delimited by
    ``VEX_TRACE_BEGIN`` / ``VEX_TRACE_END`` markers to *stdout* so that the
    calling process can parse the results reliably.

    When ``caller_file`` and ``caller_line`` are provided, the script
    breaks at the **caller** site and steps into the allocation function.
    This disambiguates cases where the same allocation function is called
    from multiple sites (e.g. inside vs outside a loop).

    Args:
        alloc_file:          Source file of the allocation.
        alloc_line:          Line number of the allocation.
        alloc_var:           Variable receiving the ``malloc`` return value.
        backtrace_functions: Relevant function names (innermost first).
        caller_file:         Source file of the caller (optional).
        caller_line:         Line number in the caller (optional).

    Returns:
        The complete Python script as a string.
    """
    # We embed parameters as literals inside the generated script.
    functions_literal = repr(set(backtrace_functions))

    script = f'''\
import gdb
import json
import os

# ---------------------------------------------------------------------------
# Parameters injected by Vex
# ---------------------------------------------------------------------------
ALLOC_FILE = {alloc_file!r}
ALLOC_LINE = {alloc_line!r}
ALLOC_VAR  = {alloc_var!r}
RELEVANT_FUNCTIONS = {functions_literal}
CALLER_FILE = {caller_file!r}
CALLER_LINE = {caller_line!r}

TRACE_BEGIN = {_TRACE_BEGIN!r}
TRACE_END   = {_TRACE_END!r}

MAX_STEPS = 50000  # Safety limit to avoid infinite loops.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def current_function_name():
    """Return the name of the current function, or None."""
    try:
        frame = gdb.selected_frame()
        return frame.name()
    except gdb.error:
        return None


def current_source_info():
    """Return (filename, line_number) for the current execution point."""
    try:
        frame = gdb.selected_frame()
        sal = frame.find_sal()
        if sal.symtab:
            return (sal.symtab.filename, sal.line)
    except gdb.error:
        pass
    return (None, None)


def is_user_code():
    """Check if the current execution point is in user source code.

    Returns True if the source file exists on disk (i.e. it is part
    of the user project, not a system library).  Falls back to a
    basename lookup from CWD when the absolute path recorded in the
    debug info does not match the current container layout.
    """
    src_file, _ = current_source_info()
    if not src_file:
        return False
    try:
        if os.path.isfile(src_file):
            return True
        basename = os.path.basename(src_file)
        if basename.endswith('.c') or basename.endswith('.h'):
            return os.path.isfile(basename)
        return False
    except (TypeError, ValueError):
        return False


def free_arg_register():
    """Return the register holding the first argument to free().

    On x86-64 this is $rdi; on AArch64 it is $x0.
    """
    arch_info = gdb.execute("show architecture", to_string=True)
    if "aarch64" in arch_info or "arm" in arch_info:
        return "$x0"
    return "$rdi"


def resolve_expression(expr):
    """Resolve index variables in an expression to their concrete values.

    Evaluates any sub-expression inside square brackets using GDB,
    replacing symbolic indices with their numeric values at the
    current execution point.

    Example: ``arr[i]`` with ``i == 3`` becomes ``arr[3]``.

    Args:
        expr: Variable expression potentially containing indices.

    Returns:
        The expression with indices resolved, or the original
        expression if resolution fails.
    """
    import re
    resolved = expr
    for match in re.finditer(r'\\[([^\\]]+)\\]', expr):
        index_expr = match.group(1)
        try:
            val = int(gdb.parse_and_eval(index_expr))
            resolved = resolved.replace(
                f'[{{index_expr}}]', f'[{{val}}]', 1,
            )
        except gdb.error:
            pass
    return resolved


def check_addr_intact(resolved_expr, tracked_addr):
    """Check whether *resolved_expr* still holds *tracked_addr*.

    Used after each GDB step to verify that the tracked memory
    address has not been overwritten or lost.

    Args:
        resolved_expr:  Concrete variable expression (e.g. ``arr[0]``).
        tracked_addr:   Integer address returned by the original malloc.

    Returns:
        ``True`` if the expression still evaluates to *tracked_addr*,
        ``False`` if it evaluates to a different value, or ``None``
        if the expression cannot be evaluated (e.g. out of scope).
    """
    if not resolved_expr or tracked_addr is None:
        return None
    try:
        current = int(gdb.parse_and_eval(f"(long)({{resolved_expr}})"))
        return current == tracked_addr
    except gdb.error:
        return None


def scan_params_for_address(tracked_addr):
    """Scan the current function's parameters for *tracked_addr*.

    Checks each parameter in two ways:

    1. **Direct**: the parameter value itself equals *tracked_addr*
       (e.g. ``process(ptr)`` where ``ptr`` is the tracked pointer).
    2. **Indirect**: the parameter points to memory that contains
       *tracked_addr* (e.g. ``cleanup(arr, n)`` where ``arr[k]``
       holds the tracked pointer).

    Returns:
        A dict mapping parameter names to the matched address value,
        or an empty dict.
    """
    if tracked_addr is None:
        return {{}}
    try:
        frame = gdb.selected_frame()
        block = frame.block()
        mappings = {{}}
        while block:
            for sym in block:
                if sym.is_argument:
                    try:
                        val = frame.read_var(sym)
                        int_val = int(val)
                        # Direct match: parameter IS the tracked pointer.
                        if int_val == tracked_addr:
                            mappings[sym.name] = int_val
                            continue
                        # Indirect match: dereference as array and check
                        # whether any element holds the tracked address.
                        try:
                            idx = 0
                            while True:
                                if int(val[idx]) == tracked_addr:
                                    mappings[sym.name] = tracked_addr
                                    break
                                idx += 1
                        except (gdb.error, gdb.MemoryError, TypeError):
                            pass
                    except (gdb.error, ValueError, OverflowError):
                        pass
            block = block.superblock
            if block and block.is_global:
                break
        return mappings
    except gdb.error:
        return {{}}


def read_source_line_gdb(filepath, line_num):
    """Read a single source line using GDB list command."""
    try:
        output = gdb.execute(
            f"list {{filepath}}:{{line_num}},{{filepath}}:{{line_num}}",
            to_string=True,
        )
        for raw_line in output.strip().split('\\n'):
            raw_line = raw_line.strip()
            if raw_line and raw_line[0].isdigit():
                parts = raw_line.split('\\t', 1)
                if len(parts) >= 2:
                    return parts[1].strip()
                idx = 0
                while idx < len(raw_line) and (raw_line[idx].isdigit() or raw_line[idx] == ' '):
                    idx += 1
                return raw_line[idx:].strip()
        return ""
    except gdb.error:
        return ""


# ---------------------------------------------------------------------------
# Main tracing logic
# ---------------------------------------------------------------------------

def run_trace():
    trace = []
    free_events = []
    tracked_address = None

    # 1. Break at the right call site and run the program. -----------------
    #    If we have a caller line, break there and step into the alloc
    #    function to reach the exact malloc call.  This disambiguates
    #    when the same function is called from multiple sites.
    if CALLER_FILE and CALLER_LINE:
        gdb.execute(f"break {{CALLER_FILE}}:{{CALLER_LINE}}")
        gdb.execute("run")
        # Step into the allocation function to reach the malloc line.
        gdb.execute("step")
        # Now we should be inside the alloc function.  Step until we
        # reach the malloc line.
        for _attempt in range(20):
            src_file, src_line = current_source_info()
            if src_line == ALLOC_LINE:
                break
            gdb.execute("next")
        # Execute the malloc line.
        gdb.execute("next")
    else:
        gdb.execute(f"break {{ALLOC_FILE}}:{{ALLOC_LINE}}")
        gdb.execute("run")
        # Execute the malloc line.
        gdb.execute("next")

    # 2. Capture the allocated address and record the alloc line. ----------

    try:
        addr_value = gdb.parse_and_eval(ALLOC_VAR)
        tracked_address = int(addr_value)
    except gdb.error:
        emit_result(False, trace, None, free_events,
                    "Could not read allocation variable after malloc.")
        return

    # Resolve the allocation expression to its concrete form.
    # For indexed variables like ``arr[i]``, this evaluates ``i``
    # at the current point to produce e.g. ``arr[0]``.
    resolved_expr = resolve_expression(ALLOC_VAR)

    # Record the allocation line as the first trace entry.
    # It was already executed by the ``next`` above but must appear in
    # the trace so that the memory tracker can initialise tracking.
    # Use the source info from GDB (absolute path) rather than the
    # relative ALLOC_FILE to ensure consistent path resolution.
    alloc_src_file, _ = current_source_info()
    trace.append({{
        "file": alloc_src_file or ALLOC_FILE,
        "line": ALLOC_LINE,
        "function": current_function_name() or "",
        "code": "",
        "addr_intact": True,
    }})

    # 3. Set a conditional breakpoint on free() for our address. ----------
    reg = free_arg_register()
    gdb.execute(
        f"break free if (long){{reg}} == (long){{tracked_address}}"
    )

    # 4. Delete the malloc breakpoint (no longer needed). -----------------
    gdb.execute("delete 1")

    # 5. Step through the program, tracing relevant functions. ------------
    steps = 0
    prev_func = None
    pending_param_mapping = None

    while steps < MAX_STEPS:
        steps += 1
        func = current_function_name()

        if func is None:
            break  # Program has likely exited.

        # --- Inject missing call site on function return -----------------
        #     When GDB returns from callee to caller, it lands on the line
        #     AFTER the call site.  The assignment line (e.g.
        #     ``ptr = callee()``) is skipped.  We look backwards up to
        #     3 lines to find and inject it into the trace.
        if (prev_func is not None
                and func != prev_func
                and is_user_code()):
            ret_file, ret_line = current_source_info()
            if ret_file and ret_line and ret_line > 1:
                for offset in range(1, 4):
                    check_line = ret_line - offset
                    if check_line < 1:
                        break
                    call_code = read_source_line_gdb(ret_file, check_line)
                    if call_code and '=' in call_code and (prev_func + '(') in call_code:
                        if (not trace
                                or trace[-1]["line"] != check_line
                                or trace[-1]["function"] != func
                                or trace[-1]["file"] != ret_file):
                            trace.append({{
                                "file": ret_file,
                                "line": check_line,
                                "function": func,
                                "code": "",
                            }})
                        break

        # --- Scan parameters on function entry ----------------------------
        #     When stepping into a new function (not a return — returns
        #     have a closing brace as the last trace step), check whether any
        #     parameter of the callee holds the tracked address.  The
        #     result is stored in *pending_param_mapping* and attached to
        #     the first trace step recorded inside the new function.
        if (prev_func is not None
                and func != prev_func
                and tracked_address is not None
                and is_user_code()
                and trace
                and trace[-1].get("code", "").strip() != chr(125)):
            params = scan_params_for_address(tracked_address)
            if params:
                pending_param_mapping = params

        prev_func = func

        # --- Free breakpoint hit -----------------------------------------
        if func == "free":
            # Walk up the call stack to find the relevant caller.
            caller_file, caller_line, caller_func = _find_relevant_caller()
            if caller_func:
                free_events.append({{
                    "caller_file": caller_file,
                    "caller_line": caller_line,
                    "caller_function": caller_func,
                }})
            try:
                gdb.execute("finish")
                # Update prev_func so the return to the caller is not
                # mistaken for a new function entry (avoids false scans).
                prev_func = current_function_name() or prev_func
            except gdb.error:
                break
            continue

        # --- User code: log and step --------------------------------------
        if is_user_code():
            src_file, src_line = current_source_info()
            if src_file and src_line:
                # Deduplicate: skip if same file/line/function as last entry.
                # GDB reports multiple steps per source line (one per
                # machine instruction), but we only need one per line.
                if (not trace
                        or trace[-1]["line"] != src_line
                        or trace[-1]["function"] != func
                        or trace[-1]["file"] != src_file):
                    entry = {{
                        "file": src_file,
                        "line": src_line,
                        "function": func,
                        "code": "",
                    }}
                    # Attach parameter mapping from function entry scan.
                    if pending_param_mapping is not None:
                        entry["param_mapping"] = pending_param_mapping
                        pending_param_mapping = None
                    trace.append(entry)
            try:
                gdb.execute("step")
            except gdb.error:
                break
            # Annotate the last recorded step with address integrity.
            # This check runs AFTER the step executes, so it tells us
            # whether the tracked address survived the executed line.
            if trace:
                trace[-1]["addr_intact"] = check_addr_intact(
                    resolved_expr, tracked_address,
                )
            continue

        # --- System/library code: skip it --------------------------------
        try:
            gdb.execute("finish")
            prev_func = current_function_name() or prev_func
        except gdb.error:
            break

    emit_result(True, trace, tracked_address, free_events, "")


def _find_relevant_caller():
    """Walk up the stack to find the first frame in user source code.

    Returns:
        (file, line, function) or (None, None, None).
    """
    try:
        frame = gdb.selected_frame().older()
        while frame:
            sal = frame.find_sal()
            if sal.symtab and os.path.isfile(sal.symtab.filename):
                return (sal.symtab.filename, sal.line, frame.name())
            frame = frame.older()
    except gdb.error:
        pass
    return (None, None, None)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def emit_result(success, trace, tracked_address, free_events, error):
    result = {{
        "success": success,
        "trace": trace,
        "tracked_address": str(tracked_address) if tracked_address else "",
        "free_events": free_events,
        "error": error,
    }}
    print(TRACE_BEGIN)
    print(json.dumps(result))
    print(TRACE_END)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
try:
    run_trace()
except Exception as exc:
    emit_result(False, [], None, [], str(exc))

gdb.execute("quit")
'''
    return script


# =============================================================================
# GDB EXECUTION
# =============================================================================

def _run_gdb(executable: str, script_content: str) -> Optional[str]:
    """
    Execute GDB in batch mode with the given Python script.

    Args:
        executable:     Path to the target binary.
        script_content: Python script to pass via ``-x``.

    Returns:
        Combined stdout+stderr from GDB, or ``None`` on failure.
    """
    script_fd = None
    script_path = None

    try:
        # Write script to a temporary file.
        script_fd, script_path = tempfile.mkstemp(
            suffix=".py", prefix="vex_gdb_"
        )
        with os.fdopen(script_fd, "w") as f:
            f.write(script_content)
        script_fd = None  # Ownership transferred to the with-block.

        command = [
            "gdb",
            "--batch",          # Exit after script completes.
            "--quiet",          # Suppress banner.
            "-x", script_path,  # Load our script.
            executable,
        ]

        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
        )

        return proc.stdout + proc.stderr

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    finally:
        if script_fd is not None:
            os.close(script_fd)
        if script_path and os.path.exists(script_path):
            os.unlink(script_path)


# =============================================================================
# OUTPUT PARSING
# =============================================================================

def _parse_trace_output(raw_output: str) -> Optional[GdbTraceResult]:
    """
    Extract the JSON payload emitted between sentinel markers.

    Args:
        raw_output: Raw stdout+stderr captured from GDB.

    Returns:
        Parsed ``GdbTraceResult``, or ``None`` if markers are missing or
        the JSON is malformed.
    """
    begin_idx = raw_output.find(_TRACE_BEGIN)
    end_idx = raw_output.find(_TRACE_END)

    if begin_idx == -1 or end_idx == -1 or end_idx <= begin_idx:
        return None

    json_str = raw_output[begin_idx + len(_TRACE_BEGIN):end_idx].strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    # Normalise to the expected TypedDict shape.
    return {
        "success": data.get("success", False),
        "trace": data.get("trace", []),
        "tracked_address": data.get("tracked_address", ""),
        "free_events": data.get("free_events", []),
        "error": data.get("error", ""),
    }


# =============================================================================
# SOURCE-CODE RESOLUTION
# =============================================================================

_source_cache: dict[str, list[str]] = {}


def _read_source_line(filepath: str, line_number: int) -> str:
    """
    Read a single source line from a file (1-indexed).

    If ``filepath`` does not exist, a recursive search is attempted
    from the current working directory using the basename.
    Results are cached per file for the lifetime of the process.

    Args:
        filepath:    Path to the source file.
        line_number: 1-based line number.

    Returns:
        The stripped source line, or an empty string on failure.
    """
    if filepath not in _source_cache:
        resolved = filepath
        if not os.path.isfile(resolved):
            resolved = _find_file_by_name(os.path.basename(filepath))
        if resolved:
            try:
                with open(resolved, "r", encoding="utf-8") as f:
                    _source_cache[filepath] = f.readlines()
            except (IOError, UnicodeDecodeError):
                _source_cache[filepath] = []
        else:
            _source_cache[filepath] = []

    lines = _source_cache[filepath]
    if 0 < line_number <= len(lines):
        return lines[line_number - 1].strip()
    return ""


def _find_file_by_name(basename: str) -> Optional[str]:
    """
    Search for a file by name starting from the current working directory.

    Walks the directory tree up to 5 levels deep.

    Args:
        basename: Filename to search for (e.g. ``"leaky.c"``).

    Returns:
        Absolute path if found, ``None`` otherwise.
    """
    for root, _dirs, files in os.walk(os.getcwd()):
        # Limit search depth.
        depth = root.replace(os.getcwd(), "").count(os.sep)
        if depth > 5:
            continue
        if basename in files:
            return os.path.join(root, basename)
    return None


def _resolve_trace_code(trace: list[TraceStep]) -> None:
    """
    Fill in the ``code`` field of every trace step by reading source files.

    Operates in place.

    Args:
        trace: List of trace steps whose ``code`` field may be empty.
    """
    for step in trace:
        if not step["code"]:
            step["code"] = _read_source_line(step["file"], step["line"])


# =============================================================================
# HELPERS
# =============================================================================

def _error_result(message: str) -> GdbTraceResult:
    """Build a failure ``GdbTraceResult``."""
    return {
        "success": False,
        "trace": [],
        "tracked_address": "",
        "free_events": [],
        "error": message,
    }
