# Italian Vocabulary repository guidance

Follow the parent workspace `AGENTS.md` as well as this file.

## Documentation

- Keep `README.md` short and introductory because it is the GitHub landing page.
  Include the app's purpose, main features, basic requirements, and links to
  setup and detailed documentation. Avoid implementation details, investigation
  history, long command lists, and detailed validation results in the README.
- Put first-time installation instructions in `START_HERE.md`, detailed usage,
  data storage, backup, and developer validation information in `GUIDE.md`, and
  performance measurements in `PERFORMANCE.md`.
- When setup, usage, or behavior changes, update the relevant detailed guide.
  Update `README.md` only when its introduction, requirements, or links need
  to change. Read the relevant guide before changing the behavior it documents.
- Check relative documentation links after moving or reorganizing content.

## GitHub history

- The user has authorized pushing major changes to the public
  `kaliezo/Vocabulary` GitHub repository as part of normal project work.
- After implementing and validating a major feature, behavior change, or bundled
  data update, make a focused commit and push it to `origin/main` in the same
  task. Small edits may be grouped into a meaningful commit.
- The user explicitly authorized public visibility. Keep the repository public
  unless the user requests a visibility change. Never stage or push local profile
  databases, `profiles.json`, backups, evaluation logs, credentials, or other
  runtime data. Review the staged file list before each push.
- Preserve history. Do not force push, reset published commits, or rewrite the
  remote branch. Use a new commit to reverse a prior change when requested.
- Verify the remote branch contains the new commit and report the commit ID.
  If authentication, network access, or approval blocks the push, explain the
  blocker and state that the change is only local.
