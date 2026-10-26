"use strict";

const $ = (selector) => document.querySelector(selector);

const state = {
  employees: [],
  reportShown: false,
};

// --- Утилиты -----------------------------------------------------------------

// Создаёт DOM-элемент. Текст вставляется через textContent, поэтому имена и
// описания задач не интерпретируются как HTML (защита от XSS).
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  const { dataset, ...rest } = props;
  Object.assign(node, rest);
  if (dataset) Object.assign(node.dataset, dataset);
  for (const child of children) {
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

async function api(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Ошибка сервера (${response.status})`);
  return data;
}

function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatDateTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

// Локальная дата в формате YYYY-MM-DD (toISOString дал бы дату в UTC).
function isoDate(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const nowSeconds = () => Date.now() / 1000;

function showError(message) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.hidden = false;
  clearTimeout(showError.timer);
  showError.timer = setTimeout(() => { notice.hidden = true; }, 6000);
}

// Блокирует кнопку на время запроса, чтобы двойной клик не отправил его дважды.
async function withButton(button, action) {
  button.disabled = true;
  try {
    await action();
    $("#notice").hidden = true;
    await refresh();
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
  }
}

// --- Сотрудники и таймеры ------------------------------------------------------

function employeeItem(employee, draftTask) {
  const name = el("div", { className: "employee-name" }, employee.name);

  if (employee.running) {
    const { task, started_at } = employee.running;
    const stopButton = el("button", { type: "button", className: "stop" }, "Стоп");
    stopButton.addEventListener("click", () =>
      withButton(stopButton, () => api("POST", `/api/employees/${employee.id}/stop`)));
    const status = el("div", { className: "status" },
      el("span", { className: "task" }, "▶ ", task),
      el("span", {},
        el("span", { className: "elapsed", dataset: { started: started_at } },
          formatDuration(nowSeconds() - started_at)),
        " ", stopButton));
    return el("li", { className: "employee" }, name, status);
  }

  const input = el("input", {
    placeholder: "Над какой задачей работаете?", maxLength: 500, required: true,
    value: draftTask || "", autocomplete: "off",
  });
  input.dataset.employeeId = employee.id;
  const startButton = el("button", { type: "submit" }, "Старт");
  const form = el("form", { className: "row" }, input, startButton);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    withButton(startButton, () =>
      api("POST", `/api/employees/${employee.id}/start`, { task: input.value }));
  });
  return el("li", { className: "employee" }, name, form);
}

function renderEmployees() {
  // Сохраняем недописанные описания задач, чтобы перерисовка их не стирала.
  const drafts = {};
  document.querySelectorAll("input[data-employee-id]").forEach((input) => {
    drafts[input.dataset.employeeId] = input.value;
  });

  $("#employees").replaceChildren(
    ...state.employees.map((e) => employeeItem(e, drafts[e.id])));
  $("#employees-empty").hidden = state.employees.length > 0;

  const select = $("#report-employee");
  const selected = select.value;
  select.replaceChildren(
    el("option", { value: "" }, "Все"),
    ...state.employees.map((e) => el("option", { value: String(e.id) }, e.name)));
  select.value = state.employees.some((e) => String(e.id) === selected) ? selected : "";
}

function tickTimers() {
  document.querySelectorAll(".elapsed[data-started]").forEach((node) => {
    node.textContent = formatDuration(nowSeconds() - Number(node.dataset.started));
  });
}

// --- Отчёт ---------------------------------------------------------------------

function renderReport(report) {
  const container = $("#report");

  if (report.entries.length === 0) {
    container.replaceChildren(
      el("p", { className: "muted" }, "За выбранный период записей нет. Итого: 0:00:00"));
    return;
  }

  const totals = el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Сотрудник"), el("th", { className: "num" }, "Всего"))),
    el("tbody", {}, ...report.totals.map((t) =>
      el("tr", {}, el("td", {}, t.name), el("td", { className: "num" }, formatDuration(t.seconds))))),
    el("tfoot", {}, el("tr", {},
      el("td", {}, "Итого"), el("td", { className: "num" }, formatDuration(report.total_seconds)))));

  const entries = el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "Сотрудник"), el("th", {}, "Задача"), el("th", {}, "Начало"),
      el("th", {}, "Конец"), el("th", { className: "num" }, "Время"))),
    el("tbody", {}, ...report.entries.map((e) =>
      el("tr", {},
        el("td", {}, e.employee_name),
        el("td", {}, e.task),
        el("td", {}, formatDateTime(e.started_at)),
        el("td", {}, e.running ? "идёт сейчас" : formatDateTime(e.ended_at)),
        el("td", { className: "num" }, formatDuration(e.seconds))))));

  container.replaceChildren(
    el("div", { className: "table-wrap" }, totals),
    el("h3", {}, "Записи"),
    el("div", { className: "table-wrap" }, entries));
}

async function loadReport() {
  const params = new URLSearchParams({ from: $("#date-from").value, to: $("#date-to").value });
  const employeeId = $("#report-employee").value;
  if (employeeId) params.set("employee_id", employeeId);
  renderReport(await api("GET", `/api/report?${params}`));
  state.reportShown = true;
}

// --- Загрузка и обработчики ------------------------------------------------------

async function refresh() {
  const data = await api("GET", "/api/employees");
  state.employees = data.employees;
  renderEmployees();
  if (state.reportShown) await loadReport();
}

$("#employee-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("#employee-name");
  withButton(event.submitter || event.target.querySelector("button"), async () => {
    await api("POST", "/api/employees", { name: input.value });
    input.value = "";
  });
});

$("#report-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if ($("#date-from").value > $("#date-to").value) {
    showError("Дата «с» позже даты «по»");
    return;
  }
  const button = event.submitter || event.target.querySelector("button");
  button.disabled = true;
  loadReport()
    .then(() => { $("#notice").hidden = true; })
    .catch((error) => showError(error.message))
    .finally(() => { button.disabled = false; });
});

const today = new Date();
$("#date-from").value = isoDate(new Date(today.getFullYear(), today.getMonth(), 1));
$("#date-to").value = isoDate(today);

setInterval(tickTimers, 1000);
refresh().catch((error) => showError(`Не удалось загрузить данные: ${error.message}`));
