#!/usr/bin/env python3
"""
Flag Guessing Game
-------------------
Renders country flags as colour ASCII art in the terminal and asks the
player to guess the country. The chosen "mode" controls which character
is used to draw each pixel — solid blocks are easy to read, sparse
characters (like a period) are much harder to make out.

Modes:
    easy / medium / hard / whyyyy? / inverted   — same as before
    progressive — flag starts 5% revealed and gradually uncovers more
                  every few seconds. The more that's shown when you
                  answer, the less the correct guess is worth.

Requires:
    pip install cairosvg pillow rapidfuzz

Expects, relative to this script:
    flag_list_json/sovereign_plus_few_flags.json   -> {"XX": "Country Name", ...}
    4x3_flags/XX.svg                                -> flag artwork per code
    flag_list_json/alt_names.json (optional)        -> {"XX": ["Alt name", ...]}

Note on Progressive Reveal:
    There's no curses UI here, so the timed reveal is implemented by
    running input() in a background thread while the main thread
    redraws the screen every tick. This means if you're mid-typing
    exactly when a redraw fires, your in-progress line will vanish
    from view for a moment (your keystrokes aren't lost — the terminal
    still buffers them — it just isn't re-drawn until you type more or
    hit Enter). Good enough for a hobby project; a curses rewrite would
    fix this properly if it ever becomes annoying.
"""

import json
import os
import random
import string
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from rapidfuzz import fuzz
from rapidfuzz.distance import DamerauLevenshtein

import cairosvg
from PIL import Image

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
FLAG_LIST_PATH = BASE_DIR / "flag_list_json" / "sovereign_plus_few_flags.json"
FLAG_SVG_DIR = BASE_DIR / "4x3_flags"
ALT_NAMES_PATH = BASE_DIR / "flag_list_json" / "alt_names.json"


ASCII_WIDTH = 60

MODE_CHARS = {
    "easy": "█",
    "medium": "@",
    "hard": "+",
    "whyyyy": ".",
    "inverted": "█",
    "progressive": "█",
}

# Accept a few friendly ways of typing each mode
MODE_ALIASES = {
    "easy": "easy",
    "e": "easy",
    "1": "easy",
    "medium": "medium",
    "med": "medium",
    "m": "medium",
    "2": "medium",
    "hard": "hard",
    "h": "hard",
    "3": "hard",
    "whyyyy": "whyyyy",
    "whyyyy?": "whyyyy",
    "why": "whyyyy",
    "w": "whyyyy",
    "4": "whyyyy",
    "inverted": "inverted",
    "invert": "inverted",
    "i": "inverted",
    "5": "inverted",
    "progressive": "progressive",
    "prog": "progressive",
    "p": "progressive",
    "6": "progressive",
}

INVERTED_MODES = {"inverted"}
PROGRESSIVE_MODES = {"progressive"}

# Progressive reveal tuning
PROGRESSIVE_START_FRACTION = 0.05   # start at 5% of pixels shown
PROGRESSIVE_STEP = 0.05             # reveal +5% each tick
PROGRESSIVE_INTERVAL = 3.0          # seconds between reveal ticks
PROGRESSIVE_MIN_MULTIPLIER = 0.3    # score floor even at 100% revealed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def normalize(answer: str) -> str:
    """Lowercase, strip whitespace, and drop all punctuation for comparison."""
    answer = answer.lower()
    answer = answer.translate(str.maketrans("", "", string.punctuation))
    answer = "".join(answer.split())  # remove all whitespace
    return answer


def load_flags() -> dict:
    with open(FLAG_LIST_PATH, "r") as f:
        return json.load(f)


def load_alt_names() -> dict:
    if not ALT_NAMES_PATH.exists():
        return {}
    with open(ALT_NAMES_PATH, "r") as f:
        return json.load(f)


def accepted_answers(code: str, country: str, alt_names: dict) -> set:
    answers = {normalize(country)}
    for alt in alt_names.get(code, []):
        answers.add(normalize(alt))
    return answers


def format_avg_time(guess_times: list) -> str:
    """Return a short 'Avg: X.XXXs' string, or a placeholder if no guesses yet."""
    if not guess_times:
        return "Avg: —"
    avg = sum(guess_times) / len(guess_times)
    return f"Avg: {avg:.3f}s"


MIN_LENGTH_FLOOR = 8  # names shorter than this won't be over-penalized per edit

