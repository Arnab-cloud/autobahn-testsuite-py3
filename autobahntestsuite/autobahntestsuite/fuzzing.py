###############################################################################
##
##  Copyright (c) typedef int GmbH
##
##  Licensed under the Apache License, Version 2.0 (the "License");
##  you may not use this file except in compliance with the License.
##  You may obtain a copy of the License at
##
##      http://www.apache.org/licenses/LICENSE-2.0
##
##  Unless required by applicable law or agreed to in writing, software
##  distributed under the License is distributed on an "AS IS" BASIS,
##  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
##  See the License for the specific language governing permissions and
##  limitations under the License.
##
###############################################################################

__all__ = ["startClient", "startServer", "WS_COMPRESSION_TESTDATA"]


import binascii

# for versions
import importlib.resources
import json
import os
import sys
import textwrap
import time

import autobahn

# import pkg_resources
from autobahn.twisted.websocket import (
    WebSocketClientFactory,
    WebSocketClientProtocol,
    WebSocketServerFactory,
    WebSocketServerProtocol,
    connectWS,
    listenWS,
)
from autobahn.util import utcnow
from autobahn.websocket.protocol import WebSocketProtocol
from case import (
    Case,
    CaseBasename,
    CaseCategories,
    Cases,
    CaseSetname,
    CaseSubCategories,
)

# from case.case import Case
from caseset import CaseSet
from report import CSS_COMMON, CSS_DETAIL_REPORT, CSS_MASTER_REPORT, JS_MASTER_REPORT
from twisted.internet import reactor, ssl
from twisted.internet.protocol import connectionDone
from twisted.python import log
from twisted.python.failure import Failure
from twisted.web.server import Site
from twisted.web.static import File

import autobahntestsuite
from autobahntestsuite.wstest import WsTestOptions


def convert(obj):
    try:
        if isinstance(obj, bytes):
            return obj.decode("utf-8")
        raise TypeError(f"Type not serializable: {type(obj)}")
    except TypeError as e:
        raise e
    except Exception:
        return obj.hex()


def binLogData(data: bytes | str, maxlen=64):
    ellipses = " ..."

    if isinstance(data, str):
        bin_data = data.encode()
    else:
        bin_data = data

    if len(data) > maxlen - len(ellipses):
        dd = binascii.b2a_hex(bin_data[:maxlen]).hex() + ellipses
    else:
        dd = bin_data.hex()
    return dd


# def asciiLogData(data, maxlen=64, replace=False):
#     ellipses = " ..."
#     try:
#         if len(data) > maxlen - len(ellipses):
#             dd = data[:maxlen]
#         else:
#             dd = data
#         return dd.decode("utf8", errors="replace" if replace else "strict")
#     except Exception:
#         return "0x" + binLogData(data, maxlen)


def asciiLogData(
    data: bytes, isBinary: bool = True, maxlen: int = 64, replace: bool = False
):
    ellipses = " ..."

    if not isBinary:
        strData = data.decode("utf-8", errors="replace" if replace else "strict")
    else:
        strData = repr(data)

    if len(strData) > maxlen - len(ellipses):
        return strData[:maxlen]
    return strData


