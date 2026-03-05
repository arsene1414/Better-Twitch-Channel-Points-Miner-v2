# -*- coding: utf-8 -*-

import json
import logging
import os
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio

logger = logging.getLogger(__name__)

CONFIG_FILE = "streamers_config.json"


class TwitchMinerTelegramBot:

    def __init__(self, token: str, chat_id: int, miner_instance=None):
        self.token = token
        self.chat_id = chat_id
        self.miner = miner_instance
        self.config_file = CONFIG_FILE

        if not os.path.exists(self.config_file):
            self._save_config({"streamers": [], "settings": {}})

    # --- config helpers ---

    def _load_config(self):
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return {"streamers": [], "settings": {}}

    def _save_config(self, config):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info("Configuration saved successfully")
            return True
        except Exception as e:
            logger.error(f"Error saving config: {e}")
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

    # --- commands ---

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
*Twitch Channel Points Miner - Management Bot*

*Streamer Management:*
-> /add <username> - Add a streamer
-> /remove <username> - Remove a streamer
-> /list - Show all streamers
-> /status - Check streamers online status

*Settings:*
-> /set\_bet <username> <percentage> - Modify bet %
-> /set\_max\_points <username> <points> - Modify max\_points
-> /enable\_predictions <username> - Enable predictions
-> /disable\_predictions <username> - Disable predictions

*Information:*
-> /stats - Global statistics
-> /help - Show this help

