import sqlite3
from datetime import date
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS day_nutrition (
    day TEXT PRIMARY KEY,
    calories REAL,
    protein REAL,
    carbs REAL,
    fat REAL,
    water_ml REAL,
    weight REAL,
    goal_calories REAL
);
CREATE TABLE IF NOT EXISTS diary_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    meal TEXT,
    name TEXT,
    calories REAL,
    protein REAL,
    carbs REAL,
    fat REAL
);
CREATE INDEX IF NOT EXISTS diary_entry_day ON diary_entry(day);
CREATE TABLE IF NOT EXISTS feel_note (
    day TEXT PRIMARY KEY,
    note TEXT,
    rating INTEGER
);
CREATE TABLE IF NOT EXISTS day_note (
    day TEXT PRIMARY KEY,
    body TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

NUTRITION_FIELDS = (
    "calories",
    "protein",
    "carbs",
    "fat",
    "water_ml",
    "weight",
    "goal_calories",
)

TREND_COLUMNS = {
    "weight": "weight",
    "calories_in": "calories",
    "protein": "protein",
    "carbs": "carbs",
    "fat": "fat",
}


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


class Store:
    def __init__(self, path: Path | None = None):
        if path is None:
            path = config.database_path()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert_nutrition(self, day: str, **fields) -> None:
        unknown = set(fields) - set(NUTRITION_FIELDS)
        if unknown:
            raise ValueError(f"unknown nutrition fields: {sorted(unknown)}")
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{name} = excluded.{name}" for name in fields)
        self.conn.execute(
            f"INSERT INTO day_nutrition (day, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(day) DO UPDATE SET {updates}",
            [day, *fields.values()],
        )
        self.conn.commit()

    def nutrition(self, day: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM day_nutrition WHERE day = ?", (day,)
        ).fetchone()
        return _row_to_dict(row)

    def replace_diary(self, day: str, entries: list[dict]) -> None:
        self.conn.execute("DELETE FROM diary_entry WHERE day = ?", (day,))
        self.conn.executemany(
            "INSERT INTO diary_entry (day, meal, name, calories, protein, carbs, fat) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    day,
                    e.get("meal"),
                    e.get("name"),
                    e.get("calories"),
                    e.get("protein"),
                    e.get("carbs"),
                    e.get("fat"),
                )
                for e in entries
            ],
        )
        self.conn.commit()

    def diary(self, day: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT meal, name, calories, protein, carbs, fat FROM diary_entry "
            "WHERE day = ? ORDER BY id",
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_feel(self, day: str, note: str | None, rating: int | None) -> dict:
        self.conn.execute(
            "INSERT INTO feel_note (day, note, rating) VALUES (?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET note = excluded.note, rating = excluded.rating",
            (day, note, rating),
        )
        self.conn.commit()
        return {"day": day, "note": note, "rating": rating}

    def feel(self, day: str) -> dict | None:
        row = self.conn.execute(
            "SELECT day, note, rating FROM feel_note WHERE day = ?", (day,)
        ).fetchone()
        return _row_to_dict(row)

    def set_note(self, day: str, body: str | None) -> None:
        """The MyFitnessPal daily diary note (synced from/to MFP), distinct from
        the local-only feel note."""
        self.conn.execute(
            "INSERT INTO day_note (day, body) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET body = excluded.body",
            (day, body),
        )
        self.conn.commit()

    def note(self, day: str) -> str | None:
        row = self.conn.execute(
            "SELECT body FROM day_note WHERE day = ?", (day,)
        ).fetchone()
        if row is None:
            return None
        return row["body"]

    def days_with_nutrition(self, start: str, end: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT day FROM day_nutrition WHERE day >= ? AND day <= ?", (start, end)
        ).fetchall()
        return {r["day"] for r in rows}

    def trend(self, metric: str, start: str, end: str) -> list[dict]:
        column = TREND_COLUMNS.get(metric)
        if column is None:
            raise ValueError(f"unknown metric (use {' | '.join(TREND_COLUMNS)})")
        rows = self.conn.execute(
            f"SELECT day, {column} AS value FROM day_nutrition "
            "WHERE day >= ? AND day <= ? AND value IS NOT NULL ORDER BY day",
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]

    def export_range(self, start: str, end: str) -> list[dict]:
        days = sorted(
            self.days_with_nutrition(start, end)
            | {
                r["day"]
                for r in self.conn.execute(
                    "SELECT DISTINCT day FROM diary_entry WHERE day >= ? AND day <= ?",
                    (start, end),
                )
            }
            | {
                r["day"]
                for r in self.conn.execute(
                    "SELECT day FROM feel_note WHERE day >= ? AND day <= ?",
                    (start, end),
                )
            }
            | {
                r["day"]
                for r in self.conn.execute(
                    "SELECT day FROM day_note WHERE day >= ? AND day <= ? "
                    "AND body IS NOT NULL AND body != ''",
                    (start, end),
                )
            }
        )
        return [
            {
                "day": day,
                "nutrition": self.nutrition(day),
                "diary": self.diary(day),
                "note": self.note(day),
                "feel": self.feel(day),
            }
            for day in days
        ]

    def last_synced_on(self) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'last_synced_on'"
        ).fetchone()
        if row is None:
            return None
        return row["value"]

    def mark_synced(self) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('last_synced_on', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (date.today().isoformat(),),
        )
        self.conn.commit()