class FuzzingProtocol:
    """
    Common mixin-base class for fuzzing server and client protocols.
    """

    MAX_WIRE_LOG_DATA = 256

    def connectionMade(self):
        if not hasattr(self, "case"):
            self.case = None
        if not hasattr(self, "runCase"):
            self.runCase = None
        if not hasattr(self, "caseAgent"):
            self.caseAgent = None
        if not hasattr(self, "caseStarted"):
            self.caseStarted = None
        if not hasattr(self, "connectionWasOpen"):
            self.connectionWasOpen = None
        if not hasattr(self, "shutdownOnComplete"):
            self.shutdownOnComplete = None

        self.caseStart = 0
        self.caseEnd = 0

        ## wire log
        ##
        self.createWirelog = True
        self.wirelog = []

        ## stats for octets and frames
        ##
        self.createStats = True
        self.rxOctetStats = {}
        self.rxFrameStats = {}
        self.txOctetStats = {}
        self.txFrameStats = {}

        ## ability to shut down server once reports are generated
        self.shutdownOnComplete = False

    def connectionLost(self, reason: Failure = connectionDone):
        if self.runCase:
            self.runCase.onConnectionLost(self.failedByMe)
            self.caseEnd = time.time()

            caseResult = {
                "case": self.case,
                "id": self.factory.CaseSet.caseClasstoId(self.Case),
                "description": self.Case.DESCRIPTION,
                "expectation": self.Case.EXPECTATION,
                "agent": self.caseAgent,
                "started": self.caseStarted,
                "duration": int(
                    round(1000.0 * (self.caseEnd - self.caseStart))
                ),  # case execution time in ms
                "reportTime": self.runCase.reportTime,  # True/False switch to control report output of duration
                "reportCompressionRatio": self.runCase.reportCompressionRatio,
                "behavior": self.runCase.behavior,
                "behaviorClose": self.runCase.behaviorClose,
                "expected": self.runCase.expected,
                "expectedClose": self.runCase.expectedClose,
                "received": self.runCase.received,
                "result": self.runCase.result,
                "resultClose": self.runCase.resultClose,
                "wirelog": self.wirelog,
                "createWirelog": self.createWirelog,
                "closedByMe": self.closedByMe,
                "failedByMe": self.failedByMe,
                "droppedByMe": self.droppedByMe,
                "wasClean": self.wasClean,
                "wasNotCleanReason": self.wasNotCleanReason,
                "wasServerConnectionDropTimeout": self.wasServerConnectionDropTimeout,
                "wasOpenHandshakeTimeout": self.wasOpenHandshakeTimeout,
                "wasCloseHandshakeTimeout": self.wasCloseHandshakeTimeout,
                "localCloseCode": self.localCloseCode,
                "localCloseReason": self.localCloseReason,
                "remoteCloseCode": self.remoteCloseCode,
                "remoteCloseReason": self.remoteCloseReason,
                "isServer": self.factory.isServer,
                "createStats": self.createStats,
                "rxOctetStats": self.rxOctetStats,
                "rxFrameStats": self.rxFrameStats,
                "txOctetStats": self.txOctetStats,
                "txFrameStats": self.txFrameStats,
                "httpRequest": self.http_request_data
                if hasattr(self, "http_request_data")
                else "?",
                "httpResponse": self.http_response_data
                if hasattr(self, "http_response_data")
                else "?",
                "trafficStats": self.runCase.trafficStats.__json__()
                if self.runCase.trafficStats
                else None,
            }

            def cleanBin(e_old):
                e_new = []
                for t in e_old:
                    if t[0] == "message":
                        e_new.append((t[0], asciiLogData(t[1]), t[2]))
                    elif t[0] in ["ping", "pong"]:
                        e_new.append((t[0], asciiLogData(t[1])))
                    elif t[0] == "timeout":
                        e_new.append(t)
                    else:
                        print(t)
                        raise Exception("unknown part type %s" % t[0])
                return e_new

            for k in caseResult["expected"]:
                e_old = caseResult["expected"][k]
                caseResult["expected"][k] = cleanBin(e_old)

            caseResult["received"] = cleanBin(caseResult["received"])

            ## now log the case results
            ##
            self.factory.logCase(caseResult)

    def enableWirelog(self, enable):
        if enable != self.createWirelog:
            self.createWirelog = enable
            self.wirelog.append(("WLM", enable))

    def logRxOctets(self, data):
        if self.createStats:
            l = len(data)
            self.rxOctetStats[l] = self.rxOctetStats.get(l, 0) + 1
        if self.createWirelog:
            self.wirelog.append(("RO", (len(data), binLogData(data))))

    def logTxOctets(self, data, sync):
        if self.createStats:
            l = len(data)
            self.txOctetStats[l] = self.txOctetStats.get(l, 0) + 1
        if self.createWirelog:
            self.wirelog.append(("TO", (len(data), binLogData(data)), sync))

    def logRxFrame(self, frameHeader, payload):
        if self.createStats:
            self.rxFrameStats[frameHeader.opcode] = (
                self.rxFrameStats.get(frameHeader.opcode, 0) + 1
            )
        if self.createWirelog:
            # p = "".join(str(payload))
            val = b""
            if isinstance(payload, list):
                for p in payload:
                    val += p
            elif isinstance(payload, bytes):
                val = payload
            else:
                val = repr(payload).encode()

            aData = asciiLogData(val)
            self.wirelog.append(
                (
                    "RF",
                    (len(payload), aData),
                    frameHeader.opcode,
                    frameHeader.fin,
                    frameHeader.rsv,
                    frameHeader.mask is not None,
                    binascii.b2a_hex(frameHeader.mask) if frameHeader.mask else None,
                )
            )

    def logTxFrame(self, frameHeader, payload, repeatLength, chopsize, sync):
        if self.createStats:
            self.txFrameStats[frameHeader.opcode] = (
                self.txFrameStats.get(frameHeader.opcode, 0) + 1
            )
        if self.createWirelog:
            aData = asciiLogData(payload)
            self.wirelog.append(
                (
                    "TF",
                    (len(payload), aData),
                    frameHeader.opcode,
                    frameHeader.fin,
                    frameHeader.rsv,
                    binascii.b2a_hex(frameHeader.mask) if frameHeader.mask else None,
                    repeatLength,
                    chopsize,
                    sync,
                )
            )

    def executeContinueLater(self, fun, tag):
        if self.state != WebSocketProtocol.STATE_CLOSED:
            self.wirelog.append(("CTE", tag))
            fun()
        else:
            pass  # connection already gone

    def continueLater(self, delay, fun, tag=None):
        self.wirelog.append(("CT", delay, tag))
        reactor.callLater(delay, self.executeContinueLater, fun, tag)

    def failConnection(self):
        pass

    def killAfter(self, delay):
        self.wirelog.append(("KL", delay))
        reactor.callLater(delay, self.executeKillAfter)

    def closeAfter(self, delay):
        self.wirelog.append(("TI", delay))
        reactor.callLater(delay, self.executeCloseAfter)

    def executeKillAfter(self):
        if self.state != WebSocketProtocol.STATE_CLOSED:
            self.wirelog.append(("KLE",))
            self.failConnection()
        else:
            pass  # connection already gone

    def executeCloseAfter(self):
        if self.state != WebSocketProtocol.STATE_CLOSED:
            self.wirelog.append(("TIE",))
            self.sendClose()
        else:
            pass  # connection already gone

    def onOpen(self):
        self.connectionWasOpen = True

        if self.runCase:
            cc_id = self.factory.CaseSet.caseClasstoId(self.runCase.__class__)
            if self.factory.CaseSet.checkAgentCaseExclude(
                self.factory.specExcludeAgentCases, self.caseAgent, cc_id
            ):
                print(
                    f"Skipping test case {cc_id} for agent {self.caseAgent} by test configuration!"
                )
                self.runCase = None
                self.sendClose()
                return
            else:
                self.caseStart = time.time()
                self.runCase.onOpen()

            return

        path = getattr(self, "path", None)
        match path:
            case "/updateReports":
                self.factory.createReports()
                self.sendClose()
                if self.shutdownOnComplete:
                    print("Report generation complete; shutting down server.")
                    reactor.stop()
                else:
                    print("Report generation complete.")

            case "/getCaseCount":
                self.sendMessage(json.dumps(len(self.factory.specCases)))
                self.sendClose()

            case "/getCaseStatus":

                def sendResults(results):
                    self.sendMessage(json.dumps({"behavior": results["behavior"]}))
                    self.sendClose()

                self.factory.addResultListener(
                    self.caseAgent,
                    self.factory.CaseSet.caseClasstoId(self.Case),
                    sendResults,
                )

            case "/getCaseInfo":
                self.sendMessage(
                    json.dumps(
                        {
                            "id": self.factory.CaseSet.caseClasstoId(self.Case),
                            "description": self.factory.CaseSet.caseClassToPrettyDescription(
                                self.Case
                            ),
                        }
                    )
                )
                self.sendClose()

            case "/stopServer":
                print("Shutting down server.")
                reactor.stop()

            case _:
                print("No path named:", path)

    def onPong(self, payload):
        if self.runCase:
            self.runCase.onPong(payload)
        else:
            if self.debug:
                log.msg("Pong received: " + payload)

    def onClose(self, wasClean, code, reason):
        if self.runCase:
            self.runCase.onClose(wasClean, code, reason)
        else:
            if self.debug:
                log.msg("Close received: %s - %s" % (code, reason))

    def onMessage(self, payload: str, isBinary: bool):

        if self.runCase:
            self.runCase.onMessage(payload, isBinary)

        else:
            if isBinary:
                raise Exception("binary command message")

            else:
                try:
                    obj = json.loads(payload)
                except Exception:
                    raise Exception("could not parse command")

                ## send one frame as specified
                ##
                if obj[0] == "sendframe":
                    pl = obj[1].get("payload", "")
                    self.sendFrame(
                        opcode=obj[1]["opcode"],
                        payload=pl.encode("UTF-8"),
                        fin=obj[1].get("fin", True),
                        rsv=obj[1].get("rsv", 0),
                        mask=obj[1].get("mask", None),
                        payload_len=obj[1].get("payload_len", None),
                        chopsize=obj[1].get("chopsize", None),
                        sync=obj[1].get("sync", False),
                    )

                ## send multiple frames as specified
                ##
                elif obj[0] == "sendframes":
                    frames = obj[1]
                    for frame in frames:
                        pl = frame.get("payload", "")
                        self.sendFrame(
                            opcode=frame["opcode"],
                            payload=pl.encode("UTF-8"),
                            fin=frame.get("fin", True),
                            rsv=frame.get("rsv", 0),
                            mask=frame.get("mask", None),
                            payload_len=frame.get("payload_len", None),
                            chopsize=frame.get("chopsize", None),
                            sync=frame.get("sync", False),
                        )

                ## send close
                ##
                elif obj[0] == "close":
                    spec = obj[1]
                    self.sendClose(spec.get("code", None), spec.get("reason", None))

                ## echo argument
                ##
                elif obj[0] == "echo":
                    spec = obj[1]
                    self.sendFrame(
                        opcode=1,
                        payload=spec.get("payload", ""),
                        payload_len=spec.get("payload_len", None),
                    )

                else:
                    raise Exception("fuzzing peer received unknown command", obj[0])

    def sendMessage(
        self,
        payload,
        isBinary=False,
        fragmentSize=None,
        sync=False,
        doNotCompress=False,
    ):
        pass

    def sendClose(self, code=None, reason=None):
        pass


