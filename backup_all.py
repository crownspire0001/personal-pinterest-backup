from pathlib import Path
from urllib.parse import urlparse
import json
import time
import re

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIG
# ============================================================

PROFILE = Path("pinterest_browser")

BACKUP_DIR = Path("backup")
PINS_FILE = BACKUP_DIR / "pins.json"
STATE_FILE = BACKUP_DIR / "state.json"
MANIFEST_FILE = BACKUP_DIR / "manifest.json"

BOARD_WAIT = 5000
PAGINATION_WAIT = 3000

MAX_PAGINATION = 20
MAX_RETRIES = 3


# ============================================================
# SETUP
# ============================================================

BACKUP_DIR.mkdir(exist_ok=True)


def load_json(path, default):

    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as error:
        print(
            f"WARNING: Could not read {path}: {error}"
        )
        return default


def save_json(path, data):

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    temp.replace(path)


# ============================================================
# LOAD MANIFEST
# ============================================================

manifest = load_json(
    MANIFEST_FILE,
    {}
)

raw_boards = manifest.get(
    "boards",
    []
)

boards = []

for item in raw_boards:

    if isinstance(item, str):

        boards.append(item)

    elif isinstance(item, dict):

        url = item.get("url")

        if url:
            boards.append(url)


boards = list(
    dict.fromkeys(boards)
)


# ============================================================
# LOAD DATABASE
# ============================================================

pins = load_json(
    PINS_FILE,
    {}
)

if isinstance(pins, list):

    pins = {
        str(pin["id"]): pin
        for pin in pins
        if isinstance(pin, dict)
        and pin.get("id")
    }


state = load_json(
    STATE_FILE,
    {
        "completed_boards": [],
        "board_stats": {},
        "current_board": None
    }
)


completed = set(
    state.get(
        "completed_boards",
        []
    )
)

board_stats = state.setdefault(
    "board_stats",
    {}
)


print()
print("=" * 70)
print("PINTEREST PIN METADATA BACKUP")
print("=" * 70)
print()

print(
    f"Boards in manifest: {len(boards)}"
)

print(
    f"Previously completed: {len(completed)}"
)

print(
    f"Existing Pins in database: {len(pins)}"
)

print()


# ============================================================
# HELPERS
# ============================================================

def board_name_from_url(url):

    path = urlparse(url).path

    parts = [
        x
        for x in path.rstrip("/").split("/")
        if x
    ]

    if not parts:
        return "Unknown Board"

    return (
        parts[-1]
        .replace("-", " ")
        .title()
    )


def extract_pin(raw):

    if not isinstance(raw, dict):
        return None

    pin_id = raw.get("id")

    seo_url = raw.get("seo_url")

    board = raw.get("board")


    # Reject Pinterest recommendation /
    # edge objects.
    if not pin_id:
        return None

    if not seo_url:
        return None

    if not isinstance(board, dict):
        return None

    if not board.get("id"):
        return None


    result = {

        "id": str(pin_id),

        "url": (
            "https://in.pinterest.com/"
            f"pin/{pin_id}/"
        ),

        "seo_url": seo_url,

        "title": raw.get("title"),

        "description": raw.get(
            "description"
        ),

        "auto_alt_text": raw.get(
            "auto_alt_text"
        ),

        "seo_alt_text": raw.get(
            "seo_alt_text"
        ),

        "link": raw.get("link"),

        "domain": raw.get("domain"),

        "image_signature": raw.get(
            "image_signature"
        ),

        "created_at": raw.get(
            "created_at"
        ),

        "is_video": raw.get(
            "is_video",
            False
        ),

        "video_status": raw.get(
            "video_status"
        ),

        "board": board,

        "pinner": raw.get(
            "pinner"
        ),

        "aggregated_pin_data": raw.get(
            "aggregated_pin_data"
        ),

        "native_creator": raw.get(
            "native_creator"
        ),

        "carousel_data": raw.get(
            "carousel_data"
        ),

        "raw_media": []
    }


    # ========================================================
    # NORMAL IMAGE
    # ========================================================

    images = raw.get(
        "images"
    )

    if isinstance(images, dict):

        original = images.get(
            "orig"
        )

        if isinstance(
            original,
            dict
        ):

            url = original.get(
                "url"
            )

            if url:

                result[
                    "raw_media"
                ].append({

                    "type": "image",

                    "url": url,

                    "source": (
                        "images.orig"
                    )
                })


    # ========================================================
    # STORY PIN
    # ========================================================

    story = raw.get(
        "story_pin_data"
    )

    if isinstance(
        story,
        dict
    ):

        pages = story.get(
            "pages",
            []
        )

        if isinstance(
            pages,
            list
        ):

            for page_number, story_page in enumerate(
                pages,
                start=1
            ):

                if not isinstance(
                    story_page,
                    dict
                ):
                    continue

                blocks = story_page.get(
                    "blocks",
                    []
                )

                if not isinstance(
                    blocks,
                    list
                ):
                    continue

                for block in blocks:

                    if not isinstance(
                        block,
                        dict
                    ):
                        continue

                    image = block.get(
                        "image"
                    )

                    if not isinstance(
                        image,
                        dict
                    ):
                        continue

                    image_set = image.get(
                        "images"
                    )

                    if not isinstance(
                        image_set,
                        dict
                    ):
                        continue

                    originals = image_set.get(
                        "originals"
                    )

                    if isinstance(
                        originals,
                        dict
                    ):

                        url = originals.get(
                            "url"
                        )

                        if url:

                            result[
                                "raw_media"
                            ].append({

                                "type": (
                                    "story_image"
                                ),

                                "url": url,

                                "page": (
                                    page_number
                                ),

                                "source": (
                                    "story_pin_data"
                                )
                            })


    # ========================================================
    # VIDEOS
    # ========================================================

    videos = raw.get(
        "videos"
    )

    if isinstance(
        videos,
        dict
    ):

        for video_key, video in videos.items():

            if not isinstance(
                video,
                dict
            ):
                continue

            url = video.get(
                "url"
            )

            if url:

                result[
                    "raw_media"
                ].append({

                    "type": "video",

                    "url": url,

                    "source": (
                        f"videos.{video_key}"
                    )
                })


    return result


