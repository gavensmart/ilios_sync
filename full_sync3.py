#!/usr/bin/env python3
import requests, json, os, subprocess, time
from icalendar import Calendar, Event
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as SACredentials
from googleapiclient.discovery import build


# === PATH SETUP (absolute) ===
ROOT                = os.path.dirname(os.path.abspath(__file__))
LATEST_ICS_PATH     = os.path.join(ROOT, "ilios_latest.ics")
STORED_JSON_PATH    = os.path.join(ROOT, "stored_events.json")
FULL_ICS_PATH       = os.path.join(ROOT, "ilios_full.ics")
MIRRORED_JSON_PATH  = os.path.join(ROOT, "mirrored_uids.json")
CREDENTIALS_PATH    = os.path.join(ROOT, "credentials.json")
TOKEN_PATH          = os.path.join(ROOT, "token.json")

# === CONFIGURATION ===
RAW_ILIOS_URL       = "https://curriculum.ufhealth.org/ics/0c819f55f781dc23c435b089286515e50aab926171a6cf71fe475fd11479c711"
SYNCED_CALENDAR_ID  = "44dede995f08d9ed486418a90a975a646cacd5ba4ab4fad2dfd79a5a734fc987@group.calendar.google.com"

SCOPES = ['https://www.googleapis.com/auth/calendar']
COLOR_MAP = {
    "lecture":    "2",
    "lab":        "5",
    "exam":       "7",
    "discussion": "3",
    "anatomy":    "5",
    "required": "4",

}

# === STEP 1: FETCH LATEST ILIOS CALENDAR ===
def fetch_calendar():
    print("🔄 Downloading latest Ilios .ics file...")
    r = requests.get(RAW_ILIOS_URL)
    if r.status_code == 200:
        with open(LATEST_ICS_PATH, "wb") as f:
            f.write(r.content)
        print("✅ Downloaded ilios_latest.ics")
    else:
        raise RuntimeError(f"❌ Failed to fetch Ilios calendar (HTTP {r.status_code})")

# === STEP 2: PARSE AND STORE EVENTS WITH COMPOSITE-KEY DEDUPE ===
def parse_and_store():
    print("📦 Parsing events (dedupe by summary+start)...")
    cal = Calendar.from_ical(open(LATEST_ICS_PATH, "rb").read())

    # Load or start fresh
    if os.path.exists(STORED_JSON_PATH):
        stored = json.load(open(STORED_JSON_PATH))
    else:
        stored = []

    # One-time cleanup: remove any existing duplicates in stored
    unique = {}
    for e in stored:
        key = f"{e['summary']}|{e['dtstart']}"
        unique[key] = e
    stored = list(unique.values())

    # Build set of existing composite keys
    existing_keys = set(unique.keys())

    new_events = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        summary = str(comp.get("summary"))
        dtstart = comp.decoded("dtstart").isoformat()
        dtend   = comp.decoded("dtend").isoformat()
        description = str(comp.get("description","")).strip()

        key = f"{summary}|{dtstart}"
        if key in existing_keys:
            continue

        new_events.append({
            "uid":         str(comp.get("uid")),
            "summary":     summary,
            "description": description,
            "dtstart":     dtstart,
            "dtend":       dtend
        })
        existing_keys.add(key)

    # Merge and save
    stored.extend(new_events)
    with open(STORED_JSON_PATH, "w") as f:
        json.dump(stored, f, indent=2)

    print(f"✅ Added {len(new_events)} new event(s). Total stored: {len(stored)}")

# === STEP 3: GENERATE FULL .ICS ===
def generate_ics():
    print("🧱 Generating ilios_full.ics...")
    stored = json.load(open(STORED_JSON_PATH))
    cal = Calendar()
    cal.add("prodid", "-//Custom Ilios Calendar//mxm.dk//")
    cal.add("version", "2.0")

    for evt in stored:
        e = Event()
        e.add("summary",     evt["summary"])
        e.add("uid",         evt["uid"])
        e.add("description", evt.get("description",""))
        e.add("dtstart",     datetime.fromisoformat(evt["dtstart"]))
        e.add("dtend",       datetime.fromisoformat(evt["dtend"]))
        cal.add_component(e)

    with open(FULL_ICS_PATH, "wb") as f:
        f.write(cal.to_ical())
    print("✅ ilios_full.ics created")

# === STEP 4: PUSH TO GITHUB ===
def push_to_github():
    print("🚀 Committing & pushing to GitHub…")
    try:
        subprocess.run(["git","add","ilios_full.ics"], check=True)
        subprocess.run(["git","commit","-m","Update ilios_full.ics"], check=True)
        subprocess.run(["git","push"], check=True)
        print("✅ GitHub push complete")
    except subprocess.CalledProcessError:
        print("⚠️ Nothing to commit")

# === STEP 5: MIRROR + COLOR TO GOOGLE CALENDAR ===
from google.oauth2.service_account import Credentials as SACredentials

def authenticate_google():
    key_path = os.path.join(ROOT, "service_account.json")
    creds = SACredentials.from_service_account_file(
        key_path,
        scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds)

def mirror_and_color():
    print("📆 Syncing & coloring events…")
    cwd = os.getcwd()
    print(f"DEBUG ▶️ cwd: {cwd}")

    # ✅ Gracefully load mirrored_uids.json (skip if not found)
    mirrored_uids = set()
    try:
        with open("mirrored_uids.json", "r") as f:
            mirrored_uids = set(json.load(f))
    except FileNotFoundError:
        print("⚠️ mirrored_uids.json not found, starting fresh...")

    print(f"DEBUG ▶️ pre-run mirrored count: {len(mirrored_uids)}")

    added = skipped = 0
    for evt in events:
        key = f"{evt['summary']}|{evt['dtstart']}"
        if key in mirrored:
            skipped += 1
            continue

        orig_desc = evt.get("description","").strip()
        desc_uid  = f"{orig_desc}\nUID:{evt['uid']}".strip()
        combo     = (evt["summary"] + " " + orig_desc).lower()
        color     = next((c for k,c in COLOR_MAP.items() if k in combo), None)

        body = {
            "summary":     evt["summary"],
            "description": desc_uid,
            "start":       {"dateTime":evt["dtstart"], "timeZone":"UTC"},
            "end":         {"dateTime":evt["dtend"],   "timeZone":"UTC"}
        }
        if color:
            body["colorId"] = color

        try:
            service.events().insert(calendarId=SYNCED_CALENDAR_ID, body=body).execute()
            mirrored.add(key)
            print(f"✅ Added: {evt['summary']} → color {color}")
            added += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"❌ Failed to add {evt['summary']}: {e}")

    # Persist mirrored keys
    with open(MIRRORED_JSON_PATH,"w") as f:
        json.dump(list(mirrored), f, indent=2)

    print("DEBUG ▶️ post-run mirrored count:", len(mirrored))
    print(f"\n✅ Done. Added: {added}, Skipped: {skipped}")

# === MAIN ===
def main():
    fetch_calendar()
    parse_and_store()
    generate_ics()
    #push_to_github()
    mirror_and_color()

if __name__ == "__main__":
    main()