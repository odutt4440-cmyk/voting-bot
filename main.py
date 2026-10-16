# pip install kurigram motor pymongo python-dotenv
import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv

from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _BaseIBtn, CallbackQuery, Message
from pyrogram.enums import ButtonStyle, ChatMemberStatus
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# ==================== COLORED BUTTON WRAPPER (Kurigram ButtonStyle) ====================
def InlineKeyboardButton(text, style=None, **kwargs):
    """Colored-button wrapper (kurigram ButtonStyle). Auto-picks color from label text."""
    if style is None:
        danger = ("❌", "🚫", "🗑", "delete", "terminate", "reject", "ban", "close",
                  "remove", "cancel", "stop", "finish", "deduct", "−")
        success = ("✅", "🟢", "✨", "approve", "confirm", "verify", "add", "claim",
                   "join", "check", "+", "vote")
        t = text.lower()
        style = ButtonStyle.DANGER if any(k in t for k in danger) else (
            ButtonStyle.SUCCESS if any(k in t for k in success) else ButtonStyle.PRIMARY)
    return _BaseIBtn(text, style=style, **kwargs)

# ==================== DATABASE ====================
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["giveaway_bot"]
giveaways = db["giveaways"]
entries = db["entries"]
votes = db["votes"]
sudo_col = db["sudo_users"]
users_col = db["users"]
settings_col = db["settings"]

app = Client("GiveawayBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_states = {}
temp_data = {}

# ==================== SUDO / BAN / MAINTENANCE ====================
SUDO_USERS = set()
BANNED_USERS = {}
MAINTENANCE = {"active": False,
               "reason": "Bot is under maintenance. Please try again later."}

# ==================== DATABASE ====================
db = giveaways = entries = votes = None
sudo_col = users_col = settings_col = None

async def init_db():
    global db, giveaways, entries, votes, sudo_col, users_col, settings_col
    mongo = AsyncIOMotorClient(MONGO_URI)
    db = mongo["giveaway_bot"]
    giveaways = db["giveaways"]
    entries = db["entries"]
    votes = db["votes"]
    sudo_col = db["sudo_users"]
    users_col = db["users"]
    settings_col = db["settings"]

    global SUDO_USERS, MAINTENANCE
    await sudo_col.update_one({"user_id": OWNER_ID}, {"$set": {"user_id": OWNER_ID}}, upsert=True)
    docs = await sudo_col.find().to_list(1000)
    SUDO_USERS = {d["user_id"] for d in docs}
    SUDO_USERS.add(OWNER_ID)
    for u in await users_col.find({"is_banned": True}).to_list(10000):
        BANNED_USERS[u["user_id"]] = u.get("ban_reason", "")
    m = await settings_col.find_one({"key": "maintenance"})
    if m:
        MAINTENANCE = {"active": m.get("is_active", False),
                       "reason": m.get("reason", MAINTENANCE["reason"])}

def is_sudo(uid): return uid in SUDO_USERS

async def ban_user(uid, reason="No reason provided"):
    BANNED_USERS[uid] = reason
    await users_col.update_one({"user_id": uid},
                               {"$set": {"is_banned": True, "ban_reason": reason}}, upsert=True)

async def unban_user(uid):
    BANNED_USERS.pop(uid, None)
    await users_col.update_one({"user_id": uid},
                               {"$set": {"is_banned": False, "ban_reason": ""}}, upsert=True)

def banned_msg(reason):
    return (f"🚫 **You are banned from using this bot.**\n\n"
            f"📝 **Reason:** {reason}\n\n"
            f"ℹ️ _Contact the bot owner if you believe this is a mistake._")

async def gate(user_id: int):
    """Return a block message if banned or maintenance, else None."""
    if user_id == OWNER_ID:
        return None
    if user_id in BANNED_USERS:
        return banned_msg(BANNED_USERS[user_id])
    if MAINTENANCE["active"] and not is_sudo(user_id):
        return (f"🚧 **SYSTEM MAINTENANCE MODE ACTIVE** 🚧\n\n"
                f"📝 **Message:** {MAINTENANCE['reason']}\n\n"
                f"ℹ️ _All bot operations are temporarily paused. Please try again later._")
    return None

# ==================== HELPERS ====================
async def is_member(user_id: int, channel_id: int) -> bool:
    try:
        m = await app.get_chat_member(channel_id, user_id)
        return m.status in (ChatMemberStatus.MEMBER,
                            ChatMemberStatus.ADMINISTRATOR,
                            ChatMemberStatus.OWNER)
    except Exception:
        return False

def vote_button(v: int, gid: str, uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🗳 VOTE — {v}", callback_data=f"gvote:{gid}:{uid}",
                             style=ButtonStyle.SUCCESS)
    ]])

async def refresh_vote_button(gid: str, uid: int):
    e = await entries.find_one({"giveaway_id": gid, "user_id": uid})
    if e and e.get("message_id"):
        try:
            await app.edit_message_reply_markup(
                e["chat_id"], e["message_id"],
                reply_markup=vote_button(max(e["votes"], 0), gid, uid))
        except Exception:
            pass

