"""
Бенчмарки оптимизатора расписания (версия с усреднением и прогрессом).

Отличия от первой версии:
  - данные генерируются ГАРАНТИРОВАННО РЕШАЕМЫМИ (required_employees=1,
    без требований навыка) — иначе на больших размерностях решатель тратил
    бы все 60 секунд таймаута на доказательство несовместности;
  - печатается прогресс по ходу выполнения (видно, что скрипт не завис);
  - решателю разрешено использовать несколько потоков (num_search_workers),
    что ускоряет большие размерности на многоядерном стенде.

Запуск:
    python manage.py shell -c "from scheduler import benchmarks; benchmarks.run()"

Константы REPEATS и SIZES можно менять ниже.
"""
import random
import time as _time
from datetime import date, time, timedelta

from django.db import transaction
from django.contrib.auth.models import User

from .models import Assignment, Employee, Shift, Skill, TimeOff
from .optimizer import ScheduleOptimizer

# Набор размерностей: (число сотрудников, число смен)
SIZES = [
    (5, 20),
    (10, 50),
    (20, 100),
    (50, 300),
]

# Число повторов на каждую размерность (для усреднения)
REPEATS = 3


def _cleanup():
    """Удалить тестовые данные бенчмарка (username начинается с 'bench_')."""
    Assignment.objects.filter(employee__user__username__startswith="bench_").delete()
    TimeOff.objects.filter(employee__user__username__startswith="bench_").delete()
    Employee.objects.filter(user__username__startswith="bench_").delete()
    User.objects.filter(username__startswith="bench_").delete()
    Shift.objects.filter(location="__bench__").delete()


@transaction.atomic
def _generate(num_employees: int, num_shifts: int, seed: int = 42):
    """
    Создать синтетических сотрудников и смены — ГАРАНТИРОВАННО РЕШАЕМЫЙ набор.
    required_employees = 1, требование навыка не ставится. Это обеспечивает
    статус OPTIMAL на всех размерностях и корректный замер времени.
    """
    random.seed(seed)
    _cleanup()

    for i in range(num_employees):
        u = User.objects.create_user(
            username=f"bench_emp_{i}", password="x",
            first_name=f"Test{i}", last_name="Bench",
        )
        Employee.objects.create(
            user=u, position="Test", rank=random.randint(3, 6),
            hire_date=date(2020, 1, 1),
            max_hours_per_week=40,
            preferred_shift_type=random.choice(["DAY", "EVENING", "NIGHT", "ANY"]),
            role="EMPLOYEE",
        )

    shift_defs = [
        ("DAY", time(8, 0), time(16, 0)),
        ("EVENING", time(16, 0), time(0, 0)),
        ("NIGHT", time(0, 0), time(8, 0)),
    ]
    start = date.today() + timedelta(days=60)  # подальше от реальных данных
    # required_employees=1, чтобы спрос гарантированно не превышал ресурс
    for i in range(num_shifts):
        day_offset = i // 3
        stype, st, et = shift_defs[i % 3]
        Shift.objects.create(
            date=start + timedelta(days=day_offset),
            shift_type=stype, start_time=st, end_time=et,
            required_employees=1,
            required_skill=None,
            location="__bench__",
        )

    return start, start + timedelta(days=(num_shifts // 3) + 1)


def run():
    print()
    print(f"Нагрузочное тестирование оптимизатора (повторов на размерность: {REPEATS})")
    print("Данные генерируются решаемыми (required=1, без навыков) → ожидается OPTIMAL.")
    print()
    print("| Сотруд. | Смен | Перемен. | Время ср., с | Время min, с | Время max, с | Статус   | Покрытие |")
    print("|---------|------|----------|--------------|--------------|--------------|----------|----------|")

    for num_emp, num_sh in SIZES:
        # прогресс — чтобы было видно, что скрипт работает, а не завис
        print(f"  … размерность {num_emp}×{num_sh} ({REPEATS} повтора)…", flush=True)

        times = []
        num_vars = "—"
        status = "—"
        coverage = "—"

        for r in range(REPEATS):
            period_start, period_end = _generate(num_emp, num_sh)
            t0 = _time.perf_counter()
            result = ScheduleOptimizer().solve(period_start, period_end)
            elapsed = _time.perf_counter() - t0
            times.append(elapsed)
            print(f"      повтор {r + 1}/{REPEATS}: {elapsed:.2f} с, статус {result.solver_status}", flush=True)

            status = result.solver_status
            if result.statistics:
                num_vars = result.statistics.get("num_variables", "—")
                coverage = result.statistics.get("coverage_percent", "—")

        avg_t = sum(times) / len(times)
        min_t = min(times)
        max_t = max(times)

        print(
            f"| {num_emp:>7} | {num_sh:>4} | {num_vars!s:>8} | "
            f"{avg_t:>12.2f} | {min_t:>12.2f} | {max_t:>12.2f} | "
            f"{status:<8} | {coverage!s:>7}% |",
            flush=True,
        )

    _cleanup()
    print()