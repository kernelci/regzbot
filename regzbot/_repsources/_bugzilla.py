#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2023 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import base64
import bugzilla
import datetime
import sys
import urllib.parse
from functools import cached_property

import regzbot._repsources._trackers
from regzbot import PatchKind

if __name__ != "__main__":
    import regzbot

    logger = regzbot.logger
else:
    import logging

    logger = logging
    if False:
        # if True:
        logger.basicConfig(level=logging.DEBUG)
        logging.getLogger("bugzilla").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)


_CACHE_INSTANCES = {}


class BzActivity(regzbot._repsources._trackers._activity):
    def __init__(self, bz_issue, *, comment=None, status_change=None):
        self.bz_issue = bz_issue
        self.id = None
        self.summary_prefix = "%s, issue %s" % (bz_issue.bz_project.name, bz_issue.id)

        # looking this one up can take a while, hence do it only on demand
        self._realname = None

        if comment:
            self._pybz_comment = comment
            self._creator = self._pybz_comment["creator"]
            self._patchkind = None
            self.created_at = datetime.datetime.fromisoformat(self._pybz_comment["creation_time"])
            self.id = self._pybz_comment["count"]
            self.message = self._pybz_comment["text"]
            # username is available here, but is a email address we should not expose due to typical privacy policies
            self.username = ""
            # looking this one up can take a while, hence do it only demand
            self._summary = None
            self.web_url = "%s#c%s" % (bz_issue.web_url, self.id)
        elif status_change:
            self._creator = status_change["who"]
            self._patchkind = 0
            self.created_at = datetime.datetime.fromisoformat(status_change["when"])
            self.message = ""
            self._summary = "Status now: %s" % status_change["added"]
            # username is available here, but is a email address we should not expose due to typical privacy policies
            self.username = ""
            self.web_url = bz_issue.web_url

    @property
    def realname(self):
        if not self._realname:
            bz_project = self.bz_issue.bz_project
            self._realname = bz_project.realname(self._creator)
        return self._realname

    @property
    def patchkind(self):
        if not self._patchkind:
            # this will set it:
            _ = self.summary
        return self._patchkind

    @property
    def summary(self):
        def is_patch_in_attachment():
            if not self._pybz_comment["attachment_id"]:
                return False

            bz_project = self.bz_issue.bz_project
            attachment = bz_project.attachment(
                self._pybz_comment["attachment_id"], exclude_fields="data"
            )
            attachment_details = attachment["attachments"][str(self._pybz_comment["attachment_id"])]
            if attachment_details["is_patch"] is not True:
                return False
            if attachment_details["content_type"] != "text/plain":
                return False

            # now get the attachment
            attachment = bz_project.attachment(self._pybz_comment["attachment_id"])
            attachment_details = attachment["attachments"][str(self._pybz_comment["attachment_id"])]
            attachment_details["decoded_data"] = base64.b64decode(
                attachment_details["data"]
            ).decode("utf-8")
            self._pybz_comment["attachment"] = attachment_details
            return True

        if not self._summary:
            if self.id == 0:
                self._summary = "%s: submission" % self.summary_prefix
            else:
                self._summary = "%s: new comment (#%s)" % (self.summary_prefix, self.id)
            if is_patch_in_attachment():
                self._patchkind = PatchKind.getby_content(
                    self._pybz_comment["attachment"]["decoded_data"]
                )
                self._summary = "%s with patch" % self._summary
            else:
                self._patchkind = 0
        return self._summary


# mock class to stay in line with what _gitlab.py and _github.py do, as with
# bugzilla it makes no sense to differentiate between a instance and a project
class BzInstance:
    def __init__(self, url, token):
        logger.debug("[bugzilla] %s: connecting", url.removeprefix("https://"))
        self._pybz_bugzilla = bugzilla.Bugzilla(url, force_rest=True, api_key=token)
        self.web_url = self._pybz_bugzilla.url.removesuffix("/rest/")

    def project(self):
        # reminder: mock class (see above), as instance and project are the same for bugzilla
        return BzProject(self, self._pybz_bugzilla)


