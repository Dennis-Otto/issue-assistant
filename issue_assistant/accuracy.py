"""The accuracy page of the website, from a report of `evaluate`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

from .cleaning import code_span
from .constants import REPOSITORY_NAME
from .digests import share
from .engines import ENGINES
from .errors import AssistantError
from .evaluation import MEASURES, MEASURES_HEAD

# The numbers of the accuracy page, from a report of `evaluate`. The job publish of
# .github/workflows/evaluate.yml takes a report that a job with an AI wrote into a job
# that writes, so the report counts as untrusted: only what passes the checks below
# reaches the page, which are numbers and the names of a measure, the engine, the model
# and the repository. The lines of the issues stay in the report.
ACCURACY_PAGE = PurePosixPath("docs/accuracy.md")
ACCURACY_BLOCK = re.compile(
    r"^<!-- accuracy numbers: start -->\n"
    r"(?P<sections>.*?)"
    r"^<!-- accuracy numbers: end -->$",
    re.DOTALL | re.MULTILINE,
)
# What comes before the lines of the issues in a report, as render_evaluation writes
# it. A number has at most nine digits, which int() always takes.
REPORT = re.compile(
    r"# How accurate the issue assistant is on (?P<repository>\S+)\n"
    r"\n"
    r"Measured on (?P<day>\d{4}-\d{2}-\d{2}) with (?P<engine>[A-Za-z]+), "
    r"`(?P<model>[\w.:/@-]+)`, on the (?P<replayed>\d{1,9}) newest of "
    r"(?P<read>\d{1,9}) closed issues read [^\n]*\n"
    r"\n"
    + "".join(rf"{re.escape(line)}\n" for line in MEASURES_HEAD)
    + r"(?P<rows>(?:\|[^\n]*\n)+)"
    r"\n"
    r"\*Precision\*[^\n]*\n"
    r"(?:\n(?P<failed>\d{1,9}) of the \d{1,9} analyses failed; they count in no "
    r"measure\.\n)?"
    r"(?:\nThe analyses cost \$(?P<cost>\d{1,9}\.\d\d) at the prices of the API\.\n)?"
    r"\n## Issue by issue\n",
    re.ASCII,
)
REPORT_ROW = re.compile(
    r"\| (?P<measure>[^|]+) \| (?P<precision>[^|]+) \| (?P<recall>[^|]+) \| "
    r"(?P<issues>\d{1,9}) \|",
    re.ASCII,
)
REPORT_SHARE = re.compile(r"\d{1,3} % \((\d{1,9}) of (\d{1,9})\)", re.ASCII)
# The table of a repository on the page: three narrow columns, the issues of a measure
# after its name and each share as "90 % · 26/29", so that it fits the width of a
# phone without a sideways scroll. The report of `evaluate` keeps its four columns.
PAGE_HEAD = (
    "| Measure (issues) | Precision | Recall |",
    "| --- | ---: | ---: |",
)


@dataclass(frozen=True)
class Measurement:
    """The numbers of a report of `evaluate`, and what they were measured on."""

    repository: str
    day: date
    engine: str
    model: str
    replayed: int
    read: int
    # The lines of the table of measures.
    rows: tuple[str, ...]
    # How many analyses failed, and what they cost in dollars, if the report says.
    failed: int
    cost: str | None


def fits_share(cell: str) -> bool:
    """Whether a cell of the table of measures is what `share` writes for its numbers,
    with no more of the part than of the whole."""
    if cell == share(0, 0):
        return True
    found = REPORT_SHARE.fullmatch(cell)
    if found is None:
        return False
    part, whole = int(found[1]), int(found[2])
    return part <= whole and cell == share(part, whole)


def fits_row(line: str) -> bool:
    """Whether a line of the table of measures has a measure and its numbers."""
    row = REPORT_ROW.fullmatch(line)
    return (
        row is not None
        and row["measure"] in MEASURES
        and fits_share(row["precision"])
        and fits_share(row["recall"])
    )


def narrow_share(cell: str) -> str:
    """A share of the report, "90 % (26 of 29)", as the page writes it,
    "90 % · 26/29"; the dash of a share without a whole stays as it is."""
    return REPORT_SHARE.sub(
        lambda found: f"{found[0].partition(' (')[0]} · {found[1]}/{found[2]}", cell
    )


def narrow_row(line: str) -> str:
    """A line of the table of measures of the report, which fits_row accepted, as a
    line of the narrow table of the page."""
    measure, precision, recall, issues = (
        part.strip() for part in line.strip("|").split("|")
    )
    return (
        f"| {measure} ({issues}) | {narrow_share(precision)} | {narrow_share(recall)} |"
    )


def measurement_of(report_text: str) -> Measurement:
    """The numbers of a report of `evaluate`, or an error when anything about them
    isn't what `evaluate` writes."""
    found = REPORT.match(report_text)
    if found is None:
        raise AssistantError(
            "That is no report of evaluate: its head doesn't have the form of one."
        )
    rows = tuple(found["rows"].splitlines())
    bad = next((row for row in rows if not fits_row(row)), None)
    if bad is not None:
        raise AssistantError(
            f"That is no report of evaluate: the line {bad!r} of its table has no "
            "measure with its numbers."
        )
    if not REPOSITORY_NAME.fullmatch(found["repository"]):
        raise AssistantError(
            "That is no report of evaluate: it names no repository as owner/name."
        )
    if found["engine"] not in {engine.name for engine in ENGINES.values()}:
        raise AssistantError(
            f"That is no report of evaluate: it names the engine {found['engine']}."
        )
    try:
        day = date.fromisoformat(found["day"])
    except ValueError:
        raise AssistantError(
            f"That is no report of evaluate: {found['day']} is no day."
        ) from None
    failed, replayed = int(found["failed"] or 0), int(found["replayed"])
    if failed >= replayed:
        raise AssistantError(
            "That is no report of evaluate with numbers: no analysis gave an answer, "
            f"as {failed} of the {replayed} failed."
        )
    return Measurement(
        found["repository"],
        day,
        found["engine"],
        found["model"],
        replayed,
        int(found["read"]),
        rows,
        failed,
        found["cost"],
    )


