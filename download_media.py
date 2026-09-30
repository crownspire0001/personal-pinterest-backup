import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright


# ============================================================
# SETTINGS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "backup"

PINS_FILE = BACKUP_DIR / "pins.json"
STATE_FILE = BACKUP_DIR / "media_state.json"
MEDIA_DIR = BACKUP_DIR / "media"

BROWSER_PROFILE = BASE_DIR / "media_browser"

# Number of simultaneous downloads.
CONCURRENCY = 5

# Attempts per URL.
MAX_RETRIES = 2

# IMPORTANT:
# Browser runs completely in the background.
HEADLESS = True

TIMEOUT = 30000
RETRY_DELAY = 1.5


# ============================================================
# JSON
# ============================================================

def load_json(path, default):

    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:

        print(f"Could not read {path}: {e}")
        return default


def save_json(path, data):

    temp = path.with_suffix(".tmp")

    with open(temp, "w", encoding="utf-8") as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    temp.replace(path)


# ============================================================
# EXTENSION
# ============================================================

def get_extension(content_type, url):

    content_type = (
        content_type or ""
    ).split(";")[0].strip().lower()

    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
    }

    if content_type in mapping:
        return mapping[content_type]

    suffix = Path(
        urlparse(url).path
    ).suffix.lower()

    if suffix in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".avif",
    }:
        return suffix

    return ".jpg"


# ============================================================
# COLLECT MEDIA
# ============================================================

def collect_media():

    pins = load_json(
        PINS_FILE,
        {}
    )

    if not isinstance(pins, dict):

        print(
            "ERROR: pins.json format is invalid."
        )

        return []

    media = []
    seen_urls = set()

    for pin_id, pin in pins.items():

        if not isinstance(pin, dict):
            continue

        real_pin_id = str(
            pin.get("id", pin_id)
        )

        raw_media = pin.get(
            "raw_media",
            []
        )

        if not isinstance(raw_media, list):
            continue

        for index, item in enumerate(raw_media):

            if not isinstance(item, dict):
                continue

            url = item.get("url")

            if not isinstance(url, str):
                continue

            url = url.strip()

            if not url.startswith("http"):
                continue

            if url in seen_urls:
                continue

            seen_urls.add(url)

            media.append({
                "pin_id": real_pin_id,
                "index": index,
                "url": url,
            })

    return media


# ============================================================
# CREATE PAGE
# ============================================================

async def create_page(context):

    page = await context.new_page()

    page.set_default_navigation_timeout(
        TIMEOUT
    )

    page.set_default_timeout(
        TIMEOUT
    )

    return page


# ============================================================
# GET FILENAME FROM STATE
#
# Supports BOTH:
#
# Old:
# "123_image_0.jpg"
#
# New:
# {
#     "pin_id": "123",
#     "file": "123_image_0.jpg",
#     "size": 12345
# }
# ============================================================

def get_state_filename(entry):

    if isinstance(entry, dict):

        filename = entry.get("file")

        if isinstance(filename, str):
            return filename

        return None

    if isinstance(entry, str):
        return entry

    return None


# ============================================================
# CHECK WHETHER STATE ENTRY HAS A REAL FILE
# ============================================================

def state_file_exists(entry):

    filename = get_state_filename(entry)

    if not filename:
        return False

    # IMPORTANT:
    # Only the filename is used.
    # This prevents old folder paths from being followed.
    filename = Path(filename).name

    return (
        MEDIA_DIR / filename
    ).exists()


# ============================================================
# WORKER
# ============================================================

