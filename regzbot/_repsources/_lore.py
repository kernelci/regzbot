#! /usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0
# Copyright (C) 2024 by Thorsten Leemhuis
__author__ = "Thorsten Leemhuis <linux@leemhuis.info>"

import datetime
import email
import email.policy
import gzip
import nntplib
import mailbox
import re
import urllib.request
import tempfile
import shutil

from regzbot import PatchKind
from regzbot import ReportSource
from regzbot import ReportThread
from functools import cached_property

if __name__ != "__main__":
    import regzbot

    logger = regzbot.logger
else:
    import logging

    logger = logging
    # if False:
    if True:
        logger.basicConfig(level=logging.DEBUG)


_NNTP_CONNECTION = None


class LoreNntp:
    # without this, occasionally [as on 20210831] errors like "nntplib.NNTPDataError: line too long" occur; not sure,
    # might be a bug in the public-inbox code behind lore
    nntplib._MAXLINE = 65536

    def __init__(self):
        self.__init_connection()

    def __init_connection(self, forced_reconnect=False):
        global _NNTP_CONNECTION
        if forced_reconnect or not _NNTP_CONNECTION:
            logger.debug("[lore] connecting to nntp.lore.kernel.org")
            _NNTP_CONNECTION = nntplib.NNTP("nntp.lore.kernel.org")
        self._nntp_connection = _NNTP_CONNECTION

    def _article(self, id, group):
        if isinstance(id, str) and id[0] != "<":
            id = "<%s>" % id
        try:
            _, article = self._nntp_connection.article(id)
        except ConnectionResetError:
            # reconnect and retry once
            self.__init_connection(forced_reconnect=True)
            self._group(group)
            _, article = self._nntp_connection.article(id)
        return email.message_from_bytes(b"\n".join(article.lines), policy=email.policy.default)

    def _group(self, groupname):
        splitted = groupname.split("/", maxsplit=4)
        if len(splitted) > 2:
            groupname = splitted[3]
        else:
            groupname = splitted[0]
        logger.debug("[lore] opening group %s", groupname)
        _, _, id_first, id_last, _ = self._nntp_connection.group(groupname)
        return id_first, id_last

    def _over(self, id_first, id_last):
        _, overviews = self._nntp_connection.over((id_first, id_last))
        for id, over in overviews:
            yield id, over


class LoreHttps:
    @staticmethod
    def download_thread(msgid, *, repsrc=None):
        if regzbot.is_running_citesting("offline"):
            import os

            found_something = False
            for directory in regzbot._TESTING["emaildirs"]:
                filename = os.path.join(directory, "%s.regzbot" % msgid)
                if not os.path.isfile(filename):
                    continue
                if not found_something:
                    found_something = True
                for mboxmsg in mailbox.mbox(filename):
                    yield email.message_from_bytes(mboxmsg.as_bytes(), policy=email.policy.default)
            if not found_something:
                raise regzbot.RepDownloadError
        else:
            with tempfile.NamedTemporaryFile() as tmpfile:
                url = "https://lore.kernel.org/all/%s/t.mbox.gz" % msgid
                try:
                    logger.debug("[lore] downloading %s", url)
                    with urllib.request.urlopen(url) as response:
                        with gzip.open(response) as uncompressed:
                            shutil.copyfileobj(uncompressed, tmpfile)
                except urllib.error.HTTPError as err:
                    logger.critical("[lore] failed to download thread from %s: %s", url, err)
                    raise regzbot.RepDownloadError
                for message in mailbox.mbox(tmpfile.name):
                    yield email.message_from_bytes(message.as_bytes(), policy=email.policy.default)


