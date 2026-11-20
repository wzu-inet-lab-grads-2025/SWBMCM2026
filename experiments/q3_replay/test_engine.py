import unittest

from .acceptance import pair_trials, verify_ledger
from .engine import fixed_actions, run_trial


class TrialRules(unittest.TestCase):
    def setUp(self):
        self.trial = next(pair_trials())

    def play(self, *commands):
        return run_trial(self.trial, fixed_actions(commands))

    def test_release_during_wait_and_review_before_predecessor(self):
        self.trial.jobs['B'].update(release_s=2, predecessors=['A'])
        result = self.play({'action':'WAIT','seconds':3}, {'action':'REVIEW','job_id':'B'},
                           {'action':'EXECUTE','job_id':'B','b_s':1})
        self.assertEqual(result['releases'][1]['time'], 2)
        self.assertEqual([c['time'] for c in result['calls']], [0,3,4])
        self.assertNotIn('B', result['calls'][0]['input']['observations'])
        self.assertEqual(result['calls'][2]['rejected'],'predecessors')
        self.assertEqual(result['metrics']['cost'],1)
        self.assertFalse(result['metrics']['valid_protocol'])

    def test_censor_or_finish_exactly_at_h(self):
        self.trial.case['horizon_s'] = 4
        result = self.play({'action':'EXECUTE','job_id':'A','b_s':2})
        verify_ledger(result)
        self.assertIsNone(result['ledger'][0]['completion'])
        self.assertEqual(result['metrics']['observed_overrun_s'],.5)
        self.assertEqual(result['metrics']['censored_units'],1)
        self.trial.case['horizon_s'] = 4.5
        result = self.play({'action':'EXECUTE','job_id':'A','b_s':2})
        verify_ledger(result)
        self.assertEqual(result['ledger'][0]['completion'],4.5)
        self.assertEqual(result['metrics']['censored_units'],0)

    def test_invalid_actions_are_terminal_without_spending(self):
        for a in ({'action':'EXECUTE','job_id':'A','b_s':float('nan')},
                  {'action':'WAIT','seconds':101}, {'action':'WAIT','seconds':0}):
            with self.subTest(command=a):
                result = self.play(a)
                self.assertEqual(result['ledger'],[])
                self.assertEqual(result['metrics']['invalid_actions'],1)
                self.assertEqual(result['metrics']['cost'],0)
