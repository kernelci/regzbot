#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

from collections import Counter
import datetime
from email.message import EmailMessage
import email.utils
import tempfile
import textwrap
import os

import regzbot

logger = regzbot.logger


class RegLinkMailReport(regzbot.RegLink):
    def __init__(self, *args):
        super().__init__(*args)

    def mailreport(self):
        if self.author:
            monitored = ""
            if (
                self.repsrcid
                and self.entry
                and regzbot.RegActivityMonitor.ismonitored(self.entry, self.regid, self.repsrcid)
            ):
                monitored = "; thread monitored."
            authored = "\n  %s days ago, by %s%s" % (
                regzbot.days_delta(self.gmtime),
                self.author,
                monitored,
            )
        else:
            authored = ""

        if self.subject == self.link:
            return "* %s%s" % (self.subject, authored)
        return "* %s\n  %s%s" % (self.subject, self.link, authored)


class RegressionMailReport(regzbot.RegressionFull):
    Reglink = RegLinkMailReport

    def __init__(self, *args):
        super().__init__(*args)

    def compile(self, lastreport_gmtime):
        if lastreport_gmtime < self.gmtime_filed:
            subject = "[ *NEW* ] %s" % self.subject
        else:
            subject = self.subject

        report = list()
        report.append(subject)
        report.append("-" * len(subject))
        report.append(
            "https://linux-regtracking.leemhuis.info/regzbot/regression/%s/%s/"
            % (self._actim_report.repsrc.generic_name, self._actim_report.repsrc.entryid)
        )
        report.append(
            regzbot.ReportSource.get_by_id(self._actim_report.repsrcid).url(
                self._actim_report.entry
            )
        )
        for regression in self._dupes:
            report.append(
                regzbot.ReportSource.get_by_id(regression._actim_report.repsrcid).url(
                    regression._actim_report.entry
                )
            )

        statusline = []
        actireports = list()
        for regression in self, *self._dupes:
            actireports.append(regression._actim_report)

        statusline.append("\nBy ")
        for actireport in actireports:
            if actireport.authorname:
                statusline.append(actireport.authorname)
            else:
                statusline.append("Unknown")
            if len(actireports) > 2 and actireport == actireports[-2]:
                statusline.append(", and ")
            elif len(actireports) > 1 and actireport == actireports[-2]:
                statusline.append(" and ")
            elif actireport == actireports[-1]:
                pass
            else:
                statusline.append(", ")

        statusline.append("; ")
        statusline.append(str(regzbot.days_delta(self.gmtime)))
        statusline.append(" days ago; ")
        statusline.append(str(len(self._actievents)))
        statusline.append(" activities")
        if len(self._actievents) > 0:
            statusline.append(", latest ")
            statusline.append(str(regzbot.days_delta(self._actievents[-1].gmtime)))
            statusline.append(" days ago")

        if self.poked:
            statusline.append("; poked %s days ago" % regzbot.days_delta(self.poked.gmtime))
        statusline.append(".")
        report.append("".join(statusline))

        report = self.add_introduced(report)
        if self.solved_reason:
            report = self.add_fix(report)
        else:
            report = self.add_involved(report, lastreport_gmtime)
            report = self.add_latestpatch(report)
            report = self.add_links(report)
        report.append("")
        return report

    def add_introduced(self, report):
        presentable = ""
        if self._introduced_presentable:
            presentable = " (%s)" % self._introduced_presentable
        report.append("Introduced in %s%s" % (self._introduced_short, presentable))
        return report

    def add_fix(self, report):
        # reminder: we only get those where solved_reason is 'to_be_fixed'
        report.append("\nFix incoming:")
        if self.solved_subject:
            report.append("* %s" % self.solved_subject)
            if self.solved_url is not None:
                report.append("  %s" % self.solved_url)
        else:
            report.append("* %s" % self.solved_url)
        return report

    def add_latestpatch(self, report):
        patchcount = 0
        for actievent in self._actievents:
            if int(actievent.patchkind) > 0:
                patchcount += 1

        for actievent in reversed(self._actievents):
            if int(actievent.patchkind) == 0:
                continue

            if patchcount == 1:
                report.append("\nOne patch associated with this regression:")
            else:
                report.append(
                    "\n%s patch postings are associated with this regression, the latest is this:"
                    % patchcount
                )

            # avoid mentioning a patch twice
            for link in self._links:
                if link.entry == actievent.entry:
                    # taking the stuff from the link will get us the monitor status
                    report.append(link.mailreport())
                    self._links.remove(link)
                    return report

            report.append("* %s" % actievent.subject)
            report.append("  %s" % actievent.url())
            report.append(
                "  %s days ago, by %s" % (regzbot.days_delta(actievent.gmtime), actievent.author)
            )

            break

        return report

    def add_involved(self, report, lastreport_gmtime):
        involved = []
        for actievent in reversed(self._actievents):
            if actievent.gmtime < lastreport_gmtime:
                break
            involved.append(actievent.author)

        if len(involved) > 0:
            counted = Counter(involved)
            involved = ""
            prefix = ""
            for name, count in counted.most_common():
                involved += "%s%s (%s)" % (prefix, name, count)
                if prefix == "":
                    prefix = ", "

            wrapped = [""]
            wrapped.extend(
                textwrap.wrap(
                    "Recent activities from: %s" % involved, width=72, subsequent_indent="  "
                )
            )
            report.append("\n".join(wrapped))
        return report

    def add_links(self, report):
        if not self._links:
            return report

        report.append("\nNoteworthy links:")
        for link in self._links:
            report.append(link.mailreport())
        return report

    def mailreport(self, lastreport_gmtime):
        return "\n".join(self.compile(lastreport_gmtime))


