### Bug fixes

- A link of the AI's answer to a file or folder of the repository stays a link only when it names a ref that the checkout of the `apply` job has: `HEAD`, a branch or tag of the repository, or a commit of the default branch's history. A link to any other commit, such as one of a fork, now shows as code, as a link to another site does. The links of the assistant's own references, to the analyzed commit and to `HEAD`, are as before.