def main_menu(user_id: int):
    rows = [
        [InlineKeyboardButton("🚀 Create Giveaway", callback_data="gw_create")],
        [InlineKeyboardButton("❓ Help", callback_data="gw_help")],
    ]
    if user_id == OWNER_ID:
        rows.append([InlineKeyboardButton("⚙️ Admin Panel (Owner)",
                                          callback_data="owner_panel")])
    return InlineKeyboardMarkup(rows)

# ==================== /START ====================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(_, m: Message):
    block = await gate(m.from_user.id)
    if block:
        return await m.reply_text(block)
    args = m.text.split(maxsplit=1)
    if len(args) == 2 and len(args[1]) == 24:
        return await handle_participant(m, args[1])
    await m.reply_text(
        "👋 **Welcome to Giveaway Bot!**\n\n"
        "• Create giveaways for your channel\n"
        "• Participants must join your channel to enter and vote\n"
        "• Real votes only — leaving the channel removes votes\n\n"
        "Choose an option below:",
        reply_markup=main_menu(m.from_user.id))

@app.on_callback_query(filters.regex("^gw_help$"))
async def help_cb(_, q: CallbackQuery):
    await q.answer(
        "📌 HOW TO USE:\n\n"
        "1️⃣ Press 'Create Giveaway'\n"
        "2️⃣ Add this bot as ADMIN in your channel (all permissions)\n"
        "3️⃣ Send your channel link here\n"
        "4️⃣ Post the participation link in your channel\n"
        "5️⃣ Every participant gets an automatic post with a colored vote button\n"
        "6️⃣ Use ⚙️ Manage panel to Add/Remove participants and set custom votes\n"
        "7️⃣ /end announces the winner, /stats shows live results",
        show_alert=True)

# ==================== CREATE GIVEAWAY ====================
@app.on_callback_query(filters.regex("^gw_create$"))
async def create_gw(_, q: CallbackQuery):
    block = await gate(q.from_user.id)
    if block:
        return await q.answer(block, show_alert=True)
    active = await giveaways.find_one(
        {"creator_id": q.from_user.id, "status": {"$in": ["waiting_channel", "active"]}})
    if active:
        return await q.answer("You already have an active giveaway. Use /end first.",
                              show_alert=True)
    await giveaways.insert_one({
        "creator_id": q.from_user.id, "creator_name": q.from_user.first_name,
        "channel_id": None, "channel_username": None, "status": "waiting_channel"})
    await q.message.edit_text(
        "🛠️ **GIVEAWAY SETUP**\n\n"
        "**Step 1:** Add this bot to your channel as **Admin** (all permissions).\n"
        "**Step 2:** Send your channel link here "
        "(example: `@mychannel` or `https://t.me/mychannel`).",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel Setup", callback_data="gw_cancel")]]))
    await q.answer()

@app.on_callback_query(filters.regex("^gw_cancel$"))
async def cancel_gw(_, q: CallbackQuery):
    res = await giveaways.delete_one(
        {"creator_id": q.from_user.id, "status": "waiting_channel"})
    if res.deleted_count:
        await q.message.edit_text("❌ **Setup cancelled.** You can start again anytime.")
        await q.answer("Cancelled ✅")
    else:
        await q.answer("Nothing to cancel.", show_alert=True)

@app.on_message(filters.private & filters.regex(r"^(?:@|https?://t\.me/)\w{4,}$"))
async def set_channel(_, m: Message):
    g = await giveaways.find_one({"creator_id": m.from_user.id,
                                  "status": "waiting_channel"})
    if not g:
        return
    handle = m.text.split("/")[-1].replace("@", "")
    try:
        chat = await app.get_chat(f"@{handle}")
    except Exception:
        return await m.reply_text(
            "❌ **Channel not found.**\n"
            "Make sure the bot is added as **ADMIN** in the channel first, "
            "then send the link again.")
    gid = str(g["_id"])
    link = f"https://t.me/{(await app.get_me()).username}?start={gid}"
    await giveaways.update_one(
        {"_id": g["_id"]},
        {"$set": {"channel_id": chat.id, "channel_username": chat.username,
                  "status": "active"}})
    await m.reply_text(
        f"🎉 **GIVEAWAY IS LIVE!**\n\n"
        f"📢 **Channel:** @{chat.username}\n\n"
        f"🔗 **Participation link** (post this in your channel):\n\n"
        f"`{link}`\n\n"
        f"⚙️ /manage — control panel\n"
        f"📊 /stats — live results\n"
        f"🏁 /end — finish and announce the winner")

# ==================== PARTICIPANT FLOW ====================
async def create_entry(g: dict, uid: int, name: str, gid: str):
    """Insert entry + post vote box in the channel. Returns True on success."""
    await entries.insert_one({"giveaway_id": gid, "user_id": uid,
                              "name": name, "votes": 0})
    try:
        post = await app.send_message(
            g["channel_id"], f"**{name}**",
            reply_markup=vote_button(0, gid, uid))
        await entries.update_one(
            {"giveaway_id": gid, "user_id": uid},
            {"$set": {"message_id": post.id, "chat_id": g["channel_id"]}})
        return True
    except Exception:
        await entries.delete_one({"giveaway_id": gid, "user_id": uid})
        return False

async def handle_participant(m, gid: str):
    try:
        g = await giveaways.find_one({"_id": ObjectId(gid)})
    except Exception:
        return await m.reply_text("❌ **Invalid giveaway link.**")
    if not g or g["status"] != "active":
        return await m.reply_text("❌ **This giveaway has ended.**")
    uid = m.from_user.id

    if not await is_member(uid, g["channel_id"]):
        return await m.reply_text(
            "🚫 **You must join the channel to participate!**\n\n"
            "After joining, press the button below:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📢 Join @{g['channel_username']}",
                                      url=f"https://t.me/{g['channel_username']}")],
                [InlineKeyboardButton("✅ I Have Joined", callback_data=f"gcheck:{gid}")]]))

    existing = await entries.find_one({"giveaway_id": gid, "user_id": uid})
    if existing:
        return await m.reply_text(
            f"✅ **You are already participating!**\n🗳️ **Your votes:** {existing['votes']}")

    name = m.from_user.first_name or "Participant"
    if not await create_entry(g, uid, name, gid):
        return await m.reply_text(
            "⚠️ **Bot cannot post in the channel.** "
            "Ask the channel owner to make the bot an Admin.")
    await m.reply_text("🎉 **You are participating!** Your vote box is live in the channel. 🍀")