class BzIssue(regzbot._repsources._trackers._issue):
    INCLUDE_FIELDS = ["attachment_id", "creator", "creation_time", "id", "status", "summary"]

    def __init__(self, bz_project, _pybz_bug):
        self.bz_project = bz_project
        self._pybz_bug = _pybz_bug

        self.id = _pybz_bug.id
        self.created_at = datetime.datetime.fromisoformat(
            _pybz_bug.creation_time.replace("Z", "+00:00")
        )
        self.message = ""
        self.realname = self.bz_project.realname(
            _pybz_bug.creator, realname=_pybz_bug.creator_detail["real_name"]
        )
        self.state = _pybz_bug.status
        self.summary = _pybz_bug.summary
        self.web_url = "%s/show_bug.cgi?id=%s" % (bz_project.web_url, _pybz_bug.id)
        # username is available as _pybz_bug.creator here, but is a email address we should not expose due to typical privacy policies
        self.username = ""

    @cached_property
    def _activities(self):
        activities = []
        logger.debug("[bugzilla] %s: retrieving comments", self.web_url[8:])
        for comment in self._pybz_bug.getcomments():
            activity = BzActivity(self, comment=comment)
            activities.append(activity)
        logger.debug("[bugzilla] %s: retrieving history", self.web_url[8:])
        history = self._pybz_bug.get_history_raw()
        for historyevent in history["bugs"][0]["history"]:
            for change in historyevent["changes"]:
                if change["field_name"] == "status":
                    # enrich dict with some data we'll need
                    change["who"] = historyevent["who"]
                    change["when"] = historyevent["when"]
                    activities.append(BzActivity(self, status_change=change))
        return activities

    def activities(self, *, since=None, until=None):
        for activity in self._activities:
            if since and activity.created_at < since:
                continue
            elif until and activity.created_at > until:
                continue
            yield activity


class BzProject:
    _usercache = {}

    def __init__(self, bz_bugzilla, pybz_bugzilla):
        self._pybz_bugzilla = pybz_bugzilla
        self.web_url = bz_bugzilla.web_url
        self.name = self.web_url[8:]

    def attachment(self, attachment_ids, include_fields=None, exclude_fields=None):
        msg_suffix = ""
        if exclude_fields and "data" in exclude_fields:
            msg_suffix = " (without data)"
        logger.debug(
            "[bugzilla] %s: retrieving attachment-id '%s%s'",
            self.web_url[8:],
            attachment_ids,
            msg_suffix,
        )
        return self._pybz_bugzilla.get_attachments(
            None, attachment_ids, include_fields, exclude_fields
        )

    def issue(self, id):
        logger.debug("[bugzilla] %s: retrieving metadata for issue '%s'", self.web_url[8:], id)
        query = self._pybz_bugzilla.build_query()
        query["include_fields"] = BzIssue.INCLUDE_FIELDS
        query["bug_id"] = id
        for result in self._pybz_bugzilla.query(query):
            return BzIssue(self, result)

    def realname(self, creator, *, realname=None):
        if creator not in self._usercache:
            if realname is None:
                logger.debug(
                    "[bugzilla] %s: retrieving details for creator %s", self.web_url[8:], creator
                )
                realname = self._pybz_bugzilla.getuser(creator).real_name
            if not realname:
                # do what bugzilla does in case realname is unset: use first half of email address
                realname = creator.split("@", 1)[0]
            self._usercache[creator] = realname
        return self._usercache[creator]

    def search(self, pattern, since, *, until=None):
        if since:
            logger.debug(
                "[bugzilla] %s: searching for '%s' in comments updated after %s",
                self.web_url[8:],
                pattern,
                since,
            )
        else:
            logger.debug("[bugzilla] %s: searching for '%s'", self.web_url[8:], pattern)
        query = self._pybz_bugzilla.build_query()
        # this for some reason doesn't work as indented for patterns with a space in it:
        #  query["longdesc"] = pattern
        #  query["longdesc_type"] = 'casesubstring'
        #  query["query_format"] = 'advanced'
        # hence approach things from a different angle:
        query["f1"] = "longdesc"
        query["o1"] = "casesubstring"
        query["v1"] = pattern
        query["query_format"] = "advanced"
        query["include_fields"] = BzIssue.INCLUDE_FIELDS
        query["j_top"] = "AND_G"
        query["f2"] = "longdesc"
        query["o2"] = "changedafter"
        query["v2"] = since.strftime("%Y-%m-%d-%H:%M:%S")
        query["f3"] = "longdesc"
        query["o3"] = "changedbefore"
        if until:
            query["v3"] = until.strftime("%Y-%m-%d-%H:%M:%S")
        else:
            query["v3"] = "Now"

        for result in self._pybz_bugzilla.query(query):
            if (
                "bugzilla-only-ids" in regzbot._TESTING
                and result.id not in regzbot._TESTING["bugzilla-only-ids"]
            ):
                continue
            yield BzPossibleSearchHit(BzIssue(self, result), pattern, since)

    def updated_issues(self, since, until=None):
        if until:
            logger.debug(
                "[bugzilla] %s: retrieving list of issues updated between '%s' and '%s'",
                self.web_url[8:],
                since,
                until,
            )
        else:
            logger.debug(
                "[bugzilla] %s: retrieving list of issues updated since '%s'",
                self.web_url[8:],
                since,
            )
        query = self._pybz_bugzilla.build_query()
        query["include_fields"] = BzIssue.INCLUDE_FIELDS
        query["chfieldfrom"] = since.strftime("%Y-%m-%d-%H:%M:%S")
        if until:
            query["chfieldto"] = until.strftime("%Y-%m-%d-%H:%M:%S")
        else:
            query["chfieldto"] = "Now"

        for result in self._pybz_bugzilla.query(query):
            if (
                "bugzilla-only-ids" in regzbot._TESTING
                and result.id not in regzbot._TESTING["bugzilla-only-ids"]
            ):
                continue
            yield BzIssue(self, result)


