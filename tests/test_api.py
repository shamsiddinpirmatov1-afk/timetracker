"""Проверки HTTP API: настоящий сервер на свободном порту + urllib."""

import json
import threading
import unittest
import urllib.error
import urllib.request

from tests.support import TempDbMixin
from tracker.server import make_server
from tracker.storage import Storage


class ApiTestCase(TempDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = self.start_server()

    def tearDown(self):
        self.stop_server(self.server)
        super().tearDown()

    def start_server(self):
        server = make_server(Storage(self.db_path, clock=self.clock), port=0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    @staticmethod
    def stop_server(server):
        server.shutdown()
        server.server_close()

    def request(self, method, path, body=None, raw=None):
        host, port = self.server.server_address[:2]
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(f"http://{host}:{port}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def add_employee(self, name="Анна Вымыслова"):
        status, data = self.request("POST", "/api/employees", {"name": name})
        self.assertEqual(status, 201, data)
        return data["id"]


class ApiTests(ApiTestCase):
    def test_index_page_served(self):
        host, port = self.server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/") as response:
            self.assertIn("Учёт рабочего времени", response.read().decode("utf-8"))

    def test_unknown_static_path_is_404(self):
        status, _ = self.request("GET", "/../storage.py")
        self.assertEqual(status, 404)

    def test_add_employee(self):
        employee_id = self.add_employee()
        status, data = self.request("GET", "/api/employees")
        self.assertEqual(status, 200)
        self.assertEqual(data["employees"], [{"id": employee_id, "name": "Анна Вымыслова", "running": None}])

    def test_add_employee_validation(self):
        self.assertEqual(self.request("POST", "/api/employees", {"name": ""})[0], 400)
        self.assertEqual(self.request("POST", "/api/employees", {})[0], 400)
        self.assertEqual(self.request("POST", "/api/employees", raw=b"{not json")[0], 400)
        self.assertEqual(self.request("POST", "/api/employees", raw=b"[1, 2]")[0], 400)

    def test_duplicate_employee_is_409(self):
        self.add_employee()
        status, data = self.request("POST", "/api/employees", {"name": "анна вымыслова"})
        self.assertEqual(status, 409)
        self.assertIn("error", data)

    def test_full_scenario_start_stop_report(self):
        employee_id = self.add_employee()
        status, _ = self.request("POST", f"/api/employees/{employee_id}/start", {"task": "Отчёт по проекту"})
        self.assertEqual(status, 201)
        self.clock.advance(45 * 60)
        status, entry = self.request("POST", f"/api/employees/{employee_id}/stop")
        self.assertEqual(status, 200)
        self.assertEqual(entry["seconds"], 45 * 60)

        status, report = self.request("GET", "/api/report?from=2026-03-01&to=2026-03-31")
        self.assertEqual(status, 200)
        self.assertEqual(report["total_seconds"], 45 * 60)
        self.assertEqual(report["entries"][0]["task"], "Отчёт по проекту")
        self.assertEqual(report["totals"][0]["seconds"], 45 * 60)

    def test_repeated_start_is_409(self):
        employee_id = self.add_employee()
        self.request("POST", f"/api/employees/{employee_id}/start", {"task": "Первая"})
        status, data = self.request("POST", f"/api/employees/{employee_id}/start", {"task": "Вторая"})
        self.assertEqual(status, 409)
        self.assertIn("Первая", data["error"])

        _, employees = self.request("GET", "/api/employees")
        self.assertEqual(employees["employees"][0]["running"]["task"], "Первая")

    def test_concurrent_starts_create_only_one_timer(self):
        employee_id = self.add_employee()
        results = []
        barrier = threading.Barrier(8)

        def start():
            barrier.wait()
            results.append(self.request("POST", f"/api/employees/{employee_id}/start", {"task": "Гонка"})[0])

        threads = [threading.Thread(target=start) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), [201] + [409] * 7)
        _, report = self.request("GET", "/api/report?from=2026-03-02&to=2026-03-02")
        self.assertEqual(len(report["entries"]), 1)

    def test_stop_without_start_is_409(self):
        employee_id = self.add_employee()
        self.assertEqual(self.request("POST", f"/api/employees/{employee_id}/stop")[0], 409)

    def test_start_with_empty_task_is_400(self):
        employee_id = self.add_employee()
        self.assertEqual(self.request("POST", f"/api/employees/{employee_id}/start", {"task": " "})[0], 400)

    def test_unknown_employee_is_404(self):
        self.assertEqual(self.request("POST", "/api/employees/999/start", {"task": "Задача"})[0], 404)
        self.assertEqual(self.request("GET", "/api/report?from=2026-03-01&to=2026-03-31&employee_id=999")[0], 404)

    def test_empty_report(self):
        self.add_employee()
        status, report = self.request("GET", "/api/report?from=2026-03-01&to=2026-03-31")
        self.assertEqual(status, 200)
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["total_seconds"], 0)
        self.assertEqual(report["totals"][0]["seconds"], 0)

    def test_report_validation(self):
        for query in ["", "?from=2026-03-01", "?from=2026-03-31&to=2026-03-01",
                      "?from=bad&to=2026-03-01", "?from=2026-03-01&to=2026-03-31&employee_id=abc"]:
            with self.subTest(query=query):
                self.assertEqual(self.request("GET", "/api/report" + query)[0], 400)

    def test_wrong_method_and_unknown_route(self):
        self.assertEqual(self.request("GET", "/api/employees/1/start")[0], 405)
        self.assertEqual(self.request("GET", "/api/nothing")[0], 404)

    def test_data_survives_server_restart(self):
        employee_id = self.add_employee()
        self.request("POST", f"/api/employees/{employee_id}/start", {"task": "До перезапуска"})

        self.stop_server(self.server)
        self.server = self.start_server()

        _, data = self.request("GET", "/api/employees")
        self.assertEqual(data["employees"][0]["running"]["task"], "До перезапуска")


if __name__ == "__main__":
    unittest.main()