@app.on_callback_query(filters.regex(r"^gcheck:(.+)$"))
async def check_join(_, q: CallbackQuery):
    gid = q.matches.group(1)
    g = await giveaways.find_one({"_id": ObjectId(gid)})
    if not g or not await is_member(q.from_user.id, g["channel_id"]):
        return await q.answer("❌ You have not joined the channel yet!", show_alert=True)
    await q.answer("✅ Verified!")
    uid = q.from_user.id
    if await entries.find_one({"giveaway_id": gid, "user_id": uid}):
        return
    name = q.from_user.first_name or "Participant"
    if await create_entry(g, uid, name, gid):
        await app.send_message(uid, "🎉 **You are participating!** Good luck! 🍀")

# ==================== PUBLIC VOTING ====================
@app.on_callback_query(filters.regex(r"^gvote:(.+):(\d+)$"))
async def vote_cb(_, q: CallbackQuery):
    gid, entry_uid = q.matches.group(1), int(q.matches.group(2))
    g = await giveaways.find_one({"_id": ObjectId(gid)})
    if not g or g["status"] != "active":
        return await q.answer("🛑 This giveaway has ended.", show_alert=True)
    if not await is_member(q.from_user.id, g["channel_id"]):
        return await q.answer("🚫 Join the channel first to vote!", show_alert=True)
    if entry_uid == q.from_user.id:
        return await q.answer("😅 You cannot vote for yourself!", show_alert=True)
    if await votes.find_one({"giveaway_id": gid, "voter_id": q.from_user.id}):
        return await q.answer("👍 You already used your vote in this giveaway!",
                              show_alert=True)
    await votes.insert_one({"giveaway_id": gid, "entry_user": entry_uid,
                            "voter_id": q.from_user.id})
    res = await entries.find_one_and_update(
        {"giveaway_id": gid, "user_id": entry_uid},
        {"$inc": {"votes": 1}})
    if res:
        await refresh_vote_button(gid, entry_uid)
        await q.answer(f"✅ Vote counted! {res['name']} now has {res['votes'] + 1} votes.")

# ==================== CREATOR CONTROL PANEL ====================
async def get_active_gw(user_id: int):
    if user_id == OWNER_ID:
        return await giveaways.find_one({"status": "active"})
    return await giveaways.find_one({"creator_id": user_id, "status": "active"})

async def open_panel(chat_id: int, gid: str, msg_id: int = None):
    rows = await entries.find({"giveaway_id": gid}).sort("votes", -1).to_list(40)
    kb = [[InlineKeyboardButton(
        f"👤 {r['name']} — {r['votes']} votes",
        callback_data=f"gp:{gid}:{r['user_id']}")] for r in rows]
    kb.append([
        InlineKeyboardButton("➕ Add Participant", callback_data=f"gaddp:{gid}"),
        InlineKeyboardButton("➖ Remove Participant", callback_data=f"grmp:{gid}")])
    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"gpanel:{gid}")])
    kb.append([InlineKeyboardButton("❌ Close Panel", callback_data="gclose")])
    text = (f"⚙️ **CREATOR CONTROL PANEL**\n\n"
            f"👥 **Total participants:** {len(rows)}\n\n"
            "Select a participant to manage votes, "
            "or use the buttons below:")
    markup = InlineKeyboardMarkup(kb)
    if msg_id:
        try:
            await app.edit_message_text(chat_id, msg_id, text, reply_markup=markup)
            return
        except Exception:
            pass
    await app.send_message(chat_id, text, reply_markup=markup)

