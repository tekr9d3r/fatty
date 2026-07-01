# Fatty — Telegram Calorie Tracker

A Telegram bot that tracks calories by letting you describe food in plain text or send a photo. It uses Claude for calorie estimation and logs every entry to a Google Sheet.

---

## Features

- Send any food description → get a calorie estimate → confirm to log
- Send a photo of your meal → same flow with Claude vision
- Log workouts → calories burned added to your daily budget
- `/today` — intake, burned, remaining
- `/history N` — last N days grouped by date
- `/undo` — remove your last entry from the sheet

---

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`, follow the prompts
3. Copy the **bot token** — you'll need it for `TELEGRAM_BOT_TOKEN`

---

### 2. Get a Claude API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account / log in
3. Navigate to **API Keys** and create a new key
4. Copy it — you'll need it for `ANTHROPIC_API_KEY`

---

### 3. Set Up Google Sheets Access

#### 3a. Create a Google Cloud project and enable the Sheets API

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Navigate to **APIs & Services → Library**
4. Search for **Google Sheets API** and click **Enable**

#### 3b. Create a service account

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → Service Account**
3. Give it a name (e.g. `fatty-bot`) and click **Done**
4. Click the service account you just created
5. Go to the **Keys** tab → **Add Key → Create new key → JSON**
6. Download the JSON file and save it as `service_account.json` in this project folder

#### 3c. Share the Google Sheet with the service account

1. Open your Google Sheet: [click here](https://docs.google.com/spreadsheets/d/1OxZdnPLmU8V3tMs7pdCHgyu0ORD4mqp1XvQx_8suZRc)
2. Click **Share**
3. Enter the service account email (looks like `fatty-bot@your-project.iam.gserviceaccount.com` — found inside `service_account.json` under `"client_email"`)
4. Set role to **Editor** and click **Send**

#### 3d. Add the header row to the sheet

In row 1, add these column headers exactly:

```
Date | Time | Type | Item | Calories (kcal) | Notes
```

---

### 4. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and fill in the three values:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json
```

---

### 5. Install Dependencies

```bash
pip install -r requirements.txt
```

---

### 6. Run the Bot

```bash
python3 bot.py
```

The bot will start polling. Open Telegram and send it a message to test.

---

## Usage

| What to send | What happens |
|---|---|
| `had a banana and coffee with milk` | Claude estimates ~145 kcal, asks you to confirm |
| Send a food photo | Claude identifies the food, estimates calories |
| `swam 47 min, 2050m, avg HR 133` | Claude estimates calories burned, logs as Workout |
| `/goal 2200` | Sets your daily calorie goal to 2200 kcal |
| `/today` | Shows today's intake, burned, and remaining calories |
| `/history 7` | Shows the last 7 days summarized by date |
| `/undo` | Removes your last logged entry from the sheet |

---

## Deploying to Railway (free tier)

1. Install the Railway CLI: `npm install -g @railway/cli`
2. Log in: `railway login`
3. In the project folder: `railway init`
4. Set environment variables in the Railway dashboard (Settings → Variables):
   - `TELEGRAM_BOT_TOKEN`
   - `ANTHROPIC_API_KEY`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — set this to the **path** or paste the **JSON content** directly as `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT` (see note below)
5. Deploy: `railway up`

> **Note on service account JSON in Railway:** Railway environment variables are strings, not files. The easiest approach is to paste the entire contents of `service_account.json` into a variable called `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT`, then update `bot.py` to write that string to a temp file on startup:
>
> ```python
> import tempfile, json, os
> sa_content = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT")
> if sa_content:
>     tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
>     tmp.write(sa_content)
>     tmp.close()
>     os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = tmp.name
> ```

## Deploying to Render (free tier)

1. Push this repo to GitHub
2. Create a new **Web Service** on [render.com](https://render.com)
3. Set the **Start Command** to: `python3 bot.py`
4. Add the three environment variables under **Environment**
5. Same note as above applies for the service account JSON

---

## Google Sheet Structure

| Date | Time | Type | Item | Calories (kcal) | Notes |
|---|---|---|---|---|---|
| 2026-07-01 | 08:30 | Food | banana, coffee with milk | 145 | Typical portions assumed |
| 2026-07-01 | 17:45 | Workout | Swimming | -520 | 2050m at moderate pace, HR 133 |
