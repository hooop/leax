"""
memory_tracker.py

Memory tracking algorithm to find root cause of memory leaks.
Analyzes code execution flow and tracks memory ownership.
"""

from typing import Optional

from type_defs import (
    TrackingEntry,
    RootCauseInfo,
    ProcessedFunction,
    ExtractedFunction,
    TraceStep,
    FreeEvent,
)

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def build_segments(path: str) -> list[str]:
    """Decompose a path into all its prefixes.

    Handles both member access (``->``) and array indexing (``[...]``).

    Args:
        path: Memory path to decompose
              (e.g., ``"head->next->data"``, ``"arr[i]"``).

    Returns:
        List of all prefixes, from the root variable to the full path.
        Example: ``"arr[i]"`` → ``["arr", "arr[i]"]``
        Example: ``"head->next->data"`` → ``["head", "head->next",
        "head->next->data"]``
    """
    import re

    segments = []
    # Split on ``->`` and ``[`` boundaries while keeping delimiters.
    # This produces tokens like: ``["arr", "[i]", "->", "next"]``.
    tokens = re.split(r"(->|\[)", path)

    current = ""
    for token in tokens:
        if token == "->" or token == "[":
            if current and current not in segments:
                segments.append(current)
            current += token
        else:
            current += token
            # Close bracket if we opened one.

    if current and current not in segments:
        segments.append(current)

    # Ensure the root (without any accessor) is always present.
    root = extract_root(path)
    if root and (not segments or segments[0] != root):
        segments.insert(0, root)

    return segments


def extract_root(path: str) -> str:
    """Extract the base variable from a path.

    Handles both member access (``->``) and array indexing (``[...]``).

    Args:
        path: Memory path (e.g., ``"head->next->data"``, ``"arr[i]"``).

    Returns:
        Base variable name (e.g., ``"head"``, ``"arr"``).
    """
    # Strip array index first, then member access.
    root = path.split("[")[0]
    root = root.split("->")[0]
    return root


def extract_free_argument(line: str) -> str:
    """Extract the argument from a free() call.

    Args:
        line: Code line containing free() (e.g., "free(second->next);")

    Returns:
        Freed variable name (e.g., "second->next")
    """

    start = line.index("free(") + 5
    end = line.index(")", start)
    return line[start:end].strip()


def extract_return_value(line: str) -> str:
    """Extract the returned value from a return statement.

    Args:
        line: Code line with return statement (e.g., "return n;", "return (n);")

    Returns:
        Returned variable or expression (e.g., "n", "ptr->data")
    """

    content = line.replace("return", "", 1).replace(";", "").strip()

    # Remove parentheses if present
    if content.startswith("(") and content.endswith(")"):
        content = content[1:-1].strip()

    return content


def extract_left_side(line: str) -> str:
    """Extract the left side of an assignment.

    Args:
        line: Code line with assignment (e.g., "Node *second = head->next;")

    Returns:
        Left-hand side variable name (e.g., "second", "head->next")
    """

    left_part = line.split("=")[0].strip()

    # If it's a declaration (Type *var), extract just var
    if "*" in left_part:
        # Find the last * and take what's after
        last_star = left_part.rfind("*")
        return left_part[last_star + 1 :].strip()

    return left_part


def extract_right_side(line: str) -> str:
    """Extract the right side of an assignment.

    Args:
        line: Code line with assignment (e.g., "Node *second = head->next;")

    Returns:
        Right-hand side value (e.g., "head->next", "NULL")
    """

    right_part = line.split("=", 1)[1].replace(";", "").strip()
    return right_part


# =============================================================================
# OPERATION DETECTION
# =============================================================================


def is_malloc(line: str) -> bool:
    """Check if line contains a heap allocation call."""
    return "malloc(" in line or "calloc(" in line or "strdup(" in line


def is_return(line: str) -> bool:
    """Check if line is a return statement."""
    return line.strip().startswith("return ")


def is_free(line: str) -> bool:
    """Check if line contains a free call."""
    return "free(" in line


