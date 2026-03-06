# -*- coding: utf-8 -*-

import json
import logging
import os
import time
import threading
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logger = logging.getLogger(__name__)

CONFIG_FILE = "streamers_config.json"


class TwitchMinerTelegramBot:

    def __init__(self, token: str, chat_id: int, miner_instance=None):
        self.token = token
        self.chat_id = chat_id
        self.miner = miner_instance
        self.config_file = CONFIG_FILE

        # usernames chosen as priority (max 2), kept in insertion order
        self.priority_streamers = []
        # tracks when a prioritized streamer went offline, username -> timestamp
        # used for the 10min cooldown before dropping priority
        self.priority_offline_at = {}
        # cooldown in seconds before a prioritized streamer loses its status after going offline
        self.priority_cooldown = 600

        if not os.path.exists(self.config_file):
            self._save_config({"streamers": [], "settings": {}})

    # --- helpers ---

    def _escape(self, text):
        # escape markdown special chars in dynamic values like usernames
        # underscore is the main culprit with twitch names like krl_stream
        return (str(text)
                .replace("_", r"\_")
                .replace("*", r"\*")
                .replace("`", r"\`")
                .replace("[", r"\["))

    def _load_config(self):
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"error loading config: {e}")
            return {"streamers": [], "settings": {}}

    def _save_config(self, config):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info("configuration saved successfully")
            return True
        except Exception as e:
            logger.error(f"error saving config: {e}")
            return False

    def _load_status_cache(self):
        # read online_at/offline_at from streamers_config.json directly
        config = self._load_config()
        cache = {}
        for entry in config.get("streamers", []):
            username = entry.get("username")
            if username:
                cache[username] = {
                    "online_at": entry.get("online_at", 0),
                    "offline_at": entry.get("offline_at", 0),
                }
        return cache

    def _restore_timestamps_from_cache(self):
        # inject saved timestamps into live streamer objects on startup or after restart
        if not self.miner:
            return
        cache = self._load_status_cache()
        for streamer in self.miner.streamers:
            if streamer.username in cache:
                entry = cache[streamer.username]
                # only restore if not already set this session
                if streamer.online_at == 0:
                    streamer.online_at = entry.get("online_at", 0)
                if streamer.offline_at == 0:
                    streamer.offline_at = entry.get("offline_at", 0)

    def _get_streamer_timing(self, streamer):
        # returns a human-readable timing string for a streamer
        if streamer.is_online:
            if streamer.online_at:
                return f"online since {datetime.fromtimestamp(streamer.online_at).strftime('%d/%m %H:%M')}"
            return "online"
        if streamer.offline_at:
            return f"last seen {datetime.fromtimestamp(streamer.offline_at).strftime('%d/%m %H:%M')}"
        if streamer.online_at:
            return f"last seen {datetime.fromtimestamp(streamer.online_at).strftime('%d/%m %H:%M')}"
        return "never seen online"

    def _build_status_message_and_keyboard(self):
        # shared builder for /status and the back button callback
        self._check_priority_cooldowns()
        self._restore_timestamps_from_cache()

        watching = getattr(self.miner, 'currently_watching', [])
        online_count = sum(1 for s in self.miner.streamers if s.is_online)
        streamers = self.miner.streamers

        message = "🎮 *Streamers Status:*\n\n"

        # build one compact line per streamer, then pair them 2 per row
        # format: 🟢 username ⭐👁 • 1,234 pts • last seen 06/03 11:08
        lines = []
        for streamer in streamers:
            status = "🟢" if streamer.is_online else "🔴"
            points = f"{streamer.channel_points:,}" if hasattr(streamer, 'channel_points') else "N/A"
            timing = self._get_streamer_timing(streamer)

            tags = ""
            if streamer.username in self.priority_streamers:
                tags += "⭐"
            elif streamer.username in self.priority_offline_at:
                tags += "🕐"
            if streamer.username in watching:
                tags += "👁"

            lines.append(
                f"{status} *{self._escape(streamer.username)}*{tags}\n"
                f"  💰 {points}  🕐 {self._escape(timing)}"
            )

        # 2 streamers per row, separated by a blank line between rows
        for i in range(0, len(lines), 2):
            if i + 1 < len(lines):
                message += lines[i] + "\n\n" + lines[i + 1] + "\n\n"
            else:
                message += lines[i] + "\n\n"

        summary = f"📊 {online_count}/{len(streamers)} online"
        if watching:
            summary += f"  •  👁 {', '.join(self._escape(u) for u in watching)}"
        message += summary

        # buttons: 2 per row, grouped by username length to keep rows balanced
        buttons = [
            InlineKeyboardButton(f"⚙️ {s.username}", callback_data=f"settings:{s.username}")
            for s in streamers
        ]
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]

        return message, InlineKeyboardMarkup(keyboard)

    # --- priority helpers ---

    def _reorder_streamers_by_priority(self):
        # move prioritized streamers to the front of self.miner.streamers so
        # Priority.ORDER naturally picks them first (max 2 watched at a time)
        if not self.miner or not self.priority_streamers:
            return
        priority_objs = []
        rest = []
        for s in self.miner.streamers:
            if s.username in self.priority_streamers:
                priority_objs.append(s)
            else:
                rest.append(s)
        # preserve the user-defined order within priority_objs
        priority_objs.sort(key=lambda s: self.priority_streamers.index(s.username))
        self.miner.streamers[:] = priority_objs + rest
        # keep original_streamers in sync
        self.miner.original_streamers[:] = [s.channel_points for s in self.miner.streamers]

    def _check_priority_cooldowns(self):
        # removes streamers whose cooldown has expired
        expired = [
            username for username, offline_ts in self.priority_offline_at.items()
            if (time.time() - offline_ts) >= self.priority_cooldown
        ]
        for username in expired:
            self.priority_streamers = [u for u in self.priority_streamers if u != username]
            del self.priority_offline_at[username]
            logger.info(f"priority cooldown expired for {username}, removed from priority list")

    def _notify_priority_offline(self, username):
        # starts the cooldown timer when a prioritized streamer goes offline
        if username in self.priority_streamers and username not in self.priority_offline_at:
            self.priority_offline_at[username] = time.time()
            logger.info(f"streamer {username} went offline - priority cooldown started (10min)")

    # --- commands ---

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
\U0001f3ae *Twitch Channel Points Miner*

