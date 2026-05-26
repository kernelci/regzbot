#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2022 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import re

if __name__ != "__main__":
    import regzbot

    logger = regzbot.logger
else:
    import logging

    logger = logging
    # if False:
    if True:
        logger.basicConfig(level=logging.DEBUG)


class RegressionCreatedException(Exception):
    pass


class RegressionNotFound(Exception):
    pass


class RbCmdSingleNew:
    def __init__(self, rbcmd_stack, cmd, parameters):
        self._rbcmd_stack = rbcmd_stack
        self.cmd = cmd.lower()
        self.parameters = parameters

        # handle frequent typos, alternatives, and renamed commands
        if self.cmd in ("backburner", "back-burner"):
            self.cmd = "backburn"
        elif self.cmd in ("dup", "dupof", "dup-of", "duplicate-of"):
            self.cmd = "duplicate"
        elif self.cmd in ("fixedby", "fixed-by"):
            self.cmd = "fix"
        elif re.match("(link|relat(ed)?-?brief(ly)?)", self.cmd):
            self.cmd = "relatebrief"
        elif self.cmd in ("monitor", "related"):
            self.cmd = "relate"
        elif self.cmd in ("resolved", "invalid"):
            self.cmd = "resolve"
        elif self.cmd in ("subject", "title"):
            self.cmd = "summary"
        elif self.cmd in ("unlink", "unmonitor", "unrelated"):
            self.cmd = "unrelate"
        elif self.cmd in ("unback-burner", "back-burner"):
            self.cmd = "unbackburn"

    @property
    def repact(self):
        return self._rbcmd_stack.repact

    @property
    def reptrd(self):
        return self._rbcmd_stack.reptrd

    def _parse_link_and_description(self, pattern):
        splitted = pattern.split(maxsplit=1)
        url = splitted[0]
        if len(splitted) > 1:
            description = splitted[1]
        else:
            description = None
        return url, description

    def _cmd_backburn(self, regression):
        reason = self.parameters
        regression.cmd_backburn(self, reason)

    def _cmd_duplicate(self, regression):
        if not regression:
            return self._cmd_duplicate_this()
        self._cmd_duplicate_overthere(regression)
        return regression

    def _cmd_duplicate_overthere(self, regression):
        for url in self.parameters.split():
            reptrd_other = regzbot.ReportThread.from_url(url, repact=self._rbcmd_stack.repact)
            regression_created = regression.cmd_duplicate(self, reptrd_other)
            if regression_created:
                self._rbcmd_stack.add_related_activities(reptrd_other, regression_created)

    def _cmd_duplicate_this(self):
        reptrd_other = regzbot.ReportThread.from_url(
            self.parameters.split()[0], repact=self._rbcmd_stack.repact
        )
        regression_other = None
        for actimon in regzbot.RegActivityMonitor.get_by_reptrd(reptrd_other):
            if actimon.regid:
                regression_other = regzbot.RegressionBasic.get_by_regid(actimon.regid)
                break
        if regression_other:
            return regression_other.cmd_duplicate(self, self.reptrd)

    def _cmd_fix(self, regression):
        def _remove_quoting_chars(pattern):
            for character in (("(", ")"), "'", '"'):
                if pattern.startswith(character[0]) and pattern.endswith(character[-1]):
                    pattern = pattern[1:-1]
            return pattern

        match = re.search(r"(^[0-9a-fA-F]{8,40})\s?(.*)?", self.parameters)
        if match:
            hexsha = match[1]
            if match[2]:
                summary = match[2]
            else:
                summary = None
        else:
            hexsha = None
            summary = _remove_quoting_chars(self.parameters)
        regression.cmd_fix(self, hexsha, summary)

    def _cmd_from(self, regression):
        if "<" in self.parameters and ">" in self.parameters:
            from email.utils import parseaddr

            realname, username = parseaddr(self.parameters)
        else:
            realname = self.parameters
            username = None
        regression.cmd_from(self, realname, username)

    def _cmd_inconclusive(self, regression):
        regression.cmd_resolve(self, self.parameters)

    def _cmd_introduced(self, regression):
        hexsha = self.parameters
        if regression:
            regression.cmd_introduced_update(self, hexsha)
            return None
        return regzbot.RegressionBasic.cmd_introduced_new(self, hexsha)

    def _cmd_relate(self, regression):
        url, description = self._parse_link_and_description(self.parameters)
        try:
            regression.cmd_monitor(self, url, description)
        except regzbot.RepDownloadError:
            regzbot.UnhandledEvent.add(
                self.repact.web_url,
                "unable to relate thread %s, download failed" % url,
                gmtime=self.repact.gmtime,
                subject=self.repact.summary,
            )

    def _cmd_relatebrief(self, regression):
        url, description = self._parse_link_and_description(self.parameters)
        regression.cmd_link(self, url, description)

    def _cmd_resolve(self, regression):
        regression.cmd_resolve(self, self.parameters)

    def _cmd_summary(self, regression):
        regression.title(self.parameters)

    def _cmd_unbackburn(self, regression):
        regression.cmd_unbackburn(self)

    def _cmd_unrelate(self, regression):
        url, _ = self._parse_link_and_description(self.parameters)
        try:
            if not regression.cmd_unlink(self, url):
                regzbot.UnhandledEvent.add(
                    self.repact.web_url,
                    "unable to unrelate thread %s, not related yet" % url,
                    gmtime=self.repact.gmtime,
                    subject=self.repact.summary,
                )
                return False
        except regzbot.RepDownloadError:
            regzbot.UnhandledEvent.add(
                self.repact.web_url,
                "unable to unrelate thread %s, parsing failed" % url,
                gmtime=self.repact.gmtime,
                subject=self.repact.summary,
            )

    def process(self, regression, regression_topmost_duplicate):
        regression_created = None
        succeeded = None

        if self.cmd == "ignore-activity":
            # this is a flag handled when processing activities, so nothing to do here
            return
        elif self.cmd in ("poke", "note"):
            # nothing to do here, the entry in the history is enough
            pass
        elif self.cmd == "duplicate" and not regression:
            regression = self._cmd_duplicate(None)
        elif self.cmd == "introduced" and not regression:
            regression_created = self._cmd_introduced(None)
            regression = regression_created
        elif self.cmd in (
            "backburn",
            "duplicate",
            "fix",
            "from",
            "inconclusive",
            "introduced",
            "relate",
            "relatebrief",
            "resolve",
            "summary",
            "unbackburn",
            "unrelate",
        ):
            succeeded = getattr(self, "_cmd_%s" % self.cmd)(regression)
            if regression_topmost_duplicate and self.cmd not in (
                "relate",
                "relatebrief",
                "duplicate",
            ):
                # some command needs to act on topmost regression as well
                getattr(self, "_cmd_%s" % self.cmd)(regression_topmost_duplicate)
                regression_topmost_duplicate.add_history_event(self)
        else:
            regzbot.UnhandledEvent.add(
                self.repact.web_url,
                "unknown regzbot command: %s" % self.cmd,
                gmtime=self.repact.gmtime,
                subject=self.repact.summary,
            )
            return

        # create the history event and let caller know if we created a regression
        if succeeded is not False:
            regression.add_history_event(self)
        return regression_created