# unused as of now
#
#   @staticmethod
#   def download_msg(msgid):
#       with tempfile.NamedTemporaryFile() as tmpfile:
#           url='https://lore.kernel.org/all/%s/raw' % msgid
#           try:
#               logger.debug("[lore] downloading %s", url)
#               with urllib.request.urlopen(url) as response:
#                   shutil.copyfileobj(response, tmpfile)
#                   return True
#           except urllib.error.HTTPError as err:
#               logger.warning('[lore] could not download msg %s: %s"', msgid, err)
#               raise LoreDownloadError()
#
#           # result might contain a raw msg or a mbox file with multiple messages
#           mbox = mailbox.mbox(tmpfile.name)
#           if mbox:
#               for message in mbox:
#                    # just pick the first one
#                    return email.message_from_bytes(message.as_bytes(), policy=email.policy.default)
#           else:
#               tmpfile.seek(0)
#               return email.message_from_string(tmpfile.read().decode('utf-8', errors='ignore'), policy=email.policy.default)


class LoActivity:
    def __init__(self, lo_thread, msg):
        self.lo_thread = lo_thread
        self._msg = msg
        self._realname = None
        self._username = None
        self.best_repsrc = LoRepSrc.best_repsrc(self.recipients)
        self.web_url = "https://lore.kernel.org/all/%s" % self.id

    @cached_property
    def ancestors(self):
        ancestors = []
        for msgid_reference in self._headerparse_references():
            ancestors.insert(0, msgid_reference)
        msgiid_inreplyto = self._headerparse_inreplyto()
        if msgiid_inreplyto:
            if msgiid_inreplyto in ancestors and ancestors[0] != msgiid_inreplyto:
                ancestors.remove(msgiid_inreplyto)
            if msgiid_inreplyto not in ancestors:
                ancestors.insert(0, msgiid_inreplyto)
        return ancestors

    @cached_property
    def created_at(self):
        dt = email.utils.parsedate_to_datetime(self._msg["Date"])
        # the following is needed to handle mails with -00.00 tz specifier, like
        # https://lore.kernel.org/all/170979602040.580595.2365620815888707390@8e613ede5ea5/
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt

    @cached_property
    def gmtime(self):
        return int(self.created_at.timestamp())

    @cached_property
    def id(self):
        return self.validate_msgid(self._msg["message-id"])

    @cached_property
    def message(self):
        msg_body = self._msg.get_body(preferencelist=("plain"))
        # handle messages without a body, like https://lore.kernel.org/all/1fea1273-f5ba-52a6-85db-2b828982f8b7@amd.com/
        if not msg_body:
            return ""
        # handle messages with unkown encoding, like https://lore.kernel.org/lkml/20240226112816.2616719-1-quic_kriskura@quicinc.com/
        try:
            content = msg_body.get_content()
        except LookupError as err:
            return ""
        return content

    @cached_property
    def recipients(self):
        recipients = []
        for field in ("To", "CC"):
            if field not in self._msg:
                continue
            # sane workarund as above, triggered by
            try:
                recipients.extend(re.findall(r"[\w\.-]+@[\w\.-]+", self._msg[field]))
            except AttributeError as err:
                # handle mails without To:, for example
                #  https://lore.kernel.org/all/20211005053239.3E8DEC4338F@smtp.codeaurora.org/raw
                #  https://lore.kernel.org/all/20210925074531.10446-1-tomm.merciai@gmail.com/raw
                # related: https://bugs.python.org/issue39100
                logger.warning(
                    'Ignoring "%s" in %s due to and exception: "AttributeError: %s"',
                    field,
                    self.validate_msgid(self._msg["message-id"]),
                    err,
                )
            except ValueError as err:
                # Workaround for https://lore.kernel.org/all/1634261360.fed2opbgxw.astroid@bobo.none/raw
                #     -> "ValueError: invalid arguments; address parts cannot contain CR or LF"
                logger.warning(
                    'Ignoring "%s" in %s due to and exception: "ValueError: %s"',
                    field,
                    self.validate_msgid(self._msg["message-id"]),
                    err,
                )
            except IndexError as err:
                # workaround for the "=?utf-8?q?=2C?=linux-arm-msm@vger.kernel.org" in
                # https://lore.kernel.org/linux-pci/166983076821.2517843.6476270112700027226.robh@kernel.org/raw
                logger.warning(
                    'Ignoring "field" in %s due to an exception: "HeaderParseError: %s"',
                    field,
                    self.validate_msgid(self._msg["message-id"]),
                    err,
                )
            except TypeError as err:
                # workaround for the ".@3429e2599065" in
                # https://lore.kernel.org/all/202312271450.C9YmLJn2-lkp@intel.com/
                logger.warning(
                    'Ignoring "field" in %s due to an exception: "TypeError: %s"',
                    field,
                    self.validate_msgid(self._msg["message-id"]),
                    err,
                )
        return recipients

    @cached_property
    def patchkind(self):
        patchkind = PatchKind.getby_content(self.message, subject=self.subject)
        if patchkind == 0:
            for attachment in self._msg.iter_attachments():
                if not attachment.get_content_maintype().startswith("text/"):
                    continue
                # create a new mail here, as that will allow easier handling for mailed git patches
                #  and does not hurt in other cases
                mocked_msg = email.message.EmailMessage()
                mocked_msg.set_content(attachment.get_content())
                if "subject" in mocked_msg:
                    newpatchkind = PatchKind.getby_content(
                        mocked_msg.get_content(), subject=mocked_msg["subject"]
                    )
                else:
                    newpatchkind = PatchKind.getby_content(mocked_msg.get_content())
                if newpatchkind > patchkind:
                    patchkind = newpatchkind
        return patchkind

    @property
    def realname(self):
        if self._realname is None:
            self._headerparse_from()
        return self._realname

    @cached_property
    def subject(self):
        # yes, there are mails without subject:
        # https://lore.kernel.org/linux-usb/trinity-09ddec50-a8ca-4663-ba91-4331ab43c9e4-1639982794116@3c-app-gmx-bs07/raw
        if "subject" in self._msg and self._msg["subject"] != "":
            return self._validate_subject(self._msg["subject"])
        return "<no subject>"

    @cached_property
    def summary(self):
        return self._subject_tagless(self.subject)

    @property
    def username(self):
        if self._username is None:
            self._headerparse_from()
        return self._username

    def __str__(self):
        return _describe(
            self,
            ("created_at", "message", "realname", "patchkind", "summary", "username", "web_url"),
        )

    def _headerparse_from(self):
        self._realname, self._username = email.utils.parseaddr(self._msg["From"])
        if len(self._realname) == 0:
            self._realname = re.sub(r"@.*", "", self._username)

    def _headerparse_references(self):
        if "references" in self._msg:
            for msgid in self._msg["References"].split():
                yield self.validate_msgid(msgid)

    def _headerparse_inreplyto(self):
        if "In-Reply-To" in self._msg:
            return self.validate_msgid(self._msg["In-Reply-To"])
        return None

    @staticmethod
    def validate_msgid(msgid):
        # this gets rid of everything after > (some email clients insert something there...)
        msgid = msgid.split(">", 1)
        return msgid[0].strip(" <>")

    @staticmethod
    def _validate_subject(subject):
        return subject.replace("\n", "").strip()

    @staticmethod
    def _subject_tagless(subject):
        return re.sub(r"^ *\[regression\] *", "", subject, flags=re.IGNORECASE)


