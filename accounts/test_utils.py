"""Shared helpers for building users and offices in tests."""

from django.contrib.auth.models import Group

from .models import BrokerOffice, Membership, User


def make_user(username, *roles, office=None, **fields):
    fields.setdefault("email", f"{username}@example.com")
    user = User.objects.create_user(username=username, password="pass-12345", **fields)
    for role in roles:
        user.groups.add(Group.objects.get(name=role))
    if office is not None:
        Membership.objects.create(user=user, office=office)
    return user


def make_office(name="Nour Realty", **fields):
    return BrokerOffice.objects.create(company_name=name, **fields)
