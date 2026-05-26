#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"
#
# FIXMELATER:
# * import commits and emails from files
# * maybe: use more of pathlib and less of os and glob (or nothing at all)
# * directly retrieve some mails from lore to see if everything works, once there are some on the list

import datetime
import email.message
import email.generator
import glob
import mailbox
import os
import pathlib
import string
import sys

import git
import regzbot
import regzbot.export_csv
import regzbot.export_web
import regzbot._repsources._lore

logger = regzbot.logger

gittrees_testing = dict()
emaildirs = dict()

MAIL_TEMPLATE = string.Template(
    """Lorem ipsum dolor sit amet, consectetur adipiscing elit. Fusce commodo
justo ac mi ornare mollis id rutrum felis.

${tag}

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Fusce commodo
justo ac mi ornare mollis id rutrum felis."""
)


class Emaildir:
    _count = 0
    _startdate = 1546304400

    def __init__(self, recipient, path_tmpdirectory, name):
        self.recipient = recipient

        self.directory = os.path.join(path_tmpdirectory, name)
        os.mkdir(self.directory)

    def create_email(
        self, funcname, tag, *, cc=None, subject=None, messageid=None, replyto=None, references=None
    ):
        if messageid is None:
            messageid = "<regzbot-testing-%s@example.com>" % funcname
        if replyto:
            replyto = "<regzbot-testing-%s@example.com>" % replyto

        new_references = []
        if references:
            for reference in references:
                new_references.append("<regzbot-testing-%s@example.com>" % reference)
        new_references.append(replyto)
        references = new_references

        msg = email.message.EmailMessage()
        if subject:
            msg["Subject"] = subject
        else:
            msg["Subject"] = "%s: Lorem ipsum dolor sit amet" % funcname
        msg.set_content(MAIL_TEMPLATE.substitute(tag=tag))
        msg["From"] = "Regzbot testingmail <nobody@example.com>"
        msg["To"] = self.recipient
        if cc:
            msg["Cc"] = cc
        msg["Date"] = email.utils.formatdate(timeval=(self._startdate + (Emaildir._count * 86400)))
        msg["Message-Id"] = messageid

        if replyto:
            msg["In-Reply-To"] = replyto
            msg["References"] = " ".join(references)

        # filename = os.path.join(self.directory, "%s.regzbot" % messageid.strip('<>'))
        # with open(filename, 'w') as out:
        #    gen = email.generator.Generator(out)
        #    gen.flatten(msg)

        # if replyto:
        #    os.symlink(os.path.join(self.directory, "%s.regzbot" % messageid.strip('<>')), filename)

        filename = os.path.join(self.directory, "%s.regzbot" % messageid.strip("<>"))
        if replyto:
            filename_replyto = os.path.join(self.directory, "%s.regzbot" % replyto.strip("<>"))
            os.symlink(filename_replyto, filename)
        mbox = mailbox.mbox(filename)
        mbox.add(mailbox.mboxMessage(msg))
        mbox.flush()

        Emaildir._count += 1

    def clear(self):
        for emailtestingfile in pathlib.Path(self.directory).glob("*.regzbot"):
            emailtestingfile.unlink()

    def process(self):
        filenames = sorted(pathlib.Path(self.directory).iterdir(), key=os.path.getmtime)
        for file in filenames:
            regzbot.mailin.processmsg_file(self.repsrc, os.path.join(self.directory, file))

    def reset(self):
        Emaildir._count = 0


class TestingGitTree:
    def __init__(self, path_testdata, path_tmprepos, reponame, startdate, branchname="master"):
        self._count = 0
        self._branchname = branchname
        self._description = reponame + "_" + branchname
        self._startdate = startdate

        # disabled: this is for a infra that can check if the commits have the expected
        # sha1sum; turned out that's unneeded, at least for now; leave the code around in
        # case things change again.
        #
        # self._filename_hashes_known = os.path.join(
        #    path_testdata, 'expected/commitids-' + self._description)
        self.hashes_known = self.__init_repo_hashes()

        self.repo = self.__init_repo(path_tmprepos, reponame, self._startdate)
        self._count_afterinit = self._hashes_afterinit = None

    def __init_repo(self, path_tmprepos, reponame, startdate):
        repodir = os.path.join(path_tmprepos, reponame)

        if os.path.isdir(repodir):
            if not os.path.isdir(os.path.join(repodir, ".git")):
                logger.critical(
                    "Directory %s exist, but does not contain .git/. Aborting." % repodir
                )
                sys.exit(1)
        else:
            os.mkdir(repodir)

        self.repo = git.Repo.init(repodir)

        # make sure the global git config doesn't interfer
        with self.repo.config_writer() as gitcw:
            gitcw.set_value("user", "name", "Regzbot Testing")
            gitcw.set_value("user", "email", "nobody@example.com")

        # is this a brand new repo?
        try:
            _ = self.repo.head.commit
        except ValueError:
            self.__init_branch()
            return self.repo

        # make sure our branch is checked out
        self.__checkout_branch()

        # is this a brand new branch?
        if len(glob.glob(os.path.join(repodir, self._description + "-*"))) == 0:
            self.__init_branch()

        return self.repo

    def __init_repo_hashes(self):
        # see comment in __init() for why this is disabled
        #
        # if os.path.isfile(self._filename_hashes_known):
        #    with open(self._filename_hashes_known, 'r') as input_file:
        #        return input_file.read().splitlines()
        # else:
        return list()

    def __check_sha1sum(self, commit, commitnr):
        if commitnr >= len(self.hashes_known):
            self.__add_unknown_hash(str(self.repo.head.commit))
        elif str(commit) != self.hashes_known[commitnr]:
            logger.critical(
                "Sha1 for the latest commit (%s) to %s doesn't match expected sha1 (%s)."
                % (commit, self._branchname, self.hashes_known[commitnr])
            )
            logger.critical("Aborting")
            sys.exit(1)

    def __commit(self, commitmsg):
        if commitmsg is None:
            commitmsg = (
                "This is a %s commit for testing regzbot, the content doesn't matter."
                % self._description
            )

        commitdate = datetime.datetime.fromtimestamp(
            self._startdate + self._count, tz=datetime.timezone.utc
        )
        self.repo.index.commit(commitmsg, author_date=commitdate, commit_date=commitdate)
        self.__check_sha1sum(self.repo.head.commit, self._count)
        self._count += 1

    def __add_unknown_hash(self, sha1sum):
        # see comment in __init() for why this is disabled
        #
        # with open(self._filename_hashes_known, 'a') as file:
        #    file.write(sha1sum + '\n')
        #
        self.hashes_known.append(sha1sum)

    def __init_branch(self):
        filename = os.path.join(self.repo.working_dir, self._description + "-" + str(self._count))
        file = open(filename, "x")
        file.write("This is a file for testing regzbot, the content doesn't matter.\n")
        file.close()
        self.repo.index.add([filename])
        self.__commit(None)

    def create_remote(self, name, url):
        return self.repo.create_remote(name, url)

    def __checkout_branch(self):
        if not self.repo.active_branch == self._branchname:
            self.repo.branches[self._branchname].checkout()

    def clone(self, path_tmprepos, name):
        return self.repo.clone(os.path.join(path_tmprepos, name))

    def mv(self, commitmsg=None):
        # make sure our branch is checked out
        self.__checkout_branch()

        fileold = os.path.join(
            self.repo.working_dir, self._description + "-" + str(self._count - 1)
        )
        filenew = os.path.join(self.repo.working_dir, self._description + "-" + str(self._count))
        self.repo.git.mv(fileold, filenew)
        self.__commit(commitmsg)

    def process(self):
        self.__checkout_branch()

    def tag(self, tag, message="This is a tag for testing regzbot, the content doesn't matter."):
        # make sure our branch is checked out
        self.__checkout_branch()

        # self.repo.create_tag(tag, message=message)
        commitdate = "%s" % datetime.datetime.fromtimestamp(
            self._startdate + self._count, tz=datetime.timezone.utc
        )
        with self.repo.git.custom_environment(GIT_COMMITTER_DATE=commitdate):
            self.repo.create_tag(tag, message=message)

    def init_done(self):
        self._count_afterinit = self._count
        self._hashes_afterinit = self.hashes_known.copy()

    def reset(self):
        self._count = self._count_afterinit
        self.hashes_known = self._hashes_afterinit.copy()

        self.__checkout_branch()
        self.repo.git.reset("--hard", self._hashes_afterinit[-1])


