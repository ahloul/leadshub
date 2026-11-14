"""Read queries for offices and their people.

Other apps read accounts data through these functions rather than importing
its models (CLAUDE.md, "Architecture Conventions").
"""

from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import Permission
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q

from .models import BrokerOffice, OfficeRegistration, OfficeStatus, User

MANAGE_TEAM = "accounts.manage_team"
RUN_CAMPAIGNS = "accounts.run_campaigns"


def office(office_id):
    """The broker office with this id."""
    return BrokerOffice.objects.get(pk=office_id)


def _permission(name):
    app_label, codename = name.split(".")
    return Permission.objects.filter(content_type__app_label=app_label, codename=codename)


def _flag(condition):
    return ExpressionWrapper(condition, output_field=BooleanField())


def office_members(office_id):
    """Everyone in an office, active or not, with what the team page needs about each.

    Annotations:

    - ``manages_team``: holds ``manage_team`` through a role or directly, or is
      a superuser. Unlike ``user.has_perm()``, this is also true for a
      deactivated owner, so other owners can't reactivate one.
    - ``campaigns_from_role``: a role (or superuser status) gives
      ``run_campaigns``.
    - ``campaigns_granted``: an owner gave ``run_campaigns`` to this person
      directly (SRS §2.2, "Conditional").
    - ``invitation_pending``: the person hasn't set a password yet.
    """
    manage_team = _permission(MANAGE_TEAM)
    campaigns = _permission(RUN_CAMPAIGNS)
    return (
        User.objects.filter(membership__office_id=office_id)
        .annotate(
            manages_team=_flag(
                Q(is_superuser=True)
                | Q(Exists(manage_team.filter(group__user=OuterRef("pk"))))
                | Q(Exists(manage_team.filter(user=OuterRef("pk"))))
            ),
            campaigns_from_role=_flag(
                Q(is_superuser=True) | Q(Exists(campaigns.filter(group__user=OuterRef("pk"))))
            ),
            campaigns_granted=Exists(campaigns.filter(user=OuterRef("pk"))),
            invitation_pending=_flag(Q(password__startswith=UNUSABLE_PASSWORD_PREFIX)),
        )
        .order_by("-manages_team", "first_name", "last_name", "email")
    )


def registration_of(user):
    """``user``'s own sign-up (with its office), or None if they didn't sign up."""
    if not user.is_authenticated:
        return None
    return OfficeRegistration.objects.select_related("office").filter(applicant_id=user.pk).first()


def pending_registration_count():
    """Self-signed-up offices waiting for staff review, with their email confirmed."""
    return BrokerOffice.objects.filter(
        status=OfficeStatus.PENDING, registration__email_confirmed_at__isnull=False
    ).count()
