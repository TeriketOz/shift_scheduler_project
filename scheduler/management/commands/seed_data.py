# scheduler/management/commands/seed_data.py
import random
from datetime import date, time, timedelta

from django.contrib.auth.models import User, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from scheduler.models import (
    Skill, Employee, Shift, TimeOff,
)


SKILLS = [
    "Электрик 3 разряда",
    "Сварщик",
    "Допуск к высоте",
    "Слесарь КИПиА",
    "Допуск 1000В",
]

EMPLOYEES = [
    ("Иванов", "Иван", "Иванович", "Электромонтёр", 5, "DAY"),
    ("Петров", "Пётр", "Сергеевич", "Электромонтёр", 4, "DAY"),
    ("Сидоров", "Алексей", "Николаевич", "Сварщик", 5, "EVENING"),
    ("Кузнецов", "Дмитрий", "Андреевич", "Слесарь КИПиА", 4, "NIGHT"),
    ("Смирнов", "Михаил", "Павлович", "Электромонтёр", 3, "DAY"),
    ("Попов", "Сергей", "Викторович", "Бригадир", 6, "ANY"),
    ("Васильев", "Андрей", "Олегович", "Электромонтёр", 4, "EVENING"),
    ("Новиков", "Владимир", "Дмитриевич", "Сварщик", 5, "DAY"),
    ("Фёдоров", "Николай", "Юрьевич", "Слесарь КИПиА", 3, "NIGHT"),
    ("Морозов", "Евгений", "Александрович", "Электромонтёр", 5, "ANY"),
    ("Волков", "Артём", "Игоревич", "Электромонтёр", 4, "DAY"),
    ("Алексеев", "Роман", "Валерьевич", "Бригадир", 6, "EVENING"),
]


class Command(BaseCommand):
    help = "Заполняет БД тестовыми данными и создаёт группы прав."

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)
        self.stdout.write("Создание групп прав…")
        self._create_groups()

        self.stdout.write("Создание суперпользователя admin/admin…")
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True, "email": "admin@example.com"},
        )
        if created:
            admin_user.set_password("admin")
            admin_user.save()
            self.stdout.write(self.style.SUCCESS("  admin создан"))
        else:
            self.stdout.write("  admin уже существует, пропуск")

        self.stdout.write("Создание навыков…")
        skills = {}
        for name in SKILLS:
            skill, _ = Skill.objects.get_or_create(name=name, defaults={"description": name})
            skills[name] = skill
        self.stdout.write(f"  навыков: {len(skills)}")

        self.stdout.write("Создание сотрудников…")
        emp_group = Group.objects.get(name="Employees")
        employees = []
        for last, first, patr, position, rank, pref in EMPLOYEES:
            username = self._translit(last).lower()
            user, u_created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last, "email": f"{username}@example.com"},
            )
            if u_created:
                user.set_password("password123")
                user.save()
            user.groups.add(emp_group)

            emp, _ = Employee.objects.get_or_create(
                user=user,
                defaults={
                    "position": position,
                    "rank": rank,
                    "hire_date": date.today() - timedelta(days=random.randint(100, 3000)),
                    "phone": f"+7900{random.randint(1000000, 9999999)}",
                    "max_hours_per_week": 40,
                    "preferred_shift_type": pref,
                    "role": "EMPLOYEE",
                },
            )
            # случайные навыки (1-3)
            emp.skills.set(random.sample(list(skills.values()), k=random.randint(1, 3)))
            employees.append(emp)
        self.stdout.write(f"  сотрудников: {len(employees)}")
        # У трёх сотрудников — предпочтение выходных в сб–вс
        for e in random.sample(employees, 3):
            e.prefers_weekend_off = True
            e.save(update_fields=["prefers_weekend_off"])
        self.stdout.write("  предпочитают выходные (сб–вс): 3")
        self.stdout.write("Создание смен на 30 дней…")
        shift_defs = [
            ("DAY", time(8, 0), time(16, 0)),
            ("EVENING", time(16, 0), time(0, 0)),
            ("NIGHT", time(0, 0), time(8, 0)),
        ]
        skill_list = list(skills.values())
        created_shifts = 0
        for d in range(30):
            shift_date = date.today() + timedelta(days=d)
            for stype, start, end in shift_defs:
                req_skill = random.choice(skill_list) if random.random() < 0.3 else None
                _, was_created = Shift.objects.get_or_create(
                    date=shift_date,
                    shift_type=stype,
                    defaults={
                        "start_time": start,
                        "end_time": end,
                        "required_employees": random.randint(1, 3),
                        "required_skill": req_skill,
                        "location": "Цех №1",
                    },
                )
                if was_created:
                    created_shifts += 1
        self.stdout.write(f"  смен создано: {created_shifts}")

        self.stdout.write("Создание TimeOff…")
        for emp in random.sample(employees, 3):
            start = date.today() + timedelta(days=random.randint(1, 20))
            TimeOff.objects.create(
                employee=emp,
                start_date=start,
                end_date=start + timedelta(days=random.randint(1, 5)),
                reason="Отпуск",
                status="APPROVED",
            )
        self.stdout.write("  TimeOff: 3")

        self.stdout.write(self.style.SUCCESS("Готово!"))

    def _create_groups(self):
        admins, _ = Group.objects.get_or_create(name="Admins")
        employees, _ = Group.objects.get_or_create(name="Employees")
        hr, _ = Group.objects.get_or_create(name="HR")

        scheduler_perms = Permission.objects.filter(
            content_type__in=ContentType.objects.filter(app_label="scheduler")
        )
        admins.permissions.set(scheduler_perms)
        hr.permissions.set(scheduler_perms.filter(codename__startswith="view_"))
        employees.permissions.set(
            scheduler_perms.filter(codename__in=["view_shift", "view_assignment", "view_timeoff"])
        )

    @staticmethod
    def _translit(text):
        table = {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
            "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
            "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
            "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
            "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        }
        return "".join(table.get(ch, ch) for ch in text.lower())