class RegExportMailReport:
    def __init__(
        self,
        entry,
        gmtime_report,
        gmtime_filed,
        gmtime_activity,
        treename,
        versionline,
        backburner,
        identified,
        reporttext,
    ):
        self.entry = entry
        self.gmtime_report = gmtime_report
        self.gmtime_filed = gmtime_filed
        self.gmtime_activity = gmtime_activity
        self.treename = treename
        self.versionline = versionline
        self.backburner = backburner
        self.identified = identified
        self.reporttext = reporttext

    @classmethod
    def __create_mail(cls, content, treename):
        msg = EmailMessage()
        msg["To"] = (
            "LKML <linux-kernel@vger.kernel.org>, Linus Torvalds <torvalds@linux-foundation.org>, Linux regressions mailing list <regressions@lists.linux.dev>"
        )
        msg["Subject"] = "%s for %s [%s]" % (
            regzbot.REPORT_SUBJECT_PREFIX,
            treename,
            datetime.date.today(),
        )
        msg["Date"] = email.utils.localtime()
        msg["Message-ID"] = email.utils.make_msgid(domain="leemhuis.info")
        msg.set_content(content, cte="quoted-printable")
        return msg

    @classmethod
    def pagecreate(cls, categories, treename, lastreport_msgid):
        def repintro(report, number_issues, treename):
            intro = list()

            print("Enter/Paste your intro for %s and hit Ctrl-D to save it." % treename)
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                intro.append(line)
            if report:
                intro.append("\n---\n")

            intro.append("Hi, this is regzbot, the Linux kernel regression tracking bot.")
            intro.append(
                "\nCurrently I'm aware of %s regressions in linux-%s. Find the"
                % (number_issues, treename)
            )
            intro.append("current status below and the latest on the web:")
            intro.append("\nhttps://linux-regtracking.leemhuis.info/regzbot/%s/" % treename)
            intro.append("\nBye bye, hope to see you soon for the next report.")
            intro.append("   Regzbot (on behalf of Thorsten Leemhuis)")
            intro.append("\n")
            report.insert(0, "\n".join(intro))
            return report

        def repsectionheader(report, headline):
            report.append("=" * len(headline))
            report.append(headline)
            report.append("=" * len(headline))
            report.append("")
            return report

        def repfooter(report, lastreport_msgid):
            intro = "All regressions marked '[ *NEW* ]' were added since the previous report"
            if not lastreport_msgid:
                report.append("%s." % intro)
            else:
                report.append("%s," % intro)
                report.append("which can be found here:")
                report.append("https://lore.kernel.org/r/%s\n" % lastreport_msgid)
            intro = None
            report.append("Thanks for your attention, have a nice day!")
            report.append("\n  Regzbot, your hard working Linux kernel regression tracking robot")
            report.append(
                "\n\nP.S.: Wanna know more about regzbot or how to use it to track regressions"
            )
            report.append("for your subsystem? Then check out the getting started guide or the")
            report.append("reference documentation:")
            report.append(
                "\nhttps://gitlab.com/knurd42/regzbot/-/blob/main/docs/getting_started.md"
            )
            report.append("https://gitlab.com/knurd42/regzbot/-/blob/main/docs/reference.md")
            report.append("\nThe short version: if you see a regression report you want to see")
            report.append("tracked, just send a reply to the report where you Cc")
            report.append("regressions@lists.linux.dev with a line like this:")
            report.append("\n#regzbot introduced: v5.13..v5.14-rc1")
            report.append("\nIf you want to fix a tracked regression, just do what is expected")
            report.append("anyway: add a 'Link:' tag with the url to the report, e.g.:")
            report.append(
                "\nLink: https://lore.kernel.org/all/30th.anniversary.repost@klaava.Helsinki.FI/"
            )
            return report

        number_issues = 0
        report = list()

        if treename == "resolved" or treename == "unassociated" or treename == "dormant":
            # no reports for those
            return report
        elif treename == "next" or treename == "stable":
            # ignore those for now; when changing this, remember to update
            # the regzbot.RegzbotState.set stuff as well
            return report

        for category in categories.keys():
            if not categories[category]["entries"]:
                # nothing to do
                continue

            number_issues += len(categories[category]["entries"])

            # if category == 'default':
            #    report = repsectionheader(report, 'Regressions with unkown culprit without activitiy in the past three weeks')
            #    report.append("There are a %s more regressions from older cycles with unkown culprit that omitted here due to lack of activity in the past three weeks:" % len(['entries']))
            #    report.append("https://linux-regtracking.leemhuis.info/regzbot/%s/" % treename)
            #    report.append('\n')
            # else:
            if True:
                report = repsectionheader(report, categories[category]["desc"])
                report.append("")
                for regexportreport in categories[category]["entries"]:
                    report.append(regexportreport.reporttext)
                    report.append("")

        # add footer and header
        report = repsectionheader(report, "End of report")
        report = repfooter(report, lastreport_msgid)
        report = repintro(report, number_issues, treename)

        return "\n".join(report)

    @classmethod
    def categorize(cls, regressionlist, lastreport_gmtime):
        # some lines are commented out below to keep code similar to the one used in export_web,
        # as it shows a few regressions that don't make it into the reports

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
                    "desc": "on back burner",
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
                    "desc": "on back burner, but with activity since the last report",
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
            },
            "unassociated": {
                "default": {
                    "desc": "",
                    "entries": list(),
                },
            },
            "dormant": {
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
            filed_days = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.datetime.fromtimestamp(regression.gmtime_filed, datetime.timezone.utc)
            ).days
            last_activity_days = regzbot.days_delta(regression.gmtime_activity)

            if regression.backburner:
                if lastreport_gmtime > regression.gmtime_activity:
                    # ignore these
                    continue
                elif regression.treename == "next" or regression.treename == "stable":
                    # only create reports for mainline for now
                    continue
                categories[regression.treename]["backburner"]["entries"].append(regression)
            elif last_activity_days > 90:
                continue
            elif regression.treename == "next" or regression.treename == "stable":
                #
                # for now only create reports for mainline regressions
                continue
                #
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
                #
                # for now only create reports for regression introduced in the current cycle
                elif True:
                    continue
                #
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
                categories["unassociated"]["default"]["entries"].append(regression)

        return categories

    @classmethod
    def compile(cls):
        logger.debug("[reportmail] generating")

        lastreport_msgid = regzbot.RegzbotState.get("lastreport_mainline_msgid")
        lastreport_gmtime = regzbot.RegzbotState.get("lastreport_mainline_gmtime")
        if lastreport_gmtime:
            lastreport_gmtime = int(lastreport_gmtime)
        else:
            lastreport_gmtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        logger.debug("[reportmail] lastreport was %s" % lastreport_gmtime)

        # gather everything we need
        regressionslist = list()

        for regression in RegressionMailReport.get_all(only_unsolved=True):
            # ignore some
            if regression._actievents:
                last_activity = regression._actievents[-1].gmtime
            else:
                last_activity = regression._histevents[-1].gmtime
            last_activity_days = regzbot.days_delta(last_activity)
            if regression._actievents:
                last_activity = regression._actievents[-1].gmtime
            else:
                last_activity = regression._histevents[-1].gmtime
            regressionslist.append(
                cls(
                    regression._actim_report.entry,
                    regression.gmtime,
                    regression.gmtime_filed,
                    last_activity,
                    regression.treename,
                    regression.versionline,
                    regression.backburner,
                    regression.identified,
                    regression.mailreport(lastreport_gmtime),
                )
            )

        regressionslist.sort(key=lambda x: x.gmtime_activity, reverse=True)
        categories = cls.categorize(regressionslist, lastreport_gmtime)

        report_gmtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        with tempfile.TemporaryDirectory() as tmpdirname:
            for counter, treename in enumerate(categories.keys()):
                report = cls.pagecreate(categories[treename], treename, lastreport_msgid)

                if not report:
                    logger.info("Nothing to report for %s" % treename)
                    continue

                filename = os.path.join(tmpdirname, "%s-regzbotreport-%s" % (counter, treename))
                msg = cls.__create_mail(report, treename)
                lastreport_msgid = msg["Message-ID"].strip("<>")
                print("#" * 120)
                print("\n%s\n" % filename)
                print("#" * 120)
                print(report)
                with open(filename, "w") as out:
                    gen = email.generator.Generator(out)
                    gen.flatten(msg)

            print("#" * 120)

            print(
                "Review the reports in %s and sent them using \"git send-email --from='Regzbot (on behalf of Thorsten Leemhuis) <regressions@leemhuis.info>' --suppress-cc=self --to '' %s/*\""
                % (tmpdirname, tmpdirname)
            )
            answer = input("Enter c to confirm you sent the report, anything else to abort: ")
            if answer.lower() != "c":
                return
            regzbot.RegzbotState.set("lastreport_mainline_gmtime", report_gmtime)
            regzbot.RegzbotState.set("lastreport_mainline_msgid", lastreport_msgid)
            lastreport_msgid = regzbot.RegzbotState.get("lastreport_mainline_msgid")

        logger.debug("[report] generated")
