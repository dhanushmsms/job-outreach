# Job Outreach Automation

Multi-user cold email outreach workflow with country targeting, sponsorship flags, Google Sheets CRM, inbox monitoring, and AI-drafted replies.

## Architecture

```
ScrapeGraphAI → Google Sheets CRM → Cold Email (Gmail + PDF resume)
                                          ↓
                              Inbox Monitor (Gmail API)
                                          ↓
                     Telegram + Gmail Notification → Claude AI Draft Reply
```

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Google Sheets — Service Account
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** → download JSON → save as `credentials/service_account.json`
4. Create a Google Sheet, share it with the service account email (Editor access)
5. Copy the Sheet ID from the URL and paste into `config/settings.yaml`

### 3. Gmail OAuth (per user)
1. In Google Cloud Console, enable **Gmail API**
2. Create **OAuth 2.0 credentials** (Desktop App) → download JSON
3. Save as `credentials/user1_credentials.json` (matching each user's config)
4. On first run, a browser window will open for each user to authorize

### 4. Telegram Bot
1. Open Telegram → search `@BotFather` → `/newbot`
2. Copy the bot token into `config/settings.yaml`
3. Start a chat with your bot, then get your chat ID:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
4. Paste chat ID into each user's yaml (`telegram_chat_id`)

### 5. Configure settings
```bash
cp config/settings.yaml config/settings.yaml  # already created
# Edit config/settings.yaml — fill in all API keys
```

### 6. Create user profiles
```bash
cp config/users/user1.example.yaml config/users/user1.yaml
# Edit user1.yaml — fill in name, email, countries, etc.
# Repeat for user2.yaml, user3.yaml
```

### 7. Add resumes
Place each person's resume PDF in the `resumes/` folder and set the path in their yaml:
```yaml
resume_pdf: "resumes/alice_resume.pdf"
```

## Running

### Streamlit App (recommended)
```bash
streamlit run app.py
```
Opens at `http://localhost:8501`

### CLI
```bash
# Scrape contacts for all users, specific countries
python run.py --mode scrape --users all --countries "United Kingdom,Germany"

# Send emails (dry run first!)
python run.py --mode email --users all --dry-run
python run.py --mode email --users all

# Monitor inbox once
python run.py --mode monitor --users all

# Monitor in a loop every 15 minutes
python run.py --mode monitor --users all --loop --interval 15

# Full pipeline (scrape + email + monitor)
python run.py --mode full --users all --countries "United Kingdom"
```

## Country Sponsorship Config

In each user's yaml, each country entry has:
```yaml
target_countries:
  - name: "United Kingdom"
    needs_sponsorship: true
    custom_prompt: "Mention I require a Skilled Worker visa sponsorship."
  - name: "India"
    needs_sponsorship: false
    custom_prompt: ""
```

When `needs_sponsorship: true`, the cold email automatically includes the custom sponsorship message.

## Google Sheets CRM Columns

| Column | Description |
|---|---|
| Name | Contact's full name |
| Email | Contact's email |
| Company | Company name |
| Title | Job title |
| Country | Target country |
| LinkedIn URL | Profile URL |
| Source | linkedin / career_page / hunter |
| Status | scraped → emailed → replied → responded |
| Date Emailed | When cold email was sent |
| Reply Date | When they replied |
| Notes | Manual notes |
| Message ID | Gmail message ID for reply tracking |

## Multi-user

All 3 users share one Google Sheet, each with their own tab named after their username. Each user has separate Gmail OAuth credentials, Telegram chat ID, and config.

## Token Cost

- Resume summary (~60 tokens) goes into the email prompt
- Reply drafting uses ~300 tokens per reply
- Full resume PDF is attached to the email (zero token cost)
