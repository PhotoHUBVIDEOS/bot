import os
import time
import base64
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote
import requests
import telebot
from telebot import types

BOT_TOKEN = os.getenv("BOT_TOKEN", "8742752757:AAHidJDg_Su2lMRvXv0KtiiyCO-bkadE-i4").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "AutoVideosPHOTOHUBBot").strip()
PRIVATE_GROUP_ID = int(os.getenv("PRIVATE_GROUP_ID", "-5325520328"))
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "https://photohub-1f63f-default-rtdb.firebaseio.com").rstrip("/")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "351222c8862e4aaa2146afbffe30a585").strip()

# Admin Telegram User IDs
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8587134530,8413083837").split(",")
    if x.strip().isdigit()
}
ANONYMOUS_ADMIN_IDS = {1087968824}

# Public Group Notification Settings
PUBLIC_GROUP_ID = int(os.getenv("PUBLIC_GROUP_ID", "-1003951414350"))
SITE_URL = os.getenv("SITE_URL", "https://photohubvideos.github.io/newsite/").strip()
WATCH_BUTTON_TEXT = os.getenv("WATCH_BUTTON_TEXT", "🔓 Unlock & Watch").strip()
VAULT_BUTTON_TEXT = os.getenv("VAULT_BUTTON_TEXT", "🌐 PhotoHub Vault").strip()

bot = telebot.TeleBot(BOT_TOKEN)
notified_keys = set()
notified_lock = threading.Lock()
watcher_initialized = False


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Health server listening on port {port}")


