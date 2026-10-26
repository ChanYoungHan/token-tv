import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from token_tv.history import UsageHistory, KST, day_at
from token_tv.sources import fetch_account
from token_tv.subscription import claude_subscription, codex_subscription, metadata


ACCOUNT = {'key': 'claude_a', 'alias': 'CLAUDE A', 'provider': 'claude', 'email': 'a@example.invalid'}


class SubscriptionTests(unittest.TestCase):
    def test_profile_plans_and_missing_values(self):
        for tier, expected in [('default_claude_max_5x', 'max_5x'),
                               ('default_claude_max_20x', 'max_20x'), ('default_claude_pro', 'pro')]:
            self.assertEqual(claude_subscription({'organization': {'rate_limit_tier': tier}})['id'], expected)
        for kind, expected in [('claude_max','max'), ('claude_pro','pro'), ('claude_team','team'),
                               ('claude_enterprise','enterprise')]:
            self.assertEqual(claude_subscription({'organization': {'organization_type': kind}})['id'], expected)
        for profile in [{}, {'organization':None}, {'organization':[]},
                        {'organization':{'rate_limit_tier':['bad'], 'organization_type':{}}}]:
            self.assertEqual(claude_subscription(profile),metadata(None))

    def test_fetch_exports_only_public_plan_metadata(self):
        profile={'account':{'email':ACCOUNT['email']},'organization':{
            'rate_limit_tier':'default_claude_max_20x','secret':'DO-NOT-EXPORT'}}
        usage={'seven_day':{'utilization':30}}
        with patch('token_tv.sources.claude_payload',return_value=(profile,usage)):
            row=fetch_account(ACCOUNT)
        self.assertEqual(row['subscription'],metadata('max_20x'))
        self.assertEqual(row['windows'][0]['used_percent'],30)
        self.assertNotIn('DO-NOT-EXPORT',json.dumps(row))
        profile['account']['email']='other@example.invalid'
        with patch('token_tv.sources.claude_payload',return_value=(profile,usage)):
            row=fetch_account(ACCOUNT)
        self.assertNotIn('subscription',row)
        self.assertEqual(row['status'],'identity_mismatch')


    def test_codex_plan_types_and_same_account_fallback(self):
        for value in ('free','go','plus','pro','prolite','promax','team','business','enterprise','edu'):
            self.assertEqual(codex_subscription({'planType':value}),metadata(value))
        self.assertEqual(codex_subscription({'planType':'plus'},{'planType':'pro'}),metadata('plus'))
        self.assertEqual(codex_subscription({'planType':'unknown'},{'planType':'pro'}),metadata('pro'))
        for value in (None,[],{},'unknown','future_plan','max_20x'):
            self.assertEqual(codex_subscription({'planType':value}),metadata(None))

    def test_codex_rpc_verifies_identity_before_exporting_plan(self):
        account={**ACCOUNT,'provider':'codex','key':'codex_a','source_home':'/unused'}
        rpc=MagicMock()
        def call(method,*args):
            return {'initialize':{},'account/read':{'account':{
                'type':'chatgpt','email':account['email'],'planType':'plus','secret':'DO-NOT-EXPORT'}},
                'account/rateLimits/read':{'rateLimits':{'planType':'plus','primary':{
                    'usedPercent':35,'windowDurationMins':10080}}}}[method]
        rpc.call.side_effect=call
        with patch('token_tv.sources.Path.is_file',return_value=True), patch('token_tv.sources.RPC',return_value=rpc):
            row=fetch_account(account)
        self.assertEqual(row['status'],'ok')
        self.assertEqual(row['subscription'],metadata('plus'))
        self.assertEqual(row['windows'][0]['used_percent'],35)
        self.assertNotIn('DO-NOT-EXPORT',json.dumps(row))
        self.assertNotIn(account['email'],json.dumps(row))
        rpc.close.assert_called_once()
        rpc.reset_mock()
        def mismatch(method,*args):
            if method=='account/read':return {'account':{'type':'chatgpt','email':'wrong@example.invalid','planType':'pro'}}
            if method=='account/rateLimits/read':raise AssertionError('Quota requested before identity check')
            return {}
        rpc.call.side_effect=mismatch
        with patch('token_tv.sources.Path.is_file',return_value=True), patch('token_tv.sources.RPC',return_value=rpc):
            row=fetch_account(account)
        self.assertEqual(row['status'],'identity_mismatch')
        self.assertNotIn('subscription',row)
        rpc.close.assert_called_once()


class PlanHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'history.sqlite3'
        self.ledger=UsageHistory(self.path,[ACCOUNT])
        self.base=(datetime.now(KST).replace(minute=0,second=0,microsecond=0)-timedelta(hours=3)).timestamp()
        self.reset=self.base+86400

    def record(self,offset,value,plan,reset=None):
        observed=self.base+offset
        row={'status':'ok','identity_verified':True,'fetched_at':observed,
             'subscription':metadata(plan),'windows':[{'label':'WEEK','used_percent':value,
             'resets_at':self.reset if reset is None else reset}]}
        with patch('token_tv.history.time.time',return_value=observed):
            self.ledger.record({'accounts':{'claude_a':row}})

    def query(self,granularity='day'):
        if granularity=='hour':
            stamp=lambda value:datetime.fromtimestamp(value,KST).isoformat(timespec='minutes')[:16]
            return self.ledger.query('claude_a',stamp(self.base),stamp(self.base+3600),'hour')
        return self.ledger.query('claude_a',day_at(self.base),day_at(self.base+3600))

    def test_plan_change_uses_new_baseline_in_both_aggregations(self):
        self.record(0,80,'pro')
        self.record(300,85,'pro')
        self.record(600,20,'max_20x')
        self.record(900,23,'max_20x')
        for granularity in ('day','hour'):
            result=self.query(granularity)
            self.assertEqual(result['range_used_pp'],8)
            self.assertEqual(len(result['plan_events']),1)
            event=result['plan_events'][0]
            self.assertEqual((event['from']['id'],event['to']['id']),('pro','max_20x'))
            self.assertEqual(sum(p['plan_changes'] for p in result['buckets']),1)
            observed=next(p for p in result['buckets'] if p['observations'])
            self.assertEqual(observed['subscription'],metadata('max_20x'))
            self.assertTrue(observed['partial'])
            self.assertEqual(result['reset_events'],[])

    def test_increase_after_downgrade_does_not_count_denominator_jump(self):
        self.record(0,10,'max_20x')
        self.record(300,70,'pro')
        self.record(600,72,'pro')
        self.assertEqual(self.query()['range_used_pp'],2)

    def test_unknown_boundaries_and_generic_max_do_not_fake_events(self):
        for offset,value,plan in [(0,30,None),(300,40,'max'),(600,41,'max_20x'),
                                   (900,42,None),(1200,43,'max_20x'),(1500,44,'max_20x')]:
            self.record(offset,value,plan)
        self.assertEqual(self.query()['plan_events'],[])
        self.assertEqual(self.query()['range_used_pp'],1)

    def test_detects_change_across_missing_or_generic_metadata(self):
        self.record(0,30,'max_5x')
        self.record(300,31,'max')
        self.record(600,32,None)
        self.record(900,10,'max_20x')
        event=self.query()['plan_events'][0]
        self.assertEqual(event['from']['id'],'max_5x')
        self.assertEqual(event['to']['id'],'max_20x')

    def test_refining_max_after_upgrade_does_not_duplicate_event(self):
        self.record(0,80,'pro')
        self.record(300,20,'max')
        self.record(600,21,'max_20x')
        self.assertEqual(len(self.query()['plan_events']),1)

    def test_reopen_and_duplicate_observations(self):
        self.record(0,20,'pro')
        self.record(300,22,'pro')
        self.ledger=UsageHistory(self.path,[ACCOUNT])
        self.record(300,22,'pro')
        self.assertEqual(self.query()['range_used_pp'],2)
        self.assertEqual(sum(p['observations'] for p in self.query()['buckets']),2)

    def test_simultaneous_plan_and_reset_changes_are_not_counted(self):
        self.record(0,80,'pro',self.base+100)
        self.record(300,10,'max_5x',self.base+86400)
        self.assertEqual(self.query()['reset_events'],[])
        self.assertEqual(len(self.query()['plan_events']),1)
        self.assertIsNone(self.query()['range_used_pp'])

    def test_codex_accounts_keep_independent_plans_and_changes(self):
        accounts=[{**ACCOUNT,'provider':'codex','key':'codex_a','alias':'CODEX A'},
                  {**ACCOUNT,'provider':'codex','key':'codex_b','alias':'CODEX B','email':'b@example.invalid'}]
        ledger=UsageHistory(self.path,accounts)
        for offset,values in [(0,[(80,'plus'),(10,'pro')]),(300,[(20,'pro'),(12,'pro')]),
                              (600,[(22,'pro'),(16,'pro')])]:
            observed=self.base+offset
            rows={a['key']:{'status':'ok','identity_verified':True,'fetched_at':observed,
                'subscription':metadata(plan),'windows':[{'label':'WEEK','used_percent':value,'resets_at':self.reset}]}
                for a,(value,plan) in zip(accounts,values)}
            with patch('token_tv.history.time.time',return_value=observed):ledger.record({'accounts':rows})
        for mode in ('day','hour'):
            for key,total,changes in [('codex_a',2,1),('codex_b',6,0)]:
                stamp=lambda value:datetime.fromtimestamp(value,KST).isoformat(timespec='minutes')[:16]
                result=ledger.query(key,day_at(self.base),day_at(self.base+3600)) if mode=='day' else ledger.query(key,stamp(self.base),stamp(self.base+3600),'hour')
                self.assertEqual(result['range_used_pp'],total)
                self.assertEqual(len(result['plan_events']),changes)
                if changes:
                    self.assertEqual(result['plan_events'][0]['from'],metadata('plus'))
                    self.assertEqual(result['plan_events'][0]['to'],metadata('pro'))

    def test_migration_preserves_legacy_observations_and_totals(self):
        self.record(0,10,None)
        self.record(300,13,None)
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE observations DROP COLUMN previous_plan')
            db.execute('ALTER TABLE observations DROP COLUMN plan')
            db.execute('PRAGMA user_version=1')
        self.ledger=UsageHistory(self.path,[ACCOUNT])
        self.assertEqual(self.query()['range_used_pp'],3)
        self.assertEqual(self.query()['buckets'][0]['subscription'],metadata(None))
        with self.ledger.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')


if __name__=='__main__':
    unittest.main()