def update_gittrees():
    for gittree in regzbot.GitTree.getall():
        gittree.update()


def emaildirs_clear():
    for emaildir in emaildirs.keys():
        emaildirs[emaildir].clear()


def populatetree_linux(gittree_testing):
    gittree_testing.mv()
    gittree_testing.tag("v1.8")
    gittree_testing.mv()
    gittree_testing.tag("v1.9-rc1")
    gittree_testing.mv()
    gittree_testing.tag("v1.9-rc2")
    gittree_testing.mv()
    gittree_testing.tag("v1.9")
    gittree_testing.mv()
    gittree_testing.tag("v1.10-rc1")
    gittree_testing.mv()
    gittree_testing.tag("v1.10-rc2")
    gittree_testing.mv()
    gittree_testing.tag("v1.10")
    gittree_testing.mv()
    gittree_testing.tag("v1.11-rc1")
    gittree_testing.mv()
    gittree_testing.tag("v1.11-rc2")
    gittree_testing.mv()


def gittree_testing_prep_linux_next(repo):
    # yes, this is how mainline repo is called in linux-next:
    repo.heads["master"].rename("stable")

    # these branches might interfer in parsing, so create them, even if we don't use them
    repo.create_head("akpm")
    repo.create_head("akpm-base")
    repo.create_head("pending-fixes")

    # this is to one we care about
    masterref = repo.create_head("master")
    masterref.checkout()


def populatetree_linux_next(gittree_testing):
    gittree_testing.mv()
    gittree_testing.tag("next-20190101")
    gittree_testing.mv()
    gittree_testing.tag("next-20190102")


def gittree_testing_prep_linux_stable(repo):
    # these branches might interfer in parsing, so create them, even if we don't use them
    repo.create_head("linux-rolling-lts")
    repo.create_head("linux-rolling-stable")

    # these are the one we care about
    repo.create_head("linux-1.8.y", commit="v1.8")
    repo.create_head("linux-1.10.y", commit="v1.10")


def populatetree_linux_stable18(gittree_testing):
    gittree_testing.mv()
    gittree_testing.tag("v1.8.1")
    gittree_testing.mv()
    gittree_testing.tag("v1.8.2")


def populatetree_linux_stable110(gittree_testing):
    gittree_testing.mv()
    gittree_testing.tag("v1.10.1")
    gittree_testing.mv()
    gittree_testing.tag("v1.10.2")


def init_repodirs(path_tmprepos, path_testdata):
    # prep
    path_upstream_tmprepos = os.path.join(path_tmprepos, "upstream")
    path_downstream_tmprepos = os.path.join(path_tmprepos, "downstream")
    logger.debug(
        "Creating git repos in %s and pulling them to %s",
        path_upstream_tmprepos,
        path_downstream_tmprepos,
    )
    os.mkdir(path_tmprepos)
    os.mkdir(path_downstream_tmprepos)
    os.mkdir(path_upstream_tmprepos)

    # create linux-mainline repo
    regzbot.GitTree.add(
        "mainline",
        "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/",
        "cgit",
        "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/",
        "master",
        0,
    )
    gittrees_testing["mainline"] = TestingGitTree(
        path_testdata, path_upstream_tmprepos, "mainline", 1546300800
    )
    gittrees_testing["mainline"].repo.clone(os.path.join(path_downstream_tmprepos, "mainline"))
    update_gittrees()
    populatetree_linux(gittrees_testing["mainline"])

    # create linux-next repo
    regzbot.GitTree.add(
        "next",
        "https://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git/",
        "cgit",
        "https://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git/commit/",
        "master",
        -1,
    )
    gittree_testing_prep_linux_next(
        gittrees_testing["mainline"].clone(path_upstream_tmprepos, "next")
    )
    gittrees_testing["next"] = TestingGitTree(
        path_testdata, path_upstream_tmprepos, "next", 1577836800
    )
    gittrees_testing["next"].repo.clone(os.path.join(path_downstream_tmprepos, "next"))
    update_gittrees()
    populatetree_linux_next(gittrees_testing["next"])

    # create linux-stable repo with two branches
    gittree_testing_prep_linux_stable(
        gittrees_testing["mainline"].clone(path_upstream_tmprepos, "stable")
    )
    regzbot.GitTree.add(
        "stable",
        "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
        "cgit",
        "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/",
        r"linux-[0-9][0-9]*.[0-9][0-9]*\.y",
        1,
    )
    gittrees_testing["linux-1.8.y"] = TestingGitTree(
        path_testdata, path_upstream_tmprepos, "stable", 1609459200, "linux-1.8.y"
    )
    gittrees_testing["linux-1.8.y"].repo.clone(os.path.join(path_downstream_tmprepos, "stable"))
    gittrees_testing["linux-1.10.y"] = TestingGitTree(
        path_testdata, path_upstream_tmprepos, "stable", 1609459200, "linux-1.10.y"
    )
    update_gittrees()
    populatetree_linux_stable110(gittrees_testing["linux-1.10.y"])
    populatetree_linux_stable18(gittrees_testing["linux-1.8.y"])
    update_gittrees()