class RbCmdStackNew:
    def __init__(self, repact, regression):
        self._commands = []
        self.repact = repact
        self.reptrd = repact.reptrd
        self.regression = None
        self.regression_topmost_duplicate = None
        self._set_regressions(regression)

    def _add_command(self, cmd, parameters):
        if cmd in ("use", "report"):
            try:
                self.reptrd = regzbot.ReportThread.from_url(
                    self._parse_pointer(parameters), repact=self.repact
                )
            except regzbot.RepDownloadError:
                regzbot.UnhandledEvent.add(
                    self.repact.web_url,
                    "unable to find a regression for %s",
                    self._parse_pointer(parameters),
                )
                raise RegressionNotFound
            for actimon in regzbot.RegActivityMonitor.get_by_reptrd(self.reptrd):
                if actimon.regid:
                    regression = regzbot.RegressionBasic.get_by_regid(actimon.regid)
                    self._set_regressions(regression)
                    return
            # nothing found, so assume this is a new regression
            self.regression = None
            self.regression_topmost_duplicate = None
            return
        elif cmd == "^introduced" or cmd == "introduced^":
            # this is here for backwards compatibility
            cmd = "introduced"
            if self.repact.repsrc.kind == "lore":
                try:
                    self._parse_pointer("^")
                    self._add_command("use", "^")
                except TypeError:
                    # just ignore
                    pass
        if cmd == "introduced":
            # this is here for backwards compatibility, too
            split_parameters = parameters.split()
            new_parameters = []
            for count, pointer in enumerate(split_parameters):
                if count == 0:
                    new_parameters.append(pointer)
                    continue
                pointer = self._parse_pointer(pointer)
                if pointer in ("^", "/", "~") or pointer.startswith("http"):
                    if self.reptrd == self.repact.reptrd:
                        self._add_command("use", pointer)
                    else:
                        self._add_command("duplicate", pointer)
                else:
                    new_parameters.append(pointer)
            parameters = " ".join(new_parameters)

        cmdobj = RbCmdSingleNew(self, cmd, parameters)
        self._commands.append(cmdobj)

    def _set_regressions(self, regression):
        if not regression:
            return None
        self.regression = regression
        for duplicate in regression.find_topmost():
            if self.regression.regid != duplicate.regid:
                self.regression_topmost_duplicate = duplicate

    def _parse_pointer(self, pointer):
        if pointer not in ("^", "/", "~"):
            return pointer
        if not self.reptrd.supports_relatives:
            return self.reptrd.web_url
        if pointer == "^":
            for msgid in self.reptrd.ancestors():
                return "https://lore.kernel.org/all/%s/" % msgid
        elif pointer in ("/", "~"):
            return "https://lore.kernel.org/all/%s/" % self.reptrd.root()

    # maybe the following is somewhat oddly placed here, but putting it in Regression class felt misplaced, too, as this
    # only should be executed in the contect of commands like duplicate and introduced; and in the latter case only
    # after all commands have been executed
    def add_related_activities(self, reptrd, regression):
        reptrd.update(None, None, triggering_repact=self.repact, actimon=regression.actimon)

    def process_commands(self):
        def _walk_commands():
            # raise introduced commands first, poke commands last
            for single_command in self._commands:
                if single_command.cmd == "introduced":
                    yield single_command
            for single_command in self._commands:
                if single_command.cmd == "introduced" or single_command.cmd == "poke":
                    continue
                yield single_command
            for single_command in self._commands:
                if single_command.cmd == "poke":
                    yield single_command

        regression_created = False
        assert self.reptrd
        for single_command in _walk_commands():
            if single_command.cmd == "introduced":
                regression_created = single_command.process(self.regression, None)
                if regression_created:
                    self._set_regressions(regression_created)
                continue
            elif single_command.cmd == "duplicate" and not self.regression:
                regression_created = single_command.process(self.regression, None)
                if regression_created:
                    self._set_regressions(regression_created)
                continue
            if not self.regression:
                regzbot.UnhandledEvent.add(
                    self.repact.web_url,
                    "regzbot tag in a thread not associated with a regression",
                    gmtime=self.repact.gmtime,
                    subject=self.repact.summary,
                )
                continue

            single_command.process(self.regression, self.regression_topmost_duplicate)

            if single_command.cmd == "duplicate":
                # we need to update this
                self._set_regressions(self.regression)

        # if a regressions was created and all commands processed, it's time to add all activities for it, which
        # might include even more commands that should only processed now
        if regression_created:
            self.add_related_activities(self.reptrd, regression_created)
        return regression_created


