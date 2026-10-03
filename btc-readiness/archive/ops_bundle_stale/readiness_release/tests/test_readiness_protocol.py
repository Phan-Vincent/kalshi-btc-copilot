import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'candidate'))
import readiness_policy as r
import study_policy as legacy


def protocol():
    return json.loads((ROOT/'candidate/readiness_protocol.json').read_text())


class ProtocolTests(unittest.TestCase):
    def test_valid_proposal_is_unapproved_independent_copy(self):
        p=protocol()
        before=copy.deepcopy(p)
        out=r.validate_readiness_protocol(p)
        self.assertEqual(p,before)
        self.assertEqual(out,p)
        self.assertIsNot(out,p)
        out['approval_policy']['deadline_utc']='changed'
        self.assertEqual(p,before)
        for key in ('activation_allowed','advice_allowed','execution_allowed','reuse_study002_approval_or_data'):
            self.assertIs(p[key],False)

    def test_exact_preservation_from_legacy_source_reference(self):
        old=json.loads((ROOT/'candidate/btc_copilot_protocol.json').read_text())
        p=protocol()
        for value in (old,p):
            for key in r.MODIFIED_FIELDS:
                value.pop(key,None)
            value['approval_policy'].pop('deadline_utc')
        self.assertEqual(p,old)
        payload=json.dumps(old,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(),r.PRESERVED_POLICY_SHA256)

    def test_legacy_validator_rejects_new_identity_directly(self):
        with self.assertRaisesRegex(ValueError,'identity'):
            legacy.validate_protocol(protocol())

    def test_changed_or_legacy_identity_rejected(self):
        for identity in ('btc-prospective-002','btc-prospective-005-20261214-PROPOSED','other',None):
            p=protocol();p['study_id']=identity
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_changed_candidate_release_rejected(self):
        p=protocol();p['candidate_release']='approved'
        with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_fixed_calendar_and_5376_holdout_slots(self):
        p=protocol()
        dates=[r._utc_midnight(p[key]) for key in r.DATES]
        self.assertEqual([(b-a).days for a,b in zip(dates,dates[1:])],[7,7,56,2])
        self.assertEqual((dates[3]-dates[2]).days*96,p['primary_acceptance']['matched_forecasts'])

    def test_changed_each_boundary_rejected(self):
        for key in r.DATES:
            p=protocol();p[key]='2027-03-01T00:00:00+00:00'
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_non_midnight_and_non_utc_rejected(self):
        for stamp in ('2026-12-14T00:00:01+00:00','2026-12-14T00:00:00.000001+00:00','2026-12-14T00:00:00-08:00','2026-12-14T00:00:00',None):
            p=protocol();p['start_utc']=stamp
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_shifted_calendar_still_rejected(self):
        from datetime import timedelta
        p=protocol()
        for key in r.DATES:p[key]=(r._utc_midnight(p[key])+timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError,'separate protocol review'):r.validate_readiness_protocol(p)

    def test_shortened_development_validation_holdout_rejected(self):
        for key,stamp in [('validation_start_utc','2026-12-20T00:00:00+00:00'),('holdout_start_utc','2026-12-27T00:00:00+00:00'),('end_utc','2027-02-21T00:00:00+00:00')]:
            p=protocol();p[key]=stamp
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_approval_deadline_not_start_or_late_or_implicit(self):
        for stamp in ('2026-12-14T00:00:00+00:00','2026-12-15T00:00:00+00:00',None):
            p=protocol();p['approval_policy']['deadline_utc']=stamp
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)
        p=protocol();p['approval_policy'].pop('deadline_utc')
        with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_every_acceptance_field_is_immutable(self):
        for key,value in protocol()['primary_acceptance'].items():
            p=protocol();p['primary_acceptance'][key]=value+1
            with self.subTest(field=key):
                with self.assertRaisesRegex(ValueError,'policy changed'):r.validate_readiness_protocol(p)

    def test_primary_checkpoint_exit_and_comparison_immutable(self):
        for key,value in [('primary_strategy','current_full'),('primary_exit','sell on signal'),('forecast_checkpoint','best of next ten quotes'),('comparison_policy','choose best arm')]:
            p=protocol();p[key]=value
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_cost_precision_fee_multiplier_and_bounds_immutable(self):
        for key,value in [('minimum_fill_quantity','1'),('balance_precisions',['0.0001']),('coefficient','0'),('multiplier','0'),('acceptance','assume rebates'),('realized_fee_claim',True)]:
            p=protocol();p['fee_policy'][key]=value
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)
        p=protocol();p['fee_multiplier']='0'
        with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_pacing_clock_uncertainty_and_outcomes_immutable(self):
        for group,key,value in [('pacing_policy','interval_seconds',1),('clock_policy','wall_monotonic_tolerance_seconds',60),('uncertainty','block_days',[1]),('uncertainty','replicates',10),('outcome_policy','recheck_seconds',0)]:
            p=protocol();p[group][key]=value
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_policy_prose_and_custody_cannot_be_weakened(self):
        for key in ('holdout_policy','missing_policy','fee_evidence_policy','risk_policy'):
            p=protocol();p[key]='optional'
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)
        p=protocol();p['approval_policy']['required']=False
        with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_implied_activation_or_profitability_unblock_rejected(self):
        for key in ('activation_allowed','advice_allowed','execution_allowed','reuse_study002_approval_or_data'):
            for value in (True,0,None):
                p=protocol();p[key]=value
                with self.assertRaises(ValueError):r.validate_readiness_protocol(p)
        for key,value in [('readiness_status','APPROVED'),('profitability_validation_mode','ENABLED')]:
            p=protocol();p[key]=value
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_unknown_missing_fields_and_non_json_rejected(self):
        for p in (None,[],dict(protocol(),auto_start=True)):
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)
        p=protocol();p.pop('clock_policy')
        with self.assertRaises(ValueError):r.validate_readiness_protocol(p)
        for value in (float('nan'),float('inf'),object()):
            p=protocol();p['primary_acceptance']['calibration_ece']=value
            with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_boolean_numeric_threshold_equivalence_not_accepted(self):
        p=protocol();p['primary_acceptance']['holdout_coverage']=True
        with self.assertRaises(ValueError):r.validate_readiness_protocol(p)

    def test_failure_does_not_mutate_supplied_protocol(self):
        p=protocol();p['primary_acceptance']['calibration_ece']=.5
        before=copy.deepcopy(p)
        with self.assertRaises(ValueError):r.validate_readiness_protocol(p)
        self.assertEqual(p,before)


if __name__=='__main__':unittest.main(verbosity=2)
