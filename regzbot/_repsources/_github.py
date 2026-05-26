#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2023 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import datetime
import github
import sys
import urllib.parse
from functools import cached_property

from regzbot import PatchKind
import regzbot._repsources._trackers

if __name__ != "__main__":
    import regzbot

    logger = regzbot.logger
else:
    import logging

    logger = logging
    if False:
        # if True:
        logger.basicConfig(level=logging.DEBUG)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("github").setLevel(logging.WARNING)

_CACHE_INSTANCES = {}
_CACHE_PROJECTS = {}


class GhActivity(regzbot._repsources._trackers._activity):
    def __init__(self, gh_issue, ghpy_event, *, comment_number=None):
        self.gh_issue = gh_issue
        self.commit_id = None
        summary_prefix = "%s, issue %s" % (gh_issue.web_url[8:], gh_issue.id)

        if not ghpy_event:
            self.created_at = gh_issue.created_at
            self.id = None
            self.message = gh_issue.message
            self.realname = gh_issue.realname
            self.summary = "%s: submission" % summary_prefix
            self.username = gh_issue.username
            self.web_url = gh_issue.web_url
            return

        self.id = ghpy_event.id
        self.username = ghpy_event.actor.login
        self.realname = ghpy_event.actor.name
        self.created_at = ghpy_event.created_at.replace(tzinfo=datetime.timezone.utc)

        if ghpy_event.event == "commented":
            self.message = ghpy_event.body
            self.summary = "%s: new comment (#%s)" % (summary_prefix, comment_number)
            # there must be a better way to access this, but I failed to find one :/
            self.web_url = ghpy_event._rawData["html_url"]
        elif ghpy_event.event == "referenced":
            self.commit_id = ghpy_event.commit_id
            self.message = ""
            self.summary = "A commit referenced this issue"
            # there must be a better way to access this, but I failed to find one :/
            self.web_url = ghpy_event.commit_url.replace(
                "api.github.com/repos/", "github.com/"
            ).replace("/commits/", "/commit/")
        elif ghpy_event.event == "closed":
            self.message = ""
            self.summary = "Status is now: closed"
            # there must be a better way to access this, but I failed to find one :/
            self.web_url = gh_issue.web_url
        else:
            logger.critical("[github] GhActivity called with an unknown event; aborting.")
            sys.exit(1)

    @cached_property
    def patchkind(self):
        if not self.commit_id:
            return 0
        gh_project = self.gh_issue.gh_project
        commit = gh_project.commit(self.commit_id)
        return PatchKind.getby_commit_header(commit.commit.message)


class GhInstance:
    def __init__(self, instance_name, token):
        if instance_name != "github.com":
            raise NotImplementedError
        logger.debug("[github] github.com: connecting")
        self._ghpy_instance = github.Github(token)

    def project(self, project_name):
        global _CACHE_PROJECTS
        if project_name not in _CACHE_PROJECTS:
            logger.debug("[github] github.com: opening project %s", project_name)
            if len(_CACHE_PROJECTS) > 12:
                del _CACHE_PROJECTS[(next(iter(_CACHE_PROJECTS)))]
            _CACHE_PROJECTS[project_name] = GhProject(
                self, self._ghpy_instance.get_repo(project_name)
            )
        return _CACHE_PROJECTS[project_name]

    def search_issues(self, pattern):
        logger.debug("[github] github.com: searching for '%s'", pattern)
        for issue in self._ghpy_instance.search_issues(pattern):
            yield issue


