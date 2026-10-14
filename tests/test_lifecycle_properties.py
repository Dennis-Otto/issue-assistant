"""The promises of the lifecycle, for any course of events.

The comments of the assistant promise a reporter whose answer it waits for a
reminder, a closing on the day that the reminder names, and a reopening by an
answer. A state machine of Hypothesis plays the life of one issue on the fake of
GitHub, in any order: time passes with the sweep of every morning, which starts
some minutes late, and a sweep by hand now and then; the reporter answers in a
comment or by an edit of the text, or only changes the title, and the event job
sometimes misses it; a maintainer comments, also while the engine writes a
follow-up. After every step, the checks below hold, each in its own words rather
than the code's.
"""

import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

import hypothesis
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

import issue_assistant as assistant
from support import (
    BUG_BODY,
    CONFIG,
    REF,
    FakeGitHub,
    make_answer,
    make_comment,
    make_follow_up,
    make_issue,
)

REPORTER = "reporter"
OPENED = datetime(2026, 9, 1, 6, 50, tzinfo=UTC)
# The schedule of issue-lifecycle.yml, which GitHub starts up to an hour late.
MORNING = time(6, 41)
# The comments of the machine, whose numbers no comment of the fake reaches.
FIRST_ID = 10**6


def day_of(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def days(start: datetime, end: datetime) -> int:
    """The days of the calendar from one moment to the other, in UTC."""
    return (day_of(end) - day_of(start)).days


def payload(action, issue, sender, **more):
    return {
        "action": action,
        "issue": issue,
        "sender": {"login": sender, "type": "User"},
        **more,
    }


class Lifecycle(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        opened = assistant.stamp(OPENED)
        issue = make_issue(labels=("bug",), created_at=opened, updated_at=opened)
        self.github = FakeGitHub(CONFIG, issue)
        self.github.clock = OPENED
        # The reporter's edits of the text while the issue is open; the fake keeps
        # only the last one, as GitHub's lastEditedAt does.
        self.edits: list[datetime] = []
        self.ids = FIRST_ID

    # What happens

    @initialize(asks=st.booleans())
    def the_analysis(self, asks):
        missing = ["Which version?"] if asks else []
        answer = make_answer(areas=[], missing_information=missing)
        assistant.apply_answer(
            self.github, "triage", 12, json.dumps(answer), CONFIG, REF
        )

    @rule(
        days=st.integers(min_value=0, max_value=20),
        hours=st.integers(min_value=0, max_value=23),
        late=st.integers(min_value=0, max_value=59),
    )
    def time_passes(self, days, hours, late):
        self.wait(timedelta(days=days, hours=hours, minutes=1), late)

    @rule()
    def a_sweep_by_hand(self):
        self.sweep(self.github.clock)

    @rule(missed=st.booleans(), data=st.data())
    def the_reporter_comments(self, missed, data):
        self.wait(timedelta(minutes=1))
        closed = self.issue["state"] == "closed"
        comment = self.write(REPORTER, "Here is the log.")
        if missed:
            # The event job didn't run, for example while it was switched off.
            return
        plan = assistant.handle_event(
            self.github,
            "issue_comment",
            payload("created", self.github.issue(12), REPORTER, comment=comment),
            CONFIG,
        )
        if closed:
            # Only the sweep closes the issue here: "as soon as you answer here,
            # it reopens automatically".
            assert self.issue["state"] == "open"
        self.follow_up(plan, data)

    @rule(
        part=st.sampled_from(["title", "text", "both"]),
        missed=st.booleans(),
        data=st.data(),
    )
    def the_reporter_edits(self, part, missed, data):
        self.wait(timedelta(minutes=1))
        before = assistant.label_names(self.issue)
        changes = {}
        if part != "text":
            changes["title"] = f"The live card stays empty ({len(self.github.calls)})"
        if part != "title":
            changes["body"] = f"{BUG_BODY}\nVersion 1.9.{len(self.github.calls)}"
            if self.issue["state"] == "open":
                self.edits.append(self.github.clock)
        changed = self.github.edit(12, REPORTER, **changes)
        if missed:
            return
        plan = assistant.handle_event(
            self.github,
            "issues",
            payload("edited", self.github.issue(12), REPORTER, changes=changed),
            CONFIG,
        )
        if part == "title":
            # A new title answers nothing: the issue goes on as it was.
            assert plan.mode == "none"
            assert assistant.label_names(self.issue) == before
        self.follow_up(plan, data)

    @precondition(lambda self: self.issue["state"] == "open")
    @rule(data=st.data())
    def a_maintainer_comments(self, data):
        self.wait(timedelta(minutes=1))
        self.maintainer(
            self.write("maintainer", "Can you send the log?", "OWNER"), data
        )

    # How it happens

    @property
    def issue(self):
        return self.github.store[12]

    @property
    def comments(self):
        return self.github.comment_store.get(12, [])

    def wait(self, period, late=0):
        """Let the period pass, with the sweep of every morning in it."""
        end = self.github.clock + period
        while True:
            morning = datetime.combine(day_of(self.github.clock), MORNING, UTC)
            if morning <= self.github.clock:
                morning += timedelta(days=1)
            if morning > end:
                break
            # How late GitHub starts the schedule changes from day to day.
            self.sweep(morning + timedelta(minutes=(late + 17 * morning.day) % 60))
        self.github.clock = max(self.github.clock, end)

    def sweep(self, start):
        # What the sweep writes comes some seconds after its start.
        self.github.clock = start + timedelta(seconds=20)
        assistant.sweep(self.github, start, releases=False)

    def write(self, author, body, association="NONE"):
        self.ids += 1
        comment = make_comment(self.ids, author, body, 0, association)
        comment["created_at"] = self.github.time()
        self.github.comment_store.setdefault(12, []).append(comment)
        return comment

    def follow_up(self, plan, data):
        """The run of the engine that the event job started, which takes a minute
        or two, in which a maintainer may join the conversation."""
        if plan.mode != "follow-up":
            return
        self.wait(timedelta(minutes=1))
        joined = None
        if data.draw(st.booleans(), label="a maintainer joins while the engine writes"):
            joined = self.write("maintainer", "Thanks, I'll look into it.", "OWNER")
        self.wait(timedelta(minutes=1))
        asks = data.draw(st.booleans(), label="the follow-up asks again")
        answer = make_follow_up(missing_information=["And the log?"] if asks else [])
        assistant.apply_answer(
            self.github, "follow-up", 12, json.dumps(answer), CONFIG, REF
        )
        if joined:
            # The event of the maintainer's comment waited for this run.
            self.maintainer(joined, data)

    def maintainer(self, comment, data):
        plan = assistant.handle_event(
            self.github,
            "issue_comment",
            payload("created", self.github.issue(12), "maintainer", comment=comment),
            CONFIG,
        )
        if plan.mode == "maintainer-reply":
            waits = data.draw(st.booleans(), label="the maintainer asked the reporter")
            reply = {"waiting_for_reporter": waits, "reason": "Asked for the log."}
            assistant.apply_answer(
                self.github, "maintainer-reply", 12, json.dumps(reply), CONFIG, REF
            )

    # What the comments promise

    def of_kind(self, kind):
        return [
            comment for comment in self.comments if assistant.kind_of(comment) == kind
        ]

    def questions(self):
        """The moments that start a waiting: every analysis and follow-up of the
        assistant, every comment of a maintainer, and the label needs-info."""
        labeled = [
            assistant.created(event)
            for event in self.github.event_store.get(12, [])
            if event["event"] == "labeled" and event["label"]["name"] == "needs-info"
        ]
        return labeled + [
            assistant.created(comment)
            for comment in self.comments
            if assistant.kind_of(comment) in {"analysis", "follow-up"}
            or comment["author_association"] == "OWNER"
        ]

    def answers(self):
        """The reporter's comments, and edits of the text while the issue was open."""
        return self.edits + [
            assistant.created(comment)
            for comment in self.comments
            if comment["user"]["login"] == REPORTER
        ]

    def asked_before(self, moment):
        return max(question for question in self.questions() if question < moment)

    def answered_between(self, start, end):
        return any(start < answer < end for answer in self.answers())

    @invariant()
    def a_stale_issue_waits_for_its_reporter(self):
        labels = assistant.label_names(self.issue)
        assert "stale" not in labels or "needs-info" in labels

    @invariant()
    def one_reminder_per_question_and_none_after_an_answer(self):
        reminders = [assistant.created(item) for item in self.of_kind("reminder")]
        asked = [self.asked_before(reminder) for reminder in reminders]
        assert max(Counter(asked).values(), default=0) <= 1
        for question, reminder in zip(asked, reminders, strict=True):
            assert days(question, reminder) >= 15
            assert not self.answered_between(question, reminder)

    @invariant()
    def a_closing_comes_on_the_named_day_and_never_after_an_answer(self):
        for closing in map(assistant.created, self.of_kind("closed-unanswered")):
            question = self.asked_before(closing)
            assert not self.answered_between(question, closing)
            assert days(question, closing) >= 30
            reminders = [
                comment
                for comment in reversed(self.of_kind("reminder"))
                if question < assistant.created(comment) < closing
            ]
            assert reminders, "closed without a reminder"
            reminder = reminders[0]
            named = date.fromisoformat(assistant.marker(reminder)[1]["closes"])
            moment = datetime.combine(named, MORNING, UTC)
            assert assistant.format_date(moment, "en") in reminder["body"]
            assert day_of(closing) >= named

    @invariant()
    def an_unanswered_issue_closes_on_the_named_day_at_the_latest(self):
        """With a sweep every morning, the day that a reminder names is day 30, and
        its sweep closes the issue."""
        if self.issue["state"] != "open":
            return
        if "needs-info" not in assistant.label_names(self.issue):
            return
        question = max(self.questions())
        if any(answer > question for answer in self.answers()):
            return
        assert days(question, self.github.clock) <= 30

    @invariant()
    def a_follow_up_stays_out_of_a_conversation_that_a_maintainer_joined(self):
        asks = [
            assistant.created(comment)
            for comment in self.comments
            if assistant.kind_of(comment) in {"analysis", "follow-up"}
        ]
        follow_ups = [assistant.created(item) for item in self.of_kind("follow-up")]
        assert len(follow_ups) <= assistant.DEFAULT_LIFECYCLE.max_follow_ups
        maintainers = [
            assistant.created(comment)
            for comment in self.comments
            if comment["author_association"] == "OWNER"
        ]
        for posted in follow_ups:
            before = max(ask for ask in asks if ask < posted)
            assert not any(before < joined < posted for joined in maintainers)


@pytest.mark.hypothesis
def test_the_lifecycle_keeps_its_promises(monkeypatch):
    # The follow-ups name no file, so the files of the checkout don't matter.
    monkeypatch.setattr(assistant, "tracked_files", lambda root: set())
    run_state_machine_as_test(
        Lifecycle,
        settings=hypothesis.settings(
            stateful_step_count=40,
            suppress_health_check=[hypothesis.HealthCheck.too_slow],
        ),
    )
