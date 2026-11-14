# Deploying to Fly.io

The app runs on Fly machines (`fly.toml`), with:

- **Database:** Fly Managed Postgres, which gives the app `DATABASE_URL`.
- **Uploads:** Tigris, Fly's S3-compatible storage. `fly storage create`
  sets `BUCKET_NAME`, `AWS_ENDPOINT_URL_S3`, `AWS_REGION` and the keys, and
  the settings read them. Files stay private: they're streamed through
  `PrivateFileView`, never served from a public bucket URL.
- **Email:** Mailtrap over SMTP.
- **Static files:** collected into the image and served by Fly's proxy
  (`[[statics]]`).

- **Several machines:** Redis (`REDIS_URL`). See [Running on several
  machines](#running-on-several-machines). Without it, run one machine.

## One-time setup

Use the arm64 `fly` (`export PATH=/opt/homebrew/bin:$PATH` on this Mac).

```sh
fly auth login

# 1. The app. Names are global: if "leadshub" is taken, pick another and
#    change app, ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS in fly.toml.
fly apps create leadshub

# 2. PostgreSQL. Create a cluster in fra, then attach it: this sets the
#    DATABASE_URL secret.
fly mpg create
fly mpg attach <cluster id> -a leadshub

# 3. Uploads bucket (private; sets the storage secrets).
fly storage create -a leadshub -n leadshub-uploads

# 4. Django secret key.
fly secrets set -a leadshub --stage \
  SECRET_KEY="$(.venv/bin/python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())')"

# 5. Email through Mailtrap (credentials: below).
fly secrets set -a leadshub --stage \
  EMAIL_HOST=sandbox.smtp.mailtrap.io \
  EMAIL_PORT=587 \
  EMAIL_HOST_USER=<username> \
  EMAIL_HOST_PASSWORD=<password> \
  DEFAULT_FROM_EMAIL="LeadsHub <no-reply@example.com>"

# 6. Deploy (one machine to start; migrations run first as the release command).
make fly-deploy

# 7. The first Platform Admin.
fly ssh console -a leadshub --pty -C "python manage.py createsuperuser"
```

### Mailtrap: test inbox or real delivery

- **Email Testing (sandbox):** `sandbox.smtp.mailtrap.io`. Every email
  lands in the Mailtrap inbox and nobody else receives it. Use this while
  the deployment is for testing.
- **Email Sending (real people):** `live.smtp.mailtrap.io`, user `api`, and
  the password is a Mailtrap API token. Verify the sending domain in Mailtrap
  first and set `DEFAULT_FROM_EMAIL` to an address on it. Switching from the
  sandbox is just `fly secrets set` with these values.

## Running on several machines

Each machine is its own process, and two things live in a process's memory
unless `REDIS_URL` is set (`leadshub/settings.py`):

- **djust's page state.** A page is rendered over HTTP and then joined over
  the WebSocket, and Fly may send the two (and every reconnect) to
  different machines. In Redis, every machine finds the state.
- **The channel layer.** Messages to other open pages (server pushes) must
  reach the machine holding that page's socket. In Redis, they do.

Everything else is already shared: sessions and login lockouts (axes) are in
PostgreSQL, uploads in Tigris. A WebSocket stays on the machine it opened on.

```sh
# Upstash Redis on Fly's private network, in the app's region. It prints the
# redis:// URL; pay-as-you-go bills per command.
fly redis create -n leadshub-redis -r fra
fly secrets set -a leadshub REDIS_URL=<the redis:// URL it printed>

# Then add machines (two in fra, or one more in another region).
fly scale count 2 -a leadshub
```

To go back to one machine: `fly scale count 1`. `REDIS_URL` can stay.

## Every deploy

Pushing or merging to `master` deploys by itself
(`.github/workflows/deploy.yml`): the tests run against PostgreSQL first,
and the deploy starts only if they pass. Pull requests run the tests only.
To deploy by hand instead:

```sh
make fly-deploy      # build, migrate, roll out
fly logs -a leadshub # follow the server
```

The workflow needs a `FLY_API_TOKEN` repository secret, a deploy token
limited to this app. Made once (and again to replace it):

```sh
fly tokens create deploy -a leadshub -n "github-actions" | gh secret set FLY_API_TOKEN -R ahloul/leadshub
```

To revoke it: `fly tokens list -a leadshub`, then `fly tokens revoke <id>`.

After a deploy, check sign-in, opening a creative's file (Tigris) and an
invitation email (Mailtrap).

Don't run `seed_demo` on a public deployment: its logins share a published
password.