class GhIssue(regzbot._repsources._trackers._issue):
    def __init__(self, gh_project, ghpy_issue):
        self.gh_project = gh_project
        self._ghpy_issue = ghpy_issue

        self.created_at = ghpy_issue.created_at.replace(tzinfo=datetime.timezone.utc)
        self.id = ghpy_issue.number
        self.message = ghpy_issue.body
        self.realname = ghpy_issue.user.name
        self.state = ghpy_issue.state
        self.username = ghpy_issue.user.login
        self.summary = ghpy_issue.title
        self.web_url = ghpy_issue.html_url

    @cached_property
    def _activities(self):
        activities = []
        activities.append(GhActivity(self, None))
        logger.debug("[github] %s: retrieving events", self.web_url[8:])
        comment_count = 0
        for event in self._ghpy_issue.get_timeline():
            # ignore 'mentioned' and 'subscribed'; also 'cross-referenced' for
            # now, not sure if that is wise
            if event.event not in ("commented", "closed", "referenced"):
                continue
            elif event.event == "commented":
                comment_count += 1
            activities.append(GhActivity(self, event, comment_number=comment_count))

        # sort
        activities.sort(key=lambda x: x.created_at)
        return activities

    def activities(self, *, since=None, until=None):
        for activity in self._activities:
            if since and activity.created_at < since:
                continue
            elif until and activity.created_at > until:
                continue
            yield activity

    # needed internally to workaround incomplete search
    def comments(self, since):
        # pygithub get_events for issues only allows to retrieve all events; to reduce the network load thus first
        # check only the latest comments, as that is possible with pygithub;
        logger.debug("[github] %s: retrieving comments submitted since %s", self.web_url[8:], since)
        for comment in self._ghpy_issue.comments(since=since):
            yield comment


class GhProject:
    def __init__(self, gh_instance, ghpy_project):
        self.gh_instance = gh_instance
        self._ghpy_project = ghpy_project

        self.name = self._ghpy_project.full_name

    @property
    def web_url(self):
        return self._ghpy_project.html_url

    @property
    def longname(self):
        return self._ghpy_project.full_name

    def commit(self, hexsha):
        logger.debug("[github] %s: retrieving commit %s", self.web_url[8:], hexsha)
        return self._ghpy_project.get_commit(hexsha)

    def issue(self, *, id=None, url=None):
        assert any((id, url))
        if url:
            id = url.removeprefix("%s/issues/" % self.web_url)
        logger.debug("[github] %s: retrieving issue %s", self.web_url[8:], id)
        issue = self._ghpy_project.get_issue(id)
        return GhIssue(self, issue)

    def search(self, pattern, since):
        search_string = ["is:issue"]
        search_string.append("repo:%s" % self.name)
        search_string.append("updated:>=%s" % (since.strftime("%Y-%m-%d")))
        search_string.append(pattern)
        for issue in self.gh_instance.search_issues(" ".join(search_string)):
            yield GhPossibleSearchHit(GhIssue(self, issue), pattern, since)

    def updated_issues(self, since):
        logger.debug(
            "[github] %s: retrieving issues updated since %s",
            self.web_url[8:],
            since,
        )
        for issue in self._ghpy_project.get_issues(state="all", sort="updated", since=since):
            # skip merge requests
            if issue.pull_request:
                continue
            yield GhIssue(self, issue)


# class to abstract limitations and vagueness of tracker search APIs
class GhPossibleSearchHit(regzbot._repsources._trackers._possible_search_result):
    def __init__(self, gh_issue, pattern, since):
        self.issue = gh_issue
        super().__init__(gh_issue.id, pattern, since)

    def is_hit_in_submission(self):
        if self.issue.created_at >= self._since and self._check_pattern(self.issue.message):
            return self.issue

    def matching_activities(self):
        # the github search doesn't allow to just search for a pattern in comments submitted after a specific date;
        # our search results will thus include issues that were updated, while the search term is only found in older
        # comments. We thus have to walk the comments or the events; this walks the comments, as pygithub's comments()
        # allows a "since=", while as get_events() (used to get the activities) as this time does not.
        hit = False
        for comment in self.issue.comments(since=self._since):
            if self._check_pattern(comment.body):
                hit = True
                break
        if hit:
            for activity in super().matching_activities():
                yield activity


