# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import configparser
import datetime
import difflib
from enum import IntFlag
import logging
import os
import pathlib
import re
import tempfile
import urllib.parse
import sqlite3
import sys

import git

from functools import cached_property

__VERSION__ = "0.0.1-dev"
__CITESTING__ = False
_TESTING = {}
DBCON = None
REPOSDIR = None
CONFIGURATION = None
REPORT_SUBJECT_PREFIX = "Linux regressions report "
LATEST_VERSIONS = None
WEBPAGEDIR = None

logger = logging.getLogger("regzbot")


class PatchKind(IntFlag):
    DIFF = 1
    SUBJECT = 2
    SIGNEDOFF = 4

    @staticmethod
    def _checkfor_signed_off(content):
        if re.search(r"^Signed-[oO]ff-[Bb]y: ", content, re.MULTILINE):
            return PatchKind.SIGNEDOFF
        return 0

    @classmethod
    def getby_commit_header(cls, content):
        # diff and subject are always true here:
        # diff and subject are always true here:
        patchkind = PatchKind(PatchKind.DIFF | PatchKind.SUBJECT)
        patchkind |= cls._checkfor_signed_off(content)
        return patchkind

    @classmethod
    def getby_content(cls, content, subject=None):
        def checkfor_diff(content):
            if re.search(r"^\-\-\- .*\n\+\+\+.*\n@@", content, re.MULTILINE | re.DOTALL):
                return PatchKind.DIFF
            return 0

        def checkfor_subject(content, subject):
            if subject and subject.startswith("[PATCH"):
                return PatchKind.SUBJECT
            elif re.search(r"^Subject: \[PATCH", content, re.MULTILINE):
                return PatchKind.SUBJECT
            return 0

        patchkind = PatchKind(0)
        patchkind |= checkfor_diff(content)
        patchkind |= checkfor_subject(content, subject)
        patchkind |= cls._checkfor_signed_off(content)

        return patchkind


