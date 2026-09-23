import unittest,json,copy,hashlib
from pathlib import Path
from reference.schema_codec import SCHEMA,validate,validate_semantics,decode
from reference.runtime_rules import canonical,RuleError
ROOT=Path(__file__).resolve().parents[1]
E=json.loads((ROOT/'examples/typed-fixtures.json').read_text())['fixtures']
class Contracts(unittest.TestCase):
    def test_all_typed_examples_structural_and_semantic(self):
        for n,v in E.items():
            with self.subTest(n=n):validate(v,SCHEMA['$defs'][n]);validate_semantics(n,v)
    def test_unknown_field_for_every_strict_object(self):
        for n,v in E.items():
            if isinstance(v,dict)and SCHEMA['$defs'][n].get('additionalProperties')is False:
                with self.subTest(n=n),self.assertRaises(RuleError):decode(n,canonical(dict(v,unexpected=True)))
    def test_all_reported_missing_pin_kinds_exist(self):
        for k in ('profile','journal_record','input_manifest','agent_turn','receipt','context','retrieval','tool_snapshot','skill_use','occurrence','completion_scope'):
            with self.subTest(k=k):validate({'kind':k,'id':'x','revision':1,'content_hash':'a'*64},SCHEMA['$defs']['Pin'])
    def test_cross_kind_still_refused(self):
        x=copy.deepcopy(E['ContextManifest']);x['profile_ref']['kind']='agent'
        with self.assertRaises(RuleError):decode('ContextManifest',canonical(x))
    def test_pagination_fields_formally_supported(self):
        x=copy.deepcopy(E['SearchPage']);x.update(next_cursor='token',has_more=True,page_semantics='PROGRESS');x['receipt'].update(status='PARTIAL',query_result_count=None,has_more=True);x['receipt']['coverage'].update(phase='SCANNING',rank_scope='NONE',ranking_final=False,snapshot_chunks=1);decode('SearchPage',canonical(x))
    def test_history_cursor_flags_inconsistent_refused(self):
        x=copy.deepcopy(E['HistoryReadPage']);x['has_more']=not x['has_more']
        with self.assertRaises(RuleError):decode('HistoryReadPage',canonical(x))
    def test_cursor_flags_inconsistent_refused(self):
        x=copy.deepcopy(E['SearchPage']);x['has_more']=True
        with self.assertRaises(RuleError):decode('SearchPage',canonical(x))
    def test_scanning_cannot_publish_final_rank(self):
        x=copy.deepcopy(E['SearchPage']);x['receipt']['coverage']['phase']='SCANNING'
        with self.assertRaises(RuleError):decode('SearchPage',canonical(x))
    def test_complete_index_cannot_be_partial(self):
        x=copy.deepcopy(E['SearchCoverage']);x.update(expected_groups=2,indexed_groups=1)
        with self.assertRaises(RuleError):decode('SearchCoverage',canonical(x))
    def test_history_slice_hash_required(self):
        x=copy.deepcopy(E['HistorySlice']);x['slice_hash']='a'*64
        with self.assertRaises(RuleError):decode('HistorySlice',canonical(x))
    def test_first_request_summary_not_fake_count(self):decode('ContextSummaryView',canonical(E['ContextSummaryView']))
    def test_settings_expected_revision_pair(self):
        x=copy.deepcopy(E['HostRequest']);x.update(verb='agent_context_settings_update',payload=E['ContextSettingsCommand'],expected_revision=4)
        with self.assertRaises(RuleError):decode('HostRequest',canonical(x))
    def test_settings_command_flow(self):
        for item in json.loads((ROOT/'examples/host-settings-flow.json').read_text())['flow']:decode('HostRequest',canonical(item['request']))
    def test_schema_rejects_host_fake_result_type(self):
        x=copy.deepcopy(E['HostResponse']);x['items']=[E['SessionView']]
        with self.assertRaises(RuleError):decode('HostResponse',canonical(x))
    def test_request_cannot_supply_two_payloads(self):
        x=copy.deepcopy(E['HostRequest']);x['payload_ref']=dict(E['Pin'],kind='artifact')
        with self.assertRaises(RuleError):decode('HostRequest',canonical(x))
    def test_catalogue_800_files_summary_and_pages(self):
        files=[dict(E['SkillFile'],relative_path=f'references/doc-{i:04}.md')for i in range(800)]
        definition=copy.deepcopy(E['Skill']);definition['files']=files;decode('Skill',canonical(definition))
        summary=copy.deepcopy(E['SkillCatalogueItem']);summary.update(file_count=800,total_payload_bytes=123548);decode('SkillCatalogueItem',canonical(summary));self.assertLess(len(canonical(summary)),4096)
        seen=[]
        for off in range(0,800,64):
            page=copy.deepcopy(E['SkillDetailsPage']);page.update(files=files[off:off+64],has_more=off+64<800,next_cursor='next'if off+64<800 else None)
            decode('SkillDetailsPage',canonical(page));self.assertLess(len(canonical(page)),65536);seen.extend(page['files'])
        self.assertEqual(seen,files)
    def test_provider_actual_definition_priority(self):
        x=copy.deepcopy(E['ProviderDefinition']);self.assertIn('selection_priority',x);validate(x,SCHEMA['$defs']['ProviderDefinition'])
    def test_summary_jobs_not_enabled(self):
        x=copy.deepcopy(E['RuntimeJobChangedBody']);x['kind']='SUMMARY'
        with self.assertRaises(RuleError):decode('RuntimeJobChangedBody',canonical(x))
    def test_no_replacement_for_unknown(self):
        x=copy.deepcopy(E['PreparedRequestDisposition']);x.update(action='RECONCILE_ORIGINAL',replacement_ordinal=2)
        with self.assertRaises(RuleError):decode('PreparedRequestDisposition',canonical(x))
    def test_deletion_requires_all_collections(self):
        x=copy.deepcopy(E['CompleteSessionDisposal']);x['collections'].pop()
        with self.assertRaises(RuleError):decode('CompleteSessionDisposal',canonical(x))
    def test_context_and_meter_hashes_match(self):
        x=copy.deepcopy(E['ContextManifest']);x['planned_request_hash']='0'*64
        with self.assertRaises(RuleError):decode('ContextManifest',canonical(x))
