"""
welcome.py

Module responsible for displaying the welcome screen with logo,
progress spinners, and summary before starting leak analysis.
"""

import os
import sys
import threading
import time

from colors import RESET, GREEN, DARK_GREEN, LIGHT_YELLOW, DARK_YELLOW, LIGHT_PINK
from type_defs import ParsedValgrindReport

# Global flags for spinner control
_spinner_active = False
_block_spinner_active = False


def clear_screen() -> None:
    """Clear the terminal screen."""

    os.system("clear")


def display_logo() -> None:
    """Display the Leax ASCII logo with cascading wave animation."""

    logo_lines = [
        "██    ██████  ██████  ██  ██",
        "██    ██▄▄    ██▄▄██    ██",
        "████  ██████  ██▀▀██  ██  ██",
    ]

    # Animation parameters
    START_ROW = 1
    LINE_STAGGER = 4  # Characters delay before next line starts
    STEP_DELAY = 0.025  # Seconds between each animation step

    # Parse non-space characters per line: list of (col, char)
    line_chars = []
    for line in logo_lines:
        chars = [(col, ch) for col, ch in enumerate(line) if ch != " "]
        line_chars.append(chars)

    max_len = max(len(chars) for chars in line_chars)
    total_steps = max_len + LINE_STAGGER * (len(logo_lines) - 1)

    for step in range(total_steps):
        for line_idx, chars in enumerate(line_chars):
            char_idx = step - line_idx * LINE_STAGGER

            if char_idx < 0 or char_idx >= len(chars):
                continue

            col, ch = chars[char_idx]

            # Pink cursor on leading edge
            print(
                f"\033[{START_ROW + line_idx};{col + 1}H{LIGHT_PINK}{ch}{RESET}",
                end="",
                flush=True,
            )

            # Turn previous char green
            if char_idx > 0:
                prev_col, prev_ch = chars[char_idx - 1]
                print(
                    f"\033[{START_ROW + line_idx};{prev_col + 1}H{DARK_GREEN}{prev_ch}{RESET}",
                    end="",
                    flush=True,
                )

        time.sleep(STEP_DELAY)

    # Final: turn last char of each line green
    for line_idx, chars in enumerate(line_chars):
        if chars:
            col, ch = chars[-1]
            print(
                f"\033[{START_ROW + line_idx};{col + 1}H{DARK_GREEN}{ch}{RESET}",
                end="",
                flush=True,
            )

    # Position cursor after logo
    print(f"\033[{len(logo_lines) + START_ROW};1H")

    print(GREEN + "Valgrind Error Exlorer" + RESET)
    print()


def _spinner_animation(message: str) -> None:
    """Thread function that displays the animated spinner."""

    spinner = ["◐", "◓", "◑", "◒"]
    colors = [LIGHT_PINK, DARK_GREEN]
    i = 0
    while _spinner_active:
        color = colors[i % len(colors)]
        symbol = spinner[i % len(spinner)]
        sys.stdout.write(f"\r{color}{symbol}{RESET} {message}")
        sys.stdout.flush()
        time.sleep(0.1)
        i += 1


def start_spinner(message: str) -> threading.Thread:
    """
    Start an animated spinner with a message.

    Args:
        message: The message to display next to the spinner

    Returns:
        threading.Thread: The spinner thread
    """

    global _spinner_active
    _spinner_active = True
    thread = threading.Thread(target=_spinner_animation, args=(message,))
    thread.daemon = True
    thread.start()
    return thread


def stop_spinner(thread: threading.Thread, message: str) -> None:
    """
    Stop the spinner and display a success checkmark.

    Args:
        thread: The spinner thread to stop
        message: The success message to display
    """

    global _spinner_active
    _spinner_active = False
    thread.join()
    sys.stdout.write(f"\r{GREEN}✓{RESET} {message}\n")
    sys.stdout.flush()


def _block_spinner_animation(message: str) -> None:
    """
    Thread function that reveals/hides text with block animation.

    Args:
        message: The message to animate.
    """

    colors = [LIGHT_PINK, DARK_GREEN]

    length = len(message)

    pos_counter = 0
    color_counter = 0

    pos_speed = 0.25  # Larger = slower scrolling
    color_speed = 3  # Larger = slower blinking

    tick = 0

    while _block_spinner_active:
        phase = (pos_counter // length) % 2
        pos = pos_counter % length
        color = colors[color_counter % 2]

        if phase == 0:
            text = f"  {message[: pos + 1]}{color}{'▉' * (length - pos - 1)}{RESET}"
        else:
            text = f"  {color}{'▉' * (pos + 1)}{RESET}{message[pos + 1 :]}"

        sys.stdout.write(f"\r{text}")
        sys.stdout.flush()

        tick += 1
        if tick % color_speed == 0:
            color_counter += 1
        if tick % pos_speed == 0:
            pos_counter += 1

        time.sleep(0.02)


def start_block_spinner(message: str) -> threading.Thread:
    """
    Start an animated block spinner with a message.

    Args:
        message: The message to display with the animation.

    Returns:
        The spinner thread.
    """

    global _block_spinner_active
    _block_spinner_active = True
    thread = threading.Thread(target=_block_spinner_animation, args=(message,))
    thread.daemon = True
    thread.start()
    return thread


def stop_block_spinner(thread: threading.Thread, message: str) -> None:
    """
    Stop the block spinner and display a success checkmark.

    Args:
        thread: The spinner thread to stop.
        message: The success message to display.
    """

    global _block_spinner_active
    _block_spinner_active = False
    thread.join()
    sys.stdout.write(f"\r{GREEN}✓{RESET} {message}\n")
    sys.stdout.flush()


def display_summary(parsed_data: ParsedValgrindReport) -> None:
    """
    Display the Valgrind report summary.

    Args:
        parsed_data: Parsed Valgrind report with summary and leaks.
    """

    print()
    print(GREEN + "• Valgrind Report Summary :" + RESET)
    print()

    summary = parsed_data.get("summary", {})
    num_leaks = len(parsed_data.get("leaks", []))

    # Larger = slower blinking
    leak_word = "memory leak detected" if num_leaks == 1 else "memory leaks detected"

    # Values for alignment
    def_bytes = f"{summary.get('definitely_lost', 0)} bytes"
    ind_bytes = f"{summary.get('indirectly_lost', 0)} bytes"
    total_bytes = f"{summary.get('total_leaked', 0)} bytes"

    # Max length of byte values
    max_bytes_len = max(len(def_bytes), len(ind_bytes), len(total_bytes))

    # Build lines
    line1 = f"{num_leaks} {leak_word}"
    line2 = f"   Definitely lost : {def_bytes:>{max_bytes_len}}"
    line3 = f"   Indirectly lost : {ind_bytes:>{max_bytes_len}}"
    line4 = f" ‣ Total : {total_bytes:>{max_bytes_len}}"

    lines = [line1, line2, line3, line4]

    # Find max length
    max_length = max(len(line) for line in lines)
    separator = "-" * max_length

    # Display
    print(LIGHT_YELLOW + separator + RESET)
    print(DARK_YELLOW + lines[0] + RESET)
    print(LIGHT_YELLOW + separator + RESET)
    print(LIGHT_YELLOW + lines[1] + RESET)
    print(LIGHT_YELLOW + separator + RESET)
    print(LIGHT_YELLOW + lines[2] + RESET)
    print(LIGHT_YELLOW + separator + RESET)
    print(DARK_YELLOW + lines[3] + RESET)
    print()
