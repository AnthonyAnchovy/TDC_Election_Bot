"""Drinking-game mode: a rule fires when something happens on the desk (no Discord here).

Off unless a server turns it on with /drinkinggame. The rules are in the pack's drinking.json if it has one, else the
ones below; each is a list of lines for an event, and one is picked the way flavor lines are (no repeats until the
rest have had a turn). Events:
  flip      a seat changes hands                 upset    the ratings favored someone else
  control   a chamber is called                  retract  the desk takes a call back
  beaten    an outlet called it before the desk  first    the desk called it before every outlet, by {lead}
  bot       the desk's own bot called it before the desk
Lines can use {winner}, {seat}, {place}, {outlet}, {lead}. Water counts.
"""
from __future__ import annotations

import random

import flavor as FL
import packs

SETTING = "drinking_game"
RULES = {
    "flip": ["🍺 A seat changed hands. Everyone who picked the loser drinks.", "🍺 Flip. Drink if you didn't see {seat} coming."],
    "upset": ["🍺🍺 The ratings were wrong about {seat}. Two drinks, and one for the raters.",
              "🍺🍺 Upset. Anyone who called it in advance hands out two drinks."],
    "control": ["🍾 {seat} is called. Everybody toasts, whoever they voted for."],
    "retract": ["🥃 The desk retracted a call. Everyone who voted for it finishes their drink.",
                "🥃 Retraction. The first person to have voted for that call drinks twice."],
    "beaten": ["🍺 {outlet} beat the desk to {seat}. The desk drinks.", "🍺 Scooped by {outlet}. One drink each for the slow."],
    "first": ["🥂 The desk had {seat} {lead} before anyone else. Whoever called it first hands out a drink."],
    "bot": ["🤖🍺 The bot called {seat} before the desk did. The humans drink."],
}


def rules() -> dict:
    own = packs.active().read("drinking.json")
    return {**RULES, **{k: v for k, v in own.items() if isinstance(v, list) and v}}


def is_on(store) -> bool:
    return bool(store.setting(SETTING, False))


def line(store, event: str, rng=random, **fmt) -> str | None:
    """The line for an event, or None when the game is off for this server or the event has no rule."""
    if store is None or not is_on(store):
        return None
    return FL.draw(store, f"drink:{event}", rules().get(event) or [], fmt, rng)


def events_for_call(kind: str, lead_minutes: float | None, beaten_by: str | None) -> list[str]:
    """Which events an official call sets off. kind is flavor.situation()'s word for it; lead_minutes is how long
    the desk was ahead of the first outlet to agree (None if no outlet has called it); beaten_by is the outlet that
    got there first."""
    out = [kind] if kind in ("flip", "upset", "control") else []
    if beaten_by:
        out.append("bot" if beaten_by == "The Bot" else "beaten")
    return out