def _parse(cmd_section):
    # the following re has to deal with:
    # - mails where a long regzbot commands will have a line break in them
    # - mails or tickets, where multiple regzbot commands are separated by a semicolon
    # hence:
    # - "((^|\n|;\s+)#regzbot\s+)": find a '#regzbot' at the
    #   * the beginning of the line
    #   * after a newline
    #   * after something like an '; '
    # - (.*?): will contain the command we are looking for
    # - (?=(;?\n\s*$|(;?\n|;\s)+#regzbot)): lookahead assertion to stop on
    #   * the end of the section, as indicated by two newlines; optionally with a ; before the first and
    #     space characters before the second)
    #   * either a newline or a combination of semicolon and space characters that are followed '#regzbot'
    for cmd_line_raw in re.finditer(
        r"((^|\n|;\s+)#regzbot\s+)(.*?)(?=(;?\n\s*$|;?\s+#regzbot))",
        cmd_section,
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    ):
        # guess there is a better way to handle "#regzbot activity-\nignore" better, but whatever
        cmd_line = re.sub(r"\-\n", "-", cmd_line_raw[3])
        # remove linebreaks
        cmd_line = re.sub(r"\s?\n", " ", cmd_line)
        # following split could be handled by above RE as well, but for the sake of readability is likely
        # better kept separate:
        # - ([\w-]+): will match the command
        # - (:?\n?\s+): commands can end in a colon and are separated from parameters using at least one space;
        #             optional, as not every command has parameters (optional)
        # - (.*)?: the parameters (optional)
        splitted = re.split(r"^([\^\w-]+)(:?\n?\s+)?(.*)?$", cmd_line)
        yield (splitted[1], splitted[3])


