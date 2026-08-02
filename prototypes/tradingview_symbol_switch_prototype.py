r"""Standalone prototype: measure real TradingView in-app symbol-switching speed.

Exploratory only -- NOT wired into main.py or display.py. Answers one
question: once a TradingView chart tab is open and logged in, how fast can
the displayed symbol be swapped via the ticker search box (not new tabs,
not page reloads)?

Uses Playwright (not Selenium): Playwright's launch_persistent_context()
gives a first-class, single-call way to reuse a real Chrome profile
directory (so a manual login persists across runs without scripting
credentials), and its auto-waiting locators are more reliable than
Selenium's explicit-wait boilerplate for a SPA-heavy site like
TradingView. Selenium would work too, but Playwright needs less code for
this exact prototype.

Usage:
    python prototypes/tradingview_symbol_switch_prototype.py                # Chromium (default)
    python prototypes/tradingview_symbol_switch_prototype.py --browser edge # Microsoft Edge

The --browser flag exists to test one specific hypothesis: that Google's
Sign-In bot-detection challenge on TradingView's Google login flow might
behave differently under a "real" branded browser (Edge) vs. Playwright's
bundled Chromium. Worth noting up front -- Google's detection is documented
to key off automation signals (navigator.webdriver, CDP fingerprints, input
timing) more than the browser's declared identity, and Playwright's
channel="msedge" still drives Edge via the same CDP automation protocol, so
this is a cheap-to-test hypothesis, not an expected fix. Each --browser
value uses its own persistent profile directory so a login in one doesn't
bleed into the other.

First run: a visible browser window opens to tradingview.com. Log in
manually (the profile directory persists the session for later runs).
Press Enter in the terminal once you're logged in and a chart is showing.
The script then cycles through TEST_SYMBOLS, measuring how long each
symbol switch actually takes (from submitting the new ticker to the
chart's symbol-name label visibly updating), and prints a results table.

--- SAFETY: using a real Edge profile instead of a Playwright-managed one ---

If Google Sign-In's bot-detection keeps interrupting the automated login in
the Playwright-managed profile, you can point this script at a real,
already-authenticated Edge profile via --profile-dir instead of fighting
the automated login. DO THIS WITH A DEDICATED SECONDARY PROFILE, NOT YOUR
LIVE DAILY-DRIVER PROFILE:

1. In Edge (your normal daily browser), click your profile icon (top-right)
   -> "Add profile" -> create a new local profile (no Microsoft sign-in
   needed) -- e.g. name it "TV-Test".
2. In that new profile's window, go to tradingview.com and log in via
   Google normally, as a human, in a real (non-automated) Edge window.
3. In that same window, open edge://version and copy the "Profile Path"
   value it shows (e.g. "C:\Users\User\AppData\Local\Microsoft\Edge\User
   Data\Profile 3").
4. Fully close that Edge window (Playwright needs the profile directory
   unlocked to attach to it).
5. Run: python prototypes/tradingview_symbol_switch_prototype.py --browser edge --profile-dir "<path from step 3>"
   Pass the full leaf path exactly as edge://version shows it (e.g.
   ".../User Data/Profile 3") -- the script internally splits this into
   Chromium's user_data_dir (the parent "User Data" root) plus a
   --profile-directory=<leaf name> flag, since Chromium's user_data_dir
   argument addresses the root folder, not an individual profile's
   subfolder (passing a subfolder directly makes Chromium silently
   initialize a fresh, unauthenticated profile inside it instead of
   loading the real one -- a real bug hit and fixed during this
   prototype's development, not a hypothetical).

Why not point --profile-dir at your actual live/default Edge profile
(commonly the folder literally named "Default")? Because that profile is
likely open and in active use, and:
  - Chromium-based browsers lock the profile directory while running;
    Playwright attaching to the same directory as your live browser risks
    a lock conflict (forcing you to close your real browsing session) or,
    worse, concurrent writes to the same SQLite databases (History,
    Cookies, Web Data, Login Data) from two processes, a known cause of
    profile corruption.
  - It carries every other logged-in account's session and all saved
    passwords/autofill -- far more exposure than this prototype needs.
  - Automated navigation writes into your real browsing history, which
    may sync to your other devices.
A dedicated secondary profile avoids all of this: it's real and
authenticated, but isolated from your daily-driver data. This script
refuses to silently proceed if --profile-dir points at a folder literally
named "Default" -- it requires an explicit typed confirmation first.
"""

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR_BY_BROWSER = {
    "chromium": Path(__file__).parent / ".tradingview_profile",
    "edge": Path(__file__).parent / ".tradingview_profile_edge",
}
CHART_URL = "https://www.tradingview.com/chart/"

