#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import os
import sys

import regzbot
import regzbot.export_csv

logger = regzbot.logger


def init(tmpdir):
    regzbot.set_citesting("online")
    regzbot.basicressources_setup(
        tmpdir=tmpdir, gittreesdir=True, databasedir=os.path.join(tmpdir, "db-onlinetsts")
    )
    regzbot.basicressources_init(
        tmpdir=tmpdir, gittreesdir=True, databasedir=os.path.join(tmpdir, "db-onlinetsts")
    )


def run(resultfilename, tmpdir, _):
    init(tmpdir)

    regzbot.GitTree.updateall()

    resultfile = open(resultfilename, "a")
    testfuncprefix = "onlntest"
    this = sys.modules[__name__]

    outercount = 0
    while "%s_%s_0" % (testfuncprefix, outercount) in dir(this):
        regzbot.db_rollback()

        innercount = 0
        while "%s_%s_%s" % (testfuncprefix, outercount, innercount) in dir(this):
            # run test
            callfunction = getattr(this, "%s_%s_%s" % (testfuncprefix, outercount, innercount))
            chk_mail, chk_git, wait = callfunction("test_%s_%s" % (outercount, innercount))

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
            innercount += 1
        outercount += 1
    resultfile.close()
    regzbot.db_commit()
    regzbot.db_close()


def onlntest_0_0(funcname):
    regzbot.checkout_msgid("a11ba91f-a520-e6ab-5566-dfc9fd934440@leemhuis.info")
    return False, False, False


def onlntest_0_1(funcname):
    regzbot.checkout_msgid("6d62738a-b213-dc9c-c13f-7d4eaa7e46b8@leemhuis.info")
    return False, False, False


def onlntest_0_2(funcname):
    regzbot.checkout_msgid("438d711b-094b-fcfd-79e3-69f03a14df21@leemhuis.info")
    return False, False, False


# the last mail in the thread will only find the report by walking the thread


def onlntest_0_3(funcname):
    regzbot.checkout_msgid("5f445dab-a152-bcaa-4462-1665998c3e2e@gmail.com")
    return False, False, False


def onlntest_1_0(funcname):
    # uses ^introduced for a report in 5edaa2b7c2fe4abd0347b8454b2ac032b6694e2c5edaa2b7c2fe4abd0347b8454b2ac032b6694e2c.camel@collabora.com
    regzbot.checkout_msgid("ae2879df-64b8-0258-e4ee-59d7c279676f@leemhuis.info")
    return False, False, False


# def onlntest_1_1(funcname):
#    regzbot.redo_regressions(['5edaa2b7c2fe4abd0347b8454b2ac032b6694e2c.camel@collabora.com', ])
#    return False, False, False
