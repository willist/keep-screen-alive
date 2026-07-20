import sys
import warnings

import dateparser

from keep_alive.backends import get_backend


def main():
    input_value = " ".join(sys.argv[1:])
    parser_settings = {
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        now = dateparser.parse("now", settings=parser_settings)
        parser_settings["RELATIVE_BASE"] = now
        later = dateparser.parse(input_value, settings=parser_settings)

    if later is None:
        print("Missing a target")
        sys.exit(1)

    if now >= later:
        print(f"{later} is in the past. It is currently {now}")
        sys.exit(1)

    diff = (later - now).seconds

    backend = get_backend()
    backend.cleanup()
    backend.inhibit(diff)

    print(f"Keeping alive until {later:%I:%M%p %Z, %b %d, %Y}")


if __name__ == "__main__":
    main()