def score_guess(guess: str, valid_answers: set) -> tuple[float, str]:
    norm_guess = normalize(guess)

    if norm_guess in valid_answers:
        return 1.0, norm_guess

    best_similarity = 0
    best_answer = ""
    for ans in valid_answers:
        distance = DamerauLevenshtein.distance(norm_guess, ans)
        denom = max(len(ans), len(norm_guess), MIN_LENGTH_FLOOR)
        similarity = (1 - distance / denom) * 100
        if similarity > best_similarity:
            best_similarity = similarity
            best_answer = ans

    if best_similarity > 80:
        points = min(1.0, (best_similarity - 80) / 20)
        return round(points, 2), best_answer

    return 0.0, best_answer


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def flag_to_grid(svg_path: Path, width: int = ASCII_WIDTH):
    """Rasterize an SVG flag into a 2D grid of (r, g, b, a) pixel tuples."""
    png = cairosvg.svg2png(url=str(svg_path))
    img = Image.open(BytesIO(png)).convert("RGBA")
    aspect = img.height / img.width
    height = max(1, int(width * aspect * 0.5))
    img = img.resize((width, height))

    grid = []
    for y in range(height):
        row = []
        for x in range(width):
            row.append(img.getpixel((x, y)))
        grid.append(row)
    return grid


def grid_to_ascii(grid, char, invert: bool = False, revealed: set | None = None,
                   hidden_char: str = "·", hidden_rgb: tuple = (55, 55, 60)) -> str:
    """
    Render a pixel grid as colour ASCII art.

    If `revealed` is None, every pixel is drawn normally (used by the
    original modes). If `revealed` is a set of (x, y) coordinates, only
    those pixels get their true colour — everything else is drawn as a
    dim placeholder, used by Progressive Reveal.
    """
    out_lines = []
    height = len(grid)
    width = len(grid[0]) if height else 0

    for y in range(height):
        line_parts = []
        for x in range(width):
            r, g, b, a = grid[y][x]
            if a == 0:
                line_parts.append(" ")
                continue
            if revealed is not None and (x, y) not in revealed:
                hr, hg, hb = hidden_rgb
                line_parts.append(f"\033[38;2;{hr};{hg};{hb}m{hidden_char}\033[0m")
                continue
            if invert:
                r, g, b = 255 - r, 255 - g, 255 - b
            line_parts.append(f"\033[38;2;{r};{g};{b}m{char}\033[0m")
        out_lines.append("".join(line_parts))
    return "\n".join(out_lines)


def svg_to_ascii_colour(svg_path: Path, char: str, width: int = ASCII_WIDTH, invert: bool = False) -> str:
    """Kept for backwards compatibility — renders a flag fully revealed."""
    grid = flag_to_grid(svg_path, width)
    return grid_to_ascii(grid, char, invert=invert)


def choose_mode() -> str:
    prompt = (
        "Choose a mode:\n"
        "  [1] easy         (█)\n"
        "  [2] medium       (@)\n"
        "  [3] hard         (+)\n"
        "  [4] whyyyy?      (.)\n"
        "  [5] inverted     (colours flipped)\n"
        "  [6] progressive  (starts hidden, reveals over time — faster guesses score more)\n"
        "> "
    )
    while True:
        choice = input(prompt).strip().lower()
        if choice in MODE_ALIASES:
            return MODE_ALIASES[choice]
        print("Didn't catch that — try easy, medium, hard, whyyyy?, inverted, or progressive\n")


# ---------------------------------------------------------------------------
# Progressive Reveal
# ---------------------------------------------------------------------------

def progressive_multiplier(revealed_fraction: float,
                            start_fraction: float = PROGRESSIVE_START_FRACTION) -> float:
    """
    Score multiplier based on how much *extra* has been revealed since
    the starting fraction — not the raw revealed fraction. This means a
    guess made right at the start (still at start_fraction) scores full
    marks, and the multiplier only decays as more gets revealed beyond
    that baseline. Floors at PROGRESSIVE_MIN_MULTIPLIER once fully revealed.
    """
    if revealed_fraction <= start_fraction:
        return 1.0
    progress = (revealed_fraction - start_fraction) / (1.0 - start_fraction)
    raw = 1.0 - progress * (1.0 - PROGRESSIVE_MIN_MULTIPLIER)
    return max(PROGRESSIVE_MIN_MULTIPLIER, raw)


def _threaded_input(prompt: str, out: list, event: threading.Event):
    try:
        out.append(input(prompt))
    except EOFError:
        out.append("")
    event.set()


