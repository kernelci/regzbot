regzbot is written in python 3. You need to install the dependencies listed in the `requirements.txt` file.

## setting up python virtual env

If you want to use a python3 virtual environment, create your virtual environment at `~/.local/share/regzbot/python-venv/`. The `regzbot.sh` script will check this dir to set the environment.

    python3 -m venv ~/.local/share/regzbot/python-venv/

Then activate the environment and install the dependencies:

    source ~/.local/share/regzbot/python-venv/bin/activate
    pip install -r requirements.txt

## setting up git trees

Next create the git trees repositories at `~/.cache/regzbot/gittrees/`. You need git checkouts
(as folders or symbolic links) for mainline, linux-next and linux-stable. The folders inside
 `~/.cache/regzbot/gittrees/` should be named `mainline`, `next` and `stable` respectively.

## setup regzbot

Now you are ready to run the setup command:

    ./regzbot.sh setup

This command will run the setup and start the database file at `~/.local/share/regzbot/database.db`. If you need to re-run the setup command delete the db file manually first.

## add config file

regzbot has a config file at `~/.config/regzbot/regzbot.cfg`. It is used for the bugzilla token.
Go to [bugzilla.kernel.org](https://bugzilla.kernel.org/), get your API key and add it to the config file. Don't add any quote around the token string.

```
[bugzilla.kernel.org]
apikey = tokenhere
```

## run regzbot

Now you are ready to run regzbot

    ./regzbot.sh run

It will generate web reports at `~/.cache/regzbot/websites/`

## available commands

| Command | Purpose |
|---------|---------|
| `setup` | Initialize database, register sources, first Git tree sync |
| `run` | Full update cycle (sources → git → web) |
| `pages` | Regenerate web output only |
| `report` | Build interactive mail reports (operator sends manually) |
| `recheck` | Reprocess specific message IDs |
| `test` | Run offline/online test suites |

## data paths

| Path | Contents |
|------|----------|
| `~/.local/share/regzbot/database.db` | SQLite database (tracked regressions, processed message IDs, repository metadata) |
| `~/.cache/regzbot/gittrees/` | Local Git clones (mainline, next, stable) |
| `~/.cache/regzbot/websites/` | Generated static HTML output |
| `~/.config/regzbot/regzbot.cfg` | Configuration file (API keys) |
