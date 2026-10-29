import functools
import json
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .food_ranking import normalize_query

SCHEMA = """
CREATE TABLE IF NOT EXISTS day_nutrition (
    day TEXT PRIMARY KEY,
    calories REAL,
    protein REAL,
    carbs REAL,
    fat REAL,
    water_ml REAL,
    weight REAL,
    goal_calories REAL,
    diary_synced INTEGER NOT NULL DEFAULT 0,
    goal_protein REAL,
    goal_carbs REAL,
    goal_fat REAL
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
CREATE TABLE IF NOT EXISTS food_pin (
    query TEXT PRIMARY KEY,
    food_id TEXT NOT NULL,
    weight_id TEXT NOT NULL,
    name TEXT,
    serving TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS food_draft (
    draft_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);
"""

DRAFT_LIFETIME = timedelta(hours=24)

NUTRITION_FIELDS = (
    "calories",
    "protein",
    "carbs",
    "fat",
    "water_ml",
    "weight",
    "goal_calories",
    "goal_protein",
    "goal_carbs",
    "goal_fat",
)

GOAL_COLUMNS = {
    "calories": "goal_calories",
    "protein": "goal_protein",
    "carbs": "goal_carbs",
    "fat": "goal_fat",
}

ADDED_NUTRITION_COLUMNS = {
    "goal_protein": "REAL",
    "goal_carbs": "REAL",
    "goal_fat": "REAL",
}

TREND_COLUMNS = {
    "weight": "weight",
    "calories_in": "calories",
    "protein": "protein",
    "carbs": "carbs",
    "fat": "fat",
}


POPULATED_DAY_QUERIES = (
    "SELECT day FROM day_nutrition WHERE day >= ? AND day <= ?",
    "SELECT DISTINCT day FROM diary_entry WHERE day >= ? AND day <= ?",
    "SELECT day FROM feel_note WHERE day >= ? AND day <= ?",
    "SELECT day FROM day_note WHERE day >= ? AND day <= ? "
    "AND body IS NOT NULL AND body != ''",
)


def trend_column(metric: str) -> str:
    column = TREND_COLUMNS.get(metric)
    if column is None:
        raise ValueError(f"unknown metric (use {' | '.join(TREND_COLUMNS)})")
    return column


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _serialized(method):
    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