def init_mailsdir(path_tmpmail):
    logger.debug("Creating directory %s for holding emails files", path_tmpmail)

    regzbot._TESTING["emaildirs"] = []

    os.mkdir(path_tmpmail)
    emaildirs["primary"] = Emaildir("regressions@example.com", path_tmpmail, "primary")
    regzbot._TESTING["emaildirs"].append(emaildirs["primary"].directory)
    repsrcid = regzbot.ReportSource.add(
        "Nonexistand primary mailinglist for regzbot testing",
        2,
        emaildirs["primary"].directory,
        "lore",
        "https://lore.kernel.org/regressions/",
        identifiers="regressions@example.com",
    )

    emaildirs["secondary"] = Emaildir("linux-kernel@example.com", path_tmpmail, "secondary")
    regzbot._TESTING["emaildirs"].append(emaildirs["secondary"].directory)
    repsrcid = regzbot.ReportSource.add(
        "Nonexistand secondary mailinglist for regzbot testing",
        1,
        emaildirs["secondary"].directory,
        "lore",
        "https://lore.kernel.org/lkml/",
        identifiers="linux-kernel@example.com",
    )

    regzbot.ReportSource.add("generic", 99, "", "generic", "")


def init(tmpdir, testdatadir):
    regzbot.set_citesting("offline")

    _, databasedir, gittreesdir, _ = regzbot.basicressources_get_dirs(
        tmpdir=tmpdir, databasedir=os.path.join(tmpdir, "db-offlinetsts")
    )
    mailsdir = os.path.join(tmpdir, "mails")

    regzbot.db_create(databasedir)
    regzbot.basicressources_init(
        tmpdir=tmpdir,
        gittreesdir=os.path.join(gittreesdir, "downstream"),
        databasedir=os.path.join(tmpdir, "db-offlinetsts"),
    )

    init_repodirs(gittreesdir, testdatadir)
    init_mailsdir(mailsdir)
    regzbot.db_commit()

    for gittree_testing in gittrees_testing:
        gittrees_testing[gittree_testing].init_done()


def run(resultfilename, tmpdir, testdatadir):
    init(tmpdir, testdatadir)

    resultfile = open(resultfilename, "a")
    testfuncprefix = "offltest"
    this = sys.modules[__name__]

    outercount = 0
    while "%s_%s_0" % (testfuncprefix, outercount) in dir(this):
        # reset git
        for gittree_testing in gittrees_testing:
            gittrees_testing[gittree_testing].reset()
        update_gittrees()

        # reset email
        for emaildir in emaildirs:
            emaildirs[emaildir].reset()
        regzbot.db_rollback()

        # go
        innercount = 0
        while "%s_%s_%s" % (testfuncprefix, outercount, innercount) in dir(this):
            # run test
            callfunction = getattr(this, "%s_%s_%s" % (testfuncprefix, outercount, innercount))
            instructions = callfunction("test_%s_%s" % (outercount, innercount))

            # process created testdata
            if instructions:
                if "mailchk" in instructions:
                    for repsrc in regzbot.ReportSource.getall():
                        if repsrc.kind != "lore":
                            continue
                        repsrc.update()
                if "gitchk" in instructions:
                    update_gittrees()

            # write results
            resultfile.write("[%s_%s_%s]\n" % (testfuncprefix, outercount, innercount))
            for data in regzbot.export_csv.dumpall_csv():
                resultfile.write(data)
            resultfile.write("\n")

            regzbot.export_web.RegExportWeb.compile()

            if instructions and "wait" in instructions:
                # regzbot.db_commit()
                os.system('read -p "Press any key to continue"')

            # finish this up
            innercount += 1
        # remove generated mails
        emaildirs_clear()
        outercount += 1
    resultfile.close()
    regzbot.db_commit()
    regzbot.db_close()


def offltest_0_0(funcname):
    logger.info("%s: create a mainline regression" % funcname)
    emaildirs["primary"].create_email(funcname, "#regzbot introduced: v1.8..v1.9-rc1")
    return ["mailchk"]


