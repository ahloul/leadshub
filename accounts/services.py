"""Office teams: invitations, deactivation and campaign access.

SRS §2.1: the Broker Owner controls the office and its team invitations, and a
Broker Team Member creates and runs campaigns only when the owner grants it
(§2.2, "Conditional"). Platform Admins open new offices and invite their owners
(ROADMAP M1, Q6).

Rules (ROADMAP Q7, Q28):

- Managing a team takes the ``manage_team`` capability, never a role name, so
  custom roles work too.
- Owners invite team members. A manager can't change their own account or
  another manager's; LeadsHub staff manage owners in the admin.
- The only permission a manager gives or takes back is ``run_campaigns``, and
  they can give it only while they hold it.

Every function takes the person acting and checks what they may do, so callers
(live views, the admin) can't skip the rules. A person outside the actor's
office, or one they may not manage, raises ``PermissionDenied``.

Brokerages can also sign up themselves (ROADMAP Q6): ``register_office``
creates a pending office whose applicant has no role yet. They confirm their
email, staff are told, and a Platform Admin approves or rejects the office in
the admin. Approval makes the applicant the office's Broker Owner.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from leadshub.emails import send_after_commit

from . import selectors
from .models import BrokerOffice, Membership, OfficeRegistration, OfficeStatus, User
from .roles import Role, permissions_for
from .tokens import email_confirmation_token

logger = logging.getLogger(__name__)

MANAGE_TEAM = selectors.MANAGE_TEAM
RUN_CAMPAIGNS = selectors.RUN_CAMPAIGNS
# Approving and rejecting sign-ups changes the office (Platform Admin).
REVIEW_OFFICES = "accounts.change_brokeroffice"


def display_name(user):
    """The name to show for ``user``: their full name, or their sign-in name."""
    return user.get_full_name() or user.get_username()


def _managed_office_id(actor):
    """The office whose team ``actor`` manages."""
    membership = Membership.objects.filter(user_id=actor.pk).first()
    if membership is None or not actor.has_perm(MANAGE_TEAM):
        raise PermissionDenied
    return membership.office_id


def _managed_member(actor, member_id):
    """The person ``member_id`` in ``actor``'s office, if ``actor`` may manage them."""
    member = selectors.office_members(_managed_office_id(actor)).filter(pk=member_id).first()
    if member is None or member.pk == actor.pk or member.manages_team:
        raise PermissionDenied
    return member


def _create_invited_account(*, office_id, role, email, first_name, last_name):
    """An account with no usable password yet; the invitation link sets one.

    The sign-in name is the email address in lower case (``clean_sign_in_email``
    in accounts.forms checks that it can be one).
    """
    user = User(username=email.lower(), email=email, first_name=first_name, last_name=last_name)
    user.set_unusable_password()
    user.save()
    user.groups.add(Group.objects.get(name=Role(role).value))
    Membership.objects.create(user=user, office_id=office_id)
    return user


