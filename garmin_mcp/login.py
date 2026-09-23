"""One-off interactive login, for when Garmin wants a multi-factor code.

The MCP server speaks MCP over stdin/stdout, so it can never prompt for
anything. Run this in a terminal instead: it authenticates once, caches the
session token, and the server picks that up from then on.
"""

from __future__ import annotations

import getpass
import os
import sys

from .session import TOKEN_FILE, GarminError, build_client, login_error, mask_email


def _prompt_mfa() -> str:
    return input("Garmin multi-factor code (check email/authenticator): ").strip()


class _NoInput(Exception):
    """stdin is not a terminal, so we cannot ask for anything."""


def _ask(prompt: str, *, secret: bool = False) -> str:
    try:
        if secret:
            # Typed by you, never echoed, never written down — it goes straight
            # to Garmin and only the resulting token is kept.
            return getpass.getpass(prompt)
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise _NoInput from exc


def main() -> int:
    if not sys.stdin.isatty():
        print(
            "This command needs a terminal so it can prompt you. Run it directly "
            "in a shell, or set GARMIN_EMAIL and GARMIN_PASSWORD first.",
            file=sys.stderr,
        )
        return 2

    try:
        email = os.environ.get("GARMIN_EMAIL", "").strip() or _ask("Garmin email: ")
        if not email:
            print("An email address is required.", file=sys.stderr)
            return 2
        os.environ["GARMIN_EMAIL"] = email

        if not os.environ.get("GARMIN_PASSWORD"):
            os.environ["GARMIN_PASSWORD"] = _ask(
                f"Garmin password for {mask_email(email)}: ", secret=True
            )
    except _NoInput:
        print("\nCancelled.", file=sys.stderr)
        return 2

    if not os.environ.get("GARMIN_PASSWORD"):
        print("A password is required.", file=sys.stderr)
        return 2

    print(f"Logging in as {mask_email(email)} ...")
    try:
        client = build_client(prompt_mfa=_prompt_mfa)
        # Same call the server makes; on success the library writes the token
        # cache itself, with owner-only permissions.
        client.login(tokenstore=str(TOKEN_FILE))
    except GarminError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - surface whatever Garmin said
        print(f"\n{login_error(exc)}", file=sys.stderr)
        return 1
    finally:
        # Don't leave the password sitting in this process's environment.
        os.environ.pop("GARMIN_PASSWORD", None)

    name = getattr(client, "full_name", None) or getattr(client, "display_name", "")
    print(f"\nLogged in{f' as {name}' if name else ''}.")
    print(f"Session cached at {TOKEN_FILE}")
    print("You can now start the MCP server, or restart Claude Desktop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
