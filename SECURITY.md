# Security Policy

## Where secrets live

All runtime secrets are stored outside the repository:

| Secret | Location |
|---|---|
| Telegram bot token | Panel admin UI → Business → Telegram (stored in panel DB) |
| YooKassa / Telegram Payments provider token | Panel admin UI → Business → YooKassa (stored in panel DB) |
| Telegram webhook secret | `/etc/hiddify-panel/panel-secrets.env` (generated at install time by `openssl rand -hex 32`) |
| Panel app secret key | `/opt/hiddify-manager/hiddify-panel/app.cfg` |

None of these values are stored in this repository. The installer generates the webhook secret automatically during installation.

---

## What must never be committed

The following must never appear in any commit, branch, or tag in this repository:

- Telegram bot tokens (pattern: `<digits>:<alphanumeric string>`)
- YooKassa or other payment provider tokens
- Private keys of any kind (`BEGIN PRIVATE KEY`, `BEGIN RSA PRIVATE KEY`, etc.)
- Real IP addresses of production servers
- Real domain names of production servers or upstreams
- Usernames, server hostnames, or SSH keys
- Database passwords or connection strings with credentials
- Any file matching: `.env`, `*.sql`, `*.log`, `*secret*`, `panel-secrets.env`

These patterns are enforced by `.gitignore`. Verify before every push with:

```bash
git grep -E '[0-9]{8,12}:[A-Za-z0-9_-]{30,45}'
git grep -E 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY'
```

---

## Telegram token handling

The Telegram bot token is loaded at runtime from the panel database via `hconfig(ConfigEnum.telegram_bot_token)`. The token is never written to disk by application code and never appears in application logs.

If a `secrets.py` module is present in the telegrambot package (not included in this repository), it takes precedence over the database value. This allows environment-based secret injection without database access.

---

## Webhook secret requirement

The webhook endpoint (`/api/v2/tgbot/`) is fail-closed: if the `HIDDIFY_TELEGRAM_WEBHOOK_SECRET` environment variable or the `/etc/hiddify-panel/panel-secrets.env` entry is missing or empty, **all incoming webhook requests are rejected with HTTP 403**. The secret is validated using `hmac.compare_digest` to prevent timing attacks.

The installer creates the secret automatically. If you need to rotate it:

1. Generate a new secret: `openssl rand -hex 32`
2. Update `/etc/hiddify-panel/panel-secrets.env`
3. Re-register the webhook: navigate to Business → Telegram in the admin UI and save settings

---

## Reporting vulnerabilities

If you discover a security vulnerability:

1. Open a GitHub issue in this repository describing the vulnerability in general terms.
2. Do not post tokens, keys, server addresses, or exploit payloads publicly.
3. If the issue is sensitive, request a private discussion via the GitHub issue.

---

## Pre-release security checklist

Before tagging any release:

- [ ] `git grep -E '[0-9]{8,12}:[A-Za-z0-9_-]{30,45}'` — no Telegram tokens
- [ ] `git grep -E '[0-9]{5,}:(TEST|LIVE):[A-Za-z0-9_:-]+'` — no YooKassa tokens
- [ ] `git grep 'BEGIN PRIVATE KEY'` — no private keys
- [ ] No real IP addresses or server hostnames in any tracked file
- [ ] No real domain names in documentation (use `<DOMAIN>` placeholders)
- [ ] `.gitignore` covers `*.sql`, `*.log`, `.env`, `*secret*`, `*.bak`
- [ ] `.gitattributes` enforces LF line endings for all text files
- [ ] All `.sh` files pass `bash -n`
- [ ] All `.py` files pass `python3 -m py_compile`
