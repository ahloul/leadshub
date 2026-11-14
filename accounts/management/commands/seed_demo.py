"""Create demo data for local/Docker testing: an office, one login per role,
a brokerage that signed itself up and waits for review, a small catalog and a
few creatives.

Safe to run repeatedly. Never run against production data.
"""

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import BrokerOffice, Membership, OfficeRegistration, OfficeStatus, User
from accounts.roles import Role
from catalog.demo import seed_catalog
from creatives.demo import seed_library

DEMO_PASSWORD = "demo-pass-123"


class Command(BaseCommand):
    help = "Create the demo office, logins for every role, a small catalog and a few creatives."

    @transaction.atomic
    def handle(self, *args, **options):
        office, _ = BrokerOffice.objects.get_or_create(
            company_name="Nour Realty",
            defaults={
                "wallet_balance_egp": 250000,
                "wallet_balance_usd": 3180,
                "spend_tier": "gold",
                "feedback_score": 82,
            },
        )
        for username, first, last, roles in [
            ("demo", "Nour", "Hassan", [Role.BROKER_OWNER]),
            ("sara", "Sara", "Adel", [Role.BROKER_TEAM_MEMBER]),
        ]:
            user = self.demo_user(username, first, last, roles)
            Membership.objects.get_or_create(user=user, defaults={"office": office})

        # Internal staff (no office). "lead" is a Media Buyer promoted to team leader.
        for username, first, last, roles in [
            ("platform", "Omar", "Fathy", [Role.PLATFORM_ADMIN]),
            ("lead", "Karim", "Saleh", [Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD]),
            ("buyer", "Mona", "Zaki", [Role.MEDIA_BUYER]),
            ("content", "Laila", "Nabil", [Role.CONTENT_MANAGER]),
            ("finance", "Hany", "Mostafa", [Role.FINANCE_MANAGER]),
        ]:
            self.demo_user(username, first, last, roles)

        # A self-signed-up brokerage with its email confirmed, waiting for a
        # Platform Admin (admin > Broker offices > Sign-ups to review).
        applicant = self.demo_user("applicant", "Mona", "Said", [])
        if not hasattr(applicant, "office_registration"):
            pending = BrokerOffice.objects.create(
                company_name="Delta Brokers", status=OfficeStatus.PENDING
            )
            Membership.objects.create(user=applicant, office=pending)
            OfficeRegistration.objects.create(
                office=pending,
                applicant=applicant,
                phone_number="01012345678",
                language="en",
                email_confirmed_at=timezone.now(),
            )

        seed_library(seed_catalog())

        self.stdout.write(
            self.style.SUCCESS(
                "Demo ready. Password for every demo login: "
                f"{DEMO_PASSWORD}\n"
                "  brokers:  demo (owner), sara (team member)\n"
                "  sign-up waiting for review: applicant\n"
                "  internal: platform, lead, buyer, content, finance"
            )
        )

    def demo_user(self, username, first, last, roles):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@example.com", "first_name": first, "last_name": last},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        user.groups.add(*Group.objects.filter(name__in=[r.value for r in roles]))
        return user