class GhRepAct(regzbot.ReportActivity):
    def __init__(self, reptrd, gh_acivitiy):
        self.reptrd = reptrd
        self._gh_acivitiy = gh_acivitiy

        self.created_at = gh_acivitiy.created_at
        self.id = gh_acivitiy.id
        self.gmtime = int(gh_acivitiy.created_at.timestamp())
        self.message = gh_acivitiy.message
        self.patchkind = gh_acivitiy.patchkind
        self.realname = gh_acivitiy.realname
        self.summary = gh_acivitiy.summary
        self.username = gh_acivitiy.username

        super().__init__()


class GhRepSrc(regzbot._repsources._trackers._repsrc):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @cached_property
    def _gh_project(self):
        parsed_url = urllib.parse.urlparse(self.serverurl)
        instance_name = parsed_url.netloc
        project_name = parsed_url.path.strip("/")

        instance = connect(instance_name)
        project = instance.project(project_name)
        if self.serverurl != project.web_url:
            logger.error(
                "[github] self.serverurl (%s) and project.web_url (%s) do not match"
                % (self.serverurl, project.web_url)
            )
            raise AssertionError
        return project

    def search(self, pattern, since):
        for searchresult in self._gh_project.search(pattern, since):
            yield searchresult

    def supports_url(self, url_lowered, url_parsed):
        if url_lowered.startswith("%s/issues/" % self.serverurl):
            id = url_lowered.removeprefix("%s/issues/" % self.serverurl)
            id = id.split("#", 1)[0]
            return int(id.strip("/"))

    def updated_threads(self, since):
        for gh_issue in self._gh_project.updated_issues(since):
            yield GhRepTrd(self, gh_issue)

    def thread(self, *, id=None, url=None, issue=None):
        if not id and url:
            id = self.supports_url(url)
        if not issue:
            issue = self._gh_project.issue(id=id, url=url)
        return GhRepTrd(self, issue)


class GhRepTrd(regzbot._repsources._trackers._reptrd):
    def __init__(self, repsrc, gh_issue):
        self.repsrc = repsrc
        self._gh_issue = gh_issue

        self.created_at = gh_issue.created_at
        self.id = gh_issue.id
        self.gmtime = int(gh_issue.created_at.timestamp())
        self.message = gh_issue.message
        self.realname = gh_issue.realname
        self.summary = gh_issue.summary
        self.username = gh_issue.username
        super().__init__()

    def activities(self, *, since=None, until=None):
        for activity in self._gh_issue.activities(since=since, until=until):
            yield GhRepAct(self, activity)


def connect(instance_name, *, token=None):
    global _CACHE_INSTANCES
    if instance_name not in _CACHE_INSTANCES:
        if len(_CACHE_INSTANCES) > 5:
            del _CACHE_INSTANCES[(next(iter(_CACHE_INSTANCES)))]
        if not token:
            token = regzbot.CONFIGURATION[instance_name]["token"]
        _CACHE_INSTANCES[instance_name] = GhInstance(instance_name, token)
    return _CACHE_INSTANCES[instance_name]


