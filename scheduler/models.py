from datetime import date, datetime, timedelta

from django.contrib.auth.models import User
from django.db import models


class Skill(models.Model):
    name = models.CharField("Название", max_length=100, unique=True)
    description = models.TextField("Описание", blank=True)

    class Meta:
        verbose_name = "Навык"
        verbose_name_plural = "Навыки"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Employee(models.Model):
    SHIFT_TYPE_CHOICES = [
        ("DAY", "Дневная"),
        ("EVENING", "Вечерняя"),
        ("NIGHT", "Ночная"),
        ("ANY", "Любая"),
    ]
    ROLE_CHOICES = [
        ("ADMIN", "Администратор"),
        ("EMPLOYEE", "Сотрудник"),
        ("HR", "Кадровик"),
    ]

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="employee",
        verbose_name="Пользователь",
    )
    position = models.CharField("Должность", max_length=100)
    rank = models.PositiveSmallIntegerField("Разряд")
    hire_date = models.DateField("Дата приёма на работу")
    phone = models.CharField("Телефон", max_length=20, blank=True)
    max_hours_per_week = models.PositiveSmallIntegerField(
        "Макс. часов в неделю", default=40
    )
    preferred_shift_type = models.CharField(
        "Предпочитаемая смена", max_length=10,
        choices=SHIFT_TYPE_CHOICES, default="ANY",
    )
    prefers_weekend_off = models.BooleanField(
        default=False,
        verbose_name="Предпочитает выходные в сб–вс",
        help_text="Мягкое ограничение: система будет стараться не ставить на выходные",
    )
    skills = models.ManyToManyField(
        Skill, blank=True, verbose_name="Навыки", related_name="employees"
    )
    role = models.CharField(
        "Роль", max_length=10, choices=ROLE_CHOICES, default="EMPLOYEE"
    )

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ["user__last_name", "user__first_name"]

    def __str__(self):
        full_name = self.user.get_full_name() or self.user.username
        return f"{full_name} ({self.position}, {self.rank} р.)"


class Shift(models.Model):
    SHIFT_TYPE_CHOICES = [
        ("DAY", "Дневная"),
        ("EVENING", "Вечерняя"),
        ("NIGHT", "Ночная"),
    ]

    date = models.DateField("Дата", db_index=True)
    shift_type = models.CharField(
        "Тип смены", max_length=10, choices=SHIFT_TYPE_CHOICES
    )
    start_time = models.TimeField("Время начала")
    end_time = models.TimeField("Время окончания")
    required_employees = models.PositiveSmallIntegerField(
        "Требуется сотрудников", default=1
    )
    required_skill = models.ForeignKey(
        Skill, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Требуемый навык", related_name="shifts",
    )
    location = models.CharField("Место", max_length=200, blank=True)

    class Meta:
        verbose_name = "Смена"
        verbose_name_plural = "Смены"
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"{self.date} {self.get_shift_type_display()} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"

    def duration_hours(self):
        """Длительность смены в часах с учётом перехода через полночь."""
        start = datetime.combine(date.today(), self.start_time)
        end = datetime.combine(date.today(), self.end_time)
        if end <= start:
            end += timedelta(days=1)
        return (end - start).total_seconds() / 3600


class Assignment(models.Model):
    STATUS_CHOICES = [
        ("PLANNED", "Запланировано"),
        ("CONFIRMED", "Подтверждено"),
        ("COMPLETED", "Выполнено"),
        ("MISSED", "Пропущено"),
    ]

    shift = models.ForeignKey(
        Shift, on_delete=models.CASCADE,
        verbose_name="Смена", related_name="assignments",
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE,
        verbose_name="Сотрудник", related_name="assignments",
    )
    status = models.CharField(
        "Статус", max_length=10, choices=STATUS_CHOICES, default="PLANNED"
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Назначение"
        verbose_name_plural = "Назначения"
        unique_together = (("shift", "employee"),)
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employee} → {self.shift} [{self.get_status_display()}]"


class TimeOff(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "На рассмотрении"),
        ("APPROVED", "Одобрено"),
        ("REJECTED", "Отклонено"),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE,
        verbose_name="Сотрудник", related_name="time_offs",
    )
    start_date = models.DateField("Дата начала")
    end_date = models.DateField("Дата окончания")
    reason = models.CharField("Причина", max_length=200)
    status = models.CharField(
        "Статус", max_length=10, choices=STATUS_CHOICES, default="PENDING"
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Отгул/больничный"
        verbose_name_plural = "Отгулы и больничные"
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.employee} {self.start_date}–{self.end_date} [{self.get_status_display()}]"


class Notification(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE,
        verbose_name="Пользователь", related_name="notifications",
    )
    title = models.CharField("Заголовок", max_length=200)
    message = models.TextField("Сообщение")
    is_read = models.BooleanField("Прочитано", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username}: {self.title}"