def process_activity(activity, *, triggering_repact=None, actimon=None):
    def _handle_activity(activity, actimon):
        regression = None
        if re.search(
            r"((^|\n|;\s+)#regzbot\s+)(ignore-activity|poke)(?=(;?\n\s*$|;?\s+#regzbot))",
            "\n" + activity.message + "\n\n",
            re.MULTILINE | re.IGNORECASE | re.DOTALL,
        ):
            ignore_activity = True
        else:
            ignore_activity = False
        if actimon and not ignore_activity:
            actimon.add_activity(activity)
            regression = regzbot.RegressionBasic.get_by_regid(actimon.regid)
        else:
            for actimon in regzbot.RegActivityMonitor.get_by_reptrd(activity.reptrd):
                if actimon.regid and not regression:
                    regression = regzbot.RegressionBasic.get_by_regid(actimon.regid)
                if not ignore_activity:
                    actimon.add_activity(activity)
        return regression

    def _handle_regzbot_commands(activity, regression):
        # only handle regzbot commands in acitivies that occured after the activity that added the report
        if triggering_repact and activity.created_at <= triggering_repact.created_at:
            return

        # The following loop locates sections with regzbot commands seperated by newlines;
        #  note, it adds a newline at the start and two at the end of the processed input, as the
        #  regzbot command might be right at its start or end.
        for cmd_section in re.finditer(
            r"^\r?\n#regzbot.*?\r?\n(?=\s*\r?\n)$",
            "\n" + activity.message + "\n\n",
            re.MULTILINE | re.IGNORECASE | re.DOTALL,
        ):
            cmd_stack = RbCmdStackNew(activity, regression)
            try:
                for command, parameter in _parse(cmd_section[0].replace("\r", "")):
                    cmd_stack._add_command(command, parameter)
            except RegressionNotFound:
                continue
            cmd_stack.process_commands()

    def _handle_expected_threads(activity):
        if activity.repsrc.kind != "lore":
            return
        for regression in regzbot.RegressionBasic.get_expected_by_subject(activity.summary):
            for actimon in regzbot.RegActivityMonitor.get_by_reptrd(activity.reptrd):
                if actimon.regid == regression.regid:
                    # already monitored, nothing to do
                    return
            cmd_stack = RbCmdStackNew(activity, regression)
            cmd_stack._add_command(
                "relate",
                "%s %s [implicit, subject is expected]" % (activity.web_url, activity.summary),
            )
            cmd_stack.process_commands()

    def _handle_msgs_linking_regressions(activity):
        def _already_monitored(activity, regression):
            actimon = None
            for actimon in regzbot.RegActivityMonitor.get_by_reptrd(activity.reptrd):
                if actimon.regid == regression.regid:
                    # already monitored, nothing to do
                    return True
            return False

        message_wo_quotes = re.sub(r"^>.*\n?", "", activity.message, flags=re.MULTILINE)
        for match in re.finditer(
            r"^(\#regzbot |Link: |Closes: |.*)?(\n)?((http://|https://)\S*)",
            message_wo_quotes,
            re.MULTILINE | re.IGNORECASE,
        ):
            linktag = False
            url = False

            if match.group(0).startswith("#regzbot"):
                continue
            if match.group(0).startswith("Link") or match.group(0).startswith("Closes"):
                linktag = True
                url = match.group(0).split()
                if len(url) == 1:
                    # malformated, like https://lore.kernel.org/lkml/20211221071634.25980-1-yu.tu@amlogic.com/
                    continue
                url = url[1]
            else:
                for section in match.groups():
                    if section and section.startswith("http"):
                        url = section
                        break
            if not url:
                continue

            regression = regzbot.RegressionBasic.get_by_url(url)
            if regression is None:
                continue
            if _already_monitored(activity, regression):
                continue

            if linktag is True:
                cmd_stack = RbCmdStackNew(activity, regression)
                cmd_stack._add_command(
                    "relate",
                    "%s %s [implicit due to Link/Closes tag]"
                    % (activity.web_url, activity.summary),
                )
                cmd_stack.process_commands()
            elif url:
                cmd_stack = RbCmdStackNew(activity, regression)
                cmd_stack._add_command(
                    "note", "%s %s [implicit due to link]" % (url, activity.summary)
                )
                cmd_stack.process_commands()

    def _handle_msgs_mentioning_culprits(activity):
        open_regressions = {}
        for match in re.finditer("^(Fixes: )([0-9,a-e]{12})", activity.message, re.MULTILINE):
            # only fill this now, as we only need it if we found a Fixes: tag
            if len(open_regressions) == 0:
                for regression in regzbot.RegressionBasic.get_all(only_unsolved=True):
                    if ".." not in regression.introduced:
                        open_regressions[regression.regid] = regression.introduced[0:12]

            if match.group(2) not in open_regressions.values():
                continue
            for regid in open_regressions.keys():
                if not open_regressions[regid] == match.group(2):
                    continue
                if regzbot.RegHistory.present(activity.reptrd.id, regid=regid):
                    # no need to add a second entry for mails that already were noticed as related,
                    # for example if this msg that already has a Link: to this regression
                    continue

                # no activity, only a history entry, as it might be about different bug in the same commit
                regzbot.RegHistory.event(
                    regid,
                    activity.gmtime,
                    activity.reptrd.id,
                    activity.summary,
                    activity.realname,
                    repsrcid=activity.repsrc.id,
                    regzbotcmd="note: \"%s\" contains a 'Fixes:' tag for the culprit of this regression"
                    % activity.summary,
                )

    if "until" in regzbot._TESTING and activity.created_at >= regzbot._TESTING["until"]:
        logger.debug("[rbcmd] skip processing %s", activity.web_url)
        return
    logger.debug("[rbcmd] processing %s", activity.web_url)
    regression = _handle_activity(activity, actimon)
    # do not process things again we currently are processing already
    if triggering_repact and triggering_repact.reptrd.id == activity.reptrd.id:
        return
    regression_created = _handle_regzbot_commands(activity, regression)
    if not regression and regression_created:
        regression = regression_created
    _handle_expected_threads(activity)
    _handle_msgs_linking_regressions(activity)
    _handle_msgs_mentioning_culprits(activity)

    # let the caller know when a regression was added, as it likely must stop processing related acitivies now, as they
    # were processed already when the regression was added to pick up all related (incuding earlier) activities
    if regression_created:
        raise RegressionCreatedException


if __name__ == "__main__":
    __TESTDATA = []
    # __TESTDATA.append("#regzbot introduced foo")
    # __TESTDATA.append("#regzbot introduced foo\n#regzbot title bar")
    __TESTDATA.append(
        "#regzbot  introduced\nfoo bar \nand more for and bar; and foobar, too;\n#regzbot ignore; #regzbot title foo;\n#regzbot title: baz;"
    )
    for i in __TESTDATA:
        print("#########")
        print('"""\n%s """' % i)
        print()
        RbCmdStackNew.process(i)
        print("\n")
