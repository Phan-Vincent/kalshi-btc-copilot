"""Proposal constraints only: no collector, account, network or outcome access."""
import hashlib,json,sys,unittest
from datetime import datetime,timedelta
from pathlib import Path
HERE=Path(__file__).resolve().parent
CANDIDATE=HERE.parent/'outputs/copilot_remediation_candidate'
sys.path.insert(0,str(CANDIDATE))
import btc_copilot_research as research
P=json.loads((HERE/'protocol_proposal.json').read_text())
I=json.loads((HERE/'candidate_identity.json').read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def utc(key):return datetime.fromisoformat(P['date_policy'][key])

class PackageContract(unittest.TestCase):
    def test_identity_matches_current_disabled_candidate(self):
        self.assertEqual(I['candidate_manifest_sha256'],sha(CANDIDATE/'release_manifest.json'))
        self.assertEqual(P['candidate_manifest_sha256'],I['candidate_manifest_sha256'])
        self.assertEqual(I['model_version'],research.version(research.load_settings(CANDIDATE/'btc_copilot_settings.json')))
        self.assertEqual(P['candidate_model_version'],I['model_version'])
        inventory=json.loads((HERE/'candidate_source_hashes.json').read_text())
        for name,digest in inventory.items():self.assertEqual(sha(CANDIDATE/name),digest,name)
        self.assertEqual(P['candidate_source_inventory_sha256'],sha(HERE/'candidate_source_hashes.json'))
    def test_proposal_has_no_activation_or_inherited_approval(self):
        for obj in [P,I]:
            for flag in ['activation_allowed','advice_allowed','execution_allowed']:self.assertIs(obj[flag],False)
        self.assertIs(P['activation_review_ready'],False)
        self.assertFalse(P['reuse_study002_approval_or_data'])
        self.assertTrue((CANDIDATE/'QA_ONLY').is_file())
        for name in ['runtime','activation_approval.json']:self.assertFalse((CANDIDATE/name).exists())
    def test_dates_are_future_provisional_utc_with_no_overlap(self):
        for key in ['start_utc','validation_start_utc','holdout_start_utc','end_utc','release_utc','approval_deadline_utc']:
            self.assertEqual(utc(key).utcoffset(),timedelta(0));self.assertEqual(utc(key).hour,0)
        self.assertGreater(utc('start_utc'),datetime.fromisoformat(P['created_as_of']+'T00:00:00+00:00'))
        old=json.loads((CANDIDATE/'btc_copilot_protocol.json').read_text())
        self.assertGreater(utc('start_utc'),datetime.fromisoformat(old['release_utc']))
        self.assertLess(utc('approval_deadline_utc'),utc('start_utc'))
        self.assertTrue(P['date_policy']['dates_are_provisional'])
    def test_predeclared_calendar_is_seven_seven_fiftysix(self):
        self.assertEqual(utc('validation_start_utc')-utc('start_utc'),timedelta(days=7))
        self.assertEqual(utc('holdout_start_utc')-utc('validation_start_utc'),timedelta(days=7))
        self.assertEqual(utc('end_utc')-utc('holdout_start_utc'),timedelta(days=56))
        self.assertGreaterEqual(utc('release_utc')-utc('end_utc'),timedelta(days=2))
        self.assertEqual(P['frozen_design']['acceptance']['matched_forecasts'],56*96)
    def test_acceptance_thresholds_are_not_weakened(self):
        old=json.loads((CANDIDATE/'btc_copilot_protocol.json').read_text())
        new=P['frozen_design']['acceptance'];previous=old['primary_acceptance']
        for key in ['matched_forecasts','filled_contracts','distinct_days','holdout_coverage','unresolved_enrolled','missing_calendar_days','amended_results','missing_execution_observations']:
            self.assertEqual(new[key],previous[key])
        self.assertEqual(new['calibration_ece_max'],previous['calibration_ece'])
        self.assertIn('strictly > 0',new['stress_daily_lower_bound']);self.assertIn('strictly < 0',new['paired_zero_minus_market_brier_upper_bound'])
        for key in ['block_days','replicates','seed']:self.assertEqual(P['frozen_design']['uncertainty'][key],old['uncertainty'][key])
    def test_cost_settings_and_original_policy_bytes_preserved(self):
        policies=json.loads((HERE/'preserved_policies.json').read_text())
        for name,item in policies.items():
            self.assertTrue(item['unchanged']);self.assertEqual(sha(CANDIDATE/name),item['sha256'])
            self.assertEqual((CANDIDATE/name).read_bytes(),(HERE/'candidate_before'/name).read_bytes())
        old=json.loads((CANDIDATE/'btc_copilot_protocol.json').read_text())
        self.assertEqual(P['cost_policy']['conservative_precision_scenarios'],old['fee_policy']['balance_precisions'])
        self.assertEqual(P['cost_policy']['minimum_fill_quantum'],old['fee_policy']['minimum_fill_quantity'])
    def test_design_is_new_fixed_primary_not_tunable_or_promotable(self):
        self.assertNotEqual(P['study_id'],json.loads((CANDIDATE/'release_manifest.json').read_text())['study_id'])
        import study_policy
        legacy=json.loads((CANDIDATE/'btc_copilot_protocol.json').read_text())
        legacy['study_id']=P['study_id']
        with self.assertRaisesRegex(ValueError,'Unsupported study identity'):study_policy.validate_protocol(legacy)
        self.assertEqual(P['frozen_design']['primary_strategy'],'drift_free')
        self.assertEqual(P['frozen_design']['quantity_contracts'],'1.00')
        for flag in ['fresh_database','protected_prior_outcomes_unread','no_tuning_or_primary_reselection','no_automatic_promotion']:self.assertTrue(P['holdout_policy'][flag])
    def test_readiness_gates_remain_explicit_and_blocking(self):
        self.assertGreaterEqual(len(P['gates_still_open']),8)
        self.assertEqual(P['cost_policy']['profitability_status'],'BLOCKED_PENDING_FEE_AND_EXECUTION_EVIDENCE')
        self.assertIs(P['operating_plan']['capacity_monitor_implemented'],False)
        self.assertIs(P['operating_plan']['quota_enforcement_implemented'],False)
        self.assertIn('not this disabled candidate',P['required_future_approval_scope']['action'])
        self.assertIn('NOT_EXECUTABLE_NOT_APPROVED',P['status'])

if __name__=='__main__':unittest.main(verbosity=2)