def is_null_assignment(line: str) -> bool:
    """Check if line assigns NULL/0/nullptr."""
    return "= NULL" in line or "= 0" in line or "= nullptr" in line


def is_alias(line: str, found_segment: str) -> bool:
    """Check if line creates an alias to tracked memory.

    Args:
        line: Code line to analyze
        found_segment: Memory segment being tracked

    Returns:
        True if line is an assignment with found_segment on right side (not NULL)
    """

    if "=" not in line:
        return False

    if is_null_assignment(line):
        return False

    right_side = extract_right_side(line)
    return found_segment in right_side


def is_reassignment(line: str, found_segment: str) -> bool:
    """Check if line reassigns a tracked segment.

    Args:
        line: Code line to analyze
        found_segment: Memory segment being tracked

    Returns:
        True if line is an assignment with found_segment on left side
    """

    if "=" not in line:
        return False

    left_side = line.split("=")[0]
    return found_segment in left_side


# =============================================================================
# SEGMENT MATCHING
# =============================================================================


def find_segment_in_line(
    line: str, tracking: dict[str, TrackingEntry]
) -> tuple[bool, Optional[str], Optional[str], Optional[TrackingEntry], Optional[str]]:
    """
    Check if line manipulates any tracked segment.

    Returns: (found, root_key, found_segment, entry, operation_type)

    operation_type: "free", "return", "alias", "reassign", or None
    """

    # Collect all segments for lookup
    all_segments = {}
    for root_key, entry in tracking.items():
        for segment in entry["segments"]:
            all_segments[segment] = (root_key, entry)

    # CASE 1: free(...)
    if is_free(line):
        arg = extract_free_argument(line)
        if arg in all_segments:
            root_key, entry = all_segments[arg]
            return (True, root_key, arg, entry, "free")
        return (False, None, None, None, None)

    # CASE 2: return ...
    if is_return(line):
        ret_val = extract_return_value(line)
        if ret_val in all_segments:
            root_key, entry = all_segments[ret_val]
            return (True, root_key, ret_val, entry, "return")
        return (False, None, None, None, None)

    # CASE 3: assignment (x = y)
    # Skip lines where '=' is part of a comparison (==, !=, <=, >=)
    # but not a real assignment.  A real assignment has a standalone '='
    # not preceded or followed by =, !, <, >.
    import re

    has_assignment = "=" in line and re.search(r"(?<![=!<>])=(?!=)", line)
    if has_assignment:
        left = extract_left_side(line)
        right = extract_right_side(line)

        # Check for reassignment (left side is a tracked segment)
        if left in all_segments:
            root_key, entry = all_segments[left]
            return (True, root_key, left, entry, "reassign")

        # Check for alias (right side is a tracked segment)
        if right in all_segments and not is_null_assignment(line):
            root_key, entry = all_segments[right]
            return (True, root_key, right, entry, "alias")

    return (False, None, None, None, None)


# =============================================================================
# UPDATE RULES
# =============================================================================


def apply_init(line: str, tracking: dict[str, TrackingEntry]) -> None:
    """Create initial tracking structure from malloc line.

    Args:
        line: Code line with malloc (e.g., "ptr = malloc(10);", "n->data = malloc(...);")
        tracking: Dictionary of tracked memory paths (modified in place)
    """

    left_side = extract_left_side(line)
    root = extract_root(left_side)

    entry: TrackingEntry = {
        "target": left_side,
        "segments": build_segments(left_side),
        "origin": None,
    }

    tracking[root] = entry


def apply_return(
    line: str, tracking: dict[str, TrackingEntry], caller_line: str
) -> None:
    """Substitute local root with receiver in calling function.

    Args:
        line: Return statement in callee (e.g., "return n;")
        tracking: Dictionary of tracked memory paths (modified in place)
        caller_line: Assignment line in caller (e.g., "head->next = create_node();")
    """

    returned_var = extract_return_value(line)
    receiver = extract_left_side(caller_line)
    new_root = extract_root(receiver)

    # Find entry of returned variable
    old_root = extract_root(returned_var)

    if old_root not in tracking:
        return

    old_entry = tracking[old_root]

    # Calculate suffix (what comes after returned variable in target)
    # If target = "n->data" and returned_var = "n", suffix = "->data"
    suffix = old_entry["target"].replace(returned_var, "", 1)

    # New target = receiver + suffix
    new_target = receiver + suffix

    new_entry: TrackingEntry = {
        "target": new_target,
        "segments": build_segments(new_target),
        "origin": None,  # This becomes the new canonical form
    }

    # Remove old root, add new one
    del tracking[old_root]
    tracking[new_root] = new_entry


