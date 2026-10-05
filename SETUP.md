# Step-by-step setup

Follow these in order. Each step ends with a command that proves it worked, so
you never move on with something quietly broken.

Total time: about 15 minutes. Cost: £0.

---

## Step 0 — Get the code running locally

```bash
cd football-bot
pip install -r requirements.txt
python test_engine.py     # maths checks
python test_api.py        # API parsing checks (no key, no quota used)
```

Both must end with `All tests passed ✅`.

```bash
python doctor.py
```

It will complain about a missing `.env` and token — that's expected, we fix it next.
Everything **below** step 3 in its output should already be green.

---

## Step 1 — Create your Telegram bot (2 min)

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Give it a name (anything, e.g. `My Football Tips`).
4. Give it a username — must end in `bot`, e.g. `my_football_tips_bot`.
5. BotFather replies with a token that looks like
   `8123456789:AAH4k...`. **Copy it.**

Now create your config file:

```bash
cp .env.example .env
```

Open `.env` and paste the token:

```ini
BOT_TOKEN=8123456789:AAH4k-your-real-token
```

**Check it:**

```bash
python doctor.py
```

Step 3 should now say `✅ token valid — bot is @your_bot_name`.

---

## Step 2 — Tell it where to send messages (2 min)

1. In Telegram, search **@userinfobot** and send it any message.
2. It replies with your `Id`, e.g. `123456789`.
3. Put that in `.env`:

```ini
CHAT_IDS=123456789
```

4. **Important:** open a chat with *your own* bot and press **Start**.
   Telegram refuses messages to anyone who never opened the chat — this is the
   single most common reason "the bot doesn't send anything".

**Check it:**

```bash
python doctor.py --send
```

You should receive a real message in Telegram saying delivery works.

---

## Step 3 — Preview a real daily run

```bash
python daily.py --dry-run
```

This prints exactly what would be sent, without sending it. On a normal match
day you'll see a SAFE ticket and a 30x BOMB ticket. On a Monday or during an
international break you may see "card too thin" — that's the bot being honest,
and step 4 fixes it.

To actually send it:

```bash
python daily.py
```

---

## Step 4 — Add the live API (optional but recommended, 3 min)

The free CSV source is excellent — real best-available odds, no limits — but it
refreshes only a couple of times a week, so thin days happen. API-Football
fills those gaps.

1. Go to **https://dashboard.api-football.com/register**
2. Register with an email. **No credit card is required.**
3. Confirm your email, log in, and open the **"API Key"** section of the dashboard.
4. Copy the key and put it in `.env`:

```ini
PROVIDER=hybrid
APIFOOTBALL_KEY=your_key_here
APIFOOTBALL_HOST=v3.football.api-sports.io
```

> If you signed up through **RapidAPI** instead of api-sports.io directly, use
> `APIFOOTBALL_HOST=api-football-v1.p.rapidapi.com`. Using the wrong host is the
> other classic failure, and `doctor.py` will tell you if you've mixed them up.

**Check it:**

```bash
python doctor.py
```

Step 6 should say `✅ key valid — plan 'Free', 0/100 requests used today`.

### Two things the free plan does that surprise people

**1. It only serves a ±1 day date window.** Ask for a date more than one day
away and it replies *"Free plans do not have access to this date, try from
X to Y"*. The bot detects this, stops scanning, and carries on with whatever
it has — it is a plan limit, not a broken key. `APIFOOTBALL_LOOKAHEAD=1`
reflects this; raise it only if you upgrade.

**2. During an international break there is genuinely nothing to bet.**
Domestic leagues stop for ~10 days. The API will show hundreds of fixtures,
but they are U21 qualifiers, friendlies and EFL Trophy games — competitions
with no reliable standings, which the model refuses to price. The bot says
"no fixtures" rather than inventing a ticket. That is correct behaviour.

### Why this won't burn your 100/day quota

`PROVIDER=hybrid` tries the free CSVs first and **only** calls the API when the
free card has fewer than `MIN_CARD_SIZE` (14) fixtures. When it does call:

| Call | Count | Cached for |
|---|---|---|
| `/fixtures` for today | 1 | 3 hours |
| `/standings` per league | 8 | 12 hours |
| `/odds` per league | 8 | 2 hours |