TEST_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN",
    "GOOGL", "META", "NFLX", "AMD", "INTC",
]

POST_SUBMIT_PAUSE_SECONDS = 0.3  # configurable "short pause" between switches
SWITCH_TIMEOUT_MS = 5000  # max time to wait for the chart to visibly update


def switch_symbol(page, symbol: str) -> float:
    """Opens the symbol search, types `symbol`, submits, and waits for the
    chart's symbol-name label to reflect the new ticker. Returns the
    measured wall-clock seconds from submit to visible update."""
    # TradingView's symbol search is opened via a keyboard shortcut on the
    # chart itself, which is more robust across UI variants than chasing a
    # specific search-icon selector.
    page.click("body")
    page.keyboard.press("/")
    search_input = page.locator("input[data-role='search']").first
    search_input.wait_for(state="visible", timeout=SWITCH_TIMEOUT_MS)
    search_input.fill("")
    search_input.type(symbol, delay=20)

    start = time.perf_counter()

    # Submit: press Enter to accept the top autocomplete result.
    page.keyboard.press("Enter")

    # The chart's legend/symbol-name element updates once the new symbol's
    # data has loaded. Poll for it rather than a fixed sleep, so the
    # measurement reflects real update latency, not the configured pause.
    legend_selector = "[data-name='legend-source-item'] [data-name='legend-source-title'], .chart-markup-table .title-l31H9iuA"
    deadline = start + (SWITCH_TIMEOUT_MS / 1000)
    updated = False
    while time.perf_counter() < deadline:
        try:
            text = page.locator(legend_selector).first.inner_text(timeout=200)
        except Exception:
            text = ""
        if symbol.upper() in text.upper():
            updated = True
            break
        time.sleep(0.05)

    elapsed = time.perf_counter() - start
    if not updated:
        print(f"  WARNING: could not confirm {symbol} visibly updated within {SWITCH_TIMEOUT_MS}ms "
              f"(legend selector may be stale for this TradingView UI version) — recording elapsed time anyway.")
    return elapsed


