#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import datetime
import json
import pathlib
import os

import yattag

import regzbot
from regzbot import PatchKind

logger = regzbot.logger


class RegLinkWeb(regzbot.RegLink):
    def __init__(self, *args):
        super().__init__(*args)

        self._for_regression = None

    def set_for_regression(self, regression):
        self._for_regression = regression

    def html(self, yattagdoc, regression):
        with yattagdoc.tag("i"):
            with yattagdoc.tag("a", href=self.link):
                yattagdoc.text(self.subject)

        if self.author:
            with yattagdoc.tag("div", style="padding-left: 3em;"):
                yattagdoc.asis(
                    '<script type="text/javascript">timeAgo(%s000);</script>' % self.gmtime
                )
                yattagdoc.text(", by %s" % self.author)
                if (
                    self.repsrcid
                    and self.entry
                    and regzbot.RegActivityMonitor.ismonitored(
                        self.entry, self.regid, self.repsrcid
                    )
                ):
                    yattagdoc.text(" (monitored)")
                if self._for_regression and not regression.regid == self._for_regression.regid:
                    yattagdoc.text(" [")
                    with yattagdoc.tag(
                        "a", href="../regression/%s/" % self._for_regression._actim_report.entry
                    ):
                        yattagdoc.text("via dup")
                    yattagdoc.text("]")

        return yattagdoc


class RegHistoryWeb(regzbot.RegHistory):
    def __init__(self, *args):
        super().__init__(*args)

    def html(self, yattagdoc):
        with yattagdoc.tag("i"):
            if self.regzbotcmd:
                if self.regzbotcmd == "poke:":
                    regzbotcmd = "poke"
                else:
                    regzbotcmd = self.regzbotcmd
                with yattagdoc.tag("a", href=self.url()):
                    yattagdoc.text("%s" % regzbotcmd)
            else:
                with yattagdoc.tag("a", href=self.url()):
                    yattagdoc.text("%s" % self.subject)

        with yattagdoc.tag("div", style="padding-left: 2em;"):
            yattagdoc.asis('<script type="text/javascript">timeAgo(%s000);</script>' % self.gmtime)

            if self.author:
                yattagdoc.text(", by %s" % self.author)

        return yattagdoc

    def html_event(self, yattagdoc):
        with yattagdoc.tag("div", style="padding-left: 3em;"):
            yattagdoc.text("History: ")

            with yattagdoc.tag("i"):
                if self.regzbotcmd:
                    if self.regzbotcmd == "poke:":
                        regzbotcmd = "poke"
                    else:
                        regzbotcmd = self.regzbotcmd
                    with yattagdoc.tag("a", href=self.url()):
                        yattagdoc.text("%s; " % regzbotcmd)
                else:
                    with yattagdoc.tag("a", href=self.url()):
                        yattagdoc.text("%s; " % self.subject)

            yattagdoc.asis('<script type="text/javascript">timeAgo(%s000);</script>' % self.gmtime)
            if self.author:
                yattagdoc.text(", by %s" % self.author)

            return yattagdoc


class RegActivityEventWeb(regzbot.RegActivityEvent):
    def __init__(self, *args):
        super().__init__(*args)

        self._for_regression = None

    def set_for_regression(self, regression):
        self._for_regression = regression

    def html(self, yattagdoc, regression):
        with yattagdoc.tag("i"):
            with yattagdoc.tag("a", href=self.url()):
                yattagdoc.text("%s" % self.subject)
        with yattagdoc.tag("div", style="padding-left: 2em;"):
            yattagdoc.asis('<script type="text/javascript">timeAgo(%s000);</script>' % self.gmtime)
            yattagdoc.text(", by %s" % self.author)
            if int(self.patchkind) > 0:
                if (PatchKind.DIFF | PatchKind.SUBJECT | PatchKind.SIGNEDOFF) in self.patchkind:
                    yattagdoc.text("; contains a signed-off patch")
                elif (PatchKind.DIFF | PatchKind.SUBJECT) in self.patchkind:
                    yattagdoc.text("; contains a proper patch")
                else:
                    yattagdoc.text("; contains a simple patch")
            if self._for_regression and not regression.regid == self._for_regression.regid:
                yattagdoc.text(" [")
                repsrc = self._for_regression._actim_report.repsrc
                with yattagdoc.tag(
                    "a", href="../regression/%s/%s/" % (repsrc.generic_name, repsrc.entryid)
                ):
                    yattagdoc.text("via dup")
                yattagdoc.text("]")

        return yattagdoc

    def html_event(self, yattagdoc):
        with yattagdoc.tag("div", style="padding-left: 3em;"):
            yattagdoc.text("Activity: ")
            with yattagdoc.tag("i"):
                with yattagdoc.tag("a", href=self.url()):
                    yattagdoc.text("%s" % self.subject)
            yattagdoc.text("; ")
            yattagdoc.asis('<script type="text/javascript">timeAgo(%s000);</script>' % self.gmtime)
            yattagdoc.text(", by %s" % self.author)
            if int(self.patchkind) > 0:
                if (PatchKind.DIFF | PatchKind.SUBJECT | PatchKind.SIGNEDOFF) in self.patchkind:
                    yattagdoc.text("; contains a signed-off patch")
                elif (PatchKind.DIFF | PatchKind.SUBJECT) in self.patchkind:
                    yattagdoc.text("; contains a proper patch")
                else:
                    yattagdoc.text("; contains a simple patch")

            return yattagdoc


