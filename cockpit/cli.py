"""Kommandozeile: python -m cockpit.cli <befehl>

  init-db                 Tabellen anlegen
  hash-password           Passwort-Hash für COCKPIT_USERS erzeugen
  nightly [--days N]      Abzug der letzten N Tage (Standard 7) + Alerts
  backfill --days N       Einmaliger Rück-Abzug (z. B. 120 Tage) für die Historie
  weekly [--end DATUM]    Wochenreport erzeugen und versenden
  monthly [--end DATUM]   Monatsreport erzeugen und versenden
  test-connections        Google/Microsoft/Trello/OpenAI/Teams prüfen
  google-auth             OAuth-Refresh-Token für Google Ads erzeugen
  ms-auth                 OAuth-Refresh-Token für Microsoft Advertising erzeugen
  seed-demo               Synthetische Demo-Daten einspielen (DEMO_MODE)
  purge-demo              Demo-Daten löschen
  serve                   Entwicklungsserver starten
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .config import settings
from .db import init_db, session_scope


def cmd_hash_password(_args):
    from .auth import hash_password

    pw = getpass.getpass("Passwort: ")
    pw2 = getpass.getpass("Wiederholen: ")
    if pw != pw2:
        print("Passwörter stimmen nicht überein.")
        sys.exit(1)
    print(hash_password(pw))


def cmd_init_db(_args):
    init_db()
    print(f"Datenbank bereit: {settings.database_url}")


def cmd_nightly(args):
    init_db()
    from . import jobs

    with session_scope() as s:
        run = jobs.nightly(s, days_back=args.days)
        print(run.status)
        print(run.log)


def cmd_backfill(args):
    init_db()
    import datetime as dt

    from .util import yesterday

    end = yesterday()
    start = end - dt.timedelta(days=args.days - 1)
    with session_scope() as s:
        if settings.google_ready:
            from .connectors import google_ads

            # In 30-Tage-Blöcken, damit die Abfragen klein bleiben
            cur = start
            while cur <= end:
                block_end = min(cur + dt.timedelta(days=29), end)
                print(f"Google {cur} bis {block_end}")
                google_ads.pull_all(s, cur, block_end, print)
                s.commit()
                cur = block_end + dt.timedelta(days=1)
        else:
            print("Google Ads nicht konfiguriert.")
        if settings.ms_ready:
            from .connectors import microsoft_ads

            print(f"Microsoft {start} bis {end}")
            microsoft_ads.pull_all(s, start, end, print)
        if settings.trello_ready:
            from .connectors import trello

            print(f"Trello: {trello.pull(s)} Karten")
        if settings.gsc_ready:
            from .connectors import search_console

            gsc_start = end - dt.timedelta(days=min(args.days, 480) - 1)  # Search Console hält 16 Monate
            print(f"Search Console {gsc_start} bis {end}")
            search_console.pull(s, gsc_start, end, print)


def cmd_weekly(args):
    init_db()
    from . import jobs
    from .util import parse_date

    with session_scope() as s:
        run = jobs.weekly(s, parse_date(args.end), send=not args.no_send)
        print(run.status)
        print(run.log)


def cmd_monthly(args):
    init_db()
    from . import jobs
    from .util import parse_date

    with session_scope() as s:
        run = jobs.monthly(s, parse_date(args.end), send=not args.no_send)
        print(run.status)
        print(run.log)


def cmd_test_connections(_args):
    def check(name, ready, fn):
        if not ready:
            print(f"{name}: nicht konfiguriert")
            return
        try:
            print(f"{name}: {fn()}")
        except Exception as exc:
            print(f"{name}: FEHLER {exc}")

    from .connectors import google_ads, microsoft_ads, search_console, sea_excel, trello

    check("Google Ads", settings.google_ready, google_ads.test_connection)
    check("Microsoft Ads", settings.ms_ready, microsoft_ads.test_connection)
    check("Trello", settings.trello_ready, trello.test_connection)
    check("Search Console", settings.gsc_ready, search_console.test_connection)
    check("Excel Conversions SEA", settings.excel_ready, sea_excel.test_connection)

    def openai_check():
        from .llm import client

        return client.plain("Antworte mit einem Wort.", "Sag: bereit")

    check(f"Sprachmodell ({settings.llm_provider})", settings.openai_ready, openai_check)

    def teams_check():
        from . import notify

        return "gesendet" if notify.send_teams("SEA-Cockpit Test", "Verbindung funktioniert.") else "nicht gesendet"

    check("Teams", bool(settings.teams_webhook_url), teams_check)


def cmd_google_auth(_args):
    """Einmaliger OAuth-Durchlauf (Desktop-App-Client) → Refresh-Token für .env."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("pip install google-auth-oauthlib")
        sys.exit(1)
    client_id = settings.google_client_id or input("OAuth Client-ID: ").strip()
    client_secret = settings.google_client_secret or getpass.getpass("OAuth Client-Secret: ").strip()
    cfg = {"installed": {"client_id": client_id, "client_secret": client_secret, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(cfg, scopes=["https://www.googleapis.com/auth/adwords"])
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    print("\nIn .env eintragen:")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")


def cmd_ms_auth(_args):
    from .connectors import microsoft_ads

    client_id = settings.ms_client_id or input("Microsoft App (Client) ID: ").strip()
    print("1. Diese URL im Browser öffnen und mit dem Microsoft-Advertising-Nutzer zustimmen:")
    print(microsoft_ads.auth_url(client_id))
    uri = input("2. Die vollständige Weiterleitungs-URL (beginnt mit https://login.microsoftonline.com/...) hier einfügen: ").strip()
    token = microsoft_ads.refresh_token_from_redirect(client_id, settings.ms_client_secret, uri)
    print("\nIn .env eintragen:")
    print(f"MS_ADS_REFRESH_TOKEN={token}")


def cmd_seed_demo(_args):
    init_db()
    from . import demo

    with session_scope() as s:
        n = demo.seed(s)
    print(f"Demo-Daten eingespielt ({n} Tageszeilen). DEMO_MODE=1 in .env setzen, damit der Nightly-Job nichts überschreibt.")


def cmd_purge_demo(_args):
    init_db()
    from . import demo

    with session_scope() as s:
        demo.purge(s)
    print("Demo-Daten entfernt.")


def cmd_serve(args):
    import uvicorn

    init_db()
    uvicorn.run("cockpit.main:app", host=args.host, port=args.port, reload=args.reload)


def main(argv=None):
    p = argparse.ArgumentParser(prog="cockpit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    sub.add_parser("hash-password").set_defaults(fn=cmd_hash_password)
    n = sub.add_parser("nightly")
    n.add_argument("--days", type=int, default=7)
    n.set_defaults(fn=cmd_nightly)
    b = sub.add_parser("backfill")
    b.add_argument("--days", type=int, default=120)
    b.set_defaults(fn=cmd_backfill)
    for name, fn in (("weekly", cmd_weekly), ("monthly", cmd_monthly)):
        sp = sub.add_parser(name)
        sp.add_argument("--end", default=None)
        sp.add_argument("--no-send", action="store_true")
        sp.set_defaults(fn=fn)
    sub.add_parser("test-connections").set_defaults(fn=cmd_test_connections)
    sub.add_parser("google-auth").set_defaults(fn=cmd_google_auth)
    sub.add_parser("ms-auth").set_defaults(fn=cmd_ms_auth)
    sub.add_parser("seed-demo").set_defaults(fn=cmd_seed_demo)
    sub.add_parser("purge-demo").set_defaults(fn=cmd_purge_demo)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    sv.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
