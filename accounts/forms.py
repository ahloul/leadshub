from django.contrib.auth.forms import AuthenticationForm


class LoginForm(AuthenticationForm):
    """Django's login form with the project's input styling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"