def main():
    parser = argparse.ArgumentParser(description="TradingView symbol-switch timing prototype")
    parser.add_argument(
        "--browser", choices=["chromium", "edge"], default="chromium",
        help="Which browser to drive. 'edge' uses channel=msedge to test whether Google "
             "Sign-In's bot-detection behaves differently under a branded browser.",
    )
    parser.add_argument(
        "--profile-dir", default=None,
        help="Override the profile directory Playwright attaches to. Use this to point at a "
             "dedicated secondary Edge profile you created and logged into manually (via "
             "edge://version's 'Profile Path' in that profile) -- e.g. "
             "'C:\\Users\\User\\AppData\\Local\\Microsoft\\Edge\\User Data\\Profile 3'. "
             "Do NOT point this at your live daily-driver profile (commonly named 'Default') "
             "-- see the safety note in this file's module docstring.",
    )
    args = parser.parse_args()

    profile_directory_arg = None  # Chromium --profile-directory=NAME, set below when --profile-dir points at a named subprofile

    if args.profile_dir:
        profile_subdir = Path(args.profile_dir)
        if profile_subdir.name == "Default":
            confirm = input(
                "\n*** WARNING: this profile directory is named 'Default', which is almost "
                "always a live daily-driver profile still in active use (saved passwords, "
                "other logged-in accounts, real history). Automating it risks profile-lock "
                "conflicts and database corruption if it's not fully closed, and exposes every "
                "other account's session to this script -- a dedicated secondary profile is "
                "strongly preferred. Type EXACTLY 'yes I understand the risk' to continue "
                "anyway, anything else aborts: "
            )
            if confirm.strip() != "yes I understand the risk":
                print("Aborted. Create a dedicated secondary profile instead (see module docstring).")
                return
        if not profile_subdir.exists():
            print(f"Profile directory does not exist: {profile_subdir}")
            print("Create the profile in Edge first (see module docstring), close Edge, then rerun.")
            return

        # BUG (found via user report): Chromium's user_data_dir means the root
        # "User Data" folder, NOT an individual profile's subfolder. Passing
        # a subfolder (e.g. ".../User Data/Profile 2") directly as
        # user_data_dir makes Chromium treat THAT folder as the root and look
        # for a "Default" profile inside it -- which doesn't exist there --
        # so it silently creates a fresh, unauthenticated profile instead of
        # loading the real one. The fix: user_data_dir must be the PARENT
        # ("User Data") and the specific profile is selected via the
        # separate --profile-directory=NAME Chromium flag.
        profile_dir = profile_subdir.parent
        profile_directory_arg = profile_subdir.name
    else:
        profile_dir = PROFILE_DIR_BY_BROWSER[args.browser]
        profile_dir.mkdir(exist_ok=True)

    launch_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
    }
    if args.browser == "edge":
        launch_kwargs["channel"] = "msedge"
    if profile_directory_arg:
        launch_kwargs["args"] = [f"--profile-directory={profile_directory_arg}"]

    print(f"\nResolved launch config:")
    print(f"  user_data_dir (root 'User Data' folder) = {profile_dir}")
    print(f"  --profile-directory                      = {profile_directory_arg or '(none -- using Chromium default profile within that root)'}")
    print(f"  channel                                  = {launch_kwargs.get('channel', '(Playwright-bundled Chromium)')}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(**launch_kwargs)

        # DIAGNOSTIC (per user report of navigation silently not happening):
        # a real profile with prior browsing history commonly has "continue
        # where you left off" / session restore enabled. launch_persistent_context
        # triggers Chromium's normal startup, so restored tabs can appear as
        # additional context.pages entries -- separate from whatever page we
        # navigate. Blindly taking context.pages[0] risks grabbing one of
        # those restored tabs (or a transient about:blank/New Tab page)
        # instead of a page we actually control, and the tab we DO navigate
        # may end up backgrounded behind a restored tab that has visual
        # focus -- which matches "window shows whatever was last open"
        # rather than a navigation failure.
        print(f"\ncontext.pages right after launch: {len(context.pages)} page(s)")
        for i, p_ in enumerate(context.pages):
            print(f"  [{i}] {p_.url!r}")

        # Fix: always open a NEW page we control explicitly, rather than
        # reusing context.pages[0] (which may be a restored session tab),
        # and bring it to the front so it's the visible/active tab.
        page = context.new_page()
        page.bring_to_front()

        print(f"Navigating new page to {CHART_URL} ...")
        try:
            page.goto(CHART_URL, wait_until="domcontentloaded", timeout=30000)
            print(f"Navigation succeeded. page.url is now: {page.url!r}")
        except Exception as e:
            print(f"NAVIGATION FAILED: {e!r}")
            print("Aborting -- fix the navigation issue before continuing (see printed "
                  "context.pages list above for other tabs that may be stealing focus).")
            context.close()
            return

        print(f"\nLaunched with --browser={args.browser}"
              f"{' (channel=msedge)' if args.browser == 'edge' else ' (Playwright-bundled Chromium)'}.")
        input(
            "\nBrowser window opened to TradingView. Log in manually if not already "
            "logged in (this profile will persist the session for future runs), "
            "then make sure a chart is visible.\nPress Enter here once ready..."
        )

        print(f"\nStarting symbol-switch timing test: {len(TEST_SYMBOLS)} symbols, "
              f"{POST_SUBMIT_PAUSE_SECONDS}s pause between switches.\n")

        results = []
        pushback_observed = []

        for symbol in TEST_SYMBOLS:
            try:
                elapsed = switch_symbol(page, symbol)
                results.append((symbol, elapsed))
                print(f"  {symbol:<8} {elapsed:.3f}s")
            except Exception as e:
                print(f"  {symbol:<8} ERROR: {e!r}")
                results.append((symbol, None))
                # Cheap heuristic check for CAPTCHA/rate-limit pushback so
                # it's visible in the final report rather than silently
                # swallowed as a generic timeout.
                body_text = page.locator("body").inner_text(timeout=1000).lower()
                for marker in ("captcha", "too many requests", "rate limit", "unusual traffic"):
                    if marker in body_text:
                        pushback_observed.append((symbol, marker))

            time.sleep(POST_SUBMIT_PAUSE_SECONDS)

        print("\n" + "=" * 40)
        print("RESULTS")
        print("=" * 40)
        successful = [e for _, e in results if e is not None]
        for symbol, elapsed in results:
            status = f"{elapsed:.3f}s" if elapsed is not None else "FAILED"
            print(f"  {symbol:<8} {status}")

        if successful:
            print(f"\n  min={min(successful):.3f}s  max={max(successful):.3f}s  "
                  f"avg={sum(successful)/len(successful):.3f}s  n={len(successful)}")
        else:
            print("\n  No successful measurements.")

        if pushback_observed:
            print(f"\n  PUSHBACK OBSERVED: {pushback_observed}")
        else:
            print("\n  No CAPTCHA/rate-limit/pushback text detected during the test.")

        input("\nPress Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()
