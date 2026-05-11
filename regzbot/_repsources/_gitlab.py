#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2023 by Thorsten Leemhuis
__author__ = 'Thorsten Leemhuis <linux@leemhuis.info>'

import datetime
import gitlab
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

_CACHE_INSTANCES = {}
_CACHE_PROJECTS = {}


class GlActivity(regzbot._repsources._trackers._activity):
    def __init__(self, gl_issue, *, comment=None, comment_number=None, commit=None, event=None):
        self.id = None
        self.patchkind = 0
        summary_prefix = '%s, issue %s' % (gl_issue.gl_project.longname, gl_issue.id)

        if not any((comment, commit, event)):
            self.created_at = gl_issue.created_at
            self.message = gl_issue.message
            self.realname = gl_issue.realname
            self.summary = '%s: submission' % summary_prefix
            self.username = gl_issue.username
            self.web_url = gl_issue.web_url
        elif comment:
            self.created_at = datetime.datetime.fromisoformat(comment.created_at)
            self.id = comment.id
            self.message = comment.body
            self.realname = comment.author['name']
            if commit:
                self.patchkind = PatchKind.getby_commit_header(commit.message)
                self.summary = '%s: gitlab noticed a commit referencing this issue' % summary_prefix
            else:
                self.summary = '%s: new comment (#%s)' % (summary_prefix, comment_number)
            self.username = comment.author['username']
            self.web_url = '%s#note_%s' % (gl_issue.web_url, comment.id)
        elif event:
            self.created_at = datetime.datetime.fromisoformat(event.created_at)
            self.message = ''
            self.realname = event.user['name']
            self.summary = "%s: state changed to: %s" % (summary_prefix, event.state)
            self.username = event.user['username']
            self.web_url = gl_issue.web_url
        else:
            logger.critical('[gitlab] GlActivity called with something unknown; aborting.')
            sys.exit(1)


class GlInstance():
    def __init__(self, netloc, token):
        logger.debug('[gitlab] %s: connecting', netloc)
        self.web_url = 'https://%s' % netloc
        self._glpy_instance = gitlab.Gitlab(self.web_url, token)

    def project(self, project_name):
        global _CACHE_PROJECTS
        if project_name not in _CACHE_PROJECTS:
            logger.debug('[gitlab] %s: opening project %s', self.web_url, project_name)
            if len(_CACHE_PROJECTS) > 12:
                del _CACHE_PROJECTS[(next(iter(_CACHE_PROJECTS)))]
            _CACHE_PROJECTS[project_name] = GlProject(self, self._glpy_instance.projects.get(project_name))
        return _CACHE_PROJECTS[project_name]


class GlIssue(regzbot._repsources._trackers._issue):
    def __init__(self, gl_project, glpy_issue):
        self.gl_project = gl_project
        self._glpy_issue = glpy_issue

        self.created_at = datetime.datetime.fromisoformat(glpy_issue.created_at)
        self.id = glpy_issue.iid
        self.message = glpy_issue.description
        self.realname = glpy_issue.author['name']
        self.state = glpy_issue.state
        self.summary = glpy_issue.title
        self.username = glpy_issue.author['username']
        self.web_url = glpy_issue.web_url

    @cached_property
    def _activities(self):
        def _get_commit(comment):
            # ohh boy, there must be a better way to do this, but I looked hard and did not find one :-/
            if type(comment.body) is set and comment.body[0] == 'mentioned in commit ':
                commit_def = comment.body[1]
            elif comment.body.startswith("mentioned in commit "):
                commit_def = comment.body[20:]
            else:
                return None

            if '@' in commit_def:
                projectname, hexsha = commit_def.split('@')
                if '/' not in projectname:
                    projectname = '%s/%s' % (self.gl_project.namespace_path, projectname)
                gl_instance = self.gl_project.gl_instance
                project = gl_instance.project(projectname)
            else:
                hexsha = commit_def
                project = self.gl_project

            try:
                return project.commit(hexsha)
            except gitlab.exceptions.GitlabGetError:
                logger.debug('[gitlab] %s: ignoring commit %s, download failed', self.web_url[8:], hexsha)
                return None

        # walk comments (and thus commits) first, then events;
        activities = []
        activities.append(GlActivity(self))

        logger.debug('[gitlab] %s: retrieving comments and events', self.web_url[8:])
        comment_counter = 0
        for comment in self._glpy_issue.notes.list(sort='asc', iterator=True):
            commit = _get_commit(comment)
            # ignore all other system notes (e.g. notes about changes to the object, like
            # assignee changes or changes to the issue's description)
            if not commit and comment.system:
                continue
            if not commit:
                comment_counter += 1
            activities.append(GlActivity(self, comment=comment, comment_number=comment_counter, commit=commit))
        for event in self._glpy_issue.resourcestateevents.list(sort='asc', iterator=True):
            activities.append(GlActivity(self, event=event))

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


