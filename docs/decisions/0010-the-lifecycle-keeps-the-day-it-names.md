# The lifecycle counts days of the calendar and keeps the day that a reminder names

- Status: accepted
- Date: 2026-10-14

## Context

An issue that waits for its reporter gets a reminder after 15 days, which says: "it closes automatically on" a date, 15 days later. The sweep that sends the reminder and the one that closes the issue run every morning on GitHub's schedule, which starts them some minutes late, and by a different number of minutes each day. The sweep closed an issue once 15 whole days had passed since the reminder, to the minute, so a sweep of the named day that started a few minutes earlier than the one of the reminder left the issue open for one more day: the comment promised one date, and the assistant kept another.

## Options

1. Count whole days to the minute, as before, and name the day after the 15th in the reminder. The promise holds, but the reporter gets a day more than the comment says the rule gives, and the date depends on the minute at which GitHub happened to start the sweep.
2. Count days of the calendar in UTC: a period ends on the day it ends on, whatever its minute, and the issue closes on the day that the reminder named.
3. Option 2, and record the named day in the hidden marker of the reminder, so that the sweep closes on that day without working it out again.

## Decision

Option 3. Reporters read days, not minutes: "after 15 days" is the 15th day, and the date in the reminder is the day it closes. Recorded in the marker, the day holds also for a reminder posted seconds before midnight, whose comment GitHub dates the day after, and for waiting periods that a later version or a setting changes: a reminder keeps the day it named. A reminder of an earlier version, whose marker names no day, closes on the 15th day after it. Only the assistant can change its comments, so nobody else can move the day.

## Consequences

A reminder or a closing can come some hours before 15 whole days have passed, on the right day. Every rule of the waiting for a reporter is a number of calendar days, which the settings of a later version can change without breaking a promise already made. The checks of the state machine in `tests/test_lifecycle_properties.py` hold the sweep to the day that each reminder names.
