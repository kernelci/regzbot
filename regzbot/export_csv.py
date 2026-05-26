#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"


import regzbot

logger = regzbot.logger


class RegLinkCSV(regzbot.RegLink):
    def __init__(self, *args):
        super().__init__(*args)

    def csv(self):
        if (
            self.repsrcid
            and self.entry
            and regzbot.RegActivityMonitor.ismonitored(self.entry, self.regid, self.repsrcid)
        ):
            return "%s, %s [monitored]" % (self.subject, self.link)
        return "%s, %s, %s, %s" % (self.subject, self.link, self.author, self.gmtime)


class RegHistoryCSV(regzbot.RegHistory):
    def __init__(self, *args):
        super().__init__(*args)

    def csv(self):
        return "%s, %s, %s, %s, %s" % (
            self.subject,
            self.gmtime,
            self.author,
            self.url(),
            self.regzbotcmd,
        )


class RegActivityEventCSV(regzbot.RegActivityEvent):
    def __init__(self, *args):
        super().__init__(*args)

    def csv(self):
        return "%s, %s, %s, %s, PatchKind(%s)" % (
            self.subject,
            self.author,
            self.url(),
            self.gmtime,
            self.patchkind.name,
        )


class RegressionFullCSV(regzbot.RegressionFull):
    Reglink = RegLinkCSV
    Reghistory = RegHistoryCSV
    Regactivityevent = RegActivityEventCSV

    def __init__(self, *args):
        super().__init__(*args)

    def compile(self):
        compiled = list()
        compiled = self.add_basics(compiled)
        compiled = self.add_links(compiled)
        compiled = self.add_solved(compiled)
        compiled = self.add_activity(compiled)
        compiled = self.add_history(compiled)
        compiled = self.add_latest(compiled)
        return compiled

    def add_basics(self, compiled):
        flags = []
        if self.identified:
            flags.append("culprit indentified")
        if self.poked:
            flags.append("poked")
        if self.backburner:
            flags.append("back-burner")

        if len(flags) == 0:
            flags.append("no flags")
        compiled.append(
            "REGRESSION: %s, %s (%s), %s, %s, %s, %s: %s"
            % (
                self.subject,
                self._introduced_short,
                self._introduced_presentable,
                self._introduced_url,
                self.treename,
                self._branchname,
                self.versionline,
                ", ".join(flags),
            )
        )

        reportlist = list()
        for regression in self, *self._dupes:
            report = regression._actim_report
            content = "%s, %s, %s, %s, %s" % (
                report.gmtime,
                report.subject,
                report.authorname,
                report.authormail,
                regzbot.ReportSource.get_by_id(report.repsrcid).url(report.entry),
            )
            if report == self._actim_report:
                reportlist.insert(0, "INITIAL_REPORT: %s" % content)
            else:
                reportlist.append("ADDITIONAL_REPORT: %s" % content)

        compiled.extend(reportlist)
        return compiled

    def add_solved(self, compiled):
        if self.solved_duplicateof:
            duplicatetext = " [duplicate of %s]" % self.solved_duplicateof
        else:
            duplicatetext = ""

        if self.solved_reason or self.solved_duplicateof:
            compiled.append(
                "SOLVED: %s, %s, %s, %s, %s%s"
                % (
                    self.solved_reason,
                    self.solved_gmtime,
                    self._solved_entry_presentable,
                    self.solved_url,
                    self.solved_subject,
                    duplicatetext,
                )
            )
        return compiled

    def add_links(self, compiled):
        for link in self._links:
            compiled.append("LINK: %s" % link.csv())
        return compiled

    def add_activity(self, compiled):
        for actievent in self._actievents:
            compiled.append("ACTIVITY: %s" % actievent.csv())
        return compiled

    def add_history(self, compiled):
        for histevent in self._histevents:
            compiled.append("HISTORY: %s" % histevent.csv())
        return compiled

    def add_latest(self, compiled):
        if self._actievents:
            compiled.append("LATEST: " + self._actievents[-1].csv())
        return compiled

    def dump(self):
        return "\n".join([*self.compile(), ""])


class UnhandledEventCSV(regzbot.UnhandledEvent):
    def __init__(self, *args):
        super().__init__(*args)

    def dump(self):
        return "UNHANDLED: %s, %s, %s, %s, %s, %s, %s, %s, %s\n" % (
            self.unhanid,
            self.link,
            self.note,
            self.gmtime,
            self.regid,
            self.subject,
            self.solved_gmtime,
            self.solved_link,
            self.solved_subject,
        )


def dumpall_csv(order="regid"):
    for regression in RegressionFullCSV.get_all(order=order):
        yield regression.dump()
    for unhandled_event in UnhandledEventCSV.get_all():
        yield unhandled_event.dump()


def main():
    for dumped_regression in dumpall_csv():
        print(dumped_regression)
