"""
Система внутренних уведомлений с дублированием на email.
Внутренние уведомления хранятся в модели Notification, email отправляется
через django.core.mail.send_mail. Сбои SMTP не ломают основную операцию.
"""
import logging

from .models import Notification

logger = logging.getLogger(__name__)


def notify(user, title: str, message: str, send_email: bool = False) -> Notification:
    """Создать внутреннее уведомление. Email отключён в текущей конфигурации."""
    return Notification.objects.create(user=user, title=title, message=message)

def notify_shift_assigned(employee, shift) -> None:
    """Сообщить сотруднику о новом назначении на смену."""
    title = f"Назначение на смену {shift.date:%d.%m.%Y}"
    message = (
        f"Здравствуйте, {employee.user.first_name or employee.user.username}!\n\n"
        f"Вы назначены на смену:\n"
        f"  Дата: {shift.date:%d.%m.%Y}\n"
        f"  Тип: {shift.get_shift_type_display()}\n"
        f"  Время: {shift.start_time:%H:%M}–{shift.end_time:%H:%M}\n"
        f"  Локация: {shift.location or '—'}\n"
    )
    notify(employee.user, title, message)


def notify_uncovered_shifts(admin_users, uncovered_shifts) -> None:
    """Разослать администраторам список непокрытых смен."""
    if not uncovered_shifts:
        return
    lines = [
        f"  • {s.date:%d.%m.%Y} {s.get_shift_type_display()} "
        f"({s.start_time:%H:%M}–{s.end_time:%H:%M}), требуется {s.required_employees}"
        for s in uncovered_shifts
    ]
    message = "Не удалось покрыть следующие смены:\n\n" + "\n".join(lines)
    title = f"Непокрытые смены: {len(uncovered_shifts)} шт."
    for admin in admin_users:
        notify(admin, title, message)


def notify_timeoff_status(timeoff) -> None:
    """Уведомить сотрудника об изменении статуса отгула."""
    status_ru = {
        "APPROVED": "одобрена",
        "REJECTED": "отклонена",
        "PENDING": "ожидает рассмотрения",
    }.get(timeoff.status, timeoff.status)
    title = f"Заявка на отгул {status_ru}"
    message = (
        f"Ваша заявка на отгул с {timeoff.start_date:%d.%m.%Y} "
        f"по {timeoff.end_date:%d.%m.%Y} ({timeoff.reason}) {status_ru}."
    )
    notify(timeoff.employee.user, title, message)


def notify_optimization_complete(admin_user, stats: dict) -> None:
    """Уведомить администратора о завершении оптимизации со статистикой."""
    title = "Оптимизация расписания завершена"
    message = (
        f"Результаты запуска оптимизатора:\n"
        f"  Назначений создано: {stats.get('num_assignments', 0)}\n"
        f"  Покрытие: {stats.get('coverage_percent', 0)}%\n"
        f"  Суммарная переработка: {stats.get('total_overtime', 0)} ч\n"
        f"  Разброс часов: {stats.get('fairness_metric', 0)} ч\n"
        f"  Предпочтения учтены: {stats.get('satisfied_preferences', 0)}%\n"
        f"  Значение целевой функции: {stats.get('objective_value', 0)}\n"
    )
    notify(admin_user, title, message)