# ============================================================
# PROCESS FEED
# ============================================================

def process_feed(
    response_data,
    board_name,
    board_url
):

    resource = response_data.get(
        "resource_response",
        {}
    )

    data = resource.get(
        "data",
        []
    )

    if not isinstance(
        data,
        list
    ):
        return 0, 0


    received = 0
    new_pins = 0


    for raw_pin in data:

        pin = extract_pin(
            raw_pin
        )

        if not pin:
            continue


        received += 1

        pin_id = pin["id"]


        membership = {

            "board_name": board_name,

            "board_url": board_url,

            "board_id": (
                pin["board"].get("id")
            )
        }


        # ----------------------------------------------------
        # Existing Pin
        # ----------------------------------------------------

        if pin_id in pins:

            existing = pins[
                pin_id
            ]

            saved_to = existing.setdefault(
                "saved_to",
                []
            )

            if membership not in saved_to:

                saved_to.append(
                    membership
                )


            # Update metadata without
            # destroying existing fields.
            for key, value in pin.items():

                if key == "raw_media":
                    continue

                if value is not None:

                    existing[key] = value


            # Preserve media discovered
            # previously.
            if pin.get(
                "raw_media"
            ):

                old_media = existing.setdefault(
                    "raw_media",
                    []
                )

                for media in pin[
                    "raw_media"
                ]:

                    if media not in old_media:

                        old_media.append(
                            media
                        )


        # ----------------------------------------------------
        # New Pin
        # ----------------------------------------------------

        else:

            pin[
                "saved_to"
            ] = [
                membership
            ]

            pins[
                pin_id
            ] = pin

            new_pins += 1


    return received, new_pins


# ============================================================
# PLAYWRIGHT
# ============================================================

