import os
from dotenv import load_dotenv

load_dotenv()

REPORT_TIME      = os.getenv("REPORT_TIME", "08:00")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_URL       = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
DELIVERY_METHOD  = os.getenv("DELIVERY_METHOD", "console")
EMAIL_SENDER     = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD   = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECIPIENT  = os.getenv("EMAIL_RECIPIENT", "")
SMTP_HOST        = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT        = int(os.getenv("SMTP_PORT", 587))
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GITHUB_USERNAME  = os.getenv("GITHUB_USERNAME", "")
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO      = os.getenv("GITHUB_REPO", "AI_valuechain")