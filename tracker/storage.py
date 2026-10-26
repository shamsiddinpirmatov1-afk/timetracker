"""Хранилище данных: SQLite-файл на диске, поэтому всё переживает перезапуск.

Время хранится как Unix-timestamp (целые секунды, UTC), а границы периода
отчёта считаются по локальной дате машины, на которой запущен сервер.
"""

import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_NAME_LENGTH = 100
MAX_TASK_LENGTH = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    name_key   TEXT    NOT NULL UNIQUE,   -- имя в casefold() для проверки дублей
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    task        TEXT    NOT NULL,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,                  -- NULL, пока таймер идёт
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- У сотрудника может быть не больше одного запущенного таймера.
-- Индекс защищает от гонки двух одновременных запросов «Старт».
CREATE UNIQUE INDEX IF NOT EXISTS one_running_timer_per_employee
    ON entries(employee_id) WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS entries_started_at ON entries(started_at);
"""


class ValidationError(ValueError):
    """Некорректные входные данные (HTTP 400)."""


class NotFound(LookupError):
    """Объект не найден (HTTP 404)."""


class Conflict(RuntimeError):
    """Действие противоречит текущему состоянию (HTTP 409)."""


def _clean_text(value, field, max_length):
    if not isinstance(value, str):
        raise ValidationError(f"Поле «{field}» должно быть строкой")
    value = " ".join(value.split())
    if not value:
        raise ValidationError(f"Поле «{field}» не может быть пустым")
    if len(value) > max_length:
        raise ValidationError(f"Поле «{field}» длиннее {max_length} символов")
    return value


def _parse_date(value, field):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Дата «{field}» должна быть в формате ГГГГ-ММ-ДД") from None


def _local_midnight(day):
    return int(datetime(day.year, day.month, day.day).timestamp())


class Storage:
    def __init__(self, path, clock=time.time):
        self.path = str(path)
        self.clock = clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as conn:
            conn.executescript(SCHEMA)

    def _now(self):
        return int(self.clock())

    @contextmanager
    def _transaction(self):
        # Отдельное соединение на каждую операцию: сервер многопоточный,
        # а sqlite3.Connection нельзя безопасно делить между потоками.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:  # commit при успехе, rollback при исключении
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _require_employee(conn, employee_id):
        row = conn.execute(
            "SELECT id, name FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"Сотрудник с id={employee_id} не найден")
        return row

    @staticmethod
    def _running_entry(conn, employee_id):
        return conn.execute(
            "SELECT id, task, started_at FROM entries "
            "WHERE employee_id = ? AND ended_at IS NULL",
            (employee_id,),
        ).fetchone()

    # --- Сотрудники ---------------------------------------------------------

    def add_employee(self, name):
        name = _clean_text(name, "Имя", MAX_NAME_LENGTH)
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO employees (name, name_key, created_at) VALUES (?, ?, ?)",
                    (name, name.casefold(), self._now()),
                )
        except sqlite3.IntegrityError:
            raise Conflict(f"Сотрудник «{name}» уже есть в списке") from None
        return {"id": cursor.lastrowid, "name": name, "running": None}

    def list_employees(self):
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.name, r.id AS entry_id, r.task, r.started_at
                FROM employees e
                LEFT JOIN entries r ON r.employee_id = e.id AND r.ended_at IS NULL
                ORDER BY e.name_key
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "running": None if row["entry_id"] is None else {
                    "entry_id": row["entry_id"],
                    "task": row["task"],
                    "started_at": row["started_at"],
                },
            }
            for row in rows
        ]

    # --- Таймер -------------------------------------------------------------

    def start_timer(self, employee_id, task):
        task = _clean_text(task, "Задача", MAX_TASK_LENGTH)
        now = self._now()
        with self._transaction() as conn:
            self._require_employee(conn, employee_id)
            running = self._running_entry(conn, employee_id)
            if running is not None:
                raise Conflict(f"Таймер уже запущен (задача «{running['task']}»). Сначала остановите его.")
            try:
                cursor = conn.execute(
                    "INSERT INTO entries (employee_id, task, started_at) VALUES (?, ?, ?)",
                    (employee_id, task, now),
                )
            except sqlite3.IntegrityError:
                # Параллельный запрос успел запустить таймер между SELECT и INSERT.
                raise Conflict("Таймер уже запущен. Сначала остановите его.") from None
        return {"id": cursor.lastrowid, "employee_id": employee_id, "task": task,
                "started_at": now, "ended_at": None}

    def stop_timer(self, employee_id):
        now = self._now()
        with self._transaction() as conn:
            self._require_employee(conn, employee_id)
            running = self._running_entry(conn, employee_id)
            if running is None:
                raise Conflict("Таймер не запущен — останавливать нечего")
            # Если системные часы перевели назад, не даём длительности стать отрицательной.
            ended_at = max(now, running["started_at"])
            conn.execute("UPDATE entries SET ended_at = ? WHERE id = ?", (ended_at, running["id"]))
        return {"id": running["id"], "employee_id": employee_id, "task": running["task"],
                "started_at": running["started_at"], "ended_at": ended_at,
                "seconds": ended_at - running["started_at"]}

    # --- Отчёт --------------------------------------------------------------

    def report(self, date_from, date_to, employee_id=None):
        """Записи и суммарное время за период [date_from; date_to] включительно.

        Запись, которая пересекает границу периода (например, работа через
        полночь), попадает в отчёт, но учитывается только часть времени,
        лежащая внутри периода. Незавершённая запись считается до текущего момента.
        """
        day_from = _parse_date(date_from, "с")
        day_to = _parse_date(date_to, "по")
        if day_from > day_to:
            raise ValidationError("Начало периода позже его конца")
        period_start = _local_midnight(day_from)
        period_end = _local_midnight(day_to + timedelta(days=1))
        now = self._now()

        with self._transaction() as conn:
            if employee_id is not None:
                self._require_employee(conn, employee_id)
                employees = conn.execute(
                    "SELECT id, name FROM employees WHERE id = ?", (employee_id,)
                ).fetchall()
            else:
                employees = conn.execute(
                    "SELECT id, name FROM employees ORDER BY name_key"
                ).fetchall()

            rows = conn.execute(
                """
                SELECT en.id, en.employee_id, em.name, en.task, en.started_at, en.ended_at
                FROM entries en
                JOIN employees em ON em.id = en.employee_id
                WHERE en.started_at < :end
                  AND (COALESCE(en.ended_at, :now) > :start OR en.started_at >= :start)
                  AND (:employee_id IS NULL OR en.employee_id = :employee_id)
                ORDER BY en.started_at, en.id
                """,
                {"start": period_start, "end": period_end, "now": now, "employee_id": employee_id},
            ).fetchall()

        totals = {row["id"]: 0 for row in employees}
        entries = []
        for row in rows:
            effective_end = row["ended_at"] if row["ended_at"] is not None else max(now, row["started_at"])
            seconds = max(0, min(effective_end, period_end) - max(row["started_at"], period_start))
            totals[row["employee_id"]] = totals.get(row["employee_id"], 0) + seconds
            entries.append({
                "id": row["id"],
                "employee_id": row["employee_id"],
                "employee_name": row["name"],
                "task": row["task"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "running": row["ended_at"] is None,
                "seconds": seconds,
            })

        return {
            "from": day_from.isoformat(),
            "to": day_to.isoformat(),
            "entries": entries,
            "totals": [
                {"employee_id": row["id"], "name": row["name"], "seconds": totals[row["id"]]}
                for row in employees
            ],
            "total_seconds": sum(totals.values()),
        }
