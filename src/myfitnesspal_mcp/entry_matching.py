from datetime import date

MEAL_ALIASES = {"snack": "snacks"}

AMBIGUITY_ADVICE = "Use a more specific query to pick one."


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def normalize_meal(meal: str | None) -> str | None:
    if meal is None:
        return None
    lowered = collapse_whitespace(meal).lower()
    return MEAL_ALIASES.get(lowered, lowered)


class DiaryLookupError(RuntimeError):
    pass


class NoMatchingEntry(DiaryLookupError):
    pass


class AmbiguousEntry(DiaryLookupError):
    def __init__(
        self,
        query: str,
        meal: str | None,
        day: date,
        candidates: list[dict],
        *,
        advice: str = AMBIGUITY_ADVICE,
    ):
        self.candidates = candidates
        where = f" in {meal}" if meal else ""
        options = "; ".join(f"{candidate['name']!r}" for candidate in candidates)
        super().__init__(
            f"'{query}'{where} on {day.isoformat()} matches multiple diary entries: "
            f"{options}. {advice}"
        )


def _meal_pool(entries: list[dict], meal: str | None) -> list[dict]:
    target = normalize_meal(meal)
    if target is None:
        return entries
    return [entry for entry in entries if entry["meal"] == target]


def find_entries(
    entries: list[dict], query: str, meal: str | None = None
) -> list[dict]:
    needle = query.lower()
    return [
        entry for entry in _meal_pool(entries, meal) if needle in entry["name"].lower()
    ]


def _interchangeable(entries: list[dict]) -> bool:
    def without_entry_id(entry: dict) -> dict:
        return {key: value for key, value in entry.items() if key != "entry_id"}

    first = without_entry_id(entries[0])
    return all(without_entry_id(entry) == first for entry in entries[1:])


def resolve_entry(
    entries: list[dict],
    query: str,
    meal: str | None,
    day: date,
    *,
    advice: str = AMBIGUITY_ADVICE,
) -> dict:
    candidates = find_entries(entries, query, meal)
    exact = [
        candidate
        for candidate in candidates
        if candidate["name"].lower() == query.lower()
    ]
    for pool in (exact, candidates):
        if pool and _interchangeable(pool):
            return pool[0]
    if candidates:
        raise AmbiguousEntry(query, meal, day, candidates, advice=advice)
    raise no_matching_entry(entries, query, meal, day)


def no_matching_entry(
    entries: list[dict], query: str, meal: str | None, day: date
) -> NoMatchingEntry:
    where = f" in {meal}" if meal else ""
    pool = _meal_pool(entries, meal)
    logged = "; ".join(f"{entry['name']!r}" for entry in pool) or "(nothing logged)"
    return NoMatchingEntry(
        f"no diary entry matching '{query}'{where} on {day.isoformat()}. "
        f"Entries actually logged{where}: {logged}"
    )