class RegressionWeb(regzbot.RegressionFull):
    Reglink = RegLinkWeb
    Reghistory = RegHistoryWeb
    Regactivityevent = RegActivityEventWeb

    def __init__(self, *args):
        super().__init__(*args)

    def event_intro(self):
        html = yattag.Doc()
        html.text("Regression ")
        report_repsrc = self._actim_report.repsrc
        with html.tag(
            "a", href="../regression/%s/%s/" % (report_repsrc.generic_name, report_repsrc.entryid)
        ):
            html.text(self.subject)
        html.text(":")
        return html

    def events(self, gmtime_offset, event_intro):
        def fresh(gmtime):
            if gmtime > gmtime_offset:
                return True
            return False

        for actievent in reversed(self._actievents):
            if not fresh(actievent.gmtime):
                break
            yield {
                "gmtime": actievent.gmtime,
                "htmlevent_intro": event_intro,
                "htmlevent_content": actievent.html_event(yattag.Doc()),
            }

        for histevent in reversed(self._histevents):
            if not fresh(histevent.gmtime):
                break
            yield {
                "gmtime": histevent.gmtime,
                "htmlevent_intro": event_intro,
                "htmlevent_content": histevent.html_event(yattag.Doc()),
            }

    def html(self):
        def cell1(yattagdoc):
            with yattagdoc.tag("div", style="padding-left: 1em;"):
                with yattagdoc.tag("li"):
                    if self._introduced_url:
                        with yattagdoc.tag("a", href=self._introduced_url):
                            yattagdoc.text(self._introduced_short)
                        if self._introduced_presentable:
                            with yattagdoc.tag("div"):
                                yattagdoc.text("(%s)" % self._introduced_presentable)
                    else:
                        yattagdoc.text(self._introduced_short)
                        if self._introduced_presentable:
                            with yattagdoc.tag("div"):
                                yattagdoc.text("(within %s)" % self._introduced_presentable)

        def cell2(yattagdoc):
            def add_introduced(yattagdoc):
                yattagdoc.text(self._introduced_short)

            def __resolution():
                if self.solved_reason == "fixed":
                    return "resolution"
                elif self.solved_reason == "to_be_fixed":
                    return "fix incoming"
                elif self.solved_reason == "resolved" or self.solved_reason == "invalid":
                    return "marked resolved"
                elif self.solved_reason == "inconclusive":
                    return "marked inconclusive"
                elif self.solved_reason is not None:
                    return "%s" % self.solved_reason

            with yattagdoc_line.tag("details", id="regression-details", style="padding-left: 1em;"):
                with yattagdoc_line.tag("summary", style="list-style-position: outside;"):
                    # hide dupes that where just used for forward a bug and have no relevant activity:
                    regids_just_forwarded = []

                    # disabled for now, needs rework or removal once proper bugzilla support has setted;
                    #
                    # for regression in self, *self._dupes:
                    #    activitycount = len(regression._actievents)
                    #    if regression.regid != self.regid and activitycount < 2:
                    #        if activitycount == 0:
                    #           regids_just_forwarded.append(regression.regid)
                    #        elif regression._actievents[0].repsrcid == regression._histevents[0].repsrcid and \
                    #                 regression._actievents[0].entry == regression._histevents[0].entry:
                    #            # the other regression's sole activity was creating this one
                    #            regids_just_forwarded.append(regression.regid)

                    actireports = list()
                    for regression in self, *self._dupes:
                        if regression.regid not in regids_just_forwarded:
                            actireports.append(regression._actim_report)
                    actireports_sorted = sorted(actireports, key=lambda x: x.gmtime)
                    actireports = None

                    actievents = list()
                    for regression in self, *self._dupes:
                        for actievent in regression._actievents:
                            actievent.set_for_regression(regression)
                            actievents.append(actievent)
                    actievents_sorted = sorted(actievents, key=lambda x: x.gmtime)
                    actievents = None

                    links = list()
                    for regression in self, *self._dupes:
                        for link in regression._links:
                            link.set_for_regression(regression)
                            links.append(link)
                    links_sorted = sorted(links, key=lambda x: x.gmtime)
                    links = None

                    with yattagdoc.tag("strong"):
                        with yattagdoc.tag("i"):
                            yattagdoc.text(self.subject)

                    yattagdoc.text(" by ")
                    len_actireports = len(actireports_sorted)
                    for actireport in actireports_sorted:
                        with yattagdoc.tag(
                            "a",
                            href=regzbot.ReportSource.get_by_id(actireport.repsrcid).url(
                                actireport.entry
                            ),
                        ):
                            authorname = actireport.authorname
                            if not authorname:
                                authorname = "Unknown"
                            yattagdoc.text(authorname)
                        if len_actireports == 1 or actireport == actireports_sorted[-1]:
                            pass
                        elif len_actireports == 2:
                            yattagdoc.text(" and ")
                        elif actireport == actireports_sorted[-2]:
                            yattagdoc.text(", and ")
                        else:
                            yattagdoc.text(", ")

                    with yattagdoc.tag("div"):
                        yattagdoc.text("Earliest & latest ")
                        if len(actievents_sorted) > 0:
                            earliest_event = actievents_sorted[0]
                            latest_event = actievents_sorted[-1]
                        else:
                            # fallback for regressions without any activities
                            earliest_event = self._histevents[0]
                            latest_event = self._histevents[-1]

                        report_repsrc = actireport.repsrc
                        with yattagdoc.tag(
                            "a",
                            href="../regression/%s/%s/"
                            % (report_repsrc.generic_name, report_repsrc.entryid),
                        ):
                            yattagdoc.text("activity")
                        yattagdoc.text(": ")
                        if earliest_event is latest_event:
                            yattagdoc.asis(
                                '<script type="text/javascript">timeAgo(%s000);</script>'
                                % earliest_event.gmtime
                            )
                        else:
                            with yattagdoc.tag("a", href=earliest_event.url()):
                                yattagdoc.asis(
                                    '<script type="text/javascript">timeAgo(%s000);</script>'
                                    % earliest_event.gmtime
                                )
                            yattagdoc.text(" & ")
                            with yattagdoc.tag("a", href=latest_event.url()):
                                yattagdoc.asis(
                                    '<script type="text/javascript">timeAgo(%s000);</script>'
                                    % latest_event.gmtime
                                )

                        if self.poked:
                            yattagdoc.text("; poked ")
                            with yattagdoc.tag("a", href=self.poked.url()):
                                yattagdoc.asis(
                                    '<script type="text/javascript">timeAgo(%s000);</script>'
                                    % self.poked.gmtime
                                )

                        yattagdoc.text(".")

                        entered_loop = False
                        for counter, link in enumerate(links_sorted, start=1):
                            if counter == 1:
                                entered_loop = True
                                yattagdoc.text(" Noteworthy: ")
                            else:
                                yattagdoc.text(", ")
                            with yattagdoc.tag("a", href=link.link):
                                yattagdoc.text("[%s]" % counter)

                        if self.solved_reason or self.solved_duplicateof:
                            if self.solved_reason:
                                if not entered_loop:
                                    yattagdoc.text(" Noteworthy: ")
                                    entered_loop = True
                                else:
                                    yattagdoc.text(", ")

                                with yattagdoc.tag("mark", style="background-color: #D0D0D0;"):
                                    yattagdoc.text("[")
                                    if self.solved_url is None:
                                        yattagdoc.text(__resolution())
                                    else:
                                        with yattagdoc.tag("a", href=self.solved_url):
                                            yattagdoc.text(__resolution())
                                    yattagdoc.text("]")
                            if self.solved_duplicateof:
                                if not entered_loop:
                                    yattagdoc.text(" Noteworthy: ")
                                    entered_loop = True
                                else:
                                    yattagdoc.text(", ")

                                regression_duplicateof = self.get_by_regid(self.solved_duplicateof)
                                __dup_report_repsrc = regression_duplicateof._actim_report.repsrc
                                with yattagdoc.tag(
                                    "a",
                                    href="https://linux-regtracking.leemhuis.info/regzbot/regression/%s/%s/"
                                    % (
                                        __dup_report_repsrc.generic_name,
                                        __dup_report_repsrc.entryid,
                                    ),
                                ):
                                    with yattagdoc.tag("mark", style="background-color: #D0D0D0;"):
                                        yattagdoc.text("[is a duplicate]")
                        else:
                            for actievent in reversed(actievents_sorted):
                                if int(actievent.patchkind) == 0:
                                    continue

                                if not entered_loop:
                                    yattagdoc.text(" Noteworthy: ")
                                    entered_loop = True
                                else:
                                    yattagdoc.text(", ")

                                yattagdoc.text("[")
                                with yattagdoc.tag("a", href=actievent.url()):
                                    yattagdoc.text("patch")
                                    if (
                                        PatchKind.DIFF | PatchKind.SUBJECT | PatchKind.SIGNEDOFF
                                    ) in actievent.patchkind:
                                        yattagdoc.text(" (SOB)")
                                yattagdoc.text("]")
                                break

                        if entered_loop:
                            yattagdoc.text(".")

                if self.backburner:
                    with yattagdoc.tag("div"):
                        yattagdoc.text("On back burner: ")
                        with yattagdoc.tag("i"):
                            with yattagdoc.tag("a", href=self.backburner.report_url()):
                                yattagdoc.text("%s" % self.backburner.subject)
                        with yattagdoc.tag("div", style="padding-left: 1em;"):
                            yattagdoc.asis(
                                '<script type="text/javascript">timeAgo(%s000);</script>'
                                % self.backburner.gmtime
                            )
                            yattagdoc.text(", by %s" % self.backburner.author)

                for counter, link in enumerate(links_sorted, start=1):
                    with yattagdoc.tag("div"):
                        yattagdoc.text("[%s]: " % counter)
                        link.html(yattagdoc, self)

                if self.solved_reason:
                    with yattagdoc.tag("div"):
                        yattagdoc.text(" ")
                        with yattagdoc.tag("strong"):
                            yattagdoc.text(__resolution().capitalize())
                            yattagdoc.text(": ")

                        def solved_explanation(yattagdoc):
                            with yattagdoc.tag("i"):
                                if (
                                    self.solved_reason == "fixed"
                                    or self.solved_reason == "to_be_fixed"
                                ):
                                    if self.solved_entry:
                                        yattagdoc.text("%s" % self.solved_entry[:12])
                                    if self.solved_subject and self.solved_entry:
                                        yattagdoc.text(' ("%s")' % self.solved_subject)
                                    elif self.solved_subject:
                                        yattagdoc.text("%s" % self.solved_subject)
                                elif self.solved_subject:
                                    yattagdoc.text(self.solved_subject)

                        if self.solved_url is None:
                            solved_explanation(yattagdoc)
                        else:
                            with yattagdoc.tag("a", href=self.solved_url):
                                solved_explanation(yattagdoc)

                        with yattagdoc.tag("div", style="padding-left: 3em;"):
                            yattagdoc.asis(
                                '<script type="text/javascript">timeAgo(%s000);</script>'
                                % self.solved_gmtime
                            )
                            if (
                                self.solved_entry
                                and self._solved_entry_presentable
                                and not self._solved_entry_presentable == self.solved_entry[:12]
                            ):
                                yattagdoc.text(" in %s" % self._solved_entry_presentable)
                else:
                    latest_shown = False
                    earlier_patches = 0
                    for actievent in reversed(actievents_sorted):
                        if int(actievent.patchkind) == 0:
                            continue
                        if not latest_shown:
                            latest_shown = True
                            yattagdoc.text("Latest patch: ")
                            with yattagdoc.tag("a", href=actievent.url()):
                                yattagdoc.text("%s" % actievent.subject)
                            with yattagdoc.tag("div", style="padding-left: 3em;"):
                                yattagdoc.asis(
                                    '<script type="text/javascript">timeAgo(%s000);</script>'
                                    % actievent.gmtime
                                )
                                yattagdoc.text(", by %s; " % actievent.author)
                                if (
                                    PatchKind.DIFF | PatchKind.SUBJECT | PatchKind.SIGNEDOFF
                                ) in actievent.patchkind:
                                    yattagdoc.text("signed-off-by present")
                                elif (PatchKind.DIFF | PatchKind.SUBJECT) in actievent.patchkind:
                                    yattagdoc.text("proper patch")
                                else:
                                    yattagdoc.text("simple patch")
                            continue

                        if earlier_patches == 0:
                            yattagdoc.text("Earlier patches: ")
                        else:
                            yattagdoc.text(", ")

                        earlier_patches += 1
                        with yattagdoc.tag("a", href=actievent.url()):
                            yattagdoc.text("%s" % earlier_patches)

                with yattagdoc_line.tag("p"):
                    if len(actievents_sorted) == 0:
                        pass
                    elif len(actievents_sorted) > 5:
                        yattagdoc.text("Latest five known activities:")
                    else:
                        yattagdoc.text("All known activities:")
                    with yattagdoc_line.tag("ul", style="padding-left: 5px; margin-top: -1em;"):
                        for actievent in reversed(actievents_sorted[-5:]):
                            with yattagdoc.tag("li", style="list-style-position: inside;"):
                                actievent.html(yattagdoc, self)

                with yattagdoc_line.tag("p"):
                    yattagdoc.text("Regzbot command history:")
                    with yattagdoc_line.tag("ul", style="padding-left: 5px; margin-top: -1em;"):
                        for histevent in reversed(self._histevents):
                            with yattagdoc.tag("li", style="list-style-position: inside;"):
                                histevent.html(yattagdoc)

                if self.solved_reason:
                    return

                with yattagdoc.tag("p"):
                    yattagdoc.text(
                        "When fixing, add this to the commit message to make regzbot notice patch postings and commits to resolve the issue:"
                    )
                    with yattagdoc_line.tag(
                        "ul",
                        style="padding-left: 1em; margin-top: -1em; font-style: italic; list-style-type: none;",
                    ):
                        with yattagdoc.tag("li"):
                            # use self._introduced_url here, as that will avoid ranges and commits we could not find
                            if self._introduced_url:
                                commitsummary = regzbot.GitTree.commit_summary(self.introduced)
                                yattagdoc.text(
                                    'Fixes: %s ("%s")' % (self.introduced[0:12], commitsummary)
                                )

                        reports = []
                        for regression in self, *self._dupes:
                            if regression.regid in regids_just_forwarded:
                                continue
                            actireport = regression._actim_report
                            reportedlink = regzbot.ReportSource.get_by_id(actireport.repsrcid).url(
                                actireport.entry, redirector=True
                            )
                            if not actireport.authorname:
                                # there are a few old database entry where authorname and authormail are missing
                                # just ignore them
                                reportedby = ""
                            elif actireport.authorname == "Unknown":
                                reportedby = ""
                            elif not actireport.authormail:
                                reportedby = "Reported-by: %s" % (actireport.authorname,)
                            else:
                                reportedby = "Reported-by: %s <%s>" % (
                                    actireport.authorname,
                                    actireport.authormail,
                                )
                            reports.append((reportedby, reportedlink))

                        reports.sort(key=lambda x: x[0])
                        lastreporter = None
                        for report in reports:
                            if reportedby:
                                if not report[0] == lastreporter:
                                    lastreporter = report[0]
                                    with yattagdoc.tag("li"):
                                        yattagdoc.text(report[0])
                            with yattagdoc.tag("li"):
                                yattagdoc.text("Link: ")
                                with yattagdoc.tag("a", href=report[1]):
                                    yattagdoc.text(report[1])

        yattagdoc_line = yattag.Doc()
        with yattagdoc_line.tag("td", style="width: 200px;"):
            cell1(yattagdoc_line)
        with yattagdoc_line.tag("td"):
            cell2(yattagdoc_line)
        return yattagdoc_line