class FuzzingFactory:
    """
    Common mixin-base class for fuzzing server and client protocol factory.
    """

    MAX_CASE_PICKLE_LEN = 1000

    def __init__(self, outdir):
        self.repeatAgentRowPerSubcategory = True
        self.outdir = outdir
        self.agents = {}
        self.cases = {}
        self.resultListeners = {}

    def logCase(self, caseResults):
        """
        Called from FuzzingProtocol instances when case has been finished to store case results.
        """

        agent = caseResults["agent"]
        case = caseResults["id"]

        ## index by agent->case
        ##
        if not self.agents.get(agent):
            self.agents[agent] = {}
        self.agents[agent][case] = caseResults

        ## index by case->agent
        ##
        if not self.cases.get(case, None):
            self.cases[case] = {}
        self.cases[case][agent] = caseResults

        if (agent, case) in self.resultListeners:
            callback = self.resultListeners.pop((agent, case))
            callback(caseResults)

    def addResultListener(self, agent, caseId, resultsCallback):
        if agent in self.agents and caseId in self.agents[agent]:
            resultsCallback(self.agents[agent][caseId])
        else:
            self.resultListeners[(agent, caseId)] = resultsCallback

    def createReports(self, produceHtml=True, produceJson=True):
        """
        Create reports from all data stored for test cases which have been executed.
        """

        ## create output directory when non-existent
        ##
        if not os.path.exists(self.outdir):
            os.makedirs(self.outdir)

        ## create master report
        ##
        if produceHtml:
            self.createMasterReportHTML(self.outdir)
        if produceJson:
            self.createMasterReportJSON(self.outdir)

        ## create case detail reports
        ##
        for agentId in self.agents:
            for caseId in self.agents[agentId]:
                if produceHtml:
                    self.createAgentCaseReportHTML(agentId, caseId, self.outdir)
                if produceJson:
                    self.createAgentCaseReportJSON(agentId, caseId, self.outdir)

    def cleanForFilename(self, str):
        """
        Clean a string for use as filename.
        """
        s0 = "".join(
            [
                c if c in "abcdefghjiklmnopqrstuvwxyz0123456789" else " "
                for c in str.strip().lower()
            ]
        )
        s1 = s0.strip()
        s2 = s1.replace(" ", "_")
        return s2

    def makeAgentCaseReportFilename(self, agentId, caseId, ext):
        """
        Create filename for case detail report from agent and case.
        """
        c = caseId.replace(".", "_")
        return self.cleanForFilename(agentId) + "_case_" + c + "." + ext

    def limitString(self, s, limit, indicator=" ..."):
        ss = str(s)
        if len(ss) > limit - len(indicator):
            return ss[: limit - len(indicator)] + indicator
        else:
            return ss

    def createMasterReportJSON(self, outdir):
        """
        Create report master JSON file.

        :param outdir: Directory where to create file.
        :type outdir: str
        :returns: str -- Name of created file.
        """
        res = {}
        for agentId in self.agents:
            if not res.get(agentId):
                res[agentId] = {}
            for caseId in self.agents[agentId]:
                case = self.agents[agentId][caseId]
                c = {}
                report_filename = self.makeAgentCaseReportFilename(
                    agentId, caseId, ext="json"
                )
                c["behavior"] = case["behavior"]
                c["behaviorClose"] = case["behaviorClose"]
                c["remoteCloseCode"] = case["remoteCloseCode"]
                c["duration"] = case["duration"]
                c["reportfile"] = report_filename
                res[agentId][caseId] = c

        report_filename = "index.json"
        f = open(os.path.join(outdir, report_filename), "w")
        f.write(json.dumps(res, sort_keys=True, indent=3, separators=(",", ": ")))
        f.close()

    def createMasterReportHTML(self, outdir):
        """
        Create report master HTML file.

        :param outdir: Directory where to create file.
        :type outdir: str
        :returns: str -- Name of created file.
        """

        ## open report file in create / write-truncate mode
        ##
        report_filename = "index.html"
        f = open(os.path.join(outdir, report_filename), "w", encoding="utf-8")

        ## write HTML
        ##
        f.write("<!DOCTYPE html>\n")
        f.write("<html>\n")
        f.write("   <head>\n")
        f.write('      <meta charset="utf-8" />\n')
        f.write('      <style lang="css">%s</style>\n' % CSS_COMMON)
        f.write('      <style lang="css">%s</style>\n' % CSS_MASTER_REPORT)
        f.write(
            '      <script language="javascript">%s</script>\n'
            % JS_MASTER_REPORT
            % {"agents_cnt": len(self.agents.keys())}
        )
        f.write("   </head>\n")
        f.write("   <body>\n")
        f.write(
            '      <a href="#"><div id="toggle_button" class="unselectable" onclick="toggleClose();">Toggle Details</div></a>\n'
        )
        f.write('      <a name="top"></a>\n')
        f.write("      <br/>\n")

        ## top logos
        f.write(
            '      <center><a href="https://autobahn-testsuite.readthedocs.io/" title="Autobahn WebSocket Testsuite"><img src="https://autobahn-testsuite.readthedocs.io/en/latest/_static/img/ws_protocol_test_report.png"          border="0" width="820" height="46" alt="Autobahn WebSocket Testsuite Report"></img></a></center>\n'
        )
        f.write(
            '      <center><a href="https://autobahn-testsuite.readthedocs.io/"           title="Autobahn WebSocket">          <img src="https://autobahn-testsuite.readthedocs.io/en/latest/_static/img/ws_protocol_test_report_autobahn.png" border="0" width="300" height="68" alt="Autobahn WebSocket">                 </img></a></center>\n'
        )

        ## write report header
        ##
        f.write('      <div id="master_report_header" class="block">\n')
        f.write(
            '         <p id="intro">Summary report generated on %s (UTC) by <a href="%s">Autobahn WebSocket Testsuite</a> v%s/v%s.</p>\n'
            % (
                utcnow(),
                "https://autobahn-testsuite.readthedocs.io/",
                autobahntestsuite.version,
                autobahn.version,
            )
        )
        f.write("""
      <table id="case_outcome_desc">
         <tr>
            <td class="case_ok">Pass</td>
            <td class="outcome_desc">Test case was executed and passed successfully.</td>
         </tr>
         <tr>
            <td class="case_non_strict">Non-Strict</td>
            <td class="outcome_desc">Test case was executed and passed non-strictly.
            A non-strict behavior is one that does not adhere to a SHOULD-behavior as described in the protocol specification or
            a well-defined, canonical behavior that appears to be desirable but left open in the protocol specification.
            An implementation with non-strict behavior is still conformant to the protocol specification.</td>
         </tr>
         <tr>
            <td class="case_failed">Fail</td>
            <td class="outcome_desc">Test case was executed and failed. An implementation which fails a test case - other
            than a performance/limits related one - is non-conforming to a MUST-behavior as described in the protocol specification.</td>
         </tr>
         <tr>
            <td class="case_info">Info</td>
            <td class="outcome_desc">Informational test case which detects certain implementation behavior left unspecified by the spec
            but nevertheless potentially interesting to implementors.</td>
         </tr>
         <tr>
            <td class="case_missing">Missing</td>
            <td class="outcome_desc">Test case is missing, either because it was skipped via the test suite configuration
            or deactivated, i.e. because the implementation does not implement the tested feature or breaks during running
            the test case.</td>
         </tr>
      </table>
      """)
        f.write("      </div>\n")

        ## write big agent/case report table
        ##
        f.write('      <table id="agent_case_results">\n')

        ## sorted list of agents for which test cases where run
        ##
        agentList = sorted(self.agents.keys())
        # print("Agents:", agentList)

        ## create list ordered list of case Ids
        ##
        cl = []
        for c in Cases:
            t = self.CaseSet.caseClasstoIdTuple(c)
            cl.append((t, self.CaseSet.caseIdTupletoId(t)))
        cl = sorted(cl)
        caseList = []
        for c in cl:
            caseList.append(c[1])

        lastCaseCategory = None
        lastCaseSubCategory = None

        for caseId in caseList:
            caseCategoryIndex = caseId.split(".")[0]
            caseCategory = CaseCategories.get(caseCategoryIndex, "Misc")
            caseSubCategoryIndex = ".".join(caseId.split(".")[:2])
            caseSubCategory = CaseSubCategories.get(caseSubCategoryIndex, None)

            ## Category/Agents row
            ##
            if caseCategory != lastCaseCategory or (
                self.repeatAgentRowPerSubcategory
                and caseSubCategory != lastCaseSubCategory
            ):
                f.write('         <tr class="case_category_row">\n')
                f.write(
                    '            <td class="case_category">%s %s</td>\n'
                    % (caseCategoryIndex, caseCategory)
                )
                for agentId in agentList:
                    f.write(
                        '            <td class="agent close_flex" colspan="2">%s</td>\n'
                        % agentId
                    )
                f.write("         </tr>\n")
                lastCaseCategory = caseCategory
                lastCaseSubCategory = None

            ## Subcategory row
            ##
            if caseSubCategory != lastCaseSubCategory:
                f.write('         <tr class="case_subcategory_row">\n')
                f.write(
                    '            <td class="case_subcategory" colspan="%d">%s %s</td>\n'
                    % (len(agentList) * 2 + 1, caseSubCategoryIndex, caseSubCategory)
                )
                f.write("         </tr>\n")
                lastCaseSubCategory = caseSubCategory

            ## Cases row
            ##
            f.write('         <tr class="agent_case_result_row">\n')
            f.write(
                '            <td class="case"><a href="#case_desc_%s">Case %s</a></td>\n'
                % (caseId.replace(".", "_"), caseId)
            )

            ## Case results
            ##
            for agentId in agentList:
                if self.agents[agentId].get(caseId):
                    case = self.agents[agentId][caseId]

                    if case["behavior"] != Case.UNIMPLEMENTED:
                        agent_case_report_file = self.makeAgentCaseReportFilename(
                            agentId, caseId, ext="html"
                        )

                        if case["behavior"] == Case.OK:
                            td_text = "Pass"
                            td_class = "case_ok"
                        elif case["behavior"] == Case.NON_STRICT:
                            td_text = "Non-Strict"
                            td_class = "case_non_strict"
                        elif case["behavior"] == Case.NO_CLOSE:
                            td_text = "No Close"
                            td_class = "case_no_close"
                        elif case["behavior"] == Case.INFORMATIONAL:
                            td_text = "Info"
                            td_class = "case_info"
                        else:
                            td_text = "Fail"
                            td_class = "case_failed"

                        if case["behaviorClose"] == Case.OK:
                            ctd_text = "%s" % str(case["remoteCloseCode"])
                            ctd_class = "case_ok"
                        elif case["behaviorClose"] == Case.FAILED_BY_CLIENT:
                            ctd_text = "%s" % str(case["remoteCloseCode"])
                            ctd_class = "case_almost"
                        elif case["behaviorClose"] == Case.WRONG_CODE:
                            ctd_text = "%s" % str(case["remoteCloseCode"])
                            ctd_class = "case_non_strict"
                        elif case["behaviorClose"] == Case.UNCLEAN:
                            ctd_text = "Unclean"
                            ctd_class = "case_failed"
                        elif case["behaviorClose"] == Case.INFORMATIONAL:
                            ctd_text = "%s" % str(case["remoteCloseCode"])
                            ctd_class = "case_info"
                        else:
                            ctd_text = "Fail"
                            ctd_class = "case_failed"

                        detail = ""

                        if case["reportTime"]:
                            detail += "%d ms" % case["duration"]

                        if (
                            case["reportCompressionRatio"]
                            and case["trafficStats"] is not None
                        ):
                            crIn = case["trafficStats"]["incomingCompressionRatio"]
                            crOut = case["trafficStats"]["outgoingCompressionRatio"]
                            detail += " [%s/%s]" % (
                                "%.3f" % crIn if crIn is not None else "-",
                                "%.3f" % crOut if crOut is not None else "-",
                            )

                        if detail != "":
                            f.write(
                                '            <td class="%s"><a href="%s">%s</a><br/><span class="case_duration">%s</span></td><td class="close close_hide %s"><span class="close_code">%s</span></td>\n'
                                % (
                                    td_class,
                                    agent_case_report_file,
                                    td_text,
                                    detail,
                                    ctd_class,
                                    ctd_text,
                                )
                            )
                        else:
                            f.write(
                                '            <td class="%s"><a href="%s">%s</a></td><td class="close close_hide %s"><span class="close_code">%s</span></td>\n'
                                % (
                                    td_class,
                                    agent_case_report_file,
                                    td_text,
                                    ctd_class,
                                    ctd_text,
                                )
                            )

                    else:
                        f.write(
                            '            <td class="case_unimplemented close_flex" colspan="2">Unimplemented</td>\n'
                        )

                else:
                    f.write(
                        '            <td class="case_missing close_flex" colspan="2">Missing</td>\n'
                    )

            f.write("         </tr>\n")

        f.write("      </table>\n")
        f.write("      <br/><hr/>\n")

        ## Case descriptions
        ##
        f.write('      <div id="test_case_descriptions">\n')
        for caseId in caseList:
            CCase = self.CaseSet.CasesById[caseId]
            f.write("      <br/>\n")
            f.write(f'      <a name="case_desc_{caseId.replace(".", "_")}"></a>\n')
            f.write(f"      <h2>Case {caseId}</h2>\n")
            f.write('      <a class="up" href="#top">Up</a>\n')
            f.write(
                f'      <p class="case_text_block case_desc"><b>Case Description</b><br/><br/>{CCase.DESCRIPTION}</p>\n'
            )
            f.write(
                f'      <p class="case_text_block case_expect"><b>Case Expectation</b><br/><br/>{CCase.EXPECTATION}</p>\n'
            )
        f.write("      </div>\n")
        f.write("      <br/><hr/>\n")

        ## end of HTML
        ##
        f.write("   </body>\n")
        f.write("</html>\n")

        ## close created HTML file and return filename
        ##
        f.close()
        return report_filename

    def createAgentCaseReportJSON(self, agentId, caseId, outdir):
        """
        Create case detail report JSON file.

        :param agentId: ID of agent for which to generate report.
        :type agentId: str
        :param caseId: ID of case for which to generate report.
        :type caseId: str
        :param outdir: Directory where to create file.
        :type outdir: str
        :returns: str -- Name of created file.
        """

        if not self.agents.get(agentId):
            raise Exception("no test data stored for agent %s" % agentId)

        if not self.agents[agentId].get(caseId):
            raise Exception(
                "no test data stored for case %s with agent %s" % (caseId, agentId)
            )

        ## get case to generate report for
        ##
        case = self.agents[agentId][caseId]

        ## open report file in create / write-truncate mode
        ##
        report_filename = self.makeAgentCaseReportFilename(agentId, caseId, ext="json")
        f = open(os.path.join(outdir, report_filename), "w")
        f.write(
            json.dumps(
                case, default=convert, sort_keys=True, indent=3, separators=(",", ": ")
            )
        )
        f.close()

    def createAgentCaseReportHTML(self, agentId, caseId, outdir):
        """
        Create case detail report HTML file.

        :param agentId: ID of agent for which to generate report.
        :type agentId: str
        :param caseId: ID of case for which to generate report.
        :type caseId: str
        :param outdir: Directory where to create file.
        :type outdir: str
        :returns: str -- Name of created file.
        """

        if not self.agents.get(agentId):
            raise Exception("no test data stored for agent %s" % agentId)

        if not self.agents[agentId].get(caseId):
            raise Exception(
                "no test data stored for case %s with agent %s" % (caseId, agentId)
            )

        ## get case to generate report for
        ##
        case = self.agents[agentId][caseId]

        ## open report file in create / write-truncate mode
        ##
        report_filename = self.makeAgentCaseReportFilename(agentId, caseId, ext="html")
        f = open(os.path.join(outdir, report_filename), "w")

        ## write HTML
        ##
        f.write("<!DOCTYPE html>\n")
        f.write("<html>\n")
        f.write("   <head>\n")
        f.write('      <meta charset="utf-8" />\n')
        f.write('      <style lang="css">%s</style>\n' % CSS_COMMON)
        f.write('      <style lang="css">%s</style>\n' % CSS_DETAIL_REPORT)
        f.write("   </head>\n")
        f.write("   <body>\n")
        f.write('      <a name="top"></a>\n')
        f.write("      <br/>\n")

        ## top logos
        f.write(
            '      <center><a href="https://autobahn-testsuite.readthedocs.io/" title="Autobahn WebSocket Testsuite"><img src="https://autobahn-testsuite.readthedocs.io/en/latest/_static/img/ws_protocol_test_report.png"          border="0" width="820" height="46" alt="Autobahn WebSocket Testsuite Report"></img></a></center>\n'
        )
        f.write(
            '      <center><a href="https://autobahn-testsuite.readthedocs.io/"           title="Autobahn WebSocket">          <img src="https://autobahn-testsuite.readthedocs.io/en/latest/_static/img/ws_protocol_test_report_autobahn.png" border="0" width="300" height="68" alt="Autobahn WebSocket">                 </img></a></center>\n'
        )
        f.write("      <br/>\n")

        ## Case Summary
        ##
        if case["behavior"] == Case.OK:
            style = "case_ok"
            text = "Pass"
        elif case["behavior"] == Case.NON_STRICT:
            style = "case_non_strict"
            text = "Non-Strict"
        elif case["behavior"] == Case.INFORMATIONAL:
            style = "case_info"
            text = "Informational"
        else:
            style = "case_failed"
            text = "Fail"
        f.write(
            '      <p class="case %s">%s - <span style="font-size: 1.3em;"><b>Case %s</b></span> : %s - <span style="font-size: 0.9em;"><b>%d</b> ms @ %s</a></p>\n'
            % (style, case["agent"], caseId, text, case["duration"], case["started"])
        )

        ## Case Description, Expectation, Outcome, Case Closing Behavior
        ##
        f.write(
            '      <p class="case_text_block case_desc"><b>Case Description</b><br/><br/>%s</p>\n'
            % case["description"]
        )
        f.write(
            '      <p class="case_text_block case_expect"><b>Case Expectation</b><br/><br/>%s</p>\n'
            % case["expectation"]
        )
        f.write(
            """
      <p class="case_text_block case_outcome">
         <b>Case Outcome</b><br/><br/>%s<br/><br/>
         <i>Expected:</i><br/><span class="case_pickle">%s</span><br/><br/>
         <i>Observed:</i><br><span class="case_pickle">%s</span>
      </p>\n"""
            % (
                case.get("result", ""),
                self.limitString(
                    case.get("expected", ""), FuzzingFactory.MAX_CASE_PICKLE_LEN
                ),
                self.limitString(
                    case.get("received", ""), FuzzingFactory.MAX_CASE_PICKLE_LEN
                ),
            )
        )
        f.write(
            '      <p class="case_text_block case_closing_beh"><b>Case Closing Behavior</b><br/><br/>%s (%s)</p>\n'
            % (case.get("resultClose", ""), case.get("behaviorClose", ""))
        )
        f.write("      <br/><hr/>\n")

        ## Opening Handshake
        ##
        f.write("      <h2>Opening Handshake</h2>\n")
        f.write('      <pre class="http_dump">%s</pre>\n' % case["httpRequest"].strip())
        f.write(
            '      <pre class="http_dump">%s</pre>\n' % case["httpResponse"].strip()
        )
        f.write("      <br/><hr/>\n")

        ## Closing Behavior
        ##
        cbv = [
            (
                "isServer",
                "True, iff I (the fuzzer) am a server, and the peer is a client.",
            ),
            (
                "closedByMe",
                "True, iff I have initiated closing handshake (that is, did send close first).",
            ),
            (
                "failedByMe",
                "True, iff I have failed the WS connection (i.e. due to protocol error). Failing can be either by initiating closing handshake or brutal drop TCP.",
            ),
            ("droppedByMe", "True, iff I dropped the TCP connection."),
            (
                "wasClean",
                "True, iff full WebSocket closing handshake was performed (close frame sent and received) _and_ the server dropped the TCP (which is its responsibility).",
            ),
            ("wasNotCleanReason", "When wasClean == False, the reason what happened."),
            (
                "wasServerConnectionDropTimeout",
                "When we are a client, and we expected the server to drop the TCP, but that didn't happen in time, this gets True.",
            ),
            (
                "wasOpenHandshakeTimeout",
                "When performing the opening handshake, but the peer did not finish in time, this gets True.",
            ),
            (
                "wasCloseHandshakeTimeout",
                "When we initiated a closing handshake, but the peer did not respond in time, this gets True.",
            ),
            ("localCloseCode", "The close code I sent in close frame (if any)."),
            ("localCloseReason", "The close reason I sent in close frame (if any)."),
            (
                "remoteCloseCode",
                "The close code the peer sent me in close frame (if any).",
            ),
            (
                "remoteCloseReason",
                "The close reason the peer sent me in close frame (if any).",
            ),
        ]
        f.write("      <h2>Closing Behavior</h2>\n")
        f.write("      <table>\n")
        f.write(
            '         <tr class="stats_header"><td>Key</td><td class="left">Value</td><td class="left">Description</td></tr>\n'
        )
        for c in cbv:
            f.write(
                (
                    '         <tr class="stats_row"><td>%s</td><td class="left">%s</td><td class="left">%s</td></tr>\n'
                    % (c[0], case[c[0]], c[1])
                )
            )
        f.write("      </table>")
        f.write("      <br/><hr/>\n")

        ## Wire Statistics
        ##
        f.write("      <h2>Wire Statistics</h2>\n")
        if not case["createStats"]:
            f.write(
                '      <p style="margin-left: 40px; color: #f00;"><i>Statistics for octets/frames disabled!</i></p>\n'
            )
        else:
            ## octet stats
            ##
            for statdef in [
                ("Received", case["rxOctetStats"]),
                ("Transmitted", case["txOctetStats"]),
            ]:
                f.write("      <h3>Octets %s by Chop Size</h3>\n" % statdef[0])
                f.write("      <table>\n")
                stats = statdef[1]
                total_cnt = 0
                total_octets = 0
                f.write(
                    '         <tr class="stats_header"><td>Chop Size</td><td>Count</td><td>Octets</td></tr>\n'
                )
                for s in sorted(stats.keys()):
                    f.write(
                        '         <tr class="stats_row"><td>%d</td><td>%d</td><td>%d</td></tr>\n'
                        % (s, stats[s], s * stats[s])
                    )
                    total_cnt += stats[s]
                    total_octets += s * stats[s]
                f.write(
                    '         <tr class="stats_total"><td>Total</td><td>%d</td><td>%d</td></tr>\n'
                    % (total_cnt, total_octets)
                )
                f.write("      </table>\n")

            ## frame stats
            ##
            for statdef in [
                ("Received", case["rxFrameStats"]),
                ("Transmitted", case["txFrameStats"]),
            ]:
                f.write("      <h3>Frames %s by Opcode</h3>\n" % statdef[0])
                f.write("      <table>\n")
                stats = statdef[1]
                total_cnt = 0
                f.write(
                    '         <tr class="stats_header"><td>Opcode</td><td>Count</td></tr>\n'
                )
                for s in sorted(stats.keys()):
                    f.write(
                        '         <tr class="stats_row"><td>%d</td><td>%d</td></tr>\n'
                        % (s, stats[s])
                    )
                    total_cnt += stats[s]
                f.write(
                    '         <tr class="stats_total"><td>Total</td><td>%d</td></tr>\n'
                    % (total_cnt)
                )
                f.write("      </table>\n")
        f.write("      <br/><hr/>\n")

        ## Wire Log
        ##
        f.write("      <h2>Wire Log</h2>\n")
        if not case["createWirelog"]:
            f.write(
                '      <p style="margin-left: 40px; color: #f00;"><i>Wire log after handshake disabled!</i></p>\n'
            )

        f.write('      <div id="wirelog">\n')
        wl = case["wirelog"]
        i = 0
        for t in wl:
            if t[0] == "RO":
                prefix = "RX OCTETS"
                css_class = "wirelog_rx_octets"

            elif t[0] == "TO":
                prefix = "TX OCTETS"
                if t[2]:
                    css_class = "wirelog_tx_octets_sync"
                else:
                    css_class = "wirelog_tx_octets"

            elif t[0] == "RF":
                prefix = "RX FRAME "
                css_class = "wirelog_rx_frame"

            elif t[0] == "TF":
                prefix = "TX FRAME "
                if t[8] or t[7] is not None:
                    css_class = "wirelog_tx_frame_sync"
                else:
                    css_class = "wirelog_tx_frame"

            elif t[0] in ["CT", "CTE", "KL", "KLE", "TI", "TIE", "WLM"]:
                pass

            else:
                raise Exception(
                    "logic error (unrecognized wire log row type %s - row %s)"
                    % (t[0], str(t))
                )

            if t[0] in ["RO", "TO", "RF", "TF"]:
                payloadLen = t[1][0]
                if isinstance(t[1][1], bytes):
                    lines = textwrap.wrap(
                        t[1][1].decode("utf-8", errors="replace"), 100
                    )
                else:
                    lines = textwrap.wrap(t[1][1], 100)

                if t[0] in ["RO", "TO"]:
                    if len(lines) > 0:
                        f.write(
                            '         <pre class="%s">%03d %s: %s</pre>\n'
                            % (css_class, i, prefix, lines[0])
                        )
                        for ll in lines[1:]:
                            f.write(
                                '         <pre class="%s">%s%s</pre>\n'
                                % (css_class, (2 + 4 + len(prefix)) * " ", ll)
                            )
                else:
                    if t[0] == "RF":
                        if t[6]:
                            mmask = binascii.b2a_hex(t[6])
                        else:
                            mmask = str(t[6])
                        f.write(
                            '         <pre class="%s">%03d %s: OPCODE=%s, FIN=%s, RSV=%s, PAYLOAD-LEN=%s, MASKED=%s, MASK=%s</pre>\n'
                            % (
                                css_class,
                                i,
                                prefix,
                                str(t[2]),
                                str(t[3]),
                                str(t[4]),
                                payloadLen,
                                str(t[5]),
                                mmask,
                            )
                        )
                    elif t[0] == "TF":
                        f.write(
                            '         <pre class="%s">%03d %s: OPCODE=%s, FIN=%s, RSV=%s, PAYLOAD-LEN=%s, MASK=%s, PAYLOAD-REPEAT-LEN=%s, CHOPSIZE=%s, SYNC=%s</pre>\n'
                            % (
                                css_class,
                                i,
                                prefix,
                                str(t[2]),
                                str(t[3]),
                                str(t[4]),
                                payloadLen,
                                str(t[5]),
                                str(t[6]),
                                str(t[7]),
                                str(t[8]),
                            )
                        )
                    else:
                        raise Exception("logic error")
                    for ll in lines:
                        f.write(
                            '         <pre class="%s">%s%s</pre>\n'
                            % (
                                css_class,
                                (2 + 4 + len(prefix)) * " ",
                                ll.encode("utf8"),
                            )
                        )

            elif t[0] == "WLM":
                if t[1]:
                    f.write(
                        '         <pre class="wirelog_delay">%03d WIRELOG ENABLED</pre>\n'
                        % (i)
                    )
                else:
                    f.write(
                        '         <pre class="wirelog_delay">%03d WIRELOG DISABLED</pre>\n'
                        % (i)
                    )

            elif t[0] == "CT":
                f.write(
                    '         <pre class="wirelog_delay">%03d DELAY %f sec for TAG %s</pre>\n'
                    % (i, t[1], t[2])
                )

            elif t[0] == "CTE":
                f.write(
                    '         <pre class="wirelog_delay">%03d DELAY TIMEOUT on TAG %s</pre>\n'
                    % (i, t[1])
                )

            elif t[0] == "KL":
                f.write(
                    '         <pre class="wirelog_kill_after">%03d FAIL CONNECTION AFTER %f sec</pre>\n'
                    % (i, t[1])
                )

            elif t[0] == "KLE":
                f.write(
                    '         <pre class="wirelog_kill_after">%03d FAILING CONNECTION</pre>\n'
                    % (i)
                )

            elif t[0] == "TI":
                f.write(
                    '         <pre class="wirelog_kill_after">%03d CLOSE CONNECTION AFTER %f sec</pre>\n'
                    % (i, t[1])
                )

            elif t[0] == "TIE":
                f.write(
                    '         <pre class="wirelog_kill_after">%03d CLOSING CONNECTION</pre>\n'
                    % (i)
                )

            else:
                raise Exception(
                    "logic error (unrecognized wire log row type %s - row %s)"
                    % (t[0], str(t))
                )

            i += 1

        if case["droppedByMe"]:
            f.write(
                '         <pre class="wirelog_tcp_closed_by_me">%03d TCP DROPPED BY ME</pre>\n'
                % i
            )
        else:
            f.write(
                '         <pre class="wirelog_tcp_closed_by_peer">%03d TCP DROPPED BY PEER</pre>\n'
                % i
            )
        f.write("      </div>\n")
        f.write("      <br/><hr/>\n")

        ## end of HTML
        ##
        f.write("   </body>\n")
        f.write("</html>\n")

        ## close created HTML file and return filename
        ##
        f.close()
        return report_filename


