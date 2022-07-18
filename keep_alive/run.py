import datetime
import math
import subprocess
import sys
import warnings

import dateparser


def main():
    input_value = ' '.join(sys.argv[1:])
    parser_settings = {
        'PREFER_DATES_FROM': 'future',
        'RETURN_AS_TIMEZONE_AWARE': True,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        later = dateparser.parse(input_value, settings=parser_settings)
        now = dateparser.parse('now', settings=parser_settings)

    if later is None:
        print("Missing a target")
        sys.exit(1)

    if now >= later:
        print(f"{later} is in the past. It is currently {now}")
        sys.exit(1)

    diff = (later - now).seconds

    subprocess.run([
        'killall',
        'caffeinate',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    subprocess.Popen([
        'caffeinate',
        '-d',
        '-u',
        '-t',
        str(diff),
    ])

    print(f"Keeping alive until {later:%I:%M%p %Z, %b %d, %Y}")


if __name__ == "__main__":
    main()