class Store:
    def __init__(self, path: Path | None = None):
        if path is None:
            path = config.database_path()
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bring databases created by older releases up to the current schema."""
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(day_nutrition)")
        }
        if "diary_synced" not in columns:
            self.conn.execute(
                "ALTER TABLE day_nutrition "
                "ADD COLUMN diary_synced INTEGER NOT NULL DEFAULT 0"
            )
            # Before this column existed, only a diary sync wrote these fields;
            # weight-only rows came from weigh-ins and still need a fetch.
            self.conn.execute(
                "UPDATE day_nutrition SET diary_synced = 1 WHERE "
                "calories IS NOT NULL OR protein IS NOT NULL OR carbs IS NOT NULL "
                "OR fat IS NOT NULL OR water_ml IS NOT NULL "
                "OR goal_calories IS NOT NULL"
            )
            self.conn.commit()
        for column, sql_type in ADDED_NUTRITION_COLUMNS.items():
            if column not in columns:
                self.conn.execute(
                    f"ALTER TABLE day_nutrition ADD COLUMN {column} {sql_type}"
                )
                self.conn.commit()

    @_serialized
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

    @_serialized
    def mark_diary_synced(self, day: str) -> None:
        """Record that the day's MyFitnessPal diary has been fetched in full.

        A row can exist with only a weigh-in (see `fitness_log_weight` and the
        measurement backfill in sync.poll); gap-fill keys off this flag, not off
        row existence, so such days still get their calories and macros.
        """
        self.conn.execute(
            "INSERT INTO day_nutrition (day, diary_synced) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET diary_synced = 1",
            (day,),
        )
        self.conn.commit()

    @_serialized
    def nutrition(self, day: str) -> dict | None:
        row = self.conn.execute(
            "SELECT day, calories, protein, carbs, fat, water_ml, weight, "
            "goal_calories FROM day_nutrition WHERE day = ?",
            (day,),
        ).fetchone()
        return _row_to_dict(row)

    @_serialized
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

    @_serialized
    def diary(self, day: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT meal, name, calories, protein, carbs, fat FROM diary_entry "
            "WHERE day = ? ORDER BY id",
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]

    @_serialized
    def set_feel(self, day: str, note: str | None, rating: int | None) -> dict:
        self.conn.execute(
            "INSERT INTO feel_note (day, note, rating) VALUES (?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET note = excluded.note, rating = excluded.rating",
            (day, note, rating),
        )
        self.conn.commit()
        return {"day": day, "note": note, "rating": rating}

    @_serialized
    def feel(self, day: str) -> dict | None:
        row = self.conn.execute(
            "SELECT day, note, rating FROM feel_note WHERE day = ?", (day,)
        ).fetchone()
        return _row_to_dict(row)

    @_serialized
    def set_note(self, day: str, body: str | None) -> None:
        """The MyFitnessPal daily diary note (synced from/to MFP), distinct from
        the local-only feel note."""
        self.conn.execute(
            "INSERT INTO day_note (day, body) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET body = excluded.body",
            (day, body),
        )
        self.conn.commit()

    @_serialized
    def note(self, day: str) -> str | None:
        row = self.conn.execute(
            "SELECT body FROM day_note WHERE day = ?", (day,)
        ).fetchone()
        if row is None:
            return None
        return row["body"]

    @_serialized
    def days_with_synced_diary(self, start: str, end: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT day FROM day_nutrition "
            "WHERE day >= ? AND day <= ? AND diary_synced = 1",
            (start, end),
        ).fetchall()
        return {r["day"] for r in rows}

    @_serialized
    def trend(self, metric: str, start: str, end: str) -> list[dict]:
        rows = self.conn.execute(
            f"SELECT day, {trend_column(metric)} AS value FROM day_nutrition "
            "WHERE day >= ? AND day <= ? AND value IS NOT NULL ORDER BY day",
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]

    @_serialized
    def goals(self, day: str) -> dict:
        row = self.conn.execute(
            f"SELECT {', '.join(GOAL_COLUMNS.values())} FROM day_nutrition "
            "WHERE day = ?",
            (day,),
        ).fetchone()
        return {
            name: (row[column] if row is not None else None)
            for name, column in GOAL_COLUMNS.items()
        }

    @_serialized
    def day_record(self, day: str) -> dict:
        nutrition = self.nutrition(day)
        goals = self.goals(day)
        logged = nutrition or {}
        remaining = {
            name: None if goal is None else round(goal - (logged.get(name) or 0.0), 1)
            for name, goal in goals.items()
        }
        return {
            "day": day,
            "nutrition": nutrition,
            "goals": goals,
            "remaining": remaining,
            "diary": self.diary(day),
            "note": self.note(day),
            "feel": self.feel(day),
        }

    @_serialized
    def export_range(self, start: str, end: str) -> list[dict]:
        days: set[str] = set()
        for query in POPULATED_DAY_QUERIES:
            days |= {row["day"] for row in self.conn.execute(query, (start, end))}
        return [self.day_record(day) for day in sorted(days)]

    @_serialized
    def last_synced_on(self) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'last_synced_on'"
        ).fetchone()
        if row is None:
            return None
        return row["value"]

    @_serialized
    def mark_synced(self, day: date | None = None) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('last_synced_on', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ((day or date.today()).isoformat(),),
        )
        self.conn.commit()

    @_serialized
    def set_pin(
        self,
        query: str,
        food_id: str,
        weight_id: str,
        name: str | None,
        serving: str | None,
    ) -> dict:
        pin = {
            "query": normalize_query(query),
            "food_id": str(food_id),
            "weight_id": str(weight_id),
            "name": name,
            "serving": serving,
            "updated_at": _utc_now().isoformat(),
        }
        self.conn.execute(
            "INSERT INTO food_pin (query, food_id, weight_id, name, serving, "
            "updated_at) VALUES (:query, :food_id, :weight_id, :name, :serving, "
            ":updated_at) ON CONFLICT(query) DO UPDATE SET "
            "food_id = excluded.food_id, weight_id = excluded.weight_id, "
            "name = excluded.name, serving = excluded.serving, "
            "updated_at = excluded.updated_at",
            pin,
        )
        self.conn.commit()
        return pin

    @_serialized
    def pin(self, query: str) -> dict | None:
        row = self.conn.execute(
            "SELECT query, food_id, weight_id, name, serving, updated_at "
            "FROM food_pin WHERE query = ?",
            (normalize_query(query),),
        ).fetchone()
        return _row_to_dict(row)

    @_serialized
    def pins(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT query, food_id, weight_id, name, serving, updated_at "
            "FROM food_pin ORDER BY query"
        ).fetchall()
        return [dict(pin_row) for pin_row in rows]

    @_serialized
    def clear_pin(self, query: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM food_pin WHERE query = ?", (normalize_query(query),)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @_serialized
    def clear_pins(self) -> int:
        cursor = self.conn.execute("DELETE FROM food_pin")
        self.conn.commit()
        return cursor.rowcount

    @_serialized
    def save_draft(self, body: dict, now: datetime | None = None) -> str:
        created_at = now or _utc_now()
        self.conn.execute(
            "DELETE FROM food_draft WHERE created_at < ?",
            ((created_at - DRAFT_LIFETIME).isoformat(),),
        )
        draft_id = secrets.token_hex(4)
        self.conn.execute(
            "INSERT INTO food_draft (draft_id, created_at, body) VALUES (?, ?, ?)",
            (draft_id, created_at.isoformat(), json.dumps(body)),
        )
        self.conn.commit()
        return draft_id

    @_serialized
    def draft(self, draft_id: str, now: datetime | None = None) -> dict | None:
        row = self.conn.execute(
            "SELECT created_at, body FROM food_draft WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            return None
        created_at = datetime.fromisoformat(row["created_at"])
        if (now or _utc_now()) - created_at > DRAFT_LIFETIME:
            return None
        return json.loads(row["body"])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