def offltest_0_1(funcname):
    replyto = "test_0_0"
    logger.info("%s: specifying the culprit for the regression created in %s" % (funcname, replyto))
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot introduced: %s" % gittrees_testing["mainline"].hashes_known[5],
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_2(funcname):
    replyto = "test_0_0"
    logger.info("%s: update title for the regression created in %s" % (funcname, replyto))
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot summary: test_0_0: updated title (set by %s)" % funcname,
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_3(funcname):
    logger.info(
        "%s: create a second mainline regression and mark it immediately as duplicate" % funcname
    )
    emaildirs["primary"].create_email(funcname, "#regzbot introduced: v1.8..v1.9-rc1")
    replyto = funcname
    emaildirs["primary"].create_email(
        "%s_1" % funcname,
        "#regzbot duplicate: https://lore.kernel.org/regressions/regzbot-testing-test_0_0@example.com",
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_4(funcname):
    replyto = "test_0_0"
    logger.info(
        "%s: mark regression created in %s as fixed with a non-existing commit which has a comment"
        % (funcname, replyto)
    )
    #
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot fixed-by: 4169881b9e0781b2286dc94e4cb731982c5371aa Testcomment to fixed-by",
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_5(funcname):
    replyto = "test_0_0"
    logger.info(
        "%s: mark regression created in %s as fixed with with a commit that is actually existing"
        % (funcname, replyto)
    )
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot fixed-by: %s" % gittrees_testing["mainline"].hashes_known[6],
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_6(funcname):
    replyto = funcname
    logger.info(
        "%s: send a mail which serves as report for a regression created by a reply later using ^introduced"
        % funcname
    )
    emaildirs["primary"].create_email(funcname, "Nothing to see here, move along")
    emaildirs["primary"].create_email(
        "%s_1" % funcname, "#regzbot ^introduced: v1.8..v1.9-rc1", replyto=replyto
    )
    return ["mailchk"]


def offltest_0_7(funcname):
    replyto = "test_0_6"
    logger.info("%s: mark the regression created in %s as resolved" % (funcname, replyto))
    emaildirs["primary"].create_email(funcname, "#regzbot resolve: some reason", replyto=replyto)
    return ["mailchk"]


def offltest_0_8(funcname):
    logger.info("%s: create a fourth mainline regression CCed to the secondary list" % funcname)
    emaildirs["primary"].create_email(
        funcname, "#regzbot introduced: v1.8..v1.9-rc1", cc=emaildirs["secondary"].recipient
    )
    return ["mailchk"]


def offltest_0_9(funcname):
    logger.info(
        "%s: send a mail which serves as report for a regression created by a reply later using 'introduced ^'"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "Nothing to see here, move along"
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "Nothing to see here either, move along",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: v1.8..v1.9-rc1 /",
        replyto="%s_%s" % (funcname, subcounter - 1),
        references=("%s_0" % funcname,),
    )
    return ["mailchk"]


def offltest_0_10(funcname):
    replyto = "test_0_9_0"
    logger.info(
        "%s: send a mail with a regzbot command, but is not added as an activity due to #regzbot ignore-activity"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot title: updated title, set by %s_%s\n\n#regzbot ignore-activity"
        % (funcname, subcounter),
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_11(funcname):
    replyto = "test_0_9_0"
    logger.info("%s: try 'regzbot poke'" % funcname)

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot poke", replyto=replyto
    )
    return ["mailchk"]


def offltest_0_12(funcname):
    replyto = "test_0_0"
    logger.info("%s: try 'regzbot from'" % funcname)

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot from Some N. Ice Person <someone@example.com>",
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_13(funcname):
    replyto = "test_0_9_0"
    logger.info("%s: try 'regzbot backburner'" % funcname)

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot backburner Some reason", replyto=replyto
    )
    return ["mailchk"]


def offltest_0_14(funcname):
    replyto = "test_0_9_0"
    logger.info("%s: try 'regzbot unbackburn'" % funcname)

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot unbackburn", replyto=replyto
    )
    return ["mailchk"]


def offltest_0_15(funcname):
    logger.info(
        "%s: create four additional regressions and mark them as duplicate in various way and then fix one marked that is marked as duplicate and has a duplicate"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )

    subcounter = 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )

    subcounter = 2
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )

    subcounter = 3
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )

    replyto = "%s_%s" % (funcname, 1)
    dupof = "%s_%s" % (funcname, 0)
    subcounter = 4
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot dup-of: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com\n"
        % dupof,
        replyto=replyto,
    )

    replyto = "%s_%s" % (funcname, 3)
    dupof = "%s_%s" % (funcname, 2)
    subcounter = 5
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot dup-of: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com\n"
        % dupof,
        replyto=replyto,
    )

    replyto = "%s_%s" % (funcname, 2)
    dupof = "%s_%s" % (funcname, 0)
    subcounter = 6
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot dup-of: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com\n"
        % dupof,
        replyto=replyto,
    )

    replyto = "%s_%s" % (funcname, 2)
    subcounter = 7
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot fixed-by: %s" % gittrees_testing["mainline"].hashes_known[6],
        replyto=replyto,
    )

    return ["mailchk"]


def offltest_0_16(funcname):
    logger.info(
        "%s: check if some attribut changes from an open regression progress downwards to duplicates"
        % funcname
    )

    subcounter = 0
    replyto = "test_0_15_3"
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot title: new title, set via a duplicate\n",
        replyto="%s" % replyto,
    )

    return ["mailchk"]


def offltest_0_17(funcname):
    logger.info("%s: create a regression and a duplicate from it" % funcname)

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )
    replyto = "%s_%s" % (funcname, subcounter)

    subcounter += 1
    emaildirs["secondary"].create_email("%s_%s" % (funcname, subcounter), "Hello hello")
    second_replyto = "%s_%s" % (funcname, subcounter)

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot dup-of: https://lore.kernel.org/lkml/regzbot-testing-%s@example.com\n"
        % second_replyto,
        replyto=replyto,
    )

    subcounter += 1
    emaildirs["secondary"].create_email(
        "%s_%s" % (funcname, subcounter), "Hello again", replyto=second_replyto
    )

    return ["mailchk"]


def offltest_0_18(funcname):
    logger.info("%s: creating a mainline regression for an arbitarily url" % funcname)
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot use https://bugzilla.example.com/show_bug.cgi?id=215744\n#regzbot introduced: v1.8..v1.9-rc1",
    )
    return ["mailchk"]


def offltest_0_19(funcname):
    logger.info(
        "%s: send a mail which serves as report for a regression created by a reply later using ^introduced"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "Nothing to see here, move along"
    )
    replyto = "%s_%s" % (funcname, subcounter)

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1 ^", replyto=replyto
    )
    return ["mailchk"]


def offltest_0_20(funcname):
    logger.info(
        "%s: add another report to an existing regression (which creates a new regression entry for the other report and marks it as a duplicate)"
        % funcname
    )

    replyto = "test_0_19"
    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot duplicate: https://bugzilla.example.com/show_bug.cgi?id=215744",
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_0_21(funcname):
    logger.info("%s: create a regression and mark it as inconclusive", funcname)
    emaildirs["primary"].create_email(
        "%s" % funcname, "#regzbot introduced: v1.8..v1.9-rc1\n#regzbot inconclusive: some reason"
    )
    return ["mailchk"]


