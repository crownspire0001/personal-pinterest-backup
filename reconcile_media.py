import json
import re
import shutil
from pathlib import Path

BASE = Path(r"F:\Pinterest Backup")
BACKUP = BASE / "backup"
MEDIA = BACKUP / "media"

PINS_FILE = BACKUP / "pins.json"
BACKUP_FILE = BACKUP / "pins_before_reconcile.json"
REPORT_FILE = BACKUP / "reconcile_report.txt"

print("=" * 65)
print("PINTEREST MEDIA METADATA RECONCILIATION")
print("=" * 65)

# ------------------------------------------------------------
# Load pins
# ------------------------------------------------------------

with open(PINS_FILE, "r", encoding="utf-8") as f:
    pins = json.load(f)

print(f"Pins loaded: {len(pins)}")

# ------------------------------------------------------------
# Safety backup
# ------------------------------------------------------------

if not BACKUP_FILE.exists():
    shutil.copy2(PINS_FILE, BACKUP_FILE)
    print(f"Safety backup created:")
    print(f"  {BACKUP_FILE}")
else:
    print("Safety backup already exists:")
    print(f"  {BACKUP_FILE}")

# ------------------------------------------------------------
# Build Pin ID lookup
# ------------------------------------------------------------

pin_ids = set(str(pid) for pid in pins.keys())

# ------------------------------------------------------------
# Existing paths
# ------------------------------------------------------------

existing_paths = {}

for pin_id, pin in pins.items():
    for item in pin.get("downloaded_media", []):
        path = item.get("local_path")

        if path:
            existing_paths.setdefault(str(pin_id), []).append(path)

# ------------------------------------------------------------
# Scan media files
# ------------------------------------------------------------

files = [
    p for p in MEDIA.iterdir()
    if p.is_file()
]

print(f"Media files found: {len(files)}")

# ------------------------------------------------------------
# Match filenames to Pin IDs
#
# Normal:
# 839428818071307876_1_xxxxx.jpg
#
# Recovered:
# 839428818074738855_recovered_1.png
# ------------------------------------------------------------

matched = {}
unmatched = []

for file in files:

    name = file.name

    match = re.match(r"^(\d+)_", name)

    if not match:
        unmatched.append(name)
        continue

    pin_id = match.group(1)

    if pin_id not in pin_ids:
        unmatched.append(name)
        continue

    matched.setdefault(pin_id, []).append(file)

# ------------------------------------------------------------
# Rebuild downloaded_media
# ------------------------------------------------------------

added = 0
already_present = 0

for pin_id, pin in pins.items():

    pin_id = str(pin_id)

    media_list = pin.setdefault("downloaded_media", [])

    # Existing local paths
    known_paths = {
        str(item.get("local_path")).replace("/", "\\")
        for item in media_list
        if item.get("local_path")
    }

    for file in matched.get(pin_id, []):

        relative_path = str(
            file.relative_to(BASE)
        ).replace("/", "\\")

        if relative_path in known_paths:
            already_present += 1
            continue

        # Determine media type
        suffix = file.suffix.lower()

        if suffix in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
            media_type = "image"
        elif suffix in [".mp4", ".mov", ".webm"]:
            media_type = "video"
        else:
            media_type = "media"

        media_list.append({
            "type": media_type,
            "local_path": relative_path
        })

        known_paths.add(relative_path)
        added += 1

# ------------------------------------------------------------
# Save repaired pins.json
# ------------------------------------------------------------

with open(PINS_FILE, "w", encoding="utf-8") as f:
    json.dump(
        pins,
        f,
        ensure_ascii=False,
        indent=2
    )

# ------------------------------------------------------------
# Statistics
# ------------------------------------------------------------

pins_with_media = sum(
    1 for pin in pins.values()
    if pin.get("downloaded_media")
)

recorded_media = sum(
    len(pin.get("downloaded_media", []))
    for pin in pins.values()
)

recovered_files = sum(
    1 for f in files
    if "_recovered_" in f.name
)

normal_files = len(files) - recovered_files

report = f"""
Pinterest Media Reconciliation Report
=====================================

Pins:
{len(pins)}

Media files:
{len(files)}

Normal files:
{normal_files}

Recovered files:
{recovered_files}

Pins with downloaded_media:
{pins_with_media}

Recorded downloaded_media:
{recorded_media}

New local_path records added:
{added}

Existing local_path records preserved:
{already_present}

Matched media files:
{sum(len(v) for v in matched.values())}

Unmatched media files:
{len(unmatched)}
"""

if unmatched:
    report += "\nUnmatched files:\n"
    for name in unmatched:
        report += f"  {name}\n"

with open(REPORT_FILE, "w", encoding="utf-8") as f:
    f.write(report)

print(report)

print("=" * 65)
print("RECONCILIATION COMPLETE")
print("=" * 65)

print()
print("pins.json updated.")
print(f"Report: {REPORT_FILE}")
print()
print("NO MEDIA FILES WERE DELETED.")
print("NO MEDIA FILES WERE DOWNLOADED.")
