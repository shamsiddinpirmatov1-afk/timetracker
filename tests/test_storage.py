import unittest

from tests.support import TempDbMixin, local_ts
from tracker.storage import Conflict, NotFound, Storage, ValidationError


class StorageTestCase(TempDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.storage = Storage(self.db_path, clock=self.clock)


class EmployeeTests(StorageTestCase):
    def test_add_and_list(self):
        self.storage.add_employee("Борис Тестов")
        self.storage.add_employee("Анна Вымыслова")
        names = [e["name"] for e in self.storage.list_employees()]
        self.assertEqual(names, ["Анна Вымыслова", "Борис Тестов"])

    def test_name_whitespace_is_normalized(self):
        employee = self.storage.add_employee("  Анна   Вымыслова \n")
        self.assertEqual(employee["name"], "Анна Вымыслова")

    def test_empty_name_rejected(self):
        for bad in ["", "   ", None, 42]:
            with self.subTest(name=bad), self.assertRaises(ValidationError):
                self.storage.add_employee(bad)

    def test_too_long_name_rejected(self):
        with self.assertRaises(ValidationError):
            self.storage.add_employee("А" * 101)

    def test_duplicate_name_rejected_case_insensitive_for_cyrillic(self):
        # SQLite COLLATE NOCASE понимает только латиницу, поэтому сравниваем casefold().
        self.storage.add_employee("Анна Вымыслова")
        with self.assertRaises(Conflict):
            self.storage.add_employee("АННА вымыслова")
        self.assertEqual(len(self.storage.list_employees()), 1)


class TimerTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.anna = self.storage.add_employee("Анна Вымыслова")["id"]

    def test_start_and_stop(self):
        self.storage.start_timer(self.anna, "Вёрстка формы")
        running = self.storage.list_employees()[0]["running"]
        self.assertEqual(running["task"], "Вёрстка формы")

        self.clock.advance(90 * 60)
        entry = self.storage.stop_timer(self.anna)
        self.assertEqual(entry["seconds"], 90 * 60)
        self.assertIsNone(self.storage.list_employees()[0]["running"])

    def test_repeated_start_is_rejected_and_keeps_first_timer(self):
        self.storage.start_timer(self.anna, "Первая задача")
        self.clock.advance(60)
        with self.assertRaises(Conflict) as ctx:
            self.storage.start_timer(self.anna, "Вторая задача")
        self.assertIn("Первая задача", str(ctx.exception))

        running = self.storage.list_employees()[0]["running"]
        self.assertEqual(running["task"], "Первая задача")
        self.assertEqual(running["started_at"], int(local_ts(2026, 3, 2, 9, 0)))

    def test_start_again_after_stop_creates_new_entry(self):
        self.storage.start_timer(self.anna, "Задача 1")
        self.clock.advance(600)
        self.storage.stop_timer(self.anna)
        self.storage.start_timer(self.anna, "Задача 2")
        self.clock.advance(300)
        self.storage.stop_timer(self.anna)

        report = self.storage.report("2026-03-02", "2026-03-02")
        self.assertEqual([e["task"] for e in report["entries"]], ["Задача 1", "Задача 2"])
        self.assertEqual(report["total_seconds"], 900)

    def test_timers_of_different_employees_are_independent(self):
        boris = self.storage.add_employee("Борис Тестов")["id"]
        self.storage.start_timer(self.anna, "Задача Анны")
        self.storage.start_timer(boris, "Задача Бориса")
        self.assertTrue(all(e["running"] for e in self.storage.list_employees()))

    def test_stop_without_running_timer(self):
        with self.assertRaises(Conflict):
            self.storage.stop_timer(self.anna)

    def test_double_stop(self):
        self.storage.start_timer(self.anna, "Задача")
        self.storage.stop_timer(self.anna)
        with self.assertRaises(Conflict):
            self.storage.stop_timer(self.anna)

    def test_empty_task_rejected(self):
        with self.assertRaises(ValidationError):
            self.storage.start_timer(self.anna, "   ")
        self.assertIsNone(self.storage.list_employees()[0]["running"])

    def test_unknown_employee(self):
        with self.assertRaises(NotFound):
            self.storage.start_timer(999, "Задача")
        with self.assertRaises(NotFound):
            self.storage.stop_timer(999)

    def test_clock_going_backwards_does_not_give_negative_duration(self):
        self.storage.start_timer(self.anna, "Задача")
        self.clock.advance(-3600)
        self.assertEqual(self.storage.stop_timer(self.anna)["seconds"], 0)


class ReportTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.anna = self.storage.add_employee("Анна Вымыслова")["id"]
        self.boris = self.storage.add_employee("Борис Тестов")["id"]

    def work(self, employee_id, task, start, minutes):
        self.clock.value = start
        self.storage.start_timer(employee_id, task)
        self.clock.advance(minutes * 60)
        self.storage.stop_timer(employee_id)

    def test_empty_report(self):
        report = self.storage.report("2026-03-01", "2026-03-31")
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["total_seconds"], 0)
        self.assertEqual([t["seconds"] for t in report["totals"]], [0, 0])

    def test_empty_report_without_employees(self):
        storage = Storage(self.db_path.with_name("empty.db"), clock=self.clock)
        report = storage.report("2026-03-01", "2026-03-31")
        self.assertEqual(report, {"from": "2026-03-01", "to": "2026-03-31",
                                  "entries": [], "totals": [], "total_seconds": 0})

    def test_totals_per_employee_and_period_filter(self):
        self.work(self.anna, "Март, день 2", local_ts(2026, 3, 2, 9, 0), 60)
        self.work(self.anna, "Март, день 3", local_ts(2026, 3, 3, 9, 0), 30)
        self.work(self.boris, "Март, день 3", local_ts(2026, 3, 3, 10, 0), 45)
        self.work(self.anna, "Апрель", local_ts(2026, 4, 1, 9, 0), 120)

        report = self.storage.report("2026-03-01", "2026-03-31")
        totals = {t["name"]: t["seconds"] for t in report["totals"]}
        self.assertEqual(totals, {"Анна Вымыслова": 90 * 60, "Борис Тестов": 45 * 60})
        self.assertEqual(report["total_seconds"], 135 * 60)
        self.assertEqual(len(report["entries"]), 3)

    def test_single_day_period_includes_whole_last_day(self):
        self.work(self.anna, "Поздний вечер", local_ts(2026, 3, 2, 23, 0), 30)
        report = self.storage.report("2026-03-02", "2026-03-02")
        self.assertEqual(report["total_seconds"], 30 * 60)

    def test_filter_by_employee(self):
        self.work(self.anna, "Задача Анны", local_ts(2026, 3, 2, 9, 0), 10)
        self.work(self.boris, "Задача Бориса", local_ts(2026, 3, 2, 9, 0), 20)
        report = self.storage.report("2026-03-02", "2026-03-02", employee_id=self.boris)
        self.assertEqual([e["task"] for e in report["entries"]], ["Задача Бориса"])
        self.assertEqual(report["totals"], [{"employee_id": self.boris, "name": "Борис Тестов",
                                             "seconds": 20 * 60}])

    def test_entry_crossing_midnight_is_split_between_days(self):
        self.work(self.anna, "Ночной релиз", local_ts(2026, 3, 2, 23, 0), 120)
        day1 = self.storage.report("2026-03-02", "2026-03-02")
        day2 = self.storage.report("2026-03-03", "2026-03-03")
        both = self.storage.report("2026-03-02", "2026-03-03")
        self.assertEqual(day1["total_seconds"], 60 * 60)
        self.assertEqual(day2["total_seconds"], 60 * 60)
        self.assertEqual(both["total_seconds"], 120 * 60)

    def test_entry_ending_exactly_at_midnight_not_in_next_day(self):
        self.work(self.anna, "До полуночи", local_ts(2026, 3, 2, 23, 0), 60)
        self.assertEqual(self.storage.report("2026-03-03", "2026-03-03")["entries"], [])

    def test_running_timer_counted_until_now(self):
        self.clock.value = local_ts(2026, 3, 2, 9, 0)
        self.storage.start_timer(self.anna, "Ещё работаю")
        self.clock.advance(25 * 60)
        report = self.storage.report("2026-03-02", "2026-03-02")
        self.assertTrue(report["entries"][0]["running"])
        self.assertEqual(report["total_seconds"], 25 * 60)

    def test_invalid_period(self):
        with self.assertRaises(ValidationError):
            self.storage.report("2026-03-31", "2026-03-01")
        with self.assertRaises(ValidationError):
            self.storage.report("31.03.2026", "2026-04-01")
        with self.assertRaises(ValidationError):
            self.storage.report(None, "2026-04-01")

    def test_unknown_employee_in_filter(self):
        with self.assertRaises(NotFound):
            self.storage.report("2026-03-01", "2026-03-31", employee_id=999)


class PersistenceTests(TempDbMixin, unittest.TestCase):
    def test_data_survives_restart(self):
        first = Storage(self.db_path, clock=self.clock)
        anna = first.add_employee("Анна Вымыслова")["id"]
        first.start_timer(anna, "Задача до перезапуска")
        self.clock.advance(600)
        first.stop_timer(anna)
        first.start_timer(anna, "Таймер, оставленный включённым")
        del first

        # «Перезапуск»: новый объект поверх того же файла.
        second = Storage(self.db_path, clock=self.clock)
        employees = second.list_employees()
        self.assertEqual([e["name"] for e in employees], ["Анна Вымыслова"])
        self.assertEqual(employees[0]["running"]["task"], "Таймер, оставленный включённым")

        self.clock.advance(300)
        second.stop_timer(anna)
        report = second.report("2026-03-02", "2026-03-02")
        self.assertEqual(report["total_seconds"], 900)


if __name__ == "__main__":
    unittest.main()