class GlProject():
    def __init__(self, gl_instance, glpy_project):
        self.gl_instance = gl_instance
        self._glpy_project = glpy_project

    @property
    def web_url(self):
        return self._glpy_project.web_url

    @property
    def namespace_path(self):
        return self._glpy_project.namespace['path']

    @property
    def longname(self):
        return self._glpy_project.path_with_namespace

    def commit(self, hexsha):
        logger.debug('[gitlab] %s: retrieving commit %s', self.web_url[8:], hexsha)
        return self._glpy_project.commits.get(hexsha)

    def issue(self, id):
        logger.debug('[gitlab] %s: retrieving issue %s', self.web_url[8:], id)
        issue = self._glpy_project.issues.get(id)
        return GlIssue(self, issue)

    def search(self, pattern, since):
        additional_msg = ''
        if since:
            additional_msg = ' submitted after %s' % since
        logger.debug("[gitlab] %s: searching for '%s' in issues%s", self.web_url[8:], pattern, additional_msg)
        for searchresult in self._glpy_project.search(gitlab.const.SearchScope.ISSUES, pattern, order_by='updated_at', sort='asc', iterator=True):
            if datetime.datetime.fromisoformat(searchresult['created_at']) < since:
                continue
            yield GlPossibleSearchHit(self, searchresult['iid'], pattern, since, is_hit_in_submission=True)
        logger.debug("[gitlab] %s: searching for '%s' in comments%s", self.web_url[8:], pattern, additional_msg)
        for searchresult in self._glpy_project.search(gitlab.const.SearchScope.PROJECT_NOTES, pattern, order_by='updated_at', sort='asc', iterator=True):
            if datetime.datetime.fromisoformat(searchresult['created_at']) < since:
                continue
            yield GlPossibleSearchHit(self, searchresult['noteable_iid'], pattern, since)

    def updated_issues(self, since):
        logger.debug('[gitlab] %s: retrieving issues updated since %s', self.web_url[8:], since)
        for issue in self._glpy_project.issues.list(iterator=True, order_by='updated_at', updated_after=since):
            yield GlIssue(self, issue)


class GlPossibleSearchHit(regzbot._repsources._trackers._possible_search_result):
    def __init__(self, gl_project, issue_id, pattern, since, *, is_hit_in_submission=False):
        self._gl_project = gl_project
        self._issue = None
        self._hit_in_submission = is_hit_in_submission
        super().__init__(issue_id, pattern, since)

    @property
    def issue(self):
        if not self._issue:
            self._issue = self._gl_project.issue(id=self.issue_id)
        return self._issue

    def is_hit_in_submission(self):
        return self._hit_in_submission


class GlRepAct(regzbot.ReportActivity):
    def __init__(self, reptrd, gl_acivitiy):
        self.reptrd = reptrd
        self._gl_acivitiy = gl_acivitiy

        self.created_at = gl_acivitiy.created_at
        self.id = gl_acivitiy.id
        self.gmtime = int(gl_acivitiy.created_at.timestamp())
        self.message = gl_acivitiy.message
        self.patchkind = gl_acivitiy.patchkind
        self.realname = gl_acivitiy.realname
        self.summary = gl_acivitiy.summary
        self.username = gl_acivitiy.username

        super().__init__()


class GlRepSrc(regzbot._repsources._trackers._repsrc):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @cached_property
    def _gl_project(self):
        parsed_url = urllib.parse.urlparse(self.serverurl)
        instance_name = parsed_url.netloc
        project_name = parsed_url.path.strip("/")

        instance = connect(instance_name)
        project = instance.project(project_name)
        assert self.serverurl == project.web_url
        return project

    def search(self, pattern, since):
        for searchresult in self._gl_project.search(pattern, since):
            yield searchresult

    def supports_url(self, url_lowered, url_parsed):
        if url_lowered.startswith(self.serverurl):
            if 'work_items' in url_lowered:
                id = url_lowered.removeprefix('%s/-/work_items/' % self.serverurl)
            else:
                id = url_lowered.removeprefix('%s/-/issues/' % self.serverurl)
            return id.strip('/')

    def updated_threads(self, since):
        for gl_issue in self._gl_project.updated_issues(since):
            yield GlRepTrd(self, gl_issue)

    def thread(self, *, id=None, url=None, issue=None):
        if not id and url:
            id = self.supports_url(url)
        if not issue:
            issue = self._gl_project.issue(id)
        return GlRepTrd(self, issue)