def progressive_reveal_and_guess(header_fn, grid, char,
                                  interval: float = PROGRESSIVE_INTERVAL,
                                  step: float = PROGRESSIVE_STEP,
                                  start_fraction: float = PROGRESSIVE_START_FRACTION):
    """
    Shows the flag with an increasing fraction of pixels revealed every
    `interval` seconds, while waiting for the player's guess in the
    background. Returns (guess_text, elapsed_seconds, revealed_fraction_at_guess).
    """
    height = len(grid)
    width = len(grid[0]) if height else 0
    all_coords = [(x, y) for y in range(height) for x in range(width)]
    random.shuffle(all_coords)
    total = len(all_coords)

    revealed_fraction = start_fraction

    result = []
    event = threading.Event()
    start_time = time.time()
    t = threading.Thread(target=_threaded_input, args=("Your guess: ", result, event), daemon=True)
    t.start()

    while True:
        revealed_count = int(total * revealed_fraction)
        revealed = set(all_coords[:revealed_count])

        clear_screen()
        print(header_fn())
        print(grid_to_ascii(grid, char, revealed=revealed))
        print(f"\nRevealed: {revealed_fraction * 100:.0f}%   "
              f"(the more that's shown, the less a correct guess is worth)")
        print("Your guess: ", end="", flush=True)

        if event.wait(interval):
            break
        if revealed_fraction < 1.0:
            revealed_fraction = min(1.0, revealed_fraction + step)

    t.join()
    elapsed = time.time() - start_time
    guess = result[0] if result else ""
    return guess, elapsed, revealed_fraction


# ---------------------------------------------------------------------------
# Main game loop
# ---------------------------------------------------------------------------

def play():
    clear_screen()
    mode = choose_mode()
    char = MODE_CHARS[mode]
    is_progressive = mode in PROGRESSIVE_MODES

    flags = load_flags()
    alt_names = load_alt_names()
    codes = list(flags.keys())
    random.shuffle(codes)

    score = 0.0
    total = len(codes)
    guess_times = []
    correct_guess_times = []

    for i, code in enumerate(codes, start=1):
        country = flags[code]
        svg_path = FLAG_SVG_DIR / f"{code}.svg"
        valid_answers = accepted_answers(code, country, alt_names)
        grid = flag_to_grid(svg_path, width=80)

        if is_progressive:
            def header(i=i, score=score, guess_times=guess_times):
                avg_str = format_avg_time(guess_times)
                return (f"Mode: {mode}   |   Flag {i} of {total}   |   "
                        f"Score: {score:.2f}   |   {avg_str}\n")

            guess, elapsed, revealed_fraction = progressive_reveal_and_guess(header, grid, char)
            raw_points, _ = score_guess(guess, valid_answers)
            points = round(raw_points * progressive_multiplier(revealed_fraction), 2)
        else:
            clear_screen()
            avg_str = format_avg_time(guess_times)
            print(f"Mode: {mode}   |   Flag {i} of {total}   |   "
                  f"Score: {score:.2f}   |   {avg_str}\n")
            print(grid_to_ascii(grid, char, invert=(mode in INVERTED_MODES)))
            print()

            start_time = time.time()
            guess = input("Your guess: ")
            elapsed = time.time() - start_time
            raw_points, _ = score_guess(guess, valid_answers)
            points = raw_points

        guess_times.append(elapsed)
        if raw_points == 1.0:
            correct_guess_times.append(elapsed)
        score += points

        if raw_points == 0:
            print(f"\n\033[31mNope — that was\033[0m {country}\033[31m.\033[0m")
        elif raw_points < 1.0:
            print(f"\n\033[33mClose! Spelling was a bit off (+{points:.2f}) — it was\033[0m "
                  f"{country}\033[33m.\033[0m")
        elif points < 1.0:
            print(f"\n\033[36mCorrect! But you let a lot of the flag show, so it's only "
                  f"worth +{points:.2f}\033[0m")
        else:
            print("\n\033[32mCorrect!\033[0m")
        print(f"(Answered in {elapsed:.3f}s)")

        input("\nPress Enter for the next flag...")

    clear_screen()
    print("=" * 40)
    print(f"  Final Score: {score:.2f} / {total}")
    print(f"  Mode: {mode}")
    if guess_times:
        avg = sum(guess_times) / len(guess_times)
        print(f"  Average time per guess: {avg:.3f}s")
        fastest_str = f"{min(correct_guess_times):.3f}s" if correct_guess_times else "—"
        print(f"  Fastest (correct): {fastest_str}   Slowest: {max(guess_times):.3f}s")
    print("=" * 40)


if __name__ == "__main__":
    try:
        play()
    except KeyboardInterrupt:
        print("\n\nGame interrupted. Bye!")
        sys.exit(0)