def accuracy_section(found: Measurement) -> str:
    """The part of the accuracy page for one repository."""
    failed = (
        f" {found.failed} of the {found.replayed} analyses failed; they count in no "
        "measure."
        if found.failed
        else ""
    )
    cost = (
        f" The analyses cost ${found.cost} at the prices of the API."
        if found.cost
        else ""
    )
    return "\n".join(
        (
            f"### [{found.repository}](https://github.com/{found.repository})",
            "",
            f"Measured on {found.day.isoformat()} with {found.engine}, "
            f"{code_span(found.model)}, on the {found.replayed} newest of {found.read} "
            "closed issues read that people labelled or closed as a duplicate of an "
            f"older issue, and that the assistant never analyzed.{failed}{cost} Small "
            "numbers vary: with few issues, one more found or missed moves a share a "
            "lot, so each share names its counts, and each measure the issues that it "
            "rests on.",
            "",
            *PAGE_HEAD,
            *(narrow_row(row) for row in found.rows),
        )
    )


def page_with_numbers(page: str, found: Measurement) -> str:
    """The accuracy page with the numbers in its block: a part for each repository,
    in the order of their names, whose newest numbers replace its older ones. The text
    that stands there before the first numbers goes."""
    block = ACCURACY_BLOCK.search(page)
    if block is None:
        raise AssistantError(
            f"{ACCURACY_PAGE} has no block of numbers between the lines "
            "<!-- accuracy numbers: start --> and <!-- accuracy numbers: end -->."
        )
    kept = {
        section[len("### [") : section.index("]")]: section.strip()
        for section in re.split(r"(?m)^(?=### \[)", block["sections"])[1:]
    }
    kept[found.repository] = accuracy_section(found)
    sections = "\n\n".join(kept[name] for name in sorted(kept, key=str.casefold))
    start, end = block.span("sections")
    return f"{page[:start]}\n{sections}\n\n{page[end:]}"