def offltest_0_22(funcname):
    logger.info(
        "%s: send a mail which serves as report for a regression created by a reply later using 'regzbot use'"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "Nothing to see here, move along"
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "Nothing to see here either, move along",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["secondary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot use https://lore.kernel.org/lkml/regzbot-testing-%s@example.com\n#regzbot introduced: v1.8..v1.9-rc1"
        % "test_0_22_0",
    )
    return ["mailchk"]


def offltest_0_23(funcname):
    logger.info(
        "%s: send a mail which serves as report for a regression created by a reply later using 'regzbot use'"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "Nothing to see here, move along"
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "Nothing to see here either, move along",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot use https://lore.kernel.org/lkml/regzbot-testing-%s@example.com\n#regzbot introduced: v1.8..v1.9-rc1"
        % "test_0_23_0",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "Nothing to see here either, move along",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )
    return ["mailchk"]


# create a mainline regression


def offltest_1_0(funcname):
    logger.info("%s: creating a mainline regression" % funcname)
    emaildirs["primary"].create_email(
        funcname, '#regzbot introduced: v1.8..v1.9-rc1 ("foo: bar baz")'
    )
    return ["mailchk"]


def offltest_1_1(funcname):
    replyto = "test_1_0"
    logger.info(
        "%s: creating a git commit that links to the regression created in %s, which should mark is as fixed"
        % (funcname, replyto)
    )
    gittrees_testing["mainline"].mv(
        "Testcommit %s\n\nLink: https://lore.kernel.org/lkml/regzbot-testing-%s@example.com\n"
        % (funcname, replyto)
    )
    return ["gitchk"]


def offltest_1_2(funcname):
    logger.info(
        '%s: create a mainline regression and mark it as "fixed-by" by a commit that has not reached the repos yet'
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )

    # create the commit here, but don't check the repo yet (see below) as we have the commitid at hand here
    gittrees_testing["mainline"].mv()

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot fix: %s" % gittrees_testing["mainline"].hashes_known[-1],
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    # the second False ensure that the tree is not check yet:
    return ["mailchk"]


def offltest_1_3(funcname):
    logger.info("%s: land the commit to fix the regression created in " % funcname)
    # in truth: now check the commit created in the last function
    return ["gitchk"]


def offltest_1_4(funcname):
    logger.info(
        "%s: create a mainline regression that will be fixed by a commit that shows up in next"
        % funcname
    )
    emaildirs["primary"].create_email(funcname, "#regzbot introduced: v1.8..v1.9-rc1")
    gittrees_testing["next"].mv(
        "Testcommit %s\n\nLink: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com\n"
        % (funcname, funcname)
    )
    return ["mailchk", "gitchk"]


def offltest_1_5(funcname):
    logger.info(
        "%s: create a mainline regression and have a commit refer to in in stable" % funcname
    )
    emaildirs["primary"].create_email(funcname, "#regzbot introduced: v1.8..v1.9-rc1")
    gittrees_testing["linux-1.8.y"].mv(
        "Testcommit %s\n\nLink: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com\n"
        % (funcname, funcname)
    )
    return ["mailchk", "gitchk"]


def offltest_1_6(funcname):
    logger.info(
        '%s: create a mainline commit which a "Fixes: %s" for a culprit of a regression introduced later'
        % (funcname, gittrees_testing["mainline"].hashes_known[-1][0:12])
    )
    gittrees_testing["mainline"].mv(
        'Testcommit %s\n\nFixes: %s ("Foo bar")\n'
        % (funcname, gittrees_testing["mainline"].hashes_known[-1][0:12])
    )
    return ["gitchk"]


def offltest_1_7(funcname):
    logger.info(
        "%s: create a mainline regression with a culprit that a commit mentions in a Fixed: tag"
        % funcname
    )
    emaildirs["primary"].create_email(
        funcname, "#regzbot introduced: %s" % gittrees_testing["mainline"].hashes_known[-2]
    )
    return ["mailchk"]


def offltest_1_8(funcname):
    logger.info(
        "%s: create a mainline commit which a Fixed: for a culprit of a present regression"
        % funcname
    )
    gittrees_testing["mainline"].mv(
        'Testcommit %s\n\nFixes: %s ("Foo bar")\n'
        % (funcname, gittrees_testing["mainline"].hashes_known[-2][0:12])
    )
    return ["gitchk"]


def offltest_1_9(funcname):
    logger.info(
        "%s: create a regression and a duplicate from it with a unsupported url, then fix with a commit specifying the latter"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )
    replyto = "%s_%s" % (funcname, subcounter)

    subcounter += 1
    link = "https://somewhere.over.the.rainbow.example.org/regzbot-testing@example.com"
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot dup-of: %s\n" % link, replyto=replyto
    )
    gittrees_testing["mainline"].mv("Testcommit %s\n\nLink: %s\n" % (funcname, link))

    return ["mailchk", "gitchk"]


def offltest_1_10(funcname):
    logger.info(
        "%s: create a regression with a fix specified by a git summary that is not yet in the repo"
        % funcname
    )
    testfix_subject = "This is a test 123456789"

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: v1.8..v1.9-rc1\n#regzbot fix: %s" % testfix_subject,
    )

    subcounter += 1
    emaildirs["secondary"].create_email(
        "%s_%s" % (funcname, subcounter), "foo", subject=testfix_subject
    )

    gittrees_testing["mainline"].mv(commitmsg=testfix_subject)

    return ["mailchk", "gitchk"]


def offltest_1_11(funcname):
    logger.info(
        "%s: create a regression with a fix specified by a git summary that already in the tree"
        % funcname
    )
    testfix_subject = "This is a test 123456789"  # that's the commit for the previous test

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: v1.8..v1.9-rc1\n#regzbot fix: %s" % testfix_subject,
    )

    return ["mailchk"]


def offltest_1_12(funcname):
    subcounter = 0
    logger.info(
        "%s_%s: create a mainline regression and use Closes tag to resolve it"
        % (funcname, subcounter)
    )

    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )
    gittrees_testing["mainline"].mv(
        "Testcommit %s_%s\n\nCloses: https://lore.kernel.org/regressions/regzbot-testing-%s_%s@example.com\n"
        % (funcname, subcounter, funcname, subcounter)
    )

    return ["mailchk", "gitchk"]