class BzPossibleSearchHit(regzbot._repsources._trackers._possible_search_result):
    def __init__(self, bz_issue, pattern, since):
        self.issue = bz_issue
        super().__init__(bz_issue.id, pattern, since)


class BzRepAct(regzbot.ReportActivity):
    def __init__(self, reptrd, bz_acivitiy):
        self.reptrd = reptrd

        self.created_at = bz_acivitiy.created_at
        self.id = bz_acivitiy.id
        self.gmtime = int(bz_acivitiy.created_at.timestamp())
        self.message = bz_acivitiy.message
        self.patchkind = bz_acivitiy.patchkind
        self.realname = bz_acivitiy.realname
        self.summary = bz_acivitiy.summary
        self.username = bz_acivitiy.username

        super().__init__()


class BzRepSrc(regzbot._repsources._trackers._repsrc):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @cached_property
    def _bz_project(self):
        parsed_url = urllib.parse.urlparse(self.serverurl)
        instance_name = parsed_url.netloc

        instance = connect(instance_name)
        project = instance.project()
        assert self.serverurl == project.web_url
        return project

    def search(self, pattern, since):
        for searchresult in self._bz_project.search(pattern, since):
            yield searchresult

    def supports_url(self, url_lowered, url_parsed):
        if url_lowered.startswith(self.serverurl):
            # there might be a comma or something else that might need to be removed:
            # https://lore.kernel.org/linux-wireless/170844096394.7.10031732457351764961.271076804@slmail.me/
            stripped = "".join(filter(str.isdigit, url_parsed.query.removeprefix("id=")))
            if not stripped:
                return False
            return int(stripped)

    def updated_threads(self, since):
        for bz_issue in self._bz_project.updated_issues(since):
            yield BzRepTrd(self, bz_issue)

    def thread(self, *, id=None, url=None, issue=None):
        assert any((id, url, issue))
        if not issue:
            if not id:
                id = self.supports_url(url)
                if not id:
                    logger.error("[bugzilla] cound not parse %s", url)
                    raise regzbot.RepDownloadError
            issue = self._bz_project.issue(id)
        return BzRepTrd(self, issue)