Leagues are ranked by how many fixtures they have that day and only the
busiest `APIFOOTBALL_MAX_LEAGUES` (10) are priced, so a packed Saturday cannot
run away with your quota. Roughly 20 requests on a day it's needed, **0 on a
normal match day**, and a local counter in `data/api_usage.json` hard-stops at
80 so you physically cannot overrun the cap.

---

## Step 5 — Run it free, forever, with no server (5 min)

GitHub Actions runs the daily job for you. Public repo = unlimited free minutes;
this uses about one minute a day. Nothing sleeps, nothing needs pinging.

1. **Create the repo.**

```bash
git init
git add .
git commit -m "football bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/football-bot.git
git push -u origin main
```

> `.env` is in `.gitignore`, so your token is **not** uploaded. Never commit it.

2. **Add your secrets.** On GitHub: your repo → **Settings** →
   **Secrets and variables** → **Actions** → **New repository secret**.

| Name | Value | Required |
|---|---|---|
| `BOT_TOKEN` | the token from BotFather | yes |
| `CHAT_IDS` | your id from @userinfobot | yes |
| `APIFOOTBALL_KEY` | your API-Football key | optional |

3. **Enable and test.** Repo → **Actions** tab → if prompted, click
   *"I understand my workflows, go ahead and enable them"* → pick
   **Daily football tickets** → **Run workflow**.

Watch the run. Green tick = it worked, and you'll have a Telegram message.

4. **It now runs every day at 08:00 UTC (09:00 Tunis).** To change the time,
   edit the `cron:` line in `.github/workflows/daily.yml`:

```yaml
    - cron: "0 8 * * *"      # minute hour * * *  (always UTC)
```

`0 6 * * *` = 07:00 Tunis. `30 17 * * *` = 18:30 Tunis.

---

## Step 6 — Use it

The Action handles the daily push. For interactive commands, run the bot
anywhere (laptop, Pi, any box):

```bash
python bot.py
```

| Command | What you get |
|---|---|
| `/today` | Full analysis: safe + bomb ticket |
| `/bomb 50` | A custom target instead of 30x |
| `/roi` | Real settled results — hit rate, profit, drawdown |
| `/status` | Which data source is live, API requests left today, current settings |
| `/value` | Where the model most disagrees with the bookmakers |

`/roi` is the one that matters. It grades past tickets against real final
scores, so after a few weeks you'll know what this is actually doing rather
than what it claims.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Bot never messages me | You never pressed **Start** in the chat | Open your bot in Telegram, press Start, rerun `python doctor.py --send` |
| `Telegram rejected the token` | Token typo, or extra spaces/quotes in `.env` | Re-copy from BotFather. No quotes: `BOT_TOKEN=123:ABC` |
| `chat not found` | Wrong `CHAT_IDS` | Re-check with @userinfobot. Group ids start with `-100` |
| `API-Football rejected the key` | Wrong `APIFOOTBALL_HOST` | api-sports.io signup → `v3.football.api-sports.io`; RapidAPI signup → `api-football-v1.p.rapidapi.com` |
| "card too thin to reach 30x" | Genuinely few fixtures today | Normal on Mon/Tue and international breaks. Add an API key, or just wait for the weekend |
| Action fails on `git push` | Workflow lacks write permission | Repo → Settings → Actions → General → Workflow permissions → **Read and write** |
| Action is green, no message | Secrets missing/misspelled | Settings → Secrets → check `BOT_TOKEN` and `CHAT_IDS` exist exactly |
| `ModuleNotFoundError` | Deps not installed | `pip install -r requirements.txt` |
| Quota exhausted | Testing too much | Counter resets at midnight UTC; `PROVIDER=free` needs no quota at all |

Whatever the symptom, start with:

```bash
python doctor.py
```

It checks all eight links in the chain in order and prints the exact fix.

---

## Before you bet real money

Read [BACKTEST.md](BACKTEST.md). Short version: across 11,800 real matches the
30x ticket landed **3.64%** of the time (about 1 day in 28), and its returns
swung **+1.1%, −32.5%, +72.1%** across three seasons. The probabilities the bot
shows are honest; the profit is not guaranteed in any direction.

The single most valuable habit is in step 4 of that document: **always take the
best available price.** The same picks returned −28% at one bookmaker and +14%
at best-available prices.