def apply_alias(
    line: str,
    aliased_segment: str,
    source_entry: TrackingEntry,
    tracking: dict[str, TrackingEntry],
) -> None:
    """Add a new root that points to the same memory.

    Args:
        line: Assignment creating alias (e.g., "Node *second = head->next;")
        aliased_segment: Memory segment being aliased (e.g., "head->next")
        source_entry: Original tracking entry for this memory
        tracking: Dictionary of tracked memory paths (modified in place)
    """

    new_name = extract_left_side(line)

    # Calculate suffix (what remains of target after aliased segment)
    suffix = source_entry["target"].replace(aliased_segment, "", 1)

    new_entry: TrackingEntry = {
        "target": new_name + suffix,
        "segments": build_segments(new_name + suffix),
        "origin": aliased_segment,
    }

    tracking[new_name] = new_entry


def apply_reassignment(
    root_key: str, tracking: dict[str, TrackingEntry], line: str, function: str
) -> Optional[RootCauseInfo]:
    """Remove the concerned root (path is broken by reassignment).

    Args:
        root_key: Key of the root being reassigned
        tracking: Dictionary of tracked memory paths (modified in place)
        line: Code line performing reassignment
        function: Function name where reassignment occurs

    Returns:
        RootCauseInfo if tracking becomes empty (Type 2 leak), None otherwise
    """

    del tracking[root_key]

    if len(tracking) == 0:
        if len(tracking) == 0:
            return {
                "leak_type": 2,
                "line": line,
                "function": function,
                "file": "",
                "steps": [],
            }

    return None


def apply_free(
    line: str,
    found_segment: str,
    entry: TrackingEntry,
    root_key: str,
    tracking: dict[str, TrackingEntry],
    function: str,
) -> Optional[RootCauseInfo]:
    """Handle free() call and detect improper memory release.

    Args:
        line: Code line with free() call
        found_segment: Memory segment being freed
        entry: Tracking entry for this memory
        root_key: Key of the root being freed
        tracking: Dictionary of tracked memory paths (modified in place)
        function: Function name where free occurs

    Returns:
        RootCauseInfo for Type 3 (freeing container before content) or Type 1 (never freed),
        None otherwise
    """

    free_arg = extract_free_argument(line)

    # If target starts with free_arg + "->" or free_arg + "[",
    # we're freeing the container before its content.
    if entry["target"].startswith(free_arg + "->") or entry["target"].startswith(
        free_arg + "["
    ):
        return {
            "leak_type": 3,
            "line": line,
            "function": function,
            "file": "",
            "steps": [],
        }

    # Otherwise, remove this root
    del tracking[root_key]

    if len(tracking) == 0:
        return {
            "leak_type": 1,
            "line": line,
            "function": function,
            "file": "",
            "steps": [],
        }

    return None


# =============================================================================
# INTEGRATION HELPER
# =============================================================================


def convert_extracted_code(
    extracted_functions: list[ExtractedFunction],
) -> list[ProcessedFunction]:
    """Convert code_extractor output to memory_tracker input format.

    Args:
        extracted_functions: List of extracted functions with numbered code lines

    Returns:
        List of functions with parsed code lines ready for memory tracking
    """
    result = []

    for func in extracted_functions:
        lines = []
        valgrind_line = func["line"]  # La ligne mentionnée par Valgrind

        # Parse the numbered code lines
        for code_line in func["code"].split("\n"):
            if not code_line.strip():
                continue

            # Remove line number prefix "23: " → "actual code"
            if ":" in code_line:
                # Extract line number
                colon_pos = code_line.index(":")
                line_num_str = code_line[:colon_pos].strip()

                # Skip lines before Valgrind line
                if line_num_str.isdigit():
                    line_num = int(line_num_str)
                    if line_num < valgrind_line:
                        continue  # Skip this line

                actual_code = code_line[colon_pos + 1 :]
                lines.append(actual_code)

        result.append(
            {
                "function": func["function"],
                "lines": lines,
                "start_line": valgrind_line,
                "file": func.get("file", "unknown"),
            }
        )

    return result


