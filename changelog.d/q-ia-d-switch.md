### Features

- An experimental input `reading` lets a run by hand read the texts of an issue as GitHub renders them: with `reading: rendered`, the action, the reusable workflow and Evaluate read GitHub's rendering of each text in a second step and give the AI the Markdown of what GitHub shows, and the source of each text without one. The default, `cleaned`, reads every text as before and asks GitHub for no rendering, so nothing changes unless you set it.
