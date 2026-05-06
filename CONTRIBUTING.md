Codebase overview
=================

| Area | Files | Role |
|------|-------|------|
| Core + DB | `__init__.py` | Regression model, `GitTree`/`GitBranch`, `ReportThread`, `run()`/`generate_web()`/`report()` |
| Bot commands | `_rbcmd.py` | Parse and execute `#regzbot` subcommands |
| CLI | `commandl.py` | argparse-based subcommands |
| Web export | `export_web.py` | Static HTML generation |
| Mail reports | `export_mail.py` | Weekly text/mail report layout |
| CSV export | `export_csv.py` | CSV-oriented export (tests) |
| Lore ingestion | `_repsources/_lore.py` | NNTP and HTTPS access to lore archives |
| Tracker sources | `_bugzilla.py`, `_gitlab.py`, `_github.py`, `_generic.py` | Tracker-specific API integrations |
| Tests | `testing_online.py`, `testing_offline.py`, `testing_trackers.py`, `testdata/*` | Offline/online/tracker tests/expected results |

Report sources (pluggable backends):

| Source | Implementation | Notes |
|--------|----------------|-------|
| **lore** (NNTP/HTTPS) | `_repsources/_lore.py` | Primary source; kernel mailing list archives |
| **bugzilla.kernel.org** | `_repsources/_bugzilla.py` | REST API with API key |
| **GitLab** | `_repsources/_gitlab.py` | Issue tracker integration |
| **GitHub** | `_repsources/_github.py` | Issue tracker integration |

Tracker polling logic lives in `_repsources/_trackers.py`.

Sign-off Process
================

Every commit contributed to this project must be signed-off.

A sign-off is a single line added to the end of your commit messages that certifies
that you wrote and/or have the right to the contributed changes.

The full text of the certification from developercertificate.org is a follows:

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

The signature should look as such:

Signed-off-by: John Doe <john.doe@email.com>

