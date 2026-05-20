#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2023 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

from regzbot import ReportSource
from regzbot import ReportThread
import datetime


class GenRepSrc(ReportSource):
    def thread(self, *, url=None, id=None):
        # for a generic report they are identical
        if not url:
            url = id
        return GenRepTrd(self, url)


class GenRepTrd(ReportThread):
    def __init__(self, repsrc, url):
        self.repsrc = repsrc
        self.created_at = datetime.datetime.now(datetime.timezone.utc)
        self.id = url
        self.summary = "Unknown"
        self.realname = "Unknown"
        self.username = None
        super().__init__()

    @property
    def gmtime(self):
        return int(self.created_at.timestamp())

    def update(self, *args, **kwargs):
        return
