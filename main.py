# pip install kurigram motor pymongo
import asyncio
import os
import re
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
                  "remove", "cancel", "stop", "finish", "deduct", "−", "-")
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

app = Client("GiveawayBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_states = {}    # {user_id: state}
temp_data = {}      # {user_id: {...}}

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

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Create Giveaway", callback_data="gw_create")],
        [InlineKeyboardButton("❓ Help", callback_data="gw_help")]
    ])

# ==================== /START ====================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(_, m: Message):
    args = m.text.split(maxsplit=1)
    # Participation link: /start <giveaway_id>
    if len(args) == 2 and len(args[1]) == 24:
        return await handle_participant(m, args[1])
    await m.reply_text(
        "👋 **Welcome to Giveaway Bot!**\n\n"
        "• Create giveaways for your channel\n"
        "• Participants must join your channel to enter and vote\n"
        "• Real votes only — leaving the channel removes votes\n\n"
        "Choose an option below:",
        reply_markup=main_menu())

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
        f"🏁 /end — finish & announce winner")

# ==================== PARTICIPANT FLOW ====================
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
    await entries.insert_one({"giveaway_id": gid, "user_id": uid,
                              "name": name, "votes": 0})
    try:
        post = await app.send_message(
            g["channel_id"], f"**{name}**",
            reply_markup=vote_button(0, gid, uid))
        await entries.update_one(
            {"giveaway_id": gid, "user_id": uid},
            {"$set": {"message_id": post.id, "chat_id": g["channel_id"]}})
    except Exception:
        await entries.delete_one({"giveaway_id": gid, "user_id": uid})
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
    # simulate entry creation directly
    uid = q.from_user.id
    existing = await entries.find_one({"giveaway_id": gid, "user_id": uid})
    if existing:
        return
    name = q.from_user.first_name or "Participant"
    await entries.insert_one({"giveaway_id": gid, "user_id": uid,
                              "name": name, "votes": 0})
    try:
        post = await app.send_message(g["channel_id"], f"**{name}**",
                                      reply_markup=vote_button(0, gid, uid))
        await entries.update_one(
            {"giveaway_id": gid, "user_id": uid},
            {"$set": {"message_id": post.id, "chat_id": g["channel_id"]}})
        await app.send_message(uid, "🎉 **You are participating!** Good luck! 🍀")
    except Exception:
        await entries.delete_one({"giveaway_id": gid, "user_id": uid})

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

# ---- Per-participant controls ----
@app.on_callback_query(filters.regex(r"^gp:(.+):(\d+)$"))
async def panel_user(_, q: CallbackQuery):
    gid, uid = q.matches.group(1), int(q.matches.group(2))
    e = await entries.find_one({"giveaway_id": gid, "user_id": uid})
    if not e:
        return await q.answer("Participant not found.", show_alert=True)
    await q.message.edit_text(
        f"👤 **{e['name']}**\n🗳️ **Current votes:** {e['votes']}\n\n"
        "Select an action:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ +1 Vote", callback_data=f"gadj:{gid}:{uid}:1")],
            [InlineKeyboardButton("➕ +5 Votes", callback_data=f"gadj:{gid}:{uid}:5")],
            [InlineKeyboardButton("➕ +10 Votes", callback_data=f"gadj:{gid}:{uid}:10")],
            [InlineKeyboardButton("➖ −1 Vote", callback_data=f"gadj:{gid}:{uid}:-1")],
            [InlineKeyboardButton("➖ −5 Votes", callback_data=f"gadj:{gid}:{uid}:-5")],
            [InlineKeyboardButton("🔢 Set Custom Votes", callback_data=f"gcust:{gid}:{uid}")],
            [InlineKeyboardButton("🗑 Remove This Participant", callback_data=f"grm1:{gid}:{uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"gpanel:{gid}")]]))
    await q.answer()

async def authorize(user_id: int, gid: str) -> bool:
    g = await giveaways.find_one({"_id": ObjectId(gid)})
    return g and (user_id == g["creator_id"] or user_id == OWNER_ID)

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
    # refresh panel user view
    await q.message.edit_text(
        f"👤 **{e['name']}**\n🗳️ **Current votes:** {new_v}\n\nSelect an action:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ +1 Vote", callback_data=f"gadj:{gid}:{uid}:1")],
            [InlineKeyboardButton("➕ +5 Votes", callback_data=f"gadj:{gid}:{uid}:5")],
            [InlineKeyboardButton("➕ +10 Votes", callback_data=f"gadj:{gid}:{uid}:10")],
            [InlineKeyboardButton("➖ −1 Vote", callback_data=f"gadj:{gid}:{uid}:-1")],
            [InlineKeyboardButton("➖ −5 Votes", callback_data=f"gadj:{gid}:{uid}:-5")],
            [InlineKeyboardButton("🔢 Set Custom Votes", callback_data=f"gcust:{gid}:{uid}")],
            [InlineKeyboardButton("🗑 Remove This Participant", callback_data=f"grm1:{gid}:{uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"gpanel:{gid}")]]))

# ---- Custom number votes ----
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

# ---- Add participant (manual) ----
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

# ---- Remove participant (selection list) ----
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

# ==================== TEXT INPUT ROUTER (custom votes / add participant) ====================
@app.on_message(filters.private & filters.text &
                ~filters.command(["start", "manage", "stats", "end"]))
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
        if len(parts) < 2 or not parts[0].isdigit():
            return await m.reply_text("❌ **Wrong format!** Use: `UserID Name`")
        uid, name = int(parts[0]), parts[1]
        if not await authorize(m.from_user.id, gid):
            return await m.reply_text("⛔ **Not authorized.**")
        if await entries.find_one({"giveaway_id": gid, "user_id": uid}):
            return await m.reply_text("⚠️ **This participant already exists.**")
        g = await giveaways.find_one({"_id": ObjectId(gid)})
        await entries.insert_one({"giveaway_id": gid, "user_id": uid,
                                  "name": name, "votes": 0})
        try:
            post = await app.send_message(g["channel_id"], f"**{name}**",
                                          reply_markup=vote_button(0, gid, uid))
            await entries.update_one(
                {"giveaway_id": gid, "user_id": uid},
                {"$set": {"message_id": post.id, "chat_id": g["channel_id"]}})
        except Exception:
            pass
        await m.reply_text(f"✅ **Added participant {name} with a vote box in the channel!**")

# ==================== LEAVE DETECTION (auto vote removal) ====================
@app.on_raw_chat_member()
async def on_chat_member(_, raw):
    old_s = raw.old_chat_member.status if raw.old_chat_member else None
    new_s = raw.new_chat_member.status if raw.new_chat_member else None
    was_in = old_s in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                       ChatMemberStatus.OWNER)
    now_out = new_s in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)
    if not (was_in and now_out):
        return
    user_id = raw.new_chat_member.user.id
    async for g in giveaways.find({"channel_id": raw.chat.id}):
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
    await app.start()
    print("🚀 Giveaway Bot Started!")
    await idle()
    await app.stop()

app.run(main())