class RegzbotDbMeta:
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "RegzbotMeta"')
        dbcursor.execute("""
                CREATE TABLE RegzbotMeta (
                    name TEXT UNIQUE,
                    version INTEGER
            )""")

    @staticmethod
    def init(databasedir):
        dbconnection = db_init(databasedir)
        if not dbconnection:
            logger.debug("aborting: dbconnection could not be initialized")
            sys.exit(1)

        return dbconnection

    @staticmethod
    def update(dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()

        if not RegzbotDbMeta.table_exists("RegzbotState", dbcursor):
            RegzbotState.db_create(1, dbcursor)

    @staticmethod
    def table_exists(tablename, dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=(?)", (tablename,)
        ).fetchone()
        if dbresult:
            return True
        return False

    @staticmethod
    def set_tableversion(tablename, version, dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()
        dbcursor.execute(
            """
            INSERT INTO RegzbotMeta
            VALUES(?, ?)""",
            (tablename, version),
        )


class RegzbotState:
    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "RegzbotState"')
        dbcursor.execute("""
                CREATE TABLE RegzbotState (
                    attribute  TEXT UNIQUE,
                    value      STRING
            )""")

    @staticmethod
    def get(attribute, dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT value FROM RegzbotState WHERE attribute=(?)", (attribute,)
        ).fetchone()
        if dbresult:
            return dbresult[0]
        return False

    @staticmethod
    def set(attribute, value, dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()
        dbcursor.execute(
            """
            INSERT OR REPLACE INTO RegzbotState
            VALUES(?, ?)""",
            (attribute, value),
        )


class RecordProcessedMsgids:
    def __init__(self, msgid, gmtime):
        self.msgid = msgid
        self.gmtime = gmtime

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "msgidrecord"')
        RegzbotDbMeta.set_tableversion("msgidrecord", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE msgidrecord (
                msgid       STRING   NOT NULL PRIMARY KEY,
                gmtime      INTEGER  NOT NULL
            )""")

    @staticmethod
    def add(msgid, gmtime, dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()

        dbcursor.execute(
            """INSERT INTO msgidrecord
                            (msgid, gmtime)
                            VALUES (?, ?)""",
            (msgid, gmtime),
        )
        logger.debug("[db msgidrecord] insert (msgid:%s, gmtime:%s)", msgid, gmtime)

    @staticmethod
    def check_presence(msgid, gmtime=None, dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()

        dbresult = dbcursor.execute(
            "SELECT * FROM msgidrecord WHERE msgid=(?)", (msgid,)
        ).fetchone()
        if dbresult:
            return True
        elif gmtime:
            # this implies that we should add the msgid if it's missing
            RecordProcessedMsgids.add(msgid, gmtime, dbcursor)
        return False

    @staticmethod
    def delete(msgid):
        dbcursor = DBCON.cursor()
        if RecordProcessedMsgids.check_presence(msgid, dbcursor=dbcursor):
            dbcursor.execute(
                """DELETE FROM msgidrecord
                             WHERE msgid=(?)""",
                (msgid,),
            )
            logger.debug("[db msgidrecord] removed msgid: %s", msgid)

    @staticmethod
    def cleanup(cutoff_days):
        dbcursor = DBCON.cursor()
        cutoff_gmtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - (
            cutoff_days * 86400
        )
        dbcursor.execute(
            """DELETE FROM msgidrecord
                            WHERE gmtime < (?)""",
            (cutoff_gmtime,),
        )
        if dbcursor.rowcount > 0:
            logger.debug("[db msgidrecord] removed %s stale entries", dbcursor.rowcount)


class GitBranch:
    def __init__(self, gitbranchid, gittreeid, name, lastchked):
        self.gitbranchid = gitbranchid
        self.gittreeid = gittreeid
        self.name = name
        self.lookupname = "origin/%s" % name
        self.lastchked = lastchked

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "gitbranches"')
        RegzbotDbMeta.set_tableversion("gitbranches", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE gitbranches (
                gitbranchid INTEGER  NOT NULL PRIMARY KEY,
                gittreeid   INTEGER  NOT NULL,
                name        STRING   NOT NULL,
                lastchked   STRING
            )""")

    @staticmethod
    def add(gittree, branchname, lastchked):
        branchname = branchname.removeprefix("origin/")
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """INSERT INTO gitbranches
            (gittreeid, name, lastchked)
            VALUES (?, ?, ?)""",
            (gittree.gittreeid, branchname, lastchked),
        )
        logger.debug(
            "[db gitbranches] insert (gitbranchid:%s, gittreeid:%s, branchname:%s, lastchked:%s)"
            % (dbcursor.lastrowid, gittree.gittreeid, branchname, lastchked)
        )
        return dbcursor.lastrowid

    def commit_exists(self, identifier, repo=None, gittree=None):
        # this makes it possible to reuse the repo obj
        if gittree is None:
            gittree = GitTree.get_by_id(self.gittreeid)
        if repo is None:
            repo = gittree.repo()

        try:
            # reminder: just relying on the exception is not enough here, as it will *not* fire
            # if the commit exists in the tree, but in another branch :-/
            result = repo.git.branch(self.lookupname, "--all", "--contains", identifier)
            if gittree.name == "next":
                # the commit or tag seems to be present, but not in the current branch -- but we do not care about that
                return True
            if result:
                return True
        except git.exc.GitCommandError as err:
            output = err.args[2].decode("utf-8")
            ignored = {"error: malformed object name", "error: no such commit"}
            if not any(x in output for x in ignored):
                logger.critical("GitCommandError: {0}".format(err))
                logger.critical(err.args)
        return False

    def describe(self, gittreename):
        if self.name == "master" or self.name == "main":
            return gittreename
        else:
            return "%s/%s" % (gittreename, self.name)

    @staticmethod
    def get_by_id(gitbranchid):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM gitbranches WHERE gitbranchid=(?)", (gitbranchid,)
        ).fetchone()
        if dbresult:
            return GitBranch(*dbresult)
        return None

    @staticmethod
    def get_by_treeid_branchname(gittreeid, name):
        # avoids programming pitfalls:
        if name.startswith("origin/"):
            name = name.removeprefix("origin/")

        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM gitbranches WHERE gittreeid=(?) AND name=(?)", (gittreeid, name)
        ).fetchone()
        if dbresult:
            return GitBranch(*dbresult)

        return None

    @staticmethod
    def getall(order="gittreeid gitbranchid"):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute("SELECT * FROM gitbranches ORDER BY ?", order):
            yield GitBranch(*dbresult)

    @staticmethod
    def getall_by_gittreeid(gittreeid):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute(
            "SELECT * FROM gitbranches WHERE gittreeid=(?)", (gittreeid,)
        ):
            yield GitBranch(*dbresult)

    def head_at_gmtime(self, gmtime, *, repo=None):
        if repo is None:
            gittree = GitTree.get_by_id(self.gittreeid)
            repo = gittree.repo()

        try:
            head = repo.git.rev_list(
                "--first-parent", '--until="%s"' % gmtime, "-n 1", "origin/%s" % self.name
            )
            return repo.commit(head)
        except git.exc.GitCommandError as err:
            errmsg = err.args[2].decode("utf-8")
            print("GitCommandError: {0}".format(errmsg))
            print(err.args)
            return None

    def is_abandoned(self, repo=None):
        if is_running_citesting():
            return False

        if repo is None:
            gittree = GitTree.get_by_id(self.gittreeid)
            repo = gittree.repo()

        date_offset = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - 86400 * 63
        date_head = repo.commit(self.lookupname).committed_date
        if date_head < date_offset:
            return True
        return False

    def merge_date(self, hexsha, repo=None):
        def get_date(repo, hexsha):
            return repo.commit(hexsha).committed_date

        if repo is None:
            gittree = GitTree.get_by_id(self.gittreeid)
            repo = gittree.repo()

        try:
            # inspired by https://stackoverflow.com/a/20615706
            ancestry_path = repo.git.rev_list(
                "--ancestry-path", "%s..origin/%s" % (hexsha, self.name)
            ).splitlines()
            first_parent = repo.git.rev_list(
                "--first-parent", "%s..origin/%s" % (hexsha, self.name)
            ).splitlines()

            # committed directly
            if len(ancestry_path) == 0:
                return get_date(repo, hexsha)
            # find the last result in ancestry_path that's also in first_parent
            ancestry_path.reverse()
            for commit in ancestry_path:
                if commit in first_parent:
                    return get_date(repo, commit)
        except git.exc.GitCommandError as err:
            errmsg = err.args[2].decode("utf-8")
            logger.critical("GitCommandError: {0}".format(errmsg))
            logger.critical(err.args)
            return None

    def subject_exists(self, subject, gittree=None, repo=None):
        if repo is None or gittree is None:
            gittree = GitTree.get_by_id(self.gittreeid)
            if repo is None:
                gittree = GitTree.get_by_id(self.gittreeid)
                repo = gittree.repo()

        # try to find a merge-base to speed things up a bit
        mergebase = None
        if gittree.name == "next":
            # the branch with mainline is called stable in next
            mergebase = repo.git.merge_base("origin/stable", self.lookupname)
        elif gittree.name == "stable":
            # the branch with mainline is called master in stable
            mergebase = repo.git.merge_base("origin/master", self.lookupname)

        if mergebase:
            iterrange = "%s..%s" % (mergebase, self.lookupname)
        else:
            if gittree.name != "mainline":
                logger.warning(
                    "GitBranch.subject_exists(): could not find a merge base for the tree %s branch %s",
                    gittree.name,
                    self.name,
                )
            iterrange = self.lookupname

        # now search for a commit with the subject
        for commit in repo.iter_commits(iterrange):
            if commit.summary == subject:
                return commit.hexsha
        return False

    def url(self, entry, gittree=None):
        if gittree is None:
            gittree = GitTree.get_by_id(self.gittreeid)
        return "%s?h=%s&id=%s" % (gittree.weburl, self.name, entry)

    @staticmethod
    def url_by_id(gitbranchid, entry):
        gitbranch = GitBranch.get_by_id(gitbranchid)
        return gitbranch.url(entry)

    def set_lastchked(self, lastchked):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE gitbranches SET lastchked = (?) WHERE gitbranchid=(?)""",
            (lastchked, self.gitbranchid),
        )


class GitTree:
    def __init__(self, gittreeid, name, server, kind, weburl, branchregex, priority):
        self.gittreeid = gittreeid
        self.name = name
        self.server = server
        self.kind = kind
        self.weburl = weburl
        self.branchregex = branchregex
        self.priority = priority
        self.__repo = None  # only initialize it once needed

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "gittrees"')
        RegzbotDbMeta.set_tableversion("gittrees", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE gittrees (
                gittreeid   INTEGER  NOT NULL PRIMARY KEY,
                name        STRING   NOT NULL,
                server      STRING   NOT NULL,
                kind        STRING   NOT NULL,
                weburl      STRING   NOT NULL,
                branchregex STRING   NOT NULL,
                priority    INTEGER  NOT NULL
            )""")

    @staticmethod
    def add(name, server, kind, weburl, branchregex, priority):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """INSERT INTO gittrees
            (name, server, kind, weburl, branchregex, priority)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (name, server, kind, weburl, branchregex, priority),
        )
        logger.debug(
            "[db gittrees] insert (gittreeid:%s, name:%s, server:%s, kind:%s, weburl:%s, branchregex:%s, priority: %s)"
            % (dbcursor.lastrowid, name, server, kind, weburl, branchregex, priority)
        )
        return dbcursor.lastrowid

    def commit(self, hexsha):
        repo = self.repo()
        return repo.commit(hexsha)

    def commit_describe(self, identifier, contains):
        repo = self.repo()

        if contains:
            kind = "--contains"
        else:
            kind = "--tags"

        try:
            # reminder: just relying on the exception is not enough here, as it will not fire
            # if the commit exists in the tree, but in another branch :-/
            result = repo.git.describe(kind, identifier)
            if result:
                if contains:
                    result = result.split("~")[0]
                else:
                    result = re.sub("-[0-9]+-g[0-9,a-f]+$", "", result)
                return result, True
        except git.exc.GitCommandError as err:
            output = err.args[2].decode("utf-8")
            if "fatal: cannot describe" in output:
                # commit present, but unabled to describe, as since then no commit was tagged
                return None, True
            ignored = "error: malformed object name"
            if not any(x in output for x in ignored):
                logger.critical("GitCommandError: {0}".format(err))
                logger.critical(err.args)
        return None, None

    @staticmethod
    # commitdesc can be a tag or a hexsha
    def commit_find_old(commitdesc):
        for gittree in GitTree.getall():
            repo = gittree.repo()
            for gitbranch in GitBranch.getall_by_gittreeid(gittree.gittreeid):
                if gitbranch.commit_exists(commitdesc, repo, gittree):
                    return gittree, gitbranch
        return None, None

    @staticmethod
    # commitdesc can be a tag or a hexsha
    def commit_find_new(hexsha=None, subject=None, ascending=True):
        if ascending:
            sortorder = "ORDER BY priority ASC"
        else:
            sortorder = "ORDER BY priority DESC"

        for gittree in GitTree.getall(FIXME=sortorder):
            repo = gittree.repo()
            for gitbranch in GitBranch.getall_by_gittreeid(gittree.gittreeid):
                if gitbranch.is_abandoned():
                    logger.debug(
                        "gittree, %s, %s: branch abandoned, skipping lookup",
                        gittree.name,
                        gitbranch.name,
                    )
                    continue
                if hexsha and gitbranch.commit_exists(hexsha, repo):
                    yield gittree, gitbranch, hexsha
                    continue
                if subject:
                    logger.debug(
                        "gittree, %s, %s: searching for subject '%s'",
                        gittree.name,
                        gitbranch.name,
                        subject,
                    )
                    hexsha = gitbranch.subject_exists(subject, gittree=gittree, repo=repo)
                    if hexsha:
                        yield gittree, gitbranch, hexsha
                        continue

    @staticmethod
    def commit_summary(hexsha):
        for gittree in GitTree.getall():
            repo = gittree.repo()
            try:
                commit = repo.commit(hexsha)
                if commit:
                    return commit.summary
            except Exception:
                pass

    @staticmethod
    def check_latest_versions(repo=None):
        if repo is None:
            gittree = GitTree.get_by_name("mainline")
            if not gittree:
                logger.critical(
                    "Unable to determine current and next version, as it's determined from a gittree with the name 'mainline', which could not be found."
                )
                return False
            repo = gittree.repo()

        global LATEST_VERSIONS
        LATEST_VERSIONS = {
            "indevelopment": None,
            "latest": None,
            "previous": None,
        }

        re_expectedtags = re.compile(r"^(v[0-9]+\.[0-9]+)(-rc[0-9]+)*(-dontuse)*$")
        for line in repo.git.tag("--sort=-creatordate").splitlines():
            match = re_expectedtags.search(line)
            if match is None:
                logger.critical(
                    "aborting: encountered a tag that doesn't follow the expected pattern ('%s')"
                    % line
                )
                sys.exit(1)

            if match.group(2):
                if LATEST_VERSIONS["indevelopment"] is None:
                    LATEST_VERSIONS["indevelopment"] = match.group(1)
                continue
            elif match.group(1) and match.group(2) is None:
                # we found our first proper (aka non-rc) tag
                if LATEST_VERSIONS["indevelopment"] is None:
                    # we haven't seen a rc tag yet, so we are in the middle of a merge window and don't known yet what the next version will be called
                    LATEST_VERSIONS["indevelopment"] = False
                    # fallthrough
                if LATEST_VERSIONS["latest"] is None:
                    LATEST_VERSIONS["latest"] = match.group(1)
                    continue
                else:
                    LATEST_VERSIONS["previous"] = match.group(1)
                    break
            logger.critical(
                "Unable to determine current and next version, could not find expected tags"
            )
            return False

        logger.debug(
            "'next' is now '%s', 'latest' is now '%s', and 'previous' is now '%s'",
            LATEST_VERSIONS["indevelopment"],
            LATEST_VERSIONS["latest"],
            LATEST_VERSIONS["previous"],
        )

    @staticmethod
    def getall(FIXME=""):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute("SELECT * FROM gittrees %s" % FIXME):
            yield GitTree(*dbresult)

    @staticmethod
    def get_by_id(gittreeid):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM gittrees WHERE gittreeid=(?)", (gittreeid,)
        ).fetchone()
        if dbresult:
            return GitTree(*dbresult)
        return None

    @staticmethod
    def get_by_name(treename):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute("SELECT * FROM gittrees WHERE name=(?)", (treename,)).fetchone()
        if dbresult:
            return GitTree(*dbresult)
        return None

    def greplogmsgs(self, pattern):
        repo = self.repo()
        result = None
        since = "--since='Aug 15 0:0:0 UTC 2021'"
        if is_running_citesting("offline"):
            since = "--since='Aug 15 0:0:0 UTC 2010'"

        try:
            for result in repo.git.log(
                "--pretty=%H", since, "--all", "--grep=%s" % pattern
            ).splitlines():
                yield result
        except Exception:
            return

    def repo(self):
        # hidden even within the class, to only initialize it when actually needed
        if self.__repo is None:
            self.__repo = git.Repo.init(os.path.join(REPOSDIR, self.name))
        return self.__repo

    @classmethod
    def search_references(cls, repsrc, regression, gmtime=None):
        def getregression(regression, regid):
            if regressionfull:
                return regressionfull
            return RegressionFull.get_by_regid(regid)

        regressionfull = None
        for gittree in cls.getall(FIXME="ORDER BY priority ASC"):
            searchprefix = "\(Link\|Closes\):"
            searchmain = repsrc.get_searchpattern()
            searchstring = "%s.*%s" % (searchprefix, searchmain)
            logger.debug("[GitTree] Trying to find '%s' in gittree %s", searchstring, gittree.name)
            for commit_hexsha in gittree.greplogmsgs(searchstring):
                for gitbranch in GitBranch.getall_by_gittreeid(gittree.gittreeid):
                    logger.debug(
                        "[GitTree] Found '%s' in this tree, thus checking branch '%s' now"
                        % (searchstring, gitbranch.describe(gittree.name))
                    )
                    if gitbranch.commit_exists(commit_hexsha, repo=gittree.repo()):
                        logger.debug(
                            "[GitTree] Found %s in %s",
                            commit_hexsha,
                            gitbranch.describe(gittree.name),
                        )
                        commit = gittree.commit(commit_hexsha)
                        getregression(regressionfull, regression.regid).commitmention(
                            gittree, gitbranch, commit
                        )

            if ".." in regression.introduced or len(regression.introduced) < 11:
                # we don't need to search for those
                continue

            searchstring = "Fixes: %s" % regression.introduced[0:12]
            logger.debug("[GitTree] Trying to find '%s' in gittree %s", searchstring, gittree.name)
            for commit_hexsha in gittree.greplogmsgs(searchstring):
                for gitbranch in GitBranch.getall_by_gittreeid(gittree.gittreeid):
                    logger.debug(
                        "[GitTree] Found '%s' in this tree, thus checking branch '%s' now"
                        % (searchstring, gitbranch.describe(gittree.name))
                    )
                    if gitbranch.commit_exists(commit_hexsha, repo=gittree.repo()):
                        if RegHistory.present(commit_hexsha, regid=regression.regid):
                            # no need to add a second entry for commits that already were noticed as related,
                            # for example if this msg that already has a Link: to this regression
                            continue
                        logger.debug(
                            "[GitTree] Found %s in %s",
                            commit_hexsha,
                            gitbranch.describe(gittree.name),
                        )
                        commit = gittree.commit(commit_hexsha)
                        mergedate = gitbranch.merge_date(commit.hexsha, gittree.repo())
                        if gmtime and gmtime > mergedate:
                            # use gmtime instead of mergetime in this case, otherwise entries will show up in strange order
                            mergedate = gmtime + 1

                        # no activity, only a history entry, as it might be about different bug in the same commit
                        RegHistory.event(
                            regression.regid,
                            mergedate,
                            commit.hexsha,
                            commit.summary,
                            "%s" % commit.author,
                            gitbranchid=gitbranch.gitbranchid,
                            regzbotcmd="note: '%s' in '%s' contains a 'Fixes:' tag for the culprit of this regression"
                            % (commit.hexsha[0:12], gitbranch.describe(gittree.name)),
                        )

    def update(self):
        # update
        repo = self.repo()
        if not is_running_citesting("online"):
            for remote in repo.remotes:
                remote.fetch()

        # check for new branches
        for repobranch in repo.remotes.origin.refs:
            # do we care about this branch?
            if re.search(self.branchregex, repobranch.name) is None:
                continue

            gitbranch = GitBranch.get_by_treeid_branchname(self.gittreeid, repobranch.name)

            # if we encounter this branch for the first time, start to track it
            # Note: we'll miss the first batch of commits if this is a new stable branch – but
            # that shouldn't be a problem, as all regressions up to this point are mainline regressions
            # anyway [famous last words?]
            if not gitbranch:
                GitBranch.add(self, repobranch.name, repobranch.commit.hexsha)
                continue

            # if nothing changed, there is nothing to do for us here
            if gitbranch.lastchked == repobranch.commit.hexsha:
                logger.debug("nothing new in %s/%s ", self.name, gitbranch.name)
                continue

            # if this is mainline repo, update the latest versions variable
            # side note: mainline should only have one branch that is relevant for this [famous last words?])
            if self.name == "mainline":
                self.check_latest_versions(repo)

            expected_fixes = RegressionBasic.fixes_expected()
            open_regressions = {}

            # now check new commits for links
            re_link = re.compile(r"(^\s*Link:\s*|^\s*Closes:\s*)(http.*?)(\s.*)?\n", re.MULTILINE)
            for commit in repo.iter_commits(
                ("--reverse", gitbranch.lastchked + ".." + repobranch.commit.hexsha)
            ):
                # is this a commit we are waiting for?
                for expected_fix in expected_fixes:
                    if (
                        expected_fix["solved_entry"]
                        and commit.hexsha.startswith(expected_fix["solved_entry"])
                    ) or (
                        expected_fix["solved_subject"]
                        and commit.summary == expected_fix["solved_subject"]
                    ):
                        regression = RegressionBasic.get_by_regid(expected_fix["regid"])
                        if regression.fixedby_found(self, gitbranch, commit):
                            # this was fixed, no need to look closer at the commit
                            continue

                # does the commit link to a tracked regression?
                for match in re_link.finditer(commit.message):
                    try:
                        regression = RegressionFull.get_by_url(match.group(2))
                    except RepDownloadError:
                        regression = None
                    if not regression:
                        logger.debug(
                            "Saw link to %s, but not aware of any regressions about it",
                            match.group(2),
                        )
                    else:
                        regression.commitmention(self, gitbranch, commit)

                # now check if this commit contains a Fixed: tag that mentions a commit known to cause a regression
                for match in re.finditer(
                    "^(Fixes: )([0-9,a-f]{12})( )", commit.message, re.MULTILINE
                ):
                    # only fill this now, as we only need it if we found a Fixes: tag
                    if len(open_regressions) == 0:
                        for regression in RegressionBasic.get_all(only_unsolved=True):
                            if ".." not in regression.introduced:
                                open_regressions[regression.regid] = regression.introduced[0:12]

                    if match.group(2) not in open_regressions.values():
                        continue
                    for regid in open_regressions.keys():
                        if not open_regressions[regid] == match.group(2):
                            continue
                        if RegHistory.present(
                            commit.hexsha, regid=regid, gitbranchid=gitbranch.gitbranchid
                        ):
                            # no need to add a second entry for commits that already were noticed as related,
                            # for example if this msg that already has a Link: to this regression
                            continue

                        # no activity, only a history entry, as it might be about different bug in the same commit
                        mergedate = gitbranch.merge_date(commit.hexsha, self.repo())
                        RegHistory.event(
                            regid,
                            mergedate,
                            commit.hexsha,
                            commit.summary,
                            "%s" % commit.author,
                            gitbranchid=gitbranch.gitbranchid,
                            regzbotcmd="note: '%s' in '%s' contains a 'Fixes:' tag for the culprit of this regression"
                            % (commit.hexsha[0:12], gitbranch.describe(self.name)),
                        )

            # and we are done here
            gitbranch.set_lastchked(repobranch.commit.hexsha)

    @staticmethod
    def updateall():
        for gittree in GitTree.getall():
            gittree.update()


class RegActivityMonitor:
    def __init__(
        self, actimonid, regid, repsrcid, entry, gmtime, subject, authorname, authormail, lastchk
    ):
        self.actimonid = actimonid
        self.regid = regid
        self.repsrcid = repsrcid
        self.entry = entry
        self.gmtime = gmtime
        self.subject = subject
        self.authorname = authorname
        self.authormail = authormail
        self.lastchk = lastchk

    @cached_property
    def web_url(self):
        return ReportSource.url_by_id(self.repsrcid, self.entry)

    @property
    def realname(self):
        return self.authorname

    @property
    def username(self):
        return self.authormail

    def add_activity(self, repact):
        RegActivityEvent.event(
            repact.gmtime,
            repact.reptrd.id,
            repact.summary,
            repact.realname,
            repact.repsrc.id,
            patchkind=repact.patchkind,
            subentry=repact.id,
            actimonid=self.actimonid,
        )

    @cached_property
    def repsrc(self):
        return ReportSourceObsolete.get_by_id_n_entry(self.repsrcid, self.entry)

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "actmonitor"')
        RegzbotDbMeta.set_tableversion("actmonitor", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE actmonitor (
                actimonid   INTEGER  NOT NULL PRIMARY KEY,
                regid       INTEGER  NOT NULL,
                repsrcid    INTEGER  NOT NULL,
                entry       STRING   NOT NULL,
                gmtime      INTEGER,
                subject     STRING,
                authorname  STRING,
                authormail  STRING,
                lastchk     INTEGER
            )""")

    @staticmethod
    def add(regid, repsrcid, entry, gmtime, subject, authorname, authormail):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """INSERT INTO actmonitor
                            (regid, repsrcid, entry, gmtime, subject, authorname, authormail)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (regid, repsrcid, entry, gmtime, subject, authorname, authormail),
        )

        logger.debug(
            "[db actmonitor] inserting (actimonid:%s, regid:%s, repsrcid:%s, entry:%s, gmtime:%s, subject:%s, authorname:%s, authormail:%s)"
            % (dbcursor.lastrowid, regid, repsrcid, entry, gmtime, subject, authorname, authormail)
        )

        return dbcursor.lastrowid

    def delete(self, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()

        # delete related activities
        for activity in RegActivityEvent.getall_by_actimonid(self.actimonid):
            activity.delete()

        dbcursor.execute(
            """DELETE FROM actmonitor
                         WHERE actimonid=(?)""",
            (self.actimonid,),
        )
        if dbcursor.rowcount > 0:
            logger.debug(
                "[db actmonitor] deleted (actimonid:%s, regid:%s, repsrcid:%s, entry:%s)",
                self.actimonid,
                self.regid,
                self.repsrcid,
                self.entry,
            )
        else:
            logger.critical(
                "[db actmonitor] failed to deleted entry (actimonid:%s, regid:%s, repsrcid:%s, entry:%s;)",
                self.actimonid,
                self.regid,
                self.repsrcid,
                self.entry,
            )

    def remove(self):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """DELETE FROM actmonitor
                         WHERE actimonid=(?)""",
            (self.actimonid,),
        )
        logger.debug(
            "[db actmonitor] deleted (actimonid:%s, regid:%s, repsrcid:%s, entry:%s; %s)"
            % (self.actimonid, self.regid, self.repsrcid, self.entry, dbcursor.lastrowid)
        )
        RegActivityEvent.remove(actimonid=self.actimonid)
        return True

    @staticmethod
    def get(actimonid):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM actmonitor WHERE actimonid=(?)", (actimonid,)
        ).fetchone()
        if dbresult:
            return RegActivityMonitor(*dbresult)
        return None

    @classmethod
    def get_by_reg_n_reptrd(cls, regression, reptrd):
        dbcursor = DBCON.cursor()
        if reptrd.repsrc.kind == "lore":
            for dbresult in dbcursor.execute(
                "SELECT * FROM actmonitor WHERE regid=(?) and entry=(?)",
                (regression.regid, reptrd.id),
            ):
                regactmon = cls(*dbresult)
                if ReportSource.islore(regactmon.repsrcid):
                    yield regactmon
        else:
            for dbresult in dbcursor.execute(
                "SELECT * FROM actmonitor WHERE regid=(?) AND repsrcid=(?) and entry=(?)",
                (regression.regid, reptrd.repsrc.id, reptrd.id),
            ):
                yield cls(*dbresult)

    @classmethod
    def get_by_regid(cls, regid, reports=None):
        dbcursor = DBCON.cursor()
        if reports:
            sqlquery = "SELECT actmonitor.* FROM actmonitor INNER JOIN regressions ON actmonitor.actimonid = regressions.actimonid WHERE regressions.regid=(?) AND actmonitor.actimonid = regressions.actimonid"
        else:
            sqlquery = "SELECT * FROM actmonitor WHERE regid=(?)"

        for dbresult in dbcursor.execute(sqlquery, (regid,)):
            yield RegActivityMonitor(*dbresult)

    @classmethod
    def getall_by_regid(cls, regid, reports=None):
        dbcursor = DBCON.cursor()

        if reports:
            sqlquery = "SELECT actmonitor.* FROM actmonitor INNER JOIN regressions ON actmonitor.actimonid = regressions.actimonid WHERE regressions.regid=(?) AND actmonitor.actimonid = regressions.actimonid"
        else:
            sqlquery = "SELECT * FROM actmonitor WHERE regid=(?)"

        for dbresult in dbcursor.execute(sqlquery, (regid,)):
            yield RegActivityMonitor(*dbresult)

    @staticmethod
    def get_by_entry(entry):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute("SELECT * FROM actmonitor WHERE entry=(?)", (entry,)):
            return RegActivityMonitor(*dbresult)

    @classmethod
    def get_by_reptrd(cls, reptrd):
        if reptrd.repsrc.kind != "lore":
            dbcursor = DBCON.cursor()
            for dbresult in dbcursor.execute(
                "SELECT * FROM actmonitor WHERE repsrcid=(?) AND entry=(?)",
                (reptrd.repsrc.id, reptrd.id),
            ):
                yield RegActivityMonitor(*dbresult)
        else:
            actimonids_found = []
            actimon = cls.get_by_entry(reptrd.id)
            if actimon:
                actimonids_found.append(actimon.actimonid)
                yield actimon
            for msgid in reptrd.ancestors():
                actimon = cls.get_by_regactivity(msgid)
                if not actimon or actimon.actimonid in actimonids_found:
                    continue
                actimonids_found.append(actimon.actimonid)
                yield actimon

    @classmethod
    def get_by_regactivity(cls, entry):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT actmonitor.* FROM actmonitor INNER JOIN regactivity ON regactivity.actimonid = actmonitor.actimonid WHERE regactivity.entry=?",
            (entry,),
        ).fetchone()
        if dbresult:
            return cls(*dbresult)
        return None

    @staticmethod
    def ismonitored(entry, regid=None, repsrcid=None):
        dbcursor = DBCON.cursor()
        if regid and repsrcid:
            if (
                dbcursor.execute(
                    "SELECT * FROM actmonitor WHERE regid=(?) AND repsrcid=(?) AND entry=(?)",
                    (regid, repsrcid, entry),
                ).fetchone()
                is not None
            ):
                return True
        else:
            if (
                dbcursor.execute("SELECT * FROM actmonitor WHERE entry=(?)", (entry,)).fetchone()
                is not None
            ):
                return True

        return False

    def update_author(self, authorname, authormail):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE actmonitor
                            SET authorname = (?), authormail = (?)
                            WHERE actimonid=(?)""",
            (authorname, authormail, self.actimonid),
        )
        logger.debug(
            "[db_actmonitor] %s (regid %s): author is now '%s', authormail now '%s'.",
            self.actimonid,
            self.regid,
            authorname,
            authormail,
        )
        self.authorname = authorname
        self.authormail = authormail


