"""Output helpers for runtime verbosity control."""

import contextlib
import sys
import textwrap
from datetime import UTC, datetime

from autosprint.config import SPEAK_LEVELS, config

CONSOLE_LOG_FILENAME = "autosprint/logs/console-verbose.log"
CONSOLE_ALL_LOG_FILENAME = "autosprint/logs/console-all.log"
_CONSOLE_LOG_SEPARATOR_WRITTEN = False
_CONSOLE_ALL_LOG_SEPARATOR_WRITTEN = False

# Reconfigure stdout/stderr to UTF-8 so emojis and Unicode symbols (✅ ❌ 🔨 …) don't crash
# on Windows consoles that default to cp1252. `errors="replace"` keeps us safe if a truly
# unencodable character sneaks in — it becomes '?' instead of raising UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        # reconfigure exists on the real console streams (TextIOWrapper) but not
        # on the abstract TextIO type, and may be absent under capture/redirect.
        _stream.reconfigure(encoding="utf-8", errors="replace")  # ty: ignore[unresolved-attribute]


def _wrap_line(line: str, max_width: int) -> str:
    """Wrap a single line to max_width, preserving the leading whitespace (spaces/tabs) on continuation lines so wrapped output stays visually aligned."""
    if len(line) <= max_width:
        return line
    stripped = line.lstrip(" \t")
    indent = line[: len(line) - len(stripped)]
    wrapper = textwrap.TextWrapper(width=max_width, initial_indent=indent, subsequent_indent=indent, break_long_words=False, break_on_hyphens=False)
    return wrapper.fill(stripped)


def wrap_message(message: str, max_width: int = 120) -> str:
    """Wrap every line of `message` to at most `max_width` characters, preserving each line's leading whitespace on its continuations. Pass max_width=0 to disable."""
    if max_width <= 0:
        return message
    return "\n".join(_wrap_line(line, max_width) for line in message.splitlines())


_CONSOLE_LOG_WARNED: bool = False
_CONSOLE_ALL_LOG_WARNED: bool = False


def _append_to_log_file(text: str, filename: str, separator_flag_attr: str, warned_flag_attr: str) -> None:
    """Shared writer for both console logs. Appends `text` to TARGET_REPO/<filename>, writes a '# === run started <ts> ===' separator on the first write of this process, and degrades silently after the first filesystem failure (with a one-time visible warning). Mutable state is held in module-level globals keyed by `separator_flag_attr` / `warned_flag_attr`."""
    if not config.SAVE_CONSOLE_LOG:
        return
    try:
        path = config.TARGET_REPO_PATH / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            if not globals()[separator_flag_attr]:
                ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                f.write(f"\n# === run started {ts} ===\n")
                globals()[separator_flag_attr] = True
            f.write(text + "\n")
    except Exception as e:
        if not globals()[warned_flag_attr]:
            print(f"[warning] Could not append to {filename}: {e}. Further writes this run will be silently skipped.")
            globals()[warned_flag_attr] = True


def _append_console_log(text: str) -> None:
    """Append `text` to TARGET_REPO/autosprint/logs/console-verbose.log — the *filtered* mirror of terminal output. Only called from `printlev` when a line passes the LOG_LEVEL filter."""
    _append_to_log_file(text, CONSOLE_LOG_FILENAME, "_CONSOLE_LOG_SEPARATOR_WRITTEN", "_CONSOLE_LOG_WARNED")


def _append_console_all_log(text: str) -> None:
    """Append `text` to TARGET_REPO/autosprint/logs/console-all.log — the *unfiltered* complete record of every printlev call, regardless of LOG_LEVEL. Use this file when the filtered console log doesn't show the detail you need (e.g. full team-lead prompts emitted at level=1)."""
    _append_to_log_file(text, CONSOLE_ALL_LOG_FILENAME, "_CONSOLE_ALL_LOG_SEPARATOR_WRITTEN", "_CONSOLE_ALL_LOG_WARNED")


def printlev(message: object, level: int = 50, max_width: int = 120) -> None:
    """Print a message when the configured log level allows it (lower configured values mean more verbose output). Each line of `message` is wrapped at `max_width`; continuation lines copy the first line's leading whitespace. Pass max_width=0 to disable wrapping. When config.SAVE_CONSOLE_LOG is True the wrapped text is appended to **both** `autosprint/logs/console-verbose.log` (filtered — only lines that passed the LOG_LEVEL filter) and `autosprint/logs/console-all.log` (unfiltered — every call regardless of level). The filtered log is grep-friendly for common searches (sprints, commits, failures); the unfiltered log is the complete record for deep debugging."""
    text = wrap_message(str(message), max_width=max_width)
    _append_console_all_log(text)
    if level >= config.LOG_LEVEL:
        print(text)
        _append_console_log(text)


_TTS_FAILED: bool = False


def speak_blocking(text: str) -> None:
    """Do the actual pyttsx3 call on the current thread. Intended for callers that already run in their own thread or process — e.g. the MCP server, or the daemon thread spawned by speak(). Runs in a background thread when called via speak() so Windows SAPI5 gets its own message pump — calling pyttsx3 directly from the asyncio event-loop thread silently drops audio on Windows because the COM dispatch has no pump to run on. On the *first* failure, prints a visible one-time warning so the user knows TTS is offline for this run."""
    global _TTS_FAILED
    try:
        import pyttsx3  # lazy — only loaded when audio is actually requested

        engine = pyttsx3.init()
        engine.setProperty("rate", config.TTS_RATE)
        if config.TTS_VOICE:
            needle = config.TTS_VOICE.lower()
            for voice in engine.getProperty("voices"):
                if needle in voice.id.lower() or needle in voice.name.lower():
                    engine.setProperty("voice", voice.id)
                    break
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        if not _TTS_FAILED:
            print(f"[warning] TTS engine failed: {e}. Audio notifications disabled for the rest of this run.")
        _TTS_FAILED = True


def speak_tier_enabled(tier: str, speak_level: str) -> bool:
    """True when a message of the given `tier` should be spoken at the configured `speak_level`.
    Pure: a message is spoken only when its tier rank does not exceed the configured level
    (off < run < reverts < sprints < all). `tier` is the message's own importance — messages
    are never tagged 'off'; that value exists only as a SPEAK_LEVEL meaning 'silent'."""
    return SPEAK_LEVELS.index(tier) <= SPEAK_LEVELS.index(speak_level)


def speak(text: str, tier: str = "run", wait: bool = False) -> None:
    """Speak `text` aloud via pyttsx3 when the message's `tier` is enabled by config.SPEAK_LEVEL (see `speak_tier_enabled`). Silent no-op otherwise. `tier` is the message's importance: 'run' (default — run-level events), 'reverts', 'sprints', or 'all' (sprint-start chatter). Runs in a daemon thread so (a) the caller isn't blocked waiting for audio and (b) Windows SAPI5 gets a thread with its own message pump — calling pyttsx3 directly from inside asyncio.run() on Windows makes audio silently drop. If `wait=True`, blocks until audio completes (or 15 s timeout) — use for the final exit announcement so the process doesn't terminate before the daemon thread finishes speaking. If the TTS engine is unavailable, falls back to silent after one attempt so the run is never blocked by an audio issue."""
    import threading

    if _TTS_FAILED or not speak_tier_enabled(tier, config.SPEAK_LEVEL):
        return
    t = threading.Thread(target=speak_blocking, args=(text,), daemon=True)
    t.start()
    if wait:
        t.join(timeout=15.0)