class BzRepTrd(regzbot._repsources._trackers._reptrd):
    def __init__(self, repsrc, bz_issue):
        self.repsrc = repsrc
        self._bz_issue = bz_issue

        self.created_at = bz_issue.created_at
        self.id = bz_issue.id
        self.gmtime = int(bz_issue.created_at.timestamp())
        self.message = bz_issue.message
        self.realname = bz_issue.realname
        self.summary = bz_issue.summary
        self.username = bz_issue.username
        super().__init__()

    def activities(self, *, since=None, until=None):
        for activity in self._bz_issue.activities(since=since, until=until):
            yield BzRepAct(self, activity)


def connect(instance_name, *, token=None):
    global _CACHE_INSTANCES
    if instance_name not in _CACHE_INSTANCES:
        if len(_CACHE_INSTANCES) > 5:
            del _CACHE_INSTANCES[(next(iter(_CACHE_INSTANCES)))]
        if not token:
            token = regzbot.CONFIGURATION[instance_name]["token"]
        _CACHE_INSTANCES[instance_name] = BzInstance(instance_name, token)
    return _CACHE_INSTANCES[instance_name]


def __test():
    # main issue used for testing (chosen without much thought): https://bugzilla.kernel.org/show_bug.cgi?id=217678
    TESTDATA = {
        "project": "https://bugzilla.kernel.org",
        "issue": {
            "total": 37,
            "issue_id": 217678,
            "expected": """<class '__main__.BzIssue'> => {'created_at': '2023-07-17 17:44:27+00:00', 'message': '', 'realname': 'hq.dev+kernel', 'state': 'RESOLVED', 'summary': 'Unexplainable packet drop starting at v6.4', 'username': '', 'web_url': 'https://bugzilla.kernel.org/show_bug.cgi?id=217678'}""",
        },
        "comments_recent": {
            "since": datetime.datetime.fromisoformat("2023-10-17 04:39:50+00:00"),
            "expected": """<class '__main__.BzActivity'> => {'created_at': '2023-10-17 04:39:55+00:00', 'message': 'It is currently in next-queue. Since 6.6.-rc6 is already out, I hope it makes i…', 'realname': 'Tirthendu Sarkar', 'summary': 'bugzilla.kernel.org, issue 217678: new comment (#33)', 'username': '', 'web_url': 'https://bugzilla.kernel.org/show_bug.cgi?id=217678#c33'}""",
        },
        "commits_recent": {
            "count": None,
            "since": datetime.datetime.fromisoformat("2023-09-29 11:21:10+00:00"),
            "expected": """<class '__main__.BzActivity'> => {'created_at': '2023-09-29 11:21:20+00:00', 'message': 'Created attachment 305161 Patch with temp fix and debug prints  Hi,  Thanks for…', 'realname': 'Tirthendu Sarkar', 'summary': 'bugzilla.kernel.org, issue 217678: new comment (#27) with patch', 'username': '', 'web_url': 'https://bugzilla.kernel.org/show_bug.cgi?id=217678#c27'}""",
            "patchkind": 3,
        },
        "search_since": {
            "pattern": "d42b1c47570eb2ed818dc3fe94b2678124af109d",
            "date": datetime.datetime.fromisoformat("2023-07-08 00:00:00+00:00"),
            "total": 6,
        },
        "search_comment": {
            "pattern": "d42b1c47570eb2ed818dc3fe94b2678124af109d",
            "total": 2,
            "since": datetime.datetime.fromisoformat("2023-07-18 03:40:27+00:00"),
            "expected": """<class '__main__.BzActivity'> => {'created_at': '2023-07-18 03:40:27+00:00', 'message': '(In reply to hq.dev+kernel from comment #4) > Created attachment 304648 [detail…', 'realname': 'Bagas Sanjaya', 'summary': 'bugzilla.kernel.org, issue 217678: new comment (#7)', 'username': '', 'web_url': 'https://bugzilla.kernel.org/show_bug.cgi?id=217678#c7'}""",
        },
        "search_days_updated": 3,
    }

    def _testing_check_result(kind, value, expected):
        if value == expected:
            print(" %s" % kind, flush=True, end="")
            return
        elif not expected:
            print(" %s (unknown, apparently '%s')" % (kind, value))
            return
        else:
            print("\n%s: mismatch; expected vs retrieved view:\n%s\n%s" % (kind, expected, value))
            if len(sys.argv) < 3 or sys.argv[2] != "--warn":
                print(" Aborting.")
                sys.exit(1)

    # = setup =

    # no need for argparse here, it's just for development anyway
    if len(sys.argv) < 2:
        print("call '$0 <bugzilla.kernel.org apikey>'")
        sys.exit(1)
    elif len(sys.argv[1]) != 40:
        print("apikey looks malformed")
        sys.exit(1)

    instance = connect(TESTDATA["project"], token=sys.argv[1])
    project = instance.project()

    # = go =
    print("Checking basic issue:", flush=True, end="")
    issue = project.issue(TESTDATA["issue"]["issue_id"])
    _testing_check_result("data", str(issue), TESTDATA["issue"]["expected"])
    _testing_check_result("total", len(list(issue.activities())), TESTDATA["issue"]["total"])
    print("; succeeded.")

    print("Checking a comment:", flush=True, end="")
    for comment in issue.activities(since=TESTDATA["comments_recent"]["since"]):
        _testing_check_result("firsthit", str(comment), TESTDATA["comments_recent"]["expected"])
        break
    print("; succeeded.")

    print("Checking a commit:", flush=True, end="")
    for commit in issue.activities(since=TESTDATA["commits_recent"]["since"]):
        _testing_check_result("firsthit, ", str(commit), TESTDATA["commits_recent"]["expected"])
        _testing_check_result(
            "patchkind of firsthit", commit.patchkind, TESTDATA["commits_recent"]["patchkind"]
        )
        break
    print("; succeeded.")

    if "search_since" in TESTDATA:
        print("Checking search:", flush=True, end="")
        results_search_broad = []
        for result in project.search(
            TESTDATA["search_since"]["pattern"],
            datetime.datetime.fromisoformat("2020-01-01T00:00:00.00Z"),
        ):
            for hit in result._hits():
                results_search_broad.append(hit)
        results_search_narrow = []
        for result in project.search(
            TESTDATA["search_since"]["pattern"], TESTDATA["search_since"]["date"]
        ):
            for hit in result._hits():
                results_search_narrow.append(hit)
        _testing_check_result("total", len(results_search_broad), TESTDATA["search_since"]["total"])
        _testing_check_result(
            "difference", len(results_search_broad) - len(results_search_narrow), 1
        )
        print("; succeeded.")

    if "search_comment" in TESTDATA:
        print("Checking search (pattern in comment):", flush=True, end="")
        results_search_comments = []
        for result in project.search(
            TESTDATA["search_comment"]["pattern"], since=TESTDATA["search_comment"]["since"]
        ):
            for hit in result._hits():
                results_search_comments.append(hit)
        _testing_check_result(
            "firsthit", str(results_search_comments[0]), TESTDATA["search_comment"]["expected"]
        )
        _testing_check_result(
            "total", len(results_search_comments), TESTDATA["search_comment"]["total"]
        )
        print("; succeeded.")

    if "search_issue" in TESTDATA:
        print("Checking search (pattern in issue):", flush=True, end="")
        results_search_issue = []
        for result in project.search(
            TESTDATA["search_issue"]["pattern"], since=TESTDATA["search_issue"]["since"]
        ):
            for hit in result._hits():
                results_search_issue.append(hit)
        _testing_check_result(
            "firsthit", str(results_search_issue[0]), TESTDATA["search_issue"]["expected"]
        )
        _testing_check_result("total", len(results_search_issue), TESTDATA["search_issue"]["total"])
        print("; succeeded.")

    print("All issues updated between %s and 7 days ago:" % TESTDATA["search_days_updated"])
    until = datetime.datetime.now() - datetime.timedelta(days=7)
    since = until - datetime.timedelta(days=TESTDATA["search_days_updated"])
    for issue in project.updated_issues(since, until=until):
        print(issue.web_url, issue.summary[0:80])


if __name__ == "__main__":
    __test()