class RegActivityEvent:
    # reminder: can either get added directly or indirectly via RegActivityMonitor,
    # hence eiher _actimonid or _regid is set

    DBCOLS = "regactivity.gmtime, regactivity.entry, regactivity.subentry, regactivity.subject, regactivity.author, regactivity.repsrcid, \
                regactivity.gitbranchid, regactivity.actimonid, regactivity.regid, regactivity.patchkind"

    def __init__(
        self,
        gmtime,
        entry,
        subentry,
        subject,
        author,
        repsrcid,
        gitbranchid,
        actimonid,
        regid,
        patchkind,
    ):
        self.gmtime = gmtime
        self.entry = entry
        self.subentry = subentry
        self.subject = subject
        self.author = author
        self.repsrcid = repsrcid
        self.gitbranchid = gitbranchid
        self._actimonid = actimonid
        self._regid = regid

        if patchkind is None:
            patchkind = 0
        self.patchkind = PatchKind(patchkind)

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "regactivity"')
        RegzbotDbMeta.set_tableversion("regactivity", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE regactivity (
                gmtime       INTEGER  NOT NULL,
                entry        STRING   NOT NULL,
                subject      STRING   NOT NULL,
                author       STRING,
                repsrcid     INTEGER,
                gitbranchid  INTEGER,
                actimonid    INTEGER,
                regid        INTEGER,
                patchkind    INTEGER,
                subentry     STRING
            )""")

    def delete(self, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()

        # delete related activities
        if self.repsrcid and ReportSource.get_by_id(self.repsrcid, dbcursor).ismail():
            RecordProcessedMsgids.delete(self.entry)

        # delete
        if self._actimonid:
            dbcursor.execute(
                """DELETE FROM regactivity
                             WHERE gmtime=(?) AND entry=(?) AND subject=(?) AND actimonid=(?)""",
                (self.gmtime, self.entry, self.subject, self._actimonid),
            )
        elif self._regid:
            dbcursor.execute(
                """DELETE FROM regactivity
                             WHERE gmtime=(?) AND entry=(?) AND subject=(?) AND regid=(?)""",
                (
                    self.gmtime,
                    self.entry,
                    self.subject,
                    self._regid,
                ),
            )

        if dbcursor.rowcount > 0:
            logger.debug(
                '[db regactivity] deleted (gmtime:%s, entry:"%s", subject:"%s", author:"%s", repsrcid:%s, gitbranchid:%s, actimonid:%s, regid:%s)',
                self.gmtime,
                self.entry,
                self.subject,
                self.author,
                self.repsrcid,
                self.gitbranchid,
                self._actimonid,
                self._regid,
            )
        else:
            logger.debug(
                '[db regactivity] failed to deleted delete entry (gmtime:%s, entry:"%s", subject:"%s", author:"%s", repsrcid:%s, gitbranchid:%s, actimonid:%s, regid:%s)',
                self.gmtime,
                self.entry,
                self.subject,
                self.author,
                self.repsrcid,
                self.gitbranchid,
                self._actimonid,
                self._regid,
            )

    @staticmethod
    def event(
        gmtime,
        entry,
        subject,
        author=None,
        repsrcid=None,
        gitbranchid=None,
        actimonid=None,
        regid=None,
        patchkind=0,
        subentry=None,
    ):
        def _getout():
            import traceback

            traceback.print_stack()
            sys.exit(1)

        # a few lines from the department of "this should not happen, but better ensure it doesn't":
        if repsrcid is None and gitbranchid is None:
            logger.critical(
                "this should not happen: RegActivityEvent.event(%s, %s, %s, %s, %s, %s, %s) was called without specifying either repsrcid or gitbranchid; "
                % (gmtime, entry, subject, repsrcid, gitbranchid, actimonid, regid)
            )
            _getout()
        if repsrcid and gitbranchid:
            logger.critical(
                "this should not happen: RegActivityEvent.event(%s, %s, %s, %s, %s, %s, %s) was called with specifying both repsrcid or gitbranchid"
                % (gmtime, entry, subject, repsrcid, gitbranchid, actimonid, regid)
            )
            _getout()

        # a few lines from the department of "this should not happen, but better ensure it doesn't":
        if actimonid is None and regid is None:
            logger.critical(
                "this should not happen: RegActivityEvent.event(%s, %s, %s, %s, %s, %s, %s) was called without specifying either actimonid or regid; "
                % (gmtime, entry, subject, repsrcid, gitbranchid, actimonid, regid)
            )
            _getout()
        if actimonid and regid:
            logger.critical(
                "this should not happen: RegActivityEvent.event(%s, %s, %s, %s, %s, %s, %s) was called with specifying both actimonid or regid"
                % (gmtime, entry, subject, repsrcid, gitbranchid, actimonid, regid)
            )
            _getout()

        patchkind = int(patchkind)

        logger.debug(
            '[db regactivity] insert (gmtime:%s, entry:"%s", subject:"%s", author:"%s", repsrcid:%s, gitbranchid:%s, actimonid:%s, regid:%s, patchkind:%s, subentry:%s)'
            % (
                gmtime,
                entry,
                subject,
                author,
                repsrcid,
                gitbranchid,
                actimonid,
                regid,
                patchkind,
                subentry,
            )
        )
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """INSERT INTO regactivity
                        (gmtime, entry, subject, author, repsrcid, gitbranchid, actimonid, regid, patchkind, subentry)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                gmtime,
                entry,
                subject,
                author,
                repsrcid,
                gitbranchid,
                actimonid,
                regid,
                patchkind,
                subentry,
            ),
        )

    @classmethod
    def getall_by_actimonid(cls, actimonid):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute(
            "SELECT %s FROM regactivity WHERE actimonid=(?)" % RegActivityEvent.DBCOLS, (actimonid,)
        ):
            yield cls(*dbresult)

    @classmethod
    def get_all(cls, regid, onlyonce=True):
        def _getall_actimonids(regid):
            actimonids = list()
            for actimon in RegActivityMonitor.getall_by_regid(regid):
                actimonids.append("%s" % actimon.actimonid)
            return actimonids

        # prepare query with an unkown number of items in the "WHERE IN" clause
        replacements = _getall_actimonids(regid)
        placeholders = ", ".join("?" for unused in replacements)
        replacements.append(regid)

        dbcursor = DBCON.cursor()
        if onlyonce:
            for dbresult in dbcursor.execute(
                "SELECT DISTINCT %s FROM regactivity WHERE actimonid IN (%s) OR regid=(?) ORDER BY gmtime"
                % (RegActivityEvent.DBCOLS, placeholders),
                replacements,
            ):
                yield cls(*dbresult)
        else:
            for dbresult in dbcursor.execute(
                "SELECT %s FROM regactivity WHERE actimonid IN (%s) OR regid=(?) ORDER BY gmtime"
                % (RegActivityEvent.DBCOLS, placeholders),
                replacements,
            ):
                yield cls(*dbresult)

    @staticmethod
    def present(entry, actimonid=None, regid=None, gitbranchid=None, subentry=None):
        if not actimonid and not regid:
            logger.critical(
                "Aborting, RegActivitaEvent.present() called with neither actimonid or regid."
            )
            sys.exit(1)
        elif actimonid and regid:
            logger.critical(
                "Aborting, RegActivitaEvent.present() called with both actimonid or regid set."
            )
            sys.exit(1)

        dbcursor = DBCON.cursor()
        if actimonid:
            if gitbranchid:
                dbresult = dbcursor.execute(
                    "SELECT * FROM regactivity WHERE actimonid=(?) AND entry=(?) AND gitbranchid=(?)",
                    (actimonid, entry, gitbranchid),
                ).fetchone()
            else:
                dbresult = dbcursor.execute(
                    "SELECT * FROM regactivity WHERE actimonid=(?) AND entry=(?)",
                    (actimonid, entry),
                ).fetchone()
        elif regid:
            if gitbranchid:
                dbresult = dbcursor.execute(
                    "SELECT * FROM regactivity WHERE regid=(?) AND entry=(?) AND gitbranchid=(?)",
                    (regid, entry, gitbranchid),
                ).fetchone()
            else:
                dbresult = dbcursor.execute(
                    "SELECT * FROM regactivity WHERE regid=(?) AND entry=(?)", (regid, entry)
                ).fetchone()

        if dbresult is None:
            return False
        else:
            return True

    @staticmethod
    def remove(actimonid=None):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT actimonid FROM regactivity WHERE actimonid=(?)", (actimonid,)
        ).fetchone()
        if dbresult is not None:
            dbcursor.execute(
                """DELETE FROM regactivity
                             WHERE actimonid=(?)""",
                (actimonid,),
            )
            logger.debug("[db regactivity] deleted all lines where actimonid=%s)", actimonid)
            RegActivityEvent.remove(actimonid=dbcursor.lastrowid)
            return True
        return False

    def url(self):
        if self.repsrcid is None:
            return GitBranch.url_by_id(self.gitbranchid, self.entry)
        return ReportSource.url_by_id(self.repsrcid, self.entry, subentry=self.subentry)