class FuzzingServerProtocol(FuzzingProtocol, WebSocketServerProtocol):
    def connectionMade(self):
        WebSocketServerProtocol.connectionMade(self)
        FuzzingProtocol.connectionMade(self)

    def connectionLost(self, reason: Failure = connectionDone):
        WebSocketServerProtocol.connectionLost(self, reason)
        FuzzingProtocol.connectionLost(self, reason)

    def onConnect(self, request):
        if self.debug:
            log.msg(
                "connection received from %s speaking WebSocket protocol %d - upgrade request for host '%s', path '%s', params %s, origin '%s', protocols %s, headers %s"
                % (
                    connectionRequest.peer,
                    connectionRequest.version,
                    connectionRequest.host,
                    connectionRequest.path,
                    str(connectionRequest.params),
                    connectionRequest.origin,
                    str(connectionRequest.protocols),
                    str(connectionRequest.headers),
                )
            )

        if connectionRequest.params.get("agent"):
            if len(connectionRequest.params["agent"]) > 1:
                raise Exception("multiple agents specified")
            self.caseAgent = connectionRequest.params["agent"][0]
        else:
            # raise Exception("no agent specified")
            self.caseAgent = None

        if connectionRequest.params.get("casetuple"):
            if len(connectionRequest.params["casetuple"]) > 1:
                raise Exception("multiple test cases specified")
            try:
                casetuple = connectionRequest.params["casetuple"][0]
                casetuple = casetuple.strip()
                self.case = self.factory.specCases.index(casetuple) + 1
            except Exception:
                raise Exception(
                    "invalid test case tuple %s"
                    % connectionRequest.params["casetuple"][0]
                )

        if connectionRequest.params.get("case"):
            if len(connectionRequest.params["case"]) > 1:
                raise Exception("multiple test cases specified")
            try:
                self.case = int(connectionRequest.params["case"][0])
            except Exception:
                raise Exception(
                    "invalid test case ID %s" % connectionRequest.params["case"][0]
                )

        if connectionRequest.params.get("shutdownOnComplete"):
            if len(connectionRequest.params["shutdownOnComplete"]) > 1:
                raise Exception(
                    "shutdownOnComplete only supports a single Boolean value"
                )
            try:
                val = connectionRequest.params["shutdownOnComplete"][0]
                if val.lower() in ["true", "yes"]:
                    self.shutdownOnComplete = True
                else:
                    self.shutdownOnComplete = False
            except Exception:
                raise Exception(
                    "invalid shutdownOnComplete parameter %s"
                    % connectionRequest.params["shutdownOnComplete"][0]
                )

        if self.case:
            if self.case >= 1 and self.case <= len(self.factory.specCases):
                self.Case = self.factory.CaseSet.CasesById[
                    self.factory.specCases[self.case - 1]
                ]
                if connectionRequest.path == "/runCase":
                    self.runCase = self.Case(self)
            else:
                raise Exception("case %s not found" % self.case)

        match connectionRequest.path:
            case "/runCase":
                if not self.runCase:
                    raise Exception("need case to run")
                if not self.caseAgent:
                    raise Exception("need agent to run case")
                self.caseStarted = utcnow()
                print(
                    f"Running test case ID {self.factory.CaseSet.caseClasstoId(self.Case)} for agent {self.caseAgent} from peer {connectionRequest.peer}"
                )

            case "/updateReports":
                if not self.caseAgent:
                    raise Exception("need agent to update reports for")
                print(f"Updating reports, requested by peer {connectionRequest.peer}")

            case "/getCaseInfo":
                if not self.Case:
                    raise Exception("need case to get info")

            case "/getCaseStatus":
                if not self.Case:
                    raise Exception("need case to get status")
                if not self.caseAgent:
                    raise Exception("need agent to get status")

            case "/getCaseCount":
                pass

            case _:
                print(
                    "Entering direct command mode for peer %s" % connectionRequest.peer
                )

        self.path = connectionRequest.path

        return None


