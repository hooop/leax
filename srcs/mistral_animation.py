#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
mistral_animation.py

Animated Mistral logo display with wave effects.
Plays for a fixed duration before transitioning to Leax.
"""

import time
import math
import random


# =========================
# PARAMETERS
# =========================
WIDTH = 18
HEIGHT = 14
SPEED = 0.05  # Time between each frame (seconds)
SPAWN_DELAY = 2  # Delay between wave spawns (frames)
CHAR_ON = "∎∎"
CHAR_OFF = "∎∎"
CHAR_LOGO = "██"

# Colors
RESET = "\033[0m"
LIGHT_PINK = "\033[38;5;160m"
RED = "\033[38;5;208m"
DARK_GREEN = "\033[38;5;202m"
BLACK_BLOCK = "\033[30m" + CHAR_LOGO + RESET  # Black block for logo

# Logo pattern (1 = pixel, 0 = empty)
LOGO = [
    [0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0],
    [0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0],
    [0, 0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 0, 0],
    [0, 0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 0, 0],
    [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0],
    [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0],
    [0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0],
    [0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0],
    [1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1],
    [1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1],
]

# Logo timing
LOGO_START_DELAY = 1.3  # Seconds before logo starts appearing
LOGO_DURATION = 0.5  # Seconds to display full logo


def _calculate_logo_pixels() -> set:
    """
    Calculate logo pixel positions centered on screen.

    Returns:
        Set of (x, y) coordinates where logo pixels should appear
    """
    logo_pixels = set()
    logo_offset_x = WIDTH // 2 - len(LOGO[0]) // 2
    logo_offset_y = HEIGHT // 2 - len(LOGO) // 2

    for y, row in enumerate(LOGO):
        for x, val in enumerate(row):
            if val == 1:
                logo_pixels.add((x + logo_offset_x, y + logo_offset_y))

    return logo_pixels


def play_mistral_animation(duration: float = 2.0) -> None:
    """
    Play the animated Mistral logo with wave effects.

    Args:
        duration: Total animation duration in seconds (default: 2.0)
    """
    # Initialize animation state
    center_x = WIDTH // 2
    center_y = HEIGHT // 2
    max_radius = max(center_x, center_y)

    logo_pixels = _calculate_logo_pixels()
    logo_pixels_shown = set()
    total_logo_pixels = len(logo_pixels)

    waves = []  # Active wave radiuses
    frame = 0
    start_time = time.time()

    # Main animation loop
    while time.time() - start_time < duration:
        # Clear screen and move cursor to top-left
        print("\033[H\033[J", end="")

        elapsed = time.time() - start_time

        # Spawn new wave periodically
        if frame % SPAWN_DELAY == 0:
            waves.append(0)

        # Create empty grid
        grid = [
            [LIGHT_PINK + CHAR_OFF + RESET for _ in range(WIDTH)] for _ in range(HEIGHT)
        ]

        # Draw active waves
        new_waves = []
        for radius in waves:
            if radius <= max_radius:
                for y in range(HEIGHT):
                    for x in range(WIDTH):
                        dx = x - center_x
                        dy = y - center_y

                        # Check if pixel is on current wave radius
                        if max(abs(dx), abs(dy)) == radius:
                            if (x, y) not in logo_pixels_shown:
                                # Apply sinusoidal wave effect
                                x_offset = int(math.cos(frame * 0.3 + dy) * 1.5)
                                y_offset = int(math.sin(frame * 0.3 + dx) * 1.5)

                                x_new = x + x_offset
                                y_new = y + y_offset

                                # Draw wave pixel if within bounds
                                if 0 <= x_new < WIDTH and 0 <= y_new < HEIGHT:
                                    grid[y_new][x_new] = DARK_GREEN + CHAR_ON + RESET

                new_waves.append(radius + 1)

        waves = new_waves

        # Progressively reveal logo after delay
        if elapsed > LOGO_START_DELAY:
            remaining_pixels = list(logo_pixels - logo_pixels_shown)

            if remaining_pixels:
                # Calculate pixels per frame to finish in LOGO_DURATION
                frames_remaining = LOGO_DURATION / SPEED
                pixels_per_frame = max(1, int(total_logo_pixels / frames_remaining))

                # Reveal random pixels
                for _ in range(min(pixels_per_frame, len(remaining_pixels))):
                    pixel = random.choice(remaining_pixels)
                    logo_pixels_shown.add(pixel)
                    remaining_pixels.remove(pixel)

        # Draw revealed logo pixels
        for lx, ly in logo_pixels_shown:
            grid[ly][lx] = BLACK_BLOCK

        # Display grid
        for row in grid:
            print("".join(row))

        # Hide cursor
        print("\033[?25l", end="")

        # Display subtitle
        print(RED + "Mistral AI Internship Application." + RESET)

        frame += 1
        time.sleep(SPEED)

    # Clean up: clear screen and show cursor
    print("\033[H\033[J", end="")
    print("\033[?25h", end="", flush=True)


if __name__ == "__main__":
    """Test the animation standalone."""
    play_mistral_animation(duration=2.0)
