"""
Тесты на pytest-django для ядра системы планирования смен.
Покрывают алгоритм оптимизации (жёсткие ограничения, infeasible-кейс)
и базовые CRUD-view.
"""
from datetime import date, time, timedelta

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from scheduler.models import (
    Assignment, Employee, Shift, Skill, TimeOff,
)
from scheduler.optimizer import ScheduleOptimizer


# --------------------------------------------------------------------------- #
# Фикстуры                                                                     #
# --------------------------------------------------------------------------- #

@pytest.fixture
def admin_user(db):
    user = User.objects.create_user(
        username="test_admin", password="pass12345",
        is_staff=True, is_superuser=True,
    )
    return user


@pytest.fixture
def skills(db):
    return {
        "welder": Skill.objects.create(name="Сварщик"),
        "electric": Skill.objects.create(name="Электрик"),
        "height": Skill.objects.create(name="Допуск к высоте"),
    }


@pytest.fixture
def employees(db, skills):
    """Пять сотрудников с разными навыками и предпочтениями."""
    Group.objects.get_or_create(name="Employees")
    emps = []
    data = [
        ("emp1", "Иванов", "DAY", 40, ["electric"]),
        ("emp2", "Петров", "EVENING", 40, ["electric", "welder"]),
        ("emp3", "Сидоров", "NIGHT", 40, ["electric"]),
        ("emp4", "Козлов", "ANY", 40, ["electric", "height"]),
        ("emp5", "Новиков", "ANY", 40, ["welder"]),
    ]
    for username, last, pref, maxh, skill_keys in data:
        u = User.objects.create_user(username=username, password="pass", last_name=last)
        e = Employee.objects.create(
            user=u, position="Электромонтёр", rank=4,
            hire_date=date(2020, 1, 1), max_hours_per_week=maxh,
            preferred_shift_type=pref, role="EMPLOYEE",
        )
        for k in skill_keys:
            e.skills.add(skills[k])
        emps.append(e)
    return emps


@pytest.fixture
def week_shifts(db):
    """10 смен на неделю: 5 дневных + 5 вечерних, по 1 человеку."""
    start = date.today() + timedelta(days=7)
    shifts = []
    for i in range(5):
        d = start + timedelta(days=i)
        shifts.append(Shift.objects.create(
            date=d, shift_type="DAY", start_time=time(8, 0), end_time=time(16, 0),
            required_employees=1,
        ))
        shifts.append(Shift.objects.create(
            date=d, shift_type="EVENING", start_time=time(16, 0), end_time=time(0, 0),
            required_employees=1,
        ))
    return shifts


# --------------------------------------------------------------------------- #
# Тесты оптимизатора                                                           #
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_optimizer_covers_all_shifts(employees, week_shifts):
    """H1: после оптимизации все смены покрыты нужным числом сотрудников."""
    period_start = week_shifts[0].date
    period_end = week_shifts[-1].date

    result = ScheduleOptimizer().solve(period_start, period_end)

    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    # проверяем каждое требование
    for s in week_shifts:
        assigned = sum(1 for _, j_id in result.assignments if j_id == s.id)
        assert assigned >= s.required_employees, (
            f"Смена {s} покрыта только {assigned} из {s.required_employees}"
        )
    assert result.statistics["coverage_percent"] == 100.0


@pytest.mark.django_db
def test_optimizer_respects_max_hours(skills):
    """H2: сотрудник с max_hours=20 не должен получить больше 20 часов/нед."""
    u = User.objects.create_user(username="limited", password="pass")
    emp = Employee.objects.create(
        user=u, position="Test", rank=3, hire_date=date(2020, 1, 1),
        max_hours_per_week=20, preferred_shift_type="ANY",
    )
    # запасной сотрудник, чтобы смены вообще покрылись
    u2 = User.objects.create_user(username="backup", password="pass")
    Employee.objects.create(
        user=u2, position="Test", rank=3, hire_date=date(2020, 1, 1),
        max_hours_per_week=40, preferred_shift_type="ANY",
    )

    start = date.today() + timedelta(days=7)
    shifts = []
    for i in range(5):
        d = start + timedelta(days=i)
        shifts.append(Shift.objects.create(
            date=d, shift_type="DAY", start_time=time(8, 0), end_time=time(16, 0),
            required_employees=1,
        ))

    result = ScheduleOptimizer().solve(start, start + timedelta(days=4))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")

    # Считаем часы ограниченного сотрудника
    shift_ids = {s.id: s for s in shifts}
    emp_hours = sum(
        shift_ids[j_id].duration_hours()
        for i_id, j_id in result.assignments if i_id == emp.id
    )
    assert emp_hours <= 20, f"Сотрудник получил {emp_hours} ч при лимите 20"


