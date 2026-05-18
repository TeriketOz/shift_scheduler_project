from django.contrib import admin

from .models import Skill, Employee, Shift, Assignment, TimeOff, Notification


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("user", "position", "rank", "role", "hire_date", "max_hours_per_week")
    list_filter = ("role", "preferred_shift_type", "rank")
    search_fields = ("user__username", "user__last_name", "user__first_name", "position")
    filter_horizontal = ("skills",)


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("date", "shift_type", "start_time", "end_time", "required_employees", "location")
    list_filter = ("shift_type", "date", "required_skill")
    search_fields = ("location",)
    date_hierarchy = "date"


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("shift", "employee", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("employee__user__username",)


@admin.register(TimeOff)
class TimeOffAdmin(admin.ModelAdmin):
    list_display = ("employee", "start_date", "end_date", "reason", "status")
    list_filter = ("status",)
    search_fields = ("employee__user__username", "reason")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("user__username", "title")