class FuzzingServerFactory(FuzzingFactory, WebSocketServerFactory):
    protocol = FuzzingServerProtocol

    def __init__(self, spec, debug=False):

        # WebSocketServerFactory.__init__(self, debug=debug, debugCodePaths=debug)
        WebSocketServerFactory.__init__(self)
        FuzzingFactory.__init__(self, spec.get("outdir", "./reports/clients/"))

        # needed for wire log / stats
        self.logOctets = True
        self.logFrames = True

        ## WebSocket session parameters
        ##
        self.setSessionParameters(
            url=spec["url"],
            protocols=spec.get("protocols", []),
            server="AutobahnTestSuite/%s-%s"
            % (autobahntestsuite.version, autobahn.version),
        )

        ## WebSocket protocol options
        ##
        self.setProtocolOptions(failByDrop=False)  # spec conformance
        self.setProtocolOptions(**spec.get("options", {}))

        self.spec = spec

        self.CaseSet = CaseSet(
            CaseSetname, CaseBasename, Cases, CaseCategories, CaseSubCategories
        )

        self.specCases = self.CaseSet.parseSpecCases(self.spec)
        self.specExcludeAgentCases = self.CaseSet.parseExcludeAgentCases(self.spec)
        print(
            "Autobahn WebSocket %s/%s Fuzzing Server (Port %d%s)"
            % (
                autobahntestsuite.version,
                autobahn.version,
                self.port,
                " TLS" if self.isSecure else "",
            )
        )
        print(
            "Ok, will run %d test cases for any clients connecting"
            % len(self.specCases)
        )
        print("Cases = %s" % str(self.specCases))