@pytest.mark.django_db
def test_optimizer_respects_skills(skills):
    """H3: смена с required_skill должна достаться только обладателю навыка."""
    # Один сотрудник со сварщиком, трое без
    u1 = User.objects.create_user(username="welder_only", password="pass")
    welder = Employee.objects.create(
        user=u1, position="Сварщик", rank=5, hire_date=date(2020, 1, 1),
        max_hours_per_week=40, preferred_shift_type="ANY",
    )
    welder.skills.add(skills["welder"])

    for i in range(3):
        u = User.objects.create_user(username=f"other{i}", password="pass")
        Employee.objects.create(
            user=u, position="Test", rank=3, hire_date=date(2020, 1, 1),
            max_hours_per_week=40, preferred_shift_type="ANY",
        )

    d = date.today() + timedelta(days=7)
    shift = Shift.objects.create(
        date=d, shift_type="DAY", start_time=time(8, 0), end_time=time(16, 0),
        required_employees=1, required_skill=skills["welder"],
    )

    result = ScheduleOptimizer().solve(d, d)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")

    assigned_ids = [i_id for i_id, j_id in result.assignments if j_id == shift.id]
    assert assigned_ids == [welder.id], "На смену должен быть назначен только сварщик"


@pytest.mark.django_db
def test_optimizer_respects_rest_period(skills):
    """H5: между дневной и вечерней сменой одного дня < 12 ч → не обе одному."""
    # два сотрудника, чтобы обе смены можно было покрыть
    u1 = User.objects.create_user(username="e1", password="pass")
    e1 = Employee.objects.create(
        user=u1, position="Test", rank=3, hire_date=date(2020, 1, 1),
        max_hours_per_week=40, preferred_shift_type="ANY",
    )
    u2 = User.objects.create_user(username="e2", password="pass")
    Employee.objects.create(
        user=u2, position="Test", rank=3, hire_date=date(2020, 1, 1),
        max_hours_per_week=40, preferred_shift_type="ANY",
    )

    d = date.today() + timedelta(days=7)
    morning = Shift.objects.create(
        date=d, shift_type="DAY", start_time=time(8, 0), end_time=time(16, 0),
        required_employees=1,
    )
    evening = Shift.objects.create(
        date=d, shift_type="EVENING", start_time=time(16, 0), end_time=time(0, 0),
        required_employees=1,
    )

    result = ScheduleOptimizer().solve(d, d)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")

    e1_shifts = {j_id for i_id, j_id in result.assignments if i_id == e1.id}
    assert not ({morning.id, evening.id} <= e1_shifts), (
        "Сотруднику назначены и утренняя, и вечерняя смена подряд — нарушено H5"
    )


@pytest.mark.django_db
def test_optimizer_infeasible():
    """Если сотрудников меньше, чем требуется — солвер возвращает INFEASIBLE."""
    # Один сотрудник на 10 смен одновременно — явно не решается
    u = User.objects.create_user(username="only_one", password="pass")
    Employee.objects.create(
        user=u, position="Test", rank=3, hire_date=date(2020, 1, 1),
        max_hours_per_week=40, preferred_shift_type="ANY",
    )
    start = date.today() + timedelta(days=7)
    for i in range(10):
        d = start + timedelta(days=i)
        Shift.objects.create(
            date=d, shift_type="DAY", start_time=time(8, 0), end_time=time(16, 0),
            required_employees=3,  # нужно 3, а у нас 1 сотрудник
        )

    result = ScheduleOptimizer().solve(start, start + timedelta(days=9))
    assert result.solver_status == "INFEASIBLE"
    assert result.infeasibility_reason, "Причина infeasible должна быть заполнена"


# --------------------------------------------------------------------------- #
# Тесты view                                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_create_shift_view(client, admin_user):
    """POST на shift_add создаёт смену и делает редирект."""
    client.force_login(admin_user)
    url = reverse("scheduler:shift_add")
    response = client.post(url, {
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "shift_type": "DAY",
        "start_time": "08:00",
        "end_time": "16:00",
        "required_employees": 2,
        "location": "Цех №1",
    })
    assert response.status_code == 302
    assert Shift.objects.filter(location="Цех №1").exists()


@pytest.mark.django_db
def test_employee_can_request_timeoff(client):
    """Авторизованный сотрудник может создать заявку на отгул со статусом PENDING."""
    Group.objects.get_or_create(name="Employees")
    u = User.objects.create_user(username="req_emp", password="pass")
    Employee.objects.create(
        user=u, position="Test", rank=3, hire_date=date(2020, 1, 1),
        max_hours_per_week=40, preferred_shift_type="ANY",
    )
    client.force_login(u)

    response = client.post(reverse("scheduler:timeoff_request"), {
        "start_date": (date.today() + timedelta(days=10)).isoformat(),
        "end_date": (date.today() + timedelta(days=12)).isoformat(),
        "reason": "Личные обстоятельства",
    })
    assert response.status_code == 302
    to = TimeOff.objects.get(employee__user=u)
    assert to.status == "PENDING"
    assert to.reason == "Личные обстоятельства"