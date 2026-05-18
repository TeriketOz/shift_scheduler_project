import csv
from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import AssignmentForm, BulkShiftForm, EmployeeForm, ShiftForm, TimeOffForm
from .models import Assignment, Employee, Notification, Shift, Skill, TimeOff
from .notifications import (
    notify_optimization_complete,
    notify_shift_assigned,
    notify_timeoff_status,
    notify_uncovered_shifts,
)


# ---------- helpers ----------

def _role(user):
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return "ADMIN"
    emp = getattr(user, "employee", None)
    return emp.role if emp else None


def is_admin_or_hr(user):
    return user.is_superuser or _role(user) in ("ADMIN", "HR")


def is_admin(user):
    return user.is_superuser or _role(user) == "ADMIN"


# ---------- home ----------

@login_required
def home(request):
    if is_admin_or_hr(request.user):
        return redirect("scheduler:main")
    return redirect("scheduler:my_schedule")


# ---------- main ----------

@login_required
@user_passes_test(is_admin_or_hr)
def main_view(request):
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)

    total_employees = Employee.objects.count()
    week_shifts = Shift.objects.filter(date__gte=week_start, date__lte=week_end)
    week_shifts_count = week_shifts.count()

    # Покрытие за текущую неделю
    covered = 0
    for s in week_shifts:
        assigned = Assignment.objects.filter(shift=s).exclude(status="MISSED").count()
        if assigned >= s.required_employees:
            covered += 1
    coverage_pct = round(100 * covered / week_shifts_count, 1) if week_shifts_count else 0.0

    # Переработка за месяц: часы > 36 * число недель
    month_assignments = Assignment.objects.filter(
        shift__date__gte=month_start, shift__date__lte=today
    ).select_related("shift", "employee__user")
    hours_by_emp: dict[int, float] = {}
    names_by_emp: dict[int, str] = {}
    for a in month_assignments:
        hours_by_emp[a.employee_id] = hours_by_emp.get(a.employee_id, 0) + a.shift.duration_hours()
        names_by_emp[a.employee_id] = f"{a.employee.user.last_name} {a.employee.user.first_name[:1]}."
    weeks_in_month = max(1, ((today - month_start).days + 1) / 7)
    total_overtime = round(
        sum(max(0, h - 36 * weeks_in_month) for h in hours_by_emp.values()), 1
    )

    # Данные для Chart.js — часы по сотрудникам за месяц
    chart_data = {
        "labels": list(names_by_emp.values()) or ["нет данных"],
        "hours": [round(h, 1) for h in hours_by_emp.values()] or [0],
    }

    # Непокрытые смены на ближайшие 7 дней
    uncovered = []
    upcoming = Shift.objects.filter(date__gte=today, date__lte=today + timedelta(days=7))
    for s in upcoming:
        assigned = Assignment.objects.filter(shift=s).exclude(status="MISSED").count()
        if assigned < s.required_employees:
            uncovered.append({
                "shift": s, "assigned": assigned, "needed": s.required_employees,
            })

    from .analytics import analyze
    report = analyze(week_start, week_end)
    top_warnings = report.warnings[:3]
    top_recommendations = report.recommendations[:2]

    return render(request, "scheduler/main.html", {
        "total_employees": total_employees,
        "week_shifts_count": week_shifts_count,
        "coverage_pct": coverage_pct,
        "total_overtime": total_overtime,
        "chart_data": chart_data,
        "uncovered": uncovered,
        "top_warnings": top_warnings,
        "top_recommendations": top_recommendations,
    })

# ---------- employees ----------

@login_required
@user_passes_test(is_admin_or_hr)
def employees_list(request):
    qs = Employee.objects.select_related("user").prefetch_related("skills").order_by("user__last_name")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(user__last_name__icontains=q) | Q(user__first_name__icontains=q)
            | Q(user__username__icontains=q)
        )
    skill_id = request.GET.get("skill")
    if skill_id:
        qs = qs.filter(skills__id=skill_id)
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "scheduler/employees_list.html", {
        "page_obj": page, "q": q, "skills": Skill.objects.all(), "skill_id": skill_id,
    })


