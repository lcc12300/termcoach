# termcoach

**English** · [中文](README.zh-CN.md)

Record selected parts of a Bash session, review the transcript, and keep a
short list of habits to work on. You can review the transcript yourself, share
it with an assistant, or ask termcoach to send it to a model you configure.
Confirmed findings can appear as local reminders the next time you record.

Shell history shows what you ran, not how you typed it. The same
`gcc -Wall -Wextra main.c` line looks identical whether you tab-completed it or
backspaced 34 times and retyped it. termcoach records key tokens for the
segments you switch on, and reports them per channel:

```text
prompt  : 14 backspaces, longest run 3     ← command-line editing
program : 35 backspaces, longest run 34    ← the run was inside Vim
```

## Install

Run inside Linux or WSL with Bash and an interactive terminal. Native Windows
Python is unsupported. Python 3.9+ runs the recorder; use Python 3.11+ to read
the optional TOML configuration. There are no third-party Python dependencies.

```bash
git clone https://github.com/lcc12300/termcoach
cd termcoach
uv tool install .        # or: pipx install .

# Or run directly from the source tree
python3 -m termcoach rec
```

## Record a session

```console
$ termcoach rec
$ record on
$ vim hello.c
$ make
$ record off
$ exit
```

`termcoach rec` starts a separate Bash shell with recording paused. Run
`record on` or `record off` at the shell prompt; there is no toggle inside Vim.
After `exit`, termcoach opens a private draft under `~/.cache/termcoach/` in
`$EDITOR` (default `vi`). Delete or edit anything you do not want to keep, then
use `[y/N]` to confirm export. The program prints the exact path of the final
`transcript_*.md`; declining the export deletes the draft.

The transcript separates prompt input, foreground program input and output.
It includes exit codes where available and timestamps relative to session start.
The recorder tries to trim long logs to the start, first matching error and tail.
For full-screen programs like Vim, it keeps key tokens and attempts to extract
plain-text errors and notices. There are no screen frames. Treat command text
and key tokens as observations: they do not establish the final Readline buffer,
Vim mode, or what an edit did. Prompt capture is intended for simple, single-line
Bash input with the default Emacs bindings.

## Review with a model (optional)

You can hand the approved transcript to an assistant with a question such as
“Where did I spend extra keystrokes during the edit–build–fix loop?” Ask for
up to three suggestions, each tied to a transcript block and a concrete action
to try next time.

To call a model from termcoach, use an endpoint that supports OpenAI-style
`POST /chat/completions`:

```bash
termcoach config init     # Creates ~/.config/termcoach/config.toml
# Edit base_url and model under [llm]. For base_url, use the API root
# (e.g. https://api.example.com/v1); termcoach adds /chat/completions.
termcoach config set-key  # Hidden input; saves a local plaintext key file
termcoach config show

# The newest transcript is used when no path is given:
termcoach review
termcoach review --pick          # list the last 10 and choose by number
termcoach review --dry-run       # show the request without calling the API
# Or name one explicitly:
termcoach review "$HOME/.cache/termcoach/transcript_YYYYMMDD_HHMMSS.md"
```

The key is read from `TERMCOACH_API_KEY`, then the variable named by
`api_key_env`, then `~/.config/termcoach/key`. The TOML file takes the variable
**name**, not the key. `TERMCOACH_BASE_URL` and `TERMCOACH_MODEL` override the
file settings. Use an HTTPS endpoint for remote review. A local relay may
forward requests to a remote provider; check where yours sends data.

`review` sends the file it selected, statistics derived from it, and a summary
of findings that have not been dismissed. When no path is given it takes the
newest `transcript_*.md` from `~/.cache/termcoach` and prints the name, size
and time so you can see which recording is being analysed; `--pick` lists the
last ten instead. It shows the destination and asks before sending; `--yes`
skips that confirmation. `--dry-run` displays the request body without making
an API call. It contains the transcript and review history, so avoid running it
in a shared terminal or redirecting it to logs.

The model must return JSON with at most three findings (extras are dropped with a
notice). After a review, choose
`t` to store a new finding for tracking or add a review to an existing one,
`d` to dismiss it, or Enter to display it only. The tool never executes the
model's recommendations.

## Optional practice

The review may come back with one small exercise tied to a finding
(`task`, `steps`, `done_when`, `minutes`). You can always decline it with a
plain Enter.