class FuzzingClientProtocol(FuzzingProtocol, WebSocketClientProtocol):
    def connectionMade(self):
        FuzzingProtocol.connectionMade(self)
        WebSocketClientProtocol.connectionMade(self)
        self.caseStarted = utcnow()

    def onConnect(self, response):
        if not self.caseAgent:
            self.caseAgent = response.headers.get("server", "UnknownServer")
        print(
            f"Running test case ID {self.factory.CaseSet.caseClasstoId(self.Case)} for agent {self.caseAgent} from peer {self.peer}"
        )

    # addition
    def onClose(self, wasClean, code, reason):
        return FuzzingProtocol.onClose(self, wasClean, code, reason)

    def onOpen(self):
        return FuzzingProtocol.onOpen(self)

    def onMessage(self, payload: str, isBinary: bool):
        return FuzzingProtocol.onMessage(self, payload, isBinary)

    def onPong(self, payload):
        return FuzzingProtocol.onPong(self, payload)

    def sendMessage(
        self,
        payload,
        isBinary=False,
        fragmentSize=None,
        sync=False,
        doNotCompress=False,
    ):
        return WebSocketClientProtocol.sendMessage(
            self, payload, isBinary, fragmentSize, sync, doNotCompress
        )

    def sendClose(self, code=None, reason=None):
        WebSocketClientProtocol.sendClose(self)

    def failConnection(self):
        self._fail_connection()


