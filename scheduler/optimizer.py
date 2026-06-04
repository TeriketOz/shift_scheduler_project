"""
Модуль автоматической оптимизации графика смен.

Задача относится к классу Shift Scheduling Problem / Nurse Rostering Problem
и решается методом целочисленного линейного программирования (ЦЛП) с помощью
CP-SAT solver из библиотеки Google OR-Tools.

МАТЕМАТИЧЕСКАЯ ПОСТАНОВКА
=========================

Множества:
    I — множество сотрудников (Employee),
    J — множество смен в плановом периоде (Shift),
    W — множество календарных недель периода,
    D — множество дат периода.

Параметры:
    d_j          — длительность смены j в часах (в целочисленном представлении
                   используются минуты, затем переводятся обратно);
    r_j          — требуемое число сотрудников на смене j;
    H_i          — максимум часов в неделю для сотрудника i (max_hours_per_week);
    S_j ⊆ Skills — требуемый навык на смене j (может быть ∅);
    K_i ⊆ Skills — навыки сотрудника i;
    T_i          — множество дат одобренных отгулов сотрудника i;
    p_i          — предпочитаемый тип смены сотрудника i (DAY/EVENING/NIGHT/ANY).

Переменные решения:
    x[i, j] ∈ {0, 1} — сотрудник i назначен на смену j.

Жёсткие ограничения:
    H1  ∀ j ∈ J:      Σ_{i ∈ I} x[i, j] ≥ r_j
    H2  ∀ i ∈ I, w ∈ W:  Σ_{j ∈ w} d_j · x[i, j] ≤ H_i
    H3  x[i, j] = 0, если S_j ≠ ∅ и S_j ⊄ K_i
    H4  ∀ i ∈ I, d ∈ D:  Σ_{j: date_j = d} x[i, j] ≤ 1
    H5  ∀ i ∈ I, ∀ (j1, j2) с интервалом отдыха < 12 ч:
            x[i, j1] + x[i, j2] ≤ 1
    H6  ∀ i ∈ I, для каждой тройки последовательных дат (d, d+1, d+2):
            Σ x[i, j] для NIGHT-смен в эти дни ≤ 2
    H7  x[i, j] = 0, если date_j ∈ T_i

Мягкие ограничения (в целевой функции):
    S1  overtime[i, w] ≥ worked_hours[i, w] − 36,  overtime[i, w] ≥ 0
    S2  max_hours_var ≥ total_hours[i],  min_hours_var ≤ total_hours[i]
    S3  для каждого назначения с shift_type ≠ p_i (и p_i ≠ ANY) — штраф
    S4  аналогично S2, но только по ночным сменам (night_max − night_min)

Целевая функция:
    minimize  W1·Σ overtime[i,w]
            + W2·(max_hours_var − min_hours_var)
            + W3·Σ violated_preferences
            + W4·(night_max − night_min)

Веса W1..W4 настраиваемы (см. константы в начале модуля).
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from django.db import transaction
from ortools.sat.python import cp_model

from .models import Assignment, Employee, Shift, TimeOff

logger = logging.getLogger(__name__)

# --- Веса целевой функции (настраиваемы) ---
W1_OVERTIME = 10       # штраф за переработку
W2_FAIRNESS = 5        # штраф за неравномерность часов
W3_PREFERENCE = 3      # штраф за нарушение предпочтений
W4_NIGHT_FAIR = 2      # штраф за неравномерность ночных смен
W5_WEEKEND_OFF = 8   # штраф за работу в выходные для тех, кто их предпочитает

# --- Прочие параметры ---
DESIRED_WEEKLY_HOURS = 36      # желаемая нагрузка (выше — overtime в модели, S1)
TK_WEEKLY_NORM_HOURS = 40      # норма по ст. 91/152 ТК РФ (выше — сверхурочные)
MIN_REST_MINUTES = 12 * 60     # минимальный отдых между сменами
SOLVER_TIME_LIMIT_SECONDS = 60


@dataclass
class ScheduleResult:
    """Результат работы оптимизатора."""
    assignments: list[tuple[int, int]] = field(default_factory=list)  # (employee_id, shift_id)
    objective_value: float = 0.0
    solver_status: str = "UNKNOWN"
    statistics: dict = field(default_factory=dict)
    computation_time: float = 0.0
    infeasibility_reason: Optional[str] = None


class ScheduleOptimizer:
    """Построение оптимального расписания смен методом ЦЛП (CP-SAT)."""

    STATUS_MAP = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }

    def __init__(self):
        self.model: cp_model.CpModel | None = None
        self.solver: cp_model.CpSolver | None = None
        self.x: dict[tuple[int, int], cp_model.IntVar] = {}
        self.employees: list[Employee] = []
        self.shifts: list[Shift] = []
        self.period_start: date | None = None
        self.period_end: date | None = None

    # ------------------------------------------------------------------ #
    # Публичный API                                                       #
    # ------------------------------------------------------------------ #

    def solve(self, period_start: date, period_end: date) -> ScheduleResult:
        """
        Построить оптимальное расписание на период [period_start, period_end].
        Загружает данные из БД, строит модель CP-SAT, запускает солвер,
        возвращает ScheduleResult.
        """
        t0 = _time.perf_counter()
        self.period_start = period_start
        self.period_end = period_end

        self._load_data()

        if not self.shifts:
            return ScheduleResult(
                solver_status="INFEASIBLE",
                infeasibility_reason="В выбранном периоде нет смен для планирования",
                computation_time=_time.perf_counter() - t0,
            )
        if not self.employees:
            return ScheduleResult(
                solver_status="INFEASIBLE",
                infeasibility_reason="В системе нет сотрудников",
                computation_time=_time.perf_counter() - t0,
            )

        # Эвристическая проверка осуществимости по человеко-часам
        reason = self._quick_feasibility_check()
        if reason:
            return ScheduleResult(
                solver_status="INFEASIBLE",
                infeasibility_reason=reason,
                computation_time=_time.perf_counter() - t0,
            )

        self.model = cp_model.CpModel()
        self._create_variables()
        self._add_hard_constraints()
        objective_terms = self._add_soft_constraints_and_objective()
        self.model.Minimize(sum(objective_terms))

        self.solver = cp_model.CpSolver()
        self.solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT_SECONDS
        self.solver.parameters.log_search_progress = False

        logger.info(
            "Запуск CP-SAT: employees=%d, shifts=%d, vars=%d",
            len(self.employees), len(self.shifts), len(self.x),
        )
        status = self.solver.Solve(self.model)
        elapsed = _time.perf_counter() - t0

        result = ScheduleResult(
            solver_status=self.STATUS_MAP.get(status, "UNKNOWN"),
            computation_time=elapsed,
        )

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            result.assignments = [
                (i_id, j_id)
                for (i_id, j_id), var in self.x.items()
                if self.solver.Value(var) == 1
            ]
            result.objective_value = self.solver.ObjectiveValue()
            result.statistics = self._collect_statistics(result.assignments)
        elif status == cp_model.INFEASIBLE:
            result.infeasibility_reason = (
                "Модель несовместна: невозможно удовлетворить все жёсткие ограничения. "
                "Попробуйте снизить требуемое число сотрудников, добавить персонал "
                "или расширить квалификации."
            )
        else:
            result.infeasibility_reason = f"Солвер не нашёл решения (status={result.solver_status})"

        logger.info("Результат: %s, время=%.2fс", result.solver_status, elapsed)
        return result

    @transaction.atomic
    def save_to_db(self, result: ScheduleResult) -> int:
        """
        Сохранить результат в БД. В одной транзакции:
        1) удаляет старые Assignment со статусом PLANNED в периоде;
        2) создаёт новые назначения из result.assignments.
        Возвращает число созданных записей.
        """
        if not result.assignments:
            return 0
        shift_ids = [s.id for s in Shift.objects.filter(
            date__gte=self.period_start, date__lte=self.period_end
        )]
        Assignment.objects.filter(shift_id__in=shift_ids, status="PLANNED").delete()
        objs = [
            Assignment(shift_id=j_id, employee_id=i_id, status="PLANNED")
            for (i_id, j_id) in result.assignments
        ]
        Assignment.objects.bulk_create(objs, ignore_conflicts=True)
        logger.info("Сохранено назначений: %d", len(objs))
        return len(objs)

    # ------------------------------------------------------------------ #
    # Загрузка данных                                                     #
    # ------------------------------------------------------------------ #

    def _load_data(self):
        self.employees = list(
            Employee.objects.select_related("user").prefetch_related("skills")
        )
        self.shifts = list(
            Shift.objects.filter(date__gte=self.period_start, date__lte=self.period_end)
            .select_related("required_skill")
            .order_by("date", "start_time")
        )
        # Отгулы: employee_id -> множество дат
        self._timeoff: dict[int, set[date]] = {}
        for t in TimeOff.objects.filter(status="APPROVED"):
            days = self._timeoff.setdefault(t.employee_id, set())
            d = t.start_date
            while d <= t.end_date:
                days.add(d)
                d += timedelta(days=1)

    def _quick_feasibility_check(self) -> Optional[str]:
        required_hours = sum(s.duration_hours() * s.required_employees for s in self.shifts)
        num_weeks = max(1, ((self.period_end - self.period_start).days + 1) / 7)
        available_hours = sum(e.max_hours_per_week for e in self.employees) * num_weeks
        if required_hours > available_hours:
            return (
                f"Недостаточно человеко-часов: требуется {required_hours:.0f}, "
                f"доступно {available_hours:.0f}"
            )
        return None

    # ------------------------------------------------------------------ #
    # Построение модели                                                   #
    # ------------------------------------------------------------------ #

    def _shift_minutes(self, shift: Shift) -> int:
        return int(round(shift.duration_hours() * 60))

    def _shift_start_dt(self, shift: Shift) -> datetime:
        return datetime.combine(shift.date, shift.start_time)

    def _shift_end_dt(self, shift: Shift) -> datetime:
        start = self._shift_start_dt(shift)
        end = datetime.combine(shift.date, shift.end_time)
        if shift.end_time <= shift.start_time:
            end += timedelta(days=1)
        return end

    def _iso_week(self, d: date) -> tuple[int, int]:
        iso = d.isocalendar()
        return (iso[0], iso[1])

    def _create_variables(self):
        self.x = {}
        for e in self.employees:
            for s in self.shifts:
                self.x[(e.id, s.id)] = self.model.NewBoolVar(f"x_e{e.id}_s{s.id}")

    def _add_hard_constraints(self):
        m = self.model

        # --- H1: покрытие потребности ---
        for s in self.shifts:
            m.Add(sum(self.x[(e.id, s.id)] for e in self.employees) >= s.required_employees)

        # --- H3: квалификация, H7: отгулы (форсируем x=0 сразу) ---
        for e in self.employees:
            emp_skill_ids = {sk.id for sk in e.skills.all()}
            timeoff_days = self._timeoff.get(e.id, set())
            for s in self.shifts:
                if s.required_skill_id and s.required_skill_id not in emp_skill_ids:
                    m.Add(self.x[(e.id, s.id)] == 0)
                if s.date in timeoff_days:
                    m.Add(self.x[(e.id, s.id)] == 0)

        # --- H2: лимит часов в неделю ---
        shifts_by_week: dict[tuple[int, int], list[Shift]] = {}
        for s in self.shifts:
            shifts_by_week.setdefault(self._iso_week(s.date), []).append(s)
        for e in self.employees:
            weekly_limit_minutes = e.max_hours_per_week * 60
            for wk, wk_shifts in shifts_by_week.items():
                m.Add(
                    sum(self.x[(e.id, s.id)] * self._shift_minutes(s) for s in wk_shifts)
                    <= weekly_limit_minutes
                )

        # --- H4: не более одной смены в день ---
        shifts_by_date: dict[date, list[Shift]] = {}
        for s in self.shifts:
            shifts_by_date.setdefault(s.date, []).append(s)
        for e in self.employees:
            for d, d_shifts in shifts_by_date.items():
                if len(d_shifts) > 1:
                    m.Add(sum(self.x[(e.id, s.id)] for s in d_shifts) <= 1)

        # --- H5: отдых ≥ 12 часов между сменами ---
        # Сортируем смены по времени начала и проверяем пары в скользящем окне.
        sorted_shifts = sorted(self.shifts, key=lambda s: self._shift_start_dt(s))
        for i, s1 in enumerate(sorted_shifts):
            end1 = self._shift_end_dt(s1)
            for s2 in sorted_shifts[i + 1:]:
                start2 = self._shift_start_dt(s2)
                gap_minutes = (start2 - end1).total_seconds() / 60
                if gap_minutes >= MIN_REST_MINUTES:
                    # дальше все смены ещё позже — прерываем
                    if start2 - end1 > timedelta(days=2):
                        break
                    continue
                if s1.id == s2.id:
                    continue
                for e in self.employees:
                    m.Add(self.x[(e.id, s1.id)] + self.x[(e.id, s2.id)] <= 1)

        # --- H6: не более 2 ночных подряд ---
        night_by_date: dict[date, list[Shift]] = {}
        for s in self.shifts:
            if s.shift_type == "NIGHT":
                night_by_date.setdefault(s.date, []).append(s)
        dates_with_night = sorted(night_by_date.keys())
        for d in dates_with_night:
            d2, d3 = d + timedelta(days=1), d + timedelta(days=2)
            if d2 in night_by_date and d3 in night_by_date:
                trio = night_by_date[d] + night_by_date[d2] + night_by_date[d3]
                for e in self.employees:
                    m.Add(sum(self.x[(e.id, s.id)] for s in trio) <= 2)

    def _add_soft_constraints_and_objective(self) -> list:
        m = self.model
        terms = []

        total_minutes_per_emp: dict[int, cp_model.LinearExpr] = {}
        for e in self.employees:
            total_minutes_per_emp[e.id] = sum(
                self.x[(e.id, s.id)] * self._shift_minutes(s) for s in self.shifts
            )

        # --- S1: переработка ---
        desired_weekly_minutes = DESIRED_WEEKLY_HOURS * 60
        shifts_by_week: dict[tuple[int, int], list[Shift]] = {}
        for s in self.shifts:
            shifts_by_week.setdefault(self._iso_week(s.date), []).append(s)

        for e in self.employees:
            for wk, wk_shifts in shifts_by_week.items():
                worked = sum(self.x[(e.id, s.id)] * self._shift_minutes(s) for s in wk_shifts)
                ot = m.NewIntVar(0, e.max_hours_per_week * 60, f"ot_e{e.id}_w{wk[0]}_{wk[1]}")
                m.Add(ot >= worked - desired_weekly_minutes)
                terms.append(W1_OVERTIME * ot)

        # --- S2: справедливость общих часов ---
        max_total = m.NewIntVar(0, 10 ** 6, "max_total_min")
        min_total = m.NewIntVar(0, 10 ** 6, "min_total_min")
        for e in self.employees:
            m.Add(max_total >= total_minutes_per_emp[e.id])
            m.Add(min_total <= total_minutes_per_emp[e.id])
        terms.append(W2_FAIRNESS * (max_total - min_total))

        # --- S3: нарушенные предпочтения ---
        pref_violations = []
        for e in self.employees:
            if e.preferred_shift_type == "ANY":
                continue
            for s in self.shifts:
                if s.shift_type != e.preferred_shift_type:
                    pref_violations.append(self.x[(e.id, s.id)])
        if pref_violations:
            terms.append(W3_PREFERENCE * sum(pref_violations))

        # --- S4: равномерность ночных смен ---
        night_shifts = [s for s in self.shifts if s.shift_type == "NIGHT"]
        if night_shifts:
            night_max = m.NewIntVar(0, len(night_shifts), "night_max")
            night_min = m.NewIntVar(0, len(night_shifts), "night_min")
            for e in self.employees:
                night_count = sum(self.x[(e.id, s.id)] for s in night_shifts)
                m.Add(night_max >= night_count)
                m.Add(night_min <= night_count)
            terms.append(W4_NIGHT_FAIR * (night_max - night_min))
        # --- S5: предпочтение выходных в субботу и воскресенье ---
        weekend_violations = []
        for e in self.employees:
            if not getattr(e, "prefers_weekend_off", False):
                continue
            for s in self.shifts:
                if s.date.weekday() in (5, 6):  # сб=5, вс=6
                    weekend_violations.append(self.x[(e.id, s.id)])
        if weekend_violations:
            terms.append(W5_WEEKEND_OFF * sum(weekend_violations))

        return terms

    # ------------------------------------------------------------------ #
    # Статистика                                                          #
    # ------------------------------------------------------------------ #

    def _collect_statistics(self, assignments: list[tuple[int, int]]) -> dict:
        shift_by_id = {s.id: s for s in self.shifts}
        emp_ids = {e.id for e in self.employees}

        # Покрытие
        assigned_per_shift: dict[int, int] = {}
        for _, j_id in assignments:
            assigned_per_shift[j_id] = assigned_per_shift.get(j_id, 0) + 1
        covered = sum(
            1 for s in self.shifts
            if assigned_per_shift.get(s.id, 0) >= s.required_employees
        )
        coverage_percent = round(100 * covered / len(self.shifts), 1) if self.shifts else 0.0

        # Часы по сотрудникам (всего за период) — для метрики справедливости
        hours_per_emp = {eid: 0.0 for eid in emp_ids}
        # Часы по сотрудникам и неделям — для корректного расчёта переработки
        hours_per_emp_week: dict[tuple[int, tuple[int, int]], float] = {}
        for (i_id, j_id) in assignments:
            shift = shift_by_id[j_id]
            dur = shift.duration_hours()
            hours_per_emp[i_id] += dur
            wk = self._iso_week(shift.date)
            hours_per_emp_week[(i_id, wk)] = hours_per_emp_week.get((i_id, wk), 0.0) + dur

        # Переработка считается ПОНЕДЕЛЬНО относительно нормы ТК РФ (40 ч/нед, ст. 152):
        # суммируются часы, отработанные сверх 40 в каждой отдельной неделе.
        total_overtime = sum(
            max(0.0, h - TK_WEEKLY_NORM_HOURS) for h in hours_per_emp_week.values()
        )
        if hours_per_emp:
            fairness = round(max(hours_per_emp.values()) - min(hours_per_emp.values()), 1)
        else:
            fairness = 0.0

        # Удовлетворённые предпочтения
        emp_by_id = {e.id: e for e in self.employees}
        satisfied = 0
        total_prefs = 0
        for (i_id, j_id) in assignments:
            e = emp_by_id[i_id]
            if e.preferred_shift_type != "ANY":
                total_prefs += 1
                if shift_by_id[j_id].shift_type == e.preferred_shift_type:
                    satisfied += 1
        satisfied_pct = round(100 * satisfied / total_prefs, 1) if total_prefs else 100.0

        return {
            "num_variables": len(self.x),
            "num_constraints": self.model.Proto().constraints.__len__() if self.model else 0,
            "num_assignments": len(assignments),
            "coverage_percent": coverage_percent,
            "total_overtime": round(total_overtime, 1),
            "fairness_metric": fairness,
            "satisfied_preferences": satisfied_pct,
            "objective_value": round(self.solver.ObjectiveValue(), 2) if self.solver else 0.0,
        }