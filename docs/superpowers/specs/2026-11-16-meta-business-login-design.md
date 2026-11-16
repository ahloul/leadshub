# Connect Business Managers with Facebook Login for Business

Date: 2026-11-16. Branch: `init/meta_ads`. Builds on US-META-01 (SRS FR-6.10, FR-6.11).

## Why

The Platform Admin currently creates a Business Manager by copying a
system-user token out of Meta Business Settings and pasting it into the
admin. The goal is that **no admin ever sees, copies or pastes a token**.

## Goals

- In the admin, "Connect with Facebook" opens Facebook Login for Business.
  The admin picks one business and returns. The Business Manager is then
  saved with its token, already checked with Meta.
- "Reconnect" on an existing Business Manager replaces its token the same
  way, for example after Recheck marks the token invalid.
- Connecting a business that is already saved updates it. It doesn't make a
  duplicate (matched on `meta_business_id`).

## Non-goals

- Connecting several businesses in one login.
- Broker-owned accounts (`ConnectedAccount` on master); that is the portal's
  OAuth work in `docs/INTEGRATIONS.md`.
- Any scheduled token refresh. A system-user token doesn't expire.

## Anti-goals (a failure even if it works)

- The token, the code or the app secret appears in a page, a URL we render,
  a log line or an error message.
- A login started by one admin can be finished by another request (CSRF):
  the `state` check is mandatory.

## Constraints

- The token type is the **Business Integration System User (BISU)** token,
  chosen by the configuration in the Meta app (approach A below).
- The Meta app must belong to a separate business portfolio from the
  Business Managers it connects (Meta's rule for BISU). This is a one-time
  setup in Meta, done by the project owner.
- The Meta app needs a Facebook Login for Business configuration with:
  - token type "System-user access token"
  - token expiry "Never"
  - permissions `ads_management` and `business_management`
  - asset types: ad accounts
- The app's Valid OAuth Redirect URIs list the callback URL for every
  environment, for example `http://localhost:8000/admin/ad_channels/metabusinessmanager/connect/callback/`.
- The admin pages are rendered by Django's template engine (Unfold), not djust.
- Tests never call Meta (the existing `client_for` fake pattern).

## Approaches (chosen: A)

| | Approach | Trade-off |
|---|---|---|
| **A** | BISU token through Facebook Login for Business, one business per login | Never expires and isn't tied to a person. Needs the app in a separate portfolio and one login per business. **Chosen** |
| B | User token extended to 60 days, many businesses in one login | Tied to the admin's personal account. Launches stop every ~60 days until someone logs in again |
| C | User login, then the server creates a system user and token in each business | Most code. Needs full admin rights in every business. The API flow isn't confirmed for this app type |

## Design

**Login uses a server redirect, not the JS SDK (my call).** The pasted SDK
snippet is not used. Facebook Login for Business with a system-user
configuration only returns a *code*, which the server must exchange
anyway. A plain redirect to Meta's dialog has several advantages:
- no third-party script in the admin
- none of the SDK's HTTPS rules
- the whole flow runs in Django views that tests can drive
Change this if you want the Facebook-styled button from the SDK.

1. **Settings:** add `META_LOGIN_CONFIG_ID` (env, `.env.example`). If it
   or `META_APP_ID` is unset, the Connect button is hidden and the
   connect URL returns an admin error message.
2. **Connect view** (`MetaBusinessManagerAdmin.get_urls`, `connect/`): needs
   `add_metabusinessmanager`. It stores a random `state` in the session,
   then redirects to
   `https://www.facebook.com/{META_API_VERSION}/dialog/oauth` with these
   parameters:
   - `client_id`
   - `redirect_uri` (the absolute callback URL)
   - `state`
   - `config_id`
   - `response_type=code`
   - `override_default_response_type=true`

   For a reconnect, the session also holds the Business Manager's pk and
   the view needs `change_metabusinessmanager`.
3. **Callback view** (`connect/callback/`), in this order:
   1. Require the same permission.
   2. Compare `state` with the session value (constant time) and pop it.
      Reject on a mismatch.
   3. On `error`/`error_description` from Meta (the admin cancelled), show
      a message and return to the list.
   4. Exchange the code: `GET /oauth/access_token` with `client_id`,
      `client_secret`, `redirect_uri` and `code`.
   5. Read the business: `GET /me?fields=client_business_id` with the new
      token, then `GET /{business_id}?fields=name`.
   6. Run the existing `check_access` (permissions and ad-account read).
   7. Create the Business Manager, or update it when that business ID
      already exists. Save the token encrypted, with status Valid.
      - On a reconnect, the business must match the stored ID. If it
        doesn't, refuse: "You picked a different business."
   8. Redirect to its change page. Offer "Save and sync" there as today.
4. **Meta calls** go in `ad_channels/meta_ads/client.py`:
   `exchange_code(code, redirect_uri) -> str` and
   `business_for_token() -> (id, name)`.
   - They raise `MetaError` with the token, code and app secret scrubbed
     out.
   - Sync code reaches them through `sync.py`, as the existing calls do.
5. **Admin form:** the add page no longer takes a typed token. "Add
   Business Manager" becomes "Connect with Facebook". The edit page shows
   a masked token, its status and a "Reconnect with Facebook" button in
   place of the token field. Name stays editable. The business ID is read
   only, because it comes from Meta.
6. **Media Buying Lead** (view only) sees neither button.
7. **Arabic** for every new string; `make messages`.

## Tests (each maps to a goal or anti-goal)

- **State and permissions:**
  - Callback with a missing or wrong `state` is refused, and Meta isn't
    called.
  - Callback without `add_metabusinessmanager` returns 403. So does a
    reconnect without `change`.
  - Connect redirects to the dialog with `config_id`, `response_type=code`
    and a `state` that matches the session.
- **Outcomes:**
  - A successful callback creates the Business Manager with an encrypted
    token and status Valid.
  - Connecting a business that is already saved updates its token and
    makes no new row.
  - A reconnect that picks a different business is refused, and the
    stored token is unchanged.
  - Meta errors (cancel, bad code, missing permission) show a message and
    save nothing.
- **Leaks and visibility:**
  - No response body or message contains the code, the token or the app
    secret.
  - Connect is hidden and refused when `META_LOGIN_CONFIG_ID` is unset.
  - The Media Buying Lead sees no Connect or Reconnect button.

## Left to the builder

- Template details for the buttons in Unfold.
- Where the `state` and reconnect pk live in the session (one key, signed
  or random).
- Whether `redirect_uri` is built with `request.build_absolute_uri` or
  from a setting. Behind a proxy it must be HTTPS and match Meta exactly.