class RegBackburner:
    def __init__(self, regid, repsrcid, entry, gmtime, author, subject, timelimit):
        self.regid = regid
        self.gmtime = gmtime
        self.repsrcid = repsrcid
        self.entry = entry
        self.subject = subject
        self.author = author
        self.timelimit = timelimit

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "regbackburner"')
        RegzbotDbMeta.set_tableversion("regbackburner", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE regbackburner (
                regid       INTEGER  NOT NULL,
                repsrcid    INTEGER,
                entry       STRING,
                gmtime      INTEGER,
                author      STRING,
                subject     STRING,
                timelimit   INTEGER
            )""")

    @classmethod
    def add(cls, regid, repsrcid, entry, gmtime, author, subject, timelimit=0):
        dbcursor = DBCON.cursor()

        # delete earlier entry in case there is one
        cls.remove(regid, dbcursor)

        # add entry
        dbcursor.execute(
            """INSERT INTO regbackburner
                            (regid, repsrcid, entry, gmtime, author, subject)
                            VALUES (?, ?, ?, ?, ?, ?)""",
            (regid, repsrcid, entry, gmtime, author, subject),
        )
        logger.debug(
            '[db regbackburner] insert (regid:%s, repsrcid:%s, entry:%s, gmtime:%s, author:"%s", subject:"%s")',
            regid,
            repsrcid,
            entry,
            gmtime,
            author,
            subject,
        )

    @classmethod
    def get_by_regid(cls, regid):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM regbackburner WHERE regid=?", (regid,)
        ).fetchone()
        if dbresult:
            return cls(*dbresult)
        return None

    @staticmethod
    def remove(regid, dbcursor=None):
        if dbcursor is None:
            dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT subject FROM regbackburner WHERE regid=(?)", (regid,)
        ).fetchone()
        if dbresult is not None:
            dbcursor.execute(
                """DELETE FROM regbackburner
                             WHERE regid=(?)""",
                (regid,),
            )
            logger.debug("[db regbackburner] delete (regid:%s, subject:%s)", regid, dbresult[0])
            return True
        return False

    def report_url(self):
        return ReportSource.url_by_id(self.repsrcid, self.entry)


class RegHistory:
    def __init__(self, regid, gmtime, entry, subject, regzbotcmd, gitbranchid, repsrcid, author):
        self.regid = regid
        self.gmtime = gmtime
        self.entry = entry
        self.subject = subject
        self.regzbotcmd = regzbotcmd
        self.gitbranchid = gitbranchid
        self.repsrcid = repsrcid
        self.author = author
        if not self.author:
            self.author = "unknown"

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "reghistory"')
        RegzbotDbMeta.set_tableversion("reghistory", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE reghistory (
                regid       INTEGER  NOT NULL,
                gmtime      INTEGER  NOT NULL,
                entry       STRING   NOT NULL,
                subject     STRING   NOT NULL,
                regzbotcmd  STRING,
                gitbranchid INTEGER,
                repsrcid    INTEGER,
                author      STRING
            )""")

    def delete(self, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()

        if self.repsrcid and ReportSource.get_by_id(self.repsrcid, dbcursor).ismail():
            RecordProcessedMsgids.delete(self.entry)

        dbcursor.execute(
            """DELETE FROM reghistory
                         WHERE regid=(?) AND gmtime=(?) AND entry=(?) AND subject=(?)""",
            (
                self.regid,
                self.gmtime,
                self.entry,
                self.subject,
            ),
        )

        if dbcursor.rowcount > 0:
            logger.debug(
                '[db reghistory] deleted (regid:%s, gmtime:%s, entry:%s, subject:"%s", regzbotcmd:"%s", gitbranchid:%s, repsrcid:%s)',
                self.regid,
                self.gmtime,
                self.entry,
                self.subject,
                self.regzbotcmd,
                self.gitbranchid,
                self.repsrcid,
            )
            return True
        else:
            logger.debug(
                '[db reghistory] failed to deleted entry (regid:%s, gmtime:%s, entry:%s, subject:"%s", regzbotcmd:"%s", gitbranchid:%s, repsrcid:%s)',
                self.regid,
                self.gmtime,
                self.entry,
                self.subject,
                self.regzbotcmd,
                self.gitbranchid,
                self.repsrcid,
            )
            return False

    @staticmethod
    def _event(
        regid, gmtime, entry, subject, author, gitbranchid=None, repsrcid=None, regzbotcmd=None
    ):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """INSERT INTO reghistory
                        (regid, gmtime, entry, subject, author, regzbotcmd, gitbranchid, repsrcid)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (regid, gmtime, entry, subject, author, regzbotcmd, gitbranchid, repsrcid),
        )
        logger.debug(
            '[db reghistory] insert (regid:%s, gmtime:%s, entry:%s, subject:"%s", author:"%s" regzbotcmd:"%s", gitbranchid:%s, repsrcid:%s)'
            % (regid, gmtime, entry, subject, author, regzbotcmd, gitbranchid, repsrcid)
        )
        return dbcursor.lastrowid

    @staticmethod
    def event(
        regid, gmtime, entry, subject, author, repsrcid=None, gitbranchid=None, regzbotcmd=None
    ):
        # a few lines from the department of "this should not happen, but better ensure it doesn't":
        if repsrcid is None and gitbranchid is None:
            logger.critical(
                "this should not happen: RegHistoryEvent.event(%s, %s, %s, %s, %s, %s, %s) was called without specifying either repsrcid or gitbranchid; "
                % (gmtime, entry, subject, repsrcid, gitbranchid, regzbotcmd, regid)
            )
        if repsrcid and gitbranchid:
            logger.critical(
                "this should not happen: RegHistoryEvent.event(%s, %s, %s, %s, %s, %s, %s) was called with specifying both repsrcid or gitbranchid"
                % (gmtime, entry, subject, repsrcid, gitbranchid, regzbotcmd, regid)
            )

        RegHistory._event(
            regid,
            gmtime,
            entry,
            subject,
            author,
            repsrcid=repsrcid,
            gitbranchid=gitbranchid,
            regzbotcmd=regzbotcmd,
        )

    def present(entry, regid=None, repsrcid=None, gitbranchid=None):
        dbcursor = DBCON.cursor()
        if gitbranchid and regid:
            dbresult = dbcursor.execute(
                "SELECT * FROM reghistory WHERE entry=(?) AND gitbranchid=(?) AND regid=(?)",
                (entry, gitbranchid, regid),
            ).fetchone()
        elif repsrcid and regid:
            dbresult = dbcursor.execute(
                "SELECT * FROM reghistory WHERE entry=(?) AND repsrcid=(?) AND regid=(?)",
                (entry, repsrcid, regid),
            ).fetchone()
        elif regid:
            dbresult = dbcursor.execute(
                "SELECT * FROM reghistory WHERE entry=(?) AND regid=(?)", (entry, regid)
            ).fetchone()
        elif repsrcid:
            dbresult = dbcursor.execute(
                "SELECT * FROM reghistory WHERE entry=(?) AND repsrcid=(?)", (entry, repsrcid)
            ).fetchone()
        else:
            dbresult = dbcursor.execute(
                "SELECT * FROM reghistory WHERE entry=(?)", (entry,)
            ).fetchone()

        if dbresult is None:
            return False
        else:
            return True

    @staticmethod
    def filed(regid):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT gmtime FROM reghistory WHERE regzbotcmd LIKE (?) AND regid=(?) ORDER BY gmtime",
            ("%%introduced: %%", regid),
        ).fetchone()
        # fallback, in case introduced command couldn't be found
        if not dbresult:
            dbresult = dbcursor.execute(
                "SELECT gmtime FROM reghistory WHERE regid=(?) ORDER BY gmtime", (regid,)
            ).fetchone()
        # fallback, in case history entry was not created yet
        if not dbresult:
            dbresult = dbcursor.execute(
                "SELECT gmtime FROM actmonitor WHERE regid=(?) ORDER BY gmtime", (regid,)
            ).fetchone()
        return dbresult[0]

    @classmethod
    def get_all(cls, regid):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute(
            "SELECT * FROM reghistory WHERE regid=(?) ORDER BY gmtime", (regid,)
        ):
            yield cls(*dbresult)

    def url(self):
        if self.gitbranchid is not None:
            return GitBranch.url_by_id(self.gitbranchid, self.entry)
        elif self.repsrcid is not None:
            return ReportSource.url_by_id(self.repsrcid, self.entry)
        return None


class RegLink:
    def __init__(self, regid, gmtime, repsrcid, entry, link, subject, author):
        self.regid = regid
        self.gmtime = gmtime
        self.repsrcid = repsrcid
        self.entry = entry
        self.subject = subject
        self.author = author

        if link is not None:
            self.link = link
        else:
            self.link = ReportSource.url_by_id(self.repsrcid, self.entry)

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "reglinks"')
        RegzbotDbMeta.set_tableversion("reglinks", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE reglinks (
                regid       INTEGER  NOT NULL,
                gmtime      INTEGER,
                repsrcid    INTEGER,
                entry       STRING,
                link        STRING,
                subject     STRING,
                author      STRING
            )""")

    @staticmethod
    def add_entry(regid, gmtime, subject, author, repsrcid, entry):
        dbcursor = DBCON.cursor()
        if dbcursor.execute(
            "SELECT entry FROM reglinks WHERE regid=(?) AND repsrcid=(?) AND entry=(?)",
            (regid, repsrcid, entry),
        ).fetchone():
            dbcursor.execute(
                """UPDATE reglinks
                SET gmtime = (?), author = (?), subject = (?)
                WHERE regid=(?) AND repsrcid=(?) AND entry=(?)""",
                (gmtime, author, subject, regid, repsrcid, entry),
            )
            logger.debug(
                '[db reglinks] updated (regid:%s, gmtime:%s, repsrcid:%s, entry:%s, subject:"%s", author:"%s" )'
                % (regid, gmtime, repsrcid, entry, subject, author)
            )
        else:
            dbcursor.execute(
                """INSERT INTO reglinks
                                (regid, gmtime, repsrcid, entry, subject, author)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                (regid, gmtime, repsrcid, entry, subject, author),
            )
            logger.debug(
                '[db reglinks] insert (regid:%s, gmtime:%s, repsrcid:%s, entry:%s, subject:"%s", author:"%s" )'
                % (regid, gmtime, repsrcid, entry, subject, author)
            )

    def remove(self):
        self.remove_entry(self.regid, self.repsrcid, self.entry)

    @staticmethod
    def remove_entry(regid, repsrcid, entry):
        dbcursor = DBCON.cursor()
        if dbcursor.execute(
            "SELECT repsrcid FROM reglinks WHERE regid=(?) AND repsrcid=(?) AND entry=(?)",
            (regid, repsrcid, entry),
        ).fetchone():
            dbcursor.execute(
                """DELETE FROM reglinks
                             WHERE regid=(?) AND repsrcid=(?) AND entry=(?)""",
                (regid, repsrcid, entry),
            )
            logger.debug(
                "[db reglinks] deleted (regid:%s, repsrcid:%s, entry:%s)" % (regid, repsrcid, entry)
            )

    @classmethod
    def get_all(cls, regid, order="ASC"):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute(
            "SELECT * FROM reglinks WHERE regid=(?) ORDER BY gmtime %s" % order, (regid,)
        ):
            yield cls(*dbresult)

    @classmethod
    def get_by_reg_n_reptrd(cls, regression, reptrd):
        dbcursor = DBCON.cursor()
        if reptrd.repsrc.kind == "lore":
            for dbresult in dbcursor.execute(
                "SELECT * FROM reglinks WHERE regid=(?) and entry=(?)",
                (regression.regid, reptrd.id),
            ):
                reglink = cls(*dbresult)
                if ReportSource.islore(reglink.repsrcid):
                    yield reglink
        else:
            for dbresult in dbcursor.execute(
                "SELECT * FROM reglinks WHERE regid=(?) AND repsrcid=(?) and entry=(?)",
                (regression.regid, reptrd.repsrc.id, reptrd.id),
            ):
                yield cls(*dbresult)

    def delete(self, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()

        dbcursor.execute(
            """DELETE FROM reglinks
                         WHERE regid=(?) AND gmtime=(?) AND subject=(?)""",
            (self.regid, self.gmtime, self.subject),
        )

        if dbcursor.rowcount > 0:
            logger.debug(
                '[db reglinks] deleted (regid:%s; subject:"%s" gmtime:%s)',
                self.regid,
                self.gmtime,
                self.subject,
            )
            return True
        else:
            logger.debug(
                '[db reglinks] failed to deleted entry (regid:%s; subject:"%s" gmtime:%s)',
                self.regid,
                self.gmtime,
                self.subject,
            )
            return False


class RegressionBasic:
    DBCOLS = "regressions.regid, regressions.subject, regressions.introduced, regressions.gitbranchid, regressions.actimonid, \
                   regressions.solved_reason, regressions.solved_gmtime, regressions.solved_entry, regressions.solved_subject, \
                   regressions.solved_gitbranchid, regressions.solved_repsrcid, regressions.solved_repentry, regressions.solved_duplicateof"

    def __init__(
        self,
        regid,
        subject,
        introduced,
        gitbranchid,
        actimonid,
        solved_reason=None,
        solved_gmtime=None,
        solved_entry=None,
        solved_subject=None,
        solved_gitbranchid=None,
        solved_repsrcid=None,
        solved_repentry=None,
        solved_duplicateof=None,
    ):
        self.regid = regid
        self.subject = subject
        self.introduced = str(introduced)
        self.gitbranchid = gitbranchid
        self.actimonid = actimonid

        self.solved_reason = solved_reason
        self.solved_gmtime = solved_gmtime
        self.solved_entry = solved_entry
        self.solved_subject = solved_subject
        self.solved_gitbranchid = solved_gitbranchid
        self.solved_repsrcid = solved_repsrcid
        self.solved_repentry = solved_repentry
        self.solved_duplicateof = solved_duplicateof

    @cached_property
    def actimon(self):
        actimon = RegActivityMonitor.get(self.actimonid)
        assert actimon
        return actimon

    @property
    def realname(self):
        return self.actimon.realname

    @property
    def web_url(self):
        return self.actimon.web_url

    @property
    def username(self):
        return self.actimon.username

    @classmethod
    def __create(cls, rgzcmd, reptrd, *, introduced=None, gitbranchid=None):
        if not introduced:
            introduced = rgzcmd.parameters
        regression = cls.__create_obsolete(
            introduced,
            gitbranchid,
            reptrd.repsrc.id,
            reptrd.id,
            reptrd.gmtime,
            reptrd.summary,
            reptrd.realname,
            reptrd.username,
        )

        return regression

    def __duplicate(self, rgzcmd, other):
        if self.regid == other.regid:
            logger.warning(
                'regression[%s, "%s"]: ignoring request to mark this regression as a duplicate of itself.',
                self.regid,
                self.subject,
            )
            return

        if self.actimon.gmtime < other.actimon.gmtime:
            older = self
            younger = other
        else:
            older = other
            younger = self

        younger.solved_gmtime = rgzcmd.repact.gmtime
        younger.solved_duplicateof = older.regid
        younger._db_update_solved()

        if self == older:
            younger.add_history_event(
                rgzcmd, cmdline="duplicate: %s [implicit via duplicate]" % older.web_url
            )
        else:
            older.add_history_event(
                rgzcmd, cmdline="duplicate: %s [implicit via duplicate]" % younger.web_url
            )
        logger.info(
            "Regression(%s): now a duplicate of Regression(%s).", younger.web_url, older.web_url
        )

    def add_history_event(self, rgzcmd, *, cmdline=None):
        if not cmdline:
            cmdline = rgzcmd.cmd
            if rgzcmd.parameters:
                cmdline = "%s: %s" % (cmdline, rgzcmd.parameters)
        RegHistory.event(
            self.regid,
            rgzcmd.repact.gmtime,
            rgzcmd.repact.reptrd.id,
            rgzcmd.repact.summary,
            rgzcmd.repact.realname,
            repsrcid=rgzcmd.repact.repsrc.id,
            regzbotcmd=cmdline,
        )

    def cmd_backburn(self, rgzcmd, reason):
        RegBackburner.add(
            self.regid,
            rgzcmd.repact.repsrc.id,
            rgzcmd.repact.reptrd.id,
            rgzcmd.repact.gmtime,
            rgzcmd.repact.realname,
            reason,
        )

    def cmd_duplicate(self, rgzcmd, reptrd):
        # handle duplicates already tracked
        for actimon in RegActivityMonitor.get_by_reptrd(reptrd):
            if actimon.regid:
                regression = RegressionBasic.get_by_regid(actimon.regid)
                self.__duplicate(rgzcmd, regression)
                return

        # handle duplicates not yet tracked
        if not reptrd.gmtime:
            reptrd.gmtime = rgzcmd.repact.gmtime
        if not reptrd.realname:
            reptrd.realname = rgzcmd.repact.realname
        if not reptrd.summary:
            reptrd.summary = self.subject
        if not reptrd.username:
            reptrd.username = rgzcmd.repact.realname
        regression_created = self.__create(
            rgzcmd, reptrd, introduced=self.introduced, gitbranchid=self.gitbranchid
        )
        regression_created.add_history_event(
            rgzcmd, cmdline="introduced: %s [implicit via duplicate]" % self.introduced
        )
        # for generic urls, take over the subject
        if reptrd.repsrc.kind == "generic":
            regression_created.title(self.subject)
        self.__duplicate(rgzcmd, regression_created)
        return regression_created

    def cmd_fix(self, rgzcmd, hexsha, summary):
        self.fixedby(
            rgzcmd.repact.gmtime,
            hexsha,
            summary,
            repsrcid=rgzcmd.repact.repsrc.repsrcid,
            repentry=rgzcmd.repact.reptrd.id,
        )

    def cmd_from(self, rgzcmd, realname, username):
        self.actimon.update_author(realname, username)
        logger.info(
            "Regression(%s)]: author is now %s, authormail now %s", self.web_url, realname, username
        )

    def cmd_introduced_update(self, rgzcmd, hexsha):
        self.introduced_update(hexsha)

    @classmethod
    def cmd_introduced_new(cls, rgzcmd, hexsha):
        return cls.introduced_create(
            rgzcmd.reptrd.repsrc.id,
            rgzcmd.reptrd.id,
            rgzcmd.reptrd.summary,
            rgzcmd.reptrd.realname,
            rgzcmd.reptrd.username,
            hexsha,
            rgzcmd.reptrd.gmtime,
        )

    def cmd_link(self, rgzcmd, url, description):
        reptrd = ReportThread.from_url(url, repact=rgzcmd.repact)
        if not description:
            description = reptrd.summary
        RegLink.add_entry(
            self.regid, reptrd.gmtime, description, reptrd.realname, reptrd.repsrc.id, reptrd.id
        )
        logger.info('regression[%s, "%s"]: added link %s' % (self.regid, self.subject, url))

    def cmd_monitor(self, rgzcmd, url, description):
        reptrd = ReportThread.from_url(url)
        if reptrd.repsrc.kind == "generic":
            # we can't monitor it, so just add a link
            self.cmd_link(rgzcmd, url, description)
            return
        if not description:
            description = reptrd.summary
        actimonid = RegActivityMonitor.add(
            self.regid,
            reptrd.repsrc.id,
            reptrd.id,
            reptrd.gmtime,
            description,
            reptrd.realname,
            reptrd.username,
        )
        actimon = RegActivityMonitor.get(actimonid)
        RegLink.add_entry(
            self.regid,
            rgzcmd.reptrd.gmtime,
            description,
            reptrd.realname,
            reptrd.repsrc.id,
            reptrd.id,
        )
        reptrd.update(None, None, actimon=actimon, triggering_repact=rgzcmd.repact)
        logger.info('regression[%s, "%s"]: started to monitor %s' % (self.regid, self.subject, url))

    def cmd_resolve(self, rgzcmd, reason):
        cmd = rgzcmd.cmd
        if cmd == "resolve":
            cmd = "resolved"
        self._solve_reason(
            cmd, reason, rgzcmd.repact.gmtime, rgzcmd.repact.reptrd.id, rgzcmd.repact.repsrc.id
        )

    def cmd_unbackburn(self, rgzcmd):
        RegBackburner.remove(self.regid)

    def cmd_unlink(self, rgzcmd, url):
        reptrd = ReportThreadOffline.from_url(url)
        for regactmon in RegActivityMonitor.get_by_reg_n_reptrd(self, reptrd):
            for reglink in RegLink.get_by_reg_n_reptrd(self, reptrd):
                reglink.remove()
            regactmon.remove()
            return True
        for reglink in RegLink.get_by_reg_n_reptrd(self, reptrd):
            reglink.remove()
            return True
        return False

    ####################################################################################################################

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "regressions"')
        RegzbotDbMeta.set_tableversion("regressions", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE regressions (
                regid              INTEGER  NOT NULL PRIMARY KEY,
                subject            STRING   NOT NULL,
                introduced         STRING   NOT NULL,
                gitbranchid        INTEGER,
                actimonid          INTEGER,
                solved_reason      STRING,
                solved_gmtime      INTEGER,
                solved_entry       STRING,
                solved_subject     STRING,
                solved_gitbranchid INTEGER,
                solved_repsrcid    INTEGER,
                solved_repentry    STRING,
                solved_duplicateof INTEGER
            )""")

    def _db_update_solved(self):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE regressions
                            SET solved_reason = (?), solved_gmtime = (?), solved_entry = (?), solved_subject = (?),
                                solved_gitbranchid = (?), solved_repsrcid = (?) , solved_repentry = (?), solved_duplicateof = (?)
                            WHERE regid=(?)""",
            (
                self.solved_reason,
                self.solved_gmtime,
                self.solved_entry,
                self.solved_subject,
                self.solved_gitbranchid,
                self.solved_repsrcid,
                self.solved_repentry,
                self.solved_duplicateof,
                self.regid,
            ),
        )

        # in case it's on backburner, unbackburn this
        if self.solved_reason != "to_be_fixed":
            RegBackburner.remove(self.regid)

        logger.debug(
            '[db regressions] update solved fieds: (regid:%s; solved_reason:%s; solved_gmtime:%s; solved_entry:%s; solved_subject:"%s"; solved_gitbranchid:%s; solved_repsrcid:%s; solved_repentry:%s;  )',
            self.regid,
            self.solved_reason,
            self.solved_gmtime,
            self.solved_entry,
            self.solved_subject,
            self.solved_gitbranchid,
            self.solved_repsrcid,
            self.solved_repentry,
        )

    def delete(self, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()

        for activity in RegActivityEvent.get_all(self.regid, onlyonce=False):
            activity.delete(dbcursor=dbcursor)
        for actimon in RegActivityMonitor.getall_by_regid(self.regid):
            actimon.delete(dbcursor=dbcursor)
        for histevent in RegHistory.get_all(self.regid):
            histevent.delete(dbcursor=dbcursor)
        for link in RegLink.get_all(self.regid):
            link.delete(dbcursor=dbcursor)

        # FIXME: tmp disabled
        # if self.repsrcid and ReportSource.get_by_id(self.repsrcid, dbcursor).ismail():
        #    RecordProcessedMsgids.delete(self.entry)

        dbcursor.execute(
            """DELETE FROM regressions
                         WHERE regid=(?)""",
            (self.regid,),
        )

        if dbcursor.rowcount > 0:
            logger.debug(
                '[db regressions] deleted (regid:%s; subject:"%s"; introduced:%s; gitbranchid:%s)',
                self.regid,
                self.subject,
                self.introduced,
                self.gitbranchid,
            )
            return True
        else:
            logger.debug(
                '[db regressions] failed to deleted entry (regid:%s; subject:"%s"; introduced:%s; gitbranchid:%s)',
                self.regid,
                self.subject,
                self.introduced,
                self.gitbranchid,
            )
            return False

    @classmethod
    def get_all(cls, order="regid", only_unsolved=False):
        dbcursor = DBCON.cursor()

        if only_unsolved:
            for dbresult in dbcursor.execute(
                'SELECT %s FROM regressions WHERE (solved_reason IS NULL AND solved_duplicateof IS NULL) OR solved_reason IS "to_be_fixed" ORDER BY %s'
                % (RegressionBasic.DBCOLS, order)
            ):
                yield cls(*dbresult)
        else:
            for dbresult in dbcursor.execute(
                "SELECT %s FROM regressions ORDER BY %s" % (RegressionBasic.DBCOLS, order)
            ):
                yield cls(*dbresult)

    @classmethod
    def get_by_regid(cls, regid, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT %s FROM regressions WHERE regid=?" % RegressionBasic.DBCOLS, (regid,)
        ).fetchone()
        if dbresult:
            return cls(*dbresult)
        return None

    @classmethod
    def get_by_entry(cls, entry, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()

        dbresult = dbcursor.execute(
            "SELECT %s FROM regressions INNER JOIN actmonitor ON actmonitor.regid = regressions.regid WHERE actmonitor.entry=?"
            % RegressionBasic.DBCOLS,
            (entry,),
        ).fetchone()
        if not dbresult:
            # fallback for deep threads
            dbresult = dbcursor.execute(
                "SELECT %s FROM ((actmonitor INNER JOIN regactivity ON regactivity.actimonid = actmonitor.actimonid) INNER JOIN regressions ON actmonitor.regid = regressions.regid) WHERE regactivity.entry=?; "
                % RegressionBasic.DBCOLS,
                (entry,),
            ).fetchone()

        if dbresult:
            yield cls(*dbresult)
        return None

    @classmethod
    def get_by_reptrd(cls, reptrd):
        for actimon in RegActivityMonitor.get_by_reptrd(reptrd):
            if actimon.regid:
                return cls.get_by_regid(actimon.regid)

    def get_dupes(self, *, recursion_count=-1):
        if recursion_count > 12:
            logger.critical(
                "Aborting, recursion limit in RegActivityMonitor.__walk_duplicates() exceeded."
            )
            sys.exit(1)
        recursion_count += 1

        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute(
            "SELECT %s FROM regressions WHERE solved_duplicateof=(?)" % self.DBCOLS, (self.regid,)
        ):
            regression = self.__class__(*dbresult)
            yield regression
            for duplicate in regression.get_dupes(recursion_count=recursion_count):
                yield duplicate

    def find_topmost(self, *, recursion_count=-1):
        if not self.solved_duplicateof:
            if recursion_count == -1:
                # this regression is not a dup of another
                return
            else:
                # we are at the top
                yield self

        if recursion_count > 12:
            logger.critical(
                "Aborting, recursion limit in RegActivityMonitor.__walk_duplicates() exceeded."
            )
            sys.exit(1)
        recursion_count += 1

        upper_regression = self.get_by_regid(self.solved_duplicateof)
        for regression in upper_regression.find_topmost(recursion_count=recursion_count):
            yield regression
            return

    @classmethod
    def get_by_regactivity(cls, entry):
        dbcursor = DBCON.cursor()

        dbresult = dbcursor.execute(
            "SELECT %s FROM regressions INNER JOIN actmonitor ON actmonitor.regid = regressions.regid WHERE actmonitor.entry=?; "
            % RegressionBasic.DBCOLS,
            (entry,),
        ).fetchone()
        if dbresult:
            return cls(*dbresult)

        # fallback for deep threads
        dbresult = dbcursor.execute(
            "SELECT %s FROM ((actmonitor INNER JOIN regactivity ON regactivity.actimonid = actmonitor.actimonid) INNER JOIN regressions ON actmonitor.regid = regressions.regid) WHERE regactivity.entry=?; "
            % RegressionBasic.DBCOLS,
            (entry,),
        ).fetchone()
        if dbresult:
            return cls(*dbresult)

        return None

    @classmethod
    def get_expected_by_subject(cls, subject):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute(
            "SELECT %s FROM regressions WHERE solved_reason=? AND solved_subject LIKE (?)"
            % RegressionBasic.DBCOLS,
            (
                "to_be_fixed",
                subject,
            ),
        ):
            if dbresult:
                yield cls(*dbresult)

    @classmethod
    def get_by_link(cls, link):
        tmpstring = link
        if tmpstring.startswith("https://"):
            tmpstring = tmpstring.removeprefix("https://")
        elif tmpstring.startswith("http://"):
            tmpstring = tmpstring.removeprefix("http://")

        if tmpstring.startswith("lore.kernel.org/"):
            _, _, tmpstring = tmpstring.split("/", maxsplit=2)
            msgid, _, _ = tmpstring.partition("/")
            for regression in cls.get_by_entry(urldecode(msgid)):
                return regression
        else:
            logger.warning("RegressionBasic.get_by_link(%s): unsupported domain ", link)
        return None

    @classmethod
    def get_by_url(cls, url):
        try:
            reptrd_pointedto = ReportThreadOffline.from_url(url)
        except RepDownloadError:
            return None
        for actimon in RegActivityMonitor.get_by_reptrd(reptrd_pointedto):
            if actimon.regid:
                return cls.get_by_regid(actimon.regid)
        return None

    @staticmethod
    def fixes_expected():
        dbcursor = DBCON.cursor()
        pending = []
        for dbresult in dbcursor.execute(
            "SELECT regid, solved_entry, solved_subject FROM regressions WHERE solved_reason=?",
            ("to_be_fixed",),
        ):
            pending.append(
                {"regid": dbresult[0], "solved_entry": dbresult[1], "solved_subject": dbresult[2]}
            )
        return pending

    @classmethod
    def __introduced_precheck(cls, introduced, gmtime=None):
        # remove everything after the first space, in case someone wrote something like this:
        # regzbot introduced cf68fffb66d6 ("add support for Clang CFI")
        introduced = introduced.split()[0]

        # try to find what tree/branch this belongs
        introduced, _, gitbranch, _ = cls._gettree_n_branch(introduced, gmtime=gmtime)

        if gitbranch:
            return introduced, gitbranch.gitbranchid
        else:
            return introduced, None

    @classmethod
    def __create_obsolete(
        cls, introduced, gitbranchid, repsrcid, entry, gmtime, subject, authorname, authormail
    ):
        dbcursor = DBCON.cursor()

        # create regression
        dbcursor.execute(
            """INSERT INTO regressions
                            (subject, introduced, gitbranchid)
                            VALUES (?, ?, ?)""",
            (subject, introduced, gitbranchid),
        )
        regid = dbcursor.lastrowid

        # create entry for monitoring
        actimonid = RegActivityMonitor.add(
            regid, repsrcid, entry, gmtime, subject, authorname, authormail
        )
        dbcursor.execute(
            """UPDATE regressions
                            SET actimonid = (?)
                            WHERE regid = (?)""",
            (actimonid, regid),
        )

        logger.debug(
            '[db regressions] inserted (regid:%s; subject:"%s"; introduced:%s; actimonid:%s; gitbranchid:%s)',
            regid,
            subject,
            introduced,
            actimonid,
            gitbranchid,
        )

        logger.info('regression[%s, "%s"]: created ("%s"; "%s")', regid, subject, entry, introduced)

        # check if it already got fixed
        regression = cls.get_by_regid(regid)
        repsrc = ReportSourceObsolete.get_by_id_n_entry(repsrcid, entry)
        GitTree.search_references(repsrc, regression, gmtime=gmtime)

        return regression

    @classmethod
    def introduced_create(
        cls, repsrcid, entry, subject, authorname, authormail, introduced, gmtime
    ):
        introduced, gitbranchid = cls.__introduced_precheck(introduced, gmtime)
        return cls.__create_obsolete(
            introduced, gitbranchid, repsrcid, entry, gmtime, subject, authorname, authormail
        )

    def introduced_update(self, tagload):
        self.introduced, self.gitbranchid = self.__introduced_precheck(tagload)

        logger.debug(
            "regression %s (%s): setting introduced to %s",
            self.regid,
            self.subject,
            self.introduced,
        )
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE regressions
                            SET
                            introduced = (?),
                            gitbranchid = (?)
                            WHERE regid=(?)""",
            (self.introduced, self.gitbranchid, self.regid),
        )
        logger.debug(
            '[db regressions] introduced is now %s (regid:%s; subject:"%s" )',
            self.introduced,
            self.regid,
            self.subject,
        )
        logger.info(
            'regression[%s, "%s"]: setting introduced to "%s"',
            self.regid,
            self.subject,
            self.introduced,
        )

    def __create_dup(self, url, gmtime):
        subject = self.subject
        repsrc, entry = ReportSource.get_by_url(url)

        # defaults that normally will be overridden
        authorname = "Unknown"
        authormail = None

        # create regression
        return self.__create_obsolete(
            self.introduced,
            self.gitbranchid,
            repsrc.repsrcid,
            entry,
            gmtime,
            subject,
            authorname,
            authormail,
        )

    def _dupof_direct(
        self, regression_other, gmtime, msgid, msgsubject, authorname, repsrcid, *, history=True
    ):
        if self.regid == regression_other.regid:
            logger.warning(
                'regression[%s, "%s"]: request to mark this a as duplicate of ourselves; aborting',
                self.regid,
                self.subject,
            )
            # FIXME properly
            sys.exit(1)

        if self.solved_subject is None:
            self.solved_subject = regression_other.subject

        self.solved_gmtime = gmtime
        self.solved_duplicateof = regression_other.regid

        self._db_update_solved()

        logger.info(
            'regression[%s, "%s"]: marked as duplicate of regression Regression[%s, "%s"])',
            self.regid,
            self.subject,
            regression_other.regid,
            regression_other.subject,
        )
        if history:
            # make sure this is mentioned in the other regression, too
            RegHistory.event(
                regression_other.regid,
                gmtime,
                msgid,
                self.solved_subject,
                authorname,
                repsrcid=repsrcid,
                regzbotcmd='dup: the regression "%s" was marked as duplicate of this'
                % (self.subject),
            )

    def dupof(self, tagload, gmtime, msgid, msgsubject, authorname, repsrcid):
        def parse(tagload):
            tagload = tagload.split(maxsplit=1)
            url = tagload[0]
            if len(tagload) > 1:
                subject = tagload[1]
            else:
                subject = None
            return url, subject

        urldup, self.solved_subject = parse(tagload)

        regression_other = self.get_by_link(urldup)
        if not regression_other:
            regression_other = self.__create_dup(urldup, gmtime)
            RegHistory.event(
                regression_other.regid,
                gmtime,
                msgid,
                msgsubject,
                authorname,
                repsrcid=repsrcid,
                regzbotcmd="introduced: %s [implicit, due to usage of 'dup-of']" % self.introduced,
            )

        self._dupof_direct(regression_other, gmtime, msgid, msgsubject, authorname, repsrcid)

    def fixed(self, gmtime, commit_hexsha, commit_subject, gitbranchid):
        if self.solved_reason == "fixed":
            logger.info(
                'regression[%s, "%s"]: was marked as fixed by %s earlier, changing it to %s instead.',
                self.regid,
                self.subject,
                self.solved_entry,
                commit_hexsha,
            )

        self.solved_reason = "fixed"
        self.solved_gmtime = gmtime
        self.solved_gitbranchid = gitbranchid
        self.solved_entry = commit_hexsha
        self.solved_subject = commit_subject

        # remove these, as they are unneeded as of now
        self.solved_repsrcid = None
        self.solved_repentry = None

        self._db_update_solved()
        logger.info(
            'regression[%s, "%s"]: marked as %s by %s ("%s")',
            self.regid,
            self.subject,
            self.solved_reason,
            self.solved_entry,
            self.solved_subject,
        )
        return True

    def fixedby(
        self,
        gmtime,
        commit_hexsha,
        commit_subject,
        gitbranchid=None,
        repsrcid=None,
        repentry=None,
        lookup=True,
    ):
        # mark the commit as fixed, unless it's already considered fixed
        if (
            self.solved_reason == "fixed"
            and commit_hexsha
            and self.solved_entry.startswith(commit_hexsha)
        ):
            return True

        self.solved_reason = "to_be_fixed"
        self.solved_gmtime = gmtime
        self.solved_entry = commit_hexsha
        self.solved_subject = commit_subject
        self.solved_entry = commit_hexsha
        self.solved_gitbranchid = gitbranchid
        self.solved_repsrcid = repsrcid
        self.solved_repentry = repentry

        self._db_update_solved()
        logger.info(
            'regression[%s, "%s"]: marked as %s by %s ("%s")',
            self.regid,
            self.subject,
            self.solved_reason,
            self.solved_entry,
            self.solved_subject,
        )

        # look the commit up, in case it was commited already
        if lookup:
            self.lookup_fixedby_everywhere(
                self.solved_entry, self.solved_subject, gmtime=self.solved_gmtime
            )

        return True

    def lookup_fixedby_everywhere(self, commit_hexsha, subject, gmtime=None):
        for gittree, gitbranch, commit_hexsha in GitTree.commit_find_new(
            hexsha=commit_hexsha, subject=subject, ascending=False
        ):
            _, culprit_gittree, _, _ = self._gettree_n_branch(self.introduced)
            logger.debug(
                "[regression.fixedby] specified fix '%s' found in %s/%s",
                commit_hexsha[0:12],
                gittree.name,
                gitbranch.name,
            )
            if culprit_gittree and gittree.priority > culprit_gittree.priority:
                # this is a commit in a downstream repo we can ignore
                continue
            self.fixedby_found(gittree, gitbranch, commit_hexsha, culprit_gittree, gmtime=gmtime)

    def fixedby_found(self, gittree, gitbranch, commit_hexsha, culprit_gittree=None, gmtime=None):
        def add_activity(gittree, gitbranch, commit, mergedate, author):
            RegActivityEvent.event(
                mergedate,
                commit.hexsha,
                "%s, the fix specified through '#regzbot fix:' earlier landed in %s"
                % (commit.hexsha[0:12], gitbranch.describe(gittree.name)),
                gitbranchid=gitbranch.gitbranchid,
                regid=self.regid,
                author=author,
            )

        def add_history(gittree, gitbranch, commit, mergedate, regzbotcmd, author):
            RegHistory.event(
                self.regid,
                mergedate,
                commit.hexsha,
                commit.summary,
                author,
                gitbranchid=gitbranch.gitbranchid,
                regzbotcmd=regzbotcmd,
            )

        def update_solved_data(gitbranch, commit, mergedate):
            self.solved_gitbranchid = gitbranch.gitbranchid
            self.solved_entry = commit.hexsha
            self.solved_subject = commit.summary
            self.solved_gmtime = mergedate
            self._db_update_solved()

        if not culprit_gittree:
            _, culprit_gittree, _, _ = self._gettree_n_branch(self.introduced)

        commit = gittree.commit(commit_hexsha)
        author = "%s" % commit.author
        mergedate = gitbranch.merge_date(commit.hexsha, gittree.repo())

        if RegActivityEvent.present(
            commit.hexsha, regid=self.regid, gitbranchid=gitbranch.gitbranchid
        ):
            # we noticed this one already
            # update data in case a fix came after we noticed it
            if not self.solved_subject:
                update_solved_data(gitbranch, commit, mergedate)
            return

        if self.solved_reason == "fixed" and self.solved_gitbranchid != gitbranch.gitbranchid:
            # we don't care what happens in other gitbranches if the commit landed already where it's supposed to
            # this can happen if something get's commited to mainline and later shows up in next
            return True

        historytext_post = "'fix' commit '%s' now in '%s'" % (
            commit.hexsha[0:12],
            gitbranch.describe(gittree.name),
        )

        if gmtime and gmtime > mergedate:
            # use gmtime instead of mergetime in this case, otherwise entries will show up in strange order
            mergedate = gmtime

        historytext = "note: %s" % historytext_post
        returnval = None
        if culprit_gittree is None or gittree.priority == culprit_gittree.priority:
            # mark the commit as fixed, unless it's already considered fixed
            if not self.solved_reason == "fixed":
                # mark the commit as fixed, unless it's already considered fixed
                historytext = "fixed: %s" % historytext_post
                self.fixed(mergedate, commit.hexsha, commit.summary, gitbranch.gitbranchid)
                returnval = True
        elif gittree.priority < culprit_gittree.priority:
            # the fix hasn't reached the proper tree yet; but we have the commit, so use
            # its data instead of relying on what the user specfied
            update_solved_data(gitbranch, commit, mergedate)
        add_activity(gittree, gitbranch, commit, mergedate, author)
        add_history(gittree, gitbranch, commit, mergedate, historytext, author)

        return returnval

    def _solve_reason(self, reason, tagload, gmtime, msgid, repsrcid):
        self.solved_reason = reason
        self.solved_gmtime = gmtime
        self.solved_subject = tagload
        self.solved_repsrcid = repsrcid
        self.solved_repentry = msgid
        self._db_update_solved()

    def update_author(self, entry, tagload):
        from email.utils import parseaddr

        author, authormail = parseaddr(tagload)

        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE actmonitor
                            SET authorname = (?), authormail = (?)
                            WHERE regid=(?) and entry=(?)""",
            (author, authormail, self.regid, entry),
        )
        logger.debug(
            '[db regressions] author is now %s, authormail now %s (regid:%s; subject:"%s")',
            author,
            authormail,
            self.regid,
            self.subject,
        )
        logger.info(
            'regression[%s, "%s"]: author is now %s, authormail now %s',
            self.regid,
            self.subject,
            author,
            authormail,
        )

        self.author = author
        self.author = authormail

    def title(self, tagload):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE regressions
                            SET subject = (?)
                            WHERE regid=(?)""",
            (tagload, self.regid),
        )
        logger.debug(
            '[db regressions] subject is now %s (regid:%s; subject:"%s" )',
            tagload,
            self.regid,
            self.subject,
        )
        logger.info('regression[%s, "%s"]: subject now "%s"', self.regid, self.subject, tagload)
        self.subject = tagload

    @staticmethod
    def _gettree_n_branch(introduced, gmtime=None):
        if ".." in introduced:
            range_start, range_end = introduced.split("..", 1)
            if not range_end:
                # something like 'v5.15..'
                gittree_start, gitbranch_start = GitTree.commit_find_old(range_start)
                commit = gitbranch_start.head_at_gmtime(gmtime, repo=gittree_start.repo())
                introduced = "%s%s" % (introduced, commit.hexsha)
                return introduced, gittree_start, gitbranch_start, True

            gittree_start, gitbranch_start = GitTree.commit_find_old(range_start)
            gittree_end, gitbranch_end = GitTree.commit_find_old(range_end)
            # make sure to not sort v5.14.15..v5.15.1 into mainline:
            if gitbranch_start and gitbranch_end and gitbranch_start.name == gitbranch_end.name:
                return introduced, gittree_end, gitbranch_end, True
            else:
                return introduced, None, None, True

        else:
            gittree, gitbranch = GitTree.commit_find_old(introduced)
            if gitbranch:
                return introduced, gittree, gitbranch, False
        return introduced, None, None, None


