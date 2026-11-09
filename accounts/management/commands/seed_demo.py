"""Create a demo broker office and one login per role for local/Docker testing.

Safe to run repeatedly. Never run against production data.
"""

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import BrokerOffice, Membership, User
from accounts.roles import Role

DEMO_PASSWORD = "demo-pass-123"


class Command(BaseCommand):
    help = "Create the Nour Realty demo office and demo logins for the broker and internal roles."

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

        self.stdout.write(
            self.style.SUCCESS(
                "Demo ready. Password for every demo login: "
                f"{DEMO_PASSWORD}\n"
                "  brokers:  demo (owner), sara (team member)\n"
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