class LoreThread:
    def __init__(self, *, msgid=None, msg=None):
        if msgid and not msg:
            self._id = urllib.parse.unquote(msgid)
            self._init_activity = {}
        elif msg and not msgid:
            loact = LoActivity(self, msg)
            self._init_activity = {
                loact.id: loact,
            }
            self._id = loact.id
        else:
            raise RuntimeError

    @cached_property
    def _all_activities(self):
        all_activities = {}
        for msg in LoreHttps.download_thread(self._id):
            # ignore messages without a message-id; happens for some reasons when parsing
            # https://lore.kernel.org/all/ZdiLCYKCujs4DgKV@matsya/t.mbox.gz
            if not msg["message-id"]:
                continue
            lo_act = LoActivity(self, msg)
            if lo_act.id in all_activities:
                continue
            all_activities[lo_act.id] = lo_act
        return all_activities

    def _activities(self, msgid):
        def is_reply(lo_act, related_msgids):
            for reference in lo_act.ancestors:
                if reference in related_msgids:
                    return True

        activities = []
        related_msgids = []
        for lo_act in self._all_activities.values():
            if msgid == lo_act.id or is_reply(lo_act, related_msgids):
                activities.append(lo_act)
                related_msgids.append(lo_act.id)
        activities.sort(key=lambda x: x.created_at)
        return activities

    @property
    def root(self):
        for id in self._all_activities:
            return self._all_activities[id].id

    def activity(self, *, msgid=None):
        if not msgid:
            msgid = self._id
        if msgid in self._init_activity:
            return self._init_activity[msgid]
        if msgid not in self._all_activities:
            pass
        return self._all_activities[msgid]

    def activities(self, *, since=None, until=None, msgid=None):
        if not msgid:
            msgid = self._id
        for activity in self._activities(msgid):
            if since and activity.created_at < since:
                continue
            elif until and activity.created_at > until:
                continue
            yield activity