def offltest_2_0(funcname):
    logger.info("%s: creating a mainline regression and add a link to it " % funcname)
    emaildirs["primary"].create_email(funcname, "#regzbot introduced: v1.8..v1.9-rc1")

    subcounter = 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot link https://www.kernel.org/releases.html Linktitle",
        replyto=funcname,
    )
    return ["mailchk"]


def offltest_2_1(funcname):
    replyto = "test_2_0"
    logger.info(
        "%s: update the title of the link just added to the regression created in %s"
        % (funcname, replyto)
    )
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot link https://www.kernel.org/releases.html Updated linktitle",
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_2_2(funcname):
    replyto = "test_2_0"
    logger.info("%s: remove the link to the regression created in %s" % (funcname, replyto))
    emaildirs["primary"].create_email(
        funcname, "#regzbot unlink https://www.kernel.org/releases.html", replyto=replyto
    )
    return ["mailchk"]


def offltest_2_3(funcname):
    replyto = "test_2_0"
    logger.info(
        "%s: refer to the regression created in %s on another mailing list" % (funcname, replyto)
    )
    emaildirs["secondary"].create_email(
        funcname,
        "https://lore.kernel.org/regressions/regzbot-testing-%s@example.com" % replyto,
        subject="%s: refer to this regression on another mainling list" % funcname,
    )
    return ["mailchk"]


def offltest_2_4(funcname):
    replyto = "test_2_0"
    referencedmail = "test_2_3"
    logger.info(
        "%s: in the regression created by %s, start to monitor the thread created in %s"
        % (funcname, replyto, referencedmail)
    )
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot monitor https://lore.kernel.org/lkml/regzbot-testing-%s@example.com"
        % referencedmail,
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_2_5(funcname):
    replyto = "test_2_3"
    logger.info("%s: add a reply to the thread %s that is now monitored" % (funcname, replyto))
    emaildirs["secondary"].create_email(
        funcname,
        "Lorem ipsum dolor sit amet",
        subject="%s: reply to the thread now monitored" % funcname,
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_2_6(funcname):
    replyto = "test_2_3"
    logger.info(
        "%s: use a rezbot comment in the thread %s that is now monitored" % (funcname, replyto)
    )
    emaildirs["secondary"].create_email(
        funcname,
        "#regzbot title new title set via a monitored thread",
        subject="%s: reply to the thread now monitored with a regzbot command" % funcname,
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_2_7(funcname):
    replyto = "test_2_0"
    referencedmail = "test_2_3"
    logger.info(
        "%s: in the regression created by %s, stop monitoring the thread created in %s"
        % (funcname, replyto, referencedmail)
    )
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot unmonitor https://lore.kernel.org/lkml/regzbot-testing-%s@example.com"
        % referencedmail,
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_2_8(funcname):
    replyto = "test_2_3"
    logger.info("%s: add a reply to the thread %s that is now unmonitored" % (funcname, replyto))
    emaildirs["secondary"].create_email(
        funcname,
        "Lorem ipsum dolor sit amet",
        subject="%s: reply to the thread now monitored" % funcname,
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_2_9(funcname):
    replyto = "test_2_0"
    logger.info(
        "%s: on another mainling list, refer to the regression created in %s with a link tag (will be monitored)"
        % (funcname, replyto)
    )
    emaildirs["secondary"].create_email(
        funcname,
        "Link: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com" % replyto,
        subject="%s: refer to this regression on another mainling list" % funcname,
    )
    return ["mailchk"]


def offltest_2_10(funcname):
    replyto = "test_2_8"
    logger.info(
        "%s: on another mainling list, add a reply to the thread %s that should be monitored now"
        % (funcname, replyto)
    )
    emaildirs["secondary"].create_email(
        funcname,
        "Lorem ipsum dolor sit amet",
        subject="%s: reply to the thread now monitored" % funcname,
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_2_11(funcname):
    # backmonitor was given up, this does nothing

    replyto = "test_2_0"
    logger.info(
        "%s: on another mainling list, use #regzbotot ^backmonitor to get a the regression created in %s monitored"
        % (funcname, replyto)
    )

    subcounter = 0
    emaildirs["secondary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "Lorem ipsum dolor sit amet",
        subject="%s_%s: a patch to fix a regression missing a Link: tag" % (funcname, subcounter),
    )
    subcounter += 1
    # emaildirs['secondary'].create_email("%s_%s" % (funcname, subcounter), "Link: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com\n\n#regzbotot ^backmonitor https://lore.kernel.org/regressions/regzbot-testing-%s@example.com" % (replyto, replyto),
    #                                    subject="%s_%s: get the previous mail monitored" % (funcname, subcounter),
    #                                    replyto='%s_0' % funcname)
    emaildirs["secondary"].create_email(
        "%s_%s" % (funcname, subcounter), "Lorem ipsum dolor sit amet", replyto="%s_0" % funcname
    )
    return ["mailchk"]


def offltest_2_12(funcname):
    subcounter = 0
    replyto = "test_2_0"
    logger.info("%s: a reply with a simple patch'" % funcname)

    subcounter += 1
    content = """something something

diff --git a/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c b/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c
index e4473a551241..57c947dad036 100644
--- a/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c
+++ b/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c
@@ -30,7 +30,8 @@ static bool rt2x00usb_check_usb_error(struct rt2x00_dev *rt2x00dev, int status)
	else
		rt2x00dev->num_proto_errs = 0;

-	if (rt2x00dev->num_proto_errs > 3)
+	if (rt2x00dev->num_proto_errs > 3 &&
+	    !test_bit(DEVICE_STATE_STARTED, &rt2x00dev->flags))
		return true;

	return false;"""

    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        content,
        subject="%s_%s: add a mail with a simple patch" % (funcname, subcounter),
        replyto=replyto,
    )

    subcounter += 1
    content = """something something

diff --git a/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c b/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c
index e4473a551241..57c947dad036 100644
--- a/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c
+++ b/drivers/net/wireless/ralink/rt2x00/rt2x00usb.c
@@ -30,7 +30,8 @@ static bool rt2x00usb_check_usb_error(struct rt2x00_dev *rt2x00dev, int status)
	else
		rt2x00dev->num_proto_errs = 0;

-	if (rt2x00dev->num_proto_errs > 3)
+	if (rt2x00dev->num_proto_errs > 3 &&
+	    !test_bit(DEVICE_STATE_STARTED, &rt2x00dev->flags))
		return true;

	return false;"""

    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        content,
        subject="[PATCH v2] %s_%s: add a mail with a simple patch" % (funcname, subcounter),
        replyto=replyto,
    )

    subcounter += 1
    content = """something something

From be7736582945b56e88d385ddd4a05e13e4bc6784 Mon Sep 17 00:00:00 2001
From: Alexei Starovoitov <ast@kernel.org>
Date: Wed, 10 Nov 2021 08:47:52 -0800
Subject: [PATCH] foo: bar

Fixes: 123456789ab ("foo: bar")
Signed-off-by: Nobody <nobody@example.co,>
---
 kernel/bpf/verifier.c | 3 ++-
 1 file changed, 2 insertions(+), 1 deletion(-)

diff --git a/kernel/bpf/verifier.c b/kernel/bpf/verifier.c
index 1aafb43f61d1..3eddcd8ebae2 100644
--- a/kernel/bpf/verifier.c
+++ b/kernel/bpf/verifier.c
@@ -1157,7 +1157,8 @@ static void mark_ptr_not_null_reg(struct bpf_reg_state *reg)
                        /* transfer reg's id which is unique for every map_lookup_elem
                         * as UID of the inner map.
                         */
-                       reg->map_uid = reg->id;
+                       if (map_value_has_timer(map->inner_map_meta))
+                               reg->map_uid = reg->id;
                } else if (map->map_type == BPF_MAP_TYPE_XSKMAP) {
                        reg->type = PTR_TO_XDP_SOCK;
                } else if (map->map_type == BPF_MAP_TYPE_SOCKMAP ||
--
2.30.2"""

    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        content,
        subject="%s_%s: add a mail with a simple patch" % (funcname, subcounter),
        replyto=replyto,
    )

    return ["mailchk"]


def offltest_2_13(funcname):
    replyto = "test_2_0"

    subcounter = 0
    logger.info(
        "%s_%s: set the introduced to a commit we know and mention it in another mail"
        % (funcname, subcounter)
    )
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: %s" % gittrees_testing["mainline"].hashes_known[-1],
        replyto=replyto,
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        'Foobar\nFixes: %s ("foo bar baz")\nSigned-off-by: Someone'
        % gittrees_testing["mainline"].hashes_known[-1][0:12],
    )

    return ["mailchk"]