class FuzzingClientFactory(FuzzingFactory, WebSocketClientFactory):
    protocol = FuzzingClientProtocol

    def __init__(self, spec: WsTestOptions, debug=False):

        WebSocketClientFactory.__init__(self)
        FuzzingFactory.__init__(self, spec.get("outdir", "./reports/servers/"))

        # needed for wire log / stats
        self.logOctets = True
        self.logFrames = True

        self.spec = spec

        self.CaseSet = CaseSet(
            CaseSetname, CaseBasename, Cases, CaseCategories, CaseSubCategories
        )

        self.specCases = self.CaseSet.parseSpecCases(self.spec)
        self.specExcludeAgentCases = self.CaseSet.parseExcludeAgentCases(self.spec)
        print(
            f"Autobahn Fuzzing WebSocket Client (Autobahn Testsuite Version {autobahntestsuite.version} / Autobahn Version {autobahn.version})"
        )
        print(
            f"Ok, will run {len(self.specCases)} test cases against {len(spec['servers'])} servers"
        )
        print("Cases = ", str(self.specCases))
        print("Servers = ", str([x["url"] for x in spec["servers"]]))

        self.currServer = -1
        if self.nextServer() and self.nextCase():
            connectWS(self, contextFactory=self.contextFactory)

    def buildProtocol(self, addr):
        proto = FuzzingClientProtocol()
        proto.factory = self

        proto.caseAgent = self.agent
        proto.case = self.currentCaseIndex
        proto.Case = Cases[self.currentCaseIndex - 1]
        proto.runCase = proto.Case(proto)

        return proto

    def nextServer(self):
        self.currSpecCase = -1
        self.currServer += 1
        if self.currServer >= len(self.spec["servers"]):
            return False
        ## run tests for next server
        ##
        server = self.spec["servers"][self.currServer]

        ## agent (=server) string for reports
        ##
        self.agent = server.get("agent")

        ## Hostname to send in TLS handshake for SNI support
        ##
        hostname = server.get("hostname")
        if hostname:
            self.contextFactory = ssl.optionsForClientTLS(hostname)
        else:
            self.contextFactory = ssl.ClientContextFactory()

        ## WebSocket session parameters
        ##
        self.setSessionParameters(
            url=server["url"],
            origin=server.get("origin", None),
            protocols=server.get("protocols", []),
            useragent="AutobahnTestSuite/%s-%s"
            % (autobahntestsuite.version, autobahn.version),
        )

        ## WebSocket protocol options
        ##
        self.resetProtocolOptions()  # reset to defaults
        self.setProtocolOptions(failByDrop=False)  # spec conformance
        self.setProtocolOptions(
            **self.spec.get("options", {})
        )  # set spec global options
        self.setProtocolOptions(
            **server.get("options", {})
        )  # set server specific options
        return True

    def nextCase(self):
        self.currSpecCase += 1
        if self.currSpecCase >= len(self.specCases):
            return False
        self.currentCaseId = self.specCases[self.currSpecCase]
        self.currentCaseIndex = self.CaseSet.CasesIndices[self.currentCaseId]
        return True

    def clientConnectionLost(self, connector, reason):
        if self.nextCase():
            connector.connect()
        else:
            if self.nextServer():
                if self.nextCase():
                    connectWS(self)
            else:
                self.createReports()
                reactor.stop()

    def clientConnectionFailed(self, connector, reason):
        print(
            f"Connection to {self.spec['servers'][self.currServer]['url']} failed ({reason.getErrorMessage()})"
        )
        if self.nextServer():
            if self.nextCase():
                connectWS(self)
        else:
            self.createReports()
            reactor.stop()


