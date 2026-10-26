"""Заполняет базу вымышленными сотрудниками и записями за последние дни.

python seed.py [--db data/timetracker.db]
"""

import argparse
from datetime import datetime, timedelta

from run import DEFAULT_DB
from tracker.storage import Conflict, Storage

EMPLOYEES = ["Анна Вымыслова", "Борис Тестов", "Вера Примерова"]

# (дней назад, сотрудник, задача, час начала, длительность в минутах)
ENTRIES = [
    (2, 0, "Вёрстка страницы оплаты", 9, 150),
    (2, 1, "Разбор обращений поддержки", 10, 90),
    (1, 0, "Код-ревью задачи DEMO-42", 11, 45),
    (1, 2, "Подготовка квартального отчёта", 14, 200),
    (0, 1, "Созвон с вымышленным клиентом", 9, 30),
]


class ManualClock:
    def __init__(self):
        self.value = datetime.now().timestamp()

    def __call__(self):
        return self.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    clock = ManualClock()
    storage = Storage(args.db, clock=clock)

    ids = []
    for name in EMPLOYEES:
        try:
            ids.append(storage.add_employee(name)["id"])
        except Conflict:
            ids.append(next(e["id"] for e in storage.list_employees() if e["name"] == name))

    today = datetime.now().replace(minute=0, second=0, microsecond=0)
    for days_ago, employee_index, task, hour, minutes in ENTRIES:
        start = (today - timedelta(days=days_ago)).replace(hour=hour)
        clock.value = start.timestamp()
        storage.start_timer(ids[employee_index], task)
        clock.value = (start + timedelta(minutes=minutes)).timestamp()
        storage.stop_timer(ids[employee_index])

    print(f"Добавлено сотрудников: {len(EMPLOYEES)}, записей: {len(ENTRIES)} → {args.db}")


if __name__ == "__main__":
    main()
