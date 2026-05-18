"""
Анализатор табеля: проверяет готовое расписание на нарушения ТК РФ,
дисбалансы и формирует рекомендации по управлению персоналом.
Является элементом системы поддержки принятия решений (DSS).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from .models import Assignment, Employee, Shift

# Нормативы (ТК РФ + практика)
WEEKLY_HARD_LIMIT = 40       # ст. 91 ТК РФ
WEEKLY_DESIRED = 36          # порог сверхурочных (мягкий)
MONTHLY_NORM = 165           # типовая норма месяца при 40-часовой неделе
MAX_NIGHT_SHIFTS_PER_MONTH = 8  # медицинская рекомендация
UTILIZATION_LOW_THRESHOLD = 0.60
UTILIZATION_HIGH_THRESHOLD = 0.95

Severity = Literal["critical", "warning", "info"]


@dataclass
class Warning:
    severity: Severity  # critical=🔴, warning=🟠, info=🟡
    title: str
    message: str


@dataclass
class Recommendation:
    title: str
    message: str
    priority: int = 1  # 1=высокий, 2=средний, 3=низкий


@dataclass
class AnalyticsReport:
    period_start: date
    period_end: date
    warnings: list[Warning] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def analyze(period_start: date, period_end: date) -> AnalyticsReport:
    """Основная функция — собрать отчёт за период."""
    report = AnalyticsReport(period_start=period_start, period_end=period_end)

    shifts = list(Shift.objects.filter(
        date__gte=period_start, date__lte=period_end
    ).select_related("required_skill"))
    assignments = list(Assignment.objects.filter(
        shift__date__gte=period_start, shift__date__lte=period_end
    ).exclude(status="MISSED").select_related("shift", "employee__user"))
    employees = list(Employee.objects.select_related("user"))

    # Часы по сотрудникам
    hours_by_emp: dict[int, float] = {e.id: 0.0 for e in employees}
    nights_by_emp: dict[int, int] = {e.id: 0 for e in employees}
    weekly_hours: dict[tuple[int, int, int], float] = {}  # (emp_id, year, iso_week) -> hours

    for a in assignments:
        eid = a.employee_id
        h = a.shift.duration_hours()
        hours_by_emp[eid] = hours_by_emp.get(eid, 0) + h
        if a.shift.shift_type == "NIGHT":
            nights_by_emp[eid] = nights_by_emp.get(eid, 0) + 1
        iso = a.shift.date.isocalendar()
        wk = (eid, iso[0], iso[1])
        weekly_hours[wk] = weekly_hours.get(wk, 0) + h

    # Покрытие смен
    assigned_count: dict[int, int] = Counter(a.shift_id for a in assignments)
    uncovered = [s for s in shifts
                 if assigned_count.get(s.id, 0) < s.required_employees]

    # Длительность периода в неделях (для пересчёта нормы)
    days = (period_end - period_start).days + 1
    weeks = max(1.0, days / 7)
    period_norm = MONTHLY_NORM * (days / 30)  # нормируем к периоду

    # === ПРЕДУПРЕЖДЕНИЯ ===

    # 🔴 Превышение 40 ч/нед — нарушение ТК
    violators_40h = set()
    for (eid, year, wk), h in weekly_hours.items():
        if h > WEEKLY_HARD_LIMIT:
            violators_40h.add(eid)
    if violators_40h:
        names = _names(violators_40h, employees)
        report.warnings.append(Warning(
            severity="critical",
            title="Превышение недельного лимита 40 ч",
            message=f"Нарушение ст. 91 ТК РФ. Сотрудники: {names}. "
                    f"Требуется пересмотр расписания.",
        ))

    # 🟠 Переработка за месяц
    overworked = [(e, h) for e in employees
                  if (h := hours_by_emp.get(e.id, 0)) > period_norm * 1.05]
    if overworked:
        overworked.sort(key=lambda x: -x[1])
        lines = ", ".join(f"{_name(e)} ({h:.0f} ч)" for e, h in overworked[:5])
        report.warnings.append(Warning(
            severity="warning",
            title=f"Переработка за период ({len(overworked)} чел.)",
            message=f"Отработано выше нормы {period_norm:.0f} ч: {lines}. "
                    f"Часы свыше нормы оплачиваются как сверхурочные "
                    f"(ст. 152 ТК РФ, ×1.5/×2).",
        ))

    # 🟡 Недоработка за месяц
    underworked = [(e, h) for e in employees
                   if 0 < (h := hours_by_emp.get(e.id, 0)) < period_norm * 0.85]
    if underworked:
        lines = ", ".join(f"{_name(e)} ({h:.0f} ч)" for e, h in underworked[:5])
        report.warnings.append(Warning(
            severity="info",
            title=f"Недоработка ({len(underworked)} чел.)",
            message=f"Отработано ниже нормы {period_norm:.0f} ч: {lines}. "
                    f"При суммированном учёте требуется доплата до среднего "
                    f"заработка (ст. 155 ТК РФ).",
        ))

    # 🔴 Непокрытые смены
    if uncovered:
        report.warnings.append(Warning(
            severity="critical",
            title=f"Непокрытые смены: {len(uncovered)}",
            message=f"Из {len(shifts)} смен не заполнены {len(uncovered)} "
                    f"({100 * len(uncovered) / max(1, len(shifts)):.1f}%). "
                    f"Необходимо ручное назначение или изменение требований.",
        ))

    # 🟠 Избыток ночных смен
    night_excess = [(e, n) for e in employees
                    if (n := nights_by_emp.get(e.id, 0)) > MAX_NIGHT_SHIFTS_PER_MONTH]
    if night_excess:
        lines = ", ".join(f"{_name(e)} ({n})" for e, n in night_excess)
        report.warnings.append(Warning(
            severity="warning",
            title="Избыток ночных смен",
            message=f"Более {MAX_NIGHT_SHIFTS_PER_MONTH} ночных за период: {lines}. "
                    f"Медицинская рекомендация — не более 8 ночных в месяц.",
        ))

    # 🟡 Дисбаланс нагрузки
    active = [h for h in hours_by_emp.values() if h > 0]
    if active:
        spread = max(active) - min(active)
        if spread > 40:
            report.warnings.append(Warning(
                severity="info",
                title=f"Дисбаланс нагрузки: {spread:.0f} ч",
                message=f"Разница между максимально и минимально загруженным "
                        f"сотрудником составляет {spread:.0f} ч. "
                        f"Рекомендуется перераспределение.",
            ))

    # === РЕКОМЕНДАЦИИ (DSS) ===

    total_required_hours = sum(s.duration_hours() * s.required_employees for s in shifts)
    total_assigned_hours = sum(hours_by_emp.values())
    total_capacity = sum(e.max_hours_per_week for e in employees) * weeks
    utilization = total_assigned_hours / total_capacity if total_capacity else 0

    coverage = 1 - len(uncovered) / len(shifts) if shifts else 1

    # R1. Дефицит персонала
    if coverage < 0.95 and total_required_hours > total_capacity:
        deficit = total_required_hours - total_capacity
        extra_people = max(1, round(deficit / (WEEKLY_DESIRED * weeks)))
        report.recommendations.append(Recommendation(
            title="Недостаточно персонала",
            message=f"Дефицит {deficit:.0f} человеко-часов за период. "
                    f"Рекомендуется нанять {extra_people} дополнительного "
                    f"сотрудника или увеличить max_hours_per_week у существующих.",
            priority=1,
        ))

    # R2. Избыток персонала
    if utilization < UTILIZATION_LOW_THRESHOLD and coverage > 0.95:
        extra = round((UTILIZATION_LOW_THRESHOLD - utilization) * len(employees))
        report.recommendations.append(Recommendation(
            title="Персонал недозагружен",
            message=f"Коэффициент использования персонала {utilization*100:.0f}% "
                    f"(нормально 60–90%). Можно либо сократить ~{extra} чел., "
                    f"либо добавить смены для расширения производства.",
            priority=2,
        ))

    # R3. Критическая зависимость от навыка
    skill_shifts = Counter()
    skill_employees = Counter()
    for s in shifts:
        if s.required_skill_id:
            skill_shifts[s.required_skill_id] += 1
    for e in employees:
        for skill in e.skills.all():
            skill_employees[skill.id] += 1
    for skill_id, shift_count in skill_shifts.items():
        emp_count = skill_employees.get(skill_id, 0)
        if shift_count >= 5 and emp_count <= 2:
            from .models import Skill
            skill = Skill.objects.filter(id=skill_id).first()
            if skill:
                report.recommendations.append(Recommendation(
                    title=f"Критическая зависимость: «{skill.name}»",
                    message=f"Навык требуется на {shift_count} сменах, "
                            f"но владеют только {emp_count} сотрудника. "
                            f"Риск срыва расписания при отсутствии. "
                    f"Рекомендуется обучить ещё 1–2 человек.",
                    priority=1,
                ))

    # R4. Высокая доля сверхурочных
    total_overtime = sum(max(0, h - period_norm) for h in hours_by_emp.values())
    if total_overtime > total_assigned_hours * 0.15:
        report.recommendations.append(Recommendation(
            title="Высокая доля сверхурочных",
            message=f"{total_overtime:.0f} ч ({total_overtime/total_assigned_hours*100:.0f}%) "
                    f"отработано сверх нормы. С учётом коэффициентов ТК РФ "
                    f"(×1.5 и ×2) это увеличивает ФОТ. Экономически целесообразно "
                    f"нанять дополнительного сотрудника.",
            priority=2,
        ))

    # R5. Неравномерное использование типов смен
    shift_type_count = Counter(s.shift_type for s in shifts)
    if shift_type_count.get("NIGHT", 0) == 0 and shift_type_count.get("DAY", 0) > 10:
        report.recommendations.append(Recommendation(
            title="Отсутствуют ночные смены",
            message="За период не запланировано ни одной ночной смены. "
                    "Если производство круглосуточное, проверьте график.",
            priority=3,
        ))

    report.recommendations.sort(key=lambda r: r.priority)

    # === СТАТИСТИКА ===
    report.stats = {
        "total_employees": len(employees),
        "active_employees": sum(1 for h in hours_by_emp.values() if h > 0),
        "total_shifts": len(shifts),
        "covered_shifts": len(shifts) - len(uncovered),
        "uncovered_shifts": len(uncovered),
        "coverage_pct": round(coverage * 100, 1),
        "required_hours": round(total_required_hours, 1),
        "assigned_hours": round(total_assigned_hours, 1),
        "capacity_hours": round(total_capacity, 1),
        "utilization_pct": round(utilization * 100, 1),
        "overtime_hours": round(total_overtime, 1),
        "avg_hours_per_employee": round(
            total_assigned_hours / max(1, len([h for h in hours_by_emp.values() if h > 0])), 1
        ),
        "shift_type_distribution": dict(shift_type_count),
    }

    return report


def _name(e: Employee) -> str:
    return f"{e.user.last_name} {e.user.first_name[:1]}."


def _names(ids: set[int], employees: list[Employee]) -> str:
    return ", ".join(_name(e) for e in employees if e.id in ids)