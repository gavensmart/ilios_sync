#!/usr/bin/env python3
import requests, json, os, subprocess, time
from icalendar import Calendar, Event
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as SACredentials

# === PATH SETUP (absolute) ===
ROOT                = os.path.dirname(os.path.abspath(__file__))
LATEST_ICS_PATH     = os.path.join(ROOT, "ilios_latest.ics")
STORED_JSON_PATH    = os.path.join(ROOT, "stored_events.json")
FULL_ICS_PATH       = os.path.join(ROOT, "ilios_full.ics")
MIRRORED_JSON_PATH  = os.path.join(ROOT, "mirrored_uids.json")
SERVICE_ACCOUNT_PATH = os.path.join(ROOT, "service_account.json")

# === CONFIGURATION ===
RAW_ILIOS_URL       = "https://curriculum.ufhealth.org/ics/0c819f55f781dc23c435b089286515e50aab926171a6cf71fe475fd11479c711"
PUBLIC_CALENDAR_ID  = "2187de2770511a23a663ff47a97c476fe6845850299dbab3d690cd6f10bd8ed4@group.calendar.google.com"
PRIVATE_CALENDAR_ID = "8dea85b44750d38fb4078c1b591061ed4e0b1e1c264711845f6d8fdf262e433b@group.calendar.google.com"

# Filenames we’ll use to track which UIDs have been mirrored
MIRRORED_JSON_PRIVATE = os.path.join(ROOT, "mirrored_uids_private.json")
MIRRORED_JSON_PUBLIC  = os.path.join(ROOT, "mirrored_uids_public.json")

SCOPES = ['https://www.googleapis.com/auth/calendar']
COLOR_MAP = {
    "lecture":    "10",
    "anatomy lab quiz": "7",
    "Q&A": "8",
    "holiday": "8",
    "lab":        "5",
    "exam":       "7",
    "discussion, large group": "8",
    "discussion, small group": "4",
    "anatomy lab":    "5",
    "quiz": "7",
    "required":   "4",
    "simulation": "8",
    "optional": "8",
    "independent learning": "8",
    "clinical skills practical": "7",
    "clinical skills practical prep session": "8",
    

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

    # Remove existing duplicates by composite key
    unique = {}
    for e in stored:
        key = f"{e['summary']}|{e['dtstart']}"
        unique[key] = e
    stored = list(unique.values())
    existing_keys = set(unique.keys())

    new_events = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        summary     = str(comp.get("summary"))
        dtstart     = comp.decoded("dtstart").isoformat()
        dtend       = comp.decoded("dtend").isoformat()
        description = str(comp.get("description", "")).strip()

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
        e.add("description", evt.get("description", ""))
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
        subprocess.run(["git", "add", "ilios_full.ics"], check=True)
        subprocess.run(["git", "commit", "-m", "Update ilios_full.ics"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("✅ GitHub push complete")
    except subprocess.CalledProcessError:
        print("⚠️ Nothing to commit")

# === STEP 5: MIRROR + COLOR TO GOOGLE CALENDAR ===
def authenticate_google():
    creds = SACredentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)

def mirror_and_color():
    print("📆 Syncing & coloring events…")
    service = authenticate_google()

    # Load all events we parsed earlier
    with open(STORED_JSON_PATH, "r") as f:
        events = json.load(f)

    # Prepare per-calendar mirrored-UID sets
    mirrored = {}
    for cal_id, mirror_path in [
        (PRIVATE_CALENDAR_ID, MIRRORED_JSON_PRIVATE),
        (PUBLIC_CALENDAR_ID,  MIRRORED_JSON_PUBLIC)
    ]:
        if os.path.exists(mirror_path):
            with open(mirror_path, "r") as f:
                mirrored[cal_id] = set(json.load(f))
        else:
            print(f"⚠️ {os.path.basename(mirror_path)} not found, starting fresh for {cal_id!r}")
            mirrored[cal_id] = set()

    # Sort COLOR_MAP by descending keyword length for specificity
    sorted_keywords = sorted(COLOR_MAP.items(), key=lambda item: -len(item[0]))

    added = skipped = 0
    for evt in events:
        key = f"{evt['summary']}|{evt['dtstart']}"

        description = (evt.get("description","").strip() + f"\nUID:{evt['uid']}").strip()
        combo = (evt["summary"] + " " + evt.get("description","")).lower()

    # Explicit anatomy-lab-quiz override
        if "anatomy lab" in combo and "quiz" in combo:
            color_id = "7"
        else:
            color_id = next((cid for kw, cid in sorted_keywords if kw in combo), None)

    # Build the body for *every* event
        body = {
         "summary":     evt["summary"],
         "description": description,
         "start":       {"dateTime": evt["dtstart"], "timeZone": "UTC"},
         "end":         {"dateTime": evt["dtend"],   "timeZone": "UTC"}
    }
    # Only add the colorId field if we have one
        if color_id:
          body["colorId"] = color_id

    # Now insert into each calendar unconditionally
        for cal_id, uids in mirrored.items():
         if key in uids:
              skipped += 1
              continue
         try:
             service.events().insert(calendarId=cal_id, body=body).execute()
             uids.add(key)
             print(f"✅ Added to {cal_id.split('@')[0]}: {evt['summary']} → color {color_id or 'default'}")
             added += 1
             time.sleep(0.3)
         except Exception as e:
            print(f"❌ Failed to add to {cal_id.split('@')[0]}: {evt['summary']} — {e}")

# === MAIN EXECUTION ===
def main():
    fetch_calendar()
    parse_and_store()
    generate_ics()
    push_to_github()
    mirror_and_color()

if __name__ == "__main__":
    main()