# =============================================================================
# MAIN ALGORITHM
# =============================================================================


def find_root_cause(
    extracted_functions: list[ProcessedFunction],
) -> Optional[RootCauseInfo]:
    """
    Main algorithm to find root cause of a memory leak.

    Args:
        extracted_functions: List of dicts with keys:
            - 'function': function name
            - 'lines': list of code lines (starting from Valgrind-indicated line)
            - 'start_line': line number of first line
            - 'file': source file path (optional)

    Returns:
        RootCause with type (1, 2, or 3), line, function, file, and steps
    """
    tracking: dict[str, TrackingEntry] = {}
    current_func_index = 0
    steps: list[str] = []  # Step log for explanation

    # =========================================================================
    # STEP 1: INITIALIZATION (first line = malloc)
    # =========================================================================

    current_func = extracted_functions[0]
    first_line = current_func["lines"][0]
    current_file = current_func.get("file", "unknown")

    apply_init(first_line, tracking)

    # Log initial allocation
    root_key = list(tracking.keys())[0]
    target = tracking[root_key]["target"]
    steps.append(f"ALLOC: {target} in {current_func['function']}()")

    line_index = 1  # Start after malloc

    # =========================================================================
    # STEP 2: LINE BY LINE TRAVERSAL
    # =========================================================================

    while True:
        # Check if we finished current function
        if line_index >= len(current_func["lines"]):
            # If tracking non-empty, local variables are lost
            if tracking:
                steps.append(
                    f"END: {current_func['function']}() exits with unreleased memory"
                )

                return {
                    "leak_type": 2,
                    "line": "}",
                    "function": current_func["function"],
                    "file": current_file,
                    "steps": steps,
                }

            # Move to next function if available
            current_func_index += 1

            if current_func_index >= len(extracted_functions):
                # End of code, structure not empty → Type 1

                return {
                    "leak_type": 1,
                    "line": "end of program",
                    "function": current_func["function"],
                    "file": current_file,
                    "steps": steps,
                }

            current_func = extracted_functions[current_func_index]
            current_file = current_func.get("file", current_file)
            line_index = 1  # Skip first line (consumed by return)
            continue

        line = current_func["lines"][line_index]
        func_name = current_func["function"]

        # =====================================================================
        # Check if line concerns us and get operation type
        # =====================================================================

        found, root_key, found_segment, entry, operation = find_segment_in_line(
            line, tracking
        )

        if not found:
            line_index += 1
            continue

        # =====================================================================
        # Apply rule based on operation type
        # =====================================================================

        if operation == "return":
            # Get first line of next function (the call)
            next_func = extracted_functions[current_func_index + 1]
            caller_line = next_func["lines"][0]

            old_target = entry["target"]
            apply_return(line, tracking, caller_line)

            # Log the return
            new_root = list(tracking.keys())[0]
            new_target = tracking[new_root]["target"]
            steps.append(
                f"RETURN: {old_target} -> {new_target} in {next_func['function']}()"
            )

            # Move to next function, after the call line
            current_func_index += 1
            current_func = next_func
            current_file = current_func.get("file", current_file)
            line_index = 1  # Call line consumed by return
            continue

        if operation == "free":
            steps.append(f"FREE: {found_segment} in {func_name}()")

            root_cause = apply_free(
                line, found_segment, entry, root_key, tracking, func_name
            )

            if root_cause is not None:
                root_cause["file"] = current_file
                root_cause["steps"] = steps

                return root_cause

            line_index += 1
            continue

        if operation == "alias":
            new_name = extract_left_side(line)
            steps.append(f"ALIAS: {new_name} = {found_segment} in {func_name}()")

            apply_alias(line, found_segment, entry, tracking)

            line_index += 1
            continue

        if operation == "reassign":
            steps.append(f"REASSIGN: {found_segment} in {func_name}()")

            root_cause = apply_reassignment(root_key, tracking, line, func_name)

            if root_cause is not None:
                root_cause["file"] = current_file
                root_cause["steps"] = steps
                return root_cause

            line_index += 1
            continue

        # Unknown operation, skip
        line_index += 1

    return None


