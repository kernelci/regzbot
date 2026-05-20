#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2021 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"


import argparse
import logging
import os
import tempfile
import sys

import regzbot
import regzbot.testing
import regzbot._rbcmd
import regzbot._repsources
import regzbot._repsources._bugzilla
import regzbot._repsources._lore
import regzbot._repsources._gitlab
import regzbot._repsources._github
import regzbot._repsources._generic


logger = regzbot.logger


def get_testresults_datadir():
    # check if we are running from git
    basedir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    testingdir = os.path.join(basedir, "testdata")
    if os.path.exists(testingdir):
        return testingdir
    return None


def cmd_setup(cmdargs):
    regzbot.basicressources_setup()


def cmd_recheck(cmdargs):
    regzbot.recheck(cmdargs.msgids_to_check)


def cmd_run(cmdargs):
    regzbot.run()


def cmd_pages(cmdargs):
    regzbot.generate_web()


def cmd_report(cmdargs):
    regzbot.report()


def cmd_test(cmdargs):
    # which tests to run
    cmdargs_dict = vars(cmdargs)
    testmodes = {}
    only = ""
    for mode in regzbot.testing.SUPPORTED_TESTMODES.keys():
        if cmdargs_dict[mode]:
            only = mode
    for mode in regzbot.testing.SUPPORTED_TESTMODES.keys():
        if only and only != mode:
            testmodes[mode] = False
        else:
            testmodes[mode] = True

    # run
    if cmdargs.tmpdir:
        regzbot.testing.run(testmodes, get_testresults_datadir(), cmdargs.tmpdir)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            regzbot.testing.run(testmodes, get_testresults_datadir(), tmpdir)


def cmd():
    parser = argparse.ArgumentParser(
        prog="regzbot",
        description="A bot for tracking Linux kernel regressions",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # basics
    parser.add_argument("--version", action="version", version=regzbot.__VERSION__)
    parser.add_argument(
        "--debug", action="store_true", default=False, help="Enable debugging info in output"
    )
    parser.add_argument(
        "--quiet", action="store_true", default=False, help="Only print critical information"
    )

    # subcommands
    subparsers = parser.add_subparsers(help="sub-command help", dest="subcmd")

    # setup
    sparser_setup = subparsers.add_parser("setup", help="Initialize regzbot")
    sparser_setup.set_defaults(func=cmd_setup)

    # run
    sparser_run = subparsers.add_parser("run", help="Run regzbot")
    sparser_run.set_defaults(func=cmd_run)

    # export web pages
    sparser_run = subparsers.add_parser("pages", help="Generate regzbot HTML pages")
    sparser_run.set_defaults(func=cmd_pages)

    # recheck
    sparser_recheck = subparsers.add_parser("recheck", help="Recheck messages")
    sparser_recheck.add_argument(dest="msgids_to_check", help="msgids to recheck", nargs="+")
    sparser_recheck.set_defaults(func=cmd_recheck)

    # status
    sparser_report = subparsers.add_parser("report", help="Send a status report")
    sparser_report.set_defaults(func=cmd_report)

    # test
    if get_testresults_datadir():
        sparser_test = subparsers.add_parser("test", help="run tests")
        sparser_test.add_argument(
            "--tmpdir",
            dest="tmpdir",
            default=None,
            help="Directory for creating repos and mails for testing",
        )
        for mode in regzbot.testing.SUPPORTED_TESTMODES.keys():
            sparser_test.add_argument(
                "--%s" % mode, action="store_true", default=False, help="Run only %s tests" % mode
            )
        sparser_test.set_defaults(func=cmd_test)

    # parse
    cmdargs = parser.parse_args()

    # handle basics
    logger.setLevel(logging.DEBUG)
    loghandler = logging.StreamHandler()
    loghandler.setFormatter(logging.Formatter("%(message)s"))
    if cmdargs.quiet:
        loghandler.setLevel(logging.CRITICAL)
    elif cmdargs.debug:
        loghandler.setLevel(logging.DEBUG)
    else:
        loghandler.setLevel(logging.INFO)
    logger.addHandler(loghandler)

    # go
    if "func" not in cmdargs:
        parser.print_help()
        sys.exit(1)

    cmdargs.func(cmdargs)


if __name__ == "__main__":
    cmd()
