---
description: How a first analysis sets a priority, assigns the owners of an area and follows the kind with an issue type, each only where labels.toml asks for it.
---

# Priorities, owners and issue types

[← All features](README.md)

Three choices of `labels.toml` give an analysis more to set, each only where the repository makes it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/what-an-analysis-sets-besides-the-kind-the-areas-and-the-topics.dark.svg">
  <img src="../images/diagrams/what-an-analysis-sets-besides-the-kind-the-areas-and-the-topics.svg" alt="What an analysis sets besides the kind, the areas and the topics: Each of the three is a choice of the repository in labels.toml. With labels of the group priority, the AI chooses one, or none when the issue shows too little; an issue without a priority gets it with its reason, and a priority that the issue has stays. A kind with an issue_type gives an issue without a type that type, and corrects the type of another kind while the issue needs triage; a type that no kind has, such as Epic, stays. Owners of an area are assigned to an issue of the area that nobody is assigned to, when it opens with the area from its form and after its first analysis, and GitHub notifies them; the assistant mentions nobody.">
</picture>

- **A priority** comes from the labels of the group `priority`, such as `priority: high`. The AI chooses the one whose description fits what the issue shows, or none when it shows too little, and its reason shows on the timeline, as that of the other labels. The assistant gives a priority only to an issue without one, also in a follow-up that brought what was missing, and never changes one or adds back one that someone removed. A possible security report gets none, so that no public label judges the vulnerability; the maintainers set its priority. Without such labels, the AI isn't asked for one.
- **The owners of an area** are assigned to its issues: `owners = ["login"]` of an area label assigns those people to a new issue whose form chose the area, and, after the first analysis, to an issue of the areas that the analysis added, as long as nobody is assigned to it and nobody unassigned anyone from it. GitHub notifies them; no comment mentions them. With `exempt_assignees = true`, the sweep leaves those issues to them, as every assigned one.
- **The issue types of an organization** follow the kind: `issue_type = "Bug"` of a kind gives its issues that type when they have none, and while an issue needs triage, its type follows a corrected kind, as its label does: to the type of that kind, or to none for a kind without a type. A type that no kind has, such as `Epic`, stays, and a repository of a person, which has no issue types, keeps the labels alone.

The AI names neither people nor types: they come from `labels.toml`, by the areas and the kind that the schema of its answer allows, and its priority is one of the labels of the group. `check` makes sure that only areas have owners, each a login of GitHub, and only kinds an issue type, as [the reference](../every-key.md#labelstoml) lists; the Labels workflow warns of an owner whom GitHub can't assign and of a type that the repository doesn't have.
