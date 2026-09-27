"""Chibi Emoji bot — entry point.  Run:  python bot.py

Deliberately tiny: render processes are started with "spawn", and each of them re-imports
this file. Keeping aiogram and the handlers out of it saves ~100 MB per render process.
"""

if __name__ == "__main__":
    from chibibot.main import run

    run()
