# Setup: Telegram + Discord + Email + GitHub Secrets

One-time setup before triggering the scan workflow. Code is already in place
(`notify.py`); this is credential setup only.

---

## 1. Telegram bot

1. In Telegram, message **@BotFather**.
2. Send `/newbot`, follow the prompts (name, then a unique username ending in `bot`).
3. BotFather replies with a token like `123456789:AAExampleTokenString`. This is `TELEGRAM_BOT_TOKEN`.
4. Send any message (e.g. `hi`) to your new bot from your own Telegram account — bots can't message you first.
5. In a browser, visit:
   ```
   https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
   ```
   (replace `<TELEGRAM_BOT_TOKEN>` with your actual token)
6. Find `"chat":{"id":123456789,...}` in the JSON response. That number is `TELEGRAM_CHAT_ID`.
   - If the response is empty (`"result":[]`), you didn't send step 4's message yet — send it and reload the URL.

---

## 2. Discord webhook

1. Open the Discord server you want alerts posted to.
2. **Server Settings** → **Integrations** → **Webhooks** → **New Webhook**.
3. Give it a name (e.g. `Trade Scanner`).
4. Pick the channel it should post into.
5. Click **Copy Webhook URL**. This is `DISCORD_WEBHOOK_URL`.

---

## 3. Gmail app password

Requires 2-Step Verification enabled on the Google account first.

1. Go to https://myaccount.google.com/security
2. Under "How you sign in to Google," confirm **2-Step Verification** is ON. If not, enable it first.
3. Go to https://myaccount.google.com/apppasswords
4. Enter an app name (e.g. `trade-scanner`), click **Create**.
5. Google shows a 16-character password like `abcd efgh ijkl mnop`. Copy it with spaces removed: `abcdefghijklmnop`. This is `EMAIL_APP_PASSWORD`.
6. `EMAIL_ADDRESS` is the Gmail address itself (e.g. `you@gmail.com`).

---

## 4. Add credentials

### Local `.env`

Edit `C:\Users\User\trade-scanner\.env`, add (or update) these five lines:

```
TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenString
TELEGRAM_CHAT_ID=123456789
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456789/example-webhook-token
EMAIL_ADDRESS=you@gmail.com
EMAIL_APP_PASSWORD=abcdefghijklmnop
```

`.env` is gitignored — this file never gets committed.

### GitHub repo secrets

1. Open the repo on github.com.
2. **Settings** tab → left sidebar **Secrets and variables** → **Actions**.
3. Click **New repository secret**, one at a time, for each of:
   - `ANTHROPIC_API_KEY` (if not already added)
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `DISCORD_WEBHOOK_URL`
   - `EMAIL_ADDRESS`
   - `EMAIL_APP_PASSWORD`
4. Same exact values as your local `.env`. Paste and **Add secret** for each — GitHub will not show the value again after saving.

---

## 5. Manually trigger the workflow

1. On github.com, open the repo's **Actions** tab.
2. In the left sidebar, click **Trade Scan**.
3. Click **Run workflow** (dropdown button, top right of the run list) → branch `master` → **Run workflow**.
4. Refresh after a few seconds — a new run appears. Click it to watch live logs.

### What success looks like

- The **Run scan** step finishes with exit code 0 (green check), log lines showing the scan pipeline ran (universe build, filter, journal, digest).
- A Telegram message arrives in the chat with your bot within ~1 minute of the workflow finishing that step.
- A Discord message arrives in the configured channel around the same time.
- An email arrives at the same Gmail address (sent to itself) with subject **Trade Scanner Digest**.
- The **Commit updated journal.json** step either commits (if the journal changed) or no-ops cleanly (if there was nothing new to resolve/log) — either is fine, both are exit code 0.

If Telegram/Discord/email don't arrive but the run is green: check the log lines from the `notify` logger — `"not configured, skipping"` means a secret name is missing or misspelled in the repo secrets list.