def escape_html(text: str = "") -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def safe_reply(message, text: str, reply_markup=None):
    try:
        return bot.reply_to(message, text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        print("Safe reply warning:", e)
        try:
            return bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as e2:
            print("Fallback send_message warning:", e2)
            return None


def get_clean_site_base() -> str:
    base = (SITE_URL or "").strip()
    if not base.startswith(("http://", "https://")):
        return ""
    clean_base = base.split("#")[0]
    if not clean_base.endswith("/"):
        clean_base += "/"
    return clean_base


def build_post_watch_url(post_key: str | None = None) -> str:
    clean_base = get_clean_site_base()
    if not clean_base:
        return ""
    if post_key:
        return f"{clean_base}#watch={quote(str(post_key), safe='')}"
    return f"{clean_base}#home"


def build_post_buttons_markup(post_key: str | None = None) -> types.InlineKeyboardMarkup | None:
    watch_url = build_post_watch_url(post_key)
    clean_base = get_clean_site_base()
    home_url = f"{clean_base}#home" if clean_base else ""

    rows = []
    if watch_url.startswith(("http://", "https://")):
        rows.append([types.InlineKeyboardButton(text=WATCH_BUTTON_TEXT, url=watch_url)])
    if home_url.startswith(("http://", "https://")):
        rows.append([types.InlineKeyboardButton(text=VAULT_BUTTON_TEXT, url=home_url)])

    if not rows:
        return None

    markup = types.InlineKeyboardMarkup()
    for r in rows:
        markup.row(*r)
    return markup


def upload_bytes_to_imgbb(image_bytes: bytes, title: str = "thumbnail") -> str:
    try:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            data={
                "key": IMGBB_API_KEY,
                "image": b64_image,
                "name": title,
            },
            timeout=30,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("success"):
            return data["data"].get("display_url") or data["data"].get("url") or ""
    except Exception as e:
        print("ImgBB upload error:", e)
    return ""


def get_telegram_file_bytes(file_id: str) -> bytes | None:
    try:
        file_info = bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        r = requests.get(file_url, timeout=30)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        print("Telegram file download error:", e)
    return None


def push_video_to_firebase(payload: dict) -> str | None:
    try:
        r = requests.post(f"{FIREBASE_DB_URL}/videos.json", json=payload, timeout=15)
        if r.status_code == 200:
            return r.json().get("name")
    except Exception as e:
        print("Firebase POST error:", e)
    return None


def fetch_all_firebase_videos() -> list[tuple[str, dict]]:
    r = requests.get(f"{FIREBASE_DB_URL}/videos.json", timeout=20)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        return []
    items = [(str(k), v) for k, v in data.items() if isinstance(v, dict)]
    items.sort(key=lambda pair: int(pair[1].get("created_at") or 0))
    return items


def build_group_message_link(chat_id: int, message_id: int) -> str:
    s = str(chat_id)
    if s.startswith("-100"):
        internal_id = s[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    return f"https://t.me/{BOT_USERNAME}"


def build_notification_caption(item: dict, post_key: str | None = None, index_label: str = "") -> str:
    title = item.get("title") or "Untitled Video"
    idx_line = f"🗂 <b>Post No:</b> <code>{escape_html(index_label)}</code>\n" if index_label else ""
    return (
        "✨🎬 <b>NEW VIDEO POST!</b> 🎬✨\n"
        "අලුත් Video එකක් Site එකට ආවා!\n"
        "======================\n"
        f"🎥 <b>Title:</b> <code>{escape_html(title)}</code>\n"
        f"{idx_line}"
        "🔐 <b>Access:</b> Unlock කරලා බලන්න\n"
        "⚡ <b>Delivery:</b> Instant Telegram Bot\n"
        "======================\n"
        "👇 පහළ Buttons Click කරලා Video එක හෝ Site එක බලන්න!"
    )


def send_post_to_public_group(item: dict, post_key: str | None = None, index_label: str = "", require_thumb: bool = True) -> bool:
    if not PUBLIC_GROUP_ID:
        return False

    thumb_url = (item.get("thumb_url") or "").strip()
    has_valid_thumb = thumb_url.startswith(("http://", "https://"))

    if require_thumb and not has_valid_thumb:
        print(f"[SKIP NOTIFY] No valid thumb_url for '{item.get('title')}'")
        return False

    title = item.get("title") or "Untitled Video"
    watch_url = build_post_watch_url(post_key)
    caption = build_notification_caption(item, post_key=post_key, index_label=index_label)
    reply_markup = build_post_buttons_markup(post_key)

    if has_valid_thumb:
        try:
            img_resp = requests.get(thumb_url, timeout=20)
            if img_resp.status_code == 200 and img_resp.content:
                bot.send_photo(
                    PUBLIC_GROUP_ID,
                    img_resp.content,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                print(f"[PUBLIC PHOTO SENT] {title} -> {watch_url}")
                return True
        except Exception as e:
            print("Photo bytes download/send warning, trying URL:", e)

        try:
            bot.send_photo(
                PUBLIC_GROUP_ID,
                thumb_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            print(f"[PUBLIC PHOTO URL SENT] {title} -> {watch_url}")
            return True
        except Exception as e:
            print("Public group send_photo error:", e)

    try:
        bot.send_message(
            PUBLIC_GROUP_ID,
            caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=False,
        )
        print(f"[PUBLIC MSG SENT] {title} -> {watch_url}")
        return True
    except Exception as e:
        print("Public group send_message error:", e)
        return False


def watch_firebase_new_posts():
    global watcher_initialized
    while True:
        try:
            r = requests.get(f"{FIREBASE_DB_URL}/videos.json", timeout=15)
            if r.status_code == 200:
                data = r.json()
                current_items = data if isinstance(data, dict) else {}

                with notified_lock:
                    if not watcher_initialized:
                        notified_keys.update(current_items.keys())
                        watcher_initialized = True
                        new_entries = []
                    else:
                        new_entries = []
                        for key, val in current_items.items():
                            if key not in notified_keys and isinstance(val, dict):
                                notified_keys.add(key)
                                new_entries.append((val.get("created_at", 0), key, val))

                new_entries.sort(key=lambda x: x[0])
                for _, key, item in new_entries:
                    print(f"[NEW SITE POST DETECTED] {key} -> {item.get('title')}")
                    send_post_to_public_group(item, post_key=key, require_thumb=True)
        except Exception as e:
            print("Firebase watcher error:", e)

        time.sleep(5)


@bot.message_handler(commands=["adminallpost"])
def handle_admin_all_post(message):
    sender = getattr(message, "from_user", None)
    sender_id = getattr(sender, "id", None)
    print(f"[/adminallpost] sender_id={sender_id}, chat_id={message.chat.id}")

    is_admin = (sender_id in ADMIN_IDS) or (sender_id in ANONYMOUS_ADMIN_IDS)
    if not is_admin:
        safe_reply(
            message,
            f"⛔ මෙම command එක භාවිතා කළ හැක්කේ admin කෙනෙකුට පමණි.\n(Your ID: <code>{escape_html(str(sender_id))}</code>)",
        )
        return

    status_msg = safe_reply(message, "⏳ Firebase වලින් සියලුම posts ලබා ගනිමින් පවතී...")

    try:
        posts = fetch_all_firebase_videos()
    except Exception as e:
        print("Admin all post fetch error:", e)
        if status_msg:
            try:
                bot.edit_message_text("❌ Firebase වලින් posts ලබා ගැනීමට නොහැකි වුණා.", status_msg.chat.id, status_msg.message_id)
            except Exception:
                pass
        return

    if not posts:
        if status_msg:
            try:
                bot.edit_message_text("ℹ️ Posts කිසිවක් හමු වුණේ නැහැ.", status_msg.chat.id, status_msg.message_id)
            except Exception:
                pass
        return

    total = len(posts)
    sent_count = 0

    if status_msg:
        try:
            bot.edit_message_text(f"📤 Posts {total}ක් public group එකට යවමින් පවතී...", status_msg.chat.id, status_msg.message_id)
        except Exception:
            pass

    for idx, (post_key, item) in enumerate(posts, start=1):
        ok = send_post_to_public_group(
            item,
            post_key=post_key,
            index_label=f"{idx} / {total}",
            require_thumb=False,
        )
        if ok:
            sent_count += 1
        time.sleep(0.4)

    if status_msg:
        try:
            bot.edit_message_text(
                f"✅ සම්පූර්ණයි! Public group එකට posts {sent_count}/{total}ක් යැවුණා.",
                status_msg.chat.id,
                status_msg.message_id,
            )
        except Exception:
            pass


def send_single_video_to_chat(chat_id: int, post_key: str, item: dict, index_label: str = "") -> bool:
    title = item.get("title") or "Untitled Video"
    file_id = (item.get("telegram_file_id") or "").strip()
    thumb_url = (item.get("thumb_url") or "").strip()
    idx_line = f"🗂 <b>No:</b> <code>{escape_html(index_label)}</code>\n" if index_label else ""

    caption = (
        f"🎬 <b>{escape_html(title)}</b>\n"
        f"{idx_line}"
        f"🆔 <code>{escape_html(post_key)}</code>"
    )
    reply_markup = build_post_buttons_markup(post_key)

    if file_id:
        try:
            bot.send_video(
                chat_id,
                file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            print(f"[SAKULA send_video warning] {post_key}: {e}")
            try:
                bot.send_document(
                    chat_id,
                    file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                return True
            except Exception as e2:
                print(f"[SAKULA send_document warning] {post_key}: {e2}")

    if thumb_url.startswith(("http://", "https://")):
        try:
            bot.send_photo(
                chat_id,
                thumb_url,
                caption=f"{caption}\n⚠️ <i>Direct video file_id නොමැත (Preview).</i>",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return True
        except Exception as e3:
            print(f"[SAKULA send_photo warning] {post_key}: {e3}")

    try:
        bot.send_message(
            chat_id,
            f"{caption}\n⚠️ <i>Video file හමු නොවුණා.</i>",
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return True
    except Exception as e4:
        print(f"[SAKULA send_message error] {post_key}: {e4}")
        return False


@bot.message_handler(commands=["adminsakula"])
def handle_admin_sakula(message):
    sender = getattr(message, "from_user", None)
    sender_id = getattr(sender, "id", None)
    print(f"[/adminsakula] sender_id={sender_id}, chat_id={message.chat.id}")

    status_msg = safe_reply(message, "⏳ Firebase වලින් videos ලබා ගනිමින් පවතී...")

    try:
        posts = fetch_all_firebase_videos()
    except Exception as e:
        print("Admin sakula fetch error:", e)
        if status_msg:
            try:
                bot.edit_message_text(
                    "❌ Firebase වලින් videos ලබා ගැනීමට නොහැකි වුණා.",
                    status_msg.chat.id,
                    status_msg.message_id,
                )
            except Exception:
                pass
        return

    video_posts = [
        (k, v) for k, v in posts
        if (v.get("telegram_file_id") or "").strip()
    ]

    if not video_posts:
        if status_msg:
            try:
                bot.edit_message_text(
                    "ℹ️ Telegram file_id සහිත videos කිසිවක් හමු වුණේ නැහැ.",
                    status_msg.chat.id,
                    status_msg.message_id,
                )
            except Exception:
                pass
        return

    total = len(video_posts)
    sent_count = 0

    if status_msg:
        try:
            bot.edit_message_text(
                f"📤 Videos {total}ක් මෙම chat එකට යවමින් පවතී...",
                status_msg.chat.id,
                status_msg.message_id,
            )
        except Exception:
            pass

    for idx, (post_key, item) in enumerate(video_posts, start=1):
        ok = send_single_video_to_chat(
            message.chat.id,
            post_key=post_key,
            item=item,
            index_label=f"{idx} / {total}",
        )
        if ok:
            sent_count += 1
        time.sleep(0.8)

    if status_msg:
        try:
            bot.edit_message_text(
                f"✅ සම්පූර්ණයි! Videos {sent_count}/{total}ක් යැවුණා.",
                status_msg.chat.id,
                status_msg.message_id,
            )
        except Exception:
            pass


@bot.message_handler(content_types=["video", "document"])
def handle_group_video(message):
    print(f"[VIDEO] Received {message.content_type} in chat_id={message.chat.id}")

    if message.chat.id != PRIVATE_GROUP_ID:
        print(f"[SKIP] chat_id={message.chat.id} != {PRIVATE_GROUP_ID}")
        return

    video = message.video or message.document
    if not video:
        return

    if message.content_type == "document":
        mime = getattr(video, "mime_type", "") or ""
        if not mime.startswith("video/"):
            print(f"[SKIP] Not a video document (mime={mime})")
            return

    default_title = getattr(video, "file_name", None) or f"Video_{video.file_unique_id}"
    title = (message.caption or default_title).strip()

    thumb_url = ""
    thumb_obj = getattr(video, "thumbnail", None) or getattr(video, "thumb", None)
    if thumb_obj:
        thumb_bytes = get_telegram_file_bytes(thumb_obj.file_id)
        if thumb_bytes:
            thumb_url = upload_bytes_to_imgbb(thumb_bytes, title=video.file_unique_id)

    group_msg_link = build_group_message_link(message.chat.id, message.message_id)
    bot_deep_link = f"https://t.me/{BOT_USERNAME}?start=vid_{video.file_unique_id}"

    payload = {
        "title": title,
        "thumb_url": thumb_url,
        "telegram_file_id": video.file_id,
        "file_unique_id": video.file_unique_id,
        "message_id": message.message_id,
        "group_id": message.chat.id,
        "group_link": group_msg_link,
        "bot_link": bot_deep_link,
        "duration": getattr(video, "duration", 0),
        "views": 0,
        "unlocked": 0,
        "created_at": int(time.time()),
    }

    fb_id = push_video_to_firebase(payload)
    if fb_id:
        with notified_lock:
            notified_keys.add(fb_id)
        print(f"[OK] Saved to Firebase: {fb_id} | Title: {title}")
        send_post_to_public_group(payload, post_key=fb_id, require_thumb=True)
        safe_reply(
            message,
            f"Uploaded to Site!\nTitle: {escape_html(title)}\nID: <code>{escape_html(fb_id)}</code>",
            reply_markup=build_post_buttons_markup(fb_id),
        )
    else:
        print("[ERROR] Failed to push to Firebase.")
        safe_reply(message, "Failed to save to site database.")


@bot.message_handler(commands=["start"])
def handle_start(message):
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("vid_"):
        unique_id = args[1][4:]
        try:
            r = requests.get(f"{FIREBASE_DB_URL}/videos.json", timeout=15)
            if r.status_code == 200 and isinstance(r.json(), dict):
                for _, item in r.json().items():
                    if isinstance(item, dict) and item.get("file_unique_id") == unique_id:
                        file_id = item.get("telegram_file_id")
                        title = item.get("title", "Video")
                        if file_id:
                            try:
                                bot.send_video(message.chat.id, file_id, caption=title)
                                return
                            except Exception as send_err:
                                print("send_video warning:", send_err)
                                safe_reply(message, "Unable to send video right now. Please try again.")
                                return
        except Exception as e:
            print("Start video lookup error:", e)
        safe_reply(message, "Video not found.")
    else:
        safe_reply(message, f"Welcome to @{BOT_USERNAME}!")


if __name__ == "__main__":
    print(f"@{BOT_USERNAME} bot running with live site: {SITE_URL}")
    start_health_server()
    watcher_thread = threading.Thread(target=watch_firebase_new_posts, daemon=True)
    watcher_thread.start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20, skip_pending=True)