@app.on_message(filters.command("manage") & filters.private)
async def manage_cmd(_, m: Message):
    block = await gate(m.from_user.id)
    if block:
        return await m.reply_text(block)
    g = await get_active_gw(m.from_user.id)
    if not g:
        return await m.reply_text("⛔ **No active giveaway found for you.**")
    await open_panel(m.chat.id, str(g["_id"]))

@app.on_callback_query(filters.regex(r"^gpanel:(.+)$"))
async def panel_refresh(_, q: CallbackQuery):
    await open_panel(q.message.chat.id, q.matches.group(1), q.message.id)
    await q.answer()

@app.on_callback_query(filters.regex("^gclose$"))
async def panel_close(_, q: CallbackQuery):
    await q.message.delete()
    await q.answer()

def participant_kb(gid: str, uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ +1 Vote", callback_data=f"gadj:{gid}:{uid}:1")],
        [InlineKeyboardButton("➕ +5 Votes", callback_data=f"gadj:{gid}:{uid}:5")],
        [InlineKeyboardButton("➕ +10 Votes", callback_data=f"gadj:{gid}:{uid}:10")],
        [InlineKeyboardButton("➖ −1 Vote", callback_data=f"gadj:{gid}:{uid}:-1")],
        [InlineKeyboardButton("➖ −5 Votes", callback_data=f"gadj:{gid}:{uid}:-5")],
        [InlineKeyboardButton("🔢 Set Custom Votes", callback_data=f"gcust:{gid}:{uid}")],
        [InlineKeyboardButton("🗑 Remove This Participant", callback_data=f"grm1:{gid}:{uid}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"gpanel:{gid}")]])

@app.on_callback_query(filters.regex(r"^gp:(.+):(\d+)$"))
async def panel_user(_, q: CallbackQuery):
    gid, uid = q.matches.group(1), int(q.matches.group(2))
    e = await entries.find_one({"giveaway_id": gid, "user_id": uid})
    if not e:
        return await q.answer("Participant not found.", show_alert=True)
    await q.message.edit_text(
        f"👤 **{e['name']}**\n🗳️ **Current votes:** {e['votes']}\n\nSelect an action:",
        reply_markup=participant_kb(gid, uid))
    await q.answer()

async def authorize(user_id: int, gid: str) -> bool:
    g = await giveaways.find_one({"_id": ObjectId(gid)})
    return bool(g) and (user_id == g["creator_id"] or user_id == OWNER_ID)

@app.on_callback_query(filters.regex(r"^gadj:(.+):(\d+):(-?\d+)$"))
async def adjust_votes(_, q: CallbackQuery):
    gid, uid, delta = q.matches.group(1), int(q.matches.group(2)), int(q.matches.group(3))
    if not await authorize(q.from_user.id, gid):
        return await q.answer("⛔ Only the giveaway creator or bot owner can do this!",
                              show_alert=True)
    e = await entries.find_one({"giveaway_id": gid, "user_id": uid})
    if not e:
        return await q.answer("Participant not found.", show_alert=True)
    new_v = max(e["votes"] + delta, 0)
    await entries.update_one({"giveaway_id": gid, "user_id": uid},
                             {"$set": {"votes": new_v}})
    await refresh_vote_button(gid, uid)
    await q.answer(f"✅ Done! {e['name']} now has {new_v} votes.", show_alert=True)
    try:
        await q.message.edit_text(
            f"👤 **{e['name']}**\n🗳️ **Current votes:** {new_v}\n\nSelect an action:",
            reply_markup=participant_kb(gid, uid))
    except Exception:
        pass

@app.on_callback_query(filters.regex(r"^gcust:(.+):(\d+)$"))
async def custom_votes(_, q: CallbackQuery):
    gid, uid = q.matches.group(1), q.matches.group(2)
    if not await authorize(q.from_user.id, gid):
        return await q.answer("⛔ Not authorized!", show_alert=True)
    user_states[q.from_user.id] = "GW_CUSTOM_VOTES"
    temp_data[q.from_user.id] = {"gid": gid, "uid": int(uid)}
    await q.message.edit_text(
        "🔢 **SET CUSTOM VOTES**\n\n"
        "Send the exact number of votes for this participant\n"
        "(example: `25` or `100`):",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=f"gpanel:{gid}")]]))
    await q.answer()

@app.on_callback_query(filters.regex(r"^gaddp:(.+)$"))
async def add_participant(_, q: CallbackQuery):
    gid = q.matches.group(1)
    if not await authorize(q.from_user.id, gid):
        return await q.answer("⛔ Not authorized!", show_alert=True)
    user_states[q.from_user.id] = "GW_ADD_PARTICIPANT"
    temp_data[q.from_user.id] = {"gid": gid}
    await q.message.edit_text(
        "➕ **ADD PARTICIPANT MANUALLY**\n\n"
        "Send the user's details in this format:\n\n"
        "`UserID Name`\n\n"
        "Example: `123456789 Rahul`",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=f"gpanel:{gid}")]]))
    await q.answer()

