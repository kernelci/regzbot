# Reference documentation for regzbot, the Linux kernel regression tracking bot

[[_TOC_]]

*Note: this document explains regzbot concept and all options; if you want something easier and quicker to consume, head over to '[getting started with regzbot](https://gitlab.com/knurd42/regzbot/-/blob/main/docs/getting_started.md)'*

## Basic concept

Regzbot is a bot watching mailing lists and Git trees to track Linux kernel regression from report to elimination, to ensure none fall though the cracks unnoticed. It tries to impose as little overhead as possible on reporters and developers, but needs two things to do everything automatically:

 * someone needs to tell regzbot when a mail contains a regression report
 * the fix and other related discussions need to link to the mail with the report

The second task normally shouldn't cause extra work for anyone, as patches fixing a regression ought to do that already years before regzbot came to light.

But yes, the first task creates a small burden on reporters. This simply can't be avoided, but easy to fulfill when reporting the regression as outlined in the [Linux kernel's "reporting issues" document](https://www.kernel.org/doc/html/latest/admin-guide/reporting-issues.html): simply add the following line to the mail with the report, separated from the earlier and later parts of the mail by a blank line:

#regzbot introduced: v5.13..v5.14

Regzbot then considers the mail as a report for a regression that was introduced between Linux 5.13 and 5.14. Instead of a version range it's possible to specify a commit-id here, too, if the change causing the regression is known.

### What regzbot does once it's aware of a regression

After regzbot was told about the regression, it will try to keep track of the fixing progress. To do so, it will record all direct and indirect replies to this mail.

In addition, regzbot will look for mails and commits that link to the report using the mail's 'Message-ID'. Say someone reported a regression in a mail with the ID '4970a940-211b-25d6-edab-21a815313954@example.com', then regzbot will look out for mails and commits with a string like this and consider them related:

Link: https://lore.kernel.org/r/4970a940-211b-25d6-edab-21a815313954@example.com/

That way regzbot can automatically record if a patch to fix the regression gets posted to one of the main Linux kernel development mailing lists for review. Regzbot will also notice when a patch with such a link gets applied to the Linux kernel sources. It then marks the regression as 'to be fixed' or 'fixed', depending on the tree where the patch is applied. Say regzbot was told about a regression with a command like `#regzbot introduced: v5.13..v5.14`. From those two version tags it can conclude the regression needs to be fixed in linux-mainline. Hence, if a fix for that regression gets applied in a tree upstream to mainline (say linux-next), it will mark the regression only as 'to be fixed' and store a pointer to the commit. Only when this change gets merged to linux-mainline it will consider the regression 'fixed'.

### What regzbot does with the gathered data

From the collected data Regzbot compiles a website holding information about all tracked regressions, like the regression's title or the age of the first and the last activity; additionally, it will link to the thread with the report, the latest activities in that thread, as well as mailing list threads and webpages related to the regression — for example those that linked to the regression report using above-mentioned Link: tag.

Regzbot is also able to compile a report as pure text and sent them to the Linux kernel mailing list and the tree maintainers, for example every week or so.

## Interacting with regzbot

Above outlines the core concept of regzbot. Obviously that's not enough, as users will sometimes forget to get regzbot involved when reporting a regression; and they might want to update the version range initially specified, for example after they found the change causing the regression using a bisection. Other times the report might turn out to be a duplicate of another report or not a regression at all. And developers might forget linking to the report in the fixes commit message, hence there needs to be another way to tell regzbot a tracked regression got resolved.

To cover these and other use-cases it's possible to interact with regzbot via mail, as explained below.

### Commands to be sent as a reply to the report

Normally one interacts with regzbot via '#regzbot commands' placed in mails sent as reply to the report (e.g., the mail that used `#regzbot introduced: ...`). That allows regzbot to automatically associate the command with the tracked regression; it doesn't need to be a direct reply to the report, a indirect reply in the sub-thread that started with the regression report is fine as well.

The following '#regzbot commands' are available. Only the `introduced` commands can be used in threads not already tracked by regzbot. Multiple commands can be used in one mail; if `introduced` is among them, it needs to be the first.

#### commands to make regzbot track a regression

 * `#regzbot introduced: <commit-id|range> [^|url]`

   Tells regzbot to track a regression introduced in <commit-id> or <range>. The mail with this tag will be considered the report of the regression, unless a caret or url is provided as second parameter:

   `<commit-id>` must be a commit-id at least 8 characters long. Regzbot will try to look the commit-id up in linux-next, linux-mainline, and linux-stable to associate the regression to one of those trees.

   `<range>` must be in the format used by git using either tags or commit-ids that ideally should both be present in one of linux-next, linux-mainline, or linux-stable. Ranges thus can look like this: `v5.13..v5.14`, `v5.14-rc1..v5.14-rc2`, `v5.13..1f2e3d4c5d`, `next-20211006..next-20211008`, or `v5.13.8..v5.13.10`. Ranges that use tags from different trees (like stable and mainline, e.g., `v5.13.8..v5.14-rc1`) won't make regzbot fail, but it might associate the regression to the wrong tree or consider it unassociated.

   `^` make regzbot treat the parent mail as the report of the regression (the one specifies in the mail's header as 'In-Reply-To'); useful to make regzbot track a regression someone reported on a mailing list, but forgot to get regzbot involved.

   `url` make regzbot treat that location as report of the regression; useful to make regzbot track regressions you or someone else reported in a bug tracker or somewhere else.

#### commands to update properties of a tracked regression

 * `#regzbot introduced: <commit-id|range>`

    When used in a thread of an already tracked regression this will update the introduced field for the tracked regression.

 * `#regzbot title: <title>`

   Sets the title regzbot assigned to the regression, which it otherwise derives from the subject of the report.

   `<title>` must be a string.

#### commands to point to related discussion, reports and webpages

 * `#regzbot dup: <link>`

   Tells regzbot about another report (e.g. a duplicate) for the tracked issue to be found at `<link>`.

   Regzbot then will create a entry for the report and mark it as duplicate for the regression.

 * `#regzbot link: <link> [title]`

   Tell regzbot about something on the web that is of interest to anyone looking into the regression. Regzbot will show the link prominently in the web-interface and its reports. This can be used to link to external sources or to highlight important mails in long and complicated discussion about a regression.

   `<link>` must point to a mail in the lore message archiver service and thus needs to look like this: `https://lore.kernel.org/lkml/30th.anniversary.repost@klaava.Helsinki.FI/`.

   `[title]` is must be a string and is is optional, but recommended to show where link leads to.

 * `#regzbot unlink: <link>`

   Remove a link added earlier by a `#regzbot link:`

 * `#regzbot monitor: <link> [title]`

   Tell regzbot about a discussion related to the regression. Regzbot will show the link prominently in the web-interface and its reports; additionally, it will also monitor the thread and consider any activity there as an activity for the regression. This can be used to monitor related threads, for example a review of a patch for the particular regression; ideally thus the mail with the patch would have linked to the report with the regression using a 'Link: ' tag, as that would have had the same effect on regzbot.

   `<link>` must point to a mail in the lore message archiver service and thus needs to look like this: `https://lore.kernel.org/lkml/30th.anniversary.repost@klaava.Helsinki.FI/`

 * `#regzbot unmonitor: <link>`

   Remove a monitored thread from the regression that was added earlier by a `regzbot monitor:` command.

#### commands to resolve a regzbot entry

 * `#regzbot dup-of: <link>`

   Mark the regzbot entry for this regression as a duplicate of the entry for the linked regression.

   `<link>` must point to a report of a tracked regression in the lore message archiver service and might look like this: `https://lore.kernel.org/lkml/30th.anniversary.repost@klaava.Helsinki.FI/`

 * `#regzbot fix: <patch subject>|<commit-id>`

   Tells regzbot the regression is fixed or is going to be fixed by by a commit with the git summary `<patch subject>` or the specified `<commit-id>`. If the commit is found in the tree where the regression occurred, regzbot will mark the regression immediately as 'fixed'; for all other cases it will consider the regression as 'fix incoming' and look out for mails with <patch subject>, until the commit shows up in the appropriate tree.

   The `<patch subject>` can be quoted, but doesn't have to be.

 * `#regzbot inconclusive: [reason]`

   Mark the entry for the regression as inconclusive. Use this when the regression is unlikely to be resolved because nobody is able to find the culprit in reasonable time.

   `[reason]` is a string and optional, but strongly recommended, as a brief explanation of why the regressions is considered resolved will help anyone who looks into the issue later.

 * `#regzbot resolve: [reason]`

   Makes regzbot mark the entry for the regression as resolved; use this whenever the regression doesn't need any further tracking because it was solved without any code changes.

   `[reason]` is a string and optional, but strongly recommended, as a brief explanation of why the regressions is considered resolved will help anyone who looks into the issue later.

#### commands users and developers normally shouldn't use

The following 'regzbot commands' are intended mainly for people helping with regression tracking:

 * `#regzbot activity-ignore`

   Regzbot will not consider a mail with this command as an activity for the regression. It thus will neither update the value for 'days since last activity' nor link to the mail in the 'latest activity' section of its web-interface. The command is useful for mails that are totally irrelevant for the bug processing process and thus would only be noise to people looking into the regression via regzbot; it's thus of use for mails only meant for regzbot, for example ones that just update Regzbot properties like the title.

   Note: the same effect as this regzbot command can be achieved by adding `#forregzbot` to the end of a mail's subject. The latter should be preferred for mails primarily intended for regzbot, as the tag makes such mails easy to catch by mail filters and easy to spot in mailing lists archives.

 * `#regzbot backburner: <reason>`

   Mark regressions that are not urgent for some reason as "on back burner". They will get sorted into a separate category in the web-ui and are excluded from the reports, unless something happened since the last report was sent.

 * `#regzbot unbackburn: [reason]`

   Remove the "on back burner" flag from the tracked regression.

 * `#regzbot poke`

   Regzbot will consider the mail with this command as a 'poke' asking for a progress update from someone involved. It's meant to be used in inquires when a regression seems to become stale, e.g., where there was no mail from a user or developer for a while. Regzbot in its reports and the web UI will show if someone sent a poke to get things rolling again. Apart from this the mail will be handled like it had contained `#regzbot ignore-activity`. It thus won't be counted as an activity and in regzbot web-interface continue to look stale until someone replies.

### Commands regzbot accepts everywhere it looks

Regzbot ignores all '#regzbot commands' in threads that are not associated with a tracked regression, but tries to look for mails that are related. To do so, it acts if it seems mails or commits with use the following:

#### backlinks

 * `Link: https://lore.kernel.org/r/30th.anniversary.repost@klaava.Helsinki.FI/`

   If used in a mail with a Link to the report of a tracked regression, regzbot will start to monitor the thread; the mail with this tag and all replies to it will thus be considered an activity for the regression in question.

   If used in a commit with a Link to the report of a tracked regression, regzbot will consider the commit a fix for the regression.

#### tag users and developers normally shouldn't use

 * `#regzbot ^backmonitor: https://lore.kernel.org/r/30th.anniversary.repost@klaava.Helsinki.FI/`

   Makes regzbot start monitoring the parent mail for the linked regressions and ignore the mail that contains this, as it would contain a '#regzbot ignore-activity'. Useful for mails in the style of 'hey, next time when writing the commit message for a tracked regression fix, please add the link to the report of said regression'. For all other cases better reply to the report with a `#regzbot monitor` command pointing to the related discussion.
