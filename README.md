# 🎬 Lotte Cinema '무비싸다구' Telegram Alarm Bot

An automated serverless system that monitors Lotte Cinema's **무비싸다구** (first-come, first-served discounted movie ticket coupon) events, extracts the exact coupon drop timestamps from event banner images using **Gemini Vision (`gemini-2.5-flash`)**, and sends Telegram alerts **twice**:
1. **1 hour before (`T - 60m`)** the event opens
2. **5 minutes before (`T - 5m`)** the event opens

Runs 100% in the cloud via **GitHub Actions** so you receive alerts even when your laptop is off.

---

## ✨ Features

- **Mobile App (`MA`) & Web (`HO`) API Coverage**: Queries Lotte Cinema's internal `EventData.aspx` endpoint across both Mobile App and PC Web channels so app-exclusive events are never missed.
- **Gemini Vision Banner Parser**: Automatically reads vertical promotional images to extract exact coupon open dates, hours (e.g., `15:00`, `14:00`), and multi-round schedules (`1차`, `2차`), caching results in [`data/events.json`](data/events.json) so Gemini is only called once per new event.
- **Cron-Jitter Proof Precision Timing**: Wakes up ~20–25 minutes ahead of `:00` and `:55` alarm windows and sleeps until the exact second of `open_time - 60m` and `open_time - 5m` before dispatching the Telegram alert.
- **Zero-Dependency Local Fallback**: Runs out-of-the-box on standard Python 3.9+ (`urllib`) as well as with `Pillow` for automatic banner image resizing.

---

## 🚀 Setup Guide (Runs When Your Laptop is Off)

### Step 1: Get Your 3 Credentials

1. **`TELEGRAM_BOT_TOKEN`**
   - Open Telegram, search for **`@BotFather`**, and send `/newbot`.
   - Follow the prompts and copy the HTTP API Token (e.g., `123456789:ABCdefGHIjklMNO...`).
   - Send a message (e.g., `Hello`) to your new bot in Telegram to activate the chat.

2. **`TELEGRAM_CHAT_ID`**
   - Open the following URL in your browser (replace `<YOUR_BOT_TOKEN>` with your token):
     ```text
     https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
     ```
   - Find `"chat":{"id":123456789,...}` in the JSON response and copy that number.

3. **`GEMINI_API_KEY`**
   - Visit [Google AI Studio](https://aistudio.google.com/apikey) and create a free API key.

---

### Step 2: Push to GitHub & Configure Secrets

1. Create a new GitHub repository and push this folder:
   ```bash
   git init
   git add .
   git commit -m "feat: initial Lotte Cinema 무비싸다구 Telegram alarm bot"
   git branch -M main
   git remote add origin https://github.com/<YOUR_USERNAME>/<YOUR_REPO>.git
   git push -u origin main
   ```

2. In your GitHub repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GEMINI_API_KEY`

3. Verify the bot in GitHub Actions:
   - Go to the **Actions** tab → **Lotte Cinema Movie Saddagu Alarm Bot**.
   - Click **Run workflow**, select `test-telegram` under **Action to run**, and click **Run workflow**.
   - Confirm that your Telegram receives the test notification!

---

## 💻 Local Usage & Testing

1. *(Optional)* Copy `.env.example` to `.env` and fill in your keys:
   ```bash
   cp .env.example .env
   ```

2. **Send a test Telegram notification:**
   ```bash
   python3 -m src.main --test-telegram
   ```

3. **Run a full crawl + alarm check (dry-run):**
   ```bash
   python3 -m src.main --mode all --dry-run
   ```

4. **Inspect any specific Lotte Cinema `EventID`:**
   ```bash
   python3 -m src.main --inspect-event 101110014726074
   ```

5. **Run the unit test suite:**
   ```bash
   python3 -m unittest discover -s tests -v
   ```
