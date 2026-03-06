# -*- coding: utf-8 -*-

# place this script in a tools/ subfolder at the project root.
# drop your old main.py in the same tools/ folder, then run:
#
#   python tools/migrate_to_json.py
#
# the generated streamers_config.json will be written to the project root (one level up).

import json
import re
import sys
import os

# project root is one level above this script's directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, "main.py")
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "streamers_config.json")


def extract_streamers_from_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[-] file not found: {filepath}")
        return None
    except Exception as e:
        print(f"[-] error reading file: {e}")
        return None

    pattern = r'Streamer\("([^"]+)"'
    matches = re.findall(pattern, content)

    if not matches:
        print(f"[!] no Streamer(...) calls found in {filepath}")
        return None

    print(f"[+] {len(matches)} streamers found")

    streamers = []
    for username in matches:
        streamers.append({
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
        })
        print(f"  -> {username}")

    return streamers


def create_config_file(streamers, output_path):
    if os.path.exists(output_path):
        response = input(f"\n[!] {output_path} already exists. overwrite? (y/n): ").lower()
        if response != 'y':
            print("[-] cancelled")
            return False

    config = {
        "streamers": streamers,
        "global_settings": {
            "default_bet_percentage": 5,
            "default_max_points": 1000,
            "default_make_predictions": False,
            "default_strategy": "SMART"
        }
    }

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"\n[+] file created: {output_path}")
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
|   Drop your old main.py in the tools/ folder and      |
|   run this script. The JSON will be written to the    |
|   project root.                                       |
|                                                       |
+-------------------------------------------------------+
    """)

    if not os.path.exists(DEFAULT_INPUT):
        print("[-] no main.py found in the tools/ folder.")
        print(f"[*] expected: {DEFAULT_INPUT}")
        print("[*] drop your old main.py there and re-run.")
        sys.exit(1)

    print(f"[*] reading from : {DEFAULT_INPUT}")
    print(f"[*] output to    : {DEFAULT_OUTPUT}\n")

    streamers = extract_streamers_from_file(DEFAULT_INPUT)

    if not streamers:
        print("\n[-] migration cancelled: no streamers found")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("[*] streamers to export:")
    print("=" * 60)
    for i, s in enumerate(streamers, 1):
        print(f"{i:2d}. {s['username']}")
    print("=" * 60)

    response = input("\n[?] create streamers_config.json? (y/n): ").lower()

    if response == 'y':
        if create_config_file(streamers, DEFAULT_OUTPUT):
            print("\n" + "=" * 60)
            print("[+] migration completed successfully!")
            print("=" * 60)
            print("\nnext steps:")
            print("  1. check streamers_config.json at the project root")
            print("  2. customize settings if needed")
            print("  3. run: python main.py")
        else:
            print("\n[-] migration failed")
            sys.exit(1)
    else:
        print("\n[-] migration cancelled by user")


if __name__ == "__main__":
    main()