class UnhandledEventWeb(regzbot.UnhandledEvent):
    def __init__(self, *args):
        super().__init__(*args)

    def html(self, yattagdoc):
        def cell1(yattagdoc):
            yattagdoc.text("%s" % self.unhanid)

        def cell2(yattagdoc):
            if self.subject is not None:
                subj = self.subject
            else:
                subj = self.link
            with yattagdoc.tag("div"):
                with yattagdoc.tag("a", href=self.link):
                    yattagdoc.text(subj)
                    # delta_filed = days_delta( self.gmtime)

            with yattagdoc.tag("div"):
                yattagdoc.text(self.note)

        # put everything together
        with yattagdoc.tag("tr", style="vertical-align:top;"):
            with yattagdoc.tag("td"):
                cell1(yattagdoc)
            with yattagdoc.tag("td"):
                cell2(yattagdoc)


class RegExportWeb:
    eventslist = list()

    def __init__(
        self,
        repsrc,
        gmtime_report,
        gmtime_filed,
        gmtime_activity,
        gmtime_solved,
        treename,
        versionline,
        solved_reason,
        backburner,
        identified,
        htmlsnippet,
    ):
        self.repsrc = repsrc
        self.gmtime_report = gmtime_report
        self.gmtime_filed = gmtime_filed
        self.gmtime_activity = gmtime_activity
        self.gmtime_solved = gmtime_solved
        self.treename = treename
        self.versionline = versionline
        self.solved_reason = solved_reason
        self.backburner = backburner
        self.identified = identified
        self.htmlsnippet = htmlsnippet

    @staticmethod
    def outpage_header(yattagdoc, htmlpages, pagename, relpath=""):
        yattagdoc.asis(
            '<script src="%s../relativetime.js" type="text/javascript"></script>' % relpath
        )
        with yattagdoc.tag("h1"):
            yattagdoc.text("Linux kernel regression status")
        with yattagdoc.tag("h2"):
            description = None
            for htmlpage in htmlpages:
                # make it obvious that stable is about longterm, too
                if htmlpage == "stable":
                    description = "stable/longterm"
                else:
                    description = htmlpage

                # put a seperator here, because new and all contain
                # entries are also show on the previous pages
                if htmlpage == "new" or htmlpage == "all" or htmlpage == "resolved":
                    yattagdoc.text("•")
                    yattagdoc.asis("&nbsp;")

                # print
                if htmlpage == pagename:
                    yattagdoc.text("[%s]" % description)
                else:
                    with yattagdoc.tag("a", href="../%s%s/" % (relpath, htmlpage)):
                        yattagdoc.text("[%s]" % description)

                # seperate entries by space, unless we are at the end
                if not htmlpage == htmlpage[-1]:
                    yattagdoc.asis("&nbsp;")

    @staticmethod
    def outpage_table_span(
        yattagdoc, description, tablecolumns, horizontal_rule=False, strong=False, heading=False
    ):
        with yattagdoc.tag("tr"):
            if heading:
                htmltag = "tr"
            else:
                htmltag = "td"
            with yattagdoc.tag(
                htmltag, colspan=tablecolumns, style="text-align: left;  padding-bottom: 1em;"
            ):
                #            with yattagdoc.tag(htmltag, style="text-align: left;  padding-bottom: 1em;"):
                if horizontal_rule:
                    yattagdoc.asis("<hr>")
                if description is None:
                    return
                if strong:
                    yattagdoc.line("strong", description)
                else:
                    yattagdoc.text(description)

    @staticmethod
    def outpage_table_header_unhandled(yattagdoc):
        with yattagdoc.tag("tr", style="vertical-align:top;"):
            with yattagdoc.tag("th", align="left", style="width: 10px;"):
                yattagdoc.text("id")
            with yattagdoc.tag("th", align="left"):
                yattagdoc.text("place")

    @staticmethod
    def outpage_footer(yattagdoc, count):
        yattagdoc.asis("<hr>")

        with yattagdoc.tag("p", style="text-align: center"):
            yattagdoc.text("[compiled by ")
            with yattagdoc.tag("a", href="https://linux-regtracking.leemhuis.info"):
                yattagdoc.text("regzbot")
            currenttime = datetime.datetime.now(datetime.timezone.utc)
            yattagdoc.text(" on %s (UTC). " % currenttime.strftime("%Y-%m-%d %H:%M:%S"))

            yattagdoc.text("Wanna know more about regzbot? Then check out its ")
            with yattagdoc.tag(
                "a", href="https://gitlab.com/knurd42/regzbot/-/blob/main/docs/getting_started.md"
            ):
                yattagdoc.text("getting started guide")
            yattagdoc.text(" or its ")
            with yattagdoc.tag(
                "a", href="https://gitlab.com/knurd42/regzbot/-/blob/main/docs/reference.md"
            ):
                yattagdoc.text("reference documentation")
            yattagdoc.text(".]")

        if count > 0:
            with yattagdoc.tag("p", style="text-align: center"):
                yattagdoc.text("[recently ")
                with yattagdoc.tag("a", href="../unhandled/"):
                    if count == 1:
                        yattagdoc.text(
                            "%s event occurred that regzbot was unable to handle" % count
                        )
                    else:
                        yattagdoc.text(
                            "%s events occurred that regzbot was unable to handle" % count
                        )
                yattagdoc.text("]")

    @staticmethod
    def outpage_head(yattagdoc):
        yattagdoc.asis('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/></head>')
        return yattagdoc

    @staticmethod
    def outpage_write(subdir, yattagdoc):
        directory = os.path.join(regzbot.WEBPAGEDIR, subdir)
        regzbot.basicressource_checkdir_exists(directory, create=True)
        with open(os.path.join(directory, "index.html"), "w") as outputfile:
            if regzbot.is_running_citesting():
                # make this easier to read
                outputfile.write(yattag.indent(yattagdoc.getvalue()))
            else:
                outputfile.write(yattagdoc.getvalue())

    @classmethod
    def create_individual_page(cls, htmlpages, unhandled_count, regression):
        tablecolumns = 3
        yattagdoc = yattag.Doc()
        cls.outpage_head(yattagdoc)
        with yattagdoc.tag("body"):
            yattagdoc.asis('<base href="../../">')
            cls.outpage_header(yattagdoc, htmlpages, None)
            with yattagdoc.tag("table", style="width:100%;"):
                with yattagdoc.tag("tr", style="vertical-align:top;"):
                    yattagdoc.asis(regression.htmlsnippet.getvalue())
                    with yattagdoc.tag("td", style="width: 100px;"):
                        yattagdoc.text(regression.treename)

            yattagdoc.asis(
                "<script>document.getElementById('regression-details').open = true;</script>"
            )
            cls.outpage_footer(yattagdoc, unhandled_count)
            cls.outpage_write(
                "regression/%s/%s" % (regression.repsrc.generic_name, regression.repsrc.entryid),
                yattagdoc,
            )

    @classmethod
    def createpage_compilation(cls, htmlpages, unhandled_count, categories, pagename):
        tablecolumns = 3
        yattagdoc = yattag.Doc()
        cls.outpage_head(yattagdoc)
        with yattagdoc.tag("body"):
            cls.outpage_header(yattagdoc, htmlpages, pagename)
            with yattagdoc.tag("table", style="width:100%;"):
                for category in categories.keys():
                    # print section header
                    cls.outpage_table_span(
                        yattagdoc,
                        categories[category]["desc"],
                        tablecolumns,
                        horizontal_rule=True,
                        strong=True,
                    )
                    # check if the list for this section is empty
                    if not categories[category]["entries"]:
                        cls.outpage_table_span(yattagdoc, "none known by regzbot", tablecolumns)
                    # add html
                    for regressionweb in categories[category]["entries"]:
                        with yattagdoc.tag("tr", style="vertical-align:top;"):
                            yattagdoc.asis(regressionweb.htmlsnippet.getvalue())
                            if (
                                pagename == "all"
                                or pagename == "resolved"
                                or pagename == "inconclusive"
                            ):
                                with yattagdoc.tag("td", style="width: 100px;"):
                                    yattagdoc.text(regressionweb.treename)
            cls.outpage_footer(yattagdoc, unhandled_count)

            cls.outpage_write(pagename, yattagdoc)

    @classmethod
    def create_events(cls, directory, unhandled_count, htmlpages, eventslist):
        directory = os.path.join(regzbot.WEBPAGEDIR, "events")
        regzbot.basicressource_checkdir_exists(directory, create=True)

        yattagdoc = yattag.Doc()
        cls.outpage_head(yattagdoc)
        with yattagdoc.tag("html"):
            cls.outpage_header(yattagdoc, htmlpages, None)

            lastintro = None
            for event in eventslist:
                if lastintro is not event["htmlevent_intro"]:
                    lastintro = event["htmlevent_intro"]
                    yattagdoc.asis(event["htmlevent_intro"].getvalue())
                yattagdoc.asis(event["htmlevent_content"].getvalue())

            cls.outpage_footer(yattagdoc, 0)

        # write out
        with open(os.path.join(directory, "index.html"), "w") as outputfile:
            outputfile.write(yattagdoc.getvalue())

    @staticmethod
    def create_scriptfile_reldate():
        with open(os.path.join(regzbot.WEBPAGEDIR, "relativetime.js"), "w") as outputfile:
            outputfile.write("""const timeAgoCurrentDate = new Date()

const timeAgoFormatter = new Intl.RelativeTimeFormat("en", {
  numeric: 'always'
})

const timeAgoDIVS = [
  { amount: 60, offset: 300, name: 'minutes' },
  { amount: 24, offset: 72, name: 'hours' },
  { amount: 7, offset: 28, name: 'days' },
  { amount: 4.34524, offset: 16, name: 'weeks' },
  { amount: 12, offset: 48, name: 'months' },
  { amount: Number.POSITIVE_INFINITY, offset: Number.POSITIVE_INFINITY, name: 'years' }
]

function timeAgo(provided_date) {
  date = new Date(provided_date)
  let duration = (date - timeAgoCurrentDate) / 1000 / 60
  for (let i = 0; i <= timeAgoDIVS.length; i++) {
    if (Math.abs(duration) < timeAgoDIVS[i].offset) {
      // console.log('Time ago: ' + timeAgoFormatter.format(Math.round(duration), timeAgoDIVS[i].name))
      document.write(timeAgoFormatter.format(Math.round(duration), timeAgoDIVS[i].name))
      return(timeAgoFormatter.format(Math.round(duration), timeAgoDIVS[i].name))
    }
    duration /=  timeAgoDIVS[i].amount
  }
}
// timeAgo("2022-10-05T08:00:00Z")
""")

    @classmethod
    def create_unhandled(cls, directory, htmlpages):
        directory = os.path.join(regzbot.WEBPAGEDIR, "unhandled")
        regzbot.basicressource_checkdir_exists(directory, create=True)

        yattagdoc = yattag.Doc()
        cls.outpage_head(yattagdoc)
        with yattagdoc.tag("html"):
            cls.outpage_header(yattagdoc, htmlpages, None)

            unhandled_events = 0
            unhandled_html = yattag.Doc()
            for unhandled in UnhandledEventWeb.get_all():
                unhandled.html(unhandled_html)
                unhandled_events += 1

            if unhandled_events == 0:
                yattagdoc.text("No unhandled events known as of now")
            else:
                with yattagdoc.tag("table", style="width:100%;"):
                    cls.outpage_table_header_unhandled(yattagdoc)
                    yattagdoc.asis(unhandled_html.getvalue())

            cls.outpage_footer(yattagdoc, 0)

        # write out
        with open(os.path.join(directory, "index.html"), "w") as outputfile:
            outputfile.write(yattagdoc.getvalue())

        return unhandled_events

    @classmethod
    def categorize(cls, regressionlist):
        if regzbot.LATEST_VERSIONS["indevelopment"] == False:
            indevelopment_descriptive = "%s-post" % regzbot.LATEST_VERSIONS["latest"]
        else:
            indevelopment_descriptive = "%s-rc" % regzbot.LATEST_VERSIONS["indevelopment"]

        categories = {
            "next": {
                "identified": {
                    "desc": "culprit identified",
                    "entries": list(),
                },
                "default": {
                    "desc": "culprit unknown",
                    "entries": list(),
                },
                "backburner": {
                    "desc": "on back burner with activity in the past six months",
                    "entries": list(),
                },
            },
            "mainline": {
                "identified_indevelopment": {
                    "desc": "current cycle (%s.. aka %s), culprit identified"
                    % (regzbot.LATEST_VERSIONS["latest"], indevelopment_descriptive),
                    "entries": list(),
                },
                "unidentified_indevelopment": {
                    "desc": "current cycle (%s.. aka %s), unknown culprit"
                    % (regzbot.LATEST_VERSIONS["latest"], indevelopment_descriptive),
                    "entries": list(),
                },
                "identified_latest": {
                    "desc": "previous cycle (%s..%s), culprit identified, with activity in the past three months"
                    % (regzbot.LATEST_VERSIONS["previous"], regzbot.LATEST_VERSIONS["latest"]),
                    "entries": list(),
                },
                "identified_old": {
                    "desc": "older cycles (..%s), culprit identified, with activity in the past three months"
                    % regzbot.LATEST_VERSIONS["previous"],
                    "entries": list(),
                },
                "unidentified_latest": {
                    "desc": "previous cycle (%s..%s), unknown culprit, with activity in the past three weeks"
                    % (regzbot.LATEST_VERSIONS["previous"], regzbot.LATEST_VERSIONS["latest"]),
                    "entries": list(),
                },
                "unidentified_old": {
                    "desc": "older cycles (..%s), unknown culprit, with activity in the past three weeks"
                    % regzbot.LATEST_VERSIONS["previous"],
                    "entries": list(),
                },
                "default": {
                    "desc": "all others with unknown culprit and activity in the past three months",
                    "entries": list(),
                },
                "backburner": {
                    "desc": "on back burner with activity in the past six months",
                    "entries": list(),
                },
            },
            "stable": {
                "identified": {
                    "desc": "culprit identified",
                    "entries": list(),
                },
                "default": {
                    "desc": "culprit unknown",
                    "entries": list(),
                },
                "backburner": {
                    "desc": "on back burner with activity in the past six months",
                    "entries": list(),
                },
            },
            "inconclusive": {
                "default": {
                    "desc": "",
                    "entries": list(),
                },
            },
            "resolved": {
                "default": {
                    "desc": "",
                    "entries": list(),
                },
            },
        }

        for regression in regressionlist:
            last_activity_days = regzbot.days_delta(regression.gmtime_activity)
            if regression.solved_reason == "inconclusive":
                categories["inconclusive"]["default"]["entries"].append(regression)
            elif regression.gmtime_solved:
                categories["resolved"]["default"]["entries"].append(regression)
            elif (
                regression.backburner and last_activity_days < 180
            ):  # things on backburner are allowed to get a little older
                categories[regression.treename]["backburner"]["entries"].append(regression)
            elif last_activity_days > 90:
                categories["inconclusive"]["default"]["entries"].append(regression)
            elif regression.treename == "next" or regression.treename == "stable":
                if regression.identified:
                    categories[regression.treename]["identified"]["entries"].append(regression)
                else:
                    categories[regression.treename]["default"]["entries"].append(regression)
            elif regression.treename == "mainline":
                if regression.versionline == "indevelopment":
                    if regression.identified:
                        categories[regression.treename]["identified_indevelopment"][
                            "entries"
                        ].append(regression)
                    else:
                        categories[regression.treename]["unidentified_indevelopment"][
                            "entries"
                        ].append(regression)
                elif regression.versionline == "latest" and regression.identified:
                    categories[regression.treename]["identified_latest"]["entries"].append(
                        regression
                    )
                elif regression.versionline == "latest" and last_activity_days < 21:
                    categories[regression.treename]["unidentified_latest"]["entries"].append(
                        regression
                    )
                elif regression.identified:
                    categories[regression.treename]["identified_old"]["entries"].append(regression)
                elif last_activity_days < 21:
                    categories[regression.treename]["unidentified_old"]["entries"].append(
                        regression
                    )
                else:
                    categories[regression.treename]["default"]["entries"].append(regression)
            else:
                # this should not happen, but in case it does due to later code changes:
                categories["mainline"]["default"]["entries"].append(regression)

        return categories

    @classmethod
    def regression_to_json(cls, regression):
        regzbot_commands = list()
        for h in regression._histevents:
            regzbot_commands.append(
                {
                    "command": h.regzbotcmd,
                    "author": h.author,
                    "url": h.url(),
                    "timestamp": datetime.datetime.fromtimestamp(
                        h.gmtime, datetime.timezone.utc
                    ).isoformat(),
                }
            )

        if regression.solved_gmtime:
            solved_tstamp = datetime.datetime.fromtimestamp(
                regression.solved_gmtime, datetime.timezone.utc
            ).isoformat()
        else:
            solved_tstamp = None
        solved = {
            "reason": regression.solved_reason,
            "commit": regression.solved_entry,
            "subject": regression.solved_subject,
            "url": regression.solved_url,
            "git_readable": regression._solved_entry_presentable,
            "timestamp": solved_tstamp,
        }

        return {
            "id": regression.regid,
            "introduced": regression.introduced,
            "subject": regression.subject,
            "tree": regression.treename,
            "timestamp": datetime.datetime.fromtimestamp(
                regression.gmtime_filed, datetime.timezone.utc
            ).isoformat(),
            "url_regzbot": "https://linux-regtracking.leemhuis.info/regzbot/regression/%s/%s/"
            % (
                regression._actim_report.repsrc.generic_name,
                regression._actim_report.repsrc.entryid,
            ),
            "identified": regression.identified,
            "introduced_url": regression._introduced_url,
            "regzbot_commands": regzbot_commands,
            "solved": solved,
        }

    @classmethod
    def compile(cls):
        logger.debug("[webpages] generating")

        # these are the pages we are going to create
        htmlpages = ("next", "mainline", "stable", "new", "all", "resolved", "inconclusive")

        # handle this page first, as we need something from it anyway
        unhandled_count = cls.create_unhandled(regzbot.WEBPAGEDIR, htmlpages)

        # gather everything we need
        regressionslist = list()
        eventslist = list()
        events_gmtime_offset = (
            int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - 604800
        )
        if regzbot.is_running_citesting("offline"):
            events_gmtime_offset = 604800 * 52 * 10

        json_data = list()
        for regression in RegressionWeb.get_all():
            json_data.append(cls.regression_to_json(regression))
            event_intro = regression.event_intro()
            for event in regression.events(events_gmtime_offset, event_intro):
                eventslist.append(event)

            gmtime_solved = None
            if (
                regression.solved_reason == "fixed"
                or regression.solved_reason == "resolved"
                or regression.solved_reason == "invalid"
                or regression.solved_reason == "inconclusive"
                or regression.solved_reason == "duplicateof"
                or regression.solved_duplicateof
            ):
                gmtime_solved = regression.solved_gmtime
            if regression._actievents:
                last_activity = regression._actievents[-1].gmtime
            else:
                last_activity = regression._histevents[-1].gmtime
            regressionslist.append(
                cls(
                    regression._actim_report.repsrc,
                    regression.gmtime,
                    regression.gmtime_filed,
                    last_activity,
                    gmtime_solved,
                    regression.treename,
                    regression.versionline,
                    regression.solved_reason,
                    regression.backburner,
                    regression.identified,
                    regression.html(),
                )
            )

        cls.create_scriptfile_reldate()

        eventslist.sort(key=lambda x: x["gmtime"], reverse=True)
        cls.create_events(regzbot.WEBPAGEDIR, unhandled_count, htmlpages, eventslist)
        # we don't need this anymore now that we iterated over all regressions
        eventslist = events_gmtime_offset = None

        # create the page listing all regressions, sorted by date
        regressionslist.sort(key=lambda x: x.gmtime_report, reverse=True)
        categories = {
            "default": {
                "desc": "sorted by date of report",
                "entries": regressionslist,
            }
        }
        cls.createpage_compilation(htmlpages, unhandled_count, categories, "all")

        # create the page listing new regressions, sorted by date
        categories = {
            "next": {
                "desc": "next",
                "entries": list(),
            },
            "mainline": {
                "desc": "mainline",
                "entries": list(),
            },
            "stable": {
                "desc": "stable/longterm",
                "entries": list(),
            },
        }
        for regression in regressionslist:
            if regression.gmtime_solved:
                continue
            filed_days = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.datetime.fromtimestamp(regression.gmtime_filed, datetime.timezone.utc)
            ).days
            if filed_days < 7:
                categories[regression.treename]["entries"].append(regression)
            else:
                break
        cls.createpage_compilation(htmlpages, unhandled_count, categories, "new")

        # create the indivudal pages
        for regression in regressionslist:
            cls.create_individual_page(htmlpages, unhandled_count, regression)

        # create all the other pages that are sorted by activity
        regressionslist.sort(key=lambda x: x.gmtime_activity, reverse=True)
        categories = cls.categorize(regressionslist)
        for pagename in categories.keys():
            cls.createpage_compilation(htmlpages, unhandled_count, categories[pagename], pagename)

        # create default
        with open(os.path.join(regzbot.WEBPAGEDIR, "index.html"), "w") as outputfile:
            outputfile.write("<head><meta http-equiv='refresh' content='0; URL=mainline/'></head>")

        if not regzbot.is_running_citesting():
            publishscript = os.path.join(
                pathlib.Path.home(), ".local/share/regzbot/", "pusblishwebsites.sh"
            )
            if os.path.exists(publishscript):
                os.system(publishscript)

        with open(os.path.join(regzbot.WEBPAGEDIR, "regressions.json"), "w") as jsonfile:
            jsonfile.write(json.dumps(json_data))

        logger.debug("[webpages] generated")
