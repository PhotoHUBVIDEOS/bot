def send_single_video_to_chat(chat_id: int, post_key: str, item: dict, index_label: str = "") -> bool:
    """Firebase post එකේ video එක (හෝ fallback ලෙස photo/text) අදාළ chat_id එකට යවයි."""
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

    is_admin = (sender_id in ADMIN_IDS) or (sender_id in ANONYMOUS_ADMIN_IDS)
    if not is_admin:
        safe_reply(
            message,
            f"⛔ මෙම command එක භාවිතා කළ හැක්කේ admin කෙනෙකුට පමණි.\n(Your ID: <code>{escape_html(str(sender_id))}</code>)",
        )
        return

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
        time.sleep(0.6)

    if status_msg:
        try:
            bot.edit_message_text(
                f"✅ සම්පූර්ණයි! Videos {sent_count}/{total}ක් යැවුණා.",
                status_msg.chat.id,
                status_msg.message_id,
            )
        except Exception:
            pass
```[cite: 3]

### වැදගත් කරුණු කිහිපයක්
* **Rate Limit ආරක්ෂාව:** Videos ගොඩක් තිබුණොත් Telegram flood wait වළක්වා ගන්න `time.sleep(0.6)` දාලා තියෙනවා; videos 50-100+ වගේ ගොඩක් තිබුණොත් ඒක `1.0` දක්වා වැඩි කරන එක safe[cite: 3].
* **Studio එකෙන් Manual Add කළ Posts:** Studio form එකෙන් `telegram_file_id` නැතුව add කළ posts `video_posts` filter එකෙන් skip වෙනවා — ඔබට Studio posts වල `bot_link` / thumbnail පවා එවන්න ඕනේ නම් `video_posts = posts` කරන්න පුළුවන්[cite: 1, 3].

ඔබට `/adminsakula` ගැහුවාම **videos එක්ක captions/buttons එන්න ඕනේද**, නැත්නම් **plain videos විතරක්** එන්න ඕනේද?