def _link_days():
    return max(1, settings.PASSWORD_RESET_TIMEOUT // (24 * 60 * 60))


def send_invitation(user, *, invited_by, request):
    """Email ``user`` a link to set their password, once the transaction commits.

    The link carries a Django password-reset token, so it stops working when
    the password is set or ``PASSWORD_RESET_TIMEOUT`` passes. Sending a new one
    is always safe. The email is written in the active language, which is the
    sender's page language. A mail server failure is logged; the person still
    shows as invited, and the invitation can be sent again.
    """
    token = default_token_generator.make_token(user)
    path = reverse(
        "accept_invitation",
        kwargs={"uidb64": urlsafe_base64_encode(force_bytes(user.pk)), "token": token},
    )
    context = {
        "first_name": user.first_name,
        "office_name": user.membership.office.company_name,
        "inviter_name": display_name(invited_by),
        "sign_in_name": user.get_username(),
        "link": request.build_absolute_uri(path),
        "valid_days": _link_days(),
        "details": [(gettext_lazy("Sign-in name"), user.get_username())],
    }
    send_after_commit("accounts/email/invitation", context, [user.email])


@transaction.atomic
def invite_member(*, actor, email, first_name="", last_name="", request):
    """Add a Broker Team Member to ``actor``'s office and email them an invitation."""
    user = _create_invited_account(
        office_id=_managed_office_id(actor),
        role=Role.BROKER_TEAM_MEMBER,
        email=email,
        first_name=first_name,
        last_name=last_name,
    )
    send_invitation(user, invited_by=actor, request=request)
    return user


def resend_invitation(*, actor, member_id, request):
    """Send a new invitation link to a member who hasn't set a password yet."""
    member = _managed_member(actor, member_id)
    if not member.is_active or not member.invitation_pending:
        raise ValidationError(
            _("%(name)s has already accepted the invitation.") % {"name": display_name(member)}
        )
    send_invitation(member, invited_by=actor, request=request)
    return member


def set_member_active(*, actor, member_id, active):
    """Deactivate or reactivate a member's account.

    A deactivated person is signed out on their next page load or live action
    (SRS §2.3): Django and djust no longer load an inactive user.
    """
    member = _managed_member(actor, member_id)
    if member.is_active != active:
        member.is_active = active
        member.save(update_fields=["is_active"])
    return member


def set_campaign_access(*, actor, member_id, allowed):
    """Give or take back ``run_campaigns`` for one member (SRS §2.2, "Conditional")."""
    member = _managed_member(actor, member_id)
    if allowed and not actor.has_perm(RUN_CAMPAIGNS):
        raise PermissionDenied
    permission = permissions_for([RUN_CAMPAIGNS]).get()
    if allowed:
        member.user_permissions.add(permission)
    else:
        member.user_permissions.remove(permission)
    return member


@transaction.atomic
def open_office(
    *, office, owner_email, owner_first_name="", owner_last_name="", opened_by, request
):
    """Save a new broker office, create its Broker Owner and email them an invitation."""
    if not opened_by.has_perm("accounts.add_brokeroffice"):
        raise PermissionDenied
    office.save()
    owner = _create_invited_account(
        office_id=office.pk,
        role=Role.BROKER_OWNER,
        email=owner_email,
        first_name=owner_first_name,
        last_name=owner_last_name,
    )
    send_invitation(owner, invited_by=opened_by, request=request)
    return owner


def resend_invitations(users, *, invited_by, request):
    """Send a new link to each of ``users`` who is in an office and hasn't set a password.

    For the admin's user list. Returns how many were sent.
    """
    if not invited_by.has_perm("accounts.change_user"):
        raise PermissionDenied
    pending = users.filter(
        is_active=True,
        password__startswith=UNUSABLE_PASSWORD_PREFIX,
        membership__isnull=False,
    )
    sent = 0
    for user in pending:
        send_invitation(user, invited_by=invited_by, request=request)
        sent += 1
    return sent


# --- Self sign-up (ROADMAP Q6) ----------------------------------------------


@transaction.atomic
def register_office(
    *, company_name, first_name, last_name, email, phone_number, password, request
):
    """Create a pending office and its applicant, and email a confirmation link.

    The applicant gets no role: they can sign in but see only the sign-up
    status page until staff approve the office. ``email`` must already have
    passed ``accounts.forms.clean_sign_in_email``. Returns the applicant.
    """
    office = BrokerOffice.objects.create(company_name=company_name, status=OfficeStatus.PENDING)
    user = User(username=email.lower(), email=email, first_name=first_name, last_name=last_name)
    user.set_password(password)
    user.save()
    Membership.objects.create(user=user, office=office)
    language = translation.get_language() or settings.LANGUAGE_CODE
    OfficeRegistration.objects.create(
        office=office,
        applicant=user,
        phone_number=phone_number,
        language=language.split("-")[0],
    )
    send_email_confirmation(user, request=request)
    logger.info("Brokerage %s signed up (office %s)", company_name, office.pk)
    return user


def send_email_confirmation(user, *, request):
    """Email the applicant a link that confirms their address (a new one is always safe)."""
    registration = user.office_registration
    if registration.email_confirmed_at is not None:
        raise ValidationError(_("Your email address is already confirmed."), code="confirmed")
    # The link opens the page in the language the sign-up was made in.
    with translation.override(registration.language):
        path = reverse(
            "confirm_email",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": email_confirmation_token.make_token(user),
            },
        )
    link = request.build_absolute_uri(path)
    context = {
        "first_name": user.first_name,
        "office_name": registration.office.company_name,
        "link": link,
        "valid_days": _link_days(),
    }
    send_after_commit("accounts/email/confirm_email", context, [user.email], language=registration.language)


