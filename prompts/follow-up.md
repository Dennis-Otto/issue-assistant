## Your task: follow up on the answer to issue #{issue}

You asked the reporter of issue #{issue} for information, and they answered. Read `{context}/issue.md` completely: your earlier comments are those whose heading names the issue assistant. `{context}/issues.jsonl` with the whole texts in `{context}/issues/`, `{context}/discussions.jsonl` and `{context}/labels.md` are there as before, without the older issues that the search of the first analysis found. The latest release is {release}.

1. Check what the answer settles: which of your questions it answers and what the new information shows. Investigate in the repository again where the answer points to something new.
2. Write the answer:
   - `reply`: thank the reporter in a few words, then say what their answer shows: an updated assessment, or that the maintainers now have what is needed. At most about 150 words. Don't repeat your earlier analysis.
   - `maintainer_summary`: for the maintainers, as the rules say: what the reporter's answer adds and the gist of your `reply` and `missing_information` in their language, or empty when `language` is theirs.
   - `missing_information`: only the questions that are still open and still block progress; empty when everything needed is there. Never ask again for what the reporter said they can't provide.
   - `kind`, `areas`, `topics` and `label_rationale`: corrected when the answer changes the picture, otherwise as before.{priority}
   - `references`, `sensitive_data` and `language`: as for the first analysis; `references` only for files that the new information makes relevant, `sensitive_data` only for what the new comments show.
