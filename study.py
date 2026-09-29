"""Pure flashcard session state, independent of Tkinter and AI."""

import math
import random
from uuid import uuid4


def parse_session_size(selection, custom=""):
    """Translate the Study control into a card count or all cards."""
    if selection == "All":
        return "all"
    if selection in ("10", "20"):
        return int(selection)
    if selection != "Custom":
        raise ValueError("Choose a session size.")
    value = custom.strip()
    if not value.isascii() or not value.isdecimal():
        raise ValueError("Enter a positive whole number for the custom session size.")
    try:
        count = int(value)
    except ValueError as exc:
        raise ValueError("Enter a valid whole number for the custom session size.") from exc
    if count < 1:
        raise ValueError("Custom session size must be at least 1.")
    return count


def recall_estimate(entry, recent_first_round=()):
    """Estimate recall with a neutral prior and extra weight for recent sessions."""
    attempts = entry["study_attempts"]
    remembered = entry["remembered_count"]
    lifetime = (remembered + 2) / (attempts + 4)
    recent = tuple(recent_first_round)[:5]
    if not recent:
        return lifetime
    recent_rate = (sum(recent) + 2) / (len(recent) + 4)
    return 0.4 * lifetime + 0.6 * recent_rate


def recall_label(entry, recent_first_round=()):
    if entry["study_attempts"] == 0:
        return "New"
    estimate = recall_estimate(entry, recent_first_round)
    if estimate < 0.45:
        return "Needs practice"
    if estimate >= 0.8:
        return "Strong"
    return "Building"


def selection_weight(entry, recent_first_round=()):
    """Keep every card eligible while favoring lower estimated recall."""
    return 1 + 3 * (1 - recall_estimate(entry, recent_first_round))


def choose_cards(entries, size="all", *, rng=None, recent_outcomes=None):
    """Draw distinct cards with probabilities proportional to their weights."""
    pool = list(entries)
    count = len(pool) if size == "all" else min(int(size), len(pool))
    if count < 0:
        raise ValueError("Session size cannot be negative.")
    random_source = rng or random
    recent_outcomes = recent_outcomes or {}
    ranked = []
    for entry in pool:
        weight = selection_weight(entry, recent_outcomes.get(entry["id"], ()))
        draw = max(random_source.random(), 1e-15)
        ranked.append((-math.log(draw) / weight, entry))
    ranked.sort(key=lambda item: item[0])
    return [entry for _, entry in ranked[:count]]


class StudySession:
    def __init__(self, entries, size="all", rng=None, recent_outcomes=None):
        self.session_id = str(uuid4())
        self.cards = choose_cards(entries, size, rng=rng, recent_outcomes=recent_outcomes)
        self.round_total = len(self.cards)
        self.index = 0
        self.reveal_stage = 0
        self.again_cards = []
        self.remembered = 0
        self.again = 0
        self.round_number = 1

    @property
    def current(self):
        return self.cards[self.index] if self.index < len(self.cards) else None

    @property
    def revealed(self):
        return self.reveal_stage == 2

    def reveal(self):
        if self.current is None or self.revealed:
            return 0
        self.reveal_stage += 1
        return self.reveal_stage

    def answer(self, remembered):
        if self.current is None or not self.revealed:
            raise ValueError("Reveal the full answer before rating this card.")
        entry = self.current
        if remembered:
            self.remembered += 1
        else:
            self.again += 1
            self.again_cards.append(entry)
        self.index += 1
        self.reveal_stage = 0
        return entry["id"]

    def next_round(self):
        if self.current is not None or not self.again_cards:
            return False
        self.cards = self.again_cards
        self.again_cards = []
        self.round_total = len(self.cards)
        self.index = 0
        self.reveal_stage = 0
        self.round_number += 1
        return True