def __test():
    # main issue used for testing (chosen without much thought): https://github.com/thesofproject/linux/issues/4455
    TESTDATA = {
        "project": "https://github.com/thesofproject/linux",
        "issue": {
            "total": 22,
            "issue_id": 4455,
            "expected": """<class '__main__.GhIssue'> => {'created_at': '2023-07-05 07:10:18+00:00', 'message': 'Commit 05cbb391aa8d2fd16c23bd43b9f1845e0a6dc333 introduced a regression.    Som…', 'realname': 'Sam Edwards', 'state': 'closed', 'summary': '[BUG] [Regression] Intel hda-dai doesn't recover gracefully from underruns; aud…', 'username': 'CFSworks', 'web_url': 'https://github.com/thesofproject/linux/issues/4455'}""",
        },
        "comments_recent": {
            "since": datetime.datetime.fromisoformat("2023-07-23T03:17:13.35Z"),
            "expected": """<class '__main__.GhActivity'> => {'created_at': '2023-07-24 15:45:04+00:00', 'message': '@CFSworks I have updated the PR now. Could you please help check if it applies …', 'realname': 'Ranjani Sridharan', 'summary': 'github.com/thesofproject/linux/issues/4455, issue 4455: new comment (#10)', 'username': 'ranj063', 'web_url': 'https://github.com/thesofproject/linux/issues/4455#issuecomment-1648167580'}""",
        },
        "commits_recent": {
            "since": datetime.datetime.fromisoformat("2023-07-24 20:10:17+00:00"),
            "expected": """<class '__main__.GhActivity'> => {'created_at': '2023-07-24 20:10:18+00:00', 'message': '', 'realname': 'Ranjani Sridharan', 'summary': 'A commit referenced this issue', 'username': 'ranj063', 'web_url': 'https://github.com/ranj063/linux/commit/3dfc905dbeb49cb5363762ad133ee4478e1b43c…'}""",
            "patchkind": 7,
        },
        "search_since": {
            "pattern": "https://bugzilla.kernel.org/show_bug.cgi.*id=217673",
            "date": datetime.datetime.fromisoformat("2023-07-21T10:26:00.00Z"),
            "total": 2,
        },
        "search_comment": {
            "pattern": "The comments in https://bugzilla.kernel.org/show_bug.cgi.*id=217673",
            "total": 1,
            "since": datetime.datetime.fromisoformat("2023-07-21T10:25:00.00Z"),
            "expected": """<class '__main__.GhActivity'> => {'created_at': '2023-07-21 10:28:54+00:00', 'message': 'I did a potentially duplicated new bug at https://github.com/thesofproject/linu…', 'realname': 'Kai Vehmanen', 'summary': 'github.com/thesofproject/linux/issues/4455, issue 4455: new comment (#7)', 'username': 'kv2019i', 'web_url': 'https://github.com/thesofproject/linux/issues/4455#issuecomment-1645363342'}""",
        },
        "search_issue": {
            "pattern": "Filing an issue to track https://bugzilla.kernel.org/show_bug.cgi.*id=217673",
            "since": datetime.datetime.fromisoformat("2023-07-21T10:22:00.00Z"),
            "total": 1,
            "expected": """<class '__main__.GhActivity'> => {'created_at': '2023-07-21 10:23:48+00:00', 'message': 'Filing an issue to track https://bugzilla.kernel.org/show_bug.cgi?id=217673    …', 'realname': 'Kai Vehmanen', 'summary': 'github.com/thesofproject/linux/issues/4482, issue 4482: submission', 'username': 'kv2019i', 'web_url': 'https://github.com/thesofproject/linux/issues/4482'}""",
        },
        "search_days_updated": 4,
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
        print("call '$0 <github apikey>'")
        sys.exit(1)
    elif len(sys.argv[1]) not in (40, 93):
        print("apikey looks malformed")
        sys.exit(1)

    parsed_url = urllib.parse.urlparse(TESTDATA["project"])
    name_project = parsed_url.path.strip("/")
    instance = connect("github.com", token=sys.argv[1])
    project = instance.project(name_project)

    # = go =
    print("Checking basic issue:", flush=True, end="")
    issue = project.issue(id=TESTDATA["issue"]["issue_id"])
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
        _testing_check_result("firsthit", str(commit), TESTDATA["commits_recent"]["expected"])
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

    print("All issues updated in the past %s days:" % TESTDATA["search_days_updated"])
    since = datetime.datetime.now() - datetime.timedelta(days=TESTDATA["search_days_updated"])
    for issue in project.updated_issues(since):
        print(issue.web_url, issue.summary[0:80])


if __name__ == "__main__":
    __test()
