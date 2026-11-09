"""Put essential roles' permissions back to the defaults in accounts/roles.py.

Role permissions are edited in the admin, and ``migrate`` never overwrites
them. Use this to undo those edits, for every essential role or the named ones:

    manage.py reset_role_permissions
    manage.py reset_role_permissions "Media Buyer" "Broker Owner"
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.roles import Role, reset_role_permissions


class Command(BaseCommand):
    help = "Reset essential roles' permissions to the defaults in accounts/roles.py."

    def add_arguments(self, parser):
        parser.add_argument("roles", nargs="*", help="Role names (default: all essential roles)")

    def handle(self, *args, **options):
        names = options["roles"]
        unknown = [n for n in names if n not in Role.values]
        if unknown:
            raise CommandError(
                f"Not essential roles: {', '.join(unknown)}. Choose from: {', '.join(Role.values)}"
            )
        reset_role_permissions(names or None)
        self.stdout.write(self.style.SUCCESS("Role permissions reset to the defaults."))