If you accept, termcoach creates a throwaway workspace under
`~/.cache/termcoach/practice/` with a few sample files, opens a shell there with
recording already on, and after `exit` exports the session as
`practice_<timestamp>.md`. It then offers to review that practice immediately,
using the same review path (with practice offering disabled for that round).

Exercises are text only — nothing is executed for you — and any suggestion
containing an obviously destructive command (`sudo`, `ssh`, `rm -rf`, `curl`,
`mkfs`, `dd`, …) is dropped with a message. Treat that filter as a sanity check,
not a security boundary: you are typing into your own shell.

## Track findings locally

```bash
termcoach metrics "$transcript"         # Key-token counts and command counts
termcoach findings list
termcoach tips                         # Also shown at the start of termcoach rec
# Replace the ID below with one from findings list.
termcoach findings accept shell.readline.backspace_run
termcoach findings dismiss shell.readline.backspace_run
termcoach import-review review.json    # Import suggestions without an API call
```

`metrics` counts key markers in the transcript text and reports them per
channel: `prompt` (command-line editing) and `program` (input to vim and other
foreground programs). Runs are measured inside one command and one channel
only, so a long run in vim never inflates the prompt editing numbers. Text
inside `<paste>…</paste>` is not counted as keystrokes — only `paste_count`
and the body length (`paste_chars`, tags excluded) reflect it, and a pasted
line that happens to start with `输出:` or `## 命令` is still just text.

Startup tips draw a trend only across **identical** metric keys
(`program.max_backspace_run: 34 → 12 → 2`); a vim number is never joined with a
prompt number, and legacy unscoped keys are labelled separately. Evidence is
restricted to known metric names, and during `review` the numbers are taken
from the local metrics rather than from the model's reply.

Output is capped: each command keeps head + first error + tail (≤4 KB after
trimming, marker included), capture drops the middle of anything over 64 KB and
says so, and a whole serialized review request stays under 160 KB
(`MAX_REQUEST_BYTES`). Raise the constants in `recorder.py` / `llm.py` if you
need more.

`import-review` accepts a JSON object such as:

```json
{
  "findings": [
    {
      "finding_id": "shell.readline.backspace_run",
      "title": "Long backspace runs at the Bash prompt",
      "recommendation": "Try Ctrl-W when you mean to delete the previous word.",
      "verification": "Compare similar edits in the next recording."
    }
  ]
}
```

Import asks before storing each finding; `--accept` also marks confirmed
imports as accepted. Acceptance (`proposed`, `accepted`, `dismissed`) and
trend (`unknown`, `recurring`, `improving`, `resolved`) are tracked separately.
Only accepted findings that have not been marked resolved appear as startup
reminders. `termcoach drill` is a separate offline shortcut exercise.

## Colors

Termcoach's own messages are colorised in layers: section titles bold cyan, item
numbers bold yellow, item titles bold, metric values cyan, labels and secondary
text dim, commands to type bold magenta, success green, warnings red.

Color is enabled only on a real terminal — pipes, redirected output, `TERM=dumb`
and `NO_COLOR` all disable it, and `TERMCOACH_COLOR=always|never` forces it on or
off. ANSI never reaches the transcript (the recorder strips SGR and skips
control blocks) or the review request.

## Privacy and local files

- Keep SSH, `sudo` and credential entry outside the recording window. There is
  no automatic secret detection. Review the transcript and any findings you
  keep for sensitive text.
- `rec`, `metrics`, `tips`, and `import-review` do not upload data. Recording is
  opt-in and writes no raw byte log. Preview uses a real temporary Markdown
  file; your editor may create swap or backup files of its own.
- Transcripts and `findings.jsonl` live under `~/.cache/termcoach/`. Clearing
  that directory removes your review history too. API settings and the optional
  key file live under `~/.config/termcoach/`.

## Development

Run from Linux or WSL:

```bash
python3 -m unittest discover -s tests -t .      # 158 unit tests
PYTHONPATH=. python3 tests/test_rec_integration.py   # PTY integration
PYTHONPATH=. python3 tests/test_integration.py       # drill harness
bash probes/probe_all.sh                        # tests + reinstall + real metrics
```

A `pre-push` hook refuses to push local paths, service ports, credentials or
stray recordings (`git config core.hooksPath .githooks` to enable it).

```text
termcoach/    cli, rec, recorder, rec_decoder, metrics, findings, llm, practice, style, coach
tests/        unit tests and PTY integration (unittest)
probes/       re-runnable probes: hook attribution, OSC boundaries, colour gating
```

MIT license.