Note: Changes are applied immediately without restart!
        """
        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /add <username>")
            return

        username = context.args[0].lower().strip()
        config = self._load_config()

        if any(s.get("username") == username for s in config["streamers"]):
            await update.message.reply_text(f"[!] {username} is already in the list!")
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
                f"[+] Streamer *{username}* added successfully!\n"
                f"-> Default settings applied.\n"
                f"-> Use /set\\_* to customize.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("[-] Error adding streamer")

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /remove <username>")
            return

        username = context.args[0].lower().strip()
        config = self._load_config()

        initial_count = len(config["streamers"])
        config["streamers"] = [s for s in config["streamers"] if s.get("username") != username]

        if len(config["streamers"]) == initial_count:
            await update.message.reply_text(f"[!] {username} is not in the list!")
            return

        if self._save_config(config):
            if self.miner and self.miner.running:
                await self._remove_streamer_from_running_miner(username)

            await update.message.reply_text(
                f"[-] Streamer *{username}* removed successfully!",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("[-] Error removing streamer")

    async def cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        config = self._load_config()

        if not config["streamers"]:
            await update.message.reply_text("No streamers configured!")
            return

        message = "*Streamers List:*\n\n"
        for i, streamer in enumerate(config["streamers"], 1):
            username = streamer.get("username", "Unknown")
            predictions = "on" if streamer.get("settings", {}).get("make_predictions") else "off"
            bet_pct = streamer.get("settings", {}).get("bet", {}).get("percentage", 5)
            max_pts = streamer.get("settings", {}).get("bet", {}).get("max_points", 1000)

            message += f"{i}. *{username}*\n"
            message += f"   -> Predictions: {predictions}\n"
            message += f"   -> Bet: {bet_pct}% (max: {max_pts} pts)\n\n"

        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.miner or not self.miner.running:
            await update.message.reply_text("[!] Miner is not running!")
            return

        # restore persisted timestamps before building the message
        self._restore_timestamps_from_cache()

        message = "*Streamers Status:*\n\n"
        online_count = 0

        for streamer in self.miner.streamers:
            status = "[ONLINE]" if streamer.is_online else "[OFFLINE]"
            points = f"{streamer.channel_points:,}" if hasattr(streamer, 'channel_points') else "N/A"

            if streamer.is_online:
                online_count += 1
                # time since stream went online
                online_since = datetime.fromtimestamp(streamer.online_at).strftime("%d/%m %H:%M") if streamer.online_at else "unknown"
                timing = f"online since {online_since}"
            else:
                # offline_at is set when they go offline, online_at is the fallback
                if streamer.offline_at:
                    last_seen = datetime.fromtimestamp(streamer.offline_at).strftime("%d/%m %H:%M")
                    timing = f"last seen {last_seen}"
                elif streamer.online_at:
                    last_seen = datetime.fromtimestamp(streamer.online_at).strftime("%d/%m %H:%M")
                    timing = f"last seen {last_seen}"
                else:
                    timing = "never seen online"

            message += f"{status} *{streamer.username}*\n"
            message += f"   -> Points: {points}\n"
            message += f"   -> {timing}\n\n"

        message += f"\nTotal: {online_count}/{len(self.miner.streamers)} online"
        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_set_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /set_bet <username> <percentage>")
            return

        username = context.args[0].lower().strip()
        try:
            percentage = int(context.args[1])
            if percentage < 1 or percentage > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("[-] Percentage must be between 1 and 100!")
            return

        config = self._load_config()
        streamer_found = False

        for streamer in config["streamers"]:
            if streamer.get("username") == username:
                streamer["settings"]["bet"]["percentage"] = percentage
                streamer_found = True
                break

        if not streamer_found:
            await update.message.reply_text(f"[!] Streamer {username} not found!")
            return

        if self._save_config(config):
            await update.message.reply_text(
                f"[+] Bet percentage for *{username}* updated: {percentage}%",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("[-] Error updating config")

    async def cmd_set_max_points(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /set_max_points <username> <points>")
            return

        username = context.args[0].lower().strip()
        try:
            max_points = int(context.args[1])
            if max_points < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("[-] Points must be a positive number!")
            return

        config = self._load_config()
        streamer_found = False

        for streamer in config["streamers"]:
            if streamer.get("username") == username:
                streamer["settings"]["bet"]["max_points"] = max_points
                streamer_found = True
                break

        if not streamer_found:
            await update.message.reply_text(f"[!] Streamer {username} not found!")
            return

        if self._save_config(config):
            await update.message.reply_text(
                f"[+] Max points for *{username}* updated: {max_points}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("[-] Error updating config")

    async def cmd_enable_predictions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /enable_predictions <username>")
            return

        username = context.args[0].lower().strip()
        await self._toggle_predictions(update, username, True)

    async def cmd_disable_predictions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /disable_predictions <username>")
            return

        username = context.args[0].lower().strip()
        await self._toggle_predictions(update, username, False)

    async def _toggle_predictions(self, update, username, enabled):
        config = self._load_config()
        streamer_found = False

        for streamer in config["streamers"]:
            if streamer.get("username") == username:
                streamer["settings"]["make_predictions"] = enabled
                streamer_found = True
                break

        if not streamer_found:
            await update.message.reply_text(f"[!] Streamer {username} not found!")
            return

        if self._save_config(config):
            status = "enabled [on]" if enabled else "disabled [off]"
            await update.message.reply_text(
                f"Predictions {status} for *{username}*",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("[-] Error updating config")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.miner or not self.miner.running:
            await update.message.reply_text("[!] Miner is not running!")
            return

        total_points = sum(s.channel_points for s in self.miner.streamers)
        online = sum(1 for s in self.miner.streamers if s.is_online)

        uptime = datetime.now() - self.miner.start_datetime if self.miner.start_datetime else None

        message = "*Global Statistics:*\n\n"
        message += f"-> Total points: {total_points:,}\n"
        message += f"-> Streamers: {len(self.miner.streamers)}\n"
        message += f"-> Online: {online}\n"
        if uptime:
            message += f"-> Uptime: {str(uptime).split('.')[0]}\n"
        message += f"-> Session: {self.miner.session_id[:8]}..."

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
            import time

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

    def start(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # job_queue disabled to avoid apscheduler/tzlocal incompatibility on linux
        app = Application.builder().token(self.token).job_queue(None).build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_start))
        app.add_handler(CommandHandler("add", self.cmd_add))
        app.add_handler(CommandHandler("remove", self.cmd_remove))
        app.add_handler(CommandHandler("list", self.cmd_list))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("set_bet", self.cmd_set_bet))
        app.add_handler(CommandHandler("set_max_points", self.cmd_set_max_points))
        app.add_handler(CommandHandler("enable_predictions", self.cmd_enable_predictions))
        app.add_handler(CommandHandler("disable_predictions", self.cmd_disable_predictions))
        app.add_handler(CommandHandler("stats", self.cmd_stats))

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