class RegressionFull(RegressionBasic):
    # define variables for other classes we rely on so subclasses can overlay them
    Reglink = RegLink
    Reghistory = RegHistory
    Regactivityevent = RegActivityEvent
    Regactivitymonitor = RegActivityMonitor

    def __init__(self, *args):
        super().__init__(*args)

        self._dupes = self._init_dupes(list())

        self._actim_report, self._actim_monitored = self._init_actimons(
            list(), self.Regactivitymonitor
        )
        self.gmtime = self._actim_report.gmtime

        self._links = self._init_related_objects(list(), self.Reglink)
        self._histevents = self._init_related_objects(list(), self.Reghistory)
        self._actievents = self._init_related_objects(list(), self.Regactivityevent)

        self.backburner = RegBackburner.get_by_regid(self.regid)

        self.poked = self._get_poked(self._histevents, self._actievents)

        # provide a default that is overwritten
        self.treename = "mainline"

        self.identified = False
        self._introduced_short, _ = self._get_presentable(self.introduced)

        self.versionline = None
        self.gmtime_filed = RegHistory.filed(self.regid)

        self.gittree = None
        self._branchname = None
        self._introduced_url = None
        self._introduced_presentable = None
        self._solved_entry_presentable = None

        if self.gitbranchid:
            gitbranch = GitBranch.get_by_id(self.gitbranchid)
            self.gittree = GitTree.get_by_id(gitbranch.gittreeid)

            # catch commits that were introduced and reported in next but moved to master
            if self.gittree.name == "next":
                _, tmpgittree, tmpgitbranch, _ = RegressionBasic._gettree_n_branch(self.introduced)
                if tmpgittree.name == "master":
                    gitbranch = tmpgitbranch
                    self.gittree = tmpgittree

            self.treename = self.gittree.name
            self._branchname = gitbranch.name
            self._introduced_presentable, self.versionline = self._get_presentable(
                self.introduced, gittree=self.gittree
            )
            if self._introduced_short == self._introduced_presentable:
                self._introduced_presentable = None

            if ".." not in self.introduced:
                self._introduced_url = gitbranch.url(self.introduced, self.gittree)

        if self.solved_gitbranchid:
            self._solved_entry_presentable, _ = self._get_presentable(
                self.solved_entry, gittree=self.gittree
            )
            self.solved_url = GitBranch.url_by_id(self.solved_gitbranchid, self.solved_entry)
        #
        # FIXMELATER: link to fixes in next that are supposed to fix this, but haven't reach master yet
        #
        elif self.solved_repsrcid:
            self.solved_url = ReportSource.url_by_id(self.solved_repsrcid, self.solved_repentry)
        else:
            self.solved_url = None

    def _init_related_objects(self, datalist, cls):
        for obj in cls.get_all(self.regid):
            datalist.append(obj)
        return datalist

    def _init_dupes(self, datalist):
        if not self.solved_duplicateof:
            for regression in self.get_dupes():
                datalist.append(regression)
        return datalist

    def _init_actimons(self, datalist, cls):
        for actimon in cls.get_by_regid(self.regid):
            if self.actimonid == actimon.actimonid:
                report = actimon
            else:
                datalist.append(actimon)
        return report, datalist

    def _get_poked(self, histevents, actievents):
        if (
            len(histevents) > 0
            and histevents[-1].regzbotcmd.startswith("poke")
            and (len(actievents) > 0 and histevents[-1].gmtime > actievents[-1].gmtime)
        ):
            return histevents[-1]
        return False

    def _get_presentable(self, gitref, gittree=None, getversionline=None):
        def iscommitid(commitid):
            if commitid is None or commitid is False or commitid is True:
                return False
            elif re.search("^[0-9a-fA-F]{8,40}", commitid) is not None:
                return True
            else:
                return False

        def lookup_commit(commitid, contains):
            if iscommitid(commitid):
                description, present = gittree.commit_describe(commitid, contains)
                if description is None:
                    # fallback for situations where a commit is present, but can't be described since it happened after the last tag
                    description = commitid
                return description, present
            return commitid, None

        def shorten(commitid):
            if iscommitid(commitid) and len(commitid) > 11:
                return commitid[0:12]
            else:
                return commitid

        def combine(point1, point2):
            point1 = shorten(point1)
            point2 = shorten(point2)
            if point1 is not None:
                return "%s..%s" % (point1, point2)
            else:
                return "%s" % (point2)

        def isdevcycle(series, version):
            if LATEST_VERSIONS[series] and version.startswith(LATEST_VERSIONS[series]):
                return True
            return False

        # use str() here, as a hexsha might be read as a int if we are unlucky

        gitref = str(gitref)
        if gitref is None:
            return None, None
        elif ".." in gitref:
            point1, point2 = gitref.split("..", 1)
        else:
            point1 = None
            point2 = gitref

        point1pres = None
        point2pres = None
        if gittree is not None:
            if point1 is not None:
                point1, point1pres = lookup_commit(point1, False)
            point2, point2pres = lookup_commit(point2, True)

            # while at it, update this:
            if point1 is None and point2pres:
                self.identified = True

        # now find the versionline, if we need it
        if self.treename != "mainline":
            return combine(point1, point2), None

        # handle all regressions specifying a commit
        if point2 and not point1:
            if isdevcycle("indevelopment", point2):
                # from the current cycle
                return combine(point1, point2), "indevelopment"
            if isdevcycle("latest", point2):
                # from the current cycle
                return combine(point1, point2), "latest"
            elif iscommitid(point2) and point2pres:
                # commit is present, but 'git describe --tags' failed, which means: commit happenend since the last tag
                return combine(point1, point2), "indevelopment"
            else:
                # this commit could not be found, so just put it in the default section
                return combine(point1, point2), "previous"

        # now handle ranges
        if isdevcycle("indevelopment", point2):
            # this checks:
            # 1) if range starts with the same version number
            # 2) if range starts with the number from the previous cycle (catches mainline and stable releases)
            if isdevcycle("indevelopment", point1) or point1.startswith(LATEST_VERSIONS["latest"]):
                return combine(point1, point2), "indevelopment"
        if isdevcycle("latest", point2):
            if isdevcycle("latest", point1) or point1.startswith(LATEST_VERSIONS["previous"]):
                return combine(point1, point2), "latest"

        # default: either its and older range or something doesn't match up, which can happen if user specifies odd ranges
        return combine(point1, point2), "previous"

    def commitmention(self, gittree, gitbranch, commit):
        mergedate = gitbranch.merge_date(commit.hexsha)
        author = "%s" % commit.author
        regzbotcmd = "%s in %s referred to this regression" % (
            commit.hexsha[0:12],
            gitbranch.describe(gittree.name),
        )

        RegActivityEvent.event(
            mergedate,
            commit.hexsha,
            "Commit %s in %s" % (commit.hexsha[0:12], gitbranch.describe(gittree.name)),
            gitbranchid=gitbranch.gitbranchid,
            regid=self.regid,
            author=author,
        )

        if self.treename == gittree.name:
            self.fixed(mergedate, commit.hexsha, commit.summary, gitbranch.gitbranchid)
            RegHistory.event(
                self.regid,
                mergedate,
                commit.hexsha,
                commit.summary,
                author,
                gitbranchid=gitbranch.gitbranchid,
                regzbotcmd="fix: %s [implicit, due to a Link/Closes tag]" % commit.hexsha[0:12],
            )
            for duplicate in self.find_topmost():
                if self.regid != duplicate.regid:
                    duplicate.fixed(mergedate, commit.hexsha, commit.summary, gitbranch.gitbranchid)
                    RegHistory.event(
                        duplicate.regid,
                        mergedate,
                        commit.hexsha,
                        commit.summary,
                        author,
                        gitbranchid=gitbranch.gitbranchid,
                        regzbotcmd="fix: %s [implicit, due to a Link/Closes tag]"
                        % commit.hexsha[0:12],
                    )
        else:
            # downstream? then just add a note
            if self.gittree and gittree.priority > self.gittree.priority:
                RegHistory.event(
                    self.regid,
                    mergedate,
                    commit.hexsha,
                    commit.summary,
                    author,
                    gitbranchid=gitbranch.gitbranchid,
                    regzbotcmd="note: %s" % regzbotcmd,
                )
                return
            # upstream and already fixed? then just add a note
            elif (
                self.solved_reason == "fixed"
                and self.gittree
                and gittree.priority < self.gittree.priority
            ):
                RegHistory.event(
                    self.regid,
                    mergedate,
                    commit.hexsha,
                    commit.summary,
                    author,
                    gitbranchid=gitbranch.gitbranchid,
                    regzbotcmd="note: %s" % regzbotcmd,
                )
                return

            RegHistory.event(
                self.regid,
                mergedate,
                commit.hexsha,
                commit.summary,
                author,
                gitbranchid=gitbranch.gitbranchid,
                regzbotcmd="fix: %s" % regzbotcmd,
            )
            self.fixedby(
                mergedate, commit.hexsha, commit.summary, gitbranch.gitbranchid, lookup=False
            )

    @staticmethod
    def get_by_entry(entry):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT %s FROM regressions INNER JOIN actmonitor ON actmonitor.regid = regressions.regid WHERE actmonitor.actimonid = regressions.actimonid AND actmonitor.entry=?"
            % RegressionBasic.DBCOLS,
            (entry,),
        ).fetchone()
        if dbresult:
            return RegressionFull(*dbresult)
        return None


