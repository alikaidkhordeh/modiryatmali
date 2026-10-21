"""Decide the category (and person) of an expense from its text.

Order of decisions:
  1. words the user taught the bot (corrections made with the buttons)
  2. the built-in keyword dictionary (bot/categories.py)
  3. optional: Claude API, only when ANTHROPIC_API_KEY is set (off by default, costs money)
  4. otherwise "سایر" and the bot asks the user to pick a category (and learns it)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .categories import CATEGORIES, DEFAULT_PEOPLE, KEYWORDS, STOPWORDS, UNKNOWN
from .db import Database
from .textutil import normalize, tokens

log = logging.getLogger(__name__)

# endings people add to a word: قسط -> قسطم, قبض -> قبضش ...
_SUFFIXES = {"م", "ت", "ش", "ا", "ی", "ه", "ها", "مون", "تون", "شون"}


def _word_matches(token: str, kw: str) -> bool:
    """Short keywords must be a whole word (plus an optional ending), long ones match inside."""
    if len(kw) >= 4:
        return kw in token
    if token == kw:
        return True
    return token.startswith(kw) and token[len(kw):] in _SUFFIXES


def _text_has(text: str, toks: List[str], kw: str) -> bool:
    kw = normalize(kw)
    if " " in kw:
        return kw in text
    return any(_word_matches(t, kw) for t in toks)


@dataclass
class Classification:
    category: str
    person: Optional[str]
    source: str  # learned | keyword | ai | none

    @property
    def resolved(self) -> bool:
        return self.category != UNKNOWN


class Categorizer:
    def __init__(self, db: Database, api_key: Optional[str] = None, model: str = "claude-haiku-5-5"):
        self.db = db
        self.model = model
        self._client = None
        if api_key:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(api_key=api_key, timeout=15)
                log.info("Claude API categorization is ON (model %s)", model)
            except Exception as exc:  # package missing etc.
                log.warning("Claude API disabled: %s", exc)

    # ---- people ---------------------------------------------------------
    def detect_person(self, user_id: int, text: str) -> Optional[str]:
        people = dict(DEFAULT_PEOPLE)
        people.update({normalize(k): v for k, v in self.db.people(user_id).items()})
        for t in tokens(normalize(text)):
            if t in people:
                return people[t]
        return None

    # ---- category -------------------------------------------------------
    def local_category(self, user_id: int, text: str) -> Optional[Classification]:
        norm = normalize(text)
        toks = tokens(norm)

        names = set(self.db.category_names(user_id))
        aliases = self.db.aliases(user_id)

        learned = self.db.keywords(user_id)
        best = None
        for word, category in learned.items():
            if category not in names:
                continue
            if _text_has(norm, toks, word) and (best is None or len(word) > len(best[0])):
                best = (word, category)
        if best:
            return Classification(best[1], None, "learned")

        # pass 1: multi-word phrases, pass 2: single words (both in priority order)
        for want_phrase in (True, False):
            for category, words in KEYWORDS:
                for kw in words:
                    if (" " in normalize(kw)) != want_phrase:
                        continue
                    if _text_has(norm, toks, kw):
                        target = aliases.get(category, category)
                        if target in names:  # skip categories the user deleted
                            return Classification(target, None, "keyword")
        return None

    async def classify(self, user_id: int, text: str) -> Classification:
        person = self.detect_person(user_id, text)
        found = self.local_category(user_id, text)
        if found:
            found.person = person
            return found

        if self._client is not None and text.strip():
            ai = await self._ask_claude(text, self.db.category_names(user_id))
            if ai:
                return Classification(ai["category"], person or ai.get("person"), "ai")

        return Classification(UNKNOWN, person, "none")

    # ---- learning -------------------------------------------------------
    def learn(self, user_id: int, note: str, category: str, max_words: int = 3) -> List[str]:
        """Remember the meaningful words of this note for the given category."""
        if category == UNKNOWN:
            return []
        people = set(DEFAULT_PEOPLE) | {normalize(k) for k in self.db.people(user_id)}
        learned = []
        for t in tokens(normalize(note)):
            if len(t) < 3 or t.isdigit() or t in STOPWORDS or t in people:
                continue
            self.db.set_keyword(user_id, t, category)
            learned.append(t)
            if len(learned) >= max_words:
                break
        return learned

    # ---- optional Claude fallback --------------------------------------
    async def _ask_claude(self, text: str, allowed: List[str]) -> Optional[Dict[str, Optional[str]]]:
        system = (
            "You classify short Persian personal-expense notes (the amount is already removed). "
            "Reply with ONLY a JSON object: {\"category\": <one of the list>, \"person\": <string or null>}. "
            "person is a family member / friend mentioned in the note (e.g. خانمم, نامزدم, a first name) or null. "
            "Categories: " + ", ".join(allowed)
        )
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=120,
                system=system,
                messages=[{"role": "user", "content": text}],
            )
            return parse_ai_reply(resp.content[0].text, allowed)
        except Exception as exc:
            log.warning("Claude categorization failed: %s", exc)
            return None


def parse_ai_reply(raw: str, allowed: Optional[List[str]] = None) -> Optional[Dict[str, Optional[str]]]:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    category = data.get("category")
    if category not in (allowed if allowed is not None else CATEGORIES) or category == UNKNOWN:
        return None
    person = data.get("person")
    person = person.strip() if isinstance(person, str) and person.strip() else None
    return {"category": category, "person": person}
