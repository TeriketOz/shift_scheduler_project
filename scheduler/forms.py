from django import forms
from django.contrib.auth.models import User, Group
from django.db import transaction

from .models import Employee, Shift, TimeOff

BS = {"class": "form-control"}
BS_SELECT = {"class": "form-select"}

from .models import Assignment

class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ["shift", "employee", "status"]
        widgets = {
            "shift": forms.Select(attrs=BS_SELECT),
            "employee": forms.Select(attrs=BS_SELECT),
            "status": forms.Select(attrs=BS_SELECT),
        }

class EmployeeForm(forms.ModelForm):
    username = forms.CharField(label="Логин", widget=forms.TextInput(attrs=BS))
    email = forms.EmailField(label="Email", required=False, widget=forms.EmailInput(attrs=BS))
    first_name = forms.CharField(label="Имя", widget=forms.TextInput(attrs=BS))
    last_name = forms.CharField(label="Фамилия", widget=forms.TextInput(attrs=BS))
    password = forms.CharField(
        label="Пароль", required=False,
        widget=forms.PasswordInput(attrs=BS),
        help_text="Оставьте пустым при редактировании, чтобы не менять",
    )

    class Meta:
        model = Employee
        fields = ["position", "rank", "hire_date", "phone",
                  "max_hours_per_week", "preferred_shift_type", "skills", "role", "prefers_weekend_off"]
        widgets = {
            "position": forms.TextInput(attrs=BS),
            "rank": forms.NumberInput(attrs=BS),
            "hire_date": forms.DateInput(attrs={**BS, "type": "date"}),
            "phone": forms.TextInput(attrs=BS),
            "max_hours_per_week": forms.NumberInput(attrs=BS),
            "preferred_shift_type": forms.Select(attrs=BS_SELECT),
            "skills": forms.SelectMultiple(attrs=BS_SELECT),
            "role": forms.Select(attrs=BS_SELECT),
            "prefers_weekend_off": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            u = self.instance.user
            self.fields["username"].initial = u.username
            self.fields["email"].initial = u.email
            self.fields["first_name"].initial = u.first_name
            self.fields["last_name"].initial = u.last_name

    @transaction.atomic
    def save(self, commit=True):
        emp = super().save(commit=False)
        if emp.pk:
            user = emp.user
        else:
            user = User(username=self.cleaned_data["username"])
        user.username = self.cleaned_data["username"]
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        pwd = self.cleaned_data.get("password")
        if pwd:
            user.set_password(pwd)
        elif not user.pk:
            user.set_unusable_password()
        user.save()
        emp.user = user
        if commit:
            emp.save()
            self.save_m2m()
            group, _ = Group.objects.get_or_create(name="Employees")
            user.groups.add(group)
        return emp


class ShiftForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = ["date", "shift_type", "start_time", "end_time",
                  "required_employees", "required_skill", "location"]
        widgets = {
            "date": forms.DateInput(attrs={**BS, "type": "date"}),
            "shift_type": forms.Select(attrs=BS_SELECT),
            "start_time": forms.TimeInput(attrs={**BS, "type": "time"}),
            "end_time": forms.TimeInput(attrs={**BS, "type": "time"}),
            "required_employees": forms.NumberInput(attrs=BS),
            "required_skill": forms.Select(attrs=BS_SELECT),
            "location": forms.TextInput(attrs=BS),
        }


class BulkShiftForm(forms.Form):
    SHIFT_CHOICES = [("DAY", "Дневная"), ("EVENING", "Вечерняя"), ("NIGHT", "Ночная")]
    TIMES = {
        "DAY": ("08:00", "16:00"),
        "EVENING": ("16:00", "00:00"),
        "NIGHT": ("00:00", "08:00"),
    }
    start_date = forms.DateField(label="С", widget=forms.DateInput(attrs={**BS, "type": "date"}))
    end_date = forms.DateField(label="По", widget=forms.DateInput(attrs={**BS, "type": "date"}))
    shift_types = forms.MultipleChoiceField(
        label="Типы смен", choices=SHIFT_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    required_employees = forms.IntegerField(
        label="Требуется сотрудников", min_value=1, initial=2,
        widget=forms.NumberInput(attrs=BS),
    )
    location = forms.CharField(label="Локация", required=False, widget=forms.TextInput(attrs=BS))

    def clean(self):
        cd = super().clean()
        if cd.get("start_date") and cd.get("end_date") and cd["end_date"] < cd["start_date"]:
            raise forms.ValidationError("Дата окончания раньше даты начала")
        return cd


class TimeOffForm(forms.ModelForm):
    class Meta:
        model = TimeOff
        fields = ["start_date", "end_date", "reason"]
        widgets = {
            "start_date": forms.DateInput(attrs={**BS, "type": "date"}),
            "end_date": forms.DateInput(attrs={**BS, "type": "date"}),
            "reason": forms.TextInput(attrs=BS),
        }

    def clean(self):
        cd = super().clean()
        if cd.get("start_date") and cd.get("end_date") and cd["end_date"] < cd["start_date"]:
            raise forms.ValidationError("Дата окончания раньше даты начала")
        return cd