def offltest_2_14(funcname):
    logger.info(
        "%s: create two regression and link to them using Link on another mailinglist" % (funcname)
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )
    replyto_1 = "%s_%s" % (funcname, subcounter)

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )
    replyto_2 = "%s_%s" % (funcname, subcounter)

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "Link: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com\nLink: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com"
        % (replyto_1, replyto_2),
        subject="%s_%s: refer to this regression on another mainling list" % (funcname, subcounter),
    )
    return ["mailchk"]


def offltest_2_15(funcname):
    logger.info(
        "%s: mark a regression that monitors some threads as a regression of another" % (funcname)
    )

    replyto = "test_2_0"
    dupof = "test_2_14_0"
    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot dup-of: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com"
        % dupof,
        replyto=replyto,
    )
    replyto_1 = "%s_%s" % (funcname, subcounter)
    return ["mailchk"]


def offltest_2_16(funcname):
    logger.info(
        "%s: create a regression and refer to it on another list using a closes tag (will be monitored)"
        % (funcname)
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )
    replyto = "%s_%s" % (funcname, subcounter)

    subcounter += 1
    emaildirs["secondary"].create_email(
        funcname,
        "Closes: https://lore.kernel.org/regressions/regzbot-testing-%s@example.com" % replyto,
        subject="%s: refer to newly created regression on another mainling list" % funcname,
    )
    return ["mailchk"]


def offltest_3_0(funcname):
    logger.info("%s: create a regression in next" % funcname)
    emaildirs["primary"].create_email(funcname, "#regzbot introduced: next-20190101..next-20190102")
    return ["mailchk"]


def offltest_3_1(funcname):
    replyto = "test_3_0"
    logger.info("%s: specify the culprit for the regression created in %s" % (funcname, replyto))
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot introduced: %s" % gittrees_testing["next"].hashes_known[1],
        replyto=replyto,
    )
    return ["mailchk"]


# mark regression as fixed by an existing commit
def offltest_3_2(funcname):
    replyto = "test_3_0"
    logger.info(
        "%s: mark regression created in %s as fixed by and exiting commit" % (funcname, replyto)
    )
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot fixed-by: %s" % gittrees_testing["next"].hashes_known[2],
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_3_3(funcname):
    logger.info("%s: create a regression in stable" % funcname)
    emaildirs["primary"].create_email(funcname, "#regzbot introduced: v1.8.1..v1.8.2")
    return ["mailchk"]


def offltest_3_4(funcname):
    replyto = "test_3_3"
    logger.info("%s: specify the culprit for the regression created in %s" % (funcname, replyto))
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot introduced: %s" % gittrees_testing["linux-1.8.y"].hashes_known[1][0:11],
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_3_5(funcname):
    replyto = "test_3_3"
    logger.info(
        "%s: mark regression created in %s as fixed by and exiting commit" % (funcname, replyto)
    )
    emaildirs["primary"].create_email(
        funcname,
        "#regzbot fixed-by: %s" % gittrees_testing["linux-1.8.y"].hashes_known[2],
        replyto=replyto,
    )
    return ["mailchk"]