class LoRepAct(regzbot.ReportActivity):
    def __init__(self, reptrd, lo_activity):
        # take adjusted repsrc, if one could be found
        if lo_activity.best_repsrc:
            self.repsrc = lo_activity.best_repsrc
        else:
            self.repsrc = reptrd.repsrc
        assert self.repsrc

        self.lo_activity = lo_activity
        self.created_at = lo_activity.created_at
        self.gmtime = int(lo_activity.created_at.timestamp())
        self.id = lo_activity.id
        self.lo_thread = lo_activity.lo_thread
        self.message = lo_activity.message
        self.patchkind = lo_activity.patchkind
        self.realname = lo_activity.realname
        self.subject = lo_activity.summary
        self.summary = lo_activity.summary
        self.username = lo_activity.username

        # reptrd need to be adjusted for lore
        if reptrd.id == lo_activity.id:
            self.reptrd = reptrd
        else:
            self.reptrd = LoRepTrd(self.repsrc, self.lo_thread, lo_activity=self.lo_activity)
        self.id = None

        super().__init__()


class LoRepSrc(ReportSource):
    def supports_url(self, url_lowered, url_parsed):
        if url_parsed.netloc in ("lore.kernel.org", "lkml.kernel.org") and (
            self.name == "lore_all" or regzbot.is_running_citesting("offline")
        ):
            path_split = url_parsed.path.split("/", maxsplit=3)
            if len(path_split) < 3:
                raise regzbot.RepDownloadError
            if not path_split[2]:
                logger.error("[lore] cound not parse %s", url_parsed.geturl())
                raise regzbot.RepDownloadError
            return path_split[2]

    def thread(self, *, id=None, url=None):
        if not id:
            url
            id = self.supports_url(url.lower())
            if not id:
                logger.error("[lore] cound not parse %s", url)
                raise regzbot.RepDownloadError
        lo_thread = LoreThread(msgid=id)
        return LoRepTrd(self, lo_thread)

    @staticmethod
    def best_repsrc(recipients):
        new_repsrc = None
        for address in recipients:
            tmp_repsrc = regzbot.ReportSource.get_by_identifier(address)
            if not tmp_repsrc or tmp_repsrc.kind != "lore":
                continue
            elif not new_repsrc:
                new_repsrc = tmp_repsrc
            elif tmp_repsrc.priority < new_repsrc.priority:
                new_repsrc = tmp_repsrc
        return new_repsrc

    def update(self):
        if regzbot.is_running_citesting("offline"):
            import pathlib
            import os

            filenames = sorted(pathlib.Path(self.serverurl).iterdir(), key=os.path.getmtime)
            for file in filenames:
                if os.path.islink(file):
                    continue
                for mboxmsg in mailbox.mbox(file):
                    msg = email.message_from_bytes(mboxmsg.as_bytes(), policy=email.policy.default)
                    lo_thread = LoreThread(msg=msg)
                    lo_retrd = LoRepTrd(self, lo_thread)
                    if regzbot.RecordProcessedMsgids.check_presence(lo_retrd.id, lo_retrd.gmtime):
                        continue
                    lo_retrd.process_single()
        else:
            if self.name == "lore_all":
                return

            lorenntp = LoreNntp()
            id_first, id_last = lorenntp._group(self.serverurl)

            if not self.lastchked:
                self.set_lastchked(id_first)
                logger.info(
                    "[lore] seeing %s for the first time, starting to monitor it from now on",
                    self.serverurl,
                )
                self.set_lastchked(id_last)
                return
            elif self.lastchked == id_last:
                logger.debug("[lore] nothing new in %s", self.serverurl)
                return

            logger.debug('[lore] processing "%s"', self.serverurl)
            for id, over in lorenntp._over(self.lastchked + 1, id_last):
                msgid = LoActivity.validate_msgid(over["message-id"])
                gmtime = email.utils.mktime_tz(email.utils.parsedate_tz(over["date"]))
                if regzbot.RecordProcessedMsgids.check_presence(msgid, gmtime):
                    logger.debug('[lore] skipping "%s", we already encountered it it', msgid)
                    continue

                msg = lorenntp._article(id, self.serverurl)
                if "subject" in msg and msg["subject"].startswith(regzbot.REPORT_SUBJECT_PREFIX):
                    logger.debug("[lore] skipping mail %s, as it's a report we send", msgid)
                    continue
                if "from" in msg:
                    if (
                        "bugzilla-daemon@kernel.org" in msg["from"]
                        or "bugbot@kernel.org" in msg["from"]
                    ):
                        logger.debug("[lore] skipping mail %s, as it's a bugzilla mail", msgid)
                        continue
                lo_thread = LoreThread(msg=msg)
                lo_retrd = LoRepTrd(self, lo_thread)
                lo_retrd.process_single()

            # update database
            self.set_lastchked(id_last)


