# -*- coding: utf-8 -*-

import json
import re


def extract_streamers_from_main():
    try:
        with open('main.py', 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print("[-] main.py not found")
        return None

    # grab all Streamer("username") calls
    pattern = r'Streamer\("([^"]+)"'
    matches = re.findall(pattern, content)

    if not matches:
        print("[!] no streamers found in main.py")
        return None

    print(f"[+] {len(matches)} streamers found")

    streamers = []
    for username in matches:
        streamer_data = {
            "username": username.lower().strip(),
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
                    "delay_mode": "FROM_END",
                    "delay": 6,
                    "minimum_points": 20000,
                    "filter_condition": {
                        "by": "TOTAL_USERS",
                        "where": "LTE",
                        "value": 800
                    }
                }
            }
        }
        streamers.append(streamer_data)
        print(f"  -> {username}")

    return streamers


def create_config_file(streamers):
    config = {
        "streamers": streamers,
        "global_settings": {
            "default_bet_percentage": 5,
            "default_max_points": 1000,
            "default_make_predictions": False,
            "default_strategy": "SMART"
        },
        "metadata": {
            "created_by": "migrate_to_json.py",
            "total_streamers": len(streamers)
        }
    }

    output_file = "streamers_config.json"

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"\n[+] file created: {output_file}")
        print(f"[*] {len(streamers)} streamers exported")
        return True
    except Exception as e:
        print(f"[-] error creating file: {e}")
        return False


def main():
    print("""
+-------------------------------------------------------+
|                                                       |
|   Migration to streamers_config.json                  |
|                                                       |
|   Extracts streamers from main.py and creates         |
|   the JSON configuration file                         |
|                                                       |
+-------------------------------------------------------+
    """)

    print("[*] searching for streamers in main.py...\n")
    streamers = extract_streamers_from_main()

    if not streamers:
        print("\n[-] migration cancelled: no streamers found")
        return

    print("\n" + "=" * 60)
    print("[*] preview of streamers to export:")
    print("=" * 60)
    for i, s in enumerate(streamers, 1):
        print(f"{i:2d}. {s['username']}")
    print("=" * 60)

    response = input("\n[?] create streamers_config.json? (y/n): ").lower()

    if response == 'y':
        if create_config_file(streamers):
            print("\n" + "=" * 60)
            print("[+] migration completed successfully!")
            print("=" * 60)
            print("\nnext steps:")
            print("  1. check streamers_config.json")
            print("  2. customize settings if needed")
            print("  3. run: python main_dynamic.py")
            print("\n[!] tip: keep a backup of your original main.py!")
        else:
            print("\n[-] migration failed")
    else:
        print("\n[-] migration cancelled by user")


if __name__ == "__main__":
    main()