@transaction.atomic
def confirm_email(user, *, request):
    """Mark the applicant's email confirmed and tell the staff who review sign-ups.

    Returns False if it was already confirmed (nothing is sent again).
    """
    registration = (
        OfficeRegistration.objects.select_for_update(of=("self",))
        .select_related("office")
        .get(applicant=user)
    )
    if registration.email_confirmed_at is not None:
        return False
    registration.email_confirmed_at = timezone.now()
    registration.save(update_fields=["email_confirmed_at"])
    _tell_reviewers(registration, request=request)
    return True


def _reviewers():
    """Active staff who may approve sign-ups, with an email address."""
    return (
        get_user_model()
        .objects.with_perm(REVIEW_OFFICES, backend="django.contrib.auth.backends.ModelBackend")
        .filter(is_active=True)
        .exclude(email="")
    )


def _tell_reviewers(registration, *, request):
    recipients = list(_reviewers().values_list("email", flat=True))
    if not recipients:
        logger.warning("No one can review the sign-up of office %s", registration.office_id)
        return
    applicant = registration.applicant
    with translation.override(settings.LANGUAGE_CODE):
        link = request.build_absolute_uri(
            reverse("admin:accounts_brokeroffice_change", args=[registration.office_id])
        )
    context = {
        "office_name": registration.office.company_name,
        "applicant_name": display_name(applicant),
        "applicant_email": applicant.email,
        "phone_number": registration.phone_number,
        "link": link,
        "details": [
            (gettext_lazy("Applicant"), display_name(applicant)),
            (gettext_lazy("Email"), applicant.email),
            (gettext_lazy("Mobile number"), registration.phone_number),
        ],
    }
    send_after_commit("accounts/email/new_signup", context, recipients, language=settings.LANGUAGE_CODE)


def _sign_in_link(registration, request):
    with translation.override(registration.language):
        return request.build_absolute_uri(reverse("login"))


@transaction.atomic
def approve_registrations(offices, *, actor, request):
    """Open each pending or rejected self-signed-up office whose email is confirmed.

    The applicant becomes the Broker Owner and is emailed. Returns how many
    were approved; offices whose email isn't confirmed yet are skipped.
    """
    if not actor.has_perm(REVIEW_OFFICES):
        raise PermissionDenied
    owner_role = Group.objects.get(name=Role.BROKER_OWNER.value)
    registrations = (
        OfficeRegistration.objects.select_for_update(of=("self",))
        .select_related("office", "applicant")
        .filter(
            office__in=offices,
            office__status__in=[OfficeStatus.PENDING, OfficeStatus.REJECTED],
            email_confirmed_at__isnull=False,
        )
    )
    approved = 0
    for registration in registrations:
        office = registration.office
        office.status = OfficeStatus.ACTIVE
        office.save(update_fields=["status"])
        registration.applicant.groups.add(owner_role)
        _mark_reviewed(registration, actor)
        send_after_commit(
            "accounts/email/signup_approved",
            {
                "first_name": registration.applicant.first_name,
                "office_name": office.company_name,
                "sign_in_name": registration.applicant.get_username(),
                "link": _sign_in_link(registration, request),
                "details": [(gettext_lazy("Sign-in name"), registration.applicant.get_username())],
            },
            [registration.applicant.email],
            language=registration.language,
        )
        logger.info("Office %s approved by %s", office.pk, actor.pk)
        approved += 1
    return approved


@transaction.atomic
def reject_registrations(offices, *, actor, request):
    """Turn down each pending self-signed-up office. Returns how many.

    The applicant keeps their sign-in and sees the decision on their status
    page; they are emailed only if their address was confirmed.
    """
    if not actor.has_perm(REVIEW_OFFICES):
        raise PermissionDenied
    registrations = (
        OfficeRegistration.objects.select_for_update(of=("self",))
        .select_related("office", "applicant")
        .filter(office__in=offices, office__status=OfficeStatus.PENDING)
    )
    rejected = 0
    for registration in registrations:
        office = registration.office
        office.status = OfficeStatus.REJECTED
        office.save(update_fields=["status"])
        _mark_reviewed(registration, actor)
        if registration.email_confirmed_at is not None:
            send_after_commit(
                "accounts/email/signup_rejected",
                {"first_name": registration.applicant.first_name, "office_name": office.company_name},
                [registration.applicant.email],
                language=registration.language,
            )
        logger.info("Office %s rejected by %s", office.pk, actor.pk)
        rejected += 1
    return rejected


def _mark_reviewed(registration, actor):
    registration.reviewed_at = timezone.now()
    registration.reviewed_by = actor
    registration.save(update_fields=["reviewed_at", "reviewed_by"])