class UnhandledEvent:
    def __init__(
        self,
        unhanid,
        link,
        note,
        gmtime,
        regid,
        subject,
        solved_gmtime,
        solved_link,
        solved_subject,
    ):
        self.unhanid = unhanid
        self.link = link
        self.note = note
        self.gmtime = gmtime
        self.regid = regid
        self.subject = subject
        self.solved_gmtime = solved_gmtime
        self.solved_link = solved_link
        self.solved_subject = solved_subject

    @staticmethod
    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "unhandled"')
        RegzbotDbMeta.set_tableversion("unhandled", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE unhandled (
                unhanid         INTEGER  NOT NULL PRIMARY KEY,
                link            STRING   NOT NULL,
                note            STRING   NOT NULL,
                gmtime          INTEGER,
                regid           INTEGER,
                subject         STRING,
                solved_gmtime   INTEGER,
                solved_link     STRING,
                solved_subject  STRING
            )""")

    @staticmethod
    def add(link, note, gmtime=None, regid=None, subject=None):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """INSERT INTO unhandled
            (link, note, gmtime, regid, subject)
            VALUES (?, ?, ?, ?, ?)""",
            (link, note, gmtime, regid, subject),
        )
        logger.debug(
            '[db unhandled] insert (unhanid:%s, link:%s,  note:%s, gmtime:%s,regid:%s, subject:"%s")'
            % (dbcursor.lastrowid, link, note, gmtime, regid, subject)
        )

    @classmethod
    def get_all(cls):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute("SELECT * FROM unhandled ORDER BY unhanid"):
            yield cls(*dbresult)


class ReportSource:
    __ids_lore = []

    def __init__(
        self, repsrcid, priority, name, serverurl, kind, weburl, identifiers, lastchked, mininterval
    ):
        self.id = repsrcid
        self.repsrcid = repsrcid
        self.name = name
        self.serverurl = serverurl
        self.kind = kind
        self.weburl = weburl
        self.identifiers = identifiers
        self.lastchked = lastchked
        self.priority = priority
        self.mininterval = mininterval

    def __new__(cls, *args, **kwargs):
        if args[4] == "bugzilla":
            return super().__new__(_repsources._bugzilla.BzRepSrc)
        elif args[4] == "gitlab":
            return super().__new__(_repsources._gitlab.GlRepSrc)
        elif args[4] == "github":
            return super().__new__(_repsources._github.GhRepSrc)
        elif args[4] == "generic":
            return super().__new__(_repsources._generic.GenRepSrc)
        elif args[4] == "lore":
            return super().__new__(_repsources._lore.LoRepSrc)
        else:
            return super().__new__(cls)

    @cached_property
    def generic_name(self):
        if self.kind == "lore":
            return self.kind
        return self.name

    def db_create(version, dbcursor):
        logger.debug('Initializing new dbtable "reportsources"')
        RegzbotDbMeta.set_tableversion("reportsources", version, dbcursor)
        dbcursor.execute("""
            CREATE TABLE reportsources (
                repsrcid    INTEGER  NOT NULL PRIMARY KEY,
                priority    INTEGER  NOT NULL,
                name        STRING   NOT NULL,
                serverurl   STRING   NOT NULL,
                kind        STRING   NOT NULL,
                weburl      STRING   NOT NULL,
                identifiers STRING,
                lastchked   STRING,
                mininterval INT
            )""")

    @classmethod
    def add_or_modify(
        cls,
        name,
        priority,
        serverurl,
        kind,
        weburl,
        identifiers=None,
        lastchked=None,
        mininterval=None,
    ):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT repsrcid, priority, serverurl, weburl, identifiers, mininterval FROM reportsources WHERE name LIKE (?) AND kind LIKE (?)",
            (name, kind),
        ).fetchone()
        if not dbresult:
            cls.add(name, priority, serverurl, kind, weburl, identifiers, lastchked, mininterval)
        else:
            (db_repsrcid, db_priority, db_serverurl, db_weburl, db_identifiers, db_mininterval) = (
                dbresult
            )
            if (
                db_priority != priority
                or db_serverurl != serverurl
                or db_weburl != weburl
                or db_identifiers != identifiers
                or db_mininterval != mininterval
            ):
                cls.modify(
                    dbresult[0], name, priority, serverurl, kind, weburl, identifiers, mininterval
                )
        return None

    @staticmethod
    def add(
        name, priority, serverurl, kind, weburl, identifiers=None, lastchked=None, mininterval=None
    ):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """INSERT INTO reportsources
            (name, serverurl, kind, priority, weburl, identifiers, lastchked, mininterval)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, serverurl, kind, priority, weburl, identifiers, lastchked, mininterval),
        )
        logger.debug(
            "[db reportsources] insert (repsrcid:%s, name:%s, serverurl:%s, kind:%s, priority:%s, weburl:%s, identifiers:%s, lastchked:%s, mininterval:%s)"
            % (
                dbcursor.lastrowid,
                name,
                serverurl,
                kind,
                priority,
                weburl,
                identifiers,
                lastchked,
                mininterval,
            )
        )
        return dbcursor.lastrowid

    @staticmethod
    def modify(
        repsrcid, name, priority, serverurl, kind, weburl, identifiers=None, mininterval=None
    ):
        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE reportsources
                            SET serverurl = (?), priority = (?), weburl = (?), identifiers = (?), mininterval  = (?)
                            WHERE repsrcid=(?)""",
            (serverurl, priority, weburl, identifiers, mininterval, repsrcid),
        )
        logger.debug(
            "[db reportsources] updated (repsrcid:%s, name:%s, serverurl:%s, kind:%s, priority:%s, weburl:%s, identifiers:%s, mininterval:%s)"
            % (repsrcid, name, serverurl, kind, priority, weburl, identifiers, mininterval)
        )

    def delete(self, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            """DELETE FROM reportsources
                                    WHERE repsrcid=(?)""",
            (self.repsrcid,),
        )

        if dbcursor.rowcount > 0:
            logger.debug("[db reportsources] deleted entry (%s)", dbresult)
            return True
        logger.debug("[db reportsources] failed to deleted entry (%s)", dbresult)
        return False

    def ismail(self):
        if self.kind == "lore":
            return True
        return False

    @classmethod
    def islore(cls, repsrcid):
        if not cls.__ids_lore:
            for repsrc in cls.getall_bykind("lore"):
                cls.__ids_lore.append(repsrc.repsrcid)
        if repsrcid in cls.__ids_lore:
            return True
        return False

    @classmethod
    def get_by_id(cls, repsrcid, dbcursor=None):
        if not dbcursor:
            dbcursor = DBCON.cursor()

        dbresult = dbcursor.execute(
            "SELECT * FROM reportsources WHERE repsrcid=(?)", (repsrcid,)
        ).fetchone()
        if dbresult:
            return cls(*dbresult)
        return None

    @staticmethod
    def get_byweburl(url):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM reportsources WHERE weburl LIKE (?)", (url,)
        ).fetchone()
        if dbresult:
            return ReportSource(*dbresult)
        return None

    @staticmethod
    def getall():
        dbcursor = DBCON.cursor()
        generic = None
        for dbresult in dbcursor.execute("SELECT * FROM reportsources"):
            repsrc = ReportSource(*dbresult)
            if repsrc.kind == "generic":
                generic = repsrc
                continue
            yield repsrc
        yield generic

    @staticmethod
    def getall_bykind(kind):
        dbcursor = DBCON.cursor()
        for dbresult in dbcursor.execute(
            "SELECT * FROM reportsources WHERE kind=(?) ORDER BY priority ASC", (kind,)
        ):
            yield ReportSource(*dbresult)

    @staticmethod
    def get_by_name(name):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM reportsources WHERE name LIKE (?)", (name,)
        ).fetchone()
        if dbresult:
            return ReportSource(*dbresult)

    @staticmethod
    def get_by_identifier(identifier):
        dbcursor = DBCON.cursor()
        dbresult = dbcursor.execute(
            "SELECT * FROM reportsources WHERE identifiers LIKE (?)", ("%%%s%%" % identifier,)
        ).fetchone()
        if dbresult:
            return ReportSource(*dbresult)
        return None

    @classmethod
    def get_by_url(cls, url):
        # old code for classes not yet converted
        splitted_url = url.split("/")
        lowered_url = url.lower().split("/")
        if url.startswith("http://"):
            lowered_wo_protocol = url.lower().removeprefix("http://")
        elif url.startswith("https://"):
            lowered_wo_protocol = url.lower().removeprefix("https://")

        if not lowered_url[0].startswith("http"):
            # whatever you are, I'm taking you just as your are...
            pass
        elif lowered_wo_protocol.startswith("bugzilla.kernel.org/show_bug.cgi?id="):
            repsrc = cls.get_byweburl("https://bugzilla.kernel.org/show_bug.cgi?id=")
            if repsrc:
                ticketid = lowered_wo_protocol.removeprefix("bugzilla.kernel.org/show_bug.cgi?id=")
                repsrc = cls.get_byweburl("https://bugzilla.kernel.org/show_bug.cgi?id=")
                return repsrc, ticketid
        elif lowered_url[2] == "lore.kernel.org":
            if lowered_url[3] == "all":
                logger.debug("ReportSource.get_by_url: FIXME")
                sys.exit(1)
            repsrc = cls.get_byweburl("https://%s/%s/" % (lowered_url[2], lowered_url[3]))
            if repsrc:
                return repsrc, splitted_url[4]

        repsrc = cls.get_by_name("generic")
        if not repsrc:
            logger.debug("ReportSource.get_by_url: genric entry not found, aborting")
            sys.exit(1)
        return repsrc, url

    @staticmethod
    def url_by_id(repsrcid, entry, subentry=None):
        repsrc = ReportSource.get_by_id(repsrcid)
        return repsrc.url(entry, subentry=subentry)

    def url(self, entry, *, redirector=None, subentry=None):
        if self.kind == "generic":
            return entry
        elif self.kind == "bugzilla":
            if subentry and subentry < 10000:
                return "%s/show_bug.cgi?id=%s#c%s" % (
                    self.serverurl.removeprefix("/"),
                    entry,
                    subentry,
                )
            return "%s%s" % (self.weburl, entry)
        elif self.kind == "lore":
            if redirector:
                return "https://lore.kernel.org/r/%s/" % urlencode(entry)
            else:
                return "%s%s/" % (self.weburl, urlencode(entry))
        elif self.kind == "gitlab":
            return "%s/-/issues/%s" % (self.serverurl.removeprefix("/"), entry)
        elif self.kind == "github":
            return "%s/issues/%s" % (self.serverurl.removeprefix("/"), entry)
        logger.critical("ReportSource doesn't yet known how to return a URL for %s", self.kind)
        return None

    def set_lastchked(self, lastchked):
        if isinstance(lastchked, int):
            self.lastchked = lastchked
        elif isinstance(lastchked, datetime.datetime):
            self.lastchked = timendate_dt_to_gmtime(lastchked)
        else:
            raise RuntimeError

        dbcursor = DBCON.cursor()
        dbcursor.execute(
            """UPDATE reportsources SET lastchked = (?) WHERE repsrcid=(?)""",
            (self.lastchked, self.repsrcid),
        )

    def update(self):
        if self.kind == "generic":
            return
        raise NotImplementedError

    @classmethod
    def update_all(cls):
        # process regressions list first, as it is usually CCed when regressions are added; that way we reduce the risk
        # of a race (e.g. fix is sent to lkml before regzbot hit the mail to track the regression that is being fixed)
        reggressions_list = cls.get_by_name("regressions")
        reggressions_list.update()
        db_commit()
        reggressions_list = None

        for repsrc in cls.getall():
            if repsrc.kind not in ("bugzilla", "gitlab", "github", "lore"):
                continue
            if repsrc.kind == "lore" and repsrc.name == "regressions":
                continue
            repsrc.update()
            db_commit()

    def supports_url(self, url_lowered, url_parsed):
        return url_parsed.geturl()


class ReportActivity:
    def __init__(self):
        # ensure self.id is present, but accept None:
        _ = self.id

        assert self.reptrd
        if "repsrc" not in self.__dict__:
            self.repsrc = self.reptrd.repsrc

    @property
    def web_url(self, *, redirector=None, subentry=None):
        return self.repsrc.url(self.reptrd.id, subentry=self.id)


class ReportThreadOffline:
    def __init__(self, repsrc, id):
        self.id = id
        self.repsrc = repsrc

    @classmethod
    def from_url(cls, url):
        try:
            url_lowered = url.lower()
            url_parsed = urllib.parse.urlparse(url)
        except ValueError:
            # this can happen when parsing fails, for example
            # https://lore.kernel.org/linux-arm-kernel/8f777b2f-ab15-4df7-91e5-f779f408fd65@app.fastmail.com/
            # leads to ValueError("Invalid IPv6 URL")
            raise RepDownloadError
        for repsrc in ReportSource.getall():
            id = repsrc.supports_url(url_lowered, url_parsed)
            if id:
                return cls(repsrc, id)

    def ancestors(self):
        # required to be able to pass ReportThreadOffline objects to functions that can handle ReportThread as well;
        # due to the yield after the return python will think this is a iterator
        return
        yield


class ReportThread(ReportThreadOffline):
    def __init__(self):
        _ = self.id
        if "supports_relatives" not in self.__dict__:
            self.supports_relatives = False

    @classmethod
    def from_url(cls, url, *, repact=None, regression=None):
        reptrd_offline = ReportThreadOffline.from_url(url)
        reptrd = reptrd_offline.repsrc.thread(id=reptrd_offline.id)
        if reptrd_offline.repsrc.kind == "generic":
            # nothing found, so assume generic
            if repact:
                # these fields would be "unkown" otherwise
                reptrd.created_at = repact.created_at
                reptrd.realname = repact.realname
                reptrd.summary = repact.summary
                reptrd.username = repact.username
        return reptrd


class ReportSourceObsolete(ReportSource):
    def __init__(self, *args):
        self.entryid = None
        super().__init__(*args)

    def __new__(cls, *args, **kwargs):
        # until this class can die: override the overridden ReportSource __new__,
        # as we otherwise get classes like GlReportSource
        return object.__new__(cls)

    @classmethod
    def get_by_id_n_entry(cls, repsrcid, entryid, dbcursor=None):
        repsrc = cls.get_by_id(repsrcid, dbcursor)
        repsrc.entryid = entryid
        return repsrc

    @classmethod
    def get_by_url(cls, url):
        cls, entryid = super().get_by_url(url)
        return cls

    def get_searchpattern(self):
        if not self.entryid:
            logger.critical(
                "ReportSourceObsolete.get_searchpattern() called while self.entryid is unset"
            )
            sys.exit(1)
        elif self.kind == "generic":
            return self.entryid
        elif self.kind == "lore":
            return "https://lore.kernel.org/.*/%s" % urlencode(self.entryid)
        elif self.kind == "bugzilla":
            return "%s/show_bug.cgi?id=%s" % (self.serverurl.removeprefix("/"), self.entryid)
        elif self.kind == "gitlab":
            return "%s/-/issues/%s" % (self.serverurl.removeprefix("/"), self.entryid)
        elif self.kind == "github":
            return "%s/issues/%s" % (self.serverurl.removeprefix("/"), self.entryid)
        logger.critical(
            "ReportSourceObsolete.get_searchpattern() doesn't yet known how to return a URL for %s",
            self.kind,
        )
        return None


class RepDownloadError(Exception):
    pass


def db_close():
    global DBCON
    DBCON.close()
    DBCON = None


def db_commit():
    DBCON.commit()


def db_create(directory):
    def db_create_all(dbcursor):
        RegzbotDbMeta.db_create(1, dbcursor)
        RegzbotState.db_create(1, dbcursor)
        RegActivityMonitor.db_create(1, dbcursor)
        GitTree.db_create(1, dbcursor)
        GitBranch.db_create(1, dbcursor)
        RecordProcessedMsgids.db_create(1, dbcursor)
        RegressionBasic.db_create(1, dbcursor)
        RegActivityEvent.db_create(1, dbcursor)
        RegBackburner.db_create(1, dbcursor)
        RegHistory.db_create(1, dbcursor)
        RegLink.db_create(1, dbcursor)
        ReportSource.db_create(1, dbcursor)
        UnhandledEvent.db_create(1, dbcursor)

    if not basicressource_checkdir_exists(directory, create=True):
        logger.error("Aborting, directory '%s' exist already." % directory)
        sys.exit(1)

    logger.info("Creating database in %s" % directory)
    dbcon = db_init(directory, create=True)
    if not dbcon:
        logger.error("Aborting, failed creating database.")
        sys.exit(1)

    dbcursor = DBCON.cursor()
    db_create_all(dbcursor)
    db_commit()
    return True


def db_init(directory, create=False):
    dbfile = os.path.join(directory, "database.db")
    if create:
        if os.path.isfile(dbfile):
            logger.warning("Database file '%s' already exists, skipping creation" % dbfile)
            return False
    elif not os.path.isfile(dbfile):
        logger.warning("aborting, database file '%s' doesn't exist" % dbfile)
        return False

    global DBCON
    if DBCON is None:
        DBCON = sqlite3.connect(dbfile, sqlite3.PARSE_DECLTYPES)

    return DBCON


def db_rollback():
    DBCON.rollback()


def db_dump(filehdl, order="regid"):
    import export_csv

    for data in export_csv.dumpall_csv(order=order):
        filehdl.write(data)


def db_diff(filehdl_old, filehdl_new, filedesc_old="before", filedesc_new="after"):
    diff = difflib.unified_diff(
        filehdl_old.readlines(),
        filehdl_new.readlines(),
        fromfile="%s" % filedesc_old,
        tofile="%s" % filedesc_new,
        n=1,
    )

    differences = False
    for line in diff:
        if differences is False:
            differences = True
            sys.stdout.write("The results from don't match the expected results:\n")
            sys.stdout.write("#######\n")
        sys.stdout.write(line)

    return differences


def init_reposdir(directory):
    global REPOSDIR
    REPOSDIR = os.path.join(directory)
    GitTree.check_latest_versions()
    return REPOSDIR


def days_delta(past):
    return (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.datetime.fromtimestamp(past, datetime.timezone.utc)
    ).days


def timendate_now():
    return datetime.datetime.now(datetime.timezone.utc)


def timendate_dt_to_gmtime(dt):
    return int(dt.timestamp())


def timendate_gmtime_to_dt(gmtime):
    return datetime.datetime.fromtimestamp(gmtime, tz=datetime.timezone.utc)


def parse_link(url):
    tmpstring = url

    if tmpstring.startswith("https://"):
        tmpstring = tmpstring.removeprefix("https://")
    elif tmpstring.startswith("http://"):
        tmpstring = tmpstring.removeprefix("http://")

    domain = mlist = msgid = None
    if tmpstring.startswith("lore.kernel.org") or tmpstring.startswith("lkml.kernel.org"):
        domain = "lore.kernel.org"
        tmplist = tmpstring.split("/", maxsplit=2)
        if len(tmplist) <= 2:
            logger.debug("Ignoring %s, failed to parse", url)
            return None, None, None

        mlist = tmplist[1]
        tmpstring = tmplist[2]

        msgid, _, _ = tmpstring.partition("/")

        if mlist == "r":
            if tmpstring.startswith("lkml.kernel.org"):
                mlist = "lkml"
            else:
                # FIXMELATER: this is the lore redirector; for now just assume it redirecting to LKML, which likely needs fixing later
                mlist = "lkml"
    elif tmpstring.startswith("bugzilla.kernel.org"):
        bugid = tmpstring.removeprefix("bugzilla.kernel.org/show_bug.cgi?id=")
        if bugid.isnumeric():
            msgid = bugid
            domain = "bugzilla.kernel.org"
        else:
            logger.debug("Tried to get bugid from %s, but failed", url)
    else:
        logger.debug("Tried to get msgid from %s, but don't known how to handle that domain", url)
    return domain, mlist, msgid


def basicressource_checkdir_exists(directory, create=False):
    try:
        if os.path.exists(directory):
            return True
        elif create is True:
            os.makedirs(directory)
            return True
        else:
            return False
    except Exception:
        return None


def basicressources_gittrees_setup(gittreesdir):
    # FIXMELATER: we should clone these ourselves, but for now leave that task to the user
    for gittreedir in (
        os.path.join(gittreesdir, "mainline"),
        os.path.join(gittreesdir, "next"),
        os.path.join(gittreesdir, "stable"),
    ):
        if not basicressource_checkdir_exists(gittreedir, create=False):
            logger.error(
                "Aborting, as the directory '%s' does not exist yet; please create it and check clone the appropriate Linux tree into it."
                % gittreedir
            )
            sys.exit(1)

        gitdir = os.path.join(gittreedir, ".git")
        if not basicressource_checkdir_exists(gitdir, create=False):
            logger.error(
                "Aborting, as the directory '%s' appears to not contain a git tree." % gittreedir
            )
            sys.exit(1)

    # hardcoded for now, too
    GitTree.add(
        "mainline",
        "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/",
        "cgit",
        "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/",
        "master",
        0,
    )
    GitTree.add(
        "next",
        "https://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git/",
        "cgit",
        "https://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git/commit/",
        "master",
        -1,
    )
    GitTree.add(
        "stable",
        "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
        "cgit",
        "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/",
        r"linux-[0-9][0-9]*.[0-9][0-9]*\.y",
        1,
    )


def basicressources_repsrces_setup():
    # these are required
    ReportSource.add("generic", 99, "", "generic", "")
    ReportSource.add("lore_all", 98, "", "lore", "https://lore.kernel.org/all/")

    ReportSource.add(
        "bugzilla.kernel.org",
        0,
        "https://bugzilla.kernel.org",
        "bugzilla",
        "https://bugzilla.kernel.org/show_bug.cgi?id=",
    )

    # these are optional; maybe they should be in a config file

    # temproraily disabled while working on new bugzilla module
    # ReportSource.add('bugzilla.kernel.org', 0,
    #                 'https://bugzilla.kernel.org',
    #                 'bugzilla', 'https://bugzilla.kernel.org/show_bug.cgi?id=')

    ReportSource.add(
        "lkml",
        1,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-kernel",
        "lore",
        "https://lore.kernel.org/lkml/",
        identifiers="linux-kernel@vger.kernel.org",
    )
    if is_running_citesting():
        ReportSource.add(
            "regressions",
            2,
            "nntp://nntp.lore.kernel.org/dev.linux.lists.regressions",
            "lore",
            "https://lore.kernel.org/regressions/",
            identifiers="regressions@lists.linux.dev",
        )
    else:
        ReportSource.add(
            "regressions",
            2,
            "nntp://nntp.lore.kernel.org/dev.linux.lists.regressions",
            "lore",
            "https://lore.kernel.org/regressions/",
            identifiers="regressions@lists.linux.dev",
            lastchked=190,
        )

    # basics
    ReportSource.add(
        "stable",
        3,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.stable",
        "lore",
        "https://lore.kernel.org/stable/",
        identifiers="stable@vger.kernel.org",
    )
    ReportSource.add(
        "mm",
        6,
        "nntp://nntp.lore.kernel.org/org.kvack.linux-mm",
        "lore",
        "https://lore.kernel.org/linux-mm/",
        identifiers="linux-mm@kvack.org",
    )
    ReportSource.add(
        "arch",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-arch",
        "lore",
        "https://lore.kernel.org/linux-arch/",
        identifiers="linux-arch@vger.kernel.org",
    )

    # arch, mm, and virt
    ReportSource.add(
        "arm",
        3,
        "nntp://nntp.lore.kernel.org/org.infradead.lists.linux-arm-kernel",
        "lore",
        "https://lore.kernel.org/linux-arm-kernel/",
        identifiers="linux-arm-kernel@lists.infradead.org",
    )
    ReportSource.add(
        "kvm",
        4,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.kvm",
        "lore",
        "https://lore.kernel.org/kvm/",
        identifiers="kvm@vger.kernel.org",
    )
    ReportSource.add(
        "mips",
        3,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-mips",
        "lore",
        "https://lore.kernel.org/linux-mips/",
        identifiers="linux-mips@vger.kernel.org",
    )
    ReportSource.add(
        "ppc-dev",
        3,
        "nntp://nntp.lore.kernel.org/org.ozlabs.lists.linuxppc-dev",
        "lore",
        "https://lore.kernel.org/linuxppc-dev/",
        identifiers="linuxppc-dev@lists.ozlabs.org",
    )
    ReportSource.add(
        "virtualization",
        5,
        "nntp://nntp.lore.kernel.org/org.linuxfoundation.lists.virtualization",
        "lore",
        "https://lore.kernel.org/virtualization/",
        identifiers="virtualization@lists.linux-foundation.org",
    )

    # graphics
    ReportSource.add(
        "dri",
        3,
        "nntp://nntp.lore.kernel.org/org.freedesktop.lists.dri-devel",
        "lore",
        "https://lore.kernel.org/dri-devel/",
        identifiers="dri-devel@lists.freedesktop.org",
    )
    ReportSource.add(
        "amd-gfx",
        5,
        "nntp://nntp.lore.kernel.org/org.freedesktop.lists.amd-gfx",
        "lore",
        "https://lore.kernel.org/amd-gfx/",
        identifiers="amd-gfx@lists.freedesktop.org",
    )
    ReportSource.add(
        "fbdev",
        7,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-fbdev",
        "lore",
        "https://lore.kernel.org/linux-fbdev/",
        identifiers="linux-fbdev@vger.kernel.org",
    )
    ReportSource.add(
        "nouveau",
        5,
        "nntp://nntp.lore.kernel.org/org.freedesktop.lists.nouveau",
        "lore",
        "https://lore.kernel.org/nouveau/",
        identifiers="nouveau@lists.freedesktop.org",
    )
    ReportSource.add(
        "intel-gfx",
        5,
        "nntp://nntp.lore.kernel.org/org.freedesktop.lists.intel-gfx",
        "lore",
        "https://lore.kernel.org/intel-gfx/",
        identifiers="intel-gfxlists.freedesktop.org",
    )

    # network
    ReportSource.add(
        "ath10k",
        7,
        "nntp://nntp.lore.kernel.org/org.infradead.lists.ath10k",
        "lore",
        "https://lore.kernel.org/ath10k/",
        identifiers="ath10k@lists.infradead.org",
    )
    ReportSource.add(
        "ath11k",
        7,
        "nntp://nntp.lore.kernel.org/org.infradead.lists.ath11k",
        "lore",
        "https://lore.kernel.org/ath11k/",
        identifiers="ath10k@lists.infradead.org",
    )
    ReportSource.add(
        "netdev",
        3,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.netdev",
        "lore",
        "https://lore.kernel.org/netdev/",
        identifiers="netdev@vger.kernel.org",
    )
    ReportSource.add(
        "rdma",
        4,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-rdma",
        "lore",
        "https://lore.kernel.org/linux-rdma/",
        identifiers="linux-rdma@vger.kernel.org",
    )
    ReportSource.add(
        "wireless",
        4,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-wireless",
        "lore",
        "https://lore.kernel.org/linux-wireless/",
        identifiers="linux-wireless@vger.kernel.org",
    )
    ReportSource.add(
        "intel-wired-lan",
        7,
        "nntp://nntp.lore.kernel.org/org.osuosl.intel-wired-lan",
        "lore",
        "https://lore.kernel.org/intel-wired-lan/",
        identifiers="intel-wired-lan@lists.osuosl.org",
    )

    # storage
    ReportSource.add(
        "block",
        3,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-block",
        "lore",
        "https://lore.kernel.org/linux-block/",
        identifiers="linux-block@vger.kernel.org",
    )
    ReportSource.add(
        "mtd",
        6,
        "nntp://nntp.lore.kernel.org/org.infradead.lists.linux-mtd",
        "lore",
        "https://lore.kernel.org/linux-mtd/",
        identifiers="linux-mtd@lists.infradead.org",
    )
    ReportSource.add(
        "nvme",
        6,
        "nntp://nntp.lore.kernel.org/org.infradead.lists.linux-nvme",
        "lore",
        "https://lore.kernel.org/linux-nvme/",
        identifiers="linux-nvme@lists.infradead.org",
    )
    ReportSource.add(
        "raid",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-raid",
        "lore",
        "https://lore.kernel.org/linux-raid/",
        identifiers="linux-raid@vger.kernel.org",
    )
    ReportSource.add(
        "scsi",
        3,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-scsi",
        "lore",
        "https://lore.kernel.org/linux-scsi/",
        identifiers="linux-scsi@vger.kernel.org",
    )

    # filesystems
    ReportSource.add(
        "cifs",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-cifs",
        "lore",
        "https://lore.kernel.org/linux-cifs/",
        identifiers="linux-cifs@vger.kernel.org",
    )
    ReportSource.add(
        "btrfs",
        4,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-btrfs",
        "lore",
        "https://lore.kernel.org/linux-btrfs/",
        identifiers="linux-btrfs@vger.kernel.org",
    )
    ReportSource.add(
        "ext4",
        4,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-ext4",
        "lore",
        "https://lore.kernel.org/linux-ext4/",
        identifiers="linux-ext4@vger.kernel.org",
    )
    ReportSource.add(
        "fsdevel",
        3,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-fsdevel",
        "lore",
        "https://lore.kernel.org/linux-fsdevel/",
        identifiers="linux-fsdevel@vger.kernel.org",
    )
    ReportSource.add(
        "nfs",
        4,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-nfs",
        "lore",
        "https://lore.kernel.org/linux-nfs/",
        identifiers="linux-nfs@vger.kernel.org",
    )
    ReportSource.add(
        "xfs",
        4,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-xfs",
        "lore",
        "https://lore.kernel.org/linux-xfs/",
        identifiers="linux-xfs@vger.kernel.org",
    )

    # pci, pm, low-level, etc.
    ReportSource.add(
        "crypto",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-crypto",
        "lore",
        "https://lore.kernel.org/linux-crypto/",
        identifiers="linux-crypto@vger.kernel.org",
    )
    ReportSource.add(
        "edac",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-edac",
        "lore",
        "https://lore.kernel.org/linux-edac/",
        identifiers="linux-edac@vger.kernel.org",
    )
    ReportSource.add(
        "i2c",
        5,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-i2c",
        "lore",
        "https://lore.kernel.org/linux-i2c/",
        identifiers="linux-i2c@vger.kernel.org",
    )
    ReportSource.add(
        "iio",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-iio",
        "lore",
        "https://lore.kernel.org/linux-iio/",
        identifiers="linux-iio@vger.kernel.org",
    )
    ReportSource.add(
        "iommu",
        6,
        "nntp://nntp.lore.kernel.org/dev.linux.lists.iommu",
        "lore",
        "https://lore.kernel.org/linux-iommu/",
        identifiers="iommu@lists.linux.dev",
    )
    ReportSource.add(
        "pci",
        5,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-pci",
        "lore",
        "https://lore.kernel.org/linux-pci/",
        identifiers="linux-pci@vger.kernel.org",
    )
    ReportSource.add(
        "pm",
        5,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-pm",
        "lore",
        "https://lore.kernel.org/linux-pm/",
        identifiers="linux-pm@vger.kernel.org",
    )
    ReportSource.add(
        "serial",
        7,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-serial",
        "lore",
        "https://lore.kernel.org/linux-serial/",
        identifiers="linux-serial@vger.kernel.org",
    )

    # other drivers
    ReportSource.add(
        "alsa",
        5,
        "nntp://nntp.lore.kernel.org/org.alsa-project.alsa-devel",
        "lore",
        "https://lore.kernel.org/alsa-devel/",
        identifiers="alsa-devel@alsa-project.org",
    )
    ReportSource.add(
        "bluetooth",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-bluetooth",
        "lore",
        "https://lore.kernel.org/linux-bluetooth/",
        identifiers="linux-bluetooth@vger.kernel.org",
    )
    ReportSource.add(
        "hwmon",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-hwmon",
        "lore",
        "https://lore.kernel.org/linux-hwmon/",
        identifiers="linux-hwmon@vger.kernel.org",
    )
    ReportSource.add(
        "input",
        6,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-input",
        "lore",
        "https://lore.kernel.org/linux-input/",
        identifiers="linux-input@vger.kernel.org",
    )
    ReportSource.add(
        "media",
        5,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-media",
        "lore",
        "https://lore.kernel.org/linux-media/",
        identifiers="linux-media@vger.kernel.org",
    )
    ReportSource.add(
        "platform-driver-x86",
        5,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.platform-driver-x86",
        "lore",
        "https://lore.kernel.org/platform-driver-x86/",
        identifiers="platform-driver-x86@vger.kernel.org",
    )
    ReportSource.add(
        "staging",
        6,
        "nntp://nntp.lore.kernel.org/dev.linux.lists.linux-staging",
        "lore",
        "https://lore.kernel.org/linux-staging/",
        identifiers="linux-staging@lists.linux.dev",
    )
    ReportSource.add(
        "usb",
        5,
        "nntp://nntp.lore.kernel.org/org.kernel.vger.linux-usb",
        "lore",
        "https://lore.kernel.org/linux-usb/",
        identifiers="linux-usb@vger.kernel.org",
    )


def basicressources_repsrces_add_or_modify():
    ReportSource.add_or_modify(
        "drm/amd",
        16,
        "https://gitlab.freedesktop.org/drm/amd",
        "gitlab",
        "<unused>",
        mininterval=3600,
    )

    ReportSource.add_or_modify(
        "drm/i915/kernel",
        16,
        "https://gitlab.freedesktop.org/drm/i915/kernel",
        "gitlab",
        "<unused>",
        mininterval=3600,
    )

    ReportSource.add_or_modify(
        "drm/misc",
        16,
        "https://gitlab.freedesktop.org/drm/misc/kernel",
        "gitlab",
        "<unused>",
        mininterval=3600,
    )

    ReportSource.add_or_modify(
        "drm/msm",
        16,
        "https://gitlab.freedesktop.org/drm/msm",
        "gitlab",
        "<unused>",
        mininterval=43200,
    )

    ReportSource.add_or_modify(
        "drm/nouveau",
        16,
        "https://gitlab.freedesktop.org/drm/nouveau",
        "gitlab",
        "<unused>",
        mininterval=3600,
    )

    ReportSource.add_or_modify(
        "drm/tegra",
        16,
        "https://gitlab.freedesktop.org/drm/tegra",
        "gitlab",
        "<unused>",
        mininterval=43200,
    )

    ReportSource.add_or_modify(
        "drm/xe",
        16,
        "https://gitlab.freedesktop.org/drm/xe/kernel",
        "gitlab",
        "<unused>",
        mininterval=14400,
    )

    ReportSource.add_or_modify(
        "v9fs", 16, "https://github.com/v9fs/linux", "github", "<unused>", mininterval=43200
    )

    ReportSource.add_or_modify(
        "AsahiLinux",
        16,
        "https://github.com/AsahiLinux/linux",
        "github",
        "<unused>",
        mininterval=43200,
    )

    ReportSource.add_or_modify(
        "ClangBuiltLinux",
        16,
        "https://github.com/ClangBuiltLinux/linux",
        "github",
        "<unused>",
        mininterval=14400,
    )

    ReportSource.add_or_modify(
        "thesofproject",
        16,
        "https://github.com/thesofproject/linux",
        "github",
        "<unused>",
        mininterval=43200,
    )


def basicressources_get_dirs(databasedir=None, gittreesdir=None, websitesdir=None, tmpdir=None):
    # constructs the directory paths
    # use default path, unless tmpdir if given; but even then use the default, if the variable is set to 'True'

    homedir = pathlib.Path.home()
    cachedir = os.path.join(homedir, ".cache/regzbot/")
    configfile = os.path.join(homedir, ".config/regzbot/regzbot.cfg")

    if not databasedir and tmpdir:
        databasedir = os.path.join(tmpdir, "database")
    elif not databasedir or databasedir is True:
        databasedir = os.path.join(homedir, ".local/share/regzbot/")

    if not gittreesdir and tmpdir:
        gittreesdir = os.path.join(tmpdir, "gittrees")
    elif not gittreesdir or gittreesdir is True:
        gittreesdir = os.path.join(cachedir, "gittrees")

    if not websitesdir and tmpdir:
        websitesdir = os.path.join(tmpdir, "websites")
    elif not websitesdir or websitesdir is True:
        websitesdir = os.path.join(cachedir, "websites")

    return configfile, databasedir, gittreesdir, websitesdir


def basicressources_setup(databasedir=None, gittreesdir=None, websitesdir=None, tmpdir=None):
    _, databasedir, gittreesdir, websitesdir = basicressources_get_dirs(
        databasedir, gittreesdir, websitesdir, tmpdir
    )

    db_create(databasedir)

    basicressources_repsrces_setup()
    basicressources_gittrees_setup(gittreesdir)

    # run this once, to make sure all gitbraches db entries get created
    basicressources_init()
    GitTree.updateall()

    db_commit()


def basicressources_init(databasedir=None, gittreesdir=None, websitesdir=None, tmpdir=None):
    from random import randrange

    configfile, databasedir, gittreesdir, websitesdir = basicressources_get_dirs(
        databasedir, gittreesdir, websitesdir, tmpdir
    )

    global CONFIGURATION
    CONFIGURATION = configparser.ConfigParser()
    if os.path.exists(configfile):
        CONFIGURATION.read(configfile)

    RegzbotDbMeta.init(databasedir)

    # occational cleanup
    if randrange(500) == 250:
        DBCON.execute("VACUUM")

    RegzbotDbMeta.update()
    RecordProcessedMsgids.cleanup(30)

    reposdir = init_reposdir(gittreesdir)
    if not reposdir:
        logger.debug("aborting: reposdir could not be initialized")
        sys.exit(1)

    basicressource_checkdir_exists(websitesdir, create=True)
    basicressource_checkdir_exists(os.path.join(websitesdir, "regression"), create=True)

    basicressources_repsrces_add_or_modify()

    global WEBPAGEDIR
    WEBPAGEDIR = websitesdir


def set_citesting(kind):
    # needed for:
    # * webui testing, otherwise everything lands on the dormant page...
    # * monitor commands, as they otherwise try to download things from the web

    global __CITESTING__
    __CITESTING__ = kind


def is_running_citesting(kind=None):
    if not kind and __CITESTING__:
        return True
    elif __CITESTING__ == kind:
        return True
    return False


def redo_regressions(msgids):
    with tempfile.TemporaryFile(mode="w+t") as tmpfile_before:
        with tempfile.TemporaryFile(mode="w+t") as tmpfile_after:
            for msgid in msgids:
                regression = RegressionBasic.get_by_entry(urldecode(msgid))
                if not regression:
                    logger.critical("Aborting, could not find any regression with msgid %s", msgid)
                    sys.exit(1)

            # store everything we need later
            db_dump(tmpfile_before, order="subject")
            msgids_to_recheck = list()

            for msgid in msgids:
                # we for now only get one
                for regression in RegressionBasic.get_by_entry(urldecode(msgid)):
                    break

                # we need to store what we need to recheck
                for histevent in RegHistory.get_all(regression.regid):
                    # we don't need these:
                    if histevent.gitbranchid:
                        continue
                    if histevent.entry not in msgids_to_recheck:
                        msgids_to_recheck.append(histevent.entry)

                # remove the old regression
                regression.delete()

            # recheck all msg found that had a entry in the history
            # to recreate the regression
            for msgid_to_check in msgids_to_recheck:
                raise NotImplementedError

            db_dump(tmpfile_after, order="subject")

            # look out for differences, unless testing code is doing it for us
            if not __CITESTING__:
                tmpfile_before.seek(0)
                tmpfile_after.seek(0)
                if db_diff(tmpfile_before, tmpfile_after):
                    answer = input("Enter 'a' to abort, anything else to move on")
                    if answer.lower() == "a":
                        sys.exit(1)

    return regression


def recheck(msgids):
    basicressources_init()
    redo_regressions(msgids)
    db_commit()

    from export_web import RegExportWeb

    RegExportWeb.compile()

    db_close()


def run():
    basicressources_init()

    # check issue trackers
    ReportSource.update_all()

    # check for new commits
    GitTree.updateall()
    db_commit()

    # update webpages
    from export_web import RegExportWeb

    RegExportWeb.compile()

    # we are done
    db_close()


def generate_web():
    basicressources_init()

    # update webpages
    from export_web import RegExportWeb

    RegExportWeb.compile()

    # we are done
    db_close()


def report():
    from export_mail import RegExportMailReport

    basicressources_init()
    RegExportMailReport.compile()

    # we are done
    db_commit()
    db_close()

    return


def checkout_msgid(msgid):
    reptrd = ReportThread.from_url("https://lore.kernel.org/all/%s/" % msgid)
    reptrd.process_single()


def checkout_url(url):
    reptrd = ReportThread.from_url(url)
    reptrd.update(None, None)


def urldecode(url):
    return urllib.parse.unquote(url)


def urlencode(url):
    return urllib.parse.quote(url, safe="@=")


def inspectobj(obj):
    for att in dir(obj):
        try:
            ref = getattr(obj, att)
            print("%s: %s  (%s)" % (att, getattr(obj, att), type(ref)))
        except Exception:
            print("ERROR: inspection of %s.%s failed" % (type(obj), att))
