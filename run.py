"""Запуск приложения: python run.py [--port 8000] [--db data/timetracker.db]"""

import argparse
from pathlib import Path

from tracker.server import make_server
from tracker.storage import Storage

DEFAULT_DB = Path(__file__).parent / "data" / "timetracker.db"


def main():
    parser = argparse.ArgumentParser(description="Учёт рабочего времени сотрудников")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="путь к файлу SQLite")
    args = parser.parse_args()

    server = make_server(Storage(args.db), args.host, args.port)
    print(f"Приложение запущено: http://{args.host}:{args.port}")
    print(f"База данных: {args.db}")
    print("Остановить: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
