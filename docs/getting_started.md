# Get started with regzbot

[[_TOC_]]


## Background

A **regression** is a change in the kernel that breaks something that previously
worked — degraded performance, a feature that stops working, or hardware that is
no longer recognized. Regressions are treated with higher urgency than ordinary
bugs; see the kernel documentation on
[reporting regressions](https://docs.kernel.org/admin-guide/reporting-regressions.html) and
[handling regressions](https://docs.kernel.org/process/handling-regressions.html).

Linux kernel development happens primarily over **email**. A few pieces of this
infrastructure are relevant to regzbot:

* **Mailing lists** — developers communicate through lists organized by
  subsystem. `regressions@lists.linux.dev` is specifically for regression
  reports.
* **lore.kernel.org** — the public archive of all kernel mailing lists, where
  every email has a permanent URL.
* **Message-ID / In-Reply-To / References** — standard email headers that
  define threading. Regzbot uses these to tie replies, patches, and fixes to the
  original report.
* **`Link:` and `Closes:` tags** — conventions in Git commit messages that
  reference mailing list discussions. Regzbot watches these to detect fixes.

Regzbot tracks three Git trees because fixes arrive at different times:
**mainline** (`torvalds/linux.git`), **linux-next** (subsystem work before
mainline), and **stable** (already-shipped kernels). A fix present only in
linux-next shows as "fix incoming" until it reaches the relevant tree.


## Why and how to make regzbot track a Linux kernel regression

When reporting a Linux kernel regression it is in your interest to make [regzbot](https://gitlab.com/knurd42/regzbot/) aware of the issue, as that ensures the report won't accidentally fall though the cracks; it also makes sure leading developers see the issue via the tracked regression website [or the weekly reports, which are not sent yet, but soon will be].

To get these benefits there is just one thing you need to do when reporting the regression by mail: include a line starting with `#regzbot introduced foo`, where `foo` specifies when the regression started to happen. One way to do that is to specify a version range mentioning the last version that worked and the first broken one:

`#regzbot introduced: v5.13..v5.14-rc1`

There is another way if you know which commit causes the regression, which will help to get the regression fixed quickly. So be sure to point it out, if you know it:

`#regzbot introduced: 1f2e3d4c5d`

In both cases ensure a blank line separates the line with this 'regzbot command' from the rest of the mail. That's all you have to do in addition to what is outlined in the kernel's [Reporting Issues](https://www.kernel.org/doc/html/latest/admin-guide/reporting-issues.html) document. Remember to CC regressions@lists.linux.dev, as outlined in that document: sending mail there ensures the report gets on the radar of regzbot and people fighting Linux Kernel regressions.

See below for a few other examples how to specify ranges, how to modify the version range later, or make regzbot and its consumers aware of additional places with further details.

## How to inform regzbot you are fixing a Linux kernel regression it tracks

Regzbot is designed to normally not create any additional chores for Linux kernel developers like you. But for that to work it's important you do something the [Linux kernel documentation specifies for a while already](https://www.kernel.org/doc/html/latest/process/submitting-patches.html): when fixing a regression, include a `Link:` tag with the URL to the report in the [mailing list archives on lore.kernel.org](https://lore.kernel.org/). This aspect is important for regzbot, as it allows the bot to connect the fix with the regression's report. That's needed so regzbot can do things automatically that otherwise would mean manual work for somebody — like marking the regression as resolved once the fix hits mainline.

But sometimes you might want to do more with regzbot, like specifying a culprit exactly after a bisection or marking a regression as resolved. The text below explains how to do these and other things; the instructions there also will tell you how to use regzbot to track regressions for your own code or the subsystem you maintain, as that will make sure none fall through the cracks unnoticed.


## More regzbot features relevant for both reporters and developers


### Important basics: How to interact with regzbot

There are things you need to be aware of to understand the examples that are about to follow in the next sections:

1. To modify properties of a tracked regression, use regzbot commands in a mail you send as reply to the mail considered as the report. The easiest and safest way to achieve that: reply to the mail that made regzbot track the regression using `#regzbot introduced`. You don't need to reply directly to the report, you can use regzbot commands anywhere below in the hierarchy. For example, if the report is in message A, and B is a reply to A, then it's fine to use a regzbot command in a reply to B: regzbot will know it's about the regression reported in A. For that to work you need to use your mailers 'Reply' or 'Reply-to-all' functions, as it only then it will set the mail's _In-Reply-To_ and _References_ header fields appropriately.

2. Always add regressions@lists.linux.dev to the recipients, as everything concerning regressions should CC that list anyway. That ensures Regzbot will see the mail, even if it's monitoring a few popular lists as well. It's up to you if you send the mail just there or use your mailers 'Reply-to-all' function to also sent it to other people and lists as well; most of the time it will be wise to keep them in the loop.

3. You can use multiple regzbot commands in one mail, but you must separate them from the rest of the mail with a blank line; also make sure the '#' before the "regzbot" is the line's first character.

4. If you have additional information relevant to the regression, just sent a reply to the report or a descendant mail. Regzbot will see it and list it as among the latest activities on its web-interface, which is meant to provide all the relevant details about a regression in a quickly consumable way.


### Make regzbot track an existing report

You want to make regzbot track a regression you or someone else reported already without getting regzbot involved? What you do then depends on how it was reported:

 * If the regression was reported by mail, simply reply to it with regressions@lists.linux.dev in CC and a paragraph that contains something like this:

   `#regzbot introduced: v5.13..v5.14-rc1 ^`

   The caret ("^") at the end of the line tells regzbot to treat the parent mail (the one you reply to) as the report.

 * If the regression was reported to some bug tracker, send a mail to the regression list that roughly outlines the regression and includes a paragraph that contains something like this:

   `#regzbot introduced: v5.13..v5.14-rc1 https://example.com/somewhere/someplace.html`

### Update properties of a tracked regression


#### change the range or commit that introduced the regression

Simply write a reply to the report that uses the 'introduced' command again. Just like initially, you can use ranges, commits, or a mix of both in the way that is understood by git. Here are a few examples:

`#regzbot introduced: v5.14-rc1..v5.14-rc2`

`#regzbot introduced: 1f2e3d4c5d`

`#regzbot introduced: v5.13..`

`#regzbot introduced: v5.13..1f2e3d4c5d`

`#regzbot introduced: v5.13.8..v5.14-rc1`

`#regzbot introduced: v5.13.8..v5.13.10`

`#regzbot introduced: next-20211006..next-20211008`

Note: to associate the regression to a tree, regzbot will look version tags and commits up in the Git trees for the Linux mainline, stable and next; if it can't find a proper match, it might miss-file the regression. Thus stick to the format used in the examples and do not put any spaces before or after the `..`.

Reminder: Linux distributors often modify or enhance their Linux based kernels, hence any problems you face with such kernels might be caused by these changes. That's why the Linux kernel developers [mainly care about regression happening with unmodified kernels, which are often called 'upstream kernel', 'official kernel', or 'vanilla'](https://www.kernel.org/doc/html/latest/admin-guide/reporting-issues.html#make-sure-you-re-using-the-upstream-linux-kernel). Regzbot thus focuses on these, too. It thus only understand version tags used by the upstream Linux kernel developers and doesn't handle version numbers like `5.13.12-200.fc34.x86_64` (Fedora) or `5.4.0-12.15-generic` (Ubuntu). If you face a regression with these kernels you should report them to your distributor; alternatively, you can recheck if they occur with a upstream kernel and then report to the Linux kernel developers.

Also remember to read the [Reporting Issues](https://www.kernel.org/doc/html/latest/admin-guide/reporting-issues.html) document carefully, as some ranges are possible to encounter, but might be too vague and thus not be handled appropriately by the developers. One such range would be `v5.13.8..v5.14.4`, as such a regression might be caused by a change in mainline between v5.13 and v5.14, or due to a modification performed between 5.14 and 5.14.4. You thus ideally should rule out which of the two it is.


#### Update the report's title

Use this command, just replace `foo` with the new title:

`#regzbot title: foo`


### Point regzbot to other places with further details about a regression

#### Link and monitor a related discussion

Sometimes someone else will report a regression a second time without getting regzbot involved; or a discussion closely related to a tracked regression will happen in a different mailing list thread. In such cases it's a good idea to make regzbot monitor such threads, as regzbot then will show this activity in its web-interface. That will help others looking into the regression to determine its current status quickly, as all relevant information then are at hand.

There are two ways to realize this. One is sending a reply to the report of the regression where you use a command like this:

`#regzbot monitor: https://lore.kernel.org/all/30th.anniversary.repost@klaava.Helsinki.FI/`

Alternatively, you can do it the other way around: by sending a mail in the second discussion that links to the report of the regression. In that case you don't even need a regzbot command, using a link tag is enough. Let's assume a regression tracked by regzbot was reported in https://lore.kernel.org/all/30th.anniversary.repost@klaava.Helsinki.FI/, then all you have to include in your mail to the second discussion is this:

`Link: https://lore.kernel.org/all/30th.anniversary.repost@klaava.Helsinki.FI/`

You might want to put a comment in front of it, for example something like this: *'# tell regzbot about this, as it's related to this tracked regression'*. That way no one will wonder why you put the link tag there.

If you wonder why regzbot relies on using `Link:` here, there is a simple reason: it will ensure regzbot automatically monitors all threads with postings of patches to fix the linked regression. Developers thus don't have to care about regzbot when posting fixes for regressions, as long as they link to the report, which they are supposed to do anyway.


#### Point to a place with further details, like a bug-tracker

Most of Linux kernel development happens via mailing lists, but sometimes additional information is stored somewhere on the web, for example an issue tracker. In such cases consider telling regzbot about it, as it will then mention it prominently in its web-interface:

`#regzbot link: https://bugzilla.kernel.org/show_bug.cgi?id=123456789`

Just like the monitor command this will help people that look into the regression to quickly gather important facts.

### Resolve a regression

#### Mark a regression as fixed

As stated earlier, the preferred way to mark a tracked regression as resolved is by using a `Link:` tag in the commit message of the fix which points to the report of the regression. Obviously sometimes people will forget to do that; other times someone will report a regression for which the fix was committed already. In those cases someone has to tell regzbot about the fix manually, for example by specifying the summary of the fix (e.g. the first line of a git commit message) like this:

`#regzbot fix: 'foo: fix a recent change in bar that broke suspend'`

Alternatively, if the commit-id is stable you can specify it like this:

`#regzbot fix: 1f2e3d4c5d`

Both approaches work even if the fix hasn't reached the tree yet where this regressions occurred: regzbot will then consider the regression as "fix incoming" and automatically mark it as fully fixed once a matching commits hits the tree.

#### Duplicates

Sometimes multiple people will report the same regressions without knowing about each other. When you notice that, check which of the two seems to be the one which is closer to the root of the problem or even a solution. Let's assume we have two reports already tracked by regzbot we call A and B; A is older, but B is more informative, as crucial developers replied there and discussed a solution. Then it's a good idea to mark A as duplicate of B. You have two options to do that:

 * Send this regzbot command to the thread with the report A, where you replace `url` with a link to the B in the [mailing list archives on lore.kernel.org](https://lore.kernel.org/all/):

   `#regzbot dup-of: url`

   It thus might look like this:

   `#regzbot dup-of: https://lore.kernel.org/all/30th.anniversary.repost@klaava.Helsinki.FI/`

 * Send this regzbot command to the thread with the report B, where you replace `url` with a link to the A in the [mailing list archives on lore.kernel.org](https://lore.kernel.org/all/):

   `#regzbot dup: url`

Regzbot from then on will consider all activies of A as if they'd happen for B.

#### Mark a regression as resolved

A tracked regression for some reason turns out to not need a fix? There are various reasons why this might happen, for example when a tracked issue turns our to not be a regression at all. In such cases just tell the world about it in a reply where you tell regzbot about it like this:

`#regzbot resolve: nothing is broken, by hardware was faulty`

The explanation is optional, but strongly recommended, as it will help anyone who looks into the issue later.

#### Mark a regression as inconclusive

Occasionally a tracked regression can't be resolved, for example when nobody is able to track down the change that causes the regression in reasonable time. Leaving such issues in the list of unresolved regressions clutters things and distracts from more pressing issues; marking such a regressions as resolved to avoid that would be unsuitable and make the issue hard to find for other people that run the same issue later. To avoid both, mark such regressions as inconclusive:

`#regzbot inconclusive: reporter since the report is MIA an ignored further inquiries`

The explanation is optional, but strongly recommended, as it will help anyone who looks into the issue later.
