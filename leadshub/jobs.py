"""Background work: the task queue and the scheduled jobs (django-q2; ROADMAP section 6).

``manage.py qcluster`` runs both: the "jobs" process on Fly, the ``jobs``
service in Docker, ``make jobs`` locally. Settings: ``Q_CLUSTER``.

- **Queue work** with ``enqueue("app.module.function", *args)``, never by
  calling a slow function in a request. Pass ids and plain values, not model
  instances. A task that raises is tried again (``max_attempts``), so each
  must be safe to run twice. Emails go this way (``leadshub.emails``).
- **Schedule work** by adding it to ``SCHEDULES``. After every ``migrate``
  (``sync_schedules`` on ``post_migrate``) each one exists in the database
  with these values; staff see them, and when each last ran, in the admin
  (Background jobs). A schedule removed from this list is deleted.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# The prefix that marks a schedule as declared here (others are left alone).
PREFIX = "leadshub: "

# name → schedule. schedule_type is django-q2's: "I" every `minutes`,
# "H" hourly, "D" daily, "C" cron (`cron`, needs croniter).
SCHEDULES = {
    # FR-6.7: tell the leads about review tasks with an hour or less left.
    "review SLA escalation": {
        "func": "reviews.services.escalate_near_breach",
        "schedule_type": "I",
        "minutes": 1,
    },
}


def enqueue(func, *args, **kwargs):
    """Queue ``func`` (an import path) to run in the cluster; returns the task id."""
    from django_q.tasks import async_task

    return async_task(func, *args, **kwargs)


def sync_schedules(**kwargs):
    """Make the database schedules match ``SCHEDULES`` (connected to ``post_migrate``)."""
    from django_q.models import Schedule

    names = set()
    for name, values in SCHEDULES.items():
        full_name = PREFIX + name
        names.add(full_name)
        defaults = {
            "func": values["func"],
            "schedule_type": values["schedule_type"],
            "minutes": values.get("minutes"),
            "cron": values.get("cron"),
            "repeats": -1,  # forever
        }
        schedule, created = Schedule.objects.update_or_create(name=full_name, defaults=defaults)
        if created:
            schedule.next_run = timezone.now()
            schedule.save(update_fields=["next_run"])
            logger.info("Scheduled %s", full_name)
    Schedule.objects.filter(name__startswith=PREFIX).exclude(name__in=names).delete()