def startClient(spec, debug=False):
    log.startLogging(sys.stdout)
    _ = FuzzingClientFactory(spec, debug)
    # no connectWS done here, since this is done within
    # FuzzingClientFactory automatically to orchestrate tests
    return True


def startServer(spec, webport, sslKey=None, sslCert=None, debug=False):
    ## use TLS server key/cert from spec, but allow overriding
    ## from cmd line
    if not sslKey:
        sslKey = spec.get("key", None)
    if not sslCert:
        sslCert = spec.get("cert", None)

    factory = FuzzingServerFactory(spec, debug)

    if sslKey and sslCert:
        sslContext = ssl.DefaultOpenSSLContextFactory(sslKey, sslCert)
    else:
        sslContext = None

    listenWS(factory, sslContext)

    if webport:
        with importlib.resources.as_file(
            importlib.resources.files("autobahntestsuite").joinpath("web/fuzzingserver")
        ) as file:
            webdir = File(str(file))
            # webdir = File(
            #     pkg_resources.resource_filename("autobahntestsuite", "web/fuzzingserver")
            # )
            curdir = File(".")
            webdir.putChild("cwd", curdir)
            web = Site(webdir)
            if factory.isSecure:
                reactor.listenSSL(webport, web, sslContext)
            else:
                reactor.listenTCP(webport, web)

    return True
