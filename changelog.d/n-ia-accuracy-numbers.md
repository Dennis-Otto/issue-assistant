### Features

- The page [How accurate is it?](https://dennis-otto.github.io/issue-assistant/accuracy.html) can now show real numbers. A new workflow of this repository, *Evaluate*, runs `evaluate` by hand on GitHub's runner with the egress firewall, and a job without an AI takes only the numbers of its report into a pull request, as a table for each repository with the day, the engine, the model and the counts. The new command `accuracy-page` writes them, and refuses a report that has anything else in their place.
