#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2023 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"


import datetime
import re

import regzbot


class _activity:
    def __str__(self):
        return _describe(
            self, ("created_at", "message", "realname", "summary", "username", "web_url")
        )


class _issue:
    def __str__(self):
        return _describe(
            self, ("created_at", "message", "realname", "state", "summary", "username", "web_url")
        )

    @classmethod
    def activities(cls, *, since=None):
        raise NotImplementedError


class _possible_search_result:
    def __init__(self, issue_id, pattern, since):
        self.id = issue_id
        self.issue_id = issue_id
        self._pattern = pattern
        self._since = since

    def __str__(self):
        return _describe(self, ("id",))

    def _check_pattern(self, body):
        return bool(re.search(self._pattern, body))

    def is_hit_in_submission(self):
        return False

    def get_matching_activities(self):
        for activity in self.issue.activities(since=self._since):
            if self._check_pattern(activity.message):
                yield activity

    # only used in testing infra
    def _hits(self):
        for hit in self.get_matching_activities():
            yield hit


class _reptrd(regzbot.ReportThread):
    def update(self, since, until, *, actimon=None, triggering_repact=None):
        try:
            for activity in self.activities(since=since, until=until):
                regzbot._rbcmd.process_activity(
                    activity, actimon=actimon, triggering_repact=triggering_repact
                )
        except regzbot._rbcmd.RegressionCreatedException:
            # the handled activity contained a #regzbot introduced that created a regression for this issue; during that
            # process all activities (both older and younger) for it will be added by calling this method again, so
            # there is nothing more for us to do here
            pass


class _repsrc(regzbot.ReportSource):
    def update(self):
        # prep
        if "until" in regzbot._TESTING:
            check_started = regzbot._TESTING["until"]
        else:
            check_started = regzbot.timendate_now()
        if self.lastchked:
            check_last = regzbot.timendate_gmtime_to_dt(self.lastchked)
        else:
            check_last = check_started - datetime.timedelta(days=14)

        if self.lastchked and self.mininterval:
            earliest_check = regzbot.timendate_gmtime_to_dt(self.lastchked + self.mininterval)
            if earliest_check > check_started:
                return

        threads_processed = []

        # check if any tracked issues were updated
        for updated_thread in self.updated_threads(since=check_last):
            for actimon in regzbot.RegActivityMonitor.get_by_reptrd(updated_thread):
                updated_thread.update(check_last, check_started, actimon=actimon)
                threads_processed.append(updated_thread.id)

        # scan any untracked issues that have #regzbot commands in them
        for searchresult in self.search("#regzbot", since=check_last):
            if searchresult.issue_id in threads_processed:
                continue
            thread = self.thread(issue=searchresult.issue)
            thread.update(check_last, check_started)
            threads_processed.append(searchresult.issue_id)

        self.set_lastchked(check_started)


def _describe(obj, variable_names):
    content = []
    for variable_name in variable_names:
        # handle normal variables and  properties:
        if variable_name in obj.__dict__:
            value = obj.__dict__[variable_name]
        else:
            value_getter = getattr(obj.__class__, variable_name)
            value = value_getter.__get__(obj, obj.__class__)

        if type(value) is str:
            value = value.replace("\r", " ")
            value = value.replace("\n", " ")
            if len(value) > 79:
                value = "%s…" % value[0:79]
        content.append("'%s': '%s'" % (variable_name, value))
    return str(obj.__class__) + " => {" + ", ".join(content) + "}"