@app.on_callback_query(filters.regex(r"^grmp:(.+)$"))
async def remove_participant_list(_, q: CallbackQuery):
    gid = q.matches.group(1)
    if not await authorize(q.from_user.id, gid):
        return await q.answer("⛔ Not authorized!", show_alert=True)
    rows = await entries.find({"giveaway_id": gid}).sort("votes", -1).to_list(40)
    if not rows:
        return await q.answer("No participants to remove.", show_alert=True)
    kb = [[InlineKeyboardButton(
        f"🗑 {r['name']} — {r['votes']} votes",
        callback_data=f"grm1:{gid}:{r['user_id']}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data=f"gpanel:{gid}")])
    await q.message.edit_text("➖ **SELECT PARTICIPANT TO REMOVE:**",
                              reply_markup=InlineKeyboardMarkup(kb))
    await q.answer()

@app.on_callback_query(filters.regex(r"^grm1:(.+):(\d+)$"))
async def remove_one_participant(_, q: CallbackQuery):
    gid, uid = q.matches.group(1), int(q.matches.group(2))
    if not await authorize(q.from_user.id, gid):
        return await q.answer("⛔ Not authorized!", show_alert=True)
    e = await entries.find_one_and_delete({"giveaway_id": gid, "user_id": uid})
    if not e:
        return await q.answer("Participant not found.", show_alert=True)
    await votes.delete_many({"giveaway_id": gid, "entry_user": uid})
    if e.get("message_id"):
        try:
            await app.delete_messages(e["chat_id"], e["message_id"])
        except Exception:
            pass
    await q.answer(f"🗑 Removed {e['name']} and deleted their post.", show_alert=True)
    await open_panel(q.message.chat.id, gid, q.message.id)

# ==================== TEXT INPUT ROUTER (single handler — no conflicts) ====================
@app.on_message(filters.private & filters.text &
                ~filters.command(["start", "manage", "stats", "end", "panel",
                                  "gban", "gunban"]))
async def text_router(_, m: Message):
    state = user_states.get(m.from_user.id)
    if not state:
        return

    if state == "GW_CUSTOM_VOTES":
        d = temp_data.pop(m.from_user.id, {})
        user_states.pop(m.from_user.id, None)
        gid, uid = d.get("gid"), d.get("uid")
        try:
            val = int(m.text.strip())
            if val < 0:
                raise ValueError
        except ValueError:
            return await m.reply_text("❌ **Send a valid positive number only!**")
        if not await authorize(m.from_user.id, gid):
            return await m.reply_text("⛔ **Not authorized.**")
        e = await entries.find_one_and_update(
            {"giveaway_id": gid, "user_id": uid},
            {"$set": {"votes": val}})
        if e:
            await refresh_vote_button(gid, uid)
            await m.reply_text(f"✅ **Votes set to {val} for {e['name']}!**")
        else:
            await m.reply_text("❌ Participant not found.")

    elif state == "GW_ADD_PARTICIPANT":
        d = temp_data.pop(m.from_user.id, {})
        user_states.pop(m.from_user.id, None)
        gid = d.get("gid")
        parts = m.text.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[0].lstrip("-").isdigit():
            return await m.reply_text("❌ **Wrong format!** Use: `UserID Name`")
        uid, name = int(parts[0]), parts[1]
        if not await authorize(m.from_user.id, gid):
            return await m.reply_text("⛔ **Not authorized.**")
        if await entries.find_one({"giveaway_id": gid, "user_id": uid}):
            return await m.reply_text("⚠️ **This participant already exists.**")
        g = await giveaways.find_one({"_id": ObjectId(gid)})
        if await create_entry(g, uid, name, gid):
            await m.reply_text(
                f"✅ **Added participant {name} with a vote box in the channel!**")
        else:
            await m.reply_text("⚠️ **Bot cannot post in the channel.** "
                               "Make sure the bot is an Admin there.")

    elif state == "OWN_ADD_SUDO":
        user_states.pop(m.from_user.id, None)
        try:
            target = int(m.text.strip())
        except ValueError:
            return await m.reply_text("❌ **Send a numeric User ID only!**")
        if target == OWNER_ID:
            return await m.reply_text("⚠️ **The owner already has full powers.**")
        SUDO_USERS.add(target)
        await sudo_col.update_one({"user_id": target},
                                  {"$set": {"user_id": target}}, upsert=True)
        await m.reply_text(f"✅ **Admin added!**\n👤 **User ID:** `{target}`",
                           reply_markup=owner_panel_kb())

    elif state == "OWN_BAN_ID":
        try:
            target = int(m.text.strip())
        except ValueError:
            return await m.reply_text("❌ **Send a numeric User ID only!**")
        if target == OWNER_ID:
            user_states.pop(m.from_user.id, None)
            return await m.reply_text("⚠️ **You cannot ban yourself (owner)!**",
                                      reply_markup=owner_panel_kb())
        user_states[m.from_user.id] = "OWN_BAN_REASON"
        temp_data[m.from_user.id] = {"ban_target": target}
        await m.reply_text(
            f"👤 **Target User ID:** `{target}`\n\n📝 **Send the ban reason:**",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="owner_panel")]]))

    elif state == "OWN_UNBAN_ID":
        user_states.pop(m.from_user.id, None)
        try:
            target = int(m.text.strip())
        except ValueError:
            return await m.reply_text("❌ **Send a numeric User ID only!**")
        await unban_user(target)
        await m.reply_text(f"🟢 **User `{target}` unbanned globally!**",
                           reply_markup=owner_panel_kb())

    elif state == "OWN_BAN_REASON":
        target = temp_data.pop(m.from_user.id, {}).get("ban_target")
        user_states.pop(m.from_user.id, None)
        if target is None:
            return await m.reply_text("❌ **Session expired.**")
        reason = m.text.strip() or "No reason provided"
        await ban_user(target, reason)
        await m.reply_text(
            f"🚫 **User banned globally!**\n\n👤 **User ID:** `{target}`\n"
            f"📝 **Reason:** {reason}",
            reply_markup=owner_panel_kb())

    elif state == "OWN_MAINT_REASON":
        user_states.pop(m.from_user.id, None)
        MAINTENANCE["reason"] = m.text.strip()
        await settings_col.update_one(
            {"key": "maintenance"},
            {"$set": {"is_active": MAINTENANCE["active"], "reason": MAINTENANCE["reason"]}},
            upsert=True)
        await m.reply_text("✅ **Maintenance message updated!**",
                           reply_markup=owner_panel_kb())

