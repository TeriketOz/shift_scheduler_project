"""
Бенчмарки оптимизатора расписания.
Генерирует синтетические данные разного размера, замеряет время
решения и печатает таблицу для главы 3 диплома.

Запуск:
    python manage.py shell -c "from scheduler import benchmarks; benchmarks.run()"
"""
import random
import time as _time
from datetime import date, time, timedelta

from django.db import transaction

from .models import Assignment, Employee, Shift, Skill, TimeOff
from .optimizer import ScheduleOptimizer

from django.contrib.auth.models import User

SIZES = [
    (5, 20),
    (10, 50),
    (20, 100),
    (50, 300),
]


def _cleanup():
    """Удалить тестовые данные бенчмарка (username начинается с 'bench_')."""
    Assignment.objects.filter(employee__user__username__startswith="bench_").delete()
    TimeOff.objects.filter(employee__user__username__startswith="bench_").delete()
    Employee.objects.filter(user__username__startswith="bench_").delete()
    User.objects.filter(username__startswith="bench_").delete()
    Shift.objects.filter(location="__bench__").delete()


@transaction.atomic
def _generate(num_employees: int, num_shifts: int, seed: int = 42):
    """Создать синтетических сотрудников и смены."""
    random.seed(seed)
    _cleanup()

    employees = []
    for i in range(num_employees):
        u = User.objects.create_user(
            username=f"bench_emp_{i}", password="x",
            first_name=f"Test{i}", last_name="Bench",
        )
        e = Employee.objects.create(
            user=u, position="Test", rank=random.randint(3, 6),
            hire_date=date(2020, 1, 1),
            max_hours_per_week=40,
            preferred_shift_type=random.choice(["DAY", "EVENING", "NIGHT", "ANY"]),
            role="EMPLOYEE",
        )
        employees.append(e)

    shift_defs = [
        ("DAY", time(8, 0), time(16, 0)),
        ("EVENING", time(16, 0), time(0, 0)),
        ("NIGHT", time(0, 0), time(8, 0)),
    ]
    start = date.today() + timedelta(days=30)  # подальше от реальных данных
    shifts = []
    for i in range(num_shifts):
        day_offset = i // 3
        stype, st, et = shift_defs[i % 3]
        shifts.append(Shift.objects.create(
            date=start + timedelta(days=day_offset),
            shift_type=stype, start_time=st, end_time=et,
            required_employees=max(1, num_employees // 10),
            location="__bench__",
        ))

    return start, start + timedelta(days=(num_shifts // 3) + 1)


def run():
    print()
    print("| Сотрудников | Смен | Время (с) | Статус     | Покрытие |")
    print("|-------------|------|-----------|------------|----------|")
    for num_emp, num_sh in SIZES:
        period_start, period_end = _generate(num_emp, num_sh)
        t0 = _time.perf_counter()
        result = ScheduleOptimizer().solve(period_start, period_end)
        elapsed = _time.perf_counter() - t0
        coverage = result.statistics.get("coverage_percent", "—") if result.statistics else "—"
        print(
            f"| {num_emp:>11} | {num_sh:>4} | {elapsed:>9.2f} | "
            f"{result.solver_status:<10} | {coverage!s:>7}% |"
        )
    _cleanup()
    print()