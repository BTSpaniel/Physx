#!/usr/bin/env python3
"""Run named unittest cases and write JSON/JUnit evidence using only stdlib.

Default execution clears PR_RUST_LIB so stale native builds cannot be picked up.
Native Rust tests remain explicitly skipped; build.py test/native compiles first.
"""
from __future__ import annotations
import argparse
import contextlib
import io
import json
import os
import sys
import time
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

class DetailedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.rows=[];self.started={};self.outcomes={}
    def startTest(self,test):self.started[id(test)]=time.perf_counter();super().startTest(test)
    def mark(self,test,status,message=''):
        key=id(test)
        old=self.outcomes.get(key)
        if old and old[0] in ('FAIL','ERROR'):return
        self.outcomes[key]=(status,message)
    def addSuccess(self,test):super().addSuccess(test);self.mark(test,'PASS')
    def addFailure(self,test,err):super().addFailure(test,err);self.mark(test,'FAIL',self._exc_info_to_string(err,test))
    def addError(self,test,err):super().addError(test,err);self.mark(test,'ERROR',self._exc_info_to_string(err,test))
    def addSkip(self,test,reason):super().addSkip(test,reason);self.mark(test,'NOT_RUN',reason)
    def addExpectedFailure(self,test,err):super().addExpectedFailure(test,err);self.mark(test,'XFAIL',self._exc_info_to_string(err,test))
    def addUnexpectedSuccess(self,test):super().addUnexpectedSuccess(test);self.mark(test,'FAIL','Unexpected success')
    def addSubTest(self,test,subtest,err):
        super().addSubTest(test,subtest,err)
        if err is not None:self.mark(test,'FAIL',str(subtest)+'\n'+self._exc_info_to_string(err,test))
    def stopTest(self,test):
        status,message=self.outcomes.pop(id(test),('ERROR','Test did not produce an outcome'))
        self.rows.append({'name':test.id(),'status':status,'seconds':time.perf_counter()-self.started.pop(id(test)),'message':message})
        super().stopTest(test)

def junit(rows: list[dict], path: Path) -> None:
    root=ET.Element('testsuite',name='PhysX tooling (not physics)',tests=str(len(rows)),
       failures=str(sum(r['status']=='FAIL' for r in rows)),errors=str(sum(r['status']=='ERROR' for r in rows)),
       skipped=str(sum(r['status'] in ('NOT_RUN','XFAIL') for r in rows)))
    for row in rows:
        case=ET.SubElement(root,'testcase',name=row['name'],time=f"{row['seconds']:.6f}")
        if row['status']!='PASS':
            tag='skipped' if row['status'] in ('NOT_RUN','XFAIL') else 'error' if row['status']=='ERROR' else 'failure'
            ET.SubElement(case,tag,message=row['status']).text=row['message']
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--pattern',default='test_*.py')
    args=parser.parse_args();os.environ.pop('PR_RUST_LIB',None)
    out=ROOT/'reports/r4';out.mkdir(parents=True,exist_ok=True)
    stream=io.StringIO();suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=args.pattern)
    with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=DetailedResult).run(suite)
    rows=result.rows
    report={'scope':'Python tooling, validation and fixture tests; no PhysX simulation.',
            'utc':datetime.now(timezone.utc).isoformat(),'physicsExecuted':False,'rustCompiled':False,
            'discovered':result.testsRun,'passed':sum(r['status']=='PASS' for r in rows),
            'failed':sum(r['status'] in ('FAIL','ERROR') for r in rows),
            'notRun':sum(r['status'] in ('NOT_RUN','XFAIL') for r in rows),'tests':rows}
    report['status']='PYTHON_CHECKS_PASSED_WITH_EXPLICIT_SKIPS' if result.wasSuccessful() and report['passed']>0 else 'FAILED'
    (out/'python-results.json').write_text(json.dumps(report,indent=2)+'\n')
    (out/'python-tests.log').write_text(stream.getvalue());junit(rows,out/'python-junit.xml')
    print(json.dumps({k:v for k,v in report.items() if k!='tests'},indent=2))
    return 0 if report['status'].startswith('PYTHON_CHECKS_PASSED') else 1
if __name__=='__main__':raise SystemExit(main())
