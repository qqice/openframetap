"""Fixed event vocabulary and experiment guidance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventDefinition:
    key: str
    event_code: str
    event_name: str
    phase: str


EVENT_DEFINITIONS = {
    "0": EventDefinition("0", "baseline_static", "baseline_static_start", "baseline"),
    "1": EventDefinition("1", "yaw_left", "yaw_left_start", "yaw"),
    "2": EventDefinition("2", "yaw_right", "yaw_right_start", "yaw"),
    "3": EventDefinition("3", "pitch_up", "pitch_up_start", "pitch"),
    "4": EventDefinition("4", "pitch_down", "pitch_down_start", "pitch"),
    "5": EventDefinition("5", "roll_clockwise", "roll_clockwise_start", "roll"),
    "6": EventDefinition(
        "6", "roll_counter_clockwise", "roll_counter_clockwise_start", "roll"
    ),
    "7": EventDefinition("7", "return_neutral", "return_to_neutral", "recovery"),
    "8": EventDefinition("8", "local_recenter", "local_recenter_action", "local_action"),
    "s": EventDefinition("s", "stable", "stable_interval", "stable"),
    "e": EventDefinition("e", "end_action", "end_current_action", "end"),
}


KEY_HELP = """Pocket 3 passive telemetry experiment
No FFF5 writes will be performed.

Keys:
0  baseline/static
1  body yaw left start
2  body yaw right start
3  body pitch up start
4  body pitch down start
5  body roll clockwise start
6  body roll counter-clockwise start
7  return to neutral
8  local recenter action
9  record displayed battery
m  mark custom event
s  mark stable interval
e  end current action
q  stop and save
"""


PROCEDURE_GUIDE = """Recommended sequence (all movement is performed manually by the owner):
A. Place the Pocket on a stable surface. Press 0, wait >=10 s, press s,
   wait >=10 s, then press e.
B. Press 1, slowly yaw the body left 30-45 degrees, press s while holding,
   press 7 before returning, press s at neutral, then e. Repeat with key 2.
C. Repeat the same start/hold/return markers with keys 3 and 4 for pitch.
D. Repeat with keys 5 and 6 for small clockwise/counter-clockwise roll.
E. Optionally press 8 immediately before a safe recenter action performed on
   the Pocket itself. The program never sends that action.
F. Press 9 near the beginning and end to enter the displayed battery percent.
Press m to annotate a skipped or unusual step. Press q to stop and save.
"""
