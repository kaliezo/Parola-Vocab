# Italian Vocabulary

A personal desktop app for collecting, classifying, and studying Italian words. The library and flashcards work offline. AI evaluation uses the official Codex CLI with your ChatGPT login. It runs when you click **Evaluate pending words** or change the learner level while previously AI-evaluated entries exist.

On a clean install, the first launch asks for a profile name and whether to start with the bundled study-ready deck or an empty library. Profiles are local to each computer and have separate words, settings, and study history.

The interface uses a built-in near-black theme with no extra theme package. The compact quick-add field appears only in Library, above the difficulty counts. The detail editor stays hidden until you select a word.

## Set up and run on Fedora

Python 3, Tkinter, and SQLite are required. This project uses only the Python standard library. Check Tkinter with:

```bash
python3 -c 'import tkinter; print(tkinter.TkVersion)'
```

If Tkinter is missing, install Fedora's package yourself:

```bash
sudo dnf install python3-tkinter
```

From this project directory, launch with:

```bash
./run.sh
```

Or run `python3 app.py`. The launcher resolves its own directory, so the workspace path may contain spaces. The application does not need a virtual environment or an API key. For AI evaluation, install the [official Codex CLI](https://learn.chatgpt.com/docs/codex/cli) if it is not already installed. Its Linux installer command is:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Then check `codex --version` and `codex login status`. If you are not signed in, run `codex login` and complete the [official browser flow](https://learn.chatgpt.com/docs/auth). The app never asks for your ChatGPT password.

To make the app searchable as **Italian Vocabulary** in the KDE Plasma or GNOME application menu, run:

```bash
python3 create_desktop_launcher.py --install
```

This writes `italian-vocabulary.desktop` to `$XDG_DATA_HOME/applications` when `XDG_DATA_HOME` is an absolute path, or to `~/.local/share/applications` otherwise. It does not require administrator privileges or change system-wide launchers. It also generates `Italian Vocabulary.desktop` inside this project. If the project is moved, run the command again with `--replace` to update the installed path. The script refuses to replace a different existing launcher unless you supply that flag. KDE may need `kbuildsycoca6` or a logout and login before its search index refreshes.

## Profiles

The first launch asks you to name a profile. **Use the default deck** adds 7,695 study-ready cards, with definitions, English glosses, examples, difficulty ratings, source tags, and A2 learner settings. The bundled deck contains no personal notes, study history, or evaluation request logs. **Start from zero** creates an empty library with the same initial evaluation settings. You can add words in Library at any time.

Click the **Profile** button at the top of the app to switch profiles or create another one. Finish or cancel an evaluation before switching. Each profile has its own SQLite database under the app data directory's `profiles/` folder; `profiles.json` stores their names and the selected profile. Neither file belongs in Git. An existing pre-profile `vocabulary.sqlite3` is copied to a local **Existing Library** profile on the first launch after upgrading. The original database is kept as a fallback.

## Use the library

Type a word or short expression in the top field and press Enter. It is saved immediately as **Unknown**, the field clears, and focus stays there for the next word. Entries are limited to 120 characters. Surrounding whitespace is removed, internal whitespace is collapsed, and Unicode is normalized to NFC. Duplicate detection ignores case but preserves accents, so `si` and `sì` are different entries.

Use the compact search field and toggle any difficulty or source-level filters. The Learning content column marks classified entries as Ready or Incomplete and flags those due for re-evaluation. Select an entry to edit its text, optional context sentence, notes, difficulty, definition, gloss, example, or difficulty reason. Save changes with **Save changes**. Changing its text or context resets it to Unknown and clears old classification content. Manual changes to difficulty or learning content take precedence over an old AI result. Notes and study counts remain. Delete requires confirmation.

### A1-B2 starter collection

Click **Add A1-B2 starter collection** in the Library tab to merge the bundled text-only collection into your database. The app asks before adding it; it does not fill your database on startup. It adds 7,695 unique entries: 7,473 words, 126 practical phrases, 76 idioms, and 20 proverbs. Source-level counts are A1: 542, A2: 1,038, B1: 2,058, B2: 4,057. Existing entries are skipped by the same accent-preserving duplicate key used for quick add. Their text, notes, classifications, and study history are not changed. Importing again safely skips entries still present. Deleted entries can be restored by explicitly importing again.

The **CEFR** column and A1-B2 filters show the collection's source tags. **Personal** shows entries without a starter tag. These tags indicate a suggested learning stage and are independent of the app's Easy, Medium, and Hard difficulty estimates relative to your learner profile. All newly imported entries start as **Unknown**. The source data does not provide reliable definitions and examples for most words, so imported entries become study-ready only after you request AI evaluation or add learning content and difficulty yourself. If more than 100 entries are pending, the app asks how many to evaluate in that run, defaulting to 50; it never starts thousands of Codex jobs from one unqualified click.

The word list is derived from the [unofficial community CEFR vocabulary dataset](https://github.com/Talhakasikci/cefr-vocabulary-dataset), whose maintainer permits personal and educational use and warns that levels, spellings, and coverage may contain errors. We removed five abbreviations and kept the source's word-level tags; this is a substantial starting collection, not every Italian word or an official CEFR syllabus. The expression list is editorially selected from common Italian usage. [Wiktionary's Italian phrase](https://en.wiktionary.org/wiki/Category:Italian_phrases), [idiom](https://en.wiktionary.org/wiki/Category:Italian_idioms), and [proverb](https://en.wiktionary.org/wiki/Category:Italian_proverbs) categories were consulted while assembling it; individual entries were not systematically verified against those sites. Its level tags are rough estimates. No definitions, examples, or other explanatory text were copied from those sites. The bundled source files are `starter_words.tsv` and `starter_expressions.tsv`; `starter.py` validates every row and rejects duplicates before import.

Difficulty estimates how challenging a meaning is for the learner profile in Settings, which defaults to B1. **Unknown means no difficulty has been assigned**. It does not mean you failed to remember the word. Remembered and Again are separate study outcomes and never change difficulty.

## Evaluate with Codex

The defaults are model `gpt-6-sol`, reasoning effort `high`, learner level `B1`, and a 900-second timeout per batch. Settings can be changed and saved in the app. If your account cannot access the selected model, the app reports the error and lets you choose another model. It never silently substitutes one.

Selecting a different **Learner level** saves it immediately and starts re-evaluating every entry that still has an AI result, including entries marked for review. If an evaluation is already running, the app cancels that job and queues the new-level work. Manually classified entries are excluded. Current difficulties and learning content remain available until a valid replacement arrives. Pending re-evaluations survive restarts; if Codex is unavailable, the request fails, or you cancel, click **Evaluate pending words** later to retry. A new `needs_review` result returns that entry to Unknown and clears its obsolete learning content.

Click **Evaluate pending words** to capture current unevaluated Unknown entries and any entries awaiting a level recheck. The app groups entries into batches of up to 20 and runs up to five `codex exec` jobs at once. For example, 50 entries use three jobs with 20, 20, and 10 entries. Each job uses the current ChatGPT login, a private temporary directory, and a read-only sandbox. Jobs may finish in a different order from the word list; progress counts completed batches, and each valid batch is saved as it finishes. You can cancel; saved results remain and unfinished entries stay queued. Entries added after the snapshot are left for the next run. Edited, deleted, or manually classified entries cannot be overwritten by a late response. Responses for an older learner level are also ignored. Running several jobs may encounter temporary throttling or account usage limits; a failed run keeps results already saved, and remaining entries can be retried later.

The app validates the final JSON against the tracked request before updating SQLite. Unclear, misspelled, or ambiguous entries may receive a review note and stay Unknown. You can add context and evaluate them again. Classification uses the ChatGPT account's Codex allowance and may be subject to usage limits. It does not use separately billed API credits.

An entry that receives a needs-review result is excluded from ordinary **Evaluate pending words** runs, so repeated clicks do not spend allowance on the same unresolved entry. Use **Show needs review** in Library to find these entries and read their notes. Adding context or changing the entry makes it eligible for ordinary evaluation again. **Retry reviewed with Codex** is an explicit option for trying existing review entries without edits; its dialog previews the entries, asks how many to retry, and warns that the result may still need context. The classifier now chooses one common meaning for ordinary words with several meanings when no context is provided. Single letters, unclear spellings, and entries without a reliable Italian meaning still need correction or removal rather than unchanged retries.

If automatic evaluation is unavailable, use **Export prompt**. It creates a new folder inside the location you choose and saves pending entries in batches of up to 20 as `prompt-01.txt`, `request-01.json`, and so on, plus a schema. Give each exact prompt to another assistant, save each JSON response, then use **Import AI JSON**. **Copy prompt** copies a tracked prompt for the first 20 pending entries, including the schema and request ID. Manual results go through the same validator as automatic results. The tracked request stays in the database and is included in backups.

For Codex troubleshooting, run `codex login status` in a terminal. If login expired, run `codex login` again. Check connectivity and Codex account limits if a request fails. Increase the timeout in Settings for slow batches. The app displays the relevant error and does not fabricate classifications.

## Study

In the Study tab, check any nonempty combination of Easy, Medium, and Hard, then choose a topic and word level from the dropdowns. **All topics** and **All levels** include the whole library; a specific topic or level limits both the displayed count and the session to matching cards. The word level is the card's estimated source CEFR level, not your learner level in Settings or its Easy/Medium/Hard difficulty. Choosing A1, A2, B1, or B2 matches that exact level. **Personal** includes cards added outside the starter collection; they have no source level. Topics are inferred locally from each card's English gloss and Italian definition. A card can appear in more than one topic, and cards without a specific match appear under **General vocabulary**. The topic is also shown in Library entry details. Changing a card's meaning can change its topic automatically. This uses no Codex evaluation or network request.

The app shows how many matching entries have a definition, English gloss, and Italian example. Unknown and incomplete entries are excluded. Choose 10, 20, or all matching entries, then start. Each chosen card appears once in the first round.

If you chose the default deck at profile creation, all 7,695 cards are ready to study. If you imported the text-only starter collection into an empty profile, those 7,695 entries are Unknown until evaluated. The Study tab shows the Unknown count and has an **Evaluate pending words** button. It uses your Codex account to add difficulty and learning content; when many entries are pending, you can choose a small run such as 20. You can also prepare individual cards in Library by assigning a difficulty and filling in the definition, English gloss, and Italian example. The Study count updates as cards become ready.

Sessions select cards with weighted randomness. The app estimates recall from your saved Remembered and Again totals, with a neutral starting estimate for new words. It gives extra weight to the five most recent first-round answers, so a recent lapse matters even after many successes. Lower estimated recall makes a word more likely to enter a 10- or 20-card session. Every eligible word retains a chance, and **All** includes every eligible word while putting lower-scoring words earlier on average. This is a simple study priority estimate, not a timed spaced-repetition schedule. The library shows a New, Needs practice, Building, or Strong label; entry details show both your recorded success rate and estimated recall.

Only the Italian entry is visible initially. Click **Reveal** or press Space to see the answer. Then choose **Remembered** (`R`) or **Again** (`A`). At the end, you can start another round with the Again words. Answers in that extra round count toward your overall attempts, but only first-round answers are used for the recent-session signal. Study runs entirely from local data and makes no AI request. Existing study totals from before this update remain available; new review history starts accumulating with your next answer.

## Data and backups

Profile databases are stored at `$XDG_DATA_HOME/italian_vocabulary/profiles/` when `XDG_DATA_HOME` is an absolute path. Otherwise, they are stored at `~/.local/share/italian_vocabulary/profiles/`. Existing installations keep the former `vocabulary.sqlite3` file after it is copied to a profile. Runtime data is never placed in the source directory or included in the repository.

Use **Export backup** to write a versioned JSON backup to a location you choose. Version 4 includes words, classification content, notes, study counts and individual review history, starter source-level tags, tracked evaluation requests, settings, and pending level rechecks. **Import backup** accepts versions 1, 2, 3, and 4, validates the whole file first, then merges it in a transaction. Existing entries and settings are preserved; the app reports added, skipped, and conflicting entries. An empty database also restores saved settings. If the imported learner level differs from the current one, imported AI-evaluated entries are queued for re-evaluation. When importing into an empty database, stable IDs and tracked requests are restored. If an imported ID collides with an existing different entry, the imported entry receives a free ID; affected evaluation requests are skipped because old response IDs would be unsafe.

The classifier policy is in `classifier_prompt.txt`, and the strict output format is in `result_schema.json`. The app, rather than the AI agent, validates results and writes the database.

## Developer validation

From this directory:

```bash
python3 -m py_compile app.py classifier.py storage.py study.py starter.py create_desktop_launcher.py tests/test_vocabulary.py
python3 -m unittest discover -s tests -v
```

Tests use temporary SQLite databases and a fake Codex executable. They do not open the GUI or put demonstration words into your real database.