# =============================================================================
# TRACE-BASED ALGORITHM (GDB execution trace)
# =============================================================================


def find_root_cause_from_trace(
    trace: list[TraceStep],
    free_events: list[FreeEvent],
) -> Optional[RootCauseInfo]:
    """
    Find root cause of a memory leak using a real GDB execution trace.

    This function applies the same pointer-tracking logic as
    ``find_root_cause`` but operates on the **actual** sequence of lines
    executed at runtime.  This allows correct handling of loops,
    conditional branches, and indirect ``free`` calls that the static
    algorithm cannot follow.

    Args:
        trace:        Ordered list of executed source lines, each carrying
                      ``file``, ``line``, ``function``, and ``code``.
        free_events:  List of ``free()`` calls on the tracked address,
                      detected via GDB conditional breakpoint.

    Returns:
        A ``RootCauseInfo`` dict, or ``None`` if no leak could be diagnosed.
    """
    if not trace:
        return None

    tracking: dict[str, TrackingEntry] = {}
    steps: list[str] = []

    # Index of the free-event list (consumed in order).
    free_idx = 0

    # Set when a structure traversal (X = X->field) moves the iterator
    # past the tracked node.  The memory is still in the structure but
    # we can no longer follow it via the iterator variable.  Instead of
    # concluding Type 2, we continue to the end of the trace.
    traversal_cleared = False
    structure_func: Optional[str] = None  # function where traversal happened

    # Track function transitions to detect RETURN operations.
    prev_function: Optional[str] = None
    # When we detect a return (function changed from callee to caller),
    # the current trace entry is the first line back in the caller,
    # which is typically the call/assignment line.
    pending_return_var: Optional[str] = None

    # Maps each tracking root to the function where it was last
    # assigned.  Used to detect scope-exit: when a function returns
    # without passing the tracked pointer back, any root local to
    # that function is lost → Type 2.
    root_function: dict[str, str] = {}

    i = 0
    while i < len(trace):
        step = trace[i]
        code = step["code"]
        func = step["function"]
        current_file = step["file"]

        # =================================================================
        # HANDLE PENDING RETURN (previous step was ``return x;``)
        # =================================================================
        if pending_return_var is not None and func != prev_function:
            # We just returned from a callee into a caller.
            # The current line should be the call/assignment site.
            _apply_return_mapping(
                pending_return_var,
                code,
                tracking,
                steps,
                func,
                callee_func=prev_function,
            )
            pending_return_var = None
            # Update root_function for the new root in the caller.
            for rk in list(tracking.keys()):
                if rk not in root_function or root_function[rk] == prev_function:
                    root_function[rk] = func
            # Skip this line — it was consumed by the return mapping.
            # Without this, the assignment would be re-analysed as a
            # REASSIGN, incorrectly breaking the tracking.
            prev_function = func
            i += 1
            continue

        # =================================================================
        # SCOPE-EXIT DETECTION
        # =================================================================
        # When a function *exits* (detected by ``}`` in the previous
        # trace step followed by a function transition) without passing
        # the tracked pointer back (no pending return), any tracked root
        # local to the exiting function is lost → Type 2.
        #
        # We require the previous step to be a closing brace to
        # distinguish true function exits from function *calls* (where
        # the function transitions forward into a callee).
        if (
            prev_function is not None
            and func != prev_function
            and pending_return_var is None
            and tracking
            and i > 0
            and trace[i - 1]["code"].strip() == "}"
        ):
            lost_roots = [
                rk for rk in tracking if root_function.get(rk) == prev_function
            ]
            if lost_roots:
                # Remove roots local to the exiting function.
                for rk in lost_roots:
                    del tracking[rk]
                    root_function.pop(rk, None)

                # If other roots survive in the caller, the pointer
                # is still reachable — continue tracking.
                if tracking:
                    prev_function = func
                    i += 1
                    continue

                # No surviving roots → pointer truly lost.
                rk = lost_roots[0]
                steps.append(f"SCOPE_EXIT: {rk} lost at end of {prev_function}()")
                prev_step = trace[i - 1]
                return {
                    "leak_type": 2,
                    "line": "}",
                    "line_number": prev_step["line"],
                    "function": prev_function,
                    "file": prev_step["file"],
                    "steps": steps,
                }

        # =================================================================
        # STEP 1: INITIALISATION (first malloc in the trace)
        # =================================================================
        # Only initialise once — prevent re-tracking after the original
        # tracking has been cleared (e.g. by a traversal or free).
        if not tracking and not traversal_cleared and is_malloc(code):
            apply_init(code, tracking)
            root_key = list(tracking.keys())[0]
            target = tracking[root_key]["target"]
            root_function[root_key] = func
            steps.append(f"ALLOC: {target} in {func}()")
            prev_function = func
            i += 1
            continue

        # Skip lines before allocation is initialised.
        if not tracking and not traversal_cleared:
            prev_function = func
            i += 1
            continue

        # =================================================================
        # STRUCTURE RE-TRACKING (after traversal cleared tracking)
        # =================================================================
        # When a linked-list traversal moved the iterator past the
        # tracked node, tracking was emptied.  The memory is still
        # inside the data structure.  Watch for the function to
        # return the structure root so we can resume tracking.
        if traversal_cleared and not tracking:
            stripped = code.strip()
            if (
                func == structure_func
                and stripped.startswith("return")
                and stripped != "return ;"
                and stripped != "return;"
            ):
                ret_val = extract_return_value(code)
                if ret_val:
                    tracking[ret_val] = {
                        "target": ret_val,
                        "segments": build_segments(ret_val),
                        "origin": None,
                        "in_structure": True,
                    }
                    root_function[ret_val] = func
                    pending_return_var = ret_val
                    traversal_cleared = False
                    structure_func = None
                    steps.append(
                        f"STRUCTURE: tracked memory returned as"
                        f" part of {ret_val} from {func}()"
                    )
                    prev_function = func
                    i += 1
                    continue
            prev_function = func
            i += 1
            continue

        # =================================================================
        # PARAMETER MAPPING (pointer passed as function argument)
        # =================================================================
        # When the GDB tracer detects that a callee's parameter holds
        # the tracked address, it annotates the first trace step in
        # the callee with ``param_mapping``.  We create an alias so
        # the tracker can follow the pointer under its new name.
        param_mapping = step.get("param_mapping")
        if param_mapping and tracking:
            for param_name in param_mapping:
                for rk, ent in list(tracking.items()):
                    if param_name not in tracking:
                        # Transfer suffix like RETURN does: if we
                        # track "data[i]" and param is "arr", the
                        # new target becomes "arr[i]".
                        old_root = extract_root(ent["target"])
                        suffix = ent["target"][len(old_root) :]
                        new_target = param_name + suffix

                        new_entry: TrackingEntry = {
                            "target": new_target,
                            "segments": build_segments(new_target),
                            "origin": ent["target"],
                        }
                        # Propagate structure flag through param mapping.
                        if ent.get("in_structure"):
                            new_entry["in_structure"] = True
                        tracking[param_name] = new_entry
                        root_function[param_name] = func
                        steps.append(
                            f"PARAM: {ent['target']} passed as {new_target} to {func}()"
                        )
                        break

        # =================================================================
        # CHECK FOR INDIRECT FREE (from GDB free-event list)
        # =================================================================
        if free_idx < len(free_events):
            fe = free_events[free_idx]
            if fe["caller_function"] == func and fe["caller_line"] == step["line"]:
                free_idx += 1
                steps.append(f"FREE (indirect): in {func}()")

                root_cause = _handle_free_event(tracking, func, code, steps)
                if root_cause is not None:
                    root_cause["file"] = current_file
                    return root_cause

                prev_function = func
                i += 1
                continue

        # =================================================================
        # DETECT OPERATIONS ON THE TRACKED POINTER
        # =================================================================
        found, root_key, found_segment, entry, operation = find_segment_in_line(
            code, tracking
        )

        if not found:
            prev_function = func
            i += 1
            continue

        # -----------------------------------------------------------------
        # RETURN
        # -----------------------------------------------------------------
        if operation == "return":
            pending_return_var = extract_return_value(code)
            prev_function = func
            i += 1
            continue

        # -----------------------------------------------------------------
        # FREE (direct ``free(ptr);`` in source)
        # -----------------------------------------------------------------
        if operation == "free":
            # Structure root freed — tracked memory is still inside
            # the data structure.  Remove this root and continue; if
            # no other root survives, the scope-exit or end-of-trace
            # fallback will catch it.
            if entry.get("in_structure"):
                steps.append(
                    f"FREE: {found_segment} in {func}()"
                    f" (structure root freed, tracked memory still inside)"
                )
                return {
                    "leak_type": 3,
                    "line": code.strip(),
                    "line_number": step.get("line"),
                    "function": func,
                    "file": current_file,
                    "steps": steps,
                }

            # When freeing an indexed expression that matches the target
            # symbolically (e.g. ``free(arr[i])`` with target ``arr[i]``),
            # we cannot determine whether this specific loop iteration
            # frees OUR tracked allocation.  Skip and keep tracking so
            # that a later container free (``free(arr)``) can be detected
            # as Type 3.
            if "[" in found_segment and found_segment == entry["target"]:
                prev_function = func
                i += 1
                continue

            # Detect if this free targets a container (Type 3).
            free_arg = extract_free_argument(code)
            if entry["target"].startswith(free_arg + "->") or entry[
                "target"
            ].startswith(free_arg + "["):
                steps.append(
                    f"FREE: {found_segment} in {func}()"
                    f" (container freed, but {entry['target']} still inside)"
                )
            else:
                steps.append(f"FREE: {found_segment} in {func}()")

            root_cause = apply_free(
                code,
                found_segment,
                entry,
                root_key,
                tracking,
                func,
            )
            if root_cause is not None:
                root_cause["file"] = current_file
                root_cause["steps"] = steps
                return root_cause

            prev_function = func
            i += 1
            continue

        # -----------------------------------------------------------------
        # ALIAS
        # -----------------------------------------------------------------
        if operation == "alias":
            new_name = extract_left_side(code)
            steps.append(f"ALIAS: {new_name} = {found_segment} in {func}()")
            apply_alias(code, found_segment, entry, tracking)
            # Track scope of any new root created by the alias.
            new_root = extract_root(new_name)
            if new_root in tracking:
                root_function[new_root] = func
            prev_function = func
            i += 1
            continue

        # -----------------------------------------------------------------
        # REASSIGNMENT
        # -----------------------------------------------------------------
        if operation == "reassign":
            # Detect linked list traversal: ``X = X->field`` where
            # ``X->field`` is a prefix of the tracking target.  In that
            # case the pointer is not lost — it just moved one step
            # down the chain.  Collapse the path instead of removing it.
            if "=" in code:
                right = extract_right_side(code)
                target = entry["target"]
                # Structure traversal: X = X->field
                if right.startswith(found_segment + "->"):
                    if target.startswith(right):
                        # Target extends past right — collapse the path.
                        suffix = target[len(right) :]
                        new_target = found_segment + suffix
                        entry["target"] = new_target
                        entry["segments"] = build_segments(new_target)
                        steps.append(f"TRAVERSE: {target} -> {new_target} in {func}()")
                    else:
                        # Iterator moved past the tracked node.  The
                        # memory is still in the data structure but we
                        # can no longer follow it via this variable.
                        steps.append(
                            f"TRAVERSE: iterator moved past tracked memory in {func}()"
                        )
                        del tracking[root_key]
                        traversal_cleared = True
                        structure_func = func
                    prev_function = func
                    i += 1
                    continue

            # Address-integrity check: if the GDB annotation confirms
            # that the tracked address is still in place after this line
            # executed, the apparent reassignment did not overwrite our
            # tracked pointer (e.g. a loop assigning to a different
            # array index).  Skip it.
            if step.get("addr_intact") is True:
                prev_function = func
                i += 1
                continue

            right_val = extract_right_side(code) if "=" in code else "?"
            steps.append(f"REASSIGN: {found_segment} = {right_val} in {func}()")

            root_cause = apply_reassignment(root_key, tracking, code, func)
            if root_cause is not None:
                root_cause["file"] = current_file
                root_cause["steps"] = steps
                return root_cause

            prev_function = func
            i += 1
            continue

        # Unknown — skip.
        prev_function = func
        i += 1

    # =====================================================================
    # END OF TRACE — memory still tracked → Type 1 (never freed)
    # =====================================================================
    if tracking or traversal_cleared:
        last = trace[-1] if trace else {"function": "?", "file": "?"}
        steps.append("END: program finishes with unreleased memory")
        return {
            "leak_type": 1,
            "line": "end of program",
            "function": last["function"],
            "file": last["file"],
            "steps": steps,
        }

    return None


