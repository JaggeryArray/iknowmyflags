#!/usr/bin/env python3
"""
Flag Guessing Game
-------------------
Renders country flags as colour ASCII art in the terminal and asks the
player to guess the country. The chosen "mode" controls which character
is used to draw each pixel — solid blocks are easy to read, sparse
characters (like a period) are much harder to make out.

Requires:
    pip install cairosvg pillow

Expects, relative to this script:
    flag_list_json/sovereign_plus_few_flags.json   -> {"XX": "Country Name", ...}
    4x3_flags/XX.svg                                -> flag artwork per code
"""

import json
import os
import random
import string
import sys
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
}

INVERTED_MODES = {"inverted"}


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


def svg_to_ascii_colour(svg_path: Path, char: str, width: int = ASCII_WIDTH, invert: bool = False) -> str:
    png = cairosvg.svg2png(url=str(svg_path))
    img = Image.open(BytesIO(png)).convert("RGBA")
    aspect = img.height / img.width
    height = max(1, int(width * aspect * 0.5))
    img = img.resize((width, height))

    out = []
    for y in range(img.height):
        line = []
        for x in range(img.width):
            r, g, b, a = img.getpixel((x, y))
            if a == 0:
                line.append(" ")
                continue
            if invert:
                r, g, b = 255 - r, 255 - g, 255 - b
            line.append(f"\033[38;2;{r};{g};{b}m{char}\033[0m")
        out.append("".join(line))
    return "\n".join(out)


def choose_mode() -> str:
    prompt = (
        "Choose a mode:\n"
        "  [1] easy      (█)\n"
        "  [2] medium    (@)\n"
        "  [3] hard      (+)\n"
        "  [4] whyyyy?   (.)\n"
        "  [5] inverted  (colours flipped)\n"
        "> "
    )
    while True:
        choice = input(prompt).strip().lower()
        if choice in MODE_ALIASES:
            return MODE_ALIASES[choice]
        print("Didn't catch that — try easy, medium, hard, whyyyy?, or inverted\n")


# ---------------------------------------------------------------------------
# Main game loop
# ---------------------------------------------------------------------------

def play():
    clear_screen()
    mode = choose_mode()
    char = MODE_CHARS[mode]

    flags = load_flags()
    alt_names = load_alt_names()
    codes = list(flags.keys())
    random.shuffle(codes)

    score = 0.0
    total = len(codes)

    for i, code in enumerate(codes, start=1):
        clear_screen()
        country = flags[code]
        svg_path = FLAG_SVG_DIR / f"{code}.svg"
        valid_answers = accepted_answers(code, country, alt_names)

        print(f"Mode: {mode}   |   Flag {i} of {total}   |   Score: {score:.2f}\n")
        print(svg_to_ascii_colour(svg_path, char, width=90, invert=(mode in INVERTED_MODES)))
        print()

        guess = input("Your guess: ")
        points, _ = score_guess(guess, valid_answers)
        score += points

        if points == 1.0:
            print("\n\033[32mCorrect!\033[0m")
        elif points > 0:
            print(f"\n\033[33mClose! (+{points:.2f}) — it was\033[0m {country}\033[33m.\033[0m")
        else:
            print(f"\n\033[31mNope — that was\033[0m {country}\033[31m.\033[0m")

        input("\nPress Enter for the next flag...")

    clear_screen()
    print("=" * 40)
    print(f"  Final Score: {score:.2f} / {total}")
    print(f"  Mode: {mode}")
    print("=" * 40)


if __name__ == "__main__":
    try:
        play()
    except KeyboardInterrupt:
        print("\n\nGame interrupted. Bye!")
        sys.exit(0)