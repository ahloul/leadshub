"""django-q2's admin screens (Background jobs) in the admin's look (Unfold).

django-q2 registers plain Django admin classes; these subclasses add
Unfold's styling and tighten what each screen allows:

- Schedules are declared in ``leadshub/jobs.py`` and rewritten after every
  ``migrate``, so here they are read-only: when each runs and how it went.
- Retrying a failed task ("Resubmit selected tasks") needs the change
  permission on failed tasks, not just the view one.
"""

from django.contrib import admin
from django_q import admin as q_admin
from django_q.models import Failure, OrmQ, Schedule, Success
from unfold.admin import ModelAdmin


def _resubmit(model_admin, request, queryset):
    return q_admin.resubmit_task(model_admin, request, queryset)


_resubmit.short_description = q_admin.resubmit_task.short_description
_resubmit.allowed_permissions = ("change",)


for model in (Schedule, Success, Failure, OrmQ):
    if admin.site.is_registered(model):
        admin.site.unregister(model)


@admin.register(Schedule)
class ScheduleAdmin(ModelAdmin, q_admin.ScheduleAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Success)
class SuccessAdmin(ModelAdmin, q_admin.TaskAdmin):
    actions = []


@admin.register(Failure)
class FailureAdmin(ModelAdmin, q_admin.FailAdmin):
    actions = [_resubmit]


@admin.register(OrmQ)
class QueuedTaskAdmin(ModelAdmin, q_admin.QueueAdmin):
    def has_change_permission(self, request, obj=None):
        return False