# =============================================================================
# TRACE-SPECIFIC HELPERS
# =============================================================================


def _apply_return_mapping(
    returned_var: str,
    caller_line: str,
    tracking: dict[str, TrackingEntry],
    steps: list[str],
    caller_func: str,
    callee_func: str = "",
) -> None:
    """
    Map a returned variable to the receiver in the calling function.

    When the trace shows a function transition after a ``return x;``
    statement, the current line in the caller is typically the
    call/assignment site (e.g. ``head->next = create_node();``).  This
    helper rewrites the tracking entry from the callee's variable to
    the caller's variable.

    Args:
        returned_var:  Variable name from the ``return`` statement.
        caller_line:   Source code of the first line in the caller after
                       the call returns.
        tracking:      Live tracking dictionary (modified in place).
        steps:         Step log (appended to).
        caller_func:   Name of the caller function.
    """
    old_root = extract_root(returned_var)

    if old_root not in tracking:
        return

    # Only apply the mapping if the caller line looks like an assignment.
    if "=" not in caller_line:
        return

    old_entry = tracking[old_root]
    old_target = old_entry["target"]

    receiver = extract_left_side(caller_line)
    new_root = extract_root(receiver)

    suffix = old_target.replace(returned_var, "", 1)
    new_target = receiver + suffix

    new_entry: TrackingEntry = {
        "target": new_target,
        "segments": build_segments(new_target),
        "origin": None,
    }
    # Propagate structure flag through return mapping.
    if old_entry.get("in_structure"):
        new_entry["in_structure"] = True

    del tracking[old_root]
    tracking[new_root] = new_entry

    steps.append(
        f"RETURN: {callee_func}() returns {old_target},"
        f" stored in {new_target} in {caller_func}()"
    )


def _handle_free_event(
    tracking: dict[str, TrackingEntry],
    func: str,
    code: str,
    steps: list[str],
) -> Optional[RootCauseInfo]:
    """
    Process an indirect free event detected by GDB.

    An indirect free is a ``free()`` that happens inside a function not
    directly visible in the source line (e.g. a cleanup helper).  We know
    that the tracked address was freed, but we need to determine whether
    this creates a Type 3 (container freed before content) or simply
    clears the tracking.

    Args:
        tracking:  Live tracking dictionary (modified in place).
        func:      Function where the free was triggered.
        code:      Source line in the relevant caller.
        steps:     Step log.

    Returns:
        A ``RootCauseInfo`` if this free causes a leak, ``None`` otherwise.
    """
    # For indirect frees, we clear all tracking (the memory is freed).
    tracking.clear()
    return None
