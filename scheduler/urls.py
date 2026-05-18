from django.urls import path
from . import views

app_name = "scheduler"

urlpatterns = [
    path("", views.home, name="home"),
    path("main/", views.main_view, name="main"),

    path("employees/", views.employees_list, name="employees_list"),
    path("employees/add/", views.employee_add, name="employee_add"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/delete/", views.employee_delete, name="employee_delete"),

    path("shifts/", views.shifts_list, name="shifts_list"),
    path("shifts/add/", views.shift_add, name="shift_add"),
    path("shifts/<int:pk>/edit/", views.shift_edit, name="shift_edit"),
    path("shifts/<int:pk>/delete/", views.shift_delete, name="shift_delete"),

    path("schedule/", views.schedule_calendar, name="schedule_calendar"),
    path("schedule/optimize/", views.run_optimizer, name="run_optimizer"),
    path("my-schedule/", views.my_schedule, name="my_schedule"),

    path("timeoff/", views.timeoff_list, name="timeoff_list"),
    path("timeoff/request/", views.timeoff_request, name="timeoff_request"),
    path("timeoff/<int:pk>/approve/", views.timeoff_approve, name="timeoff_approve"),
    path("timeoff/<int:pk>/reject/", views.timeoff_reject, name="timeoff_reject"),

    path("reports/", views.reports_view, name="reports"),

    path("notifications/", views.notifications_list, name="notifications_list"),
    path("notifications/<int:pk>/read/", views.mark_read, name="mark_read"),
    path("shifts/bulk/", views.shifts_bulk_add, name="shifts_bulk_add"),

    path("schedule/events.json", views.schedule_events_json, name="schedule_events_json"),
    path("reports/export.csv", views.reports_export_csv, name="reports_export_csv"),

    path("notifications/mark-all-read/", views.mark_all_read, name="mark_all_read"),

    path("assignments/", views.assignments_list, name="assignments_list"),
    path("assignments/add/", views.assignment_add, name="assignment_add"),
    path("assignments/<int:pk>/delete/", views.assignment_delete, name="assignment_delete"),

    path("analytics/", views.analytics_view, name="analytics"),
]