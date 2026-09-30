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

STATE_FILE = BACKUP_DIR / "media_state.json"
MEDIA_DIR = BACKUP_DIR / "media"

BROWSER_PROFILE = BASE_DIR / "media_browser"

CONCURRENCY = 5
MAX_RETRIES = 3
TIMEOUT = 30000
RETRY_DELAY = 2

# Keep browser visible.
HEADLESS = False


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
    total
):

    page = None

    try:

        page = await context.new_page()

        page.set_default_navigation_timeout(
            TIMEOUT
        )

        page.set_default_timeout(
            TIMEOUT
        )

        print(
            f"Worker {worker_id} started."
        )

        while True:

            try:
                number, url, info = await queue.get()
            except asyncio.CancelledError:
                break

            try:

                pin_id = str(
                    info.get(
                        "pin_id",
                        "unknown"
                    )
                )

                media_index = int(
                    info.get(
                        "index",
                        0
                    )
                )

                success = False
                last_error = "Unknown error"

                # ------------------------------------------------
                # Retry this failed URL.
                # ------------------------------------------------

                for attempt in range(
                    1,
                    MAX_RETRIES + 1
                ):

                    try:

                        # Recreate dead page.
                        if page.is_closed():

                            page = await context.new_page()

                            page.set_default_navigation_timeout(
                                TIMEOUT
                            )

                            page.set_default_timeout(
                                TIMEOUT
                            )

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

                        if status == 403:
                            raise Exception(
                                "HTTP 403"
                            )

                        if status != 200:
                            raise Exception(
                                f"HTTP {status}"
                            )

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
                            MEDIA_DIR / filename
                        )

                        if not output.exists():

                            output.write_bytes(
                                body
                            )

                        # ------------------------------------------------
                        # Mark as successful.
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
                                "success"
                            ] += 1

                        print(
                            f"[{number}/{total}] "
                            f"RETRY SUCCESS "
                            f"{filename} "
                            f"({len(body) / 1024:.1f} KB)"
                        )

                        success = True
                        break

                    except Exception as e:

                        last_error = str(e)

                        if attempt < MAX_RETRIES:

                            print(
                                f"[{number}/{total}] "
                                f"Retry {attempt}/"
                                f"{MAX_RETRIES - 1} "
                                f"{pin_id}: "
                                f"{last_error}"
                            )

                            await asyncio.sleep(
                                RETRY_DELAY
                            )

                # ------------------------------------------------
                # Still failed.
                # ------------------------------------------------

                if not success:

                    async with state_lock:

                        state[
                            "failed"
                        ][url] = {
                            **info,
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
                        f"[{number}/{total}] "
                        f"STILL FAILED "
                        f"{pin_id}: "
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

    MEDIA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print()
    print("=" * 60)
    print("PINTEREST FAILED MEDIA RETRY")
    print("=" * 60)
    print()

    state = load_json(
        STATE_FILE,
        {
            "downloaded": {},
            "failed": {},
        }
    )

    if not isinstance(state, dict):
        print(
            "ERROR: Invalid media_state.json"
        )
        return

    state.setdefault(
        "downloaded",
        {}
    )

    state.setdefault(
        "failed",
        {}
    )

    failed = state["failed"]

    if not failed:

        print(
            "There are no failed downloads."
        )

        print(
            "Your media backup is complete."
        )

        return

    # --------------------------------------------------------
    # Build retry list.
    # --------------------------------------------------------

    items = []

    for url, info in failed.items():

        if not isinstance(info, dict):
            info = {}

        items.append(
            (
                url,
                info
            )
        )

    total = len(items)

    print(
        f"Failed media to retry: {total}"
    )

    print(
        f"Concurrency: {CONCURRENCY}"
    )

    print(
        f"Max retries per URL: {MAX_RETRIES}"
    )

    print(
        f"Headless: {HEADLESS}"
    )

    print()

    queue = asyncio.Queue()

    for number, (url, info) in enumerate(
        items,
        start=1
    ):

        await queue.put(
            (
                number,
                url,
                info
            )
        )

    state_lock = asyncio.Lock()
    counters_lock = asyncio.Lock()

    counters = {
        "success": 0,
        "failed": 0,
    }

    # --------------------------------------------------------
    # Browser
    # --------------------------------------------------------

    async with async_playwright() as p:

        print(
            "Starting Chromium..."
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

            await queue.join()

        except KeyboardInterrupt:

            print()
            print(
                "Stopping retry process..."
            )

        finally:

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

    # --------------------------------------------------------
    # Final state
    # --------------------------------------------------------

    state = load_json(
        STATE_FILE,
        {
            "downloaded": {},
            "failed": {},
        }
    )

    remaining_failed = len(
        state.get(
            "failed",
            {}
        )
    )

    total_downloaded = len(
        state.get(
            "downloaded",
            {}
        )
    )

    print()
    print("=" * 60)
    print("FAILED RETRY FINISHED")
    print("=" * 60)
    print()

    print(
        f"Retried this run:       {total}"
    )

    print(
        f"Recovered this run:     "
        f"{counters['success']}"
    )

    print(
        f"Still failed:           "
        f"{counters['failed']}"
    )

    print(
        f"Total successful media: "
        f"{total_downloaded}"
    )

    print(
        f"Remaining failed URLs:  "
        f"{remaining_failed}"
    )

    print()

    print(
        f"Media directory:"
    )

    print(
        MEDIA_DIR
    )

    print()

    print(
        f"State file:"
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