def offltest_4_0(funcname):
    subcounter = 0
    logger.info("%s: creating a mainline regression in the current cycle (range)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10..v1.11-rc1"
    )

    subcounter += 1
    logger.info("%s: creating a mainline regression in the current cycle (bisected)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: %s" % gittrees_testing["mainline"].hashes_known[-1],
    )

    subcounter += 1
    logger.info("%s: creating a mainline regression in the previous cycle (range)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.9..v1.10-rc2"
    )

    subcounter += 1
    logger.info("%s: creating a mainline regression in the current cycle (bisected)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: %s" % gittrees_testing["mainline"].hashes_known[-4],
    )

    subcounter += 1
    logger.info("%s: creating a mainline regression in an older cycle (range)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.9-rc1"
    )

    subcounter += 1
    logger.info("%s: creating a mainline regression bisected in an older tree" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: %s" % gittrees_testing["mainline"].hashes_known[-8],
    )

    subcounter += 1
    logger.info("%s: creating a mainline regression where the range spans two releases" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.9..v1.11-rc1"
    )

    subcounter += 1
    logger.info("%s: creating a mainline regression in the current cycle with open end)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10.."
    )

    return ["mailchk"]


def offltest_4_1(funcname):
    subcounter = 0
    logger.info("%s: creating a linux-next regression (range)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: next-20190101..next-20190102"
    )

    subcounter += 1
    logger.info("%s: creating a linux-next regression (bisected)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: %s" % gittrees_testing["next"].hashes_known[1],
    )

    subcounter += 1
    logger.info("%s: creating a linux-next regression (range starting with mainline)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..next-20190102"
    )

    return ["mailchk"]


def offltest_4_2(funcname):
    subcounter = 0
    logger.info("%s: creating a linux-stable regression (range)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8.1..v1.8.2"
    )

    subcounter += 1
    logger.info("%s: creating a linux-stable regression (bisected)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: %s" % gittrees_testing["linux-1.8.y"].hashes_known[1][0:11],
    )

    subcounter += 1
    logger.info("%s: creating a linux-stable regression (range starting with mainline)" % funcname)
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.8..v1.8.1"
    )

    subcounter += 1
    logger.info(
        "%s: creating a regression with a range starting with a stable release and ending in mainline)"
        % funcname
    )
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10.2..v1.11-rc1"
    )

    subcounter += 1
    logger.info(
        "%s: creating a regression with a range starting with an earlier stable release and ending in mainline)"
        % funcname
    )
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.9.2..v1.10"
    )

    return ["mailchk"]


def offltest_4_3(funcname):
    subcounter = 0
    logger.info(
        "%s_%s: creating a regressions that refers to non-existant tag" % (funcname, subcounter)
    )
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v0.10..v0.11"
    )

    subcounter += 1
    logger.info(
        "%s_%s: creating a regressions that refers to non-existant tag" % (funcname, subcounter)
    )
    # as a side effect, the following mail will also make code fail that misses a str(foo), as something might put 123456789012 into an int instead of a string:
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: 123456789012"
    )

    return ["mailchk"]


def offltest_4_4(funcname):
    logger.info(
        "%s: creating a bunch of regressions and solve them in various ways to show everything in the webui"
        % funcname
    )

    subcounter = 0
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot introduced: %s" % gittrees_testing["mainline"].hashes_known[-1],
    )
    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot link https://www.kernel.org/releases.html Link somewhere",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10..v1.11-rc1"
    )
    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot fixed-by: %s" % gittrees_testing["mainline"].hashes_known[-2],
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10..v1.11-rc1"
    )
    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot fixed-by: 1234567890abcdef1234567890abcdef",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10..v1.11-rc1"
    )
    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot dupof: https://lore.kernel.org/regressions/regzbot-testing-%s_%s@example.com"
        % (funcname, subcounter - 3),
        replyto="%s_%s" % (funcname, subcounter - 1),
    )
    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10..v1.11-rc1"
    )
    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot resolve: some reason",
        replyto="%s_%s" % (funcname, subcounter - 1),
    )

    subcounter += 1
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot introduced: v1.10..v1.11-rc1"
    )
    gittrees_testing["next"].mv(
        "Testcommit %s\n\nLink: https://lore.kernel.org/regressions/regzbot-testing-%s_%s@example.com\n"
        % (funcname, funcname, subcounter)
    )

    return ["mailchk", "gitchk"]


# a regzbot command for a regression/ml thread that is not yet tracked
def offltest_5_0(funcname):
    logger.info("%s: create a regression as base for other tests" % funcname)
    emaildirs["primary"].create_email("%s" % funcname, "#regzbot introduced: v1.10..v1.11-rc1")
    return ["mailchk"]


def offltest_5_1(funcname):
    replyto = "test_5_0"

    subcounter = 0
    logger.info("%s_%s: use a unknown regzbot command" % (funcname, subcounter))
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot foobar: 123456789", replyto=replyto
    )

    subcounter += 1
    logger.info(
        "%s_%s: use a regzbot command in a thread not associated with a regression"
        % (funcname, subcounter)
    )
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter), "#regzbot fixed-by: 123456789"
    )

    return ["mailchk"]


def offltest_5_2(funcname):
    replyto = "test_5_0"

    subcounter = 0
    logger.info("%s_%s: try regzbot monitor with a typo in the url" % (funcname, subcounter))
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot monitor: http://lore.kernel.org/somelist_somemsgid/",
        replyto=replyto,
    )

    subcounter += 1
    logger.info("%s_%s: try regzbot monitor with a unkown mailing list " % (funcname, subcounter))
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot monitor: http://lore.kernel.org/somelist/somemsgid/",
        replyto=replyto,
    )

    subcounter += 1
    logger.info(
        "%s_%s: try regzbot unmonitor with a typo a unkown mailing list " % (funcname, subcounter)
    )
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot unmonitor: http://lore.kernel.org/somelist_somemsgid/",
        replyto=replyto,
    )

    subcounter += 1
    logger.info("%s_%s: try regzbot unmonitor with a typo in the url" % (funcname, subcounter))
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot unmonitor: http://lore.kernel.org/somelist/somemsgid/",
        replyto=replyto,
    )

    subcounter += 1
    logger.info("%s_%s: try regzbot unmonitor with a unkown mailing list " % (funcname, subcounter))
    emaildirs["primary"].create_email(
        "%s_%s" % (funcname, subcounter),
        "#regzbot unmonitor: http://lore.kernel.org/regressions/some_fake_msgid/",
        replyto=replyto,
    )

    return ["mailchk"]
