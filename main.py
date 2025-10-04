import os
import json
import time
import signal
import logging
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
from twilio.rest import Client
import openai
from datetime import datetime

# ====================
# Setup
# ====================
load_dotenv()
logging.basicConfig(filename="agent.log", level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM")  # WhatsApp sandbox number
TARGET_WHATSAPP = os.getenv("TARGET_WHATSAPP")

openai.api_key = os.getenv("OPENAI_API_KEY")

CACHE_FILE = "sent_cache.json"

# ====================
# Helper Functions
# ====================
def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return []

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def fetch_site(url, selector="h2"):
    """Scrape site headlines based on CSS selector."""
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return [el.get_text(strip=True) for el in soup.select(selector)]
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return []

def analyze_with_ai(headlines):
    """Summarize & filter headlines with OpenAI."""
    meaningful = []
    for hl in headlines:
        try:
            prompt = f"Summarize this headline in under 100 chars and decide if it's newsworthy: '{hl}'"
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": "You are a helpful AI news filter."},
                          {"role": "user", "content": prompt}],
                max_tokens=50
            )
            summary = response.choices[0].message["content"].strip()
            if "ignore" not in summary.lower():
                meaningful.append(summary)
        except Exception as e:
            logging.error(f"AI error: {e}")
    return meaningful

def filter_new(items, cache):
    """Remove duplicates already in cache."""
    new_items = []
    for item in items:
        if item not in [c["headline"] for c in cache]:
            new_items.append(item)
            cache.append({"headline": item, "timestamp": str(datetime.now())})
    return new_items, cache

def send_whatsapp(updates):
    """Send updates to WhatsApp via Twilio."""
    if not updates:
        return
    body = "🔔 Latest Updates:\n" + "\n".join([f"• {u}" for u in updates])

    client = Client(TWILIO_SID, TWILIO_TOKEN)

    for attempt in range(3):
        try:
            msg = client.messages.create(
                from_=f"whatsapp:{TWILIO_FROM}",
                to=f"whatsapp:{TARGET_WHATSAPP}",
                body=body
            )
            logging.info(f"WhatsApp message sent: {msg.sid}")
            break
        except Exception as e:
            wait = 2 ** attempt
            logging.error(f"WhatsApp send failed (retrying in {wait}s): {e}")
            time.sleep(wait)

def agent_loop():
    logging.info("Agent loop started")
    config = load_config()
    cache = load_cache()
    all_updates = []

    for site in config:
        url, selector = site["url"], site.get("selector", "h2")
        headlines = fetch_site(url, selector)
        summaries = analyze_with_ai(headlines)
        new_items, cache = filter_new(summaries, cache)
        all_updates.extend(new_items)

    save_cache(cache)
    send_whatsapp(all_updates)

# ====================
# Scheduling
# ====================
def graceful_shutdown(signum, frame):
    logging.info("Shutting down agent...")
    exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        agent_loop()
    else:
        scheduler = BlockingScheduler()
        scheduler.add_job(agent_loop, "interval", minutes=30)
        logging.info("Agent scheduled every 30 minutes.")
        scheduler.start()
