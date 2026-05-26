#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import datetime
import os
import sys

import regzbot

import regzbot.export_csv

logger = regzbot.logger


def init(tmpdir):
    regzbot.set_citesting("trackers")
    regzbot.basicressources_setup(
        tmpdir=tmpdir, gittreesdir=True, databasedir=os.path.join(tmpdir, "db-trackertsts")
    )
    regzbot.basicressources_init(
        tmpdir=tmpdir, gittreesdir=True, databasedir=os.path.join(tmpdir, "db-trackertsts")
    )
    regzbot.GitTree.updateall()


def run(resultfilename, tmpdir, _):
    init(tmpdir)

    resultfile = open(resultfilename, "a")
    testfuncprefix = "trackertest"
    this = sys.modules[__name__]

    outercount = 0
    while "%s_%s_0" % (testfuncprefix, outercount) in dir(sys.modules[__name__]):
        regzbot.db_rollback()

        innercount = 0
        while "%s_%s_%s" % (testfuncprefix, outercount, innercount) in dir(this):
            # run test
            callfunction = getattr(this, "%s_%s_%s" % (testfuncprefix, outercount, innercount))
            chk_git, wait = callfunction("test_%s_%s" % (outercount, innercount))

            if chk_git:
                for gittree in regzbot.GitTree.getall():
                    gittree.update()

            # write results
            resultfile.write("[%s_%s_%s]\n" % (testfuncprefix, outercount, innercount))
            for data in regzbot.export_csv.dumpall_csv():
                resultfile.write(data)
            resultfile.write("\n")

            regzbot.export_web.RegExportWeb.compile()

            if wait:
                # regzbot.db_commit()
                os.system('read -p "Press any key to continue"')

            # finish this up
            regzbot._TESTING["until"] = None
            innercount += 1
        outercount += 1
    resultfile.close()
    regzbot.db_commit()
    regzbot.db_close()


def trackertest_0_0(funcname):
    regzbot.ReportSource.add(
        "regzbottesting-gitlab",
        3,
        "https://gitlab.com/knurd42/linux",
        "gitlab",
        "<unused>",
        lastchked=int(datetime.datetime.fromisoformat("2023-11-20T00:00:00.000Z").timestamp()),
    )
    regzbot.ReportSource.add(
        "regzbottesting-github",
        3,
        "https://github.com/knurd/linux",
        "github",
        "<unused>",
        lastchked=int(datetime.datetime.fromisoformat("2022-03-15T00:00:00.000Z").timestamp()),
    )

    regzbot._TESTING["until"] = datetime.datetime.fromisoformat("2023-11-20T11:35:00.000Z")

    regzbot.checkout_url("https://gitlab.com/knurd42/linux/-/issues/11")
    return False, False


def trackertest_0_1(funcname):
    regzbot._TESTING["until"] = datetime.datetime.fromisoformat("2023-11-20T11:37:00.000Z")
    regzbot.checkout_url("https://gitlab.com/knurd42/linux/-/issues/11")
    return False, False


def trackertest_0_2(funcname):
    regzbot._TESTING["until"] = datetime.datetime.fromisoformat("2023-11-20T12:22:00.000Z")
    regzbot.checkout_url("https://gitlab.com/knurd42/linux/-/issues/11")
    return False, False


def trackertest_0_3(funcname):
    regzbot._TESTING["until"] = datetime.datetime.fromisoformat("2023-11-20T12:30:00.000Z")
    regzbot.checkout_url("https://gitlab.com/knurd42/linux/-/issues/11")
    return False, False


def trackertest_0_4(funcname):
    regzbot._TESTING["until"] = datetime.datetime.fromisoformat("2023-11-20T13:00:00.000Z")
    regzbot.checkout_url("https://gitlab.com/knurd42/linux/-/issues/11")
    return False, False


def trackertest_0_5(funcname):
    regzbot._TESTING["until"] = datetime.datetime.fromisoformat("2023-11-20T13:01:45.000Z")
    regzbot.checkout_url("https://gitlab.com/knurd42/linux/-/issues/11")
    return False, False


def trackertest_0_6(funcname):
    regzbot._TESTING["until"] = datetime.datetime.fromisoformat("2023-11-20T13:02:45.000Z")
    regzbot.checkout_url("https://gitlab.com/knurd42/linux/-/issues/11")
    return False, False
