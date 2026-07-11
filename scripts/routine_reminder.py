"""Local, DST-safe reminder to run the trading routines by hand.

Windows Task Scheduler runs this every 15 min on weekdays. On each tick it
computes the *US/Eastern* time (reusing the routines' own holiday/DST-aware
helpers), decides whether a routine slot is due within a tolerance window, and
if so pops a Windows notification naming the skill to run. It NEVER connects to
IBKR, reads positions, or places/modifies orders — it only reminds the human,
which keeps the human-in-the-loop design intact.

Anchoring to US/Eastern (not a fixed CET wall-clock) means the reminders stay
correct year-round even though US and EU DST shift on different dates.
"""

import datetime
import subprocess
import sys
from pathlib import Path

import pytz

# Make lib importable when run from Task Scheduler (cwd is arbitrary).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.realtime import is_market_holiday  # noqa: E402

TOLERANCE_MIN = 8  # a slot fires if |now - slot| <= this (task runs every 15m)

# (hour, minute, skill, one-line what-to-do), all in US/Eastern.
SLOTS = [
    (7, 30, "/premarket-research", "Screen watchlist + market; rank candidates."),
    (9, 30, "/market-open", "Review holdings/orders, validate candidates, build entries."),
    (10, 30, "/exit-scan", "Ratchet trailing stops; check exits."),
    (11, 30, "/exit-scan", "Ratchet trailing stops; check exits."),
    (12, 30, "/exit-scan", "Ratchet trailing stops; check exits."),
    (13, 30, "/exit-scan", "Ratchet trailing stops; check exits."),
    (14, 30, "/exit-scan", "Ratchet trailing stops; check exits."),
    (15, 30, "/exit-scan", "Last exit scan before close."),
    (16, 15, "/daily-summary", "Log P&L, what opened/closed, render EOD charts."),
]
# Friday after close also gets the weekly review.
FRIDAY_EXTRA = (16, 20, "/weekly-review", "Aggregate the week; propose next-week tweaks.")

LOG = ROOT / "output" / "reminders.log"


def _due_slot(now_et):
    slots = list(SLOTS)
    if now_et.weekday() == 4:  # Friday
        slots.append(FRIDAY_EXTRA)
    now_min = now_et.hour * 60 + now_et.minute
    for h, m, skill, note in slots:
        if abs(now_min - (h * 60 + m)) <= TOLERANCE_MIN:
            return skill, note
    return None


def _notify(title, body):
    """Native Win11 toast; falls back to a message box if toast fails."""
    ps = f"""
$ErrorActionPreference='Stop'
try {{
  [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null
  $t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $n=$t.GetElementsByTagName('text')
  $n.Item(0).AppendChild($t.CreateTextNode({title!r}))|Out-Null
  $n.Item(1).AppendChild($t.CreateTextNode({body!r}))|Out-Null
  $toast=[Windows.UI.Notifications.ToastNotification]::new($t)
  $notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Claude.TradingRoutine')
  $notifier.Show($toast)
}} catch {{
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show({body!r},{title!r},'OK','Information')|Out-Null
}}
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
    )


def main():
    now_et = datetime.datetime.now(pytz.timezone("US/Eastern"))
    if now_et.weekday() >= 5 or is_market_holiday(now_et.date()):
        return  # weekend / holiday: silent
    due = _due_slot(now_et)
    if not due:
        return
    skill, note = due
    title = f"Trading routine due: {skill}"
    body = f"{note}\n(ET {now_et:%H:%M} · run the skill in Claude Code)"
    _notify(title, body)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{now_et:%Y-%m-%d %H:%M %Z}\t{skill}\n")


if __name__ == "__main__":
    main()