with sync_playwright() as p:

    context = (
        p.chromium.launch_persistent_context(

            user_data_dir=str(
                PROFILE
            ),

            headless=False,

            viewport={
                "width": 1440,
                "height": 900
            },

            locale="en-US"
        )
    )


    page = (
        context.pages[0]
        if context.pages
        else context.new_page()
    )


    # ========================================================
    # CURRENT BOARD
    # ========================================================

    current = {

        "url": "",

        "name": "",

        "feeds": 0,

        "received": 0,

        "new": 0,

        "last_bookmark": None,

        "end_seen": False
    }


    processed_urls = set()


    # ========================================================
    # RESPONSE HANDLER
    # ========================================================

    def handle_response(response):

        if (
            "pinterest.com/resource/"
            "BoardFeedResource/get/"
            not in response.url
        ):
            return


        if not current["url"]:
            return


        # ----------------------------------------------------
        # Ignore responses belonging to another board.
        # ----------------------------------------------------

        current_path = urlparse(
            current["url"]
        ).path.rstrip("/")


        if current_path:

            slug = current_path.split(
                "/"
            )[-1]


            if (
                slug not in response.url
                and current_path not in response.url
            ):

                return


        # ----------------------------------------------------
        # Deduplicate exact resource.
        # ----------------------------------------------------

        if response.url in processed_urls:

            return

        processed_urls.add(
            response.url
        )


        try:

            body = response.body()

            if not body:
                return


            data = json.loads(
                body.decode(
                    "utf-8"
                )
            )


            received, new_pins = process_feed(

                data,

                current["name"],

                current["url"]
            )


            current[
                "feeds"
            ] += 1

            current[
                "received"
            ] += received

            current[
                "new"
            ] += new_pins


            resource = data.get(
                "resource_response",
                {}
            )

            bookmark = resource.get(
                "bookmark"
            )


            current[
                "last_bookmark"
            ] = bookmark


            if not bookmark:

                current[
                    "end_seen"
                ] = True


            print()

            print(
                f"      Feed #"
                f"{current['feeds']}"
            )

            print(
                f"      Pins received: "
                f"{received}"
            )

            print(
                f"      Board Pins seen: "
                f"{current['received']}"
            )

            print(
                f"      New Pins added: "
                f"{new_pins}"
            )


            if bookmark:

                print(
                    "      Bookmark: "
                    f"{bookmark[:40]}..."
                )

            else:

                print(
                    "      End of feed "
                    "reached."
                )


            # ------------------------------------------------
            # IMPORTANT:
            # Save metadata immediately.
            # ------------------------------------------------

            save_json(
                PINS_FILE,
                pins
            )


        except Exception as error:

            print(
                "      Feed processing "
                f"error: {error}"
            )


    page.on(
        "response",
        handle_response
    )


    # ========================================================
    # BOARD LOOP
    # ========================================================

    for index, board_url in enumerate(
        boards,
        start=1
    ):

        board_name = (
            board_name_from_url(
                board_url
            )
        )


        if board_url in completed:

            print(
                f"[{index}/{len(boards)}] "
                f"SKIP: {board_name}"
            )

            continue


        success = False


        # ====================================================
        # RETRIES
        # ====================================================

        for retry in range(
            1,
            MAX_RETRIES + 1
        ):

            print()
            print("=" * 70)
            print(
                f"BOARD {index}/"
                f"{len(boards)}"
            )
            print("=" * 70)
            print()

            print(
                f"Name: {board_name}"
            )

            print(
                f"URL:  {board_url}"
            )

            if retry > 1:

                print(
                    f"Retry: {retry}/"
                    f"{MAX_RETRIES}"
                )

            print()


            # ------------------------------------------------
            # Reset board state
            # ------------------------------------------------

            current[
                "url"
            ] = board_url

            current[
                "name"
            ] = board_name

            current[
                "feeds"
            ] = 0

            current[
                "received"
            ] = 0

            current[
                "new"
            ] = 0

            current[
                "last_bookmark"
            ] = None

            current[
                "end_seen"
            ] = False


            processed_urls.clear()


            state[
                "current_board"
            ] = board_url


            save_json(
                STATE_FILE,
                state
            )


            try:

                # ------------------------------------------------
                # Open board
                # ------------------------------------------------

                page.goto(

                    board_url,

                    wait_until=(
                        "domcontentloaded"
                    ),

                    timeout=60000
                )


                page.wait_for_timeout(
                    BOARD_WAIT
                )


                # ------------------------------------------------
                # Wait for initial feed.
                # ------------------------------------------------

                print(
                    "      Waiting for "
                    "initial feed..."
                )


                for _ in range(20):

                    if (
                        current[
                            "feeds"
                        ] > 0
                    ):
                        break

                    page.wait_for_timeout(
                        500
                    )


                if (
                    current[
                        "feeds"
                    ] == 0
                ):

                    raise RuntimeError(
                        "No BoardFeedResource "
                        "received"
                    )


                print()

                print(
                    f"      Initial feeds: "
                    f"{current['feeds']}"
                )

                print(
                    f"      Board Pins seen: "
                    f"{current['received']}"
                )


                # =================================================
                # PAGINATION
                # =================================================

                stable_rounds = 0

                previous_received = (
                    current[
                        "received"
                    ]
                )


                for attempt in range(
                    1,
                    MAX_PAGINATION + 1
                ):

                    print()

                    print(
                        f"      Pagination "
                        f"{attempt}/"
                        f"{MAX_PAGINATION}"
                    )


                    page.evaluate(
                        """
                        window.scrollTo(
                            0,
                            document.body.scrollHeight
                        );
                        """
                    )


                    page.wait_for_timeout(
                        PAGINATION_WAIT
                    )


                    current_received = (
                        current[
                            "received"
                        ]
                    )


                    if (
                        current_received
                        == previous_received
                    ):

                        stable_rounds += 1

                        print(
                            "      No additional "
                            "Pins received."
                        )

                    else:

                        stable_rounds = 0

                        print(
                            f"      Board total: "
                            f"{current_received}"
                        )


                    previous_received = (
                        current_received
                    )


                    # If Pinterest explicitly gave us
                    # the final page and nothing else is
                    # arriving, we're done.
                    if (
                        current[
                            "end_seen"
                        ]
                        and stable_rounds >= 2
                    ):

                        print(
                            "      Board "
                            "pagination complete."
                        )

                        break


                # ------------------------------------------------
                # Safety check
                # ------------------------------------------------

                if (
                    current[
                        "feeds"
                    ] == 0
                ):

                    raise RuntimeError(
                        "Zero feed responses"
                    )


                # ------------------------------------------------
                # Save statistics
                # ------------------------------------------------

                board_stats[
                    board_url
                ] = {

                    "name": board_name,

                    "feeds": (
                        current[
                            "feeds"
                        ]
                    ),

                    "pins_received": (
                        current[
                            "received"
                        ]
                    ),

                    "new_pins": (
                        current[
                            "new"
                        ]
                    ),

                    "end_seen": (
                        current[
                            "end_seen"
                        ]
                    ),

                    "status": "complete",

                    "updated": time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                }


                # ------------------------------------------------
                # Complete board
                # ------------------------------------------------

                completed.add(
                    board_url
                )


                state[
                    "completed_boards"
                ] = list(
                    completed
                )


                state[
                    "current_board"
                ] = None


                save_json(
                    PINS_FILE,
                    pins
                )

                save_json(
                    STATE_FILE,
                    state
                )


                print()
                print(
                    f"COMPLETED: "
                    f"{board_name}"
                )

                print(
                    f"Board Pins received: "
                    f"{current['received']}"
                )

                print(
                    f"New Pins added to "
                    f"database: "
                    f"{current['new']}"
                )

                print(
                    f"Total unique Pins: "
                    f"{len(pins)}"
                )

                print()


                success = True

                break


            except Exception as error:

                print()

                print(
                    "      BOARD ATTEMPT "
                    f"FAILED: {error}"
                )

                board_stats[
                    board_url
                ] = {

                    "name": board_name,

                    "feeds": (
                        current[
                            "feeds"
                        ]
                    ),

                    "pins_received": (
                        current[
                            "received"
                        ]
                    ),

                    "new_pins": (
                        current[
                            "new"
                        ]
                    ),

                    "end_seen": (
                        current[
                            "end_seen"
                        ]
                    ),

                    "status": "failed",

                    "updated": time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                }


                save_json(
                    PINS_FILE,
                    pins
                )

                save_json(
                    STATE_FILE,
                    state
                )


                if retry < MAX_RETRIES:

                    print(
                        "      Retrying board..."
                    )

                    page.wait_for_timeout(
                        3000
                    )

                else:

                    print(
                        "      Failed after "
                        f"{MAX_RETRIES} attempts."
                    )

                    print(
                        "      Board remains "
                        "uncompleted."
                    )


        if not success:

            continue


    # ========================================================
    # FINAL SAVE
    # ========================================================

    save_json(
        PINS_FILE,
        pins
    )

    save_json(
        STATE_FILE,
        state
    )


    context.close()


# ============================================================
# FINAL REPORT
# ============================================================

print()
print("=" * 70)
print("PIN METADATA BACKUP FINISHED")
print("=" * 70)
print()

print(
    f"Boards completed: "
    f"{len(completed)}/"
    f"{len(boards)}"
)

print(
    f"Unique Pins in database: "
    f"{len(pins)}"
)

print()

print(
    "Pins database:"
)

print(
    PINS_FILE.resolve()
)

print()

print(
    "Media was NOT downloaded during "
    "this run."
)

print(
    "That is intentional."
)

print()