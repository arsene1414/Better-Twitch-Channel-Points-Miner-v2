# -*- coding: utf-8 -*-

import json
import re
import sys
import os
import argparse


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

    # grab all Streamer("username") calls
    pattern = r'Streamer\("([^"]+)"'
    matches = re.findall(pattern, content)

    if not matches:
        print(f"[!] no Streamer(...) calls found in {filepath}")
        return None

    print(f"[+] {len(matches)} streamers found in {filepath}")

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


def create_config_file(streamers, output_path="streamers_config.json"):
    # don't overwrite an existing config without confirmation
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
    parser = argparse.ArgumentParser(
        description="Extract Streamer() calls from a main.py and generate streamers_config.json"
    )
    parser.add_argument(
        "--file", "-f",
        default=None,
        help="path to the main.py file to read (default: main.py in current directory)"
    )
    parser.add_argument(
        "--output", "-o",
        default="streamers_config.json",
        help="output path for the JSON config (default: streamers_config.json)"
    )
    args = parser.parse_args()

    print("""
+-------------------------------------------------------+
|                                                       |
|   Migration to streamers_config.json                  |
|                                                       |
|   Extracts Streamer() calls from main.py and creates  |
|   the JSON configuration file                         |
|                                                       |
+-------------------------------------------------------+
    """)

    # resolve source file
    if args.file:
        source_file = args.file
    else:
        source_file = "main.py"
        if not os.path.exists(source_file):
            print("[-] main.py not found in current directory.")
            print("[*] tip: use --file /path/to/your/main.py to specify a different location")
            sys.exit(1)

    print(f"[*] reading from: {os.path.abspath(source_file)}\n")
    streamers = extract_streamers_from_file(source_file)

    if not streamers:
        print("\n[-] migration cancelled: no streamers found")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("[*] streamers to export:")
    print("=" * 60)
    for i, s in enumerate(streamers, 1):
        print(f"{i:2d}. {s['username']}")
    print("=" * 60)

    response = input(f"\n[?] create {args.output}? (y/n): ").lower()

    if response == 'y':
        if create_config_file(streamers, args.output):
            print("\n" + "=" * 60)
            print("[+] migration completed successfully!")
            print("=" * 60)
            print("\nnext steps:")
            print("  1. check streamers_config.json")
            print("  2. customize settings if needed")
            print("  3. run: python main_dynamic.py")
        else:
            print("\n[-] migration failed")
            sys.exit(1)
    else:
        print("\n[-] migration cancelled by user")


if __name__ == "__main__":
    main()