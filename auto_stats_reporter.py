# -*- coding: utf-8 -*-

import asyncio
import logging
from datetime import datetime
import requests

logger = logging.getLogger(__name__)


class AutoStatsReporter:

    def __init__(self, miner_instance, telegram_token, chat_id, interval_hours=6):
        self.miner = miner_instance
        self.telegram_api = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        self.chat_id = chat_id
        self.interval_hours = interval_hours
        self.start_time = datetime.now()

    def _send_telegram_message(self, message):
        try:
            response = requests.post(
                url=self.telegram_api,
                data={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_notification": False,
                },
                timeout=10
            )
            return response.ok
        except Exception as e:
            logger.error(f"error sending telegram message: {e}")
            return False

    def generate_report(self):
        if not self.miner or not self.miner.running:
            return None

        total_points = sum(s.channel_points for s in self.miner.streamers)
        total_gained = sum(
            s.channel_points - original
            for s, original in zip(self.miner.streamers, self.miner.original_streamers)
        )
        online_count = sum(1 for s in self.miner.streamers if s.is_online)
        offline_count = len(self.miner.streamers) - online_count
        uptime = datetime.now() - self.miner.start_datetime if self.miner.start_datetime else None

        # top 5 by current points
        top_streamers = sorted(
            self.miner.streamers,
            key=lambda s: s.channel_points,
            reverse=True
        )[:5]

        message = "*Automatic Statistics Report*\n\n"
        message += f"Generated: {datetime.now().strftime('%d/%m/%Y at %H:%M')}\n\n"

        message += "*Overview*\n"
        message += f"-> Total points: {total_points:,}\n"
        message += f"-> Points gained: +{total_gained:,}\n"
        message += f"-> Streamers followed: {len(self.miner.streamers)}\n"
        message += f"-> Online: {online_count} | Offline: {offline_count}\n"

        if uptime:
            hours = int(uptime.total_seconds() // 3600)
            minutes = int((uptime.total_seconds() % 3600) // 60)
            message += f"-> Uptime: {hours}h {minutes}m\n"

        message += f"\n*Top 5 streamers (by points)*\n"
        for i, streamer in enumerate(top_streamers, 1):
            status = "[ON]" if streamer.is_online else "[OFF]"
            message += f"{i}. {status} {streamer.username}: {streamer.channel_points:,} pts\n"

        # points/hour since session start
        if uptime and uptime.total_seconds() > 0:
            points_per_hour = total_gained / (uptime.total_seconds() / 3600)
            message += f"\n-> Average: {points_per_hour:.1f} points/hour"

        return message

    async def start_reporting(self):
        logger.info(f"[*] auto stats reporter started (interval: {self.interval_hours}h)")
        interval_seconds = self.interval_hours * 3600

        while self.miner and self.miner.running:
            try:
                await asyncio.sleep(interval_seconds)
                report = self.generate_report()
                if report:
                    if self._send_telegram_message(report):
                        logger.info("[+] statistics report sent")
                    else:
                        logger.error("[-] failed to send report")
            except Exception as e:
                logger.error(f"error in reporter: {e}")
                await asyncio.sleep(60)


def start_auto_reporter(miner_instance, telegram_token, chat_id, interval_hours=6):
    # usage in main_dynamic.py:
    #   from auto_stats_reporter import start_auto_reporter
    #   start_auto_reporter(miner_instance=twitch_miner, telegram_token=TELEGRAM_TOKEN,
    #                       chat_id=TELEGRAM_CHAT_ID, interval_hours=6)
    import threading

    reporter = AutoStatsReporter(miner_instance, telegram_token, chat_id, interval_hours)

    def run_reporter():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(reporter.start_reporting())

    reporter_thread = threading.Thread(target=run_reporter, daemon=True)
    reporter_thread.name = "Auto Stats Reporter"
    reporter_thread.start()

    logger.info(f"[+] auto stats reporter started (reports every {interval_hours}h)")
    return reporter


if __name__ == "__main__":
    print("[!] this module should be imported in main_dynamic.py")
    print("\nadd in your main_dynamic.py:")
    print("""
    from auto_stats_reporter import start_auto_reporter

    start_auto_reporter(
        miner_instance=twitch_miner,
        telegram_token=TELEGRAM_TOKEN,
        chat_id=TELEGRAM_CHAT_ID,
        interval_hours=6
    )
    """)