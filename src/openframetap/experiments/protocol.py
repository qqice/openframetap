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
r  ready; start the timed procedure
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


PROCEDURE_GUIDE = """Exact body-motion timeline after key r (about 155 seconds):
00:02  press 9; enter the displayed battery percent and press Enter
00:07  press 0; leave the complete Pocket body stationary for 10 seconds
00:17  press s; remain stationary for another 10 seconds
00:27  press e
00:30  press 1; rotate the complete body left over 5 seconds
00:35  press s; hold the left-yaw pose for 5 seconds
00:40  press 7; return to neutral over 5 seconds
00:45  press s; hold neutral for 3 seconds; at 00:48 press e
00:50  press 2; repeat the same 5/5/5/3-second pattern for right yaw
01:10  press 3; repeat the same pattern for body pitch up
01:30  press 4; repeat the same pattern for body pitch down
01:50  press 5; repeat the same pattern for small clockwise body roll
02:10  press 6; repeat the same pattern for small counter-clockwise body roll
02:30  press 9; enter the displayed battery percent and press Enter
02:35  press q to stop, save, disconnect, and checksum the evidence

For every motion: start key -> move 5 s -> s -> hold 5 s -> 7 -> return 5 s
-> s -> neutral 3 s -> e. Move the complete Pocket body; do not use the
joystick, touch-screen rotation mode, key 8, or direct force on the gimbal head.
The 240-second limit starts only after key r and leaves about 85 seconds of margin.
"""
