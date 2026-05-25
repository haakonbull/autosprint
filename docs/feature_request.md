# Feature Requests

Ideas for things to add to autosprint. Not yet scheduled — captured here so they
aren't forgotten.

## Web-search-aware destination maintenance

Add a skill or agent that can perform web searches and use the results to update
or improve `destination.md`. The motivation: `destination.md` is the north star
the Plan phase reads on every sprint, but it currently only reflects what the
author already knows. A web-search-capable helper could pull in recent best
practices, library updates, or industry conventions and propose edits to the
destination — keeping the north star itself current without manual research.

Open questions:
- Should this run as part of the PIT loop (e.g. occasionally between sprints)
  or as a standalone command the human invokes?
- Should the helper edit `destination.md` directly and commit, or only propose
  diffs for human review?
- Which sources should it be allowed to fetch from?