class LoRepTrd(ReportThread):
    def __init__(self, repsrc, lo_thread, *, lo_activity=None):
        self._lo_thread = lo_thread
        self.supports_relatives = True

        # lore breaks with the model here that is based on bug trackers; work around this here
        if lo_activity:
            self._lo_activity = lo_activity
        else:
            self._lo_activity = lo_thread.activity()
        if self._lo_activity.best_repsrc:
            self.repsrc = self._lo_activity.best_repsrc
        else:
            self.repsrc = repsrc

        self.created_at = self._lo_activity.created_at
        self.id = self._lo_activity.id
        self.realname = self._lo_activity.realname
        self.summary = self._lo_activity.summary
        self.username = self._lo_activity.username

        super().__init__()

    @cached_property
    def gmtime(self):
        return int(self.created_at.timestamp())

    @cached_property
    def repsrc(self):
        return self._lo_thread.best_repsrc

    def ancestors(self):
        if self._lo_activity.ancestors:
            for msgid in self._lo_activity.ancestors:
                yield msgid

    def root(self):
        return self._lo_thread.root

    def process_single(self):
        repact = LoRepAct(self, self._lo_activity)
        try:
            regzbot._rbcmd.process_activity(repact)
        except regzbot._rbcmd.RegressionCreatedException:
            pass

    def update(self, since, until, *, actimon=None, triggering_repact=None):
        # handle this here and don't feed the msgs through the regular parsing code, as they might already have been
        #  processed earlier
        try:
            for activity in self._lo_thread.activities(msgid=self.id, since=since, until=until):
                # add the activity to the list of processed ids, as we might not have seen it yet; but nevertheless
                #  process it again, as it might have been irrelevant earlier, but that might have changed
                regzbot.RecordProcessedMsgids.check_presence(activity.id, gmtime=activity.gmtime)
                repact = LoRepAct(self, activity)
                regzbot._rbcmd.process_activity(
                    repact, actimon=actimon, triggering_repact=triggering_repact
                )

        except regzbot._rbcmd.RegressionCreatedException:
            # the handled activity contained a #regzbot introduced that created a regression for this issue; during that
            # process all activities (both older and younger) for it will be added by calling this method again, so
            # there is nothing more for us to do here
            pass


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
