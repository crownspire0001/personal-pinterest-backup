from pathlib import Path
import json
import shutil
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "backup"

PINS_FILE = BACKUP_DIR / "pins.json"
STATE_FILE = BACKUP_DIR / "media_state.json"
MEDIA_DIR = BACKUP_DIR / "media"

STATE_BACKUP = BACKUP_DIR / "media_state_before_reconcile.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    temp.replace(path)


def normalize_path(path_value):
    """
    Convert paths such as:

    backup\\media\\filename.jpg
    F:\\Pinterest Backup\\backup\\media\\filename.jpg

    into a real local path where possible.
    """

    if not path_value:
        return None

    p = Path(str(path_value))

    if p.is_absolute():
        return p

    text = str(path_value).replace("/", "\\")

    marker = "backup\\media\\"

    if marker.lower() in text.lower():
        relative = text.lower().split(marker.lower(), 1)[1]
        return MEDIA_DIR / relative

    # Fallback: filename only
    return MEDIA_DIR / Path(text).name


def file_exists_from_entry(entry):
    if isinstance(entry, str):
        path = entry
    elif isinstance(entry, dict):
        path = (
            entry.get("local_path")
            or entry.get("file")
            or entry.get("path")
        )
    else:
        return None

    if not path:
        return None

    real_path = normalize_path(path)

    if real_path and real_path.is_file():
        return real_path

    return None


def main():
    print()
    print("========================================")
    print(" RECONCILE MEDIA STATE")
    print("========================================")
    print()

    if not PINS_FILE.exists():
        print("ERROR: pins.json not found.")
        return

    if not STATE_FILE.exists():
        print("ERROR: media_state.json not found.")
        return

    # ------------------------------------------------------------
    # Load
    # ------------------------------------------------------------

    pins = load_json(PINS_FILE)
    state = load_json(STATE_FILE)

    if not isinstance(state, dict):
        print("ERROR: Unexpected media_state.json format.")
        return

    downloaded = state.get("downloaded", {})
    failed = state.get("failed", {})

    if not isinstance(downloaded, dict):
        downloaded = {}

    if not isinstance(failed, dict):
        failed = {}

    print(f"Pins:                  {len(pins):,}")
    print(f"State downloaded:      {len(downloaded):,}")
    print(f"State failed:          {len(failed):,}")
    print()

    # ------------------------------------------------------------
    # Safety backup
    # ------------------------------------------------------------

    if not STATE_BACKUP.exists():
        shutil.copy2(STATE_FILE, STATE_BACKUP)
        print(f"Safety backup created:")
        print(STATE_BACKUP)
    else:
        print("Safety backup already exists:")
        print(STATE_BACKUP)

    print()

    # ------------------------------------------------------------
    # Build lookup from pin ID + media index
    # ------------------------------------------------------------

    media_lookup = {}

    for pin_id, pin in pins.items():

        if not isinstance(pin, dict):
            continue

        downloaded_media = pin.get("downloaded_media", [])

        if not isinstance(downloaded_media, list):
            continue

        for index, entry in enumerate(downloaded_media):

            real_file = file_exists_from_entry(entry)

            if not real_file:
                continue

            key = (str(pin_id), index)

            media_lookup.setdefault(key, []).append(real_file)

    print(f"Usable pin/media mappings: {len(media_lookup):,}")
    print()

    # ------------------------------------------------------------
    # Examine failed entries
    # ------------------------------------------------------------

    recovered = 0
    already_correct = 0
    unresolved = 0

    new_downloaded = dict(downloaded)
    new_failed = dict(failed)

    changes = []

    for failed_url, failed_entry in list(failed.items()):

        if not isinstance(failed_entry, dict):
            unresolved += 1
            continue

        pin_id = failed_entry.get("pin_id")
        index = failed_entry.get("index")

        if pin_id is None or index is None:
            unresolved += 1
            continue

        key = (str(pin_id), int(index))

        candidates = media_lookup.get(key, [])

        # Remove duplicate candidate paths
        unique_candidates = []

        for candidate in candidates:
            candidate = candidate.resolve()

            if candidate not in unique_candidates:
                unique_candidates.append(candidate)

        if len(unique_candidates) == 1:

            file_path = unique_candidates[0]

            try:
                size = file_path.stat().st_size
            except OSError:
                unresolved += 1
                continue

            # Mark the ORIGINAL failed URL as successfully represented
            # by the existing recovered/local media file.
            new_downloaded[failed_url] = {
                "pin_id": str(pin_id),
                "file": file_path.name,
                "size": size,
                "reconciled": True,
                "reconciled_at": datetime.now().isoformat(timespec="seconds"),
            }

            # Remove it from failed.
            del new_failed[failed_url]

            recovered += 1

            changes.append(
                (
                    str(pin_id),
                    int(index),
                    file_path.name,
                )
            )

        elif len(unique_candidates) > 1:
            print(
                f"AMBIGUOUS: pin={pin_id} index={index} "
                f"candidates={len(unique_candidates)}"
            )
            unresolved += 1

        else:
            unresolved += 1

    # ------------------------------------------------------------
    # Safety check
    # ------------------------------------------------------------

    print("RESULT")
    print("----------------------------------------")
    print(f"Failed entries before:       {len(failed):,}")
    print(f"Reconciled successfully:     {recovered:,}")
    print(f"Still unresolved:            {unresolved:,}")
    print(f"Downloaded after repair:     {len(new_downloaded):,}")
    print(f"Failed after repair:         {len(new_failed):,}")
    print()

    if unresolved > 0:
        print("WARNING:")
        print("Some failed entries could not be safely mapped.")
        print("Nothing will be written because the repair is incomplete.")
        print()
        print("Your original media_state.json remains untouched.")
        return

    # ------------------------------------------------------------
    # Write repaired state
    # ------------------------------------------------------------

    repaired_state = dict(state)

    repaired_state["downloaded"] = new_downloaded
    repaired_state["failed"] = new_failed

    save_json(STATE_FILE, repaired_state)

    print("STATE REPAIRED SUCCESSFULLY")
    print("----------------------------------------")
    print(f"Recovered failed entries:   {recovered:,}")
    print(f"Remaining failed entries:   {len(new_failed):,}")
    print(f"Total downloaded entries:   {len(new_downloaded):,}")
    print()

    if changes:
        print("Reconciled files:")
        for pin_id, index, filename in changes:
            print(f"  Pin {pin_id} / media {index} -> {filename}")

    print()
    print("Next step:")
    print("    VERIFY_BACKUP.bat")
    print()


if __name__ == "__main__":
    main()