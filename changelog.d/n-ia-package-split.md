### Changes

- The code of the action is now a package instead of one long script. Nothing changes for a repository that uses the action or its workflows. Whoever runs a command by hand writes `PYTHONPATH="$dir" python3 -P -m issue_assistant check` instead of `python3 "$dir/issue_assistant.py" check`, as the getting started guide shows.