@login_required
@user_passes_test(is_admin_or_hr)
def employee_add(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Сотрудник создан")
        return redirect("scheduler:employees_list")
    return render(request, "scheduler/employee_form.html", {"form": form, "title": "Новый сотрудник"})


@login_required
@user_passes_test(is_admin_or_hr)
def employee_edit(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Сотрудник обновлён")
        return redirect("scheduler:employees_list")
    return render(request, "scheduler/employee_form.html", {"form": form, "title": f"Редактирование: {emp}"})


@login_required
@user_passes_test(is_admin)
def employee_delete(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        user = emp.user
        emp.delete()
        user.delete()
        messages.success(request, "Сотрудник удалён")
        return redirect("scheduler:employees_list")
    return render(request, "scheduler/employee_confirm_delete.html", {"object": emp})


# ---------- shifts ----------

@login_required
@user_passes_test(is_admin_or_hr)
def shifts_list(request):
    qs = Shift.objects.select_related("required_skill").order_by("-date", "start_time")
    start = request.GET.get("start")
    end = request.GET.get("end")
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "scheduler/shifts_list.html", {
        "page_obj": page, "start": start or "", "end": end or "",
    })


@login_required
@user_passes_test(is_admin_or_hr)
def shift_add(request):
    form = ShiftForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Смена создана")
        return redirect("scheduler:shifts_list")
    return render(request, "scheduler/shift_form.html", {"form": form, "title": "Новая смена"})


@login_required
@user_passes_test(is_admin_or_hr)
def shift_edit(request, pk):
    shift = get_object_or_404(Shift, pk=pk)
    form = ShiftForm(request.POST or None, instance=shift)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Смена обновлена")
        return redirect("scheduler:shifts_list")
    return render(request, "scheduler/shift_form.html", {"form": form, "title": "Редактирование смены"})


@login_required
@user_passes_test(is_admin)
def shift_delete(request, pk):
    shift = get_object_or_404(Shift, pk=pk)
    if request.method == "POST":
        shift.delete()
        messages.success(request, "Смена удалена")
        return redirect("scheduler:shifts_list")
    return render(request, "scheduler/shift_confirm_delete.html", {"object": shift})


@login_required
@user_passes_test(is_admin_or_hr)
@transaction.atomic
def shifts_bulk_add(request):
    form = BulkShiftForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        created = 0
        d = cd["start_date"]
        while d <= cd["end_date"]:
            for stype in cd["shift_types"]:
                s_str, e_str = BulkShiftForm.TIMES[stype]
                start_t = time(*map(int, s_str.split(":")))
                end_t = time(*map(int, e_str.split(":")))
                _, was = Shift.objects.get_or_create(
                    date=d, shift_type=stype,
                    defaults={
                        "start_time": start_t, "end_time": end_t,
                        "required_employees": cd["required_employees"],
                        "location": cd["location"],
                    },
                )
                if was:
                    created += 1
            d += timedelta(days=1)
        messages.success(request, f"Создано смен: {created}")
        return redirect("scheduler:shifts_list")
    return render(request, "scheduler/shifts_bulk_form.html", {"form": form})


# ---------- schedule ----------

@login_required
def schedule_calendar(request):
    return render(request, "scheduler/schedule_calendar.html")


SHIFT_COLORS = {"DAY": "#0d6efd", "EVENING": "#fd7e14", "NIGHT": "#6f42c1"}
SHIFT_ICONS = {"DAY": "☀", "EVENING": "🌇", "NIGHT": "🌙"}


@login_required
def schedule_events_json(request):
    start = request.GET.get("start")
    end = request.GET.get("end")
    qs = Assignment.objects.select_related(
        "shift", "employee__user"
    ).exclude(status="MISSED")
    if start:
        qs = qs.filter(shift__date__gte=start[:10])
    if end:
        qs = qs.filter(shift__date__lte=end[:10])
    if request.GET.get("mine") == "1":
        emp = getattr(request.user, "employee", None)
        qs = qs.filter(employee=emp) if emp else qs.none()

    events = []
    for a in qs:
        s = a.shift
        start_dt = datetime.combine(s.date, s.start_time)
        end_dt = datetime.combine(s.date, s.end_time)
        if s.end_time <= s.start_time:
            end_dt += timedelta(days=1)
        u = a.employee.user
        icon = SHIFT_ICONS.get(s.shift_type, "")
        title = f"{icon} {u.last_name}"
        events.append({
            "id": a.id,
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "color": SHIFT_COLORS.get(s.shift_type, "#6c757d"),
            "url": f"/admin/scheduler/assignment/{a.id}/change/",
        })
    return JsonResponse(events, safe=False)

@login_required
def my_schedule(request):
    return render(request, "scheduler/my_schedule.html")


@login_required
@user_passes_test(is_admin)
@require_POST
def run_optimizer(request):
    from .optimizer import ScheduleOptimizer

    try:
        start = datetime.strptime(request.POST["start"], "%Y-%m-%d").date()
        end = datetime.strptime(request.POST["end"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        messages.error(request, "Укажите корректные даты периода")
        return redirect("scheduler:schedule_calendar")

    if end < start:
        messages.error(request, "Дата окончания раньше даты начала")
        return redirect("scheduler:schedule_calendar")

    optimizer = ScheduleOptimizer()
    result = optimizer.solve(start, end)

    if result.solver_status in ("OPTIMAL", "FEASIBLE"):
        created = optimizer.save_to_db(result)
        st = result.statistics

        # Уведомления сотрудникам о новых назначениях
        emp_map = {e.id: e for e in optimizer.employees}
        shift_map = {s.id: s for s in optimizer.shifts}
        for emp_id, shift_id in result.assignments:
            emp = emp_map.get(emp_id)
            shift = shift_map.get(shift_id)
            if emp and shift:
                notify_shift_assigned(emp, shift)

        # Непокрытые смены — рассылка админам
        uncovered = [
            shift_map[sid] for sid in shift_map
            if sum(1 for _, j in result.assignments if j == sid)
               < shift_map[sid].required_employees
        ]
        if uncovered:
            from django.contrib.auth.models import User
            admin_users = User.objects.filter(
                is_superuser=True
            ) | User.objects.filter(employee__role="ADMIN")
            notify_uncovered_shifts(admin_users.distinct(), uncovered)

        # Админу — статистика
        notify_optimization_complete(request.user, st)

        messages.success(
            request,
            f"Оптимизация завершена ({result.solver_status}, {result.computation_time:.1f} с). "
            f"Создано назначений: {created}. "
            f"Покрытие: {st.get('coverage_percent')}%, "
            f"переработка: {st.get('total_overtime')} ч, "
            f"разброс часов: {st.get('fairness_metric')} ч, "
            f"предпочтения: {st.get('satisfied_preferences')}%."
        )
    else:
        messages.error(
            request,
            f"Оптимизация не удалась ({result.solver_status}): "
            f"{result.infeasibility_reason or 'решение не найдено'}"
        )

    return redirect("scheduler:schedule_calendar")

# ---------- timeoff ----------

@login_required
def timeoff_list(request):
    if is_admin_or_hr(request.user):
        qs = TimeOff.objects.select_related("employee__user").order_by("-created_at")
    else:
        emp = getattr(request.user, "employee", None)
        qs = TimeOff.objects.filter(employee=emp).order_by("-created_at") if emp else TimeOff.objects.none()
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "scheduler/timeoff_list.html", {"page_obj": page, "is_staff": is_admin_or_hr(request.user)})


@login_required
def timeoff_request(request):
    emp = getattr(request.user, "employee", None)
    if emp is None:
        messages.error(request, "У вашего пользователя нет карточки сотрудника")
        return redirect("scheduler:home")
    form = TimeOffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        to = form.save(commit=False)
        to.employee = emp
        to.status = "PENDING"
        to.save()
        messages.success(request, "Заявка отправлена")
        return redirect("scheduler:timeoff_list")
    return render(request, "scheduler/timeoff_form.html", {"form": form})


@login_required
@user_passes_test(is_admin)
@require_POST
def timeoff_approve(request, pk):
    to = get_object_or_404(TimeOff, pk=pk)
    to.status = "APPROVED"
    to.save(update_fields=["status"])
    notify_timeoff_status(to)
    messages.success(request, "Отгул одобрен")
    return redirect("scheduler:timeoff_list")


@login_required
@user_passes_test(is_admin)
@require_POST
def timeoff_reject(request, pk):
    to = get_object_or_404(TimeOff, pk=pk)
    to.status = "REJECTED"
    to.save(update_fields=["status"])
    notify_timeoff_status(to)
    messages.success(request, "Отгул отклонён")
    return redirect("scheduler:timeoff_list")


# ---------- reports ----------

def _reports_data(start: date, end: date):
    """Сводка по сотрудникам за период: часы, переработка, ночные, отгулы."""
    assignments = Assignment.objects.filter(
        shift__date__gte=start, shift__date__lte=end
    ).exclude(status="MISSED").select_related("shift", "employee__user")

    weeks = max(1, ((end - start).days + 1) / 7)
    desired = 36 * weeks

    per_emp: dict[int, dict] = {}
    for a in assignments:
        eid = a.employee_id
        if eid not in per_emp:
            u = a.employee.user
            per_emp[eid] = {
                "name": f"{u.last_name} {u.first_name} {u.username}",
                "hours": 0.0, "nights": 0,
            }
        per_emp[eid]["hours"] += a.shift.duration_hours()
        if a.shift.shift_type == "NIGHT":
            per_emp[eid]["nights"] += 1

    # Отгулы (одобренные), пересекающиеся с периодом
    timeoffs = TimeOff.objects.filter(
        status="APPROVED", start_date__lte=end, end_date__gte=start,
    ).values("employee_id").annotate(cnt=Count("id"))
    to_map = {t["employee_id"]: t["cnt"] for t in timeoffs}

    # Добавим сотрудников без назначений
    for e in Employee.objects.select_related("user"):
        if e.id not in per_emp:
            u = e.user
            per_emp[e.id] = {
                "name": f"{u.last_name} {u.first_name} {u.username}",
                "hours": 0.0, "nights": 0,
            }

    rows = []
    for eid, d in per_emp.items():
        rows.append({
            "name": d["name"],
            "hours": round(d["hours"], 1),
            "overtime": round(max(0, d["hours"] - desired), 1),
            "nights": d["nights"],
            "timeoffs": to_map.get(eid, 0),
        })
    rows.sort(key=lambda r: r["name"])
    return rows


def _parse_period(request):
    today = date.today()
    default_start = today.replace(day=1)
    start = request.GET.get("start") or default_start.isoformat()
    end = request.GET.get("end") or today.isoformat()
    try:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        s, e = default_start, today
    if e < s:
        s, e = e, s
    return s, e


@login_required
@user_passes_test(is_admin_or_hr)
def reports_view(request):
    start, end = _parse_period(request)
    rows = _reports_data(start, end)
    chart_data = {
        "labels": [r["name"].split()[0] for r in rows],
        "hours": [r["hours"] for r in rows],
    }
    return render(request, "scheduler/reports.html", {
        "rows": rows, "start": start, "end": end, "chart_data": chart_data,
    })


@login_required
@user_passes_test(is_admin_or_hr)
def reports_export_csv(request):
    start, end = _parse_period(request)
    rows = _reports_data(start, end)
    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="report_{start}_{end}.csv"'
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["ФИО", "Часов отработано", "Переработка", "Ночных смен", "Отгулов"])
    for r in rows:
        writer.writerow([r["name"], r["hours"], r["overtime"], r["nights"], r["timeoffs"]])
    return response

@login_required
def notifications_list(request):
    qs = Notification.objects.filter(user=request.user)
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "scheduler/notifications_list.html", {"page_obj": page})


@login_required
@require_POST
def mark_read(request, pk):
    note = get_object_or_404(Notification, pk=pk, user=request.user)
    note.is_read = True
    note.save(update_fields=["is_read"])
    return redirect("scheduler:notifications_list")


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    messages.success(request, "Все уведомления отмечены как прочитанные")
    return redirect("scheduler:notifications_list")


# ---------- assignments ----------

@login_required
@user_passes_test(is_admin_or_hr)
def assignments_list(request):
    qs = Assignment.objects.select_related(
        "shift", "employee__user"
    ).order_by("-shift__date", "shift__start_time")
    shift_id = request.GET.get("shift")
    if shift_id:
        qs = qs.filter(shift_id=shift_id)
    emp_id = request.GET.get("employee")
    if emp_id:
        qs = qs.filter(employee_id=emp_id)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "scheduler/assignments_list.html", {
        "page_obj": page,
        "shifts": Shift.objects.order_by("-date")[:90],
        "employees": Employee.objects.select_related("user").order_by("user__last_name"),
    })


@login_required
@user_passes_test(is_admin_or_hr)
def assignment_add(request):
    form = AssignmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Назначение создано")
        return redirect("scheduler:assignments_list")
    return render(request, "scheduler/assignment_form.html", {"form": form, "title": "Новое назначение"})


@login_required
@user_passes_test(is_admin)
def assignment_delete(request, pk):
    assignment = get_object_or_404(Assignment, pk=pk)
    if request.method == "POST":
        assignment.delete()
        messages.success(request, "Назначение удалено")
        return redirect("scheduler:assignments_list")
    return render(request, "scheduler/assignment_confirm_delete.html", {"object": assignment})


@login_required
@user_passes_test(is_admin_or_hr)
def analytics_view(request):
    from .analytics import analyze
    start, end = _parse_period(request)
    report = analyze(start, end)
    return render(request, "scheduler/analytics.html", {
        "report": report, "start": start, "end": end,
    })