async def worker(
    worker_id,
    context,
    queue,
    state,
    state_lock,
    counters,
    counters_lock,
    total,
):

    page = None

    try:

        page = await create_page(
            context
        )

        print(
            f"Worker {worker_id} started."
        )

        while True:

            try:

                item_number, item = await queue.get()

            except asyncio.CancelledError:

                break

            url = item["url"]
            pin_id = item["pin_id"]
            media_index = item["index"]

            try:

                # ====================================================
                # CHECK EXISTING DOWNLOAD
                # ====================================================

                async with state_lock:

                    existing = state[
                        "downloaded"
                    ].get(url)

                if existing:

                    if state_file_exists(existing):

                        async with counters_lock:

                            counters[
                                "skipped"
                            ] += 1

                        continue

                    # ------------------------------------------------
                    # Stale state:
                    # state says downloaded, but file isn't there.
                    # Remove it so we download it again.
                    # ------------------------------------------------

                    async with state_lock:

                        state[
                            "downloaded"
                        ].pop(
                            url,
                            None
                        )

                        save_json(
                            STATE_FILE,
                            state
                        )

                success = False
                last_error = "Unknown error"

                # ====================================================
                # DOWNLOAD
                # ====================================================

                for attempt in range(
                    1,
                    MAX_RETRIES + 1
                ):

                    try:

                        # ------------------------------------------------
                        # Make sure page exists.
                        # ------------------------------------------------

                        if page is None or page.is_closed():

                            if page is not None:

                                try:
                                    await page.close()
                                except Exception:
                                    pass

                            page = await create_page(
                                context
                            )

                        # ------------------------------------------------
                        # Navigate directly to media URL.
                        # ------------------------------------------------

                        response = await page.goto(
                            url,
                            wait_until="commit",
                            timeout=TIMEOUT
                        )

                        if response is None:

                            raise Exception(
                                "No HTTP response"
                            )

                        status = response.status

                        # ------------------------------------------------
                        # 403
                        # ------------------------------------------------

                        if status == 403:

                            raise Exception(
                                "HTTP 403"
                            )

                        # ------------------------------------------------
                        # Other HTTP errors
                        # ------------------------------------------------

                        if status != 200:

                            raise Exception(
                                f"HTTP {status}"
                            )

                        # ------------------------------------------------
                        # Get response
                        # ------------------------------------------------

                        content_type = (
                            response.headers.get(
                                "content-type",
                                ""
                            )
                        )

                        body = await response.body()

                        if not body:

                            raise Exception(
                                "Empty response body"
                            )

                        # ------------------------------------------------
                        # Filename
                        #
                        # ALWAYS FLAT.
                        # NEVER create subfolders.
                        # ------------------------------------------------

                        extension = get_extension(
                            content_type,
                            url
                        )

                        filename = (
                            f"{pin_id}_image_"
                            f"{media_index}"
                            f"{extension}"
                        )

                        output = (
                            MEDIA_DIR /
                            filename
                        )

                        # ------------------------------------------------
                        # Save file
                        # ------------------------------------------------

                        if not output.exists():

                            output.write_bytes(
                                body
                            )

                        # ------------------------------------------------
                        # Record successful download
                        # ------------------------------------------------

                        async with state_lock:

                            state[
                                "downloaded"
                            ][url] = {

                                "pin_id": pin_id,

                                "file": filename,

                                "size": len(body),

                            }

                            state[
                                "failed"
                            ].pop(
                                url,
                                None
                            )

                            save_json(
                                STATE_FILE,
                                state
                            )

                        async with counters_lock:

                            counters[
                                "downloaded"
                            ] += 1

                        print(
                            f"[{item_number}/{total}] "
                            f"OK  {filename} "
                            f"({len(body) / 1024:.1f} KB)"
                        )

                        success = True

                        break

                    except Exception as e:

                        last_error = str(e)

                        # ------------------------------------------------
                        # Recreate broken page.
                        # ------------------------------------------------

                        try:

                            if (
                                page is None
                                or page.is_closed()
                            ):

                                page = await create_page(
                                    context
                                )

                        except Exception:

                            try:

                                if page is not None:
                                    await page.close()

                            except Exception:
                                pass

                            page = await create_page(
                                context
                            )

                        # ------------------------------------------------
                        # Retry
                        # ------------------------------------------------

                        if attempt < MAX_RETRIES:

                            print(
                                f"[{item_number}/{total}] "
                                f"Retry {attempt}/"
                                f"{MAX_RETRIES - 1} "
                                f"{pin_id}: "
                                f"{last_error}"
                            )

                            await asyncio.sleep(
                                RETRY_DELAY
                            )

                        else:

                            break

                # ====================================================
                # FAILED
                # ====================================================

                if not success:

                    async with state_lock:

                        state[
                            "failed"
                        ][url] = {

                            "pin_id": pin_id,

                            "index": media_index,

                            "error": last_error,

                        }

                        save_json(
                            STATE_FILE,
                            state
                        )

                    async with counters_lock:

                        counters[
                            "failed"
                        ] += 1

                    print(
                        f"[{item_number}/{total}] "
                        f"FAILED {pin_id}: "
                        f"{last_error}"
                    )

            finally:

                queue.task_done()

    except asyncio.CancelledError:

        pass

    finally:

        if page is not None:

            try:
                await page.close()
            except Exception:
                pass

        print(
            f"Worker {worker_id} stopped."
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    # ============================================================
    # ONLY CREATE FLAT MEDIA DIRECTORY
    # ============================================================

    MEDIA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print()
    print("=" * 60)
    print("PINTEREST MEDIA DOWNLOADER")
    print("=" * 60)
    print()

    print(
        "Media storage: FLAT"
    )

    print(
        f"Directory: {MEDIA_DIR}"
    )

    print(
        "Browser: HEADLESS"
    )

    print()

    # ============================================================
    # COLLECT
    # ============================================================

    media = collect_media()

    if not media:

        print(
            "No media URLs found."
        )

        return

    # ============================================================
    # STATE
    # ============================================================

    state = load_json(
        STATE_FILE,
        {
            "downloaded": {},
            "failed": {},
        }
    )

    if not isinstance(state, dict):

        state = {}

    state.setdefault(
        "downloaded",
        {}
    )

    state.setdefault(
        "failed",
        {}
    )

    # ============================================================
    # COUNTS
    # ============================================================

    total = len(media)

    already_completed = 0

    for item in media:

        url = item["url"]

        existing = state[
            "downloaded"
        ].get(url)

        if not existing:
            continue

        if state_file_exists(existing):

            already_completed += 1

    remaining = (
        total -
        already_completed
    )

    print(
        f"Discovered media: {total}"
    )

    print(
        f"Already completed: {already_completed}"
    )

    print(
        f"Remaining: {remaining}"
    )

    print(
        f"Concurrency: {CONCURRENCY}"
    )

    print(
        f"Headless: {HEADLESS}"
    )

    print()

    if remaining == 0:

        print(
            "Everything is already downloaded."
        )

        return

    # ============================================================
    # QUEUE
    # ============================================================

    queue = asyncio.Queue()

    for item_number, item in enumerate(
        media,
        start=1
    ):

        url = item["url"]

        existing = state[
            "downloaded"
        ].get(url)

        if existing and state_file_exists(existing):

            continue

        await queue.put(
            (
                item_number,
                item
            )
        )

    # ============================================================
    # COUNTERS
    # ============================================================

    counters = {

        "downloaded": 0,

        "skipped": already_completed,

        "failed": 0,

    }

    state_lock = asyncio.Lock()
    counters_lock = asyncio.Lock()

    # ============================================================
    # PLAYWRIGHT
    # ============================================================

    async with async_playwright() as p:

        print(
            "Starting Chromium in background..."
        )

        print()

        context = (
            await p.chromium.launch_persistent_context(

                user_data_dir=str(
                    BROWSER_PROFILE
                ),

                headless=HEADLESS,

                viewport={
                    "width": 1280,
                    "height": 720,
                },

                args=[

                    "--disable-gpu",

                    "--disable-dev-shm-usage",

                    "--no-first-run",

                    "--no-default-browser-check",

                ],

            )
        )

        workers = []

        try:

            # ------------------------------------------------
            # Start workers.
            # ------------------------------------------------

            for worker_id in range(
                1,
                CONCURRENCY + 1
            ):

                task = asyncio.create_task(

                    worker(

                        worker_id,

                        context,

                        queue,

                        state,

                        state_lock,

                        counters,

                        counters_lock,

                        total,

                    )

                )

                workers.append(task)

            # ------------------------------------------------
            # Wait for everything.
            # ------------------------------------------------

            await queue.join()

        except KeyboardInterrupt:

            print()
            print(
                "Stopping downloader..."
            )

        finally:

            # ------------------------------------------------
            # Stop workers.
            # ------------------------------------------------

            for task in workers:

                task.cancel()

            await asyncio.gather(

                *workers,

                return_exceptions=True

            )

            try:

                await context.close()

            except Exception:
                pass

    # ============================================================
    # FINAL STATE
    # ============================================================

    state = load_json(

        STATE_FILE,

        {
            "downloaded": {},
            "failed": {},
        }

    )

    total_successful = len(
        state.get(
            "downloaded",
            {}
        )
    )

    total_failed = len(
        state.get(
            "failed",
            {}
        )
    )

    print()
    print("=" * 60)
    print("DOWNLOAD RUN FINISHED")
    print("=" * 60)
    print()

    print(
        f"Discovered media:       {total}"
    )

    print(
        f"Previously completed:   "
        f"{already_completed}"
    )

    print(
        f"Downloaded this run:    "
        f"{counters['downloaded']}"
    )

    print(
        f"Failed this run:        "
        f"{counters['failed']}"
    )

    print(
        f"Total successful:       "
        f"{total_successful}"
    )

    print(
        f"Total recorded failed:  "
        f"{total_failed}"
    )

    print()

    print(
        "Media directory:"
    )

    print(
        MEDIA_DIR
    )

    print()

    print(
        "State file:"
    )

    print(
        STATE_FILE
    )

    print()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())