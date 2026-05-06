# Regression tracking bot designed for the Linux kernel

Regzbot is a bot tailored for low-overhead regression tracking in the email
driven Linux kernel development process. It's actually used in the field, but
still in a alpha stage: more adjustments are needed to make it better suite the
its intended purpose.

Regzbot is now part of [KernelCI](https://kernelci.org/), where it contributes
to coordinated regression tracking across the kernel ecosystem. It monitors
mailing lists, bug trackers, and Git repositories so reported regressions do
not slip between first report and a fix in the appropriate kernel tree.
Reporters and developers interact with it through special lines in emails
(`#regzbot` commands) or existing conventions (`Link:` / `Closes:` tags in
commits), keeping overhead minimal.

Kernel development treats regressions with high urgency — fixing them takes
priority over new features. Before automated tracking, regression reports on
mailing lists could disappear in the traffic. Regzbot automates the
bookkeeping: open regressions appear on a public dashboard, periodic mail
summaries list outstanding issues ahead of releases, and `Link:` tags in fix
commits tie into tracked regressions without extra manual steps. See the
kernel documentation for
[reporting regressions](https://docs.kernel.org/admin-guide/reporting-regressions.html) and
[handling regressions](https://docs.kernel.org/process/handling-regressions.html).

To get an impression how regression tracking with regzbot is performed in
practice, see the
[about page for the Linux kernel regression tracking efforts](https://linux-regtracking.leemhuis.info/about/)
and
[the list of regressions regzbot currently tracks](https://linux-regtracking.leemhuis.info/regzbot/mainline/).
If you want to interact with regzbot, check out
[getting started with regzbot](docs/getting_started.md) or the bots
[reference documentation](docs/reference.md).

To install or develop for regzbot, see the [installation documentation](docs/installation.md).

### Public dashboards

| Resource | URL |
|----------|-----|
| Tracked regressions (mainline) | https://linux-regtracking.leemhuis.info/regzbot/mainline/ |
| All views (index) | https://linux-regtracking.leemhuis.info/regzbot/ |
| About the effort | https://linux-regtracking.leemhuis.info/about/ |
| Weekly reports (lore search) | [lore.kernel.org](https://lore.kernel.org/lkml/?q=%22Linux+regressions+report%22+f%3Aregzbot) |

### Documentation

* [Getting started](docs/getting_started.md) — report or fix a tracked regression
* [Reference](docs/reference.md) — full `#regzbot` command syntax
* [Installation](docs/installation.md) — run your own instance
* [Contributing](CONTRIBUTING.md) — codebase layout and development

## Licensing

Rezbot is available under the LGPL 2.1; see the file COPYING for details.

Regzbot was started by Thorsten Leemhuis as part of a project that has received
funding from the European Union’s Horizon 2020 research and innovation
programme under grant agreement No 871528.

Since May 2022 regzbot development and the Linux kernel regression tracking
efforts performed by Thorsten are supported with funds from Meta.