# ==================== OWNER ADMIN PANEL ====================
def owner_panel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Manage Sudo Admins", callback_data="own_sudo")],
        [InlineKeyboardButton("🚫 Global Ban", callback_data="own_ban"),
         InlineKeyboardButton("🟢 Global Unban", callback_data="own_unban")],
        [InlineKeyboardButton("🛠️ Maintenance Mode", callback_data="own_maint")],
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="own_stats")],
        [InlineKeyboardButton("✖️ Close Panel", callback_data="own_close")]])

def owner_panel_text():
    return (f"⚙️ **OWNER ADMIN PANEL**\n\n"
            f"👑 **Owner ID:** `{OWNER_ID}`\n"
            f"👥 **Sudo Admins:** {len(SUDO_USERS) - 1}\n"
            f"🚫 **Banned Users:** {len(BANNED_USERS)}\n"
            f"🛠️ **Maintenance:** {'🔴 ON' if MAINTENANCE['active'] else '🟢 OFF'}")

@app.on_message(filters.command("panel") & filters.private)
async def panel_cmd(_, m: Message):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("⛔ **This command is restricted to the bot owner.**")
    await m.reply_text(owner_panel_text(), reply_markup=owner_panel_kb())

@app.on_callback_query(filters.regex("^owner_panel$"))
async def owner_panel_cb(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return await q.answer("⛔ Owner only!", show_alert=True)
    await q.message.edit_text(owner_panel_text(), reply_markup=owner_panel_kb())
    await q.answer()

@app.on_callback_query(filters.regex("^own_close$"))
async def own_close(_, q: CallbackQuery):
    await q.message.delete()
    await q.answer()

@app.on_callback_query(filters.regex("^own_sudo$"))
async def own_sudo(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return await q.answer("⛔ Owner only!", show_alert=True)
    kb = [[InlineKeyboardButton("➕ Add Sudo Admin", callback_data="own_sudo_add")]]
    for uid in SUDO_USERS:
        if uid == OWNER_ID:
            continue
        kb.append([InlineKeyboardButton(f"🗑 Remove {uid}",
                                        callback_data=f"own_sudo_rm:{uid}")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
    await q.message.edit_text(
        f"👥 **MANAGE SUDO ADMINS**\n\n"
        f"Sudo admins can use the bot during maintenance and are not affected by it.\n"
        f"ℹ️ _Giveaway control is always limited to each giveaway's creator._",
        reply_markup=InlineKeyboardMarkup(kb))
    await q.answer()

@app.on_callback_query(filters.regex("^own_sudo_add$"))
async def own_sudo_add(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return
    user_states[q.from_user.id] = "OWN_ADD_SUDO"
    await q.message.edit_text(
        "➕ **ADD SUDO ADMIN**\n\nSend the **User ID** of the new admin:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="own_sudo")]]))
    await q.answer()

@app.on_callback_query(filters.regex(r"^own_sudo_rm:(\d+)$"))
async def own_sudo_rm(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return
    uid = int(q.matches.group(1))
    SUDO_USERS.discard(uid)
    await sudo_col.delete_one({"user_id": uid})
    await q.answer(f"🗑 Admin {uid} removed!", show_alert=True)
    kb = [[InlineKeyboardButton("➕ Add Sudo Admin", callback_data="own_sudo_add")]]
    for s in SUDO_USERS:
        if s == OWNER_ID:
            continue
        kb.append([InlineKeyboardButton(f"🗑 Remove {s}",
                                        callback_data=f"own_sudo_rm:{s}")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
    await q.message.edit_text("👥 **MANAGE SUDO ADMINS**",
                              reply_markup=InlineKeyboardMarkup(kb))

@app.on_callback_query(filters.regex("^own_ban$"))
async def own_ban(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return await q.answer("⛔ Owner only!", show_alert=True)
    user_states[q.from_user.id] = "OWN_BAN_ID"
    await q.message.edit_text(
        "🚫 **GLOBAL BAN**\n\nSend the **User ID** to ban from the entire bot:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="owner_panel")]]))
    await q.answer()

@app.on_callback_query(filters.regex("^own_unban$"))
async def own_unban(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return await q.answer("⛔ Owner only!", show_alert=True)
    user_states[q.from_user.id] = "OWN_UNBAN_ID"
    await q.message.edit_text(
        "🟢 **GLOBAL UNBAN**\n\nSend the **User ID** to unban:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="owner_panel")]]))
    await q.answer()

@app.on_callback_query(filters.regex("^own_maint$"))
async def own_maint(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return await q.answer("⛔ Owner only!", show_alert=True)
    state = "🔴 ON" if MAINTENANCE["active"] else "🟢 OFF"
    toggle = "🟢 Turn OFF Maintenance" if MAINTENANCE["active"] else "🔴 Turn ON Maintenance"
    await q.message.edit_text(
        f"🛠️ **MAINTENANCE MODE**\n\n"
        f"📊 **Current status:** {state}\n"
        f"📝 **Message:** {MAINTENANCE['reason']}\n\n"
        f"ℹ️ _When ON, only the owner and sudo admins can use the bot._",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle, callback_data="own_maint_toggle")],
            [InlineKeyboardButton("✏️ Change Maintenance Message", callback_data="own_maint_msg")],
            [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]]))
    await q.answer()

@app.on_callback_query(filters.regex("^own_maint_toggle$"))
async def own_maint_toggle(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return
    MAINTENANCE["active"] = not MAINTENANCE["active"]
    await settings_col.update_one(
        {"key": "maintenance"},
        {"$set": {"is_active": MAINTENANCE["active"], "reason": MAINTENANCE["reason"]}},
        upsert=True)
    state = "🔴 ON" if MAINTENANCE["active"] else "🟢 OFF"
    toggle = "🟢 Turn OFF Maintenance" if MAINTENANCE["active"] else "🔴 Turn ON Maintenance"
    await q.answer(f"✅ Maintenance is now {state}!", show_alert=True)
    try:
        await q.message.edit_text(
            f"🛠️ **MAINTENANCE MODE**\n\n"
            f"📊 **Current status:** {state}\n"
            f"📝 **Message:** {MAINTENANCE['reason']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(toggle, callback_data="own_maint_toggle")],
                [InlineKeyboardButton("✏️ Change Maintenance Message", callback_data="own_maint_msg")],
                [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]]))
    except Exception:
        pass

@app.on_callback_query(filters.regex("^own_maint_msg$"))
async def own_maint_msg(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return
    user_states[q.from_user.id] = "OWN_MAINT_REASON"
    await q.message.edit_text(
        "✏️ **CHANGE MAINTENANCE MESSAGE**\n\nSend the new message users will see "
        "during maintenance:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="own_maint")]]))
    await q.answer()

@app.on_callback_query(filters.regex("^own_stats$"))
async def own_stats(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return await q.answer("⛔ Owner only!", show_alert=True)
    total_gw = await giveaways.count_documents({})
    active_gw = await giveaways.count_documents({"status": "active"})
    total_parts = await entries.count_documents({})
    total_votes = await votes.count_documents({})
    total_users = await users_col.count_documents({})
    await q.message.edit_text(
        f"📊 **BOT STATISTICS**\n\n"
        f"🎁 **Total giveaways:** {total_gw} (Active: {active_gw})\n"
        f"👥 **Total participants:** {total_parts}\n"
        f"🗳️ **Total votes:** {total_votes}\n"
        f"👤 **Total known users:** {total_users}\n"
        f"🚫 **Banned users:** {len(BANNED_USERS)}\n"
        f"👥 **Sudo admins:** {len(SUDO_USERS) - 1}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]]))
    await q.answer()

# ==================== OWNER TEXT COMMANDS (ban/unban) ====================
@app.on_message(filters.command("gban") & filters.private)
async def gban_cmd(_, m: Message):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("⛔ **Owner only command.**")
    parts = m.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return await m.reply_text("Format: `/gban <user_id> [reason]`")
    target = int(parts[1])
    reason = parts[2] if len(parts) > 2 else "No reason provided"
    await ban_user(target, reason)
    await m.reply_text(f"🚫 **User `{target}` banned globally.**\n📝 **Reason:** {reason}")

@app.on_message(filters.command("gunban") & filters.private)
async def gunban_cmd(_, m: Message):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("⛔ **Owner only command.**")
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return await m.reply_text("Format: `/gunban <user_id>`")
    await unban_user(int(parts[1]))
    await m.reply_text(f"🟢 **User `{parts[1]}` unbanned globally.**")

# ==================== LEAVE DETECTION (version-safe, no raw imports) ====================
IN_CHAT = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
OUT_CHAT = {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}

def _raw_status_name(status_obj):
    """Map raw chat member status to ChatMemberStatus via class name —
    works on any Kurigram/Pyrogram version without imports."""
    if status_obj is None:
        return ChatMemberStatus.LEFT
    name = type(status_obj).__name__
    if name == "ChatMemberStatusCreator":
        return ChatMemberStatus.OWNER
    if name == "ChatMemberStatusAdmin":
        return ChatMemberStatus.ADMINISTRATOR
    if name == "ChatMemberStatusBanned":
        return ChatMemberStatus.BANNED
    if name == "ChatMemberStatusRight":
        return ChatMemberStatus.MEMBER
    return ChatMemberStatus.LEFT  # Left / Empty / unknown

@app.on_raw_update()
async def on_raw_member_update(client, update, users, chats):
    # Version-safe: check by class name instead of importing UpdateChatMember
    if type(update).__name__ != "UpdateChatMember":
        return
    old_member = getattr(update, "old_chat_member", None)
    new_member = getattr(update, "new_chat_member", None)
    old_s = _raw_status_name(old_member.status if old_member else None)
    new_s = _raw_status_name(new_member.status if new_member else None)
    if not (old_s in IN_CHAT and new_s in OUT_CHAT):
        return

    # Who left — from_id is the affected user in chat member updates
    user_id = getattr(update, "from_id", None)
    if user_id is None and new_member and getattr(new_member, "user", None):
        user_id = new_member.user.id
    if user_id is None:
        return

    # Convert raw chat_id to a real channel id (-100xxxxxxxxxx)
    raw_cid = getattr(update, "chat_id", None)
    if raw_cid is None:
        return
    chat_id = int("-100" + str(abs(raw_cid))) if raw_cid < 0 else raw_cid

    async for g in giveaways.find({"channel_id": chat_id}):
        gid = str(g["_id"])
        removed = await votes.find({"giveaway_id": gid,
                                    "voter_id": user_id}).to_list(None)
        for rv in removed:
            await entries.find_one_and_update(
                {"giveaway_id": gid, "user_id": rv["entry_user"]},
                {"$inc": {"votes": -1}})
            await refresh_vote_button(gid, rv["entry_user"])
        await votes.delete_many({"giveaway_id": gid, "voter_id": user_id})
        e = await entries.find_one_and_delete({"giveaway_id": gid, "user_id": user_id})
        if e and e.get("message_id"):
            try:
                await app.delete_messages(e["chat_id"], e["message_id"])
            except Exception:
                pass

# ==================== END / STATS ====================
@app.on_message(filters.command("end") & filters.private)
async def end_cmd(_, m: Message):
    block = await gate(m.from_user.id)
    if block:
        return await m.reply_text(block)
    g = await get_active_gw(m.from_user.id)
    if not g:
        return await m.reply_text("⛔ **No active giveaway for you.** Only the creator "
                                  "or the bot owner can end it.")
    gid = str(g["_id"])
    await giveaways.update_one({"_id": g["_id"]}, {"$set": {"status": "ended"}})
    top = await entries.find({"giveaway_id": gid}).sort("votes", -1).limit(5).to_list(5)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    text = "🏁 **GIVEAWAY ENDED — FINAL RESULTS**\n\n"
    text += "\n".join(f"{medals[i]} {r['name']} — **{r['votes']}** votes"
                      for i, r in enumerate(top)) or "No participants."
    await m.reply_text(text)
    try:
        await app.send_message(g["channel_id"], text)
    except Exception:
        pass

@app.on_message(filters.command("stats") & filters.private)
async def stats_cmd(_, m: Message):
    block = await gate(m.from_user.id)
    if block:
        return await m.reply_text(block)
    g = await get_active_gw(m.from_user.id)
    if not g:
        return await m.reply_text("⛔ **No active giveaway for you.**")
    gid = str(g["_id"])
    tp = await entries.count_documents({"giveaway_id": gid})
    tv = await votes.count_documents({"giveaway_id": gid})
    top = await entries.find({"giveaway_id": gid}).sort("votes", -1).limit(10).to_list(10)
    text = (f"📊 **LIVE STATS**\n\n👥 **Participants:** {tp}\n"
            f"🗳️ **Total votes:** {tv}\n\n**Top 10:**\n")
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(top):
        text += f"{medals[i] if i < 3 else f'{i+1}.'} {r['name']} — **{r['votes']}**\n"
    await m.reply_text(text)

# ==================== RUN ====================
async def main():
    await init_db()
    await app.start()
    print("🚀 Giveaway Bot Started!")
    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
