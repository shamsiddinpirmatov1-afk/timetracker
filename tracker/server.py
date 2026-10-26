"""HTTP-сервер на стандартной библиотеке: JSON API + статические файлы интерфейса."""

import json
import re
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .storage import Conflict, NotFound, ValidationError

STATIC_DIR = Path(__file__).parent / "static"

# Белый список вместо отдачи произвольных путей — так исключён path traversal.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

ROUTES = [
    ("GET", re.compile(r"^/api/employees$"), "list_employees"),
    ("POST", re.compile(r"^/api/employees$"), "add_employee"),
    ("POST", re.compile(r"^/api/employees/(\d+)/start$"), "start_timer"),
    ("POST", re.compile(r"^/api/employees/(\d+)/stop$"), "stop_timer"),
    ("GET", re.compile(r"^/api/report$"), "report"),
]

MAX_BODY_BYTES = 64 * 1024

ERROR_STATUS = {
    ValidationError: HTTPStatus.BAD_REQUEST,
    NotFound: HTTPStatus.NOT_FOUND,
    Conflict: HTTPStatus.CONFLICT,
}


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "TimeTracker/1.0"

    @property
    def storage(self):
        return self.server.storage

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def log_message(self, format, *args):
        if not getattr(self.server, "quiet", False):
            super().log_message(format, *args)

    # --- Маршрутизация ------------------------------------------------------

    def _dispatch(self, method):
        url = urlparse(self.path)

        if method == "GET" and url.path in STATIC_FILES:
            return self._send_static(*STATIC_FILES[url.path])

        path_matched = False
        for route_method, pattern, action in ROUTES:
            match = pattern.match(url.path)
            if not match:
                continue
            path_matched = True
            if route_method != method:
                continue
            try:
                status, payload = getattr(self, "api_" + action)(*match.groups(), query=parse_qs(url.query))
            except tuple(ERROR_STATUS) as error:
                status, payload = ERROR_STATUS[type(error)], {"error": str(error)}
            except Exception:
                traceback.print_exc(file=sys.stderr)
                status, payload = HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Внутренняя ошибка сервера"}
            return self._send_json(status, payload)

        if path_matched:
            return self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Метод не поддерживается"})
        return self._send_json(HTTPStatus.NOT_FOUND, {"error": "Не найдено"})

    # --- Обработчики API ----------------------------------------------------

    def api_list_employees(self, query):
        return HTTPStatus.OK, {"employees": self.storage.list_employees()}

    def api_add_employee(self, query):
        body = self._read_json()
        return HTTPStatus.CREATED, self.storage.add_employee(body.get("name"))

    def api_start_timer(self, employee_id, query):
        body = self._read_json()
        return HTTPStatus.CREATED, self.storage.start_timer(int(employee_id), body.get("task"))

    def api_stop_timer(self, employee_id, query):
        return HTTPStatus.OK, self.storage.stop_timer(int(employee_id))

    def api_report(self, query):
        employee_id = query.get("employee_id", [""])[0]
        if employee_id:
            if not employee_id.isdigit():
                raise ValidationError("employee_id должен быть числом")
            employee_id = int(employee_id)
        else:
            employee_id = None
        report = self.storage.report(
            query.get("from", [None])[0], query.get("to", [None])[0], employee_id
        )
        return HTTPStatus.OK, report

    # --- Ввод/вывод ---------------------------------------------------------

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValidationError("Некорректный заголовок Content-Length") from None
        if length > MAX_BODY_BYTES:
            raise ValidationError("Слишком большое тело запроса")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError("Тело запроса должно быть корректным JSON") from None
        if not isinstance(data, dict):
            raise ValidationError("Тело запроса должно быть JSON-объектом")
        return data

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, filename, content_type):
        body = (STATIC_DIR / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(storage, host="127.0.0.1", port=8000, quiet=False):
    server = ThreadingHTTPServer((host, port), ApiHandler)
    server.storage = storage
    server.quiet = quiet
    return server