class GlRepTrd(regzbot._repsources._trackers._reptrd):
    def __init__(self, repsrc, gl_issue):
        self.repsrc = repsrc
        self._gl_issue = gl_issue

        self.created_at = gl_issue.created_at
        self.id = gl_issue.id
        self.gmtime = int(gl_issue.created_at.timestamp())
        self.message = gl_issue.message
        self.realname = gl_issue.realname
        self.summary = gl_issue.summary
        self.username = gl_issue.username
        super().__init__()

    def activities(self, *, since=None, until=None):
        for activity in self._gl_issue.activities(since=since, until=until):
            yield GlRepAct(self, activity)


def connect(instance_name, *, token=None):
    global _CACHE_INSTANCES
    if instance_name not in _CACHE_INSTANCES:
        if len(_CACHE_INSTANCES) > 5:
            del _CACHE_INSTANCES[(next(iter(_CACHE_INSTANCES)))]
        if not token:
            token = regzbot.CONFIGURATION[instance_name]['token']
        _CACHE_INSTANCES[instance_name] = GlInstance(instance_name, token)
    return _CACHE_INSTANCES[instance_name]


def __test():
    # main issue used for testing (chosen without much thought): https://gitlab.freedesktop.org/drm/intel/-/issues/8357
    TESTDATA = {
        'project': 'https://gitlab.freedesktop.org/drm/intel',
        'issue': {
            'total': 16,
            'issue_id': 8357,
            'expected': '''<class '__main__.GlIssue'> => {'created_at': '2023-04-11 16:17:04.368000+00:00', 'message': 'I'm working on a "hatch/jinlon" Chromebook which is a Cometlake-U device, and h…', 'realname': 'Ross Zwisler', 'state': 'closed', 'summary': 'CML-U: external 5120x2160 monitor can't play video', 'username': 'zwisler', 'web_url': 'https://gitlab.freedesktop.org/drm/intel/-/issues/8357'}'''
        },
        'comments_recent': {
            'since': datetime.datetime.fromisoformat('2023-04-18T16:37:00.000Z'),
            'expected': '''<class '__main__.GlActivity'> => {'created_at': '2023-04-18 16:37:48.523000+00:00', 'message': '[0001-drm-i915-Check-pipe-source-size-when-using-skl-scale.patch](/uploads/d3b7…', 'realname': 'Ville Syrjälä', 'summary': 'drm/intel, issue 8357: new comment (#4)', 'username': 'vsyrjala', 'web_url': 'https://gitlab.freedesktop.org/drm/intel/-/issues/8357#note_1873234'}'''
        },
        'commits_recent': {
            'since': datetime.datetime.fromisoformat('2023-05-06T00:00:00.000Z'),
            'expected': '''<class '__main__.GlActivity'> => {'created_at': '2023-05-17 19:20:40.224000+00:00', 'message': 'mentioned in commit superm1/linux@74a03d3c8d895a7d137bb4be8e40cae886f5d973', 'realname': 'Ville Syrjälä', 'summary': 'drm/intel, issue 8357: gitlab noticed a commit referencing this issue', 'username': 'vsyrjala', 'web_url': 'https://gitlab.freedesktop.org/drm/intel/-/issues/8357#note_1912677'}''',
            'patchkind': 7
        },
        'search_since': {
            'pattern': '805f04d42a6b5f4187935b43c9c39ae03ccfa761',
            'date': datetime.datetime.fromisoformat('2022-08-27T00:00:01.00Z'),
            'total': 2,
        },
        'search_comment': {
            'pattern': '805f04d42a6b5f4187935b43c9c39ae03ccfa761',
            'total': 1,
            'since': datetime.datetime.fromisoformat('2022-08-27 00:00:01+00:00'),
            'expected': '''<class '__main__.GlActivity'> => {'created_at': '2022-08-27 13:26:12+00:00', 'message': 'After taking the twelve ehm 15 step program :D  $ git bisect log - bad: [f2906a…', 'realname': 'JackCasual', 'summary': 'drm/intel, issue 6652: new comment (#6)', 'username': 'JackCasual', 'web_url': 'https://gitlab.freedesktop.org/drm/intel/-/issues/6652#note_1526397'}'''
        },
        'search_issue': {
            'pattern': '805f04d42a6b5f4187935b43c9c39ae03ccfa761',
            'since': datetime.datetime.fromisoformat('2022-08-26 00:00:01+00:00'),
            'total': 2,
            'expected': '''<class '__main__.GlActivity'> => {'created_at': '2022-08-26 04:24:15.380000+00:00', 'message': 'I have a new Framework Laptop with an i7-1280P and Xe graphics, running Debian …', 'realname': 'Brian Tarricone', 'summary': 'drm/intel, issue 6679: submission', 'username': 'kelnos', 'web_url': 'https://gitlab.freedesktop.org/drm/intel/-/issues/6679'}'''
        },
        'search_days_updated': 1
    }

    def _testing_check_result(kind, value, expected):
        if value == expected:
            print(' %s' % kind, flush=True, end='')
            return
        elif not expected:
            print(" %s (unknown, apparently '%s')" % (kind, value))
            return
        else:
            print('\n%s: mismatch; expected vs retrieved view:\n%s\n%s' % (kind, expected, value))
            if len(sys.argv) < 3 or sys.argv[2] != '--warn':
                print(" Aborting.")
                sys.exit(1)

    # = setup =

    # no need for argparse here, it's just for development anyway
    if len(sys.argv) < 2:
        print("call '$0 <gitlab apikey>'")
        sys.exit(1)
    elif len(sys.argv[1]) != 26:
        print('apikey looks malformed')
        sys.exit(1)

    parsed_url = urllib.parse.urlparse(TESTDATA['project'])
    name_instance = parsed_url.netloc
    name_project = parsed_url.path.strip("/")
    instance = connect(name_instance, token=sys.argv[1])
    project = instance.project(name_project)

    # = go =
    print("Checking basic issue:", flush=True, end='')
    issue = project.issue(id=TESTDATA['issue']['issue_id'])
    _testing_check_result('data', str(issue), TESTDATA['issue']['expected'])
    _testing_check_result('total', len(list(issue.activities())),
                          TESTDATA['issue']['total'])
    print("; succeeded.")

    print("Checking a comment:", flush=True, end='')
    for comment in issue.activities(since=TESTDATA['comments_recent']['since']):
        _testing_check_result('firsthit', str(comment), TESTDATA['comments_recent']['expected'])
        break
    print("; succeeded.")

    print("Checking a commit:", flush=True, end='')
    for commit in issue.activities(since=TESTDATA['commits_recent']['since']):
        _testing_check_result('firsthit, ', str(commit), TESTDATA['commits_recent']['expected'])
        _testing_check_result('patchkind of firsthit', commit.patchkind, TESTDATA['commits_recent']['patchkind'])
        break
    print("; succeeded.")

    if 'search_since' in TESTDATA:
        print("Checking search:", flush=True, end='')
        results_search_broad = []
        for result in project.search(TESTDATA['search_since']['pattern'], datetime.datetime.fromisoformat('2020-01-01T00:00:00.00Z')):
            for hit in result._hits():
                results_search_broad.append(hit)
        results_search_narrow = []
        for result in project.search(TESTDATA['search_since']['pattern'], TESTDATA['search_since']['date']):
            for hit in result._hits():
                results_search_narrow.append(hit)
        _testing_check_result('total', len(results_search_broad), TESTDATA['search_since']['total'])
        _testing_check_result('difference', len(results_search_broad) - len(results_search_narrow), 1)
        print("; succeeded.")

    if 'search_comment' in TESTDATA:
        print("Checking search (pattern in comment):", flush=True, end='')
        results_search_comments = []
        for result in project.search(TESTDATA['search_comment']['pattern'], since=TESTDATA['search_comment']['since']):
            for hit in result._hits():
                results_search_comments.append(hit)
        _testing_check_result('firsthit', str(results_search_comments[0]), TESTDATA['search_comment']['expected'])
        _testing_check_result('total', len(results_search_comments), TESTDATA['search_comment']['total'])
        print("; succeeded.")

    if 'search_issue' in TESTDATA:
        print("Checking search (pattern in issue):", flush=True, end='')
        results_search_issue = []
        for result in project.search(TESTDATA['search_issue']['pattern'], since=TESTDATA['search_issue']['since']):
            for hit in result._hits():
                results_search_issue.append(hit)
        _testing_check_result('firsthit', str(results_search_issue[0]), TESTDATA['search_issue']['expected'])
        _testing_check_result('total', len(results_search_issue), TESTDATA['search_issue']['total'])
        print("; succeeded.")

    print('All issues updated in the past %s days:' % TESTDATA['search_days_updated'])
    since = datetime.datetime.now() - datetime.timedelta(days=TESTDATA['search_days_updated'])
    for issue in project.updated_issues(since):
        print(issue.web_url, issue.summary[0:80])


if __name__ == "__main__":
    __test()