\U0001f4cb *Streamer Management:*
-> /add <username> - Add a streamer
-> /remove <username> - Remove a streamer
-> /status - All streamers (tap \u2699\ufe0f for settings)

\u2699\ufe0f *Settings:*
-> /set_bet <username> <percentage> - Modify bet %
-> /set_max_points <username> <points> - Modify max_points
-> /enable_predictions <username> - Enable predictions
-> /disable_predictions <username> - Disable predictions

\U0001f3af *Watch Priority:*
-> /priority <username> - Set priority directly
-> /priority - Show list + tap to pick online streamers
-> /unpriority - Tap to remove a priority streamer
-> /unpriority <username> - Remove directly

\u2139\ufe0f *Information:*
-> /online - Show currently live streamers
-> /stats - Global statistics
-> /help - Show this help

\u26a1 Changes are applied immediately without restart!
        """
        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("\u2139\ufe0f Usage: /add <username>")
            return

        username = context.args[0].lower().strip()
        config = self._load_config()

        if any(s.get("username") == username for s in config["streamers"]):
            await update.message.reply_text(f"\u26a0\ufe0f {self._escape(username)} is already in the list!")
            return

        new_streamer = {
            "username": username,
            "online_at": 0,
            "offline_at": 0,
            "settings": {
                "make_predictions": False,
                "follow_raid": True,
                "claim_drops": True,
                "watch_streak": True,
                "community_goals": True,
                "bet": {
                    "strategy": "SMART",
                    "percentage": 5,
                    "stealth_mode": True,
                    "percentage_gap": 20,
                    "max_points": 1000,
                    "filter_condition": {
                        "by": "TOTAL_USERS",
                        "where": "LTE",
                        "value": 800
                    }
                }
            },
            "added_at": datetime.now().isoformat()
        }

        config["streamers"].append(new_streamer)

        if self._save_config(config):
            if self.miner and self.miner.running:
                await self._add_streamer_to_running_miner(username, new_streamer["settings"])

            await update.message.reply_text(
                f"\u2705 Streamer *{self._escape(username)}* added successfully!\n"
                f"-> Default settings applied.\n"
                f"\U0001f4a1 Use /set_* to customize.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("\u274c Error adding streamer")

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            # no argument - show button picker with all streamers
            config = self._load_config()
            streamers_cfg = config.get("streamers", [])
            if not streamers_cfg:
                await update.message.reply_text("\U0001f4ed No streamers configured!")
                return

            btns = [
                InlineKeyboardButton(f"\u274c {s.get('username')}", callback_data=f"remove:{s.get('username')}")
                for s in streamers_cfg
            ]
            keyboard = [btns[i:i+2] for i in range(0, len(btns), 2)]
            await update.message.reply_text(
                "\U0001f5d1 *Select a streamer to remove:*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        username = context.args[0].lower().strip()
        await self._do_remove(update.message.reply_text, username)

    async def callback_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # handles the remove button press - asks for confirmation
        query = update.callback_query
        await query.answer()

        username = query.data.split(":", 1)[1]

        keyboard = [[
            InlineKeyboardButton("\u2705 Confirm", callback_data=f"remove_confirm:{username}"),
            InlineKeyboardButton("\u274c Cancel", callback_data="remove_cancel"),
        ]]
        await query.edit_message_text(
            f"\U0001f5d1 Remove *{self._escape(username)}*? This cannot be undone.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def callback_remove_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # confirmed removal via button
        query = update.callback_query
        await query.answer()

        username = query.data.split(":", 1)[1]
        await self._do_remove(query.edit_message_text, username)

    async def callback_remove_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("\u274c Removal cancelled.")

    async def _do_remove(self, reply_fn, username):
        # shared remove logic used by both the command and the button callback
        config = self._load_config()
        initial_count = len(config["streamers"])
        config["streamers"] = [s for s in config["streamers"] if s.get("username") != username]

        if len(config["streamers"]) == initial_count:
            await reply_fn(f"\u26a0\ufe0f {self._escape(username)} is not in the list!")
            return

        if self._save_config(config):
            if self.miner and self.miner.running:
                await self._remove_streamer_from_running_miner(username)
            await reply_fn(
                f"\u2705 Streamer *{self._escape(username)}* removed successfully!",
                parse_mode="Markdown"
            )
        else:
            await reply_fn("\u274c Error removing streamer")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # fused /list + /status: shows live state for all streamers
        # each streamer has an inline settings button that reveals bet/prediction details
        if not self.miner or not self.miner.running:
            # miner not running - fall back to config file view
            config = self._load_config()
            streamers_cfg = config.get("streamers", [])
            if not streamers_cfg:
                await update.message.reply_text("\U0001f4ed No streamers configured!")
                return

            message = "\U0001f4cb *Streamers (miner offline):*\n\n"
            btns = []
            for s in streamers_cfg:
                u = s.get("username", "?")
                message += f"\u26ab *{self._escape(u)}*\n"
                btns.append(InlineKeyboardButton(f"\u2699\ufe0f {u}", callback_data=f"settings:{u}"))

            keyboard = [btns[i:i+2] for i in range(0, len(btns), 2)]

            await update.message.reply_text(
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        message, keyboard = self._build_status_message_and_keyboard()
        await update.message.reply_text(message, parse_mode="Markdown", reply_markup=keyboard)

    async def callback_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # handles the settings button press - shows bet/prediction details for a streamer
        query = update.callback_query
        await query.answer()

        username = query.data.split(":", 1)[1]
        config = self._load_config()

        streamer_cfg = next(
            (s for s in config.get("streamers", []) if s.get("username") == username),
            None
        )
        if not streamer_cfg:
            await query.edit_message_text(f"\u26a0\ufe0f {self._escape(username)} not found in config.")
            return

        s = streamer_cfg.get("settings", {})
        bet = s.get("bet", {})
        fc = bet.get("filter_condition", {})

        def yn(val, default=True):
            return "\u2705" if s.get(val, default) else "\u274c"

        # pre-compute conditionals to avoid backslash-in-fstring (Python < 3.12)
        predictions_str = "\u2705 on" if s.get("make_predictions") else "\u274c off"
        stealth_str = "\u2705" if bet.get("stealth_mode", True) else "\u274c"

        text = (
            f"\u2699\ufe0f *Settings: {self._escape(username)}*\n\n"
            f"\U0001f3af Predictions: {predictions_str}\n"
            f"\u2694\ufe0f Follow raids: {yn('follow_raid')}\n"
            f"\U0001f4a7 Claim drops: {yn('claim_drops')}\n"
            f"\U0001f525 Watch streak: {yn('watch_streak')}\n"
            f"\U0001f3af Community goals: {yn('community_goals')}\n\n"
            f"\U0001f4b0 *Bet settings:*\n"
            f"   Strategy: `{bet.get('strategy', 'SMART')}`\n"
            f"   Percentage: `{bet.get('percentage', 5)}%`\n"
            f"   Max points: `{bet.get('max_points', 1000)}`\n"
            f"   Min points: `{bet.get('minimum_points', 20000)}`\n"
            f"   Stealth: {stealth_str}\n"
            f"   Delay: `{bet.get('delay_mode', 'FROM_END')}` / `{bet.get('delay', 6)}s`\n"
            f"   Gap: `{bet.get('percentage_gap', 20)}%`\n\n"
            f"\U0001f50d *Filter:* `{fc.get('by', '?')}` `{fc.get('where', '?')}` `{fc.get('value', '?')}`"
        )

        # back button returns to the status view
        keyboard = [[InlineKeyboardButton("\u2190 Back", callback_data="back_to_status")]]
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def callback_back_to_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # rebuilds the status view in place when Back is pressed
        query = update.callback_query
        await query.answer()

        if not self.miner or not self.miner.running:
            await query.edit_message_text("\u26a0\ufe0f Miner is not running!")
            return

        message, keyboard = self._build_status_message_and_keyboard()
        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=keyboard)

    async def callback_priority_set(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # handles clicking a streamer button in the /priority picker
        query = update.callback_query
        await query.answer()

        username = query.data.split(":", 1)[1]
        self._check_priority_cooldowns()

        if username in self.priority_streamers:
            await query.edit_message_text(
                f"\u26a0\ufe0f *{self._escape(username)}* is already a priority streamer!",
                parse_mode="Markdown"
            )
            return

        if len(self.priority_streamers) >= 2:
            await query.edit_message_text(
                "\u26a0\ufe0f Already 2 priority streamers set.\nUse /unpriority to free a slot."
            )
            return

        self.priority_offline_at.pop(username, None)
        self.priority_streamers.append(username)
        self._reorder_streamers_by_priority()

        streamer_obj = next((s for s in self.miner.streamers if s.username == username), None)
        st = "\U0001f7e2 online" if streamer_obj and streamer_obj.is_online else "\U0001f534 offline"
        priority_list = ", ".join(self._escape(u) for u in self.priority_streamers)
        await query.edit_message_text(
            f"\u2b50 *{self._escape(username)}* set as priority ({st}).\n"
            f"\U0001f3af Priority: {priority_list}",
            parse_mode="Markdown"
        )

    async def callback_unpriority(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # handles clicking a streamer button in the /unpriority picker
        query = update.callback_query
        await query.answer()

        username = query.data.split(":", 1)[1]

        if username not in self.priority_streamers and username not in self.priority_offline_at:
            await query.edit_message_text(
                f"\u26a0\ufe0f *{self._escape(username)}* is not in the priority list.",
                parse_mode="Markdown"
            )
            return

        self.priority_streamers = [u for u in self.priority_streamers if u != username]
        self.priority_offline_at.pop(username, None)
        self._reorder_streamers_by_priority()

        remaining = ", ".join(self._escape(u) for u in self.priority_streamers) or "none"
        await query.edit_message_text(
            f"\u2705 *{self._escape(username)}* removed from priority.\n"
            f"\U0001f3af Priority: {remaining}",
            parse_mode="Markdown"
        )

    async def cmd_online(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.miner or not self.miner.running:
            await update.message.reply_text("\u26a0\ufe0f Miner is not running!")
            return

        online_streamers = [s for s in self.miner.streamers if s.is_online]

        if not online_streamers:
            await update.message.reply_text("\U0001f4ed No streamers currently online.")
            return

        watching = getattr(self.miner, 'currently_watching', [])

        message = f"\U0001f7e2 *Online Streamers ({len(online_streamers)}):*\n\n"
        for streamer in online_streamers:
            points = f"{streamer.channel_points:,}" if hasattr(streamer, 'channel_points') else "N/A"
            since = datetime.fromtimestamp(streamer.online_at).strftime("%d/%m %H:%M") if streamer.online_at else "unknown"
            watch_tag = " \U0001f441" if streamer.username in watching else ""
            message += f"\U0001f7e2 *{self._escape(streamer.username)}*{watch_tag}\n"
            message += f"   \U0001f4b0 {points} pts  •  \U0001f550 since {self._escape(since)}\n\n"

        if watching:
            message += f"\U0001f441 Watching: {', '.join(self._escape(u) for u in watching)}"

        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_priority(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.miner or not self.miner.running:
            await update.message.reply_text("\u26a0\ufe0f Miner is not running!")
            return

        self._check_priority_cooldowns()

        if context.args:
            # direct /priority <username>
            username = context.args[0].lower().strip()

            if not any(s.username == username for s in self.miner.streamers):
                await update.message.reply_text(f"\u26a0\ufe0f Streamer {self._escape(username)} not found!")
                return
            if username in self.priority_streamers:
                await update.message.reply_text(
                    f"\u26a0\ufe0f *{self._escape(username)}* is already a priority streamer!",
                    parse_mode="Markdown"
                )
                return
            if len(self.priority_streamers) >= 2:
                await update.message.reply_text(
                    "\u26a0\ufe0f Already 2 priority streamers set.\nUse /unpriority to free a slot."
                )
                return

            self.priority_offline_at.pop(username, None)
            self.priority_streamers.append(username)
            self._reorder_streamers_by_priority()

            streamer_obj = next((s for s in self.miner.streamers if s.username == username), None)
            st = "\U0001f7e2 online" if streamer_obj and streamer_obj.is_online else "\U0001f534 offline"
            await update.message.reply_text(
                f"\u2b50 *{self._escape(username)}* set as priority ({st}).\n"
                f"\U0001f3af Priority: {', '.join(self._escape(u) for u in self.priority_streamers)}",
                parse_mode="Markdown"
            )
            return

        # no argument - show current state + inline button picker for unprioritized online streamers
        lines = ""
        if self.priority_streamers:
            lines = "\n".join(f"  \u2b50 *{self._escape(u)}*" for u in self.priority_streamers)
            if self.priority_offline_at:
                cooling = []
                for u, ts in self.priority_offline_at.items():
                    remaining = max(1, int((self.priority_cooldown - (time.time() - ts)) / 60) + 1)
                    cooling.append(f"  \U0001f550 *{self._escape(u)}* (cooldown: ~{remaining}min)")
                lines += "\n\n*In cooldown:*\n" + "\n".join(cooling)
        else:
            lines = "_(none)_"

        remaining_slots = 2 - len(self.priority_streamers)
        online_unprioritized = [
            s for s in self.miner.streamers
            if s.is_online and s.username not in self.priority_streamers
        ]

        message = f"\U0001f3af *Priority Streamers:*\n{lines}"
        keyboard = []

        if remaining_slots > 0 and online_unprioritized:
            slot_word = "slots" if remaining_slots > 1 else "slot"
            message += f"\n\n*Pick from online ({remaining_slots} {slot_word} free):*"
            btns = [
                InlineKeyboardButton(f"\u2b50 {s.username}", callback_data=f"priority_set:{s.username}")
                for s in online_unprioritized
            ]
            keyboard = [btns[i:i+2] for i in range(0, len(btns), 2)]
        elif remaining_slots == 0:
            message += "\n\n_2 slots filled. Use /unpriority to change._"
        else:
            message += "\n\n_No online streamers available to prioritize._"

        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )

    async def cmd_unpriority(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._check_priority_cooldowns()

        if context.args:
            # direct /unpriority <username>
            username = context.args[0].lower().strip()

            if username not in self.priority_streamers:
                await update.message.reply_text(
                    f"\u26a0\ufe0f *{self._escape(username)}* is not in the priority list!",
                    parse_mode="Markdown"
                )
                return

            self.priority_streamers = [u for u in self.priority_streamers if u != username]
            self.priority_offline_at.pop(username, None)
            self._reorder_streamers_by_priority()

            remaining = ", ".join(self._escape(u) for u in self.priority_streamers) or "none"
            await update.message.reply_text(
                f"\u2705 *{self._escape(username)}* removed from priority.\n"
                f"\U0001f3af Priority: {remaining}",
                parse_mode="Markdown"
            )
            return

        # no argument - show button picker
        if not self.priority_streamers and not self.priority_offline_at:
            await update.message.reply_text("\U0001f3af No priority streamers set.")
            return

        all_btns = []
        for u in self.priority_streamers:
            all_btns.append(InlineKeyboardButton(f"\u274c {u}", callback_data=f"unpriority:{u}"))
        for u in self.priority_offline_at:
            if u not in self.priority_streamers:
                all_btns.append(InlineKeyboardButton(f"\U0001f550 {u} (cooldown)", callback_data=f"unpriority:{u}"))

        keyboard = [all_btns[i:i+2] for i in range(0, len(all_btns), 2)]

        await update.message.reply_text(
            "\U0001f3af *Select a streamer to remove from priority:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def cmd_set_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("\u2139\ufe0f Usage: /set_bet <username> <percentage>")
            return

        username = context.args[0].lower().strip()
        try:
            percentage = int(context.args[1])
            if percentage < 1 or percentage > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("\u274c Percentage must be between 1 and 100!")
            return

        config = self._load_config()
        streamer_found = False

        for streamer in config["streamers"]:
            if streamer.get("username") == username:
                streamer["settings"]["bet"]["percentage"] = percentage
                streamer_found = True
                break

        if not streamer_found:
            await update.message.reply_text(f"\u26a0\ufe0f Streamer {self._escape(username)} not found!")
            return

        if self._save_config(config):
            await update.message.reply_text(
                f"\u2705 Bet percentage for *{self._escape(username)}* updated: {percentage}%",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("\u274c Error updating config")

    async def cmd_set_max_points(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("\u2139\ufe0f Usage: /set_max_points <username> <points>")
            return

        username = context.args[0].lower().strip()
        try:
            max_points = int(context.args[1])
            if max_points < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("\u274c Points must be a positive number!")
            return

        config = self._load_config()
        streamer_found = False

        for streamer in config["streamers"]:
            if streamer.get("username") == username:
                streamer["settings"]["bet"]["max_points"] = max_points
                streamer_found = True
                break

        if not streamer_found:
            await update.message.reply_text(f"\u26a0\ufe0f Streamer {self._escape(username)} not found!")
            return

        if self._save_config(config):
            await update.message.reply_text(
                f"\u2705 Max points for *{self._escape(username)}* updated: {max_points}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("\u274c Error updating config")

    async def cmd_enable_predictions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("\u2139\ufe0f Usage: /enable_predictions <username>")
            return
        await self._toggle_predictions(update, context.args[0].lower().strip(), True)

    async def cmd_disable_predictions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("\u2139\ufe0f Usage: /disable_predictions <username>")
            return
        await self._toggle_predictions(update, context.args[0].lower().strip(), False)

    async def _toggle_predictions(self, update, username, enabled):
        config = self._load_config()
        streamer_found = False

        for streamer in config["streamers"]:
            if streamer.get("username") == username:
                streamer["settings"]["make_predictions"] = enabled
                streamer_found = True
                break

        if not streamer_found:
            await update.message.reply_text(f"\u26a0\ufe0f Streamer {self._escape(username)} not found!")
            return

        if self._save_config(config):
            status = "\u2705 enabled" if enabled else "\u274c disabled"
            await update.message.reply_text(
                f"\U0001f3af Predictions {status} for *{self._escape(username)}*",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("\u274c Error updating config")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.miner or not self.miner.running:
            await update.message.reply_text("\u26a0\ufe0f Miner is not running!")
            return

        total_points = sum(s.channel_points for s in self.miner.streamers)
        online = sum(1 for s in self.miner.streamers if s.is_online)
        uptime = datetime.now() - self.miner.start_datetime if self.miner.start_datetime else None

        message = "\U0001f4ca *Global Statistics:*\n\n"
        message += f"\U0001f4b0 Total points: {total_points:,}\n"
        message += f"\U0001f3ae Streamers: {len(self.miner.streamers)}\n"
        message += f"\U0001f7e2 Online: {online}\n"
        if uptime:
            message += f"\u23f1\ufe0f Uptime: {str(uptime).split('.')[0]}\n"
        message += f"\U0001f511 Session: {self.miner.session_id[:8]}..."

        await update.message.reply_text(message, parse_mode="Markdown")

    # --- dynamic streamer management ---

    async def _add_streamer_to_running_miner(self, username, settings_data):
        # build a full streamer object from the config dict and inject it into the live miner
        try:
            from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings
            from TwitchChannelPointsMiner.classes.entities.Bet import BetSettings, Strategy, FilterCondition, Condition, OutcomeKeys, DelayMode
            from TwitchChannelPointsMiner.classes.entities.PubsubTopic import PubsubTopic
            from TwitchChannelPointsMiner.utils import set_default_settings
            from TwitchChannelPointsMiner.classes.Settings import Settings

            if any(s.username == username for s in self.miner.streamers):
                logger.info(f"streamer {username} already in running miner")
                return

            # map string keys from json to actual enum values
            bet_data = settings_data.get("bet", {})
            filter_data = bet_data.get("filter_condition", {})

            strategy_map = {"SMART": Strategy.SMART, "PERCENTAGE": Strategy.PERCENTAGE, "SMART_MONEY": Strategy.SMART_MONEY, "HIGH_ODDS": Strategy.HIGH_ODDS, "MOST_VOTED": Strategy.MOST_VOTED}
            outcome_keys_map = {"TOTAL_USERS": OutcomeKeys.TOTAL_USERS, "TOTAL_POINTS": OutcomeKeys.TOTAL_POINTS, "PERCENTAGE_USERS": OutcomeKeys.PERCENTAGE_USERS, "ODDS_PERCENTAGE": OutcomeKeys.ODDS_PERCENTAGE, "ODDS": OutcomeKeys.ODDS, "TOP_POINTS": OutcomeKeys.TOP_POINTS}
            condition_map = {"LTE": Condition.LTE, "GTE": Condition.GTE, "LT": Condition.LT, "GT": Condition.GT}
            delay_mode_map = {"FROM_START": DelayMode.FROM_START, "FROM_END": DelayMode.FROM_END, "PERCENTAGE": DelayMode.PERCENTAGE}

            filter_condition = FilterCondition(
                by=outcome_keys_map.get(filter_data.get("by", "TOTAL_USERS"), OutcomeKeys.TOTAL_USERS),
                where=condition_map.get(filter_data.get("where", "LTE"), Condition.LTE),
                value=filter_data.get("value", 800)
            )
            bet_settings = BetSettings(
                strategy=strategy_map.get(bet_data.get("strategy", "SMART"), Strategy.SMART),
                percentage=bet_data.get("percentage", 5),
                percentage_gap=bet_data.get("percentage_gap", 20),
                max_points=bet_data.get("max_points", 1000),
                stealth_mode=bet_data.get("stealth_mode", True),
                delay_mode=delay_mode_map.get(bet_data.get("delay_mode", "FROM_END"), DelayMode.FROM_END),
                delay=bet_data.get("delay", 6),
                minimum_points=bet_data.get("minimum_points", 20000),
                filter_condition=filter_condition
            )
            streamer_settings = StreamerSettings(
                make_predictions=settings_data.get("make_predictions", False),
                follow_raid=settings_data.get("follow_raid", True),
                claim_drops=settings_data.get("claim_drops", True),
                claim_moments=settings_data.get("claim_moments", True),
                watch_streak=settings_data.get("watch_streak", True),
                community_goals=settings_data.get("community_goals", True),
                bet=bet_settings
            )

            streamer = Streamer(username, settings=streamer_settings)
            streamer.channel_id = self.miner.twitch.get_channel_id(username)
            streamer.settings = set_default_settings(streamer.settings, Settings.streamer_settings)
            streamer.settings.bet = set_default_settings(streamer.settings.bet, Settings.streamer_settings.bet)

            time.sleep(0.5)
            self.miner.twitch.load_channel_points_context(streamer)
            self.miner.twitch.check_streamer_online(streamer)

            # restore any previously persisted timestamps for this streamer
            cache = self._load_status_cache()
            if username in cache:
                if streamer.online_at == 0:
                    streamer.online_at = cache[username].get("online_at", 0)
                if streamer.offline_at == 0:
                    streamer.offline_at = cache[username].get("offline_at", 0)

            self.miner.streamers.append(streamer)
            self.miner.original_streamers.append(streamer.channel_points)

            # subscribe to all relevant pubsub topics for this streamer
            if self.miner.ws_pool is not None:
                self.miner.ws_pool.submit(PubsubTopic("video-playback-by-id", streamer=streamer))
                if streamer.settings.follow_raid:
                    self.miner.ws_pool.submit(PubsubTopic("raid", streamer=streamer))
                if streamer.settings.make_predictions:
                    self.miner.ws_pool.submit(PubsubTopic("predictions-channel-v1", streamer=streamer))
                if streamer.settings.claim_moments:
                    self.miner.ws_pool.submit(PubsubTopic("community-moments-channel-v1", streamer=streamer))
                if streamer.settings.community_goals:
                    self.miner.ws_pool.submit(PubsubTopic("community-points-channel-v1", streamer=streamer))

            logger.info(f"streamer {username} added to running miner dynamically")

        except Exception as e:
            logger.error(f"error adding {username} to running miner: {e}")

    async def _remove_streamer_from_running_miner(self, username):
        # remove streamer from live lists and clean up irc chat if active
        try:
            streamer_obj = next((s for s in self.miner.streamers if s.username == username), None)
            if not streamer_obj:
                logger.info(f"streamer {username} not found in running miner")
                return

            idx = self.miner.streamers.index(streamer_obj)

            if streamer_obj.irc_chat is not None:
                try:
                    streamer_obj.leave_chat()
                except Exception:
                    pass

            # pop from both lists to keep indexes in sync
            self.miner.streamers.pop(idx)
            if idx < len(self.miner.original_streamers):
                self.miner.original_streamers.pop(idx)

            logger.info(f"streamer {username} removed from running miner dynamically")

        except Exception as e:
            logger.error(f"error removing {username} from running miner: {e}")

    def _start_priority_watcher(self):
        # background thread that polls streamer online status every 30s
        # starts the cooldown timer when a priority streamer goes offline
        def watch():
            while True:
                time.sleep(30)
                try:
                    if not self.miner or not self.miner.running:
                        continue
                    self._check_priority_cooldowns()
                    for s in self.miner.streamers:
                        if s.username in self.priority_streamers and not s.is_online:
                            self._notify_priority_offline(s.username)
                        elif s.username in self.priority_offline_at and s.is_online:
                            # streamer came back online within cooldown - cancel cooldown
                            del self.priority_offline_at[s.username]
                            logger.info(f"streamer {s.username} back online - priority cooldown cancelled")
                            self._reorder_streamers_by_priority()
                except Exception as e:
                    logger.error(f"error in priority watcher: {e}")

        t = threading.Thread(target=watch, daemon=True)
        t.name = "Priority Watcher"
        t.start()

    def start(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # job_queue disabled to avoid apscheduler/tzlocal incompatibility on linux
        app = Application.builder().token(self.token).job_queue(None).build()

        # commands
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_start))
        app.add_handler(CommandHandler("add", self.cmd_add))
        app.add_handler(CommandHandler("remove", self.cmd_remove))
        app.add_handler(CommandHandler("list", self.cmd_status))   # /list redirects to /status
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("online", self.cmd_online))
        app.add_handler(CommandHandler("priority", self.cmd_priority))
        app.add_handler(CommandHandler("unpriority", self.cmd_unpriority))
        app.add_handler(CommandHandler("set_bet", self.cmd_set_bet))
        app.add_handler(CommandHandler("set_max_points", self.cmd_set_max_points))
        app.add_handler(CommandHandler("enable_predictions", self.cmd_enable_predictions))
        app.add_handler(CommandHandler("disable_predictions", self.cmd_disable_predictions))
        app.add_handler(CommandHandler("stats", self.cmd_stats))

        # inline button callbacks
        app.add_handler(CallbackQueryHandler(self.callback_settings, pattern=r"^settings:"))
        app.add_handler(CallbackQueryHandler(self.callback_back_to_status, pattern=r"^back_to_status$"))
        app.add_handler(CallbackQueryHandler(self.callback_priority_set, pattern=r"^priority_set:"))
        app.add_handler(CallbackQueryHandler(self.callback_unpriority, pattern=r"^unpriority:"))
        app.add_handler(CallbackQueryHandler(self.callback_remove, pattern=r"^remove:"))
        app.add_handler(CallbackQueryHandler(self.callback_remove_confirm, pattern=r"^remove_confirm:"))
        app.add_handler(CallbackQueryHandler(self.callback_remove_cancel, pattern=r"^remove_cancel$"))

        self._start_priority_watcher()

        logger.info("telegram bot started and ready to receive commands!")

        app.run_polling(stop_signals=None)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO)

    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")

    bot = TwitchMinerTelegramBot(TOKEN, int(CHAT_ID))
    bot.start()