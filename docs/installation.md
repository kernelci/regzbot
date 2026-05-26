regzbot is written in python 3. You need to install the dependencies listed in the `requirements.txt` file.

## setting up python virtual env

If you want to use a python3 virtual environment, create your virtual environment at `~/.local/share/regzbot/python-venv/`. The `regzbot.sh` script will check this dir to set the environment.

    python3 -m venv ~/.local/share/regzbot/python-venv/

Then activate the environment and install the dependencies:

    source ~/.local/share/regzbot/python-venv/bin/activate
    pip install -r requirements.txt

If you are going to contribute to the project, you should also install the development dependencies:

    pip install -r requirements-dev.txt

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

## Development tools

### Ruff

We use [Ruff](https://github.com/astral-sh/ruff) for fast Python linting and formatting.
The configuration for this tool can be seen in [ruff.toml](../ruff.toml).

#### Running Ruff Checker

You can check the formatting or linting status by running the following commands:

```bash
ruff format --check
ruff check
```

#### Fixing Issues Automatically

You can fix issues automatically with these commands:

```bash
ruff format
ruff check --fix
```

## Pre-commit

To run Ruff automatically on each commit, install the dev dependencies and then install the pre-commit hooks:

    pip install -r requirements-dev.txt
    pre-commit install

You can also run the hooks on all files manually:

